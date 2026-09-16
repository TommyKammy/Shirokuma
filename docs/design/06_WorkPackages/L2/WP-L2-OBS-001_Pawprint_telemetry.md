---
project: Shirokuma
doc_id: "WP-L2-OBS-001"
title: "Pawprint telemetry"
status: local-verified
created: 2026-07-05
updated: 2026-09-16
version: "0.2"
area: "workpackage"
tags: [shirokuma, wp, l2, observability]
---

# Pawprint telemetry

| Field | Value |
|---|---|
| WP ID | WP-L2-OBS-001 |
| Level | L2 |
| Workstream | Observability |
| Depends-on | WP-L2-CH-001, WP-L2-AGENT-001 |

## Scope

Record agent actions, model calls, policy blocks, PR outcomes, and cost estimates as Pawprints.

## Acceptance Criteria

- OpenTelemetry trace is emitted for at least one agent task.
- Normalized Pawprint row is written to ClickHouse.
- Loki log and ClickHouse event can be correlated by task_id.
- Token/inference cost fields are present even if cost is estimated.

## Notes

Loki/PostgreSQL are fallback stores only; ClickHouse is the accepted L2 target.

## Definition of Done

- Work is represented as a GitHub Issue linked to `WP-L2-OBS-001`.
- Changes are committed through PR.
- Required CI and policy checks pass.
- Documentation and WBS are updated.
- Any deviation from ARM64-native execution is recorded in [[10_Research/106_ARM64_Container_Image_Compatibility]].

## 2026-09-16 local functional evidence

OpenTelemetry SDK span、Loki log、ClickHouse Pawprintのtask/trace/span相関を実機11呼び出しで検証した。token/cost列は存在し、未知の場合はNULL。監査配送の失敗は成功扱いにしない。

[ADR-0034](../../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)によるlocal functional検証。旧GitHub/PR/CI gateは公開経路の履歴として保持する。実行手順・証跡・制限は[Local Experiment Milestones](../061_Local_Experiment_Milestones.md)を参照。
