---
project: Shirokuma
doc_id: "ADR-0021"
title: "Adopt a source-built Polaris 1.6.0 and signed PostgreSQL metadata store"
status: accepted
created: 2026-07-16
updated: 2026-07-20
version: "0.5"
area: "architecture"
tags: [shirokuma, adr, polaris, postgresql, arm64, supply-chain]
---

# ADR-0021: Adopt a source-built Polaris 1.6.0 and signed PostgreSQL metadata store

## Context

WP-L1-LAKE-002 requires resident linux/arm64 images for Apache Polaris and its
PostgreSQL metadata store. The `apache/polaris:1.6.0` index contains an arm64
manifest and attestation attachments, but no trusted publisher identity is
established for that OCI image. Attachment presence is not signer
authentication, so the upstream image remains inadmissible.

Apache publishes a PGP-signed Polaris 1.6.0 source release and SHA-512 checksum.
The release tag resolves to commit
`dd306009d81a0e15adafe9dcd7d1c6d04d326f34`. Upstream documents the Java 21
Gradle container build path.

PR #78 independently reviewed the exact Gradle dependency snapshot and merged
as `b12593f27ae4e6ec8b64865f9b6b0bbf114ec654`. Publication may now advance to
the separate image checkpoint, but the snapshot and any future image remain
non-admitted until their own review boundaries complete.

The UBI 9 Java 21 runtime candidate recorded during the initial source
assessment no longer meets the zero High/Critical image gate: a 2026-07-20
Trivy 0.72.0 feasibility scan reported 21 High findings. The unmodified Polaris
server added another six High findings through Hadoop 3.5.0 and Ranger runtime
dependencies. Apache Polaris has no newer release and Hadoop 3.5.0 has no
replacement release that removes its shaded vulnerable components.

The Chainguard PostgreSQL image is a viable metadata-store candidate. On
2026-07-16 the resolved index
`cgr.dev/chainguard/postgres@sha256:3dc629a917612f1630c6f8e7a17f23a42cbd5917b9b3080972b70b1583daff34`
contained linux/arm64 manifest
`sha256:c455ec159d05d99ee031d471b8692668562fed8e8c9c37be5e0dbdbee8e5f7b8`
and PostgreSQL 18.4. Cosign verified the index against the
`chainguard-images/images` main-branch release workflow and the GitHub Actions
OIDC issuer. A focused Trivy 0.72.0 scan reported High=0 and Critical=0.

These observations select build paths; they are not resident admission by
themselves. Durable evidence and repository verification remain authoritative.
Reviewed main run `29711984394`, attempt `1`, subsequently published
`ghcr.io/tommykammy/shirokuma-polaris@sha256:db403e2db7afbe4e8a62261500e229f6d796a420e814564b49f3e14217fd6c9e`
from commit `706575ba3f21987033a29b6d21367981e9c54e3e`. Final artifact
`polaris-image-publication-29711984394-1` (ID `8449181390`) passed its 32-entry
evidence manifest, High=0/Critical=0 scan, and non-root read-only smoke. These
results establish the exact Polaris candidate; they still do not authorize a
resident image or runtime.

## Decision

- Reject the upstream Polaris 1.6.0 OCI image for resident use.
- Build Polaris from the ASF-signed 1.6.0 source release in a repository-owned,
  main-only workflow. The build must pin every base image and tool, verify PGP
  and SHA-512 before building, publish only an immutable linux/arm64 digest, and
  retain signature, transparency, provenance, CycloneDX SBOM, Trivy scan, and
  runtime-smoke evidence.
- Use Amazon Corretto 21.0.11 from the Docker Official Image as the Polaris
  runtime base, pinned to index
  `sha256:d3a3476c19cbe37b2e3e46a2116ff197ab37c7072baad55ee0ad07f3b97e8d02`
  and linux/arm64 manifest
  `sha256:ba1fe4a3fd4c6b70360183fccd1f0a168c3ea6f73709e8f81945cb9087431ff2`.
  The main publisher must repeat the zero High/Critical scan; the workstation
  observation is not admission evidence.
- Build a bounded Shirokuma downstream distribution by applying one
  hash-pinned overlay only after pristine ASF source verification. The overlay
  removes HadoopFileIO, Hadoop external-catalog federation, and Ranger
  authorization runtime edges while retaining the native Polaris catalog, OPA,
  PostgreSQL persistence, and S3 storage profile. Preimage/postimage hashes,
  absence of Hadoop/Ranger/Jetty HTTP jars and SBOM components, and exact patch
  bytes are mandatory. No vulnerability exception is used.
- Use the digest-pinned Chainguard PostgreSQL candidate only after its signature,
  arm64 manifest, provenance/SBOM, and zero High/Critical scan evidence are
  retained and independently reverified by the resident-image gate.
- Follow the two-phase publication lifecycle established by ADR-0020. A branch
  may introduce a closed, runtime-disabled build contract. Only a successful
  `refs/heads/main` publication followed by an evidence-only review may approve
  the Polaris digest.
- Retain the reviewed final publication set under
  `bootstrap/polaris/v1.6.0/image-evidence/`, mark the exact digest
  `approved_for_atomic_admission`, advance the lifecycle to
  `atomic_admission_pending`, and retire the write-capable publisher. The
  mutable `1.6.0-arm64` tag is only a non-authoritative pointer.
- Do not add Polaris, PostgreSQL, catalog bootstrap, or credential manifests
  while either image is pending. Missing evidence fails closed.
- Keep PostgreSQL credentials and the SeaweedFS application credentials in the
  approved external Secret path. No placeholder or sample credential satisfies
  readiness.

## Consequences

The source-build lifecycle adds a prerequisite checkpoint before Flux resources
can be reviewed. It avoids laundering an unauthenticated upstream image through
a local signature. PostgreSQL can use an independently signed upstream image,
but its currently resolved digest must not be inferred from the mutable
`latest` pointer after this observation.

The Polaris artifact is a disclosed Shirokuma downstream distribution rather
than byte-equivalent upstream server output. Hadoop external-catalog federation,
HadoopFileIO, and Ranger authorization are unavailable in this bounded
local-lite profile. Reintroducing any of them requires a new dependency
closure, vulnerability review, contract update, and evidence-only review.

The PostgreSQL evidence-only checkpoint retains the exact Chainguard 18.4 index
and linux/arm64 manifests under `bootstrap/postgresql/v18.4/evidence/`. The
closed checksum set includes separate index and arm64 message-signature bundles,
the raw attestation manifest and SLSA/SPDX DSSE envelopes, standard Sigstore
bundle v0.3 records, a retained Sigstore TrustedRoot, an independent CycloneDX
SBOM, an exact-image Trivy report, and a CycloneDX-input Trivy report that
covers all 56 Wolfi and four Go libraries. Both scans report zero High and zero
Critical findings. Cosign 3.1.1 verifies all four retained bundles without
registry access or TUF retrieval. The reviewed state is
`approved_for_atomic_admission` while `admission=blocked`; this checkpoint does
not modify the resident-image ledger, Flux/runtime resources, or credentials.
The retained scans authorize this evidence review only. Atomic admission
requires new exact-image and CycloneDX-input scans of the same arm64 digest.
Each vulnerability database must be no more than 24 hours old, and together
they must reprove complete 56 Wolfi plus four Go library coverage at zero
High/Critical, in addition to anonymous exact-digest availability preflight.

WP-L1-LAKE-002 remains incomplete until both exact digests enter
`security/resident-images.json`, the catalog Kustomization depends on
`shirokuma-object-storage`, and live Ready plus catalog create/list/read evidence
is recorded.

The retained Polaris image and PostgreSQL image are each approved only as one
half of a future atomic admission. Their evidence-only checkpoints leave
admission, resident-ledger permission, runtime manifests, and credentials
blocked. The combined Polaris/PostgreSQL atomic admission review, including the
fresh PostgreSQL dual-scope scans, is the next permitted change. Issue #61
remains Open through runtime acceptance.

## Verification

The source-build checkpoint must pass:

    make test-polaris-build-contract
    make verify-polaris-build-contract
    python3 -m unittest -v tests.test_arm64_compatibility_matrix
    python3 scripts/verify_design_context.py
    make verify-security

The evidence-only checkpoint must pass the repository trusted-image verifier
using pinned Cosign against every retained bundle and the exact main workflow
identity. The later atomic admission checkpoint must additionally bind the
reviewed PostgreSQL evidence, <=24-hour zero High/Critical exact-image and
CycloneDX-input rescans that close all 60 libraries, both exact digests, and the
resident-image record in one change. The later runtime checkpoint owns
`make verify`,
`make verify-gitops-bootstrap`, and live `make gitops-status` evidence.

## Rollback

Before atomic admission, revert the evidence checkpoint, withdraw
`approved_for_atomic_admission`, and keep the retired publisher and runtime
manifests absent; no cluster or metadata state exists to recover. After
admission, remove or revoke the affected digest and evidence, keep runtime
manifests blocked, and rebuild only from the accepted source or re-resolved
signed PostgreSQL image. After deployment, take the documented PostgreSQL
backup before reverting Flux resources.

## Related

- `docs/design/07_ADR/ADR-0018_Use_Flux_v2_as_the_GitOps_reconciler.md`
- `docs/design/07_ADR/ADR-0020_Adopt_SeaweedFS_4_39_source_for_arm64_build.md`
- `docs/design/04_Development/049_Supply_Chain_Security.md`
- `docs/design/10_Research/106_ARM64_Container_Image_Compatibility.md`
- `docs/design/06_WorkPackages/L1/WP-L1-LAKE-002_Polaris_catalog_bootstrap.md`
