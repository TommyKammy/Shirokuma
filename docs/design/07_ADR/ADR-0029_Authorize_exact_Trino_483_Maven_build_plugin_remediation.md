---
doc_id: ADR-0029
title: Authorize exact Trino 483 Maven build-plugin remediation
status: accepted
created: 2026-08-07
updated: 2026-08-07
version: "1.0.0"
area: architecture
tags: [adr, trino, maven, supply-chain, arm64]
---

# ADR-0029: Authorize exact Trino 483 Maven build-plugin remediation

## Context

ADR-0028 kept the Trino dependency publisher blocked after reviewed-main run
`30693677356` found three nonwaivable Maven High findings. PR #142 added a
fail-closed feasibility workflow and retained evidence contract for one exact
candidate. Its final reviewed head `864da7ea3eb93c976691836630cb91775d5074af`
ran successfully as
[`31072144404`](https://github.com/TommyKammy/Shirokuma/actions/runs/31072144404).

Artifact `8956062532` retained a 5,036-file Maven repository, online and
network-none replay logs, native linux/arm64 toolchain records, and hash-bound
policy/source inputs. An independent repository verifier audit passed against
reviewed commit `864da7ea3eb93c976691836630cb91775d5074af` and workflow
execution commit `f404219ce0a189336dbcc2a48fd881064edc3780`.

Risk owner `TommyKammy` approved the exact candidate in Issue #63 comment
[`5210182460`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5210182460)
at `2026-08-07T00:08:48Z`.

## Decision

Authorize only this exact sequential third Trino source patch:

- source repository: `https://github.com/trinodb/trino`
- tag: `483`
- commit: `50b0b50b75abd47f830b7805ee1b51716eb4065e`
- tree: `3b5414292a614b12393bb4605ea2d4c588a5b8ee`
- patch: `bootstrap/trino/v483/patches/0003-shirokuma-maven-build-plugin-closure.patch`
- patch SHA-256: `731e76f296a725d34ea9e226a1815782168cae3890424e69f76a05530afc15be`
- permitted path: root `pom.xml` only
- post-ADR-0027 preimage SHA-256:
  `8d342215a3c748f7965f0a82e847cab13587b94171d9d1422922b665475109c1`
- authorized postimage SHA-256:
  `871c6b21cf9fc70c455d21b64d24dd4501a8b5943242418edc2b2f5cfe14fab8`
- apply arguments: `--unidiff-zero --whitespace=error-all`

The patch may:

- add `org.apache.velocity:velocity-engine-core:2.4.1` only to the exact
  `revapi-maven-plugin` declaration;
- add `org.codehaus.plexus:plexus-utils:4.0.3` only to the 12 exact
  build-plugin declarations fixed by the reviewed patch;
- remove the optional
  `io.github.gitflow-incremental-builder:gitflow-incremental-builder:4.6.0`
  declaration;
- consume only the two reviewed hash-bound Maven SCM metadata postimages
  already retained by ADR-0028.

The selected reactor remains
`:trino-server,:trino-server-core,:trino-server-main,:trino-hdfs,:trino-iceberg`.
The publisher must preserve native linux/arm64 execution, read-only source and
policy mounts, network-none independent reconstruction, closure-complete SBOM,
raw vulnerability evidence, and High=0/Critical=0 without waiver.

Authorization expires at `2026-08-21T22:43:36Z`, has no automatic renewal,
and requires a reviewer different from implementation author `Codex` and the
owner before merge.

## Consequences

The reviewed `main` publisher may perform one run-scoped dependency-snapshot
attempt using the three exact ordered patches. Pull requests remain
validation-only and cannot publish. The produced dependency candidate remains
review-pending and cannot authorize image publication.

Dependency artifact presence, dependency evidence admission, Trino image
publication, resident-image admission, Flux runtime reconciliation, public
Service or Ingress, production use, and Issue #63 closure remain false until
their separate evidence gates pass.

No vulnerability is waived, ignored, suppressed, or reclassified by this
decision. A changed patch, source, preimage, postimage, plugin set, Maven
metadata, reactor, toolchain, or authorization time fails closed.

## Verification

- Verify artifact `8956062532` metadata and digest
  `sha256:46c1ea610e0cb24df43b93cd954c05222573562f9b1677d3d8f7fd0ba65814da`.
- Independently audit the retained 242,729,683-byte archive
  `sha256:b12debf4e760e3042fe34bd9946c2ed96d0ead4eb7959d8491fe63f3240c208a`.
- Require closed manifest SHA-256
  `6667d7b8275c0b24ec4f8ec7070173a57a888ab425d8697e0b261f49fc347ea4`.
- Apply all three patches sequentially and verify each boundary's exact
  preimage and postimage before continuing.
- Run `python3 -m unittest -v tests.test_trino_dependency_publisher`.
- Run `python3 -m unittest -v tests.test_trino_maven_feasibility`.
- Run `python3 scripts/verify_trino_dependency_publisher.py audit --root .`.
- Run `make verify-trino-bootstrap` and `make verify`.

## Rollback

Set all three publication-permission records false, restore lifecycle state
`source_remediation_authorization_pending`, remove the third patch from the
publisher and permitted inventory, and preserve every raw feasibility record.
Do not consume an artifact produced after rollback or authorization expiry.

## Related

- [[ADR-0022_Adopt_Trino_483_repository_source_build]]
- [[ADR-0023_Allow_time_boxed_Trino_483_source_identity_exception_for_local_PoC]]
- [[ADR-0024_Apply_bounded_Trino_483_Web_UI_dependency_overlay_and_OpenVEX]]
- [[ADR-0025_Keep_Trino_483_Maven_closure_blocked_pending_source_remediation]]
- [[ADR-0026_Authorize_bounded_Parquet_Jackson_1_17_1_source_remediation]]
- [[ADR-0027_Authorize_bounded_Trino_483_Iceberg_only_Maven_closure]]
- [[ADR-0028_Keep_Trino_483_publisher_blocked_for_refreshed_Maven_findings]]
- [[../04_Development/049_Supply_Chain_Security]]
- GitHub Issue #63
