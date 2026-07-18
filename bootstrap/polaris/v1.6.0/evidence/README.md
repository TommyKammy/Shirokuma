# Polaris 1.6.0 evidence checkpoint

No release evidence is admitted at this checkpoint.

The signed ASF source and retained release-signing key are pinned in
`../source.json`, but the source archive is not yet a closed Gradle build input.
The workstation PGP and SHA-512 observations are not authoritative for a build:
this checkpoint retains neither the archive nor its detached signature, and the
repository audit does not claim to cryptographically reverify either one.
Do not add a Containerfile, write-capable publication workflow, image digest,
resident-ledger entry, or runtime manifest until the reviewed dependency closure
and clean offline repeat exist.

After a main-only publication, a separate evidence-only pull request must retain
and reverify the immutable image manifest, keyless signature and transparency
material, SLSA provenance, CycloneDX SBOM and attestation, Trivy report and
attestation, toolchain record, and non-root read-only runtime smoke.
