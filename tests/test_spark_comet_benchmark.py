from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.spark_comet import benchmark as R
from test_local_spark_benchmark_contract import TOKEN, inputs as base_inputs, output as base_output
from test_spark_comet_contract import plan


def inputs(variant):
    return dict(base_inputs(), variant=variant, image=R.C.IMAGES[variant],
                build_token=('b' if variant == 'stock' else 'c') * 32)


def output(variant):
    value = base_output()
    classes = ([R.C.G.NATIVE_AGGREGATE, R.C.G.NATIVE_SCAN] if variant == 'comet' else
               ['org.apache.spark.sql.execution.aggregate.HashAggregateExec',
                'org.apache.spark.sql.execution.datasources.v2.BatchScanExec'])
    value.update(scope=R.C.SCOPE, variant=variant, configured_image=R.C.IMAGES[variant],
                 comet_version='1.0.0' if variant == 'comet' else None,
                 scala_version='2.13.17', java_version='17', os_arch='aarch64',
                 native_acceleration_verified=variant == 'comet', checks={key: True for key in R.C.CHECKS},
                 plans={case: [plan(classes) for _ in R.W.SAMPLE_INDICES] for case in R.W.CASES})
    return value


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name).resolve(); self.path.chmod(0o700)
        self.order = []
        self.table = Mock(); self.table.receipt.return_value = {'created': True}
        self.table.prepare.return_value = inputs('stock')['sealed']
        self.table.guard_seal.side_effect = lambda: self.order.append('seal') or deepcopy(inputs('stock')['sealed'])
        self.table.cleanup.side_effect = lambda **kw: self.order.append('drop') or {'verified': True}
        self.access = Mock(); self.access.receipt.return_value = {'secrets_persisted': False}
        self.access.prepare.return_value = {k: v for k, v in inputs('stock').items() if k not in
            ('build_token', 'image', 'initial_snapshot_id', 'sealed', 'variant')}
        self.access.cleanup.side_effect = lambda: self.order.append('access-cleanup') or {'verified': True}
        self.apps = {}
        for index, variant in enumerate(R.VARIANTS, 1):
            app = Mock(); app.output = self.path / (variant + '-native')
            app.receipt = {'cleanup': {'verified': True}, 'known': [{'kind': 'Namespace',
                'name': R.NATIVE.C.namespace(TOKEN), 'uid': str(index) * 8 + '-1111-1111-1111-111111111111'}]}
            app.kube.get.return_value = None
            app.run.side_effect = lambda variant=variant: self.order.append(variant) or {
                'result': 'passed', 'cleanup': {'verified': True}, 'output': output(variant)}
            self.apps[variant] = app
        self.worker, self.control = Mock(records=[]), Mock(records=[])
        self.admin = Mock(realm='POLARIS')
        self.builds = {variant: {k: inputs(variant)[k] for k in ('build_token', 'image')} |
                       {'image_id': 'sha256:' + 'e' * 64} for variant in R.VARIANTS}
        self.drain = Mock(side_effect=lambda *args: self.order.append('drain') or {'verified': True})
        self.fixture = Mock(return_value={'unchanged': True})
        self.factory = Mock(side_effect=lambda value: self.apps[value['variant']])

    def workflow(self):
        return R.Workflow(TOKEN, self.path, self.builds, self.worker, self.control, self.admin,
            lambda guard: self.access, [], table_factory=lambda *args, **kw: self.table,
            native_factory=self.factory, drain=self.drain, fixture=self.fixture)

    def test_exact_sixteen_samples_same_seal_sequential_namespaces_and_final_cleanup(self):
        value = self.workflow().run()
        self.assertEqual(value['result'], 'passed')
        self.assertEqual(value['scope'], R.C.SCOPE)
        self.assertEqual(value['comparison']['measurements'], 16)
        self.assertEqual(value['comparison']['execution_order'], ['stock', 'comet'])
        self.assertEqual(self.order, ['seal', 'stock', 'seal', 'seal', 'comet', 'seal',
                                     'drain', 'access-cleanup', 'drop'])
        self.table.measure.assert_not_called(); self.access.prepare.assert_called_once()
        self.assertNotEqual(value['applications']['stock']['closure']['namespace_uid'],
                            value['applications']['comet']['closure']['namespace_uid'])
        self.assertEqual({call.args[0]['sealed']['identity']['snapshot_id'] for call in self.factory.call_args_list},
                         {inputs('stock')['initial_snapshot_id']})

    def test_stock_unconfirmed_cleanup_stops_second_app_and_keeps_table_access(self):
        self.apps['stock'].receipt['cleanup']['verified'] = False
        value = self.workflow().run()
        self.assertEqual(value['result'], 'failed'); self.apps['comet'].run.assert_not_called()
        self.access.cleanup.assert_not_called(); self.table.cleanup.assert_not_called()

    def test_namespace_reappearance_prevents_second_and_drop(self):
        self.apps['stock'].kube.get.return_value = {'foreign': 'namespace'}
        self.assertEqual(self.workflow().run()['result'], 'failed')
        self.apps['comet'].run.assert_not_called(); self.table.cleanup.assert_not_called()

    def test_namespace_uid_is_not_reused_for_second_app(self):
        self.apps['comet'].receipt['known'] = deepcopy(self.apps['stock'].receipt['known'])
        self.assertEqual(self.workflow().run()['result'], 'failed')

    def test_between_apps_snapshot_drift_prevents_comet(self):
        self.table.guard_seal.side_effect = [inputs('stock')['sealed'], inputs('stock')['sealed'], {}]
        self.assertEqual(self.workflow().run()['result'], 'failed')
        self.apps['stock'].run.assert_called_once(); self.apps['comet'].run.assert_not_called()

    def test_failed_native_or_wrong_oracle_never_becomes_comparison(self):
        bad = output('comet'); bad['measurements'][0]['result']['rows'][0][-1] += 1
        self.apps['comet'].run.side_effect = lambda: {'result': 'passed', 'cleanup': {'verified': True}, 'output': bad}
        value = self.workflow().run()
        self.assertEqual(value['result'], 'failed'); self.assertNotIn('comparison', value)

    def test_unknown_query_drain_or_access_cleanup_defers_drop(self):
        for target in (self.drain, self.access.cleanup):
            with self.subTest(target=target):
                target.side_effect = RuntimeError('unknown')
                self.assertEqual(self.workflow().run()['result'], 'failed')
                self.table.cleanup.assert_not_called()
                target.side_effect = lambda *args: {'verified': True}

    def test_first_cancel_survives_secondary_cleanup_and_save_failures(self):
        original = KeyboardInterrupt()
        self.apps['stock'].run.side_effect = original
        self.drain.side_effect = OSError('private failure detail')
        save = R.NATIVE.save
        def failing_save(path, value):
            if value.get('result') == 'interrupted': raise OSError('full')
            return save(path, value)
        with patch.object(R.NATIVE, 'save', side_effect=failing_save), self.assertRaises(KeyboardInterrupt) as got:
            self.workflow().run()
        self.assertIs(got.exception, original); self.apps['comet'].run.assert_not_called()

    def test_fixture_drift_is_not_success(self):
        self.fixture.side_effect = [{'before': True}, {'before': False}]
        self.assertEqual(self.workflow().run()['result'], 'failed')

    def test_native_factory_uses_distinct_private_paths_without_old_output_allocator(self):
        value = self.workflow(); value.native_factory = value.native
        with patch.object(R.NATIVE, 'Kube') as kube, patch.object(R.NATIVE, 'Experiment') as experiment, \
                patch.object(R.NATIVE, 'output_directory', side_effect=AssertionError('old allocator')):
            value.native(inputs('stock')); value.native(inputs('comet'))
            self.assertEqual([call.args[2] for call in experiment.call_args_list],
                             [self.path / 'stock-native', self.path / 'comet-native'])
            self.assertTrue(all(call.kwargs['profile'] == R.PROFILE for call in kube.call_args_list))
            with self.assertRaises(R.NATIVE.ExperimentError): value.native(inputs('stock'))

    def test_source_binding_contains_new_and_reused_dependencies(self):
        source = self.workflow().report['source_hashes']
        for relative in ('spark_comet/benchmark.py', 'spark_comet/remote_contract.py', 'spark_comet/build.py',
                         'spark_benchmark/workload.py', 'spark_benchmark/table.py', 'spark_iceberg/remote_access.py'):
            self.assertIn('tools/local_experiment/' + relative, source)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name).resolve(); self.state.chmod(0o700)
        self.root = self.state / TOKEN; self.root.mkdir(mode=0o700)
        self.path = self.root / 'stock-native'; self.path.mkdir(mode=0o700)
        self.inputs = inputs('stock')
        self.plan = R.C.render(TOKEN, inputs=self.inputs, api_endpoints=[{'ip': '10.43.0.1', 'port': 443}])
        self.receipt = {'schema_version': 1, 'run_token': TOKEN, 'mode': 'success', 'profile': R.PROFILE,
            'profile_inputs': self.inputs, 'namespace': R.NATIVE.C.namespace(TOKEN), 'result': 'failed',
            'plan_sha256': R.NATIVE.C.plan_sha256(self.plan), 'known': [{'kind': 'Namespace',
                'name': R.NATIVE.C.namespace(TOKEN), 'uid': '11111111-1111-1111-1111-111111111111',
                'resource_version': '1'}], 'preflight': {'runtime': {'fixed': True},
                'api_endpoints': [{'ip': '10.43.0.1', 'port': 443}]}, 'automatic_endpoints': [],
            'cleanup': {'verified': False}, 'pending': None}
        self.outer = {'run_token': TOKEN, 'profile': R.PROFILE, 'profile_inputs': {'stock': self.inputs},
            'builds': {'stock': {k: self.inputs[k] for k in ('build_token', 'image')}},
            'applications': {'stock': {'evidence': 'stock-native'}}}
        R.NATIVE.save(self.path / 'summary.json', self.receipt)
        R.NATIVE.save(self.path / 'plan.json', self.plan); R.NATIVE.save(self.root / 'summary.json', self.outer)
        self.kube = Mock(); self.kube.get.return_value = None

    def invoke(self):
        with patch.object(R, 'STATE', self.state): return R.recover(TOKEN, 'stock', kube=self.kube)

    def test_saved_plan_and_uid_recovery_preserves_original_and_never_runs_workload(self):
        before = {p: p.read_bytes() for p in (self.path / 'summary.json', self.path / 'plan.json', self.root / 'summary.json')}
        with patch.object(R.NATIVE.Experiment, 'run', side_effect=AssertionError('no rerun')):
            value = self.invoke()
        self.assertTrue(value['cleanup']['verified'])
        self.assertEqual(value['recovery_scope'], 'recorded-spark-namespace-only')
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.kube.recovery_guard.assert_called_once(); self.kube.create.assert_not_called()

    def test_modified_plan_or_outer_binding_refuses_before_runtime_guard(self):
        for mutation in ('plan', 'outer'):
            with self.subTest(mutation=mutation):
                if mutation == 'plan':
                    bad = deepcopy(self.plan); bad[-1]['metadata']['namespace'] = 'other'
                    R.NATIVE.save(self.path / 'plan.json', bad)
                else:
                    R.NATIVE.save(self.path / 'plan.json', self.plan)
                    bad = deepcopy(self.outer); bad['profile_inputs']['stock']['table_uuid'] = 'other'
                    R.NATIVE.save(self.root / 'summary.json', bad)
                with self.assertRaises((ValueError, R.NATIVE.ExperimentError)): self.invoke()
                self.kube.recovery_guard.assert_not_called(); self.kube.delete.assert_not_called()

    def test_foreign_namespace_uid_refuses_deletion(self):
        namespace = deepcopy(self.plan[0]); namespace['metadata'].update(uid='22222222-2222-2222-2222-222222222222', resourceVersion='1')
        self.kube.get.return_value = namespace
        with self.assertRaisesRegex(R.NATIVE.ExperimentError, 'COMET_RECOVERY_UNVERIFIED'): self.invoke()
        self.kube.delete.assert_not_called()

    def test_recovery_cancel_retains_original_receipt_and_restores_signal_handlers(self):
        import signal
        original = (self.path / 'summary.json').read_bytes()
        interruption = KeyboardInterrupt(); self.kube.get.side_effect = interruption
        before = signal.getsignal(signal.SIGTERM)
        with self.assertRaises(KeyboardInterrupt) as got: self.invoke()
        self.assertIs(got.exception, interruption)
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)
        self.assertEqual((self.path / 'summary.json').read_bytes(), original)
        self.kube.delete.assert_not_called()


class CompareTests(unittest.TestCase):
    def test_missing_sample_and_snapshot_mismatch_refused(self):
        ins = {variant: inputs(variant) for variant in R.VARIANTS}
        outs = {variant: output(variant) for variant in R.VARIANTS}
        outs['comet']['measurements'].pop()
        with self.assertRaises((ValueError, R.NATIVE.ExperimentError)): R.comparison(outs, TOKEN, ins)
        outs = {variant: output(variant) for variant in R.VARIANTS}
        ins['comet']['initial_snapshot_id'] += 1
        with self.assertRaises((ValueError, R.NATIVE.ExperimentError)): R.comparison(outs, TOKEN, ins)

    def test_outer_evidence_failure_cannot_mask_first_or_secondary_cancel(self):
        for first_cancel in (True, False):
            with self.subTest(first_cancel=first_cancel), tempfile.TemporaryDirectory() as temp:
                state = Path(temp).resolve(); state.chmod(0o700)
                interruption = KeyboardInterrupt()
                def rejected_build(_):
                    root = next(p for p in state.iterdir() if p.is_dir())
                    R.NATIVE.save(root / 'summary.json', {'run_token': root.name, 'result': 'running'})
                    raise interruption if first_cancel else RuntimeError('build failed')
                with patch.object(R, 'STATE', state), patch.object(R.B.BUILD, 'read_verified_image', side_effect=rejected_build), \
                        patch.object(R.B.BUILD, 'file_bytes', side_effect=OSError('read failed') if first_cancel else interruption), \
                        self.assertRaises(KeyboardInterrupt) as got:
                    R.run('b' * 32, 'c' * 32)
                self.assertIs(got.exception, interruption)

    def test_mismatched_comet_base_refuses_before_tunnel_or_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp).resolve(); state.chmod(0o700)
            with patch.object(R, 'STATE', state), patch.object(R.B.BUILD, 'read_verified_image', return_value={}), \
                    patch.object(R.BUILD, 'read_verified_image', return_value={'base_build_token': 'd' * 32}), \
                    patch.object(R.B.REFS, 'polaris_tunnel') as polaris, \
                    patch.object(R.B.SB.SESSION, 'trino_tunnel') as trino:
                report, _ = R.run('b' * 32, 'c' * 32)
                self.assertEqual(report['result'], 'failed')
                self.assertEqual(report['exit_error'], 'COMET_STOCK_BASE_DIFFER')
                polaris.assert_not_called(); trino.assert_not_called()


if __name__ == '__main__':
    unittest.main()
