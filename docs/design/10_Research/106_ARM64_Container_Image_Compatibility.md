---
project: Shirokuma
doc_id: "RES-106"
title: "ARM64 Container Image Compatibility"
status: draft
created: 2026-07-05
updated: 2026-07-14
version: "0.7"
area: "research"
tags: [shirokuma, arm64, apple-silicon]
---

# ARM64 Container Image Compatibility

Verification date: 2026-07-14. Primary target: Colima Linux/arm64 on Mac Studio M3 Ultra.

## L0 platform baseline

WP-L0-PLAT-001 uses Colima's native `aarch64` VZ VM and requires Kubernetes
nodes to report `arm64`; the pinned baseline disables foreign-architecture
`binfmt` handlers. No x86_64 emulation, Rosetta, alternate image, or other
ARM64-native deviation is introduced by this Work Package. It adds no resident
service image; component-specific digest and vulnerability evidence remains the
responsibility of the later Work Package that admits that component.

## Policy

- Native `linux/arm64` is required for resident L0-L3 components unless a WP explicitly accepts x86_64/Rosetta.
- x86_64/Rosetta is a fallback, not the default.
- If image support is unknown, the component cannot become resident until a verification WP is complete.

## Supply-chain evidence for resident decisions

An ARM64 compatibility result alone does not admit an image to a resident
profile. The implementing Work Package must add the exact
`repository@sha256:<digest>` reference, upstream version and source, verified
`linux/arm64` platform, retained scan and image SBOM artifact names, scanner
version, timezone-qualified vulnerability database timestamp, and signed-index
plus provenance evidence to `security/resident-images.json`. Future vulnerability
database timestamps are rejected. Every tracked image reference under `deploy/`
and Helm templates under `charts/` must match a ledger entry. Strict profiles
require High=0/Critical=0. ADR-0019 permits only exact, time-boxed High findings
for the nonproduction `mac-studio-solo/local-lab` profile; Critical remains
blocked.

Fallback images additionally require `fallback: true`, a recorded CVE risk, a
future ISO expiry date, and a replacement plan. MinIO entries are always
fallbacks. Missing, malformed, expired, future-dated, or stale evidence fails
closed. These requirements apply to later resident-component Work Packages;
this L0 baseline does not add any resident service.

## Required components

The release is the researched upstream anchor, not an admission pin. Registry
digests below are immutable observations of the named tag on the verification
date. An OCI attestation manifest is evidence that an attachment exists; it is
not treated as a trusted signature. Every later resident Work Package must
resolve the tag again, pin the accepted digest, establish signer identity, and
pass the repository SBOM and vulnerability gate.

| Component | Upstream release | Image or build path | linux/arm64 evidence | License | Signature / provenance | v0.2 decision | Fallback owner / risk / replacement | Primary sources |
|---|---|---|---|---|---|---|---|---|
| Trino | `482` (2026-06-25) | `trinodb/trino:482@sha256:90b35b7c603eaa1f889bf03981a62b75f998ee6c0f851d9f4e341b49a57022b6` | Registry index lists `linux/arm64` with amd64 and ppc64le. | Apache-2.0 | No OCI attestation entry was present in the tag index and no trusted image signer is documented; resident admission remains blocked. | mainline resident candidate for WP-L1-QUERY-001; the later WP owns admission. | Owner: WP-L1-QUERY-001. Risk: image authenticity is not anchored to a trusted signer. Replace: build from the signed/immutable release tag and retain SBOM plus scan only if the upstream image cannot pass admission; otherwise keep blocked. | [release](https://github.com/trinodb/trino/releases/tag/482), [image tags](https://hub.docker.com/r/trinodb/trino/tags?name=482), [license](https://github.com/trinodb/trino/blob/482/LICENSE) |
| Apache Polaris | `1.6.0` (2026-07-09) | `apache/polaris:1.6.0@sha256:9738b2052dea20aabf0cd42521424ff963fee41b0ee888fef9f512efb256602a` | Registry index lists `linux/arm64`; the image is the catalog candidate for the Iceberg REST path. | Apache-2.0 | ASF source releases have PGP/SHA-512 verification; the OCI index has attestation entries, but trusted image signer identity is not established, so admission remains blocked. | mainline resident candidate for WP-L1-LAKE-002. | Owner: WP-L1-LAKE-002. Risk: attestation presence alone does not authenticate the image. Replace: build from the ASF-signed source release and retain provenance, SBOM, and scan if image signer policy cannot be satisfied; otherwise keep blocked. | [release](https://github.com/apache/polaris/releases/tag/apache-polaris-1.6.0), [downloads and KEYS](https://polaris.apache.org/downloads/), [image tags](https://hub.docker.com/r/apache/polaris/tags?name=1.6.0) |
| OpenMetadata | `1.13.1` (2026-06-27) | `openmetadata/server:1.13.1@sha256:eaa318584c52d4a492a2c56c95818b5564c6ea28b2e9695ac532c856b2c61bc9` | Registry index lists `linux/arm64`. | Apache-2.0 | OCI attestation entries are present, but trusted image signer identity is not established; resident admission remains blocked. | mainline resident candidate for WP-L1-META-001. | Owner: WP-L1-META-001. Risk: image attestations are not yet bound to a trusted publisher and dependency images remain separate gates. Replace: build from the immutable release tag only after reproducible provenance, SBOM, and scan evidence exists; otherwise keep blocked. | [release](https://github.com/open-metadata/OpenMetadata/releases/tag/1.13.1-release), [image tags](https://hub.docker.com/r/openmetadata/server/tags?name=1.13.1), [license](https://github.com/open-metadata/OpenMetadata/blob/1.13.1-release/LICENSE) |
| SeaweedFS | `4.39` (2026-07-10) | `chrislusf/seaweedfs:4.39@sha256:c7d6c721b30ae711db766bbbfd40192776e263d4e51e22f57baef7bef93c12c6` | Registry index lists `linux/arm64`, arm/v7, amd64, and 386. | Apache-2.0 | No OCI attestation entry was present in the tag index and no trusted image signer is documented; resident admission remains blocked. | mainline primary object-store candidate for WP-L1-LAKE-001. | Owner: WP-L1-LAKE-001. Risk: unsigned registry delivery and storage correctness are admission blockers. Replace: build from release `4.39` at its immutable tag and retain provenance, SBOM, scan, export, and disk-impact evidence if the upstream image fails; MinIO remains a separately approved experiment-only fallback. | [release](https://github.com/seaweedfs/seaweedfs/releases/tag/4.39), [image tags](https://hub.docker.com/r/chrislusf/seaweedfs/tags?name=4.39), [license](https://github.com/seaweedfs/seaweedfs/blob/4.39/LICENSE) |
| StarRocks | `3.5.19` (2026-06-30) | `starrocks/allin1-ubuntu:3.5.19@sha256:077c81fdbf1cf6d74a1cc4543e1c9a2df6a82cd4dd69a78aeff28fb6f99fdff8`; FE/BE images are separate for later topology work. | Registry index lists `linux/arm64`; upstream ARM deployment guidance uses multi-architecture Docker artifacts. | Apache-2.0 | OCI attestation entries are present, but trusted image signer identity is not established; no L1 admission is requested. | scope-out from L1; retain as an L3 Direct Lake candidate. | Owner: future L3 Work Package. Risk: all-in-one evidence does not prove the split production topology or signer trust. Replace: select split FE/BE/CN digests only after the L3 architecture and admission gates exist. | [release](https://github.com/StarRocks/starrocks/releases/tag/3.5.19), [ARM deployment guidance](https://docs.starrocks.io/docs/deployment/prepare_deployment_files/), [image tags](https://hub.docker.com/r/starrocks/allin1-ubuntu/tags?name=3.5.19) |
| Apache Doris | `4.1.3` (2026-07-13) | `apache/doris:fe-4.1.3@sha256:3dd47644cd9fa8152028bdae449e77170ab0de004bd7a3fa311a204a106c26c7` and `be-4.1.3@sha256:9f84a8b018069cd3c9a65af42ff5ef2c733b3b25e1ca708e0a3e4078361a1eb3` | Both FE and BE registry indexes list `linux/arm64`. | Apache-2.0 | ASF source signing is available and OCI attestation entries are present, but trusted image signer identity is not established. | scope-out from resident profiles; benchmark-only. | Owner: future benchmark Work Package. Risk: accepting FE/BE images would violate the benchmark-only boundary and still lacks trusted signer policy. Replace: reconsider only for an isolated benchmark after both digests pass the image gate; never promote from benchmark results alone. | [release](https://github.com/apache/doris/releases/tag/4.1.3), [image tags](https://hub.docker.com/r/apache/doris/tags?name=4.1.3), [license](https://github.com/apache/doris/blob/4.1.3/LICENSE.txt) |
| ClickHouse | `25.8.28.1-lts` (2026-07-05) | `clickhouse/clickhouse-server:25.8.28.1@sha256:a9d328123ff8a61bf6b16448528b577d59deb85758172e13b09054b0727f8adf` | Registry index lists `linux/arm64`. | Apache-2.0 | OCI attestation entries are present, but trusted image signer identity is not established; no L1 admission is requested. | scope-out from L1; retain as the L2 minimal analytics candidate. | Owner: future L2 Work Package. Risk: ARM manifest evidence does not establish signer trust or runtime fitness. Replace: select a then-current LTS digest only after L2 smoke, SBOM, provenance, and scan gates pass. | [release](https://github.com/ClickHouse/ClickHouse/releases/tag/v25.8.28.1-lts), [container docs](https://clickhouse.com/docs/install/docker), [license](https://github.com/ClickHouse/ClickHouse/blob/v25.8.28.1-lts/LICENSE) |
| Apache Gravitino | `1.3.0` (2026-06-29) | `apache/gravitino:1.3.0@sha256:4ff340f1160600ecac8126c2a0c4b88ea2178d3f1954966af559bab526485af6` | Registry index lists `linux/arm64`. | Apache-2.0 | ASF source signing is available and OCI attestation entries are present, but trusted image signer identity is not established. | scope-out from L1; retain for L5 evaluation. | Owner: future L5 Work Package. Risk: image architecture does not prove catalog interoperability or signer trust. Replace: reconsider only after L5 defines the authoritative catalog role and the image passes admission. | [release](https://github.com/apache/gravitino/releases/tag/v1.3.0), [image tags](https://hub.docker.com/r/apache/gravitino/tags?name=1.3.0), [license](https://github.com/apache/gravitino/blob/v1.3.0/LICENSE) |
| Apache Amoro | `0.8.1-incubating` (2025-09-11) | `apache/amoro:0.8.1-incubating@sha256:5e9826d66ca2e7ae12fe3f67b8577f7cd6316b5aa75e0674d0c5ee3479d1126a` | Registry index lists `linux/arm64`. | Apache-2.0 | ASF source signing is available and OCI attestation entries are present, but trusted image signer identity is not established. | scope-out from L1; retain for L6 evaluation only. | Owner: future L6 Work Package. Risk: incubating maturity, older release cadence, and absent trusted image signer make it unsuitable for the critical path. Replace: reconsider only after a current upstream release, role ADR, and image admission evidence exist. | [release](https://github.com/apache/amoro/releases/tag/v0.8.1-incubating), [image tags](https://hub.docker.com/r/apache/amoro/tags?name=0.8.1-incubating), [license](https://github.com/apache/amoro/blob/v0.8.1-incubating/LICENSE) |
| Trino Gateway | `20` (2026-06-25) | `trinodb/trino-gateway:20@sha256:553b0bff1920b81a7d110743a69dc84f702b2129cce0f3bf42d5f01477f66036` | Registry index lists `linux/arm64` with amd64 and ppc64le. | Apache-2.0 | No OCI attestation entry was present in the tag index and no trusted image signer is documented; no L1 admission is requested. | scope-out from L1; retain as an L3 candidate requiring PostgreSQL. | Owner: future L3 Work Package. Risk: extra stateful dependency and absent trusted image signer expand the L1 boundary. Replace: reconsider only when routing requirements and PostgreSQL ownership are explicit and all images pass admission. | [release](https://github.com/trinodb/trino-gateway/releases/tag/20), [image tags](https://hub.docker.com/r/trinodb/trino-gateway/tags?name=20), [license](https://github.com/trinodb/trino-gateway/blob/20/LICENSE) |
| Apache Spark | `4.1.2` (2026-05-21) | `apache/spark:4.1.2-scala2.13-java17-python3-ubuntu@sha256:7f44fcdd38baa7bb6fdf97f84bc12d282655d8258001a9f60287a70fb9e5033e`; upstream also provides `bin/docker-image-tool.sh`. | Registry index lists `linux/arm64`; the upstream Kubernetes guide documents the Apache image and source-build path. | Apache-2.0 | ASF source artifacts have PGP/SHA-512 verification and OCI attestation entries are present, but trusted image signer identity is not established. | mainline on-demand DataOps candidate for WP-L1-DATAOPS-001; not a resident service. | Owner: WP-L1-DATAOPS-001. Risk: image dependencies have separate license terms and signer trust is not established. Replace: build from the ASF-signed source distribution with the upstream Dockerfile if the published image fails admission; otherwise keep the job blocked. | [downloads and signatures](https://spark.apache.org/downloads.html), [Kubernetes image path](https://spark.apache.org/docs/4.1.2/running-on-kubernetes.html#docker-images), [license](https://github.com/apache/spark/blob/v4.1.2/LICENSE) |
| Apache DataFusion Comet | `0.17.0` pre-release (2026-06-20) | Maven artifact `org.apache.datafusion:comet-spark-spark4.1_2.13:0.17.0`; no standalone resident image. | The release installation guide states that Maven JARs bundle native libraries for Linux amd64 and `linux/arm64`; the arm64 target is Neoverse N1. | Apache-2.0 | The release commit has a GitHub verified signature; the later smoke must retain Maven checksums and dependency provenance before use. | mainline first-line Spark accelerator candidate; enable only after Spark correctness and fallback tests. | Owner: WP-L1-DATAOPS-001. Risk: the release is marked pre-release and its Neoverse N1 baseline may not match the Colima guest CPU surface. Replace: fall back to unaccelerated Spark immediately on incompatibility; adopt a stable Comet release after the same arm64 correctness gate passes. | [release](https://github.com/apache/datafusion-comet/releases/tag/0.17.0), [arm64 installation matrix](https://github.com/apache/datafusion-comet/blob/0.17.0/docs/source/user-guide/latest/installation.md), [signed commit](https://github.com/apache/datafusion-comet/commit/5fee7ecb8e218ab2441dd819fbe2ea51a70b40a3) |
| Apache Gluten | `1.6.0` (2026-03-10) | Source-built Spark plugin and Velox native backend; no resident image selected. | Release-tag CI builds and runs tests on `ubuntu-24.04-arm`, establishing an upstream `linux/arm64` build path. | Apache-2.0 | The release commit has a GitHub verified signature, but no release image or retained Shirokuma SBOM/provenance exists. | scope-out bonus-only; it cannot replace Comet as the first-line accelerator. | Owner: a future bonus experiment Work Package. Risk: native Velox build complexity and unqualified artifacts add supply-chain and correctness risk. Replace: keep unaccelerated Spark/Comet as authoritative; reconsider Gluten only after a dedicated arm64 artifact and correctness gate. | [release](https://github.com/apache/gluten/releases/tag/v1.6.0), [ARM CI](https://github.com/apache/gluten/blob/v1.6.0/.github/workflows/velox_backend_arm.yml), [signed commit](https://github.com/apache/gluten/commit/89718982ff3731446bbdb0882d1d4184158952b8) |

### Registry inspection method

The registry evidence above was collected without pulling or running an image:

```bash
ref='<repository>:<release-tag>'
crane digest "$ref"
crane manifest "$ref" \
  | jq -r '.manifests[] | [.platform.os, .platform.architecture, .digest] | @tsv'
```

`unknown/unknown` index entries were inspected as OCI attestation manifests and
were not counted as runtime platforms. The following release tags had an
explicit `linux/arm64` manifest on 2026-07-13: Trino 482, Polaris 1.6.0,
OpenMetadata 1.13.1, SeaweedFS 4.39, StarRocks 3.5.19, Doris FE/BE 4.1.3,
ClickHouse 25.8.28.1, Gravitino 1.3.0, Amoro 0.8.1-incubating, Trino Gateway
20, and Spark 4.1.2. Comet and Gluten use the source/JAR evidence recorded in
their rows instead of a container manifest.

### Focused image-smoke follow-up

No image is pulled, run, installed, or admitted by this research Work Package.
The owning implementation Work Packages must add the narrow smoke below before
creating a resident resource or on-demand job:

1. resolve the named tag again, require `linux/arm64`, and pin the accepted
   index digest plus platform digest;
2. authenticate the image or source artifact against an explicit trusted signer
   policy and retain provenance and SBOM artifacts;
3. scan with a timezone-qualified vulnerability database and require
   High=0/Critical=0, or obtain an ADR-0019 local-lab exception for every exact
   High CVE/package/version while keeping Critical=0;
4. run a one-shot, non-resident startup/version probe, then prove that the
   failed probe leaves no cluster resource or persistent data;
5. for Spark, run an unaccelerated correctness probe first, then the same probe
   with Comet, and prove automatic fallback to Spark; Gluten is excluded.

The follow-up owners are WP-L1-LAKE-001 for SeaweedFS, WP-L1-LAKE-002 for
Polaris, WP-L1-QUERY-001 for Trino, WP-L1-DATAOPS-001 for Spark and Comet, and
WP-L1-META-001 for OpenMetadata. A missing signer, SBOM, scan, architecture, or
cleanup signal keeps the component blocked.

### Unchanged later-scope rows

The following pre-existing rows are outside Issue #25 and remain advisory for
their later Work Packages.

| Component | Image / Deployment | ARM64 status | v0.2 decision | Source / note |
|---|---|---|---|---|
| MinIO | `minio/minio` or source build | Repo archived/no longer maintained/source-only; arm64 may work only for pinned legacy/source builds | fallback only | https://github.com/minio/minio |
| RustFS | project image | Verify selected tag and maturity | experiment | https://github.com/rustfs/rustfs |
| Cube Core | container image | Verify selected tag before L3 | L3 | https://github.com/cube-js/cube |
| Superset | official image/chart | Verify selected tag before L1 | L1 | https://superset.apache.org |
| Dagster | official image | Verify selected tag before L1 | L1 | https://github.com/dagster-io/dagster |
| dbt Core | Python/Rust binary/container | Native execution on macOS/arm64 or container; verify exact runner | L1 | https://github.com/dbt-labs/dbt-core |
| OpenHands | container | Verify image arch; can run locally with selected backend | L0/L2 | https://github.com/All-Hands-AI/OpenHands |

### WP-L1-LAKE-001 SeaweedFS admission recheck

SeaweedFS `4.39` was rechecked on 2026-07-14 after the L0 entry gate closed.
The registry index remains immutable at
`chrislusf/seaweedfs@sha256:c7d6c721b30ae711db766bbbfd40192776e263d4e51e22f57baef7bef93c12c6`,
and its linux/arm64 child manifest is
`sha256:22fe8c99253508a3d4bf2fb3c66130d9c3e238506b42c41aa3aee3bfbe3a6906`.
Platform and digest checks therefore pass, but authenticity and provenance do
not:

- `cosign verify` against the immutable index returns `no signatures found`.
- Tag `4.39` is a lightweight tag at commit
  `db42bb49757b459551607939807017d7a9d5a94a`; GitHub reports that commit as
  unsigned.
- The tag's `container_release_unified.yml` explicitly sets
  `provenance: false` and contains no Cosign signing or attestation step.
- The binary release workflows contain no retained provenance or signing step
  that can anchor a replacement linux/arm64 image build.

ADR-0019 cannot waive missing signature, transparency-log, or SLSA provenance
evidence, so the upstream SeaweedFS image remains rejected. ADR-0020 instead
approves the exact source revision for a repository-controlled build. GitHub
Actions hardened replacement run `29362206249` produced native `linux/arm64`
artifact
`ghcr.io/tommykammy/shirokuma-seaweedfs@sha256:cbf49d40f1d879dd4baba866fb2f203aba971023f3843253fbd4028469093e96`.
The workflow identity passed keyless Cosign and transparency-log verification,
immutable workflow SHA `39225a3656e388999f6755ca642cd65f7ef6c6c7`
is verified, SLSA provenance is retained as attestation `35323800`, and the
CycloneDX SBOM is bound to the same digest. Trivy `0.72.0` reported Critical=0
and High=0 with vulnerability DB timestamp
`2026-07-14T13:08:09.929373878Z`. Corrected image metadata exposes `weed mini`
ports `9340` and `23646`; the exact digest sustained a 10-second non-root smoke
with a read-only root, writable `/tmp` and `/data`, all capabilities dropped,
and no-new-privileges.
The Dockerfile frontend is immutable, scan and DB freshness gates precede
signing, and generated evidence hashes Cosign verification. The hardened digest
is promoted from a run-scoped quarantine tag only after evidence retention by
checksum-verified Crane `v0.21.7` and is admitted without an ADR-0019
vulnerability exception.

The machine-readable decision is retained at
`bootstrap/seaweedfs/v4.39/admission.json`; the exact run record is retained at
`bootstrap/seaweedfs/v4.39/release-evidence.json`, and the complete Cosign,
SLSA, runtime-smoke, CycloneDX, scanner, and Trivy evidence is retained under
`bootstrap/seaweedfs/v4.39/evidence/`. The repository-owned
`verify-object-storage-profile` check preserves the upstream rejection, pins
the admitted Shirokuma digest and workflow identity, validates those durable
files, and verifies that the source-build child did not add GitOps resources or
a resident-ledger entry. Parent Issue #26 may add runtime records only after a
source-build supply-chain record backed by those files makes its proposed
resident-ledger entry pass `check-images`.

## GitOps candidate evidence

### WP-L0-GITOPS-001 Flux candidate scan

ADR-0018 supersedes the former Argo CD candidate. Flux distribution `v2.9.2`
selects source-controller `v1.9.3`, kustomize-controller `v1.9.3`,
helm-controller `v1.6.2`, and notification-controller `v1.9.2`. Exact native
`linux/arm64` digests are pinned in `opentofu/dev/bootstrap-images.json` and the
generated Flux manifests.

Trivy `0.72.0` scanning with DB timestamp
`2026-07-13T19:09:56.237113526Z` found High findings in three images:
source-controller=2, kustomize-controller=0, helm-controller=2,
notification-controller=1; Critical=0 for all four. Findings are Go stdlib
CVE-2026-39822, oras-go CVE-2026-50163, and fulcio CVE-2026-49478.

Cosign verification succeeded for each signed OCI index using GitHub Actions
OIDC and the Flux controller-release workflow identity. Each index contains the
exact linux/arm64 manifest plus SLSA provenance v1 and SPDX SBOM attestations
whose subjects match the platform digest. CycloneDX 1.7 SBOMs and Trivy reports
are retained under `security/evidence/flux-v2.9.2/`.

ADR-0019 admits these exact digests only to `mac-studio-solo/local-lab` through
2026-08-13. Source, Helm, and notification controller High findings are listed
individually in `security/resident-image-exceptions.json`; kustomize-controller
needs no exception. Strict and production profiles remain blocked, and any
Critical, new High, stale exception, digest/package/version mismatch, or expiry
restores fail-closed behavior.

## WP decision rules

- `native-arm64`: accepted resident.
- `rosetta-accepted`: x86_64 image accepted for a specific experiment only.
- `scope-out`: removed from local profile until cloud/x86 profile.
- `side-only`: may be documented but not part of resident boot.
