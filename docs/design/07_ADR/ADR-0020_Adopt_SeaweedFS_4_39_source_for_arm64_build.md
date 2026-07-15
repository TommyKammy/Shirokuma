---
project: Shirokuma
doc_id: "ADR-0020"
title: "Adopt SeaweedFS 4.39 source for the bounded arm64 build"
status: accepted
created: 2026-07-14
updated: 2026-07-15
version: "0.4"
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

The upstream tree has no root vendor directory. Shirokuma therefore retains a
deterministic `go-vendor.tar.xz` plus a replacement-aware authenticated record
for every actually vendored module and every vendored file hash. Pull-request
audit fully extracts the retained archive and,
from the exact clean source commit/tree/archive with Go `1.25.12`, authenticates a
fresh module cache through `proxy.golang.org` and `sum.golang.org`. It then disables
proxy, checksum-database, VCS, private-module, workspace, ambient environment, and
toolchain fallback, regenerates `vendor`, checks all 496 effective module
checksums against the pinned upstream `go.sum`, and requires every generated file
record to equal the retained manifest. It then repeats regeneration with the
proxy and checksum database disabled. The same gate
runs on main before registry credentials. The workflow validates both retained
files before they enter the build context; the Containerfile verifies the archive
hash, extracts it, and runs `go build` with `--network=none`, `-mod=vendor`, `GOPROXY=off`,
`GOSUMDB=off`, `GOTOOLCHAIN=local`, and VCS disabled. Rebuilding this adopted
image from its reviewed inputs no longer depends on a Go proxy, checksum database,
VCS host, or an ambient module cache. Provenance regeneration remains a separate
networked, fail-closed audit; service unavailability cannot fall back to an
unauthenticated source.

`bootstrap/seaweedfs/v4.39/trusted-build-contract.json` is the closed-world
contract for this artifact. It pins the complete Containerfile hash, Buildx
and workflow-file hashes, Buildx `v0.35.0` binary checksum, BuildKit `v0.31.1` image index and linux/arm64
manifest digests, Syft `v1.46.0`, Trivy `v0.72.0`, Cosign `v3.1.1`, and Crane
`v0.21.7` archive checksum. The Cosign contract fixes the v0.3 DSSE bundle,
`sign/v1` predicate, bundle-first JSONL registry download, and rejection of the
legacy `Base64Signature`/`Payload` format. `cosign verify IMAGE@DIGEST` is the
authoritative registry image verification; `verify-blob` is an additional
binding from the detached bundle to the raw OCI manifest bytes whose SHA-256 was
already checked against that image digest. This signing path writes the same
Sigstore bundle to the requested evidence file and OCI referrer.
`scripts/verify_trusted_image.py` validates the same
contract before the build, before promotion, in repository CI, and against the
durable evidence. Repository verification invokes pinned Cosign `v3.1.1`
against the retained bundle and raw OCI manifest, so the Fulcio certificate,
workflow identity, DSSE signature, and transparency material are verified
cryptographically rather than accepted from self-reported JSON.
Before the build, static validation binds the workflow's global source commit,
tree, and archive digest plus its checkout repository/ref to `source.json`.
The repository must remain a literal GitHub owner/name slug rather than a
runtime expression. Source pins may occur only in the canonical top-level
`env`; job- and step-level shadowing is rejected. The allowed job set and
canonical block grammar are closed by the contract.
Every workflow job step must have a non-empty name and the exact ordered set of
step names is closed by the contract, so an unnamed `run` or `uses` entry cannot
evade gate-order validation. The Trivy scan step and contract must both retain
the exact policy `scanners=vuln`, `severity=HIGH,CRITICAL`,
`ignore-unfixed=false`, `vuln-type=os,library`, and `exit-code=1`.
The `pending_main_publication` audit path checks this static contract without
requiring Cosign; the exact pinned Cosign becomes fail-closed once retained
evidence is admitted and cryptographic verification is possible and required.
The same pinned verifier cryptographically checks every retained SLSA bundle and
the dedicated CycloneDX and Trivy attestation bundles. The signed predicates
must be structurally identical to the retained SBOM and scan JSON, so an
evidence-only PR cannot replace a report and merely update its recorded hash.
Mutation fixtures cover missing tools, mutable action refs,
Containerfile drift, comment/continuation/heredoc decoys, alternate parser
directives, split Dockerfile keywords, extra networked builder instructions,
alternate build contexts/files, base-image build-argument overrides, detached
Docker plugin discovery, conflated workflow and source SHAs, partial-rerun
builder/promotion lineage, pre-credential promotion binding, rerun-unsafe
artifact names, missing promotion dependency,
feature-branch publication, mutable BuildKit cache, and premature runtime
permission.
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
.github/workflows/seaweedfs-arm64.yml in TommyKammy/Shirokuma at
`refs/heads/main`. Feature branches cannot enter either write-capable job. The
main workflow first publishes a run-scoped quarantine reference. A separate
promotion job may
move only ghcr.io/tommykammy/shirokuma-seaweedfs:4.39-arm64 after retained
candidate evidence passes the shared validator. The admission record must use
the resulting immutable digest. That mutable tag is a non-authoritative
publication pointer, not an admission signal; a post-tag evidence failure keeps
the run and digest inadmissible until final evidence is retained and committed.
Candidate lineage is keyed to the verify job's builder attempt. Promotion
evidence and the final artifact are keyed to the actual promotion attempt, which
may be later when only failed jobs are rerun; the validator requires the same
workflow run ID and a monotonic attempt number.
The main-only verify job grants only contents:read,
packages:write, id-token:write, and attestations:write; the promotion job has no
OIDC or attestation permission. It installs checksum-verified Crane before GHCR
credentials exist, uses the ephemeral OIDC identity for keyless Cosign
operations, and stores no signing key.

The trusted build imports and exports no BuildKit cache and sets
`no-cache: true`. The deterministic vendor archive is verified before a
`--network=none` build, so an admitted digest cannot reuse an unretained mutable
layer from an earlier run.

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
attestations, and their v0.3 DSSE bundles are retained beside the raw predicate
files. Repository verification uses `cosign verify-blob-attestation` with the
exact workflow identity and digest, then compares each signed predicate with its
retained JSON object. The retained vendor archive and module/file manifest are
immutable source-build inputs outside the runtime evidence directory. Complete
Cosign bundle and verification, Rekor response, raw image manifest, SLSA bundles
and verification, SBOM and Trivy attestation bundles, observed toolchain,
runtime-smoke,
raw runtime container inspect, the exact pre-promotion release snapshot,
promotion, SBOM, scanner metadata, and scan files are committed under
`bootstrap/seaweedfs/v4.39/evidence/` by a follow-up evidence-only PR and then
remain the durable source of truth for the admission lifetime. The main
workflow also mirrors those files as a GitHub Actions artifact for 90 days. OCI
signature and attestation retention follows
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

Any source revision, Go module graph or vendor archive, base-image digest,
target platform, workflow identity, or package-name change requires a reviewed
update to this record and regenerated evidence. Missing, malformed,
mixed-digest, or unverifiable evidence keeps the resident-image gate blocked.

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

1. leave or return bootstrap/seaweedfs/v4.39/admission.json to
   `pending_main_publication` or blocked;
2. remove the affected digest from any resident-image ledger;
3. revoke or delete the affected GHCR package version and its attestations;
4. revert the workflow or source-adoption record that created the mismatch;
5. rebuild from the accepted source only after independent review.

Earlier evidence explaining why the upstream candidate was rejected remains
part of the durable decision history.

## Publication evidence

Publication uses a two-phase lifecycle to remove the self-approval loop between
builder policy and release evidence:

1. PR #42 merges the source inputs, main-only no-cache workflow, contract, and
   verifier while `admission.json` remains `pending_main_publication`;
2. the merged workflow publishes, signs, scans, and promotes from
   `refs/heads/main` only;
3. a follow-up evidence-only PR copies that run's complete final artifact,
   changes admission to `approved`, and performs pinned Cosign cryptographic
   reverification.

Phase 2 completed from main run
[`29418029340`, attempt 1](https://github.com/TommyKammy/Shirokuma/actions/runs/29418029340/attempts/1).
The final artifact `seaweedfs-4.39-arm64-29418029340-1` and SLSA
[`attestation 35452942`](https://github.com/TommyKammy/Shirokuma/attestations/35452942)
admit
`ghcr.io/tommykammy/shirokuma-seaweedfs@sha256:d1339701907587c93c6af8740388226ac2277cbbfd3df581c0e85d815c90e421`.
Runtime manifests remain blocked until parent Issue #26 completes the resident
source-build contract.

During phase 1 no current digest was admitted: `release-evidence.json` and the
generated evidence set were intentionally absent. This avoided treating
evidence created by the same unmerged branch that defined its publisher as
approval authority.

Bootstrap run
[`29379475587`, attempt 1](https://github.com/TommyKammy/Shirokuma/actions/runs/29379475587/attempts/1)
successfully exercised the retained vendor input, offline arm64 build, scan,
Cosign, provenance, runtime, and promotion gates for digest
`sha256:027be5ea9a172bbe2c29adb8928061b89ceb2a11261f5248a77653070b106d6d`.
Its final artifact is `seaweedfs-4.39-arm64-29379475587-1` and its SLSA record is
[`attestation 35365038`](https://github.com/TommyKammy/Shirokuma/attestations/35365038).
Because that run originated from `codex/issue-41`, it is recorded only as
`not_admitted_branch_publication`; it cannot satisfy phase 2 or authorize
runtime use.

The original upstream image remains rejected. After the main run is committed,
parent Issue #26 must still add the source-build supply-chain record and prove
its proposed resident-ledger entry passes `check-images`; it also owns Flux
resources, functional smoke, disk impact, backup/export, and teardown evidence.

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

The approved follow-up admission record supplies the exact digest and main
workflow identity after the publication run completes. During
`pending_main_publication`, the strict `repository` verifier intentionally
fails; `audit` verifies that this pending state is closed and runtime-disabled.

## Related

- Issue #41
- Parent Issue #26
- docs/design/07_ADR/ADR-0019_Allow_time_boxed_resident_image_exceptions_for_local_lab.md
- docs/design/04_Development/049_Supply_Chain_Security.md
- docs/design/10_Research/106_ARM64_Container_Image_Compatibility.md
- bootstrap/seaweedfs/v4.39/admission.json
- bootstrap/seaweedfs/v4.39/source.json
- bootstrap/seaweedfs/v4.39/release-evidence.json
