---
project: Shirokuma
doc_id: "WP-L3-VW-002"
title: "Idle suspend and wake for VirtualWarehouse"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.4"
area: "workpackage"
tags: [shirokuma, wp, l3, warehouse]
---

# Idle suspend and wake for VirtualWarehouse

| Field | Value |
|---|---|
| WP ID | WP-L3-VW-002 |
| Level | L3 |
| Workstream | Warehouse |
| Depends-on | WP-L3-VW-001, WP-L0-OBS-001 |

## Scope

Implement idle suspend/wake semantics for Trino VirtualWarehouse classes. This is a Shirokuma controller behavior, not native Trino scale-to-zero.

## Acceptance Criteria

- Controller detects idle warehouse.
- Coordinator/worker deployments can be stopped and restarted.
- Gateway routing is updated or health checks prevent routing to stopped backend.
- `shirokuma chill` and `shirokuma wake` commands operate on warehouse definitions.
- Pawprints record suspend/wake decisions.

## Notes

Plain Trino does not provide Snowflake-like warehouse suspend by itself.

## Definition of Done

- Work is represented as a GitHub Issue linked to `WP-L3-VW-002`.
- Changes are committed through PR.
- Required CI and policy checks pass.
- Documentation and WBS are updated.
- Any deviation from ARM64-native execution is recorded in [[10_Research/106_ARM64_Container_Image_Compatibility]].

## 2026-09-17 local experiment plan

ADR-0034/DEV-04Bの承認済みlocal loopで、新規runだけを対象に停止/再開を実装する。
旧Issue/PR/都度承認gateは外部delivery向けであり、このlocal loopの実行条件にはしない。
Gateway/PGを専用Pod、Trino XS/Sを別々の新規Deployment＋Serviceに分離し、
11新規resourceとcontroller生成RS/Podのowner UID chainだけを管理する。
既存Trino/catalog/Lakehouse/DB/PVCへの接続、ホスト/Colima全体の変更は行わない。
固定ARM64 imagesはVW-001のbuild証跡を再利用する。合成TPCHの同じ25nationを検証する。
Serviceの実clusterIPは作成応答と所有権を照合して登録し、DNS/外部向け通信許可は追加しない。

停止判断にはfreshな全query一覧とhost側POST intent/未完了handleの両方を使う。
Trinoは受付直後のqueryが最初のGETまで一覧へ現れないため、サーバーの0件だけでidleとしない。
既知handleの未完了・POST結果不明・別client queryがあれば停止せず理由を保存する。
既知queryの終端を確認してからGateway deactivate→再drain確認→UID/RV付きreplicas0。
wakeは同じDeploymentをreplicas1へ戻し、新しいPod UID/readiness確認後にactivateする。
停止中もSへのSELECTが成功し、XSがSへfallbackしないことと、再開後のXS結果不変を確認する。

同一hostの単一coordinator/worker構成であり、本番の分散drain、独立fault domain、
HA、汎用CRD reconciliation、無期限の自動運転、原価削減率を証明しない。
CLIとPawprints連携を含め観測できた範囲だけをlocal-partialとして記録する。
未観測項目や実装段階は実機結果と分け、実行前の計画を完了扱いにはしない。

## 2026-09-17 local functional evidence

同期run `97b8a7289b2e42e496a1d87bddd2b4ad` とCLI session
`c41cf9f753c14275837ee9f453ed2ad0` が成功した。どちらも7固定requestを検証し、
初期XS/S・未完了handleの消費・停止中S・再開後XS/Sの6SELECTが25nation行と実node IDで一致。
停止中XSはHTTP500・空body・query IDなしでSの全query一覧へ現れず、Sのprocess IDは不変。
host未完了handleが1件ある時、サーバーの全query一覧が0件でも停止を延期した。
完了後のfreshなidle/drain観測を経て、同じXS Deploymentだけをreplicas1→0→1へ変更し、
新しいPod UIDとTrino coordinator ID、空の新XS履歴、Gatewayの再配送を確認した。

実compiled Go CLIから6commandを実行：prepared状態のwakeはnoop、chillはverified、
同request IDのchill再送は同一応答、別IDのchillはnoop、wakeはverified、その再送も同一応答。
4個のowner operation receiptと6応答をprivate保存し、再送によるscale追加は0。
socketは同UIDの0700 directory/0600 Unix socket、型付きrequest/responseとdurable journalを使用。
受付は準備後10分・16接続、成功wake後15秒は既存応答の再取得だけを許す。
CLI切断は実行開始済み操作を取り消さず、ownerが検証・回収まで担当する。
これは生成済み固定planのXSだけを対象にした短命sessionで、任意warehouse定義の操作ではない。

各runは17個の既知UIDを明示回収し、停止時にcontrollerが消した旧XS Podも残留0。
3decision＋1runのPawprint/Loki/実OTel spanをそれぞれ4件照合した。
busy-deferはblockedだがpolicy拒否ではなく、usage/costはNULL。
同期trace `2823135a5def108632ed71493ce72530`、CLI trace `ced9a0cbc583bf91dbeed4541eddf247`。
OTel child spanは監査deliveryを計測し、先に実行したcontroller操作時間を遡ってtrace化しない。
Pawprintには別途測定したcontroller durationを保存する。

初期4runはfailedとして保持した。Kubernetes生成Pod名の切り詰め、空EndpointSliceのnull値、
停止中PodのSucceeded状態、Gateway inactiveとhealth表示の非同期更新、TCP TIME_WAITを修正し、
UID/spec/配送対象の確認を維持して回帰テストを追加した。最初のrecover失敗もarchiveを残し、
次のrecoverで既知UIDのみ回収した。CLI初回はscript相対importでruntime生成前に失敗し、
入口のimport修正と実script回帰を追加した。失敗receiptを成功へ書き換えていない。

証跡は `tmp/local-experiment/warehouse-lifecycle/runs/<run-token>`。
[Runbook](../../../../tools/local_experiment/warehouse_lifecycle/README.md)とMake targetsで再実行できる。
汎用CRD reconciliation、複数workerのgraceful drain、tenant認可/HA/常駐運転/本番受入は未達。
次は既存Data Bearの3固定operationを自然文入力から利用できる入口を実装する。

独立最終監査 `warehouse-lifecycle-audit/80d8bfbffe19418b82b249aed451dd92`（08:17:20 UTC）で、
保持6Pods/27resources/6CM/3Secret UIDs、3Dockerの全設定/起動時刻/restarts、履歴4表の全行hash/5DDL、
従来22証跡filesが不変。Pawprints330＝従来311件不変＋今回19件、同じLoki19件と保存span IDsを照合。
失敗4runの11件も残し、成功2run各4件と区別した。全6runtime runsと初CLI startup failureの残留0。
CLI6応答/4operationのraw SHA256、実binary hash、同IDの完全同一応答、scale2回だけを独立再確認。
全回帰836件（835成功・標準環境のoptional OTel1skip）後、更新済みfocused112件がすべて成功。
同OTel結合は専用venvで成功。Go test/vet/race、設計79documents/関連14tests、diff checkも成功。
