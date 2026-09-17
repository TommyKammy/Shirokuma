"""Ephemeral synthetic authentication fixtures, never production credentials.

The return value contains secrets and must never be written into a normal
receipt. Only hashes/identities belong in evidence. Good access tokens must be
obtained from the live Keycloak server; host-signed tokens are validation probes.
TLS CA and realm signing keys are independent. There is no host trust change.
"""
import base64
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import time

from .support import canonical, digest, directory, read, require, token, write

REALM = 'shirokuma-lab'
ISSUER = 'https://localhost:18443/realms/' + REALM
AUDIENCE = 'shirokuma-trino'
USERS = ('lab_admin', 'lab_row', 'lab_mask', 'lab_both', 'lab_denied')
NEGATIVE_NAMES = ('wrong_issuer', 'wrong_audience', 'expired', 'unknown_principal', 'bad_signature')


def _private_write(path, value):
    directory(path.parent)
    require(stat.S_IMODE(path.parent.stat().st_mode) == 0o700, 'NONPRIVATE_FIXTURE_DIRECTORY')
    data = value.encode() if isinstance(value, str) else value
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _openssl(args, *, body=None):
    # No command, output, exception text, key or JWT is included in a receipt.
    process = subprocess.run(['openssl', *args], input=body, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=30, check=False)
    require(process.returncode == 0, 'FIXTURE_CRYPTO_FAILED')
    require(len(process.stdout) + len(process.stderr) < 65536, 'FIXTURE_CRYPTO_OUTPUT_LIMIT')
    return process.stdout


def _generate_key(path):
    # OpenSSL normally creates mode 0600, but reserve the target ourselves so
    # even tools/platforms with weaker defaults cannot briefly expose the key.
    _private_write(path, b'')
    # genpkey emits PKCS#8 consistently across LibreSSL/OpenSSL versions.
    _openssl(['genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', str(path)])
    require(read(path).startswith(b'-----BEGIN ' + b'PRIVATE KEY-----'), 'FIXTURE_KEY_FORMAT_INVALID')
    require(stat.S_IMODE(path.stat().st_mode) == 0o600, 'NONPRIVATE_FIXTURE_KEY')


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def _signed(claims, key):
    header = {'alg': 'RS256', 'typ': 'JWT'}
    signing_input = (_b64(canonical(header).encode()) + '.' + _b64(canonical(claims).encode())).encode()
    signature = _openssl(['dgst', '-sha256', '-sign', str(key)], body=signing_input)
    return signing_input.decode() + '.' + _b64(signature)


def _negative_tokens(folder, run_token, now):
    # These controls differ by one claim. The valid host-signed control makes
    # it possible to prove that a negative was not rejected for another reason.
    baseline = {'iss': ISSUER, 'aud': AUDIENCE, 'sub': 'synthetic-' + run_token,
                'preferred_username': 'lab_admin', 'iat': now,
                'nbf': now - 5, 'exp': now + 900}
    variants = {
        'wrong_issuer': {'iss': 'https://localhost:18443/realms/wrong-lab'},
        'wrong_audience': {'aud': 'not-shirokuma-trino'},
        'expired': {'exp': now - 300},
        'unknown_principal': {'preferred_username': 'lab_unregistered'},
    }
    key = folder / 'realm-signing.key'
    result = {'control': _signed(baseline, key)}
    for name, changed in variants.items():
        result[name] = _signed(dict(baseline, **changed), key)
    result['bad_signature'] = _signed(baseline, folder / 'untrusted-signing.key')
    return result


def _realm(credentials, private_key):
    return {
        'realm': REALM, 'enabled': True, 'sslRequired': 'all',
        'registrationAllowed': False, 'resetPasswordAllowed': False,
        'editUsernameAllowed': False, 'accessTokenLifespan': 300,
        'defaultSignatureAlgorithm': 'RS256',
        'defaultDefaultClientScopes': [], 'defaultOptionalClientScopes': [],
        'users': [
            {'username': user, 'enabled': True, 'emailVerified': True,
             'email': user + '@example.invalid', 'firstName': 'Synthetic',
             'lastName': user, 'requiredActions': [],
             'credentials': [{'type': 'password', 'value': credentials['users'][user], 'temporary': False}]}
            for user in USERS
        ],
        'clients': [{
            'clientId': AUDIENCE, 'protocol': 'openid-connect', 'enabled': True,
            'publicClient': False, 'secret': credentials['client_secret'],
            'directAccessGrantsEnabled': True, 'standardFlowEnabled': False,
            'implicitFlowEnabled': False, 'serviceAccountsEnabled': False,
            'fullScopeAllowed': False, 'redirectUris': [], 'webOrigins': [],
            'defaultClientScopes': [], 'optionalClientScopes': [],
            'protocolMappers': [
                # Keycloak 26.7 delegates ordinary access-token sub to this
                # mapper. Empty default scopes intentionally omit built-ins.
                {'name': 'fixed-subject', 'protocol': 'openid-connect',
                 'protocolMapper': 'oidc-sub-mapper', 'config': {'access.token.claim': 'true'}},
                {'name': 'fixed-audience', 'protocol': 'openid-connect',
                 'protocolMapper': 'oidc-audience-mapper', 'config': {
                     'included.custom.audience': AUDIENCE, 'access.token.claim': 'true',
                     'id.token.claim': 'false'}},
                {'name': 'fixed-principal', 'protocol': 'openid-connect',
                 'protocolMapper': 'oidc-usermodel-property-mapper', 'config': {
                     'user.attribute': 'username', 'claim.name': 'preferred_username',
                     'jsonType.label': 'String', 'access.token.claim': 'true',
                     'id.token.claim': 'false', 'userinfo.token.claim': 'false'}},
            ],
        }],
        'components': {'org.keycloak.keys.KeyProvider': [{
            'name': 'local-ephemeral-rsa', 'providerId': 'rsa',
            'config': {'priority': ['100'], 'enabled': ['true'], 'active': ['true'],
                       'algorithm': ['RS256'], 'privateKey': [private_key]},
        }]},
    }


def create_fixtures(run_token, output, *, now=None):
    """Create a fresh private fixture set. Caller owns the private run directory."""
    token(run_token)
    output = Path(output)
    directory(output)
    require(output.name == run_token and stat.S_IMODE(output.stat().st_mode) == 0o700,
            'NONPRIVATE_RUN_DIRECTORY')
    folder = output / 'fixtures'
    require(not folder.exists() and not folder.is_symlink(), 'FIXTURE_COLLISION')
    folder.mkdir(mode=0o700)
    for name in ('ca.key', 'tls.key', 'realm-signing.key', 'untrusted-signing.key'):
        _generate_key(folder / name)
    ca_config = '[req]\ndistinguished_name=dn\nx509_extensions=ca\nprompt=no\n[dn]\nCN=Shirokuma local identity test CA\n[ca]\nbasicConstraints=critical,CA:true\nkeyUsage=critical,keyCertSign,cRLSign\n'
    tls_config = '[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=localhost\n[leaf]\nbasicConstraints=critical,CA:false\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n'
    _private_write(folder / 'ca.cnf', ca_config)
    _private_write(folder / 'tls.cnf', tls_config)
    # Certificates and CSR are also private to keep a single simple boundary.
    for name in ('ca.crt', 'tls.csr', 'tls.crt', 'jwt-public.pem'):
        _private_write(folder / name, b'')
    _openssl(['req', '-new', '-x509', '-sha256', '-days', '2', '-key', str(folder / 'ca.key'),
              '-config', str(folder / 'ca.cnf'), '-out', str(folder / 'ca.crt')])
    _openssl(['req', '-new', '-sha256', '-key', str(folder / 'tls.key'),
              '-config', str(folder / 'tls.cnf'), '-out', str(folder / 'tls.csr')])
    _openssl(['x509', '-req', '-sha256', '-days', '2', '-in', str(folder / 'tls.csr'),
              '-CA', str(folder / 'ca.crt'), '-CAkey', str(folder / 'ca.key'),
              '-set_serial', '0x' + secrets.token_hex(16), '-extfile', str(folder / 'tls.cnf'),
              '-extensions', 'leaf', '-out', str(folder / 'tls.crt')])
    _openssl(['rsa', '-in', str(folder / 'realm-signing.key'), '-pubout', '-out', str(folder / 'jwt-public.pem')])
    _openssl(['verify', '-CAfile', str(folder / 'ca.crt'), str(folder / 'tls.crt')])
    credentials = {'client_id': AUDIENCE, 'client_secret': secrets.token_urlsafe(32),
                   'users': {user: secrets.token_urlsafe(24) for user in USERS}}
    realm = _realm(credentials, read(folder / 'realm-signing.key').decode())
    _private_write(folder / 'realm.json', canonical(realm) + '\n')
    _private_write(folder / 'internal-secret', secrets.token_urlsafe(48))
    write(folder / 'credentials.json', credentials)
    tokens = _negative_tokens(folder, run_token, int(time.time()) if now is None else now)
    write(folder / 'negative-tokens.json', tokens)
    hashes = {path.name: digest(read(path)) for path in sorted(folder.iterdir())}
    require(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in folder.iterdir()),
            'NONPRIVATE_FIXTURE_FILE')
    return {'schema_version': 1, 'run_token': run_token, 'issuer': ISSUER, 'audience': AUDIENCE,
            'users': list(USERS), 'ca_path': folder / 'ca.crt',
            'public_key_path': folder / 'jwt-public.pem', 'credentials': credentials,
            'negative_tokens': tokens, 'hashes': hashes,
            'secret_data': {name: read(folder / name).decode()
                            for name in ('tls.crt', 'tls.key', 'realm.json', 'internal-secret')},
            'public_data': {name: read(folder / name).decode() for name in ('ca.crt', 'jwt-public.pem')}}
