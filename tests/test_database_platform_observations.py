"""Platform capture: isolated persisted fixtures and synthetic Deployment runner."""
from contextlib import ExitStack
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
# Both real handler families may initialize legacy settings at import.
with tempfile.TemporaryDirectory() as _bootstrap:
    with patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': _bootstrap}):
        import db_platform_observations as obs
        import ai_routes
        import audit_service as audit
        import control_store as store
        import request_auth as auth
        import workbench_server as wb
        import warehouse_resident as wr
        import runtime_profile
from warehouse_operations import OperationError

STAMP = '2026-10-31T23:59:59.123456789012+09:30'
ROWS = b'trino-local fixed-ns 1 1 3 3\ntrino-local-worker fixed-ns 2 2 4 5\n'
FIELDS = dict(physical={'state', 'replicas', 'ready_replicas', 'deployments'},
              engine_evidence={'verified_at', 'live_probe'},
              auto_chill={'minutes', 'revision'}, saved_admissions={'active', 'unknown'})
CODES = dict(physical='PHYSICAL_UNAVAILABLE', engine_evidence='ENGINE_EVIDENCE_UNAVAILABLE',
             auto_chill='POLICY_UNAVAILABLE', saved_admissions='ADMISSIONS_UNAVAILABLE')


class Process:
    def __init__(self, raw, returncode=0):
        self.stdout = io.BytesIO(raw)
        self.returncode = returncode
        self.finished = False
        self.killed = False
        self.timeouts = []
    def wait(self, timeout=None):
        self.timeouts.append(timeout)
        self.finished = True
        return self.returncode
    def poll(self):
        return self.returncode if self.finished else None
    def kill(self):
        self.killed = True


class Selector:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def register(self, stream, events): pass
    def select(self, timeout): return [True]


class PlatformTests(unittest.TestCase):
    def patch(self, item):
        value = item.start()
        self.addCleanup(item.stop)
        return value

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.patch(patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': str(self.root)}, clear=True))
        self.users = {'p': dict(role='admin', platform_admin=True, enabled=True),
                      'dba': dict(role='admin', platform_admin=False, enabled=True)}
        self.accounts = self.patch(patch.object(auth, '_principal_accounts', side_effect=lambda: self.users))
        self.principal = auth.AuthenticatedPrincipal('p', 'admin', True)
        self.cfg = dict(warehouse_id='LOCAL', foundation_generation_id='generation-secret', foundation_revision=7)
        self.config = self.patch(patch.object(runtime_profile, 'logical_config', return_value=self.cfg))
        self.foundation = self.patch(patch.object(wr, '_logical_foundation',
                                                return_value={'warehouse_id': 'LOCAL', 'kube': {'namespace': 'fixed-ns'}}))
        self.argv = ['rtk', 'proxy', 'kubectl', '--kubeconfig', '/private/config-secret',
                     '--context', 'context-secret', '--namespace', 'fixed-ns', '--request-timeout=15s']
        self.kube = self.patch(patch.object(obs.logical_foundation, 'kube_argv', return_value=self.argv))
        self.runner = self.patch(patch.object(obs.subprocess, 'Popen', side_effect=lambda *a, **k: Process(ROWS)))
        self.selector = self.patch(patch.object(obs.selectors, 'DefaultSelector', Selector))
        self.proof = self.root / 'local-engine-ready.json'
        self.policy = self.root / 'runtime' / 'auto_chill.json'
        self.policy.parent.mkdir()
        self.proof_data = dict(generation_id='generation-secret', revision=7, epoch='epoch-secret', verified_at=STAMP)
        self.proof.write_text(json.dumps(self.proof_data))
        self.proof.chmod(0o600)
        self.policy.write_text('{"auto_chill_minutes":15,"revision":2}')
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            db.execute(f'PRAGMA user_version={store.VERSION}')
            db.execute("""CREATE TABLE warehouse_admissions (
                warehouse_id TEXT, state TEXT, kind TEXT, execution_closure TEXT,
                actor TEXT, subject_id TEXT)""")
        self.forbidden = []
        for target in ('control_store.connect', 'control_store.root',
                'query_service.submit', 'query_service.history', 'db_ai.submit', 'db_ai._complete',
                'data_management.get_database', 'data_management.get_binding',
                'request_auth.authorize_database', 'query_context.resolve_context',
                'warehouse_lifecycle.ensure_awake', 'warehouse_lifecycle.shared_engine_proof',
                'warehouse_lifecycle.invalidate_proof', 'warehouse_lifecycle._proof_path',
                'warehouse_admission.admit', 'warehouse_resident.query',
                'warehouse_resident.execute', 'warehouse_resident.kubectl',
                'warehouse_resident.runtime_status', 'logical_registry._credentials'):
            # No runtime_status exists in some revisions; all actual entry points are guarded.
            if target == 'warehouse_resident.runtime_status':
                continue
            self.forbidden.append(self.patch(patch(target, side_effect=AssertionError(target))))

    def observe(self, principal=None, role='admin'):
        return obs.observe(principal or self.principal, role)

    def files(self):
        return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mode)
                for p in self.root.rglob('*') if p.is_file()}

    def closed(self, value):
        self.assertEqual(set(value), {'scope', 'warehouse', 'selected_role', 'observed_at', 'coverage', *FIELDS})
        self.assertEqual((value['scope'], value['warehouse'], value['selected_role']), ('platform', 'LOCAL', 'admin'))
        self.assertEqual(value['coverage'], dict(kind='fixed_platform_observations', live_engine_probe=False,
                                               policy_audit=False, complete_blocker_set=False))
        for key in ('observed_at',):
            self.assertTrue(value[key].endswith('Z'))
            obs._time(value[key])
        for name, fields in FIELDS.items():
            source = value[name]
            self.assertEqual(set(source), fields | {'availability', 'code', 'observed_at'})
            self.assertTrue(source['observed_at'].endswith('Z'))
            obs._time(source['observed_at'])
            self.assertIn(source['availability'], ('available', 'unavailable'))
            if source['availability'] == 'unavailable':
                self.assertEqual(source['code'], CODES[name])
                for field in fields - {'live_probe'}:
                    self.assertIsNone(source[field])
            else:
                self.assertIsNone(source['code'])
        self.assertIs(value['engine_evidence']['live_probe'], False)
        raw = json.dumps(value)
        for secret in ('generation-secret', 'epoch-secret', 'context-secret', 'config-secret',
                       'fixed-ns', 'trino-local', 'actor-secret', 'database-secret', 'error-secret'):
            self.assertNotIn(secret, raw)

    def test_closed_success_no_database_or_grants_and_no_writes(self):
        before = self.files()
        result = self.observe()
        self.closed(result)
        self.assertTrue(all(result[k]['availability'] == 'available' for k in FIELDS))
        self.assertEqual(result['physical']['state'], 'active')
        self.assertEqual(result['physical']['replicas'], 3)
        self.assertEqual(result['physical']['ready_replicas'], 3)
        self.assertEqual(result['physical']['deployments'], [
            dict(role='coordinator', replicas=1, ready_replicas=1),
            dict(role='worker', replicas=2, ready_replicas=2)])
        self.assertEqual(result['engine_evidence']['verified_at'], STAMP)
        self.assertEqual(result['saved_admissions']['active'], 0)
        self.assertEqual(before, self.files())
        self.assertEqual(self.accounts.call_count, 2)
        self.assertEqual(self.foundation.call_count, 1)
        supplied = self.foundation.call_args.args[0]
        self.assertIsInstance(supplied, sqlite3.Connection)
        for call in self.forbidden:
            call.assert_not_called()

    def test_p_gate_before_every_source_and_no_store_prerequisite(self):
        denied = [auth.AuthenticatedPrincipal('dba', 'admin', False),
                  replace(self.principal, platform_admin=False),
                  replace(self.principal, maximum_role='data_engineer')]
        for principal in denied:
            with self.subTest(principal=principal), self.assertRaises(OperationError) as raised:
                self.observe(principal)
            self.assertEqual((raised.exception.code, raised.exception.status), ('PLATFORM_ACCESS_DENIED', 403))
        for role in ('data_engineer', 'bi_viewer', 'bogus', None):
            with self.subTest(role=role), self.assertRaises(OperationError):
                self.observe(role=role)
        for change in ({'enabled': False}, {'disabled': True}, {'role': 'data_engineer'},
                       {'platform_admin': False}):
            original = self.users['p'].copy()
            self.users['p'].update(change)
            with self.assertRaises(OperationError):
                self.observe()
            self.users['p'] = original
        self.users.pop('p')
        with self.assertRaises(OperationError):
            self.observe()
        self.runner.assert_not_called()
        self.foundation.assert_not_called()
        self.config.assert_not_called()
        # Guard the collectors themselves to establish zero ALL source calls.
        for principal, role in ((denied[0], 'admin'), (self.principal, 'bi_viewer')):
            with ExitStack() as stack:
                calls = [stack.enter_context(patch.object(obs, '_' + name,
                         side_effect=AssertionError(name))) for name in FIELDS]
                with self.assertRaises(OperationError):
                    self.observe(principal, role)
                for call in calls:
                    call.assert_not_called()

    def test_auth_called_with_none_before_and_after_sources(self):
        original = audit._authority
        order = []
        def authority(db, p, role, scope, database):
            self.assertIsNone(db)
            self.assertEqual((p, role, scope, database), (self.principal, 'admin', 'platform', None))
            order.append('auth')
            return original(db, p, role, scope, database)
        def run(*args, **kwargs):
            order.append('physical')
            return Process(ROWS)
        with patch.object(audit, '_authority', authority), patch.object(obs.subprocess, 'Popen', run):
            self.observe()
        self.assertEqual(order, ['auth', 'physical', 'auth'])

    def test_midread_principal_account_or_config_loss_discards_entire_capture(self):
        changes = ({'enabled': False}, {'platform_admin': False}, {'role': 'data_engineer'},
                   {'password_hash': 'new-fingerprint'})
        for change in changes:
            original = self.users['p'].copy()
            def run(*args, **kwargs):
                self.users['p'].update(change)
                return Process(ROWS)
            with self.subTest(change=change), patch.object(obs.subprocess, 'Popen', run):
                with self.assertRaises(OperationError) as raised:
                    self.observe()
                self.assertEqual((raised.exception.code, raised.exception.status), ('PLATFORM_ACCESS_DENIED', 403))
            self.users['p'] = original
        original_accounts = self.accounts.side_effect
        count = 0
        def unavailable():
            nonlocal count
            count += 1
            if count == 2:
                raise PermissionError('error-secret')
            return self.users
        self.accounts.side_effect = unavailable
        with self.assertRaises(OperationError):
            self.observe()
        self.accounts.side_effect = original_accounts

    def test_physical_exact_argv_single_bounded_attempt(self):
        self.observe()
        self.runner.assert_called_once_with(self.argv[:-1] + ['--request-timeout=3s',
            'get', 'deployments', 'trino-local', 'trino-local-worker', '-o', obs.COLUMNS, '--no-headers'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False)

    def test_output_cap_timeout_and_process_failure_cleanup(self):
        for mode in ('oversized', 'timeout', 'nonzero', 'outer_timeout'):
            with self.subTest(mode=mode):
                process = Process(b'x' * 50000 if mode == 'oversized' else ROWS,
                                  returncode=1 if mode == 'nonzero' else 0)
                self.runner.side_effect = lambda *a, **k: process
                class TimedSelector(Selector):
                    def select(self, timeout):
                        self_timeout = timeout
                        if not 0 < self_timeout <= 8:
                            raise AssertionError('unbounded select')
                        return [] if mode == 'timeout' else [True]
                with patch.object(obs.selectors, 'DefaultSelector', TimedSelector), ExitStack() as stack:
                    if mode == 'outer_timeout':
                        stack.enter_context(patch.object(obs.time, 'monotonic', side_effect=[0, 9]))
                    result = self.observe()
                self.closed(result)
                self.assertEqual(result['physical']['availability'], 'unavailable')
                self.assertTrue(process.stdout.closed)
                if mode in ('oversized', 'timeout', 'outer_timeout'):
                    self.assertTrue(process.killed)
                for timeout in process.timeouts:
                    if timeout is not None:
                        self.assertTrue(0 < timeout <= 8)

    def test_physical_summary_states_optional_values_and_safe_totals(self):
        cases = [
            (b'trino-local fixed-ns 0 <none> 1 <none>\ntrino-local-worker fixed-ns 0 0 1 <none>\n', 'chilled', 0, 0),
            (ROWS.replace(b'1 1 3 3', b'1 <none> 3 3'), 'transitioning', 3, 2),
            (ROWS.replace(b'3 3', b'3 <none>'), 'transitioning', 3, 3),
            (ROWS.replace(b'3 3', b'3 2'), 'transitioning', 3, 3),
            (ROWS.replace(b'1 1 3 3', b'0 1 3 3'), 'transitioning', 2, 3),
            (ROWS.replace(b'1 1 3 3', b'1 2 3 3'), 'transitioning', 3, 4),
            (b'trino-local-worker fixed-ns 2 2 4 5\ntrino-local fixed-ns 1 1 3 3\n', 'active', 3, 3),
            (ROWS.replace(b'1 1 3 3', b'9007199254740991 0 3 3').replace(b'2 2 4 5', b'0 0 4 5'),
             'transitioning', obs.SAFE, 0)]
        for raw, state, desired, ready in cases:
            with self.subTest(raw=raw):
                self.runner.side_effect = lambda *a, **k: Process(raw)
                result = self.observe()
                self.closed(result)
                self.assertEqual((result['physical']['state'], result['physical']['replicas'],
                                  result['physical']['ready_replicas']), (state, desired, ready))

    def test_physical_malformed_timeout_or_failure_isolated(self):
        cases = [b'', ROWS.splitlines()[0] + b'\n', ROWS + ROWS, ROWS.replace(b'fixed-ns', b'wrong', 1),
                 ROWS.replace(b'trino-local-worker', b'trino-local'),
                 ROWS.replace(b'trino-local-worker', b'unknown'), ROWS.replace(b'3 3', b'3'),
                 ROWS.replace(b'3 3', b'3 3 extra'), ROWS.replace(b'1 1 3 3', b'-1 1 3 3'),
                 ROWS.replace(b'1 1 3 3', b'<none> 1 3 3'), ROWS.replace(b'1 1 3 3', b'1.0 1 3 3'),
                 ROWS.replace(b'1 1 3 3', b'9007199254740992 1 3 3'),
                 ROWS.replace(b'1 1 3 3', b'9007199254740991 1 3 3'),
                 ROWS.replace(b'1 1 3 3', b'1 9007199254740991 3 3'),
                 b'x' * 4097, b'\xff',
                 subprocess.TimeoutExpired('error-secret', 8),
                 subprocess.CalledProcessError(1, 'error-secret'), OSError('error-secret')]
        for case in cases:
            with self.subTest(case=str(case)[:70]):
                self.runner.reset_mock()
                self.runner.side_effect = case if isinstance(case, Exception) else lambda *a, **k: Process(case)
                result = self.observe()
                self.closed(result)
                self.assertEqual(result['physical']['availability'], 'unavailable')
                self.assertTrue(all(result[k]['availability'] == 'available' for k in FIELDS if k != 'physical'))
                self.runner.assert_called_once()

    def test_zero_generation_rejected_even_with_positive_equal_replicas(self):
        for raw in (ROWS.replace(b'1 1 3 3', b'1 1 0 0'),
                    ROWS.replace(b'2 2 4 5', b'2 2 0 0')):
            with self.subTest(raw=raw):
                self.runner.side_effect = lambda *a, **k: Process(raw)
                result = self.observe()
                self.closed(result)
                self.assertEqual(result['physical']['availability'], 'unavailable')

    def test_foreign_or_missing_foundation_warehouse_rejected_before_command(self):
        for foundation in ({'warehouse_id': 'OTHER', 'kube': {'namespace': 'fixed-ns'}},
                           {'kube': {'namespace': 'fixed-ns'}}):
            with self.subTest(foundation=foundation):
                self.foundation.return_value = foundation
                result = self.observe()
                self.closed(result)
                self.assertEqual(result['physical']['availability'], 'unavailable')
                self.assertTrue(all(result[k]['availability'] == 'available'
                                    for k in FIELDS if k != 'physical'))
                self.kube.assert_not_called()
                self.runner.assert_not_called()

    def test_valid_private_json_extras_ignored_by_proof_and_policy_projection(self):
        private = {'private_marker': {'secret': 'valid-extra-private-marker', 'values': [1, True, None]}}
        self.proof.write_text(json.dumps({**self.proof_data, **private}))
        self.policy.write_text(json.dumps(dict(auto_chill_minutes=0, revision=3, **private)))
        before = self.files()
        result = self.observe()
        self.closed(result)
        self.assertEqual(result['engine_evidence']['availability'], 'available')
        self.assertEqual(result['engine_evidence']['verified_at'], STAMP)
        self.assertEqual(result['auto_chill']['availability'], 'available')
        self.assertEqual(result['auto_chill']['minutes'], 0)
        self.assertEqual(result['auto_chill']['revision'], 3)
        self.assertNotIn('private_marker', json.dumps(result))
        self.assertNotIn('valid-extra-private-marker', json.dumps(result))
        self.assertEqual(before, self.files())

    def test_minutes_only_policy_fixture_is_unavailable(self):
        self.policy.write_text('{"minutes":0,"revision":0}')
        result = self.observe()
        self.closed(result)
        self.assertEqual(result['auto_chill']['availability'], 'unavailable')
        self.assertTrue(all(result[k]['availability'] == 'available'
                            for k in FIELDS if k != 'auto_chill'))

    def test_foundation_failure_is_partial_without_subprocess(self):
        self.foundation.side_effect = OperationError('error-secret', 503)
        result = self.observe()
        self.closed(result)
        self.assertEqual(result['physical']['availability'], 'unavailable')
        self.runner.assert_not_called()

    def test_proof_stale_missing_invalid_bytes_and_mode_unchanged(self):
        variants = [dict(generation_id='wrong'), dict(revision=8), dict(revision=True),
                    dict(revision=0), dict(epoch=''), dict(epoch='x' * 257), dict(epoch='a\n'),
                    dict(verified_at='2026-02-30T01:00:00Z'), dict(verified_at='2026-10-31T01:00:00'),
                    dict(verified_at='2026-10-31T01:00:00+24:00'), dict(verified_at=1)]
        raws = [json.dumps({**self.proof_data, **v}).encode() for v in variants]
        raws += [b'{}', b'[]', b'\xff', b' ' * 4097,
                 b'{"revision":7,"revision":7}', b'{"unused":NaN}', b'{"unused":1e999}',
                 b'{"unused":"\\ud800"}', b'[' * 33 + b'0' + b']' * 33]
        for raw in raws:
            with self.subTest(raw=raw[:60]):
                self.proof.write_bytes(raw)
                before = self.files()
                result = self.observe()
                self.closed(result)
                self.assertEqual(result['engine_evidence']['availability'], 'unavailable')
                self.assertTrue(all(result[k]['availability'] == 'available' for k in FIELDS if k != 'engine_evidence'))
                self.assertEqual(before, self.files())
        self.proof.unlink()
        before = self.files()
        result = self.observe()
        self.closed(result)
        self.assertEqual(result['engine_evidence']['availability'], 'unavailable')
        self.assertEqual(before, self.files())

    def test_proof_long_fraction_timezone_and_persisted_zero_policy(self):
        for stamp in (STAMP, '2026-01-01T00:00:00Z', '2026-11-01T00:00:00.1-03:00'):
            self.proof.write_text(json.dumps({**self.proof_data, 'verified_at': stamp}))
            self.policy.write_text('{"auto_chill_minutes":0,"revision":0}')
            before = self.files()
            result = self.observe()
            self.closed(result)
            self.assertEqual(result['engine_evidence']['verified_at'], stamp)
            self.assertEqual(result['auto_chill']['minutes'], 0)
            self.assertEqual(result['auto_chill']['revision'], 0)
            self.assertEqual(before, self.files())

    def test_policy_missing_never_defaults_and_invalid_isolated(self):
        raws = [b'{}', b'null', b'[]', b'\xff', b'x' * 4097,
                b'{"auto_chill_minutes":0,"auto_chill_minutes":1,"revision":0}', b'{"auto_chill_minutes":NaN,"revision":0}',
                b'{"auto_chill_minutes":0,"revision":1e999}', b'[' * 33 + b'0' + b']' * 33]
        for minutes, revision in ((True, 0), (0, False), (-1, 0), (1441, 0),
                                  (0, -1), (0, obs.SAFE + 1), (0.0, 0), (0, '0')):
            raws.append(json.dumps(dict(auto_chill_minutes=minutes, revision=revision)).encode())
        for raw in raws:
            with self.subTest(raw=raw[:60]):
                self.policy.write_bytes(raw)
                before = self.files()
                result = self.observe()
                self.closed(result)
                self.assertEqual(result['auto_chill']['availability'], 'unavailable')
                self.assertTrue(all(result[k]['availability'] == 'available' for k in FIELDS if k != 'auto_chill'))
                self.assertEqual(before, self.files())
        self.policy.unlink()
        before = self.files()
        result = self.observe()
        self.closed(result)
        self.assertEqual(result['auto_chill']['availability'], 'unavailable')
        self.assertEqual(before, self.files())
        self.policy.write_text(json.dumps(dict(auto_chill_minutes=1440, revision=obs.SAFE)))
        self.assertEqual(self.observe()['auto_chill']['revision'], obs.SAFE)

    def test_count_only_local_active_unknown_exclusion_and_readonly(self):
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            for warehouse in ('LOCAL', 'OTHER'):
                for state in ('ACTIVE', 'UNKNOWN', 'FINISHED', 'FAILED', 'CANCELED'):
                    for kind, closure in (('query', ''), ('query', 'FENCED_READ_ONLY'),
                                          ('ai', 'FENCED_READ_ONLY'), ('dbt', '')):
                        db.execute('INSERT INTO warehouse_admissions VALUES (?,?,?,?,?,?)',
                                   (warehouse, state, kind, closure, 'actor-secret', 'database-secret'))
        original = audit.snapshot
        from contextlib import contextmanager
        statements = []
        @contextmanager
        def checked():
            with original() as db:
                self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0], 1)
                db.set_trace_callback(statements.append)
                yield db
        before = self.files()
        with patch.object(audit, 'snapshot', checked):
            result = self.observe()
        self.closed(result)
        self.assertEqual((result['saved_admissions']['active'], result['saved_admissions']['unknown']), (3, 3))
        self.assertEqual(before, self.files())
        admission_reads = [sql for sql in statements if 'warehouse_admissions' in sql]
        self.assertEqual(len(admission_reads), 1)
        self.assertIn('COUNT(', admission_reads[0])
        self.assertNotIn('SELECT *', admission_reads[0])
        self.assertNotIn('actor', admission_reads[0])

    def test_missing_store_or_table_only_dependent_sources_unavailable_still_pgate(self):
        path = self.root / 'jobs.sqlite3'
        for mode in ('table', 'version', 'missing'):
            with self.subTest(mode=mode):
                if mode == 'table':
                    with sqlite3.connect(path) as db:
                        db.execute('DROP TABLE warehouse_admissions')
                elif mode == 'version':
                    with sqlite3.connect(path) as db:
                        db.execute('PRAGMA user_version=0')
                else:
                    path.unlink()
                before = self.files()
                result = self.observe()
                self.closed(result)
                self.assertEqual(result['saved_admissions']['availability'], 'unavailable')
                self.assertEqual(result['physical']['availability'], 'available' if mode == 'table' else 'unavailable')
                self.assertEqual(result['engine_evidence']['availability'], 'available')
                self.assertEqual(result['auto_chill']['availability'], 'available')
                self.assertEqual(before, self.files())
                self.users['p']['enabled'] = False
                with self.assertRaises(OperationError):
                    self.observe()
                self.users['p']['enabled'] = True

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

    def request(self, handler, *, method='GET', suffix='', headers='', body='',
                role='admin', principal=None):
        class Connection:
            def __init__(self, raw): self.incoming, self.outgoing = io.BytesIO(raw), io.BytesIO()
            def makefile(self, *args): return self.incoming
            def sendall(self, raw): self.outgoing.write(raw)
        raw = (method + ' /api/v1/bears/platform-observations' + suffix + ' HTTP/1.1\r\n'
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

    def test_both_real_http_families_strict_zero_input_and_roles(self):
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
                    self.runner.reset_mock()
                    status, result = self.request(handler, **options)
                    self.assertEqual(status, expected, result)
                    if expected == 200:
                        self.closed(result)
                    else:
                        self.runner.assert_not_called()
                        self.assertNotIn('physical', result)
                        if expected == 403:
                            self.assertEqual(result['code'], 'PLATFORM_ACCESS_DENIED')
        self.assertEqual(before, self.files())

    def test_both_http_families_partial_store_failure_and_after_fence_denial(self):
        (self.root / 'jobs.sqlite3').unlink()
        for handler in self.handlers():
            status, result = self.request(handler)
            self.assertEqual(status, 200, result)
            self.closed(result)
            self.assertEqual(result['physical']['availability'], 'unavailable')
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
            self.assertNotIn('physical', result)


if __name__ == '__main__':
    unittest.main()
