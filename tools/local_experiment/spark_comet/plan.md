# Local Comet increment

Implementation record for WP-L4-SPARK-002, 2026-09-20. The first gate is a
network-free `local[1]` container with 32 disposable Iceberg rows. A passing
classpath gate does not establish remote S3/catalog compatibility or speedup.

## Fixed input

- Spark 4.1.3 / Scala 2.13 / Java 17 / Iceberg 1.11.0, using the existing verified
  Spark/Iceberg image, then one offline layer containing the Comet JAR and job.
- Comet `org.apache.datafusion:comet-spark-spark4.1_2.13:1.0.0`:
  [fixed Maven URL](https://repo.maven.apache.org/maven2/org/apache/datafusion/comet-spark-spark4.1_2.13/1.0.0/comet-spark-spark4.1_2.13-1.0.0.jar),
  87,971,336 bytes; SHA-256
  `fc47c35419c20c38743ad5752ae3ae32e7a167923be628878d9caa22f41d6b67`;
  published SHA-1 `ffe250a1a9087c7211a7e7b209b11ca9f1e91fbc`.
- Maven publishes SHA-1 and a detached signature, but no JAR SHA-256/SHA-512.
  The SHA-256 above was computed from the controlled first download. The
  detached signature is preserved; cryptographic signature verification has
  not been performed. Do not substitute the source-tarball SHA for the JAR.
- Official source tag `1.0.0` resolves to
  `3a7a2c437cc771621b6040a308657573dbc1b9c2`. The bundled native resource is
  `org/apache/comet/linux/aarch64/libcomet.so`; require ELF64, little endian,
  ET_DYN, EM_AARCH64 (183), ZIP CRC and the fixed artifact digest.
- The published native binary targets `neoverse-n1`. ELF architecture alone
  does not establish instruction compatibility with the Colima guest on this
  Mac. Actual native query execution is required. [Pinned installation source](https://github.com/apache/datafusion-comet/blob/3a7a2c437cc771621b6040a308657573dbc1b9c2/docs/source/user-guide/latest/installation.md)

## New classpath gate

| File | Responsibility / API |
| --- | --- |
| `prepare.py` | Fixed URL, bounded private download and archive inspection; `prepare()` and `verified_artifact(token)`. Failed attempts are retained, never reused as verified input. |
| `build.py` | `build(prepare_token, base_build_token)` and `read_verified_image(token)`. Reuse the existing verified base; `--pull=false --network=none --platform linux/arm64`; no RUN step, image push, or workload launch. |
| `contract.py` | Registered fixed job, exact oracle, safe output and native plan validation. No caller SQL or code. |
| `run.py` | `run --build-token TOKEN`, `recover --run-token TOKEN`; same bounded Docker ownership/UID checks as the existing Iceberg gate, a distinct component/name/state directory. |

The fixed job reuses the existing Iceberg table owner, 32-row oracle, fresh
reads and UUID-bound drop. It enables Comet before SparkContext startup, uses
512 MiB JVM heap plus a 256 MiB off-heap pool inside the existing 2 GiB container
limit, and leaves the root filesystem read-only. The existing executable `/tmp`
tmpfs accommodates JNI extraction; no host directory or network is mounted.

For the row and grouped queries, save executed physical-plan node classes and
parent indexes, a canonical structural SHA-256, raw plan SHA-256/text, and
extended explain output. Require native `CometIcebergNativeScanExec` for rows
and both that scan and `CometHashAggregateExec` for groups. JVM-only
`CometBatchScan`/`CometColumnarExchange` are insufficient. Keep fallback explain
enabled. [Pinned plan guide](https://github.com/apache/datafusion-comet/blob/3a7a2c437cc771621b6040a308657573dbc1b9c2/docs/source/user-guide/latest/understanding-comet-plans.md)

## Remote benchmark integration

After the classpath/native-query gate, reuse `spark_benchmark.workload`
unchanged: same sf0.1 lineitem source, Decimal oracle, Q1 sums/Q6, one first
sample plus three repeats per query. Reuse `HostTable`, `Client`, `RemoteAccess`
and query drain; no Trino timing pass is required for the new Spark comparison.

Run stock Spark first with the existing image, then Comet with the new image, as
two distinct Spark applications over exactly one sealed table UUID/snapshot.
Keep the same outer token/table/Polaris role/ingress scope; use fresh
`stock-native` and `comet-native` evidence subdirectories. Pass each directly to
`spark.run.Experiment`; do not call `output_directory(token)` twice. The first
application's namespace UID cleanup and absence plus table seal equality must
be verified before recreating that namespace for the second application. This
keeps the existing access-token/namespace binding and a peak of two pods.

The fixed `iceberg-comet-benchmark` profile uses these narrow integration
points in `spark/run.py`: remote-contract allowlist, profile-specific verified
image dispatch, and source hashes.
Existing profile image and input contracts remain unchanged. The remote contract
binds a fixed `stock`/`comet` variant to its corresponding build proof,
rendered pod image, Spark configuration, job and result. It reuses the
benchmark seal/result validators; existing source-token and cleanup contracts
remain unchanged. The variant recovery adapter
validates the saved plan/UIDs before invoking the existing cleanup; the
old native recover CLI only searches its own fixed state directory.

Match total pod memory budgets. Spark adds executor off-heap memory to heap and
overhead when deriving the pod request: do not simply add 256 MiB to today's
1 GiB heap + 1 GiB overhead while still expecting a 2 GiB pod. The
common budget is 1 GiB heap + 768 MiB overhead + 256 MiB off-heap for both new
variants, with the existing 2 GiB limit verified against actual pod specs.
Keep AQE off, one executor, one task attempt, bounded spills and the existing
network policies. Report fixed execution order and shared/warm cache effects.

Native Iceberg scan uses its own Rust storage client. The current remote job
passes AWS credentials as a mounted profile file and uses Java ProfileCredentialsProvider.
The actual remote native scan also succeeded with that file-only credential
path. Secret values are not embedded in persisted Spark config.
If a native path is blocked, preserve the precise failure and report the WP's
allowed documented blocker; do not silently count JVM fallback as acceleration.
[Iceberg native storage documentation](https://datafusion.apache.org/comet/user-guide/1.0/iceberg.html)

## Acceptance and ownership

Focused fakes cover changed bytes/base layers, foreign containers, unknown
create outcomes, first cancellation, malformed or JVM-only plans, and original
receipt retention. Root alone performs artifact preparation, build and actual
container/Kubernetes runs. After native execution, compare 16 fixed query
measurements against the same exact oracle and retain plan/fallback observations.
Drop only the owned table after both Spark applications and all queries are
closed; preserve the original table inventory and fixture projections.

No Gluten, x86/Rosetta, resident service, host-global configuration, external
registry publication, standard TPC-H score, or general performance claim is
part of this increment. Disk accounting includes the new JAR, private build
context and single new image layer while retaining prior images/evidence.
