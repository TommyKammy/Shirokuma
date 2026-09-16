---
project: Shirokuma
doc_id: "WP-L1-DATAOPS-001"
title: "WP-L1-DATAOPS-001 Dagster and dbt bootstrap"
status: active
created: 2026-07-05
updated: 2026-09-16
version: "0.8"
area: "workpackage"
tags: [shirokuma, workpackage, l1, dataops]
---

# WP-L1-DATAOPS-001 Dagster and dbt bootstrap

## 2026-09-16: local functional verification

[ADR-0034](../../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)に基づき、
ローカルDataOps機能を実装・実機検証した。Dagster assetからdbtのstaging/martを実行し、
通常・再実行・retryの各回で2 models + 8 data testsが成功した。意図的なrow-count test失敗も
Dagsterで失敗として観測し、その後のretryが成功した。保持fixtureは前後一致、今回生成した
2 tableだけを所有コメント確認後に削除した。外部公開の履歴・承認を更新するものではない。

- Local acceptance: dbt build/test、Dagster materialization、結果の再現、失敗検出とretry、限定cleanupを確認。
- Python 3.12系 / Dagster 1.13.22 / dbt-core 1.12.5 / dbt-trino 1.10.3と推移依存をuv.lockで固定。
- 実装: repositoryの`tools/local_experiment/dataops/`。`make local-dataops-recovery-smoke`で再実行。
- [実測結果・未達事項](../061_Local_Experiment_Milestones.md)。常駐schedulerと公開admissionは範囲外。

以下の古いadmission・GitHub gate記録は外部公開経路の履歴として保持し、ローカル実装の停止条件にはしない。

## Summary

approved Trino/Polaris/Iceberg pathに対する最小dbt modelとDagster assetを作る。

## Context

このWork PackageはLevel L1「最小Lakehouse」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

## Dependencies

- Depends on: `WP-L1-QUERY-001` / GitHub Issue `#29` and the L0 level gate.
- Issue `#29` is closed, but admitted Trino/Polaris/PostgreSQL images and their
  Flux-managed workloads are not materialized in the repository. Trino query
  smoke must pass before dbt models and Dagster assets are added.

## Scope

- dbt-core/dbt-trino projectと最小staging/mart modelを追加する。
- Dagster assetでdbt build/testをorchestrateし、再実行可能にする。
- Trino/Polaris/Iceberg fixtureを用いたdeterministic local smokeを追加する。
- Credentialsをcommitせず、failure/rollback evidenceを残す。

## Non-scope

- Production scheduler、multi-tenant orchestration、GxP対応。
- OpenMetadata/OpenLineage integration（後続Work Package）。
- Direct mutationまたはCI/policy bypass。

## Deliverables

- Pinned dbt/Dagster runtime and dependency evidence.
- dbt models/tests and Dagster asset definitions.
- Deterministic end-to-end smoke plus retry/rollback runbook.

## Acceptance Criteria

- [ ] dbt buildとdbt testがTrino経由でdeterministicに成功する。
- [ ] Dagster asset materializationが必要なdbt modelを成功させる。
- [ ] dbt-core、dbt-trino、Dagster、runtime dependenciesがpinされ、
  applicable supply-chain checksを通過する。
- [ ] credentialsを露出せず、failureとretry evidenceを観測できる。
- [ ] retry、rollback、local cleanupが文書化される。
- [ ] `make verify`、`make verify-security`、`go test ./...`が成功する。

## Reproduction Evidence

- 2026-07-16: `make verify-dataops-bootstrap` は repository-owned admission
  ledgerとGitOps workloadを直接検査し、admitted Polaris/PostgreSQL/Trino
  imagesと各workloadの6項目がすべて欠けているため失敗する。
- 2026-07-16: 未materialize prerequisiteを検査するfocused targetはaggregate
  `make verify`から分離した。これによりrepository CIは成功し、focused targetは
  blocker evidenceを失わずに明示的な再確認へ使用できる。
- 2026-07-16: admitted Trino HelmReleaseのimageが`spec.valuesFrom`参照の
  ConfigMapにある場合も検出できる回帰を追加し、`make verify`、
  `make verify-security`、`go test ./...`が成功した。
- Placeholder credentialsや推測したruntime linkageは使用しない。approved
  prerequisiteがrepositoryへmaterializeされるまでdbt/Dagster dependencyを
  追加しない。

## Related docs / ADR

- `docs/design/01_Product/010_Project_Charter.md`
- `docs/design/02_Architecture/024_GitOps_Reconciliation_Model.md`
- `docs/design/03_Requirements/030_Functional_Requirements.md`
- `docs/design/04_Development/044_Issue_and_PR_Workflow.md`
- `docs/design/04_Development/046_CI_CD_Test_Strategy.md`
- `docs/design/04_Development/049_Supply_Chain_Security.md`
- `docs/design/10_Research/106_ARM64_Container_Image_Compatibility.md`

## Suggested Labels

`level:L1`, `workstream:dataops`, `type:work-package`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L1-DATAOPS-001: Dagster and dbt bootstrap.
Read AGENTS.md, related ADRs, and this Work Package.
Create a focused PR that implements only this scope.
Include tests, docs, risk notes, and rollback instructions.
Do not add unrelated dependencies or bypass policy checks.
```

## GitHub Tracking

- Epic: [#24](https://github.com/TommyKammy/Shirokuma/issues/24)
- Issue: [#30](https://github.com/TommyKammy/Shirokuma/issues/30)
- PR: [#55](https://github.com/TommyKammy/Shirokuma/pull/55) (merged)
- Runtime follow-up Epic: [#60](https://github.com/TommyKammy/Shirokuma/issues/60)
- Runtime follow-up Issue: [#64](https://github.com/TommyKammy/Shirokuma/issues/64)
- GitHub depends on: `#29`
- Execution order: `6 of 10`
- Queue: #30 closed after the prerequisite contract merged。approved
  Trino/Polaris/Iceberg runtime pathが未materializeのためruntime実装は#64で
  行い、Trino runtime Issue #63に依存する。

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。
