#!/usr/bin/env python3
"""Collect audited MCP snapshots and classify the local SRE scenario deterministically."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
import time
import uuid
from pathlib import Path

from mcp import Client, StdioServerParameters, stdio_client
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Status, StatusCode

from diagnostics import diagnose, verify_correlation

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parent
ROOT = HERE.parents[2]
STATE = ROOT / 'tmp/local-experiment/bears'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class SpanFile(SpanExporter):
    def __init__(self, path):
        self.path = path

    def export(self, spans):
        for span in spans:
            context = span.get_span_context()
            with self.path.open('a') as stream:
                stream.write(json.dumps({'name': span.name, 'trace_id': f'{context.trace_id:032x}',
                    'span_id': f'{context.span_id:016x}', 'start_time_unix_nano': span.start_time,
                    'end_time_unix_nano': span.end_time, 'status': span.status.status_code.name,
                    'attributes': dict(span.attributes or {})}) + '\n')
        self.path.chmod(0o600)
        return SpanExportResult.SUCCESS


async def run() -> dict:
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.chmod(0o700)
    token = uuid.uuid4().hex
    output = STATE / token
    output.mkdir(mode=0o700)
    task_id = 'workflow-' + token
    provider = TracerProvider(resource=Resource.create({'service.name': 'shirokuma-sre-deterministic'}))
    provider.add_span_processor(SimpleSpanProcessor(SpanFile(output / 'spans.jsonl')))
    tracer = provider.get_tracer('shirokuma.bears.sre', '0.1.0')
    agenthouse = module('sre_agenthouse', LOCAL / 'agenthouse/manage.py')
    loki = module('sre_loki', LOCAL / 'loki/manage.py')
    started = time.monotonic()
    result = {'result': 'failed', 'mode': 'deterministic-read-only', 'llm_inference': False,
              'task_id': task_id, 'evidence': str(output), 'tool_calls': []}
    with tracer.start_as_current_span('bear.sre.diagnose', record_exception=False,
                                      set_status_on_exception=False) as span:
        context = span.get_span_context()
        trace_id, span_id = f'{context.trace_id:032x}', f'{context.span_id:016x}'
        result.update({'trace_id': trace_id, 'span_id': span_id})
        span.set_attribute('task_id', task_id)
        span.set_attribute('mode', 'deterministic-read-only')
        try:
            params = StdioServerParameters(command='rtk', args=['proxy', str(LOCAL / 'gateway/.venv/bin/python'),
                str(LOCAL / 'gateway/server.py')], env={
                    'SHIROKUMA_GATEWAY_TASK_ID': task_id,
                    'SHIROKUMA_GATEWAY_PARENT_TRACEPARENT': f'00-{trace_id}-{span_id}-01'})
            with (output / 'gateway-stderr.log').open('w') as log:
                async with Client(stdio_client(params, errlog=log)) as client:
                    snapshots = {}
                    for name, arguments in [('shirokuma_doctor', {}),
                                             ('k8s_read_pods', {'limit': 50}),
                                             ('k8s_read_events', {'limit': 50})]:
                        response = await client.call_tool(name, arguments, read_timeout_seconds=120)
                        payload = response.structured_content
                        if (response.is_error or payload['status'] != 'success'
                                or payload.get('audit') != 'delivered'):
                            raise RuntimeError('audited gateway observation failed')
                        if payload['task_id'] != task_id or payload['trace_id'] != trace_id:
                            raise RuntimeError('gateway trace or task correlation differs')
                        result['tool_calls'].append({'tool': name, 'event_id': payload['event_id'],
                                                      'span_id': payload['span_id']})
                        snapshots[name] = payload['data']
            result['diagnosis'] = diagnose(snapshots['shirokuma_doctor'], snapshots['k8s_read_pods'],
                                           snapshots['k8s_read_events'])
            result['input_sha256'] = hashlib.sha256(json.dumps(snapshots, sort_keys=True).encode()).hexdigest()
            result['result'] = 'passed'
            span.set_attribute('health', result['diagnosis']['health'])
            span.set_status(Status(StatusCode.OK))
        except Exception:
            result['error_code'] = 'OBSERVATION_OR_DIAGNOSIS_FAILED'
            span.set_status(Status(StatusCode.ERROR, result['error_code']))
        value = agenthouse.event('bear.sre.diagnose', 'kubernetes',
            status='success' if result['result'] == 'passed' else 'failed',
            agent_id='sre-bear-deterministic', task_id=task_id, trace_id=trace_id,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)), model_provider='none', model_name='')
        try:
            agenthouse.insert(agenthouse.Client(), value)
            loki.push_record({'task_id': task_id, 'trace_id': trace_id, 'span_id': span_id,
                              'action': 'bear.sre.diagnose', 'status': value['status']})
            result['event_id'] = value['event_id']
            result['audit'] = 'delivered'
        except Exception:
            result.update({'result': 'failed', 'audit': 'failed', 'error_code': 'AUDIT_DELIVERY_FAILED'})
            result.pop('diagnosis', None)
            span.set_status(Status(StatusCode.ERROR, 'AUDIT_DELIVERY_FAILED'))
    provider.shutdown()
    # Verify retained correlation, including the workflow's own observation event.
    if result['result'] == 'passed':
        try:
            ids = [x['event_id'] for x in result['tool_calls']] + [result['event_id']]
            quoted = ','.join("'" + str(uuid.UUID(value)) + "'" for value in ids)
            rows = json.loads(agenthouse.Client().query(
                'SELECT event_id, task_id, trace_id, status, prompt_tokens, completion_tokens, inference_cost_estimate '
                f'FROM agenthouse.pawprints FINAL WHERE event_id IN ({quoted}) FORMAT JSON'))['data']
            logs = loki.query_task(task_id)
            if not verify_correlation(rows, logs, result['tool_calls'], event_id=result['event_id'],
                                      task_id=task_id, trace_id=trace_id, span_id=span_id):
                raise RuntimeError('audit correlation differs')
            result['correlated_events'] = 4
        except Exception:
            result.update({'result': 'failed', 'error_code': 'AUDIT_CORRELATION_FAILED'})
            result.pop('diagnosis', None)
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    for file in output.iterdir():
        file.chmod(0o600)
    return result


if __name__ == '__main__':
    report = asyncio.run(run())
    print(json.dumps(report))
    raise SystemExit(0 if report['result'] == 'passed' else 1)
