---
project: Shirokuma
doc_id: "ADR-0020"
title: "Adopt SeaweedFS 4.39 source for the bounded arm64 build"
status: accepted
created: 2026-07-14
updated: 2026-07-15
version: "0.2"
area: "architecture"
tags: [shirokuma, adr, seaweedfs, arm64, supply-chain]
---

# ADR-0020: Adopt SeaweedFS 4.39 source for the bounded arm64 build

## Context

SeaweedFS 4.39 is the mainline object-storage choice for the
mac-studio-solo local lab, but the upstream OCI image and release commit do
not provide the signature, transparency-log, and SLSA provenance required by
the resident-image gate. ADR-0019 permits exact, time-boxed High-severity
exceptions; it does not permit these authenticity controls to be waived.

The upstream release tag resolves to unsigned commit
db42bb49757b459551607939807017d7a9d5a94a. Shirokuma therefore needs an
explicit adoption boundary that does not misrepresent a Shirokuma signature as
upstream authorship.

## Decision

Shirokuma adopts exactly the upstream Git commit and Git tree recorded in
bootstrap/seaweedfs/v4.39/source.json for a repository-controlled linux/arm64
build. The workflow verifies the commit, tree, and deterministic git-archive
SHA-256 before building. A mismatch fails closed.

The build inputs are digest-pinned in the source record. Go 1.25.12 is used
because the upstream minimum Go 1.25.8 toolchain contains known, fixed
High-severity standard-library vulnerabilities; the adopted SeaweedFS source
tree itself remains unchanged.

The builder identity is the GitHub Actions OIDC identity for
.github/workflows/seaweedfs-arm64.yml in TommyKammy/Shirokuma. The workflow
may publish only ghcr.io/tommykammy/shirokuma-seaweedfs:4.39-arm64, and the
admission record must use the resulting immutable digest. The workflow grants
only contents:read, packages:write, id-token:write, and attestations:write. It
uses the ephemeral OIDC identity for keyless Cosign operations and stores no
signing key.

The published digest must have all of the following before admission:

- a keyless Cosign signature and Rekor transparency-log evidence constrained
  to the repository workflow identity and GitHub token issuer;
- GitHub artifact-attestation SLSA provenance whose subject is the exact OCI
  digest and whose builder is the same workflow;
- a CycloneDX image SBOM and Trivy JSON scan generated for that digest;
- Critical findings equal to zero and High findings equal to zero, unless each
  High is separately admitted under the exact, expiring ADR-0019 contract.

The SBOM and Trivy report are attached to the digest as keyless OCI
attestations. Complete Cosign verification, SLSA verification, runtime-smoke,
SBOM, scanner metadata, and scan files are committed under
`bootstrap/seaweedfs/v4.39/evidence/` and remain the durable source of truth for
the admission lifetime. The workflow also mirrors those files as a GitHub
Actions artifact for 90 days. OCI signature and attestation retention follows
the GHCR package version: neither the Git evidence nor the OCI attachments may
be deleted while the digest remains admitted.

This is a Shirokuma source-adoption signature. It proves what Shirokuma built
and which workflow built it; it does not assert that SeaweedFS upstream signed
or authored the resulting OCI package.

## Scope boundary

This decision covers one SeaweedFS version, one upstream commit, one
repository-owned GHCR package, and linux/arm64 only. It does not approve a
runtime deployment, credentials, buckets, PersistentVolumes, Flux resources,
multi-architecture publication, production use, or general third-party image
hosting.

Any source revision, base-image digest, target platform, workflow identity, or
package-name change requires a reviewed update to this record and regenerated
evidence. Missing, malformed, mixed-digest, or unverifiable evidence keeps the
resident-image gate blocked.

## Consequences

Shirokuma owns the security and maintenance risk introduced by adopting an
unsigned upstream revision. Repository-controlled provenance makes the build
reproducible and attributable but cannot retroactively supply upstream
authorship. The scratch runtime image reduces resident packages and scan
surface. This child runs a bounded non-root `weed mini` startup smoke against
the exact digest; the parent work package still owns functional S3 and
persistence smoke.

The build and retained workflow artifacts consume GitHub Actions, GHCR, and
artifact storage only. This child task allocates no host SSD capacity and
creates no object-store data, so it adds no backup/export obligation.

## Revocation and rollback

If source identity, platform, signature, transparency entry, provenance, SBOM,
or scan verification fails:

1. leave or return bootstrap/seaweedfs/v4.39/admission.json to blocked;
2. remove the affected digest from any resident-image ledger;
3. revoke or delete the affected GHCR package version and its attestations;
4. revert the workflow or source-adoption record that created the mismatch;
5. rebuild from the accepted source only after independent review.

Earlier evidence explaining why the upstream candidate was rejected remains
part of the durable decision history.

## Publication evidence

The replacement GitHub Actions run
[`29362206249`](https://github.com/TommyKammy/Shirokuma/actions/runs/29362206249)
published and admitted this hardened linux/arm64 artifact:

    ghcr.io/tommykammy/shirokuma-seaweedfs@sha256:cbf49d40f1d879dd4baba866fb2f203aba971023f3843253fbd4028469093e96

The run verified the GitHub Actions OIDC workflow identity and immutable
workflow SHA `39225a3656e388999f6755ca642cd65f7ef6c6c7` with keyless Cosign
and transparency-log evidence. GitHub retained SLSA provenance at
[`attestation 35323800`](https://github.com/TommyKammy/Shirokuma/attestations/35323800).
The exact-digest CycloneDX SBOM and Trivy scan are retained in Git under
`bootstrap/seaweedfs/v4.39/evidence/` and mirrored in artifact `8322642193`;
Trivy `0.72.0` reported zero Critical and zero High findings with
vulnerability DB timestamp `2026-07-14T13:08:09.929373878Z`. The image metadata
exposes the active `weed mini` volume HTTP port `9340` and admin HTTP port
`23646`, and the non-root default command starts successfully with writable
`/tmp` and `/data` tmpfs, a read-only root, all capabilities dropped, and
no-new-privileges for the bounded 10-second smoke. The Dockerfile frontend is
pinned by digest, the vulnerability and DB
freshness gates complete before signing or provenance publication, and the
generated evidence hashes the Cosign verification output. No ADR-0019
vulnerability exception is required. The build publishes first to run-scoped
quarantine tag `quarantine-29362206249-1`; after all gates and evidence
retention pass, checksum-verified Crane `v0.21.7` promotes the unchanged digest to trusted tag
`4.39-arm64`. Repeated valid SLSA attestations are accepted when at least one
matches the exact workflow SHA and digest.

The original upstream image remains rejected. This decision admits only the
hardened Shirokuma artifact above. Runtime manifests remain blocked until parent
Issue #26 adds a source-build supply-chain record backed by the committed files
and proves its proposed resident-ledger entry passes `check-images`; the parent
also owns Flux resources, functional smoke, disk impact, backup/export, and
teardown evidence.

## Verification

Use the exact digest and workflow identity recorded by the publication run:

    cosign verify --certificate-oidc-issuer https://token.actions.githubusercontent.com \
      --certificate-identity '<workflow-identity>' \
      ghcr.io/tommykammy/shirokuma-seaweedfs@sha256:<digest>
    gh attestation verify \
      oci://ghcr.io/tommykammy/shirokuma-seaweedfs@sha256:<digest> \
      --repo TommyKammy/Shirokuma
    cosign verify-attestation \
      --certificate-oidc-issuer https://token.actions.githubusercontent.com \
      --certificate-identity '<workflow-identity>' --type cyclonedx \
      ghcr.io/tommykammy/shirokuma-seaweedfs@sha256:<digest>

The committed admission record supplies the exact digest and workflow identity
after the publication run completes.

## Related

- Issue #41
- Parent Issue #26
- docs/design/07_ADR/ADR-0019_Allow_time_boxed_resident_image_exceptions_for_local_lab.md
- docs/design/04_Development/049_Supply_Chain_Security.md
- docs/design/10_Research/106_ARM64_Container_Image_Compatibility.md
- bootstrap/seaweedfs/v4.39/admission.json
- bootstrap/seaweedfs/v4.39/source.json
- bootstrap/seaweedfs/v4.39/release-evidence.json
