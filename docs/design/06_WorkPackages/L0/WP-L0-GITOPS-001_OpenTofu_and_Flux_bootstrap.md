---
project: Shirokuma
doc_id: "WP-L0-GITOPS-001"
title: "WP-L0-GITOPS-001 OpenTofu and Flux bootstrap"
status: local-partial
created: 2026-07-05
updated: 2026-10-03
version: "0.8"
area: "workpackage"
tags: [shirokuma, workpackage, l0, gitops, flux]
---

# WP-L0-GITOPS-001 OpenTofu and Flux bootstrap

## 2026-10-03: reject ambiguous non-root Flux inventory

Dedicated Colima is stopped; no live API reads or reconciliation. The local-only read-only doctor now rejects missing kind/name/namespace, zero generation, and duplicate kind/namespace/name for **all** Flux resources, including non-root resources; previously a zero-generation Ready condition or duplicated non-root Kustomization could be reported as reconciled. Fake positive/negative tests and generic-profile compatibility test pass (`go test ./internal/cli`, `go test ./...`, `go vet ./...`). This verifies only the offline diagnostic, not runtime authenticity, Git revision provenance, strict admission, or WP acceptance. No retained data or backups changed. Roll back only the local diagnostic and its tests; preserve the stopped VM and saved evidence. Vault handoff direct sync remains pending because the original is outside this noninteractive checkout's permitted filesystem.

## 2026-10-03: fixed local diagnostic identity and port bounds

停止中の専用Colimaは起動せず、固定local doctorのloopback HTTPS endpointに数値port `1..65535`を要求し、0/範囲外/文字列は後続read前に拒否。別の独立増分として固定Flux controller4件は名前だけでなく`Deployment/flux-system`を要求する。fakeの正負例、`go test ./internal/cli`、`go test ./...`、`go vet ./...`、`make verify-local-experiment`は成功。汎用doctorの契約は変更しない。live API・remote revision・strict gate・Git reconciliationの受入は未検証。保持VM/backup/保存runの変更はなく、rollbackは局所doctor差分のみを戻す。非対話CLIからcheckout外Vault原本への直接同期は権限外のため保留。

## 2026-10-03: local doctor inventory and API endpoint fail-closed checks

同日の独立した追加点：固定Flux controller一覧とFlux source/Kustomization一覧でも`items`欠損・`metadata.continue`ありをdegradedとして拒否する。controller一覧が不完全ならsource APIを呼ばない。fakeの途中ページ/正常一覧を検証し、汎用doctorは従来契約のまま。実Fluxのlive再観測はしていない。Vault同期保留・rollback/残余は下記と同じ。

固定local profileのread-only doctorで、Lakehouseのnamespace別workload listが`items`欠損、`metadata.continue`あり、または固定kind/name/namespaceの重複を含むときは、最初のReady資源を採用してhealthyとしない。別namespaceの正常な診断は保持し、pagination tokenをsummaryへ出さない。local-scopeのloopback HTTPS API URLはport必須、path/query/fragmentを許さず、異常時は後続API readに進まない。fake応答の負例と正例、Go全package test/vet、`make verify-local-experiment`を実行。専用Colimaは停止中でlive診断なし。これはAPI真正性、Flux revisionのprovenance、SQL/保存状態、strict supply-chain gate、Git smoke/再bootstrapや本番受入を証明しない。戻す場合はdoctorの検証差分のみ戻し、保持VM/backupや既存保存結果を変更しない。Vault直接同期はこの非対話sessionのcheckout外権限がないため保留。

## 2026-10-02: fixed Flux source identity in read-only local doctor

`shirokuma doctor --profile local-experiment`は従来4 controller・Ready条件を見たが、固定root `GitRepository/flux-system/flux-system`の不在・artifact revision欠損・root `Kustomization`の別source参照でも、別のGitRepositoryがあればFluxをinformationalと報告し得た。fake応答の負例4件で修正前誤通過を再現。local profileに限り、root GitRepositoryとroot Kustomizationがそれぞれ1件、rootのsourceRefが同namespaceの固定GitRepository、GitRepositoryが現generationでReady・非削除、artifact revisionが有界の`ref@sha1:<40 lowercase hex>`形式であることを追加検査する。欠損/重複/別namespace/不正revision/別参照は`degraded`、generic doctorの旧契約は維持。root suspendedのinformational文言は「意図・source revisionのprovenance未認証」を明示し、local profile指定だけを意図の証明にしない。revisionは**形式**のみでremoteへの独立認証・このcheckoutのHEADとの一致ではない。`doctor`はread-onlyでreconcile/applyなし。

停止中の専用Colimaをprofile設定SHA記録後に一時起動し、固定contextでcompiled Go doctorをread-only実行。初回は起動後のsource-controllerと子Kustomization Ready未達で全体`degraded`、保存したまま再読した2回目は全体`healthy`・Flux `informational`（root suspended）。別API readで固定GitRepositoryは現generation Ready、artifact revision形式一致、root Kustomization sourceRef一致・suspended。取得したrevisionはprivate記録内でもhashだけ保持。専用VM・保持データ/backupは削除せず、GitHubへ書き込みなし。private記録は`tmp/local-experiment/gitops-doctor-source-checks/20261002/`。これは限定diagnosticであり、Git経由のsmoke reconcile、現checkoutのremote publication identity、strict supply-chain gate、再bootstrap/teardownの実証ではない。WP `local-partial`を維持する。

通常停止後は専用profileがStoppedへ戻りhost設定SHA不変。起動時TCP 53 forward競合、停止時k3s killallの`/var/lib/cni/` busyおよび保持Pod volume unmountの出力あり。保持PVC/backupの**内容不変は独立照合していない**。本診断を戻す場合は新しいdoctor validationだけを以前のローカルrevisionへ戻し、保存run/VM/backupに復旧目的の削除や再作成を行わない。

修正後Go CLI focused 674 testsと`go vet ./...`成功。最終全回帰`make verify-local-experiment`は121 suites/3820実行＝3812 PASS＋8 skip、90.39秒、private log SHA256 `a919c55e87b4c09117e93bf4fc3d9a4c7eb33563bce1a3ca4de1e301e252829a`。`make verify-design-context`、secret scan、diff check成功。実VMの診断実行後に追加したgenerationゼロ負例2件はfake応答・全回帰でのみ確認し、実VMを再起動していない。

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
