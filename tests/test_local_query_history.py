"""Owned source projection, additive ClickHouse schema, and retry boundaries."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('query_history_test', ROOT / 'tools/local_experiment/query_history/import_receipts.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
EVIDENCE, TOKEN = 'a' * 32, 'b' * 32
IDS = ['20260917_041733_0033' + str(i) + '_b69ae' for i in range(1, 8)]


def fixtures():
    requests = [{'case_id': case, 'kind': kind, 'query_id': IDS[i], 'state': 'FINISHED', 'response_complete': True}
                for i, (case, kind) in enumerate(M.PAIRS)]
    requests.extend({'case_id': None, 'kind': kind, 'query_id': IDS[i], 'state': 'FINISHED', 'response_complete': True}
                    for i, kind in [(4, 'own_query_history'), (5, 'fixture_stats')])
    entries = []
    for i, (case, kind) in enumerate(M.PAIRS):
        entries.append({'query_id': IDS[i], 'case_id': case, 'kind': kind, 'state': 'FINISHED',
            'created_at': '2026-09-17T04:17:34.098000Z', 'started_at': '2026-09-17T04:17:34.126000Z',
            'ended_at': '2026-09-17T04:17:35.221000Z', 'created_to_end_ms': 1123, 'started_to_end_ms': 1095,
            'queued_ms': 0, 'analysis_ms': 27, 'planning_ms': 2, 'error_type': None, 'error_code': None})
    history = {'query_id': IDS[4], 'observed_at': '2026-09-17T04:17:46.809583Z', 'completeness': 'complete',
               'durations_complete': True, 'entries': entries, 'missing_query_ids': []}
    observation = {'schema_version': 1, 'run_token': TOKEN, 'observed_at': '2026-09-17T04:17:46.809606Z',
                   'performance_gain': 'unmeasured', 'history': history,
                   'cases': [{'id': case, 'select': {'query_id': IDS[i * 2], 'row_count': 2 - i, 'matched': True},
                              'explain': {'query_id': IDS[i * 2 + 1], 'plan': {}}}
                             for i, case in enumerate(['projection', 'filtered'])]}
    receipt = {'schema_version': 1, 'run_token': TOKEN, 'outcome': 'completed', 'server_max_run_seconds': 30,
               'cleanup_verified': True, 'target_history_verified': True, 'requests': requests, 'uncaptured_requests': 0}
    return {'observer-receipt.json': receipt, 'observation.json': observation,
            'result.json': {'result': 'passed', 'scope': 'fixed-two-row-fixture-only', 'query_run_token': TOKEN,
                            'cleanup_verified': True, 'evidence_path': '/private/path', 'provider': 'private-model-name',
                            'task_id': 'workflow-' + TOKEN, 'trace_id': 'f' * 32, 'checkpoint': 'completed-in-memory',
                            'saved_otel_spans_verified': 1, 'audit_records': [{}]},
            'spans.jsonl': [{'name': 'bear.query.assess', 'trace_id': 'f' * 32, 'span_id': 'e' * 16,
                             'start_time_unix_nano': 1, 'end_time_unix_nano': 2, 'status': 'OK',
                             'attributes': {'task_id': 'workflow-' + TOKEN}}]}


def encoded(name, data):
    return ('\n'.join(M.canonical(row) for row in data) if name.endswith('.jsonl') else M.canonical(data)).encode()


def failed(value):
    value['result.json'].update(result='failed', cleanup_verified=False, error_code='QUERY_OBSERVATION_PLAN_FOLLOWUP_OR_AUDIT_FAILED')
    value['spans.jsonl'][0]['status'] = 'ERROR'


def write_run(source, value, token=EVIDENCE):
    path = source / token
    path.mkdir(mode=0o700, exist_ok=True)
    for name, data in value.items():
        p = path / name
        p.write_bytes(encoded(name, data))
        p.chmod(0o600)
    return path


class FakeStore:
    def __init__(self):
        self.present, self.data, self.writes = set(), {t: {} for t in M.SCHEMAS}, []
        self.fail_insert = False
        self.hide_readback = False

    def inspect(self):
        return set(self.present)

    def create(self, table):
        self.writes.append(('create', table))
        self.present.add(table)

    def rows(self, table):
        if self.hide_readback and self.data[table]:
            return {}
        return copy.deepcopy(self.data[table])

    def insert(self, table, rows):
        self.writes.append(('insert', table))
        for row in rows:
            self.data[table][row['record_id']] = copy.deepcopy(row)
            if self.fail_insert:
                self.fail_insert = False
                raise RuntimeError('private password and backend raw SQL')


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name).resolve()

    def test_success_preserves_distinct_tokens_and_auxiliary_unknowns(self):
        write_run(self.source, fixtures())
        plan = M.collect(self.source)
        self.assertTrue(plan['ready'], plan['issues'])
        run = plan['rows'][M.RUNS][0]
        self.assertEqual((run['evidence_token'], run['query_run_token']), (EVIDENCE, TOKEN))
        self.assertEqual(run['query_user'], 'shirokuma-query-doctor-' + TOKEN)
        self.assertEqual((run['captured_count'], run['observed_history_count'], run['auxiliary_query_count']), (6, 4, 2))
        self.assertEqual(run['provenance'], M.PROVENANCE)
        queries = plan['rows'][M.QUERIES]
        self.assertEqual(queries[0]['queued_ms'], 0)
        self.assertEqual(queries[0]['created_to_end_ms'], 1123)
        for row in queries[4:]:
            self.assertEqual(row['history_status'], 'not_target')
            self.assertTrue(all(row[key] is None for key in M.TIMES + M.DURATIONS))
        saved = M.canonical(plan['rows'])
        for forbidden in ['/private/path', 'private-model-name', 'error_code', 'SELECT ', 'query_text']:
            self.assertNotIn(forbidden, saved)

    def test_failed_captured_and_uncaptured_requests_are_not_silently_dropped(self):
        value = fixtures()
        value.pop('observation.json')
        failed(value)
        receipt = value['observer-receipt.json']
        receipt.update(outcome='failed', cleanup_verified=False, target_history_verified=False)
        receipt['requests'] = [{**receipt['requests'][0], 'state': 'FAILED', 'response_complete': False}]
        write_run(self.source, value)
        plan = M.collect(self.source)
        self.assertTrue(plan['ready'], plan['issues'])
        row = plan['rows'][M.QUERIES][0]
        self.assertEqual(row['response_state'], 'FAILED')
        self.assertEqual(row['history_status'], 'unobserved')
        self.assertIsNone(row['created_to_end_ms'])
        self.assertEqual(plan['rows'][M.RUNS][0]['workflow_result'], 'failed')
        receipt['requests'][0].update(query_id=None, state=None)
        receipt['uncaptured_requests'] = 1
        write_run(self.source, value)
        plan = M.collect(self.source)
        self.assertTrue(plan['ready'])
        self.assertEqual(plan['rows'][M.QUERIES], [])
        self.assertEqual(plan['rows'][M.RUNS][0]['uncaptured_count'], 1)
        self.assertEqual(plan['totals']['uncaptured_requests'], 1)

    def test_missing_history_is_distinct_from_zero_duration(self):
        value = fixtures()
        value['result.json']['result'] = 'failed'
        value['observer-receipt.json'].update(outcome='incomplete', cleanup_verified=False, target_history_verified=False)
        h = value['observation.json']['history']
        h.update(entries=[], missing_query_ids=IDS[:4], completeness='missing', durations_complete=False)
        write_run(self.source, value)
        plan = M.collect(self.source)
        self.assertFalse(plan['ready'])
        self.assertEqual(plan['issues'][0]['code'], 'SOURCE_NOT_TERMINAL')
        self.assertEqual(plan['rows'][M.RUNS][0]['history_missing_count'], 4)
        for row in plan['rows'][M.QUERIES][:4]:
            self.assertEqual(row['history_status'], 'missing')
            self.assertIsNone(row['queued_ms'])

    def test_latest_history_missing_keeps_explicit_missing_status(self):
        value = fixtures()
        value['result.json']['result'] = 'failed'
        value['observer-receipt.json'].update(outcome='incomplete', cleanup_verified=False, target_history_verified=False)
        value['observer-receipt.json']['requests'].append({'case_id': None, 'kind': 'own_query_history', 'query_id': IDS[6],
                                                          'state': 'FINISHED', 'response_complete': True})
        h = copy.deepcopy(value['observation.json']['history'])
        h.update(query_id=IDS[6], observed_at='2026-09-17T04:18:46.809583Z', entries=[], missing_query_ids=IDS[:4],
                 completeness='missing', durations_complete=False)
        value['own_query_history.json'] = h
        write_run(self.source, value)
        plan = M.collect(self.source)
        self.assertFalse(plan['ready'])
        self.assertEqual(plan['issues'][0]['code'], 'SOURCE_NOT_TERMINAL')
        self.assertEqual(plan['rows'][M.RUNS][0]['history_snapshot_count'], 2)
        self.assertEqual(plan['rows'][M.RUNS][0]['history_missing_count'], 4)

    def test_history_user_foreign_query_ref_and_source_token_are_rejected(self):
        mutations = [lambda v: v['observation.json']['history']['entries'][0].update(user='foreign'),
                     lambda v: v['observation.json']['history']['entries'][0].update(query_id=IDS[5]),
                     lambda v: v['observation.json']['history'].update(query_id=IDS[5]),
                     lambda v: v['observation.json'].update(run_token='c' * 32),
                     lambda v: v['result.json'].update(query_run_token='c' * 32),
                     lambda v: v['observation.json']['cases'][0]['select'].update(query_id=IDS[2])]
        for mutate in mutations:
            value = fixtures()
            mutate(value)
            write_run(self.source, value)
            self.assertFalse(M.collect(self.source)['ready'])

    def test_duration_and_completeness_contradictions_are_rejected(self):
        mutations = [lambda v: v['observation.json']['history']['entries'][0].update(created_to_end_ms=0),
                     lambda v: v['observation.json']['history']['entries'][0].update(queued_ms=True),
                     lambda v: v['observation.json']['history']['entries'][0].update(created_at='2026-09-18T00:00:00.000000Z'),
                     lambda v: v['observer-receipt.json'].update(uncaptured_requests=1),
                     lambda v: v['observer-receipt.json'].update(cleanup_verified=False),
                     lambda v: v['observation.json']['history'].update(durations_complete=False),
                     lambda v: v['observer-receipt.json'].update(schema_version=True)]
        for mutate in mutations:
            value = fixtures()
            mutate(value)
            write_run(self.source, value)
            self.assertFalse(M.collect(self.source)['ready'])

    def test_same_query_id_across_runs_blocks_whole_plan(self):
        write_run(self.source, fixtures())
        value = fixtures()
        value['observer-receipt.json']['run_token'] = 'c' * 32
        value['observation.json']['run_token'] = 'c' * 32
        value['result.json']['query_run_token'] = 'c' * 32
        value['result.json']['task_id'] = 'workflow-' + 'c' * 32
        value['spans.jsonl'][0]['attributes']['task_id'] = 'workflow-' + 'c' * 32
        write_run(self.source, value, 'd' * 32)
        plan = M.collect(self.source)
        self.assertFalse(plan['ready'])
        self.assertEqual(plan['totals']['inventory_runs'], 2)
        self.assertEqual(plan['issues'][0]['code'], 'QUERY_ID_REUSED_ACROSS_RUNS')
        with self.assertRaises(M.ImportFailure):
            M.apply(plan, FakeStore(), lambda _: None, self.source)

    def test_missing_receipt_duplicate_json_and_symlinks_fail_closed(self):
        path = write_run(self.source, {'result.json': {}})
        plan = M.collect(self.source)
        self.assertFalse(plan['ready'])
        self.assertEqual(plan['inventory'][0]['code'], 'OBSERVER_RECEIPT_MISSING')
        for raw in ['{"x":1,"x":2}', '{"x":NaN}']:
            with self.assertRaises(M.ImportFailure):
                M.strict_json(raw)
        (path / 'link').symlink_to(path / 'result.json')
        with self.assertRaises(M.ImportFailure):
            M.collect(self.source)

    def test_source_mutation_and_plan_tampering_stop_before_writes(self):
        path = write_run(self.source, fixtures())
        plan = M.collect(self.source)
        store = FakeStore()
        (path / 'additional.json').write_text('{}')
        with self.assertRaises(M.ImportFailure):
            M.apply(plan, store, lambda _: None, self.source)
        self.assertEqual(store.writes, [])
        plan = M.collect(self.source)
        plan['rows'][M.QUERIES][0]['query_user'] = 'foreign'
        with self.assertRaises(M.ImportFailure):
            M.apply(plan, store, lambda _: None, self.source)
        self.assertEqual(store.writes, [])

    def test_active_default_failed_requires_closed_terminal_evidence(self):
        for mutation in [lambda v: v['result.json'].update(result='running'),
                         lambda v: v['result.json'].update(result='failed'),
                         lambda v: v['result.json'].pop('saved_otel_spans_verified'),
                         lambda v: v['spans.jsonl'][0].pop('end_time_unix_nano'),
                         lambda v: v['spans.jsonl'][0].update(trace_id='e' * 32),
                         lambda v: v.pop('result.json')]:
            value = fixtures()
            mutation(value)
            path = self.source / EVIDENCE
            if path.exists():
                for file in path.iterdir():
                    file.unlink()
            write_run(self.source, value)
            plan = M.collect(self.source)
            self.assertFalse(plan['ready'])
            self.assertEqual(plan['inventory'][0]['status'], 'pending_terminal')
            self.assertEqual(len(plan['rows'][M.QUERIES]), 6)
            store = FakeStore()
            with self.assertRaises(M.ImportFailure):
                M.apply(plan, store, lambda _: None, self.source)
            self.assertEqual(store.writes, [])

    def test_interrupted_terminal_requires_signal_marker(self):
        value = fixtures()
        failed(value)
        value.pop('observation.json')
        receipt = value['observer-receipt.json']
        receipt.update(outcome='interrupted', cleanup_verified=False, target_history_verified=False)
        receipt['requests'] = [{**receipt['requests'][0], 'state': 'RUNNING', 'response_complete': False}]
        write_run(self.source, value)
        self.assertFalse(M.collect(self.source)['ready'])
        value['interruption.json'] = {'result': 'interrupted', 'signal': 15}
        write_run(self.source, value)
        plan = M.collect(self.source)
        self.assertTrue(plan['ready'], plan['issues'])
        self.assertEqual(plan['rows'][M.RUNS][0]['observer_outcome'], 'interrupted')

    def test_bounded_file_reader_rejects_hardlinks_permissions_and_size(self):
        path = write_run(self.source, fixtures()) / 'result.json'
        other = path.with_name('hardlink.json')
        os.link(path, other)
        with self.assertRaises(M.ImportFailure):
            M.read_file(path)
        other.unlink()
        path.chmod(0o666)
        with self.assertRaises(M.ImportFailure):
            M.read_file(path)
        path.chmod(0o600)
        with mock.patch.object(M, 'MAX_FILE_BYTES', 1), self.assertRaises(M.ImportFailure):
            M.read_file(path)

    def test_additive_apply_repeat_and_unknown_partial_retry(self):
        write_run(self.source, fixtures())
        plan = M.collect(self.source)
        store, journal = FakeStore(), []
        first = M.apply(plan, store, journal.append, self.source)
        self.assertEqual(first['inserted'], {M.RUNS: 1, M.QUERIES: 6})
        self.assertTrue(first['readback_verified'] and first['source_unchanged'])
        self.assertIsNone(first['pending'])
        self.assertEqual(M.apply(plan, store, journal.append, self.source)['inserted'], {M.RUNS: 0, M.QUERIES: 0})
        store = FakeStore()
        store.fail_insert = True
        journal = []
        with self.assertRaises(RuntimeError):
            M.apply(plan, store, journal.append, self.source)
        self.assertEqual(journal[-1]['pending']['outcome'], 'unknown')
        self.assertNotIn('password', M.canonical(journal))
        retry = M.apply(plan, store, journal.append, self.source)
        self.assertEqual(retry['inserted'], {M.RUNS: 0, M.QUERIES: 6})

    def test_conflicts_preflight_and_failed_readback_never_pass(self):
        write_run(self.source, fixtures())
        plan = M.collect(self.source)
        store = FakeStore()
        store.present = set(M.SCHEMAS)
        existing = copy.deepcopy(plan['rows'][M.QUERIES][0])
        existing['response_state'] = 'FAILED'
        store.data[M.QUERIES][existing['record_id']] = existing
        with self.assertRaises(M.ImportFailure):
            M.apply(plan, store, lambda _: None, self.source)
        self.assertEqual(store.writes, [])
        store, journal = FakeStore(), []
        store.hide_readback = True
        with self.assertRaises(M.ImportFailure):
            M.apply(plan, store, journal.append, self.source)
        self.assertEqual(journal[-1]['failure_code'], 'INSERT_READBACK_MISMATCH')
        self.assertFalse(journal[-1]['readback_verified'])

    def test_progress_persist_failure_and_signal_keep_no_false_success(self):
        write_run(self.source, fixtures())
        plan = M.collect(self.source)
        store = FakeStore()
        journal = []
        def progress(value):
            journal.append(value)
            if value['pending'] and value['pending']['operation'] == 'insert':
                raise OSError('private path')
        with self.assertRaises(OSError):
            M.apply(plan, store, progress, self.source)
        self.assertFalse(any(action == 'insert' for action, _ in store.writes))
        store = FakeStore()
        def stop(*args):
            raise KeyboardInterrupt()
        store.insert = stop
        journal = []
        with self.assertRaises(KeyboardInterrupt):
            M.apply(plan, store, journal.append, self.source)
        self.assertEqual(journal[-1]['result'], 'interrupted')
        self.assertEqual(journal[-1]['pending']['outcome'], 'unknown')


class FakeClient:
    def __init__(self):
        self.calls, self.values = [], []
        self.tables = [{'name': table, 'engine': 'ReplacingMergeTree', 'engine_full': 'ReplacingMergeTree ORDER BY record_id SETTINGS index_granularity = 8192',
                        'sorting_key': 'record_id', 'primary_key': 'record_id', 'partition_key': '', 'sampling_key': '',
                        'create_table_query': M.ddl(table), 'comment': M.marker(table)} for table in M.SCHEMAS]
        self.columns = [{'table': table, 'name': name, 'type': typ, 'position': index + 1,
                         'default_kind': '', 'default_expression': ''}
                        for table, columns in M.SCHEMAS.items() for index, (name, typ) in enumerate(columns)]

    def query(self, sql):
        self.calls.append(sql)
        rows = self.tables if 'FROM system.tables' in sql else self.columns if 'FROM system.columns' in sql else self.values
        return '\n'.join(M.canonical(v) for v in rows)


class DatabaseTests(unittest.TestCase):
    def test_exact_schema_marker_and_engine_are_required(self):
        client = FakeClient()
        self.assertEqual(M.Store(client).inspect(), set(M.SCHEMAS))
        mutations = [lambda c: c.tables[0].update(comment='someone else'),
                     lambda c: c.tables[0].update(engine_full='ReplacingMergeTree(version) ORDER BY record_id'),
                     lambda c: c.tables[0].update(partition_key='query_user'),
                     lambda c: c.tables[0].update(create_table_query=c.tables[0]['create_table_query'] + ' TTL now()'),
                     lambda c: c.columns[0].update(default_kind='DEFAULT'),
                     lambda c: c.columns[0].update(position=2),
                     lambda c: c.columns[0].update(type='String')]
        for mutate in mutations:
            client = FakeClient()
            mutate(client)
            with self.assertRaises(M.ImportFailure):
                M.Store(client).inspect()

    def test_physical_duplicates_never_hide_conflicting_payloads(self):
        value = fixtures()
        raw = {k: encoded(k, v) for k, v in value.items()}
        _, rows = M.project_run(EVIDENCE, raw, {k: M.digest(v) for k, v in raw.items()})
        client = FakeClient()
        client.values = [rows[0], copy.deepcopy(rows[0])]
        self.assertEqual(len(M.Store(client).rows(M.QUERIES)), 1)
        self.assertNotIn(' FINAL', client.calls[-1])
        client.values[1]['queued_ms'] = 99
        client.values[1]['payload_sha256'] = M.digest({k: v for k, v in client.values[1].items() if k != 'payload_sha256'})
        with self.assertRaisesRegex(M.ImportFailure, 'DATABASE_PHYSICAL_ROW_CONFLICT'):
            M.Store(client).rows(M.QUERIES)
        client.values = [copy.deepcopy(rows[0])]
        client.values[0]['query_user'] = 'foreign'
        client.values[0]['payload_sha256'] = M.digest({k: v for k, v in client.values[0].items() if k != 'payload_sha256'})
        with self.assertRaisesRegex(M.ImportFailure, 'DATABASE_QUERY_USER_CONFLICT'):
            M.Store(client).rows(M.QUERIES)

    def test_only_additive_owned_sql_and_bounds(self):
        client = FakeClient()
        store = M.Store(client)
        store.create(M.RUNS)
        store.insert(M.QUERIES, [{'safe': 'value'}])
        self.assertTrue(client.calls[0].startswith('CREATE TABLE agenthouse.query_history_runs'))
        self.assertTrue(client.calls[1].startswith('INSERT INTO agenthouse.query_history_queries SETTINGS max_execution_time=20'))
        for sql in client.calls:
            self.assertNotIn('DROP ', sql)
            self.assertNotIn('ALTER ', sql)
        with self.assertRaises(M.ImportFailure):
            store.insert('pawprints', [{}])

    def test_runtime_scope_and_private_lock(self):
        module = SimpleNamespace(IMAGE='image@sha256:fixed', LABEL='owner', VOLUME='owned-volume')
        owned = {'Id': 'same-container', 'State': {'Running': True}, 'Config': {'Image': module.IMAGE, 'Labels': {'owner': 'agenthouse-local'}},
                 'HostConfig': {'PortBindings': {'8123/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18123'}]},
                                'NanoCpus': 2000000000, 'Memory': 4 * 1024**3},
                 'Mounts': [{'Type': 'volume', 'Name': module.VOLUME, 'Destination': '/var/lib/clickhouse', 'RW': True}]}
        M.validate_runtime(module, owned)
        altered = copy.deepcopy(owned)
        altered['HostConfig']['PortBindings']['8123/tcp'][0]['HostIp'] = '0.0.0.0'
        with self.assertRaises(M.ImportFailure):
            M.validate_runtime(module, altered)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve()
            M.private_write(path / 'evidence.json', {'safe': True})
            self.assertEqual((path / 'evidence.json').stat().st_mode & 0o777, 0o600)
            with mock.patch.object(M, 'STATE', path), M.importer_lock():
                with self.assertRaisesRegex(M.ImportFailure, 'IMPORT_ALREADY_RUNNING'):
                    with M.importer_lock():
                        pass


if __name__ == '__main__':
    unittest.main()
