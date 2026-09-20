# Natural-language preview with local Qwen

Translate a short request into a bounded current-head table preview, then
materialize the two registered Notebook assets. The table is an explicit input;
the model selects column names and a row limit. It does not generate SQL,
Notebook code or executable commands.

From this checkout, with the dedicated DataOps environment and local services
already available:

```sh
rtk make local-notebook-assist <<'JSON'
{"table":"smoke.fixture_v1","request":"nameとidを1件表示"}
JSON
```

The target uses `tools/local_experiment/dataops/.venv` directly and installs or
synchronizes nothing. Each invocation builds the existing Go Reader CLI and
allocates a new private run. No approval step is inserted between an accepted
selection and materialization. There is no resume or automatic retry path.

## Input and model boundary

Stdin must contain exactly one JSON object with `table` and `request`. The whole
input is at most 4096 bytes. The request is nonblank text of at most 512
characters and 2048 UTF-8 bytes; control, format, surrogate and line/paragraph
separator characters are rejected. The table follows the existing Reader's
qualified `schema.table` contract. Unknown fields and malformed JSON are refused
before allocation, build, model or Reader execution.

For every valid input that reaches observation, the existing Reader first
performs `describe` and confirms its cleanup. The model receives only the
explicit table, request and bounded columns with `name` and `type`. It receives
no rows, table UUID, snapshot ID, schema hash, catalog description or credentials.
The existing configured local Qwen endpoint is used for at most one inference
attempt, without cloud fallback or retry. The request body is capped at 8 KiB;
the provider retains the configured timeout and output-token bounds.

The model is instructed to support only explicitly named columns in the stated
order and an explicit integer limit from 1 through 100. Its exact proposal is
`columns`, `limit` and `unsupported`. A supported proposal selects 1–16 unique
known columns. Unsupported is represented by `unsupported: true` with null
columns and limit. Missing or ambiguous columns/limits, unknown columns,
filters, counts, aggregations, joins, sorting, transformations, SQL, writes,
historical snapshots and arbitrary Notebook/code generation are outside the
supported request.

Host validation checks the proposal shape, types, known identifiers and bounds
before constructing an exact Reader v1 selection. These checks do not prove that
the model interpreted the natural language correctly. A structurally valid
selection is still a model interpretation; observed evaluation cases establish
only their stated scope.

## Describe, select and materialize

An accepted proposal flows through the existing
[selection contract](../notebook/README.md) and
[registered assets runner](../notebook_assets/README.md). It materializes
`table_preview` followed by `saved_evaluation_summary`, using the registered
`examples/local_table_preview.ipynb` template and trusted host definitions.
The runner does not import generated Python or execute Notebook cells. It starts
no kernel, Hub, scheduler or resident service.

The select independently observes the current head. Describe and select occur
at different times; a snapshot-head update between them is allowed. Final
acceptance requires the select's table UUID and schema hash to match describe,
as well as the selected table, column order and limit. This is not a transaction
or a promise to read the snapshot observed by describe. Row order is unspecified.

The second asset reads the existing MLflow evaluation experiment. Its dependency
means execution order, not that saved evaluations were derived from previewed
rows. The workflow performs no data/catalog/MLflow writes, training, evaluation
reruns or model serving. Audit delivery writes are described below.

An explicit unsupported response still has a completed describe and model
attempt. It performs no select, asset materialization or evaluation read.
A missing or unverifiable result is never converted into an empty successful
preview, zero count or unsupported response.

## Results and retained evidence

The runner's final stdout JSON contains `result`, `error_code`, `model_calls`,
`reader_cleanup_confirmed` and `evidence`. The evidence field names the saved
`owner-result.json`, or is null when that file was not saved. The Make wrapper
can print its own nonzero-exit diagnostic separately. The Python owner exits:

| Result | Owner exit | Meaning |
| --- | --- | --- |
| `passed` | 0 | Accepted selection, assets, identities, cleanup and audit verified |
| `unsupported` | 2 | Model explicitly declined; describe and audit completed, no select/assets |
| `failed` or `unknown` | 1 | No successful completion claim; inspect private evidence |
| Interrupted | Signal-derived nonzero | Original cancellation retained; cleanup can remain unknown |

Evidence stays under `tmp/local-experiment/notebook-assist/<token>/`. Private
`input.json` retains the supplied request; `model-context.json` retains the
bounded schema input, and `model-response.json` retains the parsed proposal and
measured usage. These are private experiment files, not committed documents or
public audit payloads. Source/input/binary hashes and an exclusive execution
intent bind the run and prevent same-directory replay.

`describe/` holds the metadata Reader receipt. An accepted plan also creates
`assets/` with its canonical request, registered export, private Reader stdout,
evaluation projection and dedicated Dagster store. Rows remain in private
Reader output. `summary.json` tracks workflow progress and its result;
`owner-result.json` separately records the terminal owner result and
`summary_result`. A later save failure or cancellation does not rewrite an
earlier business result into owner success.

Directories are 0700, evidence files are 0600 and the local Go binary is 0700.
Keep these files and the existing data, MLflow runs/artifacts and backups. To
stop using this feature, cease new invocations and confirm any owned Reader has
exited; retained data deletion is not part of recovery.

## Audit and cancellation

Accepted execution emits four Pawprints: describe, model, materialize and root
assessment. Normal unsupported emits three, omitting materialize. Each event
uses the same task and trace, with a distinct span ID. Local OpenTelemetry spans
are written privately, and AgentHouse insertion/readback plus Loki
push/readback must agree before the audit is verified. The service and agent
identity is `notebook-assist-local`; no global tracer provider is installed.

Only fixed actions/statuses, correlation IDs and bounded metrics enter these
records. Prompts, schema, rows, model response bodies and exception text do not.
Known usage is retained even if saving or validating a model response fails;
missing usage remains null and inference cost remains null. A normal unsupported
proposal is audited as blocked with successful local span completion. Malformed
proposal rejection, infrastructure failure and cancellation remain distinct
from that normal outcome. No failed audit is silently retried.

The dedicated DataOps lock adds `opentelemetry-sdk==1.44.0` and its required
`opentelemetry-semantic-conventions==0.65b0`; all prior 96 package versions remain
unchanged. MCP and LangGraph are not added to this environment.

The existing Reader's synchronous ownership, 240-second execution and
245-second cleanup bounds are retained. Allow at least 260 seconds after an
external interruption for Reader cleanup. An unconfirmed Reader remains
unknown; the owner does not force-kill it or replay a model/read to make the run
appear successful. The earliest cancellation is preserved through audit and
cleanup failures.

General natural-language analysis, arbitrary Notebook conversion, training,
multi-user operation and production acceptance remain outside this profile.

## 2026-09-20 local verification

Five real requests ran once each, in sequence, against the configured local
`qwen3.8-flash-next` endpoint. The explicit `name,id` preview with limit 1 passed
in 35.124 seconds and materialized both assets. Filter (16.129 s), unknown-column
(15.531 s), ambiguous (14.978 s) and SQL (15.648 s) requests returned normal
unsupported results with owner exit 2 and no select or asset materialization.
All five first described the table, made one model call, confirmed Reader cleanup
and made no retries or cloud calls. These observations cover these five requests,
not general natural-language accuracy or arbitrary Notebook conversion.

The accepted run, `1cdcb3db503849aa97d7df7df59a727b`, selected `name,id` with
limit 1 from the retained fixture. It returned a valid one-row subset, fetched
two rows and marked the result truncated; it did not promise row order. The
second asset read the same 12 saved evaluation runs, excluding four benchmark
runs. The five saved model responses total 1681 input tokens, 72 output tokens
and 6044 ms of measured inference duration. Every cost field remains null.
An invalid boolean request was rejected before allocation in 0.057 seconds,
with owner exit 1 and null evidence.

All 80 new tests passed: contract/provider 18, runtime 28, audit 18, describe
client 8 and strict provider JSON parsing 8. The full local regression passed
73 suites and 2583 tests (2582 passed, one existing optional skip) in 60.169
seconds. Existing shared suites are not counted again in the new-test total.
The before/after guard retained 65 source hashes, 527 earlier files, all 59
MLflow table row multisets, 16 runs, 1320 metric points and 16 artifacts.
All 519 previous AgentHouse rows kept their hashes; exactly 16 correlated audit
events were added, for a final total of 535.

Independent verification passed 670 checks (143 semantic checks plus 527
retained-file checks) across 693 saved inputs on its first run. It matched the
16 audit records and local spans, two materializations, twelve evaluations, all
six exited Reader owners and 25 distinct terminal query identities. It made no
application API, SDK, model or new Reader calls. Model request bodies were
reconstructed from sealed source/configuration and saved context, not captured
HTTP bytes; saved audit delivery and cleanup were not replayed against servers.

Evidence is under
`tmp/local-experiment/notebook-assist-checks/b0398f85421f4100a8737a6d05c12ff5/`.
The after guard SHA-256 is
`75d670ea4da08ce4842a58df625b38b2365e99260c8f2db949564eb8a5340d8b`.
The independent proof SHA-256 is `d271c5bfa6e0a6aed67180946d03b11a7b9bd020040d3a2057c0891639187800`.
The five run directories contain 77 files totaling 51,409,880 bytes and allocate
51,728,384 bytes, including their local Go binaries. They have 0700 directories,
0600 evidence files and 0700 binaries. No fixture, kernel or resident service
was created, and retained data and backups were preserved.
