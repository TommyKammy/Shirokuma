---
project: Shirokuma
doc_id: "WP-L0-GITOPS-001"
title: "WP-L0-GITOPS-001 OpenTofu and Flux bootstrap"
status: draft
created: 2026-07-05
updated: 2026-08-14
version: "0.5"
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
- 初回Trivy 0.72.0、DB timestamp `2026-07-13T19:09:56.237113526Z`ではHighがsource=2、kustomize=0、helm=2、notification=1、Criticalは全て0だった。この5件のdecisionは2026-08-13に失効した。
- CycloneDX 1.7 SBOM、Trivy JSON、署名・provenance summaryを`security/evidence/flux-v2.9.2/`へretained evidenceとして保存した。
- Issue #150とfinal corrective OWNER comment `5290345820`は、同じ4 digestをTrivy 0.72.0、DB `2026-08-14T01:10:44.597550261Z`で再scanしたsource=7、kustomize=6、helm=6、notification=6（合計High=25、Critical=0）の完全一致tupleだけを2026-09-13までlocal-lab限定で承認した。
- 同じdecisionはgenerated manifest（SHA-256 `ed307189fd1f9e49819a50843bb6f3c9257fe6d4d8359d1950b38207c26c3854`）のexact pathにある`KSV-0041` 1件と`KSV-0046` 8件を`2026-09-13T00:00:00Z`直前まで承認する。retained config report SHA-256は`00e87fef815ac9a99401f2a450e71c47555fd991ec6d2cfc7313e1f0dbe3bd7a`、raw Trivy metadata SHA-256は`a82d05e076fd54c9bd2e57fd1be00891a2384a3f618e9d72037bfd940a5406ea`であり、all-severity scanはunfiltered、blocking scanだけがignoreを使用する。
- strict profileは引き続き不適合を返す。新規/欠落High、Critical、stale exception、digest/advisory/severity/package/installed/fixed version、scan hash、OWNER record、expiryのdriftはbootstrapをfail closedにする。自動更新は禁止する。
- Flux v2.9.4は20 Highまで改善し公式SLSA v1/SPDX証跡もあるが、digest、CRD/RBAC、deploymentとlive runtimeを変えるため別のmigration decisionとする。
