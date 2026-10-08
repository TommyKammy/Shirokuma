import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.local_experiment import flux_refresh_scan as F


class CollectionTests(unittest.TestCase):
    def test_environment_has_no_host_auth_or_shared_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            with mock.patch.dict(os.environ, {'DOCKER_CONFIG': '/outside', 'TRIVY_USERNAME': 'secret'}):
                env = F.environment(directory)
            self.assertNotIn('TRIVY_USERNAME', env)
            for key in ('HOME', 'DOCKER_CONFIG', 'XDG_CACHE_HOME', 'TMPDIR', 'SYFT_CACHE_DIR'):
                self.assertTrue(Path(env[key]).is_relative_to(directory))
            with self.assertRaises(FileExistsError):
                F.environment(directory)

    def test_outputs_exclusive_and_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'output'
            F.private(p, b'original')
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                F.private(p, b'changed')
            self.assertEqual(p.read_bytes(), b'original')
            link = Path(tmp)/'link'
            link.symlink_to(p)
            with self.assertRaises(OSError):
                F.private(link, b'changed')
            with self.assertRaises(F.F.R.RunnerError):
                F.usage(Path(tmp))

    def test_scan_plan_is_remote_only_pinned_and_single_db(self):
        images = [{'component': 'source-controller', 'platform': 'linux/arm64',
                   'reference': 'ghcr.io/fluxcd/source-controller@sha256:'+'a'*64}]
        plan = list(F.commands(Path('/private/mission'), images))
        self.assertEqual(len(plan), 5)
        scan = plan[3][1]
        self.assertEqual(scan[scan.index('--image-src')+1], 'remote')
        self.assertIn('--skip-db-update', scan)
        self.assertEqual(scan[-1], images[0]['reference'])
        self.assertEqual(plan[0][1][-1], 'ghcr.io/aquasecurity/trivy-db:2')
        images[0]['reference'] = 'ghcr.io/fluxcd/source-controller:latest'
        with self.assertRaises(F.F.R.RunnerError):
            list(F.commands(Path('/private/mission'), images))

    def test_budget_stops_before_hard_limit(self):
        with mock.patch.object(F, 'usage', return_value=7*F.GIB), \
             mock.patch.object(F.shutil, 'disk_usage', return_value=mock.Mock(free=30*F.GIB)):
            with self.assertRaises(F.F.R.RunnerError):
                F.check_budget(Path('/unused'))

    def test_failed_operation_keeps_bytes_and_cannot_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            env = F.environment(directory)
            process = mock.Mock(returncode=1)
            process.poll.return_value = 1
            def launch(argv, **kwargs):
                kwargs['stdout'].write(b'partial scan')
                kwargs['stderr'].write(b'registry unavailable')
                return process
            with mock.patch.object(F.shutil, 'which', return_value='/installed/trivy'), \
                 mock.patch.object(F.subprocess, 'Popen', side_effect=launch) as call:
                with self.assertRaisesRegex(F.F.R.RunnerError, 'REFRESH_OPERATION_FAILED'):
                    F.execute(directory, env, 'scan', ['trivy', 'image'], 'scan.json')
                self.assertEqual((directory/'scan.json').read_bytes(), b'partial scan')
                self.assertIn('failed', (directory/'scan.finished.json').read_text())
                with self.assertRaises(FileExistsError):
                    F.execute(directory, env, 'scan', ['trivy', 'image'], 'scan.json')
                self.assertEqual(call.call_count, 1)
                process.terminate.assert_not_called()

    def test_scanner_explicit_cache_modes_normalized_without_content_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            db = work/'db'
            db.write_bytes(b'retained new bytes')
            db.chmod(0o644)
            changes = F.secure_generated(work)
            self.assertEqual(changes, [{'path': 'db', 'original_mode': '0o644'}])
            self.assertEqual(db.stat().st_mode & 0o777, 0o600)
            self.assertEqual(db.read_bytes(), b'retained new bytes')
            (work/'outside').symlink_to(db)
            with self.assertRaises(F.F.R.RunnerError):
                F.secure_generated(work)


if __name__ == '__main__':
    unittest.main()
