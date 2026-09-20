"""Private fake Assist/Reader receipts and worker lifecycle; no services."""
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

from tools.local_experiment.notebook_lab import query as Q
from tools.local_experiment.notebook_lab import runtime as R
from tests import test_notebook_assist_runtime as V
from tests import test_notebook_assist_filtered as F


class LabQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Q.ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.lab = self.base / 'lab'
        self.lab.mkdir(mode=0o700)
        self.state = self.lab / 'queries'
        self.state.mkdir(mode=0o700)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(Q, 'LAB_STATE', self.lab))
        self.stack.enter_context(patch.object(Q, 'STATE', self.state))
        self.stack.enter_context(patch.object(R, 'STATE', self.lab))
        self.stack.enter_context(patch.object(Q.T, 'group_empty', return_value=True))
        self.stack.enter_context(patch.object(Q.subprocess, 'Popen', side_effect=AssertionError('real worker forbidden')))

    def completed(self, *, filtered=False, unsupported=False):
        fixture = F.FilteredRuntimeTests() if filtered else V.RuntimeTests()
        original = tempfile.TemporaryDirectory
        with patch.object(V.tempfile, 'TemporaryDirectory', lambda: original(dir=Q.ROOT / 'tmp')):
            fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        if unsupported:
            fixture.model.proposal = dict(columns=None, limit=None, unsupported=True)
            if filtered:
                fixture.model.proposal['where'] = None
        summary = fixture.execute()
        summary['audit'] = {'verified': True, 'saved_spans_verified': True}
        Q.N._write(fixture.output / 'summary.json', summary, replace=True)
        self.stack.enter_context(patch.object(Q.A, 'FILTERED_STATE' if filtered else 'STATE', fixture.base))
        request = F.INPUT if filtered else V.INPUT
        frame = dict(result=summary['result'], error_code=None, model_calls=1,
                     reader_cleanup_confirmed=True,
                     evidence=str(fixture.output.relative_to(Q.ROOT) / 'owner-result.json'))
        owner = dict(schema_version=2 if filtered else 1, profile=F.F.PROFILE if filtered else Q.A.PROFILE,
                     result=summary['result'], summary_result=summary['result'], error_code=None,
                     model_calls=1, reader_cleanup_confirmed=True)
        Q.N._write(fixture.output / 'owner-result.json', owner)
        directory = self.state / ('d' * 32)
        directory.mkdir(mode=0o700)
        value = {'schema_version': 1, 'filtered': filtered, 'input': deepcopy(request)}
        Q.N._write(directory / 'input.json', value)
        Q.N._write(directory / 'worker.stdout.json', frame)
        return fixture, directory, frame, value

    def test_completed_original_and_filtered_use_real_validators(self):
        for filtered in (False, True):
            with self.subTest(filtered=filtered):
                fixture, directory, _, value = self.completed(filtered=filtered)
                token, result = Q.terminal_receipt(directory, 0)
                self.assertEqual(token, fixture.output.name)
                self.assertEqual(result['preview']['schema_version'], 3 if filtered else 1)
                self.assertEqual(result['preview']['table'], 'iceberg.' + value['input']['table'])
                self.assertEqual(result['model_calls'], 1)
                self.assertTrue(result['cleanup_verified'])
                if filtered:
                    self.assertEqual(set(result['preview']['where']), {'column', 'type', 'operator'})
                directory.rename(directory.with_name(('e' if filtered else 'f') * 32))

    def test_unsupported_has_no_rows_assets_or_invented_error(self):
        fixture, directory, _, _ = self.completed(filtered=True, unsupported=True)
        _, result = Q.terminal_receipt(directory, 2)
        self.assertEqual(result['result'], 'unsupported')
        self.assertIsNone(result['preview'])
        self.assertEqual(fixture.asset_calls, [])
        self.assertFalse((fixture.output / 'assets').exists())
        self.assertIn('対象外', Q.render_preview(result))

    def test_mismatched_exit_extra_fields_input_and_audit_are_refused(self):
        fixture, directory, frame, _ = self.completed()
        with self.assertRaises(Q.N.NotebookError):
            Q.terminal_receipt(directory, 2)
        for altered in [dict(frame, extra='secret'), dict(frame, model_calls=0),
                        dict(frame, evidence=frame['evidence'].replace('c' * 32, '..'))]:
            Q.N._write(directory / 'worker.stdout.json', altered, replace=True)
            with self.assertRaises((Q.N.NotebookError, FileNotFoundError)):
                Q.terminal_receipt(directory, 0)
        Q.N._write(directory / 'worker.stdout.json', frame, replace=True)
        summary = json.loads((fixture.output / 'summary.json').read_bytes())
        for change in [{'input_sha256': '0' * 64}, {'audit': {'verified': False, 'saved_spans_verified': True}}]:
            Q.N._write(fixture.output / 'summary.json', dict(summary, **change), replace=True)
            with self.assertRaises(Q.N.NotebookError):
                Q.terminal_receipt(directory, 0)

    def test_filtered_predicate_type_is_bound_to_full_describe(self):
        fixture, directory, _, _ = self.completed(filtered=True)
        p = fixture.output / 'assets/reader.stdout.jsonl'
        frames = [json.loads(x) for x in p.read_bytes().splitlines()]
        frames[1]['observation']['where']['type'] = 'integer'
        p.write_bytes(V.encoded(frames))
        with self.assertRaises(Q.N.NotebookError):
            Q.terminal_receipt(directory, 0)

    def test_preview_fixed_worker_and_parent_environment_unchanged(self):
        _, fixture_dir, frame, value = self.completed()
        # Remove only the fake helper directory so the idle check sees no pending attempt.
        fixture_dir.rename(self.base / 'fixture-directory')
        child = Mock(pid=4567, returncode=0)
        child.poll.return_value = 0
        def popen(argv, **kwargs):
            self.assertEqual(argv, Q.arguments(Path(argv[-1])))
            self.assertTrue(kwargs['start_new_session'])
            self.assertEqual(kwargs['umask'], 0o077)
            kwargs['stdout'].write(Q.N._canonical(frame))
            kwargs['stdout'].flush()
            return child
        before = dict(os.environ)
        with patch.object(Q.subprocess, 'Popen', side_effect=popen), patch.object(Q.N, '_process_start', return_value='test start'):
            result = Q.preview(**value['input'])
        self.assertEqual(os.environ, before)
        self.assertEqual(result['result'], 'passed')
        child.wait.assert_called_once_with(timeout=600)
        directory = self.state / result['attempt_token']
        self.assertTrue(Q.query_closed(directory))
        self.assertTrue(Q.check_idle())
        self.assertNotIn('HOME', Q.environment(directory))
        self.assertNotIn('HTTP_PROXY', Q.environment(directory))
        for key in ('GOCACHE', 'GOPATH', 'GOMODCACHE'):
            cache = Path(Q.environment(directory)[key])
            self.assertTrue(cache.is_relative_to(self.lab / 'query-cache'))
            self.assertEqual(cache.stat().st_mode & 0o777, 0o700)

    def test_cancel_preserves_original_after_terminal_cleanup(self):
        fixture, fixture_dir, frame, value = self.completed()
        fixture_dir.rename(self.base / 'fixture-directory')
        frame.update(result='interrupted', error_code='INTERRUPTED')
        owner = json.loads((fixture.output / 'owner-result.json').read_bytes())
        owner.update(result='interrupted', error_code='INTERRUPTED')
        Q.N._write(fixture.output / 'owner-result.json', owner, replace=True)
        child = Mock(pid=4567, returncode=130)
        child.poll.return_value = 130
        first = KeyboardInterrupt()
        child.wait.side_effect = first
        def popen(argv, **kwargs):
            kwargs['stdout'].write(Q.N._canonical(frame)); kwargs['stdout'].flush()
            return child
        with patch.object(Q.subprocess, 'Popen', side_effect=popen), patch.object(Q.N, '_process_start', return_value='test start'), \
             patch.object(Q.T, '_interrupt', side_effect=lambda child, error: error) as interrupt:
            with self.assertRaises(KeyboardInterrupt) as caught:
                Q.preview(**value['input'])
        self.assertIs(caught.exception, first)
        interrupt.assert_called_once_with(child, first)
        directory = next(p for p in self.state.iterdir() if p.is_dir())
        self.assertEqual(Q.query_status(directory)['state'], 'interrupted')
        self.assertTrue(Q.query_status(directory)['cleanup_verified'])
        self.assertFalse((directory / 'result.json').exists())

    def test_unknown_cleanup_keeps_attempt_unavailable_without_retry(self):
        fixture, fixture_dir, frame, value = self.completed()
        fixture_dir.rename(self.base / 'fixture-directory')
        frame.update(result='unknown', error_code='NOTEBOOK_ASSIST_FAILED', reader_cleanup_confirmed=False)
        owner = json.loads((fixture.output / 'owner-result.json').read_bytes())
        owner.update(result='unknown', error_code='NOTEBOOK_ASSIST_FAILED', reader_cleanup_confirmed=False)
        Q.N._write(fixture.output / 'owner-result.json', owner, replace=True)
        child = Mock(pid=4567, returncode=1); child.poll.return_value = 1
        def popen(argv, **kwargs):
            kwargs['stdout'].write(Q.N._canonical(frame)); kwargs['stdout'].flush()
            return child
        with patch.object(Q.subprocess, 'Popen', side_effect=popen) as call, \
             patch.object(Q.N, '_process_start', return_value='test start'):
            with self.assertRaisesRegex(Q.N.NotebookError, 'CLEANUP_UNKNOWN'):
                Q.preview(**value['input'])
        self.assertEqual(call.call_count, 1)
        self.assertFalse(Q.check_idle())
        with self.assertRaisesRegex(Q.N.NotebookError, 'CLEANUP_UNKNOWN'):
            Q.preview(**value['input'])

    def test_bad_input_rejected_before_allocating(self):
        for table, text, filtered in [('bad;sql', 'x', False), ('smoke.fixture_v1', '', False),
                                       ('smoke.fixture_v1', 'x' * 513, False), ('smoke.fixture_v1', 'x', 1)]:
            with self.subTest(table=table, filtered=filtered), self.assertRaisesRegex(Q.N.NotebookError, 'INVALID'):
                Q.preview(table, text, filtered=filtered)
        self.assertEqual(list(self.state.iterdir()), [])

    def test_owned_lab_operation_blocks_query_before_worker_or_allocation(self):
        with R._lock():
            with self.assertRaisesRegex(Q.N.NotebookError, 'LAB_QUERY_BUSY'):
                Q.preview('smoke.fixture_v1', 'nameとidを1件表示')
        self.assertEqual(list(self.state.iterdir()), [])
        with Q._lock():
            with self.assertRaisesRegex(R.LabError, 'LAB_BUSY'), R._lock():
                self.fail('Lab stop acquired a running query owner lock')

    def test_worker_uses_fixed_existing_kubeconfig_not_parent_override(self):
        before = dict(os.environ)
        with patch.dict(os.environ, {'KUBECONFIG': '/untrusted/alternate-config'}):
            environment = Q.environment(self.state / ('a' * 32))
            self.assertEqual(os.environ['KUBECONFIG'], '/untrusted/alternate-config')
        self.assertEqual(environment['KUBECONFIG'], str(Q.KUBECONFIG))
        self.assertEqual(Q.KUBECONFIG, Path.home() / '.kube/config')
        self.assertNotEqual(environment['KUBECONFIG'], '/untrusted/alternate-config')
        self.assertNotIn('HOME', environment)
        self.assertEqual(os.environ, before)

    def test_worker_gates_fixed_input_and_delegates_once(self):
        directory = self.state / ('a' * 32); directory.mkdir(mode=0o700)
        value = {'schema_version': 1, 'filtered': True, 'input': deepcopy(F.INPUT)}
        Q.N._write(directory / 'input.json', value)
        Q.N._write(directory / 'intent.json', {'schema_version': 1, 'profile': Q.PROFILE,
            'argv': Q.arguments(directory), 'input_sha256': hashlib.sha256(Q.N._canonical(value)).hexdigest()})
        status = dict(schema_version=1, state='running', pid=4567, pgid=4567, process_start='test start',
                      evidence_token=None, exit_code=None, child_exited=False, cleanup_verified=False, error_code=None)
        Q.N._write(directory / 'status.json', status)
        Q.N._write(directory / 'launch.json', {'pid': 4567, 'ppid': 4566, 'pgid': 4567, 'process_start': 'test start'})
        assist = Mock(return_value=2)
        with patch.object(Q.os, 'getpid', return_value=4567), patch.object(Q.os, 'getppid', return_value=4566), \
             patch.object(Q.os, 'getpgrp', return_value=4567), patch.object(Q.N, '_process_start', return_value='test start'):
            self.assertEqual(Q.worker(directory, assist=assist), 2)
        self.assertEqual(assist.call_args.args, (['--filtered-request-stdin'],))
        self.assertEqual(json.loads(assist.call_args.kwargs['stdin'].read()), value['input'])
        value['input']['request'] = 'changed'
        Q.N._write(directory / 'input.json', value, replace=True)
        with self.assertRaises(Q.N.NotebookError):
            Q.worker(directory, assist=assist)
        self.assertEqual(assist.call_count, 1)

    def test_idle_refuses_partial_symlinks_and_unknown_names(self):
        self.assertTrue(Q.check_idle())
        with Q._lock():
            with self.assertRaisesRegex(Q.N.NotebookError, 'LAB_QUERY_BUSY'), Q._lock():
                self.fail('second invocation acquired the operation lock')
        directory = self.state / ('a' * 32); directory.mkdir(mode=0o700)
        self.assertFalse(Q.check_idle())
        directory.rmdir(); directory.symlink_to(self.base)
        with self.assertRaises(Q.N.NotebookError):
            Q.check_idle()
        directory.unlink(); (self.state / 'unrecognized').mkdir(mode=0o700)
        with self.assertRaises(Q.N.NotebookError):
            Q.check_idle()

    def test_renderer_escapes_rows_and_template_is_output_free(self):
        result = {'result': 'passed', 'cleanup_verified': True, 'attempt_token': '<script>', 'evidence': '<img>',
                  'preview': {'table': 'iceberg.smoke.fixture_v1', 'row_count': 2, 'truncated': False,
                    'columns': [{'name': '<name>'}], 'rows': [[{'type': 'varchar', 'value': '<script>alert(1)</script>'}],
                                                            [{'type': 'varchar', 'value': None}]]}}
        rendered = Q.render_preview(result)
        self.assertIn('読取成功', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertNotIn('<script>', rendered)
        self.assertNotIn("'type':", rendered)
        self.assertIn('<td>NULL</td>', rendered)
        template = json.loads((Q.ROOT / 'examples/local_query_workbench.ipynb').read_bytes())
        cells = [c for c in template['cells'] if c['cell_type'] == 'code']
        self.assertEqual(len(cells), 3)
        for cell in cells:
            self.assertEqual(cell['outputs'], [])
            self.assertIsNone(cell['execution_count'])
            compile(''.join(cell['source']), '<template>', 'exec')


if __name__ == '__main__':
    unittest.main()
