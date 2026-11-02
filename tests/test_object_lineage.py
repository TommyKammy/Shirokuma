"""Saved-ledger lineage and strict read-only metadata authorization."""
from contextlib import ExitStack
from dataclasses import asdict, replace
import json
import unittest
from unittest.mock import patch
import uuid

import test_manual_wap as fixtures
import control_store
import data_management as registry
import database_inventory as inventory
import dataops_service as ds
import db_dbt
from db_marts import _closed_child
import manual_wap as wap
import object_lineage as lineage
import query_service
from query_context import QueryError


class LineageTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ManualWapTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.principal, self.context = self.f.principal, self.f.context

    def read(self, ref, principal=None, context=None):
        context = context or replace(self.context, schema_id=ref['schema_id'],
            sql_schema=self.schema_name(ref['schema_id']))
        return lineage.get_lineage(principal or self.principal, context, ref['object_id'])

    def schema_name(self, ident):
        with control_store.connect() as db:
            return registry.get_schema(db, ident).sql_name

    def test_saved_build_publication_historical_heads_and_no_write_or_sql(self):
        built, audit = self.f.audited()
        published = self.f.publish(built, audit)
        source = next(iter(built['input_refs'].values()))['ref']
        with ExitStack() as stack:
            for owner, name in ((query_service, '_transport'), (ds.Execution, 'inspect'),
                    (ds.Execution, '_observe'), (registry, 'observe_object'), (db_dbt, '_observe')):
                stack.enter_context(patch.object(owner, name, side_effect=AssertionError(name)))
            result = self.read(source)
            self.assertEqual([e['category'] for e in result['confirmed']], ['wap_build'])
            self.assertEqual(result['confirmed'][0]['source']['snapshot_id'], built['input_refs']['source']['snapshot'])
            pub = self.read(published['target']['ref'])
            self.assertIn('wap_publication', [e['category'] for e in pub['confirmed']])
            self.f.snapshot(self.f.metadata(registry.ResourceRef(**source)), [(9, 'later')])
            result = self.read(source)
            self.assertEqual(result['confirmed'][0]['temporal'], 'historical')
        public = json.dumps(result)
        for private in ('generated_sql', 'actor', 'backend_query_id', 'credential', 'fingerprint'):
            self.assertNotIn(private, public)

    def test_managed_parent_before_any_operation_links_and_saved_role(self):
        built = self.f.built()
        source = built['input_refs']['source']['ref']
        saved = dict(id=str(uuid.uuid4()), kind='run', actor='another-engineer',
            context=asdict(self.context), models=[dict(wap_model_id=built['model_id'])])
        with control_store.connect() as db:
            db.execute('INSERT INTO dbt_runs VALUES (?,?,?,NULL)',
                (saved['id'], self.context.database_id, json.dumps(saved)))
        self.assertEqual(self.read(source)['confirmed'], [])
        saved['actor'] = self.principal.principal_id
        saved['context']['selected_role'] = 'admin'
        with control_store.connect() as db:
            db.execute('UPDATE dbt_runs SET record=? WHERE id=?', (json.dumps(saved), saved['id']))
        self.assertEqual(self.read(source)['confirmed'], [])
        saved['context']['selected_role'] = 'data_engineer'
        with control_store.connect() as db:
            db.execute('UPDATE dbt_runs SET record=? WHERE id=?', (json.dumps(saved), saved['id']))
        self.assertEqual(len(self.read(source)['confirmed']), 1)
        saved['id'] = str(uuid.uuid4())
        with control_store.connect() as db:
            db.execute('INSERT INTO dbt_runs VALUES (?,?,?,NULL)',
                (saved['id'], self.context.database_id, json.dumps(saved)))
        self.assertEqual(self.read(source)['confirmed'], [])

    def test_viewer_rejected_before_catalog_and_stale_uuid_never_repairs(self):
        source = self.f.existing(schema='marts', name='任意"' + 'a'*130)
        viewer = self.f.b.context(schema='marts', role='bi_viewer')
        with patch.object(inventory, 'catalog_api', side_effect=AssertionError('catalog')):
            with self.assertRaises(QueryError) as caught:
                self.read(asdict(source), context=viewer)
            self.assertEqual(caught.exception.status, 403)
        from contextlib import contextmanager
        from urllib.parse import unquote
        @contextmanager
        def quoted_catalog(database, binding):
            def load(path):
                schema, kind, name = [unquote(p) for p in path.split('/')[2:]]
                return {'metadata': self.f.b.objects[(database.sql_catalog, schema, name, kind)]}
            yield load
        patcher = patch.object(inventory, 'catalog_api', side_effect=quoted_catalog)
        patcher.start(); self.addCleanup(patcher.stop)
        self.read(asdict(source))
        self.f.metadata(source)['table-uuid'] = str(uuid.uuid4())
        with patch.object(registry, 'observe_object', side_effect=AssertionError('repair')):
            with self.assertRaises(QueryError) as caught:
                self.read(asdict(source))
            self.assertEqual(caught.exception.code, 'STALE_RESOURCE')

    def test_pure_closure_requires_parent_and_operation_terminal(self):
        built = self.f.built()
        with control_store.connect() as db:
            admission = db.execute("SELECT id FROM warehouse_admissions WHERE kind='dataops' AND subject_id=?",
                (built['operation_id'],)).fetchone()[0]
            for parent_state, op_state in [('ACTIVE', 'FINISHED'), ('FINISHED', 'ACTIVE'), ('UNKNOWN', 'UNKNOWN')]:
                db.execute('UPDATE warehouse_admissions SET state=? WHERE id=?', (parent_state, admission))
                db.execute('UPDATE dataops_operations SET state=? WHERE id=?', (op_state, built['operation_id']))
                changes = db.total_changes
                with patch('db_marts.admission.finish', side_effect=AssertionError('finish')):
                    self.assertFalse(_closed_child(db, built['operation_id'], settle=False))
                self.assertEqual(db.total_changes, changes)
            db.execute("UPDATE warehouse_admissions SET state='FINISHED' WHERE id=?", (admission,))
            db.execute("UPDATE dataops_operations SET state='FINISHED' WHERE id=?", (built['operation_id'],))
            changes = db.total_changes
            self.assertTrue(_closed_child(db, built['operation_id'], settle=False))
            self.assertEqual(db.total_changes, changes)

    def test_cross_database_endpoint_omits_whole_fact(self):
        built = self.f.built()
        source = built['input_refs']['source']['ref']
        with control_store.connect() as db:
            record = json.loads(db.execute('SELECT record FROM wap_builds WHERE id=?', (built['id'],)).fetchone()[0])
            record['stage']['ref']['database_id'] = 'hidden-database'
            db.execute('UPDATE wap_builds SET record=? WHERE id=?', (json.dumps(record), built['id']))
        result = self.read(source)
        self.assertEqual(result['confirmed'], [])
        self.assertNotIn('hidden-database', json.dumps(result))

    def test_all_same_database_parent_mapping_beyond_projection_and_malformed_failclosed(self):
        built = self.f.built()
        source = built['input_refs']['source']['ref']
        parent = dict(id=str(uuid.uuid4()),kind='run',actor='hidden',context=asdict(self.context),
            models=[dict(wap_model_id=built['model_id'])])
        with control_store.connect() as db:
            db.execute('INSERT INTO dbt_runs VALUES (?,?,?,NULL)', (parent['id'],self.context.database_id,json.dumps(parent)))
            for i in range(lineage.LIMIT+1):
                record = dict(id=str(uuid.uuid4()),kind='plan',context=asdict(self.context),actor='hidden')
                db.execute('INSERT INTO dbt_runs VALUES (?,?,?,NULL)', (record['id'],self.context.database_id,json.dumps(record)))
        self.assertEqual(self.read(source)['confirmed'], [])
        with control_store.connect() as db:
            db.execute('DELETE FROM dbt_runs')
            for bad in ('{broken', '[]', json.dumps(dict(parent, models=[dict(wap_model_id=[])])),
                    json.dumps(dict(parent, models=None))):
                db.execute('INSERT INTO dbt_runs VALUES (?,?,?,NULL)', (str(uuid.uuid4()),self.context.database_id,bad))
                self.assertEqual(self.read(source)['confirmed'], [])
                db.execute('DELETE FROM dbt_runs')

    def test_post_projection_revocation_and_no_metadata_mutations(self):
        from contextlib import contextmanager
        built = self.f.built()
        source = built['input_refs']['source']['ref']
        statements=[]
        connect=control_store.connect
        @contextmanager
        def traced():
            with connect() as db:
                db.set_trace_callback(statements.append)
                yield db
        with patch.object(control_store,'connect',side_effect=traced):
            self.assertTrue(self.read(source)['confirmed'])
        writes=[s for s in statements if s.lstrip().split(' ',1)[0].upper() in ('INSERT','UPDATE','DELETE','REPLACE','CREATE','DROP','ALTER')]
        self.assertEqual(writes,[])
        original=lineage._current
        reads=[]
        def revoked(metadata, ref):
            result=original(metadata, ref)
            reads.append(ref)
            # Revoke after the last endpoint REST read; final current context must fail.
            if len(reads)==4:
                with connect() as db:
                    registry.update_grant(db,self.principal.principal_id,self.context.database_id,1,'revoked')
            return result
        with patch.object(lineage,'_current',side_effect=revoked):
            with self.assertRaises(QueryError):
                self.read(source)

    def test_saved_artifact_or_query_context_tampering_omits_entire_fact(self):
        built = self.f.built()
        source = built['input_refs']['source']['ref']
        with control_store.connect() as db:
            record=dict(built, stage=dict(built['stage'],snapshot='999999'))
            db.execute('UPDATE wap_builds SET record=? WHERE id=?',(json.dumps(record),built['id']))
        self.assertEqual(self.read(source)['confirmed'],[])
        with control_store.connect() as db:
            db.execute('UPDATE wap_builds SET record=? WHERE id=?',(json.dumps(built),built['id']))
            db.execute("UPDATE query_execution_contexts SET worksheet_id='foreign' WHERE query_id IN (SELECT query_id FROM dataops_queries WHERE operation_id=?)",(built['operation_id'],))
        self.assertEqual(self.read(source)['confirmed'],[])

    def test_exact_ref_request_memo_and_fresh_final_head_drift(self):
        from collections import Counter
        built = self.f.built()
        source = built['input_refs']['source']['ref']
        original = lineage._current
        reads = Counter()
        def measured(metadata, ref):
            reads[ref['object_id']] += 1
            return original(metadata, ref)
        with patch.object(lineage, '_current', side_effect=measured):
            result = self.read(source)
            self.assertTrue(result['confirmed'])
            self.assertEqual(set(result['coverage']), {'scope', 'limited', 'empty_reason'})
        self.assertEqual(set(reads.values()), {2})
        reads.clear()
        def drift(metadata, ref):
            reads[ref['object_id']] += 1
            if reads[ref['object_id']] == 2 and ref == source:
                self.f.snapshot(self.f.metadata(registry.ResourceRef(**source)), [(3, 'drift')])
            return original(metadata, ref)
        with patch.object(lineage, '_current', side_effect=drift):
            with self.assertRaises(QueryError):
                self.read(source)

    def test_repeated_stale_endpoint_measurement_is_memoized(self):
        from collections import Counter
        built = self.f.built()
        source, stage = built['input_refs']['source']['ref'], built['stage']['ref']
        with control_store.connect() as db:
            record = json.loads(db.execute('SELECT record FROM wap_model_versions LIMIT 1').fetchone()[0])
            record.update(target_kind='ref', target=stage)
            db.execute('UPDATE wap_model_versions SET record=?', (json.dumps(record),))
        self.f.metadata(registry.ResourceRef(**stage))['table-uuid'] = str(uuid.uuid4())
        original = lineage._current
        reads = Counter()
        def measured(metadata, ref):
            reads[ref['object_id']] += 1
            return original(metadata, ref)
        with patch.object(lineage, '_current', side_effect=measured):
            result = self.read(source)
        self.assertEqual(result['confirmed'], [])
        self.assertEqual(result['declared'], [])
        self.assertEqual(reads[stage['object_id']], 1)
        self.assertEqual(reads[source['object_id']], 2)

    def test_edge_limit_after_whole_fact_validation(self):
        built = self.f.built()
        source = built['input_refs']['source']['ref']
        # A declared fact is projected first but its cross-DB endpoint is denied.
        with control_store.connect() as db:
            record = json.loads(db.execute('SELECT record FROM wap_model_versions LIMIT 1').fetchone()[0])
            record['target_kind'] = 'ref'
            record['target'] = dict(source, database_id='other')
            db.execute('UPDATE wap_model_versions SET record=?', (json.dumps(record),))
        with patch.object(lineage, 'EDGE_LIMIT', 1):
            result = self.read(source)
        self.assertEqual(len(result['confirmed']), 1)
        self.assertEqual(result['declared'], [])

    def test_same_snapshot_schema_mismatch_is_historical(self):
        built = self.f.built()
        source = built['input_refs']['source']['ref']
        raw = self.f.metadata(registry.ResourceRef(**source))
        raw['schemas'][0]['fields'][0]['name'] = 'changed_name'
        result = self.read(source)
        self.assertEqual(result['confirmed'][0]['temporal'], 'historical')

    def test_empty_table_snapshot_none_minus_one_zero_and_malformed(self):
        source = self.f.existing()
        raw = self.f.metadata(source)
        for snapshot in (None, -1):
            raw['current-snapshot-id'], raw['snapshots'] = snapshot, []
            self.assertIsNone(self.read(asdict(source))['focus']['snapshot_id'])
        raw['current-snapshot-id'] = 0
        self.assertEqual(self.read(asdict(source))['focus']['snapshot_id'], '0')
        for snapshot, snapshots in ((-2, []), (-1, [{'snapshot-id': 1}]), (None, [{'snapshot-id': 1}])):
            raw['current-snapshot-id'], raw['snapshots'] = snapshot, snapshots
            with self.assertRaises(QueryError):
                self.read(asdict(source))

    def test_managed_publication_requires_exact_visible_publisher_and_source(self):
        built, audit = self.f.audited()
        published = self.f.publish(built, audit)
        source = built['input_refs']['source']['ref']
        uid = 'model.saved.output'
        owner = dict(id=str(uuid.uuid4()), kind='run', actor=self.principal.principal_id,
            context=asdict(self.context), models=[dict(unique_id=uid, wap_model_id=built['model_id'])])
        publisher = dict(id=str(uuid.uuid4()), kind='publication', actor='hidden-publisher',
            context=asdict(replace(self.context, selected_role='admin')), parent_run_id=owner['id'], node_id=uid)
        def save():
            with control_store.connect() as db:
                for record in (owner, publisher):
                    db.execute('INSERT OR REPLACE INTO dbt_runs VALUES (?,?,?,NULL)',
                        (record['id'], self.context.database_id, json.dumps(record)))
        save()
        with control_store.connect() as db:
            db.execute('INSERT INTO dbt_run_operations VALUES (?,?,?,?)',
                (publisher['id'], 'publish', uid, published['operation_id']))
        target = published['target']['ref']
        self.assertEqual(self.read(target)['confirmed'], [])
        self.assertTrue(self.read(source)['confirmed'])
        publisher['actor'] = self.principal.principal_id
        publisher['context'] = asdict(self.context)
        save()
        self.assertTrue(self.read(target)['confirmed'])
        for field, value in [('phase', 'build'), ('node_id', 'wrong-node')]:
            with control_store.connect() as db:
                db.execute('UPDATE dbt_run_operations SET '+field+'=? WHERE operation_id=?',
                    (value, published['operation_id']))
            self.assertEqual(self.read(target)['confirmed'], [])
            with control_store.connect() as db:
                db.execute("UPDATE dbt_run_operations SET phase='publish',node_id=? WHERE operation_id=?",
                    (uid, published['operation_id']))
        owner['actor'] = 'hidden-source'
        save()
        self.assertEqual(self.read(target)['confirmed'], [])
        self.assertEqual(self.read(source)['confirmed'], [])
        self.assertTrue(self.read(target, self.f.owner, replace(self.f.admin,
            schema_id=target['schema_id'], sql_schema=self.schema_name(target['schema_id'])))['confirmed'])

    def test_unmaterialized_saved_plans_are_distinct_logical_definitions(self):
        source = asdict(self.f.existing())
        physical = asdict(self.f.existing('planned', schema='marts'))
        uid = 'model.saved.planned'
        plan = dict(id=str(uuid.uuid4()), kind='plan', state='READY', actor=self.principal.principal_id,
            context=asdict(self.context), source_digest='private', source_schema=source['schema_id'],
            target_schema=physical['schema_id'], staging_materialization=None,
            models=[dict(unique_id=uid, name='planned', materialized='table', depends_on=['source.saved.input'],
                relation=[self.context.sql_catalog, 'marts', 'planned'])], tests=[],
            source_bindings=[dict(unique_id='source.saved.input', ref=source)], _targets={uid:physical})
        def save():
            plan['_plan_digest'] = db_dbt._hash_plan(plan)
            with control_store.connect() as db:
                db.execute('INSERT INTO dbt_runs VALUES (?,?,?,NULL)',
                    (plan['id'], self.context.database_id, json.dumps(plan)))
        save()
        plan['id'] = str(uuid.uuid4())
        plan['models'][0]['materialized'] = 'view'
        save()
        result = self.read(source)
        self.assertEqual(len(result['declared']), 2)
        self.assertEqual(len({f['model']['plan_id'] for f in result['declared']}), 2)
        for fact in result['declared']:
            self.assertEqual(fact['target']['kind'], 'LOGICAL')
            self.assertNotIn('ref', fact['target'])
            self.assertNotIn('run_id', fact['model'])
        # A raw run candidate is no proof of physical adoption either.
        run = dict(id=str(uuid.uuid4()), kind='run', actor=self.principal.principal_id,
            context=asdict(self.context), plan_id=plan['id'], _plan_digest=plan['_plan_digest'],
            source_digest=plan['source_digest'], models=[dict(unique_id=uid, candidate=dict(ref=physical))])
        with control_store.connect() as db:
            db.execute('INSERT INTO dbt_runs VALUES (?,?,?,NULL)',
                (run['id'], self.context.database_id, json.dumps(run)))
        self.assertEqual(self.read(physical)['declared'], [])
        self.assertNotIn('private', json.dumps(result))
        # All typed sources must authorize even when another source is selected.
        plan['id'] = str(uuid.uuid4())
        plan['source_bindings'].append(dict(unique_id='source.hidden', ref=dict(source, database_id='other')))
        save()
        self.assertEqual(len(self.read(source)['declared']), 2)


class NativeLineageTests(unittest.TestCase):
    def test_native_table_positive_and_unknown_child_are_pure_get(self):
        import test_database_dbt_native as saved
        fixture = saved.NativeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        run = fixture.normal_run()
        self.assertEqual(run['executor'], 'native_table')
        with control_store.connect() as db:
            record = db_dbt._row(db, run['id'])[1]
            op = db.execute("SELECT operation_id FROM dbt_run_operations WHERE run_id=? AND phase='build'",
                (run['id'],)).fetchone()[0]
            receipt = db_dbt._native_saved(db, record, op)
        ref = receipt['candidate']['ref']
        with patch.object(query_service, '_transport', side_effect=AssertionError('SQL')), \
                patch('warehouse_admission.finish', side_effect=AssertionError('settle')), \
                patch.object(ds.Execution, 'inspect', side_effect=AssertionError('inspect')):
            result = lineage.get_lineage(fixture.principal, fixture.context, ref['object_id'])
            self.assertEqual([f['category'] for f in result['confirmed']], ['native_graph'])
            with control_store.connect() as db:
                db.execute("UPDATE dataops_operations SET state='UNKNOWN' WHERE id=?", (op,))
            self.assertEqual(lineage.get_lineage(fixture.principal, fixture.context,
                ref['object_id'])['confirmed'], [])

    def test_real_native_table_view_publication_historical_versions_and_source_policy(self):
        import test_database_dbt_graph_publication_views as saved
        fixture = saved.ViewPublicationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        run = fixture.candidate()
        original = fixture.record(run['id'])
        view = next(m for m in original['models'] if m['materialized'] == 'view')
        table = next(m for m in original['models'] if m['materialized'] == 'table')
        publication = fixture.finish(fixture.accept(run, fixture.preview(run)['expected_target']))
        context = fixture.context
        def read(ref, principal=None, current=None):
            return lineage.get_lineage(principal or fixture.principal, current or context, ref['object_id'])
        import database_dbt as compiler
        import database_dbt_graph as graph
        with patch.object(compiler, '_compiled_project', side_effect=AssertionError('compile')), \
                patch.object(compiler, 'compile_project', side_effect=AssertionError('compile')), \
                patch.object(graph, '_run_subprocess', side_effect=AssertionError('SDK')), \
                patch.object(query_service, '_transport', side_effect=AssertionError('SQL')), \
                patch.object(db_dbt, '_observe', side_effect=AssertionError('observe')), \
                patch('warehouse_admission.finish', side_effect=AssertionError('settle')):
            result = read(view['candidate']['ref'])
            self.assertEqual(len(result['confirmed']), 2, result)
            self.assertTrue(result['declared'])
            self.assertTrue(all(e['temporal'] == 'current' for e in result['confirmed']))
            raw = fixture.view_metadata()
            # Same-version head changes, including default resolution context, are stale.
            from copy import deepcopy
            saved_head = deepcopy(raw)
            for field, value in [('default-namespace', ['different']), ('default-catalog', 'different')]:
                raw['versions'][0][field] = value
                self.assertEqual(read(view['candidate']['ref'])['confirmed'], [])
                raw.clear(); raw.update(deepcopy(saved_head))
            raw.setdefault('properties', {})['extra-property'] = 'new-head'
            self.assertEqual(read(view['candidate']['ref'])['confirmed'], [])
            raw.clear(); raw.update(deepcopy(saved_head))
            from contextlib import contextmanager
            catalog = inventory.catalog_api
            @contextmanager
            def moved_pointer(database, binding):
                with catalog(database, binding) as api:
                    def load(path):
                        response = api(path)
                        if '/views/' in path:
                            response = dict(response, **{'metadata-location': 'changed-pointer'})
                        return response
                    yield load
            with patch.object(inventory, 'catalog_api', side_effect=moved_pointer):
                self.assertEqual(read(view['candidate']['ref'])['confirmed'], [])
            raw.setdefault('properties', {})['trino.security'] = 'DEFINER'
            with self.assertRaises(QueryError):
                read(view['candidate']['ref'])
            raw.clear(); raw.update(deepcopy(saved_head))
            raw['versions'].append(dict(raw['versions'][0], **{'version-id': 2}))
            raw['current-version-id'] = 2
            result = read(view['candidate']['ref'])
            self.assertEqual(result['focus']['view_version'], 2)
            self.assertTrue(all(e['temporal'] == 'historical' for e in result['confirmed']))
            self.assertTrue(any(e['source'].get('view_version') == 1 for e in result['confirmed']))
            owner, admin = fixture.publisher()
            target = publication['native_publication']['target']['ref']
            marts = replace(admin, schema_id=target['schema_id'], sql_schema='marts')
            result = read(target, owner, marts)
            self.assertIn('native_publication', [e['category'] for e in result['confirmed']])
            # A publication's admin actor cannot grant an engineer the hidden parent run.
            with control_store.connect() as db:
                row, record = db_dbt._row(db, run['id'])
                record['actor'] = 'hidden-engineer'
                db.execute('UPDATE dbt_runs SET record=? WHERE id=?', (json.dumps(record), run['id']))
            self.assertEqual(read(target, fixture.principal, replace(fixture.context,
                schema_id=target['schema_id'], sql_schema='marts'))['confirmed'], [])
            raw['view-uuid'] = str(uuid.uuid4())
            with self.assertRaises(QueryError):
                read(view['candidate']['ref'])


class LineageHTTPTests(unittest.TestCase):
    def test_actual_both_handlers_exact_route_query_headers_framing(self):
        import io
        from http.client import HTTPResponse
        from types import SimpleNamespace
        from urllib.parse import urlencode
        import test_dataops_routes as http
        import request_auth
        import runtime_profile
        import warehouse_resident as wr
        handlers = []
        class Capture:
            def __init__(self, address, handler): handlers.append(handler)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def handle_request(self): pass
        with ExitStack() as stack:
            stack.enter_context(patch.object(wr, '_profile_active', return_value=True))
            stack.enter_context(patch('logical_creations.enabled', return_value=False))
            stack.enter_context(patch('http.server.ThreadingHTTPServer', Capture))
            stack.enter_context(patch('database_creation_bridge.configure'))
            stack.enter_context(patch.object(wr, 'warehouse', return_value='LOCAL'))
            wr.serve_endpoint(once=True)
        class Connection:
            def __init__(self, incoming): self.incoming, self.outgoing = io.BytesIO(incoming), io.BytesIO()
            def makefile(self, *args): return self.incoming
            def sendall(self, data): self.outgoing.write(data)
        ident = str(uuid.uuid4())
        path = '/api/v1/objects/' + ident + '/lineage'
        query = '?' + urlencode(dict(context=json.dumps(asdict(http.C))))
        def request(handler, target, method='GET', extra='', body=b''):
            raw = (method+' '+target+' HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n'
                'X-Shirokuma-Role: data_engineer\r\n'+extra+'\r\n').encode()+body
            connection = Connection(raw)
            with patch.object(handler, 'log_message'):
                handler(connection, ('127.0.0.1', 1), SimpleNamespace())
            response = HTTPResponse(Connection(connection.outgoing.getvalue())); response.begin()
            return response.status, json.loads(response.read())
        principal = request_auth.AuthenticatedPrincipal('alice', 'data_engineer', False)
        dto = dict(focus={},confirmed=[],declared=[],coverage={})
        with patch.object(runtime_profile, 'active', return_value=True), \
                patch.object(request_auth, 'authenticate_principal', return_value=principal), \
                patch.object(lineage, 'get_lineage', return_value=dto) as read:
            for handler in (http.wb.WorkbenchHandler, handlers[0]):
                self.assertEqual(request(handler, path+query), (200, dto))
                self.assertEqual(read.call_args.args, (principal, http.C, ident))
                for target, method, extra, body in [
                        (path+query, 'POST', '', b''),
                        (path, 'GET', '', b''),
                        (path+query+'&extra=1', 'GET', '', b''),
                        (path+query+'&'+query[1:], 'GET', '', b''),
                        (path+query, 'GET', 'Content-Length: 1\r\n', b'x'),
                        (path+query, 'GET', 'Content-Length: 0\r\nContent-Length: 0\r\n', b''),
                        (path+query, 'GET', 'Transfer-Encoding: chunked\r\n', b''),
                        (path+query, 'GET', 'X-Shirokuma-Role: data_engineer\r\n', b''),
                        (path.replace(ident, 'not-an-id')+query, 'GET', '', b''),
                        (path+'?'+urlencode(dict(context='{ "database_id":"x","database_id":"y"}')), 'GET', '', b'')]:
                    read.reset_mock()
                    status, error = request(handler, target, method, extra, body)
                    self.assertIn(status, (400, 404), (target, status, error))
                    read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
