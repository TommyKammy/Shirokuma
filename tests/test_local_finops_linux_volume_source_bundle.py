import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.finops import linux_volume_source_bundle as B


class SourceBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()/'sources';self.root.mkdir(mode=0o700)
        for name in B.SOURCES:(self.root/name).write_bytes(('fixture:'+name).encode())

    def test_exact_fixed_source_manifest_is_stable_and_content_sensitive(self):
        first=B.pin(self.root);self.assertEqual(first,B.pin(self.root))
        self.assertEqual([item['name'] for item in first['files']],list(B.SOURCES))
        self.assertEqual(len(first['bundle_digest']),64)
        target=self.root/B.SOURCES[1]
        original=target.read_bytes();target.write_bytes(original.replace(b'fixture',b'altered'))
        self.assertNotEqual(first['bundle_digest'],B.pin(self.root)['bundle_digest'])
        target.write_bytes(original)
        self.assertEqual(first['bundle_digest'],B.pin(self.root)['bundle_digest'])

    def test_missing_symlink_hardlink_special_oversize_and_world_writable_fail(self):
        target=self.root/B.SOURCES[0];target.unlink()
        with self.assertRaises(B.BundleError):B.pin(self.root)
        target.write_bytes(b'x');target.chmod(0o600)
        with self.assertRaises(B.BundleError):B.pin(self.root/'missing')
        target.unlink();target.symlink_to(self.root/B.SOURCES[1])
        with self.assertRaises(B.BundleError):B.pin(self.root)
        target.unlink();target.write_bytes(b'x')
        os.link(target,self.root/'hardlink')
        with self.assertRaises(B.BundleError):B.pin(self.root)
        (self.root/'hardlink').unlink()
        target.chmod(0o666)
        with self.assertRaises(B.BundleError):B.pin(self.root)
        target.chmod(0o600);target.write_bytes(b'x'*(B.MAX_SOURCE+1))
        with self.assertRaises(B.BundleError):B.pin(self.root)
        target.write_bytes(b'')
        with self.assertRaises(B.BundleError):B.pin(self.root)
        target.unlink();os.mkfifo(target)
        with self.assertRaises(B.BundleError):B.pin(self.root)

    def test_root_redirect_and_changed_second_pass_fail_without_digest(self):
        alias=self.root.with_name('alias');alias.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(B.BundleError):B.pin(alias)
        self.root.chmod(0o777)
        with self.assertRaises(B.BundleError):B.pin(self.root)
        self.root.chmod(0o700)
        original=B._read;reads=0
        def changed(fd,name,device):
            nonlocal reads
            result=original(fd,name,device);reads+=1
            if reads==len(B.SOURCES):
                (self.root/B.SOURCES[1]).write_bytes(b'changed on second pass')
            return result
        with patch.object(B,'_read',side_effect=changed):
            with self.assertRaises(B.BundleError):B.pin(self.root)


if __name__=='__main__':unittest.main()
