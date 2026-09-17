"""Read-only verification of bounded Data Bear artifacts for historical metadata.

This verifies local artifact consistency, not cryptographic provenance against an
attacker able to replace every artifact. No model text, SQL, paths, row values,
credentials, or arbitrary owners enter the returned metadata projection.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import stat
import uuid

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_ROOT = ROOT / 'tmp/local-experiment/data-plans'
SOURCE = 'shirokuma_local.iceberg.smoke.fixture_v1'
MODEL_PREFIX = 'model.shirokuma_local_dataops.'
SOURCE_ID = 'source.shirokuma_local_dataops.retained_smoke.fixture_v1'
TEST_PREFIX = 'test.shirokuma_local_dataops.'
FIXTURE = [[1, 'shirokuma'], [2, 'polaris']]
KINDS = ('plan', 'project', 'execution', 'dbt-manifests', 'dbt-results', 'dagster')
SPEC = importlib.util.spec_from_file_location('catalog_data_contract', ROOT / 'tools/local_experiment/bears/data_contract.py')
CONTRACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTRACT)


class EvidenceError(ValueError):
    """Evidence is missing, inconsistent, or outside the fixed local boundary."""


def _require(condition, message):
    if not condition:
        raise EvidenceError(message)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _same(actual, expected, message):
    _require(_json(actual) == _json(expected), message)


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _uuid(value):
    _require(type(value) is str and str(uuid.UUID(value)) == value, 'invalid Dagster run identity')


def _columns(operation):
    return [{'name': name, 'type': 'VARCHAR' if name in {'name', 'name_upper'} else 'BIGINT'}
            for name in CONTRACT.columns({'schema_version': 1, 'source': 'fixture_v1', 'operation': operation})]


def _base_projection(token, operation):
    plan = {'schema_version': 1, 'source': 'fixture_v1', 'operation': operation}
    CONTRACT.validate_plan(plan, executable=True)
    _require(type(token) is str and re.fullmatch('[0-9a-f]{32}', token), 'invalid data run identity')
    cols = _columns(operation)
    names = ['shirokuma_local.iceberg.local_experiment.dataops_' + token + '_' + suffix for suffix in ('stg', 'mart')]
    return {
        'schema_version': 1, 'run_token': token, 'operation': operation, 'source': SOURCE,
        'columns': cols, 'row_count': len(CONTRACT.expected_rows(plan, FIXTURE)),
        'entities': [
            {'id': 'stg', 'name': names[0], 'historical': True,
             'columns': [{'name': 'id', 'type': 'BIGINT'}, {'name': 'name', 'type': 'VARCHAR'}]},
            {'id': 'mart', 'name': names[1], 'historical': True, 'columns': cols},
        ],
        'lineage': [{'from': SOURCE, 'to': names[0]}, {'from': names[0], 'to': names[1]}],
        'cleanup_verified': True, 'fixture_unchanged': True,
    }


def validate_projection(value):
    """Check a projection before constructing an API request or model input."""
    try:
        _require(type(value) is dict, 'invalid catalog evidence projection')
        base = _base_projection(value.get('run_token'), value.get('operation'))
        _require(set(value) == set(base) | {'evidence', 'dagster_run_ids'}, 'invalid catalog projection keys')
        for key, expected in base.items():
            _same(value[key], expected, 'invalid fixed catalog projection field')
        refs = value['evidence']
        _require(type(refs) is list and len(refs) == len(KINDS), 'incomplete catalog evidence references')
        for number, (item, kind) in enumerate(zip(refs, KINDS), 1):
            _require(type(item) is dict and set(item) == {'id', 'kind', 'sha256'}, 'invalid evidence reference')
            _require(item['id'] == f'e{number:02}' and item['kind'] == kind
                     and type(item['sha256']) is str and re.fullmatch('[0-9a-f]{64}', item['sha256']),
                     'invalid evidence identity or digest')
        runs = value['dagster_run_ids']
        _require(type(runs) is list and len(runs) in (2, 4) and len(set(runs)) == len(runs), 'invalid Dagster run set')
        for run in runs:
            _uuid(run)
        return copy.deepcopy(value)
    except EvidenceError:
        raise
    except (ValueError, TypeError, KeyError):
        raise EvidenceError('invalid catalog evidence projection') from None


class _Files:
    def __init__(self, directory):
        self.directory = Path(directory).absolute()
        base = EVIDENCE_ROOT.absolute()
        _require(self.directory.parent == base and not base.is_symlink()
                 and not self.directory.is_symlink() and self.directory.is_dir(), 'data plan directory is outside the fixed boundary')
        _require(re.fullmatch('[0-9a-f]{32}', self.directory.name), 'invalid data plan directory identity')
        self.hashes = {kind: {} for kind in KINDS}
        self.observed = {}

    def path(self, relative):
        path = self.directory / relative
        _require(path.is_relative_to(self.directory), 'invalid evidence file boundary')
        for part in [path, *path.parents]:
            if part == self.directory.parent:
                break
            _require(not part.is_symlink(), 'symlinked evidence is not accepted')
        info = path.stat()
        _require(stat.S_ISREG(info.st_mode) and info.st_size <= 64 * 1024 * 1024, 'invalid or oversized evidence file')
        return path

    def raw(self, relative, kind):
        raw = self.path(relative).read_bytes()
        _require(len(raw) <= 64 * 1024 * 1024, 'oversized evidence file')
        sha = _digest(raw)
        old = self.observed.setdefault(relative, sha)
        _require(old == sha, 'evidence changed while reading')
        self.hashes[kind][relative] = sha
        return raw

    def json(self, relative, kind):
        def pairs(items):
            result = {}
            for key, val in items:
                _require(key not in result, 'duplicate JSON evidence key')
                result[key] = val
            return result
        return json.loads(self.raw(relative, kind), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(EvidenceError('non-finite JSON evidence')))

    def stable(self):
        for relative, sha in self.observed.items():
            _require(_digest(self.path(relative).read_bytes()) == sha, 'evidence changed during verification')

    def references(self):
        return [{'id': f'e{i:02}', 'kind': kind, 'sha256': _digest(_json(self.hashes[kind]).encode())}
                for i, kind in enumerate(KINDS, 1)]


def _project(plan):
    """Canonical source files; never trust model or artifact SQL as templates."""
    cols = CONTRACT.columns(plan)
    expected = CONTRACT.expected_rows(plan, FIXTURE)
    owner = "shirokuma-local-dataops:{{ var('run_token') }}"
    schema = {'version': 2, 'sources': [{'name': 'retained_smoke', 'database': 'iceberg',
              'schema': 'smoke', 'tables': [{'name': 'fixture_v1'}]}], 'models': [
        {'name': 'stg_fixture', 'description': owner, 'columns': [
            {'name': 'id', 'data_tests': ['not_null', 'unique']}, {'name': 'name', 'data_tests': ['not_null']}]},
        {'name': 'mart_fixture', 'description': owner, 'columns': [
            {'name': name, 'data_tests': ['not_null', 'unique'] if name == 'id' else ['not_null']} for name in cols]}]}
    def literal(value):
        return f'cast({value} as bigint)' if type(value) is int else "cast('" + value.replace("'", "''") + "' as varchar)"
    selected = ', '.join(cols)
    values = ',\n    '.join('(' + ', '.join(literal(value) for value in row) + ')' for row in expected)
    comparison = (f'with expected ({selected}) as (values\n    {values}\n)\n'
                  f'(select {selected} from {{{{ ref(\'mart_fixture\') }}}}\n'
                  f'except select {selected} from expected)\nunion all\n'
                  f'(select {selected} from expected\n'
                  f'except select {selected} from {{{{ ref(\'mart_fixture\') }}}})\n')
    dbt = ROOT / 'tools/local_experiment/dataops/dbt'
    return {
        'dbt_project.yml': (dbt / 'dbt_project.yml').read_text(),
        'models/stg_fixture.sql': (dbt / 'models/stg_fixture.sql').read_text(),
        'models/mart_fixture.sql': "{{ config(alias=var('relation_prefix') ~ '_mart') }}\n\n" + CONTRACT.compile_select(plan).rstrip() + '\n',
        'models/schema.yml': json.dumps(schema, indent=2, ensure_ascii=False) + '\n',
        'tests/mart_expected_rows.sql': "select count(*) as actual_rows\nfrom {{ ref('mart_fixture') }}\n"
            + 'having count(*) <> {{ ' + str(len(expected) + 1)
            + " if var('exercise_failure', false) else " + str(len(expected)) + ' }}\n',
        'tests/mart_expected_values.sql': comparison,
    }


def _manifest(manifest, project, token, cols, command):
    _require(manifest['metadata']['project_name'] == 'shirokuma_local_dataops'
             and manifest['metadata']['adapter_type'] == 'trino', 'unexpected dbt project identity')
    _require(set(manifest['sources']) == {SOURCE_ID}, 'unexpected dbt sources')
    source = manifest['sources'][SOURCE_ID]
    _same([source.get(x) for x in ('database', 'schema', 'identifier', 'source_name')],
          ['iceberg', 'smoke', 'fixture_v1', 'retained_smoke'], 'unexpected retained source identity')
    models = {k: v for k, v in manifest['nodes'].items() if v['resource_type'] == 'model'}
    _require(set(models) == {MODEL_PREFIX + 'stg_fixture', MODEL_PREFIX + 'mart_fixture'}, 'unexpected dbt models')
    tests = {}
    for suffix, node_name, column_names in [('stg', 'stg_fixture', ['id', 'name']), ('mart', 'mart_fixture', cols)]:
        key = MODEL_PREFIX + node_name
        node = models[key]
        _same([node.get(x) for x in ('unique_id', 'name', 'database', 'schema', 'alias', 'description')],
              [key, node_name, 'iceberg', 'local_experiment', f'dataops_{token}_{suffix}', f'shirokuma-local-dataops:{token}'], 'dbt model identity or ownership mismatch')
        _same(list(node['columns']), column_names, 'dbt model columns differ from fixed plan')
        _same(node['depends_on']['nodes'], [SOURCE_ID if suffix == 'stg' else MODEL_PREFIX + 'stg_fixture'], 'dbt lineage differs from fixed plan')
        raw = project[f'models/{node_name}.sql'].strip()
        _same(node['raw_code'].strip(), raw, 'dbt model source differs from generated project')
        _require(node['config']['materialized'] == 'table', 'unexpected dbt materialization')
        compiled = raw.split('\n\n', 1)[1].replace("{{ source('retained_smoke', 'fixture_v1') }}", '"iceberg"."smoke"."fixture_v1"').replace("{{ ref('stg_fixture') }}", f'"iceberg"."local_experiment"."dataops_{token}_stg"')
        if command == 'build':
            _same(node['compiled_code'].strip(), compiled, 'compiled model SQL differs from fixed plan')
    generic = set()
    expected_generic = {('stg_fixture', 'id', 'not_null'), ('stg_fixture', 'id', 'unique'), ('stg_fixture', 'name', 'not_null')}
    expected_generic |= {('mart_fixture', c, 'not_null') for c in cols}
    if 'id' in cols:
        expected_generic.add(('mart_fixture', 'id', 'unique'))
    singular = set()
    for key, node in manifest['nodes'].items():
        if key in models:
            continue
        _require(node['resource_type'] == 'test' and node['unique_id'] == key and key.startswith(TEST_PREFIX), 'unexpected dbt node')
        metadata = node.get('test_metadata')
        if metadata is None:
            name = key[len(TEST_PREFIX):]
            _require(name in {'mart_expected_rows', 'mart_expected_values'}, 'unexpected dbt singular test')
            _same(node['raw_code'].strip(), project['tests/' + name + '.sql'].strip(), 'dbt oracle test source mismatch')
            _same(node['depends_on']['nodes'], [MODEL_PREFIX + 'mart_fixture'], 'dbt test lineage mismatch')
            singular.add(name)
        else:
            deps = node['depends_on']['nodes']
            _require(len(deps) == 1 and deps[0] in models, 'unexpected dbt generic test dependency')
            model = models[deps[0]]['name']
            descriptor = (model, metadata['kwargs']['column_name'], metadata['name'])
            _require(descriptor in expected_generic and descriptor not in generic, 'unexpected or duplicate dbt generic test')
            _same(metadata['kwargs']['model'], "{{ get_where_subquery(ref('" + model + "')) }}", 'generic test source mismatch')
            generic.add(descriptor)
        tests[key] = node
    _require(generic == expected_generic and singular == {'mart_expected_rows', 'mart_expected_values'}, 'required dbt tests are missing')
    return models, tests


def _dagster(files, label, run_id, token, success):
    prefix = f'execution/{label}/dagster/history/'
    paths = [prefix + 'runs.db', prefix + f'runs/{run_id}.db']
    for relative in paths:
        path = files.path(relative)
        for suffix in ('-wal', '-journal'):
            sidecar = Path(str(path) + suffix)
            _require(not sidecar.exists() or sidecar.stat().st_size == 0, 'Dagster evidence is not checkpointed')
        files.raw(relative, 'dagster')
    with sqlite3.connect(files.path(paths[0]).as_uri() + '?mode=ro&immutable=1', uri=True) as database:
        _same(database.execute('SELECT run_id, status FROM runs').fetchall(), [(run_id, 'SUCCESS' if success else 'FAILURE')], 'Dagster stored run status mismatch')
    with sqlite3.connect(files.path(paths[1]).as_uri() + '?mode=ro&immutable=1', uri=True) as database:
        _same(database.execute('SELECT DISTINCT run_id FROM event_logs').fetchall(), [(run_id,)], 'Dagster event run identity mismatch')
        events = database.execute('SELECT dagster_event_type, event FROM event_logs WHERE dagster_event_type IN (?,?,?)', ('PIPELINE_SUCCESS', 'PIPELINE_FAILURE', 'ASSET_MATERIALIZATION')).fetchall()
    kinds = [row[0] for row in events]
    _same(sorted(kinds), sorted(['PIPELINE_SUCCESS', 'ASSET_MATERIALIZATION'] if success else ['PIPELINE_FAILURE']), 'Dagster terminal or materialization events mismatch')
    if success:
        event = json.loads(next(value for kind, value in events if kind == 'ASSET_MATERIALIZATION'))
        materialization = event['dagster_event']['event_specific_data']['materialization']
        _same(materialization['asset_key']['path'], ['local_lakehouse_dbt'], 'unexpected Dagster asset')
        entries = {item['label']: item['entry_data'] for item in materialization['metadata_entries']}
        _same(entries['run_token']['text'], token, 'Dagster materialization run token mismatch')
        _same(entries['relations']['data'], [f'dataops_{token}_stg', f'dataops_{token}_mart'], 'Dagster materialization relation mismatch')


def _read_evidence(directory):
    files = _Files(directory)
    token = files.directory.name
    plan = CONTRACT.validate_plan(files.json('plan.json', 'plan'), executable=True)
    columns = CONTRACT.columns(plan)
    expected = CONTRACT.expected_rows(plan, FIXTURE)
    project = _project(plan)
    hashes = {name: _digest(text.encode()) for name, text in project.items()}
    _same(files.json('project-sha256.json', 'project'), hashes, 'generated project hash receipt mismatch')
    for relative, text in project.items():
        _same(files.raw('project/' + relative, 'project').decode(), text, 'generated project was changed')
    summary = files.json('summary.json', 'execution')
    execution = files.json('execution/summary.json', 'execution')
    _same(summary['plan'], plan, 'summary plan mismatch')
    _same(summary['project_sha256'], hashes, 'summary project hash mismatch')
    _same(summary['expected_columns'], columns, 'summary columns mismatch')
    _same(summary['expected_rows'], expected, 'summary independent expected rows mismatch')
    _require(summary['scope'] == 'local-experiment-data-plan' and execution['scope'] == 'local-experiment-only', 'unexpected experiment scope')
    for key, value in execution.items():
        if key not in {'scope', 'evidence'}:
            _same(summary[key], value, 'execution and plan summaries disagree')
    relations = [f'iceberg.local_experiment.dataops_{token}_{suffix}' for suffix in ('stg', 'mart')]
    _same(summary['relations'], relations, 'summary generated relations mismatch')
    _require(summary['run_token'] == token and summary['result'] == 'passed'
             and summary['fixture_unchanged'] is True and summary['deterministic'] is True and summary['errors'] == [], 'experiment did not pass')
    for phase in ('before', 'after'):
        fixture = summary['fixture_' + phase]
        _same(fixture['columns'], ['id', 'name'], 'retained fixture columns mismatch')
        _same(fixture['rows'], FIXTURE, 'retained fixture rows mismatch')
    drain = summary['query_drain']
    _require(drain['verified'] is True and drain['user'] == 'shirokuma-local-dataops-' + token
             and drain['active'] == [] and drain['errors'] == [],
             'owned query drain was not verified')
    # Cancellation may race normal completion; the later empty active set is
    # the proof of termination. Keep error text private in the hashed artifact.
    cancelled, cancel_errors = drain['cancelled'], drain['cancel_errors']
    _require(type(cancelled) is list and type(cancel_errors) is list, 'invalid query drain receipt')
    for query_id in cancelled:
        _require(type(query_id) is str and re.fullmatch('[A-Za-z0-9_]+', query_id), 'invalid cancelled query identity')
    for item in cancel_errors:
        _require(type(item) is dict and set(item) == {'query_id', 'error'}
                 and type(item['query_id']) is str and re.fullmatch('[A-Za-z0-9_]+', item['query_id'])
                 and type(item['error']) is str, 'invalid cancellation race receipt')
    cleanup = summary['cleanup']
    _same(cleanup['removed'], list(reversed(relations)), 'both generated relations were not removed')
    _require(cleanup['remaining'] == [] and cleanup['errors'] == [] and cleanup.get('status') != 'deferred', 'cleanup is incomplete')
    attempts = summary['attempts']
    labels = [item['label'] for item in attempts]
    _require(labels in [['first', 'repeat'], ['first', 'repeat', 'controlled-failure', 'retry']], 'unexpected experiment attempt sequence')
    if len(labels) == 4:
        _require(summary.get('controlled_failure_observed') is True, 'controlled failure was not verified')
    run_ids = []
    for attempt in attempts:
        label = attempt['label']
        success = label != 'controlled-failure'
        saved = files.json(f'execution/{label}/result.json', 'execution')
        _same({k: v for k, v in attempt.items() if k not in {'label', 'readback'}}, saved, 'Dagster attempt receipt mismatch')
        _require(attempt['success'] is success, 'Dagster attempt outcome mismatch')
        _same(attempt['materializations'], 1 if success else 0, 'unexpected Dagster materialization count')
        run_id = attempt['run_id']
        _uuid(run_id)
        _require(run_id not in run_ids, 'duplicate Dagster run identity')
        run_ids.append(run_id)
        commands = attempt['commands']
        _same([item['command'] for item in commands], ['build', 'test'] if success else ['build'], 'incomplete dbt command sequence')
        for command in commands:
            name = command['command']
            prefix = f'execution/{label}/{name}-target/'
            manifest = files.json(prefix + 'manifest.json', 'dbt-manifests')
            results = files.json(prefix + 'run_results.json', 'dbt-results')
            models, tests = _manifest(manifest, project, token, columns, name)
            _uuid(manifest['metadata']['invocation_id'])
            _same(results['metadata']['invocation_id'], manifest['metadata']['invocation_id'], 'dbt invocation identity mismatch')
            entries = results['results']
            expected_ids = set(tests) | (set(models) if name == 'build' else set())
            _require(len(entries) == len(expected_ids) and {item['unique_id'] for item in entries} == expected_ids, 'incomplete or duplicate dbt execution results')
            _same(command['nodes'], [{key: item.get(key) for key in ('unique_id', 'status', 'failures', 'message')} for item in entries], 'dbt summary differs from saved results')
            _same(command['exit_code'], 0 if success else 1, 'unexpected dbt exit code')
            for item in entries:
                node_id = item['unique_id']
                failing = not success and node_id == TEST_PREFIX + 'mart_expected_rows'
                _same(item['status'], 'success' if node_id in models else 'fail' if failing else 'pass', 'dbt result did not pass the required checks')
                _same(item.get('failures'), None if node_id in models else 1 if failing else 0, 'unexpected dbt test failure count')
                if node_id in models:
                    _same(item['compiled_code'].strip(), models[node_id]['compiled_code'].strip(), 'executed model SQL mismatch')
                    _same(item['relation_name'], f'"iceberg"."local_experiment"."{models[node_id]["alias"]}"', 'executed model relation mismatch')
        if success:
            _same(attempt['readback']['columns'], columns, 'readback columns mismatch')
            _same(attempt['readback']['rows'], expected, 'readback differs from independent Python oracle')
        _dagster(files, label, run_id, token, success)
    _same(summary['materializations'], sum(a['materializations'] for a in attempts), 'summary materialization count mismatch')
    files.stable()
    projection = _base_projection(token, plan['operation'])
    projection.update(evidence=files.references(), dagster_run_ids=run_ids)
    return validate_projection(projection)


def read_evidence(directory):
    """Read only one generated run directory; errors omit raw artifact contents."""
    try:
        return _read_evidence(directory)
    except EvidenceError:
        raise
    except (OSError, ValueError, TypeError, KeyError, IndexError, sqlite3.Error):
        raise EvidenceError('invalid or incomplete Data Bear evidence') from None
