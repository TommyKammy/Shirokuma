"""Offline semantic tests for the staged retained-history dashboard."""
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


D = load('history_dashboard_test', 'tools/local_experiment/grafana/history_dashboard.py')
B = load('history_benchmark_schema', 'tools/local_experiment/benchmark_events/import_receipts.py')
H = load('history_query_schema', 'tools/local_experiment/query_history/import_receipts.py')


class QuerySemanticsTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        for table, schema in {**B.SCHEMAS, **H.SCHEMAS}.items():
            columns = ','.join('"' + name + '" ' + ('REAL' if 'Float' in typ else 'INTEGER' if 'Int' in typ or 'Bool' in typ else 'TEXT')
                               for name, typ in schema)
            self.db.execute('CREATE TABLE ' + table + '(' + columns + ')')
        self.add('benchmark_runs', record_id='r1', result='failed', provenance_status='unknown', recorded_pairs=14,
                 comparison_unknown_pairs=1, expected_pairs=22, wall_seconds=None, started_at=None, run_token='legacy')
        self.add('benchmark_runs', record_id='r2', result='passed', provenance_status='recorded', recorded_pairs=22,
                 comparison_unknown_pairs=0, expected_pairs=22, wall_seconds=125.25, started_at='2026-09-17', run_token='complete')
        for index, comparison in enumerate(['matched', 'unobserved']):
            self.add('benchmark_queries', record_id='b' + str(index), query_number=index + 1, side='iceberg',
                     comparison_status=comparison, wall_seconds=0.0 if index == 0 else None,
                     engine_elapsed_ms=0 if index == 0 else None, cpu_ms=0 if index == 0 else None,
                     peak_memory_bytes=1024 if index == 0 else None, provenance_status='recorded' if index == 0 else 'unknown',
                     run_token='legacy')
        self.add('query_history_runs', record_id='hr1', workflow_result='passed', observer_outcome='completed',
                 observed_history_count=4, history_missing_count=0, history_unobserved_count=0,
                 auxiliary_query_count=2, uncaptured_count=0, query_run_token='owned')
        for index, (case, kind) in enumerate(H.PAIRS):
            self.add('query_history_queries', record_id='q' + str(index), query_run_token='owned', request_index=index,
                     case_id=case, kind=kind, response_state='FINISHED', response_complete=1, history_status='observed',
                     created_to_end_ms=0 if index == 0 else 1200, started_to_end_ms=0 if index == 0 else 1000,
                     query_id='workload' + str(index))
        for index, kind in enumerate(['own_query_history', 'fixture_stats'], 4):
            self.add('query_history_queries', record_id='q' + str(index), query_run_token='owned', request_index=index,
                     case_id=None, kind=kind, response_state='FINISHED', response_complete=1, history_status='not_target',
                     created_to_end_ms=None, started_to_end_ms=None, query_id='helper' + str(index))

    def add(self, table, **values):
        self.db.execute('INSERT INTO ' + table + '(' + ','.join(values) + ') VALUES (' + ','.join('?' for _ in values) + ')', list(values.values()))

    def query(self, key):
        # Identical retry duplicates are the only duplicates accepted by the importer.
        # This SQLite oracle models their logical FINAL view; no ClickHouse runtime is used.
        sql = re.sub(r'FROM agenthouse\.(\w+) FINAL', r'FROM (SELECT DISTINCT * FROM \1)', D.QUERIES[key])
        return [dict(row) for row in self.db.execute(sql)]

    def test_failed_runs_unknown_provenance_and_missing_times_stay_visible(self):
        rows = self.query('benchmark_runs')
        self.assertEqual([row['run_token'] for row in rows], ['complete', 'legacy'])
        failed = rows[1]
        self.assertEqual((failed['result'], failed['provenance_status']), ('failed', 'unknown'))
        self.assertEqual((failed['recorded_pairs'], failed['comparison_unknown_pairs'], failed['expected_pairs']), (14, 1, 22))
        self.assertIsNone(failed['started_at'])
        self.assertIsNone(failed['wall_seconds'])

    def test_unobserved_comparison_and_null_are_not_zero(self):
        rows = self.query('benchmark_queries')
        self.assertEqual([row['comparison_status'] for row in rows], ['matched', 'unobserved'])
        self.assertEqual(rows[0]['wall_seconds'], 0.0)
        self.assertEqual(rows[0]['cpu_ms'], 0)
        self.assertIsNone(rows[1]['wall_seconds'])
        self.assertIsNone(rows[1]['cpu_ms'])
        self.assertEqual(rows[1]['provenance_status'], 'unknown')

    def test_four_workload_measurements_and_two_auxiliary_nulls_are_disjoint(self):
        run = self.query('query_runs')[0]
        self.assertEqual((run['observed_history_count'], run['auxiliary_query_count'], run['uncaptured_count']), (4, 2, 0))
        workload, helpers = self.query('workload_history'), self.query('auxiliary_reads')
        self.assertEqual((len(workload), len(helpers)), (4, 2))
        self.assertEqual({row['kind'] for row in workload}, {'select', 'explain'})
        self.assertEqual({row['kind'] for row in helpers}, {'own_query_history', 'fixture_stats'})
        self.assertEqual(workload[0]['created_to_end_ms'], 0)
        self.assertTrue(all(row['created_to_end_ms'] is None and row['history_status'] == 'not_target' for row in helpers))
        self.assertFalse({r['query_id'] for r in workload} & {r['query_id'] for r in helpers})

    def test_failed_and_missing_query_history_remain_visible(self):
        self.add('query_history_runs', record_id='failed', workflow_result='failed', observer_outcome='failed',
                 observed_history_count=0, history_missing_count=0, history_unobserved_count=1,
                 auxiliary_query_count=0, uncaptured_count=1, query_run_token='failed-run')
        self.add('query_history_queries', record_id='unknown', query_run_token='failed-run', request_index=0,
                 kind='select', case_id='projection', response_state='FAILED', response_complete=0,
                 history_status='unobserved', query_id='failed-query')
        run = next(row for row in self.query('query_runs') if row['query_run_token'] == 'failed-run')
        self.assertEqual((run['workflow_result'], run['history_unobserved_count'], run['uncaptured_count']), ('failed', 1, 1))
        row = next(row for row in self.query('workload_history') if row['query_id'] == 'failed-query')
        self.assertEqual((row['response_state'], row['history_status']), ('FAILED', 'unobserved'))
        self.assertIsNone(row['created_to_end_ms'])

    def test_retry_duplicates_count_once_and_results_are_bounded(self):
        self.db.execute('INSERT INTO query_history_queries SELECT * FROM query_history_queries')
        self.assertEqual(len(self.query('workload_history')), 4)
        self.assertEqual(len(self.query('auxiliary_reads')), 2)
        self.db.execute('DELETE FROM benchmark_queries')
        for run in range(24):
            for number in range(1, 23):
                for side in ['reference', 'iceberg']:
                    self.add('benchmark_queries', record_id=f'{run}-{number}-{side}', run_token=f'{run:02}', query_number=number, side=side)
        self.assertEqual(len(self.query('benchmark_queries')), D.LIMITS['benchmark_queries'])


class DashboardContractTests(unittest.TestCase):
    def test_generated_json_is_exact_and_staged_outside_provisioned_folder(self):
        self.assertEqual(json.loads(D.OUTPUT.read_text()), D.dashboard())
        self.assertEqual(D.OUTPUT.parent, D.HERE)
        self.assertNotEqual(D.OUTPUT.parent, D.HERE / 'dashboards')
        self.assertEqual(D.dashboard()['uid'], 'shirokuma-query-history-local')

    def test_queries_only_use_exact_schema_columns_and_owned_final_tables(self):
        schemas = {**B.SCHEMAS, **H.SCHEMAS}
        used = set()
        for key, sql in D.QUERIES.items():
            match = re.fullmatch(r'SELECT ([\w,\s]+)\nFROM agenthouse\.(\w+) FINAL\n(?:WHERE [^\n]+\n)?ORDER BY [^\n]+ LIMIT (\d+)', sql)
            self.assertIsNotNone(match, key)
            columns, table, limit = match.groups()
            self.assertIn(table, D.REQUIRED_TABLES)
            used.add(table)
            self.assertLessEqual(set(re.split(r',\s*', columns)), {name for name, _ in schemas[table]})
            self.assertEqual(int(limit), D.LIMITS[key])
            for forbidden in ['${', '$__', ';', 'FORMAT', 'SETTINGS', 'coalesce', 'ifNull', 'avg(', 'sum(', 'count(']:
                self.assertNotIn(forbidden.lower(), sql.lower())
        self.assertEqual(used, set(D.REQUIRED_TABLES))

    def test_native_readonly_layout_null_semantics_and_units(self):
        value = D.dashboard()
        self.assertFalse(value['editable'])
        self.assertEqual(value['refresh'], '')
        self.assertEqual(value['templating']['list'], [])
        self.assertTrue(value['timepicker']['hidden'])
        panels = value['panels'][1:]
        self.assertEqual(len(panels), 5)
        self.assertTrue(all(p['type'] == 'table' and p['datasource'] == D.DATASOURCE for p in panels))
        for panel in panels:
            target = panel['targets'][0]
            self.assertEqual(target['pluginVersion'], '4.21.3')
            self.assertEqual(target['format'], 1)
            self.assertEqual(panel['fieldConfig']['defaults']['noValue'], '未観測')
            self.assertTrue(panel['fieldConfig']['defaults']['custom']['filterable'])
            self.assertTrue(panel['options']['enablePagination'])
            self.assertFalse(panel['options']['footer']['show'])
            overrides = {entry['matcher']['options']: {p['id']: p['value'] for p in entry['properties']}
                         for entry in panel['fieldConfig']['overrides']}
            for column, unit in [('wall_seconds', 's'), ('created_to_end_ms', 'ms'), ('peak_memory_bytes', 'bytes')]:
                if column in target['rawSql']:
                    self.assertEqual(overrides[column]['unit'], unit)
        positions = [p['gridPos'] for p in value['panels']]
        self.assertTrue(all(a['y'] + a['h'] <= b['y'] for a, b in zip(positions, positions[1:])))

    def test_visible_boundaries_and_legible_light_theme_status_colors(self):
        dashboard = D.dashboard()
        text = dashboard['panels'][0]['options']['content']
        for required in ['全期間', '未観測', '実測の 0', '来歴不明', '固定2行', '公式性能値', '性能改善', 'Trino Gateway は未導入']:
            self.assertIn(required, text)
        def linear(channel):
            value = int(channel, 16) / 255
            return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055)**2.4
        for color in D.COLORS.values():
            luminance = sum(weight * linear(color[index:index + 2]) for weight, index in [(0.2126, 1), (0.7152, 3), (0.0722, 5)])
            self.assertGreaterEqual(1.05 / (luminance + 0.05), 4.5)


if __name__ == '__main__':
    unittest.main()
