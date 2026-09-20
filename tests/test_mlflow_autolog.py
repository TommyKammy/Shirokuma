"""Fixed autolog tests use private saved JSON and a fake SDK; never fit or call services."""
from copy import deepcopy
import contextlib
import io
import itertools
import json
from pathlib import Path
import sqlite3
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from tools.local_experiment.mlflow import autolog as A
from tools.local_experiment.mlflow import autolog_contract as C
from tests import test_notebook_lake_training_client as L

VERSIONS = dict(python='3.12.12', numpy='2.5.3', scikit_learn='1.9.1', mlflow='3.16.1')
HTML = b'<!DOCTYPE html>\n<html lang="en"><head><meta charset="UTF-8"/></head><body>Ridge(alpha=1.0, solver=svd)</body></html>'


class FakeClient:
    def __init__(self):
        self.calls, self.experiments, self.runs, self.histories = [], {}, {}, {}
        self.fail_create = False

    def get_experiment_by_name(self, name):
        self.calls.append('lookup-experiment')
        return next((x for x in self.experiments.values() if x.name == name), None)

    def create_experiment(self, name, *, artifact_location, tags):
        self.calls.append('create-experiment')
        self.experiments['6'] = SimpleNamespace(experiment_id='6', name=name,
            artifact_location=artifact_location, tags=tags, lifecycle_stage='active')
        return '6'

    def get_experiment(self, identity):
        self.calls.append('get-experiment'); return self.experiments[identity]

    def search_runs(self, experiments, *, max_results):
        self.calls.append('search-runs'); return list(self.runs.values())[:max_results]

    def create_run(self, experiment_id, *, start_time, tags, run_name):
        self.calls.append('create-run')
        run_id = ('%032x' % (len(self.runs) + 1))
        info = SimpleNamespace(run_id=run_id, experiment_id=experiment_id, status='RUNNING',
            start_time=start_time, end_time=None, lifecycle_stage='active',
            artifact_uri=A._artifact_path(run_id).parent.as_uri(), run_name=run_name, user_id='local')
        run = SimpleNamespace(info=info, data=SimpleNamespace(params={}, metrics={}, tags=dict(tags, **{'mlflow.runName':run_name})))
        self.runs[run_id] = run
        if self.fail_create: raise RuntimeError('private reply lost')
        return run

    def get_run(self, run_id):
        self.calls.append('get-run'); return self.runs[run_id]

    def get_metric_history(self, run_id, key):
        self.calls.append('history'); return self.histories.get((run_id,key), [])

    def list_artifacts(self, run_id):
        self.calls.append('artifacts')
        return [SimpleNamespace(path=p.name, is_dir=False, file_size=p.stat().st_size)
                for p in A._artifact_path(run_id).parent.iterdir()]


class AutologTests(unittest.TestCase):
    def setUp(self):
        self.fixture = L.LakeTrainingReviewTests()
        self.addCleanup(self.fixture.doCleanups); self.fixture.setUp()
        self.state = self.fixture.state
        for module in (A, A.G):
            for key, value in [('STATE',self.state), ('ROOT',self.fixture.root)]:
                changed=patch.object(module,key,value); changed.start(); self.addCleanup(changed.stop)
        self.client = FakeClient(); self.fit_calls = []
        self.backup = dict(store_id=L.STORE, manifest='tmp/local-experiment/checks/a/backup-manifest.json', sha256='b'*64)
        self.clock = itertools.count(10000,1000).__next__

    def fake_fit(self, client, run_id, train):
        self.fit_calls.append(deepcopy(train))
        run = client.runs[run_id]; start = run.info.start_time
        model = deepcopy(self.fixture.workload['model'])
        metrics = C.expected_metrics(model, self.fixture.workload)
        run.data.params = deepcopy(C.PARAMS); run.data.metrics = metrics
        run.data.tags.update(estimator_name='Ridge', estimator_class='sklearn.linear_model._ridge.Ridge')
        run.info.status, run.info.end_time = 'FINISHED', start+10
        for key,value in metrics.items():
            client.histories[run_id,key] = [SimpleNamespace(key=key,timestamp=start+5,step=0,value=value)]
        path = A._artifact_path(run_id)
        path.parent.mkdir(mode=0o700, parents=True)
        for parent in path.parents:
            if parent == self.state: break
            parent.chmod(0o700)
        path.write_bytes(HTML); path.chmod(0o600)
        return model, deepcopy(VERSIONS)

    def owner(self, **kwargs):
        return A.Runner(self.client, store_id=L.STORE, backup=self.backup, fit=self.fake_fit, clock=self.clock, **kwargs)

    def files(self):
        return {str(p.relative_to(self.state)):(p.read_bytes(),p.stat().st_mtime_ns,p.stat().st_mode)
                for p in self.state.rglob('*') if p.is_file()}

    def test_one_fit_uses_only_saved_48_train_and_complete_cold_repeat_writes_nothing(self):
        before = self.files(); result=self.owner().run(L.TOKEN)
        self.assertEqual(len(self.fit_calls),1)
        self.assertEqual(self.fit_calls[0], [self.fixture.workload['dataset']['rows'][i]
                                          for i in self.fixture.workload['split']['train_indices']])
        self.assertEqual(len(self.fit_calls[0]),48)
        self.assertEqual(result['run']['params'],C.PARAMS)
        self.assertEqual(set(result['run']['metrics']),C.METRICS)
        self.assertEqual(result['artifact']['name'],'estimator.html')
        for key,value in before.items():self.assertEqual(self.files()[key],value)
        saved=self.files(); calls=self.client.calls[:]
        with patch.object(A.R,'require_stopped',side_effect=AssertionError('runtime forbidden')), \
             patch.object(A.R,'require_store',side_effect=AssertionError('runtime forbidden')), \
             patch.object(A.G,'validate_backup',side_effect=AssertionError('backup forbidden')), \
             patch.object(A.subprocess,'run',side_effect=AssertionError('worker forbidden')), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(A.main(['--training-token',L.TOKEN]),0)
        self.assertEqual(json.loads(out.getvalue()),A.public(result))
        self.assertEqual(A.recorded(L.TOKEN),result)
        self.assertEqual(self.files(),saved); self.assertEqual(self.client.calls,calls)
        self.assertEqual(len(self.fit_calls),1)

    def test_create_reply_loss_keeps_pending_and_never_resubmits(self):
        self.client.fail_create=True
        with self.assertRaisesRegex(RuntimeError,'reply lost'): self.owner().run(L.TOKEN)
        calls=self.client.calls[:]
        with self.assertRaisesRegex(C.AutologError,'PRIOR_OUTCOME_UNKNOWN'): A.recorded(L.TOKEN)
        with self.assertRaisesRegex(C.AutologError,'PRIOR_OUTCOME_UNKNOWN'): self.owner().run(L.TOKEN)
        self.assertEqual(self.client.calls,calls); self.assertEqual(self.fit_calls,[])

    def test_autolog_warning_or_extra_artifact_is_not_verified_or_retryable(self):
        for mutation in ('metric','pickle','params','history'):
            with self.subTest(mutation=mutation):
                # Each case has its own already-private fixture, with no real SDK.
                case=AutologTests(); case.setUp()
                try:
                    owner=case.owner(); fit=owner.fit
                    def broken(client,run,rows):
                        result=fit(client,run,rows)
                        if mutation=='metric':client.runs[run].data.metrics.pop('training_score')
                        elif mutation=='params':client.runs[run].data.params['alpha']='10.0'
                        elif mutation=='history':client.histories[run,'training_score'] *= 2
                        else:
                            p=A._artifact_path(run).with_name('model.pkl');p.write_bytes(b'not allowed');p.chmod(0o600)
                        return result
                    owner.fit=broken
                    with self.assertRaises(C.AutologError):owner.run(L.TOKEN)
                    index=json.loads((case.state/C.INDEX_NAME).read_bytes())
                    self.assertFalse(index['runs'][L.TOKEN]['completed'])
                    with self.assertRaisesRegex(C.AutologError,'PRIOR_OUTCOME_UNKNOWN'):A.recorded(L.TOKEN)
                    self.assertEqual(len(case.fit_calls),1)
                finally:case.doCleanups()

    def test_first_cancel_survives_secondary_receipt_write_failure(self):
        first=KeyboardInterrupt();owner=self.owner();owner.fit=lambda *args:(_ for _ in ()).throw(first)
        persist=A.E.persist
        def write(path,value):
            if path.name=='result.json':raise OSError('secondary')
            return persist(path,value)
        with patch.object(A.E,'persist',side_effect=write):
            with self.assertRaises(KeyboardInterrupt) as error:owner.run(L.TOKEN)
        self.assertIs(error.exception,first)
        self.assertFalse(json.loads((self.state/C.INDEX_NAME).read_bytes())['runs'][L.TOKEN]['completed'])

    def test_source_changed_during_fit_preserves_pending(self):
        owner=self.owner();fit=owner.fit
        def changed(*args):
            result=fit(*args)
            self.fixture.workload_path.write_bytes(self.fixture.workload_path.read_bytes()+b' ')
            return result
        owner.fit=changed
        with self.assertRaises(Exception):owner.run(L.TOKEN)
        self.assertFalse(json.loads((self.state/C.INDEX_NAME).read_bytes())['runs'][L.TOKEN]['completed'])

    def test_completed_source_receipt_and_artifact_tampering_are_rejected(self):
        result=self.owner().run(L.TOKEN);artifact=A._artifact_path(result['run_id'])
        original=artifact.read_bytes();artifact.write_bytes(original.replace(b'Ridge',b'Other'))
        with self.assertRaises(C.AutologError):A.recorded(L.TOKEN)
        artifact.write_bytes(original)
        path=self.state/C.FOLDER/result['evidence_token']/'result.json'
        altered=deepcopy(result);altered['model']['intercept']+=1
        self.fixture.write(path,altered)
        with self.assertRaises(C.AutologError):A.recorded(L.TOKEN)

    def test_fake_sdk_autolog_context_has_exact_flags_one_fit_and_disable(self):
        calls=[];mlflow=ModuleType('mlflow');sklearn=ModuleType('mlflow.sklearn')
        mlflow.__version__='3.16.1';mlflow.sklearn=sklearn
        mlflow.set_tracking_uri=lambda value:calls.append(('tracking',value))
        mlflow.set_registry_uri=lambda value:calls.append(('registry',value))
        @contextlib.contextmanager
        def active(*,run_id):
            calls.append(('run',run_id));yield
        mlflow.start_run=active;sklearn.autolog=lambda **kwargs:calls.append(('autolog',kwargs))
        rows=[[1,2,3,4]]*48
        with patch.dict(sys.modules,{'mlflow':mlflow,'mlflow.sklearn':sklearn}), \
             patch.object(A.M,'_fit',return_value=({'coefficients':[1.,2.,3.],'intercept':4.},
                                                 {k:v for k,v in VERSIONS.items() if k!='mlflow'})) as fit:
            _,versions=A.fit_once(None,'c'*32,rows)
        fit.assert_called_once_with(rows,1.0)
        self.assertEqual([v for k,v in calls if k=='autolog'],[C.AUTOLOG_OPTIONS,{'disable':True}])
        self.assertEqual(versions,VERSIONS)

    def test_fit_cancel_survives_sdk_end_run_and_disable_failures(self):
        first=KeyboardInterrupt(); mlflow=ModuleType('mlflow');sklearn=ModuleType('mlflow.sklearn')
        mlflow.__version__='3.16.1';mlflow.sklearn=sklearn
        mlflow.set_tracking_uri=mlflow.set_registry_uri=lambda value:None
        @contextlib.contextmanager
        def active(**kwargs):
            try:yield
            finally:raise OSError('end-run secondary')
        mlflow.start_run=active
        def autolog(**kwargs):
            if kwargs.get('disable'):raise OSError('disable secondary')
        sklearn.autolog=autolog
        with patch.dict(sys.modules,{'mlflow':mlflow,'mlflow.sklearn':sklearn}), \
             patch.object(A.M,'_fit',side_effect=first):
            with self.assertRaises(KeyboardInterrupt) as observed:A.fit_once(None,'c'*32,[[1,2,3,4]]*48)
        self.assertIs(observed.exception,first)

    def test_source_change_during_fit_intent_write_prevents_fit(self):
        persist=A.E.persist
        def write(path,value):
            result=persist(path,value)
            if path.name==C.INDEX_NAME and value['runs'].get(L.TOKEN,{}).get('phase')=='fit-started':
                self.fixture.workload_path.write_bytes(self.fixture.workload_path.read_bytes()+b' ')
            return result
        with patch.object(A.E,'persist',side_effect=write):
            with self.assertRaises(Exception):self.owner().run(L.TOKEN)
        self.assertEqual(self.fit_calls,[])

    def test_second_source_uses_existing_owned_experiment_without_tracking_its_own_index_write(self):
        first=self.owner().run(L.TOKEN)
        token,run='e'*32,'f'*32
        receipt=deepcopy(self.fixture.receipt);receipt.update(token=token,run_id=run)
        self.fixture.write(self.state/'lake-training'/token/'result.json',receipt)
        self.fixture.write(self.state/'lake-training'/token/'workload.json',self.fixture.workload)
        self.fixture.write(self.state/'store/artifacts/iceberg-training'/run/'artifacts/safe-projection.json',self.fixture.workload)
        second=self.owner().run(token)
        self.assertNotEqual(first['run_id'],second['run_id'])
        self.assertEqual(self.client.calls.count('create-experiment'),1)
        self.assertEqual(len(self.fit_calls),2)
        self.assertEqual(A.recorded(L.TOKEN),first)

    def test_bad_input_is_fixed_failure_without_sdk_or_source_read(self):
        with patch.object(A,'Snapshot',side_effect=AssertionError('source')),contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(A.main(['--training-token','bad','--backup-manifest','/x']),1)
        self.assertEqual(json.loads(out.getvalue()),{'result':'failed','error_code':'AUTOLOG_TOKEN_INVALID'})

    def test_pure_contract_rejects_bool_nonfinite_holdout_and_wrong_source(self):
        result=self.owner().run(L.TOKEN);snapshot=A.Snapshot(L.TOKEN);source=snapshot.load()
        index=json.loads((self.state/C.INDEX_NAME).read_bytes())
        for field,value in [('schema_version',True),('source_unchanged',1),('started_ms',True)]:
            with self.subTest(field=field):
                bad=deepcopy(result);bad[field]=value
                with self.assertRaises(C.AutologError):C.validate_completed(index,source,bad,HTML,store_id=L.STORE,backup=self.backup)
        wrong=deepcopy(source);wrong['train_rows_sha256']='d'*64
        with self.assertRaises(C.AutologError):C.validate_source(wrong,self.fixture.workload,self.fixture.receipt,store_id=L.STORE)
        bad=deepcopy(result);bad['run']['metrics']['training_score']=float('nan')
        with self.assertRaises(C.AutologError):C.validate_completed(index,source,bad,HTML,store_id=L.STORE,backup=self.backup)
        bad=deepcopy(result);bad['run']['metrics']['training_score']-=.1
        with self.assertRaises(C.AutologError):C.validate_calculation(bad,self.fixture.workload)

    def test_pinned_html_language_wrapper_and_legacy_wrapper_with_hash_binding(self):
        import hashlib
        result=self.owner().run(L.TOKEN);source=A.Snapshot(L.TOKEN).load()
        index=json.loads((self.state/C.INDEX_NAME).read_bytes())
        for start in (b'<html lang="en">',b"<html lang='en'>",b'<html>'):
            raw=HTML.replace(b'<html lang="en">',start);candidate=deepcopy(result)
            candidate['artifact'].update(size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
            self.assertEqual(C.validate_completed(index,source,candidate,raw,store_id=L.STORE,backup=self.backup),candidate)
        for start in (b'<htmlx lang="en">',b'<html lang="other">'):
            raw=HTML.replace(b'<html lang="en">',start);candidate=deepcopy(result)
            candidate['artifact'].update(size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
            with self.assertRaisesRegex(C.AutologError,'ARTIFACT_INVALID'):
                C.validate_completed(index,source,candidate,raw,store_id=L.STORE,backup=self.backup)

    def failed_html_case(self):
        with patch.object(C,'validate_completed',side_effect=C.AutologError('AUTOLOG_ARTIFACT_INVALID')):
            with self.assertRaisesRegex(C.AutologError,'ARTIFACT_INVALID'):self.owner().run(L.TOKEN)
        index=json.loads((self.state/C.INDEX_NAME).read_bytes());entry=index['runs'][L.TOKEN]
        directory=self.state/C.FOLDER/entry['evidence_token']
        result=json.loads((directory/'result.json').read_bytes());view=result['run']
        db=self.state/'store/tracking.sqlite'
        with sqlite3.connect(db) as connection:
            connection.execute('CREATE TABLE experiments (experiment_id INTEGER,name TEXT,artifact_location TEXT,lifecycle_stage TEXT)')
            connection.execute('INSERT INTO experiments VALUES (?,?,?,?)',(6,C.EXPERIMENT,(self.state/'store/artifacts'/C.NAMESPACE).as_uri(),'active'))
            connection.execute('CREATE TABLE experiment_tags (experiment_id INTEGER,key TEXT,value TEXT)')
            connection.executemany('INSERT INTO experiment_tags VALUES (?,?,?)',[(6,k,v) for k,v in C.experiment_tags(L.STORE).items()])
            connection.execute('CREATE TABLE runs (run_uuid TEXT,experiment_id INTEGER,status TEXT,start_time INTEGER,end_time INTEGER,lifecycle_stage TEXT,artifact_uri TEXT,name TEXT,user_id TEXT)')
            info=view['info'];connection.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)',
                (info['run_id'],int(info['experiment_id']),info['status'],info['start_time'],info['end_time'],
                 info['lifecycle_stage'],info['artifact_uri'],info['run_name'],info['user_id']))
            for table in ('params','tags'):
                connection.execute('CREATE TABLE '+table+' (run_uuid TEXT,key TEXT,value TEXT)')
                connection.executemany('INSERT INTO '+table+' VALUES (?,?,?)',[(info['run_id'],k,v) for k,v in view[table].items()])
            for table in ('metrics','latest_metrics'):
                connection.execute('CREATE TABLE '+table+' (run_uuid TEXT,key TEXT,value REAL,timestamp INTEGER,step INTEGER,is_nan INTEGER)')
                connection.executemany('INSERT INTO '+table+' VALUES (?,?,?,?,?,?)',
                    [(info['run_id'],k,h[0][3],h[0][1],h[0][2],0) for k,h in view['histories'].items()])
        db.chmod(0o600)
        return directory,result,db

    @contextlib.contextmanager
    def recovery_runtime(self):
        @contextlib.contextmanager
        def lock():yield
        with patch.object(A.R,'store_lock',lock),patch.object(A.R,'require_stopped',return_value={'store_id':L.STORE}):yield

    def test_recovery_adopts_finished_run_without_changing_failure_or_database_or_fitting(self):
        directory,failed,db=self.failed_html_case()
        original={p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in directory.iterdir()}
        database=(db.read_bytes(),db.stat().st_mtime_ns);calls=self.client.calls[:]
        with self.recovery_runtime(),patch.object(A,'fit_once',side_effect=AssertionError('fit forbidden')):
            result=A.recover(L.TOKEN)
        self.assertEqual(result,dict(failed,result='verified',failure_code=None))
        self.assertEqual((db.read_bytes(),db.stat().st_mtime_ns),database)
        for name,value in original.items():self.assertEqual(((directory/name).read_bytes(),(directory/name).stat().st_mtime_ns),value)
        self.assertEqual(self.client.calls,calls);self.assertEqual(len(self.fit_calls),1)
        self.assertEqual(A.recorded(L.TOKEN),result)
        self.assertEqual(A.public(result)['observation'],'saved-autolog-recovery')
        self.assertTrue(A.public(result)['evidence'].endswith('/recovery.json'))
        recovery=json.loads((directory/'recovery.json').read_bytes())
        self.assertEqual(recovery['original_result_sha256'],C.digest(failed))
        self.assertFalse(recovery['original_entry']['completed'])

    def test_recovery_index_failure_resumes_only_saved_adoption(self):
        directory,failed,db=self.failed_html_case();persist=A.E.persist
        def fail_index(path,value):
            if path.name==C.INDEX_NAME:raise OSError('index write failed')
            return persist(path,value)
        with self.recovery_runtime(),patch.object(A.E,'persist',side_effect=fail_index):
            with self.assertRaisesRegex(OSError,'index write'):A.recover(L.TOKEN)
        self.assertTrue((directory/'recovery.json').exists())
        original=(directory/'recovery.json').read_bytes()
        with self.assertRaisesRegex(C.AutologError,'PRIOR_OUTCOME_UNKNOWN'):A.recorded(L.TOKEN)
        with self.recovery_runtime():result=A.recover(L.TOKEN)
        self.assertEqual((directory/'recovery.json').read_bytes(),original)
        self.assertEqual(result['result'],'verified');self.assertEqual(len(self.fit_calls),1)

    def test_recovery_refuses_different_database_and_unfinished_fit(self):
        directory,failed,db=self.failed_html_case()
        with sqlite3.connect(db) as connection:connection.execute("UPDATE runs SET status='RUNNING'")
        with self.recovery_runtime():
            with self.assertRaisesRegex(C.AutologError,'RUN_CHANGED'):A.recover(L.TOKEN)
        self.assertFalse((directory/'recovery.json').exists())

    def test_recovery_refuses_changed_experiment_and_latest_metric_timestamp(self):
        directory,failed,db=self.failed_html_case()
        with sqlite3.connect(db) as connection:connection.execute("UPDATE experiments SET name='foreign'")
        with self.recovery_runtime():
            with self.assertRaisesRegex(C.AutologError,'EXPERIMENT_CONFLICT'):A.recover(L.TOKEN)
        with sqlite3.connect(db) as connection:
            connection.execute('UPDATE experiments SET name=?',(C.EXPERIMENT,))
            connection.execute('UPDATE latest_metrics SET timestamp=timestamp+1')
        with self.recovery_runtime():
            with self.assertRaisesRegex(C.AutologError,'METRICS_CHANGED'):A.recover(L.TOKEN)
        self.assertFalse((directory/'recovery.json').exists())
        with sqlite3.connect(db) as connection:connection.execute("UPDATE runs SET status='FINISHED'")
        failed['phase']='fit';failed['failure_code']='AUTOLOG_FAILED'
        self.fixture.write(directory/'result.json',failed)
        with self.recovery_runtime():
            with self.assertRaisesRegex(C.AutologError,'RECOVERY_INVALID'):A.recover(L.TOKEN)
        self.assertFalse((directory/'recovery.json').exists())


if __name__=='__main__':unittest.main()
