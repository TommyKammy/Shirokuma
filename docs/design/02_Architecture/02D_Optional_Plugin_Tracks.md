---
project: Shirokuma
doc_id: "ARCH-02D"
title: "Optional Plugin Tracks Architecture"
status: active
created: 2026-10-17
updated: 2026-10-17
version: "1.0.0"
area: "architecture"
tags: [shirokuma, architecture, plugins, optional-tracks]
---

# Optional Plugin Tracks Architecture (WP-P3-001)

> [!NOTE]
> **Context & Governing Principles**:
> Under [ADR-0036](../07_ADR/ADR-0036_Adopt_Commander_Worker_Model_and_Pragmatic_Core_Reset.md) and [DEV-04F](../04_Development/04F_Commander_Worker_and_Pragmatic_Roadmap.md), Shirokuma prioritizes a lean, stable, resident core lakehouse over speculative engine sprawl.
> This document formalizes the boundary between the **Resident Core Mainline** and the **Optional Plugin Tracks**.

---

## 1. Boundary Definition: Core Mainline vs. Optional Tracks

### 1.1 Resident Core Mainline (Always-On in `solo-core`)

The Core Mainline provides an end-to-end Snowflake/Databricks-like Lakehouse experience on Apple Silicon (`mac-studio-solo`), requiring zero cloud egress or paid SaaS dependencies:

| Component | Role | Runtime / Location |
|---|---|---|
| **SeaweedFS (S3)** | High-performance local object storage | Kubernetes (`seaweedfs-s3:8333`), bucket `shirokuma-lake` |
| **PostgreSQL** | Metastore backend for Polaris | Kubernetes (`polaris-postgresql:5432`) |
| **Apache Polaris** | Apache Iceberg REST Catalog | Kubernetes (`polaris:8181`), catalog `default-catalog` |
| **Apache Iceberg** | Open table format with Parquet storage | Persistent tables in `tpch_tiny`, `staging`, `marts` |
| **Trino (483)** | Distributed SQL query engine & federation | Kubernetes (`trino-local:8080`), single shared warehouse backend |
| **ClickHouse (AgentHouse)** | Audit telemetry, Pawprints, and vector logs | Kubernetes (`agenthouse-clickhouse:8123`), database `agenthouse` |
| **dbt-core / Dagster** | Data transformation, staging views & dimensional marts | Local Python virtualenv & pipeline runner (`lake-pipeline.py`) |
| **OpenMetadata / Superset** | Metadata lineage & BI visualization | Kubernetes (`openmetadata:8585`, `superset:8088`) |
| **5 Core Bears** | Autonomous agent operators (SRE, Query Doctor, Data, Catalog, Policy) | Go CLI (`shirokuma bear *`) & local LLM (`127.0.0.1:8001`) |

### 1.2 Optional Plugin Tracks (Opt-In / Evaluated on Demand)

Peripheral engines and secondary experimental technologies are decoupled from the core lifecycle (`lab-up`, `doctor`, `bears-smoke`). They must never be required for core lakehouse operations:

| Category | Engine / Technology | Motivation for Optional Track | Activation Condition |
|---|---|---|---|
| **Warehouse Routing** | **Trino Gateway Multi-Cluster** | Single shared `trino-local` satisfies local dev; physical multi-cluster (XS vs S) adds unnecessary memory overhead on macOS. | Dedicated benchmark or multi-tenant stress testing. |
| **Alternative MPP / OLAP** | **StarRocks** | Excellent for sub-second CBO queries, but duplicates Trino + ClickHouse query capabilities while requiring >8GB RAM. | Low-latency dashboarding experiment where Trino latency is insufficient. |
| **Alternative MPP / OLAP** | **Apache Doris** | Similar to StarRocks; adds redundant operational overhead to resident lab. | Comparative MPP benchmark track. |
| **Metadata & Catalogs** | **Apache Gravitino** | Multi-catalog federation (Iceberg + Hive + relational); Polaris already handles Iceberg REST catalog natively. | Cross-catalog federation testing. |
| **Format Conversion** | **Apache XTable (OneTable)** | UniForm-style translation (Delta Lake / Hudi / Iceberg); Iceberg is the single canonical format for Shirokuma. | Multi-format legacy ingestion requirement. |
| **Lakehouse Optimization** | **Apache Amoro** | Autonomous Iceberg file compaction & optimization; manual or scheduled dbt compaction suffices in local dev. | Continuous streaming ingestion evaluation. |
| **Alternative S3 Engine** | **RustFS** | S3 engine evaluation; SeaweedFS is already fully verified and stable on arm64. | Storage layer latency research. |
| **Semantic & Compute** | **Cube.js** | Headless BI semantic layer; dbt persistent marts cover current dimensional modeling needs. | External BI API gateway experiment. |
| **Distributed AI Compute**| **Ray** | Distributed model training / orchestration; local MLX / OpenAI endpoint runs outside Colima on host. | Distributed batch embedding / LLM fine-tuning track. |

---

## 2. Optional Plugin Contract & Rules

1. **Zero Core Degradation**:
   Enabling or disabling an Optional Plugin must never break `make lab-status`, `make verify`, `make lake-test`, or `make bears-smoke`.
2. **Kustomize Overlay Pattern**:
   All Optional Plugins must reside under `deploy/gitops/plugins/<plugin-name>/` as Kustomize overlays. They must never be included in `clusters/local-lite/kustomization.yaml` by default.
3. **Resource Guardrails**:
   Any Optional Plugin must declare strict CPU/Memory requests and limits. The total footprint must not exceed the `solo-core` memory ceiling (192GB Colima total, keeping >=192GB host RAM free for macOS and local LLM).
4. **Digest Pinned Images**:
   Official ARM64 images pinned to exact SHA256 digests must be used. Upstream CVE source surgery is forbidden.
