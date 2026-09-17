# Retained Query Doctor history

This WP-L3-CH-001 local increment retains the saved Query Doctor observer
projections in the existing AgentHouse ClickHouse. It runs no new Trino query or
model inference and does not collect cluster-wide history. The Trino Gateway is
not installed; this is not its query-history export or a server audit trail.

Run from the active checkout:

```sh
rtk proxy python3 tools/local_experiment/query_history/import_receipts.py inventory
rtk proxy python3 tools/local_experiment/query_history/import_receipts.py plan
rtk proxy python3 tools/local_experiment/query_history/import_receipts.py apply
rtk proxy python3 -m unittest discover -s tests -p test_local_query_history.py
```

`inventory` and `plan` only read the fixed
`tmp/local-experiment/query-bear/<UUID>` source and create private local receipts.
They do not contact ClickHouse. `apply` checks the existing dedicated ARM64
Colima context and AgentHouse runtime, then creates or fills only the two tables
below. It does not start services, rotate credentials, replace tables, touch
Pawprints/benchmark tables, or change retained volumes. The CLI accepts no path,
SQL, URL, user, namespace, filter, or query ID override.

| Table | Meaning |
| --- | --- |
| `agenthouse.query_history_runs` | One observer run, workflow/observer outcome, captured and uncaptured request counts, history coverage and source hash. |
| `agenthouse.query_history_queries` | One captured query ID, derived dedicated observer user, response state/completeness, latest saved history coverage and nullable times. |

The evidence-directory UUID and observer run UUID are independent. The user is
bound to `shirokuma-query-doctor-<observer UUID>`. Query IDs must be captured in
that receipt; history source IDs must refer to its completed history requests.
The saved history projection already checked the raw server user when collected,
but does not retain that raw field. This importer revalidates the projection's
identity links and labels its provenance
`saved-observer-projection-not-server-attestation`; it cannot independently
prove that historical server ownership or authenticate edited source files.
The first import and every retry preserve source hashes, so later changes
conflict with retained payloads instead of silently replacing them.

The four fixed SELECT/EXPLAIN workload requests are separate from the history
and statistics helper requests. Helpers have `history_status=not_target` and
NULL history measurements. Workload requests are `observed`, `missing` from a
saved history snapshot, or `unobserved` if no snapshot was saved. The latest
saved snapshot supplies the retained view; prior snapshot bytes remain in the
unchanged source and are covered by its hash. Missing durations remain NULL;
measured zero remains zero. No resource-cost or performance-gain claim is made,
and helper durations are never aggregated as workload performance.

Failed, interrupted and incomplete observer receipts remain visible in the
inventory. Only terminal sources are eligible for apply: success needs the
completed checkpoint, saved-span verification and matching closed root span;
failure needs the producer's fixed terminal error code and closed root span;
an interrupted observer also needs the saved signal marker. The producer
initially writes `result=failed` while still running, so that string alone is
insufficient. Incomplete, running or unobserved workflow outcomes block the
whole apply without removing their projected rows from the plan. This avoids
freezing a mutable source under an immutable record ID before it finishes.
Uncaptured requests contribute explicit counts but cannot produce invented query rows.
A missing/malformed observer receipt, contradictory completion, foreign or
reused identity, unexpected directory, symlink, or oversized inventory makes the
entire plan ineligible for apply; the invalid entry is reported instead of
silently skipped. An absent workflow result remains `unobserved` and ineligible.

Both tables have dedicated owner comments, exact ordered schemas and
`ReplacingMergeTree ORDER BY record_id`. Runtime, schema, defaults, key and engine
differences are refused. No existing table is adopted or altered. Deterministic
IDs and payload hashes make unchanged retries no-ops. Conflict detection reads
physical rows without `FINAL`, so a different payload cannot be hidden by
ReplacingMergeTree. Consumers must use `FINAL` for logical row counts because an
uncertain INSERT retry may leave identical physical duplicates.

Import evidence is private under `tmp/local-experiment/query-history/<UUID>`
(directory 0700, JSON 0600): `plan.json`, `apply.json` and, on error,
`failure.json`. The apply journal saves each create/insert as pending before the
request and confirms only after readback. Failed or interrupted pending writes
remain `unknown`; retry rechecks the exact schema, physical rows and all source
hashes before filling missing rows. A local advisory lock excludes concurrent
CLI imports. Backend error text, SQL, host paths, model text and raw Trino errors
are not written to the database or diagnostic stdout.

There is no delete/rollback command. If an operation fails, retain the source,
credential, volume and private journal, correct the reported local issue, then
rerun `plan` and `apply`. Do not drop retained tables or erase failed evidence to
make the retry pass. Back up the existing AgentHouse volume using its runbook;
the source receipts remain the reproducible input and must be retained too.
