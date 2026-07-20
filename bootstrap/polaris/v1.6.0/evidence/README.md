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

The separate main-only Polaris image publication completed in run
`29711984394` from commit
`706575ba3f21987033a29b6d21367981e9c54e3e`. Its evidence is retained under
`bootstrap/polaris/v1.6.0/image-evidence/` so its filenames cannot collide with
this dependency checkpoint.

## Polaris image evidence checkpoint

The immutable review candidate is
`ghcr.io/tommykammy/shirokuma-polaris@sha256:db403e2db7afbe4e8a62261500e229f6d796a420e814564b49f3e14217fd6c9e`.
The durable image checkpoint is an exact 33-file closed set:
`bootstrap/polaris/v1.6.0/release-evidence.json` binds the publisher run,
historical contract and admission hashes, GitHub Actions artifacts, and the
exact 32-file payload inventory. The publisher-generated `evidence.sha256`
self-manifest is retained as the 33rd file and is checked independently against
that same closed payload set.
The retained `publication.json` records `promoted=true` and `admitted=false`.

The retained checks prove anonymous exact-digest retrieval, native
`linux/arm64`, keyless signature and SLSA identity at the exact publisher
commit, CycloneDX SBOM, zero High/Critical Trivy findings, bounded runtime
contents, and a non-root/read-only/capability-dropped smoke. Raw and sanitized
runtime logs are deliberately absent; only the reviewed redaction policy record
is retained.

The image publisher is retired in this evidence-only change. The checkpoint
advances only to `atomic_admission_pending`: the image remains
`admitted=false`, no resident-ledger or runtime manifest is permitted, and
Polaris must not be admitted separately from PostgreSQL.
