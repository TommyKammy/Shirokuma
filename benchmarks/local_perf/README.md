# Local P0/P1 qualification toolkit

Local-only preparation under ADR-0036. Python 3.9+ runs the stdlib suite;
generation/reference execution uses a separate Python 3.12 venv and **DuckDB
1.4.3**, pinned in `requirements.txt`. Generation/reference uses no pandas/Arrow.
The optional native bulk loader isolates PyIceberg/PyArrow in `requirements-bulk.txt`;
the public adapter supports the explicitly opted-in full-result API described below.
Toolkit commands do not apply workload/VM settings, execute in the cloud, or
install into global Python. Operator runtime changes are recorded separately.

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
loopback fake-server tests. The Commander host run on 2026-11-14 passed 197 of
203 discovered tests on Python 3.9.6, with four explicit real-data and two offline
generator tests skipped. A separate verification venv enabled real generation,
both TPC datasets and pinned SQLGlot regeneration and passed all 203. SQLGlot
remains outside the ordinary benchmark runtime. Ten real loopback cases cover public context/auth/pages, safe HTTP
rejection receipts, slow-drip status/headers, incomplete Content-Length and query
lifecycle. The same suite also covers timing failures, complete TPC-DS parity,
ORDER expressions and duplicate-preserving comparison. Fresh-cache/install failures are mocked
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
The legacy `fetch_s`, `validation_s` and `elapsed_s` retain their original
boundaries. Use the versioned `timings` record below for phase measurements.

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

`max_rows` defaults to 10,000 and retains the existing 1..10,000 preview contract.
The preview overflow example must still fail as `PUBLIC_TRUNCATED`. Explicit
`--all-results` selects the reviewed public `result_mode="all"`, `max_rows=null`,
read-only contract; it preserves ordinary authorization and context checks. Freeze
a separate full-mode connection rather than overriding a saved preview identity.
The server retains two replay pages; clients consume the next URI in order and
persist every row. A bounded-cache temporary SQLite index detects old page cycles
and is capacity-accounted and removed on every exit. Public qualification still
requires complete comparison through this adapter; direct success never sets it.
See [bulk and full-result API](../../docs/guides/performance-bulk-results-2026-11-14.md)
for request fields, page limits, failure/recovery semantics and commands.

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
validation can use the direct stream or an explicitly frozen `--all-results`
public connection. Preview mode retains its 10,000-row ceiling. COUNT or profile
statistics alone do not establish content equality.

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

## Persisted TPC-DS qualification

`prepare-tpcds` qualifies the pinned DuckDB 1.4.3 **99 single-statement** corpus.
All 24 tables and all query IDs are required. This older fixed-parameter corpus
does not cover every TPC-DS variant or TPC-DS 4. See the
[DuckDB extension documentation](https://duckdb.org/docs/lts/core_extensions/tpcds).
The static contracts retain canonical and portable SQL hashes, explicit output
types, ORDER keys and transformation provenance. SQLGlot is used only for
offline regeneration; ordinary preparation and execution need no SQL parser
dependency beyond the isolated DuckDB environment.

Use the same fresh-campaign, public connection, snapshot verification and
full-table procedure as TPC-H, with a short unique prefix. Public physical names
are limited to 31 characters; preparation shortens eligible source names with a
stable hash and rejects a prefix that leaves no room. The wide TPC-DS tables
need smaller INSERT batches under the current Trino query-text ceiling:

```sh
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf generate /tmp/local-tpcds-unique tpcds --suite tpcds --scale 0.01 --timeout 300
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf prepare-tpcds /tmp/local-tpcds-unique /tmp/local-tpcds-unique/tpcds portable --timeout 300
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf prepare-ingest /tmp/local-tpcds-unique /tmp/local-tpcds-unique/tpcds inputs --prefix ds_sample
rtk proxy python3 -m benchmarks.local_perf load-public /tmp/local-tpcds-unique /tmp/local-tpcds-unique/inputs load --adapter public --connection-file /tmp/local-tpcds-unique/public.json --batch-size 500
rtk proxy python3 -m benchmarks.local_perf freeze-snapshots /tmp/local-tpcds-unique /tmp/local-tpcds-unique/load pins --adapter public --connection-file /tmp/local-tpcds-unique/public.json
rtk proxy python3 -m benchmarks.local_perf bind-tpcds /tmp/local-tpcds-unique/portable/queries.json /tmp/local-tpcds-unique/pins/snapshots.json /tmp/local-tpcds-unique/tpcds-bound.json
```

Initialize that campaign and freeze `public.json` first using the earlier
commands. Prepare `tables-bound.json` and both DuckDB references, then run the
bound manifest through direct Trino and the public adapter as above. Retain
`--abs-tol 1e-8 --rel-tol 1e-8` for floating outputs; exact types stay exact.
For the full 24-table reference/comparison pilot, allow `--peak 1073741824
--timeout 600` for typed output, SQLite comparison and capacity checks; this is
separate from the 300-second per-query SQL qualification setting.
Requalify every scale with its own full source/cast/portable parity and pins.
The default ingest batch remains 2,000; `--batch-size` explicitly selects
1..10,000. A 500-row batch is a measured setting for this corpus, not a guarantee
that every arbitrary input fits the server's SQL-text limit.

The initial 2GiB coordinator heap exhausted during public ingestion. The accepted
pilot used a separately recorded temporary coordinator heap8GiB/container12GiB,
with the worker unchanged at heap2GiB/container4GiB, and restored the baseline
after verification. Apply an authorized, verified runtime configuration before
loading; these toolkit commands do not provision that profile. Select the [formal coordinator settings](../../docs/guides/performance-coordinator-profile-2026-11-14.md) through the resident foundation configuration before running this toolkit. Retain historical
UNKNOWNs and execution-fence evidence separately; a new run must use ordinary
admission and a fresh owned namespace rather than rewrite old outcomes.

ORDER validation supports projected numeric subtraction and integer CASE keys
in addition to column positions. LIMIT audits examine N+1 rows. If the boundary
ties, preparation scans the complete unlimited result: every row in that ORDER
group must have the identical complete typed output row. It retains the group's
multiplicity and SQL hash. Distinct rows within a cutoff tie fail qualification.
Full exact multiset equality is proved before expensive tolerant matching;
partial exact pairs are never removed from the augmenting-path fallback.

## Timing boundaries

Each `attempt.json.timings` has `version: 1`, monotonic offsets in seconds from
the attempt start, and nullable events. The start precedes durable intent and
adapter setup; `attempt_complete` precedes the final attempt JSON write.

| Field | Measured boundary |
|---|---|
| `dispatch_started` | Initial HTTP request entry; DuckDB SQL execution after connection and VIEW setup |
| `initial_response_complete` | Initial HTTP request returns decoded JSON successfully; HTTP error responses retain phase/rejection evidence instead |
| `first_row` / `last_row` | Decoded page/batch observed after identity and schema checks, before typed row serialization; empty results keep both null |
| `terminal_observed` | Latest recognized terminal protocol state observed; not the backend completion timestamp, and may precede `result_complete` |
| `result_complete` | All result pages consumed and successful typed artifact footer written |
| `validation_started` / `validation_complete` | Manifest schema, ORDER and full reference comparison; failure keeps the incomplete event null |
| `recovery_started` / `recovery_complete` | Separate cancellation/inspection window after failure |

HTTP aggregates split `initial` and `follow_up` requests into connection (including
TLS), socket send, header wait, body read, JSON decode and total seconds.
Header wait includes server processing and network delay; it is not engine queue
time. `client_poll_wait_s` is deliberate client sleep. Artifact processing
includes guards, receipts, capacity checks and typed output; it is not disk time
alone. DuckDB also records SQL execution and local fetch durations. These
overlapping observations are not additive components of end-to-end time.

`backend_report` preserves only the latest observed `response.stats` snapshot:
queued, CPU, wall and elapsed milliseconds, with its page state. Missing or
invalid values stay null; reported zero remains zero. No client interval is
converted into engine queue/execution time. See the
[Trino 483 protocol source](https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/develop/client-protocol.md).
Failures retain partial timings and original outcome; recovery HTTP is excluded
from result-fetch aggregates. Single qualification observations do not establish
latency percentiles or a performance SLO.

Before dispatch, prior owned runs are inspected with a fresh per-call manifest
and query map. The complete manifest, snapshot binding and every query contract
are checked once per saved run; every attempt, rejection, reconciliation and
uncertain status is still checked. Standalone validators perform full checks.
There is no persistent cache or caller-controlled validation bypass.

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

TPC-DS SF0.01 now has all 24 persisted tables/277,976 rows fully matching Parquet,
and all 99 queries/3,928 output rows matching through direct Trino and the public
API. Both routes preserve all 27 empty results and version1 timings. The initial
2GiB heap OOM/UNKNOWN cleanup, Q72 dialect failure and interrupted preflight check
are retained; the accepted run uses the separately recorded temporary profile.
The baseline was restored afterward without rewriting the historical UNKNOWN. The subsequent [formal coordinator resource profile](../../docs/guides/performance-coordinator-profile-2026-11-14.md) provides optional typed CPU/memory/heap settings, an optional `max_stage_count` (exact integer 1..2147483647) rendered only as coordinator `query.max-stage-count`, canonical rollout, rollback and normal stop/wake validation. Omitting `max_stage_count` preserves the previous generated bytes and resource digest; an explicit profile rejects stale stage annotations/properties. Active property lines containing backslashes are rejected as ambiguous Java Properties continuations/escapes; comment lines may contain them. The selected coordinator heap8GiB/container12GiB keeps worker and query-memory limits unchanged.
See the [TPC-DS and timing record](../../docs/guides/performance-tpcds-timings-2026-11-14.md).

The follow-up [variant and instrumentation record](../../docs/guides/performance-tpcds-variants-instrumentation-2026-11-14.md) distinguishes the unchanged 99-query corpus, four supplemental iteration cases and 14 approved-variant families. It also records offline observer-cost measurements and sampled global capacity checks in the runner, with deadline/local peak checks retained on every callback.

The [approved-variant qualification record](../../docs/guides/performance-tpcds-approved-variants-2026-11-15.md) pins 14 concrete Spark-source queries separately from current TPC template identity or general base-query equivalence. `prepare-tpcds-approved` requires `--source-dir` and a dataset-specific `--spark-reference` exported by `spark_reference.py` inside the recorded Spark runtime. Preparation, binding and new runs validate materialized successful qualification evidence; frozen recovery/cancellation remains available independently. Spark and DuckDB matched all 14 queries/903 rows at SF0.01. The earlier individual Trino and public-API audits matched 13 queries/803 rows each; 14a exceeded the then-current stage limit (348 > 150), and those failed/11 not-run receipts remain historical evidence. The subsequent [stage-limit qualification](../../docs/guides/performance-stage-limit-2026-11-15.md) applied `max_stage_count=400` and completed 14a and the full 14-query suite on both routes (14/14, 903 rows each). Coverage enumeration (99 + 4 + 14 = 117) is not an execution-success count.

The formal coordinator stage-limit setting is now applied and restored; 14a and the full approved suite completed on both routes. The subsequent [SF0.1 scale and observer characterization](../../docs/guides/performance-scale-meter-2026-11-15.md) generated 24 fresh tables/1,175,873 rows, re-qualified the 14 approved queries against a fresh Spark reference, and matched all 24 tables and the full-result `store_sales` projection through direct/public all-results. The initial observer result was invalid because it measured an unsampled guard and incomplete accounting. Corrected production-guard measurement also failed the task-local screen: 1M-row offline checked/no-op per-pair overhead median was about 156% (ratio-of-medians about 159%), and the largest corrected global scan was about 332ms (median 302ms/p95 316ms). Instrumentation readiness is therefore not accepted; the initial result is retained only as an invalid experiment. A follow-up scan-only optimization ([capacity scan record](../../docs/guides/performance-capacity-scan-2026-11-15.md)) moved `allocated` to `os.scandir`: 31-root full scan max fell from 394ms to 171ms and offline paired overhead median from 151% to 78%, so the max-scan screen passes but the 10% screen still fails. No cache/interval change was made. A later [bulk-attribute prototype record](../../docs/guides/performance-capacity-bulk-2026-11-15.md) measured `getattrlistbulk(2)` in-process under Python 3.12.12 over 33 roots (the prior 31 plus the whole task tree and campaign at zero baseline, prior host baseline carried forward): per-scan cost fell to 39.3ms in situ (8 bounded threads) and offline paired overhead to 19.99%, so the 10% screen still fails and `allocated` stays on `os.scandir` with no native helper or build dependency. Those v3 measurements remain valid. A separate [subtree work-queue record](../../docs/guides/performance-capacity-subtree-2026-11-15.md) wrote a different `ca_scan` entry point in that pass; its initial version merged every root and so overwrote directly measured file roots with zero, and leaked a descriptor on each failing directory. Both were found by independent review, fixed in a later prototype revision with mixed dir/file ordering, real 35-root vector parity and repeated-failure descriptor tests, and the screen re-run: best candidate 18.62% paired overhead (baseline 79.59%) at 32.55ms per scan, so the 10% screen still fails and no native path is adopted. A subsequent [syscall decomposition pass](../../docs/guides/performance-capacity-syscall-2026-11-15.md) profiled the 37-root scan (open 57.5%, `getattrlistbulk` 37.3% of summed per-directory phase time, profiler overhead +1.17%) and screened a new candidate against the unchanged subtree control: primary 17.35% vs control 16.55% paired overhead, both failing the 10% screen, so no native path is adopted. A follow-up [dispatch/merge decomposition and repeat screen](../../docs/guides/performance-capacity-dispatch-2026-11-15.md) introduced no new candidate and re-measured the unchanged control on 39 roots: baseline 84.89% vs control 15.54% paired overhead, still failing the 10% screen, so no native path is adopted. An [internal-cost investigation](../../docs/guides/performance-capacity-internal-2026-11-15.md) then repeated the screen on 41 roots (baseline 83.17% vs control 18.28%), withdrew an earlier attribute/buffer comparison whose probe reused one directory fd, and found no candidate; kernel-internal per-call attribution was not observable with the tools available in that pass. Remaining P1/G1 work includes reducing repeated full-global capacity scans or their per-scan cost while preserving deadline/local-peak/stop guarantees, then repeating the same readiness screen; 99/4 native coverage at this scale, other scales, engine telemetry unavailable through the current API, and cold/concurrent failure conditions. Initial generated manifests remain
unordered; reviewed TPC-H/TPC-DS plans add explicit ordering/tie contracts without
inferring them for arbitrary SQL. The reviewed opt-in public full-result mode
removes the preview row ceiling while retaining authorization and bounded replay.
Native bulk setup is a separate explicit-credential Parquet registration path,
not public ingest API throughput. Format 2 supports multiple files per table;
each file is bounded to 104,857,600,000 bytes before hashing or writes. The table
total can exceed that per-file limit within the campaign capacity budget.
P2 worker/VM profile application, guest expansion,
concurrent load, arrival-rate tests, SLO freezing and long runs remain subsequent work.

## Sharded Parquet datasets

Default generation retains the format 1 single-file manifest. Explicit
`--target-file-bytes` generates format 2 directly from DuckDB tables, without
first exporting one large Parquet file. `shard-dataset` converts an existing
successful owned dataset into a fresh dataset and preserves its source manifest
identity. Both paths stream COPY output and validate full schemas, rows and
duplicate-preserving contents before success.

```sh
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf generate "$CAMPAIGN" tpch-sharded --suite tpch --scale 0.01 --target-file-bytes 268435456 --row-group-size 122880
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf shard-dataset "$CAMPAIGN" "$SOURCE_DATASET" sharded --target-file-bytes 268435456 --row-group-size 122880 --max-file-bytes 104857600000 --timeout 600 --peak-bytes 2147483648
```

The target is soft at row-group boundaries; a separate post-generation hard
limit rejects oversized files. Row groups require at least 2,048 rows. Format 2
declares an ordered `files` list per table with each file's rows, size, physical
allocation and SHA256. Consumers use that exact list without glob expansion.
The table sums must agree, schemas must match across all files, and an empty
table has one schema-bearing empty Parquet file. Query manifests remain format 1
and retain their SQL and variants while binding to the new dataset identity.

DuckDB references, TPC-H/TPC-DS parity preparation and JSONL preparation accept
both formats. Source hashing occurs at generation, sharding, loading and JSONL
preparation boundaries, rather than once per query. Failed output and FAILED
metadata remain for diagnosis; owned temporary databases/spill are closed and
removed. See the [contract and verification record](../../docs/guides/performance-parquet-shards-2026-11-14.md).

Commander verification on 2026-11-14 covered direct sharded generation of TPC-H
SF0.01 (8 tables/28 files/22-query parity) and TPC-DS SF0.01 (24 tables/65 files/
99-query parity). A separate existing dataset was sharded into five files across
two tables, including an empty table, then registered and pinned. All 200,006 rows
matched through direct Trino and the full-result public API; JSONL preparation
also passed. The first TPC-DS parity plan exceeded its 300s deadline and remains
FAILED; a fresh 600s qualification completed all 99 without SQL/tolerance changes.
These are correctness checks, with material artifact/validation overhead;
large-scale performance and other variants/scales remain subsequent work.

## Native bulk setup

`load-bulk` copies validated source Parquet in 1MiB chunks to fresh Iceberg v2
tables with explicit DB-consumer OAuth and table-vended STS, then commits all
files once per table via PyIceberg `add_files`. Every source file is validated
before the first mutation; vended credentials are refreshed between files and
before commit, with target UUID/location/schema ID/schema/properties rechecked.
The snapshot proof covers all registered file paths, rows and bytes, including
empty files. Distinct files with identical content remain distinct objects.
It avoids VALUES expansion and eager row loading.
Install `requirements-bulk.txt` only in a dedicated environment; provide separate
`--bulk-auth-file`, `--prefix`, deadline and peak budget. `freeze-snapshots` accepts
successful owned bulk journals and checks the returned UUID/snapshot against
public observations. Mutation uncertainty retains UNKNOWN evidence and blocks
new bulk dispatch in that campaign; no remote cleanup or retry is automatic.
See the [usage and verification guide](../../docs/guides/performance-bulk-results-2026-11-14.md).

Commander qualification on 2026-11-14 matched complete public results for the
existing 73,049-row table and a fresh native-loaded 54,256,183-byte / 200,006-row
Parquet table against source references. Direct/public full comparisons, legacy
truncation, cancellation and retained pins passed. The initial comparison timeout
is retained; SQLite progress guard sampling was corrected without changing the
600s deadline. This is correctness preparation with substantial client/validation
overhead, not engine performance or large-scale acceptance. See the
[implementation record](../../docs/guides/performance-bulk-results-2026-11-14.md).
