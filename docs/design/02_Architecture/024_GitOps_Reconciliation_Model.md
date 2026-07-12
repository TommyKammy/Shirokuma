---
project: Shirokuma
doc_id: "ARCH-024"
title: "GitOps Reconciliation Model"
status: accepted
created: 2026-07-05
updated: 2026-07-12
version: "0.3"
area: "architecture"
tags: [shirokuma, architecture, gitops, flux]
---

# GitOps Reconciliation Model

## Everything is a Reconciliation Loop

```text
GitHub Issue
  -> branch / Pull Request
  -> CI / Policy / human review
  -> merge to the protected branch
  -> source-controller fetches the approved revision
  -> kustomize-controller or helm-controller reconciles desired state
  -> Kubernetes controllers converge runtime state
  -> shirokuma doctor and Pawprint record conditions and evidence
```

Reconcilers:

- OpenTofu: Colima/k3sとFlux bootstrap前提条件
- Flux source-controller: Git、OCI、Helm source artifact
- Flux kustomize-controller: manifest build、apply、health check、prune
- Flux helm-controller: HelmRelease lifecycle
- Flux notification-controller: inbound receiverとoutbound event
- Kubernetes Operators: workload固有resource
- dbt/Dagster: data transformationとworkflow state
- Shirokuma Bear controllers: Shirokuma CRDとagent workflow

Fluxの採用判断とversion policyは[[07_ADR/ADR-0018_Use_Flux_v2_as_the_GitOps_reconciler|ADR-0018]]に従う。

## Bootstrap boundary

- OpenTofuはcluster、network、storage、namespaceなどの基盤を作成する。
- `flux bootstrap git`または`flux bootstrap github`が`flux-system`へcontrollersを導入し、Gitへ自己管理manifestとsync resourcesをcommitする。
- 実行時の`latest`は使わず、`fluxcd/flux2`の承認済み安定版を固定する。
- bootstrap後のFlux upgrade、controller configuration、workload変更はGit経由で行う。
- private repositoryのDeploy Keyまたはtokenは最小権限とし、Gitへ平文保存しない。

## Resource model

| Concern | Flux resource | Shirokuma rule |
|---|---|---|
| Git revision acquisition | `GitRepository` | protected branchとcluster pathを固定する |
| OCI artifact acquisition | `OCIRepository` | digestまたは承認済みsemver範囲を使う |
| Kubernetes manifests | `Kustomization` | `prune: true`、bounded timeout、healthChecksを明示する |
| Dependency ordering | `Kustomization.spec.dependsOn` | platform → policy → workloadの順序を宣言する |
| Helm workloads | `HelmRepository`/`OCIRepository` + `HelmRelease` | chart versionとimage digestを固定する |
| Events and alerts | `Receiver`/`Alert`/`Provider` | secretをlogまたはPawprintへ含めない |

L0のcluster entrypointは`flux-system` namespaceの`GitRepository`とroot `Kustomization`である。Argo CDのApplication/ApplicationSet/AppProjectモデルは使用しない。

## Health and evidence contract

- Source、Kustomization、HelmReleaseはKubernetes conditionsの`Ready=True`を正常とする。
- `Ready=False`または`Unknown`ではreason、message、observedGeneration、lastTransitionTime、revisionをbounded evidenceとして取得する。
- controller Deployment readinessとFlux CRのreadinessを別々に報告する。
- `shirokuma doctor`は`flux get sources all -A`、`flux get kustomizations -A`、必要な`flux get helmreleases -A`相当の情報を機械可読形式へ正規化する。
- Git revision、resource identity、condition reasonをPawprintへ記録する。Secret値と無制限logは記録しない。

## Change and rollback flow

1. IssueにIntent、Scope、Acceptance Criteria、Rollback、Evidenceを記載する。
2. Agentはrepository上のdesired stateだけを変更する。
3. CI、Policy、reviewを通過したPRだけをmergeする。
4. Fluxが承認済みrevisionを取得し、依存順序に従ってreconcileする。
5. `Ready=True`とsmoke evidenceを確認する。
6. 失敗時は原因を修正するPR、またはGit revert PRを作成し、対象resourceを再reconcileする。

緊急調査で`flux reconcile`を使用してもdesired stateは変更しない。`flux suspend`は障害の拡大防止またはbounded investigationに限り、Issue/Pawprintへ理由と再開条件を記録する。

## 禁止されるパターン

- Agentが直接`kubectl apply`、`kubectl edit`、Helm install/upgradeでdesired stateを変更する。
- `flux-system`内のgenerated manifestsをcluster上だけで編集する。
- Flux resourceをGit変更なしで恒久的にpatchする。
- `main`、floating tag、実行時の`latest`からcontrollerまたはworkload imageを導入する。
- Secret、Deploy Key、token、無制限controller logをIssue、PR、Pawprintへ保存する。
- `prune`、timeout、health assessment、dependencyを暗黙値のまま運用する。

## 望ましいパターン

- すべての変更をGitHub IssueとPRへ紐付ける。
- SourceとKustomizationを小さな責務単位へ分割し、`dependsOn`で順序を明示する。
- controller、chart、imageをversionまたはdigestで固定し、署名、SBOM、provenanceを検証する。
- Flux conditionsとbounded logsを`shirokuma doctor`とPawprintへ集約する。
- Git revertと再reconciliationで再現可能にrollbackする。

## Related

- [[07_ADR/ADR-0001_Use_Agentic_GitOps_as_the_primary_operating_model]]
- [[07_ADR/ADR-0018_Use_Flux_v2_as_the_GitOps_reconciler]]
- [[06_WorkPackages/L0/WP-L0-GITOPS-001_OpenTofu_and_Flux_bootstrap]]
- [[08_Runbooks/RB-001_Bootstrap_local_lite_lab]]
- [[08_Runbooks/RB-002_Diagnose_failed_Flux_reconciliation]]
