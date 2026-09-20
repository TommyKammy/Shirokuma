"""Offline Registry tests: real saved JSON validation, fake SDK and private SQLite."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.local_experiment.mlflow import registry as G
from tools.local_experiment.mlflow import registry_contract as C
from tests import test_notebook_lake_training_client as L


def database(path):
    with sqlite3.connect(path) as db:
        db.executescript('''
        CREATE TABLE registered_models(workspace TEXT,name TEXT,creation_time INTEGER,last_updated_time INTEGER,description TEXT);
        CREATE TABLE registered_model_tags(workspace TEXT,name TEXT,key TEXT,value TEXT);
        CREATE TABLE model_versions(workspace TEXT,name TEXT,version INTEGER,creation_time INTEGER,last_updated_time INTEGER,
          description TEXT,user_id TEXT,current_stage TEXT,source TEXT,storage_location TEXT,run_id TEXT,
          status TEXT,status_message TEXT,run_link TEXT);
        CREATE TABLE model_version_tags(workspace TEXT,name TEXT,version INTEGER,key TEXT,value TEXT);
        CREATE TABLE registered_model_aliases(workspace TEXT,name TEXT,alias TEXT,version INTEGER);
        ''')
    path.chmod(0o600)


class FakeClient:
    """Match pinned SDK entity shape; simulate committed mutation / lost response."""
    def __init__(self, path):
        self.path, self.calls, self.fail = path, [], None

    def create_registered_model(self, name, *, tags):
        self.calls.append('create-model')
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO registered_models VALUES(?,?,?,?,?)', ('default', name, 2000, 2000, None))
            db.executemany('INSERT INTO registered_model_tags VALUES(?,?,?,?)',
                           [('default', name, k, v) for k, v in tags.items()])
        if self.fail == 'model': raise RuntimeError('private lost reply')
        return self.get_registered_model(name)

    def get_registered_model(self, name):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT * FROM registered_models WHERE name=?', (name,)).fetchone()
            tags = dict(db.execute('SELECT key,value FROM registered_model_tags WHERE name=?', (name,)))
        return SimpleNamespace(name=name, creation_timestamp=row[2], last_updated_timestamp=row[3],
                               description=row[4], tags=tags, aliases={}, workspace='default')

    def create_model_version(self, name, *, source, run_id, tags, await_creation_for):
        assert await_creation_for == 0
        self.calls.append('create-version')
        with sqlite3.connect(self.path) as db:
            version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM model_versions').fetchone()[0]
            db.execute('UPDATE registered_models SET last_updated_time=?', (2000+version,))
            db.execute('INSERT INTO model_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('default',name,version,2000+version,2000+version,None,None,'None',source,source,run_id,'READY',None,None))
            db.executemany('INSERT INTO model_version_tags VALUES(?,?,?,?,?)',
                           [('default',name,version,k,v) for k,v in tags.items()])
        if isinstance(self.fail, BaseException): raise self.fail
        if self.fail == 'version': raise RuntimeError('private lost reply')
        return self.get_model_version(name, version)

    def get_model_version(self, name, version):
        with sqlite3.connect(self.path) as db:
            db.row_factory=sqlite3.Row
            row=dict(db.execute('SELECT * FROM model_versions WHERE name=? AND version=?',(name,version)).fetchone())
            tags=dict(db.execute('SELECT key,value FROM model_version_tags WHERE name=? AND version=?',(name,version)))
        row.update(creation_timestamp=row.pop('creation_time'),last_updated_timestamp=row.pop('last_updated_time'),
                   tags=tags,aliases=[])
        row.pop('storage_location')
        return SimpleNamespace(**row)

    def get_model_version_download_uri(self, name, version):
        return self.get_model_version(name,version).source


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=L.LakeTrainingReviewTests()
        self.addCleanup(self.fixture.doCleanups); self.fixture.setUp()
        self.state=self.fixture.state; self.db=self.state/'store/tracking.sqlite'; database(self.db)
        for name,value in [('STATE',self.state),('ROOT',self.fixture.root)]:
            change=patch.object(G,name,value); change.start(); self.addCleanup(change.stop)
        change=patch.object(G.R,'require_store',return_value=self.fixture.marker)
        change.start(); self.addCleanup(change.stop)
        self.client=FakeClient(self.db)
        self.owner=G.Registrar(self.client,store_id=L.STORE,backup={'store_id':L.STORE})

    def files(self):
        return {str(p.relative_to(self.state)):(p.read_bytes(),p.stat().st_mtime_ns,p.stat().st_mode)
                for p in self.state.rglob('*') if p.is_file()}

    def test_first_register_two_mutations_and_repeat_no_sdk_or_file_writes(self):
        public=self.owner.register(L.TOKEN)
        self.assertEqual(self.client.calls,['create-model','create-version'])
        self.assertEqual(public['version'],'1'); self.assertEqual(public['binding']['training_token'],L.TOKEN)
        before=self.files()
        with patch.object(self.client,'get_registered_model',side_effect=AssertionError('SDK forbidden')):
            self.assertEqual(self.owner.register(L.TOKEN),public)
            stream=io.StringIO()
            with contextlib.redirect_stdout(stream),patch.object(G.R,'require_stopped',side_effect=AssertionError('runtime forbidden')):
                self.assertEqual(G.main(['register','--training-token',L.TOKEN,'--backup-manifest','unused']),0)
            self.assertEqual(json.loads(stream.getvalue()),public)
        self.assertEqual(before,self.files())
        self.assertEqual(self.fixture.calls,[])

    def test_resolve_fresh_sqlite_retains_bytes_and_matches_existing_predict_token(self):
        saved=self.owner.register(L.TOKEN); before=self.files()
        public=G.resolve(C.MODEL_NAME,'1')
        self.assertEqual(public,dict(saved,observation='sqlite-read-only'))
        from tools.local_experiment.mlflow import predict as P
        prediction=P.predict(public['binding']['training_token'],{'schema_version':1,'rows':[{'x1':0,'x2':0,'x3':0}]})
        self.assertEqual(prediction['predictions'],[4.0])
        self.assertEqual(prediction['model_sha256'],public['binding']['model_sha256'])
        self.assertEqual(before,self.files())

    def test_second_training_gets_new_version_without_replacing_first_receipt(self):
        first=self.owner.register(L.TOKEN)
        original=(self.fixture.root/first['evidence']).read_bytes()
        token,run='1'*32,'2'*32
        receipt=dict(self.fixture.receipt,token=token,run_id=run)
        self.fixture.write(self.state/'lake-training'/token/'result.json',receipt)
        self.fixture.write(self.state/'lake-training'/token/'workload.json',self.fixture.workload)
        self.fixture.write(self.state/'store/artifacts/iceberg-training'/run/'artifacts/safe-projection.json',self.fixture.workload)
        second=self.owner.register(token)
        self.assertEqual(second['version'],'2')
        self.assertEqual(self.client.calls,['create-model','create-version','create-version'])
        self.assertEqual(G.recorded(L.TOKEN)['model_version']['version'],'1')
        self.assertEqual(G.resolve(C.MODEL_NAME,'1')['binding']['training_token'],L.TOKEN)
        self.assertEqual(G.resolve(C.MODEL_NAME,'2')['binding']['training_token'],token)
        self.assertEqual((self.fixture.root/first['evidence']).read_bytes(),original)

    def test_lost_model_and_version_responses_leave_pending_and_never_resend(self):
        self.client.fail='version'
        with self.assertRaisesRegex(C.RegistryError,'REGISTRY_OUTCOME_UNKNOWN'): self.owner.register(L.TOKEN)
        calls=self.client.calls[:]
        with self.assertRaisesRegex(C.RegistryError,'REGISTRY_PRIOR_OUTCOME_UNKNOWN'): self.owner.register(L.TOKEN)
        self.assertEqual(self.client.calls,calls)
        index=json.loads((self.state/C.INDEX_NAME).read_bytes())
        self.assertEqual(index['entries'][L.TOKEN]['state'],'pending')
        with sqlite3.connect(self.db) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM model_versions').fetchone()[0],1)

    def test_lost_model_response_never_adopts_or_retries(self):
        self.client.fail='model'
        with self.assertRaisesRegex(C.RegistryError,'REGISTRY_OUTCOME_UNKNOWN'): self.owner.register(L.TOKEN)
        with self.assertRaisesRegex(C.RegistryError,'REGISTRY_PRIOR_OUTCOME_UNKNOWN'): G.recorded(L.TOKEN)
        self.assertEqual(self.client.calls,['create-model'])

    def test_first_interrupt_survives_secondary_receipt_failure(self):
        interruption=KeyboardInterrupt(); self.client.fail=interruption
        original=G.E.persist
        def persist(path,value):
            if path.name=='result.json': raise OSError('second')
            return original(path,value)
        with patch.object(G.E,'persist',side_effect=persist),self.assertRaises(KeyboardInterrupt) as caught:
            self.owner.register(L.TOKEN)
        self.assertIs(caught.exception,interruption)
        self.assertEqual(json.loads((self.state/C.INDEX_NAME).read_bytes())['entries'][L.TOKEN]['state'],'pending')

    def test_mismatched_artifact_and_symlink_fail_before_mutation(self):
        self.fixture.artifact.write_bytes(b'{}\n')
        with self.assertRaisesRegex(C.RegistryError,'REGISTRY_SOURCE_INVALID'):self.owner.register(L.TOKEN)
        self.assertEqual(self.client.calls,[])
        self.fixture.save_workload(self.fixture.workload)
        self.fixture.artifact.unlink(); self.fixture.artifact.symlink_to(self.fixture.workload_path)
        with self.assertRaises(C.RegistryError):self.owner.register(L.TOKEN)
        self.assertEqual(self.client.calls,[])

    def test_resolve_refuses_alias_stage_source_and_unrecorded_versions(self):
        self.owner.register(L.TOKEN)
        for column,value in [('current_stage','Production'),('source','file:///tmp/foreign.json'),('run_id','e'*32),('storage_location','file:///tmp/foreign.json')]:
            with self.subTest(column=column):
                with sqlite3.connect(self.db) as db:
                    old=db.execute('SELECT '+column+' FROM model_versions').fetchone()[0]
                    db.execute('UPDATE model_versions SET '+column+'=?',(value,))
                with self.assertRaises(C.RegistryError): G.resolve(C.MODEL_NAME,'1')
                with sqlite3.connect(self.db) as db:db.execute('UPDATE model_versions SET '+column+'=?',(old,))
        with sqlite3.connect(self.db) as db:db.execute('INSERT INTO registered_model_aliases VALUES(?,?,?,?)',('default',C.MODEL_NAME,'champion',1))
        with self.assertRaises(C.RegistryError):G.resolve(C.MODEL_NAME,'1')

    def test_foreign_registry_model_fails_before_sdk_write(self):
        with sqlite3.connect(self.db) as db:db.execute('INSERT INTO registered_models VALUES(?,?,?,?,?)',('default','foreign',1,1,None))
        with self.assertRaisesRegex(C.RegistryError,'REGISTRY_FOREIGN_CONTENT'):self.owner.register(L.TOKEN)
        self.assertEqual(self.client.calls,[])

    def test_saved_receipt_tamper_and_input_path_refusal(self):
        public=self.owner.register(L.TOKEN)
        path=self.fixture.root/public['evidence']; result=json.loads(path.read_bytes())
        result['model_version']['run_id']='f'*32; self.fixture.write(path,result)
        with self.assertRaises(C.RegistryError):G.recorded(L.TOKEN)
        for version in ('latest','0','01',True,1.0,'../1'):
            with self.subTest(version=version),self.assertRaises(C.RegistryError):G.resolve(C.MODEL_NAME,version)
        with self.assertRaises(C.RegistryError):G.resolve('foreign','1')

    def test_source_change_after_create_keeps_unknown_pending(self):
        original=self.client.create_model_version
        def changed(*args,**kwargs):
            entity=original(*args,**kwargs); self.fixture.artifact.write_bytes(b'{}\n'); return entity
        with patch.object(self.client,'create_model_version',side_effect=changed):
            with self.assertRaisesRegex(C.RegistryError,'REGISTRY_OUTCOME_UNKNOWN'):self.owner.register(L.TOKEN)
        self.assertEqual(json.loads((self.state/C.INDEX_NAME).read_bytes())['entries'][L.TOKEN]['state'],'pending')

    def test_stale_cold_backup_refused_before_mutation(self):
        from tools.local_experiment.mlflow import restore as B
        saved={'manifest':{'store_id':L.STORE,'inventory':{'store':{'kind':'directory'}}},
               'path':self.fixture.root/'backup-manifest.json','raw':b'original'}
        with patch.object(B,'validate_export',return_value=saved),patch.object(B,'inventory',return_value=({'changed':{}},{})):
            with self.assertRaisesRegex(C.RegistryError,'REGISTRY_BACKUP_STALE'):G.validate_backup('backup',L.STORE)
        self.assertEqual(self.client.calls,[])


if __name__=='__main__':unittest.main()
