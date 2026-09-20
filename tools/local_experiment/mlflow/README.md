# Local MLflow training, evaluation and benchmark tracking

WP-L4-ML-001 tracks a fixed supervised training workload, saved Agent evaluation
and Spark/Trino benchmark receipts. This is a dedicated single-user local
Tracking service. Hub integration, autolog, registry, serving and production
work packages remain separate.

MLflow **3.16.1** and Python **3.12** run in their own pinned environment. The UI
and API use **http://127.0.0.1:18550**. No launchd or global host settings change.

```sh
rtk make local-mlflow-sync
rtk make local-mlflow-prepare
rtk make local-mlflow-plan
rtk make local-mlflow-import
rtk make local-mlflow-start
rtk make local-mlflow-status
# Before importing new receipts:
rtk make local-mlflow-stop
rtk make local-mlflow-import
rtk make local-mlflow-start
rtk make test-local-mlflow
```

Import requires the server to be stopped. Start, stop and import share an
exclusive lock. Unknown listeners and process identities are rejected; stopping
the service checks its saved ownership before signalling its process group.
Stopping preserves the SQLite database, artifacts and importer records.

## List saved training runs

```sh
rtk proxy make local-training-runs
```

The bounded inventory covers the two fixed saved-training profiles, synthetic
and Iceberg, with at most 128 candidates. Shared saved-review validation produces
verified metadata: kind, training token, run/experiment/store identity, workload
hash, finish time, test/baseline metrics and lake provenance. Rows and model
coefficients are omitted. Use the matching
[synthetic Notebook review](../notebook/README.md#review-saved-training) or
[Iceberg Notebook review](../notebook/README.md#review-saved-iceberg-training).
Iceberg tokens also feed [batch prediction](#predict-with-saved-iceberg-training).

Output uses profile `saved-training-runs-v1`. Runs are ordered by kind, then
finish time descending and token; this is not a model-quality ranking. A stable
unavailable candidate is classified as `partial` when required result/workload
files are missing, or `invalid` when it cannot be validated as a saved review.
Invalid includes unfinished/failed attempts and does not diagnose corruption.
A complete inventory returns `result: complete` and exit 0, with unavailable
entries and their counts explicit. An invalid root, unknown name, token
collision, cap or inventory change returns `result: incomplete`, empty arrays
and exit 1. In that case `counts.candidates` is null (unknown), while verified
and unavailable counts are zero, matching the empty arrays. Public output is
bounded to 256 KiB. The listing makes no API, SDK, fit or Reader call and writes
no output file.

### 2026-09-20 inventory acceptance

The first listing completed in 0.086 seconds: three candidates, all verified,
with two Iceberg runs, one synthetic run and no unavailable entries. The latest
Iceberg token `6df9b9e5a40b4035b7865f8435fdd291` was explicitly selected by
finish time and passed two rows to the existing offline predictor in
0.081 seconds. Independent Decimal arithmetic matched the predictions.
Seven sources and 31 retained files preserved bytes, modes and modification
times. Thirteen new tests passed in 0.356 seconds. Full regression passed
81 suites / 2,896 test executions: 2,895 passed and one existing optional test
skipped, in 68.703 seconds. The same 13 new cases ran in the standalone stdlib
target and again in the existing MLflow environment; both runs are included.

The saved list SHA-256 is
`b4309b93bfc7f452ecd82ef1f94f2b79a4bf82635d031476017b9e7c93375fbc`;
the prediction SHA-256 is
`30bf87dab77dccceea5dfda5f72aff18c2a2c09a6f95f2740ea05e14684aac40`.
Private input/output, selected identity/provenance and retention evidence are
under `tmp/local-experiment/mlflow-training-runs-checks/05e9f543b6df4632842f85ba4289682f/`.
The product path adds no HTTP, SDK, fit, Reader or service operation. This is
source-based evidence, not network capture. General model quality, automatic
model selection, Registry/Serving and production acceptance remain outside scope.

## Predict with saved Iceberg training

Select a completed **ML training attempt token** and pass a bounded JSON batch:

```sh
rtk proxy printf '%s\n' '{"schema_version":1,"rows":[{"x1":0,"x2":0,"x3":0}]}' | rtk proxy env SHIROKUMA_LAKE_TRAINING_TOKEN=3da0a631a0e549f29aac96f9998da5bb make local-lake-predict
```

The input limit is 32 KiB and 1–64 rows. The top-level keys are exactly
`schema_version` and `rows`; each row has exactly `x1`, `x2` and `x3`, all finite
numbers; booleans, duplicate keys and extra keys are rejected. The saved-review
validator checks the completed receipt, model and
provenance before computing predictions. The stdout result preserves input row
order and includes model identity, source provenance and canonical request/model
SHA-256 hashes. The result profile is `lake-batch-prediction-v1`. Invalid input,
source mismatch or numeric overflow fails the whole batch with a fixed error
code, null predictions and exit 1; it never returns partial predictions.

This command makes no HTTP, SDK or Reader call, fit, MLflow write or service
lifecycle operation. The predictor itself persists no input, result or journal;
private acceptance evidence is written by the verification harness. It does not
provide Registry integration, online Serving or a general model-quality claim.
### 2026-09-20 batch acceptance

The first real invocation passed in 0.075 seconds (0.123 seconds for the outer
harness), predicting 22 rows: six new inputs and all 16 saved holdout rows.
Independent Decimal arithmetic over the saved coefficients and inputs differed
by at most 8.88e-16; the 16 holdout outputs matched their saved predictions.
Six source hashes and 28 retained store/training/marker files preserved bytes,
modes and modification times. The 17 new tests (11 prediction and six saved
reader tests) passed in 0.263 seconds. Full local regression passed 79 suites /
2,837 tests: 2,836 passed and one existing optional test skipped, in 67.537 seconds.

The frozen product path includes no HTTP, SDK, Reader, fit, LLM, database query
or service lifecycle operation; this is source-based evidence, not captured
network telemetry. The product wrote no file. Its verification harness retained
private input/output and receipts under
`tmp/local-experiment/mlflow-predict-checks/1f0b58e727e743b2a8735feb959aa692/`.
Arithmetic agreement does not measure prediction accuracy on the six new rows.

## Train from the fixed Iceberg Reader source

`rtk proxy make local-lake-training` connects an owned 64-row Iceberg fixture through
the existing Reader to one Ridge fit and a separate Tracking experiment. See
[the Iceberg training runbook](../lake_training/README.md) for the automatic
stop, complete cold backup, write verification and restoration of the original
server state. Inactive-clone restoration also accepts completed fixed synthetic
and Iceberg training receipts; see the current restore scope and acceptance below.

## Fixed supervised training

```sh
rtk make local-mlflow-stop
# Retain a complete private cold export before the first new training write.
rtk make local-mlflow-train
rtk make local-mlflow-start
rtk make test-local-mlflow-training
```

The training target uses the existing locked MLflow Python environment and does
not synchronize dependencies. It requires the owned server to be stopped and
shares the runtime's exclusive store lock. It leaves the server stopped; restart
it explicitly after checking the result. No cloud service, LLM, data warehouse,
Notebook kernel, Registry or Serving operation is involved.

Each invocation generates the same small synthetic regression dataset: 128 rows,
three features, seed 1729, and an independent seed 1730 partition into 96 training
and 32 holdout rows. Ridge with alpha 1 and the SVD solver fits only training
rows. Train and holdout MAE, RMSE and R² are recorded separately; the holdout
also has a baseline using only the training-target mean. This checks an
executable learning path on a fixed synthetic task, not general model quality.

An attempt creates one run in `shirokuma-local-supervised-training-v1`, separate
from imported evaluation and benchmark runs. Its timestamps describe the actual
local training and tracking operation. Parameters include the data/split hashes,
package versions, feature order and model settings. Nine metric points are
logged once. `safe-projection.json` contains the generated numeric data, split,
coefficients, intercept, predictions and metrics; it contains no executable
pickle. Readback reconstructs predictions from the saved JSON coefficients and
checks the training normal equation, metrics, exact SDK histories and artifact
bytes before and after marking the run FINISHED.

Private attempt records live at `mlflow-tracking/training/<token>/` and artifacts
at `store/artifacts/supervised-training/<run-id>/artifacts/`. Each invocation is
a new attempt. A durable phase precedes experiment/run creation, metric logging
and termination. Failures and cancellation retain their phase and known run ID;
unknown write outcomes are neither retried nor silently repaired. A partial run
may remain RUNNING in Tracking even though the local worker has stopped; the
attempt's `result.json` is the workflow outcome. Inspect it before starting a new
attempt. Existing evaluation/benchmark records and indexes are untouched.

Stopping further attempts is sufficient to disable this feature. Preserve new
runs and evidence; reverting code does not delete them. Earlier cold backups do
not contain newly created training runs, so export the stopped store again when
these new records need to be included in a backup.

### 2026-09-20 acceptance

One actual invocation completed in **5.616 seconds**, with local result
`verified` and Tracking status `FINISHED`. Experiment **3** contains run
`0c2a0a34683545d3acd580642a9bb30d`; the attempt token is
`e65bbc1bd3dc4ca3843ca5c502a53ff8`. The 32-row holdout had MAE **0.145263**,
RMSE **0.181238** and R² **0.993176**; its training-mean baseline RMSE was
**2.203076**. These rounded values describe this fixed synthetic split only.

The previous 16 runs, 1320 metric points and 16 artifacts were preserved.
One training run, nine points and one artifact produced totals of
**17 runs / 1329 points / 17 artifacts**. A complete private cold export before
the write retained **36 files / 2,562,597 bytes**; earlier backups remain intact.
Retention checks passed, and the same store restarted healthy on loopback port 18550
in 6.881 seconds. Two API GETs checked the experiment and finished run, including
nine latest metrics, 17 parameters and actual start/end times. A running-server
preflight was rejected before allocation; the actual training attempt succeeded.

The existing NumPy **2.5.3** and scikit-learn **1.9.1** became direct pinned
requirements. All 92 package versions and distribution records were retained;
only the root project’s direct dependency declarations changed. The dedicated
locked environment retained 87 installed packages. All 47 new focused tests
and the full local regression passed: 74 suites, 2630 tests, with 2629 passing
and one existing optional skip in 62.366 seconds. Secret scanning passed.
Independent saved-evidence verification passed 39 checks over 85 inputs: the
coefficient replay, training normal equations, nine metrics, old SQLite rows
and artifacts, cold backup, saved API bodies and owned server identity matched.
It made no additional API, SDK or fit call and did not independently regenerate
the NumPy random dataset. The three new training files occupy 25,276 bytes
(28,672 allocated), with 0600 files and 0700 directories.
Evidence, retention snapshots and the cold export are under
`tmp/local-experiment/mlflow-training-checks/c28faf05befa48d49a3792a01128326f/`.
No LLM, warehouse, Spark, kernel or Agent audit operation was added.

## What is imported

The existing [receipt reporter](../evaluation/README.md) discovers every UUID
in the six fixed evaluation roots. Both the summary receipt and attempt log
are validated and hashed. Suite, model/provider and case signature remain
separate dimensions: historical 6/8/16-case SRE evaluations, Data baseline and
request cases, and mock versus real Qwen are not interchangeable populations.

One source evaluation maps to one MLflow run. Source hashes and a durable local
index bind its identity. Identical reimport is a no-op. Recovery reads back an
already identified run and fills missing fields; it does not blindly repeat
run creation or append metrics after an uncertain outcome. A source mutation,
duplicate identity or conflicting existing record is a visible error. Failed
and incomplete evidence cannot silently become a passed evaluation.

Safe numeric outcomes, fixed identifiers and a normalized JSON projection are
retained. Raw prompts/responses, SQL, free-text errors and credentials are not
copied. Unknown token totals and inference cost remain JSON `null`, with absent
numeric metrics and explicit coverage. Source start time, code revision and
fixture version remain unknown where old receipts did not record them. The
MLflow run timeline describes **import activity**, not historical inference
start/end time. Existing task/trace IDs are correlations, not newly emitted
traces. No new model calls, AgentHouse events or evaluations are generated.

Open **Model training → Runs** in the imported experiment (the current owned
experiment is `/#/experiments/1/runs?workflowType=machine_learning`). The GenAI
trace overview has no imported traces; its empty counters are not evaluation
metrics. Use the run table to filter by suite/provider/case signature
and compare known metrics within comparable groups. A successful Tracking
import establishes saved-receipt integrity, not general model quality, current
audit-service availability or production acceptance.

On 2026-09-18 the saved SRE `observation-signals-v1` evaluation was added as
one run with 16 inference samples and 110 metric points. The store contains
12 evaluation runs and four benchmark runs, with 1320 metric points overall.
The old 15 runs/artifacts and all existing SQLite rows were preserved; a repeat
import added no runs, metrics, parameters or artifacts. The server was restarted
on the same loopback address after retaining a complete private export.
The new run is `d35f31449d054832a536f045d269d60a`; its 35 latest API metrics
and full 110-point saved history were checked separately. No inference or audit
was repeated. Evidence is under
`tmp/local-experiment/mlflow-sre-signals-checks/f2c0f8f220a743409d6ffb1985e2e8c2/`.

## Saved Spark/Trino benchmarks

```sh
rtk make local-spark-benchmark-report
rtk make local-mlflow-benchmark-plan
# Retain a complete private state export while stopped, before adding sources.
rtk make local-mlflow-stop
rtk make local-mlflow-benchmark-import
rtk make local-mlflow-start
rtk make test-local-mlflow-benchmark
```

The report and plan read saved files only. Import creates the fixed experiment
`shirokuma-local-spark-benchmark-v1`, with four runs per source: Spark and Trino
each have `q1_sums` and `q6`. It does not rerun the benchmark or call an LLM.
The existing evaluation experiment and `import-index.json` retain their original
meaning. Benchmark identities use `benchmark-import-index.json`, journals use
`benchmark-imports/`, and safe artifacts use `store/artifacts/spark-benchmarks/`.

The reporter validates the terminal orchestration and native receipts, saved
Pod identity, source hashes, snapshot/file seal and exact result evidence.
Every discovered source contributes to inventory coverage. Invalid or running
sources block import; a valid failed or interrupted terminal source remains
failed, with missing observations explicitly counted. Import does not promote
partial evidence into a passed measurement. Changed sources and ambiguous
creates are visible errors. Reimport of identical completed sources is a no-op;
an identified partial write is read back before filling only missing fields.

`client_observed_ms` uses source sample indices 0–3. First-observed and repeat
median metrics stay separate; the first observation is not a cold-cache claim.
Trino `trino_server_*` counters retain their server boundary. The Trino client
clock includes response processing and intermediate durable journal writes;
Spark measures SQL submission through `collect`. Source preparation, shared
cache warming, Trino-first execution and different resources limit comparison.
See the [benchmark timing contract](../spark_benchmark/README.md#what-timing-means).

The fixed JSON artifact preserves integer nanoseconds and string snapshot IDs,
the shared Spark startup/native workflow context, observed versions/resources,
result hashes and coverage. Startup is source context, not four independent
measurements. Unknown Trino version/resources and absent server counters remain
unknown; expected versions are separate. MLflow metric timestamps describe
import activity. Saved Trino query start times remain historical context;
Spark per-sample timestamps remain unknown. Source logs,
SQL, Pod documents, credentials and unrestricted receipt fields are not copied.
Numeric metrics omit unknowns and counters above the exact double range.

## Runtime and storage boundaries

Active Tracking state belongs to `tmp/local-experiment/mlflow-tracking/`, including the
SQLite database, artifact root, owner marker, process identity and private
HOME/XDG/temp directories. New directories/files are private (0700/0600).
The child receives a small explicit environment, telemetry and job execution
are disabled, and its PATH excludes Docker and host coding assistants.
The server's UI telemetry configuration is also disabled (rollout 0). The
standard Settings switch initially reflects a separate browser preference;
its initial On label does not override that server gate. The verified browser
preference was additionally set to Off.

This isolation matters for MLflow 3.16.1: its server startup includes orphan
Docker sandbox cleanup when Docker is available, and Assistant session cleanup
uses the temporary directory. Dedicated HOME **and** TMPDIR/TMP/TEMP keep those
operations away from unrelated host state. Tracking is the only exercised
surface; Assistant, Jobs and Model Registry operations are outside this local
increment. The standard UI is not a multi-user authenticated service.

The dedicated Python environment initially occupied about 454 MiB and setup
cache/evidence about 532 MiB on the verified ARM64 host; these are measured
setup values, not permanent capacity limits. SQLite/artifacts add storage as
receipts are imported. Retained source receipts are authoritative and unchanged.
The MLflow store is derived data that can be rebuilt from retained sources.
The service uses one fixed state path and provides no automatic deletion/reset
or clone activation command. Keep a damaged store intact before
planning a fresh replacement; do not remove it to make an import pass.

For a consistent filesystem export, stop the owned server and importer, retain
the complete private state directory with its owner/index files and artifacts,
and preserve source receipts separately. A database-only copy omits artifacts
and importer identity. This workflow does not delete or overwrite backups.

## Restore an inactive local clone

Restore reads a completed private export and creates a new token directory. It
does not replace the active store, start a server or require the current server
to stop. Set the manifest path from the retained export; do not substitute a
copy of the running SQLite file. The supported profile includes the original
experiments 0/1/2 and optional experiment 3 (fixed supervised training) and 4
(fixed Iceberg training). The accepted export below includes all five.

```sh
rtk proxy env SHIROKUMA_MLFLOW_BACKUP_MANIFEST=tmp/local-experiment/mlflow-training-restore-checks/0a4ab297acad46ee9d68982b0a0fce61/backup-manifest.json make local-mlflow-restore
# Replace RESTORE_TOKEN with the token returned by the restore command.
rtk proxy env SHIROKUMA_MLFLOW_RESTORE_TOKEN=RESTORE_TOKEN make local-mlflow-restore-readback
```

The manifest above selects the retained export with both completed training
runs. Each referenced training run must be FINISHED and have a valid saved
training receipt, workload
and matching safe artifact. Historical source hashes remain evidence of the
original execution; restoration does not require those hashes to match current
HEAD or rerun the live source pipeline. Unreferenced failed journals are copied
as inert evidence. An invalid referenced receipt or partial run is rejected.

Under `tmp/local-experiment/mlflow-restores/TOKEN/`, `export/` retains the whole
export as inert evidence, including old runtime and importer records. The
separate `clone/store/` contains SQLite and artifacts. Only experiment and run
artifact URIs are rebased to that clone; all other schema and table rows must
match the export. `clone.json` explicitly records `activation_supported: false`.
Missing artifacts, external artifact URIs, unsupported schema and uncertain
state fail closed. Existing exports and failed restore directories remain intact.

Each readback gets a new private `readback/TOKEN/` with its own HOME/XDG/temp
directories and process working directory. The pinned Python 3.12/MLflow 3.16.1
SDK reads all experiment/run metadata, metric histories and artifacts from the
clone. Artifact download with an empty path resolves the local run root; each
expected filename and byte hash is then checked beneath it. No artifact copy to
an SDK temporary download directory is needed. After the worker exits and is
reaped, all SQLite logical rows and retained artifact/export bytes are compared.

The worker prewarms stdlib `platform.platform()` before installing its audit
hook; this may launch local CPU/binary metadata utilities. SDK network access,
subprocesses and writes outside the clone/readback directories are then denied
by the limited Python audit guard. Telemetry is disabled. This is not a general
OS sandbox or a guarantee that SDK initialization cannot write: incompatible
migration/default-state changes are rejected by prechecks and postcomparison.

### Completed-training restore acceptance — 2026-09-20

A new complete cold export retained 42 files / 2,636,371 bytes. The owned
stop/export/restart cycle passed in 7.907 seconds and restored the same store to
healthy PID 23897. Restore `71b8d03059f24b2ea0594624692027c3` passed on its
first execution in 0.164 seconds with activation disabled. SDK readback
`bfd76a06c9b548bc9903b3ff526e4a2a` passed first in 3.335 seconds, reading
five experiments, 18 runs, 1,338 metric points and 18 artifacts.

Checks before export and after export, restore and readback preserved all 59
active-store tables, 18 runs / 1,338 metric points / 18 artifacts, ten sources,
730 protected files and 535 AgentHouse rows. Restore/readback added no fit,
Reader, LLM, kernel or API call; the SDK read only the clone. Runtime health GETs
in the export stop/restart cycle are recorded separately.

The 20 new tests (12 restore and eight readback) plus 47 existing tests passed
in 3.269 seconds. Full local regression passed 76 suites / 2,791 tests: 2,790
passed and one existing optional test skipped, in 67.282 seconds. Independent
saved-evidence verification passed 23 meaningful checks across 888 inputs in
0.489 seconds. All 59 tables' typed rows and DDL matched except for 23 artifact
URI values (five experiments and 18 runs). Both training receipts, model and
provenance data, and the 42-file export were preserved. Rebuilding the SDK view
from all saved parameters, tags, metric histories and artifacts reproduced its
hash. The worker and old server were gone; the new server's start/argv matched.
The checks added no API, SDK, fit, Reader or product import. The audit helper's
first run needed a count-key correction; its earlier log is retained, with no
product or actual-restore change.

The whole restore directory holds 70 files / 4,188,383 bytes
(4,321,280 allocated), including its export, clone and readback directories.
The separate source cold backup holds 42 files / 2,636,371 bytes
(2,707,456 allocated). Do not add the restore's included subdirectories
again. All files are mode 600 and directories mode 700. Detailed receipts and
hashes are retained beside the manifest under
`tmp/local-experiment/mlflow-training-restore-checks/0a4ab297acad46ee9d68982b0a0fce61/`.
Clone activation, service cutover, cross-host migration, Registry/Serving and
production recovery remain separate. The following import-only acceptance is
historical and is unchanged.

On 2026-09-18, restore `389807a736a74219a05e4d85dadd937a` recovered the historical
export's **15 runs (11 evaluations + four benchmarks), 1210 metric points and
15 artifacts**. Two SDK readbacks passed in 3.287/3.251 seconds. Independent
clone checks covered all 59 tables and 15 artifacts, with exactly 18 artifact
URI changes. The active store retained its newer **16 runs, 1320 metric points
and 16 artifacts**; its tables/index, the original export and 80 Notebook/assets
evidence files were unchanged. Disposable missing-artifact/external-URI cases
were rejected before SDK execution. Early restore and SDK failures remain
recorded, including platform probing and the SDK artifact-copy behavior.
Evidence is under
`tmp/local-experiment/mlflow-restore-checks/e63090a6c3ad4e08a49161244f9a27ba/`.
Stopping use requires no activation rollback: retain the clone and its evidence.
Cross-host migration, service cutover, training, registry and production
recovery remain unverified.

## Primary references

- [Pinned MLflow release](https://pypi.org/project/mlflow/3.16.1/)
- [Tracking server](https://mlflow.org/docs/latest/self-hosting/architecture/tracking-server/)
- [SQLite/backend store](https://mlflow.org/docs/latest/self-hosting/architecture/backend-store/)
- [Host/CORS protections](https://mlflow.org/docs/latest/self-hosting/security/network/)
- [3.16.1 server startup](https://github.com/mlflow/mlflow/blob/v3.16.1/mlflow/server/fastapi_app.py#L189-L220)
- [Usage tracking controls](https://mlflow.org/docs/latest/community/usage-tracking/)
