---
project: Shirokuma
doc_id: "PROD-010"
title: "Project Charter"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2"
area: "product"
tags: [shirokuma, charter]
---

# Project Charter

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
