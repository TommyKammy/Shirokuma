"""Private refresh enqueue, transaction and dispatch boundary tests."""
import json
import sqlite3
import unittest
from unittest.mock import patch
import test_logical_access as access
import control_store as store
import logical_registry as lr
import grant_management as grants
import request_auth as auth
import logical_creations


class LogicalRefreshTests(unittest.TestCase):
    setUp = access.LogicalAccessTests.setUp
    save_accounts = access.LogicalAccessTests.save_accounts
    secret = access.LogicalAccessTests.secret

    def change(self, *, connection=None, key='change'):
        return grants._mutate(self.operator, 'admin', 'db',
            dict(principal_id='alice', maximum_role='revoked', expected_revision=1), key,
            connection=connection)

    def test_refresh_is_durable_before_postcommit_dispatch_and_replay_never_dispatches(self):
        observed = []
        def dispatch(request_id):
            with store.connect() as db:
                request = dict(db.execute('SELECT * FROM logical_requests WHERE id=?', (request_id,)).fetchone())
                self.assertEqual(request['dispatch_state'], 'ACCEPTED')
                self.assertEqual(request['action'], 'AUTH_REFRESH')
                self.assertEqual(request['actor'], 'operator')  # not original CREATE actor
                pins = json.loads(request['pins_json'])
                self.assertEqual(pins['database_id'], 'db')
                self.assertEqual(pins['target_principal_id'], 'alice')
                self.assertEqual(pins['desired_revision'], 2)
                self.assertEqual(pins['effective_epoch'], 2)
                self.assertEqual(grants.effective(db, 'alice', 'db')['maximum_role'], 'revoked')
                observed.append(request_id)
        with patch.object(logical_creations, 'dispatch_async', side_effect=dispatch) as callback:
            result = self.change()
            callback.assert_called_once_with(result['auth_refresh_request_id'])
            self.assertEqual(observed, [result['auth_refresh_request_id']])
            self.assertEqual(self.change(), result)
            callback.assert_called_once()
        with store.connect() as db:
            row = db.execute('SELECT * FROM logical_requests').fetchone()
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE logical_requests SET ack='edited' WHERE id=?", (row['id'],))

    def test_caller_transaction_enqueues_without_dispatch_and_rolls_back_together(self):
        with patch.object(logical_creations, 'dispatch_async') as callback:
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                result = self.change(connection=db)
                self.assertTrue(result['auth_refresh_request_id'])
                self.assertEqual(db.execute('SELECT count(*) FROM logical_requests').fetchone()[0], 1)
                with store.connect() as other:
                    self.assertEqual(other.execute('SELECT count(*) FROM logical_requests').fetchone()[0], 0)
                db.rollback()
            callback.assert_not_called()
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM logical_requests').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT revision FROM grants WHERE principal_id='alice'").fetchone()[0], 1)

    def test_dispatch_failure_retains_request_and_no_engine_success(self):
        with patch.object(logical_creations, 'dispatch_async', side_effect=RuntimeError('offline')):
            result = self.change()
        with store.connect() as db:
            request = db.execute('SELECT * FROM logical_requests WHERE id=?', (result['auth_refresh_request_id'],)).fetchone()
            self.assertEqual(request['dispatch_state'], 'ACCEPTED')
            self.assertEqual(grants.effective(db, 'alice', 'db')['applied_revision'], 1)

    def test_private_helper_idempotency_target_validation_and_account_refresh(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = self.change(connection=db)
            operation_id = changed['id']
            request_id = lr.request_auth_refresh(db, 'operator', 'alice', operation_id, dict(database_id='db'))
            self.assertEqual(request_id, changed['auth_refresh_request_id'])
            for target, operation, pins in (
                ('bob', operation_id, dict(database_id='db')),
                ('alice', 'op-db', dict(database_id='db')),
                ('alice', operation_id, dict(database_id='missing')),
                ('alice', operation_id, dict(database_id='db', effective_epoch=99)),
                ('alice', None, dict(database_id='db'))):
                with self.subTest(target=target, operation=operation, pins=pins), self.assertRaises(ValueError):
                    lr.request_auth_refresh(db, 'operator', target, operation, pins)
            pins = dict(database_id='db', reason='ACCOUNT_AUTHORITY_CHANGED', idempotency_key='private-edit-1')
            account_id = lr.request_auth_refresh(db, 'app', 'bob', None, pins)
            self.assertEqual(lr.request_auth_refresh(db, 'app', 'bob', None, pins), account_id)
            self.accounts['app']['disabled'] = True
            self.save_accounts()
            with self.assertRaises(PermissionError):
                lr.request_auth_refresh(db, 'app', 'bob', None, pins)
        with store.connect() as db:
            with self.assertRaisesRegex(ValueError, 'transaction'):
                lr.request_auth_refresh(db, 'operator', 'alice', operation_id, dict(database_id='db'))

    def test_initial_candidate_does_not_enqueue_and_legacy_promotion_denied(self):
        with store.connect() as db:
            db.execute("UPDATE db_creations SET logical_state='VERIFYING'")
        with patch.object(logical_creations, 'dispatch_async') as callback:
            result = self.change()
            self.assertNotIn('auth_refresh_request_id', result)
            callback.assert_not_called()
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM logical_requests').fetchone()[0], 0)
            with self.assertRaises(grants.OperationError) as error:
                grants.promote(db, dict(grant_revisions=[dict(database_id='db')]),
                    'foundation', 'unused-apply', 'operator', 'synthetic-proof-reference')
            self.assertEqual(error.exception.code, 'CREATION_MODE_MISMATCH')

    def test_new_current_admin_seeds_only_absent_gate_in_mutation_transaction(self):
        self.accounts['new-admin'] = dict(self.accounts['operator'])
        self.save_accounts()
        self.operator = auth.AuthenticatedPrincipal('new-admin', 'admin', True)
        with patch.object(logical_creations, 'dispatch_async') as dispatch:
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                result = self.change(connection=db)
                gate = dict(db.execute("SELECT * FROM db_creation_principal_gates WHERE principal_id='new-admin'").fetchone())
                self.assertEqual((gate['state'], gate['revision'], gate['request_id']), ('ACTIVE', 1, None))
                request = dict(db.execute('SELECT * FROM logical_requests WHERE id=?',
                                         (result['auth_refresh_request_id'],)).fetchone())
                self.assertEqual((request['actor'], request['requester_gate_revision'],
                                  request['executor_id'], request['executor_gate_revision']), ('new-admin', 1, 'app', 1))
                self.assertEqual(request['dispatch_state'], 'ACCEPTED')
                self.assertEqual(json.loads(request['pins_json'])['operation_id'], result['id'])
                self.assertEqual(grants.effective(db, 'alice', 'db')['applied_revision'], 1)
                db.rollback()
            dispatch.assert_not_called()
        with store.connect() as db:
            self.assertIsNone(db.execute("SELECT * FROM db_creation_principal_gates WHERE principal_id='new-admin'").fetchone())
            self.assertEqual(db.execute('SELECT count(*) FROM logical_requests').fetchone()[0], 0)

    def test_existing_stopped_requester_never_resurrects(self):
        with store.connect() as db:
            db.execute("UPDATE db_creation_principal_gates SET state='STOPPED',revision=2,reason_code='OWNER_STOP' WHERE principal_id='operator'")
            before = dict(db.execute("SELECT * FROM db_creation_principal_gates WHERE principal_id='operator'").fetchone())
        with patch.object(logical_creations, 'dispatch_async') as dispatch:
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                with self.assertRaisesRegex(PermissionError, 'requester gate'):
                    self.change(connection=db)
                self.assertEqual(dict(db.execute("SELECT * FROM db_creation_principal_gates WHERE principal_id='operator'").fetchone()), before)
                self.assertEqual(db.execute('SELECT count(*) FROM logical_requests').fetchone()[0], 0)
                db.rollback()
            dispatch.assert_not_called()

    def test_missing_or_stopped_executor_cannot_be_seeded_by_requester(self):
        for state in (None, 'STOPPED'):
            with self.subTest(state=state), store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if state is None:
                    db.execute("DELETE FROM db_creation_principal_gates WHERE principal_id='app'")
                else:
                    db.execute("UPDATE db_creation_principal_gates SET state='STOPPED',revision=2 WHERE principal_id='app'")
                with self.assertRaises(PermissionError):
                    lr.request_auth_refresh(db, 'app', 'bob', None,
                        dict(database_id='db', reason='ACCOUNT_AUTHORITY_CHANGED', idempotency_key='executor-missing'))
                gate = db.execute("SELECT * FROM db_creation_principal_gates WHERE principal_id='app'").fetchone()
                if state is None:
                    self.assertIsNone(gate)
                else:
                    self.assertEqual((gate['state'], gate['revision']), ('STOPPED', 2))
                self.assertEqual(db.execute('SELECT count(*) FROM logical_requests').fetchone()[0], 0)
                db.rollback()

    def test_retirement_revocation_lowers_authority_without_new_dispatch(self):
        with store.connect() as db:
            db.execute("UPDATE databases SET state='retiring'")
        with patch.object(logical_creations, 'dispatch_async') as callback:
            self.change()
            callback.assert_not_called()
        with store.connect() as db:
            self.assertEqual(grants.effective(db, 'alice', 'db')['maximum_role'], 'revoked')
            self.assertEqual(db.execute('SELECT count(*) FROM logical_requests').fetchone()[0], 0)
