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
copy of the running SQLite file.

```sh
rtk proxy env SHIROKUMA_MLFLOW_BACKUP_MANIFEST=tmp/local-experiment/mlflow-sre-signals-checks/f2c0f8f220a743409d6ffb1985e2e8c2/backup-manifest.json make local-mlflow-restore
rtk proxy env SHIROKUMA_MLFLOW_RESTORE_TOKEN=RESTORE_TOKEN make local-mlflow-restore-readback
```

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
