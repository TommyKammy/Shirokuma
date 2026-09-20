"""Fake SDK and private saved fixtures only; no fit, live store, HTTP or worker."""
from copy import deepcopy
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.mlflow import import_comparisons as I
from tests.test_local_mlflow_import import FakeClient as BaseClient
from tests.test_notebook_lab_comparison_saved import COMPARISON_TOKEN, make_saved


class FakeClient(BaseClient):
    def __init__(self):
        super().__init__()
        self.lose_experiment = False
        self.lose_finish = False

    def create_experiment(self, *args, **kwargs):
        super().create_experiment(*args, **kwargs)
        self.experiment.experiment_id = '5'
        if self.lose_experiment:
            raise TimeoutError('private experiment failure')
        return '5'

    def create_run(self, *args, **kwargs):
        run = super().create_run(*args, **kwargs)
        run.info.end_time = None
        self.runs[run.info.run_id].info.end_time = None
        return run

    def set_terminated(self, identity, *, status, end_time):
        super().set_terminated(identity, status=status)
        self.runs[identity].info.end_time = end_time
        if self.lose_finish:
            raise TimeoutError('private finish failure')


class ComparisonImportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = make_saved(self)
        self.state = self.fixture.original.state
        self.artifacts = self.state / 'store/artifacts'
        self.store_id = self.fixture.original.receipt['store_id']
        self.client = FakeClient()
        for target in (patch.object(I.C, '_fit', side_effect=AssertionError('fit prohibited')),
                       patch.object(I.T, '_get', side_effect=AssertionError('HTTP prohibited'))):
            target.start()
            self.addCleanup(target.stop)

    def importer(self, **kwargs):
        return I.Importer(self.client, state=self.state, artifacts=self.artifacts, store_id=self.store_id,
                          comparison_token=COMPARISON_TOKEN, **kwargs)

    def original(self):
        paths = [self.fixture.directory / name for name in I.S.FILES]
        paths += [self.fixture.original.receipt_path, self.fixture.original.workload_path,
                  self.fixture.original.artifact, self.state / 'store.json']
        return {path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) for path in paths}

    def test_snapshot_and_pure_clone_projection_need_no_live_files_or_fit(self):
        before = self.original()
        source = I.Snapshot(COMPARISON_TOKEN).load()
        self.assertEqual(source['comparison'], self.fixture.comparison)
        self.assertEqual(source['source_training']['workload'], self.fixture.original.workload)
        self.assertEqual(len(source['source_files_sha256']), 10)
        self.assertIsNone(source['comparison_started_ms'])
        self.assertIsNone(source['comparison_finished_ms'])
        with patch.object(I.S, '_read', side_effect=AssertionError('live source read')), \
             patch.object(I.T, '_sources', side_effect=AssertionError('live training read')):
            self.assertEqual(I.validate_projection(source), source)
        self.assertEqual(before, self.original())

    def test_projection_rejects_coefficient_metric_provenance_and_invented_time(self):
        source = I.Snapshot(COMPARISON_TOKEN).load()
        changes = [lambda v: v['comparison']['candidate']['model']['coefficients'].__setitem__(0, 9.0),
                   lambda v: v['comparison']['candidate']['metrics']['test'].__setitem__('rmse', 0.0),
                   lambda v: v['comparison']['identity'].__setitem__('run_id', 'f' * 32),
                   lambda v: v['source_training']['workload']['dataset']['rows'][0].__setitem__(0, 99.0),
                   lambda v: v.__setitem__('comparison_started_ms', 1000),
                   lambda v: v.__setitem__('schema_version', True),
                   lambda v: v.__setitem__('model_sha256', '0' * 64),
                   lambda v: v['source_files_sha256'].__setitem__('training/result.json', '0' * 64)]
        for change in changes:
            value = deepcopy(source)
            change(value)
            with self.assertRaises(I.ImportError):
                I.validate_projection(value)

    def test_import_and_repeat_preserve_source_and_have_zero_second_logical_writes(self):
        before = self.original()
        result, output = self.importer().run()
        self.assertEqual(result['result'], 'verified', result)
        self.assertEqual((result['created_runs'], result['added_metrics']), (1, 18))
        self.assertTrue(result['artifact_added'] and result['source_unchanged'])
        run = self.client.runs[result['run_id']]
        self.assertEqual(run.info.experiment_id, '5')
        self.assertEqual(run.info.status, 'FINISHED')
        self.assertEqual(run.data.params['alpha'], '10.0')
        self.assertEqual(run.data.params['source_run_id'], self.fixture.comparison['identity']['run_id'])
        self.assertEqual(run.data.params['comparison_started_ms'], 'not-recorded')
        self.assertEqual(run.data.tags['shirokuma.time_semantics'], 'import-time-not-comparison-execution')
        history = self.client.histories[result['run_id']]
        self.assertEqual(len(history), 18)
        self.assertTrue(all(len(v) == 1 and v[0]['timestamp'] == run.info.start_time for v in history.values()))
        self.assertGreater(run.info.start_time, self.fixture.original.receipt['finished_ms'])
        artifact = self.artifacts / I.NAMESPACE / run.info.run_id / 'artifacts/safe-projection.json'
        source = I.N._parse(artifact.read_bytes())
        self.assertEqual(source, I.validate_projection(source))
        self.assertEqual(artifact.read_bytes(), (output / 'source.json').read_bytes())
        self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.state / I.INDEX_NAME).stat().st_mode & 0o777, 0o600)
        calls = list(self.client.calls)
        repeated, _ = self.importer().run()
        self.assertEqual(repeated['result'], 'verified', repeated)
        self.assertEqual(repeated['run_id'], result['run_id'])
        self.assertEqual((repeated['created_runs'], repeated['added_metrics'], repeated['added_params']), (0, 0, 0))
        self.assertFalse(repeated['artifact_added'])
        self.assertEqual(self.client.calls, calls)
        self.assertEqual(before, self.original())

    def test_lost_experiment_reply_stays_unknown_and_is_never_retried(self):
        self.client.lose_experiment = True
        first, _ = self.importer().run()
        self.assertEqual(first['result'], 'unknown')
        self.assertIsNone(first['created_runs'])
        self.assertEqual(first['phase'], 'create-experiment-pending')
        calls = list(self.client.calls)
        second, _ = self.importer().run()
        self.assertEqual(second['failure_code'], 'COMPARISON_IMPORT_PRIOR_OUTCOME_UNKNOWN')
        self.assertEqual(self.client.calls, calls)
        self.assertEqual(len(self.client.runs), 0)

    def test_lost_run_reply_stays_unknown_without_reconciliation_or_resend(self):
        self.client.lose_create = True
        first, _ = self.importer().run()
        self.assertEqual(first['result'], 'unknown')
        self.assertEqual(len(self.client.runs), 1)
        calls = list(self.client.calls)
        second, _ = self.importer().run()
        self.assertEqual(second['failure_code'], 'COMPARISON_IMPORT_PRIOR_OUTCOME_UNKNOWN')
        self.assertEqual(self.client.calls, calls)

    def test_partial_batch_and_lost_finish_never_get_resubmitted(self):
        for flag in ('partial_batch', 'lose_finish'):
            with self.subTest(flag=flag):
                setattr(self.client, flag, True)
                first, _ = self.importer().run()
                self.assertEqual(first['result'], 'unknown')
                calls = list(self.client.calls)
                second, _ = self.importer().run()
                self.assertEqual(second['failure_code'], 'COMPARISON_IMPORT_PRIOR_OUTCOME_UNKNOWN')
                self.assertEqual(self.client.calls, calls)
                # Separate fake store state for the other injected stage.
                if flag == 'partial_batch':
                    (self.state / I.INDEX_NAME).unlink()
                    self.client = FakeClient()

    def test_changed_source_after_batch_blocks_artifact_and_finish(self):
        self.client.after_batch = lambda: (self.fixture.directory / 'status.json').write_bytes(b'{}')
        result, _ = self.importer().run()
        self.assertEqual(result['result'], 'unknown')
        self.assertEqual(result['failure_code'], 'COMPARISON_IMPORT_SOURCE_CHANGED')
        self.assertNotIn('set_terminated', self.client.calls)
        self.assertFalse((self.artifacts / I.NAMESPACE).exists())

    def test_changed_private_file_identity_is_rejected_even_with_same_bytes(self):
        snapshot = I.Snapshot(COMPARISON_TOKEN)
        snapshot.load()
        target = self.fixture.directory / 'input.json'
        raw = target.read_bytes()
        target.unlink()
        target.write_bytes(raw)
        target.chmod(0o600)
        with self.assertRaisesRegex(I.ImportError, 'SOURCE_CHANGED'):
            snapshot.check()

    def test_wrong_store_and_invalid_source_do_not_initialize_sdk(self):
        factory = Mock(side_effect=AssertionError('SDK initialization'))
        importer = self.importer(client_factory=factory)
        importer.client = None
        importer.store_id = '00000000-0000-0000-0000-000000000000'
        result, _ = importer.run()
        self.assertEqual(result['failure_code'], 'COMPARISON_IMPORT_STORE_MISMATCH')
        factory.assert_not_called()
        (self.fixture.directory / 'status.json').write_bytes(b'{}')
        importer = self.importer(client_factory=factory)
        importer.client = None
        result, _ = importer.run()
        self.assertEqual(result['result'], 'failed')
        factory.assert_not_called()
        self.assertEqual(self.client.calls, [])

    def test_completed_history_or_artifact_change_is_not_repaired(self):
        result, _ = self.importer().run()
        identity = result['run_id']
        history = self.client.histories[identity]
        key = next(iter(history))
        history[key].append(deepcopy(history[key][0]))
        calls = list(self.client.calls)
        value, _ = self.importer().run()
        self.assertEqual(value['failure_code'], 'COMPARISON_IMPORT_HISTORY_CONFLICT')
        self.assertEqual(self.client.calls, calls)
        history[key].pop()
        artifact = self.artifacts / I.NAMESPACE / identity / 'artifacts/safe-projection.json'
        artifact.write_bytes(b'{}')
        value, _ = self.importer().run()
        self.assertEqual(value['failure_code'], 'COMPARISON_IMPORT_ARTIFACT_CONFLICT')
        self.assertEqual(artifact.read_bytes(), b'{}')
        self.assertEqual(self.client.calls, calls)

    def test_unowned_experiment_collision_and_malformed_index_no_sdk_write(self):
        self.client.experiment = NS(experiment_id='5')
        result, _ = self.importer().run()
        self.assertEqual(result['failure_code'], 'COMPARISON_IMPORT_EXPERIMENT_COLLISION')
        self.assertEqual(self.client.calls, [])
        for value in ([], dict(schema_version=True), dict(runs=[])):
            I.E.persist(self.state / I.INDEX_NAME, value)
            result, _ = self.importer().run()
            self.assertEqual(result['failure_code'], 'COMPARISON_IMPORT_INDEX_INVALID')
        self.assertEqual(self.client.calls, [])

    def test_code_change_stops_before_sdk_write(self):
        saved = I.sources()
        with patch.object(I, 'sources', side_effect=[saved, dict(saved, added='0' * 64)]):
            result, _ = self.importer().run()
        self.assertEqual(result['failure_code'], 'COMPARISON_IMPORT_CODE_CHANGED')
        self.assertEqual(self.client.calls, [])

    def test_first_cancellation_survives_secondary_receipt_save_failure(self):
        first = KeyboardInterrupt('first cancellation')
        self.client.after_batch = Mock(side_effect=first)
        persist = I.E.persist
        def failing(path, value):
            if path.name == 'result.json':
                raise OSError('secondary disk failure')
            return persist(path, value)
        with patch.object(I.E, 'persist', side_effect=failing):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.importer().run()
        self.assertIs(caught.exception, first)
        index = I.N._parse((self.state / I.INDEX_NAME).read_bytes())
        self.assertFalse(index['runs'][COMPARISON_TOKEN]['completed'])
        self.assertEqual(index['runs'][COMPARISON_TOKEN]['phase'], 'log-batch-pending')

    def test_successful_sdk_write_cannot_publish_success_when_receipt_save_fails(self):
        persist = I.E.persist
        def failing(path, value):
            if path.name == 'result.json':
                raise OSError('disk failure')
            return persist(path, value)
        with patch.object(I.E, 'persist', side_effect=failing), self.assertRaises(OSError):
            self.importer().run()
        self.assertEqual(len(self.client.runs), 1)

    def test_plan_is_read_only_and_parent_uses_fixed_sdk_environment(self):
        from tools.local_experiment.mlflow import runtime as R
        before = self.original()
        output = io.StringIO()
        with patch('sys.stdout', output), patch.object(I.subprocess, 'run', side_effect=AssertionError('worker')):
            self.assertEqual(I.main(['--comparison-token', COMPARISON_TOKEN, '--plan']), 0)
        self.assertEqual(json.loads(output.getvalue())['result'], 'verified')
        self.assertEqual(before, self.original())
        self.assertFalse((self.state / I.FOLDER).exists())
        with patch.object(R, 'require_stopped', return_value={}), \
             patch.object(I.subprocess, 'run', return_value=NS(returncode=0)) as run:
            self.assertEqual(I.main(['--comparison-token', COMPARISON_TOKEN]), 0)
        self.assertEqual(run.call_args.args[0], [str(R.VENV_PYTHON), '-I', '-B', str(Path(I.__file__).resolve()),
                                               '--worker', '--comparison-token', COMPARISON_TOKEN])
        self.assertEqual(run.call_args.kwargs['env'], R.environment())
        self.assertEqual(run.call_args.kwargs['timeout'], 180)

    def test_invalid_token_is_rejected_before_allocation(self):
        for token in ('', '../x', True, None, COMPARISON_TOKEN + '\n'):
            with self.assertRaises(I.ImportError):
                I.normalize_token(token)
        self.assertFalse((self.state / I.FOLDER).exists())

    def recorded(self):
        return I._recorded(COMPARISON_TOKEN, state=self.state, artifacts=self.artifacts, store_id=self.store_id)

    def test_cold_recorded_returns_original_receipt_with_no_sdk_runtime_or_writes(self):
        self.assertIsNone(self.recorded())
        result, _ = self.importer().run()
        before = {p: p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        with patch.object(I.Importer, 'experiment', side_effect=AssertionError('SDK read')), \
             patch.object(I.Importer, 'save', side_effect=AssertionError('write')):
            self.assertEqual(self.recorded(), result)
        repeated, _ = self.importer().run()
        self.assertNotEqual(repeated['token'], result['token'])
        self.assertEqual(self.recorded(), result)
        self.assertTrue(all(p.read_bytes() == raw for p, raw in before.items()))

    def test_cold_recorded_rejects_unknown_and_modified_import_receipts(self):
        result, output = self.importer().run()
        I.E.persist(output / 'result.json', dict(result, added_metrics=17))
        with self.assertRaisesRegex(I.ImportError, 'RECEIPT_INVALID'):
            self.recorded()
        I.E.persist(output / 'result.json', result)
        index = I.N._parse((self.state / I.INDEX_NAME).read_bytes())
        index['runs'][COMPARISON_TOKEN].update(completed=False, phase='finish-pending')
        I.E.persist(self.state / I.INDEX_NAME, index)
        with self.assertRaisesRegex(I.ImportError, 'PRIOR_OUTCOME_UNKNOWN'):
            self.recorded()

    def test_public_recorded_checks_owned_store_without_status_or_process_inspection(self):
        from tools.local_experiment.mlflow import runtime as R
        result, _ = self.importer().run()
        with patch.object(R, 'require_store', return_value=dict(store_id=self.store_id)) as marker, \
             patch.object(R, 'STATE', self.state), patch.object(R, 'ARTIFACT_ROOT', self.artifacts), \
             patch.object(R, 'status', side_effect=AssertionError('live runtime')), \
             patch.object(R, 'processes', side_effect=AssertionError('live processes')):
            self.assertEqual(I.recorded(COMPARISON_TOKEN), result)
        marker.assert_called_once_with()

    def test_sdk_worker_initializes_client_only_inside_stopped_store_lock(self):
        from tools.local_experiment.mlflow import runtime as R
        locked, calls = [False], []
        @contextmanager
        def lock():
            self.assertFalse(locked[0])
            locked[0] = True
            calls.append('lock')
            try:
                yield
            finally:
                locked[0] = False
                calls.append('unlock')
        def stopped():
            self.assertTrue(locked[0])
            calls.append('stopped')
            return dict(store_id=self.store_id)
        def fake_run(importer):
            self.assertTrue(locked[0])
            self.assertIsNone(importer.client)
            importer.runtime_guard()
            importer.client_factory()
            return dict(result='verified'), I.ROOT / 'tmp/fake-evidence'
        def client(**kwargs):
            self.assertTrue(locked[0])
            self.assertEqual(kwargs, dict(tracking_uri=R.BACKEND_URI, registry_uri=R.BACKEND_URI))
            calls.append('client')
            return self.client
        fake_mlflow = NS(MlflowClient=client, entities=NS())
        with patch.dict('sys.modules', {'mlflow': fake_mlflow}), \
             patch.object(R, '_isolated_runtime', return_value=dict(os.environ)), \
             patch.object(R, 'store_lock', side_effect=lock), \
             patch.object(R, 'require_stopped', side_effect=stopped), \
             patch.object(I.os, 'chdir') as chdir, patch.object(I.os, 'umask'), \
             patch.object(I.Importer, 'run', fake_run), patch('sys.stdout', io.StringIO()):
            self.assertEqual(I.worker(COMPARISON_TOKEN), 0)
        chdir.assert_called_once_with(R.STORE)
        self.assertEqual(calls, ['lock', 'stopped', 'stopped', 'client', 'unlock'])


if __name__ == '__main__':
    unittest.main()
