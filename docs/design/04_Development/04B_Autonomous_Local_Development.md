---
project: Shirokuma
doc_id: "DEV-04B"
title: "Autonomous Local Development"
status: accepted
created: 2026-09-16
updated: 2026-10-03
version: "1.2"
area: development
tags: [shirokuma, local, development]
---

# Autonomous Local Development

Current mode: **local experimental development**, approved by the owner on
2026-09-16 under [ADR-0034](../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md).
Do not create Issues or PRs, write to GitHub, publish externally, or wait for a
fresh approval on an operation already inside that standing scope.

## Loop

1. Read the relevant WP and dependencies, root/scoped AGENTS instructions and
   related design records. Check current code and lab state; dated notes are
   evidence, not a substitute for inspection. Pick a bounded useful increment.
2. Use a dedicated local branch/worktree and preserve existing user work. State
   the functional result to verify and note assumptions or design decisions.
3. Reproduce the gap where useful, implement, and run focused checks. Add tests
   for meaningful new behavior or regressions; avoid tests that only duplicate
   document wording or reversible cosmetic changes.
4. Apply and verify in the dedicated Shirokuma lab when needed. Pin the input,
   record any local admission/Flux override and its recovery path, and keep
   changes outside unrelated scopes. Retry local repairs without renewed owner
   approval. Recreate only disposable data generated for these experiments.
5. At a meaningful milestone, run `make verify-local-experiment` and relevant
   integration/component checks, inspect substantive local review findings,
   and fix defects that affect correctness, security or data retention.
6. Record WP ID, change/decision, commands/results, runtime scope, evidence
   location, recovery steps and remaining gaps in the WP and repository notes.
   Commit the coherent local change and continue to the next dependency-ready
   increment. Report results and unresolved items at milestones.

Local completion means the recorded functional acceptance criteria passed in
the stated lab scope. Report untested, blocked and incomplete criteria directly;
external publication and production/operator acceptance remain separate.

An optional bounded OpenCode continuation runner is documented in
[`AUTONOMOUS_RUNNER.md`](../../../tools/local_experiment/AUTONOMOUS_RUNNER.md).
It starts fresh sessions from this dedicated worktree using this loop and the
current WP/Milestones/Vault handoff, then requires a clean local commit and a
private continuation checkpoint before another call. It defaults to a zero-call
plan; execution requires explicit run/time bounds, a selected OpenAI model and
the owner's 2026-10-03 permission to use the Pro weekly allowance. The runner
fails closed if it cannot see an unambiguous OpenAI OAuth account label in the
local CLI. This is not proof of the subscription's billing route. Neither the
checkpoint nor a test pass is 100% acceptance.
The runner is not a daemon, scheduler, approval bypass, or hard currency cap.

The retained `make verify` includes historical publication/admission gates,
including expired authorization and the absence of unpublished runtime inputs.
Run relevant portions for regression evidence, but do not wait for those gates
or rewrite their historical outcomes to continue local development. See the
[local milestones](../06_WorkPackages/061_Local_Experiment_Milestones.md) for
the actual results, reproduction commands and remaining boundaries.

## Boundaries

The standing approval includes host execution, necessary dependency changes,
local build/import/registry, lab manifest/configuration/RBAC/service changes,
restarts, bounded local experimental admission and dedicated Flux suspension or
redirection. It does not authorize additional spending, public exposure, push,
GitHub writes, external registry publication, unrelated/global host changes,
retained data/backup deletion, whole-Colima reset or project-purpose changes.
When one of those is required, pause that action and proceed with independent
local work. Do not convert every routine technical uncertainty into an approval
request; record the assumption and test it.

Keep credentials, generated data, large images and machine-local absolute paths
out of Git. Infrastructure notes include recovery; storage notes include disk
impact and backup/export behavior; new resident components include ARM64
compatibility evidence. No new journal service or per-action approval ceremony
is required.

DEV-042 and DEV-044 describe the retained GitHub/supervisor delivery path, used
only when external delivery is separately authorized. Their Issue metadata,
review and per-attempt owner gates do not schedule this local loop.
