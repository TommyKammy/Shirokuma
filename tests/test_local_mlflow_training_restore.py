"""Offline training-profile restore fixtures; stdlib SQLite and JSON only."""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tools.local_experiment.mlflow import restore as R
from tests.test_local_mlflow_restore import fixture, refresh, STORE_ID


def workload(key):
    count, train_count = (128, 96) if key == '3' else (64, 48)
    rows = []
    for i in range(count):
        features = [float(1 if (i >> j) & 1 else -1) for j in range(3)]
        rows.append([*features, 4+2.5*features[0]-1.75*features[1]+.5*features[2]])
    coefficients = [value*train_count/(train_count+1) for value in (2.5,-1.75,.5)]
    predict = [4+sum(row[j]*coefficients[j] for j in range(3)) for row in rows]
    def metrics(indices, predicted):
        target = [rows[i][3] for i in indices]; mean = sum(target)/len(target)
        square = sum((actual-estimate)**2 for actual,estimate in zip(target,predicted))
        return dict(mae=sum(abs(actual-estimate) for actual,estimate in zip(target,predicted))/len(target),
                    rmse=math.sqrt(square/len(target)), r2=1-square/sum((actual-mean)**2 for actual in target))
    split = dict(seed=1730,train_indices=list(range(train_count)),test_indices=list(range(train_count,count)))
    dataset = dict(feature_names=['x1','x2','x3'],target_name='target',rows=rows)
    params = dict(algorithm='Ridge',alpha=1.0,solver='svd',fit_intercept=True)
    if key == '3': params['dataset_seed']=1729
    value = dict(schema_version=1,profile=R.TRAINING_PROFILES[key][1],dataset=dataset,split=split,params=params,
        model=dict(coefficients=coefficients,intercept=4.0),baseline=dict(prediction=4.0),
        test_predictions=predict[train_count:],
        metrics=dict(train=metrics(range(train_count),predict[:train_count]),
                     test=metrics(range(train_count,count),predict[train_count:]),
                     baseline_test=metrics(range(train_count,count),[4.0]*(count-train_count))),
        hashes=dict(dataset_sha256=R.digest(R.canonical(dataset)),split_sha256=R.digest(R.canonical(split))),
        versions=dict(python='3.12.12',numpy='2.5.3',scikit_learn='1.9.1'))
    if key == '4':
        typed = [[dict(type='bigint',value=str(i)),*[dict(type='double',value=repr(cell)) for cell in row]] for i,row in enumerate(rows)]
        observation = dict(schema_version=1,table='iceberg.local_experiment.training_'+'9'*32,table_uuid=STORE_ID,
            snapshot_id='123',schema_sha256='8'*64,rows_sha256=R.digest(R.canonical(typed)),rows=typed,
            columns=[dict(name=name,type='bigint' if i==0 else 'double',nullable=True)
                     for i,name in enumerate(['row_id','x1','x2','x3','target'])],
            row_count=64,fetched_row_count=64,requested_limit=100,truncated=False,ordering='unspecified')
        value['lake_input']=dict(fixture_profile='fixed-iceberg-regression-source-v1',query_evidence_token='7'*32,
            observation=observation,split_row_ids=dict(train=split['train_indices'],test=split['test_indices']))
    return value


def add_training(root, backup, keys=('3','4')):
    """Add self-contained finished training runs; returns their JSON/identity data."""
    def directory(path):
        path.mkdir(parents=True,exist_ok=True)
        for parent in (path,*path.parents):
            if parent == backup.parent: break
            parent.chmod(0o700)
    database = backup/'store/tracking.sqlite'
    added = {}
    with sqlite3.connect(database) as connection:
        for table, name, kind in [('runs','end_time','INTEGER'),('runs','name','TEXT'),
                ('metrics','is_nan','INTEGER'),('latest_metrics','timestamp','INTEGER'),
                ('latest_metrics','step','INTEGER'),('latest_metrics','is_nan','INTEGER')]:
            columns = {row[1] for row in connection.execute('PRAGMA table_info('+table+')')}
            if name not in columns: connection.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+kind)
        for key in keys:
            owner,namespace = R.TRAINING_EXPERIMENTS[key]
            folder,_,prefix = R.TRAINING_PROFILES[key]
            attempt,run_id = ('c'*32,'d'*32) if key=='3' else ('e'*32,'f'*32)
            data=workload(key);raw=R.canonical(data)+b'\n';sha=R.digest(raw)
            receipt=dict(schema_version=1,result='verified',phase='verified',token=attempt,store_id=STORE_ID,
                source_sha256={'historical_source.py':'0'*64},experiment_id=key,run_id=run_id,
                started_ms=1700000000000,training_finished_ms=1700000000100,finished_ms=1700000000200,
                workload_sha256=sha,failure_code=None)
            directory(backup/folder/attempt)
            R.write(backup/folder/attempt/'result.json',R.canonical(receipt)+b'\n')
            R.write(backup/folder/attempt/'workload.json',raw)
            artifact=backup/'store/artifacts'/namespace/run_id/'artifacts';directory(artifact)
            R.write(artifact/'safe-projection.json',raw)
            old=root/R.SOURCE/'store/artifacts'/namespace
            connection.execute('INSERT INTO experiments VALUES (?,?,?,?)',(int(key),owner,old.as_uri(),'active'))
            for name,value in {'shirokuma.owner':owner,'shirokuma.store_id':STORE_ID}.items():
                connection.execute('INSERT INTO experiment_tags VALUES (?,?,?)',(name,value,int(key)))
            connection.execute('INSERT INTO runs (run_uuid,experiment_id,status,lifecycle_stage,artifact_uri,start_time,end_time,name) VALUES (?,?,?,?,?,?,?,?)',
                (run_id,int(key),'FINISHED','active',(old/run_id/'artifacts').as_uri(),receipt['started_ms'],receipt['finished_ms'],prefix+attempt[:8]))
            tags={'shirokuma.owner':owner,'shirokuma.store_id':STORE_ID,'shirokuma.attempt':attempt,
                'shirokuma.workload_sha256':sha,'shirokuma.time_semantics':'actual-local-training-and-tracking','mlflow.runName':prefix+attempt[:8]}
            for name,value in tags.items():connection.execute('INSERT INTO tags VALUES (?,?,?)',(name,value,run_id))
            params={name:str(value).lower() if type(value) is bool else str(value) for name,value in data['params'].items()}
            params.update(data['hashes']);params.update(workload=data['profile'],scope='saved fixed training fixture')
            for name,value in params.items():connection.execute('INSERT INTO params VALUES (?,?,?)',(name,value,run_id))
            for group,values in data['metrics'].items():
                for name,value in values.items():
                    connection.execute('INSERT INTO metrics (key,value,timestamp,step,run_uuid,is_nan) VALUES (?,?,?,?,?,?)',
                        (group+'_'+name,value,receipt['training_finished_ms'],0,run_id,0))
                    connection.execute('INSERT INTO latest_metrics (key,value,run_uuid,timestamp,step,is_nan) VALUES (?,?,?,?,?,?)',
                        (group+'_'+name,value,run_id,receipt['training_finished_ms'],0,0))
            added[key]=dict(attempt=attempt,run=run_id,receipt=receipt,workload=data)
    return added


class TrainingRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name).resolve()
        self.manifest,self.backup=fixture(self.root)

    def add(self,keys=('3','4')):
        self.added=add_training(self.root,self.backup,keys)
        refresh(self.root,self.backup,self.manifest)

    def restore(self):return R.restore(self.manifest,root=self.root)

    def sql(self,sql,args=()):
        with sqlite3.connect(self.backup/'store/tracking.sqlite') as connection:connection.execute(sql,args)
        refresh(self.root,self.backup,self.manifest)

    def receipt_path(self,key):return self.backup/R.TRAINING_PROFILES[key][0]/self.added[key]['attempt']/'result.json'

    def change_receipt(self,key,**values):
        path=self.receipt_path(key);receipt=json.loads(path.read_bytes());receipt.update(values)
        path.write_bytes(R.canonical(receipt)+b'\n');refresh(self.root,self.backup,self.manifest)

    def test_legacy_three_experiments_remain_unchanged(self):
        self.assertEqual(set(R.EXPERIMENTS),{'0','1','2'})
        result=self.restore()
        self.assertEqual(result['database']['artifact_uri_updates'],{'experiments':3,'runs':2})

    def test_supervised_profile_optional_success_and_full_copy_unchanged(self):
        self.add(('3',));self.assert_clone(4,3)

    def test_both_training_profiles_success_and_only_uri_columns_change(self):
        self.add();self.assert_clone(5,4)

    def assert_clone(self,experiments,runs):
        before=R.inventory(self.backup);manifest=self.manifest.read_bytes()
        result=self.restore();self.assertEqual(result['result'],'verified')
        self.assertIs(result['activation_performed'],False)
        self.assertEqual(result['database']['artifact_uri_updates'],{'experiments':experiments,'runs':runs})
        inspected=R.inspect_completed(result['token'],root=self.root)
        self.assertEqual(R.inventory(self.backup),before);self.assertEqual(self.manifest.read_bytes(),manifest)
        self.assertEqual(R.inventory(inspected['root']/'export')[0],before[0])
        for table,proof in result['database']['tables'].items():
            self.assertEqual(proof['before_sha256']==proof['after_sha256'],table not in ('experiments','runs'))
        self.assertFalse((inspected['root']/'clone/runtime.json').exists())
        self.assertFalse((inspected['root']/'clone/training').exists())

    def test_training_does_not_depend_on_current_sources_or_external_lake_pipeline(self):
        self.add()
        self.assertFalse((self.root/'tools').exists())
        self.assertFalse((self.root/'tmp/local-experiment/lake-training').exists())
        self.change_receipt('3',source_sha256={'previous/source.py':'a'*64})
        self.change_receipt('4',source_sha256={'previous/other.py':'b'*64})
        self.assertEqual(self.restore()['result'],'verified')

    def test_unreferenced_failed_journal_is_inert_and_preserved(self):
        self.add()
        path=self.backup/'lake-training'/('1'*32);path.mkdir(mode=0o700)
        R.write(path/'result.json',R.canonical(dict(result='failed',phase='training')))
        refresh(self.root,self.backup,self.manifest)
        self.assert_clone(5,4)

    def test_referenced_receipt_identity_status_schema_and_times_rejected(self):
        self.add()
        original=self.receipt_path('3').read_bytes()
        for values in [dict(schema_version=True),dict(result='failed'),dict(phase='running'),dict(failure_code='failed'),
                dict(token='1'*32),dict(run_id='1'*32),dict(experiment_id='4'),dict(store_id='different'),
                dict(started_ms=True),dict(started_ms=1699999999999),dict(finished_ms=1700000000201),
                dict(training_finished_ms=1700000000201),dict(unknown=1),dict(source_sha256={}),
                dict(source_sha256={'source.py':'bad'})]:
            with self.subTest(values=values):
                self.receipt_path('3').write_bytes(original);self.change_receipt('3',**values)
                with self.assertRaises(R.RestoreError):self.restore()

    def test_missing_referenced_receipt_and_workload_are_rejected(self):
        self.add()
        for filename in ('result.json','workload.json'):
            path=self.receipt_path('4').with_name(filename);raw=path.read_bytes();path.unlink()
            refresh(self.root,self.backup,self.manifest)
            with self.subTest(filename=filename),self.assertRaises(R.RestoreError):self.restore()
            R.write(path,raw)

    def test_run_owner_store_attempt_and_workload_tags_are_exact(self):
        self.add();run=self.added['4']['run']
        originals={row[0]:row[1] for row in sqlite3.connect(self.backup/'store/tracking.sqlite').execute('SELECT key,value FROM tags WHERE run_uuid=?',(run,))}
        for name in ('shirokuma.owner','shirokuma.store_id','shirokuma.attempt','shirokuma.workload_sha256','mlflow.runName'):
            self.sql('UPDATE tags SET value=? WHERE run_uuid=? AND key=?',('wrong',run,name))
            with self.subTest(name=name),self.assertRaises(R.RestoreError):self.restore()
            self.sql('UPDATE tags SET value=? WHERE run_uuid=? AND key=?',(originals[name],run,name))
        self.sql('UPDATE tags SET value=? WHERE run_uuid=? AND key=?',('1'*32,run,'shirokuma.attempt'))
        with self.assertRaises(R.RestoreError):self.restore()
        self.sql('UPDATE tags SET value=? WHERE run_uuid=? AND key=?',(originals['shirokuma.attempt'],run,'shirokuma.attempt'))
        self.sql('INSERT INTO tags VALUES (?,?,?)',('unknown','value',run))
        with self.assertRaises(R.RestoreError):self.restore()

    def test_experiment_owner_store_name_and_namespace_are_exact(self):
        self.add()
        for name in ('shirokuma.owner','shirokuma.store_id'):
            value=R.TRAINING_EXPERIMENTS['3'][0] if name=='shirokuma.owner' else STORE_ID
            self.sql('UPDATE experiment_tags SET value=? WHERE experiment_id=3 AND key=?',('wrong',name))
            with self.subTest(name=name),self.assertRaises(R.RestoreError):self.restore()
            self.sql('UPDATE experiment_tags SET value=? WHERE experiment_id=3 AND key=?',(value,name))
        self.sql('UPDATE experiments SET name=? WHERE experiment_id=3',('unknown-training',))
        with self.assertRaises(R.RestoreError):self.restore()

    def test_unknown_experiment_id_and_unfinished_run_rejected(self):
        self.add()
        self.sql('INSERT INTO experiments VALUES (?,?,?,?)',(5,'unknown',(self.root/R.SOURCE/'store/artifacts/unknown').as_uri(),'active'))
        with self.assertRaises(R.RestoreError):self.restore()
        self.sql('DELETE FROM experiments WHERE experiment_id=5')
        for state in ('RUNNING','FAILED','SCHEDULED'):
            self.sql('UPDATE runs SET status=? WHERE run_uuid=?',(state,self.added['3']['run']))
            with self.subTest(state=state),self.assertRaises(R.RestoreError):self.restore()

    def test_cross_profile_artifact_namespace_is_rejected(self):
        self.add();run=self.added['4']['run']
        wrong=(self.root/R.SOURCE/'store/artifacts/supervised-training'/run/'artifacts').as_uri()
        self.sql('UPDATE runs SET artifact_uri=? WHERE run_uuid=?',(wrong,run))
        with self.assertRaisesRegex(R.RestoreError,'ARTIFACT_URI'):self.restore()

    def test_workload_profile_hash_and_exact_artifact_bytes_are_required(self):
        self.add();info=self.added['3'];path=self.receipt_path('3').with_name('workload.json');original=path.read_bytes()
        artifact=self.backup/'store/artifacts/supervised-training'/info['run']/'artifacts/safe-projection.json'
        for kind in ('profile','hash','bytes','noncanonical','unknown','nonfinite'):
            path.write_bytes(original);artifact.write_bytes(original)
            receipt=deepcopy(info['receipt']);data=json.loads(original)
            if kind=='profile':data['profile']='unknown-training-v1'
            elif kind=='unknown':data['unknown']=True
            elif kind=='nonfinite':data['model']['intercept']=float('inf')
            if kind in ('profile','unknown'):
                raw=R.canonical(data)+b'\n';path.write_bytes(raw);artifact.write_bytes(raw);receipt['workload_sha256']=R.digest(raw)
            elif kind=='nonfinite':
                raw=json.dumps(data).encode();path.write_bytes(raw);artifact.write_bytes(raw)
            elif kind=='hash':receipt['workload_sha256']='0'*64
            elif kind=='bytes':artifact.write_bytes(original+b' ')
            else:path.write_bytes(json.dumps(data,indent=2).encode())
            self.receipt_path('3').write_bytes(R.canonical(receipt)+b'\n')
            self.sql('UPDATE tags SET value=? WHERE run_uuid=? AND key=?',(receipt['workload_sha256'],info['run'],'shirokuma.workload_sha256'))
            with self.subTest(kind=kind),self.assertRaises(R.RestoreError):self.restore()


if __name__=='__main__':unittest.main()
