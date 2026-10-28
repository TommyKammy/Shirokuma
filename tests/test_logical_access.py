"""Logical production auth/query paths against private temp files and ledger."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store as store
import logical_registry as lr
import request_auth as auth
import runtime_profile as rp
import trino_auth
import query_context as qc
import query_service as qs
import warehouse_admission as wa
import test_logical_registry as registry_tests
from test_logical_runtime import logical_profile


class LogicalAccessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        env = patch.dict(os.environ, {}, clear=True)
        env.start(); self.addCleanup(env.stop)
        for name in ('_current', '_startup_fingerprint', '_startup_binding', '_startup_identity'):
            mock = patch.object(rp, name, None)
            mock.start(); self.addCleanup(mock.stop)
        location, self.profile, self.config = logical_profile(self.root)
        self.accounts = {name: dict(role='admin', platform_admin=name in ('operator', 'app'),
            salt='12'*16, password_hash='34'*32) for name in ('alice', 'bob', 'operator', 'app')}
        self.save_accounts()
        os.environ['SHIROKUMA_RUNTIME_PROFILE'] = str(location)
        rp.configure_process()
        fixture = registry_tests.LogicalRegistryTests()
        fixture.setUp()
        try:
            fixture.setup_ready()
            fixture.db.execute("UPDATE warehouse_config_generations SET database_revisions='[]',grant_revisions='[]',configuration_refs='[]'")
            for principal in ('alice', 'bob'):
                fixture.grant(principal)
                identity = fixture.activate(fixture.candidate(principal))
                self.secret(identity['credential_ref'], dict(username=identity['backend_username'], password=principal+'-password'))
            fixture.db.execute("INSERT INTO schemas VALUES ('marts','db','marts','Marts','marts','test',1)")
            for name in ('operator', 'app'):
                fixture.db.execute("INSERT INTO db_creation_principal_gates VALUES ('env',?,'ACTIVE',1,NULL,NULL,'now')", (name,))
            fixture.db.commit()
            with sqlite3.connect(self.root / 'jobs.sqlite3') as target:
                fixture.db.backup(target)
        finally:
            fixture.doCleanups()
        self.alice = auth.AuthenticatedPrincipal('alice', 'admin', False)
        self.bob = auth.AuthenticatedPrincipal('bob', 'admin', False)
        self.operator = auth.AuthenticatedPrincipal('operator', 'admin', True)
        tls = patch.object(trino_auth, 'backend_ssl_context', return_value=object())
        tls.start(); self.addCleanup(tls.stop)

    def save_accounts(self):
        Path(self.profile['auth_file']).write_text(json.dumps(self.accounts))

    def secret(self, ref, value):
        path = Path(self.config['credentials_dir']) / (ref + '.json')
        path.write_text(json.dumps(dict(version=1, **value)))
        path.chmod(0o600)

    def backend(self, principal=None, role='bi_viewer'):
        return trino_auth.prepare_backend_v2({'X-Presto-User': 'spoof', 'X-Trino-Role':'admin'},
            principal=principal or self.alice, database_id='db', selected_role=role)

    def context(self, principal=None):
        return qc.resolve_context(principal or self.alice, 'db', 'marts', 'bi_viewer', expected_binding_revision=1)

    def test_distinct_credentials_and_targeted_revocation_preserve_other_principal(self):
        with patch.object(trino_auth, 'load_database_credentials', side_effect=AssertionError('legacy manifest')):
            alice = self.backend()[1]
            bob = self.backend(self.bob)[1]
            context = self.context(self.bob)
            self.assertNotEqual(alice['Authorization'], bob['Authorization'])
            self.assertEqual(set(alice), {'Authorization', 'X-Trino-User'})
            with store.connect() as db:
                db.execute("UPDATE grants SET maximum_role='revoked',revision=2 WHERE principal_id='alice'")
            with self.assertRaises(PermissionError):
                self.backend()
            self.assertEqual(self.backend(self.bob)[1], bob)
            self.assertEqual(self.context(self.bob), context)
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                qc.revalidate_admission(db, self.bob, context)

    def test_fresh_actual_account_edits_deny_without_dispatch_or_foundation_freeze(self):
        self.backend()
        self.accounts['alice']['disabled'] = True
        self.save_accounts()
        rp.generation_attestation()  # mutable authority is not fixed foundation bytes
        with self.assertRaises(PermissionError):
            self.backend()
        self.backend(self.bob)
        Path(self.profile['auth_file']).chmod(0o644)
        with self.assertRaises(PermissionError):
            self.backend(self.bob)

    def test_admin_sql_uses_per_principal_identity_and_current_gate(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            candidate = lr.put_identity(db, dict(id='operator-id', principal_id='operator', database_id='db',
                selected_role='admin', grant_epoch=None, policy_revision=1, backend_username='operator-sql',
                credential_ref='operator-ref', state='CANDIDATE', verification_ref=None, revision=1,
                authority_source='platform_admin', account_gate_revision=1))
            lr.put_identity(db, dict(candidate, state='ACTIVE', verification_ref='verified', revision=2))
        self.secret('operator-ref', dict(username='operator-sql', password='operator-password'))
        with patch.object(lr, 'platform_admin_credentials', side_effect=AssertionError('internal credential')):
            self.assertEqual(self.backend(self.operator, 'admin')[1]['X-Trino-User'], 'operator-sql')
            with store.connect() as db:
                db.execute("UPDATE db_creation_principal_gates SET revision=2 WHERE principal_id='operator'")
            with self.assertRaises(PermissionError):
                self.backend(self.operator, 'admin')

    def test_candidate_missing_never_falls_back_and_admission_rechecks(self):
        context = self.context()
        with store.connect() as db:
            db.execute('DELETE FROM logical_bindings')
        with patch.object(trino_auth, 'load_database_credentials', side_effect=AssertionError('fallback')):
            with self.assertRaises(PermissionError):
                self.backend()
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(qc.QueryError):
                qc.revalidate_admission(db, self.alice, context)

    def test_receipt_cannot_rebind_username_even_at_same_context_epoch(self):
        with store.connect() as db:
            db.execute("INSERT INTO query_records VALUES ('query','alice','db','marts','LOCAL','foundation','FINISHED','now')")
            db.execute("INSERT INTO query_execution_contexts VALUES ('query','bi_viewer','read-only','db','marts',1,1,'old-user','pin',NULL,NULL,NULL,'SELECT 1')")
        with self.assertRaises(qc.QueryError) as error:
            qs._owned(self.alice, 'query', 'bi_viewer')
        self.assertEqual(error.exception.code, 'DB_ACCESS_REVOKED')
        with store.connect() as db:
            db.execute("UPDATE query_execution_contexts SET backend_username='alice-id-user'")
        self.assertEqual(qs._owned(self.alice, 'query', 'bi_viewer')['id'], 'query')

    def test_only_exact_existing_active_admission_can_continue_retirement(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            handle = wa.admit(db, actor='alice', kind='dataops', subject_id='accepted',
                             generation_id='foundation', database_id='db')
            db.execute("UPDATE databases SET state='retiring'")
        with self.assertRaises(PermissionError):
            self.backend()
        with wa.accepted(handle):
            self.backend()
            with store.connect() as db:
                db.execute("UPDATE db_creations SET logical_state='STOPPED'")
            with self.assertRaises(PermissionError):
                self.backend()

    def test_inventory_uses_catalog_consumer_without_legacy_manifest(self):
        import database_inventory as inventory
        import data_management as dm
        self.secret('catalog-db', dict(client_id='db-consumer', client_secret='consumer-secret'))
        with store.connect() as db:
            database = dm.get_database(db, 'db')
            binding = dm.get_binding(db, 'db')
        with patch.object(rp, 'validate_catalog_manifest', side_effect=AssertionError('legacy catalog manifest')):
            entry = inventory._credentials(database, binding)
            self.assertEqual(entry['client_id'], 'db-consumer')
            self.assertEqual((entry['service_ref'], entry['realm'], entry['catalog']), ('polaris', 'realm', 'db'))

    def test_retirement_only_denial_preserves_owned_cleanup_classification(self):
        context = self.context()
        with store.connect() as db:
            db.execute("UPDATE databases SET state='retiring'")
        self.assertTrue(qs._retirement_only_denial(self.alice, context))
        self.assertTrue(qs._retirement_only_denial(self.alice, context, backend_username='alice-id-user'))
        self.assertFalse(qs._retirement_only_denial(self.alice, context, backend_username='old-user'))
        self.accounts['alice']['disabled'] = True
        self.save_accounts()
        self.assertFalse(qs._retirement_only_denial(self.alice, context))

    def test_shared_logical_validation_requires_no_legacy_manifest(self):
        with patch.object(trino_auth, 'load_database_credentials', side_effect=AssertionError('legacy manifest')):
            auth.validate_shared_backend()
        self.assertTrue(trino_auth.configured_database_mode())

    def test_immediate_admission_compares_captured_username_and_decision(self):
        context = self.context()
        decision, headers = self.backend()
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            qc.revalidate_admission(db, self.alice, context, backend_username=headers['X-Trino-User'])
            with self.assertRaises(qc.QueryError):
                qc.revalidate_admission(db, self.alice, context, backend_username='old-user')
        from dataclasses import replace
        with self.assertRaises(qc.QueryError):
            qc._resolve_context(self.alice, 'db', 'marts', 'bi_viewer', warehouse_id='LOCAL',
                expected_binding_revision=1, decision=replace(decision, grant_revision=99))

    def test_retirement_denies_admission_for_another_foundation_warehouse(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            handle = wa.admit(db, warehouse_id='OTHER', actor='alice', kind='dataops', subject_id='other',
                             generation_id='foundation', database_id='db')
            db.execute("UPDATE databases SET state='retiring'")
        with wa.accepted(handle):
            with self.assertRaises(PermissionError):
                self.backend()
