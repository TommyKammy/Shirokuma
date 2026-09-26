import errno
import unittest
from unittest.mock import patch

from tools.local_experiment.finops import linux_openat2 as AT


class OpenAt2ContractTests(unittest.TestCase):
    def test_non_linux_rejected_before_syscall(self):
        called=[]
        with patch.object(AT.platform,'system',return_value='Darwin'):
            with self.assertRaises(OSError) as raised:
                AT.open_beneath(3,'file',123,syscall=lambda *args:called.append(args))
        self.assertEqual(raised.exception.errno,errno.ENOSYS)
        self.assertEqual(called,[])

    def test_injected_syscall_has_no_xdev_no_symlink_and_beneath(self):
        calls=[]
        def fake(number,fd,raw,how):
            calls.append((number,fd,raw,how.flags,how.mode,how.resolve))
            return 71
        with patch.object(AT.platform,'system',return_value='Linux'),\
             patch.object(AT.platform,'machine',return_value='aarch64'):
            self.assertEqual(AT.open_beneath(3,'sub/file',123,syscall=fake),71)
            for path in ('/absolute','../parent','sub/../parent','sub//file','file\x00other'):
                with self.subTest(path=path),self.assertRaises(OSError):
                    AT.open_beneath(3,path,123,syscall=fake)
        self.assertEqual(calls,[(AT.SYS_OPENAT2,3,b'sub/file',123,0,
                                 AT.RESOLVE_BENEATH|AT.RESOLVE_NO_SYMLINKS|AT.RESOLVE_NO_XDEV)])

    def test_unsupported_architecture_does_not_fall_back(self):
        with patch.object(AT.platform,'system',return_value='Linux'),\
             patch.object(AT.platform,'machine',return_value='mips'):
            with self.assertRaises(OSError) as raised:
                AT.open_beneath(3,'file',123,syscall=lambda *args:71)
        self.assertEqual(raised.exception.errno,errno.ENOSYS)


if __name__=='__main__':unittest.main()
