"""Shared ledger bootstrap contracts; all state belongs to temporary directories."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools/local_experiment'
sys.path.insert(0, str(TOOLS))
import control_store as store


def drop_dbt_schema(db):
    db.execute('DROP TABLE IF EXISTS ai_explanations')
    for table in ('dbt_run_operations', 'dbt_requests', 'dbt_runs'):
        db.execute('DROP TABLE ' + table)


def drop_creation_schema(db):
    """Make downgrade fixtures genuine pre-P5 stores, including implicit indexes."""
    drop_dbt_schema(db)
    # Remove the additive v20 layer when constructing genuine older fixtures.
    for table in ('logical_backend_identities', 'logical_bindings', 'logical_foundations',
                  'logical_effects', 'logical_requests'):
        db.execute('DROP TABLE IF EXISTS ' + table)
    db.execute('DROP TRIGGER IF EXISTS db_creation_requests_scope')
    for table in ('db_creation_principal_gates', 'db_creation_requests',
                  'db_creation_phase_plans', 'db_creations'):
        db.execute('DROP TABLE ' + table)


def drop_grant_schema(db):
    # Downgrade fixtures must also remove schema introduced after their version.
    drop_metadata_schema(db)
    for trigger in ('grant_insert', 'grant_update', 'grant_delete'):
        db.execute('DROP TRIGGER '+trigger)
    for table in ('grant_audit', 'grant_requests', 'effective_grants'):
        db.execute('DROP TABLE '+table)


def drop_metadata_schema(db):
    drop_recovery_schema(db)
    for table in ('db_job_requests', 'db_job_operations', 'db_job_steps', 'db_job_runs', 'db_jobs'):
        db.execute('DROP TABLE '+table)
    for table in ('metadata_audit', 'metadata_requests'):
        db.execute('DROP TABLE '+table)


def drop_recovery_schema(db):
    drop_creation_schema(db)
    for table in ('db_stage_scan_operations', 'db_stage_scan_items', 'db_stage_scans', 'db_stage_content'):
        db.execute('DROP TABLE IF EXISTS ' + table)
    for table in ('db_stage_publications', 'db_stage_requests', 'db_stage_runs', 'db_stage_claims', 'db_stages'):
        db.execute('DROP TABLE IF EXISTS ' + table)
    db.execute('DROP TABLE execution_recovery_requests')
    columns = {r[1] for r in db.execute('PRAGMA table_info(warehouse_admissions)')}
    if 'execution_closure' in columns:
        db.execute('ALTER TABLE warehouse_admissions DROP COLUMN execution_closure')


class ControlStoreTests(unittest.TestCase):
    def v23_admission_history(self, version=23):
        import data_management as dm
        with store.connect() as db:
            database = dm.create_database(db, 'closure_history', 'History', '', 'test')
            db.execute("INSERT INTO warehouse_controls VALUES ('LOCAL','OPEN',NULL,1,NULL)")
            for rowid, name, parent, closure in ((7, 'parent', None, ''),
                    (19, 'child', 'parent', 'FENCED_READ_ONLY')):
                db.execute('''INSERT INTO warehouse_admissions
                    (rowid,id,warehouse_id,actor,kind,subject_id,parent_id,state,
                     created_at,finished_at,evidence_ref,execution_closure,database_id)
                    VALUES (?,?,'LOCAL','actor','query',?,?,'UNKNOWN',
                            'created','finished','private-evidence',?,?)''',
                    (rowid, name, name, parent, closure, database.id))
            db.execute("INSERT INTO dbt_runs VALUES ('run',?,'{\"retained\":true}','child')", (database.id,))
            db.execute("INSERT INTO operations VALUES ('recovery','actor','recover','[]',1,'UNKNOWN','[]',NULL,'{}','{}')")
            db.execute("""INSERT INTO execution_recovery_requests VALUES
                ('recovery','actor','recover','key','{}','{}',1,'CLOSED','{}',1)""")
            db.execute('CREATE INDEX admission_history ON warehouse_admissions(database_id) WHERE state=\'UNKNOWN\'')
            db.execute('''CREATE TRIGGER admission_history_guard BEFORE UPDATE OF actor ON warehouse_admissions
                BEGIN SELECT RAISE(ABORT,'retained admission actor'); END''')
        # Construct the actual old constraint without changing historical migrations.
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=OFF')
            db.execute('BEGIN')
            schema = db.execute("SELECT sql FROM sqlite_master WHERE name='warehouse_admissions'").fetchone()[0]
            objects = db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='warehouse_admissions' AND sql IS NOT NULL AND type!='table'").fetchall()
            schema = schema.replace(
                "CHECK(execution_closure IN ('','FENCED_READ_ONLY','FENCED_STAGE_CLEANUP'))",
                "CHECK(execution_closure IN ('','FENCED_READ_ONLY'))")
            db.execute('CREATE TABLE old_admissions (' + schema.split('(', 1)[1])
            db.execute('INSERT INTO old_admissions SELECT * FROM warehouse_admissions')
            for rowid, name in ((7, 'parent'), (19, 'child')):
                db.execute('UPDATE old_admissions SET rowid=? WHERE id=?', (rowid, name))
            db.execute('DROP TABLE warehouse_admissions')
            db.execute('ALTER TABLE old_admissions RENAME TO warehouse_admissions')
            for row in objects:
                db.execute(row[0])
            if version == 22:
                db.execute('DROP TABLE ai_explanations')
            db.execute(f'PRAGMA user_version={version}')
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            schema = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name').fetchall()
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            history = {table: db.execute('SELECT rowid,* FROM '+table).fetchall() for table in tables}
            columns = db.execute('PRAGMA table_info(warehouse_admissions)').fetchall()
            foreign_keys = db.execute('PRAGMA foreign_key_list(warehouse_admissions)').fetchall()
        return schema, history, columns, foreign_keys

    def test_v24_team_migration_preserves_existing_database_rows_as_null(self):
        import data_management as dm
        with store.connect() as db:
            database = dm.create_database(db, 'legacy_team', 'Legacy', '', 'test')
            db.execute("UPDATE databases SET team='Old Team' WHERE id=?", (database.id,))
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            db.execute('PRAGMA foreign_keys=OFF')
            db.execute('ALTER TABLE databases DROP COLUMN team')
            db.execute('PRAGMA user_version=24')
        with store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)
            row = db.execute('SELECT id,sql_catalog,display_name,description,managed_by,state,revision,team FROM databases WHERE id=?',
                             (database.id,)).fetchone()
            self.assertEqual(tuple(row), (database.id, 'legacy_team', 'Legacy', '', 'test', 'registered', 1, None))
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_v23_closure_upgrade_preserves_rows_references_and_schema_objects(self):
        schema, history, columns, foreign_keys = self.v23_admission_history()
        with store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            for table, rows in history.items():
                self.assertEqual([tuple(r) for r in db.execute('SELECT rowid,* FROM '+table)], rows, table)
            after = [tuple(r) for r in db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name')]
            old_table = next(r[3] for r in schema if r[1] == 'warehouse_admissions')
            new_table = next(r[3] for r in after if r[1] == 'warehouse_admissions')
            self.assertEqual(new_table, old_table.replace(
                "CHECK(execution_closure IN ('','FENCED_READ_ONLY'))",
                "CHECK(execution_closure IN ('','FENCED_READ_ONLY','FENCED_STAGE_CLEANUP'))"))
            self.assertEqual([r for r in after if r[1] != 'warehouse_admissions'],
                             [r for r in schema if r[1] != 'warehouse_admissions'])
            self.assertEqual([tuple(r) for r in db.execute('PRAGMA table_info(warehouse_admissions)')], columns)
            self.assertEqual([tuple(r) for r in db.execute('PRAGMA foreign_key_list(warehouse_admissions)')], foreign_keys)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            for closure in ('', 'FENCED_READ_ONLY', 'FENCED_STAGE_CLEANUP'):
                db.execute('UPDATE warehouse_admissions SET execution_closure=? WHERE id=\'child\'', (closure,))
            for closure in ('FENCED_OTHER', 'fenced_stage_cleanup', None):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute('UPDATE warehouse_admissions SET execution_closure=? WHERE id=\'child\'', (closure,))
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'retained admission actor'):
                db.execute("UPDATE warehouse_admissions SET actor='changed'")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM warehouse_admissions WHERE id='child'")

    def test_closure_upgrade_failure_rolls_back_entire_chain_and_retries(self):
        for version in (22, 23):
            with self.subTest(version=version):
                if (self.root / 'jobs.sqlite3').exists():
                    (self.root / 'jobs.sqlite3').unlink()
                schema, history, _, _ = self.v23_admission_history(version)
                migrate = store._migrate_admission_closures
                def fail_after_rebuild(db):
                    migrate(db)
                    raise RuntimeError('injected after rebuild')
                with patch.object(store, '_migrate_admission_closures', fail_after_rebuild):
                    with self.assertRaisesRegex(RuntimeError, 'injected'):
                        with store.connect():
                            self.fail('failed migration accepted')
                with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
                    self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], version)
                    self.assertEqual(db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name').fetchall(), schema)
                    for table, rows in history.items():
                        self.assertEqual(db.execute('SELECT rowid,* FROM '+table).fetchall(), rows, table)
                with store.connect() as db:
                    self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)
                    self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_closure_upgrade_checks_foreign_keys_before_commit_and_restores_enforcement(self):
        schema, history, _, _ = self.v23_admission_history()
        migrate = store._migrate_admission_closures
        def corrupt_reference(db):
            migrate(db)
            db.execute("UPDATE warehouse_admissions SET database_id='missing'")
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            with patch.object(store, '_migrate_admission_closures', corrupt_reference):
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'migration foreign key violation'):
                    store._bootstrap(db)
            self.assertFalse(db.in_transaction)
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 23)
            self.assertEqual([tuple(r) for r in db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name')], schema)
            for table, rows in history.items():
                self.assertEqual([tuple(r) for r in db.execute('SELECT rowid,* FROM '+table)], rows, table)
            store._bootstrap(db)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)

    def test_current_version_connection_does_no_writer_work(self):
        with store.connect():
            pass
        original = sqlite3.connect
        statements = []
        def traced(*args, **kwargs):
            db = original(*args, **kwargs)
            db.set_trace_callback(statements.append)
            return db
        with patch.object(store.sqlite3, 'connect', traced):
            with store.connect() as db:
                self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertFalse(any(sql.upper().startswith(('BEGIN', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP'))
                             or 'FOREIGN_KEYS=OFF' in sql.upper() for sql in statements), statements)

    def test_v22_ai_upgrade_is_additive_atomic_and_preserves_history(self):
        with store.connect() as db:
            db.execute("INSERT INTO jobs VALUES ('kept-v22','actor','{}','now','now')")
            db.execute('DROP TABLE ai_explanations')
            db.execute('PRAGMA user_version=22')
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            before = {table: sorted([tuple(row) for row in db.execute('SELECT * FROM '+table)], key=repr)
                      for table in tables}
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            db.execute('CREATE TABLE ai_explanations(sentinel TEXT)')
        with self.assertRaises(sqlite3.OperationalError):
            with store.connect():
                self.fail('conflicting migration accepted')
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 22)
            self.assert_history(db, before)
            db.execute('DROP TABLE ai_explanations')
        with store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)
            self.assert_history(db, before)
            self.assertEqual({r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                             set(before) | {'ai_explanations'})

    def v18_history(self):
        """Retain real v18 definitions, with representative irreversible history."""
        import data_management as dm
        with store.connect() as db:
            database = dm.create_database(db, 'history', 'History', '', 'test')
            schema = dm.create_schema(db, database.id, 'raw', 'Raw', 'raw', 'test')
            dm.record_query(db, dm.QueryRecord('unknown-query', 'actor',
                dm.QueryContext(database.id, schema.id, 'LOCAL', None), 'UNKNOWN', 'now'))
            db.execute("INSERT INTO operations VALUES ('unknown-op','actor','apply','[]',1,'UNKNOWN','[]',NULL,'{}','{}')")
            db.execute("INSERT INTO operations VALUES ('retirement-op','actor','retire','[]',1,'APPLIED','[]',NULL,'{}','{}')")
            db.execute("UPDATE databases SET state='RETIRED',revision=7 WHERE id=?", (database.id,))
            db.execute("INSERT INTO database_retirements VALUES ('retirement-op',?,3,'RETIRED','[]')", (database.id,))
            db.execute("INSERT INTO database_retirement_requests VALUES ('actor','retire','key','{}','retirement-op','{}')")
            db.execute("INSERT INTO object_bindings VALUES ('tombstone',?,'old_table','table','backend-old',2,'Old table',0)", (schema.id,))
            db.execute("INSERT INTO grants VALUES ('kept-reader',?,'bi_viewer',4)", (database.id,))
            db.execute("UPDATE effective_grants SET maximum_role='bi_viewer',epoch=9,applied_revision=4 WHERE principal_id='kept-reader'")
            db.execute("INSERT INTO grants VALUES ('deleted-reader',?,'data_engineer',6)", (database.id,))
            db.execute("DELETE FROM grants WHERE principal_id='deleted-reader'")
            drop_creation_schema(db)
            db.execute('PRAGMA user_version=18')
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {table: sorted([tuple(row) for row in db.execute('SELECT * FROM '+table)], key=repr)
                    for table in tables}

    def assert_history(self, db, before):
        for table, rows in before.items():
            self.assertEqual(sorted([tuple(row) for row in db.execute('SELECT * FROM '+table)], key=repr),
                             rows, table)
        self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_v18_creation_upgrade_preserves_all_history(self):
        before = self.v18_history()
        with store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)
            self.assert_history(db, before)
            for table in ('db_creations', 'db_creation_phase_plans',
                          'db_creation_requests', 'db_creation_principal_gates'):
                self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0], 0)

    def test_v19_creation_migration_failure_rolls_back_schema_version_and_history(self):
        before = self.v18_history()
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            schema_before = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name').fetchall()
        with patch.object(store, '_CREATION_SCHEMA', store._CREATION_SCHEMA + 'CREATE TABLE broken('):
            with self.assertRaises(sqlite3.OperationalError):
                with store.connect():
                    self.fail('failed migration accepted')
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 18)
            self.assertEqual(db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name').fetchall(), schema_before)
            self.assert_history(db, before)
        with store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)
            self.assert_history(db, before)

    def v21_history(self):
        import data_management as dm
        import warehouse_admission as admission
        with store.connect() as db:
            database = dm.create_database(db, 'retained_v21', 'Retained', '', 'test')
            schema = dm.create_schema(db, database.id, 'raw', 'Raw', 'raw', 'test')
            dm.record_query(db, dm.QueryRecord('retained-query', 'actor',
                dm.QueryContext(database.id, schema.id, 'LOCAL', None), 'UNKNOWN', 'now'))
            handle = admission.admit(db, actor='actor', kind='query', subject_id='retained-query')
            admission.finish(db, handle, state='UNKNOWN')
            db.execute("INSERT INTO jobs VALUES ('kept-v21','actor','{}','now','now')")
            db.execute("INSERT INTO operations VALUES ('kept-operation','actor','apply','[]',1,'UNKNOWN','[]',NULL,'{}','{}')")
            drop_dbt_schema(db)
            db.execute('PRAGMA user_version=21')
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {table: sorted([tuple(row) for row in db.execute('SELECT * FROM '+table)], key=repr)
                    for table in tables}

    def test_v21_dbt_upgrade_preserves_all_history(self):
        before = self.v21_history()
        self.assertTrue(all(table not in before for table in
                            ('dbt_runs', 'dbt_run_operations', 'dbt_requests')))
        with store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)
            self.assert_history(db, before)
            for table in ('dbt_runs', 'dbt_run_operations', 'dbt_requests'):
                self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0], 0)

    def test_v21_dbt_upgrade_failure_rolls_back_schema_and_history(self):
        before = self.v21_history()
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            schema_before = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name').fetchall()
        with patch.object(store, '_DBT_SCHEMA', store._DBT_SCHEMA + 'CREATE TABLE broken('):
            with self.assertRaises(sqlite3.OperationalError):
                with store.connect():
                    self.fail('failed dbt migration accepted')
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 21)
            self.assertEqual(db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name').fetchall(),
                             schema_before)
            self.assert_history(db, before)
        with store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)
            self.assert_history(db, before)

    def test_creation_requests_enforce_scope_foreign_keys_and_immutable_ack(self):
        with store.connect() as db:
            request = '''INSERT INTO db_creation_requests
                (id,environment_id,actor,route,idempotency_key,payload,action,ack,
                 dispatch_state,created_at,updated_at) VALUES (?,?,?,?,?,'{}','STOP_PRINCIPAL','{}','ACCEPTED','now','now')'''
            db.execute(request, ('request', 'environment', 'executor', 'stop', 'key'))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(request, ('duplicate', 'environment', 'executor', 'stop', 'key'))
            db.execute(request, ('other-environment', 'other', 'executor', 'stop', 'key'))
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'immutable'):
                db.execute("UPDATE db_creation_requests SET ack='changed' WHERE id='request'")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO db_creation_principal_gates VALUES ('environment','actor','STOPPED',1,'reason','missing','now')")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO db_creation_principal_gates VALUES ('environment','actor','ACTIVE',0,NULL,NULL,'now')")
            db.execute("UPDATE db_creation_requests SET dispatch_state='COMPLETE' WHERE id='request'")
            self.assertEqual(db.execute("SELECT ack,dispatch_state FROM db_creation_requests WHERE id='request'").fetchone()[:],
                             ('{}', 'COMPLETE'))

    def test_v17_retirement_upgrade_retains_rows_context_and_legacy_admission(self):
        import data_management as dm
        import warehouse_admission as admission
        with store.connect() as db:
            database = dm.create_database(db, 'retained', 'Retained', '', 'test')
            schema = dm.create_schema(db, database.id, 'raw', 'Raw', 'raw', 'test')
            dm.record_query(db, dm.QueryRecord('query', 'actor', dm.QueryContext(database.id, schema.id, 'LOCAL', None), 'UNKNOWN', 'now'))
            db.execute('BEGIN IMMEDIATE') if not db.in_transaction else None
            handle = admission.admit(db, actor='actor', kind='query', subject_id='query')
            db.execute("UPDATE warehouse_admissions SET state='UNKNOWN',execution_closure='FENCED_READ_ONLY' WHERE id=?", (handle.id,))
            db.execute('''INSERT INTO query_execution_contexts VALUES
                ('query','admin','read','retained','raw',1,NULL,'user','credential',NULL,'request',NULL,'SELECT private')''')
            db.execute("INSERT INTO jobs VALUES ('kept','actor','{\"context\":\"original\"}','now','now')")
            drop_creation_schema(db)
            db.execute('DROP TABLE database_retirement_requests')
            db.execute('DROP TABLE database_retirements')
            db.execute('ALTER TABLE warehouse_admissions DROP COLUMN database_id')
            db.execute('PRAGMA user_version=17')
            before = {table: [tuple(row) for row in db.execute('SELECT * FROM '+table)] for table in
                      ('databases', 'schemas', 'query_records', 'query_execution_contexts', 'warehouse_admissions', 'jobs')}
        with store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)
            for table, rows in before.items():
                after = [tuple(row) for row in db.execute('SELECT * FROM '+table)]
                if table == 'warehouse_admissions':
                    self.assertTrue(all(row[-1] is None for row in after))
                    after = [row[:-1] for row in after]
                self.assertEqual(after, rows, table)
            self.assertEqual(admission.get_for_subject(db, kind='query', subject_id='query').database_id, None)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE warehouse_admissions SET database_id='missing'")
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        env = patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(self.root))
        env.start()
        self.addCleanup(env.stop)

    def test_fresh_reentrant_version_schema_and_permissions(self):
        expected = {'jobs', 'stages', 'stage_files', 'runs', 'steps', 'schedules', 'alerts',
                    'databases', 'catalog_bindings', 'schemas', 'grants', 'object_bindings',
                    'warehouse_config_generations', 'operations', 'query_records', 'admission_records',
                    'query_execution_contexts', 'warehouse_controls', 'warehouse_admissions',
                    'generation_plans', 'generation_plan_history', 'operation_requests', 'operation_steps', 'warehouse_action_requests',
                    'database_create_requests', 'registry_register_requests',
                    'dataops_operations', 'dataops_queries', 'dataops_owned_objects', 'dataops_target_claims',
                    'wap_models', 'wap_model_versions', 'wap_builds', 'wap_audits', 'wap_publications', 'export_artifacts',
                    'effective_grants', 'grant_requests', 'grant_audit', 'metadata_requests', 'metadata_audit',
                    'db_jobs', 'db_job_runs', 'db_job_steps', 'db_job_operations', 'db_job_requests',
                    'db_stages', 'db_stage_claims', 'db_stage_runs', 'db_stage_requests', 'db_stage_publications',
                    'db_stage_scans', 'db_stage_scan_items', 'db_stage_scan_operations', 'db_stage_content',
                    'execution_recovery_requests', 'db_marts', 'db_mart_runs',
                    'db_mart_operations', 'db_mart_requests', 'db_schedules', 'db_schedule_ticks',
                    'db_schedule_requests', 'db_alerts', 'db_alert_subjects',
                    'database_retirements', 'database_retirement_requests',
                    'db_creations', 'db_creation_phase_plans', 'db_creation_requests',
                    'db_creation_principal_gates', 'logical_requests', 'logical_effects',
                    'logical_foundations', 'logical_bindings', 'logical_backend_identities',
                    'dbt_runs', 'dbt_run_operations', 'dbt_requests', 'ai_explanations'}
        for _ in range(2):
            with store.connect() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)
                self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
                self.assertEqual(db.execute('PRAGMA busy_timeout').fetchone()[0], 5000)
                self.assertEqual({r['name'] for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}, expected)
                self.assertEqual(db.execute('SELECT count(*) FROM databases').fetchone()[0], 0)
                db.execute('BEGIN IMMEDIATE')
                with store.connect() as other:
                    self.assertIsInstance(other.execute('SELECT 1').fetchone(), sqlite3.Row)
        self.assertEqual((self.root / 'jobs.sqlite3').stat().st_mode & 0o777, 0o600)

    def test_concurrent_first_open_and_fresh_interpreter(self):
        def open_store(_):
            with store.connect() as db:
                return db.execute('PRAGMA user_version').fetchone()[0]
        with ThreadPoolExecutor(max_workers=6) as pool:
            self.assertEqual(list(pool.map(open_store, range(6))), [store.VERSION] * 6)
        result = subprocess.run([sys.executable, '-c',
            'import control_store as s\nwith s.connect() as c: print(c.execute("PRAGMA user_version").fetchone()[0])'],
            env=dict(os.environ, PYTHONPATH=str(TOOLS)), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(store.VERSION))

    def test_future_version_rejected_before_mutation(self):
        path = self.root / 'jobs.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE sentinel(value TEXT)')
            db.execute('PRAGMA user_version='+str(store.VERSION+1))
        before = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'version'):
            with store.connect():
                self.fail('future version accepted')
        self.assertEqual(path.read_bytes(), before)

    def test_v11_migration_failure_is_atomic_and_preserves_existing_rows(self):
        with store.connect() as db:
            drop_metadata_schema(db)
            db.execute('PRAGMA user_version=10')
            db.execute("INSERT INTO jobs VALUES ('kept','a','{}','now','now')")
            db.execute('CREATE TABLE metadata_audit(sentinel TEXT)')
        with self.assertRaises(sqlite3.OperationalError), store.connect():
            pass
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 10)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='metadata_requests'").fetchone())
            self.assertEqual(db.execute('SELECT name FROM jobs').fetchone()[0], 'kept')
            db.execute('DROP TABLE metadata_audit')
        with store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)
            self.assertEqual(db.execute('SELECT name FROM jobs').fetchone()[0], 'kept')

    def test_version_one_upgrade_retains_rows_and_failure_rolls_back(self):
        with store.connect() as db:
            db.execute("INSERT INTO jobs VALUES ('kept','a','{}','now','now')")
            for table in ('export_artifacts', 'wap_publications', 'wap_audits', 'wap_builds', 'wap_model_versions', 'wap_models',
                          'dataops_target_claims', 'dataops_owned_objects', 'dataops_queries', 'dataops_operations'):
                db.execute('DROP TABLE ' + table)
            db.execute('DROP TABLE query_execution_contexts')
            for table in ('registry_register_requests', 'database_create_requests', 'warehouse_action_requests', 'operation_steps', 'operation_requests', 'generation_plans',
                          'warehouse_admissions', 'warehouse_controls'):
                db.execute('DROP TABLE ' + table)
            drop_grant_schema(db)
            db.execute('PRAGMA user_version=1')
        with patch.object(store, '_QUERY_SCHEMA', 'CREATE TABLE invalid('):
            with self.assertRaises(sqlite3.OperationalError):
                with store.connect():
                    pass
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT name FROM jobs').fetchone()[0], 'kept')
        with store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)
            self.assertEqual(db.execute('SELECT name FROM jobs').fetchone()[0], 'kept')

    def test_version_two_upgrade_failure_is_atomic(self):
        with store.connect() as db:
            for table in ('export_artifacts', 'wap_publications', 'wap_audits', 'wap_builds', 'wap_model_versions', 'wap_models',
                          'dataops_target_claims', 'dataops_owned_objects', 'dataops_queries', 'dataops_operations'):
                db.execute('DROP TABLE ' + table)
            for table in ('registry_register_requests', 'database_create_requests', 'warehouse_action_requests', 'operation_steps', 'operation_requests', 'generation_plans',
                          'warehouse_admissions', 'warehouse_controls'):
                db.execute('DROP TABLE ' + table)
            drop_grant_schema(db)
            db.execute('PRAGMA user_version=2')
        with patch.object(store, '_WAREHOUSE_SCHEMA', store._WAREHOUSE_SCHEMA + 'CREATE TABLE broken('):
            with self.assertRaises(sqlite3.OperationalError):
                with store.connect():
                    pass
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='warehouse_action_requests'").fetchone())
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='warehouse_controls'").fetchone())
        with store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)

    def test_version_five_dataops_upgrade_preserves_existing_rows(self):
        with store.connect() as db:
            db.execute("INSERT INTO jobs VALUES ('kept','a','{}','now','now')")
            for table in ('export_artifacts', 'wap_publications', 'wap_audits', 'wap_builds', 'wap_model_versions', 'wap_models',
                          'dataops_target_claims', 'dataops_owned_objects', 'dataops_queries', 'dataops_operations'):
                db.execute('DROP TABLE ' + table)
            drop_grant_schema(db)
            db.execute('PRAGMA user_version=5')
        with store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)
            self.assertEqual(db.execute('SELECT name FROM jobs').fetchone()[0], 'kept')
            self.assertEqual(db.execute('SELECT count(*) FROM dataops_operations').fetchone()[0], 0)

    def test_version_six_commit_and_claim_upgrade_is_atomic_and_preserves_operations(self):
        with store.connect() as db:
            for table in ('export_artifacts', 'wap_publications', 'wap_audits', 'wap_builds', 'wap_model_versions', 'wap_models'):
                db.execute('DROP TABLE ' + table)
            db.execute('ALTER TABLE dataops_queries DROP COLUMN commit_result')
            drop_grant_schema(db)
            db.execute('PRAGMA user_version=6')
            db.execute('''INSERT INTO dataops_operations
                (id,actor,route,idempotency_key,payload,fingerprint,context,backend_username,credential_snapshot_ref,state,created_at)
                VALUES ('kept','a','r','key','{}','digest','{}','backend','private-ref','UNKNOWN','now')''')
        # Deliberately leave the new table to fail the second migration statement.
        with self.assertRaises(sqlite3.OperationalError):
            with store.connect():
                pass
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 6)
            self.assertNotIn('commit_result', [r[1] for r in db.execute('PRAGMA table_info(dataops_queries)')])
            db.execute('DROP TABLE dataops_target_claims')
        with store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)
            self.assertEqual(db.execute('SELECT state FROM dataops_operations WHERE id=?', ('kept',)).fetchone()[0], 'UNKNOWN')
            self.assertIn('commit_result', [r['name'] for r in db.execute('PRAGMA table_info(dataops_queries)')])

    def test_version_seven_wap_upgrade_is_atomic(self):
        with store.connect() as db:
            db.execute("INSERT INTO jobs VALUES ('kept','a','{}','now','now')")
            for table in ('export_artifacts', 'wap_publications', 'wap_audits', 'wap_builds', 'wap_model_versions', 'wap_models'):
                db.execute('DROP TABLE ' + table)
            drop_grant_schema(db)
            db.execute('PRAGMA user_version=7')
        with patch.object(store, '_WAP_SCHEMA', store._WAP_SCHEMA + 'CREATE TABLE broken('):
            with self.assertRaises(sqlite3.OperationalError):
                with store.connect():
                    pass
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 7)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='wap_models'").fetchone())
        with store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)
            self.assertEqual(db.execute('SELECT name FROM jobs').fetchone()[0], 'kept')

    def test_version_eight_export_upgrade_preserves_wap(self):
        with store.connect() as db:
            db.execute('DROP TABLE export_artifacts')
            drop_grant_schema(db)
            db.execute('PRAGMA user_version=8')
            db.execute("INSERT INTO jobs VALUES ('kept','a','{}','now','now')")
        with store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], store.VERSION)
            self.assertEqual(db.execute('SELECT name FROM jobs').fetchone()[0], 'kept')
            self.assertEqual(db.execute('SELECT count(*) FROM export_artifacts').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM wap_models').fetchone()[0], 0)

    def test_commit_rollback_close_and_dynamic_root(self):
        with store.connect() as db:
            db.execute("INSERT INTO jobs VALUES ('kept','a','{}','now','now')")
        with self.assertRaises(ValueError):
            with store.connect() as rolled_back:
                rolled_back.execute("INSERT INTO jobs VALUES ('lost','a','{}','now','now')")
                raise ValueError('abort')
        with self.assertRaises(sqlite3.ProgrammingError):
            rolled_back.execute('SELECT 1')
        with store.connect() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT name FROM jobs')], ['kept'])
        with patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(self.root / 'other')):
            with store.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)

    def test_existing_five_column_completion_and_partial_index(self):
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            db.execute('''CREATE TABLE runs (
                id TEXT PRIMARY KEY, job_name TEXT NOT NULL, actor TEXT NOT NULL,
                role TEXT NOT NULL, definition TEXT NOT NULL, arguments TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT,
                finished_at TEXT, error TEXT)''')
            db.execute('''CREATE TABLE schedules (
                name TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
                every_minutes INTEGER, time TEXT, timezone TEXT, arguments TEXT NOT NULL,
                enabled INTEGER NOT NULL, role TEXT NOT NULL, auth_mode TEXT NOT NULL,
                next_run_at TEXT, last_status TEXT, last_run_id TEXT, last_scheduled_for TEXT)''')
            db.execute("INSERT INTO runs VALUES ('old','job','actor','admin','{}','{}','success','now',NULL,NULL,NULL)")
        with store.connect() as db:
            self.assertTrue({'trigger', 'scheduled_for'} <= {
                r['name'] for r in db.execute('PRAGMA table_info(runs)')})
            self.assertTrue({'date', 'weekdays', 'day_of_month'} <= {
                r['name'] for r in db.execute('PRAGMA table_info(schedules)')})
            sql = db.execute("SELECT sql FROM sqlite_master WHERE name='one_active_job'").fetchone()[0]
            self.assertIn("WHERE status IN ('queued', 'running')", sql)
            self.assertEqual(tuple(db.execute('SELECT id, trigger, scheduled_for FROM runs').fetchone()),
                             ('old', 'manual', None))

    def test_malformed_jobs_bootstrap_failure_is_atomic(self):
        path = self.root / 'jobs.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE runs(id TEXT PRIMARY KEY, job_name TEXT NOT NULL)')
            db.execute("INSERT INTO runs VALUES ('retained', 'original')")
            before = db.execute('SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY name').fetchall()
        for _ in range(2):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'status'):
                with store.connect():
                    self.fail('malformed Jobs schema accepted')
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 0)
                self.assertEqual(db.execute(
                    'SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY name').fetchall(), before)
                self.assertEqual(db.execute('SELECT * FROM runs').fetchall(), [('retained', 'original')])


if __name__ == '__main__':
    unittest.main()
