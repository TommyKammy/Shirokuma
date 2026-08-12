---
project: Shirokuma
doc_id: "DEV-049"
title: "Supply Chain Security"
status: draft
created: 2026-07-05
updated: 2026-08-08
version: "1.39"
area: "development"
tags: [shirokuma, security, supply-chain]
---

# Supply Chain Security

## Threat model

AI Coding Agentは、善意で悪性コードを実行するリスクがあります。特に、unknown repository、postinstall scripts、curl|bash、obfuscated scripts、malicious branch names、package typosquattingに注意します。

## Controls

| Control            | Tool/Practice                              |
| ------------------ | ------------------------------------------ |
| Dependency pinning | lock files, digest pinning                 |
| SBOM               | syft                                       |
| Vulnerability scan | osv-scanner, grype, trivy                  |
| Secret scan        | gitleaks                                   |
| Sandbox            | devcontainer, no host mount secrets        |
| Install review     | dependency changes require human review    |
| Script allowlist   | only known scripts in AGENTS.md            |
| Network controls   | no arbitrary outbound in CI where possible |

## Pull request blocking baseline

`make verify-security` is the deterministic local entry point and is also part of
`make verify`. It rejects secret-like tracked filenames and contents, validates
the resident image evidence ledger, and runs focused unsafe-input fixtures. The
pull request workflow adds commit-range Gitleaks scanning over a complete
checkout plus Trivy filesystem scanning for dependencies, secrets, and
misconfiguration. Gitleaks v8.30.1 is downloaded from its immutable release URL,
verified against the committed archive SHA-256, executed at `info` log level,
and retains its redacted SARIF report for 30 days. This keeps secret coverage
over large retained evidence without allowing scanner debug output to amplify
multi-megabyte single-line records in the Actions log. Pull requests scan the
complete merge-base-to-head reachability difference, and protected-branch pushes
scan the complete before-to-head reachability difference. Neither range uses
first-parent or no-merge filters, so merge commits and their second-parent PR
history remain covered. Git `-m` emits separate merge patches so changes created
only during merge resolution are scanned as well. If a force-push leaves the
previous tip unavailable locally, the push gate scans the complete reachable
HEAD history instead of silently dropping coverage. Any High or Critical
finding is blocking; a separate non-blocking all-severity Trivy pass keeps lower
severities visible in the workflow log for follow-up. Scanner
errors, malformed reports, unavailable feeds, and missing prerequisite evidence
fail closed rather than silently reducing the gate.

Flux v2.9.2が生成する
`deploy/gitops/clusters/local-lite/flux-system/gotk-components.yaml`の
cluster-wide controller RBACに対してのみ、Trivy `KSV-0041`と`KSV-0046`をsingle-user
local labの期限付き例外とします。`.trivyignore.yaml`はcanonical YAMLで
記述し、`scripts/verify_trivyignore.py`が完全一致する2つのID、単一の完全一致path、
2つのexact `statement`、`2026-08-14`のUTC calendar date `expired_at`から生成した
canonical bytesとの完全一致と、非期限切れかつ30日以内というcontractをfail closedで
検証します。Trivy v0.72.0と同じくdate scalarをUTC midnightへ変換するため、
effective expiry instantは`2026-08-14T00:00:00Z`です。この瞬間は有効で、1秒後は
期限切れとなります。期限更新にはvalidator codeとignore fileの同時reviewが必要です。
all-severityのreport scanは例外を適用せずfindingを
ログへ残し、High/Criticalのblocking scanだけがこのignore fileを使います。
期限到来またはID/path/schemaの拡張は`make verify-security`を失敗させます。

The actions and scanner releases in `.github/workflows/security.yml` are pinned.
Updates must be isolated dependency changes with review of upstream release
notes and a failing fixture before the pin is advanced.

Repository-controlled image builds additionally use a closed-world contract for
repository-selected release tools and SHA-pinned Actions over an explicit,
non-hermetic GitHub-hosted runner substrate. The contract enumerates the complete
workflow-file and Containerfile hashes, Dockerfile frontend, base images,
Buildx, BuildKit image digest and platform manifest, Syft, Trivy, Cosign, and
the promotion tool. A
repository-selected release tool absent from the contract is not permitted.
Static validation also binds the adopted source record to the workflow before
any build starts: the global commit, tree, and archive pins must equal
`source.json`, and the source checkout repository and ref must equal that same
record. Repository coordinates must be a literal GitHub owner/name slug; runtime
expressions are forbidden. The three source pins may occur only in the canonical
top-level `env`; job- and step-level shadowing is rejected. The complete job set
and canonical block structure are closed by the contract. Every `jobs.*.steps`
entry must start with a non-empty `name`; unnamed
`run` or `uses` entries are rejected, and the complete ordered step-name set is
closed by the contract. The retained Trivy image scan is likewise fixed to
`vuln`, `HIGH,CRITICAL`, `ignore-unfixed=false`, `vuln-type=os,library`, and
`exit-code=1`; changing the workflow and its recorded hash together cannot
weaken these semantic filters.
Docker, GitHub CLI, Git, Python, curl, tar, sha256sum, and other operating-system
facilities supplied by the runner remain part of that trust boundary; the
security-relevant direct tools, runner label, OS, and architecture are recorded
instead of being misrepresented as independently pinned. Standalone release archives are downloaded without registry
credentials, checked against an exact SHA-256 before extraction or execution,
and only then made available to a credentialed step. The generated toolchain
record must reconcile observed versions and image digests with the contract.
The verified Buildx binary is installed under a run-private
`DOCKER_CONFIG/cli-plugins` directory so Docker cannot silently select the
runner's preinstalled plugin. Because GitHub's provenance publisher reads the
default Docker config rather than that isolated directory, the workflow mirrors
the already-issued GHCR credential only for the publisher step and restores or
removes the default config in an `always()` cleanup step. Cosign writes the image
signature bundle to both the durable evidence path and the OCI referrer; the
workflow downloads the registry copy and requires an exact structural match
before promotion. The CycloneDX SBOM and Trivy scan are also retained as v0.3
DSSE attestation bundles and verified before candidate retention. Workflow signer
SHA (`GITHUB_WORKFLOW_SHA`) and source SHA (`GITHUB_SHA`) are recorded and
verified as separate identities. The current contract explicitly selects the
Rekor v1 public API; a Rekor v2 migration must change the endpoint, identity
schema, validator, and fixtures together.
Rekor v1 REST responses are not retained as an immutable whole: the inclusion
proof and checkpoint can evolve as the transparency log grows. Promotion
therefore compares only the immutable entry identity (`UUID`, `body`,
`integratedTime`, `logID`, and top-level `logIndex`) across the retained
response, the fresh public response, and the signed Sigstore bundle. The
tree-local inclusion-proof `logIndex` is also bound across all three inputs.
Each retained and fresh response must still carry a structurally valid
inclusion proof whose own index is within its tree bounds. The proof index is
not the same coordinate as the top-level entry index, and a newly returned
`signedEntryTimestamp` is not a cross-response identity. The signed bundle is
cryptographically verified separately. Whole-response equality is not an
admissible promotion control.
Runtime smoke output and raw container inspection remain run-private temporary
data and are deleted by cleanup; neither is publication evidence. The retained
smoke-log policy records the sanitized-content hash and size, the exact
redaction count, the forbidden credential classes, and that no raw or sanitized
log was retained. The retained container inspection is an allowlist projection
of the image reference, process identity, and hardening controls. Candidate
retention and promotion both reject raw logs, raw inspection data, unexpected
files or keys, credential-shaped output, and projection drift.
The pending static-audit lifecycle does not require a local Cosign binary,
because no admitted bundles exist yet. Once admission becomes `approved`, the
repository verifier fails closed unless the exact contract-pinned Cosign is
available and all retained bundles pass cryptographic revalidation.
The source record itself is hashed into release evidence. Its exact
Containerfile digest and closed set of frontend, Go builder, and certificate
image inputs must all be consumed by the Containerfile before publication. The
validator folds Dockerfile continuations into logical instructions, requires
the sole first-line syntax directive, rejects alternate parser directives and
heredocs, and parses the complete global image-ARG set and every FROM stage. An
obsolete pin left in a comment or continuation body is not evidence that the
build consumes it. Each stage has a closed instruction sequence; the builder
has one exact network-disabled vendor-verification and Go-build RUN, the
certificate stage has no added instruction, and the scratch stage fixes every
COPY, user, entrypoint, and command. The build action fixes its complete input
mapping, including the reviewed context and Containerfile, and may pass only
`SOURCE_COMMIT` and `GO_VENDOR_BUNDLE_SHA256`; alternate files, contexts, extra
inputs, and reviewed base-image ARG overrides are forbidden.
When an adopted Go source tree does not contain a root vendor directory, the
trusted build must retain a deterministic vendor archive and a
replacement-aware module/file manifest in Git. The archive hash is checked both
before it enters the build context and inside the Containerfile. Pull-request
audit fully extracts every retained archive member. It also checks out the exact
recorded source commit and tree, selects Go `1.25.12`, creates fresh `GOMODCACHE`,
`GOCACHE`, `GOPATH`, and `HOME` directories, and downloads only the modules
required by the vendored package set from `https://proxy.golang.org` with
`sum.golang.org` authentication. Private,
direct-VCS, ambient Go-environment, workspace, and toolchain fallback are all
disabled. The first `go mod vendor` therefore authenticates every downloaded
module needed by the vendored package set. The gate checks the 496 actual
vendored module records, including versioned replacements, against the pinned
upstream `go.sum`, runs `go mod verify`, then switches to `GOPROXY=off` and
`GOSUMDB=off` and regenerates `vendor` again. Both generated trees must match
every retained path, size, mode, and SHA-256 value—including
`vendor/modules.txt`. Network or checksum-service failure is fail-closed; it
does not authorize a fallback. The same regeneration gate runs in the main
publisher before registry credentials exist. Compilation
must use `--network=none`, `-mod=vendor`, `GOPROXY=off`, `GOSUMDB=off`,
`GOTOOLCHAIN=local`, and disabled VCS so neither first-build availability nor
ambient module-cache state is an unrecorded input.

The networked module download is a provenance-regeneration audit, not a build
input. The admitted image is still compiled only from the reviewed retained
archive with networking disabled. A future fully offline provenance audit would
also need a reviewed module-proxy artifact; until then, proxy or checksum-database
unavailability blocks regeneration instead of weakening it.

Polaris 1.6.0 uses a separate fail-closed Gradle checkpoint. The static contract
pins the ASF source archive and SHA-512, the retained ASF release-signing key,
the release tag, commit, and tree, plus Java 21 and Gradle 9.6.0 requirements.
The signed upstream source archive does not contain `gradle-wrapper.jar`,
dependency lock files, or Gradle dependency-verification metadata. It is
therefore not a closed build input by itself.

Reviewed main run `29689013375` from commit
`4692bab4282dfde2c8d4082e6d706dee9ce79324` completed the one-shot dependency
publication. PR #78 merged the evidence-only review as
`b12593f27ae4e6ec8b64865f9b6b0bbf114ec654`. The schema-v4 contract and
admission record are now in `image_publication_pending`; the dependency snapshot
is `approved_for_image_build` but remains `admitted=false`. They bind the exact
public OCI reference
`ghcr.io/tommykammy/shirokuma-polaris-gradle-dependencies@sha256:fa889d2c0a6e6dc48816d79680a366e21040be333ab6007b88e4ca4dbf6e59d6`
and the retained publication record. The publication remains non-admitted even
though anonymous exact-digest retrieval succeeded.

The one-shot publisher is retired. The repository verifier requires
`.github/workflows/polaris-gradle-dependencies.yml` to remain absent and the
historical publisher record to declare `retired=true`. It hash- and size-binds
the descriptor, Gradle verification metadata, raw OCI manifest, publication
record, Cosign signature bundle, registry verification, SLSA verification,
offline-build proof, and toolchain record. It also revalidates their archive,
manifest, source-run, keyless identity, provenance, and offline-build bindings.
Both retained Sigstore checks constrain the workflow repository, ref, trigger,
and exact publisher workflow SHA
`4692bab4282dfde2c8d4082e6d706dee9ce79324`; the mutable `main` workflow
identity alone is not sufficient.
`make test-polaris-build-contract` injects a mock only at the external
cryptographic-command boundary so the unit suite needs no host Cosign binary;
`make verify-polaris-build-contract` and `make verify-security` share an
explicit `verify-cosign` prerequisite that requires Cosign v3.1.1 before either
unmocked retained-evidence audit runs. No production skip flag exists.

The Polaris Admin Tool uses a separate additive dependency lifecycle; the
retired server dependency publisher is not restored. Its reviewed OCI
snapshot
`ghcr.io/tommykammy/shirokuma-polaris-gradle-dependencies@sha256:fa889d2c0a6e6dc48816d79680a366e21040be333ab6007b88e4ca4dbf6e59d6`
is an immutable parent seed only. The Admin contract binds the parent's
descriptor `sha256:3bab7b055d29be1bc59f2fe605960f49bbceee2639ad68086822c62ee8533841`,
cache layer `sha256:18933bfb895c267302f1ee1c80cfb9712eac736ffcefade48dac53f79e8e3bc0`,
verification metadata
`sha256:b8b1fa91bc9d98eaf676dbab76c5452411fcdf6b11a8c9959c131799c71deaf2`,
and review merge `b12593f27ae4e6ec8b64865f9b6b0bbf114ec654` before resolving a
new self-contained superset.

The superset must build `:polaris-admin:assemble` and
`:polaris-admin:quarkusAppPartsBuild`, then repeat
`:polaris-server:assemble` and `:polaris-server:quarkusAppPartsBuild`.
Only a fresh network-none, offline, strict-verification build can become a
review-pending build input. Parent-cache reuse without the new complete
descriptor and verification metadata is insufficient. The upstream Admin
graph's unconditional NoSQL/MongoDB modules remain an explicit
`review_required` surface; this checkpoint neither publishes an image nor
claims a relational-only runtime.

Pull requests exercise only read-only contract validation. Package and OIDC
permissions are available only to the `refs/heads/main` publisher, which must
prove repository, event, ref, source SHA, and workflow SHA before any
third-party action or explicit token reference. All actions are fixed to
full-length commit SHAs and their exact count is closed by the verifier. The
publisher may create only a run-scoped immutable OCI artifact and
review-pending evidence. Admin image publication, resident-ledger changes,
runtime manifests, Flux reconciliation, and credentials remain forbidden until
their later reviewed checkpoints.

The Admin dependency publisher is one-shot at the lifecycle level: every
attempt uses an immutable `run_id` / `run_attempt` tag, and the evidence-review
PR must retire the publisher. It is not limited to one execution attempt.
Because a newly created GHCR package is private by default, the first signed
and attested attempt may fail closed at the anonymous-pull gate. The only
permitted recovery is for the owner to make that exact package public and
rerun before evidence review; failed attempts are not admitted, and registry
credentials are never an anonymous-pull fallback. Static contract tests run
before the lifecycle gate on every invocation, and the observed Gradle and
Java versions must match the pinned toolchain before candidate evidence is
retained.

PR #86 merged the reviewed publisher contract as
`619d52e0b1db5241867d7775cc8714a30b1a6f38`. Main run `29781460117`, attempt
`1`, completed the fresh offline Admin and server regression builds, published
the exact public OCI artifact
`ghcr.io/tommykammy/shirokuma-polaris-admin-gradle-dependencies@sha256:7a505defcd78c7a7b978e88cd4c72e0a5d8b69cbb57ddd311c163b09fe789d18`,
and proved anonymous exact-digest retrieval. Actions artifact
`polaris-admin-publication-29781460117-1` (artifact ID `8477021002`, Actions
digest `sha256:d1d33b14467a58b93796568667ab68ad3f61a12f9f9c3af439bbd6361adee621`,
582,463 bytes) contains only the 12 retained evidence records; it never carried
the dependency archive. The 701,437,153-byte
`polaris-gradle-dependencies-1.6.0.tar.gz` came from one-day candidate artifact
`polaris-admin-candidate-29781460117-1` (artifact ID `8476975401`) and became
the second OCI layer. An independent anonymous exact-digest pull verified its
SHA-256 `e771fe2ec6b2d0f6940b1247a512eb5cbc78dd0f36e7be247975f2c5fa36fc4d`,
size, and gzip structure. The publication Actions artifact is a
finite-retention evidence transport copy, not the durable review authority.

PR #87 merged the evidence-only review as
`8e5c6927e95d1027e16fe2ac27ab8322b45359c9`. It retains 12 hash-bound files
under `bootstrap/polaris/v1.6.0/admin-build-inputs-evidence/`, retires the sole
write-capable Admin dependency publisher, and approves only the exact public
dependency input
`ghcr.io/tommykammy/shirokuma-polaris-admin-gradle-dependencies@sha256:7a505defcd78c7a7b978e88cd4c72e0a5d8b69cbb57ddd311c163b09fe789d18`
for an Admin image build. It does not admit an image or enable runtime.

PR #88 merged as `0fca9059179900a6d236961c1d595a66e752fb3e` and records lifecycle state
`admin_image_publication_pending` with next state
`admin_image_evidence_review_pending`. Its bounded policy surface is
`bootstrap/polaris/v1.6.0/admin-image-contract.json`,
`bootstrap/polaris/v1.6.0/Containerfile.admin`, and
`.github/workflows/polaris-admin-arm64.yml`. The main-only workflow may publish
`ghcr.io/tommykammy/shirokuma-polaris-admin:1.6.0-arm64`; the mutable tag is
never review authority, and no exact Admin image digest is approved before the
separate retained-evidence review.

The first main publication run
[`29798208118`](https://github.com/TommyKammy/Shirokuma/actions/runs/29798208118)
built and smoke-tested quarantine digest
`sha256:78a4d4f4609dfc58d6c43526ab9ea198dea2427415ad7ce86fbf2e34e76b9a84`,
then stopped at the blocking Trivy 0.72.0 scan. The Amazon Linux 2023 runtime
base contributed 19 High findings (`glib2` 7, `libacl` 2, `python3` 5, and
`python3-libs` 5) and zero Critical findings. Promotion, keyless signing,
provenance, retained candidate evidence, and trusted-tag publication were
skipped. The quarantine digest is not review authority and cannot enter an
evidence-only review or admission record.

The approved correction does not waive or ignore those findings. The Admin
image alone will repin to the Docker Official Image for Amazon Corretto 21 on
Alpine 3.24: index
`sha256:30b1b2246cee9a98c9bf8a11537a04f1eaf8c59279b0c70ae02d7e5b934edeaa`
and linux/arm64 manifest
`sha256:dc43b39c47f1729dc772a9b8af7222757fac6c8cfa8a0802829af665b1c89925`.
Image history pins Corretto `21.0.11.10.1`, `/usr/bin/java` is present, and a
focused Trivy 0.72.0 scan reports High=0/Critical=0. The mutable `21-alpine`
tag is discovery input only; the contract, Containerfile, workflow, verifier,
and build must use the exact digests and repeat all publication checks on main.

PR #89 merged as `fe00970d75c2022c51f80cb5f00021778e8312e1` and applied that
Admin-only Alpine repin. Main run
[`29802331708`](https://github.com/TommyKammy/Shirokuma/actions/runs/29802331708)
then completed the offline build, arm64 image and CLI smoke, SBOM, Trivy
High=0/Critical=0 gate, anonymous exact-digest retrieval, Cosign signature,
SLSA provenance, attestations, candidate retention, and non-authoritative
trusted-tag move for digest
`sha256:16e3fd99da2afd446463405bd59236322c37bb066b2af5f46f6e3dd5b7c8710b`.
Final retention still failed closed: redirecting the new `evidence.sha256`
inside the evidence directory made `find` include the manifest being written,
so its self-referential checksum failed. No final artifact was retained and the
digest has no review or admission authority. The repair must stage the manifest
outside the closed directory, move it into place only after payload hashing,
and retain a regression that rejects direct self-hashing output.

PR #90 merged the closure repair as
`a1339e71bc3a19814102bd689fb88bfab4fb71c5`. Main run
[`29807128630`](https://github.com/TommyKammy/Shirokuma/actions/runs/29807128630)
attempt `1` then completed prepare, verify, and promote for exact Admin digest
`sha256:a56d09406c9dc1602cc49c0e792035c1163abf0e975fe702ef7e775c445317dd`.
The final artifact (ID `8486076696`, Actions digest
`sha256:9acfbe58503852943fc075f33a73286993be30702e235604c814202e108686db`,
expiry `2026-08-20T06:37:23Z`) contains exactly 34 payload records plus the
checksum manifest. The retained manifest SHA-256 is
`f1290ccf0fff852fb965d46ab55c12623ce15e36e15b4bbeb6627999bf11a97f`.
PR #91 merged the evidence-only review as
`2dfc02dde2d00226012500308f771326ee6b30df`. It independently rechecked every
payload, exact workflow identity/SHA, Cosign/Rekor, SLSA v1, CycloneDX 1.7,
Trivy High=0/Critical=0, and credential-free CLI smoke, then retired the
one-shot publisher and advanced only to `admin_image_admission_pending`.

The separate admission checkpoint re-proves anonymous exact-digest retrieval
with an empty Docker config and binds a Trivy 0.72.0 database updated fewer than
24 hours before the decision. It copies the exact reviewed CycloneDX and Trivy
payload bytes into `security/evidence/polaris-admin-v1.6.0/`, closes that
directory with a five-entry checksum manifest, and adds only
`ghcr.io/tommykammy/shirokuma-polaris-admin@sha256:a56d09406c9dc1602cc49c0e792035c1163abf0e975fe702ef7e775c445317dd`
to `security/resident-images.json`. The admission record binds the reviewed
contract, release evidence, 35-file publication closure, anonymous preflight,
CycloneDX 1.7 with 1,618 components, and exact-image scan scopes for 29 Alpine
packages plus 377 JAR packages. Both High and Critical remain zero; no exception
is used. This advances only to `admin_runtime_activation_pending`. Runtime,
Flux resources, credentials, and cluster mutation remain prohibited.

The Containerfile preserves upstream's Quarkus fast-jar layout
`build/quarkus-app/{lib/,quarkus-run.jar,app/,quarkus/}`, runs as
`10000:10001`, and fixes the CLI launcher to
`/usr/bin/java -jar /deployments/quarkus-run.jar` with `--help` as its inert
default. Smoke must exit zero and include
`Usage: polaris-admin-tool.jar [-hV] [COMMAND]`. Upstream's Admin graph includes
the NoSQL maintenance/metastore modules and Quarkus MongoDB client, so the SBOM
and scan must retain that surface rather than claim a relational-only image.

Runtime bootstrap may later use only the official
`bootstrap --credentials-file=<file>` path with an externally provisioned,
read-only Secret. The YAML or JSON file maps each top-level realm to non-empty
`client-id` and `client-secret`; file input is mutually exclusive with
`--realm`, singular `--credential`, and `--print-credentials`. Credential
material is forbidden from the image, workflow evidence, command arguments,
and current manifests. Admin image admission and the resident ledger are now
approved for the exact digest; runtime, Flux, and credential gates remain
false, and Issue #61 remains Open.

The image-publication checkpoint adds only the hash-bound
`bootstrap/polaris/v1.6.0/Containerfile`, the bounded downstream source overlay,
and `.github/workflows/polaris-arm64.yml`. The workflow is limited to
`TommyKammy/Shirokuma` on `refs/heads/main`, requires
`GITHUB_WORKFLOW_SHA == GITHUB_SHA`, authenticates the ASF source and exact
dependency OCI before registry credentials exist, performs a fresh
network-none strict offline Gradle build, and publishes to a run-scoped
quarantine tag before exact-digest verification and non-authoritative tag
promotion. PR, reusable-workflow, credential-fallback, and cache-backed build
paths are forbidden.

The UBI 9 Java 21 candidate retained in `source.json` remains a historical,
non-authoritative assessment candidate: a 2026-07-20 Trivy 0.72.0 feasibility
scan found 21 High findings. The selected runtime base is instead the Docker
Official Image for Amazon Corretto 21.0.11, fixed by index
`sha256:d3a3476c19cbe37b2e3e46a2116ff197ab37c7072baad55ee0ad07f3b97e8d02`
and linux/arm64 manifest
`sha256:ba1fe4a3fd4c6b70360183fccd1f0a168c3ea6f73709e8f81945cb9087431ff2`.
The feasibility scan found High=0/Critical=0; the main publisher must repeat
the authoritative `os,library` scan with `ignore-unfixed=false`.

The unmodified Polaris server also carried six High findings through Hadoop
3.5.0 and Ranger runtime dependencies. Shirokuma does not use Hadoop external
catalog federation, HadoopFileIO, or Ranger authorization in the bounded
SeaweedFS S3/OPA profile. The reviewed overlay therefore removes exactly those
runtime edges after pristine source authentication and binds both affected
Gradle files by preimage and postimage SHA-256. The workflow rejects Hadoop,
Ranger, and Jetty HTTP jars or SBOM components and does not use a vulnerability
exception. A local fresh network-none build, Java 21 check, High=0/Critical=0
scan, and non-root read-only readiness smoke passed; only the main run may
produce reviewable publication evidence.

Polaris image release evidence is retained at
`bootstrap/polaris/v1.6.0/image-evidence/` and the write-capable publisher is
retired. Its evidence-only checkpoint advanced the contract to
`atomic_admission_pending` and marked only the exact image digest
`approved_for_atomic_admission`. The later atomic-admission checkpoint now
admits that digest only as one half of the exact Polaris/PostgreSQL pair. This
resident-image decision does not permit runtime manifests, credentials, or a
cluster mutation.

Within `caches/modules-2/files-2.1`, the checksum directory follows Gradle
9.6's canonical artifact-store rule: SHA-1 is lowercase hexadecimal with every
leading zero digit removed. The packager computes that layout identity and the
authoritative SHA-256 from the same safely opened file stream, requires the
canonical SHA-1 identity to match the observed directory, and repeats that
binding while verifying the archive. SHA-1 is used only to reproduce Gradle's
cache layout; dependency trust continues to require the exact SHA-256 recorded
in strict Gradle verification metadata. Padded leading-zero aliases, arbitrary
digest directories, uppercase or nonhexadecimal values, and identities longer
than the 40-digit SHA-1 representation fail closed.

The observed `files-2.1` tree is a candidate cache, not the retained allowlist.
Repository probing can leave canonical cache files that were not consumed by
the resolved graph and therefore are absent from the generated verification
metadata. The packager retains only the GAV, filename, and SHA-256 closure
declared by that metadata and omits other canonical residues from both the
descriptor and archive. A verification record without exactly one retained
checksum match, a noncanonical cache identity, or a duplicate retained
coordinate still fails closed. Scan limits apply before projection, and the
descriptor records scanned, retained, and excluded counts and byte totals with
closed arithmetic. The deterministic dependency tar permits only its canonical
long-path PAX record: an exact file path or the exact directory path with its
POSIX trailing slash. The subsequent fresh network-none build proves that this
reduced cache plus the reviewed `metadata-2.107` root is sufficient for the
exact offline server build.

Before either source extraction, a standalone validator whose path and SHA-256
are pinned by the contract parses the authenticated archive without writing
files and checks the bounded member policy. Only regular files, explicit
directories, and relative symbolic links are admitted; paths must be printable
canonical POSIX paths under the single release root. Compressed and decompressed
size, raw tar headers and control records, logical member count, individual and
total regular-file size, path and component length, path depth, link length, and
PAX metadata are capped before extraction; each length-prefixed PAX payload must
also be consumed exactly with no trailing data. Duplicate paths, hidden GNU
name records, Solaris PAX records, implicit or non-directory parents, hard
links, special files, unknown PAX headers, and members below a symbolic-link
path fail closed. Every
symbolic-link target must name an existing archive member, remain under the
release root both before and after `--strip-components 1`, and resolve without a
cycle. Each extraction is separately bound to a fresh directory with owner and
permission restoration disabled. This admits the eight authenticated in-root
links in the ASF Polaris 1.6.0 release without turning source extraction into a
general symbolic-link bypass.

A 2026-07-18 workstation feasibility audit completed the two server tasks in a
clean source extraction with Docker networking disabled and Gradle `--offline`.
The reduced dependency seed still contained 5,014 files and 825,947,131 raw
bytes; deterministic compression produced 619,659,126 bytes, above GitHub's
normal single-file limit. This observation is not build admission evidence.

Main run `29689013375` subsequently produced the reviewed 5,412-file canonical
archive, 701,323,251 bytes with
SHA-256 `18933bfb895c267302f1ee1c80cfb9712eac736ffcefade48dac53f79e8e3bc0`,
and retained it in the signed immutable OCI artifact rather than Git. The exact
nine-file evidence set records the descriptor, verification metadata, raw
manifest, publication, signature, registry verification, SLSA verification,
offline-build proof, and toolchain. Anonymous exact-digest retrieval, the
network-disabled strict offline build, Cosign verification, and SLSA
verification all succeeded. The schema-v3 contract binds these records and the
retired publisher's historical bytes; that provenance record is not authority
to restore or rerun the publisher.

The next image build must pull the exact dependency reference without registry
credentials, verify it before extraction, and keep Gradle network-disabled.
ORAS layer paths remain canonical and relative to the bounded candidate root;
absolute workspace paths and `--disable-path-validation` remain forbidden.
With the pinned Cosign v3.1.1, `cosign sign --bundle` must generate and retain
the signature bundle, while registry-backed `cosign verify` must verify the
exact digest reference, certificate identity, and issuer without passing
`--bundle`. The bundle and registry verification output remain separate,
hash-bound evidence records; neither may be dropped or substituted.
This evidence-only review binds the retained descriptor, verification metadata,
manifest, signature, provenance, and offline-build record to their exact
main-run digests in Git. Merge authorizes only that immutable dependency input
for the next image-publication phase; it does not admit a Polaris image or
runtime. Loss of anonymous retrieval keeps image publication blocked, and a
registry credential must not be added as a fallback.

GHCR packages are private by default, so anonymous exact-digest retrieval is a
mandatory fail-closed gate before evidence may become review-pending. The
retained reference from run `29689013375` is already anonymously retrievable;
no visibility mutation or credential fallback is permitted. Any future loss of
anonymous retrieval blocks image publication. Reintroducing the deleted
dependency publisher is also forbidden: changing the dependency artifact
requires a new explicit publication lifecycle and contract review rather than a
rerun of the historical workflow.

The selected Chainguard PostgreSQL 18.4 index and linux/arm64 manifest first
entered an evidence-only state. That retained closed set binds the raw index,
arm64 and attestation manifests, role-separated index/arm64 Sigstore bundles,
SLSA v1 and SPDX 2.3 DSSE envelopes and bundles, a Syft 1.46.0 CycloneDX 1.7
SBOM, and Trivy 0.72.0 exact-image and CycloneDX-input reports. The latter
closes all 56 Wolfi plus four Go library components. A retained Sigstore
TrustedRoot lets Cosign 3.1.1 reverify all four bundles with an empty HOME and
Docker configuration while network proxies are denied. The SLSA certificate
uses workflow commit `1d360e5f7f3b749f0b1e55b3f75d3eb8db4e7004`; the index,
arm64 and SPDX certificates use
`704e38b436bc40bc9a9d669c05f0d6694bec298b`. These role-specific claims must
not be collapsed into one global workflow revision.

The atomic-admission checkpoint repeated anonymous availability preflight for
the exact Polaris and PostgreSQL references and rescanned the same PostgreSQL
arm64 digest in both exact-image and CycloneDX-input scopes. Each vulnerability
database was no more than 24 hours old; both reports remained
High=0/Critical=0 and retained complete 56 Wolfi plus four Go library coverage.
The CycloneDX-input report also retains one UNKNOWN finding:
`CVE-2026-39824` in `golang.org/x/sys` `v0.1.0`, fixed in `0.44.0`. The
High/Critical gate still passes, but the decision receipt records `unknown=1`
and requires runtime acceptance to monitor the finding.
The checkpoint binds the preflight, fresh scans, reviewed evidence, and both
exact digests under
`security/evidence/polaris-v1.6.0-postgresql-v18.4/`. It adds the Polaris and
PostgreSQL records to `security/resident-images.json` together; either record
appearing alone fails closed.

Atomic resident-image admission is complete, but runtime/Flux manifests and
credentials remain blocked. The next boundary is runtime acceptance: the
catalog Kustomization, credential-safe Secret path, live Ready conditions,
catalog API smoke, and backup/restore evidence must pass before Issue #61 can
complete.

Trusted builds must also set BuildKit `no-cache` and must not import or export a
shared GitHub Actions cache. Reusing a mutable layer that is absent from the
contract and release evidence violates the closed-world claim even when the
source and vendor archive are unchanged.

Trusted-tag publication is main-only and uses two review phases. Feature
branches may validate the static builder and contract, but may not receive the
write-capable publication path or approve their own evidence. After the policy
PR merges, `refs/heads/main` runs a two-stage publication state machine. The
verify job may push
only a run-scoped quarantine tag and must finish source checks, runtime smoke,
SBOM, scan, signing, provenance, and candidate evidence retention. A separate
promotion job receives package-write permission, revalidates the retained
candidate and binds its artifact name, digest, run ID, and monotonic attempt
before credentials exist. It then installs the checksum-verified promotion
tool and moves the trusted tag without changing the digest. A missing gate,
unretained candidate, failed revalidation, or digest mismatch prevents the tag
move. The mutable tag is only a non-authoritative publication pointer: a failure
while generating, validating, or retaining final evidence may leave that pointer
at the new digest, but cannot admit it. Admission requires the immutable digest,
successfully retained final evidence, and the reviewed Git-committed admission
record in a follow-up evidence-only PR. The interval between policy merge and
that evidence PR is explicitly `pending_main_publication`; release evidence is
absent and runtime use is forbidden. Candidate and final artifact names include
both run ID and run attempt so a rerun cannot collide with an immutable earlier
upload. The candidate name remains bound to the verify job's builder attempt,
while promotion and the final artifact record the attempt that actually moved
the tag. A promotion-only retry must stay in the same workflow run and may only
advance, never precede, the builder attempt.
For SeaweedFS 4.39, this transition completed from main run `29418029340`,
attempt `1`, admitting
`ghcr.io/tommykammy/shirokuma-seaweedfs@sha256:d1339701907587c93c6af8740388226ac2277cbbfd3df581c0e85d815c90e421`.
The Git-committed evidence remains insufficient for runtime by itself: parent
Issue #26 must still add the resident source-build record and pass
`check-images`.
The final evidence retains the exact pre-promotion release record as
`candidate-release-evidence.json`; promotion is therefore auditable after the
short-lived candidate artifact expires. Runtime evidence likewise retains raw
Docker inspect output and reconciles the effective user, command, read-only
root, tmpfs mounts, dropped capabilities, security option, and resource limits
instead of trusting a self-asserted smoke-test summary.
For Polaris 1.6.0, main run `29711984394`, attempt `1`, from reviewed main
commit `706575ba3f21987033a29b6d21367981e9c54e3e` published and promoted
`ghcr.io/tommykammy/shirokuma-polaris@sha256:db403e2db7afbe4e8a62261500e229f6d796a420e814564b49f3e14217fd6c9e`.
The mutable `1.6.0-arm64` tag remains a non-authoritative pointer. Final
artifact `polaris-image-publication-29711984394-1` (artifact ID `8449181390`,
Actions digest
`sha256:97c413927e024ff5687350b75ee172a5a890e5423292ce9c6942fd1663d3121e`)
contained 33 files; its 32-entry `evidence.sha256` manifest reverified without
mismatch before the files were fixed in Git. Unlike the SeaweedFS checkpoint
above, the Polaris set excludes the raw smoke log and raw container inspect
because those surfaces can expose temporary credentials; it retains a
secret-scanned log policy and an allowlisted hardening projection instead. The
retained publication record has `promoted=true`, `admitted=false`, and
`state=image_evidence_review_pending`. Evidence review advances the repository
state to `atomic_admission_pending`, retires the publisher, and does not
authorize resident or runtime use.

ADR-0022 selects a repository-owned Trino 483 source build after PR #101
retained the unsigned upstream image as blocked evidence. The exact source is
commit `50b0b50b75abd47f830b7805ee1b51716eb4065e`, tree
`3b5414292a614b12393bb4605ea2d4c588a5b8ee`. Both the tag object and source
commit are unsigned, so these SHAs identify bytes without authenticating the
upstream publisher. ADR-0023 accepts only that exact source-identity gap for the
`mac-studio-solo/local-lite` non-production PoC from
`2026-07-22T22:43:36Z` through `2026-08-21T22:43:36Z`. The authorization is
Issue #63-bound, limited to synthetic/PoC data with no public Service or
Ingress, requires owner/reviewer separation, cannot renew automatically, and
fails closed at expiry. It does not establish upstream authenticity.

The dependency-snapshot contract review completed before the repository enabled
the one-shot `.github/workflows/trino-maven-dependencies.yml` publisher. Pull
requests execute only static, read-only policy validation. A reviewed main push
must revalidate the unexpired Issue #63 authorization before source fetch,
source execution, dependency resolution, publication, and evidence retention.
The workflow binds the exact source coordinates, Maven 3.9.16 and Temurin 25
native-arm64 builder, Maven Central plus the explicit Confluent repository, a
closed Maven manifest plus a separately closed Bun package-cache manifest, and
an independent fresh
`mvn --offline --ignore-transitive-repositories --settings /policy/settings.xml -Dmaven.repo.local=/workspace/.m2/repository -Dmaven.compiler.debuglevel=source,lines --file /workspace/pom.xml -pl '!:trino-docs' clean install -DskipTests`
with networking disabled. The same explicit exclusion is required for both
fresh dependency resolutions and both offline rebuilds: `trino-docs` invokes
Sphinx to generate non-runtime documentation, while all remaining reactor
modules stay inside the Trino server dependency and output boundary. The
same four Maven executions require `--ignore-transitive-repositories`; this
prevents repository declarations from third-party dependency POMs from
expanding the Central/Confluent allowlist. Main run `30068723157` completed the
Trino reactor with `BUILD SUCCESS`, but the transfer audit still detected
`sonatype-nexus-snapshots` while the `git-commit-id` plugin dependency graph
resolved BouncyCastle's `bcutil-jdk18on` version range. Maven 3.9.16 does not
apply `--ignore-transitive-repositories` to that plugin-resolution path.
Repository-owned settings therefore contain exact `central` and `confluent`
mirrors to their allowlisted endpoints followed by a `mirrorOf=*` fallback to
Maven Central. This prevents a third-party POM from reusing an allowlisted
repository ID with a different URL to bypass enforcement. This is not a general
mirror escape hatch. The verifier binds all three mirrors' exact order, IDs,
selectors, names, and URLs; the packager normalizes only those exact mirror IDs
to their corresponding allowlisted origins, and all other repository IDs or
transfer endpoints fail closed. Reviewed-main run `30080444230` proved that
mirror boundary and again completed the Trino reactor with `BUILD SUCCESS`.
The transfer audit then identified the remaining non-Maven input:
`trino-web-ui`, which is a runtime dependency of `trino-main`, uses
`frontend-maven-plugin` to fetch
`bun-linux-aarch64.zip` for Bun `v1.3.14` from GitHub Releases. Excluding that
module would produce an incomplete runtime and is forbidden.

The publisher therefore treats Bun as an explicit external toolchain input,
not as another Maven repository. Each of the two online reconstructions
independently downloads the exact asset, checks size `35700603` and SHA-256
`a27ffb63a8310375836e0d6f668ae17fa8d8d18b88c37c821c65331973a19a3b`,
validates the ZIP member set and types, and only then stages it at the exact
frontend-plugin cache path. The v2 dependency manifest records the Bun URL,
version, platform, cache path, size, digest, dedicated origin ID, independent
download count, allowed HTTPS origins, redirect policy, and redirect limit.
The Bun
origin is valid only for that one cache entry. Redirects are followed manually:
the next HTTPS origin is validated before any request and may be only
`github.com` or `release-assets.githubusercontent.com`, with at most five
redirects. Protocol downgrade, credentials, custom ports, fragments, redirect
cycles, and any other host fail closed before the next outbound request. Reuse
of the Bun origin for any Maven artifact cannot bypass the exact-byte
verification. The workflow records the
future Corretto 25 Alpine 3.24
arm64 base without authorizing image use.

Reviewed-main run `30089478326` authenticated that exact Bun input and completed
the Trino reactor with `BUILD SUCCESS`. Publication still stopped fail-closed
because the original transfer audit classified every URL printed by the build
as an artifact transfer, including webpack's informational
`https://webpack.js.org/guides/code-splitting/` link. The audit therefore
recognizes only Maven 3.9 transfer-listener events beginning with
`Downloading from` or `Downloaded from`. Documentation and plugin-help URLs do
not expand the repository allowlist. A missing or malformed transfer event,
credentials, plaintext HTTP, or any transfer endpoint outside the exact
Central and Confluent origins continues to fail closed.

Reviewed-main run `30101601632` confirmed the refined transfer audit and again
completed the reactor with `BUILD SUCCESS`. Packaging then stopped fail-closed
because Maven stores checksum sidecars such as `*.jar.sha1` without separate
entries in `_remote.repositories`. A checksum sidecar now inherits an origin
only from its exact target artifact or repository metadata entry. The target
must already resolve to an allowlisted origin, and the sidecar must contain
only the correctly sized hexadecimal digest that matches the target bytes.
Orphaned, malformed, mismatched, nested, or origin-conflicting sidecars remain
rejected. Every retained sidecar is still hashed independently in the closed
dependency manifest.

Reviewed-main run `30110292434` then passed that checksum-sidecar boundary and
completed the reactor with `BUILD SUCCESS`, but Maven retained
`common-config-8.1.1.jar.lastUpdated` after an unsuccessful fallback lookup
before resolving the exact artifact from the approved Confluent mirror. A
`*.lastUpdated` file is now excluded as resolver metadata only when its exact
target artifact is present as a regular, single-link file and that target's
`_remote.repositories` entry resolves to an allowlisted origin. The metadata
itself must also be a regular, single-link file within the 64 KiB resolver
metadata limit; an explicit metadata origin must agree with the target.
Orphaned, oversized, nested, symlinked, hard-linked, unknown-origin, or
origin-conflicting resolution status remains fail-closed. The excluded status
bytes are not a build input and are absent from the deterministic archive; the
subsequent fresh network-none builds remain the sufficiency proof.

Reviewed-main run `30124086690` passed both independent online reconstructions
and their complete archive comparison, then showed that the reactor cleanup had
also removed Maven Central's external build extension
`io.trino:trino-maven-plugin:20`. That coordinate is not a Trino 483 reactor
output, but the offline source build requires it while reading the root POM.
The repository pruner now preserves only the exact version-20 JAR and POM when
both are regular, single-link files bound by `_remote.repositories` to the
allowlisted Maven Central origin. It removes every other `io/trino/**` file
produced by the online reactor. The packager and manifest verifier admit only
that exact coordinate. Both snapshot creation and independent manifest loading
require the complete JAR-and-POM pair and require each record to name the exact
Maven Central origin; a missing file, another version, another artifact,
another origin, a link, or any retained Trino 483 reactor output fails closed.

Reviewed-main run `30141163505` then passed both independent online
reconstructions and their complete archive comparison. The first network-none
build reached the Trino root lifecycle but could not resolve
`org.bouncycastle:bcutil-jdk18on:[1.81,1.82)` for
`git-commit-id-maven-plugin:10.0.0`. The closed repository contained the exact
artifact and version-range metadata, but the online resolvers had stored that
metadata under the reviewed mirror IDs while the offline invocation omitted the
repository-owned settings and looked under the original `central` ID. The
offline builder now mounts the same hash-bound settings read-only and passes
`--settings /policy/settings.xml`. Local reproduction with the exact Trino 483
source and pinned native-arm64 Maven builder failed without that argument and
passed with it. Docker networking remains `none`; this change neither adds a
repository nor permits a network fallback. The verifier requires the exact
settings mount and argument in both online resolver blocks and in the
two-execution offline block.

PR #119 merged that settings correction as
`0342dfaead031016e4e33e0a88baa8dfef6fed77`. Reviewed-main run
`30143825129` passed both independent online Maven reconstructions and their
archive comparison, then failed offline on
`io.trino.tempto:tempto-core:204`. The pruner had treated external Maven Central
dependencies under `io/trino/**` as reactor output. The dependency contract now
retains an exact 37-path external closure: 35 JAR/POM paths covering 19
coordinates plus two required version-range metadata paths. Any other
`io/trino/**` file still fails closed.

The corrected Maven closure reached the Web UI and exposed a separate
dependency-sufficiency gap: the exact Bun executable was present, but npm
packages were absent during the network-none build. The publisher now sets
`CI=true`, freezes both exact `bun.lock` files, binds
`https://registry.npmjs.org/`, and packages a separately manifested Bun cache.
Two independent local reconstructions produced byte-identical results with
75,361 regular files, 664 safe cache-alias symlinks, and 500,213,727
uncompressed bytes. The deterministic archive is 128,457,765 bytes with
manifest SHA-256
`adfcb6663080ef7f39b5e592b7ca8df94e3449ae0ab73af630feac5a5fe721b0`
and archive SHA-256
`19087b76181177178ead04cabd85f81180ce64d71d84b78e5dda74a2dc71abd7`.
Those values are local pre-merge validation evidence only; reviewed-main
publication evidence must reproduce them or fail closed.
Using that verified cache and the exact pruned Maven repository, one native
arm64 local network-none build completed with `BUILD SUCCESS` in 17 minutes
12 seconds. It produced `trino-server-483.tar.gz` at 851,844,285 bytes with
SHA-256
`d4ce3f05c26c1f29192e0668ac5345860b08df005c51d7f4187834b61e4554f2`.
This single local execution proves pre-merge dependency sufficiency only; it
does not replace the reviewed-main workflow's two fresh-build equality,
security scan, signature, provenance, publication, and anonymous-pull gates.

PR #120 merged as
`ad220f0a033c803846bdd383c0191882eada8892`. Reviewed-main run
`30157442187` completed the full Maven reactor with `BUILD SUCCESS` in
14 minutes 5 seconds, then failed closed while packaging the Bun cache because
the transient-file rule classified the legitimate package payload
`combined-stream@1.0.8@@@1/yarn.lock` as a cache-control lock. The focused
repair permits only the 11 exact reviewed package payload paths with the exact
`yarn.lock` spelling; `.lock`, `download.lock`, path or case variants, and all
other unreviewed lock-suffixed files still fail closed, and the exact reviewed
manifest/archive identity remains mandatory.

PR #121 merged that repair as
`1341bfb27af996ed81375b1cb7209cda2f4297ed`. Reviewed-main run
`30176604675` passed the independent Maven and Bun reconstruction gates and
completed both fresh network-none Trino builds, but failed closed because the
two server archives had different SHA-256 values. Local member-level
reproduction proved that both archives contained the same 6,939 members and
the same 973,910,528 uncompressed bytes. Only
`lib/io.trino_trino-main-483.jar` differed; inside that JAR, only
`io/trino/operator/window/GroupsFraming.class` differed, and its executable
bytecode was unchanged. The difference was confined to two compiler-generated
`LocalVariableTable` names whose `$<number>` suffix varied between clean
compilations.

The offline command therefore fixes
`maven.compiler.debuglevel=source,lines`. This retains source-file and
line-number diagnostics while omitting the non-runtime local-variable table;
it does not rewrite or normalize a compiled artifact after the build. Two
successive clean `trino-main` builds with the pinned native-arm64 builder
produced the same complete JAR SHA-256
`f0e6a9e3c833724a9862c7aa74fe393c193101f6e580c940085574af481d9b4e`.
Two subsequent complete network-none reactor builds succeeded in 16 minutes
40 seconds and 16 minutes 34 seconds and produced byte-identical
`trino-server-483.tar.gz` archives at 849,577,808 bytes with SHA-256
`974e3bcc3c3fc38896ef2fcd9e2b30cd592550b9272c0cde409e0d6969fa9880`.
The contract and verifier bind the exact debug policy and fail closed if
`vars`, another debug level, or an unreviewed compiler argument is restored.
Because the pending Polaris boundary inventories every privileged workflow and
script by digest, its cross-component inventory is advanced only to the exact
reviewed Trino workflow and verifier bytes in the same change.
Reviewed-main must still prove equality of two complete fresh server builds;
the local clean-build results are diagnostic evidence, not publication
evidence.

Reviewed-main run `30184349867` completed both independent Maven/Bun
reconstructions, both fresh network-none native-arm64 builds, byte-identical
server archives, and the Maven High=0/Critical=0 scan. It then failed closed on
five Bun High findings. ADR-0024 authorizes one overlay only after the pristine
source audit. The overlay is limited to the four exact Web UI package and lock
files, pins `react-router-dom 7.18.1`, and overrides only `d3-color 3.1.0`,
`fast-uri 3.1.4`, `brace-expansion 5.0.8`, and `postcss 8.5.18`. Patch bytes,
preimages, postimages, dependency versions, and the complete React Router
import inventory are hash- or value-bound and fail closed on drift.
Two independent clean native-arm64 reconstructions of the patched lockfiles
produced 75,321 regular cache files, 660 safe alias symlinks, and 500,020,836
uncompressed bytes. Their manifest SHA-256 is
`6e7be3a404014f6f7ac7e4bc326c8d46f7d5822fcea1ac000219c17f1d23f421`;
their 128,423,777-byte deterministic archive SHA-256 is
`252eade2183bdf5a371f073752420c3a45f5ef8b1dacb08a4addea350389e3c2`.
These are local pre-merge identities that reviewed main must reproduce exactly.

The remaining GHSA-qwww-vcr4-c8h2 finding applies to unstable React Server
Components APIs that the exact Trino 483 client-side import inventory does not
use. The publisher retains the raw report with exactly that one High finding
for `pkg:npm/react-router@7.18.1`, applies one hash-bound OpenVEX
`not_affected` statement with `vulnerable_code_not_in_execute_path`, and
retains a separate adjusted report. The adjusted report must be
High=0/Critical=0 and its complete package inventory must equal the raw report.
Any additional finding, changed PURL, version, advisory, severity, import,
package inventory, VEX byte, or expiry fails closed. This is a reviewed
non-applicability assessment, not an ADR-0019 vulnerability risk acceptance.
It expires with the ADR-0023 authorization at `2026-08-21T22:43:36Z` and
cannot renew automatically.

PR #123 merged the bounded overlay and OpenVEX contract as
`a217edcd2ff414d347f04997df8ad2521d554217`. Reviewed-main run
`30196716086` then completed both independent Maven/Bun reconstructions, both
fresh network-none native-arm64 builds, byte-identical server archives, the
Maven scan, and the raw/OpenVEX-adjusted Bun gates. It failed closed before
publication while recording the candidate because the direct
`trivy version --format json` command did not reuse the vulnerability database
cache populated by the Trivy action and therefore returned no database
freshness timestamps. No dependency artifact, signature, attestation, image,
resident admission, Flux object, or runtime was created.

The record step and the direct OpenVEX-adjusted scan must both set
`TRIVY_CACHE_DIR` to the same workspace cache used by the Trivy actions. The
workflow verifier binds both complete command blocks and exactly two matching
cache declarations; removing either declaration fails closed before a
publisher change can merge.

PR #124 merged that repair as
`834bfd5f11e3a1a654f9a24dd9d07170b2c1d791`. Reviewed-main run
`30202114765` completed both dependency reconstructions, both network-none
native-arm64 builds, archive equality, all Maven/Bun SBOM and vulnerability
gates, and the candidate freshness record. Publication then failed closed
before any registry object was created because ORAS 1.3.3 rejects absolute
layer source paths by default. The publisher must change into the already
validated candidate directory and push only the four reviewed basenames. It
must not use `--disable-path-validation`, broaden the layer inventory, or make
another directory the publication root.

PR #125 merged the relative-path repair as
`ce2369bdd793be990f0b2a0051003c2ed77f562f`. Reviewed-main run
`30221290325` completed both dependency reconstructions, both network-none
native-arm64 builds, archive equality, all Maven/Bun SBOM and vulnerability
gates, and the candidate freshness record. Its ORAS push succeeded and created
the run-scoped registry object
`ghcr.io/tommykammy/shirokuma-trino-maven-dependencies@sha256:91a8fc1897639f1429c794174be1a7e6303976a9076cd0970b25d6e0b9760186`.
The publish job then failed closed because its handwritten digest glob contained
only 62 hexadecimal positions and rejected the correct 64-position digest.
No signature, attestation, anonymous-pull receipt, admitted dependency
artifact, image, or runtime was created. The unsigned registry object is a
failed-attempt record and is not admissible. The focused repair replaces the
error-prone repeated glob with the anchored Bash ERE
`^sha256:[0-9a-f]{64}$`; repository verification fixes that exact expression
and rejects shortened, uppercase-permitting, or unanchored mutations.

PR #126 merged that digest correction as
`b0aae2e5f50fd96fa68766f8da3d0ae7a6ba4054`. Reviewed-main run
`30226355104` completed reconstruction, reproducibility, SBOM, scan,
OpenVEX, freshness, ORAS publication, digest validation, keyless signing, and
cryptographic attestation verification for
`sha256:06c87a7fc58d7ca155a558a6f86e110c631cb3071c80a4c58942f76f5da1ab4a`.
It then failed closed because Cosign 3.1.1 `attest --predicate` reconstructed
the signed envelope as in-toto Statement v0.1, while the reviewed repository
contract requires the exact generated Statement v1. The signed and attested
registry object is failed-attempt evidence only; no anonymous-pull receipt,
admitted dependency artifact, image, or runtime was created. The focused
follow-up keeps the v1 contract unchanged, generates the complete v1 statement
in the repository, signs its exact bytes with `cosign attest-blob`, and attaches
that verified bundle with `cosign attach attestation`. Repository verification
must reject predicate-only signing, digest-prefix ambiguity, missing attachment,
or any statement that differs from the generated object.

The publisher resolves and packages two independent fresh Maven repositories
and two independent fresh Bun caches, requires each complete
manifest/archive pair to be byte-identical, and then
performs two fresh network-none native-arm64 source builds from the exact
snapshot. Their sole expected output,
`core/trino-server/target/trino-server-483.tar.gz`, must match by digest and
size. The Provisio 2.0.0 archive members must use the exact internal root
`trino-server-483`. Its staged root-stripping behavior leaves the one required
empty assembly marker `trino-server-483/trino-server-core-483/`; descendants
under that marker fail closed, and all payload remains under the reviewed
`NOTICE`, `README.txt`, `bin`, `lib`, and `plugin` roots. Symlinks, hard links,
special files, partial files, cache-control lock
files, unknown origins, duplicate paths, and repository-produced
`io/trino/**` reactor outputs fail closed, except for the Bun cache's closed
absolute `/bun-cache/` alias symlink contract and exact reviewed package
payload lockfiles. The verified Bun cache is mounted read-only. Origin markers
are consumed before the timestamp-bearing
`_remote.repositories` and `resolver-status.properties` resolver metadata are
excluded from the deterministic archive. The fresh Maven Trivy dependency scan
must remain High=0/Critical=0. The Bun scan must retain both the exact raw
finding and the OpenVEX-adjusted High=0/Critical=0 report, plus the OpenVEX and
both CycloneDX results, with the candidate.

Publication is main-only, uses an immutable `run_id` / `run_attempt` tag, and
produces only a review-pending OCI dependency artifact. Cosign identity is bound
to the exact main-branch workflow, while SLSA subject/source/ref/SHA claims bind
the downstream build. Its
`predicate.buildDefinition.resolvedDependencies` must identify the exact Trino
483 repository, tag object, commit, and tree used by the build. The verifier
must retain runner/host/container architecture observations and reject QEMU or
binfmt emulation. Anonymous exact-digest retrieval is mandatory before the
publication evidence is retained.

GitHub Container Registry creates the first package version as private. The
first main run may therefore stop after signing and attestation at the anonymous
pull gate. That failed attempt is not admitted: the owner must make the package
public in GitHub and rerun the same reviewed main revision. User credentials
must not be substituted for the anonymous proof.

The main run does not admit the dependency artifact. A separate evidence-only
PR must pin and independently review the exact digest, then retire the
write-capable publisher. Image publication, resident admission, credentials,
Flux objects, and runtime reconciliation remain forbidden. The upstream image
and server tarball and unchecked Maven-wrapper download remain forbidden. No
control can be stacked with an ADR-0019 Trino vulnerability exception.

Trino 476 is not a permissible signed-binary fallback. Its Maven Central
detached signature verifies cryptographically against fingerprint
`C328250FE23A2420814521EC0EB69F76FD171538`, but the key was obtained from a
public keyserver and is not independently bound to an approved Trino release
authority. The extracted upstream distribution also fails the vulnerability
gate: Syft 1.46.0 plus Trivy 0.72.0 reported Critical=2 and High=52, including
Critical findings in the bundled native launcher and Ranger module and
`CVE-2026-34214` in `io.trino:trino-iceberg` 476, fixed in 480. ADR-0019 never
permits a Critical exception. Removing or replacing signed distribution
contents is not an admission workaround, because it changes the reviewed input
and leaves the Iceberg finding unresolved. The closed feasibility record is
`bootstrap/trino/v476/feasibility.json`; no 476 workflow, image, ledger entry,
or runtime is allowed.

Reviewed-main Trino dependency publisher run
[`30231656483`](https://github.com/TommyKammy/Shirokuma/actions/runs/30231656483)
from commit `1ae1996eaf654e69daad60c574c7abb4e4d2be3b` completed the closed
Maven/Bun reconstructions and published public dependency reference
`ghcr.io/tommykammy/shirokuma-trino-maven-dependencies@sha256:0394143034298f4c6606c288e8ef97154826978bf3aa97e1e952499f8af5075c`.
Evidence review rejected that object: Trivy filesystem mode inventoried zero
Maven components/results, the SBOM and scan documents lacked an exact OCI
digest subject, the retained inventory was not recursively closed against
nested directories and symlinks, the signed SLSA resolved dependency omitted
the reviewed `483` tag object, and the post-attestation anonymous-pull receipt
lacked its own trusted signature. The object is a failed attempt and is not
dependency evidence.

The repair keeps the publisher active, switches the Maven repository scan to
Trivy rootfs mode, requires exact equality between the 1,470 descriptor JAR
paths and both Trivy/CycloneDX inventories, requires every descriptor JAR to be
represented by either a Trivy file-path identity or a closed contract-authorized omission,
preserves every rootfs-discovered top-level and embedded component in the final
scan graph, re-roots each
CycloneDX dependency graph at the immutable OCI subject (creating a root edge
during Maven generation or immutable-subject rebinding when Trivy omitted
one), compares Maven scan identities by both PURL and file
path, binds every retained
SBOM and scan document to the ORAS digest before SLSA signing, includes tag object
`32d4f28e8311ea6f67edca209df59a0493d869fa`, includes the exact retained Trivy
version/database timestamp record in the signed SLSA dependencies, and
keyless-signs the anonymous-pull receipt after retrieval. No dependency
artifact, Trino image,
resident admission, Flux object, or runtime is admitted until a fresh
reviewed-main run and separate evidence-only review succeed.

PR #129 merged the rootfs-discovery correction as
`57189df014afaaca1bbd0a6cac60d6e6ef837d2b`. Reviewed-main run
[`30312893557`](https://github.com/TommyKammy/Shirokuma/actions/runs/30312893557)
then completed both independent dependency reconstructions, both fresh
network-none builds, archive equality, the raw rootfs inventory, and the
closure-complete Maven SBOM. It failed closed when the exact final scan exposed
64 High/Critical finding occurrences. Because the blocking Trivy action exited
before the normal candidate-retention step, the run retained zero artifacts.

The follow-up keeps every High/Critical finding blocking. Trivy records the
complete JSON report with exit code zero, and the repository-owned
`verify-maven-scan` command remains the explicit fail-closed gate for malformed
reports, identity drift, and any High/Critical finding. Only when that exact
verifier fails does the workflow retain the descriptor, raw rootfs SBOM,
closure-complete SBOM, and Trivy report for 14 days under a run-scoped
diagnostic artifact. The publication job still requires the validate job to
succeed and downloads only the separately named read-only candidate artifact;
failure diagnostics cannot become publication, admission, image, or runtime
inputs. Because both exact inventories live below `.trino-candidate`, their
upload steps explicitly opt into hidden files; each path remains individually
listed, and the workflow contract rejects removal, false values, extra opt-ins,
or movement of that setting to another upload.

ADR-0026 authorized an exact, reproducible rebuild of
`org.apache.parquet:parquet-jackson:1.17.1` with Jackson 2.21.4; it did not
waive the remaining Maven closure. PR #134 merged the latest focused closure
repair as `de7cd8c0c6c20173f9db788cb885b17ce215cdce`. Reviewed-main run
[`30415622742`](https://github.com/TommyKammy/Shirokuma/actions/runs/30415622742)
still failed closed at the exact Maven High/Critical gate. No dependency
snapshot, image, resident admission, Flux object, or runtime was admitted.

ADR-0027 records the owner's subsequent bounded authorization from Issue #63
comment `5115851323`. It applies a second hash-bound Trino 483 source overlay
to four paths only, selects the exact server/server-core/server-main/HDFS/
Iceberg reactor with required projects, packages only server core/main and the
Iceberg plugin with HDFS, and replaces only the build-plugin dependency
versions encoded by the reviewed patch. The authorization expires at
`2026-08-21T22:43:36Z`, cannot renew automatically, and does not permit a
waiver, ignore rule, OpenVEX expansion, credential, image, resident, Flux, or
query change. Local pre-merge feasibility completed the 40-module selected
reactor, scanned its complete Maven repository at High=0/Critical=0, and
completed a clean `--offline` rebuild with container networking set to
`none`. Reviewed-main CI must independently reconstruct twice, reproduce two
network-none builds and byte-identical server archives, and pass the same
closure-complete evidence gate before publication can proceed.

Reviewed-main run
[`30517632888`](https://github.com/TommyKammy/Shirokuma/actions/runs/30517632888)
completed both independent closed-repository reconstructions, both network-none
builds, byte-identical server archives, and the raw Trivy rootfs inventory. It
then failed closed because Trivy 0.72.0 did not emit a component for
`dev.failsafe:failsafe:3.3.2`, even though the exact descriptor-bound JAR
contains bytecode and one matching `META-INF/maven/.../pom.properties`.
Publication and all later evidence steps were skipped, and the run retained no
artifact.

The omission contract does not treat a missing Trivy component as a waiver.
It enumerates the exact 11 paths observed in run `30306042009`, their derived
PURLs, and their source, test, or base-coordinate roles. SBOM generation may
supplement only a subset of those reviewed identities; every other omitted
path fails closed. Each permitted file must match the closed descriptor's
repository origin, mode, size, and SHA-256 and must pass bounded, full-member
ZIP safety checks, including rejection of nested archives. The verifier does
not infer authorization from arbitrary JVM bytecode or reimplement JVM
loadability rules: artifact bytes and Maven identity are already fixed by the
closed descriptor and explicit path/PURL/role contract. Generated components
retain that exact identity and contract discovery mode, and
`verify-maven-scan` still requires the final Trivy SBOM scan to contain every
descriptor PURL/path identity and zero High/Critical findings. Any new path,
coordinate, version, role, origin, byte sequence, unsafe archive, or final-scan
omission continues to fail closed and requires a separately reviewed contract
change.

Trivy may also report embedded or shaded Maven components against the outer
top-level JAR path. These alternate PURLs are preserved in the CycloneDX
evidence, but they do not prove discovery of the descriptor artifact itself.
Rootfs discovery therefore normally requires the exact derived
`(PURL, FilePath)` pair. Runs `30672778826` and `30684596946` established one
narrow scanner exception: for an exact classifier-bearing descriptor path,
Trivy may report the same Maven group/artifact/version PURL without its
classifier. The generator preserves that scanner component, supplements the
exact classifier identity with `trivy-classifier-erased-purl` provenance, and
still rejects unrelated PURLs. Base JARs, new paths, and all other omissions
remain bounded by the reviewed 11-entry contract, preventing a general
path-only authorization bypass.

Reviewed-main run
[`30693677356`](https://github.com/TommyKammy/Shirokuma/actions/runs/30693677356)
then proved that the classifier repair reaches the unchanged final Maven
High/Critical gate. Both closed repository reconstructions, both network-none
builds, raw rootfs inventory, and closure-complete CycloneDX generation passed;
the gate failed closed on three High findings: embedded `commons-io 2.8.0`
inside `velocity-engine-core 2.3`, plus top-level `plexus-utils 4.0.1` and
`4.0.2`. The run retained only the diagnostic artifact and produced no
dependency publication, image admission, Flux object, or runtime state.
ADR-0028 therefore returns the lifecycle to
`source_remediation_authorization_pending`. It retains the exact report, SBOM,
classification, and a hash-bound feasibility patch, but does not activate that
patch or alter ADR-0027's exact source postimage. Publication remains false
until the risk owner explicitly approves or rejects the new Velocity 2.4.1 and
Plexus Utils 4.0.3 boundary and an independent reviewer accepts its
implementation. The run-scoped manifest and raw rootfs SBOM are retained
alongside the report and closure SBOM beyond the Actions artifact expiry. The
repository audit checks every retained file's exact hash and size, recomputes
the three finding identities and summary, and requires the feasibility patch
to use the canonical single-path zero-context format.

Preauthorization run
[`30731801825`](https://github.com/TommyKammy/Shirokuma/actions/runs/30731801825)
resolved the revised exact candidate on native arm64 with the digest-pinned
Maven builder and replayed the complete closure with container networking set
to `none` and the repository mounted read-only. Both phases exited zero and
reported zero vulnerable-coordinate lines. Independent audit also proves that
the 4,879-file archive exactly matches its manifest and contains no denied
vulnerable JAR. Artifact `8828209533`, digest
`sha256:cf0272447ec1a6afd4bda304fefeb6176ee4240d4fc6339a32de65acf015fe8d`,
retains the 273,613,724-byte reproducible repository, logs, manifest, toolchain,
and validation record until `2026-09-01T04:08:14Z`. The repository retains the
validation record and artifact receipt. This is bounded historical feasibility
evidence, but the run predates the current authorization checkpoints, read-only
source mounts, and effective Maven policy binding. It therefore does not
satisfy ADR-0028's current revalidation prerequisite: the retained
classification requires a fresh hardened run and independent artifact audit
before owner authorization. It neither authorizes nor activates the candidate,
publishes an artifact, admits an image, or changes runtime state. The explicit
owner approve/reject decision and independent implementation review remain
mandatory.

Fresh hardened feasibility run
[`31072144404`](https://github.com/TommyKammy/Shirokuma/actions/runs/31072144404)
then completed the exact selected reactor on native linux/arm64, replayed the
closed repository with networking disabled, and retained artifact
`8956062532`. Independent audit verified all 5,036 files, the
242,729,683-byte archive
`sha256:b12debf4e760e3042fe34bd9946c2ed96d0ead4eb7959d8491fe63f3240c208a`,
and closed manifest
`sha256:6667d7b8275c0b24ec4f8ec7070173a57a888ab425d8697e0b261f49fc347ea4`.
Issue #63 comment
[`5210182460`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5210182460)
records the risk owner's exact approval at `2026-08-07T00:08:48Z`.
ADR-0029 therefore activates only the hash-bound third `pom.xml` patch after
the two previously authorized patches and permits one reviewed-main dependency
publication attempt. The authorization expires at
`2026-08-21T22:43:36Z`, cannot renew automatically, and requires a human
reviewer different from both the implementation author and risk owner.
The attempt is bound to the protected-main transition after commit
`ffbb4997420d4b66abf04ec4dfaa579aff2ce965` and to run attempt `1`; reruns and
later pushes fail closed. Both independent online repositories must receive
the exact hardened Maven SCM metadata and vulnerable-input pruning before
packaging. Their two POMs and checksum sidecars retain Maven-resolvable Central
markers, while the closed manifest separately records the dedicated
`shirokuma-scm-remediation` provenance only for the four exact hash-bound
postimages. Every online and network-none
Trino `clean install` must then revalidate the complete authorized source
postimages before any resulting repository or server archive is consumed.
Pruning a blocked JAR also removes all checksum sidecars and matching Maven
origin-marker entries so the sealed repository cannot retain orphan metadata.
The write-capable publish job repeats the one-attempt check and queries the
exact merged pull request before registry authentication. Publication requires
at least one current `APPROVED` review from a human GitHub user different from
both risk owner `TommyKammy` and implementation author `Codex`; risk-owner-only
CODEOWNERS review fails closed. The approval must target the exact final pull
request head SHA; an approval for an earlier revision fails closed.
Pull-request execution remains validation-only. A resulting dependency
candidate remains review-pending: image publication, resident admission,
Flux/runtime resources, credentials, public exposure, query acceptance, and
Issue #63 closure all remain false and require separate evidence gates.

The authorized reviewed-main attempt ran as
[`31163679280`](https://github.com/TommyKammy/Shirokuma/actions/runs/31163679280)
at `27a313fca0aa080db8bd8f1d67744c68b1b0ab4f`. Two fresh online closure
reconstructions and the first network-none Maven build succeeded, after which
the candidate postimage verifier failed closed because JGit created the
untracked source path `.config/jgit/config`. Setting the container `HOME` did
not set Java's `user.home`; every Maven container must therefore receive
`MAVEN_OPTS=-Duser.home=/tmp/maven-home`. The untracked-source rejection is
retained without an allowlist or cleanup exception. The failed run published
nothing and consumed ADR-0029's one-shot authorization. Any rerun or later
reviewed-main publication attempt requires a new explicit owner authorization;
the corrective pull request is validation-only.

PR #144 merged the JGit containment as
`6f557abc42713629510090db10d03630043364d7`; its main workflow confirmed the
blocked publisher path with `validate=success` and `publish=skipped`. Issue #63
comment
[`5221869732`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5221869732)
then reauthorized exactly one second reviewed-main attempt using the unchanged
ADR-0029 candidate. That activation is bound to the protected-main transition
after `6f557abc42713629510090db10d03630043364d7` and run attempt `1`, retains the
same `2026-08-21T22:43:36Z` expiry, and requires a final-head approval before
merge and publication. The standard path remains a current independent human
`APPROVED` review on the exact final activation PR head from a reviewer
different from the owner and implementation author. It is evaluated first and,
when satisfied, completes approval without querying owner-exception comments,
final-head CI, or review-thread APIs. The independent-review requirement
remains the normal policy for every other pull request and any later attempt;
the exception below is not a permanent relaxation.

Because Shirokuma is `TommyKammy`'s personal experimental project and no
independent approver is available, Issue #63 comment
[`5262105662`](https://github.com/TommyKammy/Shirokuma/issues/63#issuecomment-5262105662)
defines one alternative for PR #145 only. After the exact final PR head has
completed `.github/workflows/ci.yml`, `.github/workflows/security.yml`,
`.github/workflows/trino-maven-remediation-feasibility.yml`, and
`.github/workflows/trino-maven-dependencies.yml` successfully, and a complete
GraphQL query reports a pull-request `headRefOid` equal to the attested final
head and zero current non-outdated `reviewThreads`, including resolved threads,
the owner may post the canonical top-level final-head attestation. Only
already-outdated threads are permitted. It must be authored by login
`TommyKammy`, GitHub type `User`, association `OWNER`, match the exact final
head, reference exception comment `5262105662`, and exist before merge. Only
exact `APPROVED` or `REVOKED` decisions are recognized. The
matching owner decision with the latest `updated_at` governs; comment ID is not
used for ordering, a later `REVOKED` denies approval, and a tie at the latest
timestamp fails closed as ambiguous. Bot or non-owner marker comments cannot
satisfy or deny an otherwise valid attestation; owner identity, association,
PR, head, body, timing, CI, thread, or pagination drift fails closed.

Only on this fallback path, the publisher reads top-level comments through the
REST issue-comments endpoint with `per_page=100`, follows every page to
exhaustion, and permits at most 10 pages with a 32 MiB response bound per page.
Every comment ID must be a unique positive integer in strictly increasing order
across all pages, and two complete scans of the ID, body, author identity and
type, association, and creation/update timestamps must match. Reaching the
1,000-comment ceiling without proving exhaustion, or receiving a missing,
malformed, reordered, duplicated, or unstable page, fails closed. A qualifying
standard independent review short-circuits before this comment query, so
owner-exception data is not fetched on the normal path.

The thread result proves the state of the exact attested head. Resolving a
non-outdated thread after attestation does not make it acceptable. Making a
thread outdated requires a new PR head, which invalidates the attestation and
the old final-head CI evidence, so a post-attestation outdated transition
cannot satisfy the gate; the new head must pass all exception gates and receive
a new attestation. The publisher queries owner comments and these CI/thread
inputs lazily only when no qualifying standard independent review exists.

On the owner-exception path, the main publisher repeats the exact merged PR,
attested-head CI, and current-thread queries at its write-capable boundary and
again immediately before registry authentication; the attestation is not a
substitute for those checks. Failure, rerun, a different main predecessor, or
candidate drift consumes or rejects the attempt and returns publication to
fail-closed reauthorization pending. The exception's downstream-authority set
is empty: no dependency artifact admission, image publication, resident
admission, Flux/runtime state, credential, public exposure, production use, or
Issue #63 closure is authorized.

## Resident image and SBOM evidence

Every image admitted to a resident profile must have an entry in
`security/resident-images.json` before its deployment manifest is merged. Each
entry records the human-readable `version`, upstream `source`, `linux/arm64`
`platform`, exact `repository@sha256:<digest>` reference, `sbom_artifact`,
`scan_artifact`, `supply_chain_artifact`, `sbom_generator`, `scanner_version`, and timezone-qualified
`vulnerability_db_updated_at`. Mutable or tag-qualified references such as
`latest` are never sufficient evidence. Future vulnerability database timestamps
are rejected. The deterministic gate reconciles every tracked image reference
under `deploy/` and Helm templates under `charts/` with the ledger. An empty
ledger is valid while L0 has no resident service images, deployment manifests,
or Helm chart images.

`sbom_artifact` and `scan_artifact` are paths relative to the resident image
ledger and must be available when the deterministic gate runs. Symlinks and
parent traversal are rejected. The SBOM must be a CycloneDX JSON object. The
referenced Trivy JSON must identify the same immutable ledger reference through
`ArtifactName` or `Metadata.RepoDigests` and pass the same High or Critical
blocking threshold as direct report checks unless the explicit `local-lab`
profile resolves every High finding through the exception contract below.
When `Metadata.RepoDigests` is populated, it is authoritative over the
operator-facing `ArtifactName`.

The `supply_chain_artifact` is a retained verification record. It binds the
platform digest to a signed immutable OCI index, signer identity, issuer,
transparency-log entry, SLSA provenance v1 subject, and upstream SPDX SBOM
subject. The signed index must contain the exact linux/arm64 manifest. A present
attestation without trusted signature verification is not sufficient evidence.

CI generates a CycloneDX JSON source SBOM with Syft for every pull request and
retains the workflow artifact for 30 days. Once resident images exist, each
digest gets a separate image SBOM and Trivy image scan before admission; the
ledger points to that retained artifact. Release evidence must preserve the
SBOM, scanner versions, vulnerability database timestamp, and immutable image
digest for the lifetime of the release evidence.

Repository-controlled source builds retain the complete Cosign verification,
Sigstore bundle v0.3 certificate and Rekor inclusion snapshot, independently
queried Rekor entry, raw signed image manifest, exact-workflow SLSA verification
and bundles, observed toolchain, runtime smoke, image SBOM and its attestation
bundle, scanner metadata, Trivy report and its attestation bundle, and promotion
result in Git for the admission lifetime. Cosign
verification binds issuer, identity, workflow name, repository, ref, SHA, and
trigger. SLSA verification uses CLI signer/source filters and then reconciles
the certificate, workflow path/ref/SHA, run and attempt, builder identity, and
subject digest. Repository verification also requires the signed SLSA
`resolvedDependencies` entry to name the exact source ref and commit, rather
than trusting the retained verification JSON's source fields. A GitHub Actions
artifact may mirror those files for operator download, but its finite retention
window is not the durable source of truth. A source-built candidate remains
blocked from runtime manifests until a
resident-ledger supply-chain record backed by those retained files passes
`check-images`.

Git-only repository verification must not trust retained certificates,
verification JSON, SBOM, or scan results structurally. It invokes the
contract-pinned Cosign version against the retained image-signature, SLSA, SBOM,
and Trivy v0.3 bundles with the exact issuer, GitHub workflow, digest, and
predicate-type constraints. The signed SBOM and scan predicates must equal the
retained JSON objects before their semantic gates run. A missing binary, version
drift, invalid Fulcio chain, identity mismatch, invalid DSSE signature, predicate
substitution, or invalid transparency material fails closed.

For the pinned Cosign v3 format, `cosign verify IMAGE@DIGEST` is the
authoritative registry-image check. A separate `verify-blob` check may bind the
detached v0.3 `sign/v1` DSSE bundle to raw OCI manifest bytes only after those
bytes hash to the exact image digest. Registry signature download must remain
bundle-first JSONL; legacy `Base64Signature`/`Payload` records, a
`messageSignature`, or another predicate type fail closed.

Pinned fallback images are exceptional and require `fallback: true`, documented
CVE risk, a future ISO `expires_on` date, and a concrete replacement plan in
the ledger. Expired or malformed dates fail closed. Every MinIO entry must be
marked as a fallback; SeaweedFS stays the mainline object-storage choice.

The Polaris runtime activation gate does not reopen image admission. It accepts
only the three exact resident references for Polaris, PostgreSQL, and the Admin
Tool and hash-closes every runtime manifest in
`security/polaris-runtime-activation.json`. Secret material is created only by
OpenTofu; Git contains Secret names and keys but no Secret manifest,
`secretGenerator`, `stringData`, credential value, or credential-producing
command. The 2026-07-21 UTC local-lite acceptance at revision
`04b0800b77d4a4731b232d14d1788ee793f5c79c` proved all four Flux
Kustomizations Ready, credential-safe Catalog create/list/read/delete, and a
PostgreSQL custom dump restored into an isolated temporary database with exact
schema and row fingerprints. The sanitized receipt is hash-bound from
`security/polaris-runtime-activation.json`; the dump remains owner-only on the
macOS host outside Git and Colima. The gate may advance to `runtime_accepted`
only through the focused PR after CI and required human review. This is bounded
local-lite evidence and makes no production recovery claim.
Credential generation is a reviewed non-secret ConfigMap consumed by both
OpenTofu and Flux substitutions. Independent `TF_VAR` generation overrides and
in-place Secret data rotation are forbidden; replacement requires a reviewed
catalog rebuild so credential and workload generations cannot diverge.

## Local-lab resident image exceptions

ADR-0019 permits a separate `local-lab` profile for development-only evaluation
on `mac-studio-solo`. The default `strict` profile continues to require
High=0/Critical=0. `check-trivy` also remains strict when run directly.

`security/resident-image-exceptions.json` may acknowledge High findings only
when each record matches the exact image digest, CVE, package, and installed
version in the retained scan. The record must reference an existing ADR, state
the bounded risk, list at least three compensating controls, provide a concrete
replacement plan, and expire no more than 30 days after approval. Critical
findings are never allowed. New or missing High findings, stale exceptions,
digest/package/version mismatch, missing evidence, expired approval, public
exposure, or production use fail closed.

The local-lab profile is not a production certification and does not assert
that an accepted CVE is unreachable. Production data and credentials, public
Service/Ingress exposure, and untrusted Git/OCI/Helm sources remain outside the
approved scope.

## Scanner or feed failure rollback

Security-tool and feed failures do not permit bypassing the check. First retry
the pinned workflow to rule out a transient service failure. If the pinned tool
or feed is broken, revert only the tool-version update to the last verified pin,
record the outage and retained scan evidence in the Work Package, and rerun the
unsafe fixtures plus the full gate. If no verified pin can scan successfully,
keep the pull request blocked and open a follow-up prerequisite; do not replace
the result with a guessed or stale success.

## Agent rules

- Unknown install instructionsをそのまま実行しない。
- 依存追加はPRで理由を書く。
- postinstall hooksがある場合はSecurity labelを付ける。
- `curl|bash`は禁止。
- generated codeにlicense header/third-party attributionが必要な場合は明記する。
