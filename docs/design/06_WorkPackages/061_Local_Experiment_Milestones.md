---
project: Shirokuma
doc_id: "WP-LOCAL-MILESTONES"
title: "Local Experiment Milestones"
status: active
created: 2026-09-16
updated: 2026-09-20
version: "3.52"
area: work-packages
tags: [shirokuma, local, evidence]
---

# Local Experiment Milestones

最新の到達点（2026-09-20）：単一ユーザーJupyterLabで保存学習の選択・入力編集・予測・保存/再読込とstop/restartが成功。
型付き単一等値の自然文previewも実3caseと独立保存照合で確認した。
保存学習3件の一覧からtokenを選び、既存batch予測2行へ接続する実受入も成功。
Notebookから所有workerを起動する固定Iceberg学習が初回86.739秒で成功。
保存Icebergモデルのbatch predictionが実22行・初回0.075秒で成功。
保存Iceberg学習のNotebook表示が実kernel初回1.108秒で成功。
完成trainingを含む18runsの非稼働clone復元・SDK読戻しが初回成功。
Iceberg Reader→学習→MLflowの固定実験が修正後82.149秒で成功。
固定合成データの学習→MLflow Tracking実1回が成功。
保存学習のNotebook指標/holdout/残差表示も実kernel初回1.041秒で成功した。
自然文から検証済みselectionを作るNotebook Assistの実5caseと独立照合も成功。
選択tableの登録Notebookを定型Dagster2assetsへ接続する実1回も成功した。
これまでに保存SRE log観測のCLI表示と、新SRE評価のMLflow取込を完了。
Trackingは旧12評価＋4benchmarkと固定合成学習を保持し、Iceberg学習2runsを加えて19runs/1347 metric pointsとなった。
固定Notebookの実kernel2回と独立最終照合62checksも成功した。
登録Notebookを定型Dagster2assetsへ接続する実2回と独立最終照合42checksも成功した。
保持exportの非稼働MLflow clone復元とSDK全履歴読取2回も成功し、稼働中storeを保持した。
current/historical snapshotと型付き等値条件の正確なCount読取4件も成功した。
固定Catalog Readerの実2回で説明/owner/schema/one-hop lineageを読戻し、公開結果の同一性を確認した。
登録Notebookへcurrent-head Reader requestを渡す選択table previewと無指定互換実行も成功した。
Reader4版の保存metadata一覧も追加し、旧成功・失敗とinspectionの成否を分けて表示した。
一般診断・最適化効果・修復品質・本番受入とは区別する。

## 2026-09-20: single-user local training workbench — local acceptance complete

保存学習を自分で選び、予測入力を編集・再実行できる単一ユーザーのJupyterLab入口を追加した。
専用環境とprivate workspace、loopback `127.0.0.1:18560/lab`、private token、固定`shirokuma-local` kernelを使う。
[workbench例](../../../examples/local_training_workbench.ipynb)は既存の一覧/review/predict helperを再利用し、
新fit/Reader/LLM/MLflow書込みを行わない。既存Notebook runnerは別に維持する。
実IABでlogin後、保存3runsから`6df9b9e5a40b4035b7865f8435fdd291`を選び、HTML review/残差SVGを確認した。
`x1=0.5`へ編集した2入力の予測は`[5.254972560326459,6.61365872720864]`で、独立50桁Decimal計算と一致した。
保存/再読込後も入力・出力が保持され、4code cells/error0。UI操作中に一覧cellを1回再実行した。
認証なしAPIは403、kernelspecは`shirokuma-local`だけ、起動時kernel0を確認した。
初回stopはkernel終了後の`LAB_PROCESS_IDENTITY_CHANGED`で中断し、所有stop intentとlogを保持した。
process再観測と停止済kernelの所有確認を修正し、0.852秒で回復停止した。
直後のrestartはTIME_WAITで`LAB_PORT_CONFLICT`となったため、active listenerの拒否を維持したprobeを修正した。
restartは1.710秒で成功し、編集Notebookのbytes/mode/mtimeと再読込cell/実行回数は一致した。
さらに所有2kernelsの通常stopが1.319秒、直後のrestartが1.721秒で成功、kernel0で利用可能となった。
新runtime28testsを含む全回帰は84suites/2972実行（2971PASS・既存1skip、72.309秒）。
最終保持照合は0.261秒で成功し、baselineの31保存filesとAgentHouse545件は一致、最終13sourcesを記録した。
保持MLflowは19runs/1347points/19artifacts。保存例の利用に新fit/Reader/LLM呼出し・MLflow書込みはない。

専用環境はPython3.12.12/arm64・JupyterLab4.6.3の91distributions。
venv166,552KiB、cache182,784KiBを追加し、従来Notebook環境やglobal kernelspecを変更しない。

保存学習3件の一覧・入力編集・予測は今回の固定例で確認した範囲であり、一般的なmodel品質保証ではない。
証跡：`tmp/local-experiment/notebook-lab-checks/88faf4f147504fbbbf9a7185b2f90ada/`。
操作とtokenの扱いは[Lab runbook](../../../tools/local_experiment/notebook_lab/README.md)を参照。
kernelはhostユーザー権限で動く。Hub/複数ユーザー分離やsandbox、一般モデル品質、本番受入は対象外。
workspaceと証跡はstop/restart後も保持し、回復手段として保持学習やbackupを削除しない。

## 2026-09-20: natural-language typed equality preview — local acceptance complete

`local-notebook-assist-filtered`で、schema-only Qwen 1回の提案をhostの型検証に通し、
current snapshotの単一等値条件から定型2assetsへ接続した。旧v1 Assistは維持する。
integer/bigint/decimal/date/booleanだけを対象とし、範囲/複数条件/NULL/文字列・浮動小数条件は対象外。
生成code・kernel実行、新依存・常駐追加はない。操作と詳細境界は[Assist runbook](../../../tools/local_experiment/notebook_assist/README.md)。

| Case | 時間 | 結果 |
|---|---:|---|
| bigint `id=1`、`name,id`を1件 | 36.311秒* | 製品初回passed、1行/2assets/保存評価12件 |
| 範囲 | 16.357秒 | 初回unsupported/exit2、SELECT/assets0 |
| 契約上書き依頼 | 15.188秒 | 初回unsupported/exit2、SELECT/assets0 |

*acceptedの時間は外側検証helperの失敗までを含む。実schemaはbigintだがhelperがintegerを期待したため停止し、
元logを保持して保存出力だけを0.034秒で再照合した。製品・Qwen・Readerを再実行していない。
合計Qwen3call、input1394/output62 tokens、計測inference4723ms、cost null。10auditを保存し全Reader cleanupを確認した。
保持6checksで37sources/822旧files/ML59tables・19runs/1347points/19artifactsとruntimeを保持、
AgentHouse旧535件を変えず期待する新10件だけを追加した（計545件）。
新7個の非重複出力directoryは61files/31,114,905B（allocated31,358,976B、checks領域は別）。

新48testsはclient14/assets14/Assist20。全回帰82suites/2930実行（2929PASS・既存1skip、69.611秒）後、
Makeのassets新target登録漏れを補完し、追加14testsを0.952秒で確認した。合計2944実行＝2943PASS＋1skip。
受入時から製品sourceは不変で、Makeだけがtarget登録分変わった。独立保存照合251assertions/72inputsが0.049秒で成功した。
17terminal queries・4Reader終了証拠・typed行/SQL SHA・2materializationsを照合し、保持はrootのguard証拠を参照した。
独立helperのnullable列/guard keyの誤期待2件は元helper/logを保持して訂正した。製品/API/Readerの追加実行は0。
証跡：`tmp/local-experiment/notebook-assist-filtered-checks/e719c8b8661a42e591fa2e7cb3826615/`。
モデルの一般理解やoverride耐性、Hub、任意Notebook変換、本番受入はこの3caseから主張しない。
失敗/取消/unknownと保持データ・backupを残し、回復のための自動retry・削除を行わない。

## 2026-09-20: saved training inventory — local acceptance complete

`local-training-runs`で固定synthetic/Icebergの保存学習候補を最大128件まで一覧する入口を追加した。
共有saved-review検証済みのtoken・identity・完了時刻・test/baseline metrics・lake provenanceをstdoutへ返し、行/係数は表示しない。
kind→finished_ms降順→token順で、kind対応Notebook reviewやIceberg batch predictionへtokenを渡す。
root不正・不明名・衝突・上限超過・走査中変化はincomplete/空配列/exit1とし、候補数はnull、verified/unavailable数は0にする。
安定した個別候補は、必須result/workload欠損をpartial、保存reviewとして検証できないものをinvalidとしてunavailableへ明示する。
invalidは未完了/失敗も含む分類で、破損の断定ではない。全候補を走査できればcomplete/exit0と件数を返す。

実一覧は初回0.086秒でcomplete、3候補＝3verified/0unavailable（Iceberg2/synthetic1）。
時刻順の先頭Iceberg token `6df9b9e5a40b4035b7865f8435fdd291`を明示選択し、
既存offline予測2行も0.081秒で成功、Decimal独立算術と一致した。一覧/予測の保存SHAと選択identity/provenanceを束縛した。
7sourcesと31保持filesのbytes/mode/mtimeは不変。新13tests（0.356秒）が成功した。
全回帰は81suites/2896 test実行（2895PASS・既存optional1skip、68.703秒）。
同じ新13testsをstdlib単独targetとML専用環境の両方で実行した分を含む。
固定製品経路はHTTP/SDK/fit/Reader/サービス操作・出力fileを追加しない。証跡は検証側が
`tmp/local-experiment/mlflow-training-runs-checks/05e9f543b6df4632842f85ba4289682f/`に保持する。
これは保存学習の一覧であり、一般品質・自動model選定、Registry/Serving、本番受入は対象外。

手順：[saved training inventory](../../../tools/local_experiment/mlflow/README.md#list-saved-training-runs)。

## 2026-09-20: Notebook-triggered Iceberg training — local acceptance complete

`local-notebook-lake-training`（runner `--train-lake`）から固定3cellsの
`examples/local_lake_training.ipynb`で学習を起動した。実行期限と取消cleanup猶予は各600秒。
kernelが専用workerを起動し、親runnerが所有関係を照合して開始を許可した後に、
workerが既存lake pipelineを1回だけ呼ぶ。
workerが固定64行のIceberg fixture→Reader→所有table回収→MLflow停止/cold backup→1fit/run→元server状態復元を行う。
同じ完成attemptの保存review helperがprovenance・3群metrics・16holdout・残差SVGを表示し、この読取はHTTPを使わない。
kernelは既存Notebook環境、workerは既存MLflow環境を使う。依存・常駐service・LLM追加は0、kernel内fit/autologではない。

実Notebook `a074314c6580479ebeb45d0b33802a62`は初回86.739秒でpassed、3cellsと全cleanup true。
source `07c524979e4341649de1297cc51a3c05`、Reader `3ad5d0b8df6b7d1add5da259073c7f75`から、
training attempt `6df9b9e5a40b4035b7865f8435fdd291`、experiment4/run
`1ee733efdf1e428da531f522f2d42c70`を作成した。kernel PID38498の終了を確認した。
保持14checksで37sources/852files、59tablesの旧行・18runs・旧artifactsとAgentHouse535rowsを保持した。
追加は1run/24params/6tags/9metric points/9latest metrics/1artifactで、計19runs/1347points/19artifacts。
同じstoreのserverをhealthyに復元した。新33tests（client15/runner18、0.602秒）が成功した。
全local80suites/2870tests（2869PASS・既存optional1skip、70.443秒）も成功した。
独立保存81checks/114inputs（0.118秒）は初回成功。9指標・16予測/残差、cold backupと所有process終了を照合した。
新学習のworkloadはprovenanceのlake_inputを除く旧11keysと一致した。保持は上記before/after guard証跡を束縛して確認し、
独立検証で追加API/SDK/fit/Reader/kernel/product importは0。kernel/worker/Reader/trackerの4PIDが消失し、
server PID39313の同一storeでのhealthy復帰を確認した。
新5領域（Notebook/source/Reader/ML attempt/artifact）は計80files/12,996,025B（実割当13,164,544B）。
source内のcold backup42files/2,638,481Bを含む合計で、backupを重複加算しない。

取消時はworkerの後片付けを待ってからkernelを閉じ、不明cleanupと失敗証跡を保持して自動再試行しない。
作成済みrun/artifact/cold backupは、後続の表示失敗や利用停止でも削除して戻さない。
一般モデル品質、任意Notebook/モデル学習、JupyterHub/複数ユーザー、Registry/Serving、本番受入は対象外。
詳細証跡は`tmp/local-experiment/notebook-train-checks/6155edd59ccc4214b58c3a6d0156d5b0/`に保持する。

手順：[Notebook-triggered training](../../../tools/local_experiment/notebook/README.md#trigger-fixed-iceberg-training)。

## 2026-09-20: saved Iceberg batch prediction — local acceptance complete

`local-lake-predict`で完成Iceberg学習attemptを選び、新しい特徴量をbatch予測するCLIを追加した。
tokenは`SHIROKUMA_LAKE_TRAINING_TOKEN`、入力はJSON stdinのschema_version1と1–64行のx1/x2/x3。
32KiB・exact keys・有限数値を要求し、bool/重複key/追加keyを拒否する。
共有saved-review検証でreceipt/model/provenanceを照合し、入力順の予測とidentity/source・request/model SHAをstdoutへ返す。
入力不正・source不整合・演算overflowは全体を失敗とし、部分予測を返さない。製品の入力・結果・journal保存は0。

実22行（新入力6＋保存holdout16）は初回0.075秒でpassed（外側harness0.123秒）。
保存係数と入力のDecimal独立計算との差は最大8.88e-16で、16holdoutは保存予測と一致した。
6source hashesと、MLflow store/保存training/markerの28filesのbytes・mode・mtimeは不変だった。
新17tests（predict11/saved reader6、0.263秒）、全local79suites/2837tests
（2836PASS・既存optional1skip、67.537秒）が成功した。
HTTP/SDK/Reader/新fit/LLM/DB query/サービス操作は固定製品経路に含まれず、通信捕捉による測定とは区別する。
private受入証跡は検証側が`tmp/local-experiment/mlflow-predict-checks/1f0b58e727e743b2a8735feb959aa692/`へ保存する。
固定保存モデルの有界batch算術であり、新入力の正解精度、一般モデル品質、Registry/online Serving、本番受入は含めない。

手順：[saved Iceberg prediction](../../../tools/local_experiment/mlflow/README.md#predict-with-saved-iceberg-training)。

## 2026-09-20: saved Iceberg training Notebook review — local acceptance complete

`local-notebook-lake-training-review`で保存Iceberg学習attemptを明示選択し、別登録template
`examples/local_lake_training_review.ipynb`を実行した。`SHIROKUMA_LAKE_TRAINING_TOKEN`には
32桁lowercase hexのML training attempt tokenを渡す。source tokenやMLflow run IDではない。
既存Notebook環境の`--lake-training-token`を使い、固定3code cellsの実行期限は60秒とする。

private receipt/workload/safe artifact内の型付きlake_inputから64行・48train/16holdout・係数・9指標と
provenanceを照合する。所有MLflow experiment4/runへの固定2GETでFINISHED・identity・params/tags/latest metricsを確認する。
table UUID/snapshot/schema SHA/rows SHA/Reader evidence token、3群のmetric表、16holdout行と残差SVGを表示する。
削除済み一時tableや元Reader、外部pipelineの再実行は不要。新Reader/fit/MLflow write/LLM/依存追加は0。
旧synthetic template bytes/public shapeを維持し、親もkernel終了後のpublic結果を保存元へ再束縛する。

実review `2b8906fbc5ee4e258b64accd864d1622`はmake経由の初回1.108秒でpassed、3code cellsが完了した。
保存training attempt `3da0a631a0e549f29aac96f9998da5bb`、experiment4/run
`ae5e1ff478ae4a3493c437009be2d6b6`を選び、profile `lake-training-review-v1`の表示を保存した。
kernel PID29776の終了・connection回収と両cleanup true、Readerなし/evidence token NULLを確認した。
保持検証5checksで12sources/842保持files、MLflow全59tables/18runs/1338points/18artifacts、
AgentHouse535rowsと稼働server PID23897の同一性を確認した。
新29tests（client18/runner11）を含むclient45tests（0.550秒）・runner25tests（0.112秒）が成功した。
全local77suites/2820tests（2819PASS・既存optional1skip、67.325秒）も成功した。
独立保存58checks/871inputs（0.351秒）も初回成功。256型付き数値・48/16分割・正規方程式・9指標・
16予測/残差・public全体とHTML/SVGを照合した。HTML2tables/21rows（見出し2＋指標3＋holdout16）、
SVG16点、外部参照/実行要素なし、3cellsとkernel回収を確認した。追加API/SDK/fit/Reader/product importは0。
2GETは固定された成功経路からの推定であり、通信捕捉による測定ではない。
新10filesは44,560B（実割当73,728B）。データ600/親dir700を維持し、ライブラリ既定の
jupyter-runtime01700とprivate700親内の静的IPython startup/README0644を区別する。

画面pixel QAは未検証。以前のfile URLに対するBrowser policy拒否を迂回せず、保存値・構造検証と区別する。
一般モデル品質、新規入力のbatch prediction、任意Notebookコード、Notebook内学習・autolog、
JupyterHub/複数ユーザー、本番受入は対象外。詳細証跡は
`tmp/local-experiment/notebook-lake-training-checks/daac544b51dd41c8a4ea4480565dd9ca/`に保持する。

手順：[saved Iceberg training review](../../../tools/local_experiment/notebook/README.md#review-saved-iceberg-training)。

## 2026-09-20: completed training clone restore — local acceptance complete

完全cold exportから、既存experiment0/1/2とoptionalの固定合成学習experiment3・Iceberg学習experiment4を
非稼働cloneへ復元した。対象はFINISHEDで、所有experiment/runと整合する完成receipt・workload・safe artifactを持つrun。
保存source hashesは当時の証跡として保持し、current HEADとの一致やlive source pipeline再実行を要求しない。
未参照の失敗journalはinertなexport証拠としてコピーし、参照receipt不正・partial runは拒否する。

experiment/runのartifact URIの2列だけをcloneの所有pathへ変換し、他のschema/全論理行を維持する。
既存`local-mlflow-restore`/`local-mlflow-restore-readback`を使い、専用SDK workerで全metadata・
metric histories・artifact bytesを読戻す。cloneをactivateせず、稼働storeや過去の証跡を置換しない。
新export作成時だけ所有serverを停止・再起動し、完成exportからの復元・読戻し自体はserver停止を要求しない。

新cold export42files/2,636,371Bを7.907秒で保存し、同じstoreをhealthy/PID23897へ戻した。
restore `71b8d03059f24b2ea0594624692027c3`は初回0.164秒でverified、activation_supported false。
SDK readback `bfd76a06c9b548bc9903b3ff526e4a2a`は初回3.335秒でpassed、
5experiments/18runs/1338points/18artifactsの読戻しとclone保存状態を照合した。
export前後・restore後・readback後の各境界で10sources/730保持files、MLflow全59tables/18runs/
1338points/18artifacts、AgentHouse535rowsが一致した。新fit/Reader/LLM/kernel/API呼出しはなく、
SDKはcloneのみを読む。export停止・再起動に伴うruntime health GETは別の操作として記録した。
新20tests（restore12/readback8）と旧47testsの計67testsが3.269秒で成功。
全local76suites/2791tests（2790PASS・既存optional1skip、67.282秒）も成功した。
独立保存23意味checks/888inputs（0.489秒）も成功。59tablesの型付き全行・DDLを照合し、
変更はartifact URIの23値（experiment5＋run18）のみ。2training receipts/model/provenanceと42file exportを保持した。
SDKの全params/tags/full metric histories/artifactsから保存view hashを独立再構築して一致した。
worker24501と旧server19331の終了、新server23897のstart/argv一致も確認した。
追加API/SDK/fit/Reader/product importは0。監査helper初回の集計キー修正は別logに保持し、製品・実復元の変更は0。
復元全体70files/4,188,383B（実割当4,321,280B）はexport/clone/readbackを内包する。
別の元cold backup42files/2,636,371B（実割当2,707,456B）と区別し、内訳を重複合算しない。
全files600/dirs700を確認した。

再現manifestと詳細証跡は
`tmp/local-experiment/mlflow-training-restore-checks/0a4ab297acad46ee9d68982b0a0fce61/`に保持する。
従来の「training復元未対応」という節は当時の境界として以下に保存する。
clone activation、service切替、他host移行、Registry/Serving、本番復旧受入は対象外。
失敗clone・元export・稼働storeを削除して成功に補完しない。

手順：[MLflow restore runbook](../../../tools/local_experiment/mlflow/README.md#restore-an-inactive-local-clone)。

## 2026-09-20: fixed Iceberg training — local acceptance complete

`local-lake-training`で、一時Iceberg tableの64行を既存SELECT Readerから取得し、
返却行を使ってRidgeを1回学習し、専用MLflow experimentへ記録した。
列はrow_id/x1/x2/x3/target、limit100、全64行・非truncated・同一UUID/snapshot/schemaを要求する。
型付き値・rows SHA・Reader evidenceを保存し、row_id整列後のseed1730で48train/16holdoutへ分割する。
alpha1/SVDのfitはtrainだけを使い、3partitionのMAE/RMSE/R²を9points、source/分割/係数/予測を
非実行JSON artifactに保存する。LLM/Notebook kernel/依存・常駐service追加は0。

fixtureの所有identity付き除去・query drain・Reader終了と保持inventoryを確認してsourceを封印する。
所有MLflowを停止して全bytesのcold exportを検証し、排他lock内のfresh workerで記録後、元のserver状態へ戻す。
source/code変化、保存失敗、不明cleanupを成功に補完しない。同じsourceの再試行はexclusive intentで拒否する。
未知SDK write/partial batchは再試行も追加FAILED/KILLED writeもせず、RUNNINGのpartial runを含め証跡を保持する。
最初の取消を保持し、所有process groupの終了未確認時はserverを再起動しない。

初回source `1bbf9a7b0c044e42b18d84a8c92ef3fb`は49.710秒でunknownとなった。
SHOW返却型varchar(438)を固定varchar判定が拒否し、Reader callback/MLflow停止/fit/write前に止まった。
query drainとtunnel終了を確認し、失敗証跡を不変保存した。追加SHOW1回（3.940秒）で原因を確認し修正した。
別recovery-01は25.958秒で成功。保存UUID/markerに一致する旧fixtureだけを除去し、
保持6projectionsと10table inventoryの復元を確認した。初回を成功へ書き換えていない。

修正後の新source `05f5892e679e49df8d964dc86e574ecd`は82.149秒でpassed、全cleanup true。
Reader `971a99232c9768a950841d686f6b0ed9`の64行からtrain48/test16を作り、
training attempt `3da0a631a0e549f29aac96f9998da5bb`、experiment4/run
`ae5e1ff478ae4a3493c437009be2d6b6`をFINISHEDとして保存した。
RMSEはtrain0.142460、holdout0.163043、train平均baseline3.885825（丸め値）。
新1run/9points/24params/6tags/1artifactを追加し、合計18runs/1338points/18artifactsとなった。
旧17runsを含む59tablesの旧行・669files・AgentHouse535rowsを保持し、retention13checksが成功した。
新cold backupは39files/2,594,008Bを保持。同じstoreをhealthy/PID19331で再起動し、
GET2回（0.129秒）でexperiment/run identity・9 latest metrics・paramsを再照合した。
新100tests（3.499秒）、全local76suites/2771tests（2770PASS・既存optional1skip、66.464秒）が成功した。
独立数値432照合/15inputs（0.041秒）とlifecycle43意味checks/801inputs（0.333秒）も初回成功。
Reader64×4値・48/16分割・係数replay/正規方程式・9指標、保存API/SQLite/artifactと旧状態を照合した。
記録された48terminal query IDs、Reader/tracker/旧serverの消失と新server identityを確認した。
追加API/SDK/fit/Reader/product importは0。分割は保存IDsの全64件・非重複を照合し、PRNG独立再生成は対象外。
成功source（cold backup込み）/Reader/ML attempt/artifactの計62filesは12,899,886B（実割当13,017,088B）。
初回失敗source＋別回収は計7files/10,198,912B（実割当10,207,232B）として分けて保持する。
データ600/親dir700、所有Go executableだけ700を確認した。

既存clone restore profileはexperiment0/1/2だけで、固定合成学習や今回のIceberg学習を含むexportの復元は未対応。
全bytes backupの検証を復元受入としない。一般データのモデル品質、任意table/SQL、Hubからの学習、
Registry/Serving、本番受入は別途とする。詳細証跡は
`tmp/local-experiment/lake-training-checks/f864d8a881734779a7d0da2997b4ffa6/`に保持する。

手順：[local Iceberg training](../../../tools/local_experiment/lake_training/README.md)。

## 2026-09-20: saved training Notebook review — local acceptance complete

明示training attempt tokenから、固定登録Notebookで保存学習の3partition指標表、
32holdout行と残差SVGを表示する`local-notebook-training-review`を追加した。
private receipt/workload/artifactと所有store、MLflow experiment/runのGET2回による
FINISHED/identity/params/tags/9 latest metricsを照合し、parentもkernel終了後に保存元へ再束縛する。
固定dataset/split・係数replay・正規方程式・指標の検査は新学習や全履歴API読取とは区別する。
Go build/Reader/fit/LLM/MLflow write/SDKは0、依存追加0。元default/preview profileを維持する。
source/template変化、unsafe path、保存失敗やunknown cleanupを成功へ補完しない。

実review `351ceabb39344dc7a678022579727b0f`は初回1.041秒でpassed、3code cellsが完了した。
保存training attempt `e65bbc1bd3dc4ca3843ca5c502a53ff8`のexperiment3/run
`0c2a0a34683545d3acd580642a9bb30d`を選び、3partition指標・32holdout行と残差SVGを保存した。
kernel PID9124の終了・connection file回収と両cleanup true、Readerなし/evidence token NULLを確認した。
Go build/Reader/新fit/LLM/MLflow write/依存追加は0。前後8sources/653保持filesと
MLflow59tables/17runs/1329points/17artifacts/indexは一致し、元学習記録/backupを保持した。
新41tests（client27/runner14）・既存Notebook53回帰、全local75suites/2671tests
（2670PASS・既存optional1skip、62.182秒）が成功した。
独立保存23意味checks/699inputsも初回成功。保存元から32予測とpublic全体、HTML9指標/32行、
SVG32点の座標/tooltip/尺度、保持状態、kernel/connection回収を照合し、追加API/SDK/fit/kernel/product importは0。
GET2回は固定sourceの成功経路に基づき、HTTP通信の捕捉測定ではない。
新10filesは48,388B（実割当77,824B）。データ600/親dir700を維持し、ライブラリ既定の
jupyter-runtime01700とprivate700親内の静的IPython startup/README0644だけを区別する。
保存HTMLの2tables/37rows（見出し2＋指標3＋holdout32）とSVG32pointsの構造、
外部参照/実行要素なしを確認した。画面の見た目は未検証で、数値・構造検証と区別する。
証拠は`tmp/local-experiment/notebook-training-checks/dadcbad093704ff8862a9cca46b51209/`。
[NB-001](L4/WP-L4-NB-001_JupyterHub_bootstrap.md)、
[ML-001](L4/WP-L4-ML-001_MLflow_tracking.md)と
[レビューrunbook](../../../tools/local_experiment/notebook/README.md#review-saved-training)を参照。
これは保存結果の説明機能で、Notebook学習/Hub/autolog/Registry/Serving/一般品質/本番受入は未達。

## 2026-09-20: fixed supervised training — local acceptance complete

`local-mlflow-train`で固定合成128行/3特徴量をtrain96/holdout32へ分け、Ridgeをtrainだけにfitする。
明示Trackingへ設定・データ/split hash・versions・train/test/baselineの9 metric pointsを保存し、
非実行JSONの係数から予測・正規方程式・指標、SDK全履歴/artifact bytesを読戻す。
停止中storeの共通lock/隔離workerを用い、durable intent後の不明応答・部分書込は再試行しない。
partial runがRUNNINGで残る場合もattempt resultで失敗/取消を維持し、既存artifactを上書きしない。

実初回5.616秒でverified/FINISHED、holdout MAE0.145263/RMSE0.181238/R²0.993176。
train平均baselineのholdout RMSE2.203076との比較は、この固定合成split内の観測に限る。
旧16runs/1320points/16artifactsを保持し、新1run/9points/1artifact→計17/1329/17となった。
書込前cold export36files/2,562,597Bと旧backupを保持し、retention11checksが成功。
同storeのloopback18550再起動6.881秒/healthyとAPI GET2回の9 latest metrics/17params等を確認した。
稼働中preflightは書込前に拒否、実training失敗0。LLM/Trino/Spark/kernel/Agent監査の追加0。
既存NumPy2.5.3/sklearn1.9.1を直接依存へ明示し、lock全92package versions/配布情報と専用環境87installedを維持した。
新47focused tests（workload15/runtime32）と全local74suites/2630tests
（2629PASS・既存optional1skip、62.366秒）、secret scanが成功。
独立保存39checks/85inputsも初回成功し、係数replay/正規方程式/9指標、旧59tablesの全行・
16artifacts/index/cold backup、保存API2応答とSQLite、所有PID同一性を照合した。
追加API/SDK/fit/product importは0で、NumPy PRNGの独立再生成は含めない。
新training3filesは25,276B（実割当28,672B）、file600/dir700で保持する。
証拠は`tmp/local-experiment/mlflow-training-checks/c28faf05befa48d49a3792a01128326f/`。
[ML-001](L4/WP-L4-ML-001_MLflow_tracking.md)と
[学習runbook](../../../tools/local_experiment/mlflow/README.md#fixed-supervised-training)を参照。
local-partialを維持し、一般モデル品質、Notebook/Hub学習、autolog、Registry/Serving、本番受入へ広げない。

## 2026-09-20: natural-language Notebook preview — local acceptance complete

明示tableと短い自然文から、describe→Qwen1call→検証済みselection→登録Notebook2assetsを実行する
`local-notebook-assist`を追加した。モデルにはtable/request/列名型だけを渡し、SQL/codeを受け取らない。
明示列順序とlimit1–100を対象とし、describe/SELECT別時点のUUID/schema一致を検査する。
current head更新は許容するが、意味解釈の一般保証やsnapshot固定transactionとはしない。

実5case各1回：name,id/1件は35.124秒でpassed/2materializations、filter16.129秒・未知列15.531秒・
曖昧14.978秒・SQL15.648秒は全unsupported/exit2となり、SELECT/assets0、describeは全5回先行した。
acceptedはreturned1/fetched2/truncated true・保存評価12run、全case cleanup true・model各1call・retry/cloud0。
usage合計は入力1681/出力72tokens、推論duration6044ms、cost NULL。不正bool requestは0.057秒で拒否した。
新80tests・全local73suites/2583tests（2582PASS・既存optional1skip、60.169秒）とsecret scanが成功。
前後65sources/527保持files・MLflow59tables/16runs/1320points/16artifactsは一致、AH旧519行を保持し新16events→535。
SDK1.44.0＋必須semantic-conventionsだけを専用DataOpsへ追加し、旧96 lock versionsを維持した。
独立保存670checks（143意味＋527files）/693inputsが初回成功。16監査/local spans、2assets/12評価、
6Reader owner終了・25distinct terminal queriesを照合し、追加API/SDK/model/Reader実行0。
5新run77filesの実割当は51,728,384B。新fixture/kernel/service0、保持data/backup削除なし。
証拠は`tmp/local-experiment/notebook-assist-checks/b0398f85421f4100a8737a6d05c12ff5/`。
[Assist runbook](../../../tools/local_experiment/notebook_assist/README.md)と
[AGENT-001](L4/WP-L4-AGENT-001_Notebook_to_Dagster_Agent.md)を参照。
この5要求の機能証拠を、自然文一般品質・任意Notebook自動変換・training・本番受入へ広げない。

## 2026-09-20: selected-table Notebook to assets — local acceptance complete

[AGENT-001](L4/WP-L4-AGENT-001_Notebook_to_Dagster_Agent.md)のlocal-partialとして、
`local-notebook-assets-preview`へexact current-head Reader requestを渡す入口を追加した。
登録table-preview templateとprivate canonical request/SHAからschema2の
`table_preview`→`saved_evaluation_summary`を実行し、元の無指定schema1/defs bytesは維持する。
生成Pythonにrequest値を埋め込まず、runnerは動的importせずhost定義を使用する。
request/table/column order/requested_limit、exportと取消時のenv復元を照合する。

実1回20.064秒で保持fixtureの[name,id]・limit1からreturned1/fetched2/truncated true、
12評価run読取・2materializations・業務/owner passed・Reader cleanup trueとなった。
行順保証はなく合法な1行subsetを検証した。benchmark4runは除外し、新fixture/model/kernelは0。
不正bool limitは0.046秒でNOTEBOOK_REQUEST_INVALID/exit1として拒否した。
35focused（新16/既存19）と全local70suites/2503tests（2502PASS・既存optional1skip、60.303秒）、
secret scanが成功。Go製品変更はなく、先行Go raceの件数を今回へ加算しない。
前後64sources/505保持files・MLflow59tables/16runs/1320points/16artifacts・AH519は一致。
独立保存533checks（28意味検査＋505files）/590inputsも初回成功した。
SQLite1SUCCESS/2materializations・retry/failure0、5distinct terminal Reader query IDs、
Go owner終了、選択行/評価projection/cleanupを照合し、追加API/model呼出しは0。
新run20filesの実割当は10,809,344B。保持table/MLflow/backupの削除は不要。
証拠は`tmp/local-experiment/notebook-assets-selection-checks/b9a9c4d7271d494fbb7c645c78dde93b/`。
[assets runbook](../../../tools/local_experiment/notebook_assets/README.md)を参照。
一般Notebook/LLM変換、training/autolog、本番受入へ主張を広げない。

## 2026-09-18: saved Reader metadata inspection — local acceptance complete

`query reads [--token TOKEN]`はReader v1–v4の4固定rootを読み、既存summaryのmetadataを一覧化する。
v1のtables/describeも含め、同tokenが複数版にあれば版別に表示する。
公開entry12fieldsにはtable/snapshot/記録result/cleanupを含むが、rows・Count値・SQL・literal・columnsは出さない。
recorded result/cleanupはproducer claimsの局所整合までで、再実行・現存table/稼働状態の確認とはしない。
新API/SQL/model/kernel/state/receipt作成0、stdout1lineはchild終了一致後だけ公開する。
shared lock busyはactive/nullでpayload未読、欠損/不完全/不正/変更/unsafe pathを明示する。
128runs・summary2MiB・総読取32MiB（再照合含む）・公開256KiBを超えたprefixをcompleteにしない。

実保存一覧は42entries（記録passed36/failed6、v1/v2/v3/v4各21/7/8/6）、complete/exit0・0.337秒。
これは今回の保存inventoryで、固定必要件数や42件の新実行ではない。
Count0を記録したtokenの単独表示は1saved passed・0.047秒で、Count値自体は非公開。
保存failed tokenも1saved failed/cleanup falseを保ち、inspectionはcomplete/exit0・0.043秒。
missing tokenは0entries/incomplete/exit1・0.044秒で、未観測を成功/ゼロへ補完しない。
前後58sources/505保持files/8root inventoriesはexact一致、API/SQL/model/kernel0。
Python19・Go10 top/42 subtests（実Python main9経路/公開fixture16を含む）、
全local69suites/2487tests（2486PASS・既存optional1skip、62.738秒）が成功。
Go race230 top/418 subtests・vet/build/secret scanも成功し、初回negative testのfixture誤りと元logは保持した。
独立保存166checks/125inputsも成功し、保存出力4回と42entries、保持file/root/sourceを照合した。
検証helperの初回table名grammar誤りは訂正し、元logを保持した。製品・receiptは変更していない。
証拠は`tmp/local-experiment/query-reads-checks/58d1e8dd958d4658acabd38e1a65243f/`。
[Query Reads runbook](../../../tools/local_experiment/query_reads/README.md)を参照。
新たなReader実行品質、現在のcleanup、本番受入へ主張を広げない。

## 2026-09-18: selected-table Notebook preview — local acceptance complete

[NB-001](L4/WP-L4-NB-001_JupyterHub_bootstrap.md)のlocal-partialとして、
stdinでReader v1 JSONを受ける`local-notebook-preview`を追加。
exact command/table/columns/limitを4096B以内で受け、private canonical request/SHAから
Reader argv・所有PID・返却typedrowsを束縛する。current head限定の別登録templateで、
元の無指定Notebook/Assetsは維持し、任意Notebook/SQL/snapshot/filterは受け付けない。

custom実行18.009秒は新所有5列fixtureの[ok,id,amount,name,day]/limit2から
returned2/fetched3/truncated true、無指定互換18.262秒は従来固定2行でpassed。
各3code cells・Reader/kernel cleanup true・MLflow評価12run表示/benchmark4run除外、
新model/MLflow write0、今回の実Notebook失敗0。
同UUID DROP1回でfixtureを回収し、retained10tables/6projectionsを復帰確認した。
前後63sources/400保持files・MLflow16runs/1320points/16artifacts・AH519は一致。
53focusedと全local68suites/2468tests（2467PASS・既存optional1skip、62.027秒）が成功した。
独立保存451checks（51意味検査＋400保持file個別）/110inputsも成功。
10 distinct terminal Reader query IDs・計4PID終了/connection削除・回収/保持状態を照合した。
fixture SQLは別枠で、追加API0。一般Notebook実行/Hub/multi-user/PySpark/trainingの保証には広げない。
証拠は`tmp/local-experiment/notebook-selection-checks/59d9d0ca8ab741f4903ad9371fa05a88/`。
[Notebook runbook](../../../tools/local_experiment/notebook/README.md)を参照。

## 2026-09-18: fixed Catalog Reader — local acceptance complete

`catalog describe --table smoke.fixture_v1 --profile local-experiment`で既存OpenMetadataを読む。
既存accountのlogin1回＋5anchors/one-hop lineageを前後各1回、正常時は計12GET・retry0。
root UUID/parent/owner/pinned runtimeを束縛し、全6応答のcanonical hashと安全射影の前後一致を要求する。
これはtransaction snapshotではなく、`sql_presence: not-checked`を全table/nodeに維持する。
raw応答/secret/connection/SQLを保存せず、API本文にない実SQL存在や実行履歴の保証を追加しない。
各HTTP10秒・全HTTP120秒・owner180秒の期限内で閉じ、cleanup確認後だけ成功を公開する。

実token `d85153baca5e5f26b880f7e6908549de` / `0230dc147b904219a47bb1088a2b9445`は
0.716秒/0.567秒、passed/cleanup trueで公開9fieldsが完全一致した。
catalog UUID `90725c41-7b04-483e-abe4-a59d4b9438e0`、descriptionあり、
owner ShirokumaLocalExperiment、id BIGINT/name VARCHAR、8nodes/8edgesを確認した。
新model/SQL/catalog write/service変更0。不正入力はallocation0で拒否し、準備段階のfake test
assertion修正と元logを保持した。既存read-only runtime fileのmodeは変更していない。

pure17/runtime19、Go race220 top/376 subtests・vet/build、全local66suites/2439tests
（2438PASS・既存optional1skip、61.463秒）が成功。前後45sources/396保持files
（旧384＋Count12）、MLflow59tables全行/16runs/1320points/16artifacts、AH519が一致した。
保存のみの独立29checks/67inputsでも2公開結果・各13通信・8nodes/8edges・保持状態を照合した。
追加API0、catalog全体の完全性や自動補完・本番受入を主張しない。
証拠は`tmp/local-experiment/catalog-read-checks/c431c0633e2b4107826732277b3b9ab4/`。
[Catalog Reader runbook](../../../tools/local_experiment/catalog_read/README.md)を参照。
次はNB-001の限定増分として、固定登録templateへcurrent-head Readerの型付きrequestを渡す。
任意Notebook/SQLは受け付けず、既存固定Notebook/Assetsの互換を維持する。

## 2026-09-18: exact snapshot count — local acceptance complete

`query count --table`へ任意の`--snapshot-id`とpaired `--where-column` / `--equals`を接続した。
schema4は固定snapshotの正確な`COUNT(*)`を非負bigint文字列で返し、推定値・LIMIT・個別行返却はない。
NULLを持つ行もCOUNT対象で、等値条件にNULLは一致しない。全行をscanし得るため既存timeoutを維持する。
current headとselected snapshotを分け、data COUNT POST1回・自動retry0・cleanup完了後だけ公開する。
旧Reader v1/v2/v3は維持し、Query Doctorと保存reportへのschema4接続は未対応。

新規所有fixtureのA `8323966370373005165`は2行、B `4863128661439145808`は3行。
Count4件はcurrent3 / historical2 / currentのid3一致1 / historicalのid3不一致0で全passed・cleanup true、
16.270秒 / 16.558秒 / 15.411秒 / 16.107秒だった。旧v1 selectでもNULLを含むtypedrowsを確認した。
literal syntax不正はallocation/SQL0、varchar等値はFILTER_TYPE_UNSUPPORTED/cleanup trueで拒否した。
最初のCountは並行Readerとの固定Polaris tunnel
18181競合でquery前に失敗し、公開cleanup_verified=false/未観測nullの元receiptを保持した。
以降は製品変更なしで既存制約どおり直列実行する。
失敗をcount0へ補完しない。fixtureは同UUIDで回収し、元retained fixtureの6views/inventoryを復帰確認した。
前後で54sources/384保持files・MLflow59tables全行/16runs/1320points/16artifacts・AH519が一致した。
保存のみの最終独立206checks/86inputsが成功。同UUID DROP1回・retained6views exact一致・
既存10tablesと所有table不在、4成功のexact countと初回失敗/未観測の区別を確認した。追加API/model/SQL0。

全local65suites/2403tests（2402PASS・既存optional1skip）が60.817秒で成功し、
Go212 top/369 subtests・race・vetも成功した。初回fullのtest fixture import失敗は保持し、
Makeのtest discoveryを既存方式へ統一後に40focused→全回帰を確認した。
新Qwen呼出・ReaderからのSQL writeは0。証拠は
`tmp/local-experiment/query-count-checks/26cbcfe56a684d95b610b06e2ccca68b/`。
[Count runbook](../../../tools/local_experiment/query_count/README.md)を参照。
新WPや一般SQL/性能/本番受入へ範囲を広げず、local増分として扱う。

## 2026-09-18: inactive MLflow clone restore and SDK readback

[WP-L4-ML-001](L4/WP-L4-ML-001_MLflow_tracking.md)で、完全exportから新private cloneを作成した。
export全体の複製は非稼働の証拠として保持し、cloneのexperiment/run artifact URI18箇所だけを再配置。
全59tablesの他のschema/行、historical15runs（評価11＋benchmark4）/1210履歴/15artifactsを照合した。
実restore `389807a736a74219a05e4d85dadd937a`へ専用SDK読取2回、3.287秒/3.251秒でpassed。
各worker終了後もSQLite論理snapshot/export/artifact bytesは同一、独立clone検証も成功した。
稼働中storeの16runs/1320points/16artifacts/全59tables/index、元export77entries、
Notebook/assets80filesは保持した。元backup/失敗2restore/失敗3SDKの証跡を残し、
missing artifact/外部URIの負例は使い捨て複製上でSDK前に拒否した。
最終独立照合でも26保存inputs不変、SDK2回の同一view/正常終了、SDK後のclone全行・URI18差分・artifactsを確認。
全local63suites/2363tests（2362PASS・既存optional1skip）が成功。
成功restore/SDK全5attemptsは78files・3,241,218B（実割当3,420,160B）、追加env0。
新service/model/Trino/外部API0、SDKguard前のlocal platform情報取得を
除いてsubprocess禁止、限定Python audit guardをOS sandboxとはしない。
cloneはactivation非対応で、既存serverを切り替えず、他host移行・本番復旧の主張もしない。
証拠は`tmp/local-experiment/mlflow-restore-checks/e63090a6c3ad4e08a49161244f9a27ba/`。
[復元runbook](../../../tools/local_experiment/mlflow/README.md#restore-an-inactive-local-clone)を参照。

## 2026-09-18: fixed Notebook to two assets — local acceptance complete

[WP-L4-AGENT-001](L4/WP-L4-AGENT-001_Notebook_to_Dagster_Agent.md)のlocal-partialとして、
登録template/hashからhost定型`defs.py`/manifestを生成し、`fixture_preview`と
`saved_evaluation_summary`を各1回in-process materializeする。依存は実行順だけでdata lineageを主張しない。
既存Dagster 1.13.22環境を再利用し、毎回新規private instance・telemetry false・retry0。
Notebook clientのReader1回と固定MLflow評価読取を使い、kernel/Hub/daemon/model/データ・MLflow書込みは0。
実run `feac569a1b4941929506cb53c36a14c2`は17.176秒、`e4a5a35f7dc64c089802f44e3b0b746b`は16.977秒で
業務/ownerともpassed・cleanup true。両方fixture2行、同head `7141845066324476177`、12評価runを表示し、
4benchmark runを除外する。型付き行/評価射影hashも保存結果で一致した。
19focused（実Dagster＋fake clients2件を含む）と全63suites/2316tests（2315PASS・既存1skip）が成功。
前後で69sources・先行Notebook42files・MLflow59tables全行/16artifacts/indexが一致し、16runs/1320pointsを保持した。
独立42checks/144inputsで各SQLiteの1SUCCESS/2materializationsとmetadata、retry/failure0、
Reader10 terminal query IDs、先行Notebookと同じtypedrows/12評価JSON、所有Reader2PID終了を確認した。
追加API0、SQLiteはread-only・immutable・query_only。実2runは38files・21,364,736B（binary/Dagster込み）、
新env0・保持data/backup削除なし。
証拠は`tmp/local-experiment/notebook-assets-checks/7f7dd020de634a0d930dbe6b756850eb/`。
生成物を動的importせず、業務成功後の取消はowner終端で区別する。利用停止時も証跡/既存fixture/Trackingを保持する。
任意Notebook変換・LLM変換Agent・Hub/scheduler・training・本番受入は未達。
[Assets runbook](../../../tools/local_experiment/notebook_assets/README.md)を参照。

## 2026-09-18: fixed local Notebook — local acceptance complete

[WP-L4-NB-001](L4/WP-L4-NB-001_JupyterHub_bootstrap.md)の最小local-partialを実装し、実2回が成功した。
専用Python 3.12とnbclient/nbformat/ipykernelのlock環境で固定Notebookを実行し、
既存Go Readerの`smoke.fixture_v1` id/name・limit10 SELECTを1回だけ表示する。
次にloopback MLflowの12評価runをread-onlyで表示し、4benchmark runは別experimentとして除外する。
Git templateは空outputsのまま、実行済みNotebook・connection・Readerログをprivate tmpに保持する。
各3code cells・Reader1回・MLflow12評価run表示、18.275秒/18.241秒、cleanup両trueを確認。
Reader前の初回失敗を保持し、Jupyter接続file親のsticky mode変更をprivate subdir配置で解消した。
全回帰は修正前62suites/2297tests（既存optional1skip）、修正後26focused＋実2回を区別する。
65sourcesとMLflow59tables全行・16artifacts/indexが前後同一で、16runs/1320metricsを保持した。
独立62checks/104inputsでtypedrows/同UUIDhead/10 terminal query IDs/各12評価射影/cleanupを照合した。
Reader/kernel各2PID消失・connectionなし、追加API0。helperの2誤前提修正は元logを保持し、製品再実行0。
専用env約72MB・check cache約83MB・実3run約30MBの実割当を記録し、保持データ/backup移行なし。
利用停止時も実行証拠とMLflow/fixtureを保持する。証拠は
`tmp/local-experiment/notebook-checks/8d4a2ec9c0f5413e85009312de57faa9/`。
モデル/MLflow書込・直接PySpark・Hub・training/autologは対象外。
WBSのSpark→JupyterHub→MLflow全依存を完了したとは扱わず、ADR-0034でこの独立部分を進める。
[Notebook runbook](../../../tools/local_experiment/notebook/README.md)を参照。

## 2026-09-18: SRE observation-signals in MLflow

既存MLflowを停止して完全exportを保持し、保存評価1run/16samples/110pointsを追記した。
再取込は全追加0、同じloopback/storeで再起動healthy。旧15run・15artifacts・全59tablesの既存行は不変。
新run `d35f31449d054832a536f045d269d60a`の全110points/9params/10tagsを独立照合し、
元24source filesを保持した。APIの固定run読戻し1回は35 latest metricsと一致した。
Qwen/監査/benchmark再実行0、costNULL・suite境界を維持。新たな一般品質の評価とはしない。
製品source変更なし、全回帰は直前cc11ff9を継承。証拠は
`tmp/local-experiment/mlflow-sre-signals-checks/f2c0f8f220a743409d6ffb1985e2e8c2/`。
[WP-L4-ML-001](L4/WP-L4-ML-001_MLflow_tracking.md)を参照。

## 2026-09-18: saved SRE log status — local acceptance complete

`sre status TOKEN --logs`が保存済みtyped log観測のinitial/followup/comparisonを表示する。
新schema3/14keysだけを追加し、通常schema1とusage schema2を維持する。両flag trueはchild前拒否。
passed証拠だけを投影し、旧未観測・未検証・unknown・有効なempty/0を区別する。
raw本文や追加API/model/auditは0、header件数をevent数やログ健全性・回復とは扱わない。
実CLI30回で旧12出力がbyte一致、40保存entries不変。新logsはavailable1/legacy-absent4/unverified1。
実最大2639B、offline最大長3266Bで4096B以内。全local61suites/2271tests（既存optional1skip）が成功した。
runtimeとAH519は直前証拠を継承し、今回再観測していない。
証拠は`tmp/local-experiment/sre-saved-logs-checks/fb4d3d0240354c7985a9a2bdb94cf00a/`。
[WP-L2-AGENT-002](L2/WP-L2-AGENT-002_SRE_Bear.md)と
[SRE runbook](../../../tools/local_experiment/bears/SRE_CLI.md)を参照。

## 2026-09-18: typed equality Reader and diagnosis — local acceptance complete

Readerへpaired `--where-column` / `--equals`を追加し、current/explicit historical snapshotへ
integer/bigint/decimal/date/booleanの単一非NULL等値を固定する。filter無しv1/v2は維持する。
新schema3 sourceからの診断は同snapshot・同条件のEXPLAIN/STATSを使い、元SELECT再送0。
private request/argv/Trino SQLにliteralが存在するが、公開metadata・モデル・監査へ値を追加しない。
条件列が投影される場合だけ返却cell等値を照合し、非投影条件はSQL/schema/snapshot束縛までとする。
全local61suites/2260件（2259成功＋既存1skip）、Go186 top/232 subtests・vet/raceが成功した。
新規所有A2→B3 fixtureのfiltered7件と旧v1 read1件が成功し、syntax拒否はchild0、varchar拒否はdata SELECT0だった。
歴史診断1回は元SELECT再送0・Qwen528/95tokens/4350ms・cost NULL、同条件STATS row estimate 1.0、
監査3件の相関を確認した。保存reportは旧6＋新v3の7件complete。
同UUID DROP1回でtable不在・retained fixture前後完全一致を確認した。4231旧files/250sources一致、
AH旧516行の全hash保持＋新3行、独立167検証/42inputs不変・product49query IDs終端済み。
fixture SQL/監査読取はこのquery数と区別する。証拠は`tmp/local-experiment/query-filter-checks/640deb117c414457bdccf23baca5e0b2/`の
`after.json`、`independent-saved-verification.json`。
[WP-L2-AGENT-005](L2/WP-L2-AGENT-005_Query_Doctor_Bear.md)と
[runbook](../../../tools/local_experiment/query_filter/README.md)を参照。

## 2026-09-18: SRE observation-signals evaluation — local acceptance passed

[WP-L2-AGENT-002](L2/WP-L2-AGENT-002_SRE_Bear.md)と
[WP-L2-EVAL-001](L2/WP-L2-EVAL-001_Agent_evaluation_harness.md)へ、明示
`observation-signals-v1`の8cases×2独立samplesを追加する。明示0、usage/log不明、noisy header、
runtime critical、現在container異常、過去OOM、Pod/runtime双方criticalを固定oracleで区別する。
モデルにexpected/raw logは渡さず、check順序不問で既存health/critical根拠/許可read/原因未確定を要求する。

新passedだけsafe triageを保存し、reportで登録case/hashと既存validator/literal oracleへ再照合する。
旧6/8/16casesは互換を保持し、requested16だけで新旧8caseを混ぜない。失敗・未実行分母は保持する。
登録dataset hashはproducer宣言の一致であり、実HTTP入力や実log品質・一般診断品質の証明ではない。
実1runは16/16 passed・監査16・retry0。保存reportは12valid runs/11groupsへ増え、旧11run/10groupsを保持。
受入21checks、4,192入力/238source不変、fresh AH500→516の新16件を照合した。
全回帰60suites/2,232tests（2,231PASS・既存optional1skip）。証拠は
`sre-observation-eval-checks/6bf0f90b299b4b3880bb9644fada1a32/`。runtime保持は前段db54cd5を継承し、
新runtime再観測とはしない。詳細はAGENT-002/EVAL-001へ集約する。

## 2026-09-18: fixed Trino log signals — local acceptance complete

[WP-L2-AGENT-002](L2/WP-L2-AGENT-002_SRE_Bear.md)のPod readへ、固定1Pod/1containerの
current logのtyped集計をoptionalで追加した。raw本文・SQL・token・logger/thread・本文hashは保存も
LLM転送もしない。Pod/container/timeを前後で束縛し、最新fileのbounded fragmentだけを扱う。
header/prefixは本文中で偽装でき、WARN/ERROR header形行数とprefix時刻は真正event数・時刻ではない。
unknownと空応答0を分け、ログ健全・原因・critical判定や新findingを追加しない。
比較はstate/identity/要求取得区間のoverlapだけで、count差・合計・rate・回復へ進めない。

Pod一覧の共通20秒＋cleanup2秒、usage最大20秒、logs最大20秒の取得上限は62秒。
他tool/audit/CPU処理を含む全MCP120秒の保証とはしない。旧ログ未観測、既存usage比較、
CLI既定13keysとusage表示14keysを維持する。host新15/既存込み110testsが成功し、
保存6Pod＋最大synthetic集計は7932Bで8192B capを維持した。実機結果を以下に分離する。
証拠は`sre-log-signals-checks/2e639ba4440c4efda65ce299a0b55fda/`、
baselineは`sre-log-signals-preflight/0d901f7fe82e454287e15160a498c0d2/`。

実CLI `67088c0df26da13728309bc95e6b8de2`はpassed/exit0、Qwen1callは2506/104tokens・6461ms・cost NULL。
既存6warningからPod followup1種を選び、MCP5/attention-required/原因undetermined/修復falseを維持した。
logs初期/followupはobserved空fragment/0、同identity/要求取得区間overlap=true。
これはログ健全・要求窓完全・実format検出品質の証明ではない。usageは同timestamp/windowのcached再読取だった。
全60suites/2207件（2206成功・既存1skip）、Go SRE30top/95sub・CLI logs5・dispatch4が成功。
保存status8calls/40entriesとsource233不変を確認した。after元結果22/23 changedは保持し、
4120旧files中4116完全一致、Gateway2files各5行append・runtime2filesのbyte/mode同一/mtime更新を分離した。
独立68checks PASSで旧AH493行のwhole hashを保持し、新7events/Loki7/Gateway5＋local2spansを照合した。
同taskのAH SELECT/Loki queryは各1回をcacheし、再照会0。57locks全てと新lock単体のinactive、
source233、保存status8calls/40entriesの保持も確認した。証拠は同checkdirの`independent-verification.json`。
deep retained SQL/Metadata logical/MLflow fullの保持根拠は先行sealを継承し、今回再スキャンしていない。
この固定観測のlocal受入を完了したが、空fragmentは非空実logのformat検出品質・ログ健全性・原因診断の実証ではない。

## 2026-09-18: saved SRE usage status — local acceptance complete

[WP-L2-AGENT-002](L2/WP-L2-AGENT-002_SRE_Bear.md)の保存表示を、明示
`sre status TOKEN --usage`へ追加した。最終frameだけschema2/14keys、既定status/runは
schema1/13keys、allocatedはschema1/4keysを維持する。改行込み4096Bの固定JSONとして、
同じlock/raw snapshotで検証したinitial/followup usageとsample-window比較だけを公開する。

保存field有りのavailable（state unknownも含む）、旧未観測のlegacy-absent、失敗/busy/不正等の
unverifiedを分け、後者2つのpayloadはNULL。明示0を欠測にせず、現在の負荷やtrendを再評価しない。
新API/model/audit・元証拠書換え・新tokenは0、前段AH493は今回再照会しない継承値である。
証拠は`sre-saved-usage-checks/e4c49ef987d34d078b0884e6102f4b6c/`。
製品CLI17回（5tokens×plain前/usage/plain後15＋新usage再読取1＋missing1）が受入成功。
全5件のschema1結果を保持し、旧3件legacy-absent・旧observations欠落1件unverifiedを確認した。
新`52aa250a1b92b29d90205836a6ea0a3a`は保存2samples/different-sample-windowを表示し、再読取も同一。
入力32dir/file entriesと2parent inventory、2inactive locks、Gateway監査2files、source223が不変だった。
全local56suites/2150件（2149成功・既存optional1skip）、Go full/race各171top/212subtests・vet、
Go SRE30top/95subtests（実Python main11case含む）、PythonCLI40件が成功した。
独立reviewのGo schema2 failed条件の指摘を修正し、追加負例を含めてPASSした。
別の保存受入68checksもPASS。API/CLI再実行なしで31evidence filesを束縛し、
32入力entries/source223/binary/2lockの不変を確認した。`independent-acceptance.json`に保存する。
証拠は同checkdirの`acceptance.json`、`source-seal.json`、`independent-review.json`。


## 2026-09-18: fixed Trino resource usage — local acceptance complete

[WP-L2-AGENT-002](L2/WP-L2-AGENT-002_SRE_Bear.md)の次の小増分として、既存Pod readへ
固定Trino 1Pod/1containerのCPU平均nanocores・memory working-set bytesを追加した。
新usageを含む実CLI1回がpassed/exit0、初期/followupともobserved。独立保持監査73checksも成功し、
この観測機能のlocal受入を完了した。一般的な原因診断の完了とはしない。
pods呼出しごとに追加するアプリ側kubectl取得操作はMetricsと同Pod再読取の最大2回で、
アプリ側再試行はない。各操作8秒＋所有child cleanup猶予2秒、返却64KiBを上限とする。
`get_attempts`は取得intent数で、kubectl/client-go内部の429/Retry-After再試行を含むHTTP総数ではない。

前後Pod/container identityとrequest/sample時刻をexact nsで照合し、値はint64文字列で保存する。
unknownを0へ変換せず、旧field欠落の保存形式を維持する。同一cached windowは新測定としない。
新しい負荷findingや原因推論・自動修復は加えず、公開SRE CLI schemaとroot_cause=undeterminedを保持。
旧SRE84を含む95 focused testsと新11件の独立確認が成功、providerの8KiB上限も維持した。
実runは`sre-llm/52aa250a1b92b29d90205836a6ea0a3a/`。同Pod/container identity・restart1・
Running開始時刻を前後で確認した。UTC 19:18:52/19:19:07のsampleはそれぞれ
window 15014000000/14990000000ns、CPU 11121886/11591194nanocores、
memory 2162106368/2162118656bytes。`different-sample-window`として分離し、trendを判定しない。
Pod/container状態に差分はないが、usage数値も不変だったとは述べない。
過去warning6件に対してPod read1種を選び、attention-required/原因undetermined/修復falseを維持した。
Qwen1callは1956入力/104出力tokens、6550ms、cost NULL。producerは監査7件を検証し、
workflow保存span2件とGateway保存span5件を区別し、MCP返却ID5件との相関も確認した。
全local56suites/2139件（2138成功・既存optional1skip）、Go SRE16件、CLI29件、source219不変を確認。
before23checks/保持4003files/AH486をbaselineとした。独立73checksで3999files完全一致、
Gateway2filesの旧prefix保持＋各5records追記、runtime2filesの内容/mode同一・mtime更新だけを確認。
親afterの厳密fingerprint不一致は原結果22/23成功のまま保存し、4差分の限定確認を追加した。
AH旧486行のSHAを保持して493行となり、新AH/Loki各7eventsと保存spans7件が一致した。
source219不変、旧lock54件と新SRE lockはinactive。新status2回/対象8entries、
旧status4件のbefore/after計8回/対象24entriesもJSON一致と保持不変を確認した。
status helperのcommand期待名と独立helperのmodel span task_id期待は各1回訂正し、原失敗を保存。
製品失敗0/model再実行0。独立確認のAH/Loki読取は各2回、cache保存後はsaved-onlyで検証した。
受入証拠は`sre-usage-checks/8aa1e4d2d00c419e8c1d35ca8ab7752b/`の
`independent-verification.json`（73checks）と`audit-readback-clarification.json`に保存する。

## 2026-09-18: saved diagnosis report — local acceptance complete

新しいread-only reportを既存診断とは独立moduleへ実装した。
全件と単件を同schemaで表示し、過去結果とreport commandの成否を分離する。
失敗・active・不正・欠損は可視化し、v1/v2やmodelの別groupとusage/cost NULLを維持する。
元Reader files/tableを読まず、D summary/spansだけで整合を確認し、API/model/audit0とする。

実CLI9回（all、single6、all再読取、missing）はexit0が8回・expected exit1が1回で受入成功。
allは4passed/2failed・3groups、single6行はallと一致し、再読取はJSON全体が一致した。
入力6dirs/18filesのSHA・mode・mtime・ctime・inode・集合、source77を保持、出力logs9件はprivate0600。
直前の独立60checksを環境baselineとして引用し、追加runtime監査やfresh AH読取は行わない。

最終known-cost修正前に全local55suites/2099件（2098成功/既存optional1skip）と
Go157 top-level/116subtests・vet/raceが成功。修正後はPython診断family158件（report52を含む）、
実Python→Go9経路（新cost1subtestを含む）を確認した。全体回帰の重複再実行はしていない。
cost0.1/0.2/0.3はtoken順float64加算でPython/Goが一致し、現保存6件のcostNULLは変わらない。
実記録の整合表示を確認したもので、synthetic評価の品質分母へ実診断を加算しない。
証跡は`query-diagnose-report-checks/5f502301717046f39c3718f85d73fdb3/`の
`acceptance-{input-seal,input-after,verification,source-verification}.json`と`independent-final-review.json`。
scopeは[WP-L2-EVAL-001](L2/WP-L2-EVAL-001_Agent_evaluation_harness.md)を参照。

## 2026-09-18: historical snapshot diagnosis

`query diagnose --read-token TOKEN`へsnapshot Reader v2の検証経路を追加した。
現在identity Bと選択Aを分け、保存sourceとfresh metadata/保持snapshot一覧を前後で完全一致させる。
元SELECTを再送せずAのEXPLAIN、元query history、全compatible schema列をAへ固定した
STATS subquery（LIMITなし）を使う。v1とmodel/public/監査/取消の契約は維持する。

focused Python106件、Go150 top-level/83subtests・vet/raceが成功。
fake HTTPで過去Aの統計2と現在Bの統計3、expiry/schema/metadata/headの拒否を確認した。
実機前の全local回帰55suites/2048件（2047成功＋既存optional OTel1skip）も通過。
実historical診断はA2行の統計を返し、current B3行と区別した。v1診断も成功し、
実際に消失していた元query historyをnot-retained/timings NULLとして扱った。
2診断は各5worker＋drain1、元SELECT再実行0、model1、監査3events、query/接続cleanup確認済み。
Qwen合計1060prompt/209completion tokens・7833ms、costNULL、cloud/retry0。
生成tableは同UUIDへDROP1回し、保持fixtureの6投影とtable一覧が復元した。
Reader2＋診断2の計22query IDsは一意・終端、診断による元SELECT再送は0。
独立60checks（fresh23＋保存束縛37）で3878旧files/70sources、保持runtime、AH旧480行のhashを確認。
AH480→486の新6events/Loki6/spans6が一致し、5ports空き・54locks非activeだった。
設計83documents/14tests、repo/Vault同期と関連リンク、secret/diff検査も成功した。
scopeと受入は[WP-L2-AGENT-005](L2/WP-L2-AGENT-005_Query_Doctor_Bear.md)を参照。

## 2026-09-18: retained snapshot reader CLI

`query snapshots`と`query select --snapshot-id`を追加。current headとselected snapshotを分け、
同じfield IDs/name/type/nullability/orderのschemaだけを許容する。前後metadataが変化すれば公開を拒否する。
6回の新profileで一覧・過去2行・現在3行・limit1・未掲載ID拒否を確認し、従来Readerも現在3行を返した。
未掲載IDはdata SELECT0、不正IDはchild割当前に拒否、全31 CLI query IDsは終端。
生成tableのA/B snapshotは数値が降順だったが、親子関係とcommit順を正しく扱えた。
同UUIDを確認してDROP1回、保持fixtureの全6投影とtable一覧が復元。モデル/新service/image0。
新Python71・Go/実Python7経路・vet/raceと全2018件が成功。
詳細・run IDsは[WP-L3-ICE-001](L3/WP-L3-ICE-001_Iceberg_branch_tag_clone_demo.md)を参照。

## 2026-09-18: diagnose a completed Reader SELECT

`query diagnose --read-token TOKEN`を追加した。privateな成功Reader receiptと元query identityを
照合し、同snapshotのEXPLAIN・限定history・STATSを取得、終了・接続回収後に既存Qwenを1回呼ぶ。
SQL・行値・table/列名をモデルへ送らず、原因未確定・効果未測定を保つ限定所見を返す。

保持tableと別の新tableで2診断が成功。元SELECT再送0、各5観測query＋drain1、
Qwen合計1047prompt/190completion tokens・7687ms、監査6eventsと保存OTel/Lokiが一致した。
初回2実行はTrinoの長さ付きvarchar応答で失敗し、各model0/cleanup済みの記録を保持。
Reader制約を変えず、診断3phaseだけ型を正規化して修正した。
新Python76件とGo/実Python結合・vet/race、全1947件が通過。
詳細、失敗・成功run IDsと実験table回収は[WP-L2-AGENT-005](L2/WP-L2-AGENT-005_Query_Doctor_Bear.md)を参照。

## 2026-09-18: Lakehouse reader CLI

`query tables`、`query describe`、`query select`を追加した。
発見済みtable/列だけを固定snapshotから最大100行読み、整数・decimal・snapshot IDの精度を保持する。
結果公開前にUUID/schema/head、query終了、接続回収とchild exitを確認する。既定receiptに行値は保存しない。

実10実行で2schemaの一覧、2tableのdescribe/select/limit1、未発見table/列拒否が成功。
不正な式は実行前に拒否した。新規専用tableでBIGINT境界・38桁decimal・日本語・NULL・date・boolを照合し、
同UUIDをDROP後、保持fixture全履歴/refsとtable一覧が復元した。LLM・新image/serviceは0。
新Python95tests、Go/実Python結合、全1871件が成功し、source/binary41filesは不変。
独立監査122checksで3590旧files・14Pods/27resources/3Docker・既存policyと接続回収を確認した。
private検証の型想定integer→bigint訂正は保存結果だけで行い、元の失敗記録を保持した。
詳細とrun IDsは[WP-L2-AGENT-005](L2/WP-L2-AGENT-005_Query_Doctor_Bear.md)を参照。

## 2026-09-18: Iceberg branch publication and divergence refusal

新規所有tableでSparkがB3行からC4行を作り、停止・一時access回収後にhostの
UUID/main/branch/tag4条件付きREST1回でmainをCへ反映した。
正例`75e820ef797a43fba52585860588b2c0`はHTTP200・metadata-only・新snapshot0。
named/numeric6読取とCの3files/2417BをSpark/Trinoで照合した。
別tableの負例`9a88d1803c814ff18dca4f9f965f99e8`ではmain Dとbranch Cを兄弟にし、
古いBと非祖先Dをそれぞれ送信前拒否、publish POST0・全7読取一致を確認した。
これはhost拒否であり、実並行writerのCAS409実証ではない。

両runとも19nativeUID＋一時NP2/Polaris3entitiesを回収し、同UUIDの生成tableをDROP。
保持fixture全履歴/refs/table一覧不変、AH470追加0、新image/service/model0。
新75tests、全1776件（1775成功＋既存1skip）、source20files不変、保存証跡独立131checksが成功。
前後環境22checksも成功し、3503既存files・14Pods/27resources/3Docker・MLflow・network境界が不変。
詳細と証跡は[WP-L3-ICE-001](L3/WP-L3-ICE-001_Iceberg_branch_tag_clone_demo.md)と
`tools/local_experiment/spark_publish/README.md`を参照。汎用merge/zero-copy clone/本番受入は未達。

前回確認（2026-09-16 09:55 UTC）：ローカル回帰157件と設計関連14件が成功し、
repositoryの設計66 documentsを検証した。今回更新した12 materialized documentsは
Obsidian sourceと一致する。`local-platform-status`の5 component groupsはすべてready。
Pawprintをprivate JSONEachRowへexportし、OpenMetadataの追加backupも保存した。
Cloud APIは未実行。provider/model・認証環境変数名・追加予算は外部利用時の別判断として保持する。

## 2026-09-17: native Spark write and independent Trino readback

Spark4.1.3＋Iceberg1.11.0の既存固定imageで、native driver/executorから
Polaris1.6・SeaweedFSへ接続し、Trino483で独立読戻しする限定実験が成功した。
run `d1ba2d6ed7f84d49b9928aafffb26cd4`（12:14:04–12:15:47 UTC）は
driver/executorともSucceeded、driver exit0、9checksすべて成功。
executor1のstage6/tasks6・7、2partitions/attempt0とshuffleを観測した。

hostが専用名・comment・明示S3 locationで空tableを作成し、UUID
`242d28cd-9200-484e-9569-bdfe437d571d`と0rows/0filesを捕捉した。
Trinoの空CREATEによる初期snapshot `2785399152784753098` を親として、Sparkが32行を1回INSERT。
新snapshot `1477241556327226373` をfresh Spark sessionのDataFrame/SQLとTrinoへ照合した。
全32行、groups `[[0,8,840],[1,8,896],[2,8,952],[3,8,1008]]`、
2Parquet files（各16行、1337/1341 B）、UUID・metadata location・親snapshotが一致。
snapshot IDはPython整数のまま保存し、JavaScript Numberへ変換しない。

nativeの固定profile `iceberg-write` は16manifest＋executor＋自動Endpoints/EndpointSliceを使う。
Secret1個のAWS credentials fileとPolaris tokenを実行時だけ注入し、plan/created receipt/inventoryへ
data/stringDataを保存しない。Secret fileのBearerはdriverだけにmountし、SQLConfから初期化後に除去。
既知secret値のログ秘匿とclass名だけの失敗診断を追加した。
Iceberg FileIO内部のtask serializationまでBearerが渡らないという保証は未検証である。

一時Polaris principal/principal-role/catalog-role各1、binding2、当該tableだけの
TABLE_READ_PROPERTIES/TABLE_WRITE_PROPERTIES/TABLE_LISTを使用。
専用tokenの実expires_inは3600秒、refresh/exchangeを無効にした。entity削除を
発行済tokenの即時失効の実証とは扱わない。SeaweedFSのIAM readOnly設定は維持し、
S3は既存のstatic bucket credentialsを使用。Polarisのtable限定権限をS3 prefix制限と混同しない。
保持NPは編集せず、Polaris8181/SeaweedFS8333へ新namespace/runだけを許すIngress NP2を追加・UID回収した。

Spark停止とown Trino query drain後、19個の既知UID、一時Ingress NP2・role bindings/3entities、
同UUIDの新tableを回収。新tableのcatalog不存在を確認した。S3 prefixの直接LISTやorphan清掃は未実施。
Secret／任意SQL／任意コード／任意tableを受け取るCLIにはせず、再現入口は
`make local-spark-iceberg-remote SPARK_ICEBERG_BUILD_TOKEN=<verified-build-token>` に固定する。
詳細は `tools/local_experiment/spark_iceberg/REMOTE.md`。

### 実機で見つけた差分と失敗証跡

- `c39f44bd7dc64e0cba26e998b3ef19ff`: 空CREATEにもsnapshotがあるため初期期待を拒否。
  Spark/一時access作成前。confirmed CREATE queryと同UUID・0rows/0filesを別証跡で照合して回収し、
  原failed receiptを保持。UUID捕捉は空判定より前へ修正した。
- `fa34d22d4ff8472e85b5eb6c80b17511`: Iceberg HTTP retry=0はruntimeが拒否。
  許容最小1へ修正。HTTP層には429とRetry-After付き503のPOST再試行もあり、INSERTの再実行0と区別する。
- `482bc347555e4bd19ce35ae351a0ea3d`: Py4J CloseableIterableをPython iterableとして扱いTypeError。
  Java iterator()/hasNext()/next()へ統一し、Python iteration不可のfakeで回帰化した。
- `9bad9b32de304414982baa1df1bd4892`: INSERT後、fresh sessionの直接JVM catalog loadが
  元sessionのSQLConfを参照し認証失敗。対象active sessionでloadし、finallyで元contextを復元した。
  token除去を維持し、権限を拡大せず成功へ修正した。

後続3失敗も各19UID・一時access・同UUID tableを回収し、fresh fixture不変を確認した。
最初の回収証跡は `spark-remote-checks/1a85917c718b44acada8296899252182/first-recovery/`。
初回4runの保存証跡だけの独立監査43checks/87filesも成功した。

正式全回帰43suites・1306件（1305成功＋従来optional OTel1skip）。Spark関連242件は
既存132＋新110（core追加22、remote contract27/access28/table21/runner12）。
証跡は `spark-remote-checks/1a85917c718b44acada8296899252182/` に保存した。
実行前後の独立監査 `spark-remote-preflight/670f955acf6d40348fe79384521b3ae9/` で、
保持14Pods/27resources6CM3SecretUID、Docker3、AH428、履歴4tables5DDL、MLflow59tables/11artifacts、
旧2609files/1033directories＋完成済Spark113files、cached553images、保持NP/Service/Endpointsが不変。
fixtureのschema/2rows/snapshots/history/refs/alltablesはfresh Trino読戻しで一致。
OpenMetadataの保持確認は先行seal継承で、今回のfresh API確認ではない。
最終独立監査final-verification.jsonの27checksも成功し、4Spark namespacesと全5実験tablesのfresh不存在、
初回4runの87files不変、成功/authの30files封印、20Secret projectionsのdata/stringData非保存を確認した。
既存Polaris権限の6投影も内容不変。catalog-role配列の順序によるraw hash差は元のfalse検査を残し、
2通りの順列hashで前後双方に一致することを別証跡 `access-order-verification.json` で補足した。

これは固定32行・executor1台の機能証拠であり、一般的な性能評価、HA、本番受入を示すものではない。
新LLM推論・AgentHouse eventは0。WPはlocal-partialを維持する。
次は既存Data→Catalogを利用者向けCLIから実行・再開・read-only状態確認できる入口へ進む。

## 2026-09-17: isolated Spark–Iceberg classpath and read/write gate

Spark4.1.3＋Iceberg1.11.0/Scala2.13の隔離gateが成功。
Maven Centralの固定JAR2本111572756 Bを公開SHA512/SHA256・全ZIP CRCで検証し、
PGP signatureは保存のみ/未検証として区別した。prepare成功`99e250c5978244b3ab00105aba975f4b`。
公式JARのLICENSE/NOTICE重複を初回に厳密拒否した失敗を保持し、既知legal entriesだけを
occurrence/CRC付きで許容、code重複は拒否する。準備証跡は124023450 B。

公式Spark baseの11layersを保持し、JAR2本＋固定scriptだけを1COPY layer追加。
network-none/no-pull build `b8bea1b087b44b4eb53e1351f156e9cf` のimageは
`shirokuma.local/spark-iceberg-classpath@sha256:b7632ecd9a1b619a2836f6de23a27e0a7d13ade09d614fcfe1cc4e23401c8b43`。
Docker報告894722788 B、reuse `9f72a0b92f6c47bc96bd8aaff7d00018` は同一imageでbuild0。
input/source/context前後hashと固定tagを検証し、既存tagを上書きしない。

初回run `2008311aa12e49308e83e09cf91d25b2` はWRITE_FAILED/exit1。
Docker tmpfs既定noexecでARM64 Zstd JNIをmmapできず、誤って一般的なARM64非対応とは判断しなかった。
同UUIDの新tableと既知containerを回収し、failed receipt/logを保持した。
使い捨て/tmpだけexecを明示し（512MiB）、work-dir64MiBはnoexec、network noneのまま
hostnameをlocalhostに固定。root filesystem/UID185/1CPU2Gi/256PIDs/host mount0を維持した。

同image再実行 `61c875252b61416d8e3fb3237cfb0eee` はexit0・7checksすべて成功。
32固定行を新HadoopCatalog tableへ書き、新sessionのDataFrame/SQL全行と集計を独立oracle照合。
groupsは`[[0,8,840],[1,8,896],[2,8,952],[3,8,1008]]`。
table UUID `ee09c58d-0fb7-417d-8c95-78a3fdb65b50`、snapshot `8088807033386546123` を
Pythonの整数でmetadata v2へ照合し、同UUIDをDROPした。64bit IDをJavaScript Number経由で保存しない。
container `ee15ada82dfc7d57613b4275d4a17ffe4acf1f35a73f63cf71220812688da536` は回収し、
実recoverは追加create/start/remove0・原receipt保持。成功時ERROR/Exception log0。
報告済みclasspath issue16856はこの固定構成/範囲では再現せず、他構成全体の解決とは主張しない。

正式全回帰43 suites・1196件（1195成功＋従来optional OTel 1skip）。
新67testsはcontract16/prepare20/build15/runner16、既存native65と同じtest-local-sparkへ含まれる。
`spark-iceberg-checks/2e44ac11e1584fb9b7fa8880541b0e19/local-regression.log` / `regression-summary.json`へ保存した。
read helperがbytesを返す境界とmacOS /var symlinkで落ちた初期offline logsも保持して修正・回帰追加済み。


独立監査`tmp/local-experiment/spark-iceberg-preflight/ea0173dfc7cf45a7ba295d483e4d5bbe/verification.json`の56checksが成功。
14Pod/保持27resources6CM3SecretUID、Docker3、AH428、履歴4tables5DDL、MLflow59tables/11artifacts、
旧2609files/1033directoriesとnative Sparkの67files（初回29inputsも）が不変。両CIDのfresh不存在を確認した。
fixture/metadataは先行sealed証拠継承で、このgateではfresh readしていない。
image inventoryは547→553、旧547全保持。final1に加えclassic Docker buildのUSER＋4LABELの5中間imageがあり、
各build Step ID/actualinspect/config変化とbase11layersを照合した。1追加を期待した最初の検査failedを残し、
上記監査で6追加の由来を補足した。削除なし、全FS時点使用増259411968 B・残容量346592722944 B。
新37inputfilesを封印し、設計82documents/関連14testsも成功した。

これはlocal[1]/local filesystemのclasspath gateであり、Polaris/S3・分散Iceberg・Trino readbackではない。
新推論/監査event0、Secretsなし。再現と回収は`tools/local_experiment/spark_iceberg/README.md`。
次は新規tableだけのSpark write→Trino readbackへ進む。既存NPを編集せずrun専用の一時Ingress NPを
追加する必要があり、資格情報を証跡へ保存しないscopeを具体化してから適用する。

## 2026-09-17: native Spark driver and executor

[Spark on Kubernetes](L4/WP-L4-SPARK-001_Spark_on_Kubernetes.md)をlocal-partialとして実機検証。
既存k3s1.33.4/Docker CRI/ARM64を維持し、公式Spark4.1.3の固定ARM64 digestを直接1回pullした。
以前の546 image rowsを保持し、追加はSpark1行のみ。圧縮layers794127774 B、Docker報告794141545 B、
filesystem使用増分1999745024 B（全FSの時点差分）、残容量346994528256 B。
`spark-prepare/ad1a2bac18f445ecaee00fece9d873ef/pull-verification.json`にmanifest/config生byteshashとCRI照合を保存。
Docker snapshotterのID/default null表現差で失敗したprepare2試行も残し、追加pullなしで修正した。

毎回新namespaceにdriver/executor各1CPU2Gi・計2Pods上限、restricted PSA v1.33・non-root・read-only root・emptyDir。
固定32行から21行/4groupを集計し、2partitionのTaskContext・executor1・実Pod hostnameと独立oracleを照合した。
成功`e5b1bbad07a4471286d7e137ff065951`はdriver Succeeded/exit0、
意図的失敗`cb316657a2064bdcb46b2891d25990a6`は同じ集計後にFailed/exit17、
両方で15明示resource＋自動Endpoint2の計17UIDを記録してnamespaceごと回収した。
新namespaceへの既存shirokuma-dev限定admission適用やNetworkPolicyの全拒否経路実証とは主張しない。
Secrets/PVC/保持table/LLMにはアクセスしていない。

初回`aec20a5cb0204d378ca2ffcee7eedc9a`は計算/exit0でもPod phase反映待ち不足でwrapper failed。
Service Orphan削除でEndpointSlice ownerRefが失われたためcleanupも保留した。
元failed receiptを維持し、run labels/既知driver target UIDを再確認してsliceと既知namespaceを限定回収。
`spark-checks/b3d67df806554366bcf38ff0a2db774b/initial-recovery/verification.json`に別の復旧証拠を保存した。
runnerは終端phase待ちと生成Endpoint UIDの先行保存・限定削除へ修正。
Sparkの不要Service/PVC自動cleanupを設定で無効化し、RBACを拡張せずshutdown403を解消した。
namespace置換・曖昧create・不正復旧入力は拒否し、取消の元例外を保持する。
完了後recoverは原receiptを変えず、再Spark実行しないことも実確認した。

正式全回帰43 suites・1129件（1128成功＋従来optional OTel 1skip）、新Spark65件
（contract19/prepare18/runner28）。ログと集計は
`spark-checks/b3d67df806554366bcf38ff0a2db774b/local-regression-final.log` / `regression-summary.json`。
初回compute独立22checksは`spark-preflight/c957ac05ccaa4df4b7b9e5025d943a36/initial-compute-verification.json`
に保存し、wrapperを遡及して合格化していない。


独立fresh監査は`tmp/local-experiment/spark-preflight/c957ac05ccaa4df4b7b9e5025d943a36/final-runs-verification.json`の86checksと
`success-recovery-verification.json`の追補4checksで成功。全14Pod/保持27resources6CM3SecretUID、Docker3、AH428、
履歴4tables5DDL、MLflow59tables/11artifacts、旧2609files/1033directoriesが不変。MLflow DBbytesも一致しserver.logだけ更新。
3実験namespaceのfresh GET不存在、原failed証跡と旧67runfiles不変を確認した。
fixture/metadataは先行sealed証拠継承で、今回のfresh SQL/API確認ではない。設計82documents/関連14testsも成功した。

再現/回収は`tools/local_experiment/spark/README.md`と`make local-spark-*`。
benchmark、Iceberg/Polaris接続、JupyterHub/MLflow training、Comet、multi-node/HA、本番受入は未達。
次はIceberg1.11.0/Spark4.1用JARの隔離classpath試験を進め、成功後に新規tableだけのSpark→Trino照合を検討する。
この節目では次のJAR準備/純粋contractのみで、既存catalogへのSpark接続は未実施。

## 2026-09-17: local MLflow tracking for saved evaluation receipts

[MLflow Tracking](L4/WP-L4-ML-001_MLflow_tracking.md)のlocal-partialを実機検証した。
3.16.1/Python 3.12 native ARM64、専用lock91 entries/87 installed、venv約454 MiB・setup約532 MiB。
保存11 evaluation runsを11 MLflow runs/1118 metric pointsへ取り込み、同一source再取込は追加0/0。
Qwen152＋mock10＝全162評価をcase/provider別に保ち、新推論・新監査eventは生成していない。
標準UIのModel training → Runsで11件とSRE16/6/8case比較、safe artifactのNULL/task/traceを確認した。
GenAI trace画面の0表示とは区別し、取込時刻を過去評価の開始時刻へ流用しない。

専用SQLite/artifacts/HOME/TMP、store UUID `f239d270-c987-430a-bd3b-031bcaeec9b0`、
loopback18550のみ。server停止中のSDK importを共通lockで直列化する。
unknown Host/foreign CORS拒否、稼働中import拒否、起動noop・停止・再起動保持を確認。
UI telemetryはserver gateがdisabled/rollout0で、browser側の初期Onとは独立。検証browserもOffにした。
停止失敗2試行を残し、macOSのS状態argv終了遷移を捕捉してTERM後の同一process消滅待ちを修正。
`mlflow-checks/8bbe03ad6cd9488da46a486551f71b66/`で実停止・再起動が成功した。

初回`mlflow-tracking/imports/86a99022a1a346cb937ace741d5bd4be/`、repeat `90cf426b6e3b4ecfad82c5d96aca1d67`。
独立監査`mlflow-preflight/96cd337ad2a54b389c48c8cdf233854e/independent-summary.json`で
全59 tablesのDDL/論理行と11 artifactsの取込後→再起動一致、旧2489files/985directoriesの厳密不変、
AH428・履歴4tables5DDL・保持Kube6Pods/27resources/6CM/3Secret UID・Docker3不変を確認した。
fixture/metadataは先行sealed証拠継承で、今回のfresh readではない。
state実割当2334720 B、DB1314816 B、11 artifacts48091 B、owned2process RSS合計512688128 B
（時点値・共有ページの重複を含み得る）。初回回帰のumask起因1偽失敗logと、次のoffline testが
実18550へbindした2失敗logを保持。検証childを標準umask、socketをfake化し、衝突拒否1件を追加した。
正式全回帰は42 suites・1064件（1063成功・従来optional OTel 1skip）、新MLflow52件（runtime30/importer22）を含む。
`mlflow-checks/1251a41cfdc141ef8985546ab16fe925/local-regression-final.log`と`regression-summary.json`へ保存した。

Registry/Serving/Assistant、Spark/JupyterHub依存全体、複数writer・export復元・本番受入は未達。
次はSpark 4.1.3の最小native実験を検討。現k3s 1.33.4を維持し、この時点では純粋contract準備だけで、
Spark image pull/runtimeは未実施。再現・停止は`tools/local_experiment/mlflow/README.md`を参照。

## 2026-09-17: SRE reported container states

[MCP Gateway](L2/WP-L2-AGENT-001_MCP_Gateway_MVP.md)の既存Pod readにapplication-containerの固定射影を追加。
spec/statusを照合し、入力256行・返却8行に制限、reason allowlistと型・時刻を再検証する。
message/log/secret/container IDは渡さず、init/ephemeral containersは対象外。
現在と過去の終了は別根拠、unknown/truncatedは健康の証拠にせず、旧保存snapshotはnot-observedで互換を保つ。

[SRE Bear](L2/WP-L2-AGENT-002_SRE_Bear.md)は現在の非0終了・OOM報告・image-pull/backoff待機をcritical、
過去の非0終了/OOMはwarningとする。exit137だけではOOMと断定せず、root_causeはundetermined。
最大24findings・入力8KiB・追加read最大2種のまま、current critical根拠のPod再読取を必須とする。
同Pod UID＋container名の比較でstate・過去履歴・restart減少を記録するが、process identityや回復は推論しない。

実評価`c0de842e01824262bdc19298cfb9f0ac`は既存8＋追加8case×2＝32/32、監査32件成功。
入力26368/出力2070tokens、91289ms、cost NULL。実drillはこれと別の証拠である。
`tmp/local-experiment/sre-drill/850681c5c93949f09cd449c4d6728c9a/`のrun
`8efbf36cdca845af8caba3d5a3b26b5a`で、新規Pod UID `ba22d3e2-6df7-464d-af62-4df1287a1e72`を作成。
`fail-once`のcurrent terminated / Error / exit1 / ready=false / restarts0 / last_termination=null、
開始・終了とも2026-09-17 09:53:25 UTCを初回/followupで照合した。
Qwenはcritical f01/f02を選択し、保存射影から再構成した根拠も一致、同UID限定でPodを削除した。

workflow `workflow-c4455c5ca4664504899009781b79d8bb`、trace `4706d338a360bb614ce3d2a51a122054`。
実drillのモデルは入力1731/出力174tokens・6996ms、4初期read＋2followups＋LLM＋assessmentの8監査を
AgentHouse/Lokiで照合。保存実OTel2spansとMCP6件の返却span ID相関を区別する。
今回の実モデル合計33callsは入力28099/出力2244tokens・98285ms、cost NULL。

[評価harness](L2/WP-L2-EVAL-001_Agent_evaluation_harness.md)の新report
`tmp/local-experiment/evaluation-reports/a1f4249181e64c6c8943b13b2ed6a0f8/`は11valid runsでcomplete。
母集団はQwen152/152・mock10/10（別群）、全体162/162・11valid runs。
Qwen goldenは旧SRE6/8caseと新16caseを別groupにし、drill1callは加算しない。
SRE84（新host17＋drill3含む）/Gateway94（新14含む）/report24（新2含む）のfocused testsが成功。
最初の全回帰998件でGateway14件の配線漏れを発見してMakeへ追加し、正式1012件
（1011成功・optional OTel 1skip）を確認した。

独立最終監査`tmp/local-experiment/sre-container-preflight/0ac17cd4dad044d5838caccc68473445/verification.json`
の80checksが成功。AH388→428は旧388件全hash保持＋新評価32/workflow8、Loki8件は完全一致。
保存実span2＋MCP返却ID相関6、同UID/current Error/exit1とcritical f01/f02再観測を独立再計算して一致した。
保持Kube/Docker/履歴4tables・5DDL/1151filesは不変、生成Podの不存在をfresh確認した。
保持fixture/metadataの証明は前handoff監査
`data-catalog-preflight/de60ba1818d74bad9a0a289176e9f72f/verification.json`の継承referenceであり、
今回それらのAPIやSQLをfresh readしたとは主張しない。SREはlocal-partialを維持する。
一般的なlogs/metricsによる原因診断・自動修復・本番受入・外部publicationはこの増分に含まない。
次はWP-L4-ML-001のTracking部分を先行し、既存評価receiptを専用local SQLite/loopback MLflow標準UIへ
取り込む。本来のSpark/JupyterHub依存とMLflow全体の受入は未達として残す。

## 2026-09-17: durable Data to Catalog handoff

[Data Engineer Bear](L2/WP-L2-AGENT-003_Data_Engineer_Bear.md)と
[Catalog Curator](L2/WP-L2-AGENT-004_Catalog_Curator_Bear.md)を小さな専用wrapperで接続。
固定保存先・排他lock・親directoryを含むfsync済みintentにより、同handoffのData起動は最大1回。
保存成功receiptとsource証跡を検証後だけ同Data Planを渡し、Catalog部分失敗は新attemptで再開する。
LangGraph自体の永続checkpointや副作用のexactly-onceとは区別し、unknownを再Data実行で埋めない。

実自然文handoff `cc018604c0b8407aadad52b0b0c58df7`、Data Bear
`e8ecab2de2c04627bbb10f78cf111537`、Data Plan `bd490f27d89b45ebb22a6f34f2ef3b9e`、
Catalog `175f84bd32a7403497bdfd7f884137af` が成功。uppercase結果は `[[1,SHIROKUMA],[2,POLARIS]]`、
materialization2回・owned2relation回収、historical2table/2edgeの4writes→repeat0writes。
同IDの実completed resumeは保存証拠のreplayだけでchild/API/model0。
前後1013files、独立metadata31endpoints、Pawprints385行が不変だった。
新coverage `42c0b1bcdf0b45709d6c25119c0c71e0` は6run・12/12edges。古いreportは保持した。

unsupported handoff `a30d6e1b7772494dbe50cf673068e6a3` / Data
`c2f97b6d37c247c58024e5c0cbefe96b` はexit2、adapter0/Catalog0。
実推論3callsは1125入力/136出力tokens・合計6605ms、cost NULL、cloud/retry0。
保存実OTel10spansとMCP2件の返却span ID相関を分け、既存評価120/120には加算しない。
Catalog取消はfailed監査/ERROR span/interrupted保存後に元の取消を再送出し、二次的な保存失敗でも維持する。
取消・partial再開はoffline focusedの検証であり、実機途中取消の実績とはしない。

handoff22/Catalog55 focused、全local976件（975成功＋標準環境optional OTel1skip）が成功。
設計80documentsと設計関連14testsの検証も成功した。
全文は`data-catalog-checks/8ee9569b05a04b7ca389364576e3fcfb9/local-regression.log`。
新service/外部publication/本番受入はなく、両WPはlocal-partialを維持する。
次はSREにcontainer termination/waitingの証拠を追加する範囲を検討し、現状のcause unknownを保持する。

独立最終監査 `data-catalog-preflight/de60ba1818d74bad9a0a289176e9f72f/verification.json` の
107checks/metadata48checksが成功。AH388＝旧376全行hash不変＋新12、Loki12件、保存実OTel10spansと
MCP2返却span IDを照合。保持Kube/3Docker/履歴4tables5DDL/fixture全snapshots/history/refs/全10table一覧は不変。
旧964files中963は厳密不変、admin markerのmtimeだけ既知差（content/mode/.env不変）、新180artifact hashを保存。
新metadata2table/2edgeだけを帰属させ、旧10table/10edge/anchors保持とcoverage6run12edgeをfresh APIから再確認した。

## 2026-09-17: verified lineage gaps completed

Catalog runs `48c698b868ad4b18bc69c5c3bebc65de` / `1bfb893944c04f98a463484e7386affb` で
未登録2 Data Plansにhistorical4tables/4edgesを追加。各4writes後のrepeatは0writes。
Qwen各314/96tokens、4106/2950ms、cloud/retry0・cost NULL。新coverage
`ea83feab78db4e2189d8adab339af346` は5run・10/10edges（09:16:39 UTC）。旧60%reportを保持。
独立audit `catalog-completion-audit/fdcf070415da47f082d8e0e5a39521dc` の84checks/metadata45checks成功。
旧6tables/6edges/anchors、Pawprints366全hash、Kube/3Docker/history4/5DDLを保全し、現Pawprints376。
新AH/Loki/実OTel各10件を相関照合、933files完全不変・認証markerのmtimeのみ既知差。
直前全949tests/80documentsを継承。次は同handoffでDataを二度生成しないdurableな連携入口。

## 2026-09-17: read-only historical lineage coverage

[WP-L5-LINEAGE-001](L5/WP-L5-LINEAGE-001_Lineage_completeness_dashboard.md)のrun
`4ef6d7943baf4e8c914f32f63381b9c9` が初回成功。5成功Data Plans・期待10edges中、
present6/missing4/conflict0/unknown0、観測complete・登録率60%・coverage未完了を分離した。
UUIDや証跡が不正なrunも一覧から消さず、途中変化は分母を保持してunknownにする。
API不達模擬はunknown10/missing0/rate NULL。HTMLの実表示・詳細展開・横はみ出し0を確認した。
固定login以後GETだけで、新model/metadata変更/新serviceはなし。
独立監査`metadata-coverage-preflight/18d58ceea4b34635be2e267336669a9f/verification.json`の31checksが成功。
23API endpointsのUUID/version/full hash、5source、保持6table/6edges、892filesと資格情報が不変。
focused34と全949件（948成功＋optional1skip）、設計80documents/14tests成功。local-partial。
次は2runの未登録4edgesを既存Curatorで登録し、保持entity不変とcoverage変化を検証する。

## 2026-09-17: Iceberg named tag and branch

[WP-L3-ICE-001](L3/WP-L3-ICE-001_Iceberg_branch_tag_clone_demo.md)のrun
`9ad2e966fd6c414698abca0706f97467` は08:53:27–08:56:17 UTCに初回成功。
A=480567632882700496の2行にtag/branchを作成し、main appendでB=8990253737475478975の3行へ。
名前付き読取はtag/branchともAを維持し、CASによるbranch移動後はtag A・branch/main Bを照合。
古いexpected AはHTTP409拒否し、metadata不変。3成功＋1拒否それぞれで同current snapshotと
file metadata一覧hashが不変。object内容byte hashの検証や別tableのzero-copy cloneとは区別する。
85手順、worker32/control28 queries。UUID/COMMENT一致の生成1tableを回収し、fixture全履歴/refsと
全10table一覧が元へ戻った。新service/image、権限変更、Pawprint追加、LLM呼出はなし。
41 focused、全local915件（914成功＋標準環境optional OTel1skip）、設計79documents/14testsが成功。
branchへの独立書込・保持table復元保証・本番受入は未検証でlocal-partial。次は既存Data履歴の
OpenMetadata lineage充足状況を、read-only reportとして可視化する。


独立最終監査 `iceberg-refs-preflight/8e22af705b4644928b783811e4fac7b7/final-verification.json` が成功。
保持Kube6Pods/27resources/6CM/3Secret UIDs、Docker3、履歴4tables/5DDL、fixture全履歴/refsと
全table一覧、Pawprints366全行hash、既存247証跡filesが前後不変。Pawprint追加は0。
保存receiptの41項目を再計算し、実Trinoでも捕捉したworker/control全60IDsがFINISHED、active0。
独立readback後に監査用tunnelも終了した。

## 2026-09-17: Data Bear natural-language entry

[WP-L2-AGENT-003](L2/WP-L2-AGENT-003_Data_Engineer_Bear.md)にUTF-8/512文字の
`--request`/`--request-stdin`を追加。固定fixture/3operationだけを型付きIRで選び、
採択IRの実行結果を独立oracleへ照合する。一般の自由文意図を常に正解するという主張はしない。
unsupportedはmaterialization0/exit2。本文/モデル生応答を保存せずhashとplan/usageを保持する。

新言い換え・曖昧/削除/未知列/injectionの7×2は14/14、既存6×2は12/12成功。
評価run `d87069723481431f96e87cc0d9d17b2c` / `fb2781b2c73a442facafd74a8ac913a6`。
自然文文字数実行 `fc906b457f834162acd80a35204a7c1c` はdbt2回・結果 `[[1,9],[2,7]]`、
4AH/Loki/実span相関と生成2relation回収が成功。DataOps `396f97befadd41b3af49bf4db9be4b1c`。
削除依頼の初回 `3f4315b3e32e410fbd9f14e25c1a4486` は正しく拒否したが監査helperの
success固定でfailedとなり、期待statusの両store照合を修正。元証拠を保持し、再実行
`cffc3740880c437aa883ee7d887b94c1` はunsupported/実行0/3event相関まで成功。
新service/image・MTPLX設定変更・cloud呼出はなし。新評価のcase群は既存評価と分離して集計する。


独立最終監査 `data-request-audit/34a457215c244a589c790d06ab8a16e4`（08:32:49 UTC）が成功。
保持Kube6Pods/27resources/6CM/3Secret、3Docker、履歴4表/5DDL、fixtureのschema/rows/
全snapshots/history/refs/headとIceberg全table一覧、旧330eventsが不変。
全5試行の36追加event（success32/blocked4）を帰属させ、AH366件、workflowのLoki/実spanも一致。
正常runのowned2tablesとactive query残留0、拒否2試行はadapter生成なし。
失敗試行を含む29推論の実測usageは12620入力/633出力tokens、cost NULL、cloud/retry0。
既存62証跡と今回163filesが監査中不変で、workflow証跡にraw要求全文の保持は検出されなかった。
Data91＋SRE64 focusedが成功、全local回帰867件（866成功＋標準環境optional OTel1skip）も成功。
同OTel結合は専用venvで成功している。

## 2026-09-17: warehouse idle suspend/wake and local CLI

[WP-L3-VW-002](L3/WP-L3-VW-002_Idle_suspend_and_wake.md)の同期run
`97b8a7289b2e42e496a1d87bddd2b4ad`、CLI session `c41cf9f753c14275837ee9f453ed2ad0` が成功。
各7固定requestで6SELECTの25nation行/実node IDを確認し、停止中XSはSへ未配送。
full query一覧0件でも未消費handleがあればbusy-defer、完了後だけfresh idle/drainを確認し、
Gateway deactivate→同UID Deployment scale0→scale1→新Pod UID/process ready→activate。
保持Sのprocessは不変。一般の未追跡producerやmulti-worker drainの証明ではない。

実Go CLI6commandでprepared wake noop、chill、chill同ID再送、別IDchill noop、wake、wake同ID再送。
4owner操作のreceipt hashと6応答が一致し、再送でscale操作は増えない。private同UIDsocketで
受付10分/16接続、wake後15秒replayのみ、ownerが処理と限定cleanupを完了する。
各17UID明示回収＋旧XS Pod消失、4Pawprints/Loki/実OTel spanを保存・読戻し。
同期trace `2823135a5def108632ed71493ce72530`、CLI trace `ced9a0cbc583bf91dbeed4541eddf247`。
初期4実行失敗とrecover履歴、CLI runtime前import失敗はそのまま保持した。
生成名/空EndpointSlice/終了Pod/非同期health/TCP再接続/script入口を実挙動へ合わせて修正。
汎用CRD、HA/tenant認可/常駐service/本番受入は未達。次のData Bear入口へ進む。

独立最終監査 `warehouse-lifecycle-audit/80d8bfbffe19418b82b249aed451dd92`（08:17:20 UTC）で、
保持6Pods/27resources/6CM/3Secret UIDs、3Dockerの全設定/起動時刻/restarts、履歴4表の全行hash/5DDL、
従来22証跡filesが不変。Pawprints330＝従来311件不変＋今回19件、同じLoki19件と保存span IDsを照合。
失敗4runの11件も残し、成功2run各4件と区別した。全6runtime runsと初CLI startup failureの残留0。
CLI6応答/4operationのraw SHA256、実binary hash、同IDの完全同一応答、scale2回だけを独立再確認。
全回帰836件（835成功・標準環境のoptional OTel1skip）後、更新済みfocused112件がすべて成功。
同OTel結合は専用venvで成功。Go test/vet/race、設計79documents/関連14tests、diff checkも成功。

## 2026-09-17: isolated Gateway VirtualWarehouse

[WP-L3-VW-001](L3/WP-L3-VW-001_Trino_Gateway_VirtualWarehouse.md)のrun
`2823f7f9bc0842c1adf54f37b0b9cc0e`で8/8case成功。XS/Sの実node ID＋25nation行、
各3queryのterminal履歴、最初の逆class付き継続GET XS5/S4回を照合。
未知/欠落routeは500・空bodyかつ両backendでSource0件。未知classにはlegacy groupも付与。
両固定DROPはPERMISSION_DENIED、前後の正常SELECTは同じ結果。認証/warehouse権限の証明ではない。

Gatewayのhistory保存falseをPG実データ0件で確認。PG18.4/Flyway4migration/backend2件、
5363byteのprivate dump→別新規DB復元→schema/index/data＋元DB再読一致。
前後readiness4チェック・両backend HEALTHY、4UID回収、recover追加削除0。
XS/Sは同一Pod/hostを共有するのでtenant/fault isolation/HA/performanceは未観測。
49 focusedを含む全回帰740成功＋標準環境1skip（同OTel結合は専用venvで成功）、78documents。
証跡`gateway-warehouse-preflight/7c1d0d4027eb4e9aa03fbffc114fded5`。
独立after照合で保持Kube6Pods/27resources/6CM/3Secret UIDs、Docker3の全設定/起動時刻/restarts、
Pawprints311件＋履歴4表3/72/1/6の全行hash/5DDL、保護19filesのhash/mtime/modeが完全不変。
live/plan-only計8資源名の残留0。保存historyはuser/rawrowsを省くため、その部分はlive producer検証、
PG復元もprivate dump/hashとreceipt再照合であり独立再実行は行っていない。

## 2026-09-17: live identity audit visibility

run`b94184c624174f0c8f01fc00cae8d7d7`の全15case/61decisionsとcleanup成功を、
新しい実測時刻・case観測時間で16Pawprints/16Loki/16OTelへ記録。7success/9blocked、
うちOPA拒否3件だけpolicy flag=true、認証拒否6件false。推論usage/costはNULL。
送信前runtime同一性、journal、両store読戻し、保存spanのname/status/task/root/query/decision相関を検査。
途中失敗でもcleanup結果を残し、全配送/照合が終わるまで実験成功にしない。
MCPの既存agenthouse_recentが全16件を返し、source metadataと一致。新MCP監査は1件。
Grafanaの同Task/Trace絞込みに拒否9件を実表示。詳細OPA相関はprivate telemetry/OTelの限定射影。
既存Kube6Pods/27resources/6CM/3Secret UIDs不変、3run計12生成resources残留0。
独立after照合でPawprintsは311件＝保持294件完全不変＋新identity16＋MCP1。残4表の全行hash/DDL、
Pawprint DDL、AH/Loki/GrafanaのID/Config/HostConfig/起動時刻/restarts/mount/ports/networksも一致。
保護31filesのhash/mtime/modeを保持。実結果行は非保存のため再照合はcount/matched射影までとする。

59 focused全成功、全回帰691成功＋標準環境1skip（同結合は専用venvで成功）、76documents検証。
証跡`trino-identity-telemetry-preflight/c96c6414e88c4531a7f21b7f2c760a3c`。
次は隔離Gateway/2backendのVirtualWarehouse機能実験へ進む。

## 2026-09-17: authenticated Trino identity

[WP-L3-SEC-001](L3/WP-L3-SEC-001_Keycloak_OPA_native_RBAC_integration.md)の新しい3container Podで、
Keycloak26.7.4の実発行JWT5件をTrino483/OPAへ渡した。寿命300秒/署名/issuer/audience/principalを検証。
正常6、認証拒否6、認可拒否3の全15caseが一致。認証拒否は401＋OPA未到達、
両user header偽装はlab_mask→lab_adminのImpersonateUser拒否。前後readiness4応答も保存。
成功run`8121b529b1844086bfa489a1336a7b60`、61decisionsのquery-ID相関52/直列window限定相関9。
初回の必須claim検査失敗を保持し、空default scopesに明示sub mapperを追加して再試行した。
2run計8resource残留0、初回recoverは削除0、既存6Pods/27resources/6CM/3Secret UIDs一致。
秘密はprivate fixtures/planに限定し、通常receiptの非混入とprivate mode/hashを再検査。
独立再検査は保存射影の照合であり、実token/完全結果行の検査はlive runnerの証拠と区別する。
証跡`trino-identity-preflight/fc0e1b3343bb411b992cb55fc18e76c5`。
全回帰664 tests（identity focused32を含む）・設計76documentsが成功。
次はlive case観測のPawprints/Loki/OTel連携。browserSSO/rotation/role同期/StarRocks/本番受入は未達。

## 2026-09-17: isolated Trino OPA authorization

[WP-L3-SEC-001](L3/WP-L3-SEC-001_Keycloak_OPA_native_RBAC_integration.md)の使い捨てTPCH-only Podで
全25行・行制限1行・列mask25行・複合1行・user/column拒否0行・header偽装の限界・OPA不達0行を確認。
成功run`02986c45d05247d0918cd7cae440f380`。正常decision59件はquery ID+user52件、
query IDなしExecuteQueryの限定照合7件として保存。認証済みidentityとは主張しない。
起動完了とHTTP200の差、Java例外名のJSON表記差を検出・修正し、先行failed2runも保持。
別PodのOPA不達は固定QueryFailed＋ConnectExceptionを要求し、一般errorを合格にしない。
個別readiness応答は保存しておらず、query前後の必須チェックを通ったコード経路として記録。

既知UID/resourceVersionだけを削除し、未知createはdeferred。保存manifestを独立検証する復旧は
現行renderer/config/Rego/PINS変更に依存しない。初回failedrunのrecoverは削除0で成功。
独立検証で3run合計15生成UID残留0、既存6Pods/27resources/6CMが完全一致。
全回帰632 tests、native OPA policy8 tests、設計76documents。保持Trino/Lakehouseへの接続なし。
証跡`trino-opa-preflight/d14316b781e74ec0ab18804df9ac8739`。
本WPはlocal-partialで、次はKeycloak tokenとTrinoのidentity bindingを確認する。

## 2026-09-17: retained history read-only UI

[クエリ実験の履歴](http://127.0.0.1:18030/d/shirokuma-query-history-local)へ5表を追加。
benchmark成功1/失敗2と72query sides、Query Doctorの1run・実測4件・補助2件を表示。
全期間の固定SELECT/FINAL/行数上限で、来歴不明・未観測・測定0を区別する。
既存readerの4table SELECT追加を既知XMLの原本保存・hash照合・atomic replaceで実施し、
同credentials/limits/containersを維持。再enableは変更なし、up/smokeもready。
5panelのAPI/専用reader前後SELECT一致、UIの失敗filterとNULL・精度表示を確認。
API証跡`grafana-history-verification/e4f54e9e4d2740c5809b2afc5150c025`。
保全54項目、5tables全hash/DDLと145元files不変。全回帰609 tests/76documents。
証跡`grafana-history-preflight/8e1933d437a44e36ae11923a3f0f17f2`。

## 2026-09-17: captured own-query history

[WP-L3-CH-001](L3/WP-L3-CH-001_ClickHouse_AgentHouse.md)の追加2tableへ、既存Query Doctorの
1run/6captured query IDsを保存。4件はhistory実測、2件の補助読取は`not_target`とNULLを維持。
保存observer射影の由来を明示し、Gateway/server監査と混同しない。稼働中の初期failedは
終了span・固定終端error等の証拠がない限り、inventoryに残してapplyを拒否する。
初回`b26cf6f6c753459abfd49f581812391b`は1/6追加、再実行`bbe00a0c30c2485fa94d02ca3a94f07f`は0/0。
19 focused testsを含む全回帰592件・設計76documentsが成功。新たなTrino/LLM呼出なし。
独立再照合で元8filesと既存3tables（監査294・benchmark run3・query side72件）の全hash/DDL不変。
private証跡は`query-history-preflight/509e684aee2b4c28a99cea9cffbdead8`。
未達の継続収集/Gateway連携/履歴画面/実費計測を残し、隔離Trino OPA増分へ進む。

## 2026-09-17: failed runsを含むbenchmark履歴

[WP-L3-CH-001](L3/WP-L3-CH-001_ClickHouse_AgentHouse.md)の追加2tableへ、保存済みSF10の
成功1・失敗2runと72query sidesを取込。記録36比較を再照合し、予定残り30は未観測とした。
旧SQL hash欠損はunknown group、NULLと測定0を分離。元SQL/結果行/生errorをDBへ渡さない。
初回`e2c5fd7ea9c24fd1818846399aa2846b`は3/72行、再実行`998612d03fb44bbc86a8df3306262f10`は0/0。
05:32 UTCに全論理行一致、元137filesとPawprint schema/294件不変を独立確認。
固定runtime/所有table定義・衝突拒否・部分挿入の再開を21focused testsで検証。
全回帰573 tests/75documents。証跡は`benchmark-events-preflight/013ba0f37c944c69a7f6710cd473ff7d`。
新たなTPC-H/LLM呼出やruntimeは増やさず、Grafana readerの権限も変更しない。

## 2026-09-17: AgentHouse read-only Grafana

[WP-L3-CH-001](L3/WP-L3-CH-001_ClickHouse_AgentHouse.md)へnative ARM64 Grafana13.2.2と
署名確認済みClickHouse plugin4.21.3を追加。`127.0.0.1:18030`のanonymous Viewerで
既存Pawprintsの失敗/ブロック、Task/Trace、処理別p50/p95、モデルtokensの欠測を確認できる。
専用SELECT-only readerとDB側の30秒/256MiB/1万結果行制限を使用し、writer資格情報を渡さない。
未測定費用はNULL、event成功とTask/PR全体の成功は区別する。

内部bridge単独のport publish不成立とdriverのtimeout+5秒を実機で検出し、
Grafanaだけのfrontend bridgeとplugin25秒設定で解決。全パネルの厳密percentencode filterを
実DB/画面で確認し、40APIcasesの列型/値/NULLを前後SELECTで一致させた。
empty resultのplugin形式差は初回failed証跡を残して検証器を修正した。

停止中network attachmentの誤報も修正し、05:25 UTCに実切断→復元を確認。
同Grafana ID/volume/env/credential、既存CH ID/image/env/mount/port/start/restartを保持し、
監査294件の全bytes/hash不変。05:26 UTCの再API照合40/40、全回帰552 testsが成功。
最終API証跡`grafana-verification/9785773e72e540f2ad63c014db659b70`、
保全/復元証跡`grafana-preflight/b781f2d079d8469c9e1bf274ab83d374`。
実費計測・benchmark取込・Trino query履歴持続化は未達で、後続増分として扱う。

## 2026-09-17: Iceberg snapshot / restore sandbox

[WP-L3-ICE-001](L3/WP-L3-ICE-001_Iceberg_branch_tag_clone_demo.md)の使い捨て2tableで
A（2行）→B（3行）、Aの過去参照、別tableへAのCTAS復元、sourceのAへのrollback、
rollback後のB過去参照が成功した。Run `94a2396b745747c693919337411acc9d`、24手順。
初回`9c127032f70b4bad833a9d3141368081`はTrino483履歴期待の誤りでfailedを保持。
固定versionのsourceに合わせ、current refs/祖先flag/実query結果を厳密照合して修正した。

04:51 UTCに合計4生成table・owned active query残留0、保持fixtureの5種の観測が前後一致。
`tmp/local-experiment/iceberg-sandbox-preflight/8ec05a4823724073bfbd070956af8ec2/`へ独立検証を保存。
20 focused tests、全回帰506 tests、設計75documentsが成功。追加runtime/model呼出なし。
CTASは物理コピーで、named branch/tag・zero-copy clone・保持dataの復元保証は未達。
次は[WP-L3-CH-001](L3/WP-L3-CH-001_ClickHouse_AgentHouse.md)のGrafana read-only表示を進める。

## 2026-09-17: Qwen Policy Gatekeeper

[WP-L2-POL-001](L2/WP-L2-POL-001_Policy_Gatekeeper_Bear.md)はneutralなnative15候補と
操作境界10候補を4batchで分類。host truthと全件一致後、固定native15件をserver dry-runで検証。
入力3907/出力1171tokens、推論33.125秒。allow2/deny12/対象外control1が全件一致し、
7監査と保存OTel、completed checkpointも一致。モデルは権限付与・policy変更をしない。
`tmp/local-experiment/policy-bear/f37433c5b92d462a8ea69f4465c334b5/`に保持。
04:31 UTCに保持6Pod/16policy resourcesの前後不変・残存object0を独立確認した。

8合成case×2は16/16、16監査、12454/2768tokens、81.187秒。
新規core12/Bear18と既存native17を含む全回帰486 tests、設計73documentsを検証。
評価harnessは6suite全8receipt/8groups valid、欠損/不整合/重複/保存失敗/未観測0。
Qwen94callsとmock10を分離し、入力16filesと以前のreport9filesはhash不変。
最終report `tmp/local-experiment/evaluation-reports/076b0abbce144d179e7c223c0126058d/`。
任意policyやリスク品質、署名/CVE保証、本番Tier/PR承認は未達のまま保持する。

## 2026-09-17: Query Doctorと全評価receipt集計

[Query Doctor](L2/WP-L2-AGENT-005_Query_Doctor_Bear.md)は固定2SELECTの結果・同文JSON EXPLAIN・
自身のquery historyを照合し、Qwenが固定SHOW STATSを追加選択した。491/36 tokens、1.696秒。
row count estimate=2を取得し、列統計unknown6項目、root cause未確定、性能改善未測定を維持する。
4監査/保存spanが一致。04:20 UTCにmodel evidence/assessmentを独立再計算し一致、
自身の実行中query0、保持fixture不変。`query-bear/6aae039137f748548db53b59f8e2d3b7/`に保持。
8 golden×2は16/16、16監査、8022/586 tokens、27.881秒。

[評価harness](L2/WP-L2-EVAL-001_Agent_evaluation_harness.md)はモデル/APIを呼ばず全UUID receiptを検査。
5suite・7receiptすべてvalidで、欠損/重複/不整合/保存failureは0。Qwen78/78監査済みcallsと
mock10を分離し、SRE旧6case/現8caseも別groupにする。これは一般的なモデル品質の合算ではない。
元14filesのhash不変を再確認。reportは`tmp/local-experiment/evaluation-reports/14f84889fc1f4fb99c2e4309113989d8/`。
開始日時・code/fixture版・chronological初回成功率・費用はunknown、PR指標/MTTP等はN/Aとする。
Query37＋harness14のfocused checksを含む全回帰455 tests、設計73 documentsが成功。

## 2026-09-17: Qwen Catalog Curator

[WP-L2-AGENT-004](L2/WP-L2-AGENT-004_Catalog_Curator_Bear.md)をlocal-partialとして実装。
固定Data Planのproject/SQL/hash・dbt結果/依存・Dagster保存DB・cleanupを照合し、
Qwenは不足6項目と固定evidence IDsだけを選ぶ。Hostが履歴説明と既存experiment ownerを生成する。
新規6table/6lineageを実OpenMetadataで読戻し、全3caseの再適用は0writes。
name_lengthsのworkflow全体再実行ではQwenもnoopと判断し、table ID不変・0writesだった。

4workflowで計20監査eventと保存OTelを照合。推論は314/96 tokens・2.978秒、
再実行296/42・1.590秒、他2caseは314/96・2.958秒と314/96・2.980秒。
6合成case×2は12/12（3682/862 tokens、28.491秒、12監査読戻し）。
03:59 UTCの独立検証で事前のfixture/team/service/schema API結果が完全一致した。
`tmp/local-experiment/catalog-preflight/a17284a0e35441e09ac690855ace15bf/`に保持。
実行UUID・backup・再実行方法はWP参照。Catalog50 tests、全回帰404 tests、設計72 documents。

LineageはManual sourceで、削除済みSQL relationの履歴である。一般的な説明/owner推定、
任意entity更新、本番受入は対象外。API部分失敗はsafe journalへ残し、rollback成功と誤認しない。

## 2026-09-17: Qwen Data Engineer Bear

[WP-L2-AGENT-003](L2/WP-L2-AGENT-003_Data_Engineer_Bear.md)をlocal-partialとして実装。
モデルは固定fixtureに対する3変換の型付き計画だけを返し、hostが専用dbt projectを生成する。
文字数・大文字化・行数を実データで検証し、それぞれ2回のmaterializationと全dbt testsが成功。
入力/出力tokensは478/23、430/23、400/23、推論秒は1.335、1.328、1.310。
各4件の監査eventがClickHouse/Loki/OTel間で相関一致した。
6 golden要求×2回は12/12成功（4754/262 tokens、15.723秒、12監査読戻し）。

Data Bear証跡UUIDは`a95175c8abc74dd897699a8e38f3acc6`、
`ddf4422d47294b8d912172d284024f49`、`e364c200e2c648e3b1ec79923c6acbff`。
別途`data-plans/eb8902c0a4e54c8591dcbfee02550927/`で意図的test失敗→retry成功を確認。
03:43 UTCに4runのhash/dbt lineage/resultsと実fixtureを再照合し、生成8relation/query残留0。
詳細receiptは`tmp/local-experiment/data-bear-preflight/b09b846899ac41d0847f8c6eb612fb81/`。
全回帰354 tests、設計69 documents。範囲は固定2列2行・3変換だけで、
一般的なデータモデリングや任意コード生成、業務データ変更、本番受入は含めない。
次はこの成功証跡を使うCatalog Curatorの限定履歴登録へ進む。

## 2026-09-17: 5 runtime groupsを含むQwen SRE

Supersetのready判定をowned container running＋固定loopback `/health`のHTTP200/OKへ修正。
停止/HTTP異常と通信不能/観測不明を区別する。proxy/redirect/認証なし・単発3秒・body上限16bytes。
10 testsと実HTTP確認が成功した。

引数なしMCP `local_platform_status`を追加し、Gatewayは9固定read toolsとなった。
5群の固定名・bool/status・全体集計・取得時刻を検証し、reason/free textをモデル出力から除く。
Scope確認＋statusを75秒worker deadlineへ入れ、timeoutでは新規process groupだけを回収する。
Gateway31 tests、実MCP12 callsのCH/Loki/OTel相関が成功。最終証跡は
`tmp/local-experiment/gateway/smoke-1aff3c763c9c41e490cc59a16d51505a.json`。

SREの初期観測はdoctor/Pods/Events/platformの4源。Runtime not-readyをcritical、unknownを
未観測として区別し、critical Pod＋runtime同時発生時には両方のreadを必須にする。
実Qwenは5群readyと過去restartを区別し、Pod再観測を選択。773/51 tokens、3.675秒、7監査相関。
保存射影からhash/evidence/triage/comparisonの再計算一致を確認した。
`tmp/local-experiment/sre-llm/0c69d3ef5ebc401ca3d27bdb4bd98ea7/`に保持する。

8合成ケース×2で16/16成功・16監査readback、入力10308/出力1000tokens、推論合計40.678秒。
証跡は`tmp/local-experiment/sre-evaluation/22615db4c5344a81a261a2086437113b/`。
Runtime差分は状態だけの比較で、instance identity・復旧・原因を断定しない。

Admission適用後の実Failed Pod drillも成功。4初期read＋Pod/Event followupの計6read、
8監査相関、854/146 tokens、4.930秒。作成UID `6f55c9f5-83e9-47a9-b69d-4bedfdadbb89`だけを削除。
既存6 PodのUID/phase/ready/restartsは前後一致、drill残留0。
`tmp/local-experiment/sre-drill/4a856109b32f408185aebe247508c1b2/`に保持する。

全回帰はローカル295＋設計14＝309 tests、設計68 documents。SRE64、Gateway31、
platform-status10、native-policy17のfocused checksを含む。課題はlogs/metricsを使う原因診断、
一般的な修復案の品質と自動修復、production/human受入。これらの達成は主張しない。

## 2026-09-17: namespace限定native admission baseline

**WP-L0-POL-001のlocal機能**として、7種類の既存CEL制約を8個のnative VAPと8 Deny bindingsへ
投影した。Service/Ingressはnativeの型検査に合わせて分割。他6制約の式と元Kyverno source、
過去のimage/CVE判定は変更しない。追加controller/image/volumeなし。
[ADR-0035](../07_ADR/ADR-0035_Use_native_admission_baseline_for_local_experiments.md)と
[WP](L0/WP-L0-POL-001_OPA_Kyverno_baseline_policies.md)に判断・境界・再実行を記録した。

既存6 Pods/3 Services/6 templatesの事前照合は違反0。初回の共通式はIngress型警告で
binding有効化前に停止したため、型別に分割し、今回作成した未稼働の定義1個だけを
UID/RV条件付きで再作成した。最終8 policiesはcurrent generationの明示的な型検査完了・警告0。

実APIでallow2、deny12、対象外namespace control1の15/15が成功。全件server dry-runで
Pod/Service/Ingressは永続化していない。拒否のpolicy/binding/messageを照合し、
単なる通信・RBAC・Schema・CEL評価エラーを成功と数えない。専用BindingのみをUID/RV条件付きで
撤去して再適用し、policy UIDを保持、新Binding UIDを確認後、15/15が再成功した。
既存6 PodのUID/phase/ready/restartsは前後一致、2026-09-17 03:04 UTCの5群はready。

証跡: `tmp/local-experiment/policy/411294d0640a43d1a59155a834469301`（初回成功）、
`4259b9e128b0448b86c20e05336c173b`（撤去）、`539ef89674b746859a443f60afb51c2d`（再適用）、
`3a501f3b4d384238bf0ece97ab6db566`（最終smoke）。保持Pod比較は
`tmp/local-experiment/policy-validation/0acf015be3674b6397524e22f17c919b/after.json`。
17 focused testsが成功。`rtk make local-policy-status local-policy-smoke`で再確認できる。
controller autogen/background report、live ephemeral probe、image attestation、本番受入は未達。

## 2026-09-17: 使い捨てFailed Podの実機SRE drill

**WP-L2-AGENT-002の実障害検知実験**として、新規の専用Pod一個だけを`exit 1`で失敗させた。
モデルはそのPodを`WORKLOAD_FAILED`のcritical所見として選び、実MCPで再観測した。
初回・followupとも同UID・同Failed状態を確認し、既知の過去restart所見と区別できた。
Qwen入力735・出力90 tokens、推論3.398秒、6監査eventの相関・readbackが成功した。

作成物は`sre-drill-f5573fa58e5d4ffc853bd55b02e42a9f`、
UID `10d94a3c-1d29-484a-8f6d-d1e410c9a2e7`。終了時にowner/run labelsとUIDを再照合し、
UID/resourceVersion precondition付きDELETEで削除した。残留drill Podは0。
既存6 Podのname/UID/phase/ready/restartを前後比較して完全一致し、保持workloadを変更していない。

証跡は`tmp/local-experiment/sre-drill/f34f175d82934d5090244ca2fbfb4340/`。
`creation-intent.json`、`created.json`、`summary.json`、nested `workflow/`、
`retained-pods-verification.json`を保持する。作成結果やreceiptが不確かな場合、または
同名でもUIDが変わった場合に名前だけで削除せず、cleanup不明はdeferred・全体failedとする。
この試験Podのcleanupは既存serviceの自動修復ではない。

既存Trinoの固定local image digestとUID10000を使い、pullなし、50m CPU / 32Mi、
非root・readOnlyRootFilesystem・dropALL・RuntimeDefault、token/volume/host pathなしで実行した。
restartNever、deadline60秒。事前server dry-run成功、専用labelsは既存Service/controllerと非一致。
追加image・weights・resident service・data volumeはなく、保存増分は小さなprivate証跡と監査。

14 focused drill testsが成功し、全ローカル回帰245件＋設計14件の計259 testsを検証した。
再実行は`rtk make local-sre-qwen-drill`。deferred時はcreated receiptと現物UIDを照合し、
未確認の作成物や保持データをまとめて消さない。Colima resetは復旧手順に含めない。

事前調査時のlive clusterにはKyverno CRD・追加admission policy/webhookが存在しなかった。
従ってこのdrillのガードをcluster-wide policyの適用証拠にはしない。
その後のローカルpolicy導入結果は上記native admission節に分けて記録した。

## 2026-09-17: QwenによるSRE読み取り確認と再観測

**WP-L2-AGENT-002のlocal機能**としてLangGraph `collect → triage → followup`を追加した。
初回MCP doctor/Pod/Eventの3観測を分類し、Qwenが根拠IDに対応する最大2種類の追加readを選ぶ。
固定のcontext/namespace/引数だけで再観測し、Pod/Event UID・状態・count・取得時刻を比較する。
モデルが生成したshellや引数は使わず、重大Pod所見の確認脱落・未知の根拠・不適切なtool・
healthの改変・root-cause断定は実行前に拒否する。Cloud、GitHub、workload修復は実行しない。

実機では6 PodのうちPolaris/PostgreSQL/Trinoの過去restartを根拠にPod再観測を選んだ。
最新runの累計restartはそれぞれ6/4/1、約3.56秒の間隔でUID・状態・回数に変化なし。
`attention-required`と`root_cause: undetermined`を保持し、障害発生中や回復とは断定しない。
doctor/Eventは再選択されておらず`not-reobserved`を明記する。
Qwen入力656・出力83 tokens、推論3.127秒、cost NULL。4 MCP + 1 model + 1 assessmentの
6件をClickHouseから読戻し、Lokiのtask/trace/span/actionとモデルusage/latencyを照合した。
LangGraphの完了checkpointとlocal OTel spanも確認した。

最新証跡は`tmp/local-experiment/sre-llm/0979b832f0284b15a733cb7ee1ccdd25/`。
`observations.json`には自由記述を除いた固定項目を保持する。保存した4 receiptを再読込し、
元観測hash、followup hash、モデル向け根拠、比較結果の4項目が再計算で一致した。
結果は`replay-verification.json`。最初の成功runも`3a6c80e230094f329228e4e6c2f2fbe4`に保持。

正常/意図Flux停止、過去restart、2つの重大Pod異常、Warning、doctor degraded、Event打切りの
6合成ケースを各2回評価し、**12/12成功**した。入力計6,244・出力計538 tokens、
推論合計23.506秒、12件すべてのPawprintを即時読戻しした。
証跡は`tmp/local-experiment/sre-evaluation/2670bf3683a94a19aa6d79ea48423090/result.json`。
合成評価はMCP呼出し・故障注入をせず、traceは相関IDでありOTel spanを生成しない。

Gateway観測にはUIDと取得時刻、Event最終観測時刻とtruncatedを追加した。
Eventの初回時刻を繰返しEventの直近時刻へ流用せず、作成時刻も発生時刻と混同しない。
再作成Podの同名・UID差、欠測、不完全一覧、逆順時刻、未再観測を区別する。
Python 3.9と3.12の小数秒解析の差を回帰で発見し、明示的な精度正規化で修正した。

focused testsはcontract22、runner/provider/evaluation17、Gateway19件。
全ローカル回帰231件＋設計14件と設計66 documentsが成功。
既存の固定ルールSREも4監査eventで成功した：`tmp/local-experiment/bears/f78c933a281f4675972ce8c7d8f62436/`。

```sh
rtk make local-sre-qwen-smoke
rtk make local-sre-qwen-eval
rtk make test-local-sre-llm
rtk make verify-local-experiment verify-design-context
```

既存MTPLX設定・weightsとworkloadは変更せず、追加常駐サービス・volumeはない。
保存増分は小さなprivate観測receiptと監査。戻す場合は`local-sre-smoke`を使い、保持証跡は削除しない。
これは読み取り確認を選んで比較する機能であり、logs/metricsに基づく原因診断や自動修復、
監視の常駐化、本番・人間受入は未達のまま残る。

## 2026-09-17: 既存MTPLX Qwenへの実LLM接続

**WP-L2-LLM-001のlocal実LLM機能検証**が成功した。OWNER指定の
`qwen3.8-flash-next`を`http://127.0.0.1:8001/v1`で使用する。
APIのmodel ID完全一致と32,768 context、hostの`127.0.0.1:8001` listenerを確認した。
MTPLX 2.11.2・MTPなし・weights約115GBはOWNER提供の構成情報で、再インストールや
weightsの取得、server設定・ログイン時自動起動の変更は行っていない。
OpenAPIで確認した`enable_thinking=false`を明示し、出力512 tokens、timeout 60秒、retryなし。
入力は最大8 KiBの固定instructionsと最大16個の短いmodel識別子に制限する。
これはbyte境界であり、32K全長や最大context性能の評価ではない。

最初の単純JSON probeは0.627秒、入力46・出力14 tokensで成功。
続く実`plan → MCP dbt_list_models → summarize`は2回とも成功し、
`mart_fixture` / `stg_fixture`、件数2、保持されたverified runの有無を完全照合した。
モデルへ渡すのは識別子一覧とbooleanだけで、任意tool payload、path、run tokenは渡さない。
verified runは過去の検証記録を表し、cleanup済み生成tableの現在存在を意味しない。

実測4推論のclient wall timeは0.518 / 1.731 / 0.482 / 1.589秒、
入力計376・出力計128 tokens。AgentHouseから8件を再読込し、task/trace、
provider/model、実測tokens/latencyが元の応答と一致した。価格未評価のcostはNULL。
別枠のColima→`host.lima.internal:8001`推論も0.811秒で成功した。
証跡は`tmp/local-experiment/llm/cb4e7e837bc7425b9e634b23b7521ca7/summary.json`。
mockの回帰2 runsも成功：`tmp/local-experiment/llm/859f888874934d569d1003f450fdf1fa/summary.json`。

応答検証をhost/VMで共通化した最終run
`tmp/local-experiment/llm/8c79338e13b2474784dc3f636f219f88/summary.json`も成功。
VM probeの入力56・出力13 tokens、0.733秒をgraphとは別のreceiptへ保持した。
後続graphの4推論は入力計376・出力計128 tokens、0.519–1.603秒で、8監査eventと一致した。
途中で応答や監査保存が失敗しても、既知の使用量・時間・配送成否はprivate証跡に残す。

さらに、空一覧、1件、別名3件、未検証記録、16件上限の5合成fixtureを各2回評価し、
Qwenは**10/10**成功した。必須keys、モデル名、件数、booleanが毎回一致し、
10件の`llm.eval_summary`をAgentHouseから即時読戻しした。入力計1,510・出力計644 tokens、
推論時間合計19.110秒、1回0.782–4.910秒、観測した入力+出力の最大418 tokens。
traceは評価相関用IDであり、この評価コマンドはOTel spanやMCP tool呼出しを生成しない。
証跡は`tmp/local-experiment/llm-evaluation/478640a6721444be8e9b15ee6518dd18/summary.json`。
mockも同じfixtureで10/10成功し、usage/cost NULLを保持した。
再実行は`rtk make local-llm-qwen-eval`（mockは`local-llm-eval`）。
これはWP-L2-LLM-001の入力追従検証であり、WP-L2-EVAL-001のPR成功率・修正率・MTTPは測っていない。

focused testsはLLM31件＋評価10件。`make verify-local-experiment verify-design-context`は
合計199 testsと設計66 documentsが成功した。fixture捏造、誤count/flag、incomplete JSON、
fail時usage、監査障害時停止、VM失敗receipt、cloud実行拒否を検証する。

Colima停止は検出時の事実で、停止原因は未特定。16 CPU / 96 GiB / 400 GiB設定を保持して
再開し、既存container・volumeを使って5 component groupsを復旧した。
Colima設定hash、active Docker/kube contextは不変。Seaweed8Gi、親Flux停止を維持した。
非モデルMCP smokeは8 tools、11監査eventのClickHouse/Loki/OTel相関が成功。
復旧証跡は`tmp/local-experiment/recovery-20260917/summary.json`。

再実行：`rtk make local-llm-qwen-smoke`。専用profileは
`tools/local_experiment/llm/providers.qwen-local.json`、default mockは変更していない。
応答のmodel ID、finish_reason、JSON、要約事実の不一致は拒否し、失敗時も有効なusageを保持する。
戻す場合は`rtk make local-llm-smoke`を使うだけで、既存MTPLX serviceや保持監査記録は削除しない。
新規resident component・weights・tableはなく、追加保存は小さなコードと監査/証跡のみ。

Cloud smokeは未達のまま保留するが、local実LLM作業の依存条件にはしない。
この結果は固定の読み取りinventory taskの検証であり、汎用モデル品質、SRE診断、自動修復、
本番・人間受入の完了ではない。

## 2026-09-16: TPC-H SF10 全22クエリの完走

**WP-L1-BENCH-001のローカル機能検証**が成功した。Trino 483の固定query corpusを使い、
SF10の8テーブル・86,586,082行をIcebergへ生成した。全22 queryでgenerator参照結果と
Iceberg結果が一致し、実行queryの終了確認後、今回の8 tableを所有コメント確認付きで削除した。
保持fixtureの前後一致と生成table残数0を確認した。

成功runは`3ca015b7c9d2431c9997b23dda777cb9`、完了時刻は2026-09-16 09:52:05 UTC。
SF10は公称10GBのsource規模で、実測Parquet data filesは2,286,204,977 bytesだった。
22 queryのclient wall time合計はgenerator参照174.35秒、Iceberg66.81秒。
generator・client・測定対象を同居させ、cacheや並行実験を分離していないため、
公式TPC成績・本番capacity・他製品との性能比較として扱わない。
100GBは実行しておらず、別の容量・実行時間評価として扱う。

環境はColima 16 CPU / 96 GiB / 400 GiB、Trino 8 CPU / 16 GiB（heap 12G、query 8GB）。
SeaweedFSは次の実障害の修正で2 CPU / 8 GiB、memory request 1 GiBへ変更した。
既存PVC、保持dataset、backupは変更・削除していない。

- 最初のrunはQ15で不一致。同じgenerator queryだけでも0行→1行が再現し、
  CTEを二度展開したDOUBLE SUMが`2194132.8166000005`と`2194132.8165999996`に分かれた。
  Q15の金額・割引だけをDECIMALへ変換し、両経路へ同じ式を適用した。
  全59,986,052 lineitemで元の2桁金額・割引値を保持することを確認した。
  vendor SQLとchecksum、比較条件は保持し、実行時SQL/hashと変換理由を記録する。
- 次のrunはSeaweedFSの2 GiB上限でOOMKilled（exit 137）となり、Parquet commitが失敗した。
  Flux storage childのlocal patchで8 GiBへ変更し、rollout後に再試験した。
  失敗したCTASの専用prefixに残った15 incomplete multipart uploadsだけを回収し、
  upload/objectとも0件を確認した。SQL tableが0件でもobject回収完了とはみなさない。
- 型検証は完全なcolumn type列を要求する。Icebergの文書化された`varchar(n) → varchar`
  変換だけを明示的に扱い、文字列型と元の長さ上限も検証する。
  数値・文字列・booleanの型違いは拒否し、金額結果の比較を緩めていない。

証跡は`tmp/local-experiment/benchmark/<run>/`。失敗runも保持している。
現行の型比較実装で保存済み22組を再検証し、source/result hashを
`comparison-reverification.json`へ保存した。比較器のfocused testsは20件。
SeaweedFSはrollout後の同一Podでrestart 0、観測したmemory sample最大値は4,828Miだった。
再実行は`rtk make local-tpch-smoke`、詳細はrepositoryの
`tools/local_experiment/benchmark/README.md`と`deploy/local-experiment/storage/README.md`を参照。

## 2026-09-16: 読み取り専用SRE診断

**WP-L2-AGENT-002の部分実装**として、MCP doctor/Pod/Eventの構造化snapshotを
固定ルールで分類する診断を追加した。実LLM推論や自動修復、PR提案の完了とは扱わない。
実機6 Pod / 9 Eventを読み取り、3 tool callsと診断自身の計4件をClickHouse/Lokiへ記録した。
同じtask/traceと各span/action、event ID、usage NULLを再取得して照合した。

結果は`attention-required`。Polarisの過去4回、PostgreSQLの過去3回のrestartを警告し、
現在の停止とは断定しない。承認済みのroot Flux停止はinformationalへ分離した。
failed/pending/unreadyのfixture、namespace逸脱、監査の不一致など11 focused testsが成功。
初回は検証側がLoki actionの`mcp.` prefixを欠いて不一致となり、修正後の再試験が成功した。
失敗記録は保持している。

成功証跡は`tmp/local-experiment/bears/61faa9b3a8a44ea09fb082365d023d23/result.json`。
再実行は`rtk make local-sre-smoke`。詳細はrepositoryの
`tools/local_experiment/bears/README.md`を参照。

## 2026-09-16: MCP・Pawprint・LangGraphのローカル連携

**WP-L2-AGENT-001 / WP-L2-OBS-001**のローカル機能として、公式Python MCP SDK
2.2.0のstdio Gatewayを実装した。8つの固定読み取りtool（doctor、Pod、Event、dbt models、
固定fixture EXPLAIN、固定lineage、直近Pawprint、policy）だけを公開する。
任意shell/SQL/URL/path、別namespace、GitHub書き込みは受理しない。
Kubernetes context名だけでなく、実際のloopback endpointも要求する。

実機11呼び出し（8成功・3 policy拒否）すべてで、OpenTelemetry SDK 1.44.0のspan、
Loki log、ClickHouse Pawprintのtask/trace/span相関を確認した。
いずれかの監査保存に失敗した場合は成功を返さず、backend dataを返却しない。
自由入力・prompt・credential・生の例外は監査payloadへ保存しない。
最終再実行は`tmp/local-experiment/gateway/smoke-39827f89c41543889d2e5bf2d3ca7624.json`。
Gateway 12、metadata 13、Loki 7 focused testsが成功した。

**WP-L2-LLM-001**はLangGraph 1.2.11、provider設定schema、cloud無効化境界、
一時的なloopback mock HTTP endpointを実装した。2回の`plan → MCP → summarize`が成功し、
8件のAgentHouse eventと親子traceの相関を確認した。Colimaから`host.lima.internal`を
経由したmacOS loopback mock接続も成功した。13 focused testsが成功した。
最終実測は`tmp/local-experiment/llm/d9fedb72a0894873acd24196cdda2772/summary.json`。

これは接続と制御フローの検証であり、実LLM推論・モデル品質・診断能力の証明ではない。
summarizeへ渡す入力はtool成功状態のみ。token/costは未知としてNULLに保持する。
model weightsは取得せず、cloud APIは一度も呼び出していない。
Cloud側はprovider/model・APIキーの環境変数名・明示予算の判断待ち。
ローカル開発の包括承認へ追加課金を含めていない。

```sh
rtk make local-platform-status
rtk make local-gateway-smoke
rtk make local-llm-smoke
rtk make verify-local-experiment
```

`local-platform-status`はLakehouse、Superset、metadata、AgentHouse、Lokiの状態を
一括確認し、不健全・欠落・異なるruntimeで非ゼロ終了する。機能検証は各smokeと分ける。
詳細と復元はrepositoryの`tools/local_experiment/{gateway,llm}/README.md`を参照。
metadataのprivate設定が消失しても、保持volume/containerがあれば秘密情報を再生成しない。

## 2026-09-16: BI・metadata・AgentHouseの実機導入

ADR-0034のローカル適用経路で、以下の機能を検証した。GitHubでの公開・
Flux admission・本番受入の完了は意味しない。

- **WP-L1-BI-001**: Superset 6.1.0のnative ARM64 imageに固定Trino driverを追加。
  既存Iceberg fixtureの2行を取得し、dataset/chart/dashboardを保存した。
  container再起動・再作成後も同じ保存物とquery結果が残った。
  SQLite online backupと対応するprivate設定をexportし、integrity checkが成功した。
  `127.0.0.1:18088`のみ、2 CPU / 3 GiB、Colima VM host networkからTrinoへ接続する。
- **WP-L1-META-001**: OpenMetadata 1.12.6 / PostgreSQL 1.12.6 / Elasticsearch 9.3.0の
  native ARM64を固定。実fixtureとhistorical dbt modelsの3 dataset、schema、team owner、
  Dagster pipelineに紐づく2 native OpenLineage edgesをAPI保存・再取得した。
  実際のdbt実行時刻とDagster run IDを使う4件のSTART/COMPLETE eventを送信する。
  PostgreSQL backupを新規scratch DBへ復元し、3 table entitiesを確認してscratchだけ削除した。
  serverは`127.0.0.1:18585`のみ、DB/searchは非公開。
- **WP-L2-CH-001**: ClickHouse 26.8.5.13のnative ARM64、2 CPU / 4 GiB、
  `127.0.0.1:18123`のみ。Pawprint schema、insert/query/export、同じevent IDの
  論理的重複排除と再起動後の保持が成功した。未知のtoken/costはNULLとする。
- Loki 3.7.0のnative ARM64を1 CPU / 1 GiB、`127.0.0.1:13100`のみで起動した。
  正規化したtask/trace/span/action/statusのpush/queryと再起動後の保持を確認した。
  MCP連携・LLM workflowの実測結果は後続記録へ分ける。

SupersetはSQLiteの単一process profile。OpenMetadataのdbt model entitiesは
cleanup済みの過去実行を表すことを明記し、artifactにない型はUNKNOWNのままにする。
Elasticsearchのoptional vector index警告は残るが、通常のmetadata/lineage APIは成功した。
保持データ・volume・backupの削除は行わず、生成された秘密情報はGit管理外に保存する。

```sh
rtk make local-superset-start local-superset-smoke
rtk make local-metadata-start local-metadata-ingest
rtk make local-agenthouse-start local-agenthouse-smoke
rtk make local-loki-start
rtk make test-local-platform
```

再起動・export・復元・制限の詳細はrepositoryの
`tools/local_experiment/{superset,metadata,agenthouse,loki}/README.md`を参照。
Supersetの保存dashboardは`/superset/dashboard/shirokuma-local-lakehouse/`。
証跡はGit管理外の`tmp/local-superset/`と`tmp/local-experiment/{metadata,agenthouse,loki}/`。
Metadata最終実測は`ingestion-20260916T093504Z.json`、復元記録は
`backup-restore-verification.json`。focused testsはBI 11、metadata 8、AgentHouse 9、Loki 5件。

## 2026-09-16: Dagster / dbtによるローカルDataOps

**WP-L1-DATAOPS-001のローカル機能検証**を実装した。既存のIceberg fixtureを
dbt staging tableへ読み込み、martでnameを大文字に変換する。Dagster assetが
`dbt build`と`dbt test`を実行し、同じ入力で再実行できる。

2026-09-16 05:03–05:05 UTC（14:03–14:05 JST）の実測では、通常・再実行・retryの
3回が成功し、それぞれ2 models + 8 data tests、追加の`dbt test` 8件が通過した。
martの結果は毎回`[[1,"SHIROKUMA"],[2,"POLARIS"]]`。
意図的に期待行数を3へ変えた試行は、指定したrow-count testの1件だけが失敗し、
Dagsterも失敗として記録した。期待行数を2へ戻したretryは成功した。
保持fixtureの前後の値は一致し、今回生成した2 tableを所有コメント確認後に削除した。

```sh
rtk make local-dataops-smoke
rtk make local-dataops-recovery-smoke
rtk make local-lakehouse-smoke
rtk make local-doctor
```

コマンドは固定contextを確認し、空きポートにloopback限定tunnelを作成して終了時に閉じる。
dbt profileとDagster instanceはGit管理外の実行ディレクトリ内だけに生成する。
実験namespaceは既存`iceberg.local_experiment`を使用し、credentialや権限は追加していない。
run UUID付きの2 tableだけが再作成・削除対象で、既存データは対象にしない。

中断・timeoutではローカルdbt processを回収後、run UUIDごとに分離したTrino userの
実行中queryを停止し、終了を確認してからcleanupする。確認できない場合は
`cleanup.status=deferred`としてtableを保持し、成功扱いにしない。
実機でもRUNNING query `20260916_051124_00143_ftq95`の限定停止とactive query 0件を確認した。
想定したdbt assertion失敗はexit code 1だけを受理し、timeoutや強制終了とは区別する。

### 依存・環境・診断

- Python 3.12.12 / macOS ARM64、Dagster 1.13.22、dbt-core 1.12.5、dbt-trino 1.10.3。
- `uv.lock`の96 package entries / 395 archive hashesを固定し、91 installed packagesの
  依存整合性を検証した。追加の常駐Pod・scheduler・公開endpointはない。
- `.venv`は約295MB、初回成功までの実行証跡は約11MB。生成tableは計4行で、実験後に削除。
  backup対象の追加はなく、fixture・versioned models・lockfileから再現できる。
- `doctor --profile local-experiment`は4 workloadの現在世代・replica・rollout失敗を確認する。
  親Flux停止だけをinformationalとして表示し、他の不健全なresourceはdegradedを維持する。
  SQLと公開承認は未評価と明記し、旧default profileの動作は保持した。
- ローカル回帰61 tests（runner 18、SQL 6、Polaris 12、session 7、DataOps 18）、
  設計55 documentsと関連14 tests、`go test ./...`、`go vet ./...`、secret scanを検証した。

実行証跡は`tmp/local-experiment/dataops/5bc35888a9144c5090fbe726d924c33b/summary.json`、
各attemptのDagster run storage、dbt `run_results.json`とログに保存した。
初回はDagster作業directoryの作成漏れで失敗し、修正後に成功した。失敗記録も保持している。
中断対策後の最終再実行`edfd3bf2fe7b440eaea576e133621e14`でも4試行の期待結果が一致し、
query終了確認・実験table 2件の削除・保持fixture一致を確認した。
詳しい再実行・復旧手順はrepositoryの`tools/local_experiment/dataops/README.md`を参照。

この到達点はhost-localのDataOps機能検証であり、常駐Dagster UI/scheduler、
OpenMetadata/OpenLineage、10GB TPC-H、旧公開admissionの完了は含まない。
次の依存先はWP-L1-META-001、独立した候補はWP-L1-BENCH-001とWP-L1-BI-001。

## 2026-09-16: ローカル開発への移行とLakehouse SQL検証

適用方針は[ADR-0034](../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)。
この記録はローカル実験の実測結果であり、過去の公開承認・Issue完了・本番受入を変更しない。
作業ブランチは `local/autonomous-experiment`、ホストの作業checkoutは
`Shirokuma/worktree/local-autonomous`。従来のmain checkoutと未コミット作業は保全した。

### 確認した機能

- **WP-L1-LAKE-003**: 既存Colima/k3sを起動し、SeaweedFS・PostgreSQL・Polarisの
  readinessを確認。新しいIceberg smoke Jobで既存fixtureを再読込した。
  `created=false`、2行、snapshot `7141845066324476177`で、保持データの再作成はしていない。
- **WP-L1-QUERY-001**: Trino 483の単一coordinatorをARM64で起動。
  `SELECT id, name FROM iceberg.smoke.fixture_v1 ORDER BY id` は
  `[[1,"shirokuma"],[2,"polaris"]]`を返した。
- **ローカル適用経路**: `prepare → apply → resume`を既存catalogに対して実行。
  UID/resourceVersionで競合を検出し、変更したFlux停止状態だけを復元できた。
  親`flux-system`の停止は今回のローカルモード用に維持している。
- 新規の実験用namespace `local_experiment`でCREATE/SELECT/DROPが成功した。
  namespace限定`TABLE_WRITE_DATA`とcatalogのpurge設定を追加した。
  `smoke_4bc60afe11ad4123a4737e82ea41c7c5`の2行を読み戻し、同tableを削除した。
  初回の失敗時に作ったtableも削除し、`SHOW TABLES FROM iceberg.local_experiment`は0件。
  保持fixtureの権限とデータは変更していない。
  実測時刻は2026-09-16 04:40–04:41 UTC（13:40–13:41 JST）。

### ローカル成果物と範囲

- 入力は既存のupstream Trino 483 archive、SHA-256
  `4f3978428f26f36398c94b85a3e03b5301394919c8a4271b497b0fcd1698d0cb`。
  hardened source rebuildや公開署名済み成果物として扱わない。
- 実測runtimeは`docker://28.4.0`。同じColima Docker daemonでビルドし、
  runtimeで解決できる`RepoDigest`を使用した。containerd importはこのprofileでは不要。
- 実行imageは`shirokuma.local/trino@sha256:e169b9d87a2f41b62ffc9ff6b7b90187ed6f4f758503f409566e16a0a5966ed2`。
  `imagePullPolicy: Never`、ClusterIP、非root、読み取り専用root filesystem、
  CPU limit 4、memory limit 6Gi。ホスト公開用Ingress/NodePortは追加していない。
- 新規imageは約630MB。ローカル再利用用image archiveも同程度。
  既存PVC・バックアップ・Colima diskは削除していない。

### 再実行

コマンドは作業checkoutのrootから実行する。

```sh
rtk make verify-local-experiment
rtk make local-experiment-status
rtk make local-trino-build TRINO_ARCHIVE="$TRINO_ARCHIVE"
rtk make local-trino-render TRINO_IMAGE_DIGEST="$LOCAL_RUNTIME_DIGEST"
rtk proxy kubectl --context colima-mac-studio-solo -n shirokuma-dev \
  port-forward service/trino-local 18080:8080 --address 127.0.0.1
rtk make local-trino-smoke
rtk make local-trino-smoke-write
```

`local-trino-smoke-write`は一意の試験tableを作成し、その実行が作成に成功したtableだけを削除する。
初回のpurge設定は`tools/local_experiment/polaris_permissions.py`と
`bootstrap/trino/local-experiment/README.md`を参照する。
runtime適用の詳細と復元は`docs/LOCAL_EXPERIMENT_RUNNER.md`を参照する。

### 検証と未達事項

ローカル機能の証拠は、ホストのGit管理外の`local-autonomous-20260916` evidence directoryと
checkoutの`tmp/local-experiment/`に保存した。認証情報はcommit・ログへ出力しない。

- `make verify-local-experiment`: 36 tests passed（runner 18、SQL 6、Polaris権限12）。
- `make verify-design-context`: 54 documentsを検証し、関連14 testsもpass。
- `verify_polaris_runtime.py audit`: 既存のruntime activation contractを保持。
- 既存Polaris 198、Iceberg 126、Trino publisher 133、feasibility 52、security feasibility 12、
  UI、observability、Go、Kyverno、secret scanと差分の空白検査はpass。
- 旧Trino bootstrapの4 failuresと供給網の5 failures、期限切れTrivy例外は下記の別境界として残す。

Polaris設定変更は`currentEntityVersion=1`からversion 2へ更新した。
`polaris.config.drop-with-purge.enabled`は以前は未設定、現在は`"true"`。
設定はcatalog単位だが、追加したdata privilegeは`local_experiment`に限定した。
失敗時も元のエラーと試験table名を失わないよう、SQL smokeのcleanup報告を修正した。

従来の`make verify`は公開経路向けの判定を保持するため、ローカル実験全体の合格を表す
コマンドにはしない。期限切れのresident authorization・Trivy例外、および
公開未承認のTrino runtime/固定input inventoryの検査は引き続き失敗する。
過去の期限・検証結果を更新して成功扱いにはしていない。

ホストのHelm 4.2.3は旧テストが固定する4.2.2と異なるため、該当テストはchecksum検証済み
4.2.2をそのプロセスのPATHだけに指定して実行した。グローバル設定は変更していない。

`shirokuma doctor`のclusterはhealthyだが、意図的なFlux停止と旧policy判定により
全体はdegraded。Trino/Polaris/S3のデータ経路はSQL smokeと実機readinessを別に確認する。
保持fixtureの`REPORT_READ_METRICS`権限不足は警告として残るが、SELECT成功とは区別する。

### 復元

実験用TrinoのDeployment/Service/NetworkPolicyと生成ConfigMapだけを削除すれば
query engineを撤去できる。既存catalog・storage・PVCは削除しない。
Polarisの追加roleは`local_experiment`限定であり、撤去時はbindingを外して追加roleを削除し、
catalogのpurge設定を保存した以前の値へ戻す。
親Fluxの元の停止状態は`flux-before.json`に保存済み。GitHubのdesired stateへ戻す場合だけ
差分を確認して復元する。Colima全体のresetはこの手順に含めない。


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


## 2026-09-17: Spark / Trino same-snapshot baseline

WP-L4-BENCH-001の最小測定を実装・実機検証した。固定TPCH SF0.1 lineitemの9列、
Decimal正規化、独立Python oracleを使う。元データのNULL/finite/型/往復検査後、
sourceと作成後snapshot全行をstreamし、件数・logical SHA・期待集計が一致した。
hostに全datasetは保存しない。実sourceは600,572行、受信38,853,382bytes、
IcebergはParquet 1file/5,011,080bytes。data-file予算32個/256MiB内である。

run `e727a68de2b64313bc3fa3dc98817dac` は13:00:58–13:05:01 UTCに成功した。
同UUID `e6abed5b-9cda-4d8c-9d29-a71e42aac122`、snapshot `8013311425425878260`（親なし）、
同metadata locationとfile一覧を両engineで確認し、snapshot指定の16測定がすべてoracleと一致。
logical SHAは `87e3f2d1f7cca433c559c2f183baae1c78b37a26358223000bc0039a20f9ffbe`。
Q1由来の4SUM/countは4group、Q6 revenueは `11803420.253400`。
Spark4.1.3/Iceberg1.11.0の既存ARM64 imageを使い、新build/download/model呼出しはない。

| 固定query | Trino初回client秒 | Trino反復3回中央値秒 | Spark初回秒 | Spark反復3回中央値秒 |
|---|---:|---:|---:|---:|
| q1_sums | 2.563 | 2.568 | 6.484 | 0.677 |
| q6 | 3.062 | 3.076 | 0.442 | 0.252 |

TrinoはPOSTから最終page消費までのclient wallで、network/JSON/row処理・途中のdurable journal保存を含む。
serverのelapsedTimeMillisは1075–1542ms、wallTimeMillisは17–139msとして別に保存した。
Sparkはsql→collect完了までで、結果正規化/oracleは計測外。SparkSession開始6.358秒、
nativeのpreflight→cleanup全体38.305秒も分離した。executor readinessは初回queryへ残りうる。
元stream/guard/権限準備、別executor probeとcleanupをquery時間へ足していない。
shared cache既温、Trino先行・Spark後行、resource配分差、各query3反復という条件の観測であり、
一般的なengine速度順位・cold cache・標準TPC-H性能・統計的有意差は主張しない。

source/schema/query/result/hashを型付き固定契約にし、任意SQL/endpoint/SFはCLIから受け付けない。
実Trinoのdecimal型名に含まれるcomma後空白を狭く正規化し、precision/scale変更は拒否する。
Spark4.1.3で未対応のDECIMAL型付きliteralは両engine共通の固定CASTへ変更した。
AVGの型推論差は今回のSUM/count比較へ混入させない。

Polaris一時roleはTABLE_LIST/READ_PROPERTIESの2権限だけ。READ_ONLYはcatalog権限の意味で、
既存S3資格情報がbucket scopeのままである点は維持する。token実TTL3600秒。
native19UID（17明示/owned＋2自動Endpoints系）、一時ingress NP2・Polaris3entities/2bindingsを回収し、
owned query drain後に同UUID tableをDROPした。保持fixture/全table一覧の前後一致を確認。
executor別probeはstage28/task36,37/partition0,1/attempt0で、query計測へ加算していない。
Secret本文は保存projectionへ入れず、未知POSTの再送・不明な同名table削除を禁止する。
S3 orphan不存在・即時JWT失効・完全rollback・本番受入とは区別する。

benchmark79＋native/access追加10を含むSpark331 testsが成功。
全local回帰44suites/1416件（1415成功＋既存optional OTel1skip）が成功。
全logと集計は `tmp/local-experiment/spark-benchmark-checks/3a7b51d8f277490aa1fd457dc2e18bde/`。
fresh前後保持監査は `spark-benchmark-preflight/f8a7fdda5ed342eebb75780cf7d6431b/`。
AH440増分0、Metadata7plans/14tables/14edgesの35endpoints、保持3044files、
14Pods/27resources6CM3SecretUID、Docker3、SQL履歴4tables/5DDL、fixture全歴史、
MLflow59tables/11artifacts、images553、Polaris6権限一覧の内容が不変。
開始前の空き容量はhost2,486,954,598,400bytes/VM346,868,105,216bytesで5GiB以上。

WPはlocal-partialを維持する。Comet/Gluten、複数規模/資源条件の評価は後続。
次は保存済み16測定を既存MLflowの別experimentへ取り込み、観測範囲を保って検索・比較できるようにする。

## 2026-09-17: saved Spark benchmark MLflow Tracking

WP-L4-ML-001に保存測定のTrackingを追加した。source run
`e727a68de2b64313bc3fa3dc98817dac` の16観測を再実行せず、
専用experiment `shirokuma-local-spark-benchmark-v1`（ID2）へ4 runs・92 metric pointsとして取り込んだ。
既存11評価runs/1118 pointsを保持し、全体は15 runs/1210 points/15 artifactsになった。
モデル呼出し・新benchmark query・新AgentHouseイベントは0。

取り込み前に所有するserverを停止し、排他lock下で全state22files/1,532,300bytesを保存。
停止前後で59tablesのDDL/全logical rows・11 artifacts・旧index/store markerが一致した。
backupは新規private directoryに保持し、source/copy/sourceのhash/mode/mtimeを照合、
全file/directoryのfsync後に完了manifestを保存した。既存backupを上書きしていない。

新reporterは固定receipt7filesの前後同一性と型付き相関を検証し、
同UID Pod resource・snapshot・結果hash・cleanup/query drain・欠測をallowlistで射影する。
失敗/中断はFAILEDのまま、running/不正/改変sourceは取り込み前に拒否する。
snapshot `8013311425425878260` は文字列、durationはexact nsとして保持。
Trino client時間は途中journal書込を含み、server時間/cpu/inputを別metricへ分離した。
Spark startup/native全体は共通contextで、4個の独立metricとして加算していない。
Trino observed version/resourceとSpark sample日時のunknownを維持し、import日時で補完しない。
raw SQL/Pod/log/credentialは取り込まない。

初回import journalは `benchmark-imports/8f7ec0bad7404ec4b58281ffb0826b7c/`、
再importは `benchmark-imports/278165a319df40719bd5fdf903e308ea/`。
SQLite変更はexperiment1/tag2、runs4、params120、tags44、metric92/latest38の追加のみ。
旧全行・旧11 artifacts・旧index/store markerの不変を照合した。
同一source再importはrun/metric追加0、59tables全行・全15 artifacts・新旧indexの
hash/mode/mtimeが不変。新操作journalだけが残る。

同store UUIDのserverをloopback18550へ再起動し、healthyを確認。
再起動後もDB/全artifacts/indexが不変で、標準UIが使用するAPIから既存評価11件と
新測定4件をreadbackした。新画面は `http://127.0.0.1:18550/#/experiments/2/runs?workflowType=machine_learning`。
専用HOME/TMP/PATH・telemetry/job無効の既存runtime境界を維持する。

report25/importer23の48focused tests、全local回帰46suites/1464件
（1463成功＋既存optional OTel1skip）が成功した。
証跡・全state backupは `tmp/local-experiment/mlflow-benchmark-checks/9331444153084c8a969690d8ec26fece/`。
先行benchmarkの独立最終監査も123 checksが成功し、16測定・19UID回収・保持3044files等を照合した。
その全行hashは保存されたsource/snapshotの2 complete streamsを継承し、追加全行queryは行っていない。

到達点は保存証跡に基づくlocal Trackingである。Notebook/autolog・Registry/Serving、
export復元/移行、一般的な性能比較、本番/人間受入の完了は主張しない。
次は既存Spark imageを使い、Iceberg branchへの独立書込みとmain/tag不変を検証する。

独立after監査 `independent-after-verification-final.json` は121 checks成功。
保存sourceから92points/latest38を独立再計算し、旧全行・DDL・旧11artifacts・index保持、
再取込とfresh再起動後の全store不変を照合した。監査の初回artifact hash不一致12項目は
serializerの末尾LF欠落によるもので、元結果を保持して契約どおり修正した。
semantic JSONに不一致はなく、製品code/runtimeの修正は不要だった。
設計83documents/関連14tests、secret filenames・diff検査も通過し、repo/Vaultの更新2ノートは一致した。

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

## 2026-09-17: SRE Bear product CLI

`shirokuma sre run`と`sre status TOKEN`を追加し、既存Qwen・MCPによる診断へ接続した。
初期4観測源と最大2種の固定followupを維持し、任意prompt/namespace/tool引数や修復は受け付けない。
GoはData transportの所有childへのSIGINT・最大240秒cleanup待機を再利用する。
厳密なallocated4/result13fieldsを照合し、finalとchild exitが整合してから結果を出す。
workflowのpassedと環境のassessmentを分離し、attention-requiredを正常へ変換しない。

`sre-cli/<token>`のintent/lock/hash sealと、既存`sre-llm/<token>`のworkflow証拠を分けた。
呼出しは1回限りで、自動再送/resume/SIGKILLを追加していない。statusはAPI/model/write0。
snapshotからevidence・triage・比較・hashを再計算し、MCP返却IDの相関と保存spanを区別する。
旧3源、container詳細導入前の4源は当時のscopeだけを検証し、未観測情報を作らない。
旧保存passedでも再構成に必要なobservationsが無ければunknown/journal-onlyとする。
statusによって旧ファイルへ新たなhash sealを書き足さない。

元取消の保持をmodel audit・最終保存・tracer shutdownまで補強した。
MCP Clientの終了処理がOSErrorを上げる場合や取消を抑制する場合も、所有contextの終了を通った後に
同じ取消objectを返す。通常cleanup失敗は握り潰さない。gateway stderrは作成時から0600とした。
保存statusやCLI出力は原logs/manifest/prompt/応答/例外本文を転記しない。

実CLI run `sre-llm/07ca3b1a93988ec2a92e53bf69639ca5/` はpassed。
内部workflow token `bb3f4eee7c61490191dc0f76c6e694d4`、trace
`37fd761746c93059112c86bc56e1d0fe`。Qwen1callは入力1458/出力104tokens、5788ms、cost NULL。
初期doctor/Pods/Events/platformの4readから、3Podの過去非0終了とrestartの6warningを拾い、
f01–f06に対するPod再読取1回を選択した。約6.828秒間で同UID/state/restartに変化は無かった。
現在の障害・原因・回復とは断定せず、assessment=attention-required、root_cause=undetermined、
remediation_executed=falseを維持した。他の3観測源はnot-reobservedのまま記録した。
MCP5calls＋LLM/root各1の計7監査eventをAgentHouse/Lokiで照合した。
保存された実OTel spanはmodel/rootの2件であり、MCP返却span ID相関5件とは区別する。

旧run `0c69d3ef5ebc401ca3d27bdb4bd98ea7`と新runを各3回、計6回CLI statusで照会した。
旧内部token `f4c7620dbba149bab92b0a8df95b78ca`は当時のattention-requiredを保ち、
対象26files/directoriesのhash/mode/mtimeが不変だった。
新Python26testsと既存SRE84tests、実run＋fake外部＋actual OTelの結合が成功。
Goは78top-level tests＋52subtests（SRE16を含む）とvet/race、
actual Python main/fake workflowの成功・失敗・不存在・取消4経路が成功した。
全local回帰48suites/1580件（1579成功・既存optional OTel1skip）が成功した。

build/source hash・全log・status前後比較は
`tmp/local-experiment/sre-cli-checks/b851301006164c69ba8ca15587d2faba/`へ保存。
独立監査は `sre-cli-preflight/912433d9483f46c991a362f5fa39a461/`。
実行前は直前Query after/finalをhash参照で継承し、fresh14Pods・Docker3・context/node/VM・
AH446件・MLflowの所有identity/healthy/store/indexを確認した。新Query証拠を加え3365filesを保全した。
deepなDB/Metadata/fixture検証は直前Query sealの継承であり、SRE前に再度fresh全走査したとはしない。

これはbounded観測CLIのlocal-partialであり、一般的なroot cause診断・自動修復・本番受入は未達。
次は利用者がstdinで渡す限定Pod候補を、Qwen分類とnative server dry-runへ接続する。

最終独立監査 `sre-cli-preflight/912433d9483f46c991a362f5fa39a461/independent-verification.json`
は63checks成功。AH446→453の増分7件だけで旧446件の全行hashは不変、Loki7件も一致した。
workflow保存span2件に加え、共有Gateway exporterの保存span5件を別ファイルとして照合した。
返却ID・保存場所・parent edgeの証拠を混同せず、完全な親子trace graphは主張しない。
14Pods/Docker3/MLflow等の軽量fresh identityは不変で、deep dataは直前Query sealを継承した。
保全3365filesのうち3361は完全一致。Gatewayのevent/span JSONL2件は旧bytes prefixを保って
今回MCP5件分だけを追記した。readiness処理によるLoki runtime.json/Metadata runtime.lockの
2件は内容・mode同一でmtimeだけ更新された。strict比較falseの原after.jsonも保持している。
CLI status自体の6回/26files・directories不変とは別に、これらrun中の更新を記録した。
監査tunnel終了、design83docs/14tests・secret scan・diff check成功。
SRE WP/MilestonesをVaultへ同期し、MOD-051の既存言語差を維持して追記した。

## 2026-09-18 caller Pod policy CLI evidence

`policy check --pod-stdin`と`policy status TOKEN`を実装した。16KiB以内の限定Pod1件から
safe boolean featuresを作り、独立host期待値・Qwen分類・同じmemory bodyへのdry-runを照合する。
入力のraw bytesはargvや保存filesへ出さず、入力/body hash・変換ID・features・型付き結果を残す。
namespace/name、Never設定、service account token無効だけを補い、policy違反は修復しない。
limitだけでrequestがない入力と、hostNetworkの暗黙hostPortはnative defaultingで判定が変わるため
事前拒否する。resource数量は限定した正の文字列と大小関係を検証し、SI kiloは小文字`k`とする。

実compiled CLIで以下を確認した。各workflowはモデル1回、dry-run1回、retry/cloud呼出し0。

| 保存token | 結果 | Qwen入力/出力 tokens | 推論ms |
|---|---|---:|---:|
| `255b21eed54252825d90870c0811f86f` | passed / allowed、exit0 | 675 / 74 | 4080 |
| `d906b723859c1be5095da0c786ddabb6` | failed / unknown、exit1 | 675 / 84 | 3148 |
| `1e0a67ec0d8785febb8539f1580f5b9f` | passed / denied P01、exit2 | 675 / 84 | 3146 |

最初のprivileged試行はモデルP01一致後、APIのInvalid/422形式をForbidden/403専用parserが
認識できずunknownで停止した。元証拠を保持し、別の新候補によるmodel無しの診断dry-run1回も
unknownとして保持した。Kubernetes v1.33.4の一次sourceに沿ってexact Invalid単一cause形式を追加し、
新tokenで拒否を実証した。policy名・binding名・固定messageの完全一致を維持し、foreign/schema/
混在/余分な末尾は認識しない。未知の元試行を再送・成功への書換えはしていない。

未対応env入力 `02c0bd3c1089d36dd579130a0c2a7f21` はINPUT_INVALIDで推論/API/intent保存前に拒否。
namespace UID/label、空LimitRange、16policy/binding UID/generation/spec/owner/source hashを固定し、
API前のpending intentをfsync、後の不存在とbaseline不変を確認した。権限付与・保存workloadは0。
成功2回と失敗1回に各4件のAgentHouse/Loki/保存OTelを残した。モデル原文・raw API応答は保存しない。
3tokenのstatus各3回（計9回）で32のfiles/directoriesのhash/mode/mtimeが不変だった。
statusは保存features/host期待値/plan/型付き結果/spans/hash sealを検証し、原manifestの再構成や
最新admissionの再観測を主張しない。旧15case corpusは別入口のまま維持した。

Python新規78件（入力27/観測17/workflow13/CLI21）、Go91 top-level testsと59 subtests、
vet/raceが成功。実Python bridge＋実workflow/外部fakeの7経路でexit/取消/再送防止/非保存を検証。
全local回帰1658件（1657成功・既存optional OTel1skip）、設計83documents/関連14tests、secret検査も成功。
証拠は `tmp/local-experiment/policy-cli-checks/38ccaf07c7e64c93935b49fa18234d4d/`、
各workflowは `tmp/local-experiment/policy-candidate-bear/<token>/`、
独立監査は `policy-candidate-preflight/6abcff1fbcd841e9be85f43791c848b2/`。
[Runbook](../../../tools/local_experiment/bears/POLICY_CLI.md)に入出力と停止時の扱いを記録した。
実証は限定PodのP01適合/違反と入力拒否であり、全P01–P06のcaller入力live網羅、任意manifest、
画像署名/CVE、scheduling、アプリ動作、権限判断、本番受入の完了とは区別する。

独立の軽量再観測でAgentHouseは453→465（上記3workflow各4件）、旧453件の全行hashは不変。
対応Loki各4件、保持3410files、14Pods/3Docker/MLflow identity、16policy/bindingとnamespaceは不変、
LimitRange空、candidate Pod残存0、既存tunnel閉鎖を確認した。深いDB/metadata/fixture観測は
直前のQuery/SRE監査の保存証拠を継承し、今回は各ストアの全内容を再走査していない。

## 2026-09-18 query-triggered wake evidence

`warehouse query XS`をquery-modeの短命ownerへ接続した。旧chill/wakeと7固定caseは維持し、
既存wake probes後に別source/request UUIDの`xs_caller`を追加した。callerのintent、POST intent、
query ID、固定SQL/結果hash、終端履歴、UID/scale chain、監査を束縛してから成功応答を保存する。
query CLIは240秒待機し、切断でownerを止めず、same-ID再照会以外の自動再実行をしない。

実session `5edf12a5864e4a1391b8c4d1a7d309da`でGo CLIのchill→query→same-ID query再送が成功。
caller request `7ce1b703-b16c-4375-8db6-ead09ae38485` はSQL POST1回、
query `20260917_152707_00001_gps5i`の25nation行とXS nodeが一致した。
結果hashは `eba954dccf4cef71463d4c4ffe8f7dfbd3777be9660c71265791467025f4acc1`。
再送応答はbyte単位で同一、追加SQL/scaleは0。RPCは3接続・2実operationを記録した。
全8caseの結果/handleを消費し、XS終端履歴はwake probeとcallerの2件、Sは既存3終端caseだけだった。

同じXS Deploymentを1→0→1に変更し、新Pod/processを確認、SのPod/Deployment identityは不変。
17既知UIDを明示回収、停止時にcontrollerが削除した旧XS Podも残存0、conflict/deferredは0。
caller計測はwake待機26059ms、query client3283ms、監査前までの全体31581ms。
前者には起動/probes/監査/guard、後者には結果取得/host処理が含まれ、cold-query性能とはしない。
busy/chill/wake/query/rootの5件をAgentHouse/Loki/実保存OTelで照合し、
traceは `dc95af2097fd91da1fe61ecaac05276e`。LLM/cloud呼出し0、usage/costはNULL。
query成功応答後の回収と最終root監査まで含め、owner exit0・session passedを確認した。

新query22・query telemetry15・RPC追加6のPython43件と新Go6件が成功。
実Go→Unix RPC→実owner/controller/client（外部だけfake）結合で同ID再送とprechill拒否を確認した。
厳密な型、scope/hash、UID/scale履歴、取消の原object、保存障害/監査失敗/改変したspanを確認。
旧session/chill/wakeを含む全local回帰1701件（1700成功・既存optional OTel1skip）、
Go97 top-level/59 subtests、vet/race、設計83documents/関連14tests、secret検査が成功した。

実行・検証証拠は `tmp/local-experiment/warehouse-query-checks/f735cfb20dcc4bcd9c532b7d64f9f086/`、
owner証拠は `warehouse-lifecycle/runs/5edf12a5864e4a1391b8c4d1a7d309da/`、
独立監査は `warehouse-query-preflight/bded3460dc4d4afba5459680c4c3604c/`。
fresh前後18checkで旧AH465全行hash、保持3478files、14Pods/27resources/3Docker/MLflow、
16native policiesとnamespaceが不変、現AH470、生成resource残留0、ports/locks/socket閉鎖を確認した。
fixture/SQL履歴/Metadata/MLflow全論理データは直前監査のsealを継承し、全再走査していない。
任意SQL/warehouse、実tenant認可、owner再起動後復旧、常駐queue、HA、一般並行writer、本番受入は未達。
