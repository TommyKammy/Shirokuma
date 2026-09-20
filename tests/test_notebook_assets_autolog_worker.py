"""Owned worker protocol with private fake ML receipts; no fit/API/process."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.notebook_assets import autolog_worker as W
from tests import test_mlflow_autolog as F


class AutologWorkerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = F.AutologTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.root = self.fixture.fixture.root
        self.state = self.root / 'tmp/local-experiment/notebook-training-assets'
        self.state.mkdir(parents=True, mode=0o700)
        for path in (self.state.parent, self.state.parent.parent): path.chmod(0o700)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(W, 'ROOT', self.root))
        self.stack.enter_context(patch.object(W, 'STATE', self.state))
        self.spawn = self.stack.enter_context(patch.object(W.subprocess, 'Popen', side_effect=AssertionError('real process forbidden')))
        self.processes = self.stack.enter_context(patch.object(W.R, 'processes', return_value={}))
        self.stack.enter_context(patch.object(W.N, '_process_start', return_value='fake start'))
        self.stopped = self.stack.enter_context(patch.object(W.R, 'require_stopped', return_value={'store_id': F.L.STORE}))
        self.environment = self.stack.enter_context(patch.object(W.R, 'environment', return_value={'PATH': '/fixed/ML/bin'}))
        self.backup_path = self.root / self.fixture.backup['manifest']
        self.backup_path.parent.mkdir(parents=True, mode=0o700)
        self.backup_path.write_bytes(b'{"fixture":true}\n'); self.backup_path.chmod(0o600)
        self.fixture.backup['sha256'] = hashlib.sha256(self.backup_path.read_bytes()).hexdigest()
        self.backup = self.stack.enter_context(patch.object(W.A.G, 'validate_backup', return_value=deepcopy(self.fixture.backup)))
        self.source_sha = W.A.C.digest(W.A.Snapshot(F.L.TOKEN).load())
        self.count = 0

    def output(self):
        self.count += 1
        parent = self.state / ('%032x' % self.count)
        parent.mkdir(mode=0o700)
        result = parent / 'autolog'; result.mkdir(mode=0o700)
        return result

    def fake_child(self, *, fit=True, code=0, change=None):
        child = Mock(pid=4567, returncode=code)
        child.poll.return_value = code
        def spawn(argv, **kwargs):
            self.assertEqual(argv, W.arguments(F.L.TOKEN, self.fixture.backup['manifest']))
            self.assertEqual(kwargs['cwd'], self.root)
            self.assertEqual(kwargs['env'], {'PATH': '/fixed/ML/bin'})
            self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
            self.assertTrue(kwargs['start_new_session']); self.assertEqual(kwargs['umask'], 0o077)
            if fit:
                receipt = self.fixture.owner().run(F.L.TOKEN)
                value = W.A.public(receipt)
                if change: change(value)
                os.write(kwargs['stdout'], W.N._canonical(value))
            return child
        return child, spawn

    def execute(self, output=None, **kwargs):
        output = output or self.output()
        child, spawn = self.fake_child(**kwargs)
        with patch.object(W.subprocess, 'Popen', side_effect=spawn) as calls:
            value = W.run(output, F.L.TOKEN, self.fixture.backup['manifest'], source_sha256=self.source_sha)
        return value, output, child, calls

    def test_one_fixed_worker_binds_source_backup_output_and_saved_receipt(self):
        before = dict(os.environ)
        value, output, child, calls = self.execute()
        self.assertEqual(dict(os.environ), before)
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(len(self.fixture.fit_calls), 1)
        self.assertFalse(value['reused']); self.assertTrue(value['cleanup_verified'])
        self.assertEqual(value['source_sha256'], self.source_sha)
        self.assertEqual(value['autolog'], W.A.public(W.A.recorded(F.L.TOKEN)))
        self.assertTrue(W.closed(output))
        self.assertEqual(W.status(output)['state'], 'passed')
        for path in output.iterdir(): self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(W.WorkerError, 'ALREADY_ATTEMPTED'):
            W.run(output, F.L.TOKEN, source_sha256=self.source_sha)
        self.assertEqual(len(self.fixture.fit_calls), 1)

    def test_completed_repeat_reads_receipts_but_no_child_runtime_or_caller_manifest(self):
        self.fixture.owner().run(F.L.TOKEN)
        before = self.fixture.files()
        for mock in (self.stopped, self.backup, self.environment, self.processes):
            mock.side_effect = AssertionError('completed repeat must not access runtime/external backup')
        value = W.run(self.output(), F.L.TOKEN, 'does-not-exist', source_sha256=self.source_sha)
        self.assertTrue(value['reused'])
        self.assertEqual(self.fixture.files(), before)
        self.spawn.assert_not_called()
        self.assertEqual(len(self.fixture.fit_calls), 1)

    def test_pending_unknown_index_refuses_before_child_and_intent(self):
        self.fixture.client.fail_create = True
        with self.assertRaises(Exception): self.fixture.owner().run(F.L.TOKEN)
        output = self.output()
        with self.assertRaises(Exception):
            W.run(output, F.L.TOKEN, self.fixture.backup['manifest'], source_sha256=self.source_sha)
        self.assertEqual(list(output.iterdir()), [])
        self.spawn.assert_not_called()
        self.assertEqual(self.fixture.fit_calls, [])

    def test_pre_index_unknown_blocks_new_attempt_and_other_token(self):
        output = self.output()
        child, spawn = self.fake_child(fit=False)
        child.poll.return_value = None
        with patch.object(W.subprocess, 'Popen', side_effect=spawn), \
             patch.object(W.R, 'processes', return_value={4567: {'pgid': 4567}}):
            with self.assertRaisesRegex(W.WorkerError, 'CLEANUP_UNKNOWN'):
                W.run(output, F.L.TOKEN, self.fixture.backup['manifest'], source_sha256=self.source_sha)
        self.assertFalse(W.closed(output))
        self.assertFalse((self.fixture.state / W.A.C.INDEX_NAME).exists())
        for token in (F.L.TOKEN, 'b' * 32):
            another = self.output()
            with self.subTest(token=token), self.assertRaisesRegex(W.WorkerError, 'CLEANUP_UNKNOWN'):
                W.run(another, token, self.fixture.backup['manifest'], source_sha256=self.source_sha)
            self.assertEqual(list(another.iterdir()), [])
        self.spawn.assert_not_called()

    def test_source_mismatch_and_unsafe_path_fail_before_launch(self):
        with self.assertRaisesRegex(W.WorkerError, 'SOURCE_CHANGED'):
            W.run(self.output(), F.L.TOKEN, source_sha256='0' * 64)
        for name in ('../backup-manifest.json', '/tmp/backup-manifest.json', 'tmp/local-experiment/a/../backup-manifest.json'):
            with self.subTest(name=name), self.assertRaises(W.WorkerError):
                W.run(self.output(), F.L.TOKEN, name, source_sha256=self.source_sha)
        self.spawn.assert_not_called()

    def test_source_change_during_worker_refuses_adoption_without_second_fit(self):
        child, spawn = self.fake_child()
        def change(*args, **kwargs):
            result = spawn(*args, **kwargs)
            path = self.fixture.fixture.workload_path
            path.write_bytes(path.read_bytes() + b' ')
            return result
        output = self.output()
        with patch.object(W.subprocess, 'Popen', side_effect=change):
            with self.assertRaisesRegex(W.WorkerError, 'FAILED'):
                W.run(output, F.L.TOKEN, self.fixture.backup['manifest'], source_sha256=self.source_sha)
        self.assertFalse((output / 'result.json').exists())
        self.assertTrue(W.closed(output)); self.assertEqual(len(self.fixture.fit_calls), 1)

    def test_backup_change_after_intent_refuses_before_child(self):
        original = W._save
        def save(path, value, **kwargs):
            result = original(path, value, **kwargs)
            if path.name == 'status.json' and value['state'] == 'pending':
                self.backup_path.write_bytes(b'{"changed":true}\n')
            return result
        with patch.object(W, '_save', side_effect=save):
            with self.assertRaisesRegex(W.WorkerError, 'FAILED'):
                W.run(self.output(), F.L.TOKEN, self.fixture.backup['manifest'], source_sha256=self.source_sha)
        self.spawn.assert_not_called()

    def test_changed_public_output_is_not_adopted(self):
        output = self.output()
        with self.assertRaisesRegex(W.WorkerError, 'FAILED'):
            self.execute(output, change=lambda value: value.update(run_id='f' * 32))
        self.assertFalse((output / 'result.json').exists())
        self.assertTrue(W.closed(output))

    def test_output_limit_is_enforced_before_result_adoption(self):
        child, spawn = self.fake_child(fit=False)
        def oversized(*args, **kwargs):
            result = spawn(*args, **kwargs)
            os.write(kwargs['stdout'], b'x' * (W.MAX_STDOUT + 1))
            return result
        output = self.output()
        with patch.object(W.subprocess, 'Popen', side_effect=oversized):
            with self.assertRaisesRegex(W.WorkerError, 'FAILED'):
                W.run(output, F.L.TOKEN, self.fixture.backup['manifest'], source_sha256=self.source_sha)
        self.assertFalse((output / 'result.json').exists())
        self.assertEqual(self.fixture.fit_calls, [])

    def test_first_cancel_preserved_when_final_status_save_fails(self):
        cancel = KeyboardInterrupt('first')
        child, spawn = self.fake_child(fit=False)
        child.wait.side_effect = cancel
        child.poll.side_effect = [None, 0, 0]
        original = W._save
        def save(path, value, **kwargs):
            if path.name == 'status.json' and value['state'] == 'interrupted': raise OSError('secondary')
            return original(path, value, **kwargs)
        output = self.output()
        with patch.object(W.subprocess, 'Popen', side_effect=spawn), patch.object(W.os, 'killpg') as sent, \
             patch.object(W, '_save', side_effect=save):
            with self.assertRaises(KeyboardInterrupt) as caught:
                W.run(output, F.L.TOKEN, self.fixture.backup['manifest'], source_sha256=self.source_sha)
        self.assertIs(caught.exception, cancel)
        sent.assert_called_once_with(child.pid, signal.SIGINT)
        self.assertFalse(W.closed(output))
        self.assertFalse((output / 'result.json').exists())

    def test_cancel_never_signals_changed_process_identity(self):
        cancel = KeyboardInterrupt('first')
        child, spawn = self.fake_child(fit=False)
        child.wait.side_effect = cancel
        child.poll.return_value = None
        output = self.output()
        with patch.object(W.subprocess, 'Popen', side_effect=spawn), \
             patch.object(W.N, '_process_start', side_effect=['first start', 'changed start']), \
             patch.object(W.os, 'killpg') as sent, \
             patch.object(W.R, 'processes', return_value={child.pid: {'pgid': child.pid}}):
            with self.assertRaises(KeyboardInterrupt) as caught:
                W.run(output, F.L.TOKEN, self.fixture.backup['manifest'], source_sha256=self.source_sha)
        self.assertIs(caught.exception, cancel); sent.assert_not_called()
        self.assertFalse(W.closed(output))

    def test_timeout_sends_one_interrupt_then_retains_unknown_without_retry(self):
        child, spawn = self.fake_child(fit=False)
        child.poll.return_value = None
        child.wait.side_effect = subprocess.TimeoutExpired('fixed worker', .2)
        output = self.output()
        with patch.object(W.subprocess, 'Popen', side_effect=spawn) as calls, \
             patch.object(W.time, 'monotonic', side_effect=[0, W.SECONDS + 1, 1000, 1000 + W.GRACE_SECONDS + 1]), \
             patch.object(W.os, 'killpg') as sent, \
             patch.object(W.R, 'processes', return_value={child.pid: {'pgid': child.pid}}):
            with self.assertRaisesRegex(W.WorkerError, 'CLEANUP_UNKNOWN'):
                W.run(output, F.L.TOKEN, self.fixture.backup['manifest'], source_sha256=self.source_sha)
        sent.assert_called_once_with(child.pid, signal.SIGINT)
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(W.status(output)['state'], 'unknown')
        self.assertFalse(W.closed(output))

    def test_symlink_output_and_dangling_prior_attempt_are_rejected(self):
        output = self.output()
        output.rmdir(); output.symlink_to(self.root / 'absent', target_is_directory=True)
        with self.assertRaises(W.WorkerError):
            W.run(output, F.L.TOKEN, source_sha256=self.source_sha)
        with self.assertRaisesRegex(W.WorkerError, 'CLEANUP_UNKNOWN'):
            W.run(self.output(), F.L.TOKEN, source_sha256=self.source_sha)
        self.spawn.assert_not_called()

    def test_completed_closure_is_saved_only_and_tampering_is_unknown(self):
        value, output, _, _ = self.execute()
        with patch.object(W.R, 'processes', side_effect=AssertionError('no historical PID inspection')):
            self.assertTrue(W.closed(output))
        path = output / 'result.json'
        saved = json.loads(path.read_bytes()); saved['source_sha256'] = 'f' * 64
        W._save(path, saved, replace=True)
        self.assertFalse(W.closed(output))

    def test_owned_flock_prevents_parallel_start(self):
        output = self.output()
        with W._lock(output):
            with self.assertRaisesRegex(W.WorkerError, 'BUSY'):
                W.run(self.output(), F.L.TOKEN, source_sha256=self.source_sha)
        self.spawn.assert_not_called()

    def test_durable_save_preserves_first_cancel_over_close_error(self):
        cancel = KeyboardInterrupt('write interrupted')
        original_close = os.close
        def close(fd):
            original_close(fd)
            raise OSError('close')
        with patch.object(W.os, 'write', side_effect=cancel), patch.object(W.os, 'close', side_effect=close):
            with self.assertRaises(KeyboardInterrupt) as caught:
                W._save(self.output() / 'test.json', {})
        self.assertIs(caught.exception, cancel)


if __name__ == '__main__':
    unittest.main()
