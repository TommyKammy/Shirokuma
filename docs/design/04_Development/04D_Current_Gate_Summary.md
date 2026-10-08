---
project: Shirokuma
doc_id: "DEV-04D"
title: "Current local gate summary"
status: active
created: 2026-10-06
updated: 2026-10-08
version: "1.26"
area: development
tags: [shirokuma, local, handoff, gates]
---

# Current gate summary (dated 2026-10-08; re-observe before acting)

**Priority:** L0 foundation, especially
[`WP-L0-GITOPS-001`](../06_WorkPackages/L0/WP-L0-GITOPS-001_OpenTofu_and_Flux_bootstrap.md).
The integrated plan's L0→L1→L2 sequence and actual gate measurements still
matter. ADR-0034 permits local experiments across dependency-ready WPs,
but that flexibility does not prove the L0 gate was passed.

**2026-10-08 isolated build (`gate-xezx_z6k`, parent review pending):**
source-controller v1.9.6 commit456fa323/tree8c7afc98 pinned before dependency-only
patch; one owned0600 nonexecuted static Linux/ARM64 binary. Go/Syft confirm six
requested versions. Upstream15 packages/482 test+subtests and API12 pass;
signature-unit attempt terminated on monitor race, envtest/OCI integrations excluded.
Fresh offline binary-derived SBOM scan317 Go packages: HIGH8/CRITICAL0, all HIGH
stdlib1.26.5 (DB lists stable1.26.6, unbuilt); lower/unknown findings remain.
7.598GiB retained, insufficient safe headroom for new toolchain/build with no
cleanup; parent conservative7GiB guard remains held. Scope all profiles freshly
Stopped, ledger/pins/runtime/old holds unchanged, admission/L0 false. Repository12
focused/design/secret/diff pass; broad verifier has four AF_UNIX path-length errors.
Stop/no_safe_work, no later batch scheduled. Exact hashes/source/patch/tests/DB/
failed reports in `tmp/local-experiment/flux-local-build/build-aoa39q7w/`;
reproduction/base/image/publisher gaps in `tools/local_experiment/flux_local_build.md`.
Original Vault sync pending; preserve caches/source/binary/failures, repository-only
rollback. This does not authorize image assembly or runtime activation.

**Parent reobservation and bounded second repair:** exact server
`ses_ee5c3d00effelBEHNEk0S2SWl7` independently succeeded/idle/non-active, valid
no_safe_work, clean checkout. Parent verifies ELF/hash, recorded source identity,
patch/go.mod/go.sum/SBOM/scan hashes and exact8 HIGH stdlib1.26.5 findings listing
stable1.26.6. Observed max logical/allocated bytes **8,157,929,472 (7.598GiB)**,
below hard8GiB but above unchanged conservative7GiB guard; original HELD capacity
summary remains intact. No additional writes to that old scope or cleanup.

`--resume-toolchain-repair` selects the second/final unchanged-approach execution:
stable Go1.26.6, same pinned source/dependency patch, fresh private build scope,
copy minimum source/read old inputs only; no source-tag refetch, old cache writes
or terminated signature-test replay. Each mission max8GiB, combined at most16GiB
with all old bytes retained, free16GiB required; not a quota/counter reset. Exact
old session/CP/result/binary hash separately reviewed before successor. Original
week/OAuth/600 and two-execution limits persist. Starts515/600, remaining85 before
launch; successful Go1.26.6 build/clearance/image/publisher acceptance unverified.

**Candidate result and next repair approach:** `candidate-hn94027w` collected
Flux v2.9.6 exact ARM64 children: S/K/H/N HIGH9/5/5/9, CRITICAL0 (28 occurrences).
Current pinned images still have94 HIGH; the candidate comparison is **not** an
applied mitigation. Four index/child bindings, scans/SBOMs and DB times retained;
publisher unverified, admission false, existing pins/ledger/runtime unchanged.
Public same-series controller tag lists independently show no newer stable
patch tags for all four components. Do not repeat candidate scans to fill slots.

Next `flux_local_build_supervisor.py` selects a materially different repair:
ONE isolated source-controller v1.9.6 patched Linux/ARM64 binary, pinned upstream
source, corrected module versions, tests/build/SBOM/scan evidence. One model
session/120minutes, at most two persistent reservations, original week/600/OAuth
unchanged. Temporary private module/toolchain caches only; no global install,
image execution/image assembly, VM/container start, pin/ledger changes, risk
approvals or runtime successor. Separate build-review binds known original holds
and candidate-summary hash; parent inspects ELF/Go metadata, not operator/image
acceptance. Before launch usage remains514/600, remaining86; launch/result separate.

**Parent review and next measurable dependency:** `gate-3f5odnk2` is independently
succeeded/idle/non-active (`ses_ee5db2f1affem4NZko8BBCpwyA`), valid no_safe_work,
clean HEAD `66c8cb553ceebd6aa36e2c2a64b248cb9415387e`. Parent summary is
`FRESH_SCAN_EVIDENCE_ONLY`; independent reobservation with the launcher's Python
environment reproduces exact hashes/HIGH counts/DB times, ledger unchanged and
all profiles Stopped. Starts **514/600, restart14/100, remaining86**.

Anonymous public metadata discovery found stable **Flux v2.9.6** (published
2026-10-01), with S/K/H/N tags v1.9.6/v1.9.6/v1.6.5/v1.9.4. These are observed
release metadata, not accepted pins/publisher proof. Private records:
`tmp/local-experiment/flux-pin-discovery/discovery-yp11cspa/`.
Next `flux_pin_candidate.py` resolves those tags once to immutable parent indexes
and unique ARM64 child digests, then scans only the children with isolated
installed tools. Zero model starts, max120minutes/two persistent reservations,
VM stays stopped; no current ledger/pin change, approval or runtime successor.
Candidate scan/publisher/fixed-package results remain unverified until collection.

**2026-10-08 exact-digest refresh (`gate-3f5odnk2`, parent-reviewed evidence only):**
All four unchanged ARM64 Flux digests freshly scanned06:15:41–06:16:08UTC,
Trivy0.72.0; DB UpdatedAt2026-10-07T07:38:55UTC/download06:15:35UTC today.
HIGH21/36/18/19, CRITICAL0:94 occurrences/42 IDs versus retained25/10.
Four new Syft SBOMs/config observations separate; publisher proof not refreshed.
VM/all profiles observed Stopped, isolated private cache/reports retained;
approximately1.343GiB/free2272GiB. Ledger/pins/security originals/old holds/counters
unchanged, strict rejection/riskDecision null, admission/full L0 false. Parent
validation and separately scoped fixed-package immutable-pin/publisher-binding
remediation required; no runtime successor/transport/apply/activation authorized.
Private evidence, precise times/hashes/recovery in newest WP and flux_refresh_scan.md;
Vault sync pending. This replaces stale-scan freshness only, not risk acceptance.

**2026-10-08 Flux finding diagnosis (`gate-5d3ur67c`, parent review pending):**
Exact10 IDs/25 HIGH occurrences, installed/fixed packages and conditional exposure
documented in `tools/local_experiment/FLUX_FINDINGS.md`; S/K/H/N HIGH7/6/6/6,
CRITICAL0 unchanged. DB55.201/scan55.012 days old at06:00:32UTC. Fresh live51
readiness/inhibition/retention passes and namespace network objects observed;
no reachability/exploitability/enforcement claim. Concrete held dependency:
separately scoped current evidence refresh for four exact ARM64 digests or new
fixed-package proven immutable Flux pins; no safe replacement digest established.
No scan/fetch/start/runtime mutation/approval/exception/activation; riskDecision
null, strict rejection/full L0 false. Focused41 pass; broad policy11failures/2errors
and doctor degraded remain. Final06:05:08UTC live51 passes; UIDs/non-Lease specs,
full Flux/PV/PVC/network unchanged. Refresh/upgrade outside this mission remains
held; worker stops/no_safe_work, not full gate completion. Old exhausted scopes
held; Vault sync pending.
Leave safe runtime for independent parent probe/idle-worker cleanup. No gate pass.

**Parent outcome and next capability:** the finding worker is independently
succeeded/idle/non-active (`ses_ee5e88acdffeURkr5O3N7Am1RT`), valid stop/no_safe_work,
clean descendant HEAD `d032eedc74cafea139219fea9b24191541fced8b`. The parent had
already passed fresh live/doctor/progress observations but incorrectly sent its
finding hold through the exhausted-admission transition, yielding
`INDEPENDENT_SCOPE_UNVERIFIED`; this is a planner defect, not risk acceptance.
Original series/worker evidence stays intact, dedicated and other profiles
independently observed Stopped. Usage **513/600, restart13/100, remaining87**.
New `flux_refresh_supervisor.py` selects an evidence-only installed-tool refresh
of four existing ARM64 digests, public read-only retrieval, isolated local cache,
VM stays stopped. No approvals/exceptions/replacement pins or runtime activation.
Old scopes/counters remain held; parent validates fresh identity/DB/timestamps
separately from vulnerability rejection. Activation/outcome are separate records.

**Continuation outcome:** the resumed loop ran six workers and passed five
parent reviews/automatic successors. `gate-azr7paf4` then checkpointed
`stop/no_safe_work`; parent classified it unsupported and stopped the VM.
Original summaries/checkpoints/counters stay intact. Usage: **512/600 starts,
restart12/100, remaining88**. The next supported transition preserves exhausted
admission-contract work as held and separately diagnoses exact Flux HIGH IDs,
affected/fixed versions, exposure and stale evidence. It requires fresh parent
runtime/doctor/terminal review, never grants risk approval, and does not reset
the old counters. Strict HIGH rejection and riskDecision=null remain.

**2026-10-08 complete evidence contract (`gate-azr7paf4`, parent review pending):**
Complete builder/validator bind full inhibited candidate0486612d, regenerated
inventory and nine occurrences/eight references with commit-bound ARM64/SBOM/
publisher/scan records, exact findings and 19 unavailable Secret references.
Rehashed omissions/approvals and fresh inhibition drift reject. Strict Flux
HIGH7/6/6/6 remains blocked, risk decision missing/null, all acceptance/activation
false. Fresh live51 final05:41:20 UTC passes; UIDs/non-Lease specs/full Flux/PV/PVC
unchanged, contract revalidated, doctor degraded. No start/runtime writes/fetch/
transport selection/policy activation. Admission22/render11/Go pass; policy11
failures/2 errors and prior stops retained. Proposal006 requests only concrete
HIGH/freshness risk-disposition diagnosis for parent review under existing limits.
Evidence/rollback in newest WP/Milestones; Vault sync pending. Leave safe runtime
for parent probe/idle-worker cleanup; never restore writers/unsuspend sources/Jobs.

**2026-10-08 complete image diagnosis (`gate-vq8p7nl2`, parent review pending):**
Nine occurrences/eight distinct references at candidate0486612d have commit-bound
ARM64 ledger/SBOM/publisher/scan evidence. Five non-Flux occurrences pass retained
strict scans HIGH0/CRITICAL0; Flux HIGH7/6/6/6 remains rejected. Five Secret
identities/19 references unavailable in repository renders, not proven live absent.
No fresh crypto/image bytes/advisory rescan; local risk decision missing/null,
admission/activation/gate false. Live51 latest05:33:13 UTC passes with unchanged
UIDs/non-Lease specs/full Flux/PV/PVC objects; doctor05:33:16 degraded. No startup,
runtime writes/fetch/transport selection/policy activation. Admission19/render11
pass; policy11 failures/2 errors and prior stops remain. Proposal005 requests only
complete-inventory non-activating evidence-contract extension for parent selection.
Private evidence/rollback in newest WP/Milestones; Vault sync pending. Leave safe
runtime for parent probe/idle-worker cleanup, never restore writers/unsuspend Jobs.

**2026-10-08 transitive inventory (`gate-r_2f0fyd`, parent review pending):**
Commit-only inventory binds candidate0486612d, 33 files/seven paths, 57 resources,
nine workloads/images and inhibited projection hashes; repository scope complete,
runtime inputs incomplete (five absent Secret identities/external image bytes).
Bound contract preserves HIGH7/6/6/6, CRITICAL0, missing/null risk decision and
admission/activation/gate false. Latest live51 05:26:40 UTC passes, UIDs/non-Lease
specs/full Flux/PV/PVC objects unchanged. No startup/runtime write/fetch/transport
choice/apply/policy activation. Doctor degraded and policy11 failures/2 errors
remain. Proposal004 requests only read-only local admission diagnosis of all nine
images and missing inputs; parent must independently review/select. Private
evidence/rollback in newest WP/Milestones, Vault sync pending. Leave runtime for
parent probe/idle-worker cleanup; never restore writers/unsuspend Flux/Jobs.

**2026-10-08 local evidence contract (`gate-5djsi8d4`, parent review pending):**
Non-activating builder/validator bind full inhibited candidate, four ARM64 image
pins and candidate-commit SBOM/publisher/scan blobs/hashes to exact HIGH7/6/6/6
and CRITICAL0 records. Strict rejection preserved; no approval/exception renewal.
Risk decision/transitive render inventory missing/null; admission/gate/activation
false. Fresh live51 latest 05:15:03 UTC passes with unchanged UIDs/non-Lease specs
and full Flux/PV/PVC objects, contract revalidated. Already-running runtime left
for parent probe; no startup/mutation/transport choice/policy activation. Doctor
degraded and policy11 failures/2 errors remain. Proposal003 requests only
repository-only transitive render/workload/image inventory for parent selection;
risk review/fetched/enforced/applied provenance and full L0 remain open. Tests and
private evidence/rollback in newest WP/Milestones; Vault sync pending. Never
restore app counts or unsuspend Jobs/sources automatically.

**2026-10-08 local admission diagnosis (`gate-n0xgt8ew`, parent review pending):**
Exact inhibited candidate/live guards and four ARM64 ledger/inventory pins match;
bootstrap admission passes. Strict retained scans fail HIGH7/6/6/6, old local-lab
expiry remains rejected. Publisher record pass is not fresh cryptographic proof
(Flux versions do not enter canonical retained-bundle checks). Existing owned
native policy8/binding8 matches content/typecheck, unchanged: `shirokuma-dev`
digest syntax only, not Flux/publisher/CVE/source admission. Missing local-only
risk decision, transitive render/image inventory, fetched bytes/commit/transport
and scoped enforcement/applied provenance remain. Proposal002 requests only a
non-activating local evidence contract for parent review. Fresh live51 latest
05:09:29 UTC passes inhibition/readiness/retention; no startup/runtime changes.
Doctor degraded, policy11 failures/2 errors and security5 failures preserved.
No gate/checkbox/operator acceptance. Runtime left for parent probe/idle-worker
cleanup; never restore writers/Jobs/sources. Private evidence/rollback in newest
WP/Milestones; Vault sync pending.

**2026-10-08 inhibited candidate (`gate-9w3pac_6`, parent review pending):**
Repository-only contract binds available full commit `0486612d`/tree and root/six
child paths, source/root/children suspended, four existing pinned controllers1,
private UID/spec guards. Transport unselected; no fetch/apply/redirect/unsuspend
or policy activation. Candidate identity is not fetched/applied provenance;
existing branch-only root remains unsafe to apply. Fresh live51 probes pass on
already-running lab, final 04:59:46 UTC, UIDs/non-Lease specs and Flux/PV/PVC
objects unchanged. Doctor still degraded; policy11 failures/2 errors preserved.
Next proposal is read-only local_admission/diagnose against strict publisher
identity requirements; parent must independently review before selecting it.
No L0/operator acceptance or checkbox change; Vault sync pending. Runtime left
available for parent probe/terminal-worker cleanup, never restore writers/Jobs.

**Transport reconciliation follow-up:** doctor worker `gate-uc9gsp8y` passed
parent review and the continuation automatically selected source diagnosis.
`gate-x1girm_f` then exited CLI with WebSocket code1000, but exact server session
`ses_ee62f34cbffejRW24zoADBOUrn` is independently succeeded/idle/non-active.
Its valid continue/more_work checkpoint HEAD is
`e147d0a4c2c2c10b44ef14f9fba65ff84f3cbbfd`; checkout is clean. CLI summary stays
`OPENCODE_RUN_FAILED`, runner result remains missing. The new transport-only
reconciliation route preserves these originals and requires fresh parent
runtime/doctor/proposal review before the next repository-only source candidate
mission. Current starts: **506/600, restart6/100, remaining94**; no deadline renewal.

**2026-10-08 source diagnosis (`gate-x1girm_f`, parent live review pending):**
Fresh inhibited live51 probe passes; existing VM/node/controllers were already
running and no runtime mutation/start was performed. Source Ready generation3
does not refresh ArtifactInStorage generation2; root/all six child Kustomization
Ready conditions remain stale. Source/children `f0d804b9`, root `59f38dc2` exist
as local ancestors, not current observation HEAD `77748a96`. All seven configured
path trees equal HEAD, not full provenance or transitive-render equality. Offline
root render omits all eight Flux suspensions and retains external branch-only
main: do not apply/unsuspend it. Next proposal is a repository-only pinned,
inhibited local-source candidate contract; parent must review/select the mission.
Runtime remains available for parent inspection/terminal-worker cleanup. Full L0,
artifact bytes, fetched HEAD and operator acceptance remain unverified; doctor/
policy failures and Vault sync pending remain. See newest WP/Milestones entry.

**2026-10-08 doctor worker diagnosis (`gate-uc9gsp8y`, parent review pending):**
Live kubectl aggregate `List/v1` has empty list-level resourceVersion; doctor
wrongly called complete responses incomplete. Bounded validator repair and
regressions pass; live doctor now explains suspended Flux/app replicas0 while
remaining degraded. Fresh worker readiness/inhibition/PV/PVC probe passes;
warmup failures preserved. No source/Job unsuspension or resource patches.
Runtime left available for parent independent review; do not infer current
VM state from earlier Stopped notes. Next proposed dependency is read-only
source identity: generation-current Ready on suspended GitRepository does not
make older ArtifactInStorage/root/child applied revisions current provenance.
Policy11 failures/2 errors and full L0 acceptance gaps remain. See newest WP/
Milestones for evidence and recovery; original Vault sync pending.

**Continuation scheduling correction:** the finite readiness job had ended,
leaving no process to choose the next gate. `gate_continuation.py` now provides
the post-readiness parent loop, initially investigating degraded doctor and
source/local-admission dependencies, with independent live reviews between
single-session/120-minute batches. It does not stop on milestone/commit alone.
Activation is recorded separately; ceiling remains 96 starts available at this
boundary, clipped to cumulative600/original week. Initial runtime capability is
observation only: sources/Jobs remain suspended and retained writers zero.
Invalid proposals, exhausted same-approach counters or unsafe/unknown execution
are still genuine holds; no claimed full L0 acceptance or arbitrary-stop repair.

**2026-10-08 parent-verified scoped readiness (04:10:57 UTC):**
The automatic successor actually ran after fresh parent inhibition verification.
Its final safety stop was caused by an invalid 180-second Ready-status cutoff:
Kubernetes normally reports unchanged Node status every five minutes while
renewing the Node Lease every ten seconds. The failed observations remain
unchanged. `3acd617` uses Ready=true plus UID/holder-bound fresh Lease (at most
40 seconds, bounded clock skew) and status age at most 600 seconds, with
negative tests for stale/missing/foreign/future Leases and non-Ready Nodes.

Parent `lease-review-brrwinpx` reopened API-only inspection and independently
verified owner/spec/retention scope before starting the existing node/controller
state. Its fresh 51-object probe passed: Node Ready plus fresh Lease, four
digest-matching available/ready Deployments and matching Ready Pods, suspended
Flux8/Jobs3 and inhibited retained application owners. Docker listed zero running
containers; this is a Docker observation, not a whole-runtime census.
No model session or resource mutation replay was added. Exact source worker
was succeeded/idle/non-active. Parent normally stopped the dedicated VM and
all profiles were independently observed Stopped. Both original HELD summaries
and worker safety checkpoint remain intact; a separate resolution review exists.
Usage: **504/600, restart4/100, remaining96**. The finite invocation has ended,
not an active unattended multi-WP planner. Next gate work is scoped source/local
admission and degraded doctor investigation; no source/Job unsuspension is
authorized merely by these readiness results. Full L0/human acceptance remains open.

**Earlier readiness worker observation (superseded parent-review status):**
`gate-3t2bauf_` follows successful fresh parent API-only inhibition verification
in `loop-82wu_3jx`, then independently rechecks complete owner/spec/UID and
retention scope. API-only stopped cleanly before dedicated normal k3s startup.
Fresh Node Ready and four existing digest-pinned Flux Deployments available/
ready=1 with matching Ready Pods observed at 04:03:03 UTC. Only four guarded
Flux replica fields changed 0→1; all apps/secondary owners zero, Flux8/Jobs3
suspended, retained PV/PVC specs/UIDs, nineteen volumes/two PVC directories
preserved. Eight cold copies freshly match original extent digests. Runtime
left live for independent parent probe and finite-end normal VM stop.
Doctor remains degraded (API/scope healthy, incomplete Flux/app responses);
root/child Ready conditions stale, GitRepository Ready generation advanced but
artifact condition stale. No new source/reconciliation acceptance inferred.
Supervisor13/runner21 tests PASS; broad policy suite still 11 failures/2 errors.
See newest WP/Milestones entry for private evidence and exact rollback. Original
stops/backups remain unchanged; no source/Job unsuspension or writer restore.
This is a narrow worker observation, not independently accepted L0 or a new
batch authorization. Older pending/Stopped notes below are historical, not
current runtime facts. Usage is now 504/600 starts (restart4/100, remaining96)
including this still-active worker; terminal outcome requires parent review.
**Final probe hold:** `NODE_NOT_FRESH_READY` at 04:06:11 UTC: Ready condition
heartbeat 04:02:17 exceeds 180 seconds, although Lease renewTime 04:06:01.642163
is fresh. Earlier readiness pass is preserved but does not waive this failed
final check. No further mutations; parent must resolve freshness before gate
acceptance, and owns runtime cleanup. No gate-pass checkpoint claimed.

**2026-10-08 policy revision:** owner-approved DEV-04C now allows bounded,
reviewed autonomous blocker resolution and new-mission selection without
routine human restart instructions. The ReplicaSet repair below has a worker
completion claim but still needs successful independent parent verification.
Current usage is 503/600 starts (restart 3/100, remaining 97); original week
and all retention/routing restrictions remain. The runner remains fail-closed;
an initial finite L0 parent supervisor is now implemented and tested, with
activation recorded separately. It selects API-only owner-chain repair, probes
real state independently, then selects Node/four-controller readiness only on
verified inhibition (maximum two worker sessions/120 minutes). Unknown worker
outcomes/new safety causes and failed parent probes still hold; this is not yet
a general multi-WP repair planner. See AUTONOMOUS_RUNNER.md for exact limits.

**2026-10-08 repair worker observation (parent review pending):**

The first supervisor held at `API_ONLY_FLAGS_UNVERIFIED`, using the old
in-memory argv-based probe despite the worker's committed probe fix. No
readiness successor ran. Parent cleanup stopped the dedicated VM; all profiles
were independently observed Stopped afterward. The original HELD summary stays
unchanged. The tested completed-repair resume route reopens API-only inspection
for fresh parent verification **without repeating resource mutations**, then
selects readiness only if verification passes. The exact worker server session
is independently reconciled succeeded/idle/non-active.
`gate-8ancmu2h` captured complete owner chains and original secondary owners,
then inhibited six matching ReplicaSets with UID/resourceVersion/replica guards.
Fresh scoped parent/ReplicaSet replicas are zero, scoped Pods absent,
Flux8/Jobs3 suspended, PV/PVC UIDs/specs preserved, zero containers. API-only
`shirokuma-l0-owner-repair-8ancmu2h.service` is left active for the independent
parent probe; ordinary k3s remains inactive/disabled. No Node/Flux/workload
startup, gate acceptance or automatic writer restoration. Parent review is
pending; first-loop cleanup completed and stopped the VM. The left-active
description is the worker handoff, not current runtime state. Private evidence and exact rollback
are in the WP's repair entry. The argv-rewriting probe defect is repaired/tested;
broader policy verification still fails (11 failures/2 errors).

**2026-10-08 fresh startup blocker:** `gate-uklhzopt` re-observed API-only
inhibition with `/readyz=ok`, zero containers, eight suspended Flux resources,
three suspended Jobs, eight zero-replica app/Flux parent workloads and preserved
PV/PVC UIDs. Expanded secondary-owner inventory found Polaris/Trino and four
Flux ReplicaSets each still requesting one replica. Starting controller-manager
and kubelet could race Pod recreation against Deployment scale-down. No node
start/Flux scale was attempted; both transient APIs and the dedicated VM were
stopped. All profiles are Stopped. Sources/Kustomizations remain suspended
with stale Ready conditions; stopped-lab doctor is degraded. **Next start is
blocked on separately reviewed API-only secondary-owner inhibition and complete
owner/Pod checks**, not cleared by the earlier parent-only snapshot. See the
WP's 2026-10-08 entry and private attempt evidence. This batch stops; no automatic
successor or acceptance claim.

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
