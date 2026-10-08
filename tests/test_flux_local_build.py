import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.local_experiment import flux_local_build as B


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.directory.chmod(0o700)
        self.evidence = B.Evidence(self.directory)

    def test_save_exclusive_private_and_symlink_rejection(self):
        path = self.directory/'record'
        B.save(path, {'test': True})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            B.save(path, b'replacement')
        link = self.directory/'link'
        link.symlink_to(path)
        with self.assertRaises(FileExistsError):
            B.save(link, b'replacement')

    def test_environment_is_private_and_drops_credentials(self):
        with mock.patch.dict(os.environ, {'GITHUB_TOKEN': 'fixture', 'GOPROXY': 'unsafe',
                                         'GOENV': 'unsafe', 'HTTP_PROXY': 'unsafe'}):
            env = self.evidence.environment(create=True)
        self.assertNotIn('GITHUB_TOKEN', env)
        self.assertNotIn('HTTP_PROXY', env)
        self.assertEqual(env['GOENV'], 'off')
        self.assertEqual(env['GOPROXY'], 'https://proxy.golang.org')
        for key in ('HOME', 'GOCACHE', 'GOMODCACHE', 'GOPATH', 'TMPDIR'):
            self.assertTrue(Path(env[key]).is_relative_to(self.directory))
        with self.assertRaises(FileExistsError):
            self.evidence.environment(create=True)

    def test_usage_tolerates_only_vanished_entries(self):
        B.save(self.directory/'file', b'abc')
        st = (self.directory/'file').stat()
        self.assertEqual(B.usage(self.directory), max(3, st.st_blocks*512))
        with mock.patch.object(Path, 'lstat', side_effect=FileNotFoundError):
            self.assertEqual(B.usage(self.directory), 0)
        with mock.patch.object(Path, 'lstat', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                B.usage(self.directory)

    def test_scope_mode_and_symlink_reject(self):
        self.directory.chmod(0o755)
        with self.assertRaises(RuntimeError):
            B.Evidence(self.directory)
        self.directory.chmod(0o700)
        link = self.directory/'link'
        link.symlink_to(self.directory)
        with self.assertRaises(RuntimeError):
            B.Evidence(link)

    def test_budget_rejects_without_launch(self):
        self.evidence.environment(create=True)
        with mock.patch.object(B, 'usage', return_value=7*B.GIB), mock.patch.object(B.subprocess, 'Popen') as launch:
            with self.assertRaises(RuntimeError):
                self.evidence.command('blocked', ['go', 'version'])
            launch.assert_not_called()
        self.assertFalse((self.directory/'blocked.started.json').exists())

    def test_command_cannot_leave_scope(self):
        self.evidence.environment(create=True)
        with self.assertRaises(RuntimeError):
            self.evidence.command('outside', ['go', 'version'], cwd=self.directory.parent)

    def test_inspection_reserve_cannot_remove_hard_limit(self):
        for reserve in (0, 255*1024**2, 2*B.GIB):
            with self.assertRaises(ValueError):
                B.budget(self.directory, reserve)

    def test_known_exit_during_guard_failure_is_preserved_no_replay(self):
        import json
        self.evidence.environment(create=True)
        process = mock.Mock(returncode=0)
        process.poll.side_effect = [None, 0]
        with mock.patch.object(B, 'budget', side_effect=[None, RuntimeError('hold')]), \
             mock.patch.object(B.subprocess, 'Popen', return_value=process) as launch:
            with self.assertRaises(RuntimeError):
                self.evidence.command('guard-race', ['go', 'version'])
        launch.assert_called_once()
        receipt = json.loads((self.directory/'guard-race.finished.json').read_text())
        self.assertEqual(receipt['returncode'], 0)
        self.assertEqual(receipt['disposition'], 'succeeded')
        self.assertEqual(receipt['observation_error'], 'RuntimeError')
        with mock.patch.object(B, 'budget'), mock.patch.object(B.subprocess, 'Popen') as launch:
            with self.assertRaises(FileExistsError):
                self.evidence.command('guard-race', ['go', 'version'])
            launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
