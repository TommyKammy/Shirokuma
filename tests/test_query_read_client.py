from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
import base64
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.query_read import client as M
from tools.local_experiment.query_read import contract as C

TOKEN = 'a' * 32
URL = 'http://127.0.0.1:18080'
QID = '20260918_010101_00001_abc12'
OTHER = '20260918_010101_00002_abc12'
REQUEST = C.request('describe', table='smoke.fixture_v1')
SQL = C.discovery_sql(C.request('tables', schema='smoke'))


def page(*, qid=QID, state='FINISHED', columns=None, rows=None, following=None):
    value = {'id': qid, 'stats': {'state': state}}
    if columns is not None:
        value['columns'] = [{'name': name, 'type': kind} for name, kind in columns]
    if rows is not None: value['data'] = rows
    if following is not None: value['nextUri'] = following
    return value


def data_page(rows=None):
    return page(columns=[('table_name', 'varchar')], rows=rows if rows is not None else [['fixture_v1']])


def next_uri(qid=QID, index=1):
    return URL + '/v1/statement/executing/' + qid + '/abcdef/' + str(index)


class Response:
    def __init__(self, value=None, *, raw=None, status=200, content_type='application/json', url=None):
        self.raw = raw if raw is not None else json.dumps(value).encode()
        self.stream = io.BytesIO(self.raw)
        self.status, self.headers, self.url = status, {'Content-Type': content_type}, url
        self.closed = False

    def __enter__(self): return self
    def __exit__(self, *_): self.close()
    def close(self): self.closed = True
    def geturl(self): return self.url
    def read(self, count): return self.stream.read(count)


class Opener:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def open(self, request, timeout):
        self.requests.append(request)
        if not self.responses: raise AssertionError('unexpected extra HTTP')
        response = self.responses.pop(0)
        if isinstance(response, BaseException): raise response
        if callable(response): response = response(request)
        if response.url is None: response.url = request.full_url
        return response


class TrinoTests(unittest.TestCase):
    def client(self, *responses, **kwargs):
        self.opener = Opener(*responses)
        return M.QueryClient(URL, TOKEN, opener=self.opener, **kwargs)

    def test_one_post_durable_intent_and_owned_headers(self):
        saved = []
        client = self.client(Response(data_page()))
        client.progress = lambda: saved.append(deepcopy(client.receipt()))
        result = client.query(SQL, 'inventory')
        self.assertEqual(result['rows'], [['fixture_v1']])
        self.assertEqual([r.get_method() for r in self.opener.requests], ['POST'])
        req = self.opener.requests[0]
        self.assertEqual(req.get_header('X-trino-user'), M.identity(TOKEN)[0])
        self.assertEqual(req.get_header('X-trino-source'), M.identity(TOKEN)[1])
        self.assertEqual(req.get_header('X-trino-session'), 'query_max_execution_time=30s,query_max_run_time=30s')
        self.assertFalse(saved[0]['records'][0]['post_attempted'])
        self.assertTrue(saved[1]['records'][0]['post_attempted'])
        self.assertEqual(saved[1]['records'][0]['result'], 'pending')
        self.assertTrue(client.records[0]['response_complete'])
        self.assertNotIn('fixture_v1', json.dumps(client.receipt()))
        self.assertNotIn(SQL, json.dumps(client.receipt()))

    def test_zero_post_if_first_or_intent_journal_or_guard_fails(self):
        for fail_at in (0, 1, 2):
            with self.subTest(fail_at=fail_at):
                client = self.client(Response(data_page()))
                calls = []
                def progress():
                    calls.append(1)
                    if len(calls) == fail_at: raise OSError('SECRET-journal')
                client.progress = progress
                if fail_at == 0: client.guard = lambda: (_ for _ in ()).throw(OSError('SECRET-guard'))
                with self.assertRaisesRegex(M.ClientError, '^QUERY_FAILED$'): client.query(SQL, 'inventory')
                self.assertEqual(self.opener.requests, [])
                self.assertFalse(client.records[0]['post_attempted'])
                self.assertEqual(client.records[0]['result'], 'not-sent')
                self.assertNotIn('SECRET', json.dumps(client.receipt()))

    def test_unknown_lost_post_cannot_retry_or_claim_complete(self):
        client = self.client(URLError('SECRET transport'))
        with patch.object(M.time, 'monotonic', return_value=10):
            with self.assertRaisesRegex(M.ClientError, '^QUERY_FAILED$'): client.query(SQL, 'inventory')
        self.assertEqual(client.records[0]['_not_before'], 42)
        self.assertEqual(client.records[0]['result'], 'unknown')
        self.assertFalse(client.records[0]['response_complete'])
        with self.assertRaisesRegex(M.ClientError, 'UNRESOLVED'): client.query(SQL, 'inventory')
        self.assertEqual(len(self.opener.requests), 1)
        self.assertNotIn('_not_before', json.dumps(client.receipt()))

    def test_typed_foreign_error_text_is_not_trusted_as_safe_code(self):
        client=self.client(M.ClientError('SECRET_ARBITRARY_TEXT'))
        with self.assertRaisesRegex(M.ClientError,'^QUERY_FAILED$'):client.query(SQL,'inventory')
        self.assertNotIn('SECRET',json.dumps(client.receipt()))

    def test_original_interrupt_survives_second_baseexception_during_save(self):
        first = KeyboardInterrupt('FIRST')
        client = self.client(first)
        calls = []
        def progress():
            calls.append(1)
            if len(calls) > 2: raise SystemExit('SECOND')
        client.progress = progress
        with self.assertRaises(KeyboardInterrupt) as caught: client.query(SQL, 'inventory')
        self.assertIs(caught.exception, first)
        self.assertEqual(client.records[0]['result'], 'interrupted')
        self.assertNotIn('FIRST', json.dumps(client.receipt()))

    def test_first_cancellation_during_failed_journal_outranks_ordinary_transport_error(self):
        first=KeyboardInterrupt();client=self.client(OSError('ordinary'))
        calls=[]
        def progress():
            calls.append(1)
            if len(calls)>2:raise first
        client.progress=progress
        with self.assertRaises(KeyboardInterrupt) as caught:client.query(SQL,'inventory')
        self.assertIs(caught.exception,first);self.assertEqual(client.records[0]['result'],'interrupted')

    def test_decimal_and_int64_exact_wire_numbers(self):
        raw = ('{"id":"' + QID + '","stats":{"state":"FINISHED"},"columns":'
               '[{"name":"id","type":"bigint"},{"name":"amount","type":"decimal(28, 6)"}],'
               '"data":[[9223372036854775807,12345678901234567890.123456]]}').encode()
        client = self.client(Response(raw=raw))
        sql = 'SELECT "id", "amount" FROM "iceberg"."smoke"."fixture_v1" FOR VERSION AS OF 9223372036854775807 LIMIT 3'
        result = client.query(sql, 'select')
        self.assertEqual(result['rows'], [[2**63-1, Decimal('12345678901234567890.123456')]])
        self.assertEqual(result['column_types'], ['bigint', 'decimal(28,6)'])
        self.assertNotIn('1234567890', json.dumps(client.receipt()))

    def test_columns_can_arrive_late_strict_same_query_get(self):
        client = self.client(Response(page(state='RUNNING', following=next_uri())), Response(data_page()))
        result = client.query(SQL, 'inventory')
        self.assertEqual(result['rows'], [['fixture_v1']])
        self.assertEqual([r.get_method() for r in self.opener.requests], ['POST', 'GET'])
        self.assertEqual(client.records[0]['pages'], 2)

    def test_continuation_cannot_change_origin_query_or_add_querystring(self):
        for following in ('https://127.0.0.1:18080/x', 'http://127.0.0.1:9999/x',
                          next_uri(OTHER), next_uri() + '?x=1', URL + '/v1/query/' + QID):
            with self.subTest(following=following):
                client = self.client(Response(page(state='RUNNING', following=following)))
                with self.assertRaises(M.ClientError): client.query(SQL, 'inventory')
                self.assertEqual(len(self.opener.requests), 1)
                self.assertEqual(client.records[0]['query_id'], QID)
                self.assertFalse(client.records[0]['response_complete'])

    def test_response_identity_type_content_and_duplicate_fail_closed(self):
        variants = [Response(data_page(), content_type='text/html'), Response(data_page(), url=URL+'/foreign'),
                    Response(data_page(), status=201), Response(raw=b'{"id":1,"id":2}'),
                    Response(raw=b'{"id":NaN}'), Response(raw=b'{"id":Infinity}'),
                    Response(raw=b'\xff'), Response(raw=b'[]')]
        for response in variants:
            client = self.client(response)
            with self.assertRaises(M.ClientError): client.query(SQL, 'inventory')
            self.assertFalse(client.records[0]['response_complete'])

    def test_malformed_page_row_types_and_terminal_state(self):
        values = [page(qid='unscoped', columns=[('x','varchar')], rows=[]),
                  page(state='RUNNING', columns=[('x','varchar')], rows=[]),
                  page(state='FUTURE', columns=[('x','varchar')], rows=[]),
                  page(columns=[('x','bigint')], rows=[[2**63]]),
                  page(columns=[('x','bigint')], rows=[[True]]),
                  page(columns=[('x','integer')], rows=[[2**31]]),
                  page(columns=[('x','varchar')], rows=[[1]]),
                  page(columns=[('x','date')], rows=[['2026-02-30']]),
                  page(columns=[('x','array(bigint)')], rows=[]),
                  page(columns=[('x','bigint'),('x','bigint')], rows=[]),
                  page(columns=[('x','bigint')], rows=[[1,2]])]
        values.append(data_page() | {'updateType':'INSERT','updateCount':1})
        values.append(data_page() | {'unexpected':'SECRET'})
        for value in values:
            with self.subTest(value=value):
                client = self.client(Response(value))
                with self.assertRaises(M.ClientError): client.query(SQL, 'inventory')
                self.assertFalse(client.records[0]['response_complete'])
                self.assertNotIn('SECRET', json.dumps(client.receipt()))

    def test_changed_columns_and_query_id_rejected(self):
        initial = page(columns=[('x','bigint')], rows=[], state='RUNNING', following=next_uri())
        for final in (page(columns=[('x','integer')], rows=[]), page(qid=OTHER, columns=[('x','bigint')], rows=[])):
            client = self.client(Response(initial), Response(final))
            with self.assertRaises(M.ClientError): client.query(SQL, 'inventory')
            self.assertFalse(client.records[0]['response_complete'])

    def test_failed_terminal_is_complete_but_never_success(self):
        client = self.client(Response(page(state='FAILED') | {'error': {'message':'SECRET SQL'}}))
        with self.assertRaisesRegex(M.ClientError, '^TRINO_QUERY_FAILED$'): client.query(SQL, 'inventory')
        self.assertEqual(client.records[0]['result'], 'failed')
        self.assertTrue(client.records[0]['response_complete'])
        self.assertNotIn('SECRET', json.dumps(client.receipt()))

    def test_final_save_failure_does_not_return_success(self):
        client = self.client(Response(data_page()))
        def progress():
            if client.records[0]['result'] == 'finished': raise OSError('SECRET final save')
        client.progress = progress
        with self.assertRaises(M.ClientError): client.query(SQL, 'inventory')
        self.assertEqual(client.records[0]['result'], 'failed')
        self.assertTrue(client.records[0]['response_complete'])

    def test_row_page_byte_page_count_and_total_bounds(self):
        variants = [(Response(data_page([['x']]*129)),), (Response(raw=b' '* (M.PAGE_BYTES+1)),)]
        for responses in variants:
            client = self.client(*responses)
            with self.assertRaises(M.ClientError): client.query(SQL, 'inventory')
        client = self.client(Response(page(state='RUNNING',following=next_uri())), Response(data_page()))
        with patch.object(M, 'MAX_PAGES', 1):
            with self.assertRaisesRegex(M.ClientError, 'PAGE_BOUND'): client.query(SQL,'inventory')
        responses = [Response(page(state='RUNNING',following=next_uri(index=i+1)) | {'warnings':['x'*900000]}) for i in range(5)]
        client = self.client(*responses)
        with self.assertRaisesRegex(M.ClientError, 'TOTAL_BYTES_BOUND'): client.query(SQL,'inventory')

    def test_read_deadline_expiry_after_blocking_read_rejected(self):
        response=Response(data_page());clock=[0]
        def slow_read(count):clock[0]=100;return response.stream.read(count)
        response.read=slow_read
        client = self.client(response)
        with patch.object(M.time, 'monotonic', side_effect=lambda:clock[0]):
            with self.assertRaisesRegex(M.ClientError, 'DEADLINE'): client.query(SQL,'inventory')
        self.assertFalse(client.records[0]['response_complete'])

    def test_mutating_or_unbounded_sql_is_rejected_before_post(self):
        bad = [('DELETE FROM iceberg.smoke.fixture_v1','select'), ('SELECT * FROM x','select'),
               (SQL + '; DROP TABLE x', 'inventory'), (SQL,'wrong'),
               ('SELECT "id" FROM "iceberg"."other"."x" FOR VERSION AS OF 1 LIMIT 2','select'),
               ('SELECT "id" FROM "iceberg"."smoke"."x" FOR VERSION AS OF 9223372036854775808 LIMIT 2','select')]
        for sql,label in bad:
            client = self.client()
            with self.assertRaises(M.ClientError): client.query(sql,label)
            self.assertEqual(client.records,[]);self.assertEqual(self.opener.requests,[])

    def test_control_accepts_only_fixed_owned_inspection(self):
        client = self.client(Response(page(columns=[('query_id','varchar'),('state','varchar'),('user','varchar'),('source','varchar')],rows=[])),control=True)
        client.query(M.inspection_sql(TOKEN),'drain')
        self.assertEqual(self.opener.requests[0].get_header('X-trino-source'),M.identity(TOKEN,True)[1])
        with self.assertRaises(M.ClientError): client.query(SQL,'inventory')
        with self.assertRaises(M.ClientError): client.query("CALL system.runtime.kill_query('x')",'drain')

    def test_owner_phase_labels_only_no_legacy_alias(self):
        for label in ('schema_before','schema_after'):
            client=self.client(Response(page(columns=[('column_name','varchar')],rows=[])))
            client.query(C.schema_sql(REQUEST),label)
            self.assertEqual(client.records[0]['label'],label)
        for label in ('discovery','schema'):
            client=self.client()
            with self.assertRaisesRegex(M.ClientError,'LABEL_INVALID'):client.query(SQL,label)

    def test_fixed_loopback_no_proxy_redirect(self):
        for url in ('http://localhost:18080','http://127.0.0.1','http://127.0.0.1:18080/',
                    'https://127.0.0.1:18080','http://u:p@127.0.0.1:18080','http://127.0.0.1:18080?x=1'):
            with self.assertRaises(M.ClientError): M.QueryClient(url,TOKEN)
        with patch.object(M.urllib.request,'build_opener') as built:
            M.QueryClient(URL,TOKEN)
            self.assertEqual(built.call_args.args[0].proxies,{})
            self.assertIsInstance(built.call_args.args[1],M.NoRedirect)


class DrainTests(unittest.TestCase):
    def clients(self, *, known=True):
        worker=M.QueryClient(URL,TOKEN,opener=Opener(Response(raw=b'',status=204)))
        worker.records=[{'query_id':QID if known else None,'post_attempted':True,'response_complete':False,'_not_before':32}]
        control=M.QueryClient(URL,TOKEN,control=True,opener=Opener())
        return worker,control

    def observed(self, rows):
        return {'query_id':OTHER,'columns':['query_id','state','user','source'], 'column_types':['varchar']*4,'rows':rows}

    def test_only_known_query_id_cancelled_after_user_source_match(self):
        worker,control=self.clients();user,source=M.identity(TOKEN)
        with patch.object(control,'query',side_effect=[self.observed([[QID,'RUNNING',user,source]]),self.observed([[QID,'FAILED',user,source]])]), patch.object(M.time,'sleep'):
            result=M.drain(control,worker,TOKEN)
        self.assertTrue(result['verified']);self.assertEqual(result['cancelled'],[QID])
        self.assertEqual(worker.opener.requests[0].get_method(),'DELETE')
        self.assertEqual(worker.opener.requests[0].full_url,URL+'/v1/query/'+QID)
        self.assertEqual(worker.cancellations,[{'query_id':QID,'result':'accepted','http_status':204}])

    def test_foreign_user_source_unrecorded_id_or_duplicate_rejected_without_cancel(self):
        user,source=M.identity(TOKEN)
        for rows in ([[QID,'RUNNING','foreign',source]],[[QID,'RUNNING',user,'foreign']],
                     [[OTHER,'RUNNING',user,source]],[[QID,'FUTURE',user,source]],
                     [[QID,'RUNNING',user,source]]*2):
            worker,control=self.clients()
            with patch.object(control,'query',return_value=self.observed(rows)):
                result=M.drain(control,worker,TOKEN)
            self.assertFalse(result['verified']);self.assertEqual(worker.opener.requests,[])

    def test_unknown_post_waits_bound_and_does_not_adopt_id_for_cancel(self):
        worker,control=self.clients(known=False);user,source=M.identity(TOKEN)
        clock=[0]
        def now(): return clock[0]
        def sleep(duration): clock[0]+=duration
        def observed(*args):
            return self.observed([[OTHER,'RUNNING',user,source]] if clock[0]<1 else [])
        with patch.object(control,'query',side_effect=observed),patch.object(M.time,'monotonic',side_effect=now),patch.object(M.time,'sleep',side_effect=sleep):
            result=M.drain(control,worker,TOKEN)
        self.assertTrue(result['verified']);self.assertGreaterEqual(clock[0],32)
        self.assertEqual(worker.opener.requests,[]);self.assertIsNone(worker.records[0]['query_id'])
        self.assertEqual(result['observed_query_ids'],[OTHER])

    def test_unknown_deadline_and_cancel_uncertain_are_not_blind_retries(self):
        worker,control=self.clients();user,source=M.identity(TOKEN)
        worker.opener=Opener(OSError('SECRET cancel'))
        clock=[0]
        with patch.object(control,'query',return_value=self.observed([[QID,'RUNNING',user,source]])),patch.object(M.time,'monotonic',side_effect=lambda:clock[0]),patch.object(M.time,'sleep',side_effect=lambda _:clock.__setitem__(0,clock[0]+1)):
            result=M.drain(control,worker,TOKEN,timeout=3)
        self.assertFalse(result['verified']);self.assertEqual(len(worker.opener.requests),1)
        self.assertEqual(result['cancel_errors'],[QID]);self.assertNotIn('SECRET',json.dumps(result))

    def test_cancel_first_interrupt_is_preserved_if_secondary_save_raises(self):
        worker,control=self.clients();first=KeyboardInterrupt();worker.opener=Opener(first)
        user,source=M.identity(TOKEN);calls=[]
        def progress():
            calls.append(1)
            if len(calls)>1: raise SystemExit('secondary')
        worker.progress=progress
        with patch.object(control,'query',return_value=self.observed([[QID,'RUNNING',user,source]])):
            with self.assertRaises(KeyboardInterrupt) as caught:M.drain(control,worker,TOKEN)
        self.assertIs(caught.exception,first)


class CatalogTests(unittest.TestCase):
    def setup_client(self,*responses):
        self.opener=Opener(*responses);self.guards=[]
        client=M.CatalogRead(lambda:self.guards.append(1),opener=self.opener)
        return client

    def authenticate(self,client):
        secret={'data':{k:base64.b64encode(v.encode()).decode() for k,v in {'client_id':'ID','client_secret':'SECRET','realm':'realm'}.items()}}
        with patch.object(M.AUTH,'_read_secret',return_value=json.dumps(secret).encode()):client.authenticate()

    def test_fixed_get_only_config_credentials_discarded_and_two_reads(self):
        response={'metadata':{'table-uuid':'00000000-0000-4000-8000-000000000001'},'metadata-location':'s3://bucket/table/metadata/v1.metadata.json',
                  'config':{'secret':'CONFIG_SECRET'},'storage-credentials':[{'credential':'STORAGE_SECRET'}]}
        client=self.setup_client(Response({'access_token':'TOKEN_SECRET'}),Response(response),Response(response))
        self.authenticate(client)
        result=client.load(REQUEST);self.assertEqual(set(result),{'metadata','metadata-location'})
        client.load(REQUEST)
        with self.assertRaisesRegex(M.ClientError,'READ_LIMIT'):client.load(REQUEST)
        with self.assertRaisesRegex(M.ClientError,'RETRY_FORBIDDEN'):client.authenticate()
        self.assertEqual([r.get_method() for r in self.opener.requests],['POST','GET','GET'])
        self.assertEqual(self.opener.requests[1].full_url,'http://127.0.0.1:18181/api/catalog/v1/shirokuma_l1/namespaces/smoke/tables/fixture_v1')
        self.assertNotIn('SECRET',json.dumps(client.receipt()));self.assertNotIn('SECRET',json.dumps(result))
        client.close();self.assertEqual(client._auth.token,'')

    def test_auth_guard_failure_is_before_secret_read(self):
        client=M.CatalogRead(lambda:(_ for _ in ()).throw(M.ClientError('GUARD')))
        with patch.object(M.AUTH,'_read_secret') as secret:
            with self.assertRaises(M.ClientError):client.authenticate()
        secret.assert_not_called()

    def test_load_strict_origin_content_duplicate_limit_and_no_raw_error(self):
        variants=[Response({},content_type='text/html'),Response({},url='http://foreign/'),
                  Response(raw=b'{"metadata":{},"metadata":{}}'),Response(raw=b'{"metadata":NaN}'),
                  Response(raw=b'{"metadata":{},"metadata-location":"x","config":{"x":1e9999}}'),
                  Response(raw=b' '* (2*1024**2+1)),Response({'metadata':{},'metadata-location':'x','unexpected':'SECRET'}),
                  URLError('SECRET')]
        for response in variants:
            client=self.setup_client(Response({'access_token':'TOKEN_SECRET'}),response);self.authenticate(client)
            with self.assertRaisesRegex(M.ClientError,'^CATALOG_READ_FAILED$'):client.load(REQUEST)
            self.assertNotIn('SECRET',json.dumps(client.receipt()))

    def test_out_of_scope_or_unauthenticated_load_has_no_http(self):
        client=self.setup_client()
        for request in (REQUEST,{'command':'describe','table':'other.table'}, {'command':'describe','table':'smoke.a/../b'}, {'command':'tables','schema':'smoke'}):
            with self.assertRaises((M.ClientError,C.ContractError)):client.load(request)
        self.assertEqual(self.opener.requests,[])

    def test_auth_adapter_rejects_non_oauth_post_before_transport(self):
        client=self.setup_client()
        with self.assertRaisesRegex(M.ClientError,'^CATALOG_SCOPE_INVALID$'):
            client._auth.exchange('POST','/api/catalog/v1/shirokuma_l1/namespaces/smoke/tables/fixture_v1',{})
        self.assertEqual(self.opener.requests,[])

    def test_first_cancellation_during_failed_get_journal_is_not_suppressed(self):
        client=self.setup_client(Response({'access_token':'TOKEN_SECRET'}),OSError('ordinary'));self.authenticate(client)
        first=KeyboardInterrupt()
        def progress():
            if client.receipt()['reads'][0]['result']=='failed':raise first
        client.progress=progress
        with self.assertRaises(KeyboardInterrupt) as caught:client.load(REQUEST)
        self.assertIs(caught.exception,first)


class RuntimeTests(unittest.TestCase):
    def setup(self,*,teardown_error=None):
        self.identity={'uid':'00000000-0000-4000-8000-000000000001','name':'trino-local','namespace':'shirokuma-dev','spec_sha256':'a'*64}
        self.closed=[];self.snapshots=[]
        @contextmanager
        def trino():
            try:yield URL
            finally:
                self.closed.append('trino')
                if teardown_error:raise teardown_error
        @contextmanager
        def polaris(progress):
            progress({'state':'ready'})
            try:yield lambda:None
            finally:self.closed.append('polaris');progress({'state':'verified'})
        for patcher in (patch.object(M.REFS.SESSION,'check_context'),patch.object(M,'trino_service_identity',return_value=self.identity),
                        patch.object(M.REFS.SESSION,'trino_tunnel',side_effect=trino),patch.object(M.REFS,'polaris_tunnel',side_effect=polaris)):
            patcher.start();self.addCleanup(patcher.stop)

    def test_actual_runtime_context_guards_closes_without_auth_and_final_verified(self):
        self.setup()
        with patch.object(M.AUTH._Authentication,'authenticate') as auth:
            with M.runtime(TOKEN,self.snapshots.append) as (worker,control,catalog):
                self.assertFalse(catalog.receipt()['authenticated'])
                self.assertEqual(worker.source,M.identity(TOKEN)[1]);self.assertTrue(control.control)
        auth.assert_not_called();self.assertEqual(self.closed,['polaris','trino'])
        self.assertEqual(self.snapshots[-1]['state'],'verified');self.assertTrue(self.snapshots[-1]['cleanup']['verified'])

    def test_original_interrupt_survives_tunnel_teardown_failure(self):
        first=KeyboardInterrupt();self.setup(teardown_error=SystemExit('SECOND'))
        with self.assertRaises(KeyboardInterrupt) as caught:
            with M.runtime(TOKEN,self.snapshots.append):raise first
        self.assertIs(caught.exception,first);self.assertEqual(self.closed,['polaris','trino'])
        self.assertEqual(self.snapshots[-1]['state'],'failed');self.assertFalse(self.snapshots[-1]['cleanup']['verified'])

    def test_first_teardown_cancellation_outranks_ordinary_body_error(self):
        first=KeyboardInterrupt();self.setup(teardown_error=first)
        with self.assertRaises(KeyboardInterrupt) as caught:
            with M.runtime(TOKEN,self.snapshots.append):raise OSError('ordinary')
        self.assertIs(caught.exception,first)

    def test_failed_owner_can_still_record_successful_tunnel_cleanup(self):
        self.setup()
        with self.assertRaises(OSError):
            with M.runtime(TOKEN,self.snapshots.append):raise OSError('ordinary')
        self.assertEqual(self.snapshots[-1]['state'],'failed');self.assertTrue(self.snapshots[-1]['cleanup']['verified'])

    def test_final_journal_failure_cannot_yield_success(self):
        self.setup()
        def progress(value):
            self.snapshots.append(value)
            if value['state']=='verified':raise OSError('SECRET')
        with self.assertRaises(OSError):
            with M.runtime(TOKEN,progress):pass
        self.assertEqual(self.closed,['polaris','trino'])


if __name__=='__main__':unittest.main()
