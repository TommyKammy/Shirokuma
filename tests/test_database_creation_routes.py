"""Normal HTTP parser coverage without a listening socket or live profile."""
from contextlib import contextmanager
from http.client import HTTPResponse
import io
import json
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_database_creations import CreationFixture
import control_store
import logical_creations
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


class NameLookupRouteChecks:
    """Exercise both modes through the production HTTP handler and real store."""

    def lookup_target(self):
        status, ack, _ = self.request(body=dict(sql_catalog='research'))
        self.assertIn(status, (201, 202))
        return ack

    def insert_lookup_root(self, db, root, database, name, **overrides):
        operation = dict(db.execute('SELECT * FROM operations WHERE id=?', (root['create_operation_id'],)).fetchone())
        operation['id'] = 'op-'+name
        other_db = dict(database, id='db-'+name, sql_catalog=name)
        other_root = dict(root, id='root-'+name, database_id=other_db['id'],
                          create_operation_id=operation['id'], reservation_active=0, **overrides)
        for table, row in [('operations', operation), ('databases', other_db), ('db_creations', other_root)]:
            db.execute('INSERT INTO '+table+' ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')', tuple(row.values()))

    def test_name_lookup_exact_target_only_with_large_unrelated_population(self):
        ack = self.lookup_target()
        self.assertEqual(self.request('?limit=100')[0], 200)
        with control_store.connect() as db:
            root = dict(db.execute('SELECT * FROM db_creations WHERE id=?', (ack['creation_id'],)).fetchone())
            database = dict(db.execute('SELECT * FROM databases WHERE id=?', (root['database_id'],)).fetchone())
            for i in range(105):
                self.insert_lookup_root(db, root, database, 'unrelated_'+str(i))
        expected = self.request('/'+ack['creation_id'], identity='bob')[1]
        coordinator = logical_creations if logical_creations.enabled() else creations
        before = self.snapshot()
        with patch.object(coordinator, '_summary', wraps=coordinator._summary) as summary, \
                patch.object(coordinator, 'listing', side_effect=AssertionError('list scan')), \
                patch.object(logical_creations, 'dispatch_async') as dispatch, \
                patch('database_creation_bridge.dispatch') as legacy_dispatch, \
                patch.object(coordinator, 'mutate', side_effect=AssertionError('GET mutation')):
            status, detail, headers = self.request('/by-name/research', identity='bob')
        self.assertEqual(status, 200)
        self.assertEqual(detail, expected)
        self.assertEqual(detail['creation_id'], ack['creation_id'])
        self.assertEqual(detail['database_id'], root['database_id'])
        self.assertEqual(detail['next_actions'], [])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        summary.assert_called_once()
        self.assertEqual(summary.call_args.args[1]['id'], ack['creation_id'])
        dispatch.assert_not_called()
        legacy_dispatch.assert_not_called()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.request('/'+ack['creation_id'])[0], 200)
        self.assertEqual(self.request('/by-name/research_extra')[:2], (404, dict(error='DATABASE_NOT_FOUND', message='Database creation request rejected')))
        self.assertEqual(self.request('/by-name/%72esearch')[1]['creation_id'], ack['creation_id'])

    def test_name_lookup_unknown_stopped_and_retired_are_observations_only(self):
        ack = self.lookup_target()
        is_logical = logical_creations.enabled()
        if not is_logical:
            _, linked, _ = self.prepare(ack)
        for state in ('UNKNOWN', 'STOPPED', 'RETIRED'):
            with self.subTest(state=state):
                with control_store.connect() as db:
                    if is_logical:
                        db.execute('UPDATE db_creations SET logical_state=?,reservation_active=0 WHERE id=?',
                                   ('UNKNOWN' if state=='UNKNOWN' else 'STOPPED', ack['creation_id']))
                    elif state == 'UNKNOWN':
                        db.execute("UPDATE operations SET state='UNKNOWN' WHERE id=?", (linked['operation_id'],))
                    else:
                        db.execute('UPDATE db_creations SET reservation_active=0 WHERE id=?', (ack['creation_id'],))
                    if state == 'RETIRED':
                        db.execute("UPDATE databases SET state='retired' WHERE id=(SELECT database_id FROM db_creations WHERE id=?)", (ack['creation_id'],))
                before = self.snapshot()
                expected = self.request('/'+ack['creation_id'])[1]
                with patch.object(logical_creations, 'dispatch_async') as dispatch, \
                        patch('database_creation_bridge.dispatch') as legacy_dispatch, \
                        patch.object(logical_creations, 'mutate', side_effect=AssertionError('GET mutation')), \
                        patch.object(creations, 'mutate', side_effect=AssertionError('GET mutation')):
                    status, detail, _ = self.request('/by-name/research')
                self.assertEqual(status, 200)
                self.assertEqual(detail, expected)
                self.assertEqual(detail['creation_id'], ack['creation_id'])
                self.assertEqual(detail['progress_code'],
                    'UNKNOWN' if is_logical and state=='UNKNOWN' else
                    'RECONCILE_REQUIRED' if state=='UNKNOWN' else 'STOPPED')
                self.assertEqual(before, self.snapshot())
                dispatch.assert_not_called()
                legacy_dispatch.assert_not_called()

    def test_name_lookup_auth_validation_and_framing(self):
        self.lookup_target()
        self.assertEqual(self.request('/by-name/research', identity=None)[0], 401)
        self.assertEqual(self.request('/by-name/research', role='bi_viewer')[0], 403)
        for tail in ('', 'Research', 'research/extra', 'research%2fextra', '%FF', '%C0%AF',
                     '%', '%2', '%GG', '%2572esearch', 'research%252fextra', 'research%00',
                     'research?limit=1', 'research#fragment'):
            with self.subTest(tail=tail):
                self.assertEqual(self.request('/by-name/'+tail)[0], 400)
        for kwargs in (dict(headers=[('Content-Length', '0')]),
                       dict(headers=[('Transfer-Encoding', 'chunked')]),
                       dict(raw=b'x', method='GET')):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.request('/by-name/research', **kwargs)[0], 400)
        self.accounts['alice']['platform_admin'] = False
        self.save_accounts()
        self.assertEqual(self.request('/by-name/research')[0], 403)
        del self.accounts['alice']
        self.save_accounts()
        self.assertEqual(self.request('/by-name/research')[0], 401)

    def test_name_lookup_environment_mode_and_ambiguity_fail_closed(self):
        ack = self.lookup_target()
        with control_store.connect() as db:
            original = dict(db.execute('SELECT * FROM db_creations WHERE id=?', (ack['creation_id'],)).fetchone())
        with control_store.connect() as db:
            database = dict(db.execute('SELECT * FROM databases WHERE id=?', (original['database_id'],)).fetchone())
            self.insert_lookup_root(db, original, database, 'foreign_name', environment_id='foreign')
            self.insert_lookup_root(db, original, database, 'other_mode',
                creation_mode='legacy' if original['creation_mode']=='logical_v1' else 'logical_v1')
        for name in ('foreign_name', 'other_mode'):
            with self.subTest(name=name):
                self.assertEqual(self.request('/by-name/'+name)[:2],
                    (404, dict(error='DATABASE_NOT_FOUND', message='Database creation request rejected')))
        connect = control_store.connect
        @contextmanager
        def duplicate_match():
            with connect() as db:
                class Duplicate:
                    def execute(inner, sql, args=()):
                        result = db.execute(sql, args)
                        if 'SELECT root.id FROM db_creations root' in sql:
                            self.assertIn('JOIN databases', sql)
                            self.assertIn('LIMIT 2', sql)
                            row = result.fetchone()
                            return SimpleNamespace(fetchall=lambda: [row, row])
                        return result
                yield Duplicate()
        with patch.object(control_store, 'connect', side_effect=duplicate_match), \
                patch.object(creations, 'get', side_effect=AssertionError('ambiguous projection')), \
                patch.object(logical_creations, 'get', side_effect=AssertionError('ambiguous projection')):
            self.assertEqual(self.request('/by-name/research')[:2], (409, dict(error='DATABASE_NAME_AMBIGUOUS', message='Database creation request rejected')))


class CreationRouteTests(NameLookupRouteChecks, CreationFixture):
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
