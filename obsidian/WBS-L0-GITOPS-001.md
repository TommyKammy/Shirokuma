---
project: Shirokuma
work_package: WP-L0-GITOPS-001
issue: 9
updated: 2026-07-14
status: in-progress
---

# WP-L0-GITOPS-001 evidence snapshot

- ADR-0018 selects pinned Flux v2 instead of Argo CD. The previous Argo CD
  scan remains historical blocker evidence only.
- The local-lab profile uses `fluxcd/flux2 v2.9.2`, the four standard
  controllers, `GitRepository`, root/dev `Kustomization`, and Git smoke state.
- Exact `linux/arm64` controller digests are recorded in
  `opentofu/dev/bootstrap-images.json` and the isolated machine-readable
  inventory under `bootstrap/flux/v2.9.2/`; no upstream-generated manifest is retained
  under `deploy/`.
- Trivy 0.72.0 with DB timestamp `2026-07-13T19:09:56.237113526Z` reports
  High findings in three official controller images: source=2, kustomize=0,
  helm=2, notification=1;
  Critical=0 for all four.
- ADR-0019 keeps the strict profile fail-closed but allows exact
  digest/CVE/package/version High findings for at most 30 days in the
  `mac-studio-solo/local-lab` profile. Critical, new High, stale exceptions,
  missing evidence, public exposure, and production use remain blocked.
- CycloneDX SBOMs, Trivy reports, signed-index identity, transparency-log,
  SLSA provenance, and upstream SPDX subject evidence are retained under
  `security/evidence/flux-v2.9.2/`.
