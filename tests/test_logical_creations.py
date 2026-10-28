"""Logical coordinator contracts on the real schema20 SQLite store only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from tests.test_database_creations import CreationFixture
import control_store
import database_creations as legacy
import logical_creations as logical
import logical_registry
import runtime_profile
from warehouse_operations import OperationError


class Provider:
    def __init__(self):
        self.calls = []
        self.fail_consumer = False
        self.hook = None
        self.config = dict(prefix='logical', polaris=dict(realm='shared-realm'), storage=dict(bucket='logical-lake'))
        self.publications = []
        self.catalog_credentials = {}
        self.callback = None
        self.bound = True
        self.probes = 0
        self.binds = []
        self.health_calls = 0
        self.hash_calls = 0
        self.alive_calls = 0
        self.compute_calls = 0
        self.warehouse_state = 'ACTIVE'

    @contextmanager
    def effects(self, callback):
        old, self.callback = self.callback, callback
        try:
            yield self
        finally:
            self.callback = old

    def bind_registered(self, expected_probe):
        self.bound = False
        actual = self.probe()
        if any(actual[k] != expected_probe[k] for k in
               ('foundation_id', 'revision', 'incarnation', 'spi_sha256')):
            raise ValueError('Native UID/config/SPI mismatch')
        self.binds.append(expected_probe)
        self.bound = True

    def bound_alive(self):
        self.alive_calls += 1
        return self.bound

    def compute_state(self):
        self.compute_calls += 1
        return self.warehouse_state

    def live_health(self):
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
        self.health_calls += 1
        return dict(ready=True, foundation_id='foundation-real', revision=1)

    def publication_hashes(self, descriptor):
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
        self.hash_calls += 1
        return {'target.'+name:'a'*64 for name in
                ('credentials.properties', 'password.db', 'rules.json', 'guard.rego')}

    def probe(self):
        self.probes += 1
        # A second writer can acquire the ledger: provider I/O is outside tx.
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
        return dict(ready=True, foundation_id='foundation-real', revision=1,
                    incarnation={'namespace':'uid', 'configmap/provider':'config-uid'}, spi_sha256='b'*64, readiness={k:True for k in ('storage','polaris','trino','runtime_files','spi')},
                    hashes={'target.'+name:'a'*64 for name in ('credentials.properties','password.db','rules.json','guard.rego')})

    def _call(self, key, *args):
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
        self.calls.append(key)
        if self.hook:
            self.hook(key)
        if key == 'consumer':
            if self.fail_consumer:
                raise RuntimeError('private-provider-secret')
            return dict(client_id='consumer-id', client_secret='private-consumer-secret', realm='realm', principal_name='principal')
        return dict(verified=True)

    def create_role(self, db): return self._call('role', db)
    def create_catalog(self, db): return self._call('catalog', db)
    def issue_consumer(self, db): return self._call('consumer', db)
    def create_namespaces(self, db, consumer): return self._call('namespaces', db)
    def publish_material(self, db, material):
        from logical_provider import Provider as ActualProvider
        ActualProvider._material(None, material)  # Pure validation, no API/SQL.
        # Model the existing Java immutable merge contract, never replacement.
        for ref, pair in material['credentials'].items():
            if ref in self.catalog_credentials and self.catalog_credentials[ref] != pair:
                raise ValueError('Immutable catalog credential changed')
        self.catalog_credentials.update(copy.deepcopy(material['credentials']))
        self.publications.append(material)
        return self.callback('publish', dict(db, kind='publication'),
                             lambda: self._call('publication', db))
    def check_material(self, db, material):
        self._call('observe-publication', db)
        if not self.publications or material != self.publications[-1]:
            return dict(verified=False)
        return self.observation('publication', db)
    def attach_catalog(self, db, ref): return self._call('attach', db)
    def verify_target(self, db, identity):
        self._call('verify', db)
        return self.verification(db, identity)
    def verification(self, db, identity):
        return self.observation('verification', db, identity_positive=True, identity_negative=True,
            prefix_positive=True, prefix_negative=True,
            **{k:v for k,v in identity.items() if k not in ('database_id','password')})

    def check_verify_target(self, db, identity):
        self._call('observe-verify', db)
        return self.verification(db, identity)

    def observation(self, kind, db, **extra):
        return dict(verified=True, complete=True, kind=kind, **db, **extra)

    def check_role(self, db):
        self._call('observe-role', db)
        return self.observation('role', db)
    def check_catalog(self, db):
        self._call('observe-catalog', db)
        return self.observation('catalog', db)


class LogicalFixture(CreationFixture):
    def setUp(self):
        super().setUp()
        self.credentials = self.directory/'credentials'
        self.credentials.mkdir(mode=0o700)
        self.provider = Provider()
        self.logical_config = dict(version=1, environment_id='test-environment', warehouse_id='LOCAL',
            foundation_generation_id='foundation-real', foundation_revision=1,
            credentials_dir=str(self.credentials), platform_admin_credential_ref='provisioner',
            provider_config_file=str(self.directory/'provider.json'), executor_id='executor')
        for p in [patch.object(logical, '_config', self.logical_config),
                  patch.object(logical, '_provider', self.provider),
                  patch.object(logical, '_executor_id', 'executor'),
                  patch.object(legacy, '_enrollment', None), patch.object(runtime_profile, 'current', return_value=None),
                  patch.object(runtime_profile, 'logical_config', side_effect=lambda *a: self.logical_config),
                  patch.object(runtime_profile, 'generation_attestation', return_value={}),
                  patch.object(logical_registry, 'platform_admin_credentials', return_value=dict(username='shirokuma_admin',password='private-admin-password'))]:
            p.start()
            self.addCleanup(p.stop)
        logical.register_foundation()

    def logical_create(self, name='research', key='create', grants=None):
        payload = dict(sql_catalog=name)
        if grants is not None:
            payload['initial_grants'] = grants
        return logical.create(self.alice, 'admin', payload, key)

    def detail(self, ack):
        return logical.get(self.alice, 'admin', ack['creation_id'])

    def mutation(self, ack, action, key):
        return logical.mutate(self.alice, 'admin', ack['creation_id'], action,
            dict(expected_revision=self.detail(ack)['revision']), key)[0]


class VerifierCacheTests(unittest.TestCase):
    def test_warm_records_authenticate_and_cold_cache_derives_again(self):
        cache = logical._VerifierCache('foundation', 1)
        derive = hashlib.pbkdf2_hmac
        with patch.object(logical.hashlib, 'pbkdf2_hmac', wraps=derive) as compute:
            first = cache.record('ref', 'user', 'synthetic-password')
            self.assertEqual(cache.record('ref', 'user', 'synthetic-password'), first)
            self.assertEqual(compute.call_count, 1)
            cold = logical._VerifierCache('foundation', 1).record('ref', 'user', 'synthetic-password')
            self.assertEqual(compute.call_count, 2)
        self.assertNotEqual(first, cold)
        self.assertRegex(first, r'^user:100000:[0-9a-f]{32}:[0-9a-f]{64}$')
        _, iterations, salt, digest = first.split(':')
        self.assertEqual(derive('sha1', b'synthetic-password', bytes.fromhex(salt),
                                int(iterations), dklen=32).hex(), digest)
        key, value = next(iter(cache._records.items()))
        self.assertEqual(key[:5], ('foundation', 1, 'ref', 'user', logical._HASH_POLICY))
        self.assertIsInstance(key[5], bytes)
        self.assertEqual(value, first)
        self.assertNotIn('synthetic-password', repr(cache.__dict__))
        self.assertFalse(any(isinstance(item, dict) for item in (*key, value)))

    def test_ref_password_username_policy_and_foundation_misses(self):
        cache = logical._VerifierCache('foundation', 1)
        derive = hashlib.pbkdf2_hmac
        with patch.object(logical.hashlib, 'pbkdf2_hmac', wraps=derive) as compute:
            for args in [('ref', 'user', 'password'), ('rotated', 'user', 'password'),
                         ('ref', 'user', 'changed'), ('ref', 'renamed', 'password')]:
                cache.record(*args)
            with patch.object(logical, '_HASH_POLICY', ('sha1', 100001, 16, 32)):
                cache.record('ref', 'user', 'password')
            for foundation, revision in [('other', 1), ('foundation', 2)]:
                logical._VerifierCache(foundation, revision).record('ref', 'user', 'password')
            self.assertEqual(compute.call_count, 7)
        other = logical._VerifierCache('foundation', 1)
        other.record('ref', 'user', 'password')
        self.assertNotEqual(next(iter(cache._records))[-1], next(iter(other._records))[-1])

    def test_lru_eviction_close_and_fallback(self):
        cache = logical._VerifierCache('foundation', 1, limit=2)
        first = cache.record('one', 'user', 'password')
        cache.record('two', 'user', 'password')
        self.assertEqual(cache.record('one', 'user', 'password'), first)
        cache.record('three', 'user', 'password')
        self.assertEqual([key[2] for key in cache._records], ['one', 'three'])
        derive = hashlib.pbkdf2_hmac
        with patch.object(logical.hashlib, 'pbkdf2_hmac', wraps=derive) as compute:
            cache.record('two', 'user', 'password')
            self.assertEqual(compute.call_count, 1)
        self.assertEqual(len(cache._records), 2)
        cache.close()
        self.assertFalse(cache._records)
        self.assertEqual(cache._key, b'')
        cache.record('one', 'user', 'password')
        self.assertFalse(cache._records)


class LogicalCreationTests(LogicalFixture):
    def test_warm_publication_still_reads_current_credentials(self):
        cache = logical._VerifierCache('foundation-real', 1)
        derive, read = hashlib.pbkdf2_hmac, runtime_profile.private_json
        with patch.object(logical, '_verifier_cache', cache), \
                patch.object(logical.hashlib, 'pbkdf2_hmac', wraps=derive) as compute:
            first, _ = self.logical_create()
            logical.dispatch(first['request_id'])
            self.assertEqual(self.detail(first)['progress_code'], 'READY')
            first_calls = compute.call_count
            self.assertEqual(first_calls, len(cache._records))
            with control_store.connect() as db:
                ref = db.execute('SELECT credential_ref FROM logical_backend_identities LIMIT 1').fetchone()[0]
            path = self.credentials/(ref+'.json')
            with patch.object(runtime_profile, 'private_json', wraps=read) as reads:
                second, _ = self.logical_create('second', 'second')
                logical.dispatch(second['request_id'])
                self.assertIn(path, [Path(c.args[0]) for c in reads.call_args_list])
            self.assertEqual(self.detail(second)['progress_code'], 'READY')
            self.assertEqual(compute.call_count-first_calls, len(cache._records)-first_calls)
            value = read(path)
            value['password'] = 'synthetic-changed-password'
            path.write_text(json.dumps(value))
            before = compute.call_count
            third, _ = self.logical_create('third', 'third')
            logical.dispatch(third['request_id'])
            self.assertEqual(self.detail(third)['progress_code'], 'READY')
            with control_store.connect() as db:
                added = db.execute('SELECT count(*) FROM logical_backend_identities WHERE database_id=?',
                                   (self.detail(third)['database_id'],)).fetchone()[0]
            self.assertEqual(compute.call_count-before, added+1)

    def test_name_key_competition_and_different_names(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.logical_create(), range(2)))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sum(fresh for _, fresh in results), 1)
        with self.assertRaises(OperationError) as error:
            self.logical_create(key='other')
        self.assertEqual(error.exception.code, 'DATABASE_ALREADY_EXISTS')
        with patch.object(legacy, 'guard_reservation', side_effect=AssertionError('legacy global reservation')):
            with ThreadPoolExecutor(max_workers=2) as pool:
                admitted = list(pool.map(lambda name: self.logical_create(name, name), ['second','third']))
        self.assertTrue(all(fresh for _, fresh in admitted))
        with self.assertRaises(OperationError) as error:
            self.logical_create('changed')
        self.assertEqual(error.exception.code, 'IDEMPOTENCY_CONFLICT')

    def test_foundation_provenance_no_legacy_apply(self):
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM operations').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT applied_generation_id FROM warehouse_controls').fetchone()[0], 'foundation-real')
            self.assertEqual(db.execute('SELECT generation_id FROM logical_foundations').fetchone()[0], 'foundation-real')

    def test_unknown_issuance_never_reissued_and_public_safe(self):
        ack, _ = self.logical_create()
        self.provider.fail_consumer = True
        logical.dispatch(ack['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'UNKNOWN')
        observation = self.mutation(ack, 'RECONCILE', 'observe')
        logical.dispatch(observation['request_id'])
        logical.dispatch(ack['request_id'])
        self.assertEqual(self.provider.calls.count('consumer'), 1)
        self.assertEqual(self.detail(ack)['progress_code'], 'UNKNOWN')
        with self.assertRaises(OperationError):
            self.mutation(ack, 'RESUME', 'resume')
        public = json.dumps(self.detail(ack))
        for forbidden in ('private-', str(self.credentials), 'client_secret', 'credential_ref'):
            self.assertNotIn(forbidden, public)

    def test_issuance_receipt_failure_is_unknown_no_reissue(self):
        ack, _ = self.logical_create()
        original = logical._receipt
        def receipt(value):
            if isinstance(value.get('result'), dict) and 'client_secret' in value['result']:
                raise OSError('private disk failure')
            return original(value)
        with patch.object(logical, '_receipt', side_effect=receipt):
            logical.dispatch(ack['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'UNKNOWN')
        logical.dispatch(ack['request_id'])
        self.assertEqual(self.provider.calls.count('consumer'), 1)

    def test_stop_wins_late_response_and_final_cas(self):
        ack, _ = self.logical_create(grants=[dict(principal_id='reader', maximum_role='bi_viewer')])
        def stop(key):
            if key == 'verify':
                self.mutation(ack, 'WITHDRAW', 'stop')
        self.provider.hook = stop
        logical.dispatch(ack['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'STOPPED')
        self.assertEqual(self.detail(ack)['next_actions'], [])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT maximum_role FROM effective_grants').fetchone()[0], 'revoked')
            self.assertEqual(db.execute("SELECT count(*) FROM logical_backend_identities WHERE state='ACTIVE'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT state FROM logical_effects WHERE effect_key LIKE 'verify_%' LIMIT 1").fetchone()[0], 'ACKNOWLEDGED')

    def test_ready_only_after_verification_and_current_grant_cas(self):
        ack, _ = self.logical_create(grants=[dict(principal_id='reader', maximum_role='bi_viewer')])
        logical.dispatch(ack['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT maximum_role FROM effective_grants').fetchone()[0], 'bi_viewer')
            dbid = self.detail(ack)['database_id']
            self.assertEqual(logical_registry.resolve_binding(db, dbid)['generation_id'], 'foundation-real')

    def test_two_owners_and_read_only_unknown_projection(self):
        ack, _ = self.logical_create()
        arrived, release = threading.Event(), threading.Event()
        def pause(key):
            if key == 'role':
                arrived.set()
                self.assertTrue(release.wait(5))
        self.provider.hook = pause
        thread = logical.dispatch_async(ack['request_id'])
        self.assertTrue(arrived.wait(5))
        try:
            self.assertEqual(self.detail(ack)['progress_code'], 'CREATING')
            logical.dispatch(ack['request_id'])
            self.assertEqual(self.provider.calls.count('role'), 1)
        finally:
            release.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        with logical.transaction() as db:
            db.execute("UPDATE db_creations SET logical_state='CREATING' WHERE id=?", (ack['creation_id'],))
            db.execute("UPDATE logical_requests SET dispatch_state='CLAIMED' WHERE id=?", (ack['request_id'],))
        before = self.snapshot()
        self.assertEqual(self.detail(ack)['progress_code'], 'UNKNOWN')
        self.assertEqual(self.snapshot(), before)

    def test_observer_only_then_explicit_resume_unsent(self):
        ack, _ = self.logical_create()
        def interrupt(key):
            if key == 'role':
                raise TimeoutError()
        self.provider.hook = interrupt
        logical.dispatch(ack['request_id'])
        self.provider.hook = None
        observation = self.mutation(ack, 'RECONCILE', 'observe')
        logical.dispatch(observation['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'BLOCKED')
        self.assertNotIn('catalog', self.provider.calls)
        resumed = self.mutation(ack, 'RESUME', 'resume')
        logical.dispatch(resumed['request_id'])
        self.assertEqual(self.provider.calls.count('role'), 1)
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')

    def test_account_loss_stops_before_next_leaf(self):
        ack, _ = self.logical_create()
        def disable(key):
            if key == 'role':
                self.accounts['alice']['disabled'] = True
                self.save_accounts()
        self.provider.hook = disable
        logical.dispatch(ack['request_id'])
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT logical_state FROM db_creations').fetchone()[0], 'STOPPED')
        self.assertEqual(self.provider.calls, ['role'])

    def test_final_cas_rejects_changed_grant_epoch(self):
        ack, _ = self.logical_create(grants=[dict(principal_id='reader',maximum_role='bi_viewer')])
        changed = False
        def change(key):
            nonlocal changed
            if key == 'verify' and not changed:
                changed = True
                with logical.transaction() as db:
                    db.execute("UPDATE grants SET maximum_role='revoked',revision=revision+1 WHERE principal_id='reader'")
        self.provider.hook = change
        logical.dispatch(ack['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'BLOCKED')
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM logical_backend_identities WHERE state='ACTIVE'").fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT applied_policy_revision FROM logical_bindings').fetchone()[0], 0)

    def test_publication_preserves_first_database_and_private_permissions(self):
        first, _ = self.logical_create()
        logical.dispatch(first['request_id'])
        second, _ = self.logical_create('second','second')
        logical.dispatch(second['request_id'])
        self.assertEqual(self.detail(first)['progress_code'], 'READY')
        self.assertEqual(self.detail(second)['progress_code'], 'READY')
        rules = json.loads(self.provider.publications[-1]['rules_json'])
        catalogs = {r.get('catalog') for r in rules['catalogs']}
        self.assertTrue({'research','second'} <= catalogs)
        old_refs = self.provider.publications[0]['credentials']
        latest = self.provider.publications[-1]
        self.assertEqual(len(old_refs), 1)
        self.assertEqual(len(latest['credentials']), 1)
        self.assertTrue(set(old_refs).isdisjoint(latest['credentials']))
        for ref, pair in old_refs.items():
            self.assertEqual(self.provider.catalog_credentials[ref], pair)
        with control_store.connect() as db:
            binding = logical_registry.get_binding(db, self.detail(second)['database_id'])
            identities = [dict(row) for row in db.execute("SELECT * FROM logical_backend_identities WHERE state='ACTIVE'")]
        self.assertEqual(set(latest['credentials']), {binding['catalog_credential_ref']})
        self.assertEqual(latest['credentials'][binding['catalog_credential_ref']],
            {k:v for k,v in runtime_profile.private_json(self.credentials/(binding['catalog_credential_ref']+'.json')).items()
             if k != 'version'})
        users = {line.split(':',1)[0] for line in latest['password_db'].splitlines()}
        self.assertTrue({row['backend_username'] for row in identities} <= users)
        self.assertIn('research', latest['guard_rego'])
        self.assertIn('second', latest['guard_rego'])
        for path in self.credentials.rglob('*.json'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_publication_refresh_only_targets_own_catalog_ref_and_preserves_global_material(self):
        first, _ = self.logical_create()
        logical.dispatch(first['request_id'])
        second, _ = self.logical_create('second', 'second')
        logical.dispatch(second['request_id'])
        first_db = self.detail(first)['database_id']
        original_ref = next(iter(self.provider.publications[0]['credentials']))
        original_pair = copy.deepcopy(self.provider.catalog_credentials[original_ref])
        with logical.transaction() as db:
            refresh = logical_registry.request_auth_refresh(db, 'executor', 'alice', None,
                dict(database_id=first_db, reason='ACCOUNT_AUTHORITY_CHANGED', idempotency_key='refresh-target'))
        with patch.object(self.provider, 'verify_revoked', create=True,
                return_value=dict(verified=True, control_positive=True, identity_denied=True)):
            logical.dispatch(refresh)
        with control_store.connect() as db:
            state = db.execute('SELECT dispatch_state FROM logical_requests WHERE id=?', (refresh,)).fetchone()[0]
            users = {r[0] for r in db.execute("SELECT backend_username FROM logical_backend_identities WHERE state='ACTIVE'")}
        self.assertEqual(state, 'COMPLETE')
        latest = self.provider.publications[-1]
        self.assertEqual(latest['credentials'], {original_ref: original_pair})
        self.assertTrue(users <= {line.split(':',1)[0] for line in latest['password_db'].splitlines()})
        self.assertTrue({'research', 'second'} <= {r.get('catalog') for r in json.loads(latest['rules_json'])['catalogs']})
        self.assertIn('second', latest['guard_rego'])
        self.assertEqual(len(self.provider.catalog_credentials), 2)

    def test_publication_invalid_target_binding_ref_and_pair_fail_before_publish(self):
        ack, _ = self.logical_create()
        logical.dispatch(ack['request_id'])
        with control_store.connect() as db:
            request = dict(db.execute('SELECT * FROM logical_requests WHERE id=?', (ack['request_id'],)).fetchone())
            root = dict(db.execute('SELECT * FROM db_creations WHERE id=?', (ack['creation_id'],)).fetchone())
            descriptor = logical._descriptor(db, root)
            binding = logical_registry.get_binding(db, root['database_id'])
        for bad in (None, {**binding, 'database_id': 'foreign'}, {**binding, 'catalog_credential_ref': 'foreign'},
                {**binding, 'catalog_credential_ref': ''}, {**binding, 'foundation_revision': 99}):
            with self.subTest(binding=bad), patch.object(logical_registry, 'get_binding', return_value=bad), \
                    patch.object(self.provider, 'publish_material') as publish, \
                    patch.object(self.provider, 'publication_hashes') as hashes:
                with self.assertRaises(ValueError):
                    logical._publication(request, descriptor, {}, key='invalid-binding')
                publish.assert_not_called()
                hashes.assert_not_called()
        private_json = runtime_profile.private_json
        ref_path = self.credentials/(binding['catalog_credential_ref']+'.json')
        for bad in (None, [], {}, {'username':'other','password':'wrong-kind'},
                {'client_id':'id'}, {'client_id':'id','client_secret':''},
                {'client_id':True,'client_secret':'s'}, {'client_id':'id','client_secret':'s','extra':'x'}):
            def wrong_pair(path):
                return bad if Path(path) == ref_path else private_json(path)
            with self.subTest(pair=bad), patch.object(runtime_profile, 'private_json', side_effect=wrong_pair), \
                    patch.object(self.provider, 'publish_material') as publish:
                with self.assertRaises(ValueError):
                    logical._publication(request, descriptor, {}, key='invalid-pair')
                publish.assert_not_called()
        with patch.object(runtime_profile, 'private_json', side_effect=FileNotFoundError('fixture missing')), \
                patch.object(self.provider, 'publish_material') as publish:
            with self.assertRaises(FileNotFoundError):
                logical._publication(request, descriptor, {}, key='missing-pair')
            publish.assert_not_called()

    def test_new_root_keeps_foreign_unknown_metadata_but_never_republishes_its_catalog_refs(self):
        first, _ = self.logical_create()
        logical.dispatch(first['request_id'])
        second, _ = self.logical_create('second', 'second')
        def interrupt(stage):
            if stage == 'publication':
                raise OSError('fixture response lost')
        # Seed a historical full manifest at its original durable write boundary;
        # immutable intent triggers stay enabled throughout this test.
        receipt, publish = logical._receipt, self.provider.publish_material
        def historical_payload(value):
            value = copy.deepcopy(value)
            value['credentials'].update(self.provider.publications[0]['credentials'])
            return value
        def historical_receipt(value):
            if isinstance(value, dict) and 'credentials' in value and 'rules_json' in value:
                value = historical_payload(value)
            return receipt(value)
        def historical_publish(descriptor, payload):
            return publish(descriptor, historical_payload(payload))
        self.provider.hook = interrupt
        with patch.object(logical, '_receipt', side_effect=historical_receipt), \
                patch.object(self.provider, 'publish_material', side_effect=historical_publish):
            logical.dispatch(second['request_id'])
        self.provider.hook = None
        self.assertEqual(self.detail(second)['progress_code'], 'UNKNOWN')
        legacy = copy.deepcopy(self.provider.publications[-1])
        self.assertEqual(len(legacy['credentials']), 2)
        with control_store.connect() as db:
            effect = dict(db.execute("SELECT * FROM logical_effects WHERE creation_id=? AND effect_key='publication'",
                (second['creation_id'],)).fetchone())
            frozen_intent = effect['intent_json']
            legacy_ref = json.loads(frozen_intent)['material_ref']
            first_effect = dict(db.execute("SELECT * FROM logical_effects WHERE creation_id=? AND effect_key='publication'",
                (first['creation_id'],)).fetchone())
        frozen_bytes = Path(legacy_ref).read_bytes()
        ack_bytes = Path(first_effect['private_ref']).read_bytes()
        third, _ = self.logical_create('third', 'third')
        logical.dispatch(third['request_id'])
        self.assertEqual(self.detail(third)['progress_code'], 'READY')
        latest = self.provider.publications[-1]
        self.assertEqual(len(latest['credentials']), 1)
        self.assertTrue(set(latest['credentials']).isdisjoint(legacy['credentials']))
        self.assertTrue({'research','second','third'} <= {r.get('catalog') for r in json.loads(latest['rules_json'])['catalogs']})
        with control_store.connect() as db:
            prior_users = {r[0] for r in db.execute('SELECT backend_username FROM logical_backend_identities WHERE database_id=?',
                (self.detail(second)['database_id'],))}
        self.assertTrue(prior_users <= {line.split(':',1)[0] for line in latest['password_db'].splitlines()})
        observed = []
        def observe(descriptor, payload):
            observed.append(copy.deepcopy(payload))
            return self.provider.observation('publication', descriptor)
        reconcile = self.mutation(second, 'RECONCILE', 'observe-frozen')
        with patch.object(self.provider, 'check_material', side_effect=observe), \
                patch.object(self.provider, 'publish_material', side_effect=AssertionError('reissued publication')):
            logical.dispatch(reconcile['request_id'])
        self.assertEqual(observed, [legacy])
        self.assertEqual(Path(legacy_ref).read_bytes(), frozen_bytes)
        self.assertEqual(Path(first_effect['private_ref']).read_bytes(), ack_bytes)
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT intent_json FROM logical_effects WHERE creation_id=? AND effect_key='publication'",
                (second['creation_id'],)).fetchone()[0], frozen_intent)

    def test_frozen_publication_fastpath_does_not_render_narrow_or_rewrite(self):
        ack, _ = self.logical_create()
        logical.dispatch(ack['request_id'])
        with control_store.connect() as db:
            request = dict(db.execute('SELECT * FROM logical_requests WHERE id=?', (ack['request_id'],)).fetchone())
            effect = dict(db.execute("SELECT * FROM logical_effects WHERE creation_id=? AND effect_key='publication'",
                (ack['creation_id'],)).fetchone())
        intent = json.loads(effect['intent_json'])
        material_path = Path(intent['material_ref'])
        original = material_path.read_bytes()
        descriptor = {key:intent[key] for key in ('database_id','sql_catalog','generation_id','foundation_revision')}
        with patch.object(logical, '_effect', return_value='frozen-ack') as replay, \
                patch.object(logical, '_render_material', side_effect=AssertionError('render')), \
                patch.object(logical, '_receipt', side_effect=AssertionError('rewrite')), \
                patch.object(self.provider, 'publish_material', side_effect=AssertionError('publish')):
            self.assertEqual(logical._publication(request, descriptor, {}), 'frozen-ack')
            self.assertEqual(replay.call_args.args[:3], (request, 'publication', intent))
        self.assertEqual(material_path.read_bytes(), original)

    def test_render_material_password_file_matches_trino_sha1_reference(self):
        import trino_auth
        salt = bytes.fromhex('000102030405060708090a0b0c0d0e0f')
        user = trino_auth.scoped_username('target', 'bi_viewer')
        identity = dict(id='target', database_id='target', selected_role='bi_viewer',
                        backend_username=user, credential_ref='personal')
        credentials = {'personal': dict(username=user, password='synthetic-personal-password')}
        internal = dict(username='logical_provisioner', password='synthetic-internal-password')
        # Offline reference: RFC 8018 section 5.2's U1/Uj XOR recurrence with
        # HMAC-SHA1, 100000 iterations, two blocks truncated to 32 bytes.
        # Generated independently without hashlib.pbkdf2_hmac.
        expected = {
            internal['username']: 'e79f354a1e4767596e4dcca8d5d2cf17482de21ce5316aee793bc21c1934c217',
            user: 'd7ab68907ee084be385e88dcfe40238446bb8a3eacd67a1f213baccb483ce41a',
        }
        with patch.object(logical_registry, 'platform_admin_credentials', return_value=internal), \
                patch.object(logical.secrets, 'token_bytes', return_value=salt) as random_salt:
            material = logical._render_material(
                [identity], {'target': dict(sql_catalog='research', generation_id='generation')},
                credentials)
        self.assertEqual(random_salt.call_args_list, [((16,), {}), ((16,), {})])
        self.assertTrue(material['password_db'].endswith('\n'))
        lines = material['password_db'].splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual([line.split(':')[0] for line in lines], [internal['username'], user])
        for line in lines:
            self.assertRegex(line, r'^[^:]+:100000:[0-9a-f]{32}:[0-9a-f]{64}$')
            username, iterations, rendered_salt, digest = line.split(':')
            self.assertEqual(iterations, '100000')
            self.assertEqual(rendered_salt, salt.hex())
            self.assertEqual(digest, expected[username])

    def test_render_material_internal_catalog_owner_is_scoped_and_app_roles_unchanged(self):
        import re
        import trino_auth
        descriptors = {'target': dict(sql_catalog='research', generation_id='generation')}
        identities, credentials = [], {}
        for role in ('admin', 'data_engineer', 'bi_viewer'):
            user = trino_auth.scoped_username('target', role)
            identities.append(dict(id='target', database_id='target', selected_role=role,
                                   backend_username=user, credential_ref=role))
            credentials[role] = dict(username=user, password='private-test-password')
        with patch.object(logical_registry, 'platform_admin_credentials', return_value=dict(
                username='logical_provisioner', password='private-provisioner-password')):
            rules = logical._render_material(identities, descriptors, credentials)['rules_json']

        def match(section, **resource):
            return next(r for r in rules[section] if all(
                k not in r or re.fullmatch(r[k], v) for k, v in resource.items()))

        internal = 'logical_provisioner'
        owner_rows = [r for r in rules['catalogs'] if r['allow'] == 'owner']
        self.assertTrue(owner_rows)
        self.assertTrue(all(r == dict(user=internal, catalog='research', allow='owner')
                            for r in owner_rows))
        self.assertEqual(match('catalogs', user=internal, catalog='research')['allow'], 'owner')
        self.assertEqual(match('catalogs', user=internal, catalog='unregistered')['allow'], 'none')
        self.assertEqual(match('catalogs', user=internal, catalog='system')['allow'], 'read-only')
        self.assertEqual(match('tables', user=internal, catalog='system', schema='runtime',
                               table='nodes')['privileges'], ['SELECT'])
        for schema, table in [('runtime', 'queries'), ('jdbc', 'tables')]:
            self.assertEqual(match('tables', user=internal, catalog='system', schema=schema,
                                   table=table)['privileges'], [])
        for identity in identities:
            user, role = identity['backend_username'], identity['selected_role']
            self.assertEqual(match('catalogs', user=user, catalog='research')['allow'],
                             'read-only' if role == 'bi_viewer' else 'all')
            for catalog in ('system', 'unregistered'):
                self.assertEqual(match('catalogs', user=user, catalog=catalog)['allow'], 'none')
                self.assertEqual(match('tables', user=user, catalog=catalog, schema='runtime',
                                       table='nodes')['privileges'], [])
        self.assertEqual(rules['catalogs'][-1], dict(allow='none'))
        self.assertEqual(rules['tables'][-1], dict(privileges=[]))

    def test_publication_internal_nodes_acl_is_narrow_and_preserves_role_union(self):
        import re
        self.accounts['reader']['role'] = 'data_engineer'
        self.save_accounts()
        first, _ = self.logical_create(grants=[dict(principal_id='reader',maximum_role='data_engineer')])
        logical.dispatch(first['request_id'])
        second, _ = self.logical_create('second','second')
        logical.dispatch(second['request_id'])
        self.assertEqual(self.detail(second)['progress_code'], 'READY')
        def match(rules, **resource):
            return next(r for r in rules if all(k not in r or re.fullmatch(r[k],v) for k,v in resource.items()))
        for publication in self.provider.publications:
            rules = json.loads(publication['rules_json'])
            self.assertEqual(match(rules['catalogs'],user='shirokuma_admin',catalog='system')['allow'], 'read-only')
            self.assertEqual(match(rules['tables'],user='shirokuma_admin',catalog='system',schema='runtime',table='nodes')['privileges'], ['SELECT'])
            for schema,table in [('runtime','queries'),('jdbc','tables')]:
                self.assertEqual(match(rules['tables'],user='shirokuma_admin',catalog='system',schema=schema,table=table)['privileges'], [])
            self.assertEqual(match(rules['catalogs'],user='shirokuma_admin',catalog='unregistered')['allow'], 'none')
            users = [line.split(':')[0] for line in publication['password_db'].splitlines() if not line.startswith('shirokuma_admin:')]
            for user in users:
                self.assertEqual(match(rules['catalogs'],user=user,catalog='system')['allow'], 'none')
                self.assertEqual(match(rules['tables'],user=user,catalog='system',schema='runtime',table='nodes')['privileges'], [])
        with control_store.connect() as db:
            identities = [dict(r) for r in db.execute('SELECT * FROM logical_backend_identities')]
        rules = json.loads(self.provider.publications[-1]['rules_json'])
        for identity in identities:
            user = identity['backend_username']
            catalog = 'research' if identity['database_id']==self.detail(first)['database_id'] else 'second'
            for schema in ('raw','sandbox','staging','marts'):
                privileges = match(rules['tables'],user=user,catalog=catalog,schema=schema,table='arbitrary')['privileges']
                self.assertEqual('SELECT' in privileges, schema=='marts' or identity['selected_role']!='bi_viewer')
            self.assertEqual(match(rules['catalogs'],user=user,catalog='unregistered')['allow'], 'none')
        opa = self.provider.publications[-1]['guard_rego']
        self.assertIn('engineer_catalogs :=', opa)
        self.assertIn('scoped_admin_catalogs :=', opa)
        self.assertIn('table_procedure', opa)

    def test_withdraw_still_works_when_foundation_and_executor_unavailable(self):
        ack, _ = self.logical_create()
        self.accounts['executor']['disabled'] = True
        self.save_accounts()
        with logical.transaction() as db:
            db.execute("UPDATE logical_foundations SET status='BLOCKED'")
        self.mutation(ack, 'WITHDRAW', 'stop')
        self.assertEqual(self.detail(ack)['progress_code'], 'STOPPED')

    def test_actual_provider_fixture_uses_valid_transport_boundary(self):
        from logical_provider import Provider as ActualProvider, Rejected
        provider = object.__new__(ActualProvider)
        provider._opened, provider._children = True, []
        provider._config = dict(foundation_id='foundation-real',revision=1)
        provider._trust = lambda: None
        provider._gate(dict(database_id='12345678-1234-1234-1234-123456789abc',sql_catalog='valid',
            generation_id='foundation-real',foundation_revision=1))


class AdmissionHealthTests(LogicalFixture):
    def counts(self):
        with control_store.connect() as db:
            return tuple(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                         for table in ('db_creations', 'logical_requests', 'databases'))

    def test_create_health_strict_pins_and_duplicate_fast_path(self):
        baseline = self.counts()
        invalid = [None, {}, dict(ready=1, foundation_id='foundation-real', revision=1),
                   dict(ready=True, foundation_id='foreign', revision=1),
                   dict(ready=True, foundation_id='foundation-real', revision=True),
                   dict(ready=True, foundation_id='foundation-real', revision='1'),
                   dict(ready=True, foundation_id='foundation-real', revision=2)]
        for result in invalid:
            with self.subTest(result=result), patch.object(self.provider, 'live_health', return_value=result):
                with self.assertRaises(OperationError):
                    self.logical_create()
                self.assertEqual(self.counts(), baseline)
        ack, _ = self.logical_create()
        with patch.object(self.provider, 'live_health', side_effect=AssertionError('no replay health')):
            self.assertEqual(self.logical_create(), (ack, False))
            with self.assertRaises(OperationError) as error:
                self.logical_create('different')
            self.assertEqual(error.exception.code, 'IDEMPOTENCY_CONFLICT')
            self.accounts['alice']['enabled'] = False
            self.save_accounts()
            with self.assertRaises(OperationError):
                self.logical_create()
        self.assertEqual(self.provider.probes, 1)  # Registration only.

    def test_health_failure_and_final_gate_race_leave_no_root(self):
        baseline = self.counts()
        for method in ('live_health', 'bound_alive'):
            with patch.object(self.provider, method, side_effect=TimeoutError('private')):
                with self.assertRaises(OperationError):
                    self.logical_create()
                self.assertEqual(self.counts(), baseline)
        health = self.provider.live_health
        def stop_executor():
            result = health()
            with logical.transaction() as db:
                logical._gate(db, 'executor')
                db.execute("UPDATE db_creation_principal_gates SET state='STOPPED' WHERE principal_id='executor'")
            return result
        with patch.object(self.provider, 'live_health', side_effect=stop_executor):
            with self.assertRaises(OperationError):
                self.logical_create()
        self.assertEqual(self.counts(), baseline)

    def test_resume_fresh_health_duplicate_and_observational_actions(self):
        from logical_provider import Rejected
        ack, _ = self.logical_create()
        with patch.object(self.provider, 'create_role', side_effect=Rejected('NO_EFFECTS')):
            logical.dispatch(ack['request_id'])
        revision = self.detail(ack)['revision']
        baseline = self.counts()
        with patch.object(self.provider, 'live_health', return_value=dict(ready=False)):
            with self.assertRaises(OperationError):
                self.mutation(ack, 'RESUME', 'resume')
        self.assertEqual(self.detail(ack)['revision'], revision)
        self.assertEqual(self.counts(), baseline)
        resumed = self.mutation(ack, 'RESUME', 'resume')
        with patch.object(self.provider, 'live_health', side_effect=AssertionError('no health')):
            replay = logical.mutate(self.alice, 'admin', ack['creation_id'], 'RESUME',
                                    dict(expected_revision=revision), 'resume')
            self.assertEqual(replay, (resumed, False))
            reconcile = self.mutation(ack, 'RECONCILE', 'reconcile')
            logical.dispatch(reconcile['request_id'])
            self.mutation(ack, 'WITHDRAW', 'stop')
        self.assertEqual(self.detail(ack)['progress_code'], 'STOPPED')

    def test_live_health_only_real_stopped_rejection_and_no_admission(self):
        from logical_provider import Rejected, Unknown
        forged = RuntimeError('private-provider-secret')
        forged.code = 'WAREHOUSE_START_REQUIRED'
        errors = [(Rejected('WAREHOUSE_START_REQUIRED'), 'WAREHOUSE_START_REQUIRED', 409),
                  (Rejected('private-provider-secret'), 'FOUNDATION_NOT_READY', 503),
                  (Unknown('WAREHOUSE_START_REQUIRED'), 'FOUNDATION_NOT_READY', 503),
                  (forged, 'FOUNDATION_NOT_READY', 503)]
        for bound in (True, False):
            for failure, code, status in errors:
                with self.subTest(bound=bound, failure=type(failure).__name__, code=code):
                    baseline = self.counts()
                    with patch.object(self.provider, 'bound_alive', return_value=bound) as alive, \
                            patch.object(self.provider, 'live_health', side_effect=failure) as health:
                        with self.assertRaises(OperationError) as error:
                            self.logical_create()
                    self.assertEqual((error.exception.code, error.exception.status), (code, status))
                    health.assert_called_once_with()
                    alive.assert_not_called()
                    self.assertEqual(self.counts(), baseline)

    def test_resume_stopped_health_rejection_preserves_root_and_requests(self):
        from logical_provider import Rejected
        ack, _ = self.logical_create()
        with patch.object(self.provider, 'create_role', side_effect=Rejected('NO_EFFECTS')):
            logical.dispatch(ack['request_id'])
        before, baseline = self.detail(ack), self.counts()
        with patch.object(self.provider, 'bound_alive', return_value=False) as alive, \
                patch.object(self.provider, 'live_health', side_effect=Rejected('WAREHOUSE_START_REQUIRED')) as health:
            with self.assertRaises(OperationError) as error:
                self.mutation(ack, 'RESUME', 'resume')
        self.assertEqual((error.exception.code, error.exception.status), ('WAREHOUSE_START_REQUIRED', 409))
        health.assert_called_once_with()
        alive.assert_not_called()
        self.assertEqual(self.detail(ack), before)
        self.assertEqual(self.counts(), baseline)

    def test_admission_profile_and_foundation_fail_before_live_health(self):
        from types import SimpleNamespace
        baseline = self.counts()
        with patch.object(self.provider, 'live_health') as health:
            with patch.object(runtime_profile, 'current', return_value=SimpleNamespace(mode='database')), \
                    patch.object(runtime_profile, 'generation_attestation', side_effect=ValueError('private-secret')):
                with self.assertRaises(OperationError) as error:
                    self.logical_create()
                self.assertEqual((error.exception.code, error.exception.status), ('FOUNDATION_NOT_READY', 503))
            with logical.transaction() as db:
                db.execute("UPDATE warehouse_config_generations SET state='FAILED'")
            with self.assertRaises(OperationError) as error:
                self.logical_create()
            self.assertEqual((error.exception.code, error.exception.status), ('FOUNDATION_NOT_READY', 503))
        health.assert_not_called()
        self.assertEqual(self.counts(), baseline)

    def test_prehealth_authority_and_gates_create_resume_and_duplicate(self):
        from logical_provider import Rejected
        ack, _ = self.logical_create()
        with patch.object(self.provider, 'create_role', side_effect=Rejected('NO_EFFECTS')):
            logical.dispatch(ack['request_id'])
        revision = self.detail(ack)['revision']
        for actor in ('alice', 'executor'):
            for failure in ('disabled', 'stopped'):
                with self.subTest(actor=actor, failure=failure):
                    if failure == 'disabled':
                        self.accounts[actor]['enabled'] = False
                        self.save_accounts()
                    else:
                        with logical.transaction() as db:
                            db.execute("UPDATE db_creation_principal_gates SET state='STOPPED' WHERE principal_id=?", (actor,))
                    baseline = self.counts()
                    with patch.object(self.provider, 'live_health', side_effect=AssertionError('gate before health')) as health:
                        for call in (lambda: self.logical_create('second', 'second'),
                                     lambda: logical.mutate(self.alice, 'admin', ack['creation_id'], 'RESUME',
                                                            dict(expected_revision=revision), 'resume')):
                            with self.assertRaises(OperationError) as error:
                                call()
                            expected = 'ADMIN_REQUIRED' if actor == 'alice' and failure == 'disabled' else 'PRINCIPAL_STOPPED'
                            self.assertEqual(error.exception.code, expected)
                        if actor != 'alice' or failure != 'disabled':
                            self.assertEqual(self.logical_create(), (ack, False))
                    health.assert_not_called()
                    self.assertEqual(self.counts(), baseline)
                    self.accounts[actor]['enabled'] = True
                    self.save_accounts()
                    with logical.transaction() as db:
                        db.execute("UPDATE db_creation_principal_gates SET state='ACTIVE' WHERE principal_id=?", (actor,))
                    self.assertEqual(self.detail(ack)['revision'], revision)

        resumed = self.mutation(ack, 'RESUME', 'resume')
        with logical.transaction() as db:
            db.execute("UPDATE db_creation_principal_gates SET state='STOPPED'")
        self.accounts['executor']['enabled'] = False
        self.save_accounts()
        with patch.object(self.provider, 'live_health') as health:
            replay = logical.mutate(self.alice, 'admin', ack['creation_id'], 'RESUME',
                                    dict(expected_revision=revision), 'resume')
        self.assertEqual(replay, (resumed, False))
        health.assert_not_called()

    def test_resume_rechecks_revision_and_unknown_after_health(self):
        ack, _ = self.logical_create()
        with logical.transaction() as db:
            db.execute("UPDATE db_creations SET logical_state='BLOCKED' WHERE id=?", (ack['creation_id'],))
        baseline = self.counts()
        health = self.provider.live_health
        def race():
            result = health()
            with logical.transaction() as db:
                db.execute('UPDATE db_creations SET revision=revision+1 WHERE id=?', (ack['creation_id'],))
            return result
        revision = self.detail(ack)['revision']
        with patch.object(self.provider, 'live_health', side_effect=race):
            with self.assertRaises(OperationError) as error:
                logical.mutate(self.alice, 'admin', ack['creation_id'], 'RESUME',
                               dict(expected_revision=revision), 'resume')
        self.assertEqual(error.exception.code, 'REVISION_CONFLICT')
        self.assertEqual(self.counts(), baseline)


class AccountRefreshPendingTests(LogicalFixture):
    def test_revoked_cached_identities_are_excluded_from_all_global_material(self):
        cache = logical._VerifierCache('foundation-real', 1)
        with patch.object(logical, '_verifier_cache', cache):
            _, _, _, old = self.disable_reader()
        latest = self.provider.publications[-1]
        for identity in old:
            self.assertTrue(any(key[2] == identity['credential_ref'] for key in cache._records))
            user = identity['backend_username']
            self.assertNotIn(user, latest['password_db'])
            self.assertNotIn(user, latest['rules_json'])
            self.assertNotIn(user, latest['guard_rego'])

    def ready(self, name='research', role='bi_viewer'):
        ack, _ = self.logical_create(name, name, [dict(principal_id='reader', maximum_role=role)])
        logical.dispatch(ack['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')
        return ack, self.detail(ack)['database_id']

    def rows(self, table):
        with control_store.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]

    def refreshes(self):
        return [r for r in self.rows('logical_requests') if r['action']=='AUTH_REFRESH']

    def pending(self, dbid):
        with logical.transaction() as db:
            return logical_registry.request_auth_refresh(db, 'executor', 'reader', None,
                dict(database_id=dbid, reason='ACCOUNT_AUTHORITY_CHANGED', idempotency_key=logical._id()))

    def target_identities(self, dbid):
        return [r for r in self.rows('logical_backend_identities')
                if r['database_id']==dbid and r['principal_id']=='reader']

    def disable_reader(self, *, race=False):
        self.accounts['reader']['role'] = 'data_engineer'
        self.save_accounts()
        ack, dbid = self.ready(role='data_engineer')
        baseline = json.loads(self.account_file.read_text())
        self.accounts['reader']['disabled'] = True
        self.save_accounts()
        denied = []
        def verify_revoked(descriptor, identity):
            denied.append(identity['username'])
            if race and len(denied)==2:
                self.accounts['reader']['disabled'] = False
                self.save_accounts()
            return dict(verified=True, control_positive=True, identity_denied=True)
        with patch.object(self.provider, 'verify_revoked', side_effect=verify_revoked, create=True):
            advanced = logical._enqueue_account_changes(baseline)
        old = self.target_identities(dbid)
        self.assertTrue(old)
        self.assertTrue(all(r['state']=='REVOKED' for r in old))
        self.assertEqual(set(denied), {r['backend_username'] for r in old})
        self.assertEqual(self.refreshes()[-1]['dispatch_state'], 'BLOCKED' if race else 'COMPLETE')
        effective = next(r for r in self.rows('effective_grants') if r['database_id']==dbid and r['principal_id']=='reader')
        self.assertEqual(effective['maximum_role'], 'data_engineer' if race else 'revoked')
        return ack, dbid, advanced, old

    def test_zero_active_restore_after_completed_disable(self):
        self.assert_zero_active_restore(race=False)

    def test_zero_active_restore_after_final_cas_block(self):
        self.assert_zero_active_restore(race=True)

    def assert_zero_active_restore(self, *, race):
        _, dbid, baseline, old = self.disable_reader(race=race)
        others = [r for r in self.rows('logical_backend_identities') if r['principal_id']!='reader']
        credentials = {p.name:p.read_bytes() for p in self.credentials.iterdir() if p.is_file()}
        previous = self.refreshes()
        self.accounts['reader']['disabled'] = False
        self.save_accounts()
        logical._enqueue_account_changes(baseline)
        self.assertEqual(len(self.refreshes()), len(previous)+1)
        self.assertEqual(self.refreshes()[:-1], previous)
        self.assertEqual(self.refreshes()[-1]['dispatch_state'], 'COMPLETE')
        active = [r for r in self.target_identities(dbid) if r['state']=='ACTIVE']
        self.assertEqual({r['selected_role'] for r in active}, {'bi_viewer','data_engineer'})
        for field in ('id','backend_username','credential_ref'):
            self.assertTrue({r[field] for r in old}.isdisjoint(r[field] for r in active))
        self.assertEqual([r for r in self.target_identities(dbid) if r['id'] in {o['id'] for o in old}], old)
        self.assertTrue(all(r in self.rows('logical_backend_identities') for r in others))
        for name, value in credentials.items():
            self.assertEqual((self.credentials/name).read_bytes(), value)
        with control_store.connect() as db:
            binding = logical_registry.resolve_binding(db, dbid)
            epoch = next(r['epoch'] for r in self.rows('effective_grants')
                         if r['database_id']==dbid and r['principal_id']=='reader')
            for identity in active:
                resolved = logical_registry.resolve_identity(db, 'reader', dbid, identity['selected_role'], epoch)
                self.assertEqual(resolved['id'], identity['id'])

    def test_zero_active_role_ceiling_restore_and_stopped_gate(self):
        _, dbid, baseline, old = self.disable_reader()
        with logical.transaction() as db:
            db.execute("INSERT INTO db_creation_principal_gates (environment_id,principal_id,state,revision,updated_at) VALUES (?,'reader','STOPPED',7,?)",
                       (self.logical_config['environment_id'], logical.admission._now()))
        self.accounts['reader'].update(disabled=False, role='bi_viewer')
        self.save_accounts()
        baseline = logical._enqueue_account_changes(baseline)
        active = [r for r in self.target_identities(dbid) if r['state']=='ACTIVE']
        self.assertEqual([r['selected_role'] for r in active], ['bi_viewer'])
        self.assertEqual(active[0]['authority_source'], 'grant')
        self.accounts['reader'].update(role='admin', platform_admin=True)
        self.save_accounts()
        self.provider.verify_revoked = lambda *a: dict(verified=True, control_positive=True, identity_denied=True)
        logical._enqueue_account_changes(baseline)
        active = [r for r in self.target_identities(dbid) if r['state']=='ACTIVE']
        self.assertEqual({r['selected_role'] for r in active}, {'bi_viewer','data_engineer'})
        self.assertTrue(all(r['authority_source']=='grant' for r in active))
        gate = next(r for r in self.rows('db_creation_principal_gates') if r['principal_id']=='reader')
        self.assertEqual((gate['state'], gate['revision']), ('STOPPED',7))

    def test_restore_readback_account_race_never_activates_stale_candidates(self):
        _, dbid, baseline, old = self.disable_reader()
        self.accounts['reader']['disabled'] = False
        self.save_accounts()
        def change_account(key):
            if key=='verify':
                self.accounts['reader']['role'] = 'bi_viewer'
                self.save_accounts()
        self.provider.hook = change_account
        advanced = logical._enqueue_account_changes(baseline)
        self.provider.hook = None
        self.assertEqual(self.refreshes()[-1]['dispatch_state'], 'BLOCKED')
        candidates = [r for r in self.target_identities(dbid) if r['state']=='CANDIDATE']
        self.assertTrue(candidates)
        self.assertFalse(any(r['state']=='ACTIVE' for r in self.target_identities(dbid)))
        self.provider.verify_revoked = lambda *a: dict(verified=True, control_positive=True, identity_denied=True)
        previous = self.refreshes()[-1]
        logical._enqueue_account_changes(advanced)
        self.assertEqual(self.refreshes()[-1]['dispatch_state'], 'COMPLETE')
        self.assertNotEqual(self.refreshes()[-1]['id'], previous['id'])
        active = [r for r in self.target_identities(dbid) if r['state']=='ACTIVE']
        self.assertEqual([r['selected_role'] for r in active], ['bi_viewer'])
        stale = {r['id'] for r in old+candidates}
        self.assertTrue(all(r['state']=='REVOKED' for r in self.target_identities(dbid) if r['id'] in stale))
        for field in ('id','backend_username','credential_ref'):
            self.assertTrue({r[field] for r in old+candidates}.isdisjoint(r[field] for r in active))

    def test_zero_active_unknown_refresh_is_not_replayed_or_reissued(self):
        _, dbid, baseline, old = self.disable_reader()
        prior = self.refreshes()[-1]
        with logical.transaction() as db:
            db.execute("UPDATE logical_requests SET dispatch_state='UNKNOWN' WHERE id=?", (prior['id'],))
            db.execute("UPDATE logical_effects SET state='UNKNOWN' WHERE creation_id=? AND effect_key=?",
                       (prior['creation_id'], 'refresh/'+prior['id']+'/material'))
        before = self.rows('logical_effects')
        calls = list(self.provider.calls)
        self.accounts['reader']['disabled'] = False
        self.save_accounts()
        advanced = logical._enqueue_account_changes(baseline)
        self.assertEqual([r['dispatch_state'] for r in self.refreshes()], ['UNKNOWN','UNKNOWN'])
        self.assertEqual(self.rows('logical_effects'), before)
        self.assertEqual(self.provider.calls, calls)
        self.assertEqual(self.target_identities(dbid), old)
        requests = self.refreshes()
        logical._enqueue_account_changes(advanced)
        self.assertEqual(self.refreshes(), requests)

    def test_zero_active_discovery_requires_current_ready_binding_and_desired_grant(self):
        _, dbid, baseline, _ = self.disable_reader()
        self.accounts['reader']['disabled'] = False
        self.save_accounts()
        exclusions = (
            ('grants','maximum_role','revoked'),
            ('databases','state','STOPPED'),
            ('db_creations','logical_state','BLOCKED'),
            ('db_creations','environment_id','foreign'),
            ('db_creations','warehouse_id','FOREIGN'),
            ('logical_bindings','foundation_revision',2),
            ('catalog_bindings','applied_generation_id',None),
            ('logical_foundations','status','BLOCKED'),
        )
        for table, field, value in exclusions:
            with self.subTest(table=table, field=field):
                with logical.transaction() as db:
                    saved = db.execute('SELECT '+field+' FROM '+table).fetchone()[0]
                    db.execute('UPDATE '+table+' SET '+field+'=?', (value,))
                try:
                    before = self.refreshes()
                    logical._enqueue_account_changes(baseline)
                    self.assertEqual(self.refreshes(), before)
                finally:
                    with logical.transaction() as db:
                        db.execute('UPDATE '+table+' SET '+field+'=?', (saved,))
        with logical.transaction() as db:
            db.execute('DELETE FROM logical_bindings WHERE database_id=?', (dbid,))
        logical._enqueue_account_changes(baseline)
        self.assertEqual(self.refreshes(), before)

    def test_busy_root_disable_refresh_survives_advanced_baseline(self):
        self.assert_busy_root_refresh('disabled')

    def test_busy_root_role_refresh_survives_advanced_baseline(self):
        self.assert_busy_root_refresh('role')

    def assert_busy_root_refresh(self, change):
        import fcntl
        self.accounts['reader'].update(disabled=False, role='data_engineer')
        self.save_accounts()
        ack, dbid = self.ready('busy_'+change, 'data_engineer')
        baseline = json.loads(self.account_file.read_text())
        before = self.rows('logical_backend_identities')
        target = [r for r in before if r['database_id']==dbid and r['principal_id']=='reader']
        other = [r for r in before if r not in target]
        calls = list(self.provider.calls)
        effects = self.rows('logical_effects')
        requests = len(self.refreshes())
        self.accounts['reader'][change] = True if change=='disabled' else 'bi_viewer'
        self.save_accounts()
        revoked = []
        def denied(descriptor, identity):
            revoked.append(identity['username'])
            return dict(verified=True, control_positive=True, identity_denied=True)
        self.provider.verify_revoked = denied
        with open(logical._private_dir()/(ack['creation_id']+'.lock'), 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            advanced = logical._enqueue_account_changes(baseline)
            pending = [r for r in self.refreshes() if r['creation_id']==ack['creation_id']]
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]['dispatch_state'], 'ACCEPTED')
            self.assertIsNone(pending[0]['claim_id'])
            self.assertEqual(self.provider.calls, calls)
            self.assertEqual(self.rows('logical_effects'), effects)
            self.assertEqual(self.rows('logical_backend_identities'), before)
        self.assertEqual(advanced, self.accounts)
        logical._enqueue_account_changes(advanced)
        completed = next(r for r in self.refreshes() if r['id']==pending[0]['id'])
        self.assertEqual(completed['dispatch_state'], 'COMPLETE')
        self.assertTrue(completed['claim_id'])
        self.assertEqual(len(self.refreshes()), requests + 1)
        after = self.rows('logical_backend_identities')
        self.assertTrue(all(next(r for r in after if r['id']==old['id'])['state']=='REVOKED' for old in target))
        self.assertTrue({r['backend_username'] for r in target}.issubset(revoked))
        for row in other:
            if row['principal_id']!='reader':
                self.assertIn(row, after)
        active = [r for r in after if r['database_id']==dbid and r['principal_id']=='reader' and r['state']=='ACTIVE']
        self.assertEqual([r['selected_role'] for r in active], [] if change=='disabled' else ['bi_viewer'])
        count = len(self.refreshes())
        logical._enqueue_account_changes(advanced)
        self.assertEqual(len(self.refreshes()), count)

    def test_two_watchers_busy_root_duplicate_requests_complete_without_duplicate_identity(self):
        self.accounts['reader']['role'] = 'data_engineer'
        self.save_accounts()
        ack, dbid = self.ready(role='data_engineer')
        baseline = json.loads(self.account_file.read_text())
        self.accounts['reader']['role'] = 'bi_viewer'
        self.save_accounts()
        others = [r for r in self.rows('logical_backend_identities') if r['principal_id']!='reader']
        self.provider.verify_revoked = lambda *a: dict(verified=True, control_positive=True, identity_denied=True)
        with logical._lock(ack['creation_id']) as held:
            self.assertTrue(held)
            with patch.object(logical, 'dispatch', wraps=logical.dispatch) as dispatch:
                advanced = logical._enqueue_account_changes(baseline)
                logical._enqueue_account_changes(baseline)
                pending = self.refreshes()
                self.assertEqual(len(pending), 2)
                self.assertTrue(all(r['dispatch_state']=='ACCEPTED' for r in pending))
                self.assertEqual(dispatch.call_count, 3)  # One ID, then the two-ID snapshot.
        with patch.object(logical, 'dispatch', wraps=logical.dispatch) as dispatch:
            logical._enqueue_account_changes(advanced)
            self.assertEqual(sorted(c.args[0] for c in dispatch.call_args_list), sorted(r['id'] for r in pending))
        self.assertEqual(len(self.refreshes()), 2)
        self.assertTrue(all(r['dispatch_state'] in ('COMPLETE','BLOCKED') for r in self.refreshes()))
        after = self.rows('logical_backend_identities')
        for row in others:
            self.assertIn(row, after)
        active = [r for r in after if r['principal_id']=='reader' and r['state']=='ACTIVE']
        self.assertEqual([r['selected_role'] for r in active], ['bi_viewer'])
        self.assertEqual(len({r['credential_ref'] for r in active}), len(active))

    def test_pending_scan_excludes_other_scopes_and_nonaccepted_states(self):
        ack, dbid = self.ready()
        baseline = json.loads(self.account_file.read_text())
        for state in ('UNKNOWN','CLAIMED','BLOCKED','COMPLETE'):
            request_id = self.pending(dbid)
            with logical.transaction() as db:
                db.execute('UPDATE logical_requests SET dispatch_state=? WHERE id=?', (state, request_id))
        eligible = self.pending(dbid)
        template = next(r for r in self.refreshes() if r['id']==eligible)
        for field, value in (('generation_id','old-foundation'), ('foundation_revision',2),
                             ('environment_id','foreign'), ('warehouse_id','FOREIGN')):
            row = dict(template, id=logical._id(), idempotency_key=logical._id())
            pins = json.loads(row['pins_json'])
            pins[field] = value
            row['pins_json'] = logical._json(pins)
            with logical.transaction() as db:
                db.execute('INSERT INTO logical_requests ('+','.join(row)+') VALUES ('+
                           ','.join('?' for _ in row)+')', tuple(row.values()))
        for index, (table, field, value) in enumerate((
                ('db_creations','environment_id','foreign'),
                ('db_creations','warehouse_id','FOREIGN'),
                ('logical_bindings','foundation_revision',2),
                ('catalog_bindings','applied_generation_id',None))):
            root, other_dbid = self.ready('excluded_'+str(index))
            self.pending(other_dbid)
            with logical.transaction() as db:
                key = 'id' if table=='db_creations' else 'database_id'
                db.execute('UPDATE '+table+' SET '+field+'=? WHERE '+key+'=?',
                           (value, root['creation_id'] if key=='id' else other_dbid))
        before = self.rows('logical_requests')
        calls = list(self.provider.calls)
        with patch.object(logical, 'dispatch') as dispatch:
            logical._enqueue_account_changes(baseline)
            dispatch.assert_called_once_with(eligible)
        self.assertEqual(self.rows('logical_requests'), before)
        self.assertEqual(self.provider.calls, calls)
        for field, value in (('foundation_generation_id','foreign'), ('foundation_revision',2),
                             ('environment_id','foreign'), ('warehouse_id','FOREIGN')):
            with self.subTest(field=field), patch.dict(self.logical_config, {field:value}), \
                    patch.object(logical, 'dispatch') as dispatch:
                logical._enqueue_account_changes(baseline)
                dispatch.assert_not_called()

    def test_dispatch_snapshot_does_not_drain_new_arrivals(self):
        _, dbid = self.ready()
        baseline = json.loads(self.account_file.read_text())
        first = self.pending(dbid)
        late = []
        def arrive(request_id):
            late.append(self.pending(dbid))
        with patch.object(logical, 'dispatch', side_effect=arrive) as dispatch:
            logical._enqueue_account_changes(baseline)
            dispatch.assert_called_once_with(first)
        with patch.object(logical, 'dispatch') as dispatch:
            logical._enqueue_account_changes(baseline)
            self.assertEqual({c.args[0] for c in dispatch.call_args_list}, {first, late[0]})

    def test_loop_waits_between_finite_passes_and_preserves_returned_baseline(self):
        baseline, advanced = {'before':{}}, {'after':{}}
        stop = type('Stop', (), {})()
        with patch.object(stop, 'wait', create=True, side_effect=[False, False, True]) as wait, \
                patch.object(logical, '_enqueue_account_changes', side_effect=[advanced, advanced]) as enqueue:
            logical._account_refresh_loop(stop, baseline, 2)
        self.assertEqual([c.args for c in wait.call_args_list], [(2,), (2,), (2,)])
        self.assertEqual([c.args[0] for c in enqueue.call_args_list], [baseline, advanced])


class StartupBindingTests(LogicalFixture):
    def test_lifetime_clears_cache_when_startup_binding_fails(self):
        caches = []
        constructor = logical._VerifierCache
        def cache(*args):
            value = constructor(*args)
            value.record('ref', 'user', 'synthetic-password')
            caches.append(value)
            return value
        previous = logical._verifier_cache
        with patch.object(logical, '_VerifierCache', side_effect=cache), \
                patch.object(self.provider, 'bind_registered', side_effect=ValueError('synthetic failure')):
            with self.assertRaises(OperationError), self.startup():
                self.fail('invalid binding')
        self.assertEqual(len(caches), 1)
        self.assertTrue(caches[0]._closed)
        self.assertFalse(caches[0]._records)
        self.assertEqual(caches[0]._key, b'')
        self.assertIs(logical._verifier_cache, previous)

    def test_lifetime_clears_records_on_normal_and_exception_exit(self):
        previous = logical._verifier_cache
        for fail in (False, True):
            with self.subTest(fail=fail):
                try:
                    with self.startup():
                        cache = logical._verifier_cache
                        self.assertIsNot(cache, previous)
                        cache.record('ref', 'user', 'synthetic-password')
                        self.assertTrue(cache._records)
                        if fail:
                            raise RuntimeError('synthetic failure')
                except RuntimeError:
                    if not fail:
                        raise
                self.assertTrue(cache._closed)
                self.assertFalse(cache._records)
                self.assertEqual(cache._key, b'')
                self.assertIs(logical._verifier_cache, previous)

    @contextmanager
    def startup(self, *, register=False):
        @contextmanager
        def opened(config):
            yield self.provider
        foundation = dict(environment_id='test-environment', warehouse_id='LOCAL',
                          foundation_id='foundation-real', revision=1)
        with patch('logical_provider.Provider', opened), patch('logical_foundation.load', return_value=foundation):
            with logical.lifetime(register=register, refresh_interval=0.01):
                yield

    def test_lifetime_joins_refresh_before_provider_close(self):
        started, exited = threading.Event(), threading.Event()
        caches = []
        @contextmanager
        def opened(config):
            try:
                yield self.provider
            finally:
                self.assertTrue(exited.is_set())
                self.assertTrue(caches[0]._closed)
                self.assertFalse(caches[0]._records)
        def refresh(stop, baseline, interval):
            started.set()
            if stop.wait(10):
                # Records must remain available until the owned watcher exits.
                if not logical._verifier_cache._closed and logical._verifier_cache._records:
                    exited.set()
        foundation = dict(environment_id='test-environment', warehouse_id='LOCAL',
                          foundation_id='foundation-real', revision=1)
        with patch('logical_provider.Provider', opened), patch('logical_foundation.load', return_value=foundation), \
                patch.object(logical, '_account_refresh_loop', side_effect=refresh):
            with logical.lifetime():
                caches.append(logical._verifier_cache)
                caches[0].record('ref', 'user', 'synthetic-password')
                self.assertTrue(started.wait(10))
                self.assertFalse(exited.is_set())
        self.assertTrue(exited.is_set())

    def foundation(self):
        with control_store.connect() as db:
            return dict(logical._foundation(db))

    def test_startup_binds_saved_positive_private_receipt_once(self):
        saved = runtime_profile.private_json(self.foundation()['verification_ref'])
        before = self.provider.probes
        with self.startup():
            self.assertEqual(self.provider.binds, [saved['result']])
            for _ in range(3):
                self.assertTrue(logical.capabilities(self.alice, 'admin')['available'])
            ack, _ = self.logical_create()
        logical.dispatch(ack['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')
        self.assertEqual(self.provider.probes, before+1)

    def test_register_startup_may_probe_then_bind_without_replacing_provenance(self):
        original = self.foundation()
        before = self.provider.probes
        with self.startup(register=True):
            pass
        self.assertEqual(self.provider.probes, before+2)
        self.assertEqual(self.foundation(), original)

    def test_startup_rejects_invalid_envelope_and_private_permissions(self):
        path = Path(self.foundation()['verification_ref'])
        original = path.read_text()
        saved = json.loads(original)
        variants = [None, {}, dict(saved, kind='other'), dict(saved, generation_id='foreign'),
                    dict(saved, revision=True), dict(saved, revision=2), dict(saved, result={}),
                    dict(saved, result=dict(saved['result'], ready=False)),
                    dict(saved, result=dict(saved['result'], revision=True)),
                    dict(saved, result=dict(saved['result'], incarnation={'namespace':'foreign'})),
                    dict(saved, result=dict(saved['result'], spi_sha256='c'*64))]
        for value in variants:
            with self.subTest(value=value):
                path.write_text(json.dumps(value))
                with self.assertRaises(OperationError), self.startup():
                    self.fail('invalid startup')
                self.assertEqual(self.provider.binds, [])
        path.write_text(original)
        path.chmod(0o644)
        with self.assertRaises(OperationError), self.startup():
            self.fail('public receipt')
        path.chmod(0o600)
        path.unlink()
        with self.assertRaises(OperationError), self.startup():
            self.fail('missing receipt')

    def test_startup_wrong_binding_or_metadata_fails_closed(self):
        original = self.foundation()
        with patch.object(self.provider, 'bind_registered', side_effect=ValueError('native UID differs')):
            with self.assertRaises(OperationError), self.startup():
                self.fail('binding mismatch')
        for field, value in [('config_ref', '/wrong/config'), ('verification_ref', '/missing/receipt.json')]:
            with logical.transaction() as db:
                db.execute('UPDATE logical_foundations SET '+field+'=?', (value,))
            with self.assertRaises(OperationError), self.startup():
                self.fail('missing provenance')
            with logical.transaction() as db:
                db.execute('UPDATE logical_foundations SET '+field+'=?', (original[field],))
        self.assertEqual(self.foundation(), original)
