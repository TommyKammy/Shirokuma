"""Exact cached-prefix export, current authorization, and both HTTP surfaces."""
import base64
from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal
from http.client import HTTPResponse
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store
import query_result_exports as exports
import query_service as qs
from query_context import QueryError
import request_auth
import runtime_profile
import warehouse_resident as wr
import workbench_server as wb


class ResultExportTests(unittest.TestCase):
    def setUp(self):
        from test_dataops_service import DataOpsTests
        self.f = DataOpsTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.context = self.f.context()

    def submit(self, pages=None):
        pages = pages or [dict(id='backend', columns=[dict(name='n', type='bigint')], data=[[1]])]
        with patch.object(qs, '_transport', side_effect=pages):
            page = qs.submit(self.f.principal, self.context, 'SELECT 1', mode='read-only',
                             max_rows=100, request_id='request', worksheet_id='worksheet')
        self.id, self.first = page['id'], page
        self.pin = qs._pins[self.id]
        self.body = dict(format='json', request_id='request', worksheet_id='worksheet',
                         received_pages=[page['result_page_id']])
        return page

    def perform(self, body=None):
        return exports.export(self.f.principal, self.id, self.context.selected_role, body or self.body)

    def content(self, body=None):
        return base64.b64decode(self.perform(body)['content_base64']).decode('utf-8')

    def assert_code(self, code, action):
        with self.assertRaises(QueryError) as error:
            action()
        self.assertEqual(error.exception.code, code)

    def test_precision_null_dialect_duplicate_columns_and_recursive_cells(self):
        columns = [dict(name='same', type='varchar') for _ in range(10)]
        row = [2**63-1, -(2**63), Decimal('12345678901234567890.00010'), None, '', 'NULL', '\\N',
               'quote"\r\n雪', [2**63, True, None, {'n': -(2**63), 'small':[1,-1,0]}], 1.25]
        self.submit([dict(id='backend', columns=columns, data=[row, row])])
        result = self.perform()
        decoded = json.loads(base64.b64decode(result['content_base64']))
        self.assertEqual(decoded['rows'][0], [str(row[0]), str(row[1]), str(row[2]), *row[3:8],
            [str(2**63), True, None, {'n': str(-(2**63)), 'small':['1','-1','0']}], 1.25])
        self.assertEqual(decoded['rows'][0], decoded['rows'][1])
        self.assertEqual(decoded['columns'], columns)
        self.assertEqual(decoded['encoding'], {'integer_cells': 'decimal-string'})
        self.assertIs(type(decoded['query']['context']['binding_revision']), int)
        self.assertEqual((result['returned_rows'], result['saved_rows'], result['partial']), (2, 2, False))
        self.assertEqual(set(result), {'format','filename','content_type','content_base64','query_id','request_id',
            'worksheet_id','context','returned_rows','saved_rows','state','partial','truncated'})
        for format, separator in (('csv', ','), ('tsv', '\t')):
            text = self.content(self.body | {'format': format})
            self.assertTrue(text.startswith(separator.join(['"same"'] * 10) + '\r\n'))
            self.assertIn('"9223372036854775807"' + separator + '"-9223372036854775808"', text)
            self.assertIn(separator.join(['\\N', '""', '"NULL"', '"\\N"']), text)
            self.assertIn('"quote""\r\n雪"', text)
            self.assertTrue(text.endswith('\r\n'))
        for format in ('json','csv','tsv'):
            body=self.body | {'format':format}
            encoded=base64.b64decode(self.perform(body)['content_base64'])
            with patch.object(exports,'MAX_BYTES',len(encoded)):
                self.assertEqual(base64.b64decode(self.perform(body)['content_base64']),encoded)
            with patch.object(exports,'MAX_BYTES',len(encoded)-1):
                self.assert_code('RESULT_EXPORT_TOO_LARGE',lambda:self.perform(body))

    def test_graph_prefix_pending_aliases_no_value_dedup_and_missing_page(self):
        first = self.submit([dict(id='backend', columns=[], data=[],
            nextUri='http://127.0.0.1:1234/v1/statement/backend/1')])
        token = first['next_uri'].rsplit('/', 1)[1]
        self.pin.responses['pending-alias'] = deepcopy(first)
        self.assertTrue(self.perform()['partial'])
        with patch.object(qs, '_transport', return_value=dict(id='backend', columns=[dict(name='n',type='bigint')], data=[[1],[1]])):
            second = qs.fetch(self.f.principal, self.id, token, selected_role=self.context.selected_role)
        self.assertNotEqual(first['result_page_id'], second['result_page_id'])
        self.assertEqual(qs.fetch(self.f.principal, self.id, token, selected_role=self.context.selected_role), second)
        self.assertTrue(self.perform()['partial'])  # ledger finished, received prefix still incomplete
        full = self.body | {'received_pages': [first['result_page_id'], second['result_page_id']]}
        self.assertEqual(json.loads(self.content(full))['rows'], [['1'],['1']])
        self.assertFalse(self.perform(full)['partial'])
        for receipts in ([second['result_page_id']], [first['result_page_id'], 'missing'],
                         [second['result_page_id'], first['result_page_id']]):
            self.assert_code('RESULT_EXPORT_UNAVAILABLE', lambda: self.perform(full | {'received_pages':receipts}))
        del self.pin.responses[token]
        self.assert_code('RESULT_EXPORT_UNAVAILABLE', lambda: self.perform(full))

    def test_cache_and_original_metadata_rejected_without_query_side_effects(self):
        self.submit()
        before = dict(qs._row(self.id))
        with ExitStack() as stack:
            for name in ('fetch','_receive','_transport','_best_cancel','_state'):
                stack.enter_context(patch.object(qs, name, side_effect=AssertionError(name)))
            self.perform()
            for field, value in (('id','other'), ('request_id','other'), ('worksheet_id','other'),
                                 ('context', self.first['context'] | {'database_id':self.f.databases[1]}),
                                 ('backend_query_id','other')):
                with patch.dict(self.pin.initial_response, {field:value}):
                    self.assert_code('RESULT_EXPORT_UNAVAILABLE', self.perform)
            self.assert_code('CONTEXT_MISMATCH', lambda: self.perform(self.body | {'worksheet_id':'other'}))
            with patch.dict(qs._pins, {}, clear=True):
                self.assert_code('RESULT_EXPORT_UNAVAILABLE', self.perform)
        self.assertEqual(dict(qs._row(self.id)), before)

    def test_owner_admin_no_cross_owner_revocation_and_post_serialization_revocation(self):
        self.submit()
        owner = self.f.fixture.principal('owner')
        self.assert_code('QUERY_NOT_FOUND', lambda: exports.export(owner,self.id,'admin',self.body))
        original = exports._serialize
        def revoke(*args):
            result = original(*args)
            with control_store.connect() as db:
                db.execute("DELETE FROM grants WHERE principal_id='alice' AND database_id=?",(self.context.database_id,))
            return result
        with patch.object(exports, '_serialize', side_effect=revoke), patch.object(qs,'_best_cancel') as cancel:
            self.assert_code('DB_ACCESS_REVOKED',self.perform)
            cancel.assert_not_called()
        self.assert_code('DB_ACCESS_REVOKED',self.perform)

    def test_serialization_bounds_malformed_rows_and_nonfinite(self):
        self.submit()
        with patch.object(exports,'MAX_BYTES',8):
            self.assert_code('RESULT_EXPORT_TOO_LARGE',self.perform)
        self.assert_code('RESULT_EXPORT_TOO_LARGE',lambda:self.perform(self.body | {'received_pages':['a']*4097}))
        for data in ([[1,2]], [[float('nan')]], [[Decimal('Infinity')]], [[object()]]):
            with patch.dict(self.pin.initial_response, {'data':data}):
                self.assert_code('RESULT_EXPORT_UNAVAILABLE',self.perform)
        for value in ([], ['x','x'], [1], 'x'):
            self.assert_code('INVALID_RESULT_EXPORT',lambda:self.perform(self.body | {'received_pages':value}))
        self.assert_code('INVALID_RESULT_EXPORT',lambda:self.perform(self.body | {'sql':'SELECT 1'}))

    def test_current_account_role_binding_generation_schema_and_saved_role(self):
        self.submit()
        self.assert_code('CONTEXT_MISMATCH',lambda:exports.export(self.f.principal,self.id,'admin',self.body))
        users=self.f.fixture.users
        users['alice']['role']='bi_viewer'; self.f.fixture.save()
        self.assert_code('DB_ACCESS_REVOKED',self.perform)
        users['alice']['role']='data_engineer'; self.f.fixture.save()
        removed=users.pop('alice'); self.f.fixture.save()
        self.assert_code('DB_ACCESS_REVOKED',self.perform)
        users['alice']=removed; self.f.fixture.save()
        mutations = (
            ('UPDATE catalog_bindings SET desired_revision=2 WHERE database_id=?', (self.context.database_id,),
             'UPDATE catalog_bindings SET desired_revision=1 WHERE database_id=?'),
            ('UPDATE catalog_bindings SET applied_generation_id=NULL WHERE database_id=?',(self.context.database_id,),
             'UPDATE catalog_bindings SET applied_generation_id=? WHERE database_id=?'),
            ("UPDATE schemas SET sql_name='changed' WHERE id=?",(self.context.schema_id,),
             "UPDATE schemas SET sql_name='raw' WHERE id=?"))
        for sql,args,restore in mutations:
            with self.subTest(sql=sql):
                with control_store.connect() as db: db.execute(sql,args)
                self.assert_code('DB_ACCESS_REVOKED',self.perform)
                with control_store.connect() as db:
                    db.execute(restore,(self.context.generation_id,self.context.database_id) if 'applied_generation_id' in restore else args)
        self.perform()
        with patch.dict(self.pin.headers,{'X-Trino-User':'changed-identity'}):
            self.assert_code('RESULT_EXPORT_UNAVAILABLE',self.perform)
        with control_store.connect() as db:
            db.execute('UPDATE grants SET revision=revision+1 WHERE principal_id=? AND database_id=?',('alice',self.context.database_id))
        self.assert_code('DB_ACCESS_REVOKED',self.perform)

    def test_authority_rechecked_inside_lock_after_snapshot_and_serialization(self):
        self.submit()
        original=qs._owned
        for boundary in (2,3,4):
            calls=[]
            def owned(*args,**kwargs):
                calls.append(kwargs)
                if len(calls)==boundary:
                    raise QueryError('DB_ACCESS_REVOKED',403)
                return original(*args,**kwargs)
            with patch.object(qs,'_owned',side_effect=owned), patch.object(qs,'_best_cancel') as cancel:
                self.assert_code('DB_ACCESS_REVOKED',self.perform)
                self.assertTrue(all(item == {'cleanup':False} for item in calls))
                cancel.assert_not_called()

    def test_plan_dataops_and_execution_closed_queries_rejected(self):
        self.submit()
        with patch.object(self.pin,'plan',object()):
            self.assert_code('RESULT_EXPORT_UNAVAILABLE',self.perform)
        operation=self.f.begin()
        with control_store.connect() as db:
            db.execute('INSERT INTO dataops_queries(operation_id,step,query_id,action) VALUES (?,?,?,?)',
                       (operation.id,'step',self.id,'select'))
        self.assert_code('RESULT_EXPORT_UNAVAILABLE',self.perform)
        with control_store.connect() as db:
            db.execute('DELETE FROM dataops_queries')
            db.execute("UPDATE warehouse_admissions SET execution_closure='FENCED_READ_ONLY' WHERE subject_id=?",(self.id,))
        self.assert_code('EXECUTION_CLOSED',self.perform)
        operation.cancel()

    def test_three_page_gap_cross_query_and_column_conflict(self):
        first=self.submit([dict(id='backend',columns=[dict(name='n',type='bigint')],data=[[1]],
            nextUri='http://127.0.0.1:1234/v1/statement/backend/1')])
        with patch.object(qs,'_transport',side_effect=[
                dict(id='backend',data=[[2]],nextUri='http://127.0.0.1:1234/v1/statement/backend/2'),
                dict(id='backend',data=[[3]])]):
            second=qs.fetch(self.f.principal,self.id,first['next_uri'].rsplit('/',1)[1],selected_role='data_engineer')
            third=qs.fetch(self.f.principal,self.id,second['next_uri'].rsplit('/',1)[1],selected_role='data_engineer')
        full=self.body | {'received_pages':[p['result_page_id'] for p in (first,second,third)]}
        self.assertEqual(json.loads(self.content(full))['rows'],[['1'],['2'],['3']])
        self.assert_code('RESULT_EXPORT_UNAVAILABLE',lambda:self.perform(full | {'received_pages':[first['result_page_id'],third['result_page_id']]}))
        token=first['next_uri'].rsplit('/',1)[1]
        cached=self.pin.responses[token]
        for change in ({'id':'foreign-query'}, {'context':second['context'] | {'schema_id':'foreign'}},
                       {'columns':[dict(name='changed',type='bigint')]}):
            with patch.dict(cached,change):
                self.assert_code('RESULT_EXPORT_UNAVAILABLE',lambda:self.perform(full))

    def test_empty_finished_truncated_and_byte_limit_exact_boundary(self):
        self.submit([dict(id='backend',data=[])])
        result=self.perform()
        self.assertEqual((result['saved_rows'],result['partial']),(0,False))
        self.assertEqual(json.loads(self.content())['columns'],[])
        encoded=base64.b64decode(result['content_base64'])
        with patch.object(exports,'MAX_BYTES',len(encoded)):
            self.assertEqual(base64.b64decode(self.perform()['content_base64']),encoded)
        with patch.object(exports,'MAX_BYTES',len(encoded)-1):
            self.assert_code('RESULT_EXPORT_TOO_LARGE',self.perform)
        with patch.dict(self.pin.initial_response,{'truncated':True}):
            self.assertTrue(self.perform()['partial'])
            self.assertTrue(self.perform()['truncated'])

    def test_cold_pending_aliases_share_one_actual_receipt(self):
        from test_query_cold_resume import ColdQueryTests
        cold=ColdQueryTests(); cold.setUp(); self.addCleanup(cold.doCleanups)
        cold.transport.return_value['columns']=[dict(name='n',type='bigint')]
        pending=cold.submit()
        self.assertTrue(cold.entered.wait(2))
        self.assertNotIn('result_page_id',pending)
        alias=cold.fetch(pending)
        self.assertNotIn('result_page_id',alias)
        cold.settle(pending)
        actual=cold.fetch(alias)
        self.assertEqual(actual['result_page_id'],qs._pins[pending['id']].initial_response['result_page_id'])
        result=exports.export(cold.f.principal,pending['id'],'data_engineer',dict(format='json',
            request_id='correlation',worksheet_id='worksheet',received_pages=[actual['result_page_id']]))
        self.assertEqual(json.loads(base64.b64decode(result['content_base64']))['rows'],[['42']])
        self.assertEqual(cold.transport.call_count,1)

    def test_logical_identity_rebound_or_revoked_never_exports_cached_bytes(self):
        from test_logical_access import LogicalAccessTests
        import logical_registry as registry
        fixture=LogicalAccessTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        context=fixture.context()
        with control_store.connect() as db:
            db.execute("INSERT INTO query_records VALUES ('logical-query','alice','db','marts','LOCAL','foundation','FINISHED','now')")
            db.execute("INSERT INTO query_execution_contexts VALUES ('logical-query','bi_viewer','read-only','db','marts',1,1,'alice-id-user','pin','backend','request','worksheet','SELECT 1')")
        row=qs._row('logical-query')
        pin=qs._Pin(context,'',{'X-Trino-User':'alice-id-user'},None)
        pin.initial_response=dict(id='logical-query',result_page_id='receipt',backend_query_id='backend',
            request_id='request',worksheet_id='worksheet',context=qs._metadata(row),
            columns=[dict(name='n',type='bigint')],data=[[1]],state='FINISHED',next_uri=None)
        body=dict(format='json',request_id='request',worksheet_id='worksheet',received_pages=['receipt'])
        def perform(): return exports.export(fixture.alice,'logical-query','bi_viewer',body)
        with patch.dict(qs._pins,{'logical-query':pin}),patch.object(qs,'_transport') as transport:
            self.assertEqual(perform()['saved_rows'],1)
            with control_store.connect() as db:
                db.execute("UPDATE query_execution_contexts SET backend_username='old-user' WHERE query_id='logical-query'")
            self.assert_code('DB_ACCESS_REVOKED',perform)
            with control_store.connect() as db:
                db.execute("UPDATE query_execution_contexts SET backend_username='alice-id-user' WHERE query_id='logical-query'")
                identity=dict(db.execute("SELECT * FROM logical_backend_identities WHERE principal_id='alice'").fetchone())
                registry.revoke_identity(db,identity['id'])
            self.assert_code('DB_ACCESS_REVOKED',perform)
            with control_store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                candidate=registry.put_identity(db,dict(identity,id='replacement',backend_username='replacement-user',
                    credential_ref='replacement-ref',state='CANDIDATE',verification_ref=None,revision=1))
                registry.put_identity(db,dict(candidate,state='ACTIVE',verification_ref='verified',revision=2))
            self.assert_code('DB_ACCESS_REVOKED',perform)  # same epoch, replacement current username
            transport.assert_not_called()

    def handlers(self):
        handlers = [wb.WorkbenchHandler]
        class Capture:
            def __init__(self,address,handler): handlers.append(handler)
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def handle_request(self): pass
        with ExitStack() as stack:
            for target,value in (('warehouse_resident._profile_active',True),('logical_creations.enabled',False),
                                 ('warehouse_resident.warehouse','LOCAL')):
                stack.enter_context(patch(target,return_value=value))
            stack.enter_context(patch('http.server.ThreadingHTTPServer',Capture))
            stack.enter_context(patch('database_creation_bridge.configure'))
            wr.serve_endpoint(once=True)
        return handlers

    def request(self,handler,method='POST',suffix='',body=None,headers=None,*,path=None,
                principal=None,role='data_engineer',auth_error=None):
        class Connection:
            def __init__(self,raw): self.incoming,self.outgoing=io.BytesIO(raw),io.BytesIO()
            def makefile(self,*args): return self.incoming
            def sendall(self,raw): self.outgoing.write(raw)
        body = json.dumps(self.body) if body is None else body
        headers = f'Content-Type: application/json\r\nContent-Length: {len(body.encode())}\r\n' if headers is None else headers
        path = path if path is not None else f'/api/v1/queries/{self.id}/results/export{suffix}'
        raw = (f'{method} {path} HTTP/1.1\r\n'
               f'Host: 127.0.0.1\r\nConnection: close\r\nX-Shirokuma-Role: {role}\r\n'+headers+'\r\n'+body).encode()
        conn=Connection(raw)
        with patch.object(runtime_profile,'active',return_value=True), \
                patch.object(request_auth,'authenticate_principal',return_value=principal or self.f.principal,
                             side_effect=auth_error),patch.object(handler,'log_message'):
            handler(conn,('127.0.0.1',1),SimpleNamespace())
        response=HTTPResponse(Connection(conn.outgoing.getvalue())); response.begin()
        return response.status,json.loads(response.read())

    def worksheet_body(self):
        # The HTTP contract uses LOCAL; the isolated service fixture defaults to w.
        with control_store.connect() as db:
            db.execute("UPDATE warehouse_config_generations SET warehouse_id='LOCAL'")
        from query_context import resolve_context
        self.context = resolve_context(self.f.principal, self.context.database_id,
            self.context.schema_id, 'data_engineer', warehouse_id='LOCAL', expected_binding_revision=1)
        return dict(sql='SELECT 1',request_id='worksheet-request',worksheet_id='worksheet-source',
                    context={key:getattr(self.context,key) for key in
                        ('database_id','schema_id','warehouse_id','binding_revision','generation_id')},
                    mode='read-only',max_rows=100)

    def native_worksheet(self,handler):
        body = self.worksheet_body()
        status, first = self.request(handler,path='/api/v1/queries',body=json.dumps(body))
        self.assertEqual(status,200,first)
        self.id = first['id']
        self.body = dict(format='csv',request_id=body['request_id'],worksheet_id=body['worksheet_id'],
                         received_pages=[first['result_page_id']])
        return first

    def test_native_actual_handler_submit_pages_export_and_shared_history(self):
        handler = self.handlers()[1]
        columns = [dict(name='n',type='bigint')]
        pages = [dict(id='backend',columns=columns,data=[[1]],
                      nextUri='http://127.0.0.1:1234/v1/statement/backend/1'),
                 dict(id='backend',columns=columns,data=[[2]])]
        with patch.object(qs,'submit',wraps=qs.submit) as submit, \
                patch.object(qs,'_transport',side_effect=pages) as transport:
            first = self.native_worksheet(handler)
            submit.assert_called_once()
            self.assertEqual((first['request_id'],first['worksheet_id']),('worksheet-request','worksheet-source'))
            self.assertEqual(first['context'],self.context.public_metadata())
            self.assertEqual((first['data'],first['columns'],first['state']),([[1]],columns,'RUNNING'))
            self.assertTrue(first['result_page_id'])
            self.assertEqual(qs._pins[self.id].initial_response,first)
            status, second = self.request(handler,'GET',path=first['next_uri'],body='',headers='')
            self.assertEqual(status,200,second)
            self.assertEqual((second['id'],second['data'],second['state'],second['next_uri']),
                             (self.id,[[2]],'FINISHED',None))
            self.assertNotEqual(first['result_page_id'],second['result_page_id'])
            self.assertEqual(self.request(handler,'GET',path=first['next_uri'],body='',headers=''),(200,second))
            self.body['received_pages'].append(second['result_page_id'])
            for surface in (handler,wb.WorkbenchHandler):
                status, result = self.request(surface)
                self.assertEqual(status,200,result)
                self.assertEqual(base64.b64decode(result['content_base64']),b'"n"\r\n"1"\r\n"2"\r\n')
                self.assertEqual((result['saved_rows'],result['partial']),(2,False))
            self.assertEqual(self.request(handler,'GET',path=f'/api/v1/queries/{self.id}',body='',headers=''),
                (200,qs.inspect_query(self.f.principal,self.id,selected_role='data_engineer')))
            history_path = '/api/v1/queries?database_id=' + self.context.database_id
            status, history = self.request(handler,'GET',path=history_path,body='',headers='')
            self.assertEqual(status,200,history)
            self.assertEqual(history,qs.history(self.f.principal,database_id=self.context.database_id,
                                              selected_role='data_engineer'))
            self.assertEqual([item['id'] for item in history['queries']],[self.id])
            self.assertEqual(transport.call_count,2)
            self.assertEqual([call.args[1] for call in transport.call_args_list],['POST','GET'])

    def test_native_namespace_inherits_strict_query_and_context_validation(self):
        handler = self.handlers()[1]
        body = self.worksheet_body()
        with patch.object(qs,'_transport') as transport:
            for raw in ('{}','[]','{"sql":"SELECT 1","sql":"SELECT 2"}',
                        json.dumps(body | {'max_rows':True}),json.dumps(body | {'extra':1})):
                status, error = self.request(handler,path='/api/v1/queries',body=raw)
                self.assertEqual((status,error['code']),(400,'INVALID_INPUT'))
            for path in ('/api/v1/queries?x=1','/api/v1/queries?database_id=',
                         '/api/v1/queries?database_id=a&database_id=b',
                         '/api/v1/queries/missing?x=1','/api/v1/queries/missing/pages/token?x=1'):
                status, error = self.request(handler,'GET',path=path,body='',headers='')
                self.assertEqual((status,error['code']),(400,'INVALID_INPUT'))
            for altered, code, status in (
                    (body | {'context':body['context'] | {'generation_id':'old'}},'STALE_CONTEXT',409),
                    (body | {'sql':'SELECT 1; SELECT 2'},'SQL_PERMISSION_DENIED',403)):
                actual, error = self.request(handler,path='/api/v1/queries',body=json.dumps(altered))
                self.assertEqual((actual,error['code']),(status,code))
            transport.assert_not_called()

    def test_native_namespace_owner_role_and_current_auth_are_shared(self):
        handler = self.handlers()[1]
        with patch.object(qs,'_transport',return_value=dict(id='backend',data=[[1]])) as transport:
            self.native_worksheet(handler)
            owner = self.f.fixture.principal('owner')
            self.assertTrue(owner.platform_admin)
            routes = [('GET',f'/api/v1/queries/{self.id}',''),
                      ('GET',f'/api/v1/queries/{self.id}/pages/token',''),
                      ('POST',f'/api/v1/queries/{self.id}/results/export',json.dumps(self.body)),
                      ('POST',f'/api/v1/queries/{self.id}/cancel','{}')]
            for method,path,body in routes:
                for principal,role,expected in ((owner,'admin',(404,'QUERY_NOT_FOUND')),
                        (self.f.principal,'bi_viewer',(409,'CONTEXT_MISMATCH'))):
                    status, error = self.request(handler,method,path=path,body=body,principal=principal,role=role)
                    self.assertEqual((status,error['code']),expected)
            history_path = '/api/v1/queries?database_id=' + self.context.database_id
            self.assertEqual(self.request(handler,'GET',path=history_path,body='',headers='',
                                         principal=owner,role='admin'),(200,{'queries':[]}))
            for path in (f'/api/v1/queries/{self.id}',history_path):
                status,error = self.request(handler,'GET',path=path,body='',headers='',
                    auth_error=request_auth.AuthenticationError('invalid credentials'))
                self.assertEqual((status,error['code']),(401,'AUTHENTICATION_REQUIRED'))
            del self.f.fixture.users['alice']
            self.f.fixture.save()
            for method,path,body in (routes[0],routes[2],('GET',history_path,'')):
                status,error = self.request(handler,method,path=path,body=body)
                self.assertEqual((status,error['code']),(403,'DB_ACCESS_REVOKED'))
            transport.assert_called_once()

    def test_native_protocol_routes_keep_native_dispatch(self):
        handler = self.handlers()[1]
        with patch.object(wr,'_native_request',return_value={'native':True}) as native, \
                patch.object(runtime_profile,'current',return_value=SimpleNamespace(sql_endpoint_url='https://native')), \
                patch.object(wb.WorkbenchHandler,'database_request',side_effect=AssertionError('query API delegation')):
            for method,path,body in (('POST','/v1/statement','SELECT 1'),('GET','/v1/info',''),
                                     ('GET','/api/v1/queries-other','')):
                self.assertEqual(self.request(handler,method,path=path,body=body),(200,{'native':True}))
                self.assertEqual(native.call_args.args[1:],(path,method,body.encode(),'https://native'))
                self.assertEqual(native.call_args.kwargs,{'warehouse_id':'LOCAL'})
            self.assertEqual(native.call_count,3)

    def test_both_http_surfaces_strict_inputs(self):
        self.submit()
        for handler in self.handlers():
            with self.subTest(handler=handler):
                status,result=self.request(handler)
                self.assertEqual(status,200)
                self.assertEqual(result['saved_rows'],1)
                for suffix in ('?','#','#x','?x=1','?x','?format=csv&format=json','#fragment'):
                    self.assertEqual(self.request(handler,suffix=suffix)[0],400)
                for body in ('{}','{"format":"json","format":"csv"}', '[]', '{"format":NaN}'):
                    self.assertEqual(self.request(handler,body=body)[0],400)
                for headers in ('Content-Type: application/json\r\n',
                    'Content-Type: application/json\r\nContent-Length: 2\r\nContent-Length: 2\r\n',
                    'Content-Type: application/json\r\nContent-Length: 2\r\nTransfer-Encoding: chunked\r\n',
                    'Content-Type: text/plain\r\nContent-Length: 2\r\n'):
                    self.assertEqual(self.request(handler,body='{}',headers=headers)[0],400)
                self.assertEqual(self.request(handler,method='GET',body='',headers='')[0],404)


if __name__ == '__main__':
    unittest.main()
