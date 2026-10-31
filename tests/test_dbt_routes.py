"""Strict normal HTTP routing, without compile or SQL execution."""
from contextlib import ExitStack
from dataclasses import asdict
from email.message import Message
from http.client import HTTPResponse
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import dbt_routes as routes
import workbench_server as wb
import warehouse_resident as wh
import request_auth
import runtime_profile
from query_context import ResolvedQueryContext, QueryError

C = ResolvedQueryContext('db', 'sc', 'LOCAL', 'sales', 'sandbox', 'data_engineer', 1, 'gen', 3)
ID = '11111111-1111-1111-1111-111111111111'
BASE = '/api/v1/dbt/'


class Routes(unittest.TestCase):
    def handler(self, path, body=None, headers=None):
        h = SimpleNamespace(path=path, headers=Message(), database_body=Mock(return_value=body), respond=Mock())
        for k, v in (headers or {}).items():
            for item in v if isinstance(v, list) else [v]:
                h.headers[k] = item
        return h

    def test_all_locked_calls(self):
        cases = [
            ('GET', 'projects', None, 'list_projects', 'projects', 200),
            ('GET', 'plans', None, 'list_plans', 'items', 200),
            ('GET', 'plans/' + ID, None, 'get_plan', 'plan', 200),
            ('GET', 'runs', None, 'list_runs', 'items', 200),
            ('GET', 'runs/' + ID, None, 'get_run', 'run', 200),
            ('GET', 'publications/' + ID, None, 'get_publication', 'publication', 200),
            ('GET', 'publications', None, 'list_publications', 'publications', 200),
            ('POST', 'plans', dict(project_id='lake_marts', source_schema='raw', target_schema='marts', staging_materialization=None), 'submit_plan', 'plan', 202),
            ('POST', 'runs', dict(plan_id=ID), 'submit_run', 'run', 202),
            ('POST', 'runs/' + ID + '/cancel', {}, 'cancel_run', 'run', 200),
            ('POST', 'runs/' + ID + '/reconcile', {}, 'reconcile_run', 'run', 200),
            ('POST', 'runs/' + ID + '/publish', dict(node_id='model.project.orders'), 'submit_publication', 'publication', 202)]
        for method, path, body, service, field, status in cases:
            with self.subTest(service=service), patch.object(routes.dbt, service, return_value={'safe': True}) as call, patch.object(routes.dbt, 'get_run_options', return_value={'version': 1}) as options, patch.object(routes.dbt, 'get_graph_options', return_value={'executor': 'native_graph'}, create=True) as graph:
                params = {'context': [json.dumps(asdict(C))]} if method == 'GET' else {}
                h = self.handler(BASE + path, dict(context=asdict(C), **(body or {})),
                    {'Content-Type': 'application/json', 'Idempotency-Key': 'original'})
                self.assertTrue(routes.dispatch(h, method, BASE + path, params, 'actor', C.selected_role))
                expected = dict(context=asdict(C), **{field: {'safe': True}})
                if method == 'GET' and service == 'get_plan':
                    expected['run_options'] = {'version': 1}
                    expected['graph_options'] = {'executor': 'native_graph'}
                    graph.assert_called_once_with('actor', C, ID)
                    options.assert_called_once_with('actor', C, ID)
                else:
                    options.assert_not_called()
                    graph.assert_not_called()
                h.respond.assert_called_once_with(expected, status)
                self.assertEqual(call.call_args.args[:2], ('actor', C))
                if method == 'POST':
                    self.assertEqual(call.call_args.kwargs['key'], 'original')
        h = self.handler(BASE + 'runs')
        with patch.object(routes.dbt, 'list_runs') as call:
            routes.dispatch(h, 'GET', h.path, dict(context=[json.dumps(asdict(C))], plan_id=[ID]), 'actor', C.selected_role)
            self.assertEqual(call.call_args.kwargs, {'plan_id': ID})

    def test_publication_preview_strict_get_and_descriptor_forwarding(self):
        context = dict(asdict(C), selected_role='admin')
        path = BASE + 'runs/' + ID + '/publication-preview'
        params = dict(context=[json.dumps(context)], node_id=['model.demo.alias'])
        with patch.object(routes.dbt, 'get_publication_preview', return_value={'origin': 'native_table'}) as preview:
            h = self.handler(path)
            routes.dispatch(h, 'GET', path, params, 'publisher', 'admin')
            self.assertEqual(preview.call_args.args[0], 'publisher')
            self.assertEqual(preview.call_args.args[1].selected_role, 'admin')
            self.assertEqual(preview.call_args.kwargs, {'node_id': 'model.demo.alias'})
            h.database_body.assert_not_called()
            h.respond.assert_called_once_with(dict(context=context, preview={'origin': 'native_table'}), 200)
        for bad, headers in [(dict(context=params['context']), {}), (dict(params, node_id=['a', 'b']), {}),
                (dict(params, extra=['x']), {}), (dict(params, node_id=['bad node']), {}),
                (params, {'Content-Length': ['0', '0']}), (params, {'Content-Length': '1'}),
                (params, {'Transfer-Encoding': 'chunked'})]:
            with patch.object(routes.dbt, 'get_publication_preview') as preview:
                with self.assertRaises((ValueError, QueryError)):
                    routes.dispatch(self.handler(path, headers=headers), 'GET', path, bad, 'publisher', 'admin')
                preview.assert_not_called()
        descriptor = dict(version=1, destination={'database_id': 'db', 'schema_id': 'marts', 'physical_name': 'alias'}, target=None)
        for include in (False, True):
            body = dict(context=context, node_id='model.demo.alias')
            if include:
                body['expected_target'] = descriptor
            h = self.handler(BASE + 'runs/' + ID + '/publish', body, {'Content-Type': 'application/json', 'Idempotency-Key': 'key'})
            with patch.object(routes.dbt, 'submit_publication', return_value={}) as publish:
                routes.dispatch(h, 'POST', h.path, {}, 'publisher', 'admin')
                expected = dict(key='key', node_id='model.demo.alias')
                if include:
                    expected['expected_target'] = descriptor
                self.assertEqual(publish.call_args.kwargs, expected)

    def test_reject_before_service(self):
        for path, method, params, body, headers in [
            ('runs/' + ID + '/extra', 'GET', {}, {}, {}),
            ('plans', 'GET', {'context': ['{}', '{}']}, {}, {}),
            ('plans', 'GET', {'context': [json.dumps(asdict(C))], 'sql': ['select 1']}, {}, {}),
            ('plans', 'GET', {'context': [json.dumps(asdict(C))]}, {}, {'Content-Length': '1'}),
            ('runs', 'POST', {}, {'context': asdict(C), 'plan_id': ID, 'actor': 'admin'}, {}),
            ('runs', 'POST', {}, {'context': asdict(C), 'plan_id': ID}, {'Idempotency-Key': ['a', 'b']}),
            ('runs', 'POST', {}, {'context': dict(asdict(C), selected_role='admin'), 'plan_id': ID}, {'Idempotency-Key': 'a'}),
            ('runs', 'POST', {}, {'context': asdict(C), 'plan_id': '../private'}, {'Idempotency-Key': 'a'}),
            ('runs', 'POST', {'x': ['1']}, {'context': asdict(C), 'plan_id': ID}, {'Idempotency-Key': 'a'}),
        ]:
            with self.subTest(path=path, method=method), patch.object(routes.dbt, 'submit_run') as run, patch.object(routes.dbt, 'list_plans') as plans:
                h = self.handler(BASE + path, body, dict({'Content-Type': 'application/json'}, **headers))
                with self.assertRaises((ValueError, QueryError)):
                    routes.dispatch(h, method, h.path, params, 'actor', C.selected_role)
                run.assert_not_called()
                plans.assert_not_called()

    def test_cancel_delegates_current_authority(self):
        h = self.handler(BASE + 'runs/' + ID + '/cancel', {'context': asdict(C)}, {'Content-Type': 'application/json', 'Idempotency-Key': 'k'})
        with patch.object(routes.common, 'current_context', side_effect=AssertionError('must delegate')), patch.object(routes.dbt, 'cancel_run') as cancel:
            routes.dispatch(h, 'POST', h.path, {}, 'actor', C.selected_role)
            cancel.assert_called_once_with('actor', C, ID, key='k')

    def test_both_http_dispatchers_and_legacy_denial(self):
        handlers = [wb.WorkbenchHandler]
        class Capture:
            def __init__(self, address, handler):
                handlers.append(handler)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def handle_request(self): pass
        with ExitStack() as stack:
            stack.enter_context(patch.object(wh, '_profile_active', return_value=True))
            stack.enter_context(patch.object(wh, 'warehouse', return_value='LOCAL'))
            stack.enter_context(patch('http.server.ThreadingHTTPServer', Capture))
            stack.enter_context(patch('logical_creations.enabled', return_value=False))
            stack.enter_context(patch('database_creation_bridge.configure'))
            wh.serve_endpoint(once=True)
        principal = request_auth.AuthenticatedPrincipal('alice', 'admin', False)
        class Connection:
            def __init__(self, incoming): self.incoming, self.outgoing = io.BytesIO(incoming), io.BytesIO()
            def makefile(self, *args): return self.incoming
            def sendall(self, data): self.outgoing.write(data)
        for handler in handlers:
            for role in ('admin', 'data_engineer', 'bi_viewer'):
                context = dict(asdict(C), selected_role=role)
                path = BASE + 'projects?' + urlencode({'context': json.dumps(context)})
                raw = ('GET ' + path + ' HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\nAuthorization: Basic synthetic\r\nX-Shirokuma-Role: ' + role + '\r\n\r\n').encode()
                conn = Connection(raw)
                def service(p, c):
                    if c.selected_role == 'bi_viewer': raise QueryError('SQL_PERMISSION_DENIED', 403)
                    return [{'id': 'lake_marts'}]
                with patch.object(runtime_profile, 'active', return_value=True), patch.object(request_auth, 'authenticate_principal', return_value=principal), patch.object(routes.dbt, 'list_projects', side_effect=service) as call, patch.object(handler, 'log_message'):
                    handler(conn, ('127.0.0.1', 1), SimpleNamespace())
                    response = HTTPResponse(Connection(conn.outgoing.getvalue())); response.begin()
                    reply = json.loads(response.read())
                    self.assertEqual(response.status, 403 if role == 'bi_viewer' else 200, reply)
                    call.assert_called_once()


    def test_http_post_strict_body_headers_origin_and_legacy(self):
        handlers = [wb.WorkbenchHandler]
        class Capture:
            def __init__(self, address, handler): handlers.append(handler)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def handle_request(self): pass
        with patch.object(wh, '_profile_active', return_value=True), patch.object(wh, 'warehouse', return_value='LOCAL'), patch('http.server.ThreadingHTTPServer', Capture), patch('logical_creations.enabled', return_value=False), patch('database_creation_bridge.configure'):
            wh.serve_endpoint(once=True)
        class Connection:
            def __init__(self, incoming): self.incoming, self.outgoing = io.BytesIO(incoming), io.BytesIO()
            def makefile(self, *args): return self.incoming
            def sendall(self, data): self.outgoing.write(data)
        body = json.dumps(dict(context=asdict(C), plan_id=ID))
        graph_body = dict(context=asdict(C), plan_id=ID, executor='native_graph')
        cases = [
            (BASE + 'runs', json.dumps(graph_body), 'Idempotency-Key: graph\r\n', 202),
            (BASE + 'runs', json.dumps(dict(graph_body, native_tests=True)), 'Idempotency-Key: graph\r\n', 202),
            (BASE + 'runs', json.dumps(dict(graph_body, native_tests=1)), 'Idempotency-Key: graph\r\n', 400),
            (BASE + 'runs', json.dumps(dict(graph_body, node_id=None)), 'Idempotency-Key: graph\r\n', 400),
            (BASE + 'runs', body, 'Idempotency-Key: k\r\n', 202),
            (BASE + 'runs', body, '', 400),
            (BASE + 'runs', body, 'Idempotency-Key: a\r\nIdempotency-Key: b\r\n', 400),
            (BASE + 'runs', body[:-1] + ',"plan_id":"' + ID + '"}', 'Idempotency-Key: k\r\n', 400),
            (BASE + 'runs', body[:-1] + ',"sql":"select 1"}', 'Idempotency-Key: k\r\n', 400),
            (BASE + 'runs', body, 'Idempotency-Key: k\r\nOrigin: http://evil.invalid\r\n', 403),
            (BASE + 'runs/' + ID + '/publish/extra', body, 'Idempotency-Key: k\r\n', 404),
            ('/api/dbt/run', body, 'Idempotency-Key: k\r\n', 404),
        ]
        for handler in handlers:
            for path, raw_body, extra, expected in cases:
                with self.subTest(handler=handler.__name__, path=path, expected=expected):
                    raw = ('POST ' + path + ' HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\nAuthorization: Basic synthetic\r\nX-Shirokuma-Role: data_engineer\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(raw_body.encode())) + '\r\n' + extra + '\r\n' + raw_body).encode()
                    conn = Connection(raw)
                    with patch.object(runtime_profile, 'active', return_value=True), patch.object(runtime_profile, 'current', return_value=SimpleNamespace(sql_endpoint_url='http://backend.invalid')), patch.object(request_auth, 'authenticate_principal', return_value=request_auth.AuthenticatedPrincipal('alice', 'data_engineer', False)), patch.object(routes.dbt, 'submit_run', return_value={'id': ID}) as call, patch.object(handler, 'log_message'):
                        handler(conn, ('127.0.0.1', 1), SimpleNamespace())
                        response = HTTPResponse(Connection(conn.outgoing.getvalue())); response.begin()
                        reply = json.loads(response.read())
                        self.assertEqual(response.status, expected, reply)
                        self.assertEqual(call.call_count, int(expected == 202))

    def test_native_quality_optional_strict_bool_and_executor(self):
        base = dict(context=asdict(C), plan_id=ID)
        for executor in ('native_table', 'native_graph', 'sql_wap', None):
            for flag in ('missing', False, True, None, 0, 1, 'true', [], {}):
                body = dict(base, executor=executor)
                if executor is None:
                    del body['executor']
                if executor == 'native_table':
                    body['node_id'] = 'model.arbitrary.table'
                if flag != 'missing':
                    body['native_tests'] = flag
                valid = flag == 'missing' or type(flag) is bool and (not flag or executor in ('native_table', 'native_graph'))
                with self.subTest(executor=executor, flag=flag), patch.object(routes.dbt, 'submit_run', return_value={}) as run:
                    h = self.handler(BASE + 'runs', body, {'Content-Type': 'application/json', 'Idempotency-Key': 'quality'})
                    if valid:
                        routes.dispatch(h, 'POST', h.path, {}, 'actor', C.selected_role)
                        expected = {k: v for k, v in body.items() if k != 'context'}
                        run.assert_called_once_with('actor', C, key='quality', **expected)
                    else:
                        with self.assertRaises(ValueError):
                            routes.dispatch(h, 'POST', h.path, {}, 'actor', C.selected_role)
                        run.assert_not_called()

    def test_sqlwap_false_replays_original_canonical_request(self):
        principal = SimpleNamespace(principal_id='actor')
        receipt = {'id': ID}
        with patch.object(routes.dbt, '_access'), patch.object(routes.dbt.control_store, 'connect'), patch.object(routes.dbt, '_request', return_value=receipt) as request:
            for executor in (None, 'sql_wap'):
                for flag in ('missing', False):
                    body = dict(context=asdict(C), plan_id=ID)
                    if executor is not None:
                        body['executor'] = executor
                    if flag is False:
                        body['native_tests'] = False
                    h = self.handler(BASE + 'runs', body, {'Content-Type': 'application/json', 'Idempotency-Key': 'original'})
                    routes.dispatch(h, 'POST', h.path, {}, principal, C.selected_role)
                    args = request.call_args.args
                    self.assertEqual(args[3:5], (BASE + 'runs', 'original'))
                    self.assertEqual(json.loads(args[5]), dict(context=asdict(C), plan_id=ID))
                    h.respond.assert_called_once_with(dict(context=asdict(C), run=receipt), 202)
            self.assertEqual(len({call.args[5] for call in request.call_args_list}), 1)

    def test_graph_forbids_any_node_id_before_service(self):
        for node in (None, '', 'model.arbitrary.table', 1, [], {}):
            with self.subTest(node=node), patch.object(routes.dbt, 'submit_run') as run:
                body = dict(context=asdict(C), plan_id=ID, executor='native_graph', node_id=node)
                h = self.handler(BASE + 'runs', body, {'Content-Type': 'application/json', 'Idempotency-Key': 'graph'})
                with self.assertRaises(ValueError):
                    routes.dispatch(h, 'POST', h.path, {}, 'actor', C.selected_role)
                run.assert_not_called()

    def test_native_run_strict_optional_fields_before_service(self):
        good = dict(context=asdict(C), plan_id=ID, executor='native_table', node_id='model.arbitrary.table')
        h = self.handler(BASE + 'runs', good, {'Content-Type': 'application/json', 'Idempotency-Key': 'native'})
        with patch.object(routes.dbt, 'submit_run', return_value={}) as run:
            routes.dispatch(h, 'POST', h.path, {}, 'actor', C.selected_role)
            run.assert_called_once_with('actor', C, key='native', plan_id=ID, executor='native_table', node_id='model.arbitrary.table')
        for changes in [dict(executor='unknown'), dict(executor=None), dict(executor=[]), dict(node_id=None),
                dict(node_id=2), dict(node_id=[]), dict(node_id=''), dict(node_id='bad node'), dict(executor='sql_wap')]:
            with self.subTest(changes=changes), patch.object(routes.dbt, 'submit_run') as run:
                h = self.handler(BASE + 'runs', dict(good, **changes), {'Content-Type': 'application/json', 'Idempotency-Key': 'native'})
                with self.assertRaises(ValueError):
                    routes.dispatch(h, 'POST', h.path, {}, 'actor', C.selected_role)
                run.assert_not_called()
        for body in [dict(context=asdict(C), plan_id=ID, executor='native_table'),
                     dict(context=asdict(C), plan_id=ID, node_id='model.arbitrary.table'),
                     dict(context=asdict(C), plan_id=ID, node_id=None)]:
            h = self.handler(BASE + 'runs', body, {'Content-Type': 'application/json', 'Idempotency-Key': 'native'})
            with patch.object(routes.dbt, 'submit_run') as run, self.assertRaises(ValueError):
                routes.dispatch(h, 'POST', h.path, {}, 'actor', C.selected_role)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
