"""Explicit TABLE publication from installed RunTask/TestTask graph evidence."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
import unittest
import uuid
from unittest.mock import patch

import test_database_dbt_graph as graph_fixtures
import test_db_dbt as publication_fixtures
import database_dbt as compiler
import database_dbt_graph as graph
import database_dbt_native as native
import control_store
import data_management as dm
import dataops_service as ds
import db_dbt as dbt
import query_service as qs
from query_context import QueryError


class GraphPublicationTests(unittest.TestCase):
    # Compose helpers only; imported fixture TestCases are never discovered here.
    clear_live = graph_fixtures.GraphTests.clear_live
    mutate_plan = graph_fixtures.GraphTests.mutate_plan
    record = graph_fixtures.GraphTests.record
    mutate = graph_fixtures.GraphTests.mutate
    execute = graph_fixtures.GraphTests.execute
    transport = graph_fixtures.GraphTests.transport
    code = publication_fixtures.DbtTests.code
    publisher = publication_fixtures.NativePublicationTests.publisher
    preview = publication_fixtures.NativePublicationTests.preview
    accept = publication_fixtures.NativePublicationTests.accept
    finish = publication_fixtures.NativePublicationTests.finish

    def setUp(self):
        graph_fixtures.GraphTests.setUp(self)
        self.mutate_plan(lambda p: p['models'].sort(key=lambda m: m['materialized'] != 'ephemeral'))
        self.mutate_plan(lambda p: p.update(_plan_digest=dbt._hash_plan(p)))

    def candidate(self):
        self.mutate_plan(lambda p: p['models'].sort(key=lambda m: m['materialized'] != 'ephemeral'))
        self.mutate_plan(lambda p: p.update(_plan_digest=dbt._hash_plan(p)))
        run = self.execute(graph_fixtures.GraphTests.accept(self, tests=True))
        self.assertEqual(run['state'], 'NATIVE_VERIFIED', run)
        self.assertTrue(run['publication_allowed'], run)
        self.assertEqual(run['models'][0]['materialized'], 'ephemeral')
        # Compatibility helper for shared candidate drift tests.
        run['native_receipt'] = next(m['native_receipt'] for m in run['models'] if m['unique_id'] == self.uid)
        return run

    test_existing_target_review_alias_head_and_uuid_compare_then_replace = (
        publication_fixtures.NativePublicationTests.test_existing_target_review_alias_head_and_uuid_compare_then_replace)
    test_native_publish_poll_can_observe_its_own_changed_target = (
        publication_fixtures.NativePublicationTests.test_native_publish_poll_can_observe_its_own_changed_target)
    test_locator_never_adopts_new_table_or_view = publication_fixtures.NativePublicationTests.test_locator_never_adopts_new_table_or_view
    test_publish_ack_loss_is_unknown_and_same_key_does_not_send_again = publication_fixtures.NativePublicationTests.test_publish_ack_loss_is_unknown_and_same_key_does_not_send_again
    test_known_commit_measure_failure_is_recording_failed = publication_fixtures.NativePublicationTests.test_known_commit_measure_failure_is_recording_failed
    test_final_send_after_wake_rechecks_target_before_any_sql = publication_fixtures.NativePublicationTests.test_final_send_after_wake_rechecks_target_before_any_sql
    test_publisher_or_source_privilege_loss_denies_publication_before_sql = publication_fixtures.NativePublicationTests.test_publisher_or_source_privilege_loss_denies_publication_before_sql
    test_owner_loss_before_audit_is_observation_only_and_reservation_persists = publication_fixtures.NativePublicationTests.test_owner_loss_before_audit_is_observation_only_and_reservation_persists

    def test_minimum_rows_and_malformed_audit_keep_reservations_without_publish(self):
        run = self.candidate()
        transport = self.w.transport
        for index, count in enumerate((0, 'invalid')):
            if index:
                self.uid = 'model.general_native.final_result'
            expected = self.preview(run)['expected_target']
            ack = self.accept(run, expected, key='audit-' + str(index))
            def measured(url, method, pin, body=None):
                page = transport(url, method, pin, body)
                if body and body.decode().startswith('SELECT COUNT(*) FROM '):
                    page['data'] = [[count]]
                return page
            before = len(self.w.posts)
            with patch.object(qs, '_transport', side_effect=measured):
                result = self.finish(ack)
            self.assertEqual(result['state'], 'FAILED', result)
            self.assertEqual(result['error_code'], 'DBT_AUDIT_FAILED' if index == 0 else 'INVALID_AUDIT_RESULT')
            self.assertEqual(len(self.w.posts), before + 1)
            self.assertEqual([op['phase'] for op in result['operations']], ['audit'])
            if index == 0:
                self.assertIs(result['native_audit']['passed'], False)
                self.assertEqual(result['native_audit']['count'], 0)
            else:
                self.assertIsNone(result.get('native_audit'))
            self.assertEqual(self.accept(run, expected, key='audit-' + str(index))['id'], ack['id'])
            self.code('DBT_PUBLICATION_EXISTS', self.accept, run, expected, key='new-' + str(index))

    def test_two_explicit_tables_credentials_origins_replay_and_historical_evidence(self):
        run = self.candidate()
        uid = self.uid
        original = self.record(run['id'])
        before = len(self.w.posts)
        with control_store.connect() as db:
            counts = [db.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in
                ('dbt_runs', 'dataops_operations', 'warehouse_admissions', 'dbt_requests', 'dataops_target_claims')]
        with (patch.object(compiler, '_compiled_project', side_effect=AssertionError('compiler')),
                patch.object(graph, '_run_subprocess', side_effect=AssertionError('graph SDK')),
                patch.object(native, '_run_subprocess', side_effect=AssertionError('SDK')),
                patch.object(qs, '_capture_dataops', side_effect=AssertionError('capture')),
                patch.object(qs, '_transport', side_effect=AssertionError('SQL'))):
            preview = self.preview(run)
            self.assertEqual(self.preview(run), preview)
        with control_store.connect() as db:
            self.assertEqual(counts, [db.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in
                ('dbt_runs', 'dataops_operations', 'warehouse_admissions', 'dbt_requests', 'dataops_target_claims')])
        self.assertEqual(preview['origin'], 'native_graph')
        self.assertEqual(preview['candidate'], run['native_receipt']['candidate'])
        self.b.credential = 'Basic graph-publisher'
        expected = preview['expected_target']
        first = self.accept(run, expected)
        self.b.credential = 'Basic rotated'
        with (patch.object(compiler, '_compiled_project', side_effect=AssertionError('compiler')),
                patch.object(graph, '_run_subprocess', side_effect=AssertionError('SDK'))):
            published = self.finish(first)
        self.assertEqual(published['state'], 'PUBLISHED', published)
        self.assertNotIn('executor', self.record(first['id']))
        self.assertEqual(published['native_audit']['origin'], 'native_graph')
        self.assertEqual(published['native_publication']['origin'], 'native_graph')
        self.assertEqual(len(self.w.posts), before + 2)
        for sql, headers in self.w.posts[before:]:
            self.assertEqual(headers['Authorization'], 'Basic graph-publisher')
            self.assertIn('FOR VERSION AS OF ' + preview['candidate']['snapshot'], sql)
        with control_store.connect() as db:
            children = list(db.execute('SELECT o.* FROM dataops_operations o JOIN dbt_run_operations l ON l.operation_id=o.id WHERE l.run_id=?', (first['id'],)))
            source_op = db.execute('SELECT * FROM dataops_operations WHERE id=?', (original['models'][1]['operation_ids'][0],)).fetchone()
            self.assertEqual({c['actor'] for c in children}, {self.w.owner.principal_id})
            self.assertTrue(all(c['credential_snapshot_ref'] != source_op['credential_snapshot_ref'] for c in children))
            for table in ('wap_models', 'wap_builds', 'wap_audits', 'wap_publications'):
                self.assertEqual(db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)
        with control_store.connect() as db:
            forged = self.record(first['id'])
            forged['_native_intent']['tables'][0]['node_id'] = 'missing-table'
            self.assertIsNone(dbt._native_publication_saved(db, forged))
            forged = self.record(first['id'])
            forged['origin'] = 'sql_wap'
            self.assertIsNone(dbt._native_audit_saved(db, forged))
            self.code('DBT_NATIVE_PUBLICATION_INVALID', dbt._native_publish_intent, forged, 'operation')
        self.assertTrue(dbt.get_run(self.principal, self.context, run['id'])['publication_allowed'])
        self.uid = 'model.general_native.final_result'
        second = self.accept(run, self.preview(run)['expected_target'], key='second')
        second_model = next(m for m in original['models'] if m['unique_id'] == self.uid)
        second_intent = self.record(second['id'])['_native_intent']
        self.assertEqual(second_intent['build_operation_id'], second_model['operation_ids'][0])
        self.assertEqual(second_intent['candidate'], second_model['candidate'])
        self.assertEqual(self.finish(second)['state'], 'PUBLISHED')
        self.uid = uid
        count = len(self.w.posts)
        self.assertEqual(self.accept(run, expected)['id'], first['id'])
        self.assertEqual(len(self.w.posts), count)
        # Retire every candidate and advance target, then observe source first.
        for model in original['models']:
            if model['materialized'] == 'table':
                ref = dm.ResourceRef(**model['candidate']['ref'])
                with control_store.connect() as db:
                    obj = dm.get_object(db, ref.object_id)
                    dm.observe_object(db, ref.schema_id, obj.physical_name, 'TABLE', str(uuid.uuid4()), obj.display_name)
        self.w.snapshot(self.w.metadata(dm.ResourceRef(**published['native_publication']['target']['ref'])), [(9, 'later')])
        self.mutate(run['id'], lambda r: r.update(cancel_requested=True))
        self.assertFalse(dbt.get_run(self.principal, self.context, run['id'])['publication_allowed'])
        dbt._settle(first['id'])
        self.assertEqual(dbt.get_publication(*self.publisher(), first['id'])['state'], 'PUBLISHED')
        self.assertEqual(self.accept(run, expected)['id'], first['id'])
        self.assertEqual(len(self.w.posts), count)

    def test_historical_missing_graph_table_rejects_proof_without_sql_replay(self):
        run = self.candidate()
        expected = self.preview(run)['expected_target']
        ack = self.accept(run, expected)
        published = self.finish(ack)
        self.assertEqual(published['state'], 'PUBLISHED', published)
        source = self.record(run['id'])
        publication = self.record(ack['id'])
        node_id = publication['_native_intent']['tables'][0]['node_id']
        before = len(self.w.posts)
        for missing in ('unknown_frozen_node', 'removed_source_table'):
            with self.subTest(missing=missing):
                if missing == 'unknown_frozen_node':
                    self.mutate(ack['id'], lambda r: r['_native_intent']['tables'][0].update(node_id='missing-table'))
                else:
                    self.mutate(run['id'], lambda r: r.update(models=[
                        m for m in r['models'] if m['unique_id'] != node_id]))
                # The frozen lineage lookup must reject before the global graph
                # gate or child payload checks could reject this same corruption.
                with (patch.object(graph, 'publication_proof', side_effect=AssertionError('later graph gate')) as gate,
                        patch.object(qs, '_transport', side_effect=AssertionError('SQL replay'))):
                    with control_store.connect() as db:
                        self.assertIsNone(dbt._native_publication_saved(db, self.record(ack['id'])))
                    dbt._settle(ack['id'])
                    observed = dbt.get_publication(*self.publisher(), ack['id'])
                    self.assertEqual(observed['state'], 'COMMITTED_RECORDING_FAILED', observed)
                    self.assertEqual(self.accept(run, expected)['id'], ack['id'])
                    gate.assert_not_called()
                self.assertEqual(len(self.w.posts), before)
                with control_store.connect() as db:
                    dbt._write(db, deepcopy(source))
                    dbt._write(db, deepcopy(publication))
        with control_store.connect() as db:
            self.assertEqual(dbt._native_publication_saved(db, self.record(ack['id'])),
                publication['native_publication'])

    def test_whole_saved_gate_rejects_incomplete_failed_or_tampered_graphs(self):
        run = self.candidate()
        saved = self.record(run['id'])
        changes = [lambda r: r.update(native_tests=False), lambda r: r.update(tests_status='NOT_RUN'),
            lambda r: r.update(state='UNKNOWN'), lambda r: r.update(cancel_requested=True),
            lambda r: r.update(_graph_sdk_closed=False), lambda r: r['tests'].pop(),
            lambda r: r['tests'][-1].update(failure_count=True), lambda r: r['tests'][-1].update(failure_count=1),
            lambda r: r['tests'][-1].update(state='FAILED'),
            lambda r: r['task_receipts']['test'].update(success=False),
            lambda r: r['task_receipts']['build'].update(task='ModelRunner'),
            lambda r: r['models'][-1].update(state='FAILED'),
            lambda r: r['models'][-1]['native_receipt'].update(native_verified=False),
            lambda r: r['tests'][-1]['native_receipt'].update(query_id='forged'),
            lambda r: r['_pins'].clear(),
            lambda r: r['models'][-1]['dependency_snapshots'].clear(),
            lambda r: r['task_receipts']['test']['nodes'][0].update(status='fail')]
        for change in changes:
            with self.subTest(change=change):
                self.mutate(run['id'], change)
                self.code('DBT_GRAPH_PUBLICATION_FORBIDDEN', self.preview, run)
                with control_store.connect() as db:
                    self.assertFalse(dbt._public(db, dbt._row(db, run['id'])[1])['publication_allowed'])
                    dbt._write(db, deepcopy(saved))
        self.uid = saved['models'][0]['unique_id']
        self.code('DBT_GRAPH_PUBLICATION_FORBIDDEN', self.preview, run)

    def test_all_candidate_heads_preacceptance_and_after_wake_are_global(self):
        run = self.candidate()
        expected = self.preview(run)['expected_target']
        unrelated = next(m for m in run['models'] if m['materialized'] == 'table' and m['unique_id'] != self.uid)
        metadata = self.w.metadata(dm.ResourceRef(**unrelated['candidate']['ref']))
        old = deepcopy(metadata)
        self.w.snapshot(metadata, [(8, 'advanced')])
        self.code('STAGE_CHANGED', self.preview, run)
        self.code('STAGE_CHANGED', self.accept, run, expected)
        metadata.clear()
        metadata.update(old)
        ack = self.accept(run, expected)
        wake = qs._ensure_query_awake
        def drift(principal, query_id):
            result = wake(principal, query_id)
            with control_store.connect() as db:
                step = db.execute('SELECT step FROM dataops_queries WHERE query_id=?', (query_id,)).fetchone()[0]
            if step == 'publish':
                self.w.snapshot(metadata, [(7, 'late')])
            return result
        before = len(self.w.posts)
        with patch.object(qs, '_ensure_query_awake', side_effect=drift):
            result = self.finish(ack)
        self.assertEqual(result['state'], 'FAILED', result)
        self.assertEqual(result['error_code'], 'STAGE_CHANGED')
        self.assertTrue(result['native_audit']['passed'])
        self.assertEqual(len(self.w.posts), before + 1)

    def test_installed_no_tests_and_failed_source_quality_never_authorize_a_table(self):
        no_tests = self.execute(graph_fixtures.GraphTests.accept(self, key='no-tests'))
        self.assertEqual(no_tests['state'], 'NATIVE_VERIFIED', no_tests)
        self.assertEqual(no_tests['tests_status'], 'NOT_RUN')
        self.assertFalse(no_tests['publication_allowed'])
        self.code('DBT_GRAPH_PUBLICATION_FORBIDDEN', self.preview, no_tests)
        # This source-level test fails while the selected TABLE filters null ids.
        self.w.snapshot(self.w.metadata(self.source), [(None, 'bad'), (1, 'good')])
        failed = self.execute(graph_fixtures.GraphTests.accept(self, key='failed-quality', tests=True))
        self.assertEqual(failed['tests_status'], 'FAILED', failed)
        selected = next(m for m in failed['models'] if m['unique_id'] == self.uid)
        self.assertEqual(selected['state'], 'NATIVE_VERIFIED')
        self.assertFalse(failed['publication_allowed'])
        with control_store.connect() as db:
            saved = self.record(failed['id'])
            self.assertTrue(graph.task_proof(db, saved, 'test'))
            self.assertIs(saved['task_receipts']['test']['success'], False)
        self.code('DBT_GRAPH_PUBLICATION_FORBIDDEN', self.preview, failed)

    def test_acceptance_race_rechecks_unselected_candidate_without_reserving(self):
        run = self.candidate()
        expected = self.preview(run)['expected_target']
        other = next(m for m in run['models'] if m['materialized'] == 'table' and m['unique_id'] != self.uid)
        accept = dbt._accept
        def race(*args, **kwargs):
            self.w.snapshot(self.w.metadata(dm.ResourceRef(**other['candidate']['ref'])), [(8, 'race')])
            return accept(*args, **kwargs)
        before = len(self.w.posts)
        with patch.object(dbt, '_accept', side_effect=race):
            self.code('STAGE_CHANGED', self.accept, run, expected)
        self.assertEqual(len(self.w.posts), before)
        selected = next(m for m in self.record(run['id'])['models'] if m['unique_id'] == self.uid)
        self.assertNotIn('publication_id', selected)

    def test_role_database_context_and_cancellation_boundaries(self):
        run = self.candidate()
        expected = self.preview(run)['expected_target']
        self.code('SQL_PERMISSION_DENIED', dbt.get_publication_preview, self.principal, self.context, run['id'], node_id=self.uid)
        self.code('SQL_PERMISSION_DENIED', dbt.submit_publication, self.principal, self.context, run['id'], key='engineer', node_id=self.uid, expected_target=expected)
        viewer = self.b.context(schema='marts', principal=self.principal, role='bi_viewer')
        self.code('SQL_PERMISSION_DENIED', dbt.get_publication_preview, self.principal, viewer,
            run['id'], node_id=self.uid)
        owner, context = self.publisher()
        other = self.b.context(self.b.databases[1], schema='sandbox', principal=owner, role='admin')
        self.code('DBT_ARTIFACT_NOT_FOUND', dbt.get_publication_preview, owner, other, run['id'], node_id=self.uid)
        for field in ('generation_id', 'binding_revision'):
            with self.subTest(field=field), self.assertRaises(QueryError):
                dbt.get_publication_preview(owner, replace(context, **{field: 999}), run['id'], node_id=self.uid)
        ack = self.accept(run, expected)
        self.mutate(ack['id'], lambda r: r.update(cancel_requested=True))
        before = len(self.w.posts)
        result = self.finish(ack)
        self.assertEqual(result['state'], 'CANCELED', result)
        self.assertEqual(len(self.w.posts), before)
        self.assertEqual(self.accept(run, expected)['id'], ack['id'])


if __name__ == '__main__':
    unittest.main()
