"""Durable dbt workflow using real WAP/admission and the existing SQL harness."""
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store
import database_dbt as compiler
import data_management as dm
import dataops_service as ds
import db_dbt as dbt
import manual_wap as wap
import query_service as qs
from query_context import QueryError
import warehouse_admission as admission
import test_manual_wap


class DbtTests(unittest.TestCase):
    def setUp(self):
        self.w = test_manual_wap.ManualWapTests()
        self.w.setUp()
        self.addCleanup(self.w.doCleanups)
        self.b = self.w.b
        self.engineer = self.w.principal
        self.admin = self.w.owner
        self.context = self.b.context(schema='sandbox')
        self.admin_context = self.b.context(schema='sandbox', principal=self.admin, role='admin')
        self.sources = {d: self.w.existing('source_rows', database=d, rows=[(1, 'one'), (2, 'two')])
            for d in self.b.databases[:2]}
        self.start_patch = patch.object(dbt, '_start')
        self.start = self.start_patch.start()
        self.addCleanup(self.start_patch.stop)
        self.compile_patch = patch.object(compiler, 'compile_project', side_effect=self.summary)
        self.compile = self.compile_patch.start()
        self.addCleanup(self.compile_patch.stop)
        self.addCleanup(self.clear_live)

    def clear_live(self):
        for live in dbt._live.values():
            live[-1].close()
        dbt._live.clear()

    def summary(self, project, *, catalog, source_schema, staging_materialization):
        source = [catalog, source_schema, 'source_rows']
        first, second = [catalog, 'sandbox', 'first'], [catalog, 'sandbox', 'second']
        def model(uid, relation, sql, depends, materialized='table'):
            return dict(unique_id=uid, name=uid.rsplit('.', 1)[1], relation=relation, compiled_sql=sql,
                depends_on=depends, materialized=materialized, table_properties={'format': 'PARQUET'} if materialized == 'table' else {})
        return dict(compatibility=compiler.compatibility(), source_digest='copied-digest', versions={}, sources=[dict(unique_id='source.rows', name='rows', relation=source)],
            models=[model('model.stage', None, f'SELECT id, label FROM {catalog}.{source_schema}.source_rows', ['source.rows'], 'ephemeral'),
                model('model.first', first, f'SELECT id, label FROM {catalog}.{source_schema}.source_rows', ['model.stage']),
                model('model.second', second, f'SELECT id, label FROM {catalog}.sandbox.first', ['model.first'])],
            tests=[dict(unique_id='test.first', name='first_not_null', test_type='not_null', depends_on=['model.first'],
                compiled_sql=f'SELECT id FROM {catalog}.sandbox.first WHERE id IS NULL'),
                dict(unique_id='test.second', name='second_not_null', test_type='not_null', depends_on=['model.second'],
                compiled_sql=f'SELECT id FROM {catalog}.sandbox.second WHERE id IS NULL')])

    def plan(self, context=None, key='plan'):
        context = context or self.context
        ack = dbt.submit_plan(self.engineer, context, key=key, project_id='lake_marts', source_schema='raw',
            target_schema='marts', staging_materialization='ephemeral')
        self.assertEqual(ack['state'], 'QUEUED')
        dbt._execute(ack['id'])
        plan = dbt.get_plan(self.engineer, context, ack['id'])
        self.assertEqual(plan['state'], 'READY', plan)
        return plan

    def build_run(self, plan=None, context=None, key='run'):
        context = context or self.context
        plan = plan or self.plan(context, key='plan-' + key)
        ack = dbt.submit_run(self.engineer, context, key=key, plan_id=plan['id'])
        self.assertEqual(ack['state'], 'QUEUED')
        dbt._execute(ack['id'])
        return dbt.get_run(self.engineer, context, ack['id'])

    def publish(self, run, uid='model.first', key='publish'):
        ack = dbt.submit_publication(self.admin, self.admin_context, run['id'], key=key, node_id=uid)
        self.assertEqual(ack['state'], 'QUEUED')
        dbt._execute(ack['id'])
        return dbt.get_publication(self.admin, self.admin_context, ack['id'])

    def code(self, code, function, *args, **kwargs):
        with self.assertRaises(QueryError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def record(self, ident):
        with control_store.connect() as db:
            return dbt._row(db, ident)[1]

    def mutate(self, ident, fn):
        with control_store.connect() as db:
            _, record = dbt._row(db, ident)
            fn(record)
            dbt._write(db, record)

    def view_project(self):
        def summary(*args, **kwargs):
            result = self.summary(*args, **kwargs)
            result['models'][1].update(materialized='view', table_properties={}, view_security='INVOKER')
            return result
        self.compile.side_effect = summary

    def view_metadata(self):
        return [value for key, value in self.b.objects.items() if key[3] == 'views'][-1]

    def test_true_view_pins_original_source_downstream_table_and_tests(self):
        self.view_project()
        source = self.sources[self.context.database_id]
        original = str(self.w.metadata(source)['current-snapshot-id'])
        transport = self.w.transport
        def append(url, method, pin, body=None):
            result = transport(url, method, pin, body)
            if body.decode().startswith('CREATE VIEW '):
                with control_store.connect() as db:
                    child = db.execute("SELECT o.id,l.run_id,l.node_id FROM dataops_operations o JOIN dbt_run_operations l ON l.operation_id=o.id WHERE o.route='dbt-view-create'").fetchone()
                    self.assertEqual(child['node_id'], 'model.first')
                    self.assertIsNotNone(db.execute('SELECT query_id FROM dataops_queries WHERE operation_id=?', (child['id'],)).fetchone())
                self.w.snapshot(self.w.metadata(source), [(9, 'new')])
            return result
        with patch.object(qs, '_transport', side_effect=append):
            run = self.build_run()
        self.assertEqual(run['state'], 'READY', run)
        view, table = run['models'][1:]
        self.assertEqual(view['candidate']['kind'], 'VIEW')
        self.assertEqual(view['candidate']['version'], 1)
        self.assertNotIn('snapshot', view['candidate'])
        self.assertEqual(view['candidate']['security'], 'INVOKER')
        self.assertEqual(view['view_security'], 'INVOKER')
        self.assertIsNone(view['build_id'])
        sql = self.view_metadata()['versions'][0]['representations'][0]['sql']
        self.assertIn('FOR VERSION AS OF ' + original, sql)
        create = [s for s, _ in self.w.posts if s.startswith('CREATE VIEW ')]
        self.assertEqual(len(create), 1)
        self.assertIn(' SECURITY INVOKER AS ', create[0])
        self.assertNotIn('format=', create[0])
        downstream = next(s for s, _ in self.w.posts if s.startswith('CREATE TABLE '))
        self.assertIn('dbtv_', downstream)
        self.assertNotIn('FOR VERSION AS OF', downstream)
        self.assertIn("WITH (format='PARQUET')", downstream)
        view_tests = [s for s, _ in self.w.posts if s.startswith('SELECT COUNT(*)') and 'dbtv_' in s]
        self.assertEqual(len(view_tests), 1)
        self.assertNotIn('FOR VERSION AS OF', view_tests[0])
        raw_build = wap.get_build(self.engineer, self.context, build_id=table['build_id'])
        self.assertEqual(next(iter(raw_build['input_refs'].values())),
            self.record(run['id'])['models'][1]['_view_measurement'])
        self.assertEqual([t['state'] for t in run['tests']], ['PASSED', 'PASSED'])
        stage = self.w.metadata(dm.ResourceRef(**table['candidate']['ref']))
        self.assertEqual(self.w.snapshots[(stage['table-uuid'], stage['current-snapshot-id'])], [(1, 'one'), (2, 'two')])
        self.code('DBT_NODE_NOT_FOUND', dbt.submit_publication, self.admin, self.admin_context,
            run['id'], key='no-view-publish', node_id='model.first')
        self.code('SQL_PERMISSION_DENIED', dbt.submit_publication, self.engineer, self.context,
            run['id'], key='no-engineer-publish', node_id='model.second')
        self.assertEqual(self.publish(run, uid='model.second')['state'], 'PUBLISHED')
        public = json.dumps(dbt.get_run(self.engineer, self.context, run['id']))
        self.assertNotIn('_head_digest', public)
        self.assertNotIn('_definition_digest', public)

    def test_canonical_invoker_metadata_pointer_and_effective_security(self):
        from contextlib import contextmanager
        self.view_project()
        run = self.build_run()
        self.assertEqual(run['state'], 'READY', run)
        view = self.view_metadata()
        self.assertNotIn('properties', view)
        saved = self.record(run['id'])['models'][1]['_view_measurement']
        ref = dm.ResourceRef(**saved['ref'])
        metadata = wap._access(self.engineer, self.context)
        self.assertEqual(metadata._dbt_measure_view(ref), saved)
        self.assertEqual(saved['view_uuid'], view['view-uuid'])
        self.assertEqual(saved['version'], view['current-version-id'])
        for properties in ({'trino.run-as-owner': 'true'}, {'trino.run-as-owner': 'false'},
                {'trino.run-as-owner': False}, {'trino.run-as-owner': None},
                {'trino.security': 'DEFINER'}, {'trino.owner': 'someone'}, {'owner': 'someone'}, None, []):
            with self.subTest(properties=properties):
                view['properties'] = properties
                self.code('DBT_VIEW_CHANGED', metadata._dbt_measure_view, ref)
        view.pop('properties')
        catalog = self.b.catalog
        for pointer in (None, '', '  ', 7, []):
            @contextmanager
            def malformed(database, binding):
                with catalog(database, binding) as api:
                    yield lambda path: dict(api(path), **{'metadata-location': pointer})
            with self.subTest(pointer=pointer), patch.object(ds.inventory, 'catalog_api', malformed):
                self.code('DBT_VIEW_CHANGED', metadata._dbt_measure_view, ref)
        with patch.object(ds.inventory, 'catalog_api', catalog):
            self.code('DBT_VIEW_CHANGED', metadata._dbt_measure_view, ref)

    def test_view_only_and_independent_final_view_require_fresh_ready_head(self):
        complete = ds.Execution.complete
        for only_view in (True, False):
            for drift in (False, True):
                with self.subTest(only_view=only_view, drift=drift):
                    def summary(*args, **kwargs):
                        result = self.summary(*args, **kwargs)
                        # An independent last VIEW, with no downstream query/test.
                        view = dict(result['models'][1], materialized='view', depends_on=['source.rows'],
                            table_properties={}, view_security='INVOKER')
                        result['models'] = [view] if only_view else [result['models'][0], result['models'][1],
                            dict(view, unique_id='model.final', name='final',
                                relation=[kwargs['catalog'], 'sandbox', 'final'])]
                        result['tests'] = []
                        return result
                    self.compile.side_effect = summary
                    plan = self.plan(key=f'final-plan-{only_view}-{drift}')
                    view_ids = {m['unique_id'] for m in plan['models'] if m['materialized'] == 'view'}
                    self.assertTrue(all(m['view_security'] == 'INVOKER' for m in plan['models']
                        if m['materialized'] == 'view'))
                    self.assertFalse(view_ids & set(self.record(plan['id'])['_targets']))
                    def change_after_observation(execution, result=None):
                        child = complete(execution, result)
                        if drift and execution._row()['route'] == 'dbt-view-create':
                            self.view_metadata()['version-log'].append({'version-id': 1, 'timestamp-ms': 2000})
                        return child
                    with patch.object(ds.Execution, 'complete', change_after_observation):
                        run = self.build_run(plan, key=f'final-run-{only_view}-{drift}')
                    self.assertEqual(run['state'], 'FAILED' if drift else 'READY', run)
                    if drift:
                        self.assertEqual(run['error_code'], 'DBT_VIEW_CHANGED')
                    else:
                        self.assertTrue(all(m['state'] in ('BUILT', 'EPHEMERAL') for m in run['models']))

    def test_complete_verification_requires_declared_models_and_view_receipts(self):
        self.view_project()
        run = self.build_run()
        metadata = wap._access(self.admin, self.admin_context)
        original = self.record(run['id'])
        mutations = [lambda r: r['models'].pop(1),
            lambda r: r['models'][1].pop('_view_measurement'),
            lambda r: r['models'][1].update(materialized='ephemeral'),
            lambda r: r['models'][2].update(state='QUEUED'),
            lambda r: r['models'][1].update(view_security='DEFINER')]
        for mutate in mutations:
            altered = json.loads(json.dumps(original))
            mutate(altered)
            with self.assertRaises(QueryError):
                dbt._verify_views(metadata, altered, complete=True)
        # Partial BUILDING verification does not require a future VIEW receipt.
        partial = json.loads(json.dumps(original))
        partial['state'] = 'BUILDING'
        partial['models'][1].update(state='QUEUED', candidate=None)
        partial['models'][1].pop('_view_measurement')
        dbt._verify_views(metadata, partial)

    def test_publication_uses_original_engineer_receipt_and_rejects_child_link_tampering(self):
        self.view_project()
        run = self.build_run()
        saved = self.record(run['id'])['models'][1]['_view_measurement']
        # The independent admin gets original E refs, with original child ownership.
        metadata = wap._access(self.admin, self.admin_context)
        with patch.object(metadata, '_dbt_measure_view', wraps=metadata._dbt_measure_view) as observe:
            dbt._verify_views(metadata, self.record(run['id']), complete=True)
        self.assertEqual(asdict(observe.call_args.args[0]), saved['ref'])
        self.assertEqual(metadata.principal, self.admin)
        for field, value in (('actor', self.admin.principal_id), ('context', ds._json(asdict(self.admin_context))),
                ('result', ds._json({'view_created': True})),
                ('result', ds._json(dict(view=dict(dbt._view_public(saved), version=999),
                    view_digest=hashlib.sha256(ds._json(saved).encode()).hexdigest()))),
                ('state', 'UNKNOWN')):
            with self.subTest(field=field):
                child_id = self.record(run['id'])['models'][1]['operation_ids'][0]
                with control_store.connect() as db:
                    original = db.execute('SELECT ' + field + ' FROM dataops_operations WHERE id=?', (child_id,)).fetchone()[0]
                    db.execute('UPDATE dataops_operations SET ' + field + '=? WHERE id=?', (value, child_id))
                self.code('DBT_VIEW_CHANGED', dbt._verify_views, metadata, self.record(run['id']), complete=True)
                with control_store.connect() as db:
                    db.execute('UPDATE dataops_operations SET ' + field + '=? WHERE id=?', (original, child_id))
        with control_store.connect() as db:
            db.execute("UPDATE dbt_run_operations SET node_id='model.other' WHERE operation_id=?", (child_id,))
        count = len(self.w.posts)
        publication = self.publish(run, uid='model.second')
        self.assertEqual(publication['error_code'], 'DBT_VIEW_CHANGED', publication)
        self.assertEqual(len(self.w.posts), count)

    def test_views_are_private_scoped_only_and_not_general_wap_inputs(self):
        self.view_project()
        run = self.build_run()
        ref = dm.ResourceRef(**run['models'][1]['candidate']['ref'])
        metadata = wap._access(self.engineer, self.context)
        self.code('DBT_CONTINUATION_REQUIRED', metadata._dbt_read_ref, ref)
        self.code('STALE_RESOURCE', metadata._ref, ref)
        self.code('STALE_RESOURCE', metadata.wap_measure, ref)
        self.code('STALE_RESOURCE', wap.save_model, self.engineer, self.context, model_id='external-view',
            sql='SELECT id FROM v', sources={'v': ref}, target=self.b.locator('external', schema='marts'), expected_version=None)
        execution = ds.begin(self.engineer, self.context, route='outside', idempotency_key='outside', payload={})
        self.code('DBT_CONTINUATION_REQUIRED', execution._dbt_create_view)
        execution.fail()

    def test_view_head_drift_before_after_build_and_after_test_never_passes(self):
        for moment in ('before_build', 'after_build', 'after_test'):
            with self.subTest(moment=moment):
                self.view_project()
                plan = self.plan(key='plan-' + moment)
                ack = dbt.submit_run(self.engineer, self.context, key=moment, plan_id=plan['id'])
                phase, transport = dbt._phase, self.w.transport
                def drift():
                    view = list(v for k, v in self.b.objects.items() if k[3] == 'views')[-1]
                    view['current-version-id'] += 1
                    view['versions'].append(dict(view['versions'][-1], **{'version-id': view['current-version-id']}))
                def before(*args):
                    if args[1:4] == ('BUILDING', 'build', 'model.second') and moment == 'before_build':
                        drift()
                    return phase(*args)
                def after(url, method, pin, body=None):
                    result = transport(url, method, pin, body)
                    sql = body.decode()
                    if moment == 'after_build' and sql.startswith('CREATE TABLE ') or moment == 'after_test' and sql.startswith('SELECT COUNT(*)'):
                        drift()
                    return result
                with patch.object(dbt, '_phase', side_effect=before), patch.object(qs, '_transport', side_effect=after):
                    dbt._execute(ack['id'])
                run = dbt.get_run(self.engineer, self.context, ack['id'])
                self.assertEqual(run['state'], 'FAILED', run)
                self.assertEqual(run['error_code'], 'DBT_VIEW_CHANGED', run)
                self.assertFalse(any(t['state'] == 'PASSED' for t in run['tests']))
                self.code('DBT_TEST_GATE_FAILED', dbt.submit_publication, self.admin, self.admin_context,
                    run['id'], key='blocked-' + moment, node_id='model.second')

    def test_view_saved_uuid_version_and_head_tampering_blocks_publication(self):
        self.view_project()
        for field, value in (('view_uuid', '00000000-0000-0000-0000-000000000000'), ('version', 2),
                ('_head_digest', 'other'), ('security', 'DEFINER')):
            with self.subTest(field=field):
                run = self.build_run(key='tamper-' + field)
                self.assertEqual(run['state'], 'READY', run)
                self.mutate(run['id'], lambda r: r['models'][1]['_view_measurement'].update({field: value}))
                count = len(self.w.posts)
                publication = self.publish(run, uid='model.second', key='publish-' + field)
                self.assertEqual(publication['state'], 'FAILED', publication)
                self.assertEqual(publication['error_code'], 'DBT_VIEW_CHANGED')
                self.assertEqual(len(self.w.posts), count)

    def test_view_location_aba_and_security_drift_block_publication(self):
        from contextlib import contextmanager
        catalog = self.b.catalog
        location = ['private-metadata-1']
        @contextmanager
        def with_location(database, binding):
            with catalog(database, binding) as api:
                def get(path):
                    response = api(path)
                    return dict(response, **{'metadata-location': location[0]}) if '/views/' in path else response
                yield get
        self.view_project()
        with patch.object(ds.inventory, 'catalog_api', with_location):
            run = self.build_run()
            self.assertEqual(run['state'], 'READY', run)
            location[0] = 'private-metadata-2'  # Same UUID/version/definition, different full head.
            count = len(self.w.posts)
            publication = self.publish(run, uid='model.second')
            self.assertEqual(publication['error_code'], 'DBT_VIEW_CHANGED')
            self.assertEqual(len(self.w.posts), count)
            self.assertNotIn('private-metadata', json.dumps(run))
        run = self.build_run(key='security-drift')
        self.assertEqual(run['state'], 'READY', run)
        self.view_metadata()['properties'] = {'trino.run-as-owner': 'true'}
        count = len(self.w.posts)
        publication = self.publish(run, uid='model.second', key='publish-security')
        self.assertEqual(publication['error_code'], 'DBT_VIEW_CHANGED')
        self.assertEqual(len(self.w.posts), count)

    def test_view_postcommit_metadata_uncertainty_retains_commit_without_replay(self):
        self.view_project()
        original = ds.Execution._dbt_measure_view
        def fail(execution, ref):
            if hasattr(execution, 'id'):
                raise QueryError('CATALOG_UNAVAILABLE', 502)
            return original(execution, ref)
        with patch.object(ds.Execution, '_dbt_measure_view', fail):
            run = self.build_run()
        self.assertEqual(run['state'], 'FAILED', run)
        saved = self.record(run['id'])['models'][1]
        self.assertTrue(saved['_created_view_ref'])
        self.assertEqual(saved['state'], 'COMMITTED_RECORDING_FAILED')
        op = ds.Execution(self.engineer, saved['operation_ids'][0]).inspect()
        self.assertTrue(op['committed'])
        self.assertEqual(op['cleanup_error'], 'CATALOG_UNAVAILABLE')
        self.assertEqual(op['result'], {'view_created': True})
        count = len(self.w.posts)
        dbt.get_run(self.engineer, self.context, run['id'])
        dbt.submit_run(self.engineer, self.context, key='run', plan_id=run['plan_id'])
        self.assertEqual(len(self.w.posts), count)
        self.assertTrue(self.view_metadata())

    def test_view_lost_ack_owner_observation_and_cancel_retain_known_view(self):
        self.view_project()
        plan = self.plan()
        ack = dbt.submit_run(self.engineer, self.context, key='lost-view', plan_id=plan['id'])
        transport = self.w.transport
        def lose(url, method, pin, body=None):
            transport(url, method, pin, body)
            raise OSError('response lost')
        with patch.object(qs, '_transport', side_effect=lose):
            dbt._execute(ack['id'])
        run = dbt.get_run(self.engineer, self.context, ack['id'])
        self.assertEqual(run['state'], 'UNKNOWN', run)
        self.assertTrue(self.view_metadata())
        count = len(self.w.posts)
        for op in run['operations']:
            live = ds._live.pop(op['id'], None)
            if live:
                live[2].close()
        dbt.get_run(self.engineer, self.context, ack['id'])
        dbt.submit_run(self.engineer, self.context, key='lost-view', plan_id=plan['id'])
        dbt.cancel_run(self.engineer, self.context, run['id'], key='cancel-unknown')
        self.assertEqual(len(self.w.posts), count)
        self.assertTrue(self.view_metadata())
        self.code('DBT_TEST_GATE_FAILED', dbt.submit_publication, self.admin, self.admin_context,
            run['id'], key='no-lost-view-publish', node_id='model.second')

    def test_view_cancel_after_create_retains_identity_and_sends_no_downstream_sql(self):
        self.view_project()
        plan = self.plan()
        ack = dbt.submit_run(self.engineer, self.context, key='cancel-view', plan_id=plan['id'])
        transport = self.w.transport
        def cancel(url, method, pin, body=None):
            result = transport(url, method, pin, body)
            if body.decode().startswith('CREATE VIEW '):
                dbt.cancel_run(self.engineer, self.context, ack['id'], key='cancel-created')
            return result
        with patch.object(qs, '_transport', side_effect=cancel):
            dbt._execute(ack['id'])
        run = dbt.get_run(self.engineer, self.context, ack['id'])
        self.assertTrue(run['cancel_requested'])
        self.assertIn(run['state'], ('CANCELED', 'UNKNOWN'))
        self.assertEqual(len(self.w.posts), 1)
        self.assertTrue(self.view_metadata())
        self.assertFalse(any(t['state'] == 'PASSED' for t in run['tests']))
        dbt.get_run(self.engineer, self.context, run['id'])
        self.assertEqual(len(self.w.posts), 1)

    def test_missing_saved_view_measurement_cannot_bypass_publication_guard(self):
        self.view_project()
        run = self.build_run()
        self.assertEqual(run['state'], 'READY', run)
        self.mutate(run['id'], lambda r: r['models'][1].pop('_view_measurement'))
        count = len(self.w.posts)
        publication = self.publish(run, uid='model.second')
        self.assertEqual(publication['state'], 'FAILED', publication)
        self.assertEqual(publication['error_code'], 'DBT_VIEW_CHANGED')
        self.assertEqual(len(self.w.posts), count)

    def test_view_owner_loss_after_known_create_is_observation_only(self):
        self.view_project()
        plan = self.plan()
        ack = dbt.submit_run(self.engineer, self.context, key='owner-view', plan_id=plan['id'])
        complete = ds.Execution.complete
        def stop(execution, result=None):
            if execution._row()['route'] == 'dbt-view-create':
                raise SystemExit('owner exited after SQL and metadata observation')
            return complete(execution, result)
        with patch.object(ds.Execution, 'complete', stop), self.assertRaises(SystemExit):
            dbt._execute(ack['id'])
        saved = self.record(ack['id'])['models'][1]
        self.assertTrue(saved['_created_view_ref'])
        self.assertTrue(self.view_metadata())
        for op in saved['operation_ids']:
            live = ds._live.pop(op, None)
            if live:
                live[2].close()
        count = len(self.w.posts)
        run = dbt.get_run(self.engineer, self.context, ack['id'])
        self.assertNotEqual(run['state'], 'READY', run)
        dbt.submit_run(self.engineer, self.context, key='owner-view', plan_id=plan['id'])
        dbt.get_run(self.engineer, self.context, ack['id'])
        self.assertEqual(len(self.w.posts), count)
        self.assertEqual(count, 1)
        self.code('DBT_TEST_GATE_FAILED', dbt.submit_publication, self.admin, self.admin_context,
            run['id'], key='no-owner-publish', node_id='model.second')

    def test_initial_view_definition_substitution_is_not_adopted(self):
        self.view_project()
        transport = self.w.transport
        def replace_definition(url, method, pin, body=None):
            result = transport(url, method, pin, body)
            if body.decode().startswith('CREATE VIEW '):
                self.view_metadata()['versions'][0]['representations'][0]['sql'] = 'SELECT 99 AS id'
            return result
        with patch.object(qs, '_transport', side_effect=replace_definition):
            run = self.build_run()
        self.assertEqual(run['state'], 'FAILED', run)
        self.assertEqual(run['error_code'], 'DBT_VIEW_CHANGED')
        self.assertEqual(run['models'][1]['state'], 'COMMITTED_RECORDING_FAILED')
        self.assertEqual(len(self.w.posts), 1)
        self.assertFalse(any(t['state'] == 'PASSED' for t in run['tests']))

    def test_view_action_validates_saved_child_request_before_sql(self):
        self.view_project()
        create = ds.Execution._dbt_create_view
        for field, value in (('route', 'outside'), ('payload', '{}'), ('fingerprint', 'forged')):
            with self.subTest(field=field):
                def tamper(execution):
                    with control_store.connect() as db:
                        db.execute('UPDATE dataops_operations SET ' + field + '=? WHERE id=?', (value, execution.id))
                    return create(execution)
                before = len(self.w.posts)
                with patch.object(ds.Execution, '_dbt_create_view', tamper):
                    run = self.build_run(key='intent-' + field)
                self.assertEqual(run['state'], 'FAILED', run)
                self.assertEqual(run['error_code'], 'DBT_CONTINUATION_REQUIRED')
                self.assertEqual(len(self.w.posts), before)

    def test_c1_explicit_table_plan_still_admitted_with_original_properties(self):
        plan = self.plan()
        def c1(record):
            record['compatibility']['version'] = 1
            record['_plan_digest'] = dbt._hash_plan(record)
        self.mutate(plan['id'], c1)
        run = self.build_run(plan)
        self.assertEqual(run['state'], 'READY', run)
        publication = self.publish(run)
        self.assertEqual(publication['state'], 'PUBLISHED')
        before = len(self.w.posts)
        self.assertEqual(dbt.submit_run(self.engineer, self.context, key='run', plan_id=plan['id']),
            dbt.get_run(self.engineer, self.context, run['id']))
        self.assertEqual(dbt.submit_publication(self.admin, self.admin_context, run['id'],
            key='publish', node_id='model.first'), publication)
        self.assertEqual(len(self.w.posts), before)
        self.assertTrue(all("WITH (format='PARQUET')" in sql for sql, _ in self.w.posts if sql.startswith('CREATE ')))

    def test_general_two_model_workflow_and_independent_admin_publication(self):
        run = self.build_run()
        self.assertEqual(run['state'], 'READY', run)
        self.assertEqual([m['state'] for m in run['models']], ['EPHEMERAL', 'BUILT', 'BUILT'])
        self.assertEqual([t['failure_count'] for t in run['tests']], [0, 0])
        self.assertEqual(len(self.w.posts), 4)
        self.assertNotIn('_measurement', json.dumps(run))
        self.assertNotIn('original-secret', json.dumps(run))
        pub = self.publish(run)
        self.assertEqual(pub['state'], 'PUBLISHED', pub)
        self.assertEqual(pub['actor'], self.admin.principal_id)
        self.assertEqual(pub['context'], asdict(self.admin_context))
        self.assertEqual(len(self.w.posts), 6)
        with control_store.connect() as db:
            build_parent = admission.get_for_subject(db, kind='dbt', subject_id=run['id'], warehouse_id='w')
            pub_parent = admission.get_for_subject(db, kind='dbt', subject_id=pub['id'], warehouse_id='w')
            self.assertNotEqual(build_parent.id, pub_parent.id)
            self.assertEqual(build_parent.actor, self.engineer.principal_id)
            self.assertEqual(pub_parent.actor, self.admin.principal_id)
            self.assertEqual(admission.check_execution(db, build_parent)['state'], 'FINISHED')
            for op in pub['operations']:
                actor = db.execute('SELECT actor FROM dataops_operations WHERE id=?', (op['id'],)).fetchone()[0]
                self.assertEqual(actor, self.admin.principal_id)
        self.assertEqual(dbt.get_run(self.admin, self.admin_context, run['id'])['state'], 'READY')
        before = len(self.w.posts)
        replay = dbt.submit_publication(self.admin, self.admin_context, run['id'], key='publish', node_id='model.first')
        self.assertEqual(replay, pub)
        self.code('DBT_PUBLICATION_EXISTS', dbt.submit_publication, self.admin, self.admin_context,
            run['id'], key='new-key', node_id='model.first')
        self.assertEqual(len(self.w.posts), before)

    def test_fixed_format_build_new_publication_and_replacement(self):
        run = self.build_run()
        self.assertEqual(run['state'], 'READY', run)
        self.assertEqual(run['models'][1]['table_properties'], {'format': 'PARQUET'})
        self.assertTrue(all(" WITH (format='PARQUET') AS " in sql
            for sql, _ in self.w.posts if sql.startswith('CREATE ')))
        pub = self.publish(run)
        self.assertEqual(pub['state'], 'PUBLISHED', pub)
        self.assertIn(" WITH (format='PARQUET') AS ", self.w.posts[-1][0])
        replacement = self.build_run(key='replacement')
        self.assertEqual(self.publish(replacement, key='replace')['state'], 'PUBLISHED')
        self.assertTrue(self.w.posts[-1][0].startswith('CREATE OR REPLACE TABLE '))
        self.assertIn(" WITH (format='PARQUET') AS ", self.w.posts[-1][0])

    def test_standalone_manual_wap_sql_keeps_existing_property_behavior(self):
        source = self.sources[self.context.database_id]
        model = wap.save_model(self.engineer, self.context, model_id='ordinary_manual',
            sql='SELECT id, label FROM source', sources={'source': source},
            target=self.b.locator('manual_output', schema='marts'), expected_version=None)
        built = wap.build(self.engineer, self.context, key='manual-build',
            model_id=model['model_id'], version=model['version'])['result']['build']
        audit = wap.audit(self.admin, self.admin_context, key='manual-audit', build_id=built['id'])['result']['audit']
        wap.publish(self.admin, self.admin_context, key='manual-publish', build_id=built['id'], audit_id=audit['id'])
        self.assertTrue(any(sql.startswith('CREATE TABLE ') for sql, _ in self.w.posts))
        self.assertFalse(any('WITH (format=' in sql for sql, _ in self.w.posts))

    def test_compile_blocked_contract_is_saved_without_admission_or_children(self):
        cases = [('model', {'materialized': 'view', 'view_security': 'definer'}, 'UNSUPPORTED_VIEW_SECURITY'),
            ('model', {'materialized': 'table', 'properties': {'format': "'ORC'"}}, 'UNSUPPORTED_TABLE_PROPERTIES'),
            ('model', {'materialized': 'table', 'on_table_exists': 'drop'}, 'UNSUPPORTED_MODEL_CONFIG'),
            ('test', {'severity': 'warn'}, 'UNSUPPORTED_TEST_CONFIG')]
        for kind, config, code in cases:
            with self.subTest(code=code):
                def reject(*args, **kwargs):
                    compiler._semantic_config({'node.example': dict(resource_type=kind, config=config)})
                self.compile.side_effect = reject
                ack = dbt.submit_plan(self.engineer, self.context, key='blocked-' + code, project_id='lake_marts',
                    source_schema='raw', target_schema='marts', staging_materialization=None)
                dbt._execute(ack['id'])
                plan = dbt.get_plan(self.engineer, self.context, ack['id'])
                self.assertEqual(plan['state'], 'FAILED')
                self.assertEqual(plan['compatibility']['status'], 'blocked')
                self.assertEqual(plan['compatibility']['issues'][0]['code'], code)
                with control_store.connect() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM warehouse_admissions').fetchone()[0], 0)
                    self.assertEqual(db.execute('SELECT count(*) FROM dataops_operations').fetchone()[0], 0)
                self.assertEqual(self.w.posts, [])

    def legacy(self, plan):
        def change(record):
            record.pop('compatibility')
            for model in record['models']:
                model.pop('table_properties')
            record['_plan_digest'] = dbt._hash_plan(record)
        self.mutate(plan['id'], change)
        return self.record(plan['id'])

    def test_legacy_recompile_required_but_admitted_run_publication_still_work(self):
        plan = self.plan()
        ack = dbt.submit_run(self.engineer, self.context, key='accepted', plan_id=plan['id'])
        legacy = self.legacy(plan)
        def admitted(record):
            record['_plan_digest'] = legacy['_plan_digest']
            for model in record['models']:
                model.pop('table_properties')
        self.mutate(ack['id'], admitted)
        before = len(self.w.posts)
        self.code('DBT_PLAN_RECOMPILE_REQUIRED', dbt.submit_run, self.engineer, self.context,
            key='new-legacy', plan_id=plan['id'])
        self.assertNotIn('compatibility', dbt.get_plan(self.engineer, self.context, plan['id']))
        dbt._execute(ack['id'])
        run = dbt.get_run(self.engineer, self.context, ack['id'])
        self.assertEqual(run['state'], 'READY', run)
        self.assertTrue(all('table_properties' not in m for m in run['models']))
        self.assertEqual(self.publish(run)['state'], 'PUBLISHED')
        self.assertFalse(any('WITH (format=' in sql for sql, _ in self.w.posts[before:]))
        count = len(self.w.posts)
        self.assertEqual(dbt.reconcile_run(self.engineer, self.context, run['id'], key='legacy-reconcile')['state'], 'READY')
        self.assertEqual(dbt.cancel_run(self.engineer, self.context, run['id'], key='legacy-cancel')['state'], 'CANCELED')
        self.assertEqual(len(self.w.posts), count)

    def test_tampered_property_or_contract_hash_blocks_before_sql(self):
        for field in ('property', 'contract'):
            plan = self.plan(key='plan-' + field)
            ack = dbt.submit_run(self.engineer, self.context, key='run-' + field, plan_id=plan['id'])
            def tamper(record):
                if field == 'property':
                    record['models'][1]['table_properties']['format'] = "PARQUET'); DROP TABLE x"
                else:
                    record['compatibility']['executor'] = 'native'
            self.mutate(plan['id'], tamper)
            before = len(self.w.posts)
            dbt._execute(ack['id'])
            self.assertEqual(self.record(ack['id'])['error_code'], 'DBT_PLAN_CHANGED')
            self.assertEqual(len(self.w.posts), before)

    def test_property_helper_rejects_wrong_child_link_and_phase(self):
        original = dbt._table_properties
        called = []
        def check(execution, **kwargs):
            scope = ds._dbt.get()
            if not called and scope and scope[6] == 'build':
                called.append(True)
                with control_store.connect() as db:
                    db.execute("UPDATE dbt_run_operations SET node_id='model.second' WHERE operation_id=?", (execution.id,))
                self.code('DBT_CONTINUATION_REQUIRED', original, execution, **kwargs)
                with control_store.connect() as db:
                    db.execute('UPDATE dbt_run_operations SET node_id=? WHERE operation_id=?', (scope[7], execution.id))
                token = ds._dbt.set(scope[:6] + ('publish',) + scope[7:])
                try:
                    self.code('DBT_CONTINUATION_REQUIRED', original, execution, **kwargs)
                finally:
                    ds._dbt.reset(token)
            return original(execution, **kwargs)
        with patch.object(dbt, '_table_properties', side_effect=check):
            self.assertEqual(self.build_run()['state'], 'READY')
        self.assertEqual(called, [True])

    def test_ack_is_saved_before_start_and_key_replay_does_not_launch(self):
        def start(ident):
            with control_store.connect() as db:
                self.assertEqual(dbt._row(db, ident)[1]['state'], 'QUEUED')
                self.assertEqual(db.execute('SELECT count(*) FROM dbt_requests').fetchone()[0], 1)
        self.start.side_effect = start
        args = dict(key='same', project_id='lake_marts', source_schema='raw', target_schema='marts', staging_materialization='ephemeral')
        ack = dbt.submit_plan(self.engineer, self.context, **args)
        self.assertEqual(dbt.submit_plan(self.engineer, self.context, **args)['id'], ack['id'])
        self.assertEqual(dbt.get_plan(self.engineer, self.context, ack['id'])['state'], 'QUEUED')
        self.assertEqual(self.start.call_count, 1)
        self.compile.assert_not_called()
        args['target_schema'] = 'sandbox'
        self.code('IDEMPOTENCY_CONFLICT', dbt.submit_plan, self.engineer, self.context, **args)
        dbt._execute(ack['id'])
        self.assertEqual(dbt.get_plan(self.engineer, self.context, ack['id'])['state'], 'READY')

    def test_reads_reconcile_and_restart_never_launch_missing_sql_or_compile(self):
        plan = self.plan()
        ack = dbt.submit_run(self.engineer, self.context, key='lost', plan_id=plan['id'])
        dbt._live.pop(ack['id'])[-1].close()
        self.assertEqual(dbt.get_run(self.engineer, self.context, ack['id'])['state'], 'FAILED')
        self.assertEqual(dbt.reconcile_run(self.engineer, self.context, ack['id'], key='reconcile')['state'], 'FAILED')
        self.assertEqual(self.w.posts, [])
        args = dict(key='lost-compile', project_id='lake_marts', source_schema='raw', target_schema='marts', staging_materialization=None)
        ack = dbt.submit_plan(self.engineer, self.context, **args)
        dbt._live.pop(ack['id'])[-1].close()
        self.assertEqual(dbt.get_plan(self.engineer, self.context, ack['id'])['state'], 'FAILED')
        self.assertEqual(self.compile.call_count, 1)

    def test_identical_model_names_in_two_databases_keep_refs_separate(self):
        a = self.build_run()
        other = self.b.context(self.b.databases[1], schema='sandbox')
        b = self.build_run(context=other, key='other')
        self.assertEqual((a['state'], b['state']), ('READY', 'READY'), (a, b))
        for run, context in ((a, self.context), (b, other)):
            self.assertTrue(all(m['candidate']['ref']['database_id'] == context.database_id for m in run['models'] if m['candidate']))
        self.assertNotEqual(a['models'][1]['wap_model_id'], b['models'][1]['wap_model_id'])
        self.code('DBT_ARTIFACT_NOT_FOUND', dbt.get_run, self.engineer, other, a['id'])

    def test_source_pin_is_captured_once_and_preserved_across_models_tests(self):
        source = self.sources[self.context.database_id]
        first = str(self.w.metadata(source)['current-snapshot-id'])
        build = wap.build
        def append(*args, **kwargs):
            self.w.snapshot(self.w.metadata(source), [(99, 'late')])
            return build(*args, **kwargs)
        with patch.object(wap, 'build', side_effect=append):
            run = self.build_run()
        self.assertEqual(run['state'], 'READY', run)
        saved = self.record(run['id'])
        self.assertEqual(len(saved['_pins']), 1)
        self.assertEqual(next(iter(saved['_pins'].values()))['snapshot'], first)
        self.assertIn('FOR VERSION AS OF ' + first, self.w.posts[0][0])
        candidate = self.w.metadata(dm.ResourceRef(**run['models'][2]['candidate']['ref']))
        self.assertEqual(self.w.snapshots[(candidate['table-uuid'], candidate['current-snapshot-id'])], [(1, 'one'), (2, 'two')])
        self.assertEqual(self.record(saved['plan_id'])['source_digest'], 'copied-digest')

    def test_direct_manual_wap_routes_cannot_mutate_or_publish_managed_artifact(self):
        run = self.build_run()
        self.assertEqual(run['state'], 'READY', run)
        model = run['models'][1]
        count = len(self.w.posts)
        self.code('DBT_CONTINUATION_REQUIRED', wap.publish, self.admin, self.admin_context,
            key='bypass', build_id=model['build_id'], audit_id='forged')
        self.code('DBT_CONTINUATION_REQUIRED', wap.audit, self.admin, self.admin_context,
            key='bypass-audit', build_id=model['build_id'])
        self.code('DBT_CONTINUATION_REQUIRED', wap.build, self.engineer, self.context,
            key='bypass-build', model_id=model['wap_model_id'], version=model['wap_version'])
        saved = wap.get_model(self.engineer, self.context, model_id=model['wap_model_id'])
        self.code('DBT_CONTINUATION_REQUIRED', wap.save_model, self.engineer, self.context,
            model_id=model['wap_model_id'], sql=saved['sql'], sources={k: dm.ResourceRef(**v) for k, v in saved['sources'].items()},
            target=dm.DestinationLocator(**saved['target']), expected_version=1)
        self.assertEqual(len(self.w.posts), count)

    def test_failed_missing_and_forged_test_results_fail_closed(self):
        for change in ('missing', 'failed', 'forged'):
            with self.subTest(change=change):
                run = self.build_run(key='run-' + change)
                def corrupt(record):
                    if change == 'missing':
                        record['tests'].pop()
                    elif change == 'failed':
                        record['tests'][0].update(state='FAILED', failure_count=1)
                    else:
                        record['tests'][0]['operation_id'] = record['tests'][1]['operation_id']
                self.mutate(run['id'], corrupt)
                count = len(self.w.posts)
                self.code('DBT_TEST_GATE_FAILED', dbt.submit_publication, self.admin, self.admin_context,
                    run['id'], key='bad-' + change, node_id='model.first')
                self.assertEqual(len(self.w.posts), count)

    def test_actual_test_failure_preserves_target_and_blocks_old_wap_publish(self):
        self.w.snapshot(self.w.metadata(self.sources[self.context.database_id]), [(None, 'bad')])
        run = self.build_run()
        self.assertEqual(run['state'], 'FAILED', run)
        self.assertEqual([t['failure_count'] for t in run['tests']], [1, 1])
        count = len(self.w.posts)
        self.code('DBT_TEST_GATE_FAILED', dbt.submit_publication, self.admin, self.admin_context,
            run['id'], key='bad', node_id='model.first')
        self.code('DBT_CONTINUATION_REQUIRED', wap.publish, self.admin, self.admin_context,
            key='bad-old', build_id=run['models'][1]['build_id'], audit_id='forged')
        self.assertEqual(count, len(self.w.posts))

    def test_candidate_mutation_after_tests_prevents_publication(self):
        run = self.build_run()
        self.w.snapshot(self.w.metadata(dm.ResourceRef(**run['models'][1]['candidate']['ref'])), [(99, 'changed')])
        count = len(self.w.posts)
        publication = self.publish(run)
        self.assertEqual(publication['state'], 'FAILED', publication)
        self.assertEqual(count, len(self.w.posts))

    def test_target_creation_after_plan_fails_closed_without_adoption(self):
        plan = self.plan()
        self.w.existing('first', schema='marts')
        run = self.build_run(plan)
        self.assertEqual(run['state'], 'FAILED', run)
        self.assertEqual(len(self.w.posts), 0)

    def test_immutable_plan_tamper_rejected_before_run(self):
        plan = self.plan()
        self.mutate(plan['id'], lambda p: p['models'][1].update(compiled_sql='SELECT 99'))
        self.code('DBT_PLAN_NOT_READY', dbt.submit_run, self.engineer, self.context, key='tampered', plan_id=plan['id'])
        self.assertEqual(self.w.posts, [])

    def test_cancel_before_sql_and_after_ready_never_publishes(self):
        plan = self.plan()
        ack = dbt.submit_run(self.engineer, self.context, key='cancel-early', plan_id=plan['id'])
        canceled = dbt.cancel_run(self.engineer, self.context, ack['id'], key='cancel')
        self.assertTrue(canceled['cancel_requested'])
        dbt._execute(ack['id'])
        self.assertEqual(dbt.get_run(self.engineer, self.context, ack['id'])['state'], 'CANCELED')
        self.assertEqual(self.w.posts, [])
        ready = self.build_run(plan, key='ready')
        self.assertEqual(ready['state'], 'READY', ready)
        canceled = dbt.cancel_run(self.engineer, self.context, ready['id'], key='withdraw')
        self.assertEqual(canceled['state'], 'CANCELED')
        self.code('DBT_TEST_GATE_FAILED', dbt.submit_publication, self.admin, self.admin_context,
            ready['id'], key='after-cancel', node_id='model.first')

    def test_other_process_cancel_latch_at_final_send_stops_sql(self):
        plan = self.plan()
        ack = dbt.submit_run(self.engineer, self.context, key='send-cancel', plan_id=plan['id'])
        receive = qs._receive
        def latch(query_id, url, method, body=None):
            if method == 'POST':
                self.mutate(ack['id'], lambda r: r.update(cancel_requested=True, state='CANCELING'))
            return receive(query_id, url, method, body)
        with patch.object(qs, '_receive', side_effect=latch):
            dbt._execute(ack['id'])
        self.assertEqual(self.w.posts, [])
        self.assertEqual(dbt.get_run(self.engineer, self.context, ack['id'])['state'], 'CANCELED')

    def test_role_revocation_denies_replay_and_stops_new_sql(self):
        plan = self.plan()
        ack = dbt.submit_run(self.engineer, self.context, key='revoked', plan_id=plan['id'])
        with control_store.connect() as db:
            db.execute('DELETE FROM grants WHERE principal_id=? AND database_id=?', (self.engineer.principal_id, self.context.database_id))
        with self.assertRaises((QueryError, PermissionError)):
            dbt.submit_run(self.engineer, self.context, key='revoked', plan_id=plan['id'])
        dbt._execute(ack['id'])
        self.assertEqual(self.w.posts, [])
        self.assertEqual(self.record(ack['id'])['state'], 'FAILED')

    def test_interrupted_unknown_write_is_never_replayed(self):
        plan = self.plan()
        ack = dbt.submit_run(self.engineer, self.context, key='unknown', plan_id=plan['id'])
        with patch.object(qs, '_transport', side_effect=OSError('lost ACK')) as transport:
            dbt._execute(ack['id'])
            self.assertEqual(transport.call_count, 1)
            run = dbt.get_run(self.engineer, self.context, ack['id'])
            self.assertEqual(run['state'], 'UNKNOWN', run)
            self.assertEqual(dbt.submit_run(self.engineer, self.context, key='unknown', plan_id=plan['id'])['id'], run['id'])
            dbt.get_run(self.engineer, self.context, run['id'])
            self.assertEqual(transport.call_count, 1)
        self.code('DBT_TEST_GATE_FAILED', dbt.submit_publication, self.admin, self.admin_context,
            run['id'], key='unknown-publish', node_id='model.first')

    def test_postcommit_recording_failure_is_explicit_and_no_new_key_reissue(self):
        run = self.build_run()
        store = wap._store_artifact
        def fail(table, record):
            if table == 'wap_publications':
                raise OSError('history write')
            return store(table, record)
        with patch.object(wap, '_store_artifact', side_effect=fail):
            publication = self.publish(run)
        self.assertEqual(publication['state'], 'COMMITTED_RECORDING_FAILED', publication)
        self.assertTrue(publication['committed'])
        count = len(self.w.posts)
        self.assertEqual(dbt.get_publication(self.admin, self.admin_context, publication['id'])['state'], 'COMMITTED_RECORDING_FAILED')
        self.code('DBT_PUBLICATION_EXISTS', dbt.submit_publication, self.admin, self.admin_context,
            run['id'], key='again', node_id='model.first')
        self.assertEqual(len(self.w.posts), count)

    def test_stale_progress_record_cannot_clear_acknowledged_cancel(self):
        plan = self.plan()
        ack = dbt.submit_run(self.engineer, self.context, key='stale', plan_id=plan['id'])
        stale = self.record(ack['id'])
        dbt.cancel_run(self.engineer, self.context, ack['id'], key='latch')
        with control_store.connect() as db:
            stale.update(state='BUILDING', cancel_requested=False)
            dbt._write(db, stale)
        saved = self.record(ack['id'])
        self.assertTrue(saved['cancel_requested'])
        self.assertEqual(saved['state'], 'CANCELING')
        dbt._execute(ack['id'])
        self.assertEqual(self.record(ack['id'])['state'], 'CANCELED')
        self.assertEqual(self.w.posts, [])

    def test_existing_target_snapshot_drift_blocks_publish_sql(self):
        target = self.w.existing('first', schema='marts')
        run = self.build_run()
        self.assertEqual(run['state'], 'READY', run)
        self.w.snapshot(self.w.metadata(target), [(77, 'external')])
        count = len(self.w.posts)
        publication = self.publish(run)
        self.assertEqual(publication['state'], 'FAILED', publication)
        self.assertEqual(len(self.w.posts), count + 1)  # Audit only.
        self.assertFalse(any(sql.startswith('CREATE OR REPLACE TABLE') for sql, _ in self.w.posts))

    def test_run_requires_exact_saved_owner_role_and_context(self):
        plan = self.plan()
        self.code('CONTEXT_MISMATCH', dbt.submit_run, self.admin, self.admin_context,
            key='adopt-plan', plan_id=plan['id'])
        self.assertEqual(self.w.posts, [])

    def test_saved_admin_role_is_not_readable_by_same_actor_engineer(self):
        from grant_fixtures import verified_grants
        with control_store.connect() as db:
            dm.create_grant(db, self.admin.principal_id, self.context.database_id, 'data_engineer')
            verified_grants(db)
        args = dict(key='admin-plan', project_id='lake_marts', source_schema='raw',
            target_schema='marts', staging_materialization='ephemeral')
        ack = dbt.submit_plan(self.admin, self.admin_context, **args)
        dbt._execute(ack['id'])
        self.assertIn('compiled_sql', dbt.get_plan(self.admin, self.admin_context, ack['id'])['models'][0])
        engineer_context = self.b.context(schema='sandbox', principal=self.admin)
        self.code('DBT_ARTIFACT_NOT_FOUND', dbt.get_plan, self.admin, engineer_context, ack['id'])
        self.assertEqual(dbt.list_plans(self.admin, engineer_context), [])
        self.code('IDEMPOTENCY_CONFLICT', dbt.submit_plan, self.admin, engineer_context, **args)
        with control_store.connect() as db:
            receipt = db.execute('SELECT route,payload FROM dbt_requests WHERE idempotency_key=?',
                (args['key'],)).fetchone()
            self.code('DBT_ARTIFACT_NOT_FOUND', dbt._request, db, self.admin, engineer_context,
                receipt['route'], args['key'], receipt['payload'])
        engineer_plan = self.plan()
        self.assertEqual(dbt.get_plan(self.admin, self.admin_context, engineer_plan['id'])['id'], engineer_plan['id'])
        self.assertIn(engineer_plan['id'], [p['id'] for p in dbt.list_plans(self.admin, self.admin_context)])

    def _paged_test_cancellation(self, *, uncertain=False, revoke=False):
        plan = self.plan()
        ack = dbt.submit_run(self.engineer, self.context, key='paged', plan_id=plan['id'])
        transport, fetch = self.w.transport, qs.fetch
        sends, observed, receipts = [], {}, []
        next_uri = 'http://127.0.0.1:1234/v1/statement/paged_test/1'
        def paged(url, method, pin, body=None):
            sends.append((method, url, pin))
            if method == 'DELETE':
                self.assertEqual(url, next_uri)
                self.assertIs(pin, observed['pin'])
                self.assertEqual(pin.headers, observed['headers'])
                if uncertain:
                    raise OSError('cancel acknowledgment lost')
                return {}
            if method == 'GET':
                self.fail('cancellation fence must prevent the next GET')
            if body.decode().startswith('SELECT COUNT(*)'):
                self.w.posts.append((body.decode(), dict(pin.headers)))
                observed.update(pin=pin, headers=dict(pin.headers))
                return dict(id='paged_test', columns=[dict(name='count', type='bigint')],
                    data=[], nextUri=next_uri)
            return transport(url, method, pin, body)
        def latch(principal, query_id, page_token, *, selected_role):
            observed['query_id'] = query_id
            if revoke:
                with control_store.connect() as db:
                    db.execute('DELETE FROM grants WHERE principal_id=? AND database_id=?',
                        (self.engineer.principal_id, self.context.database_id))
                self.code('CONTEXT_MISMATCH', dbt.cancel_run, self.admin, self.admin_context,
                    ack['id'], key='wrong-owner')
                self.code('CONTEXT_MISMATCH', dbt.cancel_run, self.engineer,
                    replace(self.context, selected_role='admin'), ack['id'], key='wrong-role')
                self.code('DB_ACCESS_REVOKED', dbt.cancel_run, ('alice', 'data_engineer'), self.context,
                    ack['id'], key='not-authenticated')
                with patch.object(ds.request_auth, '_principal_accounts', return_value={}):
                    self.code('DB_ACCESS_REVOKED', dbt.cancel_run, self.engineer, self.context,
                        ack['id'], key='removed-account')
                saved = self.record(ack['id'])
                self.assertFalse(saved['cancel_requested'])
                for _ in range(2):
                    receipts.append(dbt.cancel_run(self.engineer, self.context, ack['id'], key='revoked-cancel'))
                for receipt in receipts:
                    self.assertTrue(receipt['cancel_requested'])
                    for field in ('models', 'tests', 'operations', 'operation_ids'):
                        self.assertEqual(receipt[field], [])
                self.assertEqual(self.record(ack['id'])['_pins'], saved['_pins'])
                self.assertEqual(self.record(ack['id'])['context'], saved['context'])
                self.code('DB_ACCESS_REVOKED', dbt.get_run, self.engineer, self.context, ack['id'])
                self.code('DB_ACCESS_REVOKED', dbt.list_runs, self.engineer, self.context)
            else:
                # A canceling server has no access to the accepting owner's pins.
                with patch.object(qs, '_pins', {}):
                    receipt = dbt.cancel_run(self.engineer, self.context, ack['id'], key='other-process-cancel')
                self.assertTrue(receipt['cancel_requested'])
            return fetch(principal, query_id, page_token, selected_role=selected_role)
        with patch.object(qs, '_transport', side_effect=paged), patch.object(qs, 'fetch', side_effect=latch):
            dbt._execute(ack['id'])
        self.assertIn('query_id', observed)
        self.assertEqual([s[0] for s in sends], ['POST', 'POST', 'POST', 'DELETE'])
        run = self.record(ack['id'])
        self.assertTrue(run['cancel_requested'])
        self.assertEqual(run['state'], 'UNKNOWN' if uncertain else 'CANCELED', run)
        with control_store.connect() as db:
            query = db.execute('SELECT * FROM query_records WHERE id=?', (observed['query_id'],)).fetchone()
            child = db.execute('SELECT o.* FROM dataops_queries q JOIN dataops_operations o ON o.id=q.operation_id '
                'WHERE q.query_id=?', (observed['query_id'],)).fetchone()
            self.assertEqual(query['state'], 'UNKNOWN' if uncertain else 'CANCELED')
            self.assertEqual(child['state'], 'UNKNOWN' if uncertain else 'CANCELED')
            parent = admission.get_for_subject(db, kind='dbt', subject_id=ack['id'], warehouse_id='w')
            self.assertEqual(admission.check_execution(db, parent)['state'], 'UNKNOWN' if uncertain else 'FINISHED')

    def test_other_process_cancel_during_paged_test_cancels_original_pin(self):
        self._paged_test_cancellation()

    def test_other_process_cancel_during_paged_test_keeps_uncertain_delete_unknown(self):
        self._paged_test_cancellation(uncertain=True)

    def test_revoked_grant_owner_can_cancel_accepted_run_without_metadata_access(self):
        self._paged_test_cancellation(revoke=True)

    def test_child_links_and_admissions_are_visible_before_every_send(self):
        transport = self.w.transport
        def linked(url, method, pin, body=None):
            if method == 'POST':
                with control_store.connect() as db:
                    row = db.execute('SELECT l.*,o.actor,a.parent_id FROM query_execution_contexts e '
                        'JOIN dataops_queries q ON q.query_id=e.query_id JOIN dbt_run_operations l ON l.operation_id=q.operation_id '
                        "JOIN dataops_operations o ON o.id=l.operation_id JOIN warehouse_admissions a ON a.kind='dataops' AND a.subject_id=o.id "
                        'WHERE e.sql=?', (body.decode(),)).fetchone()
                    self.assertIsNotNone(row)
                    parent, saved = dbt._row(db, row['run_id'])
                    self.assertEqual(row['parent_id'], parent['admission_id'])
                    self.assertEqual(row['actor'], saved['actor'])
            return transport(url, method, pin, body)
        with patch.object(qs, '_transport', side_effect=linked):
            run = self.build_run()
            self.assertEqual(run['state'], 'READY', run)
            self.assertEqual(self.publish(run)['state'], 'PUBLISHED')

    def test_real_async_ack_does_not_read_worker_after_launch(self):
        entered, release = threading.Event(), threading.Event()
        original_write = dbt._write
        # _start is mocked by setUp; use a real isolated worker here.
        workers = []
        def hold(db, record):
            original_write(db, record)
            if record['state'] == 'COMPILING':
                entered.set()
                if not release.wait(10):
                    raise AssertionError('worker not released')
        def start(ident):
            worker = threading.Thread(target=dbt._execute, args=(ident,))
            workers.append(worker)
            worker.start()
            self.assertTrue(entered.wait(5))
        with patch.object(dbt, '_write', side_effect=hold):
            self.start.side_effect = start
            try:
                ack = dbt.submit_plan(self.engineer, self.context, key='real-thread', project_id='lake_marts',
                    source_schema='raw', target_schema='marts', staging_materialization='ephemeral')
                self.assertEqual(ack['state'], 'QUEUED')
                self.assertFalse(release.is_set())
            finally:
                release.set()
                for worker in workers:
                    worker.join(10)
                    self.assertFalse(worker.is_alive())
        self.assertEqual(dbt.get_plan(self.engineer, self.context, ack['id'])['state'], 'READY')

    def test_owner_loss_after_definite_child_does_not_resume_missing_steps(self):
        plan = self.plan()
        ack = dbt.submit_run(self.engineer, self.context, key='after-child', plan_id=plan['id'])
        build = wap.build
        def lose_after_child(*args, **kwargs):
            child = build(*args, **kwargs)
            self.assertEqual(child['state'], 'FINISHED')
            raise RuntimeError('owner stopped before recording model')
        with patch.object(wap, 'build', side_effect=lose_after_child):
            dbt._execute(ack['id'])
        run = dbt.get_run(self.engineer, self.context, ack['id'])
        self.assertEqual(run['state'], 'FAILED', run)
        self.assertEqual(len(self.w.posts), 1)
        self.assertTrue(run['operations'][0]['committed'])
        dbt.reconcile_run(self.engineer, self.context, run['id'], key='reconcile-child')
        self.assertEqual(len(self.w.posts), 1)
        with control_store.connect() as db:
            parent = admission.get_for_subject(db, kind='dbt', subject_id=run['id'], warehouse_id='w')
            self.assertEqual(admission.check_execution(db, parent)['state'], 'FINISHED')

    def test_reconcile_orphan_running_query_observes_without_sql_or_replay(self):
        plan = self.plan()
        ack = dbt.submit_run(self.engineer, self.context, key='orphan', plan_id=plan['id'])
        with patch.object(qs, '_transport', side_effect=OSError('lost ACK')):
            dbt._execute(ack['id'])
        with control_store.connect() as db:
            query_id = db.execute('SELECT d.query_id FROM dbt_run_operations l JOIN dataops_queries d '
                'ON d.operation_id=l.operation_id WHERE l.run_id=?', (ack['id'],)).fetchone()[0]
            # Emulate a process that exited while its last durable query was RUNNING.
            db.execute("UPDATE query_records SET state='RUNNING' WHERE id=?", (query_id,))
        qs._close_query_owner(qs._pins.pop(query_id, None))
        observed = []
        def backend(expected):
            observed.append(expected)
            return [dict(id='known_failure', source=expected['source'], user=expected['backend_username'],
                sql=qs._backend_statement(expected['sql']), state='FAILED')]
        with patch.object(qs, 'observe_query_backend', side_effect=backend), patch.object(qs, '_transport') as transport:
            run = dbt.reconcile_run(self.engineer, self.context, ack['id'], key='reconcile-running')
            self.assertEqual(run['state'], 'FAILED', run)
            transport.assert_not_called()
        self.assertEqual(len(observed), 1)
        self.assertEqual(self.w.posts, [])

    def test_admin_reconciles_own_unknown_publication_without_adopting_engineer_queries(self):
        run = self.build_run()
        ack = dbt.submit_publication(self.admin, self.admin_context, run['id'], key='lost-publication', node_id='model.first')
        transport = self.w.transport
        def lost_publish(url, method, pin, body=None):
            if body and body.decode().startswith('CREATE TABLE'):
                raise OSError('publish ACK lost')
            return transport(url, method, pin, body)
        with patch.object(qs, '_transport', side_effect=lost_publish):
            dbt._execute(ack['id'])
        self.assertEqual(dbt.get_publication(self.admin, self.admin_context, ack['id'])['state'], 'UNKNOWN')
        observed = []
        def backend(expected):
            observed.append(expected)
            return [dict(id='publication_commit', source=expected['source'], user=expected['backend_username'],
                sql=qs._backend_statement(expected['sql']), state='FINISHED')]
        with patch.object(qs, 'observe_query_backend', side_effect=backend), patch.object(qs, '_transport') as sql:
            dbt.reconcile_run(self.admin, self.admin_context, run['id'], key='reconcile-publication')
            sql.assert_not_called()
        publication = dbt.get_publication(self.admin, self.admin_context, ack['id'])
        self.assertEqual(publication['state'], 'COMMITTED_RECORDING_FAILED', publication)
        self.assertTrue(publication['committed'])
        self.assertEqual(len(observed), 1)
        self.code('DBT_PUBLICATION_EXISTS', dbt.submit_publication, self.admin, self.admin_context,
            run['id'], key='reissue', node_id='model.first')

    def test_final_send_rechecks_revoked_grant(self):
        # A second server changes authority/identity after wake and before POST.
        plan = self.plan()
        ack = dbt.submit_run(self.engineer, self.context, key='final-grant', plan_id=plan['id'])
        receive = qs._receive
        def revoke(query_id, url, method, body=None):
            with control_store.connect() as db:
                db.execute('DELETE FROM grants WHERE principal_id=? AND database_id=?',
                    (self.engineer.principal_id, self.context.database_id))
            return receive(query_id, url, method, body)
        with patch.object(qs, '_receive', side_effect=revoke):
            dbt._execute(ack['id'])
        self.assertEqual(self.w.posts, [])
        self.assertEqual(self.record(ack['id'])['state'], 'FAILED')

    def test_cancel_with_pending_admin_publication_latches_both_owners(self):
        run = self.build_run()
        publication = dbt.submit_publication(self.admin, self.admin_context, run['id'], key='pending', node_id='model.first')
        count = len(self.w.posts)
        dbt.cancel_run(self.engineer, self.context, run['id'], key='cancel-both')
        self.assertTrue(self.record(publication['id'])['cancel_requested'])
        dbt._execute(publication['id'])
        self.assertEqual(dbt.get_publication(self.admin, self.admin_context, publication['id'])['state'], 'CANCELED')
        self.assertEqual(len(self.w.posts), count)

    def test_final_send_rechecks_source_ref_incarnation(self):
        plan = self.plan()
        ack = dbt.submit_run(self.engineer, self.context, key='final-source', plan_id=plan['id'])
        receive = qs._receive
        def recreate(query_id, url, method, body=None):
            with control_store.connect() as db:
                db.execute('UPDATE object_bindings SET current=0 WHERE id=?',
                    (self.sources[self.context.database_id].object_id,))
            return receive(query_id, url, method, body)
        with patch.object(qs, '_receive', side_effect=recreate):
            dbt._execute(ack['id'])
        self.assertEqual(self.w.posts, [])
        self.assertEqual(self.record(ack['id'])['state'], 'FAILED')

    def test_plan_resolves_existing_target_without_prior_catalog_registration(self):
        target = self.w.existing('first', schema='marts')
        with control_store.connect() as db:
            db.execute('DELETE FROM object_bindings WHERE id=?', (target.object_id,))
        plan = self.plan()
        saved = self.record(plan['id'])['_targets']['model.first']
        self.assertIn('object_id', saved)
        self.assertEqual(saved['schema_id'], target.schema_id)
        run = self.build_run(plan)
        self.assertEqual(run['state'], 'READY', run)
        self.assertEqual(self.publish(run)['state'], 'PUBLISHED')

    def test_project_trust_sandbox_and_current_viewer_restrictions(self):
        listing = dbt.list_projects(self.engineer, self.context)
        self.assertEqual(listing[0]['id'], 'lake_marts')
        self.assertNotIn('path', listing[0])
        self.code('DBT_PROJECT_NOT_FOUND', dbt.submit_plan, self.engineer, self.context,
            key='path', project_id='/tmp/arbitrary', source_schema='raw', target_schema='marts', staging_materialization=None)
        self.code('SANDBOX_REQUIRED', dbt.list_projects, self.engineer, self.b.context())
        viewer_principal = self.engineer
        viewer = self.b.context(schema='marts', principal=viewer_principal, role='bi_viewer')
        self.code('SQL_PERMISSION_DENIED', dbt.list_projects, viewer_principal, viewer)
        self.code('SQL_PERMISSION_DENIED', dbt.submit_publication, self.engineer, self.context,
            'not-owned', key='engineer', node_id='model.first')


if __name__ == '__main__':
    unittest.main()
