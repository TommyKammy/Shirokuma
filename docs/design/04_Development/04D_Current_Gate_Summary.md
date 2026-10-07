---
project: Shirokuma
doc_id: "DEV-04D"
title: "Current local gate summary"
status: active
created: 2026-10-06
updated: 2026-10-07
version: "1.2"
area: development
tags: [shirokuma, local, handoff, gates]
---

# Current gate summary (dated 2026-10-07; re-observe before acting)

**Priority:** L0 foundation, especially
[`WP-L0-GITOPS-001`](../06_WorkPackages/L0/WP-L0-GITOPS-001_OpenTofu_and_Flux_bootstrap.md).
The integrated plan's L0→L1→L2 sequence and actual gate measurements still
matter. ADR-0034 permits local experiments across dependency-ready WPs,
but that flexibility does not prove the L0 gate was passed.

**2026-10-07 authorized inspection follow-up:** the former host-read blocker
is resolved in the owner-authorized parent context; dedicated stopped-disk
copies and checkout-scoped evidence are available under
`tmp/local-experiment/lab-inspection-20261007/`. All eight copy sets match
source data extents/hole maps; root/data ext4 are clean. K3s SQLite **with WAL**
is readable/integrity `ok`. Fifty-eight containers are stopped/restart=no;
nineteen named Docker volumes and two retained PVC directories are mapped.
The k3s-disabled inspection boot succeeded with no containers running, then
the VM was stopped and source profile configuration remained unchanged.
File-level recovery was rehearsed; full VM/application restoration is not
claimed. See [DEV-04E](04E_Dedicated_Lab_Inspection_and_Recovery.md).

**API-only follow-up:** a transient k3s stage with agent/scheduler/controller
manager/cloud-controller disabled returned `/readyz=ok` with zero containers.
Original resource snapshots were saved; all eight Flux resources and three
completed Jobs are now suspended, four application workloads and four Flux
controllers have replicas=0. Eleven stopped app/Flux Pod objects were retired
with UID preconditions; both PVs and both PVCs retain their original UIDs.
The API-only service and VM were normally stopped afterward.

**Next runtime boundary:** recheck persisted inhibition, then observe dedicated
node and selectively enable the four pinned Flux controllers while sources,
child reconciliations, retained application writers and completed Jobs stay
inhibited. Normal Node/Flux readiness is still **unverified**. Do not replay
saved requests, blindly restore disks/original writer counts, or restart the
old stopped batch. Exact metadata rollback is recorded in DEV-04E.

**Earlier batch observation (preserved, superseded for inspection access):** all listed Colima profiles,
including dedicated `mac-studio-solo`, are Stopped. Host free space is about
2.22 TiB; four owned metadata dumps match the saved baseline, but VM/PVC
retention and recovery are not established. Batch `gate-188rjc1d` attempt 001
is **NO-GO**: profile-config inspection was permission-rejected; current guest
writers/restart policies, saved-request disposition and prior live Flux scope
remain unknown. No bypass or VM start; no fresh Node/Flux/doctor readiness.
See the WP's 2026-10-07 inventory and private
`tmp/local-experiment/autonomous-runner/gate-188rjc1d/attempt-001/l0-inventory.json`.
Stop this batch; resume only with approved dedicated inspection and owned
retention/recovery evidence. Never reset the VM to fake full-nuke acceptance.

**Next bounded question:** can the dedicated lab safely provide fresh Node,
four pinned Flux controller, Source/Kustomization and doctor observations for
one approved local source revision? First produce an inspect-only inventory
with exact owned resource identities, stale/unknown requests and rollback
boundaries. If this preflight passes, perform only a scoped live check. If it
fails, record the blocker and stop or choose another specific gate dependency.
Do not restart unknown writers, consume retained data, waive stale strict
publisher decisions or claim acceptance from synthetic fixtures.

**Known open boundaries:** original L0 GitOps WP is `local-partial`; its eleven
checkboxes remain unchecked. The full-nuke gate is outside standing authority.
FinOps AgentHouse retained-volume bytes are `null / NOT_MEASURED`, warehouse
unknown-request recovery and whole-catalog/column lineage remain limited, and
operator/production acceptance is not established. Some WPs have narrower
historical live functional evidence; do not turn current stopped-lab status
into a claim that none ever existed.

**Runner navigation:** [DEV-04C](04C_Gate_Driven_Local_Continuation.md)'s
2026-10-07 allocation supersedes the old active-500 note. The private handoff
reports the source series reconciled; this attempt does not independently
re-prove that history. The held automatic 1000-extension remains unauthorized
to restart. Current counts/outcomes require private source results and exact
server-session reconciliation, not this summary. No later batch is scheduled
by a checkpoint or commit; the current L0 batch stops at the safety boundary.

**Evidence pointers:** source milestones in
[`061_Local_Experiment_Milestones.md`](../06_WorkPackages/061_Local_Experiment_Milestones.md),
the relevant WP, accepted [ADR-0034](../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md),
and the existing private Vault handoff snapshot. Read dates/targeted ranges;
these pointers are not replacement test results. Do not publish private logs.
