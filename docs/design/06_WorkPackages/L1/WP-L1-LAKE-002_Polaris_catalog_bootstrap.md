---
project: Shirokuma
doc_id: "WP-L1-LAKE-002"
title: "WP-L1-LAKE-002 Polaris catalog bootstrap"
status: in-progress
created: 2026-07-05
updated: 2026-07-19
version: "1.1"
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
解釈されて再度自動Closeされたため、2026-07-19にreopenしました。Issue #61を
runtime acceptanceのliveな境界とし、以下の全工程が完了するまでOpenを維持します。

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
  schema-v2 verification contractをmainへ固定済みです。
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
- 修復Draft PR #75は、`files-2.1`をGAV、filename、SHA-256が完全一致する
  verification metadata closureへ射影し、未選択probe/alternate residueをdescriptorと
  archiveから拡張子非依存で除外します。全scanにpath SHA-1、symlink/special file、
  casefold collision、file/byte capを適用し、scan/retain/exclude件数とbytesを
  descriptorへ固定します。実cacheでは5,596 scanned / 5,412 retained /
  184 excluded POMとなり、fresh Linux volume上のnetwork-none / offline /
  strict buildで245 tasksが成功しました。このlocal結果はfeasibility evidenceであり、
  protected main runのpublication evidenceではありません。
- 現在の実装範囲は`dependency_snapshot_publication_pending`です。main限定の
  3-job workflowは、ASF署名sourceからGradle verification metadataとcanonical
  snapshotを生成し、fresh network-none / offline / strict buildを証明した後に
  read-only jobでcandidate/toolchain/evidenceを再検証します。write-capable jobは
  registry tokenの明示的注入前にそのSHA-256へbytesを再結合し、run-scoped OCI
  artifactを署名・attestしてからanonymous exact-digest pullをfail-closedで
  要求します。
- GHCR初回packageはprivateのため、最初のmain runは署名・provenance生成後の
  anonymous pullで意図的に停止し得ます。そのattemptはadmitせず、ownerが公開の
  不可逆性を確認してpackageをpublic化した後にrerunします。credential fallback
  は許可しません。
- 2026-07-18の非admissionな実機監査では、5,014 files / 825,947,131 raw
  bytesのGradle seedでnetwork-none offline buildが成功しました。圧縮後も
  619,659,126 bytesのため、artifact本体をGitへ置きません。
- evidence-only follow-upでは未知のmain-run digestを自己承認しません。main
  runが生成するper-file descriptor、Gradle verification metadata、OCI manifest /
  blob digest、signature、provenance、offline-build evidenceを別のevidence-only
  PRでGitへ固定します。そのPRでdependency publisherも削除してlifecycleを
  `dependency_snapshot_review_pending`へ進めてから、Polaris Containerfileと
  image publisherを有効化します。削除前にlifecycleだけが進んだ場合も、publisher
  のread-only gateはbuild/publicationを正常skipします。
- 続く工程はdependency snapshot evidence review、main-only Polaris image
  publication、evidence-onlyのatomic Polaris/PostgreSQL admissionです。
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
- Gradle verified-closure projection repair Draft PR:
  [#75](https://github.com/TommyKammy/Shirokuma/pull/75) (review中, `Refs #61`)
- Runtime follow-up depends on: `#27` (closed prerequisite checkpoint)
- Execution order: `1 of 8`
- Queue: PR #75 verified-closure projection repairのreview/merge、新しいmain run、
  dependency evidence-only review、main-only Polaris image publication、
  atomic Polaris/PostgreSQL admission、runtime/Flux/API smoke/backup-restoreを
  順に完了するまで、Issue #61はOpen、後続#62はblockedを維持します。

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。
