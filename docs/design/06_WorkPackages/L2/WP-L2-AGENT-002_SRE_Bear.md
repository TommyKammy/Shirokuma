---
project: Shirokuma
doc_id: "WP-L2-AGENT-002"
title: "WP-L2-AGENT-002 SRE Bear"
status: local-partial
created: 2026-07-05
updated: 2026-09-16
version: "0.2"
area: "workpackage"
tags: [shirokuma, workpackage, l2, agentops]
---

# WP-L2-AGENT-002 SRE Bear

## Summary

K8s events/logs/metricsを診断してPR提案する

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
You are working on Shirokuma WP WP-L2-AGENT-002: SRE Bear.
Read AGENTS.md, related ADRs, and this Work Package.
Create a focused PR that implements only this scope.
Include tests, docs, risk notes, and rollback instructions.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-16 local functional evidence

MCP doctor/Pod/Eventを使う固定ルールの読み取り診断を実装。実機で3 tool callsと診断自身の4 eventがClickHouse/Lokiで相関一致した。意図的Flux停止と過去restartを区別し、現在の停止とは断定しない。11 focused tests成功。LLM診断・自動修復・PR提案は含まない。

[ADR-0034](../../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)によるローカル実験。旧公開gateは履歴として保持する。実行手順・失敗と修正・復元・未達事項は[Local Experiment Milestones](../061_Local_Experiment_Milestones.md)を参照。
