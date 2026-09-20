# Local training workbench

A single-user JupyterLab for exploring saved Shirokuma training runs. This uses
a dedicated environment and workspace. The existing fixed Notebook runner and
its environment remain available separately.

## Read catalog descriptions and lineage

Run `make local-notebook-lab-start` to seed `catalog_workbench.ipynb` once.
Its initial Run All makes no catalog request. Set `table = "smoke.fixture_v1"`
and `run_catalog = True` in the selection cell, then run the observation cell
to read its description, owner, columns and one-hop lineage from the existing
local OpenMetadata service.

The workbench uses the existing bounded Catalog Reader: one login and at most
12 metadata GETs, with two matching observation windows. It runs no SQL or LLM
request and writes no catalog metadata. `null` descriptions remain distinct
from empty text. Lineage may include historical generated-table records;
the workbench does not verify that the SQL tables still exist.

Set the returned Lab attempt token in the recovery cell to display the saved
result without logging in or reading the catalog again. Save and reload keeps
the selected inputs and displayed result. Failed or interrupted observations
clear their candidate output, and unconfirmed cleanup prevents another call.

The Python helpers are `query.catalog`, `query.recover_catalog` and
`query.render_catalog`; direct owned-client use is
`notebook.client.read_catalog(table)`. The underlying scope and limits are
documented in the [Catalog Reader runbook](../catalog_read/README.md).

```sh
rtk proxy make test-local-notebook-catalog-client test-local-notebook-lab-catalog
```

Local acceptance observed the retained fixture's description, owner, two
columns and eight lineage edges with one login and 12 GETs. The initial
Notebook made no request; saved recovery and browser reload preserved both
displays without another GET. All 827 protected files, 23 MLflow runs, 1,388
metric points, 23 artifacts, the registered model/version and 585 audit rows
were unchanged. The Lab retained its server process and other 11 Notebooks.
The new Notebook and private execution evidence added 18 files and 10,238,231
logical bytes, including the owned client binary. No service was added or
restarted, and SQL table presence remains unchecked.

## Diagnose a saved preview

Run `make local-notebook-lab-start` to seed `query_diagnose_workbench.ipynb`.
The initial Run All makes no diagnosis call. Enter a successful Lab preview's
`attempt_token` in `preview_attempt_token`, or a successful SELECT Reader token
in `read_token`, and set `run_diagnose = True`. Use exactly one token field.
The preview helper verifies saved completion and extracts the actual Reader
token; Lab, Assist and COUNT tokens are not interchangeable with it.

The existing Query Doctor reads the current schema, EXPLAIN, retained history
and statistics, verifies the saved source identity, then calls the configured
local Qwen model once. It does not execute the original SELECT again. Findings
and next steps remain bounded: root cause is undetermined, performance gain is
not measured, missing history is not a diagnosis, and estimates are not actual
resource usage. No SQL rewrite, model promotion or new service is performed.

Set the resulting Lab `attempt_token` in the final recovery cell to display
the saved result. Recovery validates historical completion, summary and span
seals without invoking SQL or the model. Save and reload retains the displayed
result. A failed or interrupted call clears its candidate output; uncertain
cleanup remains blocking. Diagnostic attempts share the existing Lab worker
ownership and are saved under `tmp/local-experiment/notebook-lab/queries/`.

Python entry points are `query.preview_read_token`, `query.diagnose`,
`query.recover_diagnose` and `query.render_diagnose` in
`tools.local_experiment.notebook_lab`. Direct client use is
`tools.local_experiment.notebook.client.read_diagnose(read_token)`.

```sh
rtk proxy make test-local-notebook-diagnose-client test-local-notebook-lab-diagnose
```

Local acceptance used a retained filtered preview and completed one diagnosis
through Qwen (`qwen3.8-flash-next`, 536 prompt / 114 completion tokens).
All five observations and the drain query finished, with the original SELECT
unreplayed. Its history was no longer retained; the result preserved that gap
and incomplete plan/statistics estimates. The initial Notebook ran four code
cells without a diagnostic call. Saved recovery displayed the result through
save and reload without another SQL or model call. The prior 540 protected
files, 38 source files, all MLflow records and 582 audit rows were preserved;
the diagnosis added exactly three audit rows. The Lab stayed on the same server
process and preserved its other ten Notebooks.

## Open the workbench

```sh
rtk proxy make local-notebook-lab-sync
rtk proxy make local-notebook-lab-start
rtk proxy make local-notebook-lab-status
```

Open [the local workbench](http://127.0.0.1:18560/lab) and enter the token from
`tmp/local-experiment/notebook-lab/token`. Normal status output omits the token.
Keep that file private. The server listens only on `127.0.0.1:18560` and refuses
to start if the port is occupied by an unowned process.

Open `training_workbench.ipynb` and run its cells in order:

1. List the saved training runs.
2. Copy a `training_token` into the selection cell and run it to show metrics,
   holdout rows and residuals.
3. For an Iceberg training run, edit the feature rows and rerun prediction.
4. Save the Notebook to retain the edited inputs and displayed results.

The supplied example uses the existing saved-run inventory, review and batch
prediction helpers. It performs no training, live Reader execution or MLflow
write. Synthetic runs can be reviewed; prediction currently requires an
Iceberg training run. A fresh workspace receives the registered example once.
Starting again does not replace the user's edited Notebook or its outputs.

```sh
rtk proxy make local-notebook-lab-stop
```

Stop allows owned kernels to shut down before stopping the server. Use one
operator at a time; do not create another kernel during shutdown. Uncertain
cleanup is reported and retained. Workspace files survive stop and restart.

The Python kernel runs with the host user's permissions. The workspace root is
the file browser's starting boundary, not a kernel sandbox. This is a local
single-user experiment, not JupyterHub, multi-user isolation or public hosting.
The fixed Iceberg training example uses an owned worker with the existing
pipeline's launch handshake and cleanup receipts.

Dependencies are pinned in `pyproject.toml` and `uv.lock`: JupyterLab 4.6.3,
ipykernel 7.3.0 and nbformat 5.11.1, using Python 3.12. Authentication and server
settings follow the [Jupyter Server configuration reference](https://jupyter-server.readthedocs.io/en/stable/other/full-config.html).
The environment uses a private kernelspec and project-local Jupyter/IPython
directories. No global kernelspec, login item or host-wide configuration is
installed.

## Predict with a registered model version

Run `make local-notebook-lab-start` to seed `registry_prediction.ipynb` once.
An already healthy Lab stays running, and existing Notebooks retain their
edited cells and saved outputs. The initial Run All leaves the version empty
and performs no database read or prediction.

1. Look up the version in the local MLflow **Model registry** page.
2. Set `model_version` to its explicit numeric string and run that selection cell.
3. Edit the `x1`, `x2`, `x3` input rows and run the prediction cell.
4. Save the Notebook to retain the selected version, inputs and output table.

The fixed name is `shirokuma-local-ridge`. Each prediction resolves that version
through a read-only SQLite snapshot, then calls the existing saved-model
predictor. The training token, run, store, workload and model hash must match.
The table shows the input rows in order and the model identity in an expandable
detail. Invalid input, an unavailable version or a mismatched identity clears
the prior result; interruption also clears intermediate results and propagates
the original cancellation.

The Python helpers are `notebook.registry.predict_registered(name, version,
request)` and `render_prediction(result)`. Rendering uses only the saved public
result. This path creates no fit, registration, Tracking write, Reader or LLM
call, and does not use an alias, stage or Serving endpoint.

Local acceptance on 2026-09-20 added the Notebook to the healthy running Lab
without a restart, preserving the nine existing Notebooks. Initial Run All
requested an explicit version and produced no prediction. Selecting version `1`
and editing the input rows to `(0, 0, 0)` and `(1, 2, 3)` produced
`4.016354700969291` and `4.425673825468169`, matching the direct helper result.
The model details, save and reload were verified in the UI. All 538 retained
files and the MLflow/Registry/AgentHouse snapshots stayed identical. The saved
Notebook and its checkpoint total 9,966 logical bytes; the Lab keeps the same
server PID with ten kernels. Full local regression passed 3,477 tests with one
existing skip. Receipts are under
`tmp/local-experiment/notebook-registry-checks/b27f070865ae43bab94d82b8307c2158/`.

## Start a fixed Iceberg training run

Run `make local-notebook-lab-start` to seed `lake_training_workbench.ipynb` once.
Open it and run the import and training cells. The training cell creates one new
MLflow run from the fixed 64-row Iceberg fixture, then displays its metrics and
holdout residuals. The next cell refreshes the saved-run inventory; the final
cell predicts two editable feature rows with this same new saved model.
Save the Notebook to retain the result and inputs.

With `recovery_attempt` empty, each execution of the training cell starts a fresh fixed pipeline. It accepts
no SQL, arbitrary training program or model choice and makes no LLM call. The
pipeline owns the temporary table, Reader, tracking write, backup and cleanup.
The Lab worker reuses its existing ML environment and the project-local Go
cache. Existing saved runs remain available.

Training, queries, comparisons, comparison recording and Lab lifecycle commands share one ownership
lock. Interrupt once and let the worker finish its cleanup. An incomplete or
unknown attempt prevents further execution and stop/start until its owned
cleanup is established. Inputs, launch/status records, pipeline output and the
validated review are retained under `tmp/local-experiment/notebook-lab/trainings/`.

If training was saved and only MLflow restoration failed, restore the owned
MLflow server and set `recovery_attempt` to that Lab attempt directory's token.
`recover_training(token)` checks its original evidence, exited workers and the
same healthy store, then returns the saved model review without running training.
It writes a separate immutable recovery receipt; the failed status and pipeline
records remain unchanged. Other incomplete cleanup still blocks recovery.
After recovery, an intentional MLflow stop or restart does not reopen the closed
attempt. Clear the token only when a new training run is intended.

Local verification trained from this Notebook, recovered a saved result after
one MLflow restoration failure, and then completed a separate new training run
with all cleanup confirmed. The initial failure's exact cause was not recorded;
later failures retain a fixed restoration phase and error code. Recovery did not
repeat training. The inventory showed five saved training runs, and predictions
for two edited inputs matched independent decimal arithmetic. The recovered
Notebook retained its bytes, permissions and modification time through a
five-kernel shutdown and server restart; the later normal result survived reload.
Earlier verification also rejected unauthenticated API requests.
Interrupted shutdown can resume from the saved ownership record; it does not
restart kernels or stop an unidentified process. Initial configuration and
shutdown failures are retained in the local experiment evidence.

## Compare two snapshot counts

`make local-notebook-lab-start` seeds `snapshot_workbench.ipynb` once. Select a
fixed local table and list its retained snapshots. The initial Run All only
lists snapshots; it does not count rows or call a model. Snapshot order follows
recorded timestamps, not the numeric order of snapshot IDs.

Set two different snapshot IDs from the displayed compatible snapshots and
enable the comparison flag to request two sequential COUNT reads. Each can
scan its full snapshot and retains the existing Reader timeout. Both reads
must finish and close cleanly, with the same table UUID and schema hash and the
requested snapshot IDs, before `right count - left count` is shown. Counts and
the difference remain exact decimal strings. A failed or unconfirmed side never
becomes zero or a difference. The result compares total row counts; it does not
identify added or deleted rows.

The Python entrypoints are `snapshots(table)` and
`compare_counts(table, left_id, right_id)`. Use `recover_snapshots(attempt_token)`
and `render_snapshots(result)` to display a saved observation without a new
Reader or model call. Each current head and the acquisition time describe the
saved reads; an explicit snapshot comparison does not require the current head
to stay fixed between its two reads. Historical results are checked against
their saved evidence, and remain readable after source changes or fixture
cleanup. The existing 64-snapshot limit remains in force; an over-limit response
fails instead of becoming a partial list.

Local acceptance on 2026-09-20 used a fresh disposable table containing an
initial empty snapshot, a two-row snapshot and a three-row snapshot. Initial
Run All listed them once with zero COUNT reads. The selected pair returned exact
counts `2` and `3`, with difference `1`; the two COUNT Readers ran sequentially
and closed cleanly. Saved recovery and Notebook save/reload added no reads.
After an owned describe, the fixture helper dropped only that new table and
verified the retained fixture's six observations were unchanged. Saved results
remained readable after cleanup. The prior 267 protected files, MLflow's
22 runs / 1,383 metric points / 22 artifacts, and 582 AgentHouse rows were
preserved. No model calls or audit events were added. Full local regression:
3,435 tests executed (3,434 passed, one skipped); `go test ./...` also passed.
Evidence: `tmp/local-experiment/notebook-snapshot-checks/fbcaa2d1d4044f4a9beaf85f6e1f5e4f/`.

## Browse tables and columns

Run `make local-notebook-lab-start` to add `table_workbench.ipynb` once. The
initial Run All lists both `smoke` and `local_experiment` in the fixed `iceberg`
catalog. Explicitly select one listed table to inspect its column names, types
and nullable flags. The existing Reader performs these operations without a model call.
An empty complete list, an incomplete list at the existing limit, and a failed
request are shown separately. A failed read never becomes an empty result.

The initial Run All only requests those two table lists. It does not execute a preview
or COUNT. After inspecting the schema, explicitly enable the existing preview
or count operation and provide its request. Those operations retain their
existing validation, ownership, model-call and cleanup behavior.

`tables(schema)` and `describe(table)` share the Lab query lock and owned worker
with the other reads. `recover_metadata(attempt_token)` displays a saved
observation without a new Reader or model call. The acquisition time describes
that saved observation; it does not prove the table still has the same schema.
Existing Notebooks and their saved outputs are preserved when the new template
is seeded.

Local acceptance on 2026-09-20 displayed `smoke.fixture_v1` and a complete empty
`local_experiment` list, then described `id bigint` and `name varchar` (both
non-nullable) at snapshot `7141845066324476177`. Saving and reloading the Notebook
kept the observed time and saved schema, with no additional reads. All three
metadata attempts closed cleanly and used zero model calls. The prior 218
protected files, MLflow's 22 runs / 1,383 metric points / 22 artifacts, and 582
AgentHouse rows stayed unchanged. The full local regression executed 3,408 tests
(3,407 passed, one skipped). Evidence:
`tmp/local-experiment/notebook-metadata-checks/92c916c4bfb145db9f2601648ecbb5c2/`.

## Natural-language table preview

Run `make local-notebook-lab-start` again to add `query_workbench.ipynb` to an
existing workspace. Existing Notebooks and saved results are preserved.

Open that Notebook, run the imports, then edit the table and request cell. The
example table is `smoke.fixture_v1` in the fixed `iceberg` catalog. Set
`filtered = True` for a supported single equality condition, for example
`idが1の行からnameとidを1件表示`. Run the last cell to display the typed rows.
An unsupported request displays its status and does not execute a data SELECT.
Each execution makes a fresh attempt; there is no automatic retry.

The existing local Qwen Assist receives the schema and request. The existing
Reader validates the selected columns, limit and optional equality condition;
row data is displayed in the Notebook. Save it to retain the request and output.

The worker uses the dedicated DataOps environment and project-local Go caches.
It reads the existing `~/.kube/config` for `colima-mac-studio-solo`; the existing
context checks require the local ARM64 Colima environment. No global environment
or kubeconfig is changed. Lab lifecycle operations and query execution share an
ownership lock. Use Interrupt once and allow the existing worker cleanup to
finish. An unresolved attempt remains recorded and blocks further execution or
shutdown until its cleanup is verified.

Interactive verification displayed the requested equality-filtered row and
refused an unsupported range condition without a data SELECT. Both requests and
outputs survived save and reload. Query workers and Readers exited, and the Lab
lifecycle lock was held during a real query. Existing training data and MLflow
records were preserved.

The first attempt failed during connection setup before model inference or any
data query. After verifying that no resources remained, its original failed
records were retained under `queries-closed-failures`; the connection-setting
propagation was repaired before a fresh attempt. This recovery is specific to
that verified pre-resource failure, not permission to disregard unknown cleanup.

## Ask for an exact row count

Run `make local-notebook-lab-start` to add `count_workbench.ipynb` once. Existing
Notebooks and their saved outputs are preserved. In the new Notebook, enter an
explicit table and a request such as `全体は何件ですか` or
`idが1の行は何件ですか`. Run the count cell once, then save the Notebook to
retain the input, exact count and selected snapshot.

The count profile supports all rows or one typed equality at the current head.
It makes one local Qwen proposal and uses the existing Count Reader, with the
same Lab ownership lock and cleanup handling as preview. It performs no asset
materialization, training or MLflow write. A count of zero is a successful
observation; unsupported, failed and unconfirmed outcomes remain distinct.
The result is an exact decimal string, including counts too large for precise
floating-point representation.

COUNT may scan the selected snapshot. Existing time bounds remain in effect,
and an uncertain attempt is not automatically retried. Each execution of the
count cell requests a new observation; reopening its saved output does not
execute a model or Reader. The original preview Notebook retains its original
column/limit behavior and optional single equality.

The final Notebook cell accepts a completed attempt token and calls
`recover_count(token)` to display its saved observation. It checks the original
result and receipts without calling Qwen or the Reader. The snapshot shown is
the one observed during that execution, not a new current-head measurement.

For a failed COUNT whose saved receipts prove that no Trino/Polaris runtime or
query started, `close_unstarted_count(token)` can record a separate closure
after checking the owned processes have exited. It preserves the failed status,
summary and outputs and does not repeat inference or COUNT. A started query,
missing or inconsistent receipt, or an active/unknown process is not eligible.
This closure permits a later, explicit new attempt; it does not turn the failed
attempt into a successful count or zero.

Local verification displayed all 2 fixture rows as an exact count in the Lab,
then redisplayed the saved result and preserved the Notebook through reload.
Separate calls through the same Lab API returned 1 for `id=1`, 0 for
`id=999999`, and unsupported for a range and an unknown column. All five
completed with cleanup verified. An initial failed connection attempt is kept
with its separate pre-runtime closure; the Count child now inherits the fixed
`KUBECONFIG`, as the existing describe child already did. The original six
Notebooks, MLflow records and prior audit rows were preserved.

## Compare Ridge settings

Run `make local-notebook-lab-start` to add `ridge_comparison.ipynb` once. Open it,
list the saved runs, copy an Iceberg `training_token` into the selection cell,
and set `alpha` between `0.001` and `1000`. Run the comparison cell to fit one
candidate in the existing ML environment. The tables show training and holdout
metrics plus the prediction difference for each of the 16 holdout rows. Save
the Notebook to retain the inputs and displayed comparison.

The reference is the saved Ridge model with alpha 1. Both models use the same
48 training rows and the same previously inspected 16 holdout rows. This is an
exploratory comparison; it does not provide an independent final evaluation or
select a model automatically. The worker reads validated saved data and makes
no Reader, LLM, MLflow API or new tracking-run calls.

Comparison execution shares the Lab ownership lock with query execution and
start/stop. Both workflows check each other's residual attempts before starting
new work. Interrupt sends one request to the owned worker and waits for its
exit. Unconfirmed cleanup blocks further execution and lifecycle changes.
Inputs, worker output and status are retained privately under
`tmp/local-experiment/notebook-lab/comparisons/<attempt_token>`.

Interactive verification fitted alpha 10 once, displayed its metrics and all
16 prediction differences, and retained them through save and reload. The
reused holdout RMSE rose from about 0.163 to 0.544 for this dataset. Existing
training files, MLflow runs and audit records were preserved.

## Predict with a saved comparison model

Start the Lab again to seed `comparison_prediction.ipynb`. Run its first cell
to list saved comparisons, then copy a `comparison_token` from `runs` and edit
the feature rows. The last cell shows the candidate's alpha and one prediction
per input row. Save the Notebook to retain those inputs and results.

The same inventory is available through `make local-comparison-runs`. Entries
that lack required receipts appear as `partial`; entries whose saved contract
cannot be verified appear as `invalid`. Only verified comparisons appear in
`runs`. The list sorts by token, not quality or completion time, and makes no
automatic model choice. An incomplete inventory is not a selectable result.

The reader checks six saved comparison files and the original training data,
including their bytes and identities before and after reading. It checks the
historical worker identity within the receipts without inspecting a potentially
reused PID. Prediction uses the saved coefficients and accepts 1–64 rows of
finite `x1`, `x2`, `x3` values. It performs no new fit, Reader, LLM or MLflow API
operation. It does not measure accuracy on the new inputs or serve an online
model endpoint.

Interactive verification listed the saved alpha 10 candidate, selected its
token, edited two feature rows and displayed both predictions. Saving and
reloading retained the inputs and outputs. A separate cold CLI check matched
18 predictions to independent arithmetic, including the original 16 holdout
rows. The existing saved files, MLflow records and audit rows were unchanged;
the edited Notebook also survived a Lab stop and restart.

## Record a saved comparison in MLflow

Start the Lab to seed `comparison_recording.ipynb` once. Run its inventory cell,
copy a verified `comparison_token` into the selection cell, and run the final
code cell. It records the saved candidate, reference and six metric differences
in the `shirokuma-local-ridge-comparisons-v1` experiment. The original comparison
time was not recorded; import time is stored separately and is not presented as
the model's training time.

The first call temporarily stops the owned MLflow server, saves a cold backup,
imports through the existing ML environment and restores the same store's prior
running or stopped state. The Notebook remains available. It performs no fit,
Reader or LLM call and does not promote a model. Save the Notebook to retain the
chosen token and receipt; open the linked MLflow UI to inspect the recorded run.

Calling again validates the original saved receipt, source and artifact, then
returns it without a new worker, backup, service restart or Tracking write. That
saved receipt is not a fresh readback of the current database. An uncertain
write remains pending and is not automatically resubmitted. Residual worker or
runtime cleanup blocks further Lab workflows until separately resolved.

Private records are retained under
`tmp/local-experiment/notebook-lab/comparison-recordings/` and the MLflow store's
`comparison-imports/` directory. Cold export and non-running clone verification
include the comparison profile and preserve its source training relationship.

Local acceptance recorded the saved alpha 10 candidate once, displayed its
18 metrics and 34 parameters in MLflow, and returned the same receipt on a
second call without changing the database or server identity. The selected
token and output survived Notebook save and reload. A cold export restored all
22 runs into an inactive clone and passed SDK readback. Saved-model predictions
remained unchanged; no training or Reader operation was repeated.
Stopping all six owned kernels and restarting the Lab applied the updated
modules while retaining all six Notebook files, permissions and modification times.
