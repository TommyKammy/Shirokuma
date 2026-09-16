#!/usr/bin/env python3
"""Exercise the actual official MCP client/server stdio transport and audit rows."""
from __future__ import annotations

import asyncio
import json
import sys
import uuid

from mcp import Client, StdioServerParameters, stdio_client

from core import Backends, HERE, LOCAL, STATE, TOOLS, load_module


async def smoke() -> dict:
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    logs = (STATE / 'server-stderr.log').open('a')
    calls = []
    try:
        params = StdioServerParameters(command='rtk', args=['proxy', sys.executable, str(HERE / 'server.py')])
        async with Client(stdio_client(params, errlog=logs)) as client:
            listing = await client.list_tools()
            if {tool.name for tool in listing.tools} != set(TOOLS):
                raise RuntimeError('MCP tool inventory differs from the fixed allowlist')
            for name, arguments in [
                ('gateway_policy', {}), ('shirokuma_doctor', {}),
                ('k8s_read_pods', {'limit': 5}), ('k8s_read_events', {'limit': 5}),
                ('dbt_list_models', {}), ('trino_explain_fixture', {}),
                ('openmetadata_get_lineage', {}), ('agenthouse_recent', {'limit': 3}),
                ('trino_explain_fixture', {'sql': 'DROP TABLE forbidden'}),
                ('k8s_read_pods', {'namespace': 'kube-system'}),
                ('github_issue_write', {'body': 'not-retained'}),
            ]:
                response = await client.call_tool(name, arguments=arguments, read_timeout_seconds=120)
                payload = response.structured_content
                if payload is None:
                    payload = json.loads(response.content[0].text)
                expected = 'blocked' if name == 'github_issue_write' or 'sql' in arguments or arguments.get('namespace') == 'kube-system' else 'success'
                if payload['status'] != expected or payload.get('audit') != 'delivered':
                    raise RuntimeError(f'MCP integration call {name} returned {payload.get("error_code", payload["status"])}')
                calls.append({'tool': name, 'status': payload['status'], 'event_id': payload['event_id'],
                              'task_id': payload['task_id'], 'trace_id': payload['trace_id'],
                              'span_id': payload['span_id']})
    finally:
        logs.close()
    backend = Backends()
    ids = [str(uuid.UUID(item['event_id'])) for item in calls]
    quoted = ','.join("'" + value + "'" for value in ids)
    rows = json.loads(backend.agenthouse.Client().query(
        'SELECT event_id, task_id, trace_id, status, blocked_by_policy, prompt_tokens, '
        'completion_tokens, inference_cost_estimate FROM agenthouse.pawprints FINAL '
        f'WHERE event_id IN ({quoted}) FORMAT JSON'))['data']
    if len(rows) != len(calls):
        raise RuntimeError('ClickHouse did not retain every MCP call')
    indexed = {row['event_id']: row for row in rows}
    for call in calls:
        row = indexed[call['event_id']]
        if any(row[key] != call[key] for key in ('status', 'task_id', 'trace_id')):
            raise RuntimeError('MCP and Pawprint correlation differs')
        if any(row[key] is not None for key in ('prompt_tokens', 'completion_tokens', 'inference_cost_estimate')):
            raise RuntimeError('non-model calls must not invent token or cost measurements')
    loki = load_module('gateway_loki_smoke', LOCAL / 'loki/manage.py')
    records = loki.query_task(calls[0]['task_id'])
    logs = {(row['trace_id'], row['span_id']): row for row in records}
    spans = {(row['trace_id'], row['span_id']): row
             for row in [json.loads(line) for line in (STATE / 'spans.jsonl').read_text().splitlines()]}
    for call in calls:
        key = (call['trace_id'], call['span_id'])
        if key not in logs or logs[key]['status'] != call['status']:
            raise RuntimeError('Loki did not correlate the audited MCP call')
        if key not in spans or spans[key]['attributes'].get('task_id') != call['task_id']:
            raise RuntimeError('OpenTelemetry did not emit the correlated MCP span')
    result = {'result': 'passed', 'transport': 'stdio', 'tools': len(TOOLS), 'calls': calls,
              'audited_calls': len(rows), 'loki_correlated_calls': len(calls),
              'otel_correlated_spans': len(calls),
              'policy_denials': sum(x['status'] == 'blocked' for x in calls)}
    target = STATE / ('smoke-' + uuid.uuid4().hex + '.json')
    target.write_text(json.dumps(result, indent=2) + '\n')
    target.chmod(0o600)
    return {**result, 'evidence': str(target)}


if __name__ == '__main__':
    print(json.dumps(asyncio.run(smoke())))
