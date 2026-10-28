"""Source-only foundation tests. No cluster, API, container or Java execution."""
import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch, MagicMock

import yaml

from tools.local_experiment import logical_foundation as f


def settings(root):
    root = Path(root).resolve()
    image = lambda name: 'docker.io/'+name+'@sha256:'+'a'*64
    return {'version': 1, 'creation_mode': 'logical_v1', 'foundation_id': 'wcg-owned', 'revision': 3,
        'environment_id': 'lab', 'warehouse_id': 'warehouse-1', 'prefix': 'owned', 'private_dir': str(root),
        'kube': {'kubeconfig': str(root/'kubeconfig'), 'context': 'lab-context', 'namespace': 'safe-logical',
            'node_name': 'lab-node', 'api_server': 'https://127.0.0.1:6443',
            'ca_sha256': hashlib.sha256(b'cluster-ca').hexdigest(), 'node_uids': {'lab-node': 'node-uid'}},
        'images': {k: image('chrislusf/seaweedfs' if k == 'seaweedfs' else k) for k in ('seaweedfs', 'postgres', 'polaris', 'polaris_admin', 'trino', 'opa')},
        'storage': {'account_id': '000000000000', 'bucket': 'logical-data', 'region': 'us-east-1',
            'endpoint': 'http://owned-seaweed.safe-logical.svc.cluster.local:8333',
            'sts_endpoint': 'http://owned-seaweed.safe-logical.svc.cluster.local:8333',
            'admin_credentials_file': str(root/'admin.json'), 'issuer_credentials_file': str(root/'issuer.json')},
        'polaris': {'endpoint': 'http://owned-polaris.safe-logical.svc.cluster.local:8181', 'realm': 'LOGICAL', 'root_credentials_file': str(root/'root.json')},
        'trino': {'endpoint': 'https://trino-local.safe-logical.svc.cluster.local:8080', 'ca_file': str(root/'ca.pem'),
            'provisioner_credentials_file': str(root/'provisioner.json'), 'publisher_pod': 'deployment/trino-local',
            'publisher_container': 'trino', 'compute_selector': 'app.kubernetes.io/name=trino-local',
            'workers': 0, 'include_coordinator': True},
        'artifacts': {'jar': str(root/'logical-secrets.jar')}}


def isolated_import(test, subject, mode):
    """Import real siblings with only explicit source/dependency paths available."""
    repo = Path(__file__).resolve().parents[1]
    script = r'''
import ast
import importlib
import json
from pathlib import Path
import sys

repo, dependency, subject, mode, config = sys.argv[1:]
repo = Path(repo)
source = repo / 'tools' / 'local_experiment'
sys.dont_write_bytecode = True
sys.path[:] = [str(repo if mode == 'package' else source), dependency] + sys.path
prefix = 'tools.local_experiment.' if mode == 'package' else ''
allowed = (source, Path(dependency), Path(sys.base_prefix))

class BlockSDK:
    def find_spec(self, fullname, path=None, target=None):
        assert fullname.split('.')[0] not in ('boto3', 'botocore', 'kubernetes'), fullname

sys.meta_path.insert(0, BlockSDK())

def guard(event, args):
    if event.startswith(('subprocess.', 'socket.')) or event in ('os.system', 'os.posix_spawn'):
        raise AssertionError('unexpected effect: ' + event)
    if event == 'open':
        path = Path(args[0]).resolve()
        assert path.suffix in ('.py', '.pyc') and any(path.is_relative_to(root) for root in allowed), path

sys.addaudithook(guard)
module = importlib.import_module(prefix + subject)
assert module.__name__ == prefix + subject
if subject == 'logical_foundation':
    assert prefix + 'logical_provider' not in sys.modules, 'provider import must stay lazy'
foundation = importlib.import_module(prefix + 'logical_foundation')
provider = importlib.import_module(prefix + 'logical_provider')
assert provider.foundation is foundation
assert foundation.__name__ == prefix + 'logical_foundation'
assert provider.Provider.__module__ == prefix + 'logical_provider'
assert provider.Rejected.__module__ == prefix + 'logical_provider'
instance = provider.Provider(json.loads(config))
assert instance._opened is False
assert instance._children == []

# Execute only the actual lazy import branches, never apply or its API boundary.
tree = ast.parse(Path(foundation.__file__).read_text())
apply = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'apply')
branches = [node for node in ast.walk(apply) if isinstance(node, ast.If)
            and isinstance(node.test, ast.Name) and node.test.id == '__package__']
assert len(branches) == 2
namespace = {'__package__': foundation.__package__}
for branch in branches:
    assert all(isinstance(node, ast.ImportFrom) for node in branch.body + branch.orelse)
    exec(compile(ast.Module(body=[branch], type_ignores=[]), foundation.__file__, 'exec'), namespace)
assert namespace['Provider'] is provider.Provider
assert namespace['Rejected'] is provider.Rejected
other = '' if prefix else 'tools.local_experiment.'
assert other + 'logical_provider' not in sys.modules
assert other + 'logical_foundation' not in sys.modules
print(subject + ': ' + mode + ' import, construction, lazy identities OK')
'''
    with tempfile.TemporaryDirectory() as root:
        env = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
        result = subprocess.run([sys.executable, '-I', '-S', '-c', script,
            str(repo), str(Path(yaml.__file__).resolve().parent.parent), subject, mode,
            json.dumps(settings(root))], cwd=root, env=env, capture_output=True, text=True, timeout=20)
    test.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    test.assertIn(subject + ': ' + mode + ' import, construction, lazy identities OK', result.stdout)


class FoundationImportTests(unittest.TestCase):
    def test_isolated_direct_import(self):
        isolated_import(self, 'logical_foundation', 'direct')

    def test_isolated_package_import(self):
        isolated_import(self, 'logical_foundation', 'package')


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.c = settings(self.root)

    def save(self, name, value):
        f.save_private(self.root/name, value if isinstance(value, (str, bytes)) else json.dumps(value))

    def fixture(self):
        self.save('foundation-private.json', {'admin': {'access_key': 'PRIVATE_ADMIN', 'secret_key': 'PRIVATE_ADMIN_SECRET'},
            'issuer': {'access_key': 'PRIVATE_ISSUER', 'secret_key': 'PRIVATE_ISSUER_SECRET'},
            'root': {'client_id': 'PRIVATE_ROOT', 'client_secret': 'PRIVATE_ROOT_SECRET'},
            'pg_password': 'PRIVATE_PG', 'signer': 'PRIVATE_SIGNER', 'shared': 'PRIVATE_SHARED', 'volume_max': 32,
            'bootstrap': {'credentials.properties': '', 'password.db': 'PRIVATE_PASSWORD', 'rules.json': '{}', 'guard.rego': 'package shirokuma'}})
        for name in ('jwt-private.pem', 'jwt-public.pem', 'trino-key.pem', 'trino-ca.pem'):
            self.save(name, 'PRIVATE_'+name)
        self.save('logical-secrets.jar', b'tracked-code-only-jar')

    def test_exact_shape_version_mode_and_copy(self):
        validated = f.validate(self.c)
        self.c['kube']['namespace'] = 'changed'
        self.assertEqual(validated['kube']['namespace'], 'safe-logical')
        for mutate in (
            lambda c: c.update(version=True), lambda c: c.update(version=2),
            lambda c: c.update(creation_mode='legacy'), lambda c: c.update(extra='private'),
            lambda c: c['storage'].update(extra='arbitrary'), lambda c: c.update(revision=0),
            lambda c: c.update(foundation_id=''), lambda c: c['kube'].update(namespace='bad.name'),
            lambda c: c['kube'].update(node_uids={}), lambda c: c['storage'].update(endpoint='http://localhost:8333'),
            lambda c: c['trino'].update(publisher_pod='pod/attacker'), lambda c: c['trino'].update(publisher_container='shell'),
            lambda c: c['trino'].update(compute_selector='anything'), lambda c: c['artifacts'].update(jar='relative.jar'),
            lambda c: c['images'].update(seaweedfs='private/spike@sha256:'+'b'*64)):
            c = settings(self.root)
            mutate(c)
            with self.subTest(config=c), self.assertRaises(f.ConfigError):
                f.validate(c)

    def test_supported_profile_rejects_other_accounts_without_rewriting(self):
        for account in ('123456789012', '111111111111', '', 0, None):
            c = settings(self.root)
            c['storage']['account_id'] = account
            with self.subTest(account=account), self.assertRaises(f.ConfigError):
                f.validate(c)
            self.assertEqual(c['storage']['account_id'], account)

    def test_private_load_permissions_absolute_and_fixed_kube(self):
        self.save('settings.json', self.c)
        self.assertEqual(f.load(self.root/'settings.json'), f.validate(self.c))
        (self.root/'settings.json').chmod(0o644)
        with self.assertRaises(f.ConfigError):
            f.load(self.root/'settings.json')
        argv = f.kube_argv(self.c)
        self.assertEqual(argv[:3], ['rtk', 'proxy', 'kubectl'])
        self.assertIn('safe-logical', argv)
        self.assertNotIn('--server', argv)

    def test_render_separates_private_material_and_uses_native_safe_storage(self):
        self.fixture()
        public, private = f.render(self.c)
        text, secrets = json.dumps(public), json.dumps(private)
        for value in ('PRIVATE_ADMIN', 'PRIVATE_ISSUER', 'PRIVATE_ROOT', 'PRIVATE_PG', 'PRIVATE_SIGNER', 'PRIVATE_SHARED', 'PRIVATE_PASSWORD', 'PRIVATE_jwt', 'PRIVATE_trino'):
            self.assertNotIn(value, text)
        self.assertIn('PRIVATE_', secrets)
        self.assertNotIn('"hostPath":', text)
        self.assertNotIn('"subPath":', text)
        self.assertNotIn('"nodeName":', text)
        self.assertNotIn('AWS_SESSION_TOKEN', text)
        self.assertNotIn('s3.aws-access-key', text)
        self.assertEqual(public[0]['metadata']['labels']['pod-security.kubernetes.io/enforce'], 'restricted')
        admission = [d for d in public if d['kind'].startswith('ValidatingAdmissionPolicy')]
        self.assertEqual(len(admission), 16)
        for policy in admission:
            self.assertNotIn('namespace', policy['metadata'])
            constraints = policy['spec'].get('matchConstraints', policy['spec'].get('matchResources'))
            self.assertEqual(constraints['namespaceSelector']['matchLabels']['kubernetes.io/metadata.name'], 'safe-logical')
        deployments = {d['metadata']['name']: d for d in public if d['kind'] == 'Deployment'}
        for d in deployments.values():
            pod = d['spec']['template']['spec']
            self.assertFalse(pod['automountServiceAccountToken'])
            self.assertEqual(pod['nodeSelector'], {'kubernetes.io/hostname': 'lab-node'})
            self.assertEqual(pod['securityContext']['seccompProfile']['type'], 'RuntimeDefault')
            for container in pod['containers'] + pod.get('initContainers', []):
                self.assertIn('@sha256:', container['image'])
                self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
                self.assertEqual(container['securityContext']['capabilities']['drop'], ['ALL'])
                self.assertIn('resources', container)
        trino = deployments['trino-local']['spec']['template']['spec']
        self.assertEqual(trino['securityContext']['runAsUser'], 10000)
        self.assertEqual(trino['securityContext']['fsGroup'], 10001)
        init = trino['initContainers'][0]
        self.assertEqual(init['command'][-1], 'bootstrap')
        self.assertTrue(any(v['mountPath'] == '/etc/trino-private-secrets' and not v['readOnly'] for v in init['volumeMounts']))
        opa = deployments['trino-opa']['spec']['template']['spec']['containers'][0]
        self.assertIn('--watch', opa['args'])
        self.assertEqual(opa['args'][-1], '/etc/trino-private-secrets')
        self.assertTrue(next(v for v in opa['volumeMounts'] if v['name'] == 'runtime')['readOnly'])
        config = next(d['data'] for d in public if d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'owned-trino-config')
        self.assertIn('catalog.management=dynamic', config['config.properties'])
        self.assertIn('catalog.store=file', config['config.properties'])
        self.assertIn('file.refresh-period=1s\n', config['password-authenticator.properties'])
        self.assertIn('file.auth-token-cache.max-size=0', config['password-authenticator.properties'])
        self.assertIn('security.refresh-period=1s\n', config['access-control.properties'])
        self.assertIn('opa.log-requests=false\nopa.log-responses=false', config['opa-access-control.properties'])
        self.assertEqual(len(config['config.properties'].split('access-control.config-files=')[1].strip().split(',')), 2)
        self.assertIn('[env]', config['secrets.toml'])
        code = next(d for d in public if d['metadata']['name'] == 'owned-secrets-code')
        self.assertEqual(code['data']['sha256'], hashlib.sha256(b'tracked-code-only-jar').hexdigest())
        self.assertLess(len(json.dumps(code)), 1024*1024)
        self.assertEqual(next(d for d in public if d['kind'] == 'Service' and d['metadata']['name'] == 'trino-local')['spec']['ports'][0]['port'], 8080)
        sw = deployments['owned-seaweed']['spec']['template']['spec']['containers'][0]
        self.assertIn('-volume.max=32', sw['args'])
        self.assertIn('-ip=$(POD_IP)', sw['args'])
        self.assertEqual(sw['env'], [{'name': 'POD_IP', 'valueFrom': {'fieldRef': {'fieldPath': 'status.podIP'}}}])
        for pvc in (d for d in public if d['kind'] == 'PersistentVolumeClaim'):
            self.assertEqual(pvc['metadata']['annotations'], {'volumeType': 'local'})
            self.assertEqual(pvc['spec']['storageClassName'], 'local-path')
            self.assertEqual(pvc['spec']['volumeMode'], 'Filesystem')
            self.assertEqual(pvc['spec']['accessModes'], ['ReadWriteOnce'])
        self.assertIn('-s3.iam.readOnly=false', sw['args'])
        self.assertTrue(any('iam.config' in a for a in sw['args']))

    def test_catalog_store_properties_are_coordinator_only(self):
        self.fixture()
        public, _ = f.render(self.c)
        configs = {d['metadata']['name']: d['data'] for d in public if d['kind'] == 'ConfigMap'}
        coordinator_config = configs['owned-trino-config']
        self.assertIn('catalog.management=dynamic\n', coordinator_config['config.properties'])
        self.assertIn('catalog.store=file\n', coordinator_config['config.properties'])
        self.assertNotIn('catalog.config-dir=', coordinator_config['config.properties'])
        self.assertEqual(coordinator_config['catalog-store.properties'], 'catalog.config-dir=/var/trino-catalogs\n')
        self.assertNotIn('catalog-store.name', ''.join(coordinator_config.values()))
        worker_config = configs['owned-trino-worker-config']
        self.assertIn('catalog.management=dynamic\n', worker_config['config.properties'])
        self.assertNotIn('catalog.store=', worker_config['config.properties'])
        self.assertNotIn('catalog.config-dir=', worker_config['config.properties'])
        self.assertNotIn('catalog-store.properties', worker_config)
        pods = {d['metadata']['name']: d['spec']['template']['spec'] for d in public if d['kind'] == 'Deployment'}
        coordinator = pods['trino-local']
        catalog_mount = next(m for m in coordinator['containers'][0]['volumeMounts'] if m['name'] == 'catalogs')
        self.assertEqual(catalog_mount['mountPath'], '/var/trino-catalogs')
        self.assertFalse(catalog_mount['readOnly'])
        catalog_volume = next(v for v in coordinator['volumes'] if v['name'] == 'catalogs')
        self.assertEqual(catalog_volume['persistentVolumeClaim']['claimName'], 'trino-catalogs')
        worker = pods['trino-local-worker']
        self.assertFalse(any(m['name'] == 'catalogs' for m in worker['containers'][0]['volumeMounts']))
        self.assertFalse(any(v.get('persistentVolumeClaim', {}).get('claimName') == 'trino-catalogs' for v in worker['volumes']))

    def test_seaweed_init_creates_fixed_data_directories_before_main(self):
        self.fixture()
        public, private = f.render(self.c)
        self.assertEqual((public, private), f.render(self.c))
        deployment = next(d for d in public if d['kind'] == 'Deployment' and d['metadata']['name'] == 'owned-seaweed')
        pod = deployment['spec']['template']['spec']
        self.assertEqual(len(pod['initContainers']), 1)
        self.assertEqual(len(pod['containers']), 1)
        init, main = pod['initContainers'][0], pod['containers'][0]
        self.assertEqual(init['image'], self.c['images']['seaweedfs'])
        self.assertEqual(init['image'], main['image'])
        self.assertEqual(init['imagePullPolicy'], main['imagePullPolicy'])
        self.assertEqual(init['command'], ['/bin/sh'])
        self.assertEqual(init['args'], ['-c', 'mkdir -p /data/master /data/filer'])
        self.assertEqual(init['securityContext'], {
            'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
            'capabilities': {'drop': ['ALL']}, 'runAsNonRoot': True,
            'runAsUser': 10000, 'runAsGroup': 10001})
        self.assertEqual(init['securityContext'], main['securityContext'])
        self.assertEqual(init['resources'], main['resources'])
        self.assertEqual(pod['securityContext']['fsGroup'], 10001)
        data_mount = {'name': 'data', 'mountPath': '/data', 'readOnly': False}
        self.assertIn(data_mount, main['volumeMounts'])
        self.assertEqual(init['volumeMounts'], [
            {'name': 'tmp', 'mountPath': '/tmp', 'readOnly': False}, data_mount])
        self.assertEqual(next(v for v in pod['volumes'] if v['name'] == 'data'), {
            'name': 'data', 'persistentVolumeClaim': {'claimName': 'owned-seaweed-data'}})
        self.assertEqual(init['env'], [])
        self.assertNotIn('chmod', json.dumps(init))
        self.assertNotIn('chown', json.dumps(init))

    def test_polaris_feature_settings_normalize_validate_and_copy(self):
        c = f.validate(self.c)
        self.assertEqual(c['polaris']['credential_duration_seconds'], 900)
        self.assertEqual(c['polaris']['credential_cache_duration_seconds'], 450)
        self.assertNotIn('credential_duration_seconds', self.c['polaris'])
        for duration, cache in ((899, 450), (43201, 450), (True, 0), (900.0, 450),
                                ('900', 450), (None, 450), (900, -1), (900, 900),
                                (900, 901), (900, False), (900, 450.0), (900, '450'), (900, None)):
            with self.subTest(duration=duration, cache=cache):
                invalid = copy.deepcopy(self.c)
                invalid['polaris'].update(credential_duration_seconds=duration, credential_cache_duration_seconds=cache)
                with self.assertRaises(f.ConfigError):
                    f.validate(invalid)
        for duration, cache in ((900, 0), (43200, 43199)):
            valid = copy.deepcopy(self.c)
            valid['polaris'].update(credential_duration_seconds=duration, credential_cache_duration_seconds=cache)
            self.assertEqual(f.validate(valid), valid)

    def test_polaris_features_are_exact_and_only_server_receives_config(self):
        self.fixture()
        public, _ = f.render(self.c)
        cm = next(d for d in public if d['metadata']['name'] == 'owned-polaris-features')
        self.assertEqual(cm['kind'], 'ConfigMap')
        self.assertEqual(cm['metadata']['labels'][f.LABEL], self.c['foundation_id'])
        self.assertEqual(cm['data'], {'application.properties':
            'polaris.features."STORAGE_CREDENTIAL_DURATION_SECONDS"=900\n'
            'polaris.features."STORAGE_CREDENTIAL_CACHE_DURATION_SECONDS"=450\n'})
        receivers = []
        for d in public:
            if d['kind'] not in ('Deployment', 'Job'):
                continue
            pod = d['spec']['template']['spec']
            volumes = {v['name']: v for v in pod['volumes']}
            for container in pod['containers'] + pod.get('initContainers', []):
                locations = [e for e in container['env'] if e['name'] == 'QUARKUS_CONFIG_LOCATIONS']
                mounts = [m for m in container['volumeMounts']
                    if volumes[m['name']].get('configMap', {}).get('name') == cm['metadata']['name']]
                if locations or mounts:
                    receivers.append((d['metadata']['name'], container['name']))
                    self.assertEqual(locations, [{'name': 'QUARKUS_CONFIG_LOCATIONS',
                        'value': 'file:/etc/polaris-foundation/application.properties'}])
                    self.assertEqual(mounts, [{'name': 'polaris-features',
                        'mountPath': '/etc/polaris-foundation', 'readOnly': True}])
                    self.assertEqual(volumes['polaris-features'], {'name': 'polaris-features',
                        'configMap': {'name': 'owned-polaris-features'}})
        self.assertEqual(receivers, [('owned-polaris', 'polaris')])
        custom = f.validate(self.c)
        custom['polaris'].update(credential_duration_seconds=1200, credential_cache_duration_seconds=0)
        custom_public, _ = f.render(custom)
        self.assertEqual(next(d['data'] for d in custom_public if d['metadata']['name'] == cm['metadata']['name']),
            {'application.properties': 'polaris.features."STORAGE_CREDENTIAL_DURATION_SECONDS"=1200\n'
             'polaris.features."STORAGE_CREDENTIAL_CACHE_DURATION_SECONDS"=0\n'})

    def test_stale_prepared_seed_renders_current_policy_without_rewriting_private_files(self):
        self.fixture()
        old_rules = json.dumps({'catalogs': [{'user': 'logical_provisioner', 'allow': 'all'}, {'allow': 'none'}]})
        state_path = self.root/'foundation-private.json'
        state = json.loads(state_path.read_text())
        state['bootstrap']['rules.json'] = old_rules
        state_path.write_text(json.dumps(state))
        self.save('rules.json', old_rules)
        self.save('foundation-secrets.yaml', 'OLD_SEED_SNAPSHOT')
        snapshots = {p: p.read_bytes() for p in (state_path, self.root/'rules.json', self.root/'foundation-secrets.yaml')}
        _, private = f.render(self.c)
        seed = next(d['stringData'] for d in private if d['metadata']['name'] == 'owned-bootstrap')
        self.assertEqual(json.loads(seed['rules.json']), {
            'catalogs': [{'user': 'logical_provisioner', 'allow': 'owner'}, {'allow': 'none'}],
            'system_information': [{'user': 'logical_provisioner', 'allow': ['read']}, {'allow': []}]})
        self.assertEqual(seed['password.db'], state['bootstrap']['password.db'])
        self.assertEqual(seed['credentials.properties'], state['bootstrap']['credentials.properties'])
        self.assertEqual(seed['guard.rego'], 'package shirokuma\nimport rego.v1\ndefault allow := false\n'
            'allow if { input.context.identity.user == "logical_provisioner" }\n')
        for path, original in snapshots.items():
            self.assertEqual(path.read_bytes(), original)

    def test_postgres_authenticated_readiness_and_continuous_rollout(self):
        self.fixture()
        public, _ = f.render(self.c)
        pg = next(d for d in public if d['kind'] == 'Deployment' and d['metadata']['name'] == 'owned-postgres')
        container = pg['spec']['template']['spec']['containers'][0]
        self.assertEqual(pg['spec']['minReadySeconds'], 5)
        self.assertEqual(container['startupProbe'], {'tcpSocket': {'port': 5432},
            'periodSeconds': 3, 'timeoutSeconds': 2, 'failureThreshold': 60})
        self.assertEqual(container['readinessProbe'], {'exec': {'command': [
            'psql', '--host', '127.0.0.1', '--port', '5432', '--dbname', 'polaris', '--username', 'polaris',
            '--no-password', '--no-psqlrc', '--quiet', '--tuples-only', '--set', 'ON_ERROR_STOP=1',
            '--command', 'SELECT 1']}, 'periodSeconds': 3, 'timeoutSeconds': 2})
        env = {e['name']: e for e in container['env']}
        self.assertEqual(env['PGPASSWORD']['valueFrom'], env['POSTGRES_PASSWORD']['valueFrom'])
        self.assertEqual(env['PGPASSWORD'], {'name': 'PGPASSWORD', 'valueFrom': {
            'secretKeyRef': {'name': 'owned-private', 'key': 'pg_password'}}})
        self.assertNotIn('PRIVATE_PG', json.dumps(container))
        self.assertEqual(container['image'], self.c['images']['postgres'])
        self.assertEqual(container['securityContext']['runAsUser'], 70)
        self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])

    def test_schema_init_fixed_script_and_original_bootstrap(self):
        self.fixture()
        public, _ = f.render(self.c)
        job = next(d for d in public if d['kind'] == 'Job' and d['metadata']['name'] == 'owned-bootstrap')
        pod = job['spec']['template']['spec']
        self.assertEqual(job['spec']['backoffLimit'], 0)
        self.assertEqual(pod['restartPolicy'], 'Never')
        self.assertEqual(len(pod['initContainers']), 1)
        schema = pod['initContainers'][0]
        self.assertEqual(schema['command'], ['/bin/sh', '-ec'])
        self.assertEqual(schema['args'], [f.SCHEMA_INIT_SCRIPT])
        self.assertEqual(schema['env'], [
            {'name': 'PGHOST', 'value': 'owned-postgres.safe-logical.svc.cluster.local'},
            {'name': 'PGPORT', 'value': '5432'}, {'name': 'PGDATABASE', 'value': 'polaris'},
            {'name': 'PGUSER', 'value': 'polaris'}, {'name': 'PGPASSWORD', 'valueFrom': {
                'secretKeyRef': {'name': 'owned-private', 'key': 'pg_password'}}}])
        self.assertEqual(schema['image'], self.c['images']['postgres'])
        self.assertEqual(schema['securityContext']['runAsUser'], 70)
        self.assertTrue(schema['securityContext']['readOnlyRootFilesystem'])
        self.assertEqual(len(pod['containers']), 1)
        boot = pod['containers'][0]
        self.assertEqual(boot['name'], 'bootstrap')
        self.assertEqual(boot['image'], self.c['images']['polaris_admin'])
        self.assertEqual(boot['args'], ['bootstrap', '--credentials-file=/etc/foundation/credentials.json'])

    def run_schema_script(self, read_failures=0, ddl_failure=False):
        # PATH contains only these inert executables; no real psql or sleep can run.
        binary_dir = self.root/'bin'
        binary_dir.mkdir()
        fake = f'''#!{sys.executable}
import json
import os
from pathlib import Path
import sys
root = Path(os.environ['TEST_ROOT'])
name = Path(sys.argv[0]).name
events = root/'events.jsonl'
prior = [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
event = {{'name': name, 'argv': sys.argv[1:],
    'env': {{key: os.environ.get(key) for key in
        ('PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD', 'PGCONNECT_TIMEOUT')}}}}
with events.open('a') as stream:
    stream.write(json.dumps(event)+'\\n')
if name == 'sleep':
    sys.exit(0)
print('raw diagnostic '+os.environ['PGPASSWORD'])
print('raw diagnostic '+os.environ['PGPASSWORD'], file=sys.stderr)
if sys.argv[-1] == 'SELECT 1':
    count = sum(e['name'] == 'psql' and e['argv'][-1] == 'SELECT 1' for e in prior)
    sys.exit(1 if count < int(os.environ['TEST_READ_FAILURES']) else 0)
sys.exit(int(os.environ['TEST_DDL_FAILURE']))
'''
        for name in ('psql', 'sleep'):
            path = binary_dir/name
            path.write_text(fake)
            path.chmod(0o700)
        pg = {'PGHOST': 'owned-postgres.safe-logical.svc.cluster.local', 'PGPORT': '5432',
            'PGDATABASE': 'polaris', 'PGUSER': 'polaris', 'PGPASSWORD': 'FAKE_SECRET;$(never-run)'}
        result = subprocess.run(['/bin/sh', '-ec', f.SCHEMA_INIT_SCRIPT],
            env={**pg, 'PATH': str(binary_dir), 'TEST_ROOT': str(self.root),
                'TEST_READ_FAILURES': str(read_failures), 'TEST_DDL_FAILURE': str(int(ddl_failure)),
                'PGCONNECT_TIMEOUT': '999'}, capture_output=True, text=True, timeout=10)
        events = [json.loads(line) for line in (self.root/'events.jsonl').read_text().splitlines()]
        self.assertEqual(result.stdout, '')
        self.assertNotIn(pg['PGPASSWORD'], result.stderr)
        self.assertNotIn('raw diagnostic', result.stderr)
        for event in events:
            if event['name'] == 'psql':
                self.assertEqual(event['env'], {**pg, 'PGCONNECT_TIMEOUT': '2'})
                self.assertEqual(event['argv'][:-1],
                    ['--no-password', '--no-psqlrc', '--set', 'ON_ERROR_STOP=1', '--command'])
                self.assertIn(event['argv'][-1], ('SELECT 1', 'CREATE SCHEMA IF NOT EXISTS polaris_schema;'))
                self.assertNotIn(pg['PGPASSWORD'], json.dumps(event['argv']))
            else:
                self.assertEqual(event['argv'], ['1'])
        sequence = [e['argv'][-1] if e['name'] == 'psql' else 'sleep' for e in events]
        return result, sequence

    def test_schema_read_failures_then_success_runs_ddl_once(self):
        result, sequence = self.run_schema_script(read_failures=2)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, '')
        self.assertEqual(sequence, ['SELECT 1', 'sleep', 'SELECT 1', 'sleep', 'SELECT 1',
            'CREATE SCHEMA IF NOT EXISTS polaris_schema;'])

    def test_schema_fifteen_read_failures_never_runs_ddl(self):
        result, sequence = self.run_schema_script(read_failures=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, 'Schema database readiness check failed\n')
        self.assertEqual(sequence, ['SELECT 1', 'sleep'] * 14 + ['SELECT 1'])

    def test_schema_first_read_success_runs_ddl_once_without_sleep(self):
        result, sequence = self.run_schema_script()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, '')
        self.assertEqual(sequence, ['SELECT 1', 'CREATE SCHEMA IF NOT EXISTS polaris_schema;'])

    def test_schema_ddl_failure_is_never_retried(self):
        result, sequence = self.run_schema_script(ddl_failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, 'Schema initialization failed\n')
        self.assertEqual(sequence, ['SELECT 1', 'CREATE SCHEMA IF NOT EXISTS polaris_schema;'])

    def test_every_container_has_only_its_required_foundation_secret_projection(self):
        self.fixture()
        public, private = f.render(self.c)
        secrets = {d['metadata']['name']: set(d['stringData']) for d in private}
        allowed = {
            ('owned-seaweed', 'seaweedfs'): ({'s3.json', 'iam.json', 'filer.toml'}, set()),
            ('owned-seaweed', 'seaweedfs-data-init'): (set(), set()),
            ('owned-postgres', 'postgres'): (set(), {'pg_password'}),
            ('owned-bootstrap', 'schema'): (set(), {'pg_password'}),
            ('owned-bootstrap', 'bootstrap'): ({'credentials.json'}, {'pg_password'}),
            ('owned-polaris', 'polaris'): ({'public.pem', 'private.pem'}, {'pg_password', 'issuer_access', 'issuer_secret'}),
            ('trino-local', 'trino'): ({'trino.pem'}, {'shared'}),
            ('trino-local-worker', 'trino'): ({'trino.pem'}, {'shared'}),
            ('trino-local', 'logical-bootstrap'): (set(), set()),
            ('trino-opa', 'opa'): (set(), set()),
        }
        seen = set()
        for doc in public:
            if doc['kind'] not in ('Deployment', 'Job'):
                continue
            pod = doc['spec']['template']['spec']
            volumes = {v['name']: v for v in pod['volumes']}
            for container in pod['containers'] + pod.get('initContainers', []):
                key = (doc['metadata']['name'], container['name'])
                seen.add(key)
                files, envkeys = set(), set()
                self.assertNotIn('envFrom', container)
                for mount in container.get('volumeMounts', []):
                    secret = volumes[mount['name']].get('secret')
                    if not secret:
                        continue
                    self.assertNotIn('subPath', mount)
                    self.assertTrue(mount['readOnly'])
                    self.assertEqual(secret['defaultMode'], 0o440)
                    if secret['secretName'] == 'owned-private':
                        self.assertEqual(pod['securityContext']['fsGroup'], 10001)
                        self.assertIn('items', secret)
                        self.assertEqual(len(secret['items']), len({v['key'] for v in secret['items']}))
                        for item in secret['items']:
                            self.assertEqual(item['key'], item['path'])
                            self.assertIn(item['key'], secrets['owned-private'])
                            files.add(item['key'])
                    else:
                        self.assertEqual(key, ('trino-local', 'logical-bootstrap'))
                        self.assertEqual(secret['secretName'], 'owned-bootstrap')
                        self.assertEqual(secrets['owned-bootstrap'], {'password.db', 'rules.json', 'guard.rego', 'credentials.properties'})
                for env in container.get('env', []):
                    ref = env.get('valueFrom', {}).get('secretKeyRef')
                    if ref:
                        self.assertEqual(ref['name'], 'owned-private')
                        envkeys.add(ref['key'])
                self.assertEqual((files, envkeys), allowed[key], key)
                if key[0].startswith('trino'):
                    self.assertFalse((files | envkeys) & {'s3.json', 'iam.json', 'pg_password',
                        'credentials.json', 'private.pem', 'public.pem', 'issuer_access', 'issuer_secret'})
        self.assertEqual(seen, set(allowed))

    def test_private_files_exclusive_and_render_jar_limit(self):
        self.save('exclusive', 'secret')
        self.assertEqual(stat.S_IMODE((self.root/'exclusive').stat().st_mode), 0o600)
        with self.assertRaises(FileExistsError):
            self.save('exclusive', 'replacement')
        self.fixture()
        (self.root/'logical-secrets.jar').write_bytes(b'x'*700001)
        with self.assertRaises(f.ConfigError):
            f.render(self.c)

    def test_apply_requires_explicit_flag_before_any_command(self):
        with patch.object(f, '_run', side_effect=AssertionError('no dispatch')):
            with self.assertRaises(f.ConfigError):
                f.apply(self.c)

    def prepare_fixture(self):
        target = self.root/'prepared'
        jar = self.root/'input.jar'
        jar.write_bytes(b'product-code')
        calls = []
        def command(argv, **kwargs):
            calls.append(argv)
            if 'config' in argv:
                return json.dumps({'clusters': [{'cluster': {'server': self.c['kube']['api_server'],
                    'certificate-authority-data': base64.b64encode(b'cluster-ca').decode()}}]}).encode()
            if 'node' in argv:
                return b'{"metadata":{"uid":"captured-real-node-uid"}}'
            self.assertEqual(argv[:3], ['rtk', 'proxy', 'openssl'])
            for flag in ('-out', '-keyout'):
                if flag in argv:
                    Path(argv[argv.index(flag)+1]).write_text('MOCK_GENERATED_CRYPTO')
            return b''
        c = f.prepare(private_dir=target, kubeconfig=self.root/'kubeconfig', context='lab', node_name='lab-node',
            foundation_id='registered-core-wcg', revision=5, environment_id='lab', warehouse_id='warehouse',
            seaweed_image='docker.io/chrislusf/seaweedfs@sha256:'+'a'*64, jar=jar, command=command)
        return target, c, calls

    def test_prepare_mints_private_material_and_captures_real_supplied_pins(self):
        target, c, calls = self.prepare_fixture()
        self.assertEqual(c['storage']['account_id'], '000000000000')
        self.assertEqual(c['foundation_id'], 'registered-core-wcg')
        self.assertEqual(c['polaris']['credential_duration_seconds'], 900)
        self.assertEqual(c['polaris']['credential_cache_duration_seconds'], 450)
        self.assertEqual(json.loads((target/'settings.json').read_text()), c)
        self.assertEqual(json.loads((target/'rules.json').read_text()), f.bootstrap_rules())
        self.assertEqual(c['kube']['node_uids'], {'lab-node': 'captured-real-node-uid'})
        self.assertTrue(c['images']['polaris_admin'].startswith('docker.io/apache/polaris-admin-tool@sha256:'))
        for path in target.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
        self.assertNotIn('apply', [arg for argv in calls for arg in argv])
        admin = json.loads((target/'storage-admin.json').read_text())
        issuer = json.loads((target/'storage-issuer.json').read_text())
        self.assertNotEqual(admin, issuer)
        self.assertEqual(json.loads((target/'provisioner.json').read_text())['version'], 1)
        public = (target/'foundation-public.yaml').read_text()
        self.assertNotIn(admin['secret_key'], public)
        self.assertNotIn(issuer['secret_key'], public)
        self.assertNotIn('MOCK_GENERATED_CRYPTO', public)

    def test_prepare_signer_is_standard_base64_and_stays_private(self):
        signer_bytes = b'\xfb\xff\xff' * 16
        original_token_bytes = f.secrets.token_bytes
        def token_bytes(size):
            return signer_bytes if size == 48 else original_token_bytes(size)
        with patch.object(f.secrets, 'token_bytes', side_effect=token_bytes), \
                patch('sys.stdout', new_callable=io.StringIO) as stdout:
            target, _, _ = self.prepare_fixture()
        self.assertEqual(stdout.getvalue(), '')
        state = json.loads((target/'foundation-private.json').read_text())
        signer = state['signer']
        self.assertEqual(base64.b64decode(signer, validate=True), signer_bytes)
        self.assertEqual(len(base64.b64decode(signer, validate=True)), 48)
        self.assertIn('+', signer)
        self.assertIn('/', signer)
        self.assertNotIn('-', signer)
        self.assertNotIn('_', signer)
        documents = list(f.yaml.safe_load_all((target/'foundation-secrets.yaml').read_text()))
        iam = json.loads(next(doc['stringData']['iam.json'] for doc in documents
            if 'iam.json' in doc['stringData']))
        self.assertEqual(iam['sts']['signingKey'], signer)
        self.assertEqual(iam['sts']['accountId'], '000000000000')
        s3 = json.loads(next(doc['stringData']['s3.json'] for doc in documents
            if 's3.json' in doc['stringData']))
        issuer = next(identity for identity in s3['identities'] if identity['name'] == 'polaris-issuer')
        self.assertEqual(issuer['account']['id'], '000000000000')
        self.assertEqual(issuer['actions'], [])
        self.assertNotIn(signer, (target/'foundation-public.yaml').read_text())
        for name in ('foundation-private.json', 'foundation-secrets.yaml'):
            self.assertEqual(stat.S_IMODE((target/name).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)

    def test_prepare_initial_trino_password_matches_sha1_known_vector(self):
        password = 'public-trino-test-password'
        salt = bytes(range(16))
        # Independently derived with RFC 8018's HMAC-SHA1 block/XOR construction.
        derived = '7344e33a35a6af308a3cc635c67609db4d4b2af25b83a2f3c857bc8d6505704a'
        original_token_bytes = f.secrets.token_bytes
        def token_bytes(size):
            return salt if size == 16 else original_token_bytes(size)
        with patch.object(f.secrets, 'token_bytes', side_effect=token_bytes), \
                patch.object(f.secrets, 'token_urlsafe', return_value=password):
            target, _, _ = self.prepare_fixture()
        expected = f'logical_provisioner:100000:000102030405060708090a0b0c0d0e0f:{derived}\n'
        self.assertEqual((target/'password.db').read_text(), expected)
        self.assertEqual(json.loads((target/'provisioner.json').read_text())['password'], password)
        state = json.loads((target/'foundation-private.json').read_text())
        self.assertEqual(state['bootstrap']['password.db'], expected)
        documents = list(f.yaml.safe_load_all((target/'foundation-secrets.yaml').read_text()))
        self.assertEqual(next(doc['stringData']['password.db'] for doc in documents
            if 'password.db' in doc['stringData']), expected)
        self.assertNotEqual(hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000, 32).hex(), derived)

    def retry_fixture(self):
        self.fixture()
        public, private = f.render(self.c)
        existing = {(d['kind'], d['metadata']['name']): copy.deepcopy(d) for d in public
            if d['kind'] in ('Namespace', 'PersistentVolumeClaim', 'Service')}
        for index, doc in enumerate(existing.values()):
            doc['metadata'].update(uid='owned-uid-'+str(index), resourceVersion=str(42+index))
            if doc['kind'] == 'PersistentVolumeClaim':
                doc['spec']['volumeName'] = 'pvc-owned-'+str(index)
                doc['status'] = {'phase': 'Bound'}
            elif doc['kind'] == 'Service':
                doc['spec'].update(type='ClusterIP', clusterIP='10.43.0.'+str(index),
                    clusterIPs=['10.43.0.'+str(index)], ipFamilies=['IPv4'],
                    ipFamilyPolicy='SingleStack', sessionAffinity='None', internalTrafficPolicy='Cluster')
        calls, writes = [], []
        def command(argv, *, input=None):
            args = argv[argv.index('--request-timeout=15s')+1:]
            calls.append(args)
            if args[0] == 'get':
                kind = 'Namespace' if args[1] == 'namespace' else args[1]
                return json.dumps(existing.get((kind, args[2]), {})).encode()
            if input:
                writes.append((args[0], f.yaml.safe_load(input)))
            return b''
        provider = MagicMock()
        provider.__enter__.return_value = provider
        provider._incarnation = {}
        provider.probe.return_value = {'ready': True}
        return public, private, existing, calls, writes, command, provider

    def test_partial_owned_retry_preserves_allocations_and_stages(self):
        public, private, existing, calls, writes, command, provider = self.retry_fixture()
        with patch('tools.local_experiment.logical_provider.Provider', return_value=provider):
            f.apply(self.c, apply=True, command=command)
        desired = {(d['kind'], d['metadata']['name']): d for d in public+private}
        for verb, doc in writes:
            key = (doc['kind'], doc['metadata']['name'])
            original = desired[key]
            if key in existing:
                live = existing[key]
                self.assertEqual(verb, 'replace')
                for field in ('uid', 'resourceVersion'):
                    self.assertEqual(doc['metadata'][field], live['metadata'][field])
                if doc['kind'] == 'PersistentVolumeClaim':
                    self.assertEqual(doc['spec']['volumeName'], live['spec']['volumeName'])
                    for field in ('resources', 'storageClassName', 'accessModes', 'volumeMode'):
                        self.assertEqual(doc['spec'][field], original['spec'][field])
                else:
                    for field in ('clusterIP', 'clusterIPs', 'ipFamilies', 'ipFamilyPolicy',
                            'type', 'sessionAffinity', 'internalTrafficPolicy'):
                        self.assertEqual(doc['spec'][field], live['spec'][field])
                    for field in ('ports', 'selector'):
                        self.assertEqual(doc['spec'][field], original['spec'][field])
            else:
                self.assertEqual(verb, 'create')
        self.assertEqual(provider._incarnation['namespace'], existing[('Namespace', 'safe-logical')]['metadata']['uid'])
        provider._trust.assert_called()
        self.assertTrue(all(args[0] in ('get', 'create', 'replace', 'rollout', 'wait') for args in calls))
        self.assertFalse(any(doc['kind'] == 'Namespace' for _, doc in writes))
        expected = private + [d for d in public[1:] if d['kind'] not in ('Deployment', 'Job')]
        expected += [d for d in public if d['kind'] == 'Deployment' and d['metadata']['name'] in ('owned-postgres', 'owned-seaweed')]
        expected += [d for d in public if d['kind'] == 'Job']
        expected += [d for d in public if d['kind'] == 'Deployment' and d['metadata']['name'] not in ('owned-postgres', 'owned-seaweed')]
        self.assertEqual([(d['kind'], d['metadata']['name']) for _, d in writes],
            [(d['kind'], d['metadata']['name']) for d in expected])
        self.assertEqual([args[2] for args in calls if args[0] == 'rollout'],
            ['deployment/'+d['metadata']['name'] for d in expected if d['kind'] == 'Deployment'])
        self.assertEqual(sum(args[0] == 'wait' for args in calls), 1)
        self.assertEqual([call.args[0] for call in provider._storage.call_args_list], ['head_bucket', 'head_bucket'])

    def test_retry_rejects_changed_desired_pvc_shape_before_all_writes(self):
        public, private, existing, calls, writes, command, provider = self.retry_fixture()
        for change in ({'storageClassName': 'other'}, {'accessModes': ['ReadWriteMany']},
                {'volumeMode': 'Block'}, {'resources': {'requests': {'storage': '999Gi'}}},
                {'volumeName': 'unrelated'}, {'selector': {'matchLabels': {'other': 'data'}}},
                {'dataSource': {'kind': 'PersistentVolumeClaim', 'name': 'clone'}}):
            with self.subTest(change=change):
                changed = copy.deepcopy(public)
                next(d for d in changed if d['kind'] == 'PersistentVolumeClaim')['spec'].update(change)
                calls.clear()
                with patch.object(f, 'render', return_value=(changed, private)), \
                        patch('tools.local_experiment.logical_provider.Provider', return_value=provider), self.assertRaises(f.ConfigError):
                    f.apply(self.c, apply=True, command=command)
                self.assertFalse(writes)
                self.assertTrue(all(args[0] == 'get' for args in calls))

    def test_retry_rejects_incompatible_services_before_all_writes(self):
        public, private, existing, calls, writes, command, provider = self.retry_fixture()
        for change in ({'clusterIP': '10.43.99.99'}, {'clusterIPs': ['10.43.99.99']},
                {'ipFamilies': ['IPv6']}, {'ipFamilyPolicy': 'RequireDualStack'},
                {'type': 'NodePort'}, {'type': 'LoadBalancer'}, {'clusterIP': 'None'},
                {'ports': [{'port': 80, 'nodePort': 30080}]}):
            with self.subTest(change=change):
                changed = copy.deepcopy(public)
                next(d for d in changed if d['kind'] == 'Service')['spec'].update(change)
                calls.clear()
                with patch.object(f, 'render', return_value=(changed, private)), \
                        patch('tools.local_experiment.logical_provider.Provider', return_value=provider), self.assertRaises(f.ConfigError):
                    f.apply(self.c, apply=True, command=command)
                self.assertFalse(writes)
                self.assertTrue(all(args[0] == 'get' for args in calls))

    def test_retry_rejects_foreign_or_changed_live_shape_before_all_writes(self):
        public, private, existing, calls, writes, command, provider = self.retry_fixture()
        for kind, field, value in (
                ('Namespace', f.LABEL, 'foreign'), ('Namespace', f.REVISION, '999'),
                ('PersistentVolumeClaim', f.LABEL, 'foreign'), ('Service', f.REVISION, '999'),
                ('PersistentVolumeClaim', 'dataSourceRef', {'kind': 'PersistentVolumeClaim', 'name': 'clone'}),
                ('PersistentVolumeClaim', 'selector', {'matchLabels': {'other': 'data'}}),
                ('Service', 'type', 'NodePort'), ('Service', 'clusterIP', 'None')):
            with self.subTest(kind=kind, field=field):
                doc = next(d for d in existing.values() if d['kind'] == kind)
                target = doc['metadata']['labels'] if field in (f.LABEL, f.REVISION) else doc['spec']
                before = copy.deepcopy(target)
                target[field] = value
                calls.clear()
                with patch('tools.local_experiment.logical_provider.Provider', return_value=provider), self.assertRaises(f.ConfigError):
                    f.apply(self.c, apply=True, command=command)
                self.assertFalse(writes)
                self.assertTrue(all(args[0] == 'get' for args in calls))
                target.clear()
                target.update(before)

    def test_retry_unbound_pvc_and_omitted_volume_mode(self):
        public, private, existing, calls, writes, command, provider = self.retry_fixture()
        for doc in existing.values():
            if doc['kind'] == 'PersistentVolumeClaim':
                doc['spec'].pop('volumeName')
                doc.pop('status')
        for doc in public:
            if doc['kind'] == 'PersistentVolumeClaim':
                doc['spec'].pop('volumeMode')
        with patch.object(f, 'render', return_value=(public, private)), \
                patch('tools.local_experiment.logical_provider.Provider', return_value=provider):
            f.apply(self.c, apply=True, command=command)
        for _, doc in writes:
            if doc['kind'] == 'PersistentVolumeClaim':
                self.assertNotIn('volumeName', doc['spec'])
                self.assertEqual(doc['spec']['volumeMode'], 'Filesystem')

    def test_apply_stages_job_without_replay_and_checks_bucket_then_probe(self):
        from tools.local_experiment.logical_provider import Rejected
        self.fixture()
        public, private = f.render(self.c)
        existing = {(doc['kind'], doc['metadata']['name']): copy.deepcopy(doc) for doc in public+private}
        for doc in existing.values():
            doc['metadata'].update(uid='owned-uid', resourceVersion='42')
        calls = []
        def command(argv, *, input=None):
            args = argv[argv.index('--request-timeout=15s')+1:]
            calls.append(args)
            if args[0] == 'get':
                kind = 'Namespace' if args[1] == 'namespace' else args[1]
                return json.dumps(existing.get((kind, args[2]), {})).encode()
            return b''
        provider = MagicMock()
        provider.__enter__.return_value = provider
        provider._incarnation = {}
        provider._storage.side_effect = [Rejected('PROVIDER_STORAGE_HTTP_404'), {}, {}]
        provider.probe.return_value = {'ready': True}
        with patch('tools.local_experiment.logical_provider.Provider', return_value=provider):
            f.apply(self.c, apply=True, command=command)
        writes = [args for args in calls if args[0] in ('create', 'replace')]
        self.assertTrue(writes)
        self.assertFalse(any(args[0] == 'create' for args in writes))
        # The existing Job is awaited; it is never resubmitted.
        self.assertEqual(sum(args[:3] == ['wait', '--for=condition=complete', 'job/owned-bootstrap'] for args in calls), 1)
        # Each deployment rollout is checked in the frozen staged order.
        rollouts = [args[2] for args in calls if args[0] == 'rollout']
        self.assertEqual(set(rollouts[:2]), {'deployment/owned-postgres', 'deployment/owned-seaweed'})
        self.assertEqual([call.args[0] for call in provider._storage.call_args_list], ['head_bucket', 'create_bucket', 'head_bucket'])
        provider.effects.assert_called_once()
        provider.probe.assert_called_once()
        provider._storage.side_effect = None
        provider.probe.return_value = {'ready': False}
        with patch('tools.local_experiment.logical_provider.Provider', return_value=provider), \
                patch.object(f.time, 'monotonic', side_effect=[0, 60]), \
                patch.object(f.time, 'sleep') as sleep, self.assertRaises(f.ConfigError):
            f.apply(self.c, apply=True, command=command)
        sleep.assert_not_called()

    def test_pg_rollout_finishes_before_bootstrap_creation(self):
        _, _, _, calls, writes, command, provider = self.retry_fixture()
        def ordered_command(argv, *, input=None):
            if input and f.yaml.safe_load(input)['kind'] == 'Job':
                self.assertIn(['rollout', 'status', 'deployment/owned-postgres', '--timeout=180s'], calls)
            return command(argv, input=input)
        with patch('tools.local_experiment.logical_provider.Provider', return_value=provider):
            f.apply(self.c, apply=True, command=ordered_command)
        self.assertEqual(sum(d['kind'] == 'Job' for _, d in writes), 1)
        self.assertFalse(any(args[0] == 'delete' for args in calls))

    def test_final_probe_observes_readiness_without_repeating_storage_mutation(self):
        from tools.local_experiment.logical_provider import Rejected
        _, _, _, calls, writes, command, provider = self.retry_fixture()
        for ready, times, succeeds in (([False, True], [0, 0], True), ([False, False], [0, 0, 60], False)):
            with self.subTest(ready=ready):
                provider.reset_mock(side_effect=True)
                calls.clear()
                writes.clear()
                provider._storage.side_effect = [Rejected('PROVIDER_STORAGE_HTTP_404'), {}, {}]
                trust_count = []
                def probe():
                    trust_count.append(provider._trust.call_count)
                    return {'ready': ready[len(trust_count)-1]}
                provider.probe.side_effect = probe
                with patch('tools.local_experiment.logical_provider.Provider', return_value=provider), \
                        patch.object(f.time, 'monotonic', side_effect=times), patch.object(f.time, 'sleep') as sleep:
                    if succeeds:
                        f.apply(self.c, apply=True, command=command)
                    else:
                        with self.assertRaises(f.ConfigError):
                            f.apply(self.c, apply=True, command=command)
                self.assertEqual(trust_count[1], trust_count[0]+1)
                self.assertEqual(provider.probe.call_count, 2)
                sleep.assert_called_once_with(0.5)
                self.assertEqual([c.args[0] for c in provider._storage.call_args_list],
                    ['head_bucket', 'create_bucket', 'head_bucket'])
                provider.effects.assert_called_once()
                self.assertEqual(sum(d['kind'] == 'Job' for _, d in writes), 1)
                self.assertFalse(any(args[0] == 'delete' for args in calls))

    def test_final_probe_and_trust_exceptions_propagate_without_retry(self):
        from tools.local_experiment.logical_provider import Rejected, Unknown
        _, _, _, _, writes, command, provider = self.retry_fixture()
        for failure in ('probe', 'trust'):
            with self.subTest(failure=failure):
                provider.reset_mock(side_effect=True)
                writes.clear()
                provider._storage.side_effect = [Rejected('PROVIDER_STORAGE_HTTP_404'), {}, {}]
                error = Unknown() if failure == 'probe' else Rejected('FOUNDATION_PIN_MISMATCH')
                def probe():
                    if failure == 'probe':
                        raise error
                    provider._trust.side_effect = error
                    return {'ready': False}
                provider.probe.side_effect = probe
                with patch('tools.local_experiment.logical_provider.Provider', return_value=provider), \
                        patch.object(f.time, 'monotonic', side_effect=[0, 0]), patch.object(f.time, 'sleep') as sleep:
                    with self.assertRaises(type(error)) as raised:
                        f.apply(self.c, apply=True, command=command)
                self.assertIs(raised.exception, error)
                provider.probe.assert_called_once()
                self.assertEqual(sleep.call_count, 0 if failure == 'probe' else 1)
                self.assertEqual([c.args[0] for c in provider._storage.call_args_list],
                    ['head_bucket', 'create_bucket', 'head_bucket'])
                self.assertEqual(sum(d['kind'] == 'Job' for _, d in writes), 1)

    def test_bucket_unknown_and_non404_failures_are_not_retried(self):
        from tools.local_experiment.logical_provider import Rejected, Unknown
        _, _, _, _, _, command, provider = self.retry_fixture()
        for results, expected in (([Unknown()], ['head_bucket']),
                ([Rejected('PROVIDER_STORAGE_HTTP_403')], ['head_bucket']),
                ([Rejected('PROVIDER_STORAGE_HTTP_404'), Unknown()], ['head_bucket', 'create_bucket'])):
            with self.subTest(expected=expected):
                provider.reset_mock(side_effect=True)
                provider._storage.side_effect = results
                with patch('tools.local_experiment.logical_provider.Provider', return_value=provider), \
                        patch.object(f.time, 'sleep') as sleep, self.assertRaises(type(results[-1])):
                    f.apply(self.c, apply=True, command=command)
                self.assertEqual([c.args[0] for c in provider._storage.call_args_list], expected)
                provider.probe.assert_not_called()
                sleep.assert_not_called()

    def test_catalog_reader_source_is_fixed_path_mode_and_digest_only(self):
        java = (f.ROOT/'tools/local_experiment/logical_secrets/src/main/java/org/shirokuma/logical/secrets/FixedPublisher.java').read_text()
        mode = java.split('case "catalog" -> {', 1)[1].split('case "read" ->', 1)[0]
        self.assertIn('request.size() == 1', mode)
        self.assertIn('name.matches("[A-Za-z][A-Za-z0-9_]{0,62}")', mode)
        self.assertIn('Path.of("/var/trino-catalogs").resolve(name + ".properties")', mode)
        self.assertIn('properties.load(reader)', mode)
        self.assertIn('new TreeSet<>(properties.stringPropertyNames())', mode)
        self.assertIn('catalog.sha256=', mode)
        self.assertNotIn('mutationStarted =', mode)
        self.assertNotIn('Files.write', mode)
        self.assertNotIn('System.out.println(properties', mode)

    def test_source_has_no_prototype_imports_and_build_is_inert(self):
        source = (f.ROOT/'tools/local_experiment/logical_provider.py').read_text() + Path(f.__file__).read_text()
        for module in ('database_poc', 'database_service_poc', 'warehouse_operator', 'database_generation'):
            self.assertNotIn('import '+module, source)
        folder = f.ROOT/'tools/local_experiment/logical_secrets'
        java = '\n'.join(p.read_text() for p in folder.rglob('*.java'))
        self.assertIn('/etc/trino-private-secrets/credentials.properties', java)
        self.assertIn('LOGICAL_SECRETS_DENIED', java)
        self.assertIn('ATOMIC_MOVE', java)
        self.assertIn('FileLock', java)
        self.assertNotIn('package io.airlift', java)
        self.assertTrue(any(p.name == 'io.airlift.spi.secrets.SecretsPlugin' for p in folder.rglob('*')))

    def test_fixed_worker_defaults_and_render_options(self):
        self.fixture()
        old = copy.deepcopy(self.c)
        old['trino'].pop('workers')
        old['trino'].pop('include_coordinator')
        self.assertEqual(f.validate(old)['trino'], self.c['trino'])
        for workers, include in ((-1, True), (2, True), (True, True), (0, False), (1, 1)):
            bad = copy.deepcopy(self.c)
            bad['trino'].update(workers=workers, include_coordinator=include)
            with self.assertRaises(f.ConfigError):
                f.validate(bad)
        for workers, include in ((0, True), (1, True), (1, False)):
            self.c['trino'].update(workers=workers, include_coordinator=include)
            public, _ = f.render(self.c)
            deployments = {d['metadata']['name']: d for d in public if d['kind'] == 'Deployment'}
            coordinator = deployments['trino-local']['spec']['template']['spec']
            worker = deployments['trino-local-worker']
            self.assertEqual(worker['spec']['replicas'], workers)
            pod = worker['spec']['template']['spec']
            self.assertNotIn('initContainers', pod)
            self.assertEqual(pod['nodeSelector'], coordinator['nodeSelector'])
            self.assertEqual(pod['containers'][0]['image'], coordinator['containers'][0]['image'])
            mounts = pod['containers'][0]['volumeMounts']
            self.assertTrue(next(m for m in mounts if m['name'] == 'runtime')['readOnly'])
            self.assertFalse(any(m['name'] in ('catalogs', 'bootstrap') for m in mounts))
            self.assertFalse(any(v.get('persistentVolumeClaim', {}).get('claimName') == 'trino-catalogs' for v in pod['volumes']))
            service = next(d for d in public if d['kind'] == 'Service' and d['metadata']['name'] == 'trino-local')
            self.assertEqual(service['spec']['selector'], {'app.kubernetes.io/name': 'trino-local'})
            self.assertEqual([p['port'] for p in service['spec']['ports']], [8080, 8081])
            configs = {d['metadata']['name']: d['data'] for d in public if d['kind'] == 'ConfigMap'}
            for name in ('owned-trino-config', 'owned-trino-worker-config'):
                config = configs[name]
                main = config['config.properties']
                for line in ('discovery.uri=http://trino-local:8081', 'http-server.http.port=8081',
                        'http-server.authentication.allow-insecure-over-http=false',
                        'http-server.https.port=8080', 'http-server.authentication.type=PASSWORD',
                        'internal-communication.shared-secret=${ENV:TRINO_INTERNAL_SHARED_SECRET}'):
                    self.assertIn(line+'\n', main)
                self.assertEqual(config['password-authenticator.properties'], configs['owned-trino-config']['password-authenticator.properties'])
                self.assertEqual(config['secrets.toml'], configs['owned-trino-config']['secrets.toml'])
            worker_config = configs['owned-trino-worker-config']['config.properties']
            self.assertIn('coordinator=false\n', worker_config)
            self.assertIn('catalog.management=dynamic\n', worker_config)
            self.assertNotIn('catalog.store=', worker_config)
            self.assertNotIn('catalog.config-dir=', worker_config)
            self.assertIn('node-scheduler.include-coordinator='+str(include).lower()+'\n', configs['owned-trino-config']['config.properties'])
        with patch.object(f, 'prepare', return_value={}) as prepare:
            args = ['prepare']
            for name in ('private-dir', 'kubeconfig', 'context', 'node-name', 'foundation-id', 'environment-id', 'warehouse-id', 'seaweed-image', 'jar'):
                args += ['--'+name, 'mock']
            self.assertEqual(f.main(args+['--revision', '1', '--workers', '1', '--exclude-coordinator']), 0)
            self.assertEqual(prepare.call_args.kwargs['workers'], 1)
            self.assertTrue(prepare.call_args.kwargs['exclude_coordinator'])

    def test_build_mocked_fixed_argv_private_output_and_jar_allowlist(self):
        from tools.local_experiment.logical_secrets import build as builder
        def archive(path, extra=None, service=None):
            with zipfile.ZipFile(path, 'w') as jar:
                jar.writestr('META-INF/', '')
                jar.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\r\nClass-Path: '+builder.SPI+'\r\n\r\n')
                jar.writestr(builder.SERVICE, (service or builder.PLUGIN)+'\n')
                for name in ('FileSecretsPlugin', 'FixedPublisher', 'FixedPublisher$Nested'):
                    jar.writestr('org/shirokuma/logical/secrets/'+name+'.class', b'own-code')
                if extra:
                    jar.writestr(extra, b'bad')
        calls = []
        def command(argv, **kwargs):
            calls.append(argv)
            self.assertEqual(argv[:7], ['rtk', 'proxy', 'docker', '--context', 'colima-mac-studio-solo', 'run', '--rm'])
            for option in ('--pull=never', '--network=none', '--read-only'):
                self.assertIn(option, argv)
            self.assertEqual(argv[-3:], [builder.IMAGE, '-ec', builder.SCRIPT])
            mounts = [argv[i+1] for i, arg in enumerate(argv) if arg == '--mount']
            self.assertEqual(len(mounts), 2)
            self.assertEqual(mounts[0], 'type=bind,src='+str(builder.SOURCE)+',dst=/src,readonly')
            directory = Path(mounts[1].split('src=', 1)[1].split(',dst=', 1)[0])
            self.assertEqual(directory.parent, self.root)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(list(directory.iterdir()), [])
            archive(directory/'logical-secrets.jar')
            return subprocess.CompletedProcess(argv, 0)
        output = self.root/'private output.jar'
        self.assertEqual(builder.build(output, command=command), output)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        builder.validate_jar(output)
        with self.assertRaises(FileExistsError):
            builder.build(output, command=command)
        self.assertEqual(len(calls), 1)
        builder.build(output, overwrite=True, context='colima-mac-studio-solo', command=command)
        self.assertNotEqual(calls[0], calls[1])  # Fresh isolated build output.
        for context in ('default', 'unrelated', '', None):
            with self.subTest(context=context), self.assertRaisesRegex(ValueError, 'UNAUTHORIZED_DOCKER_CONTEXT'):
                builder.build(self.root/'unauthorized.jar', context=context, command=command)
        self.assertEqual(len(calls), 2)
        with patch.object(builder, 'build') as mocked, patch('sys.stderr'), patch('sys.stdout'):
            with self.assertRaises(SystemExit):
                builder.main(['--output', str(output), '--context', 'unrelated'])
            mocked.assert_not_called()
            self.assertEqual(builder.main(['--output', str(output)]), 0)
            mocked.assert_called_once_with(str(output), overwrite=False, context=builder.CONTEXT)
        for extra in ('io/airlift/Injected.class', 'io/trino/Injected.class', 'other/Bad.class',
                'org/shirokuma/logical/secrets/../../Bad.class', '/Bad.class',
                'org/shirokuma/logical/secrets/sub/Bad.class', 'prototype.json'):
            archive(self.root/'bad.jar', extra=extra)
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                builder.validate_jar(self.root/'bad.jar')
        archive(self.root/'bad.jar', service='io.trino.OtherPlugin')
        with self.assertRaises(ValueError):
            builder.validate_jar(self.root/'bad.jar')
        for output in ('relative.jar', self.root/'not-a-jar.txt'):
            with self.assertRaises(ValueError):
                builder.build(output, command=command)
        with self.assertRaises(ValueError):
            builder.build(self.root/'failure.jar', command=lambda *args, **kwargs: subprocess.CompletedProcess(args, 1))
        self.assertFalse((self.root/'failure.jar').exists())


if __name__ == '__main__':
    unittest.main()
