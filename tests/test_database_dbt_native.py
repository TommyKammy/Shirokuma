"""Private native macro proof with installed dbt, real DS ledger, fake engine."""
from contextlib import contextmanager
from dataclasses import asdict, replace
import json
from copy import deepcopy
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import control_store
import database_dbt as compiler
import database_dbt_native as native
import data_management as registry
import dataops_service as ds
import db_dbt
from query_context import QueryError
import query_service as queries
import warehouse_admission as admission
import test_manual_wap


class SQLTests(unittest.TestCase):
    def test_shared_node_validator_accepts_constant_select_and_blocks_write_dependencies(self):
        from types import SimpleNamespace
        context = SimpleNamespace(database_id='arbitrary-db')
        node = dict(unique_id='model.any.result', materialized='table',
                    table_properties={'format': 'PARQUET'}, depends_on=[], compiled_sql='SELECT 7 AS value')
        plan = dict(models=[node], source_bindings=[])
        metadata = SimpleNamespace(_schema=lambda _: self.fail('constant SELECT has no used schema'))
        selected, bindings, approved = native._node_plan(metadata, context, plan, node['unique_id'])
        self.assertEqual(selected, node)
        self.assertEqual(bindings, {})
        self.assertEqual(approved['sources'], {})
        for materialized in ('view', 'table', 'unknown'):
            plan['models'] = [dict(node, depends_on=['model.any.dependency']),
                dict(node, unique_id='model.any.dependency', materialized=materialized)]
            with self.subTest(materialized=materialized), self.assertRaises(QueryError) as caught:
                native._node_plan(metadata, context, plan, node['unique_id'])
            self.assertEqual(caught.exception.code, 'DBT_NATIVE_MATERIALIZED_DEPENDENCY')

    def test_first_cli_import_disables_dotenv_and_restores_exact_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            sentinel = Path(temporary) / '.env'
            sentinel.write_text('NATIVE_DOTENV_SENTINEL=must-never-load\n')
            code = '''
import os, sys
sys.path.insert(0, sys.argv[1])
import database_dbt_native as native
import dotenv
assert 'dbt.cli.main' not in sys.modules
saved = dict(os.environ)
load = dotenv.load_dotenv
calls = []
def checked(*args, **kwargs):
    assert os.environ['PYTHON_DOTENV_DISABLED'] == '1'
    calls.append(True)
    return load(dotenv_path=sys.argv[2])
dotenv.load_dotenv = checked
with native._without_dotenv():
    from dbt.cli.main import cli
assert calls
assert 'NATIVE_DOTENV_SENTINEL' not in os.environ
assert dict(os.environ) == saved
'''
            result = subprocess.run([sys.executable, '-c', code,
                str(Path(native.__file__).parent), str(sentinel)],
                env=dict(os.environ, PYTHON_DOTENV_DISABLED='0', SHIROKUMA_AUTH_REFS='sentinel-ref'),
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def setUp(self):
        self.ref = registry.ResourceRef('db', 'raw', 'original', 1)
        self.bindings = {('cat', 'raw', 'input_rows'): self.ref}
        self.body = 'WITH e AS (SELECT id, label FROM cat.raw.input_rows) SELECT id, label FROM e WHERE id > 0'
        self.approved = compiler.normalize_select(self.body, self.bindings, database_id='db')
        self.sql = "CREATE TABLE cat.sandbox.native_candidate WITH (format='PARQUET') AS (" + self.body + ')'

    def check(self, sql):
        return native._native_select(sql, ('cat', 'sandbox', 'native_candidate'), self.bindings, self.approved, 'db')

    def test_arbitrary_ephemeral_body_normalizes_against_original_binding(self):
        self.assertEqual(self.check(self.sql), self.approved)
        self.assertEqual(self.check(self.sql.replace("'PARQUET'", "'parquet'")), self.approved)
        self.assertEqual(list(self.approved['sources'].values()), [self.ref])

    def test_structural_rejections(self):
        cases = [
            self.sql.replace('cat.sandbox.native_candidate', t) for t in
            ('other.sandbox.native_candidate', 'cat.raw.native_candidate', 'cat.sandbox.another')]
        cases += [self.sql.replace('CREATE TABLE', s) for s in
                  ('CREATE OR REPLACE TABLE', 'CREATE TABLE IF NOT EXISTS', 'CREATE VIEW')]
        cases += [self.sql.replace("format='PARQUET'", s) for s in
                  ("format='ORC'", "format='PARQUET', location='s3://anything'", "format='PARQUET', format='PARQUET'")]
        cases += [self.sql.replace('id > 0', 'id > 1'), self.sql.replace('cat.raw.input_rows', 'cat.raw.other'),
                  self.sql + '; DROP TABLE cat.raw.input_rows', '/* authority */ ' + self.sql,
                  self.sql.replace(' AS (', ' COMMENT \'native\' AS ('),
                  self.sql.replace('SELECT id, label FROM e', 'SELECT id, label FROM e /* retry */'),
                  'SELECT 1', 'CREATE SCHEMA cat.sandbox', 'DROP TABLE cat.sandbox.native_candidate',
                  'ALTER TABLE cat.sandbox.native_candidate RENAME TO x',
                  self.sql.replace(" WITH (format='PARQUET')", ''),
                  self.sql.replace('cat.raw.input_rows', 'cat.raw.input_rows FOR VERSION AS OF 99')]
        for sql in cases:
            with self.subTest(sql=sql), self.assertRaises((QueryError, compiler.DbtError)):
                self.check(sql)


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.w = test_manual_wap.ManualWapTests()
        self.w.setUp()
        self.addCleanup(self.w.doCleanups)
        self.b = self.w.b
        self.principal = self.w.principal
        self.context = self.b.context(schema='sandbox')
        self.source = self.w.existing('registered_input', rows=[(1, 'one'), (2, 'two')])
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'project'
        (self.project / 'models').mkdir(parents=True)
        (self.project / 'dbt_project.yml').write_text(yaml.safe_dump(dict(
            name='general_native', version='1.0', **{'config-version': 2},
            models={'general_native': {'+materialized': 'table', '+properties': {'format': "'PARQUET'"}}})))
        (self.project / 'models/sources.yml').write_text(yaml.safe_dump(dict(version=2, sources=[dict(
            name='registered', database="{{ var('shirokuma_source_database') }}",
            schema="{{ var('shirokuma_source_schema') }}", tables=[dict(name='registered_input')]) ])))
        (self.project / 'models/prepared_rows.sql').write_text(
            "{{ config(materialized='ephemeral') }} SELECT id, label FROM {{ source('registered', 'registered_input') }}")
        (self.project / 'models/arbitrary_result.sql').write_text(
            "SELECT {{ selected_columns() }} FROM {{ ref('prepared_rows') }} WHERE id > 0")
        (self.project / 'macros').mkdir()
        (self.project / 'macros/helpers.sql').write_text('''
{% macro selected_columns() %}id, label{% endmacro %}
{% macro generate_schema_name(custom_schema_name, node) %}{{ target.schema }}{% endmacro %}
{% macro trino__create_table_as(temporary, relation, sql) %}DROP TABLE forbidden{% endmacro %}
{% macro properties(properties) %}WITH (format='ORC'){% endmacro %}
{% macro materialization_table_trino() %}{{ exceptions.raise_compiler_error('project execution forbidden') }}{% endmacro %}
''')
        self.uid = 'model.general_native.arbitrary_result'
        self.artifact_context = compiler._compiled_project(self.project, catalog=self.context.sql_catalog, source_schema='raw')
        self.artifacts = self.artifact_context.__enter__()
        self.addCleanup(self.artifact_context.__exit__, None, None, None)
        self.summary = self.artifacts[0]
        project_patch = patch.object(db_dbt, '_projects', return_value={
            'general': dict(id='general', name='General', path=self.project)})
        project_patch.start()
        self.addCleanup(project_patch.stop)
        with patch.object(db_dbt, '_start'), patch.object(compiler, 'compile_project', return_value=self.summary):
            ack = db_dbt.submit_plan(self.principal, self.context, key='plan', project_id='general',
                source_schema='raw', target_schema='marts', staging_materialization=None)
            db_dbt._execute(ack['id'])
        self.plan_id = ack['id']
        self.assertEqual(db_dbt.get_plan(self.principal, self.context, self.plan_id)['state'], 'READY')
        # Reuse the real immutable copied artifacts; all runtime objects/macros
        # are still reconstructed by the actual native path on each invocation.
        @contextmanager
        def artifacts(*args, **kwargs):
            yield self.artifacts
        artifact_patch = patch.object(compiler, '_compiled_project', side_effect=artifacts)
        self.artifact_patch = artifact_patch
        self.compile = artifact_patch.start()
        self.addCleanup(artifact_patch.stop)
        idle_patch = patch('warehouse_resident.check_lifecycle')
        idle_patch.start()
        self.addCleanup(idle_patch.stop)
        self.addCleanup(self.clear_live)

    def clear_live(self):
        for live in db_dbt._live.values():
            live[-1].close()
        db_dbt._live.clear()

    def run_native(self, key='native'):
        return native.materialize(self.principal, self.context, plan_id=self.plan_id, node_id=self.uid, key=key)

    def receipt(self):
        with control_store.connect() as db:
            row = db.execute('SELECT id FROM dataops_operations WHERE route=?', (native._ROUTE,)).fetchone()
        return ds.Execution(self.principal, row['id']).inspect()

    def mutate_plan(self, fn):
        with control_store.connect() as db:
            _, plan = db_dbt._row(db, self.plan_id)
            fn(plan)
            db_dbt._write(db, plan)

    def test_installed_macro_real_callback_response_pin_and_replay(self):
        from dbt.adapters.contracts.connection import AdapterResponse
        from dbt.task.run import ModelRunner
        import trino.dbapi
        original = native._Bridge.execute
        responses, runners = [], []
        def callback(bridge, *args, **kwargs):
            self.assertIs(kwargs.get('auto_begin'), True)
            response, table = original(bridge, *args, **kwargs)
            self.assertIsInstance(response, AdapterResponse)
            self.assertEqual(response.code, 'CREATE TABLE')
            responses.append((response, table))
            return response, table
        transport = self.w.transport
        def prepost(*args, **kwargs):
            with control_store.connect() as db:
                row = db.execute('SELECT step,commit_result FROM dataops_queries').fetchone()
                self.assertEqual(row['step'], 'native-create')
                self.assertFalse(json.loads(row['commit_result'])['native_verified'])
            return transport(*args, **kwargs)
        execute = ModelRunner.execute
        def runner(instance, model, manifest):
            self.assertEqual({m.package_name for m in manifest.macros.values()}, {'dbt', 'dbt_trino'})
            self.assertIn('macro.general_native.trino__create_table_as', self.artifacts[1]['macros'])
            self.assertIn('macro.general_native.materialization_table_trino', self.artifacts[1]['macros'])
            runners.append(instance._get_materialization_macro(model, manifest).unique_id)
            self.assertEqual(instance.config.credentials.host, '127.0.0.1')
            self.assertEqual(instance.config.credentials.port, 9)
            self.assertEqual(instance.config.credentials.method, 'none')
            return execute(instance, model, manifest)
        with patch.object(native._Bridge, 'execute', callback), patch.object(ModelRunner, 'execute', runner), \
                patch.object(queries, '_transport', side_effect=prepost), \
                patch.object(trino.dbapi, 'connect', side_effect=AssertionError('DBAPI prohibited')), \
                patch('socket.create_connection', side_effect=AssertionError('network prohibited')):
            result = self.run_native()
        self.assertEqual(result['state'], 'FINISHED')
        self.assertTrue(result['result']['native_verified'])
        self.assertEqual(runners, ['macro.dbt_trino.materialization_table_trino'])
        self.assertEqual(len(responses), 1)
        self.assertEqual(len(self.w.posts), 1)

        sql, headers = self.w.posts[0]
        self.assertIn('FOR VERSION AS OF ' + str(self.w.metadata(self.source)['current-snapshot-id']), sql)
        self.assertIn('WITH (format=\'PARQUET\')', sql)
        self.assertIn('n_' + result['id'].replace('-', '')[:17], sql)
        self.assertEqual(headers['Authorization'], 'Basic original-secret')
        self.assertEqual(result['queries'][0]['step'], 'native-create')
        self.assertEqual(result['result']['executor'], 'dbt_native_model_runner')
        def safe(value):
            if isinstance(value, dict):
                self.assertFalse(any(k.startswith('_') for k in value))
                for item in value.values():
                    safe(item)
            elif isinstance(value, list):
                for item in value:
                    safe(item)
        safe(result)
        for forbidden in ('original-secret', 'memory://', self.temp.name, 'generated_sql', 'compiled_sql'):
            self.assertNotIn(forbidden, json.dumps(result))
        with control_store.connect() as db:
            row = db.execute('SELECT commit_result FROM dataops_queries WHERE operation_id=?', (result['id'],)).fetchone()
            self.assertFalse(json.loads(row[0])['native_verified'])
            self.assertEqual(db.execute('SELECT count(*) FROM dataops_owned_objects WHERE operation_id=?', (result['id'],)).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM wap_builds').fetchone()[0], 0)
        with patch.object(native, '_idle', side_effect=AssertionError('replay idle')), \
                patch.object(native, '_run_native', side_effect=AssertionError('replay runner')):
            self.assertTrue(self.run_native()['replayed'])
        self.assertEqual(self.compile.call_count, 1)
        self.assertEqual(len(self.w.posts), 1)

    def test_callback_preserves_parent_store_profile_auth_home_and_tmp(self):
        original = native._Bridge.execute
        expected = None
        root = control_store.root()
        connect = control_store.connect
        calls = []
        def same_store(*args, **kwargs):
            self.assertEqual(control_store.root(), root)
            calls.append(True)
            return connect(*args, **kwargs)
        def callback(bridge, *args, **kwargs):
            self.assertEqual(dict(os.environ), expected)
            from dbt_common.context import get_invocation_context
            self.assertNotIn('SHIROKUMA_AUTH_REFS', get_invocation_context().env)
            self.assertEqual(bridge.execution._context(), self.context)
            return original(bridge, *args, **kwargs)
        with patch.dict(os.environ, SHIROKUMA_RUNTIME_PROFILE='parent-profile-ref',
                        SHIROKUMA_AUTH_REFS='parent-auth-ref', HOME=self.temp.name, TMPDIR=self.temp.name,
                        DBT_PROFILES_DIR='/must-not-read', DBT_PROFILE='must-not-read', DBT_TARGET='must-not-read'):
            expected = dict(os.environ)
            with patch.object(native._Bridge, 'execute', callback), patch.object(control_store, 'connect', same_store):
                self.assertTrue(self.run_native()['result']['native_verified'])
            self.assertEqual(dict(os.environ), expected)
        self.assertTrue(calls)

    def test_reserved_builtin_project_name_rejected_without_mutating_manifest(self):
        from copy import deepcopy
        raw = deepcopy(self.artifacts[1])
        for namespace in ('dbt', 'dbt_trino'):
            raw['metadata']['project_name'] = namespace
            with self.subTest(namespace=namespace), self.assertRaises(QueryError) as caught:
                native._builtin_manifest(raw)
            self.assertEqual(caught.exception.code, 'DBT_NATIVE_MACRO_ORIGIN')

    def test_native_scope_requires_raw_sources_and_sandbox_destination(self):
        with patch.object(native, '_run_native', side_effect=AssertionError('must not enter runner')):
            with self.assertRaises(QueryError) as caught:
                native.materialize(self.principal, self.b.context(schema='raw'),
                    plan_id=self.plan_id, node_id=self.uid, key='wrong-schema')
            self.assertEqual(caught.exception.code, 'SANDBOX_REQUIRED')
            with control_store.connect() as db:
                # MARTS is readable by this role; the native RAW-only guard
                # must reject it independently of generic SQL permission checks.
                db.execute("UPDATE schemas SET sql_name='unused_marts' WHERE database_id=? AND sql_name='marts'",
                           (self.context.database_id,))
                db.execute("UPDATE schemas SET sql_name='marts',purpose='marts' WHERE id=?", (self.source.schema_id,))
            with self.assertRaises(QueryError) as caught:
                self.run_native()
            self.assertEqual(caught.exception.code, 'DBT_NATIVE_RAW_REQUIRED')
        self.assertEqual(self.w.posts, [])

    def test_operator_path_recompiles_exact_saved_source_without_project_mutation(self):
        before = {p.relative_to(self.project): p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        self.artifact_patch.stop()
        result = self.run_native()
        self.assertTrue(result['result']['native_verified'])
        self.assertEqual(before, {p.relative_to(self.project): p.read_bytes()
                                  for p in self.project.rglob('*') if p.is_file()})

    def test_saved_source_digest_mismatch_stops_before_runner(self):
        self.artifact_patch.stop()
        with (self.project / 'models/arbitrary_result.sql').open('a') as stream:
            stream.write('\n')
        with patch.object(native, '_run_native', side_effect=AssertionError('must not run')), self.assertRaises(QueryError) as caught:
            self.run_native()
        self.assertEqual(caught.exception.code, 'DBT_PLAN_CHANGED')
        self.assertEqual(self.receipt()['state'], 'FAILED')
        self.assertEqual(self.w.posts, [])

    def test_postcreate_measurement_failure_retains_unverified_table(self):
        observe = ds.Execution._observe
        def fail(execution, locator):
            ref = observe(execution, locator)
            self.created = ref
            # Fail candidate measurement, after UUID registration.
            self.w.metadata(ref)['current-snapshot-id'] = None
            return ref
        with patch.object(ds.Execution, '_observe', fail), self.assertRaises(QueryError):
            self.run_native()
        receipt = self.receipt()
        self.assertEqual(receipt['state'], 'FAILED')
        self.assertFalse(receipt['result']['native_verified'])
        self.assertIn('table-uuid', self.w.metadata(self.created))
        self.assertEqual(len(self.w.posts), 1)
        self.assertTrue(self.run_native()['replayed'])
        self.assertEqual(len(self.w.posts), 1)

    def test_cancel_after_create_keeps_generic_observation_unverified(self):
        runner = native._run_native
        def cancel(artifacts, uid, bridge):
            result = runner(artifacts, uid, bridge)
            bridge.execution.cancel()
            return result
        with patch.object(native, '_run_native', cancel), self.assertRaises(QueryError):
            self.run_native()
        receipt = self.receipt()
        self.assertEqual(receipt['state'], 'CANCELED')
        self.assertFalse(receipt['result']['native_verified'])
        self.assertTrue(self.run_native()['replayed'])
        self.assertEqual(len(self.w.posts), 1)
        with control_store.connect() as db:
            self.assertFalse(admission.blockers(db, self.context.warehouse_id))

    def test_owner_loss_after_create_retains_unverified_candidate(self):
        runner = native._run_native
        def lose(artifacts, uid, bridge):
            runner(artifacts, uid, bridge)
            # Model process-lease loss. Abrupt real process death is Commander proof.
            ds._live.pop(bridge.execution.id)[2].close()
            raise QueryError('DBT_OWNER_LOST', 409)
        with patch.object(native, '_run_native', lose), self.assertRaises(QueryError):
            self.run_native()
        receipt = self.receipt()
        self.assertEqual(receipt['state'], 'UNKNOWN')
        self.assertFalse(receipt['result']['native_verified'])
        locator = receipt['result']['candidate_locator']
        self.assertTrue(any(k[2] == locator['physical_name'] for k in self.b.objects))
        self.assertTrue(self.run_native()['replayed'])
        self.assertEqual(len(self.w.posts), 1)
        settled = ds.Execution(self.principal, receipt['id']).reconcile_failure()
        self.assertEqual(settled['state'], 'FAILED')
        self.assertTrue(settled['committed'])
        self.assertFalse(settled['result']['native_verified'])
        self.assertEqual(len(self.w.posts), 1)

    def test_runner_exception_after_create_retains_unverified_candidate(self):
        runner = native._run_native
        def fail(artifacts, uid, bridge):
            runner(artifacts, uid, bridge)
            raise RuntimeError('runner failed after callback')
        with patch.object(native, '_run_native', fail), self.assertRaises(RuntimeError):
            self.run_native()
        receipt = self.receipt()
        self.assertEqual(receipt['state'], 'FAILED')
        self.assertTrue(receipt['committed'])
        self.assertFalse(receipt['result']['native_verified'])
        self.assertEqual(len(self.w.posts), 1)
        with patch.object(native, '_run_native', side_effect=AssertionError('no runner')):
            self.assertTrue(self.run_native()['replayed'])

    def test_metadata_observe_exception_after_create_retains_unverified_candidate(self):
        with patch.object(ds.Execution, '_observe', side_effect=QueryError('METADATA_UNAVAILABLE', 503)), \
                self.assertRaises(QueryError):
            self.run_native()
        receipt = self.receipt()
        self.assertEqual(receipt['state'], 'FAILED')
        self.assertTrue(receipt['committed'])
        self.assertFalse(receipt['result']['native_verified'])
        locator = receipt['result']['candidate_locator']
        self.assertTrue(any(k[2] == locator['physical_name'] for k in self.b.objects))
        self.assertTrue(self.run_native()['replayed'])
        self.assertEqual(len(self.w.posts), 1)

    def test_repeated_callback_after_real_macro_and_cleanup_are_blocked(self):
        runner = native._run_native
        def repeat(artifacts, uid, bridge):
            runner(artifacts, uid, bridge)
            with self.assertRaises(QueryError):
                bridge.execute('DROP TABLE anything')
            self.assertTrue(bridge.aborted)
            bridge.execute('SELECT 1')
        with patch.object(native, '_run_native', repeat), self.assertRaises(QueryError):
            self.run_native()
        receipt = self.receipt()
        self.assertEqual(receipt['state'], 'FAILED')
        self.assertFalse(receipt['result']['native_verified'])
        self.assertEqual([q['step'] for q in receipt['queries']], ['native-create'])
        self.assertEqual(len(self.w.posts), 1)

    def test_snapshots_are_captured_once_while_later_append_is_harmless(self):
        runner = native._run_native
        captured = self.w.metadata(self.source)['current-snapshot-id']
        def append(artifacts, uid, bridge):
            self.assertEqual(admission._held.mode, 'exclusive')
            self.w.snapshot(self.w.metadata(self.source), [(1, 'one'), (2, 'two'), (3, 'three')])
            return runner(artifacts, uid, bridge)
        with patch.object(native, '_run_native', append):
            receipt = self.run_native()
        sql = self.w.posts[0][0]
        self.assertIn('FOR VERSION AS OF ' + str(captured), sql)
        candidate = receipt['result']['candidate']
        metadata = self.w.metadata(registry.ResourceRef(**candidate['ref']))
        self.assertEqual(len(self.w.snapshots[(metadata['table-uuid'], int(candidate['snapshot']))]), 2)

    def test_source_schema_change_before_or_after_create_never_verifies(self):
        runner = native._run_native
        def change_schema():
            self.w.metadata(self.source)['schemas'][0]['fields'][0]['required'] = True
        def before(artifacts, uid, bridge):
            change_schema()
            return runner(artifacts, uid, bridge)
        with patch.object(native, '_run_native', before), self.assertRaises(QueryError):
            self.run_native('before')
        self.assertEqual(self.w.posts, [])
        self.w.metadata(self.source)['schemas'][0]['fields'][0]['required'] = False
        def after(artifacts, uid, bridge):
            result = runner(artifacts, uid, bridge)
            change_schema()
            return result
        with patch.object(native, '_run_native', after), self.assertRaises(QueryError):
            self.run_native('after')
        self.assertEqual(len(self.w.posts), 1)
        self.assertFalse(ds.Execution(self.principal, self.last_operation()).inspect()['result']['native_verified'])

    def last_operation(self):
        with control_store.connect() as db:
            return db.execute('SELECT id FROM dataops_operations ORDER BY created_at DESC LIMIT 1').fetchone()[0]

    def test_idle_requirement_and_replay_even_after_plan_history_changes(self):
        other = ds.begin(self.principal, self.context, route='other', idempotency_key='busy', payload={})
        with self.assertRaises(QueryError) as caught:
            self.run_native()
        self.assertEqual(caught.exception.code, 'DBT_NATIVE_WAREHOUSE_BUSY')
        self.assertEqual(self.compile.call_count, 0)
        other.cancel()
        receipt = self.run_native()
        self.mutate_plan(lambda p: p.update(state='FAILED', source_digest='new history'))
        with patch.object(native, '_plan', side_effect=AssertionError('must observe only')):
            self.assertEqual(self.run_native()['id'], receipt['id'])
        self.assertEqual(len(self.w.posts), 1)

    def test_existing_own_tmp_relation_blocks_instead_of_cleanup_or_empty_schema(self):
        runner = native._run_native
        def existing(artifacts, uid, bridge):
            self.w.existing(bridge.target[2] + suffix, schema='sandbox')
            return runner(artifacts, uid, bridge)
        for suffix in ('', '__dbt_tmp', '__dbt_backup'):
            with self.subTest(suffix=suffix), patch.object(native, '_run_native', existing), self.assertRaises(QueryError):
                self.run_native('collision' + suffix)
        self.assertEqual(self.w.posts, [])
        self.assertEqual(self.receipt()['state'], 'FAILED')

    def test_positive_ctas_with_failed_runner_never_verifies(self):
        from dbt.task.run import ModelRunner
        from dbt.artifacts.schemas.results import RunStatus
        execute = ModelRunner.execute
        def fail(instance, node, manifest):
            result = execute(instance, node, manifest)
            result.status = RunStatus.Error
            return result
        with patch.object(ModelRunner, 'execute', fail), self.assertRaises(QueryError):
            self.run_native()
        receipt = self.receipt()
        self.assertFalse(receipt['result']['native_verified'])
        self.assertEqual(len(self.w.posts), 1)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM dataops_owned_objects WHERE operation_id=?',
                                        (receipt['id'],)).fetchone()[0], 1)

    def test_lost_ack_no_runner_or_new_step_on_unknown_replay(self):
        transport = self.w.transport
        def lost(*args, **kwargs):
            transport(*args, **kwargs)
            raise OSError('lost ACK after CREATE')
        with patch.object(queries, '_transport', side_effect=lost), self.assertRaises(QueryError):
            self.run_native()
        receipt = self.receipt()
        self.assertEqual(receipt['state'], 'UNKNOWN')
        self.assertFalse(receipt['result']['native_verified'])
        self.assertEqual(receipt['queries'][0]['step'], 'native-create')
        self.assertEqual(receipt['queries'][0]['state'], 'UNKNOWN')
        # No matching backend evidence must never settle or trigger a retry.
        queries.reconcile_query(self.principal, receipt['queries'][0]['id'],
                                selected_role=self.context.selected_role, observer=lambda expected: [])
        with self.assertRaises(QueryError) as caught:
            ds.Execution(self.principal, receipt['id']).reconcile_failure()
        self.assertEqual(caught.exception.code, 'UNRESOLVED_CHILD_QUERIES')
        self.assertEqual(self.receipt()['state'], 'UNKNOWN')
        with patch.object(native, '_run_native', side_effect=AssertionError('replay')), \
                patch.object(native, '_idle', side_effect=AssertionError('idle')):
            self.assertTrue(self.run_native()['replayed'])
        self.assertEqual(len(self.w.posts), 1)

    def test_callback_reused_and_unknown_lookup_abort_before_sql(self):
        def invalid(artifacts, uid, bridge):
            self.assertIsNone(bridge.relation(*bridge.target))
            self.assertIsNone(bridge.relation(*bridge.target[:2], bridge.target[2] + '__dbt_tmp'))
            self.assertIsNone(bridge.relation(*bridge.target[:2], bridge.target[2] + '__dbt_backup'))
            with self.assertRaises(QueryError):
                bridge.relation(*bridge.target[:2], 'unknown')
            self.assertTrue(bridge.aborted)
            bridge.execute('SELECT 1')
        with patch.object(native, '_run_native', invalid), self.assertRaises(QueryError):
            self.run_native()
        self.assertEqual(len(self.w.posts), 0)
        self.assertEqual(self.receipt()['state'], 'FAILED')

    def test_wrong_thread_capability_fails_closed(self):
        def invalid(artifacts, uid, bridge):
            errors = []
            def child():
                try:
                    bridge.relation(*bridge.target)
                except QueryError as error:
                    errors.append(error.code)
            thread = threading.Thread(target=child)
            thread.start()
            thread.join()
            self.assertEqual(errors, ['DBT_NATIVE_CALLBACK_SCOPE'])
            bridge.relation(*bridge.target)
        with patch.object(native, '_run_native', invalid), self.assertRaises(QueryError):
            self.run_native()
        self.assertEqual(self.w.posts, [])

    def test_inventory_rejects_other_database_schema_or_identifier(self):
        def invalid(artifacts, uid, bridge):
            args = list(bridge.target)
            args[index] = 'other'
            with self.assertRaises(QueryError) as caught:
                bridge.relation(*args)
            self.assertEqual(caught.exception.code, 'DBT_NATIVE_RELATION_LOOKUP')
            bridge.execute('SELECT 1')
        for index in range(3):
            with self.subTest(index=index), patch.object(native, '_run_native', invalid), self.assertRaises(QueryError):
                self.run_native('lookup-' + str(index))
        self.assertEqual(self.w.posts, [])

    def test_current_full_context_actor_digest_and_materialized_dependencies(self):
        for context in (replace(self.context, grant_revision=self.context.grant_revision + 1),
                        replace(self.context, generation_id='different')):
            with self.subTest(context=context), self.assertRaises((QueryError, PermissionError)):
                native.materialize(self.principal, context, plan_id=self.plan_id, node_id=self.uid, key='other')
        with self.assertRaises((QueryError, PermissionError)):
            native.materialize(self.w.owner, self.b.context(schema='sandbox', principal=self.w.owner, role='admin'),
                plan_id=self.plan_id, node_id=self.uid, key='admin')
        self.mutate_plan(lambda p: p['models'][0].update(materialized='view'))
        with self.assertRaises(QueryError):
            self.run_native()
        self.mutate_plan(lambda p: p.update(_plan_digest=db_dbt._hash_plan(p)))
        with self.assertRaises(QueryError) as caught:
            self.run_native()
        self.assertEqual(caught.exception.code, 'DBT_NATIVE_MATERIALIZED_DEPENDENCY')
        self.assertEqual(self.w.posts, [])

    def normal_run(self, key='normal', *, execute=True):
        with patch.object(db_dbt, '_start'):
            run = db_dbt.submit_run(self.principal, self.context, key=key,
                plan_id=self.plan_id, executor='native_table', node_id=self.uid)
        if execute:
            thread = threading.Thread(target=db_dbt._execute, args=(run['id'],))
            thread.start()
            thread.join(timeout=45)
            self.assertFalse(thread.is_alive())
            run = db_dbt.get_run(self.principal, self.context, run['id'])
        return run

    def test_normal_installed_subprocess_owns_callbacks_and_original_credentials(self):
        from dbt.flags import get_flags
        from dbt.adapters.factory import FACTORY
        flags, adapters = get_flags(), dict(FACTORY.adapters)
        environment = dict(os.environ)
        transport = self.w.transport
        seen = []
        popen = subprocess.Popen
        def sdk_child(args, **kwargs):
            env = kwargs['env']
            self.assertFalse(any(k.startswith('SHIROKUMA') for k in env))
            self.assertEqual(env, self.artifacts[3])
            self.assertNotIn('original-secret', repr(args))
            self.assertIs(kwargs['stderr'], subprocess.DEVNULL)
            return popen(args, **kwargs)
        def post(url, method, pin, body=None):
            with control_store.connect() as db:
                op = db.execute("SELECT o.*,l.node_id,l.phase,l.run_id FROM dataops_operations o "
                    "JOIN dbt_run_operations l ON l.operation_id=o.id WHERE o.route='dbt-native-table'").fetchone()
                self.assertEqual(op['phase'], 'build')
                self.assertEqual(op['node_id'], self.uid)
                self.assertEqual(op['actor'], self.principal.principal_id)
                self.assertEqual(json.loads(op['context']), asdict(self.context))
                self.assertTrue(db.execute('SELECT 1 FROM dataops_queries WHERE operation_id=?', (op['id'],)).fetchone())
                self.assertIs(ds._live[op['id']][0], db_dbt._live[op['run_id']][3])
                self.assertEqual(op['credential_snapshot_ref'], db_dbt._live[op['run_id']][4])
                seen.append(op['id'])
            self.assertEqual(threading.current_thread().name.startswith('Thread-'), True)
            return transport(url, method, pin, body)
        with patch.object(native, '_runtime', side_effect=AssertionError('parent SDK')), \
                patch.object(native, '_idle', side_effect=AssertionError('parent idle')), \
                patch.object(native.subprocess, 'Popen', side_effect=sdk_child), \
                patch.object(queries, '_transport', side_effect=post), \
                patch.object(db_dbt, '_before_query', wraps=db_dbt._before_query) as final_send:
            run = self.normal_run()
        self.assertTrue(final_send.called)
        self.assertEqual(run['state'], 'NATIVE_VERIFIED', run)
        self.assertTrue(run['native_verified'])
        self.assertFalse(run['publication_allowed'])
        self.assertEqual(run['tests_status'], 'NOT_RUN')
        self.assertEqual(run['tests'], [])
        self.assertEqual([m['unique_id'] for m in run['models']], [self.uid])
        self.assertNotIn('build_id', run['models'][0])
        self.assertEqual(run['models'][0]['operation_ids'], seen)
        self.assertTrue(run['models'][0]['candidate']['snapshot'])
        self.assertIs(get_flags(), flags)
        self.assertEqual(FACTORY.adapters, adapters)
        self.assertEqual(dict(os.environ), environment)
        self.assertEqual(len(self.w.posts), 1)
        with control_store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM wap_builds').fetchone()[0], 0)
        self.mutate_plan(lambda p: p.update(state='FAILED', source_digest='changed'))
        with patch.object(db_dbt, '_projects', side_effect=AssertionError('projects')), \
                patch.object(native, '_plan', side_effect=AssertionError('plan')), \
                patch.object(queries, '_capture_dataops', side_effect=AssertionError('capture')):
            self.assertEqual(self.normal_run(execute=False)['id'], run['id'])

    def test_normal_selects_only_table_even_with_unselected_view_and_models(self):
        self.artifact_patch.stop()
        config = yaml.safe_load((self.project / 'dbt_project.yml').read_text())
        config['models']['general_native'] = {'+materialized': 'table',
            'arbitrary_result': {'+properties': {'format': "'PARQUET'"}},
            'unselected_table': {'+properties': {'format': "'PARQUET'"}}}
        (self.project / 'dbt_project.yml').write_text(yaml.safe_dump(config))
        (self.project / 'models/unselected_view.sql').write_text(
            "{{ config(materialized='view') }} SELECT id, label FROM {{ source('registered', 'registered_input') }}")
        (self.project / 'models/unselected_table.sql').write_text(
            "SELECT id, label FROM {{ source('registered', 'registered_input') }}")
        with compiler._compiled_project(self.project, catalog=self.context.sql_catalog, source_schema='raw') as artifacts:
            self.artifacts = artifacts
            with patch.object(db_dbt, '_start'), patch.object(compiler, 'compile_project', return_value=artifacts[0]):
                plan = db_dbt.submit_plan(self.principal, self.context, key='mixed-plan', project_id='general',
                    source_schema='raw', target_schema='marts', staging_materialization=None)
                db_dbt._execute(plan['id'])
            self.plan_id = plan['id']
            self.artifact_patch.start()
            run = self.normal_run()
        self.assertEqual(run['state'], 'NATIVE_VERIFIED', run)
        self.assertEqual([m['unique_id'] for m in run['models']], [self.uid])
        self.assertEqual(len(self.w.posts), 1)
        self.assertTrue(self.w.posts[0][0].startswith('CREATE TABLE '))

    def test_normal_sdk_child_eof_invalid_protocol_and_target_never_send(self):
        popen = subprocess.Popen
        scripts = ["pass", "print('not json')", "print('{\"op\":\"get_relation\",\"database\":\"foreign\",\"schema\":\"raw\",\"identifier\":\"x\"}')"]
        for index, script in enumerate(scripts):
            children = []
            def child(args, **kwargs):
                args = [args[0], '-c', script]
                process = popen(args, **kwargs)
                children.append(process)
                return process
            with self.subTest(index=index), patch.object(native.subprocess, 'Popen', side_effect=child):
                run = self.normal_run('invalid-' + str(index))
            self.assertEqual(run['state'], 'FAILED', run)
            self.assertFalse(run['native_verified'])
            self.assertTrue(all(c.poll() is not None for c in children))
        self.assertEqual(self.w.posts, [])

    def test_normal_owned_child_failure_after_create_is_unverified_and_reaped(self):
        popen = subprocess.Popen
        scripts = [
            "import json,sys\nr=json.loads(sys.stdin.readline())\n"
            "sql=\"CREATE TABLE \"+'.'.join(r['target'])+\" WITH (format='PARQUET') AS (\"+r['manifest']['nodes'][r['node_id']]['compiled_code']+')'\n"
            "print(json.dumps(dict(op='execute',sql=sql,auto_begin=True,fetch=False,limit=None)),flush=True)\n"
            "sys.stdin.readline()\n" + end
            for end in ("sys.exit(0)", "print('invalid protocol',flush=True)",
                        "print(json.dumps(dict(op='done',status='error')),flush=True)",
                        "print(json.dumps(dict(op='done',status='success')),flush=True);print('null',flush=True)",
                        "print(json.dumps(dict(op='done',status='success')),flush=True);import time;time.sleep(30)")]
        for index, script in enumerate(scripts):
            children = []
            def child(args, **kwargs):
                process = popen([args[0], '-c', script], **kwargs)
                children.append(process)
                return process
            with self.subTest(index=index), patch.object(native.subprocess, 'Popen', side_effect=child), \
                    patch.object(native, '_SDK_TIMEOUT', 3):
                run = self.normal_run('after-create-' + str(index))
            self.assertEqual(run['state'], 'FAILED', run)
            self.assertFalse(run['native_verified'])
            self.assertTrue(run['operations'][0]['committed'])
            self.assertFalse(run['native_receipt']['native_verified'])
            self.assertTrue(run['models'][0]['candidate_locator'])
            self.assertTrue(all(c.poll() is not None for c in children))
            self.assertEqual(self.normal_run('after-create-' + str(index), execute=False)['id'], run['id'])
        self.assertEqual(len(self.w.posts), len(scripts))

    def test_parent_callbacks_do_not_consume_sdk_wait_budget(self):
        popen = subprocess.Popen
        execute, relation = native._Bridge.execute, native._Bridge.relation
        durations = []
        script = """
import json, sys
r = json.loads(sys.stdin.readline())
print(json.dumps(dict(op='get_relation', database=r['target'][0],
    schema=r['target'][1], identifier=r['target'][2])), flush=True)
assert json.loads(sys.stdin.readline()) == dict(result=None)
sql = 'CREATE TABLE ' + '.'.join(r['target']) + " WITH (format='PARQUET') AS (" + r['manifest']['nodes'][r['node_id']]['compiled_code'] + ')'
print(json.dumps(dict(op='execute', sql=sql, auto_begin=True, fetch=False, limit=None)), flush=True)
assert json.loads(sys.stdin.readline())['result']['code'] == 'CREATE TABLE'
print(json.dumps(dict(op='done', status='success')), flush=True)
"""
        def child(args, **kwargs):
            return popen([args[0], '-c', script], **kwargs)
        def slow(function):
            def call(*args, **kwargs):
                started = time.monotonic()
                time.sleep(.6)
                result = function(*args, **kwargs)
                durations.append(time.monotonic() - started)
                return result
            return call
        with patch.object(native.subprocess, 'Popen', side_effect=child), \
                patch.object(native, '_SDK_TIMEOUT', .5), \
                patch.object(native._Bridge, 'relation', slow(relation)), \
                patch.object(native._Bridge, 'execute', slow(execute)):
            run = self.normal_run()
        self.assertEqual(len(durations), 2)
        self.assertTrue(all(duration > .5 for duration in durations))
        self.assertEqual(run['state'], 'NATIVE_VERIFIED', run)
        self.assertTrue(run['native_verified'])
        self.assertEqual(len(self.w.posts), 1)

    def test_normal_failure_after_create_retains_candidate_no_resend(self):
        runner = native._run_subprocess
        def fail(*args):
            runner(*args)
            raise QueryError('DBT_NATIVE_RUNNER_FAILED', 409)
        with patch.object(native, '_run_subprocess', side_effect=fail):
            run = self.normal_run()
        self.assertEqual(run['state'], 'FAILED', run)
        self.assertFalse(run['native_verified'])
        self.assertFalse(run['native_receipt']['native_verified'])
        self.assertTrue(run['models'][0]['candidate_locator'])
        self.assertTrue(run['operations'][0]['committed'])
        with patch.object(native, '_run_subprocess', side_effect=AssertionError('resend')):
            self.assertEqual(self.normal_run(execute=False)['id'], run['id'])
        self.assertEqual(len(self.w.posts), 1)

    def test_normal_unknown_replay_without_plan_project_capture_or_sdk(self):
        transport = self.w.transport
        def lost(*args, **kwargs):
            transport(*args, **kwargs)
            raise OSError('lost create acknowledgement')
        with patch.object(queries, '_transport', side_effect=lost):
            run = self.normal_run()
        self.assertEqual(run['state'], 'UNKNOWN', run)
        self.assertFalse(run['native_verified'])
        self.assertTrue(run['models'][0]['candidate_locator'])
        self.mutate_plan(lambda p: p.update(state='FAILED', source_digest='changed'))
        with patch.object(native, '_plan', side_effect=AssertionError('plan')), \
                patch.object(db_dbt, '_projects', side_effect=AssertionError('project')), \
                patch.object(queries, '_capture_dataops', side_effect=AssertionError('capture')), \
                patch.object(native, '_run_subprocess', side_effect=AssertionError('SDK')):
            self.assertEqual(self.normal_run(execute=False)['state'], 'UNKNOWN')
        self.assertEqual(len(self.w.posts), 1)

    def test_normal_cancel_and_owner_loss_after_create_do_not_verify(self):
        runner = native._run_subprocess
        for key in ('cancel', 'owner-loss'):
            def stop(artifacts, uid, bridge):
                runner(artifacts, uid, bridge)
                if key == 'cancel':
                    with control_store.connect() as db:
                        link = db.execute('SELECT run_id FROM dbt_run_operations WHERE operation_id=?', (bridge.execution.id,)).fetchone()
                    db_dbt.cancel_run(self.principal, self.context, link['run_id'], key='cancel-native')
                else:
                    ds._live.pop(bridge.execution.id)[2].close()
                    raise QueryError('DBT_OWNER_LOST', 409)
            with patch.object(native, '_run_subprocess', side_effect=stop):
                run = self.normal_run(key)
            self.assertEqual(run['state'], 'CANCELED' if key == 'cancel' else 'FAILED', run)
            self.assertFalse(run['native_verified'])
            self.assertTrue(run['models'][0]['candidate_locator'])
            if key == 'owner-loss':
                self.assertTrue(run['operations'][0]['committed'])
                self.assertFalse(run['native_receipt']['native_verified'])
                observed = db_dbt.reconcile_run(self.principal, self.context, run['id'], key='reconcile-native')
                self.assertEqual(observed['state'], 'FAILED')
                self.assertFalse(observed['native_verified'])
        self.assertEqual(len(self.w.posts), 2)

    def test_normal_parent_pins_precede_bridge_even_when_source_appends(self):
        captured = self.w.metadata(self.source)['current-snapshot-id']
        constructor = native._Bridge.__init__
        def append(bridge, *args, **kwargs):
            self.w.snapshot(self.w.metadata(self.source), [(1, 'one'), (2, 'two'), (3, 'three')])
            constructor(bridge, *args, **kwargs)
            self.assertEqual(next(iter(bridge.inputs.values()))['snapshot'], str(captured))
        with patch.object(native._Bridge, '__init__', append):
            run = self.normal_run()
        self.assertEqual(run['state'], 'NATIVE_VERIFIED', run)
        self.assertIn('FOR VERSION AS OF ' + str(captured), self.w.posts[0][0])
        candidate = run['models'][0]['candidate']
        metadata = self.w.metadata(registry.ResourceRef(**candidate['ref']))
        self.assertEqual(len(self.w.snapshots[(metadata['table-uuid'], int(candidate['snapshot']))]), 2)

    def test_normal_stalled_and_partial_sdk_and_blocked_input_are_cancelable(self):
        popen = subprocess.Popen
        scripts = ["import sys,time;sys.stdin.readline();time.sleep(30)",
                   "import sys,time;sys.stdin.readline();sys.stdout.write('{');sys.stdout.flush();time.sleep(30)",
                   "import time;time.sleep(30)"]
        for index, script in enumerate(scripts):
            children, timers, errors = [], [], []
            def cancel():
                try:
                    with control_store.connect() as db:
                        ident = db.execute("SELECT l.run_id FROM dbt_run_operations l JOIN dataops_operations o "
                            "ON o.id=l.operation_id WHERE o.state='ACTIVE'").fetchone()[0]
                    db_dbt.cancel_run(self.principal, self.context, ident, key='stalled-cancel-' + str(index))
                except BaseException as error:
                    errors.append(error)
            def child(args, **kwargs):
                process = popen([args[0], '-c', script], **kwargs)
                children.append(process)
                timer = threading.Timer(.5, cancel)
                timers.append(timer)
                timer.start()
                return process
            raw = deepcopy(self.artifacts[1])
            raw['padding'] = 'x' * (2 * 1024 * 1024)
            artifacts = (self.artifacts[0], raw, *self.artifacts[2:])
            original = native._run_subprocess
            def padded(_, uid, bridge):
                return original(artifacts, uid, bridge)
            with self.subTest(index=index), patch.object(native.subprocess, 'Popen', side_effect=child), \
                    patch.object(native, '_run_subprocess', side_effect=padded), patch.object(native, '_SDK_TIMEOUT', 4):
                run = self.normal_run('stall-' + str(index))
            for timer in timers:
                timer.join()
            self.assertEqual(errors, [])
            self.assertEqual(run['state'], 'CANCELED', run)
            self.assertFalse(run['native_verified'])
            self.assertTrue(all(c.poll() is not None for c in children))
            with control_store.connect() as db:
                parent = admission.get_for_subject(db, kind='dbt', subject_id=run['id'], warehouse_id=self.context.warehouse_id)
                self.assertEqual(admission.check_execution(db, parent)['state'], 'FINISHED')
        self.assertEqual(self.w.posts, [])

    def test_normal_sdk_partial_reply_times_out_without_sql(self):
        popen = subprocess.Popen
        children = []
        def child(args, **kwargs):
            process = popen([args[0], '-c',
                "import sys,time;sys.stdin.readline();sys.stdout.write('{');sys.stdout.flush();time.sleep(30)"], **kwargs)
            children.append(process)
            return process
        with patch.object(native.subprocess, 'Popen', side_effect=child), patch.object(native, '_SDK_TIMEOUT', .5):
            run = self.normal_run()
        self.assertEqual(run['state'], 'FAILED', run)
        self.assertFalse(run['native_verified'])
        self.assertEqual(self.w.posts, [])
        self.assertTrue(all(c.poll() is not None for c in children))

    def test_native_settle_rechecks_canonical_saved_proof(self):
        run = self.normal_run()
        self.assertEqual(run['state'], 'NATIVE_VERIFIED', run)
        op_id = run['models'][0]['operation_ids'][0]
        with control_store.connect() as db:
            saved_run = db.execute('SELECT record FROM dbt_runs WHERE id=?', (run['id'],)).fetchone()[0]
            saved_op = dict(db.execute('SELECT * FROM dataops_operations WHERE id=?', (op_id,)).fetchone())
            self.assertIsNotNone(db_dbt._native_saved(db, json.loads(saved_run), op_id))
            self.assertIsNone(db_dbt._native_saved(db, json.loads(saved_run), 'wrong-operation'))
        mutations = [lambda r: r.update(native_verified=False), lambda r: r.pop('native_verified'),
                     lambda r: r.update(digest='wrong'), lambda r: r.update(node_id='wrong'),
                     lambda r: r['candidate']['ref'].update(object_id=self.source.object_id),
                     lambda r: r['candidate'].update(snapshot='wrong')]
        for mutate in mutations:
            receipt = json.loads(saved_op['result'])
            mutate(receipt)
            with control_store.connect() as db:
                db.execute('UPDATE dbt_runs SET record=? WHERE id=?', (saved_run, run['id']))
                db.execute('UPDATE dataops_operations SET result=? WHERE id=?', (ds._json(receipt), op_id))
            with self.subTest(receipt=receipt), patch.object(db_dbt, '_access', side_effect=AssertionError('no metadata')):
                db_dbt._settle(run['id'])
            observed = db_dbt._row_run(run['id'])
            self.assertEqual(observed['state'], 'FAILED')
            self.assertFalse(observed['native_verified'])
            self.assertFalse(observed['native_receipt']['native_verified'])
            self.assertTrue(observed['models'][0]['candidate'])
        for result in ('not json', 'null', '[]'):
            with control_store.connect() as db:
                db.execute('UPDATE dbt_runs SET record=? WHERE id=?', (saved_run, run['id']))
                db.execute('UPDATE dataops_operations SET result=? WHERE id=?', (result, op_id))
            db_dbt._settle(run['id'])
            self.assertFalse(db_dbt._row_run(run['id'])['native_verified'])
        for field, value in (('phase', 'test'), ('node_id', 'wrong')):
            with control_store.connect() as db:
                db.execute('UPDATE dbt_runs SET record=? WHERE id=?', (saved_run, run['id']))
                db.execute('UPDATE dataops_operations SET result=? WHERE id=?', (saved_op['result'], op_id))
                db.execute('UPDATE dbt_run_operations SET ' + field + '=? WHERE operation_id=?', (value, op_id))
            db_dbt._settle(run['id'])
            self.assertFalse(db_dbt._row_run(run['id'])['native_verified'])
            with control_store.connect() as db:
                db.execute("UPDATE dbt_run_operations SET phase='build',node_id=? WHERE operation_id=?", (self.uid, op_id))
        with control_store.connect() as db:
            db.execute('UPDATE dbt_runs SET record=? WHERE id=?', (saved_run, run['id']))
            db.execute('UPDATE dataops_operations SET result=? WHERE id=?', (saved_op['result'], op_id))
            db.execute("UPDATE dataops_owned_objects SET purpose='backup' WHERE operation_id=?", (op_id,))
        db_dbt._settle(run['id'])
        self.assertFalse(db_dbt._row_run(run['id'])['native_verified'])

    def test_native_saved_rejects_tampered_query_evidence(self):
        run = self.normal_run()
        self.assertEqual(run['state'], 'NATIVE_VERIFIED', run)
        run = db_dbt._row_run(run['id'])
        op_id = run['models'][0]['operation_ids'][0]
        with control_store.connect() as db:
            query_id = db.execute('SELECT query_id FROM dataops_queries WHERE operation_id=?', (op_id,)).fetchone()[0]
        changes = [
            ('dataops_queries', 'step', 'wrong'),
            ('dataops_queries', 'action', 'wrong'),
            ('query_records', 'state', 'RUNNING'),
            ('query_records', 'actor', 'wrong'),
            ('query_execution_contexts', 'backend_query_id', None),
            ('query_execution_contexts', 'backend_query_id', ''),
            ('query_execution_contexts', 'backend_query_id', ' '),
            ('query_execution_contexts', 'backend_username', 'wrong'),
            ('query_execution_contexts', 'credential_snapshot_ref', 'wrong'),
            ('query_execution_contexts', 'selected_role', 'wrong'),
            ('query_execution_contexts', 'mode', 'worksheet'),
            ('query_execution_contexts', 'sql_catalog', 'wrong'),
            ('query_execution_contexts', 'sql_schema', 'wrong'),
            ('query_execution_contexts', 'binding_revision', -1),
            ('query_execution_contexts', 'grant_revision', -1),
            ('query_execution_contexts', 'request_id', 'wrong'),
            ('query_execution_contexts', 'worksheet_id', 'wrong'),
        ]
        with control_store.connect() as db, \
                patch.object(queries, '_capture_dataops', side_effect=AssertionError('no credential capture')), \
                patch.object(db_dbt, '_access', side_effect=AssertionError('no runtime metadata')):
            self.assertIsNotNone(db_dbt._native_saved(db, run, op_id))
            for table, field, value in changes:
                key = 'operation_id' if table == 'dataops_queries' else 'id' if table == 'query_records' else 'query_id'
                ident = op_id if table == 'dataops_queries' else query_id
                with self.subTest(table=table, field=field, value=value):
                    db.execute('SAVEPOINT tamper')
                    db.execute(f'UPDATE {table} SET {field}=? WHERE {key}=?', (value, ident))
                    self.assertIsNone(db_dbt._native_saved(db, run, op_id))
                    db.execute('ROLLBACK TO tamper')
                    db.execute('RELEASE tamper')
            db.execute('DELETE FROM dataops_queries WHERE operation_id=?', (op_id,))
            self.assertIsNone(db_dbt._native_saved(db, run, op_id))

    def test_native_cleanup_receipt_redacts_saved_evidence(self):
        run = self.normal_run(execute=False)
        record = db_dbt._row_run(run['id'])
        record.update(native_receipt={'candidate': 'private', 'pins': 'private'},
                      candidate='private', candidate_locator='private', pins='private',
                      _pins={'private': 'private'})
        with control_store.connect() as db, patch.object(db_dbt, '_access', side_effect=QueryError('REVOKED', 403)):
            redacted = db_dbt._cancel_receipt(db, self.principal, self.context, record)
        for key in ('native_receipt', 'candidate', 'candidate_locator', 'pins', '_pins'):
            self.assertNotIn(key, redacted)
        for key in ('models', 'tests', 'operations', 'operation_ids'):
            self.assertEqual(redacted[key], [])

    def test_native_finish_reads_persisted_result_instead_of_return_value(self):
        materialize = native.materialize_accepted
        def tamper(*args, **kwargs):
            child = materialize(*args, **kwargs)
            receipt = dict(child['result'], digest='changed')
            with control_store.connect() as db:
                db.execute('UPDATE dataops_operations SET result=? WHERE id=?', (ds._json(receipt), child['id']))
            return child
        with patch.object(native, 'materialize_accepted', side_effect=tamper):
            run = self.normal_run()
        self.assertEqual(run['state'], 'FAILED', run)
        self.assertFalse(run['native_verified'])
        self.assertTrue(run['operations'][0]['committed'])
        self.assertTrue(run['models'][0]['candidate'])


if __name__ == '__main__':
    unittest.main()
