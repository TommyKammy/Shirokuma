"""Owned, disposable four-container lab; saved-plan recovery is pin-independent."""
import base64
import copy
import re
import uuid

from .fixtures import DATABASE, DATABASE_USER, gateway_config, trino_config
from .support import DEPLOY, NAMESPACE, PORTS, canonical, digest, require, token

OWNER = 'shirokuma.dev/gateway-warehouse-run'
HASH = 'shirokuma.dev/content-sha256'
KINDS = {'NetworkPolicy': ('networking.k8s.io/v1', 'networkpolicies'),
         'ConfigMap': ('v1', 'configmaps'), 'Secret': ('v1', 'secrets'), 'Pod': ('v1', 'pods')}
SECRET_KEYS = {'postgres-user', 'postgres-password', 'postgres-db', 'gateway-config.yaml'}
TRINO_KEYS = ('config.properties', 'jvm.config', 'node.properties', 'log.properties', 'tpch.properties', 'readonly.properties')
PUBLIC_KEYS = {size + '.' + key for size in ('xs', 's') for key in TRINO_KEYS} | {'routing-rules.yaml'}
IMAGE_FAMILIES = {'gateway': 'trinodb/trino-gateway', 'postgres': 'cgr.dev/chainguard/postgres',
                  'trino': 'shirokuma.local/trino-opa'}
RESOURCES = {
    'xs': {'requests': {'cpu': '250m', 'memory': '512Mi'}, 'limits': {'cpu': '1', 'memory': '2Gi'}},
    's': {'requests': {'cpu': '500m', 'memory': '1Gi'}, 'limits': {'cpu': '2', 'memory': '4Gi'}},
    'gateway': {'requests': {'cpu': '250m', 'memory': '512Mi'}, 'limits': {'cpu': '1', 'memory': '2Gi'}},
    'postgres': {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'cpu': '500m', 'memory': '512Mi'}},
}
# The PostgreSQL image's initial temporary server listens only on its Unix socket.
# Waiting for TCP therefore waits until initdb has completed, without credentials.
GATEWAY_START = ('for i in {1..90}; do if (echo > /dev/tcp/127.0.0.1/5432) 2>/dev/null; then '
    'exec java -Xms128m -Xmx512m -XX:+ExitOnOutOfMemoryError -Djava.io.tmpdir=/tmp '
    '-jar /usr/lib/trino-gateway/gateway-ha-jar-with-dependencies.jar /etc/trino-gateway/config.yaml; '
    'fi; sleep 1; done; exit 1')


def name(run_token):
    return 'shirokuma-vw-' + token(run_token)


def labels(run_token):
    return {OWNER: token(run_token), 'app.kubernetes.io/name': 'shirokuma-vw-local'}


def identity(obj):
    return obj['kind'] + '/' + obj['metadata']['name']


def seal(obj, run_token):
    obj = copy.deepcopy(obj)
    obj['metadata'].update(namespace=NAMESPACE, labels=labels(run_token))
    obj['metadata']['annotations'] = {HASH: digest(obj)}
    return obj


def security(uid, gid):
    return {'runAsNonRoot': True, 'runAsUser': uid, 'runAsGroup': gid, 'allowPrivilegeEscalation': False,
            'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}


def volumes(run_token):
    prefix = name(run_token)
    result = []
    for size in ('xs', 's'):
        result.append({'name': size + '-config', 'configMap': {'name': prefix + '-config', 'defaultMode': 0o440,
            'items': [{'key': size + '.' + key, 'path': 'catalog/tpch.properties' if key == 'tpch.properties' else key}
                      for key in TRINO_KEYS]}})
        result += [{'name': size + '-tmp', 'emptyDir': {'sizeLimit': '256Mi'}},
                   {'name': size + '-data', 'emptyDir': {'sizeLimit': '512Mi'}}]
    result += [
        {'name': 'gateway-config', 'projected': {'defaultMode': 0o440, 'sources': [
            {'configMap': {'name': prefix + '-config', 'items': [{'key': 'routing-rules.yaml', 'path': 'routing-rules.yaml'}]}},
            {'secret': {'name': prefix + '-secret', 'items': [{'key': 'gateway-config.yaml', 'path': 'config.yaml'}]}}]}},
        {'name': 'gateway-tmp', 'emptyDir': {'sizeLimit': '256Mi'}},
        {'name': 'postgres-data', 'emptyDir': {'sizeLimit': '1Gi'}},
        {'name': 'postgres-run', 'emptyDir': {'sizeLimit': '16Mi'}},
        {'name': 'postgres-tmp', 'emptyDir': {'sizeLimit': '128Mi'}},
    ]
    return result


def pod_spec(images, run_token):
    containers = []
    for size in ('xs', 's'):
        containers.append({'name': size, 'image': images['trino'], 'imagePullPolicy': 'Never',
            'securityContext': security(10000, 1000), 'resources': RESOURCES[size],
            'ports': [{'name': size, 'containerPort': PORTS[size]}],
            'volumeMounts': [{'name': size + '-config', 'mountPath': '/etc/trino', 'readOnly': True},
                {'name': size + '-tmp', 'mountPath': '/tmp'}, {'name': size + '-data', 'mountPath': '/var/trino'}],
            'readinessProbe': {'httpGet': {'path': '/v1/info', 'port': PORTS[size]}, 'periodSeconds': 2},
            'startupProbe': {'httpGet': {'path': '/v1/info', 'port': PORTS[size]}, 'periodSeconds': 2, 'failureThreshold': 90}})
    containers.append({'name': 'postgres', 'image': images['postgres'], 'imagePullPolicy': 'Never',
        'securityContext': security(70, 70), 'resources': RESOURCES['postgres'],
        'args': ['-c', 'listen_addresses=127.0.0.1', '-c', 'max_connections=30', '-c', 'shared_buffers=32MB'],
        'env': [{'name': key, 'valueFrom': {'secretKeyRef': {'name': name(run_token) + '-secret', 'key': secret}}}
                for key, secret in [('POSTGRES_USER', 'postgres-user'), ('POSTGRES_PASSWORD', 'postgres-password'),
                                    ('POSTGRES_DB', 'postgres-db')]] +
               [{'name': 'PGDATA', 'value': '/var/lib/postgresql/data/pgdata'}],
        'volumeMounts': [{'name': 'postgres-data', 'mountPath': '/var/lib/postgresql/data'},
                        {'name': 'postgres-run', 'mountPath': '/var/run/postgresql'}, {'name': 'postgres-tmp', 'mountPath': '/tmp'}],
        'readinessProbe': {'exec': {'command': ['pg_isready', '-h', '127.0.0.1', '-U', 'vw_owner', '-d', 'vw_gateway']},
                           'periodSeconds': 2},
        'startupProbe': {'exec': {'command': ['pg_isready', '-h', '127.0.0.1', '-U', 'vw_owner', '-d', 'vw_gateway']},
                        'periodSeconds': 2, 'failureThreshold': 90}})
    containers.append({'name': 'gateway', 'image': images['gateway'], 'imagePullPolicy': 'Never',
        'securityContext': security(1000, 1000), 'resources': RESOURCES['gateway'],
        'command': ['/bin/bash', '-ec', GATEWAY_START],
        'ports': [{'name': 'gateway', 'containerPort': PORTS['gateway']}],
        'volumeMounts': [{'name': 'gateway-config', 'mountPath': '/etc/trino-gateway', 'readOnly': True},
                        {'name': 'gateway-tmp', 'mountPath': '/tmp'}],
        'readinessProbe': {'httpGet': {'path': '/trino-gateway/readyz', 'port': PORTS['gateway']}, 'periodSeconds': 2},
        'startupProbe': {'httpGet': {'path': '/trino-gateway/livez', 'port': PORTS['gateway']},
                        'periodSeconds': 2, 'failureThreshold': 120}})
    return {'automountServiceAccountToken': False, 'enableServiceLinks': False, 'restartPolicy': 'Never',
            'terminationGracePeriodSeconds': 30,
            'securityContext': {'runAsNonRoot': True, 'fsGroup': 70, 'fsGroupChangePolicy': 'OnRootMismatch',
                                'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': containers, 'volumes': volumes(run_token)}


def render(images, run_token, material):
    token(run_token)
    require(set(images) == set(IMAGE_FAMILIES), 'PLAN_IMAGES_INVALID')
    require(set(material['public_data']) == PUBLIC_KEYS and set(material['secret_data']) == SECRET_KEYS,
            'FIXTURE_KEYS_INVALID')
    # The renderer accepts only its host-generated fixed Trino config, never extra catalogs.
    for size in ('xs', 's'):
        require(all(material['public_data'][size + '.' + key] == value
                    for key, value in trino_config(run_token, size).items()), 'FIXTURE_CONFIG_INVALID')
    secret = material['secret_data']
    require(secret['postgres-user'] == DATABASE_USER and secret['postgres-db'] == DATABASE
            and secret['gateway-config.yaml'] == canonical(gateway_config(secret['postgres-password'])) + '\n'
            and material['public_data']['routing-rules.yaml'] == (DEPLOY / 'routing-rules.yaml').read_text(),
            'FIXTURE_CONFIG_INVALID')
    prefix = name(run_token)
    objects = [
        {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy', 'metadata': {'name': prefix},
         'spec': {'podSelector': {'matchLabels': {OWNER: run_token}}, 'policyTypes': ['Ingress', 'Egress'],
                  'ingress': [], 'egress': []}},
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': prefix + '-config'},
         'immutable': True, 'data': material['public_data']},
        {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': prefix + '-secret'}, 'immutable': True,
         'type': 'Opaque', 'data': {key: base64.b64encode(value.encode()).decode()
                                    for key, value in material['secret_data'].items()}},
        {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': prefix}, 'spec': pod_spec(images, run_token)},
    ]
    result = [seal(obj, run_token) for obj in objects]
    validate_plan({'run_token': run_token, 'images': images, 'objects': result})
    return result


def _quantity(value, units):
    require(type(value) is str, 'PLAN_RESOURCES_INVALID')
    match = re.fullmatch(r'([0-9]+)([A-Za-z]*)', value)
    require(match is not None and match.group(2) in units, 'PLAN_RESOURCES_INVALID')
    return int(match.group(1)) * units[match.group(2)]


def _recovery_pod_scope(spec, images, run_token):
    """Validate disposable boundaries without calling the current Pod renderer.

    Command/config bytes are checked against the saved content hash and live Pod,
    not today's startup script. This validator cannot introduce host/PVC/API access.
    """
    require(set(spec) == {'automountServiceAccountToken', 'enableServiceLinks', 'restartPolicy',
            'terminationGracePeriodSeconds', 'securityContext', 'containers', 'volumes'}
            and spec['automountServiceAccountToken'] is False and spec['enableServiceLinks'] is False
            and spec['restartPolicy'] == 'Never' and type(spec['terminationGracePeriodSeconds']) is int
            and 0 <= spec['terminationGracePeriodSeconds'] <= 60
            and spec['securityContext'] == {'runAsNonRoot': True, 'fsGroup': 70,
                'fsGroupChangePolicy': 'OnRootMismatch', 'seccompProfile': {'type': 'RuntimeDefault'}},
            'PLAN_POD_SCOPE_INVALID')
    components = ('xs', 's', 'postgres', 'gateway')
    containers = spec['containers']
    require(type(containers) is list and [item.get('name') for item in containers] == list(components),
            'PLAN_POD_SCOPE_INVALID')
    mount_targets = {size: {size + '-config': ('/etc/trino', True), size + '-tmp': ('/tmp', False),
                           size + '-data': ('/var/trino', False)} for size in ('xs', 's')}
    mount_targets.update(postgres={'postgres-data': ('/var/lib/postgresql/data', False),
        'postgres-run': ('/var/run/postgresql', False), 'postgres-tmp': ('/tmp', False)},
        gateway={'gateway-config': ('/etc/trino-gateway', True), 'gateway-tmp': ('/tmp', False)})
    for item in containers:
        component = item['name']
        required = {'name', 'image', 'imagePullPolicy', 'securityContext', 'resources', 'volumeMounts'}
        optional = {'readinessProbe', 'startupProbe', 'livenessProbe', 'ports'}
        optional |= {'args', 'env'} if component == 'postgres' else {'command'} if component == 'gateway' else set()
        require(required <= set(item) <= required | optional and item['image'] == images[
            'trino' if component in ('xs', 's') else component] and item['imagePullPolicy'] == 'Never',
            'PLAN_POD_SCOPE_INVALID')
        uid, gid = (70, 70) if component == 'postgres' else (1000, 1000) if component == 'gateway' else (10000, 1000)
        require(item['securityContext'] == {'runAsNonRoot': True, 'runAsUser': uid, 'runAsGroup': gid,
            'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
            'PLAN_POD_SCOPE_INVALID')
        resources = item['resources']
        require(set(resources) == {'requests', 'limits'} and all(set(r) == {'cpu', 'memory'} for r in resources.values()),
                'PLAN_RESOURCES_INVALID')
        for resource, units, ceiling in [('cpu', {'': 1000, 'm': 1}, 2000),
                ('memory', {'Mi': 1024**2, 'Gi': 1024**3}, 4 * 1024**3)]:
            low, high = (_quantity(resources[key][resource], units) for key in ('requests', 'limits'))
            require(0 < low <= high <= ceiling, 'PLAN_RESOURCES_INVALID')
        mounts = item['volumeMounts']
        require(type(mounts) is list and len(mounts) == len(mount_targets[component])
                and {m.get('name') for m in mounts} == set(mount_targets[component]), 'PLAN_MOUNTS_INVALID')
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
    expected_names = {'xs-config', 'xs-tmp', 'xs-data', 's-config', 's-tmp', 's-data', 'gateway-config',
                      'gateway-tmp', 'postgres-data', 'postgres-run', 'postgres-tmp'}
    volume_list = spec['volumes']
    require(type(volume_list) is list and len(volume_list) == len(expected_names)
            and {v.get('name') for v in volume_list} == expected_names, 'PLAN_VOLUMES_INVALID')
    for volume in volume_list:
        key = volume['name']
        if key in ('xs-config', 's-config'):
            size = key.split('-')[0]
            require(volume == {'name': key, 'configMap': {'name': name(run_token) + '-config', 'defaultMode': 0o440,
                'items': [{'key': size + '.' + field, 'path': 'catalog/tpch.properties' if field == 'tpch.properties' else field}
                          for field in TRINO_KEYS]}}, 'PLAN_VOLUMES_INVALID')
        elif key == 'gateway-config':
            require(volume == {'name': key, 'projected': {'defaultMode': 0o440, 'sources': [
                {'configMap': {'name': name(run_token) + '-config', 'items': [{'key': 'routing-rules.yaml', 'path': 'routing-rules.yaml'}]}},
                {'secret': {'name': name(run_token) + '-secret', 'items': [{'key': 'gateway-config.yaml', 'path': 'config.yaml'}]}}]}},
                'PLAN_VOLUMES_INVALID')
        else:
            require(set(volume) == {'name', 'emptyDir'} and set(volume['emptyDir']) == {'sizeLimit'}
                    and 0 < _quantity(volume['emptyDir']['sizeLimit'], {'Mi': 1024**2, 'Gi': 1024**3}) <= 4 * 1024**3,
                    'PLAN_VOLUMES_INVALID')


def validate_plan(plan):
    """Bound recovery by owned shapes, not today's mutable config files/image pins."""
    run_token, images = token(plan['run_token']), plan['images']
    require(set(images) == set(IMAGE_FAMILIES) and all(type(value) is str and re.fullmatch(
        re.escape(IMAGE_FAMILIES[key]) + r'@sha256:[0-9a-f]{64}', value) for key, value in images.items()),
        'PLAN_IMAGES_INVALID')
    objects, prefix = plan['objects'], name(run_token)
    require(type(objects) is list and [identity(obj) for obj in objects] == [
        'NetworkPolicy/' + prefix, 'ConfigMap/' + prefix + '-config', 'Secret/' + prefix + '-secret', 'Pod/' + prefix],
        'PLAN_OBJECTS_INVALID')
    for obj in objects:
        kind, meta = obj['kind'], obj['metadata']
        require(set(obj) == {'apiVersion', 'kind', 'metadata', *({'immutable', 'data', 'type'} if kind == 'Secret'
            else {'immutable', 'data'} if kind == 'ConfigMap' else {'spec'})} and obj['apiVersion'] == KINDS[kind][0]
            and set(meta) == {'name', 'namespace', 'labels', 'annotations'} and meta['namespace'] == NAMESPACE
            and meta['labels'] == labels(run_token) and set(meta['annotations']) == {HASH}, 'PLAN_SCOPE_INVALID')
        raw = copy.deepcopy(obj)
        raw['metadata'].pop('annotations')
        require(meta['annotations'][HASH] == digest(raw), 'PLAN_HASH_MISMATCH')
        if kind == 'NetworkPolicy':
            require(obj['spec'] == {'podSelector': {'matchLabels': {OWNER: run_token}}, 'policyTypes': ['Ingress', 'Egress'],
                                    'ingress': [], 'egress': []}, 'PLAN_SCOPE_INVALID')
        elif kind in ('Secret', 'ConfigMap'):
            require(obj['immutable'] is True and set(obj['data']) == (SECRET_KEYS if kind == 'Secret' else PUBLIC_KEYS)
                    and all(type(value) is str and 0 < len(value) <= 131072 for value in obj['data'].values()),
                    'PLAN_SCOPE_INVALID')
            if kind == 'Secret':
                require(obj['type'] == 'Opaque', 'PLAN_SCOPE_INVALID')
                for value in obj['data'].values():
                    try:
                        base64.b64decode(value, validate=True).decode()
                    except (ValueError, UnicodeError):
                        require(False, 'PLAN_SECRET_INVALID')
        else:
            _recovery_pod_scope(obj['spec'], images, run_token)
    return objects


def created_uid(current, expected):
    meta = current.get('metadata', {})
    require(current.get('apiVersion') == expected['apiVersion'] and current.get('kind') == expected['kind']
            and all(meta.get(key) == expected['metadata'][key] for key in ('name', 'namespace', 'labels'))
            and meta.get('annotations', {}).get(HASH) == expected['metadata']['annotations'][HASH],
            'OBJECT_IDENTITY_CONFLICT')
    uid = meta.get('uid')
    require(type(uid) is str and str(uuid.UUID(uid)) == uid, 'KNOWN_UID_REQUIRED')
    return uid


def assert_owned(current, expected, uid):
    require(created_uid(current, expected) == uid, 'OBJECT_IDENTITY_CONFLICT')
    if expected['kind'] in ('ConfigMap', 'Secret'):
        require(current.get('immutable') is True and current.get('data') == expected['data']
                and (expected['kind'] != 'Secret' or current.get('type') == 'Opaque'), 'OBJECT_CONTENT_CONFLICT')
    elif expected['kind'] == 'NetworkPolicy':
        spec = copy.deepcopy(current.get('spec', {}))
        spec.setdefault('ingress', [])
        spec.setdefault('egress', [])
        require(spec == expected['spec'], 'OBJECT_CONTENT_CONFLICT')
    else:
        spec, wanted = current.get('spec', {}), expected['spec']
        require(not any(spec.get(key) for key in ('hostNetwork', 'hostPID', 'hostIPC', 'initContainers',
                    'ephemeralContainers', 'hostAliases')) and all(spec.get(key) == wanted[key] for key in (
                    'automountServiceAccountToken', 'enableServiceLinks', 'restartPolicy',
                    'terminationGracePeriodSeconds', 'securityContext', 'volumes'))
                and len(spec.get('containers', [])) == 4, 'POD_SCOPE_CONFLICT')
        for actual, target in zip(spec['containers'], wanted['containers']):
            require(all(actual.get(key, []) == target.get(key, []) for key in ('name', 'image', 'imagePullPolicy',
                'securityContext', 'resources', 'volumeMounts', 'command', 'args', 'env'))
                and not any(actual.get(key) for key in ('envFrom', 'lifecycle')), 'POD_SCOPE_CONFLICT')
            ports = copy.deepcopy(actual.get('ports', []))
            for port in ports:
                if port.get('protocol') == 'TCP':
                    port.pop('protocol')
            require(ports == target.get('ports', []), 'POD_SCOPE_CONFLICT')
            for key in ('readinessProbe', 'startupProbe', 'livenessProbe'):
                probe, goal = copy.deepcopy(actual.get(key, {})), target.get(key, {})
                for field, default in {'initialDelaySeconds': 0, 'timeoutSeconds': 1, 'successThreshold': 1,
                                       'failureThreshold': 3}.items():
                    if field not in goal and probe.get(field) == default:
                        probe.pop(field)
                if 'httpGet' in probe:
                    if probe['httpGet'].get('scheme') == 'HTTP':
                        probe['httpGet'].pop('scheme')
                require(probe == goal, 'POD_SCOPE_CONFLICT')
