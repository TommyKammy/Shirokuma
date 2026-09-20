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
The fixed training worker's outer-runner handshake is not provided by this Lab.
Use the existing fixed runner to start that training workflow.

Dependencies are pinned in `pyproject.toml` and `uv.lock`: JupyterLab 4.6.3,
ipykernel 7.3.0 and nbformat 5.11.1, using Python 3.12. Authentication and server
settings follow the [Jupyter Server configuration reference](https://jupyter-server.readthedocs.io/en/stable/other/full-config.html).
The environment uses a private kernelspec and project-local Jupyter/IPython
directories. No global kernelspec, login item or host-wide configuration is
installed.

Local verification opened this Notebook in JupyterLab, listed three saved
training runs, selected an Iceberg run, edited the inputs and displayed two
predictions. Saving and reloading retained the inputs, HTML review, residual
chart and predictions. Unauthenticated API requests were rejected. The edited file retained its bytes, permissions and modification time across
server restarts. A two-kernel shutdown and immediate restart also passed.
Interrupted shutdown can resume from the saved ownership record; it does not
restart kernels or stop an unidentified process. Initial configuration and
shutdown failures are retained in the local experiment evidence.
