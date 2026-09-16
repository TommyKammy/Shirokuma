# Local LLM endpoint and LangGraph baseline

This is the executable local portion of `WP-L2-LLM-001`, following ADR-0010's
LangGraph/MCP boundary and ADR-0017's proposed provider-neutral endpoint design.
It proves the workflow and observability interfaces with a deterministic mock.
It does not select a paid provider, execute cloud inference, download model
weights, or establish model quality.

Dependencies are isolated in this directory and pinned in `pyproject.toml` and
`uv.lock`: Python 3.12, LangGraph 1.2.11, official MCP SDK 2.2.0 and OpenTelemetry
SDK 1.44.0. Initialize the local Gateway environment and AgentHouse/Loki first.
Then run from the repository root:

```sh
rtk proxy uv sync --frozen --project tools/local_experiment/llm
rtk proxy uv run --frozen --project tools/local_experiment/llm python tools/local_experiment/llm/workflow.py --validate-only
rtk proxy uv run --frozen --project tools/local_experiment/llm python tools/local_experiment/llm/workflow.py
```

`providers.example.json` defines the configuration; `config.schema.json` is the
machine-readable schema, with request-time validation in `providers.py`.
Unknown fields and inline credentials are rejected. The default active provider
is the deterministic mock. Local MLX/llama.cpp-compatible configuration has an
explicit endpoint and a pending model name; it remains disabled until configured.
Use `--config <private-config-path>` to choose an existing local model endpoint.
Local execution accepts only HTTP on loopback or `host.lima.internal`, with
`/v1`, no URL credentials, and no query/fragment. This baseline does not consume
API-key variables for local endpoints. Requests use neither proxies nor redirects.

The cloud profile is deliberately disabled and cannot be activated by editing
the enabled flag. Provider, model, HTTPS endpoint, credential environment-variable
name, pricing and spending authorization are still pending. Configuration
inspection enumerates variable names only; it never retrieves or prints provider
credential values. Cloud keys present in the host environment do not authorize
calls and are not inherited by the Gateway subprocess.

The default smoke starts an ephemeral compatible HTTP mock on `127.0.0.1` and
checks that the dedicated Colima VM can call it through `host.lima.internal`.
The host listener remains loopback-only. It then runs the real LangGraph state
machine twice:

1. A structured mock response proposes only `dbt_list_models` with empty args.
2. The real official MCP stdio client calls the local Gateway's typed allowlist.
3. A second mock response summarizes successful, audited tool evidence.
4. The completed in-memory LangGraph checkpoint and AgentHouse records are read
   back and validated.

The summarizer receives the Gateway's success status, not model inventory
contents. This smoke proves workflow connectivity, policy enforcement, state
handling, and telemetry correlation; it does not perform substantive agent
diagnosis or evaluate whether a real model understood tool evidence.

An unexpected model tool, arguments, missing audit delivery, mismatched task/trace
identity, failed tool, or incomplete model response stops the graph. The model
cannot provide shell commands, SQL, endpoints, credentials or arbitrary tool
arguments. The mock does no inference and emits no invented usage counts. Token
counts and inference cost stay NULL; measured request and workflow latency is
recorded. A real local provider's valid reported token counts can be recorded,
while cost remains NULL without a price model. No free-form prompt, model output
or tool payload is placed in Pawprint fields.

Each run uses a distinct `workflow-<uuid>` task ID and OTel trace. The Gateway
process inherits that parent trace and task ID, so model, Gateway, and completed
workflow events can be joined. Two successful graph runs produce eight correlated
AgentHouse events. Gateway delivery currently includes AgentHouse and Loki.
Workflow/model spans are exported to local JSONL, with external LangSmith tracing
disabled in this process. The in-memory checkpoint proves graph state handling;
it is not a durable cross-process resume store or a production tracing backend.

Evidence remains under ignored `tmp/local-experiment/llm/<uuid>/`:

- `summary.json`: VM-to-host probe, provider identity, both graph results,
  Gateway references, checkpoint status and retained Pawprint readback.
- `model-events.json`: bounded event identities, measured latency and nullable
  usage fields, including failures when they occur.
- `spans.jsonl`: local OTel span IDs, parent IDs, timing and status.
- `gateway-stderr.log`: bounded runtime diagnostics from the MCP subprocess.

The temporary HTTP mock and Gateway subprocess exit after the smoke. There are
no retained model weights or new data tables. Pawprints are retained audit events
and must not be removed as ordinary experiment cleanup. The local runtime stays
within the existing Colima profile and does not change global host configuration.

The remaining cloud acceptance needs an owner-selected provider/model and an
explicit spending boundary. A bounded first test can use two short requests
(each under 4 KiB of fixed input), at most 128 output tokens each, a 30-second
deadline, no retries, and a proposed total ceiling of USD 0.05 after price
preflight. This is a proposal, not authorization or an implemented cloud path.
The completed local mock test does not satisfy the WP's cloud-smoke criterion.

Focused verification (the pinned environment runs the real LangGraph tests):

```sh
rtk proxy uv run --frozen --project tools/local_experiment/llm python -m unittest discover -s tests -p 'test_local_llm.py' -v
```

Primary references:

- [LangGraph workflow documentation](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
- [LangGraph 1.2.11 on PyPI](https://pypi.org/project/langgraph/1.2.11/)
- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Lima host loopback networking](https://lima-vm.io/docs/config/network/user/)
- [llama.cpp compatible server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
