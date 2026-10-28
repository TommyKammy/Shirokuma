"""Bounded Stage contracts against real P3 admission, SQLite and fake receipts."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import uuid
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store
import data_management as dm
import dataops_service as ds
import db_stages as stages
import manual_export
import query_service as qs
from query_context import QueryError, resolve_context
import request_auth
import warehouse_admission as admission
import test_manual_ingest as ingest_fixture
import test_manual_export as export_fixture


class StageFixture:
    def stage_setup(self, fixture):
        self.f = fixture
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.b = self.f.fixture if hasattr(self.f, 'fixture') else self.f.b
        # An ordinary database admin, so revoke/grant epochs are meaningful.
        self.b.fixture.users['owner']['platform_admin'] = False
        self.b.fixture.save()
        with control_store.connect() as db:
            for database in self.b.databases:
                dm.create_grant(db, 'owner', database, 'admin')
                # Synthetic backend-admin proof for this isolated fake transport;
                # normal grant promotion deliberately requires real verification.
                db.execute('''UPDATE effective_grants SET maximum_role='admin',epoch=epoch+1,
                    applied_revision=1,applied_generation_id=? WHERE principal_id='owner' AND database_id=?''',
                           ('g-' + database, database))
                proof_id = str(uuid.uuid4())
                dm.record_operation(db, dm.Operation(proof_id, 'stage-test-backend', 'WAREHOUSE_CONFIG_APPLY',
                    (), None, 'APPLIED', (), 'g-' + database, (), ()))
                db.execute('''INSERT INTO grant_audit
                    (operation_id,apply_operation_id,actor,principal_id,database_id,before_role,after_role,
                     effective_role,expected_revision,desired_revision,effective_epoch,generation_id,receipt)
                    VALUES (?,?,?,'owner',?,'revoked','admin','admin',1,1,2,?,?)''',
                    (proof_id, proof_id, 'stage-test-backend', database, 'g-' + database, 'test:stage-fake-backend-admin'))
        self.p = self.b.fixture.principal('owner')
        self.c = self.b.context(principal=self.p, role='admin')
        # Only the backend transport/credential provider is synthetic. Every
        # context and grant check still calls the real authorization boundary.
        def prepare_current(*, principal, database_id, selected_role):
            with control_store.connect() as db:
                generation = dm.get_binding(db, database_id).applied_generation_id
            decision = request_auth.authorize_database(principal, database_id, selected_role,
                                                       expected_generation_id=generation)
            return decision, {'X-Trino-User': 'fixed_' + database_id, 'Authorization': self.b.credential}
        self.b.start_patch('trino_auth.prepare_backend_v2', side_effect=prepare_current)
        self.root = control_store.root() / 'stages'
        self.root.mkdir()
        self.directory = self.root / 'incoming'
        self.directory.mkdir()
        self.path = self.directory / 'data.csv'
        self.path.write_bytes(b'id,label\n1,001\n2,\n3,text\n')
        self.start = patch.object(stages, '_start_run')
        self.start_mock = self.start.start()
        self.addCleanup(self.start.stop)

    def definition(self, **changes):
        result = dict(name='daily', directory=str(self.directory), mode='create', format='auto',
                      destination=asdict(self.b.locator('stage_output')))
        result.update(changes)
        return result

    def save(self, key='save', **kw):
        return stages.save_stage(self.p, kw.pop('context', self.c), key=key,
            expected_revision=kw.pop('expected_revision', 0), definition=kw.pop('definition', self.definition()), **kw)

    def preview(self, stage, key='preview'):
        file = stages.list_files(self.p, self.c, stage['id'])['files'][0]
        return stages.preview(self.p, self.c, stage['id'], key=key, expected_revision=stage['revision'],
                              file=file['name'], version=file['version']), file

    def submit(self, stage=None, key='import', **changes):
        stage = stage or self.save()
        preview, file = self.preview(stage, key='preview-' + key)
        args = dict(key=key, expected_revision=stage['revision'], file=file['name'], version=file['version'],
                    expected_preview_fingerprint=preview['confirmation_fingerprint'])
        args.update(changes)
        return stages.import_file(self.p, self.c, stage['id'], **args)

    def execute(self, run):
        stages._execute_run(run['id'], ds.Execution(self.p, run['operation_id']))
        return stages.get_run(self.p, self.c, run['stage_id'], run['id'])

    def code(self, expected, function):
        with self.assertRaises(QueryError) as caught:
            function()
        error = caught.exception
        actual = error.details['reason'] if error.code == 'STAGE_INPUT_REJECTED' else error.code
        self.assertEqual(actual, expected)

    def revoke(self):
        with control_store.connect() as db:
            db.execute('DELETE FROM grants WHERE principal_id=? AND database_id=?',
                       ('owner', self.c.database_id))

    def regrant(self, *, new_generation=False):
        with control_store.connect() as db:
            dm.create_grant(db, 'owner', self.c.database_id, 'admin')
            generation = self.c.generation_id
            if new_generation:
                generation = 'next-' + generation
                dm.record_generation(db, dm.WarehouseConfigGeneration(generation, 'w', self.c.database_id, (), (), (), 'READY'))
                db.execute('UPDATE catalog_bindings SET applied_generation_id=? WHERE database_id=?', (generation, self.c.database_id))
            db.execute("UPDATE effective_grants SET maximum_role='admin',epoch=epoch+1,applied_revision=1,applied_generation_id=? WHERE principal_id='owner' AND database_id=?", (generation, self.c.database_id))
        self.c = resolve_context(self.p, self.c.database_id, self.c.schema_id, 'admin',
                                 warehouse_id='w', expected_binding_revision=self.c.binding_revision)


class StageStateTests(StageFixture, unittest.TestCase):

    def test_retiring_individual_stage_continues_and_public_run_is_denied(self):
        run = self.submit()
        self.b.retire()
        with self.assertRaises((QueryError, PermissionError)):
            stages.get_run(self.p, self.c, run['stage_id'], run['id'])
        stages._execute_run(run['id'], ds.Execution(self.p, run['operation_id']))
        with control_store.connect() as db:
            record = json.loads(db.execute('SELECT record FROM db_stage_runs WHERE id=?',
                                           (run['id'],)).fetchone()[0])
        self.assertEqual(record['status'], 'succeeded')
        self.b.assert_database_admissions()
        result = stages.cancel_run(self.p, self.c, run['stage_id'], run['id'], key='retire-cancel')
        self.assertNotIn('summary', result)
        self.assertNotIn('result', result)

    def setUp(self):
        self.stage_setup(ingest_fixture.ManualIngestTests())

    def test_additive_v13_migration_and_retained_claims(self):
        stage = self.save()
        with control_store.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], control_store.VERSION)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM stages').fetchone()[0], 0)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('DELETE FROM db_stage_claims')
        # Separate synthetic v13 store, preserving its real P3 data.
        alternate = self.b.fixture.root / 'migration'
        with patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(alternate)):
            with control_store.connect() as db:
                from test_control_store import drop_creation_schema
                drop_creation_schema(db)
                for trigger in ('db_stage_claim_immutable', 'db_stage_claim_retained', 'db_stage_immutable'):
                    db.execute('DROP TRIGGER ' + trigger)
                for table in ('db_stage_publications', 'db_stage_requests', 'db_stage_runs', 'db_stage_claims', 'db_stages'):
                    db.execute('DROP TABLE ' + table)
                db.execute("INSERT INTO jobs VALUES ('retained','owner','{}','now','now')")
                database = dm.create_database(db, 'old_grants', 'Old', '', 'test').id
                dm.create_grant(db, 'alice', database, 'data_engineer')
                retained = tuple(db.execute('SELECT * FROM effective_grants').fetchone())
                for name in ('grant_insert', 'grant_update', 'grant_delete'):
                    db.execute('DROP TRIGGER ' + name)
                sql = db.execute("SELECT sql FROM sqlite_master WHERE name='effective_grants'").fetchone()[0]
                db.execute('DROP TABLE effective_grants')
                db.execute(sql.replace(",'admin'", ''))
                db.execute('INSERT INTO effective_grants VALUES (?,?,?,?,?,?)', retained)
                db.execute('PRAGMA user_version=13')
            with control_store.connect() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], control_store.VERSION)
                self.assertEqual(db.execute('SELECT name FROM jobs').fetchone()[0], 'retained')
                self.assertEqual(tuple(db.execute('SELECT * FROM effective_grants').fetchone()), retained)
                self.assertIn("'admin'", db.execute("SELECT sql FROM sqlite_master WHERE name='effective_grants'").fetchone()[0])
        self.assertEqual(stages.get_stage(self.p, self.c, stage['id']), stage)

    def test_interim_v14_old_roles_repair_once_without_writer_on_normal_reads(self):
        import grant_management
        alternate = self.b.fixture.root / 'interim-v14'
        with patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(alternate)):
            with control_store.connect() as db:
                for name in ('grant_insert', 'grant_update', 'grant_delete'):
                    db.execute('DROP TRIGGER ' + name)
                sql = db.execute("SELECT sql FROM sqlite_master WHERE name='effective_grants'").fetchone()[0]
                db.execute('DROP TABLE effective_grants')
                db.execute(sql.replace(",'admin'", ''))
            with patch.object(grant_management, 'migrate_database_admin', wraps=grant_management.migrate_database_admin) as migrate:
                with control_store.connect() as db:
                    self.assertIn("'admin'", db.execute("SELECT sql FROM sqlite_master WHERE name='effective_grants'").fetchone()[0])
                self.assertEqual(migrate.call_count, 1)
                with control_store.connect() as db:
                    statements = []
                    db.set_trace_callback(statements.append)
                    control_store._bootstrap(db)
                    self.assertFalse(any('BEGIN' in sql for sql in statements))
                self.assertEqual(migrate.call_count, 1)

    def test_stale_binding_requires_explicit_cas_refresh_and_other_schema_ref(self):
        stage = self.save()
        target = self.b.existing('fresh_target', schema='sandbox')
        with control_store.connect() as db:
            db.execute('UPDATE catalog_bindings SET desired_revision=2,applied_revision=2 WHERE database_id=?', (self.c.database_id,))
        self.c = resolve_context(self.p, self.c.database_id, self.c.schema_id, 'admin', warehouse_id='w', expected_binding_revision=2)
        self.code('CONTEXT_MISMATCH', lambda: stages.get_stage(self.p, self.c, stage['id']))
        self.code('CONTEXT_MISMATCH', lambda: stages.import_file(self.p, self.c, stage['id'], key='stale-run',
            expected_revision=1, file='data.csv', version='x', expected_preview_fingerprint='x'))
        definition = self.definition(mode='append', target=asdict(target))
        definition.pop('destination')
        refreshed = self.save(key='refresh', stage_id=stage['id'], expected_revision=1, definition=definition)
        self.assertEqual(refreshed['context'], asdict(self.c))
        result = self.execute(self.submit(refreshed, key='fresh'))
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['target_ref'], asdict(target))
        self.assertEqual(result['context']['binding_revision'], 2)
        self.assertTrue(any('"sandbox"."fresh_target"' in (call[1] or '') for call in self.b.calls))
        cross = self.b.locator('cross', self.b.databases[1])
        self.code('CROSS_DATABASE_TARGET', lambda: self.save(key='cross', stage_id=stage['id'], expected_revision=2,
            definition=self.definition(destination=asdict(cross))))
        with control_store.connect() as db:
            changed = dict(asdict(self.c), schema_id=self.b.schemas[self.c.database_id]['sandbox'])
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE db_stages SET context=? WHERE id=?', (ds._json(changed), stage['id']))

    def test_admitted_ingest_keeps_original_definition_after_cas_edit(self):
        stage = self.save()
        run = self.submit(stage)
        original = stages._captured_input
        replacement = self.b.locator('retargeted')
        def edit(*args):
            self.save(key='edit-after-admission', stage_id=stage['id'], expected_revision=1,
                definition=self.definition(name='renamed', destination=asdict(replacement), batch_size=1))
            self.path.write_bytes(b'id,label\n999,changed\n')
            return original(*args)
        with patch.object(stages, '_captured_input', edit):
            result = self.execute(run)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['summary']['committed_rows'], 3)
        with control_store.connect() as db:
            actual = dm.get_object(db, result['target_ref']['object_id'])
            self.assertEqual(actual.physical_name, 'stage_output')
        self.assertFalse(any('"retargeted"' in (c[1] or '') for c in self.b.calls))

    def test_partial_dot_segments_list_preview_import_rejected_before_sql(self):
        stage = self.save()
        names = ['batch.partial.csv', 'batch.tmp.csv', 'batch.part.csv', 'batch.temp.csv',
                 'batch.crdownload.csv', 'batch.csv.tmp', '~batch.csv', 'batch.csv~', '.batch.csv']
        for index, name in enumerate(names):
            (self.directory / name).write_bytes(self.path.read_bytes())
            for action in ('preview', 'import'):
                options = dict(key=action + str(index), expected_revision=1, file=name, version=stages._hash(self.path.read_bytes()))
                with self.assertRaises(QueryError) as caught:
                    if action == 'preview':
                        stages.preview(self.p, self.c, stage['id'], **options)
                    else:
                        stages.import_file(self.p, self.c, stage['id'], expected_preview_fingerprint='x', **options)
                self.assertEqual(caught.exception.code, 'STAGE_INPUT_REJECTED')
                self.assertEqual(caught.exception.details, dict(admission='NOT_ATTEMPTED', reason='INVALID_INPUT'))
        self.assertEqual([f['name'] for f in stages.list_files(self.p, self.c, stage['id'])['files']], ['data.csv'])
        self.assertFalse(self.b.calls)

    def test_fresh_input_marker_is_request_local_after_replay_before_begin_only(self):
        stage = self.save()
        preview, file = self.preview(stage)
        options = dict(expected_revision=1, file=file['name'], version=file['version'],
                       expected_preview_fingerprint=preview['confirmation_fingerprint'])
        for index, (changes, status, reason) in enumerate([
                ({'version': 'changed'}, 409, 'STAGE_INPUT_CHANGED'),
                ({'expected_preview_fingerprint': 'changed'}, 409, 'PREVIEW_CHANGED'),
                ({'expected_revision': 2}, 409, 'STAGE_REVISION_CONFLICT'),
                ({'expected_revision': []}, 400, 'INVALID_INPUT'),
                ({'file': []}, 400, 'INVALID_INPUT')]):
            with self.assertRaises(QueryError) as caught:
                stages.import_file(self.p, self.c, stage['id'], key='invalid-' + str(index), **dict(options, **changes))
            self.assertEqual((caught.exception.code, caught.exception.status), ('STAGE_INPUT_REJECTED', status))
            self.assertEqual(caught.exception.details, dict(admission='NOT_ATTEMPTED', reason=reason))
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dataops_operations').fetchone()[0], 0)
        run = stages.import_file(self.p, self.c, stage['id'], key='accepted', **options)
        self.path.unlink()
        self.assertEqual(stages.import_file(self.p, self.c, stage['id'], key='accepted', **options)['id'], run['id'])
        with self.assertRaises(QueryError) as caught:
            stages.import_file(self.p, self.c, stage['id'], key='accepted', **dict(options, file=[]))
        self.assertEqual(caught.exception.code, 'IDEMPOTENCY_CONFLICT')
        self.assertNotIn('admission', caught.exception.details)
        self.path.write_bytes(b'id,label\n1,001\n2,\n3,text\n')
        with patch.object(stages, '_start_run', side_effect=RuntimeError('thread start failed')):
            failed_start = stages.import_file(self.p, self.c, stage['id'], key='start-fails', **options)
        self.assertEqual(failed_start['status'], 'unknown')
        self.assertTrue(failed_start['operation_id'])
        self.assertNotIn('target_ref', failed_start)
        self.assertEqual(failed_start['destination_intent'], stage['definition']['destination'])
        self.assertNotIn('admission', failed_start)
        with patch.object(ds, 'begin', side_effect=QueryError('STAGE_INPUT_CHANGED', 409)):
            with self.assertRaises(QueryError) as caught:
                stages.import_file(self.p, self.c, stage['id'], key='begin-fails', **options)
        self.assertEqual(caught.exception.code, 'STAGE_INPUT_CHANGED')
        self.assertNotIn('admission', caught.exception.details)

    def test_regrant_generation_filters_old_runs_and_new_run_uses_fresh_context(self):
        stage = self.save()
        old = self.execute(self.submit(stage))
        old_context = self.c
        self.revoke()
        self.regrant(new_generation=True)
        self.assertEqual(stages.list_runs(self.p, self.c, stage['id']), [])
        self.code('CONTEXT_MISMATCH', lambda: stages.get_run(self.p, self.c, stage['id'], old['id']))
        self.code('CONTEXT_MISMATCH', lambda: stages.get_run(self.p, old_context, stage['id'], old['id']))
        self.assertEqual(stages.list_files(self.p, self.c, stage['id'])['files'][0]['name'], 'data.csv')
        new_definition = self.definition(destination=asdict(self.b.locator('fresh_after_regrant')))
        refreshed = self.save(key='new-target', stage_id=stage['id'], expected_revision=1, definition=new_definition)
        fresh = self.execute(self.submit(refreshed, key='new-import'))
        self.assertEqual(fresh['status'], 'succeeded', fresh)
        self.assertEqual([r['id'] for r in stages.list_runs(self.p, self.c, stage['id'])], [fresh['id']])
        with control_store.connect() as db:
            stored = json.loads(db.execute('SELECT record FROM db_stage_runs WHERE id=?', (old['id'],)).fetchone()[0])
        self.assertEqual(stored['context'], asdict(old_context))

    def test_owner_admin_database_isolation_and_capability(self):
        stage = self.save()
        self.code('SQL_PERMISSION_DENIED', lambda: stages.list_stages(self.b.principal, self.b.context()))
        for number, dbid in enumerate(self.b.databases[1:]):
            directory = self.root / ('db-' + str(number))
            directory.mkdir()
            context = self.b.context(dbid, principal=self.p, role='admin')
            definition = self.definition(directory=str(directory), destination=asdict(self.b.locator('stage_output', dbid)))
            other = self.save(key='db-' + str(number), context=context, definition=definition)
            self.assertNotEqual(stage['id'], other['id'])
            self.assertEqual(len(stages.list_stages(self.p, context)), 1)
            self.code('STAGE_NOT_FOUND', lambda: stages.get_stage(self.p, context, stage['id']))
        self.b.fixture.users['stranger'] = dict(self.b.fixture.users['owner'], platform_admin=True)
        self.b.fixture.save()
        stranger = self.b.fixture.principal('stranger')
        context = self.b.context(principal=stranger, role='admin')
        self.code('STAGE_NOT_FOUND', lambda: stages.get_stage(stranger, context, stage['id']))

    def test_cas_root_overlap_and_immutable_directory(self):
        stage = self.save()
        self.assertEqual(self.save(key='rename', expected_revision=1, stage_id=stage['id'],
            definition=self.definition(name='renamed'))['revision'], 2)
        self.code('STAGE_REVISION_CONFLICT', lambda: self.save(key='stale', expected_revision=1, stage_id=stage['id']))
        sibling = self.root / 'other'
        sibling.mkdir()
        self.code('STAGE_DIRECTORY_IMMUTABLE', lambda: self.save(key='move', stage_id=stage['id'], expected_revision=2,
                  definition=self.definition(directory=str(sibling))))
        child = self.directory / 'nested'
        child.mkdir()
        for index, path in enumerate((self.directory, child, self.root)):
            with self.assertRaises(QueryError):
                self.save(key='overlap-' + str(index), definition=self.definition(name='other', directory=str(path)))
        with patch.dict(os.environ, SHIROKUMA_STAGE_ROOTS=json.dumps([str(sibling)])):
            self.code('STAGE_DIRECTORY_REJECTED', lambda: stages.list_files(self.p, self.c, stage['id']))
        with patch.dict(os.environ, SHIROKUMA_STAGE_ROOTS='["relative"]'):
            with self.assertRaises(ValueError):
                stages.list_files(self.p, self.c, stage['id'])

    def _claim_peer(self):
        name = 'claim-peer'
        self.b.fixture.users[name] = dict(self.b.fixture.users['owner'])
        self.b.fixture.save()
        # Same isolated backend-admin activation proof as StageFixture; product
        # authentication and current grant resolution are not patched.
        with control_store.connect() as db:
            for database in self.b.databases:
                dm.create_grant(db, name, database, 'admin')
                db.execute('''UPDATE effective_grants SET maximum_role='admin',epoch=epoch+1,
                    applied_revision=1,applied_generation_id=? WHERE principal_id=? AND database_id=?''',
                           ('g-' + database, name, database))
                proof_id = str(uuid.uuid4())
                dm.record_operation(db, dm.Operation(proof_id, 'stage-test-backend', 'WAREHOUSE_CONFIG_APPLY',
                    (), None, 'APPLIED', (), 'g-' + database, (), ()))
                db.execute('''INSERT INTO grant_audit
                    (operation_id,apply_operation_id,actor,principal_id,database_id,before_role,after_role,
                     effective_role,expected_revision,desired_revision,effective_epoch,generation_id,receipt)
                    VALUES (?,?,?, ?,?,'revoked','admin','admin',1,1,2,?,?)''',
                    (proof_id, proof_id, 'stage-test-backend', name, database,
                     'g-' + database, 'test:stage-fake-backend-admin'))
        return self.b.fixture.principal(name)

    def test_real_case_alias_claim_blocks_other_owner_database_and_recreation(self):
        parent = self.root / 'p44'
        owned = parent / 'api'
        owned.mkdir(parents=True)
        alias = parent / 'API'
        if not alias.is_dir() or not owned.samefile(alias):
            self.skipTest('temporary filesystem has distinct case-sensitive paths; deterministic alias test still runs')
        self.assertNotEqual(owned.resolve(), alias.resolve())
        nested = owned / 'nested'
        nested.mkdir()
        secret = owned / 'private.csv'
        secret.write_bytes(b'id,label\n7,owner-only\n')
        stage = self.save(definition=self.definition(directory=str(owned)))
        peer = self._claim_peer()
        candidates = (alias, self.root / 'P44', self.root / 'P44' / 'API' / 'nested')
        for database_index, database in enumerate(self.b.databases[:2]):
            context = self.b.context(database, principal=peer, role='admin')
            for index, candidate in enumerate(candidates):
                with self.subTest(database=database_index, candidate=str(candidate)):
                    definition = self.definition(name='peer-' + str(index), directory=str(candidate),
                        destination=asdict(self.b.locator('peer_target', database)))
                    self.code('STAGE_DIRECTORY_CLAIMED', lambda: stages.save_stage(peer, context,
                        key='alias-' + str(database_index) + '-' + str(index), expected_revision=0, definition=definition))
            with patch.object(stages, '_input', side_effect=AssertionError('another owner read protected bytes')):
                self.code('STAGE_NOT_FOUND', lambda: stages.list_files(peer, context, stage['id']))
            self.assertEqual(stages.list_stages(peer, context), [])
        self.assertEqual(stages.list_files(self.p, self.c, stage['id'])['files'][0]['name'], 'private.csv')
        # Recreating the spelling does not release the retained claim; on this
        # filesystem both old and new spellings now identify the new directory.
        secret.unlink()
        nested.rmdir()
        owned.rmdir()
        alias.mkdir()
        self.assertTrue(owned.samefile(alias))
        context = self.b.context(principal=peer, role='admin')
        self.code('STAGE_DIRECTORY_CLAIMED', lambda: stages.save_stage(peer, context,
            key='recreated-alias', expected_revision=0, definition=self.definition(name='recreated', directory=str(alias))))
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_stages').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_stage_claims').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_stages WHERE owner=?', (peer.principal_id,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dataops_queries').fetchone()[0], 0)
        self.assertFalse(self.b.calls)

    def test_real_distinct_case_directories_allowed_when_not_equivalent(self):
        upper, lower = self.root / 'Case', self.root / 'case'
        upper.mkdir()
        lower.mkdir(exist_ok=True)
        self.save(key='upper', definition=self.definition(name='upper', directory=str(upper)))
        if upper.samefile(lower):
            self.code('STAGE_DIRECTORY_CLAIMED', lambda: self.save(key='lower',
                definition=self.definition(name='lower', directory=str(lower))))
            expected = 1
        else:
            self.save(key='lower', definition=self.definition(name='lower', directory=str(lower)))
            expected = 2
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_stage_claims').fetchone()[0], expected)

    def test_case_equivalence_checks_only_leaves_against_other_ancestors(self):
        old_parent = self.root / 'Area'
        old = old_parent / 'Owned'
        alias_parent = self.root / 'area'
        alias = alias_parent / 'owned'
        identities = {old_parent: 'parent', alias_parent: 'parent', old: 'leaf', alias: 'leaf'}
        def samefile(leaf, other):
            return identities.get(leaf, leaf) == identities.get(other, other)
        with patch.object(Path, 'samefile', samefile):
            for candidate in (alias, alias_parent, alias / 'nested'):
                self.assertTrue(stages._directory_overlap(candidate, old), str(candidate))
            # These share equivalent parents, but neither leaf owns the other.
            self.assertFalse(stages._directory_overlap(alias_parent / 'Sibling', old))
            self.assertFalse(stages._directory_overlap(self.root / 'unrelated', old))
        with patch.object(Path, 'samefile', side_effect=FileNotFoundError('missing old path')):
            self.assertTrue(stages._directory_overlap(old_parent, old))
            self.assertTrue(stages._directory_overlap(old / 'nested', old))
            self.assertFalse(stages._directory_overlap(self.root / 'unrelated', old))

    def test_missing_historical_directory_keeps_literal_ownership(self):
        parent = self.root / 'retained'
        old = parent / 'leaf'
        old.mkdir(parents=True)
        stage = self.save(definition=self.definition(directory=str(old)))
        old.rmdir()
        self.assertFalse(old.exists())
        self.code('STAGE_DIRECTORY_CLAIMED', lambda: self.save(key='missing-ancestor',
            definition=self.definition(name='ancestor', directory=str(parent))))
        old.mkdir()
        child = old / 'child'
        child.mkdir()
        for index, candidate in enumerate((old, parent, child)):
            self.code('STAGE_DIRECTORY_CLAIMED', lambda: self.save(key='retained-' + str(index),
                definition=self.definition(name='retained-' + str(index), directory=str(candidate))))
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT stage_id FROM db_stage_claims').fetchone()[0], stage['id'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_stage_claims').fetchone()[0], 1)
        self.assertFalse(self.b.calls)

    def test_files_limits_ignored_entries_paths_and_hashes(self):
        stage = self.save()
        for name in ('.hidden.csv', 'data.csv.partial', 'data.csv.tmp', 'notes.md'):
            (self.directory / name).write_text('ignored')
        (self.directory / 'exports').mkdir()
        files = stages.list_files(self.p, self.c, stage['id'])['files']
        self.assertEqual([f['name'] for f in files], ['data.csv'])
        self.assertEqual(files[0]['version'], stages._hash(self.path.read_bytes()))
        with self.assertRaises(QueryError):
            stages.preview(self.p, self.c, stage['id'], key='traversal', expected_revision=1, file='../data.csv', version='x')
        for limit, value, code in (('file_bytes', 1, 'STAGE_FILE_TOO_LARGE'), ('entries', 1, 'STAGE_ENTRY_LIMIT'), ('hash_bytes', 1, 'STAGE_HASH_LIMIT')):
            with patch.dict(stages.LIMITS, {limit: value}):
                self.code(code, lambda: stages.list_files(self.p, self.c, stage['id']))

    def test_preview_mismatch_and_replay_before_missing_source(self):
        stage = self.save()
        preview, file = self.preview(stage)
        self.code('PREVIEW_CHANGED', lambda: self.submit(stage, expected_preview_fingerprint='0' * 64))
        run = self.submit(stage)
        self.path.unlink()
        self.assertEqual(stages.preview(self.p, self.c, stage['id'], key='preview', expected_revision=1,
            file=file['name'], version=file['version']), preview)
        args = dict(key='import', expected_revision=1, file=file['name'], version=file['version'],
                    expected_preview_fingerprint=preview['confirmation_fingerprint'])
        self.assertEqual(stages.import_file(self.p, self.c, stage['id'], **args)['id'], run['id'])
        self.code('IDEMPOTENCY_CONFLICT', lambda: stages.import_file(self.p, self.c, stage['id'], **dict(args, version='different')))
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM warehouse_admissions WHERE kind='dataops'").fetchone()[0], 1)
            captured = db.execute('SELECT input,operation_id FROM db_stage_runs').fetchone()
            self.assertEqual(captured['operation_id'], run['operation_id'])
            self.assertIn(b'001', captured['input'])

    def test_captured_input_is_immutable_after_admission_and_one_parent(self):
        run = self.submit()
        original_stage = stages.get_stage(self.p, self.c, run['stage_id'])
        self.assertNotIn('target_ref', run)
        self.assertEqual(run['destination_intent'], original_stage['definition']['destination'])
        original = stages._captured_input
        def change(*args):
            self.path.write_bytes(b'id,label\n999,replaced\n')
            return original(*args)
        with patch.object(stages, '_captured_input', change):
            result = self.execute(run)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertTrue(result['committed'])
        self.assertEqual(result['summary']['committed_rows'], 3)
        self.assertEqual(result['target_ref'], result['summary']['target'])
        self.assertEqual(result['target_ref'], result['operation']['result']['target'])
        self.assertEqual(set(result['target_ref']), {'database_id', 'schema_id', 'object_id', 'incarnation'})
        self.assertEqual(stages.get_stage(self.p, self.c, run['stage_id'])['definition'], original_stage['definition'])
        self.assertNotIn('selected_role', result)
        self.assertEqual(result['context'], asdict(self.c))
        self.assertNotIn('input', result)
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM warehouse_admissions WHERE kind='dataops'").fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_job_runs').fetchone()[0], 0)
            self.assertFalse(admission.blockers(db, 'w'))

    def test_file_change_in_admission_hook_rolls_back_all_intent(self):
        stage = self.save()
        original = ds.begin
        def changed(*args, **kwargs):
            self.path.write_bytes(b'id,label\n9,changed\n')
            return original(*args, **kwargs)
        with patch.object(ds, 'begin', changed):
            with self.assertRaises(QueryError) as caught:
                self.submit(stage)
            self.assertEqual(caught.exception.code, 'STAGE_INPUT_CHANGED')
            self.assertNotIn('admission', caught.exception.details)
        with control_store.connect() as db:
            for table in ('db_stage_runs', 'dataops_operations', 'warehouse_admissions'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)

    def test_target_recreation_and_revoked_minimal_cancel(self):
        target = self.b.existing()
        definition = self.definition(mode='append', target=asdict(target))
        definition.pop('destination')
        stage = self.save(definition=definition)
        run = self.submit(stage)
        self.assertEqual(run['target_ref'], asdict(target))
        self.assertNotIn('destination_intent', run)
        self.b.objects[('sales', 'raw', 'observed_data', 'tables')]['table-uuid'] = '00000000-0000-0000-0000-000000000000'
        result = self.execute(run)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['committed'])
        self.revoke()
        self.code('DB_ACCESS_REVOKED', lambda: stages.get_run(self.p, self.c, stage['id'], run['id']))
        observation = stages.cancel_run(self.p, self.c, stage['id'], run['id'], key='cancel')
        self.assertEqual(set(observation), {'id', 'stage_id', 'owner', 'status', 'operation_id'})

    def test_stage_cas_is_rechecked_inside_p3_admission(self):
        stage = self.save()
        original = ds.begin
        def change(*args, **kwargs):
            self.save(key='changed', stage_id=stage['id'], expected_revision=1,
                      definition=self.definition(name='renamed'))
            return original(*args, **kwargs)
        with patch.object(ds, 'begin', change):
            self.code('STAGE_REVISION_CONFLICT', lambda: self.submit(stage))
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dataops_operations').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM db_stage_runs').fetchone()[0], 0)

    def test_commit_survives_ingest_bookkeeping_failure(self):
        import manual_ingest
        run = self.submit()
        original = manual_ingest._persist
        def fail_after_commit(execution, receipt):
            if execution._row()['committed']:
                raise OSError('bookkeeping lost')
            return original(execution, receipt)
        with patch.object(manual_ingest, '_persist', fail_after_commit):
            result = self.execute(run)
        self.assertTrue(result['committed'], result)
        self.assertEqual(result['summary']['committed_rows'], 3)
        self.assertEqual(result['operation']['result']['committed_rows'], 3)

    def test_ingest_configuration_fingerprint_uses_effective_options_not_filename_or_display(self):
        import manual_ingest
        stage = self.save()
        first = self.submit(stage)
        raw = self.path.read_bytes()
        (self.directory / 'other.csv').write_bytes(raw)
        version = stages._hash(raw)
        def other_run(current, key):
            preview = stages.preview(self.p, self.c, current['id'], key='preview-' + key,
                expected_revision=current['revision'], file='other.csv', version=version)
            return stages.import_file(self.p, self.c, current['id'], key=key,
                expected_revision=current['revision'], file='other.csv', version=version,
                expected_preview_fingerprint=preview['confirmation_fingerprint'])
        renamed_file = other_run(stage, 'other-file')
        self.assertEqual(first['input_fingerprint'], renamed_file['input_fingerprint'])
        self.assertEqual(first['configuration_fingerprint'], renamed_file['configuration_fingerprint'])
        self.assertNotEqual(first['request_fingerprint'], renamed_file['request_fingerprint'])
        normalized = manual_ingest._preview_request(self.c, **stages._options(stage['definition'], 'data.csv', raw))[5]
        expected = {k: v for k, v in normalized.items() if k not in ('input_sha256', 'input_length')}
        self.assertEqual(first['configuration_fingerprint'], stages._hash(ds._json(expected).encode()))
        renamed = self.save(key='display-name', stage_id=stage['id'], expected_revision=1,
                            definition=self.definition(name='display_changed'))
        same = other_run(renamed, 'same-config')
        self.assertEqual(first['configuration_fingerprint'], same['configuration_fingerprint'])
        changed = self.save(key='batch-size', stage_id=stage['id'], expected_revision=2,
                            definition=self.definition(name='display_changed', batch_size=1))
        different = other_run(changed, 'different-config')
        self.assertEqual(first['input_fingerprint'], different['input_fingerprint'])
        self.assertNotEqual(first['configuration_fingerprint'], different['configuration_fingerprint'])
        with control_store.connect() as db:
            private = db.execute('SELECT definition,config_fingerprint FROM db_stage_runs WHERE id=?', (first['id'],)).fetchone()
        self.assertEqual(private['config_fingerprint'], stages._hash(private['definition'].encode()))

    def test_get_preserves_committed_receipt_while_operation_still_active(self):
        run = self.submit()
        original = ds.Execution.complete
        def during_cleanup(execution, *args, **kwargs):
            stale = dict(id=execution.id, state='ACTIVE', committed=False, result=None,
                         cleanup_error=None, queries=[], replayed=False)
            with patch.object(ds.Execution, 'inspect', return_value=stale):
                observation = stages.get_run(self.p, self.c, run['stage_id'], run['id'])
            self.assertEqual(observation['status'], 'running')
            self.assertTrue(observation['committed'])
            self.assertEqual(observation['operation']['state'], 'ACTIVE')
            self.assertTrue(observation['operation']['committed'])
            self.assertEqual(observation['summary']['committed_rows'], 3)
            self.assertEqual(observation['target_ref'], observation['summary']['target'])
            return original(execution, *args, **kwargs)
        with patch.object(ds.Execution, 'complete', during_cleanup):
            result = self.execute(run)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertTrue(result['committed'])


class StageExportTests(StageFixture, unittest.TestCase):

    def test_retiring_stage_export_continues_through_physical_file_publication(self):
        run = self.submit_export(format="jsonl", file="retiring.jsonl")
        self.f.after_count = self.b.retire
        stages._execute_run(run["id"], ds.Execution(self.p, run["operation_id"]))
        with control_store.connect() as db:
            record = json.loads(db.execute("SELECT record FROM db_stage_runs WHERE id=?",
                                           (run["id"],)).fetchone()[0])
        self.assertEqual(record["status"], "succeeded", record)
        self.assertTrue((self.directory / "exports" / "retiring.jsonl").is_file())
        self.b.assert_database_admissions()
        with self.assertRaises((QueryError, PermissionError)):
            stages.read_export(self.p, self.c, run["stage_id"], record["export_id"])

    def setUp(self):
        self.stage_setup(export_fixture.ManualExportTests())
        self.f.principal, self.f.context = self.p, self.c
        self.source = self.f.source

    def submit_export(self, stage=None, key='export', file='all.csv', format='csv', source=None):
        stage = stage or self.save()
        return stages.export_table(self.p, self.c, stage['id'], key=key, expected_revision=stage['revision'],
                                   source=source or self.source, format=format, file=file)

    def test_full_fixed_snapshot_receipt_and_admission_through_link(self):
        self.f.rows = [[str(i)] for i in range(2107)]
        original = stages._publish_link
        def link(temporary, destination):
            with control_store.connect() as db:
                self.assertEqual(db.execute('SELECT state FROM dataops_operations').fetchone()[0], 'ACTIVE')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM db_stage_publications').fetchone()[0], 1)
                self.assertTrue(admission.blockers(db, 'w'))
            return original(temporary, destination)
        def append():
            self.f.metadata['current-snapshot-id'] = 102
            self.f.metadata['snapshots'].append({'snapshot-id': 102, 'schema-id': 1})
        self.f.after_count = append
        run = self.submit_export()
        with patch.object(stages, '_publish_link', link):
            result = self.execute(run)
        self.assertEqual(result['status'], 'succeeded', result)
        artifact = stages.read_export(self.p, self.c, run['stage_id'], result['export_id'])
        receipt = artifact['receipt']
        self.assertEqual(receipt['source']['snapshot'], '101')
        self.assertEqual(receipt['expected_count'], 2107)
        self.assertEqual(receipt['written_count'], 2107)
        self.assertNotIn('row_count', receipt)
        self.assertEqual(len(artifact['content'].splitlines()), 2108)
        self.assertEqual(len(self.f.posts), 2)
        with control_store.connect() as db:
            self.assertFalse(admission.blockers(db, 'w'))
        self.assertEqual(list((self.directory / 'exports').iterdir()), [self.directory / 'exports' / 'all.csv'])

    def test_complete_artifact_private_seam_public_p3_still_requires_finished(self):
        run = self.submit_export()
        original = stages._publish
        def publish(run_id, execution, export_id):
            self.code('EXPORT_NOT_COMPLETE', lambda: manual_export.read_export(self.p, self.c, export_id=export_id))
            self.assertTrue(manual_export._read_complete(self.p, self.c, export_id=export_id, _execution=execution)['content'])
            return original(run_id, execution, export_id)
        with patch.object(stages, '_publish', publish):
            self.assertEqual(self.execute(run)['status'], 'succeeded')

    def test_export_history_skips_expired_snapshot_without_changing_artifacts(self):
        stage = self.save()
        expiring = self.b.existing(name='expiring_source')
        metadata = self.b.objects[('sales', 'raw', 'expiring_source', 'tables')]
        metadata.update({k: json.loads(json.dumps(v)) for k, v in self.f.metadata.items()
                         if k != 'table-uuid'})
        results = [self.execute(self.submit_export(stage, key=name, file=name + '.csv', source=source))
                   for name, source in [('good', self.source), ('expired', expiring), ('latest', self.source)]]
        for result in results:
            self.assertEqual(result['status'], 'succeeded', result)
        receipts = [stages.get_export(self.p, self.c, stage['id'], r['export_id']) for r in results]
        paths = [self.directory / 'exports' / r['file'] for r in receipts]
        paths += [manual_export._directory() / (r['export_id'] + '.' + r['format']) for r in receipts]
        before_bytes = {path: path.read_bytes() for path in paths}
        with control_store.connect() as db:
            before_claims = [tuple(r) for r in db.execute('SELECT * FROM db_stage_publications ORDER BY rowid')]
        metadata['snapshots'] = []
        original_measure = ds.Execution.export_measure
        measured = []
        def measure(execution, ref, captured=None):
            measured.append((execution.id, ref, captured['snapshot']))
            return original_measure(execution, ref, captured)
        posts = list(self.f.posts)
        with patch.object(ds.Execution, 'export_measure', measure):
            history = stages.list_exports(self.p, self.c, stage['id'])
        self.assertEqual(history, [receipts[2], receipts[0]])
        self.assertEqual(list(dict.fromkeys(measured)), [
            (results[2]['operation_id'], self.source, '101'),
            (results[1]['operation_id'], expiring, '101'),
            (results[0]['operation_id'], self.source, '101')])
        for read in (stages.get_export, stages.read_export):
            self.code('EXPORT_SNAPSHOT_UNAVAILABLE',
                      lambda: read(self.p, self.c, stage['id'], results[1]['export_id']))
        with control_store.connect() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM db_stage_publications ORDER BY rowid')],
                             before_claims)
        self.assertEqual({path: path.read_bytes() for path in paths}, before_bytes)
        self.assertEqual(self.f.posts, posts)

    def test_export_history_revalidates_grant_after_protected_receipt(self):
        result = self.execute(self.submit_export())
        self.assertEqual(result['status'], 'succeeded', result)
        original_get = stages.get_export
        fetched = []
        def get_then_revoke(*args):
            receipt = original_get(*args)
            fetched.append(receipt)
            self.revoke()
            return receipt
        with patch.object(stages, 'get_export', get_then_revoke):
            self.code('DB_ACCESS_REVOKED', lambda: stages.list_exports(self.p, self.c, result['stage_id']))
        self.assertEqual([r['export_id'] for r in fetched], [result['export_id']])

    def test_export_history_revalidates_context_after_protected_receipt(self):
        result = self.execute(self.submit_export())
        self.assertEqual(result['status'], 'succeeded', result)
        original_get = stages.get_export
        fetched = []
        def get_then_change_binding(*args):
            receipt = original_get(*args)
            fetched.append(receipt)
            with control_store.connect() as db:
                db.execute('UPDATE catalog_bindings SET desired_revision=desired_revision+1 WHERE database_id=?',
                           (self.c.database_id,))
            return receipt
        with patch.object(stages, 'get_export', get_then_change_binding):
            self.code('STALE_BINDING', lambda: stages.list_exports(self.p, self.c, result['stage_id']))
        self.assertEqual([r['export_id'] for r in fetched], [result['export_id']])

    def test_export_history_propagates_unexpected_source_validation_failure(self):
        result = self.execute(self.submit_export())
        self.assertEqual(result['status'], 'succeeded', result)
        with patch.object(ds.Execution, 'export_measure', side_effect=RuntimeError('unexpected measurement failure')):
            with self.assertRaisesRegex(RuntimeError, 'unexpected measurement failure'):
                stages.list_exports(self.p, self.c, result['stage_id'])
        for status in (400, 500, 503):
            with self.subTest(status=status):
                error = QueryError('UNEXPECTED_MEASUREMENT_FAILURE', status)
                with patch.object(ds.Execution, 'export_measure', side_effect=error):
                    with self.assertRaises(QueryError) as raised:
                        stages.list_exports(self.p, self.c, result['stage_id'])
                self.assertIs(raised.exception, error)

    def test_no_overwrite_and_durable_reservation_precedes_sql(self):
        stage = self.save()
        output = self.directory / 'exports'
        output.mkdir()
        (output / 'all.csv').write_bytes(b'prior output')
        run = self.submit_export(stage)
        self.code('STAGE_EXPORT_RESERVED', lambda: self.submit_export(stage, key='another'))
        self.assertEqual(self.f.posts, [])
        result = self.execute(run)
        self.assertEqual(result['status'], 'failed')
        with control_store.connect() as db:
            self.assertFalse(admission.blockers(db, 'w'))
        self.assertEqual((output / 'all.csv').read_bytes(), b'prior output')
        self.assertEqual(len(self.f.posts), 2)
        self.assertEqual(len(list(output.iterdir())), 1)

    def test_linked_ledger_failure_preserves_output_unknown_claim_and_observer_no_sql(self):
        run = self.submit_export()
        with patch.object(stages, '_publication_ledger', side_effect=OSError('ledger lost')):
            stages._execute_run(run['id'], ds.Execution(self.p, run['operation_id']))
        output = self.directory / 'exports' / 'all.csv'
        self.assertTrue(output.is_file())
        before = len(self.f.posts)
        result = stages.get_run(self.p, self.c, run['stage_id'], run['id'])
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['publication_state'], 'published')
        self.assertEqual(result['operation']['state'], 'FINISHED')
        self.assertEqual(len(self.f.posts), 2)
        self.assertEqual(stages.read_export(self.p, self.c, run['stage_id'], result['export_id'])['content'], output.read_bytes())
        with control_store.connect() as db:
            self.assertFalse(admission.blockers(db, 'w'))
            self.assertTrue(db.execute("SELECT evidence_ref FROM warehouse_admissions WHERE kind='dataops'").fetchone()[0])
        self.assertEqual(len(self.f.posts), before)
        replay = self.submit_export(stages.get_stage(self.p, self.c, run['stage_id']))
        self.assertEqual(replay['id'], run['id'])
        self.assertEqual(len(self.f.posts), before)
        self.code('STAGE_EXPORT_RESERVED', lambda: self.submit_export(stages.get_stage(self.p, self.c, run['stage_id']), key='retry'))
        self.assertTrue(output.exists())

    def test_late_unknown_after_positive_reconcile_preserves_all_terminal_states(self):
        run = self.submit_export()
        execution = ds.Execution(self.p, run['operation_id'])
        stale_active = execution._row()
        with patch.object(stages, '_publication_ledger', side_effect=OSError('ledger lost')):
            stages._execute_run(run['id'], execution)
        with control_store.connect() as db:
            stale_run = json.loads(db.execute('SELECT record FROM db_stage_runs WHERE id=?', (run['id'],)).fetchone()[0])
        with patch.object(stages, '_publish_link') as link:
            recovered = stages.get_run(self.p, self.c, run['stage_id'], run['id'])
            with patch.object(execution, '_row', return_value=stale_active) as old_read, \
                    patch.object(admission, 'finish') as finish:
                stages._unknown(run['id'], execution, OSError('late worker notification'))
            old_read.assert_not_called()
            finish.assert_not_called()
            with patch.object(stages, '_publication_ledger') as ledger, patch.object(admission, 'finish') as finish:
                stages._observe_publication(self.p, self.c, stale_run)
                stages._observe_publication(self.p, self.c, stale_run)
            ledger.assert_not_called()
            finish.assert_not_called()
            for _ in range(2):
                again = stages.get_run(self.p, self.c, run['stage_id'], run['id'])
                self.assertEqual((again['status'], again['operation']['state']), ('succeeded', 'FINISHED'))
                self.assertEqual(again['summary'], recovered['summary'])
            self.assertTrue(stages.read_export(self.p, self.c, run['stage_id'], recovered['export_id'])['content'])
            link.assert_not_called()
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM dataops_operations').fetchone()[0], 'FINISHED')
            self.assertEqual(db.execute("SELECT state FROM warehouse_admissions WHERE kind='dataops'").fetchone()[0], 'FINISHED')
            self.assertFalse(admission.blockers(db, 'w'))
        self.assertEqual(len(self.f.posts), 2)

    def test_stale_get_snapshot_cannot_overwrite_reconciled_stage_status(self):
        run = self.submit_export()
        with patch.object(stages, '_publication_ledger', side_effect=OSError('ledger lost')):
            stages._execute_run(run['id'], ds.Execution(self.p, run['operation_id']))
        with control_store.connect() as db:
            stages._write_run(db, run['id'], status='running')
        original_inspect, original_observe = ds.Execution.inspect, stages._observe_publication
        entered, completed = False, False
        def inspect(execution):
            nonlocal entered, completed
            snapshot = original_inspect(execution)
            if not entered:
                entered = True
                inner = stages.get_run(self.p, self.c, run['stage_id'], run['id'])
                self.assertEqual((inner['status'], inner['operation']['state']), ('succeeded', 'FINISHED'))
                completed = True
            return snapshot
        def observe(*args):
            self.assertFalse(completed, 'stale GET regressed a terminal Stage and attempted recovery again')
            return original_observe(*args)
        with patch.object(ds.Execution, 'inspect', inspect), patch.object(stages, '_observe_publication', observe):
            result = stages.get_run(self.p, self.c, run['stage_id'], run['id'])
        self.assertEqual((result['status'], result['operation']['state']), ('succeeded', 'FINISHED'))
        self.assertEqual(len(self.f.posts), 2)

    def test_observer_terminal_transaction_does_not_refinish_competing_positive_result(self):
        run = self.submit_export()
        with patch.object(stages, '_publication_ledger', side_effect=OSError('ledger lost')):
            stages._execute_run(run['id'], ds.Execution(self.p, run['operation_id']))
        original_fsync = stages._fsync_directory
        def competing_positive(directory):
            original_fsync(directory)
            # Simulate another authorized observer committing after this GET's
            # initial proof and before its final BEGIN IMMEDIATE.
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                receipt = json.loads(db.execute('SELECT receipt FROM db_stage_publications WHERE run_id=?', (run['id'],)).fetchone()[0])
                stages._publication_ledger(db, run['id'], receipt)
                parent = admission.get_for_subject(db, kind='dataops', subject_id=run['operation_id'], warehouse_id='w')
                admission.finish(db, parent, state='FINISHED', evidence_ref='test:matching-positive-observer')
                db.execute("UPDATE dataops_operations SET state='FINISHED' WHERE id=?", (run['operation_id'],))
        with patch.object(stages, '_fsync_directory', competing_positive), \
                patch.object(stages, '_publication_ledger', wraps=stages._publication_ledger) as ledger, \
                patch.object(admission, 'finish', wraps=admission.finish) as finish:
            result = stages.get_run(self.p, self.c, run['stage_id'], run['id'])
        self.assertEqual((result['status'], result['operation']['state']), ('succeeded', 'FINISHED'))
        self.assertEqual(ledger.call_count, 1)
        self.assertEqual(finish.call_count, 1)
        self.assertEqual(len(self.f.posts), 2)
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT evidence_ref FROM warehouse_admissions WHERE kind='dataops'").fetchone()[0], 'test:matching-positive-observer')

    def test_revoke_commits_before_publication_transaction_prevents_link(self):
        run = self.submit_export()
        original = manual_export._read_complete
        def revoked(*args, **kwargs):
            result = original(*args, **kwargs)
            self.revoke()
            return result
        with patch.object(manual_export, '_read_complete', revoked), patch.object(stages, '_publish_link') as link:
            stages._execute_run(run['id'], ds.Execution(self.p, run['operation_id']))
        link.assert_not_called()
        self.assertFalse((self.directory / 'exports' / 'all.csv').exists())
        result = stages.cancel_run(self.p, self.c, run['stage_id'], run['id'], key='cancel')
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('summary', result)
        with control_store.connect() as db:
            self.assertFalse(admission.blockers(db, 'w'))

    def test_view_rejected_and_download_rechecks_source_then_grant(self):
        view = self.b.existing(name='v', kind='VIEW')
        run = self.submit_export(source=view)
        self.assertEqual(self.execute(run)['status'], 'failed')
        self.assertEqual(self.f.posts, [])
        stage = stages.get_stage(self.p, self.c, run['stage_id'])
        good = self.execute(self.submit_export(stage, key='good', file='good.csv'))
        stages.read_export(self.p, self.c, stage['id'], good['export_id'])
        self.f.metadata['table-uuid'] = '00000000-0000-0000-0000-000000000000'
        self.code('STALE_RESOURCE', lambda: stages.read_export(self.p, self.c, stage['id'], good['export_id']))
        self.revoke()
        self.code('DB_ACCESS_REVOKED', lambda: stages.read_export(self.p, self.c, stage['id'], good['export_id']))

    def test_revoke_is_serialized_with_link_and_terminal_ledger(self):
        run = self.submit_export()
        attempted, done = threading.Event(), threading.Event()
        failures, threads = [], []
        original_link, original_ledger = stages._publish_link, stages._publication_ledger
        def revoke():
            try:
                with control_store.connect() as db:
                    attempted.set()
                    db.execute('BEGIN IMMEDIATE')
                    db.execute("DELETE FROM grants WHERE principal_id='owner' AND database_id=?", (self.c.database_id,))
            except Exception as error:
                failures.append(error)
            finally:
                done.set()
        def link(temporary, destination):
            thread = threading.Thread(target=revoke)
            threads.append(thread)
            thread.start()
            self.assertTrue(attempted.wait(2))
            self.assertFalse(done.wait(.05), 'revoke slipped into publication transaction')
            return original_link(temporary, destination)
        def ledger(*args):
            self.assertFalse(done.is_set())
            return original_ledger(*args)
        with patch.object(stages, '_publish_link', link), patch.object(stages, '_publication_ledger', ledger):
            stages._execute_run(run['id'], ds.Execution(self.p, run['operation_id']))
        for thread in threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertFalse(failures)
        self.assertTrue(done.is_set())
        self.assertTrue((self.directory / 'exports' / 'all.csv').is_file())
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM dataops_operations').fetchone()[0], 'FINISHED')
            self.assertEqual(db.execute('SELECT state FROM db_stage_publications').fetchone()[0], 'published')
        self.code('DB_ACCESS_REVOKED', lambda: stages.read_export(self.p, self.c, run['stage_id'],
            json.loads(ds.Execution(self.p, run['operation_id'])._row()['result'])['export_id']))

    def test_observer_missing_unknown_or_mismatching_evidence_keeps_unknown(self):
        stage = self.save()
        variants = ('unknown-child', 'missing-execution', 'missing-query', 'missing-link',
                    'orphan-child', 'mismatched-artifact', 'missing-file', 'changed-file', 'missing-p3-file')
        for index, variant in enumerate(variants):
            with self.subTest(variant=variant):
                run = self.submit_export(stage, key='negative-' + str(index), file='negative-' + str(index) + '.csv')
                with patch.object(stages, '_publication_ledger', side_effect=OSError('lost ledger')):
                    stages._execute_run(run['id'], ds.Execution(self.p, run['operation_id']))
                with control_store.connect() as db:
                    record = json.loads(db.execute('SELECT record FROM db_stage_runs WHERE id=?', (run['id'],)).fetchone()[0])
                    child = db.execute("SELECT query_id FROM dataops_queries WHERE operation_id=? AND step='count'", (run['operation_id'],)).fetchone()[0]
                    artifact = db.execute('SELECT * FROM export_artifacts WHERE operation_id=?', (run['operation_id'],)).fetchone()
                    if variant == 'unknown-child':
                        db.execute("UPDATE query_records SET state='UNKNOWN' WHERE id=?", (child,))
                        db.execute("UPDATE warehouse_admissions SET state='UNKNOWN' WHERE kind='query' AND subject_id=?", (child,))
                    elif variant == 'missing-execution':
                        db.execute('DELETE FROM query_execution_contexts WHERE query_id=?', (child,))
                    elif variant == 'missing-query':
                        db.execute('PRAGMA foreign_keys=OFF')
                        db.execute('DELETE FROM query_records WHERE id=?', (child,))
                    elif variant == 'missing-link':
                        db.execute('DELETE FROM dataops_queries WHERE query_id=?', (child,))
                    elif variant == 'orphan-child':
                        parent = admission.get_for_subject(db, kind='dataops', subject_id=run['operation_id'], warehouse_id='w')
                        db.execute('''INSERT INTO warehouse_admissions
                            (id,warehouse_id,actor,kind,subject_id,parent_id,generation_id,state,created_at)
                            VALUES (?,'w','owner','query',?,?,?,'FINISHED',?)''',
                            (str(uuid.uuid4()), str(uuid.uuid4()), parent.id, self.c.generation_id, admission._now()))
                    elif variant == 'mismatched-artifact':
                        altered = dict(json.loads(artifact['record']), sha256='0' * 64)
                        db.execute('UPDATE export_artifacts SET record=? WHERE export_id=?', (ds._json(altered), artifact['export_id']))
                output = self.directory / 'exports' / run['file']
                if variant == 'missing-file':
                    output.unlink()
                elif variant == 'changed-file':
                    output.write_bytes(b'changed output')
                elif variant == 'missing-p3-file':
                    (manual_export._directory() / artifact['relative_name']).unlink()
                before = len(self.f.posts)
                result = stages.get_run(self.p, self.c, run['stage_id'], run['id'])
                self.assertEqual(result['status'], 'unknown', result)
                self.assertEqual(result['operation']['state'], 'UNKNOWN')
                self.assertEqual(len(self.f.posts), before)
                self.assertEqual(record['context'], result['context'])
                with control_store.connect() as db:
                    self.assertEqual(db.execute('SELECT state FROM db_stage_publications WHERE run_id=?', (run['id'],)).fetchone()[0], 'intent')

    def test_post_link_directory_fsync_loss_is_unknown_then_positive_recovery(self):
        run = self.submit_export()
        with patch.object(stages, '_fsync_directory', side_effect=OSError('directory fsync lost')):
            stages._execute_run(run['id'], ds.Execution(self.p, run['operation_id']))
        self.assertTrue((self.directory / 'exports' / run['file']).exists())
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM dataops_operations').fetchone()[0], 'UNKNOWN')
            self.assertEqual(db.execute('SELECT state FROM db_stage_publications').fetchone()[0], 'intent')
        self.assertEqual(self.execute_observation(run)['status'], 'succeeded')
        self.assertEqual(len(self.f.posts), 2)
        with control_store.connect() as db:
            self.assertFalse(admission.blockers(db, 'w'))

    def execute_observation(self, run):
        return stages.get_run(self.p, self.c, run['stage_id'], run['id'])

    def test_export_cas_edit_after_artifact_is_definite_failed_without_link(self):
        stage = self.save()
        run = self.submit_export(stage)
        original = manual_export._read_complete
        def edit(*args, **kwargs):
            result = original(*args, **kwargs)
            self.save(key='edit-export', stage_id=stage['id'], expected_revision=1, definition=self.definition(name='edited'))
            return result
        with patch.object(manual_export, '_read_complete', edit), patch.object(stages, '_publish_link') as link:
            result = self.execute(run)
        self.assertEqual(result['status'], 'failed', result)
        link.assert_not_called()
        self.assertFalse((self.directory / 'exports' / run['file']).exists())
        self.assertEqual(len(self.f.posts), 2)
        with control_store.connect() as db:
            self.assertFalse(admission.blockers(db, 'w'))

    def test_same_database_other_schema_export_and_cross_database_rejection(self):
        source = self.b.existing('other_schema_source', schema='sandbox')
        metadata = self.b.objects[('sales', 'sandbox', 'other_schema_source', 'tables')]
        metadata.update({k: v for k, v in self.f.metadata.items() if k != 'table-uuid'})
        stage = self.save()
        result = self.execute(self.submit_export(stage, source=source))
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(stages.get_export(self.p, self.c, stage['id'], result['export_id'])['source']['ref'], asdict(source))
        other = self.b.existing(database=self.b.databases[1])
        before = len(self.f.posts)
        self.code('CROSS_DATABASE_TARGET', lambda: self.submit_export(stage, key='cross', source=other, file='cross.csv'))
        self.assertEqual(len(self.f.posts), before)

    def test_fresh_epoch_lists_filter_old_receipts_and_preserve_old_bytes(self):
        stage = self.save()
        old = self.execute(self.submit_export(stage))
        old_context = self.c
        output = self.directory / 'exports' / old['file']
        prior = output.read_bytes()
        self.revoke()
        self.regrant(new_generation=True)
        self.f.context = self.c
        self.assertEqual(stages.list_runs(self.p, self.c, stage['id']), [])
        self.assertEqual(stages.list_exports(self.p, self.c, stage['id']), [])
        for action in (stages.get_export, stages.read_export):
            self.code('CONTEXT_MISMATCH', lambda: action(self.p, self.c, stage['id'], old['export_id']))
            self.code('CONTEXT_MISMATCH', lambda: action(self.p, old_context, stage['id'], old['export_id']))
        fresh = self.execute(self.submit_export(stage, key='fresh-epoch', file='fresh.csv'))
        self.assertEqual(fresh['status'], 'succeeded', fresh)
        self.assertEqual([r['export_id'] for r in stages.list_exports(self.p, self.c, stage['id'])], [fresh['export_id']])
        self.assertEqual([r['id'] for r in stages.list_runs(self.p, self.c, stage['id'])], [fresh['id']])
        self.assertEqual(output.read_bytes(), prior)

    def test_observer_rechecks_cas_root_and_accepted_epoch_without_refresh(self):
        stage = self.save()
        original_fsync = stages._fsync_directory
        for variant in ('cas', 'root', 'epoch'):
            with self.subTest(variant=variant):
                stage = stages.get_stage(self.p, self.c, stage['id'])
                run = self.submit_export(stage, key='late-' + variant, file='late-' + variant + '.csv')
                with patch.object(stages, '_publication_ledger', side_effect=OSError('lost ledger')):
                    stages._execute_run(run['id'], ds.Execution(self.p, run['operation_id']))
                before = len(self.f.posts)
                if variant == 'epoch':
                    original_context = self.c
                    self.revoke()
                    self.regrant()
                    self.code('CONTEXT_MISMATCH', lambda: stages.get_run(self.p, self.c, run['stage_id'], run['id']))
                    self.code('CONTEXT_MISMATCH', lambda: stages.get_run(self.p, original_context, run['stage_id'], run['id']))
                else:
                    root_patch = None
                    def change(directory):
                        nonlocal root_patch
                        original_fsync(directory)
                        if variant == 'cas':
                            self.save(key='late-cas-edit', stage_id=stage['id'], expected_revision=stage['revision'],
                                      definition=self.definition(name='after-proof'))
                        else:
                            other = self.root / 'other-root'
                            other.mkdir()
                            root_patch = patch.dict(os.environ, SHIROKUMA_STAGE_ROOTS=json.dumps([str(other)]))
                            root_patch.start()
                    try:
                        with patch.object(stages, '_fsync_directory', change):
                            result = self.execute_observation(run)
                        self.assertEqual(result['status'], 'unknown')
                    finally:
                        if root_patch is not None:
                            root_patch.stop()
                self.assertEqual(len(self.f.posts), before)
                with control_store.connect() as db:
                    self.assertEqual(db.execute('SELECT state FROM dataops_operations WHERE id=?', (run['operation_id'],)).fetchone()[0], 'UNKNOWN')
                    self.assertEqual(db.execute('SELECT state FROM db_stage_publications WHERE run_id=?', (run['id'],)).fetchone()[0], 'intent')


if __name__ == '__main__':
    unittest.main()
