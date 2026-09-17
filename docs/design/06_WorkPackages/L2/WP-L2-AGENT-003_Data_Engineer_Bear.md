---
project: Shirokuma
doc_id: "WP-L2-AGENT-003"
title: "WP-L2-AGENT-003 Data Engineer Bear"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.4"
area: "workpackage"
tags: [shirokuma, workpackage, l2, agentops]
---

# WP-L2-AGENT-003 Data Engineer Bear

## Summary

dbt/Dagster/Icebergの変更案を作成・検証する。ADR-0034のlocal実験では、
自然言語要求から型付き変換計画を生成し、専用の使い捨てdbt modelを実行・検証してlocal commitへ記録する。

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
You are working on Shirokuma WP WP-L2-AGENT-003: Data Engineer Bear.
Read AGENTS.md, related ADRs, and this Work Package.
Use ADR-0034 local execution and commits without Issues, PRs, or GitHub review.
Keep model output inside the typed plan contract and record functional evidence.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-17 local bounded implementation plan

依存するMCP Gateway、Qwen実LLM、Dagster/dbt/Icebergのlocal機能を再利用する。
追加resident service、image、cloud費用は不要。sourceは保持fixture `iceberg.smoke.fixture_v1`の
`id`/`name`に固定し、モデルはsource/operation/schema_versionだけの型付き計画を返す。
変換はuppercase_names、name_lengths、row_countの3種類、非対応要求はunsupportedとして拒否する。
SQL、shell、保存先、catalog/schema、任意列やaliasをモデル出力から実行しない。
Hostが固定templateからdbt modelを生成し、Dagster→dbt build/test→Trino読戻しを行う。

- [x] 日本語を含むgolden要求をexpected operationへ照合し、JSON適合だけで成功にしない。
- [x] Python独立期待値で変換結果を検証する。uppercaseはASCII名に限定し、文字数はUnicode codepoint単位。
- [x] 実LLM計画を使って実Iceberg martを生成・再実行し、結果とdbt testsを検証する。
- [x] 保持fixtureの前後一致を確認し、UUID/owner marker付き生成物だけをquery drain後にcleanupする。
- [x] モデルusage/latencyとMCP・実行結果のPawprintを保存・照合する。
- [x] 生成model/manifest/run_resultsと安全な計画・実行receiptを保持する。

任意SQL/code generation、一般的なデータモデリング、cloud、既存業務データの変更、
本番受入や上記historical GitHub gateの完了はこの小さな機能実験に含めない。

## 2026-09-17: local functional evidence

Qwen `qwen3.8-flash-next`の日本語5/英語1 golden要求×2回は12/12成功。
正しいoperationと非対応要求の拒否を照合し、12件の推論監査を読戻した。
入力4754/出力262 tokens、推論合計15.723秒。
`tmp/local-experiment/data-evaluation/3612f021adf4416894ab52d6e09ed165/`に保持する。

LangGraph observe→plan→execute→verifyを実LLM・MCP・Dagster/dbt・Trinoで接続した。
各caseで2回のmaterialization、全dbt build/test、独立Python期待値、完了checkpoint、
ClickHouse/Loki/OTelの4監査event相関が成功した。

| case | 実結果 | input/output tokens | 推論秒 | Data Bear証跡UUID |
|---|---|---|---|---|
| name_lengths | `[[1,9],[2,7]]` | 478/23 | 1.335 | a95175c8abc74dd897699a8e38f3acc6 |
| uppercase_names | `[[1,SHIROKUMA],[2,POLARIS]]` | 430/23 | 1.328 | ddf4422d47294b8d912172d284024f49 |
| row_count | `[[2]]` | 400/23 | 1.310 | e364c200e2c648e3b1ec79923c6acbff |

証跡の基点は`tmp/local-experiment/data-bear/`。各receiptは対応する
`tmp/local-experiment/data-plans/<token>/`の生成project・hash・manifest・run_resultsを参照する。
別途name_lengthsの意図的dbt test失敗→retry成功も確認した。
`data-plans/eb8902c0a4e54c8591dcbfee02550927/`は4attempt中3成功、期待した1testだけが失敗。

03:43 UTCの独立読戻しで、4runのproject6hashesとfirst/repeatのdbt成功・lineageを再照合。
元fixtureの2列2行は不変、生成8relationと当該run実行中queryは残留0。
5 runtime groups ready・native admission ready。
`tmp/local-experiment/data-bear-preflight/b09b846899ac41d0847f8c6eb612fb81/`に保存した。
新規45 testsを含むlocal340＋design14＝354 tests、設計69 documentsの検証が成功。

再実行は`make local-data-bear-eval local-data-bear-smoke`。
詳細は[Data Engineer runbook](../../../../tools/local_experiment/bears/DATA_ENGINEER.md)。
失敗・中断時も自身のchild processと専用relationだけを回収し、保存receiptでcleanup成否を確認する。
実験を使わない場合はentrypointを実行しない。新しいresident componentや保持データの変更はない。
一般的なデータ設計や任意コード生成の品質を示す結果ではなく、3変換に限定した機能証拠である。
