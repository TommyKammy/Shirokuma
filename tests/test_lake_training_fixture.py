"""Fake-only lifecycle tests for the disposable lake training source owner."""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.lake_training import fixture as F

TOKEN = 'a'*32
UUID = '11111111-1111-4111-8111-111111111111'


class Harness:
    def __init__(self, output):
        self.output = output
        self.exists = False
        self.active = False
        self.sql = []
        self.events = []
        self.loads = 0
        self.phase = 0
        self.failure = None
        self.reader_ok = True
        self.drift = None
        self.callback_error = None
        self.drop_error = None
        self.persisted_create = False
        self.show_ddl = None

    def metadata(self):
        self.loads += 1
        value = {'metadata-location':'s3://shirokuma-lakehouse/local/training/metadata/00001.metadata.json',
            'metadata':{'format-version':2,'table-uuid':UUID,'location':'s3://shirokuma-lakehouse/local/training',
            'current-schema-id':0,'schemas':[{'schema-id':0,'type':'struct','fields':[
                {'id':i+1,'name':col['name'],'type':'long' if i==0 else 'double','required':False}
                for i,col in enumerate(F.COLUMNS)]}], 'partition-specs':[{'spec-id':0,'fields':[]}],
            'default-spec-id':0,'snapshots':[{'snapshot-id':123}], 'current-snapshot-id':123,
            'refs':{'main':{'type':'branch','snapshot-id':123}}, 'properties':{'comment':F.marker(TOKEN)}}}
        if self.failure=='initial_uuid': raise ValueError('private catalog failure')
        if self.loads==2 and self.drift=='uuid': value['metadata']['table-uuid']='22222222-2222-4222-8222-222222222222'
        if self.loads==2 and self.drift=='snapshot':
            value['metadata']['current-snapshot-id']=124
            value['metadata']['snapshots'].append({'snapshot-id':124})
            value['metadata']['refs']['main']['snapshot-id']=124
        if self.loads==2 and self.drift=='rest_marker': value['metadata']['properties']['comment']='someone else'
        return value

    def query(self, client, sql):
        assert self.active
        self.sql.append(sql)
        record={'query_id':'q'+str(len(self.sql)),'response_complete':False,'result':'running'}
        client.records.append(record)
        if client.progress: client.progress()
        if sql.startswith('CREATE TABLE'):
            saved=json.loads((self.output/'report.json').read_text())
            assert saved['pending']=='create' and saved['steps'][-1]['outcome']=='pending'
            self.persisted_create=True;self.events.append('create');self.exists=True
            if self.failure=='create': raise ValueError('private lost create response')
            value={'columns':[],'column_types':[],'rows':[]}
        elif sql.startswith('DROP TABLE'):
            self.events.append('drop');self.exists=False
            if self.drop_error is not None: raise self.drop_error
            if self.failure=='drop': raise ValueError('private lost drop response')
            value={'columns':[],'column_types':[],'rows':[]}
        elif sql.startswith('SHOW CREATE'):
            marker='changed' if self.phase==2 and self.drift=='sql_marker' else F.marker(TOKEN)
            value={'columns':['Create Table'],'column_types':['varchar'],'rows':[["CREATE TABLE x COMMENT '"+marker+"'"]]}
            if self.show_ddl is not None: value=deepcopy(self.show_ddl)
        else:
            key=next(key for key,text in F.REFS.FIXTURE_SQL.items() if text==sql)
            values={
                'schema':(['column_name','data_type'],['varchar','varchar'],[['id','bigint'],['name','varchar']]),
                'rows':(['id','name'],['bigint','varchar'],[[1,'shirokuma'],[2,'polaris']]),
                'snapshots':(['snapshot_id','parent_id','operation'],['bigint','bigint','varchar'],[[321,None,'append']]),
                'history':(['snapshot_id','parent_id','is_current_ancestor'],['bigint','bigint','boolean'],[[321,None,True]]),
                'refs':(['name','type','snapshot_id'],['varchar','varchar','bigint'],[['main','BRANCH',321]]),
                'tables':(['table_catalog','table_schema','table_name'],['varchar']*3,[['iceberg','smoke','fixture_v1']])}
            cols,types,rows=deepcopy(values[key])
            if key=='tables' and self.exists: rows.append(['iceberg','local_experiment',F.table_name(TOKEN)]);rows.sort()
            if self.phase==2 and self.drift=='retained' and key=='refs': rows[0][2]=322
            if self.phase==2 and self.drift=='inventory' and key=='tables': rows.append(['iceberg','smoke','unrelated'])
            value={'columns':cols,'column_types':types,'rows':rows}
        record.update(response_complete=True,result='finished')
        if client.progress: client.progress()
        return dict(value,query_id=record['query_id'])

    @contextmanager
    def runtime(self, token, progress):
        assert token==TOKEN and not self.active
        self.phase+=1;self.active=True;self.events.append('open'+str(self.phase))
        progress({'state':'ready','cleanup_verified':False})
        harness=self
        class Client:
            def __init__(self, control):
                self.user=F.SB.run_user(TOKEN,control);self.timeout=15;self.records=[];self.progress=None
            def query(self, sql): return harness.query(self,sql)
        class Catalog:
            progress=None
            def authenticate(self):
                if self.progress:self.progress()
            def load(self, request):
                assert request['table']=='local_experiment.'+F.table_name(TOKEN)
                return harness.metadata()
            def receipt(self): return {'reads':harness.loads}
        try:
            yield Client(False),Client(True),Catalog(),lambda:None
        finally:
            self.active=False;self.events.append('close'+str(self.phase))
            closed=not (self.phase==1 and self.failure=='tunnel')
            progress({'state':'verified' if closed else 'failed','cleanup_verified':closed})
            if not closed:raise ValueError('private tunnel failure')

    def drain(self, control, worker, token):
        assert self.active and token==TOKEN
        self.events.append('drain')
        return {'verified':self.failure!='drain'}

    def callback(self, binding):
        assert not self.active
        self.events.append('reader')
        assert binding['request']['limit']==100 and binding['expected_rows']==64
        assert binding['identity']['table_uuid']==UUID
        if self.callback_error is not None: raise self.callback_error
        return {'reader_value':'kept only in caller'}

    def closed(self):
        assert not self.active
        self.events.append('reader_closed')
        return self.reader_ok


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.root.chmod(0o700)
        self.output=self.root/'fixture';self.h=Harness(self.output)

    def run_owner(self):
        return F.run(self.output,TOKEN,self.h.callback,reader_closed=self.h.closed,
                     _runtime=self.h.runtime,_drain=self.h.drain)

    def test_success_serializes_tunnels_binds_identity_and_retains_original_data(self):
        report,value=self.run_owner()
        self.assertEqual(report['result'],'passed');self.assertTrue(self.h.persisted_create)
        self.assertEqual(value,{'reader_value':'kept only in caller'})
        self.assertEqual(self.h.events,['open1','create','close1','reader','reader_closed','open2','drain','drop','close2'])
        self.assertTrue(report['cleanup']['verified']);self.assertTrue(report['fixture_unchanged'])
        self.assertTrue(report['table_inventory_unchanged']);self.assertFalse(self.h.exists)
        self.assertEqual(report['identity']['table_uuid'],UUID)
        self.assertNotIn('reader_value',(self.output/'report.json').read_text())
        self.assertEqual((self.output/'report.json').stat().st_mode & 0o777,0o600)

    def test_saved_show_create_bounded_varchar_preserves_owner_verification(self):
        # Captured Trino SHOW CREATE shape; only the disposable token is normalized.
        ddl = "CREATE TABLE iceberg.local_experiment.training_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa (\n   row_id bigint,\n   x1 double,\n   x2 double,\n   x3 double,\n   target double\n)\nCOMMENT 'fixed-iceberg-regression-source-v1:training_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'\nWITH (\n   format = 'PARQUET',\n   format_version = 2,\n   location = 's3://shirokuma-lakehouse/l1/local_experiment/training_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-b650c2cc80e2425e8a38ce27c3497841'\n)"
        self.assertEqual(len(ddl),438)
        self.h.show_ddl={'columns':['Create Table'],'column_types':['varchar(438)'],'rows':[[ddl]]}
        report,value=self.run_owner()
        self.assertEqual(report['result'],'passed');self.assertIsNotNone(value)
        self.assertTrue(report['cleanup']['verified']);self.assertEqual(self.h.events.count('drop'),1)

    def test_show_create_rejects_malformed_or_nonpositive_bounded_varchar(self):
        for types in ([],['varchar(0)'],['varchar(-1)'],['varchar(01)'],['varchar(1.0)'],
                      ['varchar()'],['varchar(1)extra'],['char(438)'],['varchar','varchar'],[438]):
            with self.subTest(types=types),tempfile.TemporaryDirectory(dir=self.root) as folder:
                owner=F.FixtureOwner(Path(folder)/'fixture',TOKEN,reader_closed=lambda:True)
                ddl={'column_types':types,'rows':[["CREATE TABLE x COMMENT '"+F.marker(TOKEN)+"'"]]}
                with patch.object(owner,'sql',return_value=ddl),self.assertRaises(F.FixtureError):
                    owner.owner_marker()

    def test_source_is_fixed_finite_typed_and_new_name_only(self):
        rows=F.generated_rows();self.assertEqual(len(rows),64)
        self.assertEqual([r[0] for r in rows],list(range(64)))
        self.assertTrue(all(len(r)==5 for r in rows))
        sql=F.create_sql(TOKEN)
        self.assertEqual(sql.count("BIGINT '"),64);self.assertEqual(sql.count("DOUBLE '"),256)
        self.assertIn('CREATE TABLE iceberg.local_experiment.training_'+TOKEN,sql)
        self.assertNotIn('fixture_v1',sql)
        for value in ('../x','A'*32,'a'*31,False):
            with self.assertRaises(F.FixtureError):F.create_sql(value)

    def test_collision_has_no_create_or_drop(self):
        self.h.exists=True
        report,value=self.run_owner()
        self.assertEqual(report['result'],'failed');self.assertIsNone(value)
        self.assertFalse(any(sql.startswith(('CREATE','DROP')) for sql in self.h.sql))
        self.assertTrue(self.h.exists)

    def test_lost_create_response_preserves_table_without_callback_or_drop(self):
        self.h.failure='create';report,value=self.run_owner()
        self.assertEqual(report['result'],'unknown');self.assertTrue(self.h.exists);self.assertIsNone(value)
        self.assertNotIn('reader',self.h.events);self.assertNotIn('drop',self.h.events)
        self.assertTrue(report['sql_outcome_unknown']);self.assertIn('drain',self.h.events)

    def test_created_table_without_original_uuid_is_preserved(self):
        self.h.failure='initial_uuid';report,_=self.run_owner()
        self.assertEqual(report['result'],'unknown');self.assertTrue(self.h.exists)
        self.assertNotIn('drop',self.h.events)

    def test_uuid_snapshot_and_both_markers_must_match_before_drop(self):
        for drift in ('uuid','snapshot','rest_marker','sql_marker'):
            with self.subTest(drift=drift):
                with tempfile.TemporaryDirectory(dir=self.root) as folder:
                    self.output=Path(folder)/'fixture';self.h=Harness(self.output);self.h.drift=drift
                    report,_=self.run_owner()
                    self.assertEqual(report['result'],'unknown');self.assertTrue(self.h.exists)
                    self.assertNotIn('drop',self.h.events)
                    self.assertTrue(report['fixture_unchanged'])

    def test_unclosed_reader_or_unverified_drain_blocks_drop(self):
        for failure in ('reader','drain'):
            with self.subTest(failure=failure):
                with tempfile.TemporaryDirectory(dir=self.root) as folder:
                    self.output=Path(folder)/'fixture';self.h=Harness(self.output)
                    self.h.reader_ok=failure!='reader';self.h.failure=failure
                    report,_=self.run_owner();self.assertEqual(report['result'],'unknown')
                    self.assertNotIn('drop',self.h.events);self.assertTrue(self.h.exists)

    def test_callback_error_with_closed_reader_cleans_but_does_not_pass(self):
        self.h.callback_error=ValueError('private callback error')
        report,value=self.run_owner();self.assertEqual(report['result'],'failed');self.assertIsNone(value)
        self.assertIn('drop',self.h.events);self.assertFalse(self.h.exists)
        self.assertNotIn('private callback error',(self.output/'report.json').read_text())

    def test_first_cancellation_survives_second_during_cleanup(self):
        first,second=KeyboardInterrupt(),SystemExit(42)
        self.h.callback_error=first;self.h.drop_error=second
        with self.assertRaises(KeyboardInterrupt) as caught:self.run_owner()
        self.assertIs(caught.exception,first)
        report=json.loads((self.output/'report.json').read_text())
        self.assertEqual(report['result'],'unknown')

    def test_cancellation_during_cleanup_overrides_ordinary_callback_failure(self):
        error=KeyboardInterrupt();self.h.callback_error=ValueError();self.h.drop_error=error
        with self.assertRaises(KeyboardInterrupt) as caught:self.run_owner()
        self.assertIs(caught.exception,error)

    def test_tunnel_close_failure_prevents_reader_and_success(self):
        self.h.failure='tunnel';report,value=self.run_owner()
        self.assertEqual(report['result'],'unknown');self.assertIsNone(value)
        self.assertNotIn('reader',self.h.events)

    def test_lost_drop_reply_is_unknown_even_if_table_is_now_absent(self):
        self.h.failure='drop';report,value=self.run_owner()
        self.assertEqual(report['result'],'unknown');self.assertIsNone(value)
        self.assertFalse(self.h.exists);self.assertTrue(report['sql_outcome_unknown'])
        self.assertEqual(self.h.events.count('drop'),1)

    def test_retained_projection_or_inventory_change_refuses_success(self):
        for drift in ('retained','inventory'):
            with self.subTest(drift=drift):
                with tempfile.TemporaryDirectory(dir=self.root) as folder:
                    self.output=Path(folder)/'fixture';self.h=Harness(self.output);self.h.drift=drift
                    report,value=self.run_owner();self.assertEqual(report['result'],'failed');self.assertIsNone(value)
                    self.assertFalse(self.h.exists)

    def test_missing_closure_validator_and_existing_output_rejected(self):
        with self.assertRaises(F.FixtureError):F.FixtureOwner(self.output,TOKEN,reader_closed=None)
        self.assertFalse(self.output.exists())
        self.output.mkdir(mode=0o700)
        with self.assertRaises(FileExistsError):self.run_owner()

    def test_create_intent_save_failure_sends_no_mutation(self):
        original=F.save
        def failing(path,value):
            if value['pending']=='create':raise OSError('private disk failure')
            original(path,value)
        with patch.object(F,'save',failing):report,value=self.run_owner()
        self.assertNotIn('create',self.h.events);self.assertNotIn('drop',self.h.events)
        self.assertNotEqual(report['result'],'passed');self.assertIsNone(value)

    def test_evidence_write_keeps_first_cancellation_when_close_also_fails(self):
        self.output.mkdir(mode=0o700)
        first,second=KeyboardInterrupt(),SystemExit(43)
        class Stream:
            def __init__(self,fd):self.fd=fd
            def write(self,raw):raise first
            def close(self):os.close(self.fd);raise second
        with patch.object(F.os,'fdopen',side_effect=lambda fd,mode:Stream(fd)):
            with self.assertRaises(KeyboardInterrupt) as caught:F.save(self.output/'save.json',{})
        self.assertIs(caught.exception,first)
        self.assertFalse((self.output/'save.json').exists())

    def test_reader_closure_exception_preserves_table(self):
        first=KeyboardInterrupt()
        def closed():raise first
        with self.assertRaises(KeyboardInterrupt) as caught:
            F.run(self.output,TOKEN,self.h.callback,reader_closed=closed,_runtime=self.h.runtime,_drain=self.h.drain)
        self.assertIs(caught.exception,first);self.assertTrue(self.h.exists)
        self.assertNotIn('drop',self.h.events)

    def test_runtime_closes_every_context_and_preserves_first_cancellation(self):
        first=KeyboardInterrupt();events=[];journals=[]
        @contextmanager
        def trino():
            events.append('open_trino')
            try:yield 'http://127.0.0.1:18080'
            finally:events.append('close_trino');raise OSError('private later failure')
        @contextmanager
        def polaris(progress):
            events.append('open_polaris')
            try:yield lambda:None
            finally:events.append('close_polaris');raise first
        class Catalog:
            def close(self):events.append('close_catalog')
        with patch.object(F.REFS.SESSION,'check_context'),patch.object(F.Q,'trino_service_identity',return_value={'uid':'same'}), \
             patch.object(F.REFS.SESSION,'trino_tunnel',side_effect=trino),patch.object(F.REFS,'polaris_tunnel',side_effect=polaris), \
             patch.object(F.SB,'SandboxClient',return_value=object()),patch.object(F.Q,'CatalogRead',return_value=Catalog()):
            with self.assertRaises(KeyboardInterrupt) as caught:
                with F.runtime(TOKEN,lambda value:journals.append(value)):events.append('body')
        self.assertIs(caught.exception,first)
        self.assertEqual(events,['open_trino','open_polaris','body','close_polaris','close_trino','close_catalog'])
        self.assertFalse(journals[-1]['cleanup_verified']);self.assertEqual(journals[-1]['state'],'failed')

    def test_runtime_contexts_close_on_ordinary_body_failure(self):
        events=[];journals=[];error=ValueError('private body failure')
        @contextmanager
        def trino():
            try:yield 'http://127.0.0.1:18080'
            finally:events.append('trino')
        @contextmanager
        def polaris(progress):
            try:yield lambda:None
            finally:events.append('polaris')
        class Catalog:
            def close(self):events.append('catalog')
        with patch.object(F.REFS.SESSION,'check_context'),patch.object(F.Q,'trino_service_identity',return_value={'uid':'same'}), \
             patch.object(F.REFS.SESSION,'trino_tunnel',side_effect=trino),patch.object(F.REFS,'polaris_tunnel',side_effect=polaris), \
             patch.object(F.SB,'SandboxClient',return_value=object()),patch.object(F.Q,'CatalogRead',return_value=Catalog()):
            with self.assertRaises(ValueError) as caught:
                with F.runtime(TOKEN,lambda value:journals.append(value)):raise error
        self.assertIs(caught.exception,error);self.assertEqual(events,['polaris','trino','catalog'])
        self.assertTrue(journals[-1]['cleanup_verified']);self.assertEqual(journals[-1]['state'],'failed')


if __name__=='__main__':unittest.main()
