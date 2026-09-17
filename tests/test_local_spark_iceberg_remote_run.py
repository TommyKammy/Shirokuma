import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.spark_iceberg import remote_run as R
from tools.local_experiment.trino_opa.common import ExperimentError

TOKEN = 'b' * 32
UUID = '00000000-0000-4000-8000-000000000001'
SNAPSHOT = 8088807033386546123


def results():
    location = R.C.table_location(TOKEN)
    identity = {'uuid': UUID, 'location': location, 'comment': R.C.marker(TOKEN),
                'snapshot_id': SNAPSHOT, 'parent_snapshot_id': None,
                'metadata_location': location + '/metadata/v2.metadata.json', 'operation': 'append'}
    spark = {'table_identity': identity, 'dataframe_rows': R.C.oracle_rows(), 'sql_rows': R.C.oracle_rows(),
             'groups': R.C.oracle_groups(), 'data_files': [{'path': location + '/data/part.parquet',
                                                         'record_count': 32, 'file_size_in_bytes': 2048}]}
    trino = {'identity': {'table_uuid': UUID, 'table_location': location, 'marker': R.C.marker(TOKEN),
                         'snapshot_id': SNAPSHOT, 'parent_snapshot_id': None, 'metadata_location': identity['metadata_location']},
             'rows': R.C.oracle_rows(), 'groups': R.C.oracle_groups(),
             'data_files': [{'file_path': location + '/data/part.parquet', 'record_count': 32, 'file_size_in_bytes': 2048}]}
    return spark, trino


class CompareTests(unittest.TestCase):
    def test_exact_int64_and_all_data_files(self):
        result = R.compare_engines(*results())
        self.assertEqual(result['snapshot_id'], SNAPSHOT)
        self.assertTrue(result['verified'])

    def test_cross_engine_changes_fail(self):
        for field, new in [('table_uuid', 'different'), ('snapshot_id', SNAPSHOT + 1),
                           ('metadata_location', 'elsewhere')]:
            spark, trino = results()
            trino['identity'][field] = new
            with self.assertRaisesRegex(ExperimentError, 'IDENTITY_DIFFER'):
                R.compare_engines(spark, trino)
        spark, trino = results()
        trino['data_files'][0]['record_count'] = 31
        with self.assertRaisesRegex(ExperimentError, 'DATA_FILES_DIFFER'):
            R.compare_engines(spark, trino)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name).resolve()
        self.output.chmod(0o700)
        self.order = []
        self.spark_output, self.trino_output = results()
        self.table = Mock()
        self.table.receipt.return_value = {'created': True}
        self.table.create.return_value = {'table_uuid': UUID, 'table_location': R.C.table_location(TOKEN), 'snapshot_id': None}
        self.table.verify_written.return_value = self.trino_output
        self.table.cleanup.side_effect = lambda **kw: self.order.append('table_drop') or {'verified': True}
        self.access = Mock()
        self.access.receipt.return_value = {'secrets_persisted': False}
        self.access.cleanup.side_effect = lambda: self.order.append('access_cleanup') or {'verified': True}
        self.spark = Mock()
        self.spark.output = R.ROOT / 'tmp/local-experiment/spark-runs' / TOKEN
        self.spark.receipt = {'cleanup': {'verified': True}}
        self.spark.run.return_value = {'result': 'passed', 'cleanup': {'verified': True}, 'output': self.spark_output}
        self.worker, self.control = Mock(), Mock()
        self.worker.records = self.control.records = []
        self.admin = Mock(realm='POLARIS')
        self.build = {'build_token': 'c' * 32, 'image': R.C.IMAGE, 'image_id': 'sha256:' + 'd' * 64}
        inputs = R.inputs_for(TOKEN, self.build, self.table.create.return_value, self.admin.realm)
        self.access.prepare.return_value = {k: v for k, v in inputs.items() if k not in ('build_token', 'image', 'initial_snapshot_id')}
        self.drain = Mock(side_effect=lambda *a: self.order.append('drain') or {'verified': True})
        self.fixture = Mock(return_value={'rows': [[1, 'shirokuma'], [2, 'polaris']], 'tables': []})

    def run_workflow(self):
        with patch.object(R.T, 'HostTable', return_value=self.table):
            self.workflow = R.Workflow(TOKEN, self.output, self.build, self.worker, self.control, self.admin,
                                       lambda guard: self.access, [], native_factory=lambda inputs: self.spark,
                                       drain=self.drain, fixture=self.fixture)
            return self.workflow.run()

    def test_success_cleans_in_order_after_cross_engine_validation(self):
        result = self.run_workflow()
        self.assertEqual(result['result'], 'passed')
        self.assertEqual(self.order, ['drain', 'access_cleanup', 'table_drop'])
        self.assertTrue(result['fixture_unchanged'])

    def test_live_writers_prevent_both_cleanup_actions(self):
        self.spark.run.side_effect = RuntimeError('failed')
        self.spark.receipt['cleanup']['verified'] = False
        result = self.run_workflow()
        self.assertEqual(result['result'], 'failed')
        self.access.cleanup.assert_not_called()
        self.table.cleanup.assert_not_called()

    def test_unknown_trino_queries_prevent_drop(self):
        self.drain.side_effect = RuntimeError('lost')
        result = self.run_workflow()
        self.assertEqual(result['result'], 'failed')
        self.access.cleanup.assert_not_called()
        self.table.cleanup.assert_not_called()

    def test_partial_access_creation_is_cleaned(self):
        self.access.prepare.side_effect = RuntimeError('secret-sentinel')
        result = self.run_workflow()
        self.assertEqual(result['result'], 'failed')
        self.spark.run.assert_not_called()
        self.assertEqual(self.order, ['drain', 'access_cleanup', 'table_drop'])
        self.assertNotIn('secret-sentinel', (self.output / 'summary.json').read_text())

    def test_unknown_access_cleanup_defers_table_drop(self):
        self.access.cleanup.side_effect = RuntimeError('lost')
        result = self.run_workflow()
        self.assertEqual(result['result'], 'failed')
        self.table.cleanup.assert_not_called()

    def test_interruption_preserved_after_cleanup(self):
        interrupted = KeyboardInterrupt()
        self.spark.run.side_effect = interrupted
        with self.assertRaises(KeyboardInterrupt) as got:
            self.run_workflow()
        self.assertIs(got.exception, interrupted)
        self.assertEqual(self.order, ['drain', 'access_cleanup', 'table_drop'])

    def test_fixture_drift_prevents_passed_result(self):
        self.fixture.side_effect = [{'rows': [[1, 'before']]}, {'rows': [[1, 'after']]}]
        self.assertEqual(self.run_workflow()['result'], 'failed')

    def test_cross_engine_failure_still_cleans_owned_resources(self):
        self.trino_output['identity']['snapshot_id'] += 1
        result = self.run_workflow()
        self.assertEqual(result['result'], 'failed')
        self.assertEqual(self.order, ['drain', 'access_cleanup', 'table_drop'])

    def test_cleanup_cancellation_is_not_hidden_by_prior_failure(self):
        self.spark.run.side_effect = RuntimeError('earlier error')
        interrupted = KeyboardInterrupt()
        self.access.cleanup.side_effect = interrupted
        with self.assertRaises(KeyboardInterrupt) as got:
            self.run_workflow()
        self.assertIs(got.exception, interrupted)
        self.table.cleanup.assert_not_called()

    def test_original_cancellation_survives_failed_final_journal(self):
        interrupted = KeyboardInterrupt()
        self.spark.run.side_effect = interrupted
        original_save = R.NATIVE.save
        def save(path, value):
            if value['result'] == 'interrupted':
                raise OSError('disk full')
            return original_save(path, value)
        with patch.object(R.NATIVE, 'save', side_effect=save):
            with self.assertRaises(KeyboardInterrupt) as got:
                self.run_workflow()
        self.assertIs(got.exception, interrupted)


if __name__ == '__main__':
    unittest.main()
