"""Small server-owned identity boundary for the local HTTP surfaces.

SHIROKUMA_AUTH_FILE maps usernames to role, salt and PBKDF2-SHA256 password_hash.
Without configuration these tools retain their trusted loopback single-user mode.
"""
import base64
from dataclasses import dataclass
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from functools import wraps
import binascii
import hashlib
import hmac
import ipaddress
import json
import os
import re
from pathlib import Path
from typing import Optional

ROLES = {'bi_viewer': 0, 'data_engineer': 1, 'admin': 2}
ITERATIONS = 600_000


class AuthenticationError(PermissionError):
    pass


def shared_mode():
    mode = os.environ.get('SHIROKUMA_AUTH_MODE', 'local')
    if mode not in {'local', 'shared'}:
        raise AuthenticationError('Invalid authentication mode')
    return mode == 'shared' or bool(os.environ.get('SHIROKUMA_AUTH_FILE'))


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), ITERATIONS).hex()


def validate_bind(bind):
    if shared_mode():
        validate_shared_backend()
        return
    try:
        local = ipaddress.ip_address(bind).is_loopback
    except ValueError:
        local = bind == 'localhost'
    if not local:
        raise AuthenticationError('Non-loopback HTTP requires configured shared authentication')


def _accounts(*, principal_mode=False, auth_file=None):
    try:
        users = json.loads(Path(auth_file or os.environ['SHIROKUMA_AUTH_FILE']).read_text())
        if not isinstance(users, dict) or not users:
            raise ValueError()
        for name, account in users.items():
            if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]+', name) or
                    not isinstance(account, dict) or account.get('role') not in ROLES or
                    len(bytes.fromhex(account['salt'])) < 16 or
                    len(bytes.fromhex(account['password_hash'])) != 32):
                raise ValueError()
            if principal_mode and (type(account.get('platform_admin')) is not bool or
                    (account['platform_admin'] and account['role'] != 'admin')):
                raise ValueError()
    except (KeyError, OSError, ValueError, TypeError):
        raise AuthenticationError('Authentication configuration is unavailable') from None
    return users


def _verify_basic(headers, users):
    try:
        if hasattr(headers, 'get_all'):
            values = headers.get_all('Authorization', [])
        else:
            values = [value for key, value in headers.items() if key.lower() == 'authorization']
        if len(values) != 1:
            raise ValueError()
        scheme, encoded = values[0].split(' ', 1)
        if scheme.lower() != 'basic':
            raise ValueError()
        username, password = base64.b64decode(encoded, validate=True).decode('utf-8').split(':', 1)
        account = users.get(username)
        if not account or not hmac.compare_digest(password_hash(password, account['salt']), account['password_hash']):
            raise ValueError()
    except (ValueError, UnicodeError, binascii.Error, AttributeError, TypeError):
        raise AuthenticationError('Valid Basic credentials are required') from None
    return username, account


def authenticate(headers):
    """Return verified (username, role), or None in trusted local mode."""
    if not shared_mode():
        return None
    username, account = _verify_basic(headers, _accounts())
    return username, account['role']


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    principal_id: str
    maximum_role: str
    platform_admin: bool


@dataclass(frozen=True)
class DatabaseAuthorization:
    principal_id: str
    database_id: str
    sql_catalog: str
    selected_role: str
    platform_admin: bool
    database_revision: int
    grant_revision: Optional[int]
    generation_id: str


def _principal_accounts():
    if (not shared_mode() or os.environ.get('SHIROKUMA_AUTH_MODE', 'local') != 'shared' or
            not os.environ.get('SHIROKUMA_AUTH_FILE')):
        raise AuthenticationError('Configured shared authentication is required')
    import runtime_profile
    profile = runtime_profile.current()
    if profile is not None and profile.mode == 'logical_v1':
        try:
            runtime_profile._validate_accounts(profile.auth_file, logical=True)
        except (ValueError, OSError, KeyError, TypeError):
            raise AuthenticationError('Authentication configuration is unavailable') from None
    return _accounts(principal_mode=True, auth_file=profile.auth_file if profile is not None and profile.mode == 'logical_v1' else None)


def authenticate_principal(headers):
    username, account = _verify_basic(headers, _principal_accounts())
    return AuthenticatedPrincipal(username, account['role'], account['platform_admin'])


def authorize_database(principal, database_id, selected_role, *, expected_generation_id, db=None):
    """Revalidate current account and committed registry metadata on every call."""
    import control_store
    import data_management as registry
    import warehouse_admission
    with (control_store.connect() if db is None else nullcontext(db)) as db:
        decision = _authorize_database_current(principal, database_id, selected_role,
            expected_generation_id=expected_generation_id, db=db)
        import logical_registry
        if logical_registry.creation_mode(db, database_id) == 'logical_v1':
            try:
                logical_registry.resolve_binding(db, database_id, expected_generation_id)
            except ValueError:
                raise PermissionError('Database is not ready for this generation') from None
        database = registry.get_database(db, database_id)
        if (database.state != 'READY' and not warehouse_admission.continuing_database(
                db, principal.principal_id, database_id, expected_generation_id)):
            raise PermissionError('Database is not ready for this generation')
        return decision


def _authorize_database_current(principal, database_id, selected_role, *, expected_generation_id, db):
    """Current authority metadata for private denial classification only.

    Ignores only database lifecycle. This is not execution or result authority;
    callers must compare the returned grant epoch with the original snapshot.
    The caller supplies the connection; no continuation capability is created.
    """
    import data_management as registry
    if (type(principal) is not AuthenticatedPrincipal or
            any(not isinstance(value, str) or not value.strip()
                for value in (principal.principal_id, database_id, expected_generation_id)) or
            not isinstance(selected_role, str) or selected_role not in ROLES):
        raise PermissionError('Explicit authenticated database context is required')
    account = _principal_accounts().get(principal.principal_id)
    if (not account or account.get('enabled', True) is not True or account.get('disabled', False) is not False or
            principal.maximum_role not in ROLES or ROLES[selected_role] > ROLES[principal.maximum_role] or
            ROLES[selected_role] > ROLES[account['role']]):
        raise PermissionError('Database role is not authorized')
    admin = account['platform_admin'] and account['role'] == 'admin'
    database = registry.get_database(db, database_id)
    binding = registry.get_binding(db, database_id)
    if (database is None or binding is None or
            binding.applied_revision != binding.desired_revision or binding.desired_revision <= 0 or
            binding.applied_generation_id != expected_generation_id or
            registry.get_generation(db, expected_generation_id) is None):
        raise PermissionError('Database is not ready for this generation')
    grant_revision = None
    if not (selected_role == 'admin' and admin):
        from grant_management import effective
        grant = effective(db, principal.principal_id, database_id)
        if (grant is None or grant['maximum_role'] not in ROLES or
                ROLES[selected_role] > ROLES[grant['maximum_role']]):
            raise PermissionError('Explicit database grant is required')
        grant_revision = grant['epoch']
    import logical_registry
    if logical_registry.creation_mode(db, database_id) == 'logical_v1':
        try:
            if database.state == 'retiring':
                # Metadata classification only: authorize_database separately
                # requires the exact persisted ACTIVE continuation capability.
                logical = logical_registry.get_binding(db, database_id)
                root = logical_registry._one(db, 'SELECT logical_state FROM db_creations WHERE database_id=?', (database_id,))
                foundation = (logical_registry.get_foundation(db, logical['environment_id'], logical['warehouse_id'])
                              if logical is not None else None)
                if (logical is None or foundation is None or root['logical_state'] != 'READY' or
                        logical['foundation_revision'] != foundation['revision'] or
                        foundation['generation_id'] != expected_generation_id or
                        logical['applied_policy_revision'] <= 0 or not logical['verification_ref']):
                    raise ValueError()
                logical_registry._verified_foundation(db, foundation)
                logical_registry._identity_for_binding(db, dict(logical, generation_id=expected_generation_id),
                    principal.principal_id, database_id, selected_role, grant_revision, selected_role == 'admin' and admin)
            else:
                logical_registry.resolve_binding(db, database_id, expected_generation_id)
                logical_registry.resolve_identity(db, principal.principal_id, database_id,
                    selected_role, grant_revision, selected_role == 'admin' and admin)
        except ValueError:
            raise PermissionError('Verified database identity is unavailable') from None
    return DatabaseAuthorization(principal.principal_id, database_id, database.sql_catalog,
        selected_role, selected_role == 'admin' and admin,
        database.revision, grant_revision, expected_generation_id)


def select_role(maximum, selectors):
    aliases = {'viewer': 'bi_viewer', 'bi_reader': 'bi_viewer', 'editor': 'data_engineer'}
    result = maximum
    for selector in selectors:
        role = aliases.get(selector.strip().lower(), selector.strip().lower())
        if role not in ROLES or ROLES[role] > ROLES[maximum]:
            raise PermissionError('User cannot assume role: ' + selector)
        if ROLES[role] < ROLES[result]:
            result = role
    return result


_ROLE = ContextVar('shirokuma_role', default='admin')


def current_role():
    return _ROLE.get()


def set_role(role):
    _ROLE.set(role)
    return role


@contextmanager
def effective_role(role):
    token = _ROLE.set(role)
    try:
        yield
    finally:
        _ROLE.reset(token)


def role_scope(function):
    @wraps(function)
    def scoped(*args, **kwargs):
        with effective_role('admin'):
            return function(*args, **kwargs)
    return scoped


def validate_shared_backend():
    import trino_auth
    if not trino_auth.password_mode():
        raise AuthenticationError('Shared HTTP services require password-authenticated Trino')
    if trino_auth.configured_database_mode():
        trino_auth.validate_database_backend()
        _principal_accounts()
    else:
        trino_auth.load_credentials()
    trino_auth.backend_ssl_context()
