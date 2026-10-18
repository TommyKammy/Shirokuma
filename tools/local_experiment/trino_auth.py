"""Server-side Trino service credentials for the opt-in authenticated lab."""
import argparse
import base64
import json
import os
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
    executable = shutil.which('htpasswd')
    openssl = shutil.which('openssl')
    if not executable or not openssl:
        raise RuntimeError('htpasswd with bcrypt support and openssl are required')
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    config = {role: dict(username=user, password=secrets.token_urlsafe(32))
              for role, user in SERVICE_USERS.items()}
    hashes = []
    for entry in config.values():
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
