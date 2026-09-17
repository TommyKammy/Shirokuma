"""Offline fixed HTTP, metadata and teardown tests; no APIs or models."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools.local_experiment.query_snapshots import observe as O, contract as C
from tools.local_experiment.query_read import client as Q, contract as R
from test_query_read_client import Response, Opener, page, URL
from test_query_read_contract import metadata as reader_metadata, schema, inventory, SNAPSHOT

TOKEN = 'b' * 32
OLD = SNAPSHOT - 1
IDS = ['20260918_040404_%05d_abc12' % i for i in range(1, 6)]


def request(*, listing=False):
    if listing:
        return {'command':'snapshots', 'table':'smoke.fixture_v1'}
    return {'command':'select','table':'smoke.fixture_v1','columns':['id','name'],
            'limit':2,'snapshot_id':str(OLD)}


def metadata():
    value = reader_metadata()
    value['metadata']['snapshots'] = [
        {'snapshot-id':OLD, 'sequence-number':1, 'timestamp-ms':1700000000000,
         'schema-id':0, 'summary':{'operation':'append'}},
        {'snapshot-id':SNAPSHOT, 'parent-snapshot-id':OLD, 'sequence-number':2,
         'timestamp-ms':1700000001000,'schema-id':0,'summary':{'operation':'append'}}]
    return value


def schema_result():
    return {'columns':['column_name','data_type','ordinal_position','is_nullable'],
            'column_types':['varchar','varchar','bigint','varchar'],
            'rows':[[s['name'],s['type'],i,'YES' if s['nullable'] else 'NO']
                    for i,s in enumerate(schema(),1)]}


class ObservationFixture:
    def setup(self):
        self.saved, self.events = [], []
        self.request = request()
        self.metadata = [metadata(), metadata()]
        self.schema_results = [schema_result(), schema_result()]
        self.inventory_names = ['fixture_v1','other']
        self.rows = [[1,'PRIVATE-ROW-SENTINEL'],[2,None]]
        self.query_error = None
        self.query_error_at = 'select'
        self.drain_error = self.teardown_error = None
        self.drain_verified = self.runtime_cleanup = True
        self.runtime_state = None

    def progress(self, value):
        self.saved.append(deepcopy(value))

    def clients(self, req, runtime_progress):
        labels = ['inventory','schema_before'] + ([] if req['command']=='snapshots' else ['select']) + ['schema_after']
        results = []
        schemas = iter(self.schema_results)
        for index,label in enumerate(labels):
            if label==self.query_error_at and self.query_error is not None:
                results.append(self.query_error)
                continue
            if label=='inventory':
                value={'columns':['table_name'],'column_types':['varchar'],'rows':[[s] for s in self.inventory_names]}
            elif label.startswith('schema_'):
                value=next(schemas)
            else:
                value={'columns':req['columns'],'column_types':['bigint','varchar'],'rows':self.rows}
            results.append(Response(page(qid=IDS[index],columns=list(zip(value['columns'],value['column_types'])),rows=value['rows'])))
        outer = self
        class ObservedOpener(Opener):
            def open(self, req, timeout):
                outer.events.append(labels[len(self.requests)])
                return super().open(req,timeout)
        self.opener = ObservedOpener(*results)
        self.worker = O.SnapshotClient(URL,TOKEN,request=req,opener=self.opener)
        self.control = Q.QueryClient(URL,TOKEN,control=True,opener=Opener(Response(page(
            qid=IDS[-1],columns=[(c,'varchar') for c in ('query_id','state','user','source')],
            rows=[[IDS[i],'FINISHED',*Q.identity(TOKEN)] for i in range(len(labels))]))))
        class Catalog:
            def __init__(self):self.auths=0;self.reads=0
            def authenticate(self):
                self.auths+=1;outer.events.append('authenticate')
            def load(self, value):
                R.normalize_request(value)
                self.reads+=1;outer.events.append('metadata')
                return deepcopy(outer.metadata[self.reads-1])
            def receipt(self):return {'authenticated':self.auths==1,'reads':self.reads}
        self.catalog=Catalog()
        return self.worker,self.control,self.catalog

    @contextmanager
    def runtime(self, token, req, progress):
        self.assertEqual(token,TOKEN)
        self.events.append('open')
        clients=self.clients(req,progress)
        error=None
        progress({'state':'ready','cleanup':{'verified':False}})
        try:
            yield clients
        except BaseException as caught:
            error=caught
            raise
        finally:
            self.events.append('close')
            progress({'state':self.runtime_state or ('verified' if error is None else 'failed'),
                      'cleanup':{'verified':self.runtime_cleanup},
                      'worker':self.worker.receipt(),'control':self.control.receipt(),
                      'catalog':self.catalog.receipt()})
            if self.teardown_error is not None:raise self.teardown_error

    def drain(self, control, worker, token):
        self.assertIs(control,self.control);self.assertIs(worker,self.worker)
        self.assertFalse(self.saved[-1]['query_drain']['verified'])
        self.events.append('drain')
        if self.drain_error is not None:raise self.drain_error
        return {'verified':self.drain_verified}

    def invoke(self, *, progress=None, drain_fn=None):
        return O.observe(self.request,TOKEN,progress or self.progress,
                         runtime_factory=self.runtime,drain_fn=drain_fn or self.drain)


class ObservationTests(ObservationFixture,unittest.TestCase):
    def setUp(self):self.setup()

    def test_historical_snapshot_is_separate_from_truthful_current_identity(self):
        journal,public=self.invoke()
        self.assertEqual(self.events,['open','inventory','schema_before','authenticate','metadata',
                                     'select','schema_after','metadata','drain','close'])
        self.assertEqual(public['snapshot_id'],str(OLD))
        self.assertEqual(public['selected_snapshot']['snapshot_id'],str(OLD))
        self.assertEqual(public['current_snapshot_id'],str(SNAPSHOT))
        self.assertEqual(journal['identity_before']['current_snapshot_id'],str(SNAPSHOT))
        self.assertEqual(journal['identity_after'],journal['identity_before'])
        self.assertEqual(journal['snapshot_catalog_after'],journal['snapshot_catalog_before'])
        self.assertEqual(journal['selection'],C.receipt_projection(public))
        self.assertEqual(journal['state'],'verified');self.assertTrue(journal['cleanup_verified'])
        self.assertEqual(journal,self.saved[-1])
        sqls=[x.data.decode() for x in self.opener.requests]
        self.assertEqual(sum(' FOR VERSION AS OF ' in sql for sql in sqls),1)
        self.assertIn(" FOR VERSION AS OF BIGINT '"+str(OLD)+"' LIMIT 3",sqls[2])
        self.assertNotIn(str(SNAPSHOT),sqls[2])

    def test_listing_has_no_data_select_and_no_selection_receipt(self):
        self.request=request(listing=True)
        journal,public=self.invoke()
        self.assertEqual([r['label'] for r in journal['queries']['worker']],['inventory','schema_before','schema_after'])
        self.assertEqual([r['snapshot_id'] for r in public['snapshots']],[str(OLD),str(SNAPSHOT)])
        self.assertIsNone(journal['selection'])
        self.assertEqual(public['ordering'],'timestamp-asc')
        self.assertEqual(self.catalog.auths,1);self.assertEqual(self.catalog.reads,2)

    def test_rows_and_catalog_credentials_are_absent_from_all_progress(self):
        journal,public=self.invoke()
        serialized=json.dumps(self.saved)
        for value in ('PRIVATE-ROW-SENTINEL','never-persist-credential','SELECT ','"rows":','storage-credentials'):
            self.assertNotIn(value,serialized)
        self.assertIn('PRIVATE-ROW-SENTINEL',json.dumps(public))
        public['rows'].clear()
        self.assertEqual(journal['selection'],self.saved[-1]['selection'])

    def test_historical_read_keeps_100_plus_one_sentinel_bound(self):
        self.request['limit']=100
        self.rows=[[i,None] for i in range(101)]
        journal,public=self.invoke()
        self.assertEqual(public['row_count'],100)
        self.assertEqual(public['fetched_row_count'],101)
        self.assertTrue(public['truncated'])
        self.assertEqual(len(public['rows']),100)
        self.assertEqual(journal['queries']['worker'][2]['row_count'],101)
        self.assertIn(' LIMIT 101',self.opener.requests[2].data.decode())
        self.setup();self.request['limit']=100
        self.rows=[[i,None] for i in range(102)]
        with self.assertRaises(O.ObservationError):self.invoke()
        self.assertIsNone(self.saved[-1]['selection'])

    def test_historical_transport_preserves_scalar_types_and_cell_byte_cap(self):
        for rows in ([[True,'invalid-id']],[[1,'日'*1366]]):
            self.setup();self.rows=rows
            with self.subTest(rows_kind=type(rows[0][0]).__name__),self.assertRaises(O.ObservationError):self.invoke()
            self.assertEqual(self.saved[-1]['queries']['worker'][-1]['error_code'],'QUERY_CELL_TYPE_INVALID')
            self.assertEqual(self.events.count('select'),1)
            self.assertEqual(self.events[-2:],['drain','close'])

    def test_invalid_input_is_rejected_before_any_runtime(self):
        for value in ({**request(),'snapshot_id':'0'},{**request(),'snapshot_id':True},
                      {**request(),'sql':'SELECT secret'}):
            self.request=value
            with self.assertRaises(O.ObservationError):self.invoke()
        self.assertEqual(self.events,[])

    def test_not_discovered_has_no_schema_auth_or_data_read(self):
        self.inventory_names=['other']
        with self.assertRaises(O.ObservationError):self.invoke()
        self.assertEqual(self.events,['open','inventory','drain','close'])

    def test_missing_snapshot_preserves_fixed_refusal_and_has_no_select(self):
        self.request['snapshot_id']='1'
        with self.assertRaisesRegex(O.ObservationError,'^SNAPSHOT_NOT_RETAINED$'):self.invoke()
        self.assertEqual(self.saved[-1]['error_code'],'SNAPSHOT_NOT_RETAINED')
        self.assertNotIn('select',self.events)
        self.assertTrue(self.saved[-1]['cleanup_verified'])

    def test_incompatible_historical_schema_refuses_before_data_post(self):
        self.metadata[0]['metadata']['snapshots'][0].pop('schema-id')
        with self.assertRaisesRegex(O.ObservationError,'^SNAPSHOT_SCHEMA_UNSUPPORTED$'):self.invoke()
        self.assertNotIn('select',self.events)
        self.assertEqual(self.saved[-1]['snapshot_catalog_before'][0]['schema_compatibility'],'schema-missing')

    def test_snapshot_limit_refuses_before_data_post(self):
        self.metadata[0]['metadata']['snapshots']*=33
        with self.assertRaises(O.ObservationError):self.invoke()
        self.assertNotIn('select',self.events)

    def test_current_head_or_metadata_movement_discards_public_rows(self):
        for change in ('head','metadata-location'):
            self.setup()
            if change=='head':
                self.metadata[1]['metadata']['current-snapshot-id']=OLD
                self.metadata[1]['metadata']['refs']['main']['snapshot-id']=OLD
            else:self.metadata[1]['metadata-location']=self.metadata[1]['metadata-location'].replace('00001','00002')
            with self.subTest(change=change),self.assertRaisesRegex(O.ObservationError,'^SNAPSHOT_WINDOW_CHANGED$'):self.invoke()
            self.assertEqual(self.events[-2:],['drain','close'])
            self.assertIsNotNone(self.saved[-1]['selection'])
            self.assertEqual(self.saved[-1]['state'],'failed')

    def test_selected_snapshot_expiration_in_after_window_is_not_success(self):
        self.metadata[1]['metadata']['snapshots']=self.metadata[1]['metadata']['snapshots'][1:]
        with self.assertRaisesRegex(O.ObservationError,'^SNAPSHOT_WINDOW_CHANGED$'):self.invoke()
        self.assertEqual(self.events[-2:],['drain','close'])

    def test_schema_after_change_refuses_publishing(self):
        self.schema_results[1]['rows'][0][1]='integer'
        with self.assertRaises(O.ObservationError):self.invoke()
        self.assertIn('select',self.events)
        self.assertEqual(self.events[-2:],['drain','close'])

    def test_unknown_post_is_not_retried_or_followed_by_more_observation(self):
        self.query_error=OSError('PRIVATE-UPSTREAM-ERROR')
        with self.assertRaisesRegex(O.ObservationError,'^OBSERVATION_FAILED$'):self.invoke()
        self.assertEqual(self.events.count('select'),1)
        self.assertNotIn('schema_after',self.events)
        self.assertEqual(self.saved[-1]['queries']['worker'][-1]['result'],'unknown')
        self.assertNotIn('PRIVATE-UPSTREAM-ERROR',json.dumps(self.saved))

    def test_first_cancellation_survives_teardown_drain_and_final_save(self):
        first=KeyboardInterrupt('PRIVATE-FIRST')
        self.query_error=first;self.drain_error=SystemExit('PRIVATE-SECOND');self.teardown_error=OSError('PRIVATE-THIRD')
        def progress(value):
            self.progress(value)
            if value['state']=='interrupted':raise RuntimeError('PRIVATE-FOURTH')
        with self.assertRaises(KeyboardInterrupt) as caught:self.invoke(progress=progress)
        self.assertIs(caught.exception,first)
        self.assertEqual(self.events[-2:],['drain','close'])
        self.assertNotIn('PRIVATE-',json.dumps(self.saved))

    def test_first_cancellation_during_error_save_outranks_ordinary_error(self):
        self.query_error=OSError('PRIVATE-UPSTREAM')
        first=KeyboardInterrupt()
        def progress(value):
            self.progress(value)
            if value['queries']['worker'] and value['queries']['worker'][-1]['result']=='unknown':raise first
        with self.assertRaises(KeyboardInterrupt) as caught:self.invoke(progress=progress)
        self.assertIs(caught.exception,first)

    def test_failed_drain_or_final_runtime_proof_never_releases_rows(self):
        for stage in ('drain','runtime-state','runtime-cleanup'):
            self.setup()
            if stage=='drain':self.drain_verified=False
            elif stage=='runtime-state':self.runtime_state='ready'
            else:self.runtime_cleanup=False
            with self.subTest(stage=stage),self.assertRaises(O.ObservationError):self.invoke()
            self.assertEqual(self.saved[-1]['state'],'failed')

    def test_final_safe_save_failure_never_returns_public_candidate(self):
        def progress(value):
            self.progress(value)
            if value['state']=='verified':raise OSError('PRIVATE-SAVE')
        with self.assertRaisesRegex(O.ObservationError,'^OBSERVATION_FAILED$'):self.invoke(progress=progress)
        self.assertEqual(self.events[-1],'close')

    def test_actual_drain_uses_only_new_worker_identity(self):
        journal,public=self.invoke(drain_fn=Q.drain)
        self.assertTrue(journal['query_drain']['verified'])
        self.assertEqual(journal['query_drain']['observed_query_ids'],IDS[:4])
        self.assertEqual(len(journal['queries']['control']),1)
        self.assertEqual([r.get_method() for r in self.opener.requests],['POST']*4)
        self.assertEqual(public['snapshot_id'],str(OLD))


class ClientTests(unittest.TestCase):
    def client(self, req=None):
        self.opener=Opener(*[Response(page(qid=q,columns=[('v','bigint')],rows=[])) for q in IDS])
        return O.SnapshotClient(URL,TOKEN,request=req or request(),opener=self.opener)

    def test_only_fresh_compiled_select_once_and_no_arbitrary_sql(self):
        req=request();client=self.client(req)
        window=C.project_window(metadata(),req,schema())
        sql=C.select_sql(req,inventory(),schema(),window)
        with self.assertRaises(Q.ClientError):client.query(sql,'select')
        with self.assertRaises(O.ObservationError):client.bind_select(inventory(),schema(),window)
        self.assertEqual(self.opener.requests,[])
        client.query(C.discovery_sql(req),'inventory');client.query(C.schema_sql(req),'schema_before')
        client.bind_select(inventory(),schema(),window)
        for bad in (sql+';',sql.replace(str(OLD),str(SNAPSHOT)),'SELECT 1',
                    sql.replace("BIGINT '"+str(OLD)+"'",str(OLD)),
                    sql.replace("BIGINT '"+str(OLD)+"'",'CAST('+str(OLD)+' AS BIGINT)')):
            with self.assertRaises(Q.ClientError):client.query(bad,'select')
        client.query(sql,'select')
        with self.assertRaises(Q.ClientError):client.query(sql,'select')
        with self.assertRaises(O.ObservationError):client.bind_select(inventory(),schema(),window)
        self.assertEqual(len(self.opener.requests),3)

    def test_listing_client_never_accepts_select_or_other_table(self):
        req=request(listing=True);client=self.client(req)
        with self.assertRaises(O.ObservationError):client.bind_select(inventory(),schema(),C.project_window(metadata(),req,schema()))
        other={**req,'table':'smoke.other'}
        with self.assertRaises(Q.ClientError):client.query(C.schema_sql(other),'schema_before')
        self.assertEqual(self.opener.requests,[])

    def test_existing_reader_numeric_allowlist_stays_unchanged(self):
        req=request();window=C.project_window(metadata(),req,schema())
        sql=C.select_sql(req,inventory(),schema(),window)
        Q.allowed_sql(sql.replace("BIGINT '"+str(OLD)+"'",str(OLD)),'select',TOKEN,False)
        with self.assertRaises(Q.ClientError):Q.allowed_sql(sql,'select',TOKEN,False)
        client=self.client()
        for label in ('inventory','schema_before','schema_after'):
            sql=C.discovery_sql(req) if label=='inventory' else C.schema_sql(req)
            client.query(sql,label)
            with self.assertRaises(Q.ClientError):client.query(sql,label)

    def test_fake_wire_sends_typed_bigint_for_small_and_large_retained_ids(self):
        for sid in (1,2147483647,2147483648,9223372036854775807):
            req={**request(),'snapshot_id':str(sid)}
            value=metadata()
            value['metadata']['snapshots'][0]['snapshot-id']=sid
            value['metadata']['snapshots'][1]['parent-snapshot-id']=sid
            window=C.project_window(value,req,schema())
            client=self.client(req)
            client.query(C.discovery_sql(req),'inventory');client.query(C.schema_sql(req),'schema_before')
            client.bind_select(inventory(),schema(),window)
            client.query(C.select_sql(req,inventory(),schema(),window),'select')
            with self.subTest(snapshot_id=sid):
                self.assertEqual(len(self.opener.requests),3)
                self.assertIn(" FOR VERSION AS OF BIGINT '"+str(sid)+"' LIMIT 3",self.opener.requests[-1].data.decode())
                self.assertTrue(client.records[-1]['response_complete'])


if __name__=='__main__':unittest.main()
