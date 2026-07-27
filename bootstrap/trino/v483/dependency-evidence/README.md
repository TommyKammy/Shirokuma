# Trino 483 dependency snapshot evidence checkpoint

Main run [`30231656483`](https://github.com/TommyKammy/Shirokuma/actions/runs/30231656483)
from reviewed commit `1ae1996eaf654e69daad60c574c7abb4e4d2be3b` published
the non-admitted dependency snapshot retained here for evidence-only review.
The exact public reference is:

`ghcr.io/tommykammy/shirokuma-trino-maven-dependencies@sha256:0394143034298f4c6606c288e8ef97154826978bf3aa97e1e952499f8af5075c`

`publication.json` remains authoritative about the boundary: the artifact role
is `review_pending_dependency_evidence`, the dependency artifact is not
admitted, and image publication, resident admission, and runtime reconciliation
remain forbidden.

The retained publication record hash- and size-binds the other 19 run outputs:
both closed dependency descriptors, the raw OCI manifest, two independent
reconstruction proofs, two network-none native arm64 build comparisons,
CycloneDX SBOMs, raw and OpenVEX-adjusted Trivy reports, Cosign bundles and
verification output, an in-toto Statement/v1 with SLSA provenance/v1, and an
anonymous no-credentials exact-digest pull receipt. The Maven and Bun archives
are intentionally not stored in Git; their identities and sizes are
cross-bound by the descriptors, OCI manifest, publication record, and pull
receipt.

The historical publisher workflow is retained as inert review input at
`historical-publisher-workflow.yml`. The write-capable
`.github/workflows/trino-maven-dependencies.yml` path is retired and must remain
absent. Reintroducing it fails repository verification.

This checkpoint does not authenticate upstream Trino source identity. It relies
only on the time-bounded, owner-approved exception in ADR-0023, expiring at
`2026-08-21T22:43:36Z`, and the bounded OpenVEX decision in ADR-0024. It does
not add a runtime image, resident ledger entry, deployment manifest, Flux
credential, or runtime reconciliation.
