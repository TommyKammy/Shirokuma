from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.finops import host_storage as H, workspace_usage as W
from tools.local_experiment.finops import contract as C, render as HTML, history as HISTORY, run as R
from test_local_finops_contract import NOW
from test_local_finops_process_report import report, processes
from test_local_finops_run import (mlflow_usage_complete, storage_complete,
                                  docker_storage_complete)


def serving_usage_complete():
    previous=mlflow_usage_complete()
    rows=[]
    for item in previous['components']:
        base={key:item[key] for key in H.unknown(item['component'],'READ_FAILED')}
        row=H.measured_row(base,version=4)
        if item['component']=='model-serving':
            row.update(usage_state='observed',usage_reason=None,used_bytes='0',
                       regular_file_count=0,directory_count=0,
                       usage_started_at='2026-09-20T00:00:20Z',usage_observed_at=NOW)
        else:
            row.update(**{key:item[key] for key in ('usage_state','usage_reason','used_bytes',
                       'regular_file_count','directory_count','usage_started_at','usage_observed_at')})
        rows.append(row)
    return H.validate(dict(schema_version=4,components=rows,limits=H.SERVING_LIMITS[:]))


def public(host):
    old=report(processes())
    return C.build_report(old['components'],old['disks'],old['inference'],observed_at=NOW,
        local_processes=processes(),storage=storage_complete(),
        docker_storage=docker_storage_complete(),host_storage=host)


class ServingUsageTests(unittest.TestCase):
    def test_empty_home_is_zero_without_invented_model_marker(self):
        with tempfile.TemporaryDirectory(dir=H.ROOT/'tmp/local-experiment') as temp:
            home=Path(temp)/'home';home.mkdir(mode=0o700)
            self.assertEqual(W.measure(home,device=home.stat().st_dev,inode=home.stat().st_ino),
                             {'used_bytes':'0','regular_file_count':0,'directory_count':0})
            nested=home/'.cache';nested.mkdir();(nested/'archived').write_bytes(b'old')
            with (home/'sparse').open('wb') as file:file.truncate(4096)
            self.assertEqual(W.measure(home,device=home.stat().st_dev,inode=home.stat().st_ino),
                             {'used_bytes':'4099','regular_file_count':2,'directory_count':1})
            link=home/'alias';link.symlink_to(nested/'archived')
            with self.assertRaisesRegex(W.UsageError,'SCOPE_INVALID'):
                W.measure(home,device=home.stat().st_dev,inode=home.stat().st_ino)

    def test_v8_contract_v7_compat_render_and_reject_injected_old_bytes(self):
        old=mlflow_usage_complete();self.assertEqual(H.validate(old),old)
        v8=serving_usage_complete();self.assertEqual(v8['components'][2]['used_bytes'],'0')
        current=public(v8);previous=public(old)
        self.assertEqual((previous['schema_version'],current['schema_version']),(7,8))
        self.assertEqual(C.validate_public(current),current)
        html=HTML.render(current);self.assertIn('Serving HOME',html)
        self.assertIn('0 bytes（論理・通常ファイル）',html)
        history=HISTORY.render(HISTORY.parse(json.dumps([previous,current]).encode()))
        self.assertIn('Serving HOME logical regular-file bytes: 0',history)
        self.assertNotIn('Serving HOME logical regular-file bytes: 0',HTML.render(previous))
        changed=deepcopy(previous);changed['host_storage']['components'][2]['used_bytes']='0'
        with self.assertRaises((C.FinOpsError,H.HostStorageError)):C.validate_public(changed)
        changed=deepcopy(current);changed['schema_version']=7
        with self.assertRaises((C.FinOpsError,H.HostStorageError)):C.validate_public(changed)
        for field,value in (('used_bytes','01'),('used_bytes',True),('regular_file_count',True),
                            ('usage_observed_at','2026-09-20T00:00:22Z')):
            changed=deepcopy(v8);changed['components'][2][field]=value
            with self.subTest(field=field),self.assertRaises(H.HostStorageError):H.validate(changed)

    def test_serving_failure_keeps_valid_reference_and_independent_rows(self):
        backend=Mock();backend.now.return_value=NOW
        backend.snapshot.return_value={'owner_sha256':'a'*64,'directory_identity':{'device':'1','inode':'42'}}
        backend.measure_workspace.return_value={'used_bytes':'16','regular_file_count':2,'directory_count':1}
        backend.measure_mlflow.return_value={'used_bytes':'100','regular_file_count':2,'directory_count':1}
        backend.measure_serving.side_effect=W.UsageError('TREE_CHANGED')
        host=H.collect(backend,measure=True,measure_mlflow=True,measure_serving=True)
        self.assertEqual([row['usage_state'] for row in host['components']],
                         ['observed','observed','unknown'])
        self.assertEqual((host['components'][2]['state'],host['components'][2]['usage_reason'],
                          host['components'][2]['used_bytes']),('observed','TREE_CHANGED',None))
        self.assertEqual(public(host)['result'],'partial')
        backend.measure_serving.assert_called_once_with(1,42)
        before=backend.snapshot.return_value
        after=deepcopy(before);after['owner_sha256']='b'*64
        with self.assertRaisesRegex(H.HostStorageError,'IDENTITY_CHANGED'):
            H.project('model-serving',before,after,request_started_at=NOW,observed_at=NOW)

    def test_parent_failure_and_worker_alarm_publish_unknown_v8_not_old_schema(self):
        backend=Mock();backend.now.return_value=NOW
        backend.host_storage.side_effect=RuntimeError('PRIVATE')
        backend.component.side_effect=RuntimeError('not running')
        backend.disk.side_effect=RuntimeError('not running')
        backend.inference.side_effect=RuntimeError('not running')
        backend.local_processes.side_effect=RuntimeError('not running')
        backend.storage.side_effect=RuntimeError('not running')
        backend.docker_storage.side_effect=RuntimeError('not running')
        value=R.collect(backend)
        self.assertEqual((value['schema_version'],value['host_storage']['schema_version']),(9,5))
        self.assertTrue(all(row['usage_state']=='unknown' for row in value['host_storage']['components']))
        with patch.object(H,'collect',side_effect=H.WorkerTimeout()),patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(H.main(['--measure-serving-home']),0)
        self.assertEqual(json.loads(output.getvalue())['schema_version'],4)


if __name__=='__main__':unittest.main()
