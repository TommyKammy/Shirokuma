import copy
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from tools.local_experiment import flux_minimal_tests as M


class MinimalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root/'tmp/rv').mkdir(parents=True, mode=0o700)

    def plan(self):
        unit = M.P.fixed_unit('index-state', 4096, 4096)
        unit.update(scope=M.SCOPES['index-state'], total_bytes=332271616)
        return dict(decision='ready_for_parent_review', units=[unit], inputs=[], seed=[],
                    retained_bytes=100, free_bytes=100*M.P.GIB)

    def review(self, plan):
        return dict(head='a'*40, plan_sha256=M.binding(plan), unit_id='index-state',
                    deadline_epoch=1791682200,
                    independent_checks={k: True for k in M.PARENT_CHECKS})

    def run_mock(self, plan, review=None, process=None, bound_error=None):
        patches = [mock.patch.object(M, 'inspect', return_value=plan),
                   mock.patch.object(M.subprocess, 'check_output', return_value='a'*40),
                   mock.patch.object(M.subprocess, 'Popen', return_value=process),
                   mock.patch.object(M.time, 'time', return_value=1791670000),
                   mock.patch.object(M, 'terminal_group'),
                   mock.patch.object(M, 'check_bounds', side_effect=bound_error)]
        with patches[0], patches[1], patches[2] as launch, patches[3], patches[4], patches[5]:
            result = M.execute(self.root, plan, review=review or self.review(plan),
                               unit_id='index-state', execute=True)
        return result, launch

    def test_default_zero_call_even_with_review(self):
        with mock.patch.object(M.subprocess, 'Popen') as launch, mock.patch.object(M, 'inspect') as inspect:
            self.assertEqual(M.execute(self.root, {}, review={}), dict(decision='zero_call', calls=0))
            launch.assert_not_called()
            inspect.assert_not_called()

    def test_import_comments_strings_and_aliases(self):
        data = b'''package p
// import "evil.example/comment"
var s = `import "evil.example/string"`
import (
 . "a.example/m"
 alias "b.example/m/sub"
 "fmt"
)
'''
        self.assertEqual(M.imports(data), ['a.example/m', 'b.example/m/sub', 'fmt'])
        self.assertEqual(M.imports(b'package p\nimport `a.example/raw`'), ['a.example/raw'])

    def test_unsupported_embed_and_cgo_hold(self):
        for data in (b'//go:embed foo\npackage p', b'package p\nimport "C"'):
            with self.assertRaises(M.P.PlanError): M.imports(data)

    def test_closure_cycles_longest_module_and_dependency_tests_excluded(self):
        versions = {'a.example/m': 'v1', 'a.example/m/sub': 'v2'}
        files = {'a.example/m': {'p.go': b'package p\nimport "a.example/m/sub"',
                                'p_test.go': b'import "missing.example/test"'},
                 'a.example/m/sub': {'s.go': b'package s\nimport "a.example/m"'}}
        modules, edges = M.closure(['a.example/m'], versions, lambda m, v: files[m])
        self.assertEqual(set(modules), set(versions))
        self.assertEqual(edges['a.example/m/sub'], ['a.example/m'])

    def test_closure_missing_dependency_holds(self):
        with self.assertRaises(M.P.PlanError):
            M.closure(['a.example/m'], {'a.example/m': 'v1'},
                      lambda m, v: {'p.go': b'package p\nimport "missing.example/x"'})

    def zip_fixture(self, member='a.example/m@v1.0.0/p.go', data=b'package p'):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as z: z.writestr(member, data)
        return stream.getvalue(), {('a.example/m', 'v1.0.0'): M.h1({member: data})}

    def test_archive_h1_and_content_tampering(self):
        data, sums = self.zip_fixture()
        self.assertEqual(M.archive_files(data, 'a.example/m', 'v1.0.0', sums), {'p.go': b'package p'})
        altered, _ = self.zip_fixture(data=b'tampered')
        with self.assertRaises(M.P.PlanError): M.archive_files(altered, 'a.example/m', 'v1.0.0', sums)

    def test_toolchain_tampering_fails_before_tar_or_execution(self):
        with mock.patch.object(M.P, 'read_bytes', return_value=b'tampered'), \
                mock.patch.object(M.subprocess, 'Popen') as launch:
            with self.assertRaises(M.P.PlanError): M.tool_manifest(self.root)
            launch.assert_not_called()

    def test_inspector_unknown_input_is_hold_and_never_writes_or_launches(self):
        before = sorted(str(p) for p in self.root.rglob('*'))
        with mock.patch.object(M.subprocess, 'Popen') as launch:
            plan = M.inspect(self.root)
        self.assertEqual(plan['decision'], 'hold')
        self.assertFalse(plan['execution_authorized'])
        self.assertTrue(plan['blockers'])
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob('*')))
        launch.assert_not_called()

    def test_archive_traversal_wrong_prefix_and_duplicate(self):
        for name in ('a.example/m@v1.0.0/../p.go', 'wrong/p.go'):
            data, sums = self.zip_fixture(member=name)
            with self.assertRaises(M.P.PlanError): M.archive_files(data, 'a.example/m', 'v1.0.0', sums)

    def test_requirements_preserve_digest_replacement(self):
        self.assertEqual(M.requirements(b'require (\n a.example/m v1.0.0\n)\n'
                         b'replace a.example/m => a.example/m v1.2.0\n'), {'a.example/m': 'v1.2.0'})

    def test_copy_charge_includes_files_and_directories(self):
        self.assertEqual(M.charge([dict(destination='m/a/f', bytes=4097)]), 3*4096+8192)

    def test_parent_binding_missing_false_extra_and_tamper_never_launch(self):
        plan = self.plan()
        for change in ({'plan_sha256': '0'*64}, {'unit_id': 'signature'},
                       {'independent_checks': True}, {'deadline_epoch': 1791682201}):
            review = dict(self.review(plan), **change)
            with self.subTest(change=change), self.assertRaises(M.P.PlanError):
                self.run_mock(plan, review)
        review = self.review(plan)
        review['independent_checks']['session_terminal_idle'] = False
        with self.assertRaises(M.P.PlanError): self.run_mock(plan, review)
        self.assertFalse((self.root/M.SCOPES['index-state']).exists())

    def test_fresh_input_scope_argv_budget_tampering_rejects(self):
        original = self.plan()
        for key, value in (('argv', ['go', 'build']), ('total_bytes', 0), ('scope', 'tmp/old')):
            changed = copy.deepcopy(original); changed['units'][0][key] = value
            with mock.patch.object(M, 'inspect', return_value=original), \
                    mock.patch.object(M.subprocess, 'check_output', return_value='a'*40), \
                    mock.patch.object(M.time, 'time', return_value=1791670000), \
                    self.assertRaises(M.P.PlanError):
                M.execute(self.root, changed, review=self.review(changed), unit_id='index-state', execute=True)

    def test_unknown_reservation_is_not_replayed(self):
        directory = self.root/M.SCOPES['index-state']; directory.mkdir()
        (directory/'reserved.json').write_text('{}')
        with self.assertRaises(FileExistsError): self.run_mock(self.plan())
        self.assertEqual((directory/'reserved.json').read_text(), '{}')

    def test_known_finished_unit_still_cannot_replay(self):
        directory = self.root/M.SCOPES['index-state']; directory.mkdir()
        (directory/'finished.json').write_text(json.dumps({'outcome': {'disposition': 'successful'}}))
        with self.assertRaises(FileExistsError): self.run_mock(self.plan())

    def test_other_unknown_reservation_holds_disjoint_unit(self):
        directory = self.root/M.SCOPES['index-digest']; directory.mkdir()
        (directory/'reserved.json').write_text('{}')
        with self.assertRaises(FileNotFoundError): self.run_mock(self.plan())
        self.assertFalse((self.root/M.SCOPES['index-state']).exists())

    def test_prior_unit_receipt_content_tampering_holds_other_unit(self):
        directory = self.root/M.SCOPES['index-digest']; directory.mkdir()
        (directory/'stdout').write_bytes(b''); (directory/'stderr').write_bytes(b'')
        (directory/'finished.json').write_text(json.dumps(dict(owned_group_terminal=True,
                                                            stdout_sha256='0'*64)))
        plan = self.plan()
        other = copy.deepcopy(plan['units'][0]); other.update(id='index-digest', scope=M.SCOPES['index-digest'])
        plan['units'].append(other)
        with self.assertRaises(M.P.PlanError): self.run_mock(plan)
        self.assertFalse((self.root/M.SCOPES['index-state']).exists())

    def test_prelaunch_bounds_hold_preserves_reservation(self):
        with self.assertRaises(M.P.PlanError):
            self.run_mock(self.plan(), bound_error=M.P.PlanError('capacity'))
        directory = self.root/M.SCOPES['index-state']
        self.assertTrue((directory/'reserved.json').exists())
        self.assertTrue((directory/'held.json').exists())
        self.assertFalse((directory/'started.json').exists())

    def test_mocked_executor_fixed_offline_argv_and_success_receipt(self):
        data = b'\n'.join(json.dumps(e).encode() for e in [
            dict(Action='pass', Package=M.TARGET, Test='TestWithIndex'),
            dict(Action='pass', Package=M.TARGET)])
        process = mock.Mock(pid=12345, returncode=0)
        process.poll.return_value = 0
        readers = []
        for payload in (data, b''):
            reader, writer = os.pipe()
            os.write(writer, payload)
            os.close(writer)
            readers.append(os.fdopen(reader, 'rb'))
        process.stdout, process.stderr = readers
        result, launch = self.run_mock(self.plan(), process=process)
        self.assertEqual(result['disposition'], 'successful')
        args, kwargs = launch.call_args
        self.assertEqual(args[0][0], str(self.root/M.P.TOOL))
        self.assertEqual(args[0][-1], './internal/index')
        self.assertTrue(kwargs['start_new_session'])
        self.assertEqual(kwargs['env']['GOPROXY'], 'off')
        self.assertEqual(kwargs['env']['GOTOOLCHAIN'], 'local')
        self.assertNotIn('OPENAI_API_KEY', kwargs['env'])
        self.assertTrue((self.root/M.SCOPES['index-state']/'finished.json').exists())
        with self.assertRaises(FileExistsError): self.run_mock(self.plan())

    def test_running_guard_failure_stops_only_owned_process_and_holds(self):
        process = mock.Mock(pid=12345)
        process.poll.return_value = None
        readers = []
        for _ in range(2):
            reader, writer = os.pipe(); os.close(writer)
            readers.append(os.fdopen(reader, 'rb'))
        process.stdout, process.stderr = readers
        with mock.patch.object(M, 'terminate_owned') as stop, self.assertRaises(M.P.PlanError):
            self.run_mock(self.plan(), process=process,
                          bound_error=[None, M.P.PlanError('running ceiling')])
        stop.assert_called_once_with(process)
        directory = self.root/M.SCOPES['index-state']
        self.assertTrue((directory/'started.json').exists())
        self.assertTrue((directory/'held.json').exists())
        self.assertFalse((directory/'finished.json').exists())

    def test_wall_deadline_terminates_owned_child_without_replay(self):
        process = mock.Mock(pid=12345); process.poll.return_value = None
        readers = []
        for _ in range(2):
            reader, writer = os.pipe(); os.close(writer)
            readers.append(os.fdopen(reader, 'rb'))
        process.stdout, process.stderr = readers
        with mock.patch.object(M.time, 'monotonic', side_effect=[0, 0, 601]), \
                mock.patch.object(M, 'terminate_owned') as stop, self.assertRaises(M.P.PlanError):
            self.run_mock(self.plan(), process=process)
        stop.assert_called_once_with(process)
        self.assertFalse((self.root/M.SCOPES['index-state']/'finished.json').exists())

    def test_receipt_limit_is_checked_before_writing_pipe_bytes(self):
        process = mock.Mock(pid=12345); process.poll.return_value = None
        readers = []
        for payload in (b'oversize', b''):
            reader, writer = os.pipe(); os.write(writer, payload); os.close(writer)
            readers.append(os.fdopen(reader, 'rb'))
        process.stdout, process.stderr = readers
        with mock.patch.object(M, 'RECEIPTS', 65537), \
                mock.patch.object(M, 'terminate_owned') as stop, self.assertRaises(M.P.PlanError):
            self.run_mock(self.plan(), process=process)
        stop.assert_called_once_with(process)
        self.assertEqual((self.root/M.SCOPES['index-state']/'stdout').read_bytes(), b'')

    def test_selector_covers_write_line_regression(self):
        import re
        self.assertTrue(re.fullmatch(M.P.SELECTORS['index-digest'], 'Test_writeLine'))
        self.assertFalse(re.fullmatch(M.P.SELECTORS['index-state'], 'Test_writeLine'))

    def test_owned_group_term_and_kill_only_created_child(self):
        process = mock.Mock(pid=12345)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('fixed', 5), 0]
        with mock.patch.object(M.os, 'getpgid', return_value=12345), mock.patch.object(M.os, 'killpg') as kill:
            M.terminate_owned(process)
            self.assertEqual(kill.call_args_list, [mock.call(12345, signal.SIGTERM),
                                                  mock.call(12345, signal.SIGKILL)])

    def test_foreign_group_is_never_signalled(self):
        process = mock.Mock(pid=12345); process.poll.return_value = None
        with mock.patch.object(M.os, 'getpgid', return_value=999), mock.patch.object(M.os, 'killpg') as kill:
            with self.assertRaises(M.P.PlanError): M.terminate_owned(process)
            kill.assert_not_called()

    def test_terminal_group_requires_no_remaining_descendants(self):
        process = mock.Mock(pid=12345)
        with mock.patch.object(M.os, 'killpg', side_effect=ProcessLookupError): M.terminal_group(process)
        with mock.patch.object(M.os, 'killpg'), self.assertRaises(M.P.PlanError): M.terminal_group(process)

    def test_unknown_empty_wrong_package_or_selector_never_success(self):
        unit = self.plan()['units'][0]
        for package, name in ((M.TARGET, 'TestWithIndex'), ('wrong', 'TestWithIndex'),
                              (M.TARGET, 'TestwriteLine')):
            data = b'\n'.join(json.dumps(e).encode() for e in [
                dict(Action='pass', Package=package, Test=name), dict(Action='pass', Package=package)])
            result = M.unit_outcome(unit, 0, data)
            self.assertEqual(result['disposition'], 'successful' if package == M.TARGET and
                             name == 'TestWithIndex' else 'unknown')
        self.assertEqual(M.unit_outcome(unit, 0, b'')['disposition'], 'unknown')

    def test_ambiguous_termination_receipt_is_unknown(self):
        data = b'{}'
        receipt = dict(returncode=-15, disposition='terminated_hold_no_replay', finished_at=1,
                       stdout_sha256=M.sha(data), termination_error='ownership unknown')
        self.assertEqual(M.P.classify(receipt, data)['disposition'], 'unknown')

    def test_real_bound_accounting_and_exact_bound_holds(self):
        directory = self.root/'scope'; directory.mkdir()
        (directory/'c').mkdir(); (directory/'t').mkdir()
        unit = dict(total_bytes=M.RESERVE+1)
        with self.assertRaises(M.P.PlanError): M.check_bounds(self.root, 'scope', unit, 0)
        unit['total_bytes'] = M.RESERVE+100000
        with mock.patch.object(M.os, 'statvfs', return_value=mock.Mock(f_bavail=100*M.P.GIB, f_frsize=1)):
            M.check_bounds(self.root, 'scope', unit, 0)
            with self.assertRaises(M.P.PlanError):
                M.check_bounds(self.root, 'scope', unit, 16*M.P.GIB-M.RESERVE)

    def test_exclusive_private_receipt_and_symlink_rejected(self):
        M.exclusive_json(self.root, 'tmp/rv/receipt.json', {'a': 1})
        path = self.root/'tmp/rv/receipt.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError): M.exclusive_json(self.root, 'tmp/rv/receipt.json', {})
        (self.root/'tmp/rv/link').symlink_to(path)
        with self.assertRaises(FileExistsError): M.exclusive_json(self.root, 'tmp/rv/link', {})


if __name__ == '__main__':
    unittest.main()
