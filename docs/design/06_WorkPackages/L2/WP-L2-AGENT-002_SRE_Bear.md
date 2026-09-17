---
project: Shirokuma
doc_id: "WP-L2-AGENT-002"
title: "WP-L2-AGENT-002 SRE Bear"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.5"
area: "workpackage"
tags: [shirokuma, workpackage, l2, agentops]
---

# WP-L2-AGENT-002 SRE Bear

## Summary

K8s events/logs/metricsを診断してPR提案する

## Context

このWork PackageはLevel L2「Agent第一陣」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

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

`level:l2`, `area:agentops`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L2-AGENT-002: SRE Bear.
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

## 2026-09-16 local functional evidence

MCP doctor/Pod/Eventを使う固定ルールの読み取り診断を実装。実機で3 tool callsと診断自身の4 eventがClickHouse/Lokiで相関一致した。意図的Flux停止と過去restartを区別し、現在の停止とは断定しない。11 focused tests成功。LLM診断・自動修復・PR提案は含まない。

[ADR-0034](../../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)によるローカル実験。旧公開gateは履歴として保持する。実行手順・失敗と修正・復元・未達事項は[Local Experiment Milestones](../061_Local_Experiment_Milestones.md)を参照。

## 2026-09-17 local Qwen triage and re-observation

既存Qwenを用いたLangGraph `collect → triage → followup`が実機で成功した。
MCP doctor/Pod/Eventを取得し、モデルが過去restartのある3 Podを根拠IDで選択した。
許可されたPod再読取だけを実行し、約4.7秒の間隔ではUID・状態・restart回数に変化なし。
`attention-required`を保持し、現在障害・原因・回復とは断定しない。
4 MCP calls + 1 inference + 1 assessmentの6件をClickHouse/Loki/OTelの相関で照合した。
モデル入力656・出力83 tokens、推論4.266秒、cost NULL。checkpoint完了も確認した。

モデルの選択は最大2種の固定読み取りに限定し、既知の根拠ID、許可tool、重大Pod所見の
再観測必須、観測health保持を検証する。任意引数・shell・root-cause主張は受理しない。
Pod同名でもUIDが変われば置換として扱い、未再観測、欠測、truncatedを明記する。
6合成ケース×2回のQwen評価も12/12成功し、全12推論の監査読戻しを確認した。
正常、過去restart、重大Pod異常、Warning、doctor degraded、Event不完全を含む。
固定射影receiptを保存する最終runでも6監査eventの検証が成功した。保存観測から
根拠と差分・hashを再計算して一致。SREの39 focused testsを含む245回帰/設計testsが成功した。

### Local functional acceptance

- [x] 実LLMが観測根拠に基づく読み取り確認候補を選び、実MCPで再観測する。
- [x] 不正な根拠・許可範囲外の提案・重大Pod確認の脱落を実行前に拒否する。
- [x] UID/時刻に基づく限定比較と、usage/latency/監査相関を検証する。
- [ ] logs/metricsを含む原因診断・修復案の品質を評価する。
- [ ] 自動修復を実装・実験で検証する。

旧PR gateはローカル実行条件にしない。再実行は`rtk make local-sre-qwen-smoke`、
合成評価は`rtk make local-sre-qwen-eval`。新しい常駐サービス・weights・workload変更なし。
既存固定ルールは`rtk make local-sre-smoke`で利用でき、監査記録は保持する。

## 2026-09-17 disposable Failed Pod drill

新規Pod一個を`exit 1`でFailedにし、Qwenがcritical根拠として選択・再観測する実機試験に成功。
初回/followupの同UID Failedを照合し、モデルの出力ではなく実観測に基づいて判定した。
入力735・出力90 tokens、推論3.398秒、6監査eventが相関一致した。
作成receiptのUID/resourceVersionを条件に削除し、残留drill Pod0、既存6 PodのUID・状態・
restart前後一致を確認した。既存サービスを故障させたり修復したりする試験ではない。

14 drill testsを含む計259回帰/設計testsが成功。再実行は`rtk make local-sre-qwen-drill`。
証跡・所有確認付きcleanup・未適用のlive admission境界は[Local Experiment Milestones](../061_Local_Experiment_Milestones.md)を参照。
logs/metricsによる原因診断と既存サービスの自動修復は未達として保持する。

## 2026-09-17 five runtime groups in Qwen evidence

Qwen workflowの初期観測に`local_platform_status`を加え、doctor/Pods/Eventsと合わせ4源とした。
5群のnot-readyをcomponentごとのcritical、unknownを警告として区別し、未観測は正常扱いしない。
critical Podとcritical runtimeが同時にある場合は最大2回のreadで両方を必ず再観測する。
Platformの差分はready/statusの変化だけを述べ、instance identityや復旧・原因は断定しない。

実機4源のSREは成功し、5群readyと既存Podの過去restartを区別してPod再観測を選んだ。
Qwen入力773・出力51 tokens、3.675秒、7監査event相関を確認した。
`tmp/local-experiment/sre-llm/0c69d3ef5ebc401ca3d27bdb4bd98ea7/`の安全な保存射影から
根拠・triage・比較・hashを再計算して一致した。8合成ケース×2回の16/16評価でも、
同時Pod/runtime障害と観測不能を含む読み取り選択・16監査のreadbackが成功。
評価証跡は`tmp/local-experiment/sre-evaluation/22615db4c5344a81a261a2086437113b/`。
評価には実service停止や故障注入を含まない。SRE tests64件成功。

Native admission適用後にも新規Failed Pod drillを再実行した。
4初期観測＋Pod/Eventの2followups、8監査event相関が成功。入力854・出力146 tokens、4.930秒。
作成UID `6f55c9f5-83e9-47a9-b69d-4bedfdadbb89`だけを削除し、既存6 Pod前後一致・残留0。
証跡は`tmp/local-experiment/sre-drill/4a856109b32f408185aebe247508c1b2/`。
