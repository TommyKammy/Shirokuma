# Project Governance

## Charter and current mode

Shirokuma is an experimental open-source data cloud lab for learning,
integration, benchmarking and operations experiments on `mac-studio-solo`.
It has no current production-use objective. The owner approved autonomous
local experimental development on 2026-09-16, including policy/Obsidian updates,
local commits and changes to the dedicated Shirokuma Colima/k3s lab.

[ADR-0034](design/07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)
records the standing authorization. [DEV-04B](design/04_Development/04B_Autonomous_Local_Development.md)
defines the current loop. Work Packages remain the source of goals, dependency
ordering and functional acceptance; Issues, PRs, GitHub review and repeated
owner approval are not prerequisites for this local mode.

The repository-owned [charter](design/01_Product/010_Project_Charter.md) and
[context manifest](design/context-manifest.json) map back to matching Obsidian
notes. Update current decisions and evidence in both; preserve historical
records rather than rewriting earlier failures or approvals.

## Authorized local scope

- Select and sequence WP increments; investigate, implement, change dependencies,
  retry, verify and make coherent commits on a dedicated local branch/worktree.
- Run on the host and build/import images or use a local-only registry; change
  dedicated lab manifests, configuration, local RBAC, policy and services;
  restart components and recreate generated disposable experiment data.
- Suspend or redirect the dedicated Shirokuma Flux scope when needed, recording
  its previous state and recovery. Use bounded local experimental admission
  with explicit image/source identity and scope in place of unavailable GitHub
  publication identity; preserve unrelated enforcement.
- Update relevant policy, ADR, WP/WBS and evidence notes; continue between
  dependency-ready WPs without another approval for each routine operation.

The lab remains single-node Linux/arm64 in Colima built-in k3s. SeaweedFS remains
preferred; pinned compatibility fallbacks need their risk and recovery notes.
Resident and benchmark-only component boundaries remain in force.

## Actions outside the standing approval

Do not create Issues/PRs, write to GitHub, push, publish to an external registry,
incur additional spending, expose services publicly, change unrelated projects
or global host settings, delete retained data/backups, destroy the whole Colima
environment, or change the project purpose. Obtain separate authorization only
for the dependent action and continue independent local work.

Preserve existing uncommitted changes and retained state. Production service
commitments, production data, production SLAs, GxP/CSV/ER/ES validation and
physical multi-node/high-availability commitments remain outside project scope.

## Precedence and evidence

The explicit owner approval and ADR-0034 define the local exception to legacy
PR/CI/review, host sandbox, GitHub-identity admission, Tier 3/Tier 4 per-change
approval and exact per-attempt owner gates. Apply root/scoped AGENTS, accepted
design decisions and WP acceptance criteria within that approved boundary.
Missing external publication evidence does not block an identified local
alternative. Missing identity, scope or required authority outside the boundary
still blocks the dependent action.

Use focused tests first, relevant integration/runtime and negative policy checks
when behavior changes, and broader checks at meaningful milestones. The
local entrypoint is `make verify-local-experiment`, with relevant component
checks and live smoke evidence. The historical `make verify` preserves strict
publication/admission outcomes and is not the local loop's approval gate.
Local agent review is useful for
substantive defects; cosmetic suggestions do not require a repeated review loop.
Infrastructure changes need rollback notes; storage changes need disk and
backup/export impact; resident additions need ARM64 evidence.

Record WP, decision, commands/results, runtime scope, artifact location,
remaining limitations and recovery. A local milestone requires evidence for its
functional acceptance criteria. It is not proof of external publication, PR
merge, issue closure, production readiness, strict supply-chain admission or a
human/operator verdict that did not occur. Failed strict checks remain failed.

## Retained external contribution path

External delivery is inactive unless separately authorized. If enabled, use:

```text
approved issue → branch → focused change → pull request → CI/policy/security
checks → required review and approval → merge
```

Do not push directly to `main` or bypass protected-branch, CI, policy, security,
CODEOWNERS or required human approval for that delivery path. DEV-042 and DEV-044
retain its supervisor and Issue/PR contracts; they do not schedule local work.
See [CONTRIBUTING](../CONTRIBUTING.md). Run `make verify-design-context` to check
repository materialization; use `make supervisor-preflight` only before the
separately authorized GitHub supervisor path.
