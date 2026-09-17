"""Fixed caller provenance, one POST, failure and owner cleanup boundaries."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from tools.local_experiment.warehouse_lifecycle import client as C, controller as K, session as S
import test_local_warehouse_lifecycle_client as F
import test_local_warehouse_lifecycle_runner as H
import test_local_warehouse_lifecycle_session as O

REQUEST = '12345678-1234-4234-9234-123456789abc'


class FakeQueryClient(F.FakeClient, C.QueryClient):
    pass


class QueryControllerTests(unittest.TestCase):
    def setUp(self):
        self.clock = F.Clock()
        self.patchers = [patch.object(K.time, 'monotonic', self.clock.monotonic),
                         patch.object(K.time, 'sleep', self.clock.sleep)]
        for item in self.patchers:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patchers)])
        self.client = FakeQueryClient()
        self.env = F.Environment(self.client)
        self.snapshots, self.audits = [], []
        self.controller = K.Controller(self.client, guard=self.env.guard, scale=self.env.scale,
            reconnect=self.env.reconnect, progress=self.snapshots.append, decision=lambda *args: None)
        self.controller.prepare()
        self.controller.chill()

    def posts(self):
        return [r for r in self.client.http_calls if r[1] == '/v1/statement'
                and '-xs_caller-' in r[3].get('X-Trino-Source', '')]

    def query(self, audit=None):
        return self.controller.query(REQUEST, audit=audit or (lambda *row: self.audits.append(row)))

    def test_wake_probes_are_distinct_from_one_durable_caller_and_terminal_audit(self):
        def audit(projection, duration):
            self.assertEqual(len(self.posts()), 1)
            self.assertTrue(self.client.ledger()['all_results_consumed'])
            self.assertEqual(self.controller.receipt()['caller']['stage'], 'audit-pending')
            self.audits.append((projection, duration))
        result = self.query(audit)
        value = self.controller.receipt()
        S.verify_query_controller(value, F.TOKEN)
        self.assertEqual(result['case'], 'xs_caller')
        self.assertEqual(result['rows_observed'], 25)
        self.assertEqual(result['post_count'], 1)
        self.assertEqual(result['rows_sha256'], C.digest(C.expected_rows(F.TOKEN, 'xs')))
        self.assertEqual(len(value['client']['requests']), 8)
        before = next(r for r in value['client']['requests'] if r['case'] == 'xs_after')
        caller = next(r for r in value['client']['requests'] if r['case'] == 'xs_caller')
        self.assertNotEqual(before['query_id'], caller['query_id'])
        self.assertEqual(caller['source'], 'shirokuma-wl-' + F.TOKEN + '-xs_caller-' + uuid.UUID(REQUEST).hex)
        self.assertGreater(caller['opposite_route_gets'], 0)
        self.assertEqual(len(self.audits), 1)
        self.assertEqual(len(self.audits[0][0]), 17)
        pending = [r for r in self.snapshots if r['client']['pending'] == {'operation': 'query-post', 'case': 'xs_caller'}]
        self.assertTrue(pending)
        self.assertEqual(pending[0]['caller']['post_intents'], 1)
        self.assertNotIn('ALGERIA', json.dumps(value))
        self.assertNotIn(C.SELECT, json.dumps(value))

    def test_legacy_client_cannot_submit_caller_case(self):
        client = F.FakeClient()
        with self.assertRaises(C.ExperimentError):
            client.select('xs_caller')
        self.assertEqual(client.http_calls, [])
        self.assertNotIn('xs_caller', C.CASES)

    def test_saved_proof_requires_replacement_and_exact_scale_identity_chain(self):
        self.query()
        original = self.controller.receipt()
        variants = [
            lambda v: v['identity']['final']['xs'].update(pod_uid=F.UIDS[1]),
            lambda v: v['identity']['final']['s'].update(pod_uid=F.UIDS[4]),
            lambda v: v['identity']['final']['xs'].update(deployment_uid=F.UIDS[2]),
            lambda v: v['scale_operations'][0].update(to_replicas=1),
            lambda v: v['scale_operations'][1].update(from_replicas=True),
            lambda v: v['scale_operations'][1]['after'].update(pod_uid=F.UIDS[1]),
            lambda v: v['scale_operations'][0]['after'].update(pod_uid=F.UIDS[1]),
            lambda v: v['scale_operations'][1]['before'].update(deployment_uid=F.UIDS[2]),
            lambda v: v['client']['query_snapshots'][-1]['rows'].pop(),
        ]
        for mutate in variants:
            value = copy.deepcopy(original)
            mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(C.ExperimentError):
                S.verify_query_controller(value, F.TOKEN)
        S.verify_query_controller(original, F.TOKEN)

    def test_same_or_different_request_cannot_invoke_controller_twice(self):
        self.query()
        for request in (REQUEST, str(uuid.uuid4())):
            with self.assertRaisesRegex(C.ExperimentError, 'CALLER_SEQUENCE_INVALID'):
                self.controller.query(request, audit=lambda *args: None)
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(len(self.audits), 1)

    def test_request_id_validation_precedes_wake(self):
        for request in ('A' * 32, REQUEST.upper(), None):
            with self.assertRaises(C.ExperimentError):
                self.controller.query(request, audit=lambda *args: None)
        self.assertEqual(len(self.env.scales), 1)
        self.assertEqual(self.posts(), [])

    def test_request_intent_save_failure_has_no_wake_or_caller_post(self):
        def fail(value):
            if 'caller' in value:
                raise OSError('PRIVATE DISK')
        self.controller.progress = fail
        with self.assertRaises(OSError):
            self.query()
        self.assertEqual(len(self.env.scales), 1)
        self.assertEqual(self.posts(), [])

    def test_post_intent_save_failure_prevents_only_caller_post(self):
        def fail(value):
            if value['client']['pending'] == {'operation': 'query-post', 'case': 'xs_caller'}:
                raise OSError('PRIVATE DISK')
        self.controller.progress = fail
        with self.assertRaises(OSError):
            self.query()
        self.assertEqual(len(self.env.scales), 2)
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.client.ledger()['uncaptured_requests'], 1)

    def test_wake_failure_never_submits_caller_or_audit(self):
        self.env.new_process = False
        with self.assertRaisesRegex(C.ExperimentError, 'WAKE_PROCESS_NOT_REPLACED'):
            self.query()
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.audits, [])
        self.assertEqual(self.controller.receipt()['caller']['post_intents'], 0)

    def test_caller_post_unknown_preserves_pending_and_never_retries(self):
        original = self.client.request
        calls = []
        def request(target, path, **kwargs):
            if path == '/v1/statement' and '-xs_caller-' in kwargs['headers']['X-Trino-Source']:
                calls.append(True)
                raise C.ExperimentError('HTTP_TRANSPORT_FAILED')
            return original(target, path, **kwargs)
        self.client.request = request
        with self.assertRaisesRegex(C.ExperimentError, 'HTTP_TRANSPORT_FAILED'):
            self.query()
        value = self.controller.receipt()
        self.assertEqual(value['caller']['result'], 'unknown')
        self.assertEqual(value['client']['pending'], {'operation': 'query-post', 'case': 'xs_caller'})
        self.assertEqual(self.client.ledger()['uncaptured_requests'], 1)
        with self.assertRaises(C.ExperimentError):
            self.query()
        self.assertEqual(calls, [True])
        self.assertEqual(self.audits, [])

    def test_uid_change_after_durable_post_intent_blocks_http(self):
        def progress(value):
            if value['client']['pending'] == {'operation': 'query-post', 'case': 'xs_caller'}:
                self.env.current['xs']['pod_uid'] = str(uuid.uuid4())
        self.controller.progress = progress
        with self.assertRaisesRegex(C.ExperimentError, 'RUNTIME_IDENTITY_CHANGED'):
            self.query()
        self.assertEqual(self.posts(), [])

    def test_missing_caller_terminal_history_never_audits_success(self):
        original = self.client.queries
        def queries(size):
            if 'xs_caller' in self.client.handles and size == 'xs':
                self.client.history['xs'] = [r for r in self.client.history['xs']
                    if '-xs_caller-' not in r['session']['source']]
            return original(size)
        self.client.queries = queries
        with self.assertRaisesRegex(C.ExperimentError, 'QUERY_TERMINAL_EVIDENCE_MISSING'):
            self.query()
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(self.audits, [])

    def test_wrong_caller_provenance_is_rejected(self):
        original = self.client.queries
        def queries(size):
            if 'xs_caller' in self.client.handles and size == 'xs':
                self.client.history['xs'][-1]['session']['user'] = 'another-user'
            return original(size)
        self.client.queries = queries
        with self.assertRaisesRegex(C.ExperimentError, 'QUERY_LIST_FOREIGN_OR_MISMATCH'):
            self.query()
        self.assertEqual(self.audits, [])

    def test_audit_failure_keeps_consumed_query_unknown_without_second_delivery(self):
        calls = []
        def audit(*args):
            calls.append(args)
            raise OSError('PRIVATE AUDIT')
        with self.assertRaises(OSError):
            self.query(audit)
        value = self.controller.receipt()
        self.assertTrue(value['client']['ledger']['all_results_consumed'])
        self.assertTrue(value['caller']['audit_attempted'])
        self.assertFalse(value['caller']['audit_verified'])
        with self.assertRaises(C.ExperimentError):
            self.query(audit)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.posts()), 1)

    def test_cancellation_during_caller_page_preserves_object_and_open_handle(self):
        original = self.client.request
        cancelled = KeyboardInterrupt()
        def request(target, path, **kwargs):
            if path.startswith('/v1/statement/queued/') and '-xs_caller-' in kwargs['headers']['X-Trino-Source']:
                raise cancelled
            return original(target, path, **kwargs)
        self.client.request = request
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.query()
        self.assertIs(caught.exception, cancelled)
        self.assertEqual(self.controller.receipt()['caller']['result'], 'interrupted')
        self.assertEqual(self.client.ledger()['pending_handles'], 1)
        self.assertEqual(len(self.posts()), 1)


class FakeQueryAudit(H.FakeAudit):
    def query(self, projection, duration):
        self.value['query'] = (copy.deepcopy(projection), duration)


class QuerySessionTests(unittest.TestCase):
    saved = O.SessionTests.saved
    assert_receipts = O.SessionTests.assert_receipts

    def setUp(self):
        O.SessionTests.setUp(self)
        self.client = FakeQueryClient()
        self.actions = ['chill', 'query']
        self.audit = None
        self.query_hook = None

    def rpc_factory(self, session_id, output, handler, **options):
        self.assertEqual(options, {'ttl_seconds': 600, 'max_requests': 16, 'allow_query': True})
        # Reuse the thread/cleanup simulation while preserving its legacy API assertion.
        return O.SessionTests.rpc_factory(self, session_id, output, handler,
                                         ttl_seconds=600, max_requests=16)

    def owner(self):
        def client_factory(token, addresses):
            self.client.service_ips = addresses
            return self.client
        testcase = self
        class Audit(FakeQueryAudit):
            def query(self, projection, duration):
                self_test = testcase
                self_test.assertFalse(self_test.current_owner.done)
                self_test.assertTrue(self_test.client.ledger()['all_results_consumed'])
                if self_test.query_hook:
                    self_test.query_hook(projection, duration)
                super().query(projection, duration)
        def audit_factory(output, token):
            self.audit = Audit(output, token)
            return self.audit
        self.current_owner = S.SessionOwner(self.plan, self.material, self.output, kube=self.kube,
            forwarders=self.forwards, client_factory=client_factory, query_mode=True,
            rpc_factory=self.rpc_factory, port_check=lambda: None, telemetry_factory=audit_factory)
        return self.current_owner

    def test_owner_query_finishes_after_audit_then_cleans_all_owned_resources(self):
        result = self.owner().run()
        self.assertEqual(result['result'], 'passed')
        self.assertEqual([r['action'] for r in self.responses], ['chill', 'query'])
        self.assertEqual(self.responses[-1]['query']['post_count'], 1)
        self.assertEqual(len(self.responses[-1]), 11)
        self.assertEqual(len(self.responses[0]), 10)
        self.assertEqual(result['observations']['caller']['request_id'], self.responses[-1]['request_id'])
        self.assertTrue(result['cleanup']['verified'])
        self.assertFalse(self.kube.objects)
        self.assertFalse(self.forwards.active)
        self.assert_receipts()

    def test_query_before_chill_has_no_caller_post_or_scale(self):
        self.actions = ['query']
        with self.assertRaises(S.C.ExperimentError):
            self.owner().run()
        self.assertEqual(self.responses[-1]['result'], 'unknown')
        self.assertIsNone(self.responses[-1]['query'])
        self.assertFalse(self.kube.patches)
        self.assertTrue(self.saved()['cleanup']['verified'])

    def test_query_mode_does_not_accept_standalone_wake(self):
        self.actions = ['chill', 'wake']
        with self.assertRaisesRegex(S.C.ExperimentError, 'SESSION_ACTION_INVALID'):
            self.owner().run()
        self.assertEqual(len(self.kube.patches), 1)
        self.assertTrue(self.saved()['cleanup']['verified'])

    def test_query_operation_intent_write_failure_has_no_wake_and_cleans(self):
        write = S.C.write
        def fail(path, value):
            if Path(path).parent.name == 'operations' and value.get('action') == 'query':
                raise OSError('PRIVATE DISK')
            return write(path, value)
        with patch.object(S.C, 'write', fail), self.assertRaises(OSError):
            self.owner().run()
        self.assertEqual(len(self.kube.patches), 1)
        self.assertTrue(self.saved()['cleanup']['verified'])

    def test_query_audit_failure_returns_unknown_and_still_cleans(self):
        self.query_hook = lambda *args: (_ for _ in ()).throw(OSError('PRIVATE AUDIT'))
        with self.assertRaises(S.C.ExperimentError):
            self.owner().run()
        self.assertEqual(self.responses[-1]['result'], 'unknown')
        self.assertIsNone(self.responses[-1]['query'])
        self.assertEqual(self.saved()['result'], 'failed')
        self.assertTrue(self.saved()['cleanup']['verified'])
        self.assertTrue(self.saved()['observations']['client']['ledger']['all_results_consumed'])

    def test_cancel_survives_secondary_receipt_cleanup_errors(self):
        cancelled = KeyboardInterrupt()
        self.query_hook = lambda *args: (_ for _ in ()).throw(cancelled)
        owner = self.owner()
        cleanup = owner.lab.cleanup
        cleanup_attempts = []
        def broken_cleanup():
            cleanup_attempts.append(True)
            cleanup()
            raise OSError('PRIVATE CLEANUP')
        owner.lab.cleanup = broken_cleanup
        write = S.C.write
        def fail_save(path, value):
            if value.get('result') == 'interrupted':
                raise OSError('PRIVATE SAVE')
            return write(path, value)
        with patch.object(S.C, 'write', fail_save), self.assertRaises(KeyboardInterrupt) as caught:
            owner.run()
        self.assertIs(caught.exception, cancelled)
        # A broken durable store also prevents safe deletion intents. Teardown
        # must be attempted, without claiming that unrecorded deletes occurred.
        self.assertEqual(cleanup_attempts, [True])
        self.assertEqual(owner.receipt['result'], 'interrupted')
        self.assertFalse(self.forwards.active)

    def test_rpc_swallowed_worker_cancel_is_raised_by_owner_after_cleanup(self):
        cancelled = KeyboardInterrupt()
        self.swallow_handler_error = True
        self.query_hook = lambda *args: (_ for _ in ()).throw(cancelled)
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.owner().run()
        self.assertIs(caught.exception, cancelled)
        self.assertEqual(self.saved()['result'], 'interrupted')
        self.assertTrue(self.saved()['cleanup']['verified'])
        self.assertFalse(self.kube.objects)


class QueryUnixGoIntegrationTests(unittest.TestCase):
    def test_real_go_rpc_owner_caller_replay_and_prechill_rejection(self):
        """Only Unix transport is real; Kubernetes, Gateway and audit remain fake."""
        from tools.local_experiment.warehouse_lifecycle import session_rpc
        go = shutil.which('go')
        self.assertIsNotNone(go, 'Go is required for the local product CLI integration')
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp).resolve() / 'shirokuma'
            build = subprocess.run([go, 'build', '-o', str(binary), './cmd/shirokuma'],
                cwd=S.ROOT, capture_output=True, timeout=60)
            self.assertEqual(build.returncode, 0, 'offline CLI build failed')
            for chilled in (True, False):
                with self.subTest(chilled=chilled):
                    fixture = QuerySessionTests('test_owner_query_finishes_after_audit_then_cleans_all_owned_resources')
                    fixture.setUp()
                    # RPC deadlines must use real monotonic time. The existing
                    # lifecycle fake sleep is replaced with a short quiet interval.
                    for item in fixture.patchers:
                        item.stop()
                    owner = fixture.owner()
                    ready, errors, results = threading.Event(), [], []
                    def factory(token, output, handler, **options):
                        self.assertTrue(options['allow_query'])
                        return session_rpc.SessionRPC(token, output, handler, allow_query=True,
                            ttl_seconds=15, max_requests=3 if chilled else 1, replay_grace_seconds=2)
                    owner.rpc_factory = factory
                    owner.announce = lambda value: ready.set()
                    def run():
                        try:
                            results.append(owner.run())
                        except BaseException as error:
                            errors.append(error)
                    thread = threading.Thread(target=run)
                    def command(action, request_id):
                        args = (['warehouse', 'query'] if action == 'query' else ['chill'])
                        return subprocess.run([str(binary), *args, 'XS', '--session', F.TOKEN,
                            '--request-id', request_id], capture_output=True, text=True, timeout=10)
                    try:
                        with patch.object(K, 'QUIET_SECONDS', .002):
                            thread.start()
                            self.assertTrue(ready.wait(10), 'fake owner did not become ready')
                            if chilled:
                                reply = command('chill', str(uuid.uuid4()))
                                self.assertEqual(reply.returncode, 0)
                                self.assertEqual(json.loads(reply.stdout)['result'], 'verified')
                            first = command('query', REQUEST)
                            if chilled:
                                self.assertEqual(first.returncode, 0)
                                count_before = len(fixture.client.http_calls), len(fixture.kube.patches)
                                replay = command('query', REQUEST)
                                self.assertEqual(replay.returncode, 0)
                                self.assertEqual(replay.stdout, first.stdout)
                                # Replay does not run the owner handler, wake,
                                # SELECT, readiness reads or audit callback again.
                                self.assertEqual(count_before,
                                    (len(fixture.client.http_calls), len(fixture.kube.patches)))
                            else:
                                self.assertEqual(first.returncode, 1)
                                self.assertEqual(json.loads(first.stdout)['result'], 'unknown')
                            thread.join(10)
                            self.assertFalse(thread.is_alive(), 'offline owner failed to drain')
                        posts = [r for r in fixture.client.http_calls if r[1] == '/v1/statement'
                            and '-xs_caller-' in r[3].get('X-Trino-Source', '')]
                        self.assertEqual(len(posts), 1 if chilled else 0)
                        self.assertEqual(len(fixture.kube.patches), 2 if chilled else 0)
                        self.assertEqual(len(results), 1 if chilled else 0)
                        self.assertEqual(len(errors), 0 if chilled else 1)
                        self.assertTrue(fixture.saved()['cleanup']['verified'])
                        self.assertFalse(bool(fixture.kube.objects))
                        self.assertFalse(fixture.forwards.active)
                        self.assertFalse((session_rpc.SOCKET_ROOT / ('shirokuma-wl-' + F.TOKEN)).exists())
                        if chilled:
                            journal = json.loads((fixture.output / 'rpc/journal.json').read_text())
                            self.assertEqual(len(journal['requests']), 2)
                            proof = json.loads(first.stdout)['query']
                            self.assertEqual(proof['post_count'], 1)
                            self.assertNotIn(C.SELECT, first.stdout)
                            self.assertNotIn('ALGERIA', first.stdout)
                    finally:
                        owner.done = True
                        if thread.is_alive():
                            thread.join(20)
                        fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
