---
project: Shirokuma
doc_id: "WP-L2-AGENT-001"
title: "WP-L2-AGENT-001 MCP Gateway MVP"
status: local-verified
created: 2026-07-05
updated: 2026-09-17
version: "0.3"
area: "workpackage"
tags: [shirokuma, workpackage, l2, agentops]
---

# WP-L2-AGENT-001 MCP Gateway MVP

## Summary

GitHub/K8s/dbt/OpenMetadata/Trino/ClickHouse toolsを束ねる

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
You are working on Shirokuma WP WP-L2-AGENT-001: MCP Gateway MVP.
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

stdio MCPの8固定読み取りtoolを実装。実機11呼び出し（8成功・3拒否）でClickHouse/Loki/OTel相関を検証し、12 focused testsが成功した。GitHub toolはinactive。

[ADR-0034](../../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)によるlocal functional検証。旧GitHub/PR/CI gateは公開経路の履歴として保持する。実行手順・証跡・制限は[Local Experiment Milestones](../061_Local_Experiment_Milestones.md)を参照。

## 2026-09-17 fixed runtime readiness tool

引数なし`local_platform_status`を追加し、現在のallowlistは9固定読み取りtool。
専用context内のlakehouse/Superset/metadata/AgentHouse/Lokiの5群だけを読み、
固定component名、ready/status整合、全体集計、RFC3339取得時刻を検証する。
free-text reasonや元reportの任意payloadはMCP出力に含めない。
`not-ready`と`unknown`は成功した観測として監査し、正常と区別する。

実MCPの12呼び出し（9成功・3拒否）でClickHouse/Loki/OTelの12件相関を確認。
2026-09-17 03:01:49 UTCには5群すべてready。証跡は
`tmp/local-experiment/gateway/smoke-341823915814494a925ba94976e070cd.json`。
SREはこのtoolを4つ目の初期観測として利用する。個々のSQL/dashboard/lineage機能や
継続稼働・復旧はこのreadinessだけで断定しない。

独立reviewでscope確認がstatus timeoutの外にあった点を修正し、両方を75秒の
専用worker deadline内へ統合した。Timeoutは新規worker process groupのみ停止し、
監査配送の時間を確保する。実親子process回収を含む31 gateway testsが成功。
修正後の実MCP12件相関・5群readyも再確認した。最終証跡は
`tmp/local-experiment/gateway/smoke-1aff3c763c9c41e490cc59a16d51505a.json`。
