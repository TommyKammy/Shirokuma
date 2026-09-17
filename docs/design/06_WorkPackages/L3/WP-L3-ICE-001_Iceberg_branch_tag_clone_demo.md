---
project: Shirokuma
doc_id: "WP-L3-ICE-001"
title: "WP-L3-ICE-001 Iceberg branch/tag clone demo"
status: local-partial
created: 2026-07-05
updated: 2026-09-18
version: "0.9"
area: "workpackage"
tags: [shirokuma, workpackage, l3, lakehouse]
---

# WP-L3-ICE-001 Iceberg branch/tag clone demo

## Summary

Time Travel/Clone風デモを作る

## 2026-09-18: retained snapshot reader plan

`query snapshots --table schema.name`と`query select ... --snapshot-id ID`を追加する。
通常のcurrent-head Reader v1は維持し、明示snapshot指定だけ別のschema v2 profileに通す。

- [x] fresh table発見・schema・Polaris metadataから最大64個の保持snapshotを一覧化する。
- [x] ID/parent/sequenceを文字列で保持し、IDの大小を時系列・祖先関係として扱わない。
- [x] 選択snapshotのfield IDs/name/type/nullability/orderが現schemaと一致する場合だけSELECTする。
- [x] current headとselected snapshotを分け、前後metadata/UUID/schema/head/一覧の変化を拒否する。
- [x] 未掲載ID・旧schema・未知queryは成功やcurrent headへのfallbackにせず、再送しない。
- [x] 新規所有tableのA2行→B3行で一覧・過去/現在指定・limit・拒否を同じCLIから実証する。
- [x] 全query終了・接続回収と同UUIDの専用table回収、保持table不変を照合する。

過去版のschema変換、named refs指定、SQL一般化、clone、モデル呼出し、保持table変更は追加しない。
旧Query Doctorへ歴史profileを暗黙接続せず、current STATSと過去snapshotを混同しない。

## 2026-09-18: retained snapshot reader evidence

6回のschema v2 CLIで、保持tableの一覧、新規所有tableの一覧・過去版2行・現在版3行・
過去版limit1・未掲載ID拒否を確認した。5成功＋期待した拒否1で、全queryが終端・接続回収済み。
snapshot指定なしのReader v1も同じ現在版3行を返した。不正ID `01`はchild/evidence割当より前に拒否した。

専用fixture `f5063b822ee0429caeb96a3b788d9b88`、UUID `f13bf2e8-4253-4e1e-bc1b-be820290528a`。
Trinoの空CREATEはsnapshot `2735408811170240157`を作り、INSERT1回でA=`696843948967848022`の2行、
append1回でB=`176340221214278052`の3行になった。B.parent=A、A.parent=空版を確認した。
snapshot IDsは新しいほど数値が小さいが、sequence/timestamp/parentで正しく一覧化できた。

保持一覧 `6f0d4be7e3a075196913df1a0f604635`、所有一覧 `3263d761201e51692bfc4ca915af00fc`、
過去読取 `4c33c07e37b6305f2bddc90274ef05a4`、現在指定 `b68c5714afe8a9b12d53728cc68b3fa2`、
過去limit1 `4c16d688dc1ac5c103e8c46c7944c0a6`、未掲載ID `7dd47b77ce20c60ceccc427ae318d039`。
未掲載IDは `SNAPSHOT_NOT_RETAINED`、inventory/schemaの2queryだけでdata SELECT0。
通常Reader `6431c2acad27aa9bd2261779948da334`はschema v1・現在head Bを維持した。
これら7実行のworker24/control7、計31 query IDsはすべて終端である。

BIGINTの2^53超と最小値、DECIMAL(38,6)、日本語、NULL、DATE、BOOLEANを精度照合した。
過去Aのrows SHAは`b5baedd878d293693ac734baa5fc4f2318dea54051f0d5d08c6bc9b77b28d415`、
現在Bは`edd61b8d3ce96dcd2fcdef35dda1b22bc072fa498eccc08891f248f346ea4692`。
limit1は取得2/返却1/truncated=true。current_snapshot_idはB、snapshot_idは選択したA/Bを明示する。
全実行の終了・lock解放・同identity確認後、fresh UUID/head/schemaを照合して生成tableだけDROP1回。
保持fixtureの全6投影とtable一覧は元に戻り、モデル・新service・新imageは0である。

実機前にTrino 483の小さい整数literal型を確認し、新profileのsnapshot IDだけを
明示 `BIGINT 'ID'`へ固定した。正のint64全範囲をfake HTTPで検証し、既存ReaderのSQL制約は維持。
private fixtureもCatalogReadの2GET上限を維持して2個へ分け、append前A・後Bを照合した。
unknown appendの再送/自動DROP禁止、原取消の保持を21 self-checksで確認した。
新Python71tests（contract29/observer23/owner19）、Go149 top-level＋77subtests・実Python Owner7経路、
vet/race、全local回帰55suites/2018件（2017成功＋既存optional OTel1skip）、設計83documents/14testsが成功。
schema変更・中断・未知POST等の故障注入はofflineであり、実機失敗注入と混同しない。
証跡・54source/test/binary/helper sealは
`tmp/local-experiment/query-snapshots-checks/ab5aa55699064e728e47f6f0228721c8/`に保存した。
利用方法は[Snapshot reader runbook](../../../../tools/local_experiment/query_snapshots/README.md)を参照。

保存証跡の独立185checksと、保持環境・相関の独立46checks（fresh21＋bindings25）が成功した。
3799旧files・14Pods/27resources/3Docker/16policy・MLflow identityは不変、AH480行も全hash一致で増分0。
5portsが空き、locksは43→50（新CLI7実行分）で全非active。54 source/helper/binaryのhashも一致した。
保存検証は上記checkdirの`independent-saved-ac8214ae28544b9a9e1d22963d023778.json`、
前後監査は`query-snapshots-preflight/59bcacce1e6f498fa09915482426336b/independent-verification.json`に保存。
保持fixture復帰は専用cleanupが取得した6投影の保存相関で、監査の追加Trino実行は0である。

## 2026-09-18 owned branch publish plan

新規所有tableだけで、検証済みbranchをmainへfast-forwardする。既存のB/C兄弟snapshotは
拒否対象として保持し、別fresh tableでA2行→main B3行→branchをBから作成→Spark C4行とする。
hostがC.parent=Bと現在refsを照合した後、UUID/main/branch/tagのCAS条件付きREST1回でmainをCへ進める。

- [x] 限定writer/profileでC4行・main B3行・tag A2行をSpark/Trinoから検証する。
- [x] mainがbranch snapshotの祖先であることと、現在UUID/marker/location/ref/metadataを確認する。
- [x] durable pending intentの後だけ、main更新1件のREST commitを最大1回送る。
- [x] refs.main/current ID/snapshot log等の合法deltaだけを認め、旧snapshot/rows/filesを保持する。
- [x] 別fresh negative tableでmain Dとbranch Cを兄弟にし、stale expected Bと非祖先DをPOST0で拒否する。
- [x] 不明結果は再commit/再INSERT/自動rollback/DROPを行わず保留する。
- [x] 成功または確認済み拒否後にquery drain/権限回収/同UUID table回収と保持環境不変を確認する。

保持table・以前の実験証拠は変更しない。新image/package/service/model呼出しは追加しない。
REST set-snapshot-ref自体は祖先関係を強制しないため、host側検証とref CASを分ける。
汎用merge/rebase、異なるtableのzero-copy clone、branch別認可、並行writerの一般保証、
object全byte hash、保持データ復旧や本番受入の達成は主張しない。

## 2026-09-18: verified branch publication

正例 `75e820ef797a43fba52585860588b2c0` が初回で成功（15:49:49–15:53:00 UTC、9月17日）。
新table UUID `509209ed-0866-4b2a-9032-d8840a923f10` で、A=1002717412236272376の2行から
main B=9211570477933275546の3行を作り、Sparkがbranchへid4を1回appendして
C=2224987170743755404の4行を生成した。C.parent=Bを確認し、Spark終了・一時access回収後、
UUID/mainB/branchC/tagAの4条件付きREST POST1回（HTTP200）でmainをCへ進めた。
CのIDはBより小さく、IDの大小ではなく実parent chainを判定している。

main/branch=C4行・tag=A2行とnumeric A/B/CをTrinoで照合。新snapshot追加0、
旧snapshot全hash・schema/spec/properties・branch/tag不変、main/current IDと所定の履歴追加だけを許容した。
Aは1file/612B、Bは2files/1228B、Cは3files/2417Bで、published mainの`$files`が
Sparkで取得したCの一覧へ完全一致した。40steps、worker38/control16 queriesが完了した。

負例 `9a88d1803c814ff18dca4f9f965f99e8` も初回で成功（15:53:21–15:56:44 UTC）。
UUID `7cfcab0c-5c32-4cb1-87b6-ebd01b65eacf`、A=7888007345279584392、B=5336059430357120780、
C=1391304228033215520、D=3974582407880763445。CとDはともにBを親とし、
branch Cはid4、main Dはid5を含む異なる4行となった。expected Bは`STALE_MAIN`、
expected Dは`MAIN_NOT_ANCESTOR`で拒否し、publish POSTは0回。
全named/numeric7読取とC/Dの独立追加fileを照合。Dは3files/1874B、44steps、worker42/control16 queries。
これはhost preflightの拒否であり、並行更新に対する実server CAS409の検証ではない。

両runともnative既知19UID、一時NP2・Polaris3entities/bindingsを回収し、所有query終了後に
同UUIDの生成tableだけをDROP。保持fixtureの全schema/rows/snapshots/history/refsとtable一覧は前後一致した。
既存imageを再利用し、新image/build/service/model/Pawprint追加は0。
再現入口と未知結果の扱いは[Runbook](../../../../tools/local_experiment/spark_publish/README.md)を参照。

新75 tests（contract20/table29/workflow26）、Spark全467、全local回帰52suites/1776件
（1775成功＋既存optional OTel1skip）が成功。取消保全、再送禁止、最終drainの再確認、
float/boolをsnapshot IDとして受理しない境界を含む。source20filesは実機前後で不変。
checksは`spark-publish-checks/4c337304b84144e4bb87ec3090996944/`、保存証跡の独立照合は
`spark-publish-audit/fe34393128fd433099e06c0434c0b0a1/`（正67＋負64 checks）へ保持した。
raw REST本体は保存せず、metadata hashの比較とsource固定journalのPOST回数を証拠とする。
独立環境監査`spark-publish-preflight/31744da846564be486e63ac1eb804215/`の前後22項目も成功。
AH470全行、保持3503files、14Pods/27resources/3Docker、MLflow store/process/indexと
既存network境界は不変、Spark namespace/Pod残留0・5ports解放・28旧locks非activeを確認した。
深いMetadata/MLflow全内容等は先行sealの継承であり、今回の全API再走査ではない。
object全byte hash、branch別認可、zero-copy clone、汎用merge/並行writer、本番受入は未達のまま維持する。

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

## 2026-09-17: independent branch write plan

既存Spark 4.1.3/Iceberg 1.11.0のverified imageで、named branchへの独立書込みを検証する。
新規owned `spark_<token>` tableだけを使い、既存4列の固定データで次を確認する。

- [x] base2行でAを作り、baseline_tag/audit_branchを条件付きRESTでAへ作成する。
- [x] mainだけへid3の行を追加してBとし、tag/branchはAのまま保持する。
- [x] Sparkからbranch_audit_branchへid4の別行を1回appendしてCとする。
- [x] main=B/tag=A/branch=C、B.parent=A/C.parent=A、BとCの内容相違をSpark/Trinoで照合する。
- [x] A/B metadata・files不変、CがAのfilesを保ちbranch分だけ追加することを照合する。
- [x] native終了・owned query drain・権限回収・既知write結果の後、同UUID tableだけを回収する。
- [x] 保持fixture全履歴/refs・table一覧・既存runtime/data不変とlocal testsを確認する。

Trino483の`$files`はcurrent main Bの確認に使用し、branch Cのfile一覧と取り違えない。
各snapshotのfilesはSpark Java scan.useSnapshotで取得する。
Trinoは名前付き/数値snapshot読取で独立固定oracleへ照合する。
初期empty snapshot Eが存在する場合はE/A/B/Cを区別する。

新image build/追加service/model呼出しは不要。既存2CPU/4Gi・一時table role/NPを再利用する。
任意SQL/ref/接続先は受け付けず、REST/Spark writeの不明結果は再送・再INSERTせず保持する。
既存S3 bucket scope、内部Iceberg HTTP/commit retryの境界は変更しない。
branch別認可、異なるtableのzero-copy clone、並行writer、汎用復旧や本番受入は対象外。
再現とcleanup境界は[Runbook](../../../../tools/local_experiment/spark_branch/README.md)に記録する。

## 2026-09-17: Spark independent Iceberg branch write

WP-L3-ICE-001のbranch独立書込みを、既存Spark4.1.3/Iceberg1.11.0 imageと
Polaris/SeaweedFS/Trino483で実証した。新service/image/build/model呼出しは0。
成功runは `2ab07312d48a4ff89c8ce62cf5b98b41`（13:55:16–13:57:57 UTC）、
table UUIDは `3d6063f9-56e4-4467-93e6-bb04284785dd`。

| snapshot | ID | 内容 | parent |
|---|---|---|---|
| E | 5816371238383776775 | 初期empty | NULL |
| A | 8820611245447280854 | base-a/base-bの2行 | E |
| B | 6408387352553140918 | A＋id3 main-only | A |
| C | 8114339936233885914 | A＋id4 branch-only | A |

条件付きREST2回でtag/branchをAへ作り、mainへid3を追加後、Sparkからbranchだけへ
id4を1回INSERTした。main=B/tag=A/branch=C、BとCが兄弟であることを照合した。
Spark freshSessionのDataFrame/SQL読取とTrinoのnamed/numeric snapshot全6読取が
固定oracleへ一致し、B/Cの行数が同じ3でも内容差を検出できる。
branchのgroup結果はbucket0=1行/合計10、bucket1=2行/合計60。
executorはstage8、task9/8、partition0/1、attempt0の別2tasksで確認した。

Sparkのsnapshot指定scanでA/B/Cを確認し、Aは1file/612B、Bは2files/1228B、
Cは2files/1801Bだった。BとCはAの612B fileを共有し、mainの追加616Bと
branchの追加1189Bは別path。旧E/A/Bのsnapshot metadata hashとA/Bfilesは不変。
Trinoの`$files`はcurrent main Bだけへ使用し、Cの一覧と取り違えていない。
これはtyped file metadataの照合であり、各object全byte hashの保証ではない。

31host手順、2成功CAS、native19UIDと一時NP2・Polaris権限entity3/2bindingsを回収した。
owned query drain後に元UUID/marker/locationのtableだけをDROPし、保持fixture全履歴/refsと
全table一覧が実行前と一致した。S3 orphan不存在・即時JWT失効・汎用rollbackは主張しない。

初回 `02c0424ff30845cf9a5a2e58a1f55321` はINSERT応答後のtag読取で失敗として保持した。
Spark4.1用Iceberg1.11.0は旧DataFrame `tag` optionをIllegalArgumentExceptionで拒否する。
tag側だけbuilt-in `versionAsOf`へ1行修正し、旧optionの書込み後READ_FAILEDと
修正版の成功をgenerated-job fakeで再現する回帰を追加した。branch option/SQLは変更していない。

初回はunknownを成功にせずtableを保留し、再INSERTしなかった。別private recovery
`spark-branch-recovery/0e7f5f5031ed4011a5f7cf54f7c9cb56/`で、元UUIDのmain/tag/branch全6read、
兄弟parent、旧snapshot hash/mainfiles、fresh native namespace・NP・entity不存在を確認。
C=`7301240948059612366`の書込みをhost readbackで確認してから同UUIDだけを1回DROPした。
保持fixture全履歴/table一覧は復帰し、原失敗27filesのbytes/mode/mtimeは不変。
原Sparkのfresh read/executor/after file検証は未達のままで、host recoveryを完全成功へ読み替えない。
inspection/cleanupのquery drainはcancel0。汎用の自動replay/recovery commandは追加していない。

新61tests（contract17/table27/workflow17）、既存を含むSpark392testsが成功。
全local回帰46suites/1525件（1524成功＋既存optional OTel1skip）が成功した。
実行・全log・集計は `tmp/local-experiment/spark-branch-checks/18a8dcbd983046b99e466b8d8f1d80d8/`。
事前/独立監査は `spark-branch-preflight/05f3546728dc4fa5a445221c3e4b143c/`。
事前はAH440、Metadata7plans/14tables/14edges、MLflow15runs/1210points/15artifacts、
14Pods/27resources6CM3SecretUID・Docker3・images553・保持3179filesと22file backupを保存した。

named referenceへの独立書込みを満たしたが、branch別認可、異なるtableのzero-copy clone、
並行writer、汎用復旧、保持tableの復元保証や本番受入は未検証のためlocal-partialを維持する。
次は既存Query Doctorを製品CLIのrun/statusから安全に実行・照会できるようにする。

独立監査 `spark-branch-preflight/05f3546728dc4fa5a445221c3e4b143c/independent-verification-final.json`
は109checks成功。fresh after/extra-afterで保持AH440件、Metadata7plans/14tables/14edges、
MLflow59tables/15runs/1210points/15artifacts、14Pods/27resources/6CM/3SecretUID、
Docker3/images553、旧3179files、停止時backup22files/34dirsが不変と確認した。
両trial namespace不存在、監査用tunnel終了も確認した。保存証跡の44query完了と31steps、
19UID回収、同tableのSpark/Trino出力・snapshot int64・file metadataを独立照合した。
削除後tableの再読取やobject全byte hashは主張しない。監査側のshape仮定2点の修正前receiptも保持。
design83docs/14tests・secret scan・diff checkが成功し、関連WP/MilestonesをVaultへ同期した。
