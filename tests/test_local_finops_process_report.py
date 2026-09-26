import json
import unittest
from unittest.mock import patch

from tools.local_experiment.finops import contract as C, local_processes as P, render as H, run as R
from test_local_finops_contract import NOW, component_args, row
import test_local_finops_run as RUN_TESTS


def processes():
    return {'schema_version': 1, 'components': [
        dict(component=name, state='observed', reason=None,
             request_started_at='2026-09-20T00:00:20.000000000Z', observed_at=NOW,
             identity=dict(process_count=14 if name == 'notebook-lab' else 1,
                           kernel_count=13 if name == 'notebook-lab' else 0, sha256='a'*64),
             sample=dict(cpu_time_delta_ms='125', elapsed_ns='1000000000', rss_bytes='2147483648'))
        for name in P.COMPONENTS]}


def report(local=None):
    return C.build_report([C.project_component(name, **component_args(name)) for name in C.COMPONENTS],
        [C.disk(name, 1000, 300, observed_at=NOW) for name in C.DISKS],
        C.inference([row()], end=NOW), observed_at=NOW, local_processes=local)


class ProcessReportTests(unittest.TestCase):
    def test_old_saved_contract_and_renderer_remain_version_one(self):
        value = report()
        self.assertEqual(value['schema_version'], 1)
        self.assertNotIn('local_processes', value)
        self.assertEqual(C.validate_public(value), value)
        self.assertNotIn('id="local-processes"', H.render(value))

    def test_version_two_copies_owned_samples_and_renders_cpu_delta_and_rss(self):
        source = processes(); value = report(source); source['components'][0]['sample']['rss_bytes'] = '0'
        self.assertEqual(value['schema_version'], 2)
        self.assertEqual(value['result'], 'complete')
        self.assertEqual(C.validate_public(value), value)
        html = H.render(value)
        for visible in ('MLflow', 'JupyterLab', 'Model Serving', 'kernel 13個を含む',
                        '0.125 cores', '2,048 MiB', 'RSSは共有ページを重複', 'idleとは判定できません'):
            self.assertIn(visible, html)
        self.assertEqual(html.count('id="local-processes"'), 1)

    def test_stopped_is_known_without_zero_usage_while_unknown_is_partial(self):
        local = processes(); item = local['components'][0]
        item.update(state='stopped', reason='STOPPED', identity=None, sample=None)
        value = report(local); self.assertEqual(value['result'], 'complete')
        html = H._local_processes(value); ml = html.split('<th scope="row">MLflow')[1].split('</tr>')[0]
        self.assertIn('停止中', ml); self.assertIn('対象なし', ml); self.assertNotIn('0 cores', ml)
        item.update(state='ownership-unknown', reason='OWNERSHIP_UNKNOWN')
        value = report(local); self.assertEqual(value['result'], 'partial')
        self.assertIn('所有確認不能', H.render(value))
        self.assertEqual(C.validate_public(value), value)

    def test_version_shape_future_sample_and_private_extra_fields_refused(self):
        for mutate in (lambda v:v.update(schema_version=1), lambda v:v.pop('local_processes'),
                       lambda v:v.update(schema_version=True),
                       lambda v:v['local_processes']['components'][0].update(argv='PRIVATE'),
                       lambda v:v['local_processes']['components'][0].update(state='observed',reason='READ_FAILED'),
                       lambda v:v.update(observed_at='2026-09-20T00:00:19Z')):
            value = report(processes()); mutate(value)
            with self.subTest(mutation=mutate), self.assertRaises((ValueError, KeyError, TypeError)):
                C.validate_public(value)

    def test_collect_includes_local_groups_and_sanitizes_worker_failure(self):
        backend = RUN_TESTS.RuntimeTests().backend(); backend.local_processes.return_value = processes()
        value = R.collect(backend)
        self.assertEqual(value['schema_version'], 3); self.assertEqual(value['result'], 'complete')
        backend.local_processes.assert_called_once_with()
        backend.local_processes.side_effect = RuntimeError('PRIVATE_PID_COMMAND_TOKEN')
        value = R.collect(backend); self.assertEqual(value['result'], 'partial')
        self.assertTrue(all(x['sample'] is None for x in value['local_processes']['components']))
        self.assertNotIn('PRIVATE_PID_COMMAND_TOKEN', json.dumps(value))

    def test_cleanup_failure_stops_local_collection_and_cancel_propagates(self):
        backend = RUN_TESTS.RuntimeTests().backend(); backend.inference.side_effect = R.U.PodUsageCleanupError()
        value = R.collect(backend); backend.local_processes.assert_not_called()
        self.assertTrue(all(x['reason'] == 'CLEANUP_UNVERIFIED' for x in value['local_processes']['components']))
        backend = RUN_TESTS.RuntimeTests().backend(); error = KeyboardInterrupt(); backend.local_processes.side_effect = error
        with self.assertRaises(KeyboardInterrupt) as caught: R.collect(backend)
        self.assertIs(caught.exception, error)

    def test_transport_uses_only_fixed_lab_worker_and_strict_public_parser(self):
        backend = R.Backend()
        with patch.object(backend, '_command', return_value=json.dumps(processes())) as command:
            self.assertEqual(backend.local_processes(), processes())
        command.assert_called_once_with(P.arguments(), timeout=20)
        for raw in ('{"schema_version":1,"schema_version":1}', '{"x":NaN}',
                    json.dumps(dict(processes(), private='TOKEN'))):
            with patch.object(backend, '_command', return_value=raw), self.assertRaises(ValueError):
                backend.local_processes()


if __name__ == '__main__': unittest.main()
