---
project: Shirokuma
doc_id: "WP-L3-CH-001"
title: "ClickHouse AgentHouse advanced dashboards"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.5"
area: "workpackage"
tags: [shirokuma, wp, l3, observability]
---

# ClickHouse AgentHouse advanced dashboards

| Field | Value |
|---|---|
| WP ID | WP-L3-CH-001 |
| Level | L3 |
| Workstream | Observability |
| Depends-on | WP-L2-CH-001, WP-L2-OBS-001 |

## Scope

Extend the L2 ClickHouse AgentHouse with dashboards, benchmark events, query history extracts, and cost analytics.

## Acceptance Criteria

- Grafana dashboard shows agent PR success, policy blocks, token cost, and latency.
- Benchmark events are stored.
- Query history import path from Trino/Trino Gateway is documented.
- L2 schema migration is backward compatible.

## Notes

ClickHouse installation itself moved to L2.

## Definition of Done

- Work is represented as a GitHub Issue linked to `WP-L3-CH-001`.
- Changes are committed through PR.
- Required CI and policy checks pass.
- Documentation and WBS are updated.
- Any deviation from ARM64-native execution is recorded in [[10_Research/106_ARM64_Container_Image_Compatibility]].

## 2026-09-17 local bounded implementation plan

ADR-0034の包括承認で既存AgentHouse Pawprintsを読むGrafana dashboardを追加する。
Native ARM64のGrafana OSS/公式ClickHouse pluginを固定し、専用Docker networkと
loopback公開、専用SELECT-only DB userを使用する。L2 schema/保持データは変更しない。
GrafanaのadminとDB credentialsはprivate ignored filesに保存し、画面/ログ/Gitへ転記しない。

- [x] 記録されたfailed/blocked eventsからtask/traceを絞り込める。
- [x] 処理別p50/p95を表示し、親/子durationを合計してwall timeとしない。
- [x] provider/modelごとのtokens既知小計・既知/欠測callsを表示し、mockと実モデルを分離する。
- [x] null費用は未観測とし、PR成功率・通貨換算・一般モデル品質を捏造しない。
- [x] ReplacingMergeTreeをFINALで読み、retry物理重複をlogical eventとして数えない。
- [x] 権限・resource/row/time bounds、所有inventory、repeat up、stop/recoveryを検証する。
- [x] 実dashboard query・API・画面・tests・Vault/WBSを確認する。

この増分ではGrafana画面は読み取り専用。監査eventの成功はworkflow全体完了や
本番受入を意味しない。全Taskの成功率はterminal契約が統一されるまで表示しない。
benchmark event取込、Trino query history持続化、実費計測は後続未達とする。
歴史的Issue/PR/CI gateは外部delivery経路でありlocal作業を止めない。

## 2026-09-17: local Grafana runtime

Grafana OSS 13.2.2と公式ClickHouse plugin 4.21.3をnative ARM64で起動した。
Imageは`sha256:d523f1346c0cd277de119bc1acb21b39ec89427975ae8b0815561698e859f725`、
plugin archiveは`sha256:da578d58f2fb1be55fab52f8a4eb97b9a5f6a7d18870669ba30537321a53f43c`。
Grafana APIでplugin signature=`valid`を確認。これはlocal互換性の証拠であり、
CVE判定・外部公開用admission・本番受入を更新するものではない。

[AgentHouse画面](http://127.0.0.1:18030/d/shirokuma-agenthouse-local)は匿名Viewerのみ。
1 CPU / 1GiB、UID472、read-only rootfs、cap-drop ALLで稼働する。
DBにはSELECT-only userを一個追加し、保持data tableのgrantをPawprintsだけに限定し、書込み・管理権限がないこと、
readonly/最大時間30秒/最大結果1万行・4MiB/最大読込100万行/メモリ256MiBを確認した。
Credentialはprivate ignored stateに保存し、既存writerをGrafanaへ渡さない。

実機で内部bridgeだけではDockerのport publishが有効にならなかったため、
DB用internal bridgeとは別にGrafana専用の通常bridgeを追加した。
ホスト公開は`127.0.0.1:18030`のみで、CHの従来port/volumeは維持する。
またdriverがdeadlineへ5秒を加えるため、plugin timeoutを25秒に設定し、
DB側の30秒上限を緩和せず接続した。初回の失敗ログも保持する。

Task/Traceは厳密percent encodingを経てClickHouseで復号する。
バックスラッシュと引用符によるSQL混入を実DB/画面で検査した。
本文・状態の文字切れと色のコントラストを実画面で修正した。
Pawprint statusはevent結果であり、PR成功率・Task全体の成功率として表示しない。

再実行: `rtk make local-grafana-up local-grafana-smoke`。
詳細なquery照合・復元手順はrepositoryの`tools/local_experiment/grafana/README.md`。
private証跡は`tmp/local-experiment/grafana-preflight/b781f2d079d8469c9e1bf274ab83d374/`。

### Final bounded verification

05:26 UTCの再検証で、5panel×8caseの40/40をanonymous Viewer APIと専用readerの前後SELECTで照合。
全体、既知Task/Trace別々・同時、不一致、悪意ある文字列の全caseを含む。
列型・数値・NULL・行数上限を検査し、empty frameの列省略は独立参照も0行のときだけ受理する。
最終runは`grafana-verification/9785773e72e540f2ad63c014db659b70`。
初回`b5369cca4d0b490aae98bb1401a96327`の空frame期待ミスもfailed証跡として残した。

起動の冪等性、停止/再起動、専用networkの3attachments撤去→復元を実機確認した。
停止中endpointがDocker network inventoryから消える挙動を考慮し、container側の接続と
NetworkIDを照合して切断、同container IDの再取得で実absenceを要求する。
Grafana ID/volume/env/credentialsは保持。CHは元のbridgeだけへ戻り、その後の復元も成功した。
CHのID/image/env/volume/port/start時刻/restart回数、監査294件の全bytes/hashは前後不変。
既存5runtime groupsとGrafanaの別smokeがready。新たなLLM呼出はない。

focused46 tests（runtime26/dashboard7/verifier13）、全回帰552 testsと設計75documentsが成功。
本WPはlocal-partialを維持し、次は保存済みbenchmark全3runの失敗を含む冪等取込を実装する。

## 2026-09-17: retained benchmark events

既存TPC-H SF10の全3run（成功1・失敗2）を検査し、専用`benchmark_runs`へ3行、
`benchmark_queries`へ実保存query-sideの72行を登録した。summaryに記録された36比較は
元result filesを再比較した。予定66比較の残り30は未観測で、失敗/実行済みと推定しない。
旧receiptのSQL hash/adaptation欠損はunknown groupのままで、Q15修正版と性能合算しない。
NULLと測定0を区別し、raw SQL/rows/errors/host pathsはDBへ渡さない。

新規2tableは固定schema/owner COMMENT/engine/partition/TTL/default条件を照合し、
既存PawprintsにはALTER/書込みを行わない。固定Colima runtimeのimage/loopback/volume/resourcesを
確認してから既存private資格情報を使用する。固定sourceの全UUIDを検査し、malformed/incompleteを
隠さずplanへ表示、apply全体を止める。保存failed runは正常な履歴として取り込む。

初回`benchmark-events/e2c5fd7ea9c24fd1818846399aa2846b`で3/72行追加、
再実行`998612d03fb44bbc86a8df3306262f10`は追加0/0・全論理行一致。
05:32 UTCの独立検証で元137files、Pawprint schemaと294件の全hashが不変。
証跡`benchmark-events-preflight/013ba0f37c944c69a7f6710cd473ff7d`。
21focused tests（衝突・部分失敗/中断・再開・source改変・権限/定義逸脱など）、全回帰573件、
設計75documentsが成功。追加benchmark/model/runtimeは実行せず、Grafana reader grantも維持。

再実行: `rtk make local-benchmark-events-plan local-benchmark-events-import`。
詳細: repository `tools/local_experiment/benchmark_events/README.md`。
この増分は保存履歴の取込であり、公式TPC性能値、費用測定、新規Grafana benchmark panelではない。
次はQuery Doctorのcaptured own-query履歴を同様に検証して持続化する。
