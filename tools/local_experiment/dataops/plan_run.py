#!/usr/bin/env python3
"""Compile a bounded data plan into a disposable private dbt experiment."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import uuid


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUN = load_module('data_plan_dataops', HERE / 'run.py')
CONTRACT = load_module('data_plan_contract', HERE.parent / 'bears/data_contract.py')


def private_text(path: Path, text: str) -> None:
    with path.open('x', encoding='utf-8') as output:
        output.write(text)
    path.chmod(0o600)


def json_text(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + '\n'


def sql_literal(value) -> str:
    # These values come from the fixed fixture's Python oracle, never model SQL.
    if type(value) is int:
        return f'cast({value} as bigint)'
    if isinstance(value, str):
        return "cast('" + value.replace("'", "''") + "' as varchar)"
    raise RUN.DataOpsError('unsupported oracle value for generated dbt verification')


def prepare_project(output: Path, plan: dict, columns: list[str], expected: list[list]) -> tuple[Path, dict]:
    project = output / 'project'
    for directory in (project, project / 'models', project / 'tests'):
        directory.mkdir(mode=0o700)
    owner = "shirokuma-local-dataops:{{ var('run_token') }}"
    schema = {'version': 2, 'sources': [{'name': 'retained_smoke', 'database': 'iceberg',
              'schema': 'smoke', 'tables': [{'name': 'fixture_v1'}]}], 'models': [
        {'name': 'stg_fixture', 'description': owner, 'columns': [
            {'name': 'id', 'data_tests': ['not_null', 'unique']},
            {'name': 'name', 'data_tests': ['not_null']}]},
        {'name': 'mart_fixture', 'description': owner, 'columns': [
            {'name': name, 'data_tests': ['not_null', 'unique'] if name == 'id' else ['not_null']}
            for name in columns]}]}
    selected = ', '.join(columns)
    values = ',\n    '.join('(' + ', '.join(sql_literal(value) for value in row) + ')' for row in expected)
    comparison = (f'with expected ({selected}) as (values\n    {values}\n)\n'
                  f'(select {selected} from {{{{ ref(\'mart_fixture\') }}}}\n'
                  f'except select {selected} from expected)\nunion all\n'
                  f'(select {selected} from expected\n'
                  f'except select {selected} from {{{{ ref(\'mart_fixture\') }}}})\n')
    files = {
        'dbt_project.yml': (HERE / 'dbt/dbt_project.yml').read_text(),
        'models/stg_fixture.sql': (HERE / 'dbt/models/stg_fixture.sql').read_text(),
        'models/mart_fixture.sql': "{{ config(alias=var('relation_prefix') ~ '_mart') }}\n\n"
                                   + CONTRACT.compile_select(plan).rstrip() + '\n',
        'models/schema.yml': json_text(schema),
        'tests/mart_expected_rows.sql': "select count(*) as actual_rows\nfrom {{ ref('mart_fixture') }}\n"
            + 'having count(*) <> {{ ' + str(len(expected) + 1)
            + " if var('exercise_failure', false) else " + str(len(expected)) + ' }}\n',
        'tests/mart_expected_values.sql': comparison,
    }
    hashes = {}
    for relative, text in files.items():
        private_text(project / relative, text)
        hashes[relative] = hashlib.sha256(text.encode()).hexdigest()
    return project, hashes


def make_private(output: Path) -> None:
    for directory, _subdirs, files in os.walk(output, followlinks=False):
        path = Path(directory)
        if not path.is_symlink():
            path.chmod(0o700)
        for filename in files:
            child = path / filename
            if not child.is_symlink():
                child.chmod(0o600)


def run_plan(plan: dict, url: str, output: Path | None = None, *, exercise_failure: bool = False,
             timeout: float = 300, token: str | None = None, client=None, materializer=None) -> dict:
    # Snapshot and validate before output creation or database interaction. Neither
    # a model-provided path nor a caller's later mutation changes this experiment.
    plan = dict(CONTRACT.validate_plan(plan, executable=True))
    columns = CONTRACT.columns(plan)
    expected = CONTRACT.expected_rows(plan, RUN.FIXTURE_ROWS)
    if type(exercise_failure) is not bool or type(timeout) not in (int, float) or not 1 <= timeout <= 900:
        raise RUN.DataOpsError('invalid bounded execution options')
    validated_client = RUN.TRINO.TrinoClient(url)
    client = validated_client if client is None else client
    if client.url != validated_client.url:
        raise RUN.DataOpsError('provided client must use the validated loopback endpoint')
    token = token or uuid.uuid4().hex
    RUN.names_for(token)
    output = Path(output) if output is not None else ROOT / 'tmp/local-experiment/data-plans' / token
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    output.chmod(0o700)
    try:
        private_text(output / 'plan.json', json_text(plan))
        project, hashes = prepare_project(output, plan, columns, expected)
        private_text(output / 'project-sha256.json', json_text(hashes))
        options = {'project': project, 'expected_columns': columns, 'expected_rows': expected}
        if materializer is not None:
            options['materializer'] = materializer
        report = RUN.run_experiment(client, output / 'execution', exercise_failure, timeout, token, **options)
        report.update(scope='local-experiment-data-plan', plan=plan, evidence=str(output),
                      execution_evidence=str(output / 'execution'), project='project',
                      project_sha256=hashes, expected_columns=columns, expected_rows=expected,
                      materializations=sum(attempt.get('materializations', 0) for attempt in report['attempts']))
        private_text(output / 'summary.json', json_text(report))
        return report
    finally:
        make_private(output)


def read_plan(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise RUN.DataOpsError('plan must be a regular local JSON file')
    with path.open('rb') as source:
        raw = source.read(4097)
    if len(raw) > 4096:
        raise RUN.DataOpsError('plan JSON exceeds the local size limit')
    return CONTRACT.validate_plan(json.loads(raw), executable=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan-json', type=Path, required=True)
    parser.add_argument('--url', default='http://127.0.0.1:18080')
    parser.add_argument('--timeout', type=float, default=300)
    parser.add_argument('--exercise-failure', action='store_true')
    args = parser.parse_args()
    try:
        report = run_plan(read_plan(args.plan_json), args.url,
                          timeout=args.timeout, exercise_failure=args.exercise_failure)
    except Exception:
        report = {'result': 'failed', 'error_code': 'DATA_PLAN_VALIDATION_OR_SETUP_FAILED'}
    print(json.dumps(report))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
