"""Owned Registry restore and SDK readback using disposable SQLite and fake SDK only."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from tools.local_experiment.mlflow import restore as R, restore_readback as B, registry_contract as K
from tests.test_local_mlflow_restore import fixture, refresh, STORE_ID
from tests.test_local_mlflow_training_restore import add_training
from tests.test_local_mlflow_restore_readback import FakeSDK


def add_registry(root, backup, *, null_storage=False):
    training = add_training(root, backup, ('4',))['4']
    binding = K.binding(training['receipt'], training['workload'], store_id=STORE_ID)
    source = (root / R.SOURCE / 'store/artifacts' / binding['artifact_relpath']).as_uri()
    stamp = 1700000010000
    model = dict(name=K.MODEL_NAME, creation_timestamp=stamp, last_updated_timestamp=stamp,
                 description=None, tags=K.model_tags(STORE_ID), aliases={})
    version = dict(name=K.MODEL_NAME, version='1', creation_timestamp=stamp, last_updated_timestamp=stamp,
        description=None, user_id=None, current_stage='None', source=source, run_id=binding['run_id'],
        status='READY', status_message=None, run_link=None, tags=K.version_tags(binding), aliases=[])
    evidence_token = '6' * 32
    index = dict(schema_version=1, profile=K.PROFILE, store_id=STORE_ID, model_name=K.MODEL_NAME,
        model_pending=None, model=model, entries={binding['training_token']: dict(state='complete',
            binding=binding, evidence_token=evidence_token, version=version)})
    evidence = backup / 'registry' / evidence_token
    evidence.mkdir(parents=True, mode=0o700); evidence.parent.chmod(0o700)
    R.write(evidence / 'source.json', K.canonical(dict(schema_version=1, profile=K.PROFILE, binding=binding)))
    R.write(evidence / 'backup.json', K.canonical(dict(
        manifest='tmp/local-experiment/fake-registry-prior/backup-manifest.json',
        sha256='8'*64, store_id=STORE_ID)))
    result = dict(schema_version=1, profile=K.PROFILE, evidence_token=evidence_token, result='verified',
        binding=binding, model=model, model_version=version, created_model=True, created_version=True,
        source_unchanged=True, error_code=None, started_ms=stamp-100, finished_ms=stamp+100)
    R.write(evidence / 'result.json', K.canonical(result))
    R.write(backup / 'registry-index.json', K.canonical(index))
    with sqlite3.connect(backup / 'store/tracking.sqlite') as db:
        db.execute('DROP TABLE registered_models')
        db.executescript('''
          CREATE TABLE registered_models (workspace TEXT,name TEXT,creation_time INTEGER,last_updated_time INTEGER,
            description TEXT,PRIMARY KEY(workspace,name));
          CREATE TABLE registered_model_tags (workspace TEXT,name TEXT,key TEXT,value TEXT,
            PRIMARY KEY(workspace,name,key),FOREIGN KEY(workspace,name) REFERENCES registered_models(workspace,name));
          CREATE TABLE model_versions (workspace TEXT,name TEXT,version INTEGER,creation_time INTEGER,
            last_updated_time INTEGER,description TEXT,user_id TEXT,current_stage TEXT,source TEXT,storage_location TEXT,
            run_id TEXT,run_link TEXT,status TEXT,status_message TEXT,PRIMARY KEY(workspace,name,version),
            FOREIGN KEY(workspace,name) REFERENCES registered_models(workspace,name));
          CREATE TABLE model_version_tags (workspace TEXT,name TEXT,version INTEGER,key TEXT,value TEXT,
            PRIMARY KEY(workspace,name,version,key),FOREIGN KEY(workspace,name,version)
              REFERENCES model_versions(workspace,name,version));
          CREATE TABLE registered_model_aliases (workspace TEXT,name TEXT,alias TEXT,version INTEGER);
        ''')
        db.execute('INSERT INTO registered_models VALUES (?,?,?,?,?)', ('default',K.MODEL_NAME,stamp,stamp,None))
        db.executemany('INSERT INTO registered_model_tags VALUES (?,?,?,?)',
            [('default', K.MODEL_NAME, key, value) for key, value in model['tags'].items()])
        db.execute('INSERT INTO model_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('default',K.MODEL_NAME,1,stamp,stamp,None,None,'None',source,None if null_storage else source,
             binding['run_id'],None,'READY',None))
        db.executemany('INSERT INTO model_version_tags VALUES (?,?,?,?,?)',
            [('default',K.MODEL_NAME,1,key,value) for key,value in version['tags'].items()])
        db.execute('ALTER TABLE runs ADD COLUMN user_id TEXT')
    return dict(training=training, binding=binding, model=model, version=version, index=index, result=result, evidence=evidence)


class RegistrySDK(FakeSDK):
    def search_registered_models(self, **kwargs):
        self.calls.append(('registered_models', kwargs))
        return [NS(name=self.expected['registry']['model']['name'])]
    def get_registered_model(self, name):
        self.calls.append(('model', name))
        registry = self.expected['registry']
        latest = self.get_model_version(name, max(registry['versions'], key=int))
        return NS(**deepcopy(registry['model']), latest_versions=[latest])
    def search_model_versions(self, **kwargs):
        self.calls.append(('versions', kwargs))
        return [NS(name=self.expected['registry']['model']['name'], version=int(key))
                for key in self.expected['registry']['versions']]
    def get_model_version(self, name, version):
        self.calls.append(('version', name, version))
        record = deepcopy(self.expected['registry']['versions'][version]); record['version'] = int(record['version'])
        return NS(**record)
    def get_model_version_download_uri(self, name, version):
        self.calls.append(('version_uri', name, version))
        registry = self.expected['registry']
        return registry['storage_locations'][version] or registry['versions'][version]['source']


class RegistryRestoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.manifest, self.backup = fixture(self.root)
        self.saved = add_registry(self.root, self.backup)
        refresh(self.root, self.backup, self.manifest)

    def sql(self, statement, parameters=()):
        with sqlite3.connect(self.backup / 'store/tracking.sqlite') as db: db.execute(statement, parameters)
        refresh(self.root, self.backup, self.manifest)

    def json_change(self, path, change):
        value = json.loads(path.read_bytes()); change(value); path.write_bytes(K.canonical(value))
        refresh(self.root, self.backup, self.manifest)

    def restore(self):
        return R.restore(self.manifest, root=self.root)

    def test_only_owned_uri_columns_change_and_every_export_byte_stays_fixed(self):
        before = R.inventory(self.backup); manifest = self.manifest.read_bytes()
        result = self.restore(); checked = R.inspect_completed(result['token'], root=self.root)
        self.assertEqual(result['result'], 'verified'); self.assertFalse(result['activation_performed'])
        self.assertEqual(result['database']['artifact_uri_updates'], {'experiments':4,'runs':3,
            'model_versions.source':1,'model_versions.storage_location':1})
        self.assertEqual(R.inventory(self.backup), before); self.assertEqual(self.manifest.read_bytes(), manifest)
        self.assertEqual(R.inventory(checked['root'] / 'export')[0], before[0])
        for table, proof in result['database']['tables'].items():
            self.assertEqual(proof['before_sha256'] == proof['after_sha256'], table not in ('experiments','runs','model_versions'))
        view = R.registry_view(checked['expected_snapshot'][1]); version = view['versions']['1']
        expected = (checked['clone_artifacts'] / self.saved['binding']['artifact_relpath']).as_uri()
        self.assertEqual(version['source'], expected); self.assertEqual(view['storage_locations']['1'], expected)
        self.assertEqual(version['current_stage'], 'None'); self.assertEqual(version['aliases'], [])
        self.assertFalse((checked['root'] / 'clone/registry').exists())

    def test_null_storage_location_keeps_fallback_instead_of_inventing_uri(self):
        self.sql('UPDATE model_versions SET storage_location=NULL')
        result = self.restore(); checked = R.inspect_completed(result['token'], root=self.root)
        self.assertEqual(result['database']['artifact_uri_updates']['model_versions.storage_location'], 0)
        self.assertIsNone(R.registry_view(checked['expected_snapshot'][1])['storage_locations']['1'])
        expected = B.expected_view(checked); sdk = RegistrySDK(expected); sdk.base = checked['clone_artifacts']
        with patch.dict('sys.modules', {'mlflow': None}):
            self.assertEqual(B.sdk_read(checked, expected, factory=sdk.factory)['result'], 'passed')

    def test_composite_uri_plan_updates_only_target_version_and_nonnull_column(self):
        before = {'model_versions': {'columns':['workspace','name','version','source','storage_location'],
            'rows':[('default',K.MODEL_NAME,1,'old1',None),('default',K.MODEL_NAME,2,'old2','old2')]}}
        changes = {'model_versions.source':[(('default',K.MODEL_NAME,1),'new1'),(('default',K.MODEL_NAME,2),'new2')],
                   'model_versions.storage_location':[(('default',K.MODEL_NAME,2),'new2')]}
        self.assertEqual(R.changed_tables(before, changes)['model_versions']['rows'],
            [('default',K.MODEL_NAME,1,'new1',None),('default',K.MODEL_NAME,2,'new2','new2')])

    def test_foreign_name_workspace_stage_status_and_alias_are_refused(self):
        statements = ["UPDATE registered_models SET name='foreign'", "UPDATE registered_models SET workspace='other'",
            "UPDATE model_versions SET current_stage='Production'", "UPDATE model_versions SET status='PENDING_REGISTRATION'",
            "INSERT INTO registered_model_aliases VALUES ('default','shirokuma-local-ridge','champion',1)"]
        database = self.backup / 'store/tracking.sqlite'; original = database.read_bytes()
        for statement in statements:
            with self.subTest(statement=statement):
                self.sql(statement)
                with self.assertRaises(R.RestoreError): self.restore()
                database.write_bytes(original); refresh(self.root,self.backup,self.manifest)

    def test_source_storage_run_and_tag_binding_refuse_escape_or_other_artifact(self):
        database = self.backup / 'store/tracking.sqlite'; original = database.read_bytes()
        for column, value in [('source','https://example.invalid/model'), ('storage_location','file:///outside.json'),
                              ('source',self.saved['version']['source'].replace('safe-projection.json','MLmodel')),
                              ('run_id','a'*32), ('storage_location',self.saved['version']['source']+'?copy=1')]:
            with self.subTest(column=column,value=value):
                self.sql('UPDATE model_versions SET '+column+'=?',(value,))
                with self.assertRaises(R.RestoreError): self.restore()
                database.write_bytes(original); refresh(self.root,self.backup,self.manifest)
        self.sql("UPDATE model_version_tags SET value='foreign' WHERE key='shirokuma.store_id'")
        with self.assertRaises(R.RestoreError): self.restore()

    def test_missing_pending_unindexed_and_altered_completed_registration_are_refused(self):
        path = self.backup / 'registry-index.json'; original = path.read_bytes()
        for change in [lambda v:v.update(model_pending='7'*32),lambda v:v['entries'].clear(),
                       lambda v:v['entries'][self.saved['binding']['training_token']].update(state='pending')]:
            with self.subTest(change=change):
                self.json_change(path,change)
                with self.assertRaises(R.RestoreError): self.restore()
                path.write_bytes(original); refresh(self.root,self.backup,self.manifest)
        self.json_change(self.saved['evidence']/'result.json',lambda v:v.update(source_unchanged=False))
        with self.assertRaises(R.RestoreError): self.restore()

    def test_safe_json_and_training_receipt_are_both_bound_to_registered_version(self):
        artifact = self.backup / 'store/artifacts' / self.saved['binding']['artifact_relpath']
        original = artifact.read_bytes(); artifact.write_bytes(original+b' ');refresh(self.root,self.backup,self.manifest)
        with self.assertRaises(R.RestoreError): self.restore()
        artifact.write_bytes(original);refresh(self.root,self.backup,self.manifest)
        path = self.backup/'lake-training'/self.saved['binding']['training_token']/'result.json'
        self.json_change(path,lambda v:v.update(run_id='a'*32))
        with self.assertRaises(R.RestoreError): self.restore()

    def test_unreferenced_failed_journal_remains_inert_and_preserved(self):
        unused = self.backup/'registry'/('9'*32);unused.mkdir(mode=0o700)
        R.write(unused/'result.json',b'{"result":"failed","phase":"before registration"}\n')
        refresh(self.root,self.backup,self.manifest)
        before = R.inventory(self.backup); result=self.restore()
        checked=R.inspect_completed(result['token'],root=self.root)
        self.assertEqual(R.inventory(self.backup),before)
        self.assertEqual((checked['root']/'export/registry'/('9'*32)/'result.json').read_bytes(),(unused/'result.json').read_bytes())

    def test_registration_backup_identity_is_validated_and_preserved_without_external_read(self):
        path = self.saved['evidence'] / 'backup.json'; raw = path.read_bytes()
        record = json.loads(raw)
        self.assertEqual({p.name for p in self.saved['evidence'].iterdir()}, {'source.json','result.json','backup.json'})
        self.assertFalse((self.root / record['manifest']).exists())
        result = self.restore(); checked = R.inspect_completed(result['token'], root=self.root)
        self.assertEqual((checked['root']/'export/registry'/self.saved['evidence'].name/'backup.json').read_bytes(), raw)
        for changes in [dict(store_id='00000000-0000-4000-8000-000000000000'), dict(sha256='bad'),
                        dict(manifest='../outside/backup-manifest.json'), dict(manifest='https://example.invalid/backup-manifest.json'),
                        dict(manifest='tmp/local-experiment/fake/result.json'), dict(extra=True)]:
            with self.subTest(changes=changes):
                path.write_bytes(K.canonical(dict(record, **changes))); refresh(self.root,self.backup,self.manifest)
                with self.assertRaises(R.RestoreError): self.restore()
                path.write_bytes(raw); refresh(self.root,self.backup,self.manifest)


class RegistryReadbackTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.manifest,self.backup=fixture(self.root)
        self.saved=add_registry(self.root,self.backup);refresh(self.root,self.backup,self.manifest)
        self.result=R.restore(self.manifest,root=self.root)
        self.checked=R.inspect_completed(self.result['token'],root=self.root)
        self.expected=B.expected_view(self.checked);self.sdk=RegistrySDK(self.expected)
        self.sdk.base=self.checked['clone_artifacts']

    def read(self):
        with patch.dict('sys.modules',{'mlflow':None}):
            return B.sdk_read(self.checked,self.expected,factory=self.sdk.factory)

    def test_exact_version_sdk_read_and_local_json_hash_without_loader_or_fit(self):
        before=B.seal(self.checked);snapshot=B.logical_digest(R.logical_snapshot(self.checked['clone_db']))
        result=self.read()
        self.assertEqual(result['counts'],dict(experiments=4,runs=3,metrics=13,artifacts=3,registered_models=1,model_versions=1))
        self.assertIn(('version_uri',K.MODEL_NAME,'1'),self.sdk.calls)
        self.assertEqual(B.seal(self.checked),before)
        self.assertEqual(B.logical_digest(R.logical_snapshot(self.checked['clone_db'])),snapshot)
        self.assertEqual(self.sdk.kwargs['registry_uri'],'sqlite:///'+str(self.checked['clone_db']))

    def test_sdk_version_int_or_canonical_string_is_exact_but_bool_float_are_refused(self):
        original=self.sdk.get_model_version
        for value in ['1',True,1.0,'01']:
            def changed(name,key):
                entity=original(name,key);entity.version=value;return entity
            with self.subTest(value=value),patch.object(self.sdk,'get_model_version',side_effect=changed):
                if value=='1':self.assertEqual(self.read()['result'],'passed')
                else:
                    with self.assertRaises(B.ReadbackError):self.read()

    def test_sdk_version_owner_fields_alias_and_uri_must_match_clone(self):
        record=self.sdk.expected['registry']['versions']['1']; original=deepcopy(record)
        for key,value in [('current_stage','Production'),('aliases',['champion']),('run_id','a'*32),
                          ('source',self.saved['version']['source']),('workspace','other')]:
            with self.subTest(key=key):
                record[key]=value
                with self.assertRaises(B.ReadbackError):self.read()
                record.clear();record.update(deepcopy(original))
        with patch.object(self.sdk,'get_model_version_download_uri',return_value=self.saved['version']['source']):
            with self.assertRaises(B.ReadbackError):self.read()

    def test_missing_extra_paginated_model_or_version_is_not_complete(self):
        class Page(list):token='more'
        for method, value in [('search_registered_models',[]),('search_registered_models',[NS(name=K.MODEL_NAME)]*2),
                              ('search_registered_models',Page([NS(name=K.MODEL_NAME)])),
                              ('search_model_versions',[]),('search_model_versions',Page([NS(name=K.MODEL_NAME,version=1)]))]:
            with self.subTest(method=method),patch.object(self.sdk,method,return_value=value),self.assertRaises(B.ReadbackError):self.read()

    def test_readback_receipt_includes_registry_and_all_retention_checks(self):
        before=B.seal(self.checked)
        def runner(evidence,checked):
            R.write(evidence/'worker-result.json',R.canonical(self.read()))
        result=B.readback(self.result['token'],root=self.root,runner=runner)
        self.assertEqual(result['result'],'passed');self.assertTrue(result['logical_unchanged'] and result['retained_unchanged'])
        self.assertEqual(result['counts']['model_versions'],1);self.assertEqual(B.seal(self.checked),before)


if __name__=='__main__':unittest.main()
