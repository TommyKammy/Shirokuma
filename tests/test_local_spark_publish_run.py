import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.spark_publish import run as R

TOKEN = 'b' * 32
UUID = '00000000-0000-4000-8000-000000000001'


class InputsTests(unittest.TestCase):
    def test_sealed_host_inputs_match_fixed_native_contract_with_optional_empty_snapshot(self):
        from test_local_spark_publish_contract import TOKEN as token, inputs
        for initial in (None, 777777777777777777):
            expected = inputs(initial)
            build = {k: expected[k] for k in ('build_token', 'image')}
            self.assertEqual(R.inputs_for(token, build, expected['sealed'], 'POLARIS'), expected)

    def test_changed_seal_cannot_become_native_inputs(self):
        from test_local_spark_publish_contract import TOKEN as token, inputs
        expected = inputs()
        expected['sealed']['refs']['audit_branch']['snapshot_id'] = expected['sealed']['snapshot_a']
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
        self.order, self.pending, self.commit_pending = [], False, False
        self.sealed = {'identity': {'table_uuid': UUID, 'table_location': R.RC.table_location(TOKEN)},
                       'snapshot_b': 8088807033386546123}
        self.table = Mock()
        self.table.receipt.side_effect = lambda: {'created': True, 'spark_pending': self.pending}
        self.table.prepare.side_effect = lambda: self.order.append('prepare') or copy.deepcopy(self.sealed)
        self.table.guard_seal.side_effect = lambda: self.order.append('guard') or copy.deepcopy(self.sealed)
        self.table.mark_spark_pending.side_effect = self.mark_pending
        self.table.verify_written.side_effect = self.verify_written
        self.table.cleanup.side_effect = self.cleanup
        self.table.plan_publish.side_effect = self.plan_publish
        self.table.publish.side_effect = self.publish
        self.table.negative_main_append.side_effect = lambda: self.order.append('append_d') or {'snapshot_d': 888888888888888888}
        self.table.verify_negative.side_effect = lambda: self.order.append('negative_readback') or {'verified': True}
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
        self.order.append('defer' if self.pending or self.commit_pending else 'drop')
        return {'verified': not (self.pending or self.commit_pending)}

    def plan_publish(self, expected):
        self.assertIn('access_cleanup', self.order)
        self.order.append('plan')
        if self.current.mode == 'negative':
            return {'outcome': 'rejected', 'reason': 'STALE_MAIN' if expected == self.sealed['snapshot_b'] else 'MAIN_NOT_ANCESTOR',
                    'body_sha256': None}
        return {'outcome': 'ready', 'body_sha256': 'a' * 64}

    def publish(self, plan):
        self.assertEqual(plan['outcome'], 'ready')
        self.assertEqual(self.access.cleanup.call_count, 1)
        self.assertTrue(self.spark.receipt['cleanup']['verified'])
        self.order.append('publish')
        return {'outcome': 'verified', 'metadata_only': True}

    def workflow(self, factory=None, mode='positive'):
        with patch.object(R.T, 'HostTable', return_value=self.table):
            return R.Workflow(TOKEN, self.output, self.build, self.worker, self.control, self.admin,
                              lambda guard: self.access, [],
                              mode=mode, native_factory=factory or (lambda _: self.spark),
                              drain=self.drain, fixture=self.fixture)

    def run_workflow(self, factory=None, mode='positive'):
        with patch.object(R, 'inputs_for', return_value=self.inputs):
            self.current = self.workflow(factory, mode)
            return self.current.run()

    def test_success_marks_before_execution_and_verifies_before_owned_cleanup(self):
        result = self.run_workflow()
        self.assertEqual(result['result'], 'passed')
        self.assertEqual(self.order, ['prepare', 'pending', 'native', 'verify',
                                     'drain', 'access_cleanup', 'plan', 'publish', 'drain', 'drop'])
        self.assertTrue(result['fixture_unchanged'])
        self.assertTrue(result['cross_engine']['independent_branch'])
        self.assertEqual(self.access.cleanup.call_count, 1)
        self.assertEqual(self.table.publish.call_count, 1)

    def test_negative_mode_evaluates_two_rejections_without_commit(self):
        result = self.run_workflow(mode='negative')
        self.assertEqual(result['result'], 'passed')
        self.assertEqual(self.order, ['prepare', 'pending', 'native', 'verify', 'drain', 'access_cleanup',
                                     'append_d', 'plan', 'plan', 'negative_readback', 'drain', 'drop'])
        self.table.publish.assert_not_called()
        self.assertEqual(self.spark.run.call_count, 1)
        self.assertEqual(self.access.cleanup.call_count, 1)

    def test_negative_rejection_and_readback_cannot_be_assumed(self):
        self.table.plan_publish.side_effect = lambda _: {'outcome':'ready', 'reason':None, 'body_sha256':'a'*64}
        self.assertEqual(self.run_workflow(mode='negative')['result'], 'failed')
        self.table.publish.assert_not_called(); self.table.verify_negative.assert_not_called()

    def test_negative_readback_failure_cannot_pass(self):
        self.table.verify_negative.side_effect = RuntimeError('metadata mismatch')
        self.assertEqual(self.run_workflow(mode='negative')['result'], 'failed')
        self.table.publish.assert_not_called()

    def test_commit_unknown_preserves_table_without_retry(self):
        def unknown(_):
            self.commit_pending = True
            raise TimeoutError('private response')
        self.table.publish.side_effect = unknown
        self.assertEqual(self.run_workflow()['result'], 'failed')
        self.assertEqual(self.table.publish.call_count, 1)
        self.assertEqual(self.order[-1], 'defer')
        self.assertEqual(self.access.cleanup.call_count, 1)

    def test_typed_commit_rejection_does_not_become_success_or_retry(self):
        self.table.publish.side_effect = lambda _: {'outcome':'rejected','metadata_only':False,'http_status':409}
        self.assertEqual(self.run_workflow()['result'], 'failed')
        self.assertEqual(self.table.publish.call_count, 1)

    def test_cancelled_commit_survives_final_save_outage_and_defers_drop(self):
        original = KeyboardInterrupt()
        def interrupted(_):
            self.commit_pending = True
            raise original
        self.table.publish.side_effect = interrupted
        save = R.NATIVE.save
        def outage(path,value):
            if value['result'] == 'interrupted': raise OSError('secondary')
            return save(path,value)
        with patch.object(R.NATIVE,'save',side_effect=outage), self.assertRaises(KeyboardInterrupt) as caught:
            self.run_workflow()
        self.assertIs(caught.exception,original)
        self.assertEqual(self.order[-1],'defer')
        self.assertEqual(self.table.publish.call_count,1)

    def test_plan_save_failure_never_sends_commit(self):
        save = R.NATIVE.save
        def outage(path,value):
            if 'publish_plan' in value: raise OSError('private')
            return save(path,value)
        with patch.object(R.NATIVE,'save',side_effect=outage), self.assertRaises(OSError):
            self.run_workflow()
        self.table.publish.assert_not_called()

    def test_changed_sources_prevent_main_commit(self):
        with patch.object(R,'inputs_for',return_value=self.inputs):
            self.current = self.workflow()
            self.current.source_hashes = Mock(return_value={'changed':'b'*64})
            result = self.current.run()
        self.assertEqual(result['result'],'failed')
        self.table.publish.assert_not_called(); self.table.plan_publish.assert_not_called()

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

    def test_final_drain_failure_cannot_reuse_precommit_drain(self):
        self.drain.side_effect = [{'verified': True}, RuntimeError('lost final query history')]
        result = self.run_workflow()
        self.assertEqual(result['result'], 'failed')
        self.assertFalse(result['query_drain']['verified'])
        self.assertEqual(self.table.publish.call_count, 1)
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
