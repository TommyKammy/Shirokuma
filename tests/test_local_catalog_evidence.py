"""Offline tamper, scope, and provenance checks for historical catalog inputs."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('catalog_evidence', ROOT / 'tools/local_experiment/metadata/data_plan_evidence.py')
E = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E)
TOKEN = '1234567890abcdef1234567890abcdef'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def change(path, edit):
    value = json.loads(path.read_text())
    edit(value)
    save(path, value)


def fixture(base, operation='name_lengths', recovery=False):
    """Minimal real-shaped dbt JSON and Dagster SQLite, with no runtime calls."""
    directory = base / TOKEN
    directory.mkdir()
    plan = {'schema_version': 1, 'source': 'fixture_v1', 'operation': operation}
    cols = E.CONTRACT.columns(plan)
    expected = E.CONTRACT.expected_rows(plan, E.FIXTURE)
    project = E._project(plan)
    hashes = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in project.items()}
    for name, text in project.items():
        path = directory / 'project' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    save(directory / 'plan.json', plan)
    save(directory / 'project-sha256.json', hashes)
    relations = [f'iceberg.local_experiment.dataops_{TOKEN}_{suffix}' for suffix in ('stg', 'mart')]
    attempts = []
    labels = ['first', 'repeat'] + (['controlled-failure', 'retry'] if recovery else [])
    for label in labels:
        success = label != 'controlled-failure'
        run_id = str(uuid.uuid4())
        commands = []
        for command in ['build', 'test'] if success else ['build']:
            nodes = {}
            for suffix, name, names in [('stg', 'stg_fixture', ['id', 'name']), ('mart', 'mart_fixture', cols)]:
                key = E.MODEL_PREFIX + name
                raw = project['models/' + name + '.sql'].strip()
                compiled = raw.split('\n\n', 1)[1].replace("{{ source('retained_smoke', 'fixture_v1') }}", '"iceberg"."smoke"."fixture_v1"').replace("{{ ref('stg_fixture') }}", f'"iceberg"."local_experiment"."dataops_{TOKEN}_stg"')
                nodes[key] = {'resource_type': 'model', 'unique_id': key, 'name': name,
                    'database': 'iceberg', 'schema': 'local_experiment', 'alias': f'dataops_{TOKEN}_{suffix}',
                    'description': f'shirokuma-local-dataops:{TOKEN}', 'columns': {c: {'name': c} for c in names},
                    'depends_on': {'nodes': [E.SOURCE_ID if suffix == 'stg' else E.MODEL_PREFIX + 'stg_fixture']},
                    'raw_code': raw, 'config': {'materialized': 'table'}}
                if command == 'build':
                    nodes[key]['compiled_code'] = compiled
            for name in ['mart_expected_rows', 'mart_expected_values']:
                key = E.TEST_PREFIX + name
                nodes[key] = {'resource_type': 'test', 'unique_id': key, 'test_metadata': None,
                    'depends_on': {'nodes': [E.MODEL_PREFIX + 'mart_fixture']},
                    'raw_code': project['tests/' + name + '.sql'].strip()}
            for model, names in [('stg_fixture', ['id', 'name']), ('mart_fixture', cols)]:
                for col in names:
                    for check in ['not_null', 'unique'] if col == 'id' else ['not_null']:
                        key = E.TEST_PREFIX + f'{check}_{model}_{col}.testhash'
                        nodes[key] = {'resource_type': 'test', 'unique_id': key,
                            'depends_on': {'nodes': [E.MODEL_PREFIX + model]},
                            'test_metadata': {'name': check, 'kwargs': {'column_name': col,
                                'model': "{{ get_where_subquery(ref('" + model + "')) }}"}}}
            invocation = str(uuid.uuid4())
            manifest = {'metadata': {'invocation_id': invocation, 'project_name': 'shirokuma_local_dataops', 'adapter_type': 'trino'},
                'sources': {E.SOURCE_ID: {'database': 'iceberg', 'schema': 'smoke', 'identifier': 'fixture_v1', 'source_name': 'retained_smoke'}},
                'nodes': nodes}
            entries = []
            for key, node in nodes.items():
                model = node['resource_type'] == 'model'
                if command == 'test' and model:
                    continue
                failing = not success and key == E.TEST_PREFIX + 'mart_expected_rows'
                item = {'unique_id': key, 'status': 'success' if model else 'fail' if failing else 'pass',
                        'failures': None if model else 1 if failing else 0, 'message': None}
                if model:
                    item.update(compiled_code=node['compiled_code'], relation_name=f'"iceberg"."local_experiment"."{node["alias"]}"')
                entries.append(item)
            prefix = directory / 'execution' / label / (command + '-target')
            save(prefix / 'manifest.json', manifest)
            save(prefix / 'run_results.json', {'metadata': {'invocation_id': invocation}, 'results': entries})
            commands.append({'command': command, 'exit_code': 0 if success else 1,
                             'nodes': [{k: x.get(k) for k in ('unique_id', 'status', 'failures', 'message')} for x in entries]})
        attempt = {'success': success, 'run_id': run_id, 'materializations': 1 if success else 0, 'commands': commands}
        save(directory / 'execution' / label / 'result.json', attempt)
        attempt.update(label=label)
        if success:
            attempt['readback'] = {'columns': cols, 'rows': expected}
        attempts.append(attempt)
        history = directory / 'execution' / label / 'dagster/history'
        (history / 'runs').mkdir(parents=True)
        with sqlite3.connect(history / 'runs.db') as database:
            database.execute('CREATE TABLE runs (run_id TEXT, status TEXT)')
            database.execute('INSERT INTO runs VALUES (?, ?)', (run_id, 'SUCCESS' if success else 'FAILURE'))
        with sqlite3.connect(history / 'runs' / (run_id + '.db')) as database:
            database.execute('CREATE TABLE event_logs (run_id TEXT, dagster_event_type TEXT, event TEXT)')
            database.execute('INSERT INTO event_logs VALUES (?, ?, ?)', (run_id, 'PIPELINE_SUCCESS' if success else 'PIPELINE_FAILURE', '{}'))
            if success:
                materialization = {'asset_key': {'path': ['local_lakehouse_dbt']}, 'metadata_entries': [
                    {'label': 'run_token', 'entry_data': {'text': TOKEN}},
                    {'label': 'relations', 'entry_data': {'data': [f'dataops_{TOKEN}_stg', f'dataops_{TOKEN}_mart']}}]}
                event = {'dagster_event': {'event_specific_data': {'materialization': materialization}}}
                database.execute('INSERT INTO event_logs VALUES (?, ?, ?)', (run_id, 'ASSET_MATERIALIZATION', json.dumps(event)))
    summary = {'result': 'passed', 'scope': 'local-experiment-only', 'run_token': TOKEN,
        'relations': relations, 'attempts': attempts, 'errors': [], 'deterministic': True,
        'fixture_before': {'columns': ['id', 'name'], 'rows': E.FIXTURE},
        'fixture_after': {'columns': ['id', 'name'], 'rows': E.FIXTURE}, 'fixture_unchanged': True,
        'query_drain': {'verified': True, 'user': 'shirokuma-local-dataops-' + TOKEN, 'active': [], 'errors': [], 'cancelled': [], 'cancel_errors': []},
        'cleanup': {'removed': list(reversed(relations)), 'remaining': [], 'errors': []}}
    if recovery:
        summary['controlled_failure_observed'] = True
    save(directory / 'execution/summary.json', summary)
    summary.update(scope='local-experiment-data-plan', plan=plan, project_sha256=hashes,
                   expected_columns=cols, expected_rows=expected, materializations=sum(x['materializations'] for x in attempts))
    save(directory / 'summary.json', summary)
    return directory


class CatalogEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name) / 'data-plans'
        self.base.mkdir()
        self.patch = patch.object(E, 'EVIDENCE_ROOT', self.base)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temporary.cleanup()

    def test_supported_plans_emit_only_fixed_historical_metadata(self):
        for operation in ['uppercase_names', 'name_lengths', 'row_count']:
            with self.subTest(operation=operation):
                base = self.base / operation
                base.mkdir()
                with patch.object(E, 'EVIDENCE_ROOT', base):
                    directory = fixture(base, operation)
                    before = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob('*') if p.is_file()}
                    result = E.read_evidence(directory)
                    after = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob('*') if p.is_file()}
                self.assertEqual(before, after)
                self.assertEqual(result['operation'], operation)
                self.assertEqual(result['row_count'], 1 if operation == 'row_count' else 2)
                self.assertEqual([x['id'] for x in result['entities']], ['stg', 'mart'])
                self.assertTrue(all(x['historical'] for x in result['entities']))
                self.assertEqual([x['kind'] for x in result['evidence']], list(E.KINDS))
                self.assertEqual(result['lineage'][0]['from'], E.SOURCE)
                self.assertNotIn(str(directory), json.dumps(result))
                self.assertNotIn('select ', json.dumps(result))
                self.assertNotIn('owners', result)

    def test_controlled_failure_requires_successful_retry_and_dagster_failure_record(self):
        directory = fixture(self.base, recovery=True)
        self.assertEqual(len(E.read_evidence(directory)['dagster_run_ids']), 4)
        path = directory / 'execution/controlled-failure/dagster/history/runs.db'
        with sqlite3.connect(path) as database:
            database.execute("UPDATE runs SET status='SUCCESS'")
        with self.assertRaises(E.EvidenceError):
            E.read_evidence(directory)

    def test_forged_project_hashes_cannot_authorize_changed_sql(self):
        directory = fixture(self.base)
        relative = 'models/mart_fixture.sql'
        sql = "select * from unrelated.secret"
        (directory / 'project' / relative).write_text(sql)
        sha = hashlib.sha256(sql.encode()).hexdigest()
        change(directory / 'project-sha256.json', lambda x: x.update({relative: sha}))
        change(directory / 'summary.json', lambda x: x['project_sha256'].update({relative: sha}))
        with self.assertRaises(E.EvidenceError):
            E.read_evidence(directory)

    def test_summary_failure_cleanup_and_oracle_tampering_rejected(self):
        directory = fixture(self.base)
        original = (directory / 'summary.json').read_bytes()
        edits = [lambda x: x.update(result='failed'), lambda x: x.update(run_token='a' * 32),
                 lambda x: x.update(fixture_unchanged=False), lambda x: x.update(deterministic=False),
                 lambda x: x['cleanup'].update(removed=[]), lambda x: x['cleanup'].update(remaining=['anything']),
                 lambda x: x['query_drain'].update(verified=False), lambda x: x['query_drain'].update(active=[['still-running']]),
                 lambda x: x.update(expected_rows=[[1, 999], [2, 7]]),
                 lambda x: x['plan'].update(operation='row_count')]
        for edit in edits:
            with self.subTest(edit=edit):
                (directory / 'summary.json').write_bytes(original)
                change(directory / 'summary.json', edit)
                with self.assertRaises(E.EvidenceError):
                    E.read_evidence(directory)

    def test_changed_manifest_lineage_columns_source_or_compiled_sql_rejected(self):
        directory = fixture(self.base)
        path = directory / 'execution/first/build-target/manifest.json'
        original = path.read_bytes()
        key = E.MODEL_PREFIX + 'mart_fixture'
        edits = [lambda x: x['nodes'][key]['depends_on'].update(nodes=[E.SOURCE_ID]),
                 lambda x: x['nodes'][key].update(compiled_code='select 1'),
                 lambda x: x['nodes'][key]['columns'].update(email={}),
                 lambda x: x['nodes'][key].update(alias='retained'),
                 lambda x: x['sources'][E.SOURCE_ID].update(schema='private'),
                 lambda x: x['nodes'].pop(E.TEST_PREFIX + 'mart_expected_values')]
        for edit in edits:
            with self.subTest(edit=edit):
                path.write_bytes(original)
                change(path, edit)
                with self.assertRaises(E.EvidenceError):
                    E.read_evidence(directory)

    def test_cancellation_race_allowed_only_with_valid_identity_and_verified_termination(self):
        directory = fixture(self.base)
        for relative in ['summary.json', 'execution/summary.json']:
            change(directory / relative, lambda x: x['query_drain'].update(
                cancel_errors=[{'query_id': '20260917_12345_00001_test', 'error': 'PRIVATE normal completion race'}]))
        self.assertNotIn('PRIVATE', json.dumps(E.read_evidence(directory)))
        for relative in ['summary.json', 'execution/summary.json']:
            change(directory / relative, lambda x: x['query_drain'].update(
                cancel_errors=[{'query_id': 'bad;identity', 'error': 'failed'}]))
        with self.assertRaisesRegex(E.EvidenceError, 'cancellation race'):
            E.read_evidence(directory)

    def test_changed_result_invocation_execution_or_missing_test_rejected(self):
        directory = fixture(self.base)
        path = directory / 'execution/first/build-target/run_results.json'
        original = path.read_bytes()
        for edit in [lambda x: x['metadata'].update(invocation_id=str(uuid.uuid4())),
                     lambda x: x['results'][0].update(status='error'),
                     lambda x: x['results'].pop(),
                     lambda x: x['results'][0].update(relation_name='"retained"'),
                     lambda x: x['results'][0].update(compiled_code='select 1')]:
            path.write_bytes(original)
            change(path, edit)
            with self.assertRaises(E.EvidenceError):
                E.read_evidence(directory)

    def test_dagster_asset_must_prove_exact_token_and_relations(self):
        directory = fixture(self.base)
        summary = json.loads((directory / 'summary.json').read_text())
        run_id = summary['attempts'][0]['run_id']
        path = directory / f'execution/first/dagster/history/runs/{run_id}.db'
        with sqlite3.connect(path) as database:
            raw = database.execute("SELECT event FROM event_logs WHERE dagster_event_type='ASSET_MATERIALIZATION'").fetchone()[0]
            event = json.loads(raw)
            event['dagster_event']['event_specific_data']['materialization']['metadata_entries'][0]['entry_data']['text'] = 'a' * 32
            database.execute("UPDATE event_logs SET event=? WHERE dagster_event_type='ASSET_MATERIALIZATION'", (json.dumps(event),))
        with self.assertRaises(E.EvidenceError):
            E.read_evidence(directory)

    def test_missing_dagster_receipt_and_uncheckpointed_database_rejected(self):
        directory = fixture(self.base)
        path = directory / 'execution/first/dagster/history/runs.db'
        wal = Path(str(path) + '-wal')
        wal.write_bytes(b'pending')
        with self.assertRaises(E.EvidenceError):
            E.read_evidence(directory)
        wal.unlink()
        path.unlink()
        with self.assertRaises(E.EvidenceError):
            E.read_evidence(directory)

    def test_wrong_root_nested_path_and_symlink_rejected(self):
        directory = fixture(self.base)
        with self.assertRaises(E.EvidenceError):
            E.read_evidence(directory / 'execution')
        alias = self.base / ('b' * 32)
        alias.symlink_to(directory, target_is_directory=True)
        with self.assertRaises(E.EvidenceError):
            E.read_evidence(alias)
        path = directory / 'plan.json'
        target = self.base / 'outside.json'
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaises(E.EvidenceError):
            E.read_evidence(directory)

    def test_duplicate_keys_unknown_plan_and_safe_errors(self):
        directory = fixture(self.base)
        path = directory / 'plan.json'
        for raw in ['{"schema_version":1,"schema_version":2,"source":"fixture_v1","operation":"name_lengths"}',
                    '{"schema_version":1,"source":"private_secret","operation":"name_lengths"}',
                    'PRIVATE_RAW_BROKEN_JSON']:
            path.write_text(raw)
            with self.assertRaises(E.EvidenceError) as error:
                E.read_evidence(directory)
            self.assertNotIn('private_secret', str(error.exception))
            self.assertNotIn('PRIVATE_RAW', str(error.exception))

    def test_projection_revalidation_rejects_aliases_owner_and_missing_evidence(self):
        projection = E.read_evidence(fixture(self.base))
        for edit in [lambda x: x.update(schema_version=True), lambda x: x.update(owner='arbitrary'),
                     lambda x: x['entities'][0].update(name=E.SOURCE),
                     lambda x: x['entities'][0].update(historical=False),
                     lambda x: x['evidence'].pop(), lambda x: x['evidence'][0].update(sha256='bad'),
                     lambda x: x['dagster_run_ids'].append(x['dagster_run_ids'][0]),
                     lambda x: x['columns'][0].update(name='email')]:
            value = copy.deepcopy(projection)
            edit(value)
            with self.assertRaises(E.EvidenceError):
                E.validate_projection(value)
        copied = E.validate_projection(projection)
        copied['entities'][0]['name'] = 'changed'
        self.assertNotEqual(copied, projection)


if __name__ == '__main__':
    unittest.main()
