# Product

## Register

product

## Users

Shirokuma serves data users, data engineers, platform operators, and product
owners working in a single-host OSS data-cloud lab. They ask questions, inspect
data and lineage, review agent proposals, approve intent, and diagnose policy or
runtime outcomes. The current local experimental workflow follows ADR-0034:
authorized implementation and verification proceed through local commits,
without GitHub publication or per-attempt approval gates.

## Product Purpose

Shirokuma makes an AI-first data platform understandable and governable. The
bear proposes a bounded change, local tools verify its effects, and humans
retain authority over intent. External GitOps delivery is a separate workflow.
Success means an operator can understand
what is proposed, its cost and blast radius, the evidence behind it, and the
applicable authorization boundary from one traceable work surface.

## Brand Personality

Calm, rigorous, and quietly helpful. Copy is concise and operational; the bear
is a subtle guide rather than decoration. The interface should create confidence
and control, especially when a proposal is blocked or needs review.

## Anti-references

- Decorative metric dashboards that make every value a card.
- Consumer-chat interfaces that hide provenance, policy, or approval state.
- Mascot-heavy screens, neon gradients, glass effects, and playful control copy.
- Direct-apply infrastructure consoles that collapse proposal and approval.

## Design Principles

1. Make the observation, proposal, execution and verification boundaries visible.
2. Put actionable failures and required human decisions ahead of ambient metrics.
3. Pair every state with provenance, policy, cost, and impact where relevant.
4. Reveal complexity progressively while keeping authoritative records reachable.
5. Prefer calm density and precise language over decorative surfaces.

## Accessibility & Inclusion

Target WCAG 2.2 AA for future implementation. Keyboard order follows the visual
workflow; focus remains visible; state is always expressed with text or an icon
in addition to color; motion respects reduced-motion preferences; tables, diffs,
and lineage views must provide equivalent structured text.
