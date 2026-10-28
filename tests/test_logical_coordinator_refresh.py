"""Coordinator durability and private authority refresh on real SQLite."""
from datetime import datetime, timezone
import sqlite3
from pathlib import Path
from contextlib import contextmanager
import json
import threading
from unittest.mock import patch

from tests.test_logical_creations import LogicalFixture
import control_store
import database_creations as legacy
import grant_management
import logical_creations as logical
import logical_registry as registry
import logical_provider
import request_auth
from warehouse_operations import OperationError


class CoordinatorRefreshTests(LogicalFixture):
    def ready(self, name='research', key='create'):
        ack, _ = self.logical_create(name, key, [dict(principal_id='reader', maximum_role='bi_viewer')])
        logical.dispatch(ack['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')
        return ack, self.detail(ack)['database_id']

    def refresh(self, dbid, role='revoked', actor=None):
        actor = actor or self.bob  # Creator ownership is deliberately different.
        with patch.object(logical, 'dispatch_async'):
            with control_store.connect() as db:
                revision = db.execute('SELECT revision FROM grants WHERE database_id=? AND principal_id=?',
                                      (dbid, 'reader')).fetchone()[0]
            result = grant_management._mutate(actor, 'admin', dbid,
                dict(principal_id='reader', maximum_role=role, expected_revision=revision), 'grant-'+logical._id())
        return result['auth_refresh_request_id']

    def rows(self, table):
        with control_store.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]

    def test_nested_ack_before_next_mutation_and_iam_datetime(self):
        ack, _ = self.logical_create()
        def role(db):
            first = self.provider.callback('create-role', {'target':'role'}, lambda: dict(CreateDate=datetime(2026,1,1,tzinfo=timezone.utc)))
            self.assertIsInstance(first['CreateDate'], datetime)
            def next_call():
                row = next(r for r in self.rows('logical_effects') if r['effect_key']=='role/leaf/create-role')
                self.assertEqual(row['state'], 'ACKNOWLEDGED')
                self.assertEqual(json.loads(Path(row['private_ref']).read_text())['result']['CreateDate'], '2026-01-01T00:00:00+00:00')
                return (200, b'raw response')
            return self.provider.callback('attach-policy', {'target':'policy'}, next_call)
        self.provider.create_role = role
        logical.dispatch(ack['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')
        refs = [r['private_ref'] for r in self.rows('logical_effects') if '/leaf/' in r['effect_key']]
        self.assertTrue(all(refs))

    def test_lost_leaf_response_never_reissued_even_reconcile(self):
        ack, _ = self.logical_create()
        calls = []
        def issue(db):
            def send():
                calls.append('sent')
                raise TimeoutError()
            return self.provider.callback('issue', {'principal':'original'}, send)
        self.provider.issue_consumer = issue
        logical.dispatch(ack['request_id'])
        reconcile = self.mutation(ack, 'RECONCILE', 'reconcile')
        logical.dispatch(reconcile['request_id'])
        self.assertEqual(calls, ['sent'])
        self.assertEqual(self.detail(ack)['progress_code'], 'UNKNOWN')
        with self.assertRaises(OperationError):
            self.mutation(ack, 'RESUME', 'resume')

    def test_known_rejection_fresh_attempt_keeps_history_and_ack_children(self):
        ack, _ = self.logical_create()
        calls = []
        reject = True
        def role(db):
            self.provider.callback('one', {'target':'one'}, lambda: calls.append('one') or {})
            def second():
                calls.append('two')
                if reject:
                    raise logical_provider.Rejected('KNOWN_NO_EFFECTS')
                return {}
            return self.provider.callback('two', {'target':'two'}, second)
        self.provider.create_role = role
        logical.dispatch(ack['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'BLOCKED')
        rejected = [r for r in self.rows('logical_effects') if r['state']=='REJECTED']
        reject = False
        resumed = self.mutation(ack, 'RESUME', 'resume')
        logical.dispatch(resumed['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')
        self.assertEqual(calls, ['one','two','two'])
        for old in rejected:
            self.assertIn(old, self.rows('logical_effects'))
        self.assertTrue(any('/attempt/'+resumed['request_id'] in r['effect_key'] for r in self.rows('logical_effects')))

    def test_partial_observer_cannot_resolve_unknown_child(self):
        ack, _ = self.logical_create()
        def role(db):
            return self.provider.callback('unknown', {'target':'role'}, lambda: (_ for _ in ()).throw(TimeoutError()))
        self.provider.create_role = role
        logical.dispatch(ack['request_id'])
        self.provider.check_role = lambda db: dict(verified=True, complete=False, kind='role', **db)
        logical.dispatch(self.mutation(ack, 'RECONCILE', 'observe')['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'UNKNOWN')

    def test_revision_conflict_and_database_lifecycle(self):
        ack, dbid = self.ready()
        with self.assertRaises(OperationError) as error:
            logical.mutate(self.alice, 'admin', ack['creation_id'], 'WITHDRAW', dict(expected_revision=1), 'stale')
        self.assertEqual(error.exception.code, 'REVISION_CONFLICT')
        for state in ('retiring','retired','RECONCILING'):
            with logical.transaction() as db:
                db.execute('UPDATE databases SET state=? WHERE id=?', (state, dbid))
            detail = self.detail(ack)
            self.assertEqual(detail['lifecycle'], state.upper())
            self.assertEqual(detail['progress_code'], 'READY')
            self.assertEqual(detail['next_actions'], [])

    def test_foundation_arrays_and_refuses_busy_legacy_switch(self):
        generation = self.rows('warehouse_config_generations')[0]
        self.assertEqual(json.loads(generation['database_revisions']), [])
        self.assertEqual(json.loads(generation['grant_revisions']), [])
        self.assertIsInstance(json.loads(generation['configuration_refs']), list)
        with logical.transaction() as db:
            db.execute('DELETE FROM logical_foundations')
            db.execute("UPDATE warehouse_controls SET state='UNKNOWN',operation_id=NULL")
        with self.assertRaises(OperationError):
            logical.register_foundation()
        self.assertEqual(self.rows('warehouse_controls')[0]['state'], 'UNKNOWN')

    def test_grant_refresh_only_target_requires_engine_denial(self):
        ack, dbid = self.ready()
        other, _ = self.ready('other', 'other')
        bindings = self.rows('logical_bindings')
        identities = self.rows('logical_backend_identities')
        target = [r for r in identities if r['database_id']==dbid and r['principal_id']=='reader']
        unaffected = [r for r in identities if r not in target]
        verified = []
        def denied(descriptor, identity):
            verified.append(identity)
            self.assertIn('password', identity)
            return dict(verified=True, control_positive=True, identity_denied=True)
        self.provider.verify_revoked = denied
        refresh = self.refresh(dbid)
        logical.dispatch(refresh)
        request = next(r for r in self.rows('logical_requests') if r['id']==refresh)
        self.assertEqual(request['dispatch_state'], 'COMPLETE')
        self.assertTrue(json.loads(request['result_json'])['engine_verified'])
        self.assertEqual(len(verified), len(target))
        self.assertEqual(self.rows('logical_bindings'), bindings)
        for row in unaffected:
            self.assertIn(row, self.rows('logical_backend_identities'))
        self.assertTrue(all(next(r for r in self.rows('logical_backend_identities') if r['id']==old['id'])['state']=='REVOKED' for old in target))
        self.assertEqual(self.detail(other)['progress_code'], 'READY')
        payload = self.provider.publications[-1]
        self.assertTrue(all('client_id' in value for value in payload['credentials'].values()))
        self.assertTrue(all('password' not in value for value in payload['credentials'].values()))

    def test_missing_or_partial_revocation_proof_never_engine_verified(self):
        for proof in (None, dict(verified=True, identity_denied=True)):
            with self.subTest(proof=proof):
                ack, dbid = self.ready('db'+logical._id()[:6], logical._id())
                if proof is not None:
                    self.provider.verify_revoked = lambda *args: proof
                refresh = self.refresh(dbid)
                logical.dispatch(refresh)
                row = next(r for r in self.rows('logical_requests') if r['id']==refresh)
                self.assertNotEqual(row['dispatch_state'], 'COMPLETE')
                self.assertIsNone(row['result_json'])
                self.assertEqual(self.detail(ack)['progress_code'], 'READY')
                self.assertFalse(any(r['principal_id']=='reader' and r['database_id']==dbid and r['state']=='ACTIVE' for r in self.rows('logical_backend_identities')))

    def test_promotion_mints_candidates_with_actual_credentials_and_no_binding_change(self):
        ack, dbid = self.ready()
        self.accounts['reader']['role'] = 'data_engineer'
        self.save_accounts()
        before = self.rows('logical_bindings')
        self.provider.verify_revoked = lambda *args: dict(verified=True, control_positive=True, identity_denied=True)
        seen = []
        original = self.provider.verify_target
        def verify(db, identity):
            self.assertEqual(set(identity), {'principal_id','database_id','selected_role','grant_epoch','policy_revision','credential_ref','username','password'})
            self.assertTrue(identity['username'].startswith('shirokuma_db_'))
            self.assertTrue(identity['password'])
            with control_store.connect() as conn:
                self.assertEqual(conn.execute('SELECT state FROM logical_backend_identities WHERE credential_ref=?', (identity['credential_ref'],)).fetchone()[0], 'CANDIDATE')
            seen.append(identity)
            return original(db, identity)
        self.provider.verify_target = verify
        refresh = self.refresh(dbid, 'data_engineer')
        logical.dispatch(refresh)
        self.assertEqual(next(r for r in self.rows('logical_requests') if r['id']==refresh)['dispatch_state'], 'COMPLETE')
        self.assertEqual({i['selected_role'] for i in seen}, {'bi_viewer','data_engineer'})
        self.assertEqual(before, self.rows('logical_bindings'))

    def test_account_change_finite_targeted_and_executor_loss_stops_dispatch(self):
        ack, dbid = self.ready()
        baseline = json.loads(self.account_file.read_text())
        self.accounts['reader']['disabled'] = True
        self.save_accounts()
        self.provider.verify_revoked = lambda *args: dict(verified=True, control_positive=True, identity_denied=True)
        after = logical._enqueue_account_changes(baseline)
        refreshes = [r for r in self.rows('logical_requests') if r['action']=='AUTH_REFRESH']
        self.assertEqual(len(refreshes), 1)
        self.assertEqual(refreshes[0]['dispatch_state'], 'COMPLETE')
        logical._enqueue_account_changes(after)
        self.assertEqual(len([r for r in self.rows('logical_requests') if r['action']=='AUTH_REFRESH']), 1)
        self.accounts['executor']['disabled'] = True
        self.accounts['alice']['disabled'] = True
        self.save_accounts()
        with self.assertRaises(OperationError):
            logical._enqueue_account_changes(after)

    def test_admin_account_loss_tombstone_and_other_admin_unchanged(self):
        ack, dbid = self.ready()
        before = self.rows('logical_backend_identities')
        baseline = json.loads(self.account_file.read_text())
        self.accounts['alice']['platform_admin'] = False
        self.save_accounts()
        self.provider.verify_revoked = lambda *args: dict(verified=True, control_positive=True, identity_denied=True)
        logical._enqueue_account_changes(baseline)
        self.assertEqual(next(r for r in self.rows('db_creation_principal_gates') if r['principal_id']=='alice')['state'], 'STOPPED')
        for row in before:
            if row['principal_id']!='alice':
                self.assertIn(row, self.rows('logical_backend_identities'))
        baseline = json.loads(self.account_file.read_text())
        self.accounts['alice']['platform_admin'] = True
        self.save_accounts()
        logical._enqueue_account_changes(baseline)
        self.assertEqual(next(r for r in self.rows('db_creation_principal_gates') if r['principal_id']=='alice')['state'], 'STOPPED')

    def test_private_action_not_public(self):
        ack, _ = self.ready()
        with self.assertRaises(OperationError) as error:
            logical.mutate(self.bob, 'admin', ack['creation_id'], 'AUTH_REFRESH', dict(expected_revision=1), 'private')
        self.assertEqual(error.exception.code, 'CREATION_MODE_MISMATCH')

    def test_configured_executor_lifetime_and_owned_task_cleanup(self):
        opened, closed = [], []
        @contextmanager
        def provider(config):
            opened.append(config)
            try:
                yield self.provider
            finally:
                closed.append(True)
        foundation = dict(environment_id='test-environment', warehouse_id='LOCAL',
                          foundation_id='foundation-real', revision=1)
        with patch.object(logical_provider, 'Provider', provider), patch('logical_foundation.load', return_value=foundation):
            with logical.lifetime(refresh_interval=0.01):
                self.assertEqual(logical._executor_id, 'executor')
                self.assertTrue(any(t.name=='logical-authority-refresh' for t in threading.enumerate()))
            self.assertFalse(any(t.name=='logical-authority-refresh' for t in threading.enumerate()))
            self.assertEqual(len(closed), 1)
            self.logical_config['executor_id'] = 'reader'
            with self.assertRaises(OperationError):
                with logical.lifetime():
                    self.fail('must not pick another administrator')
            self.assertEqual(len(closed), 2)

    def test_register_refuses_nonempty_legacy_state(self):
        import catalog_admin
        with logical.transaction() as db:
            db.execute('DELETE FROM logical_foundations')
            db.execute('UPDATE warehouse_controls SET applied_generation_id=NULL')
            db.execute('DELETE FROM warehouse_config_generations')
            catalog_admin._create_database(db, 'alice', 'legacydb', 'legacy-key', '{}')
        with self.assertRaises(OperationError):
            logical.register_foundation()
        self.assertEqual(self.rows('warehouse_controls')[0]['applied_generation_id'], None)

    def test_saved_consumer_leaf_recovered_without_minting(self):
        ack, _ = self.logical_create()
        sends = []
        secret = dict(client_id='id', client_secret='private-secret', principal_name='original', realm='realm')
        def issue(db):
            raw = self.provider.callback('issue', {'principal':'original'},
                lambda: sends.append(True) or json.dumps(secret).encode())
            return json.loads(raw)
        self.provider.issue_consumer = issue
        self.provider.check_consumer = lambda db, value: self.provider.observation('consumer', db) if value==secret else dict(verified=False)
        original = logical._receipt
        fail = True
        def receipt(value):
            if fail and value.get('result') == secret:
                raise OSError('parent receipt unavailable')
            return original(value)
        with patch.object(logical, '_receipt', side_effect=receipt):
            logical.dispatch(ack['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'UNKNOWN')
        fail = False
        logical.dispatch(self.mutation(ack, 'RECONCILE', 'observe')['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'BLOCKED')
        logical.dispatch(self.mutation(ack, 'RESUME', 'resume')['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')
        self.assertEqual(sends, [True])

    def test_exact_parent_observer_resolves_only_matching_children(self):
        ack, _ = self.logical_create()
        def role(db):
            return self.provider.callback('unknown', {'target':'role'}, lambda: (_ for _ in ()).throw(TimeoutError()))
        self.provider.create_role = role
        logical.dispatch(ack['request_id'])
        self.provider.check_role = lambda db: self.provider.observation('role', db)
        with logical.transaction() as db:
            root = logical._root(db, ack['creation_id'])
            descriptor = logical._descriptor(db, root)
            db.execute("INSERT INTO logical_effects VALUES (?,? ,?,'UNKNOWN',NULL,NULL,'then','then')",
                       (ack['creation_id'], 'catalog/leaf/unrelated', json.dumps({'target':'other'})))
        logical.dispatch(self.mutation(ack, 'RECONCILE', 'observe')['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'UNKNOWN')
        unrelated = next(r for r in self.rows('logical_effects') if r['effect_key']=='catalog/leaf/unrelated')
        self.assertEqual(unrelated['state'], 'UNKNOWN')
        self.assertIsNone(unrelated['private_ref'])
        # A separate complete catalog parent can resolve only that parent's group.
        with logical.transaction() as db:
            db.execute("INSERT INTO logical_effects VALUES (?,? ,?,'UNKNOWN',NULL,NULL,'then','then')",
                       (ack['creation_id'], 'catalog', logical._json(descriptor)))
        logical.dispatch(self.mutation(ack, 'RECONCILE', 'observe-catalog')['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'BLOCKED')
        child = next(r for r in self.rows('logical_effects') if r['effect_key']=='role/leaf/unknown')
        self.assertEqual(child['state'], 'OBSERVED_APPLIED')
        logical.dispatch(self.mutation(ack, 'RESUME', 'resume')['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')

    def test_refresh_final_authority_cas(self):
        ack, dbid = self.ready()
        self.accounts['reader']['role'] = 'data_engineer'
        self.save_accounts()
        self.provider.verify_revoked = lambda *args: dict(verified=True, control_positive=True, identity_denied=True)
        changed = False
        def change(key):
            nonlocal changed
            if key == 'verify' and not changed:
                changed = True
                with logical.transaction() as db:
                    db.execute("UPDATE grants SET maximum_role='revoked',revision=revision+1 WHERE principal_id='reader' AND database_id=?", (dbid,))
        self.provider.hook = change
        request = self.refresh(dbid, 'data_engineer')
        logical.dispatch(request)
        self.assertNotEqual(next(r for r in self.rows('logical_requests') if r['id']==request)['dispatch_state'], 'COMPLETE')
        self.assertFalse(any(r['database_id']==dbid and r['principal_id']=='reader' and r['state']=='ACTIVE' for r in self.rows('logical_backend_identities')))
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')

    def test_probe_missing_publisher_hashes_fails_closed(self):
        probe = self.provider.probe
        def incomplete():
            value = probe()
            del value['hashes']
            return value
        self.provider.probe = incomplete
        with self.assertRaises(OperationError):
            logical.register_foundation()

    def test_uncertain_refresh_publication_never_replayed_or_activated(self):
        ack, dbid = self.ready()
        original = self.provider.publish_material
        calls = []
        def lost(db, payload):
            calls.append(True)
            original(db, payload)
            raise TimeoutError()
        self.provider.publish_material = lost
        refresh = self.refresh(dbid)
        logical.dispatch(refresh)
        logical.dispatch(refresh)
        request = next(r for r in self.rows('logical_requests') if r['id']==refresh)
        self.assertEqual(request['dispatch_state'], 'UNKNOWN')
        self.assertIsNone(request['result_json'])
        self.assertEqual(calls, [True])
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')
        self.assertFalse(any(r['database_id']==dbid and r['principal_id']=='reader' and r['state']=='ACTIVE' for r in self.rows('logical_backend_identities')))

    def test_admin_refresh_unions_grant_roles_and_keeps_admin_priority(self):
        ack, dbid = self.ready()
        self.accounts['reader'].update(role='admin', platform_admin=True)
        self.save_accounts()
        self.provider.verify_revoked = lambda *args: dict(verified=True, control_positive=True, identity_denied=True)
        request = self.refresh(dbid, 'admin')
        logical.dispatch(request)
        rows = [r for r in self.rows('logical_backend_identities') if r['principal_id']=='reader' and r['state']=='ACTIVE']
        self.assertEqual({r['selected_role'] for r in rows}, {'admin','data_engineer','bi_viewer'})
        admin = next(r for r in rows if r['selected_role']=='admin')
        self.assertEqual(admin['authority_source'], 'platform_admin')
        self.assertIsNone(admin['grant_epoch'])
        self.assertTrue(all(r['authority_source']=='grant' for r in rows if r['selected_role']!='admin'))

    def test_new_admin_refreshes_all_ready_databases_once(self):
        self.ready()
        self.ready('other', 'other')
        baseline = json.loads(self.account_file.read_text())
        self.accounts['newadmin'] = dict(self.accounts['alice'])
        self.save_accounts()
        after = logical._enqueue_account_changes(baseline)
        requests = [r for r in self.rows('logical_requests') if r['action']=='AUTH_REFRESH']
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(r['dispatch_state']=='COMPLETE' for r in requests))
        self.assertEqual(len([r for r in self.rows('logical_backend_identities') if r['principal_id']=='newadmin' and r['state']=='ACTIVE']), 2)
        logical._enqueue_account_changes(after)
        self.assertEqual(len([r for r in self.rows('logical_requests') if r['action']=='AUTH_REFRESH']), 2)

    def test_credential_failure_rolls_back_candidate_rows(self):
        ack, _ = self.logical_create()
        original = logical._credential
        calls = []
        def fail(ref, value):
            original(ref, value)
            calls.append(ref)
            if ref.startswith('identity_'):
                raise OSError('crash before row commit')
        with patch.object(logical, '_credential', side_effect=fail):
            logical.dispatch(ack['request_id'])
        self.assertTrue(calls)
        self.assertEqual(self.rows('logical_backend_identities'), [])
        self.assertTrue(all((self.credentials/(ref+'.json')).is_file() for ref in calls))

    def test_candidate_files_exist_before_row_insert_and_are_immutable(self):
        ack, _ = self.logical_create()
        original = registry.put_identity
        def put(db, identity):
            if identity['state']=='CANDIDATE':
                self.assertTrue((self.credentials/(identity['credential_ref']+'.json')).is_file())
            return original(db, identity)
        with patch.object(registry, 'put_identity', side_effect=put):
            logical.dispatch(ack['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')
        identity = self.rows('logical_backend_identities')[0]
        ref = identity['credential_ref']
        path = self.credentials/(ref+'.json')
        before = path.read_bytes()
        value = json.loads(before)
        logical._credential(ref, {k:v for k,v in value.items() if k!='version'})
        with self.assertRaises(OperationError):
            logical._credential(ref, dict(username='different', password='different'))
        self.assertEqual(path.read_bytes(), before)

    def test_foundation_rejects_old_completed_run_without_rebinding(self):
        with logical.transaction() as db:
            db.execute('DELETE FROM logical_foundations')
            db.execute('UPDATE warehouse_controls SET applied_generation_id=NULL')
            db.execute('DELETE FROM warehouse_config_generations')
            db.execute("INSERT INTO runs (id,job_name,actor,role,definition,arguments,status,created_at) VALUES ('old','legacy','alice','admin','{}','{}','completed','then')")
        before = self.snapshot()
        with self.assertRaises(OperationError):
            logical.register_foundation()
        self.assertEqual(self.snapshot(), before)

    def test_wrong_descriptor_observer_keeps_unknown(self):
        ack, _ = self.logical_create()
        self.provider.hook = lambda key: (_ for _ in ()).throw(TimeoutError()) if key=='role' else None
        logical.dispatch(ack['request_id'])
        self.provider.hook = None
        for field, value in [('database_id','other'), ('sql_catalog','other'), ('generation_id','other'), ('foundation_revision',99), ('foundation_revision',True), ('kind','catalog'), ('complete',False), ('complete',None)]:
            self.provider.check_role = lambda db, field=field, value=value: dict(self.provider.observation('role', db), **{field:value})
            logical.dispatch(self.mutation(ack, 'RECONCILE', logical._id())['request_id'])
            self.assertEqual(self.detail(ack)['progress_code'], 'UNKNOWN')
        def absent(db):
            result = self.provider.observation('role', db)
            result.pop('complete')
            return result
        self.provider.check_role = absent
        logical.dispatch(self.mutation(ack, 'RECONCILE', logical._id())['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'UNKNOWN')

    def test_metadata_target_verification_is_insufficient(self):
        for proof in [dict(verified=True), dict(verified=True, identity_positive=True, identity_negative=True,
                                               principal_id='someone', selected_role='admin', grant_epoch=None, policy_revision=1)]:
            ack, _ = self.logical_create('d'+logical._id()[:8], logical._id())
            self.provider.verify_target = lambda *args: proof
            logical.dispatch(ack['request_id'])
            self.assertNotEqual(self.detail(ack)['progress_code'], 'READY')

    def test_current_schema_semantic_observation_recovers_without_sdk_ack(self):
        ack, _ = self.logical_create()
        def role(db):
            return self.provider.callback('lost', {'target':'role'}, lambda: (_ for _ in ()).throw(TimeoutError()))
        self.provider.create_role = role
        logical.dispatch(ack['request_id'])
        logical.dispatch(self.mutation(ack, 'RECONCILE', 'observe')['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'BLOCKED')
        effects = [r for r in self.rows('logical_effects') if r['effect_key'].startswith('role')]
        self.assertTrue(all(r['state']=='OBSERVED_APPLIED' for r in effects))
        self.assertTrue(all(r['private_ref'] for r in effects))

    def test_concurrent_snapshot_excludes_candidate_without_material_receipt(self):
        _, dbid = self.ready()
        self.accounts['reader']['role'] = 'data_engineer'
        self.save_accounts()
        refresh = self.refresh(dbid, 'data_engineer')
        self.provider.verify_revoked = lambda *args: dict(verified=True, control_positive=True, identity_denied=True)
        arrived, release = threading.Event(), threading.Event()
        original = logical._receipt
        candidate_users = []
        def pause(value):
            result = value.get('result')
            if isinstance(result, dict) and 'revoked' in result:
                candidate_users.extend(i['backend_username'] for i in result['identities'])
                arrived.set()
                self.assertTrue(release.wait(10))
            return original(value)
        with patch.object(logical, '_receipt', side_effect=pause):
            thread = logical.dispatch_async(refresh)
            self.assertTrue(arrived.wait(5))
            try:
                other, _ = self.logical_create('other', 'other')
                logical.dispatch(other['request_id'])
                self.assertEqual(self.detail(other)['progress_code'], 'READY')
                snapshot = self.provider.publications[-1]
                self.assertTrue(candidate_users)
                self.assertTrue(all(user not in snapshot['password_db'] for user in candidate_users))
            finally:
                release.set()
                thread.join(10)
        self.assertFalse(thread.is_alive())

    def unknown_verification(self):
        import hashlib
        ack, _ = self.logical_create()
        writes, engines = [], []
        def verify(descriptor, engine):
            engines.append(dict(engine))
            marker = hashlib.sha256((descriptor['generation_id']+':'+str(descriptor['foundation_revision'])+':'+descriptor['database_id']).encode()).digest()
            intent = dict(method='put_object', service='s3',
                target=self.provider.config['storage']['bucket']+'/'+descriptor['database_id']+'/.shirokuma/creation-marker',
                payload_sha256=hashlib.sha256(marker).hexdigest())
            key = hashlib.sha256(logical._json(intent).encode()).hexdigest()
            def put():
                writes.append(engine['username'])
                if len(writes) == 1:
                    raise TimeoutError('lost PUT response')
                return dict(ok=True)
            self.provider.callback(key, intent, put)
            return self.provider.verification(descriptor, engine)
        self.provider.verify_target = verify
        logical.dispatch(ack['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'UNKNOWN')
        return ack, writes, engines

    def test_verify_read_only_recovery_then_explicit_resume_without_duplicate_put(self):
        ack, writes, engines = self.unknown_verification()
        before_calls = list(self.provider.calls)
        before_credentials = {p.name:p.read_bytes() for p in self.credentials.glob('*.json')}
        observed = []
        def check(descriptor, engine):
            observed.append(dict(engine))
            self.assertEqual(set(engine), {'principal_id','database_id','selected_role','grant_epoch',
                'policy_revision','credential_ref','username','password'})
            self.assertEqual(engine, engines[0])
            return self.provider.verification(descriptor, engine)
        self.provider.check_verify_target = check
        logical.dispatch(self.mutation(ack, 'RECONCILE', 'observe')['request_id'])
        self.assertEqual(observed, [engines[0]])
        self.assertEqual(self.provider.calls, before_calls)
        self.assertEqual(len(writes), 1)
        self.assertEqual({p.name:p.read_bytes() for p in self.credentials.glob('*.json')}, before_credentials)
        self.assertEqual(self.detail(ack)['progress_code'], 'BLOCKED')
        effects = [r for r in self.rows('logical_effects') if r['effect_key'].startswith('verify_')]
        self.assertEqual(len(effects), 2)
        self.assertTrue(all(r['state']=='OBSERVED_APPLIED' for r in effects))
        logical.dispatch(self.mutation(ack, 'RESUME', 'resume')['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')
        self.assertEqual(writes.count(engines[0]['username']), 1)
        self.assertEqual(self.provider.calls.count('consumer'), 1)

    def test_verification_observation_requires_every_semantic_pin(self):
        ack, writes, engines = self.unknown_verification()
        bad = dict(database_id='other', sql_catalog='other', generation_id='other', foundation_revision=True,
            principal_id='other', selected_role='bi_viewer', grant_epoch=1, policy_revision=True,
            credential_ref='other', username='other', kind='catalog', complete=False, verified=False,
            identity_positive=False, identity_negative=False, prefix_positive=False, prefix_negative=False)
        for field, value in bad.items():
            for missing in (False, True):
                with self.subTest(field=field, missing=missing):
                    def check(descriptor, engine):
                        proof = self.provider.verification(descriptor, engine)
                        if missing:
                            proof.pop(field)
                        else:
                            proof[field] = value
                        return proof
                    self.provider.check_verify_target = check
                    logical.dispatch(self.mutation(ack, 'RECONCILE', logical._id())['request_id'])
                    self.assertEqual(self.detail(ack)['progress_code'], 'UNKNOWN')
                    self.assertTrue(all(r['state']=='UNKNOWN' for r in self.rows('logical_effects') if r['effect_key'].startswith('verify_')))
        self.assertEqual(len(writes), 1)
        self.assertEqual(len(engines), 1)
        self.assertEqual(self.provider.calls.count('consumer'), 1)

    def test_verify_recovery_matches_current_material_and_only_exact_marker_child(self):
        ack, writes, engines = self.unknown_verification()
        parent = next(r for r in self.rows('logical_effects') if r['effect_key'].startswith('verify_') and '/leaf/' not in r['effect_key'])
        candidate_id = json.loads(parent['intent_json'])['identity_id']
        with logical.transaction() as db:
            candidate = registry.get_identity(db, candidate_id)
            with self.assertRaises(sqlite3.IntegrityError) as error:
                db.execute("UPDATE logical_backend_identities SET credential_ref='wrong-ref' WHERE id=?", (candidate_id,))
            self.assertIn('immutable', str(error.exception))
        observed = []
        def check(descriptor, engine):
            observed.append(True)
            return self.provider.verification(descriptor, engine)
        self.provider.check_verify_target = check
        original = registry.get_identity
        for changes in (dict(credential_ref='wrong-ref'), dict(revision=2)):
            def wrong(db, identity_id):
                value = original(db, identity_id)
                return dict(value, **changes) if identity_id==candidate_id else value
            with patch.object(registry, 'get_identity', side_effect=wrong):
                logical.dispatch(self.mutation(ack, 'RECONCILE', logical._id())['request_id'])
        self.assertEqual(observed, [])
        self.assertEqual(self.detail(ack)['progress_code'], 'UNKNOWN')
        marker_child = next(r for r in self.rows('logical_effects') if '/leaf/' in r['effect_key'] and r['effect_key'].startswith('verify_'))
        mismatched_key = marker_child['effect_key']+'/attempt/wrong'
        with logical.transaction() as db:
            for key in (parent['effect_key']+'/leaf/unrelated', mismatched_key):
                db.execute("INSERT INTO logical_effects VALUES (?,?,?,'UNKNOWN',NULL,NULL,'then','then')",
                    (ack['creation_id'],key,logical._json(dict(method='put_object',target='elsewhere'))))
        logical.dispatch(self.mutation(ack, 'RECONCILE', 'correct-candidate')['request_id'])
        effects = {r['effect_key']:r for r in self.rows('logical_effects')}
        self.assertEqual(effects[parent['effect_key']]['state'], 'OBSERVED_APPLIED')
        self.assertEqual(effects[parent['effect_key']+'/leaf/unrelated']['state'], 'UNKNOWN')
        self.assertEqual(effects[mismatched_key]['state'], 'UNKNOWN')
        self.assertEqual(effects[marker_child['effect_key']]['state'], 'OBSERVED_APPLIED')
        self.assertEqual(self.detail(ack)['progress_code'], 'UNKNOWN')
        self.assertEqual(len(writes), 1)

    def test_creation_reconcile_never_observes_auth_refresh_effects(self):
        ack, _ = self.logical_create()
        self.provider.hook = lambda key: (_ for _ in ()).throw(TimeoutError()) if key=='role' else None
        logical.dispatch(ack['request_id'])
        with logical.transaction() as db:
            descriptor = logical._descriptor(db, logical._root(db, ack['creation_id']))
            db.execute("INSERT INTO logical_effects VALUES (?,?,?,'UNKNOWN',NULL,NULL,'then','then')",
                (ack['creation_id'],'refresh/private/publication',logical._json(dict(descriptor,material_ref='must-not-read'))))
        self.provider.hook = None
        logical.dispatch(self.mutation(ack, 'RECONCILE', 'observe')['request_id'])
        refresh = next(r for r in self.rows('logical_effects') if r['effect_key'].startswith('refresh/'))
        self.assertEqual(refresh['state'], 'UNKNOWN')
        self.assertIsNone(refresh['private_ref'])
        role = next(r for r in self.rows('logical_effects') if r['effect_key']=='role')
        self.assertEqual(role['state'], 'OBSERVED_APPLIED')

    def test_newly_valid_admin_does_not_release_stopped_gate(self):
        _, dbid = self.ready()
        baseline = json.loads(self.account_file.read_text())
        self.accounts['reader'].update(role='admin', platform_admin=True)
        self.save_accounts()
        with logical.transaction() as db:
            db.execute("INSERT INTO db_creation_principal_gates VALUES (?,?,'STOPPED',2,'ACCOUNT_AUTHORITY_CHANGED',NULL,'now')",
                       (self.logical_config['environment_id'], 'reader'))
        self.provider.verify_revoked = lambda *args: dict(verified=True, control_positive=True, identity_denied=True)
        logical._enqueue_account_changes(baseline)
        gate = next(r for r in self.rows('db_creation_principal_gates') if r['principal_id']=='reader')
        self.assertEqual(gate['state'], 'STOPPED')
        identities = [r for r in self.rows('logical_backend_identities') if r['database_id']==dbid and r['principal_id']=='reader' and r['state']=='ACTIVE']
        self.assertFalse(any(r['authority_source']=='platform_admin' for r in identities))

    def test_target_proof_requires_explicit_admin_null_epoch_pin(self):
        ack, _ = self.logical_create()
        original = self.provider.verify_target
        def missing(db, identity):
            proof = original(db, identity)
            proof.pop('grant_epoch')
            return proof
        self.provider.verify_target = missing
        logical.dispatch(ack['request_id'])
        self.assertNotEqual(self.detail(ack)['progress_code'], 'READY')


    def test_publication_exact_readback_durable_leaf_and_full_artifact_lock(self):
        import fcntl
        ack, _ = self.logical_create()
        stages = []
        def locked(stage):
            with open(logical._private_dir()/'publication.lock', 'a') as other:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
            stages.append(stage)
        hashes = self.provider.publication_hashes
        publish = self.provider.publish_material
        check = self.provider.check_material
        render = logical._render_material
        payloads = []
        def snapshot(*args, **kwargs):
            locked('snapshot-render')
            return render(*args, **kwargs)
        def current(descriptor):
            locked('hashes')
            return hashes(descriptor)
        def write(descriptor, material):
            locked('write')
            payloads.append((descriptor, material))
            return publish(descriptor, material)
        def read(descriptor, material):
            locked('readback')
            self.assertIs(descriptor, payloads[0][0])
            self.assertIs(material, payloads[0][1])
            parent = next(r for r in self.rows('logical_effects') if r['effect_key']=='publication')
            leaf = next(r for r in self.rows('logical_effects') if r['effect_key']=='publication/leaf/publish')
            self.assertEqual(leaf['state'], 'ACKNOWLEDGED')
            self.assertTrue(leaf['private_ref'])
            self.assertEqual(parent['state'], 'INTENT')
            self.assertEqual(logical.runtime_profile.private_json(json.loads(parent['intent_json'])['material_ref']), material)
            return check(descriptor, material)
        with patch.object(logical, '_render_material', side_effect=snapshot), \
                patch.object(self.provider, 'publication_hashes', side_effect=current), \
                patch.object(self.provider, 'publish_material', side_effect=write), \
                patch.object(self.provider, 'check_material', side_effect=read):
            logical.dispatch(ack['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')
        self.assertEqual(stages, ['hashes', 'snapshot-render', 'write', 'readback'])
        parent = next(r for r in self.rows('logical_effects') if r['effect_key']=='publication')
        result = logical.runtime_profile.private_json(parent['private_ref'])['result']
        self.assertEqual(result['kind'], 'publication')
        self.assertTrue(result['complete'])
        self.assertEqual(set(payloads[0][1]['expected_hashes']),
                         {'credentials.properties', 'password.db', 'rules.json', 'guard.rego'})

    def test_publication_hash_validator_rejects_malformed_before_write(self):
        hashes = self.provider.publication_hashes({})
        bad = [None, {}, dict(hashes, extra='a'*64),
               {k:v for k,v in hashes.items() if k!='target.credentials.properties'},
               dict(hashes, **{'target.password.db': True}),
               dict(hashes, **{'target.password.db': 'A'*64}),
               dict(hashes, **{'target.password.db': 'MISSING'})]
        for index, value in enumerate(bad):
            with self.subTest(value=value), patch.object(self.provider, 'publication_hashes', return_value=value):
                ack, _ = self.logical_create('hash_'+str(index), 'hash-'+str(index))
                logical.dispatch(ack['request_id'])
                self.assertNotEqual(self.detail(ack)['progress_code'], 'READY')
        self.assertEqual(self.provider.publications, [])

    def test_post_ack_readback_failure_is_unknown_then_reconcile_resume_without_republish(self):
        ack, _ = self.logical_create()
        with patch.object(self.provider, 'check_material', return_value=dict(verified=False)):
            logical.dispatch(ack['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'UNKNOWN')
        parent = next(r for r in self.rows('logical_effects') if r['effect_key']=='publication')
        self.assertEqual(parent['state'], 'UNKNOWN')
        self.assertIsNone(parent['private_ref'])
        self.assertEqual(self.provider.calls.count('publication'), 1)
        self.assertEqual(self.provider.calls.count('consumer'), 1)
        with patch.object(self.provider, 'live_health', side_effect=AssertionError('reconcile is read only')):
            logical.dispatch(self.mutation(ack, 'RECONCILE', 'reconcile')['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'BLOCKED')
        parent = next(r for r in self.rows('logical_effects') if r['effect_key']=='publication')
        self.assertEqual(parent['state'], 'OBSERVED_APPLIED')
        with patch.object(logical, '_render_material', side_effect=AssertionError('no new snapshot')), \
                patch.object(self.provider, 'publication_hashes', side_effect=AssertionError('no hash read')), \
                patch.object(self.provider, 'publish_material', side_effect=AssertionError('no write')), \
                patch.object(self.provider, 'check_material', side_effect=AssertionError('cached parent')):
            logical.dispatch(self.mutation(ack, 'RESUME', 'resume')['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')
        self.assertEqual(self.provider.calls.count('publication'), 1)
        self.assertEqual(self.provider.calls.count('consumer'), 1)

    def test_post_ack_malformed_wrong_descriptor_or_exception_cannot_be_rejected(self):
        for index, mode in enumerate(('partial', 'wrong', 'raises')):
            ack, _ = self.logical_create('read_'+str(index), 'read-'+str(index))
            def check(descriptor, material):
                if mode == 'raises':
                    raise logical_provider.Rejected('READBACK_FAILED_AFTER_WRITE')
                value = self.provider.observation('publication', descriptor)
                if mode == 'partial':
                    del value['complete']
                else:
                    value['foundation_revision'] = True
                return value
            with self.subTest(mode=mode), patch.object(self.provider, 'check_material', side_effect=check):
                logical.dispatch(ack['request_id'])
            self.assertEqual(self.detail(ack)['progress_code'], 'UNKNOWN')
            parent = next(r for r in self.rows('logical_effects')
                          if r['creation_id']==ack['creation_id'] and r['effect_key']=='publication')
            self.assertEqual(parent['state'], 'UNKNOWN')
            self.assertFalse(any(r['database_id']==self.detail(ack)['database_id'] and r['state']=='ACTIVE'
                                 for r in self.rows('logical_backend_identities')))

    def test_write_cas_rejection_allows_fresh_publication_attempt(self):
        ack, _ = self.logical_create()
        with patch.object(self.provider, 'publish_material', side_effect=logical_provider.StaleCAS()):
            logical.dispatch(ack['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'BLOCKED')
        logical.dispatch(self.mutation(ack, 'RESUME', 'resume')['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')
        effects = [r for r in self.rows('logical_effects') if r['effect_key'].startswith('publication')]
        self.assertEqual(effects[0]['state'], 'REJECTED')
        self.assertTrue(any('/attempt/' in r['effect_key'] and r['state']=='ACKNOWLEDGED' for r in effects))
        self.assertEqual(self.provider.calls.count('consumer'), 1)

    def test_latest_successful_publication_attempt_reused_after_attach_rejection(self):
        ack, _ = self.logical_create()
        with patch.object(self.provider, 'publish_material', side_effect=logical_provider.StaleCAS()):
            logical.dispatch(ack['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'BLOCKED')
        with patch.object(self.provider, 'attach_catalog', side_effect=logical_provider.Rejected('NO_EFFECTS')):
            logical.dispatch(self.mutation(ack, 'RESUME', 'resume-one')['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'BLOCKED')
        before = self.rows('logical_effects')
        parent = next(r for r in before if r['effect_key'].startswith('publication/attempt/'))
        self.assertEqual(parent['state'], 'ACKNOWLEDGED')
        hashes = self.provider.hash_calls
        with patch.object(logical, '_render_material', side_effect=AssertionError('no new snapshot')), \
                patch.object(self.provider, 'publication_hashes', side_effect=AssertionError('no hash read')), \
                patch.object(self.provider, 'publish_material', side_effect=AssertionError('no write')), \
                patch.object(self.provider, 'check_material', side_effect=AssertionError('cached parent')):
            logical.dispatch(self.mutation(ack, 'RESUME', 'resume-two')['request_id'])
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')
        after = {r['effect_key']: r for r in self.rows('logical_effects')}
        for effect in before:
            self.assertEqual(after[effect['effect_key']], effect)
        self.assertEqual(self.provider.hash_calls, hashes)
        self.assertEqual(self.provider.calls.count('publication'), 1)
        self.assertEqual(self.provider.calls.count('observe-publication'), 1)
        self.assertEqual(self.provider.calls.count('consumer'), 1)

    def test_refresh_all_replica_mismatch_never_activates_candidate(self):
        ack, dbid = self.ready()
        active = {r['id'] for r in self.rows('logical_backend_identities') if r['state']=='ACTIVE'}
        refresh = self.refresh(dbid, 'data_engineer')
        with patch.object(self.provider, 'check_material', return_value=dict(verified=False, complete=False)):
            logical.dispatch(refresh)
        request = next(r for r in self.rows('logical_requests') if r['id']==refresh)
        self.assertEqual(request['dispatch_state'], 'UNKNOWN')
        self.assertIsNone(request['result_json'])
        identities = self.rows('logical_backend_identities')
        self.assertFalse(any(r['state']=='ACTIVE' and r['id'] not in active for r in identities))
        self.assertTrue(any(r['state']=='CANDIDATE' and r['id'] not in active for r in identities))
        publication = next(r for r in self.rows('logical_effects')
                           if r['effect_key'].startswith('refresh/') and r['effect_key'].endswith('/publication'))
        self.assertEqual(publication['state'], 'UNKNOWN')
        self.assertEqual(self.detail(ack)['progress_code'], 'READY')
