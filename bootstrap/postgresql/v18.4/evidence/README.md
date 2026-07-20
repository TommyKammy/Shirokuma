# PostgreSQL 18.4 evidence-only checkpoint

This directory retains the reviewed evidence for the exact Chainguard
PostgreSQL 18.4 OCI index and its linux/arm64 manifest. Both immutable
references and the attestation manifest were retrieved anonymously with an
empty Docker configuration.

The closed evidence set contains:

- raw index, linux/arm64, and attestation manifests;
- role-separated Sigstore bundle v0.3 records for the index and arm64
  signatures;
- signed SLSA v1 and SPDX 2.3 DSSE envelopes and attestation bundles;
- an independent Syft 1.46.0 CycloneDX 1.7 SBOM;
- a Trivy 0.72.0 exact-image report plus a CycloneDX-input report covering
  all 56 Wolfi and four Go library components, with separate database
  metadata;
- a retained Sigstore TrustedRoot, verification summary, and sorted
  `evidence.sha256` closure.

Cosign 3.1.1 reverifies every retained signature and attestation bundle
offline, including certificate identity, issuer, workflow repository, ref,
commit, trigger, and transparency-log material. The raw manifest bytes bind the
index to its exact arm64 member. The SLSA and SPDX subjects bind to that same
arm64 digest; the SPDX document contains 257 packages. The independent scan has
zero High and zero Critical findings across the complete 60-library
CycloneDX closure.

This evidence-only checkpoint does not admit or deploy PostgreSQL. The component
is only `approved_for_atomic_admission`; the resident-image ledger and runtime
manifests remain prohibited. PostgreSQL must enter the resident-image ledger
atomically with the already reviewed Polaris digest in a later focused review.
The retained Trivy results are authoritative for this evidence review only.
The atomic-admission review must rerun and bind both the exact-image and
CycloneDX-input scopes with each vulnerability database no more than 24 hours
old, reprove complete 56 Wolfi plus four Go library coverage at zero
High/Critical, and preflight anonymous exact-digest availability.
