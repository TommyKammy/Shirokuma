---
project: Shirokuma
doc_id: "WP-L2-AGENT-005"
title: "WP-L2-AGENT-005 Query Doctor Bear"
status: local-partial
created: 2026-07-05
updated: 2026-09-18
version: "0.9"
area: "workpackage"
tags: [shirokuma, workpackage, l2, agentops]
---

# WP-L2-AGENT-005 Query Doctor Bear

## Summary

EXPLAINとquery historyから改善案を出す

## 2026-09-18: historical snapshot diagnosis

成功したsnapshot Reader v2の`evidence_token`も、既存の
`query diagnose --read-token TOKEN`から受理する。v1とv2のsource検証を分け、
public/model/provider/監査契約とv1 journalを維持する。

- [x] privateな終了済みv2 selectのrequest・元query identity・SQL hash・rowless selection・snapshot一覧を束縛する。
- [x] 現在のidentity Bを保持したまま選択Aを区別し、fresh windowが保存sourceと前後で完全一致することを要求する。
- [x] EXPLAINは元AのSELECTそのもの、STATSは全compatible current-schema列の固定subqueryを`FOR VERSION AS OF BIGINT 'A'`へ固定し、LIMITを付けない。
- [x] 元SELECT再実行0、5観測phase・所有query drain・接続回収後のQwen1回という既存順序を維持する。
- [x] fake HTTPでAの統計2/Bの統計3を区別し、expiry/schema不一致・metadata/head変化・型/byte制限・取消を検証する。
- [x] 新規2version tableの実過去読取から診断し、typed source・同snapshot統計・監査・保持状態復帰を独立照合する。

v2だけ`snapshot_catalog_before/after`をrowless journalへ追加する。選択Aの保持が失われた場合や
現在Bのmetadata/schema/refsが変わった場合はモデル前に拒否し、別snapshotへfallbackしない。
統計は選択snapshotを指定した見積りであり、実行による全件countや性能改善の測定ではない。
元query historyはID/user/source/SQL hashを照合し、履歴不在は原因を推測せずnot-retainedとする。
SQL・raw plan/history/statistics・行値はモデルへ渡さず、root cause未確定・改善未測定を維持する。

現checkpointのfocused Python106件とGo150 top-level/83subtests・vet/raceは成功した。
実機前の全local回帰55suites/2048件（2047成功＋既存optional OTel1skip）も通過した。
これらはoffline/結合検証の結果であり、以下の実機観測と区別する。
再現手順と制約は[diagnosis runbook](../../../../tools/local_experiment/query_diagnose/README.md)を参照。

実機diagnosis `e0a40664f9a78d8cca182943b7677ac3`が成功した。source Aのreadは
`b55da2066d6dbf6e7ec0af1a6d3c6bf3`、現在Bのreadは`68a6f2884d33e4d10508a371c4ea58ab`。
生成fixture `bcda2957f5de4db39a5b41c5e1e1d261`のUUIDは
`bb8fc5c5-ea93-4fdd-a655-95d267a9cd37`、A=`3046504324030823045`（2行）、
B=`2518908696447467114`（3行）。historical STATSは5列・row estimate 2を返し、
current Bの3行と混同しなかった。historyはobserved、queue0/analysis12/planning2ms、
created-to-end1212ms/started-to-end1199msだった。Qwen usageは525/95tokens、inference4208ms。

旧v1 source `e743a83a0d19bcbda85027ea8e67464b`からのdiagnosis
`09117eca13f71fbf67f4a890577b9f28`も成功。実際のhistoryはnot-retainedで全timingsをNULLとし、
STATSのrow estimate 2を保持した。Qwen usageは535/114tokens、inference3625ms。
2診断とも各5worker＋drain1、元SELECT再実行0、model1、監査3events、query/接続cleanup確認済み。
合計1060prompt/209completion tokens・7833ms、costはNULL、cloud/retry0。
これは今回取得した統計・履歴・推論の観測であり、性能改善や原因確定の証拠ではない。

実行記録と70source/binary sealは
`tmp/local-experiment/query-diagnose-snapshot-checks/18efcc40b83e4b839c1747d6cbe5d5e5/`に保存する。
生成fixtureはfresh identityと終了済みsource/diagnosisを確認後、同UUIDへDROP1回し、保持fixtureの6投影とtable一覧が復元した。
Reader2回のworker8/control2と診断2回のworker10/control2、計22query IDsは一意・終端済み。
Readerが元データをSELECTした回数は2、診断によるその再実行は0である。

独立前後監査60checks（fresh23＋保存束縛37）が成功。3878旧filesと70source/binary/helperが不変、
AHは旧480行の全hashを保持して486行へ増加し、新6event IDs/Loki6/保存spans6が一致した。
保持Kube/Docker/MLflow/policy/networkは不変、5portsが空き、54locksは非active。
監査と33保存inputsのhash束縛は
`query-diagnose-snapshot-preflight/92bebffcde6b4bed8c3bd65127bfb163/independent-verification.json`に保存した。
設計83documents/関連14tests、repo/Vaultの同期と関連リンク、secret/diff検査も成功した。
この増分のlocal機能検証は完了したが、一般原因診断・性能改善・本番受入は主張しない。
次は`query diagnose report [--token TOKEN]`で実診断の保存記録を読み取り専用で一覧/単件表示する。

## 2026-09-18: diagnose a completed Reader query

`query diagnose --read-token TOKEN`を追加し、実際の成功selectの証拠から診断する。
旧2query固定のQuery Doctor契約を維持し、新しいReader profileを分ける。

- [x] privateで終了済みの成功selectだけを受理し、request・query ID・SQL hash・identityを照合する。
- [x] 元SELECTは再送せず、同SQLのEXPLAIN1回、元queryの限定history1回、固定tableのSTATS1回を読む。
- [x] UUID/schema/headの変化は診断前後で拒否し、履歴不在の原因を推測せず、欠測はunknownとして残す。
- [x] SQL・table/列名・行値をモデルへ渡さず、限定数値・欠測・所見から既存Qwenを1回だけ呼ぶ。
- [x] 固定のfinding/next-step IDsを検証し、root cause未確定・改善未測定を保つ。
- [x] 保存監査/usage/終了・取消とCLI protocolを検証し、保持tableと別の所有tableで同じpathを実証する。

追加followup選択、元SELECTの再実行、SQL rewrite、設定変更、resume、新service/model設定は追加しない。
この増分の終了証拠は診断workflowの完了であり、性能改善や一般的原因究明の成功とは区別する。

## 2026-09-18: completed Reader diagnosis evidence

保持tableのread `e743a83a0d19bcbda85027ea8e67464b`から、diagnosis
`4b43a8bea59facddb11b37242dcc22e0`が成功した。別の新規tableのread
`988b515dae98fcca36266247cbce0a48`から、`22d2040ad8a6f9ed3aeb4146a96a0577`も成功した。
どちらも元SELECT再送0、schema前後・EXPLAIN・history・STATSの5query＋drain1query、
同UUID/schema/head、接続回収後のQwen1回、監査3eventsと保存OTel/Loki相関を照合した。
両履歴は実際に残っており、created-to-endは1123/1182ms、queueは既知の0だった。
planは2fragments/6nodes、一部見積りとtable統計の不足だけを所見として返した。
これらの時間は今回取得した元queryの観測値であり、性能比較の結果ではない。

Qwenのprompt/completion tokensは523/95と524/95、inferenceは4529/3158ms。
modelは既存`qwen3.8-flash-next`、cloud/retryは0、costはNULLのまま保持した。
SQL・行値・table/列名・raw plan/history/model responseはモデル入力に含めない。
通常receiptはtable/列名をidentity照合用に保持するが、SQL・行値・raw plan/history/model responseは保存しない。
再現は[diagnosis runbook](../../../../tools/local_experiment/query_diagnose/README.md)を参照。

実機でTrinoの長さ付き文字列型を受け取れず、最初の2実行は失敗した。
`e782245c783568d49863b5ee83a8e9b8`はEXPLAIN、`b6c34fa67b13f88719f2ea59cdb2eb64`はhistoryで停止。
どちらもmodel0・cleanup確認済みで、failed receiptを変更せず保存した。
最初の捕捉queryへのGET1回はFINISHEDを確認したが、元receiptのunknownは成功に書き換えていない。
二回目は所有historyをdrainでcancelし、query終端を確認した。
Trino 483の[日時変換](https://raw.githubusercontent.com/trinodb/trino/483/core/trino-main/src/main/java/io/trino/operator/scalar/timestamptz/ToIso8601.java)
と[EXPLAIN生成](https://raw.githubusercontent.com/trinodb/trino/483/core/trino-main/src/main/java/io/trino/sql/rewrite/ExplainRewrite.java)
を確認し、診断のEXPLAIN/history/STATSだけstrict `varchar(n)`を正規化した。
Readerの型制約、schema型、phase別byte上限は維持した。historyのprecision3はvarchar(32)に対応する。

新Python76tests（contract25/observer25/owner18/audit8）、既存Reader95、
Go132 top-level＋70subtests・実Python Owner結合6経路・vet/raceが成功。
全local回帰54suites/1947件（1946成功＋既存optional OTel1skip）が通過した。
実障害で確認した型差以外の取消・source改変・失敗注入はoffline回帰の証拠と区別する。
実機記録・最終56source/binary sealは
`tmp/local-experiment/query-diagnose-checks/4c56bd58726d4be7a68822bf9b43a903/`に保存した。

専用fixture `ab8e0e7bd9da481d8ce875386e3b00c3`はUUID
`275df25a-f3a0-4203-9888-f6218fcca806`、snapshot `7664669577755841607`。
CREATE/INSERT各1回の2行・5scalar列をReaderで精度照合し、診断の終了・lock解放・同identityを確認後、
fresh UUID/head/schema照合を経てDROP1回した。保持fixtureの6投影とtable一覧は元に戻った。
失敗2診断のworker5/control3と成功2診断のworker10/control2、計20query IDsを保存した。
query終了の確認は、失敗receiptの応答unknownを成功へ変更する根拠にはしていない。

保存結果の独立検証94checksと、前後環境・監査相関の独立67checks（fresh21＋相関46）が成功。
3712旧files、14Pods/27resources/3Docker/16policy、MLflow identityが不変、5ports空き・43locks非active。
AHは旧470行の全hashを保持して480行へ増え、失敗4＋成功6の10event IDsがLokiと一致した。
前後監査は`query-diagnose-preflight/0ffcec31a20e40f6a92a6bbbb9ebb88c/independent-verification.json`、
保存結果は上記checkdirの`independent-saved-all-466b9876f7394242a52b05244ad85bf4.json`に保存した。
保持table復帰は専用fixture cleanupが得た6投影の照合であり、監査による追加SELECTの結果ではない。

## 2026-09-18: bounded Lakehouse browsing plan

診断の前提となる実table・schema・行を利用者が確認できる入口を追加する。
`query tables --schema smoke|local_experiment`、`query describe --table schema.name`、
`query select --table schema.name --columns id,name --limit 1..100`を固定local profileへ接続する。
既存のQwenを使う`query run/status`は維持し、新しいread commands自体はmodelを呼ばない。

- [x] fresh inventoryで確認したIceberg tableと列だけから、hostが引用済SQLを組み立てる。
- [x] PolarisのUUID/schema/current snapshotとTrino schemaを照合し、SELECTを観測snapshotへ固定する。
- [x] 最大100行・型付き値を明示要求への応答として返す。整数/decimal/snapshot IDの精度を保持する。
- [x] limit+1の取得でtruncatedを区別し、ページ・byte・時間・列数にも上限を設ける。
- [x] 原SQL/行値を既定receiptへ保存せず、型・件数・hash・query identity・終了証拠を記録する。
- [x] 未発見table/列、式・任意SQL・複合型・schema/head変更・不明終了を成功として扱わない。
- [x] 取消・切断時にPOSTを再送せず、所有queryだけを終了確認し、保持fixture/環境不変を照合する。
- [x] 保持fixtureと別の新規所有tableで同一コードを実証し、Go/純粋contract/transport/ownerを検証する。

catalogはiceberg、schemaは上記2個に固定する。一般SQL、式、filter/join、書込み、認可一般化、
新service/package/model設定、保持tableの変更は追加しない。存在しないtable/列の判定には
発見用queryが必要であり、構文不正のPOST0と混同しない。orderingは未指定と明示する。

## 2026-09-18: bounded Lakehouse reader evidence

Go CLIから実Trino/Polarisへ接続し、2schemaの一覧、保持tableと別の所有tableの
describe/select/limit1、未発見table/列の拒否を確認した。10実行はすべてquery終了・
接続回収済み。不正な列式はchild起動・query・evidence割当の前に拒否した。
保持`smoke.fixture_v1`のUUID/current snapshot/schema/2行は不変である。

専用fixture `930449df6e38434185609d5c26063db4`では、BIGINTの2^53超と最小値、
DECIMAL(38,6)の38桁、Unicode日本語、NULL、DATE、BOOLEANを厳密照合した。
UUIDは`7694c343-4c25-4a2e-b8dc-16f336df5970`、snapshotは`2490578739455839616`。
describe `7439b55d46088b6dd546b877926410b2`、select `79cd344264406716a517d833b5b3f7cf`、
limit1 `237b37a4190258b517b3b8c671b9ece7`が成功。limit1は取得2/返却1/truncated=trueを返す。
CREATE/INSERT各1回の新tableだけを同UUID確認後にDROP1回し、保持fixtureの6投影とtable一覧が復元した。
prepare/cleanupのquery identityは別であり、旧terminal queryを新処理のqueryへ取り込まない。

private検証scriptは初回保持selectのid型をintegerと誤想定したためfailedを残した。
既存fixture契約と実metadataはbigintで一致しており、保存結果だけの7checksで訂正した。
製品CLIは初回からexit0、元SELECTの再送・製品code修正は0である。
実行記録と41source/binary sealは
`tmp/local-experiment/query-read-checks/09e90ea75a6149a3a109d37b06e4ab79/`に保存した。
通常receiptはrows/SQL/credentialsを含めず、この専用検証だけ明示stdoutをprivate fileへ保存した。

新Python95tests（contract32/client36/owner27）、Goの実Python owner結合とscalar照合、
全local回帰53suites/1871件（1870成功・既存optional OTel1skip）が成功した。
取消中の二次保存失敗、終端保存失敗、source/schema/head変更、未知POST、型・サイズ上限は
offline回帰で確認し、実機で故障注入したとは主張しない。独立review指摘はすべて修正済み。
再現方法は[Reader runbook](../../../../tools/local_experiment/query_read/README.md)を参照。

独立監査`query-read-preflight/5a085f17eb6e4a9d840d2d023f047dce/independent-verification.json`
は122checks成功（fresh保持20＋保存相関102）。3590旧files、14Pods/27resources/3Docker、
16policy、MLflow、NP/Serviceが不変。Readerのworker27/control10の37query IDsはすべて別で、
8成功＋2発見後拒否、SQL hash、drain/cleanup、同UUIDのfixture回収を照合した。
AH470やMetadata等のdeep確認は先行seal継承であり、この監査のfresh SQL/API確認ではない。
設計83documents/14tests、secret scan、diff checkも成功した。

一般SQL、filter/join、tenant認可、並行writer保証、性能改善、本番受入は未達としてlocal-partialを維持。
次はこの成功selectの保存証拠から元SQLを再送せず、限定EXPLAIN/historyとQwen診断へ接続する。

## 2026-09-17: product CLI plan

既存の固定2SELECT診断を`shirokuma query run --case retained-fixture`へ接続する。
`query status TOKEN`は保存済み証跡だけを読む。新たな常駐serviceや汎用SQL入口は追加しない。

- [x] Goから固定Python bridgeを起動し、Data CLIの既存取消transportを変更せず再利用する。
- [x] early allocation、durable intent、workflow呼出し、terminal resultを区別する。
- [x] 証跡directory tokenとTrino/query用の内部tokenを分離する。
- [x] 既存legacy証跡も照会でき、初期failed receiptをterminal failureと誤認しない。
- [x] 保存statusがmodel/API/writeを行わず、欠損・改変・取消・protocol不整合を扱うことを確認する。
- [x] 製品CLIからQwenを1回呼び出し、固定観測・監査・cleanupと保持データ不変を実証する。
- [x] 全local回帰・独立review・関連Obsidianノートを更新しlocal commitする。

status成功は保存状態の読取成功であり、診断成功やfresh runtime確認と同義ではない。
原SQL・plan・prompt・応答・例外本文をCLI出力へ転記しない。
取消時はownerだけにSIGINTを送りcleanupを待ち、終了不明ならtoken/PIDとunknownを保持する。
自動再実行/resume/SIGKILLは追加しない。root causeと改善効果の未確定は継続する。

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
You are working on Shirokuma WP WP-L2-AGENT-005: Query Doctor Bear.
Read AGENTS.md, related ADRs, and this Work Package.
Use ADR-0034 local commits and bounded read-only Trino diagnostics.
Preserve unknown estimates and do not claim measured performance improvement.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-17 local bounded implementation plan

保持fixtureの2固定SELECTを各1回実行し、独立した期待id集合と照合する。
同じSQLのJSON EXPLAINと、その実行で捕捉したquery ID・専用userのhistoryだけを観測する。
Qwenは追加観測として固定SHOW STATSまたは自身のquery historyだけを選ぶ。
任意SQL、ANALYZE、SET、DML、他userの履歴、設定変更、索引・データ配置変更は実行しない。

事前確認ではSELECT idがOutput→TableScan、WHERE id=1がOutput→ScanFilterとなり、
filtered planのroot estimateには文字列NaNが含まれた。複数estimatesを合算せず不明を保持する。
system.runtime.queriesにはCPU/processedRows/bytes/elapsed列が無い。
EXPLAIN自身の時間を対象SELECTの実行時間へ置換せず、異なる結果意味のquery間で速さを比較しない。
2行fixtureで性能改善や遅さ、pushdown失敗の原因を断定しない。

- [x] 2固定SELECTと同一SQLのplan、own query ID/userのhistoryを照合し、原文をモデルへ渡さない。
- [x] NaN、未知node/schema、複数estimate、欠測履歴、他query混入を明示的に扱う。
- [x] モデルは固定の追加readだけを選び、根拠IDと必要観測をgolden casesで照合する。
- [x] 実Qwen→追加read→結果照合→監査まで実環境で確認する。
- [x] 元fixtureが不変で、自身のqueryだけを終了・回収したことを確認する。
- [x] 実行証跡・制約・未解決の改善課題をRunbookとVaultへ記録する。

本段階のassessmentは限定観測または追加根拠必要、root causeはundetermined、
performance gainはunmeasuredとする。具体的な高速化変更・性能保証は別の実測課題として残す。
追加resident serviceやCloud APIは不要。停止は新entrypointの利用停止で、保持データを変更しない。

## 2026-09-17 local functional evidence

8 golden case×2の実Qwen評価は16/16、16監査readbackが成功した。
入力8022/出力586 tokens、推論合計27.881秒。
`tmp/local-experiment/query-evaluation/90af6c7220a241a4b6521be414f36439/`。

実workflowはQwenがfiltered planの推定欠測を根拠に`fixture_stats`を選択した。
入力491/出力36 tokens、推論1.696秒。固定2SELECTの結果は2行/1行と独立期待値に一致。
SHOW STATSでrow count estimate=2を取得したが、列統計6項目のunknownは残した。
root cause=undetermined、performance gain=unmeasuredを維持し、改善成功とは扱わない。

ClickHouse/Loki/保存OTelの4event相関、完了checkpoint、captured4 queryのhistory全terminal、
全6requestのresponse完了・cleanup verifiedを確認。
証跡は`tmp/local-experiment/query-bear/6aae039137f748548db53b59f8e2d3b7/`。
04:20 UTCの独立検証で保存観測からmodel evidence/assessmentを再計算し一致。
run user `shirokuma-query-doctor-7a73e325b46c4e75bd77e8b824c8fec1`の非terminal query残留0、
元fixture2行不変を確認した。`independent-verification.json`に保持する。

Query37 tests（observer18、workflow/provider19）に加え、評価harness14 testsを含む
全回帰455 tests・設計73 documentsが成功した。推論途中cancelでも失敗Pawprintとunknown usageを残し、
追加readへ進まない。同期HTTPとfinally receiptによりtunnel外へquery workerを残さない。
request timeoutや中断時に未確認終了を成功と扱わず、serverの30秒run上限と
captured/uncaptured request状態を記録する。API DELETEや広いquery killは行わない。

再実行は`make local-query-bear-eval local-query-bear-smoke`。
[Runbook](../../../../tools/local_experiment/bears/QUERY_DOCTOR.md)に測定境界と再実行を記録。
一般的な遅延原因の特定、query rewrite、実性能改善、本番query品質は未達として保持する。

## 2026-09-17: Query Doctor product CLI

`shirokuma query run --case retained-fixture`と`query status TOKEN`を追加した。
既存Qwen・固定2SELECT/同SQLのEXPLAIN/自身のhistory・最大2種のfollowupを使う。
GoはDataの既存owner取消transportを変更せず再利用し、厳密なJSONLとchild exitを照合する。
early allocationは永続intentや実行開始とは別で、出力はallowlistされた固定summaryのみ。
取消はownerへのSIGINTと最大240秒のcleanup待機、未確定はtoken/PID付きunknownとする。
SIGKILL・自動replay・resume・任意SQL入口は追加していない。

Pythonは`query-cli/<token>`へ一度だけのinvoke intent/lock/hash seal、
`query-bear/<token>`へ既存workflowの証跡を分けた。directoryのevidence_tokenと
内部query/user tokenは別のまま保持する。statusはrunner/modelをimportせず保存内容を読む。
旧成功証跡も検証できるが、新hash sealやfresh runtime観測へ読み替えない。
initial failed receiptはclosed root span・observer・保存内容が揃うまでterminal failureにしない。
graph auditやobserver保存の二次失敗が元の取消を置換する経路も修正し、実body fakeで回帰した。

実CLI成功は `query-bear/20e5c3b825be6c74e58b0ce8ab88a361/`、
内部tokenは `9de355caddb74fd299fd7b7711acdb3c`、traceは `0bd4e45890d24dc76376cade8399ab7b`。
Qwenを1回呼び、入力491/出力36tokens、2682ms、cost NULL・cloud0・retry0。
不足estimateのf01に対してfixture_statsだけを選択した。row estimate2、column stats未知6件を保持し、
root_cause=undetermined、performance_gain=unmeasuredとした。6requestsはすべてFINISHEDで
response_complete、cleanup_verified=true。AgentHouse/Lokiの4監査eventと保存OTel4spanが相関した。

初回 `b0ca236012e18f8c4a1728a5022e22f4` はモデル前observeで失敗し、失敗のまま保全した。
固定5queryは完了済みで、observe/assessの失敗監査2件・model0。CLIはexit1、
保存statusはexit0で診断failedを維持し、状態読取成功と診断成功を混同しなかった。
元4query IDと専用userに限定したhistory1回で、実started_to_end=1011msに対し
検証側のfloat式が1010msへ丸める原因を確定した。SELECT/EXPLAINを再送せず、
新規固定観測5readsと限定history1readだけで調査し、どちらもmodel0・tunnel終了。
query_contractのdurationをobserverと同じ整数演算へ修正し、1001/1003/1011ms、subms端数、
日跨ぎ・改ざん拒否を回帰した。元保存historyの全8durationも修正版で一致した。
診断は `query-cli-diagnostics/e50551a64ae94f33b7afa906e8bb59fb/`へ保存した。

旧成功・今回失敗・今回成功を各3回、計9回status照会した。対象27files/directoriesの
hash/mode/mtimeは不変。model/API/write0は隔離statusテストと固定codepathで確認した。
新Python29tests、Query Go16testsを追加し、全Go114件とvet/raceが成功。
全local回帰47suites/1554件（1553成功・既存optional OTel1skip）が成功した。
実行・build/source hash・status比較・全logは
`tmp/local-experiment/query-cli-checks/c88902c4e4b844c3bc2e7d38845fc0c2/`、独立監査は
`query-cli-preflight/831f61fbef894f3b8854e4aadd038798/`へ保存した。

2行fixtureの観測と製品入口までのlocal-partialであり、一般SQLの根本原因・性能改善・
本番受入を主張しない。次は既存4観測源のSRE Bearを製品CLI run/statusへ接続する。

独立監査 `query-cli-preflight/831f61fbef894f3b8854e4aadd038798/independent-verification.json`
は78checks成功。AH440→446の増分は失敗2＋成功4だけで、旧440件は不変。
Loki2＋4、保存span2＋4、実Qwen1回、固定6requests、整数duration8値、未知stats6を照合した。
保持旧3288files、Kube14Pods/27resources/6CM/3SecretUID、Docker3/images553、
history/fixture全履歴、Metadata35endpoints/7plans14tables14edges、MLflow59tables/
15runs1210points15artifactsとbackupが不変だった。初回seal10filesと診断原証跡も保持した。
監査tunnel終了、design83docs/14tests・secret scan・diff check成功。
Query WP/MilestonesはVaultと一致し、MOD-051の既存言語差は維持して追記だけを揃えた。
