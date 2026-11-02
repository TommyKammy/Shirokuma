"""Isolated declaration checks, authority fencing and both real HTTP adapters."""
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
        import db_platform_workload_baseline as resources
        import ai_routes
        import audit_service as audit
        import control_store as store
        import request_auth as auth
        import runtime_profile
        import warehouse_resident as wr
        import workbench_server as wb
from warehouse_operations import OperationError


def header(name='trino-local', non_root='', uid='', namespace='fixed-ns'):
    return ('D\t' + '\t'.join([name, namespace, non_root, uid]) + '\n').encode()


def row(kind='C', readonly='true', non_root='true', uid='1000', caps='["ALL"]', cpu='1', memory='1Gi'):
    return (kind + '\t' + '\t'.join([readonly, non_root, uid, caps, cpu, memory]) + '\n').encode()


ROW = row()
ROWS = header() + ROW + row('I') + header('trino-local-worker') + ROW


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


class BaselineTests(unittest.TestCase):
    def closed(self, value, available=True):
        self.assertEqual(set(value), {'scope', 'warehouse', 'selected_role', 'observed_at',
                                     'baseline_version', 'coverage', 'baseline'})
        self.assertEqual((value['scope'], value['warehouse'], value['selected_role']),
                         ('platform', 'LOCAL', 'admin'))
        self.assertEqual(value['baseline_version'], 'local-container-declarations-v1')
        self.assertEqual(value['coverage'], dict(kind='deployment_container_declarations',
            pod_template=True, per_container=True, runtime_security=False,
            admission_decision=False, global_policy=False))
        source = value['baseline']
        self.assertEqual(set(source), {'availability', 'code', 'observed_at', 'deployments'})
        for stamp in (value['observed_at'], source['observed_at']):
            self.assertTrue(stamp.endswith('Z'))
        if available:
            self.assertEqual(source['availability'], 'available')
            self.assertIsNone(source['code'])
            self.assertEqual([d['component'] for d in source['deployments']], ['coordinator', 'worker'])
            total = 0
            for deployment in source['deployments']:
                self.assertEqual(set(deployment), {'component', 'containers'})
                counts = dict(container=0, init_container=0)
                for container in deployment['containers']:
                    total += 1
                    self.assertEqual(set(container), {'kind', 'ordinal', 'checks'})
                    counts[container['kind']] += 1
                    self.assertIs(type(container['ordinal']), int)
                    self.assertEqual(container['ordinal'], counts[container['kind']])
                    self.assertEqual(list(container['checks']), ['read_only_root_filesystem',
                        'non_root', 'drop_all_capabilities', 'cpu_memory_limits'])
                    self.assertTrue(all(c in ('met', 'not_met') for c in container['checks'].values()))
                self.assertGreater(counts['container'], 0)
            self.assertLessEqual(total, 32)
        else:
            self.assertEqual(source['availability'], 'unavailable')
            self.assertEqual(source['code'], 'WORKLOAD_BASELINE_UNAVAILABLE')
            self.assertIsNone(source['deployments'])
        raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.assertLessEqual(len(raw), 65536)
        for secret in (b'fixed-ns', b'trino-local', b'config-secret', b'context-secret',
                       b'error-secret', b'securityContext', b'runAsUser', b': 1000', b'1Gi'):
            self.assertNotIn(secret, raw)
        for verdict in (b'"PASS"', b'"allow"', b'"deny"', b'"approval"', b'"security"'):
            self.assertNotIn(verdict, raw)

    def checks(self, container=None, pod_non_root='', pod_uid=''):
        self.process = Process(header(non_root=pod_non_root, uid=pod_uid)
                               + (ROW if container is None else container)
                               + header('trino-local-worker') + ROW)
        value = self.observe()
        self.closed(value)
        return value['baseline']['deployments'][0]['containers'][0]['checks']

    def test_fixed_command_exact_field_projection_and_cleanup(self):
        self.closed(self.observe())
        expected = (r'jsonpath={range .items[*]}D{"\t"}{.metadata.name}{"\t"}'
            r'{.metadata.namespace}{"\t"}{.spec.template.spec.securityContext.runAsNonRoot}'
            r'{"\t"}{.spec.template.spec.securityContext.runAsUser}{"\n"}'
            r'{range .spec.template.spec.containers[*]}C{"\t"}'
            r'{.securityContext.readOnlyRootFilesystem}{"\t"}{.securityContext.runAsNonRoot}'
            r'{"\t"}{.securityContext.runAsUser}{"\t"}{.securityContext.capabilities.drop}'
            r'{"\t"}{.resources.limits.cpu}{"\t"}{.resources.limits.memory}{"\n"}{end}'
            r'{range .spec.template.spec.initContainers[*]}I{"\t"}'
            r'{.securityContext.readOnlyRootFilesystem}{"\t"}{.securityContext.runAsNonRoot}'
            r'{"\t"}{.securityContext.runAsUser}{"\t"}{.securityContext.capabilities.drop}'
            r'{"\t"}{.resources.limits.cpu}{"\t"}{.resources.limits.memory}{"\n"}{end}{end}')
        self.assertEqual(resources.JSONPATH, expected)
        argv = self.argv[:-1] + ['--request-timeout=3s', 'get', 'deployments',
                                'trino-local', 'trino-local-worker', '-o', expected]
        self.runner.assert_called_once_with(argv, stdout=subprocess.PIPE,
                                           stderr=subprocess.DEVNULL, shell=False)
        for field in ('status', 'replicas', 'image', 'env', 'command', 'volumes', 'annotations',
                      'requests', 'metrics', 'containers[*].name'):
            self.assertNotIn(field, resources.JSONPATH)
        self.assertTrue(self.process.stdout.closed)
        self.assertFalse(self.process.killed)
        self.assertEqual(len(self.process.timeouts), 1)
        self.assertTrue(0 < self.process.timeouts[0] <= 8)

    def test_every_regular_and_init_kind_ordinal_and_reversed_components(self):
        self.process = Process(header('trino-local-worker') + ROW * 3 + row('I') * 2
                               + header() + ROW * 2 + row('I') * 3)
        value = self.observe()
        self.closed(value)
        coordinator, worker = value['baseline']['deployments']
        self.assertEqual([(c['kind'], c['ordinal']) for c in coordinator['containers']],
            [('container', 1), ('container', 2), ('init_container', 1),
             ('init_container', 2), ('init_container', 3)])
        self.assertEqual([(c['kind'], c['ordinal']) for c in worker['containers']],
            [('container', 1), ('container', 2), ('container', 3),
             ('init_container', 1), ('init_container', 2)])
        self.assertTrue(all(v == 'met' for d in value['baseline']['deployments']
                            for c in d['containers'] for v in c['checks'].values()))

    def test_missing_declarations_are_known_not_met_and_no_defaults(self):
        checks = self.checks(row(readonly='', non_root='', uid='', caps='', cpu='', memory=''))
        self.assertEqual(list(checks.values()), ['not_met'] * 4)
        self.assertEqual(self.checks(row(readonly='false'))['read_only_root_filesystem'], 'not_met')
        self.assertEqual(self.checks(row(readonly='null'))['read_only_root_filesystem'], 'not_met')
        # Pod only contributes these two non-root fields, never readonly or capabilities.
        checks = self.checks(row(readonly='', non_root='', uid='', caps=''), 'true', '1000')
        self.assertEqual(list(checks.values()), ['not_met', 'met', 'not_met', 'met'])

    def test_independent_non_root_inheritance_overrides_and_absent_uid(self):
        cases = [
            ('', '', 'true', '1000', 'met'),
            ('null', 'null', 'true', '1000', 'met'),
            ('false', '', 'true', '1000', 'not_met'),
            ('', '0', 'true', '1000', 'not_met'),
            ('true', '', 'false', '0', 'not_met'),
            ('', '1000', 'true', '0', 'met'),
            ('true', '', 'false', '1000', 'met'),
            ('true', '1000', 'false', '0', 'met'),
            ('', '1000', 'false', '0', 'not_met'),
            ('', '1000', '', '', 'not_met'),
            ('true', '', '', '', 'met'),
            ('', '', 'true', '', 'met'),
            ('true', 'null', '', 'null', 'met'),
            ('false', '', 'true', '', 'not_met'),
            ('true', '0', '', '', 'not_met'),
            ('true', str(2**63 - 1), '', '', 'met'),
            ('', '', 'true', str(2**63 - 1), 'met'),
        ]
        for nr, uid, pod_nr, pod_uid, expected in cases:
            with self.subTest(container=(nr, uid), pod=(pod_nr, pod_uid)):
                checks = self.checks(row(non_root=nr, uid=uid), pod_nr, pod_uid)
                self.assertEqual(checks['non_root'], expected)

    def test_malformed_bool_uid_and_int64_edges_are_atomic_even_if_overridden(self):
        for value in ('True', 'FALSE', '1', '0', '"true"', '[]', '{}', ' true', 'false ', '\r'):
            for field in ('readonly', 'non_root', 'pod_non_root'):
                options = {field: value}
                raw = (header(non_root=options.pop('pod_non_root', 'true')) + row(**options)
                       + header('trino-local-worker') + ROW)
                with self.subTest(value=value, field=field):
                    self.process = Process(raw)
                    self.closed(self.observe(), False)
        for value in ('-1', '-0', '+1', '1.0', '1e3', 'true', 'false', '"1"', '[]', '{}',
                      ' 1', '1 ', '01', str(2**63), '9' * 30):
            for pod in (False, True):
                raw = (header(uid=value if pod else '1000')
                       + row(uid='1000' if pod else value) + header('trino-local-worker') + ROW)
                with self.subTest(value=value, pod=pod):
                    self.process = Process(raw)
                    self.closed(self.observe(), False)

    def test_capability_exact_all_membership_with_extra_entries(self):
        for caps, expected in [('', 'not_met'), ('null', 'not_met'), ('[]', 'not_met'),
                ('["all"]', 'not_met'), ('["ALL_EXTRA"]', 'not_met'),
                ('["ALL "]', 'not_met'), ('["ALL"]', 'met'),
                ('["NET_RAW","ALL","SYS_ADMIN"]', 'met'), ('["ALL","ALL"]', 'met')]:
            with self.subTest(caps=caps):
                self.assertEqual(self.checks(row(caps=caps))['drop_all_capabilities'], expected)
        for malformed in ('ALL', 'true', '0', '{}', '"ALL"', '[null]', '[1]', '[true]',
                          '[["ALL"]]', '["ALL",]', '["ALL"]junk'):
            self.process = Process(ROWS + row('I', caps=malformed))
            with self.subTest(malformed=malformed):
                self.closed(self.observe(), False)

    def test_quantity_zero_coefficient_exponent_trap_and_positive_fraction(self):
        zero = ['', '0', '00', '+0', '0.0', '+0.0m', '0e9', '0E+9', '00Gi',
                '.0', '0.', '+.000Ki', '0e-999', '00.00Ei']
        positive = ['1', '1.', '.5', '+.5', '0.001', '1e-999', '.001E-9', '1E+999',
                    '9' * 64, '00.10Gi']
        positive += ['1' + unit for unit in ('n', 'u', 'm', 'k', 'M', 'G', 'T', 'P', 'E',
                                           'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei')]
        for values, expected in ((zero, 'not_met'), (positive, 'met')):
            for value in values:
                for field in ('cpu', 'memory'):
                    with self.subTest(value=value, field=field):
                        self.assertEqual(self.checks(row(**{field: value}))['cpu_memory_limits'], expected)

    def test_quantity_validator_is_reused_unchanged_invalid_is_atomic(self):
        import db_platform_resources
        self.assertIs(resources._quantity, db_platform_resources._quantity)
        invalid = ['-1', '-0', '-.1', 'NaN', 'Infinity', '1K', '1ki', '1mi', '1e',
                   '1e1m', '1MiB', '.', '+', '1..0', '1_000', ' 1', '1 ', '1\r',
                   '\x00', '１', '1μ', '9' * 65, '1/2', '0x10', 'true', 'null']
        for value in invalid:
            for field in ('cpu', 'memory'):
                # Invalid must not short-circuit to not_met when the other field is absent/zero.
                options = dict(cpu='', memory='0')
                options[field] = value
                with self.subTest(value=value, field=field):
                    self.process = Process(ROWS + row('I', **options))
                    self.closed(self.observe(), False)

    def test_missing_duplicate_foreign_headers_namespace_and_malformed_rows(self):
        bad = [b'', ROW, header() + ROW, ROW + ROWS, ROWS + header() + ROW,
               ROWS.replace(b'trino-local-worker', b'foreign'),
               ROWS.replace(b'fixed-ns', b'foreign', 1),
               ROWS.replace(header(), b'D\ttrino-local\tfixed-ns\n'),
               ROWS.replace(ROW, b'C\t\t\t\n', 1),
               ROWS.replace(ROW, ROW.rstrip(b'\n') + b'\textra\n', 1),
               ROWS.replace(ROW, row('X'), 1), ROWS.replace(ROW, row('I')),
               ROWS + b'\n', ROWS + b'\xff', ROWS.replace(b'\n', b'\r\n'),
               header() + header('trino-local-worker') + ROW,
               header() + row('I') + header('trino-local-worker') + ROW]
        for raw in bad:
            with self.subTest(case=bad.index(raw)):
                self.process = Process(raw)
                self.closed(self.observe(), False)

    def test_total_32_arbitrary_distribution_no_per_deployment_16_limit(self):
        for first, second in ((31, 1), (1, 31), (16, 16), (24, 8)):
            for kind in ('C', 'I'):
                # Every Deployment still has at least one ordinary container.
                raw = (header() + ROW + row(kind) * (first - 1)
                       + header('trino-local-worker') + ROW + row(kind) * (second - 1))
                self.assertEqual(len(raw.splitlines()), 34)
                self.assertLess(len(raw), 16384)
                self.process = Process(raw)
                self.closed(self.observe())
                self.process = Process(raw + row(kind))
                self.closed(self.observe(), False)
        # 33 container records under the line cap still cannot be accepted with a missing header.
        self.process = Process(header() + ROW * 33)
        self.closed(self.observe(), False)

    def test_byte_line_caps_independent_of_container_count(self):
        for raw in (b'x' * 16385, b'\n' * 35):
            self.process = Process(raw)
            self.closed(self.observe(), False)
            self.assertTrue(self.process.stdout.closed)
        namespace = 'n' * 8000
        raw = header(namespace=namespace) + ROW + header('trino-local-worker', namespace=namespace) + ROW
        # Pad an allowed capability string independently of namespace or container count.
        padding = 16384 - len(raw)
        raw = raw.replace(b'["ALL"]', b'["' + b'x' * padding + b'ALL"]', 1)
        self.assertEqual(len(raw), 16384)
        self.assertEqual(len(resources._parse(raw, namespace)), 2)
        with self.assertRaises(ValueError):
            resources._parse(raw + b'\n', namespace)

    def test_zero_replica_declarations_identical_no_replica_or_status_collection(self):
        self.assertNotIn('replicas', resources.JSONPATH)
        self.assertNotIn('status', resources.JSONPATH)
        first = self.observe()
        self.process = Process()
        second = self.observe()
        self.closed(first)
        self.closed(second)
        self.assertEqual(first['baseline']['deployments'], second['baseline']['deployments'])

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
                      replace(self.principal, maximum_role='invalid'),
                      replace(self.principal, principal_id='unknown')]
        principals += [replace(self.principal, platform_admin=value)
                       for value in (1, 0, 'true', 'false', None)]
        with patch.object(resources, '_collect', side_effect=AssertionError('source')) as collect:
            for principal in principals:
                with self.subTest(principal=principal), self.assertRaises(OperationError) as caught:
                    self.observe(principal)
                self.assertEqual((caught.exception.code, caught.exception.status), ('PLATFORM_ACCESS_DENIED', 403))
            for role in ('data_engineer', 'bi_viewer', 'invalid', None):
                with self.subTest(role=role), self.assertRaises(OperationError):
                    self.observe(role=role)
            for change in ({'enabled': False}, {'disabled': True}, {'platform_admin': False},
                           {'enabled': 1}, {'enabled': 'true'}, {'enabled': None},
                           {'disabled': 0}, {'disabled': 'false'}, {'disabled': None},
                           {'platform_admin': 1}, {'platform_admin': 'true'}, {'platform_admin': None},
                           {'role': 'data_engineer'}, {'role': 'bi_viewer'}, {'role': 'invalid'}):
                original = self.users['p'].copy()
                self.users['p'].update(change)
                with self.subTest(change=change), self.assertRaises(OperationError):
                    self.observe()
                self.users['p'] = original
            collect.assert_not_called()
        self.foundation.assert_not_called()
        self.runner.assert_not_called()

    def test_unauthorized_without_store_or_foundation_never_reads_source(self):
        (self.root / 'jobs.sqlite3').unlink()
        with patch.object(audit, 'snapshot', side_effect=AssertionError('store')) as snapshot:
            for principal in (None, object(), replace(self.principal, platform_admin=False)):
                with self.subTest(kind=type(principal).__name__), self.assertRaises(OperationError):
                    resources.observe(principal, 'admin')
            snapshot.assert_not_called()
        self.foundation.assert_not_called()
        self.runner.assert_not_called()
        self.assertFalse((self.root / 'jobs.sqlite3').exists())

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
                       {'enabled': 1}, {'disabled': 0}, {'platform_admin': 1},
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

    def test_account_disappearing_or_lookup_failure_after_source_discards_snapshot(self):
        for fail in (False, True):
            def collect():
                self.users.pop('p', None)
                if fail:
                    raise OSError('error-secret')
                return resources._parse(ROWS, 'fixed-ns')
            original = self.users['p'].copy()
            with patch.object(resources, '_collect', collect), self.assertRaises(OperationError):
                self.observe()
            self.users['p'] = original
        with patch.object(auth, '_principal_accounts', side_effect=ValueError('error-secret')):
            with self.assertRaises(OperationError):
                self.observe()
        self.runner.assert_not_called()

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

    def test_foreign_local_foundation_prevents_command(self):
        for foundation in ({'warehouse_id': 'OTHER', 'kube': {'namespace': 'fixed-ns'}},
                           {'warehouse_id': 'LOCAL', 'kube': {}},
                           {'warehouse_id': 'LOCAL', 'kube': {'namespace': ''}}):
            self.foundation.return_value = foundation
            self.closed(self.observe(), False)
        self.runner.assert_not_called()
        self.kube.assert_not_called()

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
        raw = (method + ' /api/v1/bears/platform-workload-baseline' + suffix + ' HTTP/1.1\r\n'
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
                 ({'headers': 'Content-Length: 1\r\n', 'body': ' '}, 400),
                 ({'headers': 'Content-Length: 2\r\nContent-Type: application/json\r\n', 'body': '{}'}, 400),
                 ({'headers': 'Content-Length: 00\r\n'}, 400),
                 ({'headers': 'Content-Length: +0\r\n'}, 400),
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
                        self.assertNotIn('baseline', value)
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
            self.assertNotIn('baseline', result)
