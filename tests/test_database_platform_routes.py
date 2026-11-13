"""Direct contracts for the shared Platform adapter, without HTTP harness copies."""
from contextlib import ExitStack
from email.message import Message
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import ai_routes
import platform_routes as routes
from query_context import QueryError
from warehouse_operations import OperationError


PREFIX = '/api/v1/bears/platform-'
# Keep the route ownership contract independent of the dispatcher.
SERVICES = {
    'manifest-advisory': ('manifest_advisory', 'advise'),
    'runtime-history': ('lifecycle_history', 'observe'),
    'observations': ('observations', 'observe'),
    'resources': ('resources', 'observe'),
    'usage': ('usage', 'observe'),
    'capacity': ('capacity', 'observe'),
    'workload-baseline': ('workload_baseline', 'observe'),
    'foundation-baseline': ('foundation_baseline', 'observe'),
    'foundation-controllers': ('foundation_controllers', 'observe'),
    'pod-baseline': ('pod_baseline', 'observe'),
    'controller-baseline': ('controller_baseline', 'observe'),
    'recent-failures': ('recent_failures', 'observe'),
    'telemetry-readiness': ('telemetry_readiness', 'observe'),
    'telemetry-logs': ('telemetry_logs', 'observe'),
    'telemetry-raw-logs': ('telemetry_logs', 'observe_raw'),
    'loki-errors': ('loki_errors', 'search'),
    'loki-foundation-errors': ('loki_foundation_errors', 'search'),
}


def service(name):
    module, method = SERVICES[name]
    return getattr(routes, 'db_platform_' + module), method


def handler(name, *, suffix='', headers=(), body=b'', native=False):
    message = Message()
    for key, value in headers:
        message[key] = value
    return SimpleNamespace(path=PREFIX + name + suffix, headers=message,
        rfile=io.BytesIO(body), respond=Mock(), close_connection=False,
        _native_bears=native)


class Poison:
    def __getattribute__(self, name):
        raise AssertionError('Unexpected access: ' + name)

    def __bool__(self):
        raise AssertionError('Unexpected truth test')


class PlatformRoutesTests(unittest.TestCase):
    def setUp(self):
        self.principal, self.role = object(), object()

    def dispatch(self, name, h=None, *, method='GET', params=None):
        return routes.dispatch(h if h is not None else handler(name), method,
            PREFIX + name, {} if params is None else params, self.principal, self.role)

    def error(self, code, call, status=400):
        with self.assertRaises(QueryError) as caught:
            call()
        self.assertEqual((caught.exception.code, caught.exception.status), (code, status))
        return caught.exception

    def test_every_platform_route_uses_late_module_method_and_coordinates(self):
        for name in SERVICES:
            module, method = service(name)
            for verb in (('POST',) if name == 'manifest-advisory' else
                         ('GET', 'POST') if name.startswith('loki-') else ('GET',)):
                with self.subTest(name=name, verb=verb):
                    lookup = 'capture' if verb == 'POST' and name.startswith('loki-') else method
                    h = handler(name, native=True)
                    result = object()
                    with patch.object(module, lookup, return_value=result) as operation:
                        self.assertTrue(self.dispatch(name, h, method=verb))
                    args = operation.call_args
                    self.assertIs(args.args[0], self.principal)
                    self.assertIs(args.args[1], self.role)
                    if name == 'manifest-advisory':
                        self.assertTrue(callable(args.args[2]))
                    else:
                        self.assertEqual(args.args, (self.principal, self.role))
                    self.assertEqual(args.kwargs, {'limit': 20, 'cursor': None}
                                     if name == 'runtime-history' else {})
                    h.respond.assert_called_once_with(result, 200)
                    self.assertEqual(h.close_connection,
                                     name == 'manifest-advisory' or name.startswith('loki-'))

    def test_unmatched_inputs_never_touch_handler_authority_or_services(self):
        class SourcePoison(Poison):
            def __getattribute__(self, name):
                # Branch matching reads these constants without consulting a source.
                if name in ('PATH', 'RAW_LOGS_PATH'):
                    return object.__getattribute__(self, name)
                return super().__getattribute__(name)

        with ExitStack() as stack:
            for name in {value[0] for value in SERVICES.values()}:
                module = getattr(routes, 'db_platform_' + name)
                poison = SourcePoison()
                for constant in ('PATH', 'RAW_LOGS_PATH'):
                    if hasattr(module, constant):
                        object.__setattr__(poison, constant, getattr(module, constant))
                stack.enter_context(patch.object(routes, 'db_platform_' + name, poison))
            for path in ('/api/v1/bears', '/api/v1/bears/context-observations',
                         '/api/v1/bears/ai-config', PREFIX + 'unknown', '/other'):
                self.assertFalse(routes.dispatch(Poison(), Poison(), path,
                                                 Poison(), Poison(), Poison()))

    def test_ai_delegation_and_manifest_alias(self):
        self.assertEqual(ai_routes.MANIFEST_ADVISORY_PATH, routes.MANIFEST_ADVISORY_PATH)
        h = Poison()
        with patch.object(routes, 'dispatch', return_value=True) as delegate:
            self.assertTrue(ai_routes.dispatch(h, 'GET', PREFIX + 'resources', {},
                                               self.principal, self.role))
            delegate.assert_called_once_with(h, 'GET', PREFIX + 'resources', {},
                                             self.principal, self.role)
            delegate.reset_mock()
            self.assertFalse(ai_routes.dispatch(h, Poison(), '/other', Poison(),
                                                Poison(), Poison()))
            delegate.assert_not_called()

    def test_unknown_bears_paths_keep_ai_not_found_fallback(self):
        for path in ('/api/v1/bears', '/api/v1/bears/unknown', PREFIX + 'unknown'):
            with self.subTest(path=path):
                self.error('NOT_FOUND', lambda: ai_routes.dispatch(
                    Poison(), 'GET', path, Poison(), Poison(), Poison()), 404)

    def test_operation_errors_are_translated_without_exposing_cause(self):
        for name in SERVICES:
            module, lookup = service(name)
            with self.subTest(name=name), patch.object(module, lookup,
                    side_effect=OperationError('PLATFORM_ACCESS_DENIED', 403)):
                h = handler(name)
                error = self.error('PLATFORM_ACCESS_DENIED', lambda: self.dispatch(
                    name, h, method='POST' if name == 'manifest-advisory' else 'GET'), 403)
                self.assertIsNone(error.__cause__)
                self.assertTrue(error.__suppress_context__)
                h.respond.assert_not_called()

    def test_method_rejection_precedes_headers_and_preserves_close_timing(self):
        for name in SERVICES:
            with self.subTest(name=name):
                h = SimpleNamespace(close_connection=False)
                self.error('NOT_FOUND', lambda: self.dispatch(name, h, method='DELETE'), 404)
                self.assertEqual(h.close_connection,
                                 name == 'manifest-advisory' or name.startswith('loki-'))

    def test_basic_unique_and_strict_framing_remain_distinct(self):
        for case in ('duplicate', 'defect', 'control', 'header-name'):
            for name in ('resources', 'usage', 'foundation-baseline'):
                with self.subTest(case=case, name=name):
                    h = handler(name)
                    if case == 'duplicate':
                        h.headers['X-Test'] = 'one'
                        h.headers['x-test'] = 'two'
                    elif case == 'defect':
                        h.headers.defects.append(ValueError())
                    elif case == 'control':
                        h.headers['X-Test'] = 'bad\x01value'
                    else:
                        h.headers['Bad Name'] = 'value'
                    module, method = service(name)
                    reject = name == 'foundation-baseline' or name == 'usage' and case == 'duplicate'
                    with patch.object(module, method, return_value={}) as operation:
                        if reject:
                            self.error('INVALID_INPUT', lambda: self.dispatch(name, h))
                            operation.assert_not_called()
                        else:
                            self.assertTrue(self.dispatch(name, h))
                            operation.assert_called_once()

    def test_manifest_authority_before_reader_and_early_close(self):
        module, _ = service('manifest-advisory')
        h = handler('manifest-advisory')
        h.rfile = Poison()
        h.headers = Poison()
        def denied(*args):
            self.assertTrue(h.close_connection)
            raise OperationError('PLATFORM_ACCESS_DENIED', 403)
        with patch.object(module, '_authority', side_effect=denied):
            self.error('PLATFORM_ACCESS_DENIED', lambda: self.dispatch(
                'manifest-advisory', h, method='POST'), 403)
        h.respond.assert_not_called()

    def test_manifest_complete_utf8_frame_and_limit(self):
        module, _ = service('manifest-advisory')
        valid = json.dumps({'manifest': '雪'}, ensure_ascii=False).encode()
        utf16 = '{"manifest":"snow"}'.encode('utf-16')
        utf32 = '{"manifest":"snow"}'.encode('utf-32')
        invalid_utf8 = b'{"manifest":"\xff"}'
        for body, size, accepted in ((valid, len(valid), True),
                (valid.ljust(128 * 1024), 128 * 1024, True),
                (valid, len(valid) + 1, False),
                (valid.ljust(128 * 1024 + 1), 128 * 1024 + 1, False),
                (utf16, len(utf16), False),
                (utf32, len(utf32), False),
                (invalid_utf8, len(invalid_utf8), False)):
            with self.subTest(size=size, accepted=accepted):
                h = handler('manifest-advisory', body=body, headers=(
                    ('Content-Type', 'application/json'), ('Content-Length', str(size))))
                def read(principal, role, reader):
                    self.assertTrue(h.close_connection)
                    return {'manifest': reader()}
                with patch.object(module, 'advise', side_effect=read):
                    if accepted:
                        self.assertTrue(self.dispatch('manifest-advisory', h, method='POST'))
                        h.respond.assert_called_once_with({'manifest': '雪'}, 200)
                    else:
                        self.error('INVALID_INPUT', lambda: self.dispatch(
                            'manifest-advisory', h, method='POST'))
                        h.respond.assert_not_called()
                self.assertEqual(h.rfile.tell(), 0 if size > 128 * 1024 else len(body))

    def test_manifest_encodings_on_both_captured_server_adapters(self):
        import test_database_platform_manifest_advisory as manifest_tests

        harness = manifest_tests.AdapterTests(methodName='runTest')
        self.addCleanup(harness.doCleanups)
        harness.setUp()
        document = json.dumps({'manifest': manifest_tests.YAML})
        for server_handler in harness.handlers():
            for encoding in ('utf-8', 'utf-16', 'utf-32'):
                with self.subTest(handler=server_handler.__name__, encoding=encoding):
                    body = document.encode(encoding)
                    status, result, reads = harness.request(server_handler, body=body)
                    self.assertEqual(reads, [len(body)])
                    if encoding == 'utf-8':
                        self.assertEqual((status, result),
                                         (200, manifest_tests.advisory.review(manifest_tests.YAML)))
                    else:
                        self.assertEqual((status, result['code']), (400, 'INVALID_INPUT'))

    def test_loki_native_workbench_origin_and_close(self):
        for name in ('loki-errors', 'loki-foundation-errors'):
            module, _ = service(name)
            for native, origins, allowed in ((True, (), True), (False, (), False),
                    (False, ('http://localhost',), True),
                    (True, ('http://localhost', 'http://localhost'), False)):
                with self.subTest(name=name, native=native, origins=origins):
                    h = handler(name, native=native,
                                headers=[('Origin', value) for value in origins])
                    with patch.object(module, 'capture', return_value={}) as operation, \
                         patch('http_origin.request_origin_allowed', return_value=True):
                        if allowed:
                            self.assertTrue(self.dispatch(name, h, method='POST'))
                            operation.assert_called_once_with(self.principal, self.role)
                        else:
                            self.error('INVALID_ORIGIN', lambda: self.dispatch(name, h, method='POST'), 403)
                            operation.assert_not_called()
                        self.assertTrue(h.close_connection)
            h = handler(name, headers=(('Origin', 'https://elsewhere.invalid'),))
            with patch('http_origin.request_origin_allowed', return_value=False):
                self.error('INVALID_ORIGIN', lambda: self.dispatch(name, h), 403)
            self.assertTrue(h.close_connection)

    def test_history_parses_raw_query_with_exact_grammar(self):
        module, method = service('runtime-history')
        for suffix, expected in (('', {'limit': 20, 'cursor': None}),
                ('?cursor=a_B-1.2&limit=50', {'limit': 50, 'cursor': 'a_B-1.2'}),
                ('?limit=1', {'limit': 1, 'cursor': None})):
            with self.subTest(suffix=suffix), patch.object(module, method, return_value={}) as operation:
                self.assertTrue(self.dispatch('runtime-history', handler('runtime-history', suffix=suffix),
                                              params={'ignored': ['parsed-values']}))
                operation.assert_called_once_with(self.principal, self.role, **expected)
        for suffix in ('?', '?limit=0', '?limit=51', '?limit=01', '?limit=1&limit=2',
                '?cursor=', '?cursor=a%2Eb', '?cursor=a+b', '?limit=1&', '?unknown=1',
                '?cursor=a&cursor=b', '#fragment'):
            with self.subTest(suffix=suffix), patch.object(module, method) as operation:
                self.error('INVALID_INPUT', lambda: self.dispatch(
                    'runtime-history', handler('runtime-history', suffix=suffix)))
                operation.assert_not_called()


if __name__ == '__main__':
    unittest.main()
