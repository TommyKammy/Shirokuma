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
Release evidence hashes the complete source record and repeats its exact
Containerfile digest and closed build-input map. The validator requires every
pinned input to be present in the Containerfile, so a changed frontend, builder,
or certificate image cannot inherit evidence from an earlier build.

`bootstrap/seaweedfs/v4.39/trusted-build-contract.json` is the closed-world
contract for this artifact. It pins the complete Containerfile hash, Buildx
and workflow-file hashes, Buildx `v0.35.0` binary checksum, BuildKit `v0.31.1` image index and linux/arm64
manifest digests, Syft `v1.46.0`, Trivy `v0.72.0`, Cosign `v3.1.1`, and Crane
`v0.21.7` archive checksum. This Cosign signing path
writes the same Sigstore bundle to the requested evidence file and OCI
referrer. `scripts/verify_trusted_image.py` validates the same
contract before the build, before promotion, in repository CI, and against the
durable evidence. Mutation fixtures cover missing tools, mutable action refs,
Containerfile drift, detached Docker plugin discovery, conflated workflow and
source SHAs, rerun-unsafe artifact names, missing promotion dependency, and
premature runtime permission.
The contract's closed-world rule applies to tools downloaded or selected by the
workflow. Docker, GitHub CLI, and operating-system utilities supplied by the
GitHub-hosted `ubuntu-24.04-arm` runner remain in the explicit runner trust
boundary and are recorded where they affect the evidence; they are not claimed
to be independently pinned binaries.
The verified Buildx plugin remains in an isolated Docker config. The GHCR
credential is mirrored to the default Docker config only while GitHub's
provenance action publishes its OCI attestation, then removed or restored by an
`always()` cleanup step.

The builder identity is the GitHub Actions OIDC identity for
.github/workflows/seaweedfs-arm64.yml in TommyKammy/Shirokuma. The workflow
first publishes a run-scoped quarantine reference. A separate promotion job may
move only ghcr.io/tommykammy/shirokuma-seaweedfs:4.39-arm64 after retained
candidate evidence passes the shared validator. The admission record must use
the resulting immutable digest. That mutable tag is a non-authoritative
publication pointer, not an admission signal; a post-tag evidence failure keeps
the run and digest inadmissible until final evidence is retained and committed.
The verify job grants only contents:read,
packages:write, id-token:write, and attestations:write; the promotion job has no
OIDC or attestation permission. It installs checksum-verified Crane before GHCR
credentials exist, uses the ephemeral OIDC identity for keyless Cosign
operations, and stores no signing key.

The published digest must have all of the following before admission:

- a keyless Cosign signature and Sigstore bundle containing the certificate,
  signed image manifest, Rekor log identity/index/time, SET, and inclusion proof,
  constrained to the exact workflow name, repository, ref, SHA, trigger, and
  GitHub token issuer, plus an independently retained Rekor API entry and a
  registry-downloaded bundle that exactly matches the durable bundle. This
  decision pins the public Rekor v1 API until a separately reviewed v2 evidence
  migration is complete;
- GitHub artifact-attestation SLSA provenance whose subject is the exact OCI
  digest and whose certificate, build config, builder, source ref, workflow
  path, run, and attempt all identify the same workflow invocation, with the
  workflow signer SHA kept distinct from the source repository SHA;
- a CycloneDX image SBOM and Trivy JSON scan generated for that digest;
- Critical findings equal to zero and High findings equal to zero, unless each
  High is separately admitted under the exact, expiring ADR-0019 contract.

The SBOM and Trivy report are attached to the digest as keyless OCI
attestations. Complete Cosign bundle and verification, Rekor response, raw image
manifest, SLSA bundles and verification, observed toolchain, runtime-smoke,
raw runtime container inspect, the exact pre-promotion release snapshot,
promotion, SBOM, scanner metadata, and scan files are committed under
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

GitHub Actions run
[`29376271915`, attempt 1](https://github.com/TommyKammy/Shirokuma/actions/runs/29376271915/attempts/1)
executed the hardened closed-world build, verification, and promotion contract and
admitted this linux/arm64 artifact:

    ghcr.io/tommykammy/shirokuma-seaweedfs@sha256:cde502bffee14bdcd735cb253c86a3ea56d0634a9a75574ff0b4657ca2daf299

The OIDC identity, workflow name/path/ref, push trigger, workflow SHA, source
SHA, run ID, and run attempt are all bound to immutable commit
`d0977813fde644a2eead942444c1cb8c626ab3b6`. Cosign `v3.1.1` verified both the
detached bundle and the bundle retrieved from the registry, and the retained
Rekor v1 response is structurally linked to the bundle entry. GitHub retained
the exact-invocation SLSA provenance at
[`attestation 35357720`](https://github.com/TommyKammy/Shirokuma/attestations/35357720);
multiple valid attestations are permitted, but every retained record must match
the same subject and build identity.

The contract pins the Dockerfile frontend, Buildx executable checksum, BuildKit
index and linux/arm64 child digests, source archive, Containerfile, and all build
inputs. Runner-provided Docker, GitHub CLI, Git, Python, curl, tar, and
`sha256sum` versions are recorded explicitly as runner substrate rather than
misrepresented as pinned inputs. Trivy `0.72.0` reported Critical=0 and High=0
with database update time `2026-07-14T19:03:26.337699315Z` and download time
`2026-07-14T23:30:08.329365967Z` before signing and provenance publication.
The CycloneDX SBOM, scanner metadata, Cosign/Rekor/SLSA records, toolchain
record, and raw container inspect are retained and hash-bound to the exact
digest.

The effective runtime inspect proves the non-root command, read-only root,
exact writable `/tmp` and `/data` tmpfs options, all capabilities dropped,
no-new-privileges, and bounded PID/memory settings. The immutable candidate
snapshot was retained as artifact `seaweedfs-4.39-arm64-candidate-29376271915-1`
before checksum-verified Crane `v0.21.7` promoted the unchanged digest. The
final artifact `seaweedfs-4.39-arm64-29376271915-1` is a 90-day mirror; the
committed evidence is authoritative. Trusted tag `4.39-arm64` is explicitly a
`non_authoritative_pointer`: admission depends only on the digest plus the
committed candidate-to-promotion-to-final evidence lineage. No ADR-0019
vulnerability exception is required.

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
