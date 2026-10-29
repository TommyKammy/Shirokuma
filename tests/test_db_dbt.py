"""Durable dbt workflow using real WAP/admission and the existing SQL harness."""
from dataclasses import asdict, replace
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
                depends_on=depends, materialized=materialized)
        return dict(source_digest='copied-digest', versions={}, sources=[dict(unique_id='source.rows', name='rows', relation=source)],
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
