import contextlib
import base64
import csv
from decimal import Decimal
import io
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'local_experiment'))
import lake_ingest as lake


class LakeIngestTests(unittest.TestCase):
    def test_upsert_casts_incoming_to_existing_decimal_types(self):
        self.execute.side_effect = lambda url, sql: {'rows': [['id', 'decimal(20,2)'],
            ['amount', 'decimal(38,8)']]} if sql.startswith('DESCRIBE') else {}
        lake.ingest(filename='orders.csv', content='id,amount\n1.0,1.25\n', mode='upsert', key='id')
        merged = self.statements()[-2]
        self.assertIn('CAST(t."id" AS decimal(20,2)) AS "id"', merged)
        self.assertIn('CAST(t."amount" AS decimal(38,8)) AS "amount"', merged)

    def test_decimal_deduplication_retains_last_equivalent_key(self):
        report = lake.ingest(filename='orders.csv', content='id,name\n1.0,first\n1.00,last\n-0.0,old\n0.00,new\n',
                             mode='upsert', key='id', schema_hints={'id': 'DECIMAL(20,2)'})
        self.assertEqual(report['rows_ingested'], 2)
        self.assertNotIn("'first'", self.statements()[1])
        self.assertNotIn("'old'", self.statements()[1])
        self.assertEqual(lake.literal('1.0', 'DECIMAL(20,2)'), lake.literal('1.00', 'DECIMAL(20,2)'))

    def test_json_decimal_precision_survives_loader_and_sql_literal(self):
        amount = '12345678901234567890.123456789012345678'
        for suffix, content in [('json', '[{"amount": ' + amount + '}]'),
                                ('jsonl', '{"amount": ' + amount + '}\n')]:
            with self.subTest(suffix=suffix):
                path = self.file('orders.' + suffix, content)
                hints = {'amount': 'DECIMAL(38,18)'}
                value = lake.read_file(path, schema_hints=hints)[2][0][0]
                self.assertIsInstance(value, Decimal)
                self.assertEqual(str(value), amount)
                lake.ingest(path, schema_hints=hints)
                self.assertIn(f"CAST('{amount}' AS DECIMAL(38,18))", self.statements()[-3])

    def test_nested_json_decimal_keeps_numeric_json_type(self):
        amount = '12345678901234567890.123456789012345678'
        path = self.file('nested.jsonl', '{"payload": {"amounts": [' + amount + ']}}')
        value = lake.read_file(path)[2][0][0]
        encoded = lake.json_text(value)
        self.assertEqual(json.loads(encoded, parse_float=Decimal)['amounts'], [Decimal(amount)])
        self.assertIn(amount, encoded)
        self.assertNotIn('"' + amount + '"', encoded)

    def test_export_full_and_limited_row_count_verification(self):
        rows = [[i] for i in range(10001)]
        for options, exported in (({}, rows), ({'max_rows': None}, rows),
                                  ({'full': True, 'max_rows': 2}, rows),
                                  ({'max_rows': 2}, rows[:2])):
            with self.subTest(options=options):
                self.execute.reset_mock()
                self.execute.side_effect = [dict(rows=[[len(rows)]]),
                                           dict(columns=[{'name': 'id'}], rows=exported)]
                report = lake.export_table('raw.orders', **options)
                suffix = ' LIMIT 2' if len(exported) == 2 else ''
                self.assertEqual(self.statements(), [
                    'SELECT COUNT(*) FROM "iceberg"."raw"."orders"',
                    'SELECT * FROM "iceberg"."raw"."orders"' + suffix])
                self.assertEqual((report['row_count'], report['total_rows'], report['truncated']),
                                 (len(exported), len(rows), len(exported) < len(rows)))

    def test_full_export_fetches_all_trino_pages(self):
        rows = [[i] for i in range(1501)]
        self.execute.return_value = dict(rows=rows)
        with patch.object(lake.wr, 'execute', wraps=self.real_execute), \
             patch.object(lake.wr, 'http_json', side_effect=[
                 dict(columns=[{'name': 'id'}], data=rows[:1000], nextUri='url/next'),
                 dict(data=rows[1000:])]) as http:
            report = lake.export_table('raw.orders')
        self.assertEqual((report['row_count'], report['total_rows'], report['truncated']),
                         (1501, 1501, False))
        self.assertEqual(http.call_count, 2)
        self.assertNotIn('LIMIT', http.call_args_list[0].args[1].decode())

    def test_staging_failure_preserves_target_and_cleans_up(self):
        path = self.file('orders.csv', 'id\n1\n2\n3\n')
        target = '"iceberg"."raw"."orders"'
        stage = '"iceberg"."raw"."orders__stage_abcd1234"'
        for mode in ('replace', 'upsert'):
            for failure in ('CREATE TABLE ' + stage, 'INSERT INTO ' + stage):
                with self.subTest(mode=mode, failure=failure):
                    self.execute.reset_mock()
                    def execute(url, statement):
                        if statement.startswith(failure):
                            raise RuntimeError('batch failed')
                        return {}
                    self.execute.side_effect = execute
                    with self.assertRaisesRegex(RuntimeError, 'batch failed'):
                        lake.ingest(path, mode=mode, key='id', batch_size=1)
                    statements = self.statements()
                    self.assertEqual(statements[-1], f'DROP TABLE IF EXISTS {stage}')
                    self.assertFalse(any(target in s for s in statements))
                    self.audit.assert_not_called()
                    self.assertEqual(self.telemetry.call_args.kwargs['status'], 'failed')

    def test_failed_swap_cleans_up_stage(self):
        target = '"iceberg"."raw"."orders"'
        def execute(url, statement):
            if statement.startswith('CREATE OR REPLACE TABLE ' + target):
                raise RuntimeError('swap failed')
            return {}
        self.execute.side_effect = execute
        with self.assertRaisesRegex(RuntimeError, 'swap failed'):
            lake.ingest(filename='orders.csv', content='id\n1\n2\n', batch_size=1)
        self.assertEqual(self.statements()[-1],
                         'DROP TABLE IF EXISTS "iceberg"."raw"."orders__stage_abcd1234"')
        self.audit.assert_not_called()

    def test_export_csv_jsonl_and_output_files(self):
        rows = [[1, 'a,"b\n雪', None], [2, '', True]]
        self.execute.return_value = dict(columns=[{'name': n} for n in ['id', 'text', 'value']], rows=rows)
        report = lake.export_table('marts.orders', max_rows=7)
        self.assertEqual(self.statements(), ['SELECT COUNT(*) FROM "iceberg"."marts"."orders"',
                                            'SELECT * FROM "iceberg"."marts"."orders" LIMIT 7'])
        self.assertEqual((report['action'], report['table'], report['filename'], report['row_count']),
                         ('export', 'iceberg.marts.orders', 'orders.csv', 2))
        self.assertEqual(list(csv.reader(io.StringIO(report['content']))),
                         [['id', 'text', 'value'], ['1', rows[0][1], ''], ['2', '', 'True']])
        self.telemetry.assert_called_once_with('export', report['duration_ms'], status='success', prefix='lake.')
        for suffix in ('csv', 'jsonl', 'ndjson', 'parquet'):
            if suffix == 'parquet':
                continue
            path = self.root / ('export.' + suffix)
            result = lake.export_table('iceberg.tpch_tiny.orders', file=path)
            self.assertEqual(result['file'], str(path))
            self.assertNotIn('content', result)
            if suffix != 'csv':
                self.assertEqual([json.loads(l) for l in path.read_text().splitlines()],
                                 [dict(zip(report['columns'], row)) for row in rows])
        result = lake.export_table('smoke.orders', format='jsonl')
        self.assertEqual(result['filename'], 'orders.jsonl')
        self.assertEqual(json.loads(result['content'].splitlines()[0])['value'], None)
        self.audit.assert_not_called()

    def test_export_empty_and_large_results(self):
        for rows in ([], [[i] for i in range(1501)]):
            self.execute.return_value = dict(columns=[{'name': 'id'}], rows=rows)
            result = lake.export_table('raw.orders')
            self.assertEqual(result['row_count'], len(rows))
            self.assertEqual(len(list(csv.reader(io.StringIO(result['content'])))), len(rows) + 1)
        self.execute.return_value['rows'] = []
        self.assertEqual(lake.export_table('raw.orders', format='jsonl')['content'], '')

    def test_export_invalid_inputs_before_access(self):
        for table in ('orders', 'other.raw.t', 'raw.bad-name', 'raw.t;DROP', None):
            with self.subTest(table=table), self.assertRaises(ValueError):
                lake.export_table(table)
        for kwargs in (dict(max_rows=True), dict(max_rows=0), dict(max_rows=-1), dict(max_rows=1.0),
                       dict(max_rows='5'), dict(format='xml'), dict(format=[]), dict(file='out.unknown')):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                lake.export_table('raw.orders', **kwargs)
        self.trino.assert_not_called()
        self.assertEqual(self.telemetry.call_args.args[0], 'export')
        self.assertEqual(self.telemetry.call_args.kwargs, dict(status='failed', prefix='lake.'))

    def test_export_parquet_optional_dependency_and_binary_payload(self):
        self.execute.return_value = dict(columns=[{'name': 'id'}], rows=[[1], [2]])
        with patch.dict(sys.modules, {'pyarrow': None, 'pyarrow.parquet': None}):
            with self.assertRaisesRegex(ValueError, 'parquet export requires pyarrow'):
                lake.export_table('raw.orders', format='parquet')
        arrow, parquet = MagicMock(), MagicMock()
        arrow.parquet = parquet
        parquet.write_table.side_effect = lambda table, stream: stream.write(b'PAR1\x00\xffPAR1')
        with patch.dict(sys.modules, {'pyarrow': arrow, 'pyarrow.parquet': parquet}):
            result = lake.export_table('raw.orders', format='parquet')
            arrow.table.assert_called_once_with({'id': [1, 2]})
            self.assertEqual(base64.b64decode(result['content_base64']), b'PAR1\x00\xffPAR1')
            self.assertNotIn('content', result)
            path = self.root / 'data.parquet'
            result = lake.export_table('raw.orders', file=path)
            self.assertEqual(result['format'], 'parquet')
            self.assertEqual(path.read_bytes(), b'PAR1\x00\xffPAR1')

    def test_export_query_and_file_failures_record_telemetry(self):
        self.execute.side_effect = RuntimeError('failed query')
        with self.assertRaisesRegex(RuntimeError, 'failed query'):
            lake.export_table('raw.orders')
        self.assertEqual(self.telemetry.call_args.kwargs['status'], 'failed')
        self.execute.side_effect = None
        self.execute.return_value = dict(columns=[{'name': 'id'}], rows=[[1]])
        with self.assertRaises(OSError):
            lake.export_table('raw.orders', self.root / 'missing' / 'out.csv')
        self.assertEqual(self.telemetry.call_args.kwargs['status'], 'failed')

    def test_export_cli_dispatch_and_errors(self):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['lake_ingest.py', '--export', 'raw.orders', '--output', 'out.csv',
                                     '--format', 'jsonl', '--max-rows', '7']), \
             patch.object(lake, 'export_table', return_value={'action': 'export'}) as export, \
             contextlib.redirect_stdout(output):
            self.assertEqual(lake.main(), 0)
        export.assert_called_once_with(table='raw.orders', file='out.csv', format='jsonl', max_rows=7)
        self.assertEqual(json.loads(output.getvalue()), {'action': 'export'})
        with patch.object(sys, 'argv', ['lake_ingest.py', '--export=raw.orders']), \
             patch.object(lake, 'export_table', side_effect=ValueError('invalid')) as export, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(lake.main(), 1)
        export.assert_called_once_with(table='raw.orders', file=None, format='auto', max_rows=None)
        with patch.object(sys, 'argv', ['lake_ingest.py', '--', '--export']), \
             patch.object(lake, 'ingest', return_value={'action': 'ingest'}) as ingest, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(lake.main(), 0)
        self.assertEqual(ingest.call_args.kwargs['file'], '--export')

    def enterContext(self, manager):
        value = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        return value

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.execute = self.enterContext(patch.object(lake.bears, 'sql', return_value={}))
        self.real_execute = lake.wr.execute
        self.enterContext(patch.object(lake.wr, 'execute',
                                      side_effect=lambda url, sql, headers, **kw: self.execute(url, sql)))
        self.enterContext(patch.object(lake.uuid, 'uuid4', return_value=SimpleNamespace(hex='abcd1234' * 4)))
        def execute(url, statement):
            if statement.startswith('SELECT COUNT(*)'):
                return dict(rows=[[len(self.execute.return_value['rows'])]])
            if statement.startswith('DESCRIBE '):
                create = next(s for s in reversed(self.statements())
                              if s.startswith('CREATE TABLE IF NOT EXISTS'))
                return dict(rows=re.findall(r'"([^"]+)" (DECIMAL\([^)]*\)|\w+)', create))
            return self.execute.return_value
        self.execute.side_effect = execute
        self.audit = self.enterContext(patch.object(lake.bears, 'audit_table', return_value={'row_count': 3}))
        self.telemetry = self.enterContext(patch.object(lake.wr, 'telemetry', return_value='recorded'))
        self.trino = self.enterContext(patch.object(lake.wr, 'trino', return_value=contextlib.nullcontext('url')))

    def file(self, name, content):
        path = self.root / name
        path.write_text(content)
        return path

    def statements(self):
        return [call.args[1] for call in self.execute.call_args_list]

    def test_preview_local_and_uploaded_content(self):
        content = 'Order ID,Active\n1,true\n2,false\n3,true\n'
        path = self.file('orders.csv', content)
        expected = dict(action='preview', file=str(path), format='csv',
                        columns=[dict(name='order_id', type='BIGINT'), dict(name='active', type='BOOLEAN')],
                        row_count=3, sample_rows=[['1', 'true'], ['2', 'false']])
        self.assertEqual(lake.preview(path, limit=2), expected)
        for value in (content, content.encode()):
            report = lake.preview(filename='../orders.csv', content=value, limit=2)
            self.assertEqual(report, dict(expected, file='orders.csv'))
        self.assertEqual(len(lake.preview(path, limit=0)['sample_rows']), 1)
        self.trino.assert_not_called()

    def test_upload_ingestion_preserves_modes_and_cleans_up(self):
        original = lake.read_file
        paths = []
        def read(path, fmt, schema_hints=None, encoding=None):
            paths.append(path)
            self.assertTrue(path.exists())
            return original(path, fmt, schema_hints, encoding)
        with patch.object(lake, 'read_file', side_effect=read):
            report = lake.ingest(filename='../../orders.csv', content=b'id\n1\n2\n',
                                 table='raw.uploaded', mode='upsert', key='id', batch_size=1)
        self.assertEqual((report['file'], report['table'], report['key'], report['batches']),
                         ('orders.csv', 'iceberg.raw.uploaded', ['id'], 2))
        self.assertFalse(paths[0].parent.exists())
        with patch.object(lake, 'read_file', side_effect=RuntimeError('broken')) as read:
            with self.assertRaisesRegex(RuntimeError, 'broken'):
                lake.ingest(filename='orders.csv', content='id\n1')
        self.assertFalse(read.call_args.args[0].parent.exists())

    def test_upload_validation_and_preview_formats(self):
        for kwargs in ({}, {'filename': 'x.csv'}, {'content': 'id\n1'},
                       {'filename': '..', 'content': ''}, {'filename': '', 'content': ''},
                       {'filename': 'x.csv', 'content': 1},
                       {'file': 'x.csv', 'filename': 'x.csv', 'content': ''}):
            for function in (lake.preview, lake.ingest):
                with self.subTest(kwargs=kwargs, function=function.__name__), self.assertRaises((ValueError, TypeError)):
                    function(**kwargs)
        report = lake.preview(filename='data.txt', content='{"a": 1}\n{"b": "x"}', format='jsonl')
        self.assertEqual(report['sample_rows'], [[1, None], [None, 'x']])
        self.assertEqual(lake.preview(filename='empty.csv', content='id\n')['row_count'], 0)
        with self.assertRaises(ValueError):
            lake.preview(filename='bad.csv', content='a\n1,2')
        with patch.dict(sys.modules, {'pyarrow': None, 'pyarrow.parquet': None}):
            with self.assertRaisesRegex(ValueError, 'requires pyarrow'):
                lake.preview(filename='data.parquet', content=b'parquet')

    def test_csv_replace_batches_types_and_escaping(self):
        path = self.file('Sales-2026.csv', 'ID,Active,Price,Day,Customer,Empty\n1,TRUE,1.2,2026-10-01,O\'Brien,\n2,false,2,2026-10-02,A,\n3,true,3.5,2026-10-03,B,\n')
        result = lake.ingest(path, batch_size=2)
        self.assertEqual(result['table'], 'iceberg.raw.sales_2026')
        self.assertEqual([c['type'] for c in result['columns']], ['BIGINT', 'BOOLEAN', 'DOUBLE', 'DATE', 'VARCHAR', 'VARCHAR'])
        self.assertEqual((result['rows_ingested'], result['batches']), (3, 2))
        self.assertEqual(result['format'], 'csv')
        self.assertEqual(result['telemetry'], 'recorded')
        statements = self.statements()
        self.assertEqual(statements[0], 'CREATE SCHEMA IF NOT EXISTS iceberg.raw')
        self.assertIn('CREATE TABLE "iceberg"."raw"."sales_2026__stage_abcd1234" AS SELECT * FROM (VALUES', statements[1])
        self.assertIn("O''Brien", statements[1])
        self.assertIn("DATE '2026-10-01'", statements[1])
        self.assertIn(", 'O''Brien', '')", statements[1])
        self.assertTrue(statements[2].startswith('INSERT INTO "iceberg"."raw"."sales_2026__stage_abcd1234"'))
        self.assertEqual(statements[3], 'CREATE OR REPLACE TABLE "iceberg"."raw"."sales_2026" AS SELECT * FROM "iceberg"."raw"."sales_2026__stage_abcd1234"')
        self.assertEqual(statements[4], 'DROP TABLE IF EXISTS "iceberg"."raw"."sales_2026__stage_abcd1234"')
        self.audit.assert_called_once_with('url', result['table'])
        self.telemetry.assert_called_once_with('ingest', result['duration_ms'], status='success', prefix='lake.')

    def test_append_targets_and_column_order(self):
        path = self.file('x.tsv', 'B\tA\n2\t1\n')
        for schema in sorted(lake.SCHEMAS):
            self.execute.reset_mock()
            result = lake.ingest(path, table=f'{schema}.custom', mode='append', batch_size=1)
            self.assertEqual(result['table'], f'iceberg.{schema}.custom')
            self.assertEqual(self.statements()[1], f'CREATE TABLE IF NOT EXISTS "iceberg"."{schema}"."custom" ("b" BIGINT, "a" BIGINT)')
            self.assertIn('("b", "a") VALUES', self.statements()[2])

    def test_json_formats_union_columns_and_sanitization(self):
        rows = [{'1st col': 1, 'a-b': 'hello', 'a b': True, 'a_b_2': {}},
                {'1st col': 2, 'a-b': 'world', 'later': ['x']}]
        for suffix, content in [('json', json.dumps(rows)), ('jsonl', '\n'.join(map(json.dumps, rows))),
                                ('ndjson', '\n\n'.join(map(json.dumps, rows)))]:
            _, schema, values = lake.read_file(self.file('data.' + suffix, content))
            self.assertEqual([c for c, _ in schema], ['col_1st_col', 'a_b', 'a_b_2', 'a_b_2_2', 'later'])
            self.assertEqual(values[1][2], None)
        result = lake.ingest(self.file('data.input', json.dumps(rows)), format='jsonl')
        self.assertEqual(result['format'], 'jsonl')

    def test_upsert_last_rows_in_file_order_before_batching(self):
        path = self.file('orders.csv', 'id,name\n1,old\n2,second\n3,third\n1,last\n4,fourth\n2,latest\n')
        report = lake.ingest(path, mode='upsert', key='id', batch_size=2)
        self.assertEqual(report['key'], ['id'])
        self.assertEqual((report['rows_ingested'], report['batches']), (4, 2))
        statements = self.statements()
        target = '"iceberg"."raw"."orders"'
        self.assertEqual(statements[0], 'CREATE SCHEMA IF NOT EXISTS iceberg.raw')
        stage = '"iceberg"."raw"."orders__stage_abcd1234"'
        self.assertEqual(statements[3], f'CREATE TABLE IF NOT EXISTS {target} ("id" BIGINT, "name" VARCHAR)')
        self.assertEqual(statements[4], f'DESCRIBE {target}')
        for statement, values in zip(statements[1:3], [
                "(CAST(3 AS BIGINT), 'third'), (CAST(1 AS BIGINT), 'last')",
                "(CAST(4 AS BIGINT), 'fourth'), (CAST(2 AS BIGINT), 'latest')"]):
            self.assertIn(values, statement)
            self.assertIn(stage, statement)
        self.assertEqual(statements[5],
                f'CREATE OR REPLACE TABLE {target} AS WITH incoming AS (SELECT CAST(t."id" AS BIGINT) AS "id", '
                f'CAST(t."name" AS VARCHAR) AS "name" FROM {stage} t) '
                f'SELECT t."id" AS "id", t."name" AS "name" FROM incoming t LEFT JOIN {target} old '
                'ON t."id" IS NOT DISTINCT FROM old."id" '
                f'UNION ALL SELECT old."id", old."name" FROM {target} old '
                'WHERE NOT EXISTS (SELECT 1 FROM incoming t WHERE t."id" IS NOT DISTINCT FROM old."id")')
        self.assertEqual(statements[6], f'DROP TABLE IF EXISTS {stage}')
        self.assertEqual(len(statements), 7)

    def test_upsert_composite_sanitized_keys_and_typed_values(self):
        path = self.file('keys.csv', 'Order-ID,Region,Name\n01,west,old\n1,east,other\n1,west,new\n,south,old-null\n,south,new-null\n')
        report = lake.ingest(path, mode='upsert', key='Order-ID,Region,')
        self.assertEqual(report['key'], ['order_id', 'region'])
        self.assertEqual(report['rows_ingested'], 4)
        sql = self.statements()[-2]
        self.assertIn('t."order_id" IS NOT DISTINCT FROM old."order_id" AND '
                      't."region" IS NOT DISTINCT FROM old."region"', sql)
        self.assertIn("('01', 'west', 'old')", self.statements()[1])
        self.assertNotIn("'old-null'", self.statements()[1])
        self.assertIn("('', 'south', 'new-null')", self.statements()[1])

    def test_upsert_rejects_missing_or_unknown_keys_before_access(self):
        path = self.file('orders.csv', 'id,name\n1,first\n')
        for key in (None, '', '  ', ', ,', 'unknown', 'id,unknown'):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'upsert requires --key matching input columns'):
                lake.ingest(path, mode='upsert', key=key)
        self.trino.assert_not_called()
        self.execute.assert_not_called()

    def test_empty_upsert_creates_table_without_replacing_it(self):
        report = lake.ingest(self.file('empty.csv', 'id,name\n'), mode='upsert', key='id')
        self.assertEqual((report['rows_ingested'], report['batches'], report['key']), (0, 0, ['id']))
        self.assertEqual(len(self.statements()), 4)
        self.assertTrue(self.statements()[2].startswith('CREATE TABLE IF NOT EXISTS'))
        self.assertFalse(any(s.startswith('CREATE OR REPLACE') for s in self.statements()))

    def test_duplicate_csv_headers_preserve_values_and_reserved_names(self):
        path = self.file('drop.csv', 'select,select,select_2\n1,2,3\n')
        report = lake.ingest(path)
        self.assertEqual([c['name'] for c in report['columns']], ['select', 'select_2', 'select_2_2'])
        self.assertIn('"iceberg"."raw"."drop__stage_abcd1234"', self.statements()[1])
        self.assertIn('(CAST(1 AS BIGINT), CAST(2 AS BIGINT), CAST(3 AS BIGINT))', self.statements()[1])

    def test_inference(self):
        for values, expected in [([], 'VARCHAR'), ([None, ''], 'VARCHAR'),
                                 ([True, 'FALSE'], 'BOOLEAN'), ([False, 1], 'VARCHAR'),
                                 ([-2**63, str(2**63 - 1)], 'BIGINT'),
                                 ([2**63], 'DOUBLE'), ([1, 2.5], 'DOUBLE'),
                                 (['1', '-2e3', None], 'DOUBLE'),
                                 (['2024-02-29', None], 'DATE'), (['2023-02-29'], 'VARCHAR'),
                                 (['2026-1-01'], 'VARCHAR'), (['NaN'], 'VARCHAR'),
                                 ([float('inf')], 'VARCHAR'), (['hello', 1], 'VARCHAR')]:
            with self.subTest(values=values):
                self.assertEqual(lake.infer_type(values), expected)

    def test_leading_zero_codes_remain_varchar(self):
        for code in ('000123', '01', '+01', '-000123', '00', ' 001 '):
            with self.subTest(code=code):
                self.assertFalse(lake.integer(code))
                self.assertEqual(lake.infer_type([code, '12', None, '']), 'VARCHAR')
                self.assertEqual(lake.literal(code, 'VARCHAR'), "'" + code + "'")
        for code in ('0', '+0', '-0', '123', '-123'):
            self.assertTrue(lake.integer(code))
        report = lake.ingest(filename='codes.csv', content='code\n000123\n01\n')
        self.assertEqual(report['columns'], [dict(name='code', type='VARCHAR')])
        self.assertIn("('000123'), ('01')", self.statements()[1])

    def test_schema_hints_preserve_decimal_precision_and_override_inference(self):
        amount = '12345678901234567890.123456789012345678'
        content = f'Account ID,amount\n000123,{amount}\n000124,\n'
        hints = {'Account ID': 'VARCHAR', 'amount': 'DECIMAL(38,18)'}
        path = self.file('amounts.csv', content)
        _, schema, rows = lake.read_file(path, schema_hints=hints)
        self.assertEqual(schema, [('account_id', 'VARCHAR'), ('amount', 'DECIMAL(38,18)')])
        self.assertEqual(rows[0], ['000123', amount])
        self.assertEqual(lake.literal(amount, 'decimal(38,18)'), f"CAST('{amount}' AS DECIMAL(38,18))")
        report = lake.ingest(filename='amounts.csv', content=content, schema_hints=hints)
        self.assertEqual(report['columns'][1]['type'], 'DECIMAL(38,18)')
        self.assertIn(f"CAST('{amount}' AS DECIMAL(38,18))", self.statements()[1])
        self.assertIn('CAST(NULL AS DECIMAL(38,18))', self.statements()[1])
        report = lake.preview(path, schema_hints={'account_id': 'BIGINT'})
        self.assertEqual(report['columns'][0]['type'], 'BIGINT')
        self.assertEqual(report['columns'][1]['type'], 'DOUBLE')
        for kind in ('DECIMAL', 'DECIMAL(38)', 'DECIMAL(38,18)'):
            self.assertEqual(lake.read_file(path, schema_hints={'amount': kind})[1][1], ('amount', kind))
        for hints in ([], ['BIGINT'], {'amount': 'BIGINT); DROP TABLE x; --'}):
            with self.subTest(hints=hints), self.assertRaises(ValueError):
                lake.read_file(path, schema_hints=hints)

    def test_varchar_empty_string_and_null_are_distinct(self):
        self.assertTrue(lake.is_null(None))
        self.assertFalse(lake.is_null(''))
        self.assertEqual(lake.literal(None, 'VARCHAR'), 'CAST(NULL AS VARCHAR)')
        self.assertEqual(lake.literal('', 'VARCHAR'), "''")
        for kind in ('BIGINT', 'DOUBLE', 'DATE', 'BOOLEAN', 'DECIMAL(10,2)'):
            for value in ('', None):
                with self.subTest(kind=kind, value=value):
                    self.assertEqual(lake.literal(value, kind), f'CAST(NULL AS {kind})')
        lake.ingest(filename='text.jsonl', content='{"text": ""}\n{"text": null}\n{"text": "雪"}')
        self.assertIn("(''), (CAST(NULL AS VARCHAR)), ('雪')", self.statements()[1])
        lake.ingest(self.file('missing.csv', 'id,text\n1,\n2\n'))
        self.assertIn("(CAST(1 AS BIGINT), ''), (CAST(2 AS BIGINT), CAST(NULL AS VARCHAR))",
                      self.statements()[-3])

    def test_csv_encodings_unicode_headers_and_line_breaks(self):
        content = '顧客コード,顧客名,1番目\r\n000123,"山田\r\n太郎",値\r\n'
        expected = [('顧客コード', 'VARCHAR'), ('顧客名', 'VARCHAR'), ('col_1番目', 'VARCHAR')]
        for encoding in ('utf-8-sig', 'cp932'):
            for suffix, text in (('csv', content), ('tsv', content.replace(',', '\t'))):
                path = self.root / ('customers.' + suffix)
                path.write_bytes(text.encode(encoding))
                with self.subTest(encoding=encoding, suffix=suffix):
                    _, schema, rows = lake.read_file(path)
                    self.assertEqual(schema, expected)
                    self.assertEqual(rows, [['000123', '山田\r\n太郎', '値']])
        report = lake.preview(filename='customers.csv', content=content.encode('cp932'), encoding='cp932')
        self.assertEqual(report['columns'][0]['name'], '顧客コード')
        lake.ingest(filename='customers.csv', content=content.encode('cp932'))
        self.assertIn('"顧客コード"', self.statements()[1])
        self.assertIn("'000123'", self.statements()[1])
        self.assertIn("'山田\r\n太郎'", self.statements()[1])
        # This byte sequence is valid UTF-8 too: an explicit encoding must win.
        path = self.root / 'latin.csv'
        path.write_bytes(b'text\n\xc3\xa9\n')
        self.assertEqual(lake.read_file(path, encoding='latin-1')[2], [['Ã©']])
        lake.ingest(path, encoding='latin-1')
        self.assertIn("'Ã©'", self.statements()[-3])

    def test_upsert_preserves_target_columns_and_is_idempotent(self):
        # Execute the emitted SELECT on SQLite rather than simulating its merge
        # behavior. These standard joins/CTEs also support NULL-safe key matching.
        for batch_size in (1, 20):
            with self.subTest(batch_size=batch_size), sqlite3.connect(':memory:') as db:
                db.execute('CREATE TABLE target (id BIGINT, name VARCHAR, extra VARCHAR, score BIGINT)')
                db.executemany('INSERT INTO target VALUES (?, ?, ?, ?)',
                               [(1, 'before', 'keep', 7), (2, 'untouched', 'stay', 8),
                                (None, 'null-before', 'null-key', 9)])
                def execute(url, statement):
                    statement = statement.replace('"iceberg"."raw"."orders__stage_abcd1234"', 'stage')
                    if statement.startswith('CREATE TABLE stage AS SELECT * FROM (VALUES'):
                        db.execute('CREATE TABLE stage (name VARCHAR, id BIGINT)')
                        values = statement.split('FROM (VALUES ', 1)[1].rsplit(') AS t(', 1)[0]
                        db.execute('INSERT INTO stage VALUES ' + values)
                    elif statement.startswith('INSERT INTO stage') or statement.startswith('DROP TABLE IF EXISTS stage'):
                        db.execute(statement)
                    if statement.startswith('DESCRIBE '):
                        return dict(rows=[['id', 'bigint'], ['name', 'varchar'],
                                          ['extra', 'varchar'], ['score', 'bigint']])
                    if statement.startswith('CREATE OR REPLACE TABLE '):
                        query = statement.split(' AS ', 1)[1].replace('"iceberg"."raw"."orders"', 'target')
                        rows = db.execute(query).fetchall()
                        db.execute('DELETE FROM target')
                        db.executemany('INSERT INTO target VALUES (?, ?, ?, ?)', rows)
                    return {}
                self.execute.side_effect = execute
                content = '\n'.join(json.dumps(row) for row in [
                    {'name': 'discard', 'id': 1}, {'name': '', 'id': 1},
                    {'name': 'new', 'id': 3}, {'name': None, 'id': None}])
                expected = [(None, None, 'null-key', 9), (1, '', 'keep', 7),
                            (2, 'untouched', 'stay', 8), (3, 'new', None, None)]
                for _ in range(2):
                    report = lake.ingest(filename='orders.jsonl', content=content,
                                         mode='upsert', key='id', batch_size=batch_size)
                    self.assertEqual(report['rows_ingested'], 3)
                    self.assertEqual(db.execute('SELECT * FROM target ORDER BY id').fetchall(), expected)

    def test_ingest_cli_schema_hints_and_encoding(self):
        path = self.root / 'cli.csv'
        path.write_bytes('金額\n123.4500\n'.encode('cp932'))
        args = ['ingest', str(path), '--schema-hints', '{"金額": "DECIMAL(12,4)"}', '--encoding', 'cp932']
        with patch.object(sys, 'argv', args), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(lake.main(), 0)
        self.assertIn("CAST('123.45' AS DECIMAL(12,4))", self.statements()[1])

    def test_literals_mask_sql_fragments(self):
        value = "x'); DROP TABLE iceberg.raw.t; --"
        statement = 'INSERT INTO iceberg.raw.t VALUES (' + lake.literal(value, 'VARCHAR') + ')'
        self.assertEqual(lake.wr.check_sql_policy(statement, 'sandbox-write'), statement)
        for value, kind, expected in [(None, 'BIGINT', 'CAST(NULL AS BIGINT)'),
                                      (False, 'BOOLEAN', 'FALSE'), ('true', 'BOOLEAN', 'TRUE'),
                                      (12, 'BIGINT', 'CAST(12 AS BIGINT)'),
                                      (1.5, 'DOUBLE', 'CAST(1.5 AS DOUBLE)')]:
            self.assertEqual(lake.literal(value, kind), expected)

    def test_empty_header_only_and_no_columns(self):
        path = self.file('empty.csv', 'id,name\n')
        for mode in ('replace', 'append'):
            self.execute.reset_mock()
            report = lake.ingest(path, mode=mode)
            self.assertEqual((report['rows_ingested'], report['batches']), (0, 0))
            self.assertEqual(len(self.statements()), 4 if mode == 'replace' else 2)
            if mode == 'replace':
                self.assertIn('CAST(NULL AS VARCHAR) AS "id"', self.statements()[1])
                self.assertTrue(self.statements()[1].endswith('WHERE FALSE'))
        for name, content in [('none.csv', ''), ('none.json', '[]'), ('empty.jsonl', '{}')]:
            with self.assertRaisesRegex(ValueError, 'no columns'):
                lake.ingest(self.file(name, content))

    def test_invalid_input_precedes_access_and_failure_telemetry(self):
        path = self.file('valid.csv', 'id\n1\n')
        for kwargs in [dict(table='other.t'), dict(table='other.raw.t'), dict(table='raw.bad-name'),
                       dict(table='raw.t; DROP'), dict(batch_size=0), dict(batch_size=-1),
                       dict(mode='delete'), dict(format='unknown')]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                lake.ingest(path, **kwargs)
        self.trino.assert_not_called()
        self.assertEqual(self.telemetry.call_args.kwargs, {'status': 'failed', 'prefix': 'lake.'})
        for name, content in [('bad.json', '[1]'), ('bad.jsonl', 'null'),
                              ('wide.csv', 'a\n1,2\n')]:
            with self.assertRaises(ValueError):
                lake.ingest(self.file(name, content))
        with self.assertRaises(FileNotFoundError):
            lake.ingest(self.root / 'missing.csv')

    def test_write_and_audit_failure_telemetry(self):
        path = self.file('x.csv', 'id\n1\n')
        for mock in (self.execute, self.audit):
            mock.side_effect = RuntimeError('failed')
            with self.assertRaises(RuntimeError):
                lake.ingest(path)
            self.assertEqual(self.telemetry.call_args.kwargs['status'], 'failed')
            mock.side_effect = None

    def test_parquet_optional_dependency_and_rows(self):
        path = self.root / 'x.parquet'
        with patch.dict(sys.modules, {'pyarrow': None, 'pyarrow.parquet': None}):
            with self.assertRaisesRegex(ValueError, 'parquet ingestion requires pyarrow'):
                lake.read_file(path)
        parquet = MagicMock()
        parquet.read_table.return_value.column_names = ['ID', 'Day']
        parquet.read_table.return_value.to_pylist.return_value = [{'ID': 42, 'Day': '2026-10-17'}]
        with patch.dict(sys.modules, {'pyarrow': SimpleNamespace(parquet=parquet), 'pyarrow.parquet': parquet}):
            result = lake.ingest(path)
        self.assertEqual(result['rows_ingested'], 1)
        self.assertEqual(result['format'], 'parquet')

    def test_cli_success_and_failure(self):
        path = self.file('cli.csv', 'a\n1\n')
        output = io.StringIO()
        with patch.object(sys, 'argv', ['ingest', str(path)]), contextlib.redirect_stdout(output):
            self.assertEqual(lake.main(), 0)
        self.assertEqual(json.loads(output.getvalue())['action'], 'ingest')
        output = io.StringIO()
        with patch.object(sys, 'argv', ['ingest', str(path), '--mode', 'upsert', '--key', 'a']), contextlib.redirect_stdout(output):
            self.assertEqual(lake.main(), 0)
        self.assertEqual(json.loads(output.getvalue())['key'], ['a'])
        with patch.object(sys, 'argv', ['ingest', str(path), '--batch-size', '0']), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(lake.main(), 1)

    def test_raw_guardrail_and_bear_discovery(self):
        for statement in ['CREATE SCHEMA IF NOT EXISTS iceberg.raw',
                          'CREATE TABLE IF NOT EXISTS iceberg.raw.x (a BIGINT)',
                          'CREATE OR REPLACE TABLE iceberg.raw.x AS SELECT 1',
                          'INSERT INTO iceberg.raw.x VALUES (1)',
                          'ALTER TABLE iceberg.raw.x EXECUTE rollback_to_snapshot(1)']:
            self.assertEqual(lake.wr.check_sql_policy(statement, 'sandbox-write'), statement)
        for schema in lake.SCHEMAS:
            self.assertEqual(lake.bears.data_table(schema + '.x'), 'x')
        self.execute.side_effect = lambda url, statement: (_ for _ in ()).throw(RuntimeError('missing schema')) if '"raw"' in statement else {'rows': [['x']]}
        with patch.object(lake.wr, 'kubectl', return_value='{}'):
            report = lake.bears.catalog(SimpleNamespace(action='search'))
        self.assertEqual(report['metadata']['raw'], [])
        self.assertEqual(report['metadata']['sandbox'], [['x']])
        self.assertEqual(set(report['metadata']) - {'catalog_config'}, {'tpch_tiny', 'raw', 'sandbox', 'staging', 'marts', 'smoke'})


if __name__ == '__main__':
    unittest.main()
