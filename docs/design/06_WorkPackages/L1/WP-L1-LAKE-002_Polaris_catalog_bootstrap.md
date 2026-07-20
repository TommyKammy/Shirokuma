---
project: Shirokuma
doc_id: "WP-L1-LAKE-002"
title: "WP-L1-LAKE-002 Polaris catalog bootstrap"
status: in-progress
created: 2026-07-05
updated: 2026-07-20
version: "1.12"
area: "workpackage"
tags: [shirokuma, workpackage, l1, lakehouse]
---

# WP-L1-LAKE-002 Polaris catalog bootstrap

## Summary

PolarisとPostgreSQLを起動し、Iceberg catalogを構成する。

## Context

このWork PackageはLevel L1「最小Lakehouse」の一部です。Issue #26のSeaweedFS
object-storage dependencyは完了済みです。最初のcheckpointでは未認証upstream
Polaris imageをfail-closedで拒否し、source-buildとsigned PostgreSQL candidateの
admission境界をADR-0021へ固定します。

Issue #61はPR #69のconditional close文により実装途中で自動Closeされたため、
2026-07-18にreopenしました。PR #73のmerge時にも否定文中のclosing keywordが
解釈されて再度自動Closeされたため、2026-07-19にreopenしました。PR #80の
merge時も本文中の否定文がGitHubのIssue自動終了構文として解釈され、一時Close
されました。本文をOpen-safeな表現へ修正して2026-07-20にreopen済みです。
Issue #61をruntime acceptanceのliveな境界とし、以下の全工程が完了するまで
Openを維持します。

## Dependencies

- Depends on: `WP-L1-LAKE-001` and the L0 level gate; both are satisfied.
- Object storage and its S3 credential boundary must remain Ready before catalog
  bootstrap begins.

## Scope

- Supply-chain gateを通るpinned linux/arm64 Polaris/PostgreSQL imagesを選定する。
- FluxのKustomizationでPolarisとmetadata storeを導入する。
- SeaweedFS S3 endpointを参照するIceberg REST catalogを設定する。
- credentialsをGitへ保存せず、readinessとcatalog API smokeを検証する。

## Non-scope

- Iceberg table data load、Trino、production HA、GxP対応。
- SeaweedFS built-in catalogへのmainline切替。
- Direct applyまたは未承認imageの利用。

## Deliverables

- Flux-managed Polaris/PostgreSQL resources and pinned image evidence.
- Catalog bootstrap configuration and deterministic API smoke.
- Credential, backup/restore, rollback, and nuke/rebuild documentation.

## Acceptance Criteria

- [ ] Polaris/PostgreSQL exact digests pass ARM64 and supply-chain admission.
- [ ] Flux reports the catalog Kustomization and both workloads Ready=True.
- [ ] Catalog create/list/read smoke passes against the approved S3 endpoint.
- [ ] Credentials remain outside Git and policy checks pass.
- [ ] Backup/restore, rollback, teardown, and metadata-storage host SSD impact are
  verified and documented.
- [ ] CI and required human review pass on the focused PR chain.

## Related docs / ADR

- `docs/design/01_Product/010_Project_Charter.md`
- `docs/design/06_WorkPackages/L1/WP-L1-LAKE-001_Object_storage_profile.md`
- `docs/design/07_ADR/ADR-0018_Use_Flux_v2_as_the_GitOps_reconciler.md`
- `docs/design/07_ADR/ADR-0020_Adopt_SeaweedFS_4_39_source_for_arm64_build.md`
- `docs/design/07_ADR/ADR-0021_Adopt_Polaris_1_6_0_source_build_and_Chainguard_PostgreSQL.md`
- `docs/design/02_Architecture/024_GitOps_Reconciliation_Model.md`
- `docs/design/03_Requirements/034_Platform_Requirements.md`
- `docs/design/04_Development/044_Issue_and_PR_Workflow.md`
- `docs/design/04_Development/049_Supply_Chain_Security.md`
- `docs/design/10_Research/106_ARM64_Container_Image_Compatibility.md`

## Current implementation phase

- PR #71でruntime-disabledなstatic source/candidate contractをmainへ固定済みです。
- Runtime phase-2 PR #72で、main限定dependency snapshot publisherと
  当時のschema-v2 verification contractをmainへ固定済みです。この履歴上の
  contractはPR #78のschema-v3 review-pending contractへ更新されています。
- PR #72 merge後のmain run `29670937622`は、attempt 1がASF downloadの一時的な
  `curl (28)`で停止しました。attempt 2はarchive SHA-512、signature SHA-256、
  固定ASF signing fingerprintの検証を通過した後、公式archiveのin-root symlink
  `apache-polaris-1.6.0/docs`を旧validatorが一律拒否して停止しました。Gradle
  dependency resolution、OCI publication、admissionは実行されていません。
- 修復PR #73はvalidatorを契約固定された専用scriptへ分離し、公式archiveの
  4,411 members / 8 relative symlinksを検証します。root外target、strip後escape、
  missing target、cycle、symlink配下member、hardlink/special file、malformed PAX、
  resource cap超過は引き続きfail-closedです。online/offline extractionは個別に
  fresh directoryとowner/permission非復元へ拘束し、merge SHA `cf2f470`として
  mainへ反映済みです。
- PR #73 merge後のmain run `29674520988`はASF source認証とGradle dependency
  resolutionを通過後、canonical snapshot packagingで停止しました。Gradle 9.6が
  SHA-1 `0235ba...`の先頭zeroを除いた正規cache identity `235ba...`を使用する一方、
  旧packagerは40–64桁を要求したため39桁のaopalliance entryを誤拒否しました。
  offline build、candidate retention、verification、GHCR publicationはすべてskipされ、
  Actions artifactと公開packageは生成されていません。
- 修復PR #74は、同じsafe file streamからSHA-256と非security用途SHA-1を
  算出し、全leading zeroを除いたGradle cache identityをartifact bytesへ結合します。
  createとarchive verifyの双方で結合し、trustはstrict verification metadataの
  SHA-256へ維持します。38/39/40桁の正規identityを許可し、padded、arbitrary、
  uppercase、nonhex、41–64桁、descriptor/archive再結合をfail-closedで拒否し、
  merge SHA `b752a90`としてmainへ反映済みです。
- PR #74 merge後のmain run `29681024673`はASF source認証、Gradle dependency
  resolution、strict verification metadata生成を通過後、canonical snapshot
  packagingで停止しました。Gradle 9.6は`java-jwt-4.5.2.pom`をGMM redirectの
  repository probeとしてcacheへ残しますが、最終metadata sourceには`.module`を
  選ぶため、verification metadataは`.module`と`.jar`のみを正しく記録します。
  旧packagerは`files-2.1`のsuperset全体とverification closureを同一視していました。
  offline build、candidate retention、verify、publishはskipされ、Actions artifactと
  公開packageは生成されていません。
- 修復PR #75は、`files-2.1`をGAV、filename、SHA-256が完全一致する
  verification metadata closureへ射影し、未選択probe/alternate residueをdescriptorと
  archiveから拡張子非依存で除外します。全scanにpath SHA-1、symlink/special file、
  casefold collision、file/byte capを適用し、scan/retain/exclude件数とbytesを
  descriptorへ固定します。実cacheでは5,596 scanned / 5,412 retained /
  184 excluded POMとなり、fresh Linux volume上のnetwork-none / offline /
  strict buildで245 tasksが成功し、merge SHA `4987427`としてmainへ反映済みです。
- PR #75 merge後のmain run `29684553313`は、canonical snapshot packaging、
  fresh network-none strict offline build、read-only candidate verificationまで
  成功しました。Actions artifact
  `polaris-gradle-candidate-29684553313-1`（704,377,583 bytes）のみ保持されています。
  publish jobはORASへabsolute workspace pathを渡したため、registry upload前に
  `Error: absolute file path detected`で停止しました。manifest binding、signature、
  provenance、anonymous retrieval、review-pending evidenceはskipされ、GHCR OCI
  artifact/packageとpublication evidenceは存在しません。candidate-only artifactは
  admission evidenceではありません。
- 修復PR #76は、bounded candidate root内のsubshellでORASを実行し、
  descriptor、archive、export manifestをcanonical relative pathとして渡します。
  absolute path、path traversal、第二push、`--disable-path-validation`をsemantic
  workflow auditでfail-closedに拒否し、merge SHA `c8e488b`としてmainへ
  反映済みです。
- PR #76 merge後のmain run `29686419753`はresolve/verify、canonical packaging、
  fresh network-none strict offline build、candidate retention、OCI publish、raw
  manifest binding、keyless signまで成功しました。公開されたimmutable refは
  `ghcr.io/tommykammy/shirokuma-polaris-gradle-dependencies@sha256:f963d81dc13f543ca9b543fd9fa8cf57f20c2ec1774f31a90baf786791abec62`
  です。続くregistry-backed `cosign verify`が、pinned Cosign v3.1.1では
  非対応の`--bundle`を受け取り`unknown flag: --bundle`で停止しました。SLSA
  provenance、workflow内anonymous retrieval、provenance verification、
  publication evidence生成はskipされています。Actions candidate
  `polaris-gradle-candidate-29686419753-1`（704,377,952 bytes）と公開OCI/signatureは
  存在しますが、いずれもadmission evidenceではありません。
- 修復PR #77は`cosign sign --bundle`によるbundle生成・保持を維持し、
  exact digest/identity/issuerを検証するregistry `cosign verify`からだけ
  `--bundle`を除き、merge SHA
  `4692bab4282dfde2c8d4082e6d706dee9ce79324`としてmainへ反映済みです。
  semantic auditはbundle再混入、sign側bundle欠落、identity/issuer/reference
  drift、bundle/verification evidence保持漏れをfail-closedで拒否します。
- PR #77 merge後のmain run `29689013375` attempt 1は、source認証、Gradle
  dependency resolution、canonical packaging、fresh network-none strict
  offline build、read-only verification、OCI publication、raw manifest binding、
  keyless signature、SLSA provenance、anonymous exact-digest retrieval、
  provenance verification、review-pending publication recordの保持まで
  3 jobすべて成功しました。公開されたimmutable refは
  `ghcr.io/tommykammy/shirokuma-polaris-gradle-dependencies@sha256:fa889d2c0a6e6dc48816d79680a366e21040be333ab6007b88e4ca4dbf6e59d6`
  です。
- retained publication artifact
  `polaris-gradle-publication-29689013375-1`（579,565 bytes、
  `sha256:d2618dfdfbce2b645adcab392f6509c05f5b74263f3815f8cce2e2b4b4f89345`）
  のexact 9 filesをdependency evidence-only reviewへ固定しました。
  `publication.json`は`admitted=false`、`anonymous_pull=true`、
  `state=dependency_snapshot_review_pending`を記録しており、Polaris imageまたは
  runtimeをまだ認可しません。
- 1日保持のcandidate artifact
  `polaris-gradle-candidate-29689013375-1`（704,377,716 bytes、
  `sha256:e47e6e1ec307adb09fac884ce230786f55803c3ff47ea6be5625790b80a4bf67`）
  は期限前に取得し、ZIP inventory/CRC、publicationとの共有4 files、
  701,323,251-byte archive
  `sha256:18933bfb895c267302f1ee1c80cfb9712eac736ffcefade48dac53f79e8e3bc0`
  を照合済みです。既存packagerによる5,412 filesのcanonical snapshot、
  retained Cosign/SLSA bundle、匿名registry manifestと両layerも独立再検証に
  成功しました。両Sigstore検証はworkflow repository/ref/triggerに加え、
  publisher workflow SHA `4692bab4282dfde2c8d4082e6d706dee9ce79324`を固定します。
- Dependency evidence-only PR #78はmerge SHA
  `b12593f27ae4e6ec8b64865f9b6b0bbf114ec654`としてmainへ反映済みです。
  schema-v4 contractはdependency snapshotを`approved_for_image_build`へ進めますが
  `admitted=false`を維持し、sole write-capable dependency publisherは退役済みです。
- Image publication policy PR #79はmerge SHA
  `33f2fd1e6613bc2a979aa20b42d3e6e39c9e801f`としてmainへ反映済みです。
- image-publication policyは、Corretto 21.0.11のexact linux/arm64 manifest、
  `10000:10001`の非root Containerfile、main-only 3-job publisher、ならびに
  source認証後だけ適用するbounded downstream overlayを固定します。初期UBI
  candidateはHigh=21、upstreamそのままのruntimeはHadoop/Ranger経由High=6のため
  不採用です。overlayはShirokumaで未使用のHadoopFileIO、Hadoop federation、
  Ranger authorizationだけを除外し、例外を使わずlocal fresh buildで
  High=0/Critical=0、Java 21、read-only readinessを確認済みです。
- publication workflowはPRから実行せず、merge後のmainだけがrun-scoped
  quarantineへ発行できます。ASF source、review済みdependency OCI、patch
  preimage/postimage、closed context、network-none strict offline build、
  exact-digest config/runtime、SBOM、Trivy、Cosign、SLSA、anonymous retrievalを
  credential境界込みで検証します。PR #79にはimage evidence、resident ledger、
  PostgreSQL admission、runtime/Flux manifestを含めません。
- PR #79 merge後の最初のmain Polaris run `29705250136`は、prepare jobの
  retained dependency trust auditが`cosign`未導入をfail-closedで検出して
  終了しました。source build、quarantine push、image signing、promotionは
  いずれも開始されず、新しいimage evidenceも生成されていません。
- Prepare Cosign bootstrap修復PR [#80](https://github.com/TommyKammy/Shirokuma/pull/80)
  はmerge SHA `7baa1388637b1b727a70d342d863ef8cf92bd83d`としてmainへ反映済みです。
  lifecycleが`image_publication_pending`の場合だけ、
  full commit SHA固定の`sigstore/cosign-installer` v4.1.2でCosign v3.1.1を
  prepare trust audit前に導入し、導入位置、version、lifecycle guardを
  semantic verifierとregression testで固定し、read-only/no-registry-credential
  境界を維持します。
- PR #80 merge後のmain Polaris run `29706048425`はprepareを完了し、closed
  context artifact `polaris-1.6.0-arm64-build-input-29706048425-1`
  （artifact ID `8447972100`、148,344,674 bytes、SHA-256
  `8c827fca5df448b6cfdc45ea976325368a3afcb2a225f05ab5a948dd6592673c`）
  を生成しました。続くverify job最初のauditはjob-localな`cosign`未導入を
  fail-closedで検出しました。container image build、GHCR push、image digest、
  signing、promotionは実行されていません。
- 修復PR [#81](https://github.com/TommyKammy/Shirokuma/pull/81)は、
  prepare/verify/promoteの各jobを
  Cosign非依存のstatic publication bootstrap audit、full commit SHA固定の
  Cosign installer、完全なcryptographic auditの順へ固定します。各jobに
  installerが厳密に1個だけ存在し、write-capableなartifact取得やcredential発行が
  full audit後に限られることをsemantic verifierとhash-rebinding regressionで
  強制します。
- 独立した契約監査により、従来のcandidate/final artifactは
  `registry-signature-bundles.jsonl`、`rekor-entry.json`、
  `slsa-bundles.jsonl`、`sbom-attestation-bundle.json`、
  `trivy-attestation-bundle.json`を保持しておらず、後続のevidence-only reviewで
  SBOM/TrivyをOIDC identityへ独立に再結合できないことが判明しました。
  修復PR [#81](https://github.com/TommyKammy/Shirokuma/pull/81)では5証拠を
  closed required setへ追加し、registry/Rekorの
  exact照合、current-run SLSA bundle、SBOM/Trivy predicateの暗号検証を行い、
  promotion credentialの発行前とlive registry照合時に再検証します。
- one-shot publication時はGHCR packageのprivate defaultによりanonymous pullで
  fail-closedになり得ました。今回のpackageはpackage pageと空registry config
  によるmanifest/config/two-layer取得およびsignature verifyでPublicと確認済み
  で、visibility変更は不要でした。dependency publisherは現在退役・削除済みで
  あり、再実行しません。将来anonymous retrievalが失われた場合はimage
  publicationをblockし、新しい明示的なlifecycle/contract reviewを要求します。
  credential fallbackは許可しません。
- 2026-07-18の非admissionな実機監査では、5,014 files / 825,947,131 raw
  bytesのGradle seedでnetwork-none offline buildが成功しました。圧縮後も
  619,659,126 bytesのため、artifact本体をGitへ置きません。
- 続く工程は修復PR [#81](https://github.com/TommyKammy/Shirokuma/pull/81)の
  review/merge、新しいmain-only Polaris image
  publication、image evidence review、atomic Polaris/PostgreSQL admissionです。
- admission後もcredentials、runtime/Flux Ready、catalog API smoke、
  backup/restore acceptanceが未完了です。
- PR #74以降の本文はIssue参照を`Refs #61`だけに限定します。否定文であっても
  closing keywordとIssue番号を組み合わせません。Issue #61は上記runtime
  acceptance chainの完了までOpenを維持します。

## GitHub Tracking

- Epic: [#24](https://github.com/TommyKammy/Shirokuma/issues/24)
- Issue: [#27](https://github.com/TommyKammy/Shirokuma/issues/27)
- PR: [#52](https://github.com/TommyKammy/Shirokuma/pull/52) (merged)
- Runtime follow-up Epic: [#60](https://github.com/TommyKammy/Shirokuma/issues/60)
- Runtime follow-up Issue: [#61](https://github.com/TommyKammy/Shirokuma/issues/61)
  (reopened again 2026-07-19; runtime acceptance完了までOpen)
- Runtime phase-1 Draft PR:
  [#71](https://github.com/TommyKammy/Shirokuma/pull/71) (merged, `Refs #61`)
- Runtime phase-2 PR:
  [#72](https://github.com/TommyKammy/Shirokuma/pull/72) (merged, `Refs #61`)
- Source archive validator repair PR:
  [#73](https://github.com/TommyKammy/Shirokuma/pull/73) (merged, `Refs #61`)
- Gradle cache identity repair PR:
  [#74](https://github.com/TommyKammy/Shirokuma/pull/74) (merged, `Refs #61`)
- Gradle verified-closure projection repair PR:
  [#75](https://github.com/TommyKammy/Shirokuma/pull/75)
  (merged, `Refs #61`)
- ORAS relative-layer path repair PR:
  [#76](https://github.com/TommyKammy/Shirokuma/pull/76)
  (merged, `Refs #61`)
- Cosign registry verification repair PR:
  [#77](https://github.com/TommyKammy/Shirokuma/pull/77)
  (merged, `Refs #61`)
- Dependency evidence-only PR:
  [#78](https://github.com/TommyKammy/Shirokuma/pull/78)
  (merged, `Refs #61`)
- Image publication policy PR:
  [#79](https://github.com/TommyKammy/Shirokuma/pull/79)
  (merged as `33f2fd1e6613bc2a979aa20b42d3e6e39c9e801f`, `Refs #61`)
- Prepare Cosign bootstrap repair PR:
  [#80](https://github.com/TommyKammy/Shirokuma/pull/80)
  (merged as `7baa1388637b1b727a70d342d863ef8cf92bd83d`, `Refs #61`)
- All-job Cosign bootstrap repair PR:
  [#81](https://github.com/TommyKammy/Shirokuma/pull/81) (`Refs #61`)
- Runtime follow-up depends on: `#27` (closed prerequisite checkpoint)
- Execution order: `1 of 8`
- Queue: all-job Cosign bootstrap repair review/merge、新しいmain-only Polaris
  image publication、image evidence-only review、atomic Polaris/PostgreSQL admission、
  runtime/Flux/API smoke/backup-restoreを順に完了するまで、Issue #61はOpen、
  後続#62はdependency-blockedを維持します。

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。
