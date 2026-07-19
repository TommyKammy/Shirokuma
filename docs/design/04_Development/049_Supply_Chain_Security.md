---
project: Shirokuma
doc_id: "DEV-049"
title: "Supply Chain Security"
status: draft
created: 2026-07-05
updated: 2026-07-16
version: "0.8"
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
pull request workflow adds full-history gitleaks scanning plus Trivy filesystem
scanning for dependencies, secrets, and misconfiguration. Any High or Critical
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
therefore not a closed build input by itself. While the contract state is
`dependency_snapshot_publication_pending`, the Polaris Containerfile, Polaris
image-publication workflow, release evidence, resident-image entries, and
runtime manifests must remain absent. The only write-capable Polaris path
permitted at this checkpoint is
`.github/workflows/polaris-gradle-dependencies.yml`. Its first job has
`contents: read` only, generates SHA-256 dependency-verification metadata,
packages the two reviewed Gradle cache roots into a canonical archive, and
proves both server tasks in a fresh container with Docker networking disabled,
Gradle offline mode, strict dependency verification, and both build caches
disabled. A separate second job also has `contents: read` only. It verifies the
closed candidate inventory, descriptor, verification metadata, offline proof,
and observed Gradle/Java/builder toolchain, then exports only their SHA-256
bindings. The third job has `packages: write` and `id-token: write`; before an
explicit registry token is injected or a registry login occurs, it rebinds
every downloaded candidate byte to the read-only job's outputs. It publishes a
run-scoped OCI artifact, signs and attests the exact manifest, then requires
anonymous exact-digest retrieval and retains the complete verification set for
evidence-only review. The schema-v2 contract and repository verifier close the
job/step order, every Action SHA, permissions, source and tool environment,
lifecycle gate, offline semantics, and write-credential boundary in addition
to byte-pinning the workflow.

A 2026-07-18 workstation feasibility audit completed the two server tasks in a
clean source extraction with Docker networking disabled and Gradle `--offline`.
The reduced dependency seed still contained 5,014 files and 825,947,131 raw
bytes; deterministic compression produced 619,659,126 bytes, above GitHub's
normal single-file limit. This observation is not build admission evidence.
The next phase must publish the reviewed seed as a signed immutable OCI artifact,
retain its per-file SHA-256 descriptor, and pin the OCI manifest digest and blob
hash in Git. The image build must pull that exact artifact without registry
credentials, verify it before extraction, and keep Gradle network-disabled.
The publication workflow and packager are byte-pinned by the schema-v2 contract.
The generated descriptor, verification metadata, manifest, signature,
provenance, and offline-build record remain non-authoritative until a separate
evidence-only pull request binds their exact main-run digests in Git. Failure of
the anonymous pull keeps the dependency snapshot blocked; a registry credential
must not be added as a fallback.

GHCR creates the first package as private. Therefore the first main run may
finish the immutable push, keyless signature, and provenance, then fail
intentionally at the anonymous-pull gate. That failed attempt is never admitted.
The repository owner must review the package and make it public (a public
package must be treated as an irreversible disclosure), then rerun the workflow.
Only a rerun whose exact digest is retrievable with an empty registry config may
produce review-pending evidence. The evidence-only pull request must also delete
this publisher while advancing the lifecycle to
`dependency_snapshot_review_pending`; if the lifecycle file changes before
deletion, the workflow's first read-only gate skips all build and publication
steps instead of creating another dependency artifact.

The selected Chainguard PostgreSQL 18.4 index and linux/arm64 manifest remain a
candidate, not an admission. Signature, index membership, provenance, upstream
SBOM, independent CycloneDX SBOM, and zero High/Critical scan evidence must be
retained and reverified. Polaris and PostgreSQL are admitted atomically in an
evidence-only review after a successful main publication; neither component may
appear alone in the resident-image ledger or in runtime manifests.

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
