"""Saved COUNT receipts and shared Lab ownership; no model, services or worker."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.notebook_lab import query as Q, runtime as R
from tools.local_experiment.mlflow import runtime as M
from tests import test_local_query_count as QC
from tests import test_notebook_assist_runtime as V


class LabCountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Q.ROOT / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.lab = self.base / 'lab'; self.lab.mkdir(mode=0o700)
        self.state = self.lab / 'queries'; self.state.mkdir(mode=0o700)
        self.assist = self.base / 'assist'; self.assist.mkdir(mode=0o700)
        self.reader_state = self.base / 'reader'; self.reader_state.mkdir(mode=0o700)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(Q, 'LAB_STATE', self.lab))
        self.stack.enter_context(patch.object(Q, 'STATE', self.state))
        self.stack.enter_context(patch.object(R, 'STATE', self.lab))
        self.stack.enter_context(patch.object(Q.A, 'COUNT_STATE', self.assist))
        self.stack.enter_context(patch.object(Q, 'COUNT_READER_STATE', self.reader_state))
        self.stack.enter_context(patch.object(M, 'processes', side_effect=AssertionError('process inspection forbidden')))
        self.stack.enter_context(patch.object(Q.T, 'group_empty', return_value=True))
        self.stack.enter_context(patch.object(Q.subprocess, 'Popen', side_effect=AssertionError('worker forbidden')))
        for name in ('read_count', 'read_describe'):
            self.stack.enter_context(patch.object(Q.N, name, side_effect=AssertionError('Reader forbidden')))
        self.sources = {'tools/local_experiment/query_count/' + name: 'a' * 64 for name in ('contract.py', 'run.py')}
        self.stack.enter_context(patch.object(Q.A, 'sources', return_value=self.sources))
        self.counter = 0

    def completed(self, *, where=None, amount=2, unsupported=False):
        self.counter += 1
        output = self.assist / f'{self.counter:032x}'; output.mkdir(mode=0o700)
        directory = self.state / f'{self.counter + 100:032x}'; directory.mkdir(mode=0o700)
        request = {'table': 'smoke.fixture_v1', 'request': '全行の件数を数えてください' if where is None else 'idが1の行の件数を数えてください'}
        value = {'schema_version': 2, 'mode': 'count', 'input': request}
        selected = None if unsupported else dict(QC.request(), where=where)
        public = None if unsupported else QC.public(selected, count=amount)
        identity = V.identity()
        description = output / 'describe'; description.mkdir(mode=0o700)
        V.saved_reader(description, {'command': 'describe', 'table': request['table']}, identity)
        if selected is not None:
            reader = output / 'count'; reader.mkdir(mode=0o700)
            V.saved_reader(reader, selected, public)
            frames = [json.loads(line) for line in (reader / 'reader.stdout.jsonl').read_bytes().splitlines()]
            for frame in frames:
                frame['schema_version'] = 4
            frames[1]['evidence'] = 'tmp/local-experiment/query-count/' + frames[1]['evidence_token'] + '/summary.json'
            (reader / 'reader.stdout.jsonl').write_bytes(V.encoded(frames))
        digest = hashlib.sha256(Q.N._canonical(request)).hexdigest()
        profile = 'natural-language-notebook-count-v1'
        summary = dict(schema_version=3, profile=profile, result='unsupported' if unsupported else 'passed',
            input_sha256=digest, source_hashes=deepcopy(self.sources), model_calls=1, selection=selected,
            description=identity, count_result=public, audit={'verified': True, 'saved_spans_verified': True},
            describe_cleanup_confirmed=True, count_cleanup_confirmed=True)
        frame = dict(result=summary['result'], error_code=None, model_calls=1, reader_cleanup_confirmed=True,
                     evidence=str(output.relative_to(Q.ROOT) / 'owner-result.json'))
        owner = dict(schema_version=3, profile=profile, result=summary['result'], summary_result=summary['result'],
                     error_code=None, model_calls=1, reader_cleanup_confirmed=True)
        Q.N._write(output / 'execution-intent.json', dict(schema_version=3, profile=profile, input_sha256=digest,
            binary_sha256='b' * 64, source_hashes=deepcopy(self.sources)))
        Q.N._write(output / 'input.json', request)
        Q.N._write(output / 'summary.json', summary)
        Q.N._write(output / 'model-response.json', {'content': {'where': where, 'unsupported': unsupported}})
        Q.N._write(output / 'owner-result.json', owner)
        Q.N._write(directory / 'input.json', value)
        Q.N._write(directory / 'worker.stdout.json', frame)
        return output, directory, frame, value

    def status(self, directory, output, *, state='passed', cleanup=True):
        Q.N._write(directory / 'status.json', dict(schema_version=1, state=state, pid=4567, pgid=4567,
            process_start='fake start', evidence_token=output.name, exit_code=0 if state == 'passed' else 1,
            child_exited=True, cleanup_verified=cleanup,
            error_code=None if state == 'passed' else 'LAB_QUERY_CLEANUP_UNKNOWN'))

    def unstarted(self):
        output, directory, frame, value = self.completed()
        self.status(directory, output, state='unknown', cleanup=False)
        Q.N._write(directory / 'intent.json', dict(schema_version=1, profile=Q.PROFILE,
            argv=Q.arguments(directory), input_sha256=hashlib.sha256(Q.N._canonical(value)).hexdigest()))
        Q.N._write(directory / 'launch.json', dict(pid=4567, ppid=4566, pgid=4567, process_start='fake start'))
        frame.update(result='unknown', error_code='NOTEBOOK_ASSIST_FAILED', reader_cleanup_confirmed=False)
        Q.N._write(directory / 'worker.stdout.json', frame, replace=True)
        owner = Q._json(output / 'owner-result.json')
        owner.update(result='unknown', summary_result='unknown', error_code='NOTEBOOK_ASSIST_FAILED', reader_cleanup_confirmed=False)
        Q.N._write(output / 'owner-result.json', owner, replace=True)
        summary = Q._json(output / 'summary.json')
        summary.update(result='unknown', count_result=None, count_cleanup_confirmed=False)
        Q.N._write(output / 'summary.json', summary, replace=True)
        reader_status = Q._json(output / 'count/reader-status.json')
        reader_status.update(state='failed', exit_code=1, cleanup_verified=False, error_code='NOTEBOOK_READER_FAILED')
        Q.N._write(output / 'count/reader-status.json', reader_status, replace=True)
        frames = [json.loads(line) for line in (output / 'count/reader.stdout.jsonl').read_bytes().splitlines()]
        frames[1].update(result='failed', error_code='OBSERVATION_FAILED', observation=None, cleanup_verified=False)
        (output / 'count/reader.stdout.jsonl').write_bytes(V.encoded(frames))
        reader = self.reader_state / reader_status['evidence_token']; reader.mkdir(mode=0o700)
        request = summary['selection']
        observation = dict(state='failed', request_sha256=QC.C.digest(request), inventory=None, identity_before=None,
            identity_after=None, snapshot_catalog_before=None, snapshot_catalog_after=None, selection=None,
            queries={'worker': [], 'control': []}, query_drain={'verified': False}, cleanup_verified=False,
            runtime={'state': 'failed', 'trino': None, 'polaris': None, 'cleanup': {'verified': True}},
            error_code='OBSERVATION_FAILED')
        Q.N._write(reader / 'summary.json', dict(schema_version=4, profile=QC.C.PROFILE,
            evidence_token=reader.name, result='failed', error_code='OBSERVATION_FAILED', cleanup_verified=False,
            request=request, request_sha256=QC.C.digest(request), invocations=1, model_calls=0, cloud_calls=0,
            retries=0, source_hashes=deepcopy(self.sources), observation=observation))
        for path, raw, mode in [(directory / 'worker.stderr.log', b'', 0o600), (output / 'build.log', b'', 0o600),
                (output / 'spans.jsonl', b'', 0o600), (output / 'model-context.json', b'{}\n', 0o600),
                (output / 'shirokuma', b'never executed', 0o700), (reader / 'run.lock', b'', 0o600)]:
            with path.open('xb') as stream:
                os.fchmod(stream.fileno(), mode); stream.write(raw)
        intent = Q._json(output / 'execution-intent.json')
        intent['binary_sha256'] = hashlib.sha256((output / 'shirokuma').read_bytes()).hexdigest()
        Q.N._write(output / 'execution-intent.json', intent, replace=True)
        return output, directory, reader

    def test_count_is_exact_string_with_current_snapshot_and_optional_descriptor(self):
        for amount, where in [(0, None), (9007199254740993, None), (2**63 - 1, {'column': 'id', 'literal': '1'})]:
            with self.subTest(amount=amount):
                output, directory, _, _ = self.completed(where=where, amount=amount)
                token, result = Q.terminal_receipt(directory, 0)
                self.assertEqual(token, output.name)
                self.assertEqual(result['count']['count'], str(amount))
                self.assertEqual(result['count']['snapshot_id'], result['count']['current_snapshot_id'])
                self.assertEqual(result['count']['where'], None if where is None else
                                 {'column': 'id', 'type': 'bigint', 'operator': 'eq'})
                self.assertNotIn('preview', result)
                self.assertNotIn('rows', result['count'])
                html = Q.render_count(result)
                self.assertIn(str(amount), html)
                self.assertIn(result['count']['snapshot_id'], html)

    def test_unsupported_is_not_a_zero_count_and_has_no_count_reader(self):
        output, directory, _, _ = self.completed(unsupported=True)
        _, result = Q.terminal_receipt(directory, 2)
        self.assertIsNone(result['count'])
        self.assertFalse((output / 'count').exists())
        rendered = Q.render_count(result)
        self.assertIn('COUNT は実行していません', rendered)
        self.assertNotIn('0 件', rendered)

    def test_input_modes_are_explicit_and_preview_shape_stays_unchanged(self):
        _, directory, _, value = self.completed()
        self.assertEqual(Q._input(directory), value)
        for bad in [dict(value, filtered=False), dict(value, mode='preview'), dict(value, schema_version=1),
                    dict(value, schema_version=True), dict(value, input=dict(value['input'], limit=1))]:
            Q.N._write(directory / 'input.json', bad, replace=True)
            with self.assertRaises(Exception): Q._input(directory)
        legacy = dict(schema_version=1, filtered=True, input=value['input'])
        Q.N._write(directory / 'input.json', legacy, replace=True)
        self.assertEqual(Q._input(directory), legacy)

    def test_worker_dispatches_count_once_and_binds_saved_input(self):
        _, directory, _, value = self.completed()
        Q.N._write(directory / 'intent.json', dict(schema_version=1, profile=Q.PROFILE,
            argv=Q.arguments(directory), input_sha256=hashlib.sha256(Q.N._canonical(value)).hexdigest()))
        Q.N._write(directory / 'status.json', dict(schema_version=1, state='running', pid=4567, pgid=4567,
            process_start='fake start', evidence_token=None, exit_code=None, child_exited=False,
            cleanup_verified=False, error_code=None))
        Q.N._write(directory / 'launch.json', dict(pid=4567, ppid=4566, pgid=4567, process_start='fake start'))
        assist = Mock(return_value=0)
        with patch.object(Q.os, 'getpid', return_value=4567), patch.object(Q.os, 'getppid', return_value=4566), \
                patch.object(Q.os, 'getpgrp', return_value=4567), patch.object(Q.N, '_process_start', return_value='fake start'):
            self.assertEqual(Q.worker(directory, assist=assist), 0)
        self.assertEqual(assist.call_args.args, (['--count-request-stdin'],))
        self.assertEqual(json.loads(assist.call_args.kwargs['stdin'].read()), value['input'])
        value['input']['request'] = 'changed'
        Q.N._write(directory / 'input.json', value, replace=True)
        with self.assertRaises(Q.N.NotebookError): Q.worker(directory, assist=assist)
        self.assertEqual(assist.call_count, 1)

    def test_fresh_count_shared_owner_and_saved_recovery_without_new_read(self):
        output, fixture, frame, value = self.completed(amount=9007199254740993)
        fixture.rename(self.base / 'fixture')
        child = Mock(pid=4567, returncode=0); child.poll.return_value = 0
        def popen(argv, **kwargs):
            self.assertEqual(argv, Q.arguments(Path(argv[-1])))
            self.assertTrue(kwargs['start_new_session'])
            kwargs['stdout'].write(Q.N._canonical(frame)); kwargs['stdout'].flush()
            return child
        before = dict(os.environ)
        with patch.object(Q.subprocess, 'Popen', side_effect=popen) as launch, \
                patch.object(Q.N, '_process_start', return_value='fake start'):
            result = Q.count(**value['input'])
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(os.environ, before)
        self.assertTrue(Q.check_idle())
        # Old saved evidence remains readable after a later source change.
        with patch.object(Q.A, 'sources', side_effect=AssertionError('current sources not needed')), \
                patch.object(Q.T, 'group_empty', side_effect=AssertionError('old PID may now belong to another process')) as inspect:
            saved = Q.recover_count(result['attempt_token'])
            self.assertTrue(Q.check_idle())
        inspect.assert_not_called()
        self.assertTrue(saved['saved_result'])
        self.assertEqual(saved['count'], result['count'])
        self.assertIn('保存結果', Q.render_count(saved))
        self.assertEqual(output.name, Q.query_status(self.state / result['attempt_token'])['evidence_token'])

    def test_new_count_checks_current_sources_but_saved_seal_is_sufficient_later(self):
        _, directory, _, _ = self.completed()
        with patch.object(Q.A, 'sources', return_value={'changed.py': 'f' * 64}):
            with self.assertRaises(Q.N.NotebookError):
                Q.terminal_receipt(directory, 0, verify_count_source=True)
            self.assertEqual(Q.terminal_receipt(directory, 0)[1]['result'], 'passed')

    def test_source_seal_result_and_before_identity_mismatches_are_refused(self):
        output, directory, _, _ = self.completed()
        original = Q._json(output / 'summary.json')
        mutations = [dict(original, source_hashes={'tools/local_experiment/query_count/run.py': 'a' * 64}),
                     dict(original, input_sha256='f' * 64),
                     dict(original, count_result=dict(original['count_result'], count='99')),
                     dict(original, description=dict(original['description'], table_uuid='00000000-0000-4000-8000-000000000099'))]
        for value in mutations:
            Q.N._write(output / 'summary.json', value, replace=True)
            with self.assertRaises(Q.N.NotebookError): Q.terminal_receipt(directory, 0)

    def test_count_frames_bind_snapshot_and_predicate_type_to_full_describe(self):
        output, directory, _, _ = self.completed(where={'column': 'id', 'literal': '1'})
        path = output / 'count/reader.stdout.jsonl'
        original = [json.loads(x) for x in path.read_bytes().splitlines()]
        for change in [dict(original[1]['observation'], snapshot_id='1'),
                       dict(original[1]['observation'], where={'column': 'id', 'type': 'integer', 'operator': 'eq'})]:
            frames = deepcopy(original); frames[1]['observation'] = change
            path.write_bytes(V.encoded(frames))
            summary = Q._json(output / 'summary.json'); summary['count_result'] = change
            Q.N._write(output / 'summary.json', summary, replace=True)
            with self.assertRaises(Q.N.NotebookError): Q.terminal_receipt(directory, 0)

    def test_unknown_count_blocks_preview_count_and_lab_lifecycle_without_new_worker(self):
        output, directory, _, value = self.completed()
        self.status(directory, output, state='unknown', cleanup=False)
        self.assertFalse(Q.check_idle())
        for call in [lambda: Q.count(**value['input']), lambda: Q.preview(**value['input'])]:
            with self.assertRaisesRegex(Q.N.NotebookError, 'LAB_QUERY_CLEANUP_UNKNOWN'): call()
        with patch.object(R, 'status', return_value=R._public('stopped')):
            for call in (R.start, R.stop):
                with self.assertRaisesRegex(R.LabError, 'LAB_QUERY_ACTIVE_OR_UNKNOWN'): call()
        Q.subprocess.Popen.assert_not_called()
        with self.assertRaises(Q.N.NotebookError): Q.recover_count(directory.name)

    def test_failed_or_unverified_count_cannot_be_rendered_as_saved_success(self):
        _, directory, _, _ = self.completed()
        _, result = Q.terminal_receipt(directory, 0)
        for bad in [dict(result, result='unknown'), dict(result, cleanup_verified=False),
                    dict(result, count=dict(result['count'], count=9007199254740993))]:
            with self.assertRaises(Q.N.NotebookError): Q.render_count(bad)
        result.update(attempt_token='<script>', evidence='<img>')
        rendered = Q.render_count(result)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertNotIn('<script>', rendered)
        self.assertNotIn('<img>', rendered)

    def test_count_invalid_input_never_allocates_or_starts_worker(self):
        for table, request in [('bad;sql', 'count'), ('smoke.fixture_v1', ''), ('smoke.fixture_v1', 'x' * 513)]:
            with self.assertRaises(Q.N.NotebookError): Q.count(table, request)
        self.assertEqual(list(self.state.iterdir()), [])

    def test_template_has_clear_before_run_and_recovery_never_repeats_count(self):
        template = json.loads(R.COUNT_TEMPLATE.read_bytes())
        cells = [c for c in template['cells'] if c['cell_type'] == 'code']
        self.assertEqual(len(cells), 4)
        for cell in cells:
            self.assertEqual(cell['outputs'], []); self.assertIsNone(cell['execution_count'])
            compile(''.join(cell['source']), '<count-template>', 'exec')
        scope = dict(table='smoke.fixture_v1', request='count', result={'old': 'success'},
            NotebookError=Q.N.NotebookError, count=Mock(side_effect=Q.N.NotebookError('LAB_QUERY_FAILED')),
            recover_count=Mock(return_value={'saved': True}), display=Mock(), HTML=lambda x: x,
            render_count=Mock(return_value='rendered'))
        with patch('builtins.print'):
            exec(''.join(cells[2]['source']), scope)
        self.assertIsNone(scope['result'])
        scope['display'].assert_not_called()
        scope['count'].reset_mock()
        code = ''.join(cells[3]['source']).replace('recovery_attempt = ""', 'recovery_attempt = "' + 'a' * 32 + '"')
        exec(code, scope)
        scope['recover_count'].assert_called_once_with('a' * 32)
        scope['count'].assert_not_called()
        self.assertEqual(scope['saved_result'], {'saved': True})

    def test_unstarted_closure_is_append_only_failed_and_repeat_uses_saved_proof(self):
        output, directory, reader = self.unstarted()
        before = Q._count_recovery_files(directory, output.name, reader.name)
        self.assertEqual(len(before), 23)
        with patch.object(M, 'processes', return_value={}) as processes:
            closure = Q.close_unstarted_count(directory.name)
        processes.assert_called_once_with()
        self.assertEqual(closure['result'], 'failed'); self.assertIsNone(closure['count'])
        self.assertTrue(closure['cleanup_verified'])
        self.assertEqual(closure['evidence'], before)
        self.assertEqual(Q._count_recovery_files(directory, output.name, reader.name), before)
        self.assertEqual(Q.query_status(directory)['state'], 'unknown')
        self.assertFalse(Q.query_status(directory)['cleanup_verified'])
        saved = (directory / 'recovery.json').read_bytes(), (directory / 'recovery.json').stat().st_mtime_ns
        with patch.object(Q.A, 'sources', side_effect=AssertionError('historical source must not be reread')), \
                patch.object(Q.T, 'group_empty', side_effect=AssertionError('old PID is no longer ownership')):
            self.assertEqual(Q.close_unstarted_count(directory.name), closure)
            self.assertTrue(Q.query_closed(directory)); self.assertTrue(Q.check_idle())
        M.processes.assert_not_called()
        self.assertEqual(((directory / 'recovery.json').read_bytes(), (directory / 'recovery.json').stat().st_mtime_ns), saved)
        self.assertEqual((directory / 'recovery.json').stat().st_mode & 0o777, 0o600)
        with self.assertRaises(Q.N.NotebookError): Q.recover_count(directory.name)
        with self.assertRaises(Q.N.NotebookError): Q.render_count(closure)

    def test_unstarted_closure_refuses_runtime_sql_or_incomplete_cleanup(self):
        _, directory, reader = self.unstarted()
        original = Q._json(reader / 'summary.json')
        edits = [dict(original['observation'], queries={'worker': [{'id': 'sent'}], 'control': []}),
                 dict(original['observation'], queries={'worker': [], 'control': [{'id': 'control'}]}),
                 dict(original['observation'], inventory={}),
                 dict(original['observation'], query_drain={'verified': True})]
        for key, value in [('trino', {'state': 'pending'}), ('polaris', {'state': 'pending'}),
                           ('cleanup', {'verified': False}), ('worker', {'records': []})]:
            edits.append(dict(original['observation'], runtime=dict(original['observation']['runtime'], **{key: value})))
        for observation in edits:
            Q.N._write(reader / 'summary.json', dict(original, observation=observation), replace=True)
            with self.assertRaisesRegex(Q.N.NotebookError, 'LAB_QUERY_RECOVERY_INVALID'):
                Q.close_unstarted_count(directory.name)
        self.assertFalse((directory / 'recovery.json').exists()); M.processes.assert_not_called()

    def test_unstarted_closure_refuses_live_pid_reused_pid_or_descendant_group(self):
        _, directory, _ = self.unstarted()
        cases = [{4567: {'pgid': 4567}}, {4567: {'pgid': 9999}}, {1234: {'pgid': 9999}},
                 {9999: {'pgid': 4567}}, {9999: {'pgid': 1234}}]
        for rows in cases:
            with patch.object(M, 'processes', return_value=rows), self.assertRaisesRegex(Q.N.NotebookError, 'RECOVERY_INVALID'):
                Q.close_unstarted_count(directory.name)
        with patch.object(M, 'processes', side_effect=RuntimeError('unknown process inventory')), \
                self.assertRaises(Q.N.NotebookError): Q.close_unstarted_count(directory.name)
        self.assertFalse((directory / 'recovery.json').exists())

    def test_unstarted_closure_refuses_other_unknown_attempt_without_process_io(self):
        _, directory, _ = self.unstarted()
        other = self.state / ('f' * 32); other.mkdir(mode=0o700)
        with self.assertRaises(Q.N.NotebookError): Q.close_unstarted_count(directory.name)
        self.assertFalse((directory / 'recovery.json').exists()); M.processes.assert_not_called()

    def test_unstarted_closure_refuses_changed_binding_and_changed_saved_original(self):
        output, directory, reader = self.unstarted()
        child = Q._json(reader / 'summary.json')
        for change in [dict(child, evidence_token='e' * 32), dict(child, request_sha256='e' * 64),
                       dict(child, invocations=2), dict(child, source_hashes={}),
                       dict(child, request=dict(child['request'], snapshot_id='1'))]:
            Q.N._write(reader / 'summary.json', change, replace=True)
            with self.assertRaises(Q.N.NotebookError): Q.close_unstarted_count(directory.name)
        Q.N._write(reader / 'summary.json', child, replace=True)
        with patch.object(M, 'processes', return_value={}): Q.close_unstarted_count(directory.name)
        log = output / 'build.log'; log.write_bytes(b'changed original')
        with self.assertRaises(Q.N.NotebookError): Q.query_closed(directory)
        with self.assertRaises(Q.N.NotebookError): Q.close_unstarted_count(directory.name)
        self.assertEqual(Q.query_status(directory)['state'], 'unknown')

    def test_unstarted_closure_refuses_race_after_process_check_without_append(self):
        _, directory, reader = self.unstarted()
        def changed():
            (reader / 'run.lock').write_bytes(b'changed during verification')
            return {}
        with patch.object(M, 'processes', side_effect=changed), self.assertRaises(Q.N.NotebookError):
            Q.close_unstarted_count(directory.name)
        self.assertFalse((directory / 'recovery.json').exists())


if __name__ == '__main__':
    unittest.main()
