---
project: Shirokuma
doc_id: "DEV-049"
title: "Supply Chain Security"
status: draft
created: 2026-07-05
updated: 2026-07-23
version: "1.22"
area: "development"
tags: [shirokuma, security, supply-chain]
---

# Supply Chain Security

## Threat model

AI Coding Agentは、善意で悪性コードを実行するリスクがあります。特に、unknown repository、postinstall scripts、curl|bash、obfuscated scripts、malicious branch names、package typosquattingに注意します。

## Controls

| Control | Tool/Practice |
|---|---|
| Dependency pinning | lock files, digest pinning |
| SBOM | syft |
| Vulnerability scan | osv-scanner, grype, trivy |
| Secret scan | gitleaks |
| Sandbox | devcontainer, no host mount secrets |
| Install review | dependency changes require human review |
| Script allowlist | only known scripts in AGENTS.md |
| Network controls | no arbitrary outbound in CI where possible |

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

The current permitted boundary is the evidence-only Maven dependency-snapshot
contract in `bootstrap/trino/v483/trusted-build-contract.json`; no publisher or
dependency artifact exists yet. The contract binds the exact authorization and
source coordinates, Maven 3.9.16 and Temurin 25 native-arm64 builder, Maven
Central plus the explicit Confluent repository, a regular-file-only closed
manifest, and an independent fresh
`mvn --offline -Dmaven.repo.local=/workspace/.m2/repository clean install -DskipTests`
with networking disabled. It records
the future Corretto 25 Alpine 3.24 arm64 base without authorizing image use.
The future dependency verifier must bind Cosign to the exact main-branch
publisher workflow identity, bind SLSA subject/source/ref/SHA claims, and use
the exact digest returned by the publisher as the sole isolated
`maven.repo.local` input after closed-manifest comparison. Ambient Maven caches
remain forbidden.
Every publication, image, resident, and runtime switch remains false while the
contract is reviewed. The upstream image and server tarball, unchecked
Maven-wrapper download, credentials, Flux, and runtime remain forbidden.
High=0/Critical=0, native smoke, SBOM, Cosign/Rekor, SLSA provenance, and
anonymous exact-digest retrieval remain mandatory and cannot be stacked with an
ADR-0019 Trino vulnerability exception.

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
