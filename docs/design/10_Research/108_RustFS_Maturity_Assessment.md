---
project: Shirokuma
doc_id: "RES-108"
title: "RustFS Maturity Assessment"
status: reviewed
created: 2026-07-05
updated: 2026-07-16
version: "0.3"
area: "research"
tags: [shirokuma, rustfs, object-storage, security]
---

# RustFS Maturity Assessment

Evidence cutoff: 2026-07-16 UTC. This is a non-mutating desk review of public
primary sources. Upstream claims are recorded as claims, not as Shirokuma test
evidence.

## License

The main repository declares SPDX `Apache-2.0` and contains the standard Apache
License 2.0 text. This is a permissive code-license baseline, subject to its
notice and attribution terms. It does not prove the license status of every
dependency or any separate hosted/commercial offering.

Primary sources: [repository license](https://github.com/rustfs/rustfs/blob/main/LICENSE),
[repository metadata](https://api.github.com/repos/rustfs/rustfs).

## Governance

The repository has a Code of Conduct and contribution guide and requires web
commit signoff. Its default branch is active and the repository is not archived.
However, the 2026-07-16 tree exposes no `GOVERNANCE`, `MAINTAINERS`, or
`CODEOWNERS` file. Organization ownership plus contribution volume therefore
does not establish a documented succession, release-authority, or security
decision model. Governance maturity remains an explicit unknown.

Primary sources: [contribution guide](https://github.com/rustfs/rustfs/blob/main/CONTRIBUTING.md),
[Code of Conduct](https://github.com/rustfs/rustfs/blob/main/CODE_OF_CONDUCT.md),
[repository tree](https://github.com/rustfs/rustfs/tree/main).

## Release cadence

The project is highly active but has no non-prerelease 1.0 release. GitHub lists
beta.1 on 2026-04-29, beta.2 through beta.8 from May to June, beta.9 on
2026-07-15, and beta.10 preview releases on 2026-07-16. This demonstrates a fast
cadence, but every observed release is marked prerelease and beta.9 is not an
immutable GitHub release. A 35-day beta.8-to-beta.9 interval and same-day preview
turnover make a weekly-release claim unsuitable as a promotion guarantee.

Primary sources: [releases](https://github.com/rustfs/rustfs/releases),
[beta.9](https://github.com/rustfs/rustfs/releases/tag/1.0.0-beta.9).

## Maintainers

The GitHub contributors API first page contained 100 contributors and 4,504
contributions at the cutoff. The top three accounts were `overtrue` (1,465),
`houseme` (879), and `weisd` (715), about 68% of that page's contributions.
They are activity evidence, not an authoritative maintainer roster. Because no
formal roster or governance file binds responsibility, Shirokuma must not infer
maintainer authority from contribution ranking.

Primary sources: [contributors](https://api.github.com/repos/rustfs/rustfs/contributors?per_page=100),
[organization](https://github.com/rustfs).

## Issue / PR activity

GitHub search reported 344 issues created, 1,941 pull requests created, and
1,882 pull requests merged during the 90-day window beginning 2026-04-17.
The repository also showed 63 open issue/PR records and a push on 2026-07-16.
This is strong activity evidence, but volume alone does not establish review
depth, stable interfaces, or operational maturity.

Primary sources: [90-day issues](https://github.com/rustfs/rustfs/issues?q=is%3Aissue%20created%3A%3E%3D2026-04-17),
[90-day pull requests](https://github.com/rustfs/rustfs/pulls?q=is%3Apr%20created%3A%3E%3D2026-04-17),
[90-day merged pull requests](https://github.com/rustfs/rustfs/pulls?q=is%3Apr%20merged%3A%3E%3D2026-04-17),
[commits](https://github.com/rustfs/rustfs/commits/main).

## Security policy

The policy provides private GitHub advisory reporting and
`security@rustfs.com`, promises acknowledgment within 48 hours, and documents an
assessment/fix/disclosure process. Its support table says "Latest" is supported
while `< 1.0` is unsupported, even though all observed releases are `< 1.0`
prereleases. This ambiguity must fail closed rather than be interpreted as a
support commitment for beta.9.

Primary source: [security policy](https://github.com/rustfs/rustfs/security/policy).

## Known vulnerabilities

The repository advisory API returned 26 published advisories at the cutoff:
Critical=4, High=9, Medium=9, and Low=4. Recent examples include
[CVE-2026-62378](https://github.com/rustfs/rustfs/security/advisories/GHSA-7gcx-wg4x-q9x6)
(Critical stored XSS/account takeover),
[CVE-2026-55188](https://github.com/rustfs/rustfs/security/advisories/GHSA-796f-j7xp-hwf4)
(High authorization bypass/credential disclosure), and
[CVE-2026-55189](https://github.com/rustfs/rustfs/security/advisories/GHSA-3g29-xff2-92vp)
(High FTP authorization bypass). Those three and other recent findings identify
beta.9 as the patched line.

This means beta.9 is not known-vulnerable to those disclosed ranges; it does
not mean beta.9 is vulnerability-free. No retained Shirokuma image SBOM, trusted
signature verification, timezone-qualified database scan, or High=0/Critical=0
result exists. Promotion therefore remains blocked.

Primary source: [published repository advisories](https://github.com/rustfs/rustfs/security/advisories).

## linux/arm64

The beta.9 GitHub release publishes versioned GNU and musl
`rustfs-linux-aarch64` ZIP assets, checksums, an SBOM, and a provenance JSON.
A non-pulling registry inspection on 2026-07-16 resolved
`rustfs/rustfs:1.0.0-beta.9` to index
`sha256:f75d0bca6ca322c4e59f7125f73dd9ab709b22f71396a42c95bce7f74c99e53b`
with linux/arm64 child
`sha256:4096ed795289d07b8a81711cf30cf3645230e932154f50c7946015bb85ab129a`.
The index also contained two `unknown/unknown` attachment manifests; their
presence is not trusted signer verification.

This proves a native build and image path exists, not that it is admissible.
The release is mutable/prerelease, signer identity has not been authenticated,
and the image was neither pulled nor run.

Primary sources: [beta.9 assets](https://github.com/rustfs/rustfs/releases/tag/1.0.0-beta.9),
[Linux installation](https://docs.rustfs.com/installation/linux/),
[container tags](https://hub.docker.com/r/rustfs/rustfs/tags).

## S3 compatibility

Upstream documents Signature V4, policy-based authorization, versioning,
multipart and common S3 operations, and describes read-after-write consistency.
The repository feature table marks S3 core features available. These are useful
claims, but no dated conformance result or Shirokuma client transcript was found
in the reviewed primary sources. The same feature table marks lifecycle
management and distributed mode under testing, which are relevant to cleanup,
recovery, and future scale behavior.

Risks to test at the real boundary include path-style endpoint behavior,
SigV4 canonicalization, multipart completion/abort, conditional requests,
range reads, pagination, version/delete-marker semantics, bucket policy denial,
TLS trust, and consistency after overwrite/delete. Raw compatibility claims do
not substitute for the Polaris/Trino/Spark request paths Shirokuma will use.

Primary sources: [S3 compatibility](https://docs.rustfs.com/features/s3-compatibility/),
[versioning](https://docs.rustfs.com/features/versioning/),
[architecture](https://docs.rustfs.com/concepts/architecture),
[feature status](https://github.com/rustfs/rustfs#feature--status).

## Iceberg suitability

Upstream says RustFS supports Iceberg and S3-compatible query engines, but that
is a product claim rather than an Iceberg REST Catalog or engine-specific
interoperability result. Object storage does not itself supply authoritative
Iceberg catalog semantics. Shirokuma would still rely on Polaris and must prove
that its file I/O, credential vending, warehouse URI, and cleanup paths behave
correctly against RustFS.

An eventual L5 test must cover create/write/read, multipart data files, metadata
commit, snapshot refresh, overwrite/delete, namespace isolation, failure
cleanup, backup/export, and a clean durable state after rejected or failed
operations. No such smoke is performed by Issue #34.

Primary sources: [RustFS data lake claims](https://docs.rustfs.com/features/data-lake/),
[Apache Iceberg S3 documentation](https://iceberg.apache.org/docs/latest/aws/#s3-fileio),
[Polaris storage configuration](https://polaris.apache.org/in-dev/configuring-storage/).

## Recommendation

Recommendation: **remain experimental.** Do not promote RustFS, replace SeaweedFS, admit an
image, or proceed to the L5 smoke solely from this review. The project is active
and technically interesting, but prerelease-only delivery, ambiguous supported
versions, recent Critical/High disclosures, absent formal maintainer governance,
and unverified S3/Iceberg behavior are material blockers.

## Owner

The future `WP-L5-RUSTFS-001` owner owns re-evaluation. The Shirokuma security
owner must independently approve image provenance and vulnerability evidence;
the lakehouse owner must approve the interoperability test plan. Neither role
may infer approval from this desk review.

## Follow-up criteria

Open an L5 smoke only after all of the following are simultaneously true:

1. an explicit supported release is non-prerelease, or upstream resolves the
   support-policy ambiguity and a defined observation window passes;
2. the exact tag is re-resolved to immutable index and linux/arm64 digests;
3. trusted signer identity, provenance, SBOM, and a timezone-qualified scan with
   High=0/Critical=0 are retained under Shirokuma's admission contract;
4. no unmitigated Critical or High advisory affects the selected version;
5. a bounded S3/Iceberg plan covers the real Polaris and engine boundaries,
   failure cleanup, backup/export, SSD impact, and rollback; and
6. the smoke remains non-resident and cannot mutate the mainline object-store
   profile unless a later reviewed ADR and Work Package explicitly authorize it.
