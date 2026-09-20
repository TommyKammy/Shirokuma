---
project: Shirokuma
doc_id: "WP-L4-RAY-001"
title: "WP-L4-RAY-001 Ray integration"
status: local-partial
created: 2026-07-05
updated: 2026-09-20
version: "0.3"
area: "workpackage"
tags: [shirokuma, workpackage, l4, ml]
---

# WP-L4-RAY-001 Ray integration

## Local experiment scope — local acceptance complete（2026-09-20）

完成済みIceberg学習の保存64行を、単一ホストの固定Ray actor 2個へ32行ずつ渡し、既存逐次予測と全行・順序・model/source/request identityの一致を確認した。
専用Python 3.12 ARM64環境のRay 2.58.0を使い、wheelのcommit `cec0a091b12675bbf101cf8a350c8706107baee1`へ束縛する。同名release tagとの同一性は仮定しない。
操作は[Ray README](../../../../tools/local_experiment/ray/README.md)。保存tokenを明示し、fit・MLflow処理API/書込み・Reader・LLMは追加しない。

Rayの論理CPU 2・object store 128 MiB、loopbackのみのlocal clusterに限定する。これはOSのCPU/全メモリ使用量の強制上限ではない。
使用しないdashboard/metricsを抑止し、残るlistenerもloopbackであることを確認する。固定host codeと検証済みJSONだけを渡す。
private外側dir0700の中にRay用inner dirを置く。Rayがinnerを0777へ変更するため、外側の所有境界を保持する。
親期限180秒＋所有関係を確認した取消/終了待機60秒で、`ray.shutdown(wait_for_processes=True)`後の子process終了まで確認する。未知のcleanupは証拠を残して次回起動を止め、自動retryしない。

### Local acceptance

- [x] 専用環境の依存固定とfake検証を用意した（初版17tests PASS）。
- [x] 実2actor/64行の逐次一致と、loopback・所有processの終了を確認した。
- [x] 既存43files・MLflow21runs/1365points/21artifacts・AgentHouse560件の保持を確認した。
- [x] 実測・失敗/回復・容量・最終回帰結果を記録した。

実02 `e9b10fce7b474d5e879bb62350690edc` は4.932秒でpassed/cleanup true。2actor（PID8374/8373）が32行ずつ予測し、64行の逐次一致は最大差0・同一SHAだった。driverを含む実TCP listener 7件は全て127.0.0.1、所有processの終了を確認した。
利用モデルはtraining `c60d6f25d2944c249c811e800ce2b637` / MLflow run `4c298d5871c444dab289a41da84f6006`。新fitやTracking変更はない。
全回帰98suites/3173実行＝3172PASS＋既存1skip（77.470秒）、保持照合は0.402秒で成功した。

初回 `f3bc42678039419c832d3fa5d8777194` は5.890秒で `RAY_CLEANUP_UNKNOWN`、結果未採用。macOSの固定 `/bin/ps` がreal UID501/effective UID0となる所有判定差をRay専用検査で修正した。
別々のprocess/listener/file閉鎖照合2件後にowner lock下で `ray-closed-failures/`へ移し、原bytes/status unknownを保持した。signal/削除0で閉鎖し、新規実02を明示実行した。自動retryや初回成功とは扱わない。
証跡は `tmp/local-experiment/ray-checks/cebc2440e1a04fb9a0c848855a8b533f/`のactual-02/retention-02/full-01と初回閉鎖記録。独立60桁Decimalの64行再計算も最大差8.90e-16で一致した。容量と所有終了の最終照合は[local milestones](../061_Local_Experiment_Milestones.md)に記録した。

### Remaining boundary and recovery

multi-node・高速化・一般モデル品質・本番/Serving・任意code/runtime environmentは今回の受入対象外。失敗証跡・保存学習・backupを削除せず、確認済み所有processだけを終了する。常駐Rayは追加しない。
下記は2026-07-05の計画を保持した履歴。[ADR-0034](../../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)と[local development](../../04_Development/04B_Autonomous_Local_Development.md)により、履歴中のIssue/PR/CI gatesをこのlocal実行の待機条件にはしない。

## 2026-07-05 historical plan

## Summary

Ray distributed jobを動かす

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
You are working on Shirokuma WP WP-L4-RAY-001: Ray integration.
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
