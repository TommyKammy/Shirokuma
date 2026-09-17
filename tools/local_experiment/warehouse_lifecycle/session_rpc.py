"""Private, bounded transport for one short-lived warehouse session.

Only the injected owner performs transitions. Admission is bounded to ten minutes
and sixteen connections; an admitted handler is never cancelled by a disconnect
or that admission deadline. The owner must bound its own operations. Handlers run
in worker threads, one at a time; callers propagate their telemetry context.
"""
from copy import deepcopy
import ctypes
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import tempfile
import threading
import time
import uuid


SOCKET_ROOT = Path('/private/tmp')
REQUEST_KEYS = {'schema_version', 'session_id', 'request_id', 'warehouse', 'action'}
RESPONSE_KEYS = REQUEST_KEYS | {'result', 'before', 'after', 'operation_id', 'receipt_sha256'}
STATUS = {'verified': 200, 'noop': 200, 'busy': 409, 'unknown': 409, 'failed': 500}
TARGETS = {'chill': {'replicas': 0, 'backend_active': False},
           'wake': {'replicas': 1, 'backend_active': True}}
MAX_BODY = 4096
READ_TIMEOUT_SECONDS = 2


class RPCError(RuntimeError):
    """Only fixed, non-sensitive codes cross the transport boundary."""


def require(condition, code):
    if not condition:
        raise RPCError(code)


def canonical_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'INVALID_JSON')
        result[key] = value
    return result


def _constant(_value):
    raise RPCError('INVALID_JSON')


def parse_request(raw, session_id):
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_BODY, 'INVALID_REQUEST')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise RPCError('INVALID_JSON') from None
    require(type(value) is dict and set(value) == REQUEST_KEYS, 'INVALID_REQUEST')
    require(type(value['schema_version']) is int and value['schema_version'] == 1,
            'INVALID_REQUEST')
    require(value['session_id'] == session_id and canonical_uuid(value['request_id']),
            'INVALID_REQUEST')
    require(value['warehouse'] == 'XS' and value['action'] in ('chill', 'wake'),
            'INVALID_REQUEST')
    return value


def validate_response(value, request):
    require(type(value) is dict and set(value) == RESPONSE_KEYS, 'INVALID_RESPONSE')
    require(type(value['schema_version']) is int and value['schema_version'] == 1,
            'INVALID_RESPONSE')
    require(all(value[key] == request[key] for key in REQUEST_KEYS), 'INVALID_RESPONSE')
    require(isinstance(value['result'], str) and value['result'] in STATUS, 'INVALID_RESPONSE')
    for key in ('before', 'after'):
        state = value[key]
        require(type(state) is dict and set(state) == {'replicas', 'backend_active'},
                'INVALID_RESPONSE')
        require(state['replicas'] is None or
                (type(state['replicas']) is int and state['replicas'] in (0, 1)), 'INVALID_RESPONSE')
        require(state['backend_active'] is None or type(state['backend_active']) is bool,
                'INVALID_RESPONSE')
    operation, receipt = value['operation_id'], value['receipt_sha256']
    require((operation is None and receipt is None) or
            (canonical_uuid(operation) and isinstance(receipt, str) and
             re.fullmatch(r'[0-9a-f]{64}', receipt) is not None), 'INVALID_RESPONSE')
    if value['result'] in ('verified', 'noop'):
        require(operation is not None and value['after'] == TARGETS[value['action']],
                'INVALID_RESPONSE')
        prior = TARGETS['wake' if value['action'] == 'chill' else 'chill']
        require(value['before'] == (prior if value['result'] == 'verified' else value['after']),
                'INVALID_RESPONSE')
    return deepcopy(value)


def _uncertain(request, result='unknown'):
    return {**request, 'result': result,
            'before': {'replicas': None, 'backend_active': None},
            'after': {'replicas': None, 'backend_active': None},
            'operation_id': None, 'receipt_sha256': None}


def _identity(path, kind, mode=None):
    try:
        value = path.lstat()
    except OSError:
        raise RPCError('UNSAFE_PATH') from None
    require(kind(value.st_mode) and value.st_uid == os.getuid(), 'UNSAFE_PATH')
    if mode is not None:
        require(stat.S_IMODE(value.st_mode) == mode, 'UNSAFE_PATH')
    return value.st_dev, value.st_ino


def _ancestors(path):
    require(path.is_absolute(), 'UNSAFE_PATH')
    for part in reversed((path,) + tuple(path.parents)):
        require(stat.S_ISDIR(part.lstat().st_mode), 'UNSAFE_PATH')


def peer_uid(connection):
    """Darwin getpeereid and Linux SO_PEERCRED; no unauthenticated fallback."""
    if hasattr(connection, 'getpeereid'):
        return connection.getpeereid()[0]
    if hasattr(socket, 'SO_PEERCRED'):
        return struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]
    library = ctypes.CDLL(None, use_errno=True)
    try:
        function = library.getpeereid
    except AttributeError:
        raise RPCError('PEER_IDENTITY_UNAVAILABLE') from None
    function.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    function.restype = ctypes.c_int
    user, group = ctypes.c_uint(), ctypes.c_uint()
    require(function(connection.fileno(), ctypes.byref(user), ctypes.byref(group)) == 0,
            'PEER_IDENTITY_UNAVAILABLE')
    return user.value


class SessionRPC:
    def __init__(self, session_id, output, handler, *, ttl_seconds=600, max_requests=16,
                 replay_grace_seconds=15):
        require(isinstance(session_id, str) and re.fullmatch(r'[0-9a-f]{32}', session_id),
                'INVALID_SESSION')
        require(type(ttl_seconds) in (int, float) and 0 < ttl_seconds <= 600, 'INVALID_LIMIT')
        require(type(max_requests) is int and 1 <= max_requests <= 16, 'INVALID_LIMIT')
        require(type(replay_grace_seconds) in (int, float) and 0 < replay_grace_seconds <= 15,
                'INVALID_LIMIT')
        require(callable(handler), 'INVALID_HANDLER')
        self.session_id, self.output, self.handler = session_id, Path(output), handler
        self.ttl_seconds, self.max_requests = ttl_seconds, max_requests
        self.replay_grace_seconds = replay_grace_seconds
        self.socket_directory = SOCKET_ROOT / ('shirokuma-wl-' + session_id)
        self.socket_path = self.socket_directory / 'control.sock'
        self.journal_directory = self.output / 'rpc'
        self.journal_path = self.journal_directory / 'journal.json'
        self._listener = None
        self._directory_identity = self._socket_identity = self._journal_identity = None
        self._entries = {}
        self._replies = []
        self._lock = threading.Lock()
        self._active = False
        self._threads = []
        self._requests = self._completed = 0
        self._stop = threading.Event()
        self._stop_callback = None
        self._replay_until = None
        self._error_codes = []
        self._entered = False
        self._started = None

    def __enter__(self):
        require(not self._entered, 'ALREADY_STARTED')
        _ancestors(self.output)
        _identity(self.output, stat.S_ISDIR, 0o700)
        _ancestors(SOCKET_ROOT)
        try:
            self.journal_directory.mkdir(mode=0o700)
        except FileExistsError:
            raise RPCError('JOURNAL_COLLISION') from None
        self._journal_identity = _identity(self.journal_directory, stat.S_ISDIR, 0o700)
        self._persist()
        try:
            self.socket_directory.mkdir(mode=0o700)
        except FileExistsError:
            raise RPCError('SOCKET_COLLISION') from None
        self._directory_identity = _identity(self.socket_directory, stat.S_ISDIR, 0o700)
        try:
            self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._listener.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            self._socket_identity = _identity(self.socket_path, stat.S_ISSOCK, 0o600)
            self._listener.listen(16)
            self._listener.settimeout(0.1)
            self._started = time.monotonic()
            self._entered = True
            return self
        except BaseException:
            self.close()
            raise

    def _persist(self):
        require(_identity(self.journal_directory, stat.S_ISDIR, 0o700) == self._journal_identity,
                'JOURNAL_IDENTITY_CHANGED')
        if self.journal_path.exists() or self.journal_path.is_symlink():
            _identity(self.journal_path, stat.S_ISREG, 0o600)
            require(self.journal_path.lstat().st_nlink == 1, 'UNSAFE_JOURNAL')
        value = {'schema_version': 1, 'session_id': self.session_id,
                 'requests': deepcopy(list(self._entries.values())), 'replies': deepcopy(self._replies),
                 'error_codes': list(self._error_codes)}
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'
        descriptor, temporary = tempfile.mkstemp(prefix='.journal-', dir=str(self.journal_directory))
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.journal_path)
            descriptor = os.open(str(self.journal_directory), os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _read_request(self, connection):
        deadline, buffered = time.monotonic() + READ_TIMEOUT_SECONDS, bytearray()

        def receive(limit):
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'INVALID_HTTP')
            connection.settimeout(remaining)
            part = connection.recv(limit)
            require(bool(part), 'INVALID_HTTP')
            buffered.extend(part)

        def line(limit):
            while b'\n' not in buffered:
                require(len(buffered) < limit, 'INVALID_HTTP')
                receive(min(4096, limit - len(buffered)))
            end = buffered.index(b'\n') + 1
            require(end <= limit, 'INVALID_HTTP')
            value = bytes(buffered[:end])
            del buffered[:end]
            return value

        require(line(512) == b'POST /v1/transition HTTP/1.1\r\n', 'INVALID_HTTP')
        headers, size = {}, 0
        for _ in range(32):
            value = line(MAX_BODY + 1)
            size += len(value)
            require(size <= MAX_BODY and value.endswith(b'\r\n'), 'INVALID_HTTP')
            if value == b'\r\n':
                break
            require(not value[:1].isspace() and b':' in value, 'INVALID_HTTP')
            name, value = value[:-2].split(b':', 1)
            require(re.fullmatch(rb'[A-Za-z0-9-]+', name) is not None, 'INVALID_HTTP')
            name = name.lower()
            require(name not in headers and name not in (b'transfer-encoding', b'expect'), 'INVALID_HTTP')
            headers[name] = value.strip()
        else:
            raise RPCError('INVALID_HTTP')
        require(headers.get(b'content-type') == b'application/json', 'INVALID_HTTP')
        length = headers.get(b'content-length', b'')
        require(re.fullmatch(rb'[0-9]{1,4}', length) is not None, 'INVALID_HTTP')
        length = int(length)
        require(0 < length <= MAX_BODY, 'INVALID_HTTP')
        while len(buffered) < length:
            receive(length - len(buffered))
        raw = bytes(buffered[:length])
        return parse_request(raw, self.session_id)

    def _dispatch(self, request):
        request_id = request['request_id']
        with self._lock:
            prior = self._entries.get(request_id)
            if prior is not None:
                if prior['request'] != request:
                    return _uncertain(request)
                return deepcopy(prior['response']) if prior['response'] is not None else _uncertain(request)
            entry = {'request': request, 'state': 'pending', 'response': None, 'error_code': None}
            self._entries[request_id] = entry
            if self._replay_until is not None or (self._stop_callback is not None and self._stop_callback()):
                entry.update(state='rejected', response=_uncertain(request))
                try:
                    self._persist()
                except Exception:
                    self._fail('JOURNAL_WRITE_FAILED')
                return deepcopy(entry['response'])
            try:
                self._persist()  # A failed intent write must never invoke the handler.
            except Exception:
                entry.update(state='unknown', response=_uncertain(request, 'failed'), error_code='JOURNAL_WRITE_FAILED')
                self._fail('JOURNAL_WRITE_FAILED')
                return deepcopy(entry['response'])
            if self._active:
                entry.update(state='completed', response=_uncertain(request, 'busy'))
                try:
                    self._persist()
                except Exception:
                    entry.update(state='unknown', response=_uncertain(request), error_code='JOURNAL_WRITE_FAILED')
                    self._fail('JOURNAL_WRITE_FAILED')
                else:
                    self._completed += 1
                return deepcopy(entry['response'])
            self._active = True
        try:
            response = validate_response(self.handler(request['action'], request_id), request)
            error = None
        except BaseException:
            response, error = _uncertain(request), 'HANDLER_FAILED_OR_INVALID_RESPONSE'
        with self._lock:
            if error is not None or response['result'] in ('failed', 'unknown'):
                self._error_codes.append(error or 'HANDLER_REPORTED_FAILURE')
            entry.update(state='completed', response=response, error_code=error)
            try:
                self._persist()  # Never send success until its exact response is durable.
            except Exception:
                entry.update(state='unknown', response=_uncertain(request), error_code='JOURNAL_WRITE_FAILED')
                self._fail('JOURNAL_WRITE_FAILED')
            else:
                self._completed += 1
            finally:
                self._active = False
            return deepcopy(entry['response'])

    def _fail(self, code):
        if code not in self._error_codes:
            self._error_codes.append(code)
        self._stop.set()

    @staticmethod
    def _send(connection, status, value):
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        require(len(raw) <= 16384, 'RESPONSE_TOO_LARGE')
        reason = {200: 'OK', 400: 'Bad Request', 403: 'Forbidden', 409: 'Conflict', 500: 'Internal Server Error'}[status]
        header = ('HTTP/1.1 %d %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\n'
                  'Connection: close\r\n\r\n' % (status, reason, len(raw))).encode('ascii')
        connection.sendall(header + raw)

    def _connection(self, connection):
        with connection:
            try:
                require(peer_uid(connection) == os.getuid(), 'PEER_IDENTITY_MISMATCH')
                request = self._read_request(connection)
            except Exception:
                try:
                    self._send(connection, 400, {'error': 'invalid_request'})
                except OSError:
                    pass
                return
            response = self._dispatch(request)
            with self._lock:
                # Include interim/replay/conflicting-ID replies without rewriting
                # the original operation's intent or completed response.
                self._replies.append(deepcopy(response))
                try:
                    self._persist()
                except Exception:
                    response = _uncertain(request)
                    self._fail('JOURNAL_WRITE_FAILED')
            try:
                self._send(connection, STATUS[response['result']], response)
            except OSError:
                pass  # The durable response remains replayable after a lost reply.

    def serve(self, stop=None):
        require(self._entered and self._listener is not None, 'NOT_STARTED')
        require(stop is None or callable(stop), 'INVALID_STOP_CALLBACK')
        self._stop_callback = stop
        reason = 'stopped'
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if self._replay_until is None and stop is not None and stop():
                    self._replay_until = now + self.replay_grace_seconds
                if self._replay_until is not None and now >= self._replay_until:
                    break
                if self._replay_until is None and now - self._started >= self.ttl_seconds:
                    reason = 'ttl'
                    break
                if self._requests >= self.max_requests:
                    reason = 'request_limit'
                    break
                try:
                    connection, _ = self._listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                self._requests += 1
                worker = threading.Thread(target=self._connection, args=(connection,), daemon=False)
                self._threads.append(worker)
                worker.start()
        finally:
            self._stop.set()
            if self._listener is not None:
                self._listener.close()
            self._listener = None
            self._drain()
            with self._lock:
                try:
                    self._persist()
                except Exception:
                    self._fail('JOURNAL_WRITE_FAILED')
        pending = any(entry['state'] == 'pending' for entry in self._entries.values())
        return {'reason': reason, 'requests': self._requests, 'completed': self._completed,
                'result': 'failed' if self._error_codes or pending else 'verified',
                'error_codes': list(self._error_codes)}

    def _drain(self):
        for worker in self._threads:
            if worker is not threading.current_thread():
                worker.join()

    def close(self):
        self._stop.set()
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        self._drain()
        if self._directory_identity is not None:
            require(_identity(self.socket_directory, stat.S_ISDIR, 0o700) == self._directory_identity,
                    'SOCKET_DIRECTORY_CHANGED')
            if self._socket_identity is not None:
                require(_identity(self.socket_path, stat.S_ISSOCK, 0o600) == self._socket_identity,
                        'SOCKET_IDENTITY_CHANGED')
                self.socket_path.unlink()
                self._socket_identity = None
            try:
                self.socket_directory.rmdir()
            except OSError:
                raise RPCError('SOCKET_DIRECTORY_NOT_EMPTY') from None
            self._directory_identity = None

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False
