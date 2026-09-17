---
project: Shirokuma
doc_id: "WP-L2-EVAL-001"
title: "WP-L2-EVAL-001 Agent evaluation harness"
status: active
created: 2026-07-05
updated: 2026-09-17
version: "0.3"
area: "workpackage"
tags: [shirokuma, workpackage, l2, evaluation]
---

# WP-L2-EVAL-001 Agent evaluation harness

## Summary

Agentの評価結果を同じ定義で比較する。ADR-0034のlocal実験では、
functional success、監査coverage、latency、tokensと欠損を集計し、PR指標は対象外とする。

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

`level:l2`, `area:evaluation`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L2-EVAL-001: Agent evaluation harness.
Read AGENTS.md, related ADRs, and this Work Package.
Use ADR-0034 local commits and read-only evaluation evidence aggregation.
Keep failed, incomplete and unknown observations visible.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-17 local bounded implementation plan

Inventory/SRE/Data/Catalogの4評価suiteが生成した全UUID receiptを読み取り専用で集計する。
成功runや最新runだけを選択しない。mock/local、provider/model、case signatureを分離し、
異なる評価範囲の成功数やlatencyを同じ品質指標として混ぜない。
JSONのsummaryとattempts.jsonlを照合し、不正・未完了・重複・欠損を明示する。

- [ ] 4固定directory配下の全receiptを対象とし、失敗や不正receiptを黙って除かない。
- [ ] Functional success/audit coverage/latency/tokenの分母と欠損coverageを明示する。
- [ ] mockと実LLM、異なるcase setを別groupにする。
- [ ] 初回成功率、開始日時、同一code/fixture版は根拠がない場合unknownとし、mtimeから推測しない。
- [ ] 原文prompt、自由文error、SQL、認証情報をreportへ転記しない。
- [ ] 既存成果物を変更せず、private reportと関連Vault/Runbookへ検証を記録する。

このharnessは保存済みreceiptの整合を検証する。実LLM再呼出、評価品質の一般化、
AgentHouseへの新規書込、production受入、PR成功率/修正率/MTTP測定は含まない。
過去receiptにcode/fixture hashがなければ検証済み版同一性は主張しない。
再評価を行う場合は各suiteの既存entrypointを明示実行し、新しいreceiptを追加する。
