"""Opt-in acceptance against the running password/TLS Trino lab.

Run only with SHIROKUMA_RUNTIME_TESTS=1 and configured backend credentials/CA.
Uses unique synthetic tables and deletes only those tables at completion.
"""
import base64
from contextlib import ExitStack
import http.client
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import request_auth as auth
import trino_auth
import warehouse_resident as w
import workbench_server as wb


@unittest.skipUnless(os.environ.get('SHIROKUMA_RUNTIME_TESTS') == '1', 'opt-in real TLS/Trino acceptance')
class RuntimeSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        if not trino_auth.password_mode():
            raise RuntimeError('Runtime security acceptance requires password-mode Trino')
        trino_auth.load_credentials()
        trino_auth.backend_ssl_context()
        cls.temp = cls.stack.enter_context(tempfile.TemporaryDirectory())
        cls.passwords = {user: secrets.token_urlsafe(24) for user in ('admin', 'engineer', 'viewer')}
        accounts = {}
        for user, role in [('admin', 'admin'), ('engineer', 'data_engineer'), ('viewer', 'bi_viewer')]:
            salt = secrets.token_hex(16)
            accounts[user] = dict(role=role, salt=salt, password_hash=auth.password_hash(cls.passwords[user], salt))
        path = Path(cls.temp) / 'users.json'
        path.write_text(json.dumps(accounts))
        path.chmod(0o600)
        cls.stack.enter_context(patch.dict(os.environ, SHIROKUMA_AUTH_MODE='shared', SHIROKUMA_AUTH_FILE=str(path)))
        cls.backend = cls.stack.enter_context(w.persistent_trino())
        cls.name = 'auth_acceptance_' + secrets.token_hex(6)
        cls.tables = ['iceberg.' + schema + '.' + cls.name for schema in ('marts', 'raw', 'sandbox')]
        cls.created_tables = []
        cls.stack.callback(cls.cleanup_tables)
        for schema in ('marts', 'raw', 'sandbox'):
            with auth.effective_role('admin'):
                w.execute(cls.backend, 'CREATE SCHEMA IF NOT EXISTS iceberg.' + schema, {})
        for table in cls.tables:
            with auth.effective_role('admin'):
                w.execute(cls.backend, 'CREATE TABLE ' + table + ' AS SELECT 7 AS id', {})
        cls.web = ThreadingHTTPServer(('127.0.0.1', 0), wb.WorkbenchHandler)
        cls.start_server(cls.web)
        cls.endpoint = ThreadingHTTPServer(('127.0.0.1', 0), wb.WorkbenchHandler)
        ready = threading.Event()
        def factory(address, handler):
            cls.endpoint.RequestHandlerClass = handler
            ready.set()
            return cls.endpoint
        with patch.object(w.http.server, 'ThreadingHTTPServer', side_effect=factory):
            thread = threading.Thread(target=w.serve_endpoint, daemon=True)
            thread.start()
            if not ready.wait(5):
                raise RuntimeError('Authenticated endpoint did not start')
        cls.stack.callback(cls.stop_server, cls.endpoint, thread)

    @classmethod
    def cleanup_tables(cls):
        with auth.effective_role('admin'):
            for table in cls.tables + cls.created_tables:
                w.execute(cls.backend, 'DROP TABLE IF EXISTS ' + table, {})

    @classmethod
    def start_server(cls, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cls.stack.callback(cls.stop_server, server, thread)

    @staticmethod
    def stop_server(server, thread):
        server.shutdown()
        thread.join(5)
        server.server_close()

    @classmethod
    def headers(cls, user, **extra):
        headers = dict(extra)
        if user:
            encoded = base64.b64encode((user + ':' + cls.passwords[user]).encode()).decode()
            headers['Authorization'] = 'Basic ' + encoded
        return headers

    def http(self, server, method, path, body=None, user='viewer', headers=None):
        client = http.client.HTTPConnection(*server.server_address, timeout=60)
        try:
            client.request(method, path, body=body, headers=self.headers(user, **(headers or {})))
            response = client.getresponse()
            return response.status, json.loads(response.read() or '{}')
        finally:
            client.close()

    def endpoint_sql(self, sql, user='viewer', **headers):
        code, page = self.http(self.endpoint, 'POST', '/v1/statement', sql, user, headers)
        while code == 200 and page.get('nextUri') and not page.get('error'):
            target = urllib.parse.urlsplit(page['nextUri'])
            code, page = self.http(self.endpoint, 'GET', target.path + ('?' + target.query if target.query else ''), user=user)
        return code, page

    def workbench_sql(self, sql, user='viewer', mode='read-only'):
        return self.http(self.web, 'POST', '/api/warehouse/query', json.dumps(dict(sql=sql, mode=mode)), user,
                         {'Content-Type': 'application/json', 'X-Trino-User': 'admin'})

    def backend_sql(self, sql, headers):
        page = None
        url = self.backend + '/v1/statement'
        data = sql.encode()
        while True:
            request = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(request, context=trino_auth.backend_ssl_context(), timeout=60) as response:
                page = json.load(response)
            if page.get('error') or not page.get('nextUri'):
                return page
            url, data = page['nextUri'], None
            trino_auth.validate_backend_url(url)

    def test_direct_backend_authentication_and_three_role_acl(self):
        for headers in ({}, {'Authorization': 'Basic ' + base64.b64encode(b'shiro:wrong').decode()}):
            with self.assertRaises(urllib.error.HTTPError) as failure:
                self.backend_sql('SELECT 1', headers)
            self.assertIn(failure.exception.code, (401, 403))
        viewer = trino_auth.backend_headers(role='bi_viewer')
        impersonated = dict(viewer, **{'X-Trino-User': 'shirokuma_admin'})
        page = self.backend_sql('SELECT 1', impersonated)
        self.assertEqual(page.get('error', {}).get('errorName'), 'PERMISSION_DENIED')
        self.assertIn('cannot impersonate', page['error']['message'])
        self.assertFalse(page.get('data'))
        page = self.backend_sql('SELECT * FROM ' + self.tables[0], viewer)
        self.assertNotIn('error', page)
        for sql in ('SELECT * FROM ' + self.tables[1],
                    'INSERT INTO ' + self.tables[0] + ' VALUES 8',
                    'SELECT * FROM system.runtime.queries'):
            self.assertIn('error', self.backend_sql(sql, viewer), sql)
        engineer = trino_auth.backend_headers(role='data_engineer')
        for table in self.tables[1:]:
            created = table + '_engineer'
            self.created_tables.append(created)
            self.assertNotIn('error', self.backend_sql('CREATE TABLE ' + created + ' AS SELECT 11 AS id', engineer))
            self.assertNotIn('error', self.backend_sql('INSERT INTO ' + table + ' VALUES 8', engineer))
        self.assertIn('error', self.backend_sql('INSERT INTO ' + self.tables[0] + ' VALUES 8', engineer))

    def test_direct_pod_plaintext_rejects_unauthenticated_sql(self):
        selector = ','.join(key + '=' + value for key, value in w.deployment()['spec']['selector']['matchLabels'].items())
        pods = json.loads(w.kubectl('get', 'pods', '-l', selector, '-o', 'json'))['items']
        pod = next(item['metadata']['name'] for item in pods if item.get('status', {}).get('phase') == 'Running')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        process = subprocess.Popen(w.KUBECTL + ['port-forward', '--address', '127.0.0.1',
            'pod/' + pod, str(port) + ':8080'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        def listening():
            if process.poll() is not None:
                raise RuntimeError('Plaintext probe tunnel exited before readiness')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2):
                    return True
            except OSError:
                return False
        try:
            w.wait_for(listening)
            for headers in ({}, {'X-Trino-User': 'shirokuma_admin'}):
                client = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                try:
                    client.request('POST', '/v1/statement', body='SELECT 1', headers=headers)
                    response = client.getresponse()
                    body = response.read().decode('utf-8')
                    self.assertEqual(response.status, 403)
                    self.assertIn('Authentication over HTTP is not enabled', body)
                finally:
                    client.close()
        finally:
            process.terminate()
            process.communicate(timeout=5)

    def test_real_http_entrances_and_alias_variants(self):
        marts, raw, sandbox = self.tables
        for sql in ('SELECT * FROM iceberg.marts.limit, ' + raw,
                    'SELECT * FROM ' + marts + ' limit, ' + raw,
                    'SELECT * FROM ' + marts + ' AS limit, ' + raw,
                    'SELECT * FROM ' + marts + ' window, ' + raw,
                    'WITH q AS (SELECT * FROM ' + raw + ') SELECT * FROM q',
                    'SELECT * FROM ' + marts + ' a JOIN ' + raw + ' b ON a.id=b.id'):
            self.assertEqual(self.workbench_sql(sql)[0], 403, sql)
            self.assertEqual(self.endpoint_sql(sql, **{'X-Trino-User': 'admin'})[0], 403, sql)
        self.assertEqual(self.workbench_sql('SELECT * FROM ' + marts)[0], 200)
        code, page = self.endpoint_sql('SELECT * FROM ' + marts)
        self.assertEqual(code, 200)
        self.assertNotIn('error', page)
        self.assertEqual(self.http(self.web, 'GET', '/api/session', user=None)[0], 401)
        self.assertEqual(self.http(self.web, 'GET', '/api/ai/config', user='viewer',
                                  headers={'X-Shirokuma-Role': 'admin'})[0], 403)
        self.assertEqual(self.http(self.web, 'POST', '/api/bears/run',
            json.dumps(dict(bear='catalog', action='describe', table=raw)),
            headers={'Content-Type': 'application/json'})[0], 403)
        for table in (raw, sandbox):
            self.assertEqual(self.workbench_sql('INSERT INTO ' + table + ' VALUES 9', 'engineer', 'sandbox-write')[0], 200)
            code, page = self.endpoint_sql('INSERT INTO ' + table + ' VALUES 10', 'engineer')
            self.assertEqual(code, 200)
            self.assertNotIn('error', page)
        self.assertEqual(self.workbench_sql('INSERT INTO ' + marts + ' VALUES 9', 'engineer', 'sandbox-write')[0], 403)
        self.assertEqual(self.endpoint_sql('INSERT INTO ' + marts + ' VALUES 9', 'engineer')[0], 403)


if __name__ == '__main__':
    unittest.main()
