---
project: Shirokuma
doc_id: "WP-L2-AGENT-003"
title: "WP-L2-AGENT-003 Data Engineer Bear"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.5"
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

## 2026-09-17: bounded natural-language entry

`--request` / `--request-stdin` から利用者の依頼文を受け取れるようにした。
従来の `--case` とdefaultは互換維持し、入力方式は排他。UTF-8の1行・512文字上限。
sourceは引き続きfixture_v1、許可operationはuppercase_names/name_lengths/row_countのみ。
Qwenを1回呼び、hostの固定templateだけを実行する。raw要求文/応答を通常出力・receipt・監査へ
保存せず、要求hash/長さ・採択plan・実usageを記録する。対応外はDataOpsを起動せずexit2で返す。
採択IRとの結果一致を実証し、任意の自然文の意図を常に正解するとは主張しない。

新7golden×2の実Qwen評価は14/14成功（`d87069723481431f96e87cc0d9d17b2c`）。
日本語文字数/英語大文字化/日本語件数の言い換えと、曖昧・全行削除・未知列・出力契約overrideの
4拒否を検証した。旧6golden×2も12/12成功（`fb2781b2c73a442facafd74a8ac913a6`）。
新評価は入力6274/出力294 tokens・17.099秒、旧評価再確認は5078/272・14.838秒。
26推論すべてAgentHouseの実eventとusageを読戻し、cloud/retryは0・costはNULL。
評価traceは相関IDでありexported spanではない。自然文実行traceと混同しない。

日本語の文字数依頼を実stdinから渡した `fc906b457f834162acd80a35204a7c1c` が成功。
1推論440/23 tokens・2.753秒、採択name_lengthsを2回materializeし、結果 `[[1,9],[2,7]]` が一致。
DataOps run `396f97befadd41b3af49bf4db9be4b1c` のdbt tests/readback/drain/owned2relation回収が成功。
MCP/model/materialize/assessmentの4eventをAH/Loki/実OTelへ相関させた。
trace `ee24f3ab7ca20b8a1dbdc38bc68370e5`。

削除依頼の初回 `3f4315b3e32e410fbd9f14e25c1a4486` はモデルが正しくunsupportedを返し、
DataOps起動0だったが、共通監査helperがsuccessだけを認める不備でfinal failedになった。
期待statusをsuccess/blocked/failedの範囲でAHとLokiの両方へ厳密一致させる修正と、
実run/main/helperを通す拒否・status改ざん回帰を追加した。元failed証拠は保持。
再実行 `cffc3740880c437aa883ee7d887b94c1` はunsupported/exit2・materialization0・3event相関まで成功。
414/22 tokens・1.267秒、trace `027f56a5c2c2881d4153cdba44889327`。

再現: `make local-data-bear-request` にstdinを渡し、`make local-data-bear-request-eval`で追加評価。
新規service/image、MTPLX設定・自動起動の変更、保持データへの書込みはない。
一般SQL/code generation、任意データセット、一般的な意図解釈品質・本番受入は引き続き未達。

独立最終監査 `data-request-audit/34a457215c244a589c790d06ab8a16e4`（08:32:49 UTC）が成功。
保持Kube6Pods/27resources/6CM/3Secret、3Docker、履歴4表/5DDL、fixtureのschema/rows/
全snapshots/history/refs/headとIceberg全table一覧、旧330eventsが不変。
全5試行の36追加event（success32/blocked4）を帰属させ、AH366件、workflowのLoki/実spanも一致。
正常runのowned2tablesとactive query残留0、拒否2試行はadapter生成なし。
失敗試行を含む29推論の実測usageは12620入力/633出力tokens、cost NULL、cloud/retry0。
既存62証跡と今回163filesが監査中不変で、workflow証跡にraw要求全文の保持は検出されなかった。
Data91＋SRE64 focusedが成功、全local回帰867件（866成功＋標準環境optional OTel1skip）も成功。
同OTel結合は専用venvで成功している。

追加評価を既存集計へ統合し、report `7252a562650c4c1292c6df462a832bc3` は10runs valid/complete。
baselineとrequestsのcase signatureを分離し、固定dataset hash/IR/意味判定を確認する22focused testsが成功。
