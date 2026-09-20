"""Single-user loopback safe-JSON inference, using an explicit local Registry version.

This is not MLflow pyfunc Serving. The owned process lifecycle and private token
file belong to serving_runtime; this module never saves requests or predictions.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import errno
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import re
import signal
import socket
import time

from tools.local_experiment.mlflow import predict as P
from tools.local_experiment.mlflow import registry as R
from tools.local_experiment.mlflow import registry_contract as K
from tools.local_experiment.notebook import registry as N

HOST, PORT = '127.0.0.1', 18570
READ_TIMEOUT, REQUEST_TIMEOUT = 5, 15
MAX_INPUT_BYTES, MAX_PUBLIC_BYTES = P.MAX_INPUT_BYTES, P.MAX_PUBLIC_BYTES
PROFILE = 'local-ridge-safe-json-serving-v1'
SCOPE = ('Single-user local safe-JSON inference prototype; fixed Registry version; '
         'not MLflow pyfunc Serving, multi-user service, or model quality promotion.')
ERROR_CODES = frozenset('''SERVING_ARGUMENTS_INVALID SERVING_BINDING_INVALID SERVING_BINDING_CHANGED
SERVING_SOURCE_UNAVAILABLE SERVING_REQUEST_INVALID SERVING_AUTH_REQUIRED SERVING_HOST_INVALID
SERVING_ORIGIN_REJECTED SERVING_BODY_LIMIT SERVING_METHOD_INVALID SERVING_NOT_FOUND
SERVING_REQUEST_TIMEOUT SERVING_PORT_BUSY SERVING_START_FAILED SERVING_INTERNAL_ERROR'''.split())


class ServingError(ValueError):
    pass


class RequestDeadline(BaseException):
    """Interrupt even a library that converts ordinary exceptions to fixed errors."""


def require(value, code):
    if not value:
        raise ServingError(code)


def validate_binding(value):
    """Validate only the private saved shape, without Registry or filesystem reads."""
    try:
        require(type(value) is dict and set(value) == {
            'schema_version', 'profile', 'model_name', 'version', 'binding'}
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['profile'] == PROFILE and value['model_name'] == K.MODEL_NAME
            and type(value['version']) is str and K.version(value['version']) == value['version'],
            'SERVING_BINDING_INVALID')
        K.validate_binding(value['binding'])
    except Exception:
        raise ServingError('SERVING_BINDING_INVALID') from None
    return deepcopy(value)


def load_binding(version):
    try:
        require(type(version) is str and K.version(version) == version, 'SERVING_ARGUMENTS_INVALID')
    except Exception:
        raise ServingError('SERVING_ARGUMENTS_INVALID') from None
    try:
        resolved = R.resolve(K.MODEL_NAME, version)
        require(type(resolved) is dict and type(resolved.get('schema_version')) is int
                and resolved['schema_version'] == 1 and resolved.get('profile') == K.PROFILE
                and resolved.get('result') == 'verified' and resolved.get('observation') == 'sqlite-read-only'
                and resolved.get('model_name') == K.MODEL_NAME and resolved.get('version') == version,
                'SERVING_SOURCE_UNAVAILABLE')
        return validate_binding(dict(schema_version=1, profile=PROFILE, model_name=K.MODEL_NAME,
                                     version=version, binding=resolved['binding']))
    except Exception:
        raise ServingError('SERVING_SOURCE_UNAVAILABLE') from None


def binding_sha256(binding):
    return P.digest(validate_binding(binding))


def expected_health(binding):
    binding = validate_binding(binding)
    return dict(schema_version=1, profile=PROFILE, result='ready', model_name=binding['model_name'],
                version=binding['version'], binding_sha256=binding_sha256(binding),
                model_sha256=binding['binding']['model_sha256'], scope=SCOPE)


def _unchanged(binding):
    require(load_binding(binding['version']) == binding, 'SERVING_BINDING_CHANGED')


def health(binding):
    binding = validate_binding(binding)
    _unchanged(binding)
    value = expected_health(binding)
    _unchanged(binding)
    return value


def predict(binding, request):
    binding = validate_binding(binding)
    try:
        request = P.normalize_request(request)
    except Exception:
        raise ServingError('SERVING_REQUEST_INVALID') from None
    _unchanged(binding)
    try:
        prediction = P.predict(binding['binding']['training_token'], deepcopy(request))
        checked = N.validate_public(dict(schema_version=1, profile=N.PROFILE, result='passed',
            registry=dict(name=binding['model_name'], version=binding['version'], binding=binding['binding']),
            request=request, prediction=prediction))
    except Exception:
        raise ServingError('SERVING_SOURCE_UNAVAILABLE') from None
    _unchanged(binding)
    value = expected_health(binding)
    value.update(result='passed', training_token=binding['binding']['training_token'],
                 workload_sha256=binding['binding']['workload_sha256'], request_sha256=P.digest(request),
                 predictions=checked['prediction']['predictions'])
    require(len(P.canonical(value)) + 1 <= MAX_PUBLIC_BYTES, 'SERVING_INTERNAL_ERROR')
    return value


def failure(code):
    return dict(schema_version=1, profile=PROFILE, result='failed',
                error_code=code if code in ERROR_CODES else 'SERVING_INTERNAL_ERROR', scope=SCOPE)


@contextmanager
def _deadline():
    # Serving runs on the main thread in its dedicated process. An existing
    # alarm is not silently repurposed, and runtime's TERM handler is untouched.
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), 'SERVING_INTERNAL_ERROR')
    previous = signal.getsignal(signal.SIGALRM)
    def expired(signum, frame):
        raise RequestDeadline()
    try:
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, REQUEST_TIMEOUT)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'ShirokumaSafeJSON'
    sys_version = ''

    def log_message(self, format, *args):
        # No access log, input, Authorization header, or exception text is saved.
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        finally:
            self.close_connection = True

    def handle_expect_100(self):
        self.send_error(400)
        return False

    def _reply(self, status, value):
        remaining = self.server.deadline_at - time.monotonic()
        if remaining <= 0:
            raise RequestDeadline()
        self.connection.settimeout(min(READ_TIMEOUT, remaining))
        body = P.canonical(value) + b'\n'
        require(len(body) <= MAX_PUBLIC_BYTES, 'SERVING_INTERNAL_ERROR')
        self.close_connection = True
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):
        status = 405 if code == 501 else code if code in (400, 408, 413, 414, 431) else 400
        self._reply(status, failure('SERVING_METHOD_INVALID' if status == 405 else 'SERVING_REQUEST_INVALID'))

    def _headers(self):
        headers = self.headers
        require(headers.get_all('Host', []) == [f'{HOST}:{PORT}'], 'SERVING_HOST_INVALID')
        require(not headers.get_all('Origin', []), 'SERVING_ORIGIN_REJECTED')
        auth = headers.get_all('Authorization', [])
        require(len(auth) == 1 and auth[0].isascii()
                and hmac.compare_digest(auth[0], 'Bearer ' + self.server.token), 'SERVING_AUTH_REQUIRED')
        require(not headers.get_all('Transfer-Encoding', []) and not headers.get_all('Expect', [])
                and len(headers.get_all('Content-Length', [])) <= 1, 'SERVING_REQUEST_INVALID')
        length = headers.get('Content-Length')
        if length is None:
            require(self.command == 'GET', 'SERVING_REQUEST_INVALID')
            return 0
        require(re.fullmatch(r'0|[1-9][0-9]*', length) is not None, 'SERVING_REQUEST_INVALID')
        require(len(length) <= 5 and int(length) <= MAX_INPUT_BYTES, 'SERVING_BODY_LIMIT')
        return int(length)

    def _dispatch(self):
        try:
            length = self._headers()
            if self.path == '/health':
                require(self.command == 'GET', 'SERVING_METHOD_INVALID')
                require(length == 0, 'SERVING_REQUEST_INVALID')
                value = health(self.server.binding)
            elif self.path == '/predict':
                require(self.command == 'POST', 'SERVING_METHOD_INVALID')
                require(self.headers.get_all('Content-Type', []) == ['application/json'], 'SERVING_REQUEST_INVALID')
                require(length > 0, 'SERVING_REQUEST_INVALID')
                raw = self.rfile.read(length)
                require(len(raw) == length, 'SERVING_REQUEST_INVALID')
                try:
                    request = P.parse_request(raw)
                except Exception:
                    raise ServingError('SERVING_REQUEST_INVALID') from None
                value = predict(self.server.binding, request)
            else:
                raise ServingError('SERVING_NOT_FOUND')
            self._reply(200, value)
        except ServingError as error:
            code = str(error)
            status = {'SERVING_AUTH_REQUIRED': 401, 'SERVING_BODY_LIMIT': 413,
                      'SERVING_NOT_FOUND': 404, 'SERVING_METHOD_INVALID': 405,
                      'SERVING_SOURCE_UNAVAILABLE': 503, 'SERVING_BINDING_CHANGED': 503}.get(code, 400)
            self._reply(status, failure(code))
        except (TimeoutError, socket.timeout):
            self._reply(408, failure('SERVING_REQUEST_TIMEOUT'))
        except Exception:
            self._reply(500, failure('SERVING_INTERNAL_ERROR'))

    do_GET = _dispatch
    do_POST = _dispatch


class Server(HTTPServer):
    """One process, one active request, one small socket backlog; no worker threads."""
    allow_reuse_address = True  # Permit restart after our closed connections enter TIME_WAIT.
    allow_reuse_port = False
    request_queue_size = 1

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(READ_TIMEOUT)
        return connection, address

    def finish_request(self, request, client_address):
        self.deadline_at = time.monotonic() + REQUEST_TIMEOUT
        try:
            with _deadline():
                super().finish_request(request, client_address)
        except RequestDeadline:
            # A deadline during headers, validation or a response closes the
            # connection; never append a success or a second HTTP response.
            pass

    def handle_error(self, request, client_address):
        # BaseServer otherwise prints arbitrary request-handler exceptions.
        pass


def serve(binding, token, *, on_ready=None, should_stop=None):
    """Block until the runtime's stop flag is set; never install TERM/INT handlers."""
    binding = validate_binding(binding)
    require(type(token) is str and re.fullmatch('[0-9a-f]{64}', token), 'SERVING_ARGUMENTS_INVALID')
    _unchanged(binding)
    try:
        server = Server((HOST, PORT), Handler)
    except OSError as error:
        raise ServingError('SERVING_PORT_BUSY' if error.errno == errno.EADDRINUSE else 'SERVING_START_FAILED') from None
    first = None
    try:
        server.binding, server.token = binding, token
        server.timeout = .5
        if on_ready is not None:
            on_ready()
        while should_stop is None or not should_stop():
            server.handle_request()
    except BaseException as error:
        first = error
    finally:
        try:
            server.server_close()
        except BaseException as error:
            if first is None or (isinstance(first, Exception) and not isinstance(error, Exception)):
                first = error
    if first is not None:
        raise first
