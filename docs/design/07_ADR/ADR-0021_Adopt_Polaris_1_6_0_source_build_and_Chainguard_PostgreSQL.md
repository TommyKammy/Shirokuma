---
project: Shirokuma
doc_id: "ADR-0021"
title: "Adopt a source-built Polaris 1.6.0 and signed PostgreSQL metadata store"
status: accepted
created: 2026-07-16
updated: 2026-07-21
version: "0.16"
area: "architecture"
tags: [shirokuma, adr, polaris, postgresql, arm64, supply-chain]
---

# ADR-0021: Adopt a source-built Polaris 1.6.0 and signed PostgreSQL metadata store

## Context

WP-L1-LAKE-002 requires resident linux/arm64 images for Apache Polaris and its
PostgreSQL metadata store. The `apache/polaris:1.6.0` index contains an arm64
manifest and attestation attachments, but no trusted publisher identity is
established for that OCI image. Attachment presence is not signer
authentication, so the upstream image remains inadmissible.

Apache publishes a PGP-signed Polaris 1.6.0 source release and SHA-512 checksum.
The release tag resolves to commit
`dd306009d81a0e15adafe9dcd7d1c6d04d326f34`. Upstream documents the Java 21
Gradle container build path.

PR #78 independently reviewed the exact Gradle dependency snapshot and merged
as `b12593f27ae4e6ec8b64865f9b6b0bbf114ec654`. Publication may now advance to
the separate image checkpoint, but the snapshot and any future image remain
non-admitted until their own review boundaries complete.

The UBI 9 Java 21 runtime candidate recorded during the initial source
assessment no longer meets the zero High/Critical image gate: a 2026-07-20
Trivy 0.72.0 feasibility scan reported 21 High findings. The unmodified Polaris
server added another six High findings through Hadoop 3.5.0 and Ranger runtime
dependencies. Apache Polaris has no newer release and Hadoop 3.5.0 has no
replacement release that removes its shaded vulnerable components.

The Chainguard PostgreSQL image is a viable metadata-store candidate. On
2026-07-16 the resolved index
`cgr.dev/chainguard/postgres@sha256:3dc629a917612f1630c6f8e7a17f23a42cbd5917b9b3080972b70b1583daff34`
contained linux/arm64 manifest
`sha256:c455ec159d05d99ee031d471b8692668562fed8e8c9c37be5e0dbdbee8e5f7b8`
and PostgreSQL 18.4. Cosign verified the index against the
`chainguard-images/images` main-branch release workflow and the GitHub Actions
OIDC issuer. A focused Trivy 0.72.0 scan reported High=0 and Critical=0.

These observations select build paths; they are not resident admission by
themselves. Durable evidence and repository verification remain authoritative.
Reviewed main run `29711984394`, attempt `1`, subsequently published
`ghcr.io/tommykammy/shirokuma-polaris@sha256:db403e2db7afbe4e8a62261500e229f6d796a420e814564b49f3e14217fd6c9e`
from commit `706575ba3f21987033a29b6d21367981e9c54e3e`. Final artifact
`polaris-image-publication-29711984394-1` (ID `8449181390`) passed its 32-entry
evidence manifest, High=0/Critical=0 scan, and non-root read-only smoke. These
results establish the exact Polaris candidate; they still do not authorize a
resident image or runtime.

The admitted repository image is intentionally server-only: its closed build
tasks are `:polaris-server:assemble` and
`:polaris-server:quarkusAppPartsBuild`. Relational JDBC bootstrap requires the
separate Polaris Admin Tool application before the server can use the database.
The reviewed dependency snapshot does not contain the Admin Tool's direct
`io.quarkus:quarkus-picocli` dependency, so it cannot establish an offline
Admin build. Upstream's Admin Tool graph also includes NoSQL and MongoDB modules
unconditionally; a dependency-input checkpoint must disclose that broader
surface rather than claim a relational-only runtime.

## Decision

- Reject the upstream Polaris 1.6.0 OCI image for resident use.
- Build Polaris from the ASF-signed 1.6.0 source release in a repository-owned,
  main-only workflow. The build must pin every base image and tool, verify PGP
  and SHA-512 before building, publish only an immutable linux/arm64 digest, and
  retain signature, transparency, provenance, CycloneDX SBOM, Trivy scan, and
  runtime-smoke evidence.
- Use Amazon Corretto 21.0.11 from the Docker Official Image as the Polaris
  runtime base, pinned to index
  `sha256:d3a3476c19cbe37b2e3e46a2116ff197ab37c7072baad55ee0ad07f3b97e8d02`
  and linux/arm64 manifest
  `sha256:ba1fe4a3fd4c6b70360183fccd1f0a168c3ea6f73709e8f81945cb9087431ff2`.
  The main publisher must repeat the zero High/Critical scan; the workstation
  observation is not admission evidence.
- For the separate Polaris Admin Tool image only, supersede the original
  Amazon Linux 2023 Corretto base after main run `29798208118` found 19 High
  OS-package vulnerabilities. Repin the Admin image to Amazon Corretto 21 on
  Alpine 3.24, using exact index
  `sha256:30b1b2246cee9a98c9bf8a11537a04f1eaf8c59279b0c70ae02d7e5b934edeaa`
  and exact linux/arm64 manifest
  `sha256:dc43b39c47f1729dc772a9b8af7222757fac6c8cfa8a0802829af665b1c89925`.
  This does not change the already reviewed server image. The Admin publisher
  must re-prove Java 21, CLI compatibility, SBOM/NoSQL disclosure,
  High=0/Critical=0, signature, provenance, and anonymous retrieval before any
  evidence review.
  Main run `29802331708` proved those candidate gates for exact digest
  `sha256:16e3fd99da2afd446463405bd59236322c37bb066b2af5f46f6e3dd5b7c8710b`
  but failed before final retention because the checksum generator included the
  `evidence.sha256` file being written. The trusted tag remains a
  non-authoritative pointer, the digest is not approved, and a corrected run
  must stage the manifest outside the evidence directory before atomic placement.
- Build a bounded Shirokuma downstream distribution by applying one
  hash-pinned overlay only after pristine ASF source verification. The overlay
  removes HadoopFileIO, Hadoop external-catalog federation, and Ranger
  authorization runtime edges while retaining the native Polaris catalog, OPA,
  PostgreSQL persistence, and S3 storage profile. Preimage/postimage hashes,
  absence of Hadoop/Ranger/Jetty HTTP jars and SBOM components, and exact patch
  bytes are mandatory. No vulnerability exception is used.
- Use the digest-pinned Chainguard PostgreSQL candidate only after its signature,
  arm64 manifest, provenance/SBOM, and zero High/Critical scan evidence are
  retained and independently reverified by the resident-image gate.
- Follow the two-phase publication lifecycle established by ADR-0020. A branch
  may introduce a closed, runtime-disabled build contract. Only a successful
  `refs/heads/main` publication followed by an evidence-only review may approve
  the Polaris digest.
- Treat the Polaris Admin Tool as a separate, mandatory companion artifact.
  First publish a self-contained Admin dependency snapshot from the reviewed
  server snapshot as an immutable parent seed. The new checkpoint must verify
  the parent exact reference, descriptor, cache layer, retained verification
  metadata, and review merge before adding dependencies.
- Bind the Admin tasks `:polaris-admin:assemble` and
  `:polaris-admin:quarkusAppPartsBuild` and repeat both existing server tasks as
  regressions. A fresh Gradle home with networking disabled, offline mode, and
  strict dependency verification is required before the new snapshot can enter
  evidence review.
- Keep the Admin snapshot non-admitted, the Admin image publisher disabled, and
  all runtime, Flux, and credential resources forbidden at this checkpoint.
  The upstream NoSQL/MongoDB dependency surface is `review_required`; a later
  image-publication decision must retain SBOM and vulnerability evidence and
  must not infer relational-only scope from this build-input publication.
- After the dependency evidence review merges, permit a separate Admin image
  publication-policy checkpoint to add only
  `bootstrap/polaris/v1.6.0/admin-image-contract.json`,
  `bootstrap/polaris/v1.6.0/Containerfile.admin`, and
  `.github/workflows/polaris-admin-arm64.yml`. It records
  `admin_image_publication_pending` with next state
  `admin_image_evidence_review_pending` and may publish
  `ghcr.io/tommykammy/shirokuma-polaris-admin:1.6.0-arm64` only from main. The
  mutable tag does not approve or admit an image.
- Preserve the upstream Admin fast-jar layout and launch
  `/usr/bin/java -jar /deployments/quarkus-run.jar`; an inert `--help` default
  and exact Picocli usage marker are mandatory smoke evidence. The image SBOM
  and scan must include the upstream NoSQL/MongoDB surface.
- Reserve `bootstrap --credentials-file=<file>` for the later runtime
  checkpoint using an externally provisioned, read-only Secret. The YAML or
  JSON file maps realms to non-empty `client-id` and `client-secret`, and file
  input is mutually exclusive with `--realm`, singular `--credential`, and
  `--print-credentials`. No credential material is allowed at publication.
- Treat one-shot as publisher-lifecycle retirement, not as a single workflow
  attempt. Each attempt has an immutable run-scoped tag. If the new GHCR
  package is initially private, the credential-free exact-digest retrieval gate
  fails after the quarantined candidate is created but before signing,
  provenance, or trusted-tag promotion. The owner may make that package public
  and rerun before evidence review. Failed attempts are never admitted, and
  authenticated retrieval is not a fallback.
- Evaluate the lifecycle gate first. Every active prepare, verify, and promote
  job must then run the static and cryptographic publication audits before any
  registry credential is created, and the observed Gradle and Java versions
  must equal the pinned toolchain before retaining candidate evidence.
- Retain the reviewed final publication set under
  `bootstrap/polaris/v1.6.0/image-evidence/`, mark the exact digest
  `approved_for_atomic_admission`, advance the lifecycle to
  `atomic_admission_pending`, and retire the write-capable publisher. The
  mutable `1.6.0-arm64` tag is only a non-authoritative pointer.
- Admit the exact Polaris and PostgreSQL digests only as one atomic pair after
  anonymous exact-reference preflight and fresh PostgreSQL exact-image plus
  CycloneDX-input scans. Each vulnerability database must be no more than 24
  hours old, all 56 Wolfi and four Go libraries must remain covered, and both
  reports must remain High=0/Critical=0.
- Add both exact records to `security/resident-images.json` in the same change.
  A missing peer, stale scan, incomplete coverage, or mismatched digest fails
  closed.
- Resident-image admission does not authorize catalog bootstrap, runtime/Flux,
  or credential manifests. Those remain blocked until runtime acceptance.
- Keep PostgreSQL credentials and the SeaweedFS application credentials in the
  approved external Secret path. No placeholder or sample credential satisfies
  readiness.

## Consequences

The source-build lifecycle adds a prerequisite checkpoint before Flux resources
can be reviewed. It avoids laundering an unauthenticated upstream image through
a local signature. PostgreSQL can use an independently signed upstream image,
but its currently resolved digest must not be inferred from the mutable
`latest` pointer after this observation.

The Polaris artifact is a disclosed Shirokuma downstream distribution rather
than byte-equivalent upstream server output. Hadoop external-catalog federation,
HadoopFileIO, and Ranger authorization are unavailable in this bounded
local-lite profile. Reintroducing any of them requires a new dependency
closure, vulnerability review, contract update, and evidence-only review.

The PostgreSQL evidence-only checkpoint retained the exact Chainguard 18.4
index and linux/arm64 manifests under
`bootstrap/postgresql/v18.4/evidence/`. The
closed checksum set includes separate index and arm64 message-signature bundles,
the raw attestation manifest and SLSA/SPDX DSSE envelopes, standard Sigstore
bundle v0.3 records, a retained Sigstore TrustedRoot, an independent CycloneDX
SBOM, an exact-image Trivy report, and a CycloneDX-input Trivy report that
covers all 56 Wolfi and four Go libraries. Both scans report zero High and zero
Critical findings. Cosign 3.1.1 verifies all four retained bundles without
registry access or TUF retrieval. That checkpoint authorized evidence review
only and left resident admission, Flux/runtime resources, and credentials
blocked.

The subsequent atomic-admission checkpoint repeated anonymous availability
preflight for the exact Polaris and PostgreSQL references and rescanned the same
PostgreSQL arm64 digest in both exact-image and CycloneDX-input scopes. Each
vulnerability database was no more than 24 hours old, complete 56 Wolfi plus
four Go library coverage was retained, and both reports remained
High=0/Critical=0. The CycloneDX-input report also retains one UNKNOWN finding:
`CVE-2026-39824` in `golang.org/x/sys` `v0.1.0`, fixed in `0.44.0`. The
decision receipt records `unknown=1`; the finding does not fail the
High/Critical gate and remains a runtime-acceptance monitoring item. The
preflight, fresh reports, reviewed evidence, and exact pair are bound under
`security/evidence/polaris-v1.6.0-postgresql-v18.4/`.

Both exact digests now enter `security/resident-images.json` in one atomic
change. This closes only the resident-image admission boundary. WP-L1-LAKE-002
remains incomplete until the catalog Kustomization depends on
`shirokuma-object-storage`, credential-safe runtime resources reconcile, and
live Ready plus catalog create/list/read and backup/restore evidence is
recorded. Runtime manifests and credentials remain blocked, and Issue #61
remains Open through runtime acceptance.

The Admin build-input prerequisite adds two review boundaries before runtime:
the main-only dependency publication and its evidence-only review, followed by
a separate Admin image publication/admission lifecycle. The historical server
dependency publisher stays retired; it is not rerun or mutated. Its reviewed
OCI digest is only the immutable parent for a new, independently signed
superset. A failed Admin build must leave the parent and the admitted
Polaris/PostgreSQL pair unchanged.

PR #86 fixed the Admin publication contract on main at
`619d52e0b1db5241867d7775cc8714a30b1a6f38`. Main run `29781460117`, attempt
`1`, successfully published and anonymously retrieved the exact public
dependency superset
`ghcr.io/tommykammy/shirokuma-polaris-admin-gradle-dependencies@sha256:7a505defcd78c7a7b978e88cd4c72e0a5d8b69cbb57ddd311c163b09fe789d18`.
Actions artifact `polaris-admin-publication-29781460117-1` (artifact ID
`8477021002`, Actions digest
`sha256:d1d33b14467a58b93796568667ab68ad3f61a12f9f9c3af439bbd6361adee621`,
582,463 bytes) contains only the 12 retained evidence records; it does not
contain the dependency archive. The 701,437,153-byte
`polaris-gradle-dependencies-1.6.0.tar.gz` originated in one-day candidate
artifact `polaris-admin-candidate-29781460117-1` (artifact ID `8476975401`) and
is the second OCI layer. Independent anonymous exact-digest retrieval verified
its SHA-256
`e771fe2ec6b2d0f6940b1247a512eb5cbc78dd0f36e7be247975f2c5fa36fc4d`, size,
and gzip structure before evidence review.

PR #87 merged that 12-file evidence review as
`8e5c6927e95d1027e16fe2ac27ab8322b45359c9`, retired the write-capable Admin
dependency publisher, and approved the exact public dependency superset only
for Admin image building. PR #88 merged as
`0fca9059179900a6d236961c1d595a66e752fb3e` and consumes
`ghcr.io/tommykammy/shirokuma-polaris-admin-gradle-dependencies@sha256:7a505defcd78c7a7b978e88cd4c72e0a5d8b69cbb57ddd311c163b09fe789d18`,
records `admin_image_publication_pending` with next state
`admin_image_evidence_review_pending`, and targets repository/tag
`ghcr.io/tommykammy/shirokuma-polaris-admin:1.6.0-arm64`. No exact Admin image
digest exists as review authority until the main publication and separate
evidence-only review complete.

The first main publication run
[`29798208118`](https://github.com/TommyKammy/Shirokuma/actions/runs/29798208118)
proved the fresh offline build, closed context, native arm64 image, Admin CLI
help smoke, and CycloneDX generation. Its blocking Trivy scan then found 19
High and zero Critical OS-package vulnerabilities in the pinned Amazon Linux
2023 base: `glib2` 7, `libacl` 2, `python3` 5, and `python3-libs` 5. The run
failed before signature, provenance, retained candidate evidence, and
promotion. Quarantine digest
`sha256:78a4d4f4609dfc58d6c43526ab9ea198dea2427415ad7ce86fbf2e34e76b9a84`
is rejected and is not evidence-review or admission authority.

On 2026-07-21 the Admin-only Alpine 3.24 repin above was approved. Registry
inspection records Corretto `21.0.11.10.1`, native linux/arm64, and the
`/usr/bin/java` symlink; a focused Trivy 0.72.0 scan reports
High=0/Critical=0. These are selection observations only. The corrected main
publication must generate the authoritative digest and complete every gate.

PR #90 merged the checksum-manifest closure repair as
`a1339e71bc3a19814102bd689fb88bfab4fb71c5`. Corrected main run
[`29807128630`](https://github.com/TommyKammy/Shirokuma/actions/runs/29807128630)
attempt `1` completed every publication gate for exact Admin image
`ghcr.io/tommykammy/shirokuma-polaris-admin@sha256:a56d09406c9dc1602cc49c0e792035c1163abf0e975fe702ef7e775c445317dd`.
PR #91 merged the separate evidence-only checkpoint as
`2dfc02dde2d00226012500308f771326ee6b30df`. It retains 34 payloads plus one
checksum manifest, re-verifies signature and SLSA identity against the exact
main workflow SHA, retires the one-shot publisher, and transitions only to
`admin_image_admission_pending`.

The next decision admits that exact Admin digest independently of the existing
atomic Polaris/PostgreSQL receipt. It records a new empty-Docker-config anonymous
preflight, requires the retained Trivy database to be no more than 24 hours old
at the decision, copies the exact reviewed CycloneDX and Trivy payload bytes into
a closed resident-evidence directory, and adds one canonical `polaris-admin`
entry to `security/resident-images.json`. No vulnerability exception is used;
the exact image remains High=0/Critical=0 across 29 Alpine and 377 JAR packages.
The post-decision state is `admin_runtime_activation_pending`, not runtime
acceptance. This does not amend the separation between image admission, runtime
activation, Flux resources, and credential provisioning. Issue #61 remains Open.
The later runtime activation must mount an externally provisioned Secret and use
the Admin Tool's credential-file input. Credentials in command arguments,
generated credentials printed to logs, image layers, publication evidence, or
server-side relational auto-bootstrap do not satisfy this decision.

The runtime activation decision is a static desired-state boundary after PR #92
merged the independent Admin admission as
`47ce8ad6b58f1ab5f0d7c12e5813125804b7651c`. OpenTofu owns two external
Secrets in `shirokuma-dev`: one for PostgreSQL connection material and one
for the Polaris root credential file. Flux orders
`shirokuma-object-storage`, the PostgreSQL StatefulSet, the bounded Admin
bootstrap Job, and the Polaris Deployment through three bounded
`Kustomization.spec.dependsOn` edges with explicit health checks. The Job
mounts only `credentials.json` read-only with mode `0440` and invokes only
`bootstrap --credentials-file=...`; singular credential arguments, realm
arguments, and credential output remain forbidden. The hash-closed
`security/polaris-runtime-activation.json` state is
`runtime_acceptance_pending`. It does not claim live Flux readiness, catalog
API success, backup/restore, rollback, teardown, or Issue #61 completion.
The Flux root explicitly lists the three catalog Kustomizations. A reviewed
`polaris-runtime-generation` ConfigMap is the single non-secret generation
source consumed by OpenTofu and all catalog Pod templates. In-place Secret data
updates are ignored because PostgreSQL role and Polaris root credential changes
cannot be made safe by Pod restart alone; credential replacement requires a
reviewed catalog rebuild, Job recreation, and backup/restore acceptance. The
metadata StatefulSet reserves a retained `5Gi` PVC in addition to SeaweedFS's
`20Gi` claim.

## Verification

The source-build checkpoint must pass:

    make test-polaris-build-contract
    make verify-polaris-build-contract
    make verify-polaris-admin-build-inputs-contract
    python3 -m unittest -v tests.test_arm64_compatibility_matrix
    python3 scripts/verify_design_context.py
    make verify-security

The evidence-only checkpoint must pass the repository trusted-image verifier
using pinned Cosign against every retained bundle and the exact main workflow
identity. The atomic admission checkpoint additionally binds the reviewed
PostgreSQL evidence, <=24-hour zero High/Critical exact-image and
CycloneDX-input rescans that close all 60 libraries, both exact digests, and the
resident-image records in one change. Runtime remains a separate checkpoint
that owns `make verify`, `make verify-gitops-bootstrap`, and live
`make gitops-status` evidence.

The static activation checkpoint additionally runs
`python3 scripts/verify_polaris_runtime.py audit --root .`. It closes the
manifest hashes, exact admitted image set, external Secret references, Admin
credential-file command, Flux dependency/health chain, and explicit incomplete
live-acceptance state. Live acceptance remains a later checkpoint.

For the Admin dependency snapshot, the evidence-only checkpoint additionally
hash- and size-binds all 12 retained files, the main run and attempt, the exact
public OCI digest, anonymous retrieval, the offline Admin/server regression
build, and the schema-v2 publisher-retirement transition.

## Rollback

Before runtime deployment, roll back admission by removing both resident-image
records and reverting their atomic evidence and admission state in one change;
never leave one half admitted. Keep the retired publisher and runtime manifests
absent, so no cluster or metadata state needs recovery. If either digest or
evidence becomes invalid, revoke the pair and rebuild only from the accepted
source or a re-resolved signed PostgreSQL image. After deployment, take the
documented PostgreSQL backup before reverting Flux resources.

## Related

- `docs/design/07_ADR/ADR-0018_Use_Flux_v2_as_the_GitOps_reconciler.md`
- `docs/design/07_ADR/ADR-0020_Adopt_SeaweedFS_4_39_source_for_arm64_build.md`
- `docs/design/04_Development/049_Supply_Chain_Security.md`
- `docs/design/10_Research/106_ARM64_Container_Image_Compatibility.md`
- `docs/design/06_WorkPackages/L1/WP-L1-LAKE-002_Polaris_catalog_bootstrap.md`
