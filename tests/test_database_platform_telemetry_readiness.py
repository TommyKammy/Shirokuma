"""Mocked loopback observations and captured HTTP requests on both surfaces."""
from dataclasses import replace
from datetime import datetime
from http.client import HTTPResponse
from types import SimpleNamespace
import io
import json
import unittest
from unittest.mock import call, patch

import test_database_platform_workload_baseline as base
import db_platform_telemetry_readiness as telemetry
from warehouse_operations import OperationError

auth = base.auth


class Socket:
    def __init__(self, raw):
        self.raw = raw

    def makefile(self, *_args):
        return io.BytesIO(self.raw)


def response(body=b'Ok.\n', *, status=200, headers=None):
    if headers is None:
        headers = f'Content-Length: {len(body)}\r\n'
    raw = f'HTTP/1.1 {status} Test\r\n{headers}\r\n'.encode() + body
    reply = HTTPResponse(Socket(raw))
    reply.begin()
    return reply


class TelemetryReadinessTests(unittest.TestCase):
    patch = base.BaselineTests.patch
    files = base.BaselineTests.files
    handlers = base.BaselineTests.handlers

    def setUp(self):
        base.BaselineTests.setUp(self)
        self.connection = self.patch(patch.object(telemetry, 'HTTPConnection'))
        self.http = self.connection.return_value
        self.http.getresponse.side_effect = lambda: response(
            b'Ok.\n' if self.http.request.call_args.args[1] == '/ping' else b' ready\r\n')

    def closed(self, result):
        self.assertEqual(set(result), {'agenthouse', 'loki'})
        for observation in result.values():
            self.assertEqual(set(observation), {'status', 'observed_at'})
            self.assertTrue(observation['observed_at'].endswith('Z'))
            datetime.fromisoformat(observation['observed_at'])

    def observe(self, principal=None, role='admin'):
        result = telemetry.observe(self.principal if principal is None else principal, role)
        self.closed(result)
        return tuple(result[name]['status'] for name in ('agenthouse', 'loki'))

    def test_fixed_destinations_requests_and_independent_times(self):
        with patch.object(telemetry, '_now', side_effect=['2026-11-04T00:00:00Z', '2026-11-04T00:00:01Z']):
            result = telemetry.observe(self.principal, 'admin')
        self.closed(result)
        self.assertEqual(result, {
            'agenthouse': {'observed_at': '2026-11-04T00:00:00Z', 'status': 'responsive'},
            'loki': {'observed_at': '2026-11-04T00:00:01Z', 'status': 'ready'},
        })
        self.assertEqual(self.connection.call_args_list, [
            call('127.0.0.1', 18123, timeout=2), call('127.0.0.1', 13100, timeout=2)])
        self.assertEqual(self.http.request.call_args_list, [
            call('GET', '/ping', headers={'Connection': 'close'}),
            call('GET', '/ready', headers={'Connection': 'close'})])
        self.assertEqual(self.http.close.call_count, 2)

    def test_tokens_failures_and_response_framing(self):
        for reply in [
            lambda: response(b'Ok.'),
            lambda: response(b'Ok.\n', status=301),
            lambda: response(b'Ok.\n', headers='Content-Length: 5\r\n'),
            lambda: response(b'Ok.\n', headers='Content-Length: 4\r\nContent-Length: 4\r\n'),
            lambda: response(b'Ok.\n', headers='Content-Length: +4\r\n'),
            lambda: response(b'Ok.\n', headers='Content-Length: 4\r\nTransfer-Encoding: chunked\r\n'),
            lambda: response(b'Ok.\n', headers='Malformed Header: x\r\n'),
            lambda: response(b'x' * 257),
            lambda: response(b'x' * 257, headers='Connection: close\r\n'),
        ]:
            with self.subTest(reply=reply):
                self.http.getresponse.side_effect = [reply(), response(b'ready')]
                self.assertEqual(self.observe(), ('unavailable', 'ready'))
        for second, expected in [
            (response(b'private message', status=503), 'not_ready'),
            (response(b'private message', status=503, headers='Content-Length: 30\r\n'), 'unavailable'),
            (response(b'not ready'), 'unavailable'),
            (response(b'ready\xff'), 'unavailable'),
            (response('\u00a0ready\n'.encode()), 'ready'),
            (TimeoutError('private timeout'), 'unavailable'),
        ]:
            self.http.getresponse.side_effect = [TimeoutError('private timeout'), second]
            self.assertEqual(self.observe(), ('unavailable', expected))

    def test_chunked_framing_requires_complete_bounded_chunks(self):
        headers = 'Transfer-Encoding: chunked\r\n'
        self.http.getresponse.side_effect = [
            response(b'1\r\nO\r\n3\r\nk.\n\r\n0\r\n\r\n', headers=headers),
            response(b'0\r\n\r\n', status=503, headers=headers),
        ]
        self.assertEqual(self.observe(), ('responsive', 'not_ready'))
        valid = b'4\r\nOk.\n\r\n0\r\n\r\n'
        cases = [
            (b'bad-length\r\n', headers),
            (b'4\r\nOk.', headers),
            (b'101\r\n' + b'x' * 257 + b'\r\n0\r\n\r\n', headers),
            (valid, headers + 'Content-Length: 4\r\n'),
            (valid, headers + headers),
            (valid, 'Transfer-Encoding: gzip, chunked\r\n'),
            (valid, headers + 'Content-Encoding: gzip\r\n'),
        ]
        for body, framing in cases:
            with self.subTest(length=len(body), framing=framing):
                self.http.getresponse.side_effect = [
                    response(body, headers=framing), response(b'ready')]
                self.assertEqual(self.observe(), ('unavailable', 'ready'))

    def test_authority_checked_between_and_after_even_on_failure(self):
        for port in (18123, 13100):
            with self.subTest(port=port):
                self.connection.reset_mock()
                self.users['p']['password_hash'] = 'before'

                def revoke():
                    path = self.http.request.call_args.args[1]
                    if path == ('/ping' if port == 18123 else '/ready'):
                        self.users['p']['password_hash'] = 'changed'
                        raise TimeoutError('private error')
                    return response()

                self.http.getresponse.side_effect = revoke
                with self.assertRaises(OperationError) as denied:
                    self.observe()
                self.assertEqual(denied.exception.status, 403)
                self.assertEqual(self.connection.call_count, 1 if port == 18123 else 2)

    def test_true_p_null_database_grant_no_store_reads_or_source_side_effects(self):
        before=self.files()
        with patch.object(base.audit,'snapshot',side_effect=AssertionError('store read')):
            self.closed(telemetry.observe(self.principal, 'admin'))
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
            with self.assertRaises(OperationError):telemetry.observe(principal,'admin')
        self.connection.assert_not_called()

    def request(self, handler, *, method='GET', suffix='', headers='', body='', role='admin', principal=None):
        class Connection:
            def __init__(self,raw):self.incoming,self.outgoing=io.BytesIO(raw),io.BytesIO()
            def makefile(self,*args):return self.incoming
            def sendall(self,raw):self.outgoing.write(raw)
        raw=(method+' /api/v1/bears/platform-telemetry-readiness'+suffix+' HTTP/1.1\r\n'
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
                self.connection.reset_mock()
                with self.subTest(surface=handler.__name__,options=options):
                    status,result=self.request(handler,**options);self.assertEqual(status,expected,result)
                    if expected==200:self.closed(result)
                    else:self.connection.assert_not_called();self.assertNotIn('agenthouse',result)
        self.assertEqual(before,self.files())
