"""Bounded native Spark experiment in a fresh, disposable namespace only."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.spark import contract as C, prepare as P
from tools.local_experiment.trino_opa.common import (
    ExperimentError, canonical, directory, read, require, run_lock, write)
from tools.local_experiment import local_session as SESSION

STATE = ROOT / 'tmp/local-experiment/spark-runs'
CONTEXT = P.CONTEXT
KINDS = {'Namespace': ('v1', 'namespaces'), 'ResourceQuota': ('v1', 'resourcequotas'),
         'LimitRange': ('v1', 'limitranges'), 'ServiceAccount': ('v1', 'serviceaccounts'),
         'Role': ('rbac.authorization.k8s.io/v1', 'roles'),
         'RoleBinding': ('rbac.authorization.k8s.io/v1', 'rolebindings'),
         'ConfigMap': ('v1', 'configmaps'), 'Service': ('v1', 'services'),
         'NetworkPolicy': ('networking.k8s.io/v1', 'networkpolicies'), 'Pod': ('v1', 'pods'),
         'Endpoints': ('v1', 'endpoints'), 'EndpointSlice': ('discovery.k8s.io/v1', 'endpointslices')}
INVENTORY = ('pods,services,configmaps,serviceaccounts,roles,rolebindings,resourcequotas,'
             'limitranges,networkpolicies,persistentvolumeclaims,secrets,deployments,'
             'replicasets,statefulsets,daemonsets,jobs,cronjobs,endpoints,endpointslices,events')
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def save(path, value):
    write(path, value)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def now():
    return datetime.now(timezone.utc).isoformat()


def error_code(error):
    text = str(error)
    return text if isinstance(error, (ExperimentError, C.ContractError)) and re.fullmatch(r'[A-Z0-9_]{1,100}', text) else type(error).__name__


def identity(obj):
    m = obj.get('metadata', {})
    require(type(m.get('uid')) is str and UUID.fullmatch(m['uid']) and
            type(m.get('resourceVersion')) is str and m['resourceVersion'].isdigit(), 'RESOURCE_IDENTITY_INVALID')
    return {'kind': obj['kind'], 'name': m['name'], 'uid': m['uid'], 'resource_version': m['resourceVersion']}


def owned(obj, token, *, uid=None):
    m = obj.get('metadata', {})
    require(m.get('labels', {}).items() >= C.labels(token).items() and
            m.get('annotations', {}).get('shirokuma.io/owner') == C.OWNER, 'RESOURCE_OWNER_CONFLICT')
    require((obj.get('kind') == 'Namespace' and m.get('name') == C.namespace(token)) or
            (obj.get('kind') != 'Namespace' and m.get('namespace') == C.namespace(token)), 'RESOURCE_SCOPE_CONFLICT')
    value = identity(obj)
    require(uid is None or value['uid'] == uid, 'RESOURCE_UID_CONFLICT')
    return value


def quantity(value, *, cpu=False):
    require(type(value) is str and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?(?:[EPTGMK]i|[EPTGMk]|m|n|u)?', value), 'RESOURCE_QUANTITY_INVALID')
    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)(.*)', value)
    factors = {'': 1, 'm': .001, 'u': .000001, 'n': .000000001,
               'k': 1000, 'M': 1000**2, 'G': 1000**3, 'T': 1000**4,
               'P': 1000**5, 'E': 1000**6, **{x+'i': 1024**n for n, x in enumerate('KMGTPE', 1)}}
    require(match[2] in factors and (not cpu or match[2] in ('', 'm', 'u', 'n')), 'RESOURCE_QUANTITY_INVALID')
    return float(match[1]) * factors[match[2]]


def api_addresses(service, endpoints):
    require(service.get('metadata', {}).get('namespace') == 'default' and
            service['metadata'].get('name') == 'kubernetes' and
            endpoints.get('metadata', {}).get('namespace') == 'default' and
            endpoints['metadata'].get('name') == 'kubernetes', 'API_IDENTITY_INVALID')
    ports = service.get('spec', {}).get('ports', [])
    require(len(ports) == 1 and ports[0].get('protocol', 'TCP') == 'TCP' and ports[0].get('port') == 443,
            'API_SERVICE_INVALID')
    values = [{'ip': service['spec']['clusterIP'], 'port': 443}]
    subsets = endpoints.get('subsets', [])
    require(len(subsets) == 1 and not subsets[0].get('notReadyAddresses'), 'API_ENDPOINTS_INVALID')
    ep = subsets[0].get('ports', [])
    require(len(ep) == 1 and ep[0].get('protocol', 'TCP') == 'TCP' and
            ports[0].get('targetPort') == ep[0].get('port'), 'API_ENDPOINT_PORT_MISMATCH')
    for address in subsets[0].get('addresses', []):
        values.append({'ip': address['ip'], 'port': ep[0]['port']})
    require(len(values) >= 2, 'API_ENDPOINTS_EMPTY')
    C.network_policies('0'*32, values)
    return values


class Kube:
    def __init__(self, token):
        self.token, self.namespace = token, C.namespace(token)

    def request(self, args, body=None, *, namespace=True):
        command = ['kubectl', '--context', CONTEXT, '--request-timeout=10s']
        if namespace:
            command += ['-n', self.namespace]
        return P.command(command + args, body=canonical(body) if body is not None else None,
                         timeout=15, output_limit=2*1024**2)

    def get(self, kind, name):
        raw = self.request(['get', kind, name, '--ignore-not-found=true', '-o', 'json'], namespace=kind != 'Namespace')
        return json.loads(raw) if raw.strip() else None

    def create(self, obj):
        return json.loads(self.request(['create', '-f', '-', '-o', 'json'], obj, namespace=obj['kind'] != 'Namespace'))

    def inventory(self):
        values = json.loads(self.request(['get', INVENTORY, '-o', 'json']))['items']
        require(len(values) <= 128, 'NAMESPACE_INVENTORY_LIMIT')
        return values

    def pods(self):
        values = json.loads(self.request(['get', 'pods', '-o', 'json']))['items']
        require(len(values) <= 2, 'UNEXPECTED_POD_COUNT')
        return values

    def logs(self):
        return self.request(['logs', 'driver', '-c', 'spark-driver', '--limit-bytes='+str(C.MAX_LOG_BYTES)])

    def delete(self, obj):
        kind, m = obj['kind'], obj['metadata']
        version, plural = KINDS[kind]
        base = '/api/v1' if version == 'v1' else '/apis/' + version
        path = base + ('' if kind == 'Namespace' else '/namespaces/' + self.namespace) + '/' + plural + '/' + m['name']
        return self.request(['delete', '--raw='+path, '-f', '-'], {
            'apiVersion': 'v1', 'kind': 'DeleteOptions', 'propagationPolicy': 'Background' if kind == 'Service' else 'Orphan',
            'gracePeriodSeconds': 15 if kind == 'Pod' else 0,
            'preconditions': {'uid': m['uid'], 'resourceVersion': m['resourceVersion']}}, namespace=False)

    def preflight(self):
        runtime = P.guard()
        cache = P.validate_cache(json.loads(P.command(P.docker('image', 'inspect', C.IMAGE))),
            json.loads(P.command(P.ssh('sudo', 'k3s', 'crictl', '--timeout', '10s', 'inspecti', '--output', 'json', C.IMAGE))),
            P.cache_content())
        nodes = json.loads(self.request(['get', 'nodes', '-o', 'json'], namespace=False))['items']
        require(len(nodes) == 1, 'NODE_COUNT_CHANGED')
        node = nodes[0]
        require(node['metadata']['uid'] == runtime['node_uid'] and not node.get('spec', {}).get('unschedulable') and
                any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in node['status'].get('conditions', [])) and
                not node.get('spec', {}).get('taints'), 'NODE_NOT_READY')
        pods = json.loads(self.request(['get', 'pods', '-A', '-o', 'json'], namespace=False))['items']
        require(len(pods) <= 256, 'PREFLIGHT_POD_LIMIT')
        used = {'cpu': 0., 'memory': 0.}
        for pod in pods:
            if pod.get('status', {}).get('phase') in ('Succeeded', 'Failed'):
                continue
            for key in used:
                def demand(container):
                    return quantity(container.get('resources', {}).get('requests', {}).get(key, '0'), cpu=key == 'cpu')
                spec = pod['spec']
                used[key] += max(sum(demand(c) for c in spec.get('containers', [])),
                                 max([demand(c) for c in spec.get('initContainers', [])] or [0]))
                used[key] += quantity(spec.get('overhead', {}).get(key, '0'), cpu=key == 'cpu')
        available = {k: quantity(node['status']['allocatable'][k], cpu=k == 'cpu') - used[k] for k in used}
        require(available['cpu'] >= 2 and available['memory'] >= 4*1024**3, 'INSUFFICIENT_CAPACITY')
        for verb, resource in [('create', 'namespaces'), ('delete', 'namespaces')] + [
                (v, r) for v in ('create', 'delete', 'get', 'list') for r in
                ('pods', 'services', 'configmaps', 'serviceaccounts', 'roles', 'rolebindings', 'resourcequotas', 'limitranges', 'networkpolicies')]:
            require(self.request(['auth', 'can-i', verb, resource], namespace=resource != 'namespaces').strip() == 'yes', 'PERMISSION_DENIED')
        service = json.loads(self.request(['get', 'service', 'kubernetes', '-n', 'default', '-o', 'json'], namespace=False))
        endpoints = json.loads(self.request(['get', 'endpoints', 'kubernetes', '-n', 'default', '-o', 'json'], namespace=False))
        return {'runtime': runtime, 'image': cache, 'available': available,
                'api_endpoints': api_addresses(service, endpoints),
                'api_service_uid': service['metadata']['uid'], 'api_endpoints_uid': endpoints['metadata']['uid']}

    def recovery_guard(self, previous):
        require(P.guard() == previous['runtime'], 'RECOVERY_RUNTIME_CHANGED')


def validate_pod(pod, token, role, *, driver_uid=None):
    result = owned(pod, token)
    expected = C.pod_spec(role)
    spec = pod.get('spec', {})
    require(pod['metadata']['name'] == ('driver' if role == 'driver' else C.executor_name(token)) and
            pod['metadata']['labels'].get('shirokuma.io/role') == role, 'POD_NAME_CONFLICT')
    require(not any(spec.get(key) for key in ('hostNetwork', 'hostPID', 'hostIPC', 'hostUsers')) and
            not spec.get('initContainers') and not spec.get('ephemeralContainers'), 'POD_HOST_ACCESS')
    for key in ('securityContext', 'restartPolicy', 'serviceAccountName', 'automountServiceAccountToken', 'nodeSelector'):
        require(spec.get(key) == expected[key], 'POD_SECURITY_CONFLICT')
    containers = spec.get('containers', [])
    require(len(containers) == 1, 'POD_CONTAINER_CONFLICT')
    container = containers[0]
    template = expected['containers'][0]
    for key in ('name', 'image', 'imagePullPolicy', 'securityContext'):
        require(container.get(key) == template[key], 'POD_IMAGE_OR_SECURITY_CONFLICT')
    resources = container.get('resources', {})
    for kind in ('requests', 'limits'):
        for key in ('cpu', 'memory'):
            require(quantity(resources.get(kind, {}).get(key, '0'), cpu=key == 'cpu') ==
                    quantity(template['resources'][kind][key], cpu=key == 'cpu'), 'POD_RESOURCE_CONFLICT')
    volumes = spec.get('volumes', [])
    allowed = {v['name']: v for v in expected['volumes']}
    for volume in volumes:
        if volume.get('name') in allowed:
            require(volume == allowed[volume['name']], 'POD_VOLUME_CONFLICT')
        elif role == 'driver' and volume.get('name') == 'workload':
            require(volume.get('configMap', {}).get('name') == 'workload' and set(volume) == {'name', 'configMap'}, 'POD_VOLUME_CONFLICT')
        else:
            require(role == 'driver' and volume.get('name', '').startswith('kube-api-access-') and
                    set(volume) == {'name', 'projected'} and
                    all(set(source) in ({'serviceAccountToken'}, {'configMap'}, {'downwardAPI'})
                        for source in volume['projected'].get('sources', [])), 'POD_VOLUME_CONFLICT')
    require(set(allowed) <= {v['name'] for v in volumes}, 'POD_VOLUME_MISSING')
    if role == 'executor':
        refs = pod['metadata'].get('ownerReferences', [])
        require(len(refs) == 1 and refs[0].get('kind') == 'Pod' and refs[0].get('name') == 'driver' and
                refs[0].get('uid') == driver_uid and refs[0].get('controller') is True, 'EXECUTOR_OWNER_CONFLICT')
    statuses = pod.get('status', {}).get('containerStatuses', [])
    require(len(statuses) <= 1, 'POD_STATUS_INVALID')
    image_id = statuses[0].get('imageID') if statuses else None
    if image_id:
        aliases = {name+'@'+P.DIGEST for name in ('spark', 'library/spark', 'docker.io/library/spark')}
        accepted = {P.CONFIG_DIGEST, P.DIGEST, 'docker://'+P.CONFIG_DIGEST, 'docker://'+P.DIGEST} | aliases | {
            'docker-pullable://'+name for name in aliases}
        require(image_id in accepted, 'POD_IMAGE_ID_CONFLICT')
    result.update(role=role, image_id=image_id, phase=pod.get('status', {}).get('phase'),
                  node_name=spec.get('nodeName'), hostname=spec.get('hostname', pod['metadata']['name']))
    return result


def source_hashes():
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__), Path(C.__file__), Path(P.__file__))}


def validate_saved(plan, receipt):
    token = receipt['run_token']; C.validate_token(token); C.validate_mode(receipt['mode'])
    require(type(plan) is list and C.plan_sha256(plan) == receipt['plan_sha256'], 'SAVED_PLAN_HASH_CONFLICT')
    require(len(plan) == 14 and plan[0]['kind'] == 'Namespace' and plan[-1]['kind'] == 'Pod', 'SAVED_PLAN_SCOPE_INVALID')
    names = {('Namespace', C.namespace(token)), ('ResourceQuota', 'experiment-cap'), ('LimitRange', 'container-cap'),
             ('ServiceAccount', 'driver'), ('ServiceAccount', 'executor'), ('Role', 'spark-driver'),
             ('RoleBinding', 'spark-driver'), ('ConfigMap', 'workload'), ('Service', 'driver'), ('Pod', 'driver'),
             ('NetworkPolicy', 'default-deny'), ('NetworkPolicy', 'same-run-rpc'),
             ('NetworkPolicy', 'cluster-dns'), ('NetworkPolicy', 'driver-api')}
    require(len(names) == len(plan) and {(x['kind'], x['metadata']['name']) for x in plan} == names, 'SAVED_PLAN_SCOPE_INVALID')
    for obj in plan:
        require(obj['kind'] in KINDS and obj['metadata'].get('labels', {}).items() >= C.labels(token).items() and
                obj['metadata'].get('annotations', {}).get('shirokuma.io/owner') == C.OWNER and
                (obj['metadata']['name'] == C.namespace(token) if obj['kind'] == 'Namespace' else
                 obj['metadata'].get('namespace') == C.namespace(token)), 'SAVED_PLAN_SCOPE_INVALID')
    known = receipt.get('known', [])
    allowed = names | {('Pod', C.executor_name(token))}
    require(len(known) <= 15 and len({(x['kind'], x['name']) for x in known}) == len(known) and
            all((x['kind'], x['name']) in allowed and UUID.fullmatch(x['uid']) for x in known), 'SAVED_IDENTITIES_INVALID')
    auto = receipt.get('automatic_endpoints', [])
    service = next((x for x in known if (x['kind'], x['name']) == ('Service', 'driver')), None)
    code = 'SAVED_ENDPOINT_IDENTITIES_INVALID'
    require(type(auto) is list and len(auto) <= 8, code)
    for item in auto:
        require(type(item) is dict and set(item) == {'kind', 'name', 'uid', 'resource_version', 'service_uid'} and
                all(type(value) is str for value in item.values()), code)
        require(item['kind'] in ('Endpoints', 'EndpointSlice') and
                (item['name'] == 'driver' if item['kind'] == 'Endpoints' else
                 re.fullmatch(r'driver-[a-z0-9]{5}', item['name'])) and
                UUID.fullmatch(item['uid']) and re.fullmatch(r'[0-9]{1,32}', item['resource_version']) and
                service is not None and item['service_uid'] == service['uid'], code)
    require(len({(x['kind'], x['name']) for x in auto}) == len(auto) and
            len({x['uid'] for x in auto}) == len(auto), code)


class Experiment:
    def __init__(self, token, mode, output, kube=None, *, clock=time.monotonic, sleep=time.sleep):
        C.validate_token(token); C.validate_mode(mode)
        self.token, self.mode, self.output = token, mode, Path(output)
        self.kube, self.clock, self.sleep = kube or Kube(token), clock, sleep
        self.plan = []
        self.receipt = {'schema_version': 1, 'run_token': token, 'mode': mode, 'namespace': C.namespace(token),
                        'result': 'running', 'started_at': now(), 'known': [], 'pending': None,
                        'pods': {}, 'automatic_endpoints': [], 'cleanup': {'verified': False, 'deleted': []},
                        'source_hashes': source_hashes()}

    def persist(self):
        save(self.output / 'summary.json', self.receipt)

    def remember(self, obj):
        value = identity(obj)
        matches = [x for x in self.receipt['known'] if x['kind'] == value['kind'] and x['name'] == value['name']]
        require(not matches or matches[0]['uid'] == value['uid'], 'RESOURCE_UID_CONFLICT')
        if not matches:
            self.receipt['known'].append(value)
        self.persist()

    def cleanup(self):
        result = {'verified': False, 'deleted': [], 'deferred': None}
        self.receipt['cleanup'] = result
        interruption = None
        try:
            known = self.receipt['known']
            ns = next((x for x in known if x['kind'] == 'Namespace'), None)
            if ns is None:
                require(not self.receipt.get('creation_attempted') and not (
                    self.receipt.get('pending', {}) or {}).get('operation') == 'create', 'NAMESPACE_UID_UNKNOWN')
                result['verified'] = True
                return result
            current = self.kube.get('Namespace', self.receipt['namespace'])
            if current is None:
                result.update(verified=True, namespace_already_absent=True)
                return result
            owned(current, self.token, uid=ns['uid'])
            by_name = {(x['kind'], x['name']): x for x in known}
            automatic = {('ServiceAccount', 'default'), ('ConfigMap', 'kube-root-ca.crt')}
            automatic_endpoints = self.receipt.setdefault('automatic_endpoints', [])
            def check_inventory(items, *, capture=False):
              for obj in items:
                key = (obj['kind'], obj['metadata']['name'])
                if key in by_name:
                    owned(obj, self.token, uid=by_name[key]['uid'])
                elif key in automatic or obj['kind'] == 'Event':
                    continue
                elif obj['kind'] in ('Endpoints', 'EndpointSlice'):
                    service = by_name.get(('Service', 'driver'))
                    refs = obj['metadata'].get('ownerReferences', [])
                    saved = next((x for x in automatic_endpoints if (x['kind'], x['name']) == key), None)
                    require(obj['metadata'].get('namespace') == self.receipt['namespace'], 'AUTOMATIC_ENDPOINT_CONFLICT')
                    if saved is not None:
                        require(identity(obj)['uid'] == saved['uid'], 'AUTOMATIC_ENDPOINT_UID_CONFLICT')
                    else:
                        current_service = self.kube.get('Service', 'driver') if capture else None
                        require(capture and service is not None and current_service is not None and
                                owned(current_service, self.token, uid=service['uid']) and
                                ((obj['kind'] == 'Endpoints' and key[1] == 'driver') or
                                 (obj['metadata'].get('labels', {}).get('kubernetes.io/service-name') == 'driver' and
                                  len(refs) == 1 and refs[0].get('kind') == 'Service' and refs[0].get('uid') == service['uid'])),
                                'AUTOMATIC_ENDPOINT_CONFLICT')
                        require(len(automatic_endpoints) < 8, 'AUTOMATIC_ENDPOINT_LIMIT')
                        automatic_endpoints.append({**identity(obj), 'service_uid': service['uid']})
                else:
                    raise ExperimentError('UNKNOWN_NAMESPACE_RESOURCE')
            inventory = self.kube.inventory()
            save(self.output / 'cleanup-inventory-before.json', inventory)
            check_inventory(inventory, capture=True)
            self.persist()  # Save generated endpoint UIDs before deleting their Service.
            for item in reversed(known):
                if item['kind'] == 'Namespace':
                    continue
                current = self.kube.get(item['kind'], item['name'])
                if current is not None:
                    owned(current, self.token, uid=item['uid'])
                    self.receipt['pending'] = {'operation': 'delete', **item}; self.persist()
                    self.kube.delete(current)
                result['deleted'].append({**item, 'already_absent': current is None})
                self.receipt['pending'] = None; self.persist()
            # A second inventory refuses any newly introduced object before namespace GC.
            check_inventory(self.kube.inventory())
            for item in automatic_endpoints:
                current = self.kube.get(item['kind'], item['name'])
                if current is not None:
                    require(current['metadata'].get('namespace') == self.receipt['namespace'] and
                            identity(current)['uid'] == item['uid'], 'AUTOMATIC_ENDPOINT_UID_CONFLICT')
                    self.receipt['pending'] = {'operation': 'delete', **item}; self.persist()
                    self.kube.delete(current)
                result['deleted'].append({**item, 'already_absent': current is None})
                self.receipt['pending'] = None; self.persist()
            current = self.kube.get('Namespace', self.receipt['namespace'])
            if current is not None:
                owned(current, self.token, uid=ns['uid'])
                self.receipt['pending'] = {'operation': 'delete', **ns}; self.persist()
                self.kube.delete(current)
            deadline = self.clock() + 45
            while self.kube.get('Namespace', self.receipt['namespace']) is not None:
                require(self.clock() < deadline, 'NAMESPACE_DELETE_TIMEOUT'); self.sleep(.5)
            result['deleted'].append(ns); result['verified'] = True
            self.receipt['pending'] = None
        except BaseException as error:
            result['deferred'] = error_code(error)
            if not isinstance(error, Exception):
                interruption = error
        finally:
            try:
                self.persist()
            finally:
                if interruption is not None:
                    raise interruption
        return result

    def diagnostics(self):
        if not any(x['kind'] == 'Pod' and x['name'] == 'driver' for x in self.receipt['known']):
            return
        for name, action in [('final-pods.json', self.kube.pods), ('driver-log.json', lambda: {'text': self.kube.logs()})]:
            try:
                save(self.output / name, action())
            except BaseException as error:
                self.receipt.setdefault('diagnostic_errors', {})[name] = error_code(error)

    def run(self):
        original = None
        self.persist()
        try:
            self.receipt['preflight'] = self.kube.preflight()
            self.plan = C.render(self.token, self.mode, api_endpoints=self.receipt['preflight']['api_endpoints'])
            self.receipt['plan_sha256'] = C.plan_sha256(self.plan)
            save(self.output / 'plan.json', self.plan); self.persist()
            require(self.kube.get('Namespace', self.receipt['namespace']) is None, 'NAMESPACE_ALREADY_EXISTS')
            for obj in self.plan:
                if obj['kind'] != 'Namespace':
                    ns = next(x for x in self.receipt['known'] if x['kind'] == 'Namespace')
                    current_namespace = self.kube.get('Namespace', self.receipt['namespace'])
                    require(current_namespace is not None, 'NAMESPACE_DISAPPEARED')
                    owned(current_namespace, self.token, uid=ns['uid'])
                self.receipt['pending'] = {'operation': 'create', 'kind': obj['kind'], 'name': obj['metadata']['name']}
                self.receipt['creation_attempted'] = True
                self.persist()
                current = self.kube.create(obj)
                require(current.get('kind') == obj['kind'] and current.get('metadata', {}).get('name') == obj['metadata']['name'],
                        'CREATE_RESPONSE_SCOPE_CONFLICT')
                self.remember(current)  # Capture response UID before further interpretation.
                save(self.output / ('created-'+obj['kind']+'-'+obj['metadata']['name']+'.json'), current)
                owned(current, self.token)
                self.receipt['pending'] = None; self.persist()
            driver = next(x for x in self.receipt['known'] if x['kind'] == 'Pod')
            deadline = self.clock() + 270
            terminal = None
            while terminal is None:
                require(self.clock() < deadline, 'WORKLOAD_TIMEOUT')
                for pod in self.kube.pods():
                    role = 'driver' if pod['metadata']['name'] == 'driver' else 'executor'
                    value = validate_pod(pod, self.token, role, driver_uid=driver['uid'])
                    require(role != 'driver' or value['uid'] == driver['uid'], 'DRIVER_UID_CONFLICT')
                    self.remember(pod)
                    self.receipt['pods'][role] = value
                    save(self.output / (role+'-pod.json'), pod)
                    if role == 'driver':
                        statuses = pod.get('status', {}).get('containerStatuses', [])
                        candidate = statuses[0].get('state', {}).get('terminated') if statuses else None
                        terminal = candidate if value['phase'] in ('Succeeded', 'Failed') else None
                self.persist()
                if terminal is None:
                    self.sleep(.4)
            require(type(terminal.get('exitCode')) is int and terminal['exitCode'] == C.expected_exit_code(self.mode), 'DRIVER_EXIT_MISMATCH')
            require(self.receipt['pods']['driver']['phase'] == ('Succeeded' if self.mode == 'success' else 'Failed'), 'DRIVER_PHASE_MISMATCH')
            require(set(self.receipt['pods']) == {'driver', 'executor'} and
                    all(x.get('image_id') and x.get('node_name') == self.receipt['preflight']['runtime']['node_name']
                        for x in self.receipt['pods'].values()), 'POD_EXECUTION_NOT_OBSERVED')
            executor = self.receipt['pods']['executor']
            require(executor['hostname'] == executor['name'], 'EXECUTOR_HOSTNAME_CONFLICT')
            logs = self.kube.logs()
            save(self.output / 'driver-log.json', {'text': logs})
            output = C.parse_output(logs, self.token, self.mode, executor_pod_name=executor['name'])
            self.receipt['output'] = output
            self.receipt['driver_exit_code'] = terminal['exitCode']
            self.receipt['workload_verified'] = True
        except BaseException as error:
            original = error
            self.receipt['failure_code'] = error_code(error)
        finally:
            if original is not None:
                self.diagnostics()
            try:
                self.cleanup()
            except BaseException as error:
                self.receipt['cleanup'] = {'verified': False, 'deferred': error_code(error)}
                if original is None:
                    original = error
            self.receipt['result'] = 'passed' if original is None and self.receipt['cleanup']['verified'] else 'failed'
            self.receipt['finished_at'] = now()
            try:
                self.persist()
            finally:
                if original is not None:
                    raise original
        require(self.receipt['cleanup']['verified'], 'CLEANUP_NOT_VERIFIED')
        return self.receipt


def output_directory(token):
    directory(STATE, create=True)
    output = STATE / token
    require(not output.exists() and not output.is_symlink(), 'OUTPUT_ALREADY_EXISTS')
    output.mkdir(mode=0o700)
    return output


def recover(token, kube=None):
    C.validate_token(token)
    output = STATE / token
    receipt = json.loads(read(output / 'summary.json')); plan = json.loads(read(output / 'plan.json'))
    require(receipt['run_token'] == token, 'RECOVERY_TOKEN_CONFLICT')
    validate_saved(plan, receipt)
    with run_lock(output):
        selected_kube = kube or Kube(token)
        selected_kube.recovery_guard(receipt['preflight'])
        recovery = output / ('recovery-'+uuid.uuid4().hex)
        recovery.mkdir(mode=0o700)
        instance = Experiment(token, receipt['mode'], recovery, kube=selected_kube)
        instance.receipt = deepcopy(receipt)
        instance.receipt.update(result='recovery', recovered_from_sha256=hashlib.sha256((output/'summary.json').read_bytes()).hexdigest())
        instance.cleanup()
        require(instance.receipt['cleanup']['verified'], 'CLEANUP_NOT_VERIFIED')
        return instance.receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'run', 'recover'))
    parser.add_argument('--mode', choices=('success', 'failure'), default='success')
    parser.add_argument('--run-token')
    args = parser.parse_args(argv)
    output = None
    try:
        with SESSION.interruption_handlers():
            if args.action == 'recover':
                require(args.run_token is not None, 'RUN_TOKEN_REQUIRED')
                value = recover(args.run_token)
                token = args.run_token
            else:
                require(args.run_token is None, 'RUN_TOKEN_GENERATED_ONLY')
                token = uuid.uuid4().hex; output = output_directory(token)
                with run_lock(output):
                    if args.action == 'plan':
                        preflight = Kube(token).preflight()
                        plan = C.render(token, args.mode, api_endpoints=preflight['api_endpoints'])
                        save(output/'plan.json', plan)
                        value = {'result': 'planned', 'run_token': token, 'mode': args.mode, 'preflight': preflight,
                                 'source_hashes': source_hashes(), 'plan_sha256': C.plan_sha256(plan), 'runtime_created': False}
                        save(output/'summary.json', value)
                    else:
                        value = Experiment(token, args.mode, output).run()
        print(json.dumps({'result': value['result'], 'run_token': token, 'evidence': str((STATE/token).relative_to(ROOT))}))
        return 0
    except BaseException as error:
        if output is not None:
            try:
                save(output/'interruption.json', {'error_code': error_code(error), 'interrupted': not isinstance(error, Exception)})
            except Exception:
                pass
        print(json.dumps({'result': 'failed', 'error_code': error_code(error)}))
        return 128 + error.signum if isinstance(error, SESSION.SessionInterrupted) else 1


if __name__ == '__main__':
    raise SystemExit(main())
