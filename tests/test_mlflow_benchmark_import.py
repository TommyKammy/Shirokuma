"""Offline SDK failure/replay tests. No MLflow SQLite, server, query, or model."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.mlflow import import_benchmarks as I

SPEC = importlib.util.spec_from_file_location('evaluation_import_test_helpers', ROOT / 'tests/test_local_mlflow_import.py')
HELPERS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPERS)


class FakeClient(HELPERS.FakeClient):
    """An existing unrelated evaluation experiment must never be searched/edited."""
    def __init__(self):
        super().__init__()
        self.evaluation_runs = {uuid.uuid4().hex: {'points': 1118, 'artifact': 'retained'} for _ in range(11)}
        self.inspections = []

    def get_experiment_by_name(self, name):
        assert name == I.EXPERIMENT
        return super().get_experiment_by_name(name)

    def create_experiment(self, name, **kwargs):
        assert name == I.EXPERIMENT
        return super().create_experiment(name, **kwargs)

    def search_runs(self, experiments, **kwargs):
        assert experiments == [self.experiment.experiment_id]
        return super().search_runs(experiments, **kwargs)

    def get_run(self, identity):
        assert identity not in self.evaluation_runs
        self.inspections.append(identity)
        return super().get_run(identity)


def group(token, engine, case):
    source = {
        'schema_version': 1, 'source_id': f'spark-benchmark/{token}/{engine}/{case}',
        'source_key': I.digest([token, engine, case]), 'run_token': token,
        'artifact_sha256': {'summary.json': 'a' * 64}, 'source_result': 'passed',
        'source_started_at': '2026-09-17T00:00:00.000000Z',
        'source_finished_at': '2026-09-17T00:01:00.000000Z',
        'engine': engine, 'case_id': case, 'measurement_complete': True,
        'comparison_eligible': True, 'expected_samples': 4, 'observed_samples': 4, 'unobserved_samples': 0,
        'observed_version': '483' if engine == 'trino' else '4.1.3',
        'expected_version': '483' if engine == 'trino' else '4.1.3',
        'iceberg_observed_version': None if engine == 'trino' else '1.11.0',
        'workload_id': 'tpch-sf0.1-lineitem-decimal-v1', 'scale_factor': '0.1',
        'snapshot_id': '8088807033386546123', 'table_uuid': str(uuid.uuid4()),
        'row_count': 600572, 'logical_sha256': 'b' * 64, 'expected_result_sha256': 'c' * 64,
        'conditions': {'measurement_scope': 'client-observed', 'cache': 'uncontrolled',
                       'engine_order': 'trino-then-spark', 'resource_bounds': {'driver': None, 'executor': None, 'trino': None},
                       'source_preparation': 'source-stream-and-CTAS-before-measurement', 'first_sample': 'first-observation-not-cold'},
        'timings': {'spark_session_startup_ns': 250000000, 'native_total_ns': 9000000000},
        'first_ns': 6000000000, 'repeat_median_ns': 2000000000,
        'cleanup_verified': True, 'query_drain_verified': True, 'limits': ['not-engine-only-comparison'],
        'inference_cost_estimate': None, 'model_usage': None,
        'samples': [],
    }
    for step, elapsed in enumerate((6000000000, 1000000000, 2000000000, 3000000000)):
        source['samples'].append({'sample_index': step, 'elapsed_ns': elapsed,
            'result_sha256': 'c' * 64, 'sql_sha256': 'd' * 64,
            'query_id': '20260917_000001_00001_abcde' if engine == 'trino' else None,
            'source_started_at': source['source_started_at'] if engine == 'trino' else None,
            'stats': {'elapsedTimeMillis': 1200 + step, 'wallTimeMillis': 20 + step,
                      'queuedTimeMillis': None, 'cpuTimeMillis': 10, 'processedRows': 600572,
                      'processedBytes': 123456, 'physicalInputBytes': None, 'state': 'FINISHED'} if engine == 'trino' else None})
    return source


class BenchmarkImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.state, self.artifacts = self.root / 'state', self.root / 'artifacts'
        self.state.mkdir(mode=0o700)
        self.artifacts.mkdir(mode=0o700)
        self.token, self.store_id = uuid.uuid4().hex, str(uuid.uuid4())
        self.groups = [group(self.token, engine, case) for engine in ('trino', 'spark') for case in ('q1_sums', 'q6')]
        self.client = FakeClient()
        self.initial_evaluations = copy.deepcopy(self.client.evaluation_runs)
        self.collect_patch = patch.object(I.REPORT, 'collect', side_effect=self.report)
        self.read_patch = patch.object(I.REPORT, 'read_run', side_effect=self.read)
        self.projection_patch = patch.object(I.REPORT, 'projection', side_effect=lambda run: copy.deepcopy(run['groups']))
        for item in (self.collect_patch, self.read_patch, self.projection_patch):
            item.start()
            self.addCleanup(item.stop)
        self.inventory_complete, self.eligible = True, True
        self.integrity, self.after_collect = 'valid', None
        self.collect_calls = 0

    def tearDown(self):
        self.assertEqual(self.client.evaluation_runs, self.initial_evaluations)
        self.temporary.cleanup()

    def read(self, token, **kwargs):
        self.assertEqual(token, self.token)
        return {'run_token': token, 'integrity': self.integrity, 'eligible': self.eligible,
                'source_result': self.groups[0]['source_result'], 'issues': [], 'groups': copy.deepcopy(self.groups)}

    def report(self, **kwargs):
        self.collect_calls += 1
        if self.after_collect:
            self.after_collect(self.collect_calls)
        return {'schema_version': 1, 'scope': 'saved-benchmark-only',
                'inventory': {'uuid_runs': 1, 'eligible': int(self.eligible), 'complete': self.inventory_complete},
                'runs': [{'run_token': self.token, 'integrity': self.integrity, 'eligible': self.eligible, 'issues': []}],
                'sources': copy.deepcopy(self.groups) if self.eligible else [],
                'result': 'complete' if self.inventory_complete else 'incomplete'}

    def importer(self, **kwargs):
        return I.Importer(self.client, state=self.state, artifacts=self.artifacts, store_id=self.store_id, **kwargs)

    def run_import(self, **kwargs):
        result, output = self.importer(**kwargs).run()
        self.assertEqual(json.loads(I.read_private(output / 'result.json')), result)
        return result

    def test_four_distinct_groups_repeat_no_write_and_retained_evaluations(self):
        first = self.run_import()
        self.assertEqual((first['result'], first['created_runs']), ('verified', 4))
        before = copy.deepcopy((self.client.runs, self.client.histories, self.client.calls))
        hashes = {p: I.read_private(p) for p in self.artifacts.rglob('safe-projection.json')}
        second = self.run_import()
        self.assertEqual((second['result'], second['created_runs'], second['added_metrics']), ('verified', 0, 0))
        self.assertEqual((self.client.runs, self.client.histories, self.client.calls), before)
        self.assertEqual({p: I.read_private(p) for p in hashes}, hashes)
        self.assertEqual(len(hashes), 4)
        self.assertFalse((self.state / 'import-index.json').exists())
        self.assertEqual({x.data.params['engine'] for x in self.client.runs.values()}, {'trino', 'spark'})

    def test_metrics_distinguish_client_server_and_keep_exact_snapshot_nulls(self):
        self.assertEqual(self.run_import()['result'], 'verified')
        for run in self.client.runs.values():
            self.assertEqual(run.data.params['snapshot_id'], '8088807033386546123')
            self.assertNotIn('snapshot_id', run.data.metrics)
            self.assertEqual(run.data.metrics['first_client_observed_ms'], 6000)
            self.assertEqual(run.data.metrics['repeat_median_client_observed_ms'], 2000)
            points = self.client.histories[run.info.run_id]
            self.assertEqual([p['step'] for p in points['client_observed_ms']], [0, 1, 2, 3])
            self.assertEqual('trino_server_wall_ms' in points, run.data.params['engine'] == 'trino')
            self.assertNotIn('trino_server_queued_ms', points)
            self.assertNotIn('inference_cost_estimate', points)
            path = self.artifacts / 'spark-benchmarks' / run.info.run_id / 'artifacts/safe-projection.json'
            safe = json.loads(I.read_private(path))
            self.assertIsNone(safe['model_usage'])
            self.assertIsNone(safe['inference_cost_estimate'])
            self.assertNotEqual(run.info.start_time, safe['source_started_at'])

    def test_partial_valid_failed_source_preserves_missing_metrics_and_failed_status(self):
        for source in self.groups:
            source.update(source_result='failed', measurement_complete=False, comparison_eligible=False,
                          observed_samples=1, unobserved_samples=3, repeat_median_ns=None, cleanup_verified=False)
            source['samples'] = source['samples'][:1]
        result = self.run_import()
        self.assertEqual(result['result'], 'verified')
        for run in self.client.runs.values():
            self.assertEqual(run.info.status, 'FAILED')
            self.assertNotIn('repeat_median_client_observed_ms', run.data.metrics)
            self.assertEqual(len(self.client.histories[run.info.run_id]['client_observed_ms']), 1)

    def test_invalid_inventory_is_saved_and_sdk_never_initialized(self):
        self.inventory_complete, self.eligible, self.integrity = False, False, 'invalid'
        factory = Mock(side_effect=AssertionError('SDK must remain lazy'))
        result, output = I.Importer(None, state=self.state, artifacts=self.artifacts,
                                    store_id=self.store_id, client_factory=factory).run()
        self.assertEqual(result['failure_code'], 'SOURCE_INVENTORY_INCOMPLETE')
        self.assertEqual(json.loads(I.read_private(output / 'sources.json'))['inventory']['runs'][0]['integrity'], 'invalid')
        factory.assert_not_called()

    def test_source_changed_or_removed_is_rejected_before_sdk_writes(self):
        self.assertEqual(self.run_import()['result'], 'verified')
        before = copy.deepcopy(self.client.calls)
        self.groups[0]['source_key'] = 'f' * 64
        self.assertEqual(self.run_import()['failure_code'], 'SOURCE_IDENTITY_CONFLICT')
        self.assertEqual(self.client.calls, before)
        self.eligible = False
        self.assertEqual(self.run_import()['failure_code'], 'IMPORTED_SOURCE_MISSING')
        self.assertEqual(self.client.calls, before)

    def test_duplicate_group_is_rejected_before_sdk(self):
        self.groups[-1] = copy.deepcopy(self.groups[0])
        self.assertEqual(self.run_import()['failure_code'], 'DUPLICATE_SOURCE')
        self.assertEqual(self.client.calls, [])

    def test_lost_create_reply_stays_unknown_without_adoption_or_recreate(self):
        self.client.lose_create = True
        self.assertEqual(self.run_import()['result'], 'failed')
        self.client.lose_create = False
        self.assertEqual(self.run_import()['failure_code'], 'RUN_CREATE_OUTCOME_UNKNOWN')
        self.assertEqual(self.client.calls.count('create_run'), 1)
        self.assertEqual(len(self.client.runs), 1)

    def test_partial_batch_resumes_only_absent_history_points(self):
        self.client.partial_batch = True
        self.assertEqual(self.run_import()['result'], 'failed')
        first_id = next(iter(self.client.runs))
        first_points = copy.deepcopy(self.client.histories[first_id])
        second = self.run_import()
        self.assertEqual(second['result'], 'verified')
        self.assertEqual(self.client.calls.count('create_run'), 4)
        for key, expected in first_points.items():
            actual = self.client.histories[first_id][key]
            for point in expected:
                self.assertEqual(actual.count(point), 1)
        self.assertEqual(self.run_import()['added_metrics'], 0)

    def test_duplicate_metric_history_cannot_be_hidden_by_latest_value(self):
        self.assertEqual(self.run_import()['result'], 'verified')
        points = next(iter(self.client.histories.values()))['client_observed_ms']
        points.append(copy.deepcopy(points[0]))
        self.assertEqual(self.run_import()['failure_code'], 'DUPLICATE_METRIC_HISTORY')

    def test_metric_history_changed_timestamp_is_rejected(self):
        self.assertEqual(self.run_import()['result'], 'verified')
        next(iter(self.client.histories.values()))['client_observed_ms'][0]['timestamp'] += 1
        self.assertEqual(self.run_import()['failure_code'], 'METRIC_HISTORY_CONFLICT')

    def test_latest_metric_value_matches_history_for_comparison_ui(self):
        self.assertEqual(self.run_import()['result'], 'verified')
        next(iter(self.client.runs.values())).data.metrics['client_observed_ms'] = 123
        self.assertEqual(self.run_import()['failure_code'], 'METRIC_LATEST_CONFLICT')

    def test_lost_experiment_reply_stays_pending_without_adoption(self):
        original = self.client.create_experiment
        def lose_reply(*args, **kwargs):
            original(*args, **kwargs)
            raise TimeoutError('reply lost')
        with patch.object(self.client, 'create_experiment', side_effect=lose_reply):
            self.assertEqual(self.run_import()['result'], 'failed')
        self.assertEqual(self.run_import()['failure_code'], 'EXPERIMENT_CREATE_UNKNOWN_OR_COLLISION')
        self.assertEqual(self.client.calls, ['create_experiment'])

    def test_foreign_run_is_not_adopted(self):
        self.assertEqual(self.run_import()['result'], 'verified')
        arbitrary = copy.deepcopy(next(iter(self.client.runs.values())))
        arbitrary.info.run_id = uuid.uuid4().hex
        self.client.runs[arbitrary.info.run_id] = arbitrary
        self.assertEqual(self.run_import()['failure_code'], 'UNINDEXED_OR_MISSING_RUN')

    def test_completed_artifact_missing_or_changed_is_conflict_not_repair(self):
        self.assertEqual(self.run_import()['result'], 'verified')
        artifact = next(self.artifacts.rglob('safe-projection.json'))
        original = I.read_private(artifact)
        artifact.write_bytes(b'{}\n')
        self.assertEqual(self.run_import()['failure_code'], 'ARTIFACT_CONFLICT')
        artifact.write_bytes(original)
        artifact.unlink()
        self.assertEqual(self.run_import()['failure_code'], 'COMPLETED_ARTIFACT_MISSING')

    def test_atomic_artifact_failure_can_resume_without_final_partial_file(self):
        original = I.E.os.rename
        failed = False
        def fail_once(source, target):
            nonlocal failed
            if str(target).endswith('/safe-projection.json') and not failed:
                failed = True
                raise OSError('disk failure')
            return original(source, target)
        with patch.object(I.E.os, 'rename', side_effect=fail_once):
            self.assertEqual(self.run_import()['result'], 'failed')
        self.assertEqual(list(self.artifacts.rglob('safe-projection.json')), [])
        self.assertEqual(list(self.artifacts.rglob('.pending-*')), [])
        self.assertEqual(self.run_import()['result'], 'verified')
        self.assertEqual(self.client.calls.count('create_run'), 4)

    def test_source_changes_during_import_never_reports_verified(self):
        self.client.after_batch = lambda: self.groups[0].update(source_key='e' * 64)
        self.assertEqual(self.run_import()['failure_code'], 'SOURCE_CHANGED')

    def test_guard_failure_before_mutation_does_not_create(self):
        guard = Mock(side_effect=I.ImportError('STORE_IDENTITY_CHANGED'))
        self.assertEqual(self.run_import(guard=guard)['failure_code'], 'STORE_IDENTITY_CHANGED')
        self.assertEqual(self.client.calls, [])

    def test_durable_create_intent_failure_prevents_run_creation(self):
        original = I.persist
        def fail(path, value):
            if path.name == 'benchmark-import-index.json' and value['runs']:
                raise OSError('intent failure')
            return original(path, value)
        with patch.object(I, 'persist', side_effect=fail):
            self.assertEqual(self.run_import()['result'], 'failed')
        self.assertEqual(self.client.calls.count('create_run'), 0)

    def test_original_cancellation_survives_final_receipt_write_failure(self):
        cancellation = KeyboardInterrupt()
        def interrupted():
            raise cancellation
        self.client.after_batch = interrupted
        original = I.persist
        def fail(path, value):
            if path.name == 'result.json':
                raise OSError('secondary disk failure')
            return original(path, value)
        with patch.object(I, 'persist', side_effect=fail):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.importer().run()
        self.assertIs(caught.exception, cancellation)

    def test_large_exact_counter_omitted_but_safe_artifact_keeps_it(self):
        self.groups[0]['samples'][0]['stats']['processedBytes'] = 2**53 + 1
        self.assertEqual(self.run_import()['result'], 'verified')
        run = next(r for r in self.client.runs.values() if r.data.params['engine'] == 'trino' and r.data.params['case_id'] == 'q1_sums')
        points = self.client.histories[run.info.run_id]['trino_server_processed_bytes']
        self.assertEqual([p['step'] for p in points], [1, 2, 3])
        source = json.loads(I.read_private(self.artifacts / 'spark-benchmarks' / run.info.run_id / 'artifacts/safe-projection.json'))
        self.assertEqual(source['samples'][0]['stats']['processedBytes'], 2**53 + 1)

    def test_symlink_index_is_rejected_without_sdk(self):
        target = self.state / 'retained.json'
        I.persist(target, {})
        (self.state / 'benchmark-import-index.json').symlink_to(target)
        with self.assertRaises((I.ImportError, OSError)):
            self.importer()
        self.assertEqual(self.client.calls, [])


class WorkerBoundaryTests(unittest.TestCase):
    def test_ambient_worker_rejected_before_sdk_import_or_store_lock(self):
        from tools.local_experiment.mlflow import runtime
        with patch.object(runtime, '_isolated_runtime', return_value={'ONLY': 'fixed'}), \
                patch.object(runtime, 'store_lock') as lock:
            with self.assertRaisesRegex(I.ImportError, 'WORKER_ENVIRONMENT_NOT_ISOLATED'):
                I.worker()
        lock.assert_not_called()

    def test_real_receipt_reader_to_four_sdk_runs_and_unchanged_reimport(self):
        spec = importlib.util.spec_from_file_location('benchmark_source_helpers', ROOT / 'tests/test_spark_benchmark_report.py')
        helpers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helpers)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            base, native, state, artifacts = (root / name for name in ('bench', 'native', 'state', 'artifacts'))
            state.mkdir(mode=0o700)
            artifacts.mkdir(mode=0o700)
            helpers.write_fixture(base, native)
            source_before = {p: p.read_bytes() for parent in (base, native) for p in parent.rglob('*') if p.is_file()}
            client, store = FakeClient(), str(uuid.uuid4())
            def run():
                return I.Importer(client, state=state, artifacts=artifacts, store_id=store,
                                  base=base, native_base=native).run()[0]
            first = run()
            self.assertEqual(first['result'], 'verified', first)
            self.assertEqual(first['created_runs'], 4)
            self.assertGreater(first['added_metrics'], 0)
            second = run()
            self.assertEqual((second['result'], second['created_runs'], second['added_metrics']), ('verified', 0, 0))
            self.assertEqual({p: p.read_bytes() for p in source_before}, source_before)
            # A valid projection cannot launder a modified underlying receipt.
            summary = base / helpers.TOKEN / 'summary.json'
            summary.write_bytes(summary.read_bytes().replace(b'"passed"', b'"unknown"', 1))
            mutations = copy.deepcopy(client.calls)
            self.assertEqual(run()['failure_code'], 'SOURCE_INVENTORY_INCOMPLETE')
            self.assertEqual(client.calls, mutations)


if __name__ == '__main__':
    unittest.main()
