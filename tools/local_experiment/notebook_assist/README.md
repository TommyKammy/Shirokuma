# Natural-language preview with local Qwen

Translate a short request into a bounded current-head table preview, then
materialize the two registered Notebook assets. The table is an explicit input;
the model selects column names, a row limit and, in the filtered variant, one
host-validated equality. It does not generate SQL,
Notebook code or executable commands.

From this checkout, with the dedicated DataOps environment and local services
already available:

```sh
rtk proxy make local-notebook-assist <<'JSON'
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

The original `local-notebook-assist` profile supports only explicitly named columns in the stated
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

An accepted original-profile proposal flows through the existing
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

## Preview with one typed equality

```sh
rtk proxy make local-notebook-assist-filtered <<'JSON'
{"table":"smoke.fixture_v1","request":"idが1の行からnameとidを1件表示"}
JSON
```

This target uses `run.py --filtered-request-stdin` in the same existing DataOps
environment. It keeps the exact input and schema-only model context above, with
one local Qwen call. The requested columns, numeric limit and one equality must
all be explicit. The exact proposal is `{columns,limit,where,unsupported}`;
`where` is `{column,literal}` and the literal is a string. Normal unsupported
has null columns, limit and where, with `unsupported: true`.

The host reuses the [typed equality contract](../query_filter/README.md) against
the described schema. Integer/bigint, decimal(p,s), calendar date and boolean
are supported; varchar, floating-point, NULL, ranges and multiple predicates
are refused. Literals are nonempty ASCII strings of at most 128 bytes, fit the
declared type without rounding, and use no whitespace, leading plus or exponent.
The equality column can be outside the selected columns. This scope validation
does not establish general natural-language correctness or override resistance.

Accepted proposals become exact six-field Reader requests:
`command,table,columns,limit,snapshot_id,where`, with `snapshot_id: null`.
The Reader resolves the current snapshot and verifies its typed predicate;
the final table UUID/schema must still match the earlier describe. It
materializes `filtered_table_preview` then `saved_evaluation_summary` using
[the fixed filtered template](../../../examples/local_filtered_table_preview.ipynb).
No generated code or Notebook cell is executed.

Filtered owner/summary records use schema 2 and
`natural-language-filtered-notebook-preview-v2` under
`tmp/local-experiment/notebook-assist-filtered/<token>/`. Assets use schema 3,
`filtered-table-notebook-assets-v3`. The original Assist and asset profiles keep
their existing contracts. Both variants use the audit actions below; private
profile/owner records distinguish the variant.

The literal is present in the natural-language request, private proposal/request,
Reader arguments and Trino SQL, so it can appear in process or query history.
Public predicate metadata omits the literal, but selected rows can contain it.
Audit payloads do not include prompts, schema, literals or row data. Unknown
results, save failures and cancellation retain evidence without automatic retry.

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

Original-profile evidence stays under `tmp/local-experiment/notebook-assist/<token>/`;
the filtered variant uses the separate directory above. Private
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

The original Assist increment added `opentelemetry-sdk==1.44.0` and its required
`opentelemetry-semantic-conventions==0.65b0`; all prior 96 package versions remain
unchanged at that increment. The filtered variant adds no dependency or resident
service. MCP and LangGraph are not added to this environment.

The existing Reader's synchronous ownership, 240-second execution and
245-second cleanup bounds are retained. Allow at least 260 seconds after an
external interruption for Reader cleanup. An unconfirmed Reader remains
unknown; the owner does not force-kill it or replay a model/read to make the run
appear successful. The earliest cancellation is preserved through audit and
cleanup failures.

General natural-language analysis, arbitrary Notebook conversion, training,
multi-user operation and production acceptance remain outside this profile.

## 2026-09-20 filtered preview verification

Three actual local Qwen cases each made one model call:

| Case / token | Time | Product result |
| --- | ---: | --- |
| bigint `id=1`, `name,id`, limit1 / `36916e8d87434f8e999eb465be1d9c96` | 36.311s* | First execution passed; one row `["shirokuma",1]`, two assets and 12 saved evaluations |
| Range / `7d3af76a18dc4de0a6e1ffe44388fa91` | 16.357s | First execution unsupported/exit2; no select/assets |
| Contract override / `3418f01ee10345a8a4c441c04610774e` | 15.188s | First execution unsupported/exit2; no select/assets |

*The accepted time includes the outer verifier failure: it incorrectly expected
integer instead of the observed bigint. The original failure/log is retained.
A saved-output correction passed in 0.034s without another model or Reader run.
All Reader cleanup completed. Summed measured usage is 1394 input/62 output
tokens and 4723ms inference duration; cost remains null. Ten audit events were
added while the prior 535 events, 822 protected files, 37 source inputs and
MLflow's 19 runs/1347 points/19 artifacts were retained.

The new 48 tests cover client14/assets14/Assist20. Full regression passed
82 suites/2930 test executions (2929 passed, one existing optional skip, 69.611s).
The missing Make registration for the new asset target was then added and its
14 tests passed separately in 0.952s: 2944 executions total, 2943 passed/one skip.
Product sources stayed unchanged; only that Make registration changed after
actual acceptance. Independent saved-only review passed 251 assertions over 72 inputs in 0.049s.
It checked 17 terminal queries, the typed row/SQL SHA and two materializations,
binding four Reader closures to saved owner/root PID probes. Retention relies
on the root guard; the reviewer did not re-read all 822 retained files.
Two verifier expectation errors (nullable column and relative guard key) were
corrected with the original helpers/logs retained. No extra product, API or
Reader execution was performed.

The seven nonoverlapping output directories contain 61 files/31,114,905 bytes
(31,358,976 allocated); verification files are separate. Evidence is private at
`tmp/local-experiment/notebook-assist-filtered-checks/e719c8b8661a42e591fa2e7cb3826615/`.
These three cases establish neither general language understanding nor general
resistance to contract-override requests.

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
