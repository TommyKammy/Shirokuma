"""Manual WAP through real P3-1 admission/intents, with an in-memory SQL backend.

The fake transport evaluates read projections with SQLite; it is not evidence
of Trino/Polaris acceptance. Parent/child persistence and authorization are real.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store
import data_management as dm
import dataops_service as ds
import manual_wap as wap
import query_service as qs
from query_context import QueryError
import sqlglot
from sqlglot import exp
import test_dataops_service as boundary_tests


class ManualWapTests(unittest.TestCase):

    def test_retirement_after_begin_keeps_manual_build_continuation_private(self):
        source = self.existing()
        model = wap.save_model(self.principal, self.context, model_id='retiring_model',
            sql='SELECT id, label FROM source', sources={'source': source},
            target=self.b.locator('retiring_target', schema='marts'), expected_version=None)
        begin = ds.begin
        def retire_after_begin(*args, **kwargs):
            execution = begin(*args, **kwargs)
            self.b.retire()
            return execution
        with patch.object(ds, 'begin', side_effect=retire_after_begin):
            with self.assertRaises((QueryError, PermissionError)):
                wap.build(self.principal, self.context, key='retiring-build',
                          model_id=model['model_id'], version=model['version'])
        with control_store.connect() as db:
            operation_id = db.execute('SELECT id FROM dataops_operations').fetchone()[0]
        operation = ds.Execution(self.principal, operation_id)._inspect()
        self.assertEqual(operation['state'], 'FINISHED')
        self.b.assert_database_admissions()
        with self.assertRaises((QueryError, PermissionError)):
            wap.get_build(self.principal, self.context, build_id=operation['result']['build_id'])

    def test_save_revalidates_retirement_inside_writer_transaction(self):
        source = self.existing()
        target = self.b.locator('retiring_save', schema='marts')
        metadata = wap._access(self.principal, self.context, write=True)
        original = metadata._context
        claim = wap._claim_model
        armed = []
        def claim_and_arm(*args):
            try:
                return claim(*args)
            finally:
                armed.append(True)
        def retire_after_check():
            result = original()
            if armed:
                self.b.retire()
            return result
        with patch.object(wap, '_access', return_value=metadata), \
                patch.object(metadata, '_context', side_effect=retire_after_check), \
                patch.object(wap, '_claim_model', side_effect=claim_and_arm):
            with self.assertRaises(QueryError):
                wap.save_model(self.principal, self.context, model_id='retiring_save',
                    sql='SELECT id FROM source', sources={'source': source}, target=target, expected_version=None)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM wap_models').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM wap_model_versions').fetchone()[0], 0)

    def setUp(self):
        self.b = boundary_tests.DataOpsTests()
        self.b.setUp()
        self.addCleanup(self.b.doCleanups)
        self.principal, self.context = self.b.principal, self.b.context()
        self.owner = self.b.fixture.principal('owner')
        self.admin = self.b.context(principal=self.owner, role='admin')
        self.posts, self.snapshots, self.sequence = [], {}, 100
        transport = patch.object(qs, '_transport', side_effect=self.transport)
        transport.start()
        self.addCleanup(transport.stop)

    def metadata(self, ref):
        with control_store.connect() as db:
            obj = dm.get_object(db, ref.object_id)
            schema = dm.get_schema(db, ref.schema_id)
            database = dm.get_database(db, ref.database_id)
        return self.b.objects[(database.sql_catalog, schema.sql_name, obj.physical_name, 'tables')]

    def snapshot(self, metadata, rows):
        self.sequence += 1
        metadata['current-snapshot-id'] = self.sequence
        metadata.setdefault('snapshots', []).append({'snapshot-id': self.sequence})
        self.snapshots[(metadata['table-uuid'], self.sequence)] = rows

    def existing(self, name='input_data', database=None, schema='raw', rows=None):
        ref = self.b.existing(name, database, schema)
        metadata = self.metadata(ref)
        metadata.update({'current-schema-id': 0, 'schemas': [{'schema-id': 0, 'fields': [
            {'id': 1, 'name': 'id', 'type': 'long', 'required': False},
            {'id': 2, 'name': 'label', 'type': 'string', 'required': False}]}]})
        self.snapshot(metadata, rows if rows is not None else [(1, 'one'), (2, 'two'), (3, None)])
        return ref

    def table_metadata(self, table):
        return self.b.objects[(table.catalog, table.db, table.name, 'tables')]

    def evaluate(self, select):
        tree = select.copy()
        from sqlglot.optimizer.scope import traverse_scope
        tables = [source for scope in traverse_scope(tree)
                  for _, source in scope.selected_sources.values() if isinstance(source, exp.Table)]
        with sqlite3.connect(':memory:') as db:
            for index, table in enumerate(tables):
                metadata = self.table_metadata(table)
                version = table.args.get('version')
                snapshot = int(version.expression.this) if version else metadata['current-snapshot-id']
                rows = self.snapshots[(metadata['table-uuid'], snapshot)]
                fields = next(s['fields'] for s in metadata['schemas'] if s['schema-id'] == metadata['current-schema-id'])
                name = 'input_' + str(index)
                db.execute('CREATE TABLE ' + name + ' (' + ', '.join(ds._column(f['name']) for f in fields) + ')')
                db.executemany('INSERT INTO ' + name + ' VALUES (' + ','.join('?' for _ in fields) + ')', rows)
                table.replace(exp.Table(this=exp.Identifier(this=name), alias=table.args.get('alias')))
            cursor = db.execute(tree.sql(dialect='sqlite'))
            rows = cursor.fetchall()
            names = [c[0] for c in cursor.description]
        return rows, names

    def transport(self, url, method, pin, body=None):
        if method != 'POST':
            raise AssertionError('unexpected pagination')
        sql = body.decode()
        self.posts.append((sql, dict(pin.headers)))
        with control_store.connect() as db:
            intent = db.execute('''SELECT d.operation_id FROM dataops_queries d
                JOIN query_execution_contexts q ON q.query_id=d.query_id WHERE q.sql=?''', (sql,)).fetchone()
            self.assertIsNotNone(intent, 'intent must precede every POST')
        tree = None if sql.startswith('ALTER TABLE ') else sqlglot.parse_one(sql, read='trino')
        data = []
        if isinstance(tree, exp.Create):
            table = tree.this
            rows, names = self.evaluate(tree.expression)
            key = (table.catalog, table.db, table.name, 'tables')
            metadata = self.b.objects.get(key, {'table-uuid': str(uuid.uuid4()), 'snapshots': []})
            metadata.update({'current-schema-id': 0, 'schemas': [{'schema-id': 0, 'fields': [
                dict(id=i + 1, name=n, type='long' if n == 'id' else 'string', required=False)
                for i, n in enumerate(names)]}]})
            self.snapshot(metadata, rows)
            self.b.objects[key] = metadata
        elif sql.startswith('ALTER TABLE '):
            names = re.findall(r'"([^"]+)"', sql)[:3]
            metadata = self.b.objects[(*names, 'tables')]
            metadata['current-snapshot-id'] = int(re.search(r'rollback_to_snapshot\(([0-9]+)\)', sql)[1])
        elif isinstance(tree, exp.Query):
            data, _ = self.evaluate(tree)
        else:
            raise AssertionError(sql)
        return {'id': 'backend_' + str(len(self.posts)), 'data': [list(r) for r in data]}

    def save(self, *, database=None, target=None, model_id='shared', sql='SELECT id, label FROM source',
             expected_version=None, source=None, **kwargs):
        context = self.b.context(database) if database else self.context
        source = source or self.existing(database=database)
        target = target or self.b.locator('published_data', database, 'marts')
        return wap.save_model(self.principal, context, model_id=model_id, sql=sql,
            sources={'source': source}, target=target, expected_version=expected_version, **kwargs)

    def built(self, *, database=None, key='build', **kwargs):
        model = self.save(database=database, **kwargs)
        context = self.b.context(database) if database else self.context
        receipt = wap.build(self.principal, context, key=key, model_id=model['model_id'], version=model['version'])
        self.assertEqual(receipt['state'], 'FINISHED', receipt)
        return receipt['result']['build']

    def audited(self, **kwargs):
        built = self.built(**kwargs)
        receipt = wap.audit(self.principal, self.context, key='audit', build_id=built['id'])
        return built, receipt['result']['audit']

    def publish(self, built, audit, key='publish'):
        receipt = wap.publish(self.owner, self.admin, key=key, build_id=built['id'], audit_id=audit['id'])
        self.assertEqual(receipt['state'], 'FINISHED', receipt)
        return receipt['result']['publication']

    def test_model_current_binding_read_preserves_immutable_json_and_old_version(self):
        from email.message import Message
        from types import SimpleNamespace
        from unittest.mock import Mock
        import dataops_routes

        built, audited = self.audited()
        def stored():
            with control_store.connect() as db:
                return db.execute('SELECT record FROM wap_model_versions WHERE database_id=? AND model_id=? AND version=1',
                    (self.context.database_id, built['model_id'])).fetchone()[0]
        before = stored()
        original = json.loads(before)
        self.assertIsNone(wap.get_model(self.principal, self.context,
            model_id=built['model_id'])['current_target_ref'])
        publication = self.publish(built, audited)
        current = publication['target']['ref']
        count = len(self.posts)
        loaded = wap.get_model(self.principal, self.context, model_id=built['model_id'])
        self.assertEqual(loaded, dict(original, current_target_ref=current))
        newer = self.save(target=dm.ResourceRef(**current), expected_version=1,
            source=dm.ResourceRef(**original['sources']['source']), sql='SELECT label, id FROM source')
        self.assertEqual(newer['version'], 2)
        self.assertEqual(wap.get_model(self.principal, self.context, model_id=built['model_id'], version=1), loaded)
        self.assertEqual(wap.get_model(self.principal, self.context, model_id=built['model_id'])['version'], 2)
        for selection in ({'model_id': [built['model_id']], 'version': ['1']}, {}):
            handler = SimpleNamespace(path='/api/v1/wap/models', headers=Message(), respond=Mock())
            dataops_routes.dispatch(handler, 'GET', handler.path,
                {'context': [json.dumps(asdict(self.context))], **selection}, self.principal, self.context.selected_role)
            reply = handler.respond.call_args.args[0]
            model = reply['model'] if selection else reply['items'][0]
            self.assertEqual(model['current_target_ref'], current)
            self.assertEqual(model['version'], 1 if selection else 2)
        self.assertEqual(stored(), before)
        self.assertEqual(len(self.posts), count)

    def test_model_read_keeps_snapshot_during_concurrent_head_update(self):
        source = self.existing()
        original_connect = control_store.connect
        original_json = ds._json
        for version in (None, 1):
            with self.subTest(version=version):
                model_id = 'read-race-' + str(version)
                first = self.save(model_id=model_id, source=source)
                staged, release = threading.Event(), threading.Event()
                writer = None
                callback_errors = []
                def hold_update(record):
                    if isinstance(record, dict) and record.get('model_id') == model_id and record.get('version') == 2:
                        staged.set()
                        if not release.wait(3):
                            raise AssertionError('reader did not release staged model update')
                    return original_json(record)
                @contextmanager
                def connect():
                    nonlocal writer
                    with original_connect() as db:
                        def trace(sql):
                            nonlocal writer
                            if (threading.current_thread() is threading.main_thread()
                                    and sql.startswith('SELECT record FROM wap_model_versions')):
                                try:
                                    self.assertTrue(db.in_transaction)
                                    writer = pool.submit(self.save, model_id=model_id, source=source,
                                        expected_version=1, sql='SELECT label, id FROM source')
                                    self.assertTrue(staged.wait(3), 'writer did not stage head update')
                                except BaseException as exc:
                                    callback_errors.append(exc)
                        db.set_trace_callback(trace)
                        yield db
                with ThreadPoolExecutor(max_workers=1) as pool, patch.object(ds, '_json', side_effect=hold_update), \
                        patch.object(control_store, 'connect', connect):
                    try:
                        read = wap.get_model(self.principal, self.context, model_id=model_id, version=version)
                    finally:
                        release.set()
                    self.assertEqual(callback_errors, [])
                    self.assertIsNotNone(writer)
                    self.assertEqual(writer.result(timeout=3)['version'], 2)
                self.assertEqual(read, dict(first, current_target_ref=None))
                self.assertEqual(wap.get_model(self.principal, self.context, model_id=model_id)['version'], 2)
        self.assertEqual(self.posts, [])

    def test_model_read_revalidates_on_snapshot_while_writer_commit_pending(self):
        model = self.save()
        original_store_connect = control_store.connect
        original_sqlite_connect = sqlite3.connect
        fixture_db = control_store.root() / 'jobs.sqlite3'
        with original_store_connect() as db:
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
            db.execute('CREATE TABLE model_read_test_marker(n INTEGER NOT NULL)')
            db.execute('INSERT INTO model_read_test_marker VALUES(0)')

        def stored():
            with original_store_connect() as db:
                return (tuple(db.execute('SELECT * FROM wap_models').fetchall()),
                        tuple(db.execute('SELECT * FROM wap_model_versions').fetchall()))

        before = stored()
        for dirty in (True, False):
            with self.subTest(dirty=dirty):
                with original_store_connect() as db:
                    db.execute('UPDATE model_read_test_marker SET n=0')
                committing, committed = threading.Event(), threading.Event()
                writer_errors, callback_errors, pending = [], [], []
                writer = None

                def write():
                    try:
                        with original_sqlite_connect(fixture_db, timeout=2) as db:
                            db.execute('BEGIN IMMEDIATE')
                            if dirty:
                                db.execute('UPDATE model_read_test_marker SET n=n+1')
                            else:
                                db.execute('SELECT n FROM model_read_test_marker').fetchone()
                            db.set_trace_callback(lambda sql: committing.set() if sql == 'COMMIT' else None)
                            db.commit()
                            committed.set()
                    except BaseException as exc:
                        writer_errors.append(exc)

                def short_connect(database, *args, **kwargs):
                    self.assertEqual(Path(database).resolve(), fixture_db.resolve())
                    kwargs['timeout'] = 0.1
                    return original_sqlite_connect(database, *args, **kwargs)

                @contextmanager
                def connect():
                    nonlocal writer
                    with original_store_connect() as db:
                        head_selected = False

                        def trace(sql):
                            nonlocal head_selected, writer
                            if sql.startswith('SELECT * FROM wap_models'):
                                head_selected = True
                            if not sql.startswith('SELECT record FROM wap_model_versions'):
                                return
                            try:
                                self.assertTrue(head_selected)
                                self.assertTrue(db.in_transaction)
                                writer = threading.Thread(target=write, daemon=True)
                                writer.start()
                                self.assertTrue(committing.wait(0.75), 'writer never entered COMMIT')
                                deadline = time.monotonic() + 0.75
                                while time.monotonic() < deadline:
                                    try:
                                        with original_sqlite_connect(fixture_db, timeout=0) as probe:
                                            probe.execute('SELECT n FROM model_read_test_marker').fetchone()
                                    except sqlite3.OperationalError as exc:
                                        self.assertEqual(exc.sqlite_errorcode, sqlite3.SQLITE_BUSY)
                                        self.assertEqual(exc.sqlite_errorname, 'SQLITE_BUSY')
                                        self.assertFalse(committed.is_set())
                                        self.assertTrue(db.in_transaction)
                                        pending.append(True)
                                        break
                                    committed.wait(0.001)
                                self.assertEqual(pending, [True], 'writer PENDING was not observed')
                            except BaseException as exc:
                                # sqlite trace callbacks swallow exceptions; assert them below.
                                callback_errors.append(exc)

                        db.set_trace_callback(trace)
                        yield db

                try:
                    with patch.object(sqlite3, 'connect', short_connect), \
                            patch.object(control_store, 'connect', connect):
                        loaded = wap.get_model(self.principal, self.context, model_id=model['model_id'])
                finally:
                    if writer is not None:
                        writer.join(2.5)
                        self.assertFalse(writer.is_alive())
                    self.assertEqual(callback_errors, [])
                    self.assertEqual(writer_errors, [])
                    self.assertEqual(pending, [True])
                    self.assertTrue(committed.is_set())
                self.assertEqual(loaded, dict(model, current_target_ref=None))
                self.assertEqual(stored(), before)
                with original_store_connect() as db:
                    self.assertEqual(db.execute('SELECT n FROM model_read_test_marker').fetchone()[0], int(dirty))
        self.assertEqual(self.posts, [])

    def test_model_read_provided_connection_denies_changes_before_snapshot(self):
        model = self.save()
        original_connect = control_store.connect
        original_access = wap._access
        original_guard = wap.revalidate_admission
        with original_connect() as db:
            epoch = db.execute('SELECT epoch FROM effective_grants WHERE principal_id=? AND database_id=?',
                (self.principal.principal_id, self.context.database_id)).fetchone()[0]
            revision = dm.get_binding(db, self.context.database_id).desired_revision
        for change in ('grant_epoch', 'binding_revision'):
            with self.subTest(change=change):
                changed, guarded = [], []
                outer_connections, callback_errors = [], []
                armed = False

                def access(*args, **kwargs):
                    nonlocal armed
                    result = original_access(*args, **kwargs)
                    armed = True
                    return result

                @contextmanager
                def connect():
                    with original_connect() as db:
                        def trace(sql):
                            nonlocal armed
                            if sql != 'BEGIN' or not armed:
                                return
                            armed = False
                            try:
                                # BEGIN trace runs before the first SELECT acquires SHARED.
                                with original_connect() as mutation:
                                    if change == 'grant_epoch':
                                        mutation.execute('UPDATE effective_grants SET epoch=epoch+1 '
                                            'WHERE principal_id=? AND database_id=?',
                                            (self.principal.principal_id, self.context.database_id))
                                    else:
                                        mutation.execute('UPDATE catalog_bindings SET desired_revision=desired_revision+1 '
                                            'WHERE database_id=?', (self.context.database_id,))
                                changed.append(change)
                                outer_connections.append(db)
                            except BaseException as exc:
                                callback_errors.append(exc)
                        db.set_trace_callback(trace)
                        yield db

                def guard(db, principal, context):
                    self.assertEqual(outer_connections, [db])
                    self.assertTrue(db.in_transaction)
                    guarded.append(True)
                    return original_guard(db, principal, context)

                try:
                    with patch.object(control_store, 'connect', connect), \
                            patch.object(wap, '_access', access), \
                            patch.object(wap, 'revalidate_admission', guard):
                        with self.assertRaises(QueryError) as caught:
                            wap.get_model(self.principal, self.context, model_id=model['model_id'])
                    self.assertEqual(callback_errors, [])
                    self.assertEqual(changed, [change])
                    self.assertEqual(guarded, [True])
                    self.assertEqual(caught.exception.code, 'DB_ACCESS_REVOKED')
                finally:
                    with original_connect() as db:
                        db.execute('UPDATE effective_grants SET epoch=? WHERE principal_id=? AND database_id=?',
                            (epoch, self.principal.principal_id, self.context.database_id))
                        db.execute('UPDATE catalog_bindings SET desired_revision=? WHERE database_id=?',
                            (revision, self.context.database_id))
        self.assertEqual(self.posts, [])

    def test_model_read_revalidates_grant_loss_before_return(self):
        model = self.save()
        access = wap._access
        def revoke(*args, **kwargs):
            metadata = access(*args, **kwargs)
            with control_store.connect() as db:
                db.execute('DELETE FROM grants WHERE principal_id=? AND database_id=?',
                    (self.principal.principal_id, self.context.database_id))
            return metadata
        with patch.object(wap, '_access', side_effect=revoke):
            with self.assertRaises(QueryError) as caught:
                wap.get_model(self.principal, self.context, model_id=model['model_id'])
        self.assertEqual(caught.exception.code, 'DB_ACCESS_REVOKED')
        self.assertEqual(self.posts, [])

    def test_three_db_same_names_and_foreign_artifacts(self):
        builds = [self.built(database=d, key='build-' + d) for d in self.b.databases]
        self.assertEqual(len({b['id'] for b in builds}), 3)
        for context in [self.b.context(d) for d in self.b.databases[1:]]:
            with self.assertRaises(QueryError):
                wap.get_build(self.principal, context, build_id=builds[0]['id'])
            with self.assertRaises(QueryError):
                wap.audit(self.principal, context, key='foreign', build_id=builds[0]['id'])
        self.assertEqual(len(self.posts), 3)

    def test_cas_versions_and_replay_precedes_mutable_validation(self):
        source = self.existing()
        target = self.b.locator('publication', schema='marts')
        model = self.save(source=source, target=target)
        first = wap.build(self.principal, self.context, key='build', model_id='shared', version=1)
        with self.assertRaises(QueryError):
            self.save(source=source, target=target)
        self.save(source=source, target=target, expected_version=1, sql='SELECT id FROM source')
        replay = wap.build(self.principal, self.context, key='build', model_id='shared', version=1)
        self.assertEqual(first['id'], replay['id'])
        self.assertEqual(len(self.posts), 1)
        with self.assertRaises(QueryError):
            wap.build(self.principal, self.context, key='build', model_id='shared', version=2)
        with self.assertRaises(QueryError):
            wap.audit(self.principal, self.context, key='old', build_id=first['result']['build_id'])
        with control_store.connect() as db:
            self.assertFalse(db.execute("SELECT id FROM dataops_operations WHERE state='ACTIVE'").fetchall())
        self.assertEqual(model['version'], 1)

    def test_source_append_does_not_change_published_rows(self):
        source = self.existing()
        built, audited = self.audited(source=source)
        metadata = self.metadata(source)
        self.snapshot(metadata, [(99, 'later')])
        published = self.publish(built, audited)
        ref = dm.ResourceRef(**published['target']['ref'])
        actual = self.snapshots[(self.metadata(ref)['table-uuid'], int(published['target']['snapshot']))]
        self.assertEqual(actual, [(1, 'one'), (2, 'two'), (3, None)])
        self.assertIn('FOR VERSION AS OF ' + built['stage']['snapshot'], self.posts[-1][0])
        self.assertIn('SELECT "id", "label"', self.posts[-1][0])
        self.assertNotIn('latest', self.posts[-1][0].lower())
        self.assertNotEqual(self.posts[0][1]['X-Trino-User'], '')
        with self.assertRaises(QueryError):
            ds.Execution(self.owner, built['operation_id']).inspect()
        self.assertEqual(wap.get_build(self.owner, self.admin, build_id=built['id']), built)

    def test_engineer_viewer_denied_publish_and_rollback_before_post(self):
        built, audited = self.audited()
        before = len(self.posts)
        for role in ('data_engineer', 'bi_viewer'):
            context = self.b.context(role=role, schema='marts' if role == 'bi_viewer' else 'raw')
            for action in ('publish', 'rollback'):
                with self.subTest(role=role, action=action), self.assertRaises(QueryError):
                    if action == 'publish':
                        wap.publish(self.principal, context, key=role, build_id=built['id'], audit_id=audited['id'])
                    else:
                        wap.rollback(self.principal, context, key=role, publication_id='unknown')
        self.assertEqual(len(self.posts), before)
        with control_store.connect() as db:
            self.assertFalse(db.execute("SELECT id FROM dataops_operations WHERE state='ACTIVE'").fetchall())

    def test_quality_fail_and_unaudited_or_mismatched_audit(self):
        built, audited = self.audited(not_null=('label',))
        self.assertFalse(audited['passed'])
        for audit_id in ('missing', audited['id']):
            with self.assertRaises(QueryError):
                wap.publish(self.owner, self.admin, key=audit_id, build_id=built['id'], audit_id=audit_id)
        self.assertEqual(len(self.posts), 2)

    def test_stage_head_and_schema_only_changes_deny(self):
        built, audited = self.audited()
        stage = dm.ResourceRef(**built['stage']['ref'])
        metadata = self.metadata(stage)
        original = json.loads(json.dumps(metadata))
        for change in ('head', 'schema', 'uuid'):
            metadata.clear()
            metadata.update(json.loads(json.dumps(original)))
            if change == 'head':
                self.snapshot(metadata, [(7, 'changed')])
            elif change == 'schema':
                metadata['schemas'][0]['fields'].append(dict(id=3, name='added', type='long'))
            else:
                metadata['table-uuid'] = str(uuid.uuid4())
            with self.subTest(change=change), self.assertRaises(QueryError):
                wap.publish(self.owner, self.admin, key=change, build_id=built['id'], audit_id=audited['id'])
        self.assertEqual(len(self.posts), 2)

    def test_first_publication_rollback_denied_without_drop(self):
        built, audited = self.audited()
        publication = self.publish(built, audited)
        with self.assertRaises(QueryError):
            wap.rollback(self.owner, self.admin, key='rollback', publication_id=publication['id'])
        self.assertEqual(len(self.posts), 3)

    def test_rollback_recorded_previous_snapshot_and_expiry(self):
        target = self.existing('already_published', schema='marts', rows=[(77, 'previous')])
        prior = self.metadata(target)['current-snapshot-id']
        built, audited = self.audited(target=target)
        publication = self.publish(built, audited)
        metadata = self.metadata(target)
        previous_snapshots = metadata['snapshots']
        metadata['snapshots'] = [s for s in previous_snapshots if s['snapshot-id'] != prior]
        with self.assertRaises(QueryError) as caught:
            wap.rollback(self.owner, self.admin, key='expired', publication_id=publication['id'])
        self.assertEqual(caught.exception.code, 'PREVIOUS_SNAPSHOT_EXPIRED')
        metadata['snapshots'] = previous_snapshots
        receipt = wap.rollback(self.owner, self.admin, key='rollback', publication_id=publication['id'])
        self.assertEqual(receipt['result']['publication']['target']['snapshot'], str(prior))
        self.assertEqual(metadata['current-snapshot-id'], prior)
        self.assertGreater(max(s['snapshot-id'] for s in metadata['snapshots']), prior)
        self.assertIn('rollback_to_snapshot(' + str(prior) + ')', self.posts[-1][0])

    def test_target_recreate_head_and_schema_changes_deny(self):
        target = self.existing('already_published', schema='marts')
        built, audited = self.audited(target=target)
        metadata = self.metadata(target)
        original = json.loads(json.dumps(metadata))
        for change in ('head', 'schema', 'uuid'):
            metadata.clear()
            metadata.update(json.loads(json.dumps(original)))
            if change == 'head':
                self.snapshot(metadata, [(9, 'external')])
            elif change == 'schema':
                metadata['current-schema-id'] = 1
                metadata['schemas'][0]['schema-id'] = 1
            else:
                metadata['table-uuid'] = str(uuid.uuid4())
            with self.subTest(change=change), self.assertRaises(QueryError):
                wap.publish(self.owner, self.admin, key=change, build_id=built['id'], audit_id=audited['id'])
        self.assertEqual(len(self.posts), 2)

    def test_known_commit_bookkeeping_failure_and_replay_zero_post(self):
        built, audited = self.audited()
        with patch.object(wap, '_store_artifact', side_effect=OSError('disk failed')):
            receipt = wap.publish(self.owner, self.admin, key='publish', build_id=built['id'], audit_id=audited['id'])
        self.assertTrue(receipt['committed'])
        self.assertEqual(receipt['state'], 'FINISHED')
        self.assertTrue(receipt['result']['publication_id'])
        count = len(self.posts)
        replay = wap.publish(self.owner, self.admin, key='publish', build_id=built['id'], audit_id=audited['id'])
        self.assertEqual(receipt['id'], replay['id'])
        self.assertEqual(len(self.posts), count)

    def next_audited(self, *, key='next'):
        built = wap.build(self.principal, self.context, key=key + '-build',
                          model_id='shared', version=1)['result']['build']
        audited = wap.audit(self.principal, self.context, key=key + '-audit',
                            build_id=built['id'])['result']['audit']
        return built, audited

    def assert_pending_publication_blocks(self, built, audited, target):
        count = len(self.posts)
        source = dm.ResourceRef(**built['input_refs']['source']['ref'])
        attempts = (
            lambda: wap.build(self.principal, self.context, key='blocked-build', model_id='shared', version=1),
            lambda: self.save(source=source, target=target, expected_version=1),
            lambda: wap.publish(self.owner, self.admin, key='blocked-publish',
                                build_id=built['id'], audit_id=audited['id']))
        for attempt in attempts:
            with self.assertRaises(QueryError) as caught:
                attempt()
            self.assertEqual(caught.exception.code, 'MODEL_RECONCILIATION_REQUIRED')
        self.assertEqual(len(self.posts), count)

    def test_missing_known_publication_artifact_blocks_next_model_actions(self):
        built, audited = self.audited()
        first = self.publish(built, audited)
        second_build, second_audit = self.next_audited()
        with patch.object(wap, '_store_artifact', side_effect=OSError('record failed')):
            receipt = wap.publish(self.owner, self.admin, key='unrecorded',
                build_id=second_build['id'], audit_id=second_audit['id'])
        self.assertTrue(receipt['committed'])
        self.assertEqual(receipt['state'], 'FINISHED')
        self.assert_pending_publication_blocks(second_build, second_audit, dm.ResourceRef(**first['target']['ref']))

    def test_missing_known_rollback_artifact_blocks_next_model_actions(self):
        built, audited = self.audited()
        first = self.publish(built, audited)
        second_build, second_audit = self.next_audited()
        second = self.publish(second_build, second_audit, key='second')
        with patch.object(wap, '_store_artifact', side_effect=OSError('rollback record failed')):
            receipt = wap.rollback(self.owner, self.admin, key='unrecorded-rollback', publication_id=second['id'])
        self.assertTrue(receipt['committed'])
        self.assertEqual(receipt['state'], 'FINISHED')
        self.assert_pending_publication_blocks(second_build, second_audit, dm.ResourceRef(**first['target']['ref']))

    def test_missing_reconciled_publication_artifact_blocks_even_failed_parent(self):
        built, audited = self.audited()
        first = self.publish(built, audited)
        second_build, second_audit = self.next_audited()
        original = self.transport
        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise TimeoutError('commit response lost')
        with patch.object(qs, '_transport', side_effect=lost):
            receipt = wap.publish(self.owner, self.admin, key='lost-publication',
                build_id=second_build['id'], audit_id=second_audit['id'])
        self.assertEqual(receipt['state'], 'UNKNOWN')
        child = receipt['queries'][-1]
        def observe(expected):
            return [dict(id='observed_commit', source=expected['source'], user=expected['backend_username'],
                         sql=expected['sql'], state='FINISHED')]
        qs.reconcile_query(self.owner, child['id'], selected_role='admin', observer=observe)
        settled = ds.Execution(self.owner, receipt['id']).reconcile_failure()
        self.assertEqual(settled['state'], 'FAILED')
        self.assertTrue(settled['committed'])
        self.assert_pending_publication_blocks(second_build, second_audit, dm.ResourceRef(**first['target']['ref']))

    def test_rollback_rejects_different_previous_schema_before_post(self):
        target = self.existing('old_contract', schema='marts')
        built, audited = self.audited(target=target, sql='SELECT id, label AS renamed FROM source')
        publication = self.publish(built, audited)
        before = len(self.posts)
        with self.assertRaises(QueryError) as caught:
            wap.rollback(self.owner, self.admin, key='incompatible', publication_id=publication['id'])
        self.assertEqual(caught.exception.code, 'ROLLBACK_SCHEMA_INCOMPATIBLE')
        self.assertEqual(len(self.posts), before)

    def test_rollback_rejects_same_columns_with_different_field_identity(self):
        target = self.existing('field_identity', schema='marts')
        for field in self.metadata(target)['schemas'][0]['fields']:
            field['id'] += 10
        built, audited = self.audited(target=target)
        publication = self.publish(built, audited)
        self.assertEqual(publication['previous']['columns'], publication['target']['columns'])
        before = len(self.posts)
        with self.assertRaises(QueryError) as caught:
            wap.rollback(self.owner, self.admin, key='incompatible-fields', publication_id=publication['id'])
        self.assertEqual(caught.exception.code, 'ROLLBACK_SCHEMA_INCOMPATIBLE')
        self.assertEqual(len(self.posts), before)

    def test_external_prebuild_append_is_unattributed_after_rollback(self):
        built, audited = self.audited()
        first = self.publish(built, audited)
        target = dm.ResourceRef(**first['target']['ref'])
        metadata = self.metadata(target)
        external_rows = [(1, 'one'), (2, 'two'), (3, None), (77, 'external')]
        self.snapshot(metadata, external_rows)
        external_snapshot = str(metadata['current-snapshot-id'])
        second_build, second_audit = self.next_audited()
        self.assertEqual(second_build['target']['snapshot'], external_snapshot)
        second = self.publish(second_build, second_audit, key='second')
        self.assertIsNone(second['previous_publication_id'])
        result = wap.rollback(self.owner, self.admin, key='restore-external', publication_id=second['id'])
        restored = result['result']['publication']
        self.assertEqual(restored['target']['snapshot'], external_snapshot)
        self.assertEqual(self.snapshots[(metadata['table-uuid'], int(external_snapshot))], external_rows)
        self.assertIsNone(restored['restored_publication_id'])
        self.assertIsNone(restored['audit_id'])
        self.assertIsNone(restored['stage'])

    def test_ambiguous_commit_keeps_unknown_and_replay_zero_post(self):
        built, audited = self.audited()
        original = self.transport
        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise TimeoutError('response lost')
        with patch.object(qs, '_transport', side_effect=lost):
            receipt = wap.publish(self.owner, self.admin, key='publish', build_id=built['id'], audit_id=audited['id'])
        self.assertEqual(receipt['state'], 'UNKNOWN')
        self.assertTrue(receipt['result']['publication_id'])
        self.assertFalse(receipt['committed'])
        before = len(self.posts)
        replay = wap.publish(self.owner, self.admin, key='publish', build_id=built['id'], audit_id=audited['id'])
        self.assertEqual(replay['id'], receipt['id'])
        self.assertEqual(len(self.posts), before)
        with self.assertRaises(QueryError) as caught:
            self.save(source=dm.ResourceRef(**built['input_refs']['source']['ref']),
                      target=self.b.locator('published_data', schema='marts'), expected_version=1)
        self.assertIn(caught.exception.code, ('MODEL_BUSY', 'DESTINATION_EXISTS'))

    def test_cross_database_source_target_and_stage_rejected(self):
        foreign = self.existing(database=self.b.databases[1])
        local = self.existing()
        for source, target in ((foreign, self.b.locator()), (local, foreign),
                               (local, self.b.locator(database=self.b.databases[1]))):
            with self.assertRaises(QueryError):
                self.save(source=source, target=target)
        built = self.built(source=local)
        forged = dict(built, stage=dict(built['stage'], ref=asdict(foreign)))
        execution = self.b.begin(key='forged', context=self.admin, principal=self.owner)
        with self.assertRaises(QueryError):
            execution._wap_owned(forged)
        execution.fail()
        self.assertEqual(len(self.posts), 1)

    def test_ast_scope_rewrite_and_bypass_corpus(self):
        sql = 'WITH c AS (SELECT source.id, label FROM source) SELECT c.id, c.label FROM c WHERE id IN (SELECT id FROM source)'
        built = self.built(sql=sql)
        self.assertEqual(built['generated_sql'].count('FOR VERSION AS OF'), 2)
        bad = ['SELECT * FROM sales.raw.source', 'SELECT * FROM source FOR VERSION AS OF 1',
            'SELECT * FROM source$history', 'SELECT * FROM source; DELETE FROM source',
            'WITH x AS (DELETE FROM source) SELECT * FROM x', 'SELECT * FROM UNNEST(ARRAY[1])',
            "SELECT * FROM TABLE(system.query(query => 'DELETE FROM x'))", 'CALL system.foo()',
            'SELECT * INTO target FROM source', 'SELECT * FROM source --comment',
            'SELECT /* hidden */ * FROM source', 'SELECT evil(id) FROM source',
            'SELECT * FROM (VALUES (1))', 'SELECT * FROM source TABLESAMPLE BERNOULLI (1)',
            'WITH RECURSIVE x AS (SELECT * FROM source) SELECT * FROM x',
            'SELECT * FROM source FOR TIMESTAMP AS OF CURRENT_TIMESTAMP']
        for sql in bad:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                wap._compile_model(sql, {'source': None})
        for sql, bindings in [('SELECT * FROM source', {}), ('SELECT 1', {'source': None}),
                              ('SELECT * FROM source', {'source': None, 'extra': None})]:
            with self.assertRaises(ValueError):
                wap._compile_model(sql, bindings)
        wap._compile_model('WITH source AS (SELECT id FROM a) SELECT * FROM source JOIN b ON source.id=b.id', {'a': None, 'b': None})

    def test_model_save_serializes_cas(self):
        source = self.existing()
        target = self.b.locator(schema='marts')
        barrier = threading.Barrier(2)
        def save():
            barrier.wait()
            try:
                return self.save(source=source, target=target)['version']
            except QueryError as error:
                return error.code
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: save(), range(2)))
        self.assertCountEqual(results, [1, 'MODEL_VERSION_CONFLICT'])
        self.assertFalse(self.posts)

    def test_import_no_paths_json_or_sqlglot_and_dynamic_state_root(self):
        root = Path(os.environ['SHIROKUMA_STATE_DIR']) / 'uncreated'
        code = ('import sys; sys.path.insert(0,"tools/local_experiment"); '
                'import wap_resident, manual_wap; '
                'assert "sqlglot" not in sys.modules')
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
            env=dict(os.environ, SHIROKUMA_STATE_DIR=str(root)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(root.exists())
        built = self.built()
        original = Path(os.environ['SHIROKUMA_STATE_DIR'])
        (original / 'wap_models.json').write_text('{invalid legacy JSON')
        with patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(root)):
            with control_store.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM wap_models').fetchone()[0], 0)
        self.assertEqual(wap.get_build(self.principal, self.context, build_id=built['id']), built)

    def test_valid_join_aggregate_and_subqueries(self):
        source = self.existing()
        other = self.existing('other_data', rows=[(1, 'A'), (1, 'B'), (3, 'C')])
        model = wap.save_model(self.principal, self.context, model_id='joined',
            sql='WITH c AS (SELECT source.id FROM source) SELECT c.id, COUNT(*) AS n FROM c JOIN other ON c.id=other.id GROUP BY c.id',
            sources={'source': source, 'other': other}, target=self.b.locator(schema='marts'), expected_version=None)
        receipt = wap.build(self.principal, self.context, key='join', model_id='joined', version=model['version'])
        built = receipt['result']['build']
        metadata = self.metadata(dm.ResourceRef(**built['stage']['ref']))
        self.assertEqual(self.snapshots[(metadata['table-uuid'], metadata['current-snapshot-id'])], [(1, 2), (3, 1)])
        self.assertEqual(set(built['input_refs']), {'source', 'other'})

    def test_valid_audit_of_another_build_does_not_authorize_publication(self):
        built, audited = self.audited()
        second = wap.build(self.principal, self.context, key='another', model_id='shared', version=1)['result']['build']
        with self.assertRaises(QueryError) as caught:
            wap.publish(self.owner, self.admin, key='mismatch', build_id=second['id'], audit_id=audited['id'])
        self.assertEqual(caught.exception.code, 'AUDIT_MISMATCH')
        self.assertEqual(len(self.posts), 3)

    def test_followup_build_pins_materialized_ref_and_rollback_correspondence(self):
        built, audited = self.audited()
        first = self.publish(built, audited)
        second_build = wap.build(self.principal, self.context, key='second-build', model_id='shared', version=1)['result']['build']
        self.assertEqual(second_build['target'], first['target'])
        second_audit = wap.audit(self.principal, self.context, key='second-audit', build_id=second_build['id'])['result']['audit']
        second = self.publish(second_build, second_audit, key='second-publish')
        rolled = wap.rollback(self.owner, self.admin, key='restore', publication_id=second['id'])
        restored = rolled['result']['publication']
        self.assertEqual(restored['audit_id'], first['audit_id'])
        self.assertEqual(restored['restored_publication_id'], first['id'])
        self.assertEqual(restored['target']['snapshot'], first['target']['snapshot'])
        ref = dm.ResourceRef(**first['target']['ref'])
        self.metadata(ref)['table-uuid'] = str(uuid.uuid4())
        before = len(self.posts)
        with self.assertRaises(QueryError):
            wap.build(self.principal, self.context, key='after-recreation', model_id='shared', version=1)
        self.assertEqual(len(self.posts), before)

    def test_source_schema_changed_before_ctas_rejected_with_no_post(self):
        source = self.existing()
        self.save(source=source)
        original = ds.Execution.wap_measure
        measurements = []
        def measure(execution, ref):
            value = original(execution, ref)
            if ref == source:
                measurements.append(True)
                if len(measurements) == 1:
                    self.metadata(source)['schemas'][0]['fields'].append(dict(id=3, name='extra', type='long'))
            return value
        with patch.object(ds.Execution, 'wap_measure', measure), self.assertRaises(QueryError) as caught:
            wap.build(self.principal, self.context, key='schema-change', model_id='shared', version=1)
        self.assertEqual(caught.exception.code, 'SOURCE_SCHEMA_CHANGED')
        self.assertFalse(self.posts)

    def test_grant_loss_after_begin_settles_empty_parent(self):
        built, audited = self.audited()
        real_access = wap._access
        def revoke(principal, context, **kwargs):
            with control_store.connect() as db:
                db.execute("DELETE FROM grants WHERE principal_id='alice' AND database_id=?", (context.database_id,))
            return real_access(principal, context, **kwargs)
        before = len(self.posts)
        with patch.object(wap, '_access', side_effect=revoke), self.assertRaises((QueryError, PermissionError)):
            wap.audit(self.principal, self.context, key='revoked', build_id=built['id'])
        self.assertEqual(len(self.posts), before)
        with control_store.connect() as db:
            row = db.execute("SELECT state FROM dataops_operations WHERE idempotency_key='revoked'").fetchone()
            self.assertEqual(row['state'], 'FAILED')

    def test_target_claim_never_steals_active_or_unknown(self):
        target = self.existing('publication', schema='marts')
        first = self.b.begin(key='first-admin', context=self.admin, principal=self.owner)
        second = self.b.begin(key='second-admin', context=self.admin, principal=self.owner)
        first.wap_claim(target)
        for state in ('ACTIVE', 'UNKNOWN'):
            with control_store.connect() as db:
                db.execute('UPDATE dataops_operations SET state=? WHERE id=?', (state, first.id))
            with self.assertRaises(QueryError) as caught:
                second.wap_claim(target)
            self.assertEqual(caught.exception.code, 'TARGET_BUSY')
        with control_store.connect() as db:
            db.execute("UPDATE dataops_operations SET state='ACTIVE' WHERE id=?", (first.id,))
        first.fail()
        second.wap_claim(target)
        second.fail()
        self.assertFalse(self.posts)

    def test_rollback_rejects_changed_target_head_schema_and_uuid(self):
        target = self.existing('publication', schema='marts')
        built, audited = self.audited(target=target)
        publication = self.publish(built, audited)
        metadata = self.metadata(target)
        original = json.loads(json.dumps(metadata))
        before = len(self.posts)
        for change in ('head', 'schema', 'uuid'):
            metadata.clear()
            metadata.update(json.loads(json.dumps(original)))
            if change == 'head':
                self.snapshot(metadata, [(9, 'external')])
            elif change == 'schema':
                metadata['schemas'][0]['fields'].append(dict(id=3, name='extra', type='long'))
            else:
                metadata['table-uuid'] = str(uuid.uuid4())
            with self.subTest(change=change), self.assertRaises(QueryError):
                wap.rollback(self.owner, self.admin, key=change, publication_id=publication['id'])
        self.assertEqual(len(self.posts), before)

    def test_typed_primitive_rejects_substituted_destination(self):
        built, audited = self.audited()
        execution = self.b.begin(key='substituted', context=self.admin, principal=self.owner)
        with self.assertRaises(QueryError) as caught:
            execution.wap_publish(self.b.locator('substituted', schema='marts'), built, audited,
                                  step='publish', result={})
        self.assertEqual(caught.exception.code, 'TARGET_CHANGED')
        execution.fail()
        self.assertEqual(len(self.posts), 2)

    def test_model_lock_serializes_across_processes(self):
        source = self.existing()
        target = self.b.locator(schema='marts')
        code = ('import sys; sys.path.insert(0,"tools/local_experiment"); import manual_wap as w; '
                '\nwith w._model_lock(sys.argv[1],"shared"):\n print("locked",flush=True); sys.stdin.readline()')
        process = subprocess.Popen([sys.executable, '-c', code, self.context.database_id],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'locked')
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(self.save, source=source, target=target)
                try:
                    self.assertRaises(TimeoutError, future.result, timeout=0.15)
                finally:
                    process.communicate('\n', timeout=10)
                self.assertEqual(future.result(timeout=10)['version'], 1)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


if __name__ == '__main__':
    unittest.main()
