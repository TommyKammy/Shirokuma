"""No-runtime regression tests for the bounded public-API runner."""
import contextlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import m1_small_load as load


def runtime():
    return dict(physical_state='active', replicas=2, ready_replicas=2, engine_verified=True,
                policy=dict(minutes=0, revision=4), blockers=dict(runtime_operations=0, admissions=0,
                unknown=0, creation=dict(requests=0, effects=0, roots=0)),
                operation_phase='UNKNOWN')  # A closed historical UNKNOWN is not itself a blocker.


def successful_run(identifier='run'):
    return dict(id=identifier, status='succeeded', steps=[
        dict(name='integrity', status='succeeded', summary=dict(triggered=False)),
        dict(name='read', status='succeeded', summary=dict(row_count=1))])


class FakeClient:
    principal = 'owner'
    role = 'admin'

    def __init__(self, expected):
        self.expected = expected
        self.calls = []
        self.created = False
        self.chilled = False
        self.pending = set()
        self.canceled = set()
        self.counter = 0
        self.lock = threading.Lock()

    def request(self, method, route, **kwargs):
        with self.lock:
            self.calls.append((method, route, kwargs))
            self.counter += 1
            if route == load.RUNTIME:
                value = runtime()
                value['blockers']['admissions'] = len(self.pending)
                if self.chilled:
                    value.update(physical_state='chilled', replicas=0, ready_replicas=0, engine_verified=False)
                return value
            if route == load.RUNTIME + '/chill':
                if self.pending:
                    raise load.Failure('ADMISSION_CONFLICT')
                self.chilled = True
                return dict(operation_id='stop', phase='ACCEPTED')
            if route == load.RUNTIME + '/wake':
                self.chilled = False
                return dict(operation_id='wake', phase='ACCEPTED')
            if route.startswith(load.RUNTIME + '/operations/'):
                return dict(id=route.rsplit('/', 1)[1], phase='SUCCEEDED')
            if route == load.CREATIONS + '/capabilities':
                return dict(available=True, creation_mode='logical_v1', environment_id='local-test')
            if route == load.CREATIONS:
                self.created = True
                self.name = kwargs['body']['sql_catalog']
                return dict(creation_id='creation', request_id='create-request', accepted_revision=1)
            if route.startswith(load.CREATIONS + '/by-name/'):
                if not self.created:
                    raise load.Failure('DATABASE_NOT_FOUND')
                return dict(progress_code='READY', creation_id='creation', database_id='database')
            if route == '/api/v1/databases':
                return dict(items=[dict(id='database', sql_catalog=self.name, binding_revision=1,
                    generation_id='generation', grant_revision=1)] if self.created else [])
            if route.endswith('/schemas'):
                return dict(items=[dict(id='raw', sql_name='raw')])
            if route.endswith('/ingests/preview'):
                return dict(preview=dict(request_fingerprint='a' * 64))
            if route == '/api/v1/ingests':
                return dict(operation=dict(id='ingest-' + str(self.counter), state='FINISHED', committed=True,
                    cleanup_error=None, result=dict(committed_rows=self.expected[0])))
            if route == '/api/v1/queries' and method == 'POST':
                identifier = 'query-' + str(self.counter)
                if self.chilled:
                    self.pending.add(identifier)
                    return dict(id=identifier, state='QUEUED', backend_query_id=None, data=[],
                        next_uri='/api/v1/queries/' + identifier + '/pages/next',
                        cancel_uri='/api/v1/queries/' + identifier + '/cancel')
                return dict(id=identifier, state='FINISHED', backend_query_id='backend', data=[self.expected], next_uri=None)
            if route.endswith('/cancel'):
                identifier = route.split('/')[4]
                self.pending.discard(identifier)
                self.canceled.add(identifier)
                return dict(id=identifier, state='CANCELED', backend_query_id=None)
            if '/pages/' in route:
                identifier = route.split('/')[4]
                self.pending.discard(identifier)
                self.chilled = False
                return dict(id=identifier, state='FINISHED', data=[self.expected], next_uri=None)
            if route.startswith('/api/v1/queries/'):
                identifier = route.rsplit('/', 1)[1]
                return dict(id=identifier, state='CANCELED' if identifier in self.canceled else 'FINISHED', backend_query_id=None)
            if route == '/api/v1/jobs':
                return dict(job=dict(id='job', revision=1))
            if route == '/api/v1/jobs/job/runs':
                identifier = 'run-' + str(self.counter)
                self.pending.add(identifier)
                return dict(run=dict(id=identifier, status='queued'))
            if route.startswith('/api/v1/jobs/runs/'):
                identifier = route.rsplit('/', 1)[1]
                self.pending.discard(identifier)
                return dict(run=successful_run(identifier))
            raise AssertionError('Unexpected fake route: ' + route)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name).resolve() / 'evidence'
        self.args = load.parser().parse_args(['--rows', '17', '--rounds', '2', '--output-dir', str(self.path)])
        self.client = FakeClient([17, 153, sum(i % 97 for i in range(1, 18))])
        self.runner = load.Runner(self.args, self.client, load.Evidence(self.path))

    def test_reject_invalid_cli_values(self):
        for option, value in [('--api-url', 'http://example.com'), ('--api-url', 'https://127.0.0.1'),
                              ('--api-url', 'http://[broken'), ('--rows', '10001'), ('--rows', '0'), ('--rounds', '6')]:
            with self.subTest(option=option, value=value), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                load.parser().parse_args(['--output-dir', '/tmp/unused', option, value])

    def test_post_intent_is_private_and_durable_before_send(self):
        def send(method, route, **kwargs):
            files = list(self.path.glob('request-*.json'))
            self.assertEqual(len(files), 1)
            receipt = json.loads(files[0].read_text())
            self.assertEqual(receipt['status'], 'uncertain')
            self.assertEqual(receipt['body'], {'request_id': 'sql-request'})
            self.assertIsNone(receipt['key'])
            self.assertEqual(receipt['request_id'], 'sql-request')
            self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o700)
            return dict(id='query', state='QUEUED')
        self.client.request = send
        self.runner.post('/api/v1/queries', dict(request_id='sql-request'), key=False)
        receipt = json.loads(next(self.path.glob('request-*.json')).read_text())
        self.assertEqual(receipt['result']['id'], 'query')
        self.assertIn('ack_at', receipt)

    def test_uncertain_post_never_retries_and_sanitizes_failure(self):
        calls = []
        def send(*args, **kwargs):
            calls.append(args)
            raise RuntimeError('secret password must not appear')
        self.client.request = send
        with self.assertRaises(load.Failure):
            self.runner.post('/api/v1/queries', dict(sql='SELECT 1'), key=False)
        self.assertEqual(len(calls), 1)
        raw = next(self.path.glob('request-*.json')).read_text()
        self.assertNotIn('password', raw)
        self.assertEqual(json.loads(raw)['status'], 'uncertain')

    def test_existing_evidence_is_preserved(self):
        (self.path / 'existing.json').write_text('kept')
        with self.assertRaises(load.Failure):
            load.Evidence(self.path)
        self.assertEqual((self.path / 'existing.json').read_text(), 'kept')

    def test_all_blockers_prevent_chill_without_post(self):
        for field in ('runtime_operations', 'admissions', 'unknown', 'requests', 'effects', 'roots'):
            value = runtime()
            target = value['blockers']['creation'] if field in ('requests', 'effects', 'roots') else value['blockers']
            target[field] = 1
            self.runner.runtime = lambda: value
            with self.subTest(field=field), self.assertRaises(load.Failure):
                self.runner.chill()
        self.assertFalse(any(method == 'POST' for method, _, _ in self.client.calls))
        value = runtime()
        value['blockers']['creation'] = {}
        with self.assertRaises(load.Failure):
            load.Runner.unblocked(value)

    def test_sql_collects_every_page_even_finished_with_next_uri(self):
        pages = iter([dict(state='RUNNING', data=[[2]], next_uri='/api/v1/queries/q/pages/2'),
                      dict(state='FINISHED', data=[[3]], next_uri=None)])
        self.runner.get = lambda route: next(pages)
        with patch.object(load.time, 'sleep'):
            rows = self.runner.collect_sql(dict(state='FINISHED', data=[[1]], next_uri='/api/v1/queries/q/pages/1'))
        self.assertEqual(rows, [[1], [2], [3]])

    def test_warm_orchestration_exact_context_and_results(self):
        self.assertEqual(self.runner.execute(), 0)
        summary = json.loads((self.path / 'summary.json').read_text())
        self.assertEqual(summary['phases']['lifecycle'], 'not_selected')
        self.assertEqual(set(summary['expected_tables']), {'seed', 'mix_1', 'mix_2'})
        self.assertEqual(summary['metrics']['job']['samples'], 2)
        self.assertEqual(summary['failure_count'], 0)
        self.assertTrue(all(item['receipts'] for item in summary['workloads']))
        self.assertTrue(any(handle.get('operation') for handle in summary['handles']))
        for method, route, kwargs in self.client.calls:
            if method != 'POST':
                continue
            self.assertEqual(kwargs['environment'], 'local-test')
            body = kwargs['body']
            if route == '/api/v1/queries':
                self.assertEqual(set(body), {'sql', 'request_id', 'worksheet_id', 'context', 'mode', 'max_rows'})
                self.assertEqual(set(body['context']), set(load.SQL_FIELDS))
                self.assertIsNone(kwargs['key'])
            elif 'context' in body:
                self.assertEqual(len(body['context']), 9)
                self.assertTrue(kwargs['key'])
        self.assertFalse(any('/chill' in route or '/wake' in route for _, route, _ in self.client.calls))
        self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in self.path.iterdir()))

    def test_bad_ingest_aggregate_fails_warm_and_stops_following_rounds(self):
        original = self.client.request
        def bad_result(method, route, **kwargs):
            reply = original(method, route, **kwargs)
            if method == 'POST' and route == '/api/v1/queries' and kwargs['body']['sql'].endswith('mix_1'):
                reply['data'] = [[0, 0, 0]]
            return reply
        self.client.request = bad_result
        self.assertEqual(self.runner.execute(), 1)
        self.assertEqual(self.runner.summary['error'], 'AGGREGATE_MISMATCH')
        self.assertEqual(self.runner.summary['phases']['warm'], 'failed')
        self.assertEqual(self.runner.summary['phases']['final'], 'not_run')
        self.assertIn('runtime_after', self.runner.summary)
        self.assertNotIn('mix_2', self.runner.summary['expected_tables'])

    def test_ingest_requires_physical_active_without_prior_engine_proof(self):
        self.runner.context = dict(database_id='database', schema_id='raw')
        value = runtime()
        value['engine_verified'] = False
        self.runner.runtime = lambda: value
        with patch.object(load.time, 'sleep') as sleep:
            self.runner.ingest('seed')
        sleep.assert_not_called()

    def test_commit_cleanup_error_fails_setup(self):
        original = self.client.request
        def failed_commit(method, route, **kwargs):
            reply = original(method, route, **kwargs)
            if method == 'POST' and route == '/api/v1/ingests':
                reply['operation']['cleanup_error'] = 'CLEANUP_FAILED'
            return reply
        self.client.request = failed_commit
        self.assertEqual(self.runner.execute(), 1)
        self.assertEqual(self.runner.summary['error'], 'INGEST_NOT_COMMITTED')
        self.assertEqual(self.runner.summary['phases']['warm'], 'not_run')
        self.assertTrue(any(handle.get('operation') for handle in self.runner.summary['handles']))

    def test_lost_commit_keeps_evidence_without_retry_or_restore(self):
        self.args.lifecycle = True
        self.runner.summary['phases']['lifecycle'] = 'selected'
        self.client.chilled = True
        original = self.client.request
        def lost_commit(method, route, **kwargs):
            if method == 'POST' and route == '/api/v1/ingests':
                self.client.calls.append((method, route, kwargs))
                raise load.Failure('RESPONSE_UNCERTAIN')
            return original(method, route, **kwargs)
        self.client.request = lost_commit
        self.assertEqual(self.runner.execute(), 1)
        self.assertEqual(sum(route == '/api/v1/ingests' for _, route, _ in self.client.calls), 1)
        self.assertEqual(self.runner.summary['error'], 'RESPONSE_UNCERTAIN')
        self.assertEqual(self.runner.summary['expected_tables']['seed'], self.client.expected)
        self.assertFalse(any('/chill' in route for _, route, _ in self.client.calls))
        receipts = [json.loads(path.read_text()) for path in self.path.glob('request-*.json')]
        uncertain = [value for value in receipts if value.get('status') == 'uncertain']
        self.assertEqual(len(uncertain), 1)
        self.assertTrue(uncertain[0]['key'])

    def test_job_integrity_is_required(self):
        run = successful_run()
        run['steps'][0]['summary']['triggered'] = True
        with self.assertRaises(load.Failure):
            self.runner.collect_job(run)

    def test_selected_lifecycle_cancel_and_conflict_have_no_fallback_stop(self):
        self.args.lifecycle = True
        self.runner.summary['phases']['lifecycle'] = 'selected'
        with patch.object(load.time, 'sleep'):
            self.assertEqual(self.runner.execute(), 0)
        chill_calls = [call for call in self.client.calls if call[:2] == ('POST', load.RUNTIME + '/chill')]
        self.assertEqual(len(chill_calls), 2)  # initial successful stop, then rejected competing stop
        self.assertEqual(len(self.client.canceled), 1)
        self.assertFalse(self.client.pending)
        self.assertTrue(self.runner.summary['runtime_after']['engine_verified'])
        receipts = [json.loads(path.read_text()) for path in self.path.glob('request-*.json')]
        self.assertEqual(sum(item.get('status') == 'rejected' for item in receipts), 1)

    def test_lifecycle_accepts_async_admission_conflict_without_fallback_stop(self):
        self.args.lifecycle = True
        self.runner.summary['phases']['lifecycle'] = 'selected'
        original = self.client.request
        def async_conflict(method, route, **kwargs):
            if method == 'POST' and route == load.RUNTIME + '/chill' and self.client.pending:
                self.client.calls.append((method, route, kwargs))
                return dict(operation_id='conflict', phase='ACCEPTED')
            if route == load.RUNTIME + '/operations/conflict':
                self.client.calls.append((method, route, kwargs))
                return dict(id='conflict', phase='FAILED', error_code='ADMISSION_CONFLICT')
            return original(method, route, **kwargs)
        self.client.request = async_conflict
        with patch.object(load.time, 'sleep'):
            self.assertEqual(self.runner.execute(), 0)
        self.assertEqual(sum(route == load.RUNTIME + '/chill' for _, route, _ in self.client.calls), 2)

    def test_initial_chilled_requires_lifecycle_and_failure_does_not_restore(self):
        self.client.chilled = True
        self.assertEqual(self.runner.execute(), 1)
        self.assertEqual(self.runner.summary['error'], 'INITIAL_WAKE_REQUIRES_LIFECYCLE')
        self.assertFalse(any(method == 'POST' for method, _, _ in self.client.calls))

    def test_initial_chilled_restored_only_after_success(self):
        self.client.chilled = True
        self.args.lifecycle = True
        self.runner.summary['phases']['lifecycle'] = 'selected'
        with patch.object(load.time, 'sleep'):
            self.assertEqual(self.runner.execute(), 0)
        self.assertTrue(self.client.chilled)
        self.assertEqual(self.runner.summary['runtime_after']['replicas'], 0)

    def test_final_rereads_every_table_before_restoring(self):
        self.runner.setup()
        self.runner.warm()
        self.runner.initially_chilled = True
        events = []
        physical_restore = self.runner.chill
        def read(table):
            events.append(table)
            return [self.runner.expected]
        def restore():
            events.append('restore')
            physical_restore()
        with patch.object(self.runner, 'sql', side_effect=read), \
                patch.object(self.runner, 'chill', side_effect=restore) as chill:
            self.runner.final()
        self.assertEqual(events, ['seed', 'mix_1', 'mix_2', 'restore'])
        chill.assert_called_once()

    def test_later_table_corruption_fails_final_without_restore(self):
        self.runner.setup()
        self.runner.warm()
        self.runner.initially_chilled = True
        with patch.object(self.runner, 'sql', side_effect=[
                [self.runner.expected], [self.runner.expected], [[0, 0, 0]]]) as read, \
                patch.object(self.runner, 'chill') as chill:
            with self.assertRaisesRegex(load.Failure, 'AGGREGATE_MISMATCH'):
                self.runner.final()
        self.assertEqual([call.args[0] for call in read.call_args_list], ['seed', 'mix_1', 'mix_2'])
        chill.assert_not_called()
        self.assertFalse(self.client.chilled)

    def test_profile_failure_still_writes_summary_without_secrets(self):
        path = Path(self.temporary.name).resolve() / 'profile-failure'
        with patch.object(load, 'profile_references', side_effect=RuntimeError('private auth')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(load.main(['--output-dir', str(path)]), 1)
        raw = (path / 'summary.json').read_text()
        self.assertNotIn('private auth', raw)
        self.assertFalse(json.loads(raw)['ok'])


if __name__ == '__main__':
    unittest.main()
