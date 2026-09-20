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

Training, queries, comparisons and Lab lifecycle commands share one ownership
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
