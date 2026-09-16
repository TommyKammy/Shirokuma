# Shirokuma
Shirokuma is an agentic OSS data cloud lab that recreates core ideas from Snowflake, Databricks, and Microsoft Fabric using open-source components and an AI-first operating model.

Current development uses the owner-approved local experimental loop: Work Package
→ implementation → local verification/application → local commit. No Issue or PR
is required. Read [`docs/GOVERNANCE.md`](docs/GOVERNANCE.md) and
[the local workflow](docs/design/04_Development/04B_Autonomous_Local_Development.md)
for scope, evidence and external-action boundaries.

## Development

Shirokuma requires Go 1.24 or newer. Build the minimal CLI and inspect its
current command surface with:

```bash
go build ./cmd/shirokuma
go run ./cmd/shirokuma --help
go run ./cmd/shirokuma version
```

Run focused checks during development and the local verification at milestones:

```bash
make verify-local-experiment
go test ./...
go vet ./...
```

With the dedicated Colima lakehouse running, inspect readiness and exercise the
data path. The smoke commands open and close their own loopback port-forward:

```bash
make local-doctor
make local-lakehouse-smoke
make local-dataops-recovery-smoke
```

The DataOps command installs the locked Python dependencies in a project-local
environment, materializes dbt staging/mart tables through Dagster, verifies repeat
results and recovery from a controlled test failure, and removes its experiment
tables. See [the runbook](tools/local_experiment/dataops/README.md).
The historical `make verify` retains publication checks and is separate from
this local loop.

## Design context

The repository-owned snapshot of the approved Shirokuma design notes lives
under `docs/design/`. Work Packages and local worktrees use these repository-relative
paths. Matching Obsidian notes remain the planning source.

Verify the checked-in context and issue mapping:

```bash
make verify-design-context
```

The GitHub supervisor path is inactive in local mode. Only before starting that
separately authorized path, verify its issue references on `origin/main`:

```bash
make supervisor-preflight
```
