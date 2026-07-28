---
project: Shirokuma
doc_id: "ADR-0026"
title: "Authorize bounded Parquet Jackson 1.17.1 source remediation"
status: accepted
created: 2026-07-28
updated: 2026-07-28
version: "0.1"
area: "architecture"
tags: [shirokuma, adr, trino, parquet, jackson, supply-chain, vulnerability]
---

# ADR-0026: Authorize bounded Parquet Jackson 1.17.1 source remediation

## Context

ADR-0025 keeps the Trino 483 dependency publisher blocked because the reviewed
Maven closure contains nonwaivable High/Critical findings. The first
runtime-relevant embedded copy without a fixed published artifact is
`org.apache.parquet:parquet-jackson:1.17.1`, whose shaded payload contains
Jackson 2.21.3. Maven Central does not provide a newer Parquet release that
can replace it.

The owner approved the exact remediation boundary in Issue #63 comment
[`5105612399`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5105612399).
The approval does not waive a vulnerability, reduce scan severity, or admit a
runtime. It permits one reproducible source rebuild so the unchanged
High=0/Critical=0 gate can assess the resulting closed dependency snapshot.

## Decision

Authorize exactly one source-remediation input:

- repository: `https://github.com/apache/parquet-java`
- release tag: `apache-parquet-1.17.1`
- release tag object:
  `1f54ba44afb285fecbaf54bde5c0afa259327fc4`
- nested RC tag: `apache-parquet-1.17.1-rc0`
- nested RC tag object:
  `172d200a7eb81161345bdccaf628af34178fc479`
- commit: `78a8d3230eb4769db93de5f2f2e18363c04cae81`
- tree: `28b877df95a7a661361b8776f6ebe21d73d8da6d`
- permitted path: root `pom.xml` only
- preimage: 24,493 bytes,
  `sha256:bfe7519b9886e9df51bfef8be52064b3aadcbf9ae21c77402d8a66837aa5442f`
- postimage: 24,493 bytes,
  `sha256:e07982c0f114b592c06c2aba1254df9c280b69a2dd27f3a0739421fe84d12efa`

The only permitted replacements are:

```xml
<jackson.version>2.21.3</jackson.version>
```

to:

```xml
<jackson.version>2.21.4</jackson.version>
```

and:

```xml
<jackson-databind.version>2.21.3</jackson-databind.version>
```

to:

```xml
<jackson-databind.version>2.21.4</jackson-databind.version>
```

The publisher must:

- fetch and verify two independent source checkouts;
- apply the two exact replacements only after verifying the pristine source;
- build `parquet-jackson 1.17.1` twice with the digest-pinned native arm64
  Maven builder and fixed output timestamp `2026-05-08T01:45:35Z`;
- require byte-identical JAR and dependency-reduced POM outputs;
- prove that the JAR contains shaded Jackson 2.21.4, contains no shaded
  Jackson 2.21.3 marker, and does not expose unshaded Jackson paths;
- seal the exact JAR and POM into each independent Trino dependency
  repository with a distinct source-remediation provenance origin;
- bind the source contract in the closed Maven descriptor and compare the
  complete descriptors and archives byte-for-byte;
- retain closure-complete SBOM and raw scan evidence, and require
  High=0/Critical=0 without waivers before publication; and
- retain the existing signature, Rekor, SLSA Statement/v1, and anonymous
  exact-digest pull gates.

This authorization expires at `2026-08-21T22:43:36Z`, has no automatic
renewal, and requires a reviewer different from implementation author
`Codex` before merge. Risk owner remains `TommyKammy`.

## Consequences

The dependency publisher may run on reviewed `main` with this exact source
remediation. Dependency artifact presence, image publication, resident
admission, runtime reconciliation, and Iceberg query acceptance remain false
until their separate evidence gates pass.

Any different repository, tag, object, commit, tree, path, dependency
replacement, output, or expiry fails closed. Findings outside this exact
Parquet Jackson boundary remain nonwaivable and require a separate
owner-authorized decision.

## Verification

- `python3 -m unittest tests.test_parquet_jackson_remediation`
- `python3 -m unittest tests.test_trino_dependency_publisher`
- `python3 scripts/verify_trino_dependency_publisher.py audit --root .`
- `make verify-trino-bootstrap`
- `make verify`
- On reviewed `main`, verify two source fetches, two source builds,
  byte-identical remediation outputs, byte-identical complete dependency
  snapshots, closure-complete SBOM, High=0/Critical=0, signature, SLSA
  Statement/v1, and anonymous exact-digest pull.

## Rollback

Set all three dependency-publication permission records to false, restore
lifecycle state `source_remediation_authorization_pending`, and remove the
remediation source fetch/build/stage steps. Do not consume a candidate built
under a reverted or expired authorization. Preserve the raw evidence and
High/Critical gate.

## Related

- [[ADR-0022_Adopt_Trino_483_repository_source_build]]
- [[ADR-0023_Allow_time_boxed_Trino_483_source_identity_exception_for_local_PoC]]
- [[ADR-0024_Apply_bounded_Trino_483_Web_UI_dependency_overlay_and_OpenVEX]]
- [[ADR-0025_Keep_Trino_483_Maven_closure_blocked_pending_source_remediation]]
- [[../04_Development/049_Supply_Chain_Security]]
- Issue #63
