import contextlib
from datetime import date, datetime
from decimal import Decimal
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'local_experiment'))
import sqlserver_sync as sync


class SqlServerSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'source.json'
        self.columns = [dict(name='id', type='BIGINT'),
                        dict(name='amount', type='DECIMAL', precision=38, scale=2),
                        dict(name='code', type='NVARCHAR'), dict(name='day', type='DATE'),
                        dict(name='active', type='BIT'), dict(name='created', type='DATETIME2')]
        self.rows = [dict(id=2**40, amount='1250.50', code='000123雪', day='2026-10-17',
                          active=1, created='2026-10-17 12:34:56.123456'),
                     dict(id=None, amount=None, code='', day=None, active=0, created=None)]
        self.write()
        self.options = dict(source_table='dbo.orders', target_table='orders', mock_source=str(self.path))

    def write(self):
        self.path.write_text(json.dumps(dict(columns=self.columns, rows=self.rows)), encoding='utf-8')

    def target(self, values):
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(sync.wr, 'trino', return_value=contextlib.nullcontext('url')))
        execute = stack.enter_context(patch.object(sync.wr, 'execute', return_value=dict(rows=[values])))
        return stack, execute

    def test_all_type_mappings(self):
        for kind in ('INT', 'BIGINT', 'SMALLINT', 'TINYINT'):
            self.assertEqual(sync.type_hint(dict(type=kind)), 'BIGINT')
        for kind in ('VARCHAR', 'NVARCHAR', 'CHAR', 'NCHAR', 'TEXT'):
            self.assertEqual(sync.type_hint(dict(type=kind)), 'VARCHAR')
        for kind in ('DECIMAL', 'NUMERIC'):
            self.assertEqual(sync.type_hint(dict(type=kind, precision=38, scale=9)), 'DECIMAL(38,9)')
        self.assertEqual(sync.type_hint(dict(type='DECIMAL(25,4)')), 'DECIMAL(25,4)')
        self.assertEqual(sync.type_hint(dict(type='MONEY')), 'DECIMAL(19,4)')
        self.assertEqual(sync.type_hint(dict(type='SMALLMONEY')), 'DECIMAL(10,4)')
        for kind in ('DATETIME', 'DATETIME2'):
            self.assertEqual(sync.type_hint(dict(type=kind)), 'TIMESTAMP')
        for col in (dict(type='FLOAT'), dict(type='DECIMAL(39,2)'), dict(type='DECIMAL(2,3)')):
            with self.assertRaises(ValueError):
                sync.type_hint(col)

    def test_batch_values_preserve_types(self):
        with sync.open_source('orders', mock_source=str(self.path)) as source:
            batches = list(source.batches(1))
        self.assertEqual(len(batches), 2)
        row = batches[0][0]
        self.assertEqual(row['amount'], '1250.50')
        self.assertEqual(row['code'], '000123雪')
        self.assertEqual(row['id'], 2**40)
        self.assertIs(row['active'], True)
        self.assertEqual(row['day'], '2026-10-17')
        self.assertIs(batches[1][0]['active'], False)
        self.assertIsNone(batches[1][0]['amount'])
        self.assertEqual(sync.convert(date(2026, 10, 17), 'DATE'), '2026-10-17')
        self.assertEqual(sync.convert(datetime(2026, 10, 17), 'TIMESTAMP'), '2026-10-17 00:00:00')
        self.assertEqual(sync.convert('2026-10-17T12:34:56.1234567', 'TIMESTAMP'),
                         '2026-10-17 12:34:56.1234567')

    def test_invalid_values_fail_closed(self):
        for value, hint in [(1.2, 'DECIMAL(9,2)'), ('1.001', 'DECIMAL(9,2)'),
                            ('1000', 'DECIMAL(3,0)'), ('NaN', 'DECIMAL(9,2)'),
                            ('1.5', 'BIGINT'), (2**63, 'BIGINT'), ('yes', 'BOOLEAN'),
                            ('2026-02-30', 'DATE')]:
            with self.subTest(value=value), self.assertRaises((ValueError, ArithmeticError)):
                sync.convert(value, hint)

    def test_extract_modes_schema_and_empty_source(self):
        def ingest(path, **kwargs):
            detected, schema, rows = sync.lake_ingest.read_file(path, schema_hints=kwargs['schema_hints'])
            self.assertEqual(dict(schema)['amount'], 'DECIMAL(38,2)')
            self.assertEqual(dict(schema)['created'], 'TIMESTAMP')
            if rows:
                self.assertEqual(rows[0][1], '1250.50')
                self.assertEqual(rows[0][2], '000123雪')
            return dict(rows_ingested=len(rows))
        with patch.object(sync.lake_ingest, 'ingest', side_effect=ingest) as load:
            for mode in ('replace', 'append', 'upsert'):
                result = sync.extract(**self.options, mode=mode, key='id', batch_size=1)
                self.assertEqual(result['row_count'], 2)
                self.assertEqual(load.call_args.kwargs['atomic_append'], mode == 'append')
                self.assertEqual(load.call_args.kwargs['table'], 'iceberg.raw.orders')
            self.rows = []
            self.write()
            self.assertEqual(sync.extract(**self.options)['row_count'], 0)

    def test_reconcile_matches_and_aggregate_sql(self):
        stack, execute = self.target([2, 1, '1250.5000', 1, 1])
        with stack:
            report = sync.reconcile(**self.options, key='id', amount_col='amount')
        self.assertEqual(report['status'], 'matched')
        self.assertEqual(report['discrepancies'], [])
        sql = execute.call_args.args[1]
        self.assertIn('COUNT(DISTINCT "id")', sql)
        self.assertIn('CAST(SUM("amount") AS VARCHAR)', sql)
        self.assertIn('FROM "iceberg"."raw"."orders"', sql)

    def test_mismatch_has_report_and_raises(self):
        stack, _ = self.target([3, 2, '1250.51', 0, 0])
        with stack, self.assertRaises(sync.ReconciliationError) as caught:
            sync.reconcile(**self.options, key='id', amount_col='amount')
        report = caught.exception.report
        self.assertEqual(report['status'], 'mismatch')
        self.assertEqual({d['metric'] for d in report['discrepancies']},
                         {'row_count', 'distinct_keys', 'sum_amount', 'null_counts.id', 'null_counts.amount'})

    def test_empty_and_all_null_sums(self):
        for rows in ([], [self.rows[1]]):
            self.rows = rows
            self.write()
            stack, _ = self.target([len(rows), 0, None, len(rows), len(rows)])
            with stack:
                self.assertEqual(sync.reconcile(**self.options, key='id', amount_col='amount')['status'], 'matched')

    def test_large_decimal_sum_is_exact(self):
        self.rows = [dict(self.rows[0], amount='123456789012345678901234567890123456.78')]
        self.write()
        stack, _ = self.target([1, '123456789012345678901234567890123456.78', 0])
        with stack:
            report = sync.reconcile(**self.options, amount_col='amount')
        self.assertEqual(report['status'], 'matched')

    def test_count_only_and_bad_target_result(self):
        stack, _ = self.target([2])
        with stack:
            self.assertEqual(sync.reconcile(**self.options)['status'], 'matched')
        stack, _ = self.target([])
        with stack, self.assertRaisesRegex(RuntimeError, 'aggregate'):
            sync.reconcile(**self.options)

    def test_sync_order_and_extraction_failure(self):
        events = []
        with patch.object(sync, 'extract', side_effect=lambda *a, **k: events.append('extract')), \
             patch.object(sync, 'reconcile', side_effect=lambda *a, **k: events.append('reconcile')):
            sync.sync(**self.options, key='id', amount_col='amount')
        self.assertEqual(events, ['extract', 'reconcile'])
        with patch.object(sync, 'extract', side_effect=RuntimeError('failed')), patch.object(sync, 'reconcile') as rec:
            with self.assertRaises(RuntimeError):
                sync.sync(**self.options)
            rec.assert_not_called()

    def test_sqlite_source_and_connection_closes(self):
        path = Path(self.temp.name) / 'source.sqlite'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE orders(id BIGINT, code NVARCHAR, active BIT, day DATE)')
            db.execute("INSERT INTO orders VALUES (12, '000123雪', 1, '2026-10-17')")
        with sync.open_source('orders', mock_source=str(path)) as source:
            self.assertEqual(list(source.batches())[0][0]['code'], '000123雪')

    def test_real_driver_abstraction_and_fallback(self):
        for fallback in (False, True):
            driver = MagicMock()
            metadata, data = MagicMock(), MagicMock()
            metadata.fetchall.return_value = [('id', 'int', 10, 0)]
            data.fetchmany.side_effect = [[(1,)], []]
            driver.connect.return_value.cursor.side_effect = [metadata, data]
            imports = [ImportError(), driver] if fallback else [driver]
            with patch.object(sync.importlib, 'import_module', side_effect=imports):
                with sync.open_source('dbo.orders', host='server', database='db', user='u', password='p') as source:
                    self.assertEqual(list(source.batches()), [[{'id': 1}]])
            driver.connect.return_value.close.assert_called_once()
            data.close.assert_called_once()

    def test_errors_before_ingestion(self):
        for options in (dict(mode='bad'), dict(mode='upsert'), dict(key='missing'), dict(batch_size=0)):
            with patch.object(sync.lake_ingest, 'ingest') as load, self.assertRaises(ValueError):
                sync.extract(**self.options, **options)
            load.assert_not_called()
        with self.assertRaises(ValueError):
            sync.source_name('orders; DROP TABLE x')
        with sync.open_source('orders', mock_source=str(self.path)) as source:
            with self.assertRaises(ValueError):
                list(source.batches(0))
        with self.assertRaises(ValueError):
            with sync.open_source('orders'):
                pass
        with patch.object(sync.importlib, 'import_module', side_effect=ImportError()), self.assertRaises(RuntimeError):
            with sync.open_source('orders', host='s', database='d', user='u', password='p'):
                pass

    def test_cli_parsing_and_fail_closed(self):
        args = ['--source-table', 'dbo.orders', '--target-table', 'orders', '--host', 's',
                '--port', '1444', '--database', 'd', '--user', 'u', '--password', 'secret']
        for command in ('extract', 'reconcile', 'sync'):
            with patch.object(sync, command, return_value={'status': 'matched'}) as action, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(sync.main([command] + args), 0)
            self.assertEqual(action.call_args.kwargs['port'], 1444)
        report = dict(status='mismatch', discrepancies=[dict(metric='row_count', source=2, target=1)])
        output = io.StringIO()
        with patch.object(sync, 'reconcile', side_effect=sync.ReconciliationError(report)), contextlib.redirect_stdout(output):
            self.assertEqual(sync.main(['reconcile'] + args), 1)
        self.assertEqual(json.loads(output.getvalue()), report)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(sync.main(['extract', '--source-table', 'x', '--target-table', 'y']), 1)
            with self.assertRaises(SystemExit):
                sync.main(['extract'])
            with self.assertRaises(SystemExit):
                sync.main(['sync'] + args + ['--mode', 'invalid'])

    def test_atomic_append_and_timestamp_literals(self):
        statements = []
        def execute(url, sql):
            statements.append(sql)
            return {}
        with patch.object(sync.lake_ingest.wr, 'trino', return_value=contextlib.nullcontext('url')), \
             patch.object(sync.lake_ingest.bears, 'sql', side_effect=execute), \
             patch.object(sync.lake_ingest.bears, 'audit_table', return_value={}), \
             patch.object(sync.lake_ingest.wr, 'telemetry', return_value={}):
            sync.extract(**self.options, mode='append', batch_size=1)
        target_writes = [s for s in statements if s.startswith('INSERT INTO "iceberg"."raw"."orders" ')]
        self.assertEqual(len(target_writes), 1)
        self.assertIn('SELECT', target_writes[0])
        self.assertTrue(any("TIMESTAMP '2026-10-17 12:34:56.123456'" in s for s in statements))
        self.assertTrue(statements[-1].startswith('DROP TABLE IF EXISTS'))

    def test_atomic_append_failure_never_commits_partial_batches(self):
        statements = []
        def execute(url, sql):
            statements.append(sql)
            if sql.startswith('INSERT INTO') and '__stage_' in sql:
                raise RuntimeError('batch failure')
            return {}
        with patch.object(sync.lake_ingest.wr, 'trino', return_value=contextlib.nullcontext('url')), \
             patch.object(sync.lake_ingest.bears, 'sql', side_effect=execute), \
             patch.object(sync.lake_ingest.wr, 'telemetry', return_value={}):
            with self.assertRaisesRegex(RuntimeError, 'batch failure'):
                sync.extract(**self.options, mode='append', batch_size=1)
        self.assertFalse(any(s.startswith('INSERT INTO "iceberg"."raw"."orders" ') for s in statements))
        self.assertTrue(statements[-1].startswith('DROP TABLE IF EXISTS'))

    def test_real_sqlserver_reconciliation_uses_native_aggregates(self):
        driver = MagicMock()
        metadata, data, aggregate = MagicMock(), MagicMock(), MagicMock()
        metadata.fetchall.return_value = [('id', 'int', 10, 0), ('amount', 'decimal', 38, 2)]
        aggregate.fetchone.return_value = [2, 1, '1250.50', 1, 1]
        driver.connect.return_value.cursor.side_effect = [metadata, data, aggregate]
        stack, _ = self.target([2, 1, '1250.50', 1, 1])
        with stack, patch.object(sync.importlib, 'import_module', return_value=driver):
            report = sync.reconcile('dbo.orders', 'orders', host='s', database='d', user='u',
                                    password='p', key='id', amount_col='amount')
        self.assertEqual(report['status'], 'matched')
        statement = aggregate.execute.call_args.args[0]
        self.assertIn('COUNT_BIG(DISTINCT [id])', statement)
        self.assertIn('CAST(SUM([amount]) AS VARCHAR(100))', statement)
        data.execute.assert_not_called()
        aggregate.close.assert_called_once()
        driver.connect.return_value.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
