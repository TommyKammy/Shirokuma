# Kyverno v1.18.2 local-lab candidate inventory

This directory records the exact `linux/arm64` image set rendered by Kyverno
Helm chart `3.8.2` (`appVersion: v1.18.2`). It is research evidence only and is
not a GitOps admission manifest.

The 2026-07-14 rescan used Trivy `0.72.0` with vulnerability database timestamp
`2026-07-13T19:09:56.237113526Z`. Every image still has at least one High
finding, while no Critical findings were reported. The candidate therefore
remains blocked until every exact High finding is reviewed and time-boxed under
ADR-0019, or a signed replacement image passes the strict gate.

The upstream chart also renders `readiness-checker:latest` in Helm test hooks.
Any future Flux `HelmRelease` must pin that hook image to the admitted digest or
disable the hook path explicitly. A successful digest lookup or offline policy
test is not admission evidence.
