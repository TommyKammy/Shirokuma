"""Opt-in synthetic acceptance checks against the dedicated resident Iceberg lab.

Run with SHIROKUMA_RUNTIME_TESTS=1 python3 -m unittest discover -s tests
-p test_runtime_data_integrity.py -v. Tables use fresh names and are removed.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import lake_ingest as lake
import sqlserver_sync as sync
import warehouse_resident as warehouse


@unittest.skipUnless(os.environ.get('SHIROKUMA_RUNTIME_TESTS') == '1',
                     'set SHIROKUMA_RUNTIME_TESTS=1 for dedicated synthetic lab checks')
class RuntimeDataIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tunnel = warehouse.persistent_trino()
        cls.url = cls.tunnel.__enter__()
        cls.addClassCleanup(cls.tunnel.__exit__, None, None, None)

    def setUp(self):
        self.table = 'iceberg.raw.acceptance_integrity_' + uuid.uuid4().hex[:16]
        self.sql_table = warehouse.identifier(self.table)
        # Register cleanup before creating anything, including on failed setup.
        self.addCleanup(self.cleanup_synthetic_tables)
        self.sql('CREATE SCHEMA IF NOT EXISTS iceberg.raw')

    def sql(self, statement):
        return warehouse.execute(self.url, statement, {
            'X-Trino-User': 'shirokuma-runtime-acceptance',
            'X-Trino-Catalog': 'iceberg', 'X-Trino-Schema': 'raw'})

    def cleanup_synthetic_tables(self):
        prefix = self.table.rsplit('.', 1)[1]
        tables = self.sql("SELECT table_name FROM iceberg.information_schema.tables "
                          "WHERE table_schema = 'raw' AND table_name LIKE " +
                          warehouse.literal(prefix + '%'))['rows']
        errors = []
        for row in tables:
            if row[0].startswith(prefix):
                try:
                    self.sql('DROP TABLE IF EXISTS ' + warehouse.identifier('iceberg.raw.' + row[0]))
                except Exception as exc:
                    errors.append(str(exc))
        if errors:
            raise RuntimeError('Synthetic table cleanup failed: ' + '; '.join(errors))

    def test_fractional_json_upsert_keeps_existing_integer_target(self):
        self.sql('CREATE TABLE ' + self.sql_table + ' (id INTEGER, amount BIGINT)')
        self.sql('INSERT INTO ' + self.sql_table + ' VALUES (1, 10)')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fractional.jsonl'
            path.write_text('{"id":1.1,"amount":4}\n{"id":1.9,"amount":7}\n')
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, 'invalid INTEGER', msg='fractional keys must fail before promotion'):
                    lake.ingest(path, table=self.table, mode='upsert', key='id', batch_size=1)
                self.assertEqual(self.sql('SELECT id, amount FROM ' + self.sql_table)['rows'], [[1, 10]])

    def test_timestamp6_repeated_real_sync_preserves_values(self):
        self.sql('CREATE TABLE ' + self.sql_table +
                 ' (id BIGINT, amount BIGINT, created TIMESTAMP(6))')
        self.sql('INSERT INTO ' + self.sql_table + " VALUES (1, 10, TIMESTAMP '2026-10-17 00:00:00.000001')")
        stamp = '2026-10-17 12:34:56.123456'
        source_created = stamp
        columns = [dict(name='id', type='BIGINT'), dict(name='amount', type='BIGINT'),
                   dict(name='created', type='DATETIME2')]

        @contextmanager
        def source(*args, **kwargs):
            yield sync.Source(columns, rows=[dict(id=2, amount=7, created=source_created)])

        with patch.object(sync, 'open_source', side_effect=source):
            for mode in ('upsert', 'append'):
                source_created = stamp
                # Reset the synthetic target to a unique-key baseline for each mode.
                self.sql('CREATE OR REPLACE TABLE ' + self.sql_table +
                         " AS SELECT CAST(1 AS BIGINT) id, CAST(10 AS BIGINT) amount, "
                         "CAST(TIMESTAMP '2026-10-17 00:00:00.000001' AS TIMESTAMP(6)) created")
                for _ in range(2):
                    report = sync.sync('dbo.synthetic', self.table, mode=mode, key='id', amount_col='amount')
                    self.assertEqual(report['status'], 'matched')
                expected_count = 1 if mode == 'upsert' else 2
                rows = self.sql('SELECT CAST(created AS VARCHAR) FROM ' + self.sql_table + ' WHERE id = 2')['rows']
                self.assertEqual(rows, [[stamp]] * expected_count)
                types = self.sql('DESCRIBE ' + self.sql_table)['rows']
                self.assertEqual(next(row[1] for row in types if row[0] == 'created'), 'timestamp(6)')
                before = self.sql('SELECT id, amount, CAST(created AS VARCHAR) FROM ' + self.sql_table + ' ORDER BY id')['rows']
                source_created = stamp + '7'
                with self.assertRaisesRegex(ValueError, 'timestamp does not fit'):
                    sync.sync('dbo.synthetic', self.table, mode=mode, key='id', amount_col='amount')
                self.assertEqual(self.sql('SELECT id, amount, CAST(created AS VARCHAR) FROM ' +
                                         self.sql_table + ' ORDER BY id')['rows'], before)

    def test_new_and_replaced_datetime2_target_preserves_null_first_batch(self):
        stamp = '2026-10-17 12:34:56.123456'
        columns = [dict(name='id', type='BIGINT'), dict(name='created', type='DATETIME2')]

        @contextmanager
        def source(*args, **kwargs):
            yield sync.Source(columns, rows=[dict(id=1, created=None), dict(id=2, created=stamp)])

        with patch.object(sync, 'open_source', side_effect=source):
            for _ in range(2):
                report = sync.sync('dbo.synthetic', self.table, mode='replace', key='id', batch_size=1)
                self.assertEqual(report['status'], 'matched')
                self.assertEqual(self.sql('SELECT id, CAST(created AS VARCHAR) FROM ' +
                                         self.sql_table + ' ORDER BY id')['rows'], [[1, None], [2, stamp]])
                types = self.sql('DESCRIBE ' + self.sql_table)['rows']
                self.assertEqual(next(row[1] for row in types if row[0] == 'created'), 'timestamp(6)')


if __name__ == '__main__':
    unittest.main()
