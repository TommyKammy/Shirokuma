---
project: Shirokuma
doc_id: "ADR-0024"
title: "Apply a bounded Trino 483 Web UI dependency overlay and OpenVEX"
status: accepted
created: 2026-07-26
updated: 2026-08-12
version: "0.3"
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
- `react-router 7.18.1`, GHSA-qwww-vcr4-c8h2, historically reported as fixed
  in `8.3.0` and currently reported as fixed in `7.18.2, 8.3.0`.

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

The third reviewed-main attempt, run `31590750849`, later proved that the
original overlay no longer met the fail-closed adjusted-scan contract. Trivy
0.72.0 exited `1` after OpenVEX processing because five unsuppressed High
findings remained:

- `fast-uri 3.1.4`, CVE-2026-18446;
- `brace-expansion 5.0.8`, CVE-2026-69152;
- `js-yaml 4.3.0`, GHSA-5p4m-2wfm-xmqj; and
- `nanoid 3.3.15`, CVE-2026-67213 and CVE-2026-67214.

The existing OpenVEX statement suppressed only
`pkg:npm/react-router@7.18.1` / GHSA-qwww-vcr4-c8h2, as intended. Reproduction
with the same Trivy release and vulnerability database produced six raw High
findings and five adjusted High findings. The repeated
`rolldown-vite@7.3.1` invalid-semantic-version warnings occurred identically in
both scans and were non-fatal; they are not the cause of the adjusted exit.
Because the blocking command preceded the normal upload, the failed run
retained no artifact. The repair therefore updates the same four authorized
files and makes the adjusted scan report-only before an explicit fail-closed
verification step, so a validation failure can retain only its exact diagnostic
SBOM, raw report, adjusted report, and reviewed OpenVEX document. It does not
broaden the VEX or authorize publication.

The fourth reviewed-main attempt, run `31605249586`, completed both closed
Maven repository reconstructions, both network-none builds, the raw rootfs
inventory, and the Maven and Bun SBOM/scans. The raw Bun report retained the
same single High React Router finding, and the exact OpenVEX-adjusted report
retained High=0/Critical=0. The repository verifier nevertheless failed closed
because Trivy 0.72.0 returned `FixedVersion="7.18.2, 8.3.0"` while the reviewed
contract still required `"8.3.0"`; every other finding identity field matched.
The `publish` job was skipped. This repair binds the newly observed complete
fixed-version metadata exactly. It does not change the installed 7.18.1
package, treat 7.18.2 as an admitted upgrade, broaden the OpenVEX statement, or
relax any other raw-finding identity field.

The original overlay's two independent clean native-arm64 cache
reconstructions produced manifest SHA-256
`6e7be3a404014f6f7ac7e4bc326c8d46f7d5822fcea1ac000219c17f1d23f421`
and 128,423,777-byte archive SHA-256
`252eade2183bdf5a371f073752420c3a45f5ef8b1dacb08a4addea350389e3c2`.
Those values remain historical evidence only. Two independent Linux/arm64 Bun
1.3.14 reconstructions of the repaired lockfiles produced byte-identical
75,981-entry caches: 75,321 regular files, 660 safe alias symlinks, and
500,034,428 regular-file bytes. The active reviewed manifest is 17,312,740
bytes with SHA-256
`ca5cc4e1a565ecdd7d3f29610b1cdbe869288357ae6e324c83de1a485872b453`;
the active 128,430,898-byte deterministic archive has SHA-256
`863e9d08bf8d7f106059feff7a5e6d96ca7da17b2cf633381241fe66ab88b1ca`.

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
  other changed or untracked path. Store the artifact as a zero-context unified
  diff and invoke `git apply --unidiff-zero --whitespace=error-all` only after
  all complete-file preimages pass; then require all complete-file postimages.
- Override only `d3-color 3.1.0`, `fast-uri 3.1.5`,
  `brace-expansion 5.0.9`, `js-yaml 4.3.1`, `nanoid 3.3.17`, and
  `postcss 8.5.18`. Pin
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
