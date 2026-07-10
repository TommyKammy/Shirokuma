---
project: Shirokuma
doc_id: "UI-113"
title: "UI Interaction Model"
status: draft
created: 2026-07-10
updated: 2026-07-10
version: "0.1.0"
area: "ui-ux"
tags: [shirokuma, ui, interaction, gitops, accessibility]
---

# UI Interaction Model

## Operating contract

Every change follows one visible contract: **the bear proposes, GitOps
reconciles, humans approve intent**. The UI may prepare a repository change and
open a pull request, but it does not directly mutate resident infrastructure.

```text
Observe → Explain → Propose → Human review → Pull request → CI / policy
        → Merge decision → GitOps reconcile → Pawprint outcome
```

The proposal is advisory until a pull request exists. A merge is authoritative
intent, not proof of runtime convergence; runtime state remains pending until
the reconciler records an outcome.

## Proposal pattern

1. Show the observed state and its timestamp.
2. Explain the reason, expected benefit, cost delta, policy tier, and blast radius.
3. Present a reviewable diff against an explicitly linked repository object.
4. Offer **Create pull request** as the primary action. Never label it Apply,
   Deploy, or Save when it changes desired infrastructure state.
5. After PR creation, replace optimistic success with linked PR, CI, policy, and
   reconciliation states from their authoritative records.

Missing scope, provenance, policy, or repository binding blocks PR creation and
shows the prerequisite that must be supplied. Client-provided identity or
forwarded context is not sufficient evidence.

## Human decision pattern

| Decision | Required context | Result |
|---|---|---|
| Approve intent | diff, rationale, cost, policy, blast radius | continue to PR or merge workflow |
| Request changes | comment and affected proposal section | proposal returns to draft |
| Reject | reason and durable audit link | proposal becomes terminal; no write survives |
| Request exception | policy rule, owner, expiry, justification | separate review; original guard stays active |

Risky actions require explicit confirmation that names the affected object and
scope. Rejected or failed paths must leave no partial durable state.

## Screen behavior

- **Den:** prioritizes the proposal queue. Ask responses cite evidence and create
  draft proposals; they do not silently broaden scope.
- **Warehouses:** size or resource changes show the CRD diff, monthly estimate,
  policy result, and a Create pull request action.
- **Pawprints:** reads the authoritative lifecycle in order and links policy, CI,
  cost, trace, PR, issue, and runbook evidence.
- **Catalog & Lineage:** anchors on the selected asset. Owner, schema, quality,
  recommendations, and downstream impact include their direct source links.

## State and feedback

Use text and icon labels for `Draft`, `Needs review`, `Blocked`, `PR open`,
`Reconciling`, `Converged`, and `Failed`; color is supplementary. Loading uses a
stable skeleton. Empty states explain how records are created. Errors preserve
entered work and provide a retry only when retry is safe and idempotent.

## Accessibility baseline

- Target WCAG 2.2 AA and support keyboard-only operation.
- Keep a visible focus indicator and logical order from context to decision.
- Announce proposal and reconciliation updates without moving focus.
- Respect reduced motion; reconciliation animation is never the only state cue.
- Provide structured text alternatives for YAML diffs, charts, traces, and DAGs.
- Use at least 44 by 44 CSS-pixel targets for isolated primary controls.

## Traceability

- Work Package: [WP-L0-UX-001 / issue #5](https://github.com/TommyKammy/Shirokuma/issues/5)
- Requirements: [038_UI_Requirements.md](../03_Requirements/038_UI_Requirements.md)
- Information architecture: [112_Information_Architecture.md](112_Information_Architecture.md)
- Mockups: [111_UI_Mockups_v0.2.2.md](111_UI_Mockups_v0.2.2.md)

Runtime services, authentication APIs, and direct-apply behavior remain outside
this baseline.
