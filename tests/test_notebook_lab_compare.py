"""Fake comparison worker lifecycle and saved-source replay; no actual fit."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.notebook_lab import compare as B, runtime as R
from tests import test_notebook_lake_training_client as V
from tests.test_mlflow_compare import fitted, request


class LabComparisonTests(unittest.TestCase):
    def setUp(self):
        self.fixture = V.LakeTrainingReviewTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.temp = tempfile.TemporaryDirectory(dir=B.ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.lab = Path(self.temp.name).resolve() / 'lab'
        self.lab.mkdir(mode=0o700)
        self.state = self.lab / 'comparisons'
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(B, 'LAB_STATE', self.lab))
        self.stack.enter_context(patch.object(B, 'STATE', self.state))
        self.stack.enter_context(patch.object(R, 'STATE', self.lab))
        self.stack.enter_context(patch.object(R, '_queries_idle', return_value=True))
        self.stack.enter_context(patch.object(B.T, 'group_empty', return_value=True))
        self.stack.enter_context(patch.object(B.C, '_fit', side_effect=fitted))
        self.stack.enter_context(patch.object(B.subprocess, 'Popen', side_effect=AssertionError('real process prohibited')))
        self.stack.enter_context(patch.object(B.N, '_process_start', return_value='test start'))

    def value(self):
        return B.C.compare(request())

    def child(self, frame=None, *, code=0):
        child = Mock(pid=4567, returncode=code)
        child.poll.return_value = code
        def popen(argv, **kwargs):
            self.assertEqual(argv, B.arguments(Path(argv[-1])))
            self.assertEqual(argv[0], str(B.ROOT / 'tools/local_experiment/mlflow/.venv/bin/python'))
            self.assertTrue(kwargs['start_new_session'])
            self.assertEqual(kwargs['umask'], 0o077)
            if frame is not None:
                kwargs['stdout'].write(B.N._canonical(frame))
                kwargs['stdout'].flush()
            return child
        return child, popen

    def only_directory(self):
        return next(self.state.iterdir())

    def test_success_fixed_worker_source_bound_and_parent_environment_unchanged(self):
        value = self.value()
        child, popen = self.child(value)
        before = dict(os.environ)
        with patch.object(B.subprocess, 'Popen', side_effect=popen) as calls:
            result = B.compare(V.TOKEN, 10)
        self.assertEqual(os.environ, before)
        self.assertEqual(calls.call_count, 1)
        child.wait.assert_called_once_with(timeout=B.SECONDS)
        self.assertEqual(result['comparison'], value)
        self.assertTrue(result['cleanup_verified'])
        directory = self.state / result['attempt_token']
        self.assertTrue(B.comparison_closed(directory))
        self.assertTrue(B.check_idle())
        self.assertEqual(B.comparison_status(directory)['state'], 'passed')
        self.assertEqual(json.loads((directory / 'result.json').read_bytes()), result)
        for path in directory.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
        environment = B.environment(directory)
        self.assertEqual(environment['OMP_NUM_THREADS'], '1')
        for key in ('HOME', 'KUBECONFIG', 'HTTP_PROXY', 'MLFLOW_TRACKING_URI', V.T.TOKEN_ENV):
            self.assertNotIn(key, environment)

    def test_parent_rejects_changed_prediction_without_refitting_but_cleanup_known(self):
        value = self.value()
        value['holdout'][0]['candidate_prediction'] += 1
        child, popen = self.child(value)
        with patch.object(B.subprocess, 'Popen', side_effect=popen), \
             patch.object(B.C, '_fit', side_effect=AssertionError('parent fit prohibited')):
            with self.assertRaisesRegex(B.N.NotebookError, 'LAB_COMPARISON_FAILED'):
                B.compare(V.TOKEN, 10)
        status = B.comparison_status(self.only_directory())
        self.assertEqual(status['state'], 'failed')
        self.assertTrue(status['cleanup_verified'])
        self.assertTrue(B.check_idle())
        self.assertFalse((self.only_directory() / 'result.json').exists())

    def test_cancellation_retained_when_worker_exits_without_stdout(self):
        child, popen = self.child(code=130)
        first = KeyboardInterrupt('first')
        child.wait.side_effect = first
        with patch.object(B.subprocess, 'Popen', side_effect=popen), \
             patch.object(B, '_interrupt', side_effect=lambda child, error: error) as interrupt:
            with self.assertRaises(KeyboardInterrupt) as caught:
                B.compare(V.TOKEN, 10)
        self.assertIs(caught.exception, first)
        interrupt.assert_called_once_with(child, first)
        self.assertEqual(B.comparison_status(self.only_directory())['state'], 'interrupted')
        self.assertTrue(B.check_idle())
        self.assertFalse((self.only_directory() / 'result.json').exists())

    def test_unknown_process_group_blocks_next_attempt_without_retry(self):
        child, popen = self.child(self.value())
        with patch.object(B.subprocess, 'Popen', side_effect=popen) as calls, \
             patch.object(B.T, 'group_empty', return_value=False):
            with self.assertRaisesRegex(B.N.NotebookError, 'CLEANUP_UNKNOWN'):
                B.compare(V.TOKEN, 10)
            self.assertFalse(B.check_idle())
            with self.assertRaisesRegex(B.N.NotebookError, 'CLEANUP_UNKNOWN'):
                B.compare(V.TOKEN, 10)
            self.assertEqual(calls.call_count, 1)
        self.assertEqual(B.comparison_status(self.only_directory())['state'], 'unknown')

    def test_failed_spawn_is_closed_and_records_no_pid(self):
        with patch.object(B.subprocess, 'Popen', side_effect=OSError('spawn failed')):
            with self.assertRaisesRegex(B.N.NotebookError, 'LAB_COMPARISON_FAILED'):
                B.compare(V.TOKEN, 10)
        status = B.comparison_status(self.only_directory())
        self.assertIsNone(status['pid'])
        self.assertTrue(status['cleanup_verified'])
        self.assertTrue(B.check_idle())

    def test_process_start_failure_retains_owned_pid_and_exits_before_ack(self):
        child, popen = self.child(code=1)
        with patch.object(B.subprocess, 'Popen', side_effect=popen), \
             patch.object(B.N, '_process_start', side_effect=OSError('ps failed')):
            with self.assertRaisesRegex(B.N.NotebookError, 'LAB_COMPARISON_FAILED'):
                B.compare(V.TOKEN, 10)
        status = B.comparison_status(self.only_directory())
        self.assertEqual(status['pid'], 4567)
        self.assertTrue(status['cleanup_verified'])
        self.assertFalse((self.only_directory() / 'launch.json').exists())

    def test_cancel_and_final_status_save_failure_keep_cancel_and_stale_running_unclosed(self):
        child, popen = self.child(code=130)
        first = KeyboardInterrupt('first')
        child.wait.side_effect = first
        original = B.N._write
        def write(path, value, **kwargs):
            if path.name == 'status.json' and value['state'] == 'interrupted':
                raise OSError('last save failed')
            return original(path, value, **kwargs)
        with patch.object(B.subprocess, 'Popen', side_effect=popen), \
             patch.object(B, '_interrupt', side_effect=lambda child, error: error), patch.object(B.N, '_write', side_effect=write):
            with self.assertRaises(KeyboardInterrupt) as caught:
                B.compare(V.TOKEN, 10)
        self.assertIs(caught.exception, first)
        self.assertEqual(B.comparison_status(self.only_directory())['state'], 'running')
        self.assertFalse(B.check_idle())

    def test_bad_input_and_busy_lock_prevent_allocation(self):
        for token, alpha in (('../x', 10), (V.TOKEN, True), (V.TOKEN, 1001)):
            with self.assertRaisesRegex(B.N.NotebookError, 'LAB_COMPARISON_INVALID'):
                B.compare(token, alpha)
        self.assertFalse(self.state.exists())
        with R._lock():
            with self.assertRaisesRegex(B.N.NotebookError, 'LAB_COMPARISON_BUSY'):
                B.compare(V.TOKEN, 10)
        self.assertFalse(self.state.exists())
        with B._lock():
            with self.assertRaisesRegex(R.LabError, 'LAB_BUSY'), R._lock():
                self.fail('runtime acquired the active comparison lock')

    def test_unknown_query_blocks_comparison_before_allocation(self):
        with patch.object(R, '_queries_idle', return_value=False) as check:
            with self.assertRaisesRegex(B.N.NotebookError, 'LAB_COMPARISON_CLEANUP_UNKNOWN'):
                B.compare(V.TOKEN, 10)
        check.assert_called_once_with()
        self.assertFalse(self.state.exists())

    def test_worker_checks_fixed_request_owner_then_delegates_once(self):
        self.state.mkdir(mode=0o700)
        directory = self.state / ('f' * 32)
        directory.mkdir(mode=0o700)
        B.N._write(directory / 'input.json', request())
        B.N._write(directory / 'intent.json', dict(schema_version=1, profile=B.PROFILE,
                  argv=B.arguments(directory), request_sha256=B.C.digest(request())))
        B.N._write(directory / 'status.json', dict(schema_version=1, state='running', pid=4567, pgid=4567,
                  process_start='test start', evidence_token=directory.name, exit_code=None,
                  child_exited=False, cleanup_verified=False, error_code=None))
        B.N._write(directory / 'launch.json', dict(pid=4567, ppid=4566, pgid=4567, process_start='test start'))
        calculate = Mock(return_value=0)
        with patch.object(B.os, 'getpid', return_value=4567), patch.object(B.os, 'getppid', return_value=4566), \
             patch.object(B.os, 'getpgrp', return_value=4567):
            self.assertEqual(B.worker(directory, calculate=calculate), 0)
            self.assertEqual(calculate.call_args.args, (['--request-stdin'],))
            self.assertEqual(json.loads(calculate.call_args.kwargs['stdin'].read()), request())
            B.N._write(directory / 'input.json', request(11), replace=True)
            with self.assertRaises(B.N.NotebookError):
                B.worker(directory, calculate=calculate)
        self.assertEqual(calculate.call_count, 1)

    def test_interrupt_signals_only_owned_child_once_and_preserves_first(self):
        first = KeyboardInterrupt('original')
        child = Mock(pid=4567)
        child.poll.side_effect = [None, None, 130]
        child.wait.side_effect = OSError('wait failed')
        with patch.object(B.os, 'kill', side_effect=OSError('signal failed')) as signal:
            self.assertIs(B._interrupt(child, first), first)
        signal.assert_called_once_with(4567, B.signal.SIGINT)

    def test_output_close_failure_cannot_replace_first_cancel(self):
        first = KeyboardInterrupt('original')
        streams = [Mock(), Mock()]
        for stream in streams:
            stream.close.side_effect = OSError('close failed')
        with patch.object(B.os, 'open', return_value=123), patch.object(B.os, 'fdopen', side_effect=streams):
            with self.assertRaises(KeyboardInterrupt) as caught:
                with B._outputs(self.lab):
                    raise first
        self.assertIs(caught.exception, first)
        for stream in streams:
            stream.close.assert_called_once()

    def test_japanese_renderer_escape_and_output_free_notebook(self):
        value = self.value()
        value['identity']['attempt_token'] = '<script>bad</script>'
        result = dict(profile=B.PROFILE, result='passed', cleanup_verified=True,
                      attempt_token='<img>', comparison=value)
        rendered = B.render_comparison(result)
        self.assertIn('探索的比較', rendered)
        self.assertIn('独立した最終評価ではありません', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertNotIn('<script>', rendered)
        self.assertEqual(rendered.count('<table>'), 2)
        self.assertEqual(rendered.count('<tr>'), 22)
        notebook = json.loads((B.ROOT / 'examples/local_ridge_comparison.ipynb').read_bytes())
        import nbformat
        nbformat.validate(nbformat.from_dict(notebook))
        cells = [cell for cell in notebook['cells'] if cell['cell_type'] == 'code']
        self.assertEqual(len(cells), 3)
        for cell in cells:
            self.assertEqual(cell['outputs'], [])
            self.assertIsNone(cell['execution_count'])
            compile(''.join(cell['source']), '<comparison notebook>', 'exec')


if __name__ == '__main__':
    unittest.main()
