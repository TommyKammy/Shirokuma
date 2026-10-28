"""Exercise v2 dispatch without sockets, live profiles, or backend execution."""
import io
import json
from email.message import Message
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import workbench_server as wb
import runtime_profile
import request_auth
import query_context
import query_service
import database_inventory as inventory
import bears_resident


class WorkbenchDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.principal = request_auth.AuthenticatedPrincipal('alice', 'bi_viewer', False)
        for target, name, value in ((runtime_profile, 'active', True), (request_auth, 'authenticate_principal', self.principal)):
            mocked = patch.object(target, name, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def request(self, path, method='GET', body=None, roles=('data_engineer',), raw=None, headers=()):
        handler = object.__new__(wb.WorkbenchHandler)
        handler.path = path
        handler.headers = Message()
        handler.headers['Authorization'] = 'Basic synthetic'
        for role in roles:
            handler.headers['X-Shirokuma-Role'] = role
        for key, value in headers:
            handler.headers[key] = value
        if body is not None or raw is not None:
            data = raw.encode() if raw is not None else json.dumps(body).encode()
            handler.headers['Content-Type'] = 'application/json'
            handler.headers['Content-Length'] = str(len(data))
            handler.rfile = io.BytesIO(data)
        replies = []
        handler.respond = lambda value, status=200: replies.append((status, value))
        getattr(handler, 'do_' + method)()
        self.assertEqual(len(replies), 1)
        return replies[0]

    @staticmethod
    def query():
        return dict(sql='SELECT 1', request_id='request', worksheet_id='worksheet', mode='read-only', max_rows=50,
            context=dict(database_id='database', schema_id='schema', warehouse_id='LOCAL', binding_revision=1, generation_id='generation'))

    def test_session_bootstraps_without_data_role(self):
        status, session = self.request('/api/session', roles=())
        self.assertEqual(status, 200)
        self.assertEqual(session['role'], 'bi_viewer')
        self.assertEqual(session['data_mode'], 'database')
        self.assertFalse(session['platform_admin'])
        self.assertNotIn('password', json.dumps(session))

    def test_display_patch_contract_origin_headers_duplicate_json_and_auth(self):
        import display_management
        admin = request_auth.AuthenticatedPrincipal('admin', 'admin', True)
        payload = dict(display_name='Name', description='', expected_revision=1)
        headers = [('Idempotency-Key', 'key')]
        with patch.object(request_auth, 'authenticate_principal', return_value=admin), \
                patch.object(display_management, 'mutate', return_value={'state':'APPLIED'}) as mutate:
            for resource, identifier in (('databases','db'), ('schemas','s')):
                body = payload if resource == 'databases' else {k:v for k,v in payload.items() if k != 'description'}
                self.assertEqual(self.request('/api/v1/'+resource+'/'+identifier+'/display', 'PATCH', body,
                    roles=('admin',), headers=headers), (200, {'state':'APPLIED'}))
                mutate.assert_called_with(admin, 'admin', 'database' if resource == 'databases' else 'schema', identifier, body, 'key')
            mutate.reset_mock()
            for supplied in ([], headers*2, headers+[('Origin','http://evil.example'),('Host','localhost:8888')]):
                self.assertIn(self.request('/api/v1/databases/db/display','PATCH',payload,roles=('admin',),headers=supplied)[0], (400,403))
            self.assertEqual(self.request('/api/v1/databases/db/display','PATCH',roles=('admin',),headers=headers,
                raw='{"display_name":"a","display_name":"b","description":"","expected_revision":1}')[0], 400)
            for roles in ((), ('admin','admin'), ('data_engineer',)):
                self.assertIn(self.request('/api/v1/databases/db/display','PATCH',payload,roles=roles,headers=headers)[0], (400,403))
            mutate.assert_not_called()
        with patch.object(display_management, 'mutate') as mutate:
            self.assertEqual(self.request('/api/v1/databases/db/display','PATCH',payload,roles=('admin',),headers=headers)[0], 400)
            mutate.assert_not_called()

    def test_admin_listing_does_not_replace_inventory_and_cors_supports_patch(self):
        import display_management
        admin = request_auth.AuthenticatedPrincipal('admin','admin',True)
        with patch.object(request_auth, 'authenticate_principal', return_value=admin), \
                patch.object(display_management, 'list_databases', return_value={'databases':[]}) as listing, \
                patch.object(inventory, 'list_databases', return_value={'items':[]}) as ordinary:
            self.assertEqual(self.request('/api/v1/admin/databases',roles=('admin',)), (200,{'databases':[]}))
            listing.assert_called_once_with(admin, 'admin')
            ordinary.assert_not_called()
            self.assertEqual(self.request('/api/v1/databases',roles=('admin',)), (200,{'items':[]}))
            ordinary.assert_called_once()
        handler = object.__new__(wb.WorkbenchHandler)
        handler.headers = Message()
        handler.headers['Host'] = 'localhost:8888'
        handler.headers['Origin'] = 'http://localhost:8888'
        sent = {}
        handler.send_header = lambda k,v: sent.update({k:v})
        with patch('http.server.BaseHTTPRequestHandler.end_headers'):
            handler.end_headers()
        self.assertIn('PATCH', sent['Access-Control-Allow-Methods'])
        for name in ('Authorization','Idempotency-Key','X-Shirokuma-Role'):
            self.assertIn(name, sent['Access-Control-Allow-Headers'])

    def test_exact_explicit_role_before_inventory(self):
        with patch.object(inventory, 'list_databases') as listing:
            for roles in ((), ('viewer',), ('admin', 'admin'), (' admin',), ('ADMIN',)):
                with self.subTest(roles=roles):
                    self.assertEqual(self.request('/api/v1/databases', roles=roles)[0], 400)
            listing.assert_not_called()

    def test_authentication_error_is_safe(self):
        with patch.object(request_auth, 'authenticate_principal', side_effect=request_auth.AuthenticationError('sensitive detail')):
            status, error = self.request('/api/v1/databases')
            self.assertEqual(status, 401)
            self.assertEqual(set(error), {'code', 'message', 'request_id', 'details', 'retryable'})
            self.assertNotIn('sensitive', json.dumps(error))

    def test_legacy_routes_are_unavailable_before_handlers(self):
        with patch.object(wb.bears_resident, 'catalog') as catalog, patch.object(wb.warehouse_resident, 'transition') as transition:
            for path in ('/api/catalog', '/api/history', '/api/pawprints', '/api/models', '/api/lineage', '/api/ai/config', '/api/jobs', '/api/stages', '/api/marts'):
                self.assertEqual(self.request(path)[1]['code'], 'FEATURE_UNAVAILABLE')
            for path in ('/api/warehouse/wake', '/api/warehouse/chill', '/api/query', '/api/ingest', '/api/models/build', '/api/bears/run'):
                self.assertEqual(self.request(path, 'POST', {})[1]['code'], 'FEATURE_UNAVAILABLE')
            catalog.assert_not_called()
            transition.assert_not_called()

    def test_unknown_duplicate_parameters_do_not_reach_services(self):
        with patch.object(inventory, 'list_objects') as listing, patch.object(query_service, 'history') as history:
            for path in ('/api/v1/schemas/schema/objects?role=admin', '/api/v1/schemas/schema/objects?limit=1&limit=2',
                         '/api/v1/schemas/schema/objects?limit=true', '/api/v1/queries?database_id=d&database_id=d',
                         '/api/v1/queries?database_id=d&schema_id=s'):
                self.assertEqual(self.request(path)[0], 400)
            listing.assert_not_called()
            history.assert_not_called()

    def test_query_body_validation_precedes_resolver(self):
        bad = []
        for key in self.query():
            body = self.query()
            del body[key]
            bad.append(body)
        body = self.query()
        body['role'] = 'admin'
        bad.append(body)
        for key, value in (('max_rows', True), ('max_rows', 0), ('sql', 1), ('mode', 'admin'), ('mode', 'read'), ('mode', 'write')):
            body = self.query()
            body[key] = value
            bad.append(body)
        for key, value in (('binding_revision', True), ('warehouse_id', 'OTHER'), ('database_id', ''), ('extra', 'x')):
            body = self.query()
            body['context'][key] = value
            bad.append(body)
        with patch.object(query_context, 'resolve_context') as resolve, patch.object(query_service, 'submit') as submit:
            for body in bad:
                self.assertEqual(self.request('/api/v1/queries', 'POST', body)[0], 400)
            self.assertEqual(self.request('/api/v1/queries?database_id=conflict', 'POST', self.query())[0], 400)
            for raw in ('{"sql":"a","sql":"b"}', '{"context":{"database_id":"a","database_id":"b"}}', '[]'):
                self.assertEqual(self.request('/api/v1/queries', 'POST', raw=raw)[0], 400)
            resolve.assert_not_called()
            submit.assert_not_called()

    def test_frozen_context_and_page_envelope_pass_unchanged(self):
        context = query_context.ResolvedQueryContext('database', 'schema', 'LOCAL', 'catalog', 'raw', 'data_engineer', 1, 'generation', 1)
        page = dict(id='application-id', backend_query_id='backend', data=[], next_uri='/api/v1/queries/application-id/pages/token')
        with patch.object(query_context, 'resolve_context', return_value=context) as resolve, patch.object(query_service, 'submit', return_value=page) as submit:
            status, result = self.request('/api/v1/queries', 'POST', self.query())
            self.assertEqual(status, 200)
            self.assertIs(result, page)
            self.assertIs(submit.call_args.args[1], context)
            self.assertEqual(resolve.call_args.kwargs['expected_binding_revision'], 1)
            body = self.query()
            body['context']['generation_id'] = 'stale'
            submit.reset_mock()
            self.assertEqual(self.request('/api/v1/queries', 'POST', body)[0], 409)
            submit.assert_not_called()

    def test_cancel_downgraded_role_is_delegated_to_core(self):
        with patch.object(query_service, 'cancel', return_value={'state': 'CANCELED'}) as cancel, \
                patch.object(request_auth, 'authorize_database', side_effect=AssertionError('must not authorize here')):
            self.assertEqual(self.request('/api/v1/queries/q/cancel', 'POST', {}, roles=('admin',))[0], 200)
            cancel.assert_called_once_with(self.principal, 'q', selected_role='admin')
            cancel.reset_mock()
            for body in ({'role': 'admin'}, {'sql': 'x'}):
                self.assertEqual(self.request('/api/v1/queries/q/cancel', 'POST', body)[0], 400)
            cancel.assert_not_called()

    def test_canonical_modes_are_forwarded_unchanged(self):
        context = query_context.ResolvedQueryContext('database', 'schema', 'LOCAL', 'catalog', 'raw', 'admin', 1, 'generation', None)
        with patch.object(query_context, 'resolve_context', return_value=context), patch.object(query_service, 'submit', return_value={'id': 'q'}) as submit:
            for mode in ('read-only', 'sandbox-write', 'admin-write'):
                body = self.query()
                body['mode'] = mode
                with self.subTest(mode=mode):
                    self.assertEqual(self.request('/api/v1/queries', 'POST', body, roles=('admin',))[0], 200)
                    self.assertEqual(submit.call_args.kwargs['mode'], mode)
                    self.assertEqual(submit.call_args.args[2], body['sql'])

    def test_query_errors_map_and_other_errors_do_not_leak(self):
        with patch.object(query_service, 'inspect_query', side_effect=query_service.QueryError('STALE_BINDING', 409, {'revision': 2})):
            status, result = self.request('/api/v1/queries/q')
            self.assertEqual((status, result['code'], result['details']), (409, 'STALE_BINDING', {'revision': 2}))
        with patch.object(query_service, 'inspect_query', side_effect=RuntimeError('Bearer secret')):
            status, result = self.request('/api/v1/queries/q')
            self.assertEqual(status, 502)
            self.assertNotIn('secret', json.dumps(result))

    def test_inventory_routes_and_query_pages(self):
        routes = [('/api/v1/databases', 'list_databases'), ('/api/v1/databases/d/schemas', 'list_schemas'),
                  ('/api/v1/schemas/s/objects?kind=TABLE&q=a&limit=2', 'list_objects'),
                  ('/api/v1/objects/resolve?database_id=d&schema_id=s&name=a', 'resolve_object'),
                  ('/api/v1/objects/o?detail=columns', 'object_detail')]
        for path, function in routes:
            with patch.object(inventory, function, return_value={'items': []}) as service:
                self.assertEqual(self.request(path)[0], 200)
                service.assert_called_once()
        for path, function in (('/api/v1/queries?database_id=d', 'history'), ('/api/v1/queries/q', 'inspect_query'), ('/api/v1/queries/q/pages/opaque', 'fetch')):
            with patch.object(query_service, function, return_value={'id': 'q'}) as service:
                self.assertEqual(self.request(path), (200, {'id': 'q'}))
                service.assert_called_once()

    def test_bears_helpers_are_guarded_without_autoload(self):
        with patch.object(runtime_profile, 'configure_process', side_effect=AssertionError('must not load')):
            self.assertEqual(bears_resident.run(SimpleNamespace(bear='catalog'))['code'], 'FEATURE_UNAVAILABLE')
            with self.assertRaisesRegex(ValueError, 'FEATURE_UNAVAILABLE'):
                bears_resident.catalog(SimpleNamespace(include_config=True, role='admin'))

    def test_v2_startup_configures_profile_without_legacy_background_work(self):
        events = []
        with patch.object(runtime_profile, 'configure_process') as configure, patch.object(wb, 'HTTPServer') as server, \
                patch.object(request_auth, 'validate_bind', side_effect=lambda host: events.append(('validate', host))) as validate, \
                patch.object(wb.warehouse_resident, 'initialize_auto_chill') as chill, \
                patch.object(wb.warehouse_resident, 'persistent_trino') as tunnel, \
                patch.object(wb.jobs_resident, 'start_scheduler') as scheduler, patch('sys.stdout', new_callable=io.StringIO):
            configure.side_effect = lambda: events.append(('configure',))
            wb.main([])
            configure.assert_called_once_with()
            validate.assert_called_once_with('127.0.0.1')
            self.assertEqual(events, [('configure',), ('validate', '127.0.0.1')])
            server.return_value.__enter__.return_value.serve_forever.assert_called_once_with()
            chill.assert_not_called()
            tunnel.assert_not_called()
            scheduler.assert_not_called()


if __name__ == '__main__':
    unittest.main()
