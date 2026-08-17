---
project: Shirokuma
doc_id: "ADR-0024"
title: "Apply a bounded Trino 483 Web UI dependency overlay"
status: accepted
created: 2026-07-26
updated: 2026-08-17
version: "0.5"
area: "architecture"
tags: [shirokuma, adr, trino, web-ui, supply-chain]
---

# ADR-0024: Apply a bounded Trino 483 Web UI dependency overlay

## Context

ADR-0022 selected the exact Trino 483 source tree, and ADR-0023 temporarily
accepted only its missing upstream publisher authentication for the bounded
local PoC. Reviewed-main dependency publisher run `30184349867` subsequently
completed two independent Maven/Bun reconstructions, two native-arm64
network-none full builds, byte-identical server archives, and the Maven
High=0/Critical=0 gate. It then failed closed at the Bun lockfile scan.

On 2026-08-17, the npm registry published provenance-bearing
`react-router` and `react-router-dom` 7.18.2, and GHSA-qwww-vcr4-c8h2 listed
7.18.2 as the first patched 7.x release. A fresh Trivy 0.72.0 database also
classified `nanoid 3.3.17` as High under CVE-2026-67213, fixed in 3.3.18.
Detached validation of the exact Trino 483 tree showed that the focused
7.18.2 / 3.3.18 revision passes Bun 1.3.14 frozen installation, TypeScript
typecheck, Vite packaging, and legacy webpack packaging, with zero High or
Critical findings across both lockfiles. Issue #63 owner comment `5312231113`
authorizes implementation and a Draft candidate-revision PR only. It does not
authorize merge, sequence 6, publication, or downstream use.

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

The fifth reviewed-main attempt, run
[`31616764771`](https://github.com/TommyKammy/Shirokuma/actions/runs/31616764771),
proved that repair at the dependency-evidence boundary. PR #148 final head
`7581b2413c1c820ac1f774fe28034f1b7bfa6eb1` passed the required workflows and
received exact owner attestation comment
[`5269416490`](https://github.com/TommyKammy/Shirokuma/pull/148#issuecomment-5269416490)
before squash merge `49a86522d6e6c69f4a552220b30fa510d3a5edd2`. Run attempt
`1` then completed both closed Maven reconstructions, both network-none builds,
and the Maven and Bun evidence gates with the reviewed React Router metadata.
The `validate` job succeeded, but the `publish` job failed closed at
`Revalidate the write-capable publication boundary`: GitHub returned empty
workflow-run `pull_requests` associations for the successful final-head PR
runs, while the repository verifier required exact `[148]`.

Read-only candidate artifact `trino-maven-candidate-31616764771-1` (artifact
ID `9150299769`, 844,111,993 bytes, expired `2026-08-13T16:39:07Z`) was retained
by validation but never downloaded by the publish job. Registry
authentication, registry write, signature, attestation, anonymous pull, and a
final publication artifact were not reached. The candidate is not admitted or
durable evidence. Sequence 5 is consumed, rerun and sequence 6 are not
authorized, and publication returns to
`dependency_snapshot_publication_reauthorization_pending`. This failure does
not change the exact four-path overlay, installed React Router 7.18.1, the
React Router-only OpenVEX statement, or any High=0/Critical=0 requirement.

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

The active implementation authorization is Issue #63 comment
`https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5312231113`.

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
  `brace-expansion 5.0.9`, `js-yaml 4.3.1`, `nanoid 3.3.18`, and
  `postcss 8.5.18`. Pin
  `react-router-dom` exactly to `7.18.2` so a future lock refresh cannot select
  another React Router surface implicitly.
- Retain one unadjusted High/Critical-scoped Trivy JSON report over both exact
  lockfiles. It must contain zero High and zero Critical findings while
  retaining the complete expected package inventories. Any finding or package
  graph drift fails closed.
- Remove the active OpenVEX document, adjusted scan command, adjusted report,
  hashes, provenance entries, publication inputs, and diagnostic inputs.
  OpenVEX or another waiver mechanism is not permitted for this candidate.
- Require current Web UI typecheck/Vite packaging and legacy webpack packaging
  as pre-merge validation. Require native server Web UI and client-side route
  smoke before the later image is admitted. Cross-major transitive overrides
  are not considered compatible solely because dependency installation
  succeeds.
- Expire this decision no later than `2026-08-21T22:43:36Z`, together with the
  ADR-0023 authorization. Automatic renewal is forbidden. A fresh decision is
  required for another Trino source tree, lockfile, dependency version, or
  advisory state.
- Preserve owner/reviewer separation. `TommyKammy` owns this decision; `Codex`
  authors the implementation; a different reviewer must approve before merge.
- Do not classify this as an ADR-0019 vulnerability exception. No High or
  Critical finding is waived.

This decision supersedes only ADR-0022's unmodified-source requirement for the
four hash-bound Web UI files. Every other ADR-0022 and ADR-0023 control remains
in force.

## Consequences

The resulting server is a disclosed Shirokuma downstream build of Trino 483,
not a byte-equivalent upstream build. The overlay increases policy and review
surface. Dependency or advisory drift stops publication until a new review is
complete.

Issue #63 remains open. This ADR does not publish a dependency artifact, admit
an image, create Flux resources, activate a runtime, or authorize production or
public exposure.

## Verification

- `python3 -m unittest -v tests.test_trino_dependency_publisher`
- `python3 -m unittest -v tests.test_trino_bun_dependencies`
- `make verify-trino-bootstrap`
- `make verify-security`
- `make verify`

Any separately authorized reviewed-main publisher run must retain the raw
zero-finding report and complete the existing reconstruction, reproducibility, signing,
provenance, publication, and anonymous-pull gates before evidence-only review.

## Rollback

Revert the focused pull request. The publisher then returns to the prior
fail-closed state with no dependency artifact or runtime admitted. Do not
weaken severity filters or substitute a waiver, VEX, or ignore file as rollback.

## Related

- [[ADR-0022_Adopt_Trino_483_repository_source_build]]
- [[ADR-0023_Allow_time_boxed_Trino_483_source_identity_exception_for_local_PoC]]
- [[../04_Development/049_Supply_Chain_Security]]
- Issue #63
