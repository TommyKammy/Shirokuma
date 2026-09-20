"""Saved training discovery using real private-file validation, no services."""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.mlflow import predict as P, training_runs as R
from tools.local_experiment.notebook import client as N, training as T
from tests.test_notebook_training_client import fixture_workload as synthetic_workload
from tests.test_notebook_lake_training_client import fixture_workload as lake_workload

STORE='00000000-0000-0000-0000-000000000001'


class Cancel(BaseException): pass


class TrainingRunsTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve(); self.state=self.root/'tracking'; self.state.mkdir(mode=0o700)
        self.workloads={'synthetic':synthetic_workload(),'iceberg':lake_workload()}
        values={'STATE':self.state,'DATASET_SHA256':self.workloads['synthetic']['hashes']['dataset_sha256'],
                'SPLIT_SHA256':self.workloads['synthetic']['hashes']['split_sha256'],
                'LAKE_SPLIT_SHA256':self.workloads['iceberg']['hashes']['split_sha256']}
        for name,value in values.items():
            item=patch.object(T,name,value); item.start(); self.addCleanup(item.stop)
        item=patch.object(T.urllib.request,'build_opener',side_effect=AssertionError('HTTP forbidden'))
        item.start(); self.addCleanup(item.stop)
        self.write(self.state/'store.json',dict(schema_version=1,owner='shirokuma-local-mlflow',mlflow_version='3.16.1',
            store_id=STORE,backend_uri='sqlite:///'+str(self.state/'store/tracking.sqlite'),
            artifact_root=str(self.state/'store/artifacts'),url=N.URL))
        self.put('synthetic','1'*32,'2'*32,1200)
        self.put('iceberg','3'*32,'4'*32,1300)
        self.put('iceberg','5'*32,'6'*32,1400)

    def mkdir(self,path):
        path.mkdir(parents=True,exist_ok=True,mode=0o700)
        for item in (path,*path.parents):
            if item==self.state or self.state in item.parents: item.chmod(0o700)

    def write(self,path,value):
        self.mkdir(path.parent)
        path.write_bytes(N._canonical(value)); path.chmod(0o600)

    def put(self,kind,token,run,finished):
        profile=T.profile(kind); workload=self.workloads[kind]
        receipt=dict(schema_version=1,result='verified',phase='verified',token=token,store_id=STORE,
            source_sha256={name:'0'*64 for name in profile['source_names']},experiment_id='4' if kind=='iceberg' else '3',
            run_id=run,started_ms=1000,training_finished_ms=1100,finished_ms=finished,
            workload_sha256=hashlib.sha256(N._canonical(workload)).hexdigest(),failure_code=None)
        path=self.state/profile['folder']/token
        self.write(path/'result.json',receipt); self.write(path/'workload.json',workload)
        self.write(self.state/'store/artifacts'/profile['namespace']/run/'artifacts/safe-projection.json',workload)

    def fingerprints(self):
        result={}
        for p in self.state.rglob('*'):
            st=p.lstat()
            result[str(p.relative_to(self.state))]=(st.st_mode,st.st_ino,st.st_mtime_ns,st.st_ctime_ns,
                st.st_size,hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and not p.is_symlink() else None)
        return result

    def invoke(self,args=None):
        output=io.StringIO()
        with patch.object(R.sys,'stdout',output): code=R.main([] if args is None else args)
        raw=output.getvalue()
        self.assertEqual(len(raw.splitlines()),1)
        return code,json.loads(raw)

    def test_both_profiles_multiple_lake_order_and_safe_projection_are_read_only(self):
        before=self.fingerprints()
        with patch.object(N,'_write',side_effect=AssertionError('writes forbidden')):
            report=R.collect()
        self.assertEqual(self.fingerprints(),before)
        self.assertEqual(set(report),{'schema_version','profile','result','runs','unavailable','counts','error_code','scope'})
        self.assertEqual(report['counts'],{'candidates':3,'verified':3,'unavailable':0})
        self.assertEqual([x['training_token'] for x in report['runs']],['5'*32,'3'*32,'1'*32])
        self.assertEqual(report['unavailable'],[])
        for row in report['runs']:
            public=T.load_saved_review(row['training_token'],kind=row['kind'])
            self.assertEqual(set(row),{'kind','training_token','identity','test_metrics','baseline_metrics','lake_source'})
            self.assertEqual(row['test_metrics'],public['metrics']['test'])
            self.assertEqual(row['baseline_metrics'],public['metrics']['baseline_test'])
            self.assertEqual(row['identity']['run_id'],public['identity']['run_id'])
            self.assertEqual(row['lake_source'],public.get('lake_source'))
        raw=R.canonical(report)
        for key in (b'"dataset"',b'"model"',b'"coefficients"',b'"holdout"',b'"rows"'):
            self.assertNotIn(key,raw)

    def test_selected_training_token_goes_directly_to_existing_predictor(self):
        row=R.collect()['runs'][0]
        request={'schema_version':1,'rows':[{'x1':0,'x2':0,'x3':0},{'x1':1,'x2':0,'x3':0}]}
        result=P.predict(row['training_token'],request)
        self.assertEqual(result['identity']['attempt_token'],row['training_token'])
        self.assertEqual(result['identity']['run_id'],row['identity']['run_id'])
        self.assertEqual(result['lake_source'],row['lake_source'])
        expected=self.workloads['iceberg']['model']
        self.assertEqual(result['predictions'],[expected['intercept'],expected['intercept']+expected['coefficients'][0]])

    def test_partial_invalid_failed_and_symlink_candidates_are_explicit(self):
        folder=self.state/'lake-training'
        self.mkdir(folder/('7'*32))
        self.write(folder/('8'*32)/'result.json',{'secret':'DO_NOT_PUBLISH'})
        self.write(folder/('8'*32)/'workload.json',{})
        self.put('iceberg','9'*32,'a'*32,1500)
        path=folder/('9'*32)/'result.json'; value=json.loads(path.read_text()); value['result']='failed'; self.write(path,value)
        (folder/('b'*32)).symlink_to(self.root/'unrelated',target_is_directory=True)
        code,report=self.invoke()
        self.assertEqual(code,0); self.assertEqual(report['result'],'complete')
        self.assertEqual(report['counts'],{'candidates':7,'verified':3,'unavailable':4})
        self.assertEqual([x['status'] for x in report['unavailable']],['partial','invalid','invalid','invalid'])
        self.assertNotIn('DO_NOT_PUBLISH',R.canonical(report).decode())
        self.assertTrue(all(set(x)=={'kind','training_token','status','reason'} for x in report['unavailable']))

    def test_missing_artifact_and_invalid_candidate_mode_are_unavailable(self):
        (self.state/'store/artifacts/iceberg-training'/('4'*32)/'artifacts/safe-projection.json').unlink()
        (self.state/'lake-training'/('5'*32)).chmod(0o755)
        report=R.collect()
        self.assertEqual(report['counts']['verified'],1)
        self.assertEqual(report['counts']['unavailable'],2)
        self.assertTrue(all(x['reason']=='TRAINING_RUN_INVALID' for x in report['unavailable']))

    def test_unknown_names_fail_whole_without_filename_disclosure(self):
        self.write(self.state/'training'/'DO_NOT_PUBLISH.json',{})
        with patch.object(T,'load_saved_review') as loader:
            code,report=self.invoke()
        self.assertEqual(code,1); self.assertEqual(report['error_code'],'TRAINING_RUNS_INVENTORY_INVALID')
        self.assertEqual(report['runs'],[]); self.assertEqual(report['unavailable'],[])
        self.assertNotIn('DO_NOT_PUBLISH',R.canonical(report).decode()); loader.assert_not_called()

    def test_token_collision_across_profiles_refuses_before_loader(self):
        self.mkdir(self.state/'training'/('3'*32))
        with patch.object(T,'load_saved_review') as loader: code,report=self.invoke()
        self.assertEqual(code,1); self.assertEqual(report['error_code'],'TRAINING_RUNS_TOKEN_COLLISION')
        loader.assert_not_called()

    def test_wrong_root_mode_symlink_and_absent_state_are_fixed_failures(self):
        folder=self.state/'training'; folder.chmod(0o755)
        self.assertEqual(self.invoke()[1]['error_code'],'TRAINING_RUNS_ROOT_INVALID')
        folder.chmod(0o700)
        alias=self.root/'alias'; alias.symlink_to(self.state,target_is_directory=True)
        with patch.object(T,'STATE',alias):
            self.assertEqual(self.invoke()[1]['error_code'],'TRAINING_RUNS_ROOT_INVALID')
        with patch.object(T,'STATE',self.root/'absent'):
            self.assertEqual(self.invoke()[1]['error_code'],'TRAINING_RUNS_ROOT_INVALID')

    def test_namespace_change_discards_earlier_verified_rows(self):
        load=T.load_saved_review; calls=[]
        def changed(token,**kw):
            public=load(token,**kw); calls.append(token)
            if len(calls)==1: self.write(self.state/'lake-training'/token/'new-intent.json',{})
            return public
        with patch.object(T,'load_saved_review',side_effect=changed): code,report=self.invoke()
        self.assertEqual(code,1); self.assertEqual(report['error_code'],'TRAINING_RUNS_INVENTORY_CHANGED')
        self.assertEqual(report['runs'],[]); self.assertIsNone(report['counts']['candidates'])

    def test_total_candidate_cap_refuses_before_loading(self):
        for i in range(126): self.mkdir(self.state/'lake-training'/format(i+100,'032x'))
        with patch.object(T,'load_saved_review') as loader: code,report=self.invoke()
        self.assertEqual(code,1); self.assertEqual(report['error_code'],'TRAINING_RUNS_ENTRY_LIMIT')
        loader.assert_not_called(); self.assertEqual(report['runs'],[])
        self.assertEqual(report['counts'],{'candidates':None,'verified':0,'unavailable':0})

    def test_public_size_cap_never_returns_partial_success(self):
        with patch.object(R,'MAX_PUBLIC_BYTES',512): code,report=self.invoke()
        self.assertEqual(code,1); self.assertEqual(report['error_code'],'TRAINING_RUNS_PUBLIC_LIMIT')
        self.assertEqual(report['runs'],[])

    def test_tie_order_is_token_order_and_cli_has_no_arbitrary_options(self):
        path=self.state/'lake-training'/('3'*32)/'result.json'
        value=json.loads(path.read_text()); value['finished_ms']=1400; self.write(path,value)
        self.assertEqual([x['training_token'] for x in R.collect()['runs'][:2]],['3'*32,'5'*32])
        with patch.object(R,'collect') as collect: code,report=self.invoke(['--state','SECRET'])
        self.assertEqual(code,1); self.assertEqual(report['error_code'],'TRAINING_RUNS_ARGUMENTS_INVALID')
        self.assertNotIn('SECRET',R.canonical(report).decode()); collect.assert_not_called()

    def test_first_cancellation_survives_inventory_and_output_failure(self):
        first=Cancel('first')
        class Broken:
            def write(self,*args): raise OSError('secondary')
        with patch.object(T,'load_saved_review',side_effect=first),patch.object(R.sys,'stdout',Broken()):
            with self.assertRaises(Cancel) as caught: R.main([])
        self.assertIs(caught.exception,first)

    def test_absent_profile_folders_in_owned_root_are_explicit_empty(self):
        empty=self.root/'empty'; empty.mkdir(mode=0o700)
        with patch.object(T,'STATE',empty): report=R.collect()
        self.assertEqual(report['result'],'complete')
        self.assertEqual(report['counts'],{'candidates':0,'verified':0,'unavailable':0})


if __name__=='__main__': unittest.main()
