from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.finops import workspace_usage as W, host_storage as H
from test_local_finops_contract import NOW
from test_local_finops_run import host_storage_complete, host_usage_complete, storage_complete, docker_storage_complete
from tools.local_experiment.finops import contract as C, render as HTML, history as HISTORY
from test_local_finops_process_report import report, processes
import json


class WorkspaceUsageTests(unittest.TestCase):
    def root(self):
        temp=tempfile.TemporaryDirectory(dir=H.ROOT/'tmp/local-experiment')
        root=Path(temp.name)/'workspace';root.mkdir(mode=0o700)
        return temp,root

    def test_exact_logical_bytes_nested_hidden_archive_empty_sparse(self):
        temp,root=self.root()
        with temp:
            (root/'a.ipynb').write_bytes(b'abc')
            (root/'.checkpoint').write_bytes(b'1234')
            (root/'old.zip').write_bytes(b'zip')
            (root/'empty').touch()
            nested=root/'nested';nested.mkdir();(nested/'item').write_bytes(b'12345')
            sparse=root/'sparse'
            with sparse.open('wb') as f:f.truncate(8192)
            value=W.measure(root,device=root.stat().st_dev,inode=root.stat().st_ino)
            self.assertEqual(value,{'used_bytes':str(3+4+3+5+8192),'regular_file_count':6,'directory_count':1})

    def test_empty_zero_and_empty_files_not_directory_usage(self):
        temp,root=self.root()
        with temp:
            self.assertEqual(W.measure(root,device=root.stat().st_dev,inode=root.stat().st_ino),
                             {'used_bytes':'0','regular_file_count':0,'directory_count':0})
            (root/'zero').touch();(root/'sub').mkdir()
            self.assertEqual(W.measure(root,device=root.stat().st_dev,inode=root.stat().st_ino),
                             {'used_bytes':'0','regular_file_count':1,'directory_count':1})

    def test_links_special_ownership_device_and_root_mode_rejected(self):
        temp,root=self.root()
        with temp:
            (root/'file').write_bytes(b'x')
            link=root/'link';link.symlink_to('file')
            with self.assertRaisesRegex(W.UsageError,'SCOPE_INVALID'):W.measure(root,device=root.stat().st_dev,inode=root.stat().st_ino)
            link.unlink();os.link(root/'file',root/'alias')
            with self.assertRaisesRegex(W.UsageError,'SCOPE_INVALID'):W.measure(root,device=root.stat().st_dev,inode=root.stat().st_ino)
            (root/'alias').unlink();(root/'file').unlink();os.mkfifo(root/'pipe')
            with self.assertRaisesRegex(W.UsageError,'SCOPE_INVALID'):W.measure(root,device=root.stat().st_dev,inode=root.stat().st_ino)
            (root/'pipe').unlink()
            with self.assertRaises(W.UsageError):W.measure(root,device=root.stat().st_dev+1,inode=root.stat().st_ino)
            root.chmod(0o755)
            with self.assertRaises(W.UsageError):W.measure(root,device=root.stat().st_dev,inode=root.stat().st_ino)

    def test_same_sum_replacement_and_limits_do_not_publish_partial(self):
        temp,root=self.root()
        with temp:
            (root/'a').write_bytes(b'x');device=root.stat().st_dev;inode=root.stat().st_ino
            original=W._pass;calls=[]
            def changed(*args):
                result=original(*args);calls.append(1)
                if len(calls)==1:
                    (root/'a').unlink();(root/'b').write_bytes(b'x')
                return result
            with patch.object(W,'_pass',side_effect=changed):
                with self.assertRaisesRegex(W.UsageError,'TREE_CHANGED'):W.measure(root,device=device,inode=inode)
            with patch.object(W,'MAX_ENTRIES',0):
                with self.assertRaisesRegex(W.UsageError,'READ_LIMIT'):W.measure(root,device=device,inode=inode)
            with patch.object(W,'MAX_NAME_BYTES',0):
                with self.assertRaisesRegex(W.UsageError,'READ_LIMIT'):W.measure(root,device=device,inode=inode)
            with patch.object(W,'MAX_SECONDS',-1):
                with self.assertRaisesRegex(W.UsageError,'READ_TIMEOUT'):W.measure(root,device=device,inode=inode)

    def test_root_inode_and_ancestor_symlink_redirect_refused(self):
        temp,root=self.root()
        with temp:
            device=root.stat().st_dev;inode=root.stat().st_ino
            old=root.with_name('original');root.rename(old);root.mkdir(mode=0o700)
            with self.assertRaisesRegex(W.UsageError,'IDENTITY_CHANGED'):
                W.measure(root,device=device,inode=inode)
            root.rmdir();root.symlink_to(old,target_is_directory=True)
            with self.assertRaisesRegex(W.UsageError,'SCOPE_INVALID'):
                W.measure(root,device=device,inode=inode)
            root.unlink()
            ancestor=root.parent/'redirect';ancestor.symlink_to(root.parent,target_is_directory=True)
            with self.assertRaisesRegex(W.UsageError,'SCOPE_INVALID'):
                W.measure(ancestor/'original',device=device,inode=inode)

    def test_deadline_checked_after_second_pass_before_publishing_bytes(self):
        temp,root=self.root()
        with temp:
            (root/'a').write_bytes(b'one');device=root.stat().st_dev;inode=root.stat().st_ino
            clock=[0.0]
            original=W._pass
            def delayed(*args):
                result=original(*args);clock[0]+=6.0;return result
            with patch.object(W,'_pass',side_effect=delayed),patch.object(W.time,'monotonic',side_effect=lambda:clock[0]):
                with self.assertRaisesRegex(W.UsageError,'READ_TIMEOUT'):W.measure(root,device=device,inode=inode)

    def test_usage_failure_preserves_reference_and_unknown_bytes(self):
        backend=Mock();backend.now.return_value=NOW;backend.snapshot.return_value={
            'owner_sha256':'a'*64,'directory_identity':{'device':'1','inode':'42'}}
        backend.measure_workspace.side_effect=W.UsageError('SCOPE_INVALID')
        rows=H.collect(backend,measure=True)['components'];lab=rows[1]
        self.assertEqual(lab['state'],'observed');self.assertEqual(lab['usage_state'],'unknown')
        self.assertIsNone(lab['used_bytes']);self.assertEqual(lab['usage_reason'],'SCOPE_INVALID')
        self.assertTrue(all(row['usage_state']=='not_measured' for row in (rows[0],rows[2])))

    def test_v6_contract_rejects_invalid_usage_and_retains_saved_v5(self):
        legacy=host_storage_complete()
        self.assertEqual(H.validate(legacy),legacy)
        v6=host_usage_complete()
        row=v6['components'][1]
        for field,value in (('used_bytes','-1'),('used_bytes',True),('used_bytes','01'),
                            ('used_bytes',2**63),('used_bytes','9223372036854775808'),
                            ('regular_file_count',True),('directory_count',-1),
                            ('usage_observed_at','2026-09-20T00:00:22Z')):
            altered=json.loads(json.dumps(v6));altered['components'][1][field]=value
            with self.subTest(field=field,value=value),self.assertRaises((H.HostStorageError,ValueError)):
                H.validate(altered)
        old=json.loads(json.dumps(legacy));old['components'][1]['used_bytes']='1'
        with self.assertRaises(H.HostStorageError):H.validate(old)
        old=json.loads(json.dumps(legacy));old['components'][1]['usage_state']='observed'
        with self.assertRaises(H.HostStorageError):H.validate(old)
        old=report(processes())
        v5=C.build_report(old['components'],old['disks'],old['inference'],observed_at=NOW,
                          local_processes=processes(),storage=storage_complete(),
                          docker_storage=docker_storage_complete(),host_storage=legacy)
        v6report=C.build_report(old['components'],old['disks'],old['inference'],observed_at=NOW,
                                local_processes=processes(),storage=storage_complete(),
                                docker_storage=docker_storage_complete(),host_storage=v6)
        self.assertEqual(v6report['schema_version'],6);self.assertEqual(C.validate_public(v6report),v6report)
        html=HTML.render(v6report)
        self.assertIn('16 bytes（論理・通常ファイル）',html)
        history=HISTORY.render(HISTORY.parse(json.dumps([v5,v6report]).encode()))
        self.assertIn('Workspace logical regular-file bytes: 16',history)
        self.assertNotIn('Workspace logical regular-file bytes: 16',HTML.render(v5))
        wrong=json.loads(json.dumps(v6report));wrong['schema_version']=5
        with self.assertRaises(C.FinOpsError):C.validate_public(wrong)

    def test_usage_failure_partial_but_other_targets_deliberately_unmeasured(self):
        old=report(processes());v=host_usage_complete();v['components'][1].update(
            usage_state='unknown',usage_reason='TREE_CHANGED',used_bytes=None,
            regular_file_count=None,directory_count=None)
        value=C.build_report(old['components'],old['disks'],old['inference'],observed_at=NOW,
            local_processes=processes(),storage=storage_complete(),docker_storage=docker_storage_complete(),
            host_storage=H.validate(v))
        self.assertEqual(value['result'],'partial');self.assertEqual(C.validate_public(value),value)
        self.assertIn('取得中にディレクトリが変化',HTML.render(value))
        self.assertIn('取得中にディレクトリが変化',HISTORY.render(HISTORY.parse(json.dumps([value,value]).encode())))

    def test_failed_timeout_keeps_reference_with_observed_attempt_window(self):
        v=host_usage_complete();row=v['components'][1]
        row.update(usage_state='unknown',usage_reason='READ_TIMEOUT',used_bytes=None,
                   regular_file_count=None,directory_count=None,
                   request_started_at='2026-09-20T00:00:00Z',
                   usage_started_at='2026-09-20T00:00:05Z',usage_observed_at='2026-09-20T00:00:20Z')
        self.assertEqual(H.validate(v)['components'][1]['state'],'observed')


if __name__=='__main__':unittest.main()
