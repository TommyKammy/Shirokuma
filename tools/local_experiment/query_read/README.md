# Local Lakehouse reads

These product commands browse the existing local Iceberg catalog without an
LLM, arbitrary SQL or table mutations. The existing Query Doctor `run` and
`status` commands remain separate.

```sh
shirokuma query tables --schema smoke
shirokuma query tables --schema local_experiment
shirokuma query describe --table smoke.fixture_v1
shirokuma query select --table smoke.fixture_v1 --columns id,name --limit 10
rtk make test-local-query-read
```

Use `--repo-root` when invoking the binary outside this checkout. Only the
`local-experiment` profile is accepted; the existing dedicated Python runtime
and local services must already be available. Nothing is installed or started
as a persistent service.

Each read discovers current BASE TABLE names in `iceberg.smoke` or
`iceberg.local_experiment`. Describe/select also compare Trino's current schema
with Polaris metadata, including table UUID and current snapshot. Select accepts
only discovered scalar columns and fixes `FOR VERSION AS OF` to that snapshot.
Schema and metadata must still match afterward. There is no arbitrary SQL,
expression, function, join, write or caller-provided endpoint. A separate
[typed equality profile](../query_filter/README.md) accepts the paired
`--where-column` / `--equals` flags, including with `--snapshot-id`. Reads without
these flags preserve the existing v1/v2 request and result formats.
For an exact aggregate without returning rows, use
[`query count`](../query_count/README.md). It has no row LIMIT and may scan the
selected snapshot under the existing timeout; it does not change select behavior.

Inventories are bounded at 100 tables, schemas at 32 columns and selections at
16 columns and 100 rows. One extra row detects truncation. A complete response
with `truncated: true` means the requested row limit was reached; it does not mean
the entire table was read. Row ordering is unspecified. Unsupported or oversized
data, incomplete responses and changing metadata do not become successful
observations. A table with no current snapshot can be described but not selected.

Output is JSON. Values include their scalar type; integer, decimal, floating
point and snapshot values use strings so ordinary JSON consumers cannot round
them silently. Boolean, null, date and varchar retain explicit types. Decimal
values are decoded without a binary floating point conversion. Nonfinite
numbers and complex columns are rejected. Each string cell is limited to 4 KiB;
the complete public event is limited to 256 KiB.

Rows are returned only for an explicit select and after metadata checks, owned
query drain and tunnel cleanup succeed. They are not written into the default
receipt. Private evidence under `tmp/local-experiment/query-read/<token>/`
contains request metadata, schema/identity projections, result hash/counts,
query identities and terminal/cleanup evidence. SQL text, upstream error text,
credentials and raw selected rows are excluded. A caller may separately choose
to save stdout; the evidence policy does not erase such output.

The initial `allocated` event identifies the private run. The final result is
withheld until the matching Python child exits successfully. Each POST is sent
at most once; an unknown result is not automatically repeated. Cancellation
signals the owner and waits for bounded cleanup, retaining an unknown outcome
if completion cannot be established. A token directory is never reused, and
the read workflow has no automatic resume command. Invalid syntax is rejected
before discovery; an absent table or column requires discovery queries before
the requested data SELECT can be refused.

This is host-local experimental access using the existing lab credentials.
Distinct query user/source values support provenance and scoped cleanup; they
do not establish tenant authorization. Retained tables, grants, data and backups
are not changed. These commands do not claim performance improvements, general
query diagnosis, concurrent-writer guarantees or production readiness.
