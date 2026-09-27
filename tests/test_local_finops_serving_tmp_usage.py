from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

from tools.local_experiment.finops import host_storage as H, workspace_usage as W
from tools.local_experiment.finops import contract as C, render as HTML, history as HISTORY, run as R
from test_local_finops_contract import NOW
from test_local_finops_serving_usage import serving_usage_complete, public


def serving_tmp_usage_complete():
    previous=serving_usage_complete()
    last=H.unknown('model-serving-tmp','READ_FAILED')
    last.update(state='observed',reason=None,owner_sha256='a'*64,
                directory_identity={'device':'1','inode':'42'},
                request_started_at='2026-09-20T00:00:20Z',observed_at=NOW)
    last=H.measured_row(last,version=5)
    last.update(usage_state='observed',usage_reason=None,used_bytes='0',
                regular_file_count=0,directory_count=0,
                usage_started_at='2026-09-20T00:00:20Z',usage_observed_at=NOW)
    return H.validate(dict(schema_version=5,components=previous['components']+[last],
                           limits=H.SERVING_TMP_LIMITS[:]))


class ServingTmpUsageTests(unittest.TestCase):
    def test_tmp_empty_zero_and_hidden_sparse_files_are_logical_only(self):
        with tempfile.TemporaryDirectory(dir=H.ROOT/'tmp/local-experiment') as temp:
            root=Path(temp)/'tmp';root.mkdir(mode=0o700)
            scan=lambda:W.measure(root,device=root.stat().st_dev,inode=root.stat().st_ino)
            self.assertEqual(scan(),{'used_bytes':'0','regular_file_count':0,'directory_count':0})
            (root/'.cache').mkdir();(root/'.cache'/'stale').write_bytes(b'old')
            with (root/'sparse').open('wb') as f:f.truncate(4096)
            self.assertEqual(scan(),{'used_bytes':'4099','regular_file_count':2,'directory_count':1})
            (root/'redirect').symlink_to(root/'.cache'/'stale')
            with self.assertRaisesRegex(W.UsageError,'SCOPE_INVALID'):scan()

    def test_v9_roundtrip_history_and_v8_cannot_gain_tmp_or_relabel_old_home(self):
        old=serving_usage_complete();self.assertEqual(H.validate(old),old)
        current=serving_tmp_usage_complete();self.assertEqual(current['components'][3]['used_bytes'],'0')
        v8,v9=public(old),public(current)
        self.assertEqual((v8['schema_version'],v9['schema_version']),(8,9))
        self.assertEqual(C.validate_public(v9),v9)
        self.assertIn('Model Serving TMP',HTML.render(v9))
        self.assertIn('Model Serving HOME',HTML.render(v9))
        self.assertIn('Model Serving</th>',HTML.render(v8))
        self.assertNotIn('Model Serving TMP',HTML.render(v8))
        history=HISTORY.render(HISTORY.parse(json.dumps([v8,v9]).encode()))
        self.assertIn('Serving TMP logical regular-file bytes: 0',history)
        self.assertIn('Serving HOME logical regular-file bytes: 0',history)
        changed=deepcopy(v8);changed['host_storage']['components'].append(current['components'][3])
        with self.assertRaises((C.FinOpsError,H.HostStorageError)):C.validate_public(changed)
        changed=deepcopy(v9);changed['schema_version']=8
        with self.assertRaises((C.FinOpsError,H.HostStorageError)):C.validate_public(changed)
        for field,value in (('used_bytes','01'),('used_bytes',True),('regular_file_count',True),
                            ('usage_observed_at','2026-09-20T00:00:22Z')):
            changed=deepcopy(current);changed['components'][3][field]=value
            with self.subTest(field=field),self.assertRaises(H.HostStorageError):H.validate(changed)

    def test_tmp_failure_does_not_erase_others_or_turn_missing_into_zero(self):
        backend=Mock();backend.now.return_value=NOW
        backend.snapshot.return_value={'owner_sha256':'a'*64,'directory_identity':{'device':'1','inode':'42'}}
        backend.measure_workspace.return_value={'used_bytes':'16','regular_file_count':2,'directory_count':1}
        backend.measure_mlflow.return_value={'used_bytes':'100','regular_file_count':2,'directory_count':1}
        backend.measure_serving.return_value={'used_bytes':'0','regular_file_count':0,'directory_count':0}
        backend.measure_serving_tmp.side_effect=W.UsageError('TREE_CHANGED')
        host=H.collect(backend,measure=True,measure_mlflow=True,measure_serving=True,measure_serving_tmp=True)
        self.assertEqual([row['usage_state'] for row in host['components']],
                         ['observed','observed','observed','unknown'])
        self.assertEqual((host['components'][3]['state'],host['components'][3]['usage_reason'],
                          host['components'][3]['used_bytes']),('observed','TREE_CHANGED',None))
        self.assertEqual(public(host)['result'],'partial')
        backend.measure_serving_tmp.assert_called_once_with(1,42)
        with patch.object(H,'collect',side_effect=H.WorkerTimeout()),patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(H.main(['--measure-serving-tmp']),0)
        value=json.loads(output.getvalue());self.assertEqual(value['schema_version'],5)
        self.assertEqual(len(value['components']),4)
        self.assertTrue(all(item['used_bytes'] is None for item in value['components']))

    def test_fixed_tmp_owner_config_validation_does_not_start_service(self):
        from tools.local_experiment.mlflow import serving_runtime as S
        with patch.dict(sys.modules,{'tools.local_experiment.notebook_lab.runtime':Mock()}),\
             patch.object(S,'_owner',return_value={'owner':'fixture'}),\
             patch.object(S,'environment',return_value={'HOME':'/other','TMPDIR':'/bad','TMP':'/bad','TEMP':'/bad'}):
            with self.assertRaisesRegex(H.HostStorageError,'SCOPE_INVALID'):
                H.snapshot('model-serving-tmp')


if __name__=='__main__':unittest.main()
