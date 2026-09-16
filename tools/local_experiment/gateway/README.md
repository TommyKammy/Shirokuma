# Local MCP gateway and Pawprint telemetry

This is the local functional slice of WP-L2-AGENT-001 and WP-L2-OBS-001. It uses
the official Python MCP SDK 2.2.0 and OpenTelemetry SDK 1.44.0 in an isolated
Python 3.12 environment. `uv.lock` fixes the full dependency resolution and
artifact hashes. The server exposes stdio only; it opens no public or local
HTTP listener and does not activate GitHub tools.

```sh
rtk proxy uv sync --frozen --project tools/local_experiment/gateway
rtk proxy tools/local_experiment/gateway/.venv/bin/python tools/local_experiment/gateway/smoke.py
```

An MCP host starts `tools/local_experiment/gateway/.venv/bin/python` with
`tools/local_experiment/gateway/server.py` as its argument. The adjacent
`smoke.py` demonstrates the official SDK's `Client` / `StdioServerParameters`
stdio transport. In SDK v2 the Python result attribute is `structured_content`;
protocol JSON continues to use MCP's standard field aliases.

| Tool | Input boundary | Result |
|---|---|---|
| `gateway_policy` | No arguments | Fixed allowlist and local scope |
| `shirokuma_doctor` | No arguments | Existing `local-experiment` doctor report |
| `k8s_read_pods` | Only `shirokuma-dev`; limit 1–50 | Names, phases, readiness and restart counts |
| `k8s_read_events` | Only `shirokuma-dev`; limit 1–50 | Reasons and object identities; no free-text messages or Secret events |
| `dbt_list_models` | No arguments | Fixed project and latest successful DataOps artifact dependencies |
| `trino_explain_fixture` | No SQL argument | EXPLAIN of the fixed retained fixture SELECT through a temporary managed tunnel |
| `openmetadata_get_lineage` | No entity or URL argument | Lineage for the fixed retained fixture |
| `agenthouse_recent` | Limit 1–20; status enum | Bounded structured Pawprint projection |

The dispatcher rejects unknown tools, extra arguments, invalid types, alternate
namespaces, and out-of-range limits before invoking a backend. It never accepts
shell commands, SQL, URLs, filesystem paths, or credentials from tool inputs.
Kubernetes context, service names, local API endpoints and repository paths are
fixed. Backend errors return a bounded code rather than raw command stderr or
exception details. A successful doctor tool call means the report was read;
its reported component readiness remains distinct.

Every valid MCP `tools/call` request, including policy denials and backend
failures, emits a normalized ClickHouse Pawprint and a Loki record. Unknown
tool names are normalized to `mcp.unknown_tool`. Raw arguments, prompts, query
parameters, backend errors, API tokens and secrets are not audit fields. Model
token and inference cost fields remain null for these non-model calls.
Malformed JSON-RPC envelopes rejected by the SDK are transport errors rather
than dispatched tool calls.

The response includes `task_id`, `trace_id`, `span_id`, `event_id`, duration and
audit delivery. OpenTelemetry generates genuine SDK spans; the bounded JSONL
span exporter records them under `tmp/local-experiment/gateway/spans.jsonl`.
Normalized local logs use the same identities in `events.jsonl`. Loki and
ClickHouse provide live correlation by task/trace; the smoke test also checks
span IDs and policy outcomes. Logs go to private files or stderr, never MCP's
stdout channel. No Tempo collector is installed by this component.

Both ClickHouse and Loki delivery are required for a successful tool result.
If either write fails, the response is `AUDIT_DELIVERY_FAILED` and omits the
backend data. A partial delivery can leave one store with the normalized record;
this is explicitly reported as audit failure and is not counted as a successful
MCP call. Fix the unavailable local telemetry service and retry the read.

For a parent workflow, the server accepts these narrowly formatted environment
values when the MCP host starts it:

- `SHIROKUMA_GATEWAY_TASK_ID`: `workflow-` or `gateway-` followed by 32 lowercase
  hexadecimal characters.
- `SHIROKUMA_GATEWAY_PARENT_TRACEPARENT`: valid sampled W3C `00-traceid-spanid-01`.

Otherwise each server session creates its own task ID and each call creates its
own trace. Correlation values are not tool arguments and do not authorize any
additional backend action.

The integration smoke lists eight tools, successfully calls every backend,
rejects arbitrary SQL, an alternate namespace and an inactive GitHub tool, and
verifies all eleven calls in ClickHouse, Loki and exported OpenTelemetry spans.
It retains its result under ignored `tmp/local-experiment/gateway/smoke-*.json`.
Stopping the MCP host closes the stdio server; the temporary Trino port-forward
is always stopped after its call. Read-only backend calls and audit rows require
no data-plane rollback. Persistent telemetry stores retain their own export and
lifecycle policies.

Primary references:

- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [MCP stdio client transport](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/client/transports.md)
- [OpenTelemetry Python traces](https://opentelemetry.io/docs/languages/python/instrumentation/)
