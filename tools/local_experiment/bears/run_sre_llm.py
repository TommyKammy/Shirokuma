#!/usr/bin/env python3
"""Observe -> local model triage -> bounded MCP re-observation -> verified comparison."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import TypedDict
import uuid

from sre_provider import SREProvider
from sre_contract import build_evidence, validate_triage, compare_observations, project_observations
from providers import ProviderError, load_config, unknown_metrics
from workflow import child_environment, disable_external_tracing
from evaluation import private_write, safe_metrics
from run_sre import module, SpanFile

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parent
ROOT = HERE.parents[2]
TOOLS = {'shirokuma_doctor': {}, 'k8s_read_pods': {'limit': 50}, 'k8s_read_events': {'limit': 50},
         'local_platform_status': {}}
AUDIT_FIELDS = ('event_id', 'task_id', 'trace_id', 'action_type', 'status', 'model_provider',
                'model_name', 'prompt_tokens', 'completion_tokens', 'inference_cost_estimate', 'duration_ms')


class SREError(RuntimeError):
    pass


def preserve_interruption(call, *args, **kwargs):
    """Keep a signal being unwound if secondary receipt/audit cleanup fails."""
    original = sys.exc_info()[1]
    try:
        return call(*args, **kwargs)
    except BaseException:
        if original is not None and not isinstance(original, Exception):
            raise original
        raise


@contextmanager
def trace_lifecycle(provider):
    try:
        yield
    finally:
        preserve_interruption(provider.shutdown)


@asynccontextmanager
async def gateway_lifecycle(client):
    """Allow owned MCP cleanup without replacing or suppressing a cancellation."""
    interruption = None
    try:
        async with client as gateway:
            try:
                yield gateway
            except BaseException as error:
                if not isinstance(error, Exception):
                    interruption = error
                raise
    finally:
        if interruption is not None:
            raise interruption


class State(TypedDict, total=False):
    snapshots: dict
    evidence: dict
    triage: dict
    inference: dict
    comparisons: dict
    followup_evidence: dict


def observation(response, tool: str, task_id: str, trace_id: str) -> tuple[dict, dict]:
    payload = response.structured_content
    if (response.is_error or not isinstance(payload, dict) or payload.get('status') != 'success'
            or payload.get('audit') != 'delivered' or payload.get('task_id') != task_id
            or payload.get('trace_id') != trace_id or not isinstance(payload.get('data'), dict)
            or not re.fullmatch(r'[0-9a-f]{16}', str(payload.get('span_id', '')))):
        raise SREError('Gateway did not deliver a successful correlated SRE observation')
    event_id = str(uuid.UUID(payload['event_id']))
    return payload['data'], {'tool': tool, 'event_id': event_id, 'span_id': payload['span_id'],
                             'action_type': 'mcp.' + tool}


def build_graph(model, read, audit, tracer):
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.memory import InMemorySaver

    async def collect(state):
        snapshots = {tool: await read(tool, 'initial') for tool in TOOLS}
        evidence = build_evidence(snapshots['shirokuma_doctor'], snapshots['k8s_read_pods'], snapshots['k8s_read_events'],
                                  snapshots['local_platform_status'])
        return {'snapshots': snapshots, 'evidence': evidence}

    async def triage(state):
        from opentelemetry.trace import Status, StatusCode
        with tracer.start_as_current_span('llm.sre_triage', record_exception=False,
                                          set_status_on_exception=False) as span:
            started = time.monotonic()
            try:
                response = await asyncio.to_thread(model.complete, 'sre_triage', state['evidence'])
            except BaseException as error:
                metrics = safe_metrics(error.metrics if isinstance(error, ProviderError) else {},
                                       int((time.monotonic() - started) * 1000))
                span.set_status(Status(StatusCode.ERROR, 'MODEL_RESPONSE_FAILED'))
                preserve_interruption(audit, 'llm.sre_triage', 'failed', metrics, span, inference=True)
                if not isinstance(error, Exception):
                    raise
                raise SREError('SRE model response failed') from None
            metrics = safe_metrics(response, int((time.monotonic() - started) * 1000))
            try:
                proposed = validate_triage(response['content'], state['evidence'])
            except Exception:
                span.set_status(Status(StatusCode.ERROR, 'MODEL_TRIAGE_REJECTED'))
                audit('llm.sre_triage', 'blocked', metrics, span, inference=True)
                raise SREError('SRE model proposed unsupported observations or claims') from None
            audit('llm.sre_triage', 'success', metrics, span, inference=True)
            span.set_status(Status(StatusCode.OK))
            return {'triage': proposed, 'inference': metrics}

    async def followup(state):
        snapshots, followup_evidence = {}, {}
        for check in state['triage']['checks']:
            tool = check['tool']
            after = await read(tool, 'followup')
            snapshots[tool] = after
            followup_evidence[tool] = hashlib.sha256(json.dumps(after, sort_keys=True).encode()).hexdigest()
        return {'comparisons': compare_observations(state['snapshots'], snapshots), 'followup_evidence': followup_evidence}

    graph = StateGraph(State)
    graph.add_node('collect', collect)
    graph.add_node('triage', triage)
    graph.add_node('followup', followup)
    graph.add_edge(START, 'collect')
    graph.add_edge('collect', 'triage')
    graph.add_edge('triage', 'followup')
    graph.add_edge('followup', END)
    return graph.compile(checkpointer=InMemorySaver())


def verify_audits(rows: list, logs: list, calls: list, records: list, task_id: str, trace_id: str) -> None:
    expected = {row['event_id']: row for row in calls + records}
    if len(expected) != len(calls) + len(records) or len(rows) != len(expected):
        raise SREError('missing or duplicate retained SRE audit records')
    if {row.get('event_id') for row in rows} != set(expected):
        raise SREError('retained SRE audit identities differ')
    for row in rows:
        source = expected[row['event_id']]
        status = source.get('status', 'success')
        if type(status) is not str or status not in {'success', 'blocked', 'failed'}:
            raise SREError('expected audit status is invalid')
        if (row['task_id'] != task_id or row['trace_id'] != trace_id or row['status'] != status
                or row['action_type'] != source['action_type']):
            raise SREError('retained SRE audit correlation differs')
        for key in ('model_provider', 'model_name', 'prompt_tokens', 'completion_tokens', 'inference_cost_estimate'):
            target = source.get(key, 'none' if key == 'model_provider' else '' if key == 'model_name' else None)
            value = row[key]
            if key in {'prompt_tokens', 'completion_tokens'} and value is not None:
                if isinstance(value, str) and value.isascii() and value.isdigit():
                    value = int(value)
                if type(value) is not int:
                    raise SREError('retained SRE usage has invalid types')
            if value != target:
                raise SREError('retained SRE model identity or usage differs')
        if 'duration_ms' in source:
            duration = row['duration_ms']
            if isinstance(duration, str) and duration.isascii() and duration.isdigit():
                duration = int(duration)
            if type(duration) is not int or not 0 <= duration <= 2**64 - 1 or duration != source['duration_ms']:
                raise SREError('retained SRE latency differs')
        if not any(log.get('task_id') == task_id and log.get('trace_id') == trace_id
                   and log.get('span_id') == source['span_id'] and log.get('status') == status
                   and log.get('action') == source['action_type'] for log in logs):
            raise SREError('Loki is missing a correlated SRE span/action')


async def run(config: dict, output: Path) -> dict:
    from mcp import Client, StdioServerParameters, stdio_client
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.trace import Status, StatusCode
    profile = config['providers'][config['active_provider']]
    if config['active_provider'] != 'local':
        raise SREError('SRE inference requires a configured real local model')
    model = SREProvider(profile)
    disable_external_tracing()
    output.mkdir(parents=True, mode=0o700)
    task_id = 'workflow-' + uuid.uuid4().hex
    agenthouse = module('llm_sre_agenthouse', LOCAL / 'agenthouse/manage.py')
    loki = module('llm_sre_loki', LOCAL / 'loki/manage.py')
    client = agenthouse.Client()
    provider = TracerProvider(resource=Resource.create({'service.name': 'shirokuma-sre-local-llm'}))
    provider.add_span_processor(SimpleSpanProcessor(SpanFile(output / 'spans.jsonl')))
    tracer = provider.get_tracer('shirokuma.bears.sre.llm', '0.1.0')
    calls, records, receipts = [], [], []
    result = {'result': 'failed', 'scope': 'local-read-only-sre-followup', 'task_id': task_id,
              'provider': profile['provider'], 'model': profile['model'], 'evidence_path': str(output),
              'observations_file': 'observations.json',
              'tool_calls': calls, 'audit_records': records, 'remediation_executed': False,
              'limits': ['Bounded observations; no general root-cause diagnosis.',
                         'Unselected sources are not rechecked.',
                         'A short comparison cannot establish continuous availability.']}
    started = time.monotonic()
    with trace_lifecycle(provider), tracer.start_as_current_span('bear.sre.assess', record_exception=False, set_status_on_exception=False) as root_span:
        context = root_span.get_span_context()
        trace_id = f'{context.trace_id:032x}'
        result['trace_id'] = trace_id
        root_span.set_attribute('task_id', task_id)

        def audit(action, status, metrics, span, *, inference=False):
            value = agenthouse.event(action, 'kubernetes' if not inference else 'llm-local-compatible',
                agent_id='sre-bear-local-llm', task_id=task_id, trace_id=trace_id, status=status,
                blocked_by_policy=status == 'blocked',
                model_provider=profile['provider'] if inference else 'none',
                model_name=profile['model'] if inference else '', **metrics)
            record = {key: value[key] for key in AUDIT_FIELDS}
            record.update(span_id=f'{span.get_span_context().span_id:016x}', audit_delivery='pending')
            records.append(record)
            private_write(output / 'result.json', result)
            try:
                agenthouse.insert(client, value)
                loki.push_record({'task_id': task_id, 'trace_id': trace_id, 'span_id': record['span_id'],
                                  'action': action, 'status': status})
            except Exception:
                record['audit_delivery'] = 'failed'
                raise SREError('SRE audit delivery failed') from None
            record['audit_delivery'] = 'delivered'

        try:
            if client.query('SELECT 1').strip() != '1':
                raise SREError('SRE audit preflight failed')
            params = StdioServerParameters(command='rtk', args=['proxy', str(LOCAL / 'gateway/.venv/bin/python'),
                str(LOCAL / 'gateway/server.py')], cwd=ROOT,
                env=child_environment(task_id, f'00-{trace_id}-{context.span_id:016x}-01'))
            # The child can write sensitive stderr before the workflow finishes.
            fd = os.open(output / 'gateway-stderr.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as log:
                async with gateway_lifecycle(Client(stdio_client(params, errlog=log))) as gateway:
                    async def read(tool, phase):
                        if tool not in TOOLS:
                            raise SREError('SRE read escaped the fixed tool allowlist')
                        response = await gateway.call_tool(tool, dict(TOOLS[tool]), read_timeout_seconds=120)
                        data, receipt = observation(response, tool, task_id, trace_id)
                        if tool == 'shirokuma_doctor':
                            data = {**data, 'observed_at': datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')}
                        receipt['phase'] = phase
                        calls.append(receipt)
                        data = project_observations({tool: data})[tool]
                        receipts.append({'tool': tool, 'phase': phase, 'snapshot': data})
                        private_write(output / 'observations.json', receipts)
                        return data
                    graph = build_graph(model, read, audit, tracer)
                    graph_config = {'configurable': {'thread_id': task_id}, 'recursion_limit': 8, 'callbacks': []}
                    deadline = profile['timeout_seconds'] + (len(TOOLS) + 2) * 120 + 60
                    state = await asyncio.wait_for(graph.ainvoke({}, graph_config), deadline)
                    checkpoint = graph.get_state(graph_config)
                    if checkpoint.next or checkpoint.values.get('comparisons') != state['comparisons']:
                        raise SREError('SRE completed checkpoint differs')
            result.update(evidence=state['evidence'], triage=state['triage'], inference=state['inference'],
                          comparisons=state['comparisons'], followup_sha256=state['followup_evidence'],
                          initial_sha256=hashlib.sha256(json.dumps(state['snapshots'], sort_keys=True).encode()).hexdigest(),
                          checkpoint='completed-in-memory')
            audit('bear.sre.assess', 'success', unknown_metrics(int((time.monotonic() - started) * 1000)), root_span)
            ids = [str(uuid.UUID(row['event_id'])) for row in calls + records]
            quoted = ','.join("'" + value + "'" for value in ids)
            rows = json.loads(client.query('SELECT ' + ', '.join(AUDIT_FIELDS) +
                ' FROM agenthouse.pawprints FINAL WHERE event_id IN (' + quoted + ') FORMAT JSON'))['data']
            verify_audits(rows, loki.query_task(task_id), calls, records, task_id, trace_id)
            result.update(result='passed', correlated_events=len(rows))
            root_span.set_status(Status(StatusCode.OK))
        except BaseException as error:
            result.update(result='failed' if isinstance(error, Exception) else 'interrupted',
                          error_code='SRE_OBSERVATION_INFERENCE_OR_AUDIT_FAILED')
            for key in ('triage', 'comparisons', 'checkpoint'):
                result.pop(key, None)
            root_span.set_status(Status(StatusCode.ERROR, result['error_code']))
            if not any(row['action_type'] == 'bear.sre.assess' for row in records):
                try:
                    preserve_interruption(audit, 'bear.sre.assess', 'failed', unknown_metrics(int((time.monotonic() - started) * 1000)), root_span)
                except Exception:
                    pass  # Local receipt retains the failed delivery and measured inference usage.
            if not isinstance(error, Exception):
                try:
                    private_write(output / 'result.json', result)
                finally:
                    raise error
    private_write(output / 'result.json', result)
    for file in output.iterdir():
        file.chmod(0o600)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=LOCAL / 'llm/providers.qwen-local.json')
    args = parser.parse_args()
    output = ROOT / 'tmp/local-experiment/sre-llm' / uuid.uuid4().hex
    try:
        report = asyncio.run(run(load_config(args.config), output))
    except Exception:
        report = {'result': 'failed', 'error_code': 'SRE_CONFIGURATION_OR_SETUP_FAILED', 'evidence_path': str(output)}
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(output / 'result.json', report)
    print(json.dumps(report))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
