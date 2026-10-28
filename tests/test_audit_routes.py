"""Strict audit HTTP framing, payload and authentication without listening sockets."""
import io
import json
from email.message import Message
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit, parse_qs, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import audit_routes as routes
import audit_service as audit
import request_auth
import workbench_server as wb
import runtime_profile


class AuditRoutes(unittest.TestCase):
    def request(self, path='/api/v1/audit/events?database_id=db', method='GET', headers=(), role='admin'):
        h = Message()
        h['X-Shirokuma-Role'] = role
        for k, v in headers: h[k] = v
        handler = SimpleNamespace(path=path, headers=h, respond=Mock(), send_response=Mock(), send_header=Mock(),
                                  end_headers=Mock(), wfile=io.BytesIO())
        url = urlsplit(path)
        handled = routes.dispatch(handler, method, url.path, parse_qs(url.query, keep_blank_values=True), 'principal', role)
        return handled, handler

    def test_endpoints_envelopes_download_and_exact_coordinates(self):
        event = dict(id='opaque', source='query', source_id='qid')
        reply = dict(event=event, context={'scope':'self'}, coverage=audit.COVERAGE)
        with patch.object(audit, 'events', return_value=reply) as events:
            _, handler = self.request()
            self.assertEqual(handler.respond.call_args.args[0], reply)
            events.assert_called_with('principal', 'admin', event_id=None, database_id='db')
            _, handler = self.request('/api/v1/audit/events/opaque/download?scope=database&database_id=db')
            events.assert_called_with('principal', 'admin', event_id='opaque', scope='database', database_id='db')
            self.assertEqual(json.loads(handler.wfile.getvalue()), reply)
            sent = dict(call.args for call in handler.send_header.call_args_list)
            self.assertEqual(sent['Content-Type'], 'application/json; charset=utf-8')
            self.assertIn('attachment;', sent['Content-Disposition'])
            self.assertEqual(sent['Cache-Control'], 'no-store')
            self.request('/api/v1/audit/events?database_id=db&limit=100&source=&state=&search=%25')
            self.assertEqual(events.call_args.kwargs['limit'], 100)
            self.assertEqual(events.call_args.kwargs['search'], '%')
        for endpoint in ('capabilities', 'telemetry'):
            with patch.object(audit, endpoint, return_value={'safe':True}) as method:
                _, handler = self.request('/api/v1/audit/' + endpoint)
                method.assert_called_once_with('principal', 'admin')
                self.assertEqual(handler.respond.call_args.args[0], {'safe':True})

    def test_strict_params_roles_framing_methods(self):
        with patch.object(audit, 'events') as service:
            for path in ('/api/v1/audit/events?limit=1&limit=2', '/api/v1/audit/events?unknown=x',
                         '/api/v1/audit/events?role=admin', '/api/v1/audit/events?limit=1.0',
                         '/api/v1/audit/events?limit=-1', '/api/v1/audit/events/x?state=x',
                         '/api/v1/audit/telemetry?database_id=db', '/api/v1/audit/capabilities?scope=platform'):
                with self.subTest(path=path), self.assertRaises(audit.QueryError): self.request(path)
            for headers in ([('X-Shirokuma-Role','admin')], [('Transfer-Encoding','')], [('Content-Length','1')],
                            [('Content-Length','0'),('Content-Length','0')]):
                with self.assertRaises(audit.QueryError): self.request(headers=headers)
            for method in ('POST', 'PATCH', 'DELETE', 'HEAD'):
                with self.assertRaises(audit.QueryError) as raised: self.request(method=method)
                self.assertEqual(raised.exception.status, 405)
            with self.assertRaises(audit.QueryError): self.request('/api/v1/audit/events#fragment')
            with self.assertRaises(audit.QueryError): self.request('/api/v1/audit/unknown')
            service.assert_not_called()
        self.assertFalse(self.request('/api/v1/audit-other')[0])

    def test_real_workbench_dispatch_no_legacy_bypass_even_without_profile(self):
        principal = request_auth.AuthenticatedPrincipal('alice', 'admin', False)
        for active in (False, True):
            for method in ('GET', 'POST', 'PATCH'):
                handler = object.__new__(wb.WorkbenchHandler)
                handler.path = '/api/v1/audit/events?database_id=db'
                handler.headers = Message()
                handler.headers['X-Shirokuma-Role'] = 'admin'
                handler.respond = Mock()
                with patch.object(runtime_profile, 'active', return_value=active), \
                        patch.object(request_auth, 'authenticate_principal', return_value=principal), \
                        patch.object(audit, 'events', return_value={'items':[]}) as service, \
                        patch.object(wb.bears_resident, 'catalog') as legacy:
                    getattr(handler, 'do_' + method)()
                    reply, status = (handler.respond.call_args.args + (200,))[:2]
                    self.assertEqual(status, 200 if method == 'GET' else 405)
                    self.assertEqual(service.call_count, 1 if method == 'GET' else 0)
                    legacy.assert_not_called()
        handler = object.__new__(wb.WorkbenchHandler)
        handler.path = '/api/v1/audit/events'
        handler.headers = Message()
        handler.respond = Mock()
        with patch.object(request_auth, 'authenticate_principal', side_effect=request_auth.AuthenticationError('private')):
            handler.do_GET()
        self.assertEqual(handler.respond.call_args.args[1], 401)
        self.assertNotIn('private', json.dumps(handler.respond.call_args.args[0]))


if __name__ == '__main__': unittest.main()
