---
project: Shirokuma
doc_id: "DEV-04C"
title: "Gate-driven local continuation after the 500-session series"
status: accepted
created: 2026-10-06
updated: 2026-10-07
version: "1.1"
area: development
tags: [shirokuma, local, gates, development]
---

# Gate-driven local continuation

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
