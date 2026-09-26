import json
import os
from pathlib import Path
import platform
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from tools.local_experiment.finops import linux_volume_supervisor as S


class SupervisorProtocolTests(unittest.TestCase):
    def test_private_directory_accepts_real_path_and_rejects_unsafe_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'receipts';root.mkdir(mode=0o700)
            fd=S._record_dir(root)
            try:self.assertEqual(os.fstat(fd).st_ino,root.stat().st_ino)
            finally:os.close(fd)
            root.chmod(0o755)
            with self.assertRaises(S.SupervisorError):S._record_dir(root)

    def test_failed_receipt_write_removes_only_its_temporary_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'receipts';root.mkdir(mode=0o700)
            fd=S._record_dir(root)
            try:
                S._write(fd,'one.json',{'state':'registered'},initial=True)
                with patch.object(S.os,'replace',side_effect=OSError('test-only')):
                    with self.assertRaises(OSError):S._write(fd,'one.json',{'state':'running'})
                self.assertFalse((root/'one.json.next').exists())
                self.assertEqual(json.loads((root/'one.json').read_text())['state'],'registered')
                with patch.object(S.os,'fsync',side_effect=OSError('test-only')):
                    with self.assertRaises(OSError):S._write(fd,'two.json',{'state':'registered'},initial=True)
                self.assertFalse((root/'two.json').exists())
            finally:os.close(fd)

    def test_missing_or_nonterminal_receipt_never_accepts_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'receipts';root.mkdir(mode=0o700)
            operation=str(uuid.uuid4());boot='01234567-89ab-cdef-0123-456789abcdef'
            with self.assertRaisesRegex(S.SupervisorError,'CLEANUP_UNVERIFIED'):
                S.verified_receipt(root,operation_id=operation,worker_digest='a'*64,boot_id=boot)
            record=root/(operation+'.json')
            record.symlink_to(root/'missing')
            with self.assertRaisesRegex(S.SupervisorError,'CLEANUP_UNVERIFIED'):
                S.verified_receipt(root,operation_id=operation,worker_digest='a'*64,boot_id=boot)
            record.unlink()
            record.write_text(json.dumps({'operation_id':operation,'worker_digest':'a'*64,
                'boot_id':boot,'state':'running','cleanup_verified':False,
                'result':{'logical_regular_file_bytes':'100'}}));record.chmod(0o600)
            with self.assertRaisesRegex(S.SupervisorError,'CLEANUP_UNVERIFIED'):
                S.verified_receipt(root,operation_id=operation,worker_digest='a'*64,boot_id=boot)

    def test_result_rejects_partial_or_forged_numeric_values(self):
        good={'logical_regular_file_bytes':'0','regular_file_count':0,'directory_count':0}
        self.assertEqual(S._result(good),good)
        for value in ('-1','01',True,str(2**63)):
            bad=dict(good,logical_regular_file_bytes=value)
            with self.subTest(value=value),self.assertRaises(S.SupervisorError):S._result(bad)
        bad=dict(good,regular_file_count=True)
        with self.assertRaises(S.SupervisorError):S._result(bad)

    @unittest.skipIf(platform.system()=='Linux','Linux supervisor fixture tests cover native path')
    def test_macos_rejects_before_record_or_child(self):
        with patch.object(S.os,'fork') as fork:
            with self.assertRaisesRegex(S.SupervisorError,'UNSUPPORTED_PLATFORM'):
                S.run_fixture(lambda:None,operation_id=str(uuid.uuid4()),
                              worker_digest='a'*64,record_dir=Path('/missing'))
        fork.assert_not_called()


@unittest.skipUnless(platform.system()=='Linux','Linux-only supervisor fixture')
class SupervisorLinuxFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'records';self.root.mkdir(mode=0o700)

    def test_reap_and_private_record_before_accepting_bytes(self):
        operation=str(uuid.uuid4())
        result=S.run_fixture(lambda:{'logical_regular_file_bytes':'42','regular_file_count':1,
                                     'directory_count':0},operation_id=operation,
                             worker_digest='a'*64,record_dir=self.root)
        self.assertEqual(result['state'],'complete')
        self.assertTrue(result['cleanup_verified'])
        self.assertEqual(result['result']['logical_regular_file_bytes'],'42')
        saved=self.root/(operation+'.json')
        self.assertEqual(json.loads(saved.read_text()),result)
        self.assertEqual(S.verified_receipt(self.root,operation_id=operation,
            worker_digest='a'*64,boot_id=result['boot_id'])['logical_regular_file_bytes'],'42')
        self.assertEqual(saved.stat().st_mode & 0o777,0o600)
        with self.assertRaises(FileExistsError):
            S.run_fixture(lambda:None,operation_id=operation,worker_digest='a'*64,
                          record_dir=self.root)

    def test_timeout_reaps_exact_child_without_bytes(self):
        operation=str(uuid.uuid4())
        with patch.object(S,'SCAN_DEADLINE',0.02),patch.object(S,'TERM_AT',0.05),\
             patch.object(S,'KILL_AT',0.1),patch.object(S,'END_AT',0.5):
            result=S.run_fixture(lambda:(time.sleep(1),{})[1],operation_id=operation,
                                 worker_digest='a'*64,record_dir=self.root)
        self.assertEqual(result['state'],'unknown')
        self.assertTrue(result['cleanup_verified'])
        self.assertIsNone(result['result'])
        with self.assertRaisesRegex(S.SupervisorError,'CLEANUP_UNVERIFIED'):
            S.verified_receipt(self.root,operation_id=operation,
                               worker_digest='a'*64,boot_id=result['boot_id'])
        with self.assertRaises(ChildProcessError):os.waitpid(result['child_pid'],os.WNOHANG)

    def test_record_registration_failure_cannot_start_worker_callable(self):
        operation=str(uuid.uuid4());marker=self.root/'called'
        def worker():
            marker.write_text('unsafe')
            return {'logical_regular_file_bytes':'0','regular_file_count':0,'directory_count':0}
        original=S._write;calls=[]
        def failing(*args,**kwargs):
            calls.append(1)
            if len(calls)==2:raise OSError('test-only')
            return original(*args,**kwargs)
        with patch.object(S,'_write',side_effect=failing):
            with self.assertRaises(OSError):
                S.run_fixture(worker,operation_id=operation,worker_digest='a'*64,
                              record_dir=self.root)
        self.assertFalse(marker.exists())
        saved=json.loads((self.root/(operation+'.json')).read_text())
        self.assertEqual(saved['state'],'unknown')
        self.assertTrue(saved['cleanup_verified'])

    def test_term_resistant_worker_reaches_kill_and_is_reaped(self):
        operation=str(uuid.uuid4())
        def resistant():
            import signal
            signal.signal(signal.SIGTERM,signal.SIG_IGN)
            time.sleep(1)
            return {'logical_regular_file_bytes':'1','regular_file_count':1,'directory_count':0}
        with patch.object(S,'SCAN_DEADLINE',0.02),patch.object(S,'TERM_AT',0.05),\
             patch.object(S,'KILL_AT',0.1),patch.object(S,'END_AT',0.5):
            result=S.run_fixture(resistant,operation_id=operation,worker_digest='a'*64,
                                 record_dir=self.root)
        self.assertEqual(result['state'],'unknown')
        self.assertTrue(result['cleanup_verified'])
        self.assertIsNone(result['result'])


if __name__=='__main__':unittest.main()
