"""SQL drafts: isolated real registry/SQLite, synthetic catalog and LOCAL transport."""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
from email.message import Message
from http.client import HTTPResponse
import importlib
import io
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools/local_experiment'))
with tempfile.TemporaryDirectory() as _bootstrap:
    with patch.dict(os.environ, {'SHIROKUMA_STATE_DIR': _bootstrap}):
        import test_database_ai as a1
        import test_database_ai_plan_views as a3
        import db_ai as ai
        import db_ai_sql_proposal as proposal
        import control_store as store
        import data_management as dm
        import database_inventory as inv
        import ai_routes
        from query_context import QueryError

_COMPLETE = ai._complete


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = a3.ViewPlanTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture.f
        self.principal, self.context = self.f.principal, self.f.context
        # Discover inventory refs before each test's preauthorization call counter.
        self.relations = [self.ref('orders')]
        self.f.catalog_calls.clear()
        self.f.complete.return_value = self.output()
        self.owners = []
        self.addCleanup(lambda: [o.close() for o in self.owners])
        self.tripwires = [self.patch(t, side_effect=AssertionError(t)) for t in (
            'query_service.submit_explain', 'query_service._transport', 'query_service.cancel',
            'logical_registry.identity_credentials', 'trino_auth.prepare_backend_v2',
            'bears_resident.generate_data_sql', 'bears_resident._complete_data_sql')]

    def patch(self, target, **kw):
        p = patch(target, **kw)
        value = p.start()
        self.addCleanup(p.stop)
        return value

    def ref(self, table, schema='raw', context=None, principal=None):
        c = context or self.context
        return inv.resolve_object(principal or self.principal, c.selected_role, c.database_id,
            self.f.schemas[c.database_id][schema].id, dm.quote_identifier(table))['ref']

    @staticmethod
    def output(sql='SELECT * FROM raw.orders', explanation='Draft explanation'):
        return ai._json(dict(sql=sql, explanation=explanation))

    def accept(self, key='proposal-key', **kw):
        with patch.object(proposal.threading, 'Thread') as worker:
            record = proposal.submit(kw.pop('principal', self.principal), kw.pop('context', self.context),
                key=key, request=kw.pop('request', '集計してください'),
                relations=kw.pop('relations', self.relations), language=kw.pop('language', 'en'), **kw)
            if worker.called:
                self.args = worker.call_args.kwargs['args']
                self.owners.append(self.args[-1])
        return record

    def finish(self):
        args = self.args
        proposal._run(*args)
        return self.row(args[2])

    def row(self, ident):
        return json.loads(ai._row(ident)['record'])

    def error(self, code, fn):
        with self.assertRaises(QueryError) as raised:
            fn()
        if code:
            self.assertEqual(raised.exception.code, code)
        return raised.exception

    def test_public_draft_raw_request_and_private_projection(self):
        raw = ' \n  日本語 e\u0301 🔒  \t'
        record = self.accept(request=raw)
        self.assertEqual(record['input'], dict(request=raw, relations=self.relations, language='en'))
        self.assertEqual(set(record['metadata']), {'relations', 'captured_at'})
        self.assertIsNone(record['sql'])
        self.assertIsNone(record['text'])
        for absent in ('plan', 'plan_status', 'source_snapshot', 'query_id', 'backend_query_id'):
            self.assertNotIn(absent, record)
        def complete(config, sql, relations, language, **extras):
            self.assertIsNone(sql)
            self.assertEqual(extras, {'proposal_request': raw})
            self.assertEqual(set(relations[0]), {'schema','table','kind','columns'})
            self.assertEqual(set(relations[0]['columns'][0]), {'name','type'})
            self.assertNotIn('SECRET', ai._json(relations))
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
            return self.output("SELECT * FROM raw.orders WHERE id=73123 -- user draft literal", '完了')
        self.f.complete.side_effect = complete
        done = self.finish()
        self.assertEqual((done['state'], done['text'], done['sql_status']), ('SUCCEEDED', '完了', 'NOT_EXECUTED'))
        self.assertIn('73123', done['sql'])
        self.assertEqual(done['input']['request'], raw)
        self.assertEqual(self.accept(request=raw)['id'], done['id'])
        self.assertEqual(self.f.complete.call_count, 1)
        self.assertTrue(all(not m.called for m in self.tripwires + self.f.forbidden))

    def test_general_quoted_cte_join_set_and_constant_outputs(self):
        self.f.objects[('sales','raw','奇妙 " source','TABLE')] = self.f.metadata('unusual', 'TABLE')
        self.relations += [self.ref('customers'), self.ref('奇妙 " source')]
        sqls = ['SELECT 1 AS n', 'SELECT COUNT(*) FROM orders',
            'WITH x AS (SELECT * FROM raw.orders) SELECT * FROM x',
            'SELECT o.id FROM raw.orders o JOIN raw.customers c ON o.customer_id=c.id',
            'SELECT id FROM orders UNION ALL SELECT id FROM customers',
            'SELECT id FROM orders INTERSECT SELECT id FROM customers',
            'SELECT id FROM orders EXCEPT SELECT id FROM customers',
            'SELECT * FROM "sales"."raw"."奇妙 "" source"',
            "SELECT 'CREATE DROP ; SECRET' AS s FROM orders /* literal */"]
        for i, sql in enumerate(sqls):
            with self.subTest(sql=sql):
                self.accept(key=str(i))
                self.f.complete.return_value = self.output(sql)
                done = self.finish()
                self.assertEqual(done['state'], 'SUCCEEDED', done)
                self.assertEqual(done['sql'], sql)

    def test_invalid_inputs_and_all_six_ref_fields_before_rest_or_config(self):
        ref = self.relations[0]
        cases = [dict(request='  '), dict(request=None), dict(request='界'*1366),
            dict(language='fr'), dict(key=''), dict(relations=[]), dict(relations=[ref]*9),
            dict(relations=[ref,ref]), dict(relations='bad')]
        for field in ref:
            cases += [dict(relations=[{k:v for k,v in ref.items() if k != field}]),
                dict(relations=[dict(ref, **{field: True})])]
        cases += [dict(relations=[dict(ref, extra=1)])]
        with patch.object(ai, '_config', side_effect=AssertionError('config before validation')):
            for case in cases:
                with self.subTest(case=case):
                    self.error('AI_INPUT_INVALID', lambda: self.accept(**case))
            for field in ref:
                altered = dict(ref, **{field: ref[field]+1 if type(ref[field]) is int else 'other'})
                self.error(None, lambda: self.accept(relations=[altered]))
        self.assertEqual(self.f.catalog_calls, [])
        self.assertFalse(self.f.complete.called)

    def test_all_refs_prevalidated_including_later_crossdb_and_schema_acl(self):
        other = self.f.ctx(database=self.f.databases[1])
        foreign = self.ref('orders', context=other)
        marts = self.f.ctx(role='bi_viewer', schema='marts')
        good = self.ref('orders', schema='marts', context=marts)
        raw = self.relations[0]
        self.f.catalog_calls.clear()
        with patch.object(ai, '_config', side_effect=AssertionError('config')):
            self.error('AI_METADATA_CHANGED', lambda: self.accept(relations=[raw, foreign]))
            self.error('SQL_PERMISSION_DENIED', lambda: self.accept(context=marts, relations=[good, raw]))
        self.assertEqual(self.f.catalog_calls, [])

    def test_unpaired_surrogates_rejected_before_lookup_capture_or_provider(self):
        for surrogate in ('\ud800', '\udfff'):
            cases = [dict(request='request ' + surrogate)] + [
                dict(relations=[dict(self.relations[0], **{field: 'ref' + surrogate})])
                for field in ('database_id', 'schema_id', 'object_id', 'generation_id')]
            for case in cases:
                with self.subTest(case=ascii(case)), \
                        patch.object(store, 'connect', side_effect=AssertionError('lookup')), \
                        patch.object(proposal, '_capture', side_effect=AssertionError('capture')), \
                        patch.object(ai, '_config', side_effect=AssertionError('config')):
                    error = self.error('AI_INPUT_INVALID', lambda: self.accept(**case))
                    self.assertEqual(error.status, 400)
        self.assertEqual(self.f.catalog_calls, [])
        self.f.complete.assert_not_called()
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ai_explanations').fetchone()[0], 0)

    def test_full_nine_context_fields_before_rest(self):
        for field, value in self.context.public_metadata().items():
            bad = replace(self.context, **{field: value+1 if type(value) is int else 'wrong'})
            with self.subTest(field=field):
                self.error(None, lambda: self.accept(context=bad))
        self.assertEqual(self.f.catalog_calls, [])
        self.assertFalse(self.f.complete.called)

    def test_two_databases_three_roles_and_platform_admin_nullable_epoch(self):
        for database in self.f.databases:
            for role in ('bi_viewer','data_engineer','admin'):
                c = self.f.ctx(role=role, schema='marts' if role=='bi_viewer' else 'raw', database=database)
                refs = [self.ref('orders', schema=c.sql_schema, context=c)]
                self.f.complete.return_value = self.output('SELECT * FROM '+c.sql_schema+'.orders')
                self.accept(key=database.id+role, context=c, relations=refs)
                done = self.finish()
                self.assertEqual(done['state'], 'SUCCEEDED', done)
        c = self.f.ctx(role='admin', principal=self.f.owner)
        self.assertIsNone(c.grant_revision)
        self.accept(key='owner', context=c, principal=self.f.owner)
        self.assertEqual(self.finish()['state'], 'SUCCEEDED')

    def test_identity_required_including_owner_and_no_actor_override(self):
        for principal, role in ((self.principal,'data_engineer'), (self.f.owner,'admin')):
            c = self.f.ctx(principal=principal, role=role)
            with store.connect() as db:
                db.execute("UPDATE logical_backend_identities SET state='REVOKED' WHERE principal_id=?", (principal.principal_id,))
            self.error('DB_ACCESS_REVOKED', lambda: self.accept(context=c, principal=principal))
        self.assertEqual(self.f.catalog_calls, [])

    def test_metadata_only_view_no_definition_or_dependencies(self):
        view = self.fixture.view('order_view', 'UNSUPPORTED PRIVATE SECRET VIEW DEFINITION')
        view['versions'][0].pop('representations')
        self.relations = [self.ref('order_view')]
        self.f.catalog_calls.clear()
        record = self.accept()
        self.f.complete.return_value = self.output('SELECT * FROM raw.order_view')
        done = self.finish()
        self.assertEqual(done['state'], 'SUCCEEDED', done)
        self.assertEqual(record['metadata']['relations'][0]['kind'], 'VIEW')
        self.assertNotIn('dependencies', ai._json(record['metadata']))
        self.assertNotIn('PRIVATE', ai._json(record))
        self.assertTrue(all('orders' not in path for _, path in self.f.catalog_calls))

    def test_metadata_boundary_64k_relations_envelope_excludes_time(self):
        for size in (65536, 65537):
            metadata = self.f.objects[('sales','raw','orders','TABLE')]
            columns = metadata['schemas'][0]['fields']
            columns[:] = [dict(name='', type='long')]
            envelope = [dict(schema='raw',table='orders',kind='TABLE',columns=[dict(name='',type='long')])]
            columns[0]['name'] = 'x' * (size-len(ai._json(envelope).encode()))
            if size==65536:
                record = self.accept(key='size')
                self.assertEqual(len(ai._json(record['metadata']['relations']).encode()), size)
                self.assertGreater(len(ai._json(record['metadata']).encode()), size)
            else:
                self.error('AI_INPUT_INVALID', lambda: self.accept(key='overflow'))

    def test_strict_json_rejects_duplicate_extra_fences_prose_types_and_sizes(self):
        candidates = ['{"sql":"SELECT 1","sql":"SELECT 2","explanation":"x"}',
            '{"sql":"SELECT 1","explanation":"x","other":0}',
            '```json\n'+self.output()+'\n```', 'Here: '+self.output(), self.output()+' prose',
            self.output()[:-1], 'null', '[]', '{"sql":1,"explanation":"x"}',
            '{"sql":"SELECT 1","explanation":null}', self.output('  '),
            self.output(explanation='  '), self.output('SELECT '+"'"+'界'*6000+"'"),
            self.output(explanation='界'*1366)]
        for i, candidate in enumerate(candidates):
            with self.subTest(i=i):
                self.accept(key=str(i))
                self.f.complete.return_value = candidate
                record = self.finish()
                self.assertEqual((record['state'],record['error_code'],record['sql'],record['text']),
                    ('FAILED','AI_OUTPUT_INVALID',None,None))
        self.assertEqual(self.f.complete.call_count, len(candidates))

    def test_output_sql_rejections_no_model_named_fetch_and_no_private_log(self):
        candidates = ['DROP TABLE raw.orders', 'INSERT INTO raw.orders SELECT 1',
            'DELETE FROM raw.orders', 'CREATE TABLE raw.new AS SELECT 1', 'EXPLAIN SELECT 1',
            'SELECT 1; SELECT 2', 'SELECT * FROM raw.customers', 'SELECT * FROM finance.raw.orders',
            'SELECT * FROM system.runtime.queries', 'SELECT * FROM information_schema.tables',
            'SELECT * FROM raw.orders FOR VERSION AS OF 42', 'SELECT * FROM raw.orders FOR TIMESTAMP AS OF CURRENT_TIMESTAMP',
            'SELECT * FROM UNNEST(ARRAY[1,2])', 'SELECT * FROM TABLE(system.query(query => \'SELECT 1\'))',
            'SELECT mystery(id) FROM orders', 'SELECT current_user', 'SELECT * FROM "orders$files"',
            'SELECT * FROM orders WHERE id =', 'SHOW SECRET_PRIVATE_SENTINEL',
            "SELECT JSON_EXTRACT(id, '$.SECRET_PRIVATE_SENTINEL?(@.PRIVATE > 37)') FROM customers",
            'SELECT * FROM orders WHERE id IN (DELETE FROM orders)']
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logging.getLogger('sqlglot').addHandler(handler)
        self.addCleanup(logging.getLogger('sqlglot').removeHandler, handler)
        for i, candidate in enumerate(candidates):
            with self.subTest(sql=candidate):
                self.accept(key=str(i))
                self.f.catalog_calls.clear()
                self.f.complete.return_value = self.output(candidate+' -- SECRET_PRIVATE_SENTINEL')
                record = self.finish()
                self.assertEqual(record['error_code'], 'AI_OUTPUT_INVALID', record)
                self.assertIsNone(record['sql'])
                self.assertIsNone(record['text'])
                self.assertNotIn('SECRET_PRIVATE_SENTINEL', ai._row(record['id'])['record'])
                self.assertTrue(all('customers' not in path and 'system' not in path for _,path in self.f.catalog_calls))
        self.assertNotIn('SECRET_PRIVATE_SENTINEL', stream.getvalue())
        self.assertEqual(self.f.complete.call_count, len(candidates))

    def test_preprovider_drift_blocks_inference(self):
        for kind in ('columns', 'replacement', 'view'):
            with self.subTest(kind=kind):
                if kind == 'view':
                    self.relations = [self.ref('order_view')]
                self.accept(key=kind)
                key = ('sales','raw','order_view' if kind=='view' else 'orders','VIEW' if kind=='view' else 'TABLE')
                metadata = self.f.objects[key]
                if kind=='columns':
                    metadata['schemas'][0]['fields'].append(dict(name='new',type='long'))
                elif kind=='replacement':
                    metadata['table-uuid'] = 'replacement-id'
                else:
                    metadata['current-version-id'] = 2
                    metadata['versions'].append(dict(metadata['versions'][0], **{'version-id':2}))
                done = self.finish()
                self.assertEqual(done['state'], 'FAILED')
        self.assertFalse(self.f.complete.called)

    def test_postprovider_drift_discards_but_append_stable(self):
        metadata = self.f.objects[('sales','raw','orders','TABLE')]
        self.accept(key='append')
        def append(*a, **kw):
            metadata['current-snapshot-id'] += 1
            return self.output()
        self.f.complete.side_effect = append
        self.assertEqual(self.finish()['state'], 'SUCCEEDED')
        for kind in ('columns','replacement'):
            self.relations = [self.ref('orders')]
            self.accept(key=kind)
            def drift(*a, **kw):
                if kind=='columns':
                    metadata['schemas'][0]['fields'].append(dict(name='new',type='long'))
                else:
                    metadata['table-uuid'] = 'post-replaced'
                return self.output()
            self.f.complete.side_effect = drift
            done = self.finish()
            self.assertEqual(done['state'], 'FAILED')
            self.assertIsNone(done['sql'])
            self.assertIsNone(done['text'])

    def test_registry_replacement_and_schema_revoke_admission_no_rest(self):
        with store.connect() as db:
            db.execute('UPDATE object_bindings SET current=0 WHERE id=?',(self.relations[0]['object_id'],))
        self.error('AI_METADATA_CHANGED', self.accept)
        self.assertEqual(self.f.catalog_calls, [])

    def test_final_writer_revocation_discards_output(self):
        self.accept()
        refresh = proposal._refresh
        count = 0
        def revoke(*a):
            nonlocal count
            refresh(*a)
            count += 1
            if count==2:
                with store.connect() as db:
                    db.execute("UPDATE logical_backend_identities SET state='REVOKED' WHERE principal_id='alice'")
        with patch.object(proposal, '_refresh', side_effect=revoke):
            done = self.finish()
        self.assertEqual((done['state'],done['error_code'],done['sql'],done['text']),
            ('FAILED','DB_ACCESS_REVOKED',None,None))
        self.assertEqual(self.f.complete.call_count, 1)

    def test_preprovider_authority_revocation_and_history_current_acl(self):
        record = self.accept()
        with store.connect() as db:
            db.execute("UPDATE schemas SET purpose='restricted' WHERE id=?", (self.relations[0]['schema_id'],))
        self.assertEqual(self.finish()['state'], 'FAILED')
        self.error('SQL_PERMISSION_DENIED', lambda: proposal.get(self.principal,self.context,record['id']))
        self.assertFalse(self.f.complete.called)

    def test_replay_history_reload_cancel_no_catalog_even_after_source_removed(self):
        record = self.accept()
        done = self.finish()
        with store.connect() as db:
            db.execute('DELETE FROM object_bindings')
        with patch.object(ai,'_capture',side_effect=AssertionError('recapture')), \
                patch.object(ai,'_config',side_effect=AssertionError('config')):
            importlib.reload(proposal)
            self.assertEqual(self.accept()['id'], record['id'])
            self.assertEqual(proposal.get(self.principal,self.context,record['id']), done)
            self.assertEqual(proposal.history(self.principal,self.context), [done])
            self.assertEqual(proposal.cancel(self.principal,self.context,record['id'])['state'], 'SUCCEEDED')
        self.assertEqual(self.f.complete.call_count, 1)

    def test_global_key_conflict_and_body_immutable_and_actor_isolation(self):
        record = self.accept()
        self.error('IDEMPOTENCY_CONFLICT', lambda: self.accept(request='different'))
        self.error('IDEMPOTENCY_CONFLICT', lambda: self.accept(language='ja'))
        self.error('IDEMPOTENCY_CONFLICT', lambda: ai.submit(self.principal,self.context,key='proposal-key',sql='SELECT 1',language='en'))
        self.error('AI_EXPLANATION_NOT_FOUND', lambda: proposal.get(self.f.bob,self.context,record['id']))
        self.error('AI_EXPLANATION_NOT_FOUND', lambda: proposal.cancel(self.f.bob,self.context,record['id']))
        with store.connect() as db:
            db.execute("UPDATE ai_explanations SET record=json_set(record,'$.kind','sql_explanation') WHERE id=?", (record['id'],))
        self.error('IDEMPOTENCY_CONFLICT', self.accept)

    def test_timeout_unknown_owner_loss_and_no_automatic_retry(self):
        for code in ('AI_PROVIDER_TIMEOUT','AI_PROVIDER_OUTCOME_UNKNOWN'):
            record = self.accept(key=code)
            self.f.complete.side_effect = QueryError(code,504)
            self.assertEqual(self.finish()['state'], 'UNKNOWN')
            calls = self.f.complete.call_count
            self.accept(key=code)
            proposal.history(self.principal,self.context)
            self.assertEqual(self.f.complete.call_count,calls)
        record = self.accept(key='lost')
        self.owners[-1].close()
        done = proposal.get(self.principal,self.context,record['id'])
        self.assertEqual((done['state'],done['error_code']),('UNKNOWN','AI_OWNER_LOST'))
        self.finish()
        self.assertEqual(self.f.complete.call_count,2)

    def test_cancel_queued_and_late_output_discarded(self):
        record = self.accept()
        proposal.cancel(self.principal,self.context,record['id'])
        self.assertEqual(self.finish()['state'],'CANCELED')
        self.assertFalse(self.f.complete.called)
        record = self.accept(key='late')
        def complete(*a,**kw):
            proposal.cancel(self.principal,self.context,record['id'])
            return self.output('SELECT 73123')
        self.f.complete.side_effect = complete
        done = self.finish()
        self.assertEqual((done['state'],done['sql'],done['text']),('CANCELED',None,None))

    def test_concurrent_identical_submissions_one_record_one_provider(self):
        barrier = threading.Barrier(2)
        capture = proposal._capture
        def together(*a):
            result = capture(*a)
            barrier.wait(timeout=5)
            return result
        records, errors, workers = [], [], []
        real_thread = threading.Thread
        class Queued:
            def __init__(self, **kw):
                workers.append(kw['args'])
            def start(self):
                pass
        def submit():
            try:
                records.append(proposal.submit(self.principal,self.context,key='race',request='request',relations=self.relations,language='en'))
            except Exception as e:
                errors.append(e)
        with patch.object(proposal,'_capture',side_effect=together), patch.object(proposal.threading,'Thread',Queued):
            threads = [real_thread(target=submit) for _ in range(2)]
            for t in threads: t.start()
            for t in threads: t.join(10)
        self.owners.extend(args[-1] for args in workers)
        self.assertEqual(errors,[])
        self.assertEqual(len(records),2)
        self.assertEqual(records[0]['id'],records[1]['id'])
        self.assertEqual(len(workers),1)
        proposal._run(*workers[0])
        self.assertEqual(self.f.complete.call_count,1)


    def test_exact_request_sql_explanation_utf8_limits_and_eight_selected_refs(self):
        self.relations = [self.ref(table, schema=schema) for schema in ('raw','sandbox','staging','marts')
            for table in ('orders','customers')]
        raw = '界'*1365 + ' '
        self.assertEqual(len(raw.encode()),4096)
        self.accept(request=raw)
        sql = "SELECT '" + 'x'*(16384-len("SELECT ''")) + "'"
        self.f.complete.return_value = self.output(sql, '界'*1365+' ')
        done = self.finish()
        self.assertEqual(done['state'],'SUCCEEDED',done)
        self.assertEqual(len(done['sql'].encode()),16384)
        self.assertEqual(len(done['text'].encode()),4096)
        self.assertEqual(done['input']['request'],raw)
        self.assertEqual(len(done['metadata']['relations']),8)

    def test_current_grant_epoch_must_not_be_restamped(self):
        record = self.accept()
        with store.connect() as db:
            db.execute("UPDATE grants SET revision=revision+1 WHERE principal_id='alice'")
        with patch.object(ai,'_capture',side_effect=AssertionError('recapture')):
            self.error(None,lambda:proposal.get(self.principal,self.context,record['id']))
            self.error(None,lambda:proposal.cancel(self.principal,self.context,record['id']))
            self.error(None,self.accept)
            self.error(None,lambda:proposal.history(self.principal,self.context))
        self.assertFalse(self.f.complete.called)

    def test_all_nine_stored_context_fields_and_identity_guard_reads(self):
        record = self.accept()
        self.finish()
        for field,value in self.context.public_metadata().items():
            context = replace(self.context,**{field: value+1 if type(value) is int else 'other'})
            with self.subTest(field=field):
                self.error('AI_EXPLANATION_NOT_FOUND',lambda:proposal.get(self.principal,context,record['id']))
                self.error('AI_EXPLANATION_NOT_FOUND',lambda:proposal.cancel(self.principal,context,record['id']))
        with store.connect() as db:
            db.execute("UPDATE logical_backend_identities SET state='REVOKED' WHERE principal_id='alice'")
        with patch.object(ai,'_capture',side_effect=AssertionError('recapture')):
            self.error('DB_ACCESS_REVOKED',lambda:proposal.get(self.principal,self.context,record['id']))
            self.error('DB_ACCESS_REVOKED',lambda:proposal.cancel(self.principal,self.context,record['id']))
            self.error('DB_ACCESS_REVOKED',self.accept)
            self.error('DB_ACCESS_REVOKED',lambda:proposal.history(self.principal,self.context))

    def test_writer_admission_rechecks_after_optimistic_capture(self):
        config = ai._config
        def revoke():
            result = config()
            with store.connect() as db:
                db.execute("UPDATE logical_backend_identities SET state='REVOKED' WHERE principal_id='alice'")
            return result
        with patch.object(ai,'_config',side_effect=revoke):
            self.error('DB_ACCESS_REVOKED',self.accept)
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM ai_explanations').fetchone()[0],0)
        self.assertFalse(self.f.complete.called)

    def test_postprovider_view_version_drift_discards_without_dependency_traversal(self):
        metadata = self.fixture.view('order_view','UNSUPPORTED PRIVATE VIEW SQL')
        self.relations = [self.ref('order_view')]
        self.accept()
        def change(*a,**kw):
            metadata['current-version-id']=2
            metadata['versions'].append(dict(metadata['versions'][0],**{'version-id':2}))
            return self.output('SELECT * FROM order_view')
        self.f.complete.side_effect=change
        done=self.finish()
        self.assertEqual((done['state'],done['error_code'],done['sql'],done['text']),
            ('FAILED','AI_METADATA_CHANGED',None,None))
        self.assertEqual(self.f.complete.call_count,1)

    def test_catalog_calls_and_provider_outside_sqlite_writer(self):
        catalog=self.fixture.catalog
        from contextlib import contextmanager
        @contextmanager
        def unlocked(*a):
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
            with catalog(*a) as api:
                yield api
        with patch.object(inv,'catalog_api',unlocked):
            self.accept()
            done=self.finish()
        self.assertEqual(done['state'],'SUCCEEDED')



class ProposalWireTests(unittest.TestCase):
    setUp = ProposalTests.setUp
    patch = ProposalTests.patch
    ref = ProposalTests.ref
    output = staticmethod(ProposalTests.output)
    error = ProposalTests.error
    def test_provider_body_exact_request_metadata_and_prior_messages_unchanged(self):
        class Response:
            status = 200
            def __init__(self, payload): self.payload = payload
            def read1(self, count):
                data,self.payload = self.payload[:count],self.payload[count:]
                return data
        bodies=[]
        def response(content, finish='stop', **extra):
            return json.dumps(dict(choices=[dict(finish_reason=finish,message=dict(content=content,**extra))])).encode()
        payload = response(self.output())
        class Connection:
            sock=None
            def __init__(self,*a,**kw): pass
            def connect(self): pass
            def request(self,*a,**kw): bodies.append(json.loads(kw['body']))
            def getresponse(self): return Response(payload)
            def close(self): pass
        config=dict(host='127.0.0.1',port=1,timeout=2,max_tokens=256,model='local')
        raw=' \n日本語 e\u0301 🔒 \t'
        relations=[dict(schema='raw',table='orders',kind='TABLE',columns=[dict(name='id',type='long')])]
        with patch.object(ai.http.client,'HTTPConnection',Connection):
            for language in ('ja', 'en'):
                _COMPLETE(config,None,relations,language,proposal_request=raw)
                body=bodies[-1]
                self.assertEqual(json.loads(body['messages'][1]['content']),dict(request=raw,relations=relations))
                self.assertEqual(set(body),{'model','max_tokens','temperature','enable_thinking','messages'})
                prompt=body['messages'][0]['content']
                for required in ('one read-only Trino SELECT', 'brief explanation in ' + language,
                        'schema-qualified relation names', 'quote identifiers as needed',
                        'CTEs, joins and set queries', '{"sql":"...","explanation":"..."}',
                        'untrusted data', 'No fences, surrounding prose', 'actions, execution',
                        'column-correctness or business-correctness guarantees'):
                    self.assertIn(required,prompt)
            for sql,plan in [('SELECT ?',None),('SELECT ?',{'projection_version':1,'fragments':[]})]:
                _COMPLETE(config,sql,relations,'en',plan=plan)
                old=deepcopy(bodies[-1])
                _COMPLETE(config,sql,relations,'en',plan=plan,proposal_request=None)
                self.assertEqual(bodies[-1],old)
                self.assertEqual(set(json.loads(old['messages'][1]['content'])),{'sql','relations'} | ({'plan'} if plan else set()))
            for bad in (response(self.output(),finish='length'),response(self.output(),tool_calls=[{'name':'evil'}]),
                    response(self.output(),function_call={'name':'evil'}),response(3),response('x'*32769)):
                payload=bad
                self.error('AI_OUTPUT_INVALID',lambda:_COMPLETE(config,None,relations,'en',proposal_request=raw))

    def test_collection_detail_cancel_routes_and_read_framing(self):
        base='/api/v1/bears/sql-proposals'
        ident='00000000-0000-0000-0000-000000000001'
        def handler(path,body=None):
            headers=Message()
            if body is not None:
                headers['Content-Type']='application/json'
            return SimpleNamespace(path=path,headers=headers,database_body=lambda:body,
                respond=lambda result,status: replies.append((result,status)))
        replies=[]
        params={'context':[json.dumps(self.context.public_metadata())]}
        with patch.object(proposal,'history',return_value=[]) as history, \
                patch.object(proposal,'get',return_value={'id':ident}) as get, \
                patch.object(proposal,'cancel',return_value={'id':ident,'state':'CANCELED'}) as cancel:
            h=handler(base)
            ai_routes.dispatch(h,'GET',base,params,self.principal,self.context.selected_role)
            self.assertEqual(replies[-1][0]['explanations'],[])
            history.assert_called_once_with(self.principal,self.context)
            h=handler(base+'/'+ident)
            ai_routes.dispatch(h,'GET',h.path,params,self.principal,self.context.selected_role)
            get.assert_called_once_with(self.principal,self.context,ident)
            h=handler(base+'/'+ident+'/cancel',dict(context=self.context.public_metadata()))
            ai_routes.dispatch(h,'POST',h.path,{},self.principal,self.context.selected_role)
            cancel.assert_called_once_with(self.principal,self.context,ident)
            self.assertEqual(replies[-1][1],200)
            for headers in [('Content-Length','1'),('Transfer-Encoding','chunked')]:
                h=handler(base)
                h.headers[headers[0]]=headers[1]
                with self.assertRaises(ValueError):
                    ai_routes.dispatch(h,'GET',base,params,self.principal,self.context.selected_role)
            h=handler(base+'/'+ident+'/cancel',dict(context=self.context.public_metadata(),request='bad'))
            with self.assertRaises(ValueError):
                ai_routes.dispatch(h,'POST',h.path,{},self.principal,self.context.selected_role)

    def test_both_http_families_strict_framing_and_exact_body(self):
        import workbench_server as wb
        import warehouse_resident as wr
        import runtime_profile
        import request_auth as auth
        handlers=[wb.WorkbenchHandler]
        class Capture:
            def __init__(self,address,handler): handlers.append(handler)
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def handle_request(self): pass
        with ExitStack() as stack:
            stack.enter_context(patch('warehouse_resident._profile_active',return_value=True))
            stack.enter_context(patch('logical_creations.enabled',return_value=False))
            stack.enter_context(patch('http.server.ThreadingHTTPServer',Capture))
            stack.enter_context(patch('database_creation_bridge.configure'))
            stack.enter_context(patch.object(wr,'warehouse',return_value='LOCAL'))
            wr.serve_endpoint(once=True)
        class Connection:
            def __init__(self,data): self.incoming,self.outgoing=io.BytesIO(data),io.BytesIO()
            def makefile(self,*a): return self.incoming
            def sendall(self,data): self.outgoing.write(data)
        base='/api/v1/bears/sql-proposals'
        raw=' \n 日本語 e\u0301 🔒 \t'
        body=json.dumps(dict(context=self.context.public_metadata(),request=raw,relations=self.relations,language='en'))
        cases=[(base,body,'Idempotency-Key: k\r\n',202),
            (base,body,'',400),(base,body,'Idempotency-Key: k\r\nIdempotency-Key: k\r\n',400),
            (base,body,'Idempotency-Key: k\r\nOrigin: http://evil.invalid\r\n',403),
            (base,body,'Idempotency-Key: k\r\nTransfer-Encoding: chunked\r\n',400),
            (base,body[:-1]+',"sql":"SELECT 1"}','Idempotency-Key: k\r\n',400),
            (base,body[:-1]+',"language":"ja"}','Idempotency-Key: k\r\n',400),
            (base+'?extra=1',body,'Idempotency-Key: k\r\n',400)]
        for handler in handlers:
            for path,data,extra,status in cases:
                with self.subTest(surface=handler.__name__,status=status):
                    wire=('POST '+path+' HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n'
                        'X-Shirokuma-Role: data_engineer\r\nContent-Type: application/json\r\nContent-Length: '
                        +str(len(data.encode()))+'\r\n'+extra+'\r\n'+data).encode()
                    conn=Connection(wire)
                    with patch.object(runtime_profile,'active',return_value=True), \
                            patch.object(auth,'authenticate_principal',return_value=self.principal), \
                            patch.object(proposal,'submit',return_value={'id':'new'}) as submit, \
                            patch.object(handler,'log_message'):
                        handler(conn,('127.0.0.1',1),SimpleNamespace())
                    reply=HTTPResponse(Connection(conn.outgoing.getvalue()));reply.begin()
                    self.assertEqual(reply.status,status,json.loads(reply.read()))
                    self.assertEqual(submit.call_count,int(status==202))
                    if submit.called:
                        self.assertEqual(submit.call_args.kwargs['request'],raw)
                        self.assertEqual(submit.call_args.kwargs['relations'],self.relations)

        invalid=[]
        for surrogate in ('\ud800', '\udfff'):
            invalid += [dict(request='request ' + surrogate)] + [
                dict(relations=[dict(self.relations[0], **{field: 'ref' + surrogate})])
                for field in ('database_id', 'schema_id', 'object_id', 'generation_id')]
        for handler in handlers:
            for case in invalid:
                with self.subTest(surface=handler.__name__,case=ascii(case)):
                    data=json.dumps(dict(context=self.context.public_metadata(),request=raw,
                        relations=self.relations,language='en') | case)
                    self.assertTrue('\\ud800' in data or '\\udfff' in data)
                    wire=('POST '+base+' HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n'
                        'X-Shirokuma-Role: data_engineer\r\nContent-Type: application/json\r\n'
                        'Idempotency-Key: invalid-utf8\r\nContent-Length: '+str(len(data.encode()))+'\r\n\r\n'+data).encode()
                    conn=Connection(wire)
                    with patch.object(runtime_profile,'active',return_value=True), \
                            patch.object(auth,'authenticate_principal',return_value=self.principal), \
                            patch.object(proposal,'_capture',side_effect=AssertionError('capture')), \
                            patch.object(ai,'_config',side_effect=AssertionError('config')), \
                            patch.object(handler,'log_message'):
                        handler(conn,('127.0.0.1',1),SimpleNamespace())
                    reply=HTTPResponse(Connection(conn.outgoing.getvalue()));reply.begin()
                    result=json.loads(reply.read())
                    self.assertEqual(reply.status,400,result)
                    self.assertEqual(result['code'],'AI_INPUT_INVALID')
        self.assertEqual(self.f.catalog_calls,[])
        self.f.complete.assert_not_called()
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ai_explanations').fetchone()[0],0)


if __name__ == '__main__':
    unittest.main()
