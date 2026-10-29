"""DB-P6-2 mock transports and real SQLite: no cluster or listener operations."""
import base64
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch

from tests import test_logical_provider as providers
from tests import test_logical_creations as creations
from tests import test_database_creation_routes as routes
import logical_creations as logical
import logical_provider as provider_module
import logical_foundation as foundation
import logical_registry
import control_store
import runtime_profile
import warehouse_admission as admission
from warehouse_operations import OperationError


class ComputeHarness(providers.Harness):
    """Real rendered specs, with mutable API observations for stop/wake tests."""
    def __init__(self, config):
        super().__init__(config)
        self.stopped = False
        self.ghost = False
        public, private = foundation.render(config)
        self.documents = {(d['kind'].lower(), d['metadata']['name']): d for d in public + private}
        self.change = lambda kind, name, value: None

    def resource(self, kind, name):
        result = super().resource(kind, name)
        desired = self.documents.get((kind, name))
        if desired:
            if kind == 'configmap':
                result.pop('data', None)
                result.pop('binaryData', None)
            for key, value in desired.items():
                if key != 'metadata':
                    result[key] = copy.deepcopy(value)
            if kind == 'secret':
                result['data'] = {k: base64.b64encode(v.encode()).decode()
                    for k, v in result.pop('stringData').items()}
        if self.stopped and kind == 'deployment' and name in ('trino-local', 'trino-local-worker'):
            result['spec']['replicas'] = 0
            result['status'] = {'observedGeneration': result['metadata']['generation']}
        self.change(kind, name, result)
        return result

    def command(self, argv, **kwargs):
        args = argv[argv.index('--request-timeout=15s')+1:]
        if self.stopped and args == ['get', 'pods', '-o', 'json']:
            self.commands.append((argv, kwargs))
            return json.dumps({'items': [self.resource('pod', 'trino-local-pod')] if self.ghost else []}).encode()
        return super().command(argv, **kwargs)


def prepared_config(root):
    c = foundation.validate(providers.settings(root))
    c.update(environment_id='test-environment', warehouse_id='LOCAL', foundation_id='foundation-real', revision=1)
    state = dict(admin=dict(access_key='admin', secret_key='private-admin'),
        issuer=dict(access_key='issuer', secret_key='private-issuer'),
        root=dict(client_id='root', client_secret='private-root'),
        pg_password='pg-secret', signer='signer', shared='shared', volume_max=9)
    password, salt = 'provisioner-password', b'0123456789abcdef'
    digest = hashlib.pbkdf2_hmac('sha1', password.encode(), salt, 100000, 32).hex()
    state['bootstrap'] = {'password.db': 'logical_provisioner:100000:'+salt.hex()+':'+digest+'\n',
        'credentials.properties': '', 'rules.json': '{}', 'guard.rego': 'package shirokuma'}
    for section, key, field in [('storage', 'admin_credentials_file', 'admin'),
            ('storage', 'issuer_credentials_file', 'issuer'), ('polaris', 'root_credentials_file', 'root')]:
        foundation.save_private(c[section][key], json.dumps(state[field]))
    foundation.save_private(c['trino']['provisioner_credentials_file'], json.dumps(dict(version=1,
        username='logical_provisioner', password=password)))
    foundation.save_private(root/'foundation-private.json', json.dumps(state))
    for name in ('jwt-private.pem', 'jwt-public.pem', 'trino-key.pem', 'trino-ca.pem'):
        foundation.save_private(root/name, 'synthetic '+name)
    foundation.save_private(c['trino']['ca_file'], 'synthetic trino-ca.pem')
    foundation.save_private(c['artifacts']['jar'], b'code')
    return c


class StoppedProofTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.root.chmod(0o700)
        self.c = prepared_config(self.root)
        self.h = ComputeHarness(self.c)
        active = self.make_provider()
        active.open()
        self.saved = active.probe()
        self.assertTrue(self.saved['ready'])
        active.close()
        self.h.stopped = True
        self.h.commands.clear()
        self.h.http_calls.clear()
        self.h.sdk_calls.clear()

    def make_provider(self):
        value = provider_module.Provider(self.c, command=self.h.command, http=self.h.http,
            sdk=self.h.sdk, popen=self.h.popen)
        self.addCleanup(value.close)
        return value

    def test_exact_stop_binds_metadata_without_tunnels_runtime_readiness_or_mutations(self):
        value = self.make_provider()
        original = copy.deepcopy(self.saved)
        value.bind_stopped_registered(self.saved)
        self.assertEqual(self.saved, original)
        self.assertFalse(value._opened)
        self.assertFalse(value._startup_verified)
        self.assertFalse(value.bound_alive())
        self.assertEqual(value._children, [])
        self.assertEqual(value._tunnels, {})
        self.assertEqual(self.h.http_calls, [])
        self.assertEqual(self.h.sdk_calls, [])
        self.assertTrue(all('get' in argv or 'config' in argv for argv, _ in self.h.commands))
        descriptor = dict(database_id='arbitrary-db', sql_catalog='arbitrary_catalog',
            generation_id=self.c['foundation_id'], foundation_revision=1)
        for mutate in (value.create_role, value.create_catalog, value.issue_consumer):
            with self.assertRaises(provider_module.Rejected):
                mutate(descriptor)
        self.assertEqual(self.h.sdk_calls, [])
        self.assertEqual(self.h.http_calls, [])

    def test_default_open_and_full_bind_do_not_accept_stop(self):
        value = self.make_provider()
        with self.assertRaises(provider_module.Rejected):
            value.open()
        self.assertEqual(value._children, [])
        value = self.make_provider()
        with self.assertRaises(provider_module.Rejected):
            value.bind_registered(self.saved)
        self.assertFalse(value.bound_alive())

    def test_partial_stop_stale_status_and_live_pods_fail(self):
        for mode in ('partial', 'stale', 'pods', 'ready'):
            def change(kind, name, result):
                if kind == 'deployment' and name == 'trino-local':
                    if mode == 'partial': result['spec']['replicas'] = 1
                    if mode == 'stale': result['status']['observedGeneration'] -= 1
                    if mode == 'ready': result['status']['readyReplicas'] = 1
            self.h.change = change
            self.h.ghost = mode == 'pods'
            with self.subTest(mode=mode), self.assertRaises(provider_module.Rejected):
                self.make_provider().bind_stopped_registered(self.saved)

    def test_terminal_pods_do_not_claim_live_compute(self):
        self.h.ghost = True
        self.h.change = lambda kind, name, r: r['status'].update(phase='Succeeded') if kind == 'pod' else None
        value = self.make_provider()
        value.bind_stopped_registered(self.saved)
        self.assertFalse(value._opened)

    def test_each_mandatory_saved_pin_and_spi_remains_required(self):
        stable = self.make_provider()._stable_pins(self.saved['incarnation'])
        for key in stable:
            broken = copy.deepcopy(self.saved)
            del broken['incarnation'][key]
            with self.subTest(key=key), self.assertRaises(provider_module.Rejected):
                self.make_provider().bind_stopped_registered(broken)
        broken = copy.deepcopy(self.saved)
        broken['spi_sha256'] = 'e'*64
        with self.assertRaises(provider_module.Rejected):
            self.make_provider().bind_stopped_registered(broken)

    def test_trust_spec_config_secret_and_bound_storage_drift_fail(self):
        def deployment(field, value):
            def change(kind, name, result):
                if kind == 'deployment' and name == 'trino-local':
                    result['spec']['template']['spec']['containers'][0][field] = value
            return change
        cases = [deployment('image', 'foreign@sha256:'+'a'*64),
            deployment('securityContext', {'privileged': True}),
            deployment('volumeMounts', []), deployment('command', ['/bin/evil'])]
        def drift(kind_target, name_target, modify):
            return lambda kind, name, result: modify(result) if (kind, name) == (kind_target, name_target) else None
        cases += [drift('deployment', 'trino-local-worker', lambda r: r['spec']['template']['spec']['volumes'].clear()),
            drift('configmap', 'owned-trino-config', lambda r: r['data'].update({'config.properties': 'unsafe'})),
            drift('configmap', 'owned-secrets-code', lambda r: r['binaryData'].update({'logical-secrets.jar': 'YmFk'})),
            drift('secret', 'owned-private', lambda r: r['data'].update({'shared': 'YmFk'})),
            drift('pvc', 'trino-runtime', lambda r: r['status'].update(phase='Pending')),
            drift('pv', 'trino-runtime-pv', lambda r: r['spec']['claimRef'].update(uid='foreign')),
            drift('pv', 'trino-runtime-pv', lambda r: r['spec']['nodeAffinity'].clear()),
            drift('service', 'trino-local', lambda r: r['metadata'].update(uid='replaced')),
            drift('namespace', self.c['kube']['namespace'], lambda r: r['metadata']['labels'].clear())]
        for index, change in enumerate(cases):
            self.h.change = change
            with self.subTest(index=index), self.assertRaises(provider_module.Rejected):
                self.make_provider().bind_stopped_registered(self.saved)
        self.h.change = lambda *args: None
        self.h.node_uid = 'foreign'
        with self.assertRaises(provider_module.Rejected): self.make_provider().bind_stopped_registered(self.saved)
        self.h.node_uid = 'node-uid'
        self.h.view['clusters'][0]['cluster']['certificate-authority-data'] = base64.b64encode(b'foreign').decode()
        with self.assertRaises(provider_module.Rejected): self.make_provider().bind_stopped_registered(self.saved)

    def test_configured_host_secret_drift_and_unavailable_observation_fail(self):
        Path(self.c['storage']['issuer_credentials_file']).write_text(json.dumps(dict(access_key='wrong', secret_key='wrong')))
        with self.assertRaises(provider_module.Rejected): self.make_provider().bind_stopped_registered(self.saved)
        with patch.object(self.h, 'command', side_effect=TimeoutError('unavailable')):
            with self.assertRaises(provider_module.Rejected): self.make_provider().bind_stopped_registered(self.saved)

    def test_default_metadata_transport_allows_only_the_two_fixed_secrets(self):
        for name in ('owned-private', 'owned-bootstrap'):
            path, _ = provider_module._KubeMetadata._target(['get', 'secret', name, '-o', 'json'], self.c['kube'], 'owned')
            self.assertTrue(path.endswith('/secrets/'+name))
        with self.assertRaises(provider_module.Rejected):
            provider_module._KubeMetadata._target(['get', 'secret', 'foreign', '-o', 'json'], self.c['kube'], 'owned')


class LogicalLifecycleTests(creations.LogicalFixture):
    request = routes.CreationRouteTests.request

    @contextmanager
    def actual_lifetime(self):
        directory = tempfile.TemporaryDirectory(dir=self.directory)
        self.addCleanup(directory.cleanup)
        c = prepared_config(Path(directory.name))
        h = ComputeHarness(c)
        value = provider_module.Provider(c, command=h.command, http=h.http, sdk=h.sdk, popen=h.popen)
        value.open()
        saved = value.probe()
        self.assertTrue(saved['ready'])
        value.close()
        # A new HTTP process owns a fresh unopened Provider, saved proof unchanged.
        value = provider_module.Provider(c, command=h.command, http=h.http, sdk=h.sdk, popen=h.popen)
        proof = logical._receipt(dict(kind='foundation_probe', generation_id=c['foundation_id'], revision=1, result=saved))
        with logical.transaction() as db:
            db.execute('UPDATE logical_foundations SET verification_ref=?', (proof,))
        h.stopped = True
        with patch('logical_provider.Provider', return_value=value), \
                patch('logical_foundation.load', return_value=c), \
                patch.object(logical, 'enabled', return_value=True), \
                patch.object(logical, 'dispatch_async'):
            with logical.lifetime(refresh_interval=10):
                yield value, h, saved

    def test_http_restart_asleep_metadata_and_active_lazy_create_with_owned_tunnels(self):
        with self.actual_lifetime() as (value, h, saved):
            self.assertFalse(value._opened)
            for _ in range(2):
                status, caps, _ = self.request('/capabilities')
                self.assertEqual(status, 200)
                self.assertEqual(caps['warehouse_state'], 'STOPPED')
                self.assertEqual(caps['blocker'], 'WAREHOUSE_START_REQUIRED')
            status, error, _ = self.request(body=dict(sql_catalog='sleeping_create'))
            self.assertEqual((status, error['error']), (409, 'WAREHOUSE_START_REQUIRED'))
            self.assertFalse(value._opened)
            children = len(h.children)
            h.stopped = False  # External verified Wake; coordinator never scales.
            status, ack, _ = self.request(body=dict(sql_catalog='after_wake'))
            self.assertEqual(status, 202)
            self.assertTrue(value.bound_alive())
            self.assertEqual(len(h.children), children+3)
            self.assertEqual(value._bound_pins, value._stable_pins(saved['incarnation']))
            self.assertEqual(self.request('/'+ack['creation_id'])[0], 200)
            self.assertFalse(any('scale' in argv for argv, _ in h.commands))
        self.assertFalse(value._opened)

    def test_capabilities_alone_repairs_dormant_owner_after_another_process_wakes(self):
        with self.actual_lifetime() as (value, h, saved):
            with control_store.connect() as db:
                before = {table: [tuple(r) for r in db.execute('SELECT * FROM '+table)]
                    for table in ('logical_requests', 'logical_effects', 'db_creations', 'logical_foundations')}
            children = len(h.children)
            status, caps, _ = self.request('/capabilities')
            self.assertEqual(status, 200)
            self.assertEqual(caps['warehouse_state'], 'STOPPED')
            self.assertFalse(caps['can_create'])
            self.assertFalse(value._opened)
            self.assertEqual(len(h.children), children)
            # Represents the shared physical state after native's verified Wake.
            # This Workbench process has received no POST or lifecycle callback.
            h.stopped = False
            status, caps, _ = self.request('/capabilities')
            self.assertEqual(status, 200)
            self.assertEqual(caps['warehouse_state'], 'ACTIVE')
            self.assertTrue(caps['available'])
            self.assertTrue(caps['can_create'])
            self.assertTrue(caps['can_resume'])
            self.assertIsNone(caps['blocker'])
            self.assertTrue(value.bound_alive())
            self.assertEqual(value._bound_pins, value._stable_pins(saved['incarnation']))
            self.assertEqual(len(h.children), children+3)
            calls = (len(h.http_calls), len(h.sdk_calls))
            self.assertTrue(self.request('/capabilities')[1]['can_create'])
            self.assertEqual((len(h.http_calls), len(h.sdk_calls)), calls)
            self.assertFalse(any('scale' in argv for argv, _ in h.commands))
            self.assertFalse(any(method == 'POST' and url.endswith(('/principals', '/catalogs'))
                for method, url, _ in h.http_calls))
            with control_store.connect() as db:
                after = {table: [tuple(r) for r in db.execute('SELECT * FROM '+table)] for table in before}
            self.assertEqual(after, before)

    def test_capabilities_cannot_repair_partial_trust_or_saved_proof_failures(self):
        for mode in ('partial', 'trust', 'uid', 'engine'):
            with self.subTest(mode=mode), self.actual_lifetime() as (value, h, saved):
                h.stopped = False
                children = len(h.children)
                if mode == 'partial':
                    h.change = lambda kind, name, r: r['spec'].update(replicas=0) if kind == 'deployment' and name == 'trino-local' else None
                elif mode == 'trust':
                    h.node_uid = 'foreign'
                elif mode == 'uid':
                    h.change = lambda kind, name, r: r['metadata'].update(uid='replacement') if kind == 'service' and name == 'trino-local' else None
                else:
                    h.http_handler = lambda *args, **kwargs: (503, b'{}')
                status, caps, _ = self.request('/capabilities')
                self.assertEqual(status, 200)
                self.assertFalse(caps['can_create'])
                self.assertFalse(caps['available'])
                self.assertEqual(caps['blocker'], 'FOUNDATION_NOT_READY')
                self.assertFalse(value.bound_alive())
                self.assertFalse(value._opened)
                if mode in ('partial', 'trust'):
                    self.assertEqual(len(h.children), children)
                self.assertFalse(any('scale' in argv for argv, _ in h.commands))
                with control_store.connect() as db:
                    self.assertEqual(logical._registered_probe(logical._foundation(db)), saved)

    def test_capabilities_checks_current_permissions_before_dormant_owner_repair(self):
        for denied in ('role', 'platform', 'requester', 'executor'):
            with self.subTest(denied=denied), self.actual_lifetime() as (value, h, saved):
                h.stopped = False
                children = len(h.children)
                with patch.object(logical, 'ensure_active_provider', wraps=logical.ensure_active_provider) as repair:
                    if denied == 'role':
                        self.assertEqual(self.request('/capabilities', role='bi_viewer')[0], 403)
                    elif denied == 'platform':
                        self.accounts['alice']['platform_admin'] = False
                        self.save_accounts()
                        try:
                            self.assertEqual(self.request('/capabilities')[0], 403)
                        finally:
                            self.accounts['alice']['platform_admin'] = True
                            self.save_accounts()
                    else:
                        actor = 'alice' if denied == 'requester' else 'executor'
                        with logical.transaction() as db:
                            logical._gate(db, actor)
                            db.execute("UPDATE db_creation_principal_gates SET state='STOPPED' WHERE principal_id=?", (actor,))
                        try:
                            status, caps, _ = self.request('/capabilities')
                            self.assertEqual(status, 200)
                            self.assertFalse(caps['can_create'])
                            self.assertEqual(caps['blocker'], 'PRINCIPAL_STOPPED')
                        finally:
                            with logical.transaction() as db:
                                db.execute("UPDATE db_creation_principal_gates SET state='ACTIVE' WHERE principal_id=?", (actor,))
                    repair.assert_not_called()
                self.assertFalse(value._opened)
                self.assertEqual(len(h.children), children)

    def test_repair_waits_for_inflight_credential_post_for_create_and_refresh(self):
        for action in ('CREATE', 'AUTH_REFRESH'):
            for dead_service in ('trino-local', 'owned-seaweed', 'forced'):
                with self.subTest(action=action, dead_service=dead_service):
                    name = 'inflight_' + logical._id()[:12]
                    ack, _ = self.logical_create(name, name)
                    request_id = ack['request_id']
                    if action == 'AUTH_REFRESH':
                        logical.dispatch(request_id)
                        dbid = self.detail(ack)['database_id']
                        with logical.transaction() as db:
                            request_id = logical_registry.request_auth_refresh(db, 'executor', 'bob', None,
                                dict(database_id=dbid, reason='ACCOUNT_AUTHORITY_CHANGED', idempotency_key=name))
                    with self.actual_lifetime() as (value, h, saved):
                        h.stopped = False
                        logical.ensure_active_provider()
                        old = {}
                        entered, release, returned, waiting = [threading.Event() for _ in range(4)]
                        posts, closes = [], []
                        close = value.close
                        def record_close():
                            closes.append(returned.is_set())
                            close()
                        def http(method, url, **kwargs):
                            if method == 'POST' and url.endswith('/principals'):
                                posts.append(url)
                                entered.set()
                                self.assertTrue(release.wait(5))
                                # This is the only response to the credential POST.
                                # A repair must not close its good Polaris tunnel.
                                self.assertIsNone(old['owned-polaris'].poll())
                                returned.set()
                                return 201, b'{"credentials":{"clientId":"new-client","clientSecret":"new-secret"}}'
                            handler, h.http_handler = h.http_handler, None
                            try:
                                return h.http(method, url, **kwargs)
                            finally:
                                h.http_handler = handler
                        def credential_leg(request):
                            # Exercise real dispatch, claims, nested durable leaf
                            # ACKs and Provider HTTP, isolating the credential leg.
                            old.update(zip(value._tunnels, value._children))
                            with control_store.connect() as db:
                                descriptor = logical._descriptor(db, logical._root(db, request['creation_id']))
                            result = logical._effect(request, 'credential-race', descriptor,
                                lambda: value.issue_consumer(descriptor))
                            self.assertEqual(result['client_id'], 'new-client')
                            with logical.transaction() as db:
                                logical._assert_claim(db, request)
                                db.execute("UPDATE logical_requests SET dispatch_state='COMPLETE' WHERE id=?", (request['id'],))
                        def new_create():
                            waiting.set()
                            if dead_service == 'forced':
                                logical.ensure_active_provider(force_rebind=True)
                            return self.logical_create(name+'_next', name+'_next')
                        h.http_handler = http
                        with patch.object(logical, '_run', side_effect=credential_leg), \
                                patch.object(logical, '_run_refresh', side_effect=credential_leg), \
                                patch.object(value, 'close', side_effect=record_close), \
                                ThreadPoolExecutor(max_workers=3) as pool:
                            work = pool.submit(logical.dispatch, request_id)
                            try:
                                self.assertTrue(entered.wait(5))
                                if dead_service != 'forced':
                                    old[dead_service].returncode = 1
                                repair = pool.submit(new_create)
                                self.assertTrue(waiting.wait(5))
                                caps = pool.submit(logical.capabilities, self.alice, 'admin')
                                with control_store.connect() as db:
                                    db.execute('PRAGMA busy_timeout=100')
                                    db.execute('BEGIN IMMEDIATE')
                                    self.assertEqual(db.execute('SELECT dispatch_state FROM logical_requests WHERE id=?',
                                        (request_id,)).fetchone()[0], 'CLAIMED')
                                self.assertFalse(repair.done())
                                self.assertFalse(returned.wait(.1))
                                self.assertEqual(closes, [False] if action == 'AUTH_REFRESH' else [])
                                if dead_service != 'forced':
                                    self.assertFalse(caps.done())
                                self.assertIsNone(old['owned-polaris'].poll())
                            finally:
                                release.set()
                            work.result(5)
                            next_ack, _ = repair.result(5)
                            self.assertTrue(caps.result(5)['can_create'])
                        self.assertEqual(len(posts), 1)
                        self.assertEqual(closes, [False, True] if action == 'AUTH_REFRESH' else [True])
                        self.assertTrue(value.bound_alive())
                        self.assertEqual(value._bound_pins, value._stable_pins(saved['incarnation']))
                        self.assertTrue(all(child.poll() is not None for child in old.values()))
                        with control_store.connect() as db:
                            self.assertEqual(db.execute('SELECT dispatch_state FROM logical_requests WHERE id=?',
                                (request_id,)).fetchone()[0], 'COMPLETE')
                            effects = db.execute("SELECT state FROM logical_effects WHERE creation_id=? AND effect_key LIKE 'credential-race%'",
                                (ack['creation_id'],)).fetchall()
                            self.assertEqual([r[0] for r in effects], ['ACKNOWLEDGED', 'ACKNOWLEDGED'])
                        logical.dispatch(request_id)
                        self.assertEqual(len(posts), 1)
                        self.assertEqual(self.detail(next_ack)['progress_code'], 'CREATING')

    def test_dispatch_waits_for_provider_use_before_claim_without_holding_sqlite_writer(self):
        ack, _ = self.logical_create('claim_wait')
        arrived = threading.Event()
        use = logical._provider_use
        @contextmanager
        def observed_use():
            arrived.set()
            with use():
                yield
        with patch.object(logical, '_provider_use', observed_use), ThreadPoolExecutor(max_workers=1) as pool:
            with logical._provider_lock:
                work = pool.submit(logical.dispatch, ack['request_id'])
                self.assertTrue(arrived.wait(5))
                with control_store.connect() as db:
                    db.execute('PRAGMA busy_timeout=100')
                    db.execute('BEGIN IMMEDIATE')
                    self.assertEqual(db.execute('SELECT dispatch_state FROM logical_requests WHERE id=?',
                        (ack['request_id'],)).fetchone()[0], 'ACCEPTED')
            work.result(5)
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')

    def test_wake_rebinds_own_dead_children_once_under_concurrency_and_keeps_saved_pins(self):
        with self.actual_lifetime() as (value, h, saved):
            h.stopped = False
            logical.ensure_active_provider()
            old = list(value._children)
            old[-1].returncode = 1
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(lambda _: logical.ensure_active_provider(), range(3)))
            self.assertTrue(all(c.terminated for c in old[:-1]))
            self.assertEqual(len(h.children), 9)  # preparatory 3, cold 3, repaired 3
            self.assertTrue(value.bound_alive())
            self.assertEqual(value._bound_pins, value._stable_pins(saved['incarnation']))

    def test_forced_wake_rebind_replaces_alive_stale_tunnel_before_first_metrics(self):
        import warehouse_lifecycle as lifecycle
        with self.actual_lifetime() as (value, h, saved):
            h.stopped = False
            logical.ensure_active_provider()
            old = list(value._children)
            stale = value._tunnels['trino-local']
            calls = []
            def http(method, url, **kwargs):
                calls.append((method, url))
                if url.startswith(stale+'/'):
                    raise ConnectionError('old Pod tunnel with locally live child')
                if url.endswith('/metrics'):
                    return 200, (lifecycle._METRIC_ALL+' 0\n'+lifecycle._METRIC_EXPIRED+' 0\n').encode()
                handler, h.http_handler = h.http_handler, None
                try:
                    return h.http(method, url, **kwargs)
                finally:
                    h.http_handler = handler
            h.http_handler = http
            # Physical Chill/Wake replaced the Pod, but kubectl hasn't noticed.
            self.assertTrue(value.bound_alive())
            count = len(h.children)
            logical.ensure_active_provider()  # Usual health stays cheap/nonforced.
            self.assertEqual(len(h.children), count)
            self.assertEqual(calls, [])
            self.assertIs(lifecycle._repair_provider(), value)
            self.assertTrue(all(child.terminated for child in old))
            self.assertEqual(len(h.children), count+3)
            self.assertNotEqual(value._tunnels['trino-local'], stale)
            self.assertTrue(value.bound_alive())
            self.assertEqual(value._bound_pins, value._stable_pins(saved['incarnation']))
            self.assertEqual(lifecycle._active_engine_queries(value), 0)
            self.assertEqual(sum(url.endswith('/metrics') for _, url in calls), 1)
            self.assertFalse(any(url.startswith(stale+'/') for _, url in calls))
            self.assertFalse(any('scale' in argv for argv, _ in h.commands))
            with control_store.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM logical_effects').fetchone()[0], 0)
                self.assertEqual(logical._registered_probe(logical._foundation(db)), saved)

    def test_forced_rebind_of_locally_alive_owner_still_rejects_saved_pin_drift(self):
        with self.actual_lifetime() as (value, h, saved):
            h.stopped = False
            logical.ensure_active_provider()
            self.assertTrue(value.bound_alive())
            h.change = lambda kind, name, r: r['metadata'].update(uid='replacement') if kind == 'service' and name == 'trino-local' else None
            with self.assertRaises(OperationError):
                logical.ensure_active_provider(force_rebind=True)
            self.assertFalse(value._opened)
            self.assertFalse(value.bound_alive())
            self.assertEqual(value._children, [])
            with control_store.connect() as db:
                self.assertEqual(logical._registered_probe(logical._foundation(db)), saved)

    def test_wake_full_probe_failure_and_replaced_uid_cannot_be_adopted(self):
        for mode in ('uid', 'engine', 'spi'):
            with self.subTest(mode=mode), self.actual_lifetime() as (value, h, saved):
                h.stopped = False
                if mode == 'uid':
                    h.change = lambda kind, name, r: r['metadata'].update(uid='replacement') if kind == 'service' and name == 'trino-local' else None
                elif mode == 'engine':
                    h.http_handler = lambda *args, **kwargs: (503, b'{}')
                else:
                    h.change = lambda kind, name, r: r['data'].update(sha256='f'*64) if kind == 'configmap' and name == 'owned-secrets-code' else None
                with self.assertRaises(OperationError): logical.ensure_active_provider()
                self.assertFalse(value.bound_alive())
                self.assertFalse(value._opened)
                self.assertEqual(value._children, [])
                with control_store.connect() as db:
                    self.assertEqual(logical._registered_probe(logical._foundation(db)), saved)

    def test_failed_probe_closes_children_and_a_later_explicit_attempt_can_repair(self):
        with self.actual_lifetime() as (value, h, saved):
            h.stopped = False
            h.http_handler = lambda *args, **kwargs: (503, b'{}')
            with self.assertRaises(OperationError): logical.ensure_active_provider()
            self.assertFalse(value._opened)
            self.assertEqual(value._children, [])
            h.http_handler = None
            logical.ensure_active_provider()
            self.assertTrue(value.bound_alive())
            self.assertEqual(value._bound_pins, value._stable_pins(saved['incarnation']))

    def test_ensure_without_lifetime_does_not_invent_provider(self):
        with patch.object(logical, '_provider', None), patch('logical_provider.Provider') as constructor:
            self.assertIsNone(logical.ensure_active_provider())
            constructor.assert_not_called()

    def test_refresh_after_peer_wake_rebinds_before_claim_and_verifies_revoked_identity(self):
        import warehouse_lifecycle as lifecycle
        import warehouse_resident as resident
        ack, _ = self.logical_create('peer_wake_refresh')
        logical.dispatch(ack['request_id'])
        dbid = self.detail(ack)['database_id']
        self.accounts['bob']['disabled'] = True
        self.save_accounts()
        with logical.transaction() as db:
            request_id = logical_registry.request_auth_refresh(db, 'executor', 'bob', None,
                dict(database_id=dbid, reason='ACCOUNT_AUTHORITY_CHANGED', idempotency_key='peer-wake'))
        with self.actual_lifetime() as (value, h, saved):
            h.stopped = False
            logical.ensure_active_provider()
            old = list(value._children)
            stale = value._tunnels['trino-local']
            calls, credentials = [], {}
            def http(method, url, **kwargs):
                calls.append((method, url, kwargs))
                if url.startswith(stale+'/'):
                    raise ConnectionError('old Pod tunnel with locally live child')
                if method == 'POST' and url.endswith('/v1/statement'):
                    statement = kwargs['data'].decode()
                    user = kwargs['headers']['X-Trino-User']
                    if statement == 'SELECT current_user':
                        return 200, json.dumps({'data': [[user]]}).encode()
                    if statement.startswith('SHOW SCHEMAS FROM '):
                        if user != 'logical_provisioner':
                            return 403, b'{}'
                        return 200, json.dumps({'data': [[s] for s in provider_module.SCHEMAS]}).encode()
                handler, h.http_handler = h.http_handler, None
                try:
                    return h.http(method, url, **kwargs)
                finally:
                    h.http_handler = handler
            def publisher(argv, data):
                # Model the Java publisher's immutable credentials and file hashes.
                fields = dict(line.split('=', 1) for line in (data or b'').decode().splitlines())
                fields = {k: re.sub(r'\\(.)', r'\1', v) for k, v in fields.items()}
                mode = argv[-1]
                if mode == 'publish':
                    credentials.update({k.removeprefix('credential.'): v for k, v in fields.items()
                        if k.startswith('credential.')})
                    for key, filename in provider_module.TARGETS.items():
                        if key != 'credentials' and key in fields:
                            h.hashes['target.'+filename] = hashlib.sha256(base64.b64decode(fields[key])).hexdigest()
                result = ({'ref.sha256': hashlib.sha256(credentials[fields['ref']].encode()).hexdigest()}
                    if mode == 'resolve' else h.hashes)
                return ('status=OK\n'+''.join(k+'='+v+'\n' for k, v in result.items())).encode()
            h.http_handler, h.exec_handler = http, publisher
            bind = value.bind_registered
            bindings = []
            def preclaim_bind(proof):
                with control_store.connect() as db:
                    self.assertEqual(db.execute('SELECT dispatch_state FROM logical_requests WHERE id=?',
                        (request_id,)).fetchone()[0], 'ACCEPTED')
                    self.assertEqual(db.execute("SELECT count(*) FROM logical_effects WHERE effect_key LIKE 'refresh/%'").fetchone()[0], 0)
                self.assertTrue(all(child.terminated for child in old))
                bind(proof)
                bindings.append(proof)
            # The peer's Wake did not inform this process's Provider or SQL cache.
            self.assertTrue(value.bound_alive())
            local_epoch, sql_cache = object(), object()
            with patch.object(value, 'bind_registered', side_effect=preclaim_bind), \
                    patch.object(lifecycle, '_LOCAL_PROOF_ID', local_epoch), \
                    patch.object(lifecycle, '_adopt_proof') as adopt, \
                    patch.object(lifecycle, '_save_proof') as save, \
                    patch.object(resident, '_PERSISTENT_TRINO', sql_cache), \
                    patch.object(resident, 'invalidate_persistent_trino') as invalidate:
                logical.dispatch(request_id)
                self.assertIs(lifecycle._LOCAL_PROOF_ID, local_epoch)
                self.assertIs(resident._PERSISTENT_TRINO, sql_cache)
                adopt.assert_not_called()
                save.assert_not_called()
                invalidate.assert_not_called()
            self.assertEqual(bindings, [saved])
            self.assertTrue(value.bound_alive())
            self.assertNotEqual(value._tunnels['trino-local'], stale)
            self.assertFalse(any(url.startswith(stale+'/') for _, url, _ in calls))
            self.assertTrue(any((options.get('data') or b'').startswith(b'SHOW SCHEMAS FROM ')
                and options['headers']['X-Trino-User'] != 'logical_provisioner' for _, _, options in calls))
            with control_store.connect() as db:
                self.assertEqual(db.execute('SELECT dispatch_state FROM logical_requests WHERE id=?',
                    (request_id,)).fetchone()[0], 'COMPLETE')
                revoked = db.execute("SELECT state FROM logical_effects WHERE creation_id=? AND effect_key GLOB ?",
                    (ack['creation_id'], 'refresh/'+request_id+'/revoked_*')).fetchall()
                self.assertTrue(revoked)
                self.assertTrue(all(row[0] == 'ACKNOWLEDGED' for row in revoked))
                self.assertEqual(logical._registered_probe(logical._foundation(db)), saved)
            before = (len(calls), len(h.children))
            for state in ('COMPLETE', 'BLOCKED', 'UNKNOWN'):
                with self.subTest(dispatch_state=state), logical.transaction() as db:
                    db.execute('UPDATE logical_requests SET dispatch_state=? WHERE id=?', (state, request_id))
                with patch.object(logical, 'ensure_active_provider', wraps=logical.ensure_active_provider) as repair:
                    logical.dispatch(request_id)
                    repair.assert_not_called()
                self.assertEqual((len(calls), len(h.children)), before)

    def test_refresh_stopped_or_failed_forced_binding_stays_unclaimed_without_effects(self):
        for mode in ('stopped', 'failed'):
            with self.subTest(mode=mode):
                ack, _ = self.logical_create('parked_refresh_'+mode, 'parked-refresh-'+mode)
                logical.dispatch(ack['request_id'])
                with logical.transaction() as db:
                    request_id = logical_registry.request_auth_refresh(db, 'executor', 'bob', None,
                        dict(database_id=self.detail(ack)['database_id'], reason='ACCOUNT_AUTHORITY_CHANGED',
                            idempotency_key='parked-'+mode))
                with self.actual_lifetime() as (value, h, saved):
                    if mode == 'failed':
                        h.stopped = False
                        logical.ensure_active_provider()
                        self.assertTrue(value.bound_alive())
                        h.http_handler = lambda *args, **kwargs: (503, b'{}')
                    before = len(h.children)
                    h.http_calls.clear()
                    h.sdk_calls.clear()
                    with patch.object(value, 'open', wraps=value.open) as opened, \
                            patch.object(logical, '_run_refresh') as refresh:
                        logical.dispatch(request_id)
                        refresh.assert_not_called()
                        self.assertEqual(opened.call_count, 0 if mode == 'stopped' else 1)
                    self.assertEqual(len(h.children), before if mode == 'stopped' else before+3)
                    self.assertFalse(value.bound_alive())
                    if mode == 'stopped':
                        self.assertEqual(h.sdk_calls, [])
                        self.assertEqual(h.http_calls, [])
                    else:
                        self.assertTrue(all(method in ('sts', 'get_caller_identity') for method, _ in h.sdk_calls))
                    with control_store.connect() as db:
                        row = db.execute('SELECT dispatch_state,claim_id FROM logical_requests WHERE id=?',
                            (request_id,)).fetchone()
                        self.assertEqual(tuple(row), ('ACCEPTED', None))
                        self.assertEqual(db.execute("SELECT count(*) FROM logical_effects WHERE creation_id=? AND effect_key LIKE 'refresh/%'",
                            (ack['creation_id'],)).fetchone()[0], 0)
                        self.assertEqual(logical._registered_probe(logical._foundation(db)), saved)

    def test_sleep_refresh_is_accepted_without_effect_then_active_binding_uses_existing_dispatch(self):
        ack, _ = self.logical_create('refreshable')
        logical.dispatch(ack['request_id'])
        dbid = self.detail(ack)['database_id']
        self.provider.warehouse_state = 'STOPPED'
        self.provider.bound = False
        self.accounts['bob']['disabled'] = True
        self.save_accounts()
        baseline = copy.deepcopy(self.accounts)
        baseline['bob']['disabled'] = False
        before = list(self.provider.calls)
        logical._enqueue_account_changes(baseline)
        with control_store.connect() as db:
            row = dict(db.execute("SELECT * FROM logical_requests WHERE action='AUTH_REFRESH' AND json_extract(pins_json,'$.target_principal_id')='bob'").fetchone())
            self.assertEqual(row['dispatch_state'], 'ACCEPTED')
            self.assertEqual(logical.compute_blockers(db), dict(requests=1, effects=0, roots=0))
            self.assertEqual(db.execute("SELECT count(*) FROM logical_effects WHERE effect_key LIKE 'refresh/%'").fetchone()[0], 0)
        self.assertEqual(self.provider.calls, before)
        self.provider.warehouse_state = 'ACTIVE'
        self.provider.verify_revoked = lambda *args: dict(verified=True, control_positive=True, identity_denied=True)
        logical._enqueue_account_changes(self.accounts)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT dispatch_state FROM logical_requests WHERE id=?', (row['id'],)).fetchone()[0], 'COMPLETE')
            self.assertEqual(logical.compute_blockers(db), dict(requests=0, effects=0, roots=0))
        self.assertEqual(len(self.provider.binds), 1)
        self.assertEqual(self.provider.calls.count('consumer'), before.count('consumer'))
        calls = list(self.provider.calls)
        logical.dispatch(row['id'])
        self.assertEqual(self.provider.calls, calls)
        self.assertTrue(dbid)

    def test_unknown_refresh_effect_is_never_replayed_after_sleep_or_wake(self):
        ack, _ = self.logical_create('uncertain_refresh')
        logical.dispatch(ack['request_id'])
        dbid = self.detail(ack)['database_id']
        with logical.transaction() as db:
            request_id = logical_registry.request_auth_refresh(db, 'executor', 'bob', None,
                dict(database_id=dbid, reason='ACCOUNT_AUTHORITY_CHANGED', idempotency_key='unknown'))
            now = admission._now()
            db.execute("INSERT INTO logical_effects (creation_id,effect_key,intent_json,state,created_at,updated_at) VALUES (?,'refresh/uncertain','{}','UNKNOWN',?,?)", (ack['creation_id'], now, now))
            db.execute("UPDATE logical_requests SET dispatch_state='UNKNOWN' WHERE id=?", (request_id,))
        before = list(self.provider.calls)
        with patch.object(logical, 'ensure_active_provider', wraps=logical.ensure_active_provider) as repair:
            for state in ('STOPPED', 'ACTIVE'):
                self.provider.warehouse_state = state
                logical.dispatch(request_id)
                logical._enqueue_account_changes(self.accounts)
            repair.assert_not_called()
        self.assertEqual(self.provider.calls, before)
        with control_store.connect() as db:
            self.assertEqual(logical.compute_blockers(db), dict(requests=1, effects=1, roots=0))

    def test_blockers_cover_phase_gaps_stopped_roots_and_scope_without_reservations(self):
        ack, _ = self.logical_create('phase_gaps')
        root = ack['creation_id']
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT reservation_active FROM db_creations').fetchone()[0], 0)
            self.assertEqual(logical.compute_blockers(db), dict(requests=1, effects=0, roots=1))
        with logical.transaction() as db:
            db.execute("UPDATE logical_requests SET dispatch_state='COMPLETE'")
        for state in ('CREATING', 'VERIFYING', 'UNKNOWN'):
            with logical.transaction() as db: db.execute('UPDATE db_creations SET logical_state=?', (state,))
            with control_store.connect() as db: self.assertEqual(logical.compute_blockers(db)['roots'], 1)
        for state in ('ACCEPTED', 'CLAIMED', 'UNKNOWN'):
            with logical.transaction() as db:
                db.execute("UPDATE db_creations SET logical_state='STOPPED'")
                db.execute('UPDATE logical_requests SET dispatch_state=?', (state,))
            with control_store.connect() as db: self.assertEqual(logical.compute_blockers(db), dict(requests=1, effects=0, roots=0))
        with logical.transaction() as db:
            db.execute("UPDATE logical_requests SET dispatch_state='COMPLETE'")
            now = admission._now()
            db.execute("INSERT INTO logical_effects (creation_id,effect_key,intent_json,state,created_at,updated_at) VALUES (?,'uncertain','{}','INTENT',?,?)", (root, now, now))
        for state in ('INTENT', 'UNKNOWN'):
            with logical.transaction() as db: db.execute('UPDATE logical_effects SET state=?', (state,))
            with control_store.connect() as db: self.assertEqual(logical.compute_blockers(db), dict(requests=0, effects=1, roots=0))
        for field, value in (('environment_id', 'foreign'), ('warehouse_id', 'foreign')):
            with logical.transaction() as db:
                old = db.execute('SELECT '+field+' FROM db_creations').fetchone()[0]
                db.execute('UPDATE db_creations SET '+field+'=?', (value,))
            with control_store.connect() as db: self.assertEqual(logical.compute_blockers(db), dict(requests=0, effects=0, roots=0))
            with logical.transaction() as db: db.execute('UPDATE db_creations SET '+field+'=?', (old,))

    def test_create_health_to_commit_serializes_exclusive_stop_without_sql_write_over_network(self):
        in_health, finish_health, stop_waiting, stopped = [threading.Event() for _ in range(4)]
        health = self.provider.live_health
        def paused_health():
            result = health()  # Acquires an independent writer, proving no write tx.
            in_health.set()
            self.assertTrue(finish_health.wait(5))
            return result
        def chill():
            stop_waiting.set()
            with admission.lifecycle_gate(exclusive=True):
                with control_store.connect() as db: counts = logical.compute_blockers(db)
                stopped.set()
                return counts
        with patch.object(self.provider, 'live_health', side_effect=paused_health), ThreadPoolExecutor(max_workers=2) as pool:
            create = pool.submit(self.logical_create, 'race_winner')
            self.assertTrue(in_health.wait(5))
            stop = pool.submit(chill)
            self.assertTrue(stop_waiting.wait(5))
            self.assertFalse(stopped.wait(.1))
            finish_health.set()
            ack, _ = create.result(5)
            self.assertEqual(stop.result(5), dict(requests=1, effects=0, roots=1))
        self.assertEqual(self.detail(ack)['progress_code'], 'CREATING')

    def test_stop_wins_before_create_so_no_root_or_effect_is_registered(self):
        waiting = threading.Event()
        def create():
            waiting.set()
            return self.logical_create('stop_winner')
        with ThreadPoolExecutor(max_workers=1) as pool:
            with admission.lifecycle_gate(exclusive=True):
                task = pool.submit(create)
                self.assertTrue(waiting.wait(5))
                self.provider.warehouse_state = 'STOPPED'
            with self.assertRaises(OperationError) as error: task.result(5)
        self.assertEqual(error.exception.code, 'WAREHOUSE_START_REQUIRED')
        with control_store.connect() as db: self.assertEqual(logical.compute_blockers(db), dict(requests=0, effects=0, roots=0))

    def test_dispatch_holds_shared_gate_during_effect_and_durable_claim_covers_step_gap(self):
        ack, _ = self.logical_create('effect_race')
        entered, release = threading.Event(), threading.Event()
        original = self.provider.create_role
        def role(descriptor):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(descriptor)
        def exclusive():
            with admission.lifecycle_gate(exclusive=True): return True
        with patch.object(self.provider, 'create_role', side_effect=role), ThreadPoolExecutor(max_workers=2) as pool:
            work = pool.submit(logical.dispatch, ack['request_id'])
            self.assertTrue(entered.wait(5))
            with control_store.connect() as db: self.assertEqual(logical.compute_blockers(db), dict(requests=1, effects=1, roots=1))
            stop = pool.submit(exclusive)
            self.assertFalse(stop.done())
            release.set()
            work.result(10)
            self.assertTrue(stop.result(5))
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')
