"""Explicit full-result cursors retain a bounded, owner-authorized replay window."""
from copy import deepcopy
import io
import json
import http.client
import ssl
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

    def test_first_delivery_carries_preencoded_body_and_replay_is_independent(self):
        # A fresh full-result page is encoded once for the replay cache and the
        # HTTP layer writes those bytes; replay decodes a fresh mapping.
        with patch.object(qs, '_transport',
                          side_effect=[self.backend_page(0, 2), self.backend_page(1, 2, [[7, 'x']])]):
            first = self.submit()
            delivered = self.fetch(first)
        pin = qs._pins[first['id']]
        token = self.token(first)
        self.assertIsInstance(delivered, qs.database_http_json.JSONResponse)
        self.assertEqual(delivered.body, json.dumps(dict(delivered), default=str).encode())
        self.assertEqual(pin.payloads[token], delivered.body)
        self.assertNotIn(token, pin.responses)
        expected = deepcopy(delivered['data'])
        delivered['data'][0][0] = -1
        delivered['data'].append([99, 'mutated'])
        replay = self.fetch(first)
        self.assertEqual(replay['data'], expected)
        self.assertEqual(json.loads(pin.payloads[token])['data'], expected)

    def test_respond_writes_preencoded_jsonresponse_without_reserializing(self):
        import database_http_json
        handler = Mock(_creation_location=None)
        value = database_http_json.JSONResponse({'a': 1}, b'{"a":1}\n')
        with patch.object(database_http_json.json, 'dumps',
                          side_effect=AssertionError('must not re-encode')):
            database_http_json.respond(handler, value)
        handler.wfile.write.assert_called_once_with(b'{"a":1}\n')
        handler.send_header.assert_any_call('Content-Length', '8')

    def test_json_domain_page_values_survive_first_delivery_and_replay(self):
        # The page replay cache is JSON bytes; the first delivery is decoded on
        # replay.  The JSON value domain must be preserved exactly (bigint,
        # decimal-as-string, UTF-8 and empty strings, bool, None, nested
        # list/map, negative zero).
        values = [[2**63 + 7, '12345678901234567890.123456789', '雪', '', True, None,
                   {'k': [1, [2, {'deep': -0.0}]]}, -0.0, 1.5, False]]
        page = self.backend_page(1, 2, values)
        page['stats'] = {'state': 'RUNNING', 'processedRows': 2**53 + 1,
                         'cpuTimeMillis': 7, 'queuedTimeMillis': 0}
        with patch.object(qs, '_transport', side_effect=[self.backend_page(0, 2), page]):
            first = self.submit()
            delivered = self.fetch(first)
        self.assertEqual(delivered['data'], values)
        self.assertEqual(delivered['stats']['processedRows'], 2**53 + 1)
        self.assertIs(type(delivered['data'][0][0]), int)
        self.assertIs(type(delivered['data'][0][6]['k'][1][1]['deep']), float)
        self.assertEqual(str(delivered['data'][0][6]['k'][1][1]['deep']), '-0.0')
        pin = qs._pins[first['id']]
        self.assertIn(deepcopy(values), [json.loads(payload)['data'] for payload in pin.payloads.values()])

    def test_real_http11_loopback_reuses_get_connection_and_closes_at_terminal(self):
        import http.server

        pages = [json.dumps(self.backend_page(i, 4, data=[[i]])).encode() for i in range(4)]
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), http.server.BaseHTTPRequestHandler)
        server.pages, server.next_page, server.requests = pages, 0, 0
        server.connections = 0
        server.lock = threading.Lock()
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def setup(self):
                super().setup()
                with server.lock:
                    server.connections += 1
            def do_POST(self):
                self._send()
            def do_GET(self):
                self._send()
            def _send(self):
                with server.lock:
                    index = server.next_page
                    server.next_page += 1
                    server.requests += 1
                body = server.pages[index]
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'keep-alive')
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
            def log_message(self, *args):
                pass
        server.RequestHandlerClass = Handler
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        real_connection = http.client.HTTPConnection
        def factory(host, port, timeout=30):
            return real_connection('127.0.0.1', server.server_port, timeout=timeout)
        try:
            with patch.object(qs, '_transport', wraps=self.real_transport), \
                    patch.object(qs.http.client, 'HTTPConnection', new=factory):
                page = self.submit()
                rows = []
                while True:
                    rows.extend(page['data'])
                    if not page['next_uri']:
                        break
                    page = self.fetch(page)
            self.assertEqual(rows, [[0], [1], [2], [3]])
            self.assertEqual((server.requests, server.connections), (4, 2))
            pin = qs._pins[page['id']]
            self.assertIsNone(pin.transport)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_real_http11_peer_silent_close_does_not_retry(self):
        import http.server

        pages = [json.dumps(self.backend_page(i, 3, data=[[i]])).encode() for i in range(3)]
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), http.server.BaseHTTPRequestHandler)
        server.pages, server.next_page, server.requests = pages, 0, 0
        server.lock = threading.Lock()
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def do_POST(self):
                self._send()
            def do_GET(self):
                self._send()
            def _send(self):
                with server.lock:
                    index = server.next_page
                    server.next_page += 1
                    server.requests += 1
                if index >= 2:
                    self.close_connection = True
                    return
                body = server.pages[index]
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'keep-alive')
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
            def log_message(self, *args):
                pass
        server.RequestHandlerClass = Handler
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        real_connection = http.client.HTTPConnection
        def factory(host, port, timeout=30):
            return real_connection('127.0.0.1', server.server_port, timeout=timeout)
        try:
            with patch.object(qs, '_transport', wraps=self.real_transport), \
                    patch.object(qs.http.client, 'HTTPConnection', new=factory):
                page = self.submit()
                page = self.fetch(page)
                failed_token = self.token(page)
                with self.assertRaises(qs.QueryError) as caught:
                    self.fetch(page)
                self.assertEqual(caught.exception.code, 'QUERY_BACKEND_FAILED')
                with self.assertRaises(qs.QueryError):
                    self.fetch(page)
            self.assertEqual(server.requests, 3)
            pin = qs._pins[page['id']]
            self.assertIsNone(pin.transport)
            self.assertIn(failed_token, pin.failures)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

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
            self.assertIn(expected, [json.loads(payload)['data'] for payload in pin.payloads.values()])

            delivered['data'][0]['id'] = -1
            delivered['data'][0]['details']['tags'].append('mutated')
            delivered['data'][0]['details']['nested'][0]['k'] = 'mutated'
            delivered['data'].append({'id': 99})

            before = transport.call_count
            replay = self.fetch(first)             # same token: cache hit, no backend call
            self.assertEqual(transport.call_count, before)
            self.assertEqual(replay['data'], expected)
            cached = [json.loads(payload)['data'] for payload in pin.payloads.values()]
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

    def test_page_token_never_crosses_query_context_even_for_same_owner(self):
        pages = [self.backend_page(0), self.backend_page(1), self.backend_page(0), self.backend_page(1)]
        with patch.object(qs, '_transport', side_effect=pages) as transport:
            first = self.submit()
            second = self.submit()
            first_token, second_token = self.token(first), self.token(second)
            self.assertEqual(transport.call_count, 2)
            self.assert_code('QUERY_NOT_FOUND', lambda: qs.fetch(
                self.f.principal, second['id'], first_token,
                selected_role=self.context.selected_role), 404)
            self.assert_code('QUERY_NOT_FOUND', lambda: qs.fetch(
                self.f.principal, first['id'], second_token,
                selected_role=self.context.selected_role), 404)
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

    def test_grant_revocation_after_inflight_page_never_delivers_its_data(self):
        calls = [0]
        def backend(url, method, pin, body=None):
            if method == 'DELETE':
                return {}
            if calls[0] == 0:
                calls[0] = 1
                return self.backend_page(0)
            with control_store.connect() as db:
                db.execute("UPDATE grants SET maximum_role='bi_viewer',revision=revision+1 "
                           "WHERE principal_id=? AND database_id=?",
                           (self.f.principal.principal_id, self.context.database_id))
            return self.backend_page(1, data=[[987654321]])
        with patch.object(qs, '_transport', side_effect=backend) as transport, \
                patch.object(qs, '_best_cancel'):
            first_page = self.submit()
            with self.assertRaises(qs.QueryError) as caught:
                self.fetch(first_page)
        self.assertEqual(caught.exception.code, 'DB_ACCESS_REVOKED')
        self.assertEqual(caught.exception.status, 403)
        self.assertNotIn('987654321', str(caught.exception))
        # The failed page is cached as a typed failure, never replayed as data.
        with patch.object(qs, '_transport') as retry, patch.object(qs, '_best_cancel'):
            with self.assertRaises(qs.QueryError) as cached:
                self.fetch(first_page)
        self.assertEqual(cached.exception.code, 'DB_ACCESS_REVOKED')
        retry.assert_not_called()
        self.assertEqual(transport.call_count, 2)  # POST + one GET

    def test_closure_during_inflight_page_never_delivers_its_data(self):
        query_id = [None]
        calls = [0]
        def backend(url, method, pin, body=None):
            if method == 'DELETE':
                return {}
            if calls[0] == 0:
                calls[0] = 1
                return self.backend_page(0)
            with control_store.connect() as db:
                db.execute("UPDATE warehouse_admissions SET execution_closure='FENCED_READ_ONLY' "
                           "WHERE kind='query' AND subject_id=?", (query_id[0],))
            return self.backend_page(1, data=[[123456789]])
        with patch.object(qs, '_transport', side_effect=backend):
            first_page = self.submit()
            query_id[0] = first_page['id']
            with self.assertRaises(qs.QueryError) as caught:
                self.fetch(first_page)
        self.assertEqual(caught.exception.code, 'EXECUTION_CLOSED')
        self.assertEqual(caught.exception.status, 409)
        self.assertNotIn('123456789', str(caught.exception))
        with patch.object(qs, '_transport') as retry:
            with self.assertRaises(qs.QueryError) as cached:
                self.fetch(first_page)
        self.assertEqual(cached.exception.code, 'EXECUTION_CLOSED')
        retry.assert_not_called()

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
        pin.payloads['replay'] = json.dumps(cached).encode()
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

    def logical_live_pin(self):
        import test_logical_access as logical_fixtures
        fixture = setup_fixture(self, logical_fixtures.LogicalAccessTests())
        context = fixture.context()
        query_id, token = 'full-logical-live', 'next'
        with control_store.connect() as db:
            db.execute("INSERT INTO query_records VALUES (?, 'alice', 'db', 'marts', 'LOCAL', 'foundation', 'RUNNING', 'now')", (query_id,))
            db.execute("INSERT INTO query_execution_contexts VALUES (?, 'bi_viewer', 'read-only', 'db', 'marts', 1, 1, 'alice-id-user', 'pin', 'backend', 'request', 'worksheet', 'SELECT 1')", (query_id,))
            handle = qs.admission.admit(db, actor='alice', kind='query', subject_id=query_id,
                                        generation_id='foundation', database_id='db')
        pin = qs._Pin(context, 'http://127.0.0.1:1234', {}, None, admission=handle,
                      max_rows=None, full_results=True, full_next_token=token)
        pin.tokens[token] = ('http://127.0.0.1:1234/v1/statement/backend/1', frozenset(('GET',)))
        return fixture, query_id, token, pin

    def test_logical_identity_revocation_during_inflight_page_never_delivers_its_data(self):
        import logical_registry
        fixture, query_id, token, pin = self.logical_live_pin()
        def backend(url, method, target, body=None):
            self.assertEqual(method, 'GET')
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                logical_registry.revoke_identity(db, 'alice-id')
            return dict(id='backend', data=[[987654321]], stats={'state': 'RUNNING'})
        with patch.dict(qs._pins, {query_id: pin}), patch.object(qs, '_transport', side_effect=backend) as transport, \
                patch.object(qs, '_best_cancel'):
            with self.assertRaises(qs.QueryError) as caught:
                qs.fetch(fixture.alice, query_id, token, selected_role='bi_viewer')
            self.assertEqual(caught.exception.code, 'DB_ACCESS_REVOKED')
            self.assertNotIn('987654321', str(caught.exception))
            with patch.object(qs, '_transport') as retry, patch.object(qs, '_best_cancel'):
                with self.assertRaises(qs.QueryError) as cached:
                    qs.fetch(fixture.alice, query_id, token, selected_role='bi_viewer')
        self.assertEqual(cached.exception.code, 'DB_ACCESS_REVOKED')
        retry.assert_not_called()
        transport.assert_called_once()

    def test_logical_binding_change_during_inflight_page_never_delivers_its_data(self):
        fixture, query_id, token, pin = self.logical_live_pin()
        def backend(url, method, target, body=None):
            self.assertEqual(method, 'GET')
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("UPDATE catalog_bindings SET desired_revision=2 WHERE database_id='db'")
            return dict(id='backend', data=[[987654321]], stats={'state': 'RUNNING'})
        with patch.dict(qs._pins, {query_id: pin}), patch.object(qs, '_transport', side_effect=backend) as transport, \
                patch.object(qs, '_best_cancel'):
            with self.assertRaises(qs.QueryError) as caught:
                qs.fetch(fixture.alice, query_id, token, selected_role='bi_viewer')
        self.assertEqual(caught.exception.code, 'DB_ACCESS_REVOKED')
        self.assertNotIn('987654321', str(caught.exception))
        transport.assert_called_once()

    def test_account_disable_during_inflight_page_never_delivers_its_data(self):
        fixture, query_id, token, pin = self.logical_live_pin()
        def backend(url, method, target, body=None):
            self.assertEqual(method, 'GET')
            fixture.accounts['alice']['disabled'] = True
            fixture.save_accounts()
            return dict(id='backend', data=[[987654321]], stats={'state': 'RUNNING'})
        with patch.dict(qs._pins, {query_id: pin}), patch.object(qs, '_transport', side_effect=backend), \
                patch.object(qs, '_best_cancel'):
            with self.assertRaises(qs.QueryError) as caught:
                qs.fetch(fixture.alice, query_id, token, selected_role='bi_viewer')
        self.assertEqual(caught.exception.code, 'DB_ACCESS_REVOKED')
        self.assertNotIn('987654321', str(caught.exception))

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
                    self.assertIsNone(self.pin.transport)
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
        self.assertIsNone(self.pin.transport)

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
        self.assertIsNone(self.pin.transport)
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

    def test_post_and_delete_never_retain_a_transport(self):
        response = http_response(b'{"id":"page"}', 13)
        response.will_close = False
        post_connection = Mock(sock=Mock())
        post_connection.getresponse.return_value = response
        post_connection.close = Mock()
        with patch.object(qs.http.client, 'HTTPConnection', return_value=post_connection):
            self.assertEqual(self.call(), {'id': 'page'})
        self.assertIsNone(self.pin.transport)
        post_connection.close.assert_called_once()
        delete_url = 'http://127.0.0.1:1234/v1/statement/backend/1'
        qs._registered(self.pin, delete_url, ('GET', 'DELETE'), 'backend')
        delete_response = http_response(b'{"id":"deleted"}', 16)
        delete_response.will_close = False
        delete_connection = Mock(sock=Mock())
        delete_connection.getresponse.return_value = delete_response
        delete_connection.close = Mock()
        with patch.object(qs.http.client, 'HTTPConnection', return_value=delete_connection):
            self.assertEqual(self.transport(delete_url, 'DELETE', self.pin), {'id': 'deleted'})
        self.assertIsNone(self.pin.transport)
        delete_connection.close.assert_called_once()

    def test_reused_socket_timeout_is_reset_for_a_new_absolute_deadline(self):
        first_response = http_response(b'{"id":"page"}', 13)
        first_response.will_close = False
        second_response = http_response(b'{"id":"page"}', 13)
        second_response.will_close = False
        connection = Mock(sock=Mock())
        connection.getresponse.side_effect = [first_response, second_response]
        connection.request = Mock()
        connection.close = Mock()
        url = 'http://127.0.0.1:1234/v1/statement/backend/1'
        qs._registered(self.pin, url, ('GET',), 'backend')
        with patch.object(qs.http.client, 'HTTPConnection', return_value=connection):
            self.transport(url, 'GET', self.pin)
            self.assertIs(self.pin.transport, connection)
            self.transport(url, 'GET', self.pin)
        self.assertEqual(connection.request.call_count, 2)
        self.assertGreaterEqual(connection.sock.settimeout.call_count, 3)
        # The second call is the pre-request timeout for the retained socket;
        # it must be near the new full-request deadline, not a leftover short
        # body-read timeout from the prior page.
        self.assertGreater(connection.sock.settimeout.call_args_list[1].args[0], 1.0)
        qs._close_query_owner(self.pin)
        self.assertIsNone(self.pin.transport)
        connection.close.assert_called()

    def test_will_close_closes_and_next_get_uses_a_new_connection(self):
        first, second = Mock(sock=Mock()), Mock(sock=Mock())
        first_response = http_response(b'{"id":"one"}', 12)
        first_response.will_close = True
        second_response = http_response(b'{"id":"two"}', 12)
        second_response.will_close = False
        first.getresponse.return_value = first_response
        second.getresponse.return_value = second_response
        first.close, second.close = Mock(), Mock()
        url = 'http://127.0.0.1:1234/v1/statement/backend/1'
        qs._registered(self.pin, url, ('GET',), 'backend')
        with patch.object(qs.http.client, 'HTTPConnection', side_effect=[first, second]) as constructor:
            self.transport(url, 'GET', self.pin)
            self.assertIsNone(self.pin.transport)
            self.transport(url, 'GET', self.pin)
        self.assertEqual(constructor.call_count, 2)
        first.close.assert_called_once()
        self.assertIs(self.pin.transport, second)
        qs._close_query_owner(self.pin)
        second.close.assert_called_once()

    def test_reuse_is_pin_scoped_and_preserves_the_pin_tls_context(self):
        tls_one, tls_two = ssl.create_default_context(), ssl.create_default_context()
        one = qs._Pin(None, 'https://127.0.0.1:1234', {}, tls_one,
                      max_rows=None, full_results=True)
        two = qs._Pin(None, 'https://127.0.0.1:5678', {}, tls_two,
                      max_rows=None, full_results=True)
        one_url = 'https://127.0.0.1:1234/v1/statement/backend/1'
        two_url = 'https://127.0.0.1:5678/v1/statement/backend/1'
        qs._registered(one, one_url, ('GET',), 'backend')
        qs._registered(two, two_url, ('GET',), 'backend')
        first_connection, second_connection = Mock(sock=Mock()), Mock(sock=Mock())
        first_response = http_response(b'{"id":"one"}', 12)
        first_response.will_close = False
        second_response = http_response(b'{"id":"two"}', 12)
        second_response.will_close = False
        first_connection.getresponse.return_value = first_response
        second_connection.getresponse.return_value = second_response
        with patch.object(qs.http.client, 'HTTPSConnection',
                          side_effect=[first_connection, second_connection]) as constructor:
            self.transport(one_url, 'GET', one)
            self.transport(two_url, 'GET', two)
        self.assertIs(constructor.call_args_list[0].kwargs['context'], tls_one)
        self.assertIs(constructor.call_args_list[1].kwargs['context'], tls_two)
        self.assertTrue(tls_one.check_hostname and tls_two.check_hostname)
        self.assertEqual(tls_one.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(tls_two.verify_mode, ssl.CERT_REQUIRED)
        self.assertIs(one.transport, first_connection)
        self.assertIs(two.transport, second_connection)
        self.assertIsNot(one.transport, two.transport)
        qs._close_query_owner(one)
        qs._close_query_owner(two)

    def test_error_on_reused_connection_closes_and_does_not_retry(self):
        response = http_response(b'{"id":"page"}', 13)
        response.will_close = False
        connection = Mock(sock=Mock())
        connection.getresponse.return_value = response
        connection.request = Mock(side_effect=[None, OSError('private write detail')])
        connection.close = Mock()
        url = 'http://127.0.0.1:1234/v1/statement/backend/1'
        qs._registered(self.pin, url, ('GET',), 'backend')
        with patch.object(qs.http.client, 'HTTPConnection', return_value=connection):
            self.transport(url, 'GET', self.pin)
            with self.assertRaises(qs.QueryError) as caught:
                self.transport(url, 'GET', self.pin)
        self.assertEqual(caught.exception.code, 'QUERY_BACKEND_FAILED')
        self.assertEqual(connection.request.call_count, 2)
        self.assertIsNone(self.pin.transport)
        connection.close.assert_called_once()
        self.assertNotIn('private', str(caught.exception))

    def test_closed_execution_closes_retained_transport_without_dispatch(self):
        connection = Mock()
        self.pin.transport = connection
        with patch.object(qs, '_owned', return_value={'execution_closure': 'closed'}), \
                patch.object(qs, '_receive') as receive, \
                patch.object(qs, '_transport') as transport:
            with self.assertRaises(qs.QueryError) as caught:
                qs._fetch_full(self.f.principal, 'query', 'token',
                               self.context.selected_role, self.pin)
        self.assertEqual(caught.exception.code, 'EXECUTION_CLOSED')
        self.assertIsNone(self.pin.transport)
        connection.close.assert_called_once()
        receive.assert_not_called()
        transport.assert_not_called()

    def test_cancel_closed_execution_closes_retained_transport(self):
        connection = Mock()
        self.pin.transport = connection
        with patch.object(qs, '_reconcile_result', return_value={'id': 'query'}):
            result = qs._cancel_locked('query', self.pin, {'execution_closure': 'closed'})
        self.assertEqual(result, {'id': 'query'})
        self.assertIsNone(self.pin.transport)
        connection.close.assert_called_once()

    def test_retirement_only_revocation_closes_retained_transport(self):
        query_id = 'retirement-query'
        connection = Mock()
        self.pin.transport = connection
        row = {'actor': self.f.principal.principal_id,
               'selected_role': self.context.selected_role,
               'backend_username': 'backend', 'database_id': 'database',
               'schema_id': 'schema', 'warehouse_id': 'LOCAL',
               'sql_catalog': 'catalog', 'sql_schema': 'schema',
               'binding_revision': 1, 'generation_id': 'generation',
               'grant_revision': 1}
        qs._pins[query_id] = self.pin
        try:
            with patch.object(qs, '_row', return_value=row), \
                    patch.object(qs.request_auth, '_principal_accounts',
                                 return_value={self.f.principal.principal_id: object()}), \
                    patch.object(qs, 'resolve_context', side_effect=PermissionError), \
                    patch.object(qs, '_retirement_only_denial', return_value=True), \
                    patch.object(qs, '_best_cancel') as best_cancel:
                with self.assertRaises(qs.QueryError) as caught:
                    qs._owned(self.f.principal, query_id,
                              self.context.selected_role, cleanup=True)
        finally:
            qs._pins.pop(query_id, None)
        self.assertEqual(caught.exception.code, 'DB_ACCESS_REVOKED')
        self.assertIsNone(self.pin.transport)
        connection.close.assert_called_once()
        best_cancel.assert_not_called()

    def test_cached_failure_does_not_retry_and_closes_transport(self):
        connection = Mock()
        self.pin.transport = connection
        self.pin.full_next_token = 'token'
        url = 'http://127.0.0.1:1234/v1/statement/backend/1'
        self.pin.tokens = {'token': (url, frozenset({'GET'}))}
        row = {'execution_closure': '', 'state': 'RUNNING'}
        error = qs.QueryError('QUERY_BACKEND_FAILED', 502)
        with patch.object(qs, '_owned', return_value=row), \
                patch.object(qs, '_receive', side_effect=error) as receive:
            with self.assertRaises(qs.QueryError) as first:
                qs._fetch_full(self.f.principal, 'query', 'token',
                               self.context.selected_role, self.pin)
            self.assertIsNone(self.pin.transport)
            with self.assertRaises(qs.QueryError) as second:
                qs._fetch_full(self.f.principal, 'query', 'token',
                               self.context.selected_role, self.pin)
        self.assertEqual((first.exception.code, second.exception.code),
                         ('QUERY_BACKEND_FAILED', 'QUERY_BACKEND_FAILED'))
        self.assertEqual(receive.call_count, 1)
        self.assertIsNone(self.pin.transport)
        connection.close.assert_called_once()

    def test_transport_early_admission_rejection_closes_retained_socket(self):
        connection = Mock()
        self.pin.transport = connection
        self.pin.admission = Mock()
        url = 'http://127.0.0.1:1234/v1/statement/backend/1'
        qs._registered(self.pin, url, ('GET', 'DELETE'), 'backend')
        with patch.object(qs.admission, '_row',
                          return_value={'execution_closure': 'closed'}):
            with self.assertRaises(qs.QueryError) as caught:
                self.transport(url, 'DELETE', self.pin)
        self.assertEqual(caught.exception.code, 'EXECUTION_CLOSED')
        self.assertIsNone(self.pin.transport)
        connection.close.assert_called_once()

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
