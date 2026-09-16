---
project: Shirokuma
doc_id: "PROD-010"
title: "Project Charter"
status: draft
created: 2026-07-05
updated: 2026-09-16
version: "0.2"
area: "product"
tags: [shirokuma, charter]
---

# Project Charter

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

## Mission

Build **Shirokuma**, an agentic OSS data cloud lab where AI agents observe, propose, open pull requests, and let GitOps reconcile a local Lakehouse / Warehouse / AI Ops platform.

## v0.2 hard premise

The project is developed and validated on a **single Mac Studio M3 Ultra 512GB / 4TB SSD**. Colima is the container VM runtime. No two-machine or physical multi-node cluster is in scope for the mainline plan.

## Primary outcomes

1. Recreate the control-plane ideas of Snowflake, Databricks, and Microsoft Fabric with OSS components.
2. Make the operating model agent-first: everything is declarative, policy-checked, observable, and PR-driven.
3. Measure how far AI agents can reduce toil without bypassing policy gates.
4. Keep the stack reproducible enough that `shirokuma nuke && shirokuma init` can rebuild the lab in a bounded time.

## Explicit non-goals

- GxP, CSV, ER/ES, regulated production validation.
- Physical multi-node high availability in v0.2.
- Replacing Snowflake / Databricks / Fabric in production.
- Running every candidate engine permanently.
