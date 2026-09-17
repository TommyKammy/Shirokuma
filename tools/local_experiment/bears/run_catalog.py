#!/usr/bin/env python3
"""Curate only verified historical Data Plan entities and prove a second application is a no-op."""
from __future__ import annotations

import argparse
import asyncio
import copy
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from typing import TypedDict
import uuid

from catalog_provider import CatalogProvider
from catalog_contract import TARGETS, FIELDS, build_evidence, validate_plan
from providers import ProviderError, load_config, validate_config, unknown_metrics
from evaluation import private_write, safe_metrics, verify_audit
from workflow import agenthouse_module, disable_external_tracing
from run_sre import module, SpanFile
from run_sre_llm import AUDIT_FIELDS, verify_audits

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(LOCAL))
from metadata import curate, data_plan_evidence
from metadata.client import Client as MetadataClient
from metadata.runtime import STATE as METADATA_STATE, check_runtime
from local_session import interruption_handlers, SessionInterrupted


class CatalogError(RuntimeError):
    pass


@contextmanager
def trace_lifecycle(provider):
    try:
        yield
    finally:
        provider.shutdown()


def verify_saved_spans(path: Path, records: list, trace_id: str) -> None:
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise CatalogError('catalog local trace evidence is unavailable')
    try:
        spans = [json.loads(line) for line in path.read_text().splitlines() if line]
        expected = {record['span_id']: record for record in records}
        if len(expected) != len(records) or len(spans) != len(expected):
            raise ValueError()
        if {span['span_id'] for span in spans} != set(expected):
            raise ValueError()
        for span in spans:
            record = expected[span['span_id']]
            if (span['trace_id'] != trace_id or span['name'] != record['action_type']
                    or span['status'] != 'OK' or record['status'] != 'success'):
                raise ValueError()
    except Exception:
        raise CatalogError('catalog saved local spans do not match the successful audit phases') from None


def source_directory(token: str) -> Path:
    if type(token) is not str or not re.fullmatch(r'[0-9a-f]{32}', token):
        raise CatalogError('catalog source must be one explicit local Data Plan run token')
    return ROOT / 'tmp/local-experiment/data-plans' / token


def digest(projection: dict) -> str:
    return hashlib.sha256(json.dumps(projection, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def presence(value: dict) -> dict:
    if type(value) is not dict or set(value) != set(TARGETS):
        raise CatalogError('catalog observation requires two fixed targets')
    for fields in value.values():
        if (type(fields) is not dict or set(fields) != set(FIELDS)
                or any(type(flag) is not bool for flag in fields.values())
                or fields['description'] != fields['owner']
                or fields['lineage'] and not fields['description']):
            raise CatalogError('catalog field presence is incomplete or inconsistent')
    return copy.deepcopy(value)


def validate_observation(value: dict) -> dict:
    if type(value) is not dict or set(value) != {'projection', 'catalog_state'}:
        raise CatalogError('catalog requires verified source evidence and current metadata')
    projection = data_plan_evidence.validate_projection(value['projection'])
    state = value['catalog_state']
    if (type(state) is not dict or state.get('run_token') != projection['run_token']
            or state.get('evidence_sha256') != digest(projection)):
        raise CatalogError('catalog observation is not bound to the verified source run')
    targets = presence(state.get('targets'))
    table_ids = state.get('table_ids')
    if type(table_ids) is not dict or set(table_ids) != set(TARGETS):
        raise CatalogError('catalog observation has no fixed target identities')
    for target in TARGETS:
        identity = table_ids[target]
        if targets[target]['description']:
            try:
                if type(identity) is not str or str(uuid.UUID(identity)) != identity:
                    raise ValueError()
            except (ValueError, AttributeError):
                raise CatalogError('existing catalog target has no bounded identity') from None
        elif identity is not None:
            raise CatalogError('missing catalog target cannot carry an existing identity')
    evidence = build_evidence({target: {field: not targets[target][field] for field in FIELDS} for target in TARGETS})
    return {'projection': projection, 'catalog_state': copy.deepcopy(state), 'evidence': evidence}


def verify_receipt(value: dict, observation: dict, *, previous: dict | None = None) -> dict:
    projection = observation['projection']
    if (type(value) is not dict or value.get('result') != 'passed'
            or value.get('run_token') != projection['run_token'] or value.get('evidence_sha256') != digest(projection)
            or value.get('historical_models_removed_from_trino') is not True):
        raise CatalogError('catalog receipt is not bound to the verified historical run')
    targets = presence(value.get('targets'))
    if not all(all(fields.values()) for fields in targets.values()):
        raise CatalogError('catalog readback did not verify every selected metadata field')
    table_ids = value.get('table_ids')
    if type(table_ids) is not dict or set(table_ids) != set(TARGETS):
        raise CatalogError('catalog receipt has no fixed target identities')
    try:
        if any(type(item) is not str or str(uuid.UUID(item)) != item for item in table_ids.values()):
            raise ValueError()
    except (ValueError, AttributeError):
        raise CatalogError('catalog receipt has invalid target identities') from None
    if len(set(table_ids.values())) != 2:
        raise CatalogError('catalog targets must have distinct identities')
    if any(identity is not None and table_ids[target] != identity
           for target, identity in observation['catalog_state']['table_ids'].items()):
        raise CatalogError('catalog application changed an existing target identity')
    present = presence(observation['catalog_state']['targets'])
    created_tables = 0 if previous is not None else sum(not fields['description'] for fields in present.values())
    created_edges = 0 if previous is not None else sum(not fields['lineage'] for fields in present.values())
    counts = {'created_tables': created_tables, 'reused_tables': 2 - created_tables,
              'created_edges': created_edges, 'verified_tables': 2, 'verified_edges': 2,
              'writes': created_tables + created_edges}
    if any(type(value.get(key)) is not int or value[key] != expected for key, expected in counts.items()):
        raise CatalogError('catalog write and readback counts differ from observed missing fields')
    if type(value.get('noop')) is not bool or value['noop'] != (counts['writes'] == 0):
        raise CatalogError('catalog no-op claim disagrees with verified writes')
    if previous is not None and (table_ids != previous['table_ids'] or value['evidence_sha256'] != previous['evidence_sha256']):
        raise CatalogError('catalog target identities changed across repeated application')
    return {'result': 'passed', 'run_token': projection['run_token'], 'evidence_sha256': digest(projection),
            **counts, 'noop': counts['writes'] == 0, 'historical_models_removed_from_trino': True,
            'table_ids': dict(table_ids), 'targets': targets}


class State(TypedDict, total=False):
    observation: dict
    plan: dict
    inference: dict
    applied: dict
    repeated: dict
    verification: dict


def build_graph(model, observe, apply, audit, tracer):
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.memory import InMemorySaver
    from opentelemetry.trace import Status, StatusCode

    async def collect(state):
        with tracer.start_as_current_span('bear.catalog.observe', record_exception=False, set_status_on_exception=False) as span:
            started = time.monotonic()
            try:
                observation = validate_observation(await observe())
            except BaseException as error:
                span.set_status(Status(StatusCode.ERROR, 'OBSERVATION_FAILED'))
                try:
                    audit('bear.catalog.observe', 'failed', unknown_metrics(int((time.monotonic() - started) * 1000)), span)
                finally:
                    if not isinstance(error, Exception):
                        raise error
                raise CatalogError('catalog source or metadata observation failed') from None
            audit('bear.catalog.observe', 'success', unknown_metrics(int((time.monotonic() - started) * 1000)), span)
            span.set_status(Status(StatusCode.OK))
            return {'observation': observation}

    async def plan(state):
        with tracer.start_as_current_span('llm.catalog_plan', record_exception=False, set_status_on_exception=False) as span:
            started = time.monotonic()
            try:
                response = await asyncio.to_thread(model.complete, 'catalog_plan', state['observation']['evidence'])
            except BaseException as error:
                metrics = safe_metrics(error.metrics if isinstance(error, ProviderError) else {},
                                       int((time.monotonic() - started) * 1000))
                span.set_status(Status(StatusCode.ERROR, 'MODEL_RESPONSE_FAILED'))
                try:
                    audit('llm.catalog_plan', 'failed', metrics, span, inference=True)
                finally:
                    if not isinstance(error, Exception):
                        raise error
                raise CatalogError('catalog model response failed') from None
            metrics = safe_metrics(response, int((time.monotonic() - started) * 1000))
            try:
                selected = validate_plan(response['content'], state['observation']['evidence'], executable=True)
            except Exception:
                span.set_status(Status(StatusCode.ERROR, 'MODEL_PLAN_REJECTED'))
                audit('llm.catalog_plan', 'blocked', metrics, span, inference=True)
                raise CatalogError('catalog model plan omitted or exceeded the observed missing fields') from None
            audit('llm.catalog_plan', 'success', metrics, span, inference=True)
            span.set_status(Status(StatusCode.OK))
            return {'plan': selected, 'inference': metrics}

    async def curate_node(state, repeated=False):
        action = 'bear.catalog.repeat' if repeated else 'bear.catalog.apply'
        with tracer.start_as_current_span(action, record_exception=False, set_status_on_exception=False) as span:
            started = time.monotonic()
            previous = state.get('applied') if repeated else None
            try:
                receipt = await apply(state['observation'], copy.deepcopy(state['plan']), previous)
                receipt = verify_receipt(receipt, state['observation'], previous=previous)
            except BaseException as error:
                span.set_status(Status(StatusCode.ERROR, 'CURATION_OR_READBACK_FAILED'))
                try:
                    audit(action, 'failed', unknown_metrics(int((time.monotonic() - started) * 1000)), span)
                finally:
                    if not isinstance(error, Exception):
                        raise error
                raise CatalogError('catalog application or authoritative readback failed') from None
            audit(action, 'success', unknown_metrics(int((time.monotonic() - started) * 1000)), span)
            span.set_status(Status(StatusCode.OK))
            return {'repeated' if repeated else 'applied': receipt}

    async def first(state):
        return await curate_node(state)

    async def repeat(state):
        return await curate_node(state, True)

    async def verify(state):
        repeated = verify_receipt(state['repeated'], state['observation'], previous=state['applied'])
        return {'verification': {'result': 'passed', 'run_token': repeated['run_token'],
                                 'tables': 2, 'lineage_edges': 2, 'repeat_noop': True,
                                 'table_ids': repeated['table_ids'], 'historical_models_removed_from_trino': True}}

    graph = StateGraph(State)
    nodes = [('observe', collect), ('plan', plan), ('apply', first), ('repeat', repeat), ('verify', verify)]
    for name, node in nodes:
        graph.add_node(name, node)
    graph.add_edge(START, nodes[0][0])
    for left, right in zip(nodes, nodes[1:]):
        graph.add_edge(left[0], right[0])
    graph.add_edge(nodes[-1][0], END)
    return graph.compile(checkpointer=InMemorySaver())


async def run(config: dict, token: str, output: Path) -> dict:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.trace import Status, StatusCode
    validate_config(config)
    if config['active_provider'] != 'local':
        raise CatalogError('Catalog Curator requires an explicitly configured local model')
    source = source_directory(token)
    profile = config['providers']['local']
    model = CatalogProvider(profile)
    disable_external_tracing()
    output.mkdir(parents=True, mode=0o700)
    task_id = 'workflow-' + uuid.uuid4().hex
    agenthouse, loki = agenthouse_module(), module('catalog_bear_loki', LOCAL / 'loki/manage.py')
    audit_client = agenthouse.Client()
    provider = TracerProvider(resource=Resource.create({'service.name': 'shirokuma-catalog-local-llm'}))
    provider.add_span_processor(SimpleSpanProcessor(SpanFile(output / 'spans.jsonl')))
    tracer = provider.get_tracer('shirokuma.bears.catalog', '0.1.0')
    records = []
    result = {'result': 'failed', 'scope': 'verified-historical-data-plan-metadata-only', 'task_id': task_id,
              'source_run_token': token, 'evidence_path': str(output), 'provider': profile['provider'], 'model': profile['model'],
              'audit_records': records, 'retries': 0, 'cloud_calls': 0, 'application_started': False,
              'limits': ['Historical catalog entities do not imply live SQL tables.',
                         'Partial owned metadata is retained for verified retry; no rollback is claimed.']}
    started = time.monotonic()
    interruption = None
    with trace_lifecycle(provider), tracer.start_as_current_span(
            'bear.catalog.assess', record_exception=False, set_status_on_exception=False) as root_span:
        trace_id = f'{root_span.get_span_context().trace_id:032x}'
        result['trace_id'] = trace_id
        root_span.set_attribute('task_id', task_id)

        def audit(action, status, metrics, span, *, inference=False):
            event = agenthouse.event(action, 'llm-local-compatible' if inference else 'openmetadata',
                agent_id='catalog-bear-local-llm', task_id=task_id, trace_id=trace_id, status=status,
                blocked_by_policy=status == 'blocked', model_provider=profile['provider'] if inference else 'none',
                model_name=profile['model'] if inference else '', **metrics)
            record = {key: event[key] for key in AUDIT_FIELDS}
            record.update(span_id=f'{span.get_span_context().span_id:016x}', audit_delivery='pending')
            records.append(record)
            private_write(output / 'result.json', result)
            try:
                agenthouse.insert(audit_client, event)
                verify_audit(audit_client, event)
                loki.push_record({'task_id': task_id, 'trace_id': trace_id, 'span_id': record['span_id'],
                                  'action': action, 'status': status})
            except BaseException as error:
                record['audit_delivery'] = 'failed'
                if not isinstance(error, Exception):
                    raise
                raise CatalogError('catalog audit delivery or readback failed') from None
            record['audit_delivery'] = 'delivered'

        try:
            if audit_client.query('SELECT 1').strip() != '1':
                raise CatalogError('catalog audit preflight failed')
            check_runtime()
            client = MetadataClient()
            client.authenticate(METADATA_STATE)

            async def observe():
                projection = data_plan_evidence.read_evidence(source)
                state = curate.preflight(client, projection)
                private_write(output / 'source-evidence.json', projection)
                private_write(output / 'catalog-observation.json',
                              {key: state[key] for key in ('run_token', 'evidence_sha256', 'targets', 'table_ids')})
                return {'projection': projection, 'catalog_state': state}

            async def apply(observation, plan, previous):
                # Re-read host evidence before each API application. No model-supplied
                # description, owner, source, path, entity identity or SQL is accepted.
                projection = data_plan_evidence.read_evidence(source)
                if projection != observation['projection']:
                    raise CatalogError('source evidence changed after planning')
                validate_plan(plan, observation['evidence'], executable=True)
                state = observation['catalog_state']
                if previous is not None:
                    state = curate.preflight(client, projection)
                    if (state['table_ids'] != previous['table_ids']
                            or not all(all(fields.values()) for fields in presence(state['targets']).values())):
                        raise CatalogError('repeat observation is no longer a no-op on the same target identities')
                result.update(plan=plan, application_started=True)
                private_write(output / 'result.json', result)
                private_write(output / 'plan.json', plan)
                # Synchronous bounded API calls avoid uncancelled mutation threads.
                progress_path = output / ('repeat-progress.json' if previous is not None else 'apply-progress.json')
                receipt = curate.apply(client, projection, state,
                                       progress=lambda value: private_write(progress_path, value))
                private_write(output / ('repeat.json' if previous is not None else 'apply.json'), receipt)
                return receipt

            graph = build_graph(model, observe, apply, audit, tracer)
            options = {'configurable': {'thread_id': task_id}, 'recursion_limit': 12, 'callbacks': []}
            state = await graph.ainvoke({}, options)
            checkpoint = graph.get_state(options)
            if checkpoint.next or checkpoint.values.get('verification') != state['verification']:
                raise CatalogError('Catalog Curator completed checkpoint differs')
            result.update(plan=state['plan'], inference=state['inference'], applied=state['applied'], repeated=state['repeated'],
                          verification=state['verification'], checkpoint='completed-in-memory')
            audit('bear.catalog.assess', 'success', unknown_metrics(int((time.monotonic() - started) * 1000)), root_span)
            ids = [str(uuid.UUID(record['event_id'])) for record in records]
            quoted = ','.join("'" + value + "'" for value in ids)
            rows = json.loads(audit_client.query('SELECT ' + ', '.join(AUDIT_FIELDS) +
                ' FROM agenthouse.pawprints FINAL WHERE event_id IN (' + quoted + ') FORMAT JSON'))['data']
            verify_audits(rows, loki.query_task(task_id), [], records, task_id, trace_id)
            result.update(result='passed', correlated_events=len(rows))
            root_span.set_status(Status(StatusCode.OK))
        except BaseException as error:
            if not isinstance(error, Exception):
                interruption = error
            result.update(result='interrupted' if interruption is not None else 'failed',
                          error_code='CATALOG_OBSERVATION_PLAN_APPLY_OR_AUDIT_FAILED')
            result.pop('checkpoint', None)
            root_span.set_status(Status(StatusCode.ERROR, result['error_code']))
            if not any(row['action_type'] == 'bear.catalog.assess' for row in records):
                try:
                    audit('bear.catalog.assess', 'failed', unknown_metrics(int((time.monotonic() - started) * 1000)), root_span)
                except BaseException as audit_error:
                    if interruption is None and not isinstance(audit_error, Exception):
                        interruption = audit_error
                        result['result'] = 'interrupted'
    if result['result'] == 'passed':
        try:
            verify_saved_spans(output / 'spans.jsonl', records, trace_id)
            result['saved_otel_spans_verified'] = len(records)
        except Exception:
            result.update(result='failed', error_code='CATALOG_SAVED_TRACE_VERIFICATION_FAILED')
            result.pop('checkpoint', None)
    try:
        private_write(output / 'result.json', result)
    finally:
        if interruption is not None:
            raise interruption
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=LOCAL / 'llm/providers.qwen-local.json')
    parser.add_argument('--data-plan-token', required=True)
    args = parser.parse_args()
    output = ROOT / 'tmp/local-experiment/catalog-bear' / uuid.uuid4().hex
    try:
        with interruption_handlers():
            report = asyncio.run(run(load_config(args.config), args.data_plan_token, output))
    except SessionInterrupted as error:
        report = {'result': 'interrupted', 'signal': error.signum, 'evidence_path': str(output), 'rollback_performed': False}
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(output / 'interruption.json', report)
        print(json.dumps(report))
        return 128 + error.signum
    except Exception:
        report = {'result': 'failed', 'error_code': 'CATALOG_CONFIGURATION_OR_SETUP_FAILED', 'evidence_path': str(output)}
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(output / 'result.json', report)
    print(json.dumps(report))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
