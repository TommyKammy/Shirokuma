"""Saved summaries: read-only provenance, authority, bounded keyset and HTTP."""
from contextlib import contextmanager
from dataclasses import replace
from http.client import HTTPResponse
import io
import json
import sqlite3
import socket
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

import test_database_platform_resources as base
import db_platform_lifecycle_history as history
from warehouse_operations import OperationError

STAMP = '2026-11-03T01:02:03.123456789+00:00'


class HistoryTests(unittest.TestCase):
    patch = base.ResourcesTests.patch
    files = base.ResourcesTests.files
    handlers = base.ResourcesTests.handlers

    def setUp(self):
        base.ResourcesTests.setUp(self)
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            db.executescript('''CREATE TABLE operations(id TEXT PRIMARY KEY,actor TEXT,action TEXT,
                targets TEXT,state TEXT,before_after TEXT,timestamps TEXT);
                CREATE TABLE warehouse_action_requests(operation_id TEXT,actor TEXT,route TEXT,payload TEXT,state TEXT);''')
        for target in ('warehouse_runtime_routes.status','warehouse_runtime_routes.operation',
                       'warehouse_runtime_routes._operation','warehouse_runtime_routes._owner_alive',
                       'warehouse_lifecycle.physical_started','os.kill'):
            self.forbidden.append(self.patch(patch(target, side_effect=AssertionError(target))))

    def insert(self, action='WAREHOUSE_WAKE', state='SUCCEEDED', meta=None, times=None, targets=None):
        identifier = str(uuid.uuid4())
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?)', (identifier,'actor-secret',action,
                json.dumps(['LOCAL'] if targets is None else targets),state,
                json.dumps(dict(pid=999, process='secret', message='secret') if meta is None else meta),
                json.dumps(dict(accepted=STAMP, succeeded=STAMP) if times is None else times)))
        return identifier

    def request_record(self, identifier, action='wake', **changes):
        row = dict(operation_id=identifier,actor='actor-secret',route='/api/v1/warehouses/LOCAL/runtime',
                   payload=json.dumps(dict(action=action,body={})),state='REQUESTED')
        row.update(changes)
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            db.execute('INSERT INTO warehouse_action_requests VALUES (?,?,?,?,?)', tuple(row.values()))

    def update(self, identifier, **changes):
        with sqlite3.connect(self.root / 'jobs.sqlite3') as db:
            for key, value in changes.items():
                self.assertIn(key, ('state','before_after','timestamps','targets','action'))
                db.execute('UPDATE operations SET '+key+'=? WHERE id=?', (value,identifier))

    def observe(self, **kwargs):
        return history.observe(self.principal, 'admin', **kwargs)

    def test_all_actions_policy_save_and_saved_semantics(self):
        for action in history.ACTIONS:
            self.insert(action, 'RECONCILED',dict(physical_started=True,observed_state='active',error_code='secret'))
        self.insert('WAREHOUSE_AUTO_CHILL',meta=dict(policy_change=True))
        r=self.observe();self.assertEqual(r['coverage'],history.COVERAGE)
        self.assertEqual({i['kind'] for i in r['items']},{'WAKE','CHILL','AUTO_CHILL','POLICY_SAVE','RECONCILE'})
        for row in r['items'][1:]:
            self.assertEqual(row['saved_state'],'RECONCILED');self.assertEqual(row['reconciled_observed_state'],'active')
            self.assertEqual(row['error_category'],'OTHER_RUNTIME_ERROR');self.assertEqual(row['dispatch_marker'],'recorded')
        self.assertEqual(r['items'][0]['dispatch_marker'],'not_recorded')
        self.assertEqual(r['items'][0]['kind'],'POLICY_SAVE')

    def test_positive_explicit_requests_are_unique_and_consistent(self):
        for kind,action in [('WAREHOUSE_WAKE','wake'),('WAREHOUSE_CHILL','chill'),('WAREHOUSE_RECONCILE','reconcile')]:
            identifier=self.insert(kind);self.request_record(identifier,action)
        identifier=self.insert('WAREHOUSE_AUTO_CHILL',meta=dict(policy_change=True))
        self.request_record(identifier,'auto-chill',payload=json.dumps(dict(action='auto-chill',body=dict(minutes=30,expected_revision=2))))
        self.assertEqual({r['origin'] for r in self.observe()['items']},{'explicit_request'})

    def test_missing_request_does_not_infer_wake_origin(self):
        self.insert();row=self.observe()['items'][0]
        self.assertEqual(row['origin'],'unrecorded');self.assertEqual(row['stop_trigger'],'unknown')

    def test_ambiguous_wrong_actor_route_action_and_malformed_are_unrecorded(self):
        for change in [dict(actor='other'),dict(route='/wrong'),dict(payload='[]'),dict(payload='{'),
                       dict(payload=json.dumps(dict(action='chill',body={}))),dict(payload='x'*16385)]:
            identifier=self.insert();self.request_record(identifier,**change)
        identifier=self.insert();self.request_record(identifier);self.request_record(identifier)
        self.assertEqual({r['origin'] for r in self.observe()['items']},{'unrecorded'})

    def test_noop_success_no_dispatch_and_stop_trigger(self):
        self.insert('WAREHOUSE_CHILL',meta=dict(request_kind='stop_request'))
        self.insert('WAREHOUSE_AUTO_CHILL',meta=dict(request_kind='auto_idle_request',error_code='WAREHOUSE_BUSY'))
        rows=self.observe()['items'];self.assertEqual([r['stop_trigger'] for r in rows],['auto_idle_request','stop_request'])
        self.assertEqual({r['dispatch_marker'] for r in rows},{'not_recorded'})
        self.assertEqual({r['saved_state'] for r in rows},{'SUCCEEDED'})

    def test_policy_flag_exact_boolean_duplicate_and_inconsistent_candidates(self):
        self.insert('WAREHOUSE_AUTO_CHILL')  # Real legacy missing flag is physical.
        self.insert('WAREHOUSE_AUTO_CHILL', meta={'policy_change': False})
        self.insert('WAREHOUSE_AUTO_CHILL', meta={'policy_change': True})
        for value in [1, 0, 'true', 'false', None]:
            self.insert('WAREHOUSE_AUTO_CHILL', meta={'policy_change': value})
        self.insert('WAREHOUSE_WAKE', meta={'policy_change': True})
        duplicate=self.insert('WAREHOUSE_AUTO_CHILL')
        self.update(duplicate,before_after='{"policy_change":true,"policy_change":false}')
        result=self.observe()
        self.assertEqual((result['scanned_count'],result['omitted_count']),(10,7))
        self.assertEqual([r['kind'] for r in result['items']],['POLICY_SAVE','AUTO_CHILL','AUTO_CHILL'])

    def test_stop_category_is_only_saved_action_never_metadata_request_kind(self):
        expected={'WAREHOUSE_WAKE':'unknown','WAREHOUSE_CHILL':'stop_request',
                  'WAREHOUSE_AUTO_CHILL':'auto_idle_request','WAREHOUSE_RECONCILE':'unknown'}
        for action in history.ACTIONS:
            for bogus in ['auto_idle_request','stop_request','unknown',None,1]:
                self.insert(action,meta={'request_kind':bogus})
        result=self.observe(limit=50)
        for row in result['items']:
            self.assertEqual(row['stop_trigger'],expected[row['action']])
        self.insert('WAREHOUSE_AUTO_CHILL',meta={'policy_change':True,'request_kind':'auto_idle_request'})
        self.assertEqual(self.observe()['items'][0]['stop_trigger'],'unknown')

    def test_benign_saved_writer_errors_are_preserved_and_secret_is_generic(self):
        for code in ['WAKE_FAILED','ADMISSION_CONFLICT','ENGINE_ACCESS_DENIED','WAREHOUSE_BUSY',
                     'WAREHOUSE_OBSERVATION_FAILED','WAREHOUSE_WAKE_FAILED','WAKE_CANCELED',
                     'FOUNDATION_NOT_READY','POLICY_WRITE_FAILED','WAREHOUSE_STOP_UNKNOWN']:
            self.insert(meta={'error_code':code})
        self.assertEqual({r['error_category'] for r in self.observe()['items']},
                         {'WAKE_FAILED','ADMISSION_CONFLICT','ENGINE_ACCESS_DENIED','WAREHOUSE_BUSY',
                          'WAREHOUSE_OBSERVATION_FAILED','WAREHOUSE_WAKE_FAILED','WAKE_CANCELED',
                          'FOUNDATION_NOT_READY','POLICY_WRITE_FAILED','WAREHOUSE_STOP_UNKNOWN'})

    def test_deep_metadata_skip_and_deep_origin_fallback_are_bounded(self):
        deep='['*6000+'0'+']'*6000
        self.assertLess(len(deep.encode()),16384)
        oldest=self.insert()
        gap=self.insert();self.update(gap,before_after='{"deep":'+deep+'}')
        valid=self.insert();self.request_record(valid,payload='{"body":'+deep+',"action":"wake"}')
        # Exercise the standard decoder's recursive implementation: CPython's C
        # decoder can accept this depth on some hosts without hitting its guard.
        with patch.object(json.scanner,'make_scanner',json.scanner.py_make_scanner):
            result=self.observe(limit=2)
        self.assertEqual((result['scanned_count'],result['omitted_count']),(2,1))
        self.assertEqual(result['items'][0]['operation_id'],valid)
        self.assertEqual(result['items'][0]['origin'],'unrecorded')
        older=self.observe(limit=2,cursor=result['next_cursor'])
        self.assertEqual([r['operation_id'] for r in older['items']],[oldest])
        self.assertEqual(older['omitted_count'],0)

    def test_max_rowid_ceiling_is_controlled_unavailable_and_cursor_invalid(self):
        identifier=self.insert()
        with sqlite3.connect(self.root/'jobs.sqlite3') as db:
            db.execute('UPDATE operations SET rowid=? WHERE id=?',(history.MAX_ROWID,identifier))
        result=self.observe()
        self.assertEqual((result['availability'],result['items']),('unavailable',None))
        token=history._cursor(20,history._authority(self.principal,'admin'),history.MAX_ROWID,history.MAX_ROWID)
        with self.assertRaises(OperationError) as caught:self.observe(cursor=token)
        self.assertEqual((caught.exception.code,caught.exception.status),('STALE_CURSOR',409))

    def test_unknown_secret_error_is_generic_and_no_private_fields(self):
        identifier=self.insert(meta=dict(error_code='SECRET_UPPERCASE',pid=8,query_ids=['query-secret'],actor='actor-secret',message='secret'))
        self.request_record(identifier)
        raw=json.dumps(self.observe());self.assertIn('OTHER_RUNTIME_ERROR',raw)
        for secret in ['actor-secret','SECRET_UPPERCASE','query-secret','pid','payload','before_after','principal_id','message']:
            self.assertNotIn(secret,raw)
        d=base.audit._untoken(self.observe()['page_cursor'])
        self.assertEqual(set(d),{'purpose','version','limit','authority','ceiling','last'})
        self.assertRegex(d['authority'],r'^[0-9a-f]{64}$')

    def test_utc_normalization_nanoseconds_missing_and_calendar(self):
        self.insert(times=dict(accepted=STAMP,started='2026-01-01T00:00:00Z'))
        row=self.observe()['items'][0];self.assertEqual(row['accepted_at'],STAMP.replace('+00:00','Z'))
        self.assertIsNone(row['completed_at']);self.assertIsNone(row['reconciled_at'])
        for stamp in ['2026-02-30T00:00:00Z','2026-01-01T00:00:60Z','2026-01-01T00:00:00+01:00',
                      '2026-01-01T00:00:00.1234567890Z','２０２６-01-01T00:00:00Z',True]:
            with self.assertRaises(ValueError):history._time(stamp)

    def test_malformed_oversize_rows_are_bounded_skips_without_refill(self):
        self.insert()
        for value in ['{','[]','{"x":1,"x":2}',json.dumps(dict(private='x'*16384))]:
            identifier=self.insert();self.update(identifier,before_after=value)
        r=self.observe(limit=3);self.assertEqual((r['scanned_count'],r['omitted_count'],r['items']),(3,3,[]))
        self.assertIsNotNone(r['next_cursor'])
        older=self.observe(limit=3,cursor=r['next_cursor']);self.assertEqual(len(older['items']),1)
        self.assertLessEqual(len(json.dumps(r).encode()),32768)

    def test_only_fixed_actions_exact_parsed_local_and_allowed_states(self):
        self.insert('QUERY');self.insert(targets=['OTHER']);self.insert(targets=['LOCAL','OTHER'])
        self.insert(state='CANCELED');self.insert(state='SUCCEEDED')
        r=self.observe();self.assertEqual((len(r['items']),r['omitted_count']),(1,1))

    def test_empty_available_is_not_unavailable(self):
        r=self.observe();self.assertEqual(r['items'],[]);self.assertEqual(r['availability'],'available')
        self.assertIsNotNone(r['page_cursor']);self.assertIsNone(r['next_cursor'])
        self.assertEqual(self.observe(cursor=r['page_cursor'])['items'],[])

    def test_keyset_append_ceiling_back_anchor_and_old_updates(self):
        ids=[self.insert() for _ in range(5)]
        first=self.observe(limit=2);second=self.observe(limit=2,cursor=first['next_cursor'])
        newest=self.insert();self.update(ids[2],state='UNKNOWN')
        third=self.observe(limit=2,cursor=second['next_cursor'])
        self.assertEqual([i['operation_id'] for i in first['items']],list(reversed(ids[-2:])))
        self.assertEqual([i['operation_id'] for i in third['items']],[ids[0]])
        back=self.observe(limit=2,cursor=second['page_cursor']);self.assertEqual(back['items'][0]['saved_state'],'UNKNOWN')
        self.assertEqual(self.observe(limit=2,cursor=first['page_cursor'])['items'],first['items'])
        self.assertEqual(self.observe(limit=2)['items'][0]['operation_id'],newest)

    def test_cursor_tamper_purpose_limit_authority_key_and_strict_types(self):
        self.insert();r=self.observe();cursor=r['page_cursor'];d=base.audit._untoken(cursor)
        bad=['bad',cursor+'x']
        for k,v in [('purpose','audit'),('version',True),('limit',True),('authority','other'),('ceiling',True),('last',0),('last',100)]:
            bad.append(base.audit._token(dict(d,**{k:v})))
        bad.append(base.audit._token(dict(d,extra=True)))
        for token in bad:
            with self.assertRaises(OperationError) as caught:self.observe(cursor=token)
            self.assertEqual((caught.exception.code,caught.exception.status),('STALE_CURSOR',409))
        with self.assertRaises(OperationError):self.observe(limit=19,cursor=cursor)
        with patch.object(base.audit,'_KEY',b'another-process'):
            with self.assertRaises(OperationError):self.observe(cursor=cursor)
        self.users['p']['password_hash']='new'
        with self.assertRaises(OperationError):self.observe(cursor=cursor)

    def test_no_database_or_grant_and_all_forbidden_sources_untouched(self):
        self.insert();before=self.files();self.assertEqual(len(self.observe()['items']),1)
        self.assertEqual(before,self.files())
        for mock in self.forbidden:mock.assert_not_called()
        self.runner.assert_not_called();self.foundation.assert_not_called()

    def test_all_non_p_and_current_revocation_before_snapshot(self):
        with patch.object(history.audit,'snapshot',side_effect=AssertionError('read')) as source:
            for principal in [base.auth.AuthenticatedPrincipal('dba','admin',False),replace(self.principal,platform_admin=False),
                              replace(self.principal,maximum_role='bi_viewer'),replace(self.principal,principal_id='missing')]:
                with self.assertRaises(OperationError):history.observe(principal,'admin')
            for role in ['data_engineer','bi_viewer',None]:
                with self.assertRaises(OperationError):history.observe(self.principal,role)
            self.users['p']['enabled']=False
            with self.assertRaises(OperationError):self.observe()
            source.assert_not_called()

    def test_mid_read_and_final_authority_loss_discard_data(self):
        self.insert();original=history._project
        for change in [dict(enabled=False),dict(platform_admin=False),dict(password_hash='changed')]:
            old=self.users['p'].copy()
            def project(*args):
                row=original(*args);self.users['p'].update(change);return row
            with patch.object(history,'_project',project),self.assertRaises(OperationError) as caught:self.observe()
            self.assertEqual(caught.exception.code,'PLATFORM_ACCESS_DENIED');self.users['p']=old
        original_snapshot=base.audit.snapshot
        @contextmanager
        def lose_before_read():
            with original_snapshot() as db:
                self.users['p']['enabled']=False;yield db
        with patch.object(base.audit,'snapshot',lose_before_read),self.assertRaises(OperationError):self.observe()

    def test_source_error_safe_unavailable_no_creation(self):
        (self.root/'jobs.sqlite3').unlink();before=self.files();r=self.observe()
        self.assertEqual((r['availability'],r['code'],r['items']),('unavailable','HISTORY_UNAVAILABLE',None))
        self.assertEqual(before,self.files())

    def request(self,handler,*,method='GET',suffix='',headers='',body='',role='admin',principal=None):
        class Connection:
            def __init__(self,raw):self.incoming,self.outgoing=io.BytesIO(raw),io.BytesIO()
            def makefile(self,*args):return self.incoming
            def sendall(self,raw):self.outgoing.write(raw)
        raw=(method+' /api/v1/bears/platform-runtime-history'+suffix+' HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\nX-Shirokuma-Role: '+role+'\r\n'+headers+'\r\n'+body).encode()
        conn=Connection(raw)
        with patch.object(base.runtime_profile,'active',return_value=True),patch.object(base.auth,'authenticate_principal',return_value=principal or self.principal),patch.object(handler,'log_message'):
            handler(conn,('127.0.0.1',1),SimpleNamespace())
        response=HTTPResponse(Connection(conn.outgoing.getvalue()));response.begin()
        return response.status,json.loads(response.read())

    def test_both_actual_http_faces_strict_framing_query_and_authority(self):
        self.insert()
        cases=[({},200),({'suffix':'?limit=1'},200),({'suffix':'?limit=50'},200),({'headers':'Content-Length: 0\r\n'},200),
               ({'suffix':'?'},400),({'suffix':'?limit=01'},400),({'suffix':'?limit=0'},400),({'suffix':'?limit=51'},400),
               ({'suffix':'?limit=１'},400),({'suffix':'?limit=%31'},400),({'suffix':'?cursor='},400),
               ({'suffix':'?limit=1&limit=1'},400),({'suffix':'?cursor=x&cursor=x'},400),({'suffix':'?database_id=secret'},400),
               ({'suffix':'?cursor=x'},409),({'suffix':'?cursor='+'x'*2049},400),({'suffix':'#x'},400),
               ({'headers':'Content-Length: 1\r\n','body':'x'},400),({'headers':'Content-Length: 00\r\n'},400),
               ({'headers':'Content-Length: 0\r\nContent-Length: 0\r\n'},400),({'headers':'Transfer-Encoding: \r\n'},400),
               ({'headers':'X-Other: a\r\nX-Other: a\r\n'},400),({'method':'POST','headers':'Content-Length: 0\r\n'},404),
               ({'role':'data_engineer'},403),({'role':'bi_viewer'},403),
               ({'principal':base.auth.AuthenticatedPrincipal('dba','admin',False)},403)]
        before=self.files()
        for handler in self.handlers():
            for options,expected in cases:
                with self.subTest(handler=handler.__name__,options=options):
                    status,body=self.request(handler,**options);self.assertEqual(status,expected,body)
            status,body=self.request(handler);self.assertEqual(status,200)
            self.assertEqual(self.request(handler,suffix='?cursor='+body['page_cursor'])[0],200)
        self.assertEqual(before,self.files())

    def test_both_socket_http_faces_and_no_runtime_effects(self):
        self.insert()
        before=self.files()
        for handler in self.handlers():
            try:
                server=ThreadingHTTPServer(('127.0.0.1',0),handler)
            except PermissionError as error:
                self.skipTest('Socket bind restricted by sandbox: '+str(error))
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                cases=[('', '', 'GET', 'admin', 200),('?limit=1','','GET','admin',200),
                       ('?limit=01','','GET','admin',400),('?cursor=bad','','GET','admin',409),
                       ('?limit=1&limit=1','','GET','admin',400),('?database_id=x','','GET','admin',400),
                       ('','Content-Length: 1\r\n','GET','admin',400),
                       ('','Content-Length: 0\r\nContent-Length: 0\r\n','GET','admin',400),
                       ('','Transfer-Encoding: chunked\r\n','GET','admin',400),
                       ('','','POST','admin',404),('','','GET','bi_viewer',403)]
                with patch.object(base.runtime_profile,'active',return_value=True), \
                        patch.object(base.auth,'authenticate_principal',return_value=self.principal), \
                        patch.object(handler,'log_message'):
                    for suffix,headers,method,role,status in cases:
                        with socket.socket() as client:
                            client.settimeout(3);client.connect(server.server_address)
                            client.sendall((method+' /api/v1/bears/platform-runtime-history'+suffix+' HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\nX-Shirokuma-Role: '+role+'\r\n'+headers+'\r\nx').encode())
                            response=HTTPResponse(client);response.begin()
                            self.assertEqual(response.status,status,response.read())
            finally:
                server.shutdown();server.server_close();thread.join(timeout=3)
        self.assertEqual(before,self.files())
        for mock in self.forbidden:mock.assert_not_called()


if __name__=='__main__':unittest.main()
