# Registered Notebook to Dagster assets

This local profile converts the registered, output-free
[lakehouse Notebook](../../../examples/local_lakehouse.ipynb) into two host-defined
Dagster assets. It accepts no Notebook path, arbitrary Python, SQL or prompt.
General Notebook conversion and an LLM-based conversion agent remain outside
this increment. Two explicit executions with the real Reader and MLflow passed.

From the repository root:

```sh
rtk make test-local-notebook-assets
rtk make local-notebook-assets-run
```

Both targets first synchronize the locked existing DataOps environment. The run
target uses `tools/local_experiment/dataops/.venv` and
Dagster 1.13.22; no dependency is added. The argument-free `run.py` creates a fresh
private directory, exports the fixed definitions, builds the local Go CLI and
materializes the two assets in process. Each run has its own local Dagster
instance with telemetry disabled. It starts no kernel, Hub, daemon or scheduler.
The existing local Reader services and MLflow server must already be available.

| Asset | Read and recorded metadata |
|---|---|
| `fixture_preview` | Reuses `read_fixture()` for one bounded Reader invocation against `smoke.fixture_v1`, columns `id,name`, limit 10. Records row count/hash, snapshot ID and relative artifact reference. |
| `saved_evaluation_summary` | Reuses `read_evaluations()` for the fixed MLflow evaluation experiment. Records run count, projection hash and relative artifact reference; cost stays null. |

The second asset requires the first asset's successful receipt to preserve
Notebook execution order. This dependency does **not** claim that the saved
evaluations were derived from the fixture rows. Evaluation suite/provider/case
boundaries and missing usage remain as reported by the existing client; this run
does not perform inference, evaluation, training, data mutation or MLflow writes.

## Select a table for the registered preview

A second profile uses the registered, output-free
[table preview Notebook](../../../examples/local_table_preview.ipynb). Supply an
exact Reader v1 select request on stdin:

```sh
rtk make local-notebook-assets-preview <<'JSON'
{"command":"select","table":"smoke.fixture_v1","columns":["name","id"],"limit":1}
JSON
```

The request has exactly `command`, `table`, `columns` and `limit` fields and must
fit within 4096 bytes. `command` is `select`, the table and ordered column list
follow the existing Reader contract, and the integer limit is 1 through 100.
Only the current snapshot is read. Snapshot selection, filters, arbitrary SQL,
Notebook paths and Python source are not request inputs. Invalid input is
rejected before a run directory, Go build or Reader invocation is started.

This target directly uses the existing DataOps Python environment; it does not
synchronize or install dependencies. It invokes `run.py --request-stdin`. The
argument-free command and `local-notebook-assets-run` keep the original fixed
profile and asset names.

The selected profile materializes `table_preview` followed by
`saved_evaluation_summary`. The first asset calls the existing `read_select()`
client once and verifies the requested table, column order and limit. The second
performs the same saved evaluation read as the fixed profile. This dependency
records execution order; it does not assert that evaluations came from the
selected rows. Each asset has retry count zero, with the same Reader ownership
and cleanup rules below.

The canonical request is stored in private `request.json`. Its SHA-256 binds the
registered template, export, asset metadata and terminal records. The manifest,
execution intent, business result and owner result use schema version 2 and
profile `selected-table-notebook-assets-v2`. The original profile keeps schema
version 1 and its generated definitions unchanged.

Generated `defs.py` calls a fixed host helper with `__file__` and the expected
request SHA-256. Table names, columns and other request values are never embedded
in generated Python. The helper validates the adjacent private request and
export before constructing definitions. The owned runner builds its definitions
from trusted host code and checks the export before and after materialization;
it does not dynamically import the generated file or execute Notebook cells.
A modified request or export cannot become a successful owner result. The
existing distinction between materialization success and later owner
interruption remains in effect.

## Preview with one typed equality

The registered [filtered preview Notebook](../../../examples/local_filtered_table_preview.ipynb)
adds a third profile. Pass an exact six-field current-snapshot request:

```sh
rtk proxy make local-notebook-assets-filtered <<'JSON'
{"command":"select","table":"smoke.fixture_v1","columns":["name","id"],"limit":1,"snapshot_id":null,"where":{"column":"id","literal":"1"}}
JSON
```

This target invokes `run.py --filtered-request-stdin` in the existing DataOps
environment without dependency synchronization. The private canonical request
uses the same 4096-byte limit, `snapshot_id: null` and exactly one
`where:{column,literal}`. The [typed equality contract](../query_filter/README.md)
checks integer/bigint, decimal, date or boolean against the observed schema.
Varchar/float/NULL, ranges, multiple conditions and caller-selected historical
snapshots are outside this profile. The filter column may be unprojected.

The fixed assets are `filtered_table_preview` → `saved_evaluation_summary`;
their dependency still means execution order, with no evaluation lineage claim.
Manifest, intent, result and owner use schema 3 and
`filtered-table-notebook-assets-v3`. Request SHA/template/export checks apply
before and after execution. Fixed helper calls contain no request values;
generated definitions and Notebook code are not executed. Original schema 1
and selected schema 2 profiles retain their contracts.

For natural-language input, [filtered Assist](../notebook_assist/README.md)
describes the table and validates one local Qwen proposal before invoking this
profile. The literal remains in private requests, Reader arguments and Trino
SQL/history; public predicate metadata excludes it, although selected cells can
contain it. No dependency, kernel or resident service is added.

## Generated files and results

Evidence stays under `tmp/local-experiment/notebook-assets/<token>/`. The run
directory is private (0700); generated definitions, manifest, JSON receipts and
Reader stdout are 0600. Explicitly selected rows stay in private Reader stdout,
not asset metadata or committed Notebook outputs. Existing fixture data,
MLflow runs/artifacts and earlier experiment evidence are retained.

`manifest.json` binds the exact registered Notebook hash, its three code cells,
the generated definitions, the reused client and the converter itself. `defs.py`
references the trusted host definitions. The runner checks the generated bytes
and manifest, then reconstructs those definitions from its own code; it never
dynamically imports or executes the generated file or Notebook source. Changed
exports are refused. An execution intent prevents reuse of the same run directory.

`result.json` records the materialization outcome and successful asset metadata.
`owner-result.json` records the owner's terminal outcome separately. If
cancellation arrives after successful materialization, the earlier business
result is retained, while the owner reports interruption rather than success.
The command's `materialization_result` field makes that distinction explicit.

## Failure and cancellation

Both assets have retry count zero. A failed second asset does not acquire a
successful second materialization. The client owns the Go Reader process;
SIGINT/SIGTERM unwinds through its synchronous call and bounded cleanup wait.
The Reader's existing 240-second execution and 245-second cleanup bounds are
reused. An external supervisor must allow at least 260 seconds after interruption
for this cleanup path rather than killing the owner early.

Cleanup requires a matching terminal Reader frame and saved client status;
an exit code alone does not prove it. Unconfirmed cleanup remains `unknown`,
retains evidence and is not automatically retried, resumed or deleted. Source
Notebook and retained services/data are not cleanup targets.

The original fixed-profile focused suite has **19 passing tests**, including two real Dagster engine
cases with fake clients: successful execution records two materializations;
failure in the second read records only the first and a failed run. These tests
make no real Reader, MLflow or model calls.

## 2026-09-20 filtered preview verification

The first accepted filtered Assist execution materialized both fixed assets,
returning the bigint `id=1` row in `name,id` order and the 12 saved evaluations.
The outer verifier's integer expectation was corrected using saved output;
the product/Reader was not rerun. Range and contract-override model requests
were unsupported before asset execution. New filtered asset tests14 passed,
including the explicitly registered Make target after full regression.
Independent saved-only review also passed, including two materializations read
from Dagster SQLite without SDK calls. Tokens, timings,
retention and aggregate test accounting are in the
[Assist verification record](../notebook_assist/README.md#2026-09-20-filtered-preview-verification).

## 2026-09-20 selected preview verification

Run `5b50cb221dcb4bcfb267c3fa205fa4a5` passed in 20.064 seconds using the request
shown above: `smoke.fixture_v1`, columns `name,id`, limit 1. It returned one row,
fetched two and marked the result truncated. The returned row was checked as a
valid subset; the query does not promise which row appears first. Both business
and owner results passed, with confirmed Reader cleanup. The second asset read
the same 12 saved evaluation runs and excluded the four benchmark runs.

The private Dagster instance recorded one successful run and exactly two
materializations, with no failed or retried steps. The five distinct Reader
query IDs are terminal in saved records, and the Go owner exited. This execution
created no fixture, kernel or model call and made no data or MLflow writes.
An invalid boolean limit was refused with `NOTEBOOK_REQUEST_INVALID`, exit 1,
in 0.046 seconds.

All 35 focused tests passed (16 new and 19 existing, including three real Dagster
engine cases with fake clients). The full local regression passed 70 suites and
2503 tests: 2502 passed and one existing optional skip, in 60.303 seconds.
Review corrections to `requested_limit` validation and interruption during
environment setup were included before acceptance. Go product code was unchanged;
prior Go race results are not counted as a new run for this increment.

The before/after guard retained 64 source hashes, 505 earlier files, all 59
MLflow table row multisets, 16 runs, 1320 metric points, 16 artifacts and the
519 AgentHouse rows. Independent verification passed 533 checks (28 semantic
checks plus 505 retained-file checks) across 590 saved inputs on its first run.
It checked read-only immutable Dagster SQLite state, request/export binding,
selected typed rows, evaluation projections, saved cleanup and process exit.
It made no application API, SDK or model calls and did not replay Reader queries.

Evidence is under
`tmp/local-experiment/notebook-assets-selection-checks/b9a9c4d7271d494fbb7c645c78dde93b/`.
The after guard SHA-256 is
`ca3592c2370cd074e1025892282a2d3f135d9b1c7fd4b3060a47dcbcf013a158`.
The independent proof SHA-256 is
`f791711346342a1c2d36475d6ec90e2c234e2724a88f63ec0250714265db5b05`.
The new run contains 20 files totaling 10,674,195 bytes and allocates 10,809,344
bytes. Directories are 0700, files are 0600 and the Go binary is 0700. Stop using
the profile by ceasing new invocations and confirming the owned Reader has
exited; retain its receipts and the existing data, MLflow store and backups.
General Notebook conversion, LLM conversion, training and production acceptance
remain outside these results.

## 2026-09-18 local verification

Runs `feac569a1b4941929506cb53c36a14c2` and
`e4a5a35f7dc64c089802f44e3b0b746b` passed in 17.176 and 16.977 seconds.
Each produced two asset materializations and confirmed Reader cleanup. Both
read the same two fixture rows and 12 saved evaluation runs as the Notebook;
snapshot ID, row hash, evaluation hash and asset metadata match across runs.
These are explicit repetitions with fresh private instances, not automatic
retries or a performance benchmark.

The full local regression passed 63 suites and 2316 tests (2315 passed, one
existing optional skip). The before/after guard retained all 69 selected source
hashes, 42 previous Notebook files, and MLflow's 59-table row multisets,
16 artifacts and indexes. The MLflow store remains at 16 runs and 1320 metric
points. Independent verification passed 42 checks across 144 saved inputs.
Read-only immutable SQLite inspection confirmed one successful run and two
matching materializations per instance, without failed or retried steps. The
ten Reader query identities are terminal, both Go owners have exited, and the
typed rows/evaluation projections match the earlier Notebook executions.

Evidence is under
`tmp/local-experiment/notebook-assets-checks/7f7dd020de634a0d930dbe6b756850eb/`.
The after guard SHA-256 is
`2dd33b76ec738bf5d1a1d5caf3618b0cb7467e78f2635cbd2a0de28e48e794a6`.
The independent proof SHA-256 is
`699a9392ee5dd9f54b554d4f5fd92bf1c0e473b9c0fb299705745b123010b8cf`.
The two run directories, including Go binaries and private Dagster stores,
allocate 21,364,736 bytes. There is no new environment or resident service.
To stop using the profile, stop invoking the command and confirm any owned
Reader has exited. Keep its receipts, the earlier Notebook runs, retained
fixture, MLflow store and backups; no rollback requires deleting them.

See the [Notebook client runbook](../notebook/README.md) and
[DataOps environment](../dataops/README.md) for the reused boundaries.

## Materialize saved training and autolog

The `notebook-autolog-assets-v1` profile maps the registered, output-free
[autolog Notebook](../../../examples/local_autolog_assets.ipynb) to two fixed
assets: `saved_training_source` verifies a completed Iceberg training token,
then `ridge_autolog` records or reuses its fixed sklearn autolog result. This
connects the recorded experiment to its source data; it creates no new lake
fixture and invokes no Reader or LLM.

For an unrecorded token, stop the owned MLflow store and retain a cold backup
using the [existing autolog procedure](../mlflow/README.md#verify-sklearn-autolog-on-saved-training-data).
Pass that backup's repository-relative manifest to the fixed entry point:

```sh
TRAINING_TOKEN=<completed-token> BACKUP_MANIFEST=tmp/local-experiment/<backup>/backup-manifest.json rtk proxy make local-notebook-assets-training
```

The first recording fits the saved 48 training rows once and adds one run,
eight parameters, five training metric points and `estimator.html`. Its
previously seen 16 holdout rows do not enter the fit or autolog metrics.
After the owned worker has exited and cleanup is confirmed, restore MLflow to
its previous running state. An uncertain worker remains recorded and prevents
another attempt; do not restart the store while its writer may still be active.

A completed token can be materialized again while MLflow is running, without
specifying a backup:

```sh
TRAINING_TOKEN=<completed-token> rtk proxy make local-notebook-assets-training
```

Both assets materialize again in a fresh private Dagster instance, while the
second asset reuses the verified autolog receipt. It creates no additional fit,
Tracking run, metric or artifact, and accesses no caller-supplied backup
manifest. Validation still reads the retained source, HTML and original backup
receipt. Pending or unknown autolog records are not automatically resubmitted
or recovered.

The fixed DataOps environment runs Dagster; a bounded owned process uses the
existing ML environment when recording is necessary. No dependency, resident
service or kernel is added. Attempts and generated definitions remain under
`tmp/local-experiment/notebook-training-assets/`. Each attempt binds the
canonical request, registered template, source and generated definitions;
Notebook code and generated exports are not dynamically executed. The business
materialization result and the owner's final outcome are recorded separately.
The original preview profiles retain their existing behavior.

Local acceptance on 2026-09-20 used training token
`3da0a631a0e549f29aac96f9998da5bb`. The first materialization completed in
6.360 seconds; a second completed in 1.018 seconds and reused MLflow run
`d0eece6e33414e3fa4b5b57b338eb116`, with no further fit or Tracking write.
Independent read-only inspection of both Dagster stores confirmed one successful
job and the two ordered asset events per attempt. Decimal arithmetic reproduced
all five training metrics without a further fit. The 850 retained immutable
files and all prior MLflow rows/artifacts were preserved; the existing 13 Lab
Notebooks, Serving process and 585 AgentHouse rows were unchanged.

The post-recording cold export restored to an inactive clone and read back
7 experiments, 24 runs, 1,393 metric points, 24 artifacts and the existing
Registry model/version. Evidence is under
`tmp/local-experiment/notebook-autolog-assets-checks/cb730a8e478b4c8bbb57bb4d177ee477/`.
The two Dagster attempts and their shared lock allocate 1,196,032 bytes;
including both cold backups, inactive restore, new autolog receipt and HTML,
the 280 new files allocate 12,980,224 bytes. These figures exclude other
verification evidence and ordinary mutable runtime files. Dagster's internal
files retain its default modes behind owner-only attempt and Dagster directories;
other new receipts, exports and restore files are owner-only.

To stop using this profile, stop invoking it and confirm the owned worker is
closed before restoring a previously running MLflow server. Keep the generated
receipts, prior runs and backups. Local acceptance does not establish model
quality, model promotion or production readiness.
