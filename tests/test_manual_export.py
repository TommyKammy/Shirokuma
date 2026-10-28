"""P3-4 ledger/authorization and fault tests; no live engine acceptance."""
from dataclasses import replace
from decimal import Decimal
import csv
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store
import data_management as dm
import dataops_service as ds
import manual_export as export
import query_service as qs
from query_context import QueryError
import warehouse_admission as admission
import test_dataops_service as boundary


class ManualExportTests(unittest.TestCase):

    def test_retirement_between_count_and_select_keeps_file_publication_private(self):
        self.after_count = self.b.retire
        with self.assertRaises((QueryError, PermissionError)):
            self.run_export(format='jsonl')
        with control_store.connect() as db:
            operation_id = db.execute('SELECT id FROM dataops_operations').fetchone()[0]
        operation = ds.Execution(self.principal, operation_id)._inspect()
        self.assertEqual(operation['state'], 'FINISHED')
        self.assertEqual(operation['result']['receipt']['written_count'], 1)
        self.b.assert_database_admissions()
        for read in (lambda: self.read(operation),
                     lambda: export.get_export(self.principal, self.context,
                         export_id=operation['result']['export_id']),
                     lambda: ds.Execution(self.principal, operation['id']).inspect()):
            with self.assertRaises((QueryError, PermissionError)):
                read()

    def test_private_same_execution_returns_canonical_observation_after_finish(self):
        execution = self.b.begin()
        self.after_count = self.b.retire
        operation = export.export_table(self.principal, self.context, key='private',
            source=self.source, format='jsonl', _execution=execution)
        self.assertEqual(operation['state'], 'FINISHED')
        self.assertEqual(operation['result']['receipt']['written_count'], 1)
        with self.assertRaises((QueryError, PermissionError)):
            execution.inspect()

    def setUp(self):
        self.b = boundary.DataOpsTests()
        self.b.setUp()
        self.addCleanup(self.b.doCleanups)
        self.principal, self.context = self.b.principal, self.b.context()
        self.source = self.b.existing()
        self.metadata = self.b.objects[('sales', 'raw', 'observed_data', 'tables')]
        self.metadata.update({'current-snapshot-id': 101, 'snapshots': [{'snapshot-id': 101, 'schema-id': 1}],
            'current-schema-id': 1, 'schemas': [{'schema-id': 1, 'fields': [dict(id=1, name='id', type='long')]}]})
        self.rows = [['9007199254740993']]
        self.posts = []
        self.page = None
        self.after_count = lambda: None
        self.after_select = lambda: None
        self.bad_columns = False
        self.loss = False
        self.b.start_patch('query_service._transport', side_effect=self.transport)

    def columns(self):
        return [dict(name=f['name'], type='varchar') for f in self.metadata['schemas'][0]['fields']]

    def transport(self, url, method, pin, body=None):
        self.assertEqual(pin.headers['Authorization'], 'Basic original-secret')
        self.assertEqual(pin.context, self.context)
        if method == 'DELETE':
            return {}
        if method == 'GET':
            if self.loss:
                raise QueryError('QUERY_BACKEND_FAILED', 502)
            return self.page
        sql = body.decode()
        self.posts.append(sql)
        with control_store.connect() as db:
            intent = db.execute('''SELECT a.* FROM export_artifacts a
                JOIN dataops_operations o ON o.id=a.operation_id WHERE o.state='ACTIVE' ''').fetchone()
            self.assertEqual(intent['state'], 'INTENT')
            operation = db.execute('SELECT result,committed FROM dataops_operations WHERE id=?', (intent['operation_id'],)).fetchone()
            self.assertEqual(json.loads(operation['result']), dict(export_id=intent['export_id']))
            self.assertFalse(operation['committed'])
        self.assertIn('FOR VERSION AS OF 101', sql)
        self.assertNotIn('LIMIT', sql)
        if 'COUNT(*)' in sql:
            count = len(self.rows)
            self.after_count()
            return dict(id='count', columns=[dict(name='count', type='bigint')], data=[[count]])
        self.assertIn('CAST(', sql)
        columns = self.columns()
        if self.bad_columns:
            columns = [dict(name='wrong', type='varchar')]
        self.after_select()
        if len(self.rows) > 1000 or self.loss:
            self.page = dict(id='select', columns=columns, data=self.rows[1000:])
            return dict(id='select', columns=self.columns(), data=self.rows[:1000],
                        nextUri='http://127.0.0.1:1234/v1/statement/select/1')
        return dict(id='select', columns=columns, data=self.rows)

    def run_export(self, key='request', format='csv', source=None, context=None):
        return export.export_table(self.principal, context or self.context, key=key,
                                   source=source or self.source, format=format)

    def read(self, operation):
        return export.read_export(self.principal, self.context, export_id=operation['result']['export_id'])

    def assert_settled(self, state='FAILED'):
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM dataops_operations').fetchone()[0], state)
            if state != 'UNKNOWN':
                self.assertFalse(admission.blockers(db, 'w'))
            self.assertFalse(db.execute("SELECT 1 FROM export_artifacts WHERE state='COMPLETE'").fetchone())

    def test_append_snapshot_receipt_and_replay_after_recreation(self):
        def append():
            self.metadata['current-snapshot-id'] = 102
            self.metadata['snapshots'].append({'snapshot-id': 102, 'schema-id': 1})
        self.after_count = append
        operation = self.run_export()
        receipt = self.read(operation)['receipt']
        self.assertEqual(receipt['source']['snapshot'], '101')
        self.assertEqual(receipt['expected_count'], receipt['written_count'])
        self.assertFalse(operation['committed'])
        self.assertEqual(export.get_export(self.principal, self.context, export_id=receipt['export_id']), receipt)
        self.metadata['table-uuid'] = '00000000-0000-0000-0000-000000000000'
        replay = self.run_export()
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['id'], operation['id'])
        self.assertEqual(len(self.posts), 2)
        with self.assertRaises(QueryError):
            self.read(operation)

    def evolved_current_schema(self):
        current = {'schema-id': 2, 'fields': [
            dict(id=1, name='renamed_id', type='long'),
            dict(id=2, name='added_before_export', type='string')]}
        self.metadata['schemas'].append(current)
        self.metadata['current-schema-id'] = 2

    def test_preexisting_add_rename_exports_exact_snapshot_schema(self):
        self.evolved_current_schema()
        operation = self.run_export()
        result = self.read(operation)
        source = result['receipt']['source']
        self.assertEqual(source['snapshot'], '101')
        self.assertEqual(source['schema_id'], 1)
        self.assertEqual(source['current_schema_id'], 2)
        self.assertEqual(source['columns'], [dict(id=1, name='id', type='long')])
        self.assertEqual(result['content'], b'"id"\n"9007199254740993"\n')
        self.assertIn('CAST("id" AS VARCHAR) AS "id"', self.posts[-1])
        self.assertNotIn('renamed_id', self.posts[-1])
        self.assertNotIn('added_before_export', self.posts[-1])
        json_result = self.read(self.run_export(key='json-evolved', format='jsonl'))
        self.assertEqual(json.loads(json_result['content']), {'id': 9007199254740993})
        self.metadata['schemas'][1]['fields'][0]['name'] = 'renamed_again'
        with self.assertRaises(QueryError) as caught:
            self.read(operation)
        self.assertEqual(caught.exception.code, 'EXPORT_SOURCE_CHANGED')

    def test_current_schema_change_during_evolved_export_still_rejected(self):
        self.evolved_current_schema()
        def change_current():
            self.metadata['schemas'][1]['fields'].append(dict(id=3, name='added_during', type='string'))
        self.after_count = change_current
        with self.assertRaises(QueryError) as caught:
            self.run_export()
        self.assertEqual(caught.exception.code, 'EXPORT_SOURCE_CHANGED')
        self.assertEqual(len(self.posts), 1)
        self.assert_settled()

    def test_snapshot_schema_missing_or_unavailable_rejected_before_sql(self):
        self.metadata['snapshots'][0].pop('schema-id')
        with self.assertRaises(QueryError) as caught:
            self.run_export(key='missing-snapshot-schema')
        self.assertEqual(caught.exception.code, 'SCHEMA_REQUIRED')
        self.metadata['snapshots'][0]['schema-id'] = 9
        with self.assertRaises(QueryError):
            self.run_export(key='unavailable-snapshot-schema')
        self.assertFalse(self.posts)
        with control_store.connect() as db:
            self.assertFalse(admission.blockers(db, 'w'))

    def test_prior_completed_receipt_without_separate_current_guard_is_readable(self):
        operation = self.run_export()
        receipt = json.loads(json.dumps(operation['result']['receipt']))
        receipt['source'].pop('current_schema_id')
        receipt['source'].pop('current_schema_fingerprint')
        with control_store.connect() as db:
            db.execute('UPDATE export_artifacts SET record=? WHERE operation_id=?', (ds._json(receipt), operation['id']))
            db.execute('UPDATE dataops_operations SET result=? WHERE id=?',
                (ds._json(dict(export_id=receipt['export_id'], receipt=receipt)), operation['id']))
        result = self.read(operation)
        self.assertEqual(result['receipt'], receipt)
        self.assertEqual(result['content'], b'"id"\n"9007199254740993"\n')

    def test_all_pages_1507_rows_and_identity(self):
        self.rows = [[str(i)] for i in range(1507)]
        operation = self.run_export(format='jsonl')
        content = self.read(operation)['content']
        self.assertEqual([json.loads(line)['id'] for line in content.splitlines()], list(range(1507)))
        with control_store.connect() as db:
            self.assertEqual({r[0] for r in db.execute('SELECT actor FROM query_records')}, {self.principal.principal_id})
        self.assertFalse(operation['committed'])

    def test_empty_snapshot_both_formats(self):
        self.rows = []
        self.assertEqual(self.read(self.run_export())['content'], b'"id"\n')
        self.assertEqual(self.read(self.run_export(key='jsonl', format='jsonl'))['content'], b'')

    def test_one_column_null_empty_and_literal_backslash_csv_and_jsonl(self):
        self.metadata['schemas'][0]['fields'][0].update(name='value', type='string')
        self.rows = [[None], [''], ['\\N']]
        csv_result = self.read(self.run_export())
        self.assertEqual(csv_result['content'], b'"value"\n\n""\n"\\N"\n')
        self.assertEqual(csv_result['receipt']['expected_count'], 3)
        self.assertEqual(csv_result['receipt']['written_count'], 3)
        json_result = self.read(self.run_export(key='single-column-jsonl', format='jsonl'))
        self.assertEqual([json.loads(line) for line in json_result['content'].splitlines()],
                         [{'value': None}, {'value': ''}, {'value': '\\N'}])
        self.assertEqual(json_result['receipt']['written_count'], 3)

    def test_exact_typed_values_and_csv_nulls(self):
        names = ['big', 'decimal', '零 prefix', 'empty', 'null', 'select', 'ts', 'flag', 'day', 'real']
        kinds = ['long', 'decimal(38,8)', 'string', 'string', 'string', 'string', 'timestamp', 'boolean', 'date', 'double']
        self.metadata['schemas'][0]['fields'] = [dict(id=i + 1, name=n, type=t) for i, (n, t) in enumerate(zip(names, kinds))]
        self.rows = [['9007199254740993', '123456789012345678901234567890.12345678', '001', '', None,
                      '\\N, "雪"\nnext', '2026-10-20 12:34:56.001230', 'true', '2026-10-20', '1.234e-20']]
        content = self.read(self.run_export())['content'].decode()
        self.assertIn('"001","",,"\\N, ""雪""\nnext"', content)
        self.assertEqual(list(csv.reader(io.StringIO(content)))[1][:-4], [v or '' for v in self.rows[0][:-4]])
        decoded = json.loads(self.read(self.run_export(key='json', format='jsonl'))['content'], parse_float=Decimal)
        self.assertEqual(decoded['decimal'], Decimal(self.rows[0][1]))
        self.assertEqual(decoded['big'], 9007199254740993)
        self.assertEqual(decoded['ts'], self.rows[0][6])
        self.assertIs(decoded['flag'], True)
        self.assertIsNone(decoded['null'])
        self.assertEqual(decoded['零 prefix'], '001')

    def test_formats_rejected_without_sql(self):
        for kind in ('auto', 'json', 'ndjson', 'parquet', 'CSV', ''):
            with self.assertRaises(QueryError):
                self.run_export(format=kind)
        self.assertFalse(self.posts)

    def test_unsupported_types_duplicate_names_and_snapshot_fail_before_sql(self):
        for index, kind in enumerate(('binary', 'timestamptz', 'time', {'type': 'list'})):
            self.metadata['schemas'][0]['fields'][0]['type'] = kind
            with self.assertRaises(QueryError):
                self.run_export(key=str(index))
        self.metadata['schemas'][0]['fields'][0]['type'] = 'long'
        self.metadata['schemas'][0]['fields'].append(dict(id=2, name='id', type='long'))
        with self.assertRaises(QueryError):
            self.run_export(key='duplicates')
        self.metadata['schemas'][0]['fields'].pop()
        self.metadata['snapshots'] = []
        with self.assertRaises(QueryError):
            self.run_export(key='missing')
        self.assertFalse(self.posts)

    def test_schema_expiration_recreation_between_steps(self):
        changes = [lambda: self.metadata.update({'snapshots': []}),
                   lambda: self.metadata.update({'table-uuid': '00000000-0000-0000-0000-000000000000'}),
                   lambda: self.metadata['schemas'][0]['fields'][0].update(id=2)]
        for change in changes:
            with self.subTest(change=change):
                original = json.loads(json.dumps(self.metadata))
                self.after_count = change
                with self.assertRaises(QueryError):
                    self.run_export(key=str(len(self.posts)))
                self.metadata.clear()
                self.metadata.update(original)
        self.assertEqual(len(self.posts), 3)

    def test_bad_width_page_columns_count_and_nonfinite(self):
        self.rows = [['1', 'extra']]
        with self.assertRaises(QueryError):
            self.run_export(key='width')
        self.rows = [['1']]
        self.bad_columns = True
        with self.assertRaises(QueryError):
            self.run_export(key='columns')
        self.bad_columns = False
        self.metadata['schemas'][0]['fields'][0]['type'] = 'double'
        self.rows = [['NaN']]
        with self.assertRaises(QueryError):
            self.run_export(key='nan')
        self.rows = [['1']]
        self.after_count = lambda: self.rows.append(['2'])
        with self.assertRaises(QueryError):
            self.run_export(key='count')

    def test_view_cross_database_locator_and_unregistered_denied(self):
        view = self.b.existing(name='view_data', kind='VIEW')
        with self.assertRaises(QueryError) as caught:
            self.run_export(source=view)
        self.assertEqual(caught.exception.code, 'VIEW_EXPORT_NOT_SUPPORTED')
        for index, database in enumerate(self.b.databases[1:]):
            ref = self.b.existing(name='observed_data', database=database)
            with self.assertRaises(QueryError):
                self.run_export(key='cross' + str(index), source=ref)
        with self.assertRaises(ValueError):
            self.run_export(source=self.b.locator())
        with self.assertRaises(QueryError):
            self.run_export(key='stale', source=replace(self.source, incarnation=99))
        self.assertFalse(self.posts)

    def test_other_actor_admin_context_profile_and_hash_denied(self):
        operation = self.run_export()
        export_id = operation['result']['export_id']
        owner = self.b.fixture.principal('owner')
        admin = self.b.context(principal=owner, role='admin')
        for principal, context in ((owner, admin), (self.principal, self.b.context(schema='sandbox'))):
            with self.assertRaises(QueryError):
                export.read_export(principal, context, export_id=export_id)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SHIROKUMA_STATE_DIR=directory):
            with self.assertRaises((QueryError, PermissionError)):
                self.read(operation)
        path = export._directory() / (export_id + '.csv')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        path.write_bytes(b'tampered')
        with self.assertRaises(QueryError):
            self.read(operation)

    def test_three_database_same_name_positive_and_viewer_marts(self):
        template = json.loads(json.dumps(self.metadata))
        for database in self.b.databases:
            self.context = self.b.context(database)
            ref = self.b.existing(name='same_name', database=database)
            self.metadata = next(value for key, value in self.b.objects.items()
                                 if key[0] == self.context.sql_catalog and key[2] == 'same_name')
            identity = self.metadata['table-uuid']
            self.metadata.update(template)
            self.metadata['table-uuid'] = identity
            operation = self.run_export(key=database, source=ref)
            self.assertEqual(self.read(operation)['receipt']['source']['ref']['database_id'], database)
        database = self.b.databases[0]
        self.context = self.b.context(database, schema='marts', role='bi_viewer')
        ref = self.b.existing(name='marts_export', schema='marts')
        self.metadata = self.b.objects[('sales', 'marts', 'marts_export', 'tables')]
        identity = self.metadata['table-uuid']
        self.metadata.update(template)
        self.metadata['table-uuid'] = identity
        self.assertEqual(self.read(self.run_export(key='viewer', source=ref))['receipt']['context']['selected_role'], 'bi_viewer')

    def test_revocation_and_regrant_cannot_revive_pinned_receipt(self):
        operation = self.run_export()
        with control_store.connect() as db:
            dm.update_grant(db, 'alice', self.context.database_id, 1, 'revoked')
        with self.assertRaises((QueryError, PermissionError)):
            self.read(operation)
        with control_store.connect() as db:
            dm.update_grant(db, 'alice', self.context.database_id, 2, 'data_engineer')
        with self.assertRaises((QueryError, PermissionError)):
            self.read(operation)
        with control_store.connect() as db:
            from grant_fixtures import verified_grants
            verified_grants(db)
        fresh = self.b.context()
        with self.assertRaises(QueryError):
            export.read_export(self.principal, fresh, export_id=operation['result']['export_id'])

    def test_revocation_during_export_settles_parent(self):
        def revoke():
            with control_store.connect() as db:
                dm.update_grant(db, 'alice', self.context.database_id, 1, 'revoked')
        self.after_select = revoke
        with self.assertRaises((QueryError, PermissionError)):
            self.run_export()
        self.assert_settled()

    def test_page_schema_change_on_later_page_rejected(self):
        self.rows = [[str(i)] for i in range(1005)]
        self.bad_columns = True
        with self.assertRaises(QueryError):
            self.run_export()
        self.assert_settled()

    def test_change_after_select_rejected_before_publication(self):
        self.after_select = lambda: self.metadata['schemas'][0]['fields'][0].update(name='renamed')
        with self.assertRaises(QueryError):
            self.run_export()
        self.assert_settled()

    def test_lost_terminal_page_unknown_replay_has_no_post(self):
        self.loss = True
        with patch.object(qs, '_best_cancel', wraps=qs._best_cancel) as cancel:
            with self.assertRaises(QueryError):
                self.run_export()
            cancel.assert_not_called()
        self.assert_settled('UNKNOWN')
        operation = self.run_export()
        self.assertEqual(operation['state'], 'UNKNOWN')
        self.assertEqual(operation['queries'][-1]['state'], 'UNKNOWN')
        self.assertEqual(len(self.posts), 2)
        with self.assertRaises(QueryError):
            self.read(operation)

    def test_definite_page_failure_cancels_only_running_child_with_original_pin(self):
        original = self.transport
        methods = []
        def malformed(url, method, pin, body=None):
            methods.append((method, pin.headers['Authorization']))
            page = original(url, method, pin, body)
            if body and 'COUNT(*)' not in body.decode():
                self.b.credential = 'Basic replacement-credential'
                page['columns'] = [dict(name='wrong', type='varchar')]
                page['nextUri'] = 'http://127.0.0.1:1234/v1/statement/select/1'
            return page
        with patch.object(qs, '_transport', side_effect=malformed), \
                patch.object(qs, '_best_cancel', wraps=qs._best_cancel) as cancel:
            with self.assertRaises(QueryError) as caught:
                self.run_export()
            self.assertEqual(caught.exception.code, 'EXPORT_PAGE_SCHEMA_MISMATCH')
            self.assertEqual(cancel.call_count, 1)
        self.assert_settled()
        operation = self.run_export()
        self.assertEqual(operation['queries'][-1]['state'], 'CANCELED')
        self.assertEqual([m for m, _ in methods].count('DELETE'), 1)
        self.assertEqual({credential for _, credential in methods}, {'Basic original-secret'})

    def test_partial_write_rename_and_publication_failure(self):
        def broken(stream, *args):
            stream.write('partial')
            raise OSError('disk full')
        for key, target, replacement in (
                ('write', '_write', broken), ('rename', 'os.replace', OSError('rename')),
                ('ledger', '_publish', OSError('ledger'))):
            obj, attr = (export.os, 'replace') if target == 'os.replace' else (export, target)
            with patch.object(obj, attr, side_effect=replacement):
                with self.assertRaises(OSError):
                    self.run_export(key=key)
            self.assertFalse(list(export._directory().iterdir()))
            replay = self.run_export(key=key)
            self.assertEqual(replay['state'], 'FAILED')
        with control_store.connect() as db:
            self.assertFalse(admission.blockers(db, 'w'))

    def test_cleanup_unlink_failure_still_settles_and_retained_file_is_unreadable(self):
        def broken(stream, *args):
            stream.write('partial unpublished bytes')
            raise OSError('format write failed')
        with patch.object(export, '_write', side_effect=broken), \
                patch.object(Path, 'unlink', side_effect=OSError('cleanup denied')):
            with self.assertRaisesRegex(OSError, 'format write failed'):
                self.run_export()
        self.assert_settled()
        paths = list(export._directory().iterdir())
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].name.endswith('.tmp'))
        replay = self.run_export()
        self.assertEqual(replay['state'], 'FAILED')
        self.assertIn('EXPORT_FILE_CLEANUP_FAILED', replay['cleanup_error'])
        with self.assertRaises(QueryError):
            self.read(replay)
        self.assertEqual(len(self.posts), 2)

    def test_empty_select_requires_observed_projection_but_queued_empty_is_allowed(self):
        self.rows = []
        original = self.transport
        def absent(url, method, pin, body=None):
            page = original(url, method, pin, body)
            if body and 'COUNT(*)' not in body.decode():
                page.pop('columns', None)
            return page
        with patch.object(qs, '_transport', side_effect=absent):
            with self.assertRaises(QueryError) as caught:
                self.run_export(key='missing-columns')
        self.assertEqual(caught.exception.code, 'EXPORT_PAGE_SCHEMA_MISMATCH')
        self.assert_settled()
        def queued(url, method, pin, body=None):
            if method == 'GET':
                return dict(id='select', columns=self.columns(), data=[])
            page = original(url, method, pin, body)
            if body and 'COUNT(*)' not in body.decode():
                page.pop('columns', None)
                page['nextUri'] = 'http://127.0.0.1:1234/v1/statement/select/1'
            return page
        with patch.object(qs, '_transport', side_effect=queued):
            operation = self.run_export(key='queued-empty')
        self.assertEqual(self.read(operation)['content'], b'"id"\n')

    def start_cancel_and_wait_marker(self, execution):
        errors = []
        def cancel():
            try:
                execution.cancel()
            except Exception as error:
                errors.append(error)
        canceler = threading.Thread(target=cancel, daemon=True)
        canceler.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with control_store.connect() as db:
                state = db.execute('SELECT state FROM dataops_operations WHERE id=?', (execution.id,)).fetchone()[0]
            if state == 'CANCELING':
                return canceler, errors
            time.sleep(.005)
        self.fail('cancel did not persist CANCELING')

    def test_durable_canceling_inside_publish_transaction_cannot_be_overwritten(self):
        threads = []
        original = ds.Execution._successful_work
        def interrupt(execution):
            original(execution)
            threads.append(self.start_cancel_and_wait_marker(execution))
        with patch.object(ds.Execution, '_successful_work', interrupt):
            with self.assertRaises(QueryError) as caught:
                self.run_export()
        self.assertEqual(caught.exception.code, 'ADMISSION_CONFLICT')
        for canceler, errors in threads:
            canceler.join(3)
            self.assertFalse(canceler.is_alive())
            self.assertFalse(errors)
        self.assert_settled('CANCELED')
        self.assertFalse(list(export._directory().iterdir()))
        with self.assertRaises(QueryError):
            self.read(self.run_export())

    def test_cancel_keeps_parent_admission_until_file_finalization_stops(self):
        threads = []
        original = export._write
        def cancel_during_write(stream, *args):
            with control_store.connect() as db:
                operation_id = db.execute('SELECT id FROM dataops_operations').fetchone()[0]
            execution = ds.Execution(self.principal, operation_id)
            threads.append(self.start_cancel_and_wait_marker(execution))
            with control_store.connect() as db:
                parent = admission.get_for_subject(db, kind='dataops', subject_id=operation_id, warehouse_id='w')
                self.assertEqual(db.execute('SELECT state FROM warehouse_admissions WHERE id=?', (parent.id,)).fetchone()[0], 'ACTIVE')
                self.assertTrue(admission.blockers(db, 'w'))
            self.assertTrue(threads[0][0].is_alive())
            return original(stream, *args)
        with patch.object(export, '_write', cancel_during_write):
            with self.assertRaises(QueryError):
                self.run_export()
        for canceler, errors in threads:
            canceler.join(3)
            self.assertFalse(canceler.is_alive())
            self.assertFalse(errors)
        self.assert_settled('CANCELED')

    def test_missing_artifact_intent_rolls_back_parent_completion(self):
        original = ds.Execution._successful_work
        def remove_intent(execution):
            original(execution)
            with control_store.connect() as db:
                db.execute('DELETE FROM export_artifacts WHERE operation_id=?', (execution.id,))
        with patch.object(ds.Execution, '_successful_work', remove_intent):
            with self.assertRaises(QueryError) as caught:
                self.run_export()
        self.assertEqual(caught.exception.code, 'EXPORT_NOT_COMPLETE')
        self.assert_settled()
        self.assertFalse(list(export._directory().iterdir()))

    def test_float_range_validation_retains_valid_original_tokens(self):
        valid = [('double', '1.7976931348623157e308'), ('double', '5e-324'),
                 ('float', '3.4028235E38'), ('float', '1.4E-45'),
                 ('float', '-0.0'), ('double', '-0.0')]
        for kind, value in valid:
            self.assertEqual(export._token(value, kind), value)
        for kind, value in [('double', '1e9999'), ('double', '1e-9999'),
                            ('float', '3.5e38'), ('float', '1e-46'),
                            ('float', 'NaN'), ('double', 'Infinity')]:
            with self.subTest(kind=kind, value=value), self.assertRaises(QueryError) as caught:
                export._token(value, kind)
            self.assertEqual(caught.exception.code, 'EXPORT_VALUE_INVALID')


if __name__ == '__main__':
    unittest.main()
