from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.spark_benchmark import run as R
from test_local_spark_benchmark_contract import TOKEN, SNAPSHOT, inputs, output, samples


class CompareTests(unittest.TestCase):
    def test_sixteen_observations_share_one_exact_snapshot(self):
        value = R.comparison(samples('trino'), samples(), TOKEN, inputs())
        self.assertEqual(value['snapshot_id'], SNAPSHOT)
        self.assertEqual(value['measurements'], 16)
        self.assertEqual(value['queries'][0]['spark_repeat_median_ns'], 1_000_002)

    def test_missing_sample_is_not_zero_or_median(self):
        with self.assertRaises(ValueError): R.comparison(samples('trino')[:-1], samples(), TOKEN, inputs())

    def test_unknown_post_drain_has_server_horizon(self):
        with patch.object(R.SB, 'drain_queries') as drain:
            R.drain_queries('control','worker',TOKEN)
            drain.assert_called_once_with('control','worker',TOKEN,timeout=150)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name).resolve(); self.path.chmod(0o700)
        self.order = []
        self.table = Mock(); self.table.receipt.return_value = {'created': True}
        self.table.prepare.return_value = inputs()['sealed']
        self.table.guard_seal.side_effect = lambda: self.order.append('seal') or deepcopy(inputs()['sealed'])
        self.table.measure.return_value = samples('trino')
        self.table.cleanup.side_effect = lambda **kw: self.order.append('drop') or {'verified': True}
        self.access = Mock(); self.access.receipt.return_value = {'secrets_persisted': False}
        self.access.prepare.return_value = {k:v for k,v in inputs().items() if k not in ('build_token','image','initial_snapshot_id','sealed')}
        self.access.cleanup.side_effect = lambda: self.order.append('access') or {'verified': True}
        self.native = Mock(); self.native.output = R.ROOT / 'tmp/local-experiment/spark-runs' / TOKEN
        self.native.receipt = {'cleanup': {'verified': True}}
        self.native.run.return_value = {'result': 'passed','cleanup': {'verified': True},'output':output()}
        self.worker, self.control = Mock(), Mock(); self.worker.records = []; self.control.records = []
        self.admin = Mock(realm='POLARIS')
        self.build = {'build_token': 'b'*32, 'image': inputs()['image'], 'image_id': 'sha256:'+'e'*64}
        self.drain = Mock(side_effect=lambda *a: self.order.append('drain') or {'verified': True})
        self.fixture = Mock(return_value={'rows':[[1,'shirokuma'],[2,'polaris']]})

    def run_workflow(self):
        workflow = R.Workflow(TOKEN,self.path,self.build,self.worker,self.control,self.admin,
            lambda guard:self.access,[],table_factory=lambda *a,**kw:self.table,native_factory=lambda value:self.native,
            drain=self.drain,fixture=self.fixture)
        return workflow.run()

    def test_success_validates_seal_then_cleans_after_drain(self):
        value = self.run_workflow()
        self.assertEqual(value['result'],'passed')
        self.assertEqual(self.order,['seal','seal','drain','access','drop'])
        self.assertTrue(value['fixture_unchanged'])
        self.assertEqual(value['comparison']['measurements'],16)

    def test_invalid_trino_result_prevents_spark_and_still_cleans(self):
        self.table.measure.return_value[0]['result']['rows'][0][-1] += 1
        self.assertEqual(self.run_workflow()['result'],'failed')
        self.native.run.assert_not_called()
        self.assertEqual(self.order,['drain','access','drop'])

    def test_source_prepare_failure_never_starts_native_or_access(self):
        self.table.prepare.side_effect = RuntimeError('private-secret')
        self.assertEqual(self.run_workflow()['result'],'failed')
        self.native.run.assert_not_called(); self.access.prepare.assert_not_called()
        self.assertNotIn('private-secret',(self.path/'summary.json').read_text())

    def test_pre_native_seal_drift_prevents_runtime(self):
        self.table.guard_seal.side_effect = RuntimeError('changed')
        self.assertEqual(self.run_workflow()['result'],'failed'); self.native.run.assert_not_called()

    def test_post_native_seal_drift_prevents_success(self):
        self.table.guard_seal.side_effect = [inputs()['sealed'], {}]
        self.assertEqual(self.run_workflow()['result'],'failed'); self.table.cleanup.assert_called_once()

    def test_unknown_native_exit_defers_access_and_drop(self):
        self.native.run.side_effect = RuntimeError('unknown')
        self.native.receipt['cleanup']['verified'] = False
        self.assertEqual(self.run_workflow()['result'],'failed')
        self.access.cleanup.assert_not_called(); self.table.cleanup.assert_not_called()

    def test_failed_native_verification_cannot_become_success(self):
        self.native.run.return_value['result'] = 'failed'
        self.assertEqual(self.run_workflow()['result'],'failed')

    def test_unknown_queries_defer_both_cleanup_actions(self):
        self.drain.side_effect = RuntimeError('unknown')
        self.assertEqual(self.run_workflow()['result'],'failed')
        self.access.cleanup.assert_not_called(); self.table.cleanup.assert_not_called()

    def test_access_cleanup_failure_defers_drop(self):
        self.access.cleanup.side_effect = RuntimeError('unknown')
        self.assertEqual(self.run_workflow()['result'],'failed'); self.table.cleanup.assert_not_called()

    def test_access_scope_mismatch_prevents_spark(self):
        self.access.prepare.return_value['scope'] = 'PRINCIPAL_ROLE:wrong'
        self.assertEqual(self.run_workflow()['result'],'failed'); self.native.run.assert_not_called()

    def test_cancellation_is_preserved_after_cleanup(self):
        error = KeyboardInterrupt(); self.native.run.side_effect = error
        with self.assertRaises(KeyboardInterrupt) as got:self.run_workflow()
        self.assertIs(got.exception,error); self.table.cleanup.assert_called_once()

    def test_first_cancellation_survives_failed_save(self):
        error = KeyboardInterrupt(); self.native.run.side_effect = error
        save = R.NATIVE.save
        def fail(path,value):
            if value['result']=='interrupted':raise OSError('full')
            return save(path,value)
        with patch.object(R.NATIVE,'save',side_effect=fail),self.assertRaises(KeyboardInterrupt) as got:self.run_workflow()
        self.assertIs(got.exception,error)

    def test_cleanup_cancellation_supersedes_regular_failure(self):
        self.native.run.side_effect = RuntimeError('earlier')
        error = KeyboardInterrupt();self.access.cleanup.side_effect = error
        with self.assertRaises(KeyboardInterrupt) as got:self.run_workflow()
        self.assertIs(got.exception,error);self.table.cleanup.assert_not_called()

    def test_retained_fixture_drift_is_failure(self):
        self.fixture.side_effect = [{'rows': [[1,'old']]},{'rows':[[1,'changed']]}]
        self.assertEqual(self.run_workflow()['result'],'failed')


if __name__ == '__main__':unittest.main()
