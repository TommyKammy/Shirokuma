---
project: Shirokuma
doc_id: "WP-L3-CH-001"
title: "ClickHouse AgentHouse advanced dashboards"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.8"
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

## 2026-09-17: retained Query Doctor history

保存済みQuery Doctorの終端証跡を検証し、`query_history_runs`へ1行、
`query_history_queries`へcaptured IDの6行を登録した。固定SELECT/EXPLAINの4件は
保存historyの実測値、own-history/STATSの2件は補助読取で`not_target`・測定NULLを維持する。
sourceはobserverが保存した射影であり、再取得したserver監査やGateway exportではない。
証跡directory UUIDとobserver UUIDを区別し、query ID/user/history参照の結び付きを検証する。

producerは稼働中も初期値`result=failed`を書き出すため、結果文字列だけでは取り込まない。
成功は完了checkpoint・保存span検証・一致する終了root spanを要求し、失敗も固定終端errorと
終了spanを要求する。未完了/終端未証明sourceはinventoryへ残し、apply全体を止める。
固定runtimeを各mutation前に再確認し、source hash・正確な所有schema・既存全行を照合する。

初回`query-history/b26cf6f6c753459abfd49f581812391b`で1/6行追加、
再実行`bbe00a0c30c2485fa94d02ca3a94f07f`は0/0。19 focused tests、全回帰592 tests、
設計76documentsが成功。新規Trino query/LLM推論、Grafana reader grant変更は行っていない。
独立再照合で元8filesと既存3tables（監査294・benchmark run3・query side72件）の全hash/DDL不変。
独立保全証跡は`query-history-preflight/509e684aee2b4c28a99cea9cffbdead8`。
再実行: `rtk make local-query-history-plan local-query-history-import`。
詳細: repository `tools/local_experiment/query_history/README.md`。

本WPはlocal-partial。履歴の新規Grafana画面、全clusterの継続history収集、Gateway連携、
実費計測は未達。次はWP-L3-SEC-001の隔離Trino OPA行制限/列マスクを検証する。

## 2026-09-17: retained history dashboard

[クエリ実験の履歴](http://127.0.0.1:18030/d/shirokuma-query-history-local)に保存履歴の5表を追加。
benchmark全3run（失敗2を含む）と72query sides、Query Doctorの1run・実測4件・補助2件を表示する。
全期間の固定SELECT/FINAL/行数制限を使い、timestamp不明をtime filterで隠さない。
列filterは取得行だけが対象。失敗・来歴不明・未観測・測定0を区別し、run間で性能平均しない。
補助読取の履歴時間はNULLのまま「未観測」。query時間はミリ秒精度の表示を維持する。

明示的なhistory-enableで、既存専用readerに上記4history tablesのSELECTだけを追加した。
既存1tableまたは固定5tablesの正確なgrant集合だけを認め、制限・書込拒否・credentialsは維持。
既知XMLの完全照合→private backup/pending→hash再照合→atomic replace→reload/読戻しを実装。
同CH/Grafana containerを維持し、サービス再起動は不要。再enableはchanged=false、up/smokeもready。
復元は`manage.py history-disable`でreaderを元のPawprintsだけに戻し、データは削除しない。

5panelのanonymous APIと専用reader前後SELECTの型/値/NULLが全件一致。
API証跡`grafana-history-verification/e4f54e9e4d2740c5809b2afc5150c025`。
実UIで全3run→失敗2run→全3run、実測4件、補助2件の対象外/NULL表示を確認。
本文の見切れを解消し、元画面と履歴画面の相互linkを追加した。SQL入力filterは追加していない。

保全54項目に合格し、既存5tablesの全hash/DDL（294/3/72/1/6件）、元145files、credentials、
両containerのID/image/設定/volume/resource/port/network/start/restartが不変。
Docker mount配列の順序差だけは、完全な各属性集合の一致で判定した。
証跡`grafana-history-preflight/8e1933d437a44e36ae11923a3f0f17f2`。
focused17 tests追加、全回帰609 testsと設計76documentsが成功。

再実行: `rtk make local-grafana-history-enable local-grafana-verify-history`。
本WPはlocal-partial。継続history収集/Gateway連携/実費測定は未達のまま。

## 2026-09-17: live authenticated policy observations

SEC実験の新run`b94184c624174f0c8f01fc00cae8d7d7`から16Pawprintsを追記し、
同16Loki/OTelとreadback一致を確認。7success/9blocked、検証済みOPA拒否3件だけpolicy flag=true。
認証拒否6件はfalseで、期待どおり拒否された実験結果と要求自体のblockedを区別する。
実測durationはcaseのquery＋監査照合を含み、元policy判断の性能値ではない。usage/costはNULL。
MCP agenthouse_recentが同Task全16件を読出し、そのMCP監査1件も保存した。
Grafanaの既存Task/Trace画面で拒否9件を確認。schema/table/dashboard/reader権限の変更はない。
独立after照合でPawprintsは311件＝保持294件完全不変＋新identity16＋MCP1。残4表の全行hash/DDL、
Pawprint DDL、AH/Loki/GrafanaのID/Config/HostConfig/起動時刻/restarts/mount/ports/networksも一致。
保護31filesのhash/mtime/modeを保持。実結果行は非保存のため再照合はcount/matched射影までとする。

全回帰691成功/標準環境OTel1skip（専用venvでは実行成功）、設計76documents。
詳細は[[WP-L3-SEC-001_Keycloak_OPA_native_RBAC_integration]]のlive telemetry節と、
private証跡`trino-identity-telemetry-preflight/c96c6414e88c4531a7f21b7f2c760a3c`を参照。
