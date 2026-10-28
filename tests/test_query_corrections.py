"""Bounded P2-3 review regressions; no real profiles or transports."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest
from unittest.mock import patch

import test_query_service as existing
import control_store
import data_management as dm
import query_service as qs
import query_context as qc
import request_auth
import warehouse_resident as wr


class QueryCorrections(unittest.TestCase):
    setUp = existing.QueryExecutionTests.setUp
    context = existing.QueryExecutionTests.context

    @contextmanager
    def backend(self, transport, context=None):
        context = context or self.context()
        decision = request_auth.authorize_database(self.principal, context.database_id,
            context.selected_role, expected_generation_id=context.generation_id)
        @contextmanager
        def forward():
            yield 'https://trusted:8443'
        with patch('runtime_profile.configure_process') as configure, \
                patch('runtime_profile._current', None), \
                patch('trino_auth.prepare_backend_v2', return_value=(decision,
                    {'X-Trino-User': 'scoped', 'Authorization': 'Basic fixture'})), \
                patch('trino_auth.backend_ssl_context', return_value=None), \
                patch('trino_auth.validate_backend_url'), \
                patch.object(wr, 'persistent_trino', forward), \
                patch.object(qs, '_transport', side_effect=transport):
            yield context
            configure.assert_not_called()

    @contextmanager
    def denial_backend(self, context):
        decision = request_auth.authorize_database(self.principal, context.database_id,
            context.selected_role, expected_generation_id=context.generation_id)
        self.assertIs(decision.platform_admin, False)
        def counts():
            with control_store.connect() as db:
                return {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                        for table in ('query_records', 'warehouse_admissions')}
        before = counts()
        with patch('trino_auth.prepare_backend_v2', return_value=(decision,
                {'X-Trino-User': 'scoped', 'Authorization': 'Basic fixture'})) as prepare:
            yield prepare
            prepare.assert_called_with(principal=self.principal,
                database_id=context.database_id, selected_role=context.selected_role)
        for table, after in counts().items():
            self.assertEqual(after - before[table], 0, table + ' changed after SQL denial')

    def submit(self, context, maximum=100):
        return qs.submit(self.principal, context, 'SELECT * FROM events', mode='read-only',
            max_rows=maximum, request_id='request', worksheet_id='worksheet')

    def running(self):
        return {'id': 'q', 'nextUri': 'https://trusted:8443/v1/statement/executing/q/0/a?token=issued',
                'stats': {'state': 'RUNNING'}, 'columns': [{'name': 'x'}]}

    def test_same_token_concurrent_cache_is_defensive_and_reauthorizes(self):
        calls = []
        def transport(url, method, pin, body=None):
            calls.append(method)
            if method == 'POST':
                return self.running()
            if method == 'GET':
                time.sleep(0.03)
                return dict(self.running(), data=[[1]])
            return {}
        with self.backend(transport) as context:
            first = self.submit(context)
            token = first['next_uri'].rsplit('/', 1)[1]
            def fetch(_):
                return qs.fetch(self.principal, first['id'], token, selected_role='bi_viewer')
            with ThreadPoolExecutor(max_workers=4) as pool:
                pages = list(pool.map(fetch, range(4)))
            self.assertEqual(calls.count('GET'), 1)
            self.assertEqual(qs._pins[first['id']].rows, 1)
            pages[0]['data'][0][0] = 'changed'
            pages[0]['columns'][0]['name'] = 'changed'
            self.assertEqual(fetch(0)['data'], [[1]])
            self.assertEqual(fetch(0)['columns'], [{'name': 'x'}])
            with control_store.connect() as db:
                db.execute("UPDATE grants SET maximum_role='revoked' WHERE principal_id='alice'")
            with self.assertRaises(qs.QueryError) as error:
                fetch(0)
            self.assertEqual(error.exception.code, 'DB_ACCESS_REVOKED')
            self.assertEqual(calls[-1], 'DELETE')

    def test_other_entry_process_never_changes_shared_states_and_history_has_sql(self):
        with self.backend(lambda *a: self.running()) as context:
            for state in ('RUNNING', 'FINISHED', 'FAILED', 'CANCELED'):
                first = self.submit(context)
                token = first['next_uri'].rsplit('/', 1)[1]
                qs._state(first['id'], state)
                with patch.object(qs, '_pins', {}):
                    item = qs.inspect_query(self.principal, first['id'], selected_role='bi_viewer')
                    self.assertEqual(item['state'], state)
                    self.assertEqual(item['sql'], 'SELECT * FROM events')
                    self.assertEqual(item['mode'], 'read-only')
                    self.assertTrue(item['created_at'])
                    self.assertEqual(qs.history(self.principal, database_id=context.database_id,
                        selected_role='bi_viewer')['queries'][0], item)
                    self.assertEqual(qs.history(self.principal, database_id=context.database_id,
                        selected_role='data_engineer'), {'queries': []})
                    for call in (lambda: qs.fetch(self.principal, first['id'], token, selected_role='bi_viewer'),
                                 lambda: qs.cancel(self.principal, first['id'], selected_role='bi_viewer')):
                        with self.assertRaises(qs.QueryError) as error:
                            call()
                        self.assertEqual(error.exception.code, 'QUERY_CONTEXT_LOST')
                    self.assertEqual(qs._row(first['id'])['state'], state)

    def test_cancel_ack_failure_missing_uri_and_terminal_states(self):
        calls = []
        failure = [False]
        def transport(url, method, pin, body=None):
            calls.append(method)
            if method == 'POST':
                return self.running()
            if failure[0]:
                raise qs.QueryError('QUERY_BACKEND_FAILED', 502)
            return {}
        with self.backend(transport) as context:
            first = self.submit(context)
            failure[0] = True
            with self.assertRaises(qs.QueryError):
                qs.cancel(self.principal, first['id'], selected_role='bi_viewer')
            self.assertEqual(qs._row(first['id'])['state'], 'UNKNOWN')
            failure[0] = False
            self.assertEqual(qs.cancel(self.principal, first['id'], selected_role='bi_viewer')['state'], 'CANCELED')
            for state in ('FINISHED', 'FAILED', 'CANCELED'):
                first = self.submit(context)
                qs._state(first['id'], state)
                count = len(calls)
                self.assertEqual(qs.cancel(self.principal, first['id'], selected_role='bi_viewer')['state'], state)
                self.assertEqual(len(calls), count)
            first = self.submit(context)
            qs._pins[first['id']].cancel_urls.clear()
            self.assertEqual(qs.cancel(self.principal, first['id'], selected_role='bi_viewer')['state'], 'UNKNOWN')

    def test_none_transfer_and_numeric_truncation_require_delete_ack(self):
        failure = [False]
        def transport(url, method, pin, body=None):
            if method == 'DELETE':
                if failure[0]:
                    raise qs.QueryError('QUERY_BACKEND_FAILED', 502)
                return {}
            return dict(self.running(), data=[[1], [2], [3]])
        with self.backend(transport) as context:
            unlimited = self.submit(context, None)
            self.assertEqual(unlimited['data'], [[1], [2], [3]])
            self.assertEqual(unlimited['state'], 'RUNNING')
            for failed, expected in ((False, 'CANCELED'), (True, 'UNKNOWN')):
                failure[0] = failed
                page = self.submit(context, 2)
                self.assertTrue(page['truncated'])
                self.assertEqual(page['state'], expected)
                self.assertEqual(page['data'], [[1], [2]])
                self.assertIsNone(page['next_uri'])

    def test_malformed_post_and_fetch_responses_are_typed_unknown(self):
        malformed = [[], {}, {'id': []}, {'id': 'q', 'stats': []},
            {'id': 'q', 'nextUri': 'https://evil/v1/statement/q/a'},
            {'id': 'q', 'nextUri': 'https://trusted:bad/v1/statement/q/a'},
            {'id': 'q', 'data': object()}]
        response = [None]
        calls = []
        def transport(url, method, pin, body=None):
            calls.append(method)
            return response[0]
        with self.backend(transport) as context:
            for bad in malformed:
                response[0] = bad
                with self.assertRaises(qs.QueryError):
                    self.submit(context)
                with control_store.connect() as db:
                    self.assertEqual(db.execute('SELECT state FROM query_records ORDER BY rowid DESC LIMIT 1').fetchone()[0], 'UNKNOWN')
            for bad in malformed:
                response[0] = self.running()
                first = self.submit(context)
                response[0] = bad
                count = calls.count('POST')
                with self.assertRaises(qs.QueryError):
                    qs.fetch(self.principal, first['id'], first['next_uri'].rsplit('/', 1)[1], selected_role='bi_viewer')
                self.assertEqual(qs._row(first['id'])['state'], 'UNKNOWN')
                self.assertEqual(calls.count('POST'), count)

    def test_schema_purpose_must_match_profile(self):
        for role, name in (('bi_viewer', 'marts'), ('data_engineer', 'raw')):
            with control_store.connect() as db:
                db.execute('UPDATE schemas SET purpose=? WHERE id=?', ('custom', self.schemas[name]))
            with self.assertRaises(qc.QueryError) as error:
                self.context(name, role)
            self.assertEqual(error.exception.code, 'SQL_PERMISSION_DENIED')

    def test_native_optional_role_uses_original_role_on_continuation(self):
        headers = self.fixture.headers()
        for key, value in {'X-Trino-Catalog': 'sales', 'X-Trino-Schema': 'marts', 'X-Trino-User': 'alice'}.items():
            headers[key] = value
        with self.backend(lambda *a: self.running() if a[1] == 'POST' else {}) as context:
            # Explicit downgrade first, then omit role after account maximum changes.
            headers['X-Shirokuma-Role'] = 'bi_viewer'
            page = wr._native_request(headers, '/v1/statement', 'POST', b'SELECT 1', 'https://public', warehouse_id='w')
            path = page['nextUri'].replace('https://public', '')
            del headers['X-Shirokuma-Role']
            with patch.object(qs, 'fetch', return_value=dict(id='app', backend_query_id='q',
                    columns=[], data=[], stats={}, state='FINISHED', next_uri=None, cancel_uri='')) as fetch:
                wr._native_request(headers, path, 'GET', b'', 'https://public')
                self.assertEqual(fetch.call_args.kwargs['selected_role'], 'bi_viewer')
            with patch.object(qs, 'submit', return_value={'backend_query_id': 'q', 'columns': [],
                    'data': [], 'stats': {}, 'state': 'FINISHED', 'next_uri': None, 'cancel_uri': ''}) as submit:
                wr._native_request(headers, '/v1/statement', 'POST', b'SELECT 1', 'https://public', warehouse_id='w')
                self.assertEqual(submit.call_args.args[1].selected_role, 'data_engineer')
                self.assertIsNone(submit.call_args.kwargs['max_rows'])

    def test_native_saved_role_after_maximum_loss_and_owner_cancel(self):
        calls = []
        def transport(url, method, pin, body=None):
            calls.append((method, dict(pin.headers)))
            return self.running() if method == 'POST' else {}
        with self.backend(transport, self.context(role='data_engineer')) as context:
            first, second = self.submit(context), self.submit(context)
            self.fixture.users['alice']['role'] = 'bi_viewer'
            self.fixture.save()
            headers = self.fixture.headers()
            path = '/v1/statement/' + first['id'] + '/pages/' + first['next_uri'].rsplit('/', 1)[1]
            with self.assertRaises(qs.QueryError) as error:
                wr._native_request(headers, path, 'GET', b'', 'https://public')
            self.assertEqual(error.exception.code, 'DB_ACCESS_REVOKED')
            self.assertEqual(calls[-1][0], 'DELETE')
            path = '/v1/statement/' + second['id'] + '/cancel'
            self.assertEqual(wr._native_request(headers, path, 'DELETE', b'', 'https://public')['state'], 'CANCELED')
            self.assertEqual(calls[-1][1]['Authorization'], 'Basic fixture')

    def test_fetch_cancel_serialize_and_failed_token_never_refetches(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def transport(url, method, pin, body=None):
            calls.append(method)
            if method == 'POST':
                return self.running()
            if method == 'GET':
                entered.set()
                if not release.wait(2):
                    raise AssertionError('test did not release fetch')
                return dict(self.running(), data=[[1]])
            return {}
        with self.backend(transport) as context:
            page = self.submit(context)
            token = page['next_uri'].rsplit('/', 1)[1]
            with ThreadPoolExecutor(max_workers=2) as pool:
                fetch = pool.submit(qs.fetch, self.principal, page['id'], token, selected_role='bi_viewer')
                self.assertTrue(entered.wait(2))
                cancel = pool.submit(qs.cancel, self.principal, page['id'], selected_role='bi_viewer')
                self.assertFalse(cancel.done())
                release.set()
                self.assertEqual(fetch.result()['data'], [[1]])
                self.assertEqual(cancel.result()['state'], 'CANCELED')
            cached = qs.fetch(self.principal, page['id'], token, selected_role='bi_viewer')
            self.assertEqual(cached['state'], 'CANCELED')
            self.assertIsNone(cached['next_uri'])
            self.assertEqual(calls.count('GET'), 1)
            another = self.submit(context)
            token = another['next_uri'].rsplit('/', 1)[1]
            with patch.object(qs, '_transport', return_value=[]) as malformed:
                for _ in range(2):
                    with self.assertRaises(qs.QueryError):
                        qs.fetch(self.principal, another['id'], token, selected_role='bi_viewer')
                self.assertEqual(malformed.call_count, 1)

    def test_v2_bind_and_readiness_never_use_legacy_credentials(self):
        import json
        import os
        import trino_auth
        config = {'version': 2, 'platform_admin': {'username': 'shirokuma_admin', 'password': 'fixture-admin'},
            'databases': {self.fixture.sales: {'sql_catalog': 'sales', 'generation_id': 'g',
                'roles': {role: {'username': trino_auth.scoped_username(self.fixture.sales, role),
                                'password': 'fixture-' + role} for role in ('bi_viewer', 'data_engineer')}}}}
        path = self.fixture.root / 'v2-backend.json'
        path.write_text(json.dumps(config))
        path.chmod(0o600)
        with patch.dict(os.environ, SHIROKUMA_TRINO_AUTH_MODE='password', SHIROKUMA_TRINO_AUTH_FILE=str(path)), \
                patch('runtime_profile.configure_process') as configure, patch('runtime_profile._current', None), \
                patch('trino_auth.load_credentials', side_effect=AssertionError('v1 loader called')) as legacy, \
                patch('trino_auth.backend_ssl_context', return_value='fixture-tls'), \
                patch.object(wr.socket, 'socket') as socket, patch.object(wr.subprocess, 'Popen') as process, \
                patch.object(wr, 'http_json', return_value={'starting': False}) as info, \
                patch.object(wr, 'trino_http_json', side_effect=AssertionError('legacy wrapper called')), \
                patch.object(wr, 'wait_for', side_effect=lambda check: self.assertTrue(check())):
            socket.return_value.__enter__.return_value.getsockname.return_value = ('127.0.0.1', 8123)
            process.return_value.poll.return_value = None
            request_auth.validate_bind('127.0.0.1')
            with wr._trino_connection():
                pass
            info.assert_called_once_with('https://127.0.0.1:8123/v1/info', context='fixture-tls')
            legacy.assert_not_called()
            configure.assert_not_called()
            self.fixture.users['alice']['platform_admin'] = 'invalid'
            self.fixture.save()
            with self.assertRaises(request_auth.AuthenticationError):
                request_auth.validate_bind('127.0.0.1')

    def test_backend_transport_uses_activity_gate_and_releases_on_failure(self):
        events, active = [], [False]
        @contextmanager
        def gate():
            self.assertFalse(active[0])
            active[0] = True
            events.append('enter')
            try:
                yield
            finally:
                active[0] = False
                events.append('exit')
        def touch():
            self.assertTrue(active[0])
            events.append('touch')
        pin = qs._Pin(self.context(), 'https://trusted:8443', {}, None)
        issued = 'https://trusted:8443/v1/statement/executing/q/0/a'
        qs._registered(pin, issued, ('GET', 'DELETE'), 'q')
        for method in ('POST', 'GET', 'DELETE'):
            for failure in (None, 'request', 'response'):
                with self.subTest(method=method, failure=failure):
                    events.clear()
                    def request(*args, **kwargs):
                        self.assertTrue(active[0])
                        events.append('request')
                        if failure == 'request':
                            raise OSError('fixture failure')
                    def close():
                        self.assertTrue(active[0])
                        events.append('close')
                    with patch.object(wr, '_warehouse_gate', side_effect=gate), \
                            patch.object(wr, 'touch_activity', side_effect=touch), \
                            patch.object(qs.http.client, 'HTTPSConnection') as connection:
                        connection.return_value.request.side_effect = request
                        connection.return_value.close.side_effect = close
                        response = connection.return_value.getresponse.return_value
                        response.status = 500 if failure == 'response' else 200
                        response.read.return_value = b'{}'
                        url = pin.origin + '/v1/statement' if method == 'POST' else issued
                        if failure:
                            with self.assertRaises(qs.QueryError):
                                qs._transport(url, method, pin, b'SELECT 1' if method == 'POST' else None)
                        else:
                            qs._transport(url, method, pin, b'SELECT 1' if method == 'POST' else None)
                    self.assertFalse(active[0])
                    self.assertEqual(events, ['enter', 'touch', 'request', 'close', 'touch', 'exit'])

    def test_cached_page_does_not_acquire_backend_activity_gate(self):
        import json
        actual_transport = qs._transport
        @contextmanager
        def gate():
            yield
        with self.backend(actual_transport) as context, \
                patch.object(wr, 'warehouse_activity', side_effect=gate) as activity, \
                patch.object(qs.http.client, 'HTTPSConnection') as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 200
            response.read.side_effect = [json.dumps(self.running()).encode(),
                json.dumps(dict(self.running(), data=[[1]])).encode()]
            page = self.submit(context)
            token = page['next_uri'].rsplit('/', 1)[1]
            for _ in range(2):
                self.assertEqual(qs.fetch(self.principal, page['id'], token,
                    selected_role='bi_viewer')['data'], [[1]])
            self.assertEqual(activity.call_count, 2)  # POST and one GET, never the cached return.

    def test_referenced_schema_profiles_include_registration_and_purpose(self):
        context = self.context('raw', 'data_engineer')
        for name in ('raw', 'sandbox', 'staging', 'marts'):
            sql = 'SELECT * FROM ' + name + '.t'
            self.assertEqual(wr.check_sql_policy(sql, 'sandbox-write', 'data_engineer', context=context), sql)
        for name in ('raw', 'sandbox'):
            sql = 'INSERT INTO ' + name + '.t VALUES (1)'
            self.assertEqual(wr.check_sql_policy(sql, 'sandbox-write', 'data_engineer', context=context), sql)
        for name in ('staging', 'marts'):
            with self.assertRaises(ValueError):
                wr.check_sql_policy('INSERT INTO ' + name + '.t VALUES (1)',
                    'sandbox-write', 'data_engineer', context=context)
        with control_store.connect() as db:
            db.execute('UPDATE schemas SET purpose=? WHERE id=?', ('staging', self.schemas['sandbox']))
        for missing in (False, True):
            if missing:
                with control_store.connect() as db:
                    db.execute('DELETE FROM schemas WHERE id=?', (self.schemas['sandbox'],))
            with self.denial_backend(context) as prepare, patch.object(qs, '_transport') as transport:
                for name in ('sandbox.t', 'sales.sandbox.t'):
                    for sql in ('SELECT * FROM ' + name, 'INSERT INTO ' + name + ' VALUES (1)'):
                        with self.subTest(missing=missing, sql=sql), self.assertRaises(qs.QueryError) as error:
                            qs.submit(self.principal, context, sql, mode='sandbox-write', max_rows=2,
                                request_id=None, worksheet_id=None)
                        self.assertEqual(error.exception.code, 'SQL_PERMISSION_DENIED')
                self.assertEqual(prepare.call_count, 4)
                transport.assert_not_called()

    def test_terminal_service_page_truncates_without_cancel_or_state_loss(self):
        for state in ('FINISHED', 'FAILED'):
            for later in (False, True):
                with self.subTest(state=state, later=later):
                    calls = []
                    def transport(url, method, pin, body=None):
                        calls.append(method)
                        if method == 'DELETE':
                            raise AssertionError('terminal query must not be canceled')
                        if later and method == 'POST':
                            return dict(self.running(), data=[[0]])
                        page = {'id': 'q', 'data': [[1], [2], [3]], 'stats': {'state': state}}
                        if state == 'FAILED':
                            page['error'] = {'message': 'fixture failure'}
                        return page
                    with self.backend(transport) as context:
                        page = self.submit(context, 2)
                        if later:
                            page = qs.fetch(self.principal, page['id'], page['next_uri'].rsplit('/', 1)[1],
                                selected_role='bi_viewer')
                        self.assertTrue(page['truncated'])
                        self.assertEqual(page['state'], state)
                        self.assertEqual(qs._row(page['id'])['state'], state)
                        self.assertEqual(len(page['data']), 1 if later else 2)
                        self.assertNotIn('DELETE', calls)

    def test_terminal_cli_page_truncates_without_cancel_or_state_loss(self):
        import io
        import json
        from types import SimpleNamespace
        from unittest.mock import Mock
        path = self.fixture.root / 'terminal-client.json'
        path.write_text(json.dumps({'username': 'alice', 'password': 'correct', 'selected_role': 'bi_viewer'}))
        path.chmod(0o600)
        profile = SimpleNamespace(client_auth_file=str(path), sql_endpoint_url='https://public')
        for state in ('FINISHED', 'FAILED'):
            for later in (False, True):
                with self.subTest(state=state, later=later):
                    methods = []
                    def send(request, **kwargs):
                        methods.append(request.get_method())
                        if later and request.get_method() == 'POST':
                            page = {'id': 'q', 'data': [[0]], 'stats': {'state': 'RUNNING'},
                                'nextUri': 'https://public/v1/statement/app/pages/token'}
                        else:
                            page = {'id': 'q', 'data': [[1], [2], [3]], 'stats': {'state': state}}
                        return io.BytesIO(json.dumps(page).encode())
                    with patch('runtime_profile.current', return_value=profile), \
                            patch.object(wr.urllib.request, 'build_opener', return_value=Mock(open=send)):
                        result = wr._profile_client_query('SELECT 1', 'sales', 'marts', 2)
                    self.assertTrue(result['truncated'])
                    self.assertEqual(result['state'], state)
                    self.assertEqual(len(result['rows']), 2)
                    self.assertEqual(methods, ['POST', 'GET'] if later else ['POST'])

    def test_show_create_table_view_read_only_native_targets_unchanged(self):
        calls = []
        def transport(url, method, pin, body=None):
            calls.append(body)
            return {'id': 'q', 'data': [['DDL']], 'stats': {'state': 'FINISHED'}}
        context = self.context('raw', 'data_engineer')
        with self.backend(transport, context):
            for kind in ('TABLE', 'VIEW'):
                for target in ('t', 'raw.t', 'sales.raw.t', '"sales" . "raw" . "t"'):
                    sql = '/* inspect only */ SHOW CREATE ' + kind + ' ' + target + ';\n'
                    with self.subTest(kind=kind, target=target):
                        self.assertEqual(wr.check_sql_policy(sql, 'read-only', 'data_engineer', context=context), sql)
                        page = qs.submit(self.principal, context, sql, mode='read-only', max_rows=2,
                            request_id=None, worksheet_id=None)
                        self.assertEqual(page['data'], [['DDL']])
                        expected_wire = '/* inspect only */ SHOW CREATE ' + kind + ' ' + target + '\n'
                        self.assertEqual(calls[-1], expected_wire.encode())
                        self.assertEqual(qs._row(page['id'])['sql'], sql)

    def test_show_create_table_view_rejects_context_violations_and_mutation(self):
        context = self.context('raw', 'data_engineer')
        with control_store.connect() as db:
            dm.create_schema(db, self.fixture.finance, 'raw', 'raw', 'raw', 'test')
            db.execute('UPDATE schemas SET purpose=? WHERE id=?', ('staging', self.schemas['sandbox']))
            db.execute('DELETE FROM schemas WHERE id=?', (self.schemas['staging'],))
        with self.denial_backend(context) as prepare, patch.object(qs, '_transport') as transport:
            for kind in ('TABLE', 'VIEW'):
                for target in ('finance.raw.t', 'sandbox.t', 'sales.sandbox.t', 'staging.t', 'missing.t'):
                    with self.subTest(kind=kind, target=target), self.assertRaises(qs.QueryError) as error:
                        qs.submit(self.principal, context, 'SHOW CREATE ' + kind + ' ' + target,
                            mode='read-only', max_rows=2, request_id=None, worksheet_id=None)
                    self.assertEqual(error.exception.code, 'SQL_PERMISSION_DENIED')
            for sql in ('CREATE TABLE raw.t (x integer)', 'CREATE VIEW raw.v AS SELECT 1',
                        'SHOW CREATE TABLE raw.t; SELECT 1', 'SHOW CREATE VIEW raw.t; DROP TABLE raw.t',
                        'SHOW CREATE TABLE raw.t AS SELECT 1', 'SHOW CREATE VIEW raw.t AS SELECT 1',
                        'SHOW CREATE SCHEMA raw', 'SHOW CREATE TABLE sales.raw.t.extra'):
                with self.subTest(sql=sql), self.assertRaises(qs.QueryError) as error:
                    qs.submit(self.principal, context, sql, mode='read-only', max_rows=2,
                        request_id=None, worksheet_id=None)
                self.assertEqual(error.exception.code, 'SQL_PERMISSION_DENIED')
            self.assertEqual(prepare.call_count, 18)
            transport.assert_not_called()

    def test_standard_unnest_preserves_representative_sql_and_existing_forms(self):
        representative = "SELECT x, repeat('x',1024) AS payload FROM UNNEST(sequence(1,3000)) t(x)"
        self.assertEqual(wr.check_sql_policy(representative, 'read-only', 'data_engineer'), representative)
        context = self.context('raw', 'data_engineer')
        calls = []
        def transport(url, method, pin, body=None):
            calls.append(body)
            return {'id': 'q', 'data': [[1]], 'stats': {'state': 'FINISHED'}}
        with self.backend(transport, context):
            for sql in (representative,
                        'SELECT * FROM unnest(sequence(1,3)) t(x), raw.t',
                        'SELECT * FROM UNNEST((SELECT array_agg(x) FROM raw.t)) t(x)',
                        'SELECT CASE WHEN x=1 THEN 2 ELSE 3 END FROM raw.t',
                        'SELECT * FROM (VALUES (1), (2)) t(x)',
                        'SELECT * FROM (SELECT * FROM raw.t) t'):
                with self.subTest(sql=sql):
                    self.assertEqual(wr.check_sql_policy(sql, 'read-only', 'data_engineer', context=context), sql)
                    qs.submit(self.principal, context, sql, mode='read-only', max_rows=None,
                        request_id=None, worksheet_id=None)
                    self.assertEqual(calls[-1], sql.encode())

    def test_unnest_exception_rejects_other_table_function_forms(self):
        context = self.context('raw', 'data_engineer')
        with self.denial_backend(context) as prepare, patch.object(qs, '_transport') as transport:
            for relation in ("TABLE(system.query(query => 'SELECT 1'))", 'foo(sequence(1,3))',
                             'namespace.unnest(sequence(1,3))', 'sales.raw.unnest(sequence(1,3))',
                             '"unnest"(sequence(1,3))', '"UNNEST"(sequence(1,3))'):
                with self.subTest(relation=relation), self.assertRaises(qs.QueryError) as error:
                    qs.submit(self.principal, context, 'SELECT * FROM ' + relation,
                        mode='read-only', max_rows=2, request_id=None, worksheet_id=None)
                self.assertEqual(error.exception.code, 'SQL_PERMISSION_DENIED')
            self.assertEqual(prepare.call_count, 6)
            transport.assert_not_called()

    def test_unnest_arguments_keep_nested_schema_authorization(self):
        context = self.context('raw', 'data_engineer')
        with control_store.connect() as db:
            dm.create_schema(db, self.fixture.finance, 'raw', 'raw', 'raw', 'test')
            db.execute('UPDATE schemas SET purpose=? WHERE id=?', ('staging', self.schemas['sandbox']))
            db.execute('DELETE FROM schemas WHERE id=?', (self.schemas['staging'],))
        with self.denial_backend(context) as prepare, patch.object(qs, '_transport') as transport:
            for source in ('finance.raw.t', 'sandbox.t', 'sales.sandbox.t', 'staging.t', 'missing.t'):
                for sql in ('SELECT * FROM UNNEST((SELECT array_agg(x) FROM ' + source + ')) t(x)',
                            'SELECT * FROM UNNEST(sequence(1,3)) t(x), ' + source):
                    with self.subTest(sql=sql), self.assertRaises(qs.QueryError) as error:
                        qs.submit(self.principal, context, sql, mode='read-only', max_rows=2,
                            request_id=None, worksheet_id=None)
                    self.assertEqual(error.exception.code, 'SQL_PERMISSION_DENIED')
            self.assertEqual(prepare.call_count, 10)
            transport.assert_not_called()

    def test_cli_truncation_uses_full_issued_uri_and_acknowledgment(self):
        import io
        import json
        from types import SimpleNamespace
        from unittest.mock import Mock
        path = self.fixture.root / 'client.json'
        path.write_text(json.dumps({'username': 'alice', 'password': 'correct', 'selected_role': 'bi_viewer'}))
        path.chmod(0o600)
        profile = SimpleNamespace(client_auth_file=str(path), sql_endpoint_url='https://public')
        next_uri = 'https://public/v1/statement/app/pages/token'
        for acknowledgment, expected in (({'state': 'CANCELED'}, 'CANCELED'), ({'state': 'UNKNOWN'}, 'UNKNOWN')):
            requests = []
            def send(request, **kwargs):
                requests.append(request)
                page = acknowledgment if request.get_method() == 'DELETE' else {
                    'id': 'q', 'data': [[1], [2], [3]], 'nextUri': next_uri,
                    'partialCancelUri': 'https://evil/v1/statement/other/cancel', 'stats': {'state': 'RUNNING'}}
                return io.BytesIO(json.dumps(page).encode())
            with patch('runtime_profile.current', return_value=profile), \
                    patch.object(wr.urllib.request, 'build_opener', return_value=Mock(open=send)):
                result = wr._profile_client_query('SELECT 1', 'sales', 'marts', 2)
            self.assertTrue(result['truncated'])
            self.assertEqual(result['state'], expected)
            self.assertEqual(result['rows'], [[1], [2]])
            self.assertEqual([request.get_method() for request in requests], ['POST', 'DELETE'])
            self.assertEqual(requests[-1].full_url, next_uri)


if __name__ == '__main__':
    unittest.main()
