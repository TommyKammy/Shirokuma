---
project: Shirokuma
doc_id: "REQ-038"
title: "UI Requirements"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2.2"
area: "requirements"
tags: [shirokuma, ui, requirements]
---

# UI Requirements

| ID | Area | Requirement | Priority | Acceptance |
|---|---|---|---|---|
| FR-UI-001 | Den | Home must show Ask input, Proposal Queue, Pawprints summary, and core status | Must | Static mockup and later Storybook page cover the four zones |
| FR-UI-002 | Warehouse | VirtualWarehouse changes must create PRs rather than directly applying changes | Must | UI shows YAML/CRD diff and PR CTA |
| FR-UI-003 | Pawprints | Agent actions must show policy result, CI result, token/inference cost, and OTel trace link | Must | Pawprint details panel includes those fields |
| FR-UI-004 | Catalog | Table detail must show owner, description, schema, data quality, tags, lineage, and AI explanation | Must | Catalog mockup and IA note include these fields |
| FR-UI-005 | Lineage | Impact analysis must answer “what breaks if this table changes?” | Should | Downstream assets grouped by severity |
| FR-UI-006 | FinOps | UI must surface CPU, memory, storage, host SSD watermark, and inference cost | Should | FinOps summary is available from Den and FinOps screen |
| NFR-UI-001 | Visual design | Default theme must be professional B2B SaaS, not decorative dashboard art | Must | UI Strategy defines light-first restrained theme |
| NFR-UI-002 | Accessibility | State must not depend only on color | Must | Badges include text/icon states |
| NFR-UI-003 | Safety | Destructive or policy-sensitive actions must show blast radius and policy rule | Must | Blocked/Danger interaction pattern defined |

## Related notes

- [[11_UI_UX/110_UI_Strategy]]
- [[11_UI_UX/111_UI_Mockups_v0.2.2]]
- [[11_UI_UX/113_Interaction_Model]]

## Work Package

All requirements in this baseline are owned or initially visualized by
[WP-L0-UX-001 / issue #5](https://github.com/TommyKammy/Shirokuma/issues/5).
Later runtime Work Packages may implement them without changing the PR-only
operating contract.
