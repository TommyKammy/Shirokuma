---
project: Shirokuma
doc_id: "PF-AGENTS"
title: "Root AGENTS.md for Shirokuma"
status: accepted
created: 2026-07-05
updated: 2026-10-04
version: "0.7.0"
area: "project-files"
tags: [shirokuma, agents]
---

# AGENTS.md

## Mission and current mode

Shirokuma is an experimental OSS data cloud lab on a single Mac Studio through
Colima, with no current production-use objective. The owner approved autonomous
local development on 2026-09-16. Follow
`docs/design/07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md`
and `docs/design/04_Development/04B_Autonomous_Local_Development.md`.

- Select and order useful work from WP dependencies and acceptance criteria;
  implement, change dependencies, repair, retry, commit locally and advance to
  the next WP without per-attempt approval.
- Use a dedicated local branch/worktree and preserve existing user work. Update
  matching planning notes and repository snapshots with decisions and evidence.
- Host execution and changes to the dedicated Shirokuma Colima/k3s lab are
  authorized: local build/import/registry, manifests, configuration, local RBAC,
  policy, services, restarts and newly generated disposable experiment data.
- Dedicated Flux reconciliation may be suspended or redirected for local work;
  record prior state, scope and recovery. Bounded local admission may use
  recorded source/image identity without GitHub publication identity.
- These local permissions supersede conflicting legacy PR/CI/review, host-shell,
  GitHub admission and per-attempt owner gates in scoped instructions and notes.
  Preserve actual failures and historical evidence; do not claim publication
  or production acceptance from a local result.

## Boundaries

- Do not create Issues/PRs, write to GitHub, push or publish to external registries.
- Additional spending, public exposure, unrelated projects, global host settings,
  retained data/backup deletion, whole-Colima destruction and project-purpose
  changes require separate authorization. Pause only the dependent action.
- Keep secrets, generated data and workstation-local paths out of Git. Do not
  inspect unrelated host credentials or bypass authenticated scope checks.
- If external delivery is later authorized, its protected-branch, CI, policy,
  security and CODEOWNERS rules apply; this local mode does not waive them.

## Architecture constraints

- Primary profile is `mac-studio-solo`, Linux/arm64 in Colima built-in k3s.
  kind is CI/reset-only; no physical multi-node assumptions.
- Prefer SeaweedFS. Do not add Ceph to the mainline path. MinIO is a pinned
  compatibility fallback with digest, risk and replacement notes.
- Use OPA and native RBAC for L3; no Apache Ranger without an ADR.
- Doris is benchmark-only, not resident.
- Keep pinned Flux v2; no Argo CD, Flux Operator or runtime `latest`.
- L0 Flux installs source-controller, kustomize-controller, helm-controller and
  notification-controller in `flux-system`.

## Verification and records

Read the relevant WP, dependency/design records and scoped instructions before
implementation. Ordinary missing detail is an assumption to document and test;
missing authority outside the standing scope remains a blocker for that action.
Run focused checks first, then relevant integration/runtime checks and broader
verification at milestones. Use local review for substantive defects, without
an external review wait loop.

The local-mode entrypoint is `make verify-local-experiment`, plus focused checks
for the changed component and actual runtime smoke tests. The historical
`make verify` includes expired authorizations and strict unpublished-runtime
gates; run its relevant parts for regressions, not as an approval prerequisite
on every local iteration. Preserve those failures until external delivery work
is separately authorized; do not renew old evidence to make the local loop green.

Record test results, remaining criteria and rollback steps. Storage changes
include disk and backup/export impact; new resident components include ARM64
evidence. Update ADRs for architectural decisions and related WP/WBS notes for
scope or acceptance changes. Keep durable references repository-relative.

The GitHub supervisor path is inactive for this loop. Run
`make supervisor-preflight` only before deliberately starting that separately
authorized supervisor path; it is not a prerequisite for local work.

## Opt-in OpenCode continuation

On 2026-10-03 the owner explicitly allowed consuming the entire ChatGPT Pro
weekly allowance to accelerate Shirokuma's local experiments. This is **not**
permission to use metered API-key accounts, other providers or paid services.
The conversation's account does not authenticate the local CLI automatically.
`tools/local_experiment/autonomous_runner.py` may start a bounded series of
fresh `opencode run` sessions only with explicit execution, weekly-limit,
OpenAI-model and run/time flags; it checks for an unambiguous OpenAI OAuth
account label before every attempt. This label is not proof of billing or
entitlement. Without the matching connection, stop before a model request.
The owner subsequently asked to start the runner despite the lack of an
independent billing ledger. For this local experiment, the owner's Pro account
attestation plus exactly one OpenAI OAuth connection, an explicit OpenAI model
and no API-key routing are sufficient to proceed. Do not assert that charges
were independently excluded; stop on actual evidence of metered API use, an
account/provider change or any new spending. Project OpenCode policy restricts
normal model selection to OpenAI. The noninteractive CLI cannot approve reads
outside this checkout: use the runner's private handoff snapshot and repository
instructions, and never bypass a rejected permission.
Merely reading these instructions or running its default plan must never start
a model request. The runner is not the GitHub supervisor and does not authorize
new spending, GitHub writes or publication. See
`tools/local_experiment/AUTONOMOUS_RUNNER.md` for limits and recovery.

The owner also requested a **conditional GPT-6 Astra consultation** when a
coding session meets a genuine difficult blocker. In sessions with the updated
runner prompt, the primary model stays pinned; after focused investigation,
consult only the project-local `astra-advisor` subagent (`openai/gpt-6-astra`,
no tools) if
`rtk proxy tools/local_experiment/llm/.venv/bin/python -m tools.local_experiment.astra_preflight` returns
`ASTRA_CONSULT_PREFLIGHT_OK` immediately before each call. Check the
same single OpenAI OAuth connection and no API-key route. Limit consultation
to two Astra calls total per parent session (one initial call and at most one
follow-up on its blocker); do not open another consultation for a second blocker;
routine work needs no consultation. Send only a sanitized problem statement,
relevant code/test excerpts and explicit uncertainties, never raw private logs,
credentials, Vault contents or retained data. The parent must independently
verify any advice and retain responsibility for the checkpoint and safety
boundary. A subagent call uses additional model requests, not extra runner
session slots; model listing and OAuth label do not prove Pro billing. If the
route/model is unavailable, quota is exhausted or spending is indicated, do
not use an API-key/other-provider fallback or retry an unknown operation.

Only when a continuation prompt supplies `CONTROL_JSON`: read the current WP,
Milestones and private handoff snapshot; complete a coherent local increment and
continue to the next independent task instead of treating a commit or passing
tests as the end of the turn. Do not invoke the runner again from inside a
session. Before finishing, write the requested private checkpoint with the
current full HEAD and an honest `continue` or `stop` decision. It is a control
signal, **not** evidence of production acceptance or completion. If scope,
permissions, retained data safety or execution state is uncertain, stop the
dependent action and preserve the evidence rather than fabricating a checkpoint.
