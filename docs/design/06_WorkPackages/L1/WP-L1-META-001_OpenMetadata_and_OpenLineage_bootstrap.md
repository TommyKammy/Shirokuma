---
project: Shirokuma
doc_id: "WP-L1-META-001"
title: "WP-L1-META-001 OpenMetadata and OpenLineage bootstrap"
status: local-verified
created: 2026-07-05
updated: 2026-09-16
version: "0.7"
area: "workpackage"
tags: [shirokuma, workpackage, l1, metadata]
---

# WP-L1-META-001 OpenMetadata and OpenLineage bootstrap

## Summary

LineageとDataset metadataを連携する

## Context

このWork PackageはLevel L1「最小Lakehouse」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

## Dependencies

- Depends on: `WP-L1-DATAOPS-001` and the L0 level gate.
- Trino, dbt, and Dagster evidence must be available as metadata/lineage sources.

## Scope

- Supply-chain gateを通るpinned linux/arm64 OpenMetadata dependenciesを選定する。
- Flux-managed local-lite profileでOpenMetadataを導入する。
- Trino metadataとdbt/Dagster OpenLineage eventsを取り込む。
- Dataset、owner、schema、lineageのdeterministic smokeとreadinessを追加する。

## Non-scope

- Production metadata governance、SSO、HA、GxP certification。
- GravitinoまたはL2+ agent automationの先行導入。
- Direct applyまたはcredential commit。

## Deliverables

- Flux-managed OpenMetadata profile and pinned image evidence.
- Trino ingestion and OpenLineage integration configuration.
- Metadata/lineage smoke, readiness, backup, rollback, and nuke documentation.

## Acceptance Criteria

- [ ] 変更がGitHub PRとして提出される。
- [ ] CIが通る。
- [ ] Policy checkが通る。
- [ ] `shirokuma doctor`または関連コマンドで状態確認できる。
- [ ] README/Obsidian noteが更新される。
- [ ] 未解決High riskがない。

## Current implementation evidence

- [PR #58](https://github.com/TommyKammy/Shirokuma/pull/58), now merged, adds the
  focused `verify-metadata-bootstrap` reproduction. It fails closed with
  `METADATA_TRINO_RUNTIME_PREREQUISITE_MISSING` because the repository has
  neither an admitted Trino image nor a Trino workload bound to that image.
- Aggregate `make verify`, `make verify-security`, and live `make gitops-status`
  pass. OpenMetadata/OpenLineage runtime implementation remains blocked until
  the authoritative Trino prerequisite is materialized.

## Related docs / ADR

- `docs/design/01_Product/010_Project_Charter.md`
- `docs/design/07_ADR/ADR-0018_Use_Flux_v2_as_the_GitOps_reconciler.md`
- `docs/design/02_Architecture/024_GitOps_Reconciliation_Model.md`
- `docs/design/03_Requirements/030_Functional_Requirements.md`
- `docs/design/03_Requirements/034_Platform_Requirements.md`
- `docs/design/04_Development/044_Issue_and_PR_Workflow.md`
- `docs/design/04_Development/049_Supply_Chain_Security.md`
- `docs/design/10_Research/106_ARM64_Container_Image_Compatibility.md`

## Suggested Labels

`level:L1`, `workstream:metadata`, `type:work-package`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L1-META-001: OpenMetadata and OpenLineage bootstrap.
Read AGENTS.md, related ADRs, and this Work Package.
Create a focused PR that implements only this scope.
Include tests, docs, risk notes, and rollback instructions.
Do not add unrelated dependencies or bypass policy checks.
```

## GitHub Tracking

- Epic: [#24](https://github.com/TommyKammy/Shirokuma/issues/24)
- Issue: [#33](https://github.com/TommyKammy/Shirokuma/issues/33)
- Runtime follow-up Epic: [#60](https://github.com/TommyKammy/Shirokuma/issues/60)
- Runtime follow-up Issue: [#67](https://github.com/TommyKammy/Shirokuma/issues/67)
- GitHub depends on: `#30`
- Execution order: `9 of 10`
- PR: [#58](https://github.com/TommyKammy/Shirokuma/pull/58) (merged)
- Queue: #33 closed after the focused prerequisite failure merged。Trino
  runtime materializationまでOpenMetadata/OpenLineage implementationは
  fail-closedで、#67はDataOps runtime Issue #64に依存する。

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-16 local functional evidence

OpenMetadata 1.12.6 native ARM64へ実fixtureとhistorical dbt modelsのschema/ownerを保存し、Dagster pipelineに紐づく2 native OpenLineage edgesをAPIで再取得した。4 START/COMPLETE eventsは実行artifactの時刻とrun IDを保持する。PostgreSQL backupのscratch DB復元で3 entitiesを確認した。8 focused tests成功。optional vector index警告は未解消。

[ADR-0034](../../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)に基づくlocal functional検証。旧PR/CI/Flux公開gateの履歴は保持し、GitHub完了や本番受入へ読み替えない。実行手順・証跡・復元・未達事項は[Local Experiment Milestones](../061_Local_Experiment_Milestones.md)を参照。
