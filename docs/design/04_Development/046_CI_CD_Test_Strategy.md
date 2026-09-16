---
project: Shirokuma
doc_id: "DEV-046"
title: "CI CD Test Strategy"
status: draft
created: 2026-07-05
updated: 2026-09-16
version: "0.3"
area: "development"
tags: [shirokuma, ci, testing]
---

# CI CD Test Strategy

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

## CI layers

| Layer | Checks |
|---|---|
| Docs | markdownlint, link check, frontmatter check |
| Go | gofmt, go vet, golangci-lint, go test |
| Python | ruff, mypy, pytest |
| Helm/K8s | helm lint, kubeconform, chart-testing |
| Policy | conftest, kyverno test |
| Security | gitleaks, osv-scanner, trivy, grype |
| Integration | kind smoke tests |
| Benchmark | optional nightly |

## Required checks for main

- lint
- unit tests
- policy checks
- secret scan
- build images
- kind smoke test for affected components

The L0 policy check pins Kyverno CLI `v1.18.2`, runs
`make verify-policy`, requires declared allow/deny expectations, and validates
the repository exception contract. The CI installer action is pinned by commit
SHA. Cluster admission installation is a separate gated step and is not implied
by offline policy success.

## Nightly

- dependency scan
- benchmark smoke
- agent eval harness
- docs link check
- container scan
