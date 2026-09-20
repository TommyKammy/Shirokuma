# Local Comet / Iceberg experiments

This experiment checks Apache DataFusion Comet 1.0.0 on the existing Linux
ARM64 Spark 4.1.3 / Iceberg 1.11.0 lab. It first verifies the artifact, builds
one local image layer, and executes native Iceberg reads in an isolated
container. The [implementation plan](plan.md) records the exact artifact,
native operators and subsequent same-snapshot comparison boundary.

Run from the active autonomous checkout, retaining each returned token:

```sh
rtk make local-spark-comet-prepare
rtk make local-spark-comet-build SPARK_COMET_PREPARE_TOKEN=<prepare-token> SPARK_ICEBERG_BUILD_TOKEN=<verified-base-build-token>
rtk make local-spark-comet-run SPARK_COMET_BUILD_TOKEN=<comet-build-token>
rtk make test-local-spark-comet
```

Preparation accepts no URL, version or caller-supplied checksum. It checks the
fixed 87,971,336-byte JAR against its pinned SHA-256 and published SHA-1, checks
ZIP CRCs, and verifies the embedded AArch64 ELF header. The detached signature
is retained but has not been cryptographically verified. Private preparation,
build and run evidence remains under `tmp/local-experiment/spark-comet-*`.

Building uses the verified existing image, one `COPY` layer, no `RUN`, no
network and no pull. It preserves prior images and verifies the base layers,
config, artifact bytes, generated job and source identities on every reuse.
The preparation JAR and its build-context copy each consume about 84 MiB;
the new image layer is additional storage. Receipts record exact byte counts.

The classpath job has one CPU, 2 GiB memory, a read-only filesystem, no network
or host mounts, UID 185, a bounded executable temporary filesystem, 512 MiB
JVM heap and 256 MiB off-heap memory. It creates 32 disposable Iceberg rows,
checks fresh row and group results against exact oracles, saves executed plans
and checks native scan and aggregation operators. It then drops its UUID-bound
table and removes only its verified owned container.

An uncertain or failed attempt is retained. Scoped cleanup can be requested
without rerunning its workload:

```sh
rtk make local-spark-comet-recover SPARK_COMET_RUN_TOKEN=<saved-run-token>
```

Recovery records separate evidence and rechecks ownership before any stop or
remove. It cannot adopt a same-name replacement. No host-global configuration,
cache eviction, service installation or external publication is performed.

## Observed local acceptance

On 2026-09-20, run `cfa8d266debc4e39ae44163f2e70deed` passed all nine checks
with Comet 1.0.0, Spark 4.1.3, Iceberg 1.11.0, Scala 2.13.17 and Java 17 on
`aarch64`. Both fresh reads used `CometIcebergNativeScanExec`; grouped reads
also used `CometHashAggregateExec`. The job exited zero and its owned container
was removed. The generated Range write used Spark fallback, which is retained
in its diagnostic log; it is outside the native read acceptance.

This establishes native execution over the container's local Iceberg table.
No performance improvement, full TPC-H score, Gluten compatibility or
production readiness is inferred from this gate.

## Same-snapshot remote comparison

The separate comparison entrypoint is:

```sh
rtk make local-spark-comet-benchmark SPARK_ICEBERG_BUILD_TOKEN=<verified-base-build-token> SPARK_COMET_BUILD_TOKEN=<comet-build-token>
```

It prepares one owned Iceberg table from the fixed nine-column TPC-H sf0.1
lineitem source. The existing bounded streaming Decimal oracle verifies the
complete dataset and seals its UUID, snapshot and file inventory. Stock Spark
then Comet run as separate applications, each making four observations of
Q1-derived sums and Q6. Both read that same snapshot and must return the exact
oracle values and types. The stock application's namespace must be absent
before Comet starts; the table seal is rechecked between applications.

Both variants use one driver and one executor with one CPU and 2 GiB limits
per pod, 1 GiB heap, 768 MiB overhead and 256 MiB off-heap memory. Query timing
includes `spark.sql` through `collect`; oracle checking, plan collection,
executor probes and resource cleanup occur outside that interval. The eight
stock observations precede all eight Comet observations. Source preparation
and earlier reads can warm shared caches, so first-observed is not cold-cache
and repeat medians are descriptive. The result does not rank engines in
general or meet the full TPC-H benchmark rules.

Every Comet query must show native Iceberg scan and aggregation in its executed
plan. JVM fallback elsewhere remains visible in retained explain output.
Credentials remain in the existing mounted profile files and scoped runtime
memory, with redaction before diagnostic persistence. The native Rust storage
client is tested separately from the successful local-file gate above.

Private evidence uses `spark-comet-benchmark/<token>/` with `stock-native` and
`comet-native` children. The normal path drains readers and owned queries,
removes temporary access and drops only the same owned table UUID. It verifies
the retained fixture and table inventory before and after. Existing images,
receipts, backups and unrelated data remain retained.

For an interrupted variant, this command only attempts verified Kubernetes
resource cleanup and saves a separate recovery receipt:

```sh
rtk make local-spark-comet-benchmark-recover SPARK_COMET_BENCHMARK_TOKEN=<saved-token> SPARK_COMET_VARIANT=<stock-or-comet>
```

It does not rerun queries, resend uncertain catalog writes or complete the
outer table/access cleanup. Those outcomes remain in the original receipt for
scoped inspection.

On 2026-09-20, comparison `922b6d777da849f1a95674e62f4d24b3` passed in
218.856 seconds including preparation and cleanup. All 16 observations over
600,572 rows matched the exact oracle. The table UUID was
`5e390d20-f086-47e9-8b09-d052230c0ada`, snapshot `8732990294717971534`.
All eight Comet observations used native Iceberg scan and aggregation with
the existing mounted AWS profile. Both applications passed all seven checks
and closed their distinct namespace UIDs before the final same-UUID table
drop. Temporary access cleanup, query drain and retained fixture/table
inventory checks passed.

| Query | Stock first (ms) | Comet first (ms) | Stock repeat median (ms) | Comet repeat median (ms) |
| --- | ---: | ---: | ---: | ---: |
| Q1-derived sums | 6,266.635 | 5,191.452 | 688.041 | 437.773 |
| Q6 | 465.781 | 312.891 | 195.601 | 197.370 |

These are one fixed-order local trial's observations; the repeat median uses
three samples. The Q1-derived result was lower with Comet, while Q6 was nearly
equal. No randomized-order or cold-cache claim is made. The raw nanosecond
values, result hashes, all sixteen executed plans and exact cleanup identities
are retained in the comparison receipt.
