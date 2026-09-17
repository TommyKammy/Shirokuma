---
project: Shirokuma
doc_id: "WP-L4-ML-001"
title: "WP-L4-ML-001 MLflow tracking"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.4"
area: "workpackage"
tags: [shirokuma, workpackage, l4, ml]
---

# WP-L4-ML-001 MLflow tracking

## Summary

MLflowで実験とAgent evalを記録する。ADR-0034のlocal実験では、保存済み評価receiptと
Spark/Trino測定を専用SQLite/loopback標準UIへ取り込む。Notebook連携は未達。

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
