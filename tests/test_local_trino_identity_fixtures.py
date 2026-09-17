"""Real local crypto and mocked image builder checks; no runtime/network calls."""
import base64
import copy
import json
import os
from pathlib import Path
import ssl
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid

from tools.local_experiment.trino_identity import fixtures as F
from tools.local_experiment.trino_identity import build as B
from tools.local_experiment.trino_identity.support import ExperimentError, digest, write


def decode(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name).resolve()
        cls.run_token = uuid.uuid4().hex
        cls.output = cls.root / cls.run_token
        cls.output.mkdir(mode=0o700)
        cls.now = 1800000000
        cls.fixture = F.create_fixtures(cls.run_token, cls.output, now=cls.now)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_private_files_and_no_overwrite(self):
        folder = self.output / 'fixtures'
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in folder.iterdir()))
        self.assertEqual(self.fixture['hashes'], {p.name: digest(p.read_bytes()) for p in sorted(folder.iterdir())})
        with self.assertRaisesRegex(ExperimentError, 'FIXTURE_COLLISION'):
            F.create_fixtures(self.run_token, self.output)

    def test_rejects_nonprivate_run_and_symlink(self):
        value = uuid.uuid4().hex
        output = self.root / value
        output.mkdir(mode=0o755)
        with self.assertRaisesRegex(ExperimentError, 'NONPRIVATE_RUN_DIRECTORY'):
            F.create_fixtures(value, output)
        output.chmod(0o700)
        (output / 'fixtures').symlink_to(self.output / 'fixtures', target_is_directory=True)
        with self.assertRaisesRegex(ExperimentError, 'FIXTURE_COLLISION'):
            F.create_fixtures(value, output)

    def test_tls_is_localhost_ca_validated_and_distinct_from_signing_key(self):
        cert = ssl._ssl._test_decode_cert(str(self.output / 'fixtures/tls.crt'))
        self.assertEqual(set(cert['subjectAltName']), {('DNS', 'localhost'), ('IP Address', '127.0.0.1')})
        context = ssl.create_default_context(cafile=str(self.fixture['ca_path']))
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        public = subprocess.run(['openssl', 'x509', '-in', str(self.output / 'fixtures/tls.crt'), '-pubkey', '-noout'],
                                capture_output=True, check=True).stdout
        self.assertNotEqual(public, self.fixture['public_data']['jwt-public.pem'].encode())
        self.assertNotEqual((self.output / 'fixtures/ca.key').read_bytes(),
                            (self.output / 'fixtures/realm-signing.key').read_bytes())

    def test_realm_has_only_synthetic_users_and_fixed_direct_grant_client(self):
        realm = json.loads(self.fixture['secret_data']['realm.json'])
        self.assertEqual(tuple(user['username'] for user in realm['users']), F.USERS)
        self.assertEqual(realm['sslRequired'], 'all')
        self.assertFalse(realm['registrationAllowed'])
        self.assertEqual(len(realm['clients']), 1)
        client = realm['clients'][0]
        self.assertTrue(client['directAccessGrantsEnabled'])
        for name in ('standardFlowEnabled', 'implicitFlowEnabled', 'serviceAccountsEnabled', 'fullScopeAllowed', 'publicClient'):
            self.assertFalse(client[name])
        self.assertEqual(client['redirectUris'], [])
        self.assertEqual(client['webOrigins'], [])
        subject = [m for m in client['protocolMappers'] if m['protocolMapper'] == 'oidc-sub-mapper']
        self.assertEqual(len(subject), 1)
        self.assertEqual(subject[0]['config'], {'access.token.claim': 'true'})
        for user in realm['users']:
            self.assertFalse(user['credentials'][0]['temporary'])
            self.assertEqual(user['requiredActions'], [])
            self.assertEqual(user['credentials'][0]['value'], self.fixture['credentials']['users'][user['username']])
        component = realm['components']['org.keycloak.keys.KeyProvider'][0]
        self.assertEqual(component['providerId'], 'rsa')
        self.assertEqual(component['config']['algorithm'], ['RS256'])
        self.assertEqual(component['config']['privateKey'][0], (self.output / 'fixtures/realm-signing.key').read_text())
        self.assertTrue(component['config']['privateKey'][0].startswith('-----BEGIN ' + 'PRIVATE KEY-----'))

    def test_negative_claims_change_only_the_target(self):
        tokens = self.fixture['negative_tokens']
        self.assertEqual(set(tokens), {'control', *F.NEGATIVE_NAMES})
        baseline = json.loads(decode(tokens['control'].split('.')[1]))
        expected = {'wrong_issuer': 'iss', 'wrong_audience': 'aud', 'expired': 'exp',
                    'unknown_principal': 'preferred_username'}
        for name, changed in expected.items():
            claims = json.loads(decode(tokens[name].split('.')[1]))
            self.assertEqual({key for key in claims if claims[key] != baseline[key]}, {changed})
        self.assertLess(json.loads(decode(tokens['expired'].split('.')[1]))['exp'], self.now)
        self.assertEqual(json.loads(decode(tokens['bad_signature'].split('.')[1])), baseline)

    def test_negative_claims_are_cryptographically_signed_not_corrupted(self):
        for name, value in self.fixture['negative_tokens'].items():
            parts = value.split('.')
            self.assertEqual(json.loads(decode(parts[0])), {'alg': 'RS256', 'typ': 'JWT'})
            signature = self.root / ('signature-' + name)
            F._private_write(signature, decode(parts[2]))
            result = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', str(self.fixture['public_key_path']),
                                     '-signature', str(signature)],
                                    input='.'.join(parts[:2]).encode(), capture_output=True)
            self.assertEqual(result.returncode == 0, name != 'bad_signature', name)

    def test_public_projection_contains_no_credentials_or_private_keys(self):
        public = json.dumps(self.fixture['public_data'])
        self.assertNotIn('PRIVATE KEY', public)
        self.assertNotIn(self.fixture['credentials']['client_secret'], public)
        for password in self.fixture['credentials']['users'].values():
            self.assertNotIn(password, public)

    def test_crypto_failure_does_not_expose_tool_error(self):
        result = subprocess.CompletedProcess([], 1, b'', b'sensitive-private-material')
        with patch.object(F.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(ExperimentError, '^FIXTURE_CRYPTO_FAILED$'):
                F._openssl(['version'])


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.run_token = uuid.uuid4().hex
        self.output = self.root / self.run_token
        self.output.mkdir(mode=0o700)
        self.calls = []

    def tearDown(self):
        self.temporary.cleanup()

    def fake_docker(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if args[:2] == ('context', 'inspect'):
            return json.dumps([{'Endpoints': {'docker': {'Host': 'unix:///Users/lab/.colima/mac-studio-solo/docker.sock'}}}])
        if args[:2] == ('image', 'ls'):
            return ''
        if args[:2] == ('image', 'inspect'):
            ref = args[2]
            component = 'trino-identity-keycloak' if ref.startswith(B.REPOSITORY) else 'trino-opa-shadow'
            refs = [ref] if '@' in ref else [B.REPOSITORY + '@sha256:' + 'a' * 64]
            return json.dumps([{'Architecture': 'arm64', 'Os': 'linux', 'Size': 1234, 'RepoDigests': refs,
                                'Config': {'Labels': {'shirokuma.dev.component': component}}}])
        return ''

    def test_build_scoped_no_network_build_no_runtime_commands(self):
        with patch.object(B, '_docker', side_effect=self.fake_docker), patch.object(B.opa_common, 'load_build', return_value={'images': B.PINS['images']}):
            result = B.build(self.output, self.run_token)
        self.assertEqual(result['result'], 'passed')
        self.assertEqual(set(result['images']), {'trino', 'opa', 'keycloak'})
        build_args = [args for args, _ in self.calls if args[0] == 'build'][0]
        self.assertIn('--network=none', build_args)
        self.assertIn('--pull=false', build_args)
        self.assertEqual(build_args[build_args.index('--platform') + 1], 'linux/arm64')
        self.assertTrue(all(args[0] in {'context', 'image', 'pull', 'build'} for args, _ in self.calls))
        self.assertEqual(result['images']['trino'], B.PINS['images']['trino'])
        self.assertEqual(stat.S_IMODE((self.output / 'build.json').stat().st_mode), 0o600)

    def test_context_mismatch_prevents_pull(self):
        with patch.object(B, '_docker', return_value=json.dumps([{'Endpoints': {'docker': {'Host': 'tcp://remote:2375'}}}])) as docker:
            with self.assertRaisesRegex(ExperimentError, 'DOCKER_CONTEXT_MISMATCH'):
                B.build(self.output, self.run_token)
        self.assertEqual(docker.call_count, 1)
        self.assertEqual(json.loads((self.output / 'build.json').read_text())['result'], 'failed')

    def test_architecture_and_digest_are_checked(self):
        value = {'Architecture': 'amd64', 'Os': 'linux', 'RepoDigests': []}
        with patch.object(B, '_docker', return_value=json.dumps([value])):
            with self.assertRaisesRegex(ExperimentError, 'IMAGE_NOT_ARM64'):
                B._image(B.PINS['keycloak']['source_image'])
        value['Architecture'] = 'arm64'
        with patch.object(B, '_docker', return_value=json.dumps([value])):
            with self.assertRaisesRegex(ExperimentError, 'IMAGE_DIGEST_MISMATCH'):
                B._image(B.PINS['keycloak']['source_image'])

    def test_tag_collision_refuses_overwrite(self):
        def fake(*args, **kwargs):
            if args[:2] == ('image', 'ls'):
                return 'existing-image'
            return self.fake_docker(*args, **kwargs)
        with patch.object(B, '_docker', side_effect=fake), patch.object(B.opa_common, 'load_build', return_value={'images': B.PINS['images']}):
            with self.assertRaisesRegex(ExperimentError, 'IMAGE_TAG_COLLISION'):
                B.build(self.output, self.run_token)
        self.assertFalse(any(args[0] == 'build' for args, _ in self.calls))

    def test_source_build_identity_mismatch_prevents_pull(self):
        with patch.object(B, '_docker', side_effect=self.fake_docker), patch.object(B.opa_common, 'load_build', return_value={'images': {}}):
            with self.assertRaisesRegex(ExperimentError, 'OPA_BUILD_MISMATCH'):
                B.build(self.output, self.run_token)
        self.assertFalse(any(args[0] == 'pull' for args, _ in self.calls))

    def test_load_rejects_receipt_or_input_drift(self):
        state = self.root / 'state'
        output = state / 'builds' / self.run_token
        output.mkdir(parents=True, mode=0o700)
        value = {'result': 'passed', 'build_token': self.run_token, 'inputs_sha256': B.inputs_hash(),
                 'images': dict(B.PINS['images'], keycloak=B.REPOSITORY + '@sha256:' + 'a' * 64)}
        write(output / 'build.json', value)
        with patch.object(B, 'STATE', state):
            self.assertEqual(B.load_build(self.run_token), value)
            changed = copy.deepcopy(value)
            changed['images']['keycloak'] = 'external.example/keycloak@sha256:' + 'a' * 64
            write(output / 'build.json', changed)
            with self.assertRaisesRegex(ExperimentError, 'BUILD_IDENTITY_MISMATCH'):
                B.load_build(self.run_token)
            write(output / 'build.json', value)
            with patch.object(B, 'inputs_hash', return_value='changed'):
                with self.assertRaisesRegex(ExperimentError, 'BUILD_IDENTITY_MISMATCH'):
                    B.load_build(self.run_token)

    def test_build_assets_have_no_mutable_image_or_credentials(self):
        source = (B.DEPLOY / 'Dockerfile').read_text()
        self.assertIn('FROM ' + B.PINS['keycloak']['source_image'], source)
        self.assertIn('build --db=dev-file --health-enabled=true', source)
        self.assertNotIn('latest', source)
        self.assertNotIn('COPY', source)
        self.assertNotIn('PASSWORD', source)
        self.assertNotIn('SECRET', source)


if __name__ == '__main__':
    unittest.main()
