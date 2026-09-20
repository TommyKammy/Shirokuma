---
project: Shirokuma
doc_id: "WP-L4-AGENT-001"
title: "WP-L4-AGENT-001 Notebook to Dagster Agent"
status: local-partial
created: 2026-07-05
updated: 2026-09-20
version: "0.8"
area: "workpackage"
tags: [shirokuma, workpackage, l4, agentops]
---

# WP-L4-AGENT-001 Notebook to Dagster Agent

## Summary

登録済みNotebookから保存学習source検証→固定autologの2assetsを実行し、初回記録と同tokenの追加fitなし再利用が成功した。既存previewと一般Notebook変換の未達は維持する。
schema-only Qwenから既存snapshot Countへ接続する限定増分は、修正後5caseで期待どおりとなった。
Notebookを再現可能なDagster assetへ接続する。ADR-0034のlocal実験では、登録済みの固定Notebookだけを
hostの定型2assetsへ対応付ける最小部分を実装し、実2回と独立最終照合が成功した。
選択tableの登録previewもschema2の2assetsへ接続し、実1回と独立保存照合が成功した。
さらに自然文から検証済みselectionを作る限定Assistを接続し、実5caseが期待どおりとなった。
型付き単一等値条件の自然文previewも実3caseと独立保存照合で確認した。
任意Notebookの解析・生成や一般的なLLM変換は含めない。

## 2026-09-20: saved training to Dagster autolog: local acceptance complete

`notebook-autolog-assets-v1` は登録済み `local_autolog_assets.ipynb` を `saved_training_source` → `ridge_autolog` に対応付ける。前段で保存学習のsourceを検証し、そのSHAを後段の固定workerへ渡す。新規記録は停止store・一致するcold backupを必要とし、48train行だけの既存autologを専用ML環境で1回実行する。

完成済みの同tokenは保存receiptを検証して再利用し、追加fit・SDK write・新backup作成は0。caller指定の外部manifestは読まないが、保存内backup receipt/HTML/sourceは検証する。過去の未閉鎖workerは全attemptの新規呼出しを拒否する。既存preview・依存・常駐サービス・Lab seedは変更しない。初回materializationは6.360秒で成功し、同tokenの2回目は1.018秒で同じMLflow runを再利用した。両回で2assetsのmaterializationとworker cleanupを確認した。非稼働cloneへの復元・SDK読戻しと、元データ保持・最終容量の照合も成功した。

なお、任意Notebook変換・自律的な本番配備は対象外。

操作は[Notebook assets runbook](../../../../tools/local_experiment/notebook_assets/README.md#materialize-saved-training-and-autolog)、結果は[local milestones](../061_Local_Experiment_Milestones.md)へ集約する。

## 2026-09-20: natural-language snapshot count — local acceptance complete

`local-notebook-assist-count`（`--count-request-stdin`）はexact `{table,request}`から、describe由来のschema-only Qwen1callで全件または型付き単一等値COUNTを提案する。proposalは`{where,unsupported}`、hostがtableとcurrent snapshotを指定し、既存Count契約/所有Readerへ渡す。整数/bigint/decimal/date/boolean等値だけを対象とし、range・複数条件・groupby・他aggregate・NULL/varchar/float条件・過去snapshotを扱わない。
Assist schema3/profile`natural-language-notebook-count-v1`で旧preview/filterを維持する。COUNTは定型assetsや保存ML評価を実行せず、独立したdescribe/model/count/assessmentの監査actionを使う。describeとCOUNTは別時点で、head更新を許しUUID/schema/条件型を照合する。
明示unsupported/未検証proposalはCOUNTへ進めず、送信後unknownを未実行や0件と断定しない。正確な0件は成功結果。初回のruntime開始前失敗は原本を保持した別closureとし、Go KUBECONFIG継承・監査target修正後に全件2/等値1/等値0が成功した。範囲/未知列はunsupportedでCOUNT0となり、全5caseは3passed/2unsupported。初回失敗込みQwen6calls、期待22監査eventの帰属と既存データ・証跡の保持も確認した。
一般自然文品質や任意Notebook→assets変換の完了は主張しない。[Count契約](../../../../tools/local_experiment/query_count/README.md)のtimeoutと全走査し得る境界を維持する。操作は[Assist runbook](../../../../tools/local_experiment/notebook_assist/README.md)、結果は[Milestones](../061_Local_Experiment_Milestones.md)へ記録する。

## 2026-09-20: natural-language typed equality preview — local acceptance complete

`local-notebook-assist-filtered`（`--filtered-request-stdin`）を追加した。exact `{table,request}`を受け、
describe由来の列名/型と依頼だけをQwenへ1回渡し、明示列・limit・単一等値のproposalをhostで検証する。
integer/bigint/decimal/date/booleanだけを対象とし、varchar/float/NULL・範囲・複数条件を許可しない。
6key Reader request（`snapshot_id:null`）から固定`filtered_table_preview`→`saved_evaluation_summary`を実行する。
Assist schema2/profile `natural-language-filtered-notebook-preview-v2`、assets schema3/profile
`filtered-table-notebook-assets-v3`で区別し、監査actionと旧v1の契約は維持する。
head更新は許容し、describeとSELECTの最終UUID/schemaを照合する。生成code・kernel実行、新依存・常駐追加は0。

実3caseは等値previewがpassed、範囲/契約上書き依頼がunsupported（SELECT/assets0）となり、全cleanupを確認した。
acceptedはbigint `id=1`の`[name,id]` 1行と2assets/保存評価12件を取得した。
外側検証helperのinteger誤期待は失敗記録を残し、保存出力だけで再照合した。製品・モデル・Readerの再実行はない。
新48testsを確認し、全回帰2930実行に登録補完target14実行を加え2944実行（2943PASS・既存1skip）が成功した。
独立保存照合251assertions/72inputsが成功した。helperの期待値誤り2件を訂正し、元logを保持した。

literalは依頼/保存request/Reader引数/Trino SQLに残り、監査payloadには載せない。
失敗/取消/unknownの証拠と保持データ・MLflow・backupを残し、自動retryや削除を行わない。
一般的な自然文理解・override耐性、任意Notebook変換、Hub/複数ユーザー・本番受入を示すものではない。

操作：[Assist runbook](../../../../tools/local_experiment/notebook_assist/README.md)、
[型付きReader境界](../../../../tools/local_experiment/query_filter/README.md)。
実測・保持・証跡は[Milestones](../061_Local_Experiment_Milestones.md)の同日最新節に記録する。

## 2026-09-20: natural-language Notebook preview — local acceptance complete

`make local-notebook-assist`はexact JSON `{table,request}`をstdinで受け、明示tableをdescribeしてから、
Qwenの1回の提案を既存selectionへ渡し、登録previewの2assetsを実行する。全体4096B、依頼512文字/
UTF-8 2048B、明示した1–16列と整数limit1–100だけを対象とし、モデルはSQL/codeを生成しない。
モデルに渡すのはtable/request/columns{name,type}だけで、行・UUID・snapshot/head・hashは渡さない。
proposalはexact columns/limit/unsupported。型・既知列・上限をhostで検証するが、自然文の意味が
常に正しく解釈されたという保証はしない。既存Data Engineerの固定3変換とは独立したread入口である。

accepted時だけ`table_preview`→`saved_evaluation_summary`をmaterializeする。unsupportedはexit2、
SELECT/assets/MLflow評価読取0だが、describeとQwenは実行済みである。describeとSELECTは別時点で、
current headの更新を許し、最終table UUID/schema SHAを一致確認する。snapshot固定transactionではない。
生成Python/任意Notebookの実行、kernel起動、データ/catalog/MLflowへの書込みは追加しない。
旧Notebook/Assets入口と登録templateを保ち、2assetsの依存は実行順だけで評価由来のlineageとはしない。

実5caseをそれぞれ1回逐次実行し、期待どおりとなった。

| Case / token | 時間 | 結果 |
|---|---:|---|
| 明示name,id/1件 `1cdcb3db503849aa97d7df7df59a727b` | 35.124秒 | passed/exit0、2assets、returned1/fetched2/truncated true |
| filter `293e038ad96f4f218059d572ab06236f` | 16.129秒 | unsupported/exit2、SELECT/assets0 |
| 未知列 `d6ba248034f849a29ba70a12393e8d7e` | 15.531秒 | unsupported/exit2、SELECT/assets0 |
| 曖昧 `5de46db8487f449fac806128ad986d29` | 14.978秒 | unsupported/exit2、SELECT/assets0 |
| SQL要求 `7ca4e594cedb4a16ad6127873a8fa7cd` | 15.648秒 | unsupported/exit2、SELECT/assets0 |

全caseでdescribe/owner cleanup true、model各1call・retry/cloud0。acceptedは既存fixtureの
[name,id]/limit1から合法な1行subsetを読み、保存評価12runを表示、benchmark4runを除外した。
5responseのusage合計は入力1681/出力72tokens、推論duration合計6044ms、costは全件NULL。
不正bool requestは0.057秒でINPUT_INVALID/exit1・evidence NULLとしてallocation前に拒否した。
この5caseの観測を一般的な自然文品質や任意Notebook自動変換の保証へ拡大しない。

private `notebook-assist/<token>/`にinput/context/parsed model response、describe/とaccepted assets/を保存する。
入力やresponseの本文は監査へ流さず、固定action/status/相関/既知またはNULLのusageだけを保存する。
accepted4events、unsupported各3eventsをAgentHouse/Loki/local OTelへ照合し、AHは保持519行の全hashを
保って新16eventsだけ増加し535行となった。正常unsupportedのblocked auditと不正応答/失敗/取消を区別する。
保存失敗後も既知usageを保持し、最初の取消、業務summaryとowner終端、unknown cleanupを維持する。
SDK1.44.0と必須semantic-conventions0.65b0だけを専用DataOps環境へ追加し、旧96 lock versionsを維持した。
MCP/LangGraphや新常駐serviceを追加せず、global tracerも設定しない。

新80tests（contract/provider18・runtime28・audit18・describe client8・strict parser8）と、
全local73suites/2583tests（2582PASS・既存optional1skip、60.169秒）が成功した。
65sources/527保持files・MLflow59tables/16runs/1320points/16artifactsは前後一致した。
独立保存670checks（143意味検査＋527保持files）/693inputsが初回成功。16監査とlocal spans、
2materializations/12評価、6Reader owner PID終了、25distinct terminal queriesを照合した。
追加API/SDK/model/Reader実行0。モデルbodyは封印source/configと保存contextからの再構成で、
HTTP捕捉ではない。保存監査・cleanup・保持guardの検証を現在のserver再観測とはしない。
独立proof SHA256は`d271c5bfa6e0a6aed67180946d03b11a7b9bd020040d3a2057c0891639187800`。
証拠は`tmp/local-experiment/notebook-assist-checks/b0398f85421f4100a8737a6d05c12ff5/`。
`before.json` SHA256は`5caef43881b0ab39dfcaf972f81fa03f25bc2ec56daede5474cd77b520c63fd6`、
`after.json` SHA256は`75d670ea4da08ce4842a58df625b38b2365e99260c8f2db949564eb8a5340d8b`。
5新runは77files・51,409,880B（実割当51,728,384B）、dir0700/files0600/Go binary0700。
停止時は新規実行を止め所有Reader終了を確認し、保持table/MLflow/backupと今回証拠を残す。
[Notebook Assist runbook](../../../../tools/local_experiment/notebook_assist/README.md)に操作と境界を記載する。
一般Notebook変換・Hub・複数ユーザー・直接PySpark・training/autolog・本番受入は未達として維持する。

## 2026-09-20: selected-table Notebook to assets — local acceptance complete

`local-notebook-assets-preview`へexact Reader v1 request（command/table/columns/limit）をstdinで渡し、
登録`examples/local_table_preview.ipynb`の定型2assetsを実行する。4096B・current head限定、
columns順序とlimit1–100を検証し、未知入力はallocation/build/read前に拒否する。
既存DataOps環境を直接使い、依存同期・新service・kernel起動を追加しない。
元の無指定profile/schema1/生成defs bytesを維持し、新profileはschema2の
`selected-table-notebook-assets-v2`とする。

`table_preview`は既存`read_select(request)`を1回呼び、table/columns/requested_limitを照合する。
続く`saved_evaluation_summary`は保存評価12runを読み、benchmark4runを除外する。
この依存は実行順だけを表し、選択行から評価を生成したというlineageにはしない。
private canonical `request.json`のSHAをmanifest/intent/asset metadata/result/ownerへ束縛し、
生成`defs.py`は`definitions_from_export(__file__, expected_sha)`だけを呼ぶ。
request値をPythonへ埋め込まず、runnerは生成物を動的importせずhost定義を使う。
実行前後・取消時もrequest/exportを照合し、環境設定途中の取消を含めて元envを復元する。
業務結果とowner終端を区別し、retry0・既存Reader cleanup/取消猶予を維持する。

実run `5b50cb221dcb4bcfb267c3fa205fa4a5`は20.064秒、owner/business passed・cleanup true。
保持`smoke.fixture_v1`の[name,id]・limit1からreturned1/fetched2/truncated trueを確認した。
SQLに行順保証はなく、2行のうち合法な1行subsetであることを照合した。
同snapshot `7141845066324476177`、選択行hash
`87783371544aa96b0a70f4bfea8c909a48ab3da442c88bcc06baea6e9528fb56`、
request SHA `eff87a349d77f9a21e9e3cfdb8e214521f78bf8abf0f65877373d546e8df6a85`を保持する。
新Readerは`9253036c8456cde6cfb0c186548bf88d`、Dagster runは
`6f671d00-0bdb-498f-9e0c-079224647a3c`で1SUCCESS/2materializations・retry/failure0。
新fixtureは作らず、今回の実materialization失敗は0、model/kernel呼出しも0だった。
不正bool limitは0.046秒でNOTEBOOK_REQUEST_INVALID/exit1として拒否した。

35focused（新16/既存19、実Dagster＋fake client計3cases）と、全local70suites/2503tests
（2502PASS・既存optional1skip、60.303秒）が成功した。requested_limit照合と環境設定中取消の
レビュー指摘は受入前に修正済み。Go製品変更はなく、前増分のGo race数を今回の回帰へ加算しない。
64sources/505保持files、MLflow59tables・16runs/1320points/16artifacts、AgentHouse519は前後一致。
独立保存533checks（28意味検査＋505保持files）/590inputsも初回成功した。
SQLiteをread-only/immutable/query_onlyで読み、2materializationsのmetadata、5distinct terminal
Reader query IDs、Go owner終了、選択行/評価射影/cleanupを照合した。独立検証の追加API/model0。

証拠は`tmp/local-experiment/notebook-assets-selection-checks/b9a9c4d7271d494fbb7c645c78dde93b/`。
`before.json` SHA256は`d01ebda98c23d1490e85a0a224af39b650a750cd0a645ba5b543bdf2565905d6`、
`after.json` SHA256は`ca3592c2370cd074e1025892282a2d3f135d9b1c7fd4b3060a47dcbcf013a158`。
独立proof SHA256は`f791711346342a1c2d36475d6ec90e2c234e2724a88f63ec0250714265db5b05`。
新assets runは20files・10,674,195B（実割当10,809,344B）、dir0700/files0600/binary0700。
停止時は所有Reader終了を確認して証拠を残す。保持table/MLflow/backupを削除する復旧は不要。
一般Notebook変換・LLM変換・Hub・複数ユーザー・training/autolog・本番受入は未達として維持する。
[assets runbook](../../../../tools/local_experiment/notebook_assets/README.md)に新入口と既存境界を記載する。

## 2026-09-18: fixed Notebook to two assets — local acceptance complete

対象は`examples/local_lakehouse.ipynb`だけとし、固定SHA256
`186aa3b75ef02c1efd963f1df02ed2797c5dd77a07a0856dbcc7b603d8fd3ef4`、3code cellsの内容、
空outputs・execution_count NULLを照合する。任意のNotebook pathやSQL、promptを入口にしない。
`tools/local_experiment/notebook_assets/run.py`はhostに定義した定型`defs.py`と`manifest.json`を
新規private directoryへ生成し、実行前後にtemplate/client/converter/definitionsのhashを照合する。
生成Pythonは確認可能な成果物であり、runnerが動的importして実行する経路にはしない。

固定assetは次の二つに限定する。

| Asset | 固定read | 結果metadata |
|---|---|---|
| `fixture_preview` | 既存Notebook clientのReader1回、`smoke.fixture_v1`のid/name・limit10 | 行数・型付き行hash・snapshot ID・private artifact参照 |
| `saved_evaluation_summary` | 同clientのloopback MLflow評価experiment読取 | 評価run数・safe projection hash・cost NULL・private artifact参照 |

`fixture_preview`成功後に`saved_evaluation_summary`を実行する依存は、Notebookの実行順を保つためだけ。
既存評価がfixture行から生成されたというdata lineageや、評価の再計算を主張しない。
MLflowの現12評価runを扱い、別experimentの4benchmark runは混ぜない。
raw行は既存Readerのprivate stdout、評価一覧はprivate `evaluations.json`に保持し、
Dagster materialization metadataは件数/hash/snapshotと固定参照へ限定する。

既存`tools/local_experiment/dataops/.venv`のDagster 1.13.22を使い、新依存は追加しない。
各runは新しいprivate `DagsterInstance`でin-process materializeし、telemetryを無効にする。
2assetsともretry policyは0、host側も各readの2回目を拒否する。Notebook kernel、Hub、scheduler、
常駐Dagster serviceや既存instanceへの書込みは追加しない。生成物・Dagster event store・Reader証跡・
結果は`tmp/local-experiment/notebook-assets/<token>/`の新規private領域にだけ置く。

Readerの240秒待機/245秒cleanupと最初の取消object保持を同clientから継承する。
不明なReader終了はunknownのまま残し、元SELECTの自動再送、forced kill、保持データ削除は行わない。
外側の取消猶予は少なくとも260秒を確保する。データ/catalog/MLflow変更、新model呼出しは0。
materializationの業務結果とownerの終端結果は別に保存し、業務成功後の取消をowner成功へ変換しない。
今回のlocal-partialは登録templateと定型host実装の対応付けであり、一般的なNotebook解析や
自動変換Agentの品質、JupyterHub・training/autolog・本番受入の完了を意味しない。

## 実行結果と検証範囲

| Asset run token | 経過時間 | 保存結果 |
|---|---|---|
| `feac569a1b4941929506cb53c36a14c2` | 17.176秒 | 2assets・業務/ownerともpassed・Reader cleanup true |
| `e4a5a35f7dc64c089802f44e3b0b746b` | 16.977秒 | 2assets・業務/ownerともpassed・Reader cleanup true |

各runでReader1回と固定MLflow読取を実行した。fixtureは2行、snapshot IDは両方
`7141845066324476177`、型付き行hashは
`086c3e021732eb73e24f56cd6c3289ea794088989e2cf7ee9bcfd993a32ede1c`。
12評価runの射影hashは両方
`32e7373ce268467912e9db401826e6e154967c5e6b16b7f1a9504e400efb8d38`で、4benchmark runを除外した。
独立SQLite/PID/最終証拠照合も完了した。
2回は明示的な別runであり、自動retryや性能改善の測定としては扱わない。

19focused testsが成功し、うち2件は実Dagster engineとfake clientsによる成功/第2asset失敗の検証。
全回帰は63suites/2316tests（2315PASS＋既存optional1skip）だった。
runtime前後で69sources、先行Notebook42files、MLflow59tablesの全行・16artifacts/indexが一致し、
16runs/1320 metric pointsを保持した。Notebook kernel/Hub/daemon/model呼出しとデータ・MLflow書込みは0。
証拠は`tmp/local-experiment/notebook-assets-checks/7f7dd020de634a0d930dbe6b756850eb/`。
`before.json` SHA256は`0b863ce15109f252b9bb01a7378963b55ac94f9362147431e856d6f97eee5a93`、
`after.json` SHA256は`2dd33b76ec738bf5d1a1d5caf3618b0cb7467e78f2635cbd2a0de28e48e794a6`。
独立検証は42checks/144inputsで成功。各Dagster SQLiteのrunDB/indexDBで1SUCCESS・2materializationsと
metadataの一致、retry/failure0を確認した。Reader10 unique terminal query IDs、typedrows、12評価runのJSONは
先行Notebookと一致し、source/保存guardと所有Reader2PIDの終了も照合した。
SQLiteはread-only・immutable・query_onlyで読み、追加API/model呼出しは0。
独立proofは`independent-saved-verification.json`、SHA256
`699a9392ee5dd9f54b554d4f5fd92bf1c0e473b9c0fb299705745b123010b8cf`。
実2runは38files・21,364,736Bの実割当（Go binaries/Dagster store込み）。新envは0、保持data/backup削除なし。
利用停止時は所有Readerの終了を確認し、生成証拠・先行Notebook・保持fixture/MLflowを残す。
[固定assets runbook](../../../../tools/local_experiment/notebook_assets/README.md)に操作と失敗時の境界を記載する。

## 今回のlocal受入条件

- [x] exact登録template/hash/code cellsだけを受理し、改変・出力入り・未知入力をread前に拒否する。
- [x] 新規private `defs.py`/manifestを生成し、既存exportの上書きと実行時driftを拒否する。
- [x] 実Dagster in-process実行で固定2assetsが各1回materializeし、Reader1回とMLflow固定読取を照合する。
- [x] materialization metadataと元typed Reader/評価射影の件数・hash・snapshotが一致する。
- [x] 依存は実行順のみと明示し、dataset lineage・新推論・評価再計算を主張しない。
- [x] retry0・取消/失敗/unknown保持・Reader cleanupを確認し、所有外processや既存instanceを操作しない。
- [x] 既存fixture・MLflow run/metrics/artifacts/index・Notebook templateを保持する。
- [x] 独立SQLite/PID/保存証拠の最終照合と設計/Vault同期を完了する。

今回の固定2assetsのlocal受入は完了。既存の
[Notebook WP](WP-L4-NB-001_JupyterHub_bootstrap.md)と
[Tracking WP](WP-L4-ML-001_MLflow_tracking.md)の限定readを再利用する。
元の依存にあるData Engineer/Notebook全体の完了や、次工程の起動をこの計画で認定しない。

## Context

このWork PackageはLevel L4「OSS Databricks再現」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。
以下は元WPの歴史的PR/CI条件であり、ADR-0034のlocal実行gateにはしない。

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

`level:l4`, `area:agentops`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L4-AGENT-001: Notebook to Dagster Agent.
Read AGENTS.md, related ADRs, and this Work Package.
Create a focused PR that implements only this scope.
Include tests, docs, risk notes, and rollback instructions.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。
