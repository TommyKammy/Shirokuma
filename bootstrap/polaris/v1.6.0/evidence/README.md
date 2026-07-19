# Polaris 1.6.0 evidence checkpoint

No release evidence is admitted at this checkpoint.

The signed ASF source and retained release-signing key are pinned in
`../source.json`, but the source archive is not yet a closed Gradle build input.
The workstation PGP and SHA-512 observations are not authoritative for a build:
this checkpoint retains neither the archive nor its detached signature, and the
repository audit does not claim to cryptographically reverify either one.
Do not add a Containerfile, Polaris image-publication workflow, image digest,
resident-ledger entry, or runtime manifest until the reviewed dependency closure
and clean offline repeat exist. The sole write-capable path is the contract-pinned
`polaris-gradle-dependencies.yml` input publisher. It may publish only a
run-scoped, non-admitted dependency OCI artifact from `refs/heads/main`; it does
not authorize the Polaris image or runtime. Candidate verification is isolated
in a `contents: read` job. The write-capable job may inject a registry token only
after binding the downloaded bytes to that job's SHA-256 outputs.

After that dependency workflow succeeds, a separate evidence-only pull request
must retain and reverify its per-file descriptor, Gradle verification metadata,
raw OCI manifest and layer digests, keyless signature, SLSA verification,
toolchain record, anonymous exact-digest retrieval, and fresh network-none
offline-build result. Until that review merges, the dependency artifact
reference remains null and the Polaris Containerfile and image workflow remain
forbidden. GHCR's first private-package run may stop at the anonymous-pull gate
after signing and attestation; that attempt is not evidence. The owner must
explicitly make the package public and rerun without adding a credential
fallback. The evidence-only pull request must delete the dependency publisher
as it advances the lifecycle to `dependency_snapshot_review_pending`.

After a later main-only image publication, another evidence-only pull request
must retain
and reverify the immutable image manifest, keyless signature and transparency
material, SLSA provenance, CycloneDX SBOM and attestation, Trivy report and
attestation, toolchain record, and non-root read-only runtime smoke.
