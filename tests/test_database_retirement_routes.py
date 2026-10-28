"""Synthetic frozen retirement contract through both production HTTP parsers."""
import base64
from contextlib import contextmanager
from email.message import Message
import io
import json
from pathlib import Path
import sys
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import request_auth
import runtime_profile
import warehouse_resident
import workbench_server
import test_stage_routes as stage_harness
import control_store
import database_retirement_routes as routes


class RetirementError(Exception):
    def __init__(self, code, message='Retirement rejected', status=409):
        super().__init__(message)
        self.code, self.status = code, status


def operation():
    return dict(id='retire-op', action='DATABASE_RETIRE', actor='owner', database_id='db',
                revision=2, state='RETIRING', database=dict(id='db', sql_catalog='sales', state='retiring', revision=4),
                dependencies=[dict(kind='run', id='run', state='UNKNOWN', database_id='db')],
                blockers=[dict(kind='run', id='run', state='UNKNOWN', database_id='db', code='RUN_UNKNOWN')],
                stopped_schedule_ids=['schedule'])


class RetirementRoutes(unittest.TestCase):
    def setUp(self):
        self.registry = sqlite3.connect(':memory:')
        self.registry.row_factory = sqlite3.Row
        self.registry.execute('CREATE TABLE operations (id TEXT PRIMARY KEY, action TEXT NOT NULL)')
        self.registry.executemany('INSERT INTO operations VALUES (?,?)',
            [('retire-op', 'DATABASE_RETIRE'), ('generation', 'WAREHOUSE_PLAN'), ('grant', 'DATABASE_GRANT')])
        self.registry.commit()
        self.addCleanup(self.registry.close)
        @contextmanager
        def registry_connect():
            yield self.registry
        registry_patch = patch.object(control_store, 'connect', registry_connect)
        registry_patch.start()
        self.addCleanup(registry_patch.stop)
        self.op = operation()
        self.preview = dict(database=dict(id='db', sql_catalog='sales', state='READY', revision=3),
                            dependencies=self.op['dependencies'], blockers=self.op['blockers'],
                            stopped_schedule_ids=[], operation=None)
        self.backend = SimpleNamespace(RetirementError=RetirementError,
            preview=Mock(return_value=self.preview), request_retire=Mock(return_value=self.op),
            inspect=Mock(return_value=self.op), reconcile=Mock(return_value=self.op))
        self.http = stage_harness.StageHTTPTests()
        self.http.native, self.http.loopback, self.http.c = False, False, {'selected_role': 'admin'}
        auth = 'Basic ' + base64.b64encode(b'owner:password').decode()
        self.http.f = SimpleNamespace(b=SimpleNamespace(fixture=SimpleNamespace(headers=lambda identity: {'Authorization': auth})))
        salt = '12' * 16
        self.accounts = {'owner': dict(role='admin', platform_admin=True, salt=salt,
                                      password_hash=request_auth.password_hash('password', salt))}
        self.native = Mock(return_value={'native': True})
        for p in (patch.dict(sys.modules, database_retirement=self.backend),
                  patch.object(request_auth, '_principal_accounts', return_value=self.accounts),
                  patch.object(runtime_profile, 'active', return_value=True),
                  patch.object(runtime_profile, 'current', return_value=SimpleNamespace(sql_endpoint_url='http://synthetic', mode='database')),
                  patch.object(warehouse_resident, '_native_request', self.native),
                  patch.object(workbench_server.WorkbenchHandler, 'log_message')):
            p.start()
            self.addCleanup(p.stop)

    def request(self, path='/api/v1/admin/databases/db/retire', **kwargs):
        return self.http.request(path, context=False, **kwargs)

    def test_both_handlers_exact_arguments_status_and_frozen_replay(self):
        for native in (False, True):
            self.http.native = native
            status, reply = self.request()
            self.assertEqual((status, reply), (200, self.preview))
            for _ in range(2):
                self.assertEqual(self.request(body={'expected_revision': 3}, key='same-key'), (202, self.op))
            principal, role, dbid, payload, key = self.backend.request_retire.call_args.args
            self.assertEqual((principal.principal_id, principal.maximum_role, principal.platform_admin), ('owner', 'admin', True))
            self.assertEqual((role, dbid, payload, key), ('admin', 'db', {'expected_revision': 3}, 'same-key'))
            self.assertEqual(self.request('/api/v1/operations/retire-op'), (200, self.op))
            self.assertEqual(self.request('/api/v1/operations/retire-op/reconcile',
                body={'expected_revision': 2}, key='reconcile-key'), (202, self.op))
            args = self.backend.reconcile.call_args.args
            self.assertEqual(args[1:], ('admin', 'retire-op', {'expected_revision': 2}, 'reconcile-key'))
        self.native.assert_not_called()

    def test_both_handlers_auth_and_admin_ceiling_and_selected_role(self):
        for native in (False, True):
            self.http.native = native
            self.assertEqual(self.request(identity=None)[0], 401)
            for role in ('bi_viewer', 'data_engineer'):
                self.assertEqual(self.request(role=role)[0], 403)
            self.accounts['owner']['platform_admin'] = False
            self.assertEqual(self.request()[0], 403)
            self.accounts['owner']['platform_admin'] = True
            self.accounts['owner']['role'] = 'data_engineer'
            self.assertEqual(self.request()[0], 403)
            self.accounts['owner']['role'] = 'admin'
            for headers in ([('X-Shirokuma-Role', 'admin')],):
                self.assertEqual(self.request(headers=headers)[0], 400)
            self.assertEqual(self.request(request_headers=[('Authorization', 'Basic b3duZXI6cGFzc3dvcmQ=')])[0], 400)
            for path in ('/api/v1/admin/databases/db/retire', '/api/v1/operations/retire-op', '/api/v1/operations/retire-op/reconcile'):
                self.assertEqual(self.request(path, role='bi_viewer')[0], 403)
        self.backend.preview.assert_not_called()
        self.backend.inspect.assert_not_called()

    def test_both_handlers_strict_framing_and_payload(self):
        for native in (False, True):
            self.http.native = native
            for suffix in ('?x=1', '?x=1&x=2', '?', '#fragment'):
                self.assertEqual(self.request('/api/v1/admin/databases/db/retire'+suffix)[0], 400)
            for headers in ([('Content-Length', '0')], [('Transfer-Encoding', '')], [('Content-Length', '1')]):
                self.assertEqual(self.request(headers=headers)[0], 400)
            for body in ({}, {'expected_revision': True}, {'expected_revision': 0},
                         {'expected_revision': 1.5}, {'expected_revision': '3'},
                         {'expected_revision': 3, 'unknown': True}):
                self.assertEqual(self.request(body=body, key='key')[0], 400)
            for key in (None, '', '   '):
                self.assertEqual(self.request(body={'expected_revision': 3}, key=key)[0], 400)
            for headers in ([('Idempotency-Key', 'duplicate')], [('X-Idempotency-Key', 'other')],
                            [('Content-Type', 'application/json')], [('Content-Length', '24')], [('Transfer-Encoding', 'chunked')]):
                self.assertEqual(self.request(body={'expected_revision': 3}, key='key', headers=headers)[0], 400)
            for headers in ([('Origin', 'https://evil.example')], [('Origin', 'http://127.0.0.1'), ('Origin', 'http://127.0.0.1')]):
                self.assertEqual(self.request(body={'expected_revision': 3}, key='key', headers=headers)[0], 403)
            # Send actual duplicate JSON through the production body parser.
            payload = {'expected_revision': 3}
            dumps = json.dumps
            with patch.object(stage_harness.json, 'dumps', side_effect=lambda value, *a, **kw:
                    '{"expected_revision":3,"expected_revision":3}' if value is payload else dumps(value, *a, **kw)):
                self.assertEqual(self.request(body=payload, key='key')[0], 400)
            connection = stage_harness.Connection
            length = len(json.dumps(payload).encode())
            def incomplete(incoming):
                if incoming.startswith(b'POST '):
                    incoming = incoming.replace(('Content-Length: '+str(length)).encode(),
                                                ('Content-Length: '+str(length+5)).encode(), 1)
                return connection(incoming)
            with patch.object(stage_harness, 'Connection', side_effect=incomplete):
                self.assertEqual(self.request(body=payload, key='key')[0], 400)

    def test_canonical_errors_safe_unexpected_errors(self):
        for native in (False, True):
            self.http.native = native
            self.backend.request_retire.side_effect = RetirementError('REVISION_CONFLICT', 'Database revision changed')
            status, reply = self.request(body={'expected_revision': 3}, key='key')
            self.assertEqual((status, reply['code'], reply['message']), (409, 'REVISION_CONFLICT', 'Database revision changed'))
            self.backend.request_retire.side_effect = RuntimeError('secret SQL artifact URL')
            status, reply = self.request(body={'expected_revision': 3}, key='key')
            self.assertEqual(status, 503)
            self.assertNotIn('secret', json.dumps(reply))

    def test_generation_fallthrough_preserves_body_and_original_handoff(self):
        import catalog_admin
        self.backend.inspect.side_effect = AssertionError('nonretirement reached retirement core')
        generation = dict(id='generation', state='PLANNED', handoffs=[])
        with patch.object(catalog_admin, 'inspect_operation', return_value=generation) as inspect, \
                patch.object(catalog_admin, 'request_action', return_value={'requires_operator': True}) as action:
            self.assertEqual(self.request('/api/v1/operations/generation'), (200, generation))
            self.assertEqual(self.request('/api/v1/operations/generation/reconcile',
                body={'expected_revision': 7}, key='generation-key'), (202, {'requires_operator': True}))
            self.assertEqual(action.call_args.args[2:], ('generation', '/api/v1/operations/generation/reconcile', 'generation-key', {'expected_revision': 7}))
            inspect.assert_called_once()
        self.http.native = True
        self.assertEqual(self.request('/v1/statement', body={'query': 'synthetic'})[0], 200)
        self.assertEqual(self.native.call_args.args[1:3], ('/v1/statement', 'POST'))
        self.assertEqual(self.request('/api/v1/operations/generation/reconcile', body={'expected_revision': 9}, key='key')[0], 200)
        self.assertEqual(json.loads(self.native.call_args.args[3]), {'expected_revision': 9})
        self.assertEqual(self.request('/api/v1/admin/databases/db/retire/wrong')[0], 200)
        self.backend.inspect.assert_not_called()

    def test_nonretirement_type_precheck_bypasses_auth_framing_and_body(self):
        statements = []
        self.registry.set_trace_callback(statements.append)
        for identifier in ('generation', 'grant', 'missing'):
            for method, suffix in (('GET', ''), ('POST', '/reconcile')):
                for tail in ('', '?x=1&x=2#fragment'):
                    headers = Message()
                    headers['Transfer-Encoding'] = 'chunked'
                    headers['Content-Length'] = '99'
                    headers['Origin'] = 'https://evil.invalid'
                    raw = b'{"expected_revision":7,"extra":"original handoff body"}'
                    handler = SimpleNamespace(path='/api/v1/operations/'+identifier+suffix+tail,
                        headers=headers, rfile=io.BytesIO(raw), respond=Mock())
                    with patch.object(request_auth, 'authenticate_principal',
                                      side_effect=AssertionError('adapter authenticated nonretirement')):
                        self.assertFalse(routes.dispatch(handler, method))
                    self.assertEqual(handler.rfile.tell(), 0)
                    self.assertEqual(handler.rfile.read(), raw)
                    handler.respond.assert_not_called()
        self.assertEqual(len(statements), 12)
        self.assertTrue(all(sql.startswith('SELECT action FROM operations WHERE id=') for sql in statements))
        self.backend.inspect.assert_not_called()

    def test_known_retirement_stays_strict_and_missing_core_is_unavailable(self):
        for native in (False, True):
            self.http.native = native
            for path in ('/api/v1/operations/retire-op', '/api/v1/operations/retire-op/reconcile'):
                self.assertEqual(self.request(path, request_headers=[('Authorization', 'Basic b3duZXI6cGFzc3dvcmQ=')])[0], 400)
                self.assertEqual(self.request(path+'?x=1')[0], 400)
            with patch.dict(sys.modules, database_retirement=None):
                for path in ('/api/v1/operations/retire-op', '/api/v1/operations/retire-op/reconcile',
                             '/api/v1/admin/databases/db/retire'):
                    self.assertEqual(self.request(path)[0], 503)
            self.backend.inspect.side_effect = RetirementError('OPERATION_NOT_FOUND', status=404)
            self.assertEqual(self.request('/api/v1/operations/retire-op')[0], 404)
            self.backend.inspect.side_effect = None

    def test_native_nonretirement_unknown_and_missing_role_remain_original_requests(self):
        self.http.native = True
        for identifier in ('generation', 'grant', 'missing'):
            path = '/api/v1/operations/'+identifier
            self.assertEqual(self.request(path, request_headers=[])[0], 200)
            self.assertEqual(self.native.call_args.args[1:4], (path, 'GET', b''))
            raw = {'generation': 'original body'}
            self.assertEqual(self.request(path+'/reconcile', body=raw,
                request_headers=[('Content-Type', 'application/json')])[0], 200)
            self.assertEqual(json.loads(self.native.call_args.args[3]), raw)
        self.backend.inspect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
