# SeaweedFS 4.39 admission evidence

This directory is the durable, repository-retained evidence for the exact
SeaweedFS image named by `../release-evidence.json`. The final GitHub Actions
artifact is a 90-day downloadable mirror; it is not the only retained copy.

`../trusted-build-contract.json` is the closed-world contract. It enumerates the
Containerfile, builder, signing, scanning, evidence, and promotion toolchain.
`scripts/verify_trusted_image.py repository --root .` verifies that every
required file exists, is not a symlink, matches its recorded SHA-256, and closes
the following trust chain:

- `image-manifest.json` hashes to the admitted OCI digest;
- `cosign-signature-bundle.json` retains the certificate, signed payload, Rekor
  log identity/index/time, signed entry timestamp, and inclusion proof;
- `rekor-entry.json` retains the independently queried Rekor UUID and stable
  log fields;
- `cosign-verify.json` records the exact issuer and workflow identity constraints
  that passed both bundle and registry verification;
- `registry-signature-bundles.jsonl` proves that the registry contains exactly
  the same Sigstore bundle retained in this directory;
- `slsa-bundles.jsonl` and `slsa-verify.json` bind the digest to the exact
  workflow path, ref, SHA, run, attempt, builder, and source;
- `toolchain.json` reconciles observed Buildx, BuildKit, Syft, Trivy, Cosign, and
  the deferred Crane promotion pin with the contract;
- `runtime-container-inspect.json` and `runtime-smoke.json` bind the recorded
  non-root, read-only, tmpfs, capability, and resource profile to Docker's
  effective container configuration;
- the CycloneDX SBOM, Trivy metadata/report, and
  `promotion-evidence.json` bind runtime fitness, vulnerability state, and the
  digest-preserving trusted-tag transition to the same release;
- `candidate-release-evidence.json` retains the exact pre-promotion record so
  the candidate-to-final transition remains independently reproducible after
  the short-lived candidate Actions artifact expires.

The mutable `4.39-arm64` tag is only a publication pointer. These files and the
immutable digest, not the tag location, are the admission authority.

`image-manifest.json` and `cosign-signature-bundle.json` preserve the exact
producer bytes and may not end in a newline. The repository newline check
exempts only these two byte-exact evidence files; their SHA-256 bindings remain
mandatory.

These files must be replaced together after a new publication run. They approve
the repository-controlled build artifact only. Runtime manifests remain blocked
until parent Issue #26 adds a source-build supply-chain record and proves its
proposed resident-image ledger entry passes
`scripts/verify_supply_chain.py check-images`.
