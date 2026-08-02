---
project: Shirokuma
doc_id: "ADR-0028"
title: "Keep Trino 483 publisher blocked for refreshed Maven findings"
status: accepted
created: 2026-08-01
updated: 2026-08-01
version: "0.2"
area: "architecture"
tags: [shirokuma, adr, trino, maven, supply-chain, vulnerability]
---

# ADR-0028: Keep Trino 483 publisher blocked for refreshed Maven findings

## Context

Reviewed-main run
[`30693677356`](https://github.com/TommyKammy/Shirokuma/actions/runs/30693677356)
verified the PR #140 classifier-identity repair: both closed Maven repository
reconstructions, both network-none builds, rootfs inventory generation, and
the closure-complete CycloneDX SBOM step passed. The next unchanged
High=0/Critical=0 gate then failed closed on three High findings:

- `CVE-2024-47554` for embedded `commons-io 2.8.0` inside
  `velocity-engine-core 2.3`;
- `CVE-2025-67030` for top-level `plexus-utils 4.0.1`; and
- `CVE-2025-67030` for top-level `plexus-utils 4.0.2`.

The run produced only the failure-diagnostic Actions artifact. It did not
publish a dependency artifact, admit an image, create runtime manifests, or
change Flux state.

ADR-0027 authorizes only the exact active patch SHA-256 and exact `pom.xml`
postimage recorded there. It also states that a different dependency version,
patch hash, or postimage fails closed. The refreshed findings therefore cannot
be repaired under the existing authorization even though Plexus Utils 4.0.3
is already an allowed version for other plugin realms.

## Decision

Keep dependency publication blocked and restore lifecycle state
`source_remediation_authorization_pending`. Preserve the exact run evidence:

- `docs/design/evidence/trino/run-30693677356-trivy-vulnerability.json`
- `docs/design/evidence/trino/run-30693677356-maven-closure.cdx.json`
- `docs/design/evidence/trino/run-30693677356-maven-dependency-manifest.json`
- `docs/design/evidence/trino/run-30693677356-maven-rootfs.cdx.json`
- `docs/design/evidence/trino/run-30693677356-post-adr-0027-pom.xml.gz`
- `docs/design/evidence/trino/run-30693677356-maven-vulnerability-classification.json`
- `docs/design/evidence/trino/run-30724152120-maven-feasibility-validation.json`
- `docs/design/evidence/trino/run-30724152120-maven-feasibility-artifact-receipt.json`

The retained classification binds the Actions artifact, raw report,
closure-complete SBOM, run-scoped manifest, and raw rootfs SBOM hashes and
sizes. All four diagnostic inputs are retained in the repository beyond the
Actions artifact expiry. The repository verifier compares their exact bytes
and hashes, proves the manifest-to-closure, rootfs-to-closure, and
closure-to-report identities, recomputes the Trivy finding summary and
identities, and binds the policy classifications and required next action. It
then validates the feasibility patch as a canonical single-path zero-context
diff and applies it to the hash-bound retained baseline before checking the
postimage hash. No vulnerability is waived, ignored, suppressed, or
reclassified through OpenVEX.

A non-active feasibility patch is retained at
`docs/design/evidence/trino/run-30693677356-proposed-source-overlay.patch`.
It is a canonical zero-context diff that applies only after the current
ADR-0027 overlay:

- baseline `pom.xml` SHA-256:
  `8d342215a3c748f7965f0a82e847cab13587b94171d9d1422922b665475109c1`
- retained gzip SHA-256:
  `dc5cfc5cd0ef38f2960926b364c32f476c1c94e949e0fa43711f17d951eb9b75`
- retained gzip bytes: `13531`
- candidate `pom.xml` SHA-256:
  `0b23a8f4834bddb129e1d76c2cc63823f8691cf39d8fda02a3853d6fce79fbd3`
- candidate patch SHA-256:
  `3c31e06c240196c3f141b55cb34ba1f26e71cdee003b6687b8e5acd16304a25f`
- candidate patch bytes: `7179`

The candidate adds Velocity Engine Core 2.4.1 and Plexus Utils 4.0.3 as direct
dependencies in the affected inherited Maven plugin realms, including the
Gitflow Incremental Builder extension realm. Run `30724152120` is superseded:
hardened review found that its retained repository still contained
`plexus-utils-4.0.2.jar`, and its verifier did not bind the archive contents or
retained toolchain inputs. It cannot support authorization. Fresh native arm64
evidence for the revised exact candidate must prove physical vulnerable-JAR
absence, retain and validate the builder index, Maven version output, and global
settings, and replay the pruned repository with networking disabled and a
read-only mount.

This is feasibility evidence only. It does not activate the candidate patch,
authorize a new source postimage, permit another publisher run, or claim a
successful full `clean install`, network-none reconstruction, fresh SBOM, or
fresh vulnerability scan.

## Required owner decision

Before implementation resumes, the risk owner must approve or reject the
exact candidate boundary. Approval must bind:

- the exact Trino repository, tag, commit, and tree already fixed by ADR-0027;
- post-ADR-0027 `pom.xml` as the only candidate preimage and changed path;
- the retained candidate patch SHA-256, byte length, and postimage SHA-256;
- Velocity Engine Core 2.4.1 and Plexus Utils 4.0.3 as the only new dependency
  replacements;
- the unchanged selected reactor and digest-pinned builder;
- a new retained and reproducible validation record proving the exact online
  and offline commands and vulnerable-coordinate result;
- the existing expiry `2026-08-21T22:43:36Z`, with no automatic renewal;
- independent reviewer approval distinct from implementation author `Codex`;
  and
- unchanged High=0/Critical=0, two-reconstruction, two-network-none-build,
  signature, provenance, and anonymous exact-digest gates.

Any approval must not waive either CVE, authorize OpenVEX for these findings,
or admit the produced artifact or runtime.

## Consequences

Merging this evidence checkpoint makes subsequent main pushes skip dependency
publication until the exact owner decision is recorded and separately
reviewed. Existing ADR-0023, ADR-0024, ADR-0026, and ADR-0027 authorizations
remain intact for their exact boundaries but are insufficient to publish the
new closure.

Issue #63 remains open. Issues #64 and later remain dependency-blocked because
the Trino artifact has not passed resident admission or runtime acceptance.

## Verification

- Verify every retained input and baseline hash and byte length, then prove
  the manifest, raw rootfs SBOM, closure SBOM, and report describe one closed
  package snapshot.
- Recompute three High findings, zero Critical findings, two CVE IDs, three
  package/version groups, and three physical JAR paths; require the exact
  fail-closed classifications, dependency sources, and owner next action.
- Apply the candidate patch to the exact post-ADR-0027 `pom.xml` with
  `git apply --unidiff-zero --whitespace=error-all` and verify the candidate
  postimage hash.
- Reverify run `30724152120`, artifact `8825789672`, the retained validation
  record, and the artifact receipt; require online and network-none offline
  exit status zero, a read-only offline repository, reproducible inputs, and
  an empty vulnerable-coordinate set before the separate owner decision.
- `python3 -m unittest tests.test_trino_dependency_publisher`
- `python3 scripts/verify_trino_dependency_publisher.py audit --root .`
- `make verify-trino-bootstrap`
- `make verify`

## Rollback

Remove only this evidence checkpoint and restore the prior publication-pending
contract if the run evidence or classification is shown to be incorrect. Do
not delete the failed Actions artifact while available, modify the retained
raw report, or activate the candidate patch as part of rollback.

## Related

- [[ADR-0023_Allow_time_boxed_Trino_483_source_identity_exception_for_local_PoC]]
- [[ADR-0024_Apply_bounded_Trino_483_Web_UI_dependency_overlay_and_OpenVEX]]
- [[ADR-0025_Keep_Trino_483_Maven_closure_blocked_pending_source_remediation]]
- [[ADR-0026_Authorize_bounded_Parquet_Jackson_1_17_1_source_remediation]]
- [[ADR-0027_Authorize_bounded_Trino_483_Iceberg_only_Maven_closure]]
- [[../04_Development/049_Supply_Chain_Security]]
- Issue #63
