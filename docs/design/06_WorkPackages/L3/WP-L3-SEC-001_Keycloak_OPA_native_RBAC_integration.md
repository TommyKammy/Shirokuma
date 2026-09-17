---
project: Shirokuma
doc_id: "WP-L3-SEC-001"
title: "Keycloak OPA and native RBAC integration"
status: active
created: 2026-07-05
updated: 2026-09-17
version: "0.3"
area: "workpackage"
tags: [shirokuma, wp, l3, security]
---

# Keycloak OPA and native RBAC integration

| Field | Value |
|---|---|
| WP ID | WP-L3-SEC-001 |
| Level | L3 |
| Workstream | Security |
| Depends-on | WP-L0-POL-001, WP-L1-QUERY-001 |

## Scope

Implement the default security path without Apache Ranger: Keycloak/OIDC optional identity, OPA policy, Trino OPA authorizer, StarRocks native RBAC, ClickHouse native RBAC.

## Acceptance Criteria

- Trino OPA authorizer is configured in dev.
- A row or column policy example is tested in Trino.
- StarRocks and ClickHouse RBAC examples are documented.
- Policy decisions are visible to agents through documented metadata/Pawprints.
- Ranger is not installed in default L3.

## Notes

Ranger remains a future side option only.

## Definition of Done

- Work is represented as a GitHub Issue linked to `WP-L3-SEC-001`.
- Changes are committed through PR.
- Required CI and policy checks pass.
- Documentation and WBS are updated.
- Any deviation from ARM64-native execution is recorded in [[10_Research/106_ARM64_Container_Image_Compatibility]].

## 2026-09-17 local isolated implementation plan

ADR-0034に沿って、保持Trino/Lakehouseを変更しない一時Podの実験から進める。
既存の検証済みTrino483 archiveからOPA/TPCH pluginだけのARM64 imageを新規local buildし、
OPA sidecarで合成userごとの許可・拒否・行filter・列maskを固定TPCH tiny.nationで確認する。
Polaris/S3 credentials、既存Iceberg table、PVC、Secret、外部公開には接続しない。

- [ ] exact input hashes / native ARM64 / bounded resource設定を検証する。
- [ ] 固定SELECTで全25行、行filter、列mask、denyを独立期待値へ照合する。
- [ ] OPAに接続できない別の使い捨てPodで、具体的なOPA通信失敗と結果非返却を確認する。
- [ ] UUID/owner/UIDを記録したPod/ConfigMap/NetworkPolicyだけをcleanupし、保持workloads不変を確認する。
- [ ] local tests・実機receipt・再実行/復旧手順を保存する。

Trinoの未認証user headerは呼出側が偽装できる。この実験はsynthetic identityに応じた
認可判断の機能検証であり、認証・Keycloak/OIDC統合・production securityを保証しない。
StarRocks/RBAC全体、Pawprintへのpolicy決定相関は後続未達。Grafana専用ClickHouse readerの
SELECT範囲/readonly実測は先行する限定RBACの参考証拠として、L3全体完了と分ける。
Issue/PR/過去の都度承認gateは外部deliveryの境界であり、このlocal実験の停止条件にしない。
