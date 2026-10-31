"""Real installed RunTask VIEW materialization and full saved TestTask suite."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
import uuid
from unittest.mock import patch

import yaml
import test_database_dbt_graph as fixtures
import test_database_dbt_native as native_fixtures
import database_dbt as compiler
import database_dbt_graph as graph
import database_dbt_native as native
import control_store
import data_management as registry
import dataops_service as ds
import db_dbt as dbt
import query_service as queries
from query_context import QueryError


class ViewGraphTests(unittest.TestCase):
    clear_live = fixtures.GraphTests.clear_live
    mutate_plan = fixtures.GraphTests.mutate_plan
    record = fixtures.GraphTests.record
    mutate = fixtures.GraphTests.mutate
    accept = fixtures.GraphTests.accept
    transport = fixtures.GraphTests.transport
    execute = fixtures.GraphTests.execute

    def setUp(self):
        native_fixtures.NativeTests.setUp(self)
        self.artifact_patch.stop()
        # Omit view_security deliberately: saved omission is INVOKER although
        # the installed dbt-trino macro defaults to DEFINER.
        (self.project / 'models/arbitrary_result.sql').write_text(
            "{{ config(materialized='view', properties={}) }} SELECT id, label FROM {{ ref('prepared_rows') }} WHERE id > 0")
        (self.project / 'models/final_result.sql').write_text(
            "SELECT id, label FROM {{ ref('arbitrary_result') }}")
        (self.project / 'models/quality.yml').write_text(yaml.safe_dump(dict(version=2,
            models=[dict(name='arbitrary_result', columns=[dict(name='id', data_tests=['not_null'])]),
                dict(name='final_result', columns=[dict(name='id', data_tests=[dict(relationships=dict(
                    arguments=dict(to="ref('arbitrary_result')", field='id')))])]) ])))
        source_path = self.project / 'models/sources.yml'
        source = yaml.safe_load(source_path.read_text())
        source['sources'][0]['tables'][0]['columns'] = [dict(name='id', data_tests=['not_null'])]
        source_path.write_text(yaml.safe_dump(source))
        self.save_plan()

    def save_plan(self):
        compiled = compiler._compiled_project(self.project, catalog=self.context.sql_catalog, source_schema='raw')
        self.artifacts = compiled.__enter__()
        self.addCleanup(compiled.__exit__, None, None, None)
        self.summary = self.artifacts[0]
        with patch.object(dbt, '_start'), patch.object(compiler, 'compile_project', return_value=self.summary):
            ack = dbt.submit_plan(self.principal, self.context, key='view-plan-' + uuid.uuid4().hex, project_id='general',
                source_schema='raw', target_schema='marts', staging_materialization=None)
            dbt._execute(ack['id'])
        self.plan_id = ack['id']
        self.artifact_patch.start()
        self.assertEqual(dbt.get_plan(self.principal, self.context, self.plan_id)['state'], 'READY')

    def test_installed_view_table_complete_tests_invoker_and_private_measurement(self):
        callbacks = []
        execute = graph.Bridge.execute
        def observed(bridge, sql, *args):
            callbacks.append((bridge.node['unique_id'], sql))
            return execute(bridge, sql, *args)
        with patch.object(graph.Bridge, 'execute', observed):
            result = self.execute(self.accept(tests=True))
        self.assertEqual(result['state'], 'NATIVE_VERIFIED', result)
        self.assertTrue(result['native_verified'])
        self.assertEqual(result['tests_status'], 'PASSED')
        self.assertFalse(result['publication_allowed'])
        self.assertEqual(len(result['tests']), 3)
        self.assertEqual({r['task'] for r in result['task_receipts'].values()}, {'RunTask', 'TestTask'})
        view = next(m for m in result['models'] if m['materialized'] == 'view')
        self.assertEqual(view['candidate']['kind'], 'VIEW')
        self.assertEqual(set(view['candidate']), {'kind', 'ref', 'view_uuid', 'version', 'security'})
        callback = next(sql for uid, sql in callbacks if uid == self.uid)
        self.assertIn('security invoker', callback.lower())
        self.assertIn('create or replace view', callback.lower())
        creates = [s for s, _ in self.w.posts if s.startswith('CREATE VIEW')]
        self.assertEqual(len(creates), 1)
        self.assertIn(' SECURITY INVOKER AS ', creates[0])
        self.assertNotIn('OR REPLACE', creates[0])
        self.assertIn('FOR VERSION AS OF', creates[0])
        table = next(s for s, _ in self.w.posts if s.startswith('CREATE TABLE'))
        self.assertNotIn('FOR VERSION AS OF', table)
        saved = self.record(result['id'])
        with control_store.connect() as db:
            self.assertTrue(graph.task_proof(db, saved, 'build'))
            self.assertTrue(graph.task_proof(db, saved, 'test'))
        count = len(self.w.posts)
        with patch.object(graph, '_run_subprocess', side_effect=AssertionError('observation SDK')):
            self.assertEqual(dbt.get_run(self.principal, self.context, result['id'])['id'], result['id'])
            self.assertEqual(self.accept(tests=True)['id'], result['id'])
        self.assertEqual(len(self.w.posts), count)

    def test_callback_definer_and_injected_ddl_rejected_before_query(self):
        execute = graph.Bridge.execute
        for index, change in enumerate((lambda s: s.replace('security invoker', 'security definer'),
                lambda s: s + '; DROP TABLE forbidden', lambda s: s.replace('id > 0', 'id > 1'))):
            def changed(bridge, sql, *args):
                return execute(bridge, change(sql), *args)
            with self.subTest(index=index), patch.object(graph.Bridge, 'execute', changed):
                result = self.execute(self.accept(key='fault-' + str(index)))
                self.assertEqual(result['state'], 'FAILED', result)
                self.assertFalse(result['native_verified'])
                self.assertFalse(self.w.posts)

    def test_build_is_not_quality_and_table_selected_publication_is_denied(self):
        result = self.execute(self.accept())
        self.assertTrue(result['native_verified'], result)
        self.assertEqual(result['tests_status'], 'NOT_RUN')
        self.mutate(result['id'], lambda r: r.update(publication_allowed=True))
        self.assertFalse(dbt.get_run(self.principal, self.context, result['id'])['publication_allowed'])
        table = next(m['unique_id'] for m in result['models'] if m['materialized'] == 'table')
        for method in (dbt.get_publication_preview, dbt.submit_publication):
            args = dict(node_id=table)
            if method is dbt.submit_publication:
                args['key'] = 'forbidden'
            with self.assertRaises(QueryError) as caught:
                method(self.w.owner, self.b.context(schema='sandbox', principal=self.w.owner, role='admin'), result['id'], **args)
            self.assertEqual(caught.exception.code, 'DBT_GRAPH_VIEW_PUBLICATION_UNSUPPORTED')

    def view_metadata(self):
        return next(value for key, value in self.b.objects.items() if key[3] == 'views')

    def test_pure_view_physical_graph_with_ephemeral_and_complete_tests(self):
        self.artifact_patch.stop()
        (self.project / 'models/final_result.sql').unlink()
        (self.project / 'models/quality.yml').write_text(yaml.safe_dump(dict(version=2,
            models=[dict(name='arbitrary_result', columns=[dict(name='id', data_tests=['not_null'])])])))
        self.save_plan()
        self.assertEqual(dbt.get_graph_options(self.principal, self.context, self.plan_id)['table_count'], 0)
        result = self.execute(self.accept(tests=True))
        self.assertEqual(result['state'], 'NATIVE_VERIFIED', result)
        self.assertEqual(result['tests_status'], 'PASSED')
        self.assertEqual(len(result['operation_ids']), 3)
        self.assertFalse(any(sql.startswith('CREATE TABLE') for sql, _ in self.w.posts))

    def test_raw_append_keeps_historical_view_input(self):
        transport = self.transport
        original_snapshot = self.w.metadata(self.source)['current-snapshot-id']
        def appended(url, method, pin, body=None):
            page = transport(url, method, pin, body)
            if body and body.decode().startswith('CREATE VIEW'):
                self.w.snapshot(self.w.metadata(self.source), [(3, 'new')])
            return page
        with patch.object(queries, '_transport', side_effect=appended):
            ack = self.accept(tests=True)
            dbt._execute(ack['id'])
        result = dbt.get_run(self.principal, self.context, ack['id'])
        self.assertEqual(result['tests_status'], 'PASSED', result)
        definition = self.view_metadata()['versions'][0]['representations'][0]['sql']
        self.assertIn('FOR VERSION AS OF ' + str(original_snapshot), definition)

    def test_view_drift_blocks_downstream_without_reuse_and_get_sends_no_sql(self):
        enter = graph.Graph.enter
        def drift(owner, uid, skipped):
            if uid.endswith('.final_result'):
                self.view_metadata()['properties'] = {'trino.run-as-owner': 'forbidden'}
            return enter(owner, uid, skipped)
        with patch.object(graph.Graph, 'enter', drift), patch.object(queries, '_transport', side_effect=self.transport):
            ack = self.accept(tests=True)
            dbt._execute(ack['id'])
        self.assertEqual(len(self.w.posts), 1)
        self.assertEqual(self.record(ack['id'])['error_code'], 'DBT_VIEW_CHANGED')
        with self.assertRaises(QueryError):
            dbt.get_run(self.principal, self.context, ack['id'])
        self.assertEqual(len(self.w.posts), 1)

    def test_get_rejects_definition_identity_version_and_metadata_drift(self):
        result = self.execute(self.accept())
        metadata = self.view_metadata()
        original = deepcopy(metadata)
        before = len(self.w.posts)
        changes = [lambda m: m.update(**{'view-uuid': str(uuid.uuid4())}),
            lambda m: m.update(**{'current-version-id': 99}),
            lambda m: m['versions'][0]['representations'][0].update(sql='SELECT 99 AS id'),
            lambda m: m.update(properties={'trino.security': 'DEFINER'}),
            lambda m: m.update(**{'version-log': []})]
        for change in changes:
            metadata.clear()
            metadata.update(deepcopy(original))
            change(metadata)
            with self.assertRaises(QueryError):
                dbt.get_run(self.principal, self.context, result['id'])
        metadata.clear()
        metadata.update(original)
        self.assertEqual(len(self.w.posts), before)

    def test_candidate_binding_identity_query_and_task_receipt_mismatches_reject(self):
        result = self.execute(self.accept(tests=True))
        original = self.record(result['id'])
        mutations = [lambda r: next(m for m in r['models'] if m['materialized'] == 'view')['candidate']['ref'].update(database_id='other-db'),
            lambda r: next(m for m in r['models'] if m['materialized'] == 'view').update(materialized='table'),
            lambda r: next(m for m in r['models'] if m['materialized'] == 'view')['_measurement'].update(view_uuid=str(uuid.uuid4())),
            lambda r: r['task_receipts']['build'].update(task='TestTask'),
            lambda r: r['task_receipts']['build']['results'][0].update(node_id='wrong'),
            lambda r: r['task_receipts']['build']['results'][0].update(status='error')]
        for change in mutations:
            tampered = deepcopy(original)
            change(tampered)
            with control_store.connect() as db:
                self.assertFalse(graph.task_proof(db, tampered, 'build'))
        view = next(m for m in original['models'] if m['materialized'] == 'view')
        with control_store.connect() as db:
            query = db.execute('SELECT query_id FROM dataops_queries WHERE operation_id=?', (view['operation_ids'][0],)).fetchone()[0]
            db.execute("UPDATE dataops_queries SET action='dbt_native_create' WHERE query_id=?", (query,))
            self.assertIsNone(graph.child_proof(db, original, self.uid, 'build'))

    def test_installed_view_result_config_mismatch_retains_committed_output(self):
        end = graph.Graph.end
        def mismatch(owner, result):
            if result['node_id'] == self.uid:
                result = dict(result, view_security='definer')
            return end(owner, result)
        with patch.object(graph.Graph, 'end', mismatch):
            result = self.execute(self.accept())
        self.assertEqual(result['state'], 'FAILED', result)
        self.assertFalse(result['native_verified'])
        self.assertEqual(len(self.w.posts), 1)
        self.assertTrue(result['operations'][0]['committed'])
        self.assertEqual(len([k for k in self.b.objects if k[3] == 'views']), 1)

    def test_quality_failure_is_separate_from_verified_view_build(self):
        transport = self.transport
        def failed(url, method, pin, body=None):
            page = transport(url, method, pin, body)
            if body and 'dbt_internal_test' in body.decode():
                page['data'] = [[1, True, True]]
            return page
        with patch.object(queries, '_transport', side_effect=failed):
            ack = self.accept(tests=True)
            dbt._execute(ack['id'])
        result = dbt.get_run(self.principal, self.context, ack['id'])
        self.assertTrue(result['native_verified'], result)
        self.assertEqual(result['tests_status'], 'FAILED')
        self.assertEqual(result['state'], 'FAILED')
        self.assertEqual(len(result['tests']), 3)

    def test_boolean_quality_count_cannot_pass_as_integer_zero(self):
        transport = self.transport
        def malformed(url, method, pin, body=None):
            page = transport(url, method, pin, body)
            if body and 'dbt_internal_test' in body.decode():
                page['data'] = [[False, False, False]]
            return page
        with patch.object(queries, '_transport', side_effect=malformed):
            ack = self.accept(tests=True)
            dbt._execute(ack['id'])
        result = dbt.get_run(self.principal, self.context, ack['id'])
        self.assertTrue(result['native_verified'], result)
        self.assertEqual(result['error_code'], 'DBT_NATIVE_TEST_RESULT_INVALID')
        self.assertNotEqual(result['tests_status'], 'PASSED')

    def test_transitive_same_run_table_head_change_blocks_view_reuse(self):
        self.artifact_patch.stop()
        (self.project / 'models/prepared_rows.sql').write_text(
            "SELECT id, label FROM {{ source('registered', 'registered_input') }}")
        self.save_plan()
        end = graph.Graph.end
        def changed(owner, result):
            answer = end(owner, result)
            if result['node_id'] == self.uid:
                table = next(v for k, v in self.b.objects.items() if k[1] == 'sandbox' and k[3] == 'tables')
                self.w.snapshot(table, [(9, 'changed')])
            return answer
        with patch.object(graph.Graph, 'end', changed), patch.object(queries, '_transport', side_effect=self.transport):
            ack = self.accept(tests=True)
            dbt._execute(ack['id'])
        self.assertEqual(self.record(ack['id'])['error_code'], 'STAGE_CHANGED')
        self.assertEqual(len(self.w.posts), 2)
        with self.assertRaises(QueryError):
            dbt.get_run(self.principal, self.context, ack['id'])

    def test_view_binding_remains_exact_selected_scoped_and_same_database(self):
        execute = graph.Bridge.execute
        checks = []
        def scoped(bridge, sql, *args):
            if bridge.phase == 'build' and bridge.node['materialized'] == 'table':
                saved = next(m['_measurement'] for m in self.record(bridge.owner.ident)['models'] if m['materialized'] == 'view')
                ref = registry.ResourceRef(**saved['ref'])
                before = len(self.w.posts)
                for changed_ref, expected, code in ((replace(ref, database_id='other-db'), saved, 'INVALID_BINDING'),
                        (ref, dict(saved, version=99), 'DBT_VIEW_CHANGED')):
                    with self.assertRaises(QueryError) as caught:
                        dbt._view_binding(bridge.execution, changed_ref, expected=expected)
                    self.assertEqual(caught.exception.code, code)
                with self.assertRaises(QueryError) as caught:
                    bridge.execution._dbt_measure_view(replace(ref, database_id='other-db'))
                self.assertEqual(caught.exception.code, 'CROSS_DATABASE_TARGET')
                self.assertEqual(len(self.w.posts), before)
                checks.append(True)
            return execute(bridge, sql, *args)
        with patch.object(graph.Bridge, 'execute', scoped):
            result = self.execute(self.accept())
        self.assertEqual(result['state'], 'NATIVE_VERIFIED', result)
        self.assertEqual(checks, [True])
        view = next(m for m in result['models'] if m['materialized'] == 'view')
        with self.assertRaises(QueryError) as caught:
            dbt._view_binding(ds.Execution(self.principal, view['operation_ids'][0]), registry.ResourceRef(**view['candidate']['ref']))
        self.assertEqual(caught.exception.code, 'DBT_CONTINUATION_REQUIRED')

    def test_committed_view_measurement_failure_preserves_owned_output(self):
        def failed(*args):
            raise QueryError('DBT_VIEW_OBSERVATION_REQUIRED', 409)
        with patch.object(ds.Execution, '_dbt_measure_view', failed):
            result = self.execute(self.accept())
        self.assertFalse(result['native_verified'])
        self.assertEqual(result['error_code'], 'DBT_VIEW_OBSERVATION_REQUIRED')
        self.assertEqual(len(self.w.posts), 1)
        self.assertTrue(result['operations'][0]['committed'])
        self.assertEqual(len([k for k in self.b.objects if k[3] == 'views']), 1)

    def test_cancel_after_view_build_retains_candidate_without_downstream_sql(self):
        end = graph.Graph.end
        def canceled(owner, result):
            answer = end(owner, result)
            if result['node_id'] == self.uid:
                dbt.cancel_run(self.principal, self.context, owner.ident, key='cancel-view')
            return answer
        with patch.object(graph.Graph, 'end', canceled):
            result = self.execute(self.accept(tests=True))
        self.assertEqual(result['state'], 'CANCELED', result)
        self.assertFalse(result['native_verified'])
        self.assertEqual(len(self.w.posts), 1)
        self.assertTrue(next(m for m in result['models'] if m['materialized'] == 'view')['candidate'])


class ViewSQLTests(unittest.TestCase):
    def test_view_callback_requires_exact_target_invoker_body_and_properties(self):
        target = ('catalog', 'sandbox', 'candidate')
        approved = compiler.normalize_select('SELECT 7 AS id', {}, database_id='db')
        sql = 'CREATE OR REPLACE VIEW catalog.sandbox.candidate SECURITY INVOKER AS SELECT 7 AS id'
        self.assertEqual(native._native_view_select(sql, target, {}, approved, 'db'), approved)
        for changed in (sql.replace('INVOKER', 'DEFINER'), sql.replace(' SECURITY INVOKER', ''),
                sql.replace('catalog.sandbox', 'other.sandbox'), sql.replace('candidate', 'another'),
                sql.replace(' OR REPLACE', ''), sql + '; DROP VIEW catalog.sandbox.candidate',
                sql.replace(' AS SELECT', " COMMENT 'extra' AS SELECT"),
                sql.replace('7 AS', '8 AS'), '/* extra */ ' + sql):
            with self.subTest(sql=changed), self.assertRaises(QueryError):
                native._native_view_select(changed, target, {}, approved, 'db')


if __name__ == '__main__':
    unittest.main()
