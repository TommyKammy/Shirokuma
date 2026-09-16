---
project: Shirokuma
doc_id: "WP-L1-BI-001"
title: "WP-L1-BI-001 Superset bootstrap against Trino only"
status: local-verified
created: 2026-07-05
updated: 2026-09-16
version: "0.7"
area: "workpackage"
tags: [shirokuma, workpackage, l1, bi]
---

# WP-L1-BI-001 Superset bootstrap against Trino only

## Summary

SupersetをL1 BI frontendとしてFlux経由で導入し、Trino上のIceberg datasetを
閲覧して最小dashboardを保存できる状態にする。

## Context

L1ではTrinoだけをBI接続先とする。CubeとStarRocksはL3に属し、このWork
Packageへ先取りしない。

| Field | Value |
|---|---|
| WP ID | WP-L1-BI-001 |
| Level | L1 |
| Workstream | BI |
| Depends-on | WP-L1-QUERY-001 |

## Dependencies

- Depends on: `WP-L1-QUERY-001` and the L0 level gate.

## Scope

Bootstrap Superset as the L1 OSS BI frontend. At L1, the only required connection is Trino. Cube and StarRocks do not exist until L3.

## Non-scope

- Cube, StarRocks, or a semantic-layer integration.
- Production SSO, high availability, or production dashboard migration.
- Direct deployment outside the Flux reconciliation path.

## Deliverables

- Flux-managed Superset deployment with pinned linux/arm64 image evidence.
- Trino connection configuration without committed credentials.
- Deterministic dataset/dashboard smoke fixture and readiness evidence.
- Bootstrap, rollback, and local credential-handling documentation.

## Acceptance Criteria

- Superset starts on arm64-compatible image or documented local profile.
- Superset connects to Trino.
- One TPC-H/Iceberg dataset is browsable.
- A simple chart/dashboard can be saved.
- No Cube or StarRocks acceptance criteria remain in L1.

## Related docs / ADR

- `docs/design/01_Product/010_Project_Charter.md`
- `docs/design/02_Architecture/024_GitOps_Reconciliation_Model.md`
- `docs/design/03_Requirements/030_Functional_Requirements.md`
- `docs/design/03_Requirements/034_Platform_Requirements.md`
- `docs/design/04_Development/044_Issue_and_PR_Workflow.md`
- `docs/design/04_Development/049_Supply_Chain_Security.md`
- `docs/design/10_Research/106_ARM64_Container_Image_Compatibility.md`

## Suggested Labels

`level:L1`, `workstream:bi`, `type:work-package`

## Notes

Cube/StarRocks integration belongs to WP-L3-CUBE-001 and WP-L3-SR-001.

## GitHub Tracking

- Epic: [#24](https://github.com/TommyKammy/Shirokuma/issues/24)
- Issue: [#31](https://github.com/TommyKammy/Shirokuma/issues/31)
- PR: [#56](https://github.com/TommyKammy/Shirokuma/pull/56) (merged)
- Runtime follow-up Epic: [#60](https://github.com/TommyKammy/Shirokuma/issues/60)
- Runtime follow-up Issue: [#65](https://github.com/TommyKammy/Shirokuma/issues/65)
- GitHub depends on: `#29`
- Execution order: `7 of 10`
- Queue: #31 closed after the prerequisite checkpoint merged。runtime
  materializationは#65で行い、Trino runtime Issue #63に依存する。

## Current checkpoint

- 2026-07-16: PR #56で`verify-superset-bootstrap`をfocused blocker
  evidenceとして追加した。
- Repositoryにはadmitted Trino imageと、そのexact imageを使用するTrino workloadが
  まだ存在せず、`SUPERSET_TRINO_RUNTIME_PREREQUISITE_MISSING`でfail-closedとなる。
- Aggregate `make verify`、`make verify-security`、live `make gitops-status`は成功し、
  現行Flux source/Kustomizationは`Ready=True`。PR #56の`Verify`と
  `Supply chain security`も成功した。
- Superset image admission、credential wiring、Flux resources、
  dataset/dashboard smokeはTrino runtime materialization後の実装へ保留する。
- このcheckpointはcredentialまたはsample secretを追加しない。

## Definition of Done

- Work is represented as a GitHub Issue linked to `WP-L1-BI-001`.
- Changes are committed through PR.
- Required CI and policy checks pass.
- Documentation and WBS are updated.
- Any deviation from ARM64-native execution is recorded in [[10_Research/106_ARM64_Container_Image_Compatibility]].

## 2026-09-16 local functional evidence

Superset 6.1.0 native ARM64をloopback限定で導入した。Trino経由で既存Iceberg fixtureの2行を取得し、dataset/chart/dashboardを保存した。再起動・container再作成後の保持とSQLite online backup integrityを確認した。11 focused tests成功。

[ADR-0034](../../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)に基づくlocal functional検証。旧PR/CI/Flux公開gateの履歴は保持し、GitHub完了や本番受入へ読み替えない。実行手順・証跡・復元・未達事項は[Local Experiment Milestones](../061_Local_Experiment_Milestones.md)を参照。
