# Local Ray saved-model prediction

Run one verified saved Iceberg Ridge model on two local Ray actors:

```sh
rtk proxy uv sync --locked --project tools/local_experiment/ray --python 3.12
rtk proxy make local-ray-predict TRAINING_TOKEN=YOUR_SAVED_TRAINING_TOKEN
```

The token comes from the existing saved training inventory. The dedicated Python
3.12 environment pins Ray 2.58.0 and its wheel build
`cec0a091b12675bbf101cf8a350c8706107baee1`. It does not add Ray to the Notebook or
MLflow environments. `make test-local-ray` runs the offline tests without
starting Ray.

The existing [saved-model loader](../notebook/training.py) validates the receipt,
workload and matching JSON artifact. The existing [prediction code](../mlflow/predict.py)
produces a sequential reference for the saved 64 rows. Two host-defined actors
each request one CPU and predict 32 rows with no actor or task retry. Their PIDs
must differ. Returned model, source and input hashes must agree, and all 64
predictions must exactly match the sequential reference in original row order.
Source files are checked again after calculation and before publication.

The JSON result includes predictions, model/source identity, actor PIDs and
partition bounds, parity and cleanup confirmation. It omits input rows and model
coefficients. This is a single-machine execution demonstration, without a speedup,
multi-node, general model-quality or online Serving claim. It performs no fit,
Reader call, model deserialization from pickle, MLflow API call or Tracking write.
Ray's internal local actor serialization only carries the fixed host actor and
bounded JSON values; arbitrary user code, runtime environments and cluster
addresses are not accepted.

Each attempt keeps private intent, process identities, worker logs, result and
status under `tmp/local-experiment/ray-runs/<token>`. The owner holds one local
lock and requires prior attempts to have confirmed cleanup. A short exclusive
`/private/tmp/skr-<uid>-<token-prefix>` directory stays mode 0700; Ray uses its `r/`
child because Ray itself makes its temporary root mode 0777. The private outer
directory prevents access through that shared inner mode. Temporary evidence is
retained after exit, including failures; nothing from previous runs is deleted.

The fresh child environment omits ambient Ray cluster addresses, cloud credentials
and code hooks. Ray uses `address="local"`, two CPUs, zero GPUs, 128 MiB object
store, explicit loopback node/dashboard addresses, disabled dashboard UI and
disabled usage reporting. Metrics collection is disabled for reporters. Pinned
[Ray networking source](https://github.com/ray-project/ray/tree/cec0a091b12675bbf101cf8a350c8706107baee1)
also binds remaining local agents to loopback; the run checks owned TCP listeners
and rejects wildcard or non-loopback listeners. This is not isolation from another
process running as the same host user.

The owner waits up to 180 seconds, then sends one interrupt to its identity-bound
worker and allows 60 seconds for `ray.shutdown(wait_for_processes=True)`. It
tracks descendants across process groups and confirms their exit before releasing
predictions. No parent-side forced kill or automatic retry is used. Unconfirmed
cleanup remains `unknown` and prevents another launch until separately inspected.
Cancelling the CLI preserves the first interrupt while retaining private evidence.
On macOS, process inspection records both real and effective user IDs. The fixed
`/bin/ps` inspection command may have effective UID 0 under its system setuid bit;
it is accepted only below an owned parent with the owner's real UID. The owner
signals only its original, identity-checked driver.

Focused tests use fake Ray/processes only; starting a real cluster is not part of
the test suite. On 2026-09-20, a native arm64 run completed in 4.932 seconds:
two actors returned all 64 predictions exactly equal to the sequential result,
all seven observed TCP listeners used loopback, and owned processes exited.
An independent decimal calculation differed by at most `8.90E-16`. Existing
21 MLflow runs, 1,365 metric points, 21 artifacts, 560 AgentHouse rows and 43
protected files were unchanged. See the
[Ray work package](../../../docs/design/06_WorkPackages/L4/WP-L4-RAY-001_Ray_integration.md)
for the retained first failure, closure evidence, disk impact and limitations.
