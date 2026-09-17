---
project: Shirokuma
doc_id: "WP-L3-SEC-001"
title: "Keycloak OPA and native RBAC integration"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.4"
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

- [x] exact input hashes / native ARM64 / bounded resource設定を検証する。
- [x] 固定SELECTで全25行、行filter、列mask、denyを独立期待値へ照合する。
- [x] OPAに接続できない別の使い捨てPodで、具体的なOPA通信失敗と結果非返却を確認する。
- [x] UUID/owner/UIDを記録したPod/ConfigMap/NetworkPolicyだけをcleanupし、保持workloads不変を確認する。
- [x] local tests・実機receipt・再実行/復旧手順を保存する。

Trinoの未認証user headerは呼出側が偽装できる。この実験はsynthetic identityに応じた
認可判断の機能検証であり、認証・Keycloak/OIDC統合・production securityを保証しない。
StarRocks/RBAC全体、Pawprintへのpolicy決定相関は後続未達。Grafana専用ClickHouse readerの
SELECT範囲/readonly実測は先行する限定RBACの参考証拠として、L3全体完了と分ける。
Issue/PR/過去の都度承認gateは外部deliveryの境界であり、このlocal実験の停止条件にしない。

## 2026-09-17: isolated authorization verified

Trino483/OPA1.20.2をnative ARM64でlocal buildし、使い捨てTPCH-only Podで8caseが成功した。
全25行、nationkey=0の1行制限、name列をMASKEDにした25行、両方を適用した1行、
未許可user/columnの拒否0行を独立期待値と照合した。同じclientがadmin headerを選んで
全行を読めるcaseも明示的に確認し、未認証headerが認証の代わりにならないことを示した。
正常Pod撤去後、別PodでOPA endpointだけを未待受portへ変えた同SELECTは、
Trino OPA QueryFailed＋ConnectException・返却0行になった。一般errorを合格にしない。
Trino readiness/versionはquery前後に厳密確認するコード経路を通過しているが、
個別readiness応答の保存はなく、削除後の実機再観測を意味しない。

成功run: `trino-opa/runs/02986c45d05247d0918cd7cae440f380`。
OPA decision59件のうち52件はquery ID＋user、7件のExecuteQueryはquery IDがないため
user/operationだけの限定照合として保存。policy拒否・rowfilter・columnmaskを実結果へ対応付けた。
これはprivate decision receiptであり、まだPawprints/MCPへのpolicy telemetry統合ではない。

初回`86d71344797f44eea55658b9e5c36d06`はHTTP200とstarting=falseの起動差で停止し、
最大120秒の厳密起動待ちを追加。次の`719f5e739aab48bba48de7f5ca13bcfc`は正常7caseに成功したが、
Java nested classの実JSON表記差で不達判定を拒否した。固定483の実表記と接続拒否を照合する修正を
加えて再試験した。失敗receipt/diagnostic/build4件を消去・成功へ書換えせず保持する。

Pod/ConfigMap/NetworkPolicyだけをUUIDで作成し、既知UID/resourceVersion付き削除で回収。
UID未確定のcreateは採用せずdeferredに残す。復旧は保存planのscope/hash/imageを直接検証し、
現行Rego/PINS/manifest生成コードへの依存を外した。初回runのrecover再実行は0deletesで成功。
全回帰632 tests、focused23 tests、native OPA policy8 tests、設計76documentsが成功。

### Inputs and runtime boundary

build receipt: `trino-opa/builds/55cc059f9d9648b0a28191b13905b6c7`。
Trino image: `sha256:420d88cf037c400e23ed42a319f0683d6753a525dc94d8f46bfdd57d86aacaa3`。
OPA image: `sha256:9bf48d2d88d954b562ee2090a9887814966707ec55f676f7c5127afb4f29918b`。
OPA公式ARM64 static binaryは`sha256:431bed5a365578241ab06c7cc1c7d0cdff8c11dcbc6f12c3488590deb8b8d66d`。
実験は最大同時1Pod、2.5CPU/4.25GiB、read-only rootfs、non-root、token非mount、
256Mi/512Mi emptyDir。ingress/egress拒否、OPAは同Pod loopback、hostはloopback port-forwardだけ。
既存Lakehouse/PVC/Secretへ接続せず、runtimeを残さない。image/cacheはローカルに保持する。

再実行/復旧: repository `tools/local_experiment/trino_opa/README.md`。
`rtk make local-trino-opa-run TRINO_OPA_BUILD_TOKEN=<build token>`。
このWPはlocal-partial。次は新しい隔離環境でKeycloakの認証とTrinoのidentity bindingを検証する。
StarRocks RBAC・policy telemetry・本番security acceptanceは未達を維持する。

### Independent retained-state check

06:16 UTCの独立照合で、既存6PodsのUID/spec/phase/ready/restart、27resourcesのUID/generation/spec、
6ConfigMapsのUID/data/binaryData/immutableがbaselineと完全一致。3実機runの合計15生成UIDは残留0。
成功receiptの8case・固定SQL hash・59decisions・全既知UID cleanupを再照合した。
原文rowsや決定log全bytesは成功receiptにはなく、保存射影の再計算という証拠範囲を明記した。
private証跡`trino-opa-preflight/d14316b781e74ec0ab18804df9ac8739/after.json`。
