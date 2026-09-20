"""Lab Doctor receipts, shared ownership and output-free template; no services."""
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

from tools.local_experiment.notebook_lab import query as Q, diagnose as D, runtime as R
from test_query_diagnose_report_contract import documents
from test_query_diagnose_filter import source as filtered_source

READ_TOKEN = 'a' * 32
TEMPLATE = Q.ROOT / 'examples/local_query_diagnose_workbench.ipynb'


class LabDiagnoseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Q.ROOT / 'tmp'); self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve(); self.lab = self.base / 'lab'; self.lab.mkdir(mode=0o700)
        self.state = self.lab / 'queries'; self.state.mkdir(mode=0o700)
        self.doctors = self.base / 'doctors'; self.doctors.mkdir(mode=0o700)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for owner, key, value in [(Q, 'LAB_STATE', self.lab), (Q, 'STATE', self.state),
                                   (R, 'STATE', self.lab), (D.R, 'STATE', self.doctors)]:
            self.stack.enter_context(patch.object(owner, key, value))
        self.stack.enter_context(patch.object(Q.subprocess, 'Popen', side_effect=AssertionError('real process forbidden')))
        self.stack.enter_context(patch.object(Q.T, 'group_empty', return_value=True))
        self.stack.enter_context(patch.object(D.A, 'environment', side_effect=AssertionError('model environment forbidden')))
        for name in ('read_diagnose', 'read_tables', 'read_describe', 'read_count', 'read_select'):
            self.stack.enter_context(patch.object(Q.N, name, side_effect=AssertionError('real Reader forbidden')))
        self.source = {key: 'a' * 64 for key in ('tools/local_experiment/notebook_lab/diagnose.py',
            'tools/local_experiment/notebook_lab/query.py', 'tools/local_experiment/notebook/client.py',
            'tools/local_experiment/query_diagnose/contract.py', 'tools/local_experiment/query_diagnose_report/contract.py')}
        self.stack.enter_context(patch.object(D, 'sources', side_effect=lambda: deepcopy(self.source)))
        self.counter = 0; self.reads = []; self.builds = []

    def directory(self, request=None):
        self.counter += 1
        directory = self.state / f'{self.counter:032x}'; directory.mkdir(mode=0o700)
        Q.N._write(directory / 'input.json', dict(schema_version=5, mode='diagnose',
                                                input=request or dict(read_token=READ_TOKEN)))
        return directory

    def build(self, binary, log):
        self.builds.append(binary); binary.write_bytes(b'never executed'); binary.chmod(0o700)

    def reader(self, directory, *, profile=1, result='passed', cleanup=True, first=None):
        def read(read_token):
            self.reads.append(read_token)
            output = directory / 'diagnose/reader'
            self.assertEqual(Path(os.environ['SHIROKUMA_NOTEBOOK_RUN_DIR']), output)
            token = hashlib.sha256(('doctor:' + directory.name).encode()).hexdigest()[:32]
            if profile == 3:
                with patch('test_query_diagnose_report_contract.snapshot_source', return_value=filtered_source()):
                    summary, spans = documents(2, identity=token)
                summary['source_hashes'] = {p:'d' * 64 for p in D.REPORT.FILTERED_29}
            else:
                summary, spans = documents(profile, identity=token)
            doctor = self.doctors / token; doctor.mkdir(mode=0o700)
            Q.N._write(doctor / 'summary.json', summary)
            fd = os.open(doctor / 'spans.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream: stream.write(b''.join(Q.N._canonical(s) for s in spans))
            public = D.C.public_summary(summary['source'], summary['model_evidence'], summary['diagnosis'])
            request = Q.N._diagnose_request(read_token)
            Q.N._write(output / 'reader-intent.json', dict(schema_version=1, command='query diagnose', request=request))
            rc = {'passed':0, 'failed':1, 'interrupted':130}[result]
            Q.N._write(output / 'reader-status.json', dict(schema_version=1, state=result, pid=5678, pgid=5678,
                process_start='fake start', evidence_token=token, exit_code=rc, child_exited=True,
                cleanup_verified=cleanup, error_code={'passed':None, 'failed':'NOTEBOOK_READER_FAILED',
                                                      'interrupted':'NOTEBOOK_INTERRUPTED'}[result]))
            frames = [dict(schema_version=1, event='allocated', command='query diagnose', evidence_token=token),
                dict(schema_version=1, event='result', command='query diagnose', evidence_token=token,
                    result=result, observation=public if result == 'passed' else None,
                    error_code=None if result == 'passed' else 'INTERRUPTED' if result == 'interrupted' else 'MODEL_FAILED',
                    cleanup_verified=cleanup, evidence=f'tmp/local-experiment/query-diagnose/{token}/summary.json')]
            fd = os.open(output / 'reader.stdout.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream: stream.write(b''.join(Q.N._canonical(f) for f in frames))
            if first is not None: raise first
            if result == 'interrupted': raise KeyboardInterrupt()
            if result == 'failed': raise Q.N.NotebookError('NOTEBOOK_READER_FAILED')
            return public
        return read

    def completed(self, *, profile=1, result='passed', cleanup=True):
        directory = self.directory(); stream = io.StringIO()
        code = D.worker(directory, build=self.build,
            reader=self.reader(directory, profile=profile, result=result, cleanup=cleanup), stdout=stream)
        Q.N._write(directory / 'worker.stdout.json', json.loads(stream.getvalue()))
        return directory, code

    def status(self, directory, public):
        Q.N._write(directory / 'status.json', dict(schema_version=1, state=public['result'], pid=4567, pgid=4567,
            process_start='fake start', evidence_token=directory.name, exit_code=0 if public['result'] == 'passed' else 1,
            child_exited=True, cleanup_verified=public['cleanup_verified'],
            error_code=None if public['result'] == 'passed' else 'LAB_QUERY_CLEANUP_UNKNOWN'))
        Q.N._write(directory / 'result.json', public)

    def test_three_saved_select_profiles_reconstruct_diagnosis_without_select_replay(self):
        for profile in (1, 2, 3):
            with self.subTest(profile=profile):
                directory, code = self.completed(profile=profile)
                token, public = Q.terminal_receipt(directory, code, verify_diagnose_source=True)
                self.assertEqual(code, 0); self.assertEqual(token, directory.name)
                self.assertEqual(public['schema_version'], 5); self.assertEqual(public['model_calls'], 1)
                self.assertEqual(public['diagnosis']['read_token'], READ_TOKEN)
                self.assertFalse(public['diagnosis']['original_select_reexecuted'])
                self.assertEqual(len(public['diagnosis']), 10); self.assertTrue(public['cleanup_verified'])
                summary = Q._json(self.doctors / public['diagnosis_token'] / 'summary.json', D.REPORT.MAX_SUMMARY_BYTES)
                self.assertEqual(summary['source']['schema_version'], profile)
                self.assertEqual(len(summary['observation']['queries']['worker']), 5)
                self.assertIn('根本原因は未確定', Q.render_diagnose(public))
                self.assertTrue(public['observed_at'].endswith('Z'))
        self.assertEqual(self.reads, [READ_TOKEN] * 3)
        Q.N.read_select.assert_not_called(); D.A.environment.assert_not_called()

    def test_model_failure_or_interrupt_does_not_claim_zero_calls_or_keep_public(self):
        for result, expected_code in [('failed', 1), ('interrupted', 130)]:
            directory, code = self.completed(result=result)
            _, public = Q.terminal_receipt(directory, code)
            self.assertEqual(code, expected_code); self.assertEqual(public['result'], result)
            self.assertIsNone(public['model_calls']); self.assertIsNone(public['diagnosis'])
            self.assertIsNone(public['observed_at']); self.assertTrue(public['cleanup_verified'])
            with self.assertRaises(Q.N.NotebookError): Q.render_diagnose(public)

    def test_unknown_cleanup_blocks_other_shared_lab_operations_and_recovery(self):
        directory, code = self.completed(result='failed', cleanup=False)
        _, public = Q.terminal_receipt(directory, code); self.status(directory, public)
        self.assertEqual(public['result'], 'unknown'); self.assertFalse(Q.check_idle())
        for call in [lambda: Q.diagnose(READ_TOKEN), lambda: Q.preview('smoke.fixture_v1', 'idを1件'),
                     lambda: Q.count('smoke.fixture_v1', '件数'), lambda: Q.tables('smoke'),
                     lambda: Q.snapshots('smoke.fixture_v1'), lambda: Q.recover_diagnose(directory.name)]:
            with self.assertRaises(Q.N.NotebookError): call()
        Q.subprocess.Popen.assert_not_called()

    def test_saved_recovery_uses_historical_seal_not_current_source_pid_or_reader(self):
        directory, code = self.completed(); _, public = Q.terminal_receipt(directory, code); self.status(directory, public)
        before = list(self.reads)
        with patch.object(D, 'sources', side_effect=AssertionError('current source is not historical evidence')), \
                patch.object(Q.T, 'group_empty', side_effect=AssertionError('past PID is reused')):
            recovered = Q.recover_diagnose(directory.name)
            self.assertTrue(Q.query_closed(directory)); self.assertTrue(Q.check_idle())
        self.assertEqual(recovered, dict(public, saved_result=True)); self.assertEqual(self.reads, before)
        self.assertIn('保存結果', Q.render_diagnose(recovered)); Q.N.read_diagnose.assert_not_called()

    def test_summary_spans_source_profile_and_public_tampering_are_rejected(self):
        directory, code = self.completed(); _, public = Q.terminal_receipt(directory, code)
        doctor = self.doctors / public['diagnosis_token']; path = doctor / 'summary.json'
        original = Q._json(path, D.REPORT.MAX_SUMMARY_BYTES)
        changes = [lambda v:v.update(read_token='c' * 32), lambda v:v.update(cleanup_verified=False),
            lambda v:v['source'].update(schema_version=4), lambda v:v.update(model_calls=0),
            lambda v:v['diagnosis'].update(root_cause='known')]
        for change in changes:
            altered = deepcopy(original); change(altered); Q.N._write(path, altered, replace=True)
            with self.assertRaises(Q.N.NotebookError): Q.terminal_receipt(directory, code)
        Q.N._write(path, original, replace=True)
        spans = doctor / 'spans.jsonl'; spans.write_bytes(spans.read_bytes() + b'\n')
        with self.assertRaises(Q.N.NotebookError): Q.terminal_receipt(directory, code)

    def test_terminal_requires_seal_timestamp_intent_and_current_source_only_when_fresh(self):
        directory, code = self.completed(); path = directory / 'diagnose/owner-result.json'; original = Q._json(path)
        for changed in [dict(original, diagnosis_seal=None), dict(original, diagnosis_seal={}),
                        dict(original, observed_at='yesterday'), dict(original, source_hashes={}),
                        dict(original, request_sha256='f' * 64), dict(original, model_calls=0)]:
            Q.N._write(path, changed, replace=True)
            with self.assertRaises(Q.N.NotebookError): Q.terminal_receipt(directory, code)
        Q.N._write(path, original, replace=True)
        with patch.object(D, 'sources', return_value={'changed':'f' * 64}):
            with self.assertRaises(Q.N.NotebookError): Q.terminal_receipt(directory, code, verify_diagnose_source=True)
            self.assertEqual(Q.terminal_receipt(directory, code)[1]['result'], 'passed')

    def test_changed_source_after_read_clears_success_but_preserves_known_model_call(self):
        directory = self.directory(); read = self.reader(directory)
        def changed(value):
            public = read(value); self.source = dict(self.source, changed='f' * 64); return public
        stream = io.StringIO(); code = D.worker(directory, build=self.build, reader=changed, stdout=stream)
        Q.N._write(directory / 'worker.stdout.json', json.loads(stream.getvalue()))
        _, public = Q.terminal_receipt(directory, code)
        self.assertEqual(public['result'], 'failed'); self.assertTrue(public['cleanup_verified'])
        self.assertEqual(public['model_calls'], 1); self.assertIsNone(public['diagnosis']); self.assertIsNone(public['observed_at'])

    def test_original_cancellation_survives_secondary_owner_save_failure(self):
        directory = self.directory(); first = KeyboardInterrupt(); reader = self.reader(directory, result='interrupted', first=first)
        write = Q.N._write
        def fail(path, value, **kwargs):
            if path.name == 'owner-result.json': raise OSError('secondary write failure')
            return write(path, value, **kwargs)
        with patch.object(Q.N, '_write', side_effect=fail), self.assertRaises(KeyboardInterrupt) as caught:
            D.worker(directory, build=self.build, reader=reader, stdout=io.StringIO())
        self.assertIs(caught.exception, first)

    def test_build_failure_is_known_zero_calls_and_reader_free(self):
        directory = self.directory(); stream = io.StringIO(); reader = Mock(side_effect=AssertionError('Reader forbidden'))
        code = D.worker(directory, build=Mock(side_effect=OSError('compiler failed')), reader=reader, stdout=stream)
        Q.N._write(directory / 'worker.stdout.json', json.loads(stream.getvalue()))
        _, public = Q.terminal_receipt(directory, code)
        self.assertEqual(public['result'], 'failed'); self.assertEqual(public['model_calls'], 0)
        self.assertTrue(public['cleanup_verified']); reader.assert_not_called()

    def test_parent_uses_shared_owner_lock_and_preserves_environment(self):
        child = Mock(pid=4567, returncode=0); child.poll.return_value = 0
        def popen(argv, **kwargs):
            directory = Path(argv[-1]); stream = io.StringIO()
            self.assertEqual(argv, Q.arguments(directory)); self.assertTrue(kwargs['start_new_session'])
            code = D.worker(directory, build=self.build, reader=self.reader(directory), stdout=stream)
            self.assertEqual(code, 0); kwargs['stdout'].write(stream.getvalue().encode()); kwargs['stdout'].flush()
            return child
        before = dict(os.environ)
        with patch.object(Q.subprocess, 'Popen', side_effect=popen) as launch, \
                patch.object(Q.N, '_process_start', return_value='fake start'):
            public = Q.diagnose(READ_TOKEN)
        self.assertEqual(launch.call_count, 1); self.assertEqual(dict(os.environ), before)
        self.assertTrue(Q.query_closed(self.state / public['attempt_token']))
        with Q._lock(), self.assertRaisesRegex(Q.N.NotebookError, 'LAB_QUERY_BUSY'): Q.diagnose(READ_TOKEN)

    def test_invalid_inputs_do_not_allocate_and_worker_dispatches_without_assist(self):
        for token in ('', True, None, 'A' * 32, READ_TOKEN + '\n', '../' + READ_TOKEN):
            with self.assertRaises(Q.N.NotebookError): Q.diagnose(token)
        self.assertEqual(list(self.state.iterdir()), [])
        directory = self.directory(); value = Q._input(directory)
        Q.N._write(directory / 'intent.json', dict(schema_version=1, profile=Q.PROFILE, argv=Q.arguments(directory),
            input_sha256=hashlib.sha256(Q.N._canonical(value)).hexdigest()))
        Q.N._write(directory / 'status.json', dict(schema_version=1, state='running', pid=4567, pgid=4567,
            process_start='fake start', evidence_token=None, exit_code=None, child_exited=False,
            cleanup_verified=False, error_code=None))
        Q.N._write(directory / 'launch.json', dict(pid=4567, ppid=4566, pgid=4567, process_start='fake start'))
        assist = Mock(side_effect=AssertionError('Assist forbidden'))
        with patch.object(Q.os, 'getpid', return_value=4567), patch.object(Q.os, 'getppid', return_value=4566), \
                patch.object(Q.os, 'getpgrp', return_value=4567), patch.object(Q.N, '_process_start', return_value='fake start'), \
                patch.object(D, 'worker', return_value=0) as worker:
            self.assertEqual(Q.worker(directory, assist=assist), 0)
        worker.assert_called_once_with(directory); assist.assert_not_called()

    def test_renderer_escapes_provenance_and_refuses_nonfixed_public(self):
        directory, code = self.completed(); _, public = Q.terminal_receipt(directory, code)
        public.update(attempt_token='<script>', evidence='<img>')
        rendered = Q.render_diagnose(public)
        self.assertIn('&lt;script&gt;', rendered); self.assertNotIn('<img>', rendered)
        for change in [dict(public, result='failed'), dict(public, cleanup_verified=False),
                       dict(public, diagnosis=dict(public['diagnosis'], findings=['<script>']))]:
            with self.assertRaises(Q.N.NotebookError): Q.render_diagnose(change)


class PreviewTokenTests(unittest.TestCase):
    def fixture(self, *, filtered=False, unsupported=False):
        from tests.test_notebook_lab_query import LabQueryTests
        fixture = LabQueryTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        assist, directory, _, _ = fixture.completed(filtered=filtered, unsupported=unsupported)
        code = 2 if unsupported else 0; token, public = Q.terminal_receipt(directory, code)
        Q.N._write(directory / 'status.json', dict(schema_version=1, state=public['result'], pid=4567, pgid=4567,
            process_start='old start', evidence_token=token, exit_code=code, child_exited=True,
            cleanup_verified=True, error_code=None))
        Q.N._write(directory / 'result.json', public)
        return fixture, assist, directory, public

    def test_original_and_filtered_preview_resolve_only_verified_reader_token(self):
        for filtered in (False, True):
            with self.subTest(filtered=filtered):
                _, assist, directory, public = self.fixture(filtered=filtered)
                frames = [json.loads(line) for line in (assist.output / 'assets/reader.stdout.jsonl').read_bytes().splitlines()]
                with patch.object(Q.T, 'group_empty', side_effect=AssertionError('old PID reused')), \
                        patch.object(Q.N, 'read_select', side_effect=AssertionError('no replay')):
                    token = Q.preview_read_token(directory.name)
                self.assertEqual(token, frames[-1]['evidence_token'])
                self.assertNotIn(token, (directory.name, assist.output.name))
                with self.assertRaises(Q.N.NotebookError): Q.preview_read_token(assist.output.name)
                Q.N._write(directory / 'result.json', dict(public, preview=None), replace=True)
                with self.assertRaises(Q.N.NotebookError): Q.preview_read_token(directory.name)

    def test_count_unsupported_and_pending_are_not_successful_preview_sources(self):
        _, _, directory, _ = self.fixture(filtered=True, unsupported=True)
        with self.assertRaises(Q.N.NotebookError): Q.preview_read_token(directory.name)
        Q.N._write(directory / 'input.json', dict(schema_version=2, mode='count',
                   input=dict(table='smoke.fixture_v1', request='件数')), replace=True)
        with self.assertRaises(Q.N.NotebookError): Q.preview_read_token(directory.name)


class DiagnoseTemplateTests(unittest.TestCase):
    def test_initial_run_is_inert_explicit_preview_resolves_once_and_recovery_has_no_calls(self):
        cells = [c for c in json.loads(TEMPLATE.read_bytes())['cells'] if c['cell_type'] == 'code']
        self.assertEqual(len(cells), 4)
        for cell in cells:
            self.assertEqual(cell['outputs'], []); self.assertIsNone(cell['execution_count'])
            compile(''.join(cell['source']), '<doctor-template>', 'exec')
        scope = dict(diagnose=Mock(return_value={'passed':True}), preview_read_token=Mock(return_value=READ_TOKEN),
            recover_diagnose=Mock(return_value={'saved_result':True}), render_diagnose=Mock(return_value='<section>doctor</section>'),
            NotebookError=Q.N.NotebookError, HTML=lambda x:x, display=Mock())
        for cell in cells[1:]: exec(''.join(cell['source']), scope)
        for name in ('diagnose','preview_read_token','recover_diagnose','display'): scope[name].assert_not_called()
        scope.update(preview_attempt_token='d' * 32, run_diagnose=True)
        run = ''.join(cells[2]['source']); exec(run, scope)
        scope['preview_read_token'].assert_called_once_with('d' * 32); scope['diagnose'].assert_called_once_with(READ_TOKEN)
        scope['diagnose'].side_effect = Q.N.NotebookError('LAB_QUERY_FAILED')
        with patch('builtins.print'): exec(run, scope)
        for key in ('diagnose_result','diagnose_html','candidate','candidate_html'): self.assertIsNone(scope[key])
        scope['diagnose'].side_effect = None
        for first in (KeyboardInterrupt(), RuntimeError('render failed')):
            scope['display'].reset_mock(); scope['render_diagnose'].side_effect = first
            with self.assertRaises(type(first)) as caught: exec(run, scope)
            self.assertIs(caught.exception, first)
            for key in ('diagnose_result','diagnose_html','candidate','candidate_html'): self.assertIsNone(scope[key])
            scope['display'].assert_not_called()
        scope['render_diagnose'].side_effect = None
        for name in ('diagnose','preview_read_token'): scope[name].reset_mock()
        recover = ''.join(cells[3]['source']).replace('diagnose_attempt = ""', 'diagnose_attempt = "' + 'e' * 32 + '"')
        exec(recover, scope); scope['recover_diagnose'].assert_called_once_with('e' * 32)
        for name in ('diagnose','preview_read_token'): scope[name].assert_not_called()
        for recovery_error in (KeyboardInterrupt(), RuntimeError('saved render failed')):
            scope['render_diagnose'].side_effect = recovery_error
            with self.assertRaises(type(recovery_error)) as caught: exec(recover, scope)
            self.assertIs(caught.exception, recovery_error)
            for key in ('saved_diagnosis','saved_diagnosis_html','saved_candidate','saved_candidate_html'): self.assertIsNone(scope[key])
        scope['render_diagnose'].side_effect = None


if __name__ == '__main__':
    unittest.main()
