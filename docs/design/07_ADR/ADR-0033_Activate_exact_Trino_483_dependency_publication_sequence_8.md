---
doc_id: ADR-0033
title: Activate exact Trino 483 dependency publication sequence 8
status: accepted
created: 2026-08-30
updated: 2026-08-30
version: "1.0.0"
area: architecture
tags: [adr, trino, maven, supply-chain, arm64]
---

# ADR-0033: Activate exact Trino 483 dependency publication sequence 8

## Context

PR #155 merged as `688e8e8cfcd9653a74dc93bceee2100c5acf18cb` and
reviewed-main run `32786095668`, attempt `1`, consumed sequence 7. Validation
reached the closure-complete Maven inventory and failed closed on three raw
High findings before candidate retention, registry authentication, or registry
write. PR #156 retained the exact failure evidence and merged as
`59f38dc26a1a02203df9c629360d863e4856a2ba`, leaving publication blocked.

PR #157 proved a focused remediation on final head
`6da785983a9ee460c71f35ca1d5bac35cec4184b`, merged as exact predecessor
`1f4e2ce0b958f69c91780857b11695ac47d1e00a`. Exact-head feasibility run
[`33026390700`](https://github.com/TommyKammy/Shirokuma/actions/runs/33026390700)
completed two independent native Linux/arm64 docker-java reconstructions and
two closed-repository Trino builds, generated a closure-complete Maven SBOM,
and required raw High=0/Critical=0 without OpenVEX or a waiver.

The remediation binds Trino tag object
`32d4f28e8311ea6f67edca209df59a0493d869fa`, commit
`50b0b50b75abd47f830b7805ee1b51716eb4065e`, and tree
`3b5414292a614b12393bb4605ea2d4c588a5b8ee`. The exact Trino `pom.xml`
postimage is SHA-256
`3d0c79d798c68632a23e94abb899b760485e199f4ead530bcc27c52a2f2854d3`,
with Netty 4.2.17.Final and exact `netty-transport-sctp` JAR SHA-256
`5345dabce2cbf10bc9c6a80a8d7beeaa4cd975028aa04f1903a96b8e440529f6`.

The docker-java source binding is tag and commit
`7b7fabd4567573e4957e549365dc0df8c2e54ab9`, tree
`f6119a3ff6da4b1df34a1054000b849c70f4aae6`. The independently reproduced,
canonical `docker-java-transport-zerodep` candidate is SHA-256
`6898a76926caa2c875d2963ac9e225f2566270a4a0152f8a151785cdaf8769b0`,
size `2,446,145` bytes, with a path-scoped source receipt.

Issue #63 comment
[`5469094658`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5469094658)
records the fresh proposal. The OWNER approved it exactly and without
modification in comment
[`5469184039`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5469184039).

## Decision

Authorize focused activation PR #158 from exact predecessor
`1f4e2ce0b958f69c91780857b11695ac47d1e00a`. The PR may integrate only the
exact PR #157 remediation into the existing Trino 483 dependency publisher,
closed contracts, verifier, tests, trusted-file inventories, and status
documentation. Unrelated source coordinates, reactor scope, Bun identity,
Parquet remediation, scan thresholds, admission policy, runtime manifests,
and workloads remain unchanged.

PR creation does not authorize merge. Before merge, PR #158 must pass every
required workflow on its exact final head, have zero current non-outdated
unresolved review threads, and receive a separate explicit OWNER approval and
final-head attestation containing the exact review-thread snapshot SHA-256.

After an explicitly approved merge, authorize exactly one automatic
reviewed-main execution of `.github/workflows/trino-maven-dependencies.yml`,
GitHub Actions attempt `1`, for the resulting exact merge commit. Manual
dispatch, rerun, a second attempt, another sequence-8 run, and substitution of
a different SHA are forbidden.

The publisher must retain two independent source fetches and native
Linux/arm64 reconstructions, empty separate repositories, byte-identical
canonical docker-java JARs, closed Maven repository manifests, two
network-none Trino builds, closure-complete SBOMs, and raw High=0/Critical=0
reports without OpenVEX or a waiver. It must revalidate the exact merged PR,
final-head attestation, current-head CI, review-thread snapshot, source
authorizations, candidate identities, and unexpired evidence immediately
before registry authentication and again immediately before registry write.

This authorization expires at `2026-09-05T04:48:07Z` and cannot renew
automatically. Any use at or after that instant fails closed before source
execution, dependency resolution, registry authentication, or publication.
Any activation or publisher failure consumes sequence 8, returns publication
permissions to false, and requires a focused closeout/publication-reblock PR.

## Consequences

Successful dependency publication remains review-pending evidence only. A
separate evidence-only review must bind the exact digest, signature,
provenance, SBOM, raw High=0/Critical=0 reports, anonymous exact-digest pull,
and retained run identity before admission.

This decision does not authorize dependency admission, Trino image
publication, resident-image admission, Flux/runtime reconciliation,
credentials, public Service or Ingress, production use, Polaris/Iceberg query
acceptance, dependent Issues #64-#66, or Issue #63 closure.

## References

- [[ADR-0031_Activate_exact_Trino_483_dependency_publication_sequence_7]]
- [[ADR-0032_Prove_Trino_483_Maven_security_remediation_feasibility]]
- [Issue #63 sequence-8 proposal](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5469094658)
- [Issue #63 OWNER approval](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5469184039)
- [PR #157](https://github.com/TommyKammy/Shirokuma/pull/157)
- [PR #157 feasibility run](https://github.com/TommyKammy/Shirokuma/actions/runs/33026390700)
