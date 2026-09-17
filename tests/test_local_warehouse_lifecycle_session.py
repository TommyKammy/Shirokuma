"""Offline session ownership, RPC operation and split-controller regressions."""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from tools.local_experiment.warehouse_lifecycle import session as S
from tools.local_experiment.warehouse_lifecycle import controller as K
import test_local_warehouse_lifecycle_client as F
import test_local_warehouse_lifecycle_runner as H
import test_local_lifecycle_telemetry as A


class SplitControllerTests(unittest.TestCase):
    def setUp(self):
        self.clock = F.Clock()
        self.patchers = [patch.object(K.time, 'monotonic', self.clock.monotonic),
                         patch.object(K.time, 'sleep', self.clock.sleep)]
        for item in self.patchers:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patchers)])
        self.client = F.FakeClient()
        self.env = F.Environment(self.client)
        self.events = []
        self.controller = K.Controller(self.client, guard=self.env.guard, scale=self.env.scale,
            reconnect=self.env.reconnect, decision=lambda *row: self.events.append(row))

    def test_prepare_is_stable_consumed_and_does_not_scale(self):
        result = self.controller.prepare()
        self.assertEqual(result['result'], 'prepared')
        self.assertEqual(self.env.scales, [])
        self.assertEqual(len(result['client']['requests']), 3)
        self.assertTrue(result['client']['ledger']['all_results_consumed'])
        self.assertEqual([v[0] for v in self.events], ['busy-defer'])
        self.clock.sleep(90)
        self.assertEqual(self.controller.chill()['result'], 'chilled')
        self.clock.sleep(90)
        self.assertEqual(self.controller.wake()['result'], 'verified')
        self.assertEqual([(v[0], v[1]) for v in self.env.scales], [('xs', 0), ('xs', 1)])
        self.assertEqual([v[0] for v in self.events], ['busy-defer', 'chill', 'wake'])

    def test_out_of_order_phase_never_submits_or_scales(self):
        for method in (self.controller.chill, self.controller.wake):
            with self.assertRaises(K.ExperimentError):
                method()
        self.assertEqual(self.client.http_calls, [])
        self.assertEqual(self.env.scales, [])
        self.controller.prepare()
        with self.assertRaises(K.ExperimentError):
            self.controller.prepare()
        with self.assertRaises(K.ExperimentError):
            self.controller.wake()
        self.assertEqual(self.env.scales, [])


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name).resolve() / F.TOKEN
        self.output.mkdir(mode=0o700)
        self.clock = F.Clock()
        self.patchers = [patch.object(K.time, 'monotonic', self.clock.monotonic),
                         patch.object(K.time, 'sleep', self.clock.sleep)]
        for item in self.patchers:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patchers)])
        self.material = S.fixtures.create_fixtures(F.TOKEN, self.output)
        self.images = S.R.build.PINS['images']
        self.plan = {'schema_version': 1, 'run_token': F.TOKEN, 'build_token': '2' * 32,
            'images': self.images, 'objects': S.M.render(self.images, F.TOKEN, self.material)}
        self.kube, self.forwards = H.FakeKube(), H.FakeForwards()
        self.client = F.FakeClient()
        original_scale = self.kube.scale
        def scale(current, expected, uid, replicas):
            result = original_scale(current, expected, uid, replicas)
            if replicas:
                self.client.history['xs'] = []
                self.client.instance['xs'] = 'process-xs-after'
            return result
        self.kube.scale = scale
        self.actions, self.responses, self.announcements = ['chill', 'chill', 'wake'], [], []
        self.before_request = None
        self.rpc_reason = 'stopped'
        self.rpc_verified = True
        self.swallow_handler_error = False
        self.backend = A.FakeBackend()
        H.FakeAudit.failure = False

    def rpc_factory(self, session_id, output, handler, **options):
        testcase = self
        self.assertEqual(options, {'ttl_seconds': 600, 'max_requests': 16})
        class RPC:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def serve(self, *, stop):
                for action in testcase.actions:
                    if stop():
                        break
                    if testcase.before_request:
                        testcase.before_request(action)
                    # Match the real RPC threading contract, including OTel context.
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        try:
                            reply = pool.submit(handler, action, str(uuid.uuid4())).result()
                        except BaseException:
                            if not testcase.swallow_handler_error:
                                raise
                            reply = {'result': 'unknown'}
                    testcase.responses.append(reply)
                return {'reason': testcase.rpc_reason, 'requests': len(testcase.responses),
                        'completed': len(testcase.responses), 'result': 'verified' if testcase.rpc_verified else 'failed',
                        'error_codes': [] if testcase.rpc_verified else ['JOURNAL_WRITE_FAILED']}
        return RPC()

    def owner(self, *, real_audit=False):
        def client_factory(token, addresses):
            self.assertEqual(token, F.TOKEN)
            self.client.service_ips = addresses
            return self.client
        return S.SessionOwner(self.plan, self.material, self.output, kube=self.kube,
            forwarders=self.forwards, client_factory=client_factory,
            rpc_factory=self.rpc_factory, port_check=lambda: None,
            telemetry_factory=(lambda output, token: A.T.LifecycleTelemetry(output, token, backend=self.backend))
                if real_audit else H.FakeAudit, announce=self.announcements.append)

    def saved(self):
        return json.loads((self.output / 'receipt.json').read_text())

    def assert_receipts(self):
        for response in self.responses:
            path = self.output / 'operations' / (response['operation_id'] + '.json')
            raw = S.C.read(path)
            self.assertEqual(response['receipt_sha256'], S.C.digest(raw))
            value = json.loads(raw)
            self.assertEqual(value['request_id'], response['request_id'])
            self.assertEqual(value['result'], response['result'])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(F.C.SELECT, raw.decode())
            self.assertNotIn('ALGERIA', raw.decode())

    def test_cli_cycle_noop_is_guarded_and_every_operation_has_durable_hash(self):
        result = self.owner().run()
        self.assertEqual(result['result'], 'passed')
        self.assertEqual([r['result'] for r in self.responses], ['verified', 'noop', 'verified'])
        self.assertEqual(self.responses[0]['after'], {'replicas': 0, 'backend_active': False})
        self.assertEqual(self.responses[1]['before'], self.responses[1]['after'])
        self.assertEqual(self.responses[2]['after'], {'replicas': 1, 'backend_active': True})
        self.assertEqual(len(self.kube.patches), 2)
        self.assertTrue(result['cleanup']['verified'])
        self.assertFalse(self.kube.objects)
        self.assertFalse(self.forwards.active)
        self.assert_receipts()

    def test_script_main_resolves_lazy_dependencies_and_runs_without_live_backends(self):
        from tools.local_experiment.warehouse_lifecycle import session_rpc
        script_output = self.output.parent / 'script' / F.TOKEN
        script_output.parent.mkdir(mode=0o700)
        script_output.mkdir(mode=0o700)
        self.output = script_output
        def client_factory(token, addresses):
            self.client.service_ips = addresses
            return self.client
        captured = io.StringIO()
        with patch.object(S.C, 'new_output', return_value=(F.TOKEN, script_output)), \
                patch.object(S.R.build, 'load_build', return_value={'images': self.images}), \
                patch.object(S.R, 'check_context'), patch.object(S.R, 'available_ports'), \
                patch.object(S.R, 'Kubernetes', return_value=self.kube), \
                patch.object(S.R, 'Forwarders', return_value=self.forwards), \
                patch.object(S.R, 'Client', side_effect=client_factory), \
                patch.object(A.T, 'LifecycleTelemetry', H.FakeAudit), \
                patch.object(session_rpc, 'SessionRPC', side_effect=self.rpc_factory), \
                patch.object(sys, 'argv', [S.__file__, 'run', '--build-token', '2' * 32]), \
                redirect_stdout(captured), self.assertRaises(SystemExit) as stopped:
            runpy.run_path(S.__file__, run_name='__main__')
        self.assertEqual(stopped.exception.code, 0, captured.getvalue())
        self.assertEqual([json.loads(line)['result'] for line in captured.getvalue().splitlines()],
                         ['starting', 'ready', 'passed'])
        self.assertEqual(self.saved()['result'], 'passed')
        self.assertFalse(self.kube.objects)

    def test_wake_already_prepared_noop_does_not_end_or_skip_cycle(self):
        self.actions = ['wake', 'chill', 'wake']
        self.assertEqual(self.owner().run()['result'], 'passed')
        self.assertEqual(self.responses[0]['result'], 'noop')
        self.assertEqual(len(self.kube.patches), 2)

    def test_ttl_without_wake_fails_but_cleans_prepared_resources(self):
        self.actions, self.rpc_reason = [], 'ttl'
        with self.assertRaises(S.C.ExperimentError):
            self.owner().run()
        self.assertEqual(self.saved()['result'], 'failed')
        self.assertTrue(self.saved()['cleanup']['verified'])
        self.assertFalse(self.kube.objects)
        self.assertFalse(self.kube.patches)

    def test_ttl_while_chilled_cleans_without_wake_or_success_claim(self):
        self.actions, self.rpc_reason = ['chill'], 'ttl'
        with self.assertRaises(S.C.ExperimentError):
            self.owner().run()
        self.assertEqual(len(self.kube.patches), 1)
        self.assertEqual(self.responses[0]['result'], 'verified')
        self.assertEqual(self.saved()['result'], 'failed')
        self.assertTrue(self.saved()['cleanup']['verified'])

    def test_unknown_create_does_not_adopt_or_delete_uid_from_get(self):
        self.kube.unknown_kind = 'Deployment'
        with self.assertRaises(TimeoutError):
            self.owner().run()
        self.assertFalse(self.kube.deleted)
        self.assertFalse(self.saved()['cleanup']['verified'])
        self.assertIn('unknown-deployment-create', self.saved()['cleanup']['deferred'])

    def test_noop_rechecks_live_uid_and_refuses_replacement(self):
        self.actions = ['wake']
        def replace(action):
            pod = next(v for v in self.kube.objects.values() if v['kind'] == 'Deployment'
                       and v['metadata']['labels'].get(S.M.SIZE) == 'xs')
            pod['metadata']['uid'] = str(uuid.uuid4())
        self.before_request = replace
        with self.assertRaises(S.C.ExperimentError):
            self.owner().run()
        self.assertEqual(self.responses[0]['result'], 'unknown')
        self.assertEqual(self.responses[0]['before'], S.UNKNOWN)
        self.assertFalse(self.kube.patches)
        self.assertFalse(self.saved()['cleanup']['verified'])
        self.assert_receipts()

    def test_uncertain_scale_is_failed_and_cleanup_reobserves_owned_replica_count(self):
        original = self.kube.scale
        def scale(*args):
            original(*args)
            raise TimeoutError('PRIVATE RESPONSE')
        self.kube.scale = scale
        with self.assertRaises(S.C.ExperimentError):
            self.owner().run()
        self.assertEqual(self.responses[0]['result'], 'failed')
        self.assertEqual(self.responses[0]['after'], S.UNKNOWN)
        self.assertTrue(self.saved()['cleanup']['verified'])
        self.assertFalse(self.kube.objects)
        self.assert_receipts()

    def test_failed_operation_intent_write_prevents_scale_and_still_cleans(self):
        original = S.C.write
        def write(path, value):
            if Path(path).parent.name == 'operations':
                raise OSError('PRIVATE DISK')
            return original(path, value)
        with patch.object(S.C, 'write', write), self.assertRaises(OSError):
            self.owner().run()
        self.assertFalse(self.kube.patches)
        self.assertTrue(self.saved()['cleanup']['verified'])

    def test_cleanup_failure_cannot_turn_verified_wake_into_session_success(self):
        self.kube.delete = lambda *args: (_ for _ in ()).throw(TimeoutError())
        with self.assertRaises(S.C.ExperimentError):
            self.owner().run()
        self.assertEqual(self.responses[-1]['result'], 'verified')
        self.assertEqual(self.saved()['result'], 'failed')
        self.assertFalse(self.saved()['cleanup']['verified'])

    def test_rpc_durable_response_failure_cannot_become_session_success(self):
        self.rpc_verified = False
        with self.assertRaisesRegex(S.C.ExperimentError, 'SESSION_RPC_UNVERIFIED'):
            self.owner().run()
        self.assertEqual(self.responses[-1]['result'], 'verified')
        self.assertEqual(self.saved()['result'], 'failed')
        self.assertTrue(self.saved()['cleanup']['verified'])

    def test_owner_interrupt_while_prepared_cleans_and_preserves_interrupted_result(self):
        self.before_request = lambda action: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.owner().run()
        self.assertEqual(self.saved()['result'], 'interrupted')
        self.assertTrue(self.saved()['cleanup']['verified'])
        self.assertFalse(self.kube.objects)
        self.assertFalse(self.kube.patches)

    def test_late_owner_journal_failure_cannot_pass_when_rpc_catches_it(self):
        self.swallow_handler_error = True
        original, failed = S.C.write, []
        def write(path, value):
            requests = value.get('session', {}).get('requests', [])
            if Path(path).name == 'receipt.json' and requests and requests[-1]['action'] == 'wake' \
                    and requests[-1]['result'] == 'verified' and not failed:
                failed.append(True)
                raise OSError('PRIVATE DISK')
            return original(path, value)
        with patch.object(S.C, 'write', write), self.assertRaises(S.C.ExperimentError):
            self.owner().run()
        self.assertEqual(self.responses[-1]['result'], 'unknown')
        self.assertEqual(len(self.kube.patches), 2)
        self.assertEqual(self.saved()['result'], 'failed')
        self.assertTrue(self.saved()['cleanup']['verified'])

    @unittest.skipUnless(A.HAS_OTEL, 'Pinned local LLM venv required for real OTel')
    def test_worker_thread_inherits_actual_root_span_and_four_audits(self):
        result = self.owner(real_audit=True).run()
        self.assertEqual(result['result'], 'passed')
        self.assertEqual(len(self.backend.events), 4)
        self.assertEqual(len(self.backend.logs), 4)
        self.assertTrue(result['telemetry']['saved_spans_verified'])
        rows = [json.loads(v) for v in (self.output / 'lifecycle-spans.jsonl').read_text().splitlines()]
        root = next(row for row in rows if row['name'] == 'warehouse.lifecycle.run')
        self.assertTrue(all(row['parent_span_id'] == root['span_id'] for row in rows if row is not root))
        self.assertTrue(all(row['trace_id'] == root['trace_id'] for row in rows))

    @unittest.skipUnless(A.HAS_OTEL, 'Pinned local LLM venv required for real OTel')
    def test_audit_failure_during_cli_phase_stops_before_wake(self):
        def fail(action):
            self.backend.fail = 'verify'
            self.before_request = None
        self.before_request = fail
        with self.assertRaises(S.C.ExperimentError):
            self.owner(real_audit=True).run()
        self.assertEqual(len(self.kube.patches), 1)
        self.assertEqual(self.responses[0]['result'], 'failed')
        self.assertEqual(self.saved()['telemetry']['result'], 'failed')
        self.assertTrue(self.saved()['cleanup']['verified'])


if __name__ == '__main__':
    unittest.main()
