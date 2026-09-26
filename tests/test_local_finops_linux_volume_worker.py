import os
from pathlib import Path
import platform
import tempfile
import time
import unittest
from unittest.mock import patch

from tools.local_experiment.finops import linux_volume_worker as W


LINUX = platform.system() == 'Linux' and hasattr(os, 'O_NOATIME') and hasattr(os, 'O_PATH')


class LinuxVolumeWorkerContractTests(unittest.TestCase):
    def test_mountinfo_decoding_and_same_device_bind_boundary(self):
        points = W._mountpoints('1 0 8:1 / / rw - ext4 /dev/a rw\n'
                                 '2 1 8:1 /some /fixture\\040root/sub rw - ext4 /dev/a rw\n'
                                 '3 1 8:1 /some /odd\\134name rw - ext4 /dev/a rw\n')
        self.assertIn('/fixture root/sub', points)
        self.assertIn('/odd\\name', points)
        with self.assertRaisesRegex(W.ScanError, 'MOUNT_CHANGED'):
            W._topology(Path('/fixture root'), points)
        W._topology(Path('/fixture'), points)
        with self.assertRaisesRegex(W.ScanError, 'READ_LIMIT'):
            W._mountpoints('a' * (W.MAX_MOUNTINFO + 1))
        with self.assertRaisesRegex(W.ScanError, 'SCOPE_INVALID'):
            W._mountpoints('2 1 8:1 /some /bad\\999 rw - ext4 /dev/a rw\n')
        with self.assertRaisesRegex(W.ScanError, 'SCOPE_INVALID'):
            W._topology(Path('//fixture'), {'/fixture/sub'})

    @unittest.skipIf(LINUX, 'native Linux backend exercised by Linux fixture tests')
    def test_macos_rejects_before_opening_or_enumerating_any_directory(self):
        with patch.object(W.os, 'open') as opened, patch.object(W.os, 'scandir') as enumerated:
            with self.assertRaisesRegex(W.ScanError, 'UNSUPPORTED_PLATFORM'):
                W.measure(Path('/fixture'), device=1, inode=1,
                          allowed_uids=frozenset({0}), allowed_modes=frozenset({0o700}))
        opened.assert_not_called();enumerated.assert_not_called()


@unittest.skipUnless(LINUX, 'Linux O_NOATIME integration fixture required')
class LinuxVolumeWorkerFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'fixture'
        self.root.mkdir(mode=0o700)

    def measure(self):
        s = self.root.stat()
        return W.measure(self.root, device=s.st_dev, inode=s.st_ino,
                         allowed_uids=frozenset({os.getuid()}), allowed_modes=frozenset({0o700}))

    def test_empty_nested_sparse_and_noatime_both_passes(self):
        self.assertEqual(self.measure(), {'logical_regular_file_bytes':'0',
                                         'regular_file_count':0,'directory_count':0})
        child=self.root/'nested';child.mkdir(mode=0o700)
        (child/'.hidden').write_bytes(b'abc')
        with (self.root/'sparse').open('wb') as file:file.truncate(8192)
        old=time.time_ns()-10**12
        os.utime(self.root,ns=(old,old));os.utime(child,ns=(old,old))
        before=(self.root.stat().st_atime_ns,child.stat().st_atime_ns)
        self.assertEqual(self.measure(), {'logical_regular_file_bytes':'8195',
                                         'regular_file_count':2,'directory_count':1})
        self.assertEqual((self.root.stat().st_atime_ns,child.stat().st_atime_ns),before)

    def test_links_special_entries_limits_and_changed_tree_fail_closed(self):
        file=self.root/'file';file.write_bytes(b'data')
        link=self.root/'link';link.symlink_to('file')
        with self.assertRaisesRegex(W.ScanError,'SCOPE_INVALID'):self.measure()
        link.unlink();os.link(file,self.root/'alias')
        with self.assertRaisesRegex(W.ScanError,'SCOPE_INVALID'):self.measure()
        (self.root/'alias').unlink();file.unlink();os.mkfifo(self.root/'fifo')
        with self.assertRaisesRegex(W.ScanError,'SCOPE_INVALID'):self.measure()
        (self.root/'fifo').unlink();(self.root/'a').write_bytes(b'x')
        with patch.object(W,'MAX_ENTRIES',0):
            with self.assertRaisesRegex(W.ScanError,'READ_LIMIT'):self.measure()
        original=W._pass;calls=[]
        def mutate(*args):
            result=original(*args);calls.append(1)
            if len(calls)==1:
                (self.root/'a').unlink();(self.root/'b').write_bytes(b'x')
            return result
        with patch.object(W,'_pass',side_effect=mutate):
            with self.assertRaisesRegex(W.ScanError,'TREE_CHANGED'):self.measure()

    def test_root_inode_replacement_and_nested_mount_boundary(self):
        s=self.root.stat();other=self.root.with_name('other');self.root.rename(other)
        self.root.mkdir(mode=0o700)
        with self.assertRaisesRegex(W.ScanError,'IDENTITY_CHANGED'):
            W.measure(self.root,device=s.st_dev,inode=s.st_ino,
                      allowed_uids=frozenset({os.getuid()}),allowed_modes=frozenset({0o700}))
        with self.assertRaisesRegex(W.ScanError,'MOUNT_CHANGED'):
            W.measure(self.root,device=self.root.stat().st_dev,inode=self.root.stat().st_ino,
                      allowed_uids=frozenset({os.getuid()}),allowed_modes=frozenset({0o700}),
                      mountinfo=f'1 0 8:1 / / rw - ext4 /dev/a rw\n'
                                f'2 1 8:1 /alias {self.root}/nested rw - ext4 /dev/a rw\n')
        with self.assertRaisesRegex(W.ScanError,'SCOPE_INVALID'):
            W.measure(Path('/'+str(self.root)),device=self.root.stat().st_dev,inode=self.root.stat().st_ino,
                      allowed_uids=frozenset({os.getuid()}),allowed_modes=frozenset({0o700}))


if __name__=='__main__':unittest.main()
