---
project: Shirokuma
doc_id: "WP-L5-ACT-001"
title: "WP-L5-ACT-001 Data Activator style alerts"
status: local-partial
created: 2026-07-05
updated: 2026-09-20
version: "0.3"
area: "workpackage"
tags: [shirokuma, workpackage, l5, ops]
---

# WP-L5-ACT-001 Data Activator style alerts

## Summary

専用taskの失敗alertから、Dagster sensorを通して既存の決定的SRE診断を1回だけ起動するlocal実験を完了した。
[ADR-0034](../../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)の範囲で実装・実証した限定機能であり、一般的なData Activatorや本番運用全体の完了とはしない。
操作は[local activation README](../../../../tools/local_experiment/activation/README.md)、実測は[Milestones](../061_Local_Experiment_Milestones.md)を参照する。

## Local scope and recovery

- 既存Linux ARM64 Grafana13.2.2/plugin4.21.3を使用する。同じvolume・image・認証・network・Reader設定を保持し、cold backupと停止した旧containerを残す。
  既存のloopback18030、1 CPU/1 GiB/PID上限256・read-only rootfsを維持する。別architectureへの移植は対象外。
- 既存rule 0件を確認してAlerting engineを有効化し、専用ruleをpausedで作成する。
  `notifications.alerting.grafana.app/v1beta1`のReceiver/ RoutingTree APIで、integrationのない専用contact pointと一致条件付きrouteを作り、readback確認後にそのruleだけをunpauseする。
  元の通知設定を保持し、Grafana自動生成branchは専用receiver用の子route追加だけを許容する。自動生成された兄弟routeの列挙順以外を無視しない。
  [公式仕様](https://grafana.com/docs/grafana/latest/alerting/fundamentals/notifications/contact-points/)に従う送信なし設定であり、通知テストは送らない。
- コマンド1回の範囲でnormal→新failed fixture→firing→診断→同じfiringの再観測/再tick→新success fixture→normalを確認する。
  Dagster sensor/run_key/cursorとdurable claimを束縛し、同じfingerprintを再実行しない。unknown claimを自動retryせず、解消確認前は別alertも抑止する。
- 診断は既存MCPの3readと監査4件/Loki deliveryを再利用する。既存監査イベントを変更・削除せず、新fixtureと診断の監査だけをappendする。
  コマンド終了後に無人監視を続けるsensor daemonは追加しない。任意threshold・任意SQL/action・外部通知先・LLM・自動修復は未実装範囲として残る。
- rollbackは同じvolume上で元のAlerting無効構成へ戻す操作であり、DBを過去へ巻き戻さない。
  backup・両container・unknown/failed claim・診断証跡を削除して成功へ見せ替えない。

## Local acceptance (2026-09-20)

実03は28.475秒でpassed。normal→firingを観測してSREを1回実行し、再取得したfiringの同一fingerprintに対する再tickは重複0、success fixture後にnormalへ戻った。
SREは3MCP read・監査4件を完了し、fixture2件と合わせてAgentHouseは554→560件。旧554件、保護42files、MLflow19runs/1347points/19artifactsを保持した。
診断の`attention-required`は過去の再起動・前回終了情報を含む結果であり、観測時の対象はreadyだった。合成fixtureの根本原因の証明や修復の実施とはしない。

- [x] 同じGrafana永続化領域・認証・network・Readerを保ち、backup/旧containerを確保してengineを有効化した。
- [x] paused ruleの専用通知抑止を確認した後、normal→firing→normalを実測した。
- [x] alert identity/run_key/cursor/claimに束縛した決定的SRE診断1回・重複0を確認した。
- [x] unknown claimによる再実行・別alertの抑止をfake testsで確認した。
- [x] 3MCP read・監査4件・cleanupと保持データを確認した。LLM・自動修復・resident sensor daemonは0。
- [x] scoped tests・実環境検証・repo/Vault同期を完了し、失敗と回復を保持した。

cold tarは8 entries/1 regular file、1,909,760 B（allocated 1,912,832 B）。検証DBを含む移行記録は6 files/3,818,893 B（allocated 3,833,856 B、移行完了時点、files0600）。
停止前のmount順差による拒否、rule/API正規化差、旧通知APIの404を保存して修正した。normal状態の親/子表現差で止まった実01/02も、fixture/診断/claim各0、旧データ保持、rule pausedを確認して原記録とactive markerを保存した。
全回帰は途中の版で95suites/3113実行（3112PASS・既存1skip）、現行API対応後は5suites/77tests PASS。全回帰を最終版で再実行したとは主張しない。
証跡は`tmp/local-experiment/activation-checks/71950c2bda334b7cbe645e7d4bca56a8/actual-proof.json`と同dirの失敗・回復・検証記録。

GitHub Issue/PR作成・公開・GitHub review待ちはlocal完了条件にしない。
[DEV-04B](../../04_Development/04B_Autonomous_Local_Development.md)に従い、許可済みlocal scopeで進めた。

## 2026-07-05 historical plan

以下は元のWP条件を保持した歴史的記録である。PR/CI/Issue・承認条件は、現在のlocal実行gateではない。
将来の外部公開や本番運用には別の範囲判断が必要であり、上のlocal技術受入で代替しない。

## Context

このWork PackageはLevel L5「OSS Fabric再現」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

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

`level:l5`, `area:ops`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L5-ACT-001: Data Activator style alerts.
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
