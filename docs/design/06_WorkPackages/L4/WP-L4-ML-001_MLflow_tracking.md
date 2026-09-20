---
project: Shirokuma
doc_id: "WP-L4-ML-001"
title: "WP-L4-ML-001 MLflow tracking"
status: local-partial
created: 2026-07-05
updated: 2026-09-20
version: "0.9"
area: "workpackage"
tags: [shirokuma, workpackage, l4, ml]
---

# WP-L4-ML-001 MLflow tracking

## Summary

MLflowで固定合成データの学習、保存Agent evalとSpark/Trino測定を記録する。
専用SQLite/loopback標準UIで新学習1runと旧16runsを保持し、明示metric履歴・安全なJSON artifactを照合した。
固定Notebookのread-only表示と完全exportの非稼働clone復元も確認済み。
保存学習をNotebookの指標表・holdout行・残差SVGで確認する実kernel実行も初回で成功した。
Notebook/Hubからの学習・autolog、Registry/Serving、clone切替・他host移行は未達。

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
