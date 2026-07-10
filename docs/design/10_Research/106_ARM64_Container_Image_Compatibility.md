---
project: Shirokuma
doc_id: "RES-106"
title: "ARM64 Container Image Compatibility"
status: draft
created: 2026-07-05
updated: 2026-07-10
version: "0.4.1"
area: "research"
tags: [shirokuma, arm64, apple-silicon]
---

# ARM64 Container Image Compatibility

Verification date: 2026-07-05. Primary target: Colima Linux/arm64 on Mac Studio M3 Ultra.

## L0 platform baseline

WP-L0-PLAT-001 uses Colima's native `aarch64` VZ VM and requires Kubernetes
nodes to report `arm64`. No x86_64 emulation, Rosetta, alternate image, or other
ARM64-native deviation is introduced by this Work Package. It adds no resident
service image; component-specific digest and vulnerability evidence remains the
responsibility of the later Work Package that admits that component.

## Policy

- Native `linux/arm64` is required for resident L0-L3 components unless a WP explicitly accepts x86_64/Rosetta.
- x86_64/Rosetta is a fallback, not the default.
- If image support is unknown, the component cannot become resident until a verification WP is complete.

## Supply-chain evidence for resident decisions

An ARM64 compatibility result alone does not admit an image to a resident
profile. The implementing Work Package must add the exact
`repository@sha256:<digest>` reference, upstream version and source, verified
`linux/arm64` platform, retained scan and image SBOM artifact names, scanner
version, and timezone-qualified vulnerability database timestamp to
`security/resident-images.json`. Future vulnerability database timestamps are
rejected. Every tracked image reference under `deploy/` and Helm templates under
`charts/` must match a ledger entry. High or Critical findings keep the image
out of the resident profile.

Fallback images additionally require `fallback: true`, a recorded CVE risk, a
future ISO expiry date, and a replacement plan. MinIO entries are always
fallbacks. Missing, malformed, expired, future-dated, or stale evidence fails
closed. These requirements apply to later resident-component Work Packages;
this L0 baseline does not add any resident service.

## Required components

| Component | Image / Deployment | ARM64 status | v0.2 decision | Source / note |
|---|---|---|---|---|
| Trino | `trinodb/trino` | Verified Docker Hub tags include `linux/arm64` | Resident OK | https://hub.docker.com/r/trinodb/trino/tags |
| Trino Gateway | `trinodb/trino-gateway` | Verified tags include `linux/arm64`; latest tag may lag, pin tested tag | L3 OK; requires PostgreSQL | https://hub.docker.com/r/trinodb/trino-gateway/tags |
| StarRocks | `starrocks/*` Docker Hub | Verified tags include `linux/arm64` | L3 Direct Lake OK | https://hub.docker.com/r/starrocks |
| Apache Doris | `apache/doris` FE/BE/Broker | Verified Docker Hub tags include `linux/arm64` | L5 benchmark-only | https://hub.docker.com/r/apache/doris/tags |
| ClickHouse | `clickhouse/clickhouse-server` | Official docs include arm64 image requirements | L2 resident minimal | https://clickhouse.com/docs/install/docker |
| Apache Polaris | `apache/polaris` | Verified Docker Hub tags include `linux/arm64` | L1 resident OK | https://hub.docker.com/r/apache/polaris/tags |
| OpenMetadata | `openmetadata/server` | Verified Docker Hub tags include `linux/arm64` | L1/L2 OK | https://hub.docker.com/r/openmetadata/server/tags |
| Apache Gravitino | Docker Compose / binary / images | Tags observed as linux/arm64; runtime behavior still to be tested in L5 | L5 only after runtime verification | https://hub.docker.com/r/apache/gravitino/tags |
| Apache Amoro | `apache/amoro` | Docker Hub tags include `linux/arm64`, but releases are old/incubating | L6 only | https://hub.docker.com/r/apache/amoro/tags |
| Spark | Bitnami/Apache/Spark images | Verify selected chart/image | L4 on-demand | Record exact image in WP-L4-SPARK-001 |
| DataFusion Comet | JAR with Spark | Container inherited from Spark image | First candidate | Test on arm64 Spark image |
| Gluten | Spark plugin/JAR/native backend | arm64 support uncertain; Velox path likely constrained | Bonus only | Verify before acceptance |
| MinIO | `minio/minio` or source build | Repo archived/no longer maintained/source-only; arm64 may work only for pinned legacy/source builds | fallback only | https://github.com/minio/minio |
| SeaweedFS | `chrislusf/seaweedfs` or official build | Verify selected linux/arm64 tag during WP-L1-LAKE-001 | L1 primary | https://github.com/seaweedfs/seaweedfs |
| RustFS | project image | Verify selected tag and maturity | experiment | https://github.com/rustfs/rustfs |
| Cube Core | container image | Verify selected tag before L3 | L3 | https://github.com/cube-js/cube |
| Superset | official image/chart | Verify selected tag before L1 | L1 | https://superset.apache.org |
| Dagster | official image | Verify selected tag before L1 | L1 | https://github.com/dagster-io/dagster |
| dbt Core | Python/Rust binary/container | Native execution on macOS/arm64 or container; verify exact runner | L1 | https://github.com/dbt-labs/dbt-core |
| OpenHands | container | Verify image arch; can run locally with selected backend | L0/L2 | https://github.com/All-Hands-AI/OpenHands |

## WP decision rules

- `native-arm64`: accepted resident.
- `rosetta-accepted`: x86_64 image accepted for a specific experiment only.
- `scope-out`: removed from local profile until cloud/x86 profile.
- `side-only`: may be documented but not part of resident boot.
