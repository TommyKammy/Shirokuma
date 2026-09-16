---
project: Shirokuma
doc_id: "WP-L1-BENCH-001"
title: "WP-L1-BENCH-001 TPC-H benchmark smoke on single Mac"
status: local-verified
created: 2026-07-05
updated: 2026-09-16
version: "0.6"
area: "workpackage"
tags: [shirokuma, workpackage, l1, benchmark]
---

# WP-L1-BENCH-001 TPC-H benchmark smoke on single Mac

## Summary

`mac-studio-solo`上でTrinoからIcebergへTPC-H 10GB smokeを実行し、L1 query
pathの再現性とharness correctnessを確認する。

## Context

単一ホストでload generatorと測定対象を同居させるため、結果は相対比較に限定し、
production capacityの主張には使用しない。

| Field | Value |
|---|---|
| WP ID | WP-L1-BENCH-001 |
| Level | L1 |
| Workstream | Benchmark |
| Depends-on | WP-L1-LAKE-003, WP-L1-QUERY-001 |

## Dependencies

- Depends on: `WP-L1-LAKE-003`, `WP-L1-QUERY-001`, and the L0 level gate.

## Scope

Run TPC-H smoke tests through Trino on Iceberg in the `mac-studio-solo` topology.

## Non-scope

- Production capacity, SLA, or multi-node benchmark claims.
- TPC-DS or vendor-comparison headline benchmarks.
- Mandatory 100GB execution.

## Deliverables

- Reproducible TPC-H 10GB generation and query harness.
- Machine-readable pass/fail summary and retained environment profile.
- Relative-performance report with co-location limitations.
- Cleanup and disk-space recovery procedure.

## Acceptance Criteria

- TPC-H 10GB completes.
- Optional 100GB run is documented separately.
- Report includes Colima CPU/memory/disk profile.
- Report explicitly states that load generator and measured system are co-located and results are relative comparisons, not production capacity claims.

## Related docs / ADR

- `docs/design/07_ADR/ADR-0016_Use_Colima_on_Mac_Studio_Solo_as_primary_lab_runtime.md`
- `docs/design/02_Architecture/02C_Deployment_Topologies.md`
- `docs/design/03_Requirements/034_Platform_Requirements.md`
- `docs/design/04_Development/044_Issue_and_PR_Workflow.md`
- `docs/design/04_Development/046_CI_CD_Test_Strategy.md`
- `docs/design/10_Research/106_ARM64_Container_Image_Compatibility.md`

## Suggested Labels

`level:L1`, `workstream:benchmark`, `type:work-package`

## Notes

This WP is about harness correctness, not headline performance.

- 2026-07-16: focused `make verify-tpch-benchmark` reproduces
  `TPCH_TRINO_ICEBERG_PREREQUISITE_MISSING`. The authoritative repository is
  missing both an admitted Trino image and a Flux-managed Trino workload using
  it, so 10GB generation/query implementation remains fail-closed until the
  approved Trino-on-Iceberg runtime is materialized. Aggregate `make verify`
  and live `make gitops-status` remain green; no cluster or storage mutation was
  attempted. PR #57 `Verify` and `Supply chain security` checks passed.

## GitHub Tracking

- Epic: [#24](https://github.com/TommyKammy/Shirokuma/issues/24)
- Issue: [#32](https://github.com/TommyKammy/Shirokuma/issues/32)
- PR: [#57](https://github.com/TommyKammy/Shirokuma/pull/57) (merged)
- Runtime follow-up Epic: [#60](https://github.com/TommyKammy/Shirokuma/issues/60)
- Runtime follow-up Issue: [#66](https://github.com/TommyKammy/Shirokuma/issues/66)
- GitHub depends on: `#28`, `#29`
- Execution order: `8 of 10`
- Queue: #32 closed after PR #57 merged the focused blocker regression。
  admitted Trino runtime materializationまでharness implementationは
  fail-closedで、#66はIceberg #62とTrino #63に依存する。

## Definition of Done

- Work is represented as a GitHub Issue linked to `WP-L1-BENCH-001`.
- Changes are committed through PR.
- Required CI and policy checks pass.
- Documentation and WBS are updated.
- Any deviation from ARM64-native execution is recorded in [[10_Research/106_ARM64_Container_Image_Compatibility]].

## 2026-09-16 local functional evidence

SF10の8 tables / 86,586,082 rowsを生成し、全22 queryがgenerator参照と一致した。生成8 tableの限定削除、query drain、保持fixture一致を確認。Q15の浮動小数点差は限定DECIMAL演算で修正。SeaweedFS OOMを8GiB local memory overrideで修正し、失敗runだけのmultipart uploads15件を回収した。20 focused testsと全22組の型比較再検証が成功。公式TPC性能や本番capacityの評価ではない。

[ADR-0034](../../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)によるローカル実験。旧公開gateは履歴として保持する。実行手順・失敗と修正・復元・未達事項は[Local Experiment Milestones](../061_Local_Experiment_Milestones.md)を参照。
