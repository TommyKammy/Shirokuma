"""Normal HTTP parsing and logical coordinator dispatch, with no listening API."""
from contextlib import contextmanager, ExitStack
import sqlite3
from unittest.mock import patch
from types import SimpleNamespace
from tests import test_database_creation_routes as legacy_routes
from tests.test_logical_creations import LogicalFixture
import database_creations as legacy
import logical_creations as logical
import control_store
import runtime_profile


class LogicalRouteTests(LogicalFixture):
    request = legacy_routes.CreationRouteTests.request

    def setUp(self):
        super().setUp()
        for p in [patch.object(logical, 'enabled', return_value=True),
                  patch.object(logical, 'dispatch_async')]:
            result = p.start()
            self.addCleanup(p.stop)
            if p.attribute == 'dispatch_async':
                self.dispatch = result

    def test_normal_create_replay_get_location_no_store(self):
        status, ack, headers = self.request(body=dict(sql_catalog='research'))
        self.assertEqual(status, 202)
        self.assertEqual(headers['Location'], legacy.COLLECTION+'/'+ack['creation_id'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(self.request(body=dict(sql_catalog='research'))[:2], (200, ack))
        self.dispatch.assert_called_once_with(ack['request_id'])
        self.dispatch.reset_mock()
        status, detail, headers = self.request('/'+ack['creation_id'])
        self.assertEqual(status, 200)
        self.assertEqual(detail['creation_mode'], 'logical_v1')
        self.assertEqual(detail['progress_code'], 'CREATING')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.dispatch.assert_not_called()

    def test_strict_headers_origin_key_environment(self):
        for headers, key, expected in [
                ([('Origin','https://elsewhere.example')], 'key', 403),
                ([('Idempotency-Key','duplicate')], 'key', 400),
                ([('X-Shirokuma-Role','admin')], 'key', 400),
                ([('X-Shirokuma-Environment','other')], 'key', 409),
                ([('X-Shirokuma-Environment','a'),('X-Shirokuma-Environment','b')], 'key', 400),
                ([], None, 400)]:
            with self.subTest(headers=headers):
                status, _, response = self.request(body=dict(sql_catalog='research'), headers=headers, key=key)
                self.assertEqual(status, expected)
                self.assertEqual(response['Cache-Control'], 'no-store')
        self.dispatch.assert_not_called()

    def test_old_phase_routes_rejected_and_withdraw_permanent(self):
        _, ack, _ = self.request(body=dict(sql_catalog='research'))
        for action in ('plans','phases/bootstrap/approve','phases/full/recover-verify','replan'):
            status, result, _ = self.request('/'+ack['creation_id']+'/'+action,
                body=dict(expected_revision=1), key=action)
            self.assertEqual((status, result['error']), (409, 'CREATION_MODE_MISMATCH'))
        status, _, _ = self.request('/'+ack['creation_id']+'/withdraw', body=dict(expected_revision=1), key='withdraw')
        self.assertEqual(status, 202)
        status, result, _ = self.request('/'+ack['creation_id']+'/resume', body=dict(expected_revision=2), key='resume')
        self.assertEqual(status, 409)
        self.assertEqual(self.detail(ack)['progress_code'], 'STOPPED')


    @contextmanager
    def metadata_only_caps(self):
        connect = control_store.connect
        active, writes, forbidden = [], [], []
        def authorize(action, *args):
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                writes.append((action, args))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        @contextmanager
        def read_only():
            with connect() as db:
                db.set_authorizer(authorize)
                active.append(db)
                try:
                    yield db
                finally:
                    active.remove(db)
        compute = self.provider.compute_state
        def metadata():
            self.assertEqual(active, [], 'close SQLite before compute metadata')
            return compute()
        with ExitStack() as stack:
            stack.enter_context(patch.object(control_store, 'connect', side_effect=read_only))
            observed = stack.enter_context(patch.object(self.provider, 'compute_state', side_effect=metadata))
            for name in dir(type(self.provider)):
                if not name.startswith('_') and name not in ('compute_state', 'bound_alive') and callable(getattr(self.provider, name)):
                    forbidden.append(stack.enter_context(patch.object(self.provider, name, side_effect=AssertionError('caps forbids '+name))))
            yield observed
            for method in forbidden:
                method.assert_not_called()
        self.assertEqual(writes, [], 'caps must never attempt SQLite mutations')

    def test_capabilities_repeatedly_metadata_only_and_no_gate_mutation(self):
        with control_store.connect() as db:
            before = [tuple(r) for r in db.execute('SELECT * FROM db_creation_principal_gates')]
        with self.metadata_only_caps() as compute:
            for _ in range(3):
                status, result, headers = self.request('/capabilities')
                self.assertEqual(status, 200)
                self.assertTrue(result['available'])
                self.assertTrue(result['can_create'])
                self.assertEqual(result['warehouse_state'], 'ACTIVE')
                self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(compute.call_count, 3)
        self.assertEqual(self.provider.compute_calls, 3)
        self.assertEqual(self.provider.alive_calls, 3)
        with control_store.connect() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM db_creation_principal_gates')], before)
        self.dispatch.assert_not_called()

    def test_capabilities_compute_states_pf_death_and_safe_http(self):
        for state, blocker in [('ACTIVE', None), ('STOPPED', 'WAREHOUSE_START_REQUIRED'),
                               ('TRANSITIONING', 'FOUNDATION_NOT_READY')]:
            for bound in (True, False, 1, None):
                with self.subTest(state=state, bound=bound):
                    self.provider.warehouse_state = state
                    with self.metadata_only_caps() as compute, \
                            patch.object(self.provider, 'bound_alive', return_value=bound) as alive:
                        status, result, _ = self.request('/capabilities')
                    compute.assert_called_once_with()
                    self.assertEqual(alive.call_count, int(state == 'ACTIVE'))
                    expected = 'FOUNDATION_NOT_READY' if state == 'ACTIVE' and bound is not True else blocker
                    self.assertEqual((status, result['warehouse_state'], result['blocker']), (200, state, expected))
                    self.assertEqual(result['available'], expected is None)
                    self.assertEqual(result['can_create'], expected is None)
                    self.assertEqual(result['can_resume'], expected is None)
        class StringSubclass(str):
            pass
        for invalid in ('private-secret', 'active', '', None, True, 1, {}, [], StringSubclass('ACTIVE')):
            self.provider.warehouse_state = invalid
            with self.subTest(invalid=invalid), self.metadata_only_caps() as compute:
                status, result, _ = self.request('/capabilities')
            compute.assert_called_once_with()
            self.assertEqual((status, result['blocker']), (200, 'FOUNDATION_NOT_READY'))
            self.assertNotIn('warehouse_state', result)
            self.assertNotIn('private-secret', str(result))
        from logical_provider import Rejected
        for error in (RuntimeError('private-secret'), Rejected('WAREHOUSE_START_REQUIRED')):
            with self.metadata_only_caps() as compute, \
                    patch.object(self.provider, 'compute_state', side_effect=error) as failing, \
                    patch.object(self.provider, 'bound_alive') as alive:
                status, result, _ = self.request('/capabilities')
            failing.assert_called_once_with()
            alive.assert_not_called()
            self.assertEqual((status, result['blocker']), (200, 'FOUNDATION_NOT_READY'))
            self.assertNotIn('warehouse_state', result)
            self.assertNotIn('private-secret', str(result))

    def test_capabilities_current_local_blockers_and_new_admin_without_gate(self):
        with logical.transaction() as db:
            logical._gate(db, 'alice')
            logical._gate(db, 'executor')
        self.accounts['reader'].update(role='admin', platform_admin=True)
        self.save_accounts()
        # A newly authorized administrator has no gate until a mutation.
        reader = type(self.alice)('reader', 'admin', True)
        self.assertTrue(logical.capabilities(reader, 'admin')['available'])
        with control_store.connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM db_creation_principal_gates WHERE principal_id='reader'").fetchone())
        for actor in ('alice', 'executor'):
            with logical.transaction() as db:
                db.execute("UPDATE db_creation_principal_gates SET state='STOPPED' WHERE principal_id=?", (actor,))
            with self.metadata_only_caps() as compute:
                result = logical.capabilities(self.alice, 'admin')
            compute.assert_not_called()
            self.assertNotIn('warehouse_state', result)
            self.assertEqual(result['blocker'], 'PRINCIPAL_STOPPED')
            with logical.transaction() as db:
                db.execute("UPDATE db_creation_principal_gates SET state='ACTIVE' WHERE principal_id=?", (actor,))
        for value in (False, 1, None):
            with patch.object(self.provider, 'bound_alive', return_value=value):
                self.assertFalse(logical.capabilities(self.alice, 'admin')['available'])
        with patch.object(runtime_profile, 'current', return_value=SimpleNamespace(mode='database')), \
                patch.object(runtime_profile, 'generation_attestation', side_effect=ValueError('changed bytes')):
            with self.metadata_only_caps() as compute:
                self.assertEqual(logical.capabilities(self.alice, 'admin')['blocker'], 'FOUNDATION_NOT_READY')
            compute.assert_not_called()
        with logical.transaction() as db:
            db.execute("UPDATE warehouse_config_generations SET state='FAILED'")
        with self.metadata_only_caps() as compute:
            self.assertEqual(logical.capabilities(self.alice, 'admin')['blocker'], 'FOUNDATION_NOT_READY')
        compute.assert_not_called()
        with logical.transaction() as db:
            db.execute("UPDATE warehouse_config_generations SET state='APPLIED'")
            db.execute("UPDATE warehouse_controls SET state='DRAINING'")
        with self.metadata_only_caps() as compute:
            self.assertEqual(logical.capabilities(self.alice, 'admin')['blocker'], 'WAREHOUSE_START_REQUIRED')
        compute.assert_not_called()

    def test_caps_current_authority_failure_prevents_compute(self):
        for actor in ('alice', 'executor'):
            self.accounts[actor]['enabled'] = False
            self.save_accounts()
            with self.metadata_only_caps() as compute:
                if actor == 'alice':
                    status, _, _ = self.request('/capabilities')
                    self.assertEqual(status, 403)
                else:
                    self.assertEqual(logical.capabilities(self.alice, 'admin')['blocker'], 'PRINCIPAL_STOPPED')
            compute.assert_not_called()
            self.accounts[actor]['enabled'] = True
            self.save_accounts()
