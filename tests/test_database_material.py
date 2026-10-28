"""Synthetic offline contract fixtures; no workstation profile or runtime reads."""
import contextlib
import copy
import functools
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'local_experiment'))
import database_material as material

# Fixed published bcrypt test vectors. These are synthetic public test data.
HASH_A = '$2a$06$m0CrhHm10qJ3lXRY.5zDGO3rS2KdeeWLuGmsfGlMfOxih58VYVfxe'
HASH_ABC = '$2a$06$If6bvum7DFjUnE9p2uDeDu0YHzrHM6tf.iqN8.yx.jNN1ILEf7h0i'
@functools.lru_cache(maxsize=None)
def _synthetic_hash(user, password):
    """Test-only bcrypt generation authorized for supplied synthetic passwords."""
    result = subprocess.run([shutil.which('htpasswd'), '-n', '-i', '-B', '-C', '4', user],
                            input=password + '\n', text=True, capture_output=True, check=True)
    return result.stdout.strip().split(':', 1)[1]


def private_input_from_bundle(bundle, *, phase='full'):
    """Reusable synthetic private input from any actual core-rendered bundle.

    Consumes declared refs, usernames and policy metadata; does not synthesize
    public specs, refs, prefixes, SQL names or S3 action rules.
    """
    if phase not in ('bootstrap', 'full'):
        raise ValueError('invalid synthetic fixture phase')
    metadata = bundle['tofu']
    identity = json.loads(bundle['files']['identity-public.json'])
    policy = metadata['s3_policy']
    secrets = {}

    def put(ref, data):
        secrets[material.ref_identity(ref)] = {'data': data}

    for i, db in enumerate(metadata['databases'].values()):
        for role in ('root_secret', 'trino_secret', 'bootstrap_secret'):
            if phase == 'full' or role == 'root_secret':
                put(db[role], {'client_id': 'root' if role == 'root_secret' else role + str(i),
                              'client_secret': 'synthetic-client-secret-' + role + str(i), 'realm': db['realm']})
        put(db['jwt_secret'], {
            'private.pem': '-----BEGIN PRIVATE KEY-----\nSYNTHETIC-' + str(i) + '\n-----END PRIVATE KEY-----\n',
            'public.pem': '-----BEGIN PUBLIC KEY-----\nSYNTHETIC-' + str(i) + '\n-----END PUBLIC KEY-----\n'})
        put(db['s3_secret'], {
            'AWS_ACCESS_KEY_ID': 'synthetic-db-access-' + str(i), 'AWS_SECRET_ACCESS_KEY': 's' * 40 + str(i),
            'AWS_REGION': policy['region'], 'S3_REGION': policy['region'], 'S3_ENDPOINT': policy['endpoint'],
            'S3_BUCKET': policy['bucket'], 'S3_PATH_STYLE': 'true'})
    users = [identity['platform_admin']] + [user for db in identity['databases'].values() for user in db['roles'].values()]
    passwords = {user: 'synthetic-password-' + str(i) for i, user in enumerate(users)} if phase == 'full' else {}
    hashes = {user: _synthetic_hash(user, password) for user, password in passwords.items()}
    shared_data = {
        'postgres_secret': {'database': 'polaris', 'username': 'polaris', 'password': 'p' * 40},
        'trino_auth_secret': {'password.db': ''.join(u + ':' + hashes[u] + '\n' for u in passwords),
                              'shared-secret': 'supplied-shared-secret',
                              'tls.pem': '-----BEGIN PRIVATE KEY-----\nSYNTHETIC\n-----END PRIVATE KEY-----\n'
                                         '-----BEGIN CERTIFICATE-----\nSYNTHETIC\n-----END CERTIFICATE-----\n'},
        'gitops_source_secret': {'accesskey': 'synthetic-gitops-access', 'secretkey': 'g' * 40},
        'operator_secret': {'access_key': 'synthetic-operator-access', 'secret_key': 'o' * 40},
    }
    for role, data in shared_data.items():
        if phase == 'full' or role != 'trino_auth_secret':
            put(metadata['shared'][role], data)
    return {'version': 1, 'generation_id': metadata['generation_id'], 'secrets': secrets, 'passwords': passwords}


def synthetic_fixture(count=3, *, phase='full'):
    from test_database_generation import spec
    import database_generation
    bundle = database_generation.render(spec(count), ROOT)
    return bundle, private_input_from_bundle(bundle, phase=phase)


def assembled_fixture(count=3, *, phase='full'):
    bundle, private = synthetic_fixture(count, phase=phase)
    return material.build_material(bundle, private, phase=phase)


class MaterialTests(unittest.TestCase):
    def test_explicit_v2_requires_scoped_admin_password_and_actual_hash_verification(self):
        from test_database_generation import spec
        import database_generation as generation
        import trino_auth
        value = spec(2)
        value['version'] = 2
        value['profile']['template_fingerprint'] = generation.profile_fingerprint(version=2)
        bundle = generation.render(value)
        private = private_input_from_bundle(bundle)
        built = material.build_material(bundle, private)
        for key, db in built['runtime_auth']['databases'].items():
            self.assertEqual(set(db['roles']), {'bi_viewer', 'data_engineer', 'admin'})
            self.assertEqual(db['roles']['admin']['username'], trino_auth.scoped_username(key, 'admin'))
        user = trino_auth.scoped_username('opaque_0', 'admin')
        missing = copy.deepcopy(private)
        del missing['passwords'][user]
        with self.assertRaises(ValueError):
            material.build_material(bundle, missing)
        wrong = copy.deepcopy(private)
        wrong['passwords'][user] = 'different-password-from-supplied-bcrypt'
        with self.assertRaises(ValueError):
            material.build_material(bundle, wrong)

    def setUp(self):
        self.bundle, self.private = synthetic_fixture()

    def data(self, ref):
        return self.private['secrets'][material.ref_identity(ref)]['data']

    def sync_public(self):
        self.bundle['files']['tofu-public.json'] = json.dumps(self.bundle['tofu']).encode()

    def reject(self):
        with self.assertRaisesRegex(ValueError, '^invalid database material or public/private contract mismatch$'):
            material.build_material(self.bundle, self.private)

    def test_three_database_complete_deterministic_and_no_mutation(self):
        before = copy.deepcopy((self.bundle, self.private))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            built = material.build_material(self.bundle, self.private)
            self.assertEqual(built, material.build_material(self.bundle, self.private))
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(before, (self.bundle, self.private))
        variables = built['tofu_vars']
        self.assertEqual(len(built['runtime_auth']['databases']), 3)
        self.assertEqual(built['runtime_auth']['version'], 2)
        self.assertEqual(len(variables['warehouse_generation_secrets']), 21)
        for key, supplied in self.private['secrets'].items():
            self.assertEqual(variables['warehouse_generation_secrets'][key], supplied)
        public = self.bundle['tofu']
        combined = variables['warehouse_generation_secrets'][material.ref_identity(public['shared']['polaris_bootstrap_secret'])]['data']
        for db in public['databases'].values():
            self.assertEqual(json.loads(combined[db['bootstrap_credentials_key']]), {
                db['realm']: {'client-id': 'root', 'client-secret': self.data(db['root_secret'])['client_secret']}})
        server = variables['warehouse_generation_secrets'][material.ref_identity(public['shared']['seaweedfs_server_secret'])]['data']
        identities = json.loads(server['s3.json'])['identities']
        for db, entry in zip(public['databases'].values(), identities[2:]):
            self.assertEqual(entry['name'], db['s3_identity_name'])
            self.assertEqual(entry['actions'], db['s3_actions'])
        encoded = json.dumps(built['runtime_auth'])
        for forbidden in ('client_secret', 'tls.pem', 'password.db', 'AWS_SECRET_ACCESS_KEY', '$2a$'):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(variables['polaris_database_credentials'], {})

    def test_generic_counts(self):
        for count in (1, 2, 3):
            bundle, private = synthetic_fixture(count)
            self.assertEqual(len(material.build_material(bundle, private)['runtime_auth']['databases']), count)

    def test_private_generation(self):
        self.private['generation_id'] = 'other'
        self.reject()

    def test_extra_missing_and_versioned_reference(self):
        key = next(iter(self.private['secrets']))
        for changed in ('extra', 'missing', 'version'):
            with self.subTest(changed=changed):
                original = copy.deepcopy(self.private['secrets'])
                if changed == 'extra':
                    self.private['secrets']['shirokuma-dev/extra@v7'] = {'data': {'x': 'secret'}}
                elif changed == 'missing':
                    self.private['secrets'].pop(key)
                else:
                    self.private['secrets'][key + '-other'] = self.private['secrets'].pop(key)
                self.reject()
                self.private['secrets'] = original

    def test_private_keysets(self):
        secret = next(iter(self.private['secrets'].values()))
        secret['data']['unexpected'] = 'DO-NOT-LEAK'
        self.reject()

    def test_derived_roles_exact_and_cannot_supply_derived_data(self):
        for markers in ([], ['seaweedfs_server_secret'], sorted(material.DERIVED) * 2,
                        sorted(material.DERIVED) + ['operator_secret']):
            self.bundle['tofu']['derived_secret_roles'] = markers
            self.sync_public()
            self.reject()
        self.bundle['tofu']['derived_secret_roles'] = sorted(material.DERIVED)
        self.sync_public()
        ref = self.bundle['tofu']['shared']['seaweedfs_server_secret']
        self.private['secrets'][material.ref_identity(ref)] = {'data': {'s3.json': '{}'}}
        self.reject()

    def test_public_generation_database_username_and_versions(self):
        baseline = copy.deepcopy(self.bundle)
        for mutation in ('generation', 'dbid', 'username', 'version', 'keyset', 'duplicate'):
            with self.subTest(mutation=mutation):
                self.bundle = copy.deepcopy(baseline)
                db = self.bundle['tofu']['databases']['opaque_0']
                if mutation == 'generation':
                    self.bundle['tofu']['generation_id'] = 'bad'
                elif mutation == 'dbid':
                    self.bundle['tofu']['databases']['extra'] = copy.deepcopy(db)
                elif mutation == 'username':
                    identity = json.loads(self.bundle['files']['identity-public.json'])
                    identity['databases']['opaque_0']['roles']['bi_viewer'] = 'wrong'
                    self.bundle['files']['identity-public.json'] = json.dumps(identity).encode()
                elif mutation == 'version':
                    db['root_secret']['version'] = ''
                elif mutation == 'keyset':
                    db['root_secret']['keys'].append('unexpected')
                else:
                    db['trino_secret'] = copy.deepcopy(db['root_secret'])
                self.sync_public()
                self.reject()

    def test_realm_s3_config_and_duplicate_access_keys(self):
        db = self.bundle['tofu']['databases']['opaque_0']
        baseline = copy.deepcopy(self.private)
        for role, field, value in (
            ('root_secret', 'realm', 'OTHER'), ('root_secret', 'client_id', 'not-root'),
            ('trino_secret', 'realm', 'OTHER'), ('bootstrap_secret', 'realm', 'OTHER'),
            ('s3_secret', 'S3_ENDPOINT', 'http://wrong'), ('s3_secret', 'S3_BUCKET', 'other'),
            ('s3_secret', 'S3_REGION', 'wrong'), ('s3_secret', 'AWS_REGION', 'wrong'),
            ('s3_secret', 'S3_PATH_STYLE', 'false'), ('s3_secret', 'AWS_ACCESS_KEY_ID', 'synthetic-operator-access'),
            ('jwt_secret', 'private.pem', 'invalid')):
            with self.subTest(role=role, field=field):
                self.private = copy.deepcopy(baseline)
                self.data(db[role])[field] = value
                self.reject()

    def test_password_identity_extra_missing(self):
        self.private['passwords']['unexpected'] = 'DO-NOT-LEAK'
        self.reject()
        self.private['passwords'].pop('unexpected')
        self.private['passwords'].pop('shirokuma_admin')
        self.reject()

    def test_password_hash_mismatch_is_safe_before_any_output(self):
        self.private['passwords']['shirokuma_admin'] = 'DO-NOT-LEAK-WRONG-PASSWORD'
        self.reject()

    def test_service_principals_cannot_substitute_root_or_each_other(self):
        db = self.bundle['tofu']['databases']['opaque_0']
        for role in ('trino_secret', 'bootstrap_secret'):
            original = self.data(db[role])['client_id']
            self.data(db[role])['client_id'] = 'root'
            self.reject()
            self.data(db[role])['client_id'] = original
        self.data(db['bootstrap_secret'])['client_id'] = self.data(db['trino_secret'])['client_id']
        self.reject()

    def test_service_secrets_cannot_overlap_any_database_root_secret(self):
        baseline = copy.deepcopy(self.private)
        databases = list(self.bundle['tofu']['databases'].values())
        for source_index, target_index in ((0, 0), (0, 1), (2, 0)):
            for role in ('trino_secret', 'bootstrap_secret'):
                with self.subTest(source=source_index, target=target_index, role=role):
                    self.private = copy.deepcopy(baseline)
                    self.data(databases[target_index][role])['client_secret'] = self.data(
                        databases[source_index]['root_secret'])['client_secret']
                    with mock.patch.object(material, '_verify_passwords') as verify:
                        self.reject()
                        verify.assert_not_called()
        # The exact same synthetic full input still assembles when disjoint.
        self.private = baseline
        built = material.build_material(self.bundle, self.private)
        self.assertEqual(built['pending_secret_refs'], [])
        bootstrap = private_input_from_bundle(self.bundle, phase='bootstrap')
        initial = material.build_material(self.bundle, bootstrap, phase='bootstrap')
        self.assertNotIn('runtime_auth', initial)
        self.assertEqual(initial['pending_secret_refs'], self.bundle['tofu']['deferred_secret_refs'])

    def test_duplicate_s3_secrets_root_secrets_and_jwt_private_bytes(self):
        baseline = copy.deepcopy(self.private)
        first, second = list(self.bundle['tofu']['databases'].values())[:2]
        operator = self.bundle['tofu']['shared']['operator_secret']
        gitops = self.bundle['tofu']['shared']['gitops_source_secret']
        for target, field, source, source_field in (
            (first['s3_secret'], 'AWS_SECRET_ACCESS_KEY', operator, 'secret_key'),
            (gitops, 'secretkey', operator, 'secret_key'),
            (second['s3_secret'], 'AWS_SECRET_ACCESS_KEY', first['s3_secret'], 'AWS_SECRET_ACCESS_KEY'),
            (second['root_secret'], 'client_secret', first['root_secret'], 'client_secret'),
            (second['jwt_secret'], 'private.pem', first['jwt_secret'], 'private.pem')):
            with self.subTest(field=field):
                self.private = copy.deepcopy(baseline)
                self.data(target)[field] = self.data(source)[source_field]
                self.reject()

    def test_password_utf8_byte_limit_and_duplicate_hashes(self):
        auth = self.data(self.bundle['tofu']['shared']['trino_auth_secret'])
        for password in ('a' * 73, 'あ' * 25):
            self.private['passwords']['shirokuma_admin'] = password
            self.reject()
        self.private['passwords']['shirokuma_admin'] = 'あ' * 24
        lines = auth['password.db'].splitlines()
        lines[0] = 'shirokuma_admin:' + _synthetic_hash('shirokuma_admin', 'あ' * 24)
        auth['password.db'] = '\n'.join(lines) + '\n'
        material.build_material(self.bundle, self.private)
        first_hash = lines[0].split(':', 1)[1]
        lines[1] = lines[1].split(':', 1)[0] + ':' + first_hash
        auth['password.db'] = '\n'.join(lines) + '\n'
        self.reject()

    def test_permissions_and_stable_output(self):
        built = material.build_material(self.bundle, self.private)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'out'
            material.write_material(built, directory)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual({p.name for p in directory.iterdir()}, {'terraform.tfvars.json', 'backend.json', 'catalog-auth.json'})
            before = {p.name: p.read_bytes() for p in directory.iterdir()}
            for p in directory.iterdir():
                self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
                p.chmod(0o644)
            material.write_material(built, directory)
            self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir()})
            self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in directory.iterdir()))


class PasswordTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('htpasswd'), 'local htpasswd required')
    def test_real_known_bcrypt_vectors_and_mismatch(self):
        text = 'first:' + HASH_A + '\nsecond:' + HASH_ABC + '\n'
        material._verify_passwords(text, {'first': 'a', 'second': 'abc'})
        with self.assertRaisesRegex(ValueError, 'verification failed'):
            material._verify_passwords(text, {'first': 'wrong', 'second': 'abc'})

    def test_hash_exact_user_set_syntax_duplicates_and_safe_invocation(self):
        for text in ('user:plaintext\n', 'user:' + HASH_A + '\nuser:' + HASH_A + '\n',
                     'extra:' + HASH_A + '\n'):
            with self.assertRaises(ValueError):
                material._verify_passwords(text, {'user': 'supplied-password'})
        with mock.patch.object(material.shutil, 'which', return_value='/synthetic/htpasswd'), \
                mock.patch.object(material.subprocess, 'run') as run:
            run.return_value.returncode = 0
            material._verify_passwords('user:' + HASH_A + '\n', {'user': 'supplied-password'})
            args, kwargs = run.call_args
            self.assertNotIn('supplied-password', args[0])
            self.assertEqual(kwargs['input'], 'supplied-password\n')
            self.assertEqual(kwargs['stdout'], subprocess.DEVNULL)
            self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)

    def test_duplicate_verifier_and_oversize_password_reject_before_tool(self):
        with mock.patch.object(material.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'duplicate password verifier'):
                material._verify_passwords('first:' + HASH_A + '\nsecond:' + HASH_A + '\n',
                                           {'first': 'a', 'second': 'b'})
            with self.assertRaisesRegex(ValueError, 'bcrypt byte limit'):
                material._verify_passwords('first:' + HASH_A + '\n', {'first': 'あ' * 25})
            run.assert_not_called()


class PhaseTests(unittest.TestCase):
    def test_bootstrap_three_databases_full_completion_and_same_initial_data(self):
        bundle, bootstrap = synthetic_fixture(3, phase='bootstrap')
        self.assertEqual(bootstrap['passwords'], {})
        with mock.patch.object(material, '_verify_passwords') as verify:
            initial = material.build_material(bundle, bootstrap, phase='bootstrap')
            verify.assert_not_called()
        self.assertEqual(set(initial), {'tofu_vars', 'pending_secret_refs'})
        self.assertEqual(initial['pending_secret_refs'], bundle['tofu']['deferred_secret_refs'])
        self.assertEqual(len(initial['pending_secret_refs']), 7)
        self.assertEqual(initial['tofu_vars']['warehouse_generation_phase'], 'bootstrap')
        with self.assertRaises(ValueError):
            material.build_material(bundle, bootstrap)  # default is full, never inferred
        complete = material.build_material(bundle, private_input_from_bundle(bundle))
        self.assertEqual(set(complete), {'tofu_vars', 'runtime_auth', 'runtime_catalog_auth', 'pending_secret_refs'})
        self.assertEqual(complete['pending_secret_refs'], [])
        self.assertEqual(complete['tofu_vars']['warehouse_generation_phase'], 'full')
        for key, value in initial['tofu_vars']['warehouse_generation_secrets'].items():
            self.assertEqual(value, complete['tofu_vars']['warehouse_generation_secrets'][key])
        self.assertEqual(initial['tofu_vars']['warehouse_generation'], complete['tofu_vars']['warehouse_generation'])
        self.assertEqual(len(initial['tofu_vars']['warehouse_generation_secrets']), 14)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'bootstrap'
            material.write_material(initial, directory)
            self.assertEqual([p.name for p in directory.iterdir()], ['terraform.tfvars.json'])
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((directory / 'terraform.tfvars.json').stat().st_mode), 0o600)
            material.write_material(complete, directory)
            before = {p.name: p.read_bytes() for p in directory.iterdir()}
            with self.assertRaisesRegex(ValueError, 'contains runtime authentication'):
                material.write_material(initial, directory)
            self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir()})

    def test_invalid_phase_partial_extra_bootstrap_and_passwords_rejected(self):
        bundle, private = synthetic_fixture(phase='bootstrap')
        for phase in ('', 'partial', None):
            with self.assertRaises(ValueError):
                material.build_material(bundle, private, phase=phase)
        baseline = copy.deepcopy(private)
        deferred = private_input_from_bundle(bundle)['secrets']
        for kind in ('missing', 'extra_auth', 'passwords'):
            with self.subTest(kind=kind):
                private = copy.deepcopy(baseline)
                if kind == 'missing':
                    private['secrets'].pop(next(iter(private['secrets'])))
                elif kind in ('extra_sp', 'extra_auth'):
                    ref = bundle['tofu']['deferred_secret_refs'][0] if kind == 'extra_sp' else bundle['tofu']['shared']['trino_auth_secret']
                    key = material.ref_identity(ref)
                    private['secrets'][key] = deferred[key]
                else:
                    private['passwords']['shirokuma_admin'] = 'forbidden'
                with self.assertRaises(ValueError):
                    material.build_material(bundle, private, phase='bootstrap')

    def test_bootstrap_preserves_existing_consumers_and_prepared_shared_auth(self):
        bundle, complete = synthetic_fixture(3)
        before = copy.deepcopy(complete)
        private = copy.deepcopy(complete)
        new_db = bundle['tofu']['databases']['opaque_2']
        missing = [new_db[role] for role in ('trino_secret', 'bootstrap_secret')]
        for ref in missing:
            private['secrets'].pop(material.ref_identity(ref))
        built = material.build_material(bundle, private, phase='bootstrap')
        self.assertEqual(built['pending_secret_refs'], sorted(missing, key=lambda r: (r['namespace'], r['name'], r['version'])))
        self.assertNotIn('runtime_auth', built)
        self.assertNotIn('runtime_catalog_auth', built)
        for ref, value in private['secrets'].items():
            self.assertEqual(built['tofu_vars']['warehouse_generation_secrets'][ref], value)
        self.assertEqual(before, complete)
        # Every optional supplied SP is checked even before the other SP exists.
        old = bundle['tofu']['databases']['opaque_0']['trino_secret']
        private['secrets'][material.ref_identity(old)]['data']['realm'] = 'wrong'
        with self.assertRaises(ValueError):
            material.build_material(bundle, private, phase='bootstrap')

    def test_catalog_runtime_material_uses_same_frozen_bindings_and_issued_credentials(self):
        bundle, private = synthetic_fixture(3)
        built = material.build_material(bundle, private)
        for key, entry in built['runtime_catalog_auth']['databases'].items():
            db = bundle['tofu']['databases'][key]
            self.assertEqual(entry, dict(service_ref=db['service_ref'], realm=db['realm'], catalog=db['catalog'],
                generation_id=bundle['manifest']['generation_id'],
                client_id=private['secrets'][material.ref_identity(db['trino_secret'])]['data']['client_id'],
                client_secret=private['secrets'][material.ref_identity(db['trino_secret'])]['data']['client_secret']))
            self.assertEqual(entry['generation_id'], built['runtime_auth']['databases'][key]['generation_id'])

    def test_bootstrap_checks_prepared_passwords_and_rejects_secret_version_drift(self):
        bundle, private = synthetic_fixture(3)
        private['passwords']['shirokuma_admin'] = 'wrong-password'
        with self.assertRaises(ValueError):
            material.build_material(bundle, private, phase='bootstrap')
        bundle, private = synthetic_fixture(3, phase='bootstrap')
        full = private_input_from_bundle(bundle)
        ref = bundle['tofu']['databases']['opaque_0']['trino_secret']
        key = material.ref_identity(ref)
        private['secrets'][key + '-other-version'] = full['secrets'][key]
        with self.assertRaises(ValueError):
            material.build_material(bundle, private, phase='bootstrap')

    def test_exact_partitions_objects_and_sorting(self):
        original, private = synthetic_fixture(phase='bootstrap')
        for mutation in ('overlap', 'missing', 'duplicate', 'wrong_phase', 'object_version', 'object_keys', 'order'):
            with self.subTest(mutation=mutation):
                bundle = copy.deepcopy(original)
                public = bundle['tofu']
                bootstrap, deferred = public['bootstrap_secret_refs'], public['deferred_secret_refs']
                if mutation == 'overlap':
                    bootstrap.append(copy.deepcopy(deferred[0]))
                elif mutation == 'missing':
                    bootstrap.pop()
                elif mutation == 'duplicate':
                    deferred.append(copy.deepcopy(deferred[0]))
                elif mutation == 'wrong_phase':
                    bootstrap.append(deferred.pop())
                elif mutation == 'object_version':
                    bootstrap[0] = copy.deepcopy(bootstrap[0])
                    bootstrap[0]['version'] = 'other'
                elif mutation == 'object_keys':
                    bootstrap[0] = copy.deepcopy(bootstrap[0])
                    bootstrap[0]['keys'] = ['wrong']
                else:
                    bootstrap.reverse()
                bundle['files']['tofu-public.json'] = json.dumps(public).encode()
                with self.assertRaises(ValueError):
                    material.build_material(bundle, private, phase='bootstrap')

    def test_real_core_bundle_load_and_public_namespaces(self):
        import database_generation
        from test_database_generation import spec
        bundle = database_generation.render(spec(3), ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'bundle'
            database_generation.write_bundle(bundle, directory)
            loaded = database_generation.load_bundle(directory, ROOT)
            for phase in ('bootstrap', 'full'):
                private = private_input_from_bundle(loaded, phase=phase)
                built = material.build_material(loaded, private, phase=phase)
                self.assertEqual(built['tofu_vars']['warehouse_generation'], loaded['tofu'])
        refs = bundle['tofu']['bootstrap_secret_refs'] + bundle['tofu']['deferred_secret_refs']
        self.assertNotIn('dev', {ref['namespace'] for ref in refs})
        self.assertIn('shirokuma-dev', {ref['namespace'] for ref in refs})

    def test_exact_suffix_not_substring_and_old_namespace_rejected(self):
        original, private = synthetic_fixture()
        for namespace, name in [('dev', 'sales-root-v1'), ('shirokuma-dev', 'sales-root-v1-extra')]:
            bundle = copy.deepcopy(original)
            ref = bundle['tofu']['databases']['opaque_0']['root_secret']
            ref['namespace'], ref['name'] = namespace, name
            bundle['files']['tofu-public.json'] = json.dumps(bundle['tofu']).encode()
            with self.assertRaises(ValueError):
                material.build_material(bundle, private)


class CLITests(unittest.TestCase):
    def test_real_bundle_script_and_module_two_stage_outputs(self):
        import database_generation
        bundle, bootstrap = synthetic_fixture(3, phase='bootstrap')
        complete = private_input_from_bundle(bundle)
        entries = [[sys.executable, str(ROOT / 'tools/local_experiment/database_material.py')],
                   [sys.executable, '-m', 'tools.local_experiment.database_material']]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            database_generation.write_bundle(bundle, directory / 'bundle')
            for name, private in [('bootstrap', bootstrap), ('full', complete)]:
                source = directory / (name + '.json')
                source.write_text(json.dumps(private))
                source.chmod(0o600)
            for index, entry in enumerate(entries):
                for phase in ('bootstrap', 'full'):
                    output = directory / (phase + '-out-' + str(index))
                    args = entry + ['--bundle', str(directory / 'bundle'), '--private-input',
                                    str(directory / (phase + '.json')), '--output', str(output)]
                    # Exercise default full as well as explicit bootstrap.
                    if phase == 'bootstrap':
                        args += ['--phase', phase]
                    result = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, 'Wrote {} private material files\n'.format(1 if phase == 'bootstrap' else 3))
                    self.assertEqual(result.stderr, '')
                    self.assertEqual((output / 'backend.json').exists(), phase == 'full')
                    self.assertEqual(stat.S_IMODE((output / 'terraform.tfvars.json').stat().st_mode), 0o600)
                rejected = directory / ('rejected-' + str(index))
                result = subprocess.run(entry + ['--bundle', str(directory / 'bundle'), '--private-input',
                                        str(directory / 'bootstrap.json'), '--output', str(rejected)],
                                        cwd=str(ROOT), capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, '')
                self.assertEqual(result.stderr, 'database material: invalid or unreadable explicit inputs\n')
                self.assertFalse(rejected.exists())

    def test_explicit_loader_private_permissions_success_and_failure_output(self):
        bundle, private = synthetic_fixture()
        loader = mock.Mock(return_value=bundle)
        core = types.SimpleNamespace(load_bundle=loader)
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.dict(sys.modules, {'database_generation': core}):
            directory = Path(temporary)
            source = directory / 'private-input.json'
            source.write_text(json.dumps(private))
            source.chmod(0o600)
            args = ['--bundle', str(directory / 'bundle'), '--private-input', str(source),
                    '--output', str(directory / 'out')]
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(material.main(args), 0)
            self.assertEqual(stdout.getvalue(), 'Wrote 3 private material files\n')
            self.assertEqual(stderr.getvalue(), '')
            loader.assert_called_once_with(directory / 'bundle', ROOT)
            # A rejected explicit input must not create its output directory.
            source.chmod(0o644)
            args[-1] = str(directory / 'rejected')
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(material.main(args), 1)
            self.assertEqual(stdout.getvalue(), '')
            self.assertEqual(stderr.getvalue(), 'database material: invalid or unreadable explicit inputs\n')
            self.assertFalse((directory / 'rejected').exists())

    def test_script_and_module_help_and_explicit_missing_bundle(self):
        entries = [[sys.executable, str(ROOT / 'tools/local_experiment/database_material.py')],
                   [sys.executable, '-m', 'tools.local_experiment.database_material']]
        with tempfile.TemporaryDirectory() as temporary:
            for entry in entries:
                help_result = subprocess.run(entry + ['--help'], cwd=str(ROOT), capture_output=True, text=True)
                self.assertEqual(help_result.returncode, 0, help_result.stderr)
                self.assertIn('--private-input', help_result.stdout)
                result = subprocess.run(entry + ['--bundle', temporary + '/missing', '--private-input',
                                        temporary + '/secret-DO-NOT-LEAK', '--output', temporary + '/out'],
                                        cwd=str(ROOT), capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, '')
                self.assertEqual(result.stderr, 'database material: invalid or unreadable explicit inputs\n')
                self.assertFalse((Path(temporary) / 'out').exists())

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate JSON key'):
            material._json('{"version":1,"version":1}')


if __name__ == '__main__':
    unittest.main()
