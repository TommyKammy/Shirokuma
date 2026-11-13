# Local P0/P1 qualification toolkit

Local-only preparation under ADR-0036. Python 3.9+ runs the stdlib suite;
generation/reference execution uses a separate Python 3.12 venv and **DuckDB
1.4.3**, pinned in `requirements.txt`. No pandas/Arrow, public API contract changes,
workload changes, VM application, cloud execution, or global Python installation.

This initial toolkit prepares TPC-derived data and reference results and runs
sequential qualification. It does not establish official compliant TPC scores,
an independent oracle, final SLO success, or complete G1/P1 acceptance.

## Setup and focused checks

Run from the repository root. The environment is ignored by Git. Wheels exist
for macOS arm64 and Linux aarch64; this implementation was exercised on macOS
arm64, not Linux. Ordinary Make tests never install/download extensions.

```sh
rtk proxy uv --cache-dir "${TMPDIR:-/tmp}/local-perf-uv" venv --python tools/local_experiment/dataops/.venv/bin/python3.12 benchmarks/local_perf/.venv
rtk proxy uv --cache-dir "${TMPDIR:-/tmp}/local-perf-uv" pip install --python benchmarks/local_perf/.venv/bin/python -r benchmarks/local_perf/requirements.txt
rtk make test-local-perf
rtk make qualify-local-perf
```

If uv cannot bootstrap on the host, use this isolated fallback:

```sh
rtk proxy tools/local_experiment/dataops/.venv/bin/python3.12 -m venv benchmarks/local_perf/.venv
rtk proxy benchmarks/local_perf/.venv/bin/python -m pip install --no-cache-dir -r benchmarks/local_perf/requirements.txt
```

`qualify-local-perf` explicitly loads signed core extensions, creates a fresh
temporary campaign outside Git, generates SF0.01, checks every exported table's
schema and full duplicate-preserving contents with bidirectional `EXCEPT ALL`,
prepares all 22 query references, then executes/compares all 22 again. It retains
the printed artifact directory, including FAILED metadata on exceptions.
References are same-engine Parquet roundtrips, **not independent verification**.

The stdlib suite keeps scripted transport/lifecycle tests separate from real
loopback fake-server tests. The Commander host runs on 2026-11-14 passed 123 of
126 discovered tests on both Python 3.9.6 and 3.12.12, with three explicit real-data
tests skipped. Ten real loopback cases cover public context/auth/pages, safe HTTP
rejection receipts, slow-drip status/headers, incomplete Content-Length and query
lifecycle. Dedicated Parquet export and TPC-H parity checks separately passed all
23 tests. Fresh-cache/install failures are also mocked
without network in the ordinary suite. Real generation remains an explicit step.

Focused review regressions (stdlib, no downloads):

```sh
rtk proxy python3 -m unittest tests.test_local_perf_corrections tests.test_local_perf_recovery -v
rtk proxy python3 -m unittest tests.test_local_perf_http.HTTPTests.test_absolute_slow_header_deadline tests.test_local_perf_http.HTTPTests.test_incomplete_content_length -v
```

The corrections module covers signed-cache/fresh-core bootstrap, failure cleanup,
SF1000 evidence-only planning, JSON resource reserves, multi-volume rejection,
scripted absolute deadline and incomplete/oversize response, SQL snapshot/reference
identity, fail-fast denominator, cancel/reconcile ownership and immutable audit,
integer/decimal ranges, GROUP BY deadline/capacity interruption with preserved
cause, and all-22 typed packaged-answer parsing/provenance. Mocked tests do not
establish real transport, generation, or packaged-answer qualification success.
The recovery module replays persisted INTENT/RUNNING cutpoints, terminal evidence
reuse, UNKNOWN retry without POST, and rejection of external DuckDB reconciliation.
Packaged numeric blank fields are NULL rows; empty VARCHAR remains an empty string.
After verifying all 22 canonical extension SQL hashes, the known-answer codec may
bind a bare SUM/AVG/MIN/MAX/COUNT label to the same unaliased aggregate expression
at that position (case-insensitive aggregate name). Column count/order and other
labels stay exact. Each query records its packaged/result `column_binding` in
qualification metadata; ordinary typed result schema comparison remains strict.
Commander qualification (2026-11-13) passed at both SF0.01 and SF0.1: each
generated 8 tables and 22 references, passed all 22 packaged answers (including
Q17 NULL and Q18 aggregate binding), and passed all 22 re-execution comparisons.
Allocated Parquet bytes were 2,150,400 and 21,880,832 respectively. These are
preparation checks on DuckDB, not Shirokuma product performance measurements.

## Campaign and capacity planning

`protocol.json` is versioned, local-only and freezes 2,000,000,000,000 bytes of
additional physical allocation **and** maximum estimated logical data. Profiles
are validation/planning only; the frozen next target is **L1**: 24 CPU/192GiB VM,
16 CPU/128GiB worker, leaving 8 CPU/64GiB for the shared VM baseline. Host RAM
reserves are OS 64GiB, other VM 8GiB, generator 8GiB, summed from JSON. `protocol --profile` separates dated actual
observations from requests and shows bounded resources remaining outside worker
and VM. L3 and L3-extra require later resource review. No command applies them.

```sh
rtk proxy python3 -m benchmarks.local_perf protocol --profile L1
rtk proxy python3 -m benchmarks.local_perf init "${TMPDIR:-/tmp}/local-perf-example"
rtk proxy python3 -m benchmarks.local_perf preflight "${TMPDIR:-/tmp}/local-perf-example" --additional-bytes 268435456
```

Use a **fresh name** if the example campaign exists. `init` captures its baseline
exactly once; reopening it never resets usage. Add caller-provided `--image` and
`--host-path` paths at init for VM image/snapshot or other host allocations.
Paths must be disjoint and on the same host filesystem; multi-volume accounting
is rejected because a single host-free baseline cannot represent it. Measure image allocation by `st_blocks` where available,
not apparent sparse size. Guest Parquet/spill is already contained inside the VM
image; never add guest paths to host accounting. Count retained host snapshots
explicitly as separate disjoint paths. APFS shared extents may be conservatively
overcounted by block accounting. Host free is checked independently throughout.

Budget is bounded by both the immutable baseline free minus 256GiB and current
free minus reserve, with a separate cancellation/drain allowance (default 64MiB
for tiny qualification). Increase `--drain-bytes` before a larger campaign to
cover its in-flight growth. Estimates include source DB, export/copy, spill,
validation sort and output, with conservative initial total of six logical
bytes per estimated logical byte, at least 256MiB. This is a guard estimate, not
a compression measurement. Raise it when pilot evidence requires; do not treat
it as proof that a proposed scale fits. Scale denotes approximately decimal GB
per SF; this is a declared nominal logical estimate. Generation also measures
UTF8 SQL text payload (non-null values plus per-column delimiters/newlines),
records this distinct logical definition and enforces the mode's logical ceiling
against it. The 2TB physical ceiling remains hard regardless of compression.

For example, this API call validates a plan only (no data generation):

```python
settings("performance", scale=1000, peak=1_500_000_000_000, timeout=900,
         repetitions=1, peak_evidence="pilot artifact and measured source/export/spill/result breakdown; extrapolation plus headroom")
```

Replace that illustrative text with actual pilot evidence before any execution.
Guest readiness is separate. `preflight --guest-snapshot path.json` accepts:

```json
{"data":{"total":429496729600,"free":157018820608,"additional":200000000000},
 "root":{"total":21474836480,"free":16800000000,"additional":0}}
```

It reports required reserve `max(64GiB,10%)` for data and `max(4GiB,10%)` for
root plus shortfall. Without a caller snapshot, guest readiness is unknown.
Host free never claims the current 400GiB guest can hold a TB dataset. Refresh
actual host/guest snapshots and inode availability before later VM work.

The `Campaign.check(snapshot=...)` Python API accepts measured allocations for
exactly the registered host paths plus current host free; it does not rebase.
Campaign operations are sequential; do not share a campaign between generators.

## Dataset and SQL generation

```sh
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf generate "${TMPDIR:-/tmp}/local-perf-example" tpch-small --suite tpch --scale 0.01
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf prepare-reference "${TMPDIR:-/tmp}/local-perf-example" "${TMPDIR:-/tmp}/local-perf-example/tpch-small" reference
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf run "${TMPDIR:-/tmp}/local-perf-example" qualification "${TMPDIR:-/tmp}/local-perf-example/tpch-small/queries.json" --adapter duckdb --dataset "${TMPDIR:-/tmp}/local-perf-example/tpch-small" --references "${TMPDIR:-/tmp}/local-perf-example/reference"
```

Generation defaults to qualification, SF0.01, 2 threads, 4GiB engine memory and
120s deadline. Qualification refuses nominal logical data above 1GB. Generation
uses a bounded on-disk temporary database, capped spill and streaming Parquet
COPY. Every table export/validation and long statement is capacity checked;
long statements are interrupted by a 200ms sampling thread. It is a sampling
guard, not a filesystem quota: declare realistic in-flight/drain reserve.
Only owned temporary DB/spill files are removed, including on error. Parquet,
SQL manifests and FAILED metadata remain available for inspection.

Dataset metadata records exact engine/extension versions, installed function
signatures, fixed defaults, nominal scale, schema, row counts, physical/file
bytes and SQL per query. Unsupported enumeration counts fail explicitly.
`--suite tpcds` uses `dsdgen` and enumerates all 99 SQL strings plus statement
counts; small generator support depends on the installed extension. All variants
inside a string are retained; no claim of full TPC-DS execution is made here.

Official docs: [TPC-H](https://duckdb.org/docs/2.0/core_extensions/tpch),
[TPC-DS](https://duckdb.org/docs/2.0/core_extensions/tpcds),
[pinned release](https://pypi.org/project/duckdb/1.4.3/).
The toolkit probes installed 1.4.3 APIs instead of assuming 2.0 behavior.
It reuses an installed official core extension read-only or installs from the
built-in core repository into owned output; unsigned/community extensions and
automatic extension loading are disabled. A missing network/cache is a failure,
never a fallback to fabricated data. TPC-H/TPC-DS substitution parameters are
fixed by the extensions; arbitrary query bind seeds are not exposed. DuckDB
data differs from fully audited TPC. DuckDB 1.4.3 bundles an older TPC-DS tool and is **not TPC-DS 4 compliant**.
No `tpcds_answers()` oracle is used: the installed function errors for missing
SF10 answers even when enumerated. The 99 SQL strings and all contained variants
remain enumerated with extension-default fixed parameters; enumeration does not
claim successful execution of every variant.

`qualify-packaged-tpch campaign dataset reference name` compares all 22 TPC-H
outputs at SF0.01/0.1/1 to `tpch_answers()` packaged precomputed answers. It parses
values using the result's frozen column types, compares decimals exactly and
floats with explicit abs/relative tolerance 1e-8, and retains a separate provenance
record and expected typed results. The dedicated `qualify-local-perf` smoke now
includes this check at SF0.01. These packaged answers are separate from regenerated
same-engine Parquet references; they do not establish an independent cross-engine
oracle. No real known-answer success is claimed from mocked lifecycle tests.

## Runner and full result comparison

Query manifest schema (generated manifests use this same interface):

```json
{"format":1,"dataset_id":"caller-frozen-snapshot","queries":[
  {"id":"scan","sql":"SELECT key, amount FROM example_table","ordered":false}
]}
```

Qualification conservatively accepts SELECT/CTE SQL only; `--allow-arbitrary`
explicitly selects general SQL. This lexical guard is not an authorization
boundary; use an isolated database and read-only credentials for qualification.
Adapters must use the same frozen dataset and equivalent SQL. Dialect/type
normalization beyond documented aliases must be reviewed, never silently coerced.

For a loopback Trino configured with the equivalent loaded snapshot:

```sh
rtk proxy python3 -m benchmarks.local_perf run "${TMPDIR:-/tmp}/local-perf-example" trino-qualification "${TMPDIR:-/tmp}/local-perf-example/tpch-small/queries.json" --adapter trino --url http://127.0.0.1:8080 --catalog iceberg --schema benchmark --references "${TMPDIR:-/tmp}/local-perf-example/reference"
```

This command does not load Parquet into Trino. Prepare the isolated snapshot
separately. For authenticated HTTPS use `--ca-file` and an explicit mode-0600
`--auth-file` containing exactly `username` and `password`. CA and hostname
verification remain enabled. Direct credentials must belong to the selected
database's execution identity, not a platform provisioner. The toolkit never
looks up operator secrets. Optional legacy Basic credentials come from
`--password-env ENVIRONMENT_VARIABLE_NAME`, never a CLI password. Every URL must
be the same configured loopback origin. Redirects,
foreign pages and URL credentials are rejected before sending credentials.
Backend exception bodies/auth are not written to artifacts or CLI errors.

Before dispatch the run freezes `queries.json`, SQL SHA256/dataset identity,
adapter identity and planned/pending counts. References with a different SQL
fingerprint fail before any dispatch. Fail-fast retains the full planned count
and `not_run_attempts`; completed attempt files are immutable audit records.
Each attempt registers ownership before fsyncing intent, then dispatches and saves
the query handle before consuming data. All pages, including empty pages, are consumed once; schema must
stay consistent. Success requires explicit FINISHED with no next page, no backend
error/truncation, and full validation within deadline. A single deadline timer interrupts the connected socket across status/header,
chunk parser and body reads; socket waits also use remaining deadline (maximum
5s each). The timer is canceled/joined on every exit. A separate bounded cancellation window
reconciles the engine query endpoint; cancellation failure stays UNKNOWN and
blocks all subsequent dispatch in the campaign. Any failure stops the run.
Response-loss POST is never resubmitted, including after restarting the tool.
Direct Trino attempts without handles remain UNKNOWN. Failed and
uncertain attempts remain in run counts and fresh outputs cannot overwrite them.

```sh
rtk proxy python3 -m benchmarks.local_perf inspect "${TMPDIR:-/tmp}/local-perf-example/qualification/run.json"
rtk proxy python3 -m benchmarks.local_perf reconcile "${TMPDIR:-/tmp}/local-perf-example/trino-qualification/1-0/attempt.json" --adapter trino --url http://127.0.0.1:8080 --catalog iceberg --schema benchmark
```

Reconcile verifies the owned campaign/run/attempt path and original adapter kind,
normalized origin and user/catalog/schema before
network access. It records `reconciliation.json` without rewriting the final
attempt audit. It cancels/inspects an existing handle against its original identity;
it never redispatches and never upgrades incomplete results to success. Matching
terminal reconciliation is retained without further HTTP; UNKNOWN can be inspected
again. External DuckDB reconciliation is refused because a fresh connection cannot
prove the original process stopped. A later
explicit run has its own attempt denominator. Complete results have typed JSONL
headers, every row and a mandatory count/digest/SUCCESS footer. Comparison checks
all values, not just hashes. Integers/decimals/dates/NULL are exact, Decimal
exponents normalize without precision loss, timestamp values normalize to UTC
with frozen schema precision (naive timestamps explicitly interpreted as UTC).
Floating tolerance is exact unless `--abs-tol`/`--rel-tol` is explicitly supplied.
Ordered comparison preserves order; unordered comparison preserves duplicate
counts using disk-backed SQLite with an 8MiB cache. Tolerant unordered comparison
uses disk-backed augmenting-path matching, which may be expensive for huge
all-float groups. Sort/matching remains under capacity/deadline checks.

`compare left.jsonl right.jsonl --campaign ...` also supports standalone full
comparison with owned temporary sort output. Comparison input metadata/schema
and columns must match; malformed/truncated streams and EOF without terminal
footer fail. Query time, validation time and total through validation are stored
separately. Data-cache state is recorded as unknown.

Performance mode requires explicit `--scale`, `--peak`, `--timeout`, and bounded
`--repetitions` (1..100); no implicit large load is provided. The frozen future
deadlines, concurrency 1/4/8/16/32/64, warmup, repetitions and block plan are
planning inputs. This runner remains sequential and does not generate a final
SLO verdict. Do not execute SF100+ until Commander reviews capacity/pilot evidence.
Current qualification timers include capacity checks and durable receipt IO
before dispatch. Send/queue/engine/first-row/last-row timing boundaries must be
instrumented separately before these records are used for performance comparison.

## Public API and portable connection qualification

`public` uses the normal `/api/v1/queries` interface, with Basic authentication
and `X-Shirokuma-Role`. Supply an explicit mode-0600 client JSON containing
exactly `username`, `password`, and `selected_role`. Alternatively,
`--runtime-profile path.json` reads only its absolute `client_auth_file`
reference; it does not load server credentials or infer an engine endpoint.
The default public URL is `http://127.0.0.1:8888`. The runtime profile's legacy
`sql_endpoint_url` is not proof of the logical engine's address.

Select one database and schema by ID or SQL name. A frozen connection retains
the public account, role, origin, max_rows, credential **path**, and all nine
resolved context fields: database/schema/warehouse IDs, SQL catalog/schema,
selected role, binding revision, generation, and grant revision. Later commands
cannot override that identity. Context captured outside the selector can instead
be supplied through `--context-file`; it must contain exactly those fields.

The portable examples use identical inline VALUES and explicit SQL casts across
DuckDB, direct Trino, and the public API. They exercise scalar types, NULL,
duplicates, empty results, aggregates, joins, windows, microsecond timestamps,
and 4,096 wide rows for pagination. They do not register tables or claim an
identical persisted Iceberg snapshot. Create a fresh Git-external campaign:

```sh
rtk proxy python3 -m benchmarks.local_perf init /tmp/local-perf-connections-unique
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf prepare-sql-reference /tmp/local-perf-connections-unique benchmarks/local_perf/examples/connection-qualification.json reference
rtk proxy python3 -m benchmarks.local_perf freeze-public-connection /tmp/local-perf-connections-unique/public.json --adapter public --auth-file /absolute/private/client-auth.json --database YOUR_DATABASE_ID --schema YOUR_SCHEMA_ID
rtk proxy python3 -m benchmarks.local_perf run /tmp/local-perf-connections-unique public benchmarks/local_perf/examples/connection-qualification.json --adapter public --connection-file /tmp/local-perf-connections-unique/public.json --references /tmp/local-perf-connections-unique/reference
rtk proxy python3 -m benchmarks.local_perf run /tmp/local-perf-connections-unique direct benchmarks/local_perf/examples/connection-qualification.json --adapter trino --url https://127.0.0.1:YOUR_OWNED_FORWARD_PORT --ca-file /absolute/private/trino-ca.pem --auth-file /absolute/private/database-auth.json --catalog YOUR_SQL_CATALOG --schema YOUR_SQL_SCHEMA --references /tmp/local-perf-connections-unique/reference
```

Replace every placeholder with verified local values. An owned tunnel must
remain open for the whole run and cancellation/reconciliation window. Stop only
that tunnel afterward and remove any temporary credential copy. Direct Trino
advertises `PARAMETRIC_DATETIME` to preserve timestamp precision; the public
service must preserve it on its own backend connection too. See the
[Trino client protocol](https://trino.io/docs/current/develop/client-protocol.html).
The SQL-only reference records its provenance and never fabricates `dataset.json`.

Commander live qualification passed the nine portable queries through both Trino
483 and the public API, each with 4,111 rows fully matching DuckDB. Overflow,
lost-response cancellation and role isolation were also checked; see the
[connection verification record](../../docs/guides/performance-connections-2026-11-13.md).

Public attempts persist request ID, worksheet ID, full context and read-only
mode before POST. All returned pages must retain these identities, backend ID,
schema and mapped states. Empty pre-schema waiting pages are accepted, while
duplicate receipts, foreign/cross-query page URLs, errors, truncation, missing
schema or missing terminal completion fail. Native column types remain in the
attempt audit; bounded VARCHAR is compared losslessly as VARCHAR. FINISHED alone
does not prove all requested results were fetched.

`max_rows` remains 1..10,000, default 10,000. The overflow example returns 10,001
rows and must fail as `PUBLIC_TRUNCATED`; use its own prepared reference and
run name. Never change it to `None` or bypass public authorization. Public
qualification is recorded only after full result comparison succeeds through
this adapter; direct-engine success never sets that flag.

If the initial POST response is lost, recovery searches public history for a
unique match of request/worksheet IDs, all context fields, exact SQL and mode.
Zero or multiple matches remain UNKNOWN and block dispatch; the POST is never
retried. An owned handle is inspected before cancellation. Verified terminal
state is retained, but incomplete result data is never upgraded to SUCCESS.
Public recovery uses the original frozen connection:

```sh
rtk proxy python3 -m benchmarks.local_perf reconcile /tmp/local-perf-connections-unique/public/types-0/attempt.json --adapter public --connection-file /tmp/local-perf-connections-unique/public.json
```

## Persisted TPC-H qualification

`prepare-ingest` streams a successful dataset owned by the same campaign into
UTF8 JSONL chunks of at most 4MiB. Decimal values use exact strings; NULL, empty
strings, dates, REAL and naive UTC timestamps retain their declared types.
`load-public` uses ordinary ingest preview/create and atomic append APIs, with
explicit schema hints and a fresh target prefix. The full encoded body must fit
the public 10MiB ceiling. All parts are checked before mutation. Empty JSONL
tables and unsupported types fail before submission.

Each chunk freezes its input digest, context, preview and idempotency key before
POST. A lost response is inspected by the existing public operation/key endpoints;
it is never blindly submitted again. Unresolved work blocks another load in the
campaign. Committed counts and cleanup evidence survive failures. Publication is
atomic **per chunk**, not across all chunks/tables. Partial data remains owned
and unmet; there is no implicit replace, deletion or adoption of an existing table.

The following uses a fresh, small campaign. Replace private paths, DB/schema IDs
and the unique prefix with verified local values. Include VM images, host state
and retained logs in disjoint same-filesystem accounting paths at `init`.

```sh
rtk proxy python3 -m benchmarks.local_perf init /tmp/local-tpch-unique --image /absolute/path/vm-root-image --image /absolute/path/vm-data-image --host-path /absolute/path/shirokuma-state --drain-bytes 268435456
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf generate /tmp/local-tpch-unique tpch --scale 0.01
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf prepare-reference /tmp/local-tpch-unique /tmp/local-tpch-unique/tpch canonical
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf qualify-packaged-tpch /tmp/local-tpch-unique /tmp/local-tpch-unique/tpch /tmp/local-tpch-unique/canonical known-answers
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf prepare-tpch /tmp/local-tpch-unique /tmp/local-tpch-unique/tpch portable
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf prepare-ingest /tmp/local-tpch-unique /tmp/local-tpch-unique/tpch inputs --prefix bench_unique
rtk proxy python3 -m benchmarks.local_perf freeze-public-connection /tmp/local-tpch-unique/public.json --adapter public --auth-file /absolute/private/client-auth.json --database YOUR_DATABASE_ID --schema YOUR_SCHEMA_ID
rtk proxy python3 -m benchmarks.local_perf load-public /tmp/local-tpch-unique /tmp/local-tpch-unique/inputs load --adapter public --connection-file /tmp/local-tpch-unique/public.json
rtk proxy python3 -m benchmarks.local_perf freeze-snapshots /tmp/local-tpch-unique /tmp/local-tpch-unique/load pins --adapter public --connection-file /tmp/local-tpch-unique/public.json
rtk proxy python3 -m benchmarks.local_perf bind-tpch /tmp/local-tpch-unique/portable/queries.json /tmp/local-tpch-unique/pins/snapshots.json /tmp/local-tpch-unique/tpch-bound.json
rtk proxy python3 -m benchmarks.local_perf table-manifest /tmp/local-tpch-unique/pins/snapshots.json /tmp/local-tpch-unique/tables-bound.json
```

Snapshot capture uses public ResourceRefs, table profiles and normal SQL metadata
queries. It records the real Iceberg UUID, snapshot schema, current schema,
`main` snapshot, metadata pointer and complete metadata log. An ambiguous head
fails. Every target relation is bound with `FOR VERSION AS OF`. The TPC-H binder
uses eight canonical CTEs, preserving scalar aliases and Q15's nested CTE.

The original DuckDB 1.4.3 SQL/hashes and packaged answers remain unchanged.
`tpch_contracts.json` declares all 22 output schemas, ordering keys and reviewed
rewrites. Decimal SUM stays exact; AVG and ratio operands explicitly use DOUBLE
where needed for equivalent arithmetic. Source, cast and portable results are
fully compared before binding. LIMIT queries audit N+1 rows and reject a cutoff
tie; a new dataset/scale needs its own audit. ORDER validation permits ties and
checks all rows independently of duplicate-preserving multiset comparison.
Capacity/deadline callbacks run in batches; all rows and terminal footers are
still validated.

Prepare full-table and query references with explicit execution variants:

```sh
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf run /tmp/local-tpch-unique tables-reference /tmp/local-tpch-unique/tables-bound.json --adapter duckdb --dataset /tmp/local-tpch-unique/tpch --sql-variant reference_sql --reference-label "source Parquet full tables"
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf run /tmp/local-tpch-unique query-reference /tmp/local-tpch-unique/tpch-bound.json --adapter duckdb --dataset /tmp/local-tpch-unique/tpch --sql-variant reference_sql --reference-label "source Parquet portable queries"
rtk proxy python3 -m benchmarks.local_perf verify-snapshots /tmp/local-tpch-unique /tmp/local-tpch-unique/pins --adapter public --connection-file /tmp/local-tpch-unique/public.json
rtk proxy python3 -m benchmarks.local_perf run /tmp/local-tpch-unique public /tmp/local-tpch-unique/tpch-bound.json --adapter public --connection-file /tmp/local-tpch-unique/public.json --references /tmp/local-tpch-unique/query-reference --abs-tol 1e-8 --rel-tol 1e-8
rtk proxy python3 -m benchmarks.local_perf verify-snapshots /tmp/local-tpch-unique /tmp/local-tpch-unique/pins --adapter public --connection-file /tmp/local-tpch-unique/public.json
```

Use the direct Trino TLS/auth options above to run `tables-bound.json` against
`tables-reference`, and `tpch-bound.json` against `query-reference`. Keep the
same selected DB/schema and verify pins before/after each target run. Full-table
validation uses the permitted direct stream; public SQL retains its 10,000-row
ceiling. COUNT or profile statistics alone do not establish content equality.

For bound manifests, result metadata explicitly lists logical, target and
reference SQL hashes plus the snapshot set. Each attempt retains the actual
executed variant/SQL/hash; different execution texts are not labelled as one
SQL. `reference_sql` is DuckDB-only. Independent comparison is claimed only when
both engine families are known and different; direct/public Trino share a family.

An initial public SQL POST with a complete 403 `SQL_PERMISSION_DENIED` response
is recorded as FAILED/REJECTED with a scoped receipt. Other errors and incomplete
responses retain conservative recovery. An old attempt whose rejection details
were lost can use `reconcile --rejection-evidence /absolute/audit.json` only with
an explicit local operator audit tied to the immutable attempt/manifest, exact
request/context/SQL and linked historical evidence. Observed status and a
source-derived code stay distinct. This creates a supplemental record, sends no
HTTP and never changes the original failure into a result success.

## Ownership, cleanup and remaining gates

`cleanup campaign name` only removes successful owned dataset/comparison/ingest
preparation output. It refuses unowned paths, failed/incomplete artifacts and
run/attempt/load/snapshot/known-answer/parity evidence.
Ownership names cannot be reused even after cleanup. Deleting host files does not
reset the baseline or assume VM image blocks were released. There is no broad
cleanup, supervisor or implicit deletion of previous campaigns. Keep campaign
JSON, raw attempts and results outside Git; they may contain SQL/data and runtime
paths. Never put secrets in SQL/manifests.

Commander qualification now covers persisted TPC-H SF0.01: all eight tables and
86,805 rows match Parquet, all 22 queries/932 output rows match through both
Trino and the public API, and an independent owned-table append demonstrates
AS OF reads plus drift rejection. See the
[TPC-H verification record](../../docs/guides/performance-tpch-2026-11-14.md).

Remaining P1/G1 work includes other-scale qualification, TPC-DS query execution
and all variants, transfer/queue/engine/validation telemetry, and cold/concurrent
failure conditions. Initial generated manifests remain unordered; the reviewed
TPC-H plan adds explicit ordering/tie contracts without inferring them for arbitrary
SQL. Public results beyond the
10,000-row ceiling require a separate reviewed product change. No private
bypass or `max_rows=None` is used. P2 resource/profile application, guest expansion, concurrent
load, arrival-rate tests, SLO freezing and long runs are not implemented here.
