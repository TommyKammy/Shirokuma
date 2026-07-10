---
project: Shirokuma
work_package: WP-L0-GITOPS-001
issue: 9
updated: 2026-07-10
status: blocked
---

# WP-L0-GITOPS-001 evidence snapshot

- OpenTofu root, Argo CD root Application chart, and Git smoke object are
  implemented on `codex/issue-9`.
- OpenTofu 1.12.3, providers, Argo CD chart 10.1.3, and `linux/arm64` candidate
  digests are pinned.
- Repository format, validation, chart lint, and fail-closed admission tests are
  the current focused evidence.
- Live bootstrap and repository-to-dev reconciliation remain blocked: Trivy
  0.72.0 reports High/Critical findings for the required Argo CD and Redis
  candidate images.
- Next step: select patched upstream digests, retain SBOM and Trivy artifacts,
  admit them through `security/resident-images.json`, then run the live smoke
  and teardown sequence in `docs/design/08_Runbooks/RB-001_Bootstrap_local_lite_lab.md`.
