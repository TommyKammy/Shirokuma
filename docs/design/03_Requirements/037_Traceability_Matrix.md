---
project: Shirokuma
doc_id: "REQ-037"
title: "Traceability Matrix"
status: draft
created: 2026-07-05
updated: 2026-07-14
version: "0.4"
area: "requirements"
tags: [shirokuma, rtm, requirements]
---

# Traceability Matrix

## 要求-成果物-Work Package対応

| Requirement | Deliverable | Work Package |
|---|---|---|
| FR-CLI-001 | shirokuma CLI init | WP-L0-DEV-001, WP-L1-LAKE-001 |
| FR-LAKE-001 | Iceberg/Polaris config | WP-L1-LAKE-002, WP-L1-LAKE-003 |
| FR-WH-001 | VirtualWarehouse CRD | WP-L3-VW-001 |
| FR-AGENT-001 | Agent PR workflow | WP-L0-AGENT-001, WP-L2-AGENT-001 |
| PLAT-FLUX-001..009 | Flux bootstrap, Source/Kustomization reconciliation, strict and bounded local-lab supply-chain gates, and credential controls | WP-L0-GITOPS-001, WP-L0-SEC-001, WP-L0-OBS-001; ADR-0018; ADR-0019 |
| FR-META-001 | OpenMetadata/OpenLineage | WP-L1-META-001 |
| FR-POL-001 | Kyverno ValidatingPolicy bundle, deterministic fixtures, and bounded exceptions | WP-L0-POL-001; `policies/kyverno/baseline.yaml`; `tests/policy/`; `scripts/verify_policy_exceptions.py` |
| NFR-SEC-001 | Supply chain CI | WP-L0-SEC-001 |
| NFR-OBS-001 | AgentHouse/Pawprint | WP-L0-OBS-001, WP-L2-EVAL-001 |
| NFR-PERF-001 | Benchmark harness | WP-L1-BENCH-001 |
| NFR-AI-001 | Agent guardrails | WP-L2-POL-001 |

| FR-UI-001 | Den UI / Proposal Queue | [WP-L0-UX-001](https://github.com/TommyKammy/Shirokuma/issues/5) |
| FR-UI-002 | VirtualWarehouse UI / CRD diff | [WP-L0-UX-001](https://github.com/TommyKammy/Shirokuma/issues/5), WP-L3-VW-001 |
| FR-UI-003 | Pawprint audit UI | [WP-L0-UX-001](https://github.com/TommyKammy/Shirokuma/issues/5), WP-L2-OBS-001 |
| FR-UI-004 | Catalog & Lineage UI | [WP-L0-UX-001](https://github.com/TommyKammy/Shirokuma/issues/5), WP-L1-META-001 |
| NFR-008 | Business application UX | [WP-L0-UX-001](https://github.com/TommyKammy/Shirokuma/issues/5) |
