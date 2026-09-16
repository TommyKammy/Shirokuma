# Contributing

Read [the project governance contract](docs/GOVERNANCE.md), root/scoped
`AGENTS.md`, the relevant Work Package and its design records. Current work uses
[autonomous local experimental development](docs/design/04_Development/04B_Autonomous_Local_Development.md),
approved on 2026-09-16 in
[ADR-0034](docs/design/07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md).

Use a dedicated local branch/worktree, preserve existing work, choose the next
increment from WP dependencies, implement and verify it, record evidence and
commit locally. Do not create Issues or PRs, write to GitHub, push or publish
externally. The standing approval covers routine technical decisions, local
lab application and retries; do not ask again for each attempt or WP transition.

Start with the narrowest useful reproduction or regression check. Run relevant
integration/runtime checks and the local development entrypoint at meaningful milestones:

```bash
rtk make verify-local-experiment
```

Use additional focused component checks for the changed scope. The retained
`make verify` also evaluates historical publication/admission gates, including
expired authorization and the absence of an unpublished Trino runtime. Those
failures remain explicit and do not block this separately approved local loop.

When the checkout needs preparation, use the repeatable repository entrypoint:

```bash
rtk make prepare
```

Keep credentials, generated data and large warehouse scratch files out of Git.
Keep workstation-local variable values and paths outside durable repository
documents. Record local image/admission and Flux overrides with their identity,
scope and recovery. Do not delete existing retained data or reset the whole VM.

Update the related Obsidian note and repository-owned snapshot together; update
WBS for scope/acceptance changes and record architectural choices in an ADR.
Infrastructure changes need rollback guidance, storage changes need disk and
backup/export impact, and new resident components need ARM64 evidence. Report
unverified criteria and distinguish local experimental completion from external
publication or production/operator acceptance.

If external delivery is separately authorized later, its Issue/PR, CI, policy,
security, CODEOWNERS and required human-approval gates apply. Pull requests then
include Intent, Changes, Related Issue/design records, Tests, Risk, Rollback,
agent involvement and self-review. Do not push directly to `main` or bypass those
gates. Shirokuma remains a single-node lab without production, GxP or physical
multi-node commitments.
