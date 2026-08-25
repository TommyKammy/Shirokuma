---
doc_id: ADR-0031
title: Activate exact Trino 483 dependency publication sequence 7
status: accepted
created: 2026-08-24
updated: 2026-08-25
version: "1.1.0"
area: architecture
tags: [adr, trino, maven, supply-chain, arm64]
---

# ADR-0031: Activate exact Trino 483 dependency publication sequence 7

## Context

PR #153 merged as `e634f66b4df9ad2086b32c5cf29c4da416108248` and
reviewed-main run `32315191436`, attempt `1`, consumed sequence 6. The Maven
build succeeded, but validation failed closed with `BUN_SNAPSHOT_IDENTITY`
because the reviewed sequence-4 Bun manifest did not match PR #152's revised
lockfile. Publish was skipped, no artifact was retained, and registry
authentication and write were not reached. PR #154 recorded that outcome and
merged as exact predecessor `b2177ef4b1c6e55f225649911d2ed1bc09cd3a0b`.

Issue #63 comment
[`5388964891`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5388964891)
records fresh source and candidate status through `2026-08-23T23:00:43Z`.
Trino 483 and the bounded Parquet source coordinates remain exact and unsigned.
The exact reviewed Web UI patch applies cleanly and retains React Router 7.18.2,
React Router DOM 7.18.2, and nanoid 3.3.18 without OpenVEX. Trivy DB
`2026-08-23T19:09:39.035426505Z` produced raw High=0/Critical=0 with report
SHA-256 `db01be8d2373905c4403e48e172c36c6f6ffc4d14d95818a9d9237186513b3d8`.

Two independent Linux/arm64 Bun cache reconstructions produced byte-identical
descriptors and archives:

- manifest SHA-256 `365ca0ac2b081f7be2ef531ddbba94e542a1b6bfce2bad0302a6d046ff69d647`,
  size `17,312,740` bytes; and
- archive SHA-256 `8a1d9246c6201f55b8ae10791bba3513351b3de933f8bd89b49d95db3e908cf1`,
  size `128,431,592` bytes.

The OWNER approved the proposal exactly and without modification in Issue #63
comment
[`5389044844`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5389044844)
at `2026-08-23T23:20:10Z`.

## Decision

Authorize focused activation PR #155 from predecessor
`b2177ef4b1c6e55f225649911d2ed1bc09cd3a0b` and exactly one reviewed-main
Trino dependency publisher run for sequence 7, GitHub Actions attempt `1` only.

The authorization binds the exact Trino and Parquet source coordinates already
recorded by the trusted contract; PR #152 final head
`cce85c1424691b5157f0881e249a984224eb6875` merged as
`fdec9cdb170ed63d18735ef9f6d0abacc8e475ab`; PR #154 final head
`f0124af3b9a391f7f5126463576ebd6713fba3db`; all four patched Web UI
postimages; and the exact Bun manifest and archive identities above.

The authorization expires at `2026-09-22T23:00:43Z`, cannot renew
automatically, and is limited to `mac-studio-solo/local-lite`, synthetic or PoC
data, and no public Service or Ingress. The PR #149 association repair is
permitted prospectively for this run only and is not retroactively authorized.

Before merge, PR #155 must pass every required workflow on its exact final
head, have zero current non-outdated unresolved review threads, and receive the
exact top-level OWNER final-head attestation defined by the trusted contract.
The publisher must revalidate the merged PR, final-head CI, review-thread
snapshot, OWNER attestation, source authorization, candidate identity, Bun
snapshot identity, and stable PR association before registry authentication and
again immediately before write.

Manual dispatch, workflow rerun, a second push attempt, reuse of run
`32315191436`, another sequence-6 attempt, and sequence 8 are forbidden. Any
sequence-7 failure consumes this authorization and returns publication
permissions to false.

## Consequences

PR #155 merged as `688e8e8cfcd9653a74dc93bceee2100c5acf18cb` after the
OWNER attested final head `aaf83e8433a2e306f80ab8c707c5804412096396` and
review-thread snapshot
`500ea2b7c199352b23f57363b7cf676c3d7155b657af1718e9c1c058659ace0d`.
Reviewed-main run `32786095668`, attempt `1`, consumed sequence 7. Validation
reached the closure-complete Maven inventory and failed closed at `Verify and
block the complete Maven JAR scan inventory` with `MAVEN_SCAN_FINDING`.

The raw scan reported Critical=0 and three High findings:

- `CVE-2026-59902` in `io.netty:netty-transport-sctp` 4.2.16.Final;
- `CVE-2026-54399` in `org.apache.httpcomponents.core5:httpcore5` 5.3.6; and
- `CVE-2026-54428` in `org.apache.httpcomponents.core5:httpcore5-h2` 5.3.6.

Publish was skipped. Candidate recording, registry authentication, registry
write, dependency artifact publication, and final publication evidence were
not reached. Diagnostic artifact `9542062490`, digest
`sha256:bbb58d79f653c04e4d37739bd9cfc73426edaa4cbc78c5b3a390b1524759c369`,
is retained in repository evidence through an exact receipt and four hash-bound
source files before its GitHub expiry.

Publication permissions return to false. Rerun, a second sequence-7 attempt,
and sequence 8 are forbidden. Fresh Maven remediation feasibility evidence and
a new explicit OWNER decision are required before any later activation. No
dependency digest is admitted, and image publication, resident admission,
Flux/runtime reconciliation, credentials, public exposure, production use,
Polaris/Iceberg query acceptance, dependent Issues #64-#66, and Issue #63
closure remain unauthorized.

## References

- [[ADR-0023_Allow_time_boxed_Trino_483_source_identity_exception_for_local_PoC]]
- [[ADR-0024_Apply_bounded_Trino_483_Web_UI_dependency_overlay_and_OpenVEX]]
- [[ADR-0030_Activate_exact_Trino_483_dependency_publication_sequence_6]]
- [Issue #63 sequence-7 proposal](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5388964891)
- [Issue #63 OWNER approval](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5389044844)
- [PR #155](https://github.com/TommyKammy/Shirokuma/pull/155)
- [sequence 7 run](https://github.com/TommyKammy/Shirokuma/actions/runs/32786095668)
- [sequence 7 diagnostic receipt](../evidence/trino/run-32786095668-maven-vulnerability-diagnostic-receipt.json)
