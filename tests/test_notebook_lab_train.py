"""Fixed Lab training with fake children and real private receipt validation."""
from contextlib import ExitStack
from copy import deepcopy
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.notebook_lab import train as B, runtime as R
from tools.local_experiment.mlflow import runtime as M
from tests.test_mlflow_predict import saved_review

PID, START = 43210, 'Sun Sep 20 10:00:00 2026'


class Cancel(BaseException):
    pass


class Child:
    pid = PID

    def __init__(self, test, stdout):
        self.test, self.stdout, self.returncode = test, stdout, None

    def wait(self, timeout):
        self.test.waits.append(timeout)
        if self.test.wait_error is not None:
            error, self.test.wait_error = self.test.wait_error, None
            raise error
        self.test.finish(self)
        return self.returncode

    def poll(self):
        return self.returncode


class LabTrainingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.lab = self.base / 'lab'
        self.lab.mkdir(mode=0o700)
        self.state = self.lab / 'trainings'
        self.lake = self.base / 'lake'
        self.lake.mkdir(mode=0o700)
        self.review, _ = saved_review()
        self.calls, self.waits, self.signals = [], [], []
        self.wait_error = None
        self.closed, self.emit_frame, self.early_failure = True, True, False
        self.terminal_result, self.terminal_exit = 'passed', 0
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for obj, name, value in ((B, 'LAB_STATE', self.lab), (B, 'STATE', self.state),
                                 (R, 'STATE', self.lab), (B.Tn, 'LAKE_STATE', self.lake)):
            self.stack.enter_context(patch.object(obj, name, value))
        self.queries = self.stack.enter_context(patch.object(R, '_queries_idle', return_value=True))
        self.comparisons = self.stack.enter_context(patch.object(R, '_comparisons_idle', return_value=True))
        self.empty = self.stack.enter_context(patch.object(B.Tn, 'group_empty', return_value=True))
        self.stack.enter_context(patch.object(B.N, '_process_start', return_value=START))
        self.stack.enter_context(patch.object(B.subprocess, 'Popen', side_effect=self.spawn))
        self.stack.enter_context(patch.object(B.os, 'kill', side_effect=self.kill))
        self.loader = self.stack.enter_context(patch.object(B.T, 'load_saved_review', return_value=self.review))
        self.stack.enter_context(patch.object(B.T, '_get', side_effect=AssertionError('HTTP prohibited')))

    def spawn(self, argv, **kwargs):
        self.directory = Path(argv[-1])
        self.calls.append((argv, kwargs))
        self.child = Child(self, kwargs['stdout'])
        return self.child

    def finish(self, child):
        child.returncode = self.terminal_exit
        if not self.emit_frame:
            return
        token = self.review['lake_source']['source_token']
        output = self.lake / token
        output.mkdir(mode=0o700, exist_ok=True)
        summary = dict(schema_version=1, profile='fixed-iceberg-training-pipeline-v1', source_token=token,
            result=self.terminal_result, phase='complete', store_id=self.review['identity']['store_id'],
            query_evidence_token=self.review['lake_source']['query_evidence_token'],
            tracking={k: self.review['identity'][k] for k in ('attempt_token', 'run_id', 'experiment_id', 'workload_sha256')},
            reader_cleanup_confirmed=self.closed, fixture_cleanup_confirmed=True,
            tracker_cleanup_confirmed=True, runtime_restored=True)
        if not self.early_failure:
            B.N._write(output / 'summary.json', summary)
        B.N._write(output / 'owner-result.json', dict(schema_version=1, profile=summary['profile'],
            result=self.terminal_result, summary_result=None if self.early_failure else self.terminal_result,
            cleanup_confirmed=self.closed, error_code=None if self.terminal_result == 'passed' else 'LAKE_PIPELINE_FAILED'))
        child.stdout.write(B.N._canonical(dict(result=self.terminal_result, cleanup_confirmed=self.closed,
            evidence='tmp/local-experiment/lake-training/' + token + '/owner-result.json')))

    def kill(self, pid, sig):
        self.signals.append((pid, sig))
        self.terminal_result, self.terminal_exit = 'interrupted', 130
        if B._launched(self.directory):
            self.finish(self.child)
        else:
            self.child.returncode = 130

    def test_success_saved_model_identity_private_receipts_and_clean_child_environment(self):
        before = dict(os.environ)
        self.assertEqual(B.train_lake(), self.review)
        self.assertEqual(os.environ, before)
        self.assertEqual(self.waits, [600])
        self.assertEqual(len(self.calls), 1)
        argv, options = self.calls[0]
        self.assertEqual(argv, B.arguments(self.directory))
        self.assertEqual(argv[0], str(B.ROOT / 'tools/local_experiment/mlflow/.venv/bin/python'))
        self.assertTrue(options['start_new_session'])
        self.assertEqual(options['umask'], 0o077)
        env = options['env']
        self.assertEqual(env['KUBECONFIG'], str(B.KUBECONFIG))
        self.assertEqual(env['GOENV'], 'off')
        self.assertEqual(env['GOTOOLCHAIN'], 'local')
        for name, child in (('GOCACHE', 'build'), ('GOPATH', 'gopath'), ('GOMODCACHE', 'modules')):
            self.assertEqual(Path(env[name]), self.lab / 'query-cache' / child)
            self.assertTrue(Path(env[name]).is_dir())
        self.assertFalse((self.lab / 'training-cache').exists())
        for name in ('HOME', 'HTTP_PROXY', 'MLFLOW_TRACKING_URI', 'SHIROKUMA_NOTEBOOK_RUN_DIR'):
            self.assertNotIn(name, env)
        self.loader.assert_called_once_with(self.review['identity']['attempt_token'], kind='iceberg')
        self.assertEqual(B._json(self.directory / 'training-review.json'), self.review)
        self.assertTrue(B.training_closed(self.directory))
        self.assertTrue(B.check_idle())
        self.assertEqual(B.training_status(self.directory)['evidence_token'], self.review['lake_source']['source_token'])
        for path in self.directory.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)

    def test_unknown_pipeline_cleanup_blocks_next_attempt(self):
        self.closed, self.terminal_result, self.terminal_exit = False, 'failed', 1
        with self.assertRaisesRegex(B.N.NotebookError, 'CLEANUP_UNKNOWN'):
            B.train_lake()
        self.assertFalse(B.check_idle())
        self.loader.assert_not_called()
        with self.assertRaisesRegex(B.N.NotebookError, 'CLEANUP_UNKNOWN'):
            B.train_lake()
        self.assertEqual(len(self.calls), 1)

    def test_exited_child_without_terminal_receipt_is_unknown(self):
        self.emit_frame, self.terminal_exit = False, 1
        with self.assertRaisesRegex(B.N.NotebookError, 'CLEANUP_UNKNOWN'):
            B.train_lake()
        self.assertTrue(B.training_status(self.directory)['child_exited'])
        self.assertFalse(B.check_idle())

    def test_exited_worker_with_live_group_is_unknown(self):
        self.empty.return_value = False
        with self.assertRaisesRegex(B.N.NotebookError, 'CLEANUP_UNKNOWN'):
            B.train_lake()
        self.assertFalse(B.check_idle())
        self.loader.assert_not_called()

    def test_early_build_failure_has_known_cleanup_without_saved_model(self):
        self.early_failure, self.terminal_result, self.terminal_exit = True, 'failed', 1
        with self.assertRaisesRegex(B.N.NotebookError, 'FAILED'):
            B.train_lake()
        self.assertTrue(B.training_closed(self.directory))
        self.assertTrue(B.check_idle())
        self.loader.assert_not_called()
        self.assertFalse((self.directory / 'training-review.json').exists())

    def test_cross_source_saved_review_rejected_after_known_cleanup(self):
        altered = deepcopy(self.review)
        altered['lake_source']['query_evidence_token'] = '2' * 32
        self.loader.return_value = altered
        with self.assertRaisesRegex(B.N.NotebookError, 'FAILED'):
            B.train_lake()
        self.assertTrue(B.check_idle())
        self.assertFalse((self.directory / 'training-review.json').exists())

    def test_first_cancellation_survives_cleanup_and_final_status_failure(self):
        first = Cancel('first')
        self.wait_error = first
        write = B.N._write
        def fail_final(path, value, **kwargs):
            if path.name == 'status.json' and value['state'] == 'interrupted':
                raise OSError('secondary')
            return write(path, value, **kwargs)
        with patch.object(B.N, '_write', side_effect=fail_final), self.assertRaises(Cancel) as caught:
            B.train_lake()
        self.assertIs(caught.exception, first)
        self.assertEqual(self.signals, [(PID, signal.SIGINT)])
        self.assertFalse(B.check_idle())
        self.loader.assert_not_called()

    def test_timeout_uses_existing_grace_once_and_retains_closed_failure(self):
        self.wait_error = subprocess.TimeoutExpired('fixed', 600)
        with self.assertRaises(B.N.NotebookError):
            B.train_lake()
        self.assertEqual(self.signals, [(PID, signal.SIGINT)])
        self.assertTrue(B.check_idle())
        self.assertEqual(len(self.calls), 1)

    def test_spawn_failure_is_known_no_effect(self):
        with patch.object(B.subprocess, 'Popen', side_effect=OSError('spawn refused')):
            with self.assertRaisesRegex(B.N.NotebookError, 'FAILED'):
                B.train_lake()
        self.assertTrue(B.check_idle())
        self.assertEqual(list(self.lake.iterdir()), [])

    def test_identity_failure_before_ack_keeps_owned_pid_and_closes_without_pipeline(self):
        with patch.object(B.N, '_process_start', side_effect=OSError('ps failed')):
            with self.assertRaisesRegex(B.N.NotebookError, 'FAILED'):
                B.train_lake()
        status = B.training_status(self.directory)
        self.assertEqual(status['pid'], PID)
        self.assertTrue(status['cleanup_verified'])
        self.assertTrue(B.check_idle())
        self.assertFalse(B._launched(self.directory))
        self.assertEqual(list(self.lake.iterdir()), [])

    def test_cross_workflow_unknowns_block_allocation(self):
        for other in (self.queries, self.comparisons):
            other.return_value = False
            with self.assertRaisesRegex(B.N.NotebookError, 'CLEANUP_UNKNOWN'):
                B.train_lake()
            self.assertFalse(self.state.exists())
            other.return_value = True
        self.assertEqual(self.calls, [])

    def test_shared_owner_lock_refuses_parallel_lab_operations(self):
        with R._lock(), self.assertRaisesRegex(B.N.NotebookError, 'BUSY'):
            B.train_lake()
        with B._lock(), self.assertRaisesRegex(R.LabError, 'LAB_BUSY'):
            with R._lock():
                pass
        self.assertFalse(self.state.exists())

    def initial(self):
        self.state.mkdir(mode=0o700)
        self.directory = self.state / ('a' * 32)
        self.directory.mkdir(mode=0o700)
        B.N._write(self.directory / 'input.json', B._input())
        B.N._write(self.directory / 'intent.json', B._intent(self.directory))
        B.N._write(self.directory / 'status.json', dict(schema_version=1, state='running', pid=PID, pgid=PID,
            process_start=START, evidence_token=None, exit_code=None, child_exited=False,
            cleanup_verified=False, error_code=None))

    def authorize(self):
        B.N._write(self.directory / 'launch.json', dict(pid=PID, ppid=os.getpid(), pgid=PID, process_start=START))

    def test_worker_requires_parent_ack_before_pipeline_import_or_call(self):
        self.initial()
        pipeline = Mock()
        with patch.object(B.time, 'monotonic', side_effect=[0, 31]), self.assertRaises(B.N.NotebookError):
            B.worker(self.directory, pipeline=pipeline)
        pipeline.assert_not_called()
        self.assertEqual(list(self.lake.iterdir()), [])

    def test_exact_worker_handshake_calls_existing_pipeline_once_without_arguments(self):
        self.initial()
        parent = os.getpid()
        self.authorize()
        pipeline = Mock(return_value=0)
        with patch.object(B.os, 'getpid', return_value=PID), patch.object(B.os, 'getppid', return_value=parent), \
             patch.object(B.os, 'getpgrp', return_value=PID):
            self.assertEqual(B.worker(self.directory, pipeline=pipeline), 0)
        pipeline.assert_called_once_with([])

    def test_worker_changed_parent_or_input_refused(self):
        self.initial()
        self.authorize()
        pipeline = Mock()
        with patch.object(B.os, 'getpid', return_value=PID), patch.object(B.os, 'getppid', return_value=PID + 1), \
             patch.object(B.os, 'getpgrp', return_value=PID), self.assertRaises(B.N.NotebookError):
            B.worker(self.directory, pipeline=pipeline)
        B.N._write(self.directory / 'input.json', dict(schema_version=1, profile=B.PROFILE, sql='select 1'), replace=True)
        with self.assertRaises(B.N.NotebookError):
            B.worker(self.directory, pipeline=pipeline)
        pipeline.assert_not_called()

    def test_symlink_residual_and_unknown_cli_arguments_refused(self):
        self.state.symlink_to(self.lake, target_is_directory=True)
        with self.assertRaises(B.N.NotebookError):
            B.check_idle()
        with patch.object(B, 'worker') as worker:
            self.assertEqual(B.main(['--worker', '/not/owned', '--retry']), 1)
            worker.assert_not_called()

    def recoverable(self):
        """Saved unknown pipeline with completed Reader/tracker and a valid model."""
        self.closed, self.terminal_result, self.terminal_exit = False, 'unknown', 1
        with self.assertRaises(B.N.NotebookError):
            B.train_lake()
        token = self.review['lake_source']['source_token']
        output = self.lake / token
        current = dict(schema_version=1, state='running', store_id=self.review['identity']['store_id'], healthy=True,
                       identity=dict(pid=9876, pgid=9876, start_time=START, argv=M.server_argv()))
        summary = B._json(output / 'summary.json')
        summary.update(failure_phase='restore-runtime', error_code='LAKE_PIPELINE_FAILED', fixture_result='passed',
                       reader_cleanup_confirmed=True, fixture_cleanup_confirmed=True,
                       tracker_cleanup_confirmed=True, runtime_restored=False, runtime_before=deepcopy(current))
        B.N._write(output / 'summary.json', summary, replace=True)
        B.N._write(output / 'tracker-status.json', dict(schema_version=1, pid=43212, child_exited=True,
                                                       group_empty=True, exit_code=0))
        B.N._write(output / 'tracker-owner-intent.json', dict(schema_version=1, source_token=token,
            argv=[str(M.VENV_PYTHON), '-B', str(B.ROOT / 'tools/local_experiment/lake_training/tracking.py'),
                  '--source-token', token]))
        reader = output / 'reader'
        reader.mkdir(mode=0o700)
        request = dict(command='select', table='local_experiment.training_' + token,
                       columns=['row_id', 'x1', 'x2', 'x3', 'target'], limit=100)
        B.N._write(reader / 'reader-intent.json', dict(schema_version=1, command='query select', request=request))
        query = self.review['lake_source']['query_evidence_token']
        B.N._write(reader / 'reader-status.json', dict(schema_version=1, state='passed', pid=43211, pgid=43211,
            process_start=START, evidence_token=query, exit_code=0, child_exited=True, cleanup_verified=True, error_code=None))
        rows = [[{'type':'bigint', 'value':str(i)}, *[{'type':'double', 'value':'0.0'} for _ in range(4)]] for i in range(64)]
        source = self.review['lake_source']
        source['rows_sha256'] = B.N.C.digest(rows)
        public = dict(schema_version=1, table=source['table'], table_uuid=source['table_uuid'],
            snapshot_id=source['snapshot_id'], schema_sha256=source['schema_sha256'],
            columns=[dict(name=name, type=kind, nullable=True) for name, kind in
                     zip(request['columns'], ['bigint', 'double', 'double', 'double', 'double'])],
            rows=rows, row_count=64, fetched_row_count=64, truncated=False, requested_limit=100,
            ordering='unspecified', rows_sha256=B.N.C.digest(rows))
        frames = [dict(schema_version=1, event='allocated', command='query select', evidence_token=query),
                  dict(schema_version=1, event='result', command='query select', evidence_token=query,
                       result='passed', observation=public, error_code=None, cleanup_verified=True,
                       evidence='tmp/local-experiment/query-read/' + query + '/summary.json')]
        path = reader / 'reader.stdout.jsonl'
        path.write_bytes(b''.join(B.N._canonical(frame) for frame in frames))
        path.chmod(0o600)
        self.runtime_status = self.stack.enter_context(patch.object(M, 'status', return_value=current))
        self.marker = self.stack.enter_context(patch.object(M, 'require_store',
            return_value=dict(schema_version=1, store_id=self.review['identity']['store_id'])))
        self.stack.enter_context(patch.object(M, 'start', side_effect=AssertionError('runtime mutation prohibited')))
        self.stack.enter_context(patch.object(M, 'stop', side_effect=AssertionError('runtime mutation prohibited')))
        return output

    def test_recovery_keeps_original_unknown_bytes_and_returns_completed_model_without_refit(self):
        self.recoverable()
        before = B._recovery_files(self.directory, self.review['lake_source']['source_token'])
        self.assertEqual(B.recover_training(self.directory.name), self.review)
        self.assertEqual(B._recovery_files(self.directory, self.review['lake_source']['source_token']), before)
        self.assertEqual(B.training_status(self.directory)['state'], 'unknown')
        self.assertFalse(B.training_status(self.directory)['cleanup_verified'])
        self.assertTrue(B.training_closed(self.directory))
        self.assertTrue(B.check_idle())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.signals, [])
        self.runtime_status.assert_called_once_with()
        self.assertEqual((self.directory / 'recovery.json').stat().st_mode & 0o777, 0o600)

    def test_recovery_idempotent_after_normal_server_stop_or_replacement(self):
        self.recoverable()
        B.recover_training(self.directory.name)
        raw = (self.directory / 'recovery.json').read_bytes()
        old_runtime = deepcopy(self.runtime_status.return_value)
        self.runtime_status.reset_mock()
        for current in (dict(old_runtime, state='stopped', healthy=False, identity=None),
                        dict(old_runtime, identity=dict(old_runtime['identity'], pid=9877, pgid=9877))):
            with self.subTest(runtime=current):
                self.runtime_status.return_value = current
                self.assertTrue(B.check_idle())
                self.assertEqual(B.recover_training(self.directory.name), self.review)
                self.assertEqual((self.directory / 'recovery.json').read_bytes(), raw)
                self.runtime_status.assert_not_called()

    def test_recovery_original_evidence_change_or_store_change_invalidates_closure(self):
        output = self.recoverable()
        B.recover_training(self.directory.name)
        marker = deepcopy(self.marker.return_value)
        self.marker.return_value['store_id'] = '00000000-0000-0000-0000-000000000009'
        with self.assertRaises(B.N.NotebookError):
            B.training_closed(self.directory)
        self.marker.return_value = marker
        summary = B._json(output / 'summary.json')
        summary['unrelated_new_field'] = 'changed original bytes'
        B.N._write(output / 'summary.json', summary, replace=True)
        with self.assertRaises(B.N.NotebookError):
            B.recover_training(self.directory.name)

    def test_recovery_requires_live_healthy_owned_same_store_only_on_first_call(self):
        self.recoverable()
        good = deepcopy(self.runtime_status.return_value)
        for change in ({'healthy':False}, {'store_id':'00000000-0000-0000-0000-000000000009'},
                       {'identity':dict(good['identity'], argv=['unowned'])}):
            self.runtime_status.return_value = dict(good, **change)
            with self.assertRaises(B.N.NotebookError):
                B.recover_training(self.directory.name)
            self.assertFalse((self.directory / 'recovery.json').exists())

    def test_recovery_rejects_any_remaining_worker_reader_or_tracker_group(self):
        self.recoverable()
        for pid in (PID, 43211, 43212):
            self.empty.side_effect = lambda current, live=pid: current != live
            with self.assertRaises(B.N.NotebookError):
                B.recover_training(self.directory.name)
            self.assertFalse((self.directory / 'recovery.json').exists())
        self.runtime_status.assert_not_called()

    def test_recovery_rejects_different_failure_phase_and_other_unknown_attempt(self):
        output = self.recoverable()
        summary = B._json(output / 'summary.json')
        summary['failure_phase'] = 'source-before-tracking'
        B.N._write(output / 'summary.json', summary, replace=True)
        with self.assertRaises(B.N.NotebookError):
            B.recover_training(self.directory.name)
        summary['failure_phase'] = 'restore-runtime'
        B.N._write(output / 'summary.json', summary, replace=True)
        other = self.state / ('e' * 32)
        other.mkdir(mode=0o700)
        with self.assertRaises(B.N.NotebookError):
            B.recover_training(self.directory.name)
        self.runtime_status.assert_not_called()
        self.assertFalse((self.directory / 'recovery.json').exists())

    def test_recovery_cancellation_is_preserved_and_does_not_publish(self):
        self.recoverable()
        first = Cancel('original cancellation')
        self.runtime_status.side_effect = first
        with self.assertRaises(Cancel) as caught:
            B.recover_training(self.directory.name)
        self.assertIs(caught.exception, first)
        self.assertFalse((self.directory / 'recovery.json').exists())


if __name__ == '__main__':
    unittest.main()
