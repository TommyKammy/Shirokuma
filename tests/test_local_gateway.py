from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    'local_gateway', Path(__file__).resolve().parents[1] / 'tools/local_experiment/gateway/core.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GatewayTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
