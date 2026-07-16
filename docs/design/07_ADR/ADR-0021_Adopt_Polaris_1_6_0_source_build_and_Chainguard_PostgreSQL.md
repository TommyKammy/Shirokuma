---
project: Shirokuma
doc_id: "ADR-0021"
title: "Adopt a source-built Polaris 1.6.0 and signed PostgreSQL metadata store"
status: proposed
created: 2026-07-16
updated: 2026-07-16
version: "0.1"
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

## Decision

- Reject the upstream Polaris 1.6.0 OCI image for resident use.
- Build Polaris from the ASF-signed 1.6.0 source release in a repository-owned,
  main-only workflow. The build must pin every base image and tool, verify PGP
  and SHA-512 before building, publish only an immutable linux/arm64 digest, and
  retain signature, transparency, provenance, CycloneDX SBOM, Trivy scan, and
  runtime-smoke evidence.
- Use the digest-pinned Chainguard PostgreSQL candidate only after its signature,
  arm64 manifest, provenance/SBOM, and zero High/Critical scan evidence are
  retained and independently reverified by the resident-image gate.
- Follow the two-phase publication lifecycle established by ADR-0020. A branch
  may introduce a closed, runtime-disabled build contract. Only a successful
  `refs/heads/main` publication followed by an evidence-only review may approve
  the Polaris digest.
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

WP-L1-LAKE-002 remains incomplete until both exact digests enter
`security/resident-images.json`, the catalog Kustomization depends on
`shirokuma-object-storage`, and live Ready plus catalog create/list/read evidence
is recorded.

## Verification

The source-build checkpoint must pass:

    python3 -m unittest -v tests.test_arm64_compatibility_matrix
    python3 scripts/verify_design_context.py
    make verify-security

The later admission checkpoint must additionally pass the repository trusted
image verifier using pinned Cosign and the retained evidence. The later runtime
checkpoint owns `make verify`, `make verify-gitops-bootstrap`, and live
`make gitops-status` evidence.

## Rollback

Before runtime admission, revert this ADR and its build contract; no cluster or
metadata state exists to recover. After admission, remove or revoke the affected
digest and evidence, keep runtime manifests blocked, and rebuild only from the
accepted source or re-resolved signed PostgreSQL image. After deployment, take
the documented PostgreSQL backup before reverting Flux resources.

## Related

- `docs/design/07_ADR/ADR-0018_Use_Flux_v2_as_the_GitOps_reconciler.md`
- `docs/design/07_ADR/ADR-0020_Adopt_SeaweedFS_4_39_source_for_arm64_build.md`
- `docs/design/04_Development/049_Supply_Chain_Security.md`
- `docs/design/10_Research/106_ARM64_Container_Image_Compatibility.md`
- `docs/design/06_WorkPackages/L1/WP-L1-LAKE-002_Polaris_catalog_bootstrap.md`
