---
project: Shirokuma
doc_id: "WP-L5-LINEAGE-001"
title: "WP-L5-LINEAGE-001 Lineage completeness dashboard"
status: local-partial
created: 2026-07-05
updated: 2026-10-02
version: "0.6"
area: "workpackage"
tags: [shirokuma, workpackage, l5, metadata]
---

# WP-L5-LINEAGE-001 Lineage completeness dashboard

## 2026-10-02: lineage report output pinned to owned directory descriptors

`write_report`の従来の`directory(OUTPUT_ROOT)`確認とpathname `output.mkdir`間に親pathを差し替えると、隔離tempの模擬別treeへ新report JSON/HTMLを作成してしまうことを負例で再現。checkout内の未作成親は開いたdirectory fdから相対作成し、既存親/出力root/新runの所有・mode・inodeを照合する。新runと2固定fileはfd相対作成・fsyncし、最後に現pathと開いたfdのidentityを再照合してからreport pathを返す。差し替え後の模擬別treeは空、元inodeに作成されたreportは失敗時も消さず、手動確認に残す。既存rootへのsymlink親も書込み前に拒否。既存report・catalog・Data Plan保存物・backupは削除/再生成しない。模擬同UID actorが既に開いたprivate root内の新run entryを作成直後に改変する競合と、保存source/catalogの独立認証は未解決。

実metadata API/VMは停止状態のまま使用せず、tempで生成したreportのみの機能検査。従来の歴史的分母10edge/10presentは再観測していない。既存のL5未達（全catalog・常時更新・column lineage・本番/人間受入）を残し`local-partial`。戻す場合は出力writer差分だけを以前のローカルrevisionへ戻し、差し替え負例で作ったprivate runも同一inodeを確認するまで削除しない。focused・全回帰の実測は[Milestones](../061_Local_Experiment_Milestones.md)へ記録する。

## Summary

Lineage充足率を可視化する

## Context

このWork PackageはLevel L5「OSS Fabric再現」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

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

`level:l5`, `area:metadata`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L5-LINEAGE-001: Lineage completeness dashboard.
Read AGENTS.md, related ADRs, and this Work Package.
Use ADR-0034 local commits and a bounded read-only report for verified historical Data Plan evidence.
Include tests, docs, risk notes, and rollback instructions.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-17: bounded local implementation plan

依存WP-L1-META-001 / WP-L2-OBS-001はローカル実験で充足している。既存OpenMetadataと
Data Engineerの成功証跡を使い、履歴上のlineage充足状況を読み取り専用で可視化する。
歴史的PR/CI gatesは外部deliveryの条件であり、ADR-0034のlocal実行条件にはしない。

- [x] private Data Plan証跡を列挙し、既存のdbt/Dagster/cleanup検証を通ったrunを特定する。
- [x] 検証済みrunあたりfixture→stg→martの期待2edgesを既知分母にする。
- [x] OpenMetadataの同一identity/owner/schema/lineage観測をpresent/missing/conflict/unknownへ分類する。
- [x] invalid/failed/incompleteな入力を無言で消さず、件数と理由を明示する。
- [x] API不達、取得途中の証跡・entity revision変化、観測上限到達をmissingと混同しない。
- [x] private JSONと自己完結HTMLを生成し、分母・観測時刻・各runの根拠を辿れるようにする。
- [x] 元証跡、保持metadata、稼働環境の前後不変を検証し、実結果を追記する。

この分母はこのcheckoutにある検証済み実験履歴だけであり、組織全体の全table/column/pipelineを
網羅しない。削除済みSQL relationのhistorical catalog recordをlive tableとして表示しない。
ログイン以外はGETのみで、metadata修復/削除・password変更・再取り込みを行わない。
新service/依存パッケージ/LLM呼出は不要。JSON/HTMLはホスト上のprivate証跡で外部公開しない。
初期増分はtable-level 2edge chainの充足可視化であり、任意queryの完全なcolumn lineage、
常時更新dashboard、cross-platform lineage、本番受入を主張しない。

## 2026-09-17: first read-only coverage result

run `4ef6d7943baf4e8c914f32f63381b9c9`（09:09:24 UTC観測開始）は初回で成功。
保存済み5 Data Plansすべてのdbt/Dagster/cleanup証跡を検証し、期待10edges中present6/missing4、
conflict0/unknown0を確認。観測はcomplete、登録率60.0%、coverage completeはfalseと分けた。
未登録は`396f97befadd41b3af49bf4db9be4b1c`と`eb8902c0a4e54c8591dcbfee02550927`の各2edge。
API不達を模擬した実source結合では、分母10を維持したunknown10/missing0/rate NULLとなった。

private JSON/自己完結HTMLを`tmp/local-experiment/lineage-coverage/<run-token>/`に保存。
ブラウザ1280pxで6/10・各分類・既登録/未登録の詳細展開を確認し、document/tableの横はみ出し0、
console error0。検証用loopback serverは終了した。mobile viewportの実画面検証は未実施。

独立監査 `metadata-coverage-preflight/18d58ceea4b34635be2e267336669a9f/verification.json` は
31checks成功。23API endpointsのUUID/version/full semantic hashと全5source projections、
既存6table/6lineage、保護892files（hash/mode/mtime）、資格情報/.envとadmin markerが前後一致。
Metadata3serviceの状態も再観測済み。他のKube/Docker/Pawprints366は直近Iceberg refs監査を参照し、
今回再読したという主張はしない。catalog mutation0、model/cloud呼出0、新service/dependency0。

focused34 tests、全local949件（948成功＋標準環境optional OTel1skip）、設計80documents/14testsが成功。
次は確認した2runの不足履歴を既存Catalog Curatorで限定登録し、新しいreportで変化を再確認する。
常時更新/全catalog/column lineage/本番受入はこの増分の達成条件ではなく、local-partialを維持する。

## 2026-09-17: observed gaps completed with Catalog Curator

初回reportでmissingだったData Plans `396f97befadd41b3af49bf4db9be4b1c` と
`eb8902c0a4e54c8591dcbfee02550927` に既存Curatorを適用し、historical4table/4edgesだけ追加した。
Catalog runs `48c698b868ad4b18bc69c5c3bebc65de` / `1bfb893944c04f98a463484e7386affb` は成功。
各初回は2table/2edgeの4writes、再適用は0writes。同一taskの実Qwen推論は各314入力/96出力tokens、
4106ms / 2950ms。cloud/retry0、cost NULL、2run合計628入力/192出力tokens。
traceは`a5898cccc00b61dd68f7d5a00fecca87` / `8ec47c336227cc55a056a130a960bd5f`。

補完後のread-only report `ea83feab78db4e2189d8adab339af346`（09:16:39 UTC）は5run・10/10edges、
missing/conflict/unknown0、observation/coverageともcomplete。元の60%reportは変更せず保持した。
独立監査 `catalog-completion-audit/fdcf070415da47f082d8e0e5a39521dc/verification.json` の84checksと
metadata比較45checksが成功。既存6tables/6edges/anchorsは不変、新4tables/4edgesだけを帰属させた。
旧Pawprints366行のhash不変＋新10行（現376）、Loki10件/保存実OTel10spansの相関・usageを照合。
保持Kube/3Docker、履歴4tables/5DDL不変。934保護files中933は完全不変、既存認証入口の
admin-initialized markerだけmtime更新（content/mode/.env不変）を既知差として記録した。
reportの全10edge verdictとmetadata aggregate hashも独立APIから再計算済み。

本増分は既存実装を使う実験結果の更新で、直前の全949tests/設計80documents/14testsを継承する。
次はData→Catalogの一連実行と中断再開を専用の小さな入口へまとめる。Data開始intentと保存先を
先に固定し、同handoffでDataを二度呼ばず、成功証跡を検証できた場合だけ同Data PlanをCatalogへ渡す。
