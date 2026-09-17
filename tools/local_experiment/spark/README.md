# Native Spark local experiment

Run a bounded Spark 4.1.3 driver and one executor on the existing ARM64 Shirokuma
k3s cluster. This is the local increment of WP-L4-SPARK-001 under ADR-0034.
It adds no operator, CRD, persistent volume, retained table or cluster upgrade.

```sh
rtk make test-local-spark
rtk make local-spark-prepare
rtk make local-spark-plan
rtk make local-spark-run
rtk make local-spark-failure
# Retry only cleanup of a recorded run; never rerun its workload:
rtk make local-spark-recover SPARK_RUN_TOKEN=<32-hex-token>
```

`prepare` verifies the dedicated Colima/Docker/Kubernetes contexts, downloads the
fixed official ARM64 image if absent, and checks the Docker and CRI identities.
It preserves existing images. `plan` performs fresh read-only cluster checks and
saves the proposed manifests; it does not create Kubernetes resources.

Each `run` allocates a new `shirokuma-spark-<token>` namespace. It applies
restricted Pod Security Admission pinned to v1.33, namespaced RBAC, a quota of
two Pods / 2 CPU / 4 GiB memory, and explicit NetworkPolicies. Both containers
use UID/GID 185, a read-only root filesystem, and bounded emptyDir volumes.
Only the driver has a service account token. Its role has no Secrets or PVC
access. Egress permits CoreDNS, the experiment's own Spark RPC ports, and the
private API service/endpoint addresses and ports observed during preflight.
The executor cannot use the API egress exception. Existing admission policies
scoped to `shirokuma-dev` are not claimed to cover the new namespace.

Spark's automatic Service and PVC cleanup are disabled explicitly. The host
owns the driver Service and records generated Endpoint/EndpointSlice UIDs before
deletion; no PVC permission or collection-delete permission is added to RBAC.

The job generates 32 integers, filters 21 rows, and shuffles/aggregates them
into four groups. Its result includes Spark version, image reference, executor
hostname and TaskContext evidence. A separate Python oracle checks exact group
counts/totals and partition evidence. Success requires driver exit 0. Failure
mode performs the same checked computation, then intentionally exits 17;
the runner succeeds only when that expected failure and cleanup are verified.

## Evidence and cleanup

Private evidence lives under `tmp/local-experiment/spark-prepare/<token>` and
`tmp/local-experiment/spark-runs/<token>`. It includes the input manifests and
hashes, implementation hashes, created UIDs, actual Pod/image identities,
bounded driver logs, result validation and cleanup outcome. Keep this evidence
outside Git; the source, fixtures and recovery rules belong in Git.

Cleanup uses recorded UID preconditions and checks namespace inventory before
deleting the namespace. Unknown or replaced resources stop cleanup; an
ambiguous create outcome is not adopted automatically. `recover` performs
cleanup only and writes a separate recovery record, preserving the original
failure. It cannot recover a namespace whose creation UID was never recorded.
Inspect that uncertainty before taking any manual action. Never delete by name
alone or reset Colima to repair an experiment.

The runner waits for both container termination and the Pod's terminal phase;
these are not updated simultaneously. Service garbage collection can change
EndpointSlice ownership, so previously recorded generated UIDs remain the
cleanup identity. Recovery validates fixed names, kinds and UID records before
making runtime calls. A completed recovery never re-executes Spark.

The image remains in the local cache after the job. Its compressed layers total
794,127,774 bytes; preparation records actual disk and image-store observations
separately. Job emptyDir storage disappears with the Pods. There is no dataset
backup or restore operation in this increment. Preserve the private receipts
when exporting experiment history; never include cluster credentials.

## Scope and version sources

The target is the existing single ARM64 k3s node (Kubernetes 1.33), with Docker
CRI. Image identity is
`docker.io/library/spark@sha256:82d7d3c2315a8b826d83485728013c01d1f2542708c83c0c641a363924d065b7`.
It comes from `4.1.3-scala2.13-java17-python3-ubuntu`. The official 4.1.3
Kubernetes minimum is 1.32; adopting 4.2.0 would require at least 1.34.

- [Spark 4.1.3 on Kubernetes](https://spark.apache.org/docs/4.1.3/running-on-kubernetes.html)
- [Spark 4.2.0 on Kubernetes](https://spark.apache.org/docs/4.2.0/running-on-kubernetes.html)
- [Official Spark image definition](https://github.com/apache/spark-docker/blob/994aa3719a9d6f759d50d7d9d4ba5ad23f30cb7f/4.1.3/scala2.13-java17-python3-ubuntu/Dockerfile)

This verifies a native distributed execution path on one physical node. It is
not a benchmark or evidence of multi-node resilience, general data durability,
Iceberg/Polaris integration, JupyterHub, MLflow training integration, Comet,
organization-wide policy enforcement or production acceptance. It makes no LLM
requests. Actual execution results and retained-state checks belong in the WP
and the local experiment milestones after verification.
