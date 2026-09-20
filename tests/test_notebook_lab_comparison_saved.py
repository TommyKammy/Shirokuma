"""Cold comparison receipts using private fake files and arithmetic fixtures."""
from contextlib import ExitStack
from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.local_experiment.notebook_lab import comparison_saved as S
from tests import test_notebook_lake_training_client as V
from tests.test_mlflow_compare import fitted, request

COMPARISON_TOKEN = 'e' * 32


def make_saved(test):
    original = V.LakeTrainingReviewTests()
    test.addCleanup(original.doCleanups)
    original.setUp()
    lab = original.root / 'lab'
    lab.mkdir(mode=0o700)
    state = lab / 'comparisons'
    state.mkdir(mode=0o700)
    directory = state / COMPARISON_TOKEN
    directory.mkdir(mode=0o700)
    stack = ExitStack()
    test.addCleanup(stack.close)
    stack.enter_context(patch.object(S, 'LAB_STATE', lab))
    stack.enter_context(patch.object(S, 'STATE', state))
    with patch.object(S.C, '_fit', side_effect=fitted):
        value = S.C.compare(request())
    public = dict(schema_version=1, profile=S.B.PROFILE, result='passed', attempt_token=COMPARISON_TOKEN,
                  cleanup_verified=True, comparison=value)
    status = dict(schema_version=1, state='passed', pid=4567, pgid=4567, process_start='old process start',
                  evidence_token=COMPARISON_TOKEN, exit_code=0, child_exited=True, cleanup_verified=True, error_code=None)
    records = {'input.json': request(),
               'intent.json': dict(schema_version=1, profile=S.B.PROFILE, argv=S.B.arguments(directory), request_sha256=S.C.digest(request())),
               'status.json': status,
               'launch.json': dict(pid=4567, ppid=4566, pgid=4567, process_start='old process start'),
               'worker.stdout.json': value, 'result.json': public}
    for name, record in records.items():
        S.N._write(directory / name, record)
    return SimpleNamespace(original=original, directory=directory, records=records, public=public,
                           comparison=value, state=state, lab=lab)


class SavedComparisonTests(unittest.TestCase):
    def setUp(self):
        self.fixture = make_saved(self)

    def snapshot(self):
        f = self.fixture
        paths = [f.directory / name for name in S.FILES]
        paths += [f.original.receipt_path, f.original.workload_path, f.original.artifact, f.original.state / 'store.json']
        return [(path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns, path.stat().st_ino) for path in paths]

    def test_reloads_exact_saved_result_without_fit_process_api_or_writes(self):
        before = self.snapshot()
        with patch.object(S.C, '_fit', side_effect=AssertionError('fit prohibited')), \
             patch.object(S.T, '_get', side_effect=AssertionError('HTTP prohibited')), \
             patch.object(S.B.T, 'group_empty', side_effect=AssertionError('historical PID must not be queried')), \
             patch.object(S.B.subprocess, 'run', side_effect=AssertionError('process query prohibited')), \
             patch.object(S.B.subprocess, 'Popen', side_effect=AssertionError('worker prohibited')), \
             patch.object(S.N, '_write', side_effect=AssertionError('write prohibited')):
            value = S.load_saved(COMPARISON_TOKEN)
        self.assertEqual(value, self.fixture.public)
        self.assertEqual(self.snapshot(), before)
        value['comparison']['candidate']['alpha'] = 99.0
        self.assertEqual(S.load_saved(COMPARISON_TOKEN), self.fixture.public)

    def test_bad_token_rejected_before_any_file_read(self):
        with patch.object(S, '_read', side_effect=AssertionError('file read')) as read:
            for token in (None, True, '', '../x', COMPARISON_TOKEN.upper(), COMPARISON_TOKEN + '\n'):
                with self.subTest(token=token), self.assertRaises(S.SavedComparisonError):
                    S.load_saved(token)
            read.assert_not_called()

    def test_only_strict_passed_terminal_receipt_is_accepted(self):
        f = self.fixture
        changes = [{'state': 'running'}, {'state': 'unknown'}, {'state': 'failed'}, {'cleanup_verified': False},
                   {'child_exited': False}, {'exit_code': True}, {'schema_version': True}, {'pid': None},
                   {'pgid': 7777}, {'process_start': None}, {'error_code': 'LAB_COMPARISON_FAILED'},
                   {'evidence_token': 'f' * 32}]
        for change in changes:
            S.N._write(f.directory / 'status.json', dict(f.records['status.json'], **change), replace=True)
            with self.subTest(change=change), self.assertRaises(S.SavedComparisonError):
                S.load_saved(COMPARISON_TOKEN)
        S.N._write(f.directory / 'status.json', f.records['status.json'], replace=True)
        self.assertEqual(S.load_saved(COMPARISON_TOKEN), f.public)

    def test_request_intent_launch_worker_and_public_are_all_bound(self):
        f = self.fixture
        cases = [('input.json', dict(request(), alpha=11.0)),
                 ('intent.json', dict(f.records['intent.json'], request_sha256='0' * 64)),
                 ('intent.json', dict(f.records['intent.json'], schema_version=True)),
                 ('launch.json', dict(f.records['launch.json'], ppid=True)),
                 ('launch.json', dict(f.records['launch.json'], pid=7777)),
                 ('result.json', dict(f.public, attempt_token='f' * 32)),
                 ('result.json', dict(f.public, extra=True))]
        altered = deepcopy(f.comparison)
        altered['holdout'][0]['candidate_prediction'] += 1
        cases.append(('worker.stdout.json', altered))
        for name, value in cases:
            S.N._write(f.directory / name, value, replace=True)
            with self.subTest(name=name), self.assertRaises(S.SavedComparisonError):
                S.load_saved(COMPARISON_TOKEN)
            S.N._write(f.directory / name, f.records[name], replace=True)

    def test_copy_to_different_comparison_token_cannot_reuse_old_receipts(self):
        copied = self.fixture.state / ('f' * 32)
        copied.mkdir(mode=0o700)
        for name, value in self.fixture.records.items():
            S.N._write(copied / name, value)
        with self.assertRaises(S.SavedComparisonError):
            S.load_saved(copied.name)

    def test_saved_source_model_is_revalidated(self):
        self.fixture.original.artifact.write_bytes(b'{}')
        with self.assertRaises(S.SavedComparisonError):
            S.load_saved(COMPARISON_TOKEN)

    def test_missing_oversized_nonprivate_or_linked_files_rejected(self):
        path = self.fixture.directory / 'input.json'
        original = path.read_bytes()
        for mutation in ('missing', 'size', 'mode', 'symlink', 'hardlink'):
            if mutation == 'missing': path.unlink()
            if mutation == 'size': path.write_bytes(b' ' * (S.C.MAX_INPUT_BYTES + 1))
            if mutation == 'mode': path.chmod(0o644)
            if mutation == 'symlink':
                path.unlink()
                path.symlink_to(self.fixture.original.workload_path)
            linked = path.with_name('another-link')
            if mutation == 'hardlink': os.link(path, linked)
            with self.subTest(mutation=mutation), self.assertRaises(S.SavedComparisonError):
                S.load_saved(COMPARISON_TOKEN)
            if linked.exists(): linked.unlink()
            if path.exists() or path.is_symlink(): path.unlink()
            with path.open('xb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(original)

    def test_changed_bytes_or_replaced_identity_after_validation_is_rejected(self):
        f = self.fixture
        validate = S.C.validate_result
        for replace in (False, True):
            def changed(value, request):
                result = validate(value, request)
                path = f.directory / 'status.json'
                if replace:
                    other = path.with_name('replacement')
                    S.N._write(other, f.records['status.json'])
                    os.replace(other, path)
                else:
                    path.write_bytes(b'{}')
                return result
            with patch.object(S.C, 'validate_result', side_effect=changed), self.assertRaises(S.SavedComparisonError):
                S.load_saved(COMPARISON_TOKEN)
            S.N._write(f.directory / 'status.json', f.records['status.json'], replace=True)

    def test_first_cancellation_survives_recheck_failure(self):
        first = KeyboardInterrupt('first')
        def interrupted(*args):
            (self.fixture.directory / 'status.json').write_bytes(b'{}')
            raise first
        with patch.object(S.C, 'validate_result', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt) as caught:
                S.load_saved(COMPARISON_TOKEN)
        self.assertIs(caught.exception, first)

    def test_file_read_cancellation_survives_close_error(self):
        first = KeyboardInterrupt('first')
        close = os.close
        def broken(fd):
            close(fd)
            raise OSError('later close error')
        with patch.object(S.os, 'read', side_effect=first), patch.object(S.os, 'close', side_effect=broken):
            with self.assertRaises(KeyboardInterrupt) as caught:
                S._read(self.fixture.directory / 'input.json', S.C.MAX_INPUT_BYTES)
        self.assertIs(caught.exception, first)


if __name__ == '__main__':
    unittest.main()
