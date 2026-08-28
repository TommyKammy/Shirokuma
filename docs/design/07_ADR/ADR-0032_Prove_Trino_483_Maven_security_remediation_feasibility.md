---
doc_id: ADR-0032
title: Prove Trino 483 Maven security remediation feasibility
status: proposed
created: 2026-08-26
updated: 2026-08-26
version: "0.1.0"
area: architecture
tags: [adr, trino, maven, supply-chain, arm64]
---

# ADR-0032: Prove Trino 483 Maven security remediation feasibility

## Context

ADR-0031 records that reviewed-main sequence 7 was consumed by run
`32786095668`, attempt `1`, and failed closed with Critical=0 and High=3. The
findings were Netty SCTP 4.2.16.Final (`CVE-2026-59902`) and the HttpCore and
HttpCore H2 5.3.6 packages shaded in docker-java transport zerodep 3.7.1
(`CVE-2026-54399` and `CVE-2026-54428`). No dependency artifact was published.

Issue #63 comment
[`5418664130`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5418664130)
defines a review-only focused remediation candidate from exact predecessor
`59f38dc26a1a02203df9c629360d863e4856a2ba`. The OWNER subsequently requested
creation of this Draft PR. That request authorizes only the focused feasibility
PR; it does not authorize merge, sequence 8, publication, admission, image or
runtime activation, registry access, or Issue #63 closure.

The Trino source remains tag object
`32d4f28e8311ea6f67edca209df59a0493d869fa`, commit
`50b0b50b75abd47f830b7805ee1b51716eb4065e`, and tree
`3b5414292a614b12393bb4605ea2d4c588a5b8ee`. The docker-java 3.7.1 source is
commit `7b7fabd4567573e4957e549365dc0df8c2e54ab9` and tree
`f6119a3ff6da4b1df34a1054000b849c70f4aae6`; its commit has a valid SSH
signature.

## Proposed decision

Add one PR-only, read-only Linux/arm64 workflow to evaluate exactly these two
source changes:

- change Trino's `dep.netty.version` from 4.2.16.Final to 4.2.17.Final; and
- change docker-java transport-httpclient5's HttpClient dependency from 5.5.1
  to 5.6.4, which embeds HttpClient 5.6.4 and HttpCore/H2 5.4.3 in the rebuilt
  transport zerodep JAR.

The workflow reconstructs docker-java from two independent fresh source
repositories using the exact Linux/arm64 Maven 3.9.12 and Temurin 8 builder
digest. Upstream raw JAR output is not byte reproducible because ZIP entry
timestamps differ, so a reviewed canonicalization step rejects unsafe entries,
sorts names, fixes metadata and timestamps, and preserves entry bytes. Two
locally reconstructed raw JARs had different SHA-256 values
`9462b6823e126c1e1427bb3ef83463ba181fd1336403eefcb512d861faf77f33` and
`dd3391c0ee6ac2ad424231bcd73f8ffbcedc3bab19b9db9a71ade0a53517349a`;
both canonicalized to byte-identical SHA-256
`6898a76926caa2c875d2963ac9e225f2566270a4a0152f8a151785cdaf8769b0`,
size `2,446,145` bytes.

For each closed Maven repository, the workflow first verifies the exact Maven
Central zerodep JAR preimage SHA-256
`b89bdb1754160323597f9ea32a7fe7a4a3aa8f5b3b43b88e8d71fff3b267ab21`,
size `2,304,500` bytes, then replaces only that JAR with the independently
source-built canonical candidate and records a source-remediation receipt. The
two complete repositories must have byte-identical manifests. Two separate
repositories then drive two native Linux/arm64, `--network none` Trino builds;
their server distributions must be byte-identical.

The final fresh Trivy 0.72 scan must report High=0 and Critical=0 over a
closure-complete CycloneDX SBOM derived from the complete retained Maven JAR
descriptor. Raw rootfs discovery is not treated as complete: the existing
publisher generator validates every omission against the descriptor and the
final verifier requires the exact `(PURL, FilePath)` set in the Trivy report.
The workflow checks out the exact PR head SHA and verifies that its linear
history starts at reviewed predecessor
`59f38dc26a1a02203df9c629360d863e4856a2ba`. The PR's exact final-head CI is
the review evidence.

## Consequences

The new workflow grants only `contents: read`, runs only for pull requests,
does not upload retained artifacts, and contains no registry authentication,
registry write, signing, publication, admission, or runtime action. Existing
trusted-build-contract lifecycle values and the blocked publisher remain
unchanged.

This ADR remains `proposed`. A passing Draft PR establishes remediation
feasibility, not production readiness or publication authority. Merge requires
separate approval after exact final-head CI and current review state are known.
Any sequence-8 authorization requires a later, exact OWNER decision and a
separate focused activation PR.

## References

- [[ADR-0031_Activate_exact_Trino_483_dependency_publication_sequence_7]]
- [Issue #63 remediation proposal](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5418664130)
- [sequence 7 run](https://github.com/TommyKammy/Shirokuma/actions/runs/32786095668)
- [sequence 7 diagnostic receipt](../evidence/trino/run-32786095668-maven-vulnerability-diagnostic-receipt.json)
