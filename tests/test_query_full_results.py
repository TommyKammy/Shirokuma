"""Explicit full-result cursors retain a bounded, owner-authorized replay window."""
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store
import query_service as qs
import query_result_exports as exports
import workbench_server as wb
from fixture_lifecycle import setup_fixture
import test_dataops_service as fixtures
import test_query_result_exports as export_fixtures


def http_response(payload, length=None):
    stream = io.BytesIO(payload)
    response = Mock(status=200, length=length)
    def read1(size):
        chunk = stream.read(size)
        if response.length is not None:
            response.length -= len(chunk)
        return chunk
    response.read1.side_effect = read1
    return response


class FullResultTests(unittest.TestCase):
    def setUp(self):
        self.real_transport = qs._transport
        self.f = setup_fixture(self, fixtures.DataOpsTests())
        self.context = self.f.context()

    def submit(self, **options):
        values = dict(mode='read-only', max_rows=None, result_mode='all',
                      request_id='request', worksheet_id='worksheet')
        values.update(options)
        return qs.submit(self.f.principal, self.context,
                         'SELECT id, amount, happened_at, details FROM sales.raw.events', **values)

    def fetch(self, page):
        return qs.fetch(self.f.principal, page['id'], self.token(page),
                        selected_role=self.context.selected_role)

    @staticmethod
    def token(page):
        return page['next_uri'].rsplit('/', 1)[1]

    def assert_code(self, code, action, status=None):
        with self.assertRaises(qs.QueryError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        if status is not None:
            self.assertEqual(caught.exception.status, status)
        self.assertEqual(caught.exception.details, {})

    @staticmethod
    def backend_page(index, count=20, data=None):
        page = dict(id='generic_backend', data=data if data is not None else [[index]],
                    stats=dict(state='RUNNING'))
        if index + 1 < count:
            page['nextUri'] = f'http://127.0.0.1:1234/v1/statement/generic_backend/{index + 1}'
            page['partialCancelUri'] = f'http://127.0.0.1:1234/v1/statement/generic_backend/partial/{index + 1}'
        return page

    def assert_bounded(self, pin):
        self.assertLessEqual(len(pin.responses), 2)
        self.assertLessEqual(len(pin.tokens), 4)
        self.assertLessEqual(len(pin.pending_tokens), 3)
        self.assertLessEqual(len(pin.failures), 2)
        self.assertLessEqual(len(pin.cancel_urls), 1)

    def test_typed_rows_over_ten_thousand_finish_without_truncation(self):
        columns = [dict(name='id', type='bigint'), dict(name='amount', type='decimal(25,8)'),
                   dict(name='happened_at', type='timestamp(6)'), dict(name='details', type='map(varchar,json)')]
        expected = [[2**53 + i, f'{i}.12345678', '2026-01-02 03:04:05.123456',
                     {'nullable': None, 'array': [i, True, '雪']}] for i in range(13206)]
        pages = [self.backend_page(i, 6, expected[i * 2201:(i + 1) * 2201]) for i in range(6)]
        pages[0]['columns'] = columns
        responses = [http_response(json.dumps(page).encode()) for page in pages]
        connection = Mock(sock=Mock())
        connection.getresponse.side_effect = responses
        with patch.object(qs, '_transport', wraps=self.real_transport) as transport, \
                patch.object(qs.http.client, 'HTTPConnection', return_value=connection):
            page = self.submit()
            rows = []
            while True:
                self.assertEqual(page['result_mode'], 'all')
                self.assertEqual(page['columns'], columns)
                self.assertNotIn('truncated', page)
                rows.extend(page['data'])
                if not page['next_uri']:
                    break
                page = self.fetch(page)
            self.assertEqual(rows, expected)
            self.assertEqual(page['state'], 'FINISHED')
            self.assertEqual(qs._row(page['id'])['state'], 'FINISHED')
            self.assertEqual(transport.call_count, 6)
            self.assertEqual([call.args[1] for call in transport.call_args_list], ['POST'] + ['GET'] * 5)
        pin = qs._pins[page['id']]
        self.assertTrue(pin.full_results)
        self.assertIsNone(pin.initial_response)
        self.assert_bounded(pin)

    def test_preview_still_truncates_and_private_none_does_not_opt_in(self):
        def backend(url, method, pin, body=None):
            return {} if method == 'DELETE' else self.backend_page(0, data=[[i] for i in range(12)])
        with patch.object(qs, '_transport', side_effect=backend) as transport:
            page = self.submit(result_mode='preview', max_rows=10)
        self.assertEqual(page['data'], [[i] for i in range(10)])
        self.assertTrue(page['truncated'])
        self.assertIsNone(page['next_uri'])
        self.assertNotIn('result_mode', page)
        self.assertFalse(qs._pins[page['id']].full_results)
        self.assertEqual([call.args[1] for call in transport.call_args_list], ['POST', 'DELETE'])
        private = qs._capture_dataops(self.f.principal, self.context)
        self.assertIsNone(private.max_rows)
        self.assertFalse(private.full_results)

    def test_service_all_mode_rejects_invalid_options_before_backend_or_admission(self):
        invalid = [dict(result_mode=mode) for mode in ('ALL', None, '', True, 1, {}, [])]
        invalid += [dict(max_rows=value) for value in (True, False, 0, 1, 10000, 'all')]
        invalid += [dict(mode=mode) for mode in ('sandbox-write', 'admin-write', None)]
        with patch.object(qs, '_transport') as transport, patch.object(qs.admission, 'admit') as admit:
            for options in invalid:
                with self.subTest(options=options):
                    self.assert_code('UNKNOWN_CONTEXT', lambda: self.submit(**options), 400)
            transport.assert_not_called()
            admit.assert_not_called()

    def test_http_exact_six_or_seven_fields(self):
        body = export_fixtures.ResultExportTests.worksheet_body(self)
        request = lambda value: export_fixtures.ResultExportTests.request(
            self, wb.WorkbenchHandler, path='/api/v1/queries', body=json.dumps(value))
        invalid = [body | {'max_rows': value} for value in (None, True, False, 0, 10001, 1.5)]
        invalid += [body | {'result_mode': value, 'max_rows': None}
                    for value in ('preview', 'ALL', '', None, True, 1, [])]
        invalid += [body | {'result_mode': 'all', 'max_rows': value}
                    for value in (True, False, 1, 10000)]
        invalid += [body | {'result_mode': 'all', 'max_rows': None, 'mode': mode}
                    for mode in ('sandbox-write', 'admin-write')]
        invalid += [body | {'extra': 1}, body | {'result_mode': 'all', 'max_rows': None, 'extra': 1}]
        invalid += [{key: value for key, value in body.items() if key != 'max_rows'}]
        with patch.object(qs, '_transport', return_value=dict(id='backend', data=[[1]])) as transport:
            for value in invalid:
                with self.subTest(body=value):
                    status, error = request(value)
                    self.assertEqual((status, error['code']), (400, 'INVALID_INPUT'))
            transport.assert_not_called()
            for value in (body, body | {'result_mode': 'all', 'max_rows': None}):
                status, page = request(value)
                self.assertEqual(status, 200, page)
                self.assertEqual(page.get('result_mode'), value.get('result_mode'))
            self.assertEqual(transport.call_count, 2)

    def test_caller_mutation_of_delivered_page_does_not_corrupt_cached_replay(self):
        # The cache owns the freshly received response and the caller receives a
        # deep copy, so arbitrary nested mutation of the delivered payload must
        # not leak into the replay window.
        payload = [[{'id': 1, 'details': {'tags': ['a', 'b'], 'nested': [{'k': None}]}},
                    {'id': 2, 'details': {'tags': [], 'nested': []}}]]
        with patch.object(qs, '_transport',
                          side_effect=[self.backend_page(0, 2), self.backend_page(1, 2, payload[0])]) as transport:
            first = self.submit()
            delivered = self.fetch(first)          # page 1 is stored in the replay cache
            expected = deepcopy(payload[0])
            self.assertEqual(delivered['data'], expected)
            pin = qs._pins[first['id']]
            self.assertIn(expected, [response['data'] for response in pin.responses.values()])

            delivered['data'][0]['id'] = -1
            delivered['data'][0]['details']['tags'].append('mutated')
            delivered['data'][0]['details']['nested'][0]['k'] = 'mutated'
            delivered['data'].append({'id': 99})

            before = transport.call_count
            replay = self.fetch(first)             # same token: cache hit, no backend call
            self.assertEqual(transport.call_count, before)
            self.assertEqual(replay['data'], expected)
            cached = [response['data'] for response in pin.responses.values()]
            self.assertIn(expected, cached)
            self.assertNotIn(99, [row.get('id') for data in cached for row in data])
            self.assertNotIn('mutated', json.dumps(cached))

    def test_many_pages_replay_two_evict_without_backend_and_release_initial(self):
        with patch.object(qs, '_transport', side_effect=[self.backend_page(i, 80) for i in range(80)]) as transport:
            page = self.submit()
            pin = qs._pins[page['id']]
            consumed = []
            for index in range(1, 80):
                consumed.append((self.token(page), None))
                page = self.fetch(page)
                consumed[-1] = (consumed[-1][0], deepcopy(page))
                self.assertEqual(page['data'], [[index]])
                self.assertIsNone(pin.initial_response)
                self.assert_bounded(pin)
                before = transport.call_count
                for token, expected in consumed[-2:]:
                    self.assertEqual(qs.fetch(self.f.principal, page['id'], token,
                                             selected_role=self.context.selected_role), expected)
                if len(consumed) > 2:
                    self.assert_code('QUERY_NOT_FOUND', lambda: qs.fetch(self.f.principal, page['id'],
                        consumed[-3][0], selected_role=self.context.selected_role), 404)
                self.assertEqual(transport.call_count, before)
            self.assertEqual(page['state'], 'FINISHED')
            self.assertEqual(transport.call_count, 80)

    def test_cached_results_recheck_owner_role_and_closure(self):
        with patch.object(qs, '_transport', side_effect=[self.backend_page(0), self.backend_page(1)]) as transport:
            first = self.submit()
            second = self.fetch(first)
            token = self.token(first)
            fetch = lambda principal=self.f.principal, role=self.context.selected_role: qs.fetch(
                principal, first['id'], token, selected_role=role)
            self.assertEqual(fetch(), second)
            self.assert_code('QUERY_NOT_FOUND', lambda: fetch(self.f.fixture.principal('owner'), 'admin'), 404)
            self.assert_code('CONTEXT_MISMATCH', lambda: fetch(role='admin'), 409)
            with control_store.connect() as db:
                db.execute("UPDATE warehouse_admissions SET execution_closure='FENCED_READ_ONLY' WHERE subject_id=?",
                           (first['id'],))
            self.assert_code('EXECUTION_CLOSED', fetch, 409)
            with control_store.connect() as db:
                db.execute("UPDATE warehouse_admissions SET execution_closure='' WHERE subject_id=?", (first['id'],))
            self.f.fixture.users['alice']['role'] = 'bi_viewer'
            self.f.fixture.save()
            with patch.object(qs, '_best_cancel'):
                self.assert_code('DB_ACCESS_REVOKED', fetch, 403)
            self.assertEqual(transport.call_count, 2)

    def test_cached_results_recheck_current_grant_revision_before_replay(self):
        with patch.object(qs, '_transport', side_effect=[self.backend_page(0), self.backend_page(1)]) as transport:
            first = self.submit()
            second = self.fetch(first)
            self.assertEqual(self.fetch(first), second)
            with control_store.connect() as db:
                db.execute('UPDATE grants SET revision=revision+1 WHERE principal_id=? AND database_id=?',
                           (self.f.principal.principal_id, self.context.database_id))
            with patch.object(qs, '_best_cancel'):
                self.assert_code('DB_ACCESS_REVOKED', lambda: self.fetch(first), 403)
            self.assertEqual(transport.call_count, 2)

    def test_cached_logical_identity_revocation_blocks_replay_before_data(self):
        import test_logical_access as logical_fixtures
        import logical_registry
        fixture = setup_fixture(self, logical_fixtures.LogicalAccessTests())
        context = fixture.context()
        with control_store.connect() as db:
            db.execute("INSERT INTO query_records VALUES ('full-logical','alice','db','marts','LOCAL','foundation','FINISHED','now')")
            db.execute("INSERT INTO query_execution_contexts VALUES ('full-logical','bi_viewer','read-only','db','marts',1,1,'alice-id-user','pin','backend','request','worksheet','SELECT 1')")
        pin = qs._Pin(context, '', {}, None, max_rows=None, full_results=True)
        cached = dict(id='full-logical', result_mode='all', data=[[42]], columns=[],
                      state='FINISHED', stats={'state':'FINISHED'}, next_uri=None)
        pin.responses['replay'] = cached
        fetch = lambda: qs.fetch(fixture.alice, 'full-logical', 'replay', selected_role='bi_viewer')
        with patch.dict(qs._pins, {'full-logical': pin}), patch.object(qs, '_transport') as transport, \
                patch.object(qs, '_best_cancel'):
            self.assertEqual(fetch(), cached)
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                identity = db.execute("SELECT id FROM logical_backend_identities WHERE principal_id='alice'").fetchone()[0]
                logical_registry.revoke_identity(db, identity)
            self.assert_code('DB_ACCESS_REVOKED', fetch, 403)
            transport.assert_not_called()

    def test_failure_replays_without_backend_and_latest_cancellation_is_retained(self):
        def backend(url, method, pin, body=None):
            if method == 'DELETE':
                return {}
            index = 0 if method == 'POST' else int(url.rsplit('/', 1)[1])
            if index == 25:
                raise qs.QueryError('QUERY_BACKEND_FAILED', 502)
            return self.backend_page(index, 50)
        with patch.object(qs, '_transport', side_effect=backend) as transport:
            page = self.submit()
            for _ in range(24):
                page = self.fetch(page)
            target = page['next_uri']
            for _ in range(6):
                self.assert_code('QUERY_BACKEND_FAILED', lambda: self.fetch(page), 502)
                self.assert_bounded(qs._pins[page['id']])
            self.assertEqual(transport.call_count, 26)
            self.assertEqual(qs._row(page['id'])['state'], 'UNKNOWN')
            self.assertEqual(qs.cancel(self.f.principal, page['id'], selected_role=self.context.selected_role)['state'], 'CANCELED')
            self.assertEqual(transport.call_args.args[0], 'http://127.0.0.1:1234/v1/statement/generic_backend/25')
            self.assertEqual(transport.call_args.args[1], 'DELETE')
            self.assertIsNotNone(target)

    def test_full_results_received_page_export_explicitly_unavailable(self):
        with patch.object(qs, '_transport', return_value=dict(id='backend', data=[[1]])):
            page = self.submit()
        body = dict(format='json', request_id='request', worksheet_id='worksheet',
                    received_pages=[page['result_page_id']])
        with patch.object(exports, '_serialize', side_effect=AssertionError('must not assemble full results')):
            self.assert_code('RESULT_EXPORT_UNAVAILABLE', lambda: exports.export(
                self.f.principal, page['id'], self.context.selected_role, body))

    def test_cold_many_pending_polls_then_background_completion_preserves_current_link(self):
        entered, release = threading.Event(), threading.Event()
        lifecycle = sys.modules['warehouse_lifecycle']
        lifecycle.applicable = lambda: True
        lifecycle.requires_wake = lambda: True
        def wake(**kwargs):
            kwargs['authorize']()
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test wake did not release')
            kwargs['authorize']()
        lifecycle.ensure_awake = wake
        with patch.object(qs, '_transport', side_effect=[self.backend_page(0, 2), self.backend_page(1, 2)]) as transport:
            initial = self.submit()
            pin = qs._pins[initial['id']]
            self.addCleanup(release.set)
            self.assertTrue(entered.wait(2))
            self.assertEqual(initial['result_mode'], 'all')
            page = initial
            previous = []
            try:
                for _ in range(12):
                    token = self.token(page)
                    page = self.fetch(page)
                    previous.append((token, deepcopy(page)))
                    self.assertEqual(page['result_mode'], 'all')
                    self.assertEqual(self.fetch(dict(page, next_uri='/pages/' + token)), page)
                    self.assert_bounded(pin)
                transport.assert_not_called()
                release.set()
                self.assertTrue(pin.initial_done.wait(3))
                # Completion must not overwrite an already-delivered pending replay.
                for token, expected in previous[-2:]:
                    self.assertEqual(qs.fetch(self.f.principal, page['id'], token,
                                             selected_role=self.context.selected_role), expected)
                actual = self.fetch(page)
                self.assertEqual(actual['data'], [[0]])
                self.assertEqual(self.fetch(page), actual)
                self.assertIsNone(pin.initial_response)
                final = self.fetch(actual)
                self.assertEqual((final['state'], final['data']), ('FINISHED', [[1]]))
                self.assert_bounded(pin)
                self.assert_code('QUERY_NOT_FOUND', lambda: self.fetch(initial), 404)
                self.assertEqual(transport.call_count, 2)
            finally:
                release.set()
                self.assertTrue(pin.initial_done.wait(3))

    def test_cold_initial_completion_racing_poll_delivers_first_page_once(self):
        lifecycle = sys.modules['warehouse_lifecycle']
        lifecycle.applicable = lambda: True
        lifecycle.requires_wake = lambda: True
        entered, release = threading.Event(), threading.Event()
        def backend(*args):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('test initial request did not release')
            return dict(id='backend', data=[[42]])
        with patch.object(qs, '_transport', side_effect=backend) as transport:
            page = self.submit()
            pin = qs._pins[page['id']]
            try:
                self.assertTrue(entered.wait(2))
                result = []
                polling = threading.Thread(target=lambda: result.append(self.fetch(page)))
                polling.start()
                release.set()
                polling.join(3)
                self.assertFalse(polling.is_alive())
                self.assertEqual((result[0]['state'], result[0]['data']), ('FINISHED', [[42]]))
                for _ in range(8):
                    self.assertEqual(self.fetch(page), result[0])
                self.assertIsNone(pin.initial_response)
                self.assert_bounded(pin)
                transport.assert_called_once()
            finally:
                release.set()
                self.assertTrue(pin.initial_done.wait(3))


class FullTransportTests(unittest.TestCase):
    def setUp(self):
        self.transport = qs._transport
        self.f = setup_fixture(self, fixtures.DataOpsTests())
        self.context = self.f.context()
        self.pin = qs._Pin(self.context, 'http://127.0.0.1:1234', {}, None,
                           max_rows=None, full_results=True)

    def call(self):
        return self.transport(self.pin.origin + '/v1/statement', 'POST', self.pin, b'SELECT 1')

    def test_complete_declared_body_stops_before_closed_socket_timeout(self):
        payload = b'{"id":"valid","data":[[42]]}'
        response = http_response(payload, len(payload))
        connected_socket = Mock()
        connected_socket.settimeout.side_effect = [None, OSError('Bad file descriptor')]
        connection = Mock(sock=connected_socket)
        def getresponse():
            connection.sock = None
            return response
        connection.getresponse.side_effect = getresponse
        with patch.object(qs.http.client, 'HTTPConnection', return_value=connection):
            self.assertEqual(self.call(), {'id': 'valid', 'data': [[42]]})
        self.assertEqual(response.length, 0)
        response.read1.assert_called_once()
        connected_socket.settimeout.assert_called_once()
        response.close.assert_called_once()
        connection.close.assert_called_once()

    def test_size_limit_precedes_json_decode_and_closes_connection(self):
        for size, declared in ((64, None), (64, 64), (65, None), (65, 65)):
            payload = json.dumps({'data': 'x' * (size - 12)}).encode()
            self.assertEqual(len(payload), size)
            response = http_response(payload, declared)
            connection = Mock(sock=Mock())
            connection.getresponse.return_value = response
            with self.subTest(size=size, declared=declared), patch.object(qs, 'FULL_RESPONSE_LIMIT', 64), \
                    patch.object(qs.http.client, 'HTTPConnection', return_value=connection), \
                    patch.object(qs.json, 'loads', wraps=json.loads) as decode:
                if size == 64:
                    self.assertEqual(self.call(), {'data': 'x' * 52})
                    decode.assert_called_once()
                else:
                    with self.assertRaises(qs.QueryError) as caught:
                        self.call()
                    self.assertEqual(caught.exception.status, 502)
                    self.assertNotIn('xxxx', str(caught.exception))
                    decode.assert_not_called()
                connection.close.assert_called_once()

    def test_premature_eof_with_valid_json_precedes_decode(self):
        payload = b'{"ok":true}'
        response = http_response(payload, len(payload) + 5)
        connection = Mock(sock=Mock())
        connection.getresponse.return_value = response
        with patch.object(qs.http.client, 'HTTPConnection', return_value=connection), \
                patch.object(qs.json, 'loads', wraps=json.loads) as decode:
            with self.assertRaises(qs.QueryError) as caught:
                self.call()
            self.assertEqual(caught.exception.code, 'QUERY_BACKEND_FAILED')
            self.assertEqual(caught.exception.status, 502)
            self.assertEqual(caught.exception.details, {})
            decode.assert_not_called()
        self.assertEqual(response.length, 5)
        response.close.assert_called_once()
        connection.close.assert_called_once()

    def test_deadline_interrupts_blocked_body_after_socket_detaches(self):
        interrupted = threading.Event()
        connected_socket = Mock()
        connected_socket.shutdown.side_effect = lambda how: interrupted.set()
        response = Mock(status=200, length=None)
        def read1(size):
            if not interrupted.wait(0.5):
                raise AssertionError('captured socket was not interrupted')
            raise OSError('private body detail')
        response.read1.side_effect = read1
        connection = Mock(sock=None)
        connection.connect.side_effect = lambda: setattr(connection, 'sock', connected_socket)
        def getresponse():
            connection.sock = None
            return response
        connection.getresponse.side_effect = getresponse
        timers = []
        real_timer = threading.Timer
        def make_timer(*args, **kwargs):
            timer = real_timer(*args, **kwargs)
            timers.append(timer)
            return timer
        with patch.object(qs, 'FULL_REQUEST_TIMEOUT', 0.04), \
                patch.object(qs.threading, 'Timer', side_effect=make_timer), \
                patch.object(qs.http.client, 'HTTPConnection', return_value=connection):
            start = time.monotonic()
            with self.assertRaises(qs.QueryError) as caught:
                self.call()
            self.assertLess(time.monotonic() - start, 0.4)
        self.assertTrue(interrupted.is_set())
        self.assertEqual(caught.exception.code, 'QUERY_BACKEND_FAILED')
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(caught.exception.details, {})
        self.assertNotIn('private', str(caught.exception))
        connected_socket.shutdown.assert_called_once_with(qs.socket.SHUT_RDWR)
        response.close.assert_called_once()
        connection.close.assert_called_once()
        self.assertEqual(len(timers), 1)
        self.assertFalse(timers[0].is_alive())

    def test_absolute_deadline_covers_connect_status_headers_and_slow_drip(self):
        for phase in ('connect', 'request', 'headers', 'body', 'detached-body'):
            stopped = threading.Event()
            class Socket:
                def settimeout(self, timeout):
                    pass
                def shutdown(self, how):
                    stopped.set()
            class Connection:
                def __init__(self):
                    self.sock = Socket()
                    self.timeout = None
                    self.closed = False
                def delay(self):
                    if not stopped.wait(0.5):
                        raise AssertionError('deadline failed to interrupt transport')
                    raise OSError('private transport detail')
                def connect(self):
                    if phase == 'connect':
                        self.delay()
                def request(self, *args, **kwargs):
                    if phase == 'request':
                        self.delay()
                def getresponse(self):
                    if phase == 'headers':
                        self.delay()
                    if phase == 'detached-body':
                        # HTTPResponse owns a close-delimited response socket.
                        self.sock = None
                    return self
                status = 200
                length = None
                def read1(self, size):
                    # A byte arrives frequently enough to defeat a per-read timeout.
                    stopped.wait(0.004)
                    if stopped.is_set():
                        raise OSError('private slow body detail')
                    return b' '
                def close(self):
                    self.closed = True
                    stopped.set()
            connection = Connection()
            with self.subTest(phase=phase), patch.object(qs, 'FULL_REQUEST_TIMEOUT', 0.04), \
                    patch.object(qs.http.client, 'HTTPConnection', return_value=connection):
                start = time.monotonic()
                with self.assertRaises(qs.QueryError) as caught:
                    self.call()
                self.assertLess(time.monotonic() - start, 0.4)
                self.assertTrue(connection.closed)
                self.assertNotIn('private', str(caught.exception))
                self.assertIn(caught.exception.status, (502, 504))

    def test_legacy_transport_keeps_unbounded_read_and_no_explicit_connect(self):
        self.pin.full_results = False
        response = Mock(status=200, read=Mock(return_value=b'{"ok":true}'))
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(qs.http.client, 'HTTPConnection', return_value=connection):
            self.assertEqual(self.call(), {'ok': True})
        response.read.assert_called_once_with()
        response.read1.assert_not_called()
        connection.connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
