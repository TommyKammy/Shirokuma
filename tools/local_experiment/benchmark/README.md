# Local TPC-H SF10 functional benchmark

This is the local functional slice of `WP-L1-BENCH-001`. It generates the full
TPC-H scale factor 10 dataset (10 GB nominal uncompressed source scale) through
the Trino 483 TPC-H connector, persists all eight tables as Iceberg Parquet data
in Polaris/SeaweedFS, and checks all 22 pinned Trino TPC-H queries against the
generator reference. It is not an official TPC-H performance result.

The existing ARM64 Trino experiment image must contain `plugin/tpch` from the
same pinned Trino 483 distribution as `plugin/iceberg`. Its `tpch` catalog uses:

```properties
connector.name=tpch
tpch.column-naming=SIMPLIFIED
```

The connector is only a deterministic in-memory generator; generated persistent
data lives in the existing `iceberg.local_experiment` namespace. No schemas,
grants, retained fixtures, or other workloads are modified by the harness.

With a loopback Trino port-forward available, run from the repository root:

```sh
rtk proxy python3 tools/local_experiment/benchmark/run.py --scale-factor 10 --query-timeout 900
```

`--url http://127.0.0.1:<port>` selects a different loopback port. The default
is port 18080. `--query-timeout` bounds each SQL statement (including CTAS) on
both the client and server; the allowed range is 10–3600 seconds. The client
disables proxies and redirects, bounds result bytes, and assigns an isolated
user identity to the invocation. No external Python packages are required.

The preflight records the running `mac-studio-solo` Colima CPU/memory/disk
profile, the single ARM64 Kubernetes node, and the ready Trino image digest and
resource requests/limits. It requires 50 GiB free on both the host and the VM
filesystem backing k3s local storage before creating any table. This is a
conservative experiment budget, not an estimate of Parquet compression.

The harness checks these exact SF10 row counts in both the generator and
Iceberg, then checks the Iceberg data-file record totals and bytes:

| Table | Rows |
| --- | ---: |
| region | 5 |
| nation | 25 |
| supplier | 100,000 |
| customer | 1,500,000 |
| part | 2,000,000 |
| partsupp | 8,000,000 |
| orders | 15,000,000 |
| lineitem | 59,986,052 |

Query inputs come from the official Trino 483 repository at commit
[`50b0b50b75abd47f830b7805ee1b51716eb4065e`](https://github.com/trinodb/trino/tree/50b0b50b75abd47f830b7805ee1b51716eb4065e/testing/trino-benchmark-queries/src/main/resources/sql/trino/tpch).
`query_inputs.json` records the source URL, original hash/size, and vendored
hash/size for every input. Only trailing whitespace is normalized. The included
`LICENSE.trino` retains the upstream Apache 2.0 license. The harness verifies
the vendored hashes before loading data. Runtime substitutions set
database, schema, table prefix, and scale 10, including Q11's scale-dependent
threshold. Upstream fixed parameters and result-stability adaptations remain.
One additional, explicitly recorded Q15 adaptation casts extended price to
`DECIMAL(15,2)` and discount to `DECIMAL(3,2)` before computing revenue, identically
on generator and Iceberg. The upstream file/checksum is preserved. Trino
[inlines repeated WITH references](https://trino.io/docs/483/sql/select.html#with-clause);
the original DOUBLE aggregation can give unequal rounded sums inside its exact
`total_revenue = MAX(total_revenue)` predicate. On this SF10 source, identical
original Q15 executions returned zero then one row, while a diagnostic observed
`2194132.8166000005` versus `2194132.8165999996` for the same winning supplier.
Exact decimal arithmetic preserves the intended two-decimal input domain and
the original equality predicate; it does not relax comparison tolerances.
The source-only proof is retained at
`tmp/local-experiment/benchmark/q15-diagnosis.json`.
The [official connector documentation](https://trino.io/docs/483/connector/tpch.html)
describes the generator and column naming behavior.

Each query first runs against `tpch.sf10`, then against the eight generated
Iceberg tables. Output comparison uses unordered multisets, retaining duplicate
rows and exact integer/text values. Floating point aggregation uses relative
tolerance `1e-8` and absolute tolerance `1e-6` to allow different summation order.
Both results must carry a complete column-type array. Types must match exactly,
with one recorded storage mapping: TPCH `varchar(n)` becomes Iceberg `varchar`
because the [Iceberg type mapping](https://trino.io/docs/current/connector/iceberg.html#type-mapping)
uses STRING and does not preserve the length parameter. For that mapping,
both sides must contain only strings/nulls within the original bound; each
mapped column is listed in comparison evidence. Numeric, boolean and text types
are never treated as interchangeable, even when Python considers their values
equal or can convert their strings to numbers.
This tests storage-path agreement using the same SQL engine; it is not an
independent specification oracle or a certification of SQL ordering semantics.

Evidence persists under ignored `tmp/local-experiment/benchmark/<run-token>/`:

- `summary.json`: phase checkpoints, exact inputs, environment, table counts,
  file bytes, per-query timing/metrics, comparison results, and cleanup outcome.
- `qNN-reference.json` and `qNN-iceberg.json`: complete result rows, query IDs,
  wall time, and available Trino query metrics for each query pair, persisted
  before comparison so failures remain diagnosable.
- `qNN-reference.sql` and `qNN-iceberg.sql`: exact executed SQL; the summary also
  records their hashes and the Q15 runtime adaptation.
- `run.lock`: excludes a concurrent recovery operation for the same token.

The final report passes only after all eight full tables and all 22 query pairs
pass, the retained fixture still contains its original two rows, and cleanup
has succeeded. Query timings are single observations without cold-cache control.
Generator execution, client, query engine, catalog, and object storage share one
Mac/VM. Reference timings also include data generation. These observations are
useful for repeated local relative comparisons only; they do not establish
production capacity, an SLA, vendor superiority, or official TPC throughput.

Every invocation uses a fresh UUID table prefix and an exact ownership comment
embedded in `CREATE TABLE`. Initial name collisions stop before mutation.
Cleanup first cancels only the invocation's outstanding query IDs and proves
they are terminal, then checks the comment before dropping each of the eight
possible table names. If query drain cannot be verified, DROP is deferred and
the summary records the token and pending relations. An unmarked replacement
is preserved. After a failure, recover only that recorded run with:

```sh
rtk proxy python3 tools/local_experiment/benchmark/run.py --cleanup-run <run-token>
```

Recovery writes `recovery.json`; it never converts the original failed benchmark
into a pass. It refuses an active run lock. Successful DROP uses the existing
Polaris purge configuration to remove the generated table's data objects.
If CTAS fails before catalog commit, SQL cleanup cannot enumerate its uncommitted
objects or multipart uploads. Preserve the failure log, verify run queries are
terminal, and inspect only the exact generated table prefix identified in that
log. Abort multipart uploads only when their keys match that run/query prefix;
record the listing, removals and empty readback. Do not infer an empty object
prefix from an empty SQL namespace. A 2026-09-16 SeaweedFS OOM interrupted lineitem
CTAS: seven registered tables were purged and fifteen incomplete uploads were
separately aborted; receipts remain in the failed run
`a1937e119ee6499396032eb73b6d834e`. Its original failure is retained.
SeaweedFS volume allocation can remain on disk after object deletion, so an
empty namespace does not mean the host/VM filesystem immediately shrank. Inspect
the recorded file-byte totals and VM free space; do not delete retained volumes
or run broad storage compaction as benchmark cleanup. Generated data is
reproducible and needs no backup; the retained fixture and unrelated tables are
never cleanup candidates.

SF100 is optional future work and is deliberately not accepted by this SF10
entrypoint. It needs a separate measured disk/memory budget and explicit scale
configuration before execution; no 100 GB completion is implied by SF10.

Focused verification:

```sh
rtk proxy python3 -m unittest discover -s tests -p 'test_local_tpch.py' -v
```
