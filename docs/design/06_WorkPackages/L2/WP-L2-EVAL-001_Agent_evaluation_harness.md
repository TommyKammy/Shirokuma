---
project: Shirokuma
doc_id: "WP-L2-EVAL-001"
title: "WP-L2-EVAL-001 Agent evaluation harness"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.8"
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

## 2026-09-17: SRE container observation coverage

SREの既存8caseにcontainerの現在状態・過去終了履歴・詳細unknown/truncatedの8caseを追加した。
実Qwen評価`c0de842e01824262bdc19298cfb9f0ac`は16case×2＝32/32成功、全32推論の監査読戻しも成功。
入力26368/出力2070tokens、推論91289ms、cost NULL。合成snapshotの読み取り選択評価であり、
一般的な原因診断や既存サービスの故障・復旧成功率としては扱わない。

report `a1f4249181e64c6c8943b13b2ed6a0f8` はcomplete、保持11評価runsすべてvalid。
母集団はQwen152/152・mock10/10（別群）、全体162/162。
実Qwen固定評価は旧SRE6case・8case・今回16caseを別signature/groupのまま保持する。
mockをモデル品質・usageの同じ群へ混ぜず、最新成功だけへの置換や過去run削除はしない。
別途実行したFailed Pod drillのモデル1callはgolden152件へ加算しない。
report focused24件（互換性・分母の追加2件を含む）が成功。
全回帰は新Gateway14件の配線も確認した正式1012件（1011成功・optional OTel 1skip）。
旧receiptの未記録fixture hashはunknownのまま、PR指標・実runtime成功率・本番受入はN/Aを維持する。

SRE増分の独立最終監査`tmp/local-experiment/sre-container-preflight/0ac17cd4dad044d5838caccc68473445/verification.json`
は80checks成功。旧AH388件全hashと1151filesを保持し、追加AH40件を評価32/workflow8に分けて照合した。
次はWP-L4-ML-001のTracking部分として、既存評価receiptを専用local SQLite/loopback MLflow標準UIへ
取り込む限定実装を進める。本来のSpark/JupyterHub依存とMLflow全受入が完了したとは扱わない。

## 2026-09-17: saved evaluations in local MLflow Tracking

[WP-L4-ML-001](../L4/WP-L4-ML-001_MLflow_tracking.md)の専用MLflow 3.16.1標準UIへ、
既存11評価runsを11 MLflow runs・1118 metric pointsとして取り込んだ。
同じsourceの再取込は追加0/0。元receipt/attemptsの整合検証とhashによりIDを固定し、
既知IDの部分書込はreadbackから再開、不明なcreate結果や改変sourceは自動再作成しない。

Qwen152とmock10の全162評価を保持し、suite/provider/model/case signatureを別のまま表示する。
SREの16/6/8case比較と個別safe artifactを実UIで確認した。過去開始日時・費用等のNULLは保持し、
MLflowの取込時刻を評価の実行時刻にしない。元task/traceは保存済み相関で、新spanは生成しない。
Model training → Runsが表示先で、GenAI overviewの0 tracesを評価件数として扱わない。

初回import `86a99022a1a346cb937ace741d5bd4be`、repeat `90cf426b6e3b4ecfad82c5d96aca1d67`。
独立監査`mlflow-preflight/96cd337ad2a54b389c48c8cdf233854e/independent-summary.json`は、
元11 sourcesと全1118 points、取込後→再起動の59 tables/11 artifacts不変を確認した。
既存2489files/985directoriesとAH428件を保持し、モデル/API再評価・新Pawprint書込は0。
保存評価の閲覧・比較手段が増えた結果であり、評価品質の一般化やSpark/JupyterHub連携の受入ではない。

MLflow focused52件（runtime30/importer22）と正式全回帰1064件（1063成功・従来optional OTel 1skip）が成功。
private wrapperのumask継承とoffline testの実port bindによる失敗試行は保持し、
検証childの標準umask・fake socketへ修正して確認した。評価sourceや製品の許容境界を緩和していない。
