---
project: Shirokuma
doc_id: "ADR-0024"
title: "Apply a bounded Trino 483 Web UI dependency overlay and OpenVEX"
status: accepted
created: 2026-07-26
updated: 2026-07-26
version: "0.1"
area: "architecture"
tags: [shirokuma, adr, trino, web-ui, supply-chain, openvex]
---

# ADR-0024: Apply a bounded Trino 483 Web UI dependency overlay and OpenVEX

## Context

ADR-0022 selected the exact Trino 483 source tree, and ADR-0023 temporarily
accepted only its missing upstream publisher authentication for the bounded
local PoC. Reviewed-main dependency publisher run `30184349867` subsequently
completed two independent Maven/Bun reconstructions, two native-arm64
network-none full builds, byte-identical server archives, and the Maven
High=0/Critical=0 gate. It then failed closed at the Bun lockfile scan.

Trivy 0.72.0 reported five newly disclosed High findings:

- `d3-color 1.4.1`, fixed in `3.1.0`;
- `fast-uri 3.1.3`, fixed in `3.1.4`;
- `brace-expansion 1.1.16`, fixed in `5.0.8`;
- `postcss 8.5.16`, fixed in `8.5.18`; and
- `react-router 7.18.1`, GHSA-qwww-vcr4-c8h2, fixed in `8.3.0`.

React Router versions below 7.12.0 retain other High findings. The fixed 8.3.0
tag and GitHub release exist, but the npm package is not published as of this
decision. Independently packaging that release would introduce another
unauthenticated source and a larger downstream distribution boundary.

The upstream advisory states that GHSA-qwww-vcr4-c8h2 affects only unstable
React Server Components APIs. The exact Trino 483 Web UI imports only
client-side `HashRouter`, `Routes`, `Route`, `Navigate`, `Link`, location,
parameter, and search-parameter APIs. A detached feasibility build applied the
four published fixes, preserved React Router 7.18.1, passed current Web UI
typecheck/Vite packaging and legacy webpack packaging, retained exactly the
React Router finding in the raw scan, and produced High=0/Critical=0 after an
exact-PURL OpenVEX `not_affected` statement.

Two subsequent independent clean native-arm64 cache reconstructions produced
the same manifest SHA-256
`6e7be3a404014f6f7ac7e4bc326c8d46f7d5822fcea1ac000219c17f1d23f421`
and the same 128,423,777-byte archive SHA-256
`252eade2183bdf5a371f073752420c3a45f5ef8b1dacb08a4addea350389e3c2`.

The authoritative owner approval is Issue #63 comment
`https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5081842992`.

## Decision

- Permit one repository-owned patch only after the pristine Trino 483 commit,
  tree, tag binding, clean worktree, source preimages, and repository allowlist
  pass the existing audit.
- Limit the patch to these four paths:
  `core/trino-web-ui/src/main/resources/webapp/package.json`,
  `core/trino-web-ui/src/main/resources/webapp/bun.lock`,
  `core/trino-web-ui/src/main/resources/webapp-legacy/src/package.json`, and
  `core/trino-web-ui/src/main/resources/webapp-legacy/src/bun.lock`.
  Bind the exact patch bytes and every preimage/postimage SHA-256. Reject any
  other changed or untracked path.
- Override only `d3-color 3.1.0`, `fast-uri 3.1.4`,
  `brace-expansion 5.0.8`, and `postcss 8.5.18`. Pin
  `react-router-dom` exactly to `7.18.1` so a future lock refresh cannot select
  another React Router surface implicitly.
- Retain an unadjusted High/Critical-scoped Trivy JSON report. It must contain
  exactly one High and no Critical findings: GHSA-qwww-vcr4-c8h2 for exact
  `pkg:npm/react-router@7.18.1`. Any other vulnerability, package, version,
  PURL, severity, target, or duplicate finding fails closed.
- Apply one hash-bound OpenVEX statement with status `not_affected` and
  justification `vulnerable_code_not_in_execute_path`. It may identify only
  GHSA-qwww-vcr4-c8h2 and `pkg:npm/react-router@7.18.1`.
- Bind the VEX to the exact Trino source tree, overlay, postimage lock hashes,
  and a closed inventory of every `react-router` or `react-router-dom` import.
  Any import inventory change, any unstable RSC marker, or any product/advisory
  drift fails closed before dependency resolution or publication.
- Retain a second VEX-adjusted Trivy report. It must analyze the same two exact
  Bun lockfiles and identical package inventory as the raw report, with
  High=0/Critical=0. The raw report, adjusted report, OpenVEX document, and
  their hashes remain separate evidence.
- Require current Web UI typecheck/Vite packaging and legacy webpack packaging
  as pre-merge validation. Require native server Web UI and client-side route
  smoke before the later image is admitted. Cross-major transitive overrides
  are not considered compatible solely because dependency installation
  succeeds.
- Expire this decision no later than `2026-08-21T22:43:36Z`, together with the
  ADR-0023 authorization. Automatic renewal is forbidden. A fresh decision is
  required for another Trino source tree, lockfile, React Router version,
  advisory, VEX status, or import surface.
- Preserve owner/reviewer separation. `TommyKammy` owns this decision; `Codex`
  authors the implementation; a different reviewer must approve before merge.
- Do not classify this as an ADR-0019 vulnerability exception. The raw finding
  is retained, and the adjusted High=0 result is justified by reviewed
  non-applicability rather than accepted exploitability risk. No other High or
  Critical finding is waived.

This decision supersedes only ADR-0022's unmodified-source requirement for the
four hash-bound Web UI files and refines ADR-0023's High=0 requirement with one
closed non-applicability statement. Every other ADR-0022 and ADR-0023 control
remains in force.

## Consequences

The resulting server is a disclosed Shirokuma downstream build of Trino 483,
not a byte-equivalent upstream build. The overlay and VEX increase policy and
review surface, but avoid importing an unpublished independently packaged React
Router release. A React Router import change or advisory change stops
publication until a new review is complete.

Issue #63 remains open. This ADR does not publish a dependency artifact, admit
an image, create Flux resources, activate a runtime, or authorize production or
public exposure.

## Verification

- `python3 -m unittest -v tests.test_trino_dependency_publisher`
- `python3 -m unittest -v tests.test_trino_bun_dependencies`
- `make verify-trino-bootstrap`
- `make verify-security`
- `make verify`

The first reviewed-main publisher run must retain both raw and VEX-adjusted
reports and complete the existing reconstruction, reproducibility, signing,
provenance, publication, and anonymous-pull gates before evidence-only review.

## Rollback

Revert the focused pull request. The publisher then returns to the prior
fail-closed state with no dependency artifact or runtime admitted. Do not remove
the raw finding, broaden the VEX, weaken severity filters, or substitute an
ignore file as rollback.

## Related

- [[ADR-0022_Adopt_Trino_483_repository_source_build]]
- [[ADR-0023_Allow_time_boxed_Trino_483_source_identity_exception_for_local_PoC]]
- [[../04_Development/049_Supply_Chain_Security]]
- Issue #63
