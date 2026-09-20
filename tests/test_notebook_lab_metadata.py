"""Model-free Lab metadata with fake owned Reader receipts and no services."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.notebook_lab import query as Q, metadata as M, runtime as R
from tests import test_notebook_assist_runtime as V
from tests.test_notebook_metadata_client import inventory


class LabMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Q.ROOT / 'tmp'); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve(); self.lab = self.base / 'lab'; self.lab.mkdir(mode=0o700)
        self.state = self.lab / 'queries'; self.state.mkdir(mode=0o700)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for owner, key, value in [(Q, 'LAB_STATE', self.lab), (Q, 'STATE', self.state), (R, 'STATE', self.lab)]:
            self.stack.enter_context(patch.object(owner, key, value))
        self.stack.enter_context(patch.object(Q.subprocess, 'Popen', side_effect=AssertionError('real process forbidden')))
        self.stack.enter_context(patch.object(Q.T, 'group_empty', return_value=True))
        self.stack.enter_context(patch.object(M.A, 'environment', side_effect=AssertionError('model environment forbidden')))
        for name in ('read_tables', 'read_describe', 'read_count', 'read_select'):
            self.stack.enter_context(patch.object(Q.N, name, side_effect=AssertionError('real Reader forbidden')))
        self.source = {key: 'a' * 64 for key in ('tools/local_experiment/notebook_lab/metadata.py',
            'tools/local_experiment/notebook_lab/query.py', 'tools/local_experiment/notebook/client.py',
            'tools/local_experiment/query_read/contract.py')}
        self.stack.enter_context(patch.object(M, 'sources', return_value=self.source))
        self.counter = 0; self.reads = []; self.builds = []

    def directory(self, request):
        self.counter += 1
        directory = self.state / f'{self.counter:032x}'; directory.mkdir(mode=0o700)
        Q.N._write(directory / 'input.json', dict(schema_version=3, mode='metadata', input=request))
        return directory

    def build(self, binary, log):
        self.builds.append(binary)
        binary.write_bytes(b'never executed'); binary.chmod(0o700)

    def reader(self, directory, request, public, *, result='passed', cleanup=True):
        def read(value):
            self.reads.append((request['command'], value))
            output = directory / 'metadata/reader'
            self.assertEqual(Path(os.environ['SHIROKUMA_NOTEBOOK_RUN_DIR']), output)
            V.saved_reader(output, request, public, interrupted=result == 'interrupted', cleanup=cleanup)
            if result == 'failed':
                status = Q._json(output / 'reader-status.json')
                status.update(state='failed', exit_code=1, error_code='NOTEBOOK_READER_FAILED')
                Q.N._write(output / 'reader-status.json', status, replace=True)
                frames = [json.loads(line) for line in (output / 'reader.stdout.jsonl').read_bytes().splitlines()]
                frames[1].update(result='failed', observation=None, error_code='READ_FAILED')
                (output / 'reader.stdout.jsonl').write_bytes(V.encoded(frames))
                raise Q.N.NotebookError('NOTEBOOK_READER_FAILED')
            if result == 'interrupted':
                raise KeyboardInterrupt()
            return public
        return read

    def completed(self, request=None, public=None, *, result='passed', cleanup=True):
        request = request or dict(command='tables', schema='smoke')
        if public is None:
            public = inventory(request['schema']) if request['command'] == 'tables' else V.identity()
        directory = self.directory(request); stream = io.StringIO()
        code = M.worker(directory, build=self.build,
            reader=self.reader(directory, request, public, result=result, cleanup=cleanup), stdout=stream)
        Q.N._write(directory / 'worker.stdout.json', json.loads(stream.getvalue()))
        return directory, code

    def passed_status(self, directory, public):
        Q.N._write(directory / 'status.json', dict(schema_version=1, state='passed', pid=4567, pgid=4567,
            process_start='fake start', evidence_token=directory.name, exit_code=0, child_exited=True,
            cleanup_verified=True, error_code=None))
        Q.N._write(directory / 'result.json', public)

    def test_model_free_tables_empty_and_partial_are_distinct_successes(self):
        for count, complete in [(0, True), (1, True), (100, False)]:
            with self.subTest(count=count):
                directory, code = self.completed(public=inventory(count=count, complete=complete))
                token, public = Q.terminal_receipt(directory, code, verify_metadata_source=True)
                self.assertEqual(token, directory.name); self.assertEqual(code, 0)
                self.assertEqual(public['schema_version'], 3); self.assertEqual(public['model_calls'], 0)
                self.assertEqual(len(public['metadata']['tables']), count)
                self.assertEqual(public['metadata']['complete'], complete)
                self.assertTrue(public['observed_at'].endswith('Z'))
                html = Q.render_metadata(public)
                self.assertEqual('テーブルはありません' in html, count == 0)
                self.assertEqual('一部・上限100件' in html, not complete)
        self.assertEqual(len(self.reads), 3); M.A.environment.assert_not_called()

    def test_describe_preserves_names_types_nullability_and_identity(self):
        directory, code = self.completed(dict(command='describe', table='smoke.fixture_v1'))
        _, public = Q.terminal_receipt(directory, code)
        self.assertEqual(public['metadata'], V.identity())
        self.assertEqual(public['command'], 'describe')
        rendered = Q.render_metadata(public)
        for expected in ['fixture_v1', 'bigint', 'decimal(38,6)', 'NULL 可', '不可', public['observed_at']]:
            self.assertIn(expected, rendered)
        self.assertEqual(self.reads, [('describe', 'smoke.fixture_v1')])

    def test_failed_metadata_never_becomes_empty_inventory_and_unknown_stays_blocked(self):
        directory, code = self.completed(result='failed')
        _, public = Q.terminal_receipt(directory, code)
        self.assertEqual(code, 1); self.assertEqual(public['result'], 'failed')
        self.assertIsNone(public['metadata']); self.assertIsNone(public['observed_at'])
        self.assertTrue(public['cleanup_verified'])
        with self.assertRaises(Q.N.NotebookError): Q.render_metadata(public)
        directory.rename(self.base / 'failed-closed')
        directory, code = self.completed(result='failed', cleanup=False)
        _, public = Q.terminal_receipt(directory, code)
        self.assertEqual(public['result'], 'unknown'); self.assertFalse(public['cleanup_verified'])
        Q.N._write(directory / 'status.json', dict(schema_version=1, state='unknown', pid=4567, pgid=4567,
            process_start='fake start', evidence_token=directory.name, exit_code=1, child_exited=True,
            cleanup_verified=False, error_code='LAB_QUERY_CLEANUP_UNKNOWN'))
        self.assertFalse(Q.check_idle())
        for call in [lambda: Q.tables('smoke'), lambda: Q.describe('smoke.fixture_v1'),
                     lambda: Q.preview('smoke.fixture_v1', 'idを1件'), lambda: Q.count('smoke.fixture_v1', '件数')]:
            with self.assertRaises(Q.N.NotebookError): call()
        Q.subprocess.Popen.assert_not_called()

    def test_saved_metadata_ignores_current_sources_and_reused_pid_without_read(self):
        directory, code = self.completed(); _, public = Q.terminal_receipt(directory, code)
        self.passed_status(directory, public)
        calls = list(self.reads)
        with patch.object(M, 'sources', side_effect=AssertionError('current source not historical evidence')), \
                patch.object(Q.T, 'group_empty', side_effect=AssertionError('old PID reused')):
            recovered = Q.recover_metadata(directory.name)
            self.assertTrue(Q.query_closed(directory)); self.assertTrue(Q.check_idle())
        self.assertEqual(recovered, dict(public, saved_result=True))
        self.assertEqual(self.reads, calls)
        self.assertIn('保存結果', Q.render_metadata(recovered))
        self.assertEqual(recovered['observed_at'], public['observed_at'])

    def test_parent_uses_same_owned_worker_lock_and_does_not_mutate_environment(self):
        request = dict(command='tables', schema='local_experiment')
        child = Mock(pid=4567, returncode=0); child.poll.return_value = 0
        def popen(argv, **kwargs):
            directory = Path(argv[-1]); stream = io.StringIO()
            self.assertEqual(argv, Q.arguments(directory)); self.assertTrue(kwargs['start_new_session'])
            code = M.worker(directory, build=self.build,
                reader=self.reader(directory, request, inventory('local_experiment')), stdout=stream)
            self.assertEqual(code, 0)
            kwargs['stdout'].write(stream.getvalue().encode()); kwargs['stdout'].flush()
            return child
        before = dict(os.environ)
        with patch.object(Q.subprocess, 'Popen', side_effect=popen) as launch, \
                patch.object(Q.N, '_process_start', return_value='fake start'):
            public = Q.tables('local_experiment')
        self.assertEqual(launch.call_count, 1); self.assertEqual(os.environ, before)
        self.assertEqual(public['metadata']['schema'], 'local_experiment')
        self.assertTrue(Q.query_closed(self.state / public['attempt_token']))
        with Q._lock(), self.assertRaisesRegex(Q.N.NotebookError, 'LAB_QUERY_BUSY'):
            Q.describe('smoke.fixture_v1')

    def test_invalid_request_modes_never_allocate_or_invoke_worker(self):
        for call in [lambda: Q.tables('system'), lambda: Q.tables(True), lambda: Q.describe('smoke.x;DROP'),
                     lambda: Q.describe('iceberg.smoke.fixture_v1')]:
            with self.assertRaises(Q.N.NotebookError): call()
        self.assertEqual(list(self.state.iterdir()), [])
        directory = self.directory(dict(command='count', table='smoke.fixture_v1', snapshot_id=None, where=None))
        with self.assertRaises(Q.N.NotebookError): Q._input(directory)
        Q.subprocess.Popen.assert_not_called()

    def test_worker_handshake_routes_metadata_without_assist(self):
        directory = self.directory(dict(command='describe', table='smoke.fixture_v1'))
        value = Q._input(directory)
        Q.N._write(directory / 'intent.json', dict(schema_version=1, profile=Q.PROFILE, argv=Q.arguments(directory),
            input_sha256=hashlib.sha256(Q.N._canonical(value)).hexdigest()))
        Q.N._write(directory / 'status.json', dict(schema_version=1, state='running', pid=4567, pgid=4567,
            process_start='fake start', evidence_token=None, exit_code=None, child_exited=False,
            cleanup_verified=False, error_code=None))
        Q.N._write(directory / 'launch.json', dict(pid=4567, ppid=4566, pgid=4567, process_start='fake start'))
        assist = Mock(side_effect=AssertionError('Assist forbidden'))
        with patch.object(Q.os, 'getpid', return_value=4567), patch.object(Q.os, 'getppid', return_value=4566), \
                patch.object(Q.os, 'getpgrp', return_value=4567), patch.object(Q.N, '_process_start', return_value='fake start'), \
                patch.object(M, 'worker', return_value=0) as worker:
            self.assertEqual(Q.worker(directory, assist=assist), 0)
        worker.assert_called_once_with(directory); assist.assert_not_called()

    def test_saved_receipt_refuses_source_identity_observation_and_timestamp_changes(self):
        directory, code = self.completed(); path = directory / 'metadata/owner-result.json'
        original = Q._json(path)
        for edit in [dict(original, observed_at='not UTC'), dict(original, request_sha256='f' * 64),
                     dict(original, source_hashes={}), dict(original, metadata=inventory(count=0)),
                     dict(original, model_calls=1)]:
            Q.N._write(path, edit, replace=True)
            with self.assertRaises(Q.N.NotebookError): Q.terminal_receipt(directory, code)
        Q.N._write(path, original, replace=True)
        with patch.object(M, 'sources', return_value={'changed': 'f' * 64}):
            with self.assertRaises(Q.N.NotebookError): Q.terminal_receipt(directory, code, verify_metadata_source=True)
            self.assertEqual(Q.terminal_receipt(directory, code)[1]['result'], 'passed')

    def test_changed_source_after_read_is_closed_failure_not_success(self):
        request = dict(command='tables', schema='smoke'); directory = self.directory(request)
        read = self.reader(directory, request, inventory())
        def changed(value):
            public = read(value); self.source = dict(self.source, changed='f' * 64); return public
        stream = io.StringIO()
        code = M.worker(directory, build=self.build, reader=changed, source_factory=lambda: deepcopy(self.source), stdout=stream)
        Q.N._write(directory / 'worker.stdout.json', json.loads(stream.getvalue()))
        _, public = Q.terminal_receipt(directory, code)
        self.assertEqual(public['result'], 'failed'); self.assertTrue(public['cleanup_verified'])
        self.assertIsNone(public['metadata'])

    def test_first_interrupt_survives_secondary_owner_save_failure(self):
        request = dict(command='tables', schema='smoke'); directory = self.directory(request)
        reader = self.reader(directory, request, inventory(), result='interrupted')
        write = Q.N._write
        def fail(path, value, **kwargs):
            if path.name == 'owner-result.json': raise OSError('secondary write failure')
            return write(path, value, **kwargs)
        with patch.object(Q.N, '_write', side_effect=fail), self.assertRaises(KeyboardInterrupt):
            M.worker(directory, build=self.build, reader=reader, stdout=io.StringIO())

    def test_build_failure_is_model_and_reader_free(self):
        directory = self.directory(dict(command='tables', schema='smoke')); stream = io.StringIO()
        reader = Mock(side_effect=AssertionError('Reader must not start'))
        code = M.worker(directory, build=Mock(side_effect=OSError('compiler failed')), reader=reader, stdout=stream)
        Q.N._write(directory / 'worker.stdout.json', json.loads(stream.getvalue()))
        _, public = Q.terminal_receipt(directory, code)
        self.assertEqual(public['result'], 'failed'); self.assertTrue(public['cleanup_verified'])
        reader.assert_not_called(); M.A.environment.assert_not_called()

    def test_renderer_escapes_provenance_and_refuses_failed_or_invalid_inventory(self):
        directory, code = self.completed(); _, public = Q.terminal_receipt(directory, code)
        public.update(attempt_token='<script>', evidence='<img>')
        rendered = Q.render_metadata(public)
        self.assertIn('&lt;script&gt;', rendered); self.assertNotIn('<img>', rendered)
        for edit in [dict(public, result='failed'), dict(public, cleanup_verified=False),
                     dict(public, metadata=inventory(count=1, complete=False))]:
            with self.assertRaises(Q.N.NotebookError): Q.render_metadata(edit)

    def test_template_default_run_all_lists_only_and_explicit_selection_describes(self):
        cells = [c for c in json.loads(R.TABLE_TEMPLATE.read_bytes())['cells'] if c['cell_type'] == 'code']
        self.assertEqual(len(cells), 5)
        for cell in cells:
            self.assertEqual(cell['outputs'], []); self.assertIsNone(cell['execution_count'])
            compile(''.join(cell['source']), '<table-template>', 'exec')
        listed = dict(inventory(), tables=['smoke.fixture_v1'])
        scope = dict(tables=Mock(side_effect=lambda schema: {'metadata': listed if schema == 'smoke' else inventory(schema, count=0)}),
            describe=Mock(return_value={'metadata': V.identity()}), preview=Mock(), count=Mock(), recover_metadata=Mock(),
            NotebookError=Q.N.NotebookError, display=Mock(), HTML=lambda x: x, render_metadata=Mock(return_value='metadata'),
            render_preview=Mock(), render_count=Mock())
        for cell in cells[1:]: exec(''.join(cell['source']), scope)
        self.assertEqual(scope['tables'].call_count, 2)
        for name in ('describe', 'preview', 'count', 'recover_metadata'): scope[name].assert_not_called()
        choice = ''.join(cells[2]['source']).replace('selected_table = ""', 'selected_table = "smoke.fixture_v1"')
        exec(choice, scope)
        scope['describe'].assert_called_once_with('smoke.fixture_v1')
        scope['describe'].side_effect = Q.N.NotebookError('LAB_QUERY_FAILED')
        with patch('builtins.print'): exec(choice, scope)
        self.assertIsNone(scope['table_details']); self.assertIsNone(scope['preview_result']); self.assertIsNone(scope['count_result'])
        explicit = ''.join(cells[3]['source']).replace('run_preview = False', 'run_preview = True').replace('run_count = False', 'run_count = True')
        with patch('builtins.print'): exec(explicit, scope)
        scope['preview'].assert_not_called(); scope['count'].assert_not_called()
        self.assertEqual(scope['tables'].call_count, 2)
        scope['table_details'] = {'metadata': V.identity()}
        exec(explicit, scope)
        scope['preview'].assert_called_once_with('smoke.fixture_v1', 'nameとidを1件表示')
        scope['count'].assert_called_once_with('smoke.fixture_v1', '全行の件数を数えてください')
        for name in ('preview', 'count', 'tables', 'describe'): scope[name].reset_mock()
        recovered = ''.join(cells[4]['source']).replace('metadata_attempt = ""', 'metadata_attempt = "' + 'a' * 32 + '"')
        exec(recovered, scope)
        scope['recover_metadata'].assert_called_once_with('a' * 32)
        for name in ('preview', 'count', 'tables', 'describe'): scope[name].assert_not_called()


if __name__ == '__main__':
    unittest.main()
