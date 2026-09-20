"""Cold autolog export/clone with disposable SQLite and a fake SDK, never fitting."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.mlflow import restore as R, restore_readback as B, autolog_contract as A
from tests.test_local_mlflow_restore import fixture, refresh, STORE_ID
from tests.test_mlflow_registry_restore import add_registry, RegistrySDK


def add_autolog(root, backup, training, experiment_id='6'):
    source = A.source(training['receipt'], training['workload'], store_id=STORE_ID)
    token, run_id = '2'*32, '1'*32
    begin, finish = 1700000020000, 1700000020100
    namespace = root / R.SOURCE / 'store/artifacts' / A.NAMESPACE
    run_uri = (namespace / run_id / 'artifacts').as_uri()
    metrics = A.expected_metrics(training['workload']['model'], training['workload'])
    info = dict(run_id=run_id, experiment_id=experiment_id, status='FINISHED', start_time=begin+10,
        end_time=finish-10, lifecycle_stage='active', artifact_uri=run_uri,
        run_name='autolog-'+training['attempt'], user_id='synthetic-author')
    view = dict(info=info, params=deepcopy(A.PARAMS), tags=dict(A.expected_tags(source), **{'mlflow.runName':info['run_name']}),
        metrics=metrics, histories={key:[[key,begin+50,0,value]] for key,value in metrics.items()})
    html = b'<html lang="en"><body>Ridge(alpha=1.0, solver=svd)</body></html>'
    prior_backup = dict(manifest='tmp/local-experiment/unavailable-prior-export/backup-manifest.json',
                        sha256='8'*64, store_id=STORE_ID)
    result = dict(schema_version=1, profile=A.PROFILE, evidence_token=token, training_token=training['attempt'],
        store_id=STORE_ID, experiment_id=experiment_id, run_id=run_id, result='verified', phase='verified',
        started_ms=begin, finished_ms=finish, source_sha256=A.digest(source), source_unchanged=True, failure_code=None,
        model=deepcopy(training['workload']['model']), versions=dict(training['workload']['versions'], mlflow=R.VERSION),
        run=view, artifact=dict(name=A.ARTIFACT, size=len(html), sha256=hashlib.sha256(html).hexdigest()),
        backup_sha256=A.digest(prior_backup))
    index = A.empty_index(STORE_ID); index.update(experiment_id=experiment_id, runs={training['attempt']:
        dict(evidence_token=token, run_id=run_id, source_sha256=A.digest(source), started_ms=begin,
             finished_ms=finish, completed=True, phase='verified')})
    A.validate_completed(index, source, result, html, store_id=STORE_ID, backup=prior_backup)
    A.validate_calculation(result, training['workload'])
    def directory(path):
        path.mkdir(parents=True, exist_ok=True)
        for parent in (path,*path.parents):
            if parent == backup.parent: break
            parent.chmod(0o700)
    evidence = backup / A.FOLDER / token; directory(evidence)
    for name, value in [('source.json',source), ('result.json',result), ('backup.json',prior_backup)]:
        R.write(evidence/name, A.canonical(value))
    R.write(backup/A.INDEX_NAME, A.canonical(index))
    artifact = backup/'store/artifacts'/A.NAMESPACE/run_id/'artifacts'/A.ARTIFACT; directory(artifact.parent)
    R.write(artifact, html)
    with sqlite3.connect(backup/'store/tracking.sqlite') as db:
        db.execute('INSERT INTO experiments VALUES (?,?,?,?)',(int(experiment_id),A.EXPERIMENT,namespace.as_uri(),'active'))
        db.executemany('INSERT INTO experiment_tags VALUES (?,?,?)',[(key,value,int(experiment_id)) for key,value in A.experiment_tags(STORE_ID).items()])
        db.execute('INSERT INTO runs (run_uuid,experiment_id,status,lifecycle_stage,artifact_uri,start_time,end_time,name,user_id) VALUES (?,?,?,?,?,?,?,?,?)',
            (run_id,int(experiment_id),'FINISHED','active',run_uri,info['start_time'],info['end_time'],info['run_name'],info['user_id']))
        for table in ('params','tags'):
            db.executemany('INSERT INTO '+table+' VALUES (?,?,?)',[(key,value,run_id) for key,value in view[table].items()])
        for key, value in metrics.items():
            db.execute('INSERT INTO metrics (key,value,timestamp,step,run_uuid,is_nan) VALUES (?,?,?,?,?,?)',(key,value,begin+50,0,run_id,0))
            db.execute('INSERT INTO latest_metrics (key,value,run_uuid,timestamp,step,is_nan) VALUES (?,?,?,?,?,?)',(key,value,run_id,begin+50,0,0))
    return dict(source=source,result=result,index=index,backup=prior_backup,evidence=evidence,artifact=artifact,run=run_id)


class AutologRestoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve(); self.manifest, self.backup = fixture(self.root)
        self.registry = add_registry(self.root,self.backup)
        # The arithmetic fixture has an orthogonal 48/16 split and no sklearn
        # dependency; retain the full validator with this fixture's split pin.
        pinned = patch.object(A.T,'LAKE_SPLIT_SHA256',self.registry['training']['workload']['hashes']['split_sha256'])
        pinned.start(); self.addCleanup(pinned.stop)
        self.saved = add_autolog(self.root,self.backup,self.registry['training'])
        self.refresh()

    def refresh(self): refresh(self.root,self.backup,self.manifest)

    def restore(self): return R.restore(self.manifest,root=self.root)

    def sql(self,statement,parameters=()):
        with sqlite3.connect(self.backup/'store/tracking.sqlite') as db: db.execute(statement,parameters)
        self.refresh()

    def write_json(self,path,value):
        path.write_bytes(A.canonical(value)); self.refresh()

    def checked(self):
        result=self.restore(); return R.inspect_completed(result['token'],root=self.root)

    def add_recovery(self):
        original=deepcopy(self.saved['result'])
        original.update(result='failed',failure_code='AUTOLOG_ARTIFACT_INVALID')
        entry=deepcopy(self.saved['index']['runs'][self.registry['training']['attempt']])
        entry.update(completed=False,phase='fit-started',finished_ms=None)
        recovery=dict(schema_version=1,profile=A.PROFILE,evidence_token=original['evidence_token'],
            source_sha256=A.digest(self.saved['source']),original_result_sha256=A.digest(original),
            original_entry=entry,original_entry_sha256=A.digest(entry),
            recovered_at_ms=original['finished_ms']+1000,observation='stopped-sqlite-read-only',
            result=deepcopy(self.saved['result']))
        self.assertEqual(A.completed_result(self.saved['source'],original,recovery),self.saved['result'])
        A.validate_recovery_entry(recovery,self.saved['index']['runs'][self.registry['training']['attempt']])
        self.write_json(self.saved['evidence']/'result.json',original)
        R.write(self.saved['evidence']/'recovery.json',A.canonical(recovery)); self.refresh()
        return original,recovery

    def test_html_profile_and_registry_restore_only_change_owned_uri_columns(self):
        before=R.inventory(self.backup); manifest=self.manifest.read_bytes(); result=self.restore()
        checked=R.inspect_completed(result['token'],root=self.root)
        self.assertEqual(result['result'],'verified'); self.assertFalse(result['activation_performed'])
        self.assertEqual(result['database']['artifact_uri_updates'],dict(experiments=5,runs=4,
                         **{'model_versions.source':1,'model_versions.storage_location':1}))
        self.assertEqual(R.inventory(self.backup),before); self.assertEqual(self.manifest.read_bytes(),manifest)
        self.assertEqual(R.inventory(checked['root']/'export')[0],before[0])
        for table,proof in result['database']['tables'].items():
            self.assertEqual(proof['before_sha256']==proof['after_sha256'],table not in ('experiments','runs','model_versions'))
        new_artifact=checked['clone_artifacts']/A.NAMESPACE/self.saved['run']/'artifacts'/A.ARTIFACT
        self.assertEqual(new_artifact.read_bytes(),self.saved['artifact'].read_bytes())
        self.assertFalse((checked['root']/'clone/runtime.json').exists())

    def test_sdk_readback_validates_all_html_bytes_params_history_and_retained_registry(self):
        checked=self.checked(); expected=B.expected_view(checked); sdk=RegistrySDK(expected); sdk.base=checked['clone_artifacts']
        result=B.sdk_read(checked,expected,factory=sdk.factory)
        self.assertEqual(result['result'],'passed')
        self.assertEqual(result['counts'],dict(experiments=5,runs=4,metrics=18,artifacts=4,registered_models=1,model_versions=1))
        run=expected['runs'][self.saved['run']]
        self.assertEqual(set(run['artifacts']),{A.ARTIFACT}); self.assertEqual(run['params'],A.PARAMS)
        self.assertEqual(set(run['histories']),A.METRICS)
        self.assertEqual({call[2] for call in sdk.calls if call[0]=='history' and call[1]==self.saved['run']},A.METRICS)
        self.assertTrue(any(call[0]=='version_uri' for call in sdk.calls))

    def test_sdk_readback_rejects_changed_html_without_loading_any_model(self):
        checked=self.checked(); expected=B.expected_view(checked); sdk=RegistrySDK(expected); sdk.base=checked['clone_artifacts']
        html=checked['clone_artifacts']/A.NAMESPACE/self.saved['run']/'artifacts'/A.ARTIFACT
        html.write_bytes(html.read_bytes().replace(b'Ridge',b'Other'))
        with self.assertRaisesRegex(B.ReadbackError,'ARTIFACT_MISMATCH'): B.sdk_read(checked,expected,factory=sdk.factory)

    def test_historical_backup_is_inert_and_unreferenced_failed_journal_is_preserved(self):
        self.assertFalse((self.root/self.saved['backup']['manifest']).exists())
        self.assertFalse((self.root/'tools').exists())
        failed=self.backup/A.FOLDER/('9'*32); failed.mkdir(mode=0o700)
        R.write(failed/'result.json',A.canonical({'result':'failed','phase':'fit-started'})); self.refresh()
        result=self.restore(); checked=R.inspect_completed(result['token'],root=self.root)
        self.assertEqual((checked['root']/'export'/A.FOLDER/failed.name/'result.json').read_bytes(),(failed/'result.json').read_bytes())

    def test_recovered_html_failure_preserves_original_and_sdk_readback_without_refit(self):
        original,recovery=self.add_recovery(); before=R.inventory(self.backup)
        original_raw=(self.saved['evidence']/'result.json').read_bytes()
        checked=self.checked()
        self.assertEqual(R.inventory(self.backup),before)
        exported=checked['root']/'export'/A.FOLDER/original['evidence_token']
        self.assertEqual((exported/'result.json').read_bytes(),original_raw)
        self.assertEqual(json.loads(original_raw)['result'],'failed')
        self.assertEqual((exported/'recovery.json').read_bytes(),A.canonical(recovery))
        self.assertFalse((self.root/self.saved['backup']['manifest']).exists())
        expected=B.expected_view(checked); sdk=RegistrySDK(expected); sdk.base=checked['clone_artifacts']
        result=B.sdk_read(checked,expected,factory=sdk.factory)
        self.assertEqual(result['result'],'passed'); self.assertEqual(result['counts']['artifacts'],4)
        self.assertEqual(expected['runs'][self.saved['run']]['params'],A.PARAMS)

    def test_recovery_requires_original_failure_and_completed_target_entry(self):
        original,recovery=self.add_recovery(); path=self.saved['evidence']/'recovery.json'
        path.unlink(); self.refresh()
        with self.assertRaisesRegex(R.RestoreError,'AUTOLOG_INVALID'): self.restore()
        R.write(path,A.canonical(recovery))
        self.write_json(self.saved['evidence']/'result.json',self.saved['result'])
        with self.assertRaisesRegex(R.RestoreError,'AUTOLOG_INVALID'): self.restore()
        self.write_json(self.saved['evidence']/'result.json',original)
        index=deepcopy(self.saved['index']); index['runs'][original['training_token']]=recovery['original_entry']
        self.write_json(self.backup/A.INDEX_NAME,index)
        with self.assertRaisesRegex(R.RestoreError,'AUTOLOG_INVALID'): self.restore()

    def test_recovery_cannot_change_observed_values_or_original_entry(self):
        original,recovery=self.add_recovery(); path=self.saved['evidence']/'recovery.json'
        changes=[lambda x:x.update(source_sha256='f'*64),
                 lambda x:x.update(original_result_sha256='f'*64),
                 lambda x:x.update(recovered_at_ms=original['finished_ms']-1),
                 lambda x:x.update(observation='live-sdk-write'),
                 lambda x:x['result'].update(finished_ms=original['finished_ms']+1),
                 lambda x:x['result']['model'].update(intercept=original['model']['intercept']+1),
                 lambda x:x['original_entry'].update(run_id='9'*32)]
        for change in changes:
            bad=deepcopy(recovery); change(bad)
            # A self-consistent entry digest still cannot rebind its completed target.
            bad['original_entry_sha256']=A.digest(bad['original_entry'])
            self.write_json(path,bad)
            with self.assertRaisesRegex(R.RestoreError,'AUTOLOG_INVALID'): self.restore()

    def test_recovery_is_limited_to_post_fit_html_validation_failure(self):
        original,recovery=self.add_recovery()
        for change in [lambda x:x.update(failure_code='AUTOLOG_FAILED'),
                       lambda x:x.update(phase='fit-started'),
                       lambda x:x.update(source_unchanged=False)]:
            failed=deepcopy(original); change(failed)
            adopted=deepcopy(failed); adopted.update(result='verified',failure_code=None)
            bound=deepcopy(recovery); bound.update(original_result_sha256=A.digest(failed),result=adopted)
            self.write_json(self.saved['evidence']/'result.json',failed)
            self.write_json(self.saved['evidence']/'recovery.json',bound)
            with self.assertRaisesRegex(R.RestoreError,'AUTOLOG_INVALID'): self.restore()

    def test_recovery_raw_bytes_are_bound_and_extra_files_remain_rejected(self):
        original,recovery=self.add_recovery()
        for name,value in [('result.json',original),('recovery.json',recovery)]:
            path=self.saved['evidence']/name
            path.write_bytes(json.dumps(value,sort_keys=True,indent=2).encode()+b'\n'); self.refresh()
            with self.assertRaisesRegex(R.RestoreError,'AUTOLOG_INVALID'): self.restore()
            self.write_json(path,value)
        R.write(self.saved['evidence']/'second-recovery.json',A.canonical(recovery)); self.refresh()
        with self.assertRaisesRegex(R.RestoreError,'AUTOLOG_INVALID'): self.restore()

    def test_partial_index_profile_identity_and_existing_experiment_collision_are_refused(self):
        path=self.backup/A.INDEX_NAME; original=deepcopy(self.saved['index'])
        for change in [lambda x:x.update(profile='foreign'),lambda x:x.update(experiment_id='4'),
                       lambda x:x.update(experiment_pending=True),lambda x:x.update(runs={}),
                       lambda x:x['runs'][self.registry['training']['attempt']].update(completed=False,phase='fit-started')]:
            value=deepcopy(original); change(value); self.write_json(path,value)
            with self.assertRaises(R.RestoreError): self.restore()
        self.write_json(path,original)
        path.unlink(); self.refresh()
        with self.assertRaises(R.RestoreError): self.restore()

    def test_source_and_completed_result_cannot_rebind_model_or_training_partition(self):
        paths=[(self.saved['evidence']/'source.json',self.saved['source'],[
                   lambda x:x.update(train_rows=47),lambda x:x.update(train_rows_sha256='f'*64),
                   lambda x:x['binding'].update(run_id='9'*32)]),
               (self.saved['evidence']/'result.json',self.saved['result'],[
                   lambda x:x.update(phase='fit-started'),lambda x:x.update(source_unchanged=False),
                   lambda x:x['model'].update(intercept=x['model']['intercept']+1),
                   lambda x:x.update(backup_sha256='f'*64),lambda x:x['versions'].update(mlflow='other')])]
        for path,original,changes in paths:
            for change in changes:
                altered=deepcopy(original); change(altered); self.write_json(path,altered)
                with self.assertRaises(R.RestoreError): self.restore()
            self.write_json(path,original)

    def test_completed_evidence_requires_exact_three_files_and_bound_inert_backup(self):
        directory=self.saved['evidence']; backup_path=directory/'backup.json'; raw=backup_path.read_bytes()
        backup_path.unlink(); self.refresh()
        with self.assertRaises(R.RestoreError): self.restore()
        R.write(backup_path,raw)
        extra=directory/'model.pkl'; R.write(extra,b'not a model'); self.refresh()
        with self.assertRaises(R.RestoreError): self.restore()
        extra.unlink()
        bad=dict(self.saved['backup'],manifest='../private/backup-manifest.json')
        self.write_json(backup_path,bad)
        with self.assertRaises(R.RestoreError): self.restore()

    def test_only_fixed_html_is_allowed_and_legacy_json_profiles_stay_strict(self):
        path=self.saved['artifact']; original=path.read_bytes()
        for raw in [original+b'changed',b'x'*(A.MAX_ARTIFACT_BYTES+1)]:
            path.write_bytes(raw); self.refresh()
            with self.assertRaises(R.RestoreError): self.restore()
        path.write_bytes(original)
        extra=path.parent/'model.pkl'; R.write(extra,b'inert'); self.refresh()
        with self.assertRaisesRegex(R.RestoreError,'ARTIFACT_MISSING'): self.restore()
        extra.unlink()
        legacy=self.backup/'store/artifacts/iceberg-training'/self.registry['training']['run']/'artifacts'/A.ARTIFACT
        R.write(legacy,original); self.refresh()
        with self.assertRaisesRegex(R.RestoreError,'ARTIFACT_MISSING'): self.restore()

    def test_every_db_param_tag_latest_value_and_history_point_is_bound_to_receipt(self):
        run=self.saved['run']; metric=next(iter(A.METRICS)); value=self.saved['result']['run']['metrics'][metric]
        cases=[('UPDATE params SET value=? WHERE run_uuid=? AND key=?',('2.0',run,'alpha'),('1.0',run,'alpha')),
               ('UPDATE tags SET value=? WHERE run_uuid=? AND key=?',('foreign',run,'estimator_name'),('Ridge',run,'estimator_name')),
               ('UPDATE latest_metrics SET value=? WHERE run_uuid=? AND key=?',(value+1,run,metric),(value,run,metric)),
               ('UPDATE metrics SET step=? WHERE run_uuid=? AND key=?',(1,run,metric),(0,run,metric))]
        for statement,bad,good in cases:
            self.sql(statement,bad)
            with self.assertRaises(R.RestoreError): self.restore()
            self.sql(statement,good)
        self.sql('INSERT INTO metrics (key,value,timestamp,step,run_uuid,is_nan) VALUES (?,?,?,?,?,?)',
                 (metric,value,self.saved['result']['started_ms']+51,0,run,0))
        with self.assertRaises(R.RestoreError): self.restore()

    def test_autolog_experiment_tags_run_status_and_uri_are_fixed(self):
        self.sql('UPDATE experiment_tags SET value=? WHERE experiment_id=6 AND key=?',('foreign','shirokuma.profile'))
        with self.assertRaises(R.RestoreError): self.restore()
        self.sql('UPDATE experiment_tags SET value=? WHERE experiment_id=6 AND key=?',(A.PROFILE,'shirokuma.profile'))
        self.sql('UPDATE runs SET status=? WHERE run_uuid=?',('RUNNING',self.saved['run']))
        with self.assertRaises(R.RestoreError): self.restore()
        self.sql('UPDATE runs SET status=?,artifact_uri=? WHERE run_uuid=?',('FINISHED','file:///outside',self.saved['run']))
        with self.assertRaises(R.RestoreError): self.restore()


if __name__=='__main__':
    unittest.main()
