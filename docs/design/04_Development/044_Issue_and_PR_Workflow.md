---
project: Shirokuma
doc_id: "DEV-044"
title: "Issue and PR Workflow"
status: draft
created: 2026-07-05
updated: 2026-07-10
version: "0.3.1"
area: "development"
tags: [shirokuma, github, workflow]
---

# Issue and PR Workflow

## Issue types and execution eligibility

| Type | Use | Supervisor execution |
|---|---|---|
| Epic | Level or Epic planning record | Never runnable; titles start with `Epic:`. |
| Work Package | Bounded WBS unit; normally one WP per issue | Runnable only when open, labeled `codex`, and contract-clean. |
| ADR | Architectural decision | Not runnable unless represented by an approved Work Package. |
| Bug | Bounded defect | Not runnable by default; convert or explicitly contract it as a Work Package. |
| Runbook | Operational procedure change | Not runnable by default. |
| Research | OSS comparison, license review, or benchmark | Not runnable by default. |

The scheduler must not infer execution eligibility from a title that merely
looks like a Work Package. Conversely, `type:work-package` alone is not an
execution grant. The authoritative executable set is open issues with the
`codex` label after title exclusion and `issue-lint` validation.

## Runnable Work Package issue contract

Every Codex-runnable issue body must include these sections and scheduling
fields with concrete values:

- Summary
- Scope
- Acceptance criteria
- Verification
- Related docs / ADR
- Part of
- Depends on
- Parallelizable
- Execution order

The section names above are the exact Markdown headings emitted by the Work
Package issue form. Renaming a form label must update this contract and its
regression test in the same change.

`Depends on: none` is an explicit value. `Part of` must name the authoritative
parent issue, and `Execution order` must use a concrete position such as
`2 of 5`. Empty, TODO, placeholder, malformed, or guessed values block the
issue. Include Non-scope, Risk and rollback, Risk tier, and Agent instructions
where the repository issue form requires them.

The contract is self-contained: an operator must be able to distinguish
runnable, dependency-blocked, and malformed work from the issue and
`issue-lint` output without relying on chat history.

## Canonical runnable child issue

The following repository-owned example is intentionally complete enough to use
as the source when authoring a child Work Package. Replace placeholders before
publishing; placeholder values are never valid execution metadata.

<!-- canonical-runnable-issue:start -->
```markdown
# [WP-L0-EXAMPLE-001] Repository-owned example

## Summary

Publish one bounded, reviewable repository change.

## Scope

- Update the approved workflow document.
- Add the narrow regression check for that document.

## Non-scope

- Direct changes to protected branches or deployed state.

## Acceptance criteria

- The documented behavior is explicit and testable.
- The focused regression check and repository verification pass.

## Verification

- `python3 -m unittest -v tests.test_codex_supervisor_workflow_docs`
- `make verify`

## Related docs / ADR

- `docs/design/04_Development/042_Codex_Supervisor_GitHub_Workflow.md`
- `docs/design/04_Development/044_Issue_and_PR_Workflow.md`

Part of: #1
Depends on: none
Parallelizable: No
Execution order: 1 of 1

## Risk and rollback

- Revert the focused pull request if the workflow or checks regress.

## Risk tier

T0 - docs or metadata only

## Agent instructions

- Follow repository policy and preserve the issue → branch → PR → checks → review → merge path.
```
<!-- canonical-runnable-issue:end -->

The live repository example used for supervisor compatibility is issue `#2`.
From the Codex Supervisor checkout, lint it with:

```bash
node dist/index.js issue-lint 2 --config "$CODEX_SUPERVISOR_CONFIG"
```

The command must report `execution_ready=yes`, `missing_required=none`, and
`metadata_errors=none`. The repository fixture test verifies that this durable
example retains the same required headings and scheduling fields; the live
`issue-lint` invocation verifies the supervisor's real enforcement boundary.

## Work Package lifecycle

1. Prepare an approved, contract-complete issue and add `codex` only when it is
   eligible for supervisor execution.
2. Let the supervisor create or preserve `codex/issue-<number>`; never work on
   `main` directly.
3. Run `make prepare`, reproduce the gap with a focused check, implement the
   bounded change, and run `make verify`.
4. Commit to the issue branch and open a draft PR that closes the issue.
5. Address current-head CI, policy, security, CODEOWNERS, and review findings.
6. Merge only through repository authority after every required gate passes.

Issue closure, PR merge, and any later Argo CD reconciliation are separate
authoritative lifecycle events. A status summary or failed post-mutation refresh
must not rewrite an already recorded GitHub outcome.

## PR must include

- Intent
- Changes
- Related Issue
- Related docs / ADR
- Focused and repository verification evidence
- Risk tier and policy impact
- Rollback or nuke/rebuild guidance when applicable
- Agent disclosure and self-review
- Required CODEOWNERS and human approval status

The PR begins as draft while implementation or local verification is
incomplete. It becomes reviewable only after the branch is coherent and the
documented local checks pass. CI, policy, security, review, and merge authority
remain unchanged by supervisor automation.
