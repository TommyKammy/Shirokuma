#!/usr/bin/env python3
"""Observe two fixed local queries and select bounded reads without rewriting SQL."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
from typing import TypedDict
import uuid

from query_provider import QueryProvider
from query_contract import project_observation, followup_summary, assess_followups, validate_plan
from providers import ProviderError, load_config, unknown_metrics, validate_config
from evaluation import private_write, safe_metrics, verify_audit
from workflow import agenthouse_module, disable_external_tracing
from run_sre import module, SpanFile
from run_sre_llm import AUDIT_FIELDS, verify_audits
from run_catalog import trace_lifecycle, verify_saved_spans

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(LOCAL))
import local_session as SESSION
from query_doctor.observe import QueryObserver


class QueryError(RuntimeError):
    pass


class State(TypedDict, total=False):
    observation: dict
    evidence: dict
    plan: dict
    inference: dict
    followups: dict
    assessment: dict


def build_graph(model, observe, followup, audit, tracer):
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.memory import InMemorySaver
    from opentelemetry.trace import Status, StatusCode

    async def collect(state):
        with tracer.start_as_current_span('bear.query.observe', record_exception=False, set_status_on_exception=False) as span:
            started = time.monotonic()
            try:
                observed = await observe()
                evidence = project_observation(observed)
            except BaseException as error:
                span.set_status(Status(StatusCode.ERROR, 'OBSERVATION_FAILED'))
                audit('bear.query.observe', 'failed', unknown_metrics(int((time.monotonic() - started) * 1000)), span)
                if not isinstance(error, Exception):
                    raise
                raise QueryError('fixed query observation failed') from None
            audit('bear.query.observe', 'success', unknown_metrics(int((time.monotonic() - started) * 1000)), span)
            span.set_status(Status(StatusCode.OK))
            return {'observation': observed, 'evidence': evidence}

    async def plan(state):
        with tracer.start_as_current_span('llm.query_plan', record_exception=False, set_status_on_exception=False) as span:
            started = time.monotonic()
            try:
                response = await asyncio.to_thread(model.complete, 'query_plan', state['evidence'])
            except BaseException as error:
                metrics = safe_metrics(error.metrics if isinstance(error, ProviderError) else {},
                                       int((time.monotonic() - started) * 1000))
                span.set_status(Status(StatusCode.ERROR, 'MODEL_RESPONSE_FAILED'))
                audit('llm.query_plan', 'failed', metrics, span, inference=True)
                if not isinstance(error, Exception):
                    raise
                raise QueryError('query model response failed') from None
            metrics = safe_metrics(response, int((time.monotonic() - started) * 1000))
            try:
                selected = validate_plan(response['content'], state['evidence'])
            except Exception:
                span.set_status(Status(StatusCode.ERROR, 'MODEL_PLAN_REJECTED'))
                audit('llm.query_plan', 'blocked', metrics, span, inference=True)
                raise QueryError('query model plan omitted or exceeded the fixed follow-up observations') from None
            audit('llm.query_plan', 'success', metrics, span, inference=True)
            span.set_status(Status(StatusCode.OK))
            return {'plan': selected, 'inference': metrics}

    async def read_more(state):
        selected = validate_plan(state['plan'], state['evidence'])
        observations = {}
        for check in selected['checks']:
            tool = check['tool']
            action = 'bear.query.' + tool
            with tracer.start_as_current_span(action, record_exception=False, set_status_on_exception=False) as span:
                started = time.monotonic()
                try:
                    observed = await followup(tool)
                    followup_summary(state['observation'], tool, observed)
                except BaseException as error:
                    span.set_status(Status(StatusCode.ERROR, 'FOLLOWUP_FAILED'))
                    audit(action, 'failed', unknown_metrics(int((time.monotonic() - started) * 1000)), span)
                    if not isinstance(error, Exception):
                        raise
                    raise QueryError('fixed query follow-up failed') from None
                audit(action, 'success', unknown_metrics(int((time.monotonic() - started) * 1000)), span)
                span.set_status(Status(StatusCode.OK))
                observations[tool] = observed
        return {'followups': observations}

    async def assess(state):
        return {'assessment': assess_followups(state['observation'], state['plan'], state['followups'])}

    graph = StateGraph(State)
    nodes = [('observe', collect), ('plan', plan), ('followup', read_more), ('assess', assess)]
    for name, node in nodes:
        graph.add_node(name, node)
    graph.add_edge(START, nodes[0][0])
    for left, right in zip(nodes, nodes[1:]):
        graph.add_edge(left[0], right[0])
    graph.add_edge(nodes[-1][0], END)
    return graph.compile(checkpointer=InMemorySaver())


async def run(config: dict, output: Path) -> dict:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.trace import Status, StatusCode
    validate_config(config)
    if config['active_provider'] != 'local':
        raise QueryError('Query Doctor requires an explicitly configured local model')
    profile = config['providers']['local']
    model = QueryProvider(profile)
    disable_external_tracing()
    output.mkdir(parents=True, mode=0o700)
    token = uuid.uuid4().hex
    task_id = 'workflow-' + token
    agenthouse, loki = agenthouse_module(), module('query_bear_loki', LOCAL / 'loki/manage.py')
    audit_client = agenthouse.Client()
    provider = TracerProvider(resource=Resource.create({'service.name': 'shirokuma-query-local-llm'}))
    provider.add_span_processor(SimpleSpanProcessor(SpanFile(output / 'spans.jsonl')))
    tracer = provider.get_tracer('shirokuma.bears.query', '0.1.0')
    records = []
    result = {'result': 'failed', 'scope': 'fixed-two-row-fixture-only', 'task_id': task_id,
              'query_run_token': token, 'evidence_path': str(output), 'provider': profile['provider'], 'model': profile['model'],
              'audit_records': records, 'retries': 0, 'cloud_calls': 0, 'sql_rewrites': 0,
              'root_cause': 'undetermined', 'performance_gain': 'unmeasured', 'cleanup_verified': False}
    started = time.monotonic()
    with trace_lifecycle(provider), tracer.start_as_current_span(
            'bear.query.assess', record_exception=False, set_status_on_exception=False) as root_span:
        trace_id = f'{root_span.get_span_context().trace_id:032x}'
        result['trace_id'] = trace_id
        root_span.set_attribute('task_id', task_id)

        def audit(action, status, metrics, span, *, inference=False):
            event = agenthouse.event(action, 'llm-local-compatible' if inference else 'trino-read-only',
                agent_id='query-bear-local-llm', task_id=task_id, trace_id=trace_id, status=status,
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
            except Exception:
                record['audit_delivery'] = 'failed'
                raise QueryError('query audit delivery or readback failed') from None
            record['audit_delivery'] = 'delivered'

        try:
            if audit_client.query('SELECT 1').strip() != '1':
                raise QueryError('query audit preflight failed')
            SESSION.check_context()
            with SESSION.trino_tunnel() as url:
                observer = QueryObserver(url, run_token=token)

                def save_receipt():
                    receipt = observer.receipt()
                    private_write(output / 'observer-receipt.json', receipt)
                    result['cleanup_verified'] = receipt.get('cleanup_verified') is True
                    return receipt

                async def observe():
                    # Keep bounded synchronous HTTP on the same thread. Cancellation
                    # cannot leave a background query worker beyond tunnel lifetime.
                    try:
                        value = observer.collect()
                        evidence = project_observation(value)
                        private_write(output / 'observation.json', value)
                        private_write(output / 'model-evidence.json', evidence)
                        return value
                    finally:
                        save_receipt()

                async def followup(tool):
                    try:
                        value = observer.followup(tool)
                        private_write(output / (tool + '.json'), value)
                        return value
                    finally:
                        save_receipt()

                try:
                    graph = build_graph(model, observe, followup, audit, tracer)
                    options = {'configurable': {'thread_id': task_id}, 'recursion_limit': 10, 'callbacks': []}
                    state = await graph.ainvoke({}, options)
                    checkpoint = graph.get_state(options)
                    if checkpoint.next or checkpoint.values.get('assessment') != state['assessment']:
                        raise QueryError('Query Doctor completed checkpoint differs')
                    result.update(plan=state['plan'], inference=state['inference'], assessment=state['assessment'])
                    private_write(output / 'plan.json', state['plan'])
                finally:
                    receipt = save_receipt()
                if (receipt.get('run_token') != token or receipt.get('cleanup_verified') is not True
                        or receipt.get('target_history_verified') is not True or receipt.get('outcome') != 'completed'):
                    raise QueryError('query completion was not verified; inspect the observer receipt')
            result['checkpoint'] = 'completed-in-memory'
            audit('bear.query.assess', 'success', unknown_metrics(int((time.monotonic() - started) * 1000)), root_span)
            ids = [str(uuid.UUID(record['event_id'])) for record in records]
            quoted = ','.join("'" + value + "'" for value in ids)
            rows = json.loads(audit_client.query('SELECT ' + ', '.join(AUDIT_FIELDS) +
                ' FROM agenthouse.pawprints FINAL WHERE event_id IN (' + quoted + ') FORMAT JSON'))['data']
            verify_audits(rows, loki.query_task(task_id), [], records, task_id, trace_id)
            result.update(result='passed', correlated_events=len(rows))
            root_span.set_status(Status(StatusCode.OK))
        except BaseException as error:
            result.update(result='failed', error_code='QUERY_OBSERVATION_PLAN_FOLLOWUP_OR_AUDIT_FAILED')
            result.pop('checkpoint', None)
            root_span.set_status(Status(StatusCode.ERROR, result['error_code']))
            if not any(row['action_type'] == 'bear.query.assess' for row in records):
                try:
                    audit('bear.query.assess', 'failed', unknown_metrics(int((time.monotonic() - started) * 1000)), root_span)
                except Exception:
                    pass
            private_write(output / 'result.json', result)
            if not isinstance(error, Exception):
                raise
    if result['result'] == 'passed':
        try:
            verify_saved_spans(output / 'spans.jsonl', records, trace_id)
            result['saved_otel_spans_verified'] = len(records)
        except Exception:
            result.update(result='failed', error_code='QUERY_SAVED_TRACE_VERIFICATION_FAILED')
            result.pop('checkpoint', None)
    private_write(output / 'result.json', result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=LOCAL / 'llm/providers.qwen-local.json')
    args = parser.parse_args()
    output = ROOT / 'tmp/local-experiment/query-bear' / uuid.uuid4().hex
    try:
        with SESSION.interruption_handlers():
            report = asyncio.run(run(load_config(args.config), output))
    except SESSION.SessionInterrupted as error:
        report = {'result': 'interrupted', 'signal': error.signum, 'evidence_path': str(output),
                  'cleanup': 'not-claimed; inspect observer-receipt.json; server run limit is 30 seconds'}
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(output / 'interruption.json', report)
        print(json.dumps(report))
        return 128 + error.signum
    except Exception:
        report = {'result': 'failed', 'error_code': 'QUERY_CONFIGURATION_OR_SETUP_FAILED', 'evidence_path': str(output)}
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(output / 'result.json', report)
    print(json.dumps(report))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
