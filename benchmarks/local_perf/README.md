# Local P0/P1 qualification toolkit

Local-only preparation under ADR-0036. Python 3.9+ runs the stdlib suite;
generation/reference execution uses a separate Python 3.12 venv and **DuckDB
1.4.3**, pinned in `requirements.txt`. No pandas/Arrow, product API edits,
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
loopback fake-server tests. The Commander host runs on 2026-11-13 passed all 45
tests on both Python 3.9.6 and 3.12.12, including 8 real loopback cases for slow-drip status/headers, incomplete
Content-Length and query lifecycle. Fresh-cache/install failures are also mocked
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
separately later; the worker did not contact live services. Optional Basic
credentials come from `--password-env ENVIRONMENT_VARIABLE_NAME`, never a CLI
password. Every URL must be the same configured loopback origin. Redirects,
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
Unknown attempts without handles cannot be resolved by this adapter. Failed and
uncertain attempts remain in run counts and fresh outputs cannot overwrite them.

```sh
rtk proxy python3 -m benchmarks.local_perf inspect "${TMPDIR:-/tmp}/local-perf-example/qualification/run.json"
rtk proxy python3 -m benchmarks.local_perf reconcile "${TMPDIR:-/tmp}/local-perf-example/trino-qualification/1-0/attempt.json" --adapter trino --url http://127.0.0.1:8080
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

## Ownership, cleanup and remaining gates

`cleanup campaign name` only removes successful owned dataset/comparison output;
it refuses unowned paths, failed/incomplete artifacts and runs/attempt evidence.
Ownership names cannot be reused even after cleanup. Deleting host files does not
reset the baseline or assume VM image blocks were released. There is no broad
cleanup, supervisor or implicit deletion of previous campaigns. Keep campaign
JSON, raw attempts and results outside Git; they may contain SQL/data and runtime
paths. Never put secrets in SQL/manifests.

Remaining P1/G1 work: verify real Trino snapshot/dialect/types and cancellation
behavior, independent cross-engine oracle checks, TPC-DS query execution/variants,
query ordering/tie contracts, load registration and telemetry. Generated initial
TPC manifests select duplicate-preserving unordered comparison; ordered comparison
is available explicitly, but SQL ORDER BY/tie semantics are not inferred here.
Fake HTTP transport and packaged TPC-H answers
have passed locally; they do not replace those real product/engine checks.
The **public Shirokuma adapter is not implemented**: no private bypass, no
max_rows=None, no treating 10,000-row truncation as completion. Product API
auth/context/history/cancellation and bulk/full-page handling remain a separate
reviewed change. P2 resource/profile application, guest expansion, concurrent
load, arrival-rate tests, SLO freezing and long runs are not implemented here.
