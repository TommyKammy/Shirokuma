---
project: Shirokuma
doc_id: "WP-L4-NB-001"
title: "WP-L4-NB-001 JupyterHub bootstrap"
status: local-partial
created: 2026-07-05
updated: 2026-09-20
version: "0.5"
area: "workpackage"
tags: [shirokuma, workpackage, l4, notebook]
---

# WP-L4-NB-001 JupyterHub bootstrap

## Summary

Notebook環境を整備する。ADR-0034のlocal実験では、固定Notebookを専用Python kernelで実行し、
既存ReaderとMLflow Trackingの読取へ接続する最小部分を先行した。実kernel2回と
独立した保存証拠の62項目照合が成功した。さらに別の登録templateへcurrent-head Readerの
型付きrequestを渡し、選択tableのpreviewと従来無指定実行の互換を実kernelで確認した。
同じ登録templateのrequestを自然文から選ぶAssistも定型2assets経由で実5case確認した。
JupyterHub、複数ユーザー、直接PySpark、学習・autologの完了は含めない。

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
