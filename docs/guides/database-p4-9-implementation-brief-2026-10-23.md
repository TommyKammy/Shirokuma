# P4-9 implementation and technical acceptance brief

P4-9 combines the P4-1 through P4-8 contracts through normal Workbench and CLI,
authenticated HTTP, and real Trino/Polaris/S3. The acceptance authority is
`database-phase4-implementation-plan-2026-10-20.md`, P4-A01 through P4-A18.

## Bounded implementation

- Verify typed DB ownership throughout the existing operator recovery graph.
  Preserve the canonical legacy NULL proof; reject a conflicting non-NULL DB.
- Formalize recovery of a managed single-backend read-only Job when terminal
  metadata is unavailable and the owner's grant epoch changes. Preserve the
  historical UNKNOWN outcome, immutable execution context, and no-replay rule.
  Current platform-operator authentication remains mandatory. Recovery must
  precede any generation-changing regrant apply.
- Review any newly discovered source gap before extending the worker scope.
  Uncertain writes, distributed/external backends and other DataOps recovery
  remain outside this contract.

## Separate live gates

1. Preserve the existing six catalog definitions, old retired DB, historical
   fenced UNKNOWNs, disabled schedules, original credentials/client profiles,
   and the two user SQL worksheets. Detached private baseline is captured in
   `var/shirokuma/p4-9-acceptance/private/baseline/`.
2. Create owned same-name fixtures across three READY DBs. Exercise ingestion,
   Stage scan/dedup/change, Mart snapshots/model, Job, natural schedules/alert,
   CSV/JSONL export, permissions and normal CLI plus browser operations.
3. Verify definition/grant/epoch/intent/run/UNKNOWN/schedule/retired persistence
   across normal resident restart and saved full plan/apply redeployment.
   Retire only a new disposable DB and retain its physical data/history.
4. Build a fresh dedicated k3s container, network and volume named
   `shirokuma-p49-clean-20261023`. Explicit private kubeconfig uses the existing
   supported context label; API/CA/node UID and empty storage prove physical
   separation. Never alter global kubeconfig or reuse the stopped P2-6 volume.
   Fresh private state/material, canonical bootstrap/full Operator plan/apply,
   owned Workbench/native ports 18888/18080, both generation attestations,
   new definitions and a new retirement form an independent clean gate.
   Empty-state restoration of old retire tombstones is not a DR acceptance.
5. Reconcile P2-R01 through P2-R03 and P4-A01 through P4-A18 with explicit
   historical versus fresh evidence. In particular, re-probe the quoted native
   identifier compatibility carryover against the real engine and REST names.
6. Run focused, configured full, Go, design and doctor checks, independent real
   SQL, owned-resource cleanup and final baseline comparisons. Preserve raw
   first-failure evidence; never fabricate QueryInfo or infer closure from
   absence, TTL or a PID.

Technical acceptance does not replace Phase 5 owner/operator usability approval.
No external publication, push or PR is part of this local task.

## Final-browser follow-up: stale status error

Actual restart testing found that Database-mode `refreshStatus` retained a visible
`Failed to fetch` after a successful current-identity status refresh. Implement
only the current-success error clearing in `tools/local_experiment/workbench/index.html`
and meaningful recovery/stale-identity regression coverage in
`tests/test_workbench_database_ui.py`. Preserve the existing identity fence,
failure reporting, SQL drafts, role/context choices, and all unrelated changes.
Use one bounded `gpt-6.1-sol` Medium CLI worker; no UI redesign, new framework,
backend changes, or extra files. Verify focused UI tests, the standard full gate,
and actual browser recovery plus original two-worksheet preservation before closeout.
