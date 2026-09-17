# Local Iceberg branch publish

This WP-L3-ICE-001 experiment uses the existing verified Spark 4.1.3 / Iceberg
1.11.0 image, Polaris, SeaweedFS and Trino 483. Each invocation creates one fresh
owned table and either publishes a descendant branch to main or demonstrates
that a diverged main cannot be published by this host workflow.

```sh
rtk make local-spark-publish SPARK_ICEBERG_BUILD_TOKEN=<verified-build-token>
rtk make local-spark-publish-negative SPARK_ICEBERG_BUILD_TOKEN=<verified-build-token>
rtk make test-local-spark-publish
```

Both modes create A with two fixed rows, append id 3 on main to create B, and
seal `baseline_tag` at A and `audit_branch` at B. Spark appends id 4 exactly once
to the branch, creating C as a child of B. Spark and Trino verify the exact
named and numeric snapshot contents. C contains all B files plus one new file.
The previous independent branch experiment remains separate: its B and C are
siblings and cannot satisfy this fast-forward condition.

Before publishing, native driver/executor resources must be removed, owned
queries drained, and temporary writer access removed. The host checks current
table UUID, marker, location, metadata digest and the snapshot parent chain.
The positive mode requires main B to be an ancestor of branch C and sends one
REST commit with four requirements: the original table UUID and exact main B,
branch C and tag A references. Its sole update sets main to C. The operation
identifier and request digest are durably recorded before the POST. Success
requires response and fresh metadata agreement, unchanged snapshots and files,
and independent Trino reads of main C, branch C, tag A and numeric A/B/C.
Only the main reference, current snapshot and associated metadata history may
change. This is metadata-only publication within one table.

The negative mode first appends id 5 to main B, producing D, a sibling of C.
An expectation of B is rejected as `STALE_MAIN`; an expectation of D is rejected
as `MAIN_NOT_ANCESTOR`. Both checks send zero publish POSTs. Named and numeric
A/B/C/D reads verify that main D and branch C retain their different contents.
These are host preflight rejections; they do not demonstrate a live server
CAS conflict under concurrent writers. Native Iceberg REST reference updates
alone do not enforce the host's ancestry rule.

Unknown commit outcomes, malformed responses, timeouts and cancellation retain
the pending operation and table. No automatic resend, rollback or DROP follows
an uncertain write. Cleanup requires known write outcomes, stopped writers,
drained queries, removed access and the original table UUID/marker/location.
The workflow has no automatic resume command. A fresh invocation uses a fresh
table and does not resolve or overwrite earlier uncertain evidence.

The existing one-driver/one-executor contract is reused, each limited to one CPU
and 2 GiB. No image build, model call or new persistent service is needed.
Retained tables, snapshots, backups and earlier experiment data are preserved.
No snapshot expiry, orphan-file deletion, global reset or cache flush is used.
Polaris table permissions do not make bucket-scoped S3 credentials branch- or
prefix-isolated; removal of access does not prove immediate JWT revocation.

This does not establish independent branch authorization, zero-copy cloning
between tables, generic merge or rollback, concurrent-writer correctness,
byte-for-byte hashing of all objects, or production readiness.

## Pinned references

- [Iceberg 1.11.0 fast-forward ancestry](https://raw.githubusercontent.com/apache/iceberg/apache-iceberg-1.11.0/core/src/main/java/org/apache/iceberg/UpdateSnapshotReferencesOperation.java)
- [Iceberg 1.11.0 reference commit requirements](https://raw.githubusercontent.com/apache/iceberg/apache-iceberg-1.11.0/core/src/main/java/org/apache/iceberg/UpdateRequirements.java)
- [Iceberg 1.11.0 current snapshot and metadata history](https://raw.githubusercontent.com/apache/iceberg/apache-iceberg-1.11.0/core/src/main/java/org/apache/iceberg/TableMetadata.java)
