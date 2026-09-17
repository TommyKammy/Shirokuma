# Local Spark / Trino snapshot measurements

This bounded experiment reads one new Iceberg table through both engines. It
uses the existing local Spark 4.1.3 / Iceberg 1.11.0 image and Trino 483,
Polaris and SeaweedFS services. No new image, resident service or model call is
needed. Run from the active autonomous checkout:

```sh
rtk make local-spark-benchmark SPARK_ICEBERG_BUILD_TOKEN=<verified-build-token>
rtk make test-local-spark-benchmark
```

The build token must reference the verified classpath image from the
[Spark/Iceberg experiment](../spark_iceberg/README.md). The CLI accepts no
arbitrary SQL, scale factor, endpoints or source path. Receipts are private under
`tmp/local-experiment/spark-benchmark/<token>/`; native runtime evidence uses
`spark-runs/<same-token>/`. The output token and result identify the retained
receipt. A failed run is retained; it is not automatically restarted.

## Dataset and independent result checks

The fixed source is Trino's `tpch."sf0.1".lineitem`, using nine columns:
orderkey, linenumber, returnflag, linestatus, quantity, extendedprice, discount,
tax and shipdate. Before casting, the host validates source types, non-null
values, numeric bounds, finiteness and an exact DOUBLE → DECIMAL → DOUBLE round
trip. The generated source is synthetic data. The scale factor is supported by
the [pinned Trino connector](https://raw.githubusercontent.com/trinodb/trino/483/docs/src/main/sphinx/connector/tpch.md);
its [pinned generator](https://raw.githubusercontent.com/trinodb/tpch/1.4/src/main/java/io/trino/tpch/LineItemGenerator.java)
uses fixed generation rules. Actual row count and file bytes come from each
receipt, not an estimated count.

The ordered source stream feeds an independent Python Decimal oracle and a
logical SHA-256 digest. Only counters, six possible groups, one previous key
and the digest remain in memory. The transport and oracle enforce one million
rows and 256 MiB; no raw dataset is saved on the host. The host creates one
owned Parquet table at `iceberg.local_experiment.spark_<token>`, then streams
its specific snapshot in the same key order. Complete row count, logical hash
and expected aggregate results must agree before measurement.

The sealed identity includes UUID, marker, location, snapshot, parent snapshot,
metadata location and the complete data-file list. The host checks this seal
around Trino measurements and before/after Spark. Spark checks the same identity,
schema and file list before and after its queries. Both engines explicitly read
the sealed snapshot; they do not rely on a mutable current table view.

`q1_sums` is derived from TPC-H Q1: four exact sums and a count, grouped by return
flag and line status. It intentionally excludes averages. `q6` calculates
discounted revenue for the fixed date, discount and quantity predicates. This
is a TPC-H-derived workload, not the full standard benchmark. Decimal outputs
are explicitly cast to DECIMAL(28,6), and the receipt preserves SQL NULL as null.
Different AVG result scales in
[Spark 4.1.3](https://raw.githubusercontent.com/apache/spark/v4.1.3/sql/catalyst/src/main/scala/org/apache/spark/sql/catalyst/expressions/aggregate/Average.scala)
and [Trino 483](https://raw.githubusercontent.com/trinodb/trino/483/core/trino-main/src/main/java/io/trino/operator/aggregation/DecimalAverageAggregation.java)
are therefore outside this comparison. Exact values, types and hashes must
match the Python oracle for every measurement. Floats are never rounded into
apparently exact decimal results.

## What timing means

There are 16 observations: two engines × two queries × four observations.
Index 0 is first-observed; indices 1–3 are subsequent observations. Trino runs
first and Spark second. The repeat median is descriptive only; there is no
automatic engine recommendation or claimed statistical significance.

- Trino elapsed time spans the host POST through full result consumption. It
  includes network, JSON processing, bounded row handling and the durable
  progress-journal writes made while consuming pages. Server elapsed,
  queued, CPU and input counters are separate fields; absent values remain null.
- Spark elapsed time spans `spark.sql` through `collect`. Result normalization
  and oracle comparison occur afterwards. It includes client/JVM scheduling and
  result delivery, not only worker CPU time.
- SparkSession `getOrCreate` startup time is recorded separately. Executor
  readiness can still contribute to the first query. The native workflow wall
  time includes preflight, creation, execution and verified cleanup.
- Source generation, source/table streaming, identity/file guards, the separate
  executor task probe, permission setup and cleanup are outside query timings.

The source scan and Trino-first order can warm shared filesystem, object-store,
catalog and engine caches. No global cache eviction or host reconfiguration is
performed. The Spark job does not call cache/persist. First-observed does not
mean cold-cache. The existing Trino and the one-driver/one-executor Spark job
have different resource allocations on a shared host; this is a functional
baseline, not a general performance ranking.

## Ownership, limits and recovery

The host validates the existing image/runtime/service identities. Spark uses
the existing bounded native Pod/RBAC/NetworkPolicy contract: one CPU and 2 GiB
limits each for driver and executor (driver CPU request 500m, executor request
one CPU), with a new run namespace. Before the initial
live experiment, verify at least 5 GiB free on host and VM. New table data files
are capped at 32 files / 256 MiB; metadata, temporary worker storage and private
receipts add small overhead. The cap validates completed output; it is not a
filesystem quota during CTAS. Existing images, old receipts, backups and older
experiment prefixes are retained.

The temporary Polaris role receives only TABLE_LIST and TABLE_READ_PROPERTIES
for the exact precreated table. The job contains no CREATE/INSERT/DROP. The
existing static S3 credentials still have their existing bucket scope; Polaris
read grants do not make those credentials read-only or prefix-scoped. Secret
values stay out of plans, receipts and logs. Refer to the
[remote access boundary](../spark_iceberg/REMOTE.md) for the retained credential,
token expiry and internal Iceberg serialization limits.

Every Trino POST is attempted once, with a durable pending marker and an owned
query user. A lost response is not treated as rejection. Query drain waits for
the bounded server lifetime when no query ID was returned. A new invocation
does not adopt an uncertain old table. Confirmed UUID/marker/schema identity is
required before cleanup; same-name replacements are never deleted.

After Spark resource cleanup and owned query drain are verified, temporary
access is removed and the same UUID table is dropped. The retained fixture and
table inventory must match their pre-run observation. Cleanup success proves
the observed catalog and Kubernetes boundaries; it does not prove S3 orphan
absence, immediate JWT revocation, rollback of uncertain writes, HA or production
acceptance. Unknown outcomes remain in their original receipts for scoped
inspection. The existing native recovery mechanism can inspect its saved exact
profile; the complete benchmark orchestrator has no automatic recovery/retry
command.

## Track saved measurements

`rtk make local-spark-benchmark-report` validates all saved benchmark evidence
without starting Spark, Trino queries or model calls. The
[MLflow importer](../mlflow/README.md#saved-sparktrino-benchmarks) retains four
engine/case runs per source in its own experiment. Exact durations and snapshot
identity stay in a fixed safe artifact; measurement scope, missing values and
failed sources remain visible. This is saved-evidence tracking, not a fresh
runtime or performance verification.
