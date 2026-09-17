#!/usr/bin/env python3
"""Disposable, synthetic-header Trino/OPA demo; no retained catalog connection."""
import argparse
from contextlib import contextmanager
import copy
import json
import os
import re
import selectors
import signal
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from common import (CONTEXT, DEPLOY, HERE, NAMESPACE, PINS, ROOT, STATE, ExperimentError,
                    canonical, command, digest, interruption_handlers, load_build,
                    new_output, read, require, run_lock, stop_group, token, write)
from build import source_hashes
from local_experiment import check_context

OWNER = 'shirokuma.dev/trino-opa-run'
HASH = 'shirokuma.dev/content-sha256'
SELECT = 'SELECT nationkey, name FROM tpch.tiny.nation ORDER BY nationkey'
NATIONS = ('ALGERIA', 'ARGENTINA', 'BRAZIL', 'CANADA', 'EGYPT', 'ETHIOPIA', 'FRANCE', 'GERMANY',
           'INDIA', 'INDONESIA', 'IRAN', 'IRAQ', 'JAPAN', 'JORDAN', 'KENYA', 'MOROCCO', 'MOZAMBIQUE',
           'PERU', 'CHINA', 'ROMANIA', 'SAUDI ARABIA', 'VIETNAM', 'RUSSIA', 'UNITED KINGDOM', 'UNITED STATES')
CASES = (
    ('admin_full', 'lab_admin', SELECT, [[i, name] for i, name in enumerate(NATIONS)]),
    ('row_filter', 'lab_row', SELECT, [[0, 'ALGERIA']]),
    ('column_mask', 'lab_mask', SELECT, [[i, 'MASKED'] for i in range(25)]),
    ('combined', 'lab_both', SELECT, [[0, 'MASKED']]),
    ('denied_user', 'lab_denied', SELECT, None),
    ('denied_column', 'lab_admin', 'SELECT comment FROM tpch.tiny.nation', None),
    # This deliberately shows the unauthenticated client's ability to choose the admin header.
    ('header_is_not_authentication', 'lab_admin', SELECT, [[i, name] for i, name in enumerate(NATIONS)]),
)
QUERY_ID = re.compile(r'\d{8}_\d{6}_\d{5}_[a-z0-9]{5}')
KINDS = {'ConfigMap': ('v1', 'configmaps'), 'Pod': ('v1', 'pods'),
         'NetworkPolicy': ('networking.k8s.io/v1', 'networkpolicies')}


def configs(mode):
    require(mode in {'normal', 'unavailable'}, 'INVALID_MODE')
    port = 8181 if mode == 'normal' else 8182
    return {
        'config.properties': '\n'.join([
            'coordinator=true', 'node-scheduler.include-coordinator=true', 'http-server.http.port=8080',
            'discovery.uri=http://127.0.0.1:8080', 'query.max-memory=512MB', 'query.max-memory-per-node=512MB',
            'memory.heap-headroom-per-node=512MB', 'query.max-run-time=30s', 'query.max-execution-time=20s',
            'access-control.config-files=/etc/trino/opa.properties,/etc/trino/readonly.properties', '']),
        'jvm.config': '\n'.join(['-server', '-Xmx2G', '-XX:+ExitOnOutOfMemoryError',
            '-XX:ReservedCodeCacheSize=256M', '-Djdk.nio.maxCachedBufferSize=2000000', '-Djava.io.tmpdir=/tmp',
            '--add-modules=jdk.incubator.vector', '--enable-native-access=ALL-UNNAMED', '']),
        'node.properties': 'node.environment=shirokuma_opa_shadow\nnode.id=shirokuma-opa-shadow\nnode.data-dir=/var/trino\n',
        'log.properties': 'io.trino=INFO\n', 'tpch.properties': 'connector.name=tpch\ntpch.column-naming=SIMPLIFIED\n',
        'readonly.properties': 'access-control.name=read-only\n',
        'opa.properties': '\n'.join(['access-control.name=opa',
            f'opa.policy.uri=http://127.0.0.1:{port}/v1/data/trino/allow',
            f'opa.policy.row-filters-uri=http://127.0.0.1:{port}/v1/data/trino/rowFilters',
            f'opa.policy.column-masking-uri=http://127.0.0.1:{port}/v1/data/trino/columnMask',
            'opa.allow-permission-management-operations=false', 'opa.log-requests=false', 'opa.log-responses=false',
            'opa.http-client.connect-timeout=2s', 'opa.http-client.request-timeout=5s', '']),
    }


def seal(value, run_token):
    value['metadata'].update(namespace=NAMESPACE, labels={OWNER: token(run_token),
        'app.kubernetes.io/part-of': 'shirokuma', 'shirokuma.dev/scope': 'local-experiment'})
    value['metadata']['annotations'] = {HASH: digest(value)}
    return value


def render(build, run_token, mode):
    require(build.get('policy_sha256') == source_hashes(), 'BUILD_POLICY_MISMATCH')
    name = 'opa-' + token(run_token) + '-' + mode
    config = seal({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': name + '-config'},
                   'immutable': True, 'data': configs(mode)}, run_token)
    security = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}
    containers = [
        {'name': 'trino', 'image': build['images']['trino'], 'imagePullPolicy': 'Never',
         'securityContext': security, 'ports': [{'name': 'http', 'containerPort': 8080}],
         'resources': {'requests': {'cpu': '500m', 'memory': '1Gi'}, 'limits': {'cpu': '2', 'memory': '4Gi'}},
         'startupProbe': {'httpGet': {'path': '/v1/info', 'port': 8080}, 'periodSeconds': 2, 'failureThreshold': 90},
         'readinessProbe': {'httpGet': {'path': '/v1/info', 'port': 8080}, 'periodSeconds': 2},
         'volumeMounts': [{'name': 'config', 'mountPath': '/etc/trino', 'readOnly': True},
                          {'name': 'tmp', 'mountPath': '/tmp'}, {'name': 'data', 'mountPath': '/var/trino'}]},
        {'name': 'opa', 'image': build['images']['opa'], 'imagePullPolicy': 'Never',
         'securityContext': security,
         'args': ['run', '--server', '--addr=127.0.0.1:8181', '--authorization=basic', '--log-format=json',
                  '--log-level=info', '--set=decision_logs.console=true', '/policy/trino.rego', '/policy/api.rego'],
         'resources': {'requests': {'cpu': '50m', 'memory': '32Mi'}, 'limits': {'cpu': '500m', 'memory': '256Mi'}}},
    ]
    pod = seal({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name}, 'spec': {
        'automountServiceAccountToken': False, 'restartPolicy': 'Never', 'terminationGracePeriodSeconds': 10,
        'securityContext': {'runAsNonRoot': True, 'runAsUser': 10000, 'runAsGroup': 10000, 'fsGroup': 10000,
                            'seccompProfile': {'type': 'RuntimeDefault'}},
        'containers': containers, 'volumes': [
            {'name': 'config', 'configMap': {'name': config['metadata']['name'], 'items': [
                {'key': key, 'path': 'catalog/tpch.properties' if key == 'tpch.properties' else key} for key in configs(mode)]}},
            {'name': 'tmp', 'emptyDir': {'sizeLimit': '256Mi'}}, {'name': 'data', 'emptyDir': {'sizeLimit': '512Mi'}},
        ]}}, run_token)
    policy = seal({'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy', 'metadata': {'name': name},
                   'spec': {'podSelector': {'matchLabels': {OWNER: run_token}},
                            'policyTypes': ['Ingress', 'Egress'], 'ingress': [], 'egress': []}}, run_token)
    return [policy, config, pod]


def identity(obj):
    return (obj['kind'], obj['metadata']['name'])


def assert_owned(current, expected, uid=None):
    require(current.get('apiVersion') == expected['apiVersion'] and current.get('kind') == expected['kind']
            and current.get('metadata', {}).get('name') == expected['metadata']['name']
            and current['metadata'].get('namespace') == NAMESPACE
            and current['metadata'].get('labels', {}).get(OWNER) == expected['metadata']['labels'][OWNER]
            and current['metadata'].get('annotations', {}).get(HASH) == expected['metadata']['annotations'][HASH], 'OBJECT_OWNERSHIP_CONFLICT')
    actual_uid = current['metadata'].get('uid')
    require(type(actual_uid) is str and str(uuid.UUID(actual_uid)) == actual_uid
            and (uid is None or actual_uid == uid), 'OBJECT_UID_CONFLICT')
    if current['kind'] == 'ConfigMap':
        require(current.get('data') == expected['data'] and current.get('immutable') is True, 'OBJECT_CONTENT_CONFLICT')
    if current['kind'] == 'NetworkPolicy':
        # Kubernetes may omit empty ingress/egress arrays.
        actual = copy.deepcopy(current.get('spec', {}))
        actual.setdefault('ingress', [])
        actual.setdefault('egress', [])
        require(actual == expected['spec'], 'OBJECT_CONTENT_CONFLICT')
    if current['kind'] == 'Pod':
        actual, wanted = current.get('spec', {}), expected['spec']
        require(not any(actual.get(field) for field in ('hostNetwork', 'hostPID', 'hostIPC', 'initContainers', 'ephemeralContainers'))
                and actual.get('automountServiceAccountToken') is False
                and len(actual.get('containers', [])) == 2 and len(actual.get('volumes', [])) == 3, 'POD_SCOPE_CONFLICT')
        for item, desired in zip(actual['containers'], wanted['containers']):
            require(not item.get('env') and not item.get('envFrom') and item.get('name') == desired['name']
                    and item.get('image') == desired['image'] and item.get('imagePullPolicy') == 'Never'
                    and item.get('resources') == desired['resources'] and item.get('securityContext') == desired['securityContext']
                    and item.get('args', []) == desired.get('args', []) and not item.get('command')
                    and item.get('volumeMounts', []) == desired.get('volumeMounts', []), 'POD_SCOPE_CONFLICT')
        require(actual.get('securityContext') == wanted['securityContext'], 'POD_SCOPE_CONFLICT')
        for item, desired in zip(actual['volumes'], wanted['volumes']):
            value = copy.deepcopy(item)
            if 'configMap' in value:
                value['configMap'].pop('defaultMode', None)
            require(value == desired, 'POD_SCOPE_CONFLICT')
    return actual_uid


class Kubernetes:
    def request(self, args, body=None, timeout=40):
        return command(['kubectl', '--context', CONTEXT, '-n', NAMESPACE, '--request-timeout=30s', *args],
                       body=canonical(body) if body is not None else None, timeout=timeout)

    def get(self, expected):
        raw = self.request(['get', expected['kind'], expected['metadata']['name'], '--ignore-not-found=true', '-o', 'json'])
        return json.loads(raw) if raw.strip() else None

    def dry_run(self, expected):
        self.request(['create', '--dry-run=server', '-f', '-', '-o', 'json'], expected)

    def create(self, expected):
        return json.loads(self.request(['create', '-f', '-', '-o', 'json'], expected))

    def delete(self, expected, current):
        version, resource = KINDS[expected['kind']]
        prefix = '/api/' if version == 'v1' else '/apis/'
        path = prefix + version + '/namespaces/' + NAMESPACE + '/' + resource + '/' + expected['metadata']['name']
        self.request(['delete', '--raw=' + path, '-f', '-'], {'apiVersion': 'v1', 'kind': 'DeleteOptions',
            'propagationPolicy': 'Orphan',
            'gracePeriodSeconds': 10 if expected['kind'] == 'Pod' else 0,
            'preconditions': {'uid': current['metadata']['uid'], 'resourceVersion': current['metadata']['resourceVersion']}})

    def logs(self, pod):
        return self.request(['logs', pod['metadata']['name'], '-c', 'opa', '--limit-bytes=1048576', '--tail=1000'])

    def diagnostics(self, pod, container):
        return self.request(['logs', pod['metadata']['name'], '-c', container, '--limit-bytes=262144', '--tail=300'], timeout=15)


@contextmanager
def tunnel(pod):
    process = subprocess.Popen(['kubectl', '--context', CONTEXT, '-n', NAMESPACE, 'port-forward',
                                'pod/' + pod['metadata']['name'], ':8080', '--address=127.0.0.1'],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    data, deadline, port = b'', time.monotonic() + 30, None
    try:
        while time.monotonic() < deadline:
            require(process.poll() is None, 'TUNNEL_EXITED')
            if selector.select(timeout=0.5):
                data += os.read(process.stdout.fileno(), 4096)
                require(len(data) <= 8192, 'TUNNEL_OUTPUT_LIMIT')
                match = re.search(rb'Forwarding from 127\.0\.0\.1:(\d+) -> 8080', data)
                if match:
                    port = int(match[1])
                    break
        require(port is not None and 1 <= port <= 65535, 'TUNNEL_NOT_READY')
        yield 'http://127.0.0.1:' + str(port)
    finally:
        selector.close()
        stop_group(process, signal.SIGTERM, 5)
        process.stdout.close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ExperimentError('TRINO_REDIRECT_REFUSED')


def opa_transport_failure(error):
    classes = error_classes(error)
    # Trino's serialized failure uses the canonical nested-class name ('.');
    # Java's binary class name ('$') remains accepted for the same exact type.
    return (bool({'io.trino.plugin.opa.OpaQueryException.QueryFailed',
                  'io.trino.plugin.opa.OpaQueryException$QueryFailed'}.intersection(classes))
            and 'java.net.ConnectException' in classes)


def error_classes(error):
    info, result = error.get('failureInfo', {}), []
    for _ in range(16):
        if not isinstance(info, dict):
            break
        kind = info.get('type')
        if type(kind) is str and re.fullmatch(r'[A-Za-z0-9_.$]{1,180}', kind):
            result.append(kind)
        info = info.get('cause')
    return result


class Trino:
    def __init__(self, url):
        value = urllib.parse.urlsplit(url)
        require(value.scheme == 'http' and value.hostname == '127.0.0.1' and value.port
                and not value.path and not value.query and not value.fragment and not value.username, 'UNSAFE_TRINO_URL')
        self.url = url
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, url, user=None, sql=None):
        request = urllib.request.Request(url, data=sql.encode() if sql is not None else None,
            headers={'X-Trino-User': user} if user else {})
        with self.opener.open(request, timeout=10) as response:
            data = response.read(1024**2 + 1)
        require(len(data) <= 1024**2, 'TRINO_RESPONSE_LIMIT')
        return json.loads(data)

    def ready(self, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                result = self.request(self.url + '/v1/info')
            except urllib.error.HTTPError as error:
                if error.code not in {502, 503, 504}:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                pass
            else:
                require(result.get('nodeVersion', {}).get('version') == '483', 'TRINO_VERSION_MISMATCH')
                require(type(result.get('starting')) is bool, 'TRINO_READINESS_INVALID')
                if result['starting'] is False:
                    return
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        raise ExperimentError('TRINO_NOT_READY')

    def query(self, case, user, sql, progress):
        require((case, user, sql) in {(c[0], c[1], c[2]) for c in CASES}
                or (case, user, sql) == ('opa_unavailable', 'lab_admin', SELECT), 'QUERY_NOT_ALLOWED')
        result = {'case': case, 'user': user, 'query_id': None, 'result': 'pending', 'rows': [], 'error_name': None,
                  'opa_transport_failure': False, 'response_complete': False, 'sql_sha256': digest(sql.encode()),
                  'error_classes': []}
        progress(copy.deepcopy(result))
        uri, first, total, deadline, columns = self.url + '/v1/statement', True, 0, time.monotonic() + 40, None
        while True:
            require(time.monotonic() < deadline, 'QUERY_DEADLINE')
            value = self.request(uri, user, sql if first else None)
            first = False
            query_id = value.get('id')
            require(type(query_id) is str and QUERY_ID.fullmatch(query_id)
                    and result['query_id'] in {None, query_id}, 'QUERY_ID_MISMATCH')
            result['query_id'] = query_id
            if value.get('columns') is not None:
                require(type(value['columns']) is list, 'QUERY_COLUMNS_INVALID')
                projected = [(c.get('name'), c.get('type')) for c in value['columns']]
                require(columns is None or columns == projected, 'QUERY_COLUMNS_CHANGED')
                columns = projected
            rows = value.get('data', [])
            require(type(rows) is list and len(result['rows']) + len(rows) <= 30, 'QUERY_ROWS_LIMIT')
            result['rows'].extend(rows)
            total += len(canonical(value))
            require(total <= 2 * 1024**2, 'QUERY_BYTES_LIMIT')
            error = value.get('error')
            if error:
                result['result'] = 'denied' if error.get('errorName') == 'PERMISSION_DENIED' else 'failed'
                name = error.get('errorName')
                require(type(name) is str and re.fullmatch('[A-Z_]{1,80}', name), 'INVALID_ERROR_CLASS')
                result['error_name'] = name
                result['opa_transport_failure'] = opa_transport_failure(error)
                result['error_classes'] = error_classes(error)
                result['response_complete'] = True
                progress(copy.deepcopy(result))
                return result
            uri = value.get('nextUri')
            if uri is None:
                require(columns == [('nationkey', 'bigint'), ('name', 'varchar(25)')]
                        and all(type(r) is list and len(r) == 2 and type(r[0]) is int and 0 <= r[0] <= 24
                                and type(r[1]) is str for r in result['rows']), 'QUERY_RESULT_SCHEMA_INVALID')
                result['result'], result['response_complete'] = 'success', True
                progress(copy.deepcopy(result))
                return result
            parsed = urllib.parse.urlsplit(uri)
            pieces = parsed.path.split('/')
            require(parsed.scheme + '://' + parsed.netloc == self.url and not parsed.query and not parsed.fragment
                    and len(pieces) >= 6 and pieces[:3] == ['', 'v1', 'statement']
                    and pieces[3] in {'queued', 'executing'} and pieces[4] == query_id, 'UNSAFE_NEXT_URI')
            progress(copy.deepcopy(result))


def audit_decisions(text, cases):
    require(len(text.encode()) <= 1024**2, 'OPA_LOG_LIMIT')
    wanted = {value['query_id']: value['user'] for value in cases if value['query_id']}
    require(len(wanted) == sum(bool(value['query_id']) for value in cases), 'OPA_DUPLICATE_QUERY_ID')
    decisions, seen = [], set()
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            raise ExperimentError('OPA_LOG_MALFORMED') from None
        if value.get('type') != 'openpolicyagent.org/decision_logs':
            continue
        path = value.get('path')
        if path not in {'trino/allow', 'trino/rowFilters', 'trino/columnMask'}:
            continue
        source = value.get('input', {})
        context, action = source.get('context', {}), source.get('action', {})
        query_id, user = context.get('queryId'), context.get('identity', {}).get('user')
        operation = action.get('operation')
        require(user in {c[1] for c in CASES}
                and ((query_id is None and operation == 'ExecuteQuery') or wanted.get(query_id) == user), 'OPA_FOREIGN_DECISION')
        decision_id = value.get('decision_id')
        require(type(decision_id) is str and str(uuid.UUID(decision_id)) == decision_id and decision_id not in seen, 'OPA_DECISION_ID_INVALID')
        seen.add(decision_id)
        require(operation in {'ExecuteQuery', 'AccessCatalog', 'SelectFromColumns', 'GetRowFilters', 'GetColumnMask'}, 'OPA_UNEXPECTED_OPERATION')
        require((path == 'trino/allow' and operation in {'ExecuteQuery', 'AccessCatalog', 'SelectFromColumns'})
                or (path == 'trino/rowFilters' and operation == 'GetRowFilters')
                or (path == 'trino/columnMask' and operation == 'GetColumnMask'), 'OPA_PATH_OPERATION_MISMATCH')
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
        decisions.append({'decision_id': decision_id, 'query_id': query_id, 'user': user,
                          'operation': operation, 'outcome': outcome,
                          'correlation': 'query-id-and-user' if query_id is not None else 'user-and-execute-query-only'})
    for case in cases:
        if case['case'] == 'denied_user':
            require(any(d['user'] == 'lab_denied' and d['operation'] == 'ExecuteQuery' and d['outcome'] == 'deny' for d in decisions), 'OPA_DENY_AUDIT_MISSING')
        elif case['result'] == 'success':
            require(any(d['query_id'] == case['query_id'] and d['operation'] == 'SelectFromColumns' and d['outcome'] == 'allow' for d in decisions), 'OPA_ALLOW_AUDIT_MISSING')
        else:
            require(any(d['query_id'] == case['query_id'] and d['operation'] == 'SelectFromColumns' and d['outcome'] == 'deny' for d in decisions), 'OPA_DENY_AUDIT_MISSING')
    for case_name, outcome in [('row_filter', 'row_filter'), ('column_mask', 'column_mask'), ('combined', 'row_filter'), ('combined', 'column_mask')]:
        query_id = next(c['query_id'] for c in cases if c['case'] == case_name)
        require(any(d['query_id'] == query_id and d['outcome'] == outcome for d in decisions), 'OPA_FILTER_AUDIT_MISSING')
    return decisions


def wait_ready(kube, expected, uid, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = kube.get(expected)
        require(current is not None, 'POD_DISAPPEARED')
        assert_owned(current, expected, uid)
        status = current.get('status', {})
        require(status.get('phase') not in {'Failed', 'Succeeded'}, 'POD_TERMINATED')
        containers = status.get('containerStatuses', [])
        if len(containers) == 2 and all(c.get('ready') is True and c.get('restartCount') == 0 for c in containers):
            return
        time.sleep(1)
    raise ExperimentError('POD_NOT_READY')


def cleanup(kube, receipt, objects, save):
    receipt['cleanup'] = {'verified': False, 'removed': [], 'conflicts': [], 'deferred': []}
    # Pods first; the ConfigMap and network restriction remain until each Pod is gone.
    ordered = sorted(objects, key=lambda o: {'Pod': 0, 'ConfigMap': 1, 'NetworkPolicy': 2}[o['kind']])
    for expected in ordered:
        key = '/'.join(identity(expected))
        record = next((r for r in receipt['objects'] if r['key'] == key), None)
        if record is None:
            continue
        try:
            if record.get('uid') is None:
                receipt['cleanup']['deferred'].append(key)
                save()
                # A create collision or lost response is not proof that this UID was ours.
                if expected['kind'] == 'Pod':
                    break
                continue
            current = kube.get(expected)
            if current is None:
                continue
            uid = assert_owned(current, expected, record.get('uid'))
            kube.delete(expected, current)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                check = kube.get(expected)
                if check is None:
                    break
                assert_owned(check, expected, uid)
                time.sleep(0.25)
            else:
                raise ExperimentError('DELETE_UNVERIFIED')
            receipt['cleanup']['removed'].append({'key': key, 'uid': uid})
            save()
        except Exception:
            receipt['cleanup']['conflicts'].append(key)
            save()
            # Do not remove the isolation/configuration of a Pod that remains unverified.
            if expected['kind'] == 'Pod':
                break
    receipt['cleanup']['verified'] = not receipt['cleanup']['conflicts'] and not receipt['cleanup']['deferred']
    save()


def execute(build, run_token, output, kube=None, tunnel_factory=tunnel, client_factory=Trino):
    kube = kube or Kubernetes()
    all_objects = [o for mode in ('normal', 'unavailable') for o in render(build, run_token, mode)]
    receipt = {'schema_version': 1, 'run_token': run_token, 'build_token': build['build_token'],
               'result': 'running', 'scope': 'synthetic-header-authorization-only', 'authentication_verified': False,
               'images': build['images'], 'policy_sha256': build['policy_sha256'],
               'objects': [], 'cases': [], 'decisions': [], 'pending': None, 'cleanup': None, 'phase_cleanup': []}
    def save():
        write(output / 'receipt.json', receipt)
    save()
    try:
        # Collision checks and admission validation for BOTH modes precede all creation.
        for obj in all_objects:
            require(kube.get(obj) is None, 'OBJECT_NAME_COLLISION')
            kube.dry_run(obj)
        for mode in ('normal', 'unavailable'):
            objects = render(build, run_token, mode)
            for obj in objects:
                record = {'key': '/'.join(identity(obj)), 'uid': None, 'content_sha256': obj['metadata']['annotations'][HASH]}
                receipt['objects'].append(record)
                receipt['pending'] = {'operation': 'create', 'key': record['key']}
                save()
                current = kube.create(obj)
                record['uid'] = assert_owned(current, obj)
                receipt['pending'] = None
                save()
            pod = objects[-1]
            uid = next(o['uid'] for o in receipt['objects'] if o['key'] == '/'.join(identity(pod)))
            wait_ready(kube, pod, uid)
            with tunnel_factory(pod) as url:
                client = client_factory(url)
                client.ready()
                cases = CASES if mode == 'normal' else (('opa_unavailable', 'lab_admin', SELECT, None),)
                for name, user, sql, expected in cases:
                    def progress(value):
                        receipt['pending'] = {'operation': 'query', **{k: v for k, v in value.items() if k != 'rows'}, 'rows_observed': len(value['rows'])}
                        save()
                    result = client.query(name, user, sql, progress)
                    if name == 'opa_unavailable':
                        require(result['result'] == 'failed' and result['opa_transport_failure'] is True
                                and result['rows'] == [], 'OPA_UNAVAILABLE_NOT_PROVEN')
                    elif expected is None:
                        require(result['result'] == 'denied' and result['rows'] == [], 'DENIAL_NOT_PROVEN')
                    else:
                        require(result['result'] == 'success' and result['rows'] == expected, 'POLICY_RESULT_MISMATCH')
                    receipt['cases'].append({k: v for k, v in result.items() if k != 'rows'} | {'row_count': len(result['rows']), 'matched': True})
                    receipt['pending'] = None
                    save()
                # A reachable server after the unavailable-policy query excludes a dead-Trino explanation.
                client.ready()
            if mode == 'normal':
                raw = kube.logs(pod)
                receipt['decisions'] = audit_decisions(raw, receipt['cases'])
                receipt['decision_log_sha256'] = digest(raw.encode())
                save()
                cleanup(kube, receipt, objects, save)
                receipt['phase_cleanup'].append(copy.deepcopy(receipt['cleanup']))
                save()
                require(receipt['cleanup']['verified'], 'NORMAL_CLEANUP_UNVERIFIED')
        receipt['result'] = 'verified'
    except BaseException as error:
        receipt['result'] = 'failed' if isinstance(error, Exception) else 'interrupted'
        receipt['failure_code'] = str(error) if isinstance(error, ExperimentError) else 'EXPERIMENT_FAILED'
        save()
        receipt['diagnostics'] = []
        for expected in all_objects:
            if expected['kind'] != 'Pod':
                continue
            record = next((r for r in receipt['objects'] if r['key'] == '/'.join(identity(expected))), None)
            if record is None or record.get('uid') is None:
                continue
            try:
                current = kube.get(expected)
                if current is None:
                    continue
                assert_owned(current, expected, record['uid'])
                for container in ('trino', 'opa'):
                    raw = kube.diagnostics(expected, container)
                    filename = expected['metadata']['name'] + '-' + container + '.json'
                    write(output / filename, {'owned_pod_uid': current['metadata']['uid'], 'log': raw})
                    receipt['diagnostics'].append({'container': container, 'sha256': digest(raw.encode())})
            except Exception:
                receipt['diagnostics'].append({'result': 'unavailable'})
        save()
        raise
    finally:
        cleanup(kube, receipt, all_objects, save)
        if receipt['result'] == 'verified':
            receipt['result'] = 'passed' if receipt['cleanup']['verified'] else 'failed'
        save()
    require(receipt['result'] == 'passed', 'CLEANUP_UNVERIFIED')
    return receipt


def recovery_objects(plan, build, run_token):
    """Validate the saved v1 cleanup scope without invoking today's renderer/configs."""
    objects = plan.get('objects')
    require(type(objects) is list and len(objects) == 6, 'RECOVERY_PLAN_INVALID')
    expected_identities = [(kind, 'opa-' + run_token + '-' + mode + ('-config' if kind == 'ConfigMap' else ''))
                           for mode in ('normal', 'unavailable') for kind in ('NetworkPolicy', 'ConfigMap', 'Pod')]
    require([identity(o) for o in objects] == expected_identities, 'RECOVERY_PLAN_INVALID')
    labels = {OWNER: run_token, 'app.kubernetes.io/part-of': 'shirokuma', 'shirokuma.dev/scope': 'local-experiment'}
    security = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}
    for obj in objects:
        kind, name = identity(obj)
        require(set(obj) == {'apiVersion', 'kind', 'metadata', *({'immutable', 'data'} if kind == 'ConfigMap' else {'spec'})}
                and obj['apiVersion'] == KINDS[kind][0], 'RECOVERY_PLAN_INVALID')
        metadata = obj['metadata']
        require(set(metadata) == {'name', 'namespace', 'labels', 'annotations'} and metadata['namespace'] == NAMESPACE
                and metadata['labels'] == labels and set(metadata['annotations']) == {HASH}, 'RECOVERY_PLAN_INVALID')
        unsealed = copy.deepcopy(obj)
        unsealed['metadata'].pop('annotations')
        require(metadata['annotations'][HASH] == digest(unsealed), 'RECOVERY_HASH_MISMATCH')
        if kind == 'NetworkPolicy':
            require(obj['spec'] == {'podSelector': {'matchLabels': {OWNER: run_token}},
                    'policyTypes': ['Ingress', 'Egress'], 'ingress': [], 'egress': []}, 'RECOVERY_SCOPE_INVALID')
        elif kind == 'ConfigMap':
            require(obj['immutable'] is True and type(obj['data']) is dict
                    and set(obj['data']) == {'config.properties', 'jvm.config', 'node.properties', 'log.properties',
                                            'tpch.properties', 'readonly.properties', 'opa.properties'}
                    and all(type(v) is str and len(v.encode()) <= 16384 for v in obj['data'].values()), 'RECOVERY_SCOPE_INVALID')
        else:
            spec = obj['spec']
            require(set(spec) == {'automountServiceAccountToken', 'restartPolicy', 'terminationGracePeriodSeconds',
                                  'securityContext', 'containers', 'volumes'}
                    and spec['automountServiceAccountToken'] is False and spec['restartPolicy'] == 'Never'
                    and spec['terminationGracePeriodSeconds'] == 10
                    and spec['securityContext'] == {'runAsNonRoot': True, 'runAsUser': 10000, 'runAsGroup': 10000,
                        'fsGroup': 10000, 'seccompProfile': {'type': 'RuntimeDefault'}}, 'RECOVERY_SCOPE_INVALID')
            config = next(o for o in objects if o['kind'] == 'ConfigMap' and o['metadata']['name'] == name + '-config')
            volumes = copy.deepcopy(spec['volumes'])
            require(type(volumes) is list and len(volumes) == 3, 'RECOVERY_SCOPE_INVALID')
            items = volumes[0].get('configMap', {}).get('items')
            require(type(items) is list and all(type(item) is dict and type(item.get('key')) is str for item in items),
                    'RECOVERY_SCOPE_INVALID')
            volumes[0]['configMap']['items'] = sorted(items, key=lambda item: item['key'])
            require(volumes == [
                {'name': 'config', 'configMap': {'name': name + '-config', 'items': [
                    {'key': key, 'path': 'catalog/tpch.properties' if key == 'tpch.properties' else key} for key in sorted(config['data'])]}},
                {'name': 'tmp', 'emptyDir': {'sizeLimit': '256Mi'}}, {'name': 'data', 'emptyDir': {'sizeLimit': '512Mi'}}],
                'RECOVERY_SCOPE_INVALID')
            require(type(spec['containers']) is list and len(spec['containers']) == 2, 'RECOVERY_SCOPE_INVALID')
            for container, component in zip(spec['containers'], ('trino', 'opa')):
                extra = {'ports', 'startupProbe', 'readinessProbe', 'volumeMounts'} if component == 'trino' else {'args'}
                require(set(container) == {'name', 'image', 'imagePullPolicy', 'securityContext', 'resources', *extra}
                        and container['name'] == component and container['image'] == build['images'][component]
                        and container['imagePullPolicy'] == 'Never' and container['securityContext'] == security,
                        'RECOVERY_SCOPE_INVALID')
                resources = ({'requests': {'cpu': '500m', 'memory': '1Gi'}, 'limits': {'cpu': '2', 'memory': '4Gi'}}
                             if component == 'trino' else {'requests': {'cpu': '50m', 'memory': '32Mi'},
                                                          'limits': {'cpu': '500m', 'memory': '256Mi'}})
                require(container['resources'] == resources, 'RECOVERY_SCOPE_INVALID')
                if component == 'trino':
                    require(container['ports'] == [{'name': 'http', 'containerPort': 8080}]
                            and container['volumeMounts'] == [
                                {'name': 'config', 'mountPath': '/etc/trino', 'readOnly': True},
                                {'name': 'tmp', 'mountPath': '/tmp'}, {'name': 'data', 'mountPath': '/var/trino'}]
                            and container['startupProbe'] == {'httpGet': {'path': '/v1/info', 'port': 8080},
                                'periodSeconds': 2, 'failureThreshold': 90}
                            and container['readinessProbe'] == {'httpGet': {'path': '/v1/info', 'port': 8080}, 'periodSeconds': 2},
                            'RECOVERY_SCOPE_INVALID')
                else:
                    require(container['args'] == ['run', '--server', '--addr=127.0.0.1:8181', '--authorization=basic',
                            '--log-format=json', '--log-level=info', '--set=decision_logs.console=true',
                            '/policy/trino.rego', '/policy/api.rego'], 'RECOVERY_SCOPE_INVALID')
    return objects


def recover(run_token, kube=None):
    output = STATE / 'runs' / token(run_token)
    with run_lock(output):
        original = json.loads(read(output / 'receipt.json'))
        plan = json.loads(read(output / 'plan.json'))
        require(original.get('run_token') == run_token and plan.get('run_token') == run_token
                and original.get('build_token') == plan.get('build_token'), 'RECOVERY_IDENTITY_MISMATCH')
        # Cleanup uses the saved image/manifest identities, not today's Rego or input pins.
        build = load_build(plan['build_token'], verify_inputs=False)
        require(original.get('images') == build['images'] and original.get('policy_sha256') == build['policy_sha256'],
                'RECOVERY_BUILD_MISMATCH')
        objects = recovery_objects(plan, build, run_token)
        allowed = {'/'.join(identity(o)): o for o in objects}
        require(type(original.get('objects')) is list and len(original['objects']) <= 6
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
    args = parser.parse_args()
    run_token = None
    try:
        if args.command == 'recover':
            require(args.run_token and not args.build_token, 'RECOVERY_ARGUMENTS')
            run_token = token(args.run_token)
            check_context()
            with interruption_handlers():
                result = recover(run_token)
            print(canonical({'result': 'passed', 'mode': 'recover', 'run_token': run_token,
                             'removed': len(result['cleanup']['removed'])}))
            return 0
        require(args.build_token and not args.run_token, 'RUN_ARGUMENTS')
        run_token, output = new_output('runs')
        build = load_build(args.build_token)
        objects = [o for mode in ('normal', 'unavailable') for o in render(build, run_token, mode)]
        write(output / 'plan.json', {'run_token': run_token, 'build_token': build['build_token'], 'objects': objects})
        if args.command == 'run':
            check_context()
            with run_lock(output), interruption_handlers():
                receipt = execute(build, run_token, output)
            require(receipt['result'] == 'passed', 'EXPERIMENT_UNVERIFIED')
        print(canonical({'result': 'passed', 'mode': args.command, 'run_token': run_token,
                         'evidence': str(output.relative_to(ROOT))}))
        return 0
    except BaseException as error:
        print(canonical({'result': 'failed', 'run_token': run_token,
                         'failure_code': str(error) if isinstance(error, ExperimentError) else 'EXPERIMENT_FAILED'}))
        return 128 + error.signum if hasattr(error, 'signum') else 1


if __name__ == '__main__':
    raise SystemExit(main())
