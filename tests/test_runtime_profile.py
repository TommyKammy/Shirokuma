"""Startup/profile tests use only temporary synthetic private files."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import runtime_profile as profile
import trino_auth


def manifest():
    return dict(version=2, platform_admin=dict(username='shirokuma_admin', password='synthetic-admin'),
        databases={'example-id': dict(sql_catalog='example', generation_id='gen1', roles={
            role: dict(username=trino_auth.scoped_username('example-id', role), password='synthetic-' + role)
            for role in ('bi_viewer', 'data_engineer')})})


def synthetic_profile(root):
    def private(name, value):
        path = root / name
        path.write_text(json.dumps(value) if not isinstance(value, str) else value)
        path.chmod(0o600)
        return str(path)
    value = dict(version=1, mode='database', state_dir=str(root),
        auth_file=private('accounts.json', {'alice': dict(role='admin', platform_admin=True,
            salt='12' * 16, password_hash='34' * 32)}),
        trino_auth_file=private('backend.json', manifest()), trino_ca_file=private('ca.pem', 'synthetic CA'),
        catalog_auth_file=private('catalog.json', dict(version=1, databases={'example-id': dict(
            service_ref='polaris', realm='SALES', catalog='example', generation_id='gen1',
            client_id='synthetic-client', client_secret='synthetic-secret')})),
        sql_endpoint_url='http://127.0.0.1:8080',
        client_auth_file=private('client.json', dict(username='alice', password='synthetic-password', selected_role='admin')))
    location = private('runtime-profile.json', value)
    return Path(location), value


class RuntimeProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.state = patch.object(profile, '_current', None)
        self.state.start()
        self.addCleanup(self.state.stop)
        self.repository = patch.object(profile, 'ROOT', self.root)
        self.repository.start()
        self.addCleanup(self.repository.stop)

    def test_absence_no_autoload_and_independent_discovery(self):
        os.environ['SHIROKUMA_STATE_DIR'] = str(self.root / 'other')
        os.environ['SHIROKUMA_RUNTIME_PROFILE'] = str(self.root / 'missing')
        self.assertFalse(profile.active())
        self.assertIsNone(profile.current())
        with self.assertRaises(ValueError):
            profile.configure_process()
        del os.environ['SHIROKUMA_RUNTIME_PROFILE']
        self.assertFalse(profile.configure_process())
        marker = self.root / 'var/shirokuma/runtime-profile.active'
        marker.parent.mkdir(parents=True)
        marker.touch()
        with self.assertRaises(ValueError):
            profile.configure_process()

    def test_valid_activation_is_frozen_and_propagates(self):
        location, value = synthetic_profile(self.root)
        os.environ['SHIROKUMA_RUNTIME_PROFILE'] = str(location)
        self.assertTrue(profile.configure_process())
        self.assertEqual(os.environ['SHIROKUMA_AUTH_MODE'], 'shared')
        self.assertEqual(os.environ['SHIROKUMA_TRINO_AUTH_FILE'], value['trino_auth_file'])
        with self.assertRaises(AttributeError):
            profile.current().mode = 'legacy'
        self.assertFalse((location.parent / 'runtime-profile.active').exists())

    def test_new_full_candidate_preserves_fixed_inputs_and_never_patches_a_profile(self):
        location,value = synthetic_profile(self.root)
        self.root.chmod(0o700)
        before = location.read_bytes()
        backend = profile.private_json(value['trino_auth_file'])
        catalog = profile.private_json(value['catalog_auth_file'])
        backend['databases']['example-id']['generation_id'] = 'full-new'
        catalog['databases']['example-id']['generation_id'] = 'full-new'
        new_backend,new_catalog = self.root/'new-backend.json',self.root/'new-catalog.json'
        for path,data in ((new_backend,backend),(new_catalog,catalog)):
            path.write_text(json.dumps(data)); path.chmod(0o600)
        destination = self.root/'full-candidate.json'
        candidate = profile.create_candidate(location,destination,backend=new_backend,catalog=new_catalog)
        self.assertEqual(location.read_bytes(),before)
        self.assertEqual(candidate.auth_file,value['auth_file'])
        for name in ('state_dir','trino_ca_file','client_auth_file','sql_endpoint_url'):
            self.assertEqual(getattr(candidate,name),value[name])
        self.assertEqual(profile.private_json(candidate.trino_auth_file)['platform_admin'],
                         profile.private_json(value['trino_auth_file'])['platform_admin'])
        self.assertEqual(profile.private_json(candidate.catalog_auth_file)['databases']['example-id']['client_secret'],
                         profile.private_json(value['catalog_auth_file'])['databases']['example-id']['client_secret'])
        frozen = destination.read_bytes()
        with self.assertRaises(FileExistsError):
            profile.create_candidate(location,destination,backend=new_backend,catalog=new_catalog)
        self.assertEqual(destination.read_bytes(),frozen)

    def test_invalid_references_and_exact_fields(self):
        location, value = synthetic_profile(self.root)
        for field, bad in [('mode', 'legacy'), ('version', True), ('state_dir', 'relative'),
                           ('sql_endpoint_url', 'http://user:secret@localhost:8080'),
                           ('sql_endpoint_url', 'http://example.org'), ('sql_endpoint_url', 'ftp://localhost')]:
            location.write_text(json.dumps(dict(value, **{field: bad})))
            with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                profile.load(location)
        location.write_text(json.dumps(dict(value, extra='x')))
        with self.assertRaises(ValueError):
            profile.load(location)
        location.write_text(json.dumps(value))
        for field in ('auth_file', 'trino_auth_file', 'trino_ca_file', 'catalog_auth_file', 'client_auth_file'):
            path = Path(value[field])
            path.chmod(0o644)
            with self.subTest(field=field), self.assertRaises(ValueError):
                profile.load(location)
            path.chmod(0o600)
        Path(value['trino_auth_file']).write_text(json.dumps({'version': 1}))
        with self.assertRaises(ValueError):
            profile.load(location)

    def test_default_profile_does_not_follow_state_dir(self):
        location, _ = synthetic_profile(self.root)
        default = self.root / 'var/shirokuma/runtime-profile.json'
        default.parent.mkdir(parents=True)
        default.write_bytes(location.read_bytes())
        default.chmod(0o600)
        os.environ['SHIROKUMA_STATE_DIR'] = str(self.root / 'unused')
        self.assertTrue(profile.configure_process())
        self.assertEqual(os.environ['SHIROKUMA_RUNTIME_PROFILE'], str(default))

    def test_overlay_is_only_two_secret_free_files(self):
        location, value = synthetic_profile(self.root)
        with patch.object(profile, '_current', profile.load(location)):
            original = {'unchanged': b'stable'}
            result = profile.auth_overlay(original)
            self.assertEqual(original, {'unchanged': b'stable'})
            self.assertEqual(set(result) - set(original), {
                'deploy/local-experiment/trino/secure/rules.json',
                'deploy/local-experiment/trino/secure/opa/guard.rego'})
            output = b''.join(result.values())
            for secret in ('synthetic-admin', 'synthetic-bi_viewer', 'synthetic-data_engineer', 'synthetic-secret', 'synthetic-password'):
                self.assertNotIn(secret.encode(), output)
            self.assertIn(b'example', output)
            Path(value['trino_auth_file']).write_text('{}')
            with self.assertRaises(ValueError):
                profile.auth_overlay(original)

    def test_inactive_overlay_never_reads_private_files(self):
        with patch.object(profile, 'private_json', side_effect=AssertionError('unexpected read')):
            self.assertEqual(profile.auth_overlay({'a': b'b'}), {'a': b'b'})

    def test_attestation_requires_restart_and_never_exposes_credentials(self):
        location, value = synthetic_profile(self.root)
        with self.assertRaises(ValueError):
            profile.generation_attestation()
        os.environ['SHIROKUMA_RUNTIME_PROFILE'] = str(location)
        profile.configure_process()
        status = profile.generation_attestation()
        self.assertEqual(status['generation_id'], 'gen1')
        self.assertEqual(status['databases']['example-id']['sql_catalog'], 'example')
        text = json.dumps(status)
        for secret in ('synthetic-admin', 'synthetic-password', 'synthetic-secret', 'password_hash', 'salt'):
            self.assertNotIn(secret, text)
        self.assertNotIn(str(self.root), text)
        other_backend = self.root / 'different-backend.json'
        other_backend.write_bytes(Path(value['trino_auth_file']).read_bytes())
        other_backend.chmod(0o600)
        other = profile.RuntimeProfile(**dict(value, trino_auth_file=str(other_backend)))
        self.assertNotEqual(profile._public_identity(other)['profile_ref'], status['profile_ref'])
        old = Path(value['trino_auth_file']).read_text()
        changed = json.loads(old)
        changed['databases']['example-id']['generation_id'] = 'gen2'
        Path(value['trino_auth_file']).write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, 'restart'):
            profile.generation_attestation()
        Path(value['trino_auth_file']).write_text(old)
        self.assertEqual(profile.generation_attestation(), status)
        with patch.object(profile, '_public_identity', side_effect=AssertionError('must use startup proof')):
            self.assertEqual(profile.generation_attestation(), status)
        original = location.read_bytes()
        location.write_bytes(original+b' ')
        with self.assertRaises(ValueError):
            profile.generation_attestation()
        location.write_bytes(original)

    def test_private_binding_seals_same_generation_credential_changes(self):
        location, value = synthetic_profile(self.root)
        sealed = profile.private_binding(location)
        data = json.loads(Path(value['trino_auth_file']).read_text())
        data['platform_admin']['password'] = 'changed-with-same-public-identity'
        Path(value['trino_auth_file']).write_text(json.dumps(data))
        self.assertNotEqual(profile.private_binding(location), sealed)

    def test_candidate_generation_validation_rejects_old_and_mixed_profiles(self):
        import database_generation
        from test_database_generation import spec
        from test_database_material import private_input_from_bundle
        import database_material
        bundle = database_generation.render(spec(3))
        built = database_material.build_material(bundle, private_input_from_bundle(bundle))
        location, value = synthetic_profile(self.root)
        Path(value['trino_auth_file']).write_text(json.dumps(built['runtime_auth']))
        Path(value['catalog_auth_file']).write_text(json.dumps(built['runtime_catalog_auth']))
        status = profile.validate_generation(location, bundle)
        self.assertEqual(status['generation_id'], bundle['manifest']['generation_id'])
        self.assertEqual(set(status['databases']), set(bundle['tofu']['databases']))
        for file_key, material_key in (('trino_auth_file', 'runtime_auth'), ('catalog_auth_file', 'runtime_catalog_auth')):
            data = json.loads(json.dumps(built[material_key]))
            next(iter(data['databases'].values()))['generation_id'] = 'old'
            Path(value[file_key]).write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'frozen generation'):
                profile.validate_generation(location, bundle)
            Path(value[file_key]).write_text(json.dumps(built[material_key]))
        data = built['runtime_catalog_auth']
        next(iter(data['databases'].values()))['realm'] = 'WRONG'
        Path(value['catalog_auth_file']).write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            profile.validate_generation(location, bundle)


if __name__ == '__main__':
    unittest.main()
