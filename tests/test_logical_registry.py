"""Temporary SQLite authority tests. No resident DB, network or credentials."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store as store
import logical_registry as lr
import runtime_profile


class LogicalRegistryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        store._bootstrap(self.db)
        self.addCleanup(self.db.close)
        current = patch.object(runtime_profile, '_current', None)
        current.start(); self.addCleanup(current.stop)
        self.db.execute('BEGIN IMMEDIATE')

    def base(self, database_id='db', mode='logical_v1', reservation=1):
        db = self.db
        db.execute("INSERT INTO databases VALUES (?,?,'Database','','test','READY',1)", (database_id, database_id))
        db.execute("INSERT INTO operations VALUES (?, 'actor','create','[]',1,'APPLIED','[]',NULL,'{}','{}')", ('op-' + database_id,))
        db.execute('''INSERT INTO db_creations
            (id,environment_id,warehouse_id,actor,enrollment_id,enrollment_revision,database_id,
             create_operation_id,intent_json,child_ids_json,revision,reservation_active,
             created_at,updated_at,creation_mode,logical_state)
             VALUES (?,'env','LOCAL','actor','enrollment',1,?,?,'{}','{}',1,?,'now','now',?,'READY')''',
             ('root-' + database_id, database_id, 'op-' + database_id, reservation, mode))
        return database_id

    def generation(self, generation='foundation', state='APPLIED', warehouse='LOCAL'):
        self.db.execute('INSERT INTO warehouse_config_generations VALUES (?,?,?, ?,?,?,?)',
                        (generation, warehouse, 'real-apply', '{}', '{}', '{}', state))

    def foundation(self):
        self.generation()
        return lr.put_foundation(self.db, dict(environment_id='env', warehouse_id='LOCAL',
            generation_id='foundation', revision=1, status='READY', config_ref='config-proof', verification_ref='verified'))

    def binding(self, database_id='db'):
        self.db.execute('''INSERT INTO catalog_bindings VALUES
            (?,'polaris','realm',?,'storage','credential','s3://bucket/prefix',1,1,'foundation',1)''',
            (database_id, database_id))
        return lr.put_binding(self.db, dict(database_id=database_id, environment_id='env', warehouse_id='LOCAL',
            foundation_revision=1, desired_policy_revision=1, applied_policy_revision=1,
            catalog_credential_ref='catalog-' + database_id, verification_ref='verified', revision=1))

    def setup_ready(self):
        self.base()
        self.foundation()
        return self.binding()

    def grant(self, principal='alice', role='data_engineer'):
        self.db.execute('INSERT INTO grants VALUES (?,\'db\',?,1)', (principal, role))
        self.db.execute('''UPDATE effective_grants SET maximum_role=?,applied_revision=1,
            applied_generation_id='foundation' WHERE principal_id=?''', (role, principal))

    def candidate(self, principal='alice', identity_id=None, **extra):
        identity_id = identity_id or principal + '-id'
        return lr.put_identity(self.db, dict(id=identity_id, principal_id=principal, database_id='db',
            selected_role='bi_viewer', grant_epoch=1, policy_revision=1, backend_username=identity_id + '-user',
            credential_ref=identity_id + '-ref', state='CANDIDATE', verification_ref=None, revision=1, **extra))

    def activate(self, identity):
        return lr.put_identity(self.db, dict(identity, state='ACTIVE', verification_ref='verified', revision=2))

    def test_foundation_requires_real_applied_generation_and_cas(self):
        self.generation('not-applied', 'PLANNED')
        values = dict(environment_id='env', warehouse_id='LOCAL', generation_id='missing',
                      revision=1, status='READY', config_ref='config', verification_ref='verified')
        for change in ({}, {'generation_id': 'not-applied'}, {'verification_ref': ''}):
            with self.assertRaises(ValueError):
                lr.put_foundation(self.db, dict(values, **change))
        self.generation('wrong-warehouse', warehouse='OTHER')
        with self.assertRaises(ValueError):
            lr.put_foundation(self.db, dict(values, generation_id='wrong-warehouse'))
        foundation = self.foundation()
        self.assertEqual(lr.get_foundation(self.db, 'env', 'LOCAL'), foundation)
        with self.assertRaises(ValueError):
            lr.put_foundation(self.db, dict(foundation, revision=2), expected_revision=7)
        self.assertEqual(lr.put_foundation(self.db, dict(foundation, revision=2), expected_revision=1)['revision'], 2)
        self.db.commit()
        with self.assertRaisesRegex(ValueError, 'transaction'):
            lr.put_foundation(self.db, dict(foundation, revision=3), expected_revision=2)

    def test_mode_no_candidate_fallback_and_binding_pins(self):
        self.base()
        self.assertEqual(lr.creation_mode(self.db, 'db'), 'logical_v1')
        with self.assertRaises(ValueError):
            lr.resolve_binding(self.db, 'db')
        self.foundation()
        binding = self.binding()
        result = lr.resolve_binding(self.db, 'db', 'foundation')
        self.assertEqual(result['sql_catalog'], 'db')
        self.assertEqual(result['binding_revision'], 1)
        for change in ({'foundation_revision': 7}, {'revision': 7}):
            with self.assertRaises(ValueError):
                lr.put_binding(self.db, dict(binding, **change), expected_revision=1)
        with self.assertRaises(ValueError):
            lr.resolve_binding(self.db, 'db', 'wrong')
        for sql in ("UPDATE databases SET state='RECONCILING'", "UPDATE db_creations SET logical_state='STOPPED'",
                    'UPDATE catalog_bindings SET applied_revision=0',
                    'UPDATE logical_bindings SET applied_policy_revision=0',
                    "UPDATE logical_foundations SET status='MAINTENANCE'",
                    "UPDATE warehouse_config_generations SET state='PLANNED'",
                    'UPDATE logical_foundations SET revision=2'):
            self.db.execute('SAVEPOINT changed')
            self.db.execute(sql)
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                lr.resolve_binding(self.db, 'db')
            self.db.execute('ROLLBACK TO changed'); self.db.execute('RELEASE changed')

    def test_per_principal_identity_and_unrelated_pending_policy(self):
        binding = self.setup_ready()
        for principal in ('alice', 'bob'):
            self.grant(principal)
            self.activate(self.candidate(principal))
        alice = lr.resolve_identity(self.db, 'alice', 'db', 'bi_viewer', 1)
        bob = lr.resolve_identity(self.db, 'bob', 'db', 'bi_viewer', 1)
        self.assertNotEqual(alice['backend_username'], bob['backend_username'])
        lr.put_binding(self.db, dict(binding, desired_policy_revision=2, revision=2), expected_revision=1)
        self.db.execute("UPDATE grants SET revision=2 WHERE principal_id='bob'")
        self.assertEqual(lr.resolve_identity(self.db, 'alice', 'db', 'bi_viewer', 1), alice)
        self.assertEqual(self.db.execute('SELECT desired_revision,revision FROM catalog_bindings').fetchone()[:], (1, 1))
        for principal, epoch, role in (('bob', 1, 'bi_viewer'), ('alice', 2, 'bi_viewer'),
                                      ('alice', 1, 'admin'), ('unknown', 1, 'bi_viewer')):
            with self.assertRaises(ValueError):
                lr.resolve_identity(self.db, principal, 'db', role, epoch)
        self.db.execute("DELETE FROM grants WHERE principal_id='alice'")
        with self.assertRaises(ValueError):
            lr.resolve_identity(self.db, 'alice', 'db', 'bi_viewer', 1)
        self.db.execute("INSERT INTO grants VALUES ('alice','db','data_engineer',3)")
        with self.assertRaises(ValueError):
            lr.resolve_identity(self.db, 'alice', 'db', 'bi_viewer', 1)

    def test_identity_immutable_uniqueness_activation_and_terminal_revocation(self):
        self.setup_ready(); self.grant()
        candidate = self.candidate()
        self.assertEqual(lr.put_identity(self.db, candidate), candidate)
        with self.assertRaises(ValueError):
            lr.put_identity(self.db, dict(candidate, state='ACTIVE', revision=2))
        for field, changed in (('grant_epoch', 2), ('credential_ref', 'other'), ('principal_id', 'bob'),
                               ('policy_revision', 2), ('backend_username', 'other'), ('selected_role', 'admin')):
            with self.subTest(field=field), self.assertRaises(ValueError):
                lr.put_identity(self.db, dict(candidate, **{field: changed, 'revision': 2}))
            with self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(f'UPDATE logical_backend_identities SET {field}=? WHERE id=?', (changed, candidate['id']))
        active = self.activate(candidate)
        duplicate = self.candidate(identity_id='fresh')
        with self.assertRaises(sqlite3.IntegrityError):
            self.activate(duplicate)
        revoked = lr.revoke_identity(self.db, active['id'])
        self.assertEqual(revoked['credential_ref'], active['credential_ref'])
        with self.assertRaises(ValueError):
            lr.put_identity(self.db, dict(revoked, state='ACTIVE', revision=4, verification_ref='proof'))
        self.assertEqual(lr.revoke_identity(self.db, active['id']), revoked)
        with self.assertRaises(ValueError):
            lr.resolve_identity(self.db, 'alice', 'db', 'bi_viewer', 1)

    def test_platform_admin_per_principal_gate_and_never_grant_flag_escalation(self):
        self.setup_ready()
        self.db.execute("INSERT INTO db_creation_principal_gates VALUES ('env','admin-a','ACTIVE',1,NULL,NULL,'now')")
        value = dict(id='admin-a-id', principal_id='admin-a', database_id='db', selected_role='admin',
                     grant_epoch=None, policy_revision=1, backend_username='admin-a-sql', credential_ref='admin-a-ref',
                     state='CANDIDATE', verification_ref=None, revision=1,
                     authority_source='platform_admin', account_gate_revision=1)
        active = self.activate(lr.put_identity(self.db, value))
        self.assertEqual(lr.resolve_identity(self.db, 'admin-a', 'db', 'admin', None, True), active)
        for principal, epoch, flag in (('admin-a', None, False), ('admin-a', 1, True), ('admin-b', None, True)):
            with self.assertRaises(ValueError):
                lr.resolve_identity(self.db, principal, 'db', 'admin', epoch, flag)
        self.db.execute("UPDATE db_creation_principal_gates SET revision=2,state='STOPPED'")
        with self.assertRaises(ValueError):
            lr.resolve_identity(self.db, 'admin-a', 'db', 'admin', None, True)
        self.assertEqual(lr.revoke_identity(self.db, active['id'])['state'], 'REVOKED')

    def request(self, table, request_id, key='key'):
        columns = 'id,environment_id,actor,route,idempotency_key,payload,creation_id,action,ack,dispatch_state,created_at,updated_at'
        if table == 'logical_requests':
            columns += ',pins_json'
        self.db.execute(f'INSERT INTO {table} ({columns}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?'
                        + (",'{}')" if table == 'logical_requests' else ')'),
                        (request_id, 'env', 'alice', 'create', key, '{}', 'root-db', 'CREATE', '{}', 'ACCEPTED', 'now', 'now'))

    def test_crossmode_scope_and_immutable_effect_intent_ack(self):
        self.base()
        self.request('logical_requests', 'logical')
        with self.assertRaises(sqlite3.IntegrityError):
            self.request('db_creation_requests', 'legacy')
        self.request('db_creation_requests', 'legacy', 'other-key')
        with self.assertRaises(sqlite3.IntegrityError):
            self.request('logical_requests', 'logical-other', 'other-key')
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE logical_requests SET ack='changed'")
        self.db.execute("INSERT INTO logical_effects VALUES ('root-db','catalog','{}','INTENT',NULL,NULL,'now','now')")
        self.db.execute("UPDATE logical_effects SET state='UNKNOWN',outcome_json='{}'")
        for field in ('creation_id', 'effect_key', 'intent_json'):
            with self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(f'UPDATE logical_effects SET {field}={field}')
        self.assertEqual(self.db.execute('SELECT count(*) FROM operation_steps').fetchone()[0], 0)

    def test_v20_migration_failure_is_atomic(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        with patch.object(store, '_migrate_logical'), patch.object(store, '_migrate_observed_effects'):
            store._bootstrap(db)
        db.execute('PRAGMA user_version=19')
        before = db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name').fetchall()
        with patch.object(store, '_LOGICAL_SCHEMA', store._LOGICAL_SCHEMA + 'CREATE TABLE broken('):
            with self.assertRaises(sqlite3.OperationalError):
                store._bootstrap(db)
        db.rollback()
        self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 19)
        self.assertEqual(db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name').fetchall(), before)
        store._bootstrap(db)
        self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)

    def test_unpublished_binding_identity_and_active_profile_foundation_denied(self):
        binding = self.setup_ready(); self.grant()
        lr.put_binding(self.db, dict(binding, desired_policy_revision=2, revision=2), expected_revision=1)
        candidate = self.candidate()
        self.db.execute('UPDATE logical_bindings SET applied_policy_revision=0')
        with self.assertRaises(ValueError):
            self.activate(candidate)
        self.db.execute('UPDATE logical_bindings SET applied_policy_revision=1')
        self.db.execute('UPDATE logical_backend_identities SET verification_ref=NULL')
        with patch.object(runtime_profile, '_current', object()), patch.object(runtime_profile, 'logical_config',
                return_value=dict(environment_id='env', warehouse_id='LOCAL',
                                  foundation_generation_id='different', foundation_revision=1)):
            with self.assertRaises(ValueError):
                lr.resolve_binding(self.db, 'db')
        self.db.execute("UPDATE logical_backend_identities SET state='ACTIVE',verification_ref='proof'")
        self.db.execute("UPDATE logical_backend_identities SET state='REVOKED'")
        pending = dict(candidate, id='alice-policy2', policy_revision=2,
                       backend_username='alice-policy2-user', credential_ref='alice-policy2-ref')
        pending = lr.put_identity(self.db, pending)
        with self.assertRaises(ValueError):
            self.activate(pending)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE logical_backend_identities SET state='ACTIVE' WHERE id='alice-id'")

    def test_migration19_retains_history_defaults_and_partial_reservation(self):
        old = sqlite3.connect(':memory:')
        self.addCleanup(old.close)
        old.row_factory = sqlite3.Row
        old.execute('PRAGMA foreign_keys=ON')
        with patch.object(store, '_migrate_logical'), patch.object(store, '_migrate_observed_effects'):
            store._bootstrap(old)
        old.execute('PRAGMA user_version=19')
        current = self.db; self.db = old
        try:
            # Populate the genuine v19 column shape before the additive upgrade.
            old.execute("INSERT INTO databases VALUES ('kept','kept','Kept','','test','READY',1)")
            old.execute("INSERT INTO operations VALUES ('op','actor','create','[]',1,'UNKNOWN','[]',NULL,'{}','{}')")
            old.execute("INSERT INTO db_creations VALUES ('root','env','LOCAL','actor','enroll',1,'kept','op','{}','{}',1,1,NULL,'now','now')")
            old.commit()
            before = {table: [tuple(row) for row in old.execute('SELECT * FROM ' + table)]
                      for table in ('databases', 'operations', 'db_creations')}
            store._bootstrap(old)
            self.assertEqual(old.execute('PRAGMA user_version').fetchone()[0], store.VERSION)
            for table in ('databases', 'operations'):
                self.assertEqual([tuple(row) for row in old.execute('SELECT * FROM ' + table)], before[table])
            migrated = old.execute('SELECT * FROM db_creations').fetchone()
            self.assertEqual(tuple(migrated)[:-2], before['db_creations'][0])
            self.assertEqual(tuple(migrated)[-2:], ('legacy', None))
            self.base('logical-a'); self.base('logical-b')
            with self.assertRaises(sqlite3.IntegrityError):
                self.base('legacy-second', 'legacy')
            self.assertEqual(old.execute('PRAGMA foreign_key_check').fetchall(), [])
        finally:
            self.db = current

    def v20_fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        db = sqlite3.connect(root / 'jobs.sqlite3')
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        # Execute the original production v20 migration without the v21 rebuild.
        with patch.object(store, '_migrate_observed_effects'):
            store._bootstrap(db)
        db.execute('PRAGMA user_version=20')
        current = self.db
        self.db = db
        try:
            self.base('first')
            self.base('second')
        finally:
            self.db = current
        rows = [
            (11 + 17 * index, 'root-first', 'effect-' + str(index), '{ "intent": %d }' % index, state,
             None if index == 0 else 'fixture-ref-' + str(index),
             None if index == 0 else '{ "outcome": %d }' % index,
             '2026-10-01T01:02:0%dZ' % index, '2026-10-02T03:04:0%dZ' % index)
            for index, state in enumerate(('INTENT', 'UNKNOWN', 'ACKNOWLEDGED', 'REJECTED'))]
        rows.append((103, 'root-second', 'effect-0', '{}', 'INTENT', None, None, 'then', 'later'))
        db.executemany('''INSERT INTO logical_effects
            (rowid,creation_id,effect_key,intent_json,state,private_ref,outcome_json,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)''', rows)
        db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE logical_effects SET state='OBSERVED_APPLIED'")
        db.rollback()
        return root, db

    def effect_rows(self, db):
        return [tuple(row) for row in db.execute(
            'SELECT rowid,* FROM logical_effects ORDER BY creation_id,effect_key')]

    def effect_objects(self, db):
        return [tuple(row) for row in db.execute("""SELECT type,name,sql FROM sqlite_master
            WHERE tbl_name='logical_effects' AND type IN ('index','trigger') ORDER BY name""")]

    def assert_effect_constraints(self, db):
        self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        for field in ('creation_id', 'effect_key', 'intent_json', 'created_at'):
            with self.subTest(field=field), self.assertRaisesRegex(sqlite3.IntegrityError, 'immutable'):
                db.execute(f'UPDATE logical_effects SET {field}={field}')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'retained'):
            db.execute('DELETE FROM logical_effects')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'CHECK'):
            db.execute("UPDATE logical_effects SET state='INVALID'")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'UNIQUE'):
            db.execute('INSERT INTO logical_effects SELECT * FROM logical_effects LIMIT 1')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY'):
            db.execute("INSERT INTO logical_effects VALUES ('missing','key','{}','INTENT',NULL,NULL,'now','now')")
        self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_fresh_bootstrap_supports_distinct_observed_effect(self):
        self.base()
        self.db.execute("INSERT INTO logical_effects VALUES ('root-db','catalog','{}','OBSERVED_APPLIED',NULL,'{}','then','now')")
        self.assertEqual(self.db.execute('SELECT state FROM logical_effects').fetchone()[0], 'OBSERVED_APPLIED')
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0], 21)
        self.assert_effect_constraints(self.db)

    def test_populated_v20_upgrade_retains_effects_and_constraints(self):
        _, db = self.v20_fixture()
        before = self.effect_rows(db)
        # Explicit indexes, as well as the composite-PK autoindex, survive rebuild.
        db.execute('CREATE INDEX fixture_effect_state ON logical_effects(state)')
        db.commit()
        objects = self.effect_objects(db)
        columns = [tuple(row) for row in db.execute('PRAGMA table_info(logical_effects)')]
        foreign_keys = [tuple(row) for row in db.execute('PRAGMA foreign_key_list(logical_effects)')]
        parents = [tuple(row) for row in db.execute('SELECT * FROM db_creations ORDER BY id')]
        store._bootstrap(db)
        self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 21)
        self.assertEqual(self.effect_rows(db), before)
        self.assertEqual(self.effect_objects(db), objects)
        self.assertEqual([tuple(row) for row in db.execute('PRAGMA table_info(logical_effects)')], columns)
        self.assertEqual([tuple(row) for row in db.execute('PRAGMA foreign_key_list(logical_effects)')], foreign_keys)
        self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM db_creations ORDER BY id')], parents)
        self.assert_effect_constraints(db)
        db.execute("UPDATE logical_effects SET state='OBSERVED_APPLIED',outcome_json='observed proof' WHERE state='UNKNOWN'")
        states = dict(db.execute("SELECT effect_key,state FROM logical_effects WHERE creation_id='root-first'"))
        self.assertEqual(states['effect-1'], 'OBSERVED_APPLIED')
        self.assertEqual(states['effect-2'], 'ACKNOWLEDGED')

    def test_v21_reopen_is_idempotent(self):
        root, db = self.v20_fixture()
        store._bootstrap(db)
        db.execute("UPDATE logical_effects SET state='OBSERVED_APPLIED' WHERE state='UNKNOWN'")
        db.commit()
        before = self.effect_rows(db)
        schema = [tuple(row) for row in db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')]
        with patch.object(store, 'root', return_value=root), patch.object(
                store, '_migrate_observed_effects', side_effect=AssertionError('must not migrate again')):
            for _ in range(2):
                with store.connect() as reopened:
                    self.assertEqual(reopened.execute('PRAGMA user_version').fetchone()[0], 21)
                    self.assertEqual(self.effect_rows(reopened), before)
                    self.assertEqual([tuple(row) for row in reopened.execute(
                        'SELECT type,name,sql FROM sqlite_master ORDER BY name')], schema)
                    self.assert_effect_constraints(reopened)

    def test_v21_migration_failure_rolls_back_and_retry_succeeds(self):
        root, db = self.v20_fixture()
        before = self.effect_rows(db)
        schema = [tuple(row) for row in db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')]
        migrate = store._migrate_observed_effects

        def fail_after_rebuild(connection):
            migrate(connection)
            self.assertTrue(connection.in_transaction)
            self.assertIn('OBSERVED_APPLIED', connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='logical_effects'").fetchone()[0])
            connection.execute('CREATE TABLE broken(')

        with patch.object(store, 'root', return_value=root):
            with patch.object(store, '_migrate_observed_effects', side_effect=fail_after_rebuild):
                with self.assertRaises(sqlite3.OperationalError):
                    with store.connect():
                        self.fail('failed migration must not yield a connection')
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 20)
            self.assertEqual(self.effect_rows(db), before)
            self.assertEqual([tuple(row) for row in db.execute(
                'SELECT type,name,sql FROM sqlite_master ORDER BY name')], schema)
            self.assert_effect_constraints(db)
            db.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE logical_effects SET state='OBSERVED_APPLIED'")
            db.rollback()
            with store.connect() as restored:
                self.assertEqual(restored.execute('PRAGMA user_version').fetchone()[0], 21)
                self.assertEqual(self.effect_rows(restored), before)
                self.assert_effect_constraints(restored)
                restored.execute("UPDATE logical_effects SET state='OBSERVED_APPLIED' WHERE state='UNKNOWN'")


if __name__ == '__main__':
    unittest.main()
