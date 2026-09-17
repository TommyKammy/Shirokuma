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
| `local_platform_status` | No arguments | Fixed lakehouse, Superset, metadata, AgentHouse and Loki readiness, including unknown observations |
| `k8s_read_pods` | Only `shirokuma-dev`; limit 1–50 | Names, phases, readiness/restart counts, bounded application-container states, and optional fixed Trino usage/log signals |
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

`local_platform_status` runs the fixed local status command with a minimal child
environment and validates all five component identities, the observation time,
and readiness consistency. Its result contains only `profile`, UTC `observed_at`,
aggregate `ready`, and `components`; each component has `ready` and
`status` (`ready`, `not-ready`, or `unknown`). A component is ready only when its
status is `ready`; unknown is never healthy. Component reasons, endpoint details,
and raw backend errors are omitted. A valid non-ready report remains a successful
read. Superset readiness requires both a running owned container and its fixed
loopback `/health` endpoint returning HTTP 200 with a bounded `OK` body.
The scope check and all platform status reads share one 75-second worker
deadline. On timeout, only that worker process group is terminated and reaped;
resident services are untouched. This leaves time for audit delivery within
the MCP client limit. Scope failures omit component data.

`k8s_read_pods` shares one 20-second deadline across local context resolution,
the fixed single ARM64/k3s Node guard, and the first `shirokuma-dev` Pod page
(limit 50). Each API response is capped at 2 MiB; continuation is retained as
incomplete coverage and never followed. An owned child has a further two-second
cleanup allowance. Optional fixed Trino usage and log acquisitions each add at
most 20 seconds, giving these acquisition paths a combined 62-second bound.
This does not guarantee that the whole MCP operation, including other tools,
audit delivery and CPU processing, completes within the client's 120 seconds.

Optional `log_signals` reads only the current `trino` container, without follow
or previous logs. It fixes `sinceTime` to the request's whole second minus 300
seconds and `limitBytes` to 32768, with no `tailLines`. At most two application
acquisition intents read the log fragment and the same Pod again; there is no
application retry. Internal kubectl/client-go retries are not counted as these
intents. Before/after Pod and container identities and times must match.

Only a closed 12-field projection and eight fragment fields leave the parser:
bounded byte/line counts and accepted prefix-shaped timestamp bounds. Raw text,
SQL, tokens, logger/thread names, body hashes and error bodies are neither
persisted nor sent to the model. WARN/ERROR header-shaped counts are not genuine
event counts or lower bounds: message continuations can forge headers and
timestamp prefixes. Only a fragment of the latest rotated file is covered.
Invalid, unsupported, cut or capped fragments and identity drift remain unknown;
an empty valid response remains observed zero. Neither proves healthy logs or
absence of errors. Four fixed limitations travel with every projection. No
new finding, cause, remediation or workload/configuration change is introduced.

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

The integration smoke lists nine tools, successfully calls every backend,
rejects arbitrary SQL, an alternate namespace and an inactive GitHub tool, and
verifies all twelve calls in ClickHouse, Loki and exported OpenTelemetry spans.
It retains the safe platform observation alongside the audit correlations.
It retains its result under ignored `tmp/local-experiment/gateway/smoke-*.json`.
Stopping the MCP host closes the stdio server; the temporary Trino port-forward
is always stopped after its call. Read-only backend calls and audit rows require
no data-plane rollback. Persistent telemetry stores retain their own export and
lifecycle policies.

Primary references:

- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [MCP stdio client transport](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/client/transports.md)
- [OpenTelemetry Python traces](https://opentelemetry.io/docs/languages/python/instrumentation/)
