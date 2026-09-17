---
project: Shirokuma
doc_id: "WP-L3-ICE-001"
title: "WP-L3-ICE-001 Iceberg branch/tag clone demo"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.6"
area: "workpackage"
tags: [shirokuma, workpackage, l3, lakehouse]
---

# WP-L3-ICE-001 Iceberg branch/tag clone demo

## Summary

Time Travel/Clone風デモを作る

## Context

このWork PackageはLevel L3「OSS Snowflake再現」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

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

`level:l3`, `area:lakehouse`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L3-ICE-001: Iceberg branch/tag clone demo.
Read AGENTS.md, related ADRs, and this Work Package.
Use ADR-0034 local commits and a fresh disposable Iceberg sandbox.
Verify time travel, restore CTAS, rollback and owned cleanup without changing retained tables.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-17 local bounded implementation plan

既存のTrino483/Polaris/SeaweedFSを使用し、新UUIDの所有印付き2tableだけで
snapshot参照・別tableへの復元・実験tableのrollbackを検証する。
現物fixtureは読み取りのみで、前後のschema/rowsが不変であることを照合する。
歴史的PR/CI gatesは外部delivery経路として残し、ADR-0034 local実行を止めない。

- [x] 新規tableに2行を作成しsnapshot Aを取得、追加1行でsnapshot Bへ変化する。
- [x] 現在値とA/BのFOR VERSION AS OF結果を独立期待値へ照合する。
- [x] Aから別の新規owned tableへCTAS復元し、元実験tableをAへrollbackしてreadbackする。
- [x] 実snapshot IDs、parent/history、操作順とquery identitiesをprivate receiptへ保存する。
- [x] 成功/失敗/中断時、同run queryの終了と所有印確認後だけ新規tableをcleanupする。
- [x] 不明なquery/所有権/cleanup結果は不明のまま保持し、成功としない。
- [x] 保持fixture不変・生成table残留0、tests、README/Vault更新を確認する。

新runtimeやモデル呼出は追加しない。既存fixtureのrollback/drop、snapshot expiry、
orphan file削除、保持data/backupの破壊、global設定変更は行わない。
このCTASは新しいdata filesを作る論理コピーでありzero-copy cloneではない。
Named branch/tag作成・一般的な復元保証・本番受入は未達として残す。
SQL構文は[Trino483公式Iceberg connector](https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/connector/iceberg.md)の
snapshot/time-travel/rollback機能に従い、最終結果は実機で検証する。

## 2026-09-17 local functional evidence

実機run `94a2396b745747c693919337411acc9d` が成功（04:48:50–04:50:30 UTC）。
source snapshot A=5790915452109445581（2行）からB=369089816394298103（3行）を生成し、
Aの過去参照と別tableのCTAS復元（独立snapshot5723397051227493413）が2行で一致。
sourceをAへrollbackした後もBの過去参照は3行で一致した。24手順、worker24/control13 queries。
main refs=A、B ancestor=false、A/B snapshots保持を照合。新規owned2tableだけを削除した。

初回`9c127032f70b4bad833a9d3141368081`はrollback後の履歴行数の期待誤りでfailedとして保持。
Trino483の[HistoryTable source](https://github.com/trinodb/trino/blob/483/plugin/trino-iceberg/src/main/java/io/trino/plugin/iceberg/system/HistoryTable.java)は
snapshot一覧と現在の祖先判定を返し、head移動logの末尾A行を追加しない。
正しい2行の期待へ修正し、誤った3行・wrong head/ancestorを拒否する回帰を追加した。
初回失敗時にも保持fixture不変、owned2table cleanupが成功した。

04:51 UTCの独立実readで、2run合計4生成table残留0、自身のworker/control active query0、
保持fixtureのschema/rows/snapshots/history/refsが事前と完全一致。
証跡は`tmp/local-experiment/iceberg-sandbox/<run-token>/summary.json`と
`tmp/local-experiment/iceberg-sandbox-preflight/8ec05a4823724073bfbd070956af8ec2/`に保持。
20 focused tests、全回帰506 tests、設計75documents（次のGrafana計画を含む）が成功した。

再実行は`make local-iceberg-sandbox`。停止要求は専用childだけへsignalを送り、
query終了確認・owner marker照合・限定cleanup後に管理tunnelを閉じる。
応答不明のPOSTは失敗観測時点からserver上限を待ち、未確認ならcleanupを保留する。
新規使い捨て2tableの小さなdata/metadataだけを使い、保持snapshot expiry/backup削除はない。
[Runbook](../../../../tools/local_experiment/iceberg_sandbox/README.md)にSQL・履歴の意味を記録した。
Named branch/tag、zero-copy clone、保持tableの復元保証、本番受入は未達のまま維持する。

## 2026-09-17: named-reference experiment plan

次のlocal増分は新規owned `refs_<uuid>` table1個のnamed tag/branch。既存Polaris RESTの
set-snapshot-refとTrino483の名前付きtime travelを利用し、PyIceberg/新serviceを追加しない。
read-only preflight `iceberg-refs-preflight/0aacab2bf4ac4e108f3da461b8aede0c`で
同UID Polaris Service、local_experiment namespace、現在のcatalog管理/namespace write role、
保持fixture UUID/headを確認した。権限変更は0。実set-refの検証結果は次節に記録する。

A2行にbaseline_tag/audit_branchを作成→mainへ固定1行appendしてB→名前付き読取は両方A、
mainだけB→branchのみCASでBへ変更→tagA/branchB/mainBをREST/$refs/行期待値で照合する。
UUID/main/ref条件付きでcommitし、古いexpected refの競合拒否とmetadata不変も確認する。
ref変更前後に同current snapshotとdata-files inventoryが不変であることを別途照合する。
異なるtableのzero-copy clone、branchへの独立書込み、retained table復元保証は主張しない。

固定名・snapshot IDsは新規tableの実metadataからだけ取得する。任意SQL/URL/保存先は受け付けない。
API結果不明はpendingを保持して後続mutationとcleanupを止め、無条件再試行しない。
正常時はowned queryをdrainし、同UUID/COMMENTのtableのみ削除し、保持fixture全履歴/refsと
table一覧の不変を確認する。snapshot expiry/orphan file purge/保持dataやbackup削除は行わない。
[Runbook](../../../../tools/local_experiment/iceberg_refs/README.md)とprivate receiptへ実結果を記録する。

## 2026-09-17: named-reference functional evidence

実機run `9ad2e966fd6c414698abca0706f97467` が初回で成功（08:53:27–08:56:17 UTC）。
新規table UUID `e2972708-dfdb-4142-bb39-76df06b91ada`、A=480567632882700496（2行）、
B=8990253737475478975（3行）をRESTとTrinoで照合した。85手順、worker32/control28 queries。

- [x] Aにtag/branchを作成し、mainのBへのappend後も両名はAの2行を返す。
- [x] 条件付きbranch更新後はtag=A2行、branch/main=B3行となる。
- [x] 古いexpected branch Aを使う更新は型付きHTTP409拒否。metadataとrefsは不変。
- [x] 3成功commitと1拒否の前後でcurrent snapshot・data-file一覧hash/件数/行数/sizeが一致。
- [x] 自身のqueries終了後、同UUID/COMMENTの1tableだけ回収。保持fixture全履歴/refsと全table一覧が一致。

API 200/200/200/409の単発送信・readback、所有権・cleanupをprivate summaryへ保存した。
Aは1file/2records/368bytes、Bは2files/3records/740bytes。これらはTrinoのfile metadata観測であり、
object内容の全byte hash照合ではない。参照更新によるsnapshot/data-file metadata不変の証拠とする。
Pawprint追加・LLM呼出・認可変更・新service/imageはない。private操作journalがこの実験の監査記録。
認証Secret読取もhost15秒/API10秒/合計64KiBの上限を持ち、異常時はOAuth送信前に止まる。

41 focused testsと全local回帰915件（914成功、標準環境optional OTel1skip）が成功。
同OTel結合は前増分の専用venvで確認済み。設計79documents/関連14testsも成功。
現在の機能到達点はnamed references・snapshot read/CTAS/rollbackまで。異なるtableのzero-copy clone、
branchへの独立書込み、保持tableの復元保証、汎用コンソール統合、本番受入は未検証のためlocal-partialを維持する。

独立最終監査 `iceberg-refs-preflight/8e22af705b4644928b783811e4fac7b7/final-verification.json` が成功。
保持Kube6Pods/27resources/6CM/3Secret UIDs、Docker3、履歴4tables/5DDL、fixture全履歴/refsと
全table一覧、Pawprints366全行hash、既存247証跡filesが前後不変。Pawprint追加は0。
保存receiptの41項目を再計算し、実Trinoでも捕捉したworker/control全60IDsがFINISHED、active0。
独立readback後に監査用tunnelも終了した。
