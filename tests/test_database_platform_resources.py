"""Isolated resource configuration projection and both real HTTP adapters."""
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from http.client import HTTPResponse
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools/local_experiment'))
with tempfile.TemporaryDirectory() as _bootstrap:
    with patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': _bootstrap}):
        import db_platform_resources as resources
        import ai_routes
        import audit_service as audit
        import control_store as store
        import request_auth as auth
        import runtime_profile
        import warehouse_resident as wr
        import workbench_server as wb
from warehouse_operations import OperationError


ROW = b'C\t100m\t128Mi\t1\t1Gi\n'
ROWS = b'D\ttrino-local\tfixed-ns\n' + ROW + b'I\t0\t\t\t0\n' + b'D\ttrino-local-worker\tfixed-ns\n' + ROW


class Process:
    def __init__(self, raw=ROWS, returncode=0):
        self.stdout = io.BytesIO(raw)
        self.returncode = returncode
        self.finished = self.killed = False
        self.timeouts = []
    def wait(self, timeout=None):
        self.timeouts.append(timeout)
        self.finished = True
        return self.returncode
    def poll(self): return self.returncode if self.finished else None
    def kill(self): self.killed = True


class Selector:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def register(self, stream, events): pass
    def select(self, timeout): return [True]


class ResourcesTests(unittest.TestCase):
    def patch(self, item):
        result = item.start()
        self.addCleanup(item.stop)
        return result

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.patch(patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': str(self.root)}, clear=True))
        self.users = {'p': dict(role='admin', platform_admin=True, enabled=True),
                      'dba': dict(role='admin', platform_admin=False, enabled=True)}
        self.accounts = self.patch(patch.object(auth, '_principal_accounts', side_effect=lambda: self.users))
        self.principal = auth.AuthenticatedPrincipal('p', 'admin', True)
        self.foundation = self.patch(patch.object(wr, '_logical_foundation', return_value={
            'warehouse_id': 'LOCAL', 'kube': {'namespace': 'fixed-ns'}}))
        self.argv = ['rtk', 'proxy', 'kubectl', '--kubeconfig', '/private/config-secret',
                     '--context', 'context-secret', '--namespace', 'fixed-ns', '--request-timeout=15s']
        self.kube = self.patch(patch.object(resources.logical_foundation, 'kube_argv', return_value=self.argv))
        self.process = Process()
        self.runner = self.patch(patch.object(resources.subprocess, 'Popen', side_effect=lambda *a, **k: self.process))
        self.patch(patch.object(resources.selectors, 'DefaultSelector', Selector))
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            db.execute(f'PRAGMA user_version={store.VERSION}')
        self.forbidden = []
        for target in ('control_store.connect', 'control_store.root', 'query_service.submit',
                'query_service.history', 'db_ai.submit', 'db_ai._complete',
                'data_management.get_database', 'data_management.get_binding',
                'request_auth.authorize_database', 'query_context.resolve_context',
                'warehouse_lifecycle.ensure_awake', 'warehouse_lifecycle.shared_engine_proof',
                'warehouse_lifecycle.invalidate_proof', 'warehouse_lifecycle._proof_path',
                'warehouse_admission.admit', 'warehouse_resident.query',
                'warehouse_resident.execute', 'warehouse_resident.kubectl',
                'logical_registry._credentials', 'runtime_profile.logical_config',
                'urllib.request.urlopen', 'socket.create_connection'):
            self.forbidden.append(self.patch(patch(target, side_effect=AssertionError(target))))

    def observe(self, principal=None, role='admin'):
        return resources.observe(principal or self.principal, role)

    def files(self):
        return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mode)
                for p in self.root.rglob('*') if p.is_file()}

    def closed(self, value, available=True):
        self.assertEqual(set(value), {'scope', 'warehouse', 'selected_role', 'observed_at', 'coverage', 'resources'})
        self.assertEqual((value['scope'], value['warehouse'], value['selected_role']), ('platform', 'LOCAL', 'admin'))
        self.assertEqual(value['coverage'], dict(kind='deployment_container_resource_configuration',
            pod_template=True, per_container=True, live_usage=False, effective_pod_resources=False, capacity=False))
        source = value['resources']
        self.assertEqual(set(source), {'availability', 'code', 'observed_at', 'deployments'})
        for stamp in (value['observed_at'], source['observed_at']):
            self.assertTrue(stamp.endswith('Z'))
        if available:
            self.assertEqual(source['availability'], 'available')
            self.assertIsNone(source['code'])
            self.assertEqual([d['role'] for d in source['deployments']], ['coordinator', 'worker'])
            for deployment in source['deployments']:
                self.assertEqual(set(deployment), {'role', 'containers'})
                counts = dict(container=0, init=0)
                self.assertTrue(1 <= len(deployment['containers']) <= 16)
                for container in deployment['containers']:
                    self.assertEqual(set(container), {'kind', 'index', 'requests', 'limits'})
                    counts[container['kind']] += 1
                    self.assertEqual(container['index'], counts[container['kind']])
                    for key in ('requests', 'limits'):
                        self.assertEqual(set(container[key]), {'cpu', 'memory'})
                        for cell in container[key].values():
                            self.assertTrue(cell is None or type(cell) is str)
                self.assertGreater(counts['container'], 0)
        else:
            self.assertEqual(source['availability'], 'unavailable')
            self.assertEqual(source['code'], 'RESOURCE_CONFIGURATION_UNAVAILABLE')
            self.assertIsNone(source['deployments'])
        raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.assertLessEqual(len(raw), 65536)
        for secret in (b'fixed-ns', b'trino-local', b'config-secret', b'context-secret', b'error-secret'):
            self.assertNotIn(secret, raw)

    def test_true_p_without_database_schema_grants_readonly_and_no_writes(self):
        original = audit.snapshot
        @contextmanager
        def checked():
            with original() as db:
                self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0], 0)
                yield db
        before = self.files()
        with patch.object(audit, 'snapshot', checked):
            result = self.observe()
        self.closed(result)
        self.assertEqual(self.accounts.call_count, 2)
        self.assertEqual(self.foundation.call_count, 1)
        self.assertIsInstance(self.foundation.call_args.args[0], sqlite3.Connection)
        self.assertEqual(before, self.files())
        for call in self.forbidden:
            call.assert_not_called()

    def test_all_non_p_gates_before_source(self):
        principals = [auth.AuthenticatedPrincipal('dba', 'admin', False),
                      replace(self.principal, platform_admin=False),
                      replace(self.principal, maximum_role='data_engineer'),
                      replace(self.principal, maximum_role='bi_viewer'),
                      replace(self.principal, principal_id='unknown')]
        with patch.object(resources, '_collect', side_effect=AssertionError('source')) as collect:
            for principal in principals:
                with self.subTest(principal=principal), self.assertRaises(OperationError) as caught:
                    self.observe(principal)
                self.assertEqual((caught.exception.code, caught.exception.status), ('PLATFORM_ACCESS_DENIED', 403))
            for role in ('data_engineer', 'bi_viewer', 'invalid', None):
                with self.subTest(role=role), self.assertRaises(OperationError):
                    self.observe(role=role)
            for change in ({'enabled': False}, {'disabled': True}, {'platform_admin': False},
                           {'role': 'data_engineer'}, {'role': 'bi_viewer'}):
                original = self.users['p'].copy()
                self.users['p'].update(change)
                with self.subTest(change=change), self.assertRaises(OperationError):
                    self.observe()
                self.users['p'] = original
            collect.assert_not_called()
        self.foundation.assert_not_called()
        self.runner.assert_not_called()

    def test_pre_post_authority_order_and_no_database_argument(self):
        original = audit._authority
        order = []
        def authority(db, principal, role, scope, database):
            self.assertIsNone(db)
            self.assertEqual((principal, role, scope, database), (self.principal, 'admin', 'platform', None))
            order.append('auth')
            return original(db, principal, role, scope, database)
        def run(*args, **kwargs):
            order.append('source')
            return self.process
        with patch.object(audit, '_authority', authority), patch.object(resources.subprocess, 'Popen', run):
            self.closed(self.observe())
        self.assertEqual(order, ['auth', 'source', 'auth'])

    def test_post_loss_or_fingerprint_change_discards_all(self):
        for change in ({'enabled': False}, {'disabled': True}, {'platform_admin': False},
                       {'role': 'data_engineer'}, {'password_hash': 'changed-fingerprint'}):
            for fail in (False, True):
                original = self.users['p'].copy()
                def collect():
                    self.users['p'].update(change)
                    if fail:
                        raise OSError('error-secret')
                    return resources._parse(ROWS, 'fixed-ns')
                with self.subTest(change=change, fail=fail), patch.object(resources, '_collect', collect):
                    with self.assertRaises(OperationError) as caught:
                        self.observe()
                    self.assertEqual((caught.exception.code, caught.exception.status), ('PLATFORM_ACCESS_DENIED', 403))
                self.users['p'] = original

    def test_partial_store_and_foundation_failure_are_source_unavailable(self):
        for error in (OperationError('error-secret', 503), sqlite3.OperationalError('error-secret'),
                      ValueError('error-secret')):
            with patch.object(wr, '_logical_foundation', side_effect=error):
                self.closed(self.observe(), False)
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            db.execute('PRAGMA user_version=0')
        self.closed(self.observe(), False)
        (self.root / 'jobs.sqlite3').unlink()
        self.closed(self.observe(), False)
        self.assertFalse((self.root / 'jobs.sqlite3').exists())
        self.runner.assert_not_called()

    def test_fixed_command_only_template_fields_and_cleanup(self):
        self.closed(self.observe())
        argv = self.argv[:-1] + ['--request-timeout=3s', 'get', 'deployments',
            'trino-local', 'trino-local-worker', '-o', resources.JSONPATH]
        self.runner.assert_called_once_with(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False)
        expected = (r'jsonpath={range .items[*]}D{"\t"}{.metadata.name}{"\t"}{.metadata.namespace}{"\n"}'
            r'{range .spec.template.spec.containers[*]}C{"\t"}{.resources.requests.cpu}{"\t"}'
            r'{.resources.requests.memory}{"\t"}{.resources.limits.cpu}{"\t"}{.resources.limits.memory}{"\n"}{end}'
            r'{range .spec.template.spec.initContainers[*]}I{"\t"}{.resources.requests.cpu}{"\t"}'
            r'{.resources.requests.memory}{"\t"}{.resources.limits.cpu}{"\t"}{.resources.limits.memory}{"\n"}{end}{end}')
        self.assertEqual(resources.JSONPATH, expected)
        for field in ('status', 'replicas', 'image', 'env', 'command', 'capacity', 'containers[*].name'):
            self.assertNotIn(field, resources.JSONPATH)
        self.assertTrue(self.process.stdout.closed)
        self.assertFalse(self.process.killed)
        self.assertEqual(len(self.process.timeouts), 1)
        self.assertTrue(0 < self.process.timeouts[0] <= 8)

    def test_foreign_local_foundation_prevents_command(self):
        for foundation in ({'warehouse_id': 'OTHER', 'kube': {'namespace': 'fixed-ns'}},
                           {'warehouse_id': 'LOCAL', 'kube': {}},
                           {'warehouse_id': 'LOCAL', 'kube': {'namespace': ''}}):
            self.foundation.return_value = foundation
            self.closed(self.observe(), False)
        self.runner.assert_not_called()
        self.kube.assert_not_called()

    def test_all_regular_and_init_ordinal_order_and_reversed_headers(self):
        first = b'D\ttrino-local-worker\tfixed-ns\n' + ROW * 3 + b'I\t\t\t\t\n' * 2
        second = b'D\ttrino-local\tfixed-ns\n' + ROW * 2 + b'I\t0\t0\t0\t0\n' * 3
        self.process = Process(first + second)
        result = self.observe()
        self.closed(result)
        self.assertEqual([(c['kind'], c['index']) for c in result['resources']['deployments'][0]['containers']],
                         [('container', 1), ('container', 2), ('init', 1), ('init', 2), ('init', 3)])

    def test_missing_nullable_zero_and_all_missing_no_fallback(self):
        self.process = Process(b'D\ttrino-local\tfixed-ns\nC\t\t\t0\t0\nI\t0\t\t\t0\n'
                               b'D\ttrino-local-worker\tfixed-ns\nC\t\t\t\t\n')
        result = self.observe()
        self.closed(result)
        a, b = result['resources']['deployments']
        self.assertEqual(a['containers'][0]['requests'], dict(cpu=None, memory=None))
        self.assertEqual(a['containers'][0]['limits'], dict(cpu='0', memory='0'))
        self.assertEqual(a['containers'][1]['requests'], dict(cpu='0', memory=None))
        self.assertEqual(b['containers'][0]['requests'], dict(cpu=None, memory=None))
        self.assertEqual(b['containers'][0]['limits'], dict(cpu=None, memory=None))

    def test_quantity_decimal_si_binary_exponent_and_json_number_text_preserved(self):
        values = ['0', '00', '+0', '123', '1.0', '1.', '.5', '+.5', '0.001', '1e3',
                  '1E+3', '1e-3', '0e-999', '9' * 64]
        values += ['1' + unit for unit in ('n', 'u', 'm', 'k', 'M', 'G', 'T', 'P', 'E',
                                         'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei')]
        values += [json.dumps(v) for v in (0, 1, 1.5, 1e30, 1e-30)]
        for value in values:
            with self.subTest(value=value):
                row = ('C\t' + '\t'.join([value] * 4) + '\n').encode()
                self.process = Process(b'D\ttrino-local\tfixed-ns\n' + row +
                                       b'D\ttrino-local-worker\tfixed-ns\n' + ROW)
                result = self.observe()
                self.closed(result)
                self.assertEqual(result['resources']['deployments'][0]['containers'][0]['requests']['cpu'], value)

    def test_quantity_rejection_is_atomic(self):
        for value in ('-1', '-0', '-.1', 'NaN', 'Infinity', '1K', '1ki', '1mi', '1e',
                      '1e1m', '1MiB', '.', '+', '1..0', '1_000', ' 1', '1 ', '1\r',
                      '\x00', '１', '1μ', '9' * 65, '1/2', '0x10', 'true', 'null'):
            for cell in range(4):
                cells = ['0'] * 4
                cells[cell] = value
                row = ('I\t' + '\t'.join(cells) + '\n').encode()
                self.process = Process(ROWS + row)
                with self.subTest(value=value, cell=cell):
                    self.closed(self.observe(), False)

    def test_missing_duplicate_foreign_headers_and_orphan_or_malformed_rows(self):
        bad = [b'', ROW, b'D\ttrino-local\tfixed-ns\n' + ROW, ROW + ROWS,
               ROWS + b'D\ttrino-local\tfixed-ns\n' + ROW,
               ROWS.replace(b'trino-local-worker', b'foreign'),
               ROWS.replace(b'fixed-ns', b'foreign', 1),
               ROWS.replace(b'D\ttrino-local\tfixed-ns\n', b'D\ttrino-local\n'),
               ROWS.replace(ROW, b'C\t\t\t\n', 1),
               ROWS.replace(ROW, b'C\t0\t0\t0\t0\textra\n', 1),
               ROWS.replace(ROW, b'X\t0\t0\t0\t0\n', 1),
               ROWS.replace(ROW, b'I\t0\t0\t0\t0\n'),
               ROWS + b'\n', ROWS + b'\xff', ROWS.replace(b'\n', b'\r\n'),
               b'D\ttrino-local\tfixed-ns\nD\ttrino-local-worker\tfixed-ns\n' + ROW]
        for raw in bad:
            with self.subTest(raw=raw):
                self.process = Process(raw)
                self.closed(self.observe(), False)

    def test_container_line_byte_limits_and_max_public_dto(self):
        row = b'C\t' + b'\t'.join([b'9' * 64] * 4) + b'\n'
        raw = b'D\ttrino-local\tfixed-ns\n' + row * 16 + b'D\ttrino-local-worker\tfixed-ns\n' + row * 16
        self.assertEqual(len(raw.splitlines()), 34)
        self.process = Process(raw)
        self.closed(self.observe())
        combined_overflow = (b'D\ttrino-local\tfixed-ns\n' + ROW * 16 + b'I\t0\t\t\t0\n'
                             + b'D\ttrino-local-worker\tfixed-ns\n' + ROW)
        self.assertLessEqual(len(combined_overflow.splitlines()), 34)
        self.assertLessEqual(len(combined_overflow), 16384)
        for malformed in (raw + ROW, raw.replace(row, row + ROW, 1), combined_overflow, b'x' * 16385,
                          b'\n' * 35):
            self.process = Process(malformed)
            self.closed(self.observe(), False)
            self.assertTrue(self.process.stdout.closed)
        # A valid long private namespace pads stdout to the exact byte boundary.
        namespace = 'n' * 8123
        padded = b'D\ttrino-local\t' + namespace.encode() + b'\n' + ROW
        padded += b'D\ttrino-local-worker\t' + namespace.encode() + b'\n' + ROW
        if len(padded) % 2:
            padded = padded.replace(b'100m', b'1000m', 1)
        namespace += 'n' * ((16384 - len(padded)) // 2)
        padded = padded.replace(b'n' * 8123, namespace.encode())
        self.assertEqual(len(padded), 16384)
        self.assertEqual(len(resources._parse(padded, namespace)), 2)
        with self.assertRaises(ValueError):
            resources._parse(padded + b'\n', namespace)

    def test_encoded_dto_limit_discards_source(self):
        with patch.object(resources, '_collect', return_value=[{'private': 'x' * 65536}]):
            self.closed(self.observe(), False)

    def test_timeout_exit_overflow_spawn_and_kill_wait_close_single_attempt(self):
        for mode in ('select_timeout', 'deadline', 'exit', 'overflow', 'wait_timeout', 'read_error'):
            self.process = Process(b'x' * 16385 if mode == 'overflow' else ROWS,
                                   returncode=1 if mode == 'exit' else 0)
            self.runner.reset_mock()
            with ExitStack() as stack:
                if mode == 'select_timeout':
                    stack.enter_context(patch.object(Selector, 'select', return_value=[]))
                if mode == 'deadline':
                    stack.enter_context(patch.object(resources.time, 'monotonic', side_effect=[0, 9]))
                if mode == 'wait_timeout':
                    original = self.process.wait
                    def wait(timeout=None):
                        if timeout is not None:
                            raise subprocess.TimeoutExpired('fixed', timeout)
                        return original()
                    stack.enter_context(patch.object(self.process, 'wait', side_effect=wait))
                if mode == 'read_error':
                    stack.enter_context(patch.object(self.process.stdout, 'read1', side_effect=OSError('error-secret')))
                with self.subTest(mode=mode):
                    self.closed(self.observe(), False)
                    self.runner.assert_called_once()
                    self.assertTrue(self.process.stdout.closed)
                    self.assertTrue(self.process.finished)
                    self.assertEqual(self.process.killed, mode != 'exit')
                    if mode != 'exit':
                        self.assertIsNone(self.process.timeouts[-1])
        with patch.object(resources.subprocess, 'Popen', side_effect=OSError('error-secret')) as run:
            self.closed(self.observe(), False)
            run.assert_called_once()

    def test_chilled_deployment_template_is_independent_of_replica_status(self):
        # Projection has no replica/status cells: zero-replica Deployments use the same templates.
        self.assertNotIn('replicas', resources.JSONPATH)
        self.assertNotIn('status', resources.JSONPATH)
        result = self.observe()
        self.closed(result)
        self.assertEqual(result['resources']['deployments'][0]['containers'][0],
            dict(kind='container', index=1, requests=dict(cpu='100m', memory='128Mi'),
                 limits=dict(cpu='1', memory='1Gi')))

    def handlers(self):
        handlers = [wb.WorkbenchHandler]
        class Capture:
            def __init__(self, address, handler): handlers.append(handler)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def handle_request(self): pass
        with ExitStack() as stack:
            stack.enter_context(patch('warehouse_resident._profile_active', return_value=True))
            stack.enter_context(patch('logical_creations.enabled', return_value=False))
            stack.enter_context(patch('http.server.ThreadingHTTPServer', Capture))
            stack.enter_context(patch('database_creation_bridge.configure'))
            stack.enter_context(patch.object(wr, 'warehouse', return_value='LOCAL'))
            wr.serve_endpoint(once=True)
        self.assertEqual(len(handlers), 2)
        return handlers

    def request(self, handler, *, method='GET', suffix='', headers='', body='', role='admin', principal=None):
        class Connection:
            def __init__(self, raw): self.incoming, self.outgoing = io.BytesIO(raw), io.BytesIO()
            def makefile(self, *args): return self.incoming
            def sendall(self, raw): self.outgoing.write(raw)
        raw = (method + ' /api/v1/bears/platform-resources' + suffix + ' HTTP/1.1\r\n'
               'Host: 127.0.0.1\r\nConnection: close\r\nX-Shirokuma-Role: ' + role + '\r\n'
               + headers + '\r\n' + body).encode()
        conn = Connection(raw)
        with patch.object(runtime_profile, 'active', return_value=True), \
                patch.object(auth, 'authenticate_principal', return_value=principal or self.principal), \
                patch.object(handler, 'log_message'):
            handler(conn, ('127.0.0.1', 1), SimpleNamespace())
        response = HTTPResponse(Connection(conn.outgoing.getvalue()))
        response.begin()
        return response.status, json.loads(response.read())

    def test_both_actual_http_families_strict_zero_input(self):
        cases = [({}, 200), ({'headers': 'Content-Length: 0\r\n'}, 200),
                 ({'suffix': '?'}, 400), ({'suffix': '?context=%7B%7D'}, 400),
                 ({'suffix': '?database_id=secret'}, 400), ({'suffix': '?x=&x='}, 400),
                 ({'suffix': '#fragment'}, 400), ({'suffix': '?%FF'}, 400),
                 ({'headers': 'Content-Length: 1\r\n', 'body': 'x'}, 400),
                 ({'headers': 'Content-Length: nope\r\n'}, 400),
                 ({'headers': 'Content-Length: 0\r\nContent-Length: 0\r\n'}, 400),
                 ({'headers': 'Content-Length: -1\r\n'}, 400),
                 ({'headers': 'Transfer-Encoding: \r\n'}, 400),
                 ({'headers': 'Transfer-Encoding: chunked\r\n', 'body': '0\r\n\r\n'}, 400),
                 ({'method': 'POST', 'headers': 'Content-Length: 0\r\n'}, 404),
                 ({'role': 'data_engineer'}, 403), ({'role': 'bi_viewer'}, 403),
                 ({'principal': auth.AuthenticatedPrincipal('dba', 'admin', False)}, 403)]
        before = self.files()
        for handler in self.handlers():
            for options, expected in cases:
                with self.subTest(surface=handler.__name__, options=options):
                    self.process = Process()
                    self.runner.reset_mock()
                    status, value = self.request(handler, **options)
                    self.assertEqual(status, expected, value)
                    if expected == 200:
                        self.closed(value)
                    else:
                        self.runner.assert_not_called()
                        self.assertNotIn('resources', value)
                        if expected == 403:
                            self.assertEqual(value['code'], 'PLATFORM_ACCESS_DENIED')
        self.assertEqual(before, self.files())

    def test_both_http_families_source_failure_200_post_fence_403(self):
        (self.root / 'jobs.sqlite3').unlink()
        for handler in self.handlers():
            status, result = self.request(handler)
            self.assertEqual(status, 200, result)
            self.closed(result, False)
            original = audit._authority
            calls = 0
            def revoked(*args):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OperationError('error-secret', 409)
                return original(*args)
            with patch.object(audit, '_authority', revoked):
                status, result = self.request(handler)
            self.assertEqual(status, 403, result)
            self.assertEqual(result['code'], 'PLATFORM_ACCESS_DENIED')
            self.assertNotIn('resources', result)


if __name__ == '__main__':
    unittest.main()
