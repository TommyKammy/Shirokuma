"""Synthetic private files only; no runtime or provider calls."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import runtime_profile as rp
import logical_registry as registry
from test_runtime_profile import synthetic_profile


def logical_profile(root):
    location, value = synthetic_profile(root)
    def private(name, data):
        path = root / name
        path.write_text(json.dumps(data))
        path.chmod(0o600)
        return str(path)
    credentials = root / 'credentials'
    credentials.mkdir(mode=0o700)
    config = dict(version=1, environment_id='env', warehouse_id='LOCAL',
                  foundation_generation_id='foundation', foundation_revision=1,
                  credentials_dir=str(credentials), platform_admin_credential_ref='internal-admin',
                  provider_config_file=private('provider.json', {'provider': 'synthetic'}), executor_id='app')
    value.update(version=2, mode='logical_v1', trino_auth_file='', catalog_auth_file='',
                 logical_config_file=private('logical.json', config))
    location.write_text(json.dumps(value))
    return location, value, config


class LogicalRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name in ('_current', '_startup_fingerprint', '_startup_binding', '_startup_identity'):
            mock = patch.object(rp, name, None)
            mock.start(); self.addCleanup(mock.stop)
        env = patch.dict(os.environ, {}, clear=True)
        env.start(); self.addCleanup(env.stop)

    def test_executor_is_required_application_principal_configuration(self):
        location, value, config = logical_profile(self.root)
        for executor in (None, '', '   ', 'app\n', 'app/engine'):
            changed = dict(config)
            if executor is None:
                changed.pop('executor_id')
            else:
                changed['executor_id'] = executor
            Path(value['logical_config_file']).write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                rp.load(location)

    def activate(self):
        location, value, config = logical_profile(self.root)
        os.environ['SHIROKUMA_RUNTIME_PROFILE'] = str(location)
        self.assertTrue(rp.configure_process())
        return location, value, config

    def test_v1_exact_serialization_fingerprint_and_seal(self):
        location, value = synthetic_profile(self.root)
        profile = rp.load(location)
        self.assertEqual(rp.profile_values(profile), value)
        self.assertEqual(rp._public_identity(profile)['profile_ref'],
                         hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest())
        digest = hashlib.sha256()
        names = ('auth_file', 'trino_auth_file', 'trino_ca_file', 'catalog_auth_file', 'client_auth_file')
        for name in names:
            digest.update(value[name].encode() + b'\0' + Path(value[name]).read_bytes() + b'\0')
        self.assertEqual(rp._fingerprint(profile), digest.digest())
        self.assertEqual(set(rp.private_binding(location)['files']), set(names))
        location.write_text(json.dumps(dict(value, logical_config_file='')))
        with self.assertRaises(ValueError):
            rp.load(location)

    def test_logical_attests_only_foundation_and_accepts_live_accounts(self):
        location, value, config = self.activate()
        identity = rp.generation_attestation()
        self.assertEqual(set(identity), {'version', 'mode', 'foundation_id', 'foundation_revision',
                                         'environment_id', 'warehouse_id', 'profile_ref'})
        self.assertEqual(identity['foundation_id'], 'foundation')
        self.assertEqual(identity['version'], 2)
        self.assertNotIn(str(self.root), json.dumps(identity))
        self.assertEqual(rp.auth_overlay({'unchanged': b'yes'}), {'unchanged': b'yes'})
        account_file = Path(value['auth_file'])
        account_file.write_text(json.dumps({'alice': dict(role='bi_viewer', platform_admin=False,
                                      salt='ab' * 16, password_hash='cd' * 32, disabled=True)}))
        self.assertEqual(rp.generation_attestation(), identity)
        account_file.chmod(0o644)
        with self.assertRaises(ValueError):
            rp.generation_attestation()
        account_file.chmod(0o600)
        account_file.write_text('{}')
        with self.assertRaises(ValueError):
            rp.generation_attestation()

    def test_fixed_inputs_are_sealed_and_no_dummy_manifests_loaded(self):
        location, value, config = self.activate()
        for target in (location, Path(value['logical_config_file']), Path(config['provider_config_file']),
                       Path(value['trino_ca_file']), Path(value['client_auth_file'])):
            original = target.read_bytes()
            target.write_bytes(original + b' ')
            with self.subTest(target=target.name), self.assertRaises(ValueError):
                rp.generation_attestation()
            target.write_bytes(original)
            rp.generation_attestation()
        self.assertEqual(os.environ['SHIROKUMA_AUTH_FILE'], value['auth_file'])
        self.assertEqual(os.environ['SHIROKUMA_TRINO_AUTH_FILE'], '')

    def test_exact_config_private_files_and_no_secret_error_output(self):
        location, value, config = logical_profile(self.root)
        config_path = Path(value['logical_config_file'])
        variants = [dict(config, extra=True), dict(config, version=True),
                    dict(config, foundation_revision=0), dict(config, credentials_dir='relative'),
                    dict(config, platform_admin_credential_ref='../secret')]
        for variant in variants:
            config_path.write_text(json.dumps(variant))
            with self.assertRaises(ValueError) as error:
                rp.load(location)
            self.assertNotIn(str(self.root), str(error.exception))
        config_path.write_text(json.dumps(config))
        Path(config['provider_config_file']).chmod(0o644)
        with self.assertRaises(ValueError):
            rp.load(location)

    def test_new_refs_work_without_restart_and_invalid_private_refs_fail_closed(self):
        _, _, config = self.activate()
        identity = rp.generation_attestation()
        directory = Path(config['credentials_dir'])
        def write(ref, data):
            target = directory / (ref + '.json')
            target.write_text(json.dumps(dict(version=1, **data)))
            target.chmod(0o600)
            return target
        row = dict(credential_ref='alice-1', backend_username='alice-backend')
        path = write('alice-1', dict(username='alice-backend', password='synthetic-password'))
        credentials = registry.identity_credentials(row)
        self.assertEqual(credentials['username'], 'alice-backend')
        self.assertNotIn('synthetic-password', repr(credentials))
        self.assertNotIn('synthetic-password', str(credentials))
        write('catalog-1', dict(client_id='consumer', client_secret='synthetic-secret'))
        self.assertEqual(registry.catalog_credentials(dict(catalog_credential_ref='catalog-1'))['client_id'], 'consumer')
        write('internal-admin', dict(username='provisioner', password='synthetic-internal'))
        self.assertEqual(registry.platform_admin_credentials()['username'], 'provisioner')
        with self.assertRaisesRegex(ValueError, 'Internal credential'):
            registry.identity_credentials(dict(credential_ref='internal-admin', backend_username='provisioner'))
        self.assertEqual(rp.generation_attestation(), identity)
        for ref in ('../alice-1', '/absolute', 'missing', 'alice-1.json'):
            with self.assertRaises(ValueError) as error:
                registry.identity_credentials(dict(row, credential_ref=ref))
            self.assertNotIn(str(self.root), str(error.exception))
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            registry.identity_credentials(row)
        path.chmod(0o600)
        path.write_text('{"version":1,"username":"wrong","password":"never-output"}')
        with self.assertRaises(ValueError) as error:
            registry.identity_credentials(row)
        self.assertNotIn('never-output', str(error.exception))
        provider = Path(config['provider_config_file'])
        original = provider.read_bytes()
        provider.write_bytes(original + b' ')
        with self.assertRaises(ValueError):
            registry.catalog_credentials(dict(catalog_credential_ref='catalog-1'))
        provider.write_bytes(original)
        path.write_text('{"version":1,"version":1}')
        with self.assertRaises(ValueError):
            registry.identity_credentials(row)


if __name__ == '__main__':
    unittest.main()
