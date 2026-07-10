---
project: Shirokuma
doc_id: "UI-112"
title: "UI Information Architecture"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2.2"
area: "ui-ux"
tags: [shirokuma, ui, information-architecture]
---

# UI Information Architecture

## Top-level model

```text
Den
  ├─ Ask / command input
  ├─ Proposal Queue
  ├─ Pawprints summary
  └─ KPI summary

Warehouses
  ├─ VirtualWarehouse cards
  ├─ Budget / resource bars
  ├─ Query Doctor recommendations
  └─ CRD diff → PR creation

Catalog
  ├─ Polaris namespace tree
  ├─ Table details
  ├─ Schema / quality / owner / tags
  └─ AI-generated description approval

Lineage
  ├─ OpenLineage DAG
  ├─ Impact analysis
  ├─ Iceberg snapshot slider
  └─ downstream risk summary

Pawprints
  ├─ Agent timeline
  ├─ policy/CI results
  ├─ token and inference cost
  ├─ OTel trace
  └─ linked PR/Issue/Runbook

Policy
  ├─ OPA/Kyverno rules
  ├─ blocked action catalog
  ├─ auto-approval tiers
  └─ exception requests

FinOps
  ├─ CPU/memory/storage
  ├─ host SSD watermark
  ├─ token/inference cost
  └─ chill/wake recommendations
```

## User roles

| Role | Primary screens | Main jobs |
|---|---|---|
| Data user | Den, Catalog, Lineage | ask questions, inspect lineage, understand tables |
| Data engineer | Den, Catalog, Pawprints | review model proposals, inspect tests, create PRs |
| Platform operator | Warehouses, Policy, FinOps, Pawprints | approve resource changes, diagnose incidents, guard policies |
| Product owner | Den, KPI, Proposal Queue | approve intent, prioritize issues, review value |

## Navigation rule

Every object detail page should link to:

- source definition in Git;
- latest PR/Issue;
- current runtime status;
- Pawprints;
- policy decisions;
- lineage impact.

Recommendations, evidence, and reconciliation notes remain scoped to the
selected authoritative object. Sibling or same-parent records are not included
without an explicit link.

## Work Package

- [WP-L0-UX-001 / issue #5](https://github.com/TommyKammy/Shirokuma/issues/5)
- [Interaction model](113_Interaction_Model.md)
