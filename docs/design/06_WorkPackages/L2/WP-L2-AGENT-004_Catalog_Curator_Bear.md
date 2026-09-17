---
project: Shirokuma
doc_id: "WP-L2-AGENT-004"
title: "WP-L2-AGENT-004 Catalog Curator Bear"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.4"
area: "workpackage"
tags: [shirokuma, workpackage, l2, agentops]
---

# WP-L2-AGENT-004 Catalog Curator Bear

## Summary

OpenMetadataの説明、Owner、Lineage欠落を補完する

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
You are working on Shirokuma WP WP-L2-AGENT-004: Catalog Curator Bear.
Read AGENTS.md, related ADRs, and this Work Package.
Use ADR-0034 local commits and bounded local metadata changes.
Validate retained evidence before proposing missing catalog fields.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-17 local bounded implementation plan

ADR-0034の範囲で、Data Engineer Bearが新規作成しcleanup済みの2生成tableを
OpenMetadataへ履歴登録する。実SQL relationが現在存在すると扱わない。
依存するMCP/実LLM/OpenMetadataに加え、WP-L2-AGENT-003の検証済み成果物を使用する。
追加resident service、image、cloud費用、global設定は不要。

Hostが固定`data-plans/<32hex>`からplan・project hashes・dbt manifest/results・Dagster ID・
query drain・cleanupを照合する。Qwenはstg/martの不足項目と固定evidence IDsだけを返し、
保存先、owner名、任意説明、SQL、entity FQNを生成しない。
Hostが履歴説明と既存`ShirokumaLocalExperiment` teamのowner、fixture→stg→martを確定生成する。
既存fixture・service・schema・teamはGETによる照合のみ。新規runの2tableと2lineage以外を書かない。
既存の一致する登録はno-opとし、別owner・人手変更や根拠不一致は上書きせず停止する。

- [x] 改変・失敗・未cleanup・未知schema/evidenceをmetadata mutation前に拒否する。
- [x] 実Qwenが不足項目の全件だけを選択し、既登録時no-opを返すことをgolden casesで評価する。
- [x] 3変換の成功証跡から生成metadataを登録し、説明・owner・lineageを実APIで読戻す。
- [x] 同一runの再実行で重複entityや不要更新がなく、既存保持metadataが不変である。
- [x] モデルusage/latencyと適用結果のPawprintを照合し、安全なreceiptを保持する。
- [x] README/RunbookとVaultへlocal functional evidence・未解決範囲を記録する。

Lineageは検証済みdbt dependenciesに基づくManual sourceの登録で、native OpenLineage
イベントを新しく発生させたとは主張しない。実業務ownerの推定、自由なdescription生成、
任意entityの更新、catalog全体の完全性、本番受入とhistorical GitHub gatesは未達のままとする。
ロールバックは新entrypointの利用停止とlocal codeの取り消しが基本。
保持metadata/backupの削除や全体復元は含めず、新規生成物もidentity照合なしには削除しない。

## 2026-09-17 local functional evidence

6合成case×2回のQwen評価が12/12成功、12監査readback、入力3682/出力862 tokens、
推論合計28.491秒。欠落項目全件・既登録no-op・未検証/対象外拒否を照合した。
`tmp/local-experiment/catalog-evaluation/5f2877e1271642afb1c364cf320a4ef6/`に保持する。

実Qwen→host証跡照合→OpenMetadata作成→readback→再適用が4workflowで成功。
各workflowのClickHouse/Loki/保存OTelが5event一致し、完了checkpointを確認した。

| 入力operation | Qwen判断 | 新規table/edge | repeat書込み | input/output tokens | 推論秒 | workflow UUID |
|---|---|---|---|---|---|---|
| name_lengths | curate | 2/2 | 0 | 314/96 | 2.978 | 93922abf793940489ea730ce5ee62825 |
| 同一run再実行 | noop | 0/0 | 0 | 296/42 | 1.590 | 37daceda57be43f1b15b8f825f259d64 |
| uppercase_names | curate | 2/2 | 0 | 314/96 | 2.958 | 456c8305e25d43ba8dbfb75958e1bf49 |
| row_count | curate | 2/2 | 0 | 314/96 | 2.980 | e55f8cf297ff4a67ae904457c689271a |

基点は`tmp/local-experiment/catalog-bear/`。03:59 UTCに全4workflowのsource再照合、
実API table ID/owner/description/lineageと20保存span、write journal完了を独立再検証した。
新規entityは6tables、6edges。事前保存したfixture/team/service/schemaのAPI結果は前後完全一致。
`tmp/local-experiment/catalog-preflight/a17284a0e35441e09ac690855ace15bf/final-verification.json`。
事前backupは`tmp/local-experiment/metadata/backup-20260917T034530Z.dump`。

Catalog50 tests（evidence12、API16、workflow/provider22）を含む全回帰404 testsと設計72 documentsを検証。
source検証はData Planのmaterialization成果物が境界で、別directoryのData Bear推論receiptを
暗黙に認証するものではない。今回の3入力は実Qwen Data Bearの成功tokenを明示指定した。
部分API失敗時はwrite前pending、確認済みID、成否未確定のsafe private journalを保持する。
複数APIは単一transactionではなく、失敗時rollbackや外部同時編集との原子的分離は保証しない。
再実行方法は[Catalog Curator runbook](../../../../tools/local_experiment/bears/CATALOG_CURATOR.md)。
