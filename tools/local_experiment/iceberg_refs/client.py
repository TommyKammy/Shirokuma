"""Bounded Polaris reference commits; unknown outcomes are never blindly retried.

The OAuth flow and no-redirect transport reuse the local Polaris convention.
Credential loading is independently bounded by time and output size; the generic
request method is replaced by a fixed-origin, size-limited JSON transport.
Only this invocation's fixed table and two reference names are
available here; table creation, data writes and cleanup belong to the runner.
"""
from __future__ import annotations

import copy
import base64
import json
import os
import selectors
import signal
import subprocess
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode
from urllib.request import Request

from tools.local_experiment import polaris_permissions as AUTH
from tools.local_experiment.iceberg_refs import contract as C

BASE_URL = f'http://127.0.0.1:{C.PORT}'
TOKEN_PATH = '/api/catalog/v1/oauth/tokens'
SECRET_TIMEOUT_SECONDS = 15
MAX_SECRET_BYTES = 64 * 1024


def _read_secret():
    """Read one fixed Secret; bound combined pipe bytes before buffering them."""
    command = ['kubectl', '--context', AUTH.CONTEXT, '--request-timeout=10s', '-n',
               'shirokuma-dev', 'get', 'secret', 'polaris-root-credentials', '-o', 'json']
    process = None
    output, received = bytearray(), 0
    deadline = time.monotonic() + SECRET_TIMEOUT_SECONDS
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RefError('SECRET_READ_TIMEOUT', outcome='not-sent')
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), min(8192, MAX_SECRET_BYTES + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    received += len(chunk)
                    if received > MAX_SECRET_BYTES:
                        raise RefError('SECRET_READ_TOO_LARGE', outcome='not-sent')
                    if key.data == 'stdout':
                        output.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RefError('SECRET_READ_TIMEOUT', outcome='not-sent')
        if process.wait(timeout=remaining) != 0:
            raise RefError('SECRET_READ_FAILED', outcome='not-sent')
        return bytes(output)
    except subprocess.TimeoutExpired:
        raise RefError('SECRET_READ_TIMEOUT', outcome='not-sent') from None
    except OSError:
        raise RefError('SECRET_READ_FAILED', outcome='not-sent') from None
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=2)
            for pipe in (process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()


class RefError(RuntimeError):
    def __init__(self, code, *, outcome='unknown', http_status=None):
        self.code, self.outcome, self.http_status = code, outcome, http_status
        super().__init__(code)


class RefConflict(RefError):
    def __init__(self):
        super().__init__('REF_COMMIT_CONFLICT', outcome='rejected', http_status=409)


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON key')
        value[key] = item
    return value


def _json(response):
    raw = response.read(C.MAX_RESPONSE_BYTES + 1)
    if len(raw) > C.MAX_RESPONSE_BYTES:
        raise RefError('RESPONSE_TOO_LARGE')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if type(value) is not dict:
            raise ValueError()
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise RefError('RESPONSE_JSON_INVALID') from None


def _header(value, code, limit):
    if (type(value) is not str or not value or len(value) > limit or not value.isascii()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise RefError(code, outcome='not-sent')
    return value


class _Authentication(AUTH.PolarisClient):
    def __init__(self, guard, opener=None):
        super().__init__(C.PORT)
        self.guard = guard
        if opener is not None:
            self.opener = opener

    def authenticate(self):
        # Do not inherit the old helper's unbounded kubectl subprocess. Keep the
        # same fixed Secret and client-credentials flow with stricter IO bounds.
        self.guard()
        raw = _read_secret()
        try:
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=_object)
            data = value['data']
            if type(data) is not dict or len(data) > 16:
                raise ValueError()
            values = {key: base64.b64decode(data[key], validate=True).decode('ascii')
                      for key in ('client_id', 'client_secret', 'realm')}
            _header(values['client_id'], 'CREDENTIAL_INVALID', 512)
            _header(values['client_secret'], 'CREDENTIAL_INVALID', 4096)
            self.realm = _header(values['realm'], 'REALM_INVALID', 256)
            if ':' in values['client_id']:
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError, RefError):
            raise RefError('SECRET_CONTENT_INVALID', outcome='not-sent') from None
        basic = base64.b64encode((values['client_id'] + ':' + values['client_secret']).encode()).decode()
        response = self.request('POST', TOKEN_PATH,
            urlencode({'grant_type': 'client_credentials', 'scope': 'PRINCIPAL_ROLE:ALL'}).encode(),
            {'Authorization': 'Basic ' + basic, 'Content-Type': 'application/x-www-form-urlencoded'})
        self.token = _header(response.get('access_token'), 'TOKEN_INVALID', 16384)

    def exchange(self, method, path, body=None, headers=None):
        self.guard()
        if self.base != BASE_URL or method not in {'GET', 'POST'}:
            raise RefError('TRANSPORT_SCOPE_MISMATCH', outcome='not-sent')
        request_headers = {'Accept': 'application/json',
            'Polaris-Realm': _header(self.realm, 'REALM_INVALID', 256)}
        if self.token:
            request_headers['Authorization'] = 'Bearer ' + _header(self.token, 'TOKEN_INVALID', 16384)
        if headers:
            request_headers.update(headers)
        data = json.dumps(body, allow_nan=False, separators=(',', ':')).encode() if type(body) is dict else body
        if type(body) is dict:
            request_headers['Content-Type'] = 'application/json'
        request = Request(BASE_URL + path, data=data, headers=request_headers, method=method)
        try:
            with self.opener.open(request, timeout=20) as response:
                if response.status != 200 or response.geturl() != BASE_URL + path:
                    raise RefError('RESPONSE_STATUS_OR_ORIGIN_INVALID', http_status=response.status)
                if response.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                    raise RefError('RESPONSE_CONTENT_TYPE_INVALID')
                return _json(response)
        except HTTPError as error:
            status = error.code
            if status == 409 and method == 'POST' and path != TOKEN_PATH:
                try:
                    value = _json(error).get('error')
                    if (type(value) is dict and value.get('type') == 'CommitFailedException'
                            and type(value.get('code')) is int and value['code'] == 409):
                        raise RefConflict()
                finally:
                    error.close()
                raise RefError('CONFLICT_RESPONSE_INVALID', http_status=409)
            error.close()
            # 5xx, redirects, lost replies, and malformed replies are not evidence
            # that a submitted commit did not take effect. No automatic retry.
            outcome = 'rejected' if status in {400, 401, 403, 404, 405, 419} else 'unknown'
            raise RefError('HTTP_REQUEST_FAILED', outcome=outcome, http_status=status) from None
        except (URLError, OSError, ValueError):
            raise RefError('HTTP_RESPONSE_UNCERTAIN') from None

    def request(self, method, path, body=None, headers=None):
        """Only the parent's fixed client-credentials token flow may use this API."""
        if (method != 'POST' or path != TOKEN_PATH or type(body) is not bytes or len(body) > 1024
                or parse_qs(body.decode('ascii')) != {
                    'grant_type': ['client_credentials'], 'scope': ['PRINCIPAL_ROLE:ALL']}
                or type(headers) is not dict or set(headers) != {'Authorization', 'Content-Type'}
                or not headers['Authorization'].startswith('Basic ')
                or headers['Content-Type'] != 'application/x-www-form-urlencoded'):
            raise RefError('AUTHENTICATION_SCOPE_INVALID', outcome='not-sent')
        _header(headers['Authorization'], 'AUTHENTICATION_HEADER_INVALID', 16384)
        return self.exchange(method, path, body, headers)


class RefClient:
    def __init__(self, token, progress, guard, *, opener=None):
        C.table_name(token)
        if not callable(progress) or not callable(guard):
            raise RefError('JOURNAL_AND_GUARD_REQUIRED', outcome='not-sent')
        self.token, self.progress, self.guard = token, progress, guard
        self._auth = _Authentication(guard, opener)
        self._location = None
        self._journal = {'schema_version': 1, 'run_token': token, 'table': C.table_name(token),
            'authenticated': False, 'table_uuid': None, 'outcome': 'ready', 'pending': None,
            'operations': [], 'observations': []}

    def receipt(self):
        return copy.deepcopy(self._journal)

    def _save(self):
        self.progress(self.receipt())

    def authenticate(self):
        if self._journal['authenticated']:
            raise RefError('AUTHENTICATION_ALREADY_PERFORMED', outcome='not-sent')
        self.guard()
        try:
            self._auth.authenticate()
            _header(self._auth.realm, 'REALM_INVALID', 256)
            _header(self._auth.token, 'TOKEN_INVALID', 16384)
        except Exception:
            raise RefError('AUTHENTICATION_FAILED', outcome='not-sent') from None
        self._journal['authenticated'] = True
        self._save()

    def load(self, expected_uuid=None):
        if not self._journal['authenticated']:
            raise RefError('AUTHENTICATION_REQUIRED', outcome='not-sent')
        if len(self._journal['observations']) >= 48:
            raise RefError('OBSERVATION_LIMIT_EXCEEDED', outcome='not-sent')
        known = self._journal['table_uuid']
        if expected_uuid is not None:
            C.canonical_uuid(expected_uuid)
            if known is not None and known != expected_uuid:
                raise RefError('TABLE_UUID_CHANGED', outcome='not-sent')
        response = self._auth.exchange('GET', C.table_path(self.token))
        try:
            value = C.project_metadata(response, self.token, known or expected_uuid)
            if self._location is not None and value['table_location_sha256'] != self._location:
                raise C.ContractError('TABLE_LOCATION_CHANGED')
        except C.ContractError as error:
            raise RefError(str(error), outcome='not-sent') from None
        self._journal['table_uuid'] = value['table_uuid']
        self._location = value['table_location_sha256']
        self._journal['observations'].append(copy.deepcopy(value))
        self._save()
        return value

    def set_ref(self, before, ref_name, target_snapshot, expected_ref=None):
        if self._journal['pending'] is not None or self._journal['outcome'] == 'unknown':
            raise RefError('UNRESOLVED_COMMIT_PREVENTS_MUTATION')
        if len(self._journal['operations']) >= 8:
            raise RefError('COMMIT_LIMIT_EXCEEDED', outcome='not-sent')
        body = C.commit_body(before, self.token, ref_name, target_snapshot, expected_ref)
        current = self.load(before['table_uuid'])
        if current['metadata_sha256'] != before['metadata_sha256']:
            raise RefError('STALE_METADATA_BEFORE_COMMIT', outcome='not-sent')
        operation = {'operation_id': str(uuid.uuid4()), 'ref_name': ref_name,
            'expected_snapshot_id': expected_ref, 'target_snapshot_id': target_snapshot,
            'table_uuid': before['table_uuid'], 'request_sha256': C.digest(body),
            'before_metadata_sha256': before['metadata_sha256'], 'outcome': 'pending', 'http_status': None}
        self._journal['operations'].append(operation)
        self._journal['pending'] = operation['operation_id']
        self._journal['outcome'] = 'unknown'
        try:
            self._save()  # A durable intent is required before the HTTP mutation.
        except BaseException:
            operation['outcome'] = 'not-sent'
            self._journal.update(pending=None, outcome='not-sent')
            raise
        try:
            response = self._auth.exchange('POST', C.table_path(self.token), body)
            operation['http_status'] = 200
            committed = C.project_metadata(response, self.token, before['table_uuid'])
            C.verify_transition(before, committed, self.token, ref_name, target_snapshot)
            operation['response_metadata_sha256'] = committed['metadata_sha256']
            observed = self.load(before['table_uuid'])
            C.verify_transition(before, observed, self.token, ref_name, target_snapshot)
            C.require(observed['metadata_sha256'] == committed['metadata_sha256'] and
                      observed['metadata_location_sha256'] == committed['metadata_location_sha256'],
                      'COMMIT_READBACK_DIFFERS')
            operation['readback_metadata_sha256'] = observed['metadata_sha256']
            operation['outcome'] = 'verified'
            self._journal.update(pending=None, outcome='verified')
            self._save()
            return observed
        except BaseException as error:
            rejected = isinstance(error, RefError) and error.outcome == 'rejected'
            outcome = 'rejected' if rejected else 'unknown'
            operation.update(outcome=outcome, http_status=getattr(error, 'http_status', None)
                             or operation['http_status'])
            self._journal.update(outcome=outcome, pending=None if rejected else operation['operation_id'])
            save_error = None
            try:
                self._save()
            except BaseException as caught:
                # Failed final persistence cannot certify rejection or success.
                save_error = caught
                operation['outcome'] = 'unknown'
                self._journal.update(outcome='unknown', pending=operation['operation_id'])
            if not isinstance(error, Exception):
                raise
            if save_error is not None and not isinstance(save_error, Exception):
                raise save_error
            if isinstance(error, RefConflict) and save_error is None:
                raise
            outcome = self._journal['outcome']
            raise RefError('REF_COMMIT_REJECTED' if outcome == 'rejected' else 'REF_COMMIT_UNCERTAIN',
                           outcome=outcome, http_status=operation['http_status']) from None
