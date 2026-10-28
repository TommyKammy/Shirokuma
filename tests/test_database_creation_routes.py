"""Normal HTTP parser coverage without a listening socket or live profile."""
from http.client import HTTPResponse
import io
import json
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_database_creations import CreationFixture
import database_creations as creations
import workbench_server


class Connection:
    def __init__(self, data):
        self.incoming=io.BytesIO(data)
        self.outgoing=io.BytesIO()
    def makefile(self, mode, *args):
        return self.incoming if 'r' in mode else self.outgoing
    def sendall(self,data):
        self.outgoing.write(data)


class CreationRouteTests(CreationFixture):
    def request(self, tail='', *, body=None, raw=None, headers=(), role='admin', identity='alice',key='key',method=None):
        data=raw if raw is not None else b'' if body is None else json.dumps(body).encode()
        values=list(self.headers(identity).items()) if identity else []
        values=[(k,v) for k,v in values if k!='X-Shirokuma-Role']
        values += [('Host','127.0.0.1'),('Connection','close'),('X-Shirokuma-Role',role),('Content-Length',str(len(data)))]
        if body is not None or raw is not None:
            values.append(('Content-Type','application/json'))
        if key is not None:
            values.append(('Idempotency-Key',key))
        values+=list(headers)
        verb=method or ('POST' if body is not None or raw is not None else 'GET')
        wire=(verb+' '+creations.COLLECTION+tail+' HTTP/1.1\r\n'+''.join(k+': '+v+'\r\n' for k,v in values)+'\r\n').encode()+data
        connection=Connection(wire)
        with patch.object(workbench_server.WorkbenchHandler,'log_message'):
            workbench_server.WorkbenchHandler(connection,('127.0.0.1',1),SimpleNamespace())
        response=HTTPResponse(Connection(connection.outgoing.getvalue()))
        response.begin()
        return response.status,json.loads(response.read()),dict(response.headers)

    def test_create_replay_detail_location_and_no_store_normal_http(self):
        status,ack,headers=self.request(body=dict(sql_catalog='research'))
        self.assertEqual(status,201)
        self.assertEqual(headers['Location'],creations.COLLECTION+'/'+ack['creation_id'])
        self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertEqual(self.request(body=dict(sql_catalog='research'))[:2],(200,ack))
        detail=self.request('/'+ack['creation_id'])
        self.assertEqual(detail[0],200)
        self.assertEqual(detail[1]['lifecycle'],'registered')
        accepted=self.request('/'+ack['creation_id']+'/plans',body=dict(expected_revision=1,phase='bootstrap'),key='prepare')
        self.assertEqual(accepted[0],202)
        self.assertEqual(accepted[2]['Location'],headers['Location'])
        before=self.snapshot()
        with patch('database_creation_bridge.dispatch') as dispatch,\
                patch('database_creation_preparation.Preparation.prepare') as prepare:
            for owner,blocker,progress in [(True,None,'PREPARATION_PENDING'),
                    (False,'OPERATOR_UNAVAILABLE','BLOCKED'),(True,None,'PREPARATION_PENDING')]:
                with self.subTest(owner=owner):
                    self.probe.owner=owner
                    status,current,poll_headers=self.request('/'+ack['creation_id'])
                    self.assertEqual(status,200)
                    self.assertEqual(poll_headers['Cache-Control'],'no-store')
                    self.assertEqual(current['blocker'],blocker)
                    self.assertEqual(current['progress_code'],progress)
                    self.assertEqual(current['next_actions'],['withdraw'])
                    self.assertEqual(current['lifecycle'],'registered')
                    self.assertEqual(current['revision'],accepted[1]['accepted_revision'])
                    self.assertEqual(current['plans'],[])
                    self.assertEqual(self.snapshot(),before)
                    dispatch.assert_not_called()
                    prepare.assert_not_called()

    def test_strict_duplicate_headers_json_fields_origin_types_and_unknown_fields(self):
        cases=[dict(body=dict(sql_catalog='research'),headers=[('X-Shirokuma-Role','admin')]),
            dict(body=dict(sql_catalog='research'),headers=[('Idempotency-Key','second')]),
            dict(body=dict(sql_catalog='research'),key=None),
            dict(raw=b'{"sql_catalog":"one","sql_catalog":"two"}'),
            dict(body=dict(sql_catalog='research',actor='alice')),
            dict(body=dict(sql_catalog='research',initial_grants=True)),
            dict(body=dict(sql_catalog='research',display_name=False))]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.request(**kwargs)[0],400)
        self.assertEqual(self.request(body=dict(sql_catalog='research'),headers=[('Origin','https://unrelated.example')])[0],403)
        self.assertEqual(self.snapshot()['databases'],[])

    def test_authentication_selected_role_and_principal_secrecy(self):
        self.assertEqual(self.request('/principals',identity=None)[0],401)
        self.assertEqual(self.request('/principals',role='bi_viewer')[0],403)
        status,reply,_=self.request('/principals')
        self.assertEqual(status,200)
        self.assertTrue(all(set(p)=={'principal_id','display_name','maximum_role'} for p in reply['principals']))
        status,reply,_=self.request('/runtime-instance')
        self.assertEqual((status,reply['error']),(503,'OPERATOR_UNAVAILABLE'))

    def test_capability_missing_implementation_and_bad_queries_are_explicit(self):
        self.probe.owner=False
        self.assertEqual(self.request('/capabilities')[1]['blocker'],'OPERATOR_UNAVAILABLE')
        self.assertEqual(self.request(body=dict(sql_catalog='research'))[0],503)
        for tail in ('?limit=0','?limit=101','?limit=1&limit=2','?unknown=1','?cursor=!!!','/principals?limit=1'):
            with self.subTest(tail=tail):
                self.assertEqual(self.request(tail)[0],400)

    def test_other_admin_reads_but_cannot_mutate_and_no_public_attach(self):
        status,ack,_=self.request(body=dict(sql_catalog='research'))
        self.assertEqual(self.request('/'+ack['creation_id'],identity='bob')[0],200)
        self.assertEqual(self.request('/'+ack['creation_id']+'/withdraw',identity='bob',body=dict(expected_revision=1))[0],403)
        self.assertEqual(self.request('/'+ack['creation_id']+'/attach',body=dict(operation_id='arbitrary'))[0],404)
        self.assertEqual(self.request('/'+ack['creation_id']+'/plans',body=dict(expected_revision=True,phase='bootstrap'))[0],400)
