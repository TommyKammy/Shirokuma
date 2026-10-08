---
project: Shirokuma
doc_id: "DEV-04C"
title: "Gate-driven local continuation after the 500-session series"
status: accepted
created: 2026-10-06
updated: 2026-10-08
version: "1.5"
area: development
tags: [shirokuma, local, gates, development]
---

# Gate-driven local continuation

## 2026-10-08: reviewed autonomous remediation and continuation

The owner accepted the proposed gate-focused autonomous supervision policy.
This section supersedes the **manual-selection/no-successor scheduling** rules
below, not their safety, evidence or budget requirements. The historical
1000-extension stays unloaded on `POLICY_REVIEW_HOLD`. This is a new finite
local loop, not the GitHub supervisor, a recurring launchd job, an allowance
renewal or permission to blindly restart failed work.

At adoption the reconciled usage is **502/600 cumulative starts, 2/100 restart
starts, 98 remaining**. Recalculate from preserved start evidence before each
launch. Failures, unknown starts and newly started repair/review model sessions
consume the same allocation; supervision is not a free accounting exception.
Keep GPT-6.1 Sol, the original seven-day deadline, single OpenAI OAuth/no API-key
route and no-new-spending boundaries. Each batch remains at most six sessions
and 120 minutes, with separate evidence review between batches.

### State machine and stop classification

`SELECT_GATE -> EXECUTE_BOUNDED_MISSION -> RECONCILE -> REVIEW` then:

| Classification | Next action |
| --- | --- |
| PROGRESSED | Select the next measurable increment or dependency-ready gate |
| REPAIRABLE | Inhibit the unsafe action; investigate/repair within standing authority, verify, then reconsider the gate |
| STALLED | Change the approach or select a genuine gate dependency, not unrelated offline micro-hardening |
| UNKNOWN_EXECUTION | Read-only session/operation reconciliation; no new mutation until outcome is established; never replay the uncertain operation |
| NEEDS_OWNER | Hold the out-of-scope action; ask for the specific missing decision, continuing genuinely independent authorized work if safe |
| EXHAUSTED | Terminate on budget/deadline/quota/routing boundary; no renewal or paid fallback |

Ordinary technical uncertainty and dedicated-lab configuration defects are
development tasks, not repeated owner-approval gates. Unknown ownership or
retention calls for authorized read-only inspection before mutation. An
irreducible safety uncertainty remains a hold, not presumed clearance.
Permission rejection must be preserved: do not bypass access controls, change
global permissions or automatically re-request a rejected operation. Use
already approved accessible evidence or escalate genuinely missing access.
New spending, retained-data/backup deletion, public exposure, GitHub writes and
other out-of-scope actions still require a separate owner decision.

### Review and new missions, not failed-operation replay

The worker must not recursively invoke the runner. A separate parent review
phase verifies the exact worker's terminal outcome/idle/non-active state,
operation disposition, original result/checkpoint, clean descendant HEAD,
budget/deadline/routing and fresh gate evidence before selecting a **new
HEAD-bound mission**. Safety stops terminate the unsafe action/worker, not
automatically the whole development process. In-mission safe repairs may
continue; changed scope needs a new mission. Human restart instructions are
not required for ordinary in-scope investigation, repair or gate transitions.

Do not trust worker prose, commits, checkboxes or checkpoints as sufficient
proof. For runtime repair, independently inspect the complete owner/resource
scope, UIDs, writer state, retained PV/PVC/volume identities and recovery.
Store review evidence separately and preserve original stops/failures. An
attestation's valid form alone does not prove its contents.

A terminal failure permits investigation, not automatic replay. An unresolved
safety stop permits only a bounded diagnostic/repair mission with the dangerous
action prohibited. A resolved stop permits a separately reviewed new gate
mission. These transitions must be implemented/tested before a scheduler uses
them; do not forge a budget stop or resolution attestation to pass the existing
runner, which currently requires resolution before a subsequent gate launch.

### Bounded repairs, progress and owner communication

A terminal `no_safe_work` for the proved exhausted local-admission evidence
contract scope may select exact Flux finding/exposure/freshness diagnosis after
separate parent review. Preserve the old no-safe scope and its two counters;
the new question must be materially different and supported by retained HIGH
findings and the completed contract, not a cosmetic rename. The implemented
independent gate permits no approval/exception/riskDecision change, fetch/rescan,
policy activation or runtime mutation. It has its own bounded counters and
cannot reopen the old scope merely through a proposal. Unknown no-safe causes
remain holds. See the exact independent-review contract in AUTONOMOUS_RUNNER.md.

- Track stable blocker identity (gate, root cause, affected resources), attempts
  and outcomes across batches. At most **two repair execution sessions per
  unchanged blocker/approach**; then require a materially different hypothesis
  supported by observations or hold that gate. Cosmetic changes/renaming do
  not reset the counter. Do not spend the remaining allocation unconditionally.
- Measure complete prerequisite validation, verified risk inhibition and live
  gate observations within their actual scope. Offline tests/commits alone are
  not runtime readiness, production/human acceptance or completion percentages.
- Report milestones, changed risks, holds and termination without asking for
  another restart command. Request owner intervention only for missing authority
  or irreducible safety uncertainty that cannot be resolved within scope.
- Keep a compact private decision record: gate/blocker ID, baseline,
  classification, prior run/session/HEAD, operation outcomes, allowed next action,
  prohibited actions, verification, recovery, attempt count and remaining budget.
  Keep raw logs, credentials and retained data out of Git.

### Adoption is not operational proof

Post-readiness implementation: `tools/local_experiment/gate_continuation.py`
adds evidence-reviewed next-mission selection instead of terminating after two
fixed stages. One worker/120-minute batch at a time under the remaining allocation
and original deadline; milestone reports are informational, not stop conditions.
Initial capabilities cover L0 doctor/source-identity/local-admission diagnosis
and repository repair only, with runtime sources/Jobs/app writers inhibited.
Typed next-mission proposals cannot widen capabilities or supply parent shell
commands. Real API/doctor observations, terminal worker state, descendant HEAD
and fixed relevant tests precede successors. Persistent two-reservation limits
apply to each topic/approach; stalled/exhausted/unsafe work holds. This is not
blanket arbitrary-WP planning or automatic unsuspension. The original finite
repair/readiness supervisor remains available but is not the ongoing planner.

Initial implementation: `tools/local_experiment/gate_supervisor.py` implements
the finite owner-chain repair -> independent live verification -> Node/Flux
readiness sequence, using at most two single-session batches in one 120-minute
invocation. Terminal unresolved safety stops are admitted only to a HEAD-bound
repair-only mission; a resolution review is generated only after the parent
checks real API state. Repeated approach reservations are counted across
invocations. Arbitrary new stops/probe failures remain holds, not automatically
classified as safe repairs. See AUTONOMOUS_RUNNER.md for exact capability,
verification and cleanup contracts. This is an initial scoped implementation,
not the entire multi-WP planner; runtime activation is a separate observation.

This policy authorizes implementing and using the finite reviewed loop within
the current allocation. **It does not mean an unattended supervisor is already
implemented, tested or running.** At adoption both L0 batches are stopped and
no supervision process is active. Validate exhausted budgets, active/unknown
sessions, stale reviews/HEADs, repeated blockers, denied access, unsafe repairs
and valid new-mission transitions before claiming unattended continuation.

The immediate dependency is the API-only Deployment/ReplicaSet/Pod owner-chain
inhibition found by `gate-uklhzopt`: capture original identities/state, inhibit
known secondary owners with UID/version guards, then independently verify all
owners/Pods and retained volumes before node startup. Do not start kubelet to
test whether the race occurs; preserve the original safety stop.

## 2026-10-07 restart allocation

The owner requested resumption with **100 new session slots**, not a 100-session
unreviewed run. The previous series ended at 500; this allocation stops at
**600 cumulative starts**, counting failures and unknown starts. The older
1000 ceiling and held successor do not authorize more than these 100 now.
Primary sessions use `openai/gpt-6.1-sol`. The source run's 240 results and
checkpoints, descendant history and final specific server session were
reconciled as succeeded/idle/non-active before activation. Historical missing
results remain missing. The original seven-day deadline still applies.
Select batches of at most six/120 minutes and review gate-linked functional
evidence between them. Do not auto-spend the remaining 100 slots or restart
an unknown request; the original automatic extension job stays unloaded.

This replaces the unbounded *scheduling policy* after the running 500-total
series ends. Do not edit its checkout or interrupt a live OpenCode request.
Its current `run-qjiwuw2k` keeps its original per-run bound and stop behavior;
the previously prepared 1000-total one-shot extension was **put on hold** on
2026-10-06 and is not a license to start another 500 sessions. The historical
owner ceiling of 1000 is a maximum, not a target or an automatic restart.

## Mission and batch contract

1. Reconcile the old run first: exact summary/last result and checkpoint,
   specific OpenCode session outcome/idle state, clean descendant HEAD, original
   seven-day allowance, OAuth-only route and cumulative started-session count.
   If any state is unknown or failed, do not replay it or start a fresh run.
2. Select **one named WP and one measurable gate blocker**. Record the baseline,
   explicit evidence to collect, allowed runtime scope, stop conditions and
   rollback. The default next mission is the L0 GitOps/Flux readiness and
   bootstrap investigation described in the [current gate summary](04D_Current_Gate_Summary.md).
   A finished or locally verified WP is not a source of unlimited routine
   edge-case work; only a reproduced material regression or a gate dependency
   justifies revisiting it.
3. Start at most **six fresh sessions per manually selected mission batch**;
   the prepared runner also caps each batch at 120 minutes. Choose fewer when
   runtime changes or retained data are involved. A commit,
   passing unit tests, green documentation checkboxes and `more_work` are not
   independently measured gate progress. They do not automatically schedule
   another batch. No automatic continuation job is installed for this mode.
4. At the batch boundary, separately review recorded functional evidence
   against the mission's concrete gate: progressed, safely blocked, or not
   progressed. If there is no verifiable gate-linked change, stop or change
   approach; do not convert uncertain state into a `complete` claim. New batches
   require a new bounded goal, independent review of the previous results and
   the same routing/time/retention preflights. This is a technical safety and
   planning gate under the existing owner approval, not a GitHub or per-attempt
   approval ceremony.

The next runner launch remains **disabled by default**: it needs a private
review attestation tied to the last source HEAD/session, a fresh HEAD-bound L0
mission, and a clean checkout. The code checks its *form* and the local run
summaries, cumulative scheduling count and original week; it cannot infer
whether the attested server outcome, lab observations or evidence are true.
Validate those separately before writing the attestation. Future prior
nonbudget/unknown run states fail closed rather than auto-restarting. See
[`AUTONOMOUS_RUNNER.md`](../../../tools/local_experiment/AUTONOMOUS_RUNNER.md)
for the private contract. Do not install a successor launchd job.

## L0 before further breadth

- Prioritize `WP-L0-GITOPS-001` and its prerequisites: dedicated Colima/k3s
  Node readiness, pinned four Flux controllers, Source/Kustomization status,
  local admission identity and `shirokuma doctor` evidence. The historic
  GitHub publisher/strict admission gate remains failed where expired; a
  bounded local admission alternative must be labeled as such.
- Inspect `colima list`, dedicated profile identity, storage/backups, disk
  headroom, known workloads, Flux reconciliation scope and recovery **before**
  any VM start. If retained state or writer ownership cannot be established,
  record the blocker and stop only that action. Never reset all Colima,
  destroy retained data or replay an unknown database/HTTP operation.
- A scoped start of the dedicated lab is within ADR-0034's local authority
  after those checks. The integrated plan's *full nuke/rebuild* L0 criterion
  is **not** within the standing deletion authority. Mark it blocked rather
  than treating an ordinary restart or offline test as equivalent.
- Other independent, dependency-ready work remains possible if L0 live work is
  unsafe. It still needs its own named gate and batch evidence. Do not infer
  production or human acceptance from a local functional result.

## Evidence, context and other scopes

Read [the short current-gate summary](04D_Current_Gate_Summary.md) first, then
the relevant WP/ADR and **targeted sections** of
[`061_Local_Experiment_Milestones.md`](../06_WorkPackages/061_Local_Experiment_Milestones.md)
and the private handoff snapshot. Do not paste or reread the entire growing
evidence ledger every session. The ledger, original Vault, historical failures,
worktrees, backups and private session results remain retained; archive or
summarize via links and dates, not deletion or retroactive rewriting. A short
summary is navigation, **not** independent functional evidence.

GitHub `main`/issue-63 and publisher image alternatives are a separate,
currently unauthorized external-delivery path. Record an explicit risk/ADR
decision before any local pinned fallback, and do not create an Issue/PR,
push or publish in this loop. The 74 observed worktrees are not disposable
merely because of their count; inventory ownership before any cleanup.

The OpenAI OAuth label and model catalog are routing checks only. Keep the
original seven-day guard and stop on quota, account change or signs of metered
API use; no spending or weekly-reset renewal is authorized. The session budget
is not a product-completion percentage.
