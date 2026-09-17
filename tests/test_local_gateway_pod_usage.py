"""Fixed PodMetrics projection and bounded fake transport; no cluster requests."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('usage_test_gateway', ROOT / 'tools/local_experiment/gateway/core.py')
G = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(G)
U = G.POD_USAGE
START = '2026-09-18T01:00:00.000000Z'
REQUEST = '2026-09-18T01:59:59.000000000Z'
OBSERVED = '2026-09-18T02:00:00.000000000Z'
POD = 'trino-local-77d97b477f-test1'
UID = '11111111-1111-1111-1111-111111111111'
SENTINEL = 'SECRET_VALUE_NEVER_PUBLISHED'


def pod():
    return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': POD, 'namespace': U.NAMESPACE,
            'uid': UID, 'creationTimestamp': '2026-09-18T00:00:00Z',
            'labels': {U.LABEL: U.TARGET}, 'annotations': {'private': SENTINEL}},
            'spec': {'containers': [{'name': 'trino', 'env': [{'name': 'SECRET', 'value': SENTINEL}]}]},
            'status': {'phase': 'Running', 'containerStatuses': [{'name': 'trino', 'ready': True,
                'restartCount': 1, 'containerID': 'docker://' + 'a' * 64,
                'state': {'running': {'startedAt': START}}}]}}


def metrics():
    return {'apiVersion': 'metrics.k8s.io/v1beta1', 'kind': 'PodMetrics',
            'metadata': {'name': POD, 'namespace': U.NAMESPACE, 'creationTimestamp': OBSERVED,
                         'annotations': {'private': SENTINEL}},
            'timestamp': '2026-09-18T01:59:58.123456789Z', 'window': '30.000000001s',
            'containers': [{'name': 'trino', 'usage': {'cpu': '123456789n', 'memory': '2Gi'}}]}


def observed():
    return U.project(pod(), metrics(), pod(), request_started_at=REQUEST, observed_at=OBSERVED)


def unknown():
    return U.unknown('READ_FAILED', identity=U.pod_identity(pod())[0], attempts=1,
                     started=REQUEST, observed=OBSERVED)


class PodUsageTests(unittest.TestCase):
    def collect(self, value=None, *, limit=50, fetch=None, clock=None):
        fetch = fetch or Mock(side_effect=[metrics(), pod()])
        clock = clock or Mock(side_effect=[REQUEST, OBSERVED])
        result = U.collect({'items': [pod()]} if value is None else value,
                           limit=limit, fetch=fetch, now=clock)
        return result, fetch

    def test_observed_exact_ns_zero_and_private_projection(self):
        source = metrics()
        source['containers'][0]['usage'] = {'cpu': '0', 'memory': '0'}
        original = copy.deepcopy(source)
        result = U.project(pod(), source, pod(), request_started_at=REQUEST, observed_at=OBSERVED)
        self.assertEqual(result['sample'], {'timestamp': '2026-09-18T01:59:58.123456789Z',
            'window_ns': '30000000001', 'cpu_nanocores': '0', 'memory_working_set_bytes': '0'})
        self.assertEqual(source, original)
        self.assertEqual(result['binding'], 'name-time-correlated')
        self.assertNotIn(SENTINEL, json.dumps(result))
        self.assertNotIn('docker://', json.dumps(result))
        self.assertEqual(result['identity']['started_at'], START)
        self.assertEqual(U.validate(result), result)
        self.assertIsNot(U.validate(result)['sample'], result['sample'])

    def test_collect_at_most_two_fixed_gets(self):
        result, fetch = self.collect()
        self.assertEqual(result['state'], 'observed')
        self.assertEqual(fetch.call_args_list, [(('metrics', POD),), (('pod', POD),)])

    def test_missing_and_truncated_or_ambiguous_targets_do_not_fetch(self):
        cases = [({'items': []}, 50, 'TARGET_NOT_FOUND'),
                 ({'items': [pod(), pod()]}, 50, 'TARGET_AMBIGUOUS'),
                 ({'items': [pod(), pod()]}, 1, 'POD_LIST_INCOMPLETE'),
                 ({'items': [pod()], 'metadata': {'continue': 'not-saved'}}, 50, 'POD_LIST_INCOMPLETE')]
        for data, limit, code in cases:
            with self.subTest(code=code):
                result, fetch = self.collect(data, limit=limit)
                self.assertEqual((result['reason'], result['get_attempts']), (code, 0))
                fetch.assert_not_called()

    def test_nonrunning_or_extra_container_is_unknown_without_fetch(self):
        for mutate in (lambda p: p['status'].update(phase='Pending'),
                       lambda p: p['spec']['containers'].append({'name': 'sidecar'}),
                       lambda p: p['spec'].update(initContainers=[{'name': 'init'}]),
                       lambda p: p['metadata'].update(deletionTimestamp=OBSERVED),
                       lambda p: p['metadata'].update(name='../elsewhere'),
                       lambda p: p['status']['containerStatuses'][0].update(containerID=SENTINEL)):
            value = pod(); mutate(value)
            result, fetch = self.collect({'items': [value]})
            self.assertEqual(result['state'], 'unknown')
            self.assertIsNone(result['sample'])
            fetch.assert_not_called()

    def test_pod_uid_container_restart_start_and_label_drift_never_observed(self):
        for mutate in (lambda p: p['metadata'].update(uid='22222222-2222-2222-2222-222222222222'),
                       lambda p: p['status']['containerStatuses'][0].update(containerID='docker://' + 'b' * 64),
                       lambda p: p['status']['containerStatuses'][0].update(restartCount=2),
                       lambda p: p['status']['containerStatuses'][0]['state']['running'].update(startedAt='2026-09-18T01:00:00.000000001Z'),
                       lambda p: p['metadata'].update(creationTimestamp='2026-09-18T00:00:00.000000001Z'),
                       lambda p: p['metadata']['labels'].update({U.LABEL: 'foreign'})):
            after = pod(); mutate(after)
            result, _ = self.collect(fetch=Mock(side_effect=[metrics(), after]))
            self.assertEqual(result['state'], 'unknown')
            self.assertIsNone(result['sample'])

    def test_missing_quantity_and_wrong_metrics_identity_are_not_zero(self):
        for mutate in (lambda m: m['containers'][0]['usage'].pop('cpu'),
                       lambda m: m['containers'][0]['usage'].update(memory=None),
                       lambda m: m['metadata'].update(name='foreign'),
                       lambda m: m['metadata'].update(namespace='foreign'),
                       lambda m: m['metadata'].update(uid='22222222-2222-2222-2222-222222222222'),
                       lambda m: m['containers'].append({'name': 'sidecar', 'usage': {'cpu': '1', 'memory': '1'}})):
            value = metrics(); mutate(value)
            result, _ = self.collect(fetch=Mock(side_effect=[value, pod()]))
            self.assertEqual(result['state'], 'unknown')
            self.assertIsNone(result['sample'])

    def test_exact_quantities_and_supported_si_case(self):
        for raw, resource, expected in [('1', 'cpu', '1000000000'), ('0.001', 'cpu', '1000000'),
            ('0.000000001', 'cpu', '1'), ('17u', 'cpu', '17000'), ('12m', 'cpu', '12000000'),
            ('1.5Gi', 'memory', '1610612736'), ('1000k', 'memory', '1000000'),
            (str(U.INT64), 'memory', str(U.INT64))]:
            self.assertEqual(U.quantity(raw, resource), expected)
        for raw, resource in [('1K', 'memory'), ('1e3', 'cpu'), ('-1', 'cpu'), ('NaN', 'memory'),
            ('0.1n', 'cpu'), ('0.1', 'memory'), ('9223372036854775808', 'memory'),
            (True, 'cpu'), (1.0, 'memory'), ('0' * 65, 'cpu')]:
            with self.subTest(raw=raw), self.assertRaises(U.PodUsageError):
                U.quantity(raw, resource)

    def test_time_exact_ns_and_invalid_time_or_window(self):
        self.assertEqual(U.timestamp_ns('2026-09-18T00:00:00.000000001Z') -
                         U.timestamp_ns('2026-09-17T23:59:59.999999999Z'), 2)
        for timestamp in ('2026-02-30T00:00:00Z', '2026-09-18T00:00:00+00:00',
                          '2026-09-18T00:00:00.1234567890Z', None):
            with self.assertRaises(U.PodUsageError): U.stamp(timestamp)
        for window in ('0s', '-1s', '121s', '1m0s', '0.0000000001s', 30):
            value = metrics(); value['window'] = window
            result, _ = self.collect(fetch=Mock(side_effect=[value, pod()]))
            self.assertEqual(result['state'], 'unknown')

    def test_stale_future_prestart_and_clock_rollback_are_unknown(self):
        for timestamp, window, code in [('2026-09-18T01:57:59.999999999Z', '30s', 'SAMPLE_STALE'),
            ('2026-09-18T02:00:05.000000001Z', '30s', 'SAMPLE_STALE'),
            ('2026-09-18T01:00:30Z', '30s', 'SAMPLE_PRECEDES_CONTAINER')]:
            value = metrics(); value.update(timestamp=timestamp, window=window)
            result, _ = self.collect(fetch=Mock(side_effect=[value, pod()]))
            self.assertEqual(result['reason'], code)
        result, _ = self.collect(clock=Mock(side_effect=[OBSERVED, REQUEST]))
        self.assertEqual(result['reason'], 'TIME_INVALID')
        self.assertIsNone(result['request_started_at'])

    def test_supported_microsecond_identity_with_submicrosecond_comparison(self):
        before = pod(); before['status']['containerStatuses'][0]['state']['running']['startedAt'] = '2026-09-18T01:00:00.123456789Z'
        result = U.project(before, metrics(), copy.deepcopy(before), request_started_at=REQUEST, observed_at=OBSERVED)
        self.assertEqual(result['identity']['started_at'], '2026-09-18T01:00:00.123456Z')

    def test_creation_is_before_start_and_never_replaced_by_metrics_creation(self):
        value = pod(); value['metadata']['creationTimestamp'] = '2026-09-18T01:00:00.000000001Z'
        result, fetch = self.collect({'items': [value]})
        self.assertEqual(result['reason'], 'POD_INVALID')
        fetch.assert_not_called()
        value = metrics(); value['metadata']['creationTimestamp'] = SENTINEL
        result, _ = self.collect(fetch=Mock(side_effect=[value, pod()]))
        self.assertEqual(result['state'], 'observed')
        self.assertNotIn(SENTINEL, json.dumps(result))

    def test_unverified_owned_transport_cleanup_is_fatal_and_never_model_evidence(self):
        failure = U.PodUsageCleanupError()
        with self.assertRaises(U.PodUsageCleanupError) as caught:
            self.collect(fetch=Mock(side_effect=failure))
        self.assertIs(caught.exception, failure)
        self.assertEqual(str(caught.exception), 'POD_USAGE_CLEANUP_FAILED')

    def test_errors_are_closed_and_first_interruption_propagates(self):
        for error in (OSError(SENTINEL), U.PodUsageError('READ_TIMEOUT'), U.PodUsageError('READ_LIMIT')):
            result, fetch = self.collect(fetch=Mock(side_effect=error))
            self.assertEqual(result['get_attempts'], 1)
            self.assertIsNone(result['sample'])
            self.assertNotIn(SENTINEL, json.dumps(result))
            self.assertEqual(fetch.call_count, 1)
        cancel = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.collect(fetch=Mock(side_effect=cancel))
        self.assertIs(caught.exception, cancel)

    def test_public_strict_shape_types_and_unknown_sample_rejection(self):
        mutations = [lambda v: v.update(extra=SENTINEL), lambda v: v.update(schema_version=True),
            lambda v: v.update(get_attempts=True), lambda v: v.update(get_attempts=1),
            lambda v: v['sample'].update(cpu_nanocores=0), lambda v: v['sample'].update(window_ns='01'),
            lambda v: v['identity'].update(restart_count=True), lambda v: v.update(reason='READ_FAILED'),
            lambda v: v.update(state='unknown'), lambda v: v['sample'].update(memory_working_set_bytes='-1')]
        for mutate in mutations:
            value = observed(); mutate(value)
            with self.assertRaises(U.PodUsageError): U.validate(value)
        value = unknown(); value['reason'] = []
        with self.assertRaises(U.PodUsageError): U.validate(value)

    def test_core_keeps_outer_time_and_old_container_shape(self):
        backend = G.Backends.__new__(G.Backends)
        backend.command_json = Mock(return_value={'items': [pod()]})
        backend.pod_usage_get = Mock(side_effect=[metrics(), pod()])
        times = ['2026-09-18T01:59:58.500000Z', REQUEST, OBSERVED]
        with patch.object(G, 'observation_time', side_effect=times), \
                patch.object(G.POD_LOGS, 'collect', return_value=G.POD_LOGS.unknown('READ_FAILED')):
            result = backend.k8s_read_pods(U.NAMESPACE, 50)
        self.assertEqual(result['observed_at'], times[0])
        self.assertEqual(result['resource_usage']['request_started_at'], REQUEST)
        self.assertEqual(result['pods'][0]['container_observation'],
                         G.CONTAINER_STATUS.project_container_observation(pod(), timestamp=G.safe_timestamp))
        self.assertEqual(backend.command_json.call_count, 1)
        self.assertNotIn(SENTINEL, json.dumps(result))


class BoundedTransportTests(unittest.TestCase):
    def command(self, chunks, *, error=None, close_error=None, kill_error=None):
        process = Mock()
        process.stdout.fileno.return_value = 99
        process.stdout.close.side_effect = close_error
        process.poll.return_value = None
        process.pid = 1234
        process.wait.return_value = 0
        selector = Mock()
        selector.select.return_value = [True]
        patches = [patch.object(U.subprocess, 'Popen', return_value=process),
                   patch.object(U.selectors, 'DefaultSelector', return_value=selector),
                   patch.object(U.os, 'set_blocking'), patch.object(U.os, 'read', side_effect=error or chunks),
                   patch.object(U.os, 'killpg', side_effect=kill_error), patch.object(U.time, 'monotonic', return_value=0)]
        mocks = [item.start() for item in patches]
        for item in reversed(patches): self.addCleanup(item.stop)
        return process, selector, mocks

    def test_byte_cap_stops_stream_at_limit_plus_one_and_only_owned_group(self):
        process, _, mocks = self.command([b'x' * 4096] * 16 + [b'x'])
        with self.assertRaises(U.PodUsageError) as caught:
            U._command(['get', '--raw', '/fixed'], deadline=8, environment={})
        self.assertEqual(caught.exception.code, 'READ_LIMIT')
        self.assertEqual(mocks[3].call_count, 17)
        self.assertEqual(mocks[3].call_args.args[1], 1)
        mocks[4].assert_called_once_with(1234, signal.SIGKILL)
        self.assertEqual(process.wait.call_args.kwargs['timeout'], 2)
        kwargs = mocks[0].call_args.kwargs
        self.assertTrue(kwargs['start_new_session'])
        self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)

    def test_empty_or_duplicate_nonfinite_or_nonobject_json_fails_closed(self):
        for raw in (b'', b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'{bad}'):
            with self.subTest(raw=raw):
                self.command([raw, b''])
                with self.assertRaises(U.PodUsageError):
                    U._command([], deadline=8, environment={})
                self.doCleanups()

    def test_timeout_no_exit_or_unreadable_body_is_not_success(self):
        process, selector, mocks = self.command([b'{}', b''])
        selector.select.return_value = []
        with self.assertRaises(U.PodUsageError) as caught:
            U._command([], deadline=8, environment={})
        self.assertEqual(caught.exception.code, 'READ_TIMEOUT')
        self.assertEqual(mocks[3].call_count, 0)
        mocks[4].assert_called_once()

    def test_first_cancellation_survives_kill_and_close_errors(self):
        cancel = KeyboardInterrupt()
        process, selector, _ = self.command([], error=cancel, close_error=OSError(SENTINEL), kill_error=OSError(SENTINEL))
        with self.assertRaises(KeyboardInterrupt) as caught:
            U._command([], deadline=8, environment={})
        self.assertIs(caught.exception, cancel)
        selector.close.assert_called_once()
        process.stdout.close.assert_called_once()

    def test_cleanup_first_cancellation_survives_secondary_close(self):
        cancel = KeyboardInterrupt()
        self.command([b'{}', b''], close_error=OSError(SENTINEL), kill_error=cancel)
        with self.assertRaises(KeyboardInterrupt) as caught:
            U._command([], deadline=8, environment={})
        self.assertIs(caught.exception, cancel)

    def test_original_read_error_with_kill_or_reap_failure_is_fatal(self):
        for failure in ('kill', 'wait'):
            with self.subTest(failure=failure):
                process, selector, _ = self.command([], error=U.PodUsageError('READ_LIMIT'),
                    kill_error=OSError(SENTINEL) if failure == 'kill' else None)
                if failure == 'wait': process.wait.side_effect = subprocess.TimeoutExpired('private', 2)
                with self.assertRaises(U.PodUsageCleanupError) as caught:
                    U._command([], deadline=8, environment={})
                self.assertEqual(str(caught.exception), 'POD_USAGE_CLEANUP_FAILED')
                process.wait.assert_called_once_with(timeout=2)
                selector.close.assert_called_once()
                process.stdout.close.assert_called_once()
                self.doCleanups()

    def test_exit_racing_signal_is_accepted_only_after_confirmed_reap(self):
        process, _, mocks = self.command([], error=U.PodUsageError('READ_TIMEOUT'), kill_error=ProcessLookupError())
        with self.assertRaises(U.PodUsageError) as caught:
            U._command([], deadline=8, environment={})
        self.assertEqual(caught.exception.code, 'READ_TIMEOUT')
        process.wait.assert_called_once_with(timeout=2)
        mocks[4].assert_called_once()

    def test_completed_process_is_never_signalled_and_all_handles_close(self):
        process, selector, mocks = self.command([b'{"safe":1}', b''])
        process.poll.return_value = 0
        self.assertEqual(U._command([], deadline=8, environment={}), {'safe': 1})
        mocks[4].assert_not_called()
        selector.close.assert_called_once()
        process.stdout.close.assert_called_once()

    def test_fixed_get_scope_local_guard_and_minimal_environment(self):
        config = {'clusters': [{'cluster': {'server': 'https://127.0.0.1:55737'}}]}
        with patch.object(U, '_command', side_effect=[config, metrics()]) as command, \
             patch.dict(U.os.environ, {'AWS_SECRET_ACCESS_KEY': SENTINEL, 'HTTPS_PROXY': SENTINEL, 'KUBECONFIG': SENTINEL}):
            result = U.fixed_get('metrics', POD)
        self.assertEqual(result, metrics())
        self.assertEqual(command.call_count, 2)
        self.assertEqual(command.call_args_list[0].args[0], ['config', 'view', '--minify', '-o', 'json'])
        self.assertEqual(command.call_args.args[0], ['--server', 'https://127.0.0.1:55737',
            '--request-timeout=6s', 'get', '--raw', f'/apis/metrics.k8s.io/v1beta1/namespaces/{U.NAMESPACE}/pods/{POD}'])
        self.assertNotIn(SENTINEL, json.dumps(command.call_args.kwargs['environment']))
        self.assertEqual(command.call_args_list[0].kwargs['deadline'], command.call_args.kwargs['deadline'])

    def test_nonlocal_or_injected_target_never_gets(self):
        for server in ('https://evil.example', 'http://127.0.0.1', 'https://user@localhost', 'https://localhost/path'):
            with patch.object(U, '_command', return_value={'clusters': [{'cluster': {'server': server}}]}) as command:
                with self.assertRaises(U.PodUsageError): U.fixed_get('metrics', POD)
                self.assertEqual(command.call_count, 1)
        with patch.object(U, '_command') as command:
            for operation, target in [('other', POD), ('pod', '../secret'), ('pod', 'a\nsecret')]:
                with self.assertRaises(U.PodUsageError): U.fixed_get(operation, target)
            command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
