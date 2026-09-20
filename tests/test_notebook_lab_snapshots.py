"""Offline Lab snapshot lifecycle: explicit serial COUNTs, exact saved results."""
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

from tools.local_experiment.notebook_lab import query as Q, snapshots as S, runtime as R
from tests.test_notebook_snapshot_client import snapshots_public, frames, TOKEN
from tests.test_local_query_count import public as count_public
from tests.test_query_snapshots_contract import OLD, HEAD


def request():
    return dict(command='compare_counts', table='smoke.fixture_v1', left_id=str(OLD), right_id=str(HEAD))


class LabSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Q.ROOT / 'tmp'); self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve(); self.lab = self.base / 'lab'; self.lab.mkdir(mode=0o700)
        self.state = self.lab / 'queries'; self.state.mkdir(mode=0o700)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for owner, key, value in [(Q, 'LAB_STATE', self.lab), (Q, 'STATE', self.state), (R, 'STATE', self.lab)]:
            self.stack.enter_context(patch.object(owner, key, value))
        self.stack.enter_context(patch.object(Q.subprocess, 'Popen', side_effect=AssertionError('real process forbidden')))
        self.stack.enter_context(patch.object(Q.T, 'group_empty', return_value=True))
        self.stack.enter_context(patch.object(S.A, 'environment', side_effect=AssertionError('model forbidden')))
        for name in ('read_tables', 'read_describe', 'read_count', 'read_select', 'read_snapshots', 'read_snapshot_count'):
            self.stack.enter_context(patch.object(Q.N, name, side_effect=AssertionError('real Reader forbidden')))
        self.source = {key: 'a' * 64 for key in ('tools/local_experiment/notebook_lab/snapshots.py',
            'tools/local_experiment/notebook_lab/query.py', 'tools/local_experiment/notebook/client.py',
            'tools/local_experiment/query_snapshots/contract.py', 'tools/local_experiment/query_count/contract.py')}
        self.stack.enter_context(patch.object(S, 'sources', side_effect=lambda: deepcopy(self.source)))
        self.counter = 0; self.reads = []

    def directory(self, selected):
        self.counter += 1
        directory = self.state / f'{self.counter:032x}'; directory.mkdir(mode=0o700)
        Q.N._write(directory / 'input.json', dict(schema_version=4, mode='snapshots', input=selected))
        return directory

    def build(self, binary, log):
        binary.write_bytes(b'never executed'); binary.chmod(0o700)

    def reader(self, directory, selected, *, counts=(2, 3), fail=None, cleanup=True, transform=None):
        def read(argument):
            # The phase directory is chosen by the owner, independently of this fake's call history.
            output = Path(os.environ['SHIROKUMA_NOTEBOOK_RUN_DIR']); phase = output.name
            self.assertEqual(output, directory / 'snapshots' / phase)
            self.reads.append((directory, phase, argument))
            if phase == 'right':
                self.assertTrue(S._closed(directory / 'snapshots', selected, 'left'))
            req = selected if phase == 'listing' else S.count_request(selected, phase)
            self.assertEqual(argument, req['table'] if phase == 'listing' else req)
            public = snapshots_public() if phase == 'listing' else count_public(req, counts[phase == 'right'])
            if transform is not None: public = transform(phase, public)
            result = fail[1] if fail and fail[0] == phase else 'passed'
            closed = cleanup if fail and fail[0] == phase else True
            code = 0 if result == 'passed' else 130 if result == 'interrupted' else 1
            Q.N._write(output / 'reader-intent.json', dict(schema_version=1, command='query ' + req['command'], request=req))
            Q.N._write(output / 'reader-status.json', dict(schema_version=1, state=result, pid=5678, pgid=5678,
                process_start='fake reader', evidence_token=TOKEN, exit_code=code, child_exited=True,
                cleanup_verified=closed, error_code={'passed': None, 'failed': 'NOTEBOOK_READER_FAILED',
                    'interrupted': 'NOTEBOOK_INTERRUPTED'}[result]))
            with (output / 'reader.stdout.jsonl').open('xb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(b''.join(Q.N._canonical(frame) for frame in frames(public, count=phase != 'listing',
                    result=result, cleanup=closed)))
            if result == 'interrupted': raise KeyboardInterrupt()
            if result == 'failed': raise Q.N.NotebookError('NOTEBOOK_READER_FAILED')
            return public
        return read

    def completed(self, selected=None, **options):
        selected = selected or request(); directory = self.directory(selected); stream = io.StringIO()
        code = S.worker(directory, build=self.build, reader=self.reader(directory, selected, **options), stdout=stream)
        Q.N._write(directory / 'worker.stdout.json', json.loads(stream.getvalue()))
        return directory, code, Q.terminal_receipt(directory, code)[1]

    def passed_status(self, directory, public):
        Q.N._write(directory / 'status.json', dict(schema_version=1, state='passed', pid=4567, pgid=4567,
            process_start='fake worker', evidence_token=directory.name, exit_code=0, child_exited=True,
            cleanup_verified=True, error_code=None))
        Q.N._write(directory / 'result.json', public)

    def test_listing_and_empty_are_model_free_and_snapshot_times_are_not_observation_time(self):
        selected = dict(command='snapshots', table='smoke.fixture_v1')
        for empty in (False, True):
            def transform(phase, public):
                return dict(public, current_snapshot_id=None, snapshots=[]) if empty else public
            directory, code, public = self.completed(selected, transform=transform)
            self.assertEqual(code, 0); self.assertEqual(public['model_calls'], 0)
            self.assertEqual(public['command'], 'snapshots'); self.assertIsNone(public['comparison'])
            self.assertEqual(public['snapshots']['snapshots'] == [], empty)
            html = Q.render_snapshots(public)
            self.assertEqual('snapshot はありません' in html, empty)
            self.assertIn(public['observed_at'], html)
            Q.terminal_receipt(directory, code, verify_snapshot_source=True)
        self.assertEqual([phase for _, phase, _ in self.reads], ['listing', 'listing'])
        S.A.environment.assert_not_called()

    def test_serial_comparison_exact_signed_zero_and_large_integer_difference(self):
        for counts, expected in [((2, 3), '1'), ((3, 2), '-1'), ((0, 0), '0'),
                                 ((0, 2**63 - 1), str(2**63 - 1)), ((2**63 - 1, 0), str(-(2**63 - 1)))]:
            directory, code, public = self.completed(counts=counts)
            self.assertEqual(code, 0); self.assertEqual(public['comparison']['difference'], expected)
            self.assertEqual(public['comparison']['definition'], 'right_count_minus_left_count')
            self.assertEqual(public['comparison']['left']['snapshot_id'], str(OLD))
            self.assertEqual(public['comparison']['right']['snapshot_id'], str(HEAD))
            self.assertIsNone(public['comparison']['left']['where']); self.assertIsNone(public['snapshots'])
            self.assertEqual([phase for d, phase, _ in self.reads if d == directory], ['left', 'right'])
            self.assertIn(expected, Q.render_snapshots(public))
            self.assertIn('追加・削除された行の内訳', Q.render_snapshots(public))

    def test_mismatched_uuid_schema_and_selected_snapshot_never_authorize_difference(self):
        for key, value in [('table_uuid', '11111111-1111-4111-8111-111111111111'), ('schema_sha256', 'f' * 64),
                           ('snapshot_id', str(OLD))]:
            directory, code, public = self.completed(transform=lambda phase, p: dict(p, **{key: value}) if phase == 'right' else p)
            self.assertEqual(code, 1); self.assertNotEqual(public['result'], 'passed')
            self.assertIsNone(public['comparison']['difference'])
            with self.assertRaises(Q.N.NotebookError): Q.render_snapshots(public)

    def test_left_failure_unknown_and_interruption_do_not_start_right(self):
        for state, cleanup in [('failed', True), ('failed', False), ('interrupted', True)]:
            directory, code, public = self.completed(fail=('left', state), cleanup=cleanup)
            self.assertEqual([phase for d, phase, _ in self.reads if d == directory], ['left'])
            self.assertFalse((directory / 'snapshots/right').exists())
            self.assertEqual(public['result'], state if cleanup else 'unknown')
            self.assertEqual(public['cleanup_verified'], cleanup)
            self.assertIsNone(public['comparison']['difference']); self.assertIsNone(public['observed_at'])
            self.assertEqual(code, 130 if state == 'interrupted' else 1)

    def test_right_failure_keeps_left_observation_without_a_difference(self):
        directory, code, public = self.completed(fail=('right', 'failed'))
        self.assertEqual(public['comparison']['left']['count'], '2')
        self.assertIsNone(public['comparison']['right']); self.assertIsNone(public['comparison']['difference'])
        self.assertEqual(public['result'], 'failed'); self.assertTrue(public['cleanup_verified'])

    def test_unknown_attempt_blocks_all_lab_query_modes_and_shared_idle(self):
        directory, code, public = self.completed(fail=('left', 'failed'), cleanup=False)
        Q.N._write(directory / 'status.json', dict(schema_version=1, state='unknown', pid=4567, pgid=4567,
            process_start='fake worker', evidence_token=directory.name, exit_code=code, child_exited=True,
            cleanup_verified=False, error_code='LAB_QUERY_CLEANUP_UNKNOWN'))
        self.assertFalse(Q.check_idle())
        for call in [lambda: Q.snapshots('smoke.fixture_v1'), lambda: Q.compare_counts('smoke.fixture_v1', str(OLD), str(HEAD)),
                     lambda: Q.tables('smoke'), lambda: Q.describe('smoke.fixture_v1'),
                     lambda: Q.preview('smoke.fixture_v1', 'idを1件'), lambda: Q.count('smoke.fixture_v1', '件数')]:
            with self.assertRaises(Q.N.NotebookError): call()
        Q.subprocess.Popen.assert_not_called()

    def test_saved_listing_and_comparison_need_no_current_source_pid_or_table(self):
        for selected in [dict(command='snapshots', table='smoke.fixture_v1'), request()]:
            directory, code, public = self.completed(selected); self.passed_status(directory, public)
            before = list(self.reads)
            with patch.object(S, 'sources', side_effect=AssertionError('current source forbidden')), \
                    patch.object(Q.T, 'group_empty', side_effect=AssertionError('old PID reused')):
                recovered = Q.recover_snapshots(directory.name)
                self.assertTrue(Q.query_closed(directory)); self.assertTrue(Q.check_idle())
            self.assertEqual(recovered, dict(public, saved_result=True)); self.assertEqual(self.reads, before)
            self.assertIn('保存結果', Q.render_snapshots(recovered))
            self.assertEqual(recovered['observed_at'], public['observed_at'])

    def test_receipt_refuses_tampered_seal_difference_ids_and_timestamp(self):
        directory, code, public = self.completed(); path = directory / 'snapshots/owner-result.json'
        original = Q._json(path)
        for changed in [dict(original, difference='999'), dict(original, observed_at='not UTC'),
                        dict(original, source_hashes={}), dict(original, request_sha256='f' * 64),
                        dict(original, left=dict(original['left'], snapshot_id=str(HEAD))), dict(original, model_calls=1)]:
            Q.N._write(path, changed, replace=True)
            with self.assertRaises(Q.N.NotebookError): Q.terminal_receipt(directory, code)
        Q.N._write(path, original, replace=True)
        with patch.object(S, 'sources', return_value={'changed': 'f' * 64}):
            with self.assertRaises(Q.N.NotebookError): Q.terminal_receipt(directory, code, verify_snapshot_source=True)
            self.assertEqual(Q.terminal_receipt(directory, code)[1], public)

    def test_source_change_after_left_is_closed_failure_and_no_right(self):
        selected = request(); directory = self.directory(selected); stream = io.StringIO()
        read = self.reader(directory, selected)
        def changed(argument):
            public = read(argument); self.source['changed'] = 'f' * 64; return public
        code = S.worker(directory, build=self.build, reader=changed, stdout=stream)
        Q.N._write(directory / 'worker.stdout.json', json.loads(stream.getvalue()))
        _, public = Q.terminal_receipt(directory, code)
        self.assertEqual(public['result'], 'failed'); self.assertTrue(public['cleanup_verified'])
        self.assertIsNone(public['comparison']['difference']); self.assertFalse((directory / 'snapshots/right').exists())

    def test_build_failure_starts_no_reader_and_preserves_first_interrupt(self):
        directory = self.directory(request()); stream = io.StringIO(); read = Mock()
        code = S.worker(directory, build=Mock(side_effect=OSError('build failed')), reader=read, stdout=stream)
        Q.N._write(directory / 'worker.stdout.json', json.loads(stream.getvalue()))
        public = Q.terminal_receipt(directory, code)[1]
        self.assertEqual(public['result'], 'failed'); self.assertTrue(public['cleanup_verified']); read.assert_not_called()
        selected = request(); directory = self.directory(selected); write = Q.N._write
        def failed_save(path, value, **kwargs):
            if path.name == 'owner-result.json': raise OSError('secondary')
            return write(path, value, **kwargs)
        with patch.object(Q.N, '_write', side_effect=failed_save), self.assertRaises(KeyboardInterrupt):
            S.worker(directory, build=self.build, reader=self.reader(directory, selected, fail=('left', 'interrupted')), stdout=io.StringIO())

    def test_invalid_table_ids_and_extra_filter_allocate_nothing(self):
        for selected in [dict(request(), table='other.x'), dict(request(), left_id=None), dict(request(), left_id=True),
                         dict(request(), left_id=1), dict(request(), left_id='01'), dict(request(), left_id=str(2**63)),
                         dict(request(), left_id=str(HEAD)), dict(request(), where=None)]:
            with self.assertRaises(Q.N.NotebookError): S.normalize_request(selected)
        for call in [lambda: Q.snapshots('system.x'), lambda: Q.compare_counts('smoke.fixture_v1', '01', str(HEAD)),
                     lambda: Q.compare_counts('smoke.fixture_v1', str(HEAD), str(HEAD))]:
            with self.assertRaises(Q.N.NotebookError): call()
        self.assertEqual(list(self.state.iterdir()), []); Q.subprocess.Popen.assert_not_called()

    def test_parent_reuses_worker_ownership_lock_and_child_only_environment(self):
        child = Mock(pid=4567, returncode=0); child.poll.return_value = 0
        def popen(argv, **kwargs):
            directory = Path(argv[-1]); stream = io.StringIO(); selected = Q._input(directory)['input']
            self.assertEqual(argv, Q.arguments(directory)); self.assertTrue(kwargs['start_new_session'])
            self.assertEqual(S.worker(directory, build=self.build, reader=self.reader(directory, selected), stdout=stream), 0)
            kwargs['stdout'].write(stream.getvalue().encode()); kwargs['stdout'].flush(); return child
        before = dict(os.environ)
        with patch.object(Q.subprocess, 'Popen', side_effect=popen) as launch, \
                patch.object(Q.N, '_process_start', return_value='fake worker'):
            public = Q.compare_counts('smoke.fixture_v1', str(OLD), str(HEAD))
        self.assertEqual(launch.call_count, 1); self.assertEqual(os.environ, before)
        self.assertTrue(Q.query_closed(self.state / public['attempt_token']))
        with Q._lock(), self.assertRaisesRegex(Q.N.NotebookError, 'LAB_QUERY_BUSY'):
            Q.snapshots('smoke.fixture_v1')

    def test_worker_handshake_routes_snapshot_owner_without_assist(self):
        directory = self.directory(request()); value = Q._input(directory)
        Q.N._write(directory / 'intent.json', dict(schema_version=1, profile=Q.PROFILE, argv=Q.arguments(directory),
            input_sha256=hashlib.sha256(Q.N._canonical(value)).hexdigest()))
        Q.N._write(directory / 'status.json', dict(schema_version=1, state='running', pid=4567, pgid=4567,
            process_start='fake worker', evidence_token=None, exit_code=None, child_exited=False, cleanup_verified=False, error_code=None))
        Q.N._write(directory / 'launch.json', dict(pid=4567, ppid=4566, pgid=4567, process_start='fake worker'))
        assist = Mock()
        with patch.object(Q.os, 'getpid', return_value=4567), patch.object(Q.os, 'getppid', return_value=4566), \
                patch.object(Q.os, 'getpgrp', return_value=4567), patch.object(Q.N, '_process_start', return_value='fake worker'), \
                patch.object(S, 'worker', return_value=0) as worker:
            self.assertEqual(Q.worker(directory, assist=assist), 0)
        worker.assert_called_once_with(directory); assist.assert_not_called()

    def test_render_escapes_provenance_and_rejects_failed_or_fabricated_difference(self):
        _, _, public = self.completed(); public.update(attempt_token='<script>', evidence='<img>')
        html = Q.render_snapshots(public)
        self.assertIn('&lt;script&gt;', html); self.assertNotIn('<img>', html)
        for changed in [dict(public, result='failed'), dict(public, cleanup_verified=False),
                        dict(public, comparison=dict(public['comparison'], difference='0'))]:
            with self.assertRaises(Q.N.NotebookError): Q.render_snapshots(changed)

    def test_template_default_lists_once_explicit_counts_and_stale_results_are_cleared(self):
        cells = [cell for cell in json.loads(R.SNAPSHOT_TEMPLATE.read_bytes())['cells'] if cell['cell_type'] == 'code']
        self.assertEqual(len(cells), 4)
        for cell in cells:
            self.assertEqual(cell['outputs'], []); self.assertIsNone(cell['execution_count'])
            compile(''.join(cell['source']), '<snapshot-template>', 'exec')
        scope = dict(snapshots=Mock(return_value={'snapshots': snapshots_public()}), compare_counts=Mock(return_value={'comparison': 'fake'}),
            recover_snapshots=Mock(), render_snapshots=Mock(return_value='snapshot'), NotebookError=Q.N.NotebookError,
            display=Mock(), HTML=lambda x: x)
        for cell in cells[1:]: exec(''.join(cell['source']), scope)
        scope['snapshots'].assert_called_once_with('smoke.fixture_v1')
        scope['compare_counts'].assert_not_called(); scope['recover_snapshots'].assert_not_called()
        explicit = ''.join(cells[2]['source']).replace('run_compare = False', 'run_compare = True').replace(
            'left_id = ""', 'left_id = "' + str(OLD) + '"').replace('right_id = ""', 'right_id = "' + str(HEAD) + '"')
        exec(explicit, scope)
        scope['compare_counts'].assert_called_once_with('smoke.fixture_v1', str(OLD), str(HEAD))
        scope['compare_counts'].side_effect = Q.N.NotebookError('LAB_QUERY_FAILED')
        with patch('builtins.print'): exec(explicit, scope)
        self.assertIsNone(scope['comparison_result'])
        scope['compare_counts'].reset_mock(); scope['snapshots'].side_effect = Q.N.NotebookError('LAB_QUERY_FAILED')
        with patch('builtins.print'):
            exec(''.join(cells[1]['source']), scope); exec(explicit, scope)
        self.assertIsNone(scope['listing_result']); self.assertIsNone(scope['comparison_result'])
        scope['compare_counts'].assert_not_called()
        scope['snapshots'].reset_mock()
        exec(''.join(cells[3]['source']).replace('snapshot_attempt = ""', 'snapshot_attempt = "' + TOKEN + '"'), scope)
        scope['recover_snapshots'].assert_called_once_with(TOKEN)
        scope['snapshots'].assert_not_called(); scope['compare_counts'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
