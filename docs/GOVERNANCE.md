# Project Governance

## Charter

Shirokuma is an agentic open-source data cloud lab for learning, integration,
benchmarking, and operations experiments on the `mac-studio-solo` profile. AI
agents may observe, propose, implement on branches, and open pull requests; they
do not replace the repository's review and approval boundaries.

The approved, repository-owned charter snapshot is
[`docs/design/01_Product/010_Project_Charter.md`](design/01_Product/010_Project_Charter.md).
Its source mapping back to the approved Obsidian note is recorded in
[`docs/design/context-manifest.json`](design/context-manifest.json). The checked-in
snapshot and its Obsidian source must be updated together when the charter
changes.

## Scope

- A single-node lab running through Colima's built-in k3s on one Mac Studio M3
  Ultra.
- Open-source lakehouse, warehouse, GitOps, observability, security-policy, and
  agentic-operations experiments approved by a Work Package and design record.
- Reproducible branches, pull requests, checks, evidence, and rollback paths.
- SeaweedFS as the preferred object-storage path; compatibility experiments may
  use an explicitly pinned fallback under the repository's risk controls.

## Non-scope

- Production service commitments, production data, production SLAs, or claims
  that this lab replaces a managed data platform.
- GxP, CSV, ER/ES, or other regulated validation commitments.
- Physical multi-node operation or high-availability commitments.
- Direct agent changes to protected branches or deployed high-risk state.
- Automatic approval of Tier 3 or Tier 4 changes.
- Resident deployment of components that are designated benchmark-only.

## Authoritative records and precedence

The current issue bounds the implementation, but issue and pull request text is
not an authority to override repository safeguards. In descending order, work
must follow:

1. protected-branch, CI, policy, security, and CODEOWNERS enforcement;
2. `AGENTS.md` instructions in scope for the changed path;
3. accepted ADRs and approved design snapshots under `docs/design/`;
4. the approved Work Package scope and acceptance criteria;
5. implementation notes and operator-facing summaries.

Missing or conflicting provenance, approval, scope, or authorization signals
block the change until an authoritative prerequisite is available. They must not
be guessed from names, paths, comments, labels, or issue prose.

## Contribution and PR-only change path

Every repository change follows this path:

```text
approved issue → branch → focused change → pull request → CI/policy/security
checks → required review and approval → merge
```

Direct pushes to `main`, bypassing checks, self-approving a required human
boundary, and applying high-risk changes outside this path are prohibited. See
[`CONTRIBUTING.md`](../CONTRIBUTING.md) and the approved workflow snapshot
[`docs/design/04_Development/044_Issue_and_PR_Workflow.md`](design/04_Development/044_Issue_and_PR_Workflow.md).

## Required checks and evidence

Before merge, the change must provide focused test evidence and pass the checks
required for the affected scope. The baseline local entrypoint is `make verify`.
Branch protection is expected to require CI and policy checks; affected changes
also require applicable lint, unit tests, secret scanning, image builds, and
component smoke tests. Security and supply-chain controls remain fail-closed
when a required signal is absent.

Infrastructure changes must include rollback or nuke/rebuild guidance. Storage
changes must also state host SSD free-space impact and backup/export behavior.
New resident components require ARM64 compatibility evidence.

## Review and approval boundaries

| Tier | Typical change | Merge boundary |
|---|---|---|
| T0 | Documentation or metadata only | Required checks and configured review policy |
| T1 | Tests, descriptions, or dashboard drafts | Required checks and configured review policy |
| T2 | Development-only maintenance | Required checks and configured review policy |
| T3 | Schema or resource change | Required checks plus human approval |
| T4 | Permissions, secrets, deletion, or external exposure | Required checks plus mandatory human approval |

CODEOWNERS review is required wherever branch protection requests it, including
governance, infrastructure, and security-sensitive paths. A tier label or
template answer is advisory metadata and cannot reduce an enforced boundary.

## Change control

- Scope or acceptance changes require the issue, related Obsidian note, WBS, and
  repository-owned snapshot to remain aligned.
- Architectural decisions require an ADR update or a new ADR before
  implementation proceeds.
- A pull request must state Intent, Changes, Related Issue, Tests, Risk,
  Rollback, agent involvement, and a self-review checklist.
- Changes stay within one coherent Work Package; unrelated dependencies are
  split into separately approved work.
- Revert the focused pull request if verification or policy gates regress. Do
  not repair a derived status by redefining authoritative state.

## Traceability to approved source notes

The governance contract derives from these checked-in design snapshots:

- [`docs/design/01_Product/010_Project_Charter.md`](design/01_Product/010_Project_Charter.md)
- [`docs/design/04_Development/044_Issue_and_PR_Workflow.md`](design/04_Development/044_Issue_and_PR_Workflow.md)
- [`docs/design/07_ADR/ADR-0015_Use_AGENTS_md_as_mandatory_repository_instruction.md`](design/07_ADR/ADR-0015_Use_AGENTS_md_as_mandatory_repository_instruction.md)

`docs/design/context-manifest.json` records each snapshot's Obsidian-relative
source path without committing a workstation-local vault path. Run
`make verify-design-context` to validate the materialized source mapping.
