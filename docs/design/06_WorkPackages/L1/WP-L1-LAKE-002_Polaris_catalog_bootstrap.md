---
project: Shirokuma
doc_id: "WP-L1-LAKE-002"
title: "WP-L1-LAKE-002 Polaris catalog bootstrap"
status: in-progress
created: 2026-07-05
updated: 2026-07-21
version: "1.19"
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

- [x] Polaris/PostgreSQL exact digests pass ARM64 and supply-chain admission.
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
- PR [#81](https://github.com/TommyKammy/Shirokuma/pull/81)は
  `e30c01935bbe02c70b82cf3d27fcb056910a3860`としてmerge済みです。続くmain
  run `29709853932`ではprepareとverifyが成功し、credential発行前のbuild-input
  artifact `polaris-1.6.0-arm64-build-input-29709853932-1`
  （artifact ID `8448793363`、148,343,785 bytes、Actions SHA-256
  `3b9892a2f26e2de33864f9054952ff80d43bf920335b29140f5ad073fa5ee8d4`）
  とcandidate artifactを生成しました。promotionは、成長するRekor logの
  inclusion proof/checkpointを含むREST response全体をretained responseと
  比較したため、`live Rekor entry differs from retained evidence`として
  fail-closedになりました。trusted tagは更新されず、final publication
  artifact、release evidence、admissionは存在しません。
- failed candidateの`runtime-smoke.log`には、in-memory smokeで自動生成された
  Polaris root credentialが1件平文で含まれていました。candidate全体を不採用とし、
  artifact ID `8448803363`を2026-07-20に削除し、runのartifact一覧から消えたことを
  確認済みです。調査用に展開したローカルcopyも削除済みです。このrunの
  candidate digestおよびevidenceは再利用しません。
- 先行するpublisher repair
  [#82](https://github.com/TommyKammy/Shirokuma/pull/82)はmerge SHA
  `706575ba3f21987033a29b6d21367981e9c54e3e`としてmainへ反映され、Rekor照合を
  `UUID`、`body`、
  `integratedTime`、`logID`、top-level `logIndex`のimmutable identityへ
  限定し、tree-localなproof `logIndex`も3入力間で束縛しました。proof indexは
  entry `logIndex`とは別座標としてtree boundsを個別に構造検証し、
  再取得ごとに変わり得る`signedEntryTimestamp`は
  cross-response identityに含めませんでした。
  runtime raw logとraw container inspectはrunner tempだけに置いてcleanupし、
  retained evidenceはsecret scan済みlog policyとhardening controlのallowlist
  projectionだけにしました。candidate retentionとpromotionの双方でclosed set、
  禁止ファイル、schema、hash、credential markerを再検証します。
- PR #82 merge後のmain run
  [29711984394](https://github.com/TommyKammy/Shirokuma/actions/runs/29711984394)
  attempt 1はprepare/verify/promoteの全jobに成功しました。reviewed source/workflow
  SHAは`706575ba3f21987033a29b6d21367981e9c54e3e`で、公開したimmutable refは
  `ghcr.io/tommykammy/shirokuma-polaris@sha256:db403e2db7afbe4e8a62261500e229f6d796a420e814564b49f3e14217fd6c9e`
  です。`ghcr.io/tommykammy/shirokuma-polaris:1.6.0-arm64`は同じdigestを指す
  non-authoritative pointerです。
- runはbuild-input artifact
  `polaris-1.6.0-arm64-build-input-29711984394-1`（ID `8449152758`、
  148,344,354 bytes、Actions SHA-256
  `41a10eb6eeb46691d28c74262b3698baf14a7d9b31b0c960e4844b541ef2b657`）、
  candidate artifact `polaris-1.6.0-arm64-candidate-29711984394-1`
  （ID `8449174814`、1,754,984 bytes、Actions SHA-256
  `73097c25794a8e58b46bad453236065cce39f38eece3c2647044d5cd910f98de`）、
  final artifact `polaris-image-publication-29711984394-1`
  （ID `8449181390`、1,764,175 bytes、Actions SHA-256
  `97c413927e024ff5687350b75ee172a5a890e5423292ce9c6942fd1663d3121e`）
  を生成しました。
- final artifactは33 files / 10,007,161 bytesで、32-entry
  `evidence.sha256`が全件一致しました。SBOM policyはHadoop/Ranger/Jetty HTTP
  component 0件、TrivyはHigh=0/Critical=0、non-root/read-only smokeはpassedです。
  raw smoke logとraw container inspectは含まず、sanitized log policyとallowlist
  projectionだけを保持します。`publication.json`は
  `state=image_evidence_review_pending`、`promoted=true`、`admitted=false`を
  記録します。
- 本evidence-only reviewはfinal 33 filesを
  `bootstrap/polaris/v1.6.0/image-evidence/`へ固定し、publisherをretireします。
  post-review stateは`atomic_admission_pending`、Polaris imageは
  `approved_for_atomic_admission`です。これは単独admissionではなく、
  resident ledger、runtime/Flux manifest、credentialsを一切許可しません。
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
- PostgreSQL evidence-only checkpointでは、exact index/arm64/attestation
  manifest、index/arm64署名、SLSA/SPDX、TrustedRoot、CycloneDX、fresh Trivyを
  18-file checksum closureへ固定しました。image scanに加えてCycloneDX入力scanが
  Wolfi 56 + Go 4の全library componentを閉じます。Cosign 3.1.1は空HOME/Docker
  configとdeny proxyの下で4 bundleをoffline再検証し、High=0/Critical=0です。
  `state=approved_for_atomic_admission`ですが`admission=blocked`を維持し、
  resident ledger、runtime/Flux manifest、credentialsは変更しません。保持scanは
  このevidence review専用です。atomic admission時には同じarm64 digestの
  exact-image/CycloneDX-input両scopeを各database age 24時間以内で再scanし、
  Wolfi 56 + Go 4の完全coverageを再確認します。
- atomic-admission checkpointは、空のDocker configurationでPolarisと
  PostgreSQLのexact referenceをanonymous preflightし、同じPostgreSQL arm64
  digestをexact-image/CycloneDX-inputの両scopeで再scanしました。各database ageは
  24時間以内、Wolfi 56 + Go 4は完全coverage、両reportは
  High=0/Critical=0です。
- CycloneDX-input scanはseverity UNKNOWNの`CVE-2026-39824`
  （`golang.org/x/sys` `v0.1.0`、fixed `0.44.0`）1件も隠さず保持します。
  High/Critical gateは通過しますが、decision receiptは`unknown=1`を記録し、
  runtime acceptanceで継続監視します。
- reviewed evidence、anonymous preflight、fresh dual-scope scan、両exact
  digestは
  `security/evidence/polaris-v1.6.0-postgresql-v18.4/`へ一体で束縛しました。
  PolarisとPostgreSQLは`security/resident-images.json`へ同時追加され、片側だけの
  resident recordはfail-closedになります。
- atomic resident-image admissionはPR #85としてmerge SHA
  `51fb24ebc83eb9b0b7f100a30bbc2761141a0553`へ反映され、main Supply-chain
  run `29736240085`とCI run `29736240089`はsuccessしました。ただし、admit済み
  Polaris digestは`:polaris-server:assemble`と
  `:polaris-server:quarkusAppPartsBuild`だけから作られたserver-only artifactです。
- Polaris 1.6.0のrelational JDBC schema、realm、root credentialを初期化するには
  別Quarkus applicationのPolaris Admin Toolが必要です。review済みdependency
  snapshotはserver taskのclosureであり、Admin Toolの直接依存
  `io.quarkus:quarkus-picocli`を含まないため、そのままoffline buildへ流用しません。
- PR #86はreview済みOCI snapshot
  `ghcr.io/tommykammy/shirokuma-polaris-gradle-dependencies@sha256:fa889d2c0a6e6dc48816d79680a366e21040be333ab6007b88e4ca4dbf6e59d6`
  をimmutable parent seedとして再検証し、Admin Tool不足分を加えた自己完結superset
  snapshotだけを発行できる限定publisher contractとして、merge SHA
  `619d52e0b1db5241867d7775cc8714a30b1a6f38`でmainへ反映済みです。
  `:polaris-admin:assemble`と`:polaris-admin:quarkusAppPartsBuild`に加えて既存
  server taskをregression buildし、fresh network-none/offline/strict buildを
  必須としました。
- one-shotはworkflow実行回数ではなくpublisher lifecycleの退役を意味します。
  各attemptは`run_id` / `run_attempt`固有のimmutable tagを使います。新規GHCR
  packageがprivate defaultで初回anonymous pullに失敗した場合、署名・provenance
  済みのexact packageをownerがPublicへ変更してevidence review前にrerunする
  ことだけを許可します。失敗attemptはadmitせず、registry credential fallbackは
  禁止します。static contract auditはlifecycle判定より前に常時実行し、Gradle
  9.6.0とJava 21の実測一致もcandidate保持前に必須とします。
- PR #86 merge後のmain run `29781460117`、attempt `1`は、Admin/server taskの
  fresh offline build、署名・provenance検証、およびanonymous exact-digest取得を
  完了し、公開OCI
  `ghcr.io/tommykammy/shirokuma-polaris-admin-gradle-dependencies@sha256:7a505defcd78c7a7b978e88cd4c72e0a5d8b69cbb57ddd311c163b09fe789d18`
  を確定しました。Actions artifact
  `polaris-admin-publication-29781460117-1`（artifact ID `8477021002`、Actions
  digest `sha256:d1d33b14467a58b93796568667ab68ad3f61a12f9f9c3af439bbd6361adee621`、
  582,463 bytes）は12 retained evidence recordsだけのfinite-retention搬送
  コピーであり、dependency archiveは含みません。701,437,153-byteの
  `polaris-gradle-dependencies-1.6.0.tar.gz`は1-day candidate artifact
  `polaris-admin-candidate-29781460117-1`（artifact ID `8476975401`）由来で、
  OCI第2 layerとして公開されました。独立したanonymous exact-digest pullで
  SHA-256 `e771fe2ec6b2d0f6940b1247a512eb5cbc78dd0f36e7be247975f2c5fa36fc4d`、
  size、gzip構造を再検証済みです。
- 現在のevidence-only review checkpointは12ファイルを
  `bootstrap/polaris/v1.6.0/admin-build-inputs-evidence/`へ保持し、schema-v2
  contractを`admin_dependency_snapshot_review_pending`、次状態を
  `admin_image_publication_pending`へ固定します。このcheckpointでsole
  write-capable publisherは退役・削除されます。Admin image publication/admission、
  runtime、Flux、credentialのdownstream gateはすべて`false`です。
- upstream Admin Toolはrelational JDBCだけでなくNoSQL/MongoDB moduleも
  build graphへ無条件に含めます。002Hはこのsurfaceを`review_required`として
  明示し、relational-only imageやruntime適合を主張しません。現在のevidence-only
  review完了後、別のAdmin image publication/admission checkpointを通過してから
  runtime activationへ進みます。
- PR #74以降の本文はIssue参照を`Refs #61`だけに限定します。否定文であっても
  closing keywordとIssue番号を組み合わせません。Issue #61は上記runtime
  acceptance chainの完了までOpenを維持します。

## GitHub Tracking

- Epic: [#24](https://github.com/TommyKammy/Shirokuma/issues/24)
- Issue: [#27](https://github.com/TommyKammy/Shirokuma/issues/27)
- PR: [#52](https://github.com/TommyKammy/Shirokuma/pull/52) (merged)
- Runtime follow-up Epic: [#60](https://github.com/TommyKammy/Shirokuma/issues/60)
- Runtime follow-up Issue: [#61](https://github.com/TommyKammy/Shirokuma/issues/61)
  (reopened again 2026-07-20; runtime acceptance完了までOpen)
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
  [#81](https://github.com/TommyKammy/Shirokuma/pull/81)
  (merged as `e30c01935bbe02c70b82cf3d27fcb056910a3860`, `Refs #61`)
- Rekor/runtime evidence publisher repair:
  [#82](https://github.com/TommyKammy/Shirokuma/pull/82)
  (merged as `706575ba3f21987033a29b6d21367981e9c54e3e`, `Refs #61`)
- Polaris image evidence-only review:
  [#83](https://github.com/TommyKammy/Shirokuma/pull/83)
  (merged as `11fca8a4ad180a8d862bc5f93aec3729fca7e5ee`, `Refs #61`)
- PostgreSQL evidence-only review:
  [#84](https://github.com/TommyKammy/Shirokuma/pull/84)
  (merged as `e075ce5a4095fd21a626f0feb9c7d37bef6cb0f6`, `Refs #61`)
- Polaris/PostgreSQL atomic admission:
  [#85](https://github.com/TommyKammy/Shirokuma/pull/85)
  (merged as `51fb24ebc83eb9b0b7f100a30bbc2761141a0553`, `Refs #61`)
- Polaris Admin build-input checkpoint:
  [#86](https://github.com/TommyKammy/Shirokuma/pull/86)
  (merged as `619d52e0b1db5241867d7775cc8714a30b1a6f38`; trusted
  dependency-superset publication contract、Admin image、resident admission、
  runtime activationはnon-scope、`Refs #61`)
- Polaris Admin build-input main publication:
  [run `29781460117`](https://github.com/TommyKammy/Shirokuma/actions/runs/29781460117)
  (attempt `1` success、public exact digestと12 retained filesを確定)
- Polaris Admin dependency evidence-only review:
  [#87](https://github.com/TommyKammy/Shirokuma/pull/87)
  (Draft、head `4e3282617b3334b6ef5bfdeedb35b6bccb11e2fd`;
  `admin_dependency_snapshot_review_pending`、publisherを退役し、次状態を
  `admin_image_publication_pending`へ固定、`Refs #61`)
- Runtime follow-up depends on: `#27` (closed prerequisite checkpoint)
- Execution order: `1 of 8`
- Queue: evidence-only review、Admin image publication/admission、
  credential-safe Flux activation、API smoke、
  backup/restoreを順に完了するまで、Issue #61はOpen、後続#62は
  dependency-blockedを維持します。

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。
