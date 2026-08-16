---
project: Shirokuma
doc_id: "ADR-0019"
title: "Allow time-boxed resident image exceptions for the local lab"
status: accepted
created: 2026-07-14
updated: 2026-08-16
version: "0.3"
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
  image digest、advisory、severity、package、installed/fixed version、ADR、
  OWNER Issue/comment、承認日、失効日、risk、compensating controls、
  replacement planを記録する。
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

## Expired initial bounded approval

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

このinitial decisionは2026-08-13に失効した。後続の承認で自動更新されたとは
扱わず、歴史的な5件のHigh findingとして保持する。

## Second bounded approval

[Issue #150](https://github.com/TommyKammy/Shirokuma/issues/150)とOWNER
[final corrective OWNER comment 5290345820](https://github.com/TommyKammy/Shirokuma/issues/150#issuecomment-5290345820)
は、同じFlux v2.9.2 exact `linux/arm64` digestについて、Trivy `0.72.0`、
DB timestamp `2026-08-14T01:10:44.597550261Z`で再取得した次の完全一致findingを
2026-09-13まで`mac-studio-solo/local-lab`に限り承認する。
このfinal corrective commentは誤った旧hash bindingをsupersedeし、policy pathを
専用sectionへ移した構造整理後の最終OWNER承認済みIssue bodyの
raw SHA-256 `b125527ca8eb81f50baa90c0a07194dc8a761ae4e79fbf9e90a88bfc31c2f0b0`
をauthoritative authorization sourceとする。

| Component | Version | High | Critical | Retained scan SHA-256 |
|---|---|---:|---:|---|
| source-controller | v1.9.3 | 7 | 0 | `e30437cbd9f82ed6a6f8388d534f8e5f4aa41445b870fd542e37a8e363f62752` |
| kustomize-controller | v1.9.3 | 6 | 0 | `e818eadafd8de0bfbd3817462ba9b27539044624ea3a309288b64ad4187dcb33` |
| helm-controller | v1.6.2 | 6 | 0 | `a84640be06b89e07c30d602dec8be8194ed76371c801245a39c1ce911b38feb1` |
| notification-controller | v1.9.2 | 6 | 0 | `81ef8a0a8f23cf0d320866ba5a4d5b6327c0feee1b2a68807cb9e57ca97cdb07` |

exact 25 tupleはIssue body、OWNER commentに束縛された
`security/resident-image-exceptions.json`、および上記retained scanが
authoritativeである。advisory、severity、package、installed version、fixed
version、digest、scan hashのいずれかが変化すれば、この承認は適用しない。
例外validatorはさらにexact OWNER login/association/comment timestampと、各scanの
artifact path、実ファイルSHA-256、`CreatedAt`、Trivy version、DB timestampを
resident ledgerおよびretained JSONへ束縛する。

同じOWNER decisionはgenerated `gotk-components.yaml`の完全一致pathに限り、
Trivy `KSV-0041` 1件と`KSV-0046` 8件を
`2026-09-13T00:00:00Z`まで承認する。all-severity reportにはignoreを適用せず、
CIはignore前にfreshなcanonical-manifest scanを取得して完全一致する9 findingsと
作成時刻を検証し、同じIDの追加発生も拒否する。blocking scanだけがcanonical
`.trivyignore.yaml`を使用する。resident imageと
RBACのどちらも自動更新を禁止し、期限到来時はfail closedへ戻す。
retained config report SHA-256は
`00e87fef815ac9a99401f2a450e71c47555fd991ec6d2cfc7313e1f0dbe3bd7a`、raw
Trivy/check-bundle metadata SHA-256は
`a82d05e076fd54c9bd2e57fd1be00891a2384a3f618e9d72037bfd940a5406ea`である。
expiry instant自体は無効であり、その直前までだけを有効とする。

Flux v2.9.4はsigned index内にSLSA provenance v1とSPDXを持ち、同じDBで
High=20/Critical=0まで改善するが、4 digest、CRD/RBAC、admission binding、
deploymentとlive self-reconciliationを変更するため、このdecisionの対象外とする。

## Compensating controls

- Local Colima/k3s labだけで実行し、production dataまたはproduction credentialを
  使用しない。
- Repositoryで承認されたGitとOCI/Helm sourceだけをreconcileし、untrusted
  archiveを処理しない。
- Public ServiceまたはIngressを作成しない。
- Flux distributionまたはdigest更新時と例外更新前に再scanする。
- 新しいHighまたはCriticalを検出した時点でbootstrapを再びblockする。
- OWNER record、scan hash、fixed versionを含む完全一致bindingが欠けた場合もblockする。

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
- 期限到来は更新要求ではなくfail-closed eventであり、自動更新しない。
- L1 resident componentは自動的に例外対象にならない。各digestは別途審査し、
  必要なら同じbounded processで承認する。

## Verification

- `make verify-security`
- `make verify-gitops-image-admission`
- strict profileがFlux v2.9.2の既知Highを拒否すること
- local-lab profileがexact exceptionだけを許可すること
- Critical、新規High、stale CVE、digest/package/version mismatch、expired approvalを
  fixtureで拒否すること
- fixed-version、OWNER record、Issue body hash、retained scan hashのdriftを拒否すること
- `scripts/verify_trivyignore.py`がexact KSV ID/path/statement/expiryを検証すること

## Rollback

`security/resident-image-exceptions.json`から対象entryを削除するか期限切れにし、
Flux resourcesをsuspendまたは`make gitops-teardown`で削除する。次のclean upstream
releaseへdigestを更新してevidenceを再生成した後、不要になった例外を削除する。

## Related

- [[07_ADR/ADR-0018_Use_Flux_v2_as_the_GitOps_reconciler]]
- [[04_Development/049_Supply_Chain_Security]]
- [[06_WorkPackages/L0/WP-L0-GITOPS-001_OpenTofu_and_Flux_bootstrap]]
- [[10_Research/106_ARM64_Container_Image_Compatibility]]
- [Issue #150](https://github.com/TommyKammy/Shirokuma/issues/150)
