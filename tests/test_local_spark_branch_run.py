import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.spark_branch import run as R

TOKEN = 'b' * 32
UUID = '00000000-0000-4000-8000-000000000001'


class InputsTests(unittest.TestCase):
    def test_sealed_host_inputs_match_fixed_native_contract_with_optional_empty_snapshot(self):
        from test_local_spark_branch_contract import TOKEN as token, inputs
        for initial in (None, 777777777777777777):
            expected = inputs(initial)
            build = {k: expected[k] for k in ('build_token', 'image')}
            self.assertEqual(R.inputs_for(token, build, expected['sealed'], 'POLARIS'), expected)

    def test_changed_seal_cannot_become_native_inputs(self):
        from test_local_spark_branch_contract import TOKEN as token, inputs
        expected = inputs()
        expected['sealed']['refs']['audit_branch']['snapshot_id'] = expected['sealed']['snapshot_b']
        build = {k: expected[k] for k in ('build_token', 'image')}
        with self.assertRaises(ValueError):
            R.inputs_for(token, build, expected['sealed'], 'POLARIS')


class WorkflowTests(unittest.TestCase):
    """Contract and table tests validate SQL/metadata; these cover orchestration."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name).resolve()
        self.output.chmod(0o700)
        self.order, self.pending = [], False
        self.sealed = {'identity': {'table_uuid': UUID, 'table_location': R.RC.table_location(TOKEN)},
                       'snapshot_b': 8088807033386546123}
        self.table = Mock()
        self.table.receipt.side_effect = lambda: {'created': True, 'spark_pending': self.pending}
        self.table.prepare.side_effect = lambda: self.order.append('prepare') or copy.deepcopy(self.sealed)
        self.table.guard_seal.side_effect = lambda: self.order.append('guard') or copy.deepcopy(self.sealed)
        self.table.mark_spark_pending.side_effect = self.mark_pending
        self.table.verify_written.side_effect = self.verify_written
        self.table.cleanup.side_effect = self.cleanup
        self.access = Mock()
        self.access.receipt.return_value = {'secrets_persisted': False}
        self.access.cleanup.side_effect = lambda: self.order.append('access_cleanup') or {'verified': True}
        self.spark = Mock()
        self.spark.output = R.ROOT / 'tmp/local-experiment/spark-runs' / TOKEN
        self.spark.receipt = {'cleanup': {'verified': True}}
        self.spark.run.side_effect = self.native_run
        self.worker, self.control = Mock(), Mock()
        self.worker.records = self.control.records = []
        self.admin = Mock(realm='POLARIS')
        self.build = {'build_token': 'c' * 32, 'image': R.RC.IMAGE, 'image_id': 'sha256:' + 'd' * 64}
        self.inputs = {'table_uuid': UUID, 'table_location': self.sealed['identity']['table_location'],
                       'build_token': self.build['build_token'], 'image': self.build['image'],
                       'initial_snapshot_id': self.sealed['snapshot_b'], 'sealed': self.sealed,
                       'scope': 'PRINCIPAL_ROLE:spark_' + TOKEN}
        self.access.prepare.return_value = {k: v for k, v in self.inputs.items()
                                            if k not in ('build_token', 'image', 'initial_snapshot_id', 'sealed')}
        self.drain = Mock(side_effect=lambda *a: self.order.append('drain') or {'verified': True})
        self.fixture = Mock(return_value={'rows': [[1, 'shirokuma'], [2, 'polaris']], 'tables': []})

    def mark_pending(self):
        self.pending = True
        self.order.append('pending')

    def native_run(self):
        self.assertTrue(self.pending)
        self.order.append('native')
        return {'result': 'passed', 'cleanup': {'verified': True},
                'output': {'write_outcome': 'verified'}}

    def verify_written(self, output):
        self.assertEqual(output['write_outcome'], 'verified')
        self.order.append('verify')
        self.pending = False
        return {'verified': True, 'independent_branch': True}

    def cleanup(self, **kwargs):
        self.assertEqual(kwargs, {'writers_stopped': True, 'queries_drained': True})
        self.order.append('defer' if self.pending else 'drop')
        return {'verified': not self.pending}

    def workflow(self, factory=None):
        with patch.object(R.T, 'HostTable', return_value=self.table):
            return R.Workflow(TOKEN, self.output, self.build, self.worker, self.control, self.admin,
                              lambda guard: self.access, [],
                              native_factory=factory or (lambda _: self.spark),
                              drain=self.drain, fixture=self.fixture)

    def run_workflow(self, factory=None):
        with patch.object(R, 'inputs_for', return_value=self.inputs):
            self.current = self.workflow(factory)
            return self.current.run()

    def test_success_marks_before_execution_and_verifies_before_owned_cleanup(self):
        result = self.run_workflow()
        self.assertEqual(result['result'], 'passed')
        self.assertEqual(self.order, ['prepare', 'pending', 'native', 'verify',
                                     'drain', 'access_cleanup', 'drop'])
        self.assertTrue(result['fixture_unchanged'])
        self.assertTrue(result['cross_engine']['independent_branch'])

    def test_scope_mismatch_never_starts_native(self):
        self.access.prepare.return_value = {'scope': 'foreign'}
        result = self.run_workflow()
        self.assertEqual(result['result'], 'failed')
        self.spark.run.assert_not_called()
        self.table.mark_spark_pending.assert_not_called()

    def test_prewrite_identity_drift_never_starts_native(self):
        self.table.mark_spark_pending.side_effect = ValueError('TABLE_CHANGED')
        self.assertEqual(self.run_workflow()['result'], 'failed')
        self.spark.run.assert_not_called()
        self.assertFalse(self.pending)

    def test_pending_journal_failure_prevents_insert_and_defers_table(self):
        def failed_journal():
            self.pending = True
            raise OSError('disk full')
        self.table.mark_spark_pending.side_effect = failed_journal
        self.assertEqual(self.run_workflow()['result'], 'failed')
        self.spark.run.assert_not_called()
        self.assertEqual(self.order[-1], 'defer')

    def test_unknown_spark_result_never_retries_or_drops_table(self):
        self.spark.run.side_effect = RuntimeError('lost response')
        self.assertEqual(self.run_workflow()['result'], 'failed')
        self.assertEqual(self.spark.run.call_count, 1)
        self.table.verify_written.assert_not_called()
        self.assertEqual(self.order[-1], 'defer')

    def test_native_failed_receipt_not_promoted_even_with_verified_output(self):
        self.spark.run.side_effect = lambda: {'result': 'failed', 'cleanup': {'verified': True},
                                             'output': {'write_outcome': 'verified'}}
        self.assertEqual(self.run_workflow()['result'], 'failed')
        self.table.verify_written.assert_not_called()
        self.assertEqual(self.order[-1], 'defer')

    def test_cross_engine_mismatch_keeps_pending_table(self):
        self.table.verify_written.side_effect = ValueError('BRANCH_ROWS_DIFFER')
        self.assertEqual(self.run_workflow()['result'], 'failed')
        self.assertTrue(self.pending)
        self.assertEqual(self.order[-1], 'defer')

    def test_live_writers_prevent_access_and_table_cleanup(self):
        self.spark.run.side_effect = RuntimeError('failed')
        self.spark.receipt['cleanup']['verified'] = False
        self.assertEqual(self.run_workflow()['result'], 'failed')
        self.access.cleanup.assert_not_called()
        self.table.cleanup.assert_not_called()

    def test_unknown_owned_queries_prevent_cleanup(self):
        self.drain.side_effect = RuntimeError('lost')
        self.assertEqual(self.run_workflow()['result'], 'failed')
        self.access.cleanup.assert_not_called()
        self.table.cleanup.assert_not_called()

    def test_partial_access_creation_is_cleaned_without_native_execution(self):
        self.access.prepare.side_effect = RuntimeError('secret-sentinel')
        self.assertEqual(self.run_workflow()['result'], 'failed')
        self.spark.run.assert_not_called()
        self.assertEqual(self.order[-3:], ['drain', 'access_cleanup', 'drop'])
        self.assertNotIn('secret-sentinel', (self.output / 'summary.json').read_text())

    def test_access_cleanup_failure_prevents_table_drop(self):
        self.access.cleanup.side_effect = RuntimeError('unknown')
        self.assertEqual(self.run_workflow()['result'], 'failed')
        self.table.cleanup.assert_not_called()

    def test_fixture_drift_prevents_pass(self):
        self.fixture.side_effect = [{'rows': [[1, 'before']]}, {'rows': [[1, 'after']]}]
        self.assertEqual(self.run_workflow()['result'], 'failed')

    def test_original_cancellation_survives_cleanup(self):
        interrupted = KeyboardInterrupt()
        self.spark.run.side_effect = interrupted
        with self.assertRaises(KeyboardInterrupt) as got:
            self.run_workflow()
        self.assertIs(got.exception, interrupted)
        self.assertEqual(self.order[-1], 'defer')

    def test_cleanup_cancellation_not_hidden_by_earlier_failure(self):
        self.spark.run.side_effect = RuntimeError('earlier')
        interrupted = KeyboardInterrupt()
        self.access.cleanup.side_effect = interrupted
        with self.assertRaises(KeyboardInterrupt) as got:
            self.run_workflow()
        self.assertIs(got.exception, interrupted)
        self.table.cleanup.assert_not_called()

    def test_cancellation_survives_final_journal_failure(self):
        interrupted = KeyboardInterrupt()
        self.spark.run.side_effect = interrupted
        save = R.NATIVE.save
        def broken(path, value):
            if value['result'] == 'interrupted':
                raise OSError('disk full')
            return save(path, value)
        with patch.object(R.NATIVE, 'save', side_effect=broken):
            with self.assertRaises(KeyboardInterrupt) as got:
                self.run_workflow()
        self.assertIs(got.exception, interrupted)


if __name__ == '__main__':
    unittest.main()
