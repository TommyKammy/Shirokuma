---
project: Shirokuma
doc_id: "WP-LOCAL-MILESTONES"
title: "Local Experiment Milestones"
status: active
created: 2026-09-16
updated: 2026-10-17
version: "7.0.0"
area: work-packages
tags: [shirokuma, local, milestones]
---

# Local Experiment Milestones

> [!NOTE]
> **2026-10-16 Strategic Reset ([ADR-0036](../07_ADR/ADR-0036_Adopt_Commander_Worker_Model_and_Pragmatic_Core_Reset.md) / [DEV-04F](../04_Development/04F_Commander_Worker_and_Pragmatic_Roadmap.md))**
> The 12,463-line granular session ledger from the September–October 2026 autonomous runner series (523 sessions) has been preserved intact in [`archive/061_Local_Experiment_Milestones_Archive_2026-09_to_10.md`](archive/061_Local_Experiment_Milestones_Archive_2026-09_to_10.md). Going forward, only concise, human-readable milestone summaries are recorded here.

---

## Active Roadmap Milestones (Phase 0 – Phase 6)

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

### 2026-10-17 — WP-P2-001a: Commander Review Fixes & Corrections (Completed)
- Fixed bare `make` running `lab-up` / `warehouse-status` (regression since WP-P1-002) via `.DEFAULT_GOAL := verify`.
- Correction: XS/S/LOCAL share the single `trino-local` backend (now default `LOCAL`, reports include `backend: trino-local`); Gateway-based XS/S separation is deferred to Optional Track per Owner decision.
- Added `--max-rows` (default 1000, server-side cancel, `truncated` flag) and `status=failed` Pawprints for failed queries (no SQL/error text). Verified live.
- Correction to WP-P1-001: the root `Kustomization/flux-system` remains intentionally suspended because its source tracks GitHub `main` while this branch is local-only; child Kustomizations are active. `trino-local` is not Flux-managed.

### 2026-10-17 — WP-P2-002a: Persistent Iceberg TPC-H Dataset Seeding (Completed)
- Implemented `tools/local_experiment/lake_seed.py` and `tests/test_lake_seed.py` (13 unit tests passed), providing `make lake-seed` and `make lake-status`.
- Seeds 8 persistent TPC-H tables (`region`, `nation`, `part`, `supplier`, `partsupp`, `customer`, `orders`, `lineitem`) into `iceberg.tpch_tiny` via a single Trino session without any DROP/destructive operations.
- Verified live on `mac-studio-solo`: initial `lake-status` exits 1 (missing), initial `lake-seed` creates all 8 tables and matches row counts (total 86,805 rows), second `lake-seed` verifies idempotency (`present` with 0 recreation), and multi-table 4-way JOIN revenue query against Iceberg executes cleanly.

### 2026-10-17 — WP-P2-002b: Persistent Iceberg dbt Analytics Pipeline (Completed)
- Created persistent dbt project at `tools/local_experiment/dataops/dbt_persistent/` with staging views (`stg_tpch_orders`, `stg_tpch_customers`, `stg_tpch_lineitem`) and dimensional/fact marts (`dim_customers`, `fct_orders`) backed by Iceberg Parquet storage.
- Implemented `tools/local_experiment/lake_pipeline.py` and `tests/test_lake_pipeline.py` (6 unit tests passed), adding `make lake-build` and `make lake-test`.
- Verified live on `mac-studio-solo`: `make lake-build` builds all 5 models and passes 7 schema assertions in 9.9s; `make lake-test` passes all 7 assertions in 3.5s; marts aggregate query joins `fct_orders` and `dim_customers` to produce correct nation-level revenue stats; Pawprints recorded to ClickHouse (`lake.pipeline.build`, `lake.pipeline.test`).

### 2026-10-17 — WP-P2-003: 5 Core Bears Resident Integration (Completed)
- Removed mock-only restrictions across the 5 Core Bears, implementing `tools/local_experiment/bears_resident.py`, `tests/test_bears_resident.py` (10 unit tests), and Go CLI bindings `internal/cli/bear.go` (`shirokuma bear sre`, `query`, `data`, `catalog`, `policy`). Added `make bears-smoke` and `make bear-*`.
- **SRE Bear**: Gathers live readiness across 6 core components, pod CPU/memory container metrics, and recent failed ClickHouse Pawprints.
- **Query Doctor Bear**: Fetches runtime query history and distributed EXPLAIN execution plans from resident Trino, providing automated cost, fragment, and join/scan skew recommendations.
- **Data Engineer Bear**: Queries live Iceberg catalog column metadata, prompts local LLM (`qwen3.8-flash-next` via `127.0.0.1:8001/v1`) with `enable_thinking: false`, strips markdown fences, and verifies generated SQL plan validity via Trino `EXPLAIN`.
- **Catalog Curator Bear**: Discovers live schemas/tables across `iceberg` (`tpch_tiny`, `staging`, `marts`), resolves dbt model lineage (`fct_orders` -> `orders`, `lineitem`), and inspects Kustomize-labeled ConfigMaps (`app.kubernetes.io/name=trino-local`).
- **Policy Gatekeeper Bear**: Audits live pod security standards, drop capabilities, root filesystem, and resource limits against resident manifests.
- Verified live on `mac-studio-solo`: all 5 bears execute end-to-end and pass in `make bears-smoke`, recording Pawprint telemetry (`bear.sre_bear`, `bear.query_doctor`, `bear.data_engineer`, `bear.catalog_curator`, `bear.policy_gatekeeper`) to ClickHouse.

### 2026-10-17 — WP-P3-001: Peripheral Engine Optional Plugin Track Formalization (Completed)
- Authored [`docs/design/02_Architecture/02D_Optional_Plugin_Tracks.md`](../02_Architecture/02D_Optional_Plugin_Tracks.md), formalizing the architectural boundaries between the **Resident Core Mainline** (SeaweedFS, PostgreSQL, Polaris, Iceberg, Trino, ClickHouse AgentHouse, dbt, OpenMetadata, Superset, 5 Core Bears) and the **Optional Plugin Tracks**.
- Decoupled secondary engines (Trino Gateway multi-cluster routing, StarRocks, Apache Doris, Apache Gravitino, Apache XTable, Apache Amoro, RustFS, Cube.js, Ray) from the mainline lifecycle to prevent resource bloat and keep the local macOS/Colima lab fast, clean, and reliable.
- Defined four strict plugin guardrails: zero core degradation, Kustomize overlay isolation under `deploy/gitops/plugins/`, strict memory/CPU limits within `solo-core`, and official digest-pinned arm64 images.

### 2026-10-17 — WP-P3-002: Shirokuma Workbench Unified Local Web UI (Completed)
- Implemented `tools/local_experiment/workbench_server.py`, `tools/local_experiment/workbench/index.html`, and Go CLI bindings `internal/cli/workbench.go` (`shirokuma workbench [--port <port>] [--open]`) with `make workbench`.
- Zero-dependency local web architecture using Python standard library `http.server`, serving a high-performance single-page web UI designed with a clean Snowflake/Databricks B2B SaaS aesthetic (dark/light mode, responsive, accessible).
- Features 4 fully integrated operational surfaces:
  1. **Warehouse & SQL Workspace**: Live warehouse state (Active/Chilled/Replicas), 1-click Chill/Wake/Refresh, SQL editor with sample query shortcuts, tabular query results with execution latency.
  2. **Lakehouse Catalog & Lineage Explorer**: Interactive Iceberg schema/table tree (`tpch_tiny`, `staging`, `marts`), column details, and visual dbt model lineage (`dim_customers`, `fct_orders`).
  3. **Autonomous Bears Cockpit**: Interactive controls for all 5 Core Bears (`SRE`, `Query Doctor`, `Data Engineer`, `Catalog Curator`, `Policy Gatekeeper`) with instant formatted finding/recommendation display.
  4. **AgentHouse Pawprints Telemetry**: Real-time ClickHouse telemetry viewer with automatic background polling.
- Unit testing: 14 tests in `tests/test_workbench.py` and 7 tests in `internal/cli/workbench_test.go` (100% green). Live cluster verification passed across all HTTP REST API endpoints and SQL execution.

---

### 2026-10-17 — WP-P4-001: SQL Safety Guardrails, Write-Audit-Publish (WAP), & Iceberg Snapshot Rollback (Completed)

- Added SQL safety checks and WAP sandbox execution to review changes before publishing them to Iceberg.
- Provided Iceberg snapshot rollback to restore an earlier table state.
- Exposed the workflow through the resident warehouse CLI for controlled data changes.

### 2026-10-17 — WP-P4-002: Local File (CSV / JSONL / Parquet) Ingestion into Iceberg (Completed)

- Added `shirokuma lake ingest` for local CSV, TSV, JSONL, and Parquet files.
- Infers source schemas and loads data into a selected Iceberg table.
- Supports configurable ingestion mode and batch size for different input datasets.

### 2026-10-17 — WP-P4-003: Rich Pawprints Audit Trail, Persistent Trino Tunnel, & Workbench WAP/Ingest UI (Completed)

- Enriched AgentHouse Pawprints with audit details for resident operations.
- Reuses the resident Trino tunnel across requests to reduce connection setup overhead.
- Integrated WAP and local file ingestion controls into Shirokuma Workbench.

### 2026-10-17 — WP-P4-004: Fast Resident Core Verification (`make verify-core`) & Phase 4 Roadmap Sync (Completed)

- Added `make test-resident-core` for the 7 resident core test modules and included it in `make verify-local-experiment`.
- Added `make verify-core` to combine resident tests, Go checks, design validation, and repository hygiene checks.
- Synchronized Phase 4 completion records across DEV-04F, DEV-04D, and this milestone ledger.

---

### 2026-10-17 — WP-P5-001: WAP Audit & SQL Self-Correction (Completed)

- Added single-scan WAP quality auditing and regression guards.
- Rewrites QUALIFY queries for resident Trino execution.
- Added AI SQL self-correction for generated queries.

### 2026-10-17 — WP-P5-002: Dynamic WAP Models, Lineage & Refresh (Completed)

- Registers WAP model definitions and their lineage dynamically.
- Provides one-command `--refresh` to rerun registered models.
- Exposes model refresh through the resident Data Bear and Workbench.

### 2026-10-17 — WP-P5-003: Idempotent Upsert Ingestion & Phase 5 Sync (Completed)

- Added `shirokuma lake ingest --mode upsert --key order_id` with comma-separated composite keys.
- Keeps the last input row per key before batching; atomically merges each batch with existing Iceberg rows.
- Added Workbench upsert mode and key input, and synchronized Phase 5 roadmap, gates, and milestones.

---

### 2026-10-17 — WP-P6-001: Resident WAP Module & Shared SQL Helpers (Completed)

- Extracted resident WAP execution into `tools/local_experiment/wap_resident.py`.
- Reused shared SQL helpers across resident Bears, Warehouse, and Ingest workflows.
- Removed duplicated WAP orchestration from the resident Bears module.

### 2026-10-17 — WP-P6-002: Legacy Fixture CLI Removal (Completed)

- Removed retired fixture CLI subcommands and their Go tests.
- Consolidated Warehouse commands around the resident execution path.
- Updated CLI registration and resident command tests for the reduced surface.

### 2026-10-17 — WP-P6-003: Legacy Scripts, Tests, Virtual Environments & Makefile Cleanup (Completed)

- Retained resident core scripts, AgentHouse, Loki, Workbench, and persistent dbt while removing retired fixture trees and over 1.2 GB of unused virtual environments.
- Removed 261 legacy fixture test files and ~910 lines of retired `local-*` Make targets while preserving all 42 CI and resident core test suites.
- Verified `make verify-core` (all 114 resident Python tests and Go CLI tests passing) and synchronized Phase 6 roadmap, gates, and milestones.

### 2026-10-17 — WP-P6-004: CVE Source-Build Cleanup & Verification Alignment (Completed)

- Removed retired CVE source-build scripts, patches, and evidence.
- Removed associated legacy tests and verification dependencies.
- Aligned `make verify` with the retained resident core and upstream images.

### 2026-10-17 — WP-P6-005: Doctor Implementation & Test Simplification (Completed)

- Simplified Doctor JSON handling and component health checks.
- Removed byte-mutation defensive tests from the local Doctor suite.
- Preserved resident health reporting and CLI verification.

### 2026-10-17 — WP-P6-006: AgentHouse Simplification & Phase 6 Sync (Completed)

- Replaced fd-level state handling and byte-level inspection with standard path, JSON, and HTTP operations.
- Preserved saved credentials, resident telemetry APIs, and all five CLI commands; covered chunked and NDJSON responses.
- Synchronized Phase 6 roadmap, gate summary, and milestones with core, full, and live runtime verification.

---

### 2026-10-17 — WP-P7-001: Rich Table Inspector, Time Travel Preview, Upload Preview, & Models Registry APIs (Completed)

- Extended `GET /api/catalog` with `detail=full` (50-row data preview, single-scan row/NULL profile, DDL, upstream/downstream lineage) and `snapshot_id=<id>` for non-destructive Iceberg Time Travel preview (`FOR VERSION AS OF`).
- Added `POST /api/lake/preview` and browser content upload support (`filename` + `content`/`content_base64`) to `lake_ingest.py`, plus `GET /api/models`, `POST /api/models/build`, and restricted `POST /api/catalog/drop`.
- Verified HTTP/1.1 Keep-Alive connection reuse and single-query catalog discovery (`~58ms` catalog load, `~256ms` full table inspector).

### 2026-10-17 — WP-P7-002: Snowsight-Style Navigation & Multi-Feature Workbench Web UI (Completed)

- Redesigned `tools/local_experiment/workbench/index.html` around Snowsight-style grouped navigation (`Work with Data`, `Catalog`, `Manage & Monitor`) across 7 sections with a global header warehouse status bar.
- Upgraded SQL Worksheets with an inline Database Object Explorer, persistent multi-tab worksheets and query history, inline AI Bear Copilot (`Generate SQL`, `Explain Plan`, `Stage & Audit`, `Publish`), client-side sorting/filtering, and CSV/JSON/TSV export.
- Added browser drag-and-drop file upload with schema/row preview, a unified Transformation (`dbt` + `WAP`) view, and a 6-tab Table Inspector with Time Travel snapshot preview and rollback.

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
