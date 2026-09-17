---
project: Shirokuma
doc_id: "WP-L2-AGENT-005"
title: "WP-L2-AGENT-005 Query Doctor Bear"
status: active
created: 2026-07-05
updated: 2026-09-17
version: "0.3"
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

- [ ] 2固定SELECTと同一SQLのplan、own query ID/userのhistoryを照合し、原文をモデルへ渡さない。
- [ ] NaN、未知node/schema、複数estimate、欠測履歴、他query混入を明示的に扱う。
- [ ] モデルは固定の追加readだけを選び、根拠IDと必要観測をgolden casesで照合する。
- [ ] 実Qwen→追加read→結果照合→監査まで実環境で確認する。
- [ ] 元fixtureが不変で、自身のqueryだけを終了・回収したことを確認する。
- [ ] 実行証跡・制約・未解決の改善課題をRunbookとVaultへ記録する。

本段階のassessmentは限定観測または追加根拠必要、root causeはundetermined、
performance gainはunmeasuredとする。具体的な高速化変更・性能保証は別の実測課題として残す。
追加resident serviceやCloud APIは不要。停止は新entrypointの利用停止で、保持データを変更しない。
