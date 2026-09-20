"""Sealed lake-source tracking with fake SDK writes and injected fit results."""
from contextlib import nullcontext, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.lake_training import tracking as T
from tools.local_experiment.mlflow import runtime as R
from tests.test_mlflow_training import FakeClient

TOKEN, QUERY_TOKEN = 'a' * 32, 'b' * 32
STORE_ID = '00000000-0000-4000-8000-000000000002'


def observation():
    rows = []
    for i in reversed(range(64)):
        x, y, z = float(i), float(i % 7), float(i % 3)
        rows.append([{'type': 'bigint', 'value': str(i)},
                     *[{'type': 'double', 'value': repr(value)} for value in (x, y, z, 2*x-y+z)]])
    return dict(schema_version=1, table='iceberg.local_experiment.training_' + TOKEN,
                table_uuid='00000000-0000-4000-8000-000000000001', schema_sha256='c' * 64,
                snapshot_id='8088807033386546123',
                columns=[dict(name=name, type=kind, nullable=True) for name, kind in
                         [('row_id', 'bigint'), ('x1', 'double'), ('x2', 'double'), ('x3', 'double'), ('target', 'double')]],
                rows=rows, row_count=64, fetched_row_count=64, truncated=False, requested_limit=100,
                ordering='unspecified', rows_sha256=T.N.C.digest(rows))


def injected_workload(source):
    dataset = dict(feature_names=['x1', 'x2', 'x3'], target_name='target',
                   rows=[[float(cell['value']) for cell in row[1:]] for row in
                         sorted(source['observation']['rows'], key=lambda row: int(row[0]['value']))])
    split = dict(seed=1730, train_indices=source['split_row_ids']['train'], test_indices=source['split_row_ids']['test'])
    return dict(schema_version=1, profile=T.L.PROFILE, lake_input=deepcopy(source), dataset=dataset,
                split=split, params=deepcopy(T.L.PARAMS), model={'coefficients': [2.0, -1.0, 1.0], 'intercept': 0.0},
                baseline={'prediction': 1.0}, test_predictions=[1.0]*16,
                metrics={key: {'mae': .1, 'rmse': .2, 'r2': .9} for key in ('train', 'test', 'baseline_test')},
                hashes={'dataset_sha256': T.E.digest(dataset), 'split_sha256': T.E.digest(split)},
                versions={'python': '3.12.12', 'numpy': '2.5.3', 'scikit_learn': '1.9.1'})


class TrackingTests(unittest.TestCase):
    def setUp(self):
        previous_umask = os.umask(0o022)
        self.addCleanup(os.umask, previous_umask)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source_root = self.root / 'sources'
        self.directory = self.source_root / TOKEN
        self.source_root.mkdir(mode=0o700)
        self.directory.mkdir(mode=0o700)
        for name in ('fixture', 'reader'):
            (self.directory / name).mkdir(mode=0o700)
        self.state, self.artifacts = self.root / 'state', self.root / 'artifacts'
        self.state.mkdir(mode=0o700)
        self.artifacts.mkdir(mode=0o700)
        self.enterContext(patch.object(T, 'SOURCE_ROOT', self.source_root))
        self.code = {'base/train.py': 'd' * 64, 'lake/tracking.py': 'e' * 64}
        self.enterContext(patch.object(T, 'source_hashes', side_effect=lambda: deepcopy(self.code)))
        self.public = observation()
        self.source = T.L.normalize_input(self.public, evidence_token=QUERY_TOKEN)
        self.request = dict(command='select', table='local_experiment.training_' + TOKEN,
                            columns=['row_id', 'x1', 'x2', 'x3', 'target'], limit=100)
        self.fixture = dict(schema_version=1, result='passed', profile=T.L.FIXTURE_PROFILE,
                            fixture_token=TOKEN, table=self.public['table'],
                            runtime={phase: {'state': 'verified', 'cleanup_verified': True}
                                     for phase in ('prepare', 'cleanup')},
                            identity=dict(table_uuid=self.public['table_uuid'],
                                          current_snapshot_id=self.public['snapshot_id'],
                                          schema_sha256=self.public['schema_sha256']),
                            reader_cleanup_verified=True, cleanup={'verified': True},
                            query_drain={'verified': True}, fixture_unchanged=True, table_inventory_unchanged=True)
        self.status = dict(schema_version=1, state='passed', child_exited=True, cleanup_verified=True,
                           exit_code=0, error_code=None, evidence_token=QUERY_TOKEN)
        self.frames = [dict(schema_version=1, event='allocated', command='query select', evidence_token=QUERY_TOKEN),
                       dict(schema_version=1, event='result', command='query select', evidence_token=QUERY_TOKEN,
                            result='passed', observation=self.public, error_code=None, cleanup_verified=True,
                            evidence='tmp/local-experiment/query-read/' + QUERY_TOKEN + '/summary.json')]
        self.write_bundle()

    def save(self, name, value, *, raw=False):
        path = self.directory / name
        path.write_bytes(value if raw else T.E.canonical(value))
        path.chmod(0o600)

    def write_bundle(self):
        self.save('lake-input.json', self.source)
        self.save('fixture/report.json', self.fixture)
        self.save('reader/reader-intent.json', dict(schema_version=1, command='query select', request=self.request))
        self.save('reader/reader-status.json', self.status)
        self.save('reader/reader.stdout.jsonl', b''.join(T.E.canonical(x) for x in self.frames), raw=True)
        self.seal_ready()

    def seal_ready(self):
        ready = dict(schema_version=1, result='passed', source_token=TOKEN, fixture_profile=T.L.FIXTURE_PROFILE,
                     query_evidence_token=QUERY_TOKEN, lake_input_sha256=T.sha((self.directory / 'lake-input.json').read_bytes()),
                     files={name: T.sha((self.directory / name).read_bytes()) for name in T.FILES})
        self.save('source-ready.json', ready)

    def trainer(self, *, guard=lambda: None):
        self.client = FakeClient()
        self.workload = injected_workload(self.source)
        self.fit, self.validate = Mock(side_effect=lambda: deepcopy(self.workload)), Mock()
        return T.LakeTrainer(self.client, source=self.source, source_token=TOKEN,
            state=self.state, artifacts=self.artifacts, store_id=STORE_ID,
            train=self.fit, validate=self.validate, guard=guard)

    def test_source_binds_fixed_files_frames_and_normalized_input(self):
        source = T.Source(TOKEN)
        self.assertEqual(source.value, self.source)
        self.assertEqual(len(source.saved), 6)
        before = {path: path.read_bytes() for path in source.saved}
        source.guard()
        self.assertEqual(before, {path: path.read_bytes() for path in source.saved})

    def test_ready_requires_exact_keys_canonical_bytes_and_input_digest(self):
        for mode in ('extra-key', 'boolean-version', 'bad-sha', 'noncanonical'):
            with self.subTest(mode=mode):
                self.seal_ready()
                value = json.loads((self.directory / 'source-ready.json').read_bytes())
                if mode == 'extra-key': value['path'] = '/foreign'
                elif mode == 'boolean-version': value['schema_version'] = True
                elif mode == 'bad-sha': value['lake_input_sha256'] = '0' * 64
                self.save('source-ready.json', json.dumps(value).encode() if mode == 'noncanonical' else value,
                          raw=mode == 'noncanonical')
                with self.assertRaises((T.E.ImportError, T.N.NotebookError)):
                    T.Source(TOKEN)

    def test_input_extra_fields_or_split_changes_fail_before_any_sdk_work(self):
        for mode in ('extra', 'split'):
            with self.subTest(mode=mode):
                value = deepcopy(self.source)
                if mode == 'extra': value['extra'] = True
                else: value['split_row_ids']['train'][0] = value['split_row_ids']['test'][0]
                self.save('lake-input.json', value)
                self.seal_ready()
                with self.assertRaises(T.E.ImportError): T.Source(TOKEN)

    def test_status_requires_success_and_actual_boolean_cleanup(self):
        for key, value in [('exit_code', False), ('child_exited', 1), ('cleanup_verified', False),
                           ('state', 'unknown'), ('evidence_token', 'f' * 32)]:
            with self.subTest(key=key):
                altered = dict(self.status, **{key: value})
                self.save('reader/reader-status.json', altered)
                self.seal_ready()
                with self.assertRaises(T.E.ImportError): T.Source(TOKEN)

    def test_reader_frames_and_input_must_describe_the_same_rows(self):
        frames = deepcopy(self.frames)
        frames[-1]['observation']['rows'][0][1]['value'] = '123.0'
        frames[-1]['observation']['rows_sha256'] = T.N.C.digest(frames[-1]['observation']['rows'])
        self.save('reader/reader.stdout.jsonl', b''.join(T.E.canonical(x) for x in frames), raw=True)
        self.seal_ready()
        with self.assertRaises(T.E.ImportError): T.Source(TOKEN)

    def test_fixture_cleanup_and_identity_are_required_even_if_report_says_passed(self):
        for mode in ('cleanup', 'identity', 'retention', 'boolean-version', 'token', 'runtime'):
            with self.subTest(mode=mode):
                fixture = deepcopy(self.fixture)
                if mode == 'cleanup': fixture['cleanup']['verified'] = False
                elif mode == 'identity': fixture['identity']['current_snapshot_id'] = '7'
                elif mode == 'retention': fixture['fixture_unchanged'] = False
                elif mode == 'boolean-version': fixture['schema_version'] = True
                elif mode == 'token': fixture['fixture_token'] = 'f' * 32
                else: fixture['runtime']['prepare']['cleanup_verified'] = False
                self.save('fixture/report.json', fixture)
                self.seal_ready()
                with self.assertRaises(T.E.ImportError): T.Source(TOKEN)

    def test_verified_bundle_cannot_be_reclaimed_under_another_source_token(self):
        other_token = 'f' * 32
        other = self.source_root / other_token
        shutil.copytree(self.directory, other)
        ready_path = other / 'source-ready.json'
        ready = json.loads(ready_path.read_bytes())
        ready['source_token'] = other_token
        ready_path.write_bytes(T.E.canonical(ready))
        with self.assertRaises(T.E.ImportError): T.Source(other_token)
        self.assertFalse((other / 'tracking-intent.json').exists())

    def test_direct_trainer_requires_table_token_to_match_source_token(self):
        with self.assertRaises(T.E.ImportError):
            T.LakeTrainer(FakeClient(), source=self.source, source_token='f' * 32,
                state=self.state, artifacts=self.artifacts, store_id=STORE_ID,
                train=Mock(), validate=Mock())
        self.assertFalse((self.state / 'lake-training').exists())

    def test_unsafe_source_symlink_is_rejected_without_touching_target(self):
        path = self.directory / 'lake-input.json'
        retained = self.root / 'retained.json'
        path.rename(retained)
        path.symlink_to(retained)
        before = retained.read_bytes()
        with self.assertRaises(T.E.ImportError): T.Source(TOKEN)
        self.assertEqual(retained.read_bytes(), before)

    def test_guard_rejects_byte_preserving_file_replacement_and_source_change(self):
        source = T.Source(TOKEN)
        path = self.directory / 'lake-input.json'
        raw = path.read_bytes()
        path.unlink()
        self.save('lake-input.json', raw, raw=True)
        with self.assertRaises(T.E.ImportError): source.guard()
        source = T.Source(TOKEN)
        self.code['lake/tracking.py'] = 'f' * 64
        with self.assertRaises(T.E.ImportError): source.guard()

    def test_parent_claim_is_private_durable_and_never_replayed(self):
        source = T.Source(TOKEN)
        source.claim(STORE_ID)
        path = self.directory / 'tracking-intent.json'
        raw = path.read_bytes()
        self.assertEqual(raw, T.E.canonical(source.intent(STORE_ID)))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError): T.Source(TOKEN).claim(STORE_ID)
        self.assertEqual(path.read_bytes(), raw)

    def test_exclusive_claim_preserves_write_cancel_when_stream_close_also_fails(self):
        cancel = KeyboardInterrupt('first write cancellation')
        stream = Mock()
        stream.write.side_effect = cancel
        stream.close.side_effect = OSError('later close failure')
        with patch.object(T.os, 'open', return_value=777), patch.object(T.os, 'fdopen', return_value=stream):
            with self.assertRaises(KeyboardInterrupt) as caught:
                T.exclusive(self.directory / 'tracking-intent.json', {'claim': True})
        self.assertIs(caught.exception, cancel)
        stream.close.assert_called_once()

    def test_exclusive_claim_preserves_directory_fsync_cancel_over_close_failure(self):
        cancel = KeyboardInterrupt('first directory cancellation')
        stream = Mock()
        stream.fileno.return_value = 777
        with patch.object(T.os, 'open', side_effect=[777, 778]), \
                patch.object(T.os, 'fdopen', return_value=stream), \
                patch.object(T.os, 'fsync', side_effect=[None, cancel]), \
                patch.object(T.os, 'close', side_effect=OSError('later directory close failure')):
            with self.assertRaises(KeyboardInterrupt) as caught:
                T.exclusive(self.directory / 'tracking-intent.json', {'claim': True})
        self.assertIs(caught.exception, cancel)

    def test_worker_intent_is_store_bound(self):
        source = T.Source(TOKEN)
        source.claim(STORE_ID)
        with self.assertRaises(T.E.ImportError):
            source.verify_intent('00000000-0000-4000-8000-000000000099')

    def test_lake_trainer_uses_separate_namespace_and_exact_provenance(self):
        trainer = self.trainer()
        result, output = trainer.run()
        self.assertEqual(result['result'], 'verified')
        self.assertEqual(output.parent, self.state / 'lake-training')
        self.assertEqual(self.fit.call_count, 1)
        self.assertEqual(self.client.experiment.name, T.EXPERIMENT)
        self.assertEqual(self.client.experiment.tags['shirokuma.owner'], T.EXPERIMENT)
        run = self.client.runs[result['run_id']]
        self.assertTrue(run.data.tags['mlflow.runName'].startswith('lake-ridge-'))
        self.assertEqual(run.data.params['source_token'], TOKEN)
        self.assertEqual(run.data.params['query_evidence_token'], QUERY_TOKEN)
        self.assertEqual(run.data.params['lake_snapshot_id'], self.public['snapshot_id'])
        self.assertEqual(run.data.params['lake_rows_sha256'], self.public['rows_sha256'])
        self.assertEqual(run.data.params['lake_table_uuid'], self.public['table_uuid'])
        self.assertEqual(run.data.params['train_rows'], '48')
        self.assertEqual(run.data.params['test_rows'], '16')
        self.assertNotIn('dataset_seed', run.data.params)
        artifact = self.artifacts / 'iceberg-training' / result['run_id'] / 'artifacts/safe-projection.json'
        self.assertEqual(artifact.read_bytes(), T.E.canonical(self.workload))
        self.assertFalse((self.artifacts / 'supervised-training').exists())
        self.assertFalse((self.state / 'training').exists())

    def test_injected_fit_cannot_substitute_another_reader_observation(self):
        trainer = self.trainer()
        self.workload['lake_input']['query_evidence_token'] = 'f' * 32
        result, _ = trainer.run()
        self.assertEqual(result['failure_code'], 'LAKE_TRAINING_INPUT_CHANGED')
        self.assertEqual(self.client.calls, [])

    def test_source_changed_after_fit_blocks_remote_writes(self):
        source = T.Source(TOKEN)
        trainer = self.trainer(guard=source.guard)
        def fit():
            (self.directory / 'source-ready.json').write_bytes(b'{}\n')
            return deepcopy(self.workload)
        self.fit.side_effect = fit
        result, _ = trainer.run()
        self.assertEqual(result['result'], 'failed')
        self.assertEqual(result['failure_code'], 'LAKE_SOURCE_FILE_CHANGED')
        self.assertEqual(self.client.calls, [])

    def test_lake_partial_batch_does_not_retry_or_terminate(self):
        trainer = self.trainer()
        self.client.partial_batch = True
        result, _ = trainer.run()
        self.assertEqual(result['result'], 'failed')
        self.assertEqual(result['phase'], 'log-batch-pending')
        self.assertEqual(self.client.calls, ['create_experiment', 'create_run', 'log_batch'])
        self.assertEqual(len(self.client.runs[result['run_id']].data.metrics), 1)
        self.assertEqual(self.fit.call_count, 1)

    def test_first_cancellation_is_preserved_through_source_guard_failure(self):
        source = T.Source(TOKEN)
        trainer = self.trainer(guard=source.guard)
        cancel = KeyboardInterrupt('first')
        def fit():
            (self.directory / 'source-ready.json').write_bytes(b'{}\n')
            raise cancel
        self.fit.side_effect = fit
        with self.assertRaises(KeyboardInterrupt) as caught: trainer.run()
        self.assertIs(caught.exception, cancel)
        self.assertEqual(self.client.calls, [])

    def test_invalid_cli_token_never_checks_runtime_or_allocates_intent(self):
        with patch.object(R, 'require_stopped') as stopped, patch.object(T.subprocess, 'run') as spawn, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(T.main(['--source-token', '../bad']), 1)
        stopped.assert_not_called()
        spawn.assert_not_called()
        self.assertFalse((self.directory / 'tracking-intent.json').exists())

    def test_parent_uses_fresh_worker_environment_and_claim_before_spawn(self):
        environment = {'PATH': '/owned/runtime', 'HOME': '/owned/home'}
        def spawn(arguments, **kwargs):
            self.assertEqual(arguments[-3:], ['--source-token', TOKEN, '--worker'])
            self.assertEqual(kwargs['env'], environment)
            self.assertEqual(kwargs['timeout'], 180)
            self.assertEqual(json.loads((self.directory / 'tracking-intent.json').read_bytes())['store_id'], STORE_ID)
            return NS(returncode=0)
        with patch.object(R, 'require_stopped', return_value={'store_id': STORE_ID}), \
                patch.object(R, 'environment', return_value=environment), \
                patch.object(T.subprocess, 'run', side_effect=spawn) as worker:
            self.assertEqual(T.main(['--source-token', TOKEN]), 0)
        self.assertEqual(worker.call_count, 1)

    def test_unknown_worker_outcome_keeps_claim_and_blocks_second_spawn(self):
        with patch.object(R, 'require_stopped', return_value={'store_id': STORE_ID}), \
                patch.object(R, 'environment', return_value={}), \
                patch.object(T.subprocess, 'run', side_effect=TimeoutError('private worker error')) as spawn, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(T.main(['--source-token', TOKEN]), 1)
            self.assertEqual(T.main(['--source-token', TOKEN]), 1)
        self.assertEqual(spawn.call_count, 1)
        self.assertTrue((self.directory / 'tracking-intent.json').exists())

    def test_direct_worker_cannot_reuse_existing_worker_claim(self):
        source = T.Source(TOKEN)
        source.claim(STORE_ID)
        T.exclusive(self.directory / 'tracking-worker-intent.json', source.intent(STORE_ID))
        with patch.object(R, '_isolated_runtime', return_value=dict(os.environ)), \
                patch.object(R, 'store_lock', return_value=nullcontext()), \
                patch.object(R, 'require_stopped', return_value={'store_id': STORE_ID}), \
                patch.object(T, 'LakeTrainer') as trainer:
            with self.assertRaises(FileExistsError): T.worker(TOKEN)
        trainer.assert_not_called()

    def test_worker_uses_bound_source_in_locked_isolated_sdk_and_cannot_repeat(self):
        source = T.Source(TOKEN)
        source.claim(STORE_ID)
        client = FakeClient()
        fit = Mock(side_effect=lambda: injected_workload(self.source))
        sdk = NS(MlflowClient=Mock(return_value=client), entities=NS(
            Metric=lambda **value: NS(**value), Param=lambda **value: NS(**value)))
        actual_trainer = T.LakeTrainer
        def create(client, **kwargs):
            return actual_trainer(client, **kwargs, train=fit, validate=Mock())
        output = io.StringIO()
        with patch.dict(sys.modules, {'mlflow': sdk}), \
                patch.object(R, '_isolated_runtime', return_value=dict(os.environ)), \
                patch.object(R, 'store_lock', return_value=nullcontext()) as lock, \
                patch.object(R, 'require_stopped', return_value={'store_id': STORE_ID}), \
                patch.object(R, 'STATE', self.state), patch.object(R, 'ARTIFACT_ROOT', self.artifacts), \
                patch.object(T, 'ROOT', self.root), patch.object(T.os, 'chdir') as change_directory, \
                patch.object(T, 'LakeTrainer', side_effect=create), redirect_stdout(output):
            self.assertEqual(T.worker(TOKEN), 0)
            with self.assertRaises(FileExistsError): T.worker(TOKEN)
        receipt = json.loads(output.getvalue())
        self.assertEqual(receipt['result'], 'verified')
        self.assertEqual(receipt['source_token'], TOKEN)
        self.assertEqual(receipt['run_id'], next(iter(client.runs)))
        self.assertEqual(set(receipt), {'result', 'source_token', 'run_id', 'evidence', 'failure_code'})
        self.assertTrue((self.root / receipt['evidence'] / 'result.json').is_file())
        self.assertEqual(fit.call_count, 1)
        self.assertEqual(sdk.MlflowClient.call_count, 1)
        self.assertEqual(lock.call_count, 2)
        change_directory.assert_called_once_with(R.STORE)

    def test_parent_cancel_identity_survives_final_source_guard_failure(self):
        cancel = KeyboardInterrupt('first cancel')
        def spawn(*args, **kwargs):
            (self.directory / 'lake-input.json').write_bytes(b'{}\n')
            raise cancel
        with patch.object(R, 'require_stopped', return_value={'store_id': STORE_ID}), \
                patch.object(R, 'environment', return_value={}), \
                patch.object(T.subprocess, 'run', side_effect=spawn):
            with self.assertRaises(KeyboardInterrupt) as caught:
                T.main(['--source-token', TOKEN])
        self.assertIs(caught.exception, cancel)
        self.assertTrue((self.directory / 'tracking-intent.json').exists())


if __name__ == '__main__':
    unittest.main()
