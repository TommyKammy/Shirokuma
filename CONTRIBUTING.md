# Contributing

Read [the project governance contract](docs/GOVERNANCE.md), the root
`AGENTS.md`, and every design document or ADR referenced by the approved issue
before changing the repository. Work must stay inside a bounded GitHub issue and
use an issue branch and pull request; never push directly to `main` or bypass
CI, policy, security, CODEOWNERS, or required human approval.

Start with the narrowest focused reproduction or regression check. Then run the
repository-owned verification command before opening a pull request:

```bash
make verify
```

If a preserved supervisor worktree needs setup before verification, use the
repository-owned preparation entrypoint:

```bash
make prepare
```

Keep machine-local state, credentials, generated data, and large warehouse
scratch files out of git. If a local setting is required for development,
document the variable name in the repository and keep the value outside the
repository.

Pull requests must include Intent, Changes, Related Issue and design records,
test evidence, risk tier and policy impact, rollback, agent disclosure, and
self-review. Tier 3 schema or resource changes and Tier 4 permissions, secrets,
deletion, or external-exposure changes require human approval. Infrastructure
changes need rollback or nuke/rebuild guidance; storage changes also need host
SSD and backup/export impact; new resident components need ARM64 compatibility
evidence.

If scope or evidence changes, update the related Obsidian note, WBS, and its
repository-owned snapshot together. Architectural changes require an ADR. Use
repository-relative paths in durable artifacts and issue text; never commit a
workstation-local Obsidian path.

Shirokuma is a single-node lab. Contributions must not add production, GxP, or
physical multi-node commitments.
