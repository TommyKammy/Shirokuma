"""Owned deployments, services and per-size network boundaries for lifecycle tests."""
import base64
import copy
import ipaddress
import re
import uuid

from tools.local_experiment.gateway_warehouse import manifest as previous
from .fixtures import DATABASE, DATABASE_USER, gateway_config, name, trino_config
from .support import DEPLOY, NAMESPACE, PORTS, canonical, digest, require, token

OWNER = 'shirokuma.dev/warehouse-lifecycle-run'
HASH = 'shirokuma.dev/content-sha256'
ROLE = 'shirokuma.dev/warehouse-role'
SIZE = 'shirokuma.dev/warehouse-size'
TEMPLATE_HASH = 'shirokuma.dev/template-sha256'
KINDS = {'NetworkPolicy': ('networking.k8s.io/v1', 'networkpolicies'), 'ConfigMap': ('v1', 'configmaps'),
         'Secret': ('v1', 'secrets'), 'Pod': ('v1', 'pods'), 'Deployment': ('apps/v1', 'deployments'),
         'Service': ('v1', 'services'), 'ReplicaSet': ('apps/v1', 'replicasets')}
IMAGE_FAMILIES = {'gateway': 'trinodb/trino-gateway', 'postgres': 'cgr.dev/chainguard/postgres',
                  'trino': 'shirokuma.local/trino-opa'}
TRINO_KEYS = ('config.properties', 'jvm.config', 'node.properties', 'log.properties', 'tpch.properties', 'readonly.properties')
PUBLIC_KEYS = {size + '.' + key for size in ('xs', 's') for key in TRINO_KEYS} | {'routing-rules.yaml'}
SECRET_KEYS = {'postgres-user', 'postgres-password', 'postgres-db', 'gateway-config.yaml'}


def labels(run_token, role=None, size=None):
    value = {OWNER: token(run_token), 'app.kubernetes.io/name': 'shirokuma-warehouse-lifecycle'}
    if role is not None:
        require(role in ('gateway', 'backend'), 'ROLE_INVALID')
        value[ROLE] = role
    if size is not None:
        require(role == 'backend' and size in ('xs', 's'), 'WAREHOUSE_INVALID')
        value[SIZE] = size
    return value


def selector(run_token, role=None, size=None):
    result = {OWNER: token(run_token)}
    if role:
        result[ROLE] = role
    if size:
        result[SIZE] = size
    return result


def identity(obj):
    return obj['kind'] + '/' + obj['metadata']['name']


def seal(obj, run_token, role=None, size=None):
    obj = copy.deepcopy(obj)
    obj['metadata'].update(namespace=NAMESPACE, labels=labels(run_token, role, size))
    obj['metadata']['annotations'] = {HASH: digest(obj)}
    return obj


def network_objects(run_token):
    prefix = name(run_token)
    specs = [('deny', {'podSelector': {'matchLabels': selector(run_token)}, 'policyTypes': ['Ingress', 'Egress'],
                      'ingress': [], 'egress': []}),
             ('gateway', {'podSelector': {'matchLabels': selector(run_token, 'gateway')}, 'policyTypes': ['Egress'],
                          'egress': [{'to': [{'podSelector': {'matchLabels': selector(run_token, 'backend', size)}}],
                                      'ports': [{'protocol': 'TCP', 'port': PORTS[size]}]} for size in ('xs', 's')]})]
    for size in ('xs', 's'):
        specs.append((size, {'podSelector': {'matchLabels': selector(run_token, 'backend', size)},
                            'policyTypes': ['Ingress'], 'ingress': [{'from': [
                                {'podSelector': {'matchLabels': selector(run_token, 'gateway')}}],
                                'ports': [{'protocol': 'TCP', 'port': PORTS[size]}]}]}))
    return [seal({'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
                  'metadata': {'name': prefix + '-net-' + suffix}, 'spec': spec}, run_token)
            for suffix, spec in specs]


def _replace_references(value, run_token):
    """Reuse the proven container config, replacing only fixed owned CM/Secret names."""
    if type(value) is dict:
        return {key: _replace_references(item, run_token) for key, item in value.items()}
    if type(value) is list:
        return [_replace_references(item, run_token) for item in value]
    if type(value) is str:
        for suffix in ('-config', '-secret'):
            if value == previous.name(run_token) + suffix:
                return name(run_token) + suffix
    return value


def gateway_pod_spec(images, run_token):
    spec = _replace_references(previous.pod_spec(images, run_token), run_token)
    spec['containers'] = [container for container in spec['containers'] if container['name'] in ('postgres', 'gateway')]
    spec['volumes'] = [volume for volume in spec['volumes'] if volume['name'].startswith(('postgres-', 'gateway-'))]
    return spec


def backend_pod_spec(images, run_token, size):
    require(size in ('xs', 's'), 'WAREHOUSE_INVALID')
    spec = _replace_references(previous.pod_spec(images, run_token), run_token)
    spec['containers'] = [container for container in spec['containers'] if container['name'] == size]
    spec['volumes'] = [volume for volume in spec['volumes'] if volume['name'].startswith(size + '-')]
    spec['restartPolicy'] = 'Always'
    spec['securityContext']['fsGroup'] = 1000
    return spec


def deployment(images, run_token, size):
    template = {'metadata': {'labels': labels(run_token, 'backend', size)},
                'spec': backend_pod_spec(images, run_token, size)}
    template['metadata']['annotations'] = {TEMPLATE_HASH: digest(template)}
    return seal({'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': name(run_token) + '-' + size},
                 'spec': {'replicas': 1, 'selector': {'matchLabels': selector(run_token, 'backend', size)},
                          'strategy': {'type': 'Recreate'}, 'revisionHistoryLimit': 0, 'progressDeadlineSeconds': 120,
                          'template': template}}, run_token, 'backend', size)


def service(run_token, size):
    return seal({'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': name(run_token) + '-' + size},
        'spec': {'type': 'ClusterIP', 'selector': selector(run_token, 'backend', size),
                 'ports': [{'name': size, 'port': PORTS[size], 'targetPort': PORTS[size], 'protocol': 'TCP'}],
                 'sessionAffinity': 'None'}}, run_token, 'backend', size)


def render(images, run_token, material):
    token(run_token)
    require(set(material['public_data']) == PUBLIC_KEYS and set(material['secret_data']) == SECRET_KEYS,
            'FIXTURE_KEYS_INVALID')
    for size in ('xs', 's'):
        require(all(material['public_data'][size + '.' + key] == value
                    for key, value in trino_config(run_token, size).items()), 'FIXTURE_CONFIG_INVALID')
    secret = material['secret_data']
    require(secret['postgres-user'] == DATABASE_USER and secret['postgres-db'] == DATABASE
            and secret['gateway-config.yaml'] == canonical(gateway_config(secret['postgres-password'])) + '\n'
            and material['public_data']['routing-rules.yaml'] == (DEPLOY / 'routing-rules.yaml').read_text(),
            'FIXTURE_CONFIG_INVALID')
    prefix = name(run_token)
    objects = network_objects(run_token) + [
        seal({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': prefix + '-config'},
              'immutable': True, 'data': material['public_data']}, run_token),
        seal({'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': prefix + '-secret'}, 'immutable': True,
              'type': 'Opaque', 'data': {key: base64.b64encode(value.encode()).decode()
                                        for key, value in secret.items()}}, run_token),
        service(run_token, 'xs'), service(run_token, 's'),
        seal({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': prefix + '-gateway'},
              'spec': gateway_pod_spec(images, run_token)}, run_token, 'gateway'),
        deployment(images, run_token, 'xs'), deployment(images, run_token, 's'),
    ]
    validate_plan({'run_token': run_token, 'images': images, 'objects': objects})
    return objects


def _uid(value):
    require(type(value) is str, 'KNOWN_UID_REQUIRED')
    try:
        require(str(uuid.UUID(value)) == value, 'KNOWN_UID_REQUIRED')
    except ValueError:
        require(False, 'KNOWN_UID_REQUIRED')
    return value


def _version(value):
    require(type(value) is str and re.fullmatch(r'[0-9]{1,30}', value), 'RESOURCE_VERSION_REQUIRED')
    return value


def _subset(actual, expected):
    return type(actual) is dict and all(actual.get(key) == value for key, value in expected.items())


def _pod_scope(spec, images, run_token, size=None):
    """Validate saved disposable boundaries without using the current renderer."""
    require(set(spec) == {'automountServiceAccountToken', 'enableServiceLinks', 'restartPolicy',
            'terminationGracePeriodSeconds', 'securityContext', 'containers', 'volumes'}, 'PLAN_POD_SCOPE_INVALID')
    expected_names = [size] if size else ['postgres', 'gateway']
    expected_volumes = {size + '-config', size + '-tmp', size + '-data'} if size else {
        'gateway-config', 'gateway-tmp', 'postgres-data', 'postgres-run', 'postgres-tmp'}
    require([c.get('name') for c in spec['containers']] == expected_names
            and {v.get('name') for v in spec['volumes']} == expected_volumes
            and len(spec['volumes']) == len(expected_volumes), 'PLAN_POD_SCOPE_INVALID')
    # Validate the active parts directly; no current renderer is used for recovery.
    require(spec['automountServiceAccountToken'] is False and spec['enableServiceLinks'] is False
            and spec['restartPolicy'] == ('Always' if size else 'Never')
            and type(spec['terminationGracePeriodSeconds']) is int and 0 <= spec['terminationGracePeriodSeconds'] <= 60
            and spec['securityContext'] == {'runAsNonRoot': True, 'fsGroup': 1000 if size else 70,
                'fsGroupChangePolicy': 'OnRootMismatch', 'seccompProfile': {'type': 'RuntimeDefault'}}, 'PLAN_POD_SCOPE_INVALID')
    mount_targets = {s: {s + '-config': ('/etc/trino', True), s + '-tmp': ('/tmp', False),
                         s + '-data': ('/var/trino', False)} for s in ('xs', 's')}
    mount_targets.update(postgres={'postgres-data': ('/var/lib/postgresql/data', False),
        'postgres-run': ('/var/run/postgresql', False), 'postgres-tmp': ('/tmp', False)},
        gateway={'gateway-config': ('/etc/trino-gateway', True), 'gateway-tmp': ('/tmp', False)})
    for item in spec['containers']:
        component = item['name']
        required = {'name', 'image', 'imagePullPolicy', 'securityContext', 'resources', 'volumeMounts'}
        optional = {'readinessProbe', 'startupProbe', 'livenessProbe', 'ports'}
        optional |= {'args', 'env'} if component == 'postgres' else {'command'} if component == 'gateway' else set()
        require(required <= set(item) <= required | optional and item['image'] == images[
            'trino' if component in ('xs', 's') else component] and item['imagePullPolicy'] == 'Never', 'PLAN_POD_SCOPE_INVALID')
        uid, gid = (70, 70) if component == 'postgres' else (1000, 1000) if component == 'gateway' else (10000, 1000)
        require(item['securityContext'] == {'runAsNonRoot': True, 'runAsUser': uid, 'runAsGroup': gid,
            'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
            'PLAN_POD_SCOPE_INVALID')
        resources = item['resources']
        require(set(resources) == {'requests', 'limits'} and all(set(r) == {'cpu', 'memory'} for r in resources.values()),
                'PLAN_RESOURCES_INVALID')
        for resource, units, ceiling in [('cpu', {'': 1000, 'm': 1}, 2000),
                ('memory', {'Mi': 1024**2, 'Gi': 1024**3}, 4 * 1024**3)]:
            low, high = (previous._quantity(resources[key][resource], units) for key in ('requests', 'limits'))
            require(0 < low <= high <= ceiling, 'PLAN_RESOURCES_INVALID')
        mounts = item['volumeMounts']
        require(len(mounts) == len(mount_targets[component]) and {m.get('name') for m in mounts} == set(mount_targets[component]),
                'PLAN_MOUNTS_INVALID')
        for mount in mounts:
            require(set(mount) <= {'name', 'mountPath', 'readOnly'} and
                    (mount.get('mountPath'), mount.get('readOnly', False)) == mount_targets[component][mount['name']],
                    'PLAN_MOUNTS_INVALID')
        if component != 'postgres':
            require(item.get('ports') == [{'name': component, 'containerPort': PORTS[component]}], 'PLAN_PORTS_INVALID')
        else:
            require(not item.get('ports') and type(item.get('args')) is list and len(item['args']) <= 16
                    and all(type(arg) is str and 0 < len(arg) <= 256 for arg in item['args']), 'PLAN_POD_SCOPE_INVALID')
            wanted_env = [{'name': key, 'valueFrom': {'secretKeyRef': {'name': name(run_token) + '-secret', 'key': secret}}}
                for key, secret in [('POSTGRES_USER', 'postgres-user'), ('POSTGRES_PASSWORD', 'postgres-password'),
                                    ('POSTGRES_DB', 'postgres-db')]] + [{'name': 'PGDATA', 'value': '/var/lib/postgresql/data/pgdata'}]
            require(item.get('env') == wanted_env, 'PLAN_POD_SCOPE_INVALID')
        if component == 'gateway':
            command = item.get('command')
            require(type(command) is list and len(command) == 3 and command[:2] == ['/bin/bash', '-ec']
                    and type(command[2]) is str and 0 < len(command[2]) <= 4096, 'PLAN_POD_SCOPE_INVALID')
        for key in ('readinessProbe', 'startupProbe', 'livenessProbe'):
            if key not in item:
                continue
            probe = item[key]
            require(set(probe) <= {'httpGet', 'exec', 'periodSeconds', 'initialDelaySeconds', 'timeoutSeconds',
                    'successThreshold', 'failureThreshold'}, 'PLAN_PROBE_INVALID')
            for field in set(probe) - {'httpGet', 'exec'}:
                require(type(probe[field]) is int and 0 <= probe[field] <= 180, 'PLAN_PROBE_INVALID')
            if component == 'postgres':
                require('httpGet' not in probe and probe.get('exec') == {'command': [
                    'pg_isready', '-h', '127.0.0.1', '-U', 'vw_owner', '-d', 'vw_gateway']}, 'PLAN_PROBE_INVALID')
            else:
                endpoint = probe.get('httpGet', {})
                require('exec' not in probe and set(endpoint) == {'path', 'port'}
                        and endpoint['port'] == PORTS[component] and endpoint['path'] in (
                            ('/trino-gateway/livez', '/trino-gateway/readyz') if component == 'gateway' else ('/v1/info',)),
                        'PLAN_PROBE_INVALID')
    for volume in spec['volumes']:
        key = volume['name']
        if key in ('xs-config', 's-config'):
            s = key.split('-')[0]
            require(volume == {'name': key, 'configMap': {'name': name(run_token) + '-config', 'defaultMode': 0o440,
                'items': [{'key': s + '.' + field, 'path': 'catalog/tpch.properties' if field == 'tpch.properties' else field}
                          for field in TRINO_KEYS]}}, 'PLAN_VOLUMES_INVALID')
        elif key == 'gateway-config':
            require(volume == {'name': key, 'projected': {'defaultMode': 0o440, 'sources': [
                {'configMap': {'name': name(run_token) + '-config', 'items': [{'key': 'routing-rules.yaml', 'path': 'routing-rules.yaml'}]}},
                {'secret': {'name': name(run_token) + '-secret', 'items': [{'key': 'gateway-config.yaml', 'path': 'config.yaml'}]}}]}},
                'PLAN_VOLUMES_INVALID')
        else:
            require(set(volume) == {'name', 'emptyDir'} and set(volume['emptyDir']) == {'sizeLimit'}
                    and 0 < previous._quantity(volume['emptyDir']['sizeLimit'], {'Mi': 1024**2, 'Gi': 1024**3}) <= 4 * 1024**3,
                    'PLAN_VOLUMES_INVALID')


def validate_plan(plan):
    run_token, images = token(plan['run_token']), plan['images']
    require(set(images) == set(IMAGE_FAMILIES) and all(type(image) is str and re.fullmatch(
        re.escape(IMAGE_FAMILIES[key]) + r'@sha256:[0-9a-f]{64}', image) for key, image in images.items()), 'PLAN_IMAGES_INVALID')
    objects, prefix = plan['objects'], name(run_token)
    expected_keys = ['NetworkPolicy/' + prefix + '-net-' + suffix for suffix in ('deny', 'gateway', 'xs', 's')] + [
        'ConfigMap/' + prefix + '-config', 'Secret/' + prefix + '-secret', 'Service/' + prefix + '-xs',
        'Service/' + prefix + '-s', 'Pod/' + prefix + '-gateway', 'Deployment/' + prefix + '-xs', 'Deployment/' + prefix + '-s']
    require(type(objects) is list and [identity(o) for o in objects] == expected_keys, 'PLAN_OBJECTS_INVALID')
    nets = {identity(obj): obj for obj in network_objects(run_token)}
    for obj in objects:
        kind, meta = obj['kind'], obj['metadata']
        size = meta['name'].rsplit('-', 1)[-1] if kind in ('Deployment', 'Service') else None
        role = 'backend' if size else 'gateway' if kind == 'Pod' else None
        require(set(obj) == {'apiVersion', 'kind', 'metadata', *({'immutable', 'data', 'type'} if kind == 'Secret'
            else {'immutable', 'data'} if kind == 'ConfigMap' else {'spec'})} and obj['apiVersion'] == KINDS[kind][0]
            and set(meta) == {'name', 'namespace', 'labels', 'annotations'} and meta['namespace'] == NAMESPACE
            and meta['labels'] == labels(run_token, role, size) and set(meta['annotations']) == {HASH}, 'PLAN_SCOPE_INVALID')
        raw = copy.deepcopy(obj)
        raw['metadata'].pop('annotations')
        require(meta['annotations'][HASH] == digest(raw), 'PLAN_HASH_MISMATCH')
        if kind == 'NetworkPolicy':
            require(obj == nets[identity(obj)], 'PLAN_NETWORK_INVALID')
        elif kind in ('ConfigMap', 'Secret'):
            require(obj['immutable'] is True and set(obj['data']) == (SECRET_KEYS if kind == 'Secret' else PUBLIC_KEYS)
                    and all(type(v) is str and 0 < len(v) <= 131072 for v in obj['data'].values()), 'PLAN_SCOPE_INVALID')
            if kind == 'Secret':
                require(obj['type'] == 'Opaque', 'PLAN_SCOPE_INVALID')
                for value in obj['data'].values():
                    try:
                        base64.b64decode(value, validate=True).decode()
                    except (ValueError, UnicodeError):
                        require(False, 'PLAN_SECRET_INVALID')
        elif kind == 'Service':
            require(obj['spec'] == service(run_token, size)['spec'], 'PLAN_SERVICE_INVALID')
        elif kind == 'Pod':
            _pod_scope(obj['spec'], images, run_token)
        else:
            spec = obj['spec']
            require(set(spec) == {'replicas', 'selector', 'strategy', 'revisionHistoryLimit', 'progressDeadlineSeconds', 'template'}
                    and type(spec['replicas']) is int and spec['replicas'] in (0, 1)
                    and spec['selector'] == {'matchLabels': selector(run_token, 'backend', size)}
                    and spec['strategy'] == {'type': 'Recreate'} and spec['revisionHistoryLimit'] == 0
                    and type(spec['progressDeadlineSeconds']) is int and 30 <= spec['progressDeadlineSeconds'] <= 300,
                    'PLAN_DEPLOYMENT_INVALID')
            template = copy.deepcopy(spec['template'])
            require(set(template) == {'metadata', 'spec'} and set(template['metadata']) == {'labels', 'annotations'}
                    and template['metadata']['labels'] == labels(run_token, 'backend', size)
                    and set(template['metadata']['annotations']) == {TEMPLATE_HASH}, 'PLAN_TEMPLATE_INVALID')
            saved_hash = template['metadata'].pop('annotations')[TEMPLATE_HASH]
            require(saved_hash == digest(template), 'PLAN_TEMPLATE_HASH_MISMATCH')
            _pod_scope(template['spec'], images, run_token, size)
    return objects


def created_uid(current, expected):
    meta = current.get('metadata', {})
    require(current.get('apiVersion') == expected['apiVersion'] and current.get('kind') == expected['kind']
            and meta.get('name') == expected['metadata']['name'] and meta.get('namespace') == NAMESPACE
            and _subset(meta.get('labels'), expected['metadata']['labels'])
            and meta.get('annotations', {}).get(HASH) == expected['metadata']['annotations'][HASH]
            and not meta.get('ownerReferences'), 'OBJECT_IDENTITY_CONFLICT')
    return _uid(meta.get('uid'))


def _normalized_pod(spec, expected):
    value = copy.deepcopy(spec)
    fixed_defaults = {'dnsPolicy': 'ClusterFirst', 'schedulerName': 'default-scheduler', 'serviceAccountName': 'default',
                      'serviceAccount': 'default', 'priority': 0, 'preemptionPolicy': 'PreemptLowerPriority'}
    for key, default in fixed_defaults.items():
        if key not in expected and value.get(key) == default:
            value.pop(key)
    if 'nodeName' in value:
        require(type(value['nodeName']) is str and re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}', value['nodeName']),
                'POD_NODE_INVALID')
        value.pop('nodeName')
    if 'tolerations' in value:
        defaults = [
            {'key': 'node.kubernetes.io/not-ready', 'operator': 'Exists', 'effect': 'NoExecute', 'tolerationSeconds': 300},
            {'key': 'node.kubernetes.io/unreachable', 'operator': 'Exists', 'effect': 'NoExecute', 'tolerationSeconds': 300}]
        # Documented admission default for non-BestEffort Pods; no wildcard toleration.
        memory = {'key': 'node.kubernetes.io/memory-pressure', 'operator': 'Exists', 'effect': 'NoSchedule'}
        observed = value['tolerations']
        require(type(observed) is list and len(observed) in (2, 3)
                and {canonical(item) for item in observed} in (
                    {canonical(item) for item in defaults}, {canonical(item) for item in defaults + [memory]})
                and len({canonical(item) for item in observed}) == len(observed), 'POD_TOLERATIONS_CONFLICT')
        value.pop('tolerations')
    require(len(value.get('containers', [])) == len(expected['containers']), 'POD_SCOPE_CONFLICT')
    for item, goal in zip(value['containers'], expected['containers']):
        for key, default in {'terminationMessagePath': '/dev/termination-log', 'terminationMessagePolicy': 'File'}.items():
            if key not in goal and item.get(key) == default:
                item.pop(key)
        for port in item.get('ports', []):
            if port.get('protocol') == 'TCP':
                port.pop('protocol')
        for key in ('startupProbe', 'readinessProbe', 'livenessProbe'):
            if key not in item:
                continue
            probe, target = item[key], goal.get(key, {})
            for field, default in {'initialDelaySeconds': 0, 'timeoutSeconds': 1, 'successThreshold': 1,
                                    'failureThreshold': 3}.items():
                if field not in target and probe.get(field) == default:
                    probe.pop(field)
            if 'httpGet' in probe and probe['httpGet'].get('scheme') == 'HTTP':
                probe['httpGet'].pop('scheme')
    return value


def _template_equal(actual, expected, pod_hash=None):
    value = copy.deepcopy(actual)
    require(type(value) is dict and set(value) == {'metadata', 'spec'}, 'TEMPLATE_CONFLICT')
    meta = value['metadata']
    # API metadata default only; no free-form annotations may alter projected files.
    if meta.get('creationTimestamp', 'missing') is None:
        meta.pop('creationTimestamp')
    require(set(meta) == {'labels', 'annotations'}, 'TEMPLATE_CONFLICT')
    if pod_hash is not None:
        require(meta['labels'].pop('pod-template-hash', None) == pod_hash, 'TEMPLATE_CONFLICT')
    require(meta == expected['metadata'], 'TEMPLATE_CONFLICT')
    require(_normalized_pod(value['spec'], expected['spec']) == expected['spec'], 'POD_SCOPE_CONFLICT')


def assert_owned(current, expected, uid):
    require(created_uid(current, expected) == _uid(uid), 'OBJECT_IDENTITY_CONFLICT')
    kind = expected['kind']
    if kind in ('ConfigMap', 'Secret'):
        require(current.get('immutable') is True and current.get('data') == expected['data']
                and (kind != 'Secret' or current.get('type') == 'Opaque'), 'OBJECT_CONTENT_CONFLICT')
    elif kind == 'NetworkPolicy':
        spec = copy.deepcopy(current.get('spec', {}))
        goal = copy.deepcopy(expected['spec'])
        for key in ('ingress', 'egress'):
            spec.setdefault(key, [])
            goal.setdefault(key, [])
        require(spec == goal, 'OBJECT_CONTENT_CONFLICT')
    elif kind == 'Pod':
        require(_normalized_pod(current.get('spec', {}), expected['spec']) == expected['spec'], 'POD_SCOPE_CONFLICT')
    elif kind == 'Deployment':
        spec = copy.deepcopy(current.get('spec', {}))
        if spec.get('minReadySeconds') == 0:
            spec.pop('minReadySeconds')
        template = spec.pop('template', None)
        wanted = {key: value for key, value in expected['spec'].items() if key != 'template'}
        require(spec == wanted, 'DEPLOYMENT_SCOPE_CONFLICT')
        _template_equal(template, expected['spec']['template'])
    elif kind == 'Service':
        spec = copy.deepcopy(current.get('spec', {}))
        address = spec.pop('clusterIP', None)
        try:
            parsed = ipaddress.ip_address(address)
            require(parsed.version == 4 and parsed.is_private and not parsed.is_loopback and not parsed.is_link_local
                    and not parsed.is_unspecified and not parsed.is_multicast, 'SERVICE_IP_INVALID')
        except ValueError:
            require(False, 'SERVICE_IP_INVALID')
        require(spec.pop('clusterIPs', [address]) == [address]
                and spec.pop('ipFamilies', ['IPv4']) == ['IPv4']
                and spec.pop('ipFamilyPolicy', 'SingleStack') == 'SingleStack'
                and spec.pop('internalTrafficPolicy', 'Cluster') == 'Cluster', 'SERVICE_SCOPE_CONFLICT')
        require(spec == expected['spec'], 'SERVICE_SCOPE_CONFLICT')


def scale_patch(current, expected, uid, target):
    require(type(target) is int and target in (0, 1) and expected['kind'] == 'Deployment', 'SCALE_TARGET_INVALID')
    assert_owned(current, expected, uid)
    version = _version(current['metadata'].get('resourceVersion'))
    return [{'op': 'test', 'path': '/metadata/uid', 'value': uid},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': version},
            {'op': 'test', 'path': '/spec/replicas', 'value': expected['spec']['replicas']},
            {'op': 'replace', 'path': '/spec/replicas', 'value': target}]


def _child_scope(current, expected, parent_uid, *, known_uid=None, allow_orphan=False):
    kind, meta = current.get('kind'), current.get('metadata', {})
    require(kind in ('ReplicaSet', 'Pod') and current.get('apiVersion') == KINDS[kind][0]
            and meta.get('namespace') == NAMESPACE and _subset(meta.get('labels'), expected['metadata']['labels']),
            'CHILD_SCOPE_CONFLICT')
    uid = _uid(meta.get('uid'))
    _uid(parent_uid)
    if known_uid is not None:
        require(uid == _uid(known_uid), 'CHILD_UID_CONFLICT')
    references = meta.get('ownerReferences', [])
    if not references:
        require(allow_orphan is True and known_uid is not None, 'CHILD_PARENT_MISSING')
    else:
        require(type(references) is list and len(references) == 1 and references[0].get('controller') is True
                and references[0].get('apiVersion') == 'apps/v1'
                and references[0].get('kind') == ('Deployment' if kind == 'ReplicaSet' else 'ReplicaSet')
                and references[0].get('uid') == parent_uid, 'CHILD_PARENT_CONFLICT')
    pod_hash = meta.get('labels', {}).get('pod-template-hash')
    require(type(pod_hash) is str and re.fullmatch(r'[a-z0-9]{1,16}', pod_hash), 'CHILD_HASH_INVALID')
    rs_name = expected['metadata']['name'] + '-' + pod_hash
    if kind == 'ReplicaSet':
        require(meta.get('name') == rs_name and (not references or references[0].get('name') == expected['metadata']['name']),
                'CHILD_NAME_CONFLICT')
        spec = copy.deepcopy(current.get('spec', {}))
        require(set(spec) <= {'replicas', 'selector', 'template', 'minReadySeconds'} and type(spec.get('replicas')) is int
                and spec['replicas'] in (0, 1) and spec.get('minReadySeconds', 0) == 0, 'CHILD_SPEC_CONFLICT')
        wanted_selector = copy.deepcopy(expected['spec']['selector'])
        wanted_selector['matchLabels']['pod-template-hash'] = pod_hash
        require(spec.get('selector') == wanted_selector, 'CHILD_SELECTOR_CONFLICT')
        _template_equal(spec.get('template'), expected['spec']['template'], pod_hash)
    else:
        require(_generated_name_matches(meta.get('name'), rs_name + '-')
                and meta.get('generateName') == rs_name + '-'
                and (not references or references[0].get('name') == rs_name)
                and meta.get('annotations', {}).get(TEMPLATE_HASH) == expected['spec']['template']['metadata']['annotations'][TEMPLATE_HASH],
                'CHILD_NAME_CONFLICT')
        require(_normalized_pod(current.get('spec', {}), expected['spec']['template']['spec']) == expected['spec']['template']['spec'],
                'POD_SCOPE_CONFLICT')
    return uid, pod_hash


def _generated_name_matches(value, generate_name):
    # Kubernetes v1.33 SimpleNameGenerator reserves five characters within 63:
    # k8s.io/apiserver/pkg/storage/names/generate.go (MaxGeneratedNameLength=58).
    return type(value) is str and re.fullmatch(re.escape(generate_name[:58]) + r'[a-z0-9]{5}', value) is not None


def _endpoint_pod_name_matches(value, service_name):
    """Shape only; cleanup separately requires a previously verified Pod UID.

    An EndpointSlice has no ReplicaSet hash label. Check the possible generated
    shapes while preserving the complete service/run prefix and fixed suffix.
    """
    if type(value) is not str:
        return False
    prefix = service_name + '-'
    if len(value) <= 63 and re.fullmatch(re.escape(prefix) + r'[a-z0-9]{1,16}-[a-z0-9]{5}', value):
        return True
    visible_hash = 58 - len(prefix)
    return (1 <= visible_hash <= 16 and len(value) == 63
            and re.fullmatch(re.escape(prefix) + r'[a-z0-9]{' + str(visible_hash) + r'}[a-z0-9]{5}', value) is not None)


def assert_child(current, expected_deployment, parent_uid, known_uid=None, *, allow_orphan=False):
    """Allow orphan cleanup only for a UID recorded before its parent was deleted."""
    _child_scope(current, expected_deployment, parent_uid, known_uid=known_uid, allow_orphan=allow_orphan)
    return current


def _child_projection(current, expected, parent_uid):
    meta, status = current['metadata'], current.get('status', {})
    result = {'kind': current['kind'], 'name': meta['name'], 'uid': meta['uid'],
              'resource_version': _version(meta.get('resourceVersion')), 'parent_uid': parent_uid,
              'template_hash': expected['spec']['template']['metadata']['annotations'][TEMPLATE_HASH]}
    if current['kind'] == 'Pod':
        address = status.get('podIP')
        if address is not None:
            try:
                parsed = ipaddress.ip_address(address)
                require(parsed.version == 4 and parsed.is_private and not parsed.is_loopback
                        and not parsed.is_link_local and not parsed.is_unspecified, 'CHILD_IP_INVALID')
            except ValueError:
                require(False, 'CHILD_IP_INVALID')
        phase = status.get('phase')
        require(phase is None or phase in ('Pending', 'Running', 'Succeeded', 'Failed', 'Unknown'), 'CHILD_STATUS_INVALID')
        result.update(pod_ip=address, phase=phase, ready=any(
            item.get('type') == 'Ready' and item.get('status') == 'True' for item in status.get('conditions', [])),
            deleting=meta.get('deletionTimestamp') is not None)
    return result


def child_inventory(expected, current_deployment, replica_sets, pods, *, deployment_uid):
    """Fail on any matching child whose controller chain or immutable shape differs."""
    require(expected['kind'] == 'Deployment', 'CHILD_DEPLOYMENT_REQUIRED')
    assert_owned(current_deployment, expected, deployment_uid)
    require(type(replica_sets) is list and type(pods) is list and len(replica_sets) <= 4 and len(pods) <= 8,
            'CHILD_INVENTORY_LIMIT')
    children, parents = [], {}
    for current in replica_sets:
        require(current.get('kind') == 'ReplicaSet', 'CHILD_KIND_INVALID')
        uid, pod_hash = _child_scope(current, expected, deployment_uid)
        require(uid not in parents, 'CHILD_DUPLICATE')
        parents[uid] = pod_hash
        children.append(_child_projection(current, expected, deployment_uid))
    seen = set(parents)
    for current in pods:
        require(current.get('kind') == 'Pod', 'CHILD_KIND_INVALID')
        references = current.get('metadata', {}).get('ownerReferences', [])
        require(len(references) == 1 and references[0].get('uid') in parents, 'CHILD_PARENT_UNKNOWN')
        parent_uid = references[0]['uid']
        uid, pod_hash = _child_scope(current, expected, parent_uid)
        require(uid not in seen and pod_hash == parents[parent_uid], 'CHILD_CHAIN_CONFLICT')
        seen.add(uid)
        children.append(_child_projection(current, expected, parent_uid))
    return {'size': expected['metadata']['labels'][SIZE], 'deployment_uid': deployment_uid,
            'replicas': current_deployment['spec']['replicas'], 'children': children}


def validate_endpoints(expected_service, current_service, service_uid, endpoint_slices, owned_pod):
    """Validate Service routing to one Pod already verified through child_inventory."""
    require(expected_service['kind'] == 'Service', 'SERVICE_REQUIRED')
    assert_owned(current_service, expected_service, service_uid)
    require(type(endpoint_slices) is list and len(endpoint_slices) == 1, 'ENDPOINT_SLICE_COUNT_INVALID')
    meta, status = owned_pod.get('metadata', {}), owned_pod.get('status', {})
    require(owned_pod.get('kind') == 'Pod' and owned_pod.get('apiVersion') == 'v1'
            and meta.get('namespace') == NAMESPACE and _subset(meta.get('labels'), expected_service['spec']['selector'])
            and meta.get('deletionTimestamp') is None and status.get('phase') == 'Running'
            and any(item.get('type') == 'Ready' and item.get('status') == 'True'
                    for item in status.get('conditions', [])), 'ENDPOINT_POD_NOT_READY')
    pod_uid, address = _uid(meta.get('uid')), status.get('podIP')
    try:
        parsed = ipaddress.ip_address(address)
        require(parsed.version == 4 and parsed.is_private and not parsed.is_loopback and not parsed.is_link_local
                and not parsed.is_unspecified and not parsed.is_multicast, 'ENDPOINT_ADDRESS_INVALID')
    except ValueError:
        require(False, 'ENDPOINT_ADDRESS_INVALID')
    slice_obj = endpoint_slices[0]
    slice_meta = slice_obj.get('metadata', {})
    slice_uid = _uid(slice_meta.get('uid'))
    references = slice_meta.get('ownerReferences', [])
    require(slice_obj.get('apiVersion') == 'discovery.k8s.io/v1' and slice_obj.get('kind') == 'EndpointSlice'
            and slice_meta.get('namespace') == NAMESPACE
            and slice_meta.get('labels', {}).get('kubernetes.io/service-name') == expected_service['metadata']['name']
            and slice_meta.get('labels', {}).get('endpointslice.kubernetes.io/managed-by') == 'endpointslice-controller.k8s.io'
            and type(references) is list and len(references) == 1 and references[0].get('kind') == 'Service'
            and references[0].get('apiVersion') == 'v1' and references[0].get('controller') is True
            and references[0].get('uid') == service_uid and references[0].get('name') == expected_service['metadata']['name']
            and slice_obj.get('addressType') == 'IPv4', 'ENDPOINT_SLICE_OWNER_CONFLICT')
    size = expected_service['metadata']['labels'][SIZE]
    require(slice_obj.get('ports') == [{'name': size, 'protocol': 'TCP', 'port': PORTS[size]}], 'ENDPOINT_PORT_CONFLICT')
    endpoints = slice_obj.get('endpoints')
    require(type(endpoints) is list and len(endpoints) == 1, 'ENDPOINT_COUNT_INVALID')
    endpoint = endpoints[0]
    conditions, target = endpoint.get('conditions', {}), endpoint.get('targetRef', {})
    require(endpoint.get('addresses') == [address] and conditions.get('ready') is True
            and conditions.get('serving', True) is True and conditions.get('terminating', False) is False
            and target.get('kind') == 'Pod' and target.get('namespace') == NAMESPACE
            and target.get('name') == meta['name'] and target.get('uid') == pod_uid,
            'ENDPOINT_TARGET_CONFLICT')
    if 'nodeName' in endpoint:
        require(endpoint['nodeName'] == owned_pod.get('spec', {}).get('nodeName'), 'ENDPOINT_NODE_CONFLICT')
    return {'service_name': expected_service['metadata']['name'], 'service_uid': service_uid,
            'cluster_ip': current_service['spec']['clusterIP'], 'port': PORTS[size],
            'pod_name': meta['name'], 'pod_uid': pod_uid, 'pod_ip': address,
            'endpoint_slice': {'kind': 'EndpointSlice', 'name': slice_meta['name'], 'uid': slice_uid,
                'parent_uid': service_uid, 'resource_version': _version(slice_meta.get('resourceVersion'))}, 'verified': True}


def assert_endpoint(current, expected_service, service_uid, known_uid, *, allow_orphan=False, pod_uids=()):
    """Cleanup gate; changing readiness/empty endpoints is allowed, foreign Pods are not."""
    _uid(service_uid)
    known_uid = _uid(known_uid)
    allowed_pods = {_uid(value) for value in pod_uids}
    require(expected_service.get('kind') == 'Service', 'SERVICE_REQUIRED')
    meta = current.get('metadata', {})
    service_name = expected_service['metadata']['name']
    require(current.get('apiVersion') == 'discovery.k8s.io/v1' and current.get('kind') == 'EndpointSlice'
            and meta.get('namespace') == NAMESPACE and meta.get('uid') == known_uid
            and type(meta.get('name')) is str and re.fullmatch(re.escape(service_name) + r'-[a-z0-9]{5}', meta['name'])
            and meta.get('labels', {}).get('kubernetes.io/service-name') == service_name
            and meta.get('labels', {}).get('endpointslice.kubernetes.io/managed-by') == 'endpointslice-controller.k8s.io'
            and current.get('addressType') == 'IPv4', 'ENDPOINT_SLICE_SCOPE_CONFLICT')
    references = meta.get('ownerReferences', [])
    if not references:
        require(allow_orphan is True, 'ENDPOINT_PARENT_MISSING')
    else:
        require(type(references) is list and len(references) == 1 and references[0].get('kind') == 'Service'
                and references[0].get('apiVersion') == 'v1' and references[0].get('controller') is True
                and references[0].get('uid') == service_uid and references[0].get('name') == service_name,
                'ENDPOINT_SLICE_OWNER_CONFLICT')
    size = expected_service['metadata']['labels'][SIZE]
    endpoints = current.get('endpoints', [])
    if endpoints is None:
        endpoints = []
    require(type(endpoints) is list and len(endpoints) <= 4, 'ENDPOINT_COUNT_INVALID')
    fixed_ports = [{'name': size, 'protocol': 'TCP', 'port': PORTS[size]}]
    # The controller serializes an emptied slice as endpoints:null, ports:null.
    # This exception is for owned-slice cleanup only, never the ready routing gate.
    require(current.get('ports') == fixed_ports if endpoints else current.get('ports') in (None, [], fixed_ports),
            'ENDPOINT_PORT_CONFLICT')
    for endpoint in endpoints:
        target = endpoint.get('targetRef', {})
        require(target.get('kind') == 'Pod' and target.get('namespace') == NAMESPACE and target.get('uid') in allowed_pods
                and _endpoint_pod_name_matches(target.get('name'), service_name),
                'ENDPOINT_TARGET_CONFLICT')
        addresses = endpoint.get('addresses')
        require(type(addresses) is list and len(addresses) == 1 and type(addresses[0]) is str, 'ENDPOINT_ADDRESS_INVALID')
        try:
            parsed = ipaddress.ip_address(addresses[0])
            require(parsed.version == 4 and parsed.is_private and not parsed.is_loopback and not parsed.is_link_local
                    and not parsed.is_unspecified and not parsed.is_multicast, 'ENDPOINT_ADDRESS_INVALID')
        except ValueError:
            require(False, 'ENDPOINT_ADDRESS_INVALID')
        conditions = endpoint.get('conditions', {})
        require(type(conditions) is dict and set(conditions) <= {'ready', 'serving', 'terminating'}
                and all(value is None or type(value) is bool for value in conditions.values()), 'ENDPOINT_CONDITIONS_INVALID')
    return current
