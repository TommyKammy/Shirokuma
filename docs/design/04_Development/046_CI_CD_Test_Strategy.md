---
project: Shirokuma
doc_id: "DEV-046"
title: "CI CD Test Strategy"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2"
area: "development"
tags: [shirokuma, ci, testing]
---

# CI CD Test Strategy

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

## Nightly

- dependency scan
- benchmark smoke
- agent eval harness
- docs link check
- container scan
