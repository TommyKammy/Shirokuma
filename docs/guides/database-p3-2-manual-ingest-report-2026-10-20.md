# P3-2 manual CSV / JSON Ingest implementation

2026-10-20. Callable adapter: `lake_ingest.manual_ingest(principal, context, *, idempotency_key, mode, ...)`; create requires `destination=DestinationLocator`, other modes require `target=ResourceRef`. Input is `file=` or `filename=` / `content=`; options are `format`, `delimiter`, `encoding`, `schema_hints`, `key_columns`, `batch_size`, `atomic_append`.

Captured immutable bytes and copied options enter the durable request before parsing / target resolution; replay observes without SQL. Engineer and admin Ingest stay in the selected DB's registered RAW / SANDBOX. Existing types precede append / upsert and overlapping replace inference and conversion, preserving target order; conflicting existing-type hints are rejected before writes. No-hint CSV / JSON numerics use exact BIGINT / DECIMAL inference, and exact typed keys implement last-input-row wins. Unicode / reserved / long column names are quoted.

Create / replace / atomic append / upsert load an owned SANDBOX stage, recheck target UUID and columns, and publish with one generated CTAS / CRTA SELECT / INSERT SELECT / MERGE respectively. MERGE updates only supplied non-key columns and inserts supplied columns, preserving omitted values and target UUID; key-only input has no matched-update clause. Ordinary append commits each batch and reports known partial counts. Schema v7 adds per-physical-target claims and optional query commit receipts; FINISHED commit and receipt are recorded in the same query-state transaction, including evidence-based reconciliation. ACTIVE / CANCELING / UNKNOWN claims cannot be stolen; no timeout / PID-based release or automatic re-POST exists.

Receipts include operation / input / request fingerprints, requested hints, validation columns and separately observed engine columns, format / encoding / delimiter, target Ref when observed, atomic semantics, accepted / committed rows and batches, stage batches, cleanup outcome and errors. Iceberg-normalized integer / timestamp types are recorded after commit rather than claiming the requested physical precision. Known commits survive observation, cleanup, bookkeeping and process-loss faults. A changed source path is never reread after capture. Legacy route gates, public SQL guard, WAP / Export and UI / CLI capabilities remain unchanged.

Independent focused verification used `rtk proxy tools/local_experiment/dataops/.venv/bin/python3 -m unittest discover -s tests -p <pattern> -q`: 189 passed.

| Test file | Result |
| --- | --- |
| `test_manual_ingest.py` | 44 passed; three READY DBs, CSV / JSON / JSONL, exact replace types / numeric inference, MERGE shapes / UUID, observed schema receipts, whitespace / BOM, replay, claims, partial / UNKNOWN append, commit / cleanup / bookkeeping faults, real subprocess loss and system-Python import |
| `test_dataops_service.py` | 25 passed, including the socket-based HTTP response-loss test |
| `test_control_store.py` | 10 passed, including v6-to-v7 preservation and migration rollback |
| `test_query*.py` | 33 passed |
| `test_lake_ingest.py` | 47 passed |
| `test_sqlserver_sync.py` | 30 passed |

Independent `rtk proxy make PYTHON=tools/local_experiment/dataops/.venv/bin/python3 verify-local-experiment` passed: 1,109 tests, 1,077 passed and 32 skipped. The same command with `verify-design-context` passed 14. Python 3.9.6 imports and diff / new-file whitespace checks passed; no dependency installation was needed.

Real Trino 483 acceptance run `0f2b7c93` passed 183/183 recorded checks: main 134, permission / claim / external-append supplement 22, lost FINISHED response 17, and paired actor positives 10. Sales-only, finance-only and selected-admin actors each ingested two rows in their own authorized RAW; lowering the admin to engineer without a grant was denied without fallback. Exact decimals, timestamps, omitted upsert columns and MERGE UUID preservation were checked across three READY DBs. Target contention and external append during staging were checked in one DB's supplement.

The actual lost-response MERGE sent one POST and changed the target, while the child / parent remained UNKNOWN, replay sent zero POSTs and the claim stayed held. A real GET observer positively reconciled FINISHED and restored the commit receipt; the parent stayed UNKNOWN until explicit `reconcile_failure` settled FAILED while retaining the known commit.

Final ledger: 48 operations (26 FINISHED, 22 expected FAILED, including four retained known commits); 237 child queries / POSTs (234 FINISHED, three expected stage failures), including 146 mutating adapter POSTs. Every same-key replay sent zero POSTs. UNKNOWN operations and unresolved claims were zero. Exact Ref / UUID cleanup removed all 11 fixtures; all three DBs stayed READY, LOCAL was OPEN, and doctor returned healthy with exit 0.

Ignored evidence: `var/shirokuma/p3-2-acceptance/0f2b7c93/` contains `runtime-summary.json`, `supplement-summary.json`, `lost-commit-summary.json`, `actor-positive-summary.json` and `final-readonly-evidence.json`; the acceptance script is `var/shirokuma/p3-2-acceptance/runtime-checks.py`.

Claims serialize adapter operations; native external SQL is outside that protocol. Supported scalars are VARCHAR, BOOLEAN, integer widths, finite DOUBLE / REAL, DATE, DECIMAL up to 38 digits and timezone-naive TIMESTAMP(0..6); unsupported types / offsets are rejected. This accepts the P3-2 callable adapter, including real-engine faults. Normal UI / CLI route acceptance remains P3-5; this report does not claim all Phase 3 complete. No commit or external publication was performed.
