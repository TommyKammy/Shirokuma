---
project: Shirokuma
doc_id: "RES-107"
title: "Trino 476 Signed Distribution Feasibility"
status: reviewed
created: 2026-07-23
updated: 2026-07-23
version: "0.1"
area: "research"
tags: [shirokuma, trino, arm64, iceberg, supply-chain]
---

# Trino 476 Signed Distribution Feasibility

Evidence cutoff: 2026-07-23 UTC. This review asks whether the last readily
discoverable Trino server distribution with a detached signature can replace
the source-authentication-blocked Trino 483 candidate for Issue #63. It does
not authorize a publisher, resident image, credentials, Flux objects, or a
runtime.

## Decision

Reject Trino 476 as a Shirokuma build or runtime input. The detached signature
is cryptographically valid for the downloaded bytes, but the signing key is not
anchored to a separately approved Trino trust root. More importantly, a fresh
SBOM scan reports two Critical findings and 52 High findings. Critical findings
are never eligible for the ADR-0019 local-lab exception, and the required
Iceberg module contains `CVE-2026-34214`, fixed in Trino 480.

Removing Ranger or replacing the native launcher would not make the candidate
acceptable: the linux/arm64 launcher itself carries one Critical finding, the
Iceberg module remains High, and modifying the contents would leave the signed
distribution boundary. ADR-0022 therefore remains the current decision and its
Trino 483 source-authentication gate remains blocked.

The normalized evidence and fail-closed decision are retained in
`bootstrap/trino/v476/feasibility.json`. Raw downloaded archives, generated
SBOMs, and scanner output are deliberately not committed; this is a feasibility
record, not admission evidence, and any future candidate requires a fresh
retained publication and admission run.

## Source and distribution identity

The GitHub release tag resolves to annotated tag object
`ecb143d60e11131d167b3d3e1d726e053745aa6f`, commit
`7f3746a7fa0b27ace2470340e848feaf3ee73f48`, and tree
`74ac3497643a111798df430355077f3a9a9d6da5`. GitHub reports both the tag and
commit as unsigned.

Maven Central serves `trino-server-476.tar.gz` and its detached `.asc`:

- archive size: 821,045,832 bytes;
- SHA-256:
  `cfd5accde17e8ebd251eeeb78aed1f490e77bb3a164d95a0f454bf8a7c1cbd3f`;
- SHA-512:
  `fe5e5b8cb2d71e8ccaccfc2acb60f88d43686a2a77b239bfd4fda6d2b0a5016e01f3bfad078954928de9b88f76b55023871c2b0efb83b609d9300906813ac7c2`;
- signature SHA-256:
  `e6a17fa6e1d086316b2308dfdf3145bd87fcc836f18c89f74559550e03e497d6`.

PGPy 0.6.0 verifies the RSA/SHA-512 detached signature made at
`2025-06-06T04:05:16Z` with fingerprint
`C328250FE23A2420814521EC0EB69F76FD171538`. The key was obtained from Ubuntu's
keyserver and has SHA-256
`e37a6a94215760b0bfa695eedd12ff70962df737a8aa648643b710c0660850b3`.
This proves only that the supplied key verifies the supplied bytes. The
keyserver is not a Trino-owned trust root, no reviewed upstream policy binds
that fingerprint to release authority, and PGPy warns that it does not validate
self-signatures, revocation, or disabled-key state. No claim of trusted source
authentication follows from this check.

Primary sources: [release](https://github.com/trinodb/trino/releases/tag/476),
[Maven Central distribution](https://repo.maven.apache.org/maven2/io/trino/trino-server/476/),
[repository tree](https://github.com/trinodb/trino/tree/476).

## Archive and ARM64 feasibility

The archive contains one `trino-server-476` root with 6,732 entries: 70
directories, 949 regular files, and 5,713 hard links referencing 454 unique
in-root targets. No absolute path, parent traversal, symbolic link, special
file, or missing hard-link target was observed. A future packager would still
need explicit hard-link validation rather than extracting this archive as an
implicitly trusted input.

The distribution includes native launchers for linux-amd64, linux-arm64, and
linux-ppc64le. The Iceberg plugin contains 451 entries including
`plugin/iceberg/io.trino_trino-iceberg-476.jar`. The project targets JDK 24 and
enforces Java 24.0.1 or newer. Java 25 on the ADR-0022 Alpine 3.24 candidate is
therefore syntactically above the minimum, but no native container smoke was
run because the local Docker/Colima runtime was unavailable. This missing smoke
does not change the rejection, which already fails the stricter vulnerability
gate.

## SBOM and vulnerability result

Syft 1.46.0 generated CycloneDX 1.7 with 13,484 components: 6,587 files and
6,897 libraries. The high count reflects the upstream distribution's hard-link
layout. Trivy 0.72.0 scanned that SBOM using database timestamp
`2026-07-22T13:17:28.096603393Z` and reported:

| Severity | Findings |
|---|---:|
| Critical | 2 |
| High | 52 |
| Medium | 55 |
| Low | 9 |
| Unknown | 2 |

The decisive findings are:

| ID | Component | Installed | Fixed | Why blocking |
|---|---|---|---|---|
| `CVE-2025-68121` | Go `stdlib` in bundled native launchers | `1.24.2` | `1.24.13`, `1.25.7`, or `1.26.0-rc.3` | Critical; includes linux-arm64 launcher |
| `CVE-2025-59059` | `org.apache.ranger:ranger-plugins-common` | `2.6.0` | `2.8.0` | Critical code-injection finding |
| `CVE-2026-34214` | `io.trino:trino-iceberg` | `476` | `480` | High credential-exposure finding in the required Iceberg path |

An initial `trivy fs` pass did not identify the embedded JAR dependency graph;
that empty result was rejected. The recorded counts come from Trivy's scan of
the Syft-generated CycloneDX document, which identified 6,852 Maven components.

## Consequence and next boundary

Issue #63 remains open. No 476 publisher, image, resident-ledger entry, or
runtime path may be added. A future candidate must be Trino 480 or newer because
of the Iceberg fix, must authenticate the exact upstream source or distribution,
and must pass fresh High=0/Critical=0, linux/arm64 smoke, SBOM, provenance,
signature, and anonymous exact-digest retrieval gates. Selecting or patching a
different release requires a separate reviewed decision; it must not be inferred
from this rejection record.
