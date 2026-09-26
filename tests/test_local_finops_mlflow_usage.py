from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.finops import host_storage as H, workspace_usage as W
from tools.local_experiment.finops import contract as C, render as HTML, history as HISTORY
from test_local_finops_contract import NOW
from test_local_finops_process_report import report, processes
from test_local_finops_run import host_usage_complete, mlflow_usage_complete, storage_complete, docker_storage_complete


class MLflowUsageTests(unittest.TestCase):
    def test_fixed_db_sidecars_and_artifacts_are_logical_metadata_only(self):
        with tempfile.TemporaryDirectory(dir=H.ROOT/'tmp/local-experiment') as temp:
            root=Path(temp)/'store';root.mkdir(mode=0o700)
            for name,data in (('tracking.sqlite',b'db'),('tracking.sqlite-wal',b'wal'),
                              ('tracking.sqlite-shm',b'shm')):(root/name).write_bytes(data)
            art=root/'artifacts';art.mkdir();nested=art/'old-run';nested.mkdir()
            (nested/'model.zip').write_bytes(b'archive')
            def scan():
                return W.measure(root,device=root.stat().st_dev,inode=root.stat().st_ino,
                                 required_root_entries=(('tracking.sqlite','file'),('artifacts','directory')))
            observed=scan()
            self.assertEqual(observed,{'used_bytes':'15','regular_file_count':4,'directory_count':2})
            (root/'tracking.sqlite-wal').unlink()
            self.assertEqual(scan()['used_bytes'],'12')
            (root/'tracking.sqlite').unlink()
            with self.assertRaisesRegex(W.UsageError,'SCOPE_INVALID'): scan()
            (root/'tracking.sqlite').write_bytes(b'db')
            art.rename(root/'saved-artifacts')
            with self.assertRaisesRegex(W.UsageError,'SCOPE_INVALID'): scan()

    def test_sidecar_change_between_passes_never_publishes_partial_sum(self):
        with tempfile.TemporaryDirectory(dir=H.ROOT/'tmp/local-experiment') as temp:
            root=Path(temp)/'store';root.mkdir(mode=0o700)
            (root/'tracking.sqlite').write_bytes(b'db');(root/'artifacts').mkdir()
            sidecar=root/'tracking.sqlite-wal';sidecar.write_bytes(b'wal')
            original=W._pass;attempts=[]
            def mutate(*args):
                value=original(*args);attempts.append(1)
                if len(attempts)==1:sidecar.write_bytes(b'updated wal')
                return value
            with patch.object(W,'_pass',side_effect=mutate):
                with self.assertRaisesRegex(W.UsageError,'TREE_CHANGED'):
                    W.measure(root,device=root.stat().st_dev,inode=root.stat().st_ino,
                              required_root_entries=(('tracking.sqlite','file'),('artifacts','directory')))

    def test_v7_contract_v6_compat_and_mlflow_failure_independent_of_lab(self):
        legacy=host_usage_complete();self.assertEqual(H.validate(legacy),legacy)
        current=mlflow_usage_complete();self.assertEqual(H.validate(current),current)
        old=report(processes())
        def build(host):
            return C.build_report(old['components'],old['disks'],old['inference'],observed_at=NOW,
                local_processes=processes(),storage=storage_complete(),
                docker_storage=docker_storage_complete(),host_storage=host)
        v6=build(legacy);v7=build(current)
        self.assertEqual((v6['schema_version'],v7['schema_version']),(6,7))
        self.assertEqual(C.validate_public(v7),v7)
        html=HTML.render(v7);self.assertIn('100 bytes（論理・通常ファイル）',html)
        history=HISTORY.render(HISTORY.parse(json.dumps([v6,v7]).encode()))
        self.assertIn('MLflow store logical regular-file bytes: 100',history)
        self.assertNotIn('MLflow store logical regular-file bytes: 100',HTML.render(v6))
        for field,value in (('used_bytes',True),('used_bytes','-1'),('used_bytes','01'),
                            ('regular_file_count',True),('usage_state','not_measured')):
            invalid=deepcopy(current);invalid['components'][0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(H.HostStorageError):H.validate(invalid)
        wrong=deepcopy(v7);wrong['schema_version']=6
        with self.assertRaises(C.FinOpsError):C.validate_public(wrong)
        failed=deepcopy(current);failed['components'][0].update(usage_state='unknown',usage_reason='TREE_CHANGED',
            used_bytes=None,regular_file_count=None,directory_count=None)
        value=build(H.validate(failed))
        self.assertEqual(value['result'],'partial')
        self.assertEqual(value['host_storage']['components'][1]['usage_state'],'observed')
        self.assertIn('取得中にディレクトリが変化',HTML.render(value))

    def test_mlflow_failure_preserves_reference_and_lab_can_still_measure(self):
        backend=Mock();backend.now.return_value=NOW
        backend.snapshot.return_value={'owner_sha256':'a'*64,'directory_identity':{'device':'1','inode':'42'}}
        backend.measure_mlflow.side_effect=W.UsageError('TREE_CHANGED')
        backend.measure_workspace.return_value={'used_bytes':'0','regular_file_count':0,'directory_count':0}
        rows=H.collect(backend,measure=True,measure_mlflow=True)['components']
        self.assertEqual((rows[0]['state'],rows[0]['usage_state'],rows[0]['usage_reason']),
                         ('observed','unknown','TREE_CHANGED'))
        self.assertEqual((rows[1]['state'],rows[1]['usage_state'],rows[1]['used_bytes']),
                         ('observed','observed','0'))
        self.assertEqual(rows[2]['usage_state'],'not_measured')
        backend.measure_mlflow.assert_called_once_with(1,42)
        backend.measure_workspace.assert_called_once_with(1,42)


if __name__=='__main__':unittest.main()
