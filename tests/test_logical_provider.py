"""Mock-only provider contract tests, including uncertain dispatch boundaries."""
import base64
import asyncio
import copy
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
import stat
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_logical_foundation import isolated_import, settings
from tools.local_experiment import logical_foundation as f
from tools.local_experiment import logical_provider as p

PROBE_SQL = "SELECT current_user, version(), coordinator, active_nodes FROM (SELECT coordinator, count(*) AS active_nodes FROM system.runtime.nodes WHERE state='active' GROUP BY coordinator) AS active_compute"


class ProviderImportTests(unittest.TestCase):
    def test_isolated_direct_import(self):
        isolated_import(self, 'logical_provider', 'direct')

    def test_isolated_package_import(self):
        isolated_import(self, 'logical_provider', 'package')


class Child:
    def __init__(self, port):
        self.stdout = io.BytesIO(f'Forwarding from 127.0.0.1:{port} -> 8080\n'.encode())
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout):
        return self.returncode

    def kill(self):
        self.returncode = -9


class Harness:
    def __init__(self, config):
        self.c = config
        self.commands, self.http_calls, self.sdk_calls, self.children = [], [], [], []
        self.view = {'clusters': [{'cluster': {'server': config['kube']['api_server'], 'certificate-authority-data': base64.b64encode(b'cluster-ca').decode()}}]}
        self.node_uid = 'node-uid'
        self.ns_uid = 'namespace-uid'
        self.exec_handler = None
        self.http_handler = None
        self.iam_handler = None
        self.hashes = {'target.'+filename: hashlib.sha256(filename.encode()).hexdigest() for filename in p.TARGETS.values()}
        self.pod_uid = 'pod-uid'

    def resource(self, kind, name):
        result = {'metadata': {'name': name, 'uid': kind+'-uid', 'labels': {f.LABEL: self.c['foundation_id'], f.REVISION: str(self.c['revision'])}}}
        if kind != 'namespace':
            result['metadata']['namespace'] = self.c['kube']['namespace']
        if kind == 'namespace':
            result['metadata']['uid'] = self.ns_uid
            result['metadata']['labels'].update({'pod-security.kubernetes.io/enforce': 'restricted', 'shirokuma.dev/native-admission': 'logical-v1'})
        elif kind == 'pod':
            workload = name.removesuffix('-pod')
            key = {'owned-seaweed': 'seaweedfs', 'owned-postgres': 'postgres', 'owned-polaris': 'polaris', 'trino-opa': 'opa'}.get(workload, 'trino')
            uid = self.pod_uid if workload == 'trino-local' else workload+'-pod-uid'
            result['metadata'].update(uid=uid, ownerReferences=[{'kind': 'ReplicaSet', 'name': workload+'-rs', 'uid': 'replicaset-uid', 'controller': True}])
            result['spec'] = {'nodeName': 'lab-node', 'containers': [{'name': key, 'image': self.c['images'][key]}]}
            if key == 'trino':
                worker = workload == 'trino-local-worker'
                result['spec'].update(securityContext={'runAsUser': 10000, 'runAsGroup': 10001, 'runAsNonRoot': True},
                    volumes=[{'name': 'runtime', 'persistentVolumeClaim': {'claimName': 'trino-runtime'}}])
                result['spec']['containers'][0]['volumeMounts'] = [
                    {'name': 'runtime', 'mountPath': '/etc/trino-private-secrets', 'readOnly': worker}]
                for path, name, readonly in (('/tmp', 'tmp', False), ('/opt/logical-secrets', 'code', True),
                        ('/usr/lib/trino/secrets-plugin/logical-file', 'code', True),
                        ('/etc/trino', 'config', True), ('/etc/foundation', 'private', True)):
                    result['spec']['containers'][0]['volumeMounts'].append({'name': name, 'mountPath': path, 'readOnly': readonly})
                result['spec']['containers'][0]['securityContext'] = {'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False,
                    'runAsUser': 10000, 'runAsNonRoot': True, 'capabilities': {'drop': ['ALL']}}
                result['spec']['volumes'] += [{'name': 'tmp', 'emptyDir': {}},
                    {'name': 'code', 'configMap': {'name': self.c['prefix']+'-secrets-code'}},
                    {'name': 'config', 'configMap': {'name': self.c['prefix']+('-trino-worker-config' if worker else '-trino-config')}},
                    {'name': 'private', 'secret': {'secretName': self.c['prefix']+'-private'}}]
                if not worker:
                    result['spec']['containers'][0]['volumeMounts'].append({'name': 'catalogs', 'mountPath': '/var/trino-catalogs', 'readOnly': False})
                    result['spec']['volumes'].append({'name': 'catalogs', 'persistentVolumeClaim': {'claimName': 'trino-catalogs'}})
            result['status'] = {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}],
                'containerStatuses': [{'name': key, 'ready': True, 'imageID': self.c['images'][key]}]}
        elif kind == 'deployment':
            result['metadata']['generation'] = 2
            replicas = self.c['trino']['workers'] if name == 'trino-local-worker' else 1
            result['status'] = {'observedGeneration': 2, 'availableReplicas': replicas, 'readyReplicas': replicas,
                'updatedReplicas': replicas, 'replicas': replicas}
            result['spec'] = {'replicas': replicas}
            if name == 'trino-opa':
                result['spec']['template'] = {'spec': {'containers': [{'name': 'opa', 'image': self.c['images']['opa']}]}}
        elif kind == 'replicaset':
            result['metadata']['ownerReferences'] = [{'kind': 'Deployment', 'name': name.removesuffix('-rs'), 'uid': 'deployment-uid', 'controller': True}]
        elif kind == 'service':
            result['spec'] = {'selector': {'app.kubernetes.io/name': name}}
        elif kind == 'pvc':
            result['status'] = {'phase': 'Bound'}
            result['spec'] = {'storageClassName': 'local-path', 'volumeMode': 'Filesystem',
                'accessModes': ['ReadWriteOnce'], 'volumeName': name+'-pv'}
        elif kind == 'pv':
            result['status'] = {'phase': 'Bound'}
            result['spec'] = {'storageClassName': 'local-path', 'volumeMode': 'Filesystem', 'local': {'path': '/local/data'},
                'claimRef': {'uid': 'pvc-uid', 'name': name.removesuffix('-pv'), 'namespace': self.c['kube']['namespace']},
                'nodeAffinity': {'required': {'nodeSelectorTerms': [{'matchExpressions': [
                    {'key': 'kubernetes.io/hostname', 'operator': 'In', 'values': ['lab-node']}]}]}}}
        elif kind == 'configmap':
            result['binaryData'] = {'logical-secrets.jar': base64.b64encode(b'code').decode()}
            result['data'] = {'sha256': hashlib.sha256(b'code').hexdigest()}
        return result

    def command(self, argv, *, input=None):
        self.commands.append((argv, input))
        args = argv[argv.index('--request-timeout=15s')+1:]
        if args[:2] == ['config', 'view']:
            return json.dumps(self.view).encode()
        if args[:2] == ['get', 'node']:
            return json.dumps({'metadata': {'uid': self.node_uid}}).encode()
        if args[:2] == ['get', 'pods']:
            selector = args[args.index('-l')+1] if '-l' in args else None
            if selector is None:
                names = ['trino-local'] + (['trino-local-worker'] if self.c['trino']['workers'] else [])
            else:
                names = [selector.split('=', 1)[1]]
            return json.dumps({'items': [self.resource('pod', name+'-pod') for name in names]}).encode()
        if args[0] == 'get':
            return json.dumps(self.resource(args[1], args[2])).encode()
        if args[0] == 'exec':
            if self.exec_handler:
                return self.exec_handler(argv, input)
            mode = args[-1]
            pairs = self.hashes if mode == 'read' else {'ref.sha256': hashlib.sha256(b'secret').hexdigest()} if mode == 'resolve' else self.hashes
            return ('status=OK\n'+''.join(k+'='+v+'\n' for k, v in pairs.items())).encode()
        raise AssertionError('unexpected mock command')

    def popen(self, argv, **kwargs):
        self.commands.append((argv, kwargs))
        child = Child(20000+len(self.children))
        self.children.append(child)
        return child

    def http(self, method, url, **kwargs):
        self.http_calls.append((method, url, kwargs))
        if self.http_handler:
            return self.http_handler(method, url, **kwargs)
        if url.endswith('/oauth/tokens'):
            return 200, b'{"access_token":"private-token"}'
        if url.endswith('/v1/info'):
            return 200, b'{"starting":false,"nodeVersion":{"version":"483"}}'
        if url.endswith('/v1/statement') and method == 'POST':
            statement = kwargs['data'].decode()
            if statement == PROBE_SQL:
                rows = [['logical_provisioner', '483', True, 1]] + (
                    [['logical_provisioner', '483', False, self.c['trino']['workers']]]
                    if self.c['trino']['workers'] else [])
                return 200, json.dumps({'data': rows}).encode()
        if url.endswith('/principals') and method == 'POST':
            return 201, b'{"credentials":{"clientId":"client","clientSecret":"private-secret"}}'
        return 200, b'{}'

    def sdk(self, service, **kwargs):
        self.sdk_calls.append((service, kwargs))
        harness = self
        class SDK:
            def __getattr__(self, method):
                def call(**params):
                    harness.sdk_calls.append((method, params))
                    if method == 'get_caller_identity':
                        return {'Account': '000000000000', 'Arn': 'arn:aws:iam::000000000000:user/polaris-issuer'}
                    if harness.iam_handler:
                        return harness.iam_handler(method, params)
                    if method == 'create_policy':
                        return {'Policy': {'Arn': 'arn:aws:iam::000000000000:policy/owned-db-1',
                            'PolicyName': 'owned-db-1', 'PolicyId': 'policy-id'}}
                    if method == 'create_role':
                        return {'Role': {'Arn': 'arn:aws:iam::000000000000:role/owned-db-1'}}
                    return {}
                return call
        return SDK()


class MetadataTransportTests(unittest.TestCase):
    """Default executor with fake TLS/HTTPS only; never open real sockets."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.c = settings(self.root)
        self.h = Harness(self.c)
        self.h.view.update({'current-context': self.c['kube']['context'],
            'contexts': [{'name': self.c['kube']['context'], 'context': {'cluster': 'lab', 'user': 'client'}}],
            'users': [{'name': 'client', 'user': {'client-certificate-data': base64.b64encode(b'fixture-cert').decode(),
                'client-key-data': base64.b64encode(b'fixture-key').decode()}}]})
        self.h.view['clusters'][0]['name'] = 'lab'
        self.connections, self.contexts, self.scratch_paths = [], [], []
        self.response_handler = self.metadata_response
        owner = self
        class Connection:
            def __init__(self, host, port, **kwargs):
                self.host, self.port, self.options = host, port, kwargs
                self.closed, self.pending, self.requests = False, None, []
                owner.connections.append(self)
            def request(self, method, path, headers):
                owner.assertFalse(self.closed)
                owner.assertIsNone(self.pending)
                owner.assertEqual(method, 'GET')
                self.requests.append((method, path, headers))
                self.pending = path
            def getresponse(self):
                path, self.pending = self.pending, None
                return owner.response_handler(path)
            def close(self):
                self.closed = True
        def ssl_context(**kwargs):
            self.assertEqual(kwargs, {'cadata': 'cluster-ca'})
            context = Mock(verify_mode=p.ssl.CERT_REQUIRED, check_hostname=True)
            def load(cert, key, *, password):
                self.assertEqual(password, '')
                paths = (Path(cert), Path(key))
                for path in paths:
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                    self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
                context.material = tuple(path.read_bytes() for path in paths)
                self.scratch_paths.extend(paths)
            context.load_cert_chain.side_effect = load
            self.contexts.append(context)
            return context
        for target, kwargs in [('_command', {'side_effect': self.h.command})]:
            patcher = patch.object(p, target, **kwargs)
            self.command = patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(p.http.client, 'HTTPSConnection', side_effect=Connection)
        self.https = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(p.ssl, 'create_default_context', side_effect=ssl_context)
        self.ssl = patcher.start()
        self.addCleanup(patcher.stop)
        self.provider = p.Provider(self.c, http=self.h.http, sdk=self.h.sdk, popen=self.h.popen)
        self.addCleanup(self.provider.close)

    def metadata_response(self, path):
        parts = p.urllib.parse.urlsplit(path)
        pieces = parts.path.split('/')
        if pieces[-1] == 'pods':
            selectors = p.urllib.parse.parse_qs(parts.query)
            names = [selectors['labelSelector'][0].split('=', 1)[1]] if selectors else ['trino-local']
            result = {'metadata': {}, 'items': [self.h.resource('pod', name+'-pod') for name in names]}
        else:
            kind = {'nodes': 'node', 'namespaces': 'namespace', 'persistentvolumes': 'pv',
                'services': 'service', 'pods': 'pod', 'persistentvolumeclaims': 'pvc',
                'configmaps': 'configmap', 'deployments': 'deployment', 'replicasets': 'replicaset'}[pieces[-2]]
            name = p.urllib.parse.unquote(pieces[-1])
            result = self.h.resource(kind, name)
            if kind == 'node':
                result = {'metadata': {'name': name, 'uid': self.h.node_uid}}
        return Mock(status=200, read=Mock(return_value=json.dumps(result).encode()))

    def paths(self):
        return [path for connection in self.connections for _, path, _ in connection.requests]

    def inline_view(self):
        return {**copy.deepcopy(self.h.view), 'apiVersion': 'v1', 'kind': 'Config', 'preferences': {}}

    def write_inline(self, view=None, *, yaml=False):
        path = Path(self.c['kube']['kubeconfig'])
        view = self.inline_view() if view is None else view
        path.write_text(p.yaml.safe_dump(view) if yaml else json.dumps(view))
        return path

    def assert_observation_only(self):
        self.assertFalse(self.h.http_calls)
        self.assertFalse(self.h.sdk_calls)
        self.assertFalse(self.h.children)
        for argv, body in self.h.commands:
            self.assertEqual(argv, f.kube_argv(self.c) +
                ['config', 'view', '--minify', '--raw', '--flatten', '-o', 'json'])
            self.assertIsNone(body)
        self.assertTrue(all(method == 'GET' for connection in self.connections
            for method, _, _ in connection.requests))

    def test_fresh_inline_json_and_yaml_public_dispatch_rereads_and_checks_both_uids(self):
        self.provider._incarnation['namespace'] = self.h.ns_uid
        original_read = Path.read_bytes
        for as_yaml in (False, True):
            path = self.write_inline(yaml=as_yaml)
            with patch.object(Path, 'read_bytes', autospec=True, side_effect=original_read) as read:
                before = len(self.paths())
                for _ in range(2):
                    self.assertEqual(self.provider.compute_state(), 'ACTIVE')
                self.assertEqual(sum(call.args == (path,) for call in read.call_args_list), 2)
            fresh = self.paths()[before:]
            self.assertEqual(fresh.count('/api/v1/nodes/lab-node'), 2)
            self.assertEqual(fresh.count('/api/v1/namespaces/safe-logical'), 2)
        self.command.assert_not_called()
        self.assertEqual(len(self.connections), 1)
        self.assert_observation_only()

    def test_fresh_inline_same_path_size_and_mtime_client_drift_replaces_tls(self):
        view = self.inline_view()
        path = self.write_inline(view)
        stamp = path.stat()
        self.provider._trust()
        for field, value in [('client-certificate-data', b'rotated-cert'), ('client-key-data', b'rotated-key')]:
            old = self.connections[-1]
            before = len(old.requests)
            fingerprint = self.provider._metadata_transport._fingerprint
            view['users'][0]['user'][field] = base64.b64encode(value).decode()
            self.write_inline(view)
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            self.assertEqual(path.stat().st_size, stamp.st_size)
            self.assertEqual(path.stat().st_mtime_ns, stamp.st_mtime_ns)
            self.provider._trust()
            self.assertTrue(old.closed)
            self.assertEqual(len(old.requests), before)
            self.assertIsNot(self.connections[-1], old)
            self.assertNotEqual(self.provider._metadata_transport._fingerprint, fingerprint)
        self.assertEqual(self.contexts[-1].material, (b'rotated-cert', b'rotated-key'))
        self.assertFalse(any(path.exists() for path in self.scratch_paths))
        self.command.assert_not_called()
        self.assert_observation_only()

    def test_fresh_inline_bad_eligible_material_and_authority_close_without_fallback(self):
        valid = self.inline_view()
        changes = [lambda v: v.update(**{'current-context': 'foreign'}),
            lambda v: v['contexts'][0].update(name='foreign'),
            lambda v: v['contexts'][0]['context'].update(user='foreign'),
            lambda v: v['contexts'][0]['context'].update(cluster='foreign'),
            lambda v: v['clusters'][0]['cluster'].update(server='https://foreign:6443'),
            lambda v: v['clusters'][0]['cluster'].update(**{'certificate-authority-data': 'invalid!'}),
            lambda v: v['clusters'][0]['cluster'].update(**{'certificate-authority-data': base64.b64encode(b'foreign-ca').decode()}),
            lambda v: v['clusters'][0]['cluster'].update(**{'insecure-skip-tls-verify': True}),
            lambda v: v['users'][0]['user'].update(**{'client-certificate-data': ''}),
            lambda v: v['users'][0]['user'].update(**{'client-key-data': 'invalid!'})]
        for as_yaml in (False, True):
            for change in changes:
                self.write_inline(valid, yaml=as_yaml)
                self.provider._trust()
                old = self.connections[-1]
                before = len(self.paths())
                changed = copy.deepcopy(valid)
                change(changed)
                self.write_inline(changed, yaml=as_yaml)
                with self.subTest(yaml=as_yaml, change=change), self.assertRaises(p.Rejected) as caught:
                    self.provider._trust()
                self.assertEqual(str(caught.exception), 'FOUNDATION_TRUST_MISMATCH')
                self.assertTrue(old.closed)
                self.assertIsNone(self.provider._metadata_transport._fingerprint)
                self.assertEqual(len(self.paths()), before)
        self.command.assert_not_called()
        self.assert_observation_only()

    def test_fresh_inline_invalid_tls_material_closes_and_redacts_error(self):
        self.write_inline()
        self.provider._trust()
        old = self.connections[-1]
        view = self.inline_view()
        view['users'][0]['user']['client-key-data'] = base64.b64encode(b'invalid-private-key').decode()
        self.write_inline(view)
        context = Mock(verify_mode=p.ssl.CERT_REQUIRED, check_hostname=True)
        context.load_cert_chain.side_effect = p.ssl.SSLError('invalid-private-key')
        before = len(self.paths())
        with patch.object(p.ssl, 'create_default_context', return_value=context), self.assertRaises(p.Rejected) as caught:
            self.provider._trust()
        self.assertEqual(str(caught.exception), 'FOUNDATION_TRUST_MISMATCH')
        self.assertTrue(old.closed)
        self.assertEqual(len(self.paths()), before)
        self.assertIsNone(self.provider._metadata_transport._connection)
        self.command.assert_not_called()
        self.assert_observation_only()

    def test_fresh_inline_node_namespace_and_native_ownership_swaps_reject(self):
        self.write_inline()
        self.provider._incarnation['namespace'] = self.h.ns_uid
        resource = self.h.resource
        def foreign_namespace(kind, name):
            result = resource(kind, name)
            if kind == 'namespace':
                result['metadata']['labels']['shirokuma.dev/native-admission'] = 'foreign'
            return result
        self.provider._trust()
        for target, value in [('node_uid', 'changed-node'), ('ns_uid', 'changed-namespace')]:
            old = self.connections[-1]
            original = getattr(self.h, target)
            setattr(self.h, target, value)
            with self.assertRaises(p.Rejected):
                self.provider._trust()
            self.assertTrue(old.closed)
            setattr(self.h, target, original)
            self.provider._trust()
        old = self.connections[-1]
        with patch.object(self.h, 'resource', side_effect=foreign_namespace), self.assertRaises(p.Rejected):
            self.provider._trust()
        self.assertTrue(old.closed)
        self.command.assert_not_called()
        self.assert_observation_only()

    def test_fresh_inline_close_epoch_fences_paused_parse_and_new_lifecycle(self):
        self.write_inline()
        self.provider._trust()
        for reopen in (False, True):
            if self.provider._closed:
                self.provider.open()
            entered, release = threading.Event(), threading.Event()
            read_bytes = Path.read_bytes
            def paused_read(path):
                raw = read_bytes(path)
                if path == Path(self.c['kube']['kubeconfig']) and threading.current_thread() is not threading.main_thread():
                    entered.set()
                    self.assertTrue(release.wait(timeout=3))
                return raw
            with patch.object(Path, 'read_bytes', paused_read), ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.provider._trust)
                try:
                    self.assertTrue(entered.wait(timeout=3))
                    self.provider.close()
                    if reopen:
                        self.provider.open()
                        current = self.connections[-1]
                    before = (len(self.connections), len(self.paths()))
                finally:
                    release.set()
                with self.assertRaises(p.Rejected):
                    pending.result(timeout=3)
            self.assertEqual((len(self.connections), len(self.paths())), before)
            if reopen:
                self.assertIs(self.provider._metadata_transport._connection, current)
                self.assertFalse(current.closed)
            else:
                self.assertIsNone(self.provider._metadata_transport._connection)
        self.command.assert_not_called()
        # Explicit open uses only the existing mocked loopback port forwards.
        self.assertEqual(len(self.h.children), 6)
        self.assertTrue(all('port-forward' in argv and '--address=127.0.0.1' in argv
            for argv, _ in self.h.commands))
        self.assertFalse(self.h.http_calls or self.h.sdk_calls)
        self.assertTrue(all(method == 'GET' for connection in self.connections
            for method, _, _ in connection.requests))

    def test_fresh_inline_custom_command_never_reads_file(self):
        self.write_inline()
        provider = p.Provider(self.c, command=self.h.command, http=self.h.http, sdk=self.h.sdk, popen=self.h.popen)
        self.addCleanup(provider.close)
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('custom transport must not read')):
            provider._trust()
        self.assertEqual(self.h.commands[0][0], f.kube_argv(self.c) +
            ['config', 'view', '--minify', '--raw', '--flatten', '-o', 'json'])
        self.assertEqual(self.connections, [])
        self.assertFalse(self.h.http_calls or self.h.sdk_calls or self.h.children)
        self.assertTrue(all('config' in argv or 'get' in argv for argv, _ in self.h.commands))

    def test_fresh_inline_noncanonical_fallback_argv_and_existing_validation(self):
        valid = self.inline_view()
        changes = [lambda v: v.pop('apiVersion'), lambda v: v.update(kind='Other'),
            lambda v: v['contexts'].append(copy.deepcopy(v['contexts'][0])),
            lambda v: v['clusters'].append(copy.deepcopy(v['clusters'][0])),
            lambda v: v['users'].append(copy.deepcopy(v['users'][0])),
            lambda v: v['users'][0]['user'].update(token='private-token'),
            lambda v: v['users'][0]['user'].update(exec={'command': 'arbitrary'}),
            lambda v: v['users'][0]['user'].update(**{'auth-provider': {'name': 'external'}}),
            lambda v: v['clusters'][0]['cluster'].update(**{'proxy-url': 'https://foreign'}),
            lambda v: v['clusters'][0]['cluster'].update(**{'tls-server-name': 'foreign'}),
            lambda v: v['clusters'][0]['cluster'].update(**{'certificate-authority': '/external-ca'}),
            lambda v: v['users'][0]['user'].update(**{'client-key': '/external-key'}),
            lambda v: v['users'][0]['user'].update(**{'client-certificate': '/external-cert'}),
            lambda v: v['contexts'][0]['context'].update(namespace=True),
            lambda v: v['clusters'][0]['cluster'].update(**{'insecure-skip-tls-verify': 'false'})]
        for as_yaml in (False, True):
            for change in changes:
                view = copy.deepcopy(valid)
                change(view)
                self.write_inline(view, yaml=as_yaml)
                before = self.command.call_count
                self.provider._trust()  # kubectl's flattened view is still validated.
                self.assertEqual(self.command.call_count, before+1)
        path = Path(self.c['kube']['kubeconfig'])
        raw = json.dumps(valid)
        ambiguous = ['{"kind":"Other",'+raw[1:],
            p.yaml.safe_dump(valid)+'kind: Other\n',
            p.yaml.safe_dump(valid).replace('current-context: lab-context', 'current-context: on'),
            p.yaml.safe_dump(valid).replace('current-context: lab-context', 'current-context: 123'),
            p.yaml.safe_dump(valid)+'unsupported: !!str value\n',
            raw.replace('"user": "client"', '"user": "foreign", "user": "client"'),
            'private-key: [malformed', '---\n'+p.yaml.safe_dump(valid)+'---\n{}',
            p.yaml.safe_dump(valid).replace('preferences: {}', 'preferences: &prefs {}')+'unknown: *prefs\n']
        for content in ambiguous:
            path.write_text(content)
            before = self.command.call_count
            self.provider._trust()
            self.assertEqual(self.command.call_count, before+1)
        # A failed fallback view must not be rescued by the local file or old TLS.
        self.write_inline({**valid, 'unsupported': 'field'})
        old = self.connections[-1]
        self.h.view['users'][0]['user']['token'] = 'private-token'
        before = len(self.paths())
        with self.assertRaises(p.Rejected) as caught:
            self.provider._trust()
        self.assertEqual(str(caught.exception), 'FOUNDATION_TRUST_MISMATCH')
        self.assertTrue(old.closed)
        self.assertEqual(len(self.paths()), before)
        self.assert_observation_only()

    def test_reuses_connection_but_each_trust_and_owned_read_is_fresh(self):
        self.provider._trust()
        ns = self.provider._owned('namespace', self.c['kube']['namespace'])
        self.provider._incarnation['namespace'] = ns['metadata']['uid']
        self.provider._owned('service', 'trino-local')
        self.provider._trust()
        self.provider._owned('service', 'trino-local')
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(self.command.call_count, 2)  # Both calls are canonical config views.
        self.assertTrue(all('config' in call.args[0] for call in self.command.call_args_list))
        self.assertEqual(len(self.paths()), 6)  # Two nodes, two namespaces, two services.
        self.assertEqual(self.connections[0].options['timeout'], 15)
        self.assertEqual(self.contexts[0].material, (b'fixture-cert', b'fixture-key'))
        for path in self.scratch_paths:
            self.assertFalse(path.exists())
            self.assertFalse(path.parent.exists())

    def test_all_fixed_routes_include_configured_namespace_and_no_cached_resource_version(self):
        self.provider._trust()
        for kind, name in [('node', 'lab-node'), ('namespace', 'safe-logical'), ('pv', 'runtime-pv'),
                ('service', 'trino-local'), ('pod', 'trino-local-pod'), ('pvc', 'trino-runtime'),
                ('configmap', 'owned-secrets-code'), ('deployment', 'trino-local'), ('replicaset', 'trino-local-rs')]:
            value = self.provider._json(self.provider._cmd(['get', kind, name, '-o', 'json']))
            self.assertEqual(value['metadata']['name'], name)
        self.provider._cmd(['get', 'pods', '-o', 'json'])
        self.provider._cmd(['get', 'pods', '-l', 'app.kubernetes.io/name=owned-polaris', '-o', 'json'])
        self.assertIn('/apis/apps/v1/namespaces/safe-logical/deployments/trino-local', self.paths())
        self.assertIn('/api/v1/persistentvolumes/runtime-pv', self.paths())
        self.assertIn('/api/v1/namespaces/safe-logical/pods?labelSelector=app.kubernetes.io%2Fname%3Downed-polaris', self.paths())
        self.assertFalse(any('resourceVersion' in path or 'watch' in path for path in self.paths()))
        self.assertEqual(self.command.call_count, 1)

    def test_node_namespace_and_service_changes_are_observed_and_rejected(self):
        self.provider._trust()
        namespace = self.provider._owned('namespace', 'safe-logical')
        self.provider._incarnation['namespace'] = namespace['metadata']['uid']
        self.provider._owned('service', 'trino-local')
        resource = self.h.resource
        def replacement(kind, name):
            result = resource(kind, name)
            if kind == 'service':
                result['metadata']['uid'] = 'replacement'
            return result
        with patch.object(self.h, 'resource', side_effect=replacement):
            with self.assertRaises(p.Rejected):
                self.provider._owned('service', 'trino-local')
        def foreign_selector(kind, name):
            result = resource(kind, name)
            if kind == 'service':
                result['spec']['selector'] = {'app': 'foreign'}
            return result
        with patch.object(self.h, 'resource', side_effect=foreign_selector), self.assertRaises(p.Rejected):
            self.provider._owned('service', 'trino-local')
        self.h.ns_uid = 'changed-namespace'
        with self.assertRaises(p.Rejected):
            self.provider._trust()
        self.assertTrue(self.connections[-1].closed)
        self.h.ns_uid = 'namespace-uid'
        self.h.node_uid = 'changed-node'
        with self.assertRaises(p.Rejected):
            self.provider._trust()
        self.assertTrue(self.connections[-1].closed)

    def test_canonical_ambiguity_and_unsupported_auth_close_before_get(self):
        valid = copy.deepcopy(self.h.view)
        changes = [lambda v: v.update(**{'current-context': 'foreign'}),
            lambda v: v['contexts'][0].update(name='foreign'),
            lambda v: v['contexts'][0]['context'].update(user='foreign'),
            lambda v: v['contexts'][0]['context'].update(cluster='foreign'),
            lambda v: v['clusters'].append(copy.deepcopy(v['clusters'][0])),
            lambda v: v['users'][0]['user'].update(token='private-token'),
            lambda v: v['users'][0]['user'].update(exec={'command': 'arbitrary'}),
            lambda v: v['users'][0]['user'].update(**{'as': 'foreign'}),
            lambda v: v['clusters'][0]['cluster'].update(**{'proxy-url': 'https://foreign'}),
            lambda v: v['clusters'][0]['cluster'].update(**{'tls-server-name': 'foreign'}),
            lambda v: v['clusters'][0]['cluster'].update(**{'insecure-skip-tls-verify': True}),
            lambda v: v['clusters'][0]['cluster'].update(**{'certificate-authority-data': 'invalid!'}),
            lambda v: v['clusters'][0]['cluster'].update(**{'certificate-authority-data': base64.b64encode(b'foreign-ca').decode()}),
            lambda v: v['users'][0]['user'].pop('client-certificate-data'),
            lambda v: v['users'][0]['user'].pop('client-key-data'),
            lambda v: v['users'][0]['user'].update(**{'client-key-data': 'invalid!'})]
        for change in changes:
            self.h.view = copy.deepcopy(valid)
            self.provider._trust()
            previous = self.connections[-1]
            before = len(self.paths())
            change(self.h.view)
            with self.subTest(change=change), self.assertRaises(p.Rejected) as caught:
                self.provider._trust()
            self.assertEqual(str(caught.exception), 'FOUNDATION_TRUST_MISMATCH')
            self.assertTrue(previous.closed)
            self.assertEqual(len(self.paths()), before)
            self.assertIsNone(self.provider._metadata_transport._fingerprint)

    def test_pinned_server_still_rejects_url_features_before_http(self):
        original = self.provider._config['kube']['api_server']
        valid = copy.deepcopy(self.h.view)
        for server in ('https://user@host:6443', 'https://host:6443/', 'https://host:6443/path',
                'https://host:6443?query=1', 'https://host:6443#fragment', 'http://host:6443',
                'https://foreign:6443'):
            self.h.view = copy.deepcopy(valid)
            self.h.view['clusters'][0]['cluster']['server'] = server
            # Also exercise URL validation independently of the equality pin.
            if 'foreign' not in server:
                self.provider._config['kube']['api_server'] = server
            with self.subTest(server=server), self.assertRaises(p.Rejected):
                self.provider._trust()
            self.assertEqual(self.connections, [])
            self.provider._config['kube']['api_server'] = original

    def test_client_material_drift_rebuilds_context_and_never_reuses_old_connection(self):
        self.provider._trust()
        for field, content in [('client-certificate-data', b'new-cert'), ('client-key-data', b'new-key')]:
            old = self.connections[-1]
            count = len(old.requests)
            self.h.view['users'][0]['user'][field] = base64.b64encode(content).decode()
            self.provider._trust()
            self.assertTrue(old.closed)
            self.assertEqual(len(old.requests), count)
            self.assertIsNot(self.connections[-1], old)
        self.assertEqual(len(self.contexts), 3)
        self.assertEqual(self.contexts[-1].material, (b'new-cert', b'new-key'))
        self.assertFalse(any(path.exists() for path in self.scratch_paths))

    def test_http_json_metadata_and_transport_errors_close_without_retry_or_subprocess_fallback(self):
        for status, raw in [(401, b'private-key'), (403, b''), (404, b''), (302, b''), (500, b''),
                (200, b'not json private-key'), (200, b'[]'), (200, b'{}'),
                (200, b'{"metadata":{}}'), (200, b'{"metadata":{"name":"lab-node","uid":null}}'),
                (200, b'x'*(p._KubeMetadata.LIMIT+1))]:
            self.response_handler = self.metadata_response
            self.provider._trust()
            before = self.command.call_count
            self.response_handler = lambda path: Mock(status=status, read=Mock(return_value=raw))
            previous = self.connections[-1]
            count = len(previous.requests)
            with self.subTest(status=status, raw=raw[:60]), self.assertRaises(p.Rejected) as caught:
                self.provider._cmd(['get', 'node', 'lab-node', '-o', 'json'])
            self.assertEqual(str(caught.exception), 'PROVIDER_METADATA_OBSERVATION_FAILED')
            self.assertEqual(self.command.call_count, before)
            self.assertTrue(previous.closed)
            self.assertEqual(len(previous.requests), count+1)
        for failure in (p.ssl.SSLError('private-key'), OSError('private-key'), TimeoutError('private-key')):
            self.response_handler = self.metadata_response
            self.provider._trust()
            with patch.object(self.connections[-1], 'request', side_effect=failure) as request:
                with self.assertRaises(p.Rejected) as caught:
                    self.provider._cmd(['get', 'node', 'lab-node', '-o', 'json'])
                self.assertNotIn('private-key', str(caught.exception))
                request.assert_called_once()
            self.assertTrue(self.connections[-1].closed)

    def test_ssl_load_failure_removes_private_scratch_and_old_connection(self):
        self.provider._trust()
        old = self.connections[-1]
        self.h.view['users'][0]['user']['client-key-data'] = base64.b64encode(b'new-key').decode()
        def fail(cert, key, *, password):
            self.scratch_paths.extend([Path(cert), Path(key)])
            raise p.ssl.SSLError('private-key')
        context = Mock(verify_mode=p.ssl.CERT_REQUIRED, check_hostname=True)
        context.load_cert_chain.side_effect = fail
        with patch.object(p.ssl, 'create_default_context', return_value=context), self.assertRaises(p.Rejected):
            self.provider._trust()
        self.assertTrue(old.closed)
        self.assertFalse(any(path.exists() or path.parent.exists() for path in self.scratch_paths))

    def test_only_fixed_get_grammar_is_accepted_and_exec_keeps_kubectl_and_trust(self):
        self.provider._trust()
        for args in (['get', 'secret', 'private', '-o', 'json'], ['get', 'node', 'foreign', '-o', 'json'],
                ['get', 'namespace', 'foreign', '-o', 'json'], ['get', 'pod', '../escape', '-o', 'json'],
                ['get', 'pods', '-l', 'app.kubernetes.io/name=foreign', '-o', 'json'],
                ['get', 'pods', '--watch', '-o', 'json']):
            self.provider._trust()
            before = len(self.paths())
            with self.subTest(args=args), self.assertRaises(p.Rejected):
                self.provider._cmd(args)
            self.assertEqual(len(self.paths()), before)
        self.provider._trust()
        for mode in ('read', 'resolve', 'catalog', 'publish'):
            before = self.command.call_count
            self.provider._cmd(['exec', '-i', 'trino-local-pod', '-c', 'trino', '--', *p.PUBLISHER, mode],
                mutation=mode == 'publish')
            self.assertEqual(self.command.call_count, before+2)  # config view, then kubectl exec.
        with patch.object(self.provider, '_trust', side_effect=p.Rejected('FOUNDATION_TRUST_MISMATCH')):
            before = self.command.call_count
            with self.assertRaises(p.Rejected):
                self.provider._cmd(['exec', 'trino-local-pod'])
            self.assertEqual(self.command.call_count, before)
        for kwargs in ({'input': b'body'}, {'mutation': True}):
            self.provider._trust()
            with self.assertRaises(p.Rejected):
                self.provider._cmd(['get', 'pod', 'trino-local-pod', '-o', 'json'], **kwargs)

    def test_default_publisher_keeps_effect_callback_and_immediate_uid_fence(self):
        self.provider._trust()
        effects = []
        def journal(key, identity, call):
            effects.append(identity)
            return call()
        with self.provider.effects(journal):
            self.assertEqual(self.provider._exec('publish', b'fixture-payload'), self.h.hashes)
        self.assertEqual(effects, [{'method': 'publish', 'service': 'trino-local',
            'target': '/etc/trino-private-secrets',
            'payload_sha256': hashlib.sha256(b'fixture-payload').hexdigest()}])
        self.assertTrue(any('exec' in argv for argv, _ in self.h.commands))
        self.h.commands.clear()
        resource = self.h.resource
        reads = 0
        def replaced(kind, name):
            nonlocal reads
            result = resource(kind, name)
            if kind == 'pod' and name == 'trino-local-pod':
                reads += 1
                if reads == 3:  # List, writer selection, then immediate dispatch reread.
                    result['metadata']['uid'] = 'replaced-before-exec'
            return result
        with patch.object(self.h, 'resource', side_effect=replaced), \
                self.provider.effects(journal), self.assertRaises(p.Rejected):
            self.provider._exec('publish', b'fixture-payload')
        self.assertFalse(any('exec' in argv for argv, _ in self.h.commands))
        self.assertEqual(len(effects), 1)

    def test_transport_serializes_only_requests_and_close_clears_material(self):
        self.provider._trust()
        def observe(_):
            return self.provider._json(self.provider._cmd(['get', 'service', 'trino-local', '-o', 'json']))
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(observe, range(32)))
        self.assertEqual(len(results), 32)
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(len(self.paths()), 33)
        entered, release = threading.Event(), threading.Event()
        def held_response(path):
            entered.set()
            self.assertTrue(release.wait(timeout=2))
            return self.metadata_response(path)
        self.response_handler = held_response
        with ThreadPoolExecutor(max_workers=2) as pool:
            reading = pool.submit(observe, None)
            try:
                self.assertTrue(entered.wait(timeout=2))
                # The response holds only the transport lock, not the workflow lock.
                transport_acquired = self.provider._metadata_transport._lock.acquire(blocking=False)
                if transport_acquired:
                    self.provider._metadata_transport._lock.release()
                self.assertFalse(transport_acquired)
                self.assertTrue(self.provider._lock.acquire(blocking=False))
                self.provider._lock.release()
                closing = pool.submit(self.provider.close)
            finally:
                release.set()
            reading.result(timeout=2)
            closing.result(timeout=2)
        self.assertTrue(self.connections[-1].closed)
        self.assertIsNone(self.provider._metadata_transport._fingerprint)
        self.assertIsNone(self.provider._metadata_transport._connection)
        self.assertFalse(any(path.exists() for path in self.scratch_paths))

    def _check_close_during_canonical_trust(self, *, reopen):
        entered, release = threading.Event(), threading.Event()
        raw = self.provider._raw
        def paused_view(argv, **kwargs):
            result = raw(argv, **kwargs)
            if 'config' in argv and threading.current_thread() is not threading.main_thread():
                entered.set()
                self.assertTrue(release.wait(timeout=3))
            return result
        with patch.object(self.provider, '_raw', side_effect=paused_view), ThreadPoolExecutor(max_workers=1) as pool:
            old_trust = pool.submit(self.provider._trust)
            try:
                self.assertTrue(entered.wait(timeout=3))
                self.assertEqual(self.connections, [])
                self.provider.close()  # Must complete while canonical view's caller is paused.
                self.assertTrue(self.provider._closed)
                if reopen:
                    self.provider.open()
                    current = self.connections[-1]
                    self.assertFalse(current.closed)
                before = (len(self.connections), len(self.paths()))
            finally:
                release.set()
            with self.assertRaises(p.Rejected):
                old_trust.result(timeout=3)
        self.assertEqual((len(self.connections), len(self.paths())), before)
        if reopen:
            self.assertFalse(current.closed)  # The old epoch's exception cannot tear down the new one.
            self.assertIs(self.provider._metadata_transport._connection, current)
            self.assertEqual(self.provider.compute_state(), 'ACTIVE')
        else:
            self.assertEqual(before, (0, 0))
            self.assertIsNone(self.provider._metadata_transport._connection)
            self.assertIsNone(self.provider._metadata_transport._fingerprint)
        self.assertFalse(any(path.exists() for path in self.scratch_paths))

    def test_close_fences_trust_paused_after_canonical_view_before_configure(self):
        self._check_close_during_canonical_trust(reopen=False)

    def test_old_trust_cannot_configure_or_close_connection_after_explicit_reopen(self):
        self._check_close_during_canonical_trust(reopen=True)

    def test_explicit_close_is_terminal_until_open_but_unopened_and_dead_pf_compute_are_allowed(self):
        self.assertFalse(self.provider._opened)
        self.assertEqual(self.provider.compute_state(), 'ACTIVE')
        self.provider.close()
        before = self.command.call_count
        for observe in (self.provider.compute_state, self.provider._trust):
            with self.subTest(observe=observe), self.assertRaises(p.Rejected) as caught:
                observe()
            self.assertEqual(caught.exception.code, 'FOUNDATION_CLOSED')
        self.assertEqual(self.command.call_count, before)
        self.provider.open()
        self.assertEqual(self.provider.compute_state(), 'ACTIVE')
        for child in self.h.children:
            child.returncode = 1
        self.assertFalse(self.provider.bound_alive())
        self.assertEqual(self.provider.compute_state(), 'ACTIVE')

    def test_close_between_configure_and_get_rejects_old_epoch_without_using_new_transport(self):
        entered, release = threading.Event(), threading.Event()
        get = self.provider._metadata_transport.get
        def paused_get(*args, **kwargs):
            if threading.current_thread() is not threading.main_thread():
                entered.set()
                self.assertTrue(release.wait(timeout=3))
            return get(*args, **kwargs)
        with patch.object(self.provider._metadata_transport, 'get', side_effect=paused_get), \
                ThreadPoolExecutor(max_workers=1) as pool:
            old_trust = pool.submit(self.provider._trust)
            try:
                self.assertTrue(entered.wait(timeout=3))
                self.assertEqual(len(self.connections), 1)
                self.assertEqual(self.paths(), [])
                self.provider.close()
                self.assertTrue(self.connections[0].closed)
                self.provider.open()
                current = self.connections[-1]
                before = len(self.paths())
            finally:
                release.set()
            with self.assertRaises(p.Rejected):
                old_trust.result(timeout=3)
        self.assertEqual(len(self.paths()), before)
        self.assertFalse(current.closed)
        self.assertIs(self.provider._metadata_transport._connection, current)

    def test_failed_explicit_open_remains_closed_and_has_no_transport(self):
        self.provider.close()
        self.h.view['current-context'] = 'foreign'
        with self.assertRaises(p.Rejected):
            self.provider.open()
        self.assertTrue(self.provider._closed)
        self.assertIsNone(self.provider._metadata_transport._connection)
        before = self.command.call_count
        with self.assertRaises(p.Rejected):
            self.provider.compute_state()
        self.assertEqual(self.command.call_count, before)

    def test_custom_command_executor_never_requires_tls_or_creates_connection(self):
        self.h.view = {'clusters': [{'cluster': {'server': self.c['kube']['api_server'],
            'certificate-authority-data': base64.b64encode(b'cluster-ca').decode()}}]}
        custom = p.Provider(self.c, command=self.h.command)
        self.addCleanup(custom.close)
        custom._trust()
        custom._owned('service', 'trino-local')
        self.assertIsNone(custom._metadata_transport)
        self.https.assert_not_called()
        self.ssl.assert_not_called()
        self.assertTrue(any('node' in argv for argv, _ in self.h.commands))
        self.assertTrue(any('service' in argv for argv, _ in self.h.commands))


class APISerializedHarness(Harness):
    """Kubernetes omits the default false value on VolumeMount.readOnly."""
    def resource(self, kind, name):
        result = super().resource(kind, name)
        spec = result.get('spec', {})
        for container in spec.get('containers', []) + spec.get('initContainers', []):
            for mount in container.get('volumeMounts', []):
                if mount.get('readOnly') is False:
                    del mount['readOnly']
        return result


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.c = settings(self.root)
        for name, value in [('admin.json', {'access_key': 'admin', 'secret_key': 'admin-secret'}),
                ('issuer.json', {'access_key': 'issuer', 'secret_key': 'issuer-secret'}),
                ('root.json', {'client_id': 'root', 'client_secret': 'root-secret'}),
                ('provisioner.json', {'version': 1, 'username': 'logical_provisioner', 'password': 'provisioner-secret'})]:
            f.save_private(self.root/name, json.dumps(value))
        f.save_private(self.root/'logical-secrets.jar', b'code')
        self.h = Harness(self.c)
        self.provider = p.Provider(self.c, command=self.h.command, http=self.h.http, sdk=self.h.sdk, popen=self.h.popen)
        self.addCleanup(self.provider.close)
        self.db = {'database_id': 'db-1', 'sql_catalog': 'DB_one', 'generation_id': 'wcg-owned', 'foundation_revision': 3}

    def bind(self):
        self.open(effects=False)
        saved = {'result': self.provider.probe()}
        self.assertTrue(saved['result']['ready'])
        self.provider.bind_registered(saved['result'])
        self.assertTrue(self.provider.bound_alive())
        return saved['result']

    def test_compute_state_active_for_both_configured_worker_counts(self):
        for workers in (0, 1):
            config = copy.deepcopy(self.c)
            config['trino']['workers'] = workers
            harness = Harness(config)
            provider = p.Provider(config, command=harness.command, http=harness.http,
                sdk=harness.sdk, popen=harness.popen)
            with self.subTest(workers=workers):
                self.assertFalse(provider._opened)
                self.assertEqual(provider.compute_state(), 'ACTIVE')
                deployments = [argv for argv, _ in harness.commands if 'deployment' in argv]
                self.assertEqual([argv[argv.index('deployment')+1] for argv in deployments],
                    ['trino-local', 'trino-local-worker'])
                self.assertEqual(harness.http_calls, [])
                self.assertEqual(harness.sdk_calls, [])

    def test_compute_state_stopped_requires_current_zero_actual_counts(self):
        resource = self.h.resource
        for omitted in (False, True):
            def stopped(kind, name):
                result = resource(kind, name)
                if kind == 'deployment':
                    result['spec']['replicas'] = 0
                    result['status'] = {'observedGeneration': 2}
                    if not omitted:
                        result['status'].update(readyReplicas=0, availableReplicas=0,
                            updatedReplicas=0, replicas=0)
                return result
            with self.subTest(omitted=omitted), patch.object(self.h, 'resource', side_effect=stopped):
                self.assertEqual(self.provider.compute_state(), 'STOPPED')

    def test_compute_state_transitioning_for_scaling_stale_or_partial_readiness(self):
        resource = self.h.resource
        cases = [('spec', 'replicas', 2), ('status', 'readyReplicas', 0),
            ('status', 'availableReplicas', 0), ('status', 'updatedReplicas', 0),
            ('status', 'replicas', 0), ('status', 'observedGeneration', 1),
            ('status', 'observedGeneration', 3)]
        for name in ('trino-local', 'trino-local-worker'):
            for section, field, value in cases:
                def changing(kind, observed_name):
                    result = resource(kind, observed_name)
                    if kind == 'deployment' and observed_name == name:
                        # Zero is healthy for the configured worker=0 baseline.
                        result[section][field] = (1 if name == 'trino-local-worker' and
                            section == 'status' and value == 0 else value)
                    return result
                with self.subTest(name=name, field=field, value=value), \
                        patch.object(self.h, 'resource', side_effect=changing):
                    self.assertEqual(self.provider.compute_state(), 'TRANSITIONING')
        # A scaled-down desired state is not stopped while an old replica remains.
        def draining(kind, name):
            result = resource(kind, name)
            if kind == 'deployment':
                result['spec']['replicas'] = 0
                result['status'].update(readyReplicas=0, availableReplicas=0, updatedReplicas=0,
                    replicas=1 if name == 'trino-local' else 0)
            return result
        with patch.object(self.h, 'resource', side_effect=draining):
            self.assertEqual(self.provider.compute_state(), 'TRANSITIONING')

    def test_compute_state_rejects_malformed_generation_desired_and_status_counts(self):
        resource = self.h.resource
        fields = [('metadata', 'generation'), ('spec', 'replicas'), ('status', 'observedGeneration')]
        fields += [('status', field) for field in
            ('readyReplicas', 'availableReplicas', 'updatedReplicas', 'replicas')]
        for name in ('trino-local', 'trino-local-worker'):
            for section, field in fields:
                values = (None, True, False, -1, '0', 0.0, [], {})
                if field == 'generation':
                    values += (0,)
                for value in values:
                    def malformed(kind, observed_name):
                        result = resource(kind, observed_name)
                        if kind == 'deployment' and observed_name == name:
                            result[section][field] = value
                        return result
                    with self.subTest(name=name, field=field, value=value), \
                            patch.object(self.h, 'resource', side_effect=malformed):
                        with self.assertRaises(p.Rejected) as caught:
                            self.provider.compute_state()
                        self.assertEqual(caught.exception.code, 'FOUNDATION_COMPUTE_STATE_INVALID')

    def test_compute_state_metadata_only_unopened_and_with_dead_port_forwards(self):
        for bound in (False, True):
            if bound:
                self.bind()
                for child in self.h.children:
                    child.returncode = 1
            self.h.commands.clear()
            with self.subTest(bound=bound), \
                    patch.object(self.provider, 'bound_alive', side_effect=AssertionError('local gate')), \
                    patch.object(self.provider, '_gate', side_effect=AssertionError('open gate')), \
                    patch.object(self.provider, 'probe', side_effect=AssertionError('probe')), \
                    patch.object(self.provider, '_compute', side_effect=AssertionError('pods')), \
                    patch.object(self.provider, '_exec', side_effect=AssertionError('exec')), \
                    patch.object(self.provider, '_sql', side_effect=AssertionError('SQL')), \
                    patch.object(self.provider, '_sdk_factory', side_effect=AssertionError('SDK')), \
                    patch.object(self.provider, '_http_transport', side_effect=AssertionError('HTTP')), \
                    patch.object(f, 'private_read', side_effect=AssertionError('private file')):
                self.assertEqual(self.provider.compute_state(), 'ACTIVE')
            for argv, _ in self.h.commands:
                args = argv[argv.index('--request-timeout=15s')+1:]
                self.assertTrue(args[:2] == ['config', 'view'] or
                    args[:2] in (['get', 'node'], ['get', 'namespace'], ['get', 'deployment']))

    def test_compute_state_preserves_stable_identity_and_trust_fences(self):
        self.bind()
        resource = self.h.resource
        for kind, name in [('namespace', self.c['kube']['namespace']),
                ('deployment', 'trino-local'), ('deployment', 'trino-local-worker')]:
            for foreign in (False, True):
                def changed(observed_kind, observed_name):
                    result = resource(observed_kind, observed_name)
                    if (observed_kind, observed_name) == (kind, name):
                        if foreign:
                            result['metadata']['labels'][f.LABEL] = 'foreign'
                        else:
                            result['metadata']['uid'] = 'replacement'
                    return result
                with self.subTest(kind=kind, name=name, foreign=foreign), \
                        patch.object(self.h, 'resource', side_effect=changed):
                    with self.assertRaises(p.Rejected):
                        self.provider.compute_state()
        self.h.node_uid = 'replacement-node'
        with self.assertRaises(p.Rejected) as caught:
            self.provider.compute_state()
        self.assertEqual(caught.exception.code, 'FOUNDATION_TRUST_MISMATCH')

    def test_compute_state_masks_malformed_response_and_transport_errors(self):
        for failure in (TimeoutError('private details'), ValueError('private details')):
            with self.subTest(failure=failure), patch.object(self.provider, '_command', side_effect=failure):
                with self.assertRaises(p.Rejected) as caught:
                    self.provider.compute_state()
                self.assertEqual(str(caught.exception), 'PROVIDER_OBSERVATION_FAILED')
        resource = self.h.resource
        for field, value in [('status', None), ('spec', []), ('metadata', None)]:
            def malformed(kind, name):
                result = resource(kind, name)
                if kind == 'deployment':
                    result[field] = value
                return result
            with self.subTest(field=field), patch.object(self.h, 'resource', side_effect=malformed):
                with self.assertRaises(p.Rejected) as caught:
                    self.provider.compute_state()
                self.assertNotIn('private details', str(caught.exception))

    def test_live_health_stopped_and_transitioning_raise_before_binding_and_services(self):
        resource = self.h.resource
        for state, code in [('STOPPED', 'WAREHOUSE_START_REQUIRED'),
                ('TRANSITIONING', 'FOUNDATION_NOT_READY')]:
            def current(kind, name):
                result = resource(kind, name)
                if kind == 'deployment':
                    result['spec']['replicas'] = 0
                    result['status'] = {'observedGeneration': 2 if state == 'STOPPED' else 1}
                return result
            with self.subTest(state=state), patch.object(self.h, 'resource', side_effect=current), \
                    patch.object(self.provider, 'bound_alive', side_effect=AssertionError('bound')), \
                    patch.object(self.provider, '_gate', side_effect=AssertionError('gate')), \
                    patch.object(self.provider, '_sql', side_effect=AssertionError('SQL')), \
                    patch.object(self.provider, '_sdk_factory', side_effect=AssertionError('SDK')), \
                    patch.object(self.provider, '_exec', side_effect=AssertionError('exec')):
                with self.assertRaises(p.Rejected) as caught:
                    self.provider.live_health()
                self.assertEqual(caught.exception.code, code)

    def test_binding_rejects_extra_expected_stable_keys(self):
        valid = self.bind()
        for key in ('namespace/extra', 'service/extra', 'deployment/extra', 'pvc/extra',
                'pv/extra', 'configmap/extra'):
            invalid = copy.deepcopy(valid)
            invalid['incarnation'][key] = 'extra-uid'
            with self.subTest(key=key), patch.object(self.provider, 'probe', return_value=valid) as probe:
                with self.assertRaises(p.Rejected) as caught:
                    self.provider.bind_registered(invalid)
                self.assertEqual(caught.exception.code, 'FOUNDATION_BINDING_MISMATCH')
                self.assertFalse(self.provider._startup_verified)
                probe.assert_called_once_with()

    def test_binding_rejects_incomplete_positive_actual_deployment_and_pv_pins(self):
        valid = self.bind()
        keys = [key for key in valid['incarnation'] if key.startswith(('deployment/', 'pv/'))]
        for key in keys:
            incomplete = copy.deepcopy(valid)
            del incomplete['incarnation'][key]
            # Both sets match: rejection must come from mandatory proof coverage.
            with self.subTest(key=key), patch.object(self.provider, 'probe', return_value=incomplete) as probe:
                with self.assertRaises(p.Rejected):
                    self.provider.bind_registered(copy.deepcopy(incomplete))
                self.assertFalse(self.provider._startup_verified)
                probe.assert_called_once_with()
        extra = copy.deepcopy(valid)
        extra['incarnation']['pv/fifth-volume'] = 'fifth-uid'
        with patch.object(self.provider, 'probe', return_value=extra):
            with self.assertRaises(p.Rejected):
                self.provider.bind_registered(copy.deepcopy(extra))
            self.assertFalse(self.provider._startup_verified)

    def test_binding_uses_own_full_probe_and_does_not_modify_receipt(self):
        self.open(effects=False)
        receipt = {'result': self.provider.probe()}
        original = copy.deepcopy(receipt)
        with patch.object(self.provider, 'probe', wraps=self.provider.probe) as probe:
            self.assertIsNone(self.provider.bind_registered(receipt['result']))
            probe.assert_called_once_with()
        self.assertEqual(receipt, original)
        self.assertTrue(self.provider.bound_alive())
        self.assertNotIn('service/owned-postgres', self.provider._bound_pins)
        self.assertNotIn('service/trino-opa', self.provider._bound_pins)

    def test_binding_requires_every_actual_stable_pin(self):
        valid = self.bind()
        for key in self.provider._stable_pins(valid['incarnation']):
            for replacement in (None, '', ' ', 7, True, {}, 'replacement-uid'):
                invalid = copy.deepcopy(valid)
                if replacement is None:
                    del invalid['incarnation'][key]
                else:
                    invalid['incarnation'][key] = replacement
                with self.subTest(key=key, replacement=replacement), \
                        patch.object(self.provider, 'probe', wraps=self.provider.probe) as probe:
                    with self.assertRaises(p.Rejected):
                        self.provider.bind_registered(invalid)
                    probe.assert_called_once_with()
                    self.assertFalse(self.provider.bound_alive())
                    self.assertFalse(self.provider._startup_verified)

    def test_binding_requires_positive_well_typed_identity_node_and_spi(self):
        valid = self.bind()
        invalid = [None, [], {'result': valid}]
        for field, values in {'ready': [False, 1, None], 'revision': [True, '3', 4, None],
                'foundation_id': [None, 'other'], 'spi_sha256': [None, '', 'a'*63, 'A'*64, 'b'*64],
                'incarnation': [None, [], {}]}.items():
            for value in values:
                bad = copy.deepcopy(valid)
                bad[field] = value
                invalid.append(bad)
        for nodes in (None, [], {}, {'lab-node': 'changed'}, {'lab-node': True},
                {'lab-node': 'node-uid', 'extra': 'extra'}):
            bad = copy.deepcopy(valid)
            bad['incarnation']['node'] = nodes
            invalid.append(bad)
        for bad in invalid:
            with self.subTest(bad=bad), patch.object(self.provider, 'probe', return_value=valid) as probe:
                with self.assertRaises(p.Rejected):
                    self.provider.bind_registered(bad)
                probe.assert_called_once_with()
                self.assertFalse(self.provider._startup_verified)

    def test_binding_actual_unhealthy_or_malformed_cannot_be_overridden(self):
        valid = self.bind()
        actuals = [None, [], {}, {**valid, 'ready': False}, {**valid, 'ready': 1}]
        for key in self.provider._stable_pins(valid['incarnation']):
            bad = copy.deepcopy(valid)
            bad['incarnation'][key] = ''
            actuals.append(bad)
        for actual in actuals:
            with self.subTest(actual=actual), patch.object(self.provider, 'probe', return_value=actual):
                with self.assertRaises(p.Rejected):
                    self.provider.bind_registered(valid)
                self.assertFalse(self.provider._startup_verified)
        with patch.object(self.provider, 'probe', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                self.provider.bind_registered(valid)
            self.assertFalse(self.provider._startup_verified)

    def test_binding_ignores_ephemeral_and_publication_evidence(self):
        valid = self.bind()
        valid['incarnation'].update({'pod/trino-local-pod': 'old-pod',
            'replicaset/trino-local-rs': 'old-rs', 'compute': ['old-pod'],
            'images': {'old-pod': 'old-image'}, 'tunnels': {'old': 'old-port'}})
        valid['hashes'] = {'old-publication': 'different'}
        self.h.pod_uid = 'replacement-pod'
        resource = self.h.resource
        def rotated(kind, name):
            result = resource(kind, name)
            if kind == 'pod':
                result['metadata']['ownerReferences'][0]['uid'] = 'new-rs'
            elif kind == 'replicaset':
                result['metadata']['uid'] = 'new-rs'
            return result
        with patch.object(self.h, 'resource', side_effect=rotated):
            self.provider.bind_registered(valid)
            self.assertTrue(self.provider.live_health()['ready'])
        self.assertFalse(any(key.startswith(('pod/', 'replicaset/')) for key in self.provider._incarnation))

    def test_bound_alive_is_local_and_close_reopen_requires_binding(self):
        self.bind()
        with patch.object(self.provider, 'probe', side_effect=AssertionError('probe')), \
                patch.object(self.provider, '_command', side_effect=AssertionError('command')), \
                patch.object(self.provider, '_http_transport', side_effect=AssertionError('http')), \
                patch.object(self.provider, '_sdk_factory', side_effect=AssertionError('sdk')), \
                patch.object(f, 'private_read', side_effect=AssertionError('private read')), \
                patch.object(Path, 'read_bytes', side_effect=AssertionError('file read')):
            for _ in range(50):
                self.assertTrue(self.provider.bound_alive())
            for child in self.h.children:
                child.returncode = 1
                self.assertFalse(self.provider.bound_alive())
                child.returncode = None
        self.provider.close()
        self.assertFalse(self.provider.bound_alive())
        self.provider.open()
        self.assertFalse(self.provider.bound_alive())

    def test_live_health_has_exact_shape_and_no_full_or_java_proof(self):
        self.bind()
        self.h.commands.clear()
        self.h.http_calls.clear()
        self.h.sdk_calls.clear()
        read_bytes = Path.read_bytes
        def without_jar(path):
            if path == Path(self.c['artifacts']['jar']):
                raise AssertionError('jar read')
            return read_bytes(path)
        with patch.object(self.provider, 'probe', side_effect=AssertionError('full probe')), \
                patch.object(self.provider, '_exec', side_effect=AssertionError('Java exec')), \
                patch.object(Path, 'read_bytes', without_jar):
            self.assertEqual(self.provider.live_health(), {'ready': True,
                'foundation_id': self.c['foundation_id'], 'revision': self.c['revision']})
        self.assertFalse(any('exec' in argv for argv, _ in self.h.commands))
        self.assertEqual([kwargs['data'].decode() for method, url, kwargs in self.h.http_calls
            if url.endswith('/v1/statement')], [PROBE_SQL])
        self.assertTrue(any(url.endswith('/principals/root') for _, url, _ in self.h.http_calls))
        self.assertIn(('list_roles', {'MaxItems': 1}), self.h.sdk_calls)
        self.assertIn(('head_bucket', {'Bucket': self.c['storage']['bucket']}), self.h.sdk_calls)

    def test_live_health_requires_binding_without_observation(self):
        self.open(effects=False)
        with patch.object(self.provider, '_gate', side_effect=AssertionError('observation')):
            self.assertFalse(self.provider.live_health()['ready'])
            with self.assertRaises(p.Rejected):
                self.provider.publication_hashes(self.db)

    def test_live_health_rejects_replacement_of_each_stable_resource(self):
        self.bind()
        resource = self.h.resource
        for key in self.provider._bound_pins:
            if '/' not in key:
                continue
            kind, name = key.split('/', 1)
            def replaced(observed_kind, observed_name):
                result = resource(observed_kind, observed_name)
                if (observed_kind, observed_name) == (kind, name):
                    result['metadata']['uid'] = 'replacement'
                return result
            with self.subTest(key=key), patch.object(self.h, 'resource', side_effect=replaced):
                if key in ('namespace/'+self.c['kube']['namespace'],
                        'deployment/trino-local', 'deployment/trino-local-worker'):
                    with self.assertRaises(p.Rejected):
                        self.provider.live_health()
                else:
                    self.assertFalse(self.provider.live_health()['ready'])

    def test_live_health_pvs_need_only_uid_and_no_foundation_labels(self):
        self.bind()
        resource = self.h.resource
        def metadata_only(kind, name):
            result = resource(kind, name)
            if kind == 'pv':
                return {'metadata': {'uid': result['metadata']['uid']}}
            if kind == 'pvc':
                result.pop('spec')
                result.pop('status')
            if kind == 'configmap':
                result.pop('binaryData')
                result.pop('data')
            return result
        with patch.object(self.h, 'resource', side_effect=metadata_only):
            self.assertTrue(self.provider.live_health()['ready'])

    def test_live_health_rejects_native_image_mount_readiness_and_owner(self):
        self.bind()
        resource = self.h.resource
        def bad_image(result):
            result['spec']['containers'][0]['image'] = 'bad-image'
        def bad_mount(result):
            result['spec']['containers'][0]['volumeMounts'] = []
        def unready(result):
            result['status']['conditions'] = []
        def wrong_owner(result):
            result['metadata']['ownerReferences'][0]['uid'] = 'other-rs'
        for workload, change in [('trino-local', bad_image), ('trino-local', bad_mount),
                ('trino-local', unready), ('trino-local', wrong_owner),
                ('owned-seaweed', bad_image), ('owned-postgres', unready), ('owned-polaris', bad_image)]:
            def broken(kind, name):
                result = resource(kind, name)
                if kind == 'pod' and name == workload+'-pod':
                    change(result)
                return result
            with self.subTest(workload=workload, change=change), patch.object(self.h, 'resource', side_effect=broken):
                self.assertFalse(self.provider.live_health()['ready'])

    def test_live_health_sql_and_service_failures_are_false_without_retry(self):
        self.bind()
        failures = [(PROBE_SQL, [['admin', '483', True, 1]]),
            (PROBE_SQL, [['logical_provisioner', '482', True, 1]]),
            (PROBE_SQL, [['logical_provisioner', '483', True, 2]]),
            (PROBE_SQL, [['logical_provisioner', '483', 1, 1]])]
        for statement, rows in failures:
            def http(method, url, **kwargs):
                if url.endswith('/v1/statement') and kwargs['data'].decode() == statement:
                    return 200, json.dumps({'data': rows}).encode()
                return self.h.http(method, url, **kwargs)
            with self.subTest(statement=statement, rows=rows), \
                    patch.object(self.provider, '_http_transport', side_effect=http):
                self.assertFalse(self.provider.live_health()['ready'])
        for boundary in ('_polaris', '_iam', '_storage'):
            with self.subTest(boundary=boundary), patch.object(self.provider, boundary,
                    side_effect=p.Rejected('failed')) as call:
                self.assertFalse(self.provider.live_health()['ready'])
                self.assertEqual(call.call_count, 1)

    def test_publication_hashes_only_gates_and_reads_writer(self):
        self.bind()
        with patch.object(self.provider, '_gate', wraps=self.provider._gate) as gate, \
                patch.object(self.provider, '_exec', return_value=self.h.hashes) as read, \
                patch.object(self.provider, 'probe', side_effect=AssertionError('probe')), \
                patch.object(self.provider, '_sdk_factory', side_effect=AssertionError('sdk')), \
                patch.object(self.provider, '_http_transport', side_effect=AssertionError('http')):
            self.assertEqual(self.provider.publication_hashes(self.db), self.h.hashes)
            gate.assert_called_once_with(self.db)
            read.assert_called_once_with('read')
            with self.assertRaises(p.Rejected):
                self.provider.publication_hashes({**self.db, 'foundation_revision': 4})
            read.assert_called_once_with('read')

    def test_publication_hashes_strict_direct_four_target_map(self):
        self.bind()
        invalid = [None, [], {}, {'hashes': self.h.hashes}, {**self.h.hashes, 'unknown': 'a'*64}]
        for key in self.h.hashes:
            missing = dict(self.h.hashes)
            del missing[key]
            invalid.append(missing)
            for value in ('MISSING', '', 'A'*64, 'a'*63, 'g'*64, None, True, 1, {}):
                invalid.append({**self.h.hashes, key: value})
        for hashes in invalid:
            with self.subTest(hashes=hashes), patch.object(self.provider, '_exec', return_value=hashes) as read:
                with self.assertRaises(p.Rejected):
                    self.provider.publication_hashes(self.db)
                read.assert_called_once_with('read')

    def mock_refresh_clock(self):
        self.elapsed = 0.0
        self.sleeps = []
        def sleep(seconds):
            self.sleeps.append(seconds)
            self.elapsed += seconds
        for name, value in [('monotonic', lambda: self.elapsed), ('sleep', sleep)]:
            mocked = patch.object(p.time, name, side_effect=value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def open(self, *, effects=True):
        if effects:
            # Existing mutation tests explicitly opt into a short operator scope.
            scope = self.provider.effects(lambda key, identity, call: call())
            scope.__enter__()
            self.addCleanup(scope.__exit__, None, None, None)
        return self.provider.open()

    def test_inert_construction_immutable_config_private_path(self):
        self.assertEqual(self.h.commands, [])
        self.c['revision'] = 999
        snapshot = self.provider.config
        snapshot['kube']['context'] = 'attacker'
        self.assertEqual(self.provider.config['revision'], 3)
        self.assertEqual(self.provider.config['kube']['context'], 'lab-context')
        f.save_private(self.root/'settings.json', json.dumps(settings(self.root)))
        other = p.Provider(self.root/'settings.json')
        self.assertEqual(other.config['revision'], 3)

    def test_lifetime_owned_ephemeral_tunnels_and_close(self):
        self.open()
        first = list(self.h.children)
        self.open()
        self.assertEqual(len(self.h.children), 3)
        forwards = [(a, kw) for a, kw in self.h.commands if 'port-forward' in a]
        self.assertEqual({a[-2] for a, _ in forwards}, {'service/owned-seaweed', 'service/owned-polaris', 'service/trino-local'})
        for argv, kwargs in forwards:
            self.assertIn('--address=127.0.0.1', argv)
            self.assertTrue(argv[-1].startswith(':'))
            self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
        self.provider.close()
        self.assertTrue(all(c.terminated for c in first))
        self.assertEqual(self.provider._tunnels, {})

    def test_unrelated_listener_never_adopted_and_failed_open_cleans_children(self):
        def not_forwarding(argv, **kwargs):
            child = Child(9999)
            child.returncode = 1
            self.h.children.append(child)
            return child
        self.provider._popen = not_forwarding
        with self.assertRaises(p.Rejected):
            self.open()
        self.assertFalse(self.provider._opened)
        self.assertEqual(self.h.http_calls, [])

    def test_revision_and_trust_fences_before_every_dispatch(self):
        self.open()
        for key, value in [('generation_id', 'fake'), ('foundation_revision', 4), ('sql_catalog', 'x; DROP CATALOG y')]:
            db = {**self.db, key: value}
            with self.assertRaises(p.Rejected):
                self.provider.create_catalog(db)
        self.assertEqual(self.h.http_calls, [])
        self.h.node_uid = 'replaced'
        with self.assertRaises(p.Rejected):
            self.provider.create_catalog(self.db)
        self.h.node_uid = 'node-uid'
        self.h.ns_uid = 'replaced'
        with self.assertRaises(p.Rejected):
            self.provider.create_catalog(self.db)
        self.assertEqual(self.h.http_calls, [])

    def test_ca_and_api_pins_and_publisher_ownership(self):
        self.h.view['clusters'][0]['cluster']['server'] = 'https://attacker:6443'
        with self.assertRaises(p.Rejected):
            self.open()
        self.h.view['clusters'][0]['cluster']['server'] = self.c['kube']['api_server']
        self.open()
        original = self.h.resource
        def foreign(kind, name):
            result = original(kind, name)
            if kind == 'replicaset':
                result['metadata']['ownerReferences'][0]['uid'] = 'foreign-owner'
            return result
        self.h.resource = foreign
        with self.assertRaises(p.Rejected):
            self.provider.publish_material(self.db, {'credentials': {'ref': 'secret'}})
        self.assertFalse(any('exec' in args for args, _ in self.h.commands))

    def test_native_policy_wire_identity_create_attach_and_complete_readback(self):
        self.open()
        name = 'owned-db-1'
        for arn in ('arn:aws:iam:::policy/'+name, 'arn:aws:iam::000000000000:policy/'+name):
            policy = {'PolicyName': name, 'PolicyId': 'native-policy-id', 'Arn': arn,
                'DefaultVersionId': 'v1'}
            responses = {'create_role': {'Role': {'Arn': 'arn:aws:iam::role/'+name,
                    'RoleName': name, 'RoleId': 'native-role-id'}},
                'get_role': {'Role': {'Arn': 'arn:aws:iam::role/'+name, 'RoleName': name,
                    'RoleId': 'native-role-id', 'AssumeRolePolicyDocument': self.provider._trust_policy()}},
                'create_policy': {'Policy': policy}, 'attach_role_policy': {},
                'list_attached_role_policies': {'AttachedPolicies': [{'PolicyName': name, 'PolicyArn': arn}]},
                'get_policy': {'Policy': policy},
                'get_policy_version': {'PolicyVersion': {'Document': self.provider._policy(self.db)}}}
            self.h.iam_handler = lambda method, params: responses[method]
            self.h.sdk_calls.clear()
            with self.subTest(arn=arn):
                self.assertEqual(self.provider.create_role(self.db),
                    {'role_arn': 'arn:aws:iam::000000000000:role/'+name})
                proof = self.provider.check_role(self.db)
                self.assertTrue(proof['verified'] and proof['complete'])
                for method, params in self.h.sdk_calls:
                    if method in ('attach_role_policy', 'get_policy', 'get_policy_version'):
                        self.assertEqual(params['PolicyArn'], arn)
                    if method == 'get_policy_version':
                        self.assertEqual(params['VersionId'], 'v1')

    def test_policy_identity_rejects_wrong_name_id_and_arn_in_create_and_readback(self):
        self.open()
        name, arn = 'owned-db-1', 'arn:aws:iam:::policy/owned-db-1'
        valid = {'PolicyName': name, 'PolicyId': 'policy-id', 'Arn': arn, 'DefaultVersionId': 'v1'}
        invalid = []
        for field, values in {'PolicyName': [None, '', 'other', '/owned-db-1'],
                'PolicyId': [None, '', ' ', True, 1, {}],
                'Arn': ['arn:aws:iam::policy/'+name, 'arn:aws:iam::999999999999:policy/'+name,
                    'arn:aws-cn:iam:::policy/'+name, 'arn:aws:iam:region::policy/'+name,
                    arn+'/extra', 'arn:aws:iam:::policy/path/'+name, 'arn:aws:iam:::policy/other']}.items():
            missing = dict(valid)
            del missing[field]
            invalid.append(missing)
            invalid.extend({**valid, field: value} for value in values)
        for policy in invalid:
            for readback in (False, True):
                responses = {'create_role': {'Role': {'Arn': 'arn:aws:iam::000000000000:role/'+name}},
                    'create_policy': {'Policy': policy},
                    'get_role': {'Role': {'Arn': 'arn:aws:iam::000000000000:role/'+name,
                        'AssumeRolePolicyDocument': self.provider._trust_policy()}},
                    'list_attached_role_policies': {'AttachedPolicies': [{'PolicyName': name, 'PolicyArn': arn}]},
                    'get_policy': {'Policy': policy}}
                self.h.iam_handler = lambda method, params: responses[method]
                self.h.sdk_calls.clear()
                with self.subTest(policy=policy, readback=readback):
                    with self.assertRaises(p.ProviderError):
                        (self.provider.check_role if readback else self.provider.create_role)(self.db)
                    self.assertFalse(any(method in ('attach_role_policy', 'get_policy_version')
                        for method, _ in self.h.sdk_calls))
        # Individually valid wire ARNs must still agree across list and GetPolicy.
        responses['get_policy']['Policy'] = {**valid, 'Arn': 'arn:aws:iam::000000000000:policy/'+name}
        with self.assertRaises(p.Rejected):
            self.provider.check_role(self.db)

    def test_attached_policy_arn_and_name_are_validated_before_get(self):
        self.open(effects=False)
        name = 'owned-db-1'
        for arn in ('arn:aws:iam::policy/'+name, 'arn:aws:iam::999999999999:policy/'+name,
                'arn:aws-cn:iam:::policy/'+name, 'arn:aws:iam:::policy/other'):
            def iam(method, params):
                if method == 'get_role':
                    return {'Role': {'Arn': 'arn:aws:iam::000000000000:role/'+name}}
                if method == 'list_attached_role_policies':
                    return {'AttachedPolicies': [{'PolicyName': name, 'PolicyArn': arn}]}
                self.fail('invalid listed ARN reached GetPolicy')
            self.h.iam_handler = iam
            with self.subTest(arn=arn), self.assertRaises(p.Rejected):
                self.provider.check_role(self.db)
        self.h.iam_handler = lambda method, params: ({'Role': {'Arn': 'arn:aws:iam::000000000000:role/'+name}}
            if method == 'get_role' else {'AttachedPolicies': [{'PolicyName': 'other',
                'PolicyArn': 'arn:aws:iam:::policy/'+name}]})
        self.assertFalse(self.provider.check_role(self.db)['verified'])

    def test_preexisting_role_and_policy_acks_replay_original_fingerprints_attach_only(self):
        self.open(effects=False)
        # Frozen fixture JSON and hashes for the supported new account, independent
        # of today's constructors; no prior-foundation receipts are migrated.
        trust_json = ('{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", '
            '"Principal": {"AWS": "arn:aws:iam::000000000000:user/polaris-issuer"}, '
            '"Action": "sts:AssumeRole"}]}')
        policy_json = ('{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", '
            '"Action": ["s3:ListBucket"], "Resource": ["arn:aws:s3:::logical-data"], '
            '"Condition": {"StringLike": {"s3:prefix": ["db-1/", "db-1/*"]}}}, '
            '{"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", '
            '"s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"], '
            '"Resource": ["arn:aws:s3:::logical-data/db-1/*"]}]}')
        role = {'Arn': 'arn:aws:iam::role/owned-db-1', 'RoleName': 'owned-db-1', 'RoleId': 'role-id'}
        policy = {'Arn': 'arn:aws:iam:::policy/owned-db-1', 'PolicyName': 'owned-db-1',
            'PolicyId': 'policy-id', 'DefaultVersionId': 'v1'}
        saved = {'af33ac2ce80c3021714fb839de4319ebbea8f37f9690df01b0b437fffedf8869': {'Role': role},
            '10466e813dc355a5b993e803d4083b96cd1eee7b26bb36222612ab162e67b6a2': {'Policy': policy}}
        fingerprints = {'create_role': 'a287c9af07cc79f25a869d4b9dd177faca718c1715f0701d5b312dcaf123de2a',
            'create_policy': '4c429040428038653901334687c3c0ab1b06ac52814e2da451d7433a7e1222c6'}
        events, dispatched = [], []
        def journal(key, identity, call):
            events.append(identity['method'])
            if identity['method'] in fingerprints:
                self.assertEqual(identity['payload_sha256'], fingerprints[identity['method']])
                self.assertEqual(identity['service'], 'iam')
                return copy.deepcopy(saved[key])
            self.assertEqual(identity['method'], 'attach_role_policy')
            return call()
        def iam(method, params):
            dispatched.append((method, params))
            if method == 'get_role':
                return {'Role': dict(role)}
            self.assertEqual(method, 'attach_role_policy')
            self.assertEqual(params, {'RoleName': 'owned-db-1', 'PolicyArn': policy['Arn']})
            return {}
        self.h.iam_handler = iam
        with self.provider.effects(journal), patch.object(self.provider, '_sdk_call', wraps=self.provider._sdk_call) as sdk:
            self.assertEqual(self.provider.create_role(self.db),
                {'role_arn': 'arn:aws:iam::000000000000:role/owned-db-1'})
            self.assertEqual(sdk.call_args_list[0].args, ('iam', 'create_role',
                {'RoleName': 'owned-db-1', 'AssumeRolePolicyDocument': trust_json}))
            self.assertEqual(sdk.call_args_list[2].args, ('iam', 'create_policy',
                {'PolicyName': 'owned-db-1', 'PolicyDocument': policy_json}))
        self.assertEqual(events, ['create_role', 'create_policy', 'attach_role_policy'])
        self.assertEqual([method for method, _ in dispatched], ['get_role', 'attach_role_policy'])

    def test_metadata_get_skips_only_redundant_trust_and_keeps_current_reads(self):
        self.bind()
        self.h.commands.clear()
        with patch.object(self.provider, '_trust', wraps=self.provider._trust) as trust:
            self.assertEqual(self.provider.compute_state(), 'ACTIVE')
            trust.assert_called_once_with()
        self.assertEqual(len(self.h.commands), 5)  # CA/server, node, namespace, two deployments.
        gets = [argv[argv.index('--request-timeout=15s')+1:] for argv, _ in self.h.commands]
        self.assertEqual(sum(args[:2] == ['get', 'deployment'] for args in gets), 2)
        with patch.object(self.provider, '_trust', side_effect=AssertionError('redundant trust')):
            for _ in range(2):
                self.provider._owned('service', 'trino-local')
        self.assertEqual(len(self.h.commands), 7)

    def test_cmd_nonget_and_nonfalse_mutation_always_refresh_trust(self):
        for args, mutation in [(['get', 'deployment', 'trino-local'], True),
                (['get', 'deployment', 'trino-local'], 0), ([], False),
                (['config', 'view'], False), (['unknown'], False),
                (['exec', 'fixed-pod'], False), (['exec', 'fixed-pod'], True)]:
            with self.subTest(args=args, mutation=mutation), \
                    patch.object(self.provider, '_trust', side_effect=p.Rejected('FOUNDATION_TRUST_MISMATCH')) as trust, \
                    patch.object(self.provider, '_raw', side_effect=AssertionError('unguarded dispatch')):
                with self.assertRaises(p.Rejected):
                    self.provider._cmd(args, mutation=mutation)
                trust.assert_called_once_with()

    def test_all_fixed_exec_modes_refresh_trust_immediately_before_dispatch(self):
        self.open()
        for mode in ('read', 'resolve', 'catalog', 'publish'):
            self.h.commands.clear()
            with self.subTest(mode=mode), patch.object(self.provider, '_trust',
                    side_effect=p.Rejected('FOUNDATION_TRUST_MISMATCH')) as trust:
                with self.assertRaises(p.Rejected):
                    self.provider._exec(mode)
                trust.assert_called_once_with()
            # Metadata selection and immediate UID reread still happen, but no exec.
            self.assertTrue(any('pod' in argv for argv, _ in self.h.commands))
            self.assertFalse(any('exec' in argv for argv, _ in self.h.commands))

    def test_public_metadata_entries_recheck_api_ca_node_and_namespace_pins(self):
        self.bind()
        view = copy.deepcopy(self.h.view)
        for changed in ('api', 'ca', 'node', 'namespace'):
            for entry in (self.provider.compute_state, self.provider.open,
                    lambda: self.provider._gate(self.db)):
                self.h.view = copy.deepcopy(view)
                self.h.node_uid, self.h.ns_uid = 'node-uid', 'namespace-uid'
                cluster = self.h.view['clusters'][0]['cluster']
                if changed == 'api':
                    cluster['server'] = 'https://foreign:6443'
                elif changed == 'ca':
                    cluster['certificate-authority-data'] = base64.b64encode(b'foreign-ca').decode()
                elif changed == 'node':
                    self.h.node_uid = 'foreign-node'
                else:
                    self.h.ns_uid = 'foreign-namespace'
                with self.subTest(changed=changed, entry=entry), self.assertRaises(p.Rejected):
                    entry()

    def test_sdk_and_http_leaf_gates_refresh_trust_after_outer_gate(self):
        self.open()
        for entry in (lambda: self.provider.create_role(self.db), lambda: self.provider.create_catalog(self.db)):
            self.h.http_calls.clear()
            self.h.sdk_calls.clear()
            with self.subTest(entry=entry), patch.object(self.provider, '_trust',
                    side_effect=[None, p.Rejected('FOUNDATION_TRUST_MISMATCH')]) as trust:
                with self.assertRaises(p.Rejected):
                    entry()
                self.assertEqual(trust.call_count, 2)
            self.assertEqual(self.h.sdk_calls, [])
            self.assertEqual(self.h.http_calls, [])

    def test_iam_prefix_policy_and_canonical_identity(self):
        self.open()
        self.assertEqual(self.provider.create_role(self.db)['role_arn'], 'arn:aws:iam::000000000000:role/owned-db-1')
        policy = json.loads(next(params for method, params in self.h.sdk_calls if method == 'create_policy')['PolicyDocument'])
        self.assertEqual(policy['Statement'][0]['Condition']['StringLike']['s3:prefix'], ['db-1/', 'db-1/*'])
        self.assertEqual(policy['Statement'][1]['Resource'], ['arn:aws:s3:::logical-data/db-1/*'])
        with self.assertRaises(p.Rejected):
            self.provider._arn(self.db, 'arn:aws:iam::123456789012:role/owned-db-1')
        legacy = {'Arn': 'arn:aws:iam::role/owned-db-1', 'RoleName': 'owned-db-1', 'RoleId': 'real-role-id'}
        self.assertEqual(self.provider._arn(self.db, legacy['Arn'], legacy), 'arn:aws:iam::000000000000:role/owned-db-1')
        with self.assertRaises(p.Rejected):
            self.provider._arn(self.db, legacy['Arn'], {**legacy, 'RoleName': 'other-role'})
        options = self.h.sdk_calls[0][1]
        self.assertTrue(options['endpoint_url'].startswith('http://127.0.0.1:'))
        catalog = self.provider._catalog(self.db)
        self.assertFalse(catalog['storageConfigInfo']['stsUnavailable'])
        self.assertTrue(catalog['storageConfigInfo']['kmsUnavailable'])
        self.assertIn('.svc.cluster.local', catalog['storageConfigInfo']['endpoint'])
        numeric_id = {**self.db, 'database_id': '1234-abcd'}
        self.provider._gate(numeric_id)
        long_id = {**self.db, 'database_id': '1'+'a'*190}
        self.provider._gate(long_id)
        self.assertLessEqual(len(self.provider._role(long_id)), 64)

    def test_consumer_returns_credentials_before_another_api(self):
        self.open()
        result = self.provider.issue_consumer(self.db)
        self.assertEqual(result, {'client_id': 'client', 'client_secret': 'private-secret', 'realm': 'LOGICAL', 'principal_name': 'owned-db-1-consumer'})
        calls = self.h.http_calls
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[-1][1].endswith('/principals'))
        self.assertEqual(calls[-1][0], 'POST')
        self.assertEqual(self.h.sdk_calls, [])
        self.assertFalse(any('exec' in args for args, _ in self.h.commands))

    def test_transport_unknown_known_rejection_and_no_retry(self):
        self.open()
        self.provider._tokens['root'] = 'token'
        def lose(*args, **kwargs):
            raise OSError('RAW PRIVATE TOKEN')
        self.h.http_handler = lose
        with self.assertRaises(p.Unknown) as raised:
            self.provider.issue_consumer(self.db)
        self.assertNotIn('PRIVATE', str(raised.exception))
        self.assertEqual(len(self.h.http_calls), 1)
        self.h.http_handler = lambda *a, **kw: (409, b'raw secret')
        with self.assertRaises(p.Rejected) as raised:
            self.provider.issue_consumer(self.db)
        self.assertFalse(raised.exception.unknown)
        self.assertEqual(len(self.h.http_calls), 2)

    def test_material_whitelist_stale_cas_precedes_any_write(self):
        self.open()
        for bad in ({'credentials': {}, 'path': '/tmp/arbitrary'}, {'credentials': {'../bad': 'value'}},
                {'credentials': {}, 'rules_json': '{}'}, {'credentials': {}, 'expected_hashes': {'evil': 'MISSING'}}):
            with self.assertRaises(p.Rejected):
                self.provider.publish_material(self.db, bad)
        with self.assertRaises(p.StaleCAS):
            self.provider.publish_material(self.db, {'credentials': {}, 'rules_json': '{}', 'expected_hashes': {'rules.json': '0'*64}})
        self.assertFalse(any('exec' in args and args[-1] == 'publish' for args, _ in self.h.commands))

    def test_material_stdin_fixed_target_uid_and_readback(self):
        self.open()
        result = self.provider.publish_material(self.db, {'credentials': {'new_ref': 'secret'}, 'rules_json': '{}',
            'expected_hashes': {'rules.json': self.h.hashes['target.rules.json']}})
        writes = [(args, data) for args, data in self.h.commands if 'exec' in args and args[-1] == 'publish']
        self.assertEqual(len(writes), 1)
        argv, data = writes[0]
        self.assertEqual(argv[-len(p.PUBLISHER)-1:], [*p.PUBLISHER, 'publish'])
        self.assertIn('trino-local-pod', argv)
        self.assertIn('-i', argv)
        self.assertNotIn('credential.new_ref', ' '.join(argv))
        self.assertNotIn('=secret', ' '.join(argv))
        self.assertIn(b'credential.new_ref=secret\n', data)
        self.assertIn(b'rules_json=e30\\=', data)
        self.assertEqual(result, self.h.hashes)
        self.assertTrue(self.provider.check_material(self.db, {'credentials': {'new_ref': 'secret'}})['verified'])
        # Ordinary replacement is allowed; a change after writer selection is not.
        self.h.pod_uid = 'replaced-pod'
        self.provider.publish_material(self.db, {'credentials': {}})
        publisher = self.provider._publisher
        def replaced_after_selection():
            selected = publisher()
            self.h.pod_uid = 'changed-before-dispatch'
            return selected
        before = len([args for args, _ in self.h.commands if 'exec' in args])
        with patch.object(self.provider, '_publisher', side_effect=replaced_after_selection):
            with self.assertRaises(p.Rejected):
                self.provider.publish_material(self.db, {'credentials': {}})
        self.assertEqual(len([args for args, _ in self.h.commands if 'exec' in args]), before)

    def test_publisher_loss_and_jvm_failure_never_false_denial(self):
        self.open()
        def failure(argv, data):
            if argv[-1] == 'read':
                return ('status=OK\n'+''.join(k+'='+v+'\n' for k, v in self.h.hashes.items())).encode()
            return subprocess.CompletedProcess(argv, 1, stdout=b'', stderr=b'RAW JVM FAILURE')
        self.h.exec_handler = failure
        with self.assertRaises(p.Unknown):
            self.provider.publish_material(self.db, {'credentials': {'ref': 'secret'}})
        self.h.exec_handler = lambda argv, data: subprocess.CompletedProcess(argv, 23, stdout=b'', stderr=b'LOGICAL_SECRETS_DENIED\n')
        with self.assertRaises(p.Rejected) as raised:
            self.provider._exec('publish', b'credential.ref=changed\n')
        self.assertEqual(raised.exception.code, 'MATERIAL_PUBLICATION_DENIED')

    def test_attach_uses_secret_ref_native_vending_not_static_key(self):
        self.open()
        with patch.object(self.provider, '_sql', return_value=[]) as sql:
            self.provider.attach_catalog(self.db, 'consumer_ref')
            statement, identity = sql.call_args.args
        self.assertIn('${file:consumer_ref}', statement)
        self.assertIn('vended-credentials-enabled', statement)
        self.assertIn('.svc.cluster.local', statement)
        self.assertNotIn('admin-secret', statement)
        self.assertNotIn('aws-access-key', statement)
        self.assertEqual(identity['username'], 'logical_provisioner')

    def test_verify_real_identity_never_falls_back_and_negative_is_real_denial(self):
        self.mock_refresh_clock()
        self.open()
        identity = {'principal_id': 'principal1', 'database_id': 'db-1', 'selected_role': 'data_engineer', 'grant_epoch': 4,
            'policy_revision': 5, 'username': 'actual_user', 'password': 'actual_password', 'credential_ref': 'principal_ref'}
        def sql(statement, supplied):
            self.assertIs(supplied, identity)
            if 'current_user' in statement:
                return [['actual_user']]
            if 'SHOW SCHEMAS' in statement:
                return [[s] for s in p.SCHEMAS]
            raise p.Rejected('ENGINE_ACCESS_DENIED')
        with patch.object(self.provider, '_sql', side_effect=sql), patch.object(self.provider, '_verify_prefix', return_value={'prefix_positive': True, 'prefix_negative': True}):
            proof = self.provider.verify_target(self.db, identity)
            self.assertTrue(proof['verified'])
            self.assertEqual(proof['grant_epoch'], 4)
        with patch.object(self.provider, '_sql', side_effect=p.Unknown()):
            with self.assertRaises(p.Unknown):
                self.provider.verify_target(self.db, identity)
        with patch.object(self.provider, '_sql') as sql_mock:
            with self.assertRaises(p.Rejected):
                self.provider.verify_target(self.db, {**identity, 'username': 'logical_provisioner'})
            sql_mock.assert_not_called()

    def test_prefix_marker_uses_db_role_and_real_denial_no_retry(self):
        self.open()
        calls = []
        class AccessDenied(Exception):
            response = {'ResponseMetadata': {'HTTPStatusCode': 403}, 'Error': {'Code': 'AccessDenied'}}
        class SDK:
            marker = None
            def assume_role(self, **params):
                calls.append(('assume_role', params))
                return {'Credentials': {'AccessKeyId': 'temporary', 'SecretAccessKey': 'temporary-secret', 'SessionToken': 'temporary-token'}}
            def put_object(self, **params):
                calls.append(('put', params))
                self.marker = params['Body']
            def get_object(self, **params):
                calls.append(('get', params))
                if params['Key'].startswith('_logical_denied/'):
                    raise AccessDenied()
                return {'Body': io.BytesIO(self.marker)}
        client = SDK()
        def sdk(service, **options):
            self.assertNotEqual(options['aws_access_key_id'], 'admin')
            if service == 's3':
                self.assertEqual(options['aws_session_token'], 'temporary-token')
            return client
        self.provider._sdk_factory = sdk
        entries = []
        with self.provider.effects(lambda key, identity, call: (entries.append(identity), call())[1]):
            proof = self.provider._verify_prefix(self.db)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['method'], 'put_object')
        self.assertEqual(entries[0]['target'], 'logical-data/db-1/.shirokuma/creation-marker')
        self.assertNotIn('temporary-secret', json.dumps(entries))
        self.assertTrue(proof['prefix_positive'] and proof['prefix_negative'])
        self.assertEqual(calls[0][1]['RoleArn'], 'arn:aws:iam::000000000000:role/owned-db-1')
        self.assertEqual(calls[1][1]['Key'], 'db-1/.shirokuma/creation-marker')
        with patch.object(client, 'put_object', side_effect=OSError('lost response')) as mutation:
            with self.assertRaises(p.Unknown):
                self.provider._verify_prefix(self.db)
            self.assertEqual(mutation.call_count, 1)

    def test_provider_rejects_old_and_foreign_accounts_before_sdk_or_commands(self):
        for account in ('123456789012', '111111111111'):
            config = copy.deepcopy(self.c)
            config['storage']['account_id'] = account
            filename = self.root/('unsupported-'+account+'.json')
            f.save_private(filename, json.dumps(config))
            for supplied in (config, filename):
                with self.subTest(account=account, supplied=type(supplied)), \
                        patch.object(p, '_sdk', side_effect=AssertionError('SDK')), \
                        patch.object(p, '_command', side_effect=AssertionError('command')), \
                        self.assertRaises(f.ConfigError):
                    p.Provider(supplied)
            self.assertEqual(config['storage']['account_id'], account)
            self.assertEqual(json.loads(filename.read_text())['storage']['account_id'], account)

    def test_full_probe_current_issuer_exact_identity_uses_explicit_credentials_no_effects(self):
        self.open(effects=False)
        sts = Mock()
        sts.get_caller_identity.return_value = {'Account': '000000000000',
            'Arn': 'arn:aws:iam::000000000000:user/polaris-issuer'}
        def sdk(service, **kwargs):
            if service == 'sts':
                self.assertEqual(kwargs, {'endpoint_url': self.provider._tunnels['owned-seaweed'],
                    'region_name': self.c['storage']['region'], 'aws_access_key_id': 'issuer',
                    'aws_secret_access_key': 'issuer-secret'})
                return sts
            return self.h.sdk(service, **kwargs)
        with patch.object(self.provider, '_sdk_factory', side_effect=sdk), \
                patch.object(self.provider, '_effect', side_effect=AssertionError('business effect')):
            for _ in range(2):
                proof = self.provider.probe()
                self.assertTrue(proof['ready'])
                for private in ('issuer-secret', 'admin-secret', 'aws_access_key_id', 'get_caller_identity',
                        'arn:aws:iam::000000000000:user/polaris-issuer'):
                    self.assertNotIn(private, json.dumps(proof))
        self.assertEqual(sts.get_caller_identity.call_args_list, [unittest.mock.call(), unittest.mock.call()])

    def test_full_probe_rejects_wrong_or_malformed_issuer_identity(self):
        self.open(effects=False)
        good = {'Account': '000000000000', 'Arn': 'arn:aws:iam::000000000000:user/polaris-issuer'}
        invalid = [None, [], {}, {'Account': good['Account']}, {'Arn': good['Arn']}]
        invalid += [{**good, 'Account': account} for account in ('123456789012', '111111111111', 0, None)]
        invalid += [{**good, 'Arn': arn} for arn in ('arn:aws:iam::123456789012:user/polaris-issuer',
            'arn:aws:iam::000000000000:user/other', 'arn:aws:iam::000000000000:root',
            'arn:aws-cn:iam::000000000000:user/polaris-issuer', None)]
        for identity in invalid:
            sts = Mock()
            sts.get_caller_identity.return_value = identity
            with self.subTest(identity=identity), patch.object(self.provider, '_sdk_factory', return_value=sts), \
                    patch.object(self.provider, '_effect', side_effect=AssertionError('effect')):
                self.assertFalse(self.provider.probe()['ready'])
                sts.get_caller_identity.assert_called_once_with()

    def test_full_probe_issuer_denial_transport_and_sdk_failures_are_closed_without_retry(self):
        self.open(effects=False)
        class Denied(Exception):
            def __init__(self, status):
                self.response = {'ResponseMetadata': {'HTTPStatusCode': status}}
                super().__init__('issuer-secret')
        for failure in (Denied(401), Denied(403), TimeoutError('issuer-secret'), OSError('issuer-secret')):
            for construction in (False, True):
                sts = Mock()
                sts.get_caller_identity.side_effect = failure
                with self.subTest(failure=failure, construction=construction), \
                        patch.object(self.provider, '_sdk_factory',
                            **({'side_effect': failure} if construction else {'return_value': sts})) as factory, \
                        patch.object(self.provider, '_effect', side_effect=AssertionError('effect')):
                    proof = self.provider.probe()
                    self.assertFalse(proof['ready'])
                    self.assertNotIn('issuer-secret', json.dumps(proof))
                    factory.assert_called_once()
                    self.assertEqual(sts.get_caller_identity.call_count, 0 if construction else 1)

    def test_issuer_observation_refreshes_gate_and_owned_service_before_credentials_and_sdk(self):
        self.open(effects=False)
        for boundary in ('_gate', '_owned'):
            with self.subTest(boundary=boundary), patch.object(self.provider, boundary,
                    side_effect=p.Rejected('FOUNDATION_TRUST_MISMATCH')), \
                    patch.object(self.provider, '_private') as private, \
                    patch.object(self.provider, '_sdk_factory') as factory:
                with self.assertRaises(p.Rejected):
                    self.provider._issuer_identity()
                private.assert_not_called()
                factory.assert_not_called()
        with patch.object(self.provider, '_gate', wraps=self.provider._gate) as gate, \
                patch.object(self.provider, '_owned', wraps=self.provider._owned) as owned, \
                patch.object(self.provider, '_private', wraps=self.provider._private) as private:
            self.provider._issuer_identity()
            self.provider._issuer_identity()
            self.assertEqual(gate.call_count, 2)
            self.assertEqual(owned.call_args_list, [unittest.mock.call('service', 'owned-seaweed')]*2)
            self.assertEqual(private.call_args_list, [unittest.mock.call('storage', 'issuer_credentials_file')]*2)
        resource = self.h.resource
        def foreign(kind, name):
            result = resource(kind, name)
            if kind == 'service':
                result['metadata']['labels'][f.LABEL] = 'foreign'
            return result
        with patch.object(self.h, 'resource', side_effect=foreign), \
                patch.object(self.provider, '_sdk_factory') as factory:
            self.assertFalse(self.provider.probe()['ready'])
            factory.assert_not_called()

    def test_issuer_sts_client_uses_existing_no_retry_configuration(self):
        with patch('boto3.client') as client:
            p._sdk('sts', endpoint_url='http://127.0.0.1:23456', region_name='us-east-1',
                aws_access_key_id='issuer', aws_secret_access_key='fixture-secret')
            config = client.call_args.kwargs['config']
            self.assertEqual(config.retries['total_max_attempts'], 1)
            self.assertEqual(config.proxies, {})
            self.assertEqual(client.call_args.kwargs['aws_access_key_id'], 'issuer')

    def test_cheap_compute_and_live_health_do_not_observe_issuer(self):
        self.bind()
        with patch.object(self.provider, '_issuer_identity', side_effect=AssertionError('issuer observation')):
            self.assertTrue(self.provider.bound_alive())
            self.assertEqual(self.provider.compute_state(), 'ACTIVE')
            self.assertTrue(self.provider.live_health()['ready'])

    def test_probe_health_starting_false_and_safe_private_incarnation(self):
        self.open()
        proof = self.provider.probe()
        self.assertTrue(proof['ready'])
        self.assertEqual(proof['incarnation']['compute'], ['pod-uid'])
        self.assertEqual(set(proof['incarnation']['tunnels']), {'owned-seaweed', 'owned-polaris', 'trino-local'})
        self.assertTrue(proof['incarnation']['tunnels']['trino-local']['endpoint'].startswith('https://127.0.0.1:'))
        for value in ('admin-secret', 'provisioner-secret', 'private-token', 'root-secret'):
            self.assertNotIn(value, json.dumps(proof))
        original = self.h.http
        def starting(method, url, **kwargs):
            if url.endswith('/v1/info'):
                return 200, b'{"starting":true}'
            return original(method, url, **kwargs)
        self.provider._http_transport = starting
        self.assertFalse(self.provider.probe()['ready'])

    def test_probe_sql_gates_use_only_internal_readonly_credentials(self):
        for workers in (0, 1):
            for reverse in (False, True):
                with self.subTest(workers=workers, reverse=reverse):
                    self.c['trino']['workers'] = workers
                    harness = Harness(self.c)
                    calls = []
                    def http(method, url, **kwargs):
                        if url.endswith('/v1/statement'):
                            calls.append((method, kwargs))
                            if kwargs['data'].decode() == PROBE_SQL:
                                rows = [['logical_provisioner', '483', True, 1]] + (
                                    [['logical_provisioner', '483', False, workers]] if workers else [])
                                return 200, json.dumps({'data': rows[::-1] if reverse else rows}).encode()
                        return harness.http(method, url, **kwargs)
                    with p.Provider(self.c, command=harness.command, http=http,
                            sdk=harness.sdk, popen=harness.popen) as provider, \
                            patch.object(provider, '_request', wraps=provider._request) as request, \
                            patch.object(provider, '_effect', side_effect=AssertionError('unexpected business mutation')):
                        proof = provider.probe()
                        self.assertTrue(proof['ready'])
                        self.assertIsNone(provider._effects.get())
                        self.assertEqual([kw['data'].decode() for _, kw in calls],
                            [PROBE_SQL])
                        for method, kwargs in calls:
                            self.assertEqual(method, 'POST')
                            self.assertEqual(kwargs['headers']['X-Trino-User'], 'logical_provisioner')
                            self.assertEqual(kwargs['headers']['Authorization'], 'Basic '+base64.b64encode(
                                b'logical_provisioner:provisioner-secret').decode())
                            self.assertEqual(kwargs['ca_file'], self.c['trino']['ca_file'])
                        sql_calls = [call for call in request.call_args_list if call.args[2] == '/v1/statement']
                        self.assertEqual(len(sql_calls), 1)
                        for call in sql_calls:
                            self.assertIs(call.kwargs['business'], False)
                            self.assertIs(call.kwargs['journal'], False)
                        self.assertTrue(all(method in ('iam', 's3', 'sts', 'get_caller_identity', 'list_roles', 'head_bucket')
                            for method, _ in harness.sdk_calls))
                        self.assertTrue(all(argv[-1] in ('read',) for argv, _ in harness.commands if 'exec' in argv))
                        self.assertEqual(set(proof['readiness']), {'opa', 'storage', 'polaris', 'trino', 'runtime_files', 'spi'})
                        for private in ('provisioner-secret', 'admin-secret', 'root-secret', 'private-token',
                                'Authorization', PROBE_SQL):
                            self.assertNotIn(private, json.dumps(proof))

    def test_probe_sql_identity_and_version_are_strict(self):
        self.open(effects=False)
        invalid = [[], ['logical_provisioner'], [['logical_provisioner']],
            [['logical_provisioner', '483']], [['logical_provisioner', '483', True]],
            [['logical_provisioner', '483', True, 1, 'extra']],
            [{'user': 'logical_provisioner', 'version': '483', 'coordinator': True, 'active_nodes': 1}],
            [None]]
        invalid += [[[user, '483', True, 1]] for user in ('admin', 'actual_user', '', None, True, 1)]
        invalid += [[['logical_provisioner', version, True, 1]]
            for version in ('482', '483.0', '', 483, 483.0, True, None)]
        for rows in invalid:
            calls = []
            def http(method, url, **kwargs):
                if url.endswith('/v1/statement'):
                    calls.append(kwargs['data'].decode())
                    return 200, json.dumps({'data': rows}).encode()
                return self.h.http(method, url, **kwargs)
            with self.subTest(rows=rows), patch.object(self.provider, '_http_transport', side_effect=http):
                proof = self.provider.probe()
                self.assertFalse(proof['ready'])
                self.assertFalse(proof['readiness']['trino'])
                self.assertEqual(calls, [PROBE_SQL])
                self.assertNotIn('provisioner-secret', json.dumps(proof))

    def test_probe_sql_rejects_bad_second_row_identity_and_version(self):
        self.c['trino']['workers'] = 1
        harness = Harness(self.c)
        invalid = [[user, '483', False, 1] for user in ('admin', 'actual_user', '', None, True, 1)]
        invalid += [['logical_provisioner', version, False, 1]
            for version in ('482', '483.0', '', 483, 483.0, True, None)]
        for second in invalid:
            calls = []
            def http(method, url, **kwargs):
                if url.endswith('/v1/statement'):
                    calls.append(kwargs['data'].decode())
                    return 200, json.dumps({'data': [['logical_provisioner', '483', True, 1], second]}).encode()
                return harness.http(method, url, **kwargs)
            with self.subTest(second=second), p.Provider(self.c, command=harness.command,
                    http=http, sdk=harness.sdk, popen=harness.popen) as provider:
                proof = provider.probe()
                self.assertFalse(proof['ready'])
                self.assertFalse(proof['readiness']['trino'])
                self.assertEqual(calls, [PROBE_SQL])
                self.assertNotIn('provisioner-secret', json.dumps(proof))

    def test_sql_health_rejects_nonlist_and_malformed_rows_in_either_position(self):
        identity = {'username': 'logical_provisioner'}
        valid = ['logical_provisioner', '483', True, 1]
        invalid = [None, True, {}, 'logical_provisioner', tuple(valid), [], valid[:1],
            valid[:2], valid[:3], valid + ['extra']]
        with patch.object(self.provider, '_provisioner', return_value=identity):
            for row in invalid:
                for rows in ([row], [valid, row]):
                    with self.subTest(rows=rows), patch.object(self.provider, '_sql', return_value=rows) as sql:
                        with self.assertRaises(p.Rejected) as caught:
                            self.provider._sql_health()
                        self.assertEqual(str(caught.exception), 'FOUNDATION_SQL_IDENTITY_MISMATCH')
                        sql.assert_called_once_with(PROBE_SQL, identity)

    def test_probe_sql_registered_compute_shape_roles_and_counts_are_strict(self):
        for workers in (0, 1):
            self.c['trino']['workers'] = workers
            harness = Harness(self.c)
            invalid = [[], [[False, 1]], [[True, 2]], [[True, 1], [False, 2]],
                [[True, 1], [True, 1]], [[True, 1], [False, 1], [False, 1]],
                [[True, 1], [False, 0]], [[True, 1], [None, 1]],
                [[True, 1], ['worker', 1]], [[True, 1, 'extra']], [True], [{}]]
            invalid += [[[role, 1]] for role in (1, 0, 'true', None)]
            invalid += [[[True, count]] for count in (True, False, 1.0, '1', None, 0, -1)]
            invalid += [[[True, 1], [False, count]] for count in (True, False, 1.0, '1', None, -1)]
            invalid += [[[True, 1], [False, 1]]] if workers == 0 else [[[True, 1]]]
            for compute_rows in invalid:
                rows = [['logical_provisioner', '483', *row] if type(row) is list else row
                    for row in compute_rows]
                calls = []
                def http(method, url, **kwargs):
                    if url.endswith('/v1/statement'):
                        calls.append(kwargs['data'].decode())
                        if calls[-1] == PROBE_SQL:
                            return 200, json.dumps({'data': rows}).encode()
                    return harness.http(method, url, **kwargs)
                with self.subTest(workers=workers, rows=rows), p.Provider(self.c, command=harness.command,
                        http=http, sdk=harness.sdk, popen=harness.popen) as provider:
                    self.assertFalse(provider.probe()['ready'])
                    self.assertEqual(calls, [PROBE_SQL])

    def test_probe_sql_failures_are_safe_false_without_retry(self):
        self.open(effects=False)
        failures = [(401, b'provisioner-secret'), (403, b'provisioner-secret'),
            (200, b'{"error":{"errorName":"ACCESS_DENIED","message":"provisioner-secret"}}'),
            (200, b'{"error":{"errorName":"PERMISSION_DENIED"}}'),
            (200, b'{"error":{"errorName":"GENERIC_INTERNAL_ERROR"}}'),
            (200, b'not json provisioner-secret'), (200, b'[]'), (200, b'{}'),
            (200, b'{"data":null}'), (200, b'{"data":{}}'),
            (200, b'{"error":"provisioner-secret"}'), TimeoutError('provisioner-secret')]
        statement = PROBE_SQL
        for failure in failures:
            calls = []
            def http(method, url, **kwargs):
                if url.endswith('/v1/statement'):
                    calls.append(kwargs['data'].decode())
                    if calls[-1] == statement:
                        if isinstance(failure, Exception):
                            raise failure
                        return failure
                return self.h.http(method, url, **kwargs)
            with self.subTest(statement=statement, failure=failure), \
                    patch.object(self.provider, '_http_transport', side_effect=http), \
                    patch.object(p.time, 'sleep', side_effect=AssertionError('unexpected retry')):
                proof = self.provider.probe()
                self.assertFalse(proof['ready'])
                self.assertEqual(calls, [PROBE_SQL])
                for private in ('provisioner-secret', 'Authorization', PROBE_SQL):
                    self.assertNotIn(private, json.dumps(proof))

    def test_probe_rejects_invalid_private_provisioner_before_sql(self):
        self.open(effects=False)
        valid = {'version': 1, 'username': 'logical_provisioner', 'password': 'provisioner-secret'}
        invalid = [None, [], {**valid, 'extra': 'field'}, {**valid, 'version': True},
            {**valid, 'version': 2}, {**valid, 'version': '1'}, {**valid, 'version': 1.0}]
        invalid += [{**valid, 'username': user} for user in ('admin', 'actual_user', '', None)]
        invalid += [{**valid, 'password': password} for password in ('', None, 123)]
        invalid += [{k: v for k, v in valid.items() if k != missing} for missing in valid]
        for raw in [json.dumps(value) for value in invalid] + ['malformed provisioner-secret']:
            (self.root/'provisioner.json').write_text(raw)
            with self.subTest(raw=raw), patch.object(self.provider, '_sql', wraps=self.provider._sql) as sql:
                proof = self.provider.probe()
                self.assertFalse(proof['ready'])
                sql.assert_not_called()
                self.assertNotIn('provisioner-secret', json.dumps(proof))

    def consumer(self):
        return {'client_id': 'client', 'client_secret': 'secret', 'realm': 'LOGICAL', 'principal_name': 'owned-db-1-consumer'}

    def test_namespace_leaves_ack_order_and_frozen_replay(self):
        self.open(effects=False)
        events, saved = [], {}
        def journal(key, identity, call):
            self.assertEqual(len(saved), len(events))
            before = len(self.h.http_calls)
            ack = call()
            self.assertEqual(len(self.h.http_calls), before+1)
            saved[key] = json.loads(json.dumps(ack))
            events.append((key, identity, len(self.h.http_calls)))
            return ack
        with self.provider.effects(journal):
            result = self.provider.create_namespaces(self.db, self.consumer())
        self.assertEqual(result, {'namespaces': list(p.SCHEMAS)})
        self.assertEqual(len(events), 9)  # two roles, two bindings, one grant, four namespaces
        self.assertEqual(len(saved), 9)
        self.assertEqual([e[1]['method'] for e in events], ['POST', 'POST', 'PUT', 'PUT', 'PUT']+['POST']*4)
        for _, identity, _ in events:
            self.assertEqual(set(identity), {'method', 'service', 'target', 'payload_sha256'})
            self.assertNotIn('"secret"', json.dumps(identity))
        count = len(self.h.http_calls)
        replayed = []
        def replay(key, identity, call):
            replayed.append(key)
            return copy.deepcopy(saved[key])
        with self.provider.effects(replay):
            self.provider.create_namespaces(self.db, self.consumer())
        self.assertEqual(replayed, [key for key, _, _ in events])
        self.assertEqual(len(self.h.http_calls), count)

    def test_issuance_raw_ack_is_checkpoint_before_return_and_no_later_api(self):
        self.open(effects=False)
        snapshots = []
        def journal(key, identity, call):
            raw = call()
            self.assertEqual(raw['credentials']['clientSecret'], 'private-secret')
            raw = json.loads(json.dumps(raw))
            snapshots.append((key, identity, len(self.h.commands), len(self.h.http_calls)))
            return raw
        with self.provider.effects(journal):
            result = self.provider.issue_consumer(self.db)
        self.assertEqual(result['client_secret'], 'private-secret')
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0][2:], (len(self.h.commands), len(self.h.http_calls)))
        count = len(self.h.http_calls)
        with self.provider.effects(lambda key, identity, call: json.loads('{"credentials":{"clientId":"saved","clientSecret":"frozen"}}')):
            self.assertEqual(self.provider.issue_consumer(self.db)['client_id'], 'saved')
        self.assertEqual(len(self.h.http_calls), count)

    def test_iam_leaf_journal_replay_and_checkpoint_failure_stops_chain(self):
        self.open(effects=False)
        saved, events = {}, []
        def journal(key, identity, call):
            raw = call()
            saved[key] = copy.deepcopy(raw)
            events.append(identity['method'])
            return raw
        with self.provider.effects(journal):
            result = self.provider.create_role(self.db)
        self.assertEqual(events, ['create_role', 'create_policy', 'attach_role_policy'])
        self.assertEqual(result['role_arn'], 'arn:aws:iam::000000000000:role/owned-db-1')
        before = sum(method in events for method, _ in self.h.sdk_calls)
        with self.provider.effects(lambda key, identity, call: copy.deepcopy(saved[key])):
            self.assertEqual(self.provider.create_role(self.db), result)
        self.assertEqual(sum(method in events for method, _ in self.h.sdk_calls), before)
        self.h.sdk_calls.clear()
        def lose_checkpoint(key, identity, call):
            call()
            raise OSError('PRIVATE ACK STORE')
        with self.provider.effects(lose_checkpoint), self.assertRaises(OSError) as caught:
            self.provider.create_role(self.db)
        self.assertEqual(str(caught.exception), 'PRIVATE ACK STORE')
        self.assertEqual([method for method, _ in self.h.sdk_calls if method.startswith(('create_', 'attach_'))], ['create_role'])
        with self.provider.effects(lambda *args: (_ for _ in ()).throw(OSError('PRIVATE'))), self.assertRaises(OSError) as caught:
            self.provider.create_role(self.db)
        self.assertEqual(str(caught.exception), 'PRIVATE')

    def test_iam_and_marker_transport_denial_vs_unknown_without_retry(self):
        self.open(effects=False)
        class Denied(Exception):
            response = {'ResponseMetadata': {'HTTPStatusCode': 403}}
        attempts = []
        def fail(method, params):
            attempts.append(method)
            raise Denied('PRIVATE')
        self.h.iam_handler = fail
        with self.provider.effects(lambda key, identity, call: call()), self.assertRaises(p.Rejected) as caught:
            self.provider.create_role(self.db)
        self.assertEqual(caught.exception.code, 'PROVIDER_IAM_REJECTED')
        self.assertEqual(attempts, ['create_role'])
        self.h.iam_handler = lambda *args: (_ for _ in ()).throw(OSError('PRIVATE'))
        with self.provider.effects(lambda key, identity, call: call()), self.assertRaises(p.Unknown):
            self.provider.create_role(self.db)
        # Unknown IAM transports dispatched only once in each invocation.
        self.assertEqual(sum(method == 'create_role' for method, _ in self.h.sdk_calls), 2)
        class Client:
            def assume_role(self, **params):
                return {'Credentials': {'AccessKeyId': 'temporary', 'SecretAccessKey': 'private', 'SessionToken': 'private'}}
            def put_object(self, **params):
                attempts.append('put_object')
                raise Denied('PRIVATE')
        self.provider._sdk_factory = lambda *args, **kwargs: Client()
        with self.provider.effects(lambda key, identity, call: call()), self.assertRaises(p.Rejected) as caught:
            self.provider._verify_prefix(self.db)
        self.assertEqual(caught.exception.code, 'STORAGE_MARKER_REJECTED')
        self.assertEqual(attempts.count('put_object'), 1)
        with self.assertRaises(p.Rejected) as caught:
            self.provider._verify_prefix(self.db)
        self.assertEqual(caught.exception.code, 'EFFECTS_REQUIRED_NO_EFFECTS')
        self.assertEqual(attempts.count('put_object'), 1)

    def test_observers_use_verified_boolean(self):
        self.open(effects=False)
        consumer = self.consumer()
        def polaris(method, path, *args, **kwargs):
            self.assertEqual(method, 'GET')
            if '/namespaces' in path:
                return {'namespaces': [[name] for name in p.SCHEMAS]}
            if path.endswith('/principal-roles') or '/catalog-roles/' in path and not path.endswith('/grants'):
                return {'roles': [{'name': self.provider._role(self.db)}]}
            if path.endswith('/grants'):
                return {'grants': [{'type': 'catalog', 'privilege': 'CATALOG_MANAGE_CONTENT'}]}
            if '/principals/' in path:
                return {'principal': {'name': consumer['principal_name'], 'clientId': consumer['client_id']}}
            return {'catalog': self.provider._catalog(self.db)}
        with patch.object(self.provider, '_polaris', side_effect=polaris):
            for result in (self.provider.check_catalog(self.db), self.provider.check_consumer(self.db, consumer),
                    self.provider.check_namespaces(self.db, consumer)):
                self.assertTrue(result['verified'])
                self.assertTrue(result['complete'])
                for key, value in self.db.items():
                    self.assertEqual(result[key], value)
                self.assertIs(type(result['verified']), bool)
                self.assertIn(result['kind'], ('catalog', 'consumer', 'namespaces'))
                if result['kind'] == 'namespaces':
                    self.assertIs(result['grants_verified'], True)

    def test_callback_cannot_dispatch_one_leaf_twice(self):
        calls = []
        def twice(key, identity, call):
            call()
            return call()
        with self.provider.effects(twice), self.assertRaises(p.Unknown):
            self.provider._effect('POST', 'fixed', '/target', b'', lambda: calls.append('once'))
        self.assertEqual(calls, ['once'])

    def test_publication_callback_receives_raw_ack_and_replay_skips_exec(self):
        self.open(effects=False)
        saved = {}
        def journal(key, identity, call):
            raw = call()
            self.assertIsInstance(raw, dict)
            self.assertEqual(raw, self.h.hashes)
            self.assertNotIn('"secret"', json.dumps(identity))
            saved[key] = json.loads(json.dumps(raw))
            return saved[key]
        material = {'credentials': {'ref': 'secret'}}
        with self.provider.effects(journal):
            self.provider.publish_material(self.db, material)
        self.assertEqual(len(saved), 1)
        before = sum('exec' in argv and argv[-1] == 'publish' for argv, _ in self.h.commands)
        with self.provider.effects(lambda key, identity, call: saved[key]):
            self.provider.publish_material(self.db, material)
        self.assertEqual(sum('exec' in argv and argv[-1] == 'publish' for argv, _ in self.h.commands), before)

    def test_missing_callback_is_known_no_business_effects(self):
        self.open(effects=False)
        operations = [lambda: self.provider.issue_consumer(self.db), lambda: self.provider.create_role(self.db),
            lambda: self.provider.publish_material(self.db, {'credentials': {'ref': 'secret'}}),
            lambda: self.provider.attach_catalog(self.db, 'ref')]
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(p.Rejected) as caught:
                operation()
            self.assertEqual(caught.exception.code, 'EFFECTS_REQUIRED_NO_EFFECTS')
            self.assertFalse(caught.exception.unknown)
        self.assertTrue(all(url.endswith('/oauth/tokens') for _, url, _ in self.h.http_calls))
        self.assertFalse(any(method.startswith(('create_', 'attach_', 'put_')) for method, _ in self.h.sdk_calls))
        self.assertFalse(any('exec' in argv and argv[-1] == 'publish' for argv, _ in self.h.commands))

    def test_effects_nested_reset_exception_and_other_provider_isolation(self):
        self.open(effects=False)
        outer, inner = [], []
        def callback(sink):
            return lambda key, identity, call: (sink.append(identity), call())[1]
        with self.provider.effects(callback(outer)):
            self.provider.create_catalog(self.db)
            with self.assertRaisesRegex(ValueError, 'checkpoint'):
                with self.provider.effects(callback(inner)):
                    self.provider.create_catalog(self.db)
                    raise ValueError('checkpoint')
            self.provider.create_catalog(self.db)
            other = p.Provider(self.c)
            with self.assertRaises(p.Rejected):
                other._effect('POST', 'fixed', '/target', b'', lambda: None)
        self.assertEqual((len(outer), len(inner)), (2, 1))
        with self.assertRaises(p.Rejected):
            self.provider.create_catalog(self.db)

    def test_effects_concurrent_thread_and_async_contexts(self):
        barrier = threading.Barrier(2)
        def worker(label):
            with self.provider.effects(lambda key, identity, call: label):
                barrier.wait(timeout=5)
                return self.provider._effect('POST', 'fixed', '/target', b'', lambda: 'dispatch')
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, label) for label in ('one', 'two')]
            self.assertEqual([future.result() for future in futures], ['one', 'two'])
        async def run():
            async def task(label):
                with self.provider.effects(lambda key, identity, call: label):
                    await asyncio.sleep(0)
                    return self.provider._effect('POST', 'fixed', '/target', b'', lambda: 'dispatch')
            return await asyncio.gather(task('a'), task('b'))
        self.assertEqual(asyncio.run(run()), ['a', 'b'])
        self.assertIsNone(self.provider._effects.get())

    def test_oauth_get_select_show_polling_not_journaled_and_refresh(self):
        self.open(effects=False)
        events = []
        with self.provider.effects(lambda key, identity, call: (events.append(identity), call())[1]):
            self.provider._polaris('GET', p.MANAGEMENT+'/principals/root')
            self.provider._token_deadlines['root'] = 0
            self.provider._polaris('GET', p.MANAGEMENT+'/principals/root')
            def http(method, url, **kwargs):
                if method == 'POST':
                    return 200, json.dumps({'id': 'statement-1', 'nextUri': self.provider._tunnels['trino-local']+'/v1/statement/next'}).encode()
                return 200, b'{"data":[["ok"]]}'
            self.h.http_handler = http
            for sql in ('SELECT current_user', 'SHOW CATALOGS'):
                self.assertEqual(self.provider._sql(sql, {'username': 'user', 'password': 'password'}), [['ok']])
        self.assertEqual(events, [])
        self.assertEqual(sum(url.endswith('/oauth/tokens') for _, url, _ in self.h.http_calls), 2)

    def test_sql_create_whole_terminal_action_journaled_and_payload_private(self):
        self.open(effects=False)
        entries = []
        def http(method, url, **kwargs):
            if method == 'POST':
                return 200, json.dumps({'id': 'statement-1', 'nextUri': self.provider._tunnels['trino-local']+'/v1/statement/next'}).encode()
            return 200, b'{}'
        self.h.http_handler = http
        with self.provider.effects(lambda key, identity, call: (entries.append(identity), call())[1]):
            self.provider.attach_catalog(self.db, 'private_ref')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['target'], '/v1/statement')
        self.assertNotIn('CREATE', json.dumps(entries))
        self.assertNotIn('private_ref', json.dumps(entries))
        self.assertEqual([method for method, _, _ in self.h.http_calls], ['POST', 'GET'])

    def test_managed_policy_semantic_readback_and_strict_identity(self):
        self.open(effects=False)
        name, arn = 'owned-db-1', 'arn:aws:iam::000000000000:policy/owned-db-1'
        policy = self.provider._policy(self.db)
        policy['Statement'].reverse()
        policy['Statement'][0]['Action'].reverse()
        policy['Statement'][0]['Resource'] = policy['Statement'][0]['Resource'][0]
        policy['Statement'][1]['Condition']['StringLike']['s3:prefix'].reverse()
        trust = self.provider._trust_policy()
        trust['Statement'][0]['Principal']['AWS'] = [trust['Statement'][0]['Principal']['AWS']]
        trust['Statement'][0]['Action'] = ['sts:AssumeRole']
        responses = {'get_role': {'Role': {'Arn': 'arn:aws:iam::000000000000:role/'+name, 'AssumeRolePolicyDocument': trust}},
            'list_attached_role_policies': {'AttachedPolicies': [{'PolicyName': name, 'PolicyArn': arn}]},
            'get_policy': {'Policy': {'Arn': arn, 'PolicyName': name, 'PolicyId': 'policy-id', 'DefaultVersionId': 'v1'}},
            'get_policy_version': {'PolicyVersion': {'Document': p.urllib.parse.quote(json.dumps(policy))}}}
        self.h.iam_handler = lambda method, params: responses[method]
        proof = self.provider.check_role(self.db)
        self.assertEqual(proof, p._proof(self.db, 'role', True, role_arn='arn:aws:iam::000000000000:role/'+name))
        for changed in ('resource', 'condition', 'principal'):
            with self.subTest(changed=changed):
                altered = copy.deepcopy(responses)
                if changed == 'principal':
                    altered['get_role']['Role']['AssumeRolePolicyDocument']['Statement'][0]['Principal']['AWS'] = ['arn:aws:iam::000000000000:root']
                else:
                    doc = copy.deepcopy(policy)
                    if changed == 'resource':
                        doc['Statement'][0]['Resource'] = '*'
                    else:
                        doc['Statement'][1]['Condition']['StringLike']['s3:prefix'] = ['*']
                    altered['get_policy_version']['PolicyVersion']['Document'] = doc
                self.h.iam_handler = lambda method, params: altered[method]
                proof = self.provider.check_role(self.db)
                self.assertFalse(proof['verified'])
                self.assertFalse(proof['complete'])
        self.h.iam_handler = lambda method, params: responses[method]
        responses['list_attached_role_policies']['AttachedPolicies'][0]['PolicyArn'] = 'arn:aws:iam::999999999999:policy/'+name
        with self.assertRaises(p.Rejected):
            self.provider.check_role(self.db)
        methods = [method for method, _ in self.h.sdk_calls]
        self.assertNotIn('put_role_policy', methods)
        self.assertNotIn('get_role_policy', methods)

    def test_catalog_pair_normalization_and_all_compute_readback(self):
        self.open()
        material = {'credentials': {'ref': {'version': 1, 'client_id': 'client', 'client_secret': 'secret'}}}
        self.assertEqual(self.provider._material(material)['credential.ref'], 'client:secret')
        for bad in ({'username': 'u', 'password': 'p'}, {'version': 2, 'client_id': 'c', 'client_secret': 's'},
                {'client_id': 'c', 'client_secret': 's', 'extra': 'x'}):
            with self.assertRaises(p.Rejected):
                self.provider._material({'credentials': {'ref': bad}})
        nodes = [{'metadata': {'uid': 'one'}}, {'metadata': {'uid': 'two'}}]
        reads = []
        def read(mode, data=b'', *, reader=None):
            reads.append((mode, reader))
            return {'ref.sha256': hashlib.sha256(b'client:secret').hexdigest()} if mode == 'resolve' else self.h.hashes
        with patch.object(self.provider, '_compute', return_value=nodes), patch.object(self.provider, '_exec', side_effect=read):
            proof = self.provider.check_material(self.db, material)
            self.assertEqual(proof['kind'], 'publication')
            self.assertTrue(proof['complete'])
            for key, value in self.db.items():
                self.assertEqual(proof[key], value)
            self.assertEqual([node['metadata']['uid'] for mode, node in reads if mode == 'resolve'], ['one', 'two'])
            self.assertEqual([node['metadata']['uid'] for mode, node in reads if mode == 'read'], ['one', 'two'])
        def stale(mode, data=b'', *, reader=None):
            return {'ref.sha256': '0'*64} if reader == nodes[1] else read(mode, data, reader=reader)
        with patch.object(self.provider, '_compute', return_value=nodes), patch.object(self.provider, '_exec', side_effect=stale):
            proof = self.provider.check_material(self.db, material)
            self.assertFalse(proof['verified'])
            self.assertFalse(proof['complete'])

    def _worker_material_setup(self):
        self.c['trino']['workers'] = 1
        self.provider = p.Provider(self.c, command=self.h.command, http=self.h.http,
            sdk=self.h.sdk, popen=self.h.popen)
        self.addCleanup(self.provider.close)
        self.open(effects=False)

    def test_target_delta_and_legacy_full_refs_resolve_on_every_native_compute(self):
        self._worker_material_setup()
        pairs = {'target_ref': {'client_id':'target-client','client_secret':'target-secret'},
            'retained_ref': {'client_id':'retained-client','client_secret':'retained-secret'}}
        resolved = []
        reads = []
        def exec_handler(argv, data):
            if argv[-1] == 'resolve':
                ref = data.decode().strip().split('=',1)[1]
                pod = argv[argv.index('exec')+2]
                resolved.append((pod, ref))
                pair = pairs[ref]
                digest = hashlib.sha256((pair['client_id']+':'+pair['client_secret']).encode()).hexdigest()
                values = {'ref.sha256': digest}
            else:
                self.assertEqual(argv[-1], 'read')
                reads.append(argv[argv.index('exec')+2])
                values = self.h.hashes
            return ('status=OK\n'+''.join(k+'='+v+'\n' for k,v in values.items())).encode()
        self.h.exec_handler = exec_handler
        for refs in ({'target_ref':pairs['target_ref']}, pairs):
            resolved.clear()
            reads.clear()
            with self.subTest(refs=list(refs)):
                proof = self.provider.check_material(self.db, {'credentials': refs})
                self.assertTrue(proof['complete'] and proof['verified'])
                self.assertEqual(set(resolved), {(pod,ref) for pod in
                    ('trino-local-pod','trino-local-worker-pod') for ref in refs})
                self.assertEqual(len(resolved), len(refs)*2)
                self.assertEqual(reads, ['trino-local-pod', 'trino-local-worker-pod'])
                self.assertEqual(proof['compute_uids'], ['pod-uid','trino-local-worker-pod-uid'])
                self.assertEqual(proof['hashes'], self.h.hashes)
        self.assertFalse(any('exec' in argv and argv[-1] == 'publish' for argv,_ in self.h.commands))

    def test_all_four_file_digests_must_propagate_even_when_target_ref_resolves(self):
        self._worker_material_setup()
        resolved = hashlib.sha256(b'target-value').hexdigest()
        for filename in p.TARGETS.values():
            def exec_handler(argv, data):
                if argv[-1] == 'resolve':
                    values = {'ref.sha256':resolved}
                else:
                    values = dict(self.h.hashes)
                    if 'trino-local-worker-pod' in argv:
                        values['target.'+filename] = '0'*64
                return ('status=OK\n'+''.join(k+'='+v+'\n' for k,v in values.items())).encode()
            self.h.exec_handler = exec_handler
            with self.subTest(filename=filename):
                proof = self.provider.check_material(self.db, {'credentials':{'target_ref':'target-value'}})
                self.assertFalse(proof['verified'] or proof['complete'])
        # Equal files still must match the requested non-credential payload bytes.
        with patch.object(self.provider, '_exec', side_effect=lambda mode, *a, **kw:
                {'ref.sha256':resolved} if mode == 'resolve' else self.h.hashes):
            proof = self.provider.check_material(self.db, {'credentials':{'target_ref':'target-value'},
                'rules_json':'{}', 'expected_hashes':{'rules.json':self.h.hashes['target.rules.json']}})
            self.assertFalse(proof['verified'])

    def test_reference_and_each_node_require_exact_four_nonmissing_lowercase_hashes(self):
        self._worker_material_setup()
        invalid = [None, [], {}, {**self.h.hashes,'unknown':'a'*64}]
        for key in self.h.hashes:
            partial = dict(self.h.hashes)
            del partial[key]
            invalid.append(partial)
            invalid.extend({**self.h.hashes,key:value} for value in
                ('MISSING','A'*64,'g'*64,'a'*63,'',None,True,1))
        digest = hashlib.sha256(b'value').hexdigest()
        for bad in invalid:
            for reference in (False, True):
                def read(mode, data=b'', *, reader=None):
                    if mode == 'resolve':
                        return {'ref.sha256':digest}
                    if reference or reader['metadata']['name'] == 'trino-local-worker-pod':
                        return bad
                    return self.h.hashes
                with self.subTest(bad=bad, reference=reference), patch.object(self.provider, '_exec', side_effect=read):
                    proof = self.provider.check_material(self.db, {'credentials':{'target_ref':'value'}})
                    self.assertFalse(proof['verified'] or proof['complete'])

    def test_check_material_retains_immediate_reader_pod_uid_guard(self):
        self._worker_material_setup()
        resource = self.h.resource
        worker_reads = 0
        def replaced(kind, name):
            nonlocal worker_reads
            result = resource(kind,name)
            if kind == 'pod' and name == 'trino-local-worker-pod':
                worker_reads += 1
                if worker_reads == 2:  # Compute listing, then the immediate reader UID reread.
                    result['metadata']['uid'] = 'replacement-worker'
            return result
        self.h.exec_handler = lambda argv,data: ('status=OK\n'+''.join(k+'='+v+'\n' for k,v in
            ({'ref.sha256':hashlib.sha256(b'value').hexdigest()} if argv[-1] == 'resolve' else self.h.hashes).items())).encode()
        with patch.object(self.h,'resource',side_effect=replaced), self.assertRaises(p.Rejected):
            self.provider.check_material(self.db, {'credentials':{'target_ref':'value'}})
        self.assertFalse(any('exec' in argv and 'trino-local-worker-pod' in argv for argv,_ in self.h.commands))

    def test_attached_exact_catalog_digest_presence_and_reference(self):
        self.open(effects=False)
        props = self.provider._attach_properties(self.db, 'consumer_ref')
        self.assertEqual(props['connector.name'], 'iceberg')
        expected = p._catalog_digest(props)
        state = {'digest': expected, 'ref': 'a'*64, 'rows': [['DB_one']]}
        def read(mode, data=b'', **kwargs):
            self.assertIn(mode, ('resolve', 'catalog'))
            if mode == 'catalog':
                self.assertEqual(data, b'catalog=DB_one\n')
                return {'catalog.sha256': state['digest']}
            return {'ref.sha256': state['ref']}
        def sql(statement, identity):
            self.assertEqual(statement, 'SHOW CATALOGS')
            self.assertEqual(identity['username'], 'logical_provisioner')
            return state['rows']
        with patch.object(self.provider, '_exec', side_effect=read), patch.object(self.provider, '_sql', side_effect=sql):
            proof = self.provider.check_attached(self.db, 'consumer_ref')
            self.assertEqual(proof, p._proof(self.db, 'attach', True, binding_verified=True,
                credential_ref='consumer_ref', catalog_sha256=expected))
            for field, bad in (('digest', 'MISSING'), ('digest', '0'*64), ('ref', 'MISSING'), ('rows', [])):
                old = state[field]
                state[field] = bad
                proof = self.provider.check_attached(self.db, 'consumer_ref')
                self.assertFalse(proof['verified'])
                self.assertFalse(proof['complete'])
                self.assertFalse(proof['binding_verified'])
                state[field] = old
            for key in props:
                changed = {**props, key: props[key]+'wrong'}
                state['digest'] = p._catalog_digest(changed)
                proof = self.provider.check_attached(self.db, 'consumer_ref')
                self.assertFalse(proof['verified'])
                self.assertFalse(proof['complete'])
                self.assertFalse(proof['binding_verified'])
        with self.assertRaises(p.Rejected):
            self.provider.check_attached({**self.db, 'sql_catalog': '../evil'}, 'consumer_ref')
        with self.assertRaises(p.Rejected):
            self.provider._exec('arbitrary')

    def test_attached_show_catalogs_uses_internal_readonly_transport_and_exact_membership(self):
        self.open(effects=False)
        digest = p._catalog_digest(self.provider._attach_properties(self.db, 'consumer_ref'))
        state = {'rows':[['DB_one']], 'digest':digest, 'ref':'a'*64}
        def exec_handler(argv, data):
            self.assertIn(argv[-1], ('resolve', 'catalog'))
            if argv[-1] == 'catalog':
                self.assertEqual(data, b'catalog=DB_one\n')
                values = {'catalog.sha256':state['digest']}
            else:
                self.assertEqual(data, b'ref=consumer_ref\n')
                values = {'ref.sha256':state['ref']}
            return ('status=OK\n'+''.join(k+'='+v+'\n' for k,v in values.items())).encode()
        def http(method, url, **kwargs):
            self.assertEqual(method, 'POST')
            self.assertTrue(url.endswith('/v1/statement'))
            self.assertEqual(kwargs['data'], b'SHOW CATALOGS')
            self.assertEqual(kwargs['headers']['X-Trino-User'], 'logical_provisioner')
            self.assertEqual(kwargs['headers']['Authorization'], 'Basic '+base64.b64encode(
                b'logical_provisioner:provisioner-secret').decode())
            self.assertEqual(kwargs['ca_file'], self.c['trino']['ca_file'])
            return 200,json.dumps({'data':state['rows']}).encode()
        self.h.exec_handler = exec_handler
        self.h.http_handler = http
        with patch.object(self.provider, '_effect', side_effect=AssertionError('business journal')), \
                patch.object(self.provider, '_request', wraps=self.provider._request) as request:
            for rows, verified in [([['DB_one']],True), ([['system'],['DB_one']],True),
                    ([],False), ([['DB_one_extra']],False), ([['db_one']],False),
                    ([['prefix_DB_one']],False), ([['DB_one','extra']],False)]:
                state['rows'] = rows
                with self.subTest(rows=rows):
                    proof = self.provider.check_attached(self.db, 'consumer_ref')
                    self.assertIs(proof['verified'],verified)
                    self.assertIs(proof['complete'],verified)
                    self.assertIs(proof['binding_verified'],verified)
            state['rows'] = [['DB_one']]
            for field, value in [('digest','0'*64),('digest','MISSING'),('ref','MISSING')]:
                old = state[field]
                state[field] = value
                with self.subTest(field=field, value=value):
                    self.assertFalse(self.provider.check_attached(self.db, 'consumer_ref')['verified'])
                state[field] = old
            for call in request.call_args_list:
                self.assertIs(call.kwargs['business'],False)
                self.assertIs(call.kwargs['journal'],False)
        before = (len(self.h.http_calls),sum('exec' in argv for argv,_ in self.h.commands))
        self.h.node_uid = 'replacement-node'
        with self.assertRaises(p.Rejected):
            self.provider.check_attached(self.db, 'consumer_ref')
        self.assertEqual((len(self.h.http_calls),sum('exec' in argv for argv,_ in self.h.commands)),before)

    def test_provisioner_version_and_unknown_fields_rejected(self):
        self.open()
        path = self.root/'provisioner.json'
        self.assertEqual(self.provider._provisioner()['version'], 1)
        for bad in ({'username': 'logical_provisioner', 'password': 's'},
                {'version': 1, 'username': 'logical_provisioner', 'password': 's', 'other': 1},
                {'version': True, 'username': 'logical_provisioner', 'password': 's'}):
            path.write_text(json.dumps(bad))
            with self.assertRaises(p.Rejected):
                self.provider._provisioner()

    def test_publisher_exact_sentinel_lines_with_kubectl_suffix(self):
        self.open()
        for code, sentinel, error in ((22, b'LOGICAL_SECRETS_CAS_STALE', p.StaleCAS),
                (23, b'LOGICAL_SECRETS_DENIED', p.Rejected)):
            def result(argv, data):
                return subprocess.CompletedProcess(argv, code, stdout=b'', stderr=sentinel+b'\ncommand terminated with exit code '+str(code).encode()+b'\n')
            self.h.exec_handler = result
            with self.assertRaises(error) as caught:
                self.provider._exec('publish', b'credential.ref=secret\n')
            self.assertFalse(caught.exception.unknown)
            for stderr in (b'prefix '+sentinel+b'\n', sentinel+b' suffix\n'):
                self.h.exec_handler = lambda argv, data: subprocess.CompletedProcess(argv, code, stdout=b'', stderr=stderr)
                with self.assertRaises(p.Unknown):
                    self.provider._exec('publish', b'credential.ref=secret\n')
        for code in (1, 24, 137):
            self.h.exec_handler = lambda argv, data: subprocess.CompletedProcess(argv, code, stdout=b'', stderr=b'LOGICAL_SECRETS_DENIED\n')
            with self.assertRaises(p.Unknown):
                self.provider._exec('publish', b'credential.ref=secret\n')

    def test_publisher_truncated_or_unexpected_mutation_receipt_unknown(self):
        self.open()
        for raw in (b'status=OK\n', b'status=OK\nref.sha256='+b'a'*64+b'\n', b'truncated'):
            self.h.exec_handler = lambda *args: raw
            with self.subTest(raw=raw), self.assertRaises(p.Unknown):
                self.provider._exec('publish', b'credential.ref=secret\n')
        self.h.exec_handler = lambda *args: b'status=OK\ncatalog.sha256=MISSING\n'
        self.assertEqual(self.provider._exec('catalog', b'catalog=DB_one\n'), {'catalog.sha256': 'MISSING'})

    def test_invalid_mutation_ack_shapes_remain_unknown(self):
        self.open(effects=False)
        self.provider._tokens['root'] = 'token'
        with self.provider.effects(lambda key, identity, call: []), self.assertRaises(p.Unknown):
            self.provider.issue_consumer(self.db)
        with self.provider.effects(lambda key, identity, call: {}), self.assertRaises(p.Unknown):
            self.provider.create_role(self.db)

    def test_probe_rejects_foreign_claim_or_nonlocal_or_unpinned_pv(self):
        self.open(effects=False)
        original = self.h.resource
        for change in ('claim', 'local', 'node', 'class'):
            def resource(kind, name):
                result = original(kind, name)
                if kind == 'pv':
                    if change == 'claim':
                        result['spec']['claimRef']['uid'] = 'foreign'
                    elif change == 'local':
                        result['spec'].pop('local')
                    elif change == 'node':
                        result['spec']['nodeAffinity']['required']['nodeSelectorTerms'][0]['matchExpressions'][0]['values'] = ['other']
                    else:
                        result['spec']['storageClassName'] = 'other'
                return result
            self.h.resource = resource
            with self.subTest(change=change):
                self.assertFalse(self.provider.probe()['ready'])

    def test_default_sdk_disables_retries_and_proxies(self):
        with patch('boto3.client') as client:
            p._sdk('iam', region_name='us-east-1', aws_access_key_id='mock', aws_secret_access_key='mock')
            config = client.call_args.kwargs['config']
            self.assertEqual(config.retries['total_max_attempts'], 1)
            self.assertEqual(config.proxies, {})

    def test_json_roundtrip_ack_and_known_failures_inside_callback(self):
        self.open(effects=False)
        self.provider._tokens['root'] = 'token'
        saved, errors = {}, []
        def journal(key, identity, call):
            try:
                value = call()
            except p.ProviderError as error:
                errors.append((type(error), error.code))
                raise
            saved[key] = json.loads(json.dumps(value, default=lambda item: item.isoformat()
                if isinstance(item, (datetime.date, datetime.datetime)) else (_ for _ in ()).throw(TypeError())))
            return saved[key]
        def iam(method, params):
            if method == 'create_role':
                return {'Role': {'Arn': 'arn:aws:iam::000000000000:role/owned-db-1',
                    'CreateDate': datetime.datetime(2026, 10, 28, tzinfo=datetime.timezone.utc)}}
            if method == 'create_policy':
                return {'Policy': {'Arn': 'arn:aws:iam::000000000000:policy/owned-db-1', 'PolicyName': 'owned-db-1',
                    'PolicyId': 'policy-id', 'CreateDate': datetime.date(2026, 10, 28)}}
            return {}
        self.h.iam_handler = iam
        with self.provider.effects(journal):
            self.provider.issue_consumer(self.db)
            self.provider.create_role(self.db)
            self.provider.publish_material(self.db, {'credentials': {'ref': 'secret'}})
        before = (len(self.h.http_calls), len([x for x in self.h.sdk_calls if x[0] not in ('iam', 's3')]))
        with self.provider.effects(lambda key, identity, call: saved[key]):
            self.provider.issue_consumer(self.db)
            self.provider.create_role(self.db)
            self.provider.publish_material(self.db, {'credentials': {'ref': 'secret'}})
        self.assertEqual(before, (len(self.h.http_calls), len([x for x in self.h.sdk_calls if x[0] not in ('iam', 's3')])))
        # Client construction may occur on replay, but no SDK operation dispatches.
        self.h.http_handler = lambda *args, **kw: (400, b'PRIVATE')
        with self.provider.effects(journal), self.assertRaises(p.Rejected):
            self.provider.create_catalog(self.db)
        self.assertEqual(errors[-1], (p.Rejected, 'PROVIDER_HTTP_400'))
        class BadRequest(Exception):
            response = {'ResponseMetadata': {'HTTPStatusCode': 400}}
        self.h.iam_handler = lambda *args: (_ for _ in ()).throw(BadRequest('PRIVATE'))
        with self.provider.effects(journal), self.assertRaises(p.Rejected):
            self.provider.create_role(self.db)
        self.assertEqual(errors[-1], (p.Rejected, 'PROVIDER_IAM_REJECTED'))
        with self.provider.effects(journal), self.assertRaises(p.Rejected):
            self.provider._storage('create_bucket', Bucket='logical-data')
        self.assertEqual(errors[-1], (p.Rejected, 'PROVIDER_STORAGE_HTTP_400'))
        for raw in (b'invalid', b'[]'):
            self.h.http_handler = lambda *args, **kw: (200, raw)
            with self.provider.effects(journal), self.assertRaises(p.Unknown):
                self.provider.create_catalog(self.db)
            self.assertIs(errors[-1][0], p.Unknown)
        self.h.exec_handler = lambda *args: b'status=OK\n'
        with self.provider.effects(journal), self.assertRaises(p.Unknown):
            self.provider._exec('publish', b'credential.ref=secret\n')
        self.assertEqual(errors[-1], (p.Unknown, 'PUBLISHER_RECEIPT_INVALID'))

    def test_exact_roles_grants_namespaces_and_catalog_readback(self):
        self.open(effects=False)
        role, cat = self.provider._role(self.db), self.db['sql_catalog']
        consumer = self.consumer()
        responses = {
            p.MANAGEMENT+'/principals/'+consumer['principal_name']: {'principal': {'name': consumer['principal_name'], 'clientId': consumer['client_id']}},
            p.MANAGEMENT+'/principals/'+consumer['principal_name']+'/principal-roles': {'roles': [{'name': role}]},
            p.MANAGEMENT+'/principal-roles/'+role+'/catalog-roles/'+cat: {'roles': [{'name': role}]},
            p.MANAGEMENT+'/catalogs/'+cat+'/catalog-roles/'+role+'/grants': {'grants': [{'type': 'catalog', 'privilege': 'CATALOG_MANAGE_CONTENT'}]},
            '/api/catalog/v1/'+cat+'/namespaces': {'namespaces': [[s] for s in p.SCHEMAS]},
            p.MANAGEMENT+'/catalogs/'+cat: {'catalog': self.provider._catalog(self.db)},
        }
        def read(method, path, *args, **kwargs):
            self.assertEqual(method, 'GET')
            if '/api/catalog/' in path:
                self.assertIs(kwargs['consumer'], consumer)
            return responses[path]
        with patch.object(self.provider, '_polaris', side_effect=read):
            self.assertTrue(self.provider.check_namespaces(self.db, consumer)['verified'])
            for path, wrapper in [(path, wrapper) for path, response in responses.items() for wrapper in response if wrapper in ('roles', 'grants', 'namespaces')]:
                original = responses[path][wrapper]
                wrong = [{'name': 'wrong'}] if wrapper == 'roles' else [{'type': 'namespace', 'privilege': 'CATALOG_MANAGE_CONTENT'}] if wrapper == 'grants' else [['wrong']]
                for changed in ([], wrong, original+original, original+wrong):
                    responses[path][wrapper] = changed
                    self.assertFalse(self.provider.check_namespaces(self.db, consumer)['verified'], (path, changed))
                responses[path][wrapper] = original
            grant_path = p.MANAGEMENT+'/catalogs/'+cat+'/catalog-roles/'+role+'/grants'
            for privilege in ('CATALOG_MANAGE_ACCESS', 'CATALOG_READ_PROPERTIES'):
                responses[grant_path]['grants'] = [{'type': 'catalog', 'privilege': privilege}]
                self.assertFalse(self.provider.check_namespaces(self.db, consumer)['verified'])
            responses[grant_path]['grants'] = [{'type': 'catalog', 'privilege': 'CATALOG_MANAGE_CONTENT'}]
            for field in ('clientId', 'name'):
                principal = responses[p.MANAGEMENT+'/principals/'+consumer['principal_name']]['principal']
                old = principal[field]
                principal[field] = 'wrong'
                with self.assertRaises(p.Rejected):
                    self.provider.check_namespaces(self.db, consumer)
                principal[field] = old
            catalog = responses[p.MANAGEMENT+'/catalogs/'+cat]['catalog']
            self.assertTrue(self.provider.check_catalog(self.db)['verified'])
            for section, field, wrong in [('storageConfigInfo', 'roleArn', 'wrong'),
                    ('storageConfigInfo', 'endpoint', 'http://wrong'), ('storageConfigInfo', 'endpointInternal', 'http://wrong'),
                    ('storageConfigInfo', 'stsEndpoint', 'http://wrong'), ('storageConfigInfo', 'allowedLocations', ['s3://logical-data/other/']),
                    ('properties', 'default-base-location', 's3://logical-data/other/')]:
                old = catalog[section][field]
                catalog[section][field] = wrong
                self.assertFalse(self.provider.check_catalog(self.db)['verified'])
                catalog[section][field] = old
            with self.assertRaises(p.Rejected):
                self.provider.check_namespaces(self.db, {**consumer, 'realm': 'OTHER'})

    def test_revoked_control_then_only_allowed_old_identity_denials(self):
        self.mock_refresh_clock()
        self.open(effects=False)
        identity = {'principal_id': 'principal1', 'database_id': 'db-1', 'selected_role': 'data_engineer', 'grant_epoch': 4,
            'policy_revision': 5, 'username': 'old_user', 'password': 'old_password', 'credential_ref': 'old_ref'}
        for outcome in ('PROVIDER_HTTP_401', 'PROVIDER_HTTP_403', 'ENGINE_ACCESS_DENIED',
                'ENGINE_QUERY_FAILED', 'PROVIDER_HTTP_404', 'PROVIDER_OBSERVATION_FAILED', 'loss', 'usable'):
            calls = []
            def sql(statement, supplied):
                calls.append((statement, supplied))
                if supplied['username'] == 'logical_provisioner':
                    return [['logical_provisioner']] if 'current_user' in statement else [[s] for s in p.SCHEMAS]
                self.assertIs(supplied, identity)
                if outcome == 'loss':
                    raise p.Unknown()
                if outcome != 'usable':
                    raise p.Rejected(outcome)
                return [[s] for s in p.SCHEMAS]
            with self.subTest(outcome=outcome), patch.object(self.provider, '_sql', side_effect=sql):
                if outcome in ('PROVIDER_HTTP_401', 'PROVIDER_HTTP_403', 'ENGINE_ACCESS_DENIED', 'usable'):
                    self.assertEqual(self.provider.verify_revoked(self.db, identity), {'verified': outcome != 'usable', 'control_positive': True, 'identity_denied': outcome != 'usable'})
                else:
                    with self.assertRaises(p.ProviderError):
                        self.provider.verify_revoked(self.db, identity)
            self.assertEqual(len(calls), 3 if outcome != 'usable' else 2+len(self.sleeps)+1)
            self.assertIn('current_user', calls[0][0])
            self.assertIn('SHOW SCHEMAS FROM "DB_one"', calls[1][0])
        for control in ('wrong_user', 'missing_schema', 'query_error', 'loss'):
            def sql(statement, supplied):
                self.assertEqual(supplied['username'], 'logical_provisioner')
                if control == 'loss':
                    raise p.Unknown()
                if control == 'query_error':
                    raise p.Rejected('ENGINE_QUERY_FAILED')
                if 'current_user' in statement:
                    return [['wrong']] if control == 'wrong_user' else [['logical_provisioner']]
                return []
            with patch.object(self.provider, '_sql', side_effect=sql), self.assertRaises(p.ProviderError):
                self.provider.verify_revoked(self.db, identity)
        with patch.object(self.provider, '_sql') as sql, self.assertRaises(p.Rejected):
            self.provider.verify_revoked(self.db, {**identity, 'username': 'logical_provisioner'})
        sql.assert_not_called()

    def test_sql_terminal_ack_after_poll_and_frozen_resume_without_network(self):
        self.open(effects=False)
        saved, order = {}, []
        def journal(key, identity, call):
            self.assertEqual(set(identity), {'method', 'service', 'target', 'payload_sha256'})
            saved[key] = json.loads(json.dumps(call()))
            order.append('durable')
            return saved[key]
        def http(method, url, **kwargs):
            order.append(method)
            self.assertFalse(saved)  # No ACK before terminal result.
            if method == 'POST':
                return 200, json.dumps({'id': 'q1', 'nextUri': self.provider._tunnels['trino-local']+'/v1/statement/next'}).encode()
            return 200, b'{"id":"q1","data":[["done"]]}'
        self.h.http_handler = http
        identity = {'username': 'user', 'password': 'secret'}
        with self.provider.effects(journal):
            self.assertEqual(self.provider._sql('CREATE CATALOG "example"', identity), [['done']])
        self.assertEqual(order, ['POST', 'GET', 'durable'])
        self.assertEqual(list(saved.values()), [{'rows': [['done']], 'query_id': 'q1', 'success': True}])
        self.h.http_handler = lambda *a, **kw: self.fail('frozen ACK must not use network')
        before = len(self.h.http_calls)
        with self.provider.effects(lambda key, identity, call: saved[key]):
            self.assertEqual(self.provider._sql('CREATE CATALOG "example"', identity), [['done']])
        self.assertEqual(len(self.h.http_calls), before)

    def test_sql_terminal_denial_is_captured_and_explicit_new_attempt_posts_once(self):
        self.open(effects=False)
        attempts, saved = [], {}
        attempt = 1
        def journal(key, identity, call):
            owned_key = (key, attempt)  # Core owns the explicit retry attempt key.
            self.assertNotIn(owned_key, saved)
            try:
                result = call()
            except p.ProviderError as error:
                attempts.append((owned_key, error.code, error.unknown))
                raise
            saved[owned_key] = json.loads(json.dumps(result))
            return saved[owned_key]
        def http(method, url, **kwargs):
            if method == 'POST':
                return 200, json.dumps({'id': 'q'+str(attempt), 'nextUri': self.provider._tunnels['trino-local']+'/v1/statement/next'}).encode()
            return 200, json.dumps({'error': {'errorName': 'ACCESS_DENIED'}} if attempt == 1 else {'data': []}).encode()
        self.h.http_handler = http
        with self.provider.effects(journal), self.assertRaises(p.Rejected) as caught:
            self.provider.attach_catalog(self.db, 'ref')
        self.assertEqual(caught.exception.code, 'ENGINE_ACCESS_DENIED')
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0][1:], ('ENGINE_ACCESS_DENIED', False))
        self.assertEqual(saved, {})
        self.assertEqual([m for m, _, _ in self.h.http_calls], ['POST', 'GET'])
        attempt = 2  # Explicit safe retry, not an automatic provider resend.
        with self.provider.effects(journal):
            self.provider.attach_catalog(self.db, 'ref')
        self.assertEqual([m for m, _, _ in self.h.http_calls], ['POST', 'GET', 'POST', 'GET'])
        self.assertEqual(next(iter(saved))[0], attempts[0][0][0])
        self.assertEqual(next(iter(saved))[1], 2)

    def test_sql_mutation_uncertainty_never_saves_ack_or_retries(self):
        self.open(effects=False)
        for outcome in ('initial_loss', 'initial_500', 'poll_loss', 'poll_500', 'poll_403', 'terminal_error', 'initial_error', 'broken', 'missing_id', 'bad_uri'):
            seen, saved, classified = [], [], []
            def journal(key, identity, call):
                try:
                    result = call()
                except p.ProviderError as error:
                    classified.append(error.unknown)
                    raise
                saved.append(result)
                return result
            def http(method, url, **kwargs):
                seen.append(method)
                if method == 'POST':
                    if outcome == 'initial_loss':
                        raise TimeoutError('private')
                    if outcome == 'initial_500':
                        return 500, b''
                    if outcome == 'broken':
                        return 200, b'broken'
                    if outcome == 'missing_id':
                        return 200, b'{}'
                    if outcome == 'bad_uri':
                        return 200, b'{"id":"q","nextUri":"https://[invalid"}'
                    if outcome == 'initial_error':
                        return 200, b'{"id":"q","error":{"errorName":"GENERIC_INTERNAL_ERROR"}}'
                    return 200, json.dumps({'id': 'q', 'nextUri': self.provider._tunnels['trino-local']+'/v1/statement/next'}).encode()
                if outcome == 'poll_loss':
                    raise TimeoutError('private')
                if outcome in ('poll_500', 'poll_403'):
                    return (500 if outcome == 'poll_500' else 403), b''
                return 200, b'{"error":{"errorName":"GENERIC_INTERNAL_ERROR"}}'
            self.h.http_handler = http
            with self.subTest(outcome=outcome), self.provider.effects(journal), self.assertRaises(p.Unknown):
                self.provider.attach_catalog(self.db, 'ref')
            self.assertEqual(seen, ['POST', 'GET'] if outcome in ('poll_loss', 'poll_500', 'poll_403', 'terminal_error') else ['POST'])
            self.assertEqual(classified, [True])
            self.assertEqual(saved, [])

    def test_revoked_uses_actual_http_and_engine_denial_classification(self):
        self.mock_refresh_clock()
        self.open(effects=False)
        identity = {'principal_id': 'principal1', 'database_id': 'db-1', 'selected_role': 'data_engineer', 'grant_epoch': 4,
            'policy_revision': 5, 'username': 'old_user', 'password': 'old_password', 'credential_ref': 'old_ref'}
        for denial in (401, 403, 'PERMISSION_DENIED', 'ACCESS_DENIED', 'GENERIC_INTERNAL_ERROR', 404, 'loss', 'usable'):
            users = []
            def http(method, url, **kwargs):
                user = kwargs['headers']['X-Trino-User']
                users.append(user)
                if user == 'logical_provisioner':
                    data = [['logical_provisioner']] if b'current_user' in kwargs['data'] else [[s] for s in p.SCHEMAS]
                    return 200, json.dumps({'id': 'control', 'data': data}).encode()
                self.assertEqual(user, 'old_user')
                expected = 'Basic '+base64.b64encode(b'old_user:old_password').decode()
                self.assertEqual(kwargs['headers']['Authorization'], expected)
                if isinstance(denial, int):
                    return denial, b'PRIVATE'
                if denial == 'loss':
                    raise TimeoutError('PRIVATE')
                if denial == 'usable':
                    return 200, json.dumps({'id': 'old', 'data': [[s] for s in p.SCHEMAS]}).encode()
                return 200, json.dumps({'id': 'old', 'error': {'errorName': denial}}).encode()
            self.h.http_handler = http
            with self.subTest(denial=denial):
                if denial in (401, 403, 'PERMISSION_DENIED', 'ACCESS_DENIED', 'usable'):
                    self.assertEqual(self.provider.verify_revoked(self.db, identity), {'verified': denial != 'usable', 'control_positive': True, 'identity_denied': denial != 'usable'})
                else:
                    with self.assertRaises(p.ProviderError):
                        self.provider.verify_revoked(self.db, identity)
            self.assertEqual(users[:2], ['logical_provisioner', 'logical_provisioner'])
            self.assertTrue(all(user == 'old_user' for user in users[2:]))
            self.assertEqual(len(users), 3 if denial != 'usable' else 3+len(self.sleeps))

    def test_worker_membership_comes_from_sql_and_not_pod_metadata(self):
        self.mock_refresh_clock()
        self.open(effects=False)
        self.provider._config['trino']['workers'] = 1
        identity = {'principal_id': 'principal1', 'database_id': 'db-1', 'selected_role': 'data_engineer', 'grant_epoch': 4,
            'policy_revision': 5, 'username': 'actual_user', 'password': 'actual_password', 'credential_ref': 'ref'}
        rows = [['coordinator', True, 'active'], ['worker', False, 'active']]
        def sql(statement, supplied):
            if supplied['username'] == 'logical_provisioner':
                self.assertEqual(statement, 'SELECT node_id, coordinator, state FROM system.runtime.nodes')
                return rows
            self.assertIs(supplied, identity)
            if 'current_user' in statement:
                return [['actual_user']]
            if 'SHOW SCHEMAS' in statement:
                return [[s] for s in p.SCHEMAS]
            raise p.Rejected('ENGINE_ACCESS_DENIED')
        with patch.object(self.provider, '_sql', side_effect=sql), patch.object(self.provider, '_verify_prefix',
                return_value={'prefix_positive': True, 'prefix_negative': True}):
            result = self.provider.verify_target(self.db, identity)
            self.assertTrue(result['verified'])
            self.assertEqual(result['runtime_nodes'], rows)
            rows.pop()
            self.assertFalse(self.provider.verify_target(self.db, identity)['verified'])

    def test_api_serialized_mount_defaults_compute_and_probe(self):
        for workers in (0, 1):
            with self.subTest(workers=workers):
                self.c['trino']['workers'] = workers
                harness = APISerializedHarness(self.c)
                with p.Provider(self.c, command=harness.command, http=harness.http,
                        sdk=harness.sdk, popen=harness.popen) as provider:
                    nodes = provider._compute()
                    self.assertEqual(len(nodes), 1+workers)
                    for node in nodes:
                        mounts = node['spec']['containers'][0]['volumeMounts']
                        self.assertTrue(all(m.get('readOnly') is True for m in mounts if 'readOnly' in m))
                        self.assertNotIn('readOnly', next(m for m in mounts if m['mountPath'] == '/tmp'))
                    proof = provider.probe()
                    self.assertTrue(proof['ready'])
                    self.assertEqual(set(proof['incarnation']['compute']),
                        {'pod-uid', 'trino-local-worker-pod-uid'} if workers else {'pod-uid'})
                    self.assertEqual(proof['hashes'], harness.hashes)
                    self.assertTrue(any(argv[-5:] == ['get', 'deployment', 'trino-local-worker', '-o', 'json']
                        for argv, _ in harness.commands))
                    readers = {argv[argv.index('exec')+2] for argv, _ in harness.commands
                        if 'exec' in argv and argv[-1] == 'read'}
                    self.assertEqual(readers, {node['metadata']['name'] for node in nodes})
                    self.assertEqual(harness.sdk_calls[-1][0], 'head_bucket')
                    self.assertFalse(any(argv[-1] == 'publish' for argv, _ in harness.commands))

    def test_compute_mount_readonly_values_remain_strict(self):
        self.c['trino']['workers'] = 1
        self.provider = p.Provider(self.c, command=self.h.command, http=self.h.http, sdk=self.h.sdk, popen=self.h.popen)
        self.addCleanup(self.provider.close)
        original = self.h.resource
        missing = object()
        paths = {'trino-local': ('/etc/trino-private-secrets', '/tmp', '/var/trino-catalogs'),
            'trino-local-worker': ('/etc/trino-private-secrets', '/tmp', '/opt/logical-secrets',
                '/usr/lib/trino/secrets-plugin/logical-file', '/etc/trino', '/etc/foundation')}
        for workload, mounts in paths.items():
            for path in mounts:
                readonly = path not in ('/tmp', '/var/trino-catalogs') and workload == 'trino-local-worker'
                for value in (None, 0, 1, 'false', 'true', not readonly, missing):
                    if value is missing and not readonly:
                        continue
                    def resource(kind, name):
                        result = original(kind, name)
                        if kind == 'pod' and name == workload+'-pod':
                            mount = next(m for m in result['spec']['containers'][0]['volumeMounts'] if m['mountPath'] == path)
                            if value is missing:
                                del mount['readOnly']
                            else:
                                mount['readOnly'] = value
                        return result
                    with self.subTest(workload=workload, path=path, value=value), \
                            patch.object(self.h, 'resource', side_effect=resource), \
                            self.assertRaisesRegex(p.Rejected, 'FOUNDATION_COMPUTE_MISMATCH'):
                        self.provider._compute()

    def test_compute_privilege_escalation_must_remain_explicit_false(self):
        original = self.h.resource
        def resource(kind, name):
            result = original(kind, name)
            if kind == 'pod':
                result['spec']['containers'][0]['securityContext'].pop('allowPrivilegeEscalation')
            return result
        with patch.object(self.h, 'resource', side_effect=resource), \
                self.assertRaisesRegex(p.Rejected, 'FOUNDATION_COMPUTE_MISMATCH'):
            self.provider._compute()

    def test_worker_compute_readback_and_fixed_coordinator_publisher(self):
        self.c['trino']['workers'] = 1
        self.provider = p.Provider(self.c, command=self.h.command, http=self.h.http, sdk=self.h.sdk, popen=self.h.popen)
        self.addCleanup(self.provider.close)
        self.open(effects=False)
        self.assertEqual(len(self.provider._compute()), 2)
        self.assertEqual(self.provider._publisher()['metadata']['name'], 'trino-local-pod')
        proof = self.provider.probe()
        self.assertTrue(proof['ready'])
        self.assertEqual(set(proof['incarnation']['compute']), {'pod-uid', 'trino-local-worker-pod-uid'})
        self.assertEqual(proof['hashes'], self.h.hashes)
        original = self.h.resource
        for change in ('image', 'node', 'uid', 'runtime', 'writable', 'catalogs', 'unready', 'foreign'):
            def resource(kind, name):
                result = original(kind, name)
                if kind == 'pod' and name == 'trino-local-worker-pod':
                    spec = result['spec']
                    if change == 'image':
                        spec['containers'][0]['image'] = 'wrong'
                    elif change == 'node':
                        spec['nodeName'] = 'other'
                    elif change == 'uid':
                        result['metadata']['ownerReferences'][0]['uid'] = 'wrong'
                    elif change == 'runtime':
                        spec['volumes'][0]['persistentVolumeClaim']['claimName'] = 'wrong'
                    elif change == 'writable':
                        spec['containers'][0]['volumeMounts'][0]['readOnly'] = False
                    elif change == 'catalogs':
                        spec['volumes'].append({'name': 'catalogs', 'persistentVolumeClaim': {'claimName': 'trino-catalogs'}})
                    elif change == 'unready':
                        result['status']['conditions'][0]['status'] = 'False'
                    else:
                        result['metadata']['ownerReferences'][0]['name'] = 'unexpected-rs'
                return result
            self.h.resource = resource
            with self.subTest(change=change), self.assertRaises(p.Rejected):
                self.provider._compute()
        self.h.resource = original
        self.provider._config['trino']['workers'] = 0
        with self.assertRaises(p.Rejected):
            self.provider._compute()


    def test_final_callback_exceptions_are_preserved_for_all_sdk_leaves(self):
        self.open(effects=False)
        class Sentinel(Exception):
            pass
        class Client:
            def assume_role(self, **kwargs):
                return {'Credentials': {'AccessKeyId': 'a', 'SecretAccessKey': 's', 'SessionToken': 't'}}
            def put_object(self, **kwargs):
                return {'ETag': 'fixed'}
            def create_role(self, **kwargs):
                return {}
        self.provider._sdk_factory = lambda *args, **kwargs: Client()
        operations = [lambda: self.provider._iam('create_role', RoleName='fixed'),
            lambda: self.provider._storage('put_object', Bucket='fixed', Key='key', Body=b'x'),
            lambda: self.provider._verify_prefix(self.db)]
        for operation in operations:
            for after in (False, True):
                sentinel, dispatched = Sentinel('journal-owned'), []
                def journal(key, identity, call):
                    if after:
                        call()
                        dispatched.append(True)
                    raise sentinel
                with self.provider.effects(journal), self.assertRaises(Sentinel) as caught:
                    operation()
                self.assertIs(caught.exception, sentinel)
                self.assertEqual(len(dispatched), int(after))
        class Denied(Exception):
            response = {'ResponseMetadata': {'HTTPStatusCode': 403}}
        sentinel = Sentinel('CREATION_FAILED')
        def journal(key, identity, call):
            try:
                call()
            except p.Rejected as error:
                self.assertFalse(error.unknown)
                raise sentinel
        with patch.object(Client, 'create_role', side_effect=Denied()), self.provider.effects(journal):
            with self.assertRaises(Sentinel) as caught:
                operations[0]()
        self.assertIs(caught.exception, sentinel)

    def test_final_http_sdk_uncertain_statuses_never_retry(self):
        self.open()
        class Failure(Exception):
            pass
        for status, code, known in [(400, '', True), (401, '', True), (403, '', True),
                (408, '', False), (499, '', False), (400, 'RequestTimeout', False),
                (403, 'RequestTimeoutException', False), (409, 'PriorRequestNotComplete', False)]:
            failure = Failure('private')
            failure.response = {'ResponseMetadata': {'HTTPStatusCode': status}, 'Error': {'Code': code}}
            expected = p.Rejected if known else p.Unknown
            for operation in (lambda: self.provider._iam('create_role', RoleName='fixed'),
                    lambda: self.provider._storage('put_object', Bucket='fixed', Key='key', Body=b'x')):
                with patch.object(self.provider, '_sdk_factory') as factory:
                    client = factory.return_value
                    client.create_role.side_effect = failure
                    client.put_object.side_effect = failure
                    with self.assertRaises(expected):
                        operation()
                    self.assertEqual(len(client.method_calls), 1)
            if not code:
                before = len(self.h.http_calls)
                self.h.http_handler = lambda *args, **kwargs: (status, b'private')
                with self.assertRaises(expected):
                    self.provider._request('trino-local', 'POST', '/fixed', body={})
                self.assertEqual(len(self.h.http_calls)-before, 1)
            class Client:
                def assume_role(self, **kwargs):
                    return {'Credentials': {'AccessKeyId': 'a', 'SecretAccessKey': 's', 'SessionToken': 't'}}
                def put_object(self, **kwargs):
                    raise failure
            with patch.object(self.provider, '_sdk_factory', return_value=Client()):
                with self.assertRaises(expected):
                    self.provider._verify_prefix(self.db)

    def test_final_lost_marker_put_observed_without_second_put(self):
        self.mock_refresh_clock()
        self.open()
        class Denied(Exception):
            response = {'ResponseMetadata': {'HTTPStatusCode': 403}, 'Error': {'Code': 'AccessDenied'}}
        class Client:
            marker = None
            puts = 0
            def assume_role(self, **kwargs):
                return {'Credentials': {'AccessKeyId': 'a', 'SecretAccessKey': 's', 'SessionToken': 't'}}
            def put_object(self, **kwargs):
                self.puts += 1
                self.marker = kwargs['Body']
                raise TimeoutError('lost response')
            def get_object(self, **kwargs):
                if kwargs['Key'].startswith('_logical_denied/'):
                    raise Denied()
                if self.marker is None:
                    raise KeyError('missing')
                return {'Body': io.BytesIO(self.marker)}
        client = Client()
        self.provider._sdk_factory = lambda *args, **kwargs: client
        with self.assertRaises(p.Unknown):
            self.provider._verify_prefix(self.db)
        identity = {'principal_id': 'principal1', 'database_id': 'db-1', 'selected_role': 'admin',
            'grant_epoch': None, 'policy_revision': 5, 'username': 'actual', 'password': 'secret', 'credential_ref': 'ref'}
        def sql(statement, supplied):
            if 'current_user' in statement:
                return [['actual']]
            if 'SHOW SCHEMAS' in statement:
                return [[schema] for schema in p.SCHEMAS]
            raise p.Rejected('ENGINE_ACCESS_DENIED')
        with patch.object(self.provider, '_sql', side_effect=sql):
            proof = self.provider.check_verify_target(self.db, identity)
            self.assertTrue(proof['complete'])
            self.assertEqual(proof['marker_sha256'], hashlib.sha256(client.marker).hexdigest())
        self.assertEqual(client.puts, 1)
        for marker in (None, b'wrong'):
            client.marker = marker
            with self.assertRaises(p.Rejected):
                self.provider._verify_prefix(self.db, write_marker=False)
        self.assertEqual(client.puts, 1)

    def test_final_readonly_verification_role_visibility_envelope_and_pins(self):
        self.mock_refresh_clock()
        self.open(effects=False)
        identity = {'principal_id': 'principal1', 'database_id': 'db-1', 'selected_role': 'bi_viewer',
            'grant_epoch': 4, 'policy_revision': 5, 'username': 'actual', 'password': 'secret', 'credential_ref': 'ref'}
        visible = [['marts'], ['information_schema']]
        denial = 'ENGINE_ACCESS_DENIED'
        def sql(statement, supplied):
            self.assertIs(supplied, identity)
            if 'current_user' in statement:
                return [['actual']]
            if 'SHOW SCHEMAS' in statement:
                return visible
            raise p.Rejected(denial)
        with patch.object(self.provider, '_sql', side_effect=sql), patch.object(self.provider, '_verify_prefix',
                return_value={'prefix_positive': True, 'prefix_negative': True, 'marker_sha256': 'a'*64}) as prefix:
            for method, write in ((self.provider.check_verify_target, False), (self.provider.verify_target, True)):
                proof = method(self.db, identity)
                self.assertTrue(proof['complete'])
                self.assertEqual(proof['kind'], 'verification')
                for key, value in {**self.db, **identity}.items():
                    if key != 'password':
                        self.assertEqual(proof[key], value)
                self.assertNotIn('password', proof)
                prefix.assert_called_with(self.db, write_marker=write)
            visible.append(['raw'])
            self.assertFalse(self.provider.check_verify_target(self.db, identity)['complete'])
            visible[:] = [[schema] for schema in p.SCHEMAS]
            identity.update(selected_role='admin', grant_epoch=None)
            self.assertTrue(self.provider.check_verify_target(self.db, identity)['verified'])
            identity.update(selected_role='data_engineer', grant_epoch=4)
            self.assertTrue(self.provider.check_verify_target(self.db, identity)['verified'])
            denial = 'ENGINE_QUERY_FAILED'
            with self.assertRaises(p.Rejected):
                self.provider.check_verify_target(self.db, identity)
        for role, epoch, policy in [('viewer', 4, 1), ('unknown', 4, 1), ('admin', True, 1), ('bi_viewer', None, 1), ('admin', 0, 1), ('admin', None, True)]:
            bad = {**identity, 'selected_role': role, 'grant_epoch': epoch, 'policy_revision': policy}
            with patch.object(self.provider, '_sql') as read:
                for method in (self.provider.verify_target, self.provider.check_verify_target, self.provider.verify_revoked):
                    with self.assertRaises(p.Rejected):
                        method(self.db, bad)
                read.assert_not_called()

    def test_refresh_target_reads_until_real_identity_role_and_negative_proofs(self):
        self.mock_refresh_clock()
        self.open()
        identity = {'principal_id': 'principal1', 'database_id': 'db-1', 'selected_role': 'bi_viewer',
            'grant_epoch': 4, 'policy_revision': 5, 'username': 'actual', 'password': 'secret', 'credential_ref': 'ref'}
        class Denied(Exception):
            response = {'ResponseMetadata': {'HTTPStatusCode': 403}, 'Error': {'Code': 'AccessDenied'}}
        class Client:
            puts = 0
            marker = None
            def assume_role(self, **kwargs):
                return {'Credentials': {'AccessKeyId': 'a', 'SecretAccessKey': 's', 'SessionToken': 't'}}
            def put_object(self, **kwargs):
                self.puts += 1
                self.marker = kwargs['Body']
                return {'ETag': 'marker'}
            def get_object(self, **kwargs):
                if kwargs['Key'].startswith('_logical_denied/'):
                    raise Denied()
                return {'Body': io.BytesIO(self.marker)}
        client = Client()
        self.provider._sdk_factory = lambda *a, **kw: client
        for method, expected_puts in ((self.provider.verify_target, 1), (self.provider.check_verify_target, 1)):
            self.elapsed = 0.0
            reads = []
            def http(verb, url, **kwargs):
                self.assertEqual(verb, 'POST')  # Read-only SQL protocol still uses POST.
                self.assertEqual(kwargs['headers']['X-Trino-User'], 'actual')
                sql = kwargs['data'].decode()
                self.assertTrue(sql.startswith(('SELECT', 'SHOW')))
                reads.append((self.elapsed, sql))
                if 'current_user' in sql:
                    if self.elapsed < 0.1:
                        return 401, b''
                    if self.elapsed < 0.2:
                        return 403, b''
                    if self.elapsed < 0.3:
                        return 200, b'{"error":{"errorName":"ACCESS_DENIED"}}'
                    data = [['actual']]
                elif sql.startswith('SHOW'):
                    data = [] if self.elapsed < 0.4 else [['marts']]
                elif self.elapsed < 0.5:
                    data = [['unexpected-visible-node']]
                else:
                    return 200, b'{"error":{"errorName":"ACCESS_DENIED"}}'
                return 200, json.dumps({'data': data}).encode()
            self.h.http_handler = http
            proof = method(self.db, identity)
            self.assertTrue(proof['verified'])
            self.assertTrue(proof['identity_positive'])
            self.assertTrue(proof['identity_negative'])
            self.assertGreaterEqual(self.elapsed, 0.5)
            self.assertLess(self.elapsed, 5)
            self.assertGreater(len(reads), 3)
            self.assertEqual(client.puts, expected_puts)  # Recovery PUT delta is zero, even after polling.

    def test_refresh_target_deadline_and_generic_failures_never_write_marker(self):
        self.mock_refresh_clock()
        self.open()
        identity = {'principal_id': 'principal1', 'database_id': 'db-1', 'selected_role': 'bi_viewer',
            'grant_epoch': 4, 'policy_revision': 5, 'username': 'actual', 'password': 'secret', 'credential_ref': 'ref'}
        for outcome in (401, 403, 'denied', 'missing_role', 'visible_negative', 'query_error', 'loss', 500, 'wrong_user'):
            self.elapsed = 0.0
            self.sleeps.clear()
            reads = []
            def http(method, url, **kwargs):
                sql = kwargs['data'].decode()
                reads.append(sql)
                if outcome == 'loss':
                    raise TimeoutError('private')
                if isinstance(outcome, int):
                    return outcome, b''
                if outcome in ('denied', 'query_error'):
                    return 200, json.dumps({'error': {'errorName': 'ACCESS_DENIED' if outcome == 'denied' else 'GENERIC_INTERNAL_ERROR'}}).encode()
                data = ([['wrong' if outcome == 'wrong_user' else 'actual']] if 'current_user' in sql else
                    [] if outcome == 'missing_role' else [['marts']])
                return 200, json.dumps({'data': data}).encode()
            self.h.http_handler = http
            with self.subTest(outcome=outcome), patch.object(self.provider, '_verify_prefix') as marker:
                if outcome in (401, 403, 'denied', 'missing_role', 'visible_negative'):
                    proof = self.provider.verify_target(self.db, identity)
                    self.assertFalse(proof['verified'])
                    self.assertFalse(proof['complete'])
                    self.assertAlmostEqual(self.elapsed, 5.0)
                    self.assertGreater(len(reads), 1)
                else:
                    with self.assertRaises(p.ProviderError):
                        self.provider.verify_target(self.db, identity)
                    self.assertEqual(len(reads), 1)
                    self.assertEqual(self.sleeps, [])
                marker.assert_not_called()
        for invalid in ({**identity, 'extra': 'field'}, {k: v for k, v in identity.items() if k != 'grant_epoch'}):
            with patch.object(self.provider, '_sql') as sql, self.assertRaises(p.Rejected):
                self.provider.check_verify_target(self.db, invalid)
            sql.assert_not_called()

    def test_revoked_refresh_polls_old_identity_with_control_once_and_real_denial(self):
        self.mock_refresh_clock()
        self.open(effects=False)
        identity = {'principal_id': 'principal1', 'database_id': 'db-1', 'selected_role': 'admin',
            'grant_epoch': None, 'policy_revision': 5, 'username': 'old', 'password': 'secret', 'credential_ref': 'ref'}
        for denied in (True, False):
            self.elapsed = 0.0
            self.sleeps.clear()
            users = []
            def http(method, url, **kwargs):
                user = kwargs['headers']['X-Trino-User']
                users.append(user)
                sql = kwargs['data'].decode()
                self.assertTrue(sql.startswith(('SELECT', 'SHOW')))
                if user == 'logical_provisioner':
                    data = [[user]] if 'current_user' in sql else [[s] for s in p.SCHEMAS]
                else:
                    self.assertEqual(user, 'old')
                    if denied and self.elapsed >= 0.3:
                        return 403, b''
                    data = [[s] for s in p.SCHEMAS]
                return 200, json.dumps({'data': data}).encode()
            self.h.http_handler = http
            proof = self.provider.verify_revoked(self.db, identity)
            self.assertEqual(proof, {'verified': denied, 'control_positive': True, 'identity_denied': denied})
            self.assertEqual(users.count('logical_provisioner'), 2)
            self.assertGreater(users.count('old'), 1)
            if denied:
                self.assertGreaterEqual(self.elapsed, 0.3)
                self.assertLess(self.elapsed, 5)
            else:
                self.assertAlmostEqual(self.elapsed, 5)

    def test_sql_callback_failure_is_not_masked_before_or_after_terminal(self):
        self.open(effects=False)
        self.h.http_handler = lambda *a, **kw: (200, b'{"id":"q"}')
        class Sentinel(Exception):
            pass
        for after in (False, True):
            before = len(self.h.http_calls)
            sentinel = Sentinel('journal')
            def journal(key, identity, call):
                if after:
                    self.assertEqual(call(), {'rows': [], 'query_id': 'q', 'success': True})
                raise sentinel
            with self.provider.effects(journal), self.assertRaises(Sentinel) as caught:
                self.provider.attach_catalog(self.db, 'ref')
            self.assertIs(caught.exception, sentinel)
            self.assertEqual(len(self.h.http_calls)-before, int(after))

    def test_final_opa_fixed_deployment_readiness(self):
        self.open(effects=False)
        original = self.h.resource
        for field, value in [('availableReplicas', 0), ('readyReplicas', 0), ('observedGeneration', 1), ('image', 'wrong')]:
            def resource(kind, name):
                result = original(kind, name)
                if kind == 'deployment' and name == 'trino-opa':
                    if field == 'image':
                        result['spec']['template']['spec']['containers'][0]['image'] = value
                    else:
                        result['status'][field] = value
                return result
            with patch.object(self.h, 'resource', side_effect=resource):
                proof = self.provider.probe()
                self.assertFalse(proof['ready'])
                self.assertFalse(proof['readiness']['opa'])
        self.assertTrue(self.provider.probe()['readiness']['opa'])




class ReadOnlyConcurrencyTests(unittest.TestCase):
    """Default fanout with inert observations; no live collaborator is reachable."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.c = settings(Path(temp.name).resolve())
        self.h = Harness(self.c)
        for target in ('_command', '_http', '_sdk'):
            self.mock(p, target, side_effect=AssertionError('unstubbed transport'))
        self.mock(p.subprocess, 'Popen', side_effect=AssertionError('unstubbed process'))
        self.provider = p.Provider(self.c)
        self.addCleanup(self.provider.close)
        self.db = {'database_id': 'db-1'}
        self.nodes = [{'metadata': {'uid': str(i), 'name': 'node-'+str(i)}} for i in range(5)]
        self.material = {'credentials': {'one': 'value-one', 'two': {'client_id': 'c', 'client_secret': 's'}}}
        self.values = {'one': 'value-one', 'two': 'c:s'}

    def mock(self, obj, name, **kwargs):
        patcher = patch.object(obj, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def verification_stubs(self, provider=None):
        provider = provider or self.provider
        self.db['sql_catalog'] = 'catalog_one'
        self.mock(provider, '_gate')
        prefix = self.mock(provider, '_verify_prefix', return_value={
            'prefix_positive': True, 'prefix_negative': True})
        provisioner = {'username': 'logical_provisioner', 'password': 'private'}
        self.mock(provider, '_provisioner', return_value=provisioner)
        identity = {'principal_id': 'principal1', 'database_id': 'db-1', 'selected_role': 'bi_viewer',
            'grant_epoch': 4, 'policy_revision': 5, 'username': 'actual', 'password': 'secret',
            'credential_ref': 'principal_ref'}
        return identity, prefix, provisioner

    def test_target_positive_pair_overlaps_with_two_workers_then_caller_proofs_and_journal(self):
        identity, prefix, provisioner = self.verification_stubs()
        self.provider._config['trino']['workers'] = 1
        caller = threading.get_ident()
        executor = self.mock(p, 'ThreadPoolExecutor', wraps=ThreadPoolExecutor)
        for role in ('bi_viewer', 'data_engineer', 'admin'):
            identity.update(selected_role=role, grant_epoch=None if role == 'admin' else 4)
            for write in (True, False):
                with self.subTest(role=role, write=write):
                    barrier = threading.Barrier(2)
                    lock = threading.Lock()
                    events, finished = [], []
                    active = peak = 0
                    def record(name):
                        events.append((name, threading.get_ident()))
                    def sql(statement, supplied):
                        nonlocal active, peak
                        if statement == 'SELECT current_user' or statement.startswith('SHOW SCHEMAS'):
                            self.assertIs(supplied, identity)
                            self.assertIsNone(self.provider._effects.get())
                            with lock:
                                active += 1
                                peak = max(peak, active)
                            barrier.wait(timeout=5)
                            with lock:
                                active -= 1
                                finished.append(statement)
                                record('positive')
                            return [[identity['username']]] if 'current_user' in statement else [
                                [schema] for schema in (('marts',) if role == 'bi_viewer' else p.SCHEMAS)]
                        self.assertEqual(len(finished), 2)
                        self.assertEqual(active, 0)
                        if statement == 'SELECT node_id FROM system.runtime.nodes':
                            self.assertIs(supplied, identity)
                            record('negative')
                            raise p.Rejected('ENGINE_ACCESS_DENIED')
                        self.assertEqual(statement, 'SELECT node_id, coordinator, state FROM system.runtime.nodes')
                        self.assertIs(supplied, provisioner)
                        record('runtime')
                        return [['coordinator', True, 'active'], ['worker', False, 'active']]
                    def marker(db, *, write_marker):
                        self.assertIs(db, self.db)
                        self.assertEqual(write_marker, write)
                        record('prefix')
                        if write_marker:
                            self.provider._effect('put_object', 's3', 'marker', b'marker', lambda: record('put'))
                        return {'prefix_positive': True, 'prefix_negative': True}
                    def journal(key, leaf, dispatch):
                        record('journal')
                        return dispatch()
                    prefix.side_effect = marker
                    self.mock(self.provider, '_sql', side_effect=sql)
                    with self.provider.effects(journal):
                        proof = self.provider._verify_target(self.db, identity, write_marker=write)
                    self.assertTrue(proof['complete'])
                    self.assertTrue(proof['verified'])
                    self.assertTrue(proof['identity_positive'])
                    self.assertTrue(proof['identity_negative'])
                    self.assertEqual(peak, 2)
                    self.assertEqual([name for name, _ in events], ['positive', 'positive', 'negative', 'prefix'] +
                        (['journal', 'put'] if write else []) + ['runtime'])
                    self.assertTrue(all(thread != caller for _, thread in events[:2]))
                    self.assertTrue(all(thread == caller for _, thread in events[2:]))
                    for key, value in identity.items():
                        if key != 'password':
                            self.assertEqual(proof[key], value)
                    self.assertNotIn('password', proof)
        self.assertEqual(executor.call_count, 6)
        self.assertTrue(all(call.kwargs == {'max_workers': 2} for call in executor.call_args_list))

    def test_target_joins_both_positive_tasks_on_either_failure_without_later_reads(self):
        identity, prefix, _ = self.verification_stubs()
        self.provider._config['trino']['workers'] = 1
        for failing in ('SELECT current_user', 'SHOW SCHEMAS FROM "catalog_one"'):
            for error in (p.Rejected('PROVIDER_HTTP_401'), p.Rejected('PROVIDER_HTTP_403'),
                    p.Rejected('ENGINE_ACCESS_DENIED'), p.Rejected('ENGINE_QUERY_FAILED'),
                    p.Unknown(), TimeoutError('inert transport')):
                with self.subTest(failing=failing, error=type(error).__name__, code=getattr(error, 'code', None)):
                    entered = threading.Barrier(2)
                    failed, release = threading.Event(), threading.Event()
                    finished, calls = [], []
                    def sql(statement, supplied):
                        self.assertIs(supplied, identity)
                        self.assertIn(statement, ('SELECT current_user', 'SHOW SCHEMAS FROM "catalog_one"'))
                        calls.append(statement)
                        entered.wait(timeout=5)
                        if statement == failing:
                            failed.set()
                            raise error
                        self.assertTrue(release.wait(5))
                        finished.append(statement)
                        return [['actual']] if 'current_user' in statement else [['marts']]
                    self.mock(self.provider, '_sql', side_effect=sql)
                    # Observe exactly once so the retryable failure's empty proof is inspectable.
                    def once(observe):
                        with self.assertRaises(type(error)) as raised:
                            observe()
                        self.assertIs(raised.exception, error)
                        return False
                    with patch.object(self.provider, '_refresh_read', side_effect=once), ThreadPoolExecutor(max_workers=1) as caller:
                        result = caller.submit(self.provider.verify_target, self.db, identity)
                        try:
                            self.assertTrue(failed.wait(5))
                            self.assertFalse(result.done())
                        finally:
                            release.set()
                        proof = result.result(timeout=5)
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(len(finished), 1)
                    self.assertFalse(proof['identity_positive'])
                    self.assertFalse(proof['identity_negative'])
                    self.assertFalse(proof['verified'])
                    self.assertFalse(proof['complete'])
                    self.assertFalse(proof['prefix_positive'])
                    self.assertFalse(proof['prefix_negative'])
                    self.assertNotIn('runtime_nodes', proof)
                    prefix.assert_not_called()
                    self.provider._provisioner.assert_not_called()

    def test_target_invalid_parallel_positive_never_attempts_negative_prefix_or_runtime(self):
        identity, prefix, _ = self.verification_stubs()
        self.provider._config['trino']['workers'] = 1
        for actual, rows, mismatch in (([['wrong']], [['marts']], True),
                ([['actual'], ['extra']], [['marts']], True), ([['actual']], [], False),
                ([['actual']], [['marts'], ['raw']], False)):
            with self.subTest(actual=actual, rows=rows):
                barrier = threading.Barrier(2)
                calls = []
                def sql(statement, supplied):
                    calls.append(statement)
                    self.assertIn(statement, ('SELECT current_user', 'SHOW SCHEMAS FROM "catalog_one"'))
                    barrier.wait(timeout=5)
                    return actual if 'current_user' in statement else rows
                self.mock(self.provider, '_sql', side_effect=sql)
                def once(observe):
                    if mismatch:
                        with self.assertRaises(p.Rejected) as raised:
                            observe()
                        self.assertEqual(raised.exception.code, 'ENGINE_IDENTITY_MISMATCH')
                        return False
                    return observe()
                with patch.object(self.provider, '_refresh_read', side_effect=once):
                    proof = self.provider.verify_target(self.db, identity)
                self.assertEqual(len(calls), 2)
                for key in ('verified', 'complete', 'identity_positive', 'identity_negative', 'prefix_positive', 'prefix_negative'):
                    self.assertFalse(proof[key])
                self.assertNotIn('runtime_nodes', proof)
                prefix.assert_not_called()
                self.provider._provisioner.assert_not_called()

    def test_target_refresh_discards_partial_positives_and_repeats_fresh_pair(self):
        identity, prefix, _ = self.verification_stubs()
        self.provider._config['trino']['workers'] = 1
        for earlier_positive in (False, True):
            with self.subTest(earlier_positive=earlier_positive):
                attempt = 0
                calls = []
                barriers = [threading.Barrier(2) for _ in range(3)]
                def sql(statement, supplied):
                    current = attempt
                    calls.append((current, statement))
                    if statement == 'SELECT node_id FROM system.runtime.nodes':
                        self.assertTrue(earlier_positive)
                        self.assertEqual(current, 0)
                        return []  # Positive pair alone never proves verification.
                    self.assertIn(statement, ('SELECT current_user', 'SHOW SCHEMAS FROM "catalog_one"'))
                    barriers[current].wait(timeout=5)
                    if 'current_user' in statement:
                        if current == 1:
                            raise p.Rejected('PROVIDER_HTTP_401')
                        return [['actual']]
                    if current == 0 and not earlier_positive:
                        raise p.Rejected('ENGINE_ACCESS_DENIED')
                    return [] if current == 2 else [['marts']]
                def sleep(delay):
                    nonlocal attempt
                    attempt += 1
                self.mock(self.provider, '_sql', side_effect=sql)
                with patch.object(p.time, 'monotonic', side_effect=[0, 0, 0, 5]), \
                        patch.object(p.time, 'sleep', side_effect=sleep):
                    proof = self.provider.verify_target(self.db, identity)
                self.assertEqual(attempt, 2)
                for current in range(3):
                    pair = [statement for a, statement in calls if a == current and statement != 'SELECT node_id FROM system.runtime.nodes']
                    self.assertCountEqual(pair, ['SELECT current_user', 'SHOW SCHEMAS FROM "catalog_one"'])
                self.assertEqual(sum(statement == 'SELECT node_id FROM system.runtime.nodes' for _, statement in calls), int(earlier_positive))
                for key in ('verified', 'complete', 'identity_positive', 'identity_negative', 'prefix_positive', 'prefix_negative'):
                    self.assertFalse(proof[key])
                self.assertNotIn('runtime_nodes', proof)
                prefix.assert_not_called()
                self.provider._provisioner.assert_not_called()

    def test_target_custom_transport_preserves_serial_order_and_visibility_false_negative(self):
        for transport in ('command', 'http', 'sdk', 'popen'):
            provider = p.Provider(self.c, **{transport: Mock()})
            self.addCleanup(provider.close)
            identity, prefix, _ = self.verification_stubs(provider)
            for outcome in ('success', 'visibility_false', 'wrong_user'):
                with self.subTest(transport=transport, outcome=outcome):
                    calls = []
                    def sql(statement, supplied):
                        self.assertEqual(threading.get_ident(), caller)
                        self.assertIs(supplied, identity)
                        calls.append(statement)
                        if 'current_user' in statement:
                            return [['wrong' if outcome == 'wrong_user' else 'actual']]
                        if statement.startswith('SHOW SCHEMAS'):
                            return [] if outcome == 'visibility_false' else [['marts']]
                        raise p.Rejected('ENGINE_ACCESS_DENIED')
                    caller = threading.get_ident()
                    self.mock(provider, '_sql', side_effect=sql)
                    prefix.reset_mock()
                    with patch.object(p, 'ThreadPoolExecutor', side_effect=AssertionError('injected fanout')), \
                            patch.object(provider, '_refresh_read', side_effect=lambda observe: observe()):
                        if outcome == 'wrong_user':
                            with self.assertRaises(p.Rejected) as raised:
                                provider.verify_target(self.db, identity)
                            self.assertEqual(raised.exception.code, 'ENGINE_IDENTITY_MISMATCH')
                        else:
                            proof = provider.verify_target(self.db, identity)
                            self.assertEqual(proof['verified'], outcome == 'success')
                            self.assertEqual(proof['identity_positive'], outcome == 'success')
                            self.assertTrue(proof['identity_negative'])
                    self.assertEqual(calls, ['SELECT current_user'] + ([] if outcome == 'wrong_user' else [
                        'SHOW SCHEMAS FROM "catalog_one"', 'SELECT node_id FROM system.runtime.nodes']))
                    if outcome == 'success':
                        prefix.assert_called_once_with(self.db, write_marker=True)
                    else:
                        prefix.assert_not_called()

    def health(self, provider=None, start=lambda branch: None):
        provider = provider or self.provider
        calls = []
        def record(name, result=None):
            def call(*args, **kwargs):
                calls.append((name, args, kwargs, threading.get_ident()))
                if name in ('opa', 'polaris', 'iam'):
                    start(name)
                return result
            return call
        for method, name, result in (
                ('compute_state', 'state', 'ACTIVE'), ('bound_alive', 'bound', True),
                ('_gate', 'gate', None), ('_stable_health', 'stable', None),
                ('_services', 'services', ('service-a', 'service-b')), ('_owned', 'owned', None),
                ('_opa_health', 'opa', None), ('_compute', 'compute', self.nodes),
                ('_native_health', 'native', None), ('_trino_health', 'trino', None),
                ('_sql_health', 'sql', None), ('_polaris', 'polaris', None),
                ('_iam', 'iam', None), ('_storage', 'storage', None)):
            self.mock(provider, method, side_effect=record(name, result))
        return calls

    def test_health_three_branches_overlap_and_keep_exact_order_and_arguments(self):
        barrier = threading.Barrier(3)
        calls = self.health(start=lambda branch: barrier.wait(timeout=5))
        caller = threading.get_ident()
        executor = self.mock(p, 'ThreadPoolExecutor', wraps=ThreadPoolExecutor)
        self.assertTrue(self.provider.live_health()['ready'])
        executor.assert_called_once_with(max_workers=3)
        prefix = ['state', 'bound', 'gate', 'stable', 'services', 'owned', 'owned']
        self.assertEqual([v[0] for v in calls[:7]], prefix)
        self.assertTrue(all(v[3] == caller for v in calls[:7]))
        groups = {}
        for name, args, kwargs, thread in calls[7:]:
            groups.setdefault(thread, []).append(name)
            self.assertNotEqual(thread, caller)
        self.assertCountEqual(list(groups.values()),
            [['opa', 'compute', 'native', 'trino', 'sql'], ['polaris'], ['iam', 'storage']])
        by_name = {name: (args, kwargs) for name, args, kwargs, _ in calls}
        self.assertEqual(by_name['native'], ((self.nodes,), {}))
        self.assertEqual(by_name['polaris'], (('GET', p.MANAGEMENT+'/principals/root'), {}))
        self.assertEqual(by_name['iam'], (('list_roles',), {'MaxItems': 1}))
        self.assertEqual(by_name['storage'], (('head_bucket',), {'Bucket': self.c['storage']['bucket']}))

    def test_health_joins_other_branches_on_each_failure_and_never_ready(self):
        for failing in ('opa', 'polaris', 'iam'):
            with self.subTest(failing=failing):
                entered = threading.Barrier(3)
                failed, release = threading.Event(), threading.Event()
                completed = []
                def start(branch):
                    entered.wait(timeout=5)
                    if branch == failing:
                        failed.set()
                        raise p.Rejected('SAFE_FAILURE')
                    self.assertTrue(release.wait(5))
                    completed.append(branch)
                self.health(start=start)
                with ThreadPoolExecutor(max_workers=1) as caller:
                    result = caller.submit(self.provider.live_health)
                    try:
                        self.assertTrue(failed.wait(5))
                        self.assertFalse(result.done())
                    finally:
                        release.set()
                    self.assertFalse(result.result(timeout=5)['ready'])
                self.assertCountEqual(completed, set(('opa', 'polaris', 'iam'))-{failing})

    def material_stubs(self, nodes=None, read=None):
        self.mock(self.provider, '_gate')
        self.mock(self.provider, '_compute', return_value=nodes or self.nodes)
        def observe(mode, data=b'', *, reader=None):
            if mode == 'read':
                return dict(self.h.hashes)
            self.assertEqual(mode, 'resolve')
            ref = data.decode().strip().split('=', 1)[1]
            return {'ref.sha256': hashlib.sha256(self.values[ref].encode()).hexdigest()}
        self.mock(self.provider, '_exec', side_effect=read or observe)
        return observe

    def test_material_two_node_bound_overlap_read_once_all_refs_and_deterministic_proof(self):
        entered = threading.Barrier(3)
        release = threading.Event()
        lock = threading.Lock()
        active, maximum, calls = set(), [0], {}
        base = self.material_stubs()
        def read(mode, data=b'', *, reader=None):
            uid = reader['metadata']['uid']
            with lock:
                calls.setdefault(uid, []).append((mode, data, threading.get_ident()))
                if mode == 'read':
                    active.add(uid)
                    maximum[0] = max(maximum[0], len(active))
            if mode == 'read' and uid in ('0', '1'):
                entered.wait(timeout=5)
                self.assertTrue(release.wait(5))
            result = base(mode, data, reader=reader)
            if mode == 'resolve' and data == p._properties({'ref': 'two'}):
                with lock:
                    active.remove(uid)
            return result
        self.provider._exec.side_effect = read
        executor = self.mock(p, 'ThreadPoolExecutor', wraps=ThreadPoolExecutor)
        with ThreadPoolExecutor(max_workers=1) as caller:
            result = caller.submit(self.provider.check_material, self.db, self.material)
            try:
                entered.wait(timeout=5)
                self.assertEqual(set(calls), {'0', '1'})
                self.assertFalse(result.done())
            finally:
                release.set()
            proof = result.result(timeout=5)
        executor.assert_called_once_with(max_workers=2)
        self.assertEqual(maximum[0], 2)
        self.assertEqual(proof, p._proof(self.db, 'publication', True,
            hashes=self.h.hashes, compute_uids=[str(i) for i in range(5)]))
        for node_calls in calls.values():
            self.assertEqual([(mode, data) for mode, data, _ in node_calls],
                [('read', b''), ('resolve', p._properties({'ref': 'one'})),
                 ('resolve', p._properties({'ref': 'two'}))])
            self.assertEqual(len({thread for _, _, thread in node_calls}), 1)

    def test_material_joins_on_node_failure(self):
        entered = threading.Barrier(2)
        failed, release, finished = threading.Event(), threading.Event(), threading.Event()
        def read(mode, data=b'', *, reader=None):
            if mode == 'read':
                entered.wait(timeout=5)
                if reader == self.nodes[0]:
                    failed.set()
                    raise p.Rejected('SAFE_FAILURE')
                self.assertTrue(release.wait(5))
                return self.h.hashes
            if data == p._properties({'ref': 'two'}):
                finished.set()
            return {'ref.sha256': '0'*64}
        self.material_stubs(nodes=self.nodes[:2], read=read)
        with ThreadPoolExecutor(max_workers=1) as caller:
            result = caller.submit(self.provider.check_material, self.db, self.material)
            try:
                self.assertTrue(failed.wait(5))
                self.assertFalse(result.done())
            finally:
                release.set()
            with self.assertRaises(p.Rejected):
                result.result(timeout=5)
        self.assertTrue(finished.is_set())

    def test_parallel_material_strict_hashes_payload_and_every_ref_fail_closed(self):
        base = self.material_stubs(nodes=self.nodes[:2])
        bad_hashes = [None, {}, {**self.h.hashes, 'extra': 'a'*64}]
        for key in self.h.hashes:
            bad_hashes.append({k: v for k, v in self.h.hashes.items() if k != key})
            bad_hashes.extend({**self.h.hashes, key: bad} for bad in ('MISSING', 'A'*64, 'g'*64, 'a'*63, None))
            bad_hashes.append({**self.h.hashes, key: '0'*64})
        for bad in bad_hashes:
            for both in (False, True):
                def read(mode, data=b'', *, reader=None):
                    if mode == 'read' and (both or reader == self.nodes[1]):
                        return bad
                    return base(mode, data, reader=reader)
                self.provider._exec.side_effect = read
                with self.subTest(bad=bad, both=both):
                    proof = self.provider.check_material(self.db, self.material)
                    # An equal valid hash can pass only without a requested artifact.
                    valid_equal = both and isinstance(bad, dict) and set(bad) == set(self.h.hashes) and all(
                        isinstance(v, str) and p.re.fullmatch('[0-9a-f]{64}', v) for v in bad.values())
                    self.assertEqual(proof['verified'], bool(valid_equal))
        self.provider._exec.side_effect = base
        for key, filename in p.TARGETS.items():
            if key == 'credentials':
                continue
            material = {**self.material, key: '{}', 'expected_hashes': {filename: '0'*64}}
            self.assertFalse(self.provider.check_material(self.db, material)['verified'])
        for node in self.nodes[:2]:
            for ref in self.values:
                def read(mode, data=b'', *, reader=None):
                    if mode == 'resolve' and reader == node and data == p._properties({'ref': ref}):
                        return {'ref.sha256': '0'*64}
                    return base(mode, data, reader=reader)
                self.provider._exec.side_effect = read
                self.assertFalse(self.provider.check_material(self.db, self.material)['verified'])

    def test_material_checks_gate_normalization_and_compute_before_pool(self):
        for failing in ('_gate', '_material', '_compute'):
            with self.subTest(failing=failing):
                calls = []
                for method, result in (('_gate', None), ('_material', {}), ('_compute', self.nodes)):
                    def check(*args, name=method, value=result):
                        calls.append(name)
                        if name == failing:
                            raise p.Rejected('SAFE_FAILURE')
                        return value
                    self.mock(self.provider, method, side_effect=check)
                with patch.object(p, 'ThreadPoolExecutor', side_effect=AssertionError('premature pool')), \
                        self.assertRaises(p.Rejected):
                    self.provider.check_material(self.db, self.material)
                expected = ['_gate', '_material', '_compute']
                self.assertEqual(calls, expected[:expected.index(failing)+1])

    def test_polaris_refresh_pair_is_consistent_without_locking_network(self):
        entered = threading.Barrier(3)
        release = threading.Event()
        self.mock(self.provider, '_private', return_value={'client_id': 'root', 'client_secret': 'secret'})
        tokens = {}
        def request(service, method, path, **kwargs):
            if method == 'POST':
                token = str(threading.get_ident())
                ttl = 30 if not tokens else 90
                tokens[token] = ttl
                entered.wait(timeout=5)
                self.assertTrue(release.wait(5))
                return {'access_token': token, 'expires_in': ttl}
            return kwargs['headers']['Authorization']
        self.mock(self.provider, '_request', side_effect=request)
        with patch.object(p.time, 'monotonic', return_value=100), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.provider._polaris, 'GET', '/read') for _ in range(2)]
            try:
                entered.wait(timeout=5)
                acquired = self.provider._lock.acquire(timeout=1)
                self.assertTrue(acquired)
                if acquired:
                    self.provider._lock.release()
            finally:
                release.set()
            self.assertCountEqual([future.result(timeout=5) for future in futures], ['Bearer '+t for t in tokens])
        token = self.provider._tokens['root']
        self.assertEqual(self.provider._token_deadlines['root'], 100+tokens[token]-10)

    def test_each_injected_transport_keeps_health_and_material_on_caller_thread(self):
        for transport in ('command', 'http', 'sdk', 'popen'):
            provider = p.Provider(self.c, **{transport: Mock()})
            self.addCleanup(provider.close)
            calls = self.health(provider)
            with patch.object(p, 'ThreadPoolExecutor', side_effect=AssertionError('injected fanout')):
                self.assertTrue(provider.live_health()['ready'])
                self.assertEqual([v[0] for v in calls], ['state', 'bound', 'gate', 'stable', 'services',
                    'owned', 'owned', 'opa', 'compute', 'native', 'trino', 'sql', 'polaris', 'iam', 'storage'])
                seen = []
                def read(mode, data=b'', *, reader=None):
                    seen.append((reader, mode, threading.get_ident()))
                    return self.h.hashes
                self.mock(provider, '_exec', side_effect=read)
                provider.check_material(self.db, {'credentials': {}})
            self.assertTrue(all(v[3] == threading.get_ident() for v in calls))
            self.assertEqual(seen, [(node, 'read', threading.get_ident()) for node in self.nodes])

    def test_default_material_preserves_native_uid_and_fresh_dispatch_trust_guards(self):
        nodes = [self.h.resource('pod', name+'-pod') for name in ('trino-local', 'trino-local-worker')]
        self.mock(self.provider, '_gate')
        self.mock(self.provider, '_compute', return_value=nodes)
        trust = self.mock(self.provider, '_trust')
        def metadata(args):
            resource = self.h.resource(args[1], args[2])
            if args[1:3] == ['pod', 'trino-local-worker-pod']:
                resource['metadata']['uid'] = 'stale-worker'
            return json.dumps(resource).encode()
        self.mock(self.provider, '_metadata', side_effect=metadata)
        raw = self.mock(self.provider, '_raw', side_effect=lambda argv, input=None, mutation=False:
            self.h.command(argv, input=input))
        self.h.exec_handler = lambda argv, data: ('status=OK\n'+''.join(k+'='+v+'\n' for k, v in
            (self.h.hashes if argv[-1] == 'read' else {'ref.sha256': hashlib.sha256(b'value-one').hexdigest()}).items())).encode()
        with self.assertRaises(p.Rejected) as error:
            self.provider.check_material(self.db, {'credentials': {'one': 'value-one'}})
        self.assertEqual(error.exception.code, 'FOUNDATION_INCARNATION_CHANGED')
        self.assertEqual([call.args[0][-1] for call in raw.call_args_list], ['read', 'resolve'])
        self.assertTrue(all('trino-local-worker-pod' not in call.args[0] for call in raw.call_args_list))
        self.assertEqual(trust.call_count, 2)

    def test_publish_and_effect_journal_stay_on_original_thread_after_fanout(self):
        self.material_stubs()
        self.provider.check_material(self.db, self.material)
        caller = threading.get_ident()
        seen = []
        def journal(key, identity, call):
            seen.append(('journal', threading.get_ident()))
            return call()
        def dispatch(mode, data=b'', **kwargs):
            seen.append((mode, threading.get_ident()))
            if mode == 'publish':
                return self.provider._effect('publish', 'trino-local', '/private', data, lambda: self.h.hashes)
            return self.h.hashes
        self.provider._exec.side_effect = dispatch
        with self.provider.effects(journal):
            self.provider.publish_material(self.db, self.material)
        self.assertEqual(seen, [('read', caller), ('publish', caller), ('journal', caller)])

    def test_owned_concurrent_first_observation_cannot_overwrite_incarnation(self):
        barrier = threading.Barrier(2)
        def command(args):
            resource = self.h.resource('service', 'trino-local')
            resource['metadata']['uid'] = str(threading.get_ident())
            barrier.wait(timeout=5)
            return json.dumps(resource).encode()
        self.mock(self.provider, '_cmd', side_effect=command)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.provider._owned, 'service', 'trino-local') for _ in range(2)]
        results, rejected = [], []
        for future in futures:
            try:
                results.append(future.result()['metadata']['uid'])
            except p.Rejected as error:
                rejected.append(error.code)
        self.assertEqual(rejected, ['FOUNDATION_INCARNATION_CHANGED'])
        self.assertEqual(results, [self.provider._incarnation['service/trino-local']])


if __name__ == '__main__':
    unittest.main()
