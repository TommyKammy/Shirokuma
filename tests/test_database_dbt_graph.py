"""Installed task DAGs against the real DataOps ledger and fake Trino transport."""
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml
import test_database_dbt_native as fixtures
import database_dbt as compiler
import database_dbt_graph as graph
import database_dbt_graph_worker as worker
import database_dbt_native as native
import control_store
import dataops_service as ds
import db_dbt as dbt
import query_service as queries
from query_context import QueryError


class GraphTests(unittest.TestCase):
    clear_live = fixtures.NativeTests.clear_live
    mutate_plan = fixtures.NativeTests.mutate_plan

    def setUp(self):
        fixtures.NativeTests.setUp(self)
        self.artifact_patch.stop()
        with (self.project / 'macros/helpers.sql').open('a') as helpers:
            helpers.write('''
{% macro projection__columns() %}id, label{% endmacro %}
{% macro generate_projection() %}{{ projection__columns() }}{% endmacro %}
''')
        (self.project / 'models/arbitrary_result.sql').write_text(
            "SELECT {{ generate_projection() }} FROM {{ ref('prepared_rows') }} WHERE id > 0")
        # A diamond with a real ephemeral ancestor and helper macro expansion.
        (self.project / 'models/left_branch.sql').write_text("SELECT id, label FROM {{ ref('arbitrary_result') }}")
        (self.project / 'models/right_branch.sql').write_text("SELECT id, label FROM {{ ref('arbitrary_result') }}")
        (self.project / 'models/final_result.sql').write_text("SELECT l.id, l.label FROM {{ ref('left_branch') }} l JOIN {{ ref('right_branch') }} r ON l.id = r.id")
        (self.project / 'models/quality.yml').write_text(yaml.safe_dump(dict(version=2,
            models=[dict(name='arbitrary_result', columns=[dict(name='id', data_tests=['not_null'])]),
                dict(name='final_result', columns=[dict(name='id', data_tests=[dict(relationships=dict(
                    arguments=dict(to="ref('left_branch')", field='id')))])])],
            )))
        source_path = self.project / 'models/sources.yml'
        source_config = yaml.safe_load(source_path.read_text())
        source_config['sources'][0]['tables'][0]['columns'] = [dict(name='id', data_tests=['not_null'])]
        source_path.write_text(yaml.safe_dump(source_config))
        compiled = compiler._compiled_project(self.project, catalog=self.context.sql_catalog, source_schema='raw')
        self.artifacts = compiled.__enter__()
        self.addCleanup(compiled.__exit__, None, None, None)
        self.summary = self.artifacts[0]
        with patch.object(dbt, '_start'), patch.object(compiler, 'compile_project', return_value=self.summary):
            ack = dbt.submit_plan(self.principal, self.context, key='graph-plan', project_id='general',
                source_schema='raw', target_schema='marts', staging_materialization=None)
            dbt._execute(ack['id'])
        self.plan_id = ack['id']
        self.artifact_patch.start()
        self.assertEqual(dbt.get_plan(self.principal, self.context, self.plan_id)['state'], 'READY')

    def record(self, ident=None):
        with control_store.connect() as db:
            return dbt._row(db, ident or self.plan_id)[1]

    def mutate(self, ident, function):
        with control_store.connect() as db:
            _, record = dbt._row(db, ident)
            function(record)
            dbt._write(db, record)

    def accept(self, key='graph', tests=False):
        with patch.object(dbt, '_start'):
            return dbt.submit_run(self.principal, self.context, key=key, plan_id=self.plan_id,
                executor='native_graph', native_tests=tests)

    def transport(self, url, method, pin, body=None):
        return fixtures.NativeQualityTests.quality_transport(self, url, method, pin, body)

    def execute(self, ack):
        with patch.object(queries, '_transport', side_effect=self.transport):
            dbt._execute(ack['id'])
        return dbt.get_run(self.principal, self.context, ack['id'])

    def test_installed_diamond_ephemeral_helpers_and_full_quality(self):
        options = dbt.get_graph_options(self.principal, self.context, self.plan_id)
        self.assertTrue(options['eligible'], options)
        self.assertTrue(options['testability']['eligible'], options)
        self.assertEqual((options['model_count'], options['table_count'], options['testability']['count']), (5, 4, 3))
        inventory, starts, listings = [], [], []
        lookup = graph.Graph.inventory
        popen = graph.subprocess.Popen
        def observed(owner, message):
            inventory.append(message['op'])
            before = len(self.w.posts)
            answer = lookup(owner, message)
            if message['op'] == 'list_relations':
                listings.append(answer)
                for database, schema in [('other_catalog', owner.context.sql_schema),
                        (owner.context.sql_catalog, 'raw')]:
                    with self.assertRaises(QueryError) as caught:
                        lookup(owner, dict(message, database=database, schema=schema))
                    self.assertEqual(caught.exception.code, 'DBT_NATIVE_RELATION_LOOKUP')
            self.assertEqual(len(self.w.posts), before, 'metadata inventory must not send SQL')
            return answer
        def started(command, **kwargs):
            starts.append((command, kwargs))
            return popen(command, **kwargs)
        with patch.object(graph.Graph, 'inventory', observed), patch.object(graph.subprocess, 'Popen', side_effect=started):
            result = self.execute(self.accept(tests=True))
        self.assertEqual(len(starts), 1)
        self.assertTrue(starts[0][0][0].endswith('dataops/.venv/bin/python3'))
        self.assertNotIn('SHIROKUMA_AUTH_REFS', starts[0][1]['env'])
        self.assertTrue({'list_schemas', 'list_relations', 'get_relation'} <= set(inventory), inventory)
        self.assertEqual(listings[0], [])
        self.assertEqual(len(listings[-1]), 4)
        self.assertTrue(all(r['database'] == self.context.sql_catalog and r['schema'] == self.context.sql_schema
            for listing in listings for r in listing))
        copied = worker.manifest_copy(self.artifacts[1])['macros']
        for name in ('generate_projection', 'projection__columns'):
            self.assertIn('macro.general_native.' + name, copied)
        for name in ('generate_schema_name', 'trino__create_table_as', 'properties', 'materialization_table_trino'):
            self.assertNotIn('macro.general_native.' + name, copied)
        self.assertEqual(result['state'], 'NATIVE_VERIFIED', result)
        self.assertTrue(result['native_verified'])
        self.assertEqual(result['tests_status'], 'PASSED')
        self.assertTrue(result['publication_allowed'])
        self.assertEqual(len(result['operation_ids']), 7)
        self.assertTrue(all(ident not in ds._live for ident in result['operation_ids']))
        self.assertEqual([m['state'] for m in result['models']].count('EXPANDED'), 1)
        self.assertEqual({r['task'] for r in result['task_receipts'].values()}, {'RunTask', 'TestTask'})
        with control_store.connect() as db:
            saved = self.record(result['id'])
            self.assertTrue(graph.task_proof(db, saved, 'build'))
            self.assertTrue(graph.task_proof(db, saved, 'test'))
            ops = list(db.execute('SELECT o.* FROM dataops_operations o JOIN dbt_run_operations l ON l.operation_id=o.id WHERE l.run_id=?', (result['id'],)))
            self.assertEqual(len({o['credential_snapshot_ref'] for o in ops}), 1)
            self.assertEqual({o['actor'] for o in ops}, {self.principal.principal_id})

    def test_installed_full_quality_excludes_parent_checks_and_metadata_from_sdk_budget(self):
        # Advance only the graph's deadline clock; real SQL/cancel clocks stay intact.
        offset = 0
        clock = SimpleNamespace(monotonic=lambda: time.monotonic() + offset)
        check, candidates = graph.Graph.check, graph.check_candidates
        checks, scans = [], []

        def slow_candidates(metadata, run):
            nonlocal offset
            scans.append(run['id'])
            offset += native._SDK_TIMEOUT + 1
            return candidates(metadata, run)

        def slow_check(owner, *args, **kwargs):
            nonlocal offset
            before = len(scans)
            answer = check(owner, *args, **kwargs)
            strict = kwargs.get('candidates', True)
            checks.append(strict)
            self.assertEqual(len(scans) - before, int(strict))
            # Even idle scope checks may acquire parent I/O in the future.
            offset += native._SDK_TIMEOUT + 1
            return answer

        with patch.object(graph, 'time', clock), patch.object(graph.Graph, 'check', slow_check), \
                patch.object(graph, 'check_candidates', side_effect=slow_candidates):
            result = self.execute(self.accept(tests=True))
        self.assertEqual(result['state'], 'NATIVE_VERIFIED', result)
        self.assertTrue(result['native_verified'])
        self.assertEqual(result['tests_status'], 'PASSED')
        self.assertIn(False, checks)
        self.assertIn(True, checks)
        self.assertEqual(len(result['operation_ids']), 7)
        with control_store.connect() as db:
            saved = self.record(result['id'])
            self.assertEqual({r['task'] for r in saved['task_receipts'].values()}, {'RunTask', 'TestTask'})
            self.assertTrue(graph.task_proof(db, saved, 'build'))
            self.assertTrue(graph.task_proof(db, saved, 'test'))

    def test_build_only_and_replay_no_sdk_project_or_new_sql(self):
        result = self.execute(self.accept())
        self.assertEqual(result['state'], 'NATIVE_VERIFIED', result)
        self.assertEqual(result['tests_status'], 'NOT_RUN')
        count = len(self.w.posts)
        self.mutate(self.plan_id, lambda plan: plan.update(state='FAILED', source_digest='changed-after-acceptance'))
        with patch.object(dbt, '_projects', side_effect=AssertionError('replay project')), patch.object(graph, '_run_subprocess', side_effect=AssertionError('replay SDK')):
            replay = self.accept()
            dbt.get_run(self.principal, self.context, result['id'])
        self.assertEqual(replay['id'], result['id'])
        self.assertEqual(len(self.w.posts), count)


    def test_all_finished_creates_without_final_task_never_verify(self):
        original = graph.Graph.task_done
        def missing(owner, message):
            if message['phase'] == 'build':
                raise QueryError('DBT_GRAPH_TASK_RECEIPT_INVALID', 409)
            return original(owner, message)
        with patch.object(graph.Graph, 'task_done', missing):
            result = self.execute(self.accept())
        self.assertEqual(result['state'], 'FAILED')
        self.assertFalse(result['native_verified'])
        self.assertEqual(len(self.w.posts), 4)
        self.assertTrue(all(m['candidate'] for m in result['models'] if m['materialized'] == 'table'))

    def test_task_collections_status_and_duplicate_node_tampering(self):
        original = graph.Graph.task_done
        def altered(owner, message):
            message = deepcopy(message)
            message['results'][0]['status'] = 'error'
            return original(owner, message)
        with patch.object(graph.Graph, 'task_done', altered):
            result = self.execute(self.accept())
        self.assertEqual(result['error_code'], 'DBT_GRAPH_TASK_RECEIPT_INVALID')
        self.assertFalse(result['native_verified'])

    def test_tampered_sdk_body_never_sends(self):
        execute = graph.Bridge.execute
        def changed(bridge, sql, *args):
            return execute(bridge, sql.replace('id > 0', 'id > 1'), *args)
        with patch.object(graph.Bridge, 'execute', changed):
            result = self.execute(self.accept())
        self.assertEqual(result['error_code'], 'DBT_NATIVE_BODY_CHANGED')
        self.assertFalse(self.w.posts)

    def test_wrong_node_duplicate_callback_never_creates_second_child(self):
        enter = graph.Graph.enter
        def repeated(owner, uid, skipped):
            answer = enter(owner, uid, skipped)
            if owner.bridge:
                enter(owner, uid, skipped)
            return answer
        with patch.object(graph.Graph, 'enter', repeated):
            result = self.execute(self.accept())
        self.assertEqual(result['error_code'], 'DBT_NATIVE_CALLBACK_REUSED')
        self.assertEqual(len(result['operation_ids']), 1)
        self.assertFalse(self.w.posts)

    def test_sdk_error_after_create_preserves_candidate_and_actual_downstream_skips(self):
        transport = self.transport
        def failed(url, method, pin, body=None):
            page = transport(url, method, pin, body)
            page['error'] = dict(message='backend failure', errorCode=1, errorName='FAILED', errorType='USER_ERROR')
            return page
        with patch.object(queries, '_transport', side_effect=failed):
            ack = self.accept()
            dbt._execute(ack['id'])
        result = dbt.get_run(self.principal, self.context, ack['id'])
        self.assertEqual(result['state'], 'FAILED', result)
        self.assertFalse(result['native_verified'])
        self.assertEqual(len(self.w.posts), 1)
        self.assertEqual([m['state'] for m in result['models']].count('SKIPPED'), 3)
        self.assertEqual(len(result['operation_ids']), 1)
        self.assertFalse(result['task_receipts']['build']['success'])

    def test_finished_create_then_sdk_error_is_not_definite_sql_failure(self):
        handle = graph.Graph.handle
        def sdk_error(owner, message):
            response = handle(owner, message)
            if message['op'] == 'execute' and owner.phase == 'build':
                # The CREATE callback succeeded; the installed runner then sees an SDK error.
                return {'_graph_error': 'DATAOPS_STEP_FAILED'}
            return response
        with patch.object(graph.Graph, 'handle', sdk_error):
            result = self.execute(self.accept())
        self.assertEqual(result['state'], 'FAILED', result)
        self.assertEqual(result['error_code'], 'DBT_GRAPH_TASK_RECEIPT_INVALID')
        self.assertFalse(result['native_verified'])
        self.assertFalse(result['publication_allowed'])
        self.assertEqual(len(self.w.posts), 1)
        saved = self.record(result['id'])
        receipt = saved['task_receipts']['build']
        self.assertIs(receipt['completed'], True)
        self.assertIs(receipt['success'], False)
        self.assertEqual((len(receipt['selected']), len(receipt['results']), len(receipt['nodes'])), (5, 4, 5))
        failed = next(m for m in saved['models'] if m['state'] == 'FAILED' and m['operation_ids'])
        self.assertTrue(failed['candidate'])
        self.assertIs(failed['native_receipt']['native_verified'], False)
        with control_store.connect() as db:
            op = db.execute('SELECT * FROM dataops_operations WHERE id=?', (failed['operation_ids'][0],)).fetchone()
            self.assertEqual((op['state'], op['committed']), ('FAILED', 1))
            query = db.execute('SELECT q.state FROM dataops_queries d JOIN query_records q ON q.id=d.query_id '
                'WHERE d.operation_id=?', (op['id'],)).fetchone()
            self.assertEqual(query['state'], 'FINISHED')
            self.assertFalse(graph._task_completion_proof(db, saved, 'build'))
            self.assertFalse(graph.task_proof(db, saved, 'build'))
            db.execute('SAVEPOINT hide_commit')
            try:
                db.execute('UPDATE dataops_operations SET committed=0 WHERE id=?', (op['id'],))
                self.assertFalse(graph._task_completion_proof(db, saved, 'build'))
            finally:
                db.execute('ROLLBACK TO hide_commit')
                db.execute('RELEASE hide_commit')

    def check_definite_sql_failure(self, tests):
        ack = self.accept(tests=tests)
        transport = self.transport
        failures = []
        def failed(url, method, pin, body=None):
            saved = self.record(ack['id'])
            uid = 'model.general_native.left_branch'
            target = saved['_graph_locators'][uid]['physical_name']
            if method == 'POST' and body.decode().startswith('CREATE TABLE ') and target in body.decode().split(' AS ', 1)[0]:
                # Definite engine rejection: do not create a physical table first.
                failures.append(body.decode())
                self.w.posts.append((body.decode(), dict(pin.headers)))
                return dict(id='definite-sql-failure', error=dict(message='SQL execution failed',
                    errorCode=1, errorName='DIVISION_BY_ZERO', errorType='USER_ERROR'))
            return transport(url, method, pin, body)
        with patch.object(queries, '_transport', side_effect=failed):
            dbt._execute(ack['id'])
        result = dbt.get_run(self.principal, self.context, ack['id'])
        self.assertEqual(result['state'], 'FAILED', result)
        self.assertEqual(result['error_code'], 'DBT_GRAPH_BUILD_FAILED')
        self.assertFalse(result['native_verified'])
        self.assertFalse(result['publication_allowed'])
        self.assertEqual(result['tests_status'], 'ERROR' if tests else 'NOT_RUN')
        self.assertEqual(len(failures), 1)
        self.assertEqual(len(self.w.posts), 3)
        saved = self.record(ack['id'])
        receipt = saved['task_receipts']['build']
        self.assertEqual(receipt['task'], 'RunTask')
        self.assertIs(receipt['completed'], True)
        self.assertIs(receipt['success'], False)
        self.assertEqual((len(receipt['selected']), len(receipt['results']), len(receipt['nodes'])), (5, 4, 5))
        self.assertEqual(set(saved['task_receipts']), {'build'})
        self.assertTrue(saved['_graph_sdk_closed'])
        models = {m['name']: m for m in saved['models']}
        self.assertEqual(models['left_branch']['state'], 'FAILED')
        self.assertIsNone(models['left_branch']['candidate'])
        self.assertEqual(models['left_branch']['_sdk_result']['status'], 'error')
        self.assertEqual(models['final_result']['state'], 'SKIPPED')
        self.assertEqual(models['final_result']['_sdk_result']['status'], 'skipped')
        self.assertEqual(models['final_result']['operation_ids'], [])
        self.assertEqual(sum(m['state'] == 'NATIVE_VERIFIED' and bool(m['candidate']) for m in saved['models']), 2)
        if tests:
            self.assertTrue(all(t['state'] == 'ERROR' and not t.get('operation_id') for t in saved['tests']))
        with control_store.connect() as db:
            self.assertTrue(graph._task_completion_proof(db, saved, 'build'))
            self.assertFalse(graph.task_proof(db, saved, 'build'))
            failed_op = models['left_branch']['operation_ids'][0]
            self.assertEqual(db.execute('SELECT state FROM dataops_operations WHERE id=?',
                (failed_op,)).fetchone()[0], 'FAILED')
            query = db.execute('SELECT q.state,e.backend_query_id FROM dataops_queries d '
                'JOIN query_records q ON q.id=d.query_id JOIN query_execution_contexts e ON e.query_id=q.id '
                'WHERE d.operation_id=?', (failed_op,)).fetchone()
            self.assertEqual((query['state'], query['backend_query_id']), ('FAILED', 'definite-sql-failure'))
            for mutate in (
                lambda r: r['task_receipts']['build']['selected'].pop(),
                lambda r: r['task_receipts']['build']['results'].append(r['task_receipts']['build']['results'][0]),
                lambda r: r['task_receipts']['build']['nodes'][0].update(status='error'),
                lambda r: r['task_receipts']['build'].update(success=True),
                lambda r: next(m for m in r['models'] if m['name'] == 'left_branch')['operation_ids'].clear(),
            ):
                tampered = deepcopy(saved)
                mutate(tampered)
                self.assertFalse(graph._task_completion_proof(db, tampered, 'build'))
                self.assertFalse(graph.task_proof(db, tampered, 'build'))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM dbt_run_operations WHERE run_id=? AND node_id=?",
                (ack['id'], models['final_result']['unique_id'])).fetchone()[0], 0)
        with patch.object(graph, '_run_subprocess', side_effect=AssertionError('no SDK replay')), \
                patch.object(queries, '_transport', side_effect=AssertionError('no SQL replay')):
            self.assertEqual(self.accept(tests=tests)['id'], ack['id'])
            self.assertEqual(dbt.get_run(self.principal, self.context, ack['id'])['error_code'], 'DBT_GRAPH_BUILD_FAILED')
        return ack

    def test_installed_runtask_definite_sql_failure(self):
        self.check_definite_sql_failure(tests=False)

    def test_installed_runtask_definite_sql_failure_with_tests_opted_in(self):
        self.check_definite_sql_failure(tests=True)

    def test_negative_child_query_context_and_admission_tampering_invalidates_completion(self):
        ack = self.check_definite_sql_failure(tests=False)
        saved = self.record(ack['id'])
        op = next(m for m in saved['models'] if m['name'] == 'left_branch')['operation_ids'][0]
        with control_store.connect() as db:
            query = db.execute('SELECT query_id FROM dataops_queries WHERE operation_id=?', (op,)).fetchone()[0]
            child = db.execute("SELECT id FROM warehouse_admissions WHERE kind='dataops' AND subject_id=?", (op,)).fetchone()[0]
            query_admission = db.execute("SELECT id FROM warehouse_admissions WHERE kind='query' AND subject_id=?", (query,)).fetchone()[0]
            changes = [
                ('dataops_operations', 'id', op, 'committed', 1),
                ('dataops_operations', 'id', op, 'actor', 'foreign'),
                ('dataops_operations', 'id', op, 'fingerprint', 'tampered'),
                ('dataops_operations', 'id', op, 'context', '{}'),
                ('dataops_operations', 'id', op, 'credential_snapshot_ref', 'foreign'),
                ('dataops_queries', 'operation_id', op, 'step', 'other'),
                ('dataops_queries', 'operation_id', op, 'action', 'dbt_test'),
                ('query_records', 'id', query, 'state', 'FINISHED'),
                ('query_records', 'id', query, 'actor', 'foreign'),
            ]
            other_db = db.execute('SELECT id FROM databases WHERE id!=?', (saved['context']['database_id'],)).fetchone()[0]
            record_fields = {'database_id', 'schema_id', 'warehouse_id', 'generation_id'}
            for field, original in dict(saved['context'], mode='dataops', request_id=op,
                    worksheet_id='native-create', backend_username='', credential_snapshot_ref='', backend_query_id='').items():
                value = other_db if field == 'database_id' else 'foreign' if isinstance(original, str) else 999
                if field == 'backend_query_id':
                    value = '   '
                table, key = ('query_records', 'id') if field in record_fields else ('query_execution_contexts', 'query_id')
                changes.append((table, key, query, field, value))
            for ident in (child, query_admission):
                changes.extend(('warehouse_admissions', 'id', ident, field, value) for field, value in (
                    ('parent_id', None), ('state', 'ACTIVE'), ('state', 'FINISHED'), ('actor', 'foreign'),
                    ('database_id', other_db), ('finished_at', None), ('generation_id', 'foreign'),
                    ('execution_closure', 'FENCED_READ_ONLY')))
            for table, key, ident, field, value in changes:
                with self.subTest(table=table, field=field, value=value):
                    db.execute('SAVEPOINT tamper')
                    try:
                        # Inspect corrupt context tuples without committing fixture corruption.
                        db.execute('PRAGMA defer_foreign_keys=ON')
                        db.execute(f'UPDATE {table} SET {field}=? WHERE {key}=?', (value, ident))
                        self.assertFalse(graph._task_completion_proof(db, saved, 'build'))
                        self.assertFalse(graph.task_proof(db, saved, 'build'))
                    finally:
                        db.execute('ROLLBACK TO tamper')
                        db.execute('RELEASE tamper')
            self.assertTrue(graph._task_completion_proof(db, saved, 'build'))
            db.execute('SAVEPOINT missing_query')
            try:
                db.execute('DELETE FROM dataops_queries WHERE operation_id=?', (op,))
                self.assertFalse(graph._task_completion_proof(db, saved, 'build'))
            finally:
                db.execute('ROLLBACK TO missing_query')
                db.execute('RELEASE missing_query')

    def test_saved_negative_task_collection_tampering_is_receipt_invalid(self):
        def failed(url, method, pin, body=None):
            # No physical CREATE is performed for this definite engine failure.
            self.w.posts.append((body.decode(), dict(pin.headers)))
            return dict(id='sql-error', error=dict(message='SQL execution failed', errorCode=1,
                errorName='DIVISION_BY_ZERO', errorType='USER_ERROR'))
        run = graph._run_subprocess
        def tampered(*args):
            run(*args)
            self.mutate(args[1].ident, lambda r: r['task_receipts']['build']['results'].pop())
        ack = self.accept()
        with patch.object(queries, '_transport', side_effect=failed), patch.object(graph, '_run_subprocess', side_effect=tampered):
            dbt._execute(ack['id'])
        result = dbt.get_run(self.principal, self.context, ack['id'])
        self.assertEqual(result['error_code'], 'DBT_GRAPH_TASK_RECEIPT_INVALID')
        self.assertFalse(result['native_verified'])
        self.assertFalse(result['publication_allowed'])

    def test_quality_failure_completes_suite_and_preserves_build(self):
        transport = self.transport
        def failed(url, method, pin, body=None):
            page = transport(url, method, pin, body)
            if body.decode().startswith('SELECT COUNT(*) AS failures') and not getattr(self, 'failed_once', False):
                self.failed_once = True
                page['data'] = [[2, True, True]]
            return page
        ack = self.accept(tests=True)
        with patch.object(queries, '_transport', side_effect=failed):
            dbt._execute(ack['id'])
        result = dbt.get_run(self.principal, self.context, ack['id'])
        self.assertEqual(result['state'], 'FAILED', result)
        self.assertTrue(result['native_verified'])
        self.assertEqual(result['tests_status'], 'FAILED')
        self.assertEqual(len(result['operation_ids']), 7)
        self.assertEqual(sorted(t['failure_count'] for t in result['tests']), [0, 0, 2])

    def test_raw_append_uses_one_frozen_snapshot_and_candidate_heads(self):
        snapshot = self.w.metadata(self.source)['current-snapshot-id']
        original = graph.Graph.enter
        def append(owner, uid, skipped):
            if not getattr(self, 'appended', False):
                self.appended = True
                self.w.snapshot(self.w.metadata(self.source), [(1, 'one'), (2, 'two'), (3, 'new')])
            return original(owner, uid, skipped)
        with patch.object(graph.Graph, 'enter', append):
            result = self.execute(self.accept(tests=True))
        self.assertEqual(result['state'], 'NATIVE_VERIFIED', result)
        raw_reads = [sql for sql, _ in self.w.posts if 'registered_input' in sql]
        self.assertTrue(raw_reads)
        self.assertTrue(all('FOR VERSION AS OF ' + str(snapshot) in sql for sql in raw_reads))
        for model in result['models']:
            if model['candidate']:
                self.assertEqual(len(self.w.snapshots[(self.w.metadata(fixtures.registry.ResourceRef(**model['candidate']['ref']))['table-uuid'], int(model['candidate']['snapshot']))]), 2)

    def test_candidate_drift_after_callback_blocks_downstream(self):
        end = graph.Graph.end
        def drift(owner, result):
            answer = end(owner, result)
            run = dbt._row_run(owner.ident)
            built = next((m for m in run['models'] if m['candidate']), None)
            if built:
                self.w.snapshot(self.w.metadata(fixtures.registry.ResourceRef(**built['candidate']['ref'])), [(9, 'changed')])
            return answer
        with patch.object(graph.Graph, 'end', drift):
            result = self.execute(self.accept())
        self.assertEqual(result['error_code'], 'STAGE_CHANGED')
        self.assertFalse(result['native_verified'])
        self.assertEqual(len(self.w.posts), 1)
        self.assertTrue(next(m for m in result['models'] if m['candidate'])['candidate'])

    def test_cancel_after_create_keeps_unverified_committed_output(self):
        execute = graph.Bridge.execute
        def cancel(bridge, *args):
            result = execute(bridge, *args)
            self.mutate(bridge.owner.ident, lambda run: run.update(cancel_requested=True))
            return result
        with patch.object(graph.Bridge, 'execute', cancel):
            result = self.execute(self.accept())
        self.assertEqual(result['state'], 'CANCELED', result)
        self.assertFalse(result['native_verified'])
        self.assertEqual(len(self.w.posts), 1)
        self.assertTrue(next(m for m in result['models'] if m.get('candidate'))['candidate'])

    def test_owner_loss_after_committed_callback_never_resumes(self):
        execute = graph.Bridge.execute
        saved_live = []
        def lost(bridge, *args):
            result = execute(bridge, *args)
            saved_live.append(dbt._live.pop(bridge.owner.ident))
            return result
        try:
            with patch.object(graph.Bridge, 'execute', lost):
                result = self.execute(self.accept())
        finally:
            for live in saved_live:
                live[-1].close()
        self.assertNotEqual(result['state'], 'NATIVE_VERIFIED')
        self.assertFalse(result['native_verified'])
        self.assertEqual(len(self.w.posts), 1)
        with patch.object(graph, '_run_subprocess', side_effect=AssertionError('resume')):
            observed = dbt.get_run(self.principal, self.context, result['id'])
        self.assertFalse(observed['native_verified'])
        self.assertEqual(len(self.w.posts), 1)

    def test_sdk_eof_invalid_protocol_and_silent_timeout(self):
        # Use a real isolated process, changing only the child program.
        popen = graph.subprocess.Popen
        for name, code in [('eof', 'pass'), ('protocol', "print('[]', flush=True)"), ('silent', 'import time; time.sleep(20)')]:
            with self.subTest(name=name):
                def child(command, **kwargs):
                    return popen([command[0], '-c', code], **kwargs)
                with patch.object(graph.subprocess, 'Popen', side_effect=child), patch.object(native, '_SDK_TIMEOUT', .3):
                    result = self.execute(self.accept(key=name))
                self.assertNotEqual(result['state'], 'NATIVE_VERIFIED')
                self.assertFalse(result['native_verified'])
                self.assertFalse(self.w.posts)
                if name == 'silent':
                    self.assertEqual(result['error_code'], 'DBT_NATIVE_SDK_TIMEOUT')

    def test_bridge_constructor_failure_releases_original_child_without_sql(self):
        executions = []
        def failed(owner, execution, node, phase):
            executions.append(execution)
            raise QueryError('TABLE_OBSERVATION_REQUIRED', 409)
        ack = self.accept(key='constructor-failure')
        with patch.object(graph, 'Bridge', side_effect=failed):
            result = self.execute(ack)
        self.assertEqual(result['error_code'], 'TABLE_OBSERVATION_REQUIRED')
        self.assertFalse(self.w.posts)
        self.assertEqual(len(executions), 1)
        execution = executions[0]
        self.assertNotIn(execution.id, ds._live)
        self.assertEqual(execution.inspect()['state'], 'FAILED')
        execution.reconcile_queries()
        owner = queries._acquire_query_owner('dataops-' + execution.id)
        owner.close()
        with control_store.connect() as db:
            admission = db.execute("SELECT state FROM warehouse_admissions WHERE kind='dataops' AND subject_id=?",
                (execution.id,)).fetchone()
            self.assertEqual(admission['state'], 'FAILED')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dataops_queries WHERE operation_id=?',
                (execution.id,)).fetchone()[0], 0)
        with patch.object(graph, '_run_subprocess', side_effect=AssertionError('SDK replay')):
            self.assertEqual(self.accept(key='constructor-failure')['id'], ack['id'])
        self.assertEqual(dbt.get_run(self.principal, self.context, ack['id'])['operation_ids'], [execution.id])
        self.assertFalse(self.w.posts)

    def test_saved_quality_result_requires_exact_scalar_types(self):
        result = self.execute(self.accept(tests=True))
        self.assertEqual(result['state'], 'NATIVE_VERIFIED', result)
        saved = self.record(result['id'])
        uid = saved['tests'][0]['unique_id']
        with control_store.connect() as db:
            self.assertIsNotNone(graph.child_proof(db, saved, uid, 'test'))
            for field, value in [('failures', False), ('should_warn', 0), ('should_error', 0)]:
                with self.subTest(field=field):
                    tampered = deepcopy(saved)
                    tampered['_graph_test_results'][uid][field] = value
                    self.assertIsNone(graph.child_proof(db, tampered, uid, 'test'))
                    self.assertFalse(graph.task_proof(db, tampered, 'test'))

    def test_saved_proof_tampering_prevents_retained_success(self):
        result = self.execute(self.accept(tests=True))
        self.assertEqual(result['state'], 'NATIVE_VERIFIED', result)
        saved = self.record(result['id'])
        for mutation in (
            lambda r: r['task_receipts'].pop('build'),
            lambda r: r['task_receipts']['build']['selected'].pop(),
            lambda r: r['models'][1]['operation_ids'].append('foreign-child'),
            lambda r: r['models'][1]['native_receipt']['pins'].clear(),
            lambda r: r['models'][1]['candidate']['ref'].update(database_id='other'),
            lambda r: r['task_receipts']['build']['nodes'][0].update(status='error'),
        ):
            current = deepcopy(saved)
            mutation(current)
            with control_store.connect() as db:
                self.assertFalse(graph.task_proof(db, current, 'build'))
        with control_store.connect() as db:
            op = saved['models'][1]['operation_ids'][0]
            query = db.execute('SELECT query_id FROM dataops_queries WHERE operation_id=?', (op,)).fetchone()[0]
            db.execute("UPDATE query_execution_contexts SET credential_snapshot_ref='different' WHERE query_id=?", (query,))
        dbt._settle(result['id'], owner_lost=True)
        self.assertFalse(self.record(result['id'])['native_verified'])
        self.assertEqual(self.record(result['id'])['state'], 'FAILED')

    def test_final_send_after_wake_rechecks_candidate_head(self):
        wake = queries._ensure_query_awake
        def drift(principal, query_id):
            value = wake(principal, query_id)
            with control_store.connect() as db:
                ident = db.execute('SELECT l.run_id FROM dbt_run_operations l JOIN dataops_queries d ON d.operation_id=l.operation_id WHERE d.query_id=?', (query_id,)).fetchone()[0]
                run = dbt._row(db, ident)[1]
            built = next((m for m in run['models'] if m['candidate']), None)
            if built:
                self.w.snapshot(self.w.metadata(fixtures.registry.ResourceRef(**built['candidate']['ref'])), [(9, 'late')])
            return value
        with patch.object(queries, '_ensure_query_awake', side_effect=drift):
            result = self.execute(self.accept())
        self.assertEqual(result['error_code'], 'STAGE_CHANGED')
        self.assertEqual(len(self.w.posts), 1)
        self.assertFalse(result['native_verified'])

    def test_wrong_sdk_node_and_malformed_count_fail_closed(self):
        handle = graph.Graph.handle
        def wrong(owner, message):
            if message.get('op') == 'enter_node':
                message = dict(message, node_id='model.foreign.unselected')
            return handle(owner, message)
        with patch.object(graph.Graph, 'handle', wrong):
            result = self.execute(self.accept(key='wrong-node'))
        self.assertEqual(result['error_code'], 'DBT_NODE_NOT_FOUND')
        self.assertFalse(self.w.posts)
        transport = self.transport
        def malformed(url, method, pin, body=None):
            page = transport(url, method, pin, body)
            if body.decode().startswith('SELECT COUNT(*) AS failures'):
                page['data'] = [[True, False, False]]
            return page
        ack = self.accept(key='bad-count', tests=True)
        with patch.object(queries, '_transport', side_effect=malformed):
            dbt._execute(ack['id'])
        result = dbt.get_run(self.principal, self.context, ack['id'])
        self.assertEqual(result['error_code'], 'DBT_NATIVE_TEST_RESULT_INVALID')
        self.assertTrue(result['native_verified'])
        self.assertEqual(result['tests_status'], 'ERROR')
        self.assertTrue(all(t['state'] == 'ERROR' for t in result['tests']))
        self.assertEqual(len(self.w.posts), 5)

    def test_sdk_error_after_positive_create_retains_unverified_candidate(self):
        end = graph.Graph.end
        def failed(owner, result):
            if owner.bridge:
                result = dict(result, status='error', adapter_response={})
            return end(owner, result)
        with patch.object(graph.Graph, 'end', failed):
            result = self.execute(self.accept())
        self.assertFalse(result['native_verified'])
        self.assertNotEqual(result['state'], 'NATIVE_VERIFIED')
        self.assertEqual(len(self.w.posts), 1)
        model = next(m for m in result['models'] if m['candidate'])
        self.assertFalse(model['native_receipt']['native_verified'])
        self.assertTrue(model['candidate']['snapshot'])


    def test_installed_graph_in_second_database_uses_only_its_candidates_and_sources(self):
        first = self.execute(self.accept(key='first-db'))
        self.assertEqual(first['state'], 'NATIVE_VERIFIED', first)
        self.artifact_patch.stop()
        self.context = self.b.context(database=self.b.databases[1], schema='sandbox')
        self.source = self.w.existing('registered_input', database=self.b.databases[1], rows=[(8, 'other-db')])
        copied = compiler._compiled_project(self.project, catalog=self.context.sql_catalog, source_schema='raw')
        self.artifacts = copied.__enter__()
        self.addCleanup(copied.__exit__, None, None, None)
        with patch.object(dbt, '_start'), patch.object(compiler, 'compile_project', return_value=self.artifacts[0]):
            ack = dbt.submit_plan(self.principal, self.context, key='second-plan', project_id='general',
                source_schema='raw', target_schema='marts', staging_materialization=None)
            dbt._execute(ack['id'])
        self.plan_id = ack['id']
        self.artifact_patch.start()
        result = self.execute(self.accept(key='second-db', tests=True))
        self.assertEqual(result['state'], 'NATIVE_VERIFIED', result)
        self.assertTrue(all(m['candidate']['ref']['database_id'] == self.context.database_id
            for m in result['models'] if m['candidate']))
        first_refs = {m['candidate']['ref']['object_id'] for m in first['models'] if m['candidate']}
        second_refs = {m['candidate']['ref']['object_id'] for m in result['models'] if m['candidate']}
        self.assertFalse(first_refs & second_refs)
        self.assertTrue(all(first['context']['sql_catalog'] not in sql for sql, _ in self.w.posts[4:]))

    def test_lost_ack_is_unknown_and_same_key_never_sends_again(self):
        transport = self.transport
        def lost(url, method, pin, body=None):
            transport(url, method, pin, body)
            raise TimeoutError('lost acknowledgement')
        ack = self.accept(key='lost-ack')
        with patch.object(queries, '_transport', side_effect=lost):
            dbt._execute(ack['id'])
        result = dbt.get_run(self.principal, self.context, ack['id'])
        self.assertEqual(result['state'], 'UNKNOWN', result)
        self.assertFalse(result['native_verified'])
        self.assertEqual(len(self.w.posts), 1)
        self.assertEqual(len(result['operation_ids']), 1)
        child = result['operation_ids'][0]
        self.assertNotIn(child, ds._live)
        owner = queries._acquire_query_owner('dataops-' + child)
        owner.close()
        self.assertTrue(next(m for m in result['models'] if m['operation_ids'])['candidate_locator'])
        with patch.object(graph, '_run_subprocess', side_effect=AssertionError('SDK replay')):
            self.assertEqual(self.accept(key='lost-ack')['id'], ack['id'])
        self.assertEqual(len(self.w.posts), 1)


class GraphContractTests(unittest.TestCase):
    def setUp(self):
        import test_db_dbt
        test_db_dbt.DbtTests.setUp(self)
    clear_live = fixtures.NativeTests.clear_live

    def plan(self):
        import test_db_dbt
        return test_db_dbt.DbtTests.plan(self)
    summary = staticmethod(lambda *args, **kwargs: __import__('test_db_dbt').DbtTests.summary(None, *args, **kwargs))

    def saved(self, ident):
        with control_store.connect() as db:
            return dbt._row(db, ident)[1]

    def test_projection_is_pure_and_unsupported_graph_reasons(self):
        plan = self.plan()
        with patch.object(compiler, '_compiled_project', side_effect=AssertionError('SDK')), patch.object(queries, '_capture_dataops', side_effect=AssertionError('credentials')):
            options = dbt.get_graph_options(self.engineer, self.context, plan['id'])
            self.assertTrue(options['eligible'])
            self.assertEqual(options['model_count'], 3)
            self.assertEqual(options['table_count'], 2)
        saved = self.saved(plan['id'])
        metadata = dbt._access(self.engineer, self.context)
        for change, code in [
            (lambda p: p['models'][1].update(materialized='view'), 'DBT_GRAPH_MATERIALIZATION_UNSUPPORTED'),
            (lambda p: p['models'][1].update(materialized='incremental'), 'DBT_GRAPH_MATERIALIZATION_UNSUPPORTED'),
            (lambda p: p['models'][1].update(table_properties={}), 'DBT_NATIVE_PARQUET_REQUIRED'),
            (lambda p: p['models'][0].update(depends_on=['model.second']), 'DBT_GRAPH_DEPENDENCY_INVALID'),
            (lambda p: p['models'][0].update(depends_on=['missing']), 'DBT_GRAPH_DEPENDENCY_INVALID'),
            (lambda p: p['models'][2].update(relation=p['models'][1]['relation']), 'DBT_GRAPH_ALIAS_COLLISION'),
        ]:
            candidate = deepcopy(saved)
            change(candidate)
            with self.subTest(code=code), self.assertRaises(QueryError) as caught:
                graph.validate(metadata, self.context, candidate)
            self.assertEqual(caught.exception.code, code)

    def test_graph_arguments_publication_and_original_actor_full_context(self):
        plan = self.plan()
        for kwargs in [dict(node_id=None), dict(node_id='model.first'), dict(native_tests=1), dict(native_tests='false')]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                dbt.submit_run(self.engineer, self.context, key='bad', plan_id=plan['id'], executor='native_graph', **kwargs)
        ack = dbt.submit_run(self.engineer, self.context, key='graph', plan_id=plan['id'], executor='native_graph')
        for fn, context in [(dbt.get_publication_preview, self.admin_context), (dbt.submit_publication, self.admin_context)]:
            kwargs = dict(node_id='model.first')
            if fn is dbt.submit_publication:
                kwargs['key'] = 'pub'
            with self.assertRaises(QueryError) as caught:
                fn(self.admin, context, ack['id'], **kwargs)
            self.assertEqual(caught.exception.code, 'DBT_GRAPH_PUBLICATION_FORBIDDEN')
        with self.assertRaises(QueryError):
            dbt.submit_run(self.admin, self.admin_context, key='other', plan_id=plan['id'], executor='native_graph')
        with self.assertRaises(QueryError):
            dbt.submit_run(self.engineer, replace(self.context, binding_revision=100), key='ctx', plan_id=plan['id'], executor='native_graph')

    def test_complete_suite_uses_all_tests_and_validates_ownership(self):
        plan = self.saved(self.plan()['id'])
        plan['_native_tests'] = dict(version=1, owners={t['unique_id']: None for t in plan['tests']})
        metadata = dbt._access(self.engineer, self.context)
        self.assertEqual(graph.validate(metadata, self.context, plan, tests=True), plan['tests'])
        for owners in ({}, {t['unique_id']: 'missing-owner' for t in plan['tests']}):
            changed = deepcopy(plan)
            changed['_native_tests']['owners'] = owners
            with self.assertRaises(QueryError) as caught:
                graph.validate(metadata, self.context, changed, tests=True)
            self.assertEqual(caught.exception.code, 'DBT_NATIVE_TEST_OWNERS_INVALID')
        plan['tests'] = []
        with self.assertRaises(QueryError) as caught:
            graph.validate(metadata, self.context, plan, tests=True)
        self.assertEqual(caught.exception.code, 'DBT_NATIVE_TEST_EMPTY_SUITE')

    def test_alias_remapping_before_sorted_normalization_including_qualified_columns(self):
        ref = fixtures.registry.ResourceRef('db', 'sandbox', 'x', 1)
        other = fixtures.registry.ResourceRef('db', 'sandbox', 'y', 1)
        bindings = {('cat', 'sandbox', 'a'): ref, ('cat', 'sandbox', 'z'): other}
        logical = 'SELECT "cat"."sandbox"."a".id, z.label FROM "cat"."sandbox"."a" JOIN "cat"."sandbox"."z" z ON "a".id = z.id'
        remapped = logical.replace('"a"', '"g_zzz"').replace('"z"', '"g_aaa"')
        restored = graph.logical_sql(remapped, {('cat', 'sandbox', 'g_zzz'): ('cat', 'sandbox', 'a'),
            ('cat', 'sandbox', 'g_aaa'): ('cat', 'sandbox', 'z')})
        self.assertEqual(compiler.normalize_select(restored, bindings, database_id='db'),
            compiler.normalize_select(logical, bindings, database_id='db'))
        with self.assertRaises(compiler.DbtError):
            compiler.normalize_select('SELECT * FROM cat.sandbox.a JOIN cat.sandbox.z ON a.id = z.id',
                {('cat', 'sandbox', 'a'): ref, ('cat', 'sandbox', 'z'): ref}, database_id='db')

    def test_two_database_scope_and_saved_current_plan_proof(self):
        plan = self.plan()
        other = self.b.context(database=self.b.databases[1], schema='sandbox')
        with self.assertRaises(QueryError):
            dbt.get_graph_options(self.engineer, other, plan['id'])
        with self.assertRaises(QueryError):
            dbt.submit_run(self.engineer, other, key='other-db', plan_id=plan['id'], executor='native_graph')
        metadata = dbt._access(self.engineer, self.context)
        saved = self.saved(plan['id'])
        bad = deepcopy(saved)
        bad['source_bindings'][0]['ref']['database_id'] = other.database_id
        with self.assertRaises(QueryError) as caught:
            graph.validate(metadata, self.context, bad)
        self.assertEqual(caught.exception.code, 'DBT_NATIVE_RAW_REQUIRED')
        for mutate, code in [
            (lambda p: p.update(state='FAILED'), 'DBT_PLAN_NOT_READY'),
            (lambda p: p.update(actor='another'), 'CONTEXT_MISMATCH'),
            (lambda p: p['context'].update(grant_revision=99), 'CONTEXT_MISMATCH'),
            (lambda p: p.update(source_digest='changed'), 'DBT_PLAN_CHANGED'),
        ]:
            bad = deepcopy(saved)
            mutate(bad)
            with control_store.connect() as db:
                options = graph.options(db, metadata, self.engineer, self.context, bad)
            self.assertFalse(options['eligible'])
            self.assertEqual(options['reason_code'], code)

    def test_sql_semantics_and_test_dependencies_have_no_hidden_omissions(self):
        plan = self.saved(self.plan()['id'])
        metadata = dbt._access(self.engineer, self.context)
        for mutate in (
            lambda p: p['models'][1].update(compiled_sql='SELECT * FROM another.raw.input'),
            lambda p: p['models'][1].update(depends_on=[]),
            lambda p: p['models'].append(deepcopy(p['models'][1])),
        ):
            bad = deepcopy(plan)
            mutate(bad)
            with self.assertRaises((QueryError, compiler.DbtError)):
                graph.validate(metadata, self.context, bad)
        plan['_native_tests'] = dict(version=1, owners={t['unique_id']: None for t in plan['tests']})
        plan['tests'].append(dict(unique_id='test.unattached', name='unattached', relation=None,
            test_type='not_null', compiled_sql='SELECT 7 AS id WHERE FALSE', depends_on=[]))
        plan['_native_tests']['owners']['test.unattached'] = None
        self.assertEqual(len(graph.validate(metadata, self.context, plan, tests=True)), 3)
        plan['tests'][-1]['depends_on'] = ['model.missing']
        with self.assertRaises(QueryError):
            graph.validate(metadata, self.context, plan, tests=True)


if __name__ == '__main__':
    unittest.main()
