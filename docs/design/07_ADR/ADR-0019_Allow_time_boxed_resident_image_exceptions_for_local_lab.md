---
project: Shirokuma
doc_id: "ADR-0019"
title: "Allow time-boxed resident image exceptions for the local lab"
status: accepted
created: 2026-07-14
updated: 2026-07-14
version: "0.1"
area: "adr"
tags: [shirokuma, adr, security, supply-chain, local-lab]
---

# ADR-0019: Allow time-boxed resident image exceptions for the local lab

## Status

Accepted

## Context

Shirokumaは、単一のMac Studio上でOSS Data Cloudの有用性を検証する実験的
プロジェクトであり、現時点ではproduction workloadを提供しない。一方、
resident imageの従来gateはHighまたはCriticalを1件でも検出すると無条件に
拒否するため、署名、provenance、SBOM、native ARM64が揃ったFlux v2.9.2も、
controller dependencyに残るHigh findingsによってbootstrapできない。

脆弱性を無視したり結果を削除したりせず、production適合と実験継続可否を
分離する必要がある。例外はreachabilityの不存在を意味せず、既知リスクを
限定された環境で一時的に受容するdecisionである。

## Decision

- Resident image verificationの既定profileは`strict`のままとし、High=0かつ
  Critical=0を要求する。
- `mac-studio-solo`の非production実験に限り、明示的な`local-lab` profileを
  使用できる。
- `local-lab`例外は`security/resident-image-exceptions.json`へ分離し、exact
  image digest、CVE、package、installed version、ADR、承認日、失効日、risk、
  compensating controls、replacement planを記録する。
- 例外期間は最大30日とする。expired、future-dated、malformed、orphaned、
  duplicateな例外はfail closedとする。
- 例外で許可できるseverityはHighだけとする。Critical、未承認の新規High、
  scanから消えたstale例外、package/version不一致はfail closedとする。
- Mutable/tag-qualified image、linux/arm64不一致、署名、transparency log、
  SLSA provenance、upstream SBOM attestation、CycloneDX image SBOM、scan bindingの
  欠落は例外対象にしない。
- `check-trivy`単体commandはstrictのままとする。例外はresident ledgerと
  evidenceを同時に検証する`check-images --profile local-lab`だけで評価する。
- Production profile、production data/credential、public Service/Ingress、
  untrusted Git/OCI/Helm sourceにはこのdecisionを適用しない。

## Initial bounded approval

Flux v2.9.2の標準4controllerについて、2026-07-14に取得したTrivy 0.72.0
scanと`2026-07-13T19:09:56.237113526Z`のDBを基準に、次を2026-08-13まで
承認する。

| Component | Version | High exceptions | Critical |
|---|---|---|---:|
| source-controller | v1.9.3 | CVE-2026-49478, CVE-2026-50163 | 0 |
| kustomize-controller | v1.9.3 | none | 0 |
| helm-controller | v1.6.2 | CVE-2026-39822, CVE-2026-50163 | 0 |
| notification-controller | v1.9.2 | CVE-2026-39822 | 0 |

承認のauthoritative recordは例外台帳とretained scanであり、この表だけでは
admissionにならない。

## Compensating controls

- Local Colima/k3s labだけで実行し、production dataまたはproduction credentialを
  使用しない。
- Repositoryで承認されたGitとOCI/Helm sourceだけをreconcileし、untrusted
  archiveを処理しない。
- Public ServiceまたはIngressを作成しない。
- Flux distributionまたはdigest更新時と例外更新前に再scanする。
- 新しいHighまたはCriticalを検出した時点でbootstrapを再びblockする。

## Alternatives considered

### Disable the High/Critical gate

未知の追加findingやCriticalまで通過し、監査可能性を失うため不採用とする。

### Mark current findings as false positives

Feature-level reachabilityを証明していないため不採用とする。例外はfalse positive
判定ではなく、明示的なrisk acceptanceとして扱う。

### Build custom hardened Flux images immediately

Upstream patchを待たずに進められるが、独自build、署名、release、更新責任が
増える。local-labの初期検証ではtime-boxed exceptionを先に採用し、upstream
更新が長期間得られない場合に別ADRで再評価する。

## Consequences

- Shirokumaは既知High findingsを保持したままFlux bootstrapの実用性を検証できる。
- `strict` profileは従来どおり不適合を返すため、この承認をproduction適合と
  誤認できない。
- 例外更新は定期作業になり、scanの変化は明示的なreviewを要求する。
- L1 resident componentは自動的に例外対象にならない。各digestは別途審査し、
  必要なら同じbounded processで承認する。

## Verification

- `make verify-security`
- `make verify-gitops-image-admission`
- strict profileがFlux v2.9.2の既知Highを拒否すること
- local-lab profileがexact exceptionだけを許可すること
- Critical、新規High、stale CVE、digest/package/version mismatch、expired approvalを
  fixtureで拒否すること

## Rollback

`security/resident-image-exceptions.json`から対象entryを削除するか期限切れにし、
Flux resourcesをsuspendまたは`make gitops-teardown`で削除する。次のclean upstream
releaseへdigestを更新してevidenceを再生成した後、不要になった例外を削除する。

## Related

- [[07_ADR/ADR-0018_Use_Flux_v2_as_the_GitOps_reconciler]]
- [[04_Development/049_Supply_Chain_Security]]
- [[06_WorkPackages/L0/WP-L0-GITOPS-001_OpenTofu_and_Flux_bootstrap]]
- [[10_Research/106_ARM64_Container_Image_Compatibility]]
