---
project: Shirokuma
doc_id: "WP-L4-NB-001"
title: "WP-L4-NB-001 JupyterHub bootstrap"
status: local-partial
created: 2026-07-05
updated: 2026-09-18
version: "0.3"
area: "workpackage"
tags: [shirokuma, workpackage, l4, notebook]
---

# WP-L4-NB-001 JupyterHub bootstrap

## Summary

Notebook環境を整備する。ADR-0034のlocal実験では、固定Notebookを専用Python kernelで実行し、
既存ReaderとMLflow Trackingの読取へ接続する最小部分を先行した。実kernel2回と
独立した保存証拠の62項目照合が成功した。
JupyterHub、複数ユーザー、直接PySpark、学習・autologの完了は含めない。

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
