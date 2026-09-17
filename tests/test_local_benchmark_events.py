"""Receipt integrity and append-only import recovery, without a running database."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('benchmark_events', ROOT / 'tools/local_experiment/benchmark_events/import_receipts.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeStore:
    def __init__(self):
        self.tables = {}
        self.writes = []
        self.partial = False
        self.omit = False
        self.conflict = False

    def inspect(self):
        if self.conflict:
            raise MODULE.ImportError('TABLE_OWNERSHIP_CONFLICT')
        return set(self.tables)

    def create(self, table):
        self.tables[table] = {}
        self.writes.append(('create', table))

    def rows(self, table):
        return copy.deepcopy(self.tables[table])

    def insert(self, table, rows):
        self.writes.append(('insert', table, len(rows)))
        if self.omit:
            return
        for i, row in enumerate(rows):
            self.tables[table][row['record_id']] = copy.deepcopy(row)
            if self.partial and table == MODULE.QUERIES and i == 0:
                self.partial = False
                raise TimeoutError('raw backend details must not escape')


class BenchmarkEventsTests(unittest.TestCase):
    def setUp(self):
        # resolve() keeps macOS /var -> /private/var out of the trusted path.
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.source = self.base / 'benchmark'
        self.source.mkdir(mode=0o700)
        self.token = 'a' * 32

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, path, value):
        path.write_text(json.dumps(value, allow_nan=False))
        path.chmod(0o600)

    def fixture(self, token=None, count=1, known=True, result='failed', recorded=True):
        token = token or self.token
        path = self.source / token
        path.mkdir(mode=0o700)
        benchmark = MODULE.benchmark_module()
        tables = [dict(table=name, relation=f'iceberg.local_experiment.tpch_{token}_{name}', rows=benchmark.SF10_COUNTS[name])
                  for name in benchmark.TABLES] if result == 'passed' else []
        summary = {
            'schema_version': 1, 'run_token': token, 'scope': 'local-experiment-only',
            'query_user': 'shirokuma-local-tpch-' + token,
            'scale_factor': 10, 'queries_expected': 22, 'official_tpc_result': False,
            'result': result, 'version': [['483']], 'started_at': '2026-09-16T09:00:00Z',
            'completed_at': '2026-09-16T09:01:00Z', 'tables': tables, 'queries': [],
            'errors': [] if result == 'passed' else ['secret raw failure /sql/password'],
            'query_drain': {'verified': True, 'user': 'shirokuma-local-tpch-' + token, 'active': [], 'errors': []},
            'cleanup': {'removed': [f'tpch_{token}_{name}' for name in benchmark.TABLES] if tables else [], 'remaining': [], 'errors': []},
            'fixture_unchanged': True, 'fixture_before': {'rows': benchmark.FIXTURE_ROWS},
            'fixture_after': {'rows': benchmark.FIXTURE_ROWS},
            'inputs': {'commit': benchmark.SOURCE_COMMIT}, 'environment': {'co_located': True},
        }
        if known:
            summary['runtime_query_adaptations'] = []
        for number in range(1, count + 1):
            q = f'q{number:02}'
            comparison = {'query': q, 'comparison': {'matched': True, 'rows': 1}}
            for index, side in enumerate(MODULE.SIDES):
                value = {'query_id': f'20260916_090000_{number * 2 + index:05}_{token[:5]}',
                         'columns': ['name', 'name'], 'column_types': ['varchar', 'varchar'],
                         'rows': [['sensitive source value', 'same']], 'wall_seconds': 0.0,
                         'stats': {'state': 'FINISHED', 'elapsedTimeMillis': 0, 'spilledBytes': 0}}
                self.write(path / f'{q}-{side}.json', value)
                comparison[side + '_query_id'] = value['query_id']
                comparison[side + '_wall_seconds'] = value['wall_seconds']
                comparison[side + '_stats'] = value['stats']
                if known:
                    sql = f'SELECT name, name FROM fixture_{token}'
                    (path / f'{q}-{side}.sql').write_text(sql + ';\n')
                    comparison[side + '_sql_sha256'] = MODULE.digest(sql.encode())
            if recorded:
                summary['queries'].append(comparison)
        self.write(path / 'summary.json', summary)
        return path, summary

    def collect(self):
        return MODULE.collect(self.source)

    def apply(self, plan, store, events):
        return MODULE.apply(plan, store, events.append, self.source)

    def test_all_terminal_runs_keep_failures_and_unobserved_denominator(self):
        self.fixture('a' * 32, count=0)
        self.fixture('b' * 32, count=14, known=False)
        self.fixture('c' * 32, count=22, result='passed')
        plan = self.collect()
        self.assertTrue(plan['ready'])
        self.assertEqual(plan['totals'], {'runs': 3, 'passed': 1, 'failed': 2, 'invalid': 0, 'incomplete': 0,
                                         'expected_pairs_valid_runs': 66, 'recorded_pairs': 36,
                                         'comparison_unknown_pairs': 30, 'observed_query_sides': 72})
        self.assertEqual([r['provenance_status'] for r in plan['rows'][MODULE.RUNS]], ['unknown', 'unknown', 'recorded'])
        raw = MODULE.canonical(plan)
        for secret in ('sensitive source value', 'secret raw failure', 'SELECT name', 'password', str(self.base)):
            self.assertNotIn(secret, raw)
        self.assertEqual(len(plan['source_hashes']), 3)

    def test_null_is_distinct_from_measured_zero_and_duplicate_column_names_are_valid(self):
        self.fixture()
        row = self.collect()['rows'][MODULE.QUERIES][0]
        self.assertIsNone(row['cpu_ms'])
        self.assertEqual(row['spilled_bytes'], 0)
        self.assertEqual(row['wall_seconds'], 0.0)
        self.assertEqual(row['engine_elapsed_ms'], 0)

    def test_orphan_result_is_observed_not_an_invented_successful_comparison(self):
        self.fixture(recorded=False)
        plan = self.collect()
        self.assertTrue(plan['ready'])
        self.assertEqual(plan['totals']['recorded_pairs'], 0)
        self.assertEqual(plan['totals']['comparison_unknown_pairs'], 22)
        self.assertEqual(plan['totals']['observed_query_sides'], 2)
        self.assertEqual({r['comparison_status'] for r in plan['rows'][MODULE.QUERIES]}, {'unobserved'})

    def test_incomplete_and_malformed_runs_are_visible_and_block_every_write(self):
        self.fixture()
        path, summary = self.fixture('b' * 32)
        summary['result'] = 'running'
        self.write(path / 'summary.json', summary)
        missing = self.source / ('c' * 32)
        missing.mkdir(mode=0o700)
        malformed = self.source / ('d' * 32)
        malformed.mkdir(mode=0o700)
        (malformed / 'summary.json').write_text('{bad json and secret}')
        plan = self.collect()
        self.assertFalse(plan['ready'])
        self.assertEqual(plan['totals']['incomplete'], 2)
        self.assertEqual(plan['totals']['invalid'], 1)
        store, events = FakeStore(), []
        with self.assertRaisesRegex(MODULE.ImportError, 'INVENTORY_NOT_READY'):
            self.apply(plan, store, events)
        self.assertFalse(store.writes)

    def test_sql_tampering_summary_disagreement_and_false_pass_fail_closed(self):
        path, summary = self.fixture()
        sql = path / 'q01-reference.sql'
        original = sql.read_text()
        sql.write_text(original + 'SELECT unrelated;')
        self.assertEqual(self.collect()['issues'][0]['code'], 'SQL_HASH_MISMATCH')
        sql.write_text(original)
        summary['queries'][0]['reference_wall_seconds'] = 5
        self.write(path / 'summary.json', summary)
        self.assertEqual(self.collect()['issues'][0]['code'], 'SUMMARY_QUERY_MISMATCH')
        summary['queries'][0]['reference_wall_seconds'] = 0.0
        summary['result'] = 'passed'
        self.write(path / 'summary.json', summary)
        self.assertEqual(self.collect()['issues'][0]['code'], 'FALSE_PASS_CLAIM')

    def test_retained_result_tampering_rechecks_independent_comparison(self):
        path, _ = self.fixture()
        value = json.loads((path / 'q01-iceberg.json').read_text())
        value['rows'][0][0] = 'changed result'
        self.write(path / 'q01-iceberg.json', value)
        self.assertEqual(self.collect()['issues'][0]['code'], 'RESULT_COMPARISON_MISMATCH')

    def test_failed_receipt_still_requires_bound_recorded_query_user(self):
        path, summary = self.fixture()
        summary['query_user'] = 'unrelated-user'
        summary['query_drain'] = {'verified': False}
        self.write(path / 'summary.json', summary)
        self.assertEqual(self.collect()['issues'][0]['code'], 'SUMMARY_IDENTITY_MISMATCH')

    def test_missing_result_and_duplicate_query_id_are_not_silently_skipped(self):
        path, _ = self.fixture()
        (path / 'q01-reference.json').unlink()
        self.assertEqual(self.collect()['issues'][0]['code'], 'QUERY_RESULT_MISSING')
        self.write(path / 'q01-reference.json', json.loads((path / 'q01-iceberg.json').read_text()))
        self.assertEqual(self.collect()['issues'][0]['code'], 'INVALID_QUERY_IDENTITY')

    def test_nonfinite_duplicate_json_and_hardlinks_are_rejected_without_raw_errors(self):
        path, _ = self.fixture()
        (path / 'summary.json').write_text('{"schema_version": 1, "schema_version": NaN}')
        self.assertEqual(self.collect()['issues'][0]['code'], 'NONFINITE_JSON')
        (path / 'summary.json').write_text('{"schema_version": 1, "schema_version": 1}')
        self.assertEqual(self.collect()['issues'][0]['code'], 'DUPLICATE_JSON_KEY')
        os.link(path / 'q01-reference.json', path / 'hardlink.json')
        self.assertEqual(self.collect()['issues'][0]['code'], 'UNSAFE_SOURCE_FILE')

    def test_symlink_and_size_bounds(self):
        path, _ = self.fixture()
        (path / 'bad.log').symlink_to(path / 'summary.json')
        self.assertEqual(self.collect()['issues'][0]['code'], 'SOURCE_UNREADABLE')
        (path / 'bad.log').unlink()
        with patch.object(MODULE, 'MAX_FILE_BYTES', 10):
            self.assertEqual(self.collect()['issues'][0]['code'], 'SOURCE_FILE_TOO_LARGE')

    def test_repeat_import_and_partial_timeout_recovery_preserve_exact_rows(self):
        self.fixture()
        plan, store, events = self.collect(), FakeStore(), []
        store.partial = True
        with self.assertRaises(TimeoutError):
            self.apply(plan, store, events)
        self.assertEqual(events[-1]['pending_outcome'], 'unknown')
        self.assertEqual(events[-1]['pending']['table'], MODULE.QUERIES)
        self.assertFalse(events[-1]['readback_verified'])
        self.assertNotIn('raw backend details', MODULE.canonical(events))
        saved = copy.deepcopy(store.tables)
        recovered = self.apply(plan, store, [])
        self.assertEqual(recovered['inserted'], {MODULE.RUNS: 0, MODULE.QUERIES: 1})
        for table, rows in saved.items():
            for key, row in rows.items():
                self.assertEqual(store.tables[table][key], row)
        after = copy.deepcopy(store.tables)
        repeat = self.apply(plan, store, [])
        self.assertEqual(repeat['inserted'], {MODULE.RUNS: 0, MODULE.QUERIES: 0})
        self.assertTrue(repeat['readback_verified'])
        self.assertTrue(repeat['source_unchanged'])
        self.assertEqual(after, store.tables)

    def test_retained_row_conflict_precedes_creation_or_insertion(self):
        self.fixture()
        plan, store = self.collect(), FakeStore()
        row = copy.deepcopy(plan['rows'][MODULE.RUNS][0])
        row['result'] = 'passed'
        store.tables[MODULE.RUNS] = {row['record_id']: row}
        with self.assertRaisesRegex(MODULE.ImportError, 'RETAINED_ROW_CONFLICT'):
            self.apply(plan, store, [])
        self.assertEqual(store.writes, [])
        self.assertNotIn(MODULE.QUERIES, store.tables)

    def test_false_insert_ack_and_ownership_conflict_do_not_report_success(self):
        self.fixture()
        plan, store, events = self.collect(), FakeStore(), []
        store.omit = True
        with self.assertRaisesRegex(MODULE.ImportError, 'INSERT_READBACK_MISMATCH'):
            self.apply(plan, store, events)
        self.assertEqual(events[-1]['result'], 'failed')
        self.assertIsNotNone(events[-1]['pending'])
        conflict = FakeStore()
        conflict.conflict = True
        with self.assertRaisesRegex(MODULE.ImportError, 'TABLE_OWNERSHIP_CONFLICT'):
            self.apply(plan, conflict, [])
        self.assertFalse(conflict.writes)

    def test_input_or_in_memory_plan_change_stops_before_mutation(self):
        path, _ = self.fixture()
        plan = self.collect()
        (path / 'new.log').write_text('new evidence')
        store = FakeStore()
        with self.assertRaisesRegex(MODULE.ImportError, 'PLAN_CHANGED'):
            self.apply(plan, store, [])
        self.assertFalse(store.writes)
        plan = self.collect()
        plan['rows'][MODULE.QUERIES][0]['wall_seconds'] = 10
        with self.assertRaisesRegex(MODULE.ImportError, 'PLAN_CHANGED'):
            self.apply(plan, store, [])
        self.assertFalse(store.writes)

    def test_source_change_during_insert_is_unverified_and_never_completed(self):
        path, _ = self.fixture()
        plan, store, events = self.collect(), FakeStore(), []
        original = store.insert
        def changing(table, rows):
            original(table, rows)
            (path / 'new.log').write_text('later content')
        store.insert = changing
        with self.assertRaisesRegex(MODULE.ImportError, 'SOURCE_CHANGED'):
            self.apply(plan, store, events)
        self.assertFalse(events[-1]['source_unchanged'])
        self.assertFalse(events[-1]['readback_verified'])
        self.assertEqual(events[-1]['pending_outcome'], 'unknown')

    def test_cancellation_after_insert_keeps_pending_receipt_and_allows_retry(self):
        self.fixture()
        plan, store, events = self.collect(), FakeStore(), []
        original = store.insert
        def interrupted(table, rows):
            original(table, rows)
            raise KeyboardInterrupt()
        store.insert = interrupted
        with self.assertRaises(KeyboardInterrupt):
            self.apply(plan, store, events)
        self.assertEqual(events[-1]['result'], 'interrupted')
        self.assertEqual(events[-1]['pending_outcome'], 'unknown')
        store.insert = original
        self.assertEqual(self.apply(plan, store, [])['result'], 'passed')

    def test_private_evidence_and_progress_failure_before_pending_causes_no_write(self):
        self.fixture()
        plan, store = self.collect(), FakeStore()
        output = self.base / 'output'
        output.mkdir(mode=0o700)
        MODULE.private_write(output / 'plan.json', plan)
        self.assertEqual((output / 'plan.json').stat().st_mode & 0o777, 0o600)
        def rejected(_):
            raise OSError('no evidence storage')
        with self.assertRaises(OSError):
            MODULE.apply(plan, store, rejected, self.source)
        self.assertFalse(store.writes)

    def test_store_checks_physical_duplicate_conflicts_and_normalizes_float_json(self):
        self.fixture()
        row = self.collect()['rows'][MODULE.QUERIES][0]
        integral_float = copy.deepcopy(row)
        integral_float['wall_seconds'] = 0
        class Client:
            def __init__(self, values):
                self.values, self.sql = values, []
            def query(self, sql):
                self.sql.append(sql)
                return '\n'.join(json.dumps(value) for value in self.values)
        client = Client([row, integral_float])
        self.assertEqual(MODULE.Store(client).rows(MODULE.QUERIES), {row['record_id']: row})
        self.assertNotIn(' FINAL', client.sql[0])
        other = copy.deepcopy(row)
        other['wall_seconds'] = 10.0
        other['payload_sha256'] = MODULE.digest({k: v for k, v in other.items() if k != 'payload_sha256'})
        with self.assertRaisesRegex(MODULE.ImportError, 'DATABASE_ROW_CONFLICT'):
            MODULE.Store(Client([row, other])).rows(MODULE.QUERIES)

    def test_exact_schema_comment_and_fixed_database_sql(self):
        class Client:
            def __init__(self):
                self.sql = []
                self.bad = False
            def query(self, sql):
                self.sql.append(sql)
                if 'FROM system.tables' in sql:
                    return '\n'.join(json.dumps({'name': name, 'engine': 'ReplacingMergeTree', 'sorting_key': 'record_id',
                                                   'engine_full': 'ReplacingMergeTree ORDER BY record_id SETTINGS index_granularity = 8192',
                                                   'primary_key': 'record_id', 'partition_key': '', 'sampling_key': '',
                                                   'create_table_query': MODULE.ddl(name),
                                                   'comment': 'foreign' if self.bad else MODULE.marker(name)}) for name in MODULE.SCHEMAS)
                if 'FROM system.columns' in sql:
                    return '\n'.join(json.dumps({'table': table, 'name': name, 'type': kind, 'position': i + 1,
                                                   'default_kind': '', 'default_expression': ''})
                                     for table, columns in sorted(MODULE.SCHEMAS.items()) for i, (name, kind) in enumerate(columns))
                return ''
        client = Client()
        store = MODULE.Store(client)
        self.assertEqual(store.inspect(), set(MODULE.SCHEMAS))
        client.bad = True
        with self.assertRaisesRegex(MODULE.ImportError, 'TABLE_OWNERSHIP_CONFLICT'):
            store.inspect()
        with self.assertRaisesRegex(MODULE.ImportError, 'UNKNOWN_TABLE'):
            MODULE.ddl('pawprints')
        with self.assertRaisesRegex(MODULE.ImportError, 'INVALID_INSERT'):
            store.insert('pawprints', [{}])
        for sql in client.sql:
            self.assertNotIn('pawprints', sql)
            self.assertIn('max_execution_time=20', sql)

    def test_engine_version_partition_and_ttl_are_refused(self):
        class Client:
            field, value = 'engine_full', 'ReplacingMergeTree(source_schema_version) ORDER BY record_id'
            def query(self, sql):
                if 'FROM system.tables' in sql:
                    value = {'name': MODULE.RUNS, 'engine': 'ReplacingMergeTree', 'sorting_key': 'record_id',
                             'engine_full': 'ReplacingMergeTree ORDER BY record_id', 'primary_key': 'record_id',
                             'partition_key': '', 'sampling_key': '', 'create_table_query': MODULE.ddl(MODULE.RUNS),
                             'comment': MODULE.marker(MODULE.RUNS)}
                    value[self.field] = self.value
                    return json.dumps(value)
                return ''
        client = Client()
        for field, value in [('engine_full', 'ReplacingMergeTree(source_schema_version) ORDER BY record_id'),
                             ('partition_key', 'toYYYYMM(started_at)'), ('primary_key', 'run_token'),
                             ('create_table_query', MODULE.ddl(MODULE.RUNS) + ' TTL completed_at + INTERVAL 1 DAY')]:
            client.field, client.value = field, value
            with self.subTest(field=field), self.assertRaisesRegex(MODULE.ImportError, 'TABLE_ENGINE_CONFLICT'):
                MODULE.Store(client).inspect()

    def test_runtime_scope_refuses_public_port_wrong_image_mount_and_resources(self):
        class Runtime:
            VOLUME, IMAGE, LABEL = 'owned', 'pinned-image', 'owner-label'
        good = {'State': {'Running': True}, 'Config': {'Image': Runtime.IMAGE, 'Labels': {Runtime.LABEL: 'agenthouse-local'}},
                'HostConfig': {'NanoCpus': 2_000_000_000, 'Memory': 4 * 1024**3,
                               'PortBindings': {'8123/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18123'}]}},
                'Mounts': [{'Type': 'volume', 'Name': Runtime.VOLUME, 'Destination': '/var/lib/clickhouse', 'RW': True}]}
        MODULE.validate_runtime(Runtime, good)
        for kind in ('port', 'mount', 'image', 'memory'):
            changed = copy.deepcopy(good)
            if kind == 'port':
                changed['HostConfig']['PortBindings']['8123/tcp'][0]['HostIp'] = '0.0.0.0'
            elif kind == 'mount':
                changed['Mounts'][0]['Name'] = 'retained-unrelated'
            elif kind == 'image':
                changed['Config']['Image'] = 'different'
            else:
                changed['HostConfig']['Memory'] = 0
            with self.subTest(kind=kind), self.assertRaisesRegex(MODULE.ImportError, 'AGENTHOUSE_SCOPE_CONFLICT'):
                MODULE.validate_runtime(Runtime, changed)


if __name__ == '__main__':
    unittest.main()
