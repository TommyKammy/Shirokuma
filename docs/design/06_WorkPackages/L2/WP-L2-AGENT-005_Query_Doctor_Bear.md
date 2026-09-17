---
project: Shirokuma
doc_id: "WP-L2-AGENT-005"
title: "WP-L2-AGENT-005 Query Doctor Bear"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.5"
area: "workpackage"
tags: [shirokuma, workpackage, l2, agentops]
---

# WP-L2-AGENT-005 Query Doctor Bear

## Summary

EXPLAINとquery historyから改善案を出す

## 2026-09-17: product CLI plan

既存の固定2SELECT診断を`shirokuma query run --case retained-fixture`へ接続する。
`query status TOKEN`は保存済み証跡だけを読む。新たな常駐serviceや汎用SQL入口は追加しない。

- [x] Goから固定Python bridgeを起動し、Data CLIの既存取消transportを変更せず再利用する。
- [x] early allocation、durable intent、workflow呼出し、terminal resultを区別する。
- [x] 証跡directory tokenとTrino/query用の内部tokenを分離する。
- [x] 既存legacy証跡も照会でき、初期failed receiptをterminal failureと誤認しない。
- [x] 保存statusがmodel/API/writeを行わず、欠損・改変・取消・protocol不整合を扱うことを確認する。
- [x] 製品CLIからQwenを1回呼び出し、固定観測・監査・cleanupと保持データ不変を実証する。
- [x] 全local回帰・独立review・関連Obsidianノートを更新しlocal commitする。

status成功は保存状態の読取成功であり、診断成功やfresh runtime確認と同義ではない。
原SQL・plan・prompt・応答・例外本文をCLI出力へ転記しない。
取消時はownerだけにSIGINTを送りcleanupを待ち、終了不明ならtoken/PIDとunknownを保持する。
自動再実行/resume/SIGKILLは追加しない。root causeと改善効果の未確定は継続する。

## Context

このWork PackageはLevel L2「Agent第一陣」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

## Scope

- 設計・実装・設定の最小単位を定義する。
- 関連するREADME/Runbook/ADRを更新する。
- CIで検証できる形にする。

## Non-scope

- Level外の大規模OSS追加。
- 本番データ利用。
- GxP対応。
- 人間承認が必要な危険操作の自動化。

## Deliverables

- 実装または設定ファイル
- ドキュメント更新
- テストまたは検証ログ
- 必要に応じてADR/Runbook

## Acceptance Criteria

- [ ] 変更がGitHub PRとして提出される。
- [ ] CIが通る。
- [ ] Policy checkが通る。
- [ ] `shirokuma doctor`または関連コマンドで状態確認できる。
- [ ] README/Obsidian noteが更新される。
- [ ] 未解決High riskがない。

## Suggested Labels

`level:l2`, `area:agentops`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L2-AGENT-005: Query Doctor Bear.
Read AGENTS.md, related ADRs, and this Work Package.
Use ADR-0034 local commits and bounded read-only Trino diagnostics.
Preserve unknown estimates and do not claim measured performance improvement.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-17 local bounded implementation plan

保持fixtureの2固定SELECTを各1回実行し、独立した期待id集合と照合する。
同じSQLのJSON EXPLAINと、その実行で捕捉したquery ID・専用userのhistoryだけを観測する。
Qwenは追加観測として固定SHOW STATSまたは自身のquery historyだけを選ぶ。
任意SQL、ANALYZE、SET、DML、他userの履歴、設定変更、索引・データ配置変更は実行しない。

事前確認ではSELECT idがOutput→TableScan、WHERE id=1がOutput→ScanFilterとなり、
filtered planのroot estimateには文字列NaNが含まれた。複数estimatesを合算せず不明を保持する。
system.runtime.queriesにはCPU/processedRows/bytes/elapsed列が無い。
EXPLAIN自身の時間を対象SELECTの実行時間へ置換せず、異なる結果意味のquery間で速さを比較しない。
2行fixtureで性能改善や遅さ、pushdown失敗の原因を断定しない。

- [x] 2固定SELECTと同一SQLのplan、own query ID/userのhistoryを照合し、原文をモデルへ渡さない。
- [x] NaN、未知node/schema、複数estimate、欠測履歴、他query混入を明示的に扱う。
- [x] モデルは固定の追加readだけを選び、根拠IDと必要観測をgolden casesで照合する。
- [x] 実Qwen→追加read→結果照合→監査まで実環境で確認する。
- [x] 元fixtureが不変で、自身のqueryだけを終了・回収したことを確認する。
- [x] 実行証跡・制約・未解決の改善課題をRunbookとVaultへ記録する。

本段階のassessmentは限定観測または追加根拠必要、root causeはundetermined、
performance gainはunmeasuredとする。具体的な高速化変更・性能保証は別の実測課題として残す。
追加resident serviceやCloud APIは不要。停止は新entrypointの利用停止で、保持データを変更しない。

## 2026-09-17 local functional evidence

8 golden case×2の実Qwen評価は16/16、16監査readbackが成功した。
入力8022/出力586 tokens、推論合計27.881秒。
`tmp/local-experiment/query-evaluation/90af6c7220a241a4b6521be414f36439/`。

実workflowはQwenがfiltered planの推定欠測を根拠に`fixture_stats`を選択した。
入力491/出力36 tokens、推論1.696秒。固定2SELECTの結果は2行/1行と独立期待値に一致。
SHOW STATSでrow count estimate=2を取得したが、列統計6項目のunknownは残した。
root cause=undetermined、performance gain=unmeasuredを維持し、改善成功とは扱わない。

ClickHouse/Loki/保存OTelの4event相関、完了checkpoint、captured4 queryのhistory全terminal、
全6requestのresponse完了・cleanup verifiedを確認。
証跡は`tmp/local-experiment/query-bear/6aae039137f748548db53b59f8e2d3b7/`。
04:20 UTCの独立検証で保存観測からmodel evidence/assessmentを再計算し一致。
run user `shirokuma-query-doctor-7a73e325b46c4e75bd77e8b824c8fec1`の非terminal query残留0、
元fixture2行不変を確認した。`independent-verification.json`に保持する。

Query37 tests（observer18、workflow/provider19）に加え、評価harness14 testsを含む
全回帰455 tests・設計73 documentsが成功した。推論途中cancelでも失敗Pawprintとunknown usageを残し、
追加readへ進まない。同期HTTPとfinally receiptによりtunnel外へquery workerを残さない。
request timeoutや中断時に未確認終了を成功と扱わず、serverの30秒run上限と
captured/uncaptured request状態を記録する。API DELETEや広いquery killは行わない。

再実行は`make local-query-bear-eval local-query-bear-smoke`。
[Runbook](../../../../tools/local_experiment/bears/QUERY_DOCTOR.md)に測定境界と再実行を記録。
一般的な遅延原因の特定、query rewrite、実性能改善、本番query品質は未達として保持する。

## 2026-09-17: Query Doctor product CLI

`shirokuma query run --case retained-fixture`と`query status TOKEN`を追加した。
既存Qwen・固定2SELECT/同SQLのEXPLAIN/自身のhistory・最大2種のfollowupを使う。
GoはDataの既存owner取消transportを変更せず再利用し、厳密なJSONLとchild exitを照合する。
early allocationは永続intentや実行開始とは別で、出力はallowlistされた固定summaryのみ。
取消はownerへのSIGINTと最大240秒のcleanup待機、未確定はtoken/PID付きunknownとする。
SIGKILL・自動replay・resume・任意SQL入口は追加していない。

Pythonは`query-cli/<token>`へ一度だけのinvoke intent/lock/hash seal、
`query-bear/<token>`へ既存workflowの証跡を分けた。directoryのevidence_tokenと
内部query/user tokenは別のまま保持する。statusはrunner/modelをimportせず保存内容を読む。
旧成功証跡も検証できるが、新hash sealやfresh runtime観測へ読み替えない。
initial failed receiptはclosed root span・observer・保存内容が揃うまでterminal failureにしない。
graph auditやobserver保存の二次失敗が元の取消を置換する経路も修正し、実body fakeで回帰した。

実CLI成功は `query-bear/20e5c3b825be6c74e58b0ce8ab88a361/`、
内部tokenは `9de355caddb74fd299fd7b7711acdb3c`、traceは `0bd4e45890d24dc76376cade8399ab7b`。
Qwenを1回呼び、入力491/出力36tokens、2682ms、cost NULL・cloud0・retry0。
不足estimateのf01に対してfixture_statsだけを選択した。row estimate2、column stats未知6件を保持し、
root_cause=undetermined、performance_gain=unmeasuredとした。6requestsはすべてFINISHEDで
response_complete、cleanup_verified=true。AgentHouse/Lokiの4監査eventと保存OTel4spanが相関した。

初回 `b0ca236012e18f8c4a1728a5022e22f4` はモデル前observeで失敗し、失敗のまま保全した。
固定5queryは完了済みで、observe/assessの失敗監査2件・model0。CLIはexit1、
保存statusはexit0で診断failedを維持し、状態読取成功と診断成功を混同しなかった。
元4query IDと専用userに限定したhistory1回で、実started_to_end=1011msに対し
検証側のfloat式が1010msへ丸める原因を確定した。SELECT/EXPLAINを再送せず、
新規固定観測5readsと限定history1readだけで調査し、どちらもmodel0・tunnel終了。
query_contractのdurationをobserverと同じ整数演算へ修正し、1001/1003/1011ms、subms端数、
日跨ぎ・改ざん拒否を回帰した。元保存historyの全8durationも修正版で一致した。
診断は `query-cli-diagnostics/e50551a64ae94f33b7afa906e8bb59fb/`へ保存した。

旧成功・今回失敗・今回成功を各3回、計9回status照会した。対象27files/directoriesの
hash/mode/mtimeは不変。model/API/write0は隔離statusテストと固定codepathで確認した。
新Python29tests、Query Go16testsを追加し、全Go114件とvet/raceが成功。
全local回帰47suites/1554件（1553成功・既存optional OTel1skip）が成功した。
実行・build/source hash・status比較・全logは
`tmp/local-experiment/query-cli-checks/c88902c4e4b844c3bc2e7d38845fc0c2/`、独立監査は
`query-cli-preflight/831f61fbef894f3b8854e4aadd038798/`へ保存した。

2行fixtureの観測と製品入口までのlocal-partialであり、一般SQLの根本原因・性能改善・
本番受入を主張しない。次は既存4観測源のSRE Bearを製品CLI run/statusへ接続する。

独立監査 `query-cli-preflight/831f61fbef894f3b8854e4aadd038798/independent-verification.json`
は78checks成功。AH440→446の増分は失敗2＋成功4だけで、旧440件は不変。
Loki2＋4、保存span2＋4、実Qwen1回、固定6requests、整数duration8値、未知stats6を照合した。
保持旧3288files、Kube14Pods/27resources/6CM/3SecretUID、Docker3/images553、
history/fixture全履歴、Metadata35endpoints/7plans14tables14edges、MLflow59tables/
15runs1210points15artifactsとbackupが不変だった。初回seal10filesと診断原証跡も保持した。
監査tunnel終了、design83docs/14tests・secret scan・diff check成功。
Query WP/MilestonesはVaultと一致し、MOD-051の既存言語差は維持して追記だけを揃えた。
