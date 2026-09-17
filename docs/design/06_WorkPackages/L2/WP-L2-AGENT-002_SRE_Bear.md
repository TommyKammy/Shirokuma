---
project: Shirokuma
doc_id: "WP-L2-AGENT-002"
title: "WP-L2-AGENT-002 SRE Bear"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.3"
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
