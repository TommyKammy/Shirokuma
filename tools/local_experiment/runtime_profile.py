"""Explicit startup-only activation of the local database runtime."""
from dataclasses import asdict, dataclass, fields
from pathlib import Path
import hashlib
import copy
import ipaddress
import json
import os
import stat
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
_current = None
_startup_fingerprint = None
_startup_binding = None
_startup_identity = None


def private_binding(path):
    """Seal explicit profile and referenced bytes in a private operator receipt."""
    return _seal_profile(load(path), path)


def create_candidate(base_path, path, *, backend, catalog):
    """Create a fresh immutable full profile, preserving all fixed inputs.

    Trusted preparation supplies the new full material. Existing profiles,
    including previously sealed P52 candidates, are never patched in place.
    """
    original = load(base_path)
    path = Path(path)
    if not path.is_absolute() or path.parent.stat().st_mode & 0o777 != 0o700:
        raise ValueError('Private new candidate path required')
    value = profile_values(original)
    value.update(trino_auth_file=str(private_file(backend)),catalog_auth_file=str(private_file(catalog)))
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as output:
        json.dump(value,output,sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    return load(path)


def _seal_profile(profile, path):
    sealed = dict(profile_path=str(Path(path).resolve()),
                  profile=hashlib.sha256(private_file(path).read_bytes()).hexdigest(), files={})
    for name, location in _fixed_files(profile).items():
        sealed['files'][name] = dict(path=location,
            digest=hashlib.sha256(private_file(location).read_bytes()).hexdigest())
    if profile.mode == 'logical_v1':
        _validate_accounts(profile.auth_file, logical=True)
        sealed['authority'] = dict(path=profile.auth_file, form='private0600-json')
    return sealed


def _fingerprint(profile):
    """Private startup binding; never expose credential-derived digests in HTTP."""
    digest = hashlib.sha256()
    for name, path in _fixed_files(profile).items():
        digest.update(path.encode() + b'\0' + private_file(path).read_bytes() + b'\0')
    if profile.mode == 'logical_v1':
        _validate_accounts(profile.auth_file, logical=True)
        digest.update(profile.auth_file.encode() + b'\0private0600-json\0')
    return digest.digest()


def profile_values(profile):
    """Version 1 retains its exact serialized shape and profile_ref input."""
    value = asdict(profile)
    if profile.version == 1:
        value.pop('logical_config_file')
    return value


def _fixed_files(profile):
    if profile.mode == 'logical_v1':
        config = logical_config(profile)
        return dict(trino_ca_file=profile.trino_ca_file, client_auth_file=profile.client_auth_file,
                    logical_config_file=profile.logical_config_file, provider_config_file=config['provider_config_file'])
    return {name: getattr(profile, name) for name in
            ('auth_file', 'trino_auth_file', 'trino_ca_file', 'catalog_auth_file', 'client_auth_file')}


def _public_identity(profile):
    if profile.mode == 'logical_v1':
        config = logical_config(profile)
        return dict(version=2, mode='logical_v1', environment_id=config['environment_id'],
                    warehouse_id=config['warehouse_id'], foundation_id=config['foundation_generation_id'],
                    foundation_revision=config['foundation_revision'],
                    profile_ref=hashlib.sha256(json.dumps(profile_values(profile), sort_keys=True).encode()).hexdigest())
    from trino_auth import validate_database_config
    backend = validate_database_config(private_json(profile.trino_auth_file))
    catalogs = validate_catalog_manifest(private_json(profile.catalog_auth_file))['databases']
    if set(backend['databases']) != set(catalogs):
        raise ValueError('Runtime database identities differ')
    databases = {}
    for key, entry in backend['databases'].items():
        catalog = catalogs[key]
        if entry['generation_id'] != catalog['generation_id']:
            raise ValueError('Runtime generations differ')
        databases[key] = {name: catalog[name] for name in ('service_ref', 'realm', 'catalog', 'generation_id')}
        databases[key]['sql_catalog'] = entry['sql_catalog']
        databases[key]['roles'] = {role: account['username'] for role, account in entry['roles'].items()}
    generations = {entry['generation_id'] for entry in databases.values()}
    if len(generations) != 1:
        raise ValueError('Runtime generations differ')
    # Bind the live server to the exact explicit file references checked by the
    # operator, without exposing paths or password-derived values in HTTP.
    profile_ref = hashlib.sha256(json.dumps(profile_values(profile), sort_keys=True).encode()).hexdigest()
    return dict(version=1, generation_id=generations.pop(), databases=databases, profile_ref=profile_ref)


def validate_generation(path, bundle):
    """Validate an explicit restart profile against one immutable public bundle."""
    try:
        profile = load(path)
        actual = _public_identity(profile)
        identity = json.loads(bundle['files']['identity-public.json'])
        expected = dict(version=1, generation_id=bundle['manifest']['generation_id'], databases={},
                        profile_ref=actual['profile_ref'])
        for key, db in bundle['tofu']['databases'].items():
            expected['databases'][key] = dict(service_ref=db['service_ref'], realm=db['realm'],
                catalog=db['catalog'], sql_catalog=db['sql_catalog'],
                generation_id=bundle['manifest']['generation_id'], roles=identity['databases'][key]['roles'])
        if actual != expected:
            raise ValueError()
        return actual
    except (ValueError, OSError, TypeError, KeyError, AttributeError):
        raise ValueError('Runtime profile does not match the frozen generation') from None


def generation_attestation():
    """Admin-only HTTP handoff evidence from the running server's startup inputs."""
    try:
        if (_current is None or _startup_fingerprint is None or _startup_binding is None
                or _startup_identity is None or _fingerprint(_current) != _startup_fingerprint
                or _seal_profile(_current, _startup_binding['profile_path']) != _startup_binding):
            raise ValueError()
        return copy.deepcopy(_startup_identity)
    except (ValueError, OSError, TypeError, KeyError, AttributeError):
        raise ValueError('Runtime restart on unchanged explicit material required') from None


@dataclass(frozen=True)
class RuntimeProfile:
    state_dir: str
    auth_file: str
    trino_auth_file: str
    trino_ca_file: str
    catalog_auth_file: str
    sql_endpoint_url: str
    client_auth_file: str
    version: int = 1
    mode: str = 'database'
    logical_config_file: str = ''


def private_file(path):
    path = Path(path)
    info = path.stat()
    if not path.is_absolute() or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError('Private file must be absolute and mode 0600')
    return path


def private_json(path):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate configuration field')
            value[key] = item
        return value
    try:
        return json.loads(private_file(path).read_text(), object_pairs_hook=pairs)
    except (OSError, ValueError, TypeError):
        raise ValueError('Private configuration is unavailable or invalid') from None


def validate_catalog_manifest(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'databases'} or
            type(value['version']) is not int or value['version'] != 1 or
            not isinstance(value['databases'], dict) or not value['databases']):
        raise ValueError('Invalid catalog credentials')
    import re
    for key, entry in value['databases'].items():
        if (not isinstance(key, str) or not key or not isinstance(entry, dict) or
                set(entry) != {'service_ref', 'realm', 'catalog', 'generation_id', 'client_id', 'client_secret'} or
                any(not isinstance(v, str) or not v.strip() or any(c in v for c in '\r\n\x00') for v in entry.values()) or
                not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', entry['service_ref'])):
            raise ValueError('Invalid catalog credentials')
    return value


def _validate_accounts(path, *, logical=False):
    accounts = private_json(path)
    from request_auth import ROLES
    if not isinstance(accounts, dict) or not accounts:
        raise ValueError('Invalid account authority')
    for name, account in accounts.items():
        if (not isinstance(name, str) or not name or not isinstance(account, dict) or
                account.get('role') not in ROLES or type(account.get('platform_admin')) is not bool or
                (account['platform_admin'] and account['role'] != 'admin') or
                len(bytes.fromhex(account['salt'])) < 16 or len(bytes.fromhex(account['password_hash'])) != 32):
            raise ValueError('Invalid account authority')
        if logical and any(key in account and type(account[key]) is not bool for key in ('enabled', 'disabled')):
            raise ValueError('Invalid account authority')


def credential_ref(value):
    """Server minted identifier, never a path."""
    import re
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', value):
        raise ValueError('Invalid private reference')
    return value


def logical_config(profile=None):
    """Read fixed logical settings, without interpreting provider private content."""
    try:
        profile = profile or current()
        if profile is None or profile.mode != 'logical_v1':
            raise ValueError()
        value = private_json(profile.logical_config_file)
        if (not isinstance(value, dict) or set(value) != {'version', 'environment_id', 'warehouse_id',
                'foundation_generation_id', 'foundation_revision', 'credentials_dir',
                'platform_admin_credential_ref', 'provider_config_file', 'executor_id'} or
                type(value['version']) is not int or value['version'] != 1 or
                type(value['foundation_revision']) is not int or value['foundation_revision'] < 1):
            raise ValueError()
        for name in ('environment_id', 'warehouse_id', 'foundation_generation_id', 'executor_id'):
            if not isinstance(value[name], str) or not value[name].strip():
                raise ValueError()
        import re
        if not re.fullmatch(r'[A-Za-z0-9_.@-]+', value['executor_id']):
            raise ValueError()
        directory = Path(value['credentials_dir'])
        if not directory.is_absolute() or not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode) != 0o700:
            raise ValueError()
        credential_ref(value['platform_admin_credential_ref'])
        private_file(value['provider_config_file'])
        return value
    except (ValueError, OSError, TypeError, KeyError, AttributeError):
        raise ValueError('Invalid logical runtime configuration') from None


def load(path):
    try:
        value = private_json(path)
        keys = {f.name for f in fields(RuntimeProfile)}
        if isinstance(value, dict) and value.get('version') == 1:
            keys.remove('logical_config_file')
        if not isinstance(value, dict) or set(value) != keys:
            raise ValueError()
        profile = RuntimeProfile(**value)
        if type(profile.version) is not int or (profile.version, profile.mode) not in ((1, 'database'), (2, 'logical_v1')):
            raise ValueError()
        names = ('state_dir', 'auth_file', 'trino_ca_file', 'client_auth_file') + (
            ('logical_config_file',) if profile.mode == 'logical_v1' else ('trino_auth_file', 'catalog_auth_file'))
        for name in names:
            location = getattr(profile, name)
            if not isinstance(location, str) or not Path(location).is_absolute():
                raise ValueError()
        if not Path(profile.state_dir).is_dir():
            raise ValueError()
        url = urlsplit(profile.sql_endpoint_url)
        if url.scheme not in ('http', 'https') or url.username or url.password or url.query or url.fragment:
            raise ValueError()
        if url.hostname != 'localhost' and not ipaddress.ip_address(url.hostname).is_loopback:
            raise ValueError()
        url.port
        if profile.mode == 'logical_v1':
            if any(not isinstance(location, str) or (location and not Path(location).is_absolute())
                   for location in (profile.trino_auth_file, profile.catalog_auth_file)):
                raise ValueError()
            logical_config(profile)
        else:
            from trino_auth import validate_database_config
            validate_database_config(private_json(profile.trino_auth_file))
            validate_catalog_manifest(private_json(profile.catalog_auth_file))
        private_file(profile.trino_ca_file)
        _validate_accounts(profile.auth_file, logical=profile.mode == 'logical_v1')
        from request_auth import ROLES
        client = private_json(profile.client_auth_file)
        if (not isinstance(client, dict) or set(client) != {'username', 'password', 'selected_role'} or
                client['selected_role'] not in ROLES or
                any(not isinstance(client[k], str) or not client[k] for k in ('username', 'password'))):
            raise ValueError()
        _public_identity(profile)
        return profile
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('Invalid database runtime profile') from None


def configure_process():
    global _current, _startup_fingerprint, _startup_binding, _startup_identity
    _current = None
    _startup_fingerprint = None
    _startup_binding = None
    _startup_identity = None
    explicit = os.environ.get('SHIROKUMA_RUNTIME_PROFILE')
    default = ROOT / 'var/shirokuma/runtime-profile.json'
    path = Path(explicit) if explicit is not None else default
    if explicit is None and not path.exists() and not default.with_suffix('.active').exists():
        _current = None
        return False
    before = private_binding(path)
    profile = load(path)
    identity = _public_identity(profile)
    fingerprint = _fingerprint(profile)
    if before != private_binding(path):
        raise ValueError('Runtime startup inputs changed; explicit restart required')
    os.environ.update(SHIROKUMA_RUNTIME_PROFILE=str(path.resolve()),
        SHIROKUMA_STATE_DIR=profile.state_dir, SHIROKUMA_AUTH_MODE='shared',
        SHIROKUMA_AUTH_FILE=profile.auth_file, SHIROKUMA_TRINO_AUTH_MODE='password',
        SHIROKUMA_TRINO_AUTH_FILE=profile.trino_auth_file, SHIROKUMA_TRINO_CA_FILE=profile.trino_ca_file)
    _current = profile
    _startup_fingerprint = fingerprint
    _startup_binding = before
    _startup_identity = identity
    return True


def active():
    return _current is not None


def current():
    return _current


def auth_overlay(files):
    result = dict(files)
    if active() and current().mode == 'database':
        from trino_auth import validate_database_config
        from database_security import render_file_acl, render_opa_policy
        config = validate_database_config(private_json(current().trino_auth_file))
        base = 'deploy/local-experiment/trino/secure/'
        result[base + 'rules.json'] = (json.dumps(render_file_acl(config), sort_keys=True) + '\n').encode()
        result[base + 'opa/guard.rego'] = render_opa_policy(config).encode()
    return result
