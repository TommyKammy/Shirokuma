# Spark–Iceberg isolated classpath gate

Verify the official Spark 4.1.3 / Iceberg 1.11.0 Java 17 combination before
connecting Spark to retained Polaris or SeaweedFS data. This extends the native
Spark increment in WP-L4-SPARK-001 under the approved local experiment mode.

```sh
rtk make test-local-spark-iceberg
rtk make local-spark-iceberg-prepare
rtk make local-spark-iceberg-build SPARK_ICEBERG_PREPARE_TOKEN=<prepare-token>
rtk make local-spark-iceberg-run SPARK_ICEBERG_BUILD_TOKEN=<build-token>
rtk make local-spark-iceberg-recover SPARK_ICEBERG_RUN_TOKEN=<run-token>
```

Preparation downloads only the fixed `iceberg-spark-runtime-4.1_2.13` and
`iceberg-aws-bundle` 1.11.0 JARs from Maven Central. It checks both published
SHA-512 and SHA-256 against source pins, exact sizes, ZIP CRCs and required class
headers. The two files total 111,572,756 bytes. PGP signatures are saved but not
verified; TLS checksum agreement is not a signature-verification claim.
Duplicate packaged LICENSE/NOTICE entries are recorded with their individual
CRC/occurrence. Duplicate code entries and unsafe paths are rejected.

The builder starts from the already verified official Spark ARM64 digest and
copies only the two JARs plus the fixed script. It uses no build network, no
package installer, no image publication and no replacement of existing tags.
The image tag is derived from all build inputs. Reuse requires fresh validation
of inputs, image identity, configuration and the unchanged base filesystem
layers. The image stays cached for subsequent experiments.

## Execution and recovery

The fixed script runs Spark `local[1]` in a disposable Docker container with
network `none`, 1 CPU, 2 GiB memory, 256 PIDs, UID/GID 185, dropped capabilities,
no privilege escalation, a read-only root, and bounded tmpfs storage. It mounts
no host folders or volumes and exposes no ports. No credentials enter it.
The 512 MiB `/tmp` explicitly allows execution for bundled Zstd/Netty JNI
libraries; Docker otherwise mounts tmpfs with `noexec`. The 64 MiB work directory
remains `noexec`. A fixed `localhost` hostname resolves inside the network-free
container without DNS.

Inside `/tmp`, a fresh HadoopCatalog table receives 32 deterministic rows.
A new Spark session reads the table through both DataFrame and SQL paths and
checks every row, aggregate counts/sums and the metadata UUID/snapshot. The
script drops only the same UUID/table location before reporting success.
Exit 0 and a valid passed result are both required. The gate identifies missing
classes, ABI mismatch, read/write and cleanup failures separately; a partial
write is not accepted as interoperability.

Private receipts, bounded logs, build contexts and full table metadata identity
are saved under `tmp/local-experiment/spark-iceberg-{prepare,builds,runs}/`.
The CLI prints a result and evidence path, without table metadata details.
Container cleanup checks the recorded full container ID, image, labels and
resource/security settings. An unknown create outcome is preserved for
inspection, not adopted by name. Recovery only removes the known container;
it does not repeat the data operation or overwrite the original receipt.

The dataset exists only in container tmpfs; deleting the owned container removes
it. Image/JAR caches and diagnostic evidence remain. This increment has no
retained-data backup or restore operation and never resets Colima.

## Evidence boundary

This is a single-process classpath and local-filesystem compatibility check.
Loading the AWS classes does not verify S3 authentication or connectivity.
Kubernetes distributed Iceberg execution, Polaris/SeaweedFS access, cross-engine
Trino readback, benchmark performance and production acceptance remain separate
work. No model requests are made. A reported Spark-only classpath failure in
Iceberg 1.11.0 is the reason for this gate; it is tested rather than assumed to
apply to every environment.

- [Official Iceberg releases and artifacts](https://iceberg.apache.org/releases/)
- [Spark integration](https://iceberg.apache.org/docs/latest/spark-getting-started/)
- [Reported classpath issue 16856](https://github.com/apache/iceberg/issues/16856)

Actual results and retained-state checks are recorded in the WP and local
experiment milestones after the gate runs.

The next bounded integration has a separate
[native Spark write → Trino readback contract](REMOTE.md), including temporary
Polaris access, static S3 credential limits and cleanup/recovery boundaries.
That remote flow preserves an empty Trino-created initial snapshot when present
and requires the Spark append to point to that exact parent snapshot.
