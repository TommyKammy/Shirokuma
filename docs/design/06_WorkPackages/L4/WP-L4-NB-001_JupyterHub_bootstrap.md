---
project: Shirokuma
doc_id: "WP-L4-NB-001"
title: "WP-L4-NB-001 JupyterHub bootstrap"
status: local-partial
created: 2026-07-05
updated: 2026-09-20
version: "0.16"
area: "workpackage"
tags: [shirokuma, workpackage, l4, notebook]
---

# WP-L4-NB-001 JupyterHub bootstrap

## Summary

自然文からsnapshotの正確な件数を表示する限定Lab入口は、修正後5caseと保存再表示で受入を確認した。
保存比較候補をMLflowへ1回記録し、同tokenの再表示で追加writeを避けるLab入口を実UIで確認した。最終保持・非稼働clone復元も成功した。
Notebook環境を整備する。ADR-0034のlocal実験では、固定Notebookを専用Python kernelで実行し、
既存ReaderとMLflow Trackingの読取へ接続する最小部分を先行した。実kernel2回と
独立した保存証拠の62項目照合が成功した。さらに別の登録templateへcurrent-head Readerの
型付きrequestを渡し、選択tableのpreviewと従来無指定実行の互換を実kernelで確認した。
同じ登録templateのrequestを自然文から選ぶAssistも定型2assets経由で実5case確認した。
保存済み固定学習結果を表と残差SVGで読む別templateも、実kernel初回で成功した。
保存Iceberg学習のprovenance・16holdoutを確認する別templateも、実kernel初回1.108秒で成功した。
Notebookから所有workerへ固定Iceberg学習を依頼する入口も、初回86.739秒で成功した。
型付き単一等値条件の登録previewも自然文から定型assetsへ接続し、実3caseと独立保存照合で確認した。
保存学習を選択・編集・予測する単一ユーザーJupyterLabで、画面実行・保存/再読込を確認した。所有kernel/serverの停止・再起動後の保持も確認した。
Labから自然文table previewを呼ぶ別Notebookも、修正後の等値preview/unsupportedを実画面で確認した。
保存データでRidgeのalphaを変えるoffline比較も、Labで実1fit・表示/保存/再読込を確認した。
保存比較一覧から候補を選び、再fitせず新入力を予測するLab入口も実UI受入を確認した。
Labの固定学習workbenchで、初回unknownを追加fitなしで回復し、別の新規正常実行とレビュー/一覧/編集予測・保存/再読込を確認した。
JupyterHub、複数ユーザー、直接PySpark、kernel内fit・autologの完了は含めない。

## 2026-09-20: natural-language snapshot count — local acceptance complete

[固定COUNT Notebook](../../../../examples/local_count_workbench.ipynb)を初回だけ配置し、`count(table, request)`で明示tableの全件またはtyped単一等値を数える。schemaと要求だけをQwenへ1回渡し、host検証後に既存Count Readerへ接続する。任意SQL/集計式/row limitは生成せず、assets実行・MLflow読取/書込もない。旧previewと既存6Notebookを保持する。
成功時はcanonical文字列の正確な件数と集計snapshot/条件を表示する。観測した0件、計画unsupported、送信後unknownを区別する。`recover_count(token)`は当時のsource seal/receipt/resultを照合する保存再表示で、追加model/readは0、現行sourceや現在件数の受入とは分ける。過去PIDを現在のownerとして照会しない。
初回unknownはCount Go childがKUBECONFIGを継承せずruntime開始前に失敗したもの。原23filesとunknown statusを保持し、`close_unstarted_count(token)`で開始前の厳密な保存状態とPID/PGID消失を確認して別recovery.jsonだけを追記した。SQL/tunnel開始済み・不明状態を閉じず、自動retryや成功/0件への変更もしない。
Go環境継承とCOUNT監査targetの修正後、全件2を実UI、等値1・等値0・範囲/未知列拒否を同じ`count()` APIの実接続で確認した（3passed/2unsupported）。全件2の保存再表示とsave/reloadでもNotebook bytes/mode/mtimeが一致した。旧71＋初回失敗23files・MLflow/旧監査を保持し、期待する新監査だけを追加した。一般自然文理解、任意Notebook生成、JupyterHub/複数ユーザー・本番受入は未達。操作は[Lab runbook](../../../../tools/local_experiment/notebook_lab/README.md)、全実測は[Milestones](../061_Local_Experiment_Milestones.md)を参照する。

## 2026-09-20: saved comparison recording in MLflow — local acceptance complete

Labの[比較記録Notebook](../../../../examples/local_comparison_recording.ipynb)で、保存比較一覧から明示tokenを選び、`record_comparison(token)`で候補/参照/差の指標をMLflowへ1回記録する入口を実UIで確認した。
既存Notebookは保持し、このtemplateは初回だけ配置する。元比較の実行時刻は未記録のまま保持し、今回の記録時刻と混同しない。

共有Lab所有lockをcleanupまで保持する。初回は専用ML workerが同storeを停止・cold backup・import/読戻し・元runtime状態へ復帰し、完成receiptの再表示ではworker/backup/runtime操作/API/追加writeを行わない。
未解消pending/unknownは再送を拒否し、source/保存receipt/artifactの変化も成功扱いにしない。元比較と保存予測は保持し、新fit/Reader/LLMを呼ばない。
実UIで初回登録と同tokenの再表示が成功し、後者の前後snapshotはruntime PIDを含め同一。attempt/backupは各1件だけで、初回/再表示Notebookを保持した。保存/再読込でtoken・出力・bytes/mtime/modeを保持し、cloneのSDK読戻しも成功した。旧kernelのmoduleを更新するため所有6kernelを正常終了し、Lab再起動後も全6Notebookのbytes/mtime/modeを保持した。記録しただけで品質改善・独立評価・Registry昇格やServingを意味しない。
詳細は[MLflow WP](WP-L4-ML-001_MLflow_tracking.md)、[Lab runbook](../../../../tools/local_experiment/notebook_lab/README.md)、[Milestones](../061_Local_Experiment_Milestones.md)へ記録する。

## 2026-09-20: Lab fixed Iceberg training and prediction — local acceptance complete

Labの[固定学習workbench](../../../../examples/local_lake_training_workbench.ipynb)で、新しい固定学習→指標/16holdout/残差SVG→保存学習一覧→編集入力2行の予測→保存/再読込を確認した。
引数なし`train_lake()`が既存ML専用workerで64行・48/16分割のpipelineを1回実行する。初回seedだけを行い、既存Notebookの編集と保存出力は保持する。
学習セルの再実行は新runを追加するが、予測セルだけの再実行では学習しない。実行前に古いreview/predictionを消し、固定例外を日本語で表示する。

初回はfit/保存済みだがruntime復帰の例外でpipeline/親statusがunknownとなった。原因は未記録・未確定のまま、原証跡を保持した。
同storeのruntimeを別操作で復帰後、`recovery_attempt`に元のLab実行IDを指定する`recover_training()`で、追加fit・process操作なしに保存結果を照合した。原unknownを変更せず、不変のrecovery receiptを追加する。
回復結果のUI表示/2行予測・保存/再読込とLab stop/restart後の保持に成功した。その後、別途明示した新規学習1回は全cleanup/runtime復帰が正常で、UI一覧5 verified・レビュー・編集予測・保存/再読込にも成功した。

未解消unknownは次の学習/query/比較/start/stopを拒否し、全cleanupまで共通owner lockを保持する。既存Go cacheを共有し、新dependency/serviceは追加しない。
これは単一ユーザーLabの固定実験であり、Hub/複数ユーザー・kernel内fit/autolog・一般品質保証は未達。失敗と回復・検証は[Milestones](../061_Local_Experiment_Milestones.md)に記録した。

## 2026-09-20: saved comparison model prediction — local acceptance complete

Labの[保存比較予測例](../../../../examples/local_comparison_prediction.ipynb)から、完成した比較tokenを再読込する入口を追加した。
`comparison_prediction.ipynb`の保存比較一覧からtokenを明示選択し、`x1,x2,x3`の新入力1–64行を指定する。
一覧は固定private directoryの最大128件を対象に、verified/partial/invalidを分け、
比較/元学習token・alpha・metrics・source identityをtoken順に表示する。品質/新しさによる順位ではない。
必須receipt欠損はpartial、検証不可はinvalidで、破損の断定とは区別する。選択候補の予測は入力順で返す。
`load_saved()`/`predict()`は保存6filesのbytes/identityを前後照合し、元学習への`validate_result`束縛を再確認する。
過去PIDは記録内の整合確認だけに使い、実行中processとして扱わない。追加fit・Reader・LLM・MLflow処理APIはない。
既存編集Notebookを保持して初回だけ配置し、入力と予測はNotebook保存で残す。
別processのcold予測18行と独立Decimal一致を確認した。実UIでは一覧1verifiedから候補を選び、2入力を編集して予測・保存/再読込が成功した。
全回帰90suites/3043実行（3042PASS・既存1skip）と、4kernelsのstop/restart後のNotebook/旧保持データ一致を確認した。
未知データでの正解精度や元の探索比較の独立評価は主張しない。[Lab runbook](../../../../tools/local_experiment/notebook_lab/README.md)、
[Milestones](../061_Local_Experiment_Milestones.md)を参照する。

## 2026-09-20: offline Ridge comparison — local acceptance complete

Labに[固定Ridge比較例](../../../../examples/local_ridge_comparison.ipynb)を追加した。
`ridge_comparison.ipynb`で保存Iceberg学習tokenとalphaを選び、既存ML専用環境のworkerへ1fitを依頼する。
保存alpha=1モデルと同じ48行で候補を学習し、trainと再利用holdout16行の指標・予測差を表で表示する。
alphaは0.001〜1000の有限数値。候補はMLflowへ登録せず、API/Reader/LLM呼出し、新依存/serviceを追加しない。
入力・結果はNotebook保存とprivate比較証跡へ残す。kernel内fit/autologではない。

preview/compareはLab所有lockを共用し、互いの未確認実行を点検する。未確認比較が残る場合は、
queryの有無によらず新実行・start/stopを拒否する。Interruptは1回、所有workerの終了を待ち、自動retryしない。
同じ既視holdoutを使う探索であり、独立した最終評価や一般品質の改善を示すものではない。自動model選択も行わない。
保存token`6df9b9e5a40b4035b7865f8435fdd291`/alpha10で実1fitし、3code cells・16行の比較表と保存/再読込を確認した。
再利用holdoutのRMSEは0.163043→0.543633へ悪化した。候補のMLflow登録は0、元学習は保持する。
runtime34とquery/compare/core37のfocused、全回帰87suites/3017実行（3016PASS・既存1skip）が成功した。
所有workerの終了・Lab stop/restart後のNotebook/旧データ保持も確認済み。独立60桁Decimalとの74数値照合も成功し、追加fit/APIは0。
操作は[Lab runbook](../../../../tools/local_experiment/notebook_lab/README.md)、実測は[Milestones](../061_Local_Experiment_Milestones.md)を参照する。

## 2026-09-20: interactive natural-language query workbench — local acceptance complete

JupyterLabに[query workbench例](../../../../examples/local_query_workbench.ipynb)を追加した。
`query_workbench.ipynb`でtable・自然文request・`filtered`の真偽を明示し、`preview()`を1回実行する。
kernelが専用DataOps workerを起動し、既存Qwen Assistへ接続する。モデルはschema/依頼だけで1回提案し、
host検証済みのReader previewを、列名・値・NULLをescapeしたHTML表として表示する。
`filtered=True`は型付き単一等値条件、falseは従来の列/件数だけ。対応範囲は[Assist WP](WP-L4-AGENT-001_Notebook_to_Dagster_Agent.md)を継承する。

新依存・serviceは追加せず、親kernel環境と既存の保存学習Notebookを保持する。初回配置後の編集Notebookを上書きしない。
query全体とcleanupの間はLab所有lockを共有し、start/stopとの競合を防ぐ。取消はInterruptを1回使い、
所有workerの既存cleanup待機を行う。過去queryのcleanup不明時は新実行・start/stopを拒否し、自動retryしない。
依頼・選択行はprivate query証跡や保存Notebookへ残る。Labのhost権限kernelという境界は変わらない。

初回queryのchild設定不足は失敗証拠を保持して修正した。修正後は画面からbigint等値条件の1行を表示し、
範囲指定はunsupportedとなった。両結果のNotebook保存/再読込、worker/Reader終了、query中の所有lockを確認した。
新依頼を増やさず保存証拠だけで、検証helperのsnapshot型誤期待も訂正した。
全回帰85suites/2990実行（2989PASS・既存1skip）が成功。その後CLI stopのimport pathを修正し、
runtime31testsと実stop/restartを別途確認した。旧32filesとMLflow19runs/1347points/19artifactsを保持した。
手順は[Lab runbook](../../../../tools/local_experiment/notebook_lab/README.md)、実測は[Milestones](../061_Local_Experiment_Milestones.md)を参照。
任意SQL/Notebook生成・一般的自然文理解、Hub/複数ユーザー分離、直接PySpark/autologの受入は含めない。

## 2026-09-20: single-user local training workbench — local acceptance complete

保存した学習をブラウザーで試す単一ユーザーのJupyterLab作業台を追加した。
専用環境・private workspaceを使い、`127.0.0.1:18560/lab`だけで待受ける。
private tokenで認証し、利用可能なkernelは`shirokuma-local`だけとする。既存固定Notebook runner/環境は維持する。
`local-notebook-lab-sync/start/status/stop`の手順は[Lab runbook](../../../../tools/local_experiment/notebook_lab/README.md)を参照。

[登録workbench例](../../../../examples/local_training_workbench.ipynb)は保存学習の一覧からtokenを選び、
metrics・holdout・残差を表示し、Iceberg学習では`x1,x2,x3`を編集して保存modelの予測を計算する。
syntheticのreviewにも対応する。新fit/Reader/LLM/MLflow書込みは行わず、Notebook保存で入力・出力をworkspaceに残す。
再起動時に利用者の編集をtemplateで上書きしない。stopは所有kernelを閉じてserverを停止し、不明なcleanupは記録に残す。

実ブラウザーで認証し、保存学習3件からIceberg学習を選択してHTML reviewと残差SVGを表示した。
入力`x1=0.5`へ編集した2行の予測が独立Decimal計算と一致し、Notebook保存/再読込で入力・結果が残った。
4code cellsを実行し、表示errorは0。UI操作中の一覧cell再実行1回を含み、新Reader/fit/LLMはない。
初回stopの一時的なprocess照合差を修正し、所有証拠を再利用して停止を完了した。
TIME_WAITによる再起動拒否も、active listenerを迂回しないprobeの修正後に回復した。
保存Notebookのbytes/mode/mtimeと再読込cellは不変。所有2kernelsの通常stopと直後のrestartも成功した。
新runtime28testsを含む全回帰84suites/2972実行（2971PASS・既存1skip、72.309秒）が成功した。
最終保持照合で旧31保存filesとAgentHouse545件は不変。初回失敗のlog/所有証拠は保持する。
kernelはhostユーザー権限で動き、workspaceはsandboxではない。この例の読取範囲と任意の追加cellの権限は区別する。
JupyterHub/複数ユーザー分離、直接PySpark、kernel内fit/autolog、本番運用は未達のまま。
固定学習workerの外側runner handshakeはLabへ移植せず、学習起動には既存固定runnerを使う。
保持対象は保存学習3件とMLflow19runs/1347points/19artifacts・AgentHouse545件。検証結果は
[Milestones](../061_Local_Experiment_Milestones.md)へ記録する。

## 2026-09-20: natural-language typed equality preview — local acceptance complete

別の固定[filtered preview template](../../../../examples/local_filtered_table_preview.ipynb)を定型2assetsへ対応付けた。
`local-notebook-assist-filtered`からschemaだけを使うQwenの1提案を検証し、型付き単一等値条件で読取る。
実3caseは等値preview 1行/2assetsがpassed、範囲と契約上書き依頼がunsupportedとなった。
この入口はkernel・生成codeを実行せず、従来Notebook/Assistのprofileを維持する。Hub/任意Notebook変換の完了ではない。
独立保存照合も成功した。条件・literalの保持範囲・失敗時の扱いは
[Assist runbook](../../../../tools/local_experiment/notebook_assist/README.md)、
実測は[Milestones](../061_Local_Experiment_Milestones.md)を参照する。

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

## 2026-09-20: saved training Notebook review — local acceptance complete

`local-notebook-training-review`で明示した32桁lowercase hexの学習attempt tokenを、
別登録template `examples/local_training_review.ipynb`へ渡す。既存専用環境の3code cellsで、
保存済みRidge結果のtrain96/holdout32/平均baseline指標表、32holdout行と残差SVGを表示する。
表示対象は固定合成データの過去の学習結果で、Notebook内の新学習や汎化品質評価ではない。
元の無指定Reader/評価Notebookとtable-previewは維持し、依存・常駐service・global kernelspecは追加しない。

所有store marker/完了receipt/workload hash/artifact bytes、固定dataset/split、係数replayと指標を照合し、
既存MLflowへexperiment/runのGET2回だけを行う。FINISHED/identity/params/tags/9 latest metricsを要求し、
新SDK/Reader/Go build/LLM/fit/MLflow write/autologは0。全metric historyの再読取とは区別する。
parentはkernel終了後もpublic-shaped結果をprivate保存元へ束縛し、同tokenでの別run/係数/指標を拒否する。
source/template変化、unsafe path、Reader証跡混入、保存失敗やunknown cleanupを成功にしない。

明示tokenは親環境のReader/request値から分離する。新review token配下のprivate
`training-review.json`/`executed.ipynb`/kernel証拠を保持し、元training attemptのtokenと区別する。
残差はactual−predictionで、横軸は固定holdout順序。公開形JSONにも係数/holdout値を含むが外部公開しない。
3code cellsの実行期限は合計60秒で、kernel準備/終了時間は別。元Reader profileの900秒は維持する。
利用停止は新reviewを止め、既存の所有kernel取消処理を使う。元run/証跡/backupは保持する。

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
[学習レビューrunbook](../../../../tools/local_experiment/notebook/README.md#review-saved-training)を参照。
local-partialを維持し、Hub/複数ユーザー/直接PySpark/Notebook学習/autolog/本番受入は未達。

## 2026-09-20: natural-language Notebook preview — local acceptance complete

登録table-preview templateの選択requestを、短い自然文から生成する限定入口
`local-notebook-assist`を追加した。明示tableを既存Readerでdescribeし、列名/型と依頼だけをQwenへ渡す。
columns順序・limitをhost検証した後、同じ登録templateに対応する`table_preview`と
`saved_evaluation_summary`をDagsterでmaterializeする。今回の実行はhost定義の2assetsで、
新しいkernel実行やHub統合、任意Notebook/code生成の受入ではない。

実5caseを各1回実行し、明示name,id/1件だけがpassed/2assets、filter・未知列・曖昧・SQLは
unsupported/exit2でSELECT/assets0だった。describeは全5caseで先行し、Qwen各1call、retry/cloud0。
acceptedは35.124秒、returned1/fetched2/truncated true・12保存評価run、全case cleanup true。
describeとSELECTは別時点でcurrent head更新を許容し、table UUID/schema SHAを最終照合する。
モデルの意味解釈保証や行順保証とは区別する。元の明示request入口・無指定Notebookは保持した。

新80testsを含む全local73suites/2583tests（2582PASS・既存optional1skip）が成功。
65sources/527保持files・MLflow16runs/1320points/16artifactsは前後一致し、AH旧519行を保持して新監査16件を追加した。
独立保存670checks（143意味＋527files）/693inputsが初回成功。16監査/local spans、2assets/12評価、
6Reader owner終了・25distinct terminal queriesを照合し、追加API/SDK/model/Reader実行0。
証拠は`tmp/local-experiment/notebook-assist-checks/b0398f85421f4100a8737a6d05c12ff5/`。
[Notebook Assist runbook](../../../../tools/local_experiment/notebook_assist/README.md)と
[Notebook Assets WP](WP-L4-AGENT-001_Notebook_to_Dagster_Agent.md)に操作・保存・取消の詳細を記載する。
JupyterHub・複数ユーザー・直接PySpark・学習/autolog・自然文一般品質・本番受入は未達を維持する。

## 2026-09-18: selected-table Notebook preview — local acceptance complete

`local-notebook-preview`はstdinのReader v1 requestを、別登録template
`examples/local_table_preview.ipynb`へ渡す。exact4keysはcommand=select/table/columns/limit、
4096B以下、許可schema smoke/local_experiment・1–16列・strict整数limit1–100とする。
current headだけを読み、任意Notebook/code/SQL、snapshot指定、filterは受け付けない。
既存無指定template/read_fixtureとNotebookAssetsの固定profileは維持する。
private canonical request.jsonとSHAを保存し、Reader argv・所有PID・返却typedrowsを同じrequestへ束縛する。
改変や未確認cleanupを成功へ補完せず、従来の取消grace/query drainを再利用する。

custom `fa53c3cc80924087af29586b02cd339e`は18.009秒、Reader
`a48a672833e70cdb5829c4860562c541`で新所有5列fixtureを読み、
columns=[ok,id,amount,name,day]/limit2からreturned2/fetched3/truncated trueを確認した。
その時点のtable UUIDは`96a4df79-78a0-4b85-a906-da654bf4200e`、
headは`1152123351964077594`。要求SHAは
`3fc19978f04ef6d92f02eea12dbfd71249dfd567cd869fff5ff26f5e7589b05e`。
無指定互換 `53a2f8a2603a4453a6937d20fff2e550`は18.262秒、Reader
`264e609249cf2d04e27ff4f3b2f86c39`で従来固定2行を保持した。
各3code cells・MLflow評価12run表示/別benchmark4run除外、Reader/kernel cleanup true。
新model/MLflow write0、今回の実Notebook失敗0。表示上限を全件数や一般性能保証とはしない。

selection14＋parameterized15＋既存fake24の53focused testsが成功。
全local68suites/2468tests（2467PASS・既存optional1skip、62.027秒）とsecret scanも成功した。
使い捨てfixtureは同UUIDでDROP1回、35.587秒で回収し、retained10tables/6projectionsの復帰、
pending null/cleanup trueを確認した。前後63sources/400保持files、MLflow16runs/1320points/16artifacts、
AH519は一致。新2runは33files・20,424,079B（実割当20,516,864B）。
private700配下で機密・rows・receiptは600、binary700、IPythonの公共startup/README複製だけ644を許容する。
独立保存検証は451checks（51意味検査＋400保持file個別）/110inputsで成功。
custom合法typedrows subsetと既定旧shape、各Reader4worker＋1drainの10distinct terminal Query IDs、
Reader/kernel計4PIDの不在・connectionfile削除、同UUID回収・保持状態を照合した。fixture SQLは別枠。
追加API0で、proof `independent-saved-verification.json` のSHA256は
`6d5f6ec59ad6053e51dae078e27b0b43fd698bae9587c93744886edf55526a5c`。
証拠は`tmp/local-experiment/notebook-selection-checks/59d9d0ca8ab741f4903ad9371fa05a88/`、
after SHA256は`614b4438f38fb061d958088df80bb5a97f38766d7754885611204d997c384edf`。
[Notebook runbook](../../../../tools/local_experiment/notebook/README.md)を参照。
local-partialを維持し、Hub/複数ユーザー/直接PySpark/学習/autologの受入へ拡大しない。

## 2026-09-18: fixed local Notebook — local acceptance complete

`tools/local_experiment/notebook/`に専用Python 3.12環境を置き、
nbclient 0.11.0、nbformat 5.11.1、ipykernel 7.3.0をlockする。
`examples/local_lakehouse.ipynb`は固定cell・空outputs・execution_count NULLのtemplateとしてGit管理する。
実kernelでの実行済みNotebook、connection情報、Readerログと検証receiptはprivate tmpへ分離する。
Hub/serviceの追加、global kernelspec登録、既存Python環境の変更は行わない。

Notebookは既存Go CLIの`query select --table smoke.fixture_v1 --columns id,name --limit 10`を1回だけ呼び、
既存Readerの型付き結果・snapshot・query drain/cleanupの成功を確認して表示する。
任意SQLや直接Trino/PySpark接続を追加しない。次に固定loopback MLflowの
`shirokuma-local-evaluation-receipts-v1`を読み取り、現12評価runのsafeな一覧を表示する。
別experimentの4benchmark runは混ぜず、suite/provider/case signature、unknown/cost NULL、
import時刻と元評価時刻の区別を維持する。新model呼出し、MLflow追記、catalog/data writeは0。

失敗・取消時は所有kernel/Readerの終了とReaderのquery drainを確認し、
不明な実行結果を自動再送しない。後続再実行は新たな明示実験であり、初回失敗の証跡を保持する。
既存Reader接続・MLflow storeの境界を再利用し、既存保持データやbackupをrollbackで削除しない。

今回のlocal受入条件は次の範囲に限る。

- [x] 固定templateを実kernelで実行し、各実行でReader1回とMLflow評価12runのread-only表示を確認する。
- [x] Readerの型付き行と元receipt、MLflowの固定experiment/indexとの対応を照合する。
- [x] 実成功2回/Reader前失敗1回のkernel終了を確認し、取消grace・unknown保持をfocused testsで確認する。
- [x] Git templateのoutputsが空のまま、private実行結果・connection・ログを保持する。
- [x] 既存MLflow run/metrics/artifacts/indexと65sourcesの前後不変を確認する。
- [x] focused testsとlocal回帰を実施し、修正前後の検証範囲を区別する。
- [x] 独立保存証拠の照合と最終設計/Vault同期を完了する。

今回の機能は固定Notebookのlocal-partialであり、JupyterHubの提供を主張しない。
[Notebook runbook](../../../../tools/local_experiment/notebook/README.md)、
[MLflow Tracking](WP-L4-ML-001_MLflow_tracking.md)、
[Local Milestones](../061_Local_Experiment_Milestones.md)を参照する。

## 実測と保持境界

| Notebook token | Reader token | 実行時間 |
|---|---|---:|
| `fcd6b48ab9b84f86bba01058cd909087` | `717c560803bf6696efe898512e3eff36` | 18.275秒 |
| `093b1e1c4ad34f3aa01d71cc8413edfd` | `f138f19a050a9af741066c355dae9303` | 18.241秒 |

各3code cells、固定Reader1回、MLflow評価12run表示でpassed/cleanup確認済み。
二つの明示実行であり、自動retryや性能benchmarkの結果とは扱わない。
初回`1f0c75ef56db4bad98d1143bda983cc9`は、Jupyterが接続file親のmode700を1700へ変更したため
private directory guardが拒否し、Reader/MLflow API前に失敗した。kernel回収とfailedreceiptを保持。
接続fileを`jupyter-runtime/`へ移す狭修正後、26focused testsと上記実2回が成功した。

runnerはReader statusをexact10fieldsで検証し、最初のnonException取消objectを保持する。
Readerは通常待機240秒・取消cleanup245秒、runnerはinterrupt後260秒のcell終了猶予を持ち、
その後kernelへshutdownを要求して30秒以内の終了を待つ。確認不能ならunknown/PID/証拠を残す。
forced kill、未知結果の自動再送、保持run/backup削除は行わない。

全local回帰は接続file配置修正前に62suites/2297tests（2296成功＋既存optional1skip）が成功。
初回fullの既存1test失敗はwrapper umask077でmkdir755が700となったfixture前提差であり、
通常umask022で再実行して成功し、双方のprivate600ログを保持した。
修正後は26focused tests（無害な実kernel2件を含む）と実2回で確認し、full再走行とは区別する。

`runtime-before-fixed.json`→`runtime-after.json`で65sources、MLflow59tablesの全行multiset、
16artifacts/indexが同一。16runs/1320metricsを維持し、secret scanも成功した。
独立最終照合は62checks/104inputsで成功。typedrows、同table UUID/head、10 unique terminal query IDs、
各12評価run表示とbenchmark4件除外、65sources/MLflow保存guardとの一致を確認した。
Reader/kernel各2PIDの消失とconnection file不在も確認し、追加API呼出しは0。
初期verifierのpublic query_id参照と無関係runtime filesへのprivate gate拡大という2誤前提を狭修正し、
元失敗logとfailed Notebook receiptを保持した。製品の追加再実行は行っていない。
証拠は`tmp/local-experiment/notebook-checks/8d4a2ec9c0f5413e85009312de57faa9/`、after SHA256は
`7c38441ad4fd372261a581c3d2cafcae298ec49f94c2390b677689e17c29472e`。
独立proofは`independent-saved-verification.json`、SHA256
`70d4f50fc66522c2662be0498dfad860c15d1c42429144c6b53a7611a30eaa1e`。

実割当は専用.venv 71,782,400B、checkdir uv-cache 82,808,832B、実3run成果物30,420,992B（Go binaries込み）。
保持データ/backupの移行は0。rollbackはコマンド利用停止と所有Reader/kernel終了確認までとし、
実行証拠・既存MLflow/fixture/backupを保持する。global kernelspec/Hubは追加していない。

## Context

このWork PackageはLevel L4「OSS Databricks再現」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。
以下は元のJupyterHub全体に対する歴史的条件であり、ADR-0034のlocal実行gateにはしない。

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

`level:l4`, `area:notebook`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L4-NB-001: JupyterHub bootstrap.
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
