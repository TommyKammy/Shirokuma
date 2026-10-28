# P3-1 DataOps execution boundary — 2026-10-20

`dataops_service.begin` freezes the authenticated actor, resolved context and
actual backend credentials once. Its explicit create/stage, insert, select,
insert-select, rename and owned-cleanup methods generate qualified same-DB SQL.
Table Refs require fresh REST UUID/incarnation checks; destination conflicts,
including UUID-less Views, return 409. Stage names are server-generated.

Control-store migration 6 adds separate operation, child-query and owned-object
tables. Request/admission and every child intent/relation precede transport POST.
Replay is observation-only; accepted parents survive DRAINING between steps.
Lost responses/processes retain UNKNOWN blockers without resending SQL. Child
pages/cancellation reuse query-service ownership, transport and captured credentials.

`record_commit` preserves successful work before cleanup; `complete` rejects
failed non-cleanup children, `fail` settles definite failure, and `cancel` interrupts
active page loops using original credentials. `reconcile_queries` observes child
outcomes; explicit `reconcile_failure` can abandon an UNKNOWN parent only after
all children have definite terminal outcomes, retaining known commits/partial effects.
An abandoned operation with no child intents can also be explicitly failed under
its process-owner lease and transaction; it never claims success or sends SQL.
Absent or uncertain backend observations continue to block drain.

Worker verification: `rtk proxy python3 -m unittest discover -v -s tests -p
test_dataops_service.py` passed 25 tests, including real HTTP response loss,
actual subprocess exit/recovery, endless-page cancellation, three-DB separation,
grant/binding changes, ownership and commit/cleanup failures. The same command
with `-p 'test_query*.py'` passed 33; `test_warehouse_admission.py` passed 8.
`test_control_store.py` passes 9, including upgrade from version 5.

Independent `rtk proxy make PYTHON=tools/local_experiment/dataops/.venv/bin/python3
verify-local-experiment` passed: 38 suites, 1,063 tests, 1,031 passed and 32 skipped.
That run preceded the final zero-child regression; the final 25-test DataOps rerun
covers that delta. The final independent focused run passed all 75 tests
(DataOps 25, query 33, control-store 9, admission 8).
The same make command with `verify-design-context` passed 14.
The initial system-Python broad run lacked YAML; the existing repository venv
resolved the prerequisite without installing dependencies. Diff/whitespace/newline
checks passed; only query-service and control-store changed among the seven
original boundary modules inspected against the baseline.

Commander runtime verification: `rtk proxy python3
var/shirokuma/p3-1-acceptance/runtime-checks.py` passed 22 recorded checks
(run `ba3efd4dca`), across sales, finance and a separately registered third DB.
Three operations and twelve children finished; exact three-row values, observed
UUIDs, replay/conflict/authorization guards and cleanup were checked. Evidence is
in ignored `var/shirokuma/p3-1-acceptance/runtime-summary.json`; no stages remained.
`rtk proxy go run ./cmd/shirokuma doctor --profile local-experiment --output json`
also returned healthy with exit 0. Its SQL check is informational; the real
three-DB execution smoke above supplies separate SQL evidence.

This boundary has no routes or enabled product capabilities. Ingest/WAP/export
adapters, atomic upsert/replace orchestration and full UI/CLI same-name acceptance
remain subsequent P3 work. This report does not claim Phase 3 completion.
