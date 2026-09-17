"""A model plan chooses fixed transformations; host execution retains ownership guards."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    'data_plan_run', Path(__file__).resolve().parents[1] / 'tools/local_experiment/dataops/plan_run.py')
PLAN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLAN)
RUN = PLAN.RUN
TOKEN = 'b' * 32
URL = 'http://127.0.0.1:18080'


def plan(operation='name_lengths'):
    return {'schema_version': 1, 'source': 'fixture_v1', 'operation': operation}


class FakeTrino:
    url, timeout = URL, 120

    def __init__(self, selected):
        self.tables = {'retained_other': 'not-owned'}
        self.queries = []
        self.rows = PLAN.CONTRACT.expected_rows(selected, RUN.FIXTURE_ROWS)
        self.columns = PLAN.CONTRACT.columns(selected)
        self.fixture_reads = 0
        self.changed_fixture = False
        self.active_queries = []
        self.drain_error = None

    def query(self, sql):
        self.queries.append(sql)
        if 'FROM system.runtime.queries' in sql:
            if self.drain_error:
                raise RUN.DataOpsError(self.drain_error)
            return {'rows': self.active_queries.copy()}
        if sql.startswith('CALL system.runtime.kill_query'):
            self.active_queries = []
            return {'rows': []}
        if sql == RUN.FIXTURE_SQL:
            self.fixture_reads += 1
            rows = [[1, 'changed']] if self.changed_fixture and self.fixture_reads > 1 else RUN.FIXTURE_ROWS
            return {'columns': ['id', 'name'], 'rows': copy.deepcopy(rows)}
        if sql.startswith('SHOW TABLES'):
            return {'rows': [[name] for name in self.tables]}
        if sql.startswith('SHOW CREATE TABLE'):
            name = sql.rsplit('.', 1)[1]
            return {'rows': [[f"CREATE TABLE {name} COMMENT '{self.tables[name]}'"]]}
        if sql.startswith('DROP TABLE'):
            del self.tables[sql.rsplit('.', 1)[1]]
            return {'rows': []}
        if sql.startswith('SELECT '):
            expected_sql = 'SELECT ' + ', '.join(self.columns) + ' FROM ' + RUN.NAMESPACE + '.' + RUN.names_for(TOKEN)[1]
            if self.columns[0] == 'id':
                expected_sql += ' ORDER BY id'
            if sql != expected_sql:
                raise AssertionError('unexpected readback SQL: ' + sql)
            return {'columns': self.columns.copy(), 'rows': copy.deepcopy(self.rows)}
        raise AssertionError(sql)


class DataPlanRunTests(unittest.TestCase):
    def materializer(self, client):
        def materialize(attempt, profiles, token, fail, timeout, env, *, project):
            attempt.mkdir()
            self.assertTrue((project / 'models/mart_fixture.sql').is_file())
            for name in RUN.names_for(token):
                client.tables[name] = 'shirokuma-local-dataops:' + token
            return {'success': not fail, 'materializations': 0 if fail else 1,
                    'commands': [{'command': 'build', 'exit_code': 1 if fail else 0, 'nodes': [{
                        'unique_id': 'test.shirokuma_local_dataops.mart_expected_rows',
                        'status': 'fail' if fail else 'pass', 'failures': 1 if fail else 0}]}]}
        return materialize

    def execute(self, directory, selected=None, client=None, materializer=None, **kwargs):
        selected = selected or plan()
        client = client or FakeTrino(selected)
        return PLAN.run_plan(selected, URL, Path(directory) / 'new-run', token=TOKEN, client=client,
                             materializer=materializer or self.materializer(client), **kwargs)

    def test_each_fixed_plan_materializes_twice_matches_oracle_and_cleans_owned_tables(self):
        for operation in ('uppercase_names', 'name_lengths', 'row_count'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                selected = plan(operation)
                client = FakeTrino(selected)
                result = self.execute(directory, selected, client)
                self.assertEqual(result['result'], 'passed', result)
                self.assertEqual(result['materializations'], 2)
                self.assertEqual([x['label'] for x in result['attempts']], ['first', 'repeat'])
                self.assertTrue(result['deterministic'])
                self.assertTrue(result['fixture_unchanged'])
                self.assertTrue(result['query_drain']['verified'])
                self.assertEqual(result['cleanup']['errors'], [])
                self.assertEqual(result['cleanup']['remaining'], [])
                self.assertEqual(len(result['cleanup']['removed']), 2)
                self.assertEqual(client.tables, {'retained_other': 'not-owned'})
                output = Path(result['evidence'])
                self.assertEqual(json.loads((output / 'summary.json').read_text()), result)
                self.assertEqual(json.loads((output / 'plan.json').read_text()), selected)
                generated = (output / 'project/models/mart_fixture.sql').read_text()
                self.assertIn(PLAN.CONTRACT.compile_select(selected).strip(), generated)
                schema = json.loads((output / 'project/models/schema.yml').read_text())
                self.assertEqual([item['name'] for item in schema['models'][1]['columns']], client.columns)
                self.assertEqual(schema['models'][0]['description'], "shirokuma-local-dataops:{{ var('run_token') }}")
                for file in output.rglob('*'):
                    self.assertEqual(file.stat().st_mode & 0o777, 0o700 if file.is_dir() else 0o600)

    def test_unsupported_or_extra_model_fields_cannot_create_output_or_touch_database(self):
        invalid = [plan('unsupported'), {**plan(), 'sql': 'drop table retained'},
                   {**plan(), 'source': 'other'}, {**plan(), 'project': '/arbitrary/path'},
                   {**plan(), 'schema_version': True}]
        for selected in invalid:
            with self.subTest(selected=selected), tempfile.TemporaryDirectory() as directory:
                client, materialize = Mock(), Mock()
                with self.assertRaises(PLAN.CONTRACT.ContractError):
                    self.execute(directory, selected, client, materialize)
                client.query.assert_not_called()
                materialize.assert_not_called()
                self.assertFalse((Path(directory) / 'new-run').exists())

    def test_non_ascii_uppercase_oracle_rejects_before_output_or_materialization(self):
        with tempfile.TemporaryDirectory() as directory:
            client, materialize = Mock(), Mock()
            with patch.object(RUN, 'FIXTURE_ROWS', [[1, 'straße']]):
                with self.assertRaises(PLAN.CONTRACT.ContractError):
                    self.execute(directory, plan('uppercase_names'), client, materialize)
            client.query.assert_not_called()
            materialize.assert_not_called()
            self.assertFalse((Path(directory) / 'new-run').exists())

    def test_collision_does_not_materialize_or_delete_retained_relation(self):
        client = FakeTrino(plan())
        client.tables[RUN.names_for(TOKEN)[0]] = 'retained-data'
        materialize = Mock()
        with tempfile.TemporaryDirectory() as directory:
            result = self.execute(directory, client=client, materializer=materialize)
        self.assertEqual(result['result'], 'failed')
        materialize.assert_not_called()
        self.assertFalse(any(sql.startswith('DROP') for sql in client.queries))
        self.assertEqual(client.tables[RUN.names_for(TOKEN)[0]], 'retained-data')

    def test_result_mismatch_cleans_generated_relations_and_verifies_fixture(self):
        client = FakeTrino(plan())
        client.rows = [[1, 999], [2, 7]]
        with tempfile.TemporaryDirectory() as directory:
            result = self.execute(directory, client=client)
        self.assertEqual(result['result'], 'failed')
        self.assertTrue(result['fixture_unchanged'])
        self.assertEqual(len(result['attempts']), 1)
        self.assertEqual(len(result['cleanup']['removed']), 2)
        self.assertTrue(any('unexpected columns or rows' in error for error in result['errors']))
        self.assertEqual(client.tables, {'retained_other': 'not-owned'})

    def test_fixture_change_is_not_hidden_by_correct_mart_or_cleanup(self):
        client = FakeTrino(plan())
        client.changed_fixture = True
        with tempfile.TemporaryDirectory() as directory:
            result = self.execute(directory, client=client)
        self.assertEqual(result['result'], 'failed')
        self.assertFalse(result['fixture_unchanged'])
        self.assertEqual(len(result['cleanup']['removed']), 2)

    def test_foreign_replacement_marker_prevents_repeated_materialization_and_cleanup(self):
        client = FakeTrino(plan())
        calls = []
        def replace(*args, **kwargs):
            result = self.materializer(client)(*args, **kwargs)
            calls.append(1)
            client.tables[RUN.names_for(TOKEN)[0]] = 'another-owner'
            return result
        with tempfile.TemporaryDirectory() as directory:
            result = self.execute(directory, client=client, materializer=replace)
        self.assertEqual(result['result'], 'failed')
        self.assertEqual(len(calls), 1)
        self.assertIn(RUN.names_for(TOKEN)[0], client.tables)
        self.assertNotIn(RUN.names_for(TOKEN)[1], client.tables)
        self.assertTrue(any('ownership marker missing' in error for error in result['errors']))

    def test_server_query_drain_precedes_drop_and_failed_drain_preserves_relations(self):
        for failed_drain in (False, True):
            with self.subTest(failed_drain=failed_drain), tempfile.TemporaryDirectory() as directory:
                client = FakeTrino(plan())
                def fail(*args, **kwargs):
                    self.materializer(client)(*args, **kwargs)
                    client.active_queries = [['20260917_abc', 'RUNNING', RUN.run_user(TOKEN)]]
                    client.drain_error = 'query monitoring unavailable' if failed_drain else None
                    raise RUN.DataOpsError('original dbt timeout')
                with patch.object(RUN.time, 'sleep'):
                    result = self.execute(directory, client=client, materializer=fail)
                self.assertEqual(result['result'], 'failed')
                self.assertIn('original dbt timeout', result['errors'])
                if failed_drain:
                    self.assertEqual(result['cleanup']['status'], 'deferred')
                    self.assertTrue(set(RUN.names_for(TOKEN)) <= set(client.tables))
                    self.assertFalse(any(sql.startswith('DROP') for sql in client.queries))
                else:
                    self.assertTrue(result['query_drain']['verified'])
                    self.assertLess(next(i for i, sql in enumerate(client.queries) if sql.startswith('CALL')),
                                    next(i for i, sql in enumerate(client.queries) if sql.startswith('DROP')))
                    self.assertEqual(client.tables, {'retained_other': 'not-owned'})

    def test_controlled_failure_and_retry_use_plan_specific_row_count(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.execute(directory, plan('row_count'), exercise_failure=True)
            generated = (Path(result['evidence']) / 'project/tests/mart_expected_rows.sql').read_text()
        self.assertEqual(result['result'], 'passed')
        self.assertTrue(result['controlled_failure_observed'])
        self.assertEqual(result['materializations'], 3)
        self.assertEqual([attempt['label'] for attempt in result['attempts']],
                         ['first', 'repeat', 'controlled-failure', 'retry'])
        self.assertIn("2 if var('exercise_failure', false) else 1", generated)

    def test_caller_plan_mutation_cannot_change_compiled_plan_or_expected_rows(self):
        selected = plan('name_lengths')
        client = FakeTrino(selected)
        def mutate(*args, **kwargs):
            selected['operation'] = 'row_count'
            return self.materializer(client)(*args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            result = self.execute(directory, selected, client, mutate)
        self.assertEqual(result['result'], 'passed')
        self.assertEqual(result['plan']['operation'], 'name_lengths')
        self.assertEqual(result['expected_columns'], ['id', 'name_length'])
        self.assertEqual(result['expected_rows'], [[1, 9], [2, 7]])

    def test_different_plans_get_independent_projects_and_outputs_are_never_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.execute(Path(directory) / 'lengths', plan('name_lengths'))
            second = self.execute(Path(directory) / 'counts', plan('row_count'))
            self.assertNotEqual(first['project_sha256']['models/mart_fixture.sql'],
                                second['project_sha256']['models/mart_fixture.sql'])
            self.assertNotEqual(first['expected_rows'], second['expected_rows'])
            materialize = Mock()
            with self.assertRaises(FileExistsError):
                self.execute(Path(directory) / 'lengths', plan('row_count'), materializer=materialize)
            materialize.assert_not_called()

    def test_readback_columns_cannot_supply_sql_and_default_dbt_project_is_preserved(self):
        client = Mock()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RUN.DataOpsError):
                RUN.run_experiment(client, Path(directory) / 'run', expected_columns=['id; DROP TABLE retained'],
                                   expected_rows=[[1]])
            client.query.assert_not_called()
        args = RUN.dbt_command('build', Path('/attempt'), Path('/profiles'), TOKEN, False)
        self.assertEqual(args[args.index('--project-dir') + 1], str(RUN.HERE / 'dbt'))
        args = RUN.dbt_command('build', Path('/attempt'), Path('/profiles'), TOKEN, False, project=Path('/host-generated'))
        self.assertEqual(args[args.index('--project-dir') + 1], '/host-generated')

    def test_local_plan_file_is_bounded_and_remote_endpoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'plan.json'
            path.write_text(json.dumps(plan()))
            self.assertEqual(PLAN.read_plan(path), plan())
            path.write_bytes(b' ' * 4097)
            with self.assertRaises(RUN.DataOpsError):
                PLAN.read_plan(path)
            with self.assertRaises(RUN.TRINO.SmokeError):
                PLAN.run_plan(plan(), 'https://remote.invalid', Path(directory) / 'output')
            self.assertFalse((Path(directory) / 'output').exists())


if __name__ == '__main__':
    unittest.main()
