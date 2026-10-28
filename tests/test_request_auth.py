"""P2-2 identity and registry authorization contract (no live backend)."""
import base64
from dataclasses import FrozenInstanceError
from email.message import Message
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import request_auth as auth
import control_store
import data_management as dm
from grant_fixtures import verified_grants


class PrincipalTests(unittest.TestCase):
    def test_private_current_factor_classifies_unknown_lifecycle_without_rescuing_old_epoch(self):
        import warehouse_admission as admission
        from query_context import QueryError, resolve_context, revalidate_admission
        principal = self.principal()
        generation = 'g-' + self.sales
        with control_store.connect() as db:
            schema = dm.create_schema(db, self.sales, 'raw', 'Raw', 'raw', 'test')
        frozen = resolve_context(principal, self.sales, schema.id, 'data_engineer',
            warehouse_id='w', expected_binding_revision=1)
        with control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            handle = admission.admit(db, warehouse_id='w', actor='alice', kind='query',
                subject_id='original-query', database_id=self.sales, generation_id=generation)
            dm.record_query(db, dm.QueryRecord('original-query', 'alice',
                dm.QueryContext(self.sales, schema.id, 'w', generation), 'UNKNOWN', 'now'))
            admission.finish(db, handle, state='UNKNOWN')
            db.execute("UPDATE databases SET state='retiring' WHERE id=?", (self.sales,))
        with admission.accepted(handle, allow_unknown=True), control_store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            query_before = tuple(db.execute("SELECT * FROM query_records WHERE id='original-query'").fetchone())
            admission_before = tuple(db.execute('SELECT * FROM warehouse_admissions WHERE id=?', (handle.id,)).fetchone())
            with patch.object(control_store, 'connect', side_effect=AssertionError('must use caller connection')):
                decision = auth._authorize_database_current(principal, self.sales, 'data_engineer',
                    expected_generation_id=generation, db=db)
            self.assertIsInstance(decision, auth.DatabaseAuthorization)
            self.assertEqual((decision.principal_id, decision.database_id, decision.sql_catalog,
                              decision.selected_role, decision.platform_admin, decision.generation_id),
                             ('alice', self.sales, 'sales', 'data_engineer', False, generation))
            self.assertEqual(decision.grant_revision, frozen.grant_revision)
            self.assertFalse(admission.continuing_database(db, 'alice', self.sales, generation))
            with self.assertRaises(PermissionError):
                auth.authorize_database(principal, self.sales, 'data_engineer', expected_generation_id=generation, db=db)
            with self.assertRaises(admission.AdmissionConflict):
                admission.admit(db, warehouse_id='w', actor='alice', kind='query', subject_id='new-child')
            db.execute("UPDATE effective_grants SET epoch=epoch+1 WHERE principal_id='alice' AND database_id=?", (self.sales,))
            changed = auth._authorize_database_current(principal, self.sales, 'data_engineer',
                expected_generation_id=generation, db=db)
            self.assertEqual(changed.grant_revision, frozen.grant_revision + 1)
            self.assertNotEqual(changed.grant_revision, frozen.grant_revision)
            with self.assertRaises(QueryError):
                revalidate_admission(db, principal, frozen)
            db.execute("UPDATE databases SET state='retired' WHERE id=?", (self.sales,))
            with self.assertRaises(PermissionError):
                auth.authorize_database(principal, self.sales, 'data_engineer', expected_generation_id=generation, db=db)
            self.assertEqual(tuple(db.execute("SELECT * FROM query_records WHERE id='original-query'").fetchone()), query_before)
            self.assertEqual(tuple(db.execute('SELECT * FROM warehouse_admissions WHERE id=?', (handle.id,)).fetchone()), admission_before)
        with self.assertRaises(TypeError):
            auth._authorize_database_current(principal, self.sales, 'data_engineer', expected_generation_id=generation)

    def test_private_current_factor_preserves_exact_principal_and_current_account_authority(self):
        principal = self.principal()
        owner = self.principal('owner')
        class Derived(auth.AuthenticatedPrincipal):
            pass
        with control_store.connect() as db:
            db.execute("UPDATE databases SET state='retiring' WHERE id=?", (self.sales,))
            for invalid in (('alice', 'admin'), Derived('alice', 'admin', True),
                            auth.AuthenticatedPrincipal('alice', 'bi_viewer', False)):
                with self.assertRaises(PermissionError):
                    auth._authorize_database_current(invalid, self.sales, 'data_engineer',
                        expected_generation_id='g-' + self.sales, db=db)
            for field, value in (('role', 'bi_viewer'), ('enabled', False), ('disabled', True)):
                original = dict(self.users['alice'])
                self.users['alice'][field] = value
                self.save()
                with self.assertRaises(PermissionError):
                    auth._authorize_database_current(principal, self.sales, 'data_engineer',
                        expected_generation_id='g-' + self.sales, db=db)
                self.users['alice'] = original
            self.save()
            decision = auth._authorize_database_current(owner, self.sales, 'admin',
                expected_generation_id='g-' + self.sales, db=db)
            self.assertTrue(decision.platform_admin)
            self.assertIsNone(decision.grant_revision)
            self.users['owner']['platform_admin'] = False
            self.save()
            with self.assertRaises(PermissionError):
                auth._authorize_database_current(owner, self.sales, 'admin', expected_generation_id='g-' + self.sales, db=db)
            del self.users['alice']
            self.save()
            with self.assertRaises(PermissionError):
                auth._authorize_database_current(principal, self.sales, 'data_engineer',
                    expected_generation_id='g-' + self.sales, db=db)

    def test_private_current_factor_preserves_grants_binding_and_generation_checks(self):
        principal = self.principal()
        generation = 'g-' + self.sales
        with control_store.connect() as db:
            db.execute("UPDATE databases SET state='retiring' WHERE id=?", (self.sales,))
            for statement in ("UPDATE grants SET maximum_role='revoked' WHERE database_id=?",
                              "DELETE FROM effective_grants WHERE database_id=?",
                              "UPDATE effective_grants SET maximum_role='bi_viewer' WHERE database_id=?",
                              'UPDATE catalog_bindings SET applied_revision=0 WHERE database_id=?',
                              'UPDATE catalog_bindings SET desired_revision=2 WHERE database_id=?',
                              'UPDATE catalog_bindings SET applied_generation_id=NULL WHERE database_id=?',
                              'DELETE FROM catalog_bindings WHERE database_id=?'):
                with self.subTest(statement=statement):
                    db.execute('SAVEPOINT invalid_authority')
                    db.execute(statement, (self.sales,))
                    with self.assertRaises(PermissionError):
                        auth._authorize_database_current(principal, self.sales, 'data_engineer',
                            expected_generation_id=generation, db=db)
                    db.execute('ROLLBACK TO invalid_authority')
                    db.execute('RELEASE invalid_authority')
            with patch.object(dm, 'get_generation', return_value=None), self.assertRaises(PermissionError):
                auth._authorize_database_current(principal, self.sales, 'data_engineer', expected_generation_id=generation, db=db)
            for database_id, expected_generation in ((self.sales, 'wrong'), ('missing', generation), (self.finance, generation)):
                with self.assertRaises(PermissionError):
                    auth._authorize_database_current(principal, database_id, 'data_engineer',
                        expected_generation_id=expected_generation, db=db)

    def test_nonplatform_admin_requires_current_global_and_exact_effective_db_ceiling(self):
        self.users['alice']['role'] = 'admin'
        self.save()
        principal = self.principal()
        with self.assertRaises(PermissionError):
            self.authorize(principal, role='admin')
        with control_store.connect() as db:
            dm.update_grant(db, 'alice', self.sales, 1, 'admin')
        with self.assertRaises(PermissionError):
            self.authorize(principal, role='admin')
        # Synthetic verified effective authority: production promote must wait
        # for genuine scoped-admin backend verification (tested separately).
        with control_store.connect() as db:
            db.execute("UPDATE effective_grants SET maximum_role='admin',epoch=epoch+1 WHERE principal_id='alice'")
        decision = self.authorize(principal, role='admin')
        self.assertFalse(decision.platform_admin)
        self.assertIsNotNone(decision.grant_revision)
        for role in ('bi_viewer', 'data_engineer'):
            self.assertEqual(self.authorize(principal, role=role).grant_revision, decision.grant_revision)
        with self.assertRaises(PermissionError):
            self.authorize(principal, self.finance, 'admin')
        self.users['alice']['role'] = 'data_engineer'
        self.save()
        with self.assertRaises(PermissionError):
            self.authorize(principal, role='admin')
        self.users['alice']['role'] = 'admin'
        self.save()
        for revision, ceiling in ((2, 'data_engineer'), (3, 'bi_viewer'), (4, 'revoked')):
            with control_store.connect() as db:
                dm.update_grant(db, 'alice', self.sales, revision, ceiling)
            with self.assertRaises(PermissionError):
                self.authorize(principal, role='admin')
            if ceiling != 'revoked':
                self.authorize(principal, role=ceiling)
        with control_store.connect() as db:
            dm.update_grant(db, 'alice', self.sales, 5, 'admin')
        for role in auth.ROLES:
            with self.assertRaises(PermissionError):
                self.authorize(principal, role=role)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.accounts = self.root / 'accounts.json'
        self.users = {'alice': dict(role='data_engineer', platform_admin=False,
            salt='ab' * 16, password_hash=auth.password_hash('correct', 'ab' * 16)),
            'owner': dict(role='admin', platform_admin=True, salt='cd' * 16,
                password_hash=auth.password_hash('correct', 'cd' * 16))}
        self.save()
        environment = patch.dict(os.environ, {'SHIROKUMA_AUTH_MODE': 'shared',
            'SHIROKUMA_AUTH_FILE': str(self.accounts), 'SHIROKUMA_STATE_DIR': str(self.root / 'state')}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        with control_store.connect() as db:
            self.sales = dm.create_database(db, 'sales', 'Sales', '', 'test').id
            self.finance = dm.create_database(db, 'finance', 'Finance', '', 'test').id
            for database_id in (self.sales, self.finance):
                dm.create_binding(db, database_id, 'service', database_id, 'catalog', 'storage', 'credentials', 's3://bucket')
                dm.record_generation(db, dm.WarehouseConfigGeneration('g-' + database_id, 'w', database_id, (), (), (), 'READY'))
                dm.update_binding(db, database_id, 1, 1, 'g-' + database_id)
                db.execute("UPDATE databases SET state='READY' WHERE id=?", (database_id,))
            dm.create_grant(db, 'alice', self.sales, 'data_engineer')
            verified_grants(db)

    def save(self):
        self.accounts.write_text(json.dumps(self.users))

    def headers(self, user='alice', password='correct'):
        headers = Message()
        headers['Authorization'] = 'Basic ' + base64.b64encode((user + ':' + password).encode()).decode()
        return headers

    def principal(self, user='alice'):
        return auth.authenticate_principal(self.headers(user))

    def authorize(self, principal=None, database_id=None, role='bi_viewer'):
        database_id = database_id or self.sales
        return auth.authorize_database(principal or self.principal(), database_id, role,
            expected_generation_id='g-' + database_id)

    def test_real_basic_and_v1_compatibility(self):
        principal = self.principal()
        self.assertEqual((principal.principal_id, principal.maximum_role, principal.platform_admin), ('alice', 'data_engineer', False))
        self.assertEqual(auth.authenticate(self.headers()), ('alice', 'data_engineer'))
        with self.assertRaises(FrozenInstanceError):
            principal.maximum_role = 'admin'
        for headers in (Message(), self.headers(password='wrong')):
            with self.assertRaises(auth.AuthenticationError):
                auth.authenticate_principal(headers)
        self.assertEqual(auth.select_role('admin', ['editor', 'viewer']), 'bi_viewer')
        with auth.effective_role('bi_viewer'):
            self.assertEqual(auth.current_role(), 'bi_viewer')

    def test_explicit_bool_and_shared_configuration_required(self):
        del self.users['alice']['platform_admin']
        self.save()
        with self.assertRaises(auth.AuthenticationError):
            self.principal()
        for value in (None, 1, 'false'):
            self.users['alice']['platform_admin'] = value
            self.save()
            with self.assertRaises(auth.AuthenticationError):
                self.principal()
        self.users['alice']['platform_admin'] = True
        self.save()
        with self.assertRaises(auth.AuthenticationError):
            self.principal()
        with patch.dict(os.environ, {'SHIROKUMA_AUTH_MODE': 'local', 'SHIROKUMA_AUTH_FILE': ''}):
            self.assertIsNone(auth.authenticate(Message()))
            with self.assertRaises(auth.AuthenticationError):
                self.principal()
        self.users['alice']['platform_admin'] = False
        self.save()
        with patch.dict(os.environ, SHIROKUMA_AUTH_MODE='local'):
            self.assertEqual(auth.authenticate(self.headers()), ('alice', 'data_engineer'))
            with self.assertRaises(auth.AuthenticationError):
                self.principal()

    def test_grants_caps_admin_and_immutable_metadata(self):
        decision = self.authorize(role='data_engineer')
        self.assertEqual(decision.grant_revision, 2)
        self.assertEqual(decision.sql_catalog, 'sales')
        self.assertNotIn('password', vars(decision))
        with self.assertRaises(PermissionError):
            self.authorize(decision)
        with self.assertRaises(FrozenInstanceError):
            decision.selected_role = 'admin'
        for role in ('admin', '', None, 'viewer'):
            with self.assertRaises(PermissionError):
                self.authorize(role=role)
        with self.assertRaises(PermissionError):
            self.authorize(database_id=self.finance)
        owner = self.principal('owner')
        self.assertIsNone(self.authorize(owner, role='admin').grant_revision)
        self.assertTrue(self.authorize(owner, role='admin').platform_admin)
        with self.assertRaises(PermissionError):
            self.authorize(owner)
        with control_store.connect() as db:
            dm.create_grant(db, 'owner', self.sales, 'bi_viewer')
            dm.create_grant(db, 'alice', self.finance, 'bi_viewer')
            verified_grants(db)
        self.assertEqual(self.authorize(owner).selected_role, 'bi_viewer')
        self.assertFalse(self.authorize(owner).platform_admin)
        with control_store.connect() as db:
            dm.update_grant(db, 'owner', self.sales, 1, 'data_engineer')
            verified_grants(db)
        self.assertFalse(self.authorize(owner, role='data_engineer').platform_admin)
        self.assertTrue(owner.platform_admin)
        self.assertEqual(self.authorize(database_id=self.finance).sql_catalog, 'finance')
        with self.assertRaises(PermissionError):
            self.authorize(database_id=self.finance, role='data_engineer')

    def test_current_account_and_revocation_are_rechecked(self):
        principal = self.principal()
        self.authorize(principal, role='data_engineer')
        self.users['alice']['role'] = 'bi_viewer'
        self.save()
        with self.assertRaises(PermissionError):
            self.authorize(principal, role='data_engineer')
        self.authorize(principal)
        with control_store.connect() as db:
            dm.update_grant(db, 'alice', self.sales, 1, 'revoked')
        with self.assertRaises(PermissionError):
            self.authorize(principal)
        with control_store.connect() as db:
            dm.update_grant(db, 'alice', self.sales, 2, 'bi_viewer')
        with self.assertRaises(PermissionError):
            self.authorize(principal)
        with control_store.connect() as db:
            verified_grants(db)
        self.assertGreater(self.authorize(principal).grant_revision, 3)
        del self.users['alice']
        self.save()
        with self.assertRaises(PermissionError):
            self.authorize(principal)

    def test_finance_only_and_both_database_principals(self):
        self.users['financier'] = dict(self.users['alice'])
        self.save()
        with control_store.connect() as db:
            dm.create_grant(db, 'financier', self.finance, 'data_engineer')
            verified_grants(db)
        financier = self.principal('financier')
        self.authorize(financier, self.finance, 'data_engineer')
        with self.assertRaises(PermissionError):
            self.authorize(financier, self.sales)
        with control_store.connect() as db:
            dm.create_grant(db, 'financier', self.sales, 'bi_viewer')
            verified_grants(db)
        self.authorize(financier, self.sales)
        self.authorize(financier, self.finance)

    def test_platform_privilege_downgrade_and_missing_real_generation(self):
        owner = self.principal('owner')
        self.authorize(owner, role='admin')
        self.users['owner']['platform_admin'] = False
        self.save()
        with self.assertRaises(PermissionError):
            self.authorize(owner, role='admin')
        with patch.object(dm, 'get_generation', return_value=None):
            with self.assertRaises(PermissionError):
                self.authorize()
        with patch.dict(os.environ, SHIROKUMA_AUTH_FILE=''):
            with self.assertRaises(auth.AuthenticationError):
                self.principal()

    def test_registry_fail_closed_without_display_revision_coupling(self):
        with control_store.connect() as db:
            dm.update_database_display(db, self.sales, 1, 'Renamed', 'display only')
        self.assertEqual(self.authorize().database_revision, 2)
        with self.assertRaises(PermissionError):
            auth.authorize_database(('alice', 'admin'), self.sales, 'admin', expected_generation_id='g-' + self.sales)
        for statement, parameters in (
            ("UPDATE databases SET state='registered' WHERE id=?", (self.sales,)),
            ("UPDATE catalog_bindings SET applied_revision=0 WHERE database_id=?", (self.sales,)),
            ("UPDATE catalog_bindings SET applied_generation_id=NULL WHERE database_id=?", (self.sales,)),
            ('DELETE FROM catalog_bindings WHERE database_id=?', (self.sales,))):
            with control_store.connect() as db:
                db.execute(statement, parameters)
            with self.assertRaises(PermissionError):
                self.authorize()
            with control_store.connect() as db:
                db.execute("UPDATE databases SET state='READY' WHERE id=?", (self.sales,))
                db.execute('UPDATE catalog_bindings SET applied_revision=1, applied_generation_id=? WHERE database_id=?', ('g-' + self.sales, self.sales))
        for database_id, generation in (('unknown', 'g'), (self.finance, 'wrong'), ('', ''), (None, None)):
            with self.assertRaises(PermissionError):
                auth.authorize_database(self.principal(), database_id, 'bi_viewer', expected_generation_id=generation)
