"""Private dbt feasibility boundary; no runtime or backend queries."""
import copy
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml
from jinja2 import Environment

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))

import database_dbt as dbt
import data_management as registry
import manual_wap


PROJECT = Path(__file__).resolve().parents[1] / 'tools/local_experiment/dataops/dbt_persistent'


def contents(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


class NormalizeTests(unittest.TestCase):
    def ref(self, name='orders', database='db1'):
        return registry.ResourceRef(database, 'raw', name, 1)

    def test_catalogs_with_identical_names_remain_independent(self):
        for catalog, database in [('cat_a', 'db1'), ('cat_b', 'db2')]:
            ref = self.ref(database=database)
            result = dbt.normalize_select(f'SELECT o.id FROM {catalog}.raw.orders o',
                                          {(catalog, 'raw', 'orders'): ref}, database_id=database)
            self.assertEqual(list(result['sources'].values()), [ref])
            self.assertNotIn(catalog + '.', result['sql'])
            manual_wap._compile_model(**result)

    def test_cte_ephemeral_join_quoted_and_qualified_columns(self):
        sql = '''WITH "__dbt__cte__e" AS
          (SELECT "Order ID" FROM "Cat"."Raw"."Orders")
          SELECT e."Order ID", o."Order ID" FROM "__dbt__cte__e" e
          JOIN "Cat"."Raw"."Orders" o ON e."Order ID" = o."Order ID"'''
        binding = {('Cat', 'Raw', 'Orders'): self.ref()}
        result = dbt.normalize_select(sql, binding, database_id='db1')
        self.assertIn('"Order ID"', result['sql'])
        self.assertEqual(len(result['sources']), 1)
        manual_wap._compile_model(**result)
        result = dbt.normalize_select('SELECT "Cat"."Raw"."Orders"."Order ID" FROM "Cat"."Raw"."Orders"',
                                      binding, database_id='db1')
        manual_wap._compile_model(**result)

    def test_alias_collision_avoided_and_deterministic(self):
        sql = 'WITH source_0 AS (SELECT id FROM cat.raw.orders) SELECT * FROM source_0'
        bindings = {('cat', 'raw', 'orders'): self.ref()}
        one = dbt.normalize_select(sql, bindings, database_id='db1')
        self.assertEqual(one, dbt.normalize_select(sql, bindings, database_id='db1'))
        self.assertNotIn('source_0', one['sources'])
        manual_wap._compile_model(**one)

    def test_generic_failure_row_selects(self):
        binding = {('cat', 'sandbox', 'orders'): self.ref()}
        queries = [
            'select id, count(*) as n from cat.sandbox.orders group by id having count(*) > 1',
            'select id from cat.sandbox.orders where id is null',
            "select status from cat.sandbox.orders where status not in ('O', 'F', 'P')",
            '''with child as (select custkey from cat.sandbox.orders where custkey is not null),
                parent as (select custkey from cat.sandbox.customers)
                select child.custkey from child left join parent
                on child.custkey = parent.custkey where parent.custkey is null''',
        ]
        for sql in queries:
            bindings = dict(binding)
            if 'sandbox.customers' in sql:
                bindings[('cat', 'sandbox', 'customers')] = self.ref('customers')
            with self.subTest(sql=sql):
                result = dbt.normalize_select(sql, bindings, database_id='db1')
                manual_wap._compile_model(**result)

    def test_duplicate_resource_identity_is_rejected(self):
        bindings = {('cat', 'raw', 'orders'): self.ref(),
                    ('cat', 'raw', 'other'): self.ref()}
        with self.assertRaises(dbt.DbtError):
            dbt.normalize_select('SELECT * FROM cat.raw.orders o JOIN cat.raw.other x ON o.id=x.id',
                                 bindings, database_id='db1')

    def test_reject_cross_database_unbound_unused_and_nonread(self):
        cases = [
            ('SELECT * FROM cat.raw.orders', {('cat', 'raw', 'orders'): self.ref(database='db2')}),
            ('SELECT * FROM cat.raw.missing', {('cat', 'raw', 'orders'): self.ref()}),
            ('SELECT 1', {('cat', 'raw', 'orders'): self.ref()}),
            ('DELETE FROM cat.raw.orders', {('cat', 'raw', 'orders'): self.ref()}),
            ('SELECT * FROM orders', {('cat', 'raw', 'orders'): self.ref()}),
            ('SELECT * FROM cat.information_schema.tables',
             {('cat', 'information_schema', 'tables'): self.ref()}),
        ]
        for sql, bindings in cases:
            with self.subTest(sql=sql), self.assertRaises(dbt.DbtError):
                dbt.normalize_select(sql, bindings, database_id='db1')


class CompileBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'project'
        shutil.copytree(PROJECT, self.project)

    def test_legacy_project_defaults_are_preserved(self):
        env = Environment()
        variables = lambda name, default=None: default
        project = yaml.safe_load(env.from_string((self.project / 'dbt_project.yml').read_text())
                                 .render(var=variables))
        models = project['models']['shirokuma_lake_marts']
        self.assertEqual(models['staging']['+materialized'], 'view')
        self.assertEqual(models['marts']['+materialized'], 'table')
        source = yaml.safe_load(env.from_string((self.project / 'models/sources.yml').read_text())
                                .render(var=variables))['sources'][0]
        self.assertEqual((source['database'], source['schema']), ('iceberg', 'tpch_tiny'))
        macro = env.from_string((self.project / 'macros/generate_schema_name.sql').read_text())
        module = macro.make_module({'var': variables, 'target': {'schema': 'fallback'}})
        for schema in ('staging', 'marts'):
            self.assertEqual(module.generate_schema_name(schema, None).strip(), schema)
        module = macro.make_module({'var': lambda key, default=None: 'sandbox',
                                    'target': {'schema': 'fallback'}})
        self.assertEqual(module.generate_schema_name('marts', None).strip(), 'sandbox')

    def test_installed_builtin_default_views_and_ephemeral_typed_format(self):
        summary = dbt.compile_project(self.project, catalog='cat', source_schema='raw')
        self.assertEqual(summary['compatibility'], dbt.compatibility())
        views = [m for m in summary['models'] if m['materialized'] == 'view']
        self.assertTrue(views)
        self.assertTrue(all(m['view_security'] == 'INVOKER' for m in views))
        self.assertTrue(all(m['relation'] and m['table_properties'] == {} for m in views))
        summary = dbt.compile_project(self.project, catalog='cat', source_schema='raw', staging_materialization='ephemeral')
        self.assertEqual(set(summary['test_owners']), {t['unique_id'] for t in summary['tests']})
        owners = summary['test_owners']
        dim = next(m['unique_id'] for m in summary['models'] if m['name'] == 'dim_customers')
        relationships = [t for t in summary['tests'] if t['test_type'] == 'relationships']
        self.assertTrue(relationships)
        self.assertTrue(any(dim in t['depends_on'] and owners[t['unique_id']] != dim for t in relationships))
        tables = [m for m in summary['models'] if m['materialized'] == 'table']
        self.assertTrue(tables)
        self.assertTrue(all(m['table_properties'] == {'format': 'PARQUET'} for m in tables))

    def test_preflight_rejects_execution_features_before_subprocess(self):
        cases = [
            ('models/unsafe.py', 'print(1)'),
            ('seeds/input.csv', 'a\n1\n'),
            ('snapshots/s.sql', 'select 1'),
            ('packages.yml', 'packages:\n  - package: dbt-labs/dbt_utils\n    version: 1.0.0\n'),
            ('dependencies.yml', 'projects:\n  - name: external\n'),
            ('macros/custom.sql', '{% materialization custom, default %} select 1 {% endmaterialization %}'),
            ('models/hook.sql', "{{ config(pre_hook='select 1') }} select 1"),
            ('models/hook.sql', "{{ config(store_failures=true) }} select 1"),
        ]
        for path, text in cases:
            with self.subTest(path=path):
                file = self.project / path
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(text)
                with patch.object(dbt.subprocess, 'run') as run, self.assertRaises(dbt.DbtError):
                    dbt.compile_project(self.project, catalog='cat', source_schema='raw')
                run.assert_not_called()
                file.unlink()

    def test_digest_matches_captured_sources_when_originals_change(self):
        preflight = dbt._preflight
        captured = {}
        model = self.project / 'models/staging/stg_orders.sql'

        def capture_then_change(project):
            config, files, digest = preflight(project)
            captured.update(files)
            model.write_text('select 999 as changed_after_preflight\n')
            (project / 'dbt_project.yml').write_text('name: changed_after_preflight\n')
            return config, files, digest

        def compile_snapshot(command, **kwargs):
            copied = Path(command[command.index('--project-dir') + 1])
            for rel, raw in captured.items():
                if rel != 'dbt_project.yml':
                    self.assertEqual((copied / rel).read_bytes(), raw)
            config = yaml.safe_load((copied / 'dbt_project.yml').read_bytes())
            self.assertEqual(config['name'], yaml.safe_load(captured['dbt_project.yml'])['name'])
            target = Path(command[command.index('--target-path') + 1])
            target.mkdir()
            (target / 'manifest.json').write_text('{}')
            return subprocess.CompletedProcess(command, 0, '', '')

        with patch.object(dbt, '_preflight', side_effect=capture_then_change), \
                patch.object(dbt.subprocess, 'run', side_effect=compile_snapshot), \
                patch.object(dbt, '_validate_manifest', return_value={}):
            result = dbt.compile_project(self.project, catalog='cat', source_schema='raw')
        digest = hashlib.sha256()
        for rel, raw in sorted(captured.items()):
            digest.update(rel.encode() + b'\0' + raw + b'\0')
        self.assertEqual(result['source_digest'], digest.hexdigest())
        self.assertEqual(model.read_text(), 'select 999 as changed_after_preflight\n')
        self.assertEqual((self.project / 'dbt_project.yml').read_text(),
                         'name: changed_after_preflight\n')

    def test_project_config_is_read_once_for_validation_and_digest(self):
        original_read = Path.read_bytes
        project_file = self.project / 'dbt_project.yml'
        original_bytes = project_file.read_bytes()
        reads = []

        def read_then_change(path):
            raw = original_read(path)
            if path == project_file:
                reads.append(path)
                project_file.write_text('name: changed_after_read\n')
            return raw

        with patch.object(Path, 'read_bytes', read_then_change):
            config, files, digest = dbt._preflight(self.project)
        self.assertEqual(reads, [project_file])
        self.assertEqual(files['dbt_project.yml'], original_bytes)
        self.assertEqual(config['name'], yaml.safe_load(original_bytes)['name'])
        expected = hashlib.sha256()
        for rel, raw in sorted(files.items()):
            expected.update(rel.encode() + b'\0' + raw + b'\0')
        self.assertEqual(digest, expected.hexdigest())

    def test_subprocess_failure_sanitizes_cleans_and_does_not_mutate(self):
        before = contents(self.project)
        captured = []
        checked = []

        def fail(command, **kwargs):
            profile_dir = Path(command[command.index('--profiles-dir') + 1])
            captured.append(profile_dir)
            profile_file = profile_dir / 'profiles.yml'
            self.assertEqual(stat.S_IMODE(profile_file.stat().st_mode), 0o600)
            profile = yaml.safe_load(profile_file.read_text())
            output = next(iter(profile['offline']['outputs'].values()))
            self.assertEqual((output['host'], output['port'], output['schema']), ('127.0.0.1', 9, 'sandbox'))
            self.assertNotIn('password', output)
            self.assertNotEqual(kwargs['env'].get('DBT_PROFILES_DIR'), '/private/real')
            self.assertNotIn('SECRET_SENTINEL', kwargs['env'])
            for flag in ('--no-introspect', '--no-populate-cache', '--no-partial-parse'):
                self.assertIn(flag, command)
            checked.append(True)
            return subprocess.CompletedProcess(command, 1, 'SECRET_SENTINEL private log', '')

        with patch.dict(os.environ, DBT_PROFILES_DIR='/private/real', SECRET_SENTINEL='secret'):
            with patch.object(dbt.subprocess, 'run', side_effect=fail):
                with self.assertRaises(dbt.DbtError) as caught:
                    dbt.compile_project(self.project, catalog='cat', source_schema='raw')
        self.assertEqual(caught.exception.code, 'COMPILE_FAILED')
        self.assertNotIn('SECRET_SENTINEL', str(caught.exception))
        self.assertEqual(contents(self.project), before)
        self.assertTrue(captured)
        self.assertEqual(checked, [True])
        self.assertTrue(all(not p.exists() for p in captured))


class ManifestTests(unittest.TestCase):
    def manifest(self):
        def model(name, materialized, dependencies, sql):
            return {'resource_type': 'model', 'name': name, 'database': 'cat',
                    'schema': 'sandbox', 'alias': name,
                    'config': {'materialized': materialized},
                    'depends_on': {'nodes': dependencies}, 'compiled_code': sql}
        return {'sources': {'source.orders': {'resource_type': 'source',
                    'name': 'orders', 'database': 'cat', 'schema': 'raw', 'identifier': 'orders'}},
                'nodes': {
                    'model.e': model('e', 'ephemeral', ['source.orders'],
                                     'SELECT id FROM cat.raw.orders'),
                    'model.t': model('t', 'table', ['model.e'],
                                     'WITH e AS (SELECT id FROM cat.raw.orders) SELECT * FROM e'),
                    'test.unique': {'resource_type': 'test', 'name': 'unique_t',
                        'config': {}, 'depends_on': {'nodes': ['model.t']},
                        'test_metadata': {'name': 'unique', 'namespace': None},
                        'compiled_code': 'SELECT id FROM cat.sandbox.t GROUP BY id HAVING count(*) > 1'}}}

    def test_topological_order_keeps_ephemeral_and_test_dependencies(self):
        summary = dbt._validate_manifest(self.manifest(), catalog='cat', source_schema='raw')
        self.assertEqual([m['unique_id'] for m in summary['models']], ['model.e', 'model.t'])
        self.assertEqual(summary['materialized_order'], ['model.t'])
        self.assertIsNone(summary['models'][0]['relation'])
        self.assertEqual(summary['tests'][0]['depends_on'], ['model.t'])

    def test_view_security_properties_and_view_graph_fail_closed(self):
        for security in ('definer', 'unknown', '', True):
            manifest = self.manifest()
            manifest['nodes']['model.t']['config'].update(materialized='view', view_security=security)
            with self.assertRaises(dbt.DbtError) as caught:
                dbt._validate_manifest(manifest, catalog='cat', source_schema='raw')
            self.assertEqual(caught.exception.code, 'UNSUPPORTED_VIEW_SECURITY')
            if security == 'unknown':
                self.assertNotIn(security, str(caught.exception.compatibility['issues']))
        for props in ({'format': "'PARQUET'"}, {'location': 'secret'}, {'replace': True}):
            manifest = self.manifest()
            manifest['nodes']['model.t']['config'].update(materialized='view', properties=props)
            with self.assertRaises(dbt.DbtError) as caught:
                dbt._validate_manifest(manifest, catalog='cat', source_schema='raw')
            self.assertEqual(caught.exception.code, 'UNSUPPORTED_VIEW_PROPERTIES')
            self.assertNotIn('secret', str(caught.exception.compatibility))
        manifest = self.manifest()
        manifest['nodes']['model.e']['config']['materialized'] = 'view'
        manifest['nodes']['model.t'].update(compiled_code='SELECT * FROM cat.sandbox.e')
        summary = dbt._validate_manifest(manifest, catalog='cat', source_schema='raw')
        self.assertEqual(summary['models'][0]['materialized'], 'view')
        self.assertEqual(summary['models'][0]['table_properties'], {})
        self.assertEqual(summary['models'][0]['view_security'], 'INVOKER')
        manifest['nodes']['model.t']['config']['materialized'] = 'view'
        with self.assertRaises(dbt.DbtError) as caught:
            dbt._validate_manifest(manifest, catalog='cat', source_schema='raw')
        self.assertEqual(caught.exception.code, 'UNSUPPORTED_VIEW_DEPENDENCY')

    def test_resolved_semantic_issues_are_fixed_safe_and_collected(self):
        cases = [('on_table_exists', 'rename', 'UNSUPPORTED_MODEL_CONFIG'),
            ('column_types', {'id': 'secret'}, 'UNSUPPORTED_MODEL_CONFIG'),
            ('table_format', 'iceberg', 'UNSUPPORTED_MODEL_CONFIG'),
            ('properties', {'format': "'ORC'"}, 'UNSUPPORTED_TABLE_PROPERTIES'),
            ('properties', {'format': "'PARQUET'; secret"}, 'UNSUPPORTED_TABLE_PROPERTIES'),
            ('properties', {'partitioning': 'secret'}, 'UNSUPPORTED_TABLE_PROPERTIES')]
        for setting, value, code in cases:
            with self.subTest(setting=setting, value=value):
                manifest = self.manifest()
                manifest['nodes']['model.t']['config'][setting] = value
                manifest['nodes']['test.unique']['config'].update(severity='warn', fail_calc='secret',
                    error_if='> 99', warn_if='> 1', limit=1)
                with self.assertRaises(dbt.DbtError) as caught:
                    dbt._validate_manifest(manifest, catalog='cat', source_schema='raw')
                issues = caught.exception.compatibility['issues']
                self.assertEqual(issues[0], dict(node_id='model.t', code=code, setting=setting))
                self.assertEqual({i['setting'] for i in issues[1:]},
                    {'test.severity', 'test.fail_calc', 'test.error_if', 'test.warn_if', 'test.limit'})
                self.assertNotIn('secret', str(issues))
                self.assertEqual(caught.exception.compatibility['status'], 'blocked')

    def test_typed_literal_and_metadata_and_injected_defaults(self):
        for literal in ("'PARQUET'", "  'parquet'  "):
            manifest = self.manifest()
            manifest['nodes']['model.t']['config'].update(properties={'format': literal},
                meta={'properties': {'format': 'documentation'}, 'hooks': 'documentation'},
                view_security='definer', column_types={}, persist_docs={},
                on_schema_change='ignore', on_configuration_change='apply', contract={'enforced': False})
            manifest['nodes']['model.t']['columns'] = {'id': {'properties': {'anything': 'metadata'}}}
            manifest['nodes']['test.unique']['config'].update(severity='ERROR', fail_calc='count(*)',
                error_if='!= 0', warn_if='!= 0', limit=None, store_failures=None)
            summary = dbt._validate_manifest(manifest, catalog='cat', source_schema='raw')
            self.assertEqual(summary['compatibility'], dbt.compatibility())
            self.assertEqual(summary['models'][1]['table_properties'], {'format': 'PARQUET'})
            self.assertEqual(summary['models'][0]['table_properties'], {})

    def test_inherited_supported_format_applies_only_to_table(self):
        for literal in ("'PARQUET'", "  'parquet'  "):
            with self.subTest(literal=literal):
                manifest = self.manifest()
                # dbt resolves inherited project properties onto both model configs.
                for uid in ('model.e', 'model.t'):
                    manifest['nodes'][uid]['config']['properties'] = {'format': literal}
                summary = dbt._validate_manifest(manifest, catalog='cat', source_schema='raw')
                models = {model['unique_id']: model for model in summary['models']}
                self.assertEqual(models['model.e']['table_properties'], {})
                self.assertIsNone(models['model.e']['relation'])
                self.assertEqual(models['model.t']['table_properties'], {'format': 'PARQUET'})
                self.assertEqual(summary['materialized_order'], ['model.t'])

    def test_source_schema_must_match_requested_schema(self):
        manifest = self.manifest()
        manifest['sources']['source.orders']['schema'] = 'other_raw'
        for node in manifest['nodes'].values():
            node['compiled_code'] = node['compiled_code'].replace('cat.raw.orders', 'cat.other_raw.orders')
        with self.assertRaises(dbt.DbtError) as caught:
            dbt._validate_manifest(manifest, catalog='cat', source_schema='raw')
        self.assertEqual(caught.exception.code, 'INVALID_RELATION')

        summary = dbt._validate_manifest(manifest, catalog='cat', source_schema='other_raw')
        self.assertEqual(summary['sources'], [{'unique_id': 'source.orders', 'name': 'orders',
                                              'relation': ['cat', 'other_raw', 'orders']}])

    def test_reject_invalid_materialization_config_relations_and_graph(self):
        original = self.manifest()
        cases = [
            ('UNSUPPORTED_VIEW_SECURITY', lambda m: m['nodes']['model.t']['config'].update(materialized='view', view_security='definer')),
            ('UNSUPPORTED_MATERIALIZATION', lambda m: m['nodes']['model.t']['config'].update(materialized='incremental')),
            ('UNSUPPORTED_TEST_CONFIG', lambda m: m['nodes']['test.unique']['config'].update(store_failures=True)),
            ('UNSUPPORTED_MODEL_CONFIG', lambda m: m['nodes']['model.t']['config'].update(contract={'enforced': True})),
            ('UNSUPPORTED_MODEL_CONFIG', lambda m: m['nodes']['model.t']['config'].update({'post-hook': ['select 1']})),
            ('INVALID_RELATION', lambda m: m['nodes']['model.t'].update(schema='outside')),
            ('INVALID_RELATION', lambda m: m['sources']['source.orders'].update(database='other')),
            ('INVALID_RELATION', lambda m: m['nodes']['model.t'].update(alias='e')),
            ('INVALID_RELATION', lambda m: m['nodes']['model.t'].update(compiled_code='SELECT * FROM cat.raw.unbound')),
            ('INVALID_DEPENDENCY', lambda m: m['nodes']['model.t']['depends_on'].update(nodes=['missing'])),
            ('INVALID_DEPENDENCY', lambda m: m['nodes']['model.e']['depends_on'].update(nodes=['model.t'])),
            ('INVALID_MANIFEST', lambda m: m['nodes']['model.t'].update(resource_type='seed')),
        ]
        for code, mutate in cases:
            with self.subTest(code=code):
                manifest = copy.deepcopy(original)
                mutate(manifest)
                with self.assertRaises(dbt.DbtError) as caught:
                    dbt._validate_manifest(manifest, catalog='cat', source_schema='raw')
                self.assertEqual(caught.exception.code, code)


if __name__ == '__main__':
    unittest.main()
