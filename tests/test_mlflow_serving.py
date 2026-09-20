"""Serving boundaries with in-memory HTTP sockets; no bind, SDK or live reads."""
from copy import deepcopy
import contextlib
import errno
import io
import json
import signal
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.mlflow import serving as S
from tests.test_mlflow_predict import saved_review

TOKEN = '9' * 64


def fixture():
    saved, _ = saved_review()
    identity = saved['identity']
    bound = dict(training_token=identity['attempt_token'], run_id=identity['run_id'],
                 experiment_id=identity['experiment_id'], store_id=identity['store_id'],
                 workload_sha256=identity['workload_sha256'], model_sha256=S.P.digest(saved['model']),
                 artifact_relpath='iceberg-training/' + identity['run_id'] + '/artifacts/safe-projection.json')
    resolved = dict(schema_version=1, profile=S.K.PROFILE, result='verified', observation='sqlite-read-only',
                    model_name=S.K.MODEL_NAME, version='1', binding=bound)
    binding = dict(schema_version=1, profile=S.PROFILE, model_name=S.K.MODEL_NAME, version='1', binding=bound)
    return saved, resolved, binding


def request(rows=None):
    return dict(schema_version=1, rows=rows if rows is not None else [
        dict(x1=0, x2=0, x3=0), dict(x1=1, x2=2, x3=3)])


class Connection:
    def __init__(self, raw):
        self.input = io.BytesIO(raw)
        self.output = bytearray()
        self.timeouts = []

    def makefile(self, mode, buffering=None):
        return self.input

    def sendall(self, value):
        self.output.extend(value)

    def settimeout(self, value):
        self.timeouts.append(value)


class ServingTests(unittest.TestCase):
    def setUp(self):
        self.saved, self.resolved, self.binding = fixture()
        for mocked in (patch.object(S.R, 'resolve', return_value=self.resolved),
                       patch.object(S.P.T, 'load_saved_review', return_value=self.saved)):
            mocked.start(); self.addCleanup(mocked.stop)

    def http(self, path='/predict', *, method='POST', body=None, headers=None, extra=b''):
        raw = S.P.canonical(request()) if body is None and method == 'POST' else body or b''
        fields = headers if headers is not None else [
            ('Host', f'{S.HOST}:{S.PORT}'), ('Authorization', 'Bearer ' + TOKEN),
            ('Content-Type', 'application/json'), ('Content-Length', str(len(raw)))]
        packet = f'{method} {path} HTTP/1.1\r\n'.encode() + b''.join(
            (key + ': ' + value + '\r\n').encode('latin1') for key, value in fields) + b'\r\n' + raw + extra
        connection = Connection(packet)
        server = SimpleNamespace(binding=deepcopy(self.binding), token=TOKEN,
                                 deadline_at=time.monotonic() + S.REQUEST_TIMEOUT)
        logs = io.StringIO()
        with contextlib.redirect_stderr(logs), contextlib.redirect_stdout(logs):
            S.Handler(connection, (S.HOST, 12345), server)
        self.assertEqual(logs.getvalue(), '')
        head, payload = bytes(connection.output).split(b'\r\n\r\n', 1)
        self.assertIn(b'Connection: close', head)
        self.assertIn(b'Cache-Control: no-store', head)
        self.assertNotIn(b'Access-Control-Allow-Origin', head)
        self.assertNotIn(TOKEN.encode(), connection.output)
        return int(head.split()[1]), json.loads(payload), bytes(connection.output)

    def test_load_explicit_binding_and_health_are_exact_and_pure_expected_health(self):
        self.assertEqual(S.load_binding('1'), self.binding)
        S.R.resolve.assert_called_once_with(S.K.MODEL_NAME, '1')
        S.R.resolve.reset_mock()
        expected = S.expected_health(self.binding)
        S.R.resolve.assert_not_called()
        self.assertEqual(set(expected), {'schema_version', 'profile', 'result', 'model_name', 'version',
                                       'binding_sha256', 'model_sha256', 'scope'})
        self.assertEqual(S.health(self.binding), expected)
        self.assertEqual(S.R.resolve.call_count, 2)
        S.P.T.load_saved_review.assert_not_called()

    def test_prediction_uses_real_batch_arithmetic_and_original_order_through_64_rows(self):
        for rows in (request()['rows'], [dict(x1=i, x2=-i, x3=.5) for i in range(64)]):
            value = request(rows)
            expected = S.P.predict(self.binding['binding']['training_token'], value)
            S.R.resolve.reset_mock()
            result = S.predict(self.binding, value)
            self.assertEqual(result['predictions'], expected['predictions'])
            self.assertEqual(result['request_sha256'], S.P.digest(value))
            self.assertEqual(result['binding_sha256'], S.binding_sha256(self.binding))
            self.assertEqual(S.R.resolve.call_count, 2)
            self.assertEqual(len(result), 12)
            self.assertNotIn('rows', result)

    def test_binding_change_before_or_after_predict_refuses_result_without_rebinding(self):
        changed = deepcopy(self.resolved); changed['binding']['model_sha256'] = 'f' * 64
        for values, calls in (([changed], 0), ([self.resolved, changed], 1)):
            with patch.object(S.R, 'resolve', side_effect=values), patch.object(S.P, 'predict', wraps=S.P.predict) as predict:
                with self.assertRaisesRegex(S.ServingError, '^SERVING_BINDING_CHANGED$'):
                    S.predict(self.binding, request())
                self.assertEqual(predict.call_count, calls)
        self.assertEqual(self.binding, fixture()[2])

    def test_predictor_identity_model_and_request_must_match_registry_binding(self):
        good = S.P.predict(self.binding['binding']['training_token'], request())
        for key in ('attempt_token', 'run_id', 'experiment_id', 'store_id', 'workload_sha256',
                    'model_sha256', 'request_sha256'):
            altered = deepcopy(good)
            target = altered['identity'] if key in altered['identity'] else altered
            target[key] = 'wrong'
            with self.subTest(key=key), patch.object(S.P, 'predict', return_value=altered), \
                    self.assertRaisesRegex(S.ServingError, '^SERVING_SOURCE_UNAVAILABLE$'):
                S.predict(self.binding, request())

    def test_invalid_binding_and_version_fail_before_registry_or_prediction(self):
        S.R.resolve.reset_mock()
        for version in (None, 1, True, '01', '0', '-1', '1/../2', '1\n'):
            with self.subTest(version=version), self.assertRaises(S.ServingError): S.load_binding(version)
        S.R.resolve.assert_not_called()
        for key, value in (('schema_version', True), ('profile', 'pyfunc'), ('model_name', 'other'),
                           ('version', 1), ('extra', 'arbitrary')):
            changed = deepcopy(self.binding); changed[key] = value
            with self.assertRaises(S.ServingError): S.validate_binding(changed)

    def test_http_health_and_prediction_require_auth_and_do_not_log_or_keep_alive(self):
        status, health, _ = self.http('/health', method='GET')
        self.assertEqual((status, health), (200, S.expected_health(self.binding)))
        status, result, raw = self.http(extra=b'GET /health HTTP/1.1\r\n\r\n')
        self.assertEqual(status, 200)
        self.assertEqual(result['predictions'], [4.0, 1.5])
        self.assertEqual(raw.count(b'HTTP/1.1'), 1)
        self.assertNotIn(b'coefficients', raw)

    def test_http_host_origin_auth_and_framing_reject_without_model_reads(self):
        base = [('Host', f'{S.HOST}:{S.PORT}'), ('Authorization', 'Bearer ' + TOKEN),
                ('Content-Type', 'application/json'), ('Content-Length', '1')]
        cases = [(base[:1] + base[2:], 401),
                 (base + [('Authorization', 'Bearer ' + TOKEN)], 401),
                 ([base[0], ('Authorization', 'Bearer PRIVATE'), *base[2:]], 401),
                 ([('Host', 'attacker.invalid'), *base[1:]], 400),
                 (base + [('Host', f'{S.HOST}:{S.PORT}')], 400),
                 (base + [('Origin', 'http://127.0.0.1')], 400),
                 (base + [('Transfer-Encoding', 'chunked')], 400),
                 (base + [('Content-Length', '1')], 400),
                 (base[:-1], 400), (base[:-1] + [('Content-Length', '01')], 400),
                 (base[:-1] + [('Content-Length', str(S.MAX_INPUT_BYTES + 1))], 413)]
        S.R.resolve.reset_mock(); S.P.T.load_saved_review.reset_mock()
        for headers, expected in cases:
            with self.subTest(headers=headers):
                status, value, _ = self.http(body=b'0', headers=headers)
                self.assertEqual(status, expected); self.assertEqual(value['result'], 'failed')
        S.R.resolve.assert_not_called(); S.P.T.load_saved_review.assert_not_called()

    def test_http_strict_json_numeric_size_and_rows_reject_before_source_read(self):
        invalid = [b'{"schema_version":1,"schema_version":1,"rows":[]}',
                   b'{"schema_version":1,"rows":[{"x1":NaN,"x2":0,"x3":0}]}',
                   b'{"schema_version":1,"rows":[{"x1":1e400,"x2":0,"x3":0}]}', b'\xff', b'{}',
                   S.P.canonical(request([])), S.P.canonical(request([dict(x1=True, x2=0, x3=0)])),
                   S.P.canonical(request([dict(x1=0, x2=0, x3=0)] * 65))]
        S.R.resolve.reset_mock()
        for raw in invalid:
            with self.subTest(raw=raw[:60]):
                status, value, _ = self.http(body=raw)
                self.assertEqual((status, value['error_code']), (400, 'SERVING_REQUEST_INVALID'))
        status, _, _ = self.http(body=b' ' * (S.MAX_INPUT_BYTES + 1))
        self.assertEqual(status, 413)
        S.R.resolve.assert_not_called()

    def test_exact_body_cap_and_truncated_body_do_not_relax_framing(self):
        raw = S.P.canonical(request())
        status, result, _ = self.http(body=raw + b' ' * (S.MAX_INPUT_BYTES - len(raw)))
        self.assertEqual((status, result['result']), (200, 'passed'))
        S.R.resolve.reset_mock()
        headers = [('Host', f'{S.HOST}:{S.PORT}'), ('Authorization', 'Bearer ' + TOKEN),
                   ('Content-Type', 'application/json'), ('Content-Length', str(len(raw) + 1))]
        self.assertEqual(self.http(body=raw, headers=headers)[0], 400)
        S.R.resolve.assert_not_called()

    def test_http_unknown_routes_methods_and_source_errors_have_fixed_safe_output(self):
        self.assertEqual(self.http('/predict?version=2')[0], 404)
        self.assertEqual(self.http('/health', method='POST')[0], 405)
        self.assertEqual(self.http('/predict', method='GET')[0], 405)
        with patch.object(S.R, 'resolve', side_effect=ValueError('PRIVATE_CREDENTIAL')):
            status, result, raw = self.http()
        self.assertEqual((status, result['error_code']), (503, 'SERVING_SOURCE_UNAVAILABLE'))
        self.assertNotIn(b'PRIVATE_CREDENTIAL', raw)
        self.assertNotIn('predictions', result)

    def test_deadline_interrupts_ordinary_exception_wrappers_and_restores_alarm(self):
        handlers = []
        prior = object()
        with patch.object(S.signal, 'getitimer', return_value=(0.0, 0.0)), \
                patch.object(S.signal, 'getsignal', return_value=prior), \
                patch.object(S.signal, 'signal', side_effect=lambda *args: handlers.append(args)), \
                patch.object(S.signal, 'setitimer') as timer:
            with self.assertRaises(S.RequestDeadline):
                with S._deadline():
                    handlers[0][1](signal.SIGALRM, None)
        self.assertEqual(handlers[-1], (signal.SIGALRM, prior))
        self.assertEqual(timer.call_args_list[-1].args, (signal.ITIMER_REAL, 0))
        with patch.object(S.signal, 'getitimer', return_value=(2.0, 0.0)), patch.object(S.signal, 'signal') as change:
            with self.assertRaises(S.ServingError):
                with S._deadline(): pass
        change.assert_not_called()

    def test_accepted_connection_timeout_and_deadline_close_without_success(self):
        server = object.__new__(S.Server)
        connection = Connection(b'')
        with patch.object(S.HTTPServer, 'get_request', return_value=(connection, (S.HOST, 1))):
            self.assertEqual(server.get_request()[0], connection)
        self.assertEqual(connection.timeouts, [5])
        with patch.object(S, '_deadline', return_value=contextlib.nullcontext()), \
                patch.object(S.HTTPServer, 'finish_request', side_effect=S.RequestDeadline()) as dispatch:
            server.finish_request(connection, (S.HOST, 1))
        dispatch.assert_called_once()
        self.assertEqual(connection.output, b'')
        original = KeyboardInterrupt('original')
        with patch.object(S, '_deadline', return_value=contextlib.nullcontext()), \
                patch.object(S.HTTPServer, 'finish_request', side_effect=original), \
                self.assertRaises(KeyboardInterrupt) as caught:
            server.finish_request(connection, (S.HOST, 1))
        self.assertIs(caught.exception, original)

    def test_server_serial_loop_fixed_listener_ready_and_close_without_real_bind(self):
        server = Mock(); stopped = iter([False, False, True]); ready = Mock()
        with patch.object(S, 'Server', return_value=server) as factory:
            S.serve(self.binding, TOKEN, on_ready=ready, should_stop=lambda: next(stopped))
        factory.assert_called_once_with((S.HOST, S.PORT), S.Handler)
        self.assertEqual(server.handle_request.call_count, 2)
        ready.assert_called_once_with(); server.server_close.assert_called_once_with()
        self.assertEqual(server.timeout, .5)
        self.assertTrue(S.Server.allow_reuse_address)
        self.assertFalse(S.Server.allow_reuse_port)
        self.assertEqual(S.Server.request_queue_size, 1)

    def test_server_preserves_first_interrupt_and_port_conflict_never_retries(self):
        original = KeyboardInterrupt('first')
        server = Mock(); server.handle_request.side_effect = original
        server.server_close.side_effect = OSError('secondary close')
        with patch.object(S, 'Server', return_value=server), self.assertRaises(KeyboardInterrupt) as caught:
            S.serve(self.binding, TOKEN)
        self.assertIs(caught.exception, original)
        with patch.object(S, 'Server', side_effect=OSError(errno.EADDRINUSE, 'private')) as factory, \
                self.assertRaisesRegex(S.ServingError, '^SERVING_PORT_BUSY$'):
            S.serve(self.binding, TOKEN)
        self.assertEqual(factory.call_count, 1)


if __name__ == '__main__': unittest.main()
