import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from tools.local_experiment import capacity as C


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.root.chmod(0o700)

    def snapshot(self, used, free):
        with mock.patch.object(C, '_measure', return_value=used), \
             mock.patch.object(C.os, 'fstatvfs', return_value=mock.Mock(f_bavail=free, f_frsize=1)):
            return C.observe(self.root)

    def test_hard_and_soft_usage_bounds_are_separate_observations(self):
        for used, hard, bulk in ((0, False, True), (7*C.GIB-1, False, True),
                                 (7*C.GIB, False, False), (8*C.GIB-1, False, False),
                                 (8*C.GIB, True, False), (8*C.GIB+1, True, False)):
            with self.subTest(used=used):
                self.assertEqual(self.snapshot(used, 30*C.GIB), dict(used_bytes=used,
                    free_bytes=30*C.GIB, hard_limit_exceeded=hard, bulk_writes_allowed=bulk))

    def test_free_space_hard_and_writer_margins_unchanged(self):
        for free, hard, bulk in ((16*C.GIB-1, True, False), (16*C.GIB, False, False),
                                 (17*C.GIB-1, False, False), (17*C.GIB, False, True)):
            with self.subTest(free=free):
                value = self.snapshot(0, free)
                self.assertIs(value['hard_limit_exceeded'], hard)
                self.assertIs(value['bulk_writes_allowed'], bulk)

    def test_sum_each_regular_entry_using_max_not_max_of_totals(self):
        (self.root/'nested').mkdir(mode=0o700)
        files = [self.root/'logical', self.root/'nested/allocated']
        for p in files:
            p.write_bytes(b'fixture')
        original = C.os.stat
        def fake(name, **kwargs):
            if name in ('logical', 'allocated'):
                return mock.Mock(st_uid=os.getuid(), st_mode=stat.S_IFREG | 0o600,
                    st_size=10000 if name == 'logical' else 1,
                    st_blocks=1 if name == 'logical' else 30)
            return original(name, **kwargs)
        with mock.patch.object(C.os, 'stat', side_effect=fake):
            self.assertEqual(C.observe(self.root)['used_bytes'], 10000 + 30*512)

    def test_real_sparse_file_and_hard_links_are_conservatively_charged(self):
        file = self.root/'sparse'
        with file.open('wb') as stream:
            stream.truncate(10*1024**2)
        os.link(file, self.root/'second-entry')
        st = file.stat()
        self.assertEqual(C.observe(self.root)['used_bytes'], 2*max(st.st_size, st.st_blocks*512))

    def test_observe_keeps_contents_modes_mtime_and_entries(self):
        file = self.root/'retained'
        file.write_bytes(b'original failure record')
        file.chmod(0o600)
        before = (file.read_bytes(), file.stat(), self.root.stat(), sorted(self.root.iterdir()))
        C.observe(self.root)
        self.assertEqual(file.read_bytes(), before[0])
        for current, old in ((file.stat(), before[1]), (self.root.stat(), before[2])):
            self.assertEqual((current.st_mode, current.st_mtime_ns, current.st_size),
                             (old.st_mode, old.st_mtime_ns, old.st_size))
        self.assertEqual(sorted(self.root.iterdir()), before[3])

    def test_root_private_owned_directory_required(self):
        self.root.chmod(0o755)
        with self.assertRaises(C.CapacityError):
            C.observe(self.root)
        self.root.chmod(0o700)
        with mock.patch.object(C.os, 'getuid', return_value=os.getuid()+1):
            with self.assertRaises(C.CapacityError):
                C.observe(self.root)
        file = self.root/'file'
        file.touch()
        for path in (file, self.root/'missing'):
            with self.assertRaises(OSError):
                C.observe(path)

    def test_root_and_descendant_symlinks_never_followed(self):
        (self.root/'alias').symlink_to(self.root)
        with self.assertRaises(OSError):
            C.observe(self.root/'alias')
        with self.assertRaises(C.CapacityError):
            C.observe(self.root)

    def test_special_or_foreign_entries_reject(self):
        (self.root/'entry').touch()
        for mode, uid in ((stat.S_IFIFO | 0o600, os.getuid()),
                          (stat.S_IFREG | 0o600, os.getuid()+1)):
            with mock.patch.object(C.os, 'stat', return_value=mock.Mock(st_mode=mode, st_uid=uid)):
                with self.assertRaises(C.CapacityError):
                    C.observe(self.root)

    def test_only_vanished_child_is_tolerated_not_denied_observation(self):
        (self.root/'entry').touch()
        with mock.patch.object(C.os, 'stat', side_effect=FileNotFoundError):
            self.assertEqual(C.observe(self.root)['used_bytes'], 0)
        with mock.patch.object(C.os, 'stat', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                C.observe(self.root)
        with mock.patch.object(C.os, 'scandir', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                C.observe(self.root)

    def test_free_space_observation_failure_never_grants_writes(self):
        with mock.patch.object(C.os, 'fstatvfs', side_effect=OSError):
            with self.assertRaises(OSError):
                C.observe(self.root)


if __name__ == '__main__':
    unittest.main()
