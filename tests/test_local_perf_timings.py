"""Client timing boundaries and backend reports, without lab mutations."""
import copy
from contextlib import nullcontext
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from benchmarks.local_perf import runner
from benchmarks.local_perf.capacity import Campaign, CapacitySampler
from benchmarks.local_perf.public import PublicAdapter
from tests.local_perf_host import setUpModule, tearDownModule  # noqa: F401


class Clock:
    def __init__(self):
        self.now = 100.

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class CapacitySamplerTests(unittest.TestCase):
    def test_initial_interval_completion_and_force(self):
        clock = Clock()
        sampler = CapacitySampler(clock=clock)
        def scan():
            clock.advance(1)
            return 'proof'
        self.assertEqual(sampler.sample(scan), 'proof')
        clock.advance(.125)
        self.assertIsNone(sampler.sample(scan))  # scan start + interval has passed
        clock.advance(.125)
        self.assertEqual(sampler.sample(scan), 'proof')
        self.assertEqual(sampler.sample(scan, force=True), 'proof')
        stats = sampler.snapshot()
        self.assertEqual(stats['global_requests'], 4)
        self.assertEqual(stats['global_scans'], 3)
        self.assertEqual(stats['global_scan_s'], 3)
        self.assertEqual(stats['global_failed_scans'], 0)
        self.assertEqual(stats['interval_s'], .2)
        stats['global_scans'] = 999
        self.assertEqual(sampler.snapshot()['global_scans'], 3)

    def test_failed_scan_propagates_and_next_request_retries_immediately(self):
        for prior_success in (False, True):
            with self.subTest(prior_success=prior_success):
                clock = Clock()
                sampler = CapacitySampler(clock=clock)
                callback = MagicMock(return_value='proof')
                if prior_success:
                    sampler.sample(callback)
                error = TimeoutError('scan failed')
                def fail():
                    clock.advance(.05)
                    raise error
                with self.assertRaises(TimeoutError) as raised:
                    sampler.sample(fail, force=prior_success)
                self.assertIs(raised.exception, error)
                callback.reset_mock()
                self.assertEqual(sampler.sample(callback), 'proof')
                callback.assert_called_once()
                self.assertIsNone(sampler.sample(callback))
                callback.assert_called_once()
                clock.advance(.25)
                self.assertEqual(sampler.sample(callback), 'proof')
                self.assertEqual(callback.call_count, 2)
                stats = sampler.snapshot()
                self.assertEqual(stats['global_requests'], 4 + int(prior_success))
                self.assertEqual(stats['global_scans'], 3 + int(prior_success))
                self.assertEqual(stats['global_failed_scans'], 1)
                self.assertAlmostEqual(stats['global_scan_s'], .05)

    def test_concurrent_requests_coalesce_without_racing_scans(self):
        sampler = CapacitySampler(clock=Clock())
        entered, release, requesting = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def scan():
            entered.set()
            if not release.wait(2):
                raise RuntimeError('test release timeout')
        callback = MagicMock(side_effect=scan)
        def request(waiter=False):
            if waiter:
                requesting.set()
            try:
                sampler.sample(callback)
            except BaseException as exc:
                errors.append(exc)
        first = threading.Thread(target=request)
        second = threading.Thread(target=request, args=(True,))
        first.start()
        try:
            self.assertTrue(entered.wait(2))
            second.start()
            self.assertTrue(requesting.wait(2))
        finally:
            release.set()
            first.join(2)
            if second.ident is not None:
                second.join(2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        callback.assert_called_once()
        self.assertEqual(sampler.snapshot()['global_requests'], 2)

    def test_waiting_request_retries_after_concurrent_scan_failure(self):
        sampler = CapacitySampler(clock=Clock())
        entered, release, requesting = threading.Event(), threading.Event(), threading.Event()
        error = RuntimeError('capacity proof failed')
        errors = []
        def fail():
            entered.set()
            if not release.wait(2):
                raise RuntimeError('test release timeout')
            raise error
        retry = MagicMock(return_value='proof')
        def request(callback, waiter=False):
            if waiter:
                requesting.set()
            try:
                sampler.sample(callback)
            except BaseException as exc:
                errors.append(exc)
        first = threading.Thread(target=request, args=(fail,))
        second = threading.Thread(target=request, args=(retry, True))
        first.start()
        try:
            self.assertTrue(entered.wait(2))
            second.start()
            self.assertTrue(requesting.wait(2))
        finally:
            release.set()
            first.join(2)
            if second.ident is not None:
                second.join(2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [error])
        retry.assert_called_once()
        stats = sampler.snapshot()
        self.assertEqual(stats['global_requests'], 2)
        self.assertEqual(stats['global_scans'], 2)
        self.assertEqual(stats['global_failed_scans'], 1)


class TimingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.clock = Clock()
        self.patcher = patch.object(runner.time, 'monotonic', self.clock)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def transport(self, pages, failure=None):
        clock = self.clock
        responses = iter(pages)

        class Response:
            status = 200
            def __init__(self, page):
                self.raw = json.dumps(page).encode()
                self.length = len(self.raw)
            def isclosed(self):
                return False
            def read1(self, size):
                clock.advance(4)
                self.length = 0
                return self.raw

        class Connection:
            def __init__(self, *args, **kwargs):
                self.sock = MagicMock()
            def connect(self):
                clock.advance(1)
                if failure == 'connect':
                    raise OSError('connect failed')
            def request(self, *args):
                clock.advance(2)
            def getresponse(self):
                clock.advance(3)
                if failure == 'header':
                    raise OSError('lost POST')
                return Response(next(responses))
            def close(self):
                clock.advance(5)

        return patch.object(runner.http.client, 'HTTPConnection', Connection)

    def test_transport_initial_followup_and_recovery_excluded(self):
        a = runner.TrinoAdapter('http://localhost:1234')
        a.timings = runner.Timings()
        with self.transport([{}, {}, {}]), patch.object(runner.threading, 'Timer'):
            for kind, method in [('initial', 'POST'), ('follow_up', 'GET'), (None, 'DELETE')]:
                a.request_kind = kind
                a.request(method, a.url, 1000)
        for kind in ('initial', 'follow_up'):
            self.assertEqual(a.timings.http[kind], dict(calls=1, connect=1, send=2,
                header_wait=3, body_read=4, decode=0, total=15))

    def test_execute_attributes_initial_and_get_with_terminal_rows(self):
        a = runner.TrinoAdapter('http://localhost:1234')
        a.timings = runner.Timings()
        pages = [dict(id='q', stats={'state': 'RUNNING'}, nextUri=a.url + '/page'),
                 dict(id='q', stats={'state': 'FINISHED'},
                      columns=[dict(name='x', type='integer')], data=[[1], [2]])]
        with self.transport(pages), patch.object(runner.threading, 'Timer'):
            a.execute('SELECT 1', self.root / 'http-result', {}, 1000, lambda h: None, lambda: None)
        t = a.timings
        self.assertEqual(t.http['initial']['calls'], 1)
        self.assertEqual(t.http['follow_up']['calls'], 1)
        self.assertEqual(t.events['initial_response_complete'], 15)
        self.assertEqual(t.events['first_row'], 30)
        self.assertEqual(t.events['last_row'], 30)
        self.assertEqual(t.events['terminal_observed'], 30)
        with self.transport([{}, {'state': 'FINISHED'}]), patch.object(runner.threading, 'Timer'):
            self.assertEqual(a.cancel_reconcile({'id': 'q'}, timeout=100), 'FINISHED')
        self.assertEqual(t.http['follow_up']['calls'], 1)

    def test_transport_partial_failure_phases_and_total(self):
        for failure in ('connect', 'header'):
            a = runner.TrinoAdapter('http://localhost:1234')
            a.timings = runner.Timings()
            a.request_kind = 'initial'
            with self.transport([], failure), patch.object(runner.threading, 'Timer'):
                with self.assertRaises(OSError):
                    a.request('POST', a.url, 1000, b'SELECT 1')
            h = a.timings.http['initial']
            self.assertEqual(h['calls'], 1)
            self.assertEqual(h['connect'], 1)
            self.assertIsNone(h['decode'])
            self.assertIsNone(h['body_read'])
            self.assertEqual(h['total'], 6 if failure == 'connect' else 11)
            self.assertEqual(h['header_wait'], None if failure == 'connect' else 3)

    def test_latest_backend_snapshot_null_zero_invalid(self):
        t = runner.Timings()
        t.page({'stats': {'queuedTimeMillis': 0, 'cpuTimeMillis': 12,
                         'private': 'never retain'}}, 'RUNNING')
        self.assertEqual(t.backend['values']['queuedTimeMillis'], 0)
        self.assertEqual(t.backend['values']['cpuTimeMillis'], 12)
        for bad in (True, -1, float('nan'), float('inf'), '1', 10 ** 1000, Decimal('sNaN')):
            t.page({'stats': {'cpuTimeMillis': bad}}, 'FINISHED')
            self.assertEqual(t.backend['values'], dict.fromkeys(t.STATS))
        self.assertTrue(t.backend['client_observed'])
        self.assertEqual(t.backend['page_state'], 'FINISHED')
        self.assertNotIn('private', json.dumps(t.snapshot()))

    def run_attempt(self, adapter, name='run'):
        campaign = Campaign.create(self.root / name)
        manifest = dict(format=1, dataset_id='d', queries=[dict(id='q', sql='SELECT 1')])
        return campaign, manifest

    def test_capacity_forced_boundaries_local_every_callback_and_remaining_peak(self):
        a = runner.TrinoAdapter('http://localhost:1234')
        c, manifest = self.run_attempt(a)
        phase = ['initial']
        scans = []
        def global_check(additional):
            scans.append((phase[0], additional))
        def execute(sql, out, metadata, deadline, save, check):
            # Both initial and immediately-before-execute proofs precede dispatch.
            self.assertEqual([p for p, _ in scans], ['initial'] * 3)  # includes run preflight
            self.assertEqual(json.loads((out.parent / 'attempt.json').read_text())['status'], 'INTENT')
            phase[0] = 'execute'
            for _ in range(20):
                check()
            writer = runner.Writer(out, [dict(name='x', type='integer')], metadata)
            writer.row([1])
            writer.finish()
            phase[0] = 'delivered'
        def validate(path, order, check):
            self.assertEqual(scans[-1][0], 'delivered')
            phase[0] = 'validation'
            for _ in range(10):
                check()
            phase[0] = 'validated'
        local = MagicMock(side_effect=lambda path: self.clock.advance(.001) or
                         (100 if phase[0] == 'initial' else 150))
        with patch.object(c, 'check', side_effect=global_check), patch.object(a, 'execute', execute), \
                patch.object(runner, 'validate_order', validate), patch.object(runner, 'allocated', local):
            root = runner.run(c, 'result', manifest, a, reference_label='test')
        guard = json.loads((root / 'q-0/attempt.json').read_text())['capacity_guard']
        self.assertEqual([p for p, _ in scans], ['initial'] * 3 + ['delivered', 'validated'])
        self.assertEqual(guard['check_calls'], 34)
        self.assertEqual(local.call_count, guard['check_calls'] + 1)  # initial root baseline
        self.assertEqual(guard['global_requests'], 34)
        self.assertEqual(guard['global_scans'], 4)
        self.assertAlmostEqual(guard['local_peak_check_s'], .034)
        self.assertEqual([n for _, n in scans], [scans[0][1]] * 3 + [scans[0][1] - 50] * 2)

    def test_capacity_each_forced_boundary_failure_stays_in_recovery(self):
        for fail_at in (4, 5):  # delivery, final validation proof
            with self.subTest(fail_at=fail_at):
                a = runner.TrinoAdapter('http://localhost:1234')
                c, manifest = self.run_attempt(a, f'boundary-{fail_at}')
                scans = []
                error = RuntimeError('boundary capacity')
                def scan(additional):
                    scans.append(additional)
                    if len(scans) == fail_at:
                        raise error
                def execute(sql, out, metadata, deadline, save, check):
                    writer = runner.Writer(out, [dict(name='x', type='integer')], metadata)
                    writer.row([1])
                    writer.finish()
                    save({'id': 'q'})
                with patch.object(c, 'check', side_effect=scan), patch.object(a, 'execute', side_effect=execute) as dispatch, \
                        patch.object(a, 'cancel_reconcile', return_value='FINISHED') as recover:
                    with self.assertRaises(RuntimeError) as raised:
                        runner.run(c, 'result', manifest, a, reference_label='test')
                self.assertIs(raised.exception, error)
                dispatch.assert_called_once()
                recover.assert_called_once_with({'id': 'q'})
                intent = json.loads((c.root / 'result/q-0/attempt.json').read_text())
                self.assertEqual(intent['status'], 'FAILED')
                self.assertEqual(intent['capacity_guard']['global_scans'], fail_at - 1)
                self.assertEqual(intent['capacity_guard']['global_failed_scans'], 1)
                self.assertIsNone(intent['timings']['events']['validation_complete'])

    def test_capacity_local_peak_and_deadline_are_not_sampled(self):
        for failure in ('peak', 'deadline'):
            with self.subTest(failure=failure):
                a = runner.TrinoAdapter('http://localhost:1234')
                c, manifest = self.run_attempt(a, failure)
                growth = [0]
                def execute(sql, out, metadata, deadline, save, check):
                    for _ in range(10):
                        check()
                    if failure == 'peak':
                        growth[0] = 10 ** 15
                    else:
                        self.clock.now = deadline
                    check()
                    self.fail('callback must fail immediately')
                with patch.object(c, 'check') as scans, patch.object(a, 'execute', execute), \
                        patch.object(a, 'cancel_reconcile', return_value='UNKNOWN'), \
                        patch.object(runner, 'allocated', side_effect=lambda p: growth[0]):
                    with self.assertRaisesRegex(RuntimeError if failure == 'peak' else TimeoutError,
                                                'declared run peak' if failure == 'peak' else 'deadline'):
                        runner.run(c, 'result', manifest, a, reference_label='test')
                intent = json.loads((c.root / 'result/q-0/attempt.json').read_text())
                self.assertEqual(intent['status'], 'UNKNOWN')
                self.assertEqual(intent['capacity_guard']['check_calls'], 13)
                self.assertEqual(intent['capacity_guard']['global_requests'], 12)
                self.assertEqual(scans.call_count, 3)

    def test_capacity_preflight_scan_failure_and_post_scan_deadline_are_unsubmitted(self):
        for fail_at in (2, 3):  # initial and pre-dispatch; call 1 is run preflight
            for failure in ('scan', 'deadline'):
                with self.subTest(fail_at=fail_at, failure=failure):
                    a = runner.TrinoAdapter('http://localhost:1234')
                    c, manifest = self.run_attempt(a, f'{failure}-{fail_at}')
                    calls = []
                    error = RuntimeError('scan failure')
                    def scan(additional):
                        calls.append(additional)
                        if len(calls) != fail_at:
                            return
                        self.assertFalse((c.root / 'result/q-0/attempt.json').exists())
                        self.clock.advance(1000 if failure == 'deadline' else 2)
                        if failure == 'scan':
                            raise error
                    with patch.object(c, 'check', side_effect=scan), patch.object(a, 'execute') as execute, \
                            patch.object(a, 'cancel_reconcile') as cancel, patch.object(PublicAdapter, 'recover') as recover:
                        with self.assertRaisesRegex(RuntimeError if failure == 'scan' else TimeoutError,
                                                    'scan failure' if failure == 'scan' else 'deadline') as raised:
                            runner.run(c, 'result', manifest, a, reference_label='test')
                    if failure == 'scan':
                        self.assertIs(raised.exception, error)
                    execute.assert_not_called()
                    cancel.assert_not_called()
                    recover.assert_not_called()
                    self.assertEqual(len(calls), fail_at)
                    self.assertFalse((c.root / 'result/q-0/attempt.json').exists())
                    report = json.loads((c.root / 'result/run.json').read_text())
                    self.assertEqual(report['status'], 'FAILED')
                    self.assertEqual(report['attempts'], [])
                    self.assertEqual(report['planned_attempts'], 1)
                    self.assertEqual(report['not_run_attempts'], 1)
                    self.assertEqual(report['pending_attempts'], 0)
                    self.assertEqual(c.state['owned']['result']['status'], 'FAILED')

    def test_capacity_post_scan_deadline_after_dispatch_retains_instrumentation(self):
        for phase in ('execute', 'delivered', 'validated'):
            with self.subTest(phase=phase):
                a = runner.TrinoAdapter('http://localhost:1234')
                c, manifest = self.run_attempt(a, phase)
                calls = []
                current = ['preflight']
                def scan(additional):
                    calls.append(current[0])
                    if current[0] == phase:
                        self.clock.advance(1000)
                def execute(sql, out, metadata, deadline, save, check):
                    save({'id': 'q'})
                    current[0] = 'execute'
                    if phase == 'execute':
                        self.clock.advance(.25)  # ordinary callback must perform a fresh scan
                        check()
                    writer = runner.Writer(out, [dict(name='x', type='integer')], metadata)
                    writer.row([1])
                    writer.finish()
                    current[0] = 'delivered'
                def validate(path, order, check):
                    current[0] = 'validated'
                with patch.object(c, 'check', side_effect=scan), patch.object(a, 'execute', side_effect=execute) as dispatch, \
                        patch.object(a, 'cancel_reconcile', return_value='FINISHED') as cancel, \
                        patch.object(runner, 'validate_order', validate):
                    with self.assertRaisesRegex(TimeoutError, 'deadline'):
                        runner.run(c, 'result', manifest, a, reference_label='test')
                dispatch.assert_called_once()
                cancel.assert_called_once_with({'id': 'q'})
                intent = json.loads((c.root / 'result/q-0/attempt.json').read_text())
                self.assertEqual(intent['status'], 'FAILED')
                self.assertEqual(intent['error_type'], 'TimeoutError')
                guard = intent['capacity_guard']
                self.assertEqual(guard['global_scans'], len(calls) - 1)
                self.assertEqual(guard['global_requests'], len(calls) - 1)
                self.assertEqual(guard['check_calls'], len(calls) - 1)
                self.assertEqual(guard['global_failed_scans'], 0)
                self.assertEqual(guard['global_scan_s'], 1000)
                events = intent['timings']['events']
                self.assertIsNotNone(events['recovery_complete'])
                self.assertIsNotNone(events['attempt_complete'])
                self.assertIsNone(events['validation_complete'])
                self.assertEqual(events['result_complete'] is None, phase == 'execute')
                report = json.loads((c.root / 'result/run.json').read_text())
                self.assertEqual(report['status'], 'FAILED')
                self.assertEqual(report['not_run_attempts'], 0)
                self.assertEqual(report['attempts'][0]['status'], 'FAILED')

    def test_page_boundaries_serialization_and_validation_independent(self):
        a = runner.TrinoAdapter('http://localhost:1234')
        pages = [dict(id='q', stats={'state': 'RUNNING'}, data=[[1], [2]],
                      columns=[dict(name='x', type='integer')], nextUri=a.url + '/page'),
                 dict(id='q', stats={'state': 'FINISHED'}, data=[])]
        def request(*args):
            self.clock.advance(2)
            return pages.pop(0)
        original = runner.Writer.row
        def slow_row(writer, row):
            self.clock.advance(10)
            return original(writer, row)
        def validation(*args):
            self.clock.advance(7)
        c, manifest = self.run_attempt(a)
        with patch.object(a, 'request', request), patch.object(runner.Writer, 'row', slow_row), \
                patch.object(runner, 'validate_order', validation):
            root = runner.run(c, 'result', manifest, a, reference_label='timing-test')
        intent = json.loads((root / 'q-0/attempt.json').read_text())
        t = intent['timings']
        self.assertEqual(t['version'], 1)
        e = t['events']
        self.assertEqual(e['first_row'], 2)
        self.assertEqual(e['last_row'], 2)  # two row writes cost 20, arrival costs neither
        self.assertEqual(e['terminal_observed'], 24)
        self.assertEqual(e['result_complete'], 24)
        self.assertEqual(e['validation_complete'] - e['validation_started'], 7)
        self.assertEqual(t['durations']['artifact_processing_s'], 20)
        self.assertEqual(intent['fetch_s'], 31)
        self.assertEqual(intent['validation_s'], 0)
        self.assertEqual(intent['elapsed_s'], 31)

    def test_full_comparison_is_inside_new_validation_events(self):
        a = runner.TrinoAdapter('http://localhost:1234')
        c, manifest = self.run_attempt(a)
        ref = self.root / 'reference'
        (ref / 'q-0').mkdir(parents=True)
        writer = runner.Writer(ref / 'q-0/result.jsonl', [dict(name='x', type='integer')],
                               runner.result_metadata(manifest, manifest['queries'][0]))
        writer.row([1])
        writer.finish()
        page = dict(id='q', stats={'state': 'FINISHED'}, data=[[1]],
                    columns=[dict(name='x', type='integer')])
        original_compare = runner.compare
        def compare(*args, **kwargs):
            self.clock.advance(11)
            return original_compare(*args, **kwargs)
        def order(*args):
            self.clock.advance(7)
        with patch.object(a, 'request', return_value=page), patch.object(runner, 'compare', compare), \
                patch.object(runner, 'validate_order', order):
            root = runner.run(c, 'result', manifest, a, references=ref)
        intent = json.loads((root / 'q-0/attempt.json').read_text())
        e = intent['timings']['events']
        self.assertEqual(e['validation_complete'] - e['validation_started'], 18)
        self.assertEqual(intent['fetch_s'], 7)
        self.assertEqual(intent['validation_s'], 11)
        self.assertEqual(e['result_complete'], 0)

    def test_lost_post_single_submission_partial_timings_and_recovery(self):
        a = runner.TrinoAdapter('http://localhost:1234')
        c, manifest = self.run_attempt(a)
        def recover(handle):
            self.assertIsNone(a.request_kind)
            self.clock.advance(9)
            return 'UNKNOWN'
        with self.transport([], 'header'), patch.object(runner.threading, 'Timer'), \
                patch.object(a, 'cancel_reconcile', recover):
            with self.assertRaises(OSError):
                runner.run(c, 'result', manifest, a, reference_label='timing-test')
        t = json.loads((c.root / 'result/q-0/attempt.json').read_text())['timings']
        self.assertEqual(t['http']['initial']['calls'], 1)
        self.assertEqual(t['http']['follow_up']['calls'], 0)
        self.assertIsNone(t['events']['initial_response_complete'])
        self.assertIsNone(t['events']['result_complete'])
        self.assertEqual(t['events']['recovery_complete'] - t['events']['recovery_started'], 9)
        self.assertEqual(t['events']['attempt_complete'], 20)

    def test_public_pending_then_rows_and_empty_result(self):
        context = dict(database_id='db', schema_id='s', warehouse_id='LOCAL', sql_catalog='c',
                       sql_schema='s', selected_role='data_engineer', binding_revision=1,
                       generation_id='g', grant_revision=None)
        for empty in (False, True):
            a = PublicAdapter('http://localhost:1234', 'u', 'p', 'data_engineer', context)
            a.prepare_attempt({'request_id': 'request', 'sql': 'SELECT 1', 'adapter_identity': a.identity()})
            t = a.timings = runner.Timings()
            calls = []
            def request(method, *args):
                calls.append(method)
                self.clock.advance(3)
                pending = method == 'POST'
                return dict(id='q', backend_query_id=None if pending else 'b',
                    **copy.deepcopy(a.binding), state='QUEUED' if pending else 'FINISHED',
                    stats={'state': 'QUEUED' if pending else 'FINISHED', 'elapsedTimeMillis': 0},
                    columns=[] if pending else [dict(name='x', type='integer')],
                    data=[] if pending or empty else [[1]],
                    **({'next_uri': '/api/v1/queries/q/pages/t'} if pending else {'result_page_id': 1}))
            with patch.object(a, 'request', request):
                a.execute('SELECT 1', self.root / f'public-{empty}', {}, 1000, lambda h: None, lambda: None)
            self.assertEqual(calls, ['POST', 'GET'])
            self.assertEqual(t.events['initial_response_complete'], 3)
            self.assertEqual(t.events['terminal_observed'], 6)
            self.assertEqual(t.events['first_row'], None if empty else 6)
            self.assertEqual(t.events['last_row'], None if empty else 6)
            self.assertEqual(t.backend['values']['elapsedTimeMillis'], 0)
            self.assertEqual(t.durations['client_poll_wait_s'], 0)

    def public_adapter(self):
        context = dict(database_id='db', schema_id='s', warehouse_id='LOCAL', sql_catalog='c',
                       sql_schema='s', selected_role='data_engineer', binding_revision=1,
                       generation_id='g', grant_revision=None)
        a = PublicAdapter('http://localhost:1234', 'u', 'p', 'data_engineer', context)
        a.prepare_attempt({'request_id': 'request', 'sql': 'SELECT 1', 'adapter_identity': a.identity()})
        return a

    def delivery_pages(self, adapter):
        first = dict(columns=[dict(name='x', type='integer')], data=[[1]],
                     stats={'state': 'RUNNING', 'cpuTimeMillis': 12}, id='q')
        if isinstance(adapter, PublicAdapter):
            first.update(**copy.deepcopy(adapter.binding), backend_query_id='b', state='RUNNING',
                         result_page_id=1, next_uri='/api/v1/queries/q/pages/t')
            last = dict(copy.deepcopy(first), state='FINISHED', result_page_id=2, next_uri=None)
        else:
            first['nextUri'] = adapter.url + '/page'
            last = copy.deepcopy(first)
            last.pop('nextUri')
        last['stats'] = {'state': 'FINISHED', 'cpuTimeMillis': 999}
        return first, last

    def test_rejected_pages_preserve_accepted_row_and_backend_evidence(self):
        for public in (False, True):
            cases = ('query', 'schema', 'data', 'duplicate') + (('context',) if public else ())
            for case in cases:
                with self.subTest(public=public, case=case):
                    a = self.public_adapter() if public else runner.TrinoAdapter('http://localhost:1234')
                    first, last = self.delivery_pages(a)
                    if case == 'query':
                        last['id'] = 'foreign'
                    elif case == 'context':
                        last['context']['database_id'] = 'foreign'
                    elif case == 'schema':
                        last['columns'][0]['type'] = 'varchar'
                    elif case == 'data':
                        last['data'] = 'malformed'
                    else:
                        first['result_page_id'] = last['result_page_id'] = 1
                    pages = iter((first, last))
                    def request(*args):
                        self.clock.advance(3)
                        return next(pages)
                    def check():
                        # Accepted row observation precedes capacity callbacks.
                        self.assertEqual(a.timings.events['first_row'], 3)
                        self.clock.advance(5)
                    start = self.clock.now
                    with patch.object(a, 'request', request), self.assertRaises(ValueError):
                        a.execute('SELECT 1', self.root / f'bad-{public}-{case}', {}, 1000,
                                  lambda h: None, check)
                    self.assertEqual(a.timings.start, start)
                    self.assertEqual(a.timings.events['first_row'], 3)
                    self.assertEqual(a.timings.events['last_row'], 3)
                    self.assertIsNone(a.timings.events['terminal_observed'])
                    self.assertEqual(a.timings.backend['page_state'], 'RUNNING')
                    self.assertEqual(a.timings.backend['values']['cpuTimeMillis'], 12)
                    self.assertIsNone(a.request_kind)

    def test_terminal_failures_observe_only_valid_identity_stats(self):
        for public in (False, True):
            states = ('FAILED', 'CANCELED') if public else ('FAILED', 'CANCELED', 'CANCELLED')
            for state in states:
                for foreign in (None, 'query', 'context') if public else (None, 'query'):
                    for truncated in (False, True):
                        with self.subTest(public=public, state=state, foreign=foreign, truncated=truncated):
                            a = self.public_adapter() if public else runner.TrinoAdapter('http://localhost:1234')
                            first, last = self.delivery_pages(a)
                            first['data'] = []
                            last['stats'] = dict(state=state, queuedTimeMillis=0, cpuTimeMillis=99,
                                                 wallTimeMillis=-1, elapsedTimeMillis=True, private='discard')
                            last.update(data=[[999]], columns=[dict(name='bad', type='invalid')],
                                        error={'message': 'failed'}, truncated=truncated)
                            if public:
                                last['state'] = state
                            if foreign == 'query':
                                last['id'] = 'foreign'
                            elif foreign == 'context':
                                last['context']['database_id'] = 'foreign'
                            pages = iter((first, last))
                            accepted = {}
                            def request(*args):
                                if accepted:
                                    self.assertEqual(a.timings.events, accepted['events'])
                                self.clock.advance(3)
                                return next(pages)
                            def check():
                                accepted.update(events=copy.deepcopy(a.timings.events),
                                                backend=copy.deepcopy(a.timings.backend))
                            message = ('PUBLIC_CONTEXT' if public else 'query identity changed') if foreign else (
                                ('PUBLIC_TRUNCATED' if truncated else 'PUBLIC_QUERY_UNMET') if public
                                else 'backend error/truncation/invalid state')
                            with patch.object(a, 'request', request), self.assertRaisesRegex(ValueError, message):
                                a.execute('SELECT 1', self.root / f'terminal-{public}-{state}-{foreign}-{truncated}',
                                          {}, 1000, lambda h: None, check)
                            t = a.timings
                            self.assertIsNone(t.events['first_row'])
                            self.assertIsNone(t.events['last_row'])
                            self.assertIsNone(t.events['result_complete'])
                            self.assertIsNone(a.request_kind)
                            if foreign:
                                self.assertEqual(t.events, accepted['events'])
                                self.assertEqual(t.backend, accepted['backend'])
                            else:
                                self.assertEqual(t.events['terminal_observed'], 6)
                                self.assertEqual(t.backend['page_state'], state)
                                self.assertEqual(t.backend['values'], dict(queuedTimeMillis=0, cpuTimeMillis=99,
                                                                         wallTimeMillis=None, elapsedTimeMillis=None))
                                self.assertNotIn('private', json.dumps(t.backend))

    def test_accepted_page_observed_before_native_receipt_and_header_failure(self):
        for public in (False, True):
            for fail_header in (False, True):
                with self.subTest(public=public, fail_header=fail_header):
                    a = self.public_adapter() if public else runner.TrinoAdapter('http://localhost:1234')
                    page, _ = self.delivery_pages(a)
                    saved = []
                    original_writer = runner.Writer
                    def observed():
                        self.assertEqual(a.timings.events['first_row'], 3)
                        self.assertEqual(a.timings.events['last_row'], 3)
                        self.assertEqual(a.timings.backend['values']['cpuTimeMillis'], 12)
                        self.assertEqual(a.timings.backend['page_state'], 'RUNNING')
                    def request(*args):
                        self.clock.advance(3)
                        return page
                    def save(handle):
                        saved.append(handle)
                        if 'native_columns' in handle:
                            observed()
                            self.clock.advance(7)
                        else:
                            self.assertIsNone(a.timings.events['first_row'])
                    def writer(*args):
                        observed()
                        self.assertIn('native_columns', saved[-1])
                        self.clock.advance(11)
                        if fail_header:
                            raise RuntimeError('header setup')
                        return original_writer(*args)
                    def check():
                        observed()
                        raise RuntimeError('capacity')
                    target = 'benchmarks.local_perf.public.Writer' if public else 'benchmarks.local_perf.runner.Writer'
                    with patch.object(a, 'request', side_effect=request) as mocked_request, patch(target, writer), \
                            self.assertRaisesRegex(RuntimeError, 'header setup' if fail_header else 'capacity'):
                        a.execute('SELECT 1', self.root / f'header-{public}-{fail_header}', {}, 1000, save, check)
                    observed()
                    self.assertEqual(mocked_request.call_count, 1)
                    self.assertEqual(len(saved), 2)
                    self.assertEqual(a.timings.durations['artifact_processing_s'], 18)
                    self.assertIsNone(a.timings.events['result_complete'])
                    self.assertIsNone(a.request_kind)

    def test_pending_public_page_stats_observed_before_capacity_failure(self):
        a = self.public_adapter()
        page = dict(id='q', backend_query_id=None, **copy.deepcopy(a.binding), state='QUEUED',
                    stats={'state': 'QUEUED', 'queuedTimeMillis': 7}, columns=[], data=[],
                    next_uri='/api/v1/queries/q/pages/t')
        with patch.object(a, 'request', return_value=page), self.assertRaisesRegex(RuntimeError, 'capacity'):
            a.execute('SELECT 1', self.root / 'pending', {}, 1000, lambda h: None,
                      MagicMock(side_effect=RuntimeError('capacity')))
        self.assertEqual(a.timings.backend['values']['queuedTimeMillis'], 7)
        self.assertEqual(a.timings.backend['page_state'], 'QUEUED')
        self.assertIsNone(a.timings.events['first_row'])
        self.assertIsNone(a.timings.events['last_row'])
        self.assertIsNone(a.request_kind)

    def test_standalone_collector_and_request_kind_cleanup(self):
        for public in (False, True):
            for failure in (None, 'connect', 'header'):
                with self.subTest(public=public, failure=failure):
                    a = self.public_adapter() if public else runner.TrinoAdapter('http://localhost:1234')
                    pages = self.delivery_pages(a)
                    with self.transport(pages, failure), patch.object(runner.threading, 'Timer'):
                        if failure:
                            with self.assertRaises(OSError):
                                a.execute('SELECT 1', self.root / f'solo-{public}-{failure}', {}, 1000,
                                          lambda h: None, lambda: None)
                        else:
                            a.execute('SELECT 1', self.root / f'solo-{public}', {}, 1000,
                                      lambda h: None, lambda: None)
                    t = a.timings
                    self.assertIs(runner.adapter_timings(a), t)
                    self.assertEqual(t.http['initial']['calls'], 1)
                    self.assertEqual(t.http['follow_up']['calls'], 0 if failure else 1)
                    self.assertEqual(t.events['dispatch_started'], 0)
                    self.assertEqual(t.events['initial_response_complete'], None if failure else 15)
                    self.assertIsNone(a.request_kind)
                    if failure:
                        self.assertIsNone(t.events['result_complete'])
                        self.assertGreater(t.http['initial']['total'], 0)
                    else:
                        self.assertEqual(t.events['result_complete'], 30)
        a = self.public_adapter()
        a.request_kind = 'initial'
        with self.assertRaises(ValueError):
            a.execute('SELECT 2', self.root / 'wrong-sql', {}, 1000, lambda h: None, lambda: None)
        self.assertIsNone(a.request_kind)
        self.assertIsNone(a.timings.events['dispatch_started'])

    def test_run_reuse_uses_fresh_collector_without_leaking_standalone_timings(self):
        a = runner.TrinoAdapter('http://localhost:1234')
        page = dict(id='q', stats={'state': 'FINISHED'}, columns=[dict(name='x', type='integer')], data=[[1]])
        with self.transport([page]), patch.object(runner.threading, 'Timer'):
            a.execute('SELECT 1', self.root / 'standalone', {}, 1000, lambda h: None, lambda: None)
        previous = a.timings
        before = copy.deepcopy(previous.snapshot())
        for i in range(2):
            c, manifest = self.run_attempt(a, f'reuse-{i}')
            with self.transport([page]), patch.object(runner.threading, 'Timer'):
                root = runner.run(c, 'result', manifest, a, reference_label='timing-test')
            t = json.loads((root / 'q-0/attempt.json').read_text())['timings']
            self.assertEqual(t['events']['initial_response_complete'], 15)
            self.assertEqual(t['events']['result_complete'], 15)
            self.assertEqual(t['http']['initial']['calls'], 1)
            self.assertEqual(t['http']['follow_up']['calls'], 0)
            self.assertIs(a.timings, previous)
            self.assertEqual(previous.snapshot(), before)
            self.assertIsNone(a.request_kind)

    def test_duckdb_dispatch_after_dataset_connect_setup_and_views(self):
        dataset = self.root / 'dataset'
        dataset.mkdir()
        (dataset / 'dataset.json').write_text(json.dumps(dict(status='SUCCESS', peak_bytes=1024,
            tables=[dict(name=name, file=name + '.parquet') for name in ('a', 'b')])))
        a = runner.DuckDBAdapter(dataset)
        con = MagicMock()
        con.description = [('x', 'INTEGER')]
        read_text = Path.read_text
        def load(path, *args, **kwargs):
            if path == dataset / 'dataset.json':
                self.clock.advance(5)
            return read_text(path, *args, **kwargs)
        def connect(*args, **kwargs):
            self.clock.advance(11)
            return con
        def setup(*args):
            self.clock.advance(13)
            return nullcontext()
        def execute(sql):
            self.clock.advance(7 if sql.startswith('CREATE VIEW') else 2)
            return con
        con.execute.side_effect = execute
        con.fetchmany.side_effect = [[(1,)], []]
        c, manifest = self.run_attempt(a)
        with patch.object(Path, 'read_text', load), patch.object(runner, 'connect', connect), \
                patch.object(runner, 'bounded', setup):
            root = runner.run(c, 'result', manifest, a, reference_label='timing-test')
        t = json.loads((root / 'q-0/attempt.json').read_text())['timings']
        self.assertEqual(t['events']['dispatch_started'], 43)
        self.assertEqual(t['durations']['query_execute_s'], 2)
        self.assertEqual(con.execute.call_count, 3)
        self.assertEqual(t['events']['result_complete'], 45)

    def test_duckdb_fake_connection_execute_fetch_and_no_network(self):
        a = runner.DuckDBAdapter()
        a.timings = runner.Timings()
        con = MagicMock()
        con.description = [('x', 'INTEGER')]
        def execute(sql):
            self.clock.advance(2)
            return con
        batches = iter([[(1,), (2,)], []])
        def fetch(size):
            self.clock.advance(3)
            return next(batches)
        con.execute.side_effect = execute
        con.fetchmany.side_effect = fetch
        with patch.object(runner, 'connect', return_value=con), \
                patch.object(runner, 'bounded', return_value=nullcontext()):
            a.execute('SELECT 1', self.root / 'duck', {}, 1000, lambda h: None, lambda: None)
        t = a.timings
        self.assertEqual(t.durations['query_execute_s'], 2)
        self.assertEqual(t.durations['local_fetch_s'], 6)
        self.assertEqual(t.events['first_row'], 5)
        self.assertEqual(t.events['last_row'], 5)
        self.assertEqual(t.events['terminal_observed'], 8)
        self.assertIsNone(t.durations['client_poll_wait_s'])
        self.assertIsNone(t.events['initial_response_complete'])
        self.assertFalse(t.backend['client_observed'])
        self.assertEqual(t.http['initial']['calls'], 0)


if __name__ == '__main__':
    unittest.main()
