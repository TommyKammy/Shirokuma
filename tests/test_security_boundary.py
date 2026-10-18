"""Identity and relation authorization regressions at real localhost HTTP entrances."""
import base64
from contextlib import nullcontext
from email.message import Message
import http.client
from http.server import ThreadingHTTPServer
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import request_auth as auth
import trino_auth
validate_shared_backend = auth.validate_shared_backend
import workbench_server as wb
import test_warehouse_resident as warehouse_tests
w = warehouse_tests.w


class SecurityBoundaryTests(unittest.TestCase):
    endpoint_request = warehouse_tests.ResidentWarehouseTests.endpoint_request

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.accounts = Path(self.temp.name) / 'accounts.json'
        self.users = {}
        for user, role in [('alice', 'admin'), ('emily', 'data_engineer'), ('vera', 'bi_viewer')]:
            salt = os.urandom(16).hex()
            self.users[user] = dict(role=role, salt=salt, password_hash=auth.password_hash('secret-' + user, salt))
        self.accounts.write_text(json.dumps(self.users))
        env = patch.dict(os.environ, SHIROKUMA_AUTH_MODE='shared',
                         SHIROKUMA_AUTH_FILE=str(self.accounts),
                         SHIROKUMA_STATE_DIR=self.temp.name,
                         SHIROKUMA_TRINO_AUTH_MODE='local')
        env.start()
        self.addCleanup(env.stop)
        backend_guard = patch.object(auth, 'validate_shared_backend')
        backend_guard.start()
        self.addCleanup(backend_guard.stop)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), wb.WorkbenchHandler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def headers(self, user=None, **extra):
        headers = {'Content-Type': 'application/json', **extra}
        if user:
            headers['Authorization'] = 'Basic ' + base64.b64encode((user + ':secret-' + user).encode()).decode()
        return headers

    def workbench(self, path, body=None, user=None, **extra):
        client = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            client.request('POST' if body is not None else 'GET', path,
                           json.dumps(body) if body is not None else None, self.headers(user, **extra))
            response = client.getresponse()
            response.read()
            return response.status
        finally:
            client.close()

    def test_three_authenticated_roles_and_spoofing(self):
        with patch.object(wb.bears_resident, 'load_ai_config', return_value={}) as load:
            self.assertEqual(self.workbench('/api/ai/config', user='alice'), 200)
            for user in ('emily', 'vera'):
                self.assertEqual(self.workbench('/api/ai/config', user=user), 403)
                self.assertEqual(self.workbench('/api/ai/config', user=user,
                                 **{'X-Trino-User': 'admin', 'X-Shirokuma-Role': 'admin'}), 403)
            self.assertEqual(self.workbench('/api/ai/config', **{'X-Trino-User': 'admin'}), 401)
            self.assertEqual(load.call_count, 1)
        with patch.object(wb.warehouse_resident, 'query', return_value={}) as query:
            for user in ('alice', 'emily'):
                self.assertEqual(self.workbench('/api/warehouse/query', {'sql': 'SELECT * FROM iceberg.raw.secret'}, user), 200)
            self.assertEqual(self.workbench('/api/warehouse/query', {'sql': 'SELECT * FROM iceberg.raw.secret'}, 'vera'), 403)
            self.assertEqual(query.call_count, 2)
        with patch.object(wb.bears_resident, 'run') as run:
            self.assertEqual(self.workbench('/api/bears/run', {'bear': 'catalog', 'action': 'describe', 'table': 'raw.secret'}, 'vera'), 403)
            run.assert_not_called()

    def test_invalid_authentication_fails_closed(self):
        for value in ('', 'Bearer token', 'Basic !!!', 'Basic ' + base64.b64encode(b'alice:wrong').decode(),
                      'Basic ' + base64.b64encode(b'unknown:password').decode()):
            headers = Message()
            headers['Authorization'] = value
            with self.assertRaises(auth.AuthenticationError):
                auth.authenticate(headers)
        headers = Message()
        headers['Authorization'] = self.headers('alice')['Authorization']
        headers['Authorization'] = headers['Authorization']
        with self.assertRaises(auth.AuthenticationError):
            auth.authenticate(headers)
        for malformed in ('{}', '[]', '{bad', '{"x":{"role":"admin"}}'):
            self.accounts.write_text(malformed)
            self.assertEqual(self.workbench('/'), 401)
        self.accounts.unlink()
        self.assertEqual(self.workbench('/'), 401)

    def test_sql_endpoint_identity_and_default_ceiling(self):
        for user in ('vera', None):
            code, _, _, backend, *_ = self.endpoint_request('POST', '/v1/statement',
                'SELECT * FROM iceberg.raw.secret', headers=self.headers(user, **{'X-Trino-User': 'ordinary_person'}))
            self.assertEqual(code, 403 if user else 401)
            backend.request.assert_not_called()
        for user in ('alice', 'emily'):
            code, _, _, backend, *_ = self.endpoint_request('POST', '/v1/statement',
                'SELECT * FROM iceberg.raw.secret', headers=self.headers(user))
            self.assertEqual(code, 200)
            self.assertEqual(backend.request.call_args.kwargs['headers']['X-Trino-User'], user)
            self.assertNotIn('Authorization', backend.request.call_args.kwargs['headers'])
        with patch.dict(os.environ, SHIROKUMA_AUTH_MODE='local', SHIROKUMA_AUTH_FILE='', SHIROKUMA_DEFAULT_ROLE='bi_viewer'):
            for sql in ('SELECT * FROM iceberg.raw.secret', 'DROP TABLE iceberg.marts.t'):
                code, _, _, backend, *_ = self.endpoint_request('POST', '/v1/statement', sql,
                    headers={'X-Trino-User': 'ordinary_person'})
                self.assertEqual(code, 403)
                backend.request.assert_not_called()

    def test_session_identity_initializes_roles_without_selector(self):
        client = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            for user, maximum in [('alice', 'admin'), ('emily', 'data_engineer'), ('vera', 'bi_viewer')]:
                client.request('GET', '/api/session', headers=self.headers(user))
                response = client.getresponse()
                raw = response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(raw), dict(username=user, role=maximum, maximum_role=maximum, authenticated=True))
                self.assertNotIn(b'password', raw)
                self.assertNotIn(b'secret-', raw)
        finally:
            client.close()
        html = wb.INDEX.read_text()
        self.assertIn("const response=await fetch('/api/session')", html)
        self.assertIn("option.disabled=ranks[option.value]>ranks[currentRole]", html)

    def test_viewer_frontend_bootstrap_uses_authenticated_role(self):
        html = wb.INDEX.read_text()
        bootstrap = html.split('async function initializeSession() {', 1)[1].split('initializeSession().catch', 1)[0]
        script = """
let currentRole='admin';
const options=['admin','data_engineer','bi_viewer'].map(value=>({value,disabled:false}));
const select={options,value:'admin'};
const calls=[];
const $=()=>select;
let applied;
function applyRole(){applied=currentRole;}
function refreshStatus(){calls.push('status:'+currentRole);}
function loadCatalog(){calls.push('catalog:'+currentRole);}
async function fetch(path){calls.push(path);return {ok:true,json:async()=>({maximum_role:'bi_viewer'})};}
async function initializeSession(){""" + bootstrap + """
initializeSession().then(()=>{
 if(applied!=='bi_viewer'||select.value!=='bi_viewer'||!options[0].disabled||!options[1].disabled||options[2].disabled)throw Error('incorrect role menu');
 if(JSON.stringify(calls)!==JSON.stringify(['/api/session','status:bi_viewer','catalog:bi_viewer']))throw Error('API called before identity');
});
"""
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_engineer_endpoint_write_scope(self):
        for schema in ('raw', 'sandbox', 'marts', 'staging'):
            sql = 'INSERT INTO iceberg.' + schema + '.t SELECT 1'
            for user in ('emily', 'vera', 'alice'):
                expected = 200 if user == 'alice' or (user == 'emily' and schema in {'raw', 'sandbox'}) else 403
                with self.subTest(schema=schema, user=user):
                    code, _, _, backend, *_ = self.endpoint_request('POST', '/v1/statement', sql, headers=self.headers(user))
                    self.assertEqual(code, expected)
                    self.assertEqual(backend.request.called, expected == 200)

    def test_relation_positions_and_http_boundaries(self):
        denied = [
            'SELECT * FROM iceberg.marts.t limit, iceberg.raw.secret',
            'SELECT * FROM iceberg.marts.limit, iceberg.raw.secret',
            'SELECT * FROM "iceberg"."marts"."limit" "where", "iceberg"."raw"."secret"',
            'WITH x AS (SELECT * FROM iceberg.raw.secret) SELECT * FROM x',
            'WITH x AS (SELECT * FROM iceberg.marts.t) SELECT * FROM x JOIN iceberg.raw.secret ON true',
            'SELECT * FROM (SELECT * FROM iceberg.marts.t) limit, iceberg.raw.secret',
            'SELECT * FROM TABLE(iceberg.raw.function())',
            'SELECT * FROM marts.public window, raw.secret',
            'SELECT * FROM marts.public fetch, raw.secret',
            'SELECT * FROM (VALUES 1) window, raw.secret',
            'SELECT * FROM marts.public t JOIN marts.other o ON t.window = o.window, raw.secret',
            'SELECT * FROM marts.public t JOIN marts.other o ON limit = 1, raw.secret',
        ]
        allowed = [
            'SELECT execute FROM iceberg.marts.limit',
            'SELECT "from" FROM "iceberg"."marts"."t" "where"',
            'WITH x AS (SELECT * FROM iceberg.marts.t) SELECT * FROM x',
            'SELECT * FROM iceberg.marts.t limit, iceberg.marts.other',
            'SELECT * FROM iceberg.marts.t a JOIN iceberg.marts.u b ON a.id=b.id LIMIT 5',
            'SELECT * FROM (SELECT * FROM iceberg.marts.t) x',
        ]
        with patch.object(wb.warehouse_resident, 'query', return_value={}) as query:
            for sql in denied + allowed:
                expected = 403 if sql in denied else 200
                with self.subTest(sql=sql):
                    query.reset_mock()
                    self.assertEqual(self.workbench('/api/warehouse/query', {'sql': sql}, 'vera'), expected)
                    self.assertEqual(query.called, expected == 200)
                    code, _, _, backend, *_ = self.endpoint_request('POST', '/v1/statement', sql, headers=self.headers('vera'))
                    self.assertEqual(code, expected)
                    self.assertEqual(backend.request.called, expected == 200)

    def test_endpoint_continuations_are_bound_to_identity(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), wb.WorkbenchHandler)
        client = http.client.HTTPConnection(*server.server_address, timeout=3)
        backend = Mock()
        response = backend.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps({'nextUri': 'http://backend/v1/statement/executing/query1/0/1',
            'partialCancelUri': 'http://backend/v1/statement/partialCancel/query1/0'}).encode()
        response.getheaders.return_value = []
        response.getheader.return_value = ''
        ready = threading.Event()
        def factory(address, handler):
            server.RequestHandlerClass = handler
            ready.set()
            return server
        with patch.object(w.http.server, 'ThreadingHTTPServer', side_effect=factory), \
             patch.object(w.http.client, 'HTTPConnection', return_value=backend), \
             patch.object(w, 'trino', return_value=nullcontext('http://127.0.0.1:1234')), \
             patch.object(w, 'status', return_value={'replicas': 1}):
            thread = threading.Thread(target=w.serve_endpoint, daemon=True)
            thread.start()
            self.assertTrue(ready.wait(3))
            try:
                def request(method, path, user=None, body=None):
                    client.request(method, path, body=body, headers=self.headers(user, **{'X-Trino-User': 'alice'}))
                    result = client.getresponse()
                    result.read()
                    return result.status
                self.assertEqual(request('POST', '/v1/statement', 'alice', 'SELECT 1'), 200)
                for method, path in [('GET', '/v1/statement/executing/query1/0/1'),
                                     ('DELETE', '/v1/statement/partialCancel/query1/0')]:
                    self.assertEqual(request(method, path, 'vera'), 403)
                    self.assertEqual(request(method, path), 401)
                    self.assertEqual(request(method, path, 'alice'), 200)
                self.assertEqual(request('GET', '/v1/statement/executing/unknown/0/1', 'alice'), 403)
                self.assertEqual(backend.request.call_count, 3)
            finally:
                client.close()
                server.shutdown()
                thread.join(3)
                server.server_close()

    def test_shared_startup_requires_secure_backend(self):
        with self.assertRaisesRegex(auth.AuthenticationError, 'password-authenticated'):
            validate_shared_backend()
        with patch.object(trino_auth, 'password_mode', return_value=True), \
             patch.object(trino_auth, 'load_credentials') as credentials, \
             patch.object(trino_auth, 'backend_ssl_context') as tls:
            validate_shared_backend()
            credentials.assert_called_once()
            tls.assert_called_once()

    def test_local_mode_cannot_bind_publicly(self):
        with patch.dict(os.environ, SHIROKUMA_AUTH_MODE='local', SHIROKUMA_AUTH_FILE=''):
            auth.validate_bind('127.0.0.1')
            with self.assertRaises(auth.AuthenticationError):
                auth.validate_bind('0.0.0.0')


if __name__ == '__main__':
    unittest.main()
