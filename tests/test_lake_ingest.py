import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'local_experiment'))
import lake_ingest as lake


class LakeIngestTests(unittest.TestCase):
    def enterContext(self, manager):
        value = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        return value

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.execute = self.enterContext(patch.object(lake.bears, 'sql', return_value={}))
        self.audit = self.enterContext(patch.object(lake.bears, 'audit_table', return_value={'row_count': 3}))
        self.telemetry = self.enterContext(patch.object(lake.wr, 'telemetry', return_value='recorded'))
        self.trino = self.enterContext(patch.object(lake.wr, 'trino', return_value=contextlib.nullcontext('url')))

    def file(self, name, content):
        path = self.root / name
        path.write_text(content)
        return path

    def statements(self):
        return [call.args[1] for call in self.execute.call_args_list]

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
        self.assertIn('CREATE OR REPLACE TABLE "iceberg"."raw"."sales_2026" AS SELECT * FROM (VALUES', statements[1])
        self.assertIn("O''Brien", statements[1])
        self.assertIn("DATE '2026-10-01'", statements[1])
        self.assertIn('CAST(NULL AS VARCHAR)', statements[1])
        self.assertTrue(statements[2].startswith('INSERT INTO "iceberg"."raw"."sales_2026"'))
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

    def test_duplicate_csv_headers_preserve_values_and_reserved_names(self):
        path = self.file('drop.csv', 'select,select,select_2\n1,2,3\n')
        report = lake.ingest(path)
        self.assertEqual([c['name'] for c in report['columns']], ['select', 'select_2', 'select_2_2'])
        self.assertIn('"iceberg"."raw"."drop"', self.statements()[1])
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
            self.assertEqual(len(self.statements()), 2)
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
