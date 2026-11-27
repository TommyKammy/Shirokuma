"""Opt-in acceptance against the running password/TLS Trino lab.

Run only with SHIROKUMA_RUNTIME_TESTS=1 and configured backend credentials/CA.
Uses unique synthetic tables and deletes only those tables at completion.
"""
import base64
from contextlib import ExitStack
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import request_auth as auth
import trino_auth
import warehouse_resident as w


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
        cls.backend = cls.stack.enter_context(w.persistent_trino())
        cls.name = 'auth_acceptance_' + secrets.token_hex(6)
        cls.tables = ['iceberg.' + schema + '.' + cls.name for schema in ('marts', 'raw', 'sandbox')]
        cls.created_tables = []
        cls.stack.callback(cls.cleanup_tables)
        for schema in ('marts', 'raw', 'sandbox'):
            with auth.effective_role('admin'):
                page = cls.backend_sql(cls, 'CREATE SCHEMA IF NOT EXISTS iceberg.' + schema,
                                       trino_auth.backend_headers(role='admin'))
                if page.get('error'):
                    raise RuntimeError(page['error'].get('message', 'backend setup failed'))
        for table in cls.tables:
            with auth.effective_role('admin'):
                page = cls.backend_sql(cls, 'CREATE TABLE ' + table + ' AS SELECT 7 AS id',
                                       trino_auth.backend_headers(role='admin'))
                if page.get('error'):
                    raise RuntimeError(page['error'].get('message', 'backend setup failed'))

    @classmethod
    def cleanup_tables(cls):
        with auth.effective_role('admin'):
            for table in cls.tables + cls.created_tables:
                cls.backend_sql(cls, 'DROP TABLE IF EXISTS ' + table,
                                trino_auth.backend_headers(role='admin'))

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


if __name__ == '__main__':
    unittest.main()
