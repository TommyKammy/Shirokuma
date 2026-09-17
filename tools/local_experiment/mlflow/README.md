# Local MLflow evaluation tracking

WP-L4-ML-001 starts with saved Agent evaluation receipts. This is a dedicated
single-user local Tracking service, not completion of the Spark/JupyterHub,
training, registry, serving or production work packages.

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

## Runtime and storage boundaries

All state belongs to `tmp/local-experiment/mlflow-tracking/`, including the
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
This implementation uses one fixed state path and provides no automatic
deletion/reset or alternate-store command. Keep a damaged store intact before
planning a fresh replacement; do not remove it to make an import pass.

For a consistent filesystem export, stop the owned server and importer, retain
the complete private state directory with its owner/index files and artifacts,
and preserve source receipts separately. A database-only copy omits artifacts
and importer identity. This workflow does not delete or overwrite backups;
restoring an export and migrating it between hosts are not yet verified.

## Primary references

- [Pinned MLflow release](https://pypi.org/project/mlflow/3.16.1/)
- [Tracking server](https://mlflow.org/docs/latest/self-hosting/architecture/tracking-server/)
- [SQLite/backend store](https://mlflow.org/docs/latest/self-hosting/architecture/backend-store/)
- [Host/CORS protections](https://mlflow.org/docs/latest/self-hosting/security/network/)
- [3.16.1 server startup](https://github.com/mlflow/mlflow/blob/v3.16.1/mlflow/server/fastapi_app.py#L189-L220)
- [Usage tracking controls](https://mlflow.org/docs/latest/community/usage-tracking/)
