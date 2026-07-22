---
project: Shirokuma
doc_id: "RES-106"
title: "ARM64 Container Image Compatibility"
status: draft
created: 2026-07-05
updated: 2026-07-22
version: "0.23"
area: "research"
tags: [shirokuma, arm64, apple-silicon]
---

# ARM64 Container Image Compatibility

Verification date: 2026-07-22. Primary target: Colima Linux/arm64 on Mac Studio M3 Ultra.

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
| Trino | `483` (2026-07-17) | blocked upstream index `docker.io/trinodb/trino@sha256:db58cc93e593a2706553745f276bb119c9810e69918be56ecde088ba7ccb0534`; ADR-0022 selects a conditional repository-owned source-build candidate | The index contains `linux/amd64`, `linux/arm64`, and `linux/ppc64le`; the arm64 child is `sha256:aa18e61b2e7776ab8641ba8baaa8687d0430894e88c639e61010cc46a994ab36`. The selected Maven/Temurin builder and Corretto Alpine 3.24 runtime candidates also have native arm64 manifests. | Apache-2.0 | The index has no attestation manifest or trusted signer; both annotated tag object `32d4f28e8311ea6f67edca209df59a0493d869fa` and source commit `50b0b50b75abd47f830b7805ee1b51716eb4065e` are unsigned; no trusted SLSA provenance binds the exact source coordinates to an approved publisher identity. | mainline candidate blocked at source authentication; ADR-0022 defines acceptable proof, and dependency publication, image publication, resident admission, and runtime remain blocked until a separate evidence review closes that gate. | Owner: WP-L1-QUERY-001. Risk: SHA pinning or Shirokuma re-signing would preserve bytes while laundering missing upstream publisher identity. Replace: authenticate the exact repository, tag, commit, and tree using an approved upstream signature, matching signed source release, or trusted provenance before executing the repository-owned reproducible source build; otherwise keep Trino blocked. | [release](https://github.com/trinodb/trino/releases/tag/483), [image tags](https://hub.docker.com/r/trinodb/trino/tags?name=483), [license](https://github.com/trinodb/trino/blob/483/LICENSE) |
| Apache Polaris | `1.6.0` (2026-07-09) | upstream `apache/polaris:1.6.0@sha256:9738b2052dea20aabf0cd42521424ff963fee41b0ee888fef9f512efb256602a` rejected; reviewed repository build `ghcr.io/tommykammy/shirokuma-polaris@sha256:db403e2db7afbe4e8a62261500e229f6d796a420e814564b49f3e14217fd6c9e` from source commit `dd306009d81a0e15adafe9dcd7d1c6d04d326f34` | Main run `29711984394` proved the exact image manifest is native `linux/arm64`; the mutable `1.6.0-arm64` tag is only a non-authoritative pointer. | Apache-2.0 | ASF PGP/SHA-512, exact source overlay, keyless signature/Rekor, current-run SLSA, CycloneDX plus attestation, Trivy High=0/Critical=0 plus attestation, and hardened smoke are retained under `bootstrap/polaris/v1.6.0/image-evidence/`. | mainline — exact image admitted only as part of the atomic Polaris/PostgreSQL pair; publisher retired; runtime remains blocked, and Flux manifests and credentials remain blocked pending runtime acceptance. | Owner: WP-L1-LAKE-002. Risk: running Polaris without the exact admitted PostgreSQL peer or before runtime acceptance would violate the metadata-store boundary. Replace: revoke both pair entries atomically, then admit only the exact reviewed Polaris/PostgreSQL pair after refreshed evidence, or keep the catalog blocked. | [release](https://github.com/apache/polaris/releases/tag/apache-polaris-1.6.0), [downloads and KEYS](https://polaris.apache.org/downloads/), [publication run](https://github.com/TommyKammy/Shirokuma/actions/runs/29711984394) |
| Polaris Admin Tool | `1.6.0` companion application (reviewed and admitted 2026-07-21) | `ghcr.io/tommykammy/shirokuma-polaris-admin@sha256:a56d09406c9dc1602cc49c0e792035c1163abf0e975fe702ef7e775c445317dd`; Amazon Corretto 21 Alpine 3.24 arm64 base manifest `sha256:dc43b39c47f1729dc772a9b8af7222757fac6c8cfa8a0802829af665b1c89925` | Main run `29807128630` attempt `1` proved native linux/arm64, Java `21.0.11`, Alpine `3.24.1`, network-none CLI smoke, and anonymous exact-digest retrieval; admission repeated an empty-Docker-config exact-digest preflight. | Apache-2.0 | ASF PGP/SHA-512 source, exact dependency OCI `ghcr.io/tommykammy/shirokuma-polaris-admin-gradle-dependencies@sha256:7a505defcd78c7a7b978e88cd4c72e0a5d8b69cbb57ddd311c163b09fe789d18`, fresh network-none/offline/strict build, Cosign/Rekor, SLSA v1, CycloneDX 1.7 (1,618 components with NoSQL/MongoDB), Trivy 0.72.0 High=0/Critical=0, attestations, 35-file publication closure, and separate six-file resident admission closure. | `admin_runtime_activation_pending`; the exact image is resident-admitted without an exception. Credentials, runtime, and Flux remain blocked. | Owner: WP-L1-LAKE-002. Risk: resident admission does not authorize runtime activation or credential provisioning. Replace: remove the exact ledger entry and admission record together if evidence is invalidated; otherwise keep catalog activation blocked until credential-safe Flux and runtime acceptance complete. | [release](https://github.com/apache/polaris/releases/tag/apache-polaris-1.6.0), [Admin Tool](https://polaris.apache.org/releases/1.6.0/admin-tool/), [PR #91](https://github.com/TommyKammy/Shirokuma/pull/91), [successful publication run](https://github.com/TommyKammy/Shirokuma/actions/runs/29807128630), [Corretto image](https://hub.docker.com/_/amazoncorretto) |
| PostgreSQL | `18.4` (reviewed 2026-07-20) | `cgr.dev/chainguard/postgres@sha256:3dc629a917612f1630c6f8e7a17f23a42cbd5917b9b3080972b70b1583daff34` | The retained index contains exactly `linux/amd64` and `linux/arm64`; the arm64 child is `sha256:c455ec159d05d99ee031d471b8692668562fed8e8c9c37be5e0dbdbee8e5f7b8`. | PostgreSQL | Separate index/arm64 signatures, SLSA v1, SPDX 2.3 (257 packages), Syft 1.46.0 CycloneDX 1.7 (4,725 components), and Trivy 0.72.0 exact-image plus CycloneDX-input scans are retained. Atomic admission repeated anonymous preflight and both scans with each database <=24 hours old; all 56 Wolfi and four Go libraries remained covered at High=0/Critical=0. Cosign 3.1.1 verifies all four standard bundles offline with the retained Sigstore TrustedRoot. | mainline — exact arm64 digest admitted atomically with the exact Polaris digest; runtime remains blocked, and Flux manifests and credentials remain blocked pending runtime acceptance. | Owner: WP-L1-LAKE-002. Risk: either half alone, incomplete library coverage, stale scans, or collapsed role-specific workflow claims would bypass the metadata-store boundary. Replace: revoke both pair entries atomically, refresh evidence, and readmit only the reviewed pair. | [image overview](https://images.chainguard.dev/directory/image/postgres/overview), [build definition](https://github.com/chainguard-images/images/tree/main/images/postgres), [license](https://www.postgresql.org/about/licence/) |
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
explicit `linux/arm64` manifest on 2026-07-13: Polaris 1.6.0,
OpenMetadata 1.13.1, SeaweedFS 4.39, StarRocks 3.5.19, Doris FE/BE 4.1.3,
ClickHouse 25.8.28.1, Gravitino 1.3.0, Amoro 0.8.1-incubating, Trino Gateway
20, and Spark 4.1.2. Comet and Gluten use the source/JAR evidence recorded in
their rows instead of a container manifest.

Issue #63 re-resolved Trino on 2026-07-22. Release 483 points to annotated tag
object `32d4f28e8311ea6f67edca209df59a0493d869fa` and commit
`50b0b50b75abd47f830b7805ee1b51716eb4065e`; GitHub reports the tag as
unsigned. The immutable image index is
`sha256:db58cc93e593a2706553745f276bb119c9810e69918be56ecde088ba7ccb0534`,
its native arm64 child is
`sha256:aa18e61b2e7776ab8641ba8baaa8687d0430894e88c639e61010cc46a994ab36`,
and the index contains no attestation manifest. The upstream server asset is
851,844,304 bytes with SHA-256
`4f3978428f26f36398c94b85a3e03b5301394919c8a4271b497b0fcd1698d0cb`,
but it is evaluated evidence rather than an approved build input because no
trusted provenance binds it to the source commit. The closed blocker record is
`bootstrap/trino/v483/admission.json`; no workflow, ledger entry, or runtime is
authorized until a separately reviewed repository-owned source build closes
the stated trust gaps.

ADR-0022 completed a conditional source-build decision on 2026-07-22 without
enabling a publisher. GitHub reports both the exact tag object and source commit
as unsigned, so a separate source-authentication evidence review must bind the
repository, tag, commit, and tree to an approved upstream publisher identity
before any dependency workflow is permitted. The later build design selects
native-arm64 Maven/Temurin builder index
`sha256:7e461cec477077c1d9e50b13df8aef9018764410f4c4cd7c34803f10c4c99e4c`
with arm64 child
`sha256:5476bfca9d0a6485b7161f6863123f7e6822336de4177273b47b5ec38ffd573a`,
and Amazon Corretto 25 Alpine 3.24 runtime index
`sha256:32d81edae73e1670244827c2f12e5bcf0d335f035b538455fe9d02eb0771d41b`
with arm64 child
`sha256:da20e1e0a2004dfb95e963d6ad978b5c0effdfc7000bce6a68836058ef24b427`.
Trivy 0.72.0 with DB timestamp `2026-07-22T13:17:28Z` reported
High=0/Critical=0 for both exact candidates. This is feasibility evidence only;
source authentication, the dependency snapshot, network-none offline build,
native container smoke, image evidence, resident admission, and runtime remain
separate fail-closed checkpoints.

The Chainguard PostgreSQL 18.4 candidate was inspected with the same command
path on 2026-07-16 and retained as an evidence-only checkpoint on 2026-07-20.
The resolved index digest
`sha256:3dc629a917612f1630c6f8e7a17f23a42cbd5917b9b3080972b70b1583daff34`
and `linux/arm64` child digest
`sha256:c455ec159d05d99ee031d471b8692668562fed8e8c9c37be5e0dbdbee8e5f7b8`
are recorded in its row. The implementing Work Package subsequently repeated
anonymous exact-reference preflight and both exact-image and CycloneDX-input
scans with each database no more than 24 hours old. The scans preserved complete
56 Wolfi plus four Go library coverage at High=0/Critical=0. The exact
PostgreSQL arm64 digest and exact Polaris digest now enter the resident-image
ledger together, while runtime/Flux manifests and credentials remain blocked.
The CycloneDX-input decision receipt separately retains `unknown=1` for
`CVE-2026-39824` in `golang.org/x/sys` `v0.1.0` (fixed in `0.44.0`).
This finding does not fail the High/Critical gate and remains explicit for
runtime-acceptance monitoring.

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
Polaris and PostgreSQL, WP-L1-QUERY-001 for Trino, WP-L1-DATAOPS-001 for Spark
and Comet, and WP-L1-META-001 for OpenMetadata. A missing signer, SBOM, scan,
architecture, or cleanup signal keeps the component blocked.

### Unchanged later-scope rows

The following pre-existing rows are outside Issue #25 and remain advisory for
their later Work Packages.

| Component | Image / Deployment | ARM64 status | v0.2 decision | Source / note |
|---|---|---|---|---|
| MinIO | `minio/minio` or source build | Repo archived/no longer maintained/source-only; arm64 may work only for pinned legacy/source builds | fallback only | https://github.com/minio/minio |
| RustFS | `rustfs/rustfs:1.0.0-beta.9@sha256:f75d0bca6ca322c4e59f7125f73dd9ab709b22f71396a42c95bce7f74c99e53b` (desk-review observation only; no pull) | Registry index inspected 2026-07-16 contains `linux/arm64` child `sha256:4096ed795289d07b8a81711cf30cf3645230e932154f50c7946015bb85ab129a`; versioned GNU/musl aarch64 release assets also exist | remain experimental; prerelease, signer trust, retained SBOM/scan with High=0/Critical=0, and S3/Iceberg smoke are unresolved | [desk review](108_RustFS_Maturity_Assessment.md), [release](https://github.com/rustfs/rustfs/releases/tag/1.0.0-beta.9), [image tags](https://hub.docker.com/r/rustfs/rustfs/tags) |
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
approves the exact source revision for a repository-controlled build. Trusted
publication is now main-only and two-phase: the builder, contract, and verifier
merge first; the merged workflow then publishes from `refs/heads/main`; a
follow-up evidence-only PR admits the resulting digest. Main run `29418029340`,
attempt `1`, completed that transition and admitted
`sha256:d1339701907587c93c6af8740388226ac2277cbbfd3df581c0e85d815c90e421`.

The closed-world contract binds source archive, Containerfile, build inputs,
the deterministic Go vendor archive, Buildx checksum, and BuildKit index/arm64
digests. Compilation is network-disabled and the trusted build imports or
exports no mutable BuildKit/GHA cache. Repository verification uses pinned
Cosign `v3.1.1` to cryptographically reverify the retained certificate, DSSE
signature, workflow identity, and transparency material rather than trusting
JSON structure alone.

Bootstrap run `29379475587`, attempt `1`, exercised the offline arm64 build,
runtime, scan, signing, provenance, and promotion gates for digest
`sha256:027be5ea9a172bbe2c29adb8928061b89ceb2a11261f5248a77653070b106d6d`.
Its final artifact is `seaweedfs-4.39-arm64-29379475587-1` and SLSA attestation
is `35365038`. Because it ran from `codex/issue-41`, it is explicitly recorded
as `not_admitted_branch_publication`; it proves implementation fitness but does
not authorize runtime use.

The machine-readable decision is retained at
`bootstrap/seaweedfs/v4.39/admission.json`. The complete Cosign, registry,
Rekor, SLSA, raw runtime-inspect, CycloneDX, scanner, candidate, and promotion
set from final artifact `seaweedfs-4.39-arm64-29418029340-1` is retained under
`bootstrap/seaweedfs/v4.39/evidence/`. Repository audit cryptographically
reverifies that set. Runtime permission remains false: parent Issue #26 may add
runtime records only after the source-build supply-chain record makes its
proposed resident-ledger entry pass `check-images`.

### WP-L1-LAKE-002 Polaris publication recheck

Polaris `1.6.0` was rechecked on 2026-07-20 after the reviewed publisher repair
merged as `706575ba3f21987033a29b6d21367981e9c54e3e`. Main run
`29711984394`, attempt `1`, completed the native arm64 build, exact-digest
runtime checks, signing, provenance, attestation, anonymous retrieval, and
trusted-tag promotion for
`ghcr.io/tommykammy/shirokuma-polaris@sha256:db403e2db7afbe4e8a62261500e229f6d796a420e814564b49f3e14217fd6c9e`.

Final artifact `polaris-image-publication-29711984394-1` (artifact ID
`8449181390`, Actions digest
`sha256:97c413927e024ff5687350b75ee172a5a890e5423292ce9c6942fd1663d3121e`)
contained 33 files / 10,007,161 bytes. Its 32-entry evidence manifest passed
without mismatch, the SBOM contained no Hadoop, Ranger, or Jetty HTTP
component, Trivy reported High=0/Critical=0, and the non-root read-only smoke
passed. Raw smoke logs and raw container inspection are excluded; the retained
set uses a secret-scanned log policy and an allowlist hardening projection.

The reviewed set is fixed under
`bootstrap/polaris/v1.6.0/image-evidence/`, and the write-capable publisher is
retired. The machine-readable state is `atomic_admission_pending`, with the
exact Polaris image `approved_for_atomic_admission`. This is not resident
admission: exact PostgreSQL evidence is reviewed separately, while the atomic
two-image review, fresh PostgreSQL dual-scope scans, credentials, Flux
resources, and live catalog acceptance remain pending. Issue #61 remains Open.

### WP-L1-LAKE-002 Polaris Admin dependency publication recheck

The separately required Polaris Admin dependency superset was rechecked on
2026-07-21 after PR #86 merged as
`619d52e0b1db5241867d7775cc8714a30b1a6f38`. Main run `29781460117`, attempt
`1`, completed the fresh network-disabled offline Admin/server regression
build, signature and provenance verification, publication, and anonymous
exact-digest retrieval for
`ghcr.io/tommykammy/shirokuma-polaris-admin-gradle-dependencies@sha256:7a505defcd78c7a7b978e88cd4c72e0a5d8b69cbb57ddd311c163b09fe789d18`.

Actions artifact `polaris-admin-publication-29781460117-1` (artifact ID
`8477021002`, Actions digest
`sha256:d1d33b14467a58b93796568667ab68ad3f61a12f9f9c3af439bbd6361adee621`,
582,463 bytes) contains only the 12 retained evidence records and does not
contain the dependency archive. The 701,437,153-byte
`polaris-gradle-dependencies-1.6.0.tar.gz` came from one-day candidate artifact
`polaris-admin-candidate-29781460117-1` (artifact ID `8476975401`) and is the
second OCI layer. Independent anonymous exact-digest retrieval verified SHA-256
`e771fe2ec6b2d0f6940b1247a512eb5cbc78dd0f36e7be247975f2c5fa36fc4d`, size,
and gzip structure. PR #87 merged the 12-file evidence review as
`8e5c6927e95d1027e16fe2ac27ab8322b45359c9`, retired the write-capable
dependency publisher, and approved only the exact dependency input for an
Admin image build.

PR #88 merged as `0fca9059179900a6d236961c1d595a66e752fb3e` and adds
`bootstrap/polaris/v1.6.0/admin-image-contract.json`,
`bootstrap/polaris/v1.6.0/Containerfile.admin`, and
`.github/workflows/polaris-admin-arm64.yml`. It records
`admin_image_publication_pending` with next state
`admin_image_evidence_review_pending` and targets
`ghcr.io/tommykammy/shirokuma-polaris-admin:1.6.0-arm64`. The Containerfile
preserves the Quarkus fast-jar layout, uses non-root `10000:10001`, and launches
`/usr/bin/java -jar /deployments/quarkus-run.jar --help`; smoke must match
`Usage: polaris-admin-tool.jar [-hV] [COMMAND]`. The image includes upstream's
NoSQL/MongoDB surface and must expose it in SBOM and scan evidence.

The first main publication run
[`29798208118`](https://github.com/TommyKammy/Shirokuma/actions/runs/29798208118)
completed the fresh offline build, closed-context image build, native arm64
checks, Admin CLI help smoke, and CycloneDX generation. Trivy 0.72.0 rejected
quarantine digest
`sha256:78a4d4f4609dfc58d6c43526ab9ea198dea2427415ad7ce86fbf2e34e76b9a84`
with High 19/Critical 0 from Amazon Linux 2023 packages: `glib2` 7, `libacl` 2,
`python3` 5, and `python3-libs` 5. Promotion, signature, provenance, and retained
candidate evidence were skipped, so this digest has no review authority.

The approved replacement candidate is Amazon Corretto 21 on Alpine 3.24,
resolved from the mutable discovery tag to exact index
`sha256:30b1b2246cee9a98c9bf8a11537a04f1eaf8c59279b0c70ae02d7e5b934edeaa`
and exact linux/arm64 manifest
`sha256:dc43b39c47f1729dc772a9b8af7222757fac6c8cfa8a0802829af665b1c89925`.
Registry history reports Corretto `21.0.11.10.1`, the filesystem exposes
`/usr/bin/java`, and a focused Trivy 0.72.0 scan reports High=0/Critical=0.
The correction PR must pin these bytes and repeat every main publication gate;
this feasibility result is not image evidence or admission.

PR #89 merged that correction as
`fe00970d75c2022c51f80cb5f00021778e8312e1`. Main run
[`29802331708`](https://github.com/TommyKammy/Shirokuma/actions/runs/29802331708)
proved native arm64, Admin CLI smoke, the disclosed NoSQL/MongoDB SBOM surface,
Trivy High=0/Critical=0, anonymous exact-digest retrieval, signature, provenance,
and attestations for
`sha256:16e3fd99da2afd446463405bd59236322c37bb066b2af5f46f6e3dd5b7c8710b`.
Promotion moved the non-authoritative trusted tag, but final artifact creation
failed because `evidence.sha256` hashed itself while being generated. The run
therefore supplies feasibility and failure evidence only; it does not authorize
review, admission, runtime, Flux, or credentials.

The later bootstrap command may use only
`bootstrap --credentials-file=<file>` with a read-only external Secret. YAML or
JSON maps top-level realms to non-empty `client-id` and `client-secret`; this
file mode is exclusive with standard realm/credential/print flags. No exact
Admin image is admitted yet, and admission, runtime, Flux, and credential gates
remain false. Issue #61 remains Open.

PR #90 merged the checksum closure repair as
`a1339e71bc3a19814102bd689fb88bfab4fb71c5`. Main run
[`29807128630`](https://github.com/TommyKammy/Shirokuma/actions/runs/29807128630)
attempt `1` completed the native arm64 build, network-none Admin CLI smoke,
CycloneDX 1.7 SBOM (1,618 components with the disclosed NoSQL/MongoDB surface),
Trivy 0.72.0 High=0/Critical=0, exact anonymous retrieval, Cosign/Rekor, SLSA v1,
SBOM/Trivy attestations, and final retention. The authoritative digest is
`sha256:a56d09406c9dc1602cc49c0e792035c1163abf0e975fe702ef7e775c445317dd`;
the mutable tag remains a pointer only. PR #91 retained and hash-bound the exact
35-file closure, retired the publisher, and moved to
`admin_image_admission_pending`. The separate resident decision rechecked
anonymous exact-digest retrieval and admitted only that image with a DB-fresh
High=0/Critical=0 record. The lifecycle is now
`admin_runtime_activation_pending`; runtime, Flux, and credential gates remain
separate and disabled.

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
