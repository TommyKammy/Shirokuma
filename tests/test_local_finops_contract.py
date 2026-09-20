from copy import deepcopy
import unittest

from tools.local_experiment.finops import contract as C

NOW = '2026-09-20T00:00:21.000Z'


def pod(component='trino'):
    namespace, label, container = C.COMPONENTS[component]
    return {'metadata': {'namespace': namespace, 'name': label + '-abc12',
                'uid': '11111111-1111-4111-8111-111111111111', 'creationTimestamp': '2026-09-19T23:59:00Z',
                'labels': {'app.kubernetes.io/name': label,
                           **({'app.kubernetes.io/component': 'catalog-server'} if component == 'polaris' else {})}},
            'spec': {'containers': [{'name': container}]},
            'status': {'phase': 'Running', 'containerStatuses': [{'name': container, 'restartCount': 0,
                'containerID': 'docker://' + 'a'*64, 'state': {'running': {'startedAt': '2026-09-20T00:00:00Z'}}}]}}


def metrics(component='trino'):
    value = pod(component)
    return {'kind': 'PodMetrics', 'apiVersion': 'metrics.k8s.io/v1beta1',
            'metadata': {key: value['metadata'][key] for key in ('namespace', 'name')},
            'timestamp': '2026-09-20T00:00:20Z', 'window': '10s',
            'containers': [{'name': C.COMPONENTS[component][2], 'usage': {'cpu': '123456789n', 'memory': '2Gi'}}]}


def component_args(component='trino'):
    return {'before': pod(component), 'metrics': metrics(component), 'after': pod(component),
            'request_started_at': '2026-09-20T00:00:20Z', 'observed_at': NOW}


def row(events='2', known='1'):
    return {'model_provider': 'ollama', 'model_name': 'qwen3:8b', 'events': events,
            **{key+'_known': events if key == 'duration_ms' else known for key in C.METRICS},
            **{key+'_sum': ('30' if key in C.INTEGER_METRICS else 0.5) if known != '0' or key == 'duration_ms'
               else None for key in C.METRICS}}


class ContractTests(unittest.TestCase):
    def test_three_fixed_components_keep_exact_cpu_and_memory_and_identity(self):
        for component in C.COMPONENTS:
            value = C.project_component(component, **component_args(component))
            self.assertEqual(value['sample']['cpu_nanocores'], '123456789')
            self.assertEqual(value['sample']['memory_working_set_bytes'], '2147483648')
            self.assertEqual(value['identity']['namespace'], C.COMPONENTS[component][0])
            self.assertEqual(C.validate_component(value), value)

    def test_zero_observation_and_unknown_are_distinct(self):
        args = component_args(); args['metrics']['containers'][0]['usage'] = {'cpu': '0', 'memory': '0'}
        observed = C.project_component('trino', **args)
        self.assertEqual(observed['sample']['cpu_nanocores'], '0')
        unknown = C.unknown_component('trino', 'READ_FAILED')
        self.assertIsNone(unknown['sample']); self.assertEqual(unknown['state'], 'unknown')

    def test_polaris_foreign_or_missing_component_rejected_in_both_windows(self):
        for phase in ('before', 'after'):
            for component in ('catalog-bootstrap', None):
                args = component_args('polaris')
                labels = args[phase]['metadata']['labels']
                if component is None: del labels['app.kubernetes.io/component']
                else: labels['app.kubernetes.io/component'] = component
                with self.subTest(phase=phase, component=component), self.assertRaisesRegex(C.FinOpsError, 'SCOPE_INVALID'):
                    C.project_component('polaris', **args)

    def test_drift_rejects_restart_uid_container_start_or_id(self):
        mutations = [lambda p: p['metadata'].update(uid='22222222-2222-4222-8222-222222222222'),
                     lambda p: p['status']['containerStatuses'][0].update(restartCount=1),
                     lambda p: p['status']['containerStatuses'][0].update(containerID='docker://'+'b'*64),
                     lambda p: p['status']['containerStatuses'][0]['state']['running'].update(startedAt='2026-09-20T00:00:01Z')]
        for mutate in mutations:
            args = component_args(); mutate(args['after'])
            with self.subTest(mutate=mutate), self.assertRaisesRegex(C.FinOpsError, 'IDENTITY_CHANGED'):
                C.project_component('trino', **args)

    def test_scope_metrics_name_and_sidecar_mismatch_rejected(self):
        for change in ('scope', 'metrics', 'sidecar'):
            args = component_args()
            if change == 'scope': args['before']['metadata']['namespace'] = 'other-project'
            elif change == 'metrics': args['metrics']['metadata']['name'] = 'another-pod'
            else: args['before']['spec']['containers'].append({'name': 'sidecar'})
            with self.subTest(change=change), self.assertRaises(C.FinOpsError): C.project_component('trino', **args)

    def test_stale_future_prestart_and_non_integral_cpu_rejected(self):
        for mutation in ('stale', 'future', 'prestart', 'quantity', 'longrequest'):
            args = component_args()
            if mutation == 'stale': args['observed_at'] = '2026-09-20T00:03:00Z'; args['request_started_at'] = args['observed_at']
            if mutation == 'future': args['metrics']['timestamp'] = '2026-09-20T00:00:27Z'
            if mutation == 'prestart': args['metrics']['window'] = '20s'
            if mutation == 'quantity': args['metrics']['containers'][0]['usage']['cpu'] = '0.0000000001'
            if mutation == 'longrequest': args['observed_at'] = '2026-09-20T00:00:41Z'
            with self.subTest(mutation=mutation), self.assertRaises(C.FinOpsError): C.project_component('trino', **args)

    def test_exact_disk_boundaries_without_float_comparison(self):
        for free, expected in ((26,'normal'), (25,'warning'), (21,'warning'), (20,'low'), (16,'low'), (15,'critical'), (0,'critical')):
            self.assertEqual(C.disk('host', 100, free, observed_at=NOW)['severity'], expected)
        value = C.disk('host', 10**18, 250000000000000001, observed_at=NOW)
        self.assertEqual(value['severity'], 'normal')  # rounded display is not the threshold input
        self.assertEqual(C.disk('colima', observed_at=NOW, reason='READ_FAILED')['severity'], 'unknown')

    def test_invalid_disk_bytes_bool_or_free_over_total_rejected(self):
        for total, free in ((True, 0), (0, 0), (100, 101), (100, -1)):
            with self.subTest(total=total, free=free), self.assertRaises(C.FinOpsError): C.disk('host', total, free, observed_at=NOW)

    def test_inference_known_subtotal_does_not_become_unknown_total_or_currency(self):
        value = C.inference([row()], end=NOW)
        group = value['groups'][0]
        self.assertEqual(group['prompt_tokens']['known_sum'], '30')
        self.assertIsNone(group['prompt_tokens']['total'])
        self.assertEqual(group['prompt_tokens']['unknown_events'], 1)
        self.assertEqual(group['inference_cost_estimate']['known_sum'], 0.5)
        self.assertIsNone(value['currency'])
        self.assertEqual(value['units']['inference_cost_estimate'], 'producer-defined-estimate')
        self.assertEqual(group['event_duration_ms']['total'], '30')

    def test_all_unknown_metrics_are_valid_observation_not_measurement_failure(self):
        value = C.inference([row(known='0')], end=NOW)
        self.assertEqual(value['state'], 'observed')
        self.assertIsNone(value['groups'][0]['cpu_seconds']['known_sum'])
        self.assertEqual(value['groups'][0]['cpu_seconds']['reason'], 'NOT_RECORDED_FOR_ALL_EVENTS')
        self.assertEqual(C.inference([], end=NOW)['recorded_model_events'], 0)
        self.assertIsNone(C.inference(None, end=NOW, reason='READ_FAILED')['recorded_model_events'])

    def test_inference_refuses_duplicate_groups_invalid_counts_nan_and_partial_cap(self):
        values = [[row(), row()], [dict(row(), prompt_tokens_known='3')],
                  [dict(row(), inference_cost_estimate_sum=float('nan'))],
                  [dict(row(), events=True)], [dict(row(), prompt_tokens_sum='01')], [row()] * 51]
        for rows in values:
            with self.subTest(rows=len(rows)), self.assertRaises(C.FinOpsError): C.inference(rows, end=NOW)

    def test_fixed_window_is_24_hours_in_utc(self):
        self.assertEqual(C.window(NOW), {'hours': 24, 'start': '2026-09-19T00:00:21.000Z', 'end': NOW})

    def test_report_roundtrip_tampering_and_partial_are_distinct(self):
        components = [C.project_component(name, **component_args(name)) for name in C.COMPONENTS]
        disks = [C.disk(name, 100, 15, observed_at=NOW) for name in C.DISKS]
        value = C.build_report(components, disks, C.inference([row(known='0')], end=NOW), observed_at=NOW)
        self.assertEqual(value['result'], 'complete'); self.assertEqual(C.validate_public(value), value)
        self.assertEqual(len(value['recommendations']), 2)
        for mutate in (lambda v: v['inference'].update(currency='USD'),
                       lambda v: v['disks'][0].update(severity='normal'),
                       lambda v: v['components'][0].update(secret='private'),
                       lambda v: v['inference']['groups'][0]['prompt_tokens'].update(total='0')):
            bad = deepcopy(value); mutate(bad)
            with self.assertRaises(C.FinOpsError): C.validate_public(bad)
        components[0] = C.unknown_component('trino', 'SAMPLE_STALE')
        self.assertEqual(C.build_report(components, disks, value['inference'], observed_at=NOW)['result'], 'partial')


if __name__ == '__main__': unittest.main()
