# Local Iceberg branch write

This WP-L3-ICE-001 experiment uses the existing verified Spark 4.1.3 / Iceberg
1.11.0 image, Polaris, SeaweedFS and Trino 483. It creates one fresh owned table
and tests a branch write while preserving the main and tag contents.

```sh
rtk make local-spark-branch SPARK_ICEBERG_BUILD_TOKEN=<verified-build-token>
rtk make test-local-spark-branch
```

The fixed four-column table has two base rows at snapshot A. `baseline_tag` and
`audit_branch` initially point to A. A main-only row (id 3) creates B. Spark then
inserts a different branch-only row (id 4) through `branch_audit_branch`, creating
C. Main must remain B, the tag must remain A, and the branch must point to C.
B and C have the same row count but different contents and both have A as their
parent. A possible initial empty snapshot is retained as separate history.

The host validates UUID, marker, location, schema, references and exact snapshot
metadata before allowing the job. Spark reads every A/B/C snapshot with an
independent fixed row oracle, and captures each snapshot's data-file inventory.
A/B metadata and files must stay unchanged. C must retain A's files and add only
the branch row. Trino verifies named and numeric snapshot reads independently.
Its `$files` observation covers current main B; it is not used as a branch file
inventory. All snapshot IDs remain exact Python integers in private evidence.
For Spark 4.1, DataFrame tag reads use `versionAsOf`; the Iceberg adapter rejects
the legacy `tag` option. Named branch reads keep the supported `branch` option.

The job reuses the existing one-driver/one-executor contract, each limited to
one CPU and 2 GiB, with a temporary exact-table Polaris role and scoped ingress
policies. It does not rebuild an image, start a new service or call an LLM.
Polaris table permissions do not make the retained bucket-scoped S3 credentials
branch-specific or prefix-isolated.

The host journals intent before each write and before native execution. An
unknown REST or Spark write outcome is not repeated. The job calls INSERT once;
the pinned Iceberg library still has its documented internal commit/HTTP retry
behavior. A failed, missing or uncertain write receipt cannot be accepted as a
successful branch experiment.

Cleanup requires verified native resource removal, owned query drain and access
cleanup. The host drops only its original UUID/marker/location table after the
write outcome is known; uncertain outcomes retain the table for scoped
inspection. Retained tables, snapshots, backups and earlier experiment data
are preserved. No snapshot expiry, orphan-file deletion, global reset or cache
flush is performed. Native recovery can inspect the saved exact profile;
the full branch workflow has no automatic replay/recovery command.

These are named references on one table. This does not establish independent
branch authorization, cross-table zero-copy cloning, concurrent-writer behavior,
general restoration guarantees, immediate JWT revocation or production readiness.

## Pinned references

- [Iceberg 1.11.0 Spark branch writes](https://raw.githubusercontent.com/apache/iceberg/apache-iceberg-1.11.0/docs/docs/spark-writes.md)
- [Iceberg snapshot parent selection](https://raw.githubusercontent.com/apache/iceberg/apache-iceberg-1.11.0/core/src/main/java/org/apache/iceberg/SnapshotProducer.java)
- [Iceberg reference and current-snapshot handling](https://raw.githubusercontent.com/apache/iceberg/apache-iceberg-1.11.0/core/src/main/java/org/apache/iceberg/TableMetadata.java)
- [Trino 483 Iceberg metadata](https://raw.githubusercontent.com/trinodb/trino/483/plugin/trino-iceberg/src/main/java/io/trino/plugin/iceberg/IcebergMetadata.java)
- [Spark 4.1 legacy time travel option validation](https://raw.githubusercontent.com/apache/iceberg/apache-iceberg-1.11.0/spark/v4.1/spark/src/main/java/org/apache/iceberg/spark/Spark3Util.java)
