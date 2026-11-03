"""Recent failure source selection, schema compatibility and both captured HTTP faces.

Authored for ROOT host execution. No real AgentHouse or network is used.
"""
from copy import deepcopy
from dataclasses import replace
from http.client import HTTPResponse
from types import SimpleNamespace
import io
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_database_platform_workload_baseline as base
import db_platform_recent_failures as recent
from warehouse_operations import OperationError

audit, auth, wr = base.audit, base.auth, base.wr


def record(**changes):
    row = dict(event_id='duplicate', timestamp='2001-02-03 04:05:06.007', agent_id='',
        action_type='saved_action', target_system='', status='failed', trace_id='', duration_ms='18446744073709551615')
    row.update(changes)
    return row


class RecentFailuresTests(unittest.TestCase):
    patch = base.BaselineTests.patch
    files = base.BaselineTests.files
    handlers = base.BaselineTests.handlers

    def setUp(self):
        base.BaselineTests.setUp(self)
        self.client = self.patch(patch.object(recent.manage, 'Client'))
        self.rows = self.client.return_value.rows
        self.rows.return_value = [record()]
        for name in ('ensure_runtime', 'docker', 'check_context', 'insert', 'export', 'smoke', 'write_private'):
            self.forbidden.append(self.patch(patch.object(recent.manage, name, side_effect=AssertionError(name))))

    def observe(self, principal=None, role='admin'):
        return recent.observe(self.principal if principal is None else principal, role)

    def closed(self, result, available=True):
        self.assertEqual(set(result), {'scope','warehouse','selected_role','observed_at','source',
            'availability','code','coverage','items','truncated'})
        self.assertEqual((result['scope'], result['warehouse'], result['selected_role'], result['source']),
            ('platform','LOCAL','admin','agenthouse_pawprints'))
        self.assertTrue(result['observed_at'].endswith('Z'))
        self.assertEqual(result['coverage'], dict(kind='recent_failed_pawprints_metadata', limit=20,
            server_filtered=True, database_correlation=False, raw_logs=False, health_check=False,
            policy_audit=False, complete_history=False))
        self.assertEqual(result['availability'], 'available' if available else 'unavailable')
        self.assertEqual(result['code'], None if available else 'RECENT_FAILURES_UNAVAILABLE')
        if available:
            self.assertIs(type(result['truncated']), bool)
            for row in result['items']:
                self.assertEqual(set(row), set(audit.TELEMETRY_FIELDS))
                self.assertEqual(row['status'], 'failed')
                self.assertIs(type(row['duration_ms']), str)
        else:
            self.assertEqual(result['items'], [])
            self.assertIsNone(result['truncated'])
        raw=json.dumps(result,default=str).encode()
        self.assertLessEqual(len(raw),65536)
        for secret in (b'error-secret',b'payload-secret',b'SELECT ',b'raw_logs_secret'):
            self.assertNotIn(secret,raw)

    def test_exact_server_where_before_limit_with_over_100_newer_successes(self):
        # SQLite executes the *actual* SQL, with an attached source schema. A
        # latest-100 preview followed by local filtering would return no failures.
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.row_factory=sqlite3.Row
        db.execute("ATTACH DATABASE ':memory:' AS agenthouse")
        db.execute('CREATE TABLE agenthouse.pawprints (' + ','.join(k+' TEXT' for k in audit.TELEMETRY_FIELDS) + ')')
        rows=[record(event_id='f%03d'%i,timestamp='2001-02-03 04:05:06.007') for i in range(25)]
        rows += [record(event_id='s%03d'%i,timestamp='2026-11-03 04:05:06.007',status='success') for i in range(150)]
        db.executemany('INSERT INTO agenthouse.pawprints VALUES (?,?,?,?,?,?,?,?)',
            [tuple(r[k] for k in audit.TELEMETRY_FIELDS) for r in rows])
        def execute(sql, *, timeout):
            self.assertEqual(timeout,3)
            return [dict(r) for r in db.execute(sql)]
        self.rows.side_effect=execute
        expected=("SELECT event_id,timestamp,agent_id,action_type,target_system,status,trace_id,duration_ms"
            " FROM agenthouse.pawprints WHERE status='failed' ORDER BY timestamp DESC,event_id DESC LIMIT 21")
        self.assertEqual(recent.SQL,expected)
        result=self.observe();self.closed(result)
        self.assertEqual([r['event_id'] for r in result['items']],['f%03d'%i for i in range(24,4,-1)])
        self.assertTrue(result['truncated'])
        self.client.assert_called_once_with();self.rows.assert_called_once_with(expected,timeout=3)
        self.assertIs(recent.TELEMETRY_FIELDS,audit.TELEMETRY_FIELDS)

    def test_zero_twenty_sentinel_and_duplicates_are_source_records(self):
        for size in (0,1,20,21):
            self.rows.return_value=[record() for _ in range(size)]
            result=self.observe();self.closed(result)
            self.assertEqual(len(result['items']),min(size,20));self.assertEqual(result['truncated'],size>20)
            self.assertEqual([r['event_id'] for r in result['items']],['duplicate']*min(size,20))

    def test_old_space_utc_timestamp_empty_strings_and_exact_uint64(self):
        for duration in (0,'0',2**53+1,str(2**53+1),2**64-1,str(2**64-1)):
            self.rows.return_value=[record(duration_ms=duration,event_id='',action_type='')]
            result=self.observe();self.closed(result)
            self.assertEqual(result['items'][0],dict(record(duration_ms=str(duration),event_id='',action_type=''),
                timestamp='2001-02-03T04:05:06.007Z'))
        self.rows.return_value=[record(timestamp='2000-02-29 23:59:59.999')]
        self.assertEqual(self.observe()['items'][0]['timestamp'],'2000-02-29T23:59:59.999Z')

    def test_extra_raw_fields_stripped_and_scalar_field_dto_bounds(self):
        self.rows.return_value=[record(payload='payload-secret',raw_logs_secret={'nested':'secret'})]
        result=self.observe();self.closed(result);self.assertEqual(set(result['items'][0]),set(audit.TELEMETRY_FIELDS))
        self.rows.return_value=[record(event_id='界'*256)]
        self.closed(self.observe())
        for field in ('event_id','agent_id','action_type','target_system','trace_id'):
            for value in (None,True,5,1.5,[],{},'x'*257,'bad\x00','bad\n','\ud800'):
                self.rows.return_value=[record(**{field:value})]
                with self.subTest(field=field,value=repr(value)):self.closed(self.observe(),False)
        self.rows.return_value=[dict(record(),**{'extra%d'%i:'x' for i in range(25)})]
        self.closed(self.observe(),False)
        # Individually bounded fields can still exceed the complete DTO budget.
        self.rows.return_value=[record(**{k:'界'*256 for k in ('event_id','agent_id','action_type','target_system','trace_id')}) for _ in range(21)]
        self.closed(self.observe(),False)

    def test_malformed_every_row_including_sentinel_never_partial_or_refill(self):
        invalid=[None,[],True,'raw',{},record(status='success'),record(status='FAILED')]
        invalid += [{k:v for k,v in record().items() if k!=field} for field in audit.TELEMETRY_FIELDS]
        for bad in invalid:
            for index in (0,20):
                rows=[record() for _ in range(21)];rows[index]=bad;self.rows.return_value=rows;self.rows.reset_mock()
                self.closed(self.observe(),False);self.rows.assert_called_once_with(recent.SQL,timeout=3)
        for rows in (None,{},'raw',tuple(),[record()]*22):
            self.rows.return_value=rows;self.closed(self.observe(),False)

    def test_uint64_and_timestamp_invalid_forms_sanitized(self):
        for duration in (True,False,1.0,1.5,-1,2**64,str(2**64),'01','+1','-0','1.0','1e3','１','9'*21,None,{},[]):
            self.rows.return_value=[record(duration_ms=duration)];self.closed(self.observe(),False)
        for stamp in ('2001-02-29 00:00:00.000','2026-01-01 24:00:00.000','2026-01-01 00:00:60.000',
                      '2026-01-01T00:00:00.000Z','2026-01-01 00:00:00','raw',None,0):
            self.rows.return_value=[record(timestamp=stamp)];self.closed(self.observe(),False)

    def test_final_capture_timestamp_is_counted_in_actual_available_dto_size(self):
        initial = '2026-11-03T04:00:00Z'
        final = '2026-11-03T04:00:00.123456Z'
        expected = dict(scope='platform', warehouse='LOCAL', selected_role='admin',
            observed_at=initial, source='agenthouse_pawprints', availability='available',
            code=None, coverage=dict(recent.COVERAGE),
            items=[dict(record(), timestamp='2001-02-03T04:05:06.007Z')], truncated=False)
        budget = len(json.dumps(expected, default=str).encode('utf-8'))
        self.assertLess(budget, 1024)
        with patch.object(recent, 'MAX_DTO_BYTES', budget), patch.object(recent, '_now', return_value=initial):
            self.assertEqual(self.observe(), expected)
        expected['observed_at'] = final
        self.assertGreater(len(json.dumps(expected, default=str).encode('utf-8')), budget)
        with patch.object(recent, 'MAX_DTO_BYTES', budget), \
                patch.object(recent, '_now', side_effect=[initial, final, final]):
            result = self.observe()
        self.closed(result, False)
        self.assertEqual(result['observed_at'], final)
        self.assertLessEqual(len(json.dumps(result, default=str).encode('utf-8')), budget)

    def test_outage_and_client_init_error_unavailable_not_empty_success(self):
        for target in (self.rows,self.client):
            for error in (OSError('error-secret'),recent.manage.AgentHouseError('error-secret'),ValueError('error-secret')):
                target.side_effect=error;self.closed(self.observe(),False)
            target.side_effect=None

    def test_true_p_null_database_grant_no_store_reads_or_source_side_effects(self):
        before=self.files()
        with patch.object(audit,'snapshot',side_effect=AssertionError('store read')):
            self.closed(self.observe())
        self.assertEqual(self.files(),before)
        self.foundation.assert_not_called();self.kube.assert_not_called();self.runner.assert_not_called()
        for forbidden in self.forbidden:forbidden.assert_not_called()

    def test_non_p_roles_current_account_and_principal_checked_before_client_init(self):
        principals=[auth.AuthenticatedPrincipal('dba','admin',False),replace(self.principal,principal_id='unknown')]
        principals += [replace(self.principal,platform_admin=v) for v in (False,1,'true',None)]
        principals += [replace(self.principal,maximum_role=v) for v in ('data_engineer','bi_viewer','invalid')]
        for principal in principals:
            with self.assertRaises(OperationError) as caught:self.observe(principal)
            self.assertEqual(caught.exception.status,403)
        for role in ('data_engineer','bi_viewer','invalid',None):
            with self.assertRaises(OperationError):self.observe(role=role)
        for change in ({'enabled':False},{'disabled':True},{'platform_admin':False},{'platform_admin':1},
                       {'role':'data_engineer'},{'role':'bi_viewer'}):
            old=self.users['p'].copy();self.users['p'].update(change)
            with self.assertRaises(OperationError):self.observe()
            self.users['p']=old
        for principal in (None,object()):
            with self.assertRaises(OperationError):recent.observe(principal,'admin')
        self.client.assert_not_called()

    def test_midread_revocation_or_fingerprint_change_wins_even_source_error(self):
        for change in ({'platform_admin':False},{'enabled':False},{'role':'data_engineer'},{'password_hash':'changed'}):
            for fail in (False,True):
                old=self.users['p'].copy()
                def rows(*args,**kwargs):
                    self.users['p'].update(change)
                    if fail:raise OSError('error-secret')
                    return [record()]
                self.rows.side_effect=rows
                with self.assertRaises(OperationError) as caught:self.observe()
                self.assertEqual((caught.exception.code,caught.exception.status),('PLATFORM_ACCESS_DENIED',403))
                self.users['p']=old

    def test_authority_after_projection_prevents_late_disclosure(self):
        original=recent._records
        def project(rows):
            result=original(rows);self.users['p']['password_hash']='changed';return result
        with patch.object(recent,'_records',side_effect=project),self.assertRaises(OperationError):self.observe()

    def request(self, handler, *, method='GET', suffix='', headers='', body='', role='admin', principal=None):
        class Connection:
            def __init__(self,raw):self.incoming,self.outgoing=io.BytesIO(raw),io.BytesIO()
            def makefile(self,*args):return self.incoming
            def sendall(self,raw):self.outgoing.write(raw)
        raw=(method+' /api/v1/bears/platform-recent-failures'+suffix+' HTTP/1.1\r\n'
            'Host: 127.0.0.1\r\nConnection: close\r\nX-Shirokuma-Role: '+role+'\r\n'+headers+'\r\n'+body).encode()
        conn=Connection(raw)
        with patch.object(base.runtime_profile,'active',return_value=True),patch.object(auth,'authenticate_principal',return_value=principal or self.principal),patch.object(handler,'log_message'):
            handler(conn,('127.0.0.1',1),SimpleNamespace())
        response=HTTPResponse(Connection(conn.outgoing.getvalue()));response.begin()
        self.response_bytes = response.read()
        return response.status,json.loads(self.response_bytes)

    def test_both_captured_http_surfaces_strict_get_zero_inputs_and_headers(self):
        cases=[({},200),({'headers':'Content-Length: 0\r\n'},200),
            ({'headers':'X-Shirokuma-Role: admin\r\n'},400),
            ({'headers':'Authorization: Basic x\r\nAuthorization: Basic x\r\n'},400),
            ({'headers':'Cookie: a=b\r\nCookie: a=b\r\n'},400),
            ({'headers':'X-Unrelated: a\r\nX-Unrelated: b\r\n'},400),
            ({'headers':'Bad Header: value\r\n'},400),({'headers':'X-Test: bad\x01value\r\n'},400),
            *[({'suffix':s},400) for s in ('?','?x=1','?x=&x=','?database_id=secret','#fragment','?%FF')],
            *[({'headers':'Content-Length: '+s+'\r\n','body':'x' if s=='1' else ''},400) for s in ('1','00','+0','-1','nope')],
            ({'headers':'Content-Length: 0\r\nContent-Length: 0\r\n'},400),
            ({'headers':'Content-Length: 2\r\nContent-Type: application/json\r\n','body':'{}'},400),
            ({'headers':'Transfer-Encoding: \r\n'},400),
            ({'headers':'Transfer-Encoding: chunked\r\n','body':'0\r\n\r\n'},400),
            ({'method':'POST','headers':'Content-Length: 0\r\n'},404),
            ({'role':'data_engineer'},403),({'role':'bi_viewer'},403),
            ({'principal':auth.AuthenticatedPrincipal('dba','admin',False)},403)]
        before=self.files()
        for handler in self.handlers():
            for options,expected in cases:
                self.client.reset_mock()
                with self.subTest(surface=handler.__name__,options=options):
                    status,result=self.request(handler,**options);self.assertEqual(status,expected,result)
                    if expected==200:self.closed(result)
                    else:self.client.assert_not_called();self.assertNotIn('items',result)
        self.assertEqual(before,self.files())

    def test_both_http_surfaces_bound_actual_ascii_escaped_unicode_wire_size(self):
        candidate = self.observe()
        event_id = '12345678-1234-4234-8234-123456789abc'
        large = [record(event_id=event_id, **{key: '界' * 160 for key in
                 ('agent_id', 'action_type', 'target_system', 'trace_id')})
                 for _ in range(20)]
        candidate['items'] = [dict(row, timestamp='2001-02-03T04:05:06.007Z') for row in large]
        self.assertLess(len(json.dumps(candidate, ensure_ascii=False).encode('utf-8')), 65536)
        self.assertGreater(len(json.dumps(candidate, default=str).encode('utf-8')), 65536)
        for handler in self.handlers():
            self.rows.return_value = large
            self.rows.reset_mock()
            status, result = self.request(handler)
            self.assertEqual(status, 200)
            self.closed(result, False)
            self.assertLessEqual(len(self.response_bytes), 65536)
            self.rows.assert_called_once_with(recent.SQL, timeout=3)
            # Ordinary small Unicode metadata is still a successful source read.
            self.rows.return_value = [record(event_id=event_id, agent_id='界')]
            status, result = self.request(handler)
            self.assertEqual(status, 200)
            self.closed(result)
            self.assertEqual(result['items'][0]['event_id'], event_id)
            self.assertEqual(result['items'][0]['agent_id'], '界')
            self.assertIn(b'\\u754c', self.response_bytes)
            self.assertLessEqual(len(self.response_bytes), 65536)

    def test_both_http_surfaces_source_outage_and_revocation_discard_rows(self):
        for handler in self.handlers():
            self.rows.side_effect=OSError('error-secret')
            status,result=self.request(handler);self.assertEqual(status,200);self.closed(result,False)
            def rows(*args,**kwargs):
                self.users['p']['platform_admin']=False;raise OSError('error-secret')
            self.rows.side_effect=rows
            status,result=self.request(handler);self.assertEqual(status,403);self.assertNotIn('items',result)
            self.assertEqual(result['code'],'PLATFORM_ACCESS_DENIED');self.users['p']['platform_admin']=True


if __name__ == '__main__':
    unittest.main()
