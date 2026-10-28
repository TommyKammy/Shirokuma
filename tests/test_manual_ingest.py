"""Manual adapter contracts; engine verification belongs to the Commander."""
from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store
import data_management as dm
import dataops_service as ds
import lake_ingest as lake
import manual_ingest as manual
import query_service as qs
from query_context import QueryError
import request_auth
import warehouse_admission as admission
import test_dataops_service as boundary


class ManualIngestTests(unittest.TestCase):

    def test_retirement_after_first_sql_preserves_multistep_ingest(self):
        retired = []
        def retire(sql):
            if sql and not retired:
                retired.append(True)
                self.fixture.retire()
        self.fault = retire
        with self.assertRaises((QueryError, PermissionError)):
            self.run_ingest(batch_size=1)
        with control_store.connect() as db:
            operation_id = db.execute('SELECT id FROM dataops_operations').fetchone()[0]
        operation = ds.Execution(self.principal, operation_id)._inspect()
        self.assertEqual(operation['state'], 'FINISHED')
        self.assertEqual(operation['result']['committed_rows'], 3)
        self.fixture.assert_database_admissions()
        with self.assertRaises((QueryError, PermissionError)):
            ds.Execution(self.principal, operation['id']).inspect()

    def test_private_same_execution_can_observe_finished_ingest_after_retirement(self):
        execution = self.fixture.begin()
        retired = []
        def retire(sql):
            if sql and not retired:
                retired.append(True)
                self.fixture.retire()
        self.fault = retire
        operation = self.run_ingest(batch_size=1, _execution=execution)
        self.assertEqual(operation['id'], execution.id)
        self.assertEqual(operation['state'], 'FINISHED')
        self.assertEqual(operation['result']['committed_rows'], 3)
        with self.assertRaises(QueryError):
            execution.inspect()

    def setUp(self):
        self.fixture = boundary.DataOpsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.principal = self.fixture.principal
        self.columns = (('id', 'BIGINT'), ('label', 'VARCHAR'))
        self.bad_keys = False
        self.fault = None
        self.schema_change = False
        self.describes = 0
        self.patch = patch.object(qs, '_transport', side_effect=self.transport)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def transport(self, url, method, pin, body=None):
        sql = body.decode() if body else ''
        result = self.fixture.transport(url, method, pin, body)
        if self.fault is not None:
            override = self.fault(sql)
            if override is not None:
                return override
        if sql.startswith('DESCRIBE'):
            self.describes += 1
            result['data'] = [list(c) for c in self.columns]
            if self.schema_change and self.describes > 1:
                result['data'].append(['extra', 'VARCHAR'])
        elif sql.startswith('SELECT'):
            result['data'] = [[None]] if self.bad_keys else []
        elif sql.startswith('CREATE OR REPLACE TABLE'):
            names = re.findall(r'"([^"]+)"', sql)[:3]
            self.fixture.objects[(*names, 'tables')] = {'table-uuid': str(uuid.uuid4())}
        return result

    def run_ingest(self, key='ingest', mode='create', **kwargs):
        field = 'destination' if mode == 'create' else 'target'
        if field not in kwargs:
            kwargs[field] = self.fixture.locator('fresh') if mode == 'create' else self.fixture.existing()
        kwargs.setdefault('filename', 'data.csv')
        kwargs.setdefault('content', 'id,label\n1,001\n2,\n3,text\n')
        return lake.manual_ingest(self.principal, kwargs.pop('context', self.fixture.context()),
            idempotency_key=key, mode=mode, **kwargs)

    def sql(self):
        return [sql for method, sql, _ in self.fixture.calls if method == 'POST']

    def test_three_ready_databases_names_and_row_counts(self):
        for index, dbid in enumerate(self.fixture.databases):
            result = self.run_ingest(str(index), context=self.fixture.context(dbid),
                destination=self.fixture.locator('arbitrary_' + str(index), dbid),
                content='id,label\n' + ''.join(f'{i},00{i}\n' for i in range(index + 4)), batch_size=2)
            self.assertEqual(result['state'], 'FINISHED')
            self.assertTrue(result['committed'])
            self.assertEqual(result['result']['committed_rows'], index + 4)
            self.assertIsNotNone(result['result']['target'])
        statements = self.sql()
        self.assertFalse(any('CREATE SCHEMA' in s for s in statements))
        self.assertTrue(any('"research"."sandbox"' in s for s in statements))
        with control_store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM warehouse_admissions WHERE kind='dataops'").fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dataops_queries WHERE commit_result IS NOT NULL').fetchone()[0], 3)

    def test_equivalent_file_upload_and_replay_no_sql(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'anything.csv'
            path.write_bytes(b'id,label\n1,001\n')
            locator = self.fixture.locator()
            first = lake.manual_ingest(self.principal, self.fixture.context(), idempotency_key='same',
                mode='create', destination=locator, file=path)
            calls = len(self.sql())
            again = self.run_ingest('same', destination=locator, filename='other.csv', content=path.read_bytes())
            self.assertEqual(first['id'], again['id'])
            self.assertTrue(again['replayed'])
            self.assertEqual(len(self.sql()), calls)
            path.write_bytes(b'broken,"unterminated')
            with self.assertRaises(QueryError) as caught:
                lake.manual_ingest(self.principal, self.fixture.context(), idempotency_key='same',
                    mode='create', destination=locator, file=path)
            self.assertEqual(caught.exception.status, 409)
            self.assertEqual(len(self.sql()), calls)

    def test_json_array_jsonl_equivalence_and_exact_values(self):
        for index, (filename, content) in enumerate((('x.json', '[{"id":1,"label":"001"},{"id":2,"label":null},{"id":3,"label":""}]'),
            ('x.jsonl', '{"id":1,"label":"001"}\n{"id":2,"label":null}\n{"id":3,"label":""}\n'))):
            result = self.run_ingest('json-' + str(index), destination=self.fixture.locator('j' + str(index)),
                                     filename=filename, content=content)
            self.assertEqual(result['result']['committed_rows'], 3)
        values = '\n'.join(self.sql())
        self.assertIn("CAST('001' AS VARCHAR)", values)
        self.assertIn('CAST(NULL AS VARCHAR)', values)
        self.assertIn("CAST('' AS VARCHAR)", values)

    def test_json_file_upload_replay_and_inferred_decimal_no_float(self):
        raw = b'[{"amount":123456789012345678901234567890123456.78,"label":"001"}]'
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'data.json'
            path.write_bytes(raw)
            locator = self.fixture.locator()
            first = lake.manual_ingest(self.principal, self.fixture.context(), idempotency_key='json-file',
                mode='create', destination=locator, file=path)
            calls = len(self.sql())
            second = self.run_ingest('json-file', destination=locator, filename='uploaded.jsonl', content=raw)
            self.assertEqual(first['id'], second['id'])
            self.assertEqual(len(self.sql()), calls)
        self.assertIn("DECIMAL '123456789012345678901234567890123456.78'", '\n'.join(self.sql()))
        self.assertIn('DECIMAL(38,2)', '\n'.join(self.sql()))

    def test_create_conflicts_table_and_uuidless_view(self):
        for kind in ('tables', 'views'):
            name = 'conflict_' + kind
            self.fixture.objects[('sales', 'raw', name, kind)] = {}
            with self.assertRaises(QueryError) as caught:
                self.run_ingest(name, destination=self.fixture.locator(name))
            self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.sql(), [])

    def test_explicit_ref_required_and_no_implicit_schema_or_table(self):
        for mode in ('append', 'replace', 'upsert'):
            with self.assertRaises(ValueError):
                self.run_ingest(mode, mode, target=self.fixture.locator())
        self.assertFalse(self.sql())

    def test_stale_recreated_and_cross_database_refs(self):
        ref = self.fixture.existing()
        self.fixture.objects[('sales', 'raw', 'observed_data', 'tables')]['table-uuid'] = str(uuid.uuid4())
        for index, target in enumerate((ref, self.fixture.existing(database=self.fixture.databases[1]),
                                       replace(ref, incarnation=ref.incarnation + 1))):
            with self.assertRaises(QueryError) as caught:
                self.run_ingest('stale' + str(index), 'append', target=target)
            self.assertEqual(caught.exception.status, 409)
        self.assertFalse(self.sql())

    def test_viewer_unauthorized_and_admin_same_database_bound(self):
        with self.assertRaises((QueryError, PermissionError)):
            self.run_ingest(context=self.fixture.context(role='bi_viewer'))
        with self.assertRaises(QueryError):
            self.run_ingest('cross', destination=self.fixture.locator(database=self.fixture.databases[1]))
        admin = self.fixture.fixture.principal('owner')
        context = self.fixture.context(principal=admin, role='admin')
        with self.assertRaises(QueryError):
            lake.manual_ingest(admin, context, idempotency_key='admin-cross', mode='create',
                destination=self.fixture.locator(database=self.fixture.databases[1]), filename='x.csv', content='id\n1\n')
        with self.assertRaises(QueryError):
            self.run_ingest('marts', destination=self.fixture.locator(schema='marts'))

    def test_target_types_and_order_override_inference(self):
        self.columns = (('label', 'VARCHAR'), ('id', 'BIGINT'))
        result = self.run_ingest(mode='append')
        self.assertEqual(result['result']['columns'][0]['name'], 'label')
        self.assertIn("(CAST('001' AS VARCHAR), CAST(1 AS BIGINT))", '\n'.join(self.sql()))
        self.assertFalse(result['result']['atomic'])

    def test_decimal38_and_typed_key_last_wins(self):
        self.columns = (('id', 'DECIMAL(38,2)'), ('label', 'VARCHAR'), ('omitted', 'INTEGER'))
        result = self.run_ingest(mode='upsert', key_columns=['id'], content='id,label\n1.001,first\n1.004,last\n123456789012345678901234567890123456.78,wide\n')
        self.assertEqual(result['result']['accepted_rows'], 3)
        self.assertEqual(result['result']['committed_rows'], 2)
        values = '\n'.join(self.sql())
        self.assertNotIn("'first'", values)
        self.assertIn("DECIMAL '123456789012345678901234567890123456.78'", values)
        merge = next(s for s in self.sql() if s.startswith('MERGE INTO'))
        self.assertIn('WHEN MATCHED THEN UPDATE SET "label" = s."label"', merge)
        self.assertIn('WHEN NOT MATCHED THEN INSERT ("id", "label")', merge)
        self.assertNotIn('"omitted"', merge)
        self.assertNotIn('CREATE OR REPLACE', values)
        self.assertNotIn('CREATE TABLE IF NOT EXISTS', values)

    def test_upsert_key_only_has_no_matched_update_and_retains_uuid(self):
        self.columns = (('id', 'BIGINT'), ('omitted', 'VARCHAR'))
        target = self.fixture.existing()
        result = self.run_ingest(mode='upsert', target=target, key_columns=['id'], content='id\n7\n11\n')
        merge = next(s for s in self.sql() if s.startswith('MERGE INTO'))
        self.assertNotIn('WHEN MATCHED', merge)
        self.assertIn('WHEN NOT MATCHED THEN INSERT ("id") VALUES (s."id")', merge)
        self.assertEqual(result['result']['target'], asdict(target))

    def test_replace_preserves_overlapping_decimal_timestamp_types_and_target_order(self):
        self.columns = (('amount', 'DECIMAL(20,2)'), ('created_at', 'TIMESTAMP(6)'), ('id', 'BIGINT'))
        result = self.run_ingest(mode='replace', batch_size=1,
            content='id,created_at,amount\n1,2026-10-20 12:34:56.123456,9007199254740993.25\n'
                    '2,2026-10-20 12:34:57.654321,9007199254740995.75\n')
        self.assertTrue(result['committed'])
        self.assertEqual(result['result']['columns'], [dict(name=n, type=t) for n, t in self.columns])
        values = '\n'.join(self.sql())
        self.assertIn("DECIMAL '9007199254740993.25'", values)
        self.assertIn("DECIMAL '9007199254740995.75'", values)
        self.assertIn("TIMESTAMP '2026-10-20 12:34:57.654321'", values)
        self.assertNotIn(' AS DOUBLE', values)
        self.assertIn('SELECT "amount", "created_at", "id" FROM', values)

    def test_existing_conflicting_hint_is_rejected_before_stage(self):
        self.columns = (('amount', 'DECIMAL(20,2)'),)
        for index, mode in enumerate(('append', 'replace', 'upsert')):
            with self.assertRaisesRegex(ValueError, 'conflicts'):
                self.run_ingest('hint-conflict-' + str(index), mode, content='amount\n9007199254740993.25\n',
                    schema_hints={'amount': 'DOUBLE'}, key_columns=['amount'] if mode == 'upsert' else [])
        self.assertTrue(all(s.startswith('DESCRIBE') for s in self.sql()))

    def test_json_leading_whitespace_bom_and_malformed_replay_conflict(self):
        raw = b'\xef\xbb\xbf' + b' ' * 350 + b'[{"id":1,"label":"001"}]'
        locator = self.fixture.locator()
        first = self.run_ingest('whitespace', destination=locator, filename='x.json', content=raw)
        self.assertEqual(first['result']['format'], 'json')
        count = len(self.sql())
        with self.assertRaises(QueryError) as caught:
            self.run_ingest('whitespace', destination=locator, filename='x.json', content=b' ' * 400 + b'[{broken')
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(len(self.sql()), count)

    def test_exact_no_hint_numeric_create_csv_and_json(self):
        for index, (filename, content) in enumerate((
            ('x.csv', 'amount,large_id\n9007199254740993.25,12345678901234567890123456789012345678\n'),
            ('x.json', '[{"amount":9007199254740993.25,"large_id":12345678901234567890123456789012345678}]'))):
            result = self.run_ingest('exact-' + str(index), destination=self.fixture.locator('exact_' + str(index)),
                                     filename=filename, content=content)
            self.assertTrue(result['committed'])
        sql = '\n'.join(self.sql())
        self.assertIn("DECIMAL '9007199254740993.25'", sql)
        self.assertIn("DECIMAL '12345678901234567890123456789012345678'", sql)
        count = len(self.sql())
        with self.assertRaises(ValueError):
            self.run_ingest('too-large', content='amount\n' + '9' * 39 + '\n')
        self.assertEqual(len(self.sql()), count)

    def test_receipt_separates_validation_types_from_observed_engine_types(self):
        self.columns = (('small', 'INTEGER'), ('ts', 'TIMESTAMP(6)'))
        hints = {'small': 'TINYINT', 'ts': 'TIMESTAMP(0)'}
        result = self.run_ingest(content='small,ts\n7,2026-10-20 12:34:56\n', schema_hints=hints)
        receipt = result['result']
        self.assertEqual(receipt['schema_hints'], hints)
        self.assertEqual(receipt['columns'], [dict(name=n, type=t) for n, t in hints.items()])
        self.assertEqual(receipt['observed_columns'], [dict(name=n, type=t) for n, t in self.columns])

    def test_existing_system_python_can_import_boundary_and_manual_adapter(self):
        interpreter = Path('/usr/bin/python3')
        if not interpreter.exists():
            self.skipTest('system Python not available')
        result = subprocess.run([str(interpreter), '-c', 'import dataops_service,manual_ingest'],
            env=dict(os.environ, PYTHONPATH=str(Path(ds.__file__).parent)), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_duplicate_and_null_keys_reject_before_mutation(self):
        self.bad_keys = True
        with self.assertRaises(ValueError):
            self.run_ingest(mode='upsert', key_columns=['id'])
        self.assertTrue(all(s.startswith(('DESCRIBE', 'SELECT')) for s in self.sql()))

    def test_invalid_input_keys_reject_before_mutation(self):
        with self.assertRaises(ValueError):
            self.run_ingest(mode='upsert', key_columns=['id'], content='id,label\n,empty\n')
        self.assertTrue(all(s.startswith(('DESCRIBE', 'SELECT')) for s in self.sql()))

    def test_timestamp_precisions_and_offset_validation(self):
        for precision in range(7):
            fraction = '.' + '123456'[:precision] if precision else ''
            result = self.run_ingest('ts' + str(precision), destination=self.fixture.locator('ts' + str(precision)),
                content='ts\n2026-10-20 12:34:56' + fraction + '\n', schema_hints={'ts': f'TIMESTAMP({precision})'})
            self.assertTrue(result['committed'])
        calls = len(self.sql())
        for index, value in enumerate(('2026-10-20 12:34:56+09:00', '2026-10-20 12:34:56.000001')):
            with self.assertRaises(ValueError):
                self.run_ingest('offset' + str(index), content='ts\n' + value + '\n', schema_hints={'ts': 'TIMESTAMP(0)'})
        self.assertEqual(len(self.sql()), calls)

    def test_invalid_values_all_rows_before_stage(self):
        for index, (kind, value) in enumerate((('BOOLEAN', 'oops'), ('DOUBLE', 'nan'), ('INTEGER', '2147483648'),
                ('DECIMAL(3,1)', '1000'), ('DATE', '2026-02-31'), ('VARCHAR', 'a\x00b'))):
            with self.assertRaises((ValueError, OverflowError)):
                self.run_ingest('bad' + str(index), content='x\n1\n' + value + '\n', schema_hints={'x': kind}, batch_size=1)
        self.assertFalse(self.sql())

    def test_unsupported_or_missing_hint_not_discarded(self):
        for index, hints in enumerate(({'id': 'ARRAY(BIGINT)'}, {'missing': 'VARCHAR'})):
            with self.assertRaises(ValueError):
                self.run_ingest(str(index), schema_hints=hints)
        self.assertFalse(self.sql())

    def test_unicode_reserved_long_and_quoted_column_names(self):
        name = 'あ' * 40 + '"quoted'
        result = self.run_ingest(filename='x.json', content=json.dumps([{'select': '001', name: 'x'}]))
        self.assertTrue(result['committed'])
        self.assertIn('"' + name.replace('"', '""') + '"', '\n'.join(self.sql()))

    def test_input_bytes_and_mutable_options_fixed_after_acceptance(self):
        hints = {'label': 'VARCHAR'}
        keys = []
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'data.csv'
            path.write_text('id,label\n1,001\n')
            original = ds.begin
            def mutate(*args, **kwargs):
                result = original(*args, **kwargs)
                hints['label'] = 'BOOLEAN'
                keys.append('label')
                path.write_text('id,label\n99,changed\n')
                return result
            with patch.object(ds, 'begin', side_effect=mutate):
                result = lake.manual_ingest(self.principal, self.fixture.context(), idempotency_key='frozen',
                    mode='create', destination=self.fixture.locator(), file=path, schema_hints=hints, key_columns=keys)
            self.assertTrue(result['committed'])
            self.assertIn("'001'", '\n'.join(self.sql()))
            self.assertNotIn('changed', '\n'.join(self.sql()))

    def test_atomic_load_failure_never_publishes_existing_target(self):
        for index, mode in enumerate(('replace', 'append', 'upsert')):
            load_count = 0
            def fail_load(sql):
                nonlocal load_count
                if sql.startswith('INSERT INTO') and '."sandbox".' in sql:
                    load_count += 1
                    if load_count == 2:
                        return {'id': 'load_failed', 'error': {'message': 'failure'}}
            self.fault = fail_load
            start = len(self.sql())
            result = self.run_ingest('load' + str(index), mode, target=self.fixture.existing('target_' + str(index)),
                atomic_append=True, key_columns=['id'], batch_size=1)
            self.assertFalse(result['committed'])
            self.assertEqual(result['result']['committed_rows'], 0)
            self.assertFalse(any(s.startswith('CREATE OR REPLACE') or 'INSERT INTO "sales"."raw"' in s
                                 for s in self.sql()[start:]))

    def test_ordinary_append_reports_successful_partial_batches(self):
        count = 0
        def fail_second(sql):
            nonlocal count
            if sql.startswith('INSERT INTO'):
                count += 1
                if count == 2:
                    return {'id': 'failed_second', 'error': { 'message': 'failure'}}
        self.fault = fail_second
        result = self.run_ingest(mode='append', batch_size=1)
        self.assertEqual(result['state'], 'FAILED')
        self.assertTrue(result['committed'])
        self.assertEqual(result['result']['committed_rows'], 1)
        self.assertEqual(result['result']['committed_batches'], 1)
        self.assertFalse(result['result']['atomic'])

    def test_schema_change_before_publication_rejects(self):
        self.schema_change = True
        result = self.run_ingest(mode='replace')
        self.assertFalse(result['committed'])
        self.assertFalse(any(s.startswith('CREATE OR REPLACE') for s in self.sql()))

    def test_recreated_uuid_after_stage_load_denies_publication(self):
        ref = self.fixture.existing()
        def recreate(sql):
            if sql.startswith('INSERT INTO'):
                self.fixture.objects[('sales', 'raw', 'observed_data', 'tables')]['table-uuid'] = str(uuid.uuid4())
        self.fault = recreate
        result = self.run_ingest(mode='replace', target=ref)
        self.assertFalse(result['committed'])
        self.assertFalse(any(s.startswith('CREATE OR REPLACE') for s in self.sql()))

    def test_unknown_later_append_preserves_prior_committed_count(self):
        count = 0
        def lose(sql):
            nonlocal count
            if sql.startswith('INSERT INTO'):
                count += 1
                if count == 2:
                    raise QueryError('QUERY_BACKEND_FAILED', 502)
        self.fault = lose
        result = self.run_ingest(mode='append', batch_size=1)
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertTrue(result['committed'])
        self.assertEqual(result['result']['committed_rows'], 1)
        self.assertEqual(result['result']['committed_batches'], 1)

    def test_engineer_rename_remains_admin_only(self):
        execution = self.fixture.begin()
        with self.assertRaises(QueryError) as caught:
            execution.rename(self.fixture.existing(), self.fixture.locator('renamed'), step='rename')
        self.assertEqual(caught.exception.status, 403)

    def test_admin_can_ingest_raw_but_not_marts(self):
        owner = self.fixture.fixture.principal('owner')
        context = self.fixture.context(principal=owner, role='admin')
        result = lake.manual_ingest(owner, context, idempotency_key='admin', mode='create',
            destination=self.fixture.locator('admin_created'), filename='x.csv', content='id\n7\n')
        self.assertTrue(result['committed'])
        with self.assertRaises(QueryError) as caught:
            lake.manual_ingest(owner, context, idempotency_key='admin-marts', mode='create',
                destination=self.fixture.locator('admin_created', schema='marts'), filename='x.csv', content='id\n7\n')
        self.assertEqual(caught.exception.status, 403)

    def test_reconcile_finished_commit_marks_receipt_without_reposting(self):
        def lose(sql):
            if sql.startswith('CREATE OR REPLACE'):
                raise QueryError('QUERY_BACKEND_FAILED', 502)
        self.fault = lose
        result = self.run_ingest(mode='replace')
        commit = next(q for q in result['queries'] if q['step'] == 'commit')
        def observer(expected):
            return [dict(id='known_commit', source=expected['source'], user=expected['backend_username'],
                         sql=expected['sql'], state='FINISHED')]
        calls = len(self.sql())
        qs.reconcile_query(self.principal, commit['id'], selected_role='data_engineer', observer=observer)
        snapshot = ds.Execution(self.principal, result['id']).inspect()
        self.assertTrue(snapshot['committed'])
        self.assertEqual(snapshot['result']['committed_rows'], 3)
        self.assertEqual(snapshot['state'], 'UNKNOWN')
        self.assertEqual(len(self.sql()), calls)

    def test_commit_fault_is_not_reported_committed(self):
        self.fault = lambda s: {'id': 'commit_failed', 'error': {'message': 'failed'}} if s.startswith('CREATE OR REPLACE') else None
        result = self.run_ingest(mode='replace')
        self.assertFalse(result['committed'])
        self.assertEqual(result['result']['committed_rows'], 0)

    def test_after_commit_observation_failure_preserves_receipt(self):
        original = ds.Execution._observe
        def fail_target(execution, locator):
            if locator.physical_name == 'fresh':
                raise QueryError('CATALOG_UNAVAILABLE', 502)
            return original(execution, locator)
        with patch.object(ds.Execution, '_observe', fail_target):
            result = self.run_ingest()
        self.assertTrue(result['committed'])
        self.assertEqual(result['result']['committed_rows'], 3)
        count = len(self.sql())
        self.assertEqual(self.run_ingest()['id'], result['id'])
        self.assertEqual(len(self.sql()), count)

    def test_after_commit_cleanup_failure_is_separate(self):
        self.fault = lambda s: {'id': 'cleanup_failed', 'error': {'message': 'failed'}} if s.startswith('DROP') else None
        result = self.run_ingest()
        self.assertTrue(result['committed'])
        self.assertEqual(result['result']['committed_rows'], 3)
        self.assertEqual(result['result']['cleanup'], 'failed')
        self.assertIsNotNone(result['cleanup_error'])

    def test_after_commit_cleanup_response_loss_remains_unknown_no_resend(self):
        def lose(sql):
            if sql.startswith('DROP'):
                raise QueryError('QUERY_BACKEND_FAILED', 502)
        self.fault = lose
        result = self.run_ingest()
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertTrue(result['committed'])
        self.assertEqual(result['result']['committed_rows'], 3)
        self.assertEqual(result['result']['cleanup'], 'unknown')
        count = len(self.sql())
        self.assertEqual(self.run_ingest()['id'], result['id'])
        self.assertEqual(len(self.sql()), count)

    def test_after_commit_bookkeeping_failure_keeps_durable_commit(self):
        original = manual._persist
        def fail_committed(execution, receipt):
            if receipt['committed_rows']:
                raise OSError('receipt bookkeeping fault')
            return original(execution, receipt)
        with patch.object(manual, '_persist', side_effect=fail_committed):
            result = self.run_ingest()
        self.assertTrue(result['committed'])
        self.assertEqual(result['result']['committed_rows'], 3)
        count = len(self.sql())
        self.assertEqual(self.run_ingest()['id'], result['id'])
        self.assertEqual(len(self.sql()), count)

    def test_unknown_commit_replay_and_target_claim_never_stolen(self):
        def lose_commit(sql):
            if sql.startswith('CREATE OR REPLACE'):
                raise QueryError('QUERY_BACKEND_FAILED', 502)
        self.fault = lose_commit
        ref = self.fixture.existing()
        result = self.run_ingest(mode='replace', target=ref)
        self.assertEqual(result['state'], 'UNKNOWN')
        calls = len(self.sql())
        self.assertEqual(self.run_ingest(mode='replace', target=ref)['id'], result['id'])
        self.assertEqual(len(self.sql()), calls)
        with self.assertRaises(QueryError) as caught:
            self.run_ingest('other', mode='append', target=ref)
        self.assertEqual(caught.exception.code, 'TARGET_BUSY')
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT operation_id FROM dataops_target_claims WHERE table_name=?', ('observed_data',)).fetchone()[0], result['id'])

    def test_process_loss_after_finished_commit_retains_success_no_send(self):
        original = ds.Execution._observe
        def crash(execution, locator):
            if locator.physical_name == 'fresh':
                raise SystemExit(23)
            return original(execution, locator)
        with patch.object(ds.Execution, '_observe', crash):
            with self.assertRaises(SystemExit):
                self.run_ingest()
        self.fixture.clear_pins()
        calls = len(self.sql())
        replay = self.run_ingest()
        self.assertEqual(replay['state'], 'UNKNOWN')
        self.assertTrue(replay['committed'])
        self.assertEqual(replay['result']['committed_rows'], 3)
        self.assertEqual(len(self.sql()), calls)

    def test_grant_regrant_denies_replay_and_results(self):
        result = self.run_ingest()
        with control_store.connect() as db:
            dm.update_grant(db, 'alice', self.fixture.databases[0], 1, 'revoked')
            dm.update_grant(db, 'alice', self.fixture.databases[0], 2, 'data_engineer')
        with self.assertRaises(QueryError):
            ds.Execution(self.principal, result['id']).inspect()
        with self.assertRaises(QueryError):
            qs.inspect_query(self.principal, result['queries'][0]['id'], selected_role='data_engineer')

    def test_terminal_claim_reclaimable_by_next_operation(self):
        ref = self.fixture.existing()
        first = self.run_ingest('first', 'append', target=ref)
        second = self.run_ingest('second', 'append', target=ref)
        self.assertEqual(first['state'], 'FINISHED')
        self.assertEqual(second['state'], 'FINISHED')
        with control_store.connect() as db:
            row = db.execute('SELECT operation_id FROM dataops_target_claims WHERE table_name=?', ('observed_data',)).fetchone()
        self.assertEqual(row[0], second['id'])

    def test_actual_subprocess_lost_commit_intent_no_repost(self):
        # Child really exits after the commit intent transaction, before response.
        locator = self.fixture.locator('crash_target')
        script = '''
import os,json,uuid
from contextlib import contextmanager
import lake_ingest,query_service as qs,data_management as dm,database_inventory as inv,request_auth
from query_context import ResolvedQueryContext
c=ResolvedQueryContext(**json.loads(os.environ['INGEST_CONTEXT']))
p=request_auth.AuthenticatedPrincipal('alice','data_engineer',False)
qs._capture_dataops=lambda p,c: qs._Pin(c,'http://127.0.0.1:1234',{'X-Trino-User':'fixed'},None)
objects={}
@contextmanager
def catalog(database,binding):
 def api(path):
  if path not in objects: raise inv.InventoryError('NOT_FOUND',404,'missing')
  return {'metadata':objects[path]}
 yield api
inv.catalog_api=catalog
def transport(url,method,pin,body=None):
 import re
 sql=body.decode()
 if sql.startswith('CREATE TABLE') and ' AS SELECT ' in sql: os._exit(23)
 if sql.startswith('CREATE TABLE'):
  parts=re.findall(r'"([^\"]+)"',sql)[:3]
  objects['/namespaces/'+parts[1]+'/tables/'+parts[2]]={'table-uuid':str(uuid.uuid4())}
 return {'id':'child_'+str(uuid.uuid4())}
qs._transport=transport
lake_ingest.manual_ingest(p,c,idempotency_key='crash',mode='create',destination=dm.DestinationLocator(**json.loads(os.environ['INGEST_LOCATOR'])),filename='data.csv',content='id\\n1\\n')
'''
        environment = dict(os.environ, PYTHONPATH=str(Path(ds.__file__).parent),
            INGEST_CONTEXT=json.dumps(asdict(self.fixture.context())), INGEST_LOCATOR=json.dumps(asdict(locator)))
        child = subprocess.run([sys.executable, '-c', script], env=environment, capture_output=True, text=True)
        self.assertEqual(child.returncode, 23, child.stderr)
        replay = self.run_ingest('crash', destination=locator, filename='data.csv', content='id\n1\n')
        self.assertEqual(replay['state'], 'UNKNOWN')
        self.assertFalse(replay['committed'])
        self.assertFalse(self.sql())
        with control_store.connect() as db:
            self.assertIsNotNone(db.execute('SELECT commit_result FROM dataops_queries WHERE operation_id=? AND step=?', (replay['id'], 'commit')).fetchone()[0])


class TypedValueTests(unittest.TestCase):
    def test_exact_conversions(self):
        self.assertEqual(manual.typed_value('001', 'VARCHAR'), '001')
        self.assertEqual(manual.typed_value('', 'VARCHAR'), '')
        self.assertIsNone(manual.typed_value(None, 'VARCHAR'))
        self.assertEqual(manual.typed_value('1.005', 'DECIMAL(38,2)'), Decimal('1.01'))
        self.assertEqual(manual.typed_value('01', 'BIGINT'), manual.typed_value('1.0', 'BIGINT'))
        for precision in range(7):
            self.assertEqual(manual.typed_value('2026-01-01 00:00:00', f'TIMESTAMP({precision})'), datetime(2026, 1, 1))

    def test_invalid_types(self):
        for value, kind in ((True, 'INTEGER'), ('TRUEish', 'BOOLEAN'), ('inf', 'DOUBLE'),
                            ('128', 'TINYINT'), ('999999999999999999999999999999999999999', 'DECIMAL(38,0)'),
                            ('2026-01-01 00:00:00Z', 'TIMESTAMP(6)')):
            with self.assertRaises((ValueError, InvalidOperation, OverflowError)):
                manual.typed_value(value, kind)
