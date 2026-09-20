# Local training workbench

A single-user JupyterLab for exploring saved Shirokuma training runs. This uses
a dedicated environment and workspace. The existing fixed Notebook runner and
its environment remain available separately.

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
