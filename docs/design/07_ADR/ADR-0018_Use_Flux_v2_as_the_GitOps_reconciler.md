---
project: Shirokuma
doc_id: "ADR-0018"
title: "Use Flux v2 as the GitOps reconciler"
status: accepted
created: 2026-07-12
updated: 2026-07-14
version: "0.2"
area: "adr"
tags: [shirokuma, adr, gitops, flux]
---

# ADR-0018: Use Flux v2 as the GitOps reconciler

## Status

Accepted

## Context

Shirokumaは、単一のApple Silicon Mac Studio上で再現可能なAgentic GitOps Labを構築する。従来計画はArgo CDをreconcilerとしていたが、採用可能なコンテナイメージの入手条件をL0 bootstrapの外部依存にしたくない。また、CLI中心の自動実行、Git上の宣言的な自己管理、ARM64イメージ、署名、SBOM、provenanceを標準経路で扱えることが必要である。

Flux v2は公式の`fluxcd/flux2`ディストリビューションとGitOps Toolkit controllersから構成される。標準bootstrapは`GitRepository`と`Kustomization`を生成し、Flux自身もGitから継続的に管理できる。

## Decision

- GitOps reconcilerとしてArgo CDではなくFlux v2を採用する。
- Flux CLIと配布マニフェストは`fluxcd/flux2`の安定リリースを固定する。初期local-lab採用版は`v2.9.2`とし、`main`または実行時の`latest`を追跡しない。
- L0では`source-controller`、`kustomize-controller`、`helm-controller`、`notification-controller`を`flux-system` namespaceへ導入する。
- Image Automation controllersとsource-watcherはL0のnon-scopeとし、必要性をADRまたはWork Packageで承認してから追加する。
- OpenTofuはColima/k3s、namespace、bootstrap前提条件などの基盤を管理する。Fluxの導入と自己管理は原則として`flux bootstrap git`または`flux bootstrap github`で行う。
- Git上のcluster entrypointは`GitRepository`とroot `Kustomization`で表現する。Kubernetes manifestは`Kustomization`、Helm workloadは`HelmRepository`または`OCIRepository`と`HelmRelease`で表現する。
- 依存順序は`Kustomization.spec.dependsOn`、削除同期は`prune`、readinessはKubernetes conditionsの`Ready=True`で判定する。
- Git認証情報はrepositoryへ保存しない。private repositoryには最小権限のDeploy KeyまたはGitHub App/tokenを使用し、Secretの作成経路とrotationをRunbookで管理する。
- controller imageはdigestを記録し、署名、SBOM、provenance、ARM64対応、High/Critical脆弱性をbootstrap前に検証する。既知Highを伴う実験はADR-0019のlocal-lab限定・期限付き例外だけを使用でき、Criticalまたはproduction useには適用しない。
- `shirokuma doctor`とPawprintはArgo CDの`Synced/Healthy`ではなく、Flux controllers、Source、Kustomization、HelmReleaseのconditionsを報告する。

## Alternatives Considered

### Continue using Argo CD

Web UIとApplication/ApplicationSetモデルは有用だが、L0のCLI中心・単一クラスタ・自動実行では必須ではない。利用可能イメージに関する外部条件をL0 bootstrapのblockerとして残すため不採用とする。

### Install Flux using the community Helm chart

導入は容易だが、community chartはbest-effortであり公式リリースとの同期保証が弱い。Shirokumaの標準bootstrapには採用しない。

### Manage Flux entirely with OpenTofu

Flux providerによるbootstrapは可能だが、OpenTofu state、Git write、cluster reconciliationの責務が密結合になる。L0では基盤をOpenTofu、Fluxの自己管理を公式bootstrapに分離する。

### Use Flux Operator

自動upgradeやFluxInstance APIは有用だが、L0には追加controllerと運用面が増える。将来の複数clusterまたは自動upgrade要件で再評価する。

## Consequences

- Argo CD Application/ApplicationSet/AppProjectと`argocd` namespaceは設計・実装から除去する。
- Web UIを標準運用面とせず、Flux CLI、Kubernetes conditions、Pawprintを主要な観測面とする。
- Runbook、CLI schema、fixtures、GitHub Issuesの受け入れ条件をFlux resource modelへ移行する必要がある。
- Flux controllerごとのrelease cycleと互換性を、`flux2`の固定リリース単位で管理する。
- Local-lab admissionはproduction適合を意味せず、exception expiryまたはscan差分でbootstrapが再びfail closedになる。
- 既存のArgo CD実装証跡は削除せず、ADR-0018でsupersededになった履歴として明示する。

## Verification

- `flux version --client`が固定版と一致する。
- `flux check --pre`と`flux check`が成功する。
- `flux get sources git -A`で対象Sourceが`Ready=True`になる。
- `flux get kustomizations -A`でrootと依存Kustomizationが`Ready=True`になる。
- Git経由のsmoke変更がdirect `kubectl apply`なしでclusterへ反映される。
- controller imageの署名、SBOM、provenance、ARM64 manifest、脆弱性gateを検証できる。
- `strict` profileがHighを拒否し、`local-lab` profileがADR-0019のexact exceptionだけを許可する。

## Rollback

Flux導入前は既存のArgo CD設計を履歴として参照できる。Flux導入後にrollbackが必要な場合は、Git上のFlux-managed workloadをsuspendし、reproducible dataを退避してからFlux bootstrap resourcesを削除する。Argo CDへ戻す場合は新しいADRで再採用を決定し、このADRをsupersededにする。

## Related

- [[07_ADR/ADR-0001_Use_Agentic_GitOps_as_the_primary_operating_model]]
- [[02_Architecture/024_GitOps_Reconciliation_Model]]
- [[06_WorkPackages/L0/WP-L0-GITOPS-001_OpenTofu_and_Flux_bootstrap]]
- [[08_Runbooks/RB-001_Bootstrap_local_lite_lab]]
- [[08_Runbooks/RB-002_Diagnose_failed_Flux_reconciliation]]
- [[07_ADR/ADR-0019_Allow_time_boxed_resident_image_exceptions_for_local_lab]]
