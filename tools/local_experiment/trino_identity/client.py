"""Fixed HTTPS probes; bearer values never become receipt fields."""
import base64
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from support import ExperimentError, canonical, digest, require

ISSUER = 'https://localhost:18443/realms/shirokuma-lab'
AUDIENCE = 'shirokuma-trino'
TRINO_URL = 'https://localhost:18444'
USERS = ('lab_admin', 'lab_row', 'lab_mask', 'lab_both', 'lab_denied')
SELECT = 'SELECT nationkey, name FROM tpch.tiny.nation ORDER BY nationkey'
NATIONS = ('ALGERIA', 'ARGENTINA', 'BRAZIL', 'CANADA', 'EGYPT', 'ETHIOPIA', 'FRANCE', 'GERMANY',
           'INDIA', 'INDONESIA', 'IRAN', 'IRAQ', 'JAPAN', 'JORDAN', 'KENYA', 'MOROCCO', 'MOZAMBIQUE',
           'PERU', 'CHINA', 'ROMANIA', 'SAUDI ARABIA', 'VIETNAM', 'RUSSIA', 'UNITED KINGDOM', 'UNITED STATES')
QUERY_ID = re.compile(r'\d{8}_\d{6}_\d{5}_[a-z0-9]{5}')
NEGATIVES = ('bad_signature', 'wrong_issuer', 'wrong_audience', 'expired', 'unknown_principal')
CASES = (
    ('admin_before', 'lab_admin', 'success'), ('row', 'lab_row', 'success'),
    ('mask', 'lab_mask', 'success'), ('both', 'lab_both', 'success'), ('denied', 'lab_denied', 'denied'),
    ('fixture_control', 'control', 'success'),
    ('no_token', None, 'unauthenticated'),
    *((name, name, 'unauthenticated') for name in NEGATIVES),
    ('forge_user', 'lab_mask', 'denied'), ('forge_original', 'lab_mask', 'denied'),
    ('admin_after', 'lab_admin', 'success'),
)


def expected_rows(user):
    if user == 'lab_admin':
        return [[i, name] for i, name in enumerate(NATIONS)]
    if user == 'lab_row':
        return [[0, 'ALGERIA']]
    if user == 'lab_mask':
        return [[i, 'MASKED'] for i in range(25)]
    if user == 'lab_both':
        return [[0, 'MASKED']]
    raise ExperimentError('UNEXPECTED_SUCCESS_USER')


def decode_segment(value):
    require(type(value) is str and re.fullmatch(r'[A-Za-z0-9_-]+', value), 'TOKEN_FORMAT_INVALID')
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def verify_signature(token, public_key_path):
    pieces = token.split('.')
    require(len(pieces) == 3 and len(token) <= 16384, 'TOKEN_FORMAT_INVALID')
    with tempfile.TemporaryDirectory(prefix='signature-', dir=public_key_path.parent) as temporary:
        signature = Path(temporary) / 'signature'
        with os.fdopen(os.open(signature, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(decode_segment(pieces[2]))
        result = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', str(public_key_path), '-signature', str(signature)],
            input=(pieces[0] + '.' + pieces[1]).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, env={key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR') if key in os.environ})
    require(result.returncode == 0 and result.stdout.strip() == b'Verified OK', 'TOKEN_SIGNATURE_INVALID')


def token_projection(token, expected_user, public_key_path, now=None, *, fixture_control=False):
    require(type(token) is str and len(token) <= 16384, 'TOKEN_FORMAT_INVALID')
    pieces = token.split('.')
    require(len(pieces) == 3, 'TOKEN_FORMAT_INVALID')
    header, claims = json.loads(decode_segment(pieces[0])), json.loads(decode_segment(pieces[1]))
    require(type(header) is dict and type(claims) is dict and header.get('alg') == 'RS256', 'TOKEN_ALGORITHM_INVALID')
    verify_signature(token, public_key_path)
    audience, now = claims.get('aud'), time.time() if now is None else now
    require(claims.get('iss') == ISSUER, 'TOKEN_ISSUER_MISMATCH')
    require(audience == AUDIENCE or (type(audience) is list and AUDIENCE in audience), 'TOKEN_AUDIENCE_MISMATCH')
    require(expected_user in USERS and claims.get('preferred_username') == expected_user, 'TOKEN_PRINCIPAL_MISMATCH')
    require(type(claims.get('sub')) is str and bool(claims['sub']), 'TOKEN_SUBJECT_MISSING')
    require(claims['sub'] != expected_user, 'TOKEN_SUBJECT_NOT_DISTINCT')
    require(type(claims.get('exp')) is int and type(claims.get('iat')) is int, 'TOKEN_TIMES_INVALID')
    require(claims['iat'] <= now + 5, 'TOKEN_ISSUED_IN_FUTURE')
    require(now + 5 < claims['exp'], 'TOKEN_EXPIRY_TOO_CLOSE')
    require(0 < claims['exp'] - claims['iat'] <= (900 if fixture_control else 600), 'TOKEN_LIFETIME_INVALID')
    return {'sha256': digest(token.encode()), 'user': expected_user, 'issuer': ISSUER, 'audience': AUDIENCE,
            'algorithm': 'RS256', 'signature_verified': True, 'expires_at': claims['exp'], 'issued_at': claims['iat'],
            'principal_field': 'preferred_username',
            'source': 'host-signed-validation-control' if fixture_control else 'keycloak-password-grant'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ExperimentError('TLS_REDIRECT_REFUSED')


class Client:
    def __init__(self, ca_path):
        self.context = ssl.create_default_context(cafile=str(ca_path))
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        require(self.context.check_hostname and self.context.verify_mode == ssl.CERT_REQUIRED, 'TLS_VERIFICATION_REQUIRED')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                                urllib.request.HTTPSHandler(context=self.context))

    def request(self, url, *, data=None, headers=None, timeout=10):
        parsed = urllib.parse.urlsplit(url)
        require(parsed.scheme == 'https' and parsed.hostname == 'localhost' and parsed.port in {18443, 18444}
                and not parsed.username and not parsed.password and not parsed.fragment and not parsed.query,
                'TLS_TARGET_INVALID')
        request = urllib.request.Request(url, data=data, headers=headers or {})
        try:
            response = self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read(1024**2 + 1)
            result = {'status': response.status, 'authenticate': response.headers.get('WWW-Authenticate', ''), 'body': body}
        require(len(body) <= 1024**2, 'TLS_RESPONSE_LIMIT')
        return result

    def ready(self, service, bearer=None, timeout=180):
        require(service in {'keycloak', 'trino'}, 'READINESS_SERVICE_INVALID')
        url = ISSUER + '/.well-known/openid-configuration' if service == 'keycloak' else TRINO_URL + '/v1/info'
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                response = self.request(url, headers={'Authorization': 'Bearer ' + bearer} if bearer else {},
                                        timeout=max(0.1, min(10, deadline - time.monotonic())))
            except urllib.error.URLError as error:
                if isinstance(error.reason, ssl.SSLCertVerificationError):
                    raise ExperimentError('TLS_CERTIFICATE_INVALID') from None
            except (TimeoutError, ConnectionError):
                pass
            else:
                require(response['status'] in {200, 404, 502, 503, 504}, 'READINESS_HTTP_INVALID')
                if response['status'] == 200:
                    value = json.loads(response['body'])
                    if service == 'keycloak':
                        require(value.get('issuer') == ISSUER and value.get('token_endpoint') == ISSUER + '/protocol/openid-connect/token',
                                'ISSUER_DISCOVERY_MISMATCH')
                        return {'service': 'keycloak', 'issuer': ISSUER, 'token_endpoint': value['token_endpoint'],
                                'tls_certificate_verified': True, 'observed_at': datetime.now(timezone.utc).isoformat()}
                    require(value.get('nodeVersion', {}).get('version') == '483'
                            and type(value.get('starting')) is bool, 'TRINO_IDENTITY_MISMATCH')
                    if value['starting'] is False:
                        return {'service': 'trino', 'version': '483', 'starting': False,
                                'tls_certificate_verified': True, 'observed_at': datetime.now(timezone.utc).isoformat()}
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        raise ExperimentError('SERVICE_NOT_READY')

    def acquire(self, user, credentials, public_key_path):
        require(user in USERS and set(credentials['users']) == set(USERS), 'TOKEN_USER_INVALID')
        body = urllib.parse.urlencode({'grant_type': 'password', 'client_id': credentials['client_id'],
            'client_secret': credentials['client_secret'], 'username': user, 'password': credentials['users'][user]}).encode()
        response = self.request(ISSUER + '/protocol/openid-connect/token', data=body,
                                headers={'Content-Type': 'application/x-www-form-urlencoded'})
        require(response['status'] == 200, 'TOKEN_ISSUANCE_FAILED')
        value = json.loads(response['body'])
        require(value.get('token_type', '').lower() == 'bearer', 'TOKEN_TYPE_INVALID')
        bearer = value.get('access_token')
        return bearer, token_projection(bearer, user, public_key_path)

    def query(self, case, bearer, progress):
        require(case in {c[0] for c in CASES}, 'QUERY_CASE_INVALID')
        headers = {'Authorization': 'Bearer ' + bearer} if bearer is not None else {}
        if case in {'no_token', 'forge_user'}:
            headers['X-Trino-User'] = 'lab_admin'
        elif case == 'forge_original':
            headers.update({'X-Trino-User': 'lab_mask', 'X-Trino-Original-User': 'lab_admin'})
        result = {'case': case, 'result': 'pending', 'query_id': None, 'http_status': None,
                  'error_name': None, 'response_complete': False, 'rows': [], 'sql_sha256': digest(SELECT.encode())}
        progress(copy.deepcopy(result))
        url, first, total, columns, deadline = TRINO_URL + '/v1/statement', True, 0, None, time.monotonic() + 45
        while True:
            require(time.monotonic() < deadline, 'QUERY_DEADLINE')
            response = self.request(url, data=SELECT.encode() if first else None, headers=headers,
                                    timeout=max(0.1, min(10, deadline - time.monotonic())))
            result['http_status'] = response['status']
            if response['status'] == 401:
                require(first and response['authenticate'] == 'Bearer realm="Trino", token_type="JWT"', 'AUTH_CHALLENGE_INVALID')
                result.update(result='unauthenticated', response_complete=True)
                progress(copy.deepcopy(result))
                return result
            require(response['status'] == 200, 'TRINO_HTTP_INVALID')
            first = False
            total += len(response['body'])
            require(total <= 2 * 1024**2, 'QUERY_BYTES_LIMIT')
            value = json.loads(response['body'])
            query_id = value.get('id')
            require(type(query_id) is str and QUERY_ID.fullmatch(query_id)
                    and result['query_id'] in {None, query_id}, 'QUERY_ID_MISMATCH')
            result['query_id'] = query_id
            rows = value.get('data', [])
            require(type(rows) is list and len(result['rows']) + len(rows) <= 25, 'QUERY_ROWS_LIMIT')
            result['rows'].extend(rows)
            if value.get('columns') is not None:
                current = [(v.get('name'), v.get('type')) for v in value['columns']]
                require(columns is None or columns == current, 'QUERY_COLUMNS_CHANGED')
                columns = current
            if value.get('error'):
                require(value['error'].get('errorName') == 'PERMISSION_DENIED' and result['rows'] == [], 'UNEXPECTED_QUERY_ERROR')
                result.update(result='denied', error_name='PERMISSION_DENIED', response_complete=True)
                progress(copy.deepcopy(result))
                return result
            url = value.get('nextUri')
            if url is None:
                require(columns == [('nationkey', 'bigint'), ('name', 'varchar(25)')]
                        and all(type(row) is list and len(row) == 2 and type(row[0]) is int and type(row[1]) is str
                                for row in result['rows']), 'QUERY_RESULT_SCHEMA_INVALID')
                result.update(result='success', response_complete=True)
                progress(copy.deepcopy(result))
                return result
            parsed = urllib.parse.urlsplit(url)
            pieces = parsed.path.split('/')
            require(parsed.scheme + '://' + parsed.netloc == TRINO_URL and not parsed.query and not parsed.fragment
                    and len(pieces) >= 6 and pieces[:3] == ['', 'v1', 'statement']
                    and pieces[3] in {'queued', 'executing'} and pieces[4] == query_id, 'UNSAFE_NEXT_URI')
            progress(copy.deepcopy(result))
