---
project: Shirokuma
work_package: WP-L0-GITOPS-001
issue: 9
updated: 2026-10-06
status: local-partial
---

# WP-L0-GITOPS-001 evidence snapshot

## 2026-10-06 pinned client warnings and overrides

- The Flux gate refuses stderr warnings and caller overrides of pinned v2.9.2; fake cases and installed client pass. Independent OpenTofu formatting and 22 bootstrap/teardown tests plus full local verifier pass offline. The dedicated Colima profile remains Stopped; no live precheck, reconcile, storage/backup inspection or acceptance. Roll back the gate/test only; original Vault sync pending. See WP and Milestones.

## 2026-10-06 pinned client and stopped runtime

- Installed Flux client matches v2.9.2; the Make version gate now refuses ambiguous extra output or failed clients (seven synthetic cases). A fresh read-only local doctor degraded live cluster/Flux/workloads while the dedicated Colima profile was Stopped. Full local regression and design-context passed. This is neither `flux check --pre` nor remote source or strict admission acceptance. Inventory retained disk/backup/writers and Flux scope before starting the VM; rollback only the version gate/test, not saved state. Original Vault sync pending; see WP and Milestones.

## 2026-10-03 offline Deployment condition checks

- Fixed Flux controllers and Lakehouse Deployments now require an explicit `Available=True` alongside complete replica counters; StatefulSets continue to use replica counters. Fake missing-condition negatives and healthy fixtures, Go tests/vet and `make verify-local-experiment` pass. Dedicated Colima was observed stopped and not started. This does not authenticate live runtime, remote source or strict admission. Direct Vault handoff sync remains pending outside the checkout; rollback and gaps are in the WP and Milestones.

## 2026-10-03 offline diagnostic increment

- Fixed-profile doctor rejects endpoint ports outside 1–65535 and verifies the four named Flux controllers are Deployments in `flux-system`; fake negative and positive cases, Go tests/vet and local verification passed. Dedicated Colima remained stopped. Runtime source provenance, strict gate and Git smoke remain unverified. Vault sync pending due to noninteractive checkout boundary; see WP and Milestones for rollback.

## 2026-10-02 local-only diagnostic increment

- `shirokuma doctor --profile local-experiment` now fails closed if the fixed root GitRepository or Kustomization is missing/duplicated, the root references another source, the source revision has no bounded `ref@sha1:<40hex>` shape, either root generation is zero, or the source is terminating/not Ready at the current generation. The suspended root remains informational, not accepted as reconciled; intent and revision provenance are not independently authenticated. Generic doctor behavior remains separate.
- A bounded read-only diagnostic on `mac-studio-solo` first returned `degraded` while controllers/children were starting, then `healthy` overall and Flux `informational` with the root suspended. A separate API read confirmed the root/source fields, not the remote revision's authenticity. The profile returned to Stopped with unchanged host config hash; retained PVC/backup contents were not independently compared. Private report/logs: `tmp/local-experiment/gitops-doctor-source-checks/20261002/`.
- Go focused 674 tests and vet passed; local verification 121 suites/3820 tests (3812 pass, 8 skipped), SHA256 `a919c55e87b4c09117e93bf4fc3d9a4c7eb33563bce1a3ca4de1e301e252829a`. No GitHub write, Git reconciliation smoke, strict supply-chain admission or bootstrap/teardown was performed. See the WP and local milestones for limits and rollback. Historical scan and expired exception below remain historical, not renewed authorization.

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
