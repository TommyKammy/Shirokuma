---
project: Shirokuma
doc_id: "WP-L2-EVAL-001"
title: "WP-L2-EVAL-001 Agent evaluation harness"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.6"
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

Inventory/SRE/Data/Catalog/Query Doctor/Policy Gatekeeperの6評価suiteが生成した全UUID receiptを読み取り専用で集計する。
成功runや最新runだけを選択しない。mock/local、provider/model、case signatureを分離し、
異なる評価範囲の成功数やlatencyを同じ品質指標として混ぜない。
JSONのsummaryとattempts.jsonlを照合し、不正・未完了・重複・欠損を明示する。

- [x] 6固定directory配下の全receiptを対象とし、失敗や不正receiptを黙って除かない。
- [x] Functional success/audit coverage/latency/tokenの分母と欠損coverageを明示する。
- [x] mockと実LLM、異なるcase setを別groupにする。
- [x] 初回成功率、開始日時、同一code/fixture版は根拠がない場合unknownとし、mtimeから推測しない。
- [x] 原文prompt、自由文error、SQL、認証情報をreportへ転記しない。
- [x] 既存成果物を変更せず、private reportと関連Vault/Runbookへ検証を記録する。

このharnessは保存済みreceiptの整合を検証する。実LLM再呼出、評価品質の一般化、
AgentHouseへの新規書込、production受入、PR成功率/修正率/MTTP測定は含まない。
過去receiptにcode/fixture hashがなければ検証済み版同一性は主張しない。
再評価を行う場合は各suiteの既存entrypointを明示実行し、新しいreceiptを追加する。

## 2026-09-17 local functional evidence

標準libraryだけのread-only reporterと14 focused testsが成功。
不正JSON、欠損receipt、途中failure、誤ったaudit/usage集計、duplicate identity、秘密情報非転記、
first-repeatとchronological first attemptの区別を検証した。
Query Doctor開始前のreportは未観測suiteを正しく表示してexit1となり、既存結果を隠さなかった。

5suiteが揃った04:14 UTCの実集計は7全receipt/7valid、invalid/incomplete/duplicate/failed各0。
mock 10callsは別group、実Qwenは6receiptに78成功/78監査が保存されている。
SRE旧6caseと現8caseも別group、計7groups。これらを一般的なモデル品質へ合算しない。
各groupのcase数/signature、observed/requested成功率、監査coverage、latency p50/p95、
token total/known subtotal/known callsをJSONとMarkdownへ表示する。

最終reportは`tmp/local-experiment/evaluation-reports/14f84889fc1f4fb99c2e4309113989d8/`。
Root独立検証で入力14filesのSHA256不変を確認し、`independent-verification.json`へ保存した。
古い4suite complete reportとQuery Doctor未観測reportも保持した。
実LLM/API再呼出やruntime変更をせずに集計できる。

再実行は`make local-evaluation-report`。
[Runbook](../../../../tools/local_experiment/evaluation/README.md)に終了codeと各指標の意味を記録。
PR関連指標/MTTP/実runtime成功率はN/A、既存receiptの開始日時・code/fixture版・
chronological初回成功率・修正率・費用はunknownのまま保持する。

Query Doctor統合後、全回帰455 testsと設計73 documentsの検証も成功した。

## 2026-09-17 Policy Gatekeeper suite追加

固定8case×2のPolicy評価を第6suiteへ追加し、focused15 testsが成功。
Policy未観測のreportはexit1となり、後続の成功が過去の失敗を隠さない分母もテストした。
全6suiteの最終集計は8receipt/8groups valid、invalid/incomplete/duplicate/failed/unobserved各0。
Qwenは7receiptに94/94成功・監査、入力46974/出力6660tokensは全94calls既知。
Mock10callsは別groupでtokens欠測を保持する。異なるsuiteの一般品質比較には使用しない。

`tmp/local-experiment/evaluation-reports/076b0abbce144d179e7c223c0126058d/`へ保存。
独立検証は入力16filesと従来report9filesのSHA256前後不変、case/provider別分母、
latency quantiles、saved audit metrics、unique IDsを再計算して一致。
既存5suite complete reportも保持。全回帰486 testsと設計73documentsが成功。

## 2026-09-17: Data natural-language evaluation coverage

Data Bear追加requests suiteの7case×2とbaseline再確認6case×2を評価reportへ統合した。
固定case IDs/順序/回数/expected operation/strict IRと保存監査を検証し、baseline/requestsを
別case signature・表示名で集計する。新receiptは固定dataset SHA256へ束縛する。
古いsuite/hash未記録receiptはbaseline互換のまま、hashはlegacy-unrecordedとして扱う。
保存producerのdataset宣言とtyped verdict/監査整合の証拠であり、当時のHTTP prompt自体を証明しない。

report `7252a562650c4c1292c6df462a832bc3` がcomplete、10/10retained evaluation runsがvalid。
invalid/incomplete/duplicate/unobserved suites/evaluation failuresは0。
Qwen固定評価は合計120/120、実測58326入力/7226出力tokens。mock10件は別群、usage NULLを維持。
新Data requests14件とbaseline24件（旧12＋今回12）は別集計。旧SRE6case/新8caseも混在させない。
このreportは評価suiteだけを集計し、別workflow実行で発生した失敗を削除・成功化するものではない。
追加report focused22件が成功。実機生成にはmodel/API呼出や外部書込みを追加していない。
