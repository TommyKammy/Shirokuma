---
project: Shirokuma
doc_id: "WP-L4-ML-001"
title: "WP-L4-ML-001 MLflow tracking"
status: local-partial
created: 2026-07-05
updated: 2026-09-20
version: "1.6"
area: "workpackage"
tags: [shirokuma, workpackage, l4, ml]
---

# WP-L4-ML-001 MLflow tracking

## Summary

MLflowで固定合成データの学習、保存Agent evalとSpark/Trino測定を記録する。
Icebergの固定64行を既存Readerから学習へ接続する実1回も、初回失敗の修正・別回収後に成功した。
専用SQLite/loopback標準UIで旧16runsと固定合成1run・Iceberg2runsを保持し、metric履歴・JSON artifactを照合した。
固定Notebookのread-only表示と旧import profileの完全export・非稼働clone復元も確認済み。
完成training receiptsを含む5experiments/18runsの非稼働clone復元とSDK読戻しも初回で成功した。
保存学習をNotebookの指標表・holdout行・残差SVGで確認する実kernel実行も初回で成功した。
Iceberg学習も保存artifactと所有MLflow読取からNotebook表示へ接続し、実kernel初回で成功した。
保存Icebergモデルへ新入力を渡すbatch prediction CLIも実22行で成功し、独立算術と一致した。
Notebookから所有workerへ固定学習を依頼し、1runを追加する実行も初回で成功した。
保存学習3件のtoken・指標・provenance一覧と、選択tokenによる既存予測も実受入で成功した。
保存Icebergモデルの同一分割でalphaを変える未登録のoffline比較も、実1fitとLab表示で確認した。
Hub連携・kernel内fit/autolog、Registry/Serving、clone切替・他host移行は未達。

## 2026-09-20: offline Ridge comparison — local acceptance complete

保存Iceberg学習のalpha=1 Ridgeを基準に、alphaだけを変えるoffline比較を追加した。
検証済み保存artifactの同じ48 train行で候補を1回だけfitし、同じ16 holdout行で予測・指標差を算出する。
source/model/dataset/splitとrequestを束縛し、worker出力は親で追加fitせず検証する。保存sourceが変化すれば成功としない。
alphaは0.001〜1000の有限数値で、MLflow登録・API/Reader/LLM・元学習の更新は行わない。
Labの[比較例](../../../../examples/local_ridge_comparison.ipynb)は既存ML専用環境のworkerを使い、入力・表をNotebookへ保存できる。

実比較`b393842b6c9a4f8db7da8da19672c449`は保存token`6df9b9e5a40b4035b7865f8435fdd291`/alpha10で候補1fitを完了した。
Labで16行比較表・保存/再読込を確認し、再利用holdoutのRMSEは0.163043→0.543633と悪化した。
MLflow19runs/1347points/19artifactsと旧保存物を保持、新run/Reader/LLMは0。独立60桁Decimalとの74数値照合も成功し、追加fit/APIは0。
既視holdoutの探索的再利用であり、独立した最終評価・generalization改善・自動model選択は主張しない。
kernel内fit/autolog、候補のMLflow Tracking/Registry登録、Servingの受入は未達を維持する。
[Notebook WP](WP-L4-NB-001_JupyterHub_bootstrap.md)と[Milestones](../061_Local_Experiment_Milestones.md)へ操作境界・結果を記録する。

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

手順：[saved training inventory](../../../../tools/local_experiment/mlflow/README.md#list-saved-training-runs)。

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

手順：[Notebook-triggered training](../../../../tools/local_experiment/notebook/README.md#trigger-fixed-iceberg-training)。

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

手順：[saved Iceberg prediction](../../../../tools/local_experiment/mlflow/README.md#predict-with-saved-iceberg-training)。

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

手順：[saved Iceberg training review](../../../../tools/local_experiment/notebook/README.md#review-saved-iceberg-training)。

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

手順：[MLflow restore runbook](../../../../tools/local_experiment/mlflow/README.md#restore-an-inactive-local-clone)。

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

手順：[local Iceberg training](../../../../tools/local_experiment/lake_training/README.md)。

## 2026-09-20: saved training Notebook review — local acceptance complete

[NB-001](WP-L4-NB-001_JupyterHub_bootstrap.md)に、保存済みtraining attemptを明示選択する
`local-notebook-training-review`を追加した。private receipt/workload/artifactと所有storeを照合し、
loopbackのexperiment/run GET2回からFINISHED/identity/params/tags/9 latest metricsを確認する。
固定3code cellsで3partitionの指標表・32holdout行・残差SVGを表示し、parentも保存元へ再束縛する。
係数replay・正規方程式・固定dataset/splitの検査は読取であり、新fitや全metric history再読取ではない。
Go build/Reader/LLM/MLflow write/SDK/autologを追加せず、旧評価/benchmark/学習runとbackupを保持する。

既存Notebook環境を使い依存追加0。source/template変化、別run/指標へのすり替え、unsafe path、
不明cleanupは成功にしない。取消は所有kernelだけを対象とし、失敗時も新review証拠を保持する。
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
[レビューrunbook](../../../../tools/local_experiment/notebook/README.md#review-saved-training)を参照。
Notebookからの学習・autolog、Hub、Registry/Serving、一般モデル品質・本番受入は未達を維持する。

## 2026-09-20: fixed supervised training — local acceptance complete

`local-mlflow-train`で固定合成データのRidge学習と明示的なTracking記録を追加した。
128行・3特徴量をseed 1729で生成し、別seed 1730でtrain96/holdout32へ固定分割する。
alpha1/SVDでtrainだけをfitし、train/testとtrain target平均baselineのholdout指標を分離する。
データ・split hash、特徴順序、設定、package versionと9 metric pointsを記録する。
これは固定合成課題の学習経路の証拠で、一般データでの品質や最適なモデル選択を保証しない。

停止中の所有storeと共通排他lock・隔離workerを使う。実行ごとに新attemptを作り、
experiment/run作成・metric batch・FINISHED更新の前にdurable intentを保存する。
応答不明や部分書込を再試行・自動修復せず、既知ID/phaseと失敗・取消を保持する。
部分runはTracking上RUNNINGのまま残り得るため、業務結果はattemptの`result.json`で確認する。
artifactは非実行JSONのデータ/split/係数/予測/指標で、pickle/autologを用いない。
係数からの予測再計算とRidge正規方程式、exact metadata/metric履歴・artifact bytesを
FINISHED更新前後に照合する。保持artifactの上書き、unsafe path、source変化を拒否する。

実attempt `e65bbc1bd3dc4ca3843ca5c502a53ff8`は初回5.616秒でverified、
experiment3のrun `0c2a0a34683545d3acd580642a9bb30d`はFINISHEDとなった。

| 評価範囲 | MAE | RMSE | R² |
| --- | ---: | ---: | ---: |
| train96 | 0.153680 | 0.191315 | 0.996371 |
| holdout32 | 0.145263 | 0.181238 | 0.993176 |
| train平均baseline / holdout32 | 1.802236 | 2.203076 | -0.008340 |

表示値は6桁へ丸め、元の数値はprivate workload/artifactとTrackingに保持する。
旧16runs/1320points/16artifactsを保持し、新1run/9points/1artifactだけを追加して
計17runs/1329points/17artifactsとなった。保存評価12・benchmark4・training1の範囲は分ける。
書込前の完全cold export36files/2,562,597Bを新規private領域に保持し、旧backupを上書きしていない。
retention11checksが成功し、同store UUID・loopback18550で6.881秒の再起動後healthyを確認した。
API GET2回はexperiment3/runのidentity、FINISHED、9 latest metrics/17params/開始終了時刻を照合した。
server稼働中のpreflightは書込前に拒否し、実training失敗は0。新LLM/Trino/Spark/kernel/Agent監査0。

専用MLflow環境の既存NumPy2.5.3/scikit-learn1.9.1を直接依存として明示した。
lock全92package versions/配布情報を維持し、rootの直接依存宣言だけを変更した。
専用環境のlocked syncで87installed packagesを確認した。
新47focused tests（workload15/runtime32）と全local74suites/2630tests
（2629PASS・既存optional1skip、62.366秒）、secret scanが成功。
独立保存39checks/85inputsも初回成功し、係数replay/正規方程式/9指標、旧59tablesの全行・
16artifacts/index/cold backup、保存API2応答とSQLite、所有PID同一性を照合した。
追加API/SDK/fit/product importは0で、NumPy PRNGの独立再生成は含めない。
新training3filesは25,276B（実割当28,672B）、file600/dir700で保持する。
証拠とcold exportは`tmp/local-experiment/mlflow-training-checks/c28faf05befa48d49a3792a01128326f/`。
[学習runbook](../../../../tools/local_experiment/mlflow/README.md#fixed-supervised-training)を参照。
利用停止は追加attemptを止め、必要なら所有serverを停止する。新run/証跡を削除せず、
新学習記録もbackupへ含める場合は停止中の完全exportを追加する。
WPはlocal-partialを維持し、Notebook/Hubからの学習、autolog、Registry/Serving、
別データでの汎化、clone切替・他host移行、本番/人間受入は未達である。

## 2026-09-18: inactive local clone restore and SDK readback

保持済み完全exportを新規private `mlflow-restores/TOKEN/export/`へ複製し、旧runtime/indexは
証拠として非稼働のまま保持する。別`clone/store/`のSQLite/artifactsだけを利用対象とし、
experiment/runのartifact URI 18箇所以外は全59tablesのschema・行を維持する。
`clone.json`はactivation非対応を明記し、既存Trackingのpath/server/設定を置換しない。

実restore `389807a736a74219a05e4d85dadd937a`はhistorical exportの15runs
（評価11＋benchmark4）・全1210 metric履歴・15artifactsを復元した。
専用MLflow 3.16.1/Python 3.12 SDKの新private workerによる読取は実2回、3.287秒/3.251秒でpassed。
各3experiments/15runsのmetadata・全metric履歴・artifact root/filename/SHAを照合し、
worker終了後のSQLite全論理snapshotと保持bytesが不変だった。独立clone照合も全59tables・
15artifacts・URI差分18のみを確認した。数値costの追加や旧import時刻の意味変更は行わない。

SDKはclone固定URIと新private HOME/XDG/TMP/cwdを使う。audit hook前にstdlib
`platform.platform()`でlocal CPU/binary情報をcacheし、hook後のnetwork/subprocessと
clone/readback外書込みを拒否する。これはPythonの限定guardでありOS sandboxではない。
SDK初期化のmigration能力は消しておらず、事前schema/Default確認と終了後全行比較で拒否する。
artifactは空pathのSDK downloadでlocal rootを取得して配下のexpected fileを照合し、
一時downloadへの複製を避ける。新service/model/Trino/APIへの接続や既存server停止は0。

正規triggerとsource index意味差による初期restore失敗、platform probeとartifact-copy経路による
SDK失敗3件を保持し、未知結果を成功へ書換えていない。使い捨て複製のmissing artifactと外部URIは
SDK0で拒否した。前後で稼働中の59tables全行・16runs/1320points/16artifacts/index、
元export77entries、Notebook/assets80filesが不変だった。元backupや失敗cloneを削除していない。

最終独立照合は保存26inputs不変、SDK2回のowner/worker終端と共通view hash、
失敗3SDKの保持、SDK後の全59tables/URI18差分/15artifacts/export77一致を確認した。
`independent-clone-final.json`と`independent-sdk-final.json`へ結果を分離して保存する。

全local回帰63suites/2363tests（2362PASS・既存optional1skip）が61.551秒で成功した。
成功restoreとSDK全5attemptsは78files・3,241,218B（実割当3,420,160B）で、追加envは不要。
証拠は
`tmp/local-experiment/mlflow-restore-checks/e63090a6c3ad4e08a49161244f9a27ba/`。
[復元runbook](../../../../tools/local_experiment/mlflow/README.md#restore-an-inactive-local-clone)を参照。
利用停止時もclone/証跡/元exportを保持する。今回の機能はlocal-partialであり、サービス切替・
他host移行・本番復旧や旧WP全体の受入を主張しない。

## 2026-09-18: fixed Notebook consumer — local acceptance complete

[WP-L4-NB-001](WP-L4-NB-001_JupyterHub_bootstrap.md)で、専用Python kernelの固定Notebookから
loopback Trackingの評価experimentを読む。既存12評価runをsafe projectionで表示し、
別experimentの4benchmark runを含めない。モデル推論・新評価・MLflow追記・autologは行わず、
suite/provider/case signature、cost NULL、import時刻の意味を維持する。
Notebook側の最初の読取は既存Go Reader経由の固定fixture SELECT1回で、直接PySparkではない。

実Notebook `fcd6b48ab9b84f86bba01058cd909087`、`093b1e1c4ad34f3aa01d71cc8413edfd`で
各12評価runを表示し、Reader/kernel cleanupも確認した。MLflow59tablesの全行multiset・16artifacts・
indexは前後同一、全16runs/1320pointsを維持した。新推論/MLflow書込は0。
初回のReader前失敗と接続file配置修正、full回帰と修正後focusedの区分はNB-001に記録する。
保存射影とindexを含む独立照合62checks/104inputsが成功し、各12評価runとbenchmark4件除外、
既存MLflow59tables全行/16artifacts/indexの保持を確認した。追加API呼出しは0。証拠は
`tmp/local-experiment/notebook-checks/8d4a2ec9c0f5413e85009312de57faa9/`。
[Notebook runbook](../../../../tools/local_experiment/notebook/README.md)を参照。

## 2026-09-18: observation-signals evaluation import

新しいSRE `observation-signals-v1`評価を既存Trackingへ追記した。sourceは
`sre/21b4281f95014941b91384825a4dcb7a`、8cases×2の16保存推論だけで、新推論は行わない。
既存importerとstoreを使い、専用server停止→全state export→取込→同一取込→再起動を確認した。
exportは31files/1,762,594 bytesをprivateに保持し、既存backupを置き換えていない。

初回import `6ca069b53a5945109852226f7262b5a8`は1run/110 metric pointsを追加、
再取込`d6fa8648596a446996ce881eea0d80a5`はrun/metric/params/artifactの追加0。
新MLflow runは`d35f31449d054832a536f045d269d60a`で、FINISHED・index completedを確認した。
全16runsは評価12＋benchmark4、全1320 metric points、全16artifactsとなった。
旧15run・59tablesの全既存行・15artifactsを保持し、追加5tablesの全行が新runに帰属する。
独立照合で110points・9params・10tagsとsourceのexact一致、元24source files不変を確認した。
costはNULLのままで数値metricへ補完しない。suite/case signatureを分離し、一般診断品質とはしない。

同じstore UUID・loopback `127.0.0.1:18550`設定でhealthyに再起動した。
APIの固定run GET1回でも35 latest metrics・9params・10tagsが一致した。全110履歴はSQLiteで別途照合した。
新API検証はMLflowに限定し、Qwen/AgentHouse/Loki/Trino/Sparkは再実行・再検査していない。
製品source変更はなく、直前cc11ff9のlocal全2271tests（既存optional1skip）を継承する。
証拠と完全exportは`tmp/local-experiment/mlflow-sre-signals-checks/f2c0f8f220a743409d6ffb1985e2e8c2/`。
停止は既存`local-mlflow-stop`で行う。保持runの削除やexportの上書きをrollbackには使わない。

## Context

このWork PackageはLevel L4「OSS Databricks再現」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

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

`level:l4`, `area:ml`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L4-ML-001: MLflow tracking.
Read AGENTS.md, related ADRs, and this Work Package.
Use ADR-0034 local commits without GitHub Issue/PR/publication.
Include local tests, evidence, docs and stop/recovery instructions.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-17 local Tracking increment

ADR-0034により上記の歴史的PR/CI受入をlocal実行gateにしない。
WBSのSpark→JupyterHub→MLflow依存全体を完了したとは扱わず、独立したAgent eval Trackingを先行する。

- [x] MLflow 3.16.1/Python 3.12の専用ARM64環境をhash固定し、標準UIをloopbackのみで起動する。
- [x] 全6suiteの保存receipt/attemptsを整合検証し、失敗・未完了・不正・重複を隠さない。
- [x] suite/provider/model/case signatureを保持し、異なる評価範囲を同じ品質値へ混ぜない。
- [x] NULLのtokens/cost/過去開始日時を0やimport時刻で補完しない。
- [x] 同一source再importは追加0、部分書込の再開は既知runのreadbackを前提とする。
- [x] 停止→再起動後も記録を保持し、所有不明processやstoreを操作しない。
- [x] 元証跡・既存サービスを保持し、モデル再評価・外部LLM APIを呼ばない。

専用stateは`tmp/local-experiment/mlflow-tracking/`、API/UIは`http://127.0.0.1:18550`。
importはserver停止中のみ、共通排他lockでstart/stop/importを直列化する。
新たなAgent workflowを実行しないためAgentHouse/Pawprintの評価監査を二重生成しない。
原receiptのtask/traceは相関用であり、import時刻を評価開始時刻に読み替えない。

専用HOME/XDG/TMPと限定PATHを使用し、telemetry/job executionを無効化する。
MLflow 3.16.1起動時のDocker孤児sandbox cleanupを避けるためDockerをPATHに置かず、
Assistant session領域も専用TMPで隔離する。Tracking以外のAssistant/Jobs/Model Registry操作は対象外。
自動起動・外部bind・認証統合・共有利用は追加しない。

[Runbook](../../../../tools/local_experiment/mlflow/README.md)に再現、停止、保存・export境界を記録する。
新storeは元receiptから再構成できる派生実験データだが、自動削除や保持backupの上書きは行わない。
本番運用、認証/認可、複数writer、Spark/Notebook連携、model serving/registryは未検証・未達。

## 2026-09-17 local Tracking functional evidence

専用MLflow 3.16.1/Python 3.12をnative ARM64で起動した。`uv.lock`は91 entries、
87 packagesを導入し、専用venv約454 MiB・setup cache/evidence約532 MiBを実測した。
setup証跡は`tmp/local-experiment/mlflow-setup/e7a23fd14b6945bfbe1774c2cfce86c4/`。
専用store UUIDは`f239d270-c987-430a-bd3b-031bcaeec9b0`、API/UIは`http://127.0.0.1:18550`。
新storeのSQLite・artifacts・owner/index・HOME/XDG/TMPを0700/0600で保持し、
限定PATH・telemetry/job execution無効・worker 1で起動する。host全体設定は変更していない。

全6suiteの保存11 evaluation runsを、11 MLflow runs・1118 metric pointsとして取り込んだ。
母集団は実Qwen152callsとmock10calls、全162callsで、新推論・新AgentHouse監査は0。
同一source再importは新run0・追加metric0。source hash・固定case signature・provider/modelを保ち、
不正source・未知のcreate結果・既存run競合を拒否し、既知IDの部分書込だけ読戻して再開する。
raw prompt/response・SQL・credentialを取り込まず、費用や過去開始日時のNULLを0へ補完しない。
MLflowのrun開始/終了は取込処理の時刻で、sourceの評価開始日時ではない。

初回は`tmp/local-experiment/mlflow-tracking/imports/86a99022a1a346cb937ace741d5bd4be/`、
再取込は`imports/90cf426b6e3b4ecfad82c5d96aca1d67/`。標準UIの **Model training → Runs** で
11件一覧、SREの16/6/8case構成を区別した比較、個別safe artifactのcost/source_started_at NULLと
元task/trace相関を確認した。GenAI画面の0 tracesはこの保存評価の表示先ではない。
UI証跡は`tmp/local-experiment/mlflow-checks/1251a41cfdc141ef8985546ab16fe925/ui-verification.json`。

稼働中import拒否、同PIDへの再start noop、停止・再起動保持、unknown Host拒否、
CORSの固定origin許可/foreign拒否を実測した。server側UI telemetryは既存無効化環境変数により
`disable_ui_telemetry=true`・rollout 0だった。Settingsの初期Onは別のbrowser参加設定であり、
server gateを上書きしない。検証browserの設定もOffにした。

停止失敗2試行はshutdown中のmacOS `ps`表示が原因で、証跡を保持した。
状態がSのままargvだけ`(python3.12)`へ変わる実遷移を捕捉し、TERM前の厳密な所有照合を維持したまま、
TERM後は同PID/UID/PGID/start timeの消滅を期限内で待つよう修正。追加signalは送らない。
修正後`tmp/local-experiment/mlflow-checks/8bbe03ad6cd9488da46a486551f71b66/`で停止・再起動が成功した。

独立監査`tmp/local-experiment/mlflow-preflight/96cd337ad2a54b389c48c8cdf233854e/independent-summary.json`
で11 sources・1118 points・59 SQLite tablesのDDL/全行・11 artifactsを照合し、取込後→再起動の不変を確認。
旧2489 files/985 directoriesのhash・mode・mtime・一覧、AH428件、履歴4tables（3/72/1/6件）と5DDL、
Kube6 Pods/27 resources/6 ConfigMaps/3 Secret UID、Docker3も不変だった。
Trino fixtureとOpenMetadataは先行sealed証跡の継承であり、今回fresh readは行っていない。
stateの実割当は2334720 B、DB1314816 B、11 artifacts48091 B。
owned2process RSS合計512688128 Bは測定時点の値で、共有ページの重複を含み得る。

初回全回帰はprivate log wrapperのumask077継承で既存権限テスト1件が偽失敗した。
製品コードを変えず、検証childだけ標準umask022として再実行し、初回logも保持した。
次の回帰ではoffline test 2件が稼働中18550へ実bindして失敗したため、socketをfake化し、
衝突拒否の回帰1件を追加した。これも製品runtimeの変更ではなく、失敗logを保持した。
正式`local-regression-final.log`は42 suites・1064件（1063成功・従来optional OTel 1skip）でexit0。
新MLflow focused52件（runtime30/importer22）を含む。集計は同checksの`regression-summary.json`。
設計81documents/関連14tests、secret filenames・diff検査も成功し、更新3ノートのrepo/Vaultをbyte照合した。

Trackingのlocal-partialであり、Spark/JupyterHub依存全体、Registry/Serving/Assistant、
複数writer、export復元、本番・人間受入の完了は主張しない。
次の独立候補はSpark 4.1.3の最小native実験。必要なKubernetes 1.32以上は現k3s 1.33.4で満たすが、
この節目では純粋contractの準備だけで、Spark image pull・runtime実行はまだ行っていない。

## 2026-09-17: saved Spark benchmark Tracking

WP-L4-BENCH-001の保存済み成功/失敗receiptを新規推論やquery実行なしで読み取り、
別experiment `shirokuma-local-spark-benchmark-v1`へ取り込む。
1 sourceをengine（Spark/Trino）×case（q1_sums/q6）の4 runsに分け、
各sampleの順序、exact結果hash、文字列snapshot ID、測定条件と欠測数を保持する。
元11評価runsのexperiment/index/artifactsは独立して保持する。

読取処理は固定receipt bundleの所有者・権限・symlink・前後hashと型を検証する。
outer/native/access/tunnel、同UID Pod、snapshot/file seal、queryと結果を相関し、
allowlist JSONだけを出力する。不正・稼働中・source変更はimport前に拒否し、
validな失敗/中断sourceはpartial観測のままFAILEDとして扱う。

`client_observed_ms`は両engineのclient境界であり、TrinoはHTTP/JSON処理と途中の
durable journal書込を含む。Sparkはsql→collectで、Trinoのserver時間/CPU/inputは
`trino_server_*`へ分ける。Trino先行・共有cache・異なるresource条件のため性能順位を主張しない。
SparkSession startup/native全体時間はsource共通contextとexact ns artifactに保持し、
4回の独立観測として加算しない。過去のTrino version/resourceはunknown、期待versionは別項目。
MLflow日時はimport時刻。保存済みTrino query開始時刻は別context、Spark sample日時はunknown。

既存停止/排他lock/専用workerを再利用し、取り込み前に全stateをprivate保存する。
既知runの部分書込だけreadbackで補い、create応答不明は採用も再作成もしない。
同一sourceの再取込はrun/metric/artifact/indexを変えず、操作journalだけを新規に残す。
raw SQL/log/Pod/credentialはMLflowに複製しない。
Notebook/autolog・Registry/Serving・復元/移行・本番受入は本増分の対象外。

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
