#!/usr/bin/env python3
"""Observe a fixed fixture, plan locally, materialize owned tables, and verify cleanup."""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import TypedDict
import uuid

from data_provider import DataProvider
from data_contract import columns, expected_rows, model_cases, schema_evidence, validate_plan
from providers import ProviderError, load_config, unknown_metrics, validate_config
from evaluation import private_write, safe_metrics, verify_audit
from workflow import agenthouse_module, child_environment, disable_external_tracing, inventory_evidence
from run_sre import module, SpanFile
from run_sre_llm import observation as gateway_observation, verify_audits, AUDIT_FIELDS

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(LOCAL))
import local_session as SESSION
from local_trino_smoke import TrinoClient

FIXTURE_ROWS = [[1, 'shirokuma'], [2, 'polaris']]
FIXTURE_SQL = 'SELECT id, name FROM iceberg.smoke.fixture_v1 ORDER BY id'
SCHEMA_SQL = ("SELECT column_name, data_type FROM iceberg.information_schema.columns "
              "WHERE table_schema = 'smoke' AND table_name = 'fixture_v1' ORDER BY ordinal_position")
EXPECTED_OPERATION = 'name_lengths'
DEFAULT_CASE = 'name_lengths_ja'
CASE_IDS = ('uppercase_names_ja', 'name_lengths_ja', 'row_count_ja')


class DataError(RuntimeError):
    pass


def smoke_case(case_id: str = DEFAULT_CASE) -> dict:
    if case_id not in CASE_IDS:
        raise DataError('Data Bear smoke case must be one of the three fixed supported requests')
    return next(case for case in model_cases() if case['case_id'] == case_id)


def validate_observation(value: dict) -> dict:
    if (not isinstance(value, dict) or set(value) != {'schema', 'inventory', 'fixture_verified'}
            or value['schema'] != schema_evidence() or value['fixture_verified'] is not True
            or not isinstance(value['inventory'], dict)):
        raise DataError('fixed source schema and retained fixture must be verified before planning')
    try:
        inventory = inventory_evidence({'models': value['inventory'].get('models'),
            'latest_verified_run': {'verified': value['inventory'].get('verified_run_available')}})
    except Exception:
        raise DataError('model inventory is not a bounded verified observation') from None
    return {'schema': schema_evidence(), 'inventory': inventory, 'fixture_verified': True}


def verify_fixture(client) -> dict:
    schema = client.query(SCHEMA_SQL)
    rows = client.query(FIXTURE_SQL)
    expected_schema = [[column['name'], column['type']] for column in schema_evidence()['columns']]
    expected_rows({'schema_version': 1, 'source': 'fixture_v1', 'operation': EXPECTED_OPERATION}, rows.get('rows'))
    if (schema.get('columns') != ['column_name', 'data_type'] or schema.get('rows') != expected_schema
            or rows.get('columns') != ['id', 'name'] or rows.get('rows') != FIXTURE_ROWS):
        raise DataError('retained fixture schema or rows differ from the fixed source')
    return schema_evidence()


def validate_smoke_plan(value: dict, case_id: str = DEFAULT_CASE) -> dict:
    plan = validate_plan(value, executable=True)
    if plan['operation'] != smoke_case(case_id)['expected_operation']:
        raise DataError('plan operation does not match the fixed smoke request')
    return dict(plan)


def verify_materialization(receipt: dict, plan: dict, case_id: str = DEFAULT_CASE) -> dict:
    plan = validate_smoke_plan(plan, case_id)
    if not isinstance(receipt, dict):
        raise DataError('DataOps receipt is missing')
    if (receipt.get('result') != 'passed' or receipt.get('plan') != plan
            or receipt.get('fixture_unchanged') is not True or receipt.get('deterministic') is not True
            or type(receipt.get('materializations')) is not int or receipt['materializations'] != 2):
        raise DataError('DataOps did not verify the selected plan and retained fixture')
    token = receipt.get('run_token')
    if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{32}', token):
        raise DataError('DataOps receipt has no bounded run identity')
    evidence = ROOT / 'tmp/local-experiment/data-plans' / token
    if receipt.get('evidence') != str(evidence):
        raise DataError('DataOps evidence path does not belong to its fixed run identity')
    attempts = receipt.get('attempts')
    if (not isinstance(attempts, list) or any(not isinstance(item, dict) for item in attempts)
            or [item.get('label') for item in attempts] != ['first', 'repeat']):
        raise DataError('DataOps must verify exactly the first and repeated materializations')
    expected = expected_rows(plan, FIXTURE_ROWS)
    for attempt in attempts:
        readback = attempt.get('readback')
        if (attempt.get('success') is not True or type(attempt.get('materializations')) is not int
                or attempt['materializations'] != 1 or not isinstance(readback, dict)
                or readback.get('columns') != columns(plan)
                or json.dumps(readback.get('rows'), ensure_ascii=False) != json.dumps(expected, ensure_ascii=False)):
            raise DataError('DataOps result differs from the independent fixed-fixture oracle')
    drain, cleanup = receipt.get('query_drain'), receipt.get('cleanup')
    if (not isinstance(drain, dict) or drain.get('verified') is not True
            or not isinstance(cleanup, dict) or cleanup.get('errors') != [] or cleanup.get('remaining') != []):
        raise DataError('DataOps query drain or cleanup is not verified')
    removed = cleanup.get('removed')
    expected_removed = {f'iceberg.local_experiment.dataops_{token}_stg',
                        f'iceberg.local_experiment.dataops_{token}_mart'}
    if (not isinstance(removed, list) or any(not isinstance(name, str) for name in removed)
            or len(removed) != 2 or set(removed) != expected_removed):
        raise DataError('DataOps cleanup does not identify the two owned relations')
    return {'result': 'passed', 'plan': plan, 'run_token': token, 'materializations': 2,
            'evidence': str(evidence),
            'fixture_unchanged': True, 'deterministic': True, 'query_drain_verified': True,
            'cleanup': {'removed': removed, 'remaining': [], 'errors': []},
            'columns': columns(plan), 'rows': expected}


class State(TypedDict, total=False):
    observation: dict
    plan: dict
    inference: dict
    execution_receipt: dict
    verification: dict


def build_graph(model, read, execute, audit, tracer, *, case_id: str = DEFAULT_CASE):
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.memory import InMemorySaver
    case = smoke_case(case_id)

    async def observe(state):
        return {'observation': validate_observation(await read())}

    async def plan(state):
        from opentelemetry.trace import Status, StatusCode
        with tracer.start_as_current_span('llm.data_plan', record_exception=False,
                                          set_status_on_exception=False) as span:
            started = time.monotonic()
            try:
                response = await asyncio.to_thread(model.complete, 'data_plan',
                    {'request': case['request'], 'schema': state['observation']['schema']})
            except Exception as error:
                metrics = safe_metrics(error.metrics if isinstance(error, ProviderError) else {},
                                       int((time.monotonic() - started) * 1000))
                span.set_status(Status(StatusCode.ERROR, 'MODEL_RESPONSE_FAILED'))
                audit('llm.data_plan', 'failed', metrics, span, inference=True)
                raise DataError('Data Bear model response failed') from None
            metrics = safe_metrics(response, int((time.monotonic() - started) * 1000))
            try:
                selected = validate_smoke_plan(response['content'], case_id)
            except Exception:
                span.set_status(Status(StatusCode.ERROR, 'MODEL_PLAN_REJECTED'))
                audit('llm.data_plan', 'blocked', metrics, span, inference=True)
                raise DataError('Data Bear plan did not match the bounded smoke request') from None
            audit('llm.data_plan', 'success', metrics, span, inference=True)
            span.set_status(Status(StatusCode.OK))
            return {'plan': selected, 'inference': metrics}

    async def materialize(state):
        from opentelemetry.trace import Status, StatusCode
        with tracer.start_as_current_span('bear.data.materialize', record_exception=False,
                                          set_status_on_exception=False) as span:
            started = time.monotonic()
            try:
                receipt = await execute(copy.deepcopy(state['plan']))
                verify_materialization(receipt, state['plan'], case_id)
            except BaseException as error:
                span.set_status(Status(StatusCode.ERROR, 'MATERIALIZATION_REJECTED'))
                audit('bear.data.materialize', 'failed', unknown_metrics(int((time.monotonic() - started) * 1000)), span)
                if not isinstance(error, Exception):
                    raise
                raise DataError('DataOps materialization receipt did not pass bounded verification') from None
            audit('bear.data.materialize', 'success', unknown_metrics(int((time.monotonic() - started) * 1000)), span)
            span.set_status(Status(StatusCode.OK))
            return {'execution_receipt': receipt}

    async def verify(state):
        return {'verification': verify_materialization(state['execution_receipt'], state['plan'], case_id)}

    graph = StateGraph(State)
    for name, node in [('observe', observe), ('plan', plan), ('execute', materialize), ('verify', verify)]:
        graph.add_node(name, node)
    graph.add_edge(START, 'observe')
    graph.add_edge('observe', 'plan')
    graph.add_edge('plan', 'execute')
    graph.add_edge('execute', 'verify')
    graph.add_edge('verify', END)
    return graph.compile(checkpointer=InMemorySaver())


def adapter_environment() -> dict:
    value = {key: os.environ[key] for key in ('PATH', 'HOME', 'USER', 'TMPDIR', 'LANG', 'LC_ALL') if key in os.environ}
    value.update(DAGSTER_DISABLE_TELEMETRY='true', DBT_SEND_ANONYMOUS_USAGE_STATS='false',
                 DO_NOT_TRACK='1', OTEL_SDK_DISABLED='true', LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false')
    return value


def verify_adapter_receipt(receipt: dict) -> None:
    """Bind stdout to the child's private, host-selected run directory and saved receipt."""
    if not isinstance(receipt, dict) or not re.fullmatch(r'[0-9a-f]{32}', str(receipt.get('run_token', ''))):
        raise DataError('DataOps receipt has no bounded run identity')
    directory = ROOT / 'tmp/local-experiment/data-plans' / receipt['run_token']
    summary = directory / 'summary.json'
    if (receipt.get('evidence') != str(directory) or directory.resolve() != directory
            or not directory.is_dir() or directory.stat().st_mode & 0o777 != 0o700
            or directory.stat().st_uid != os.getuid()
            or summary.is_symlink() or not summary.is_file() or summary.stat().st_mode & 0o777 != 0o600
            or summary.stat().st_uid != os.getuid()
            or summary.stat().st_size > 1024 * 1024):
        raise DataError('DataOps evidence is not the private receipt for its fixed run identity')
    if json.dumps(json.loads(summary.read_bytes()), sort_keys=True) != json.dumps(receipt, sort_keys=True):
        raise DataError('DataOps stdout and saved receipt differ')


async def execute_adapter(plan: dict, url: str, output: Path, *, case_id: str = DEFAULT_CASE) -> dict:
    """No model-controlled executable, SQL, argument, URL or path reaches the child."""
    plan = validate_smoke_plan(plan, case_id)
    TrinoClient(url)  # Validate the host-created tunnel endpoint before spawning anything.
    private_write(output / 'plan.json', plan)
    private_write(output / 'adapter-stdout.json', {})
    private_write(output / 'adapter-stderr.log', {})
    command = ['rtk', 'proxy', str(LOCAL / 'dataops/.venv/bin/python'), str(LOCAL / 'dataops/plan_run.py'),
               '--plan-json', str(output / 'plan.json'), '--url', url, '--timeout', '120']
    with (output / 'adapter-stdout.json').open('w') as stdout, (output / 'adapter-stderr.log').open('w') as stderr:
        process = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr,
                                   start_new_session=True, env=adapter_environment())
        try:
            await asyncio.wait_for(asyncio.to_thread(process.wait), 900)
        except BaseException:
            # Keep the parent tunnel alive while the child drains queries and removes owned tables.
            await asyncio.shield(asyncio.to_thread(SESSION.stop_group, process, signal.SIGINT, 180))
            raise
        finally:
            if process.poll() is None:
                await asyncio.shield(asyncio.to_thread(SESSION.stop_group, process, signal.SIGINT, 180))
            else:
                SESSION.signal_group(process, signal.SIGKILL)
    raw = (output / 'adapter-stdout.json').read_bytes()
    if len(raw) > 1024 * 1024:
        raise DataError('DataOps receipt exceeds the bounded limit')
    try:
        receipt = json.loads(raw)
    except Exception:
        raise DataError('DataOps returned no structured receipt') from None
    if process.returncode != 0 or not isinstance(receipt, dict):
        raise DataError('DataOps execution failed; private receipt retained')
    verify_adapter_receipt(receipt)
    return receipt


async def run(config: dict, output: Path, *, case_id: str = DEFAULT_CASE) -> dict:
    from mcp import Client, StdioServerParameters, stdio_client
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.trace import Status, StatusCode
    validate_config(config)
    if config['active_provider'] != 'local':
        raise DataError('Data Bear requires an explicitly configured local model')
    case = smoke_case(case_id)
    profile = config['providers']['local']
    model = DataProvider(profile)
    disable_external_tracing()
    output.mkdir(parents=True, mode=0o700)
    task_id = 'workflow-' + uuid.uuid4().hex
    agenthouse = agenthouse_module()
    loki = module('data_bear_loki', LOCAL / 'loki/manage.py')
    client = agenthouse.Client()
    provider = TracerProvider(resource=Resource.create({'service.name': 'shirokuma-data-local-llm'}))
    provider.add_span_processor(SimpleSpanProcessor(SpanFile(output / 'spans.jsonl')))
    tracer = provider.get_tracer('shirokuma.bears.data', '0.1.0')
    calls, records = [], []
    result = {'result': 'failed', 'scope': 'fixed-fixture-owned-table-smoke', 'task_id': task_id,
              'provider': profile['provider'], 'model': profile['model'], 'evidence_path': str(output),
              'case_id': case_id, 'expected_operation': case['expected_operation'], 'tool_calls': calls, 'audit_records': records,
              'retries': 0, 'cloud_calls': 0, 'retained_data_mutation_authorized': False}
    started = time.monotonic()
    with tracer.start_as_current_span('bear.data.assess', record_exception=False, set_status_on_exception=False) as root_span:
        context = root_span.get_span_context()
        trace_id = f'{context.trace_id:032x}'
        result['trace_id'] = trace_id
        root_span.set_attribute('task_id', task_id)

        def audit(action, status, metrics, span, *, inference=False):
            event = agenthouse.event(action, 'llm-local-compatible' if inference else 'local-dataops',
                agent_id='data-bear-local-llm', task_id=task_id, trace_id=trace_id, status=status,
                blocked_by_policy=status == 'blocked', model_provider=profile['provider'] if inference else 'none',
                model_name=profile['model'] if inference else '', **metrics)
            record = {key: event[key] for key in AUDIT_FIELDS}
            record.update(span_id=f'{span.get_span_context().span_id:016x}', audit_delivery='pending')
            records.append(record)
            private_write(output / 'result.json', result)
            try:
                agenthouse.insert(client, event)
                verify_audit(client, event)
                loki.push_record({'task_id': task_id, 'trace_id': trace_id, 'span_id': record['span_id'],
                                  'action': action, 'status': status})
            except Exception:
                record['audit_delivery'] = 'failed'
                raise DataError('Data Bear audit delivery or readback failed') from None
            record['audit_delivery'] = 'delivered'

        try:
            if client.query('SELECT 1').strip() != '1':
                raise DataError('Data Bear audit preflight failed')
            SESSION.check_context()
            with SESSION.trino_tunnel() as url:
                trino = TrinoClient(url, timeout=30)
                params = StdioServerParameters(command='rtk', args=['proxy', str(LOCAL / 'gateway/.venv/bin/python'),
                    str(LOCAL / 'gateway/server.py')], cwd=ROOT,
                    env=child_environment(task_id, f'00-{trace_id}-{context.span_id:016x}-01'))
                private_write(output / 'gateway-stderr.log', {})
                with (output / 'gateway-stderr.log').open('w') as log:
                    async with Client(stdio_client(params, errlog=log)) as gateway:
                        async def read():
                            response = await gateway.call_tool('dbt_list_models', {}, read_timeout_seconds=120)
                            data, receipt = gateway_observation(response, 'dbt_list_models', task_id, trace_id)
                            calls.append(receipt)
                            schema = await asyncio.to_thread(verify_fixture, trino)
                            observed = {'inventory': inventory_evidence(data), 'schema': schema, 'fixture_verified': True}
                            private_write(output / 'observation.json', observed)
                            return observed

                        async def execute(plan):
                            result['plan'] = dict(plan)
                            private_write(output / 'result.json', result)
                            return await execute_adapter(plan, url, output, case_id=case_id)

                        graph = build_graph(model, read, execute, audit, tracer, case_id=case_id)
                        options = {'configurable': {'thread_id': task_id}, 'recursion_limit': 10, 'callbacks': []}
                        state = await graph.ainvoke({}, options)
                        checkpoint = graph.get_state(options)
                        if checkpoint.next or checkpoint.values.get('verification') != state['verification']:
                            raise DataError('Data Bear completed checkpoint differs')
            result.update(plan=state['plan'], inference=state['inference'], verification=state['verification'],
                          adapter_evidence=state['execution_receipt'].get('evidence'), checkpoint='completed-in-memory')
            audit('bear.data.assess', 'success', unknown_metrics(int((time.monotonic() - started) * 1000)), root_span)
            ids = [str(uuid.UUID(row['event_id'])) for row in calls + records]
            quoted = ','.join("'" + value + "'" for value in ids)
            rows = json.loads(client.query('SELECT ' + ', '.join(AUDIT_FIELDS) +
                ' FROM agenthouse.pawprints FINAL WHERE event_id IN (' + quoted + ') FORMAT JSON'))['data']
            verify_audits(rows, loki.query_task(task_id), calls, records, task_id, trace_id)
            result.update(result='passed', correlated_events=len(rows))
            root_span.set_status(Status(StatusCode.OK))
        except Exception:
            result.update(result='failed', error_code='DATA_OBSERVATION_PLAN_EXECUTION_OR_AUDIT_FAILED')
            result.pop('checkpoint', None)
            root_span.set_status(Status(StatusCode.ERROR, result['error_code']))
            if not any(row['action_type'] == 'bear.data.assess' for row in records):
                try:
                    audit('bear.data.assess', 'failed', unknown_metrics(int((time.monotonic() - started) * 1000)), root_span)
                except Exception:
                    pass
    provider.shutdown()
    private_write(output / 'result.json', result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=LOCAL / 'llm/providers.qwen-local.json')
    parser.add_argument('--case', choices=CASE_IDS, default=DEFAULT_CASE)
    args = parser.parse_args()
    output = ROOT / 'tmp/local-experiment/data-bear' / uuid.uuid4().hex
    try:
        with SESSION.interruption_handlers():
            report = asyncio.run(run(load_config(args.config), output, case_id=args.case))
    except SESSION.SessionInterrupted as error:
        # asyncio.run cancels and awaits the graph; execute_adapter finishes its
        # bounded child cleanup before the managed tunnel is allowed to close.
        report = {'result': 'interrupted', 'signal': error.signum, 'evidence_path': str(output),
                  'cleanup': 'not-claimed; inspect the private adapter receipt'}
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(output / 'interruption.json', report)
        print(json.dumps(report))
        return 128 + error.signum
    except Exception:
        report = {'result': 'failed', 'error_code': 'DATA_CONFIGURATION_OR_SETUP_FAILED', 'evidence_path': str(output)}
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(output / 'result.json', report)
    print(json.dumps(report))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
