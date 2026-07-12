---
project: Shirokuma
doc_id: "DEV-042"
title: "Codex Supervisor x GitHub Workflow"
status: draft
created: 2026-07-05
updated: 2026-07-10
version: "0.3.1"
area: "development"
tags: [shirokuma, codex, github, agent]
---

# Codex Supervisor x GitHub Workflow

## Purpose and authority

Codex Supervisor may select and implement an approved Shirokuma Work Package,
but it does not replace GitHub, branch protection, CI, policy checks,
CODEOWNERS, or human approval. Repository policy and approved design records
remain authoritative when issue text or operator-facing status disagrees.

The only supported path is:

```text
approved Issue → preserved issue worktree → issue branch → draft PR
  → local verification → CI/policy/security → review → merge
  → Flux reconciliation when deployment is in scope
```

The agent must not push directly to `main`, approve a Tier 3 or Tier 4 change,
merge around a required check, or apply high-risk state outside GitHub PR and
Flux v2.

## Repository-owned supervisor contract

The workstation configuration stays outside this repository because it holds
host paths and runtime choices. Its Shirokuma entry must nevertheless preserve
these repository-owned settings:

```json
{
  "issueLabel": "codex",
  "skipTitlePrefixes": ["Epic:"],
  "workspacePreparationCommand": "make prepare",
  "localCiCommand": "make verify",
  "branchPrefix": "codex/issue-"
}
```

This selection is fail-closed:

- only open issues carrying the `codex` label enter the candidate set;
- a title beginning with `Epic:` is skipped even if it is accidentally labeled
  `codex`;
- a Work Package without `codex` is blocked until an operator adds the label;
- the issue body must pass `issue-lint`; missing or malformed dependency,
  ordering, scope, or verification metadata is not inferred from its title,
  labels, parent, or neighboring issues.

The `type:work-package` label is useful operator-facing classification, but the
`codex` label and the issue-body contract are the execution signals. Epic issues
remain planning records rather than executable parent tasks.

## Operator preflight

Run the supervisor from its own checkout and supply the machine-local config by
environment variable. Do not copy its absolute path into repository docs or
issue bodies.

```bash
export CODEX_SUPERVISOR_CONFIG=<supervisor-config-path>
node dist/index.js issue-lint 2 --config "$CODEX_SUPERVISOR_CONFIG"
```

Issue `#2` is the canonical live child-issue lint example. A clean result reports
`execution_ready=yes`, no missing required fields, and no metadata errors. A
non-clean result blocks scheduling; repair the GitHub issue and rerun the lint
instead of substituting chat context.

Before starting the host loop, run this repository's live reference preflight
from the Shirokuma checkout:

```bash
make supervisor-preflight
```

This verifies the configured L0 issues and every `Related docs / ADR` path on
the selected git ref. A missing document, unreachable authoritative ref, or
reference mismatch blocks execution. Runnable-issue contract failures are
enforced separately by `issue-lint` before scheduling.

## Agent execution sandbox

Prompt prohibitions do not establish an enforcement boundary. Every supervisor
run must retain these sandbox controls:

| Control | Requirement |
|---|---|
| Runner isolation | Coding agents run inside disposable containers or ephemeral VMs, not on the host shell. |
| Network egress | Default deny; allow GitHub, package registries, and documented vendor endpoints only. |
| Secrets | No long-lived production-like secrets. Use short-lived tokens or no secrets for code tasks. |
| Filesystem | Workspace mount only. No access to host home, SSH keys, browser profiles, or Obsidian vault secrets. |
| Package install | Dependency additions require diff review and lockfile update. No `curl | bash`. |
| Tool allowlist | MCP tools are explicitly allowlisted per agent role. |
| Audit | Every agent action creates a Pawprint event with model, tokens, repo, task, and risk tier. |

## Preserved worktree lifecycle

For each selected issue the supervisor creates or reuses an isolated issue
branch and preserved worktree. Inside that worktree the execution sequence is:

1. Read `AGENTS.md`, the issue journal, and every issue-linked design document
   or ADR before planning or editing.
2. Run `make prepare`. Preparation must be repository-owned, repeatable, and
   safe to rerun in the preserved worktree.
3. Reproduce the requested gap with the narrowest focused check.
4. Implement only the approved Work Package and update its focused test.
5. Run the focused check, then `make verify`.
6. Commit the coherent result to the issue branch and open a draft PR early.

The worktree is preserved across repair turns so local evidence and the issue
journal survive. Generated state, credentials, workstation-local paths, and
Obsidian vault paths must not be committed.

## Pull request, review, and merge behavior

The draft PR must link the Work Package and include Intent, Changes, related
design records, focused and full test evidence, risk and policy impact,
rollback, agent disclosure, and self-review. Infrastructure changes also need a
nuke/rebuild path. Storage changes need host SSD and backup/export impact, and a
new resident component needs ARM64 compatibility evidence.

The supervisor may continue repairs on the issue branch, but merge readiness is
derived from the current PR head and authoritative GitHub checks:

- required CI, policy, and security checks pass on the current head;
- required CODEOWNERS and human review is present;
- unresolved review threads and merge conflicts are cleared;
- Tier 3 and Tier 4 changes retain their human approval boundary;
- the final merge still uses repository merge authority and never a direct
  agent push to `main`.

If a signal is absent, stale, or ambiguous, keep the PR in draft or blocked
state. Do not treat an earlier green run, badge, summary, or supervisor status
projection as proof for a newer head.

## Failure handling and rollback

Preparation, focused checks, and `make verify` are rerunnable in the preserved
worktree. Fix implementation or documentation failures on the issue branch and
rerun the narrow check before broad verification. Missing permissions, trusted
credentials, authoritative design records, or required human approval are real
blockers and must be surfaced explicitly.

Rollback for this workflow is to stop scheduling the affected Work Package,
close or revert its focused PR, and retain the GitHub issue and journal as the
audit record. No workflow failure authorizes bypassing the protected path.
