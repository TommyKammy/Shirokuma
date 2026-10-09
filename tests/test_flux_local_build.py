import os
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

from tools.local_experiment import flux_local_build as B


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name).resolve()
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

    def archive(self, name='go/bin/go', kind=tarfile.REGTYPE):
        path = self.directory/'archive.tar'
        with tarfile.open(path, 'w') as tar:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.mode = 0o755
            member.size = 3 if kind == tarfile.REGTYPE else 0
            tar.addfile(member, io.BytesIO(b'abc') if member.size else None)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_checked_archive_private_modes_and_no_overwrite(self):
        path, digest = self.archive()
        dest = self.directory/'toolchain'
        B.extract_verified_archive(path, dest, digest, path.stat().st_size, executable=True)
        self.assertEqual((dest/'go/bin/go').stat().st_mode & 0o777, 0o700)
        self.assertEqual((dest/'go').stat().st_mode & 0o777, 0o700)
        self.assertEqual((dest/'go/bin').stat().st_mode & 0o777, 0o700)
        with self.assertRaises(FileExistsError):
            B.extract_verified_archive(path, dest, digest)

    def test_archive_checksum_and_size_before_writes(self):
        path, digest = self.archive()
        dest = self.directory/'output'
        for sha, size in [('0'*64, None), (digest, 1)]:
            with self.assertRaises(RuntimeError):
                B.extract_verified_archive(path, dest, sha, size)
            self.assertFalse(dest.exists())

    def test_archive_rejects_links_and_traversal_before_writes(self):
        for name, kind in [('../escape', tarfile.REGTYPE), ('/escape', tarfile.REGTYPE),
                           ('link', tarfile.SYMTYPE), ('hardlink', tarfile.LNKTYPE)]:
            with self.subTest(name=name, kind=kind):
                path, digest = self.archive(name, kind)
                dest = self.directory/'output'
                with self.assertRaises(RuntimeError):
                    B.extract_verified_archive(path, dest, digest)
                self.assertFalse(dest.exists())

    def test_archive_source_not_executable(self):
        path, digest = self.archive()
        dest = self.directory/'source'
        B.extract_verified_archive(path, dest, digest)
        self.assertEqual((dest/'go/bin/go').stat().st_mode & 0o777, 0o600)

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
        with mock.patch.object(B.C.os, 'stat', side_effect=FileNotFoundError):
            self.assertEqual(B.usage(self.directory), 0)
        with mock.patch.object(B.C.os, 'stat', side_effect=PermissionError):
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
        with mock.patch.object(B.C, 'observe', return_value=dict(used_bytes=7*B.GIB,
                free_bytes=30*B.GIB, hard_limit_exceeded=False, bulk_writes_allowed=False)), \
                mock.patch.object(B.subprocess, 'Popen') as launch:
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
