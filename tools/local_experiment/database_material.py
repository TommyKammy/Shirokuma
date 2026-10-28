"""Offline assembly of verified public generations and explicitly supplied secrets."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

# Existing local-experiment modules use sibling imports in both entry modes.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import trino_auth


DERIVED = {'polaris_bootstrap_secret', 'seaweedfs_server_secret'}
DB_KEYS = {
    'root_secret': {'client_id', 'client_secret', 'realm'},
    'trino_secret': {'client_id', 'client_secret', 'realm'},
    'bootstrap_secret': {'client_id', 'client_secret', 'realm'},
    'jwt_secret': {'private.pem', 'public.pem'},
    's3_secret': {'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_REGION',
                  'S3_ENDPOINT', 'S3_BUCKET', 'S3_REGION', 'S3_PATH_STYLE'},
}
SHARED_KEYS = {
    'postgres_secret': {'database', 'username', 'password'},
    'seaweedfs_server_secret': {'s3.json'},
    'trino_auth_secret': {'password.db', 'shared-secret', 'tls.pem'},
    'gitops_source_secret': {'accesskey', 'secretkey'},
    'operator_secret': {'access_key', 'secret_key'},
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and '\x00' not in value


def ref_identity(ref):
    """Full immutable reference identity, also used as the private map key."""
    return '{namespace}/{name}@{version}'.format(**ref)


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def _public_contract(bundle):
    metadata = bundle['tofu']
    identity = _json(bundle['files']['identity-public.json'])
    _require(metadata == _json(bundle['files']['tofu-public.json']), 'public metadata mismatch')
    generation = bundle['manifest']['generation_id']
    _require(type(metadata['version']) is int and metadata['version'] == 1 and
             _text(generation) and metadata['generation_id'] == generation and
             type(identity['version']) is int and identity['version'] in (1, 2) and
             identity['version'] == bundle['manifest']['spec']['version'], 'generation mismatch')
    databases = metadata['databases']
    _require(isinstance(databases, dict) and databases and
             set(databases) == set(identity['databases']), 'database set mismatch')
    _require(isinstance(metadata['derived_secret_roles'], list) and
             len(metadata['derived_secret_roles']) == 2 and
             set(metadata['derived_secret_roles']) == DERIVED, 'derived roles mismatch')
    shared = metadata['shared']
    _require(set(shared) == set(SHARED_KEYS) | {'polaris_bootstrap_secret'},
             'shared reference roles mismatch')
    refs = {}
    derived = set()

    def add(ref, keys, namespace, is_derived=False):
        _require(isinstance(ref, dict) and set(ref) == {'namespace', 'name', 'version', 'keys'},
                 'invalid secret reference')
        _require(ref['namespace'] == namespace and _text(ref['version']) and
                 re.fullmatch(r'[a-z0-9][a-z0-9.-]*', ref['name']) is not None and
                 ref['name'].endswith('-' + ref['version']) and
                 isinstance(ref['keys'], list) and len(ref['keys']) == len(keys) and
                 set(ref['keys']) == set(keys), 'secret reference version or keys mismatch')
        key = ref_identity(ref)
        _require(key not in refs and all((r['namespace'], r['name']) !=
                 (ref['namespace'], ref['name']) for r in refs.values()), 'duplicate secret reference')
        refs[key] = ref
        if is_derived:
            derived.add(key)

    bootstrap_keys = []
    for dbid, db in databases.items():
        _require(_text(dbid), 'invalid database identity')
        public = identity['databases'][dbid]
        _require(public['generation_id'] == generation and public['sql_catalog'] == db['sql_catalog']
                 and set(public['roles']) == ({'bi_viewer', 'data_engineer', 'admin'}
                     if identity['version'] == 2 else {'bi_viewer', 'data_engineer'}), 'identity metadata mismatch')
        for role, user in public['roles'].items():
            _require(user == trino_auth.scoped_username(dbid, role), 'username mismatch')
        _require(db['bootstrap_credentials_key'] == 'credentials.' +
                 hashlib.sha256(dbid.encode()).hexdigest()[:16] + '.json', 'bootstrap key mismatch')
        bootstrap_keys.append(db['bootstrap_credentials_key'])
        for role, keys in DB_KEYS.items():
            add(db[role], keys, 'shirokuma-dev')
    _require(len(set(bootstrap_keys)) == len(bootstrap_keys) and
             all(re.fullmatch(r'credentials\.[0-9a-f]{16}\.json', k) for k in bootstrap_keys),
             'bootstrap keys mismatch')
    for role, ref in shared.items():
        keys = bootstrap_keys if role == 'polaris_bootstrap_secret' else SHARED_KEYS[role]
        namespace = ('shirokuma-storage' if role in {'operator_secret', 'seaweedfs_server_secret'}
                     else 'flux-system' if role == 'gitops_source_secret' else 'shirokuma-dev')
        add(ref, keys, namespace, role in DERIVED)
    _require(identity['platform_admin'] == 'shirokuma_admin', 'platform identity mismatch')
    deferred = [db[role] for db in databases.values() for role in ('trino_secret', 'bootstrap_secret')]
    deferred.append(shared['trino_auth_secret'])
    deferred_ids = {ref_identity(ref) for ref in deferred}
    sort_ref = lambda ref: (ref['namespace'], ref['name'], ref['version'])
    _require(metadata['deferred_secret_refs'] == sorted(deferred, key=sort_ref) and
             metadata['bootstrap_secret_refs'] == sorted(
                 (ref for key, ref in refs.items() if key not in deferred_ids), key=sort_ref),
             'secret phase partitions mismatch')
    return metadata, identity, refs, derived


def _pem(value, label):
    return value.strip().startswith('-----BEGIN ' + label + '-----') and value.strip().endswith(
        '-----END ' + label + '-----')


def _verify_passwords(password_db, passwords):
    lines = password_db.splitlines()
    hashes = {}
    for line in lines:
        match = re.fullmatch(r'([^:\s]+):(\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53})', line)
        _require(match is not None, 'invalid password hash file')
        user, hashed = match.groups()
        _require(user not in hashes, 'duplicate password hash identity')
        hashes[user] = hashed
    _require(set(hashes) == set(passwords), 'password hash identities mismatch')
    _require(len(set(hashes.values())) == len(hashes), 'duplicate password verifier')
    _require(all(isinstance(value, str) and 0 < len(value.encode('utf-8')) <= 72
                 for value in passwords.values()), 'password exceeds bcrypt byte limit')
    executable = shutil.which('htpasswd')
    _require(executable is not None, 'local htpasswd is required')
    with tempfile.TemporaryDirectory(prefix='shirokuma-material-') as directory:
        path = Path(directory) / 'password.db'
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(password_db)
        for user, password in passwords.items():
            result = subprocess.run([executable, '-v', '-i', str(path), user],
                                    input=password + '\n', text=True, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, check=False)
            _require(result.returncode == 0, 'password hash verification failed')


def build_material(bundle, private_input, *, phase='full'):
    """Return private Tofu and v2 backend data; never generate credentials."""
    try:
        _require(phase in ('bootstrap', 'full'), 'invalid material phase')
        return _build_material(bundle, private_input, phase)
    except (KeyError, TypeError, AttributeError, IndexError, UnicodeError, OSError, ValueError):
        # Never relay exceptions that could contain secret values.
        raise ValueError('invalid database material or public/private contract mismatch') from None


def _build_material(bundle, private_input, phase):
    metadata, identity, refs, derived = _public_contract(bundle)
    _require(set(private_input) == {'version', 'generation_id', 'secrets', 'passwords'} and
             type(private_input['version']) is int and private_input['version'] == 1 and
             private_input['generation_id'] == metadata['generation_id'], 'private generation mismatch')
    secrets = copy.deepcopy(private_input['secrets'])
    required = {ref_identity(ref) for ref in metadata['bootstrap_secret_refs']} - derived
    _require(isinstance(private_input['passwords'], dict), 'invalid passwords object')
    # Previously issued consumers and independently prepared shared auth must stay
    # in the bootstrap plan when another database is added. Only missing refs
    # may be deferred; a bootstrap never invents or rotates credentials.
    _require(set(secrets) == set(refs) - derived if phase == 'full' else
             required <= set(secrets) <= set(refs) - derived, 'private reference set mismatch')
    for key, secret in secrets.items():
        _require(set(secret) == {'data'} and isinstance(secret['data'], dict) and
                 set(secret['data']) == set(refs[key]['keys']) and
                 all(_text(v) for v in secret['data'].values()), 'private secret keys mismatch')

    def data(ref):
        return secrets[ref_identity(ref)]['data']

    shared = metadata['shared']
    policy = metadata['s3_policy']
    operator = data(shared['operator_secret'])
    gitops = data(shared['gitops_source_secret'])
    access_keys = []
    secret_keys = []

    def s3_pair(access, secret):
        _require(re.fullmatch(r'[A-Za-z0-9._-]{16,128}', access) is not None and
                 32 <= len(secret) <= 256 and secret == secret.strip() and
                 not any(c in secret for c in '\r\n\x00'), 'invalid S3 credentials')
        access_keys.append(access)
        secret_keys.append(secret)
        return [{'accessKey': access, 'secretKey': secret}]

    identities = [
        {'name': policy['operator']['name'], 'actions': policy['operator']['actions'],
         'credentials': s3_pair(operator['access_key'], operator['secret_key'])},
        {'name': policy['gitops']['name'], 'actions': policy['gitops']['actions'],
         'credentials': s3_pair(gitops['accesskey'], gitops['secretkey'])},
    ]
    roots = {}
    root_secrets, jwt_private_keys, service_secrets = [], [], []
    for dbid, db in metadata['databases'].items():
        root = data(db['root_secret'])
        _require(root['client_id'] == 'root' and root['realm'] == db['realm'], 'root realm mismatch')
        root_secrets.append(root['client_secret'])
        roles = tuple(role for role in ('root_secret', 'trino_secret', 'bootstrap_secret')
                      if ref_identity(db[role]) in secrets)
        service_secrets.extend(data(db[role])['client_secret'] for role in roles if role != 'root_secret')
        _require(len({data(db[role])['client_id'] for role in roles}) == len(roles),
                 'root and service principal identities must be distinct')
        for role in roles:
            _require(data(db[role])['realm'] == db['realm'], 'principal realm mismatch')
            _require(all(not any(c in value for c in '\r\n') and value == value.strip()
                         for value in data(db[role]).values()), 'invalid principal credentials')
        jwt = data(db['jwt_secret'])
        jwt_private_keys.append(jwt['private.pem'])
        _require(_pem(jwt['private.pem'], 'PRIVATE KEY') and _pem(jwt['public.pem'], 'PUBLIC KEY'),
                 'invalid JWT PEM format')
        s3 = data(db['s3_secret'])
        _require(s3['AWS_REGION'] == policy['region'] and s3['S3_REGION'] == policy['region'] and
                 s3['S3_ENDPOINT'] == policy['endpoint'] and s3['S3_BUCKET'] == policy['bucket'] and
                 s3['S3_PATH_STYLE'] == 'true', 'S3 public configuration mismatch')
        _require(isinstance(db['s3_actions'], list) and db['s3_actions'] and
                 all(_text(a) for a in db['s3_actions']), 'invalid public S3 actions')
        identities.append({'name': db['s3_identity_name'], 'actions': db['s3_actions'],
                           'credentials': s3_pair(s3['AWS_ACCESS_KEY_ID'], s3['AWS_SECRET_ACCESS_KEY'])})
        roots[db['bootstrap_credentials_key']] = json.dumps({db['realm']: {
            'client-id': root['client_id'], 'client-secret': root['client_secret']}}, sort_keys=True)
    _require(not set(root_secrets).intersection(service_secrets),
             'root and service principal secrets must not overlap')
    _require(len(set(access_keys)) == len(access_keys) and
             len(set(secret_keys)) == len(secret_keys) and
             len(set(root_secrets)) == len(root_secrets) and
             len(set(jwt_private_keys)) == len(jwt_private_keys) and
             len({i['name'] for i in identities}) == len(identities), 'duplicate S3 identity')
    secrets[ref_identity(shared['polaris_bootstrap_secret'])] = {'data': roots}
    secrets[ref_identity(shared['seaweedfs_server_secret'])] = {'data': {
        's3.json': json.dumps({'identities': identities}, sort_keys=True)}}
    pg = data(shared['postgres_secret'])
    _require(32 <= len(pg['password']) <= 256 and pg['password'] == pg['password'].strip() and
             not any(c in pg['password'] for c in '\r\n'), 'invalid postgres password')
    result = {'pending_secret_refs': [copy.deepcopy(ref) for ref in metadata['deferred_secret_refs']
                                     if ref_identity(ref) not in secrets],
              'tofu_vars': {
        'warehouse_generation': copy.deepcopy(metadata), 'warehouse_generation_secrets': secrets,
        'warehouse_generation_phase': phase,
        'seaweedfs_s3_operator_access_key': operator['access_key'],
        'seaweedfs_s3_operator_secret_key': operator['secret_key'],
        'local_gitops_s3_credentials': {'access_key': gitops['accesskey'], 'secret_key': gitops['secretkey']},
        'polaris_postgresql_password': pg['password'], 'seaweedfs_s3_credential_generation': '1',
        'polaris_database_credentials': {},
    }}
    if ref_identity(shared['trino_auth_secret']) in secrets:
        runtime = _runtime_auth(metadata, identity, secrets, private_input['passwords'])
        if phase == 'full':
            result['runtime_auth'] = runtime
    else:
        _require(private_input['passwords'] == {}, 'passwords require shared auth material')
    if phase == 'full':
        result['runtime_catalog_auth'] = {'version': 1, 'databases': {
            dbid: dict(service_ref=db['service_ref'], realm=db['realm'], catalog=db['catalog'],
                       generation_id=metadata['generation_id'],
                       client_id=data(db['trino_secret'])['client_id'],
                       client_secret=data(db['trino_secret'])['client_secret'])
            for dbid, db in metadata['databases'].items()}}
    return result


def _runtime_auth(metadata, identity, secrets, passwords):
    users = {identity['platform_admin']}
    for db in identity['databases'].values():
        users.update(db['roles'].values())
    _require(set(passwords) == users, 'password identities mismatch')
    runtime = {'version': 2, 'platform_admin': {'username': identity['platform_admin'],
               'password': passwords[identity['platform_admin']]}, 'databases': {}}
    for dbid, db in identity['databases'].items():
        runtime['databases'][dbid] = {
            'sql_catalog': db['sql_catalog'], 'generation_id': db['generation_id'],
            'roles': {r: {'username': u, 'password': passwords[u]} for r, u in db['roles'].items()}}
    trino_auth.validate_database_config(runtime)
    auth = secrets[ref_identity(metadata['shared']['trino_auth_secret'])]['data']
    _require('-----BEGIN CERTIFICATE-----' in auth['tls.pem'] and
             '-----END CERTIFICATE-----' in auth['tls.pem'] and
             '-----BEGIN PRIVATE KEY-----' in auth['tls.pem'] and
             '-----END PRIVATE KEY-----' in auth['tls.pem'], 'invalid TLS PEM format')
    _verify_passwords(auth['password.db'], passwords)
    return runtime


def write_material(material, directory):
    """Write the phase's private files, rejecting stale backend data in bootstrap."""
    directory = Path(directory)
    phase = material['tofu_vars']['warehouse_generation_phase']
    _require(phase in ('bootstrap', 'full'), 'invalid material phase')
    _require(phase == 'full' or not any((directory / name).exists()
             for name in ('backend.json', 'catalog-auth.json')),
             'bootstrap output directory contains runtime authentication')
    files = [('terraform.tfvars.json', 'tofu_vars')]
    if phase == 'full':
        _require('runtime_auth' in material, 'full material requires backend authentication')
        files.append(('backend.json', 'runtime_auth'))
        _require('runtime_catalog_auth' in material, 'full material requires catalog authentication')
        files.append(('catalog-auth.json', 'runtime_catalog_auth'))
    else:
        _require('runtime_auth' not in material, 'bootstrap material cannot contain backend authentication')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    for name, key in files:
        path = directory / name
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(material[key], stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--private-input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--phase', choices=('bootstrap', 'full'), default='full')
    args = parser.parse_args(argv)
    try:
        from database_generation import load_bundle
        bundle = load_bundle(args.bundle, Path(__file__).resolve().parents[2])
        mode = args.private_input.stat().st_mode
        _require(stat.S_ISREG(mode) and stat.S_IMODE(mode) == 0o600, 'private input must be mode 0600')
        private_input = _json(args.private_input.read_text())
        material = build_material(bundle, private_input, phase=args.phase)
        write_material(material, args.output)
    except Exception:
        print('database material: invalid or unreadable explicit inputs', file=sys.stderr)
        return 1
    print('Wrote {} private material files'.format(1 if args.phase == 'bootstrap' else 3))
    return 0


if __name__ == '__main__':
    sys.exit(main())
