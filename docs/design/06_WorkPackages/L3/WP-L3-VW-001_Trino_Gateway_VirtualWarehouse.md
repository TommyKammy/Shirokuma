---
project: Shirokuma
doc_id: "WP-L3-VW-001"
title: "Trino Gateway VirtualWarehouse"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.4"
area: "workpackage"
tags: [shirokuma, wp, l3, warehouse]
---

# Trino Gateway VirtualWarehouse

| Field | Value |
|---|---|
| WP ID | WP-L3-VW-001 |
| Level | L3 |
| Workstream | Warehouse |
| Depends-on | WP-L1-QUERY-001 |

## Scope

Deploy Trino Gateway and at least two backend Trino clusters/classes to emulate virtual warehouses.

## Acceptance Criteria

- PostgreSQL backend for Trino Gateway is deployed and backed up.
- Gateway routes to at least two backend clusters/classes.
- Routing rules are Git-managed.
- Gateway query history retention is configured or disabled intentionally.
- VirtualWarehouse CRD draft maps to Gateway backend routing.

## Notes

Trino Gateway requires MySQL/PostgreSQL/Oracle backend DB. v0.2 uses PostgreSQL.

## Definition of Done

- Work is represented as a GitHub Issue linked to `WP-L3-VW-001`.
- Changes are committed through PR.
- Required CI and policy checks pass.
- Documentation and WBS are updated.
- Any deviation from ARM64-native execution is recorded in [[10_Research/106_ARM64_Container_Image_Compatibility]].

## 2026-09-17 local experiment plan

ADR-0034/DEV-04Bの承認済みlocal loopで実装を進める。旧Issue/PR/都度owner gateは
外部delivery向けに残し、この非本番実験の実行条件にはしない。
新規の同一PodにGateway21・専用PostgreSQL18.4・Trino483のXS/S単一coordinatorを置く。
保持Trino、catalog、Lakehouse、DB/PVC、credentialへ接続しない。合成TPCHだけを使用する。
XS/Sはそれぞれ別node.id/discovery/config/resourcesで動くが、同じPod/hostを共有するため
tenant isolation、独立障害domain、性能優位や実際のautoscalingを証明するものではない。

- [x] ARM64固定imageとprivate fixtureを準備する。新規Pod/CM/Secret/NetworkPolicyのみ生成する。
- [x] FILE routing rulesとGit管理のXS/S→backend/group対応を検証する。
- [x] XS/Sが25 nation行＋異なる実node_idを返し、両backend metadataでquery ID/Source所属を照合する。
- [x] query継続GETに逆class headerを付けても元query ID/backendを維持する。GET0件は未観測とする。
- [x] 未知class/欠落headerがどちらのbackendにも到達せず、既知classは前後に成功する。
- [x] 両classの固定DROPはread-only native authorizerのPERMISSION_DENIEDで拒否し、後続SELECTが不変。
- [x] history保存を明示的に無効化し、DBの空query historyとrouting cacheの継続機能を分けて検証する。
- [x] 新規DBをprivate bounded dumpへ保存し、同じ新規PG内の別新規DBへ復元、schema/dataを比較する。
- [x] 前後readiness、既知UIDだけのcleanup/保存planによるrecover、保持環境不変、testsを確認する。

Gateway21は未知groupをdefault groupへfallbackする。未登録の`local-unrouted`をdefaultにし、
XS/Sの2groupだけを登録する。negativeのHTTP statusはsource/live証拠から特定し、
一般的なtimeoutや全backend停止を正しいroute拒否と扱わない。これは認可ではなく経路選択の検証。
Gateway18580だけをworkload入口とし、18581/18582は固定metadataの独立観測用に限定する。
すべて新規Podへのloopback port-forward。MAC hosts/truststore/既存serviceは変更しない。

VirtualWarehouseは既存MOD-053のdraftへ対応表を付けるだけで、controller/CRD適用、replica管理、
idle suspend/wake、cost explain、HA、利用者認証/warehouse権限、外部公開はこの増分に含めない。
Cloud API/課金は不要。全て新しい使い捨て実験データで、保持data/backupは削除しない。

一次source: [Gateway21](https://github.com/trinodb/trino-gateway/releases/tag/21)、
[設定](https://trinodb.github.io/trino-gateway/installation/)、
[default fallback](https://github.com/trinodb/trino-gateway/blob/21/gateway-ha/src/main/java/io/trino/gateway/ha/router/BaseRoutingManager.java)。

## 2026-09-17 local result

Build `b3811eea3c3c494d8641dff5ddd5f4cc`、plan-only `08f7e0dba9a84988b856822a931e2f10`、
実行 `2823f7f9bc0842c1adf54f37b0b9cc0e` が初回で成功。8/8caseと両backendの6query履歴が一致。
XS/Sの25行＋実node IDが一致し、最初の逆class付き継続GETはXS5回/S4回、同じquery IDを保持した。
未知class（legacy group=local-xs併用）/header欠落はHTTP500・空bodyとなり、両Sourceは両backendで0件。
これは認可errorではなく未配送の経路不一致。両固定DROPはPERMISSION_DENIEDで拒否、後続SELECT不変。
起動前後4readinessチェック、空registryからの固定2件登録/読戻し、Gateway側HEALTHY2件を保存した。

PG18.4でFlyway4migration、2backend、history0件を確認。5363byteのprivate custom dumpを
新規vw_restoredへ復元し、schema/index/dataと元DB再読が完全一致。
source/restored logical SHA256 `e5a414e994771a958c0cfbf1c874380a0ff7a9345d9ca36c32509b1e8fdf0ded`。
4生成UIDを回収し、保存planによるrecover再実行は追加削除0。通常証跡にpasswordは含めない。
raw結果行はliveで比較し、保存後の独立再検証は行hash/count/identity射影までとする。

49 focused testsを含む全回帰740成功＋標準環境1skip、78documentsを検証。
同じoptional OTel結合は専用venvの必須targetで成功。実験のreceipt/private dumpは
`tmp/local-experiment/gateway-warehouse/runs/2823f7f9bc0842c1adf54f37b0b9cc0e`、
独立前後証跡は`gateway-warehouse-preflight/7c1d0d4027eb4e9aa03fbffc114fded5`。
保持Kube/Docker、311Pawprints＋履歴4表、5DDL、保護19filesは独立前後照合で不変。controller/CRD実適用/認証/HA/耐久backupは未達。
次はWP-L3-VW-002の分離backendによるidle停止・再開実験へ進む。
