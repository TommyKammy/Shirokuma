"""Private, bounded transport for one short-lived warehouse session.

Only the injected owner performs transitions. Admission is bounded to ten minutes
and sixteen connections; an admitted handler is never cancelled by a disconnect
or that admission deadline. The owner must bound its own operations. Handlers run
in worker threads, one at a time; callers propagate their telemetry context.
"""
from copy import deepcopy
from contextlib import ExitStack, closing
import ctypes
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid


SOCKET_ROOT = Path('/private/tmp')
REQUEST_KEYS = {'schema_version', 'session_id', 'request_id', 'warehouse', 'action'}
RESPONSE_KEYS = REQUEST_KEYS | {'result', 'before', 'after', 'operation_id', 'receipt_sha256'}
QUERY_KEYS = {'case', 'query_id', 'rows_observed', 'rows_sha256', 'post_count',
              'response_complete', 'matched'}
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


def parse_request(raw, session_id, *, allow_query=False):
    require(type(allow_query) is bool, 'INVALID_REQUEST')
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
    actions = ('chill', 'wake', 'query') if allow_query else ('chill', 'wake')
    require(value['warehouse'] == 'XS' and value['action'] in actions,
            'INVALID_REQUEST')
    return value


def validate_response(value, request):
    is_query = request['action'] == 'query'
    require(type(value) is dict and set(value) == RESPONSE_KEYS | ({'query'} if is_query else set()),
            'INVALID_RESPONSE')
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
    if is_query:
        require(value['result'] != 'noop', 'INVALID_RESPONSE')
        query = value['query']
        if value['result'] == 'verified':
            require(operation is not None and value['before'] == TARGETS['chill']
                    and value['after'] == TARGETS['wake'], 'INVALID_RESPONSE')
            require(type(query) is dict and set(query) == QUERY_KEYS
                    and query['case'] == 'xs_caller'
                    and type(query['query_id']) is str
                    and re.fullmatch(r'[0-9]{8}_[0-9]{6}_[0-9]{5}_[a-z0-9]{5}', query['query_id']) is not None
                    and type(query['rows_observed']) is int and query['rows_observed'] == 25
                    and type(query['rows_sha256']) is str
                    and re.fullmatch(r'[0-9a-f]{64}', query['rows_sha256']) is not None
                    and type(query['post_count']) is int and query['post_count'] == 1
                    and query['response_complete'] is True and query['matched'] is True,
                    'INVALID_RESPONSE')
        else:
            require(query is None, 'INVALID_RESPONSE')
    elif value['result'] in ('verified', 'noop'):
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
            'operation_id': None, 'receipt_sha256': None,
            **({'query': None} if request['action'] == 'query' else {})}


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


def bind_in_directory(directory):
    """Use a child cwd anchored to directory; never resolve the parent path to bind."""
    with ExitStack() as stack:
        receiver, sender = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        stack.enter_context(closing(receiver))
        stack.enter_context(closing(sender))
        receiver.settimeout(2)
        try:
            result = subprocess.run(
                [sys.executable, '-I', str(Path(__file__).with_name('socket_bind.py')),
                 str(directory), str(sender.fileno())],
                pass_fds=(directory, sender.fileno()), stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=5, check=False)
            require(result.returncode == 0, 'SOCKET_BIND_FAILED')
            data, controls, flags, _ = receiver.recvmsg(1, socket.CMSG_SPACE(struct.calcsize('i')))
            descriptors = [fd for level, kind, raw in controls
                           if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS
                           for fd in struct.unpack(f'{len(raw) // struct.calcsize("i")}i', raw)]
            try:
                require(data == b'B' and not flags & (socket.MSG_CTRUNC | socket.MSG_TRUNC)
                        and len(descriptors) == 1,
                        'SOCKET_BIND_FAILED')
                descriptor = descriptors.pop()
                try:
                    return socket.socket(fileno=descriptor)
                except BaseException:
                    os.close(descriptor)
                    raise
            finally:
                for fd in descriptors:
                    os.close(fd)
        except (OSError, subprocess.SubprocessError):
            raise RPCError('SOCKET_BIND_FAILED') from None


class SessionRPC:
    def __init__(self, session_id, output, handler, *, ttl_seconds=600, max_requests=16,
                 replay_grace_seconds=15, allow_query=False):
        require(isinstance(session_id, str) and re.fullmatch(r'[0-9a-f]{32}', session_id),
                'INVALID_SESSION')
        require(type(ttl_seconds) in (int, float) and 0 < ttl_seconds <= 600, 'INVALID_LIMIT')
        require(type(max_requests) is int and 1 <= max_requests <= 16, 'INVALID_LIMIT')
        require(type(replay_grace_seconds) in (int, float) and 0 < replay_grace_seconds <= 15,
                'INVALID_LIMIT')
        require(callable(handler), 'INVALID_HANDLER')
        require(type(allow_query) is bool, 'INVALID_QUERY_MODE')
        self.allow_query = allow_query
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
            try:
                directory = os.open(self.socket_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            except OSError:
                raise RPCError('SOCKET_DIRECTORY_CHANGED') from None
            try:
                state = os.fstat(directory)
                require((state.st_dev, state.st_ino) == self._directory_identity and
                        state.st_uid == os.getuid() and stat.S_IMODE(state.st_mode) == 0o700,
                        'SOCKET_DIRECTORY_CHANGED')
                self._listener = bind_in_directory(directory)
                current = os.stat('control.sock', dir_fd=directory, follow_symlinks=False)
                require(stat.S_ISSOCK(current.st_mode) and current.st_uid == os.getuid() and
                        stat.S_IMODE(current.st_mode) == 0o600,
                        'SOCKET_IDENTITY_CHANGED')
                updated = os.stat('control.sock', dir_fd=directory, follow_symlinks=False)
                require(stat.S_ISSOCK(updated.st_mode) and updated.st_uid == os.getuid() and
                        stat.S_IMODE(updated.st_mode) == 0o600 and
                        (updated.st_dev, updated.st_ino) == (current.st_dev, current.st_ino),
                        'SOCKET_IDENTITY_CHANGED')
                require(_identity(self.socket_directory, stat.S_ISDIR, 0o700) == self._directory_identity,
                        'SOCKET_DIRECTORY_CHANGED')
                self._socket_identity = updated.st_dev, updated.st_ino
            finally:
                os.close(directory)
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
        try:
            directory = os.open(self.journal_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            raise RPCError('JOURNAL_IDENTITY_CHANGED') from None
        try:
            state = os.fstat(directory)
            require((state.st_dev, state.st_ino) == self._journal_identity and
                    state.st_uid == os.getuid() and stat.S_IMODE(state.st_mode) == 0o700,
                    'JOURNAL_IDENTITY_CHANGED')
            try:
                current = os.stat('journal.json', dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                require(stat.S_ISREG(current.st_mode) and current.st_uid == os.getuid() and
                        stat.S_IMODE(current.st_mode) == 0o600 and current.st_nlink == 1,
                        'UNSAFE_JOURNAL')
            value = {'schema_version': 1, 'session_id': self.session_id,
                     'requests': deepcopy(list(self._entries.values())), 'replies': deepcopy(self._replies),
                     'error_codes': list(self._error_codes)}
            raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'
            temporary = '.journal-' + uuid.uuid4().hex
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            created = os.fstat(descriptor)
            replaced = False
            try:
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, 'journal.json', src_dir_fd=directory, dst_dir_fd=directory)
                replaced = True
                os.fsync(directory)
            finally:
                if not replaced:
                    try:
                        current = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                            os.unlink(temporary, dir_fd=directory)
            require(_identity(self.journal_directory, stat.S_ISDIR, 0o700) == self._journal_identity,
                    'JOURNAL_IDENTITY_CHANGED')
        finally:
            os.close(directory)

    def _read_request(self, connection):
        deadline, buffered = time.monotonic() + READ_TIMEOUT_SECONDS, bytearray()

        def receive(limit):
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'INVALID_HTTP')
            connection.settimeout(remaining)
            part = connection.recv(limit)
            require(time.monotonic() < deadline, 'INVALID_HTTP')
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
            # The owner speaks a single fixed HTTP/1.1 request with no
            # negotiation, upgrade or trailing fields. Credential headers have
            # no meaning here: only the private socket/peer authenticates.
            # Refuse the whole forwarding namespace, not just the currently
            # recognized proxy fields: an unrecognized variant must not carry
            # a second authority or target alongside this fixed Unix request.
            require(name not in (b'x-forwarded', b'x-real', b'x-original', b'x-rewrite', b'x-proxy', b'x-envoy', b'x-remote') and
                    not name.startswith((b'forwarded-', b'x-forwarded-')) and
                    not name.startswith(b'x-real-') and
                    not name.startswith((b'x-original-', b'x-rewrite-')) and
                    not name.startswith((b'proxy-', b'x-proxy-', b'x-envoy-', b'x-remote-', b'remote-',
                                         b'x-client-ip-', b'client-ip-')) and
                    name not in headers and name not in
                    (b'transfer-encoding', b'expect', b'upgrade', b'trailer',
                     b'te', b'proxy-connection', b'keep-alive', b'proxy', b'via',
                     b'authorization', b'proxy-authorization', b'cookie',
                      b'client-ip', b'x-client-ip', b'true-client-ip', b'cf-connecting-ip',
                      b'fastly-client-ip', b'x-cluster-client-ip',
                     b'forwarded', b'x-real-ip', b'x-real-host', b'x-forwarded-host', b'x-forwarded-proto',
                     b'x-forwarded-for', b'x-forwarded-port', b'x-forwarded-prefix',
                     b'x-original-url', b'x-original-uri', b'x-original-host',
                     b'x-rewrite-url', b'x-http-method-override', b'x-method-override',
                     b'x-http-method', b'x-original-method'), 'INVALID_HTTP')
            # Do not normalize ambiguous framing: a peer and a proxy may
            # interpret whitespace around a length differently.
            require(value.startswith(b' ') and value[1:] and
                    value[1:] == value[1:].strip(b' \t') and
                    all(32 <= byte <= 126 for byte in value[1:]), 'INVALID_HTTP')
            # Only an explicit close matches the one-request Unix connection.
            # Connection: Upgrade (or a nominated extension header) must not
            # turn the fixed transition into negotiated protocol traffic.
            require(name != b'connection' or value[1:].lower() == b'close', 'INVALID_HTTP')
            headers[name] = value[1:]
        else:
            raise RPCError('INVALID_HTTP')
        require(headers.get(b'content-type') == b'application/json', 'INVALID_HTTP')
        # The fixed Go Unix client names this origin. A different explicit
        # HTTP authority cannot describe the same scoped transition.
        require(b'host' not in headers or headers[b'host'] == b'warehouse.local', 'INVALID_HTTP')
        length = headers.get(b'content-length', b'')
        # This private client emits the canonical decimal byte count. Refuse
        # alternate spellings rather than admitting a second framing syntax.
        require(re.fullmatch(rb'[1-9][0-9]{0,3}', length) is not None, 'INVALID_HTTP')
        length = int(length)
        require(0 < length <= MAX_BODY, 'INVALID_HTTP')
        while len(buffered) < length:
            receive(length - len(buffered))
        # One connection carries exactly one request. Bytes already received
        # beyond its declared body cannot be silently interpreted as another
        # request (or hidden behind a valid first transition).
        require(len(buffered) == length, 'INVALID_HTTP')
        raw = bytes(buffered[:length])
        request = parse_request(raw, self.session_id, allow_query=self.allow_query)
        # Parsing the complete JSON is part of receiving a bounded request.
        # Do not dispatch a body whose validation finishes after the deadline.
        require(time.monotonic() < deadline, 'INVALID_HTTP')
        return request

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
            stop_requested = False
            if (not self._stop.is_set() and self._replay_until is None and
                    time.monotonic() - self._started < self.ttl_seconds and
                    self._stop_callback is not None):
                try:
                    stop_requested = bool(self._stop_callback())
                except BaseException:
                    # A broken owner stop check cannot leave an unjournaled
                    # request or authorize the handler to run.
                    stop_requested = True
                    self._fail('STOP_CALLBACK_FAILED')
                if stop_requested and not self._stop.is_set():
                    # A one-shot stop signal seen by a worker must not be
                    # forgotten if the serve thread sees it as false later.
                    self._replay_until = time.monotonic() + self.replay_grace_seconds
            # accept() may return just before the TTL while its worker reaches
            # dispatch afterward. The serve-loop deadline alone cannot bound
            # admission of that already accepted connection.
            if (self._stop.is_set() or self._replay_until is not None or
                    time.monotonic() - self._started >= self.ttl_seconds or
                    stop_requested):
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
                if self._replay_until is None and stop is not None:
                    try:
                        stop_requested = bool(stop())
                    except BaseException:
                        self._fail('STOP_CALLBACK_FAILED')
                        reason = 'stop_callback_failed'
                        break
                    # The callback may stop the owner while returning false;
                    # do not enter another accept after that signal.
                    if self._stop.is_set():
                        break
                    if stop_requested:
                        self._replay_until = time.monotonic() + self.replay_grace_seconds
                # The callback can consume the remaining TTL (or grace); do
                # not use a clock sampled before it to authorize accept().
                now = time.monotonic()
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
            try:
                directory = os.open(self.socket_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            except OSError:
                raise RPCError('SOCKET_DIRECTORY_CHANGED') from None
            try:
                state = os.fstat(directory)
                require((state.st_dev, state.st_ino) == self._directory_identity and
                        state.st_uid == os.getuid() and stat.S_IMODE(state.st_mode) == 0o700,
                        'SOCKET_DIRECTORY_CHANGED')
                if self._socket_identity is not None:
                    try:
                        current = os.stat('control.sock', dir_fd=directory, follow_symlinks=False)
                    except OSError:
                        raise RPCError('SOCKET_IDENTITY_CHANGED') from None
                    require(stat.S_ISSOCK(current.st_mode) and stat.S_IMODE(current.st_mode) == 0o600
                            and current.st_uid == os.getuid() and
                            (current.st_dev, current.st_ino) == self._socket_identity,
                            'SOCKET_IDENTITY_CHANGED')
                    os.unlink('control.sock', dir_fd=directory)
                    self._socket_identity = None
                with os.scandir(directory) as entries:
                    require(next(entries, None) is None, 'SOCKET_DIRECTORY_NOT_EMPTY')
                # rmdir cannot atomically require the verified inode: a same-UID
                # replacement of this shared-parent path could delete another
                # empty directory. Retain our private directory instead.
                require(_identity(self.socket_directory, stat.S_ISDIR, 0o700) == self._directory_identity,
                        'SOCKET_DIRECTORY_CHANGED')
            finally:
                os.close(directory)

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False
