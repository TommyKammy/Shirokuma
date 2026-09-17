# Retained local benchmark events

This bounded `WP-L3-CH-001` increment imports the existing local TPC-H SF10
receipts into two additive AgentHouse tables. It does not run benchmarks, call
a model, collect arbitrary Trino history, or start/change a runtime service.
`agenthouse.pawprints` and its schema remain outside this importer.

From the repository root:

```sh
rtk proxy python3 tools/local_experiment/benchmark_events/import_receipts.py plan
rtk proxy python3 tools/local_experiment/benchmark_events/import_receipts.py apply
```

The default command is `plan`. Neither command accepts a source path, URL,
database, table name, query, or arbitrary SQL. `plan` reads all UUID directories
under `tmp/local-experiment/benchmark` and writes a new private evidence directory
under `tmp/local-experiment/benchmark-events/<uuid>/`. It performs no runtime
operation. `apply` regenerates that inventory, verifies the existing dedicated
Colima AgentHouse container, and uses its existing private loopback credential.
It never creates/starts a container, rotates credentials, or modifies grants.

## Meaning of the data

- `benchmark_runs` contains one logical record per retained benchmark run,
  including failed runs. The original outcome stays failed after any separately
  recorded cleanup recovery. `recorded_pairs` counts comparisons explicitly
  recorded in the summary and rechecked against both saved result files.
  `expected_pairs` is the planned 22, not an observed query count.
  `comparison_unknown_pairs` is the remaining planned comparisons; it does not
  classify them as failed or executed. `observed_query_sides` counts only saved
  reference/Iceberg query results.
- `benchmark_queries` contains only actually saved query-side results. It stores
  the captured query ID, known timings/counters, result row count, evidence hash,
  and whether the comparison was recorded. Missing measurements remain NULL;
  measured zero remains zero. A saved result without a summary comparison is
  `unobserved` for comparison status, even if its rows happen to match.
- A run-level wall duration comes from the second-resolution receipt timestamps.
  Query wall time, engine elapsed time and CPU time are separate measurements;
  do not add them or infer production capacity. Reference queries include data
  generation. All measurements share one host/VM and lack cold-cache controls.
- Provenance is `recorded` only when every observed side has a summary SQL hash
  matching its saved executed-SQL file, and input/adaptation metadata exists.
  The group hash removes only the fresh run token from SQL; other SQL differences
  remain. Old missing SQL hashes/adaptations remain `unknown:<run-token>` and
  are not silently grouped with the corrected Q15 run. An evidence hash proves
  the retained artifact's content, not an original Git revision or historical
  source authenticity.
- `cleanup_sql_verified` describes the receipt's SQL table-cleanup check only.
  It does not prove that orphan object-storage data or multipart uploads are
  absent. No cleanup, DROP, ALTER, DELETE, or benchmark recreation occurs here.

The initial retained corpus has three runs: one passed and two failed. It has
36 recorded comparison pairs and 72 saved query-side results. Thirty of the
66 planned comparisons are unobserved. This is not a claim of 66 executed queries
or a 36/66 query success rate.

## Integrity and retry

All files inside every UUID source directory are bounded, hashed and checked for
same-user ownership, unsafe writable modes, hardlinks and symlinks. Source
directories are never modified. Duplicate JSON keys, non-finite metrics,
conflicting identities, inconsistent summaries, missing comparison result files
and tampered SQL/result artifacts prevent import. Malformed and incomplete runs
remain visible in `plan.json` and block the whole apply; no success-only filter
is used. Non-UUID diagnostics are counted separately and are not benchmark runs.
The summary must record the fixed worker user for its run token, even on failure.
That is receipt provenance, not an independent database attestation of the user.

The two table definitions are fixed in `SCHEMAS` in `import_receipts.py` and use
`ReplacingMergeTree ORDER BY record_id`. Each table has a dedicated owner comment.
An existing table must match its engine, sorting key, comment and complete column
schema. A name collision stops before creating either table. Deterministic UUIDs
come from the run token and, for queries, query number/side. Existing records must
match every projected field, including payload/evidence hashes. Different
retained content is a conflict; it is never overwritten or deleted.

The importer reads physical rows before writing so `FINAL` cannot hide a
conflicting duplicate. Identical physical copies, possible after an uncertain
HTTP response, collapse to one logical record. Use `FINAL` in analytical queries.
A private process lock serializes this importer. Ownership is checked again
before inserts. Source hashes are rechecked before/after writes and at the end.
Successful inserts require exact database readback, preserving previously read
rows. Running `apply` again inserts zero records when the input is unchanged.

`apply.json` is atomically written at mode 0600, in a mode-0700 directory. It
records a pending operation before each mutation and confirms it only after
readback. On timeout/cancellation the pending batch has unknown outcome; some
rows might have arrived. Keep that receipt and rerun the same fixed command.
The next invocation validates all existing rows and inserts only missing ones.
There is deliberately no destructive rollback. Source drift after a partial
write requires inspection; the importer refuses to replace retained content.

The safe projection excludes raw rows, SQL, errors, credentials and host paths.
Source filenames and hashes appear only in the private plan. Stdout contains
counts, fixed error codes and a repository-relative evidence path. Exit 0 means
the complete inventory is valid (and for `apply`, readback/source checks passed);
known failed benchmark runs do not make a valid import fail. Exit 1 means invalid
or incomplete input, conflict or unverified import; handled SIGINT/SIGTERM uses
128 plus the signal number. An import pass is not a benchmark pass.

## Verification

```sh
rtk proxy python3 -m unittest discover -s tests -p 'test_local_benchmark_events.py'
```

Before a first live apply, preserve the source-file hashes and existing
AgentHouse schema/data evidence. Check all three retained run outcomes and exact
36-pair/72-side/30-unobserved counts after readback, then repeat `apply` and verify
zero inserts and identical logical rows. Compare the original source hashes and
Pawprint schema/records after both invocations. Keep every import receipt,
including failures. Removing these new tables is not part of this command;
ordinary retry is non-destructive and needs no rollback.

## Verified local application (2026-09-17)

First apply `e2c5fd7ea9c24fd1818846399aa2846b` inserted 3 run records and 72
query-side records. Repeat `998612d03fb44bbc86a8df3306262f10` inserted 0/0,
with identical full logical rows. Independent checks at 05:32 UTC preserved all
137 original files (135 inside run directories and 2 diagnostic files), the
Pawprint schema, and all 294 Pawprint records. Evidence is retained under
`tmp/local-experiment/benchmark-events-preflight/013ba0f37c944c69a7f6710cd473ff7d/`.
The existing Grafana reader still has only its original Pawprint grant; this
increment does not expand dashboard access or add a benchmark visualization.
