---
project: Shirokuma
doc_id: "ADR-0023"
title: "Allow a time-boxed Trino 483 source identity exception for the local PoC"
status: accepted
created: 2026-07-23
updated: 2026-07-23
version: "0.1"
area: "architecture"
tags: [shirokuma, adr, trino, source, supply-chain, local-poc]
---

# ADR-0023: Allow a time-boxed Trino 483 source identity exception for the local PoC

## Context

ADR-0022 selected a repository-owned Trino 483 source build, but required a
qualifying upstream signature or provenance statement before the exact source
tree could become a build input. Trino 483 publishes neither. Waiting for a
future release with acceptable evidence leaves the L1 PoC without a predictable
query-runtime schedule, while downgrading to the signed 476 distribution is not
viable: its signing identity is not anchored to an approved Trino trust root and
the fresh scan has Critical=2 and High=52.

The exact 483 coordinates remain immutable and independently inspectable:

- repository `https://github.com/trinodb/trino`;
- release tag `483`, annotated tag object
  `32d4f28e8311ea6f67edca209df59a0493d869fa`;
- commit `50b0b50b75abd47f830b7805ee1b51716eb4065e`;
- tree `3b5414292a614b12393bb4605ea2d4c588a5b8ee`.

Those hashes identify bytes but do not authenticate the publisher. Shirokuma
therefore treats this as explicit residual source-identity risk, not as proof of
upstream authenticity and not as an image-admission exception.

## Decision

- Accept only the missing upstream source-identity proof for the exact Trino
  483 coordinates above, from `2026-07-22T22:43:36Z` through
  `2026-08-21T22:43:36Z`. The maximum duration is 30 days and automatic renewal
  is forbidden. The authoritative owner approval is Issue #63 comment
  `https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5052385803`.
- Limit the authorization to the `mac-studio-solo/local-lite` non-production
  PoC, synthetic or PoC data, and no public Service or Ingress. Production data,
  production credentials, other source revisions, and other environments remain
  out of scope.
- Preserve owner/reviewer separation. `TommyKammy` owns the risk decision;
  `Codex` authors the implementation; a reviewer other than the implementation
  author must approve the authorization change before merge. Issue #63 is
  mandatory for the decision and remains open through runtime acceptance.
- Permit the next evidence-only checkpoint to define and review an authenticated,
  closed Maven dependency-snapshot contract. This ADR does not add or authorize
  a dependency publisher, image publisher, dependency artifact, Containerfile,
  resident-image entry, credential, Flux object, or runtime.
- Continue to reject the upstream Trino OCI image and server archive as build or
  resident inputs. Shirokuma re-signing cannot repair their missing upstream
  identity or provenance.
- Require the later repository-owned path to retain the exact source binding,
  authenticated closed dependency snapshot, network-none reproducible native
  linux/arm64 build, digest-pinned builder and runtime bases, native arm64 smoke,
  CycloneDX SBOM, fresh High=0/Critical=0 scan, Cosign/Rekor signature, SLSA
  provenance, anonymous exact-digest retrieval, and a separate resident-image
  admission before any Flux reconciliation.
- Do not stack this authorization with an ADR-0019 vulnerability exception for
  Trino. This decision waives no vulnerability finding: High=0/Critical=0
  remains mandatory for the exact dependency closure, build output, and runtime
  image.
- Treat Shirokuma's later signature and SLSA provenance only as proof of the
  downstream repository build. They must continue to disclose the accepted
  upstream source-identity gap and cannot claim to authenticate Trino's publisher.
- Fail closed at expiry. After `2026-08-21T22:43:36Z`, dependency or image
  publication, resident admission, and runtime reconciliation are forbidden.
  A future workflow must verify the authorization before fetching or executing
  the source and before each publication or admission step.

This decision supersedes only ADR-0022's requirement to wait for qualifying
upstream authentication before reviewing the dependency-snapshot contract. All
other ADR-0022 controls and every ADR-0019 non-waivable trust control remain in
force.

## Renewal and exit

Renewal requires a new Issue-bound decision, fresh verification of the exact
source status, a fresh vulnerability review, explicit start and expiry
timestamps no more than 30 days apart, and the same owner/reviewer separation.
Editing the existing expiry in place or relying on continued runtime operation
is not renewal.

The exception ends early when a qualifying upstream signature or provenance
statement authenticates these exact coordinates, or when a newer authenticated
Trino release passes the same compatibility and High=0/Critical=0 gates. The
admission record must then remove or retire the provisional authorization before
the authenticated path proceeds.

## Subsequent publisher checkpoint

The dependency-snapshot contract review completed on 2026-07-24. The next
reviewed boundary permits a one-shot repository-owned publisher at
`.github/workflows/trino-maven-dependencies.yml` while the exact Issue #63
authorization remains active. Pull requests perform only static read-only
validation. A main push must revalidate the authorization before every
source-use or publication boundary and stops fail closed at expiry.

The publisher resolves two fresh Maven repositories through only Maven Central
and Confluent, compares their complete deterministic manifests and archives,
uses resolver markers to bind each dependency origin, excludes timestamp-bearing
resolver metadata, and performs two fresh native linux/arm64 builds with
networking disabled. It retains the dependency SBOM, fresh High=0/Critical=0 scan, Cosign/Rekor
signature, SLSA provenance with the exact source and dependency inputs, and
anonymous exact-digest retrieval proof. Its output is
`review_pending_dependency_evidence`, not an admitted dependency or runtime
input.

A separate evidence-only PR must review and pin the exact OCI digest and retire
the publisher. Image publication, resident admission, credentials, Flux
objects, and runtime activation remain forbidden until their own later
checkpoints.

GitHub Container Registry creates a first publication as private. If the first
main run stops at the anonymous pull gate, the failed attempt is not admitted.
The owner must make the package public and rerun the same reviewed main
revision; a user-credential fallback is forbidden.

## Consequences

Shirokuma can make bounded progress toward the L1 query PoC without claiming
that immutable hashes authenticate the upstream publisher. The repository also
accepts operational cost: expiry can block unrelated forward progress until the
authorization is retired, replaced with authenticated evidence, or re-approved
through a new review.

The current admission remains `blocked`; no Trino image exists yet. The
repository state is `dependency_snapshot_publication_pending`, and a successful
main run can create only a review-pending dependency artifact. Dependency
admission, image publication, resident admission, Flux readiness, and
Polaris/Iceberg query acceptance remain separate fail-closed checkpoints.

## Verification

- `make verify-trino-bootstrap`
- `make verify-design-context`
- `make verify-security`
- `make verify`
- fixture rejection for expired, over-30-day, auto-renewing, reviewer-colliding,
  source-mismatched, or vulnerability-stacking authorization records

## Rollback

Revert this ADR and the matching `provisional_source_authorization` record, then
restore `source_authentication.status` and the next phase to blocked source
authentication review. If later artifacts or runtime exist, suspend Flux first,
remove the exact Trino runtime and resident entry, and preserve unrelated Polaris
and object-storage state.

## Related

- [[07_ADR/ADR-0019_Allow_time_boxed_resident_image_exceptions_for_local_lab]]
- [[07_ADR/ADR-0022_Adopt_Trino_483_repository_source_build]]
- [[04_Development/049_Supply_Chain_Security]]
- [Issue #63](https://github.com/TommyKammy/Shirokuma/issues/63)
