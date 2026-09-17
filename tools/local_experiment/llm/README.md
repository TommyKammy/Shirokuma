# Local LLM endpoint and LangGraph baseline

This is the executable local portion of `WP-L2-LLM-001`, following ADR-0010's
LangGraph/MCP boundary and ADR-0017's proposed provider-neutral endpoint design.
It connects a deterministic mock or the owner's existing MTPLX Qwen endpoint to
the real workflow and observability interfaces. It does not select a paid
provider, execute cloud inference, download weights, or establish general model
quality.

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

For this host's existing MTPLX service, use the checked-in
`providers.qwen-local.json` profile:

```sh
rtk make local-llm-qwen-smoke
```

This selects `qwen3.8-flash-next` at `http://127.0.0.1:8001/v1`, with a
60-second request timeout, 512 output tokens, temperature zero, and
`enable_thinking=false`. The last field is optional for other compatible
providers and is sent only when configured. The server's live OpenAPI schema
exposes this field. `/v1/models` reported a 32,768-token context window.
Requests are bounded to 8 KiB and contain fixed instructions plus at most 16
short model identifiers; this is a byte limit, not a tokenizer measurement.
The owner reports MTPLX 2.11.2, no MTP and approximately 115 GB weights. This
command neither installs nor changes that service, its startup settings or
weights. Cloud remains disabled. The default `local-llm-smoke` stays a mock.

The Qwen command first makes one VM-to-host plan request via
`host.lima.internal:8001`, then four inference calls in two graph runs. The
VM probe is a connectivity receipt separate from the eight graph audit events;
its reported usage and latency are retained as well. Each call runs once,
without retries or fallback to a different provider. The graph deadline allows
two configured request timeouts plus 90 seconds for Gateway/audit overhead.

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

1. A structured model response proposes only `dbt_list_models` with empty args.
2. The real official MCP stdio client calls the local Gateway's typed allowlist.
3. A second response summarizes successful, audited tool evidence.
4. The completed in-memory LangGraph checkpoint and AgentHouse records are read
   back and validated.

The summarizer receives only validated model names and a boolean indicating
whether a retained verified dbt run exists. Arbitrary tool payloads, filesystem
paths, run tokens and instructions are excluded. Its JSON must contain exactly
`result`, `model_count`, `models`, and `verified_run_available`; every fact is
compared with the delivered evidence. A past verified run does not imply the
generated tables still exist. This proves a narrow inventory task, policy
enforcement, state handling and telemetry correlation, not SRE diagnosis or
general reasoning quality.

An unexpected model tool, arguments, missing audit delivery, mismatched task/trace
identity, failed tool, or incomplete model response stops the graph. The model
cannot provide shell commands, SQL, endpoints, credentials or arbitrary tool
arguments. The mock does no inference and emits no invented usage counts. Token
counts and inference cost stay NULL for the mock; request and workflow latency
is measured. A real local provider's reported token counts and latency are
recorded and compared with AgentHouse readback. Valid reported usage survives
incomplete output, malformed JSON and rejected plans/summaries in failure audit
events. Cost remains NULL without a price model. No free-form prompt, model output
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

The temporary HTTP mock and Gateway subprocess exit after the smoke. No new
model weights or data tables are created. Pawprints are retained audit events
and must not be removed as ordinary experiment cleanup. The local runtime stays
within the existing Colima profile and does not change global host configuration.

Cloud acceptance remains a separate, unexecuted WP criterion requiring a
provider/model and explicit spending boundary. It is not needed to continue
local agent development with the verified existing Qwen endpoint. To return to
the deterministic baseline, run `local-llm-smoke`; the Qwen service is left as-is.

For a small repeatable fact-following evaluation, run:

```sh
rtk make local-llm-eval       # deterministic mock
rtk make local-llm-qwen-eval  # existing local Qwen endpoint
```

`evaluation.py` supplies five synthetic inventories twice each: empty/no run,
one model/no run, three renamed models/verified run, a false verification
record, and the 16-name boundary. These are ten independent summary requests,
without retries. The expected facts come from the fixtures and are checked by
the same strict validator used with live MCP evidence. A fabricated count,
name, flag or extra field fails that attempt and the overall result.
This evaluation makes no MCP call and does not measure tool selection, SRE
diagnosis, broad model quality, PR success rate or repair time.

AgentHouse must be ready. Each inference, including a failed one, is written as
`llm.eval_summary` and immediately read back to verify its identity and metrics.
An audit failure stops further inference. Task/trace fields here are explicit
evaluation correlation identifiers, not exported OTel spans. Private evidence
under `tmp/local-experiment/llm-evaluation/<uuid>/` contains per-case correctness,
usage and duration, including partial failures; raw prompts/responses are not
stored. Unknown cost stays NULL. A passing small fixture suite is evidence only
for this bounded contract.

Focused verification (the pinned environment runs the real LangGraph tests):

```sh
rtk proxy uv run --frozen --project tools/local_experiment/llm python -m unittest discover -s tests -p 'test_local_llm*.py' -v
```

Primary references:

- [LangGraph workflow documentation](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
- [LangGraph 1.2.11 on PyPI](https://pypi.org/project/langgraph/1.2.11/)
- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Lima host loopback networking](https://lima-vm.io/docs/config/network/user/)
- [llama.cpp compatible server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
