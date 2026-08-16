---
project: Shirokuma
doc_id: "REQ-036"
title: "Security Policy Requirements"
status: draft
created: 2026-07-05
updated: 2026-08-14
version: "0.5"
area: "requirements"
tags: [shirokuma, policy, requirements]
---

# Security Policy Requirements

## Policy requirements

| ID | Policy | Description |
|---|---|---|
| POL-001 | no-prod-direct-write | Agentによる本番直接変更禁止 |
| POL-002 | no-secret-in-git | SecretsのGit混入禁止 |
| POL-003 | deny-dangerous-sql | DROP/PURGE/DELETE大規模操作の禁止 |
| POL-004 | deny-public-service | 外部公開Service/Ingressをデフォルト拒否 |
| POL-005 | require-resource-limits | Podにresource requests/limits必須 |
| POL-006 | require-owner-labels | K8s/DB/Metric/Assetにowner必須 |
| POL-007 | require-rollback-plan | Infra PRにRollback記載必須 |
| POL-008 | restrict-auto-merge | Auto-merge対象をTierで限定 |

## L0 enforcement

- `policies/kyverno/baseline.yaml` uses Kyverno v1.18 stable
  `ValidatingPolicy` resources with `Deny` actions for privileged containers,
  host namespaces, hostPath, hostPort, public Service/Ingress exposure,
  resource requests/limits, and mutable images.
- `security/resident-images.json` remains the exact image approval source. The
  Kyverno digest rule and repository supply-chain check form one fail-closed
  boundary; digest syntax alone is not image approval.
- `security/resident-image-exceptions.json` is a separate local-lab risk
  acceptance source. It may allow only OWNER Issue/comment-authorized High
  findings for an admitted digest for no more than 30 days. Advisory, severity,
  package, installed version, fixed version, scan hash, approval dates, and
  authorization record must match exactly. Critical, production use, missing
  evidence, new or missing findings, stale records, and automatic renewal fail
  closed.
- Path-scoped Trivy misconfiguration ignores require a separate exact OWNER
  decision, canonical ID/path/statement bytes, a maximum-30-day UTC expiry, and
  an unfiltered reporting scan. Expiry or count/path/ID drift restores the
  blocking gate.
- `policies/exceptions/` requires a separate owner and reviewer, a Shirokuma
  Issue, a narrow metadata match, and an expiry no more than 30 days ahead.
- CI validates policies and fixtures offline. Live Kyverno admission may proceed
  in the local lab only after the Flux controller images satisfy the ADR-0019
  bounded resident-image gate.

## Auto-merge tiers

| Tier | 対象 | Approval |
|---|---|---|
| T0 | docs/metadata only | auto |
| T1 | tests/descriptions/dashboard draft | auto after CI |
| T2 | dev-only maintenance | auto after CI |
| T3 | schema/resource change | human approval |
| T4 | permissions/secrets/delete/external exposure | mandatory human approval |
