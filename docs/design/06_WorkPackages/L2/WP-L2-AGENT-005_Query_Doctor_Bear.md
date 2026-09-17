---
project: Shirokuma
doc_id: "WP-L2-AGENT-005"
title: "WP-L2-AGENT-005 Query Doctor Bear"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.4"
area: "workpackage"
tags: [shirokuma, workpackage, l2, agentops]
---

# WP-L2-AGENT-005 Query Doctor Bear

## Summary

EXPLAINとquery historyから改善案を出す

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
