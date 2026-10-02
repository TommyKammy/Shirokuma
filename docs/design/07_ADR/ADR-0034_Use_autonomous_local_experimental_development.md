---
project: Shirokuma
doc_id: "ADR-0034"
title: "Use autonomous local experimental development"
status: accepted
created: 2026-09-16
updated: 2026-10-03
version: "1.2"
area: adr
tags: [shirokuma, adr, local, development]
---

# ADR-0034: Use autonomous local experimental development

## Context and authorization

Shirokuma is an experimental project with no current production-use objective.
The owner explicitly approved the following standing scope in the development
conversation on 2026-09-16: local autonomous selection, implementation, local
commits, application, verification and recreation of generated experiment data,
including corresponding repository policy and Obsidian updates. Approval is
already granted; agents must not ask again for each attempt or WP transition.

Issue creation, PR creation, GitHub review and repeated approval waits have
added disproportionate overhead to local experiments. Preserve functional
acceptance and reproducible evidence while removing those scheduling gates.

## Decision

Use the Work Packages and their dependency/acceptance criteria to select the
next useful local increment. Implement and retry in a dedicated local branch or
worktree, run focused checks and relevant integration/runtime checks, record
results and remaining gaps, and make coherent local commits. Continue across
WP boundaries when dependencies justify it. Ordinary technical choices,
dependency changes and remediation attempts are covered by this approval;
record assumptions and architectural decisions rather than waiting for an
owner decision on every attempt.

The authorized environment is this host's Shirokuma checkout, matching planning
Vault, and dedicated Shirokuma Colima/k3s lab. Agents may run on the host and
build, import or store images in a local-only registry; apply manifests, local
RBAC, policy, configuration and services; restart lab components; and recreate
newly generated disposable experiment data. Identify the dedicated runtime and
preserve existing uncommitted work, retained data and backups before mutation.

Suspend or redirect the dedicated Shirokuma Flux reconciliation scope when it
would overwrite local experiments. Record its prior state, exact local scope
and recovery steps. Other reconciliation scopes remain unaffected. Use pinned
inputs and bounded experimental admission with recorded image/source identity,
reason and scope when GitHub publication identity is unavailable. Local
admission must not masquerade as strict published-image attestation or disable
unrelated controls. A failed strict publication gate remains failed; evidence
from a local experiment does not retroactively authorize its publisher.

## Precedence and boundaries

For this approved local mode, this decision supersedes conflicting workflow
requirements in ADR-0001, ADR-0015, ADR-0018, DEV-042, DEV-044, earlier WP notes,
per-attempt owner gates and repository instructions: Issue/PR creation, GitHub
CI/review/merge, GitHub identity admission, host-shell prohibition and renewed
human approval for in-scope local changes are not prerequisites. Historical
publication receipts, failed checks and approval records remain unchanged.
Missing an Issue or an external publication receipt does not block a local
alternative with explicit identity and scope.

The standing approval does **not** authorize GitHub writes, push, publication,
external registry writes, additional spending, unrelated projects, global host
settings, deletion of retained data/backups, destroying the entire Colima
environment, public service exposure, or changes to the project's purpose.
Stop only the dependent action at such a boundary and continue independent
work. Protected-branch, CI, CODEOWNERS and publication rules still apply if an
external delivery is separately authorized later.

On 2026-10-03 the owner separately allowed Shirokuma's local continuation runner
to consume the whole existing ChatGPT Pro weekly allowance. That allowance is
not an authorization for metered API credentials, paid fallback providers or
new subscriptions. The current chat's authentication does not establish the
local OpenCode CLI's identity or billing route. The runner therefore requires
explicit model selection and a visible OpenAI OAuth label before each call;
when those are missing or ambiguous it must not make a model request. This
check is evidence of a CLI label, not independent proof of provider billing.
The owner then expressly requested starting the runner. After a one-session
pilot stopped before WP work solely because it could not independently read a
billing ledger, local continuation treats the owner's Pro attestation, a
single OpenAI OAuth connection, an explicit OpenAI model and no API-key route
as sufficient experimental routing evidence. Do not claim independent billing
proof. Restrict normal provider selection to OpenAI in the dedicated checkout;
stop on actual evidence of another account/provider, metered API usage, or an
out-of-scope action. This does not waive data retention or acceptance limits.

## Verification and completion

Keep focused tests, relevant policy/negative checks, ARM64 and resource evidence
for resident components, and rollback notes. Run wider integration or repository
checks at meaningful milestones, not every exploratory edit. Use local agent
review when useful; fix substantive correctness, security and data-loss issues,
and queue cosmetic improvements without a repeated external review loop.

Record a local milestone against the WP's functional acceptance criteria using
commands, outcomes, artifact locations and unresolved limitations. A local
experimental milestone does not assert PR merge, issue closure, production
readiness, strict supply-chain admission or human/operator acceptance that has
not occurred. Keep those classifications explicit.

## Consequences and recovery

Local learning and implementation can proceed without GitHub scheduling latency.
The cost is intentional divergence from GitHub main and from the historical
publication workflow. Keep a local commit trail and document runtime overrides.
Revert the affected local commit/manifests or restore the recorded Flux scope to
recover; do not destroy retained state. Returning to external delivery requires
separate authorization and its applicable gates.

The operating loop is documented in
[DEV-04B](../04_Development/04B_Autonomous_Local_Development.md).
