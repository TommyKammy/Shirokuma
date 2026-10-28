"""Strict schedule dispatcher contract, independent of concurrent engine work."""
import base64
import importlib.util
from contextlib import redirect_stdout
import io
import json
from email.message import Message
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
from query_context import QueryError

C = dict(database_id='db', schema_id='sc', warehouse_id='LOCAL', sql_catalog='sales',
         sql_schema='raw', selected_role='admin', binding_revision=2, generation_id='gen', grant_revision=7)
ID = '00000000-0000-0000-0000-000000000001'


class ScheduleRoutes(unittest.TestCase):
    def setUp(self):
        self.backend = SimpleNamespace(**{name: Mock(return_value=[])
            for name in ('save_schedule', 'list_schedules', 'get_schedule', 'list_ticks', 'list_alerts', 'acknowledge_alert')})
        spec = importlib.util.spec_from_file_location('schedule_route_contract', Path(__file__).resolve().parents[1] / 'tools/local_experiment/db_schedule_routes.py')
        self.routes = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, db_schedules=self.backend):
            spec.loader.exec_module(self.routes)
        self.principal = object()
        self.body = dict(context=C, target_kind='job', target_id=ID, target_revision=3,
                         expected_revision=0, spec={'kind': 'interval', 'every_minutes': 60}, intent={'arguments': {'n': 7}})

    def request(self, path='/api/v1/schedules', method='GET', params=None, body=None, headers=(), role='admin'):
        h = Message()
        if method == 'POST':
            h['Content-Type'] = 'application/json'
            h['X-Idempotency-Key'] = 'caller-key'
        for key, value in headers:
            h[key] = value
        handler = SimpleNamespace(path=path, headers=h, database_body=lambda: body or self.body, respond=Mock())
        result = self.routes.dispatch(handler, method, urlsplit(path).path, {'context': [json.dumps(C)]} if params is None and method == 'GET' else params or {}, self.principal, role)
        return result, handler.respond

    def test_routes_and_exact_backend_coordinates(self):
        record = {'id': ID}
        self.backend.save_schedule.return_value = record
        _, response = self.request(method='POST')
        self.assertEqual(response.call_args.args[0], dict(context=C, schedule=record))
        args, kwargs = self.backend.save_schedule.call_args
        self.assertIs(args[0], self.principal)
        self.assertEqual(args[1].public_metadata(), C)
        self.assertEqual(kwargs, dict(key='caller-key', **{k: v for k, v in self.body.items() if k != 'context'}))
        self.request(params=dict(context=[json.dumps(C)], target_kind=['stage'], target_id=[ID]))
        self.assertEqual(self.backend.list_schedules.call_args.kwargs, dict(target_kind='stage', target_id=ID))
        self.request('/api/v1/schedules/' + ID)
        self.assertEqual(self.backend.get_schedule.call_args.args[-1], ID)
        self.request('/api/v1/schedules/' + ID + '/ticks')
        self.assertEqual(self.backend.list_ticks.call_args.args[-1], ID)
        self.backend.list_alerts.return_value = dict(alerts=[], unacknowledged_count=0)
        _, response = self.request('/api/v1/alerts')
        self.assertEqual(response.call_args.args[0], dict(context=C, alerts=[], unacknowledged_count=0))
        self.backend.acknowledge_alert.return_value = record
        _, response = self.request('/api/v1/alerts/' + ID + '/ack', 'POST', body={'context': C})
        self.assertEqual(response.call_args.args[0], dict(context=C, alert=record))
        self.assertEqual(self.backend.acknowledge_alert.call_args.kwargs, {'key': 'caller-key'})

    def test_strict_queries_context_and_framing(self):
        for params in ({}, {'context': ['{}']}, {'context': [json.dumps(C)] * 2},
                       {'context': [json.dumps(C)], 'unknown': ['x']},
                       {'context': [json.dumps(C)], 'target_kind': ['bad']},
                       {'context': [json.dumps(C)], 'target_id': ['bad']},
                       {'context': [json.dumps(C)], 'target_id': ['']}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.request(params=params)
        for key, value in [('Transfer-Encoding', ''), ('Transfer-Encoding', 'chunked'), ('Content-Length', '1')]:
            with self.assertRaises(ValueError):
                self.request(headers=[(key, value)])
        with self.assertRaises(ValueError):
            self.request('/api/v1/schedules#fragment')
        with self.assertRaises(ValueError):
            self.request(role='data_engineer')
        for name in C:
            with self.assertRaises(ValueError):
                self.request(params={'context': [json.dumps({k: v for k, v in C.items() if k != name})]})
        with self.assertRaises(ValueError):
            self.request(params={'context': [json.dumps(C)[:-1] + ',"database_id":"other"}']})

    def test_strict_post_and_unsupported_paths(self):
        for headers in ([('X-Idempotency-Key', 'duplicate')], [('Idempotency-Key', 'ambiguous')],
                        [('Content-Type', 'application/json')], [('Transfer-Encoding', '')]):
            with self.assertRaises(ValueError):
                self.request(method='POST', headers=headers)
        for body in (dict(self.body, unknown=True), {'context': C}):
            with self.assertRaises(ValueError):
                self.request(method='POST', body=body)
        with self.assertRaises(ValueError):
            self.request(method='POST', params={'x': ['y']})
        with self.assertRaises(ValueError):
            self.request('/api/v1/alerts/' + ID + '/ack', 'POST', body=dict(context=C, owner='other'))
        for path, method in (('/api/v1/schedules/nope', 'GET'), ('/api/v1/schedules/' + ID + '/enable', 'POST'),
                             ('/api/v1/alerts', 'POST'), ('/api/v1/alerts/' + ID + '/ack', 'GET')):
            with self.assertRaises(QueryError) as raised:
                self.request(path, method)
            self.assertEqual(raised.exception.status, 404)
        self.assertFalse(self.request('/api/v1/jobs')[0])

    def test_active_workbench_runtime_scheduler_lifecycle(self):
        import workbench_server as server
        events = []
        with patch('runtime_profile.configure_process'), patch('runtime_profile.active', return_value=True), \
                patch.object(server.request_auth, 'validate_bind'), patch.object(server, 'HTTPServer') as factory, \
                patch.object(server.schedule_resident, 'start_scheduler', side_effect=lambda: events.append('start')) as start, \
                patch.object(server.schedule_resident, 'stop_scheduler', side_effect=lambda: events.append('stop')) as stop, \
                patch.object(server.warehouse_resident, 'persistent_trino') as trino, \
                patch.object(server.warehouse_resident, 'initialize_auto_chill') as chill, \
                patch.object(server.jobs_resident, 'start_scheduler') as legacy, redirect_stdout(io.StringIO()):
            http = factory.return_value.__enter__.return_value
            http.serve_forever.side_effect = lambda: events.append('serve')
            server.main(['--port', '8888'])
            self.assertEqual(events, ['start', 'serve', 'stop'])
            start.assert_called_once_with()
            stop.assert_called_once_with()
            trino.assert_not_called()
            chill.assert_not_called()
            legacy.assert_not_called()


class NativeScheduleRoutes(unittest.TestCase):
    def setUp(self):
        import request_auth
        import runtime_profile
        import warehouse_resident
        from test_stage_routes import StageHTTPTests

        ScheduleRoutes.setUp(self)
        self.http = StageHTTPTests()
        self.http.native, self.http.loopback, self.http.c = True, False, C
        credentials = 'Basic ' + base64.b64encode(b'owner:password').decode()
        self.http.f = SimpleNamespace(b=SimpleNamespace(fixture=SimpleNamespace(
            headers=lambda identity: {'Authorization': credentials})))
        salt = '12' * 16
        accounts = {'owner': dict(role='admin', platform_admin=False, salt=salt,
                                 password_hash=request_auth.password_hash('password', salt))}
        self.sql = Mock(side_effect=AssertionError('schedule routed through native SQL'))
        for p in (patch.dict(sys.modules, db_schedule_routes=self.routes),
                  patch.object(request_auth, '_principal_accounts', return_value=accounts),
                  patch.object(runtime_profile, 'active', return_value=True),
                  patch.object(warehouse_resident, '_native_request', self.sql)):
            p.start()
            self.addCleanup(p.stop)
        self.backend.list_alerts.return_value = dict(alerts=[], unacknowledged_count=0)

    def test_native_routes_preserve_context_identity_role_and_filters(self):
        cases = [('/api/v1/schedules?' + urlencode(dict(context=json.dumps(C), target_kind='stage', target_id=ID)),
                  None, 'list_schedules', 'schedules', []),
                 ('/api/v1/schedules', self.body, 'save_schedule', 'schedule', {'id': ID}),
                 ('/api/v1/schedules/' + ID, None, 'get_schedule', 'schedule', {'id': ID}),
                 ('/api/v1/schedules/' + ID + '/ticks', None, 'list_ticks', 'ticks', []),
                 ('/api/v1/alerts', None, 'list_alerts', 'alerts', []),
                 ('/api/v1/alerts/' + ID + '/ack', dict(context=C), 'acknowledge_alert', 'alert', {'id': ID})]
        for path, body, name, field, result in cases:
            with self.subTest(path=path):
                backend = getattr(self.backend, name)
                if name != 'list_alerts':
                    backend.return_value = result
                status, reply = self.http.request(path, body=body, context='?' not in path,
                    headers=[('X-Idempotency-Key', 'native-key')] if body else [])
                self.assertEqual(status, 200, reply)
                self.assertEqual(reply['context'], C)
                self.assertEqual(reply[field], result)
                principal, context, *ids = backend.call_args.args
                self.assertEqual((principal.principal_id, principal.maximum_role, principal.platform_admin),
                                 ('owner', 'admin', False))
                self.assertEqual(context.public_metadata(), C)
                self.assertEqual(ids, [ID] if name in ('get_schedule', 'list_ticks', 'acknowledge_alert') else [])
        self.assertEqual(self.backend.list_schedules.call_args.kwargs, dict(target_kind='stage', target_id=ID))
        self.assertEqual(self.backend.save_schedule.call_args.kwargs,
                         dict(key='native-key', **{k: v for k, v in self.body.items() if k != 'context'}))
        self.assertEqual(self.backend.acknowledge_alert.call_args.kwargs, dict(key='native-key'))
        # A caller may select a lower role; the adapter must forward that role exactly.
        self.http.c = dict(C, selected_role='bi_viewer')
        self.assertEqual(self.http.request('/api/v1/alerts')[0], 200)
        self.assertEqual(self.backend.list_alerts.call_args.args[1].selected_role, 'bi_viewer')
        self.sql.assert_not_called()

    def test_native_auth_roles_origin_and_strict_input_fail_before_backend(self):
        for path in ('/api/v1/schedules', '/api/v1/alerts'):
            cases = [(dict(identity=None), 401),
                     (dict(headers=[('Authorization', 'Basic invalid')]), 401),
                     (dict(request_headers=[('Authorization', 'Basic b3duZXI6cGFzc3dvcmQ=')]), 400),
                     (dict(role='invalid'), 400), (dict(role='bi_viewer'), 400),
                     (dict(headers=[('X-Shirokuma-Role', 'admin')]), 400),
                     (dict(context=False), 400),
                     (dict(headers=[('Content-Length', '0')]), 400),
                     (dict(headers=[('Transfer-Encoding', '')]), 400)]
            for options, expected in cases:
                with self.subTest(path=path, options=options):
                    self.assertEqual(self.http.request(path, **options)[0], expected)
            queries = [{'context': [json.dumps(C)] * 2}, {'context': ['{}']},
                       {'context': [json.dumps(C)[:-1] + ',"database_id":"other"}']},
                       {'context': [json.dumps(C)], 'unexpected': ['x']}]
            queries += [{'context': [json.dumps({k: v for k, v in C.items() if k != name})]} for name in C]
            for query in queries:
                with self.subTest(path=path, query=query):
                    self.assertEqual(self.http.request(path + '?' + urlencode(query, doseq=True), context=False)[0], 400)
            self.assertEqual(self.http.request(path + '#fragment')[0], 400)
        for path, body in (('/api/v1/schedules', self.body),
                           ('/api/v1/alerts/' + ID + '/ack', dict(context=C))):
            for extra, expected in [([('Origin', 'https://foreign.example')], 403),
                                    ([('X-Idempotency-Key', 'duplicate')], 400),
                                    ([('Idempotency-Key', 'ambiguous')], 400),
                                    ([('Content-Type', 'application/json')], 400),
                                    ([('Content-Length', '0')], 400),
                                    ([('Transfer-Encoding', 'chunked')], 400)]:
                with self.subTest(path=path, headers=extra):
                    self.assertEqual(self.http.request(path, body=body,
                        headers=[('X-Idempotency-Key', 'native-key')] + extra)[0], expected)
            self.assertEqual(self.http.request(path, body=body)[0], 400)
            self.assertEqual(self.http.request(path, body=dict(body, context={}),
                headers=[('X-Idempotency-Key', 'native-key')])[0], 400)
        for backend in vars(self.backend).values():
            backend.assert_not_called()
        self.sql.assert_not_called()

    def test_native_unknown_children_and_methods_do_not_fall_through(self):
        for path, method in (('/api/v1/schedules/nope', 'GET'),
                             ('/api/v1/alerts/nope', 'GET'),
                             ('/api/v1/schedules/' + ID + '/enable', 'POST'),
                             ('/api/v1/alerts', 'POST'),
                             ('/api/v1/alerts/' + ID + '/ack', 'GET'),
                             ('/api/v1/schedules', 'DELETE')):
            with self.subTest(path=path, method=method):
                self.assertEqual(self.http.request(path, method=method,
                    body=dict(context=C) if method == 'POST' else None,
                    headers=[('X-Idempotency-Key', 'native-key')])[0], 404)
        for backend in vars(self.backend).values():
            backend.assert_not_called()
        self.sql.assert_not_called()
        import runtime_profile
        with patch.object(runtime_profile, 'current', return_value=SimpleNamespace(sql_endpoint_url='http://unused', mode='database')):
            self.sql.side_effect = None
            self.sql.return_value = {'id': 'existing-native-fallback'}
            for path in ('/api/v1/schedules-other', '/api/v1/alerts-other', '/api/v1/unrelated', '/v1/statement'):
                self.assertEqual(self.http.request(path, context=False)[1], self.sql.return_value)
                self.assertEqual(self.sql.call_args.args[1:3], (path, 'GET'))


if __name__ == '__main__':
    unittest.main()
