# Local Iceberg → Ridge → MLflow training

This fixed local experiment reads a disposable Iceberg table through the existing
Go Reader, trains one Ridge model from the returned rows, and records the model's
metrics and safe JSON artifact in the owned MLflow store. It uses the existing
Trino, Polaris and MLflow environments; it adds no dependency or service.

```sh
rtk proxy make local-lake-training
rtk proxy make test-local-lake-training
```

Run these commands from the active checkout with the existing locked MLflow
Python environment available. The training target accepts no arguments, stdin
request, caller SQL, arbitrary table, model or artifact path. Each invocation
allocates a new private source token and performs at most one training fit.
The test target uses fake runtime/Tracking boundaries and fixed workload tests;
it does not run the live pipeline.

## Data and operation

1. Build one owned Go Reader binary. Create the uniquely owned temporary table
   `iceberg.local_experiment.training_<source_token>` with 64 fixed regression
   rows: `row_id`, three double features `x1`, `x2`, `x3`, and a double `target`.
2. Close the preparation tunnels, then use the existing SELECT contract to read
   those five columns with limit 100. Require the expected UUID, snapshot and
   schema, all 64 rows, no truncation, and confirmed Reader cleanup.
3. Remove only the same owned fixture identity, drain its queries and confirm
   that the retained fixture and table inventory are unchanged. Save the typed
   Reader observation and its evidence token, then seal the source files and
   their hashes. A failed or uncertain fixture cleanup never reaches training.
4. Check the owned MLflow store. If its server is running, stop it with the
   existing ownership checks. While stopped, retain and verify a complete cold
   export before any new Tracking write.
5. Run one fresh MLflow worker under the existing store lock. It rechecks the
   sealed source and code, fits the fixed workload once and verifies the saved
   experiment, run, parameters, metric histories and artifact bytes.
6. Recheck the source, Tracking receipt and artifact in the parent. Confirm that
   the worker process group has exited, then restore the original server state:
   running becomes a healthy owned server again; stopped remains stopped.

Learning uses only the Reader's supplied rows. It sorts the numeric data by
`row_id` and uses split seed 1730 for 48 training and 16 holdout rows. This sort
does not assert a SQL row order; the saved observation and rows hash retain the
Reader's original order. Ridge uses alpha 1 and the SVD solver, fitting only the
training partition. Train, holdout and training-mean holdout baseline each have
MAE, RMSE and R², for nine metric points per successful run.

The separate experiment is `shirokuma-local-iceberg-training-v1`. Parameters bind
the table identity, snapshot, schema and rows hashes, Reader evidence token,
source token, dataset/split hashes, settings and library versions. The sole
artifact, `safe-projection.json`, contains the typed source, explicit split row
IDs, fitted coefficients, predictions and metrics. It contains no executable
model serialization. Existing evaluation, benchmark and fixed synthetic
training records remain separate.

## Results and retained evidence

The command prints an owner result with `result`, `cleanup_confirmed` and the
private `owner-result.json` path. Success requires both verified business output
and closure of the Reader, fixture, tracker and runtime lifecycle. Inspect
`summary.json` for the individual phases and cleanup results.

The source attempt is retained under
`tmp/local-experiment/lake-training/<source_token>/`. It includes the owned
binary, pipeline intent, fixture report, Reader evidence, `lake-input.json`,
`source-ready.json`, cold backup, tracker process receipts and final summaries.
The distinct training attempt is retained under
`tmp/local-experiment/mlflow-tracking/lake-training/<attempt_token>/`, and its
artifact under the owned store's `artifacts/iceberg-training/<run_id>/artifacts/`.
Source tokens, training attempt tokens and MLflow run IDs are different values.
Evidence files use mode 600 and private directories use mode 700; the owned
executable uses mode 700.

An exclusive `tracking-intent.json` is written before launching the worker, and
the worker creates its own exclusive claim. The same source token cannot be
blindly retried. Tracking records intent before each SDK write. A lost reply or
partial batch is retained as a failed or uncertain attempt without a retry or
an extra FAILED/KILLED write; an incomplete MLflow run can remain RUNNING.
Cancellation preserves the first interruption and closes only owned processes.
Unverified cleanup stays unknown, and an unclosed tracker prevents server
restart. Keep the receipts, database, artifacts and backup for diagnosis.

## Scope and backup restoration

This is a fixed 64-row synthetic Iceberg fixture, not arbitrary lake data,
general model quality, Notebook/Hub training, autolog, Registry or Serving.
It uses no LLM, cloud API or Notebook kernel. The
[existing MLflow training and backup runbook](../mlflow/README.md) remains the
reference for the owned Tracking store.

The complete cold export preserves all source files, including newer training
experiments. The inactive-clone restore profile supports experiments 0/1/2 and
optional completed fixed synthetic and Iceberg training experiments 3/4. It
requires valid FINISHED training receipts, matching workload
and safe artifact bytes. Historical source hashes remain evidence; restoration
does not rerun this pipeline or require its current code to match those hashes.
Unreferenced failed journals are inert evidence, while referenced invalid
receipts or partial runs are rejected. A subsequent complete export containing
both training experiments was restored and read through the SDK on 2026-09-20:
five experiments, 18 runs, 1,338 metric points and 18 artifacts. See the
[restore runbook](../mlflow/README.md#restore-an-inactive-local-clone) for the
accepted manifest, tokens and completed independent verification.
This pipeline neither activates a clone nor rolls back by deleting retained
runs, artifacts or backups. The dated acceptance below preserves the limitation
that applied before this new restore increment.

## Local acceptance — 2026-09-20

The first source attempt, `1bbf9a7b0c044e42b18d84a8c92ef3fb`, ended unknown
in 49.710 seconds. The ownership-marker check expected literal `varchar`, but
SHOW returned `varchar(438)`. It stopped before the Reader callback, MLflow stop,
fit or write. Query drain and tunnel closure were confirmed. One additional
SHOW read in 3.940 seconds identified the mismatch. After the fix, a separate
25.958-second recovery matched the saved UUID and ownership marker before
removing that fixture; six retained projections and the ten-table inventory
were restored. The original failed evidence remains unchanged.

A fresh source attempt, `05f5892e679e49df8d964dc86e574ecd`, passed in
82.149 seconds with all cleanup checks true. Reader evidence
`971a99232c9768a950841d686f6b0ed9` supplied 64 rows for the 48/16 split.
Training attempt `3da0a631a0e549f29aac96f9998da5bb` produced FINISHED
experiment 4, run `ae5e1ff478ae4a3493c437009be2d6b6`.

| Partition | MAE | RMSE | R² |
| --- | ---: | ---: | ---: |
| Train | 0.120649 | 0.142460 | 0.998290 |
| Holdout | 0.139141 | 0.163043 | 0.997324 |
| Training-mean holdout baseline | 3.372920 | 3.885825 | -0.520294 |

Values are rounded from the saved workload. The new run has nine metric points,
24 parameters, six tags and one artifact. Retention's 13 checks preserved all
old rows across 59 tables, the old 17 runs, 669 files and 535 AgentHouse rows.
The store now has 18 runs, 1,338 metric points and 18 artifacts. Its complete
pre-write cold backup retained 39 files / 2,594,008 bytes. The same store was
restored to a healthy server, PID 19331; two API GETs in 0.129 seconds matched
its experiment/run identity, parameters and nine latest metrics.

The 100 new tests passed in 3.499 seconds. The full local regression passed
76 suites / 2,771 tests: 2,770 passed and one existing optional test skipped,
in 66.464 seconds. Independent arithmetic verification passed 432 comparisons
across 15 saved inputs in 0.041 seconds: the Reader's 64 × 4 values, 48/16 split,
coefficient replay, Ridge normal equations and nine metrics matched the saved
API bodies, SQLite records and artifact. The split check covered all 64 IDs
without overlap; independent regeneration of the PRNG split was outside scope.

Independent lifecycle verification passed 43 meaningful checks across 801 inputs
in 0.333 seconds, covering retained data, the exact cold backup, 48 recorded
terminal query IDs, closure of the Reader/tracker/old server and the new server's
identity. These checks added no API call, SDK operation, fit, Reader or product
import. The successful source (including cold backup), Reader, training attempt
and artifact directories total 62 files / 12,899,886 bytes (13,017,088
allocated). The failed source and its separate recovery total 7 files /
10,198,912 bytes (10,207,232 allocated); they are counted separately.
Data files are mode 600, directories mode 700 and
only the owned Go executable is mode 700. Detailed receipts and hashes are
retained under
`tmp/local-experiment/lake-training-checks/f864d8a881734779a7d0da2997b4ffa6/`.
These results establish this fixed local path; they do not establish general
model quality or restore acceptance for the new training experiments.
