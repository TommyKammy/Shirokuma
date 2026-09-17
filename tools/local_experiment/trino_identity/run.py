#!/usr/bin/env python3
"""Disposable Keycloak JWT -> Trino/OPA experiment with private TLS trust."""
import argparse
import base64
from contextlib import contextmanager, nullcontext
import copy
from datetime import datetime, timezone
import json
import os
import re
import selectors
import signal
import socket
import subprocess
import sys
import time
import uuid

from support import (CONTEXT, HERE, NAMESPACE, ROOT, STATE, ExperimentError, canonical, command, digest,
    interruption_handlers, new_output, read, require, run_lock, stop_group, token, write)
from client import (AUDIENCE, CASES, Client, ISSUER, NEGATIVES, USERS, expected_rows, token_projection)
from tools.local_experiment.trino_identity import fixtures, build as image_build
from tools.local_experiment.local_experiment import check_context

OWNER = 'shirokuma.dev/trino-identity-run'
HASH = 'shirokuma.dev/content-sha256'
KINDS = {'NetworkPolicy': ('networking.k8s.io/v1', 'networkpolicies'), 'ConfigMap': ('v1', 'configmaps'),
         'Secret': ('v1', 'secrets'), 'Pod': ('v1', 'pods')}
TRINO_KEYS = ('jvm.config', 'node.properties', 'log.properties', 'tpch.properties', 'opa.properties',
              'readonly.properties', 'ca.crt', 'jwt-public.pem')
SECRET_KEYS = ('tls.crt', 'tls.key', 'realm.json', 'trino.pem', 'config.properties')
SECURITY = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}
RESOURCES = {
    'trino': {'requests': {'cpu': '500m', 'memory': '1Gi'}, 'limits': {'cpu': '2', 'memory': '4Gi'}},
    'opa': {'requests': {'cpu': '50m', 'memory': '32Mi'}, 'limits': {'cpu': '500m', 'memory': '256Mi'}},
    'keycloak': {'requests': {'cpu': '250m', 'memory': '512Mi'}, 'limits': {'cpu': '2', 'memory': '2Gi'}},
}
OPA_ARGS = ['run', '--server', '--addr=127.0.0.1:8181', '--authorization=basic', '--log-format=json',
            '--log-level=info', '--set=decision_logs.console=true', '/policy/trino.rego', '/policy/api.rego']
KC_ARGS = ['start', '--optimized', '--import-realm', '--http-enabled=false', '--hostname=https://localhost:18443',
           '--https-port=18443', '--https-certificate-file=/identity/tls.crt', '--https-certificate-key-file=/identity/tls.key',
           '--cache=local']


def stamp():
    return datetime.now(timezone.utc).isoformat()


def name(run_token):
    return 'identity-' + token(run_token)


def labels(run_token):
    return {OWNER: run_token, 'app.kubernetes.io/part-of': 'shirokuma', 'shirokuma.dev/scope': 'local-experiment'}


def seal(obj, run_token):
    obj['metadata'].update(namespace=NAMESPACE, labels=labels(run_token))
    obj['metadata']['annotations'] = {HASH: digest(obj)}
    return obj


def configs(secret):
    return '\n'.join([
        'coordinator=true', 'node-scheduler.include-coordinator=true', 'http-server.http.port=18080',
        'discovery.uri=http://127.0.0.1:18080', 'http-server.https.enabled=true', 'http-server.https.port=18444',
        'http-server.https.keystore.path=/etc/trino/trino.pem', 'http-server.process-forwarded=false',
        'http-server.authentication.type=JWT', 'http-server.authentication.allow-insecure-over-http=false',
        'http-server.authentication.jwt.key-file=/etc/trino/jwt-public.pem',
        'http-server.authentication.jwt.required-issuer=' + ISSUER,
        'http-server.authentication.jwt.required-audience=' + AUDIENCE,
        'http-server.authentication.jwt.principal-field=preferred_username',
        'http-server.authentication.jwt.user-mapping.pattern=^(lab_admin|lab_row|lab_mask|lab_both|lab_denied)$',
        'internal-communication.shared-secret=' + secret,
        'query.max-memory=512MB', 'query.max-memory-per-node=512MB', 'memory.heap-headroom-per-node=512MB',
        'query.max-run-time=30s', 'query.max-execution-time=20s',
        'access-control.config-files=/etc/trino/opa.properties,/etc/trino/readonly.properties', ''])


def volumes(run_token):
    prefix = name(run_token)
    return [
        {'name': 'trino-config', 'projected': {'defaultMode': 0o440, 'sources': [
            {'configMap': {'name': prefix + '-config', 'items': [
                {'key': key, 'path': 'catalog/tpch.properties' if key == 'tpch.properties' else key} for key in TRINO_KEYS]}},
            {'secret': {'name': prefix + '-secret', 'items': [
                {'key': key, 'path': key} for key in ('trino.pem', 'config.properties')]}}]}},
        {'name': 'keycloak-config', 'projected': {'defaultMode': 0o440, 'sources': [
            {'secret': {'name': prefix + '-secret', 'items': [{'key': key, 'path': key}
                                                            for key in ('tls.crt', 'tls.key')]}}]}},
        {'name': 'realm-import', 'projected': {'defaultMode': 0o440, 'sources': [
            {'secret': {'name': prefix + '-secret', 'items': [{'key': 'realm.json', 'path': 'realm.json'}]}}]}},
        {'name': 'trino-tmp', 'emptyDir': {'sizeLimit': '256Mi'}},
        {'name': 'trino-data', 'emptyDir': {'sizeLimit': '512Mi'}},
        {'name': 'keycloak-tmp', 'emptyDir': {'sizeLimit': '256Mi'}},
        {'name': 'keycloak-data', 'emptyDir': {'sizeLimit': '2Gi'}},
    ]


def mounts(component):
    return {
        'trino': [{'name': 'trino-config', 'mountPath': '/etc/trino', 'readOnly': True},
                  {'name': 'trino-tmp', 'mountPath': '/tmp'}, {'name': 'trino-data', 'mountPath': '/var/trino'}],
        'opa': [],
        'keycloak': [{'name': 'keycloak-config', 'mountPath': '/identity', 'readOnly': True},
                     {'name': 'keycloak-data', 'mountPath': '/opt/keycloak/data'},
                     {'name': 'realm-import', 'mountPath': '/opt/keycloak/data/import', 'readOnly': True},
                     {'name': 'keycloak-tmp', 'mountPath': '/tmp'}],
    }[component]


def render(images, run_token, material):
    require(material['run_token'] == run_token and material['issuer'] == ISSUER and material['audience'] == AUDIENCE,
            'FIXTURE_IDENTITY_MISMATCH')
    prefix = name(run_token)
    public = dict(material['public_data'], **{
        'jvm.config': '-server\n-Xmx2G\n-XX:+ExitOnOutOfMemoryError\n-XX:ReservedCodeCacheSize=256M\n'
                      '-Djava.io.tmpdir=/tmp\n--add-modules=jdk.incubator.vector\n--enable-native-access=ALL-UNNAMED\n',
        'node.properties': 'node.environment=shirokuma_identity_shadow\nnode.id=shirokuma-identity-shadow\n'
                           'node.internal-address=127.0.0.1\nnode.data-dir=/var/trino\n',
        'log.properties': 'io.trino=INFO\n', 'tpch.properties': 'connector.name=tpch\ntpch.column-naming=SIMPLIFIED\n',
        'readonly.properties': 'access-control.name=read-only\n',
        'opa.properties': '\n'.join(['access-control.name=opa',
            'opa.policy.uri=http://127.0.0.1:8181/v1/data/trino/allow',
            'opa.policy.row-filters-uri=http://127.0.0.1:8181/v1/data/trino/rowFilters',
            'opa.policy.column-masking-uri=http://127.0.0.1:8181/v1/data/trino/columnMask',
            'opa.allow-permission-management-operations=false', 'opa.log-requests=false', 'opa.log-responses=false',
            'opa.http-client.connect-timeout=2s', 'opa.http-client.request-timeout=5s', '']),
    })
    require(set(public) == set(TRINO_KEYS), 'PUBLIC_CONFIG_INVALID')
    private = {key: material['secret_data'][key] for key in ('tls.crt', 'tls.key', 'realm.json')}
    private['trino.pem'] = private['tls.key'] + '\n' + private['tls.crt']
    private['config.properties'] = configs(material['secret_data']['internal-secret'])
    objects = [
        {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy', 'metadata': {'name': prefix},
         'spec': {'podSelector': {'matchLabels': {OWNER: run_token}}, 'policyTypes': ['Ingress', 'Egress'], 'ingress': [], 'egress': []}},
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': prefix + '-config'}, 'immutable': True, 'data': public},
        {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': prefix + '-secret'}, 'immutable': True, 'type': 'Opaque',
         'data': {key: base64.b64encode(value.encode()).decode() for key, value in private.items()}},
    ]
    containers = []
    for component in ('trino', 'opa', 'keycloak'):
        container = {'name': component, 'image': images[component], 'imagePullPolicy': 'Never',
            'securityContext': dict(SECURITY, runAsUser=1000 if component == 'keycloak' else 10000, runAsGroup=1000),
            'resources': RESOURCES[component]}
        if mounts(component):
            container['volumeMounts'] = mounts(component)
        if component == 'opa':
            container['args'] = OPA_ARGS
        else:
            port = 18444 if component == 'trino' else 18443
            container.update(ports=[{'name': 'https', 'containerPort': port}],
                startupProbe={'tcpSocket': {'port': port}, 'periodSeconds': 2, 'failureThreshold': 150},
                readinessProbe={'tcpSocket': {'port': port}, 'periodSeconds': 2})
        if component == 'keycloak':
            container.update(args=KC_ARGS, env=[{'name': 'JAVA_OPTS_KC_HEAP', 'value': '-Xms128m -Xmx512m'}])
        containers.append(container)
    objects.append({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': prefix}, 'spec': {
        'automountServiceAccountToken': False, 'enableServiceLinks': False, 'restartPolicy': 'Never',
        'terminationGracePeriodSeconds': 15,
        'securityContext': {'runAsNonRoot': True, 'runAsUser': 10000, 'runAsGroup': 1000, 'fsGroup': 1000,
                            'seccompProfile': {'type': 'RuntimeDefault'}},
        'containers': containers, 'volumes': volumes(run_token)}})
    return [seal(obj, run_token) for obj in objects]


def identity(obj):
    return obj['kind'] + '/' + obj['metadata']['name']


def validate_plan(plan):
    run_token, images = token(plan['run_token']), plan['images']
    families = {'trino': 'trino-opa', 'opa': 'opa', 'keycloak': 'keycloak-identity'}
    require(set(images) == set(families) and all(type(image) is str and
        re.fullmatch(r'shirokuma\.local/' + families[component] + r'@sha256:[0-9a-f]{64}', image)
        for component, image in images.items()), 'PLAN_IMAGES_INVALID')
    objects = plan['objects']
    prefix = name(run_token)
    require(type(objects) is list and [identity(o) for o in objects] == [
        'NetworkPolicy/' + prefix, 'ConfigMap/' + prefix + '-config', 'Secret/' + prefix + '-secret', 'Pod/' + prefix],
        'PLAN_OBJECTS_INVALID')
    for obj in objects:
        meta, kind = obj['metadata'], obj['kind']
        require(set(obj) == {'apiVersion', 'kind', 'metadata', *({'immutable', 'data', 'type'} if kind == 'Secret'
                else {'immutable', 'data'} if kind == 'ConfigMap' else {'spec'})}
                and obj['apiVersion'] == KINDS[kind][0] and set(meta) == {'name', 'namespace', 'labels', 'annotations'}
                and meta['namespace'] == NAMESPACE and meta['labels'] == labels(run_token)
                and set(meta['annotations']) == {HASH}, 'PLAN_SCOPE_INVALID')
        raw = copy.deepcopy(obj)
        raw['metadata'].pop('annotations')
        require(meta['annotations'][HASH] == digest(raw), 'PLAN_HASH_MISMATCH')
        if kind == 'NetworkPolicy':
            require(obj['spec'] == {'podSelector': {'matchLabels': {OWNER: run_token}}, 'policyTypes': ['Ingress', 'Egress'],
                                    'ingress': [], 'egress': []}, 'PLAN_SCOPE_INVALID')
        elif kind in {'ConfigMap', 'Secret'}:
            require(obj['immutable'] is True and set(obj['data']) == set(TRINO_KEYS if kind == 'ConfigMap' else SECRET_KEYS)
                    and all(type(value) is str and len(value) <= 131072 for value in obj['data'].values()), 'PLAN_SCOPE_INVALID')
            if kind == 'Secret':
                require(obj['type'] == 'Opaque', 'PLAN_SCOPE_INVALID')
        else:
            spec = obj['spec']
            require(set(spec) == {'automountServiceAccountToken', 'enableServiceLinks', 'restartPolicy',
                'terminationGracePeriodSeconds', 'securityContext', 'containers', 'volumes'}
                and spec['automountServiceAccountToken'] is False and spec['enableServiceLinks'] is False
                and spec['restartPolicy'] == 'Never' and spec['terminationGracePeriodSeconds'] == 15
                and spec['securityContext'] == {'runAsNonRoot': True, 'runAsUser': 10000, 'runAsGroup': 1000,
                    'fsGroup': 1000, 'seccompProfile': {'type': 'RuntimeDefault'}}
                and spec['volumes'] == volumes(run_token), 'PLAN_SCOPE_INVALID')
            require(type(spec['containers']) is list and [c['name'] for c in spec['containers']] == ['trino', 'opa', 'keycloak'],
                    'PLAN_SCOPE_INVALID')
            for container in spec['containers']:
                component = container['name']
                extra = {'args'} if component == 'opa' else {'ports', 'startupProbe', 'readinessProbe', 'volumeMounts'}
                if component == 'keycloak':
                    extra |= {'args', 'env'}
                require(set(container) == {'name', 'image', 'imagePullPolicy', 'securityContext', 'resources', *extra}
                        and container['image'] == images[component] and container['imagePullPolicy'] == 'Never'
                        and container['resources'] == RESOURCES[component]
                        and container['securityContext'] == dict(SECURITY,
                            runAsUser=1000 if component == 'keycloak' else 10000, runAsGroup=1000)
                        and container.get('volumeMounts', []) == mounts(component)
                        and container.get('args', []) == ({'trino': [], 'opa': OPA_ARGS, 'keycloak': KC_ARGS}[component])
                        and container.get('env', []) == ([{'name': 'JAVA_OPTS_KC_HEAP', 'value': '-Xms128m -Xmx512m'}]
                                                         if component == 'keycloak' else [])
                        and not container.get('command') and not container.get('envFrom') and not container.get('lifecycle'),
                        'PLAN_SCOPE_INVALID')
                if component != 'opa':
                    port = 18444 if component == 'trino' else 18443
                    require(container['ports'] == [{'name': 'https', 'containerPort': port}]
                            and container['startupProbe'] == {'tcpSocket': {'port': port}, 'periodSeconds': 2, 'failureThreshold': 150}
                            and container['readinessProbe'] == {'tcpSocket': {'port': port}, 'periodSeconds': 2}, 'PLAN_SCOPE_INVALID')
    return objects


def created_uid(current, expected):
    meta = current.get('metadata', {})
    uid = meta.get('uid')
    require(type(uid) is str and str(uuid.UUID(uid)) == uid, 'KNOWN_UID_REQUIRED')
    require(current.get('apiVersion') == expected['apiVersion'] and current.get('kind') == expected['kind']
            and meta.get('name') == expected['metadata']['name'] and meta.get('namespace') == NAMESPACE
            and meta.get('labels', {}).get(OWNER) == expected['metadata']['labels'][OWNER]
            and meta.get('annotations', {}).get(HASH) == expected['metadata']['annotations'][HASH], 'OBJECT_IDENTITY_CONFLICT')
    return uid


def assert_owned(current, expected, uid):
    require(type(uid) is str and str(uuid.UUID(uid)) == uid, 'KNOWN_UID_REQUIRED')
    require(created_uid(current, expected) == uid, 'OBJECT_IDENTITY_CONFLICT')
    if expected['kind'] in {'ConfigMap', 'Secret'}:
        require(current.get('immutable') is True and current.get('data') == expected['data']
                and (expected['kind'] != 'Secret' or current.get('type') == 'Opaque'), 'OBJECT_CONTENT_CONFLICT')
    elif expected['kind'] == 'NetworkPolicy':
        spec = copy.deepcopy(current.get('spec', {}))
        spec.setdefault('ingress', [])
        spec.setdefault('egress', [])
        require(spec == expected['spec'], 'OBJECT_CONTENT_CONFLICT')
    else:
        spec, wanted = current.get('spec', {}), expected['spec']
        require(not any(spec.get(key) for key in ('hostNetwork', 'hostPID', 'hostIPC', 'initContainers', 'ephemeralContainers', 'hostAliases'))
                and all(spec.get(key) == wanted[key] for key in ('automountServiceAccountToken', 'enableServiceLinks',
                    'restartPolicy', 'terminationGracePeriodSeconds', 'securityContext', 'volumes'))
                and len(spec.get('containers', [])) == 3, 'POD_SCOPE_CONFLICT')
        for item, target in zip(spec['containers'], wanted['containers']):
            require(all(item.get(key, []) == target.get(key, []) for key in ('name', 'image', 'imagePullPolicy',
                'securityContext', 'resources', 'volumeMounts', 'args', 'env'))
                and not any(item.get(key) for key in ('command', 'envFrom', 'lifecycle')), 'POD_SCOPE_CONFLICT')
            ports = copy.deepcopy(item.get('ports', []))
            for port in ports:
                if port.get('protocol') == 'TCP':
                    port.pop('protocol')
            require(ports == target.get('ports', []), 'POD_SCOPE_CONFLICT')
            for probe_name in ('startupProbe', 'readinessProbe', 'livenessProbe'):
                actual = copy.deepcopy(item.get(probe_name, {}))
                expected_probe = target.get(probe_name, {})
                for key, default in {'initialDelaySeconds': 0, 'timeoutSeconds': 1, 'successThreshold': 1, 'failureThreshold': 3}.items():
                    if key not in expected_probe and actual.get(key) == default:
                        actual.pop(key)
                require(actual == expected_probe, 'POD_SCOPE_CONFLICT')


class Kubernetes:
    def request(self, args, body=None, timeout=40):
        return command(['kubectl', '--context', CONTEXT, '-n', NAMESPACE, '--request-timeout=30s', *args],
                       body=canonical(body) if body is not None else None, timeout=timeout)

    def get(self, obj):
        raw = self.request(['get', obj['kind'], obj['metadata']['name'], '--ignore-not-found=true', '-o', 'json'])
        return json.loads(raw) if raw.strip() else None

    def dry_run(self, obj):
        self.request(['create', '--dry-run=server', '-f', '-', '-o', 'json'], obj)

    def create(self, obj):
        return json.loads(self.request(['create', '-f', '-', '-o', 'json'], obj))

    def delete(self, obj, current):
        version, resource = KINDS[obj['kind']]
        path = ('/api/' if version == 'v1' else '/apis/') + version + '/namespaces/' + NAMESPACE + '/' + resource + '/' + obj['metadata']['name']
        self.request(['delete', '--raw=' + path, '-f', '-'], {'apiVersion': 'v1', 'kind': 'DeleteOptions',
            'propagationPolicy': 'Orphan', 'gracePeriodSeconds': 15 if obj['kind'] == 'Pod' else 0,
            'preconditions': {'uid': current['metadata']['uid'], 'resourceVersion': current['metadata']['resourceVersion']}})

    def logs(self, pod, container='opa'):
        return self.request(['logs', pod['metadata']['name'], '-c', container, '--limit-bytes=1048576', '--tail=1200'])


@contextmanager
def tunnel(pod):
    # Fixed host ports preserve the issuer URL and TLS hostname inside/outside the Pod.
    for port in (18443, 18444):
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                raise ExperimentError('TUNNEL_PORT_COLLISION') from None
    process = subprocess.Popen(['kubectl', '--context', CONTEXT, '-n', NAMESPACE, 'port-forward',
        'pod/' + pod['metadata']['name'], '18443:18443', '18444:18444', '--address=127.0.0.1'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
        env={key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'USER', 'LOGNAME') if key in os.environ})
    selector, data, deadline = selectors.DefaultSelector(), b'', time.monotonic() + 30
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while time.monotonic() < deadline:
            require(process.poll() is None, 'TUNNEL_EXITED')
            if selector.select(timeout=0.5):
                data += os.read(process.stdout.fileno(), 4096)
                require(len(data) <= 8192, 'TUNNEL_OUTPUT_LIMIT')
                if all(('Forwarding from 127.0.0.1:' + str(port) + ' -> ' + str(port)).encode() in data for port in (18443, 18444)):
                    yield
                    return
        raise ExperimentError('TUNNEL_NOT_READY')
    finally:
        selector.close()
        stop_group(process, signal.SIGTERM, 5)
        process.stdout.close()


def wait_ready(kube, pod, uid):
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        value = kube.get(pod)
        require(value is not None, 'POD_DISAPPEARED')
        assert_owned(value, pod, uid)
        require(value.get('status', {}).get('phase') not in {'Failed', 'Succeeded'}, 'POD_TERMINATED')
        statuses = value.get('status', {}).get('containerStatuses', [])
        if len(statuses) == 3 and all(item.get('ready') is True and item.get('restartCount') == 0 for item in statuses):
            return
        time.sleep(1)
    raise ExperimentError('POD_NOT_READY')


def cleanup(kube, receipt, objects, save):
    receipt['cleanup'] = {'verified': False, 'removed': [], 'conflicts': [], 'deferred': []}
    for obj in sorted(objects, key=lambda value: {'Pod': 0, 'Secret': 1, 'ConfigMap': 2, 'NetworkPolicy': 3}[value['kind']]):
        record = next((r for r in receipt['objects'] if r['key'] == identity(obj)), None)
        if record is None:
            continue
        if record.get('uid') is None:
            receipt['cleanup']['deferred'].append(identity(obj))
            save()
            if obj['kind'] == 'Pod':
                break
            continue
        try:
            current = kube.get(obj)
            if current is None:
                continue
            assert_owned(current, obj, record['uid'])
            kube.delete(obj, current)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                current = kube.get(obj)
                if current is None:
                    break
                assert_owned(current, obj, record['uid'])
                time.sleep(0.25)
            else:
                raise ExperimentError('DELETE_UNVERIFIED')
            receipt['cleanup']['removed'].append({'key': identity(obj), 'uid': record['uid']})
        except Exception:
            receipt['cleanup']['conflicts'].append(identity(obj))
            save()
            if obj['kind'] == 'Pod':
                break
        save()
    receipt['cleanup']['verified'] = not receipt['cleanup']['conflicts'] and not receipt['cleanup']['deferred']
    save()


def audit_case(raw, case, result, previous):
    require(len(raw.encode()) <= 1024**2, 'OPA_LOG_LIMIT')
    found, seen = [], dict(previous)
    expected_user = 'lab_admin' if case == 'fixture_control' else next(value[1] for value in CASES if value[0] == case)
    for line in raw.splitlines():
        value = json.loads(line)
        if value.get('type') != 'openpolicyagent.org/decision_logs' or value.get('path') not in {
                'trino/allow', 'trino/rowFilters', 'trino/columnMask'}:
            continue
        decision_id = value.get('decision_id')
        require(type(decision_id) is str and str(uuid.UUID(decision_id)) == decision_id, 'OPA_DECISION_ID_INVALID')
        sha = digest(value)
        if decision_id in seen:
            require(seen[decision_id] == sha, 'OPA_DECISION_CHANGED')
            continue
        require(result['result'] != 'unauthenticated', 'AUTH_REJECTION_REACHED_OPA')
        context, action = value.get('input', {}).get('context', {}), value.get('input', {}).get('action', {})
        operation, path = action.get('operation'), value['path']
        user, query_id = context.get('identity', {}).get('user'), context.get('queryId')
        require(user == expected_user and context.get('identity', {}).get('groups') == []
                and (query_id == result['query_id'] or (query_id is None and operation in {'ExecuteQuery', 'ImpersonateUser'})),
                'OPA_IDENTITY_MISMATCH')
        require((path == 'trino/allow' and operation in {'ExecuteQuery', 'ImpersonateUser', 'AccessCatalog', 'SelectFromColumns'})
                or (path == 'trino/rowFilters' and operation == 'GetRowFilters')
                or (path == 'trino/columnMask' and operation == 'GetColumnMask'), 'OPA_OPERATION_MISMATCH')
        answer = value.get('result')
        if path == 'trino/allow':
            require(type(answer) is bool, 'OPA_RESULT_INVALID')
            outcome = 'allow' if answer else 'deny'
        elif path == 'trino/rowFilters':
            require(answer in ([], [{'expression': 'nationkey = 0'}]), 'OPA_RESULT_INVALID')
            outcome = 'row_filter' if answer else 'no_filter'
        else:
            require(answer in (None, {'expression': "'MASKED'"}), 'OPA_RESULT_INVALID')
            outcome = 'column_mask' if answer else 'no_mask'
        target = None
        if operation == 'ImpersonateUser':
            target = action.get('resource', {}).get('user', {}).get('user')
            require(case in {'forge_user', 'forge_original'} and target == 'lab_admin' and outcome == 'deny',
                    'IMPERSONATION_AUDIT_INVALID')
        seen[decision_id] = sha
        found.append({'case': case, 'decision_id': decision_id, 'user': user, 'target_user': target,
                      'query_id': query_id, 'operation': operation, 'outcome': outcome,
                      'correlation': 'query-id-and-user' if query_id else 'serial-case-window-and-user-operation'})
    if result['result'] == 'success':
        require(any(v['operation'] == 'SelectFromColumns' and v['outcome'] == 'allow' for v in found), 'OPA_AUDIT_MISSING')
        if expected_user in {'lab_row', 'lab_both'}:
            require(any(v['outcome'] == 'row_filter' for v in found), 'OPA_AUDIT_MISSING')
        if expected_user in {'lab_mask', 'lab_both'}:
            require(any(v['outcome'] == 'column_mask' for v in found), 'OPA_AUDIT_MISSING')
    elif result['result'] == 'denied':
        operation = 'ImpersonateUser' if case in {'forge_user', 'forge_original'} else 'ExecuteQuery'
        require(any(v['operation'] == operation and v['outcome'] == 'deny' for v in found), 'OPA_AUDIT_MISSING')
    else:
        require(result['result'] == 'unauthenticated' and not found, 'AUTH_REJECTION_AUDIT_INVALID')
    return found, seen


def collect_audit(kube, pod, case, result, seen):
    deadline = time.monotonic() + 5
    while True:
        raw = kube.logs(pod)
        try:
            values, updated = audit_case(raw, case, result, seen)
            return values, updated, digest(raw.encode())
        except ExperimentError as error:
            if str(error) != 'OPA_AUDIT_MISSING' or time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def redact(raw, secrets):
    for secret in sorted({value for value in secrets if type(value) is str and len(value) >= 8}, key=len, reverse=True):
        raw = raw.replace(secret, '[REDACTED]')
    raw = re.sub(r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----', '[REDACTED_PRIVATE_KEY]', raw)
    return re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[REDACTED_JWT]', raw)


def open_telemetry(output, run_token):
    # Standard experiments require only the standard library. The opt-in path
    # uses the existing pinned observability environment without global setup.
    from tools.local_experiment.trino_identity.telemetry import identity_telemetry
    return identity_telemetry(output, run_token)


def execute(plan, material, output, *, kube=None, tunnel_factory=tunnel, client_factory=Client,
            telemetry_factory=None):
    objects, kube = validate_plan(plan), kube or Kubernetes()
    receipt = {'schema_version': 1, 'run_token': plan['run_token'], 'build_token': plan['build_token'],
        'result': 'running', 'observed_at': stamp(), 'scope': 'isolated-keycloak-jwt-username-authorization',
        'images': plan['images'], 'plan_sha256': digest(plan), 'fixture_hashes': material['hashes'],
        'objects': [], 'tokens': [], 'readiness': [], 'cases': [], 'decisions': [], 'pending': None, 'cleanup': None,
        'unobserved': ['browser-sso', 'key-rotation', 'token-revocation', 'realm-role-propagation', 'production-readiness']}
    if telemetry_factory is not None:
        receipt['telemetry'] = {'enabled': True, 'result': 'running', 'evidence': 'telemetry.json'}
    write(output / 'receipt.json', receipt)
    try:
        context = telemetry_factory(output, plan['run_token']) if telemetry_factory is not None else nullcontext()
        with context as audit:
            _execute_runtime(plan, material, output, receipt, objects, kube,
                             tunnel_factory=tunnel_factory, client_factory=client_factory, audit=audit)
            if audit is not None:
                audit.finish(receipt)
        if telemetry_factory is not None:
            receipt['telemetry']['result'] = 'passed'
            receipt['result'] = 'passed'
            write(output / 'receipt.json', receipt)
        return receipt
    except BaseException as error:
        receipt['result'] = 'failed' if isinstance(error, Exception) else 'interrupted'
        if telemetry_factory is not None:
            receipt['telemetry']['result'] = 'failed'
        receipt.setdefault('failure_code', str(error) if isinstance(error, ExperimentError) else (
            'TELEMETRY_UNVERIFIED' if telemetry_factory is not None else 'IDENTITY_EXPERIMENT_FAILED'))
        write(output / 'receipt.json', receipt)
        raise


def _execute_runtime(plan, material, output, receipt, objects, kube, *, tunnel_factory, client_factory, audit):
    secrets = list(material['credentials']['users'].values()) + [material['credentials']['client_secret']]
    secrets += list(material['secret_data'].values()) + list(material['negative_tokens'].values())
    def save():
        write(output / 'receipt.json', receipt)
    save()
    try:
        for obj in objects:
            require(kube.get(obj) is None, 'OBJECT_NAME_COLLISION')
            kube.dry_run(obj)
        for obj in objects:
            record = {'key': identity(obj), 'uid': None, 'content_sha256': obj['metadata']['annotations'][HASH]}
            receipt['objects'].append(record)
            receipt['pending'] = {'operation': 'create', 'key': identity(obj)}
            save()
            current = kube.create(obj)
            uid = created_uid(current, obj)
            record['uid'] = uid
            save()  # Preserve the create-response UID even if later content/default validation fails.
            assert_owned(current, obj, uid)
            receipt['pending'] = None
            save()
        pod, pod_uid = objects[-1], receipt['objects'][-1]['uid']
        wait_ready(kube, pod, pod_uid)
        with tunnel_factory(pod):
            client = client_factory(material['ca_path'])
            receipt['readiness'].append(client.ready('keycloak'))
            save()
            bearers = dict(material['negative_tokens'])
            require(set(bearers) == {*NEGATIVES, 'control'}, 'TOKEN_FIXTURES_INVALID')
            for user in USERS:
                receipt['pending'] = {'operation': 'token-issuance', 'user': user}
                save()
                bearer, projected = client.acquire(user, material['credentials'], material['public_key_path'])
                bearers[user] = bearer
                secrets.append(bearer)
                receipt['tokens'].append(projected)
                receipt['pending'] = None
                save()
            receipt['tokens'].append(token_projection(bearers['control'], 'lab_admin', material['public_key_path'], fixture_control=True))
            receipt['readiness'].append(client.ready('trino', bearers['lab_admin']))
            save()
            seen = {}
            for case, token_key, expected in CASES:
                # This observation includes query dispatch/polling and OPA
                # correlation. It is not a measurement of SQL engine latency.
                with audit.case(case) if audit is not None else nullcontext() as observed:
                    def progress(result):
                        receipt['pending'] = {'operation': 'query', **{key: value for key, value in result.items() if key != 'rows'},
                                              'rows_observed': len(result['rows'])}
                        save()
                        if observed is not None:
                            observed.progress(result)
                    result = client.query(case, bearers[token_key] if token_key else None, progress)
                    require(result['result'] == expected and result['response_complete'], 'CASE_RESULT_MISMATCH')
                    if expected == 'success':
                        user = 'lab_admin' if case == 'fixture_control' else token_key
                        require(result['rows'] == expected_rows(user), 'CASE_ROWS_MISMATCH')
                    else:
                        require(result['rows'] == [], 'DENIED_ROWS_OBSERVED')
                    decisions, seen, log_sha = collect_audit(kube, pod, case, result, seen)
                    receipt['decisions'].extend(decisions)
                    receipt['cases'].append({key: value for key, value in result.items() if key != 'rows'} | {
                        'row_count': len(result['rows']), 'matched': True, 'audit_decision_ids': [v['decision_id'] for v in decisions],
                        'opa_log_sha256': log_sha, 'token_source': 'none' if token_key is None else (
                            'host-signed-validation-fixture' if token_key in {*NEGATIVES, 'control'} else 'keycloak-password-grant')})
                    save()
                    if observed is not None:
                        observed.finish(result, decisions)
                receipt['pending'] = None
                save()
            receipt['readiness'].append(client.ready('keycloak'))
            receipt['readiness'].append(client.ready('trino', bearers['lab_admin']))
            current = kube.get(pod)
            assert_owned(current, pod, pod_uid)
            require(all(v.get('restartCount') == 0 for v in current['status']['containerStatuses']), 'POD_RESTARTED')
            receipt['result'] = 'verified'
            save()
    except BaseException as error:
        receipt['result'] = 'failed' if isinstance(error, Exception) else 'interrupted'
        receipt['failure_code'] = str(error) if isinstance(error, ExperimentError) else 'IDENTITY_EXPERIMENT_FAILED'
        receipt['diagnostics'] = []
        save()
        record = next((r for r in receipt['objects'] if r['key'] == identity(objects[-1])), None)
        if record and record.get('uid'):
            try:
                assert_owned(kube.get(objects[-1]), objects[-1], record['uid'])
                for component in ('trino', 'opa', 'keycloak'):
                    raw = redact(kube.logs(objects[-1], component), secrets)
                    write(output / ('diagnostic-' + component + '.json'), {'pod_uid': record['uid'], 'redacted_log': raw})
                    receipt['diagnostics'].append({'component': component, 'sha256': digest(raw.encode())})
            except Exception:
                receipt['diagnostics'].append({'result': 'unavailable'})
        save()
        raise
    finally:
        original_error = sys.exc_info()[1]
        cleanup(kube, receipt, objects, save)
        if receipt['result'] == 'verified':
            receipt['result'] = ('passed' if audit is None else 'verified') if receipt['cleanup']['verified'] else 'failed'
        save()
        if audit is not None:
            try:
                audit.record_cleanup(receipt['cleanup'])
            except BaseException:
                # Cleanup has already run. Keep an original cancellation or
                # runtime exception instead of replacing it with delivery error.
                if original_error is None:
                    raise
    require(receipt['result'] == ('passed' if audit is None else 'verified'), 'CLEANUP_UNVERIFIED')


def recover(run_token, kube=None):
    output = STATE / 'runs' / token(run_token)
    with run_lock(output):
        plan, original = json.loads(read(output / 'plan.json')), json.loads(read(output / 'receipt.json'))
        require(plan['run_token'] == original['run_token'] == run_token and plan['build_token'] == original['build_token']
                and original['images'] == plan['images'] and original['plan_sha256'] == digest(plan), 'RECOVERY_IDENTITY_MISMATCH')
        objects = validate_plan(plan)
        allowed = {identity(obj): obj for obj in objects}
        require(type(original['objects']) is list and len(original['objects']) <= 4
                and len({r['key'] for r in original['objects']}) == len(original['objects'])
                and all(r['key'] in allowed and r['content_sha256'] == allowed[r['key']]['metadata']['annotations'][HASH]
                        for r in original['objects']), 'RECOVERY_OBJECTS_INVALID')
        receipt = {'schema_version': 1, 'run_token': run_token, 'original_result': original['result'],
                   'objects': copy.deepcopy(original['objects']), 'cleanup': None}
        def save():
            write(output / 'recovery.json', receipt)
        cleanup(kube or Kubernetes(), receipt, objects, save)
        require(receipt['cleanup']['verified'], 'RECOVERY_UNVERIFIED')
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'run', 'recover'))
    parser.add_argument('--build-token')
    parser.add_argument('--run-token')
    parser.add_argument('--telemetry', action='store_true', help='Record this new run in AgentHouse, Loki and private OTel spans')
    args = parser.parse_args()
    run_token = None
    try:
        require(not args.telemetry or args.command == 'run', 'TELEMETRY_RUN_ONLY')
        if args.command == 'recover':
            require(args.run_token and not args.build_token, 'RECOVERY_ARGUMENTS')
            run_token = token(args.run_token)
            check_context()
            with interruption_handlers():
                recover(run_token)
        else:
            require(args.build_token and not args.run_token, 'RUN_ARGUMENTS')
            images = image_build.load_build(args.build_token)['images']
            run_token, output = new_output('runs')
            with run_lock(output), interruption_handlers():
                material = fixtures.create_fixtures(run_token, output)
                plan = {'schema_version': 1, 'run_token': run_token, 'build_token': args.build_token, 'images': images,
                        'objects': render(images, run_token, material)}
                validate_plan(plan)
                write(output / 'plan.json', plan)
                if args.command == 'run':
                    check_context()
                    if args.telemetry:
                        execute(plan, material, output, telemetry_factory=open_telemetry)
                    else:
                        execute(plan, material, output)
        print(canonical({'result': 'passed', 'mode': args.command, 'run_token': run_token,
                         'evidence': 'tmp/local-experiment/trino-identity/runs/' + run_token}))
        return 0
    except BaseException as error:
        print(canonical({'result': 'failed', 'run_token': run_token,
            'failure_code': str(error) if isinstance(error, ExperimentError) else 'IDENTITY_EXPERIMENT_FAILED'}))
        return 128 + error.signum if hasattr(error, 'signum') else 1


if __name__ == '__main__':
    raise SystemExit(main())
