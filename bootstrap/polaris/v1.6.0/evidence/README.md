# Polaris 1.6.0 dependency snapshot evidence checkpoint

Run `29689013375` from reviewed main commit
`4692bab4282dfde2c8d4082e6d706dee9ce79324` produced the non-admitted
dependency snapshot retained here for evidence-only review. The exact public
reference is
`ghcr.io/tommykammy/shirokuma-polaris-gradle-dependencies@sha256:fa889d2c0a6e6dc48816d79680a366e21040be333ab6007b88e4ca4dbf6e59d6`.
`publication.json` records `admitted=false`, `anonymous_pull=true`, and
`state=dependency_snapshot_review_pending`.

This checkpoint retains and hash-binds the per-file descriptor, Gradle
verification metadata, raw OCI manifest and layer digests, keyless signature
bundle and registry verification, SLSA verification, toolchain record, and
fresh network-none strict offline-build result. The 701,323,251-byte dependency
archive is intentionally not stored in Git. Its
`sha256:18933bfb895c267302f1ee1c80cfb9712eac736ffcefade48dac53f79e8e3bc0`
identity and size are cross-bound by the retained descriptor, OCI manifest, and
publication record.
Both retained Sigstore reverifications constrain the GitHub workflow
repository, ref, trigger, and exact publisher workflow SHA
`4692bab4282dfde2c8d4082e6d706dee9ce79324`; path identity on mutable `main`
alone is insufficient.

The reviewed publisher is retired in the same change that advances the
dependency lifecycle. No remaining workflow may republish or replace this
snapshot. This checkpoint does not authorize a Polaris image, resident-ledger
entry, credentials, deployment, or runtime manifest.

After this evidence-only review merges, the next permitted step is a separate
main-only Polaris image publication. Image admission still requires its own
evidence-only review, including the immutable image manifest, keyless signature,
SLSA provenance, CycloneDX SBOM, Trivy result, pinned toolchain, and non-root
read-only runtime smoke. Polaris and PostgreSQL admission must remain atomic.
