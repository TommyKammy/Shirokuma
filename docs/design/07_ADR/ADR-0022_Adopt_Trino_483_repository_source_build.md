---
project: Shirokuma
doc_id: "ADR-0022"
title: "Select a conditional repository-owned Trino 483 source build"
status: accepted
created: 2026-07-22
updated: 2026-07-24
version: "0.6"
area: "architecture"
tags: [shirokuma, adr, trino, arm64, maven, supply-chain]
---

# ADR-0022: Select a conditional repository-owned Trino 483 source build

## Context

WP-L1-QUERY-001A requires a resident linux/arm64 Trino artifact before Flux can
materialize the query runtime. The upstream `trinodb/trino:483` index is
immutable and contains a native arm64 manifest, but it has no attestation
manifest or established trusted signer. The annotated release tag is also
unsigned, and no trusted SLSA statement binds the upstream image or server
asset to source commit `50b0b50b75abd47f830b7805ee1b51716eb4065e`.
Re-signing those upstream bytes would not establish source or publisher
identity, so PR #101 retained a fail-closed admission checkpoint instead.

The exact upstream source identity reviewed for this decision is:

- release tag `483`, annotated tag object
  `32d4f28e8311ea6f67edca209df59a0493d869fa`;
- source commit `50b0b50b75abd47f830b7805ee1b51716eb4065e` and tree
  `3b5414292a614b12393bb4605ea2d4c588a5b8ee`;
- `mvnw` SHA-256
  `cae96cef89ebea3531221f4ae17c23cf8edf67d00eae8306d4186ae1bbed4d02`;
- `.mvn/wrapper/maven-wrapper.properties` SHA-256
  `488e1b3f2e641779d4636abf9390845f901e64607261bc3c0b0bfe4fe96e6706`;
- root `pom.xml` SHA-256
  `e1ba9a61315097e3a7133238c778ec161ac6097fe77a660fc5455a3e84568820`;
- `core/trino-server/pom.xml` SHA-256
  `663d8bc33313160b26df9c80d4f1e5a3d970700573a914fb22db3462ac0e06d2`.

GitHub reports both the annotated tag object and the source commit as unsigned.
The immutable SHAs above identify exact bytes but do not authenticate the
upstream publisher. They are therefore comparison coordinates, not sufficient
authorization to execute the tree as a build input or publish its outputs.

Upstream requires Java 25.0.1 or newer and recommends
`./mvnw clean install -DskipTests`. The wrapper selects Maven 3.9.16 but does
not declare `distributionSha256Sum`; a trusted workflow therefore cannot allow
the wrapper to download and execute Maven without a separate integrity
boundary. The complete server build also resolves from Maven Central and the
explicit Confluent repository used by the server and connector modules.

The upstream Dockerfile is unsuitable as a repository trust boundary because
it uses mutable `latest` bases. On 2026-07-22 the selected native-arm64
feasibility candidates were:

- builder tag observation `maven:3.9.16-eclipse-temurin-25` and index
  `docker.io/library/maven@sha256:7e461cec477077c1d9e50b13df8aef9018764410f4c4cd7c34803f10c4c99e4c`
  with arm64 manifest
  `sha256:5476bfca9d0a6485b7161f6863123f7e6822336de4177273b47b5ec38ffd573a`;
- runtime tag observation `amazoncorretto:25-alpine3.24` and index
  `docker.io/library/amazoncorretto@sha256:32d81edae73e1670244827c2f12e5bcf0d335f035b538455fe9d02eb0771d41b`
  with arm64 manifest
  `sha256:da20e1e0a2004dfb95e963d6ad978b5c0effdfc7000bce6a68836058ef24b427`.

The builder provides Maven 3.9.16 with Eclipse Temurin 25 on Ubuntu 24.04 and
satisfies Trino's Maven Enforcer vendor requirement. The runtime provides Java
25 on Alpine 3.24.1. Trivy 0.72.0 with vulnerability DB timestamp
`2026-07-22T13:17:28Z` reported High=0/Critical=0 for both candidates. These are
feasibility observations, not retained publication or resident-admission
evidence. The local Docker daemon was unavailable during this decision review,
so native container smoke remains a mandatory publisher gate.

## Decision

- Reject the upstream Trino 483 OCI image and the upstream server tarball as
  resident or repository-build inputs. Keep both as evaluated evidence only.
- Select the unmodified Trino 483 source tree at exact commit
  `50b0b50b75abd47f830b7805ee1b51716eb4065e` only as a conditional build
  candidate. No dependency or image publisher may fetch, execute, build, or
  publish it until a separate evidence-only PR closes the source-authentication
  gate described below. Any source patch requires a new decision record and a
  closed preimage/postimage review.
- Accept source authentication only when retained, independently verified
  evidence binds repository `https://github.com/trinodb/trino`, release tag
  `483`, the exact commit above, and tree
  `3b5414292a614b12393bb4605ea2d4c588a5b8ee` through at least one of: (1) a
  verified upstream signature from a separately approved Trino release identity
  over the exact tag, or over the exact commit plus an authenticated
  release-to-commit binding; (2) a signed upstream source release verified
  against a separately approved Trino release trust root and signer identity,
  whose digest and extracted tree bind to the exact commit and tree; or (3)
  trusted upstream provenance whose subject and source claims bind all four
  coordinates. A self-selected or merely embedded signing key is not a trust
  root. A SHA, HTTPS transport, GitHub account attribution, release page, or
  Shirokuma re-signature alone is insufficient. If no qualifying proof is
  available, Trino remains blocked and the implementation must not proceed.
- Use Maven 3.9.16 with Eclipse Temurin 25 from the exact builder index above.
  After the source-authentication gate closes, the main-only workflow must
  verify the native arm64 child and observed Maven, Java, OS, and architecture
  before resolving dependencies. It must invoke the pinned image's `mvn` binary
  directly; the unchecked wrapper download path is forbidden.
- Limit networked dependency resolution to HTTPS Maven Central
  (`https://repo.maven.apache.org/maven2/`) and the explicit Confluent
  repository (`https://packages.confluent.io/maven/`). Private repositories,
  arbitrary mirrors, proxies, user settings, ambient Maven homes, extensions,
  and credential fallback are forbidden. Every online resolver and offline
  rebuild must use Maven's `--ignore-transitive-repositories` control. The
  repository-owned settings must additionally define exactly one closed mirror
  with selector `*,!central,!confluent` that routes every other repository ID
  to Maven Central. This covers plugin dependency version-range resolution,
  which Maven 3.9.16 does not suppress with
  `--ignore-transitive-repositories`, without expanding the two-endpoint
  network allowlist. The packager may normalize that exact mirror ID to the
  Maven Central origin; any other repository ID or transfer URL fails closed.
- Publish the Maven local repository only as a deterministic, run-scoped OCI
  dependency artifact after a closed manifest records every regular file,
  canonical path, size, mode, SHA-256, repository origin, and total byte count.
  Symlinks, hard links, special files, locks, partial downloads, unknown
  repositories, duplicate paths, mutable tags, and repository-produced
  `io/trino/**` artifacts fail closed. Reactor outputs must be rebuilt from the
  reviewed source and cannot enter the dependency input.
- Require an independent clean verifier to reconstruct the candidate from the
  same allowlisted repositories, compare the complete manifest, then run
  `mvn --offline --ignore-transitive-repositories -Dmaven.repo.local=/workspace/.m2/repository --file /workspace/pom.xml -pl '!:trino-docs' clean install -DskipTests`
  in a fresh network-none native-arm64 builder. The output must be exactly
  `core/trino-server/target/trino-server-483.tar.gz`; its hash, size, and
  reproducible-build comparison become retained evidence. The explicit
  `!:trino-docs` exclusion follows the Trino 483 upstream product-build
  boundary: that reactor module invokes Sphinx to generate documentation and
  contributes no Trino server runtime output. Both fresh dependency resolutions
  and both offline rebuilds must use the same exclusion; all remaining reactor
  modules stay inside the complete server-build and dependency-closure boundary.
- Require the verifier workflow to run on a native linux/arm64 host. It must
  retain `RUNNER_ARCH=ARM64`, host `uname -m=aarch64`, and container
  architecture `arm64` observations, reject QEMU or binfmt emulation, and fail
  closed before the offline rebuild when any observation is absent or differs.
- Bind dependency-snapshot SLSA provenance to the exact Trino source input via
  `predicate.buildDefinition.resolvedDependencies`. Exactly one descriptor must
  identify `git+https://github.com/trinodb/trino@refs/tags/483` and carry the
  reviewed tag-object, commit, and tree digests; the checkout used by the build
  must match that descriptor.
- Bind the SBOM and vulnerability-scan documents and their attestations to the
  immutable dependency-snapshot digest returned by the publisher. Evidence
  review must reject a missing or mismatched document subject or attestation
  subject before admitting the snapshot.
- Follow the two-phase publication lifecycle used by ADR-0020 and ADR-0021.
  Only after source-authentication evidence is reviewed may a reviewed main-only
  publisher create review-pending dependency evidence; a separate evidence-only
  PR must pin and verify it before any Trino image publisher is introduced.
- Build the later runtime image from the reviewed server archive with the exact
  Amazon Corretto 25 Alpine 3.24 runtime base above. Repeat a fresh
  High=0/Critical=0 scan, native arm64 Java/server smoke, non-root and read-only
  hardening checks, CycloneDX SBOM, Cosign/Rekor signature, SLSA provenance,
  and anonymous exact-digest retrieval on the main publication run.
- Do not add a Trino workflow, dependency artifact, Containerfile, resident
  ledger entry, credentials, Flux object, Helm chart, or runtime manifest in
  this decision checkpoint. The next review boundary is source-authentication
  evidence, not the dependency-snapshot contract, packager/verifier, or
  main-only publisher.
- Keep Issue #63 open through dependency review, image publication and review,
  resident admission, Flux reconciliation, and deterministic Polaris/Iceberg
  queries. No checkpoint may infer completion from an earlier boundary.

## Subsequent amendment

ADR-0023 supersedes only the requirement to wait indefinitely for qualifying
upstream authentication before the dependency-snapshot contract can be
reviewed. From `2026-07-22T22:43:36Z` through `2026-08-21T22:43:36Z`, the exact
483 source coordinates may proceed under a time-boxed local-PoC source-identity
risk acceptance. All dependency, offline-build, image, vulnerability,
provenance, admission, and runtime controls in this ADR remain mandatory.

## Consequences

The conditional source-build path avoids laundering an unsigned upstream image
and, if source authentication is later established, makes the complete Maven
dependency graph reviewable. It also introduces substantial Actions time,
registry storage, and review surface; a future publisher PR must measure and
disclose its actual dependency artifact size before merge.

The build is intentionally full rather than a hand-pruned collection of JARs.
This preserves the upstream Trino server distribution and Iceberg plugin while
the later runtime profile controls which catalogs are configured. Reducing the
distribution or replacing dependencies requires a separate reviewed decision.

If either allowed repository, the native arm64 builder, the offline rebuild,
the vulnerability feed, or anonymous registry access is unavailable, the
publication remains blocked. Authenticated pulls, stale scans, a mutable base,
or direct cluster mutation are not fallbacks.

If the source tag, commit, and publisher identity cannot be authenticated under
the accepted evidence policy, no publication phase exists to fall back to. The
exact source coordinates remain useful only as rejected-candidate evidence.

Rollback for this decision-only checkpoint is to revert the focused PR and
restore `decision_record_required: true` in the Trino admission record. No
image, dependency artifact, credential, cluster object, or host persistent data
is created by this ADR.
