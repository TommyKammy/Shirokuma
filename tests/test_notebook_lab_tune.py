"""Fake CV worker ownership and saved redisplay; no subprocess, API, or fit."""
from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.notebook_lab import tune as U, compare as B, runtime as R
from tools.local_experiment.notebook_lab import query as Q, train as L, record_comparison as I
from tests import test_notebook_lake_training_client as V
from tests.test_mlflow_tune import fitted, request


class LabTuningTests(unittest.TestCase):
    def setUp(self):
        self.fixture = V.LakeTrainingReviewTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        temporary = tempfile.TemporaryDirectory(dir=B.ROOT / 'tmp')
        self.addCleanup(temporary.cleanup)
        self.lab = Path(temporary.name).resolve() / 'lab'
        self.lab.mkdir(mode=0o700)
        self.state = self.lab / 'tunings'
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module in (U, B):
            self.stack.enter_context(patch.object(module, 'LAB_STATE', self.lab))
        self.stack.enter_context(patch.object(U, 'STATE', self.state))
        self.stack.enter_context(patch.object(B, 'STATE', self.lab / 'comparisons'))
        self.stack.enter_context(patch.object(R, 'STATE', self.lab))
        self.stack.enter_context(patch.object(B.T, 'group_empty', return_value=True))
        self.fit = self.stack.enter_context(patch.object(U.C.C, '_fit', side_effect=fitted))
        self.stack.enter_context(patch.object(B.subprocess, 'Popen', side_effect=AssertionError('process prohibited')))
        self.stack.enter_context(patch.object(B.N, '_process_start', return_value='test start'))

    def value(self):
        value = U.C.tune(request())
        self.fit.reset_mock()
        return value

    def child(self, value=None):
        child = Mock(pid=4567, returncode=0)
        child.poll.return_value = 0
        def popen(argv, **kwargs):
            self.assertEqual(argv, U.arguments(Path(argv[-1])))
            self.assertTrue(kwargs['start_new_session'])
            self.assertEqual(kwargs['umask'], 0o077)
            if value is not None:
                kwargs['stdout'].write(U.N._canonical(value))
                kwargs['stdout'].flush()
            return child
        return child, popen

    def execute(self, value=None):
        child, popen = self.child(self.value() if value is None else value)
        with patch.object(B.subprocess, 'Popen', side_effect=popen):
            result = U.tune(V.TOKEN)
        return result, child

    def test_success_one_owned_worker_and_environment_unchanged(self):
        before = dict(os.environ)
        result, child = self.execute()
        self.assertEqual(dict(os.environ), before)
        child.wait.assert_called_once_with(timeout=B.SECONDS)
        self.assertEqual(result['comparison']['cv']['fit_count'], 10)
        self.assertEqual(result['profile'], U.PROFILE)
        self.assertTrue(U.check_idle())
        self.fit.assert_not_called()
        for path in self.state.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
        self.assertFalse(B.STATE.exists())

    def test_saved_redisplay_checks_receipts_without_fit_or_reused_pid_probe(self):
        public, _ = self.execute()
        self.fit.reset_mock()
        with patch.object(B.T, 'group_empty', side_effect=AssertionError('historical PID is not live ownership')):
            saved = U.recover_tuning(public['attempt_token'])
            self.assertTrue(U.check_idle())
        self.assertTrue(saved.pop('saved_result'))
        self.assertEqual(saved, public)
        self.fit.assert_not_called()

    def test_saved_tampered_fold_and_receipt_are_rejected(self):
        public, _ = self.execute()
        path = self.state / public['attempt_token'] / 'result.json'
        value = json.loads(path.read_bytes())
        value['comparison']['cv']['selected_alpha'] = 10
        U.N._write(path, value, replace=True)
        with self.assertRaisesRegex(U.N.NotebookError, 'LAB_TUNING_SAVED_INVALID'):
            U.recover_tuning(public['attempt_token'])
        self.fit.assert_not_called()

    def test_failed_source_validation_retains_receipts_without_adopting_result(self):
        value = self.value()
        value['candidate']['metrics']['test']['rmse'] += 1
        child, popen = self.child(value)
        with patch.object(B.subprocess, 'Popen', side_effect=popen):
            with self.assertRaisesRegex(U.N.NotebookError, 'LAB_TUNING_FAILED'):
                U.tune(V.TOKEN)
        directory = next(self.state.iterdir())
        self.assertFalse((directory / 'result.json').exists())
        self.assertEqual(U.tuning_status(directory)['state'], 'failed')
        self.assertTrue(U.check_idle())
        self.fit.assert_not_called()

    def test_invalid_training_token_allocates_nothing(self):
        for token in (None, True, '../outside', 'A' * 32):
            with self.subTest(token=token), self.assertRaises(U.N.NotebookError):
                U.tune(token)
        self.assertFalse(self.state.exists())

    def test_unknown_tuning_blocks_every_owner_entry_before_any_allocation(self):
        self.state.mkdir(mode=0o700)
        (self.state / ('a' * 32)).mkdir(mode=0o700)
        self.assertFalse(U.check_idle())
        with patch.object(R, '_prepare', side_effect=AssertionError('seed forbidden')), \
             patch.object(R, 'status', side_effect=AssertionError('service forbidden')):
            for action in (R.start, R.stop):
                with self.subTest(action=action.__name__), self.assertRaisesRegex(R.LabError, 'LAB_TUNING_ACTIVE_OR_UNKNOWN'):
                    action()
        for enter in (Q._lock, B._lock, L._lock, I._lock):
            with self.subTest(lock=enter.__module__), self.assertRaisesRegex(U.N.NotebookError, 'CLEANUP_UNKNOWN'):
                with enter():
                    self.fail('unknown tuning must block this entry')
        with self.assertRaisesRegex(U.N.NotebookError, 'LAB_TUNING_CLEANUP_UNKNOWN'):
            U.tune(V.TOKEN)
        self.assertEqual(len(list(self.state.iterdir())), 1)

    def test_unknown_other_work_blocks_tuning_without_allocation(self):
        for name in ('_recordings_idle', '_trainings_idle', '_queries_idle'):
            with self.subTest(name=name), patch.object(R, name, return_value=False):
                with self.assertRaisesRegex(U.N.NotebookError, 'LAB_TUNING_CLEANUP_UNKNOWN'):
                    U.tune(V.TOKEN)
        with patch.object(B, 'check_idle', return_value=False):
            with self.assertRaisesRegex(U.N.NotebookError, 'LAB_TUNING_CLEANUP_UNKNOWN'):
                U.tune(V.TOKEN)
        self.assertFalse(self.state.exists())

    def test_cancel_identity_survives_secondary_status_save_failure(self):
        cancel = KeyboardInterrupt('original')
        child, popen = self.child()
        child.wait.side_effect = cancel
        original_write = U.N._write
        def write(path, value, **kwargs):
            if path.name == 'status.json' and value['state'] == 'interrupted':
                raise OSError('secondary')
            return original_write(path, value, **kwargs)
        with patch.object(B.subprocess, 'Popen', side_effect=popen), patch.object(U.N, '_write', side_effect=write):
            with self.assertRaises(KeyboardInterrupt) as caught:
                U.tune(V.TOKEN)
        self.assertIs(caught.exception, cancel)
        directory = next(self.state.iterdir())
        self.assertFalse((directory / 'result.json').exists())
        self.assertFalse(U.check_idle())

    def test_nonempty_group_stays_unknown_and_no_automatic_retry(self):
        child, popen = self.child(self.value())
        with patch.object(B.subprocess, 'Popen', side_effect=popen) as spawn, \
             patch.object(B.T, 'group_empty', return_value=False):
            with self.assertRaisesRegex(U.N.NotebookError, 'CLEANUP_UNKNOWN'):
                U.tune(V.TOKEN)
            with self.assertRaisesRegex(U.N.NotebookError, 'CLEANUP_UNKNOWN'):
                U.tune(V.TOKEN)
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(U.tuning_status(next(self.state.iterdir()))['state'], 'unknown')

    def test_worker_requires_parent_handshake_before_one_core_call(self):
        public, _ = self.execute()
        directory = self.state / public['attempt_token']
        status = U.tuning_status(directory)
        status.update(state='running', pid=os.getpid(), pgid=os.getpid(), exit_code=None,
                      child_exited=False, cleanup_verified=False)
        U.N._write(directory / 'status.json', status, replace=True)
        U.N._write(directory / 'launch.json', dict(pid=os.getpid(), ppid=os.getppid(), pgid=os.getpid(),
                   process_start='test start'), replace=True)
        calculate = Mock(return_value=0)
        with patch.object(os, 'getpgrp', return_value=os.getpid()):
            self.assertEqual(U.worker(directory, calculate=calculate), 0)
        calculate.assert_called_once_with(['--training-token', V.TOKEN])
        status['pid'] += 1
        U.N._write(directory / 'status.json', status, replace=True)
        with self.assertRaises(U.N.NotebookError):
            U.worker(directory, calculate=calculate)
        self.assertEqual(calculate.call_count, 1)

    def test_renderer_exposes_selection_fold_scores_and_holdout_caveat(self):
        public, _ = self.execute()
        public['attempt_token'] = '<script>unsafe</script>'
        html = U.render_tuning(public)
        self.assertIn('平均 validation MSE', html)
        self.assertIn('fold 3 MSE', html)
        self.assertIn('独立した最終テストではありません', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertNotIn(str(self.lab), html)
        self.fit.assert_not_called()

    def cells(self):
        book = json.loads(R.TUNING_TEMPLATE.read_bytes())
        self.assertTrue(all(not c.get('outputs') and c.get('execution_count') is None
                            for c in book['cells'] if c['cell_type'] == 'code'))
        return [''.join(c['source']) for c in book['cells']]

    def test_initial_notebook_runs_no_fit_or_saved_reads(self):
        cells = self.cells()
        no_call = Mock(side_effect=AssertionError('initial execution forbidden'))
        namespace = dict(tune=no_call, recover_tuning=no_call, render_tuning=no_call)
        for index in (3, 4, 6):
            exec(cells[index], namespace)
        no_call.assert_not_called()
        self.assertIsNone(namespace['tuning_result'])
        self.assertIsNone(namespace['saved_tuning'])

    def test_render_cancellation_or_error_clears_public_and_intermediate_results(self):
        cells = self.cells()
        for failure in (KeyboardInterrupt('cancel'), RuntimeError('unexpected'), U.N.NotebookError('LAB_TUNING_FAILED')):
            for saved in (False, True):
                with self.subTest(error=type(failure).__name__, saved=saved):
                    namespace = dict(training_token=V.TOKEN, run_tuning=True, tune=Mock(return_value={'partial': True}),
                        recover_tuning=Mock(return_value={'partial': True}), NotebookError=U.N.NotebookError,
                        render_tuning=Mock(side_effect=failure), display=Mock(), HTML=lambda x: x)
                    source = cells[6].replace('tuning_attempt = ""', 'tuning_attempt = "' + 'a' * 32 + '"') if saved else cells[4]
                    if isinstance(failure, U.N.NotebookError):
                        exec(source, namespace)
                    else:
                        with self.assertRaises(type(failure)) as caught:
                            exec(source, namespace)
                        self.assertIs(caught.exception, failure)
                    keys = ('saved_tuning', 'saved_tuning_html', 'saved_candidate', 'saved_candidate_html') if saved else (
                        'tuning_result', 'tuning_html', 'candidate', 'candidate_html')
                    self.assertTrue(all(namespace[key] is None for key in keys))
                    namespace['display'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
