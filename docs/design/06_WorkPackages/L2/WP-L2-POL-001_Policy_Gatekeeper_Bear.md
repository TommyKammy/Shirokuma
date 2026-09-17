---
project: Shirokuma
doc_id: "WP-L2-POL-001"
title: "WP-L2-POL-001 Policy Gatekeeper Bear"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.4"
area: "workpackage"
tags: [shirokuma, workpackage, l2, policy]
---

# WP-L2-POL-001 Policy Gatekeeper Bear

## Summary

Local実験では固定candidateのrule違反・適用範囲を実native admissionと照合する。
モデルは助言だけを返し、権限付与・本番Tier/PRリスク判断は未実装とする。

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

`level:l2`, `area:policy`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L2-POL-001: Policy Gatekeeper Bear.
Read AGENTS.md, related ADRs, and this Work Package.
Use ADR-0034 local development and fixed server dry-run verification.
The model identifies observations; it cannot grant authorization or weaken policies.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-17 local bounded implementation plan

既存native admission baselineの15 server dry-run corpusを再利用し、
モデルが固定candidateのどのrule違反を観測するかを実APIの結果と照合する。
候補はneutral IDsとsafe featureだけにし、allow/denyを含むcase名やhost expected answerを
モデル入力に渡さない。Model出力はviolation/scope-limit/verification IDsのみ。
モデルにallow/approve/Tier付与やpolicy緩和を決定させない。

- [x] 15固定candidateのモデル入力がexpected答えを含まないことを検証する。
- [x] Native policyのsource/manifest identityとready状態を照合し、固定server dry-runだけを行う。
- [x] モデルのrule/limit/verification選択をhost truthと実Admissionへ照合する。
- [x] default namespace controlのallowを安全性や実作成権限へ変換しない。
- [x] ADR-0034の範囲内/範囲外をhostが固定分類し、modelやK8s成功で権限を昇格しない。
- [x] 実Qwen評価、実API比較、Pawprintと保存証跡、README/Vault更新を確認する。

実Pod等の作成、policy apply/delete、GitHub書込、外部公開、追加費用、保持データ削除、
global host変更、全Colima resetはこのentrypointでは実行しない。
Local commitや新規使い捨て実験データ再作成には既存の包括承認があり、
この実装は都度owner承認やGitHub review待ちを再導入しない。
K8s baselineのnamespace適用範囲、image digest syntaxと署名/CVE承認の違い、
server dry-runと実workload動作の違いを明示する。
PRリスクや本番Tier判定・一般的な権限判断の品質は未達として残す。

## 2026-09-17 local functional evidence

実Qwenはnative 15候補（5×3batch）と操作境界10候補（1batch）をhost truthと完全一致で分類。
入力3907/出力1171 tokens、推論合計33.125秒。全分類完了後だけ固定15server dry-runを実行し、
allow2/deny12/対象外namespace control1が期待と一致した。権限付与・境界操作・保存workloadは0。
7件のAgentHouse/Loki/保存OTelとcompleted checkpointを確認した。

実workflowは`tmp/local-experiment/policy-bear/f37433c5b92d462a8ea69f4465c334b5/`。
04:31 UTCの独立再観測で保持6PodのUID/phase/ready/restartsと16policy/binding identitiesは不変、
両namespaceにdry-run残存objectは0。事前/事後証跡は
`tmp/local-experiment/policy-bear-preflight/fa25204603de4af38a659279a90a3dd0/`。
Pending candidateをAPI前に保存し、確認後に結果を保存する。途中失敗・signal・保存障害では
未知の結果と既知/未知token使用を保持し、次の推論/操作を止めることをfakeで確認した。

8合成case×2の実Qwen評価は16/16成功、16監査readback。
入力12454/出力2768 tokens、推論81.187秒、retry0。
`tmp/local-experiment/policy-evaluation/11754e69d7ce4ef89e8e231b5ad5a629/`に全attemptを保存。
Core12＋Bear18の新規tests、既存native17 testsを含む全回帰486 tests、設計73documentsが成功。

再実行は`make local-policy-bear-eval`と`make local-policy-bear-smoke`。
[Runbook](../../../../tools/local_experiment/bears/POLICY_GATEKEEPER.md)に境界・中断証跡・終了方法を記録。
今回の成功は固定25分類と15dry-runに限る。任意policy、一般的なリスク判断、
実workload作成、署名/CVE保証、本番受入、historical GitHub gatesの完了を主張しない。
