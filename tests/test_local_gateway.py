from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    'local_gateway', Path(__file__).resolve().parents[1] / 'tools/local_experiment/gateway/core.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
WORKER_SPEC = importlib.util.spec_from_file_location(
    'local_platform_worker', Path(__file__).resolve().parents[1]
    / 'tools/local_experiment/gateway/platform_worker.py')
WORKER = importlib.util.module_from_spec(WORKER_SPEC)
WORKER_SPEC.loader.exec_module(WORKER)


def platform_report():
    return {'profile': 'local-experiment', 'observed_at': '2026-09-17T12:00:00+09:00',
            'ready': True, 'scope': 'private-free-text', 'components': {
                name: {'ready': True, 'status': 'ready', 'reason': 'private-component-reason'}
                for name in MODULE.PLATFORM_COMPONENTS}}


class GatewayTests(unittest.TestCase):
    def test_platform_projection_retains_only_fixed_readiness_facts(self):
        result = MODULE.platform_projection(platform_report())
        self.assertEqual(set(result), {'profile', 'observed_at', 'ready', 'components'})
        self.assertEqual(result['observed_at'], '2026-09-17T03:00:00.000000Z')
        self.assertEqual(set(result['components']), set(MODULE.PLATFORM_COMPONENTS))
        self.assertTrue(result['ready'])
        for value in result['components'].values():
            self.assertEqual(value, {'ready': True, 'status': 'ready'})
        self.assertNotIn('private-', json.dumps(result))

    def test_platform_projection_preserves_not_ready_and_unknown(self):
        for status in ('not-ready', 'unknown'):
            report = platform_report()
            report['ready'] = False
            report['components']['superset'] = {'ready': False, 'status': status}
            result = MODULE.platform_projection(report)
            self.assertFalse(result['ready'])
            self.assertEqual(result['components']['superset'], {'ready': False, 'status': status})

    def test_platform_projection_refuses_malformed_or_inconsistent_reports(self):
        cases = [None, [], {}, {**platform_report(), 'profile': 'remote'},
                 {**platform_report(), 'observed_at': None},
                 {**platform_report(), 'observed_at': '2026-09-17T03:00:00+05:60'},
                 {**platform_report(), 'observed_at': '2026-09-17T03:00:00'},
                 {**platform_report(), 'ready': 1}, {**platform_report(), 'ready': False},
                 {**platform_report(), 'unexpected': 'private'}]
        for value in (None, [], {}, {'ready': 'true', 'status': 'ready'},
                      {'ready': True, 'status': 'unknown'}, {'ready': False, 'status': 'ready'},
                      {'ready': False, 'status': 'other'}, {'ready': False, 'status': []},
                      {'ready': True, 'status': 'ready', 'unexpected': 'private'}):
            report = platform_report()
            report['components']['superset'] = value
            cases.append(report)
        for name in ('missing', 'extra'):
            report = platform_report()
            if name == 'missing':
                del report['components']['loki']
            else:
                report['components']['unknown-group'] = {'ready': True, 'status': 'ready'}
            cases.append(report)
        for report in cases:
            with self.subTest(report=report):
                with self.assertRaises(MODULE.BackendError):
                    MODULE.platform_projection(report)

    def test_platform_command_is_fixed_scoped_and_has_minimal_environment(self):
        backend = MODULE.Backends.__new__(MODULE.Backends)
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps(platform_report()), '')
        environment = {'PATH': '/fixed/bin', 'HOME': '/fixed/home', 'USER': 'local', 'LANG': 'C',
                       'TMPDIR': '/tmp', 'LC_ALL': 'C', 'KUBECONFIG': 'private-override',
                       'DOCKER_HOST': 'remote', 'HTTP_PROXY': 'private-proxy',
                       'API_KEY': 'private-credential', 'PYTHONPATH': 'private-injection'}
        with patch.object(MODULE.subprocess, 'Popen', return_value=process) as popen, patch.dict(MODULE.os.environ, environment, clear=True):
            backend.local_platform_status()
        popen.assert_called_once_with(
            ['rtk', 'proxy', MODULE.sys.executable, str(MODULE.HERE / 'platform_worker.py')],
            cwd=MODULE.ROOT, stdout=MODULE.subprocess.PIPE, stderr=MODULE.subprocess.PIPE,
            text=True, start_new_session=True, env={key: environment[key]
                for key in ('PATH', 'HOME', 'USER', 'TMPDIR', 'LANG', 'LC_ALL')})
        process.communicate.assert_called_once_with(timeout=75)

    def test_platform_scope_failure_prevents_status_read_inside_worker(self):
        scope = Mock()
        scope.check_context.side_effect = RuntimeError('private invalid scope')
        with patch.object(WORKER, 'load_module', return_value=scope) as load, patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(WORKER.main(), 2)
        load.assert_called_once_with('gateway_platform_scope', WORKER.LOCAL / 'local_experiment.py')
        self.assertEqual(json.loads(output.getvalue()), {'error_code': 'LOCAL_PLATFORM_SCOPE_OR_STATUS_FAILED'})
        self.assertNotIn('private', output.getvalue())

    def test_platform_worker_runs_status_only_after_scope_check(self):
        scope, status = Mock(), Mock()
        status.main.return_value = 1
        calls = []
        scope.check_context.side_effect = lambda: calls.append('scope')
        status.main.side_effect = lambda: calls.append('status') or 1
        with patch.object(WORKER, 'load_module', side_effect=[scope, status]):
            self.assertEqual(WORKER.main(), 1)
        self.assertEqual(calls, ['scope', 'status'])

    def test_platform_timeout_kills_only_owned_process_group_and_reaps(self):
        backend = MODULE.Backends.__new__(MODULE.Backends)
        process = Mock(pid=54321)
        process.communicate.side_effect = [subprocess.TimeoutExpired('fixed worker', 75), ('', '')]
        with patch.object(MODULE.subprocess, 'Popen', return_value=process), patch.object(MODULE.os, 'killpg') as kill:
            with self.assertRaisesRegex(MODULE.BackendError, 'timed out'):
                backend.local_platform_status()
        kill.assert_called_once_with(54321, MODULE.signal.SIGKILL)
        self.assertEqual([call.kwargs for call in process.communicate.call_args_list], [{'timeout': 75}, {'timeout': 2}])

    def test_platform_cleanup_race_or_stuck_pipe_does_not_extend_deadline(self):
        process = Mock(pid=54321)
        process.communicate.side_effect = subprocess.TimeoutExpired('fixed worker', 2)
        with patch.object(MODULE.os, 'killpg', side_effect=ProcessLookupError):
            MODULE.stop_platform_worker(process)
        process.communicate.assert_called_once_with(timeout=2)
        process.stdout.close.assert_called_once_with()
        process.stderr.close.assert_called_once_with()
        process.poll.assert_called_once_with()

    def test_platform_timeout_closes_pipes_held_by_real_worker_descendant(self):
        # A surviving descendant keeps stdout open after its parent dies. This
        # exercises actual process-group cleanup without waiting 75 seconds or
        # starting any lab service.
        backend = MODULE.Backends.__new__(MODULE.Backends)
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'child-started'
            code = ('import subprocess,sys,time; from pathlib import Path; '
                    'subprocess.Popen([sys.executable,"-c","import time; time.sleep(20)"]); '
                    f'Path({str(marker)!r}).write_text("started"); time.sleep(20)')
            process = subprocess.Popen(['rtk', 'proxy', MODULE.sys.executable, '-c', code],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, start_new_session=True)
            communicate = process.communicate
            expired = []
            def short_initial_deadline(*, timeout):
                try:
                    return communicate(timeout=0.01 if timeout == 75 else timeout)
                except subprocess.TimeoutExpired:
                    expired.append(timeout)
                    raise
            try:
                deadline = time.monotonic() + 3
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(marker.exists(), 'isolated fixture child did not start')
                with patch.object(MODULE.subprocess, 'Popen', return_value=process), patch.object(process, 'communicate', side_effect=short_initial_deadline):
                    with self.assertRaisesRegex(MODULE.BackendError, 'timed out'):
                        backend.local_platform_status()
                self.assertEqual(expired, [75], 'descendant kept pipes open during cleanup')
                self.assertIsNotNone(process.returncode)
            finally:
                try:
                    os.killpg(process.pid, MODULE.signal.SIGKILL)
                except ProcessLookupError:
                    pass
                communicate(timeout=2)

    def test_platform_scope_worker_failure_is_not_an_unknown_successful_read(self):
        backend = MODULE.Backends.__new__(MODULE.Backends)
        process = Mock(returncode=2)
        process.communicate.return_value = ('{"error_code":"LOCAL_PLATFORM_SCOPE_OR_STATUS_FAILED"}', 'private')
        with patch.object(MODULE.subprocess, 'Popen', return_value=process):
            with self.assertRaises(MODULE.BackendError) as error:
                backend.local_platform_status()
        self.assertNotIn('private', str(error.exception))

    def test_non_ready_platform_exit_one_is_successful_audited_read(self):
        report = platform_report()
        report['ready'] = False
        report['components']['superset'] = {'ready': False, 'status': 'unknown'}
        backend = MODULE.Backends.__new__(MODULE.Backends)
        dispatcher, _, events = self.dispatcher()
        dispatcher.backends = backend
        process = Mock(returncode=1)
        process.communicate.return_value = (json.dumps(report), 'private-backend-error')
        with patch.object(MODULE.subprocess, 'Popen', return_value=process):
            result = dispatcher.call('local_platform_status', {})
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['audit'], 'delivered')
        self.assertFalse(result['data']['ready'])
        self.assertEqual(result['data']['components']['superset']['status'], 'unknown')
        self.assertEqual(events[0][0]['action_type'], 'mcp.local_platform_status')
        self.assertEqual(events[0][0]['status'], 'success')
        self.assertNotIn('private-', json.dumps([result, events]))

    def test_platform_arguments_are_rejected_before_backend_and_audited(self):
        dispatcher, backend, events = self.dispatcher()
        result = dispatcher.call('local_platform_status', {'url': 'private-alternate-url'})
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(events[0][0]['action_type'], 'mcp.local_platform_status')
        backend.execute.assert_not_called()
        self.assertNotIn('private-', json.dumps([result, events]))

    def test_changed_kubernetes_context_prevents_backend_request(self):
        backend = MODULE.Backends.__new__(MODULE.Backends)
        scope = Mock()
        scope.check_context.side_effect = RuntimeError('remote endpoint')
        with patch.object(MODULE, 'load_module', return_value=scope), patch.object(MODULE.subprocess, 'run') as run:
            with self.assertRaises(RuntimeError):
                backend.command_json(['kubectl', '--context', MODULE.CONTEXT, 'get', 'pods'])
            run.assert_not_called()

    def dispatcher(self, failure=None):
        backend = Mock()
        backend.execute.return_value = {'ok': True}
        if failure:
            backend.execute.side_effect = failure
        events = []
        def audit(event, span_id):
            events.append((event.copy(), span_id))
            return '11111111-1111-1111-1111-111111111111'
        return MODULE.Dispatcher(backend, audit), backend, events

    def test_valid_read_is_audited_with_correlated_standard_trace_identifiers(self):
        dispatcher, backend, events = self.dispatcher()
        result = dispatcher.call('k8s_read_pods', {})
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['audit'], 'delivered')
        backend.execute.assert_called_once_with('k8s_read_pods', {'namespace': 'shirokuma-dev', 'limit': 20})
        event, span_id = events[0]
        self.assertEqual(event['trace_id'], result['trace_id'])
        self.assertEqual(span_id, result['span_id'])
        self.assertRegex(result['trace_id'], r'^[0-9a-f]{32}$')
        self.assertRegex(result['span_id'], r'^[0-9a-f]{16}$')
        self.assertEqual(event['task_id'], result['task_id'])
        self.assertGreaterEqual(event['duration_ms'], 0)

    def test_invalid_namespace_is_denied_and_audited_without_backend_call(self):
        dispatcher, backend, events = self.dispatcher()
        result = dispatcher.call('k8s_read_pods', {'namespace': 'kube-system'})
        self.assertEqual(result['error_code'], 'POLICY_DENIED')
        self.assertEqual(result['status'], 'blocked')
        self.assertTrue(events[0][0]['blocked_by_policy'])
        backend.execute.assert_not_called()

    def test_arbitrary_sql_and_extra_secret_argument_are_never_retained(self):
        dispatcher, backend, events = self.dispatcher()
        secret = 'sensitive-untrusted-input'
        result = dispatcher.call('trino_explain_fixture', {'sql': 'DROP TABLE x', 'password': secret})
        self.assertEqual(result['status'], 'blocked')
        backend.execute.assert_not_called()
        self.assertNotIn(secret, json.dumps([result, events]))
        self.assertNotIn('DROP TABLE', json.dumps([result, events]))

    def test_unknown_tool_name_is_normalized_in_audit(self):
        dispatcher, backend, events = self.dispatcher()
        result = dispatcher.call('secret-name-that-must-not-be-recorded', {})
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(events[0][0]['action_type'], 'mcp.unknown_tool')
        self.assertNotIn('secret-name', json.dumps([result, events]))
        backend.execute.assert_not_called()

    def test_integer_bounds_reject_bool_float_and_sql_fragments(self):
        for value in (True, 2.0, 0, 51, "2; DROP TABLE x"):
            with self.assertRaises(MODULE.PolicyDenied):
                MODULE.validate('k8s_read_events', {'limit': value})
        for value in (0, 21, -1):
            with self.assertRaises(MODULE.PolicyDenied):
                MODULE.validate('agenthouse_recent', {'limit': value})

    def test_non_object_arguments_are_policy_denials(self):
        dispatcher, backend, events = self.dispatcher()
        result = dispatcher.call('gateway_policy', ['unexpected'])
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(len(events), 1)
        backend.execute.assert_not_called()

    def test_backend_error_does_not_claim_success_or_leak_error_text(self):
        dispatcher, backend, events = self.dispatcher(RuntimeError('credential=secret-detail'))
        result = dispatcher.call('openmetadata_get_lineage', {})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'BACKEND_FAILED')
        self.assertEqual(events[0][0]['status'], 'failed')
        self.assertNotIn('credential=', json.dumps([result, events]))

    def test_audit_delivery_failure_is_fail_closed_even_after_successful_read(self):
        dispatcher, backend, _ = self.dispatcher()
        dispatcher.audit = Mock(side_effect=RuntimeError('unavailable'))
        result = dispatcher.call('gateway_policy', {})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['backend_status'], 'success')
        self.assertEqual(result['error_code'], 'AUDIT_DELIVERY_FAILED')
        self.assertNotIn('data', result)
        backend.execute.assert_called_once()

    def test_audit_failure_does_not_turn_policy_denial_into_success(self):
        dispatcher, backend, _ = self.dispatcher()
        dispatcher.audit = Mock(side_effect=RuntimeError('unavailable'))
        result = dispatcher.call('github_issue_write', {})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['backend_status'], 'blocked')
        backend.execute.assert_not_called()

    def test_pod_output_omits_specs_environment_and_container_messages(self):
        backend = MODULE.Backends.__new__(MODULE.Backends)
        backend.command_json = Mock(return_value={'items': [{
            'metadata': {'name': 'safe-pod', 'annotations': {'secret': 'hidden'}},
            'spec': {'containers': [{'env': [{'name': 'PASSWORD', 'value': 'hidden'}]}]},
            'status': {'phase': 'Running', 'containerStatuses': [{'ready': True, 'restartCount': 2}]}}]})
        result = backend.k8s_read_pods('shirokuma-dev', 5)
        self.assertEqual(result['pods'][0]['ready_containers'], 1)
        self.assertNotIn('hidden', json.dumps(result))
        self.assertNotIn('PASSWORD', json.dumps(result))

    def test_events_exclude_secrets_and_free_text(self):
        backend = MODULE.Backends.__new__(MODULE.Backends)
        backend.command_json = Mock(return_value={'items': [
            {'involvedObject': {'kind': 'Secret', 'name': 'private'}, 'message': 'credential'},
            {'involvedObject': {'kind': 'Pod', 'name': 'pod-1'}, 'reason': 'Started',
             'type': 'Normal', 'message': 'token=hidden', 'count': 2}]})
        result = backend.k8s_read_events('shirokuma-dev', 5)
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(result['events'][0]['reason'], 'Started')
        self.assertNotIn('hidden', json.dumps(result))
        self.assertNotIn('private', json.dumps(result))

    def test_pod_identity_and_collection_time_distinguish_recreated_pods(self):
        backend = MODULE.Backends.__new__(MODULE.Backends)
        backend.command_json = Mock(return_value={'items': [
            {'metadata': {'name': 'same-name', 'uid': 'pod-before'}, 'status': {'phase': 'Running'}},
            {'metadata': {'name': 'same-name', 'uid': 'pod-after'}, 'status': {'phase': 'Pending'}},
            {'metadata': {'name': 'unknown-identity'}, 'status': {}},
        ]})
        timestamp = '2026-09-17T03:04:05.123456Z'
        with patch.object(MODULE, 'observation_time', return_value=timestamp):
            result = backend.k8s_read_pods(MODULE.NAMESPACE, 3)
        self.assertEqual(result['observed_at'], timestamp)
        self.assertEqual([pod['uid'] for pod in result['pods']], ['pod-before', 'pod-after', None])
        self.assertFalse(result['truncated'])
        self.assertTrue(backend.k8s_read_pods(MODULE.NAMESPACE, 2)['truncated'])
        backend.command_json.assert_called_with(['kubectl', '--context', MODULE.CONTEXT, '-n', MODULE.NAMESPACE,
                                                 'get', 'pods', '--chunk-size=50', '-o', 'json'])

    def test_uid_is_opaque_bounded_and_unknown_is_not_an_identity(self):
        self.assertEqual(MODULE.safe_uid('pod-opaque_123'), 'pod-opaque_123')
        for value in (None, '', 'private\nvalue', 'x' * 129, 'spaces are not identifiers', 1, True):
            with self.subTest(value=value):
                self.assertIsNone(MODULE.safe_uid(value))

    def test_event_last_occurrence_prefers_series_then_last_timestamp(self):
        value = {'series': {'lastObservedTime': '2026-09-17T03:04:05.123456Z'},
                 'lastTimestamp': '2026-09-17T01:00:00Z', 'eventTime': '2026-09-16T01:00:00Z',
                 'metadata': {'creationTimestamp': '2026-09-17T05:00:00Z'}}
        self.assertEqual(MODULE.event_last_observed_at(value), '2026-09-17T03:04:05.123456Z')
        value['series']['lastObservedTime'] = 'invalid-private-value'
        self.assertEqual(MODULE.event_last_observed_at(value), '2026-09-17T01:00:00.000000Z')
        value['lastTimestamp'] = None
        self.assertIsNone(MODULE.event_last_observed_at(value))

    def test_first_observed_event_time_is_last_only_for_singleton(self):
        first = '2026-09-17T12:00:00+09:00'
        self.assertEqual(MODULE.event_last_observed_at({'eventTime': first}), '2026-09-17T03:00:00.000000Z')
        self.assertEqual(MODULE.event_last_observed_at({'eventTime': first, 'count': 1}),
                         '2026-09-17T03:00:00.000000Z')
        for extra in ({'count': 2}, {'count': True}, {'count': '1'}, {'count': None},
                      {'series': {'count': 5}}, {'series': {}}):
            with self.subTest(extra=extra):
                self.assertIsNone(MODULE.event_last_observed_at({'eventTime': first, **extra}))
        self.assertIsNone(MODULE.event_last_observed_at({'metadata': {'creationTimestamp': first}}))
        self.assertIsNone(MODULE.event_last_observed_at({'firstTimestamp': first}))

    def test_timestamps_reject_missing_invalid_and_free_text_values(self):
        for value in (None, '', 123, 'private-value', '2026-09-17', '2026-09-17T03:00:00',
                      '2026-02-30T03:00:00Z', '2026-09-17T25:00:00Z', '2026-09-17T03:00:00+25:00',
                      '2026-09-17T03:00:00Z\nprivate', '2026-09-17T03:00:00.' + '1' * 100 + 'Z'):
            with self.subTest(value=value):
                self.assertIsNone(MODULE.safe_timestamp(value))
        self.assertEqual(MODULE.safe_timestamp('2026-09-17T03:00:00.1Z'), '2026-09-17T03:00:00.100000Z')
        self.assertRegex(MODULE.observation_time(), r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$')

    def test_event_selection_uses_last_occurrence_and_truncates_after_secret_filter(self):
        backend = MODULE.Backends.__new__(MODULE.Backends)
        secret = {'metadata': {'uid': 'private-secret-event'}, 'involvedObject': {'kind': 'Secret'},
                  'lastTimestamp': '2026-09-17T10:00:00Z', 'message': 'private-secret-message'}
        recent_recurrence = {'metadata': {'uid': 'recent-event', 'creationTimestamp': '2026-09-01T01:00:00Z'},
                            'involvedObject': {'kind': 'Pod', 'name': 'pod-1', 'uid': 'current-pod'},
                            'lastTimestamp': '2026-09-17T03:00:00Z', 'count': 5,
                            'reason': 'BackOff', 'type': 'Warning', 'message': 'private-free-text'}
        new_old_occurrence = {'metadata': {'uid': 'older-event', 'creationTimestamp': '2026-09-17T04:00:00Z'},
                             'involvedObject': {'kind': 'Pod', 'name': 'pod-1', 'uid': 'previous-pod'},
                             'lastTimestamp': '2026-09-17T02:00:00Z', 'reason': 'Started', 'type': 'Normal'}
        unknown = {'metadata': {'creationTimestamp': '2026-09-17T06:00:00Z'},
                   'involvedObject': {'kind': 'Pod', 'name': 'pod-2'}}
        backend.command_json = Mock(return_value={'items': [secret, unknown, new_old_occurrence, recent_recurrence]})
        timestamp = '2026-09-17T05:00:00.000000Z'
        with patch.object(MODULE, 'observation_time', return_value=timestamp):
            result = backend.k8s_read_events(MODULE.NAMESPACE, 1)
        self.assertEqual(result['observed_at'], timestamp)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['events'][0]['uid'], 'recent-event')
        self.assertEqual(result['events'][0]['object_uid'], 'current-pod')
        self.assertEqual(result['events'][0]['last_observed_at'], '2026-09-17T03:00:00.000000Z')
        self.assertNotIn('private-', json.dumps(result))
        all_visible = backend.k8s_read_events(MODULE.NAMESPACE, 3)
        self.assertFalse(all_visible['truncated'])
        self.assertEqual([event['uid'] for event in all_visible['events']], ['recent-event', 'older-event', None])
        self.assertIsNone(all_visible['events'][-1]['last_observed_at'])
        self.assertIsNone(all_visible['events'][-1]['object_uid'])
        backend.command_json.assert_called_with(['kubectl', '--context', MODULE.CONTEXT, '-n', MODULE.NAMESPACE,
                                                 'get', 'events', '--chunk-size=50', '-o', 'json'])

    def test_secret_only_events_are_empty_without_false_truncation(self):
        backend = MODULE.Backends.__new__(MODULE.Backends)
        backend.command_json = Mock(return_value={'items': [
            {'involvedObject': {'kind': 'Secret', 'name': 'private'}} for _ in range(3)]})
        result = backend.k8s_read_events(MODULE.NAMESPACE, 1)
        self.assertEqual(result['events'], [])
        self.assertFalse(result['truncated'])
        self.assertEqual(result['free_text_messages'], 'omitted')


if __name__ == '__main__':
    unittest.main()
