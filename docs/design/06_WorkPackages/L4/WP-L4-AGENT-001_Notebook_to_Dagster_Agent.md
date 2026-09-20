---
project: Shirokuma
doc_id: "WP-L4-AGENT-001"
title: "WP-L4-AGENT-001 Notebook to Dagster Agent"
status: local-partial
created: 2026-07-05
updated: 2026-09-20
version: "0.4"
area: "workpackage"
tags: [shirokuma, workpackage, l4, agentops]
---

# WP-L4-AGENT-001 Notebook to Dagster Agent

## Summary

Notebookを再現可能なDagster assetへ接続する。ADR-0034のlocal実験では、登録済みの固定Notebookだけを
hostの定型2assetsへ対応付ける最小部分を実装し、実2回と独立最終照合が成功した。
選択tableの登録previewもschema2の2assetsへ接続し、実1回と独立保存照合が成功した。
任意NotebookやLLM変換は含めない。

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
