---
project: Shirokuma
doc_id: "WP-LOCAL-MILESTONES"
title: "Local Experiment Milestones"
status: active
created: 2026-09-16
updated: 2026-10-16
version: "6.0.0"
area: work-packages
tags: [shirokuma, local, milestones]
---

# Local Experiment Milestones

> [!NOTE]
> **2026-10-16 Strategic Reset ([ADR-0036](../07_ADR/ADR-0036_Adopt_Commander_Worker_Model_and_Pragmatic_Core_Reset.md) / [DEV-04F](../04_Development/04F_Commander_Worker_and_Pragmatic_Roadmap.md))**
> The 12,463-line granular session ledger from the September–October 2026 autonomous runner series (523 sessions) has been preserved intact in [`archive/061_Local_Experiment_Milestones_Archive_2026-09_to_10.md`](archive/061_Local_Experiment_Milestones_Archive_2026-09_to_10.md). Going forward, only concise, human-readable milestone summaries are recorded here.

---

## Active Roadmap Milestones (Phase 0 – Phase 3)

### 2026-10-16 — WP-P0-001: Governance Reset & Documentation Slimming (Completed)
- Adopted [ADR-0036](../07_ADR/ADR-0036_Adopt_Commander_Worker_Model_and_Pragmatic_Core_Reset.md) and [DEV-04F](../04_Development/04F_Commander_Worker_and_Pragmatic_Roadmap.md), establishing the **Commander (`Antigravity`) + Implementation Worker (`codex-cli` GPT-6.1 Sol Medium)** operating model.
- Retired the unattended 500-session runner and blocking upstream CVE source-build rules in favor of digest-pinned official ARM64 images and audit-only vulnerability scanning.
- Archived the 1MB+ historical milestone log into `archive/061_Local_Experiment_Milestones_Archive_2026-09_to_10.md` and slimmed the active WBS and Milestone documents.

### 2026-10-16 — WP-P0-002: Warehouse Lifecycle Test Normalization (Completed)
- Fixed the macOS `/var` → `/private/var` temp symlink path resolution in `tests/test_local_warehouse_session_rpc.py` (`test_isolated_termination_boundaries_leave_unknown_saved_intent`, `boundary='after_handler'`) via `codex-cli` (`gpt-6.1-sol` Medium).
- Confirmed all 268 tests in `make test-local-warehouse-lifecycle` pass (`OK`).

### 2026-10-16 — WP-P1-001 & WP-P1-002: Resident L0/L1 Core Revival & 1-Command Lab Lifecycle (Completed)
- Booted `mac-studio-solo` Colima VM (without affecting other profiles) and repaired the stale DHCP Node IP in k3s `state.db` (`192.168.5.1` → `192.168.5.3`).
- Restored and unsuspended all 4 Flux v2 controllers, `GitRepository/flux-system`, 6 child Kustomizations, `seaweedfs`, `polaris-postgresql`, `polaris`, `trino-local`, `agenthouse` (ClickHouse), and `loki`.
- Fixed `tools/local_experiment/agenthouse/manage.py` to accept standard ClickHouse `Transfer-Encoding: chunked` and `application/x-ndjson` responses as well as already-running containers.
- Added `tools/local_experiment/lab_lifecycle.py` (`make lab-up`, `make lab-down`, `make lab-status`) and verified live `make local-doctor` (`Status: healthy`), `make local-lakehouse-smoke` (`result: passed`), `make local-agenthouse-smoke` (`result: passed`), and `make lab-status` (`ready: true`).

### 2026-10-16 — WP-P2-001: Resident Virtual Warehouse & General SQL Execution (Completed)
- Implemented `tools/local_experiment/warehouse_resident.py` and `internal/cli/warehouse_resident.go` via `codex-cli` (`gpt-6.1-sol` Medium), adding `shirokuma warehouse status / chill / wake / query` against the resident `Deployment/trino-local` in `shirokuma-dev` while keeping 100% of legacy `--session` unit tests green.
- Removed the fixed 25-row `tpch.tiny.nation` fixture restriction for resident queries, supporting arbitrary SQL (`--sql` or positional argument), `--catalog` / `--schema`, pagination over `/v1/statement`, automatic cold wake (`auto_woken: true`), and AgentHouse Pawprint recording (`agenthouse.pawprints`).
- Verified live on `mac-studio-solo`: `status`, Iceberg query (`iceberg.smoke.fixture_v1`), TPC-H join/aggregation (`tpch.tiny.nation JOIN tpch.tiny.region`), `chill` (`replicas=0`), and auto-wake SQL query (`tpch.tiny.customer`, `auto_woken: true`, `telemetry: recorded`).

---

## Summary of Pre-Reset Verified Capabilities (2026-09-16 to 2026-10-10)

The following capabilities were functionally demonstrated on `mac-studio-solo` during the initial local experiment wave and serve as the foundation for Phase 1–3 graduation:

| Layer | Capability | Status & Next Step in Phase 1–3 |
|---|---|---|
| **L0 Platform & Policy** | Colima (`vz`, Linux/arm64) + k3s + 4 Flux v2 controllers + 16 native Kubernetes admission policies (`169` policy tests green) | Unsuspend in **WP-P1-001**; make `shirokuma doctor` green on live cluster (**WP-P1-002**) |
| **L1 Core Lakehouse** | SeaweedFS S3 + Apache Polaris + PostgreSQL + Apache Iceberg (snapshots, time-travel, branches/tags) + Trino 483 + Dagster/dbt + OpenMetadata (`105` lineage tests green) + Superset + TPC-H SF10 smoke | Transition from disposable post-smoke `DROP` fixtures to persistent local Lakehouse in **WP-P2-002** |
| **L2 Agent Control Plane** | ClickHouse AgentHouse + OTel Pawprints + Loki + MCP stdio Gateway + local Qwen (`127.0.0.1:8001`) + 5 Bears (`SRE`, `Data Engineer`, `Catalog Curator`, `Query Doctor`, `Policy Gatekeeper`) | Remove fixed-fixture restrictions in **WP-P2-003** so Bears operate on arbitrary queries/requests and live cluster state |
| **L3 Virtual Warehouse & Security** | Trino Gateway XS/S routing + `shirokuma chill / wake / query` lifecycle + Keycloak JWT + OPA row filters/column masks | Fix remaining `UNSAFE_PATH` test fixture in **WP-P0-002**; open `warehouse query` to general SQL in **WP-P2-001** |
| **L4 Spark, ML & Notebooks** | Spark 4.1.3 + Iceberg 1.11.0 + Apache Comet ARM64 native acceleration + MLflow Tracking/Registry + local JupyterLab workbench | Keep Spark/MLflow/Notebooks integrated with persistent Iceberg catalog |
| **L5 & L6 Observability & FinOps** | Grafana Alerting → Dagster Sensor → SRE diagnosis flow + Lineage completeness report + FinOps resource/storage report | Consolidate operator views into the unified **Shirokuma Workbench** (**WP-P3-002**) |
