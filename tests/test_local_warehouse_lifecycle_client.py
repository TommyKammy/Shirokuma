import copy
import json
import unittest
from unittest.mock import patch

from tools.local_experiment.warehouse_lifecycle import client as C
from tools.local_experiment.warehouse_lifecycle import controller as K

TOKEN = '1' * 32
IPS = {'xs': '10.43.0.41', 's': '10.43.0.42'}
UIDS = [f'00000000-0000-4000-8000-{index:012d}' for index in range(1, 6)]


def response(value, status=200):
    return {'status': status, 'body': json.dumps(value).encode()}


class Clock:
    def __init__(self):
        self.value = 100.0

    def monotonic(self):
        return self.value

    def sleep(self, value):
        self.value += value


class FakeClient(C.Client):
    def __init__(self):
        super().__init__(TOKEN, IPS)
        self.registry_rows = []
        self.history = {'xs': [], 's': []}
        self.accepted = {}
        self.serial = 0
        self.instance = {'xs': 'process-xs-before', 's': 'process-s-before'}
        self.http_calls = []
        self.break_list = False
        self.control_failure = None
        self.fallback = False
        self.drop_history = False

    def request(self, target, path, *, data=None, headers=None, timeout=10):
        headers = headers or {}
        self.http_calls.append((target, path, data, copy.deepcopy(headers)))
        if path == '/gateway/backend/all':
            return response(self.registry_rows)
        if path == C.REGISTER:
            value = json.loads(data)
            self.registry_rows.append(value)
            return response(value)
        if path == C.HEALTH:
            return response({'code': 200, 'data': [dict(row, status='HEALTHY' if row['active'] else 'UNKNOWN',
                                                      queued=0, running=0) for row in self.registry_rows]})
        if path.startswith('/gateway/backend/activate/') or path.startswith('/gateway/backend/deactivate/'):
            if self.control_failure:
                raise C.ExperimentError(self.control_failure)
            enabled = '/activate/' in path
            for row in self.registry_rows:
                if row['name'] == C.name(TOKEN, 'xs'):
                    row['active'] = enabled
            return {'status': 200, 'body': b''}
        if path == '/v1/info':
            return response({'nodeId': C.name(TOKEN, target), 'nodeVersion': {'version': '483'},
                             'coordinator': True, 'coordinatorId': self.instance[target],
                             'starting': False, 'state': 'ACTIVE'})
        if path == '/v1/query':
            if self.break_list:
                raise C.ExperimentError('HTTP_TRANSPORT_FAILED')
            return response([] if self.drop_history else self.history[target])
        if path == '/v1/statement':
            size = headers[C.HEADER].lower()
            enabled = next(row['active'] for row in self.registry_rows if row['name'] == C.name(TOKEN, size))
            if not enabled and not self.fallback:
                return {'status': 500, 'body': b'no route'}
            if not enabled:
                size = 's'
            self.serial += 1
            query_id = f'20260917_120000_{self.serial:05d}_abcde'
            self.accepted[query_id] = {'queryId': query_id, 'state': 'FINISHED', 'size': size,
                                      'session': {'user': headers['X-Trino-User'], 'source': headers['X-Trino-Source']}}
            return response({'id': query_id, 'nextUri': C.ORIGINS['gateway'] + '/v1/statement/queued/' + query_id + '/abc/1'})
        query_id = path.split('/')[4]
        value = self.accepted[query_id]
        self.history[value['size']].append({key: v for key, v in value.items() if key != 'size'})
        return response({'id': query_id, 'columns': [{'name': k, 'type': v} for k, v in C.SELECT_COLUMNS],
                         'data': C.expected_rows(TOKEN, value['size'])})


class Environment:
    def __init__(self, client):
        self.client = client
        self.current = {'xs': {'deployment_uid': UIDS[0], 'pod_uid': UIDS[1], 'replicas': 1},
                        's': {'deployment_uid': UIDS[2], 'pod_uid': UIDS[3], 'replicas': 1}}
        self.scales = []
        self.reconnections = []
        self.new_process = True
        self.new_pod = True

    def guard(self):
        return copy.deepcopy(self.current)

    def scale(self, size, replicas):
        self.scales.append((size, replicas, self.client.ledger(), self.client.active.copy()))
        row = self.current[size]
        row.update(replicas=replicas, pod_uid=(UIDS[4] if self.new_pod else UIDS[1]) if replicas else None)
        if replicas:
            self.client.history['xs'] = []
            if self.new_process:
                self.client.instance['xs'] = 'process-xs-after'
        return copy.deepcopy(row)

    def reconnect(self, size):
        self.reconnections.append(size)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.patches = [patch.object(C.time, 'monotonic', self.clock.monotonic),
                        patch.object(C.time, 'sleep', self.clock.sleep)]
        for item in self.patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])
        self.client = FakeClient()
        self.env = Environment(self.client)
        self.events = []
        self.snapshots = []
        self.controller = K.Controller(self.client, guard=self.env.guard, scale=self.env.scale,
            reconnect=self.env.reconnect, progress=lambda row: self.snapshots.append(row),
            decision=lambda *args: self.events.append(args))

    def registered(self):
        self.client.register_backends(guard=self.env.guard)

    def test_fixed_chill_wake_and_safe_receipt(self):
        result = self.controller.run()
        self.assertEqual(result['result'], 'verified')
        self.assertTrue(all(result[key] for key in ('busy_defer_verified', 'chill_verified', 'wake_verified')))
        self.assertEqual([(row[0], row[1]) for row in self.env.scales], [('xs', 0), ('xs', 1)])
        self.assertEqual(result['identity']['initial']['s'], result['identity']['final']['s'])
        self.assertNotEqual(result['identity']['initial']['xs']['pod_uid'], result['identity']['final']['xs']['pod_uid'])
        self.assertEqual([row[0] for row in self.events], ['busy-defer', 'chill', 'wake'])
        self.assertEqual([row[1] for row in self.events], ['blocked', 'success', 'success'])
        self.assertEqual(self.events[0][2]['open_handles'], 1)
        self.assertEqual(self.events[0][2]['active_queries'], 0)
        self.assertIsNone(self.events[2][2]['active_queries'])
        self.assertEqual(self.env.reconnections, ['xs'])
        self.assertEqual(len(result['client']['requests']), 7)
        self.assertTrue(result['client']['ledger']['all_results_consumed'])
        self.assertEqual(result['client']['ledger']['uncaptured_requests'], 0)
        self.assertNotIn(C.SELECT, json.dumps(result))
        self.assertNotIn('ALGERIA', json.dumps(result))
        self.assertTrue(all(row[2]['pending_handles'] == 0 for row in self.env.scales))
        self.assertFalse(self.env.scales[0][3]['xs'])
        self.assertTrue(any(row['client']['pending'] == {'operation': 'query-post', 'case': 'xs_hold'} for row in self.snapshots))

    def test_audit_delivery_occurs_after_held_result_consumption(self):
        def event(action, *args):
            if action == 'busy-defer':
                self.assertTrue(self.client.handles['xs_hold']['row']['response_complete'])
                self.clock.sleep(25)
            self.events.append(action)
        self.controller.decision = event
        self.assertEqual(self.controller.run()['result'], 'verified')

    def test_lost_post_remains_unknown_never_idle_or_scale(self):
        original = self.client.request
        def fail(target, path, **kwargs):
            if path == '/v1/statement' and kwargs['headers']['X-Trino-Source'].endswith('-xs_hold'):
                raise C.ExperimentError('HTTP_TRANSPORT_FAILED')
            return original(target, path, **kwargs)
        self.client.request = fail
        with self.assertRaisesRegex(C.ExperimentError, 'HTTP_TRANSPORT_FAILED'):
            self.controller.run()
        self.assertEqual(self.client.ledger()['uncaptured_requests'], 1)
        self.assertEqual(self.env.scales, [])
        self.assertEqual(self.controller.receipt()['result'], 'failed')
        self.assertEqual(self.events[-1][1], 'failed')

    def test_finished_server_query_with_unconsumed_handle_is_busy(self):
        self.registered()
        row = self.client.start('xs_hold')
        self.client.history['xs'] = [{k: v for k, v in self.client.accepted[row['query_id']].items() if k != 'size'}]
        self.controller.checked_guard()
        self.clock.sleep(3)
        snapshot = self.controller.observe()
        self.assertEqual(snapshot['nonterminal_count'], 0)
        self.assertEqual(K.idle_decision(snapshot, now=self.clock.value, epoch=self.client.epoch), 'busy')

    def test_query_list_failure_is_unknown_not_empty(self):
        self.registered()
        self.client.break_list = True
        snapshot = self.controller.observe()
        self.assertEqual(snapshot['result'], 'unknown')
        self.assertEqual(K.idle_decision(snapshot, now=self.clock.value, epoch=self.client.epoch), 'unknown')

    def test_foreign_query_and_unknown_state_are_rejected(self):
        self.registered()
        self.client.select('xs_before')
        original = copy.deepcopy(self.client.history['xs'][0])
        for mutate in (lambda row: row.update(state='NEW_STATE'),
                       lambda row: row['session'].update(user='someone-else'),
                       lambda row: row['session'].update(source='someone-else'),
                       lambda row: row.update(queryId='20260917_120000_99999_abcde')):
            with self.subTest(mutate=mutate):
                row = copy.deepcopy(original)
                mutate(row)
                self.client.history['xs'] = [row]
                with self.assertRaises(C.ExperimentError):
                    self.client.queries('xs')

    def test_all_nonterminal_states_include_finishing(self):
        self.registered()
        self.client.select('xs_before')
        for state in C.STATES - C.TERMINAL:
            self.client.history['xs'][0]['state'] = state
            self.assertEqual(self.client.queries('xs')['nonterminal_count'], 1, state)

    def test_missing_terminal_history_cannot_authorize_stop(self):
        def callback(action, *args):
            if action == 'busy-defer':
                self.client.drop_history = True
        self.controller.decision = callback
        with self.assertRaisesRegex(C.ExperimentError, 'QUERY_TERMINAL_EVIDENCE_MISSING'):
            self.controller.run()
        self.assertEqual(self.env.scales, [])

    def test_deactivation_uncertainty_stops_without_scale(self):
        self.client.control_failure = 'HTTP_TRANSPORT_FAILED'
        with self.assertRaisesRegex(C.ExperimentError, 'HTTP_TRANSPORT_FAILED'):
            self.controller.run()
        self.assertEqual(self.env.scales, [])
        self.assertEqual(self.client.journal['controls'][0]['result'], 'pending')
        self.assertIsNotNone(self.client.journal['pending'])

    def test_inactive_registry_can_have_healthy_monitor_status(self):
        request = self.client.request
        def healthy(target, path, **kwargs):
            value = request(target, path, **kwargs)
            if path == C.HEALTH:
                body = json.loads(value['body'])
                for row in body['data']:
                    row['status'] = 'HEALTHY'
                return response(body)
            return value
        self.client.request = healthy
        self.assertEqual(self.controller.run()['result'], 'verified')
        self.assertFalse(self.client.journal['controls'][0]['active'])
        self.assertEqual(self.client.journal['controls'][0]['health'], 'HEALTHY')

    def test_stale_fresh_drain_rechecked_immediately_before_scale(self):
        def progress(value):
            if value['pending'] == {'operation': 'scale', 'size': 'xs', 'replicas': 0}:
                self.clock.sleep(3)
        self.controller.progress = progress
        with self.assertRaisesRegex(C.ExperimentError, 'SCALE_WITHOUT_FRESH_DRAIN'):
            self.controller.run()
        self.assertEqual(self.env.scales, [])

    def test_slow_query_observation_delivery_cannot_refresh_timestamp(self):
        self.registered()
        def progress(_):
            self.clock.sleep(3)
        self.client.progress = progress
        snapshot = self.controller.observe()
        self.assertEqual(K.idle_decision(snapshot, now=self.clock.value, epoch=self.client.epoch), 'unknown')

    def test_unchanged_s_pod_with_restarted_trino_is_rejected(self):
        original = self.env.scale
        def scale(size, replicas):
            value = original(size, replicas)
            if replicas == 1:
                self.client.instance['s'] = 'process-s-restarted'
            return value
        self.controller.scale = scale
        with self.assertRaisesRegex(C.ExperimentError, 'S_PROCESS_CHANGED'):
            self.controller.run()
        self.assertFalse(self.controller.journal['wake_verified'])

    def test_guard_identity_drift_during_observation_is_unknown(self):
        self.registered()
        self.controller.checked_guard()
        original = self.client.request
        def drift(target, path, **kwargs):
            value = original(target, path, **kwargs)
            if path == '/v1/query':
                self.env.current['s']['pod_uid'] = UIDS[4]
            return value
        self.client.request = drift
        snapshot = self.controller.observe()
        self.assertEqual(snapshot['result'], 'unknown')
        self.assertEqual(snapshot['failure_code'], 'RUNTIME_IDENTITY_CHANGED')

    def test_new_post_invalidates_drain_epoch(self):
        fired = False
        def progress(value):
            nonlocal fired
            if not fired and value['pending'] == {'operation': 'scale', 'size': 'xs', 'replicas': 0}:
                fired = True
                self.client.start('s_chilled')
        self.controller.progress = progress
        with self.assertRaisesRegex(C.ExperimentError, 'SCALE_WITHOUT_FRESH_DRAIN'):
            self.controller.run()
        self.assertEqual(self.env.scales, [])

    def test_wrong_new_pod_or_process_cannot_activate(self):
        self.env.new_process = False
        with self.assertRaisesRegex(C.ExperimentError, 'WAKE_PROCESS_NOT_REPLACED'):
            self.controller.run()
        self.assertFalse(self.client.active['xs'])
        self.assertEqual([row['operation'] for row in self.client.journal['controls']], ['deactivate'])

    def test_old_pod_uid_cannot_be_wake_success(self):
        self.env.new_pod = False
        with self.assertRaisesRegex(C.ExperimentError, 'WAKE_POD_NOT_REPLACED'):
            self.controller.run()
        self.assertFalse(self.client.active['xs'])

    def test_unknown_fallback_into_s_cannot_pass_unavailable(self):
        self.client.fallback = True
        with self.assertRaisesRegex(C.ExperimentError, 'UNAVAILABLE_NOT_PROVEN'):
            self.controller.run()
        self.assertFalse(self.controller.journal['chill_verified'])
        self.assertEqual(len(self.env.scales), 1)

    def test_decision_callback_failure_is_not_retried_or_passed(self):
        called = []
        def failure(*args):
            called.append(args)
            raise OSError('must not copy this error')
        self.controller.decision = failure
        with self.assertRaises(OSError):
            self.controller.run()
        self.assertEqual(len(called), 1)
        self.assertEqual(self.env.scales, [])
        self.assertEqual(self.controller.receipt()['result'], 'failed')
        self.assertNotIn('must not copy', json.dumps(self.controller.receipt()))

    def test_cancel_during_deactivation_preserves_uncertainty(self):
        original = self.client.request
        def cancel(target, path, **kwargs):
            if '/deactivate/' in path:
                raise KeyboardInterrupt()
            return original(target, path, **kwargs)
        self.client.request = cancel
        with self.assertRaises(KeyboardInterrupt):
            self.controller.run()
        self.assertEqual(self.controller.receipt()['result'], 'interrupted')
        self.assertEqual(self.env.scales, [])
        self.assertIsNotNone(self.client.journal['pending'])
        self.assertEqual(self.events[-1][2]['reason'], 'interrupted')

    def test_registration_guard_failure_before_any_post(self):
        def fail():
            raise C.ExperimentError('UID_CHANGED')
        with self.assertRaisesRegex(C.ExperimentError, 'UID_CHANGED'):
            self.client.register_backends(guard=fail)
        self.assertEqual(self.client.registry_rows, [])
        self.assertFalse(any(path == C.REGISTER for _, path, _, _ in self.client.http_calls))

    def test_url_and_sql_scope_remain_fixed(self):
        actual = C.Client(TOKEN, IPS)
        for target, path, data in [('xs', '/v1/statement', C.SELECT.encode()),
                                   ('gateway', '/v1/statement', b'DROP TABLE retained'),
                                   ('xs', '/v1/info/state', b'"SHUTTING_DOWN"'),
                                   ('gateway', '/gateway/backend/deactivate/foreign', b'')]:
            with self.subTest(path=path), self.assertRaises(C.ExperimentError):
                actual.request(target, path, data=data)
        for origin in ('http://example.com', 'http://127.0.0.1:18581', 'http://127.0.0.1:18580@evil'):
            with self.assertRaises(C.ExperimentError):
                C.next_uri(origin + '/v1/statement/queued/20260917_120000_00001_abcde/abc/1', 'gateway', '20260917_120000_00001_abcde')

    def test_service_ip_scope_and_distinctness(self):
        for bad in ('127.0.0.1', '0.0.0.0', '169.254.1.1', '8.8.8.8', '10.43.0.42', 'host.invalid'):
            with self.assertRaises(C.ExperimentError):
                C.Client(TOKEN, {'xs': bad, 's': IPS['s']})

    def test_invalid_query_rows_duplicate_and_bound(self):
        self.registered()
        self.client.select('xs_before')
        original = self.client.history['xs'][0]
        for rows in ([original, original], [original] * 65, [{'state': 'FINISHED'}]):
            self.client.history['xs'] = rows
            with self.assertRaises(C.ExperimentError):
                self.client.queries('xs')


if __name__ == '__main__':
    unittest.main()
