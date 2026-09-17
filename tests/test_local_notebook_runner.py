"""Owned Notebook lifecycle: fakes and two harmless dedicated-kernel checks.

No Reader, MLflow, cluster, model, global kernel registration or forced kill.
"""
import asyncio
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import signal
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import nbformat
from tools.local_experiment.notebook import runner as R


class FakeKernel:
    def __init__(self):
        self.calls = []
        self.alive = True
        self.started = asyncio.Event()
        self.interrupted = asyncio.Event()
        self.reader_cleaned = False
        self.interrupt_error = None
        self.shutdown_error = None
        self.provisioner = SimpleNamespace(pid=202, pgid=202, wait=self.reap)

    async def is_alive(self):
        return self.alive

    async def interrupt_kernel(self):
        self.calls.append('interrupt')
        self.interrupted.set()
        if self.interrupt_error is not None:
            raise self.interrupt_error

    async def request_shutdown(self, **kwargs):
        self.calls.append(('shutdown', self.reader_cleaned))
        if self.shutdown_error is not None:
            raise self.shutdown_error
        self.alive = False

    async def reap(self):
        self.calls.append('reap')

    async def cleanup_resources(self, **kwargs):
        self.calls.append('cleanup-resources')


class FakeNotebook:
    def __init__(self, kernel, *, complete=False, stalled=False):
        self.kernel = kernel
        self.complete = complete
        self.stalled = stalled
        self.nb = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell('pass')])
        self.code_cells_executed = 0
        self.kc = SimpleNamespace(stop_channels=lambda: kernel.calls.append('stop-channels'))

    async def async_execute_cell(self, cell, index, **kwargs):
        self.kernel.calls.append('cell')
        self.kernel.started.set()
        if not self.complete:
            if self.stalled:
                await asyncio.Event().wait()
            await self.kernel.interrupted.wait()
            await asyncio.sleep(0.02)
        self.kernel.reader_cleaned = True
        self.kernel.calls.append('reader-cleaned')
        self.code_cells_executed += 1

    def set_widgets_metadata(self):
        pass


class NotebookRunnerFakeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.kernel = FakeKernel()
        self.client = FakeNotebook(self.kernel)
        self.enterContext(patch.object(R, 'bind_reader', return_value=None))
        self.enterContext(patch.object(R, 'reader_closed', side_effect=lambda _: self.kernel.reader_cleaned))
        self.kill = self.enterContext(patch.object(R.os, 'kill', side_effect=AssertionError('unexpected signal')))
        self.killpg = self.enterContext(patch.object(R.os, 'killpg', side_effect=AssertionError('unexpected group signal')))

    async def execute(self, **kwargs):
        return await R.execute_owned(self.client, self.kernel, self.directory,
                                     timeout=1, reader_grace=0.2, kernel_grace=0.1, **kwargs)

    def assert_orderly(self):
        calls = self.kernel.calls
        self.assertIn(('shutdown', True), calls)
        self.assertLess(calls.index('reader-cleaned'), calls.index(('shutdown', True)))
        self.assertLess(calls.index(('shutdown', True)), calls.index('reap'))
        self.assertLess(calls.index('reap'), calls.index('cleanup-resources'))
        self.kill.assert_not_called()
        self.killpg.assert_not_called()

    async def test_success_waits_for_reader_before_graceful_shutdown(self):
        self.client.complete = True
        result = await self.execute()
        self.assertEqual(result['result'], 'passed')
        self.assertIsNone(result['error'])
        self.assertTrue(result['reader_cleanup_confirmed'])
        self.assertTrue(result['kernel_cleanup_confirmed'])
        self.assertNotIn('interrupt', self.kernel.calls)
        self.assert_orderly()

    async def test_preexisting_stop_never_schedules_a_cell(self):
        cancel = R.NotebookInterrupted(signal.SIGINT)
        self.kernel.reader_cleaned = True
        result = await self.execute(stop={'reason': cancel})
        self.assertIs(result['error'], cancel)
        self.assertEqual(result['result'], 'interrupted')
        self.assertNotIn('cell', self.kernel.calls)
        self.assertNotIn('interrupt', self.kernel.calls)
        self.assertTrue(result['kernel_cleanup_confirmed'])

    async def test_signal_keeps_first_object_across_interrupt_and_shutdown_errors(self):
        cancel = R.NotebookInterrupted(signal.SIGTERM)
        stop = {'reason': None}
        self.kernel.interrupt_error = OSError('PRIVATE_INTERRUPT_FAILURE')
        self.kernel.shutdown_error = OSError('PRIVATE_SHUTDOWN_FAILURE')
        task = asyncio.create_task(self.execute(stop=stop))
        await self.kernel.started.wait()
        stop['reason'] = cancel
        result = await task
        self.assertIs(result['error'], cancel)
        self.assertEqual(result['result'], 'unknown')
        self.assertTrue(result['reader_cleanup_confirmed'])
        self.assertFalse(result['kernel_cleanup_confirmed'])
        self.assertEqual(self.kernel.calls.count('interrupt'), 1)
        self.assertIn(('shutdown', True), self.kernel.calls)
        self.kill.assert_not_called()
        self.killpg.assert_not_called()

    async def test_external_task_cancel_still_interrupts_and_waits_for_cleanup(self):
        task = asyncio.create_task(self.execute())
        await self.kernel.started.wait()
        task.cancel('owner-cancel')
        result = await task
        self.assertIsInstance(result['error'], asyncio.CancelledError)
        self.assertEqual(result['error'].args, ('owner-cancel',))
        self.assertNotEqual(result['result'], 'passed')
        self.assertTrue(result['reader_cleanup_confirmed'])
        self.assertTrue(result['kernel_cleanup_confirmed'])
        self.assertEqual(self.kernel.calls.count('interrupt'), 1)
        self.assert_orderly()

    async def test_cancelled_error_identity_survives_secondary_interrupt_error(self):
        cancel = asyncio.CancelledError('first object')
        original_wait = asyncio.wait
        injected = False

        async def wait(*args, **kwargs):
            nonlocal injected
            if not injected:
                injected = True
                await self.kernel.started.wait()
                raise cancel
            return await original_wait(*args, **kwargs)

        self.kernel.interrupt_error = OSError('PRIVATE_SECONDARY')
        with patch.object(R.asyncio, 'wait', side_effect=wait):
            result = await self.execute()
        self.assertIs(result['error'], cancel)
        self.assertTrue(result['reader_cleanup_confirmed'])
        self.assertTrue(result['kernel_cleanup_confirmed'])
        self.assert_orderly()

    async def test_first_signal_during_timeout_grace_takes_priority_over_timeout(self):
        cancel = R.NotebookInterrupted(signal.SIGINT)
        stop = {'reason': None}
        task = asyncio.create_task(R.execute_owned(self.client, self.kernel, self.directory,
            stop=stop, timeout=0.01, reader_grace=0.2, kernel_grace=0.1))
        await self.kernel.interrupted.wait()
        # The execution deadline already caused the single interrupt. A later
        # first user cancellation must survive the remaining cleanup grace.
        stop['reason'] = cancel
        result = await task
        self.assertIs(result['error'], cancel)
        self.assertEqual(result['result'], 'interrupted')
        self.assertTrue(result['reader_cleanup_confirmed'])
        self.assertTrue(result['kernel_cleanup_confirmed'])
        self.assertEqual(self.kernel.calls.count('interrupt'), 1)
        self.assert_orderly()

    async def test_grace_expiry_preserves_unknown_and_never_requests_shutdown(self):
        self.client.stalled = True
        result = await R.execute_owned(self.client, self.kernel, self.directory,
            timeout=0.01, reader_grace=0.02, kernel_grace=0.01)
        self.assertEqual(result['result'], 'unknown')
        self.assertIsInstance(result['error'], TimeoutError)
        self.assertFalse(result['reader_cleanup_confirmed'])
        self.assertFalse(result['kernel_cleanup_confirmed'])
        self.assertEqual(self.kernel.calls.count('interrupt'), 1)
        self.assertFalse(any(isinstance(x, tuple) and x[0] == 'shutdown' for x in self.kernel.calls))
        self.assertNotIn('reap', self.kernel.calls)
        self.kill.assert_not_called()
        self.killpg.assert_not_called()


class NotebookRunnerOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        argv = [str(self.directory / 'shirokuma'), '--repo-root', str(R.ROOT),
                '--profile', 'local-experiment', 'query', 'select', '--table', 'smoke.fixture_v1',
                '--columns', 'id,name', '--limit', '10']
        self.owner = dict(pid=303, ppid=202, pgid=303,
            process_start='Fri Sep 18 07:00:00 2026', command=' '.join(argv))
        self.status = dict(schema_version=1, state='running', pid=303, pgid=303,
            process_start=self.owner['process_start'], evidence_token=None, exit_code=None,
            child_exited=False, cleanup_verified=False, error_code=None)

    def test_reader_status_rejects_partial_keys_and_bool_or_inconsistent_identity(self):
        valid = dict(self.status, state='passed', evidence_token='a' * 32,
                     exit_code=0, child_exited=True, cleanup_verified=True)
        path = self.directory / 'reader-status.json'
        for state, exit_code, error in [('passed', 0, None), ('failed', 1, 'NOTEBOOK_READER_FAILED'),
                                        ('interrupted', 130, 'NOTEBOOK_INTERRUPTED')]:
            value = dict(valid, state=state, exit_code=exit_code, error_code=error)
            path.write_text(json.dumps(value))
            self.assertEqual(R.reader_status(self.directory), value)
            self.assertTrue(R.reader_closed(self.directory))
        malformed = [
            {'child_exited': True, 'cleanup_verified': True},
            dict(valid, pid=True, pgid=True),
            dict(valid, exit_code=False),
            dict(valid, child_exited=1),
            dict(valid, pgid=999),
            dict(valid, evidence_token=None),
            dict(valid, state='running'),
            dict(valid, exit_code=None),
        ]
        for index, value in enumerate(malformed):
            with self.subTest(index=index):
                path.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError, 'Invalid saved Reader status'):
                    R.reader_closed(self.directory)

    def test_fixed_real_reader_argv_binds_only_matching_live_identity(self):
        with patch.object(R, 'reader_status', return_value=self.status), \
             patch.object(R, 'process_identity', return_value=self.owner):
            result = R.bind_reader(self.directory, 202)
        self.assertEqual(result, self.owner)
        self.assertEqual(json.loads((self.directory / 'reader-owner.json').read_text()), self.owner)
        for field, value in [('ppid', 999), ('pgid', 999), ('process_start', 'Fri Sep 18 07:00:01 2026'),
                             ('command', self.owner['command'] + ' --extra'),
                             ('command', self.owner['command'].replace('fixture_v1', 'another_table'))]:
            with self.subTest(field=field, value=value):
                damaged = dict(self.owner, **{field: value})
                with patch.object(R, 'reader_status', return_value=self.status), \
                     patch.object(R, 'process_identity', return_value=damaged), \
                     patch.object(R, 'write_json') as save:
                    self.assertIsNone(R.bind_reader(self.directory, 202))
                    save.assert_not_called()

    async def test_dead_kernel_only_allows_prebound_same_reader_one_interrupt(self):
        reparented = dict(self.owner, ppid=1)
        with patch.object(R, 'process_identity', side_effect=[reparented, None]), \
             patch.object(R.os, 'kill') as send, patch.object(R.os, 'killpg') as group:
            self.assertTrue(await R.interrupt_orphan(self.owner, timeout=0.01))
        send.assert_called_once_with(303, signal.SIGINT)
        group.assert_not_called()
        for mode in ('no-owner', 'pid', 'pgid', 'process_start', 'command'):
            with self.subTest(mode=mode):
                damaged = dict(reparented)
                if mode in ('pid', 'pgid'): damaged[mode] += 1
                elif mode != 'no-owner': damaged[mode] += 'changed'
                with patch.object(R, 'process_identity', return_value=damaged), patch.object(R.os, 'kill') as send:
                    self.assertFalse(await R.interrupt_orphan(None if mode == 'no-owner' else self.owner, timeout=0))
                send.assert_not_called()

    async def test_shutdown_timeout_never_escalates_or_claims_cleanup(self):
        kernel = FakeKernel()
        kernel.request_shutdown = AsyncMock()
        client = SimpleNamespace(stop_channels=lambda: self.fail('channels stopped over live kernel'))
        with patch.object(R.os, 'kill') as send, patch.object(R.os, 'killpg') as group:
            self.assertFalse(await R.shutdown_kernel(kernel, client, timeout=0))
        kernel.request_shutdown.assert_awaited_once_with(restart=False)
        self.assertNotIn('reap', kernel.calls)
        self.assertNotIn('cleanup-resources', kernel.calls)
        send.assert_not_called()
        group.assert_not_called()


class NotebookRunnerPersistenceTests(unittest.TestCase):
    def test_evidence_save_failure_preserves_interrupted_exit_and_public_code(self):
        for failed_file in ('executed.ipynb', 'result.json'):
            with self.subTest(failed_file=failed_file), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                template = root / 'template.ipynb'
                template.write_text(nbformat.writes(nbformat.v4.new_notebook(
                    cells=[nbformat.v4.new_code_cell('pass')])))
                cancel = R.NotebookInterrupted(signal.SIGINT)
                original_write = R.write_json

                def build(args, **kwargs):
                    Path(args[3]).write_bytes(b'not-an-executed-binary')
                    return SimpleNamespace(returncode=0)

                async def execute(directory, binary, notebook):
                    return dict(result='interrupted', error=cancel,
                        reader_cleanup_confirmed=True, kernel_cleanup_confirmed=True, kernel_pid=None)

                def write(path, value):
                    if failed_file == path.name:
                        raise OSError('PRIVATE_SECONDARY_SAVE_ERROR')
                    return original_write(path, value)

                notebook_write = R.nbformat.write
                if failed_file == 'executed.ipynb':
                    def notebook_write(*args, **kwargs):
                        raise OSError('PRIVATE_SECONDARY_SAVE_ERROR')

                output = io.StringIO()
                with patch.object(R, 'ROOT', root), patch.object(R, 'STATE', root / 'runs'), \
                     patch.object(R, 'TEMPLATE', template), patch.object(R.subprocess, 'run', side_effect=build), \
                     patch.object(R, 'execute', side_effect=execute) as observed, \
                     patch.object(R, 'write_json', side_effect=write), \
                     patch.object(R.nbformat, 'write', side_effect=notebook_write), \
                     patch.object(R.os, 'umask'), redirect_stdout(output):
                    code = R.main([])
                observed.assert_awaited_once()
                self.assertEqual(code, 130)
                self.assertNotIn('PRIVATE', output.getvalue())
                value = json.loads(output.getvalue())
                self.assertEqual((value['result'], value['error_code'], value['signal']),
                                 ('unknown', 'INTERRUPTED', signal.SIGINT))
                self.assertTrue(value['reader_cleanup_confirmed'])
                self.assertTrue(value['kernel_cleanup_confirmed'])


class NotebookRunnerBenignKernelTests(unittest.IsolatedAsyncioTestCase):
    async def run_benign(self, interrupted):
        parent = R.ROOT / 'tmp/local-experiment/notebook-test-kernels'
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='benign-', dir=parent) as temporary:
            directory = Path(temporary).resolve()
            manager_instances, calls = [], []
            actual_manager = R.AsyncKernelManager

            class TrackedManager(actual_manager):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    manager_instances.append(self)

                async def interrupt_kernel(self):
                    calls.append('interrupt')
                    await super().interrupt_kernel()

                async def request_shutdown(self, **kwargs):
                    calls.append(('shutdown', (directory / 'cell-cleaned').exists()))
                    await super().request_shutdown(**kwargs)

                async def shutdown_kernel(self, **kwargs):
                    raise AssertionError('automatic forced shutdown must not run')

            original_execute = R.execute_owned
            cancel = R.NotebookInterrupted(signal.SIGINT)

            async def bounded_execute(client, manager, run_directory, *, stop):
                watcher = None
                if interrupted:
                    async def request():
                        deadline = time.monotonic() + 5
                        while not (directory / 'cell-started').exists():
                            if time.monotonic() >= deadline:
                                raise AssertionError('benign cell never started')
                            await asyncio.sleep(0.01)
                        stop['reason'] = cancel
                    watcher = asyncio.create_task(request())
                try:
                    return await original_execute(client, manager, run_directory, stop=stop,
                        timeout=8, reader_grace=3, kernel_grace=5)
                finally:
                    if watcher is not None:
                        await watcher

            source = """from tools.local_experiment.notebook.client import _directory
assert _directory().stat().st_mode & 0o7777 == 0o700
print(2 + 2)
"""
            if interrupted:
                source = """import os, time
from pathlib import Path
p = Path(os.environ['SHIROKUMA_NOTEBOOK_RUN_DIR'])
(p / 'cell-started').write_text('started')
try:
    time.sleep(5)
except KeyboardInterrupt:
    time.sleep(0.2)
    (p / 'cell-cleaned').write_text('cleaned')
    raise
"""
            notebook = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(source)])
            try:
                with patch.object(R, 'AsyncKernelManager', TrackedManager), \
                     patch.object(R, 'execute_owned', side_effect=bounded_execute):
                    result = await R.execute(directory, directory / 'unused-shirokuma', notebook)
                self.assertTrue(result['kernel_cleanup_confirmed'])
                self.assertTrue(result['reader_cleanup_confirmed'])
                self.assertEqual(result['result'], 'interrupted' if interrupted else 'passed')
                self.assertEqual(calls.count('interrupt'), 1 if interrupted else 0)
                self.assertEqual(len(manager_instances), 1)
                manager = manager_instances[0]
                self.assertFalse(await manager.is_alive())
                self.assertFalse(manager.provisioner.has_process)
                self.assertFalse((directory / 'jupyter-runtime/kernel-connection.json').exists())
                self.assertEqual(directory.stat().st_mode & 0o7777, 0o700)
                self.assertFalse((directory / 'reader-intent.json').exists())
                self.assertFalse((directory / 'unused-shirokuma').exists())
                if interrupted:
                    self.assertIs(result['error'], cancel)
                    self.assertIn(('shutdown', True), calls)
                    self.assertEqual((directory / 'cell-cleaned').read_text(), 'cleaned')
                else:
                    self.assertIsNone(result['error'])
                    self.assertEqual(notebook.cells[0].outputs[0]['text'].strip(), '4')
            finally:
                # Test cells cannot start a Reader. A failed assertion still
                # releases their owned kernel using request-only shutdown.
                for manager in manager_instances:
                    if manager.provisioner is not None and await manager.is_alive():
                        await manager.interrupt_kernel()
                        cleaned = await R.shutdown_kernel(manager, None, timeout=10)
                        self.assertTrue(cleaned, 'benign kernel did not shut down; no kill attempted')

    async def test_benign_kernel_success_reaps_and_removes_connection(self):
        await self.run_benign(False)

    async def test_benign_kernel_interrupt_waits_for_cell_cleanup_then_reaps(self):
        await self.run_benign(True)


if __name__ == '__main__':
    unittest.main()
