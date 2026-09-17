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
The [Iceberg snapshot sandbox](tools/local_experiment/iceberg_sandbox/README.md)
demonstrates historical reads, a separate CTAS restoration and rollback on new
disposable tables. Run it with `rtk make local-iceberg-sandbox`.
The historical `make verify` retains publication checks and is separate from
this local loop.

The extended local profile adds Superset, OpenMetadata, ClickHouse AgentHouse,
Loki, an MCP stdio gateway and a bounded LangGraph workflow:

```bash
rtk make local-platform-status
rtk make local-superset-smoke local-metadata-ingest
rtk make local-gateway-smoke local-llm-smoke
rtk make local-llm-qwen-smoke
rtk make local-llm-qwen-eval
rtk make local-sre-qwen-smoke local-sre-qwen-eval
rtk make local-sre-qwen-drill
rtk make local-data-bear-eval local-data-bear-smoke
rtk make local-policy-status local-policy-smoke
rtk make local-tpch-smoke
```

The TPC-H target generates disposable SF10 Iceberg tables and compares 22 queries
with the generator reference. This is a functional experiment on one shared
host, not an official performance result. `local-llm-smoke` uses a deterministic
mock. `local-llm-qwen-smoke` uses the existing MTPLX `qwen3.8-flash-next` server
at `127.0.0.1:8001`, validates real MCP inventory summaries, and reads back token
counts and timing from AgentHouse. `local-llm-qwen-eval` checks five synthetic
inventory cases twice each. See the [LLM runbook](tools/local_experiment/llm/README.md).
Cloud execution remains disabled and is not required for this local loop.
The [SRE workflow](tools/local_experiment/bears/README.md) uses Qwen to select
follow-up MCP reads, verifies the evidence references, and compares selected
Pod/Event observations by UID. It also observes all five runtime groups,
distinguishes unavailable services from unknown status, and requires critical
runtime findings to be re-observed. It does not perform workload remediation.
The optional drill creates one disposable Failed Pod, verifies that Qwen selects
its re-observation, and removes only the creation receipt's matching UID.
The [local admission baseline](tools/local_experiment/policy/README.md) reuses
seven repository CEL rules through native Kubernetes policies scoped to
`shirokuma-dev`. Its smoke requests are server-side dry-runs. This local bridge
does not install Kyverno or attest image signatures/vulnerabilities.
The [Data Engineer Bear](tools/local_experiment/bears/DATA_ENGINEER.md) converts
small requests into a validated plan, generates fixed dbt templates, and checks
disposable Iceberg results against an independent oracle before scoped cleanup.
The [Catalog Curator Bear](tools/local_experiment/bears/CATALOG_CURATOR.md) records
verified Data Engineer runs as historical metadata with fixed owners and lineage.
The [Query Doctor Bear](tools/local_experiment/bears/QUERY_DOCTOR.md) selects bounded
read-only follow-ups from verified Trino plan and query-history observations.
The [Policy Gatekeeper Bear](tools/local_experiment/bears/POLICY_GATEKEEPER.md)
classifies fixed policy candidates and verifies native admission through server dry-runs.
The [isolated Trino/OPA experiment](tools/local_experiment/trino_opa/README.md)
checks row filters, column masks, denials and an unavailable policy backend on
disposable TPCH-only Pods. Its synthetic user headers demonstrate authorization
behavior; authenticated identity is a separate next increment.
The [evaluation report](tools/local_experiment/evaluation/README.md) checks all
retained suite receipts and keeps failures, missing evidence and case differences visible.
The [AgentHouse dashboard](tools/local_experiment/grafana/README.md) provides
read-only Task/Trace filters, recorded failures and blocks, phase latency and
model token coverage at [local Grafana](http://127.0.0.1:18030/d/shirokuma-agenthouse-local).
Run `rtk make local-grafana-up local-grafana-smoke` to start and verify it.
The [benchmark receipt importer](tools/local_experiment/benchmark_events/README.md)
preserves recorded successes, failures and missing comparisons in separate
AgentHouse tables. `rtk make local-benchmark-events-import` reuses retained
receipts and inserts nothing on a repeated import of the same input.
The [Query Doctor history importer](tools/local_experiment/query_history/README.md)
retains captured query identities and measured history separately from auxiliary
reads. `rtk make local-query-history-import` checks terminal evidence and imports
saved observations without sending new Trino or model requests.
The [query experiment history](http://127.0.0.1:18030/d/shirokuma-query-history-local)
displays retained failures, missing comparisons and Query Doctor measurements.
After importing receipts, enable its bounded reader with
`rtk make local-grafana-history-enable local-grafana-verify-history`.
Superset is available at [the local dashboard](http://127.0.0.1:18088/superset/dashboard/shirokuma-local-lakehouse/),
and OpenMetadata at [the local catalog](http://127.0.0.1:18585).
Generated credentials stay in private ignored state; see the component runbooks
linked from [the local runner guide](docs/LOCAL_EXPERIMENT_RUNNER.md).

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
