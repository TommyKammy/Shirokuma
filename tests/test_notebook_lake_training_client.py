"""Saved lake-review contract tests using arithmetic data and fake HTTP only."""
from copy import deepcopy
import hashlib
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse
import xml.etree.ElementTree as ET

from tools.local_experiment.notebook import client as N
from tools.local_experiment.notebook import training as T
from tests import test_notebook_training_client as B

Response = B.Response

TOKEN, RUN, SOURCE, QUERY = 'a'*32, 'b'*32, 'c'*32, 'd'*32
STORE = '00000000-0000-0000-0000-000000000001'
TABLE_UUID = '00000000-0000-0000-0000-000000000002'
TABLE = 'iceberg.local_experiment.training_'+SOURCE


def fixture_workload():
    """Orthogonal ±1 data has a closed-form Ridge optimum; no fit dependency."""
    rows=[]
    coefficients=[value*48/49 for value in (2.5,-1.75,0.5)]
    for i in range(64):
        x=[float(1 if (i >> j) & 1 else -1) for j in range(3)]
        rows.append([*x,4.0+2.5*x[0]-1.75*x[1]+0.5*x[2]])
    predictions=[4.0+sum(row[j]*coefficients[j] for j in range(3)) for row in rows]
    def metrics(indices,predictions):
        target=[rows[i][3] for i in indices]; mean=sum(target)/len(target)
        square=sum((a-b)**2 for a,b in zip(target,predictions))
        return dict(mae=sum(abs(a-b) for a,b in zip(target,predictions))/len(target),
                    rmse=math.sqrt(square/len(target)),r2=1-square/sum((a-mean)**2 for a in target))
    dataset=dict(feature_names=['x1','x2','x3'],target_name='target',rows=rows)
    split=dict(seed=1730,train_indices=list(range(48)),test_indices=list(range(48,64)))
    columns=[dict(name=name,type=kind,nullable=True) for name,kind in
             [('row_id','bigint'),('x1','double'),('x2','double'),('x3','double'),('target','double')]]
    typed=[[dict(type='bigint',value=str(i)),*[dict(type='double',value=str(v)) for v in rows[i]]]
           for i in reversed(range(64))]
    observation=dict(schema_version=1,table=TABLE,table_uuid=TABLE_UUID,schema_sha256='e'*64,
                     snapshot_id='12345',columns=columns,rows=typed,row_count=64,fetched_row_count=64,
                     truncated=False,requested_limit=100,ordering='unspecified',rows_sha256=T._digest(typed))
    return dict(schema_version=1,profile='fixed-iceberg-regression-v1',dataset=dataset,split=split,
                params=dict(algorithm='Ridge',alpha=1.0,solver='svd',fit_intercept=True),
                model=dict(coefficients=coefficients,intercept=4.0),baseline=dict(prediction=4.0),
                metrics=dict(train=metrics(range(48),predictions[:48]),test=metrics(range(48,64),predictions[48:]),
                             baseline_test=metrics(range(48,64),[4.0]*16)),
                test_predictions=predictions[48:],hashes=dict(dataset_sha256=T._digest(dataset),split_sha256=T._digest(split)),
                versions=dict(python='3.12.12',numpy='2.5.3',scikit_learn='1.9.1'),
                lake_input=dict(fixture_profile=T.LAKE_FIXTURE_PROFILE,query_evidence_token=QUERY,
                    observation=observation,split_row_ids=dict(train=split['train_indices'][:],test=split['test_indices'][:])))


class LakeTrainingReviewTests(unittest.TestCase):
    # Reuse only transport/file helpers, not the synthetic-specific tests.
    mkdir=B.TrainingReviewTests.mkdir
    write=B.TrainingReviewTests.write
    opener=B.TrainingReviewTests.opener
    open=B.TrainingReviewTests.open
    reset_attempt=B.TrainingReviewTests.reset_attempt

    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve(); self.state=self.root/'tracking'; self.output=self.root/'notebook'
        self.mkdir(self.state); self.mkdir(self.output)
        self.workload=fixture_workload()
        for name,value in [('STATE',self.state),('LAKE_SPLIT_SHA256',self.workload['hashes']['split_sha256'])]:
            changed=patch.object(T,name,value); changed.start(); self.addCleanup(changed.stop)
        changed=patch.dict(os.environ,{T.TOKEN_ENV:TOKEN,T.KIND_ENV:'iceberg','SHIROKUMA_NOTEBOOK_RUN_DIR':str(self.output)})
        changed.start(); self.addCleanup(changed.stop)
        self.receipt=dict(schema_version=1,result='verified',phase='verified',token=TOKEN,store_id=STORE,
            source_sha256={name:'f'*64 for name in T.LAKE_SOURCE_NAMES},experiment_id='4',run_id=RUN,
            started_ms=1000,training_finished_ms=1100,finished_ms=1200,
            workload_sha256=hashlib.sha256(N._canonical(self.workload)).hexdigest(),failure_code=None)
        self.receipt_path=self.state/'lake-training'/TOKEN/'result.json'
        self.workload_path=self.receipt_path.with_name('workload.json')
        self.artifact=self.state/'store/artifacts/iceberg-training'/RUN/'artifacts/safe-projection.json'
        self.save_workload(self.workload)
        self.marker=dict(schema_version=1,owner='shirokuma-local-mlflow',mlflow_version='3.16.1',store_id=STORE,
            backend_uri='sqlite:///'+str(self.state/'store/tracking.sqlite'),artifact_root=str(self.state/'store/artifacts'),url=N.URL)
        self.write(self.state/'store.json',self.marker)
        pairs=lambda values:[dict(key=key,value=value) for key,value in values.items()]
        self.experiment=dict(experiment=dict(experiment_id='4',name=T.LAKE_EXPERIMENT,lifecycle_stage='active',
            artifact_location=(self.state/'store/artifacts/iceberg-training').as_uri(),
            tags=pairs({'shirokuma.owner':T.LAKE_EXPERIMENT,'shirokuma.store_id':STORE})))
        observation=self.workload['lake_input']['observation']
        params=dict(algorithm='Ridge',alpha='1.0',solver='svd',fit_intercept='true',
            **self.workload['hashes'],train_rows='48',test_rows='16',feature_names='x1,x2,x3',split_seed='1730',
            workload='fixed-iceberg-regression-v1',model_artifact='safe-projection.json',
            scope='fixed-iceberg-holdout; not general model quality',version_python='3.12.12',version_numpy='2.5.3',
            version_scikit_learn='1.9.1',lake_table=TABLE,lake_table_uuid=TABLE_UUID,lake_snapshot_id='12345',
            lake_schema_sha256='e'*64,lake_rows_sha256=observation['rows_sha256'],query_evidence_token=QUERY,
            source_token=SOURCE,fixture_profile=T.LAKE_FIXTURE_PROFILE)
        tags={'shirokuma.owner':T.LAKE_EXPERIMENT,'shirokuma.store_id':STORE,'shirokuma.attempt':TOKEN,
            'shirokuma.workload_sha256':self.receipt['workload_sha256'],
            'shirokuma.time_semantics':'actual-local-training-and-tracking','mlflow.runName':'lake-ridge-'+TOKEN[:8]}
        metrics={group+'_'+key:value for group,values in self.workload['metrics'].items() for key,value in values.items()}
        self.run=dict(run=dict(info=dict(run_id=RUN,experiment_id='4',status='FINISHED',lifecycle_stage='active',
            artifact_uri=self.artifact.parent.as_uri(),start_time=1000,end_time=1200),
            data=dict(params=pairs(params),tags=pairs(tags),metrics=pairs(metrics))))
        self.responses=[Response(self.experiment),Response(self.run)]; self.calls=[]
        changed=patch.object(T.urllib.request,'build_opener',side_effect=self.opener)
        changed.start(); self.addCleanup(changed.stop)

    def save_workload(self,value):
        self.receipt['workload_sha256']=hashlib.sha256(N._canonical(value)).hexdigest()
        self.write(self.receipt_path,self.receipt); self.write(self.workload_path,value); self.write(self.artifact,value)

    def public(self):
        return T._project(self.receipt,self.workload,kind='iceberg')

    def assert_refused(self,calls=0):
        with self.assertRaisesRegex(N.NotebookError,'^NOTEBOOK_TRAINING_READ_FAILED$'):
            T.read_training(kind='iceberg')
        self.assertEqual(len(self.calls),calls)
        self.assertFalse((self.output/'training-review.json').exists())

    def test_cold_saved_review_needs_only_two_gets_and_no_pipeline(self):
        before={p:p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        value=T.read_training(kind='iceberg')
        self.assertEqual(value,self.public())
        self.assertEqual(value['profile'],T.LAKE_PROFILE)
        self.assertEqual(value['lake_source'],dict(source_token=SOURCE,fixture_profile=T.LAKE_FIXTURE_PROFILE,
            table=TABLE,table_uuid=TABLE_UUID,snapshot_id='12345',schema_sha256='e'*64,
            rows_sha256=self.workload['lake_input']['observation']['rows_sha256'],query_evidence_token=QUERY,ordering='unspecified'))
        self.assertEqual(len(value['holdout']),16)
        self.assertEqual(self.calls,[N.URL+'/api/2.0/mlflow/experiments/get-by-name?'+urllib.parse.urlencode({'experiment_name':T.LAKE_EXPERIMENT}),
                                     N.URL+'/api/2.0/mlflow/runs/get?run_id='+RUN])
        self.assertEqual([len(self.run['run']['data'][key]) for key in ('params','tags','metrics')],[24,6,9])
        self.assertTrue(all(response.closed for response in self.responses))
        self.assertEqual(before,{p:p.read_bytes() for p in before})
        self.assertEqual(N._parse((self.output/'training-intent.json').read_bytes()),
            dict(schema_version=1,profile=T.LAKE_PROFILE,attempt_token=TOKEN,kind='iceberg'))
        self.assertFalse((self.root/'lake-training').exists())

    def test_kind_or_environment_conflicts_fail_before_intent_and_http(self):
        for kind,environment in [('iceberg','synthetic'),('synthetic','iceberg'),('unknown','unknown'),(True,'iceberg')]:
            with self.subTest(kind=kind),patch.dict(os.environ,{T.KIND_ENV:environment}):
                with self.assertRaises(N.NotebookError): T.read_training(kind=kind)
        self.assertEqual(list(self.output.iterdir()),[]); self.assertEqual(self.calls,[])

    def test_profile_folder_and_receipt_source_allowlist_are_fixed(self):
        for key,value in [('result','failed'),('phase','training'),('schema_version',True),('token',SOURCE),
                          ('failure_code','unknown'),('source_sha256',{name:'f'*64 for name in T.SOURCE_NAMES})]:
            with self.subTest(key=key):
                self.write(self.receipt_path,dict(self.receipt,**{key:value}))
                self.assert_refused(); self.reset_attempt()
        self.write(self.receipt_path,self.receipt)
        with patch.dict(os.environ,{T.KIND_ENV:'synthetic'}),self.assertRaises(N.NotebookError): T.read_training()
        self.assertEqual(self.calls,[])

    def test_lake_typed_rows_are_sorted_into_the_exact_dataset(self):
        self.assertEqual(self.workload['lake_input']['observation']['rows'][0][0]['value'],'63')
        T._workload(self.workload,kind='iceberg')
        bad=deepcopy(self.workload); bad['dataset']['rows'][0][0]+=0.125
        bad['hashes']['dataset_sha256']=T._digest(bad['dataset'])
        self.save_workload(bad); self.assert_refused()

    def test_reader_completeness_types_nulls_bounds_and_original_order_hash(self):
        mutations=[lambda o:o.__setitem__('requested_limit',64),lambda o:o.__setitem__('row_count',True),
            lambda o:o.__setitem__('fetched_row_count',101),lambda o:o.__setitem__('truncated',True),
            lambda o:o.__setitem__('ordering','row_id'),lambda o:o['rows'].reverse(),
            lambda o:o['rows'][0][0].__setitem__('value','62'),lambda o:o['rows'][0][1].__setitem__('value',None),
            lambda o:o['rows'][0][1].__setitem__('value','nan'),lambda o:o['rows'][0][1].__setitem__('value','1000001.0'),
            lambda o:o['columns'][1].__setitem__('type','real')]
        for index,mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                bad=deepcopy(self.workload); mutate(bad['lake_input']['observation'])
                if index != 5: bad['lake_input']['observation']['rows_sha256']=T._digest(bad['lake_input']['observation']['rows'])
                self.save_workload(bad); self.assert_refused(); self.reset_attempt()

    def test_fixed_split_and_split_ids_cannot_drift(self):
        mutations=[lambda w:w['split']['test_indices'].reverse(),
            lambda w:w['split']['train_indices'].__setitem__(0,True),
            lambda w:w['lake_input']['split_row_ids']['train'].__setitem__(0,True),
            lambda w:w['lake_input']['split_row_ids']['test'].reverse(),
            lambda w:w['split']['test_indices'].__setitem__(0,0)]
        for index,mutate in enumerate(mutations):
            bad=deepcopy(self.workload); mutate(bad); bad['hashes']['split_sha256']=T._digest(bad['split'])
            with self.subTest(mutation=index),self.assertRaises(N.NotebookError): T._workload(bad,kind='iceberg')

    def test_train_only_optimum_baseline_predictions_and_nine_metrics(self):
        mutations=[lambda w:w['model']['coefficients'].__setitem__(0,2.5),
            lambda w:w['model'].__setitem__('intercept',4.1),lambda w:w['baseline'].__setitem__('prediction',0.0),
            lambda w:w['test_predictions'].__setitem__(0,0.0)]
        mutations += [lambda w,g=g,k=k:w['metrics'][g].__setitem__(k,w['metrics'][g][k]+0.125)
                      for g in ('train','test','baseline_test') for k in ('mae','rmse','r2')]
        for index,mutate in enumerate(mutations):
            bad=deepcopy(self.workload); mutate(bad)
            with self.subTest(mutation=index),self.assertRaises(N.NotebookError): T._workload(bad,kind='iceberg')

    def test_unknown_keys_nonfinite_and_boolean_numbers_are_rejected(self):
        for key in ('lake_input','hashes','params','dataset','model','versions'):
            bad=deepcopy(self.workload); bad[key]['unknown']=1
            with self.subTest(key=key),self.assertRaises(N.NotebookError): T._workload(bad,kind='iceberg')
        for number in (True,float('nan'),float('inf'),10**400,1_000_001):
            bad=deepcopy(self.workload); bad['model']['intercept']=number
            with self.subTest(number=str(number)),self.assertRaises(N.NotebookError): T._workload(bad,kind='iceberg')

    def test_wrong_experiment_and_artifact_namespace_stop_at_first_get(self):
        for key,value in [('name',T.EXPERIMENT),('experiment_id','3'),('artifact_location','file:///other')]:
            bad=deepcopy(self.experiment); bad['experiment'][key]=value; self.responses[0]=Response(bad)
            self.assert_refused(1); self.reset_attempt()

    def test_every_provenance_parameter_and_owner_tag_is_bound(self):
        keys=('source_token','lake_table','lake_table_uuid','lake_snapshot_id','lake_schema_sha256',
              'lake_rows_sha256','query_evidence_token','fixture_profile')
        for key in keys:
            bad=deepcopy(self.run)
            next(row for row in bad['run']['data']['params'] if row['key']==key)['value']='changed'
            self.responses[1]=Response(bad); self.assert_refused(2); self.reset_attempt()
        bad=deepcopy(self.run)
        next(row for row in bad['run']['data']['tags'] if row['key']=='shirokuma.owner')['value']=T.OWNER
        self.responses[1]=Response(bad); self.assert_refused(2)

    def test_artifact_bytes_and_canonical_newline_are_bound(self):
        self.write(self.artifact,self.artifact.read_bytes()+b' '); self.assert_refused()

    def test_live_saved_file_change_during_get_prevents_publication(self):
        self.responses[1].on_read=lambda:self.write(self.receipt_path,dict(self.receipt,finished_ms=1201))
        self.assert_refused(2)

    def test_environment_kind_or_token_change_during_get_prevents_publication(self):
        for key,replacement in ((T.KIND_ENV,'synthetic'),(T.TOKEN_ENV,'1'*32)):
            with self.subTest(key=key),patch.dict(os.environ):
                self.responses[1].on_read=lambda:os.environ.__setitem__(key,replacement)
                self.assert_refused(2); self.reset_attempt()

    def test_first_cancellation_survives_close_and_source_recheck(self):
        first=KeyboardInterrupt('first')
        def cancel():
            self.artifact.chmod(0o644)
            raise first
        def close_failed(): raise RuntimeError('close')
        self.responses[0].on_read=cancel; self.responses[0].on_close=close_failed
        with self.assertRaises(KeyboardInterrupt) as caught: T.read_training(kind='iceberg')
        self.assertIs(caught.exception,first); self.assertTrue(self.responses[0].closed)
        self.assertFalse((self.output/'training-review.json').exists()); self.assertEqual(len(self.calls),1)

    def test_parent_rebinds_whole_projection_without_http(self):
        value=self.public(); T.verify_saved_review(value,TOKEN,kind='iceberg')
        self.assertEqual(self.calls,[]); self.assertEqual(list(self.output.iterdir()),[])
        with self.assertRaises(N.NotebookError): T.verify_saved_review(value,TOKEN)
        variants=[('table_uuid','00000000-0000-0000-0000-000000000003'),('snapshot_id','12346'),
            ('schema_sha256','0'*64),('rows_sha256','0'*64),('query_evidence_token','1'*32)]
        for key,replacement in variants:
            value=self.public(); value['lake_source'][key]=replacement; T.validate_public(value)
            with self.subTest(key=key),self.assertRaises(N.NotebookError): T.verify_saved_review(value,TOKEN,kind='iceberg')
        value=self.public(); value['metrics']['train']['mae']+=0.01; T.validate_public(value)
        with self.assertRaises(N.NotebookError): T.verify_saved_review(value,TOKEN,kind='iceberg')

    def test_public_source_schema_counts_and_identity_are_strict(self):
        mutations=[lambda v:v['lake_source'].__setitem__('source_token','1'*32),
            lambda v:v['lake_source'].__setitem__('snapshot_id',True),
            lambda v:v['lake_source'].__setitem__('unknown',1),lambda v:v.__setitem__('profile',T.PROFILE),
            lambda v:v['model'].__setitem__('train_rows',96),lambda v:v['holdout'][0].__setitem__('row_index',True)]
        for index,mutate in enumerate(mutations):
            value=self.public(); mutate(value)
            with self.subTest(mutation=index),self.assertRaises(N.NotebookError): T.validate_public(value)

    def test_saved_identity_metrics_table_and_sixteen_residual_points(self):
        value=self.public(); rendered=T.render_review(value)
        for text in (TABLE,TABLE_UUID,'12345',QUERY,SOURCE,'Training · 48 rows','Holdout · 16 rows','saved row_id'):
            self.assertIn(text,rendered)
        self.assertEqual(rendered.count('<table>'),2); self.assertEqual(rendered.count('<tr>'),21)
        svg=ET.fromstring(T.residual_svg(value)); circles=svg.findall('{http://www.w3.org/2000/svg}circle')
        self.assertEqual(len(circles),16)
        for circle in circles:
            self.assertTrue(48 <= float(circle.get('cx')) <= 552)
            self.assertTrue(40 <= float(circle.get('cy')) <= 220)

    def test_registered_templates_and_cross_profile_rejection(self):
        T.validate_template(T.TEMPLATE_PATH.read_bytes())
        T.validate_template(T.LAKE_TEMPLATE_PATH.read_bytes(),kind='iceberg')
        with self.assertRaises(N.NotebookError): T.validate_template(T.TEMPLATE_PATH.read_bytes(),kind='iceberg')
        with self.assertRaises(N.NotebookError): T.validate_template(T.LAKE_TEMPLATE_PATH.read_bytes())


if __name__ == '__main__':
    unittest.main()
