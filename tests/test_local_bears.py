"""Behavioral checks for the bounded, deterministic SRE diagnostic scenario."""
import copy
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'tools/local_experiment/bears/diagnostics.py'
SPEC = importlib.util.spec_from_file_location('bear_diagnostics', PATH)
diagnostics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostics)


def observations():
    return ({'profile': 'local-experiment', 'context': 'colima-mac-studio-solo',
             'checks': [{'name': 'storage', 'status': 'healthy', 'summary': 'ready'},
                        {'name': 'flux', 'status': 'informational', 'summary':
                         '4 controllers and 3 other reconciled resources are Ready; root '
                         'Kustomization/flux-system/flux-system is intentionally suspended for local-experiment'}]},
            {'namespace': 'shirokuma-dev', 'truncated': False, 'pods': [
                {'name': 'seaweedfs-0', 'phase': 'Running', 'ready_containers': 1,
                 'total_containers': 1, 'restarts': 0}]},
            {'namespace': 'shirokuma-dev', 'events': []})


class DiagnosticTests(unittest.TestCase):
    def test_healthy_and_intentional_flux_are_separate(self):
        result = diagnostics.diagnose(*observations())
        self.assertEqual(result['health'], 'healthy-observed-scope')
        self.assertTrue(result['intentional_flux_suspension'])
        self.assertEqual([x['code'] for x in result['findings']], ['INTENTIONAL_FLUX_SUSPENSION'])
        self.assertEqual(result['actions_executed'], [])
        self.assertIs(result['llm_inference'], False)

    def test_failed_pending_and_unready_workloads_require_attention(self):
        for phase, ready, expected in [('Failed', 0, 'WORKLOAD_FAILED'),
                                       ('Pending', 0, 'WORKLOAD_UNAVAILABLE'),
                                       ('Running', 0, 'WORKLOAD_NOT_READY')]:
            with self.subTest(phase=phase):
                data = observations()
                data[1]['pods'][0].update(phase=phase, ready_containers=ready)
                result = diagnostics.diagnose(*data)
                self.assertEqual(result['health'], 'attention-required')
                finding = next(x for x in result['findings'] if x['code'] == expected)
                self.assertEqual(finding['severity'], 'critical')

    def test_ready_restart_is_historical_not_current_outage(self):
        data = observations()
        data[1]['pods'][0]['restarts'] = 2
        result = diagnostics.diagnose(*data)
        finding = next(x for x in result['findings'] if x['code'] == 'WORKLOAD_RESTARTED')
        self.assertEqual(finding['severity'], 'warning')
        self.assertIn('does not prove a current outage', finding['detail'])
        self.assertFalse(any(x['severity'] == 'critical' for x in result['findings']))

    def test_successful_job_with_zero_ready_is_normal(self):
        data = observations()
        data[1]['pods'][0].update(phase='Succeeded', ready_containers=0)
        self.assertEqual(diagnostics.diagnose(*data)['health'], 'healthy-observed-scope')

    def test_unexpected_flux_suspension_is_not_exempted(self):
        data = observations()
        data[0]['checks'][1].update(status='degraded', summary='2 Flux resources are suspended or not Ready')
        result = diagnostics.diagnose(*data)
        self.assertFalse(result['intentional_flux_suspension'])
        self.assertEqual(result['health'], 'attention-required')
        self.assertIn('CHECK_DEGRADED', [x['code'] for x in result['findings']])

    def test_missing_or_truncated_observation_cannot_claim_healthy(self):
        for change in ({'pods': []}, {'truncated': True}):
            data = observations()
            data[1].update(change)
            self.assertEqual(diagnostics.diagnose(*data)['health'], 'attention-required')

    def test_scope_mismatch_rejected(self):
        for target, field, value in [(0, 'context', 'production'), (0, 'profile', 'mac-studio-solo'),
                                      (1, 'namespace', 'default'), (2, 'namespace', 'default')]:
            data = observations()
            data[target][field] = value
            with self.assertRaises(diagnostics.DiagnosticError):
                diagnostics.diagnose(*data)

    def test_invalid_pod_counts_rejected(self):
        for key, value in [('restarts', -1), ('restarts', True), ('ready_containers', 2),
                           ('total_containers', '1')]:
            data = observations()
            data[1]['pods'][0][key] = value
            with self.assertRaises(diagnostics.DiagnosticError):
                diagnostics.diagnose(*data)

    def test_warning_events_omit_untrusted_free_text(self):
        data = observations()
        data[2]['events'].append({'type': 'Warning', 'reason': 'BackOff', 'object_kind': 'Pod',
                                  'object_name': 'example-0', 'message': 'PRIVATE RAW TEXT'})
        result = diagnostics.diagnose(*data)
        self.assertEqual(result['health'], 'attention-required')
        self.assertNotIn('PRIVATE RAW TEXT', str(result))
        self.assertIn('WARNING_EVENT', [x['code'] for x in result['findings']])


class CorrelationTests(unittest.TestCase):
    def setUp(self):
        self.calls = [{'event_id': str(i), 'span_id': str(i), 'tool': name} for i, name in
                      enumerate(['shirokuma_doctor', 'k8s_read_pods', 'k8s_read_events'])]
        self.kwargs = dict(event_id='own', span_id='own', task_id='task', trace_id='trace')
        self.rows = [{'event_id': value, 'task_id': 'task', 'trace_id': 'trace', 'status': 'success',
                      'prompt_tokens': None, 'completion_tokens': None, 'inference_cost_estimate': None}
                     for value in ['0', '1', '2', 'own']]
        self.logs = [{'task_id': 'task', 'trace_id': 'trace', 'span_id': call['span_id'],
                      'action': 'mcp.' + call['tool'], 'status': 'success'} for call in self.calls]
        self.logs.append({'task_id': 'task', 'trace_id': 'trace', 'span_id': 'own',
                          'action': 'bear.sre.diagnose', 'status': 'success'})

    def verify(self, rows=None, logs=None):
        return diagnostics.verify_correlation(self.rows if rows is None else rows,
                                              self.logs if logs is None else logs,
                                              self.calls, **self.kwargs)

    def test_full_audit_chain_required(self):
        self.assertTrue(self.verify())
        self.assertFalse(self.verify(rows=self.rows[:-1]))
        self.assertFalse(self.verify(logs=self.logs[:-1]))

    def test_wrong_trace_status_action_or_usage_rejected(self):
        for field, value in [('trace_id', 'other'), ('status', 'failed'), ('prompt_tokens', 0),
                              ('event_id', 'other')]:
            rows = copy.deepcopy(self.rows)
            rows[0][field] = value
            self.assertFalse(self.verify(rows=rows))
        for field, value in [('trace_id', 'other'), ('status', 'failed'), ('action', 'other')]:
            logs = copy.deepcopy(self.logs)
            logs[0][field] = value
            self.assertFalse(self.verify(logs=logs))


if __name__ == '__main__':
    unittest.main()
