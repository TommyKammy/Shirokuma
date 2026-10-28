"""Server-side Trino service credentials for the opt-in authenticated lab."""
import argparse
import base64
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import ssl
import stat
import subprocess
from urllib.parse import urlsplit


SERVICE_USERS = {'admin': 'shirokuma_admin', 'data_engineer': 'shirokuma_engineer',
                 'bi_viewer': 'shirokuma_viewer'}


def password_mode():
    mode = os.environ.get('SHIROKUMA_TRINO_AUTH_MODE', 'local')
    if mode not in {'local', 'password'}:
        raise RuntimeError('SHIROKUMA_TRINO_AUTH_MODE must be local or password')
    return mode == 'password'


def load_credentials():
    """Reload private configuration; never retain credentials in logs or errors."""
    location = os.environ.get('SHIROKUMA_TRINO_AUTH_FILE', '')
    if not location:
        raise RuntimeError('password mode requires SHIROKUMA_TRINO_AUTH_FILE')
    try:
        path = Path(location)
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise ValueError('credentials must be a private regular file')
        config = json.loads(path.read_text())
        if not isinstance(config, dict) or set(config) != set(SERVICE_USERS):
            raise ValueError('invalid roles')
        for role, username in SERVICE_USERS.items():
            credential = config[role]
            if not isinstance(credential, dict) or set(credential) != {'username', 'password'}:
                raise ValueError('invalid credentials')
            if credential['username'] != username or not isinstance(credential['password'], str):
                raise ValueError('invalid service user')
            if not credential['password'] or any(c in credential['password'] for c in '\r\n\x00'):
                raise ValueError('invalid password')
        if len({entry['password'] for entry in config.values()}) != len(SERVICE_USERS):
            raise ValueError('roles require distinct passwords')
        return config
    except (OSError, ValueError, TypeError):
        raise RuntimeError('invalid or unreadable Trino service credential file') from None


def backend_headers(headers=None, *, role=None):
    """Use the authenticated caller's role, never their claimed Trino identity."""
    result = dict(headers or {})
    if not password_mode():
        return result
    if role not in SERVICE_USERS:
        raise PermissionError('an authenticated Trino role is required')
    credential = load_credentials()[role]
    removed = {'authorization', 'proxy-authorization', 'x-trino-user',
               'x-trino-original-user', 'x-trino-original-roles', 'x-trino-role',
               'x-trino-extra-credential'}
    result = {key: value for key, value in result.items() if key.lower() not in removed}
    encoded = base64.b64encode((credential['username'] + ':' + credential['password']).encode()).decode()
    result['Authorization'] = 'Basic ' + encoded
    result['X-Trino-User'] = credential['username']
    return result


def scoped_username(database_id, role):
    if not isinstance(database_id, str) or not database_id.strip() or role not in ('bi_viewer', 'data_engineer', 'admin'):
        raise ValueError('invalid scoped identity context')
    return 'shirokuma_db_' + hashlib.sha256(database_id.encode()).hexdigest()[:24] + {
        'bi_viewer': '_viewer', 'data_engineer': '_engineer', 'admin': '_admin'}[role]


def validate_database_config(config):
    """One manifest contract shared by credential loading and policy rendering."""
    from data_management import validate_sql_name
    try:
        if (not isinstance(config, dict) or set(config) != {'version', 'platform_admin', 'databases'} or
                type(config['version']) is not int or config['version'] != 2 or
                not isinstance(config['databases'], dict) or not config['databases']):
            raise ValueError()
        credentials = [(config['platform_admin'], 'shirokuma_admin')]
        catalogs = set()
        for database_id, database in config['databases'].items():
            if not isinstance(database_id, str) or not database_id.strip() or '\x00' in database_id:
                raise ValueError()
            if not isinstance(database, dict) or set(database) != {'sql_catalog', 'generation_id', 'roles'}:
                raise ValueError()
            catalog = validate_sql_name(database['sql_catalog'])
            if catalog in catalogs:
                raise ValueError()
            catalogs.add(catalog)
            generation = database['generation_id']
            if not isinstance(generation, str) or not generation.strip() or any(c in generation for c in '\r\n\x00'):
                raise ValueError()
            roles = database['roles']
            if not isinstance(roles, dict) or set(roles) not in (
                    {'bi_viewer', 'data_engineer'}, {'bi_viewer', 'data_engineer', 'admin'}):
                raise ValueError()
            credentials.extend((entry, scoped_username(database_id, role)) for role, entry in roles.items())
        users, passwords = set(), set()
        for entry, expected in credentials:
            if not isinstance(entry, dict) or set(entry) != {'username', 'password'} or entry['username'] != expected:
                raise ValueError()
            password = entry['password']
            if not isinstance(password, str) or not password.strip() or any(c in password for c in '\r\n\x00'):
                raise ValueError()
            if expected in users or password in passwords:
                raise ValueError()
            users.add(expected)
            passwords.add(password)
        return config
    except (KeyError, ValueError, TypeError):
        raise ValueError('invalid database authentication manifest') from None


def load_database_credentials():
    try:
        location = os.environ.get('SHIROKUMA_TRINO_AUTH_FILE', '')
        if not location:
            raise ValueError()
        path = Path(location)
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise ValueError()
        return validate_database_config(json.loads(path.read_text()))
    except (OSError, ValueError, TypeError):
        raise RuntimeError('invalid or unreadable Trino database credential file') from None


def configured_database_mode():
    """Inspect only configured process state or an explicitly supplied manifest."""
    import runtime_profile
    if runtime_profile.active():
        return True
    if not password_mode():
        return False
    location = os.environ.get('SHIROKUMA_TRINO_AUTH_FILE')
    if not location:
        return False
    try:
        path = Path(location)
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise ValueError()
        config = json.loads(path.read_text())
        return isinstance(config, dict) and config.get('version') == 2
    except (OSError, TypeError, ValueError):
        raise RuntimeError('invalid or unreadable Trino credential file') from None


def backend_headers_v2(headers=None, *, principal, database_id, selected_role):
    return prepare_backend_v2(headers, principal=principal, database_id=database_id,
                              selected_role=selected_role)[1]


def prepare_backend_v2(headers=None, *, principal, database_id, selected_role):
    """Capture the authorization decision and credentials from one configuration read."""
    import request_auth
    if not password_mode():
        raise RuntimeError('database authentication requires password mode')
    import control_store
    import logical_registry
    backend_ssl_context()
    with control_store.connect() as db:
        db.execute('BEGIN')
        logical = logical_registry.creation_mode(db, database_id) == 'logical_v1'
        if logical:
            try:
                binding = logical_registry.resolve_binding(db, database_id)
                decision = request_auth.authorize_database(principal, database_id, selected_role,
                    expected_generation_id=binding['generation_id'], db=db)
                identity = logical_registry.resolve_identity(db, principal.principal_id, database_id,
                    selected_role, decision.grant_revision, decision.platform_admin)
                credential = logical_registry.identity_credentials(identity)
            except ValueError:
                raise PermissionError('Verified database identity is unavailable') from None
        else:
            config = load_database_credentials()
            if not isinstance(database_id, str) or database_id not in config['databases']:
                raise PermissionError('Explicit registered database is required')
            database = config['databases'][database_id]
            decision = request_auth.authorize_database(principal, database_id, selected_role,
                expected_generation_id=database['generation_id'])
            if decision.sql_catalog != database['sql_catalog']:
                raise PermissionError('Database catalog configuration mismatch')
            credential = config['platform_admin'] if decision.platform_admin else database['roles'].get(selected_role)
            if credential is None:
                raise PermissionError('Scoped database credential is unavailable')
    # Drop all identity-bearing Trino and legacy Presto equivalents, including
    # alternate header spellings; callers cannot supply a second identity.
    removed = {'authorization', 'proxy-authorization', 'user', 'original-user',
        'original-roles', 'role', 'extra-credential', 'authorization-user', 'session-user',
        'authenticated-user', 'principal', 'groups'}
    result = {}
    for key, value in (headers or {}).items():
        name = key.lower()
        suffix = re.sub(r'^x-(trino|presto)-', '', name)
        if name not in removed and suffix not in removed:
            result[key] = value
    result['Authorization'] = 'Basic ' + base64.b64encode(
        (credential['username'] + ':' + credential['password']).encode()).decode()
    result['X-Trino-User'] = credential['username']
    return decision, result


def validate_backend_url(url):
    if password_mode():
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise RuntimeError('password-mode Trino requires an HTTPS backend URL')


def backend_ssl_context():
    """Trust the configured lab certificate while keeping normal CA validation."""
    if not password_mode():
        return None
    certificate = os.environ.get('SHIROKUMA_TRINO_CA_FILE', '')
    if not certificate:
        raise RuntimeError('password mode requires SHIROKUMA_TRINO_CA_FILE')
    try:
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=certificate)
        return context
    except (OSError, ssl.SSLError):
        raise RuntimeError('invalid or unreadable Trino CA certificate') from None


def initialize(directory):
    """Create private local credentials and bcrypt hashes without shell arguments."""
    config = {role: dict(username=user, password=secrets.token_urlsafe(32))
              for role, user in SERVICE_USERS.items()}
    return _initialize(directory, config, list(config.values()))


def initialize_database(directory, databases):
    try:
        if not isinstance(databases, dict) or not databases:
            raise ValueError()
        config = {'version': 2, 'platform_admin': dict(username='shirokuma_admin', password=secrets.token_urlsafe(32)),
            'databases': {key: dict(sql_catalog=value['sql_catalog'], generation_id=value['generation_id'],
                roles={role: dict(username=scoped_username(key, role), password=secrets.token_urlsafe(32))
                       for role in ('bi_viewer', 'data_engineer', 'admin')}) for key, value in databases.items()}}
        if any(not isinstance(value, dict) or set(value) != {'sql_catalog', 'generation_id'} for value in databases.values()):
            raise ValueError()
        validate_database_config(config)
    except (KeyError, TypeError, ValueError):
        raise ValueError('invalid database initialization context') from None
    entries = [config['platform_admin']]
    for database in config['databases'].values():
        entries.extend(database['roles'].values())
    return _initialize(directory, config, entries)


def _initialize(directory, config, entries):
    executable = shutil.which('htpasswd')
    openssl = shutil.which('openssl')
    if not executable or not openssl:
        raise RuntimeError('htpasswd with bcrypt support and openssl are required')
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    hashes = []
    for entry in entries:
        hashed = subprocess.run([executable, '-niB', '-C', '10', entry['username']],
                                input=entry['password'] + '\n', text=True, capture_output=True)
        if hashed.returncode or not hashed.stdout.strip().startswith(entry['username'] + ':$2'):
            raise RuntimeError('htpasswd failed to produce a bcrypt password hash')
        hashes.append(hashed.stdout.strip())
    files = {'backend.json': json.dumps(config, indent=2) + '\n',
             'password.db': '\n'.join(hashes) + '\n',
             'shared-secret': secrets.token_urlsafe(48)}
    for name, content in files.items():
        with os.fdopen(os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            stream.write(content)
    certificate = subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
        '-days', '365', '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1',
        '-keyout', str(directory / 'tls.key'), '-out', str(directory / 'tls.crt')], capture_output=True)
    if certificate.returncode:
        raise RuntimeError('openssl failed to create the local TLS certificate')
    (directory / 'tls.key').chmod(0o600)
    (directory / 'tls.crt').chmod(0o600)
    with os.fdopen(os.open(directory / 'tls.pem', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
        stream.write((directory / 'tls.key').read_bytes() + (directory / 'tls.crt').read_bytes())
    return directory


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init'])
    parser.add_argument('directory', type=Path)
    arguments = parser.parse_args()
    initialize(arguments.directory)
    print('Created private Trino credentials and password hashes in', arguments.directory)


def platform_admin_credentials():
    """INTERNAL observer/provisioner accessor; never used by user SQL."""
    import runtime_profile
    profile = runtime_profile.current()
    if profile is not None and profile.mode == 'logical_v1':
        import logical_registry
        return logical_registry.platform_admin_credentials()
    return load_database_credentials()['platform_admin']


def validate_database_backend():
    import runtime_profile
    profile = runtime_profile.current()
    if profile is not None and profile.mode == 'logical_v1':
        runtime_profile.generation_attestation()
        runtime_profile.logical_config(profile)
    else:
        load_database_credentials()
