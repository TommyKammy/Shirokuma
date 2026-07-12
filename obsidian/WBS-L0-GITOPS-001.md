---
project: Shirokuma
work_package: WP-L0-GITOPS-001
issue: 9
updated: 2026-07-12
status: in-progress
---

# WP-L0-GITOPS-001 evidence snapshot

- ADR-0018 selects pinned Flux v2 instead of Argo CD. The previous Argo CD
  scan remains historical blocker evidence only.
- The migration branch uses `fluxcd/flux2 v2.9.1`, the four standard
  controllers, `GitRepository`, root/dev `Kustomization`, and Git smoke state.
- Exact `linux/arm64` controller digests are recorded in
  `opentofu/dev/bootstrap-images.json` and the isolated machine-readable
  inventory under `bootstrap/flux/v2.9.1/`; no unapproved manifest is retained
  under `deploy/`.
- Trivy 0.72.0 on 2026-07-12 reports unresolved High findings in all four
  official controller images: source=3, kustomize=2, helm=2, notification=1;
  Critical=0 for all four.
- CVE-2026-39822 has a fixed Go toolchain, CVE-2026-49478 has fulcio v1.8.6,
  CVE-2026-33630 has a fixed Alpine c-ares package, and CVE-2026-50163 is fixed
  in oras-go v2.6.2 but is not yet incorporated into the Flux v2.9.1 images.
- Repository scaffolding and diagnostics can migrate now, but the live
  bootstrap remains fail-closed until a signed upstream Flux release contains
  the fixes and scans with High=0/Critical=0, or a separately approved custom
  hardened image supply chain is created.
