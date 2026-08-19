---
doc_id: ADR-0030
title: Activate exact Trino 483 dependency publication sequence 6
status: accepted
created: 2026-08-18
updated: 2026-08-18
version: "1.0.0"
area: architecture
tags: [adr, trino, maven, supply-chain, arm64]
---

# ADR-0030: Activate exact Trino 483 dependency publication sequence 6

## Context

Sequence 5 was consumed by reviewed-main run `31616764771`. Its `validate`
job completed, but the `publish` job failed closed before candidate download or
registry authentication because GitHub returned no pull-request association on
the workflow-run record. PR #149 replaced that brittle association with bounded
stable queries over the merge commit, final head, and head-filtered all-state
pull requests. Its merge did not itself authorize publication.

PR #152 then replaced React Router 7.18.1 plus OpenVEX with React Router 7.18.2
and nanoid 3.3.18. The active candidate now requires one raw Bun Trivy report
with High=0 and Critical=0. PR #152 final head
`cce85c1424691b5157f0881e249a984224eb6875` was merged as
`fdec9cdb170ed63d18735ef9f6d0abacc8e475ab`; its reviewed-main workflows passed
while publication remained disabled.

Issue #63 comment
[`5322657371`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5322657371)
records fresh source, package-signature, provenance, and vulnerability status.
The Trino and bounded Parquet source coordinates remain exact and unsigned;
the patched Web UI postimages remain exact; npm signatures and attestations
verified; and Trivy DB `2026-08-18T01:03:12.592597961Z` returned raw Bun
High=0/Critical=0.

The risk owner approved that proposal exactly and without modification in
Issue #63 comment
[`5324238100`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5324238100)
at `2026-08-18T05:58:59Z`.

## Decision

Authorize focused activation PR #153 from predecessor
`fdec9cdb170ed63d18735ef9f6d0abacc8e475ab` and exactly one reviewed-main Trino
dependency publisher run for sequence 6, GitHub Actions attempt `1` only.

The authorization binds all of the following:

- Trino tag object `32d4f28e8311ea6f67edca209df59a0493d869fa`, commit
  `50b0b50b75abd47f830b7805ee1b51716eb4065e`, and tree
  `3b5414292a614b12393bb4605ea2d4c588a5b8ee`;
- Parquet release tag object `1f54ba44afb285fecbaf54bde5c0afa259327fc4`,
  RC tag object `172d200a7eb81161345bdccaf628af34178fc479`, commit
  `78a8d3230eb4769db93de5f2f2e18363c04cae81`, and tree
  `28b877df95a7a661361b8776f6ebe21d73d8da6d`;
- PR #152 final head and the exact React Router 7.18.2, React Router DOM 7.18.2,
  and nanoid 3.3.18 postimages without OpenVEX; and
- the PR #149 association repair prospectively for sequence 6, without
  retroactively authorizing the PR #149 merge.

The authorization expires at `2026-09-17T02:15:58Z`, cannot renew
automatically, and is limited to `mac-studio-solo/local-lite`, synthetic or PoC
data, and no public Service or Ingress.

Before merge, the activation pull request must pass every required workflow on
its exact final head, have zero current non-outdated unresolved review threads,
and receive the exact top-level OWNER final-head attestation defined by the
trusted contract. That attestation binds the canonical review-thread snapshot
SHA-256 before merge, including resolved and outdated state, and is required
for both approval modes. The standard independent-review path remains valid,
is evaluated first, and must remain current after the final API gates. The
publisher must capture the exact pull binding, final-head CI, review-thread
snapshot, OWNER attestation, and selected independent review twice and require
the complete authorization snapshots to match before returning. A required
workflow run created before the applicable cutoff but updated at or after it
fails closed; a post-attestation rerun cannot authorize publication by falling
back to an older successful run.

The dependency package already exists publicly at the retained immutable
sequence-4 digest `sha256:0394143034298f4c6606c288e8ef97154826978bf3aa97e1e952499f8af5075c`.
Sequence 6 must anonymously fetch that manifest before registry
authentication. It may not rely on a post-write visibility change or rerun.

Any sequence-6 failure consumes the authorization. Manual dispatch, rerun, a
second push attempt, reuse of runs `31605249586` or `31616764771`, and sequence
7 are forbidden without another explicit fresh owner decision.

## Consequences

Merging the activation pull request may enable only the dependency publisher.
The publisher must revalidate the exact merged pull request, final-head
attestation, CI, review threads, source authorization, candidate identity, and
stable PR association before registry authentication and again before write.

Successful publication creates review-pending evidence only. It does not admit
the dependency digest or authorize image publication, resident admission,
Flux/runtime reconciliation, credentials, public exposure, production use,
Polaris/Iceberg query acceptance, dependent Issues #64-#66, or Issue #63
closure. A separate evidence-only review is required before admission.

## References

- [[ADR-0023_Allow_time_boxed_Trino_483_source_identity_exception_for_local_PoC]]
- [[ADR-0024_Apply_bounded_Trino_483_Web_UI_dependency_overlay_and_OpenVEX]]
- [[ADR-0029_Authorize_exact_Trino_483_Maven_build_plugin_remediation]]
- [Issue #63 OWNER approval](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5324238100)
- [PR #153](https://github.com/TommyKammy/Shirokuma/pull/153)
