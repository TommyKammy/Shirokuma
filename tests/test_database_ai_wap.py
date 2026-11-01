"""Metadata-only B1 handoff: genuine isolated SQLite, synthetic REST, real AST/HTTP."""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import replace
from http.client import HTTPResponse
import io
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
import uuid

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools/local_experiment'))
with tempfile.TemporaryDirectory() as _bootstrap:
    with patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': _bootstrap}):
        import test_database_ai_plan_views as a3
        import db_ai as ai
        import db_ai_sql_proposal as b1
        import db_ai_wap as bridge
        import control_store as store
        import data_management as dm
        import database_inventory as inv
        import manual_wap
        import workbench_server as wb
        import warehouse_resident as wr
        import runtime_profile
        import request_auth as auth
        from query_context import QueryError


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.fixture = a3.ViewPlanTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture.f
        self.principal, self.context = self.f.principal, self.f.context
        self.destination = self.f.ctx(schema='sandbox')
        self.ident = str(uuid.uuid4())
        self.seed()

    def seed(self, sql='SELECT * FROM orders', names=(('raw', 'orders'),), context=None, principal=None):
        self.context = context or self.context
        self.principal = principal or self.principal
        refs = [inv.resolve_object(self.principal, self.context.selected_role, self.context.database_id,
            self.f.schemas[self.context.database_id][schema].id, dm.quote_identifier(table))['ref']
            for schema, table in names]
        self.pins, public = b1._capture(self.principal, self.context, refs)
        self.record = dict(id=self.ident, kind='sql_proposal', state='SUCCEEDED',
            actor=self.principal.principal_id, context=self.context.public_metadata(), sql=sql,
            input=dict(request=' \n 日本語 e\u0301 🔒 \t', relations=refs, language='en'),
            metadata=dict(relations=public, captured_at='frozen'), created_at='original-time')
        with store.connect() as db:
            db.execute('INSERT OR REPLACE INTO ai_explanations VALUES (?,?,?,?,?,?,?)',
                (self.ident, self.principal.principal_id, 'key', '{}', ai._json(self.context.public_metadata()),
                 ai._json(self.pins), ai._json(self.record)))
        self.f.catalog_calls.clear()

    def update(self, record=None, pins=None):
        with store.connect() as db:
            db.execute('UPDATE ai_explanations SET record=?,pins=? WHERE id=?',
                (ai._json(record or self.record), ai._json(self.pins if pins is None else pins), self.ident))

    def prepare(self, **kw):
        return bridge.prepare(kw.get('principal', self.principal), kw.get('context', self.context),
            kw.get('destination', self.destination), self.ident)

    def error(self, code, fn=None):
        with self.assertRaises(QueryError) as caught:
            (fn or self.prepare)()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    @contextmanager
    def readonly(self):
        connect = store.connect
        statements = []
        @contextmanager
        def read():
            with connect() as db:
                db.execute('PRAGMA query_only=ON')
                db.set_trace_callback(statements.append)
                yield db
        with patch.object(store, 'connect', read):
            yield statements
        self.assertFalse(any(s.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'BEGIN IMMEDIATE'))
                             for s in statements), statements)

    def test_closed_dto_provenance_readonly_and_pure_compile_bindings(self):
        before = dict(ai._row(self.ident))
        targets = ('db_ai._config', 'db_ai._complete', 'db_ai._capture', 'db_ai._owner',
            'db_ai_sql_proposal._capture', 'db_ai_sql_proposal.get',
            'database_inventory.resolve_object', 'database_inventory.object_detail',
            'database_inventory._item', 'data_management.observe_object',
            'manual_wap.save_model', 'manual_wap.build', 'manual_wap.audit', 'manual_wap.publish',
            'logical_registry.identity_credentials', 'trino_auth.prepare_backend_v2')
        with ExitStack() as stack:
            mocks = [stack.enter_context(patch(t, side_effect=AssertionError(t))) for t in targets]
            compile_model = stack.enter_context(patch.object(manual_wap, '_compile_model', wraps=manual_wap._compile_model))
            with self.readonly():
                result = self.prepare()
                again = self.prepare()
        self.assertEqual(set(result), {'version','proposal_id','source_context','destination_context',
            'original_sql','sql','sources','request','proposal_created_at','prepared_at'})
        self.assertEqual(result['version'], 1)
        self.assertEqual(result['original_sql'], self.record['sql'])
        self.assertEqual(result['request'], self.record['input']['request'])
        self.assertEqual(result['proposal_created_at'], 'original-time')
        self.assertEqual(result['source_context'], self.context.public_metadata())
        self.assertEqual(result['destination_context'], self.destination.public_metadata())
        self.assertEqual({k:v for k,v in result.items() if k != 'prepared_at'},
                         {k:v for k,v in again.items() if k != 'prepared_at'})
        item = result['sources'][0]
        self.assertEqual(set(item), {'alias','ref','schema','table'})
        self.assertEqual(item['ref'], self.record['input']['relations'][0])
        self.assertEqual(len(item['ref']), 6)
        bound = compile_model.call_args.args[1][item['alias']]
        self.assertIs(type(bound), dm.ResourceRef)
        self.assertEqual(len(vars(bound)), 4)
        self.assertEqual(compile_model.call_args.kwargs, {})
        self.assertEqual(dict(ai._row(self.ident)), before)
        self.assertFalse(any(m.called for m in mocks + self.f.forbidden))
        self.f.complete.assert_not_called()

    def test_source_schema_defaults_and_roles_two_databases(self):
        for database in self.f.databases:
            for schema in ('raw','marts','staging','sandbox'):
                for role in ('data_engineer','admin'):
                    with self.subTest(database=database.sql_catalog, schema=schema, role=role):
                        context = self.f.ctx(role=role, schema=schema, database=database)
                        self.seed(context=context, names=((schema,'orders'),))
                        self.destination = self.f.ctx(role=role, schema='sandbox', database=database)
                        draft = self.prepare()
                        self.assertEqual(draft['sources'][0]['schema'], schema)
                        self.assertEqual(self.f.catalog_calls[0], (database.sql_catalog, '/namespaces/'+schema+'/tables/orders'))

    def test_owned_kind_actor_context_and_states(self):
        self.error('AI_EXPLANATION_NOT_FOUND', lambda: self.prepare(principal=self.f.bob))
        for field, value in self.context.public_metadata().items():
            with self.subTest(field=field):
                bad = replace(self.context, **{field: value+1 if type(value) is int else 'wrong'})
                self.error('AI_EXPLANATION_NOT_FOUND', lambda: self.prepare(context=bad))
        self.record['kind'] = 'sql_explanation'; self.update()
        self.error('AI_EXPLANATION_NOT_FOUND')
        self.record['kind'] = 'sql_proposal'
        for state in ('QUEUED','RUNNING','FAILED','CANCELED','UNKNOWN'):
            self.record['state'] = state; self.update()
            self.error('AI_WAP_UNAVAILABLE')
            self.assertEqual(json.loads(ai._row(self.ident)['record'])['state'], state)
        self.assertEqual(self.f.catalog_calls, [])

    def test_malformed_persisted_shape_unavailable(self):
        original = deepcopy(self.record)
        mutations = [lambda r: r.update(sql=None), lambda r: r.update(sql=' '),
            lambda r: r.update(sql='x'*16385), lambda r: r.update(created_at=None),
            lambda r: r['input'].update(request=3), lambda r: r['input'].update(relations=[]),
            lambda r: r['input']['relations'][0].update(extra=1),
            lambda r: r['metadata']['relations'][0]['columns'][0].update(type='changed')]
        for mutate in mutations:
            r = deepcopy(original); mutate(r); self.update(r)
            self.error('AI_WAP_UNAVAILABLE')
        self.update(original, pins=[])
        self.error('AI_WAP_UNAVAILABLE')
        self.assertEqual(self.f.catalog_calls, [])

    def test_pairing_all_shared_fields_and_non_sandbox(self):
        for field, value in self.destination.public_metadata().items():
            if field in ('schema_id','sql_schema'): continue
            with self.subTest(field=field):
                bad = replace(self.destination, **{field: value+1 if type(value) is int else 'wrong'})
                self.error('AI_WAP_CONTEXT_MISMATCH', lambda: self.prepare(destination=bad))
        self.error('AI_WAP_CONTEXT_MISMATCH', lambda: self.prepare(destination=self.context))
        with store.connect() as db:
            db.execute("UPDATE schemas SET purpose='other' WHERE id=?", (self.destination.schema_id,))
        self.error('SQL_PERMISSION_DENIED')
        self.assertEqual(self.f.catalog_calls, [])

    def test_viewer_and_platform_owner_identity_not_overridden(self):
        context = self.f.ctx(role='bi_viewer', schema='marts')
        self.seed(context=context, names=(('marts','orders'),))
        self.destination = replace(context, schema_id=self.destination.schema_id, sql_schema='sandbox')
        self.error('SQL_PERMISSION_DENIED')
        context = self.f.ctx(role='admin', principal=self.f.owner)
        self.seed(context=context, principal=self.f.owner)
        self.destination = self.f.ctx(role='admin', schema='sandbox', principal=self.f.owner)
        self.assertIsNone(self.prepare()['source_context']['grant_revision'])
        self.f.catalog_calls.clear()
        with store.connect() as db:
            db.execute("UPDATE logical_backend_identities SET state='REVOKED' WHERE principal_id='owner'")
        self.error('DB_ACCESS_REVOKED')
        self.assertEqual(self.f.catalog_calls, [])

    def test_used_subset_unused_deleted_view_and_table(self):
        self.seed(names=(('raw','orders'),('marts','order_view'),('staging','customers')))
        with store.connect() as db:
            for pin in self.pins:
                if pin['table'] == 'orders': continue
                db.execute('UPDATE object_bindings SET current=0 WHERE id=?', (pin['ref']['object_id'],))
        self.f.objects.pop(('sales','marts','order_view','VIEW'))
        self.f.objects.pop(('sales','staging','customers','TABLE'))
        self.assertEqual(len(self.prepare()['sources']), 1)
        self.assertEqual(self.f.catalog_calls, [('sales','/namespaces/raw/tables/orders')])

    def test_used_view_and_constant_rejected_without_rest(self):
        self.seed('SELECT * FROM order_view', (('raw','order_view'),))
        self.error('AI_WAP_UNSUPPORTED')
        self.record['sql'] = 'SELECT 1'; self.update()
        self.error('AI_WAP_UNSUPPORTED')
        self.assertEqual(self.f.catalog_calls, [])

    def test_all_frozen_acl_checked_before_rest_even_unused_view(self):
        self.seed(names=(('raw','orders'),('marts','order_view')))
        with store.connect() as db:
            db.execute("UPDATE schemas SET purpose='restricted' WHERE id=?", (next(p['schema_id'] for p in self.pins if p['kind']=='VIEW'),))
        self.error('SQL_PERMISSION_DENIED')
        self.assertEqual(self.f.catalog_calls, [])

    def test_all_used_refs_prevalidated_before_first_rest(self):
        self.seed('SELECT * FROM orders CROSS JOIN customers', (('raw','orders'),('raw','customers')))
        with store.connect() as db:
            db.execute('UPDATE object_bindings SET current=0 WHERE id=?', (self.pins[1]['ref']['object_id'],))
        self.error('AI_METADATA_CHANGED')
        self.assertEqual(self.f.catalog_calls, [])

    def test_six_ref_fields_and_incarnation_identity_preflight(self):
        original_record, original_pins = deepcopy(self.record), deepcopy(self.pins)
        for field, value in original_pins[0]['ref'].items():
            with self.subTest(field=field):
                r,p = deepcopy(original_record), deepcopy(original_pins)
                changed = value+1 if type(value) is int else 'other'
                r['input']['relations'][0][field] = changed
                p[0]['ref'][field] = changed
                if field=='schema_id': p[0]['schema_id'] = changed
                self.update(r,p)
                self.error('SQL_PERMISSION_DENIED' if field=='schema_id' else 'AI_METADATA_CHANGED')
        self.assertEqual(self.f.catalog_calls, [])

    def test_append_and_empty_table_eligible(self):
        metadata = self.f.objects[('sales','raw','orders','TABLE')]
        for snapshot in (43, None, -1):
            metadata['current-snapshot-id'] = snapshot
            self.assertEqual(len(self.prepare()['sources']), 1)
        metadata.pop('current-snapshot-id')
        self.prepare()

    def test_uuid_and_current_columns_drift_rejected_without_observation(self):
        metadata = self.f.objects[('sales','raw','orders','TABLE')]
        original = deepcopy(metadata)
        for mode in ('uuid','name','type','order','schema'):
            metadata.clear(); metadata.update(deepcopy(original))
            if mode=='uuid': metadata['table-uuid'] = 'replacement'
            elif mode=='name': metadata['schemas'][0]['fields'][0]['name'] = 'changed'
            elif mode=='type': metadata['schemas'][0]['fields'][0]['type'] = 'string'
            elif mode=='order': metadata['schemas'][0]['fields'].reverse()
            else: metadata['current-schema-id'] = 2
            self.error('AI_METADATA_CHANGED')

    def test_current_both_contexts_and_frozen_acl_rechecked_after_rest(self):
        metadata = inv._metadata
        for mode in ('destination','source','unused','grant'):
            with self.subTest(mode=mode):
                # Restore only isolated test authority between cases.
                with store.connect() as db:
                    db.execute('UPDATE schemas SET purpose=sql_name')
                self.seed(names=(('raw','orders'),('marts','order_view')))
                def revoke(*args):
                    result = metadata(*args)
                    with store.connect() as db:
                        if mode in ('destination','source','unused'):
                            schema_id = self.destination.schema_id if mode=='destination' else self.context.schema_id if mode=='source' else next(p['schema_id'] for p in self.pins if p['kind']=='VIEW')
                            db.execute("UPDATE schemas SET purpose='restricted' WHERE id=?", (schema_id,))
                        elif mode=='identity':
                            db.execute("UPDATE logical_backend_identities SET state='REVOKED' WHERE principal_id='alice'")
                        else: db.execute("UPDATE grants SET revision=revision+1 WHERE principal_id='alice'")
                    return result
                with patch.object(inv,'_metadata',side_effect=revoke):
                    self.error('SQL_PERMISSION_DENIED' if mode in ('destination','source','unused') else 'DB_ACCESS_REVOKED')

    def test_post_rest_local_identity_and_no_network_writer_lock(self):
        metadata = inv._metadata
        def replace_identity(*args):
            result = metadata(*args)
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('UPDATE object_bindings SET backend_identity=? WHERE id=?', ('changed',self.pins[0]['ref']['object_id']))
            return result
        with patch.object(inv,'_metadata',side_effect=replace_identity):
            self.error('AI_METADATA_CHANGED')

    def test_pre_rest_grant_revocation(self):
        with store.connect() as db:
            db.execute("UPDATE grants SET revision=revision+1 WHERE principal_id='alice'")
        self.error('DB_ACCESS_REVOKED')
        self.assertEqual(self.f.catalog_calls, [])

    def test_pre_rest_identity_revocation(self):
        with store.connect() as db:
            db.execute("UPDATE logical_backend_identities SET state='REVOKED' WHERE principal_id='alice'")
        self.error('DB_ACCESS_REVOKED')
        self.assertEqual(self.f.catalog_calls, [])

    def test_post_rest_identity_revocation(self):
        metadata = inv._metadata
        def revoke(*args):
            result = metadata(*args)
            with store.connect() as db:
                db.execute("UPDATE logical_backend_identities SET state='REVOKED' WHERE principal_id='alice'")
            return result
        with patch.object(inv,'_metadata',side_effect=revoke):
            self.error('DB_ACCESS_REVOKED')

    def test_ast_ctes_shadowing_joins_sets_correlated_and_literals(self):
        names = (('raw','orders'),('raw','customers'))
        sqls = [
            'WITH orders AS (SELECT * FROM raw.orders) SELECT * FROM orders',
            'WITH source_1 AS (SELECT * FROM orders) SELECT * FROM source_1',
            'WITH X AS (SELECT * FROM ORDERS) SELECT X.ID FROM X',
            'SELECT a.id,b.id FROM orders a JOIN orders b ON a.id=b.id',
            'SELECT id FROM orders UNION ALL SELECT id FROM customers',
            'SELECT id FROM orders INTERSECT SELECT id FROM customers',
            'SELECT id FROM orders EXCEPT SELECT id FROM customers',
            'SELECT orders.id FROM orders WHERE EXISTS (SELECT 1 FROM customers c WHERE c.id=orders.id)',
            'SELECT (SELECT sales.raw.orders.id) FROM sales.raw.orders',
            'SELECT sales.raw.orders.id,raw.orders.amount FROM orders',
            "SELECT '-- string /* retained */', 'e''quoted' FROM ORDERS",
            'SELECT t.id FROM (SELECT * FROM orders) t',
            'WITH x AS (SELECT * FROM orders) SELECT * FROM (WITH x AS (SELECT * FROM customers) SELECT * FROM x) y',
        ]
        for sql in sqls:
            with self.subTest(sql=sql):
                self.seed(sql,names)
                result = self.prepare()
                self.assertEqual(result['original_sql'],sql)
                manual_wap._compile_model(result['sql'], {s['alias']:dm.ResourceRef(**{k:s['ref'][k] for k in
                    ('database_id','schema_id','object_id','incarnation')}) for s in result['sources']})
                if 'source_1 AS' in sql: self.assertEqual(result['sources'][0]['alias'],'source_2')
                if '-- string' in sql: self.assertIn("'-- string /* retained */'",result['sql'])
                if 'JOIN orders' in sql: self.assertEqual(len(result['sources']),1)

    def test_quoted_identifiers_and_unquoted_normalization(self):
        name = 'Odd " Table'
        self.f.objects[('sales','raw',name,'TABLE')] = self.f.metadata(name,'TABLE')
        sql = 'SELECT "Odd "" Table".id FROM "sales"."raw"."Odd "" Table"'
        self.seed(sql,(('raw',name),))
        result = self.prepare()
        self.assertIn('AS "Odd "" Table"',result['sql'])
        self.assertIn('"Odd "" Table".id',result['sql'])
        self.assertEqual(result['sources'][0]['table'],name)

    def test_unsupported_ast_and_qualified_ambiguity_private_logs(self):
        cases = ['SELECT 1', 'SELECT * FROM orders -- SECRET_SENTINEL',
            'SELECT * FROM orders /* SECRET_SENTINEL */',
            'WITH RECURSIVE x AS (SELECT * FROM orders) SELECT * FROM x',
            'SELECT * FROM orders FOR VERSION AS OF 42', 'SELECT * FROM orders TABLESAMPLE BERNOULLI (10)',
            'SELECT mystery(id) FROM orders', 'SELECT current_user FROM orders',
            'SELECT * FROM UNNEST(ARRAY[1])', 'SELECT * FROM orders; SELECT * FROM orders',
            'SELECT raw.orders.id FROM orders a JOIN orders b ON a.id=b.id',
            'SELECT raw.orders.id FROM orders a',
            'WITH x AS (SELECT * FROM orders) SELECT raw.x.id FROM x',
            'SELECT other.raw.orders.id FROM orders',
            'SELECT raw.missing.id FROM orders', 'SELECT * FROM orders WHERE id =']
        stream = io.StringIO(); handler = logging.StreamHandler(stream)
        logging.getLogger('sqlglot').addHandler(handler)
        self.addCleanup(logging.getLogger('sqlglot').removeHandler, handler)
        for sql in cases:
            with self.subTest(sql=sql):
                self.record['sql'] = sql; self.update()
                error = self.error('AI_WAP_UNSUPPORTED')
                self.assertNotIn('SECRET_SENTINEL',str(error))
        self.assertNotIn('SECRET_SENTINEL',stream.getvalue())
        self.assertEqual(self.f.catalog_calls, [])

    def test_malformed_nonquery_and_command_are_silent_readonly_rejections(self):
        cases = [
            "SELECT 'private_malformed_literal_7821' FROM private_malformed_source_7821 WHERE id =",
            "DELETE FROM private_nonquery_source_4936 WHERE id = 'private_nonquery_literal_4936'",
            "EXECUTE private_command_source_6519 USING 'private_command_literal_6519'",
        ]
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logging.getLogger('sqlglot').addHandler(handler)
        self.addCleanup(logging.getLogger('sqlglot').removeHandler, handler)
        for sql in cases:
            with self.subTest(sql=sql):
                self.record['sql'] = sql
                self.update()
                before = dict(ai._row(self.ident))
                with self.readonly(), ExitStack() as stack:
                    mocks = [stack.enter_context(patch(target, side_effect=AssertionError(target)))
                        for target in ('database_inventory.catalog_api', 'db_ai._config', 'db_ai._complete')]
                    error = self.error('AI_WAP_UNSUPPORTED')
                for mock in mocks:
                    mock.assert_not_called()
                self.assertNotIn('private_', str(error))
                self.assertEqual(dict(ai._row(self.ident)), before)
        self.assertEqual(stream.getvalue(), '')
        self.assertEqual(self.f.catalog_calls, [])

    def test_http_both_real_families_closed_framing_and_post_noop(self):
        handlers = [wb.WorkbenchHandler]
        class Capture:
            def __init__(self,address,handler): handlers.append(handler)
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def handle_request(self): pass
        with ExitStack() as stack:
            stack.enter_context(patch('warehouse_resident._profile_active',return_value=True))
            stack.enter_context(patch('logical_creations.enabled',return_value=False))
            stack.enter_context(patch('http.server.ThreadingHTTPServer',Capture))
            stack.enter_context(patch('database_creation_bridge.configure'))
            stack.enter_context(patch.object(wr,'warehouse',return_value='LOCAL'))
            wr.serve_endpoint(once=True)
        class Connection:
            def __init__(self,data): self.incoming,self.outgoing = io.BytesIO(data),io.BytesIO()
            def makefile(self,*args): return self.incoming
            def sendall(self,data): self.outgoing.write(data)
        base = '/api/v1/bears/sql-proposals/'+self.ident+'/wap-draft'
        params = dict(context=ai._json(self.context.public_metadata()),
                      destination_context=ai._json(self.destination.public_metadata()))
        good = base+'?'+urlencode(params)
        cases = [('GET',good,'','',200), ('POST',good,'','',404),
            ('GET',base+'?'+urlencode(dict(context=params['context'])),'','',400),
            ('GET',good+'&sql=SELECT+1','','',400), ('GET',good+'&target=x','','',400),
            ('GET',good+'&context='+params['context'],'','',400),
            ('GET',good,'Content-Length: 1\r\n','x',400),
            ('GET',good,'Transfer-Encoding: chunked\r\n','',400),
            ('GET',good,'Idempotency-Key: key\r\n','',400),
            ('GET',good,'Content-Length: 0\r\nContent-Length: 0\r\n','',400),
            ('GET',good+'#bad','','',400),
            ('GET',base+'?'+urlencode(dict(params,destination_context=ai._json(dict(self.destination.public_metadata(),extra=1)))),'','',400)]
        before = dict(ai._row(self.ident))
        for handler in handlers:
            for method,path,extra,body,status in cases:
                with self.subTest(surface=handler.__name__,status=status,path=path):
                    conn = Connection((method+' '+path+' HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n'
                        'X-Shirokuma-Role: data_engineer\r\n'+extra+'\r\n'+body).encode())
                    with patch.object(runtime_profile,'active',return_value=True), \
                            patch.object(auth,'authenticate_principal',return_value=self.principal), \
                            patch.object(handler,'log_message'), \
                            patch.object(bridge,'prepare',wraps=bridge.prepare) as prepare:
                        handler(conn,('127.0.0.1',1),SimpleNamespace())
                    response = HTTPResponse(Connection(conn.outgoing.getvalue())); response.begin()
                    reply = json.loads(response.read())
                    self.assertEqual(response.status,status,reply)
                    self.assertEqual(prepare.call_count,int(status==200))
                    if status==200:
                        self.assertEqual(set(reply),{'context','draft'})
                        self.assertEqual(reply['draft']['proposal_id'],self.ident)
        self.assertEqual(dict(ai._row(self.ident)),before)


if __name__ == '__main__':
    unittest.main()
