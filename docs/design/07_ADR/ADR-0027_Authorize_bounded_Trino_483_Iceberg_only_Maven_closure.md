---
project: Shirokuma
doc_id: "ADR-0027"
title: "Authorize bounded Trino 483 Iceberg-only Maven closure"
status: accepted
created: 2026-07-29
updated: 2026-07-29
version: "0.1"
area: "architecture"
tags: [shirokuma, adr, trino, iceberg, maven, supply-chain, vulnerability]
---

# ADR-0027: Authorize bounded Trino 483 Iceberg-only Maven closure

## Context

The reviewed-main dependency publisher run `30415622742` remained blocked by
nonwaivable High/Critical findings in Maven build-plugin closure that the full
Trino reactor downloaded but the Shirokuma Iceberg PoC does not need. The
runtime boundary needs Trino server core/main, HDFS support, and the Iceberg
plugin. Building and packaging every Trino plugin unnecessarily expands both
the dependency snapshot and the runtime distribution.

The owner approved the exact boundary at `2026-07-29T09:35:05Z` in Issue #63 comment
[`5115851323`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5115851323).
The approval does not waive a vulnerability, ignore a finding, expand OpenVEX,
or admit a runtime. It permits a reviewed source overlay whose result must
still pass the unchanged High=0/Critical=0 and network-none gates.

## Decision

Apply
`bootstrap/trino/v483/patches/0002-shirokuma-iceberg-only-maven-closure.patch`
with SHA-256
`dd9cd76984c4bd2845aa95e87cdb404f7d24c0cfed65d5a780da32ce4f9d4269`
only after verifying the exact Trino 483 source binding:

- repository: `https://github.com/trinodb/trino`
- tag: `483`
- commit: `50b0b50b75abd47f830b7805ee1b51716eb4065e`
- tree: `3b5414292a614b12393bb4605ea2d4c588a5b8ee`

The only permitted paths and hashes are:

| Path | Preimage SHA-256 | Postimage SHA-256 |
| --- | --- | --- |
| `pom.xml` | `e1ba9a61315097e3a7133238c778ec161ac6097fe77a660fc5455a3e84568820` | `8d342215a3c748f7965f0a82e847cab13587b94171d9d1422922b665475109c1` |
| `core/trino-spi/pom.xml` | `9a3ab7c1e730e9534ca575b243865f4ff8ca355d201e5a7aa79f244401806993` | `3032163467da8247367e3c0ac60d790ddabc96c632c083448d5b5a7d63f05b2b` |
| `core/trino-server-core/src/main/provisio/trino-core.xml` | `0f2e86c7cb0873c43a602a55e8c8827bc3292fbe09868014ca360b61179d6863` | `585f0b68b6e0c2b1da66f71a0e289b77e776fca3e0451a17d55f35b10e18727a` |
| `core/trino-server/src/main/provisio/trino.xml` | `ca8b95cdd6579da16fe531c2110f5c4d67e63f385b37b5b7ab9a220bee58c323` | `f549d66db97d1bbee1b2505b6b3875ca3db9362a88b0d7402de8cc921bd5c018` |

The patch is a canonical zero-context unified diff and must be applied with
`git apply --unidiff-zero --whitespace=error-all`. This representation avoids
embedding generator summaries or context-only whitespace while preserving the
same four postimages.

The online resolution and offline rebuild must select exactly:

```text
-pl ':trino-server,:trino-server-core,:trino-server-main,:trino-hdfs,:trino-iceberg' -am
```

The distribution contains only server core/main and the Iceberg plugin with
its HDFS runtime dependency. Other Trino plugins are not permitted in the
archive. The build uses fixed output timestamp `2026-07-18T00:36:39Z`.

The reviewed overlay upgrades build plugins and their dependency closure only
to the exact versions encoded in the patch:

- frontend-maven-plugin `2.0.2`
- Provisio Maven plugin `2.0.0`
- Maven JAR plugin `3.5.1`
- Jackson core/databind `2.21.4`
- Commons BeanUtils `1.11.0`
- Commons IO `2.22.0`
- Maven core `3.9.16`
- Plexus Archiver `4.12.0`
- Plexus Utils `3.6.1` or `4.0.3`, according to plugin compatibility

The publisher must retain the separately authorized Parquet Jackson
remediation, independently reconstruct the closed Maven repository twice,
perform two network-none offline clean builds, compare the server archives
byte-for-byte, retain closure-complete SBOM/raw scan evidence, and require
High=0/Critical=0 without waiver before publication.

This authorization expires at `2026-08-21T22:43:36Z`, has no automatic
renewal, and requires a reviewer different from implementation author `Codex`
before merge. Risk owner remains `TommyKammy`.

## Consequences

The dependency snapshot becomes smaller and matches the PoC runtime boundary.
It does not authorize image publication, resident admission, Flux/runtime
changes, credentials, or Iceberg query acceptance. Reactor-only modules that
are needed to compile the selected projects may still build, but they are not
packaged as runtime plugins.

Any different source path, hash, plugin/dependency version, reactor selection,
distribution content, expiry, or evidence result fails closed. No finding is
waived or suppressed.

## Verification

- Apply both source patches to a fresh exact Trino 483 checkout and verify the
  closed changed-path set.
- Run the exact selected reactor with the digest-pinned native arm64 builder.
- Inspect each server archive with the repository verifier and require the
  exact Iceberg-only plugin set and required server/HDFS members.
- Scan the complete resulting Maven repository and require High=0/Critical=0.
- Repeat `clean install` with `--offline` and container network `none`.
- `python3 -m unittest tests.test_trino_dependency_publisher`
- `python3 scripts/verify_trino_dependency_publisher.py audit --root .`
- `make verify-trino-bootstrap`
- `make verify`

## Rollback

Set dependency-publication permissions to false, restore lifecycle state
`source_remediation_authorization_pending`, and remove the second source
overlay and selected-reactor commands. Do not consume a candidate produced
under a reverted or expired authorization. Preserve all raw evidence.

## Related

- [[ADR-0022_Adopt_Trino_483_repository_source_build]]
- [[ADR-0023_Allow_time_boxed_Trino_483_source_identity_exception_for_local_PoC]]
- [[ADR-0024_Apply_bounded_Trino_483_Web_UI_dependency_overlay_and_OpenVEX]]
- [[ADR-0025_Keep_Trino_483_Maven_closure_blocked_pending_source_remediation]]
- [[ADR-0026_Authorize_bounded_Parquet_Jackson_1_17_1_source_remediation]]
- [[../04_Development/049_Supply_Chain_Security]]
- Issue #63
