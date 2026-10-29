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
            ('UNSUPPORTED_MATERIALIZATION', lambda m: m['nodes']['model.t']['config'].update(materialized='view')),
            ('UNSUPPORTED_MATERIALIZATION', lambda m: m['nodes']['model.t']['config'].update(materialized='incremental')),
            ('UNSUPPORTED_CONFIG', lambda m: m['nodes']['test.unique']['config'].update(store_failures=True)),
            ('UNSUPPORTED_CONFIG', lambda m: m['nodes']['model.t']['config'].update(contract={'enforced': True})),
            ('UNSUPPORTED_CONFIG', lambda m: m['nodes']['model.t']['config'].update({'post-hook': ['select 1']})),
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
