"""Training Tracking boundaries with injected work and an in-memory SDK."""
from __future__ import annotations

import builtins
import copy
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.mlflow import train as T


def workload():
    """Deliberately tiny injected workload; the real fitter has separate tests."""
    dataset = {'feature_names': ['x', 'y'], 'target_name': 'target',
               'rows': [[1.0, 2.0, 3.0], [2.0, 3.0, 5.0], [3.0, 4.0, 7.0]]}
    split = {'seed': 42, 'train_indices': [0, 1], 'test_indices': [2]}
    return dict(schema_version=1, profile='injected-training-fixture', dataset=dataset,
                split=split, params={'alpha': 1.0, 'fit_intercept': True, 'solver': 'svd'},
                model={'coefficients': [1.0, 1.0], 'intercept': 0.0},
                baseline={'prediction': 4.0}, test_predictions=[7.0],
                metrics={name: {'mae': offset + .1, 'rmse': offset + .2, 'r2': .8 - offset}
                         for name, offset in [('train', 0), ('test', .1), ('baseline_test', .4)]},
                hashes={'dataset_sha256': T.E.digest(dataset), 'split_sha256': T.E.digest(split)},
                versions={'python': '3.12.0', 'numpy': '2.5.3', 'scikit_learn': '1.9.1'})


class FakeClient:
    """Only owned SDK records exist here; hooks can fail after durable effects."""

    def __init__(self):
        self.experiment = None
        self.runs = {}
        self.histories = {}
        self.calls = []
        self.before = lambda action: None
        self.after = lambda action: None
        self.on_read = lambda run: run
        self.partial_batch = False

    def begin(self, action):
        self.before(action)
        self.calls.append(action)

    def get_experiment_by_name(self, name):
        return copy.deepcopy(self.experiment)

    def create_experiment(self, name, *, artifact_location, tags):
        self.begin('create_experiment')
        self.experiment = NS(experiment_id='7', name=name, artifact_location=artifact_location,
                             tags=copy.deepcopy(tags), lifecycle_stage='active')
        self.after('create_experiment')
        return '7'

    def get_experiment(self, identity):
        return copy.deepcopy(self.experiment)

    def create_run(self, experiment, *, start_time, tags):
        self.begin('create_run')
        identity = uuid.uuid4().hex
        run = NS(info=NS(run_id=identity, experiment_id=experiment, start_time=start_time,
                         end_time=None, artifact_uri=self.experiment.artifact_location + '/' + identity + '/artifacts',
                         lifecycle_stage='active', status='RUNNING'),
                 data=NS(params={}, metrics={}, tags=copy.deepcopy(tags)))
        self.runs[identity] = run
        self.histories[identity] = {}
        self.after('create_run')
        return copy.deepcopy(run)

    def get_run(self, identity):
        return self.on_read(copy.deepcopy(self.runs[identity]))

    def get_metric_history(self, identity, key):
        return [NS(**point) for point in copy.deepcopy(self.histories[identity].get(key, []))]

    def log_batch(self, identity, *, metrics, params, synchronous):
        self.begin('log_batch')
        if synchronous is not True:
            raise AssertionError('asynchronous metric delivery is forbidden')
        for parameter in params:
            if not isinstance(parameter, dict):
                parameter = vars(parameter)
            self.runs[identity].data.params[parameter['key']] = parameter['value']
        for point in metrics:
            if not isinstance(point, dict):
                point = vars(point)
            self.histories[identity].setdefault(point['key'], []).append(copy.deepcopy(point))
            self.runs[identity].data.metrics[point['key']] = point['value']
            if self.partial_batch:
                raise TimeoutError('private partial-batch body')
        self.after('log_batch')

    def set_terminated(self, identity, *, status, end_time):
        self.begin('set_terminated')
        self.runs[identity].info.status = status
        self.runs[identity].info.end_time = end_time
        self.after('set_terminated')


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.state, self.artifacts = self.root / 'state', self.root / 'artifacts'
        self.state.mkdir(mode=0o700)
        self.artifacts.mkdir(mode=0o700)
        self.store_id = str(uuid.uuid4())
        self.client = FakeClient()
        self.value = workload()
        self.train = Mock(side_effect=lambda: copy.deepcopy(self.value))
        self.validate = Mock()
        self.guard = Mock()
        self.source = {'train.py': 'a' * 64, 'training_workload.py': 'b' * 64}
        self.source_patch = patch.object(T, 'sources', side_effect=lambda: copy.deepcopy(self.source))
        self.source_patch.start()
        self.addCleanup(self.source_patch.stop)
        self.trainer = T.Trainer(self.client, state=self.state, artifacts=self.artifacts,
                                 store_id=self.store_id, train=self.train,
                                 validate=self.validate, guard=self.guard)
        self.intent_phases = []
        self.client.before = self.check_durable_intent

    def check_durable_intent(self, action):
        path = self.trainer.output / 'result.json'
        saved = json.loads(T.E.read_private(path))
        expected = {'create_experiment': 'create-experiment-pending', 'create_run': 'create-run-pending',
                    'log_batch': 'log-batch-pending', 'set_terminated': 'finish-pending'}[action]
        self.assertEqual(saved['phase'], expected)
        self.assertEqual(saved['result'], 'running')
        self.assertEqual(saved['token'], self.trainer.result['token'])
        self.assertEqual(saved['workload_sha256'], T.E.digest(self.value))
        self.assertEqual(T.E.read_private(self.trainer.output / 'workload.json'), T.E.canonical(self.value))
        self.intent_phases.append(expected)

    def artifact(self):
        return (self.artifacts / 'supervised-training' / self.trainer.result['run_id'] /
                'artifacts' / 'safe-projection.json')

    def run_trainer(self):
        result, output = self.trainer.run()
        self.assertEqual(json.loads(T.E.read_private(output / 'result.json')), result)
        self.assertEqual(self.train.call_count, 1)
        return result, output

    def existing_experiment(self):
        self.client.experiment = NS(experiment_id='7', name=T.EXPERIMENT, lifecycle_stage='active',
            artifact_location=(self.artifacts / 'supervised-training').as_uri(),
            tags={'shirokuma.owner': T.OWNER, 'shirokuma.store_id': self.store_id})

    def test_success_has_one_fit_exact_tracking_and_private_artifact(self):
        result, output = self.run_trainer()
        self.assertEqual(result['result'], 'verified')
        self.assertEqual(result['phase'], 'verified')
        self.assertIsNone(result['failure_code'])
        self.assertEqual(result['source_sha256'], self.source)
        self.assertEqual(self.client.calls, ['create_experiment', 'create_run', 'log_batch', 'set_terminated'])
        run = self.client.runs[result['run_id']]
        self.assertEqual(run.info.status, 'FINISHED')
        self.assertEqual(run.info.end_time, result['finished_ms'])
        self.assertEqual(run.data.params['fit_intercept'], 'true')
        self.assertEqual(run.data.params['train_rows'], '2')
        self.assertEqual(run.data.params['test_rows'], '1')
        self.assertEqual(run.data.params['feature_names'], 'x,y')
        self.assertEqual(run.data.params['version_scikit_learn'], '1.9.1')
        expected = {group + '_' + key: value for group, metrics in self.value['metrics'].items()
                    for key, value in metrics.items()}
        self.assertEqual(run.data.metrics, expected)
        self.assertEqual(len(expected), 9)
        for key, history in self.client.histories[result['run_id']].items():
            self.assertEqual(history, [dict(key=key, value=expected[key], step=0,
                                            timestamp=result['training_finished_ms'])])
        self.assertEqual(T.E.read_private(self.artifact()), T.E.canonical(self.value))
        self.assertEqual(T.E.read_private(output / 'workload.json'), self.artifact().read_bytes())
        self.assertEqual(self.validate.call_count, 3)
        for path in self.root.rglob('*'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_injected_workload_does_not_import_training_or_mlflow_sdk(self):
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.startswith(('mlflow', 'numpy', 'sklearn')) or name.endswith('training_workload'):
                raise AssertionError('runtime dependency imported by injected test')
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=guarded):
            result, _ = self.run_trainer()
        self.assertEqual(result['result'], 'verified')

    def test_matching_existing_experiment_is_reused(self):
        self.existing_experiment()
        result, _ = self.run_trainer()
        self.assertEqual(result['result'], 'verified')
        self.assertEqual(self.client.calls, ['create_run', 'log_batch', 'set_terminated'])

    def test_entity_construction_uses_explicit_synchronous_batch(self):
        metric, param = Mock(side_effect=lambda **value: NS(**value)), Mock(side_effect=lambda **value: NS(**value))
        self.trainer.entities = NS(Metric=metric, Param=param)
        result, _ = self.run_trainer()
        self.assertEqual(result['result'], 'verified')
        self.assertEqual(metric.call_count, 9)
        self.assertEqual(param.call_count, len(self.client.runs[result['run_id']].data.params))

    def test_created_experiment_readback_id_must_match_create_reply(self):
        original = self.client.get_experiment
        def get(identity):
            experiment = original(identity)
            experiment.experiment_id = '8'
            return experiment
        self.client.get_experiment = get
        result, _ = self.run_trainer()
        self.assertEqual(result['result'], 'failed')
        self.assertEqual(self.client.calls, ['create_experiment'])

    def test_foreign_or_inexact_experiment_is_never_mutated(self):
        mutations = {'name': 'foreign', 'experiment_id': '../escape', 'lifecycle_stage': 'deleted',
                     'artifact_location': (self.root / 'elsewhere').as_uri(), 'tags': {'extra': 'tag'}}
        for key, value in mutations.items():
            with self.subTest(field=key):
                self.existing_experiment()
                setattr(self.client.experiment, key, value)
                before = copy.deepcopy(vars(self.client.experiment))
                self.train.reset_mock()
                result, _ = self.run_trainer()
                self.assertEqual(result['failure_code'], 'TRAINING_EXPERIMENT_CONFLICT')
                self.assertEqual(self.client.calls, [])
                self.assertEqual(vars(self.client.experiment), before)

    def test_invalid_created_run_is_rejected_before_content_write(self):
        original = self.client.create_run
        def create(*args, **kwargs):
            run = original(*args, **kwargs)
            run.info.artifact_uri = (self.root / 'foreign').as_uri()
            return run
        self.client.create_run = create
        result, _ = self.run_trainer()
        self.assertEqual(result['failure_code'], 'TRAINING_CREATED_RUN_CONFLICT')
        self.assertEqual(self.client.calls, ['create_experiment', 'create_run'])
        self.assertFalse((self.artifacts / 'supervised-training').exists())

    def test_unsafe_run_id_is_rejected_before_artifact_path_construction(self):
        original = self.client.create_run
        def create(*args, **kwargs):
            run = original(*args, **kwargs)
            run.info.run_id = '../escape'
            return run
        self.client.create_run = create
        result, _ = self.run_trainer()
        self.assertEqual(result['failure_code'], 'TRAINING_RUN_ID_INVALID')
        self.assertIsNone(result['run_id'])
        self.assertEqual(self.client.calls, ['create_experiment', 'create_run'])

    def test_training_error_never_allocates_remote_experiment(self):
        self.train.side_effect = RuntimeError('private fitter error')
        result, output = self.run_trainer()
        self.assertEqual(result['failure_code'], 'TRAINING_FAILED')
        self.assertEqual(result['phase'], 'training')
        self.assertEqual(self.client.calls, [])
        self.assertNotIn('private fitter error', (output / 'result.json').read_text())

    def test_workload_validation_failure_prevents_all_sdk_writes(self):
        self.validate.side_effect = T.E.ImportError('INVALID_TEST_WORKLOAD')
        result, output = self.run_trainer()
        self.assertEqual(result['failure_code'], 'INVALID_TEST_WORKLOAD')
        self.assertEqual(self.client.calls, [])
        self.assertFalse((output / 'workload.json').exists())

    def lost_reply(self, action):
        def after(actual):
            if actual == action:
                raise TimeoutError('private reply-loss body')
        self.client.after = after
        result, output = self.run_trainer()
        self.assertEqual(result['result'], 'failed')
        self.assertEqual(result['failure_code'], 'TRAINING_FAILED')
        self.assertEqual(self.client.calls.count(action), 1)
        self.assertEqual(self.client.calls[-1], action)
        self.assertNotIn('private reply-loss body', (output / 'result.json').read_text())
        return result

    def test_experiment_create_reply_loss_keeps_pending_without_retry(self):
        result = self.lost_reply('create_experiment')
        self.assertEqual(result['phase'], 'create-experiment-pending')
        self.assertIsNone(result['experiment_id'])
        self.assertIsNotNone(self.client.experiment)
        self.assertEqual(self.client.runs, {})

    def test_run_create_reply_loss_keeps_remote_run_without_retry(self):
        result = self.lost_reply('create_run')
        self.assertEqual(result['phase'], 'create-run-pending')
        self.assertIsNone(result['run_id'])
        self.assertEqual(len(self.client.runs), 1)
        self.assertEqual(next(iter(self.client.runs.values())).info.status, 'RUNNING')

    def test_log_batch_reply_loss_does_not_replay_or_terminate(self):
        result = self.lost_reply('log_batch')
        self.assertEqual(result['phase'], 'log-batch-pending')
        self.assertEqual(len(self.client.runs[result['run_id']].data.metrics), 9)
        self.assertEqual(self.client.runs[result['run_id']].info.status, 'RUNNING')
        self.assertFalse(self.artifact().exists())

    def test_partial_batch_keeps_partial_history_without_repair_or_termination(self):
        self.client.partial_batch = True
        result, _ = self.run_trainer()
        self.assertEqual(result['phase'], 'log-batch-pending')
        self.assertEqual(result['result'], 'failed')
        self.assertEqual(self.client.calls, ['create_experiment', 'create_run', 'log_batch'])
        self.assertEqual(len(self.client.runs[result['run_id']].data.metrics), 1)
        self.assertFalse(self.artifact().exists())

    def test_termination_reply_loss_keeps_finished_record_unverified(self):
        result = self.lost_reply('set_terminated')
        self.assertEqual(result['phase'], 'finish-pending')
        self.assertEqual(self.client.runs[result['run_id']].info.status, 'FINISHED')
        self.assertTrue(self.artifact().is_file())

    def test_intent_save_failure_prevents_sdk_write(self):
        persist = T.E.persist
        def fail_pending(path, value):
            if path.name == 'result.json' and value['phase'] == 'create-run-pending':
                raise OSError('private durable intent failure')
            return persist(path, value)
        with patch.object(T.E, 'persist', side_effect=fail_pending):
            with self.assertRaises(OSError):
                self.trainer.run()
        self.assertEqual(self.train.call_count, 1)
        self.assertEqual(self.client.calls, ['create_experiment'])
        saved = json.loads(T.E.read_private(self.trainer.output / 'result.json'))
        self.assertEqual(saved['phase'], 'experiment-created')

    def test_source_change_after_training_prevents_sdk_writes(self):
        def train():
            self.source['train.py'] = 'c' * 64
            return copy.deepcopy(self.value)
        self.train.side_effect = train
        result, _ = self.run_trainer()
        self.assertEqual(result['failure_code'], 'TRAINING_SOURCE_CHANGED')
        self.assertEqual(self.client.calls, [])

    def test_source_change_after_create_run_prevents_batch(self):
        def after(action):
            if action == 'create_run':
                self.source['train.py'] = 'c' * 64
        self.client.after = after
        result, _ = self.run_trainer()
        self.assertEqual(result['failure_code'], 'TRAINING_SOURCE_CHANGED')
        self.assertEqual(self.client.calls, ['create_experiment', 'create_run'])
        self.assertEqual(self.client.runs[result['run_id']].data.metrics, {})

    def test_source_change_while_finish_intent_is_saved_prevents_termination(self):
        persist = T.E.persist
        def mutate_source(path, value):
            persist(path, value)
            if path.name == 'result.json' and value['phase'] == 'finish-pending':
                self.source['train.py'] = 'c' * 64
        with patch.object(T.E, 'persist', side_effect=mutate_source):
            result, _ = self.run_trainer()
        self.assertEqual(result['failure_code'], 'TRAINING_SOURCE_CHANGED')
        self.assertNotIn('set_terminated', self.client.calls)

    def test_first_cancellation_is_preserved_even_if_failure_save_also_cancels(self):
        original, later = KeyboardInterrupt('first'), KeyboardInterrupt('later')
        self.train.side_effect = original
        persist = T.E.persist
        def fail_save(path, value):
            if path.name == 'result.json' and value['result'] == 'interrupted':
                raise later
            return persist(path, value)
        with patch.object(T.E, 'persist', side_effect=fail_save):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.trainer.run()
        self.assertIs(caught.exception, original)
        self.assertEqual(self.client.calls, [])

    def test_cancel_after_batch_effect_preserves_original_without_cleanup_writes(self):
        original = KeyboardInterrupt('first private cancellation')
        def after(action):
            if action == 'log_batch':
                raise original
        self.client.after = after
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.trainer.run()
        self.assertIs(caught.exception, original)
        saved = json.loads(T.E.read_private(self.trainer.output / 'result.json'))
        self.assertEqual(saved['result'], 'interrupted')
        self.assertEqual(saved['phase'], 'log-batch-pending')
        self.assertEqual(self.client.calls, ['create_experiment', 'create_run', 'log_batch'])

    def test_symlink_artifact_parent_is_rejected_and_target_untouched(self):
        retained = self.root / 'retained'
        retained.mkdir(mode=0o700)
        sentinel = retained / 'keep'
        sentinel.write_bytes(b'retained')
        sentinel.chmod(0o600)
        (self.artifacts / 'supervised-training').symlink_to(retained, target_is_directory=True)
        result, _ = self.run_trainer()
        self.assertEqual(result['failure_code'], 'UNSAFE_PRIVATE_DIRECTORY')
        self.assertEqual(list(retained.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b'retained')
        self.assertNotIn('set_terminated', self.client.calls)

    def test_preexisting_artifact_bytes_are_never_overwritten(self):
        def after(action):
            if action == 'log_batch':
                path = self.artifact()
                for directory in [path.parents[2], path.parents[1], path.parent]:
                    directory.mkdir(mode=0o700)
                path.write_bytes(b'"retained foreign artifact"\n')
                path.chmod(0o600)
        self.client.after = after
        result, _ = self.run_trainer()
        self.assertEqual(result['failure_code'], 'ARTIFACT_CONFLICT')
        self.assertEqual(self.artifact().read_bytes(), b'"retained foreign artifact"\n')
        self.assertNotIn('set_terminated', self.client.calls)

    def test_artifact_byte_change_after_write_is_detected(self):
        original = self.client.get_run
        def altered(identity):
            if self.artifact().exists():
                self.artifact().write_bytes(b'{}\n')
            return original(identity)
        self.client.get_run = altered
        result, _ = self.run_trainer()
        self.assertEqual(result['failure_code'], 'TRAINING_ARTIFACT_CONFLICT')
        self.assertNotIn('set_terminated', self.client.calls)

    def test_artifact_symlink_hardlink_and_public_mode_are_rejected(self):
        retained = self.root / 'retained.json'
        retained.write_bytes(T.E.canonical(self.value))
        retained.chmod(0o600)
        for kind in ('symlink', 'hardlink', 'public-mode'):
            with self.subTest(kind=kind):
                self.client.calls.clear()
                self.train.reset_mock()
                def after(action):
                    if action != 'log_batch':
                        return
                    path = self.artifact()
                    for directory in [path.parents[2], path.parents[1], path.parent]:
                        directory.mkdir(mode=0o700, exist_ok=True)
                    if kind == 'symlink':
                        path.symlink_to(retained)
                    elif kind == 'hardlink':
                        os.link(retained, path)
                    else:
                        path.write_bytes(T.E.canonical(self.value))
                        path.chmod(0o644)
                self.client.after = after
                result, _ = self.run_trainer()
                self.assertEqual(result['failure_code'], 'UNSAFE_PRIVATE_FILE')
                self.assertEqual(retained.read_bytes(), T.E.canonical(self.value))
                self.assertNotIn('set_terminated', self.client.calls)

    def test_saved_workload_change_is_detected_before_termination(self):
        def after(action):
            if action == 'log_batch':
                (self.trainer.output / 'workload.json').write_bytes(b'{}\n')
        self.client.after = after
        result, _ = self.run_trainer()
        self.assertEqual(result['failure_code'], 'TRAINING_WORKLOAD_CHANGED')
        self.assertNotIn('set_terminated', self.client.calls)

    def test_run_identity_readback_must_match_before_termination(self):
        def read(run):
            run.info.end_time = 123
            return run
        self.client.on_read = read
        result, _ = self.run_trainer()
        self.assertEqual(result['failure_code'], 'TRAINING_RUN_IDENTITY_CONFLICT')
        self.assertNotIn('set_terminated', self.client.calls)

    def test_metric_latest_and_history_are_both_checked(self):
        def after(action):
            if action == 'log_batch':
                identity = self.trainer.result['run_id']
                self.client.runs[identity].data.metrics['test_mae'] += .1
        self.client.after = after
        result, _ = self.run_trainer()
        self.assertEqual(result['failure_code'], 'TRAINING_RUN_CONTENT_CONFLICT')
        self.assertNotIn('set_terminated', self.client.calls)

    def test_duplicate_metric_history_is_rejected_even_when_latest_matches(self):
        def after(action):
            if action == 'log_batch':
                history = self.client.histories[self.trainer.result['run_id']]['test_mae']
                history.append(copy.deepcopy(history[0]))
        self.client.after = after
        result, _ = self.run_trainer()
        self.assertEqual(result['failure_code'], 'TRAINING_METRIC_HISTORY_CONFLICT')
        self.assertNotIn('set_terminated', self.client.calls)

    def test_metric_history_timestamp_must_match(self):
        def after(action):
            if action == 'log_batch':
                self.client.histories[self.trainer.result['run_id']]['test_mae'][0]['timestamp'] += 1
        self.client.after = after
        result, _ = self.run_trainer()
        self.assertEqual(result['failure_code'], 'TRAINING_METRIC_HISTORY_CONFLICT')
        self.assertNotIn('set_terminated', self.client.calls)

    def test_metric_history_boolean_step_is_not_an_integer_step(self):
        def after(action):
            if action == 'log_batch':
                self.client.histories[self.trainer.result['run_id']]['test_mae'][0]['step'] = False
        self.client.after = after
        result, _ = self.run_trainer()
        self.assertEqual(result['failure_code'], 'TRAINING_METRIC_HISTORY_CONFLICT')
        self.assertNotIn('set_terminated', self.client.calls)

    def test_post_termination_readback_failure_never_marks_verified(self):
        def after(action):
            if action == 'set_terminated':
                self.client.runs[self.trainer.result['run_id']].data.tags['unexpected'] = 'tag'
        self.client.after = after
        result, _ = self.run_trainer()
        self.assertEqual(result['result'], 'failed')
        self.assertEqual(result['phase'], 'finish-pending')
        self.assertEqual(result['failure_code'], 'TRAINING_RUN_CONTENT_CONFLICT')
        self.assertEqual(self.client.calls.count('set_terminated'), 1)


if __name__ == '__main__':
    unittest.main()
