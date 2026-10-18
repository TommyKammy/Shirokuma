"""Small server-owned identity boundary for the local HTTP surfaces.

SHIROKUMA_AUTH_FILE maps usernames to role, salt and PBKDF2-SHA256 password_hash.
Without configuration these tools retain their trusted loopback single-user mode.
"""
import base64
from contextlib import contextmanager
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


def authenticate(headers):
    """Return verified (username, role), or None in trusted local mode."""
    if not shared_mode():
        return None
    try:
        users = json.loads(Path(os.environ['SHIROKUMA_AUTH_FILE']).read_text())
        if not isinstance(users, dict) or not users:
            raise ValueError()
        for name, account in users.items():
            if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]+', name) or
                    not isinstance(account, dict) or account.get('role') not in ROLES or
                    len(bytes.fromhex(account['salt'])) < 16 or
                    len(bytes.fromhex(account['password_hash'])) != 32):
                raise ValueError()
    except (KeyError, OSError, ValueError, TypeError):
        raise AuthenticationError('Authentication configuration is unavailable') from None
    values = headers.get_all('Authorization', [])
    try:
        if len(values) != 1:
            raise ValueError()
        scheme, encoded = values[0].split(' ', 1)
        if scheme.lower() != 'basic':
            raise ValueError()
        username, password = base64.b64decode(encoded, validate=True).decode('utf-8').split(':', 1)
        account = users.get(username)
        if not account or not hmac.compare_digest(password_hash(password, account['salt']), account['password_hash']):
            raise ValueError()
    except (ValueError, UnicodeError, binascii.Error):
        raise AuthenticationError('Valid Basic credentials are required') from None
    return username, account['role']


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
    trino_auth.load_credentials()
    trino_auth.backend_ssl_context()
