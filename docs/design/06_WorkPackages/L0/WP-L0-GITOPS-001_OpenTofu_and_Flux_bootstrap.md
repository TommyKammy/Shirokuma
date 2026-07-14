---
project: Shirokuma
doc_id: "WP-L0-GITOPS-001"
title: "WP-L0-GITOPS-001 OpenTofu and Flux bootstrap"
status: draft
created: 2026-07-05
updated: 2026-07-14
version: "0.4"
area: "workpackage"
tags: [shirokuma, workpackage, l0, gitops, flux]
---

# WP-L0-GITOPS-001 OpenTofu and Flux bootstrap

## Summary

OpenTofuでlocal Kubernetesの前提条件を構築し、固定したFlux v2リリースを公式bootstrap経路で導入して、Gitからdev desired stateを継続的にreconcileできるようにする。

旧Argo CD前提のWork Packageは[[07_ADR/ADR-0018_Use_Flux_v2_as_the_GitOps_reconciler|ADR-0018]]によりsupersededされた。`doc_id`と依存関係は維持する。

## Context

このWork PackageはLevel L0「Agentic GitOps土台」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

## Depends on

- `WP-L0-PLAT-001`

## Scope

- OpenTofuとFlux bootstrapの責務境界を実装する。
- `fluxcd/flux2`の承認済み安定版をversion固定する。初期local-lab版は`v2.9.2`とする。
- `source-controller`、`kustomize-controller`、`helm-controller`、`notification-controller`を`flux-system`へ導入する。
- repositoryのcluster pathにFlux自己管理manifest、`GitRepository`、root `Kustomization`を配置する。
- dev overlayとGit-reconciled smoke resourceをroot `Kustomization`から管理する。
- controller imageのdigest、署名、SBOM、provenance、ARM64 manifest、脆弱性scan evidenceを検証する。
- ADR-0019に従い、strict gateを維持しながらexact digest/CVE/package/versionへ限定した30日以内のlocal-lab例外を検証する。
- non-interactiveなbootstrap/status/teardown Make targetとbounded evidenceを整備する。

## Non-scope

- Image Automation controllersとsource-watcher
- Flux Operator
- community Flux Helm chartを標準bootstrapにすること
- 複数cluster、multi-tenant sharding、production HA
- Secret値またはDeploy Key private materialのrepository保存
- direct `kubectl apply`を正常なGitOps変更経路として扱うこと

## Deliverables

- 固定されたFlux CLI/distribution versionとcontroller image inventory
- OpenTofuによるcluster bootstrap prerequisites
- `flux bootstrap git`または`flux bootstrap github`のnon-interactive wrapper
- `clusters/local-lite/flux-system`のgenerated bootstrap manifests
- dev root `Kustomization`とsmoke manifest
- `make gitops-bootstrap`、`make gitops-status`、`make gitops-reconcile`、`make gitops-teardown`
- supply-chain verification evidenceとfailed reconciliation fixture
- 更新されたRunbook、CLI doctor contract、rollback手順

## Acceptance Criteria

- [ ] `flux version --client`がrepositoryで固定したversionと一致する。
- [ ] `flux check --pre`がbootstrap前に成功する。
- [ ] Fluxの標準4controllerが`flux-system`でAvailableになる。
- [ ] `flux check`が成功する。
- [ ] cluster entrypointの`GitRepository`が`Ready=True`で承認済みrevisionを報告する。
- [ ] rootとdevの`Kustomization`が`Ready=True`になる。
- [ ] Git経由のsmoke変更がdirect `kubectl apply`なしでclusterへ反映される。
- [ ] controller imagesの署名、SBOM、provenance、ARM64を検証し、strict High/Critical gateまたはADR-0019のlocal-lab限定High例外を評価できる。Criticalは常に拒否する。
- [ ] `shirokuma doctor`がFlux controllers、Source、Kustomizationの状態をJSON/Markdownで報告する。
- [ ] teardown後に同じ手順で再bootstrapし、同一のdesired stateへ収束できる。
- [ ] Secret値、token、Deploy Key private materialがevidence、Issue、PR、logへ含まれない。

## Suggested Labels

`level:l0`, `area:gitops`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L0-GITOPS-001: OpenTofu and Flux bootstrap.
Follow ADR-0018 and the repository AGENTS.md.
Pin the approved fluxcd/flux2 stable release; do not track main or runtime latest.
Keep OpenTofu responsible for cluster prerequisites and use the official Flux bootstrap path for self-management.
Implement GitRepository and Kustomization readiness, Git-reconciled smoke evidence, supply-chain verification, bounded diagnostics, teardown, and rollback.
Do not introduce Image Automation, Flux Operator, the community Helm chart, or direct kubectl mutation.
```

## Definition of Done

- Code、declarative manifests、tests、docs、Runbook、rollbackが同一PRに含まれる。
- Acceptance CriteriaをCIまたはbounded local evidenceで検証している。
- GitHub Issueの依存、Scope、Non-scope、Evidence、Rollbackが実装と一致する。
- `issue-lint`とrepositoryのpre-PR verificationが成功する。

## Migration note

2026-07-12以前のIssue、PR、Pawprint、fixtureにあるArgo CD blockerは履歴証跡として保持する。現在の実装契約と新しいevidenceはFlux resource modelを使用する。

## Current implementation evidence

- Flux v2.9.2の標準4controllerについて公式linux/arm64 platform digest、signed OCI index、SLSA provenance v1、upstream SPDX SBOM subjectを確定した。
- Trivy 0.72.0、DB timestamp `2026-07-13T19:09:56.237113526Z`でHighはsource=2、kustomize=0、helm=2、notification=1、Criticalは全て0だった。
- CycloneDX 1.7 SBOM、Trivy JSON、署名・provenance summaryを`security/evidence/flux-v2.9.2/`へretained evidenceとして保存した。
- ADR-0019によりsource、helm、notificationのexact findingsを2026-08-13までlocal-lab限定で承認した。strict profileは引き続き不適合を返す。
- 新規High、Critical、scanから消えたstale exception、digest/package/version mismatch、expired approvalはbootstrapを再びfail closedにする。
