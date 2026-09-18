"""Parameterized Notebook transport and ownership; all children/kernels are fake."""
import asyncio
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tools.local_experiment.notebook import client as N, runner as R, selection as S
from tests.test_local_notebook_client import Child, encoded, reader_frames
from tests.test_local_notebook_runner import FakeKernel, FakeNotebook
from tests.test_query_read_contract import identity, schema, result

REQUEST = {'command': 'select', 'table': 'smoke.fixture_v1', 'columns': ['name'], 'limit': 1}


def preview_frames(request=REQUEST):
    raw = result(request, rows=[['Shirokuma'], ['Polaris']])
    public = N.C.project_rows(raw, request, schema(), identity())
    frames = reader_frames()
    frames[-1]['observation'] = public
    return frames


def write_request(directory, request=REQUEST):
    path = directory / 'request.json'
    path.write_bytes(S.canonical_bytes(request)); path.chmod(0o600)
    return path


class ParameterizedClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.binary = self.directory / 'shirokuma'; self.binary.write_bytes(b'fake')
        self.path = write_request(self.directory)
        environment = {'SHIROKUMA_NOTEBOOK_RUN_DIR': str(self.directory),
                       'SHIROKUMA_NOTEBOOK_BINARY': str(self.binary),
                       'SHIROKUMA_NOTEBOOK_REQUEST_PATH': str(self.path),
                       'SHIROKUMA_NOTEBOOK_REQUEST_SHA256': S.digest(REQUEST)}
        self.env = patch.dict(os.environ, environment); self.env.start(); self.addCleanup(self.env.stop)

    def spawn(self, child, *, frames=None):
        def call(arguments, **kwargs):
            self.assertEqual(arguments, S.arguments(self.binary, N.ROOT, REQUEST))
            self.assertTrue(kwargs['start_new_session'])
            intent = json.loads((self.directory / 'reader-intent.json').read_bytes())
            self.assertEqual(intent['request'], REQUEST)
            kwargs['stdout'].write(encoded(frames or preview_frames()))
            return child
        return call

    def test_requested_table_uses_verified_canonical_input_and_matching_frames(self):
        with patch.object(N.subprocess, 'Popen', side_effect=self.spawn(Child())) as spawn, \
                patch.object(N, '_process_start', return_value='Thu Sep 17 12:00:00 2026'):
            actual = N.read_requested_table()
        self.assertEqual(actual, preview_frames()[-1]['observation'])
        self.assertEqual(spawn.call_count, 1)
        self.assertTrue(actual['truncated'])
        status = json.loads((self.directory / 'reader-status.json').read_bytes())
        self.assertEqual(status['state'], 'passed')
        self.assertTrue(status['child_exited'] and status['cleanup_verified'])

    def test_table_column_order_and_limit_are_bound_even_with_valid_public_hashes(self):
        for key, value in [('table', 'smoke.other'), ('columns', ['id', 'name']), ('limit', 2)]:
            changed = deepcopy(REQUEST); changed[key] = value
            with self.subTest(key=key), self.assertRaises(N.NotebookError):
                N._reader_frames(encoded(preview_frames()), 0, request=changed)
        N._reader_frames(encoded(reader_frames()), 0)  # Original default request unchanged.

    def test_request_file_or_environment_tampering_never_starts_reader(self):
        for mode in ('hash', 'path', 'bytes', 'mode'):
            with self.subTest(mode=mode):
                self.path.write_bytes(S.canonical_bytes(REQUEST)); self.path.chmod(0o600)
                environment = {'SHIROKUMA_NOTEBOOK_REQUEST_PATH': str(self.path),
                               'SHIROKUMA_NOTEBOOK_REQUEST_SHA256': S.digest(REQUEST)}
                if mode == 'hash': environment['SHIROKUMA_NOTEBOOK_REQUEST_SHA256'] = '0' * 64
                if mode == 'path': environment['SHIROKUMA_NOTEBOOK_REQUEST_PATH'] = str(self.directory / 'other.json')
                if mode == 'bytes': self.path.write_bytes(json.dumps(REQUEST).encode())
                if mode == 'mode': self.path.chmod(0o644)
                with patch.dict(os.environ, environment), patch.object(N, 'read_select') as child:
                    with self.assertRaises(N.NotebookError): N.read_requested_table()
                    child.assert_not_called()

    def test_request_change_after_child_prevents_preview_return(self):
        def child(request):
            self.path.write_bytes(S.canonical_bytes(dict(request, limit=2)))
            return preview_frames()[-1]['observation']
        with patch.object(N, 'read_select', side_effect=child) as call:
            with self.assertRaises(N.NotebookError): N.read_requested_table()
        self.assertEqual(call.call_count, 1)

    def test_custom_cancel_keeps_original_and_cleanup_grace_with_bound_request(self):
        original = KeyboardInterrupt()
        frames = preview_frames()
        frames[-1].update(result='interrupted', observation=None, error_code='INTERRUPTED', cleanup_verified=True)
        child = Child(initial=original)
        with patch.object(N.subprocess, 'Popen', side_effect=self.spawn(child, frames=frames)), \
                patch.object(N, '_process_start', return_value='Thu Sep 17 12:00:00 2026'), \
                patch.object(N.os, 'killpg') as interrupt:
            with self.assertRaises(KeyboardInterrupt) as caught: N.read_requested_table()
        self.assertIs(caught.exception, original)
        interrupt.assert_called_once_with(child.pid, signal.SIGINT)
        self.assertEqual(child.calls, 2)
        saved = json.loads((self.directory / 'reader-status.json').read_bytes())
        self.assertEqual(saved['state'], 'interrupted')
        self.assertTrue(saved['cleanup_verified'])

    def test_read_fixture_remains_the_original_fixed_wrapper(self):
        with patch.object(N, 'read_select', return_value='fixed') as call:
            self.assertEqual(N.read_fixture(), 'fixed')
        call.assert_called_once_with(N.REQUEST)
        self.assertEqual(N.REQUEST, {'command': 'select', 'table': 'smoke.fixture_v1', 'columns': ['id', 'name'], 'limit': 10})


class ParameterizedRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / 'runs'

    def main(self, payload, *, mutate=False, error=None, custom=True):
        observed = []
        def build(arguments, **kwargs):
            Path(arguments[arguments.index('-o') + 1]).write_bytes(b'fake-go-binary')
        async def execute(directory, binary, notebook, **kwargs):
            observed.append((directory, binary, notebook, kwargs))
            if custom:
                self.assertEqual(kwargs, {'request': REQUEST})
                self.assertEqual(S.load_request(directory / 'request.json', S.digest(REQUEST)), REQUEST)
            else:
                self.assertEqual(kwargs, {})
            if mutate:
                (directory / 'request.json').write_bytes(S.canonical_bytes(dict(REQUEST, limit=2)))
            return {'result': 'passed' if error is None else 'interrupted', 'error': error,
                    'reader_cleanup_confirmed': True, 'kernel_cleanup_confirmed': True, 'kernel_pid': 202}
        output = io.StringIO()
        with patch.object(R, 'ROOT', self.root), patch.object(R, 'STATE', self.state), \
                patch.object(R.subprocess, 'run', side_effect=build), patch.object(R, 'execute', side_effect=execute), \
                patch.object(R, 'reader_status', return_value={'evidence_token': 'a' * 32}), redirect_stdout(output):
            code = R.main(['--request-stdin'] if custom else [], stdin=io.BytesIO(payload))
        return code, json.loads(output.getvalue()), observed

    def test_custom_main_selects_registered_template_and_private_input_record(self):
        code, final, observed = self.main(S.canonical_bytes(REQUEST))
        self.assertEqual(code, 0)
        self.assertEqual(final['profile'], 'table-preview-v1')
        self.assertEqual(final['request_sha256'], S.digest(REQUEST))
        directory, _, notebook, _ = observed[0]
        self.assertEqual([c.source for c in notebook.cells if c.cell_type == 'code'], list(S.CODE_CELLS))
        self.assertEqual((directory / 'request.json').stat().st_mode & 0o777, 0o600)
        inputs = json.loads((directory / 'inputs.json').read_bytes())
        self.assertEqual(inputs['request_file'], 'request.json')
        self.assertEqual(inputs['request_sha256'], S.digest(REQUEST))
        self.assertEqual(inputs['template_sha256'], S.TEMPLATE_SHA256)

    def test_invalid_inputs_are_rejected_before_allocation_build_or_kernel(self):
        invalid = [b'{}', b'x' * 4097, S.canonical_bytes(REQUEST).replace(b'"select"', b'"count"'),
                   b'{"command":"select","command":"select","table":"smoke.fixture_v1","columns":["id"],"limit":1}']
        for payload in invalid:
            with self.subTest(payload_length=len(payload)), patch.object(R, 'STATE', self.state), \
                    patch.object(R.subprocess, 'run') as build, patch.object(R, 'execute') as execute, \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(R.main(['--request-stdin'], stdin=io.BytesIO(payload)), 1)
                self.assertEqual(json.loads(output.getvalue())['error_code'], 'NOTEBOOK_REQUEST_INVALID')
                self.assertFalse(self.state.exists())
                build.assert_not_called(); execute.assert_not_called()

    def test_saved_request_change_after_execution_refuses_success(self):
        code, final, _ = self.main(S.canonical_bytes(REQUEST), mutate=True)
        self.assertEqual(code, 1)
        self.assertEqual(final['result'], 'failed')
        self.assertTrue(final['reader_cleanup_confirmed'] and final['kernel_cleanup_confirmed'])

    def test_post_request_failure_never_replaces_prior_interrupt_classification(self):
        original = R.NotebookInterrupted(signal.SIGINT)
        code, final, _ = self.main(S.canonical_bytes(REQUEST), mutate=True, error=original)
        self.assertEqual(code, 130)
        self.assertEqual(final['result'], 'interrupted')
        self.assertEqual(final['error_code'], 'INTERRUPTED')
        self.assertEqual(final['signal'], signal.SIGINT)

    def test_default_input_and_result_shape_remain_unchanged(self):
        code, final, observed = self.main(b'ignored default stdin', custom=False)
        self.assertEqual(code, 0)
        self.assertNotIn('profile', final); self.assertNotIn('request_sha256', final)
        directory = observed[0][0]
        self.assertFalse((directory / 'request.json').exists())
        self.assertEqual(set(json.loads((directory / 'inputs.json').read_bytes())),
                         {'template_sha256', 'binary_sha256', 'python', 'scope'})

    def test_kernel_environment_ignores_parent_request_values_and_pins_custom_input(self):
        for custom in (False, True):
            directory = self.root / str(custom); directory.mkdir(mode=0o700)
            if custom: write_request(directory)
            with patch.dict(os.environ, {'SHIROKUMA_NOTEBOOK_REQUEST_PATH': '/untrusted/file',
                                        'SHIROKUMA_NOTEBOOK_REQUEST_SHA256': 'f' * 64}):
                environment = R.kernel_environment(directory, directory / 'shirokuma', request=REQUEST if custom else None)
            if custom:
                self.assertEqual(environment['SHIROKUMA_NOTEBOOK_REQUEST_PATH'], str(directory / 'request.json'))
                self.assertEqual(environment['SHIROKUMA_NOTEBOOK_REQUEST_SHA256'], S.digest(REQUEST))
            else:
                self.assertNotIn('SHIROKUMA_NOTEBOOK_REQUEST_PATH', environment)
                self.assertNotIn('SHIROKUMA_NOTEBOOK_REQUEST_SHA256', environment)

    def test_custom_pid_owner_requires_same_request_argv_as_client(self):
        status = {'state': 'running', 'pid': 303, 'pgid': 303, 'process_start': 'Thu Sep 17 12:00:00 2026'}
        current = {'pid': 303, 'pgid': 303, 'ppid': 202, 'process_start': status['process_start'],
                   'command': ' '.join(S.arguments(self.root / 'shirokuma', R.ROOT, REQUEST))}
        with patch.object(R, 'reader_status', return_value=status), patch.object(R, 'process_identity', return_value=current):
            self.assertEqual(R.bind_reader(self.root, 202, request=REQUEST), current)
            self.assertIsNone(R.bind_reader(self.root, 202))
            self.assertIsNone(R.bind_reader(self.root, 202, request=dict(REQUEST, limit=2)))


class ParameterizedOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_custom_request_reaches_owner_monitor_and_cancel_waits_for_reader(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            kernel, stop = FakeKernel(), {'reason': None}
            notebook = FakeNotebook(kernel)
            original = R.NotebookInterrupted(signal.SIGINT)
            with patch.object(R, 'bind_reader', return_value=None) as bind, \
                    patch.object(R, 'reader_closed', side_effect=lambda _: kernel.reader_cleaned):
                task = asyncio.create_task(R.execute_owned(notebook, kernel, directory, request=REQUEST,
                                           stop=stop, timeout=1, reader_grace=0.3, kernel_grace=0.1))
                await kernel.started.wait(); stop['reason'] = original
                final = await task
            self.assertIs(final['error'], original)
            self.assertEqual(final['result'], 'interrupted')
            self.assertTrue(final['reader_cleanup_confirmed'] and final['kernel_cleanup_confirmed'])
            self.assertTrue(all(call.kwargs == {'request': REQUEST} for call in bind.call_args_list))
            self.assertLess(kernel.calls.index('reader-cleaned'), kernel.calls.index(('shutdown', True)))

    async def test_execute_rejects_changed_request_before_kernel_allocation(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve(); write_request(directory, dict(REQUEST, limit=2))
            with patch.object(R, 'AsyncKernelManager') as kernel:
                with self.assertRaises(S.SelectionError):
                    await R.execute(directory, directory / 'shirokuma', object(), request=REQUEST)
                kernel.assert_not_called()


if __name__ == '__main__':
    unittest.main()
