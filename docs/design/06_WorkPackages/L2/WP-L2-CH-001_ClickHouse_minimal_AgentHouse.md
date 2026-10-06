---
project: Shirokuma
doc_id: "WP-L2-CH-001"
title: "ClickHouse minimal AgentHouse"
status: local-verified
created: 2026-07-05
updated: 2026-09-16
version: "0.3"
area: "workpackage"
tags: [shirokuma, wp, l2, observability]
---

# ClickHouse minimal AgentHouse

## 2026-10-06 offline direct-client credential boundary

The direct AgentHouse client now refuses an oversized, symlinked, non-private
or non-regular credential before forming its authorization header. The
independent FinOps collector may pass its own pinned credential bytes instead
of reopening the path. Synthetic private-file, mode, size and symlink cases
passed in the focused AgentHouse tests; the full local verifier passed. An
initial attempt to use a nonexistent `make test-local-agenthouse` target did
not run tests; the focused unittest command was used instead. The dedicated
Colima profile was freshly Stopped. No retained credential contents, live
Docker/query, backup or volume were accessed or changed. This bounds a local
file read, not same-UID pre-open mutation, runtime authentication or operator
acceptance. Restore only the client read if necessary while retaining saved
credentials/data; original Vault sync pending outside checkout.

| Field | Value |
|---|---|
| WP ID | WP-L2-CH-001 |
| Level | L2 |
| Workstream | Observability |
| Depends-on | WP-L0-OBS-001 |

## Scope

Deploy a minimal single-instance ClickHouse for Pawprint and AgentHouse telemetry.

## Acceptance Criteria

- ClickHouse runs native arm64.
- Resource limits are set.
- Pawprint schema exists.
- Sample agent event can be inserted and queried.
- Backup/export path is documented.

## Notes

This resolves the previous dependency bug where L2 Pawprint telemetry depended on L3 ClickHouse.

## Definition of Done

- Work is represented as a GitHub Issue linked to `WP-L2-CH-001`.
- Changes are committed through PR.
- Required CI and policy checks pass.
- Documentation and WBS are updated.
- Any deviation from ARM64-native execution is recorded in [[10_Research/106_ARM64_Container_Image_Compatibility]].

## 2026-09-16 local functional evidence

ClickHouse 26.8.5.13 native ARM64を2 CPU / 4 GiB、loopback限定で導入した。Pawprint schema、insert/query/export、event IDによる論理的重複排除、再起動後の保持を確認した。9 focused tests成功。未知のusage/costはNULL。

[ADR-0034](../../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)に基づくlocal functional検証。旧PR/CI/Flux公開gateの履歴は保持し、GitHub完了や本番受入へ読み替えない。実行手順・証跡・復元・未達事項は[Local Experiment Milestones](../061_Local_Experiment_Milestones.md)を参照。
