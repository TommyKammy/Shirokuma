import copy
import importlib.util
import json
from pathlib import Path
import re
import stat
import signal
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('iceberg_sandbox_run', ROOT / 'tools/local_experiment/iceberg_sandbox/run.py')
S = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(S)
TOKEN = 'a' * 32


class Signal(BaseException):
    pass


class Database:
    """Tiny stateful SQL fake, with real snapshot head/history distinctions."""
    def __init__(self):
        self.tables = {}
        self.calls = []
        self.fixture = [[1, 'shirokuma'], [2, 'polaris']]
        self.fixture_ref = 7141845066324476177
        self.active = []
        self.kill_error = False
        self.history_bad = False
        self.refs_bad = False
        self.restore_history_bad = False
        self.legacy_history_log = False
        self.fail = None
        self.after = None

    def query(self, client, sql):
        self.calls.append((client.user, sql))
        qid = f'20260917_000000_{len(self.calls):05}_local'
        record = {'query_id': qid, 'result': 'running', 'response_complete': False,
                  'server_max_run_seconds': 30, '_not_before': 0}
        client.records.append(record)
        if self.fail:
            self.fail(sql)
        def result(columns=(), types=(), rows=()):
            record.update(result='finished', response_complete=True)
            if self.after:
                self.after(sql)
            return {'query_id': qid, 'columns': list(columns), 'column_types': list(types), 'rows': copy.deepcopy(list(rows))}
        if sql.startswith('SELECT query_id, state, "user" FROM system.runtime.queries'):
            return result(['query_id', 'state', 'user'], ['varchar'] * 3, self.active)
        if sql.startswith('CALL system.runtime.kill_query'):
            self.active = []
            if self.kill_error:
                raise S.SandboxError('simulated completion/cancellation race')
            return result()
        if sql == f'SHOW TABLES FROM {S.NAMESPACE}':
            return result(['Table'], ['varchar'], [[name] for name in sorted(self.tables)])
        if sql == f'SELECT id, name FROM {S.FIXTURE} ORDER BY id':
            return result(['id', 'name'], ['bigint', 'varchar'], self.fixture)
        if 'iceberg.smoke."fixture_v1$refs"' in sql:
            return result(['name', 'type', 'snapshot_id'], ['varchar', 'varchar', 'bigint'], [['main', 'BRANCH', self.fixture_ref]])
        match = re.search(r'sandbox_[0-9a-f]{32}_(?:source|restore)', sql)
        if match is None:
            raise AssertionError('Unexpected SQL: ' + sql)
        name = match.group()
        if sql.startswith('CREATE TABLE '):
            if name in self.tables:
                raise S.SandboxError('collision')
            sid = 100 if name.endswith('source') else 300
            self.tables[name] = {'head': sid, 'snapshots': [[sid, None, 'append']],
                'history': [[sid, None, True]], 'rows': {sid: copy.deepcopy(self.fixture)},
                'marker': S.marker(TOKEN), 'rolled_back': False}
            return result(['rows'], ['bigint'], [[2]])
        table = self.tables[name]
        if sql.startswith('SHOW CREATE TABLE '):
            return result(['Create Table'], ['varchar'], [[f"CREATE TABLE {S.NAMESPACE}.{name} (id bigint, name varchar) COMMENT '{table['marker']}'"]])
        if sql.startswith('DROP TABLE '):
            del self.tables[name]
            return result()
        if sql.startswith('INSERT INTO '):
            table['head'] = 200
            table['snapshots'].append([200, 100, 'append'])
            table['history'].append([200, 100, True])
            table['rows'][200] = table['rows'][100] + [[3, 'sandbox']]
            return result(['rows'], ['bigint'], [[1]])
        if sql.startswith('ALTER TABLE '):
            table['head'] = 100
            table['history'][1][2] = False
            table['rolled_back'] = True
            return result()
        if '$snapshots"' in sql:
            return result(['snapshot_id', 'parent_id', 'operation'], ['bigint', 'bigint', 'varchar'], table['snapshots'])
        if '$history"' in sql:
            rows = copy.deepcopy(table['history'])
            if self.history_bad and table['rolled_back']:
                rows[-1][2] = True
            if self.legacy_history_log and table['rolled_back']:
                rows.append([100, None, True])
            if self.restore_history_bad and name.endswith('restore'):
                rows[0][0] = 100
            return result(['snapshot_id', 'parent_id', 'is_current_ancestor'], ['bigint', 'bigint', 'boolean'], rows)
        if '$refs"' in sql:
            head = 200 if self.refs_bad and table['rolled_back'] else table['head']
            return result(['name', 'type', 'snapshot_id'], ['varchar', 'varchar', 'bigint'], [['main', 'BRANCH', head]])
        if sql.startswith('SELECT id, name FROM '):
            selected = re.search(r'FOR VERSION AS OF ([0-9]+)', sql)
            sid = int(selected.group(1)) if selected else table['head']
            return result(['id', 'name'], ['bigint', 'varchar'], table['rows'][sid])
        raise AssertionError('Unexpected SQL: ' + sql)


class Client:
    def __init__(self, db, control=False):
        self.user = S.run_user(TOKEN, control)
        self.timeout = 45
        self.records = []
        self.progress = None
        self.db = db

    def query(self, sql):
        return self.db.query(self, sql)


class SandboxTests(unittest.TestCase):
    def run_case(self, db):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / TOKEN
            report = S.run_experiment(Client(db), Client(db, True), output, TOKEN)
            saved = json.loads((output / 'summary.json').read_text())
            self.assertEqual(saved, report)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((output / 'summary.json').stat().st_mode), 0o600)
            return report

    def test_time_travel_copy_and_rollback_preserve_independent_history(self):
        db = Database()
        report = self.run_case(db)
        self.assertEqual(report['result'], 'passed')
        self.assertEqual(report['snapshot_a']['head'], 100)
        self.assertEqual(report['snapshot_b']['head'], 200)
        self.assertEqual(report['rolled_back']['head'], 100)
        self.assertEqual(report['rolled_back']['history'], [[100, None, True], [200, 100, False]])
        self.assertEqual(report['restored_copy']['head'], 300)
        self.assertEqual(report['cleanup']['removed'], list(reversed(S.token_names(TOKEN))))
        self.assertTrue(report['fixture_unchanged'])
        self.assertEqual(db.tables, {})
        self.assertEqual(db.fixture, [[1, 'shirokuma'], [2, 'polaris']])
        self.assertIn('NO_ZERO_COPY_CLONE', report['limits'])
        for _, sql in db.calls:
            if sql.startswith(('CREATE ', 'INSERT ', 'ALTER ', 'DROP ')):
                self.assertIn('sandbox_' + TOKEN, sql)
                self.assertFalse(sql.startswith(('INSERT INTO iceberg.smoke', 'ALTER TABLE iceberg.smoke', 'DROP TABLE iceberg.smoke')))

    def test_collision_preserves_even_a_table_with_the_same_marker(self):
        db = Database()
        source = S.token_names(TOKEN)[0]
        db.tables[source] = {'marker': S.marker(TOKEN)}
        report = self.run_case(db)
        self.assertEqual(report['result'], 'failed')
        self.assertEqual(report['attempted_tables'], [])
        self.assertIn(source, db.tables)
        self.assertFalse(any(sql.startswith(('CREATE ', 'DROP ')) for _, sql in db.calls))

    def test_missing_or_changed_owner_marker_never_drops_table(self):
        db = Database()
        def tamper(sql):
            if sql.startswith('CREATE TABLE '):
                db.tables[S.token_names(TOKEN)[0]]['marker'] = 'other-owner'
        db.after = tamper
        report = self.run_case(db)
        self.assertEqual(report['result'], 'failed')
        self.assertFalse(report['cleanup']['verified'])
        self.assertIn(S.token_names(TOKEN)[0], db.tables)
        drops = [sql for _, sql in db.calls if sql.startswith('DROP TABLE')]
        self.assertFalse(any(sql.endswith(S.token_names(TOKEN)[0]) for sql in drops))

    def test_query_drain_must_match_worker_identity_before_drop(self):
        db = Database()
        db.active = [['foreign_query', 'RUNNING', 'another-user']]
        report = self.run_case(db)
        self.assertEqual(report['result'], 'failed')
        self.assertFalse(report['query_drain']['verified'])
        self.assertEqual(report['cleanup']['errors'], ['QUERY_DRAIN_REQUIRED'])
        self.assertFalse(any(sql.startswith('DROP TABLE') for _, sql in db.calls))

    def test_mutation_stops_if_source_owner_changes_before_insert_or_rollback(self):
        for before in ('insert', 'rollback'):
            db = Database()
            def tamper(sql):
                trigger = sql.startswith('CREATE TABLE ') if before == 'insert' else sql.startswith('SELECT id, name') and '_restore ' in sql
                if trigger:
                    db.tables[S.token_names(TOKEN)[0]]['marker'] = 'new-owner'
            db.after = tamper
            with self.subTest(before=before):
                report = self.run_case(db)
                self.assertEqual(report['result'], 'failed')
                prefix = 'INSERT INTO ' if before == 'insert' else 'ALTER TABLE '
                self.assertFalse(any(sql.startswith(prefix) for _, sql in db.calls))
                self.assertIn(S.token_names(TOKEN)[0], db.tables)

    def test_cancel_completion_race_requires_fresh_empty_inspection(self):
        db = Database()
        db.active = [['owned_query', 'RUNNING', S.run_user(TOKEN)]]
        db.kill_error = True
        report = self.run_case(db)
        self.assertEqual(report['result'], 'passed')
        self.assertTrue(report['query_drain']['cancel_errors'])
        self.assertEqual(report['query_drain']['active'], [])
        self.assertEqual(sum(sql.startswith('SELECT query_id') for _, sql in db.calls), 2)

    def test_uncaptured_post_waits_for_server_deadline_before_empty_drain(self):
        db = Database()
        worker, control = Client(db), Client(db, True)
        worker.records = [{'query_id': None, 'response_complete': False, '_not_before': 2.0}]
        now = [0.0]
        with patch.object(S.time, 'monotonic', side_effect=lambda: now[0]), patch.object(S.time, 'sleep', side_effect=lambda n: now.__setitem__(0, now[0] + n)):
            receipt = S.drain_queries(control, worker, TOKEN, timeout=5)
        self.assertTrue(receipt['verified'])
        self.assertGreaterEqual(now[0], 2)
        self.assertGreater(len(db.calls), 1)

    def test_unresolved_post_beyond_drain_budget_defers_cleanup(self):
        db = Database()
        worker, control = Client(db), Client(db, True)
        worker.records = [{'query_id': None, 'response_complete': False, '_not_before': 1000}]
        now = [0.0]
        with patch.object(S.time, 'monotonic', side_effect=lambda: now[0]), patch.object(S.time, 'sleep', side_effect=lambda n: now.__setitem__(0, now[0] + n)):
            receipt = S.drain_queries(control, worker, TOKEN, timeout=1)
        self.assertFalse(receipt['verified'])

    def test_history_or_main_ref_disagreement_cannot_pass(self):
        for field in ('history_bad', 'refs_bad', 'restore_history_bad'):
            db = Database()
            setattr(db, field, True)
            with self.subTest(field=field):
                report = self.run_case(db)
                self.assertEqual(report['result'], 'failed')
                self.assertTrue(report['cleanup']['verified'])

    def test_trino_483_history_is_snapshot_ancestry_not_repeated_head_log(self):
        db = Database()
        db.legacy_history_log = True
        report = self.run_case(db)
        self.assertEqual(report['result'], 'failed')
        self.assertEqual(report['rolled_back']['head'], 100)
        self.assertEqual(len(report['rolled_back']['snapshots']), 2)
        self.assertEqual(len(report['rolled_back']['history']), 3)
        self.assertTrue(report['cleanup']['verified'])

    def test_fixture_rows_or_snapshot_changes_cannot_pass(self):
        for mode in ('rows', 'snapshot'):
            db = Database()
            def change(sql):
                if sql.startswith('DROP TABLE'):
                    if mode == 'rows':
                        db.fixture = [[1, 'unexpected']]
                    else:
                        db.fixture_ref = 99
            db.after = change
            with self.subTest(mode=mode):
                report = self.run_case(db)
                self.assertEqual(report['result'], 'failed')
                self.assertFalse(report['fixture_unchanged'])

    def test_interrupt_cleans_owned_tables_and_keeps_safe_pending_receipt(self):
        db = Database()
        def fail(sql):
            if sql.startswith('INSERT INTO'):
                raise Signal('PRIVATE interrupt')
        db.fail = fail
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / TOKEN
            with self.assertRaises(Signal):
                S.run_experiment(Client(db), Client(db, True), path, TOKEN)
            saved = json.loads((path / 'summary.json').read_text())
            self.assertEqual(saved['result'], 'failed')
            self.assertEqual(saved['error_code'], 'SANDBOX_INTERRUPTED')
            self.assertTrue(saved['cleanup']['verified'])
            self.assertTrue(saved['fixture_unchanged'])
            self.assertEqual(saved['steps'][-1]['result'], 'pending')
            self.assertNotIn('PRIVATE', json.dumps(saved))

    def test_partial_ctas_commit_with_lost_response_is_owned_cleanup_candidate(self):
        db = Database()
        def lose(sql):
            if sql.startswith('CREATE TABLE '):
                raise S.SandboxError('PRIVATE response disappeared after commit')
        db.after = lose
        report = self.run_case(db)
        self.assertEqual(report['result'], 'failed')
        self.assertTrue(report['cleanup']['verified'])
        self.assertEqual(db.tables, {})
        self.assertNotIn('PRIVATE', json.dumps(report))

    def test_strict_independent_row_types_and_token_validation(self):
        for rows in ([[True, 'shirokuma'], [2, 'polaris']], [[1, None], [2, 'polaris']], [[2, 'polaris'], [1, 'shirokuma']]):
            with self.assertRaises(S.SandboxError):
                S.check_rows({'columns': ['id', 'name'], 'column_types': ['bigint', 'varchar'], 'rows': rows}, S.ROWS_A)
        for token in ('../fixture', "x';DROP TABLE fixture", True, 'A' * 32):
            with self.assertRaises(S.SandboxError):
                S.token_names(token)
        for sid in (True, '100 OR 1=1', -1, 2**63):
            with self.assertRaises(S.SandboxError):
                S.snapshot_id(sid)


class Response:
    def __init__(self, page):
        self.data = json.dumps(page).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, limit):
        return self.data[:limit]


class Opener:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.requests = []
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return Response(outcome)


class TransportTests(unittest.TestCase):
    def test_early_query_identity_and_server_deadline_headers(self):
        client = S.SandboxClient('http://127.0.0.1:18080', TOKEN)
        client.opener = Opener([{'id': 'query_1', 'nextUri': 'http://127.0.0.1:18080/v1/statement/executing/query_1/token/1'}, TimeoutError('PRIVATE')])
        with self.assertRaises(S.SandboxError):
            client.query('SELECT 1')
        record = client.records[0]
        self.assertEqual(record['query_id'], 'query_1')
        self.assertFalse(record['response_complete'])
        self.assertEqual(record['result'], 'failed')
        headers = dict(client.opener.requests[0][0].header_items())
        self.assertEqual(headers['X-trino-user'], S.run_user(TOKEN))
        self.assertEqual(headers['X-trino-session'], 'query_max_run_time=30s')
        self.assertTrue(all(timeout <= 10 for _, timeout in client.opener.requests))

    def test_initial_submission_failure_stays_uncaptured_and_unknown(self):
        client = S.SandboxClient('http://127.0.0.1:18080', TOKEN)
        client.opener = Opener([OSError('PRIVATE response failure')])
        with self.assertRaises(S.SandboxError) as caught:
            client.query('CREATE TABLE fixed')
        self.assertIsNone(client.records[0]['query_id'])
        self.assertFalse(client.records[0]['response_complete'])
        self.assertGreater(client.records[0]['_not_before'], S.time.monotonic())
        self.assertNotIn('PRIVATE', str(caught.exception))

    def test_unknown_submission_horizon_starts_after_failed_http_wait(self):
        client = S.SandboxClient('http://127.0.0.1:18080', TOKEN)
        now = [0.0]
        class DelayedFailure:
            def open(self, request, timeout):
                now[0] = 10
                raise TimeoutError('delayed initial response')
        client.opener = DelayedFailure()
        with patch.object(S.time, 'monotonic', side_effect=lambda: now[0]), self.assertRaises(S.SandboxError):
            client.query('CREATE TABLE fixed')
        self.assertEqual(client.records[0]['_not_before'], 42)

    def test_polling_identity_origin_and_response_id_cannot_change(self):
        for uri in ['http://example.com/v1/statement/executing/query_1/x/1',
                    'http://127.0.0.1:18080/v1/statement/executing/other/x/1']:
            client = S.SandboxClient('http://127.0.0.1:18080', TOKEN)
            client.opener = Opener([{'id': 'query_1', 'nextUri': uri}])
            with self.assertRaises(S.SandboxError):
                client.query('SELECT 1')
            self.assertEqual(len(client.opener.requests), 1)
        client = S.SandboxClient('http://127.0.0.1:18080', TOKEN)
        client.opener = Opener([{'id': 'query_1', 'nextUri': 'http://127.0.0.1:18080/v1/statement/executing/query_1/x/1'}, {'id': 'other'}])
        with self.assertRaises(S.SandboxError):
            client.query('SELECT 1')

    def test_bad_endpoint_and_signal_are_not_success(self):
        for url in ['https://127.0.0.1:18080', 'http://external:18080', 'http://127.0.0.1', 'http://u:p@127.0.0.1:18080']:
            with self.assertRaises((S.SandboxError, S.TRINO.SmokeError)):
                S.SandboxClient(url, TOKEN)
        client = S.SandboxClient('http://127.0.0.1:18080', TOKEN)
        client.opener = Opener([Signal('signal')])
        with self.assertRaises(Signal):
            client.query('SELECT 1')
        self.assertEqual(client.records[0]['result'], 'interrupted')

    def test_cli_scoped_sigterm_handler_saves_private_interruption(self):
        original = signal.getsignal(signal.SIGTERM)
        with tempfile.TemporaryDirectory() as tmp:
            def interrupted(*args):
                handler = signal.getsignal(signal.SIGTERM)
                self.assertTrue(callable(handler))
                handler(signal.SIGTERM, None)
            with patch.object(S, 'ROOT', Path(tmp)), patch.object(S.uuid, 'uuid4', return_value=SimpleNamespace(hex=TOKEN)), patch.object(S.sys, 'argv', ['sandbox']), patch.object(S, 'run_experiment', side_effect=interrupted), patch('builtins.print'):
                self.assertEqual(S.main(), 143)
            path = Path(tmp) / 'tmp/local-experiment/iceberg-sandbox' / TOKEN / 'interruption.json'
            self.assertEqual(json.loads(path.read_text())['signal'], signal.SIGTERM)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(signal.getsignal(signal.SIGTERM), original)


if __name__ == '__main__':
    unittest.main()
