---
project: Shirokuma
doc_id: "WP-L2-AGENT-003"
title: "WP-L2-AGENT-003 Data Engineer Bear"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.7"
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

## 2026-09-17: durable Data to Catalog handoff

既存のData/Catalog async APIを使う専用入口 `run_data_catalog.py` を追加した。
privateなhandoff UUIDごとに排他lockを取り、Dataの保存先と開始intentをatomic write・file/親directoryの
fsyncで先に固定する。同じhandoffでDataを呼ぶのは最大1回。intent直後の中断では0回の可能性があり、
分散transactionや副作用のexactly-onceを保証するものではない。結果不明を理由にDataを再起動しない。

Dataの保存成功receipt、入力hash、adapter stdout/summaryの一致、独立結果oracle、dbt/Dagster証跡、
query drain/cleanupを検証した場合だけ、同一Data Plan token/hashをCatalogへ渡す。
自然文はstdinで受け取り、本文は保持しない。既存3caseも選択でき、任意SQL/保存先/API URLは受け付けない。
Catalogの部分失敗は旧attemptを保持して同じsourceへの新attemptで再開する。
完了済みresumeは保存証跡の再検証とreplayだけで、現在のcatalog状態を再観測したとは扱わない。

実自然文handoff `cc018604c0b8407aadad52b0b0c58df7` が成功した。

| 段階 | 保存token | 確認結果 |
|---|---|---|
| Data Bear | `e8ecab2de2c04627bbb10f78cf111537` | 採択operationはuppercase_names |
| Data Plan | `bd490f27d89b45ebb22a6f34f2ef3b9e` | `[[1,SHIROKUMA],[2,POLARIS]]`、materialization2回、owned2relation回収 |
| Catalog | `175f84bd32a7403497bdfd7f884137af` | historical2table/2edgeの4writes、repeat0writes |

同handoffの実completed resumeはchild/API/model呼出し0で成功。前後1013files、metadata31endpoints、
その時点のPawprints385行が不変だった。read-only coverage `42c0b1bcdf0b45709d6c25119c0c71e0` は
検証済み6run・期待12edgesの12/12登録を確認した。履歴登録の充足であり、生成SQL tableの稼働率ではない。

非対応要求のhandoff `a30d6e1b7772494dbe50cf673068e6a3` / Data Bear
`c2f97b6d37c247c58024e5c0cbefe96b` はunsupported/exit2、adapter0・Catalog0で終了した。
今回の正常Data/Catalogとunsupportedの実LLM計3callsは入力1125/出力136tokens、推論合計6605ms、
cost NULL・cloud/retry0。保存した実OTel10spansとMCP2件の返却span ID相関を区別する。
これらのworkflow観測は既存golden評価120/120へ加算しない。

handoff focused22、Catalog focused55、全local回帰976件（975成功＋標準環境optional OTel1skip）が成功。
全文logは `tmp/local-experiment/data-catalog-checks/8ee9569b05a04b7ca389364576e3fcfb9/local-regression.log`。
中断・保存失敗・同時起動・部分適用の再開はoffline回帰で検証し、今回の実機で途中取消は行っていない。
実行方法は [Data to Catalog runbook](../../../../tools/local_experiment/bears/DATA_CATALOG.md) を参照。
`make local-data-catalog-request`、同IDの再開は `make local-data-catalog-resume DATA_CATALOG_HANDOFF_TOKEN=<32hex>`。
新service/外部公開/保持データ破壊はなく、一般的な自然文意図の保証や本番受入は未達のままlocal-partialを維持する。

独立最終監査 `data-catalog-preflight/de60ba1818d74bad9a0a289176e9f72f/verification.json` の
107checksとmetadata48checksが成功。Pawprints388＝旧376全行hash不変＋新12、Loki12件、
保存実OTel10spans＋MCP2件の返却span IDを照合した。保持Kube/3Docker、履歴4tables/5DDL、
fixture全snapshots/history/refsとIceberg全10table一覧が不変で、生成2relationは残留しない。
旧964保護files中963は厳密不変。既存admin-initialized markerのmtime更新のみを既知差とし、
そのcontent/modeと.envは不変だった。新180artifactのhashも保存した。
metadataの追加は2table/2edgeだけで旧10table/10edge/anchorsを保持し、6run・12/12のcoverageを
fresh API hashから独立再計算した。


## 2026-09-17: Data to Catalog product CLI

`shirokuma data run --case NAME | --request-stdin`、`data resume TOKEN`、
`data status TOKEN`を実装した。profileはlocal-experiment、checkoutと既存専用venvを検証して
固定bridgeを起動する。環境install、service開始、GitHub操作、任意SQLやAPI URLの追加はない。
stdin本文はメモリ/child stdinだけに渡し、argv/env/保存receiptへ残さない。
早期allocatedは識別子であり永続intentやモデル呼出しの証明ではない。
固定JSONLを厳密検証し、child終了とreceiptが一致してから最終成功行を返す。

statusは既存lock/journal/receiptをread-onlyで検証し、busy、journal-only、saved-evidenceを区別する。
statusのexit0は観測成功であり、処理結果の成功とは別。run/resumeは成功0、unsupported2、
失敗・不明1。確認済み取消130/143を保持し、20分deadlineはunknown/1にする。
取消時はPython ownerだけにSIGINTを送り最大240秒cleanupを待つ。強制killや自動再実行はしない。
未確定ならtoken/PIDを返し、statusと保存証跡から状態を確認する。
Dataは同handoffで最大1回、完了済みresumeは保存証拠の再検証だけである。

実CLIによるname_lengthsの新規handoff `58aaec13018c59aee22eb33fcd3c13d0` がexit0で成功。
Data `11985f9a806047cbb51e81449527b292` → Plan `9ac5fe34e1a4436a93854a09605ab240` →
Catalog `9100587a19da4135884cf57adf2b3ea1` を記録した。結果 `[[1,9],[2,7]]`、
materialization2回、query drain/生成2relation cleanup成功。Catalogはhistorical2table/2edgeを
4writesで追加し、repeat0writes。保持fixture不変の判定と、履歴登録の証拠を分ける。
非対応stdinのhandoff `d86dd23e921f4b40d9eb90205bcebea2` / Data
`c139e7c97b5b47d3ad125a4f87f89245` はunsupported/exit2、adapter0・Catalog0。
3実推論は入力1238/出力141tokens、推論計6779ms、cost NULL、cloud/retry0。
既存golden評価の母集団へ追加せず、今回のworkflow実測として保持する。

旧完了handoffの実CLI status/resume前後で2823files、AH428全行、既存6plansの
OpenMetadata31endpoints（12table/12edge）が完全一致した。新完了handoffのstatus/resumeと
unsupported statusも成功し、関連1181filesのhash/mode/mtimeは不変。
不正stdinはchild起動前に拒否し、不存在statusはnot_found/exit1となった。
実機での途中取消は今回実施しておらず、取消・deadline・protocol破損はoffline subprocess回帰の証拠である。

Python bridge21 tests、Go Data17 testsが成功。Go全98tests、vet/raceが成功し、
全local回帰は44suites/1327件（1326成功＋既存optional OTel1skip）。
証跡は `tmp/local-experiment/data-cli-checks/7a23ef675b9f45eca8d7809090853ecf/`、
独立保持監査は `data-cli-checks/9a53b66a39d045329f7383806eaabaa5/`。
CLI/関連runbookを更新し、3操作のbounded機能、本番受入や一般自然文理解の未達を維持する。

最終独立監査 `final-verification.json` の121checksが成功。AH428→440の追加12件は
Data4＋Catalog5＋unsupported3に一致し、Loki12件、保存実spans10＋MCP返却span ID相関2も照合した。
既存metadata30API応答hashは不変、anchorには新stg node/edgeだけが加わった。
新sourceの2tables/2edgesを独立検証し、対象7plans/14tables/14edgesを確認。
保持Kube/Docker/SQL履歴/fixture全履歴/MLflow59tables11artifacts/cached553imagesは不変。
2823保護files中2820は完全不変、Gateway2logsは旧prefix不変＋各2追記、既存admin markerは
content/mode不変・mtimeだけ更新。raw比較のchanged判定を保存し、許容差分を別証拠で説明した。
