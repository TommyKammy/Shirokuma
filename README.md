# Shirokuma
Shirokuma is an agentic OSS data cloud lab that recreates core ideas from Snowflake, Databricks, and Microsoft Fabric using open-source components and an AI-first operating model.

The repository's charter, scope, non-scope, review boundaries, and PR-only
change path are defined in [`docs/GOVERNANCE.md`](docs/GOVERNANCE.md).

## Development

Run the repository-owned verification command before opening a pull request:

```bash
make verify
```

The supervisor can run the repository-owned preparation command before local
verification in preserved worktrees:

```bash
make prepare
```

## Design context

The repository-owned snapshot of the approved Shirokuma design notes lives
under `docs/design/`. GitHub issues must reference these repository-relative
paths so Codex can read them from an issue worktree.

Verify the checked-in context and issue mapping:

```bash
make verify-design-context
```

Before starting the host codex-supervisor loop, verify that every L0 issue
references documents that exist on `origin/main`:

```bash
make supervisor-preflight
```
