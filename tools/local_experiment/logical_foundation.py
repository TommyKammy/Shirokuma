"""Private logical-v1 foundation settings and explicit operator preparation.

Importing this module performs no I/O. Foundation IDs are registered WCG IDs
supplied by core; this module neither registers generations nor creates a ledger.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import time

import yaml

ROOT = Path(__file__).resolve().parents[2]
LABEL = 'shirokuma.dev/logical-foundation'
REVISION = 'shirokuma.dev/foundation-revision'
DNS = re.compile(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z')
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
IMAGE = re.compile(r'[^\s@]+@sha256:[0-9a-f]{64}\Z')
SCHEMA_INIT_SCRIPT = """export PGCONNECT_TIMEOUT=2
attempt=1
while ! psql --no-password --no-psqlrc --set ON_ERROR_STOP=1 --command 'SELECT 1' >/dev/null 2>&1; do
    if [ "$attempt" -eq 15 ]; then
        echo 'Schema database readiness check failed' >&2
        exit 1
    fi
    attempt=$((attempt + 1))
    sleep 1
done
if ! psql --no-password --no-psqlrc --set ON_ERROR_STOP=1 --command 'CREATE SCHEMA IF NOT EXISTS polaris_schema;' >/dev/null 2>&1; then
    echo 'Schema initialization failed' >&2
    exit 1
fi
"""
KEYS = {'version', 'creation_mode', 'foundation_id', 'revision', 'environment_id',
        'warehouse_id', 'kube', 'prefix', 'images', 'storage', 'polaris', 'trino',
        'artifacts', 'private_dir'}
NESTED = {
    'kube': {'kubeconfig', 'context', 'namespace', 'node_name', 'api_server', 'ca_sha256', 'node_uids'},
    'images': {'seaweedfs', 'postgres', 'polaris', 'polaris_admin', 'trino', 'opa'},
    'storage': {'account_id', 'bucket', 'region', 'endpoint', 'sts_endpoint', 'admin_credentials_file', 'issuer_credentials_file'},
    'polaris': {'endpoint', 'realm', 'root_credentials_file', 'credential_duration_seconds', 'credential_cache_duration_seconds'},
    'trino': {'endpoint', 'ca_file', 'provisioner_credentials_file', 'publisher_pod', 'publisher_container', 'compute_selector', 'workers', 'include_coordinator'},
    'artifacts': {'jar'},
}


class ConfigError(ValueError):
    def __init__(self, code='INVALID_FOUNDATION_SETTINGS'):
        super().__init__(code)


def _require(ok):
    if not ok:
        raise ConfigError()


def private_read(path):
    """Read operator-only regular files; never echo their contents or OS errors."""
    try:
        p = Path(path)
        s = p.lstat()
        _require(p.is_absolute() and stat.S_ISREG(s.st_mode) and stat.S_IMODE(s.st_mode) == 0o600)
        return p.read_bytes()
    except (OSError, TypeError):
        raise ConfigError() from None


def validate(config):
    try:
        c = copy.deepcopy(dict(config))
        _require(set(c) == KEYS and type(c['version']) is int and c['version'] == 1)
        _require(c['creation_mode'] == 'logical_v1')
        if isinstance(c.get('trino'), dict):
            c['trino'].setdefault('workers', 0)
            c['trino'].setdefault('include_coordinator', True)
        if isinstance(c.get('polaris'), dict):
            c['polaris'].setdefault('credential_duration_seconds', 900)
            c['polaris'].setdefault('credential_cache_duration_seconds', 450)
        for key, fields in NESTED.items():
            _require(isinstance(c[key], dict) and set(c[key]) == fields)
        for key in ('foundation_id', 'environment_id', 'warehouse_id'):
            _require(isinstance(c[key], str) and bool(ID.fullmatch(c[key])))
        _require(type(c['revision']) is int and c['revision'] > 0)
        k = c['kube']
        for value in (c['prefix'], k['namespace']):
            _require(isinstance(value, str) and len(value) <= 40 and bool(DNS.fullmatch(value)))
        for value in (k['context'], k['node_name'], c['polaris']['realm'], c['storage']['region']):
            _require(isinstance(value, str) and bool(ID.fullmatch(value)))
        _require(re.fullmatch(r'https://[^\s/?#]+(?::[0-9]+)?', k['api_server']) is not None)
        _require(re.fullmatch('[0-9a-f]{64}', k['ca_sha256']) is not None)
        _require(isinstance(k['node_uids'], dict) and set(k['node_uids']) == {k['node_name']})
        _require(bool(ID.fullmatch(k['node_uids'][k['node_name']])))
        _require(all(isinstance(v, str) and IMAGE.fullmatch(v) for v in c['images'].values()))
        _require(re.fullmatch(r'(?:docker.io/)?chrislusf/seaweedfs(?::4\.51)?@sha256:[0-9a-f]{64}', c['images']['seaweedfs']) is not None)
        _require(c['storage']['account_id'] == '000000000000')
        _require(bool(DNS.fullmatch(c['storage']['bucket'])) and len(c['storage']['bucket']) >= 3)
        native = f"{k['namespace']}.svc.cluster.local"
        _require(c['storage']['endpoint'] == f"http://{c['prefix']}-seaweed.{native}:8333")
        _require(c['storage']['sts_endpoint'] == c['storage']['endpoint'])
        _require(c['polaris']['endpoint'] == f"http://{c['prefix']}-polaris.{native}:8181")
        duration = c['polaris']['credential_duration_seconds']
        cache = c['polaris']['credential_cache_duration_seconds']
        _require(type(duration) is int and 900 <= duration <= 43200)
        _require(type(cache) is int and 0 <= cache < duration)
        _require(c['trino']['endpoint'] == f"https://trino-local.{native}:8080")
        _require(type(c['trino']['workers']) is int and c['trino']['workers'] in (0, 1))
        _require(type(c['trino']['include_coordinator']) is bool and (c['trino']['include_coordinator'] or c['trino']['workers'] == 1))
        _require(c['trino']['publisher_pod'] == 'deployment/trino-local')
        _require(c['trino']['publisher_container'] == 'trino')
        _require(c['trino']['compute_selector'] == 'app.kubernetes.io/name=trino-local')
        paths = [c['private_dir'], k['kubeconfig'], c['artifacts']['jar']]
        for section in ('storage', 'polaris', 'trino'):
            paths += [v for key, v in c[section].items() if key.endswith('_file')]
        _require(all(isinstance(p, str) and Path(p).is_absolute() and '..' not in Path(p).parts for p in paths))
        return c
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ConfigError() from None


def load(path):
    try:
        return validate(json.loads(private_read(path)))
    except (ValueError, UnicodeError):
        raise ConfigError() from None


def kube_argv(config):
    c = validate(config)
    k = c['kube']
    return ['rtk', 'proxy', 'kubectl', '--kubeconfig', k['kubeconfig'], '--context', k['context'],
            '--namespace', k['namespace'], '--request-timeout=15s']


def save_private(path, data):
    p = Path(path)
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require(stat.S_IMODE(p.parent.stat().st_mode) == 0o700)
    raw = data.encode() if isinstance(data, str) else data
    fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)


def _run(argv, *, input=None):
    try:
        r = subprocess.run(argv, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240, check=False)
        if r.returncode:
            raise ConfigError('FOUNDATION_COMMAND_FAILED')
        return r.stdout
    except (OSError, subprocess.SubprocessError):
        raise ConfigError('FOUNDATION_COMMAND_FAILED') from None


def _pin(path, container):
    template = yaml.safe_load((ROOT / path).read_text())['spec']['template']['spec']
    matches = [item['image'] for item in template.get('containers', []) + template.get('initContainers', [])
               if item['name'] == container]
    _require(len(matches) == 1 and IMAGE.fullmatch(matches[0]) is not None)
    return matches[0]


def bootstrap_rules():
    """Initial INTERNAL-only seed, including catalog creation and system reads."""
    return {'catalogs': [{'user': 'logical_provisioner', 'allow': 'owner'}, {'allow': 'none'}],
        'system_information': [{'user': 'logical_provisioner', 'allow': ['read']}, {'allow': []}]}


def bootstrap_guard():
    return 'package shirokuma\nimport rego.v1\ndefault allow := false\nallow if { input.context.identity.user == "logical_provisioner" }\n'


def prepare(*, private_dir, kubeconfig, context, node_name, foundation_id, revision,
            environment_id, warehouse_id, seaweed_image, jar, namespace='shirokuma-logical',
            prefix='logical', volume_max=32, workers=0, exclude_coordinator=False, command=_run):
    """Operator only. Capture trust, mint material, write private deploy documents.

    Does not apply resources. Existing directories/material are never overwritten.
    Core must register foundation_id/revision before admitting logical databases.
    """
    _require(type(workers) is int and workers in (0, 1) and type(exclude_coordinator) is bool and (not exclude_coordinator or workers == 1))
    p = Path(private_dir).resolve()
    _require(not p.exists() and type(volume_max) is int and volume_max >= 7)
    base = ['rtk', 'proxy', 'kubectl', '--kubeconfig', str(Path(kubeconfig).resolve()), '--context', context]
    view = json.loads(command(base + ['config', 'view', '--minify', '--raw', '--flatten', '-o', 'json']))
    cluster = view['clusters'][0]['cluster']
    ca = base64.b64decode(cluster['certificate-authority-data'], validate=True)
    node = json.loads(command(base + ['get', 'node', node_name, '-o', 'json']))
    native = f'{namespace}.svc.cluster.local'
    c = validate({'version': 1, 'creation_mode': 'logical_v1', 'foundation_id': foundation_id,
        'revision': revision, 'environment_id': environment_id, 'warehouse_id': warehouse_id,
        'kube': {'kubeconfig': str(Path(kubeconfig).resolve()), 'context': context, 'namespace': namespace,
            'node_name': node_name, 'api_server': cluster['server'], 'ca_sha256': hashlib.sha256(ca).hexdigest(),
            'node_uids': {node_name: node['metadata']['uid']}}, 'prefix': prefix,
        'images': {'seaweedfs': seaweed_image,
            'postgres': _pin('deploy/gitops/catalog/database/statefulset.yaml', 'postgresql'),
            'polaris': _pin('deploy/gitops/catalog/server/deployment.yaml', 'polaris'),
            'polaris_admin': _pin('deploy/catalog/bootstrap/job.yaml', 'polaris-admin'),
            'trino': 'docker.io/trinodb/trino@sha256:aa18e61b2e7776ab8641ba8baaa8687d0430894e88c639e61010cc46a994ab36',
            'opa': _pin('deploy/local-experiment/trino/secure/deployment.yaml', 'opa')},
        'storage': {'account_id': '000000000000', 'bucket': 'logical-data', 'region': 'us-east-1',
            'endpoint': f'http://{prefix}-seaweed.{native}:8333', 'sts_endpoint': f'http://{prefix}-seaweed.{native}:8333',
            'admin_credentials_file': str(p / 'storage-admin.json'), 'issuer_credentials_file': str(p / 'storage-issuer.json')},
        'polaris': {'endpoint': f'http://{prefix}-polaris.{native}:8181', 'realm': 'LOGICAL', 'root_credentials_file': str(p / 'polaris-root.json')},
        'trino': {'endpoint': f'https://trino-local.{native}:8080', 'ca_file': str(p / 'trino-ca.pem'),
            'provisioner_credentials_file': str(p / 'provisioner.json'), 'publisher_pod': 'deployment/trino-local',
            'publisher_container': 'trino', 'compute_selector': 'app.kubernetes.io/name=trino-local',
            'workers': workers, 'include_coordinator': not exclude_coordinator},
        'artifacts': {'jar': str(Path(jar).resolve())}, 'private_dir': str(p)})
    p.mkdir(mode=0o700)
    admin = {'access_key': secrets.token_hex(16), 'secret_key': secrets.token_hex(32)}
    issuer = {'access_key': secrets.token_hex(16), 'secret_key': secrets.token_hex(32)}
    root = {'client_id': secrets.token_hex(16), 'client_secret': secrets.token_hex(32)}
    provisioner = {'version': 1, 'username': 'logical_provisioner', 'password': secrets.token_urlsafe(32)}
    for path, value in [(c['storage']['admin_credentials_file'], admin), (c['storage']['issuer_credentials_file'], issuer),
                        (c['polaris']['root_credentials_file'], root), (c['trino']['provisioner_credentials_file'], provisioner)]:
        save_private(path, json.dumps(value))
    save_private(p / 'cluster-ca.pem', ca)
    # OpenSSL only during explicit operator prepare; never at import or DB create.
    command(['rtk', 'proxy', 'openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-days', '365',
        '-subj', '/CN=logical-trino', '-addext', f'subjectAltName=DNS:trino-local.{native},DNS:localhost,IP:127.0.0.1',
        '-keyout', str(p / 'trino-key.pem'), '-out', str(p / 'trino-ca.pem')])
    command(['rtk', 'proxy', 'openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072', '-out', str(p / 'jwt-private.pem')])
    command(['rtk', 'proxy', 'openssl', 'pkey', '-in', str(p / 'jwt-private.pem'), '-pubout', '-out', str(p / 'jwt-public.pem')])
    for name in ('trino-key.pem', 'trino-ca.pem', 'jwt-private.pem', 'jwt-public.pem'):
        (p / name).chmod(0o600)
    # Trino 483's built-in file authenticator uses PBKDF2-HMAC-SHA1.
    salt = secrets.token_bytes(16)
    password_hash = f'100000:{salt.hex()}:{hashlib.pbkdf2_hmac("sha1", provisioner["password"].encode(), salt, 100000, 32).hex()}'
    bootstrap = {'credentials.properties': '', 'password.db': f'{provisioner["username"]}:{password_hash}\n',
        'rules.json': json.dumps(bootstrap_rules()), 'guard.rego': bootstrap_guard()}
    for name, value in bootstrap.items():
        save_private(p / name, value)
    save_private(p / 'foundation-private.json', json.dumps({'admin': admin, 'issuer': issuer, 'root': root,
        'pg_password': secrets.token_urlsafe(32), 'signer': base64.b64encode(secrets.token_bytes(48)).decode(), 'shared': secrets.token_urlsafe(48),
        'volume_max': volume_max, 'bootstrap': bootstrap}))
    save_private(p / 'settings.json', json.dumps(c, indent=2))
    public, private = render(c)
    save_private(p / 'foundation-public.yaml', yaml.safe_dump_all(public, sort_keys=False))
    save_private(p / 'foundation-secrets.yaml', yaml.safe_dump_all(private, sort_keys=False))
    return c


def render(config):
    """Return (public documents, private Secret documents); no cluster calls."""
    c = validate(config)
    p, prefix, ns = Path(c['private_dir']), c['prefix'], c['kube']['namespace']
    state = json.loads(private_read(p / 'foundation-private.json'))
    jar = Path(c['artifacts']['jar']).read_bytes()
    _require(0 < len(jar) <= 700000)
    labels = {LABEL: c['foundation_id'], REVISION: str(c['revision']), 'app.kubernetes.io/part-of': 'shirokuma'}
    def doc(kind, name, **body):
        return {'apiVersion': {'Deployment': 'apps/v1', 'Job': 'batch/v1', 'ValidatingAdmissionPolicy': 'admissionregistration.k8s.io/v1',
                'ValidatingAdmissionPolicyBinding': 'admissionregistration.k8s.io/v1',
                'NetworkPolicy': 'networking.k8s.io/v1'}.get(kind, 'v1'), 'kind': kind,
                'metadata': {'name': name, 'namespace': ns, 'labels': dict(labels)}, **body}
    def secret(name, data):
        return doc('Secret', name, type='Opaque', stringData=data)
    def env(name, value=None, secret_key=None):
        return {'name': name, **({'value': value} if secret_key is None else {'valueFrom': {'secretKeyRef': {'name': prefix+'-private', 'key': secret_key}}})}
    def mount(name, path, ro=True):
        return {'name': name, 'mountPath': path, 'readOnly': ro}
    def volume(name, kind, target):
        return {'name': name, kind: target}
    def container(name, image, args=None, mounts=(), envs=(), uid=10000):
        result = {'name': name, 'image': image, 'imagePullPolicy': 'IfNotPresent',
            'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                'capabilities': {'drop': ['ALL']}, 'runAsNonRoot': True, 'runAsUser': uid, 'runAsGroup': 10001},
            'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'}, 'limits': {'cpu': '4', 'memory': '4Gi'}},
            'volumeMounts': [mount('tmp', '/tmp', False), *mounts], 'env': list(envs)}
        if args is not None:
            result['args'] = args
        return result
    def workload(name, containers, volumes=(), init=(), job=False):
        pod = {'automountServiceAccountToken': False, 'enableServiceLinks': False,
            'nodeSelector': {'kubernetes.io/hostname': c['kube']['node_name']},
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 10000, 'runAsGroup': 10001,
                'fsGroup': 10001, 'fsGroupChangePolicy': 'OnRootMismatch', 'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': containers, 'volumes': [volume('tmp', 'emptyDir', {}), *volumes]}
        if init:
            pod['initContainers'] = list(init)
        podlabels = {**labels, 'app.kubernetes.io/name': name}
        if name in ('trino-local', 'trino-local-worker'):
            podlabels['shirokuma.dev/logical-compute'] = 'true'
        template = {'metadata': {'labels': podlabels}, 'spec': pod}
        if job:
            pod['restartPolicy'] = 'Never'
            return doc('Job', name, spec={'backoffLimit': 0, 'template': template})
        return doc('Deployment', name, spec={'replicas': 1, 'strategy': {'type': 'Recreate'},
            'selector': {'matchLabels': {'app.kubernetes.io/name': name}}, 'template': template})
    def pvc(name, size):
        claim = doc('PersistentVolumeClaim', name, spec={'storageClassName': 'local-path', 'volumeMode': 'Filesystem',
            'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': size}}})
        # Rancher local-path defaults to hostPath; choose its documented per-PVC
        # local volume type without changing the cluster-wide StorageClass.
        claim['metadata']['annotations'] = {'volumeType': 'local'}
        return claim
    def service(name, ports):
        return doc('Service', name, spec={'selector': {'app.kubernetes.io/name': name},
            'ports': [{'name': key, 'port': port, 'targetPort': port} for key, port in ports.items()]})
    namespace = doc('Namespace', ns)
    namespace['metadata'].pop('namespace')
    namespace['metadata']['labels'].update({'pod-security.kubernetes.io/enforce': 'restricted',
        'pod-security.kubernetes.io/enforce-version': 'latest', 'pod-security.kubernetes.io/audit': 'restricted',
        'pod-security.kubernetes.io/warn': 'restricted'})
    namespace['metadata']['labels']['shirokuma.dev/native-admission'] = 'logical-v1'
    public = [namespace, doc('NetworkPolicy', prefix+'-isolation', spec={'podSelector': {},
        'policyTypes': ['Ingress', 'Egress'], 'ingress': [{'from': [{'podSelector': {}}]}],
        'egress': [{'to': [{'podSelector': {}}]}, {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}}}],
            'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}]}]})]
    # Reuse reviewed CEL expressions, with new names and exact namespace scope;
    # do not broaden or replace the existing shirokuma-dev policies.
    baseline = json.loads((ROOT / 'deploy/local-experiment/policy/native-baseline.json').read_text())['items']
    policy_names = {item['metadata']['name']: ns+'-'+prefix+'-'+item['metadata']['name'].removeprefix('shirokuma-local-')
                    for item in baseline if item['kind'] == 'ValidatingAdmissionPolicy'}
    selector = {'matchLabels': {'kubernetes.io/metadata.name': ns,
                               'shirokuma.dev/native-admission': 'logical-v1'}}
    admission = []
    for template in baseline:
        spec = copy.deepcopy(template['spec'])
        if template['kind'] == 'ValidatingAdmissionPolicy':
            spec['matchConstraints']['namespaceSelector'] = copy.deepcopy(selector)
        else:
            spec['policyName'] = policy_names[spec['policyName']]
            spec['matchResources']['namespaceSelector'] = copy.deepcopy(selector)
        policy = doc(template['kind'], policy_names[template['metadata']['name']], spec=spec)
        policy['metadata'].pop('namespace')
        admission.append(policy)
    public[1:1] = admission
    pg, sw, pol = prefix+'-postgres', prefix+'-seaweed', prefix+'-polaris'
    # Secret-only IAM bootstrap, durable IAM store and signer; issuer has no object rights.
    s3config = json.dumps({'identities': [
        {'name': 'foundation-admin', 'account': {'id': c['storage']['account_id'], 'displayName': 'foundation'},
         'credentials': [{'accessKey': state['admin']['access_key'], 'secretKey': state['admin']['secret_key']}], 'actions': ['Admin']},
        {'name': 'polaris-issuer', 'account': {'id': c['storage']['account_id'], 'displayName': 'foundation'},
         'credentials': [{'accessKey': state['issuer']['access_key'], 'secretKey': state['issuer']['secret_key']}], 'actions': []}]})
    iamconfig = json.dumps({'sts': {'signingKey': state['signer'], 'issuer': c['foundation_id'],
        'accountId': c['storage']['account_id'], 'tokenDuration': '1h', 'maxSessionLength': '12h'},
        'policy': {'defaultEffect': 'Deny', 'storeType': 'filer'}, 'roleStore': {'storeType': 'filer'}})
    secret_data = {'s3.json': s3config, 'iam.json': iamconfig,
        'filer.toml': '[leveldb2]\nenabled = true\ndir = "/data/filer"\n',
        'pg_password': state['pg_password'], 'shared': state['shared'],
        'issuer_access': state['issuer']['access_key'], 'issuer_secret': state['issuer']['secret_key'],
        'credentials.json': json.dumps({c['polaris']['realm']: {'client-id': state['root']['client_id'], 'client-secret': state['root']['client_secret']}}),
        'private.pem': private_read(p / 'jwt-private.pem').decode(), 'public.pem': private_read(p / 'jwt-public.pem').decode(),
        'trino.pem': private_read(p / 'trino-key.pem').decode()+private_read(p / 'trino-ca.pem').decode()}
    # Prepared host files are immutable snapshots. Refresh only the generated
    # seed policy; preserve its original password and credential material.
    bootstrap = {**state['bootstrap'], 'rules.json': json.dumps(bootstrap_rules()), 'guard.rego': bootstrap_guard()}
    private = [secret(prefix+'-private', secret_data), secret(prefix+'-bootstrap', bootstrap)]
    def privatevol(*keys):
        return volume('private', 'secret', {'secretName': prefix+'-private', 'defaultMode': 0o440,
            'items': [{'key': key, 'path': key} for key in keys]})
    for name, size in ((sw+'-data', '40Gi'), (pg+'-data', '5Gi'), ('trino-runtime', '1Gi'), ('trino-catalogs', '1Gi')):
        public.append(pvc(name, size))
    public += [service(sw, {'s3': 8333, 'filer': 8888}), service(pg, {'postgres': 5432}), service(pol, {'http': 8181}),
        service('trino-local', {'https': 8080, 'internal': 8081}), service('trino-opa', {'http': 8181})]
    # The filer uses its standard /etc/seaweedfs/filer.toml search path. Both
    # metadata stores live on the data PVC; actual image launch remains a live gate.
    swc = container('seaweedfs', c['images']['seaweedfs'], ['server', '-dir=/data', '-master.dir=/data/master', '-filer', '-s3',
        '-ip=$(POD_IP)', '-ip.bind=0.0.0.0', '-master.volumeSizeLimitMB=1024', '-volume.max='+str(state['volume_max']),
        '-s3.port=8333', '-s3.config=/etc/seaweedfs/s3.json', '-s3.iam.readOnly=false', '-s3.iam.config=/etc/seaweedfs/iam.json'],
        [mount('data', '/data', False), mount('private', '/etc/seaweedfs')],
        envs=[{'name': 'POD_IP', 'valueFrom': {'fieldRef': {'fieldPath': 'status.podIP'}}}])
    swc['workingDir'] = '/data'
    swinit = container('seaweedfs-data-init', c['images']['seaweedfs'],
        ['-c', 'mkdir -p /data/master /data/filer'], [mount('data', '/data', False)])
    swinit['command'] = ['/bin/sh']
    public.append(workload(sw, [swc], [privatevol('s3.json', 'iam.json', 'filer.toml'),
        volume('data', 'persistentVolumeClaim', {'claimName': sw+'-data'})], init=[swinit]))
    pgc = container('postgres', c['images']['postgres'], mounts=[mount('data', '/var/lib/postgresql/data', False), mount('run', '/var/run/postgresql', False)],
        envs=[env('POSTGRES_DB', 'polaris'), env('POSTGRES_USER', 'polaris'), env('POSTGRES_PASSWORD', secret_key='pg_password'),
            env('PGPASSWORD', secret_key='pg_password'), env('PGDATA', '/var/lib/postgresql/data/pgdata')], uid=70)
    public.append(workload(pg, [pgc], [volume('data', 'persistentVolumeClaim', {'claimName': pg+'-data'}), volume('run', 'emptyDir', {})]))
    polenv = [env('QUARKUS_LOG_LEVEL', 'WARN'), env('QUARKUS_LOG_FILE_ENABLED', 'false'), env('POLARIS_PERSISTENCE_TYPE', 'relational-jdbc'),
        env('POLARIS_REALM_CONTEXT_REALMS', c['polaris']['realm']), env('QUARKUS_DATASOURCE_DB_KIND', 'postgresql'),
        env('QUARKUS_DATASOURCE_JDBC_URL', f'jdbc:postgresql://{pg}.{ns}.svc.cluster.local:5432/polaris?currentSchema=polaris_schema'),
        env('QUARKUS_DATASOURCE_USERNAME', 'polaris'), env('QUARKUS_DATASOURCE_PASSWORD', secret_key='pg_password')]
    # Match the tracked bootstrap template: create the PostgreSQL schema first;
    # the official Polaris bootstrap command creates its relational tables.
    schema = container('schema', c['images']['postgres'], [SCHEMA_INIT_SCRIPT], envs=[
            env('PGHOST', f'{pg}.{ns}.svc.cluster.local'), env('PGPORT', '5432'),
            env('PGDATABASE', 'polaris'), env('PGUSER', 'polaris'), env('PGPASSWORD', secret_key='pg_password')], uid=70)
    schema['command'] = ['/bin/sh', '-ec']
    boot = container('bootstrap', c['images']['polaris_admin'], ['bootstrap', '--credentials-file=/etc/foundation/credentials.json'], [mount('private', '/etc/foundation')], polenv)
    public.append(workload(prefix+'-bootstrap', [boot], [privatevol('credentials.json')], [schema], job=True))
    public.append(doc('ConfigMap', prefix+'-polaris-features', data={'application.properties':
        f'polaris.features."STORAGE_CREDENTIAL_DURATION_SECONDS"={c["polaris"]["credential_duration_seconds"]}\n'
        f'polaris.features."STORAGE_CREDENTIAL_CACHE_DURATION_SECONDS"={c["polaris"]["credential_cache_duration_seconds"]}\n'}))
    serverenv = polenv + [env('QUARKUS_CONFIG_LOCATIONS', 'file:/etc/polaris-foundation/application.properties'),
        env('POLARIS_AUTHENTICATION_TOKEN_BROKER_TYPE', 'rsa-key-pair'),
        env('POLARIS_AUTHENTICATION_TOKEN_BROKER_RSA_KEY_PAIR_PUBLIC_KEY_FILE', '/etc/foundation/public.pem'),
        env('POLARIS_AUTHENTICATION_TOKEN_BROKER_RSA_KEY_PAIR_PRIVATE_KEY_FILE', '/etc/foundation/private.pem'),
        env('AWS_ACCESS_KEY_ID', secret_key='issuer_access'), env('AWS_SECRET_ACCESS_KEY', secret_key='issuer_secret'), env('AWS_REGION', c['storage']['region'])]
    public.append(workload(pol, [container('polaris', c['images']['polaris'], mounts=[mount('private', '/etc/foundation'),
        mount('polaris-features', '/etc/polaris-foundation')], envs=serverenv)],
        [privatevol('public.pem', 'private.pem'), volume('polaris-features', 'configMap', {'name': prefix+'-polaris-features'})]))
    config = {'config.properties': '\n'.join(['coordinator=true', 'node-scheduler.include-coordinator='+str(c['trino']['include_coordinator']).lower(), 'discovery.uri=http://trino-local:8081',
        'catalog.management=dynamic', 'catalog.store=file',
        'http-server.http.enabled=true', 'http-server.http.port=8081', 'http-server.authentication.allow-insecure-over-http=false', 'http-server.https.enabled=true', 'http-server.https.port=8080',
        'http-server.https.keystore.path=/etc/foundation/trino.pem', 'http-server.authentication.type=PASSWORD',
        'internal-communication.shared-secret=${ENV:TRINO_INTERNAL_SHARED_SECRET}', 'query.max-memory=1GB', 'query.max-memory-per-node=1GB', 'memory.heap-headroom-per-node=512MB'])+'\n',
        'catalog-store.properties': 'catalog.config-dir=/var/trino-catalogs\n',
        'node.properties': 'node.environment=logical\nnode.data-dir=/tmp/trino\n', 'jvm.config': '-server\n-Xmx2G\n-XX:+UseG1GC\n-DsecretsConfig=/etc/trino/secrets.toml\n',
        'log.properties': 'io.trino=WARN\n', 'secrets.toml': 'secrets-plugins-dir="/usr/lib/trino/secrets-plugin"\n[env]\nsecrets-provider.name="env"\n[file]\nsecrets-provider.name="file"\n',
        'password-authenticator.properties': 'password-authenticator.name=file\nfile.password-file=/etc/trino-private-secrets/password.db\nfile.refresh-period=1s\nfile.auth-token-cache.max-size=0\n',
        'access-control.properties': 'access-control.name=file\nsecurity.config-file=/etc/trino-private-secrets/rules.json\nsecurity.refresh-period=1s\n',
        'opa-access-control.properties': 'access-control.name=opa\nopa.policy.uri=http://trino-opa:8181/v1/data/shirokuma/allow\nopa.log-requests=false\nopa.log-responses=false\n'}
    config['config.properties'] += 'access-control.config-files=/etc/trino/access-control.properties,/etc/trino/opa-access-control.properties\n'
    public += [doc('ConfigMap', prefix+'-trino-config', data=config), doc('ConfigMap', prefix+'-secrets-code',
        binaryData={'logical-secrets.jar': base64.b64encode(jar).decode()}, data={'sha256': hashlib.sha256(jar).hexdigest()})]
    runtime = volume('runtime', 'persistentVolumeClaim', {'claimName': 'trino-runtime'})
    codevol = volume('code', 'configMap', {'name': prefix+'-secrets-code'})
    bootstrapper = container('logical-bootstrap', c['images']['trino'], mounts=[mount('runtime', '/etc/trino-private-secrets', False),
        mount('code', '/opt/logical-secrets'), mount('bootstrap', '/etc/trino-bootstrap')])
    bootstrapper['command'] = ['java', '-cp', '/opt/logical-secrets/logical-secrets.jar', 'org.shirokuma.logical.secrets.FixedPublisher', 'bootstrap']
    trino = container('trino', c['images']['trino'], mounts=[mount('runtime', '/etc/trino-private-secrets', False),
        mount('catalogs', '/var/trino-catalogs', False), mount('code', '/opt/logical-secrets'),
        mount('code', '/usr/lib/trino/secrets-plugin/logical-file'), mount('config', '/etc/trino'), mount('private', '/etc/foundation')],
        envs=[env('TRINO_INTERNAL_SHARED_SECRET', secret_key='shared')])
    public.append(workload('trino-local', [trino], [runtime, codevol, privatevol('trino.pem'),
        volume('config', 'configMap', {'name': prefix+'-trino-config'}), volume('catalogs', 'persistentVolumeClaim', {'claimName': 'trino-catalogs'}),
        volume('bootstrap', 'secret', {'secretName': prefix+'-bootstrap', 'defaultMode': 0o440})], [bootstrapper]))
    worker_config = copy.deepcopy(config)
    del worker_config['catalog-store.properties']
    worker_config['config.properties'] = '\n'.join(line for line in config['config.properties'].replace(
        'coordinator=true', 'coordinator=false').splitlines() if not line.startswith((
            'catalog.store=', 'catalog.config-dir=', 'node-scheduler.include-coordinator=')))+'\n'
    public.append(doc('ConfigMap', prefix+'-trino-worker-config', data=worker_config))
    worker = copy.deepcopy(trino)
    worker['volumeMounts'] = [m for m in worker['volumeMounts'] if m['name'] != 'catalogs']
    next(m for m in worker['volumeMounts'] if m['name'] == 'runtime')['readOnly'] = True
    worker_deployment = workload('trino-local-worker', [worker], [runtime, codevol, privatevol('trino.pem'),
        volume('config', 'configMap', {'name': prefix+'-trino-worker-config'})])
    worker_deployment['spec']['replicas'] = c['trino']['workers']
    public.append(worker_deployment)
    opa = container('opa', c['images']['opa'], ['run', '--server', '--addr=0.0.0.0:8181', '--watch', '/etc/trino-private-secrets'],
        [mount('runtime', '/etc/trino-private-secrets')])
    public.append(workload('trino-opa', [opa], [runtime]))
    for d in public:
        if d['kind'] != 'Deployment':
            continue
        pod = d['spec']['template']['spec']
        for container_spec in pod['containers']:
            name = container_spec['name']
            checks = {'postgres': {'tcpSocket': {'port': 5432}}, 'seaweedfs': {'tcpSocket': {'port': 8333}},
                'polaris': {'httpGet': {'port': 8182, 'path': '/q/health/ready'}},
                'trino': {'httpGet': {'scheme': 'HTTPS', 'port': 8080, 'path': '/v1/info'}},
                'opa': {'httpGet': {'port': 8181, 'path': '/health'}}}
            check = checks[name]
            container_spec['readinessProbe'] = {**check, 'periodSeconds': 3, 'timeoutSeconds': 2}
            container_spec['startupProbe'] = {**check, 'periodSeconds': 3, 'timeoutSeconds': 2, 'failureThreshold': 60}
            if name == 'postgres':
                container_spec['readinessProbe'] = {'exec': {'command': [
                    'psql', '--host', '127.0.0.1', '--port', '5432', '--dbname', 'polaris', '--username', 'polaris',
                    '--no-password', '--no-psqlrc', '--quiet', '--tuples-only', '--set', 'ON_ERROR_STOP=1',
                    '--command', 'SELECT 1']}, 'periodSeconds': 3, 'timeoutSeconds': 2}
                # Rollout must observe sustained authenticated readiness across startup.
                d['spec']['minReadySeconds'] = 5
    return public, private


def _preserve_allocations(desired, existing):
    """Validate owned replace compatibility; retain only known API allocations."""
    spec, live = desired['spec'], existing['spec']
    if desired['kind'] == 'PersistentVolumeClaim':
        for field in ('selector', 'dataSource', 'dataSourceRef'):
            _require(not spec.get(field) and not live.get(field))
        _require(spec.get('storageClassName') == live.get('storageClassName'))
        _require(sorted(spec['accessModes']) == sorted(live['accessModes']))
        _require(spec.get('volumeMode', 'Filesystem') == live.get('volumeMode', 'Filesystem'))
        # Generated product sizes use fixed Kubernetes quantities (e.g. 10Gi).
        _require(spec['resources']['requests']['storage'] == live['resources']['requests']['storage'])
        if 'volumeName' in spec:
            _require(spec['volumeName'] == live.get('volumeName', ''))
        elif 'volumeName' in live:
            spec['volumeName'] = live['volumeName']
        if 'volumeMode' not in spec and 'volumeMode' in live:
            spec['volumeMode'] = live['volumeMode']
    elif desired['kind'] == 'Service':
        _require(spec.get('type', 'ClusterIP') == live.get('type', 'ClusterIP') == 'ClusterIP')
        _require(spec.get('clusterIP') != 'None' and live.get('clusterIP') != 'None')
        _require(not any(port.get('nodePort') for port in spec['ports'] + live['ports']))
        for field in ('clusterIP', 'clusterIPs', 'ipFamilies', 'ipFamilyPolicy'):
            if field in spec:
                _require(spec[field] == live.get(field))
            elif field in live:
                spec[field] = copy.deepcopy(live[field])
        for field, default in (('type', 'ClusterIP'), ('sessionAffinity', 'None'), ('internalTrafficPolicy', 'Cluster')):
            if field not in spec and field in live:
                _require(live[field] == default)
                spec[field] = live[field]


def apply(config, *, apply=False, command=_run):
    """Explicit operator effects, never called by Provider. Refuse foreign resources."""
    _require(apply is True)
    c = validate(config)
    public, private = render(c)
    base = kube_argv(c)
    # Require the captured API/CA/node pins even for explicit operator application.
    if __package__:
        from .logical_provider import Provider
    else:
        from logical_provider import Provider
    verifier = Provider(c, command=command)
    verifier._trust()
    existing = json.loads(command(base + ['get', 'namespace', c['kube']['namespace'], '--ignore-not-found', '-o', 'json']) or b'{}')
    if existing:
        _require(existing.get('metadata', {}).get('labels', {}).get(LABEL) == c['foundation_id'])
        _require(existing['metadata']['labels'].get(REVISION) == str(c['revision']))
        _require(all(existing['metadata']['labels'].get(key) == value for key, value in public[0]['metadata']['labels'].items()))
        verifier._incarnation['namespace'] = existing['metadata']['uid']
    for d in public[1:] + private:
        found = json.loads(command(base + ['get', d['kind'], d['metadata']['name'], '--ignore-not-found', '-o', 'json']) or b'{}') if existing else {}
        if found:
            _require(found.get('metadata', {}).get('labels', {}).get(LABEL) == c['foundation_id'])
            _require(found['metadata']['labels'].get(REVISION) == str(c['revision']))
            d['metadata']['resourceVersion'] = found['metadata']['resourceVersion']
            d['metadata']['uid'] = found['metadata']['uid']
            if d['kind'] in ('PersistentVolumeClaim', 'Service'):
                _preserve_allocations(d, found)
    # Foundation startup is explicit and staged. Never replay bootstrap on a
    # second operator apply; the Job's completion is its native durable record.
    documents = public[:1] + private + [d for d in public[1:] if d['kind'] not in ('Deployment', 'Job')]
    documents += [d for d in public if d['kind'] == 'Deployment' and d['metadata']['name'] in (c['prefix']+'-postgres', c['prefix']+'-seaweed')]
    documents += [d for d in public if d['kind'] == 'Job']
    documents += [d for d in public if d['kind'] == 'Deployment' and d['metadata']['name'] not in (c['prefix']+'-postgres', c['prefix']+'-seaweed')]
    for d in documents:
        verifier._trust()
        verb = 'replace' if 'resourceVersion' in d['metadata'] else 'create'
        if d['kind'] == 'Namespace' and existing:
            continue
        if not (d['kind'] == 'Job' and verb == 'replace'):
            command(base + [verb, '-f', '-'], input=yaml.safe_dump(d).encode())
        name = d['metadata']['name']
        if d['kind'] == 'Namespace':
            ns = json.loads(command(base + ['get', 'namespace', name, '-o', 'json']))
            verifier._incarnation['namespace'] = ns['metadata']['uid']
        if d['kind'] == 'Deployment':
            command(base + ['rollout', 'status', 'deployment/'+name, '--timeout=180s'])
        elif d['kind'] == 'Job':
            command(base + ['wait', '--for=condition=complete', 'job/'+name, '--timeout=180s'])
    # Bucket creation belongs exclusively to this operator phase, never normal
    # DB creation. A lost create response stops here; reruns inspect it first.
    with Provider(c, command=command) as provider:
        try:
            provider._storage('head_bucket', Bucket=c['storage']['bucket'])
        except Exception as error:
            if __package__:
                from .logical_provider import Rejected
            else:
                from logical_provider import Rejected
            if not isinstance(error, Rejected) or error.code != 'PROVIDER_STORAGE_HTTP_404':
                raise
            with provider.effects(lambda key, identity, call: call()):
                provider._storage('create_bucket', Bucket=c['storage']['bucket'])
        provider._storage('head_bucket', Bucket=c['storage']['bucket'])
        deadline = time.monotonic() + 60
        while True:
            provider._trust()
            if provider.probe()['ready']:
                break
            _require(time.monotonic() < deadline)
            time.sleep(0.5)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Explicit private logical foundation operator')
    sub = parser.add_subparsers(dest='action', required=True)
    prep = sub.add_parser('prepare')
    for name in ('private-dir', 'kubeconfig', 'context', 'node-name', 'foundation-id', 'environment-id', 'warehouse-id', 'seaweed-image', 'jar'):
        prep.add_argument('--'+name, required=True)
    prep.add_argument('--revision', type=int, required=True)
    prep.add_argument('--namespace', default='shirokuma-logical')
    prep.add_argument('--prefix', default='logical')
    prep.add_argument('--volume-max', type=int, default=32)
    prep.add_argument('--workers', type=int, choices=(0, 1), default=0)
    prep.add_argument('--exclude-coordinator', action='store_true')
    for action in ('render', 'apply'):
        subparser = sub.add_parser(action)
        subparser.add_argument('--settings', required=True)
        if action == 'apply':
            subparser.add_argument('--apply', action='store_true')
    args = vars(parser.parse_args(argv))
    action = args.pop('action')
    try:
        if action == 'prepare':
            prepare(**args)
        elif action == 'apply':
            apply(load(args['settings']), apply=args['apply'])
        else:
            c = load(args['settings'])
            public, private = render(c)
            # No private manifest on stdout, even on explicit render.
            save_private(Path(c['private_dir']) / 'render-public.yaml', yaml.safe_dump_all(public))
            save_private(Path(c['private_dir']) / 'render-secrets.yaml', yaml.safe_dump_all(private))
        print('FOUNDATION_DOCUMENTS_READY')
        return 0
    except Exception:
        print('FOUNDATION_OPERATION_FAILED')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
