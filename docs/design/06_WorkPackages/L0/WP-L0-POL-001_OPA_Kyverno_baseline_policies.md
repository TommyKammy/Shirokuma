---
project: Shirokuma
doc_id: "WP-L0-POL-001"
title: "WP-L0-POL-001 OPA/Kyverno baseline policies"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.5"
area: "workpackage"
tags: [shirokuma, workpackage, l0, policy]
---

# WP-L0-POL-001 OPA/Kyverno baseline policies

## Summary

Kyverno ValidatingPolicyとrepository supply-chain gateで、危険なKubernetes
workload、外部公開、mutable/unapproved imageをCIで拒否する。
2026-09-17のlocal実験では、ADR-0035のnative admission bridgeを別経路で適用した。
Kyverno upstream imageのblockと厳密なimage approvalの未達は保持する。

## Context

このWork PackageはLevel L0「Agentic GitOps土台」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

## Scope

- Kyverno v1.18.2 stable ValidatingPolicyをCIでoffline検証する。
- privileged、host namespace/hostPath/hostPort、public Service/Ingress、
  resource requests/limits不足、mutable imageをfail-closedで拒否する。
- exact image approvalは`security/resident-images.json`へ委譲し、
  digest syntax checkと組み合わせる。
- exceptionへowner、reviewer、Issue、30日以内のexpiry、narrow matchを必須化する。

## Non-scope

- Level外の大規模OSS追加。
- 本番データ利用。
- GxP対応。
- 人間承認が必要な危険操作の自動化。

## Deliverables

- `policies/kyverno/baseline.yaml`
- `tests/policy/`のallow/deny fixtures
- `scripts/verify_policy_exceptions.py`
- `make verify-policy`とpinned Kyverno CI

## Acceptance Criteria

- [x] 変更がGitHub PRとして提出される。
- [x] CIが通る。
- [x] local Policy checkが通る。
- [x] `make verify-policy`で状態確認できる。
- [x] README/Obsidian noteが更新される。
- [ ] 未解決High riskがない。

## Suggested Labels

`level:l0`, `area:policy`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L0-POL-001: OPA/Kyverno baseline policies.
Read AGENTS.md, related ADRs, and this Work Package.
Use ADR-0034 local implementation, verification and local commits without Issues or PRs.
Record bounded functional evidence and preserve the historical upstream blockers.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [x] PR本文にIntent/Risk/Test/Rollbackがある。
- [x] 変更対象がIssue範囲内である。
- [x] repository/CIのSecurity/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## Historical mainline evidence (2026-07-13)

- PR [#23](https://github.com/TommyKammy/Shirokuma/pull/23) は2026-07-13に
  merge済みで、GitHub Verify / Supply chain securityは2件とも成功した。
- Local: `make verify`、`make verify-policy`、repository Trivy
  High/Critical gate passed.
- Kyverno CLI: `v1.18.2`、14 declared allow/deny expectations passed.
- Live admission: 未導入。2026-07-13時点の最新安定版Kyverno `v1.18.2`を
  Trivy `0.72.0`（DB updated 2026-07-12）でlinux/arm64 digest scanした結果、
  `kyverno` High 4 / Critical 0、`kyvernopre` High 4 / Critical 0、
  `readiness-checker` High 1 / Critical 0のためresident-image gateで拒否した。
- Cluster: Colima k3sのarm64 nodeはReadyだが、`flux-system`と`kyverno`
  namespaceは存在しない。Direct applyや例外追加は実施していない。
- Unblock: signed upstream patch releaseの全resident imageがHigh 0 /
  Critical 0となり、署名・provenance・SBOM・linux/arm64 gateを通過すること。
- GitHub: Issue [#11](https://github.com/TommyKammy/Shirokuma/issues/11)。

## 2026-09-17 local native admission bridge

[ADR-0035](../../07_ADR/ADR-0035_Use_native_admission_baseline_for_local_experiments.md)に従い、
専用k3sの`shirokuma-dev`だけに7種類のbaseline制約を適用した。既存のCELを再利用し、
Service/Ingressだけnativeのkind別型検査のため分割し、8 policies + 8 Deny bindingsとした。
他6ルールの式は変更していない。Kyverno controller、追加image、volumeは導入しない。

- [x] 適用前に既存6 Pod、3 Services、6 controller templatesを照合し、違反0を確認。
- [x] server dry-runとcurrent-generationの警告なし型検査を完了してからBindingを有効化。
- [x] 対象内allow2/deny12と対象外namespace control1の計15ケースが実APIで成功。
- [x] 拒否は期待policy名・binding名・validation messageで照合し、RBAC/Schema/CEL評価エラーを成功扱いしない。
- [x] UID/resourceVersion条件付きで専用Bindingのみ撤去・再適用し、同じ15ケースが再成功。
- [x] 既存6 PodのUID/phase/ready/restartが前後一致、5 runtime groups readyを確認。
- [ ] Kyverno controller autogen/background report、ephemeral containerのlive試験。
- [ ] strict image approval/signature/CVE受入、mainline upstream image blockerの解消。

実行は`rtk make local-policy-status local-policy-smoke`、適用は`local-policy-apply`、
解除は`local-policy-withdraw`。実装と詳細は`tools/local_experiment/policy/README.md`。
17 focused testsが成功。証跡は`tmp/local-experiment/policy/`内の
初回成功`411294d0640a43d1a59155a834469301`、撤去`4259b9e128b0448b86c20e05336c173b`、
再適用`539ef89674b746859a443f60afb51c2d`、最終15ケース`3a501f3b4d384238bf0ece97ab6db566`。
保持Pod比較は`tmp/local-experiment/policy-validation/0acf015be3674b6397524e22f17c919b/`。

最初のService/Ingress共通式は実型警告でBinding有効化前に停止した。
型別分割の際は今回作成した未稼働の定義1個だけをUID/RV照合して再作成し、
型検査の完了条件を緩めなかった。試行履歴もprivate receiptに保持している。
このbridgeはPod APIへの制約であり、controller template検査やimage署名判定と同一視しない。
上記のhistorical PR/owner gateはローカル実行の追加承認条件にしない。
