import base64
import hashlib
import importlib.util
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import io
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/local_experiment'))
SPEC = importlib.util.spec_from_file_location('trino_auth', ROOT / 'tools/local_experiment/trino_auth.py')
auth = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auth)
IMAGE_SPEC = importlib.util.spec_from_file_location('trino_auth_image', ROOT / 'tools/local_experiment/trino_auth_image.py')
image_builder = importlib.util.module_from_spec(IMAGE_SPEC)
IMAGE_SPEC.loader.exec_module(image_builder)
SECURE = ROOT / 'deploy/local-experiment/trino/secure'


class TrinoAuthTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'backend.json'
        self.config = {role: dict(username=user, password='test-' + role)
                       for role, user in auth.SERVICE_USERS.items()}
        self.path.write_text(json.dumps(self.config))
        self.path.chmod(0o600)
        environment = patch.dict(os.environ, {'SHIROKUMA_TRINO_AUTH_MODE': 'password',
                               'SHIROKUMA_TRINO_AUTH_FILE': str(self.path),
                               'PATH': os.environ.get('PATH', os.defpath)}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_role_credentials_replace_untrusted_identity_case_insensitively(self):
        supplied = {'authorization': 'Bearer client-secret', 'x-trino-user': 'shirokuma_admin',
                    'X-Trino-Original-User': 'shirokuma_admin', 'X-Trino-Role': 'ALL',
                    'X-Trino-Original-Roles': 'ALL', 'X-Trino-Extra-Credential': 'secret=value',
                    'Proxy-Authorization': 'Basic client', 'X-Trino-Catalog': 'iceberg'}
        for role in auth.SERVICE_USERS:
            with self.subTest(role=role):
                result = auth.backend_headers(supplied, role=role)
                credential = self.config[role]
                expected = base64.b64encode((credential['username'] + ':' + credential['password']).encode()).decode()
                self.assertEqual(result, {'Authorization': 'Basic ' + expected,
                    'X-Trino-User': credential['username'], 'X-Trino-Catalog': 'iceberg'})
        self.assertEqual(supplied['authorization'], 'Bearer client-secret')

    def test_local_mode_is_explicit_and_invalid_modes_fail(self):
        supplied = {'X-Trino-User': 'local'}
        with patch.dict(os.environ, {'SHIROKUMA_TRINO_AUTH_MODE': 'local'}):
            self.assertEqual(auth.backend_headers(supplied), supplied)
            self.assertIsNot(auth.backend_headers(supplied), supplied)
        with patch.dict(os.environ, {'SHIROKUMA_TRINO_AUTH_MODE': 'typo'}):
            with self.assertRaises(RuntimeError):
                auth.backend_headers(supplied)

    def test_missing_role_or_credential_file_fails_closed(self):
        for role in (None, '', 'engineer', 'unknown'):
            with self.assertRaises(PermissionError):
                auth.backend_headers(role=role)
        self.path.unlink()
        with self.assertRaises(RuntimeError):
            auth.backend_headers(role='admin')
        with patch.dict(os.environ, {'SHIROKUMA_TRINO_AUTH_FILE': ''}):
            with self.assertRaises(RuntimeError):
                auth.backend_headers(role='admin')

    def test_malformed_credentials_and_permissions_fail_without_secret_errors(self):
        invalid = [[], {}, {**self.config, 'extra': {}},
                   {**self.config, 'admin': {'username': 'wrong', 'password': 'sensitive'}},
                   {**self.config, 'admin': {'username': 'shirokuma_admin', 'password': ''}},
                   {**self.config, 'admin': {'username': 'shirokuma_admin', 'password': 5}},
                   {role: {**value, 'password': 'sensitive'} for role, value in self.config.items()}]
        for value in invalid:
            self.path.write_text(json.dumps(value))
            with self.assertRaises(RuntimeError) as failure:
                auth.backend_headers(role='admin')
            self.assertNotIn('sensitive', str(failure.exception))
        self.path.write_text('{malformed')
        with self.assertRaises(RuntimeError):
            auth.backend_headers(role='admin')
        self.path.write_text(json.dumps(self.config))
        self.path.chmod(0o644)
        with self.assertRaises(RuntimeError):
            auth.backend_headers(role='admin')

    def test_configuration_is_reloaded_after_rotation(self):
        before = auth.backend_headers(role='admin')
        self.config['admin']['password'] = 'rotated-test-value'
        self.path.write_text(json.dumps(self.config))
        self.assertNotEqual(auth.backend_headers(role='admin'), before)

    def test_password_mode_ai_requests_keep_provider_credentials(self):
        import bears_resident as bears
        import trino_auth as backend_auth
        active = dict(provider='openai', model='ai-model', api_url='https://ai.invalid/v1',
                      api_key='ai-provider-secret', api_key_env='', timeout=5,
                      temperature=0, max_tokens=10)
        captured = []

        def capture(request, **options):
            captured.append(request)
            return io.BytesIO(json.dumps(dict(choices=[dict(message=dict(content='SELECT 1'))])).encode())

        paths = (
            lambda: self.assertEqual(bears.test_ai_connection()['status'], 'connected'),
            lambda: self.assertEqual(bears.generate_data_sql('trino', 'one'), 'SELECT 1'),
            lambda: self.assertEqual(bears.generate_data_sql_retry('trino', 'one', [],
                                      'SELECT broken', 'unknown column'), 'SELECT 1'),
        )
        with patch.object(bears, 'load_ai_config', side_effect=lambda: dict(active)), \
             patch.object(bears, 'sql', return_value={'rows': []}), \
             patch.object(bears.wr.urllib.request, 'urlopen', side_effect=capture), \
             patch.object(backend_auth, 'backend_ssl_context', return_value=None) as backend_tls:
            for call in paths:
                call()
                request = captured[-1]
                self.assertEqual(request.full_url, 'https://ai.invalid/v1/chat/completions')
                self.assertEqual(request.get_header('Authorization'), 'Bearer ai-provider-secret')
                self.assertIsNone(request.get_header('X-trino-user'))
                self.assertNotIn('Basic ', str(request.headers))
            backend_tls.assert_not_called()
        self.assertEqual(len(captured), 3)

    def test_password_mode_rejects_plaintext_urls_and_missing_ca(self):
        for url in ('http://127.0.0.1:8080', 'https://user:password@localhost', 'https:relative'):
            with self.assertRaises(RuntimeError):
                auth.validate_backend_url(url)
        auth.validate_backend_url('https://127.0.0.1:8443')
        with self.assertRaises(RuntimeError):
            auth.backend_ssl_context()
        with patch.dict(os.environ, {'SHIROKUMA_TRINO_CA_FILE': str(self.path)}):
            with self.assertRaises(RuntimeError):
                auth.backend_ssl_context()

    @unittest.skipUnless(shutil.which('htpasswd') and shutil.which('openssl'), 'htpasswd/openssl not installed')
    def test_generated_password_database_authenticates_each_private_service_user(self):
        directory = self.path.parent / 'auth'
        auth.initialize(directory)
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        with patch.dict(os.environ, {'SHIROKUMA_TRINO_AUTH_FILE': str(directory / 'backend.json')}):
            credentials = auth.load_credentials()
        for name in ('backend.json', 'password.db', 'shared-secret', 'tls.key', 'tls.crt', 'tls.pem'):
            self.assertEqual((directory / name).stat().st_mode & 0o777, 0o600)
        for entry in credentials.values():
            result = subprocess.run([shutil.which('htpasswd'), '-vi', str(directory / 'password.db'),
                                     entry['username']], input=entry['password'] + '\n',
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0)
        with self.assertRaises(FileExistsError):
            auth.initialize(directory)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(self.headers.get('Authorization', '').encode())

            def log_message(self, *args):
                pass

        server = HTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(directory / 'tls.pem')
        server.socket = context.wrap_socket(server.socket, server_side=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join)
        self.addCleanup(server.shutdown)
        url = 'https://127.0.0.1:' + str(server.server_port)
        with patch.dict(os.environ, {'SHIROKUMA_TRINO_CA_FILE': str(directory / 'tls.crt')}):
            headers = auth.backend_headers(role='bi_viewer')
            with urlopen(Request(url, headers=headers), context=auth.backend_ssl_context()) as response:
                self.assertEqual(response.read().decode(), headers['Authorization'])

    def test_acl_denies_raw_writes_and_query_history_for_viewer(self):
        rules = json.loads((SECURE / 'rules.json').read_text())

        def permissions(user, catalog, schema, table):
            for rule in rules['tables']:
                if all(re.fullmatch(rule.get(key, '.*'), value)
                       for key, value in [('user', user), ('catalog', catalog),
                                          ('schema', schema), ('table', table)]):
                    return set(rule['privileges'])
            return set()

        viewer = auth.SERVICE_USERS['bi_viewer']
        engineer = auth.SERVICE_USERS['data_engineer']
        self.assertEqual(permissions(viewer, 'iceberg', 'marts', 'sales'), {'SELECT'})
        for schema in ('raw', 'sandbox'):
            self.assertEqual(permissions(viewer, 'iceberg', schema, 'secret'), set())
            self.assertIn('OWNERSHIP', permissions(engineer, 'iceberg', schema, 'new_table'))
        for user in (viewer, engineer):
            self.assertEqual(permissions(user, 'system', 'runtime', 'queries'), set())
            query = next(rule for rule in rules['queries'] if re.fullmatch(rule['user'], user))
            self.assertEqual(query['allow'], ['execute'])
        self.assertEqual(rules['impersonation'], [])
        self.assertEqual(rules['catalogs'][-1], {'allow': 'none'})

    @unittest.skipUnless(shutil.which('kubectl'), 'kubectl is not installed')
    def test_secure_manifest_renders_authentication_secret_and_acl(self):
        result = subprocess.run(['kubectl', 'kustomize', '--load-restrictor', 'LoadRestrictionsNone',
                                 str(SECURE)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for required in ('http-server.authentication.type=PASSWORD', 'file.password-file=/etc/trino-auth/password.db',
                         'access-control.name=file', 'path: access-control.properties',
                         'path: rules.json', 'secretName: trino-local-auth', 'ingress: []',
                         'name: TRINO_INTERNAL_SHARED_SECRET', 'targetPort: https',
                         'http-server.authentication.allow-insecure-over-http=false',
                         'http-server.https.enabled=true'):
            self.assertIn(required, result.stdout)


class TrinoAuthImageTests(unittest.TestCase):
    def test_builder_materializes_distribution_hardlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'distribution.tar.gz'
            with tarfile.open(archive, 'w:gz') as output:
                dependency = tarfile.TarInfo('trino-server-483/lib/shared.jar')
                dependency.size = 3
                output.addfile(dependency, io.BytesIO(b'jar'))
                linked = tarfile.TarInfo('trino-server-483/plugin/password-authenticators/shared.jar')
                linked.type = tarfile.LNKTYPE
                linked.linkname = dependency.name
                output.addfile(linked)
                main = tarfile.TarInfo('trino-server-483/plugin/password-authenticators/trino-password-authenticators-483.jar')
                main.size = 4
                output.addfile(main, io.BytesIO(b'main'))
            image = [{'Config': {'Labels': {'shirokuma.dev.upstream-archive-sha256':
                      hashlib.sha256(archive.read_bytes()).hexdigest()}}}]

            def build(arguments, **kwargs):
                plugin = Path(arguments[-1]) / 'password-authenticators'
                self.assertEqual((plugin / 'shared.jar').read_bytes(), b'jar')
                self.assertFalse((plugin / 'shared.jar').is_symlink())
                self.assertEqual(len(list(plugin.iterdir())), 2)

            with patch.object(image_builder.subprocess, 'check_output', return_value=json.dumps(image)), \
                 patch.object(image_builder.subprocess, 'run', side_effect=build) as docker:
                image_builder.build(archive)
                docker.assert_called_once()

    def test_builder_rejects_archive_from_another_distribution(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'wrong.tar.gz'
            archive.write_bytes(b'wrong')
            image = [{'Config': {'Labels': {'shirokuma.dev.upstream-archive-sha256': 'mismatch'}}}]
            with patch.object(image_builder.subprocess, 'check_output', return_value=json.dumps(image)), \
                 patch.object(image_builder.subprocess, 'run') as docker:
                with self.assertRaisesRegex(RuntimeError, 'approved base image'):
                    image_builder.build(archive)
                docker.assert_not_called()


if __name__ == '__main__':
    unittest.main()
