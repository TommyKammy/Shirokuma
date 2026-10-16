---
project: Shirokuma
doc_id: "ADR-0036"
title: "Adopt Commander-Worker Model and Pragmatic Core Reset"
status: accepted
created: 2026-10-16
updated: 2026-10-16
version: "1.0"
area: adr
tags: [shirokuma, adr, governance, roadmap, codex-cli]
---

# ADR-0036: Adopt Commander-Worker Model and Pragmatic Core Reset

## Context

Between 2026-09-16 and 2026-10-10, Shirokuma ran over 520 autonomous local development sessions under [ADR-0034](ADR-0034_Use_autonomous_local_experimental_development.md), [DEV-04B](../04_Development/04B_Autonomous_Local_Development.md), and [DEV-04C](../04_Development/04C_Gate_Driven_Local_Continuation.md).

While those sessions proved that the core open-source stack (SeaweedFS, Apache Iceberg, Apache Polaris, Trino, Spark/Comet, Dagster/dbt, OpenMetadata, Superset, ClickHouse AgentHouse, MLflow, and local Qwen via MCP) can execute on the single Mac Studio M3 Ultra (`mac-studio-solo`), development ultimately stalled due to four structural issues:

1. **Defensive Micro-Hardening Spiral**: Unattended multi-hundred-session loops with strict fail-closed rules incentivized the agent to spend dozens of sessions adding symlink/fd-relative filesystem guards (`openat2`, `O_NOFOLLOW`), Mach clock checks, and supervisor wrapper scripts rather than user-facing data platform capabilities.
2. **Upstream CVE Zero-Tolerance Deadlock**: Requiring `Trivy HIGH=0 / CRITICAL=0` and custom offline source builds for third-party OSS containers (Flux v2 controllers, Trino 483, Polaris) caused the agent to spend over 20 sessions manually patching Go binaries and Alpine `pcre2` APK tar layers inside a 16 GiB disk budget.
3. **Retained-Evidence Paralysis**: Treating 3,400+ generated test receipts and interrupted process states (`tmp/rv/mis`) as untouchable state prevented normal startup of the Colima VM and left Flux and all workloads suspended (`replicas=0`), contradicting the Project Charter's goal of a disposable, reproducible lab (`shirokuma nuke && shirokuma init`).
4. **Fixture-Only Feature Theatre**: To guarantee zero side effects during unattended runs, most features in `tools/local_experiment/` were restricted to hardcoded tokens, 2-row or 48-row fixtures, or single pre-canned queries (`--case retained-fixture`), preventing interactive use with real data and arbitrary SQL.

On 2026-10-16, the owner approved a strategic reset to resolve this stagnation and establish a clear division of labor between an architectural commander and an implementation worker.

## Decision

### 1. Commander–Worker Operating Model (Antigravity + `codex-cli`)

Unattended multi-hundred-session loops (`autonomous_runner.py`, `gate_supervisor.py`, `restart_supervisor.py`, `flux_refresh_supervisor.py`, etc.) are **retired**. Development proceeds under a two-tier **Commander–Worker** architecture:

- **Commander / Architect / Reviewer (`Antigravity`)**:
  - Owns architecture, roadmap prioritization, Work Package scoping, and Vault/repository documentation alignment.
  - Formulates concise, bounded implementation prompts for the worker with explicit anti-over-engineering constraints.
  - Reviews all diffs, test results, and runtime behavior produced by the worker, rejecting unnecessary complexity, fixture-only hacks, or scope drift.
- **Implementation Worker (`codex-cli` with `gpt-6.1-sol` Medium)**:
  - Invoked via `/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex exec -m gpt-6.1-sol -c model_reasoning_effort="medium"`.
  - Executes scoped code changes, bug fixes, and focused unit/integration tests within `worktree/local-autonomous` under Antigravity's supervision.

### 2. Pragmatic Supply-Chain & CVE Policy for `mac-studio-solo`

For the local experimental lab (`mac-studio-solo`):

- **Use Official Digest-Pinned ARM64 Images**: Upstream OSS components (Flux v2, SeaweedFS, PostgreSQL, Polaris, Trino, ClickHouse, Dagster, OpenMetadata, Superset, Spark) must use official upstream ARM64 images pinned by SHA256 digest.
- **No Custom Upstream Source/Layer Surgery for CVEs**: Custom-compiling or binary-patching upstream OSS containers to eliminate upstream CVEs is terminated.
- **Audit-Only Vulnerability Scanning for Upstream Images**: Trivy and Syft scans remain available for visibility and reporting (`warn-only`), not as blocking gates that halt local cluster startup or development.
- **Focus Policy Enforcement on Shirokuma Configurations**: Admission policies (OPA / Kubernetes native policies) guard Shirokuma's own workload manifests, RBAC boundaries, and SQL/Agent guardrails.

### 3. Restoration of Disposable, Reproducible Lab State

- **Archive Bloated Logs**: The 12,463-line milestone log and 2,794-line WBS log from the September–October autonomous run are preserved intact under `06_WorkPackages/archive/` and replaced with concise, human-readable tracking tables.
- **Unfreeze Runtime Operations**: Historical `UNKNOWN_EXECUTION` holds (`tmp/rv/mis`) and old receipt checksums no longer block starting Colima, unsuspending Flux/workloads, or resetting disposable lab state.
- **Reproducibility First**: The standard of quality for L0/L1 is that `make lab-up`, `shirokuma doctor`, and `make lab-reset` reliably bring up a healthy, usable Lakehouse.

### 4. Graduation from Fixtures to Core Data Cloud Capabilities

- **General Inputs Over Hardcoded Fixtures**: Features in `shirokuma` CLI, Virtual Warehouse lifecycle, DataOps pipelines, and the 5 Agent Bears must accept general inputs (arbitrary valid read/analytical SQL, real Iceberg tables, live cluster telemetry) rather than hardcoded fixture cases.
- **Core Stack Focus**: Engineering effort concentrates on the Core Lakehouse & Agent Control Plane:
  - **Storage & Catalog**: SeaweedFS + Apache Iceberg + Apache Polaris
  - **Compute & Virtual Warehouse**: Trino + Trino Gateway (XS/S auto-suspend & wake)
  - **DataOps & Lineage**: Dagster + dbt + OpenMetadata
  - **Agent Control Plane & Telemetry**: `shirokuma` CLI + MCP Gateway + 5 Bears (SRE, Query Doctor, Data Engineer, Catalog Curator, Policy Gatekeeper) + ClickHouse AgentHouse + OTel Pawprints
  - **Operator Surface**: Unified Shirokuma Workbench UI + Superset / Grafana
- **Deferral of Peripheral Engines**: StarRocks, Doris, Gravitino, XTable, RustFS, Cube, Ray, and Amoro are moved to an optional plugin/comparison backlog until Phase 1–3 core milestones are complete.

## Precedence

This ADR supersedes the unattended runner scheduling, blocking upstream CVE rebuild gates, and retained-receipt immutability holds in [ADR-0019](ADR-0019_Allow_time_boxed_resident_image_exceptions_for_local_lab.md), [ADR-0022](ADR-0022_Adopt_Trino_483_repository_source_build.md)–[ADR-0033](ADR-0033_Activate_exact_Trino_483_dependency_publication_sequence_8.md), [ADR-0034](ADR-0034_Use_autonomous_local_experimental_development.md), [DEV-04B](../04_Development/04B_Autonomous_Local_Development.md), and [DEV-04C](../04_Development/04C_Gate_Driven_Local_Continuation.md). Standing local authorization boundaries (no GitHub writes/PRs/pushes, no external registry publication, no new paid API spending, no global host destruction) remain unchanged.

## Consequences

- **Positive**: Immediate elimination of bureaucratic overhead and CVE-patching deadlocks; clean, readable documentation; clear accountability between Antigravity (strategy/review) and `codex-cli` (implementation); rapid path to a live, interactive Lakehouse.
- **Trade-off**: Local upstream images may contain upstream-unfixed CVEs, which is explicitly acceptable for a single-host non-production lab under the Project Charter.
