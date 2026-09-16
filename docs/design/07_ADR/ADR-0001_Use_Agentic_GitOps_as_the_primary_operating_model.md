---
project: Shirokuma
doc_id: "ADR-0001"
title: "Use Agentic GitOps as the primary operating model"
status: "accepted"
created: 2026-07-05
updated: 2026-09-16
version: "0.2"
area: "adr"
tags: [shirokuma, adr, v0.2]
---

# ADR-0001: Use Agentic GitOps as the primary operating model

## Current local development precedence — 2026-09-16

The owner-approved [ADR-0034](../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)
and [DEV-04B](../04_Development/04B_Autonomous_Local_Development.md) govern current
local experiments. Within that standing scope, agents select WPs, execute on
the host, implement, retry, commit locally and apply/verify in the dedicated
Shirokuma lab without Issues, PRs, GitHub review or renewed per-attempt approval.
Local admission may use bounded recorded image/source identity; dedicated Flux
may be suspended or redirected with recovery notes. Earlier external-publication
and per-attempt gates below remain historical/external contracts and do not
block an authorized local alternative. Existing retained data, backups, whole
Colima resets and external writes remain outside the approval.

## Status

Accepted

## Context

ShirokumaはAIエージェントが開発・運用を支援するLabである。Agentに直接kubectl、SQL DDL、object storage lifecycle変更を許可すると、単一Mac Studio環境では復旧できても原因追跡が困難になる。GitHub Issue/PR/CI/Flux reconciliationを唯一の変更経路にすれば、Codex supervisorと人間レビューの境界を明確化できる。reconcilerの選定は[[07_ADR/ADR-0018_Use_Flux_v2_as_the_GitOps_reconciler|ADR-0018]]で確定する。

## Decision

Agentは差分生成、説明、PR作成までを行う。実環境への反映はCI、Policy、Branch Protection、Flux controllersのreconciliation loopへ任せる。

## Alternatives Considered

- **ChatOps direct apply**: 速度は速いが、監査可能性とrollback性が低い。
- **手動運用中心**: 安全だがAgent-first Labの目的から外れる。
- **Agent専用管理API**: 初期開発量が大きく、GitHub/Codexとの親和性が下がる。

## Consequences

- PRとPawprintが運用証跡になる。
- AgentはGit差分を作る能力に集中できる。
- 即時復旧よりもレビュー可能性を優先するため、操作レイテンシは増える。

## Related

- [[04_Development/042_Codex_Supervisor_GitHub_Workflow]]
- [[06_WorkPackages/060_WBS_Master]]
