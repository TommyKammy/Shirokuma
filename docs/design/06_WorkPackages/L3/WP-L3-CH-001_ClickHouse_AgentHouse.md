---
project: Shirokuma
doc_id: "WP-L3-CH-001"
title: "ClickHouse AgentHouse advanced dashboards"
status: active
created: 2026-07-05
updated: 2026-09-17
version: "0.3"
area: "workpackage"
tags: [shirokuma, wp, l3, observability]
---

# ClickHouse AgentHouse advanced dashboards

| Field | Value |
|---|---|
| WP ID | WP-L3-CH-001 |
| Level | L3 |
| Workstream | Observability |
| Depends-on | WP-L2-CH-001, WP-L2-OBS-001 |

## Scope

Extend the L2 ClickHouse AgentHouse with dashboards, benchmark events, query history extracts, and cost analytics.

## Acceptance Criteria

- Grafana dashboard shows agent PR success, policy blocks, token cost, and latency.
- Benchmark events are stored.
- Query history import path from Trino/Trino Gateway is documented.
- L2 schema migration is backward compatible.

## Notes

ClickHouse installation itself moved to L2.

## Definition of Done

- Work is represented as a GitHub Issue linked to `WP-L3-CH-001`.
- Changes are committed through PR.
- Required CI and policy checks pass.
- Documentation and WBS are updated.
- Any deviation from ARM64-native execution is recorded in [[10_Research/106_ARM64_Container_Image_Compatibility]].

## 2026-09-17 local bounded implementation plan

ADR-0034の包括承認で既存AgentHouse Pawprintsを読むGrafana dashboardを追加する。
Native ARM64のGrafana OSS/公式ClickHouse pluginを固定し、専用Docker networkと
loopback公開、専用SELECT-only DB userを使用する。L2 schema/保持データは変更しない。
GrafanaのadminとDB credentialsはprivate ignored filesに保存し、画面/ログ/Gitへ転記しない。

- [ ] 記録されたfailed/blocked eventsからtask/traceを絞り込める。
- [ ] 処理別p50/p95を表示し、親/子durationを合計してwall timeとしない。
- [ ] provider/modelごとのtokens既知小計・既知/欠測callsを表示し、mockと実モデルを分離する。
- [ ] null費用は未観測とし、PR成功率・通貨換算・一般モデル品質を捏造しない。
- [ ] ReplacingMergeTreeをFINALで読み、retry物理重複をlogical eventとして数えない。
- [ ] 権限・resource/row/time bounds、所有inventory、repeat up、stop/recoveryを検証する。
- [ ] 実dashboard query・API・画面・tests・Vault/WBSを確認する。

この増分ではGrafana画面は読み取り専用。監査eventの成功はworkflow全体完了や
本番受入を意味しない。全Taskの成功率はterminal契約が統一されるまで表示しない。
benchmark event取込、Trino query history持続化、実費計測は後続未達とする。
歴史的Issue/PR/CI gateは外部delivery経路でありlocal作業を止めない。
