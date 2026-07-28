---
project: Shirokuma
doc_id: "ADR-0025"
title: "Keep the Trino 483 Maven closure blocked pending source remediation"
status: proposed
created: 2026-07-28
updated: 2026-07-28
version: "0.1"
area: "architecture"
tags: [shirokuma, adr, trino, maven, supply-chain, vulnerability]
---

# ADR-0025: Keep the Trino 483 Maven closure blocked pending source remediation

## Context

Reviewed-main run
[`30331912718`](https://github.com/TommyKammy/Shirokuma/actions/runs/30331912718)
failed closed at the repository-owned Maven High/Critical verifier. Its
run-bound artifact
`trino-maven-vulnerability-diagnostics-30331912718-1` has digest
`sha256:b507ea3df6ac0ff5ebb1676c4fb397852b33a16157522fa905ff7c04c040c819`
and contains exactly the descriptor, raw rootfs SBOM, closure-complete SBOM,
and Trivy report required by the contract.

The exact report contains 64 High/Critical occurrences across 40
package/version groups and 37 physical JAR paths. None of those 37 JAR
basenames is copied verbatim into the official Trino 483 server archive. That
is useful build-closure evidence, but it does not prove that vulnerable code
was not shaded, generated into another artifact, or otherwise reachable. It
therefore cannot support a vulnerability waiver or an OpenVEX
`not_affected` statement.

Focused experiments kept the complete High=0/Critical=0 gate unchanged:

- replacing `clean install` with `clean package` and activating Airbase's
  documented `air.check.skip-all` property removes verification- and
  release-only plugin goals, but still needs reviewed safe overrides for
  packaging plugins;
- adding `maven.test.skip=true` prevents resolution of test-only
  `parquet-jackson 1.17.1`, but also prevents required reactor test-JARs such
  as `io.trino:trino-parser:tests:483` from being produced; and
- retaining ordinary `skipTests` keeps those reactor artifacts, but resolves
  `parquet-jackson 1.17.1`. Maven Central lists 1.17.1 as the latest release,
  and its embedded Jackson 2.21.3 finding cannot be upgraded through a newer
  released Parquet artifact.

The exact classification is retained in
`docs/design/evidence/trino/run-30331912718-maven-vulnerability-classification.json`.

## Proposed decision

- Keep the Maven dependency publisher fail-closed. Do not publish, sign,
  attest, admit, or consume the failed candidate.
- Do not add `.trivyignore`, severity exclusions, `ignore-unfixed`, a
  build-only waiver, an inferred non-reachability statement, or a broader
  OpenVEX record.
- Do not merge a lifecycle-only narrowing that cannot complete both fresh
  network-none rebuilds from the closed snapshot.
- Require a separately owner-authorized and time-bounded source-remediation
  contract before changing the Trino or Airbase POM surface. The authorization
  must bind exact source repositories, commits, trees, preimages, postimages,
  permitted paths, dependency replacements, expiry, and rollback.
- The recommended remediation is an independently reproducible source build
  for each embedded dependency that has no fixed published artifact, beginning
  with `parquet-jackson 1.17.1`, combined with exact safe plugin dependency
  overrides and a lifecycle narrowed only to producing the server archive.
- Require two independent dependency reconstructions, two fresh
  network-none builds, byte-identical server archives, closure-complete SBOM,
  High=0/Critical=0 without waivers, signature, SLSA Statement/v1, and
  anonymous exact-digest pull before Issue #63 can advance.
- Preserve owner/reviewer separation. `Codex` may author the implementation;
  `TommyKammy` must own the expanded source decision, and a different reviewer
  must approve it before merge.

This proposal does not modify ADR-0023 or ADR-0024 and does not constitute the
required owner authorization. Until that authorization exists, the current
publisher and admission state remain blocked.

## Consequences

The Trino PoC remains blocked at the Maven dependency evidence boundary. This
is slower than weakening the build-closure gate, but avoids converting newly
disclosed build inputs into an undocumented exception.

Issue #63 remains open. No dependency artifact, image, resident exception,
Flux object, runtime, or Iceberg query is authorized by this proposal.

## Verification

- Verify artifact `8678853700` has the expected digest and exact four files.
- Recompute the 64 occurrences, 40 package/version groups, and 37 physical JAR
  paths from the retained Trivy report.
- Verify zero verbatim basename matches against the official Trino 483 server
  archive, while retaining the explicit non-reachability caveat.
- `python3 -m json.tool docs/design/evidence/trino/run-30331912718-maven-vulnerability-classification.json`
- `make verify-trino-bootstrap`
- `make verify`

## Rollback

Revert the evidence and proposal. The existing publisher remains fail-closed.
Do not remove or weaken the Maven High/Critical verifier as rollback.

## Related

- [[ADR-0022_Adopt_Trino_483_repository_source_build]]
- [[ADR-0023_Allow_time_boxed_Trino_483_source_identity_exception_for_local_PoC]]
- [[ADR-0024_Apply_bounded_Trino_483_Web_UI_dependency_overlay_and_OpenVEX]]
- [[../04_Development/049_Supply_Chain_Security]]
- Issue #63
