"""Identity and relation authorization regressions at real localhost HTTP entrances."""
import base64
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
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import request_auth as auth
import trino_auth
validate_shared_backend = auth.validate_shared_backend
import workbench_server as wb
import runtime_profile
from workbench_source import WorkbenchSource


class SecurityBoundaryTests(unittest.TestCase):

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

    def test_session_source_initializes_roles_without_selector(self):
        html = WorkbenchSource().source
        self.assertIn("const response=await fetch('/api/session')", html)
        self.assertIn("option.disabled=ranks[option.value]>ranks[currentRole]", html)

    def test_chart_asset_uses_configured_auth_in_database_mode(self):
        accounts = {user: dict(account, platform_admin=False) for user, account in self.users.items()}
        self.accounts.write_text(json.dumps(accounts))
        with patch.object(runtime_profile, 'active', return_value=True), \
                patch.object(runtime_profile, 'current', return_value=None):
            for path in ('/assets/result_chart.js', '/assets/base_i18n.js', '/assets/saved_lineage.js', '/assets/result_export.js', '/assets/db_admin.js', '/assets/db_dataops.js', '/assets/db_explorer.js', '/assets/db_jobs.js', '/assets/db_stages.js', '/assets/db_marts.js', '/assets/db_dbt.js', '/assets/db_creation.js', '/assets/db_audit.js', '/assets/db_runtime.js', '/assets/workbench.css'):
                with self.subTest(path=path):
                    self.assertEqual(self.workbench(path), 401)
                    self.assertEqual(self.workbench(path, Authorization='Basic invalid'), 401)
                    for user in ('alice', 'emily', 'vera'):
                        self.assertEqual(self.workbench(path, user=user), 200)

    def test_viewer_frontend_bootstrap_uses_authenticated_role(self):
        html = WorkbenchSource().source
        bootstrap = 'async function initializeSession() {' + html.split(
            'async function initializeSession() {', 1)[1].split('\nstartWorkbench();', 1)[0]
        script = """
let currentRole='admin',databaseMode=false;
const session={username:'vera',authenticated:true,maximum_role:'bi_viewer',data_mode:'database'};
const options=['admin','data_engineer','bi_viewer'].map(value=>({value,disabled:false}));
const select={options,value:'admin'};
const calls=[];
const $=()=>select;
function assertViewerRole(){
 if(currentRole!=='bi_viewer'||select.value!=='bi_viewer'||!options[0].disabled||!options[1].disabled||options[2].disabled)throw Error('incorrect role menu');
}
async function initializeDatabaseUI(restoredSession){
 assertViewerRole();
 if(restoredSession!==session)throw Error('incorrect restored session');
 calls.push('database');
 await Promise.resolve();
 calls.push('database-ready');
}
async function fetch(path){calls.push(path);return {ok:true,json:async()=>session};}
""" + bootstrap + """
initializeSession().then(()=>{
 assertViewerRole();
 if(!databaseMode)throw Error('database mode not selected');
 if(JSON.stringify(calls)!==JSON.stringify(['/api/session','database','database-ready']))throw Error('API called before identity or database UI');
}).catch(error=>{console.error(error);process.exit(1);});
"""
        result = subprocess.run(['node', '-'], input=script, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @patch('runtime_profile.active', return_value=False)
    def test_shared_startup_requires_secure_backend(self, _profile_active):
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
