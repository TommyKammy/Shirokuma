# Native Spark write and Trino readback

This local experiment extends the [isolated classpath gate](README.md) with one
native Kubernetes executor, the retained Polaris REST catalog and SeaweedFS S3
endpoint. The host creates one new table, Spark appends a fixed dataset, and
Trino independently reads the same table. This document describes the code
contract; it does not record a successful live connection or production
acceptance.

```sh
rtk proxy python3 tools/local_experiment/spark_iceberg/remote_run.py \
  --build-token <verified-build-token>
```

The build token must identify an existing verified classpath image receipt.
There are no arbitrary SQL, table, URL, credential, output-path or workload
arguments. Each invocation generates a fresh 32-character run token. The image
is freshly verified against the stored build and dedicated runtime cache; this
entrypoint does not build or publish an image and makes no model requests.

## Fixed operation and success conditions

1. Verify the dedicated runtime, fixed service identities and owned loopback
   tunnels for host Trino/Polaris access. Capture the retained fixture's schema,
   rows, snapshots, history, refs and table inventory before mutation.
2. Require catalog absence of `iceberg.local_experiment.spark_<run-token>`.
   Through Trino, create that table with `id INTEGER`, `name VARCHAR`,
   `bucket INTEGER`, `amount BIGINT`, Parquet, Iceberg format version 2 and no
   partitioning. Its exact location is
   `s3://shirokuma-lakehouse/l1/local_experiment/spark_<run-token>` and its comment
   is `shirokuma-local-spark-iceberg:<run-token>`. Capture its original UUID,
   metadata/schema identity and empty state before Spark receives access.
   An empty Trino-created table may already have an append snapshot. Preserve
   its exact ID as `initial_snapshot_id`; use null only when no snapshot exists.
   An existing initial snapshot must be parentless and report zero total records
   and zero data files, with actual empty row and file readback.
3. Create only the run's temporary Polaris principal, principal role and
   catalog role; verify exact table grants and role bindings. Add two temporary
   ingress NetworkPolicies for the run namespace. Issue the temporary Bearer
   only when the workload Secret is requested.
4. Create a new restricted-PSA namespace and the fixed native Spark profile.
   The driver checks UUID, location, comment, schema, format, partitioning and
   the host-observed initial snapshot ID and empty state. It checks that same
   ID again immediately before writing. It issues one fixed `INSERT INTO` for 32 rows, with
   `id=0..31`, `name=row-000..row-031`, `bucket=id % 4` and
   `amount=(id+1)*7`. The script contains no CREATE, ALTER, DELETE or DROP.
5. A fresh Spark session reads every row through DataFrame and SQL paths. A
   shuffle produces four groups, independently expected as
   `[[0,8,840],[1,8,896],[2,8,952],[3,8,1008]]`. Two partition observations must
   identify the one expected executor Pod, executor ID `1`, the same stage,
   distinct task attempts and no retry. The host also verifies the actual Pod
   identity, image and termination; job output alone is not runtime attestation.
6. Require a passed structured result and driver exit 0, then inspect the table
   again through host REST/Trino clients. Spark DataFrame rows, Spark SQL rows,
   Trino rows and the independent Python oracle must agree. UUID, location,
   comment, metadata location, aggregate groups and exact data-file
   paths/counts/sizes must match across engines. The new append snapshot must
   differ from the initial ID and have that ID as its parent. The complete
   snapshot set is exactly the initial empty snapshot plus the new append, or
   one parentless append when the initial ID was null. Snapshot
   IDs remain exact 64-bit integers, not floating-point display values.
7. Prove Spark cleanup and owned Trino-query drain before access cleanup. Remove
   the temporary access policies/bindings/entities, then recheck the original
   table identity and drop only that table. Verify catalog absence and compare
   the retained fixture/table inventory with the initial capture. Any failed
   verification prevents an overall passed receipt.

The host's catalog-name absence check and explicit location do **not** establish
that the S3 prefix was empty: this entrypoint does not perform a prefix LIST
before creation. Cleanup proves the stated table/resource removals; it does not
prove absence of all orphan objects or multipart uploads in S3.

## Authentication and network scope

The management credential stays in the host process. Spark receives no Polaris
management client secret. Its temporary role has only `TABLE_LIST`,
`TABLE_READ_PROPERTIES` and `TABLE_WRITE_PROPERTIES` grants on the exact new
table. Creation/drop remain host operations. Entity names, versions, properties,
grants, bindings and memberships are read back; an existing name or unrelated
membership is a conflict.

Polaris reports the issued token's `expires_in`. The client records that value;
it does not choose a shorter server lifetime. Token refresh and token exchange
are disabled. Deleting a principal or removing role bindings is not a claim of
instant revocation of an already issued Bearer. Polaris entity deletes are
name-based and lack Kubernetes-style atomic UID preconditions: exact prior
create/readback identities and no observed drift are required, but that is not
an atomic compare-and-delete guarantee.

The S3 key is the **existing static bucket credential** read from the fixed
application Secret. It is copied into an AWS shared-credentials file for the
experiment. A short-lived namespace, Secret or Polaris role does not reduce its
S3 permissions or expire that key. Table-scoped Polaris authorization is not
S3 prefix authorization; this is not a least-privilege S3 credential demo.

The immutable `connection` Secret has exactly `aws-credentials` and
`polaris-token`. The driver mounts both; the executor's Secret projection mounts
only `aws-credentials`. Both use a read-only file mount and
`AWS_SHARED_CREDENTIALS_FILE`; S3 key values are not placed in SparkConf, command
arguments or environment variables. The fixed region and endpoint are public
connection settings. The driver reads the Bearer file into a runtime catalog
option and unsets that SQLConf key after initializing each Spark session. Saved
ConfigMaps and submit arguments contain no Bearer value.
The profile explicitly selects `rest.auth.type=oauth2`. Direct JVM catalog loads
temporarily make the target session active and restore the previous active
session (or clear it) in `finally`, including on initialization failure. This
also applies to the fresh read session: Spark's
[CatalogManager](https://raw.githubusercontent.com/apache/spark/v4.1.3/sql/catalyst/src/main/scala/org/apache/spark/sql/connector/catalog/CatalogManager.scala)
obtains options through the active SQLConf, rather than from the method's
session argument alone.

**The file boundary is narrower than a complete in-memory secrecy claim.**
Iceberg's REST catalog passes catalog properties into FileIO, so the absence of
a Bearer file on the executor does not prove that its value never appears in
internal task/FileIO serialization. This experiment has not verified that
internal boundary. See the pinned
[Iceberg 1.11.0 RESTSessionCatalog source](https://raw.githubusercontent.com/apache/iceberg/apache-iceberg-1.11.0/core/src/main/java/org/apache/iceberg/rest/RESTSessionCatalog.java).

The fixed service endpoints are:

| Service | Endpoint | Allowed target |
| --- | --- | --- |
| Polaris | `http://polaris.shirokuma-dev.svc.cluster.local:8181/api/catalog` | `shirokuma-dev`, labels `name=polaris`, `component=catalog-server`, TCP 8181 |
| SeaweedFS S3 | `http://seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333` | `shirokuma-storage`, labels `name=seaweedfs`, `component=object-storage`, TCP 8333 |

The label keys in both rows are `app.kubernetes.io/name` and
`app.kubernetes.io/component`. The host verifies the existing Service UID and
spec before use and guards against drift. The new namespace keeps default deny,
same-run Spark RPC, cluster DNS and driver-only access to the observed Kubernetes
API addresses/ports. One additional data-egress policy permits only the two
listed service Pod selectors/ports. Each service namespace receives one new
ingress policy limited to the exact run namespace and Spark run labels; existing
policies are not edited. Known policy UID/resourceVersion checks precede deletion.
NetworkPolicies are additive: this does not establish that all existing network
policies provide exclusive isolation. Cluster service HTTP is not a TLS/mTLS
verification, and there is no public endpoint or host trust-store change.

The native profile retains ARM64 digest pinning, image pull policy `Never`,
restricted PSA v1.33, UID/GID 185, a read-only root, dropped capabilities and
bounded emptyDir storage. The driver and one executor together are capped at
2 CPUs and 4 GiB; the namespace quota allows two Pods, one Service, one Secret
and no PVC. Driver RBAC is limited to namespace Pod/Service/ConfigMap operations;
the executor service-account token is not mounted. A plan without observed API
endpoints is deliberately not run-ready.

## Private evidence and failure handling

The host receipt is stored under
`tmp/local-experiment/spark-iceberg-remote/<run-token>/`; native runtime evidence
is under `tmp/local-experiment/spark-runs/<run-token>/`. Directories/files use
private modes. Receipts include source hashes, public service/table identities,
durable pending operations, query IDs, readback evidence and cleanup status.
Full typed rows and snapshot/file metadata are private evidence; the CLI prints
only the result, run token and relative evidence path.

Secret material remains in memory for API submission and log redaction. The
saved plan retains Secret key names and identity metadata, not `data` or
`stringData`. Issued tokens and raw management credentials are not journaled.
Known secret values and supported encoded variants are redacted from captured
workload logs; Spark credential/token redaction settings provide an additional
layer. These measures do not claim forensic erasure or absence from all internal
library memory. AWS profiles, Bearers and raw HTTP exception bodies are not
intentional receipt fields.

Mutation intent is persisted before the request. A lost response, timeout,
malformed response, failed identity readback or interruption cannot be promoted
to success. The fixed INSERT is not rerun by the host or generated script.
This is distinct from the Iceberg REST HTTP layer: Iceberg 1.11.0 rejects zero
for `rest.client.max-retries`, so the profile uses its minimum of **one retry**,
with 10-second connection/socket timeouts. Its I/O-exception path retries only
idempotent methods and excludes interrupted I/O, DNS/connect/closed-connection,
no-route and SSL failures, and cancelled requests. The response path permits
429 and 503 with `Retry-After` even for POST; other 500/502/503/504/408 retries
require an idempotent method. A POST retry is therefore not evidence that the
first request was never sent. The library can honor a server `Retry-After`;
the bounded Pod lifecycle still governs the workload. These are configured
rules, not measurements of how many HTTP requests occurred during a run. See
the pinned [Iceberg retry strategy](https://raw.githubusercontent.com/apache/iceberg/apache-iceberg-1.11.0/core/src/main/java/org/apache/iceberg/rest/ExponentialHttpRequestRetryStrategy.java).

Failed job results retain a bounded `error_classes` list with Python/Java cause
class names only, alongside the fixed stage error code. Messages, HTTP bodies
and stack traces are not included in that field; unavailable class information
remains `unknown`. A passed result requires an empty error-class list.

An unknown access
mutation keeps its pending state and defers access cleanup; later GET state is
not adopted as proof that the original create succeeded. Unconfirmed table
creation/UUID, active or uncertain writers, undrained queries, identity drift or
unverified access removal likewise prevents table DROP. Partial resources/data
may therefore remain, with the original failed evidence retained. A successful
later cleanup does not rewrite the failed attempt as a successful write.

`remote_run.py` currently exposes a fresh-run command only; it has no complete
remote-workflow resume/recover CLI. Native Spark recovery can remove known
Kubernetes workload resources from its saved receipt, but does not by itself
revoke remote Polaris entities, remove cross-namespace ingress policies or drop
the Iceberg table. Those remaining operations require the captured identities,
readback and writer/drain checks to be resolved within the same bounded local
scope. Do not rerun an INSERT or adopt an unknown create merely to make cleanup
appear complete.

## Claim boundary

This tests one fixed, small append through the stated local REST/S3 integration
and cross-engine readback. It does not establish transaction/retry behavior under
concurrent writers, credential rotation, prefix-isolated S3 permissions,
workload recovery, scale or performance, retained-data backup/restore, end-to-end
TLS, operator acceptance or production readiness. Live results and preservation
audits belong in the WP/milestones after execution.
