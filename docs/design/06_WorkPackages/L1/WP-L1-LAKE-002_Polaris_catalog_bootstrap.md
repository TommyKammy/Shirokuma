---
project: Shirokuma
doc_id: "WP-L1-LAKE-002"
title: "WP-L1-LAKE-002 Polaris catalog bootstrap"
status: in-progress
created: 2026-07-05
updated: 2026-07-16
version: "0.6"
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

## GitHub Tracking

- Epic: [#24](https://github.com/TommyKammy/Shirokuma/issues/24)
- Issue: [#27](https://github.com/TommyKammy/Shirokuma/issues/27)
- PR: [#52](https://github.com/TommyKammy/Shirokuma/pull/52) (merged)
- Runtime follow-up Epic: [#60](https://github.com/TommyKammy/Shirokuma/issues/60)
- Runtime follow-up Issue: [#61](https://github.com/TommyKammy/Shirokuma/issues/61)
- GitHub depends on: `#26` (closed 2026-07-16 JST)
- Execution order: `3 of 10`
- Queue: #27 closed after the supply-chain selection checkpoint. Runtime scope
  is now executable as #61: trusted Polaris build/publication, PostgreSQL
  admission, Flux workloads, Ready evidence, and catalog smoke.

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。
