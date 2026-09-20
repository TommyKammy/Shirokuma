"""Catalog Lab ownership and saved metadata; fake children only, no HTTP/SQL."""
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

from tools.local_experiment.notebook_lab import query as Q, catalog as G, runtime as R
from tests.test_catalog_read_contract import documents, public, snapshot, CANARY
from tests.test_notebook_catalog_client import frames, TOKEN, TABLE


class LabCatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Q.ROOT / 'tmp')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.lab = self.base / 'lab'; self.lab.mkdir(mode=0o700)
        self.state = self.lab / 'queries'; self.state.mkdir(mode=0o700)
        self.catalog_state = self.base / 'catalog'; self.catalog_state.mkdir(mode=0o700)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for owner, key, value in [(Q, 'LAB_STATE', self.lab), (Q, 'STATE', self.state),
                                  (R, 'STATE', self.lab), (G.R, 'STATE', self.catalog_state)]:
            self.stack.enter_context(patch.object(owner, key, value))
        self.stack.enter_context(patch.object(Q.subprocess, 'Popen', side_effect=AssertionError('process forbidden')))
        self.stack.enter_context(patch.object(Q.T, 'group_empty', return_value=True))
        self.stack.enter_context(patch.object(G.A, 'environment', side_effect=AssertionError('model forbidden')))
        for name in ('read_catalog', 'read_tables', 'read_describe', 'read_count', 'read_select'):
            self.stack.enter_context(patch.object(Q.N, name, side_effect=AssertionError('Reader forbidden')))
        self.source = {key: 'a' * 64 for key in ('tools/local_experiment/notebook_lab/catalog.py',
            'tools/local_experiment/notebook_lab/query.py', 'tools/local_experiment/notebook/client.py',
            'tools/local_experiment/catalog_read/contract.py')}
        self.stack.enter_context(patch.object(G, 'sources', side_effect=lambda: deepcopy(self.source)))
        self.counter = 0; self.calls = []

    def directory(self, request=None):
        self.counter += 1
        directory = self.state / f'{self.counter:032x}'; directory.mkdir(mode=0o700)
        Q.N._write(directory / 'input.json', dict(schema_version=6, mode='catalog',
                     input=request or dict(command='describe', table=TABLE)))
        return directory

    def build(self, binary, log):
        binary.write_bytes(b'never executed'); binary.chmod(0o700)

    def saved_summary(self, raw=None):
        request = dict(command='describe', table=TABLE)
        observed = snapshot(raw)
        reads = [dict(method='POST', scope='login', attempt=1, result='finished', response_sha256=None)]
        for attempt in (1, 2):
            reads += [dict(method='GET', scope=key, attempt=attempt, result='finished',
                           response_sha256=observed['response_sha256'][key])
                      for key in ('team', 'service', 'database', 'schema', 'table', 'lineage')]
        summary = dict(schema_version=1, case=G.N.CR.PROFILE, evidence_token=TOKEN,
            request=request, request_sha256=G.N.CR.digest(request), result='passed',
            started_at='2026-09-20T00:00:00Z', finished_at='2026-09-20T00:00:01Z',
            source_hashes={key: 'a' * 64 for key in ('tools/local_experiment/catalog_read/run.py',
                                                   'tools/local_experiment/catalog_read/contract.py')},
            state_before={'identity': 'saved'}, state_after={'identity': 'saved'},
            runtime_before={'identity': 'owned'}, runtime_after={'identity': 'owned'},
            snapshots=[observed, deepcopy(observed)], reads=reads, observation=public(raw), error_code=None,
            cleanup_verified=True, invocations=1, retries=0, model_calls=0, sql_queries=0)
        directory = self.catalog_state / TOKEN
        if not directory.exists(): directory.mkdir(mode=0o700)
        Q.N._write(directory / 'summary.json', summary, replace=True)
        return summary

    def reader(self, directory, raw=None, *, result='passed', cleanup=True, interrupt=None):
        def read(table):
            self.calls.append(table)
            output = directory / 'catalog/reader'
            self.assertEqual(Path(os.environ['SHIROKUMA_NOTEBOOK_RUN_DIR']), output)
            value = public(raw) if result == 'passed' else None
            Q.N._write(output / 'reader-intent.json', dict(schema_version=1, command='catalog describe',
                          request=dict(command='describe', table=TABLE)))
            code = 0 if result == 'passed' else 130 if result == 'interrupted' else 1
            Q.N._write(output / 'reader-status.json', dict(schema_version=1, state=result, pid=4568, pgid=4568,
                process_start='saved start', evidence_token=TOKEN, exit_code=code, child_exited=True,
                cleanup_verified=cleanup, error_code=None if result == 'passed' else
                    'NOTEBOOK_INTERRUPTED' if result == 'interrupted' else 'NOTEBOOK_READER_FAILED'))
            saved_frames = frames(value, result=result, cleanup=cleanup)
            data = b''.join(Q.N._canonical(frame) for frame in saved_frames)
            p = output / 'reader.stdout.jsonl'; p.write_bytes(data); p.chmod(0o600)
            if result == 'passed': self.saved_summary(raw)
            if interrupt is not None: raise interrupt
            if result != 'passed': raise Q.N.NotebookError('NOTEBOOK_READER_FAILED')
            return value
        return read

    def completed(self, raw=None, *, result='passed', cleanup=True, interrupt=None):
        directory = self.directory(); stream = io.StringIO()
        code = G.worker(directory, build=self.build,
            reader=self.reader(directory, raw, result=result, cleanup=cleanup, interrupt=interrupt), stdout=stream)
        Q.N._write(directory / 'worker.stdout.json', json.loads(stream.getvalue()))
        return directory, code

    def passed_status(self, directory, value):
        Q.N._write(directory / 'status.json', dict(schema_version=1, state='passed', pid=4567, pgid=4567,
            process_start='saved start', evidence_token=directory.name, exit_code=0, child_exited=True,
            cleanup_verified=True, error_code=None))
        Q.N._write(directory / 'result.json', value)

    def test_fixed_catalog_binds_safe_summary_and_two_observations(self):
        directory, code = self.completed()
        token, value = Q.terminal_receipt(directory, code, verify_catalog_source=True)
        self.assertEqual(code, 0); self.assertEqual(token, directory.name)
        self.assertEqual(value['catalog'], public()); self.assertEqual(value['catalog_token'], TOKEN)
        self.assertEqual(value['schema_version'], 6); self.assertEqual(value['model_calls'], 0)
        self.assertTrue(value['cleanup_verified']); self.assertTrue(value['observed_at'].endswith('Z'))
        self.assertEqual(self.calls, [TABLE]); G.A.environment.assert_not_called()
        rendered = Q.render_catalog(value)
        for expected in ('BIGINT', 'VARCHAR', 'OpenLineage', '過去の関連', '実在', '所有者'):
            self.assertIn(expected, rendered)
        self.assertNotIn(CANARY, rendered); self.assertNotIn('tmp/local-experiment', rendered)

    def test_render_distinguishes_missing_empty_and_observed_zero_and_escapes_text(self):
        raw = documents(empty=True); raw['table']['description'] = ''
        raw['table']['columns'][1]['description'] = '<script>bad()</script>'
        directory, code = self.completed(raw); _, value = Q.terminal_receipt(directory, code)
        rendered = Q.render_catalog(value)
        for expected in ('空文字', '未記録', '関連は0件', '&lt;script&gt;'):
            self.assertIn(expected, rendered)
        self.assertNotIn('<script>', rendered)
        bad = deepcopy(value); del bad['catalog']['lineage']['edges']
        with self.assertRaises(Q.N.NotebookError): Q.render_catalog(bad)

    def test_invalid_targets_never_allocate_or_launch(self):
        for table in (None, True, '', 'iceberg.' + TABLE, '../fixture_v1', 'smoke.other'):
            with self.subTest(table=table), self.assertRaises(Q.N.NotebookError): Q.catalog(table)
        self.assertEqual(list(self.state.iterdir()), []); Q.subprocess.Popen.assert_not_called()

    def test_failed_catalog_is_not_empty_success_and_unknown_blocks_shared_owner(self):
        directory, code = self.completed(result='failed')
        _, value = Q.terminal_receipt(directory, code)
        self.assertEqual(value['result'], 'failed'); self.assertIsNone(value['catalog'])
        self.assertIsNone(value['observed_at']); self.assertTrue(value['cleanup_verified'])
        with self.assertRaises(Q.N.NotebookError): Q.render_catalog(value)
        directory.rename(self.base / 'closed-failure')
        directory, code = self.completed(result='failed', cleanup=False)
        _, value = Q.terminal_receipt(directory, code)
        self.assertEqual(value['result'], 'unknown'); self.assertFalse(value['cleanup_verified'])
        Q.N._write(directory / 'status.json', dict(schema_version=1, state='unknown', pid=4567, pgid=4567,
            process_start='saved start', evidence_token=directory.name, exit_code=1, child_exited=True,
            cleanup_verified=False, error_code='LAB_QUERY_CLEANUP_UNKNOWN'))
        self.assertFalse(Q.check_idle())
        for call in (lambda: Q.catalog(TABLE), lambda: Q.preview(TABLE, 'idを1件'), lambda: Q.count(TABLE, '件数')):
            with self.assertRaises(Q.N.NotebookError): call()
        Q.subprocess.Popen.assert_not_called()

    def test_saved_recovery_has_no_live_source_process_or_reader_dependency(self):
        directory, code = self.completed(); _, value = Q.terminal_receipt(directory, code)
        self.passed_status(directory, value); before = list(self.calls)
        with patch.object(G, 'sources', side_effect=AssertionError('no current source')), \
             patch.object(Q.T, 'group_empty', side_effect=AssertionError('old PID reused')):
            recovered = Q.recover_catalog(directory.name)
            self.assertTrue(Q.query_closed(directory)); self.assertTrue(Q.check_idle())
        self.assertEqual(recovered, dict(value, saved_result=True)); self.assertEqual(self.calls, before)
        self.assertIn('追加GETは行っていません', Q.render_catalog(recovered))

    def test_summary_tampering_and_partial_reads_are_refused(self):
        directory, code = self.completed(); path = self.catalog_state / TOKEN / 'summary.json'
        original = Q._json(path, G.MAX_SUMMARY_BYTES)
        variants = [dict(original, sql_queries=1), dict(original, model_calls=True),
                    dict(original, reads=original['reads'][:-1]), dict(original, source_hashes={}),
                    dict(original, request_sha256='f' * 64), dict(original, observation=public(documents(empty=True)))]
        for value in variants:
            Q.N._write(path, value, replace=True)
            with self.assertRaises((Q.N.NotebookError, G.N.CR.ContractError)): Q.terminal_receipt(directory, code)
        Q.N._write(path, original, replace=True)
        self.assertEqual(Q.terminal_receipt(directory, code)[1]['result'], 'passed')

    def test_current_source_comparison_only_applies_to_new_execution(self):
        directory, code = self.completed()
        with patch.object(G, 'sources', return_value={'changed': 'f' * 64}):
            with self.assertRaises(Q.N.NotebookError): Q.terminal_receipt(directory, code, verify_catalog_source=True)
            self.assertEqual(Q.terminal_receipt(directory, code)[1]['result'], 'passed')
        path = directory / 'catalog/owner-result.json'; original = Q._json(path)
        for value in (dict(original, model_calls=1), dict(original, observed_at='bad UTC'),
                      dict(original, catalog_token='e' * 32), dict(original, catalog_seal={'summary_sha256':'f'*64})):
            Q.N._write(path, value, replace=True)
            with self.assertRaises(Q.N.NotebookError): Q.terminal_receipt(directory, code)

    def test_source_change_after_read_clears_result(self):
        directory = self.directory(); read = self.reader(directory); stream = io.StringIO()
        def changed(table):
            value = read(table); self.source['changed'] = 'f' * 64; return value
        code = G.worker(directory, build=self.build, reader=changed, stdout=stream)
        Q.N._write(directory / 'worker.stdout.json', json.loads(stream.getvalue()))
        _, value = Q.terminal_receipt(directory, code)
        self.assertEqual(value['result'], 'failed'); self.assertIsNone(value['catalog'])
        self.assertIsNone(value['catalog_token']); self.assertTrue(value['cleanup_verified'])

    def test_original_cancel_survives_secondary_owner_write_failure(self):
        directory = self.directory(); first = KeyboardInterrupt(); write = Q.N._write
        def fail(path, value, **kwargs):
            if path.name == 'owner-result.json': raise OSError('secondary')
            return write(path, value, **kwargs)
        with patch.object(Q.N, '_write', side_effect=fail), self.assertRaises(KeyboardInterrupt) as caught:
            G.worker(directory, build=self.build,
                reader=self.reader(directory, result='interrupted', interrupt=first), stdout=io.StringIO())
        self.assertIs(caught.exception, first)

    def test_parent_uses_shared_lock_and_one_owned_worker_without_environment_mutation(self):
        child = Mock(pid=4567, returncode=0); child.poll.return_value = 0
        def launch(argv, **kwargs):
            directory = Path(argv[-1]); stream = io.StringIO()
            self.assertEqual(argv, Q.arguments(directory)); self.assertTrue(kwargs['start_new_session'])
            self.assertEqual(G.worker(directory, build=self.build, reader=self.reader(directory), stdout=stream), 0)
            kwargs['stdout'].write(stream.getvalue().encode()); kwargs['stdout'].flush(); return child
        before = dict(os.environ)
        with patch.object(Q.subprocess, 'Popen', side_effect=launch) as start, \
             patch.object(Q.N, '_process_start', return_value='saved start'):
            value = Q.catalog(TABLE)
        self.assertEqual(start.call_count, 1); self.assertEqual(dict(os.environ), before)
        self.assertTrue(Q.query_closed(self.state / value['attempt_token']))
        with Q._lock(), self.assertRaisesRegex(Q.N.NotebookError, 'LAB_QUERY_BUSY'): Q.catalog(TABLE)

    def test_worker_handshake_routes_catalog_without_assist(self):
        directory = self.directory(); value = Q._input(directory)
        Q.N._write(directory / 'intent.json', dict(schema_version=1, profile=Q.PROFILE,
            argv=Q.arguments(directory), input_sha256=hashlib.sha256(Q.N._canonical(value)).hexdigest()))
        Q.N._write(directory / 'status.json', dict(schema_version=1, state='running', pid=4567, pgid=4567,
            process_start='saved start', evidence_token=None, exit_code=None, child_exited=False,
            cleanup_verified=False, error_code=None))
        Q.N._write(directory / 'launch.json', dict(pid=4567, ppid=4566, pgid=4567, process_start='saved start'))
        assist = Mock(side_effect=AssertionError('Assist forbidden'))
        with patch.object(Q.os, 'getpid', return_value=4567), patch.object(Q.os, 'getppid', return_value=4566), \
             patch.object(Q.os, 'getpgrp', return_value=4567), patch.object(Q.N, '_process_start', return_value='saved start'), \
             patch.object(G, 'worker', return_value=0) as worker:
            self.assertEqual(Q.worker(directory, assist=assist), 0)
        worker.assert_called_once_with(directory); assist.assert_not_called()


class CatalogTemplateTests(unittest.TestCase):
    def cells(self):
        doc = json.loads(R.CATALOG_TEMPLATE.read_bytes())
        code = [c for c in doc['cells'] if c['cell_type'] == 'code']
        self.assertTrue(all(c['execution_count'] is None and c['outputs'] == [] for c in code))
        return [''.join(c['source']) for c in code]

    def scope(self):
        return {name: Mock(side_effect=AssertionError('unexpected call')) for name in
                ('catalog', 'recover_catalog', 'render_catalog', 'display', 'HTML')} | {'NotebookError': Q.N.NotebookError}

    def test_initial_run_all_has_no_reads_or_display(self):
        cells = self.cells(); scope = self.scope()
        for cell in cells[1:]: exec(cell, scope)
        for name in ('catalog', 'recover_catalog', 'render_catalog', 'display'): scope[name].assert_not_called()
        self.assertIsNone(scope['catalog_result']); self.assertIsNone(scope['saved_catalog'])

    def test_live_and_saved_render_failure_clear_public_and_intermediate_values(self):
        cells = self.cells()
        for saved in (False, True):
            for error in (Q.N.NotebookError('LAB_CATALOG_INVALID'), KeyboardInterrupt(), RuntimeError('unexpected')):
                scope = self.scope(); exec(cells[1], scope)
                scope['catalog'].side_effect = None; scope['catalog'].return_value = {'safe':'value'}
                scope['recover_catalog'].side_effect = None; scope['recover_catalog'].return_value = {'safe':'value'}
                scope['render_catalog'].side_effect = error
                scope.update(table=TABLE, run_catalog=True, catalog_result={'stale':True}, catalog_html='stale',
                             saved_catalog={'stale':True}, saved_catalog_html='stale')
                cell = cells[3].replace('catalog_attempt = ""', 'catalog_attempt = "' + 'a'*32 + '"') if saved else cells[2]
                if isinstance(error, Q.N.NotebookError):
                    with patch('builtins.print'): exec(cell, scope)
                else:
                    try: exec(cell, scope)
                    except BaseException as caught: self.assertIs(caught, error)
                    else: self.fail('original exception not raised')
                keys = ('saved_catalog','saved_catalog_html','saved_candidate','saved_candidate_html') if saved else (
                    'catalog_result','catalog_html','candidate','candidate_html')
                self.assertTrue(all(scope[key] is None for key in keys)); scope['display'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
