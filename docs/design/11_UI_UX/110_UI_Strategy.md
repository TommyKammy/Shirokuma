---
project: Shirokuma
doc_id: "UI-110"
title: "UI Strategy"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2.2"
area: "ui-ux"
tags: [shirokuma, ui, ux, product-design]
---

# UI Strategy

## Positioning

ShirokumaのUIは、Snowflake/Fabric風の管理ダッシュボードではなく、**Agentic Data Cloudの作業面**である。

中心に置くべき体験は、メトリクスを眺めることではなく、次の循環である。

```text
Human intent → Bear proposes → GitHub PR → CI/Policy → GitOps reconciliation → Pawprint evidence
```

Fable 5/JAIのコンセプトレビューは採用する。ただしv0.2.2では、派手なpolar-night表現を抑え、日常的な業務利用に耐える**洗練されたB2B SaaS / developer platform UI**へ寄せる。

## Visual direction

| Item | Decision |
|---|---|
| Theme | Light-first professional theme |
| Accent | calm ice blue, restrained teal, semantic red/amber/green |
| Mascot | subtle Shirokuma avatar; no oversized decorative mascot in working screens |
| Density | business app density; cards are functional, not decorative |
| Motion | minimal; use motion only for reconciliation state, streaming traces, and loading |
| Typography | clear Japanese UI labels; numeric metrics use tabular style |
| Dark theme | optional later; not the default mockup |

## Accessibility baseline

- Target WCAG 2.2 AA for the production implementation.
- Express state with text or icons as well as color and preserve visible focus.
- Support keyboard-only review and reduced motion.
- Provide structured alternatives for diffs, charts, traces, and lineage DAGs.

## Work Package

[WP-L0-UX-001 / issue #5](https://github.com/TommyKammy/Shirokuma/issues/5)
owns this strategy baseline. The detailed interaction contract is defined in
[113_Interaction_Model.md](113_Interaction_Model.md).

## Product principles

1. **Approval-first UI**: 主要CTAは「適用」ではなく「PRを作成」「Approve」「Request changes」。
2. **Policy-visible UI**: OPA/Kyverno/CIによって何が許可・拒否されたかを画面に出す。
3. **Evidence-first UI**: Pawprint、PR、Issue、OpenTelemetry trace、token costを相互リンクする。
4. **Human-readable, machine-traceable**: 人間に読みやすい説明と、Agentが再利用できるJSON/YAML/trace IDを同時に持つ。
5. **Cute, not childish**: しろくまは親しみのために使うが、業務アプリの信頼感を優先する。

## Primary navigation

| Navigation | Purpose |
|---|---|
| Den | Agent Mission Control / Home |
| Warehouses | VirtualWarehouse management and PR-based scaling |
| Catalog | Polaris / OpenMetadata / OpenLineage table exploration |
| Lineage | DAG, impact analysis, snapshot time travel |
| Pawprints | Agent activity, audit timeline, OTel trace |
| Data Quality | dbt tests, Soda/GX, freshness |
| Policy | OPA/Kyverno policies, blocked actions |
| FinOps | CPU/memory/storage/token/host SSD watermark |
| Settings | profiles, endpoints, GitHub integration, LLM providers |

## Mockup set

See [[11_UI_UX/111_UI_Mockups_v0.2.2]].
