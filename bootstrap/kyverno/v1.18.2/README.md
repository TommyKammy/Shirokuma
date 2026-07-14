# Kyverno v1.18.2 local-lab candidate inventory

This directory records the eight exact `linux/arm64` image references rendered
across the seven image repositories used by Kyverno Helm chart `3.8.2`
(`appVersion: v1.18.2`). It is research evidence only and is not a GitOps
admission manifest.

The 2026-07-14 rescan used Trivy `0.72.0` with vulnerability database timestamp
`2026-07-13T19:09:56.237113526Z`. Every image still has at least one High
finding. The mutable test-hook `readiness-checker:latest` reference currently
resolves to a different digest than `readiness-checker:v1.18.2` and has one
Critical finding, which cannot receive an ADR-0019 exception. The candidate
therefore remains blocked.

The chart renders `readiness-checker:latest` in Helm test hooks, while its
default-enabled pre-delete webhook cleanup hooks render
`readiness-checker:v1.18.2`. Any future Flux `HelmRelease` must make both paths
render an admitted immutable digest, or keep the corresponding test or cleanup
hook disabled with rollback consequences documented. A successful digest
lookup or offline policy test is not admission evidence.
