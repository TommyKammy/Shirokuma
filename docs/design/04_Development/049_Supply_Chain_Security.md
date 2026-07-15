---
project: Shirokuma
doc_id: "DEV-049"
title: "Supply Chain Security"
status: draft
created: 2026-07-05
updated: 2026-07-14
version: "0.5"
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
removes the default config in an `always()` cleanup step. Cosign writes one Sigstore bundle to both the
durable evidence path and the OCI referrer; the workflow downloads the registry
copy and requires an exact structural match before promotion. Workflow signer
SHA (`GITHUB_WORKFLOW_SHA`) and source SHA (`GITHUB_SHA`) are recorded and
verified as separate identities. The current contract explicitly selects the
Rekor v1 public API; a Rekor v2 migration must change the endpoint, identity
schema, validator, and fixtures together.
The source record itself is hashed into release evidence. Its exact
Containerfile digest and closed set of frontend, Go builder, and certificate
image inputs must all be consumed by the Containerfile before publication.
When an adopted Go source tree does not contain a root vendor directory, the
trusted build must retain a deterministic vendor archive and a
replacement-aware module/file manifest in Git. The archive hash is checked both
before it enters the build context and inside the Containerfile. Compilation
must use `--network=none`, `-mod=vendor`, `GOPROXY=off`, `GOSUMDB=off`,
`GOTOOLCHAIN=local`, and disabled VCS so neither first-build availability nor
ambient module-cache state is an unrecorded input.
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
candidate before credentials exist, installs the checksum-verified promotion
tool, and moves the trusted tag without changing the digest. A missing gate,
unretained candidate, failed revalidation, or digest mismatch prevents the tag
move. The mutable tag is only a non-authoritative publication pointer: a failure
while generating, validating, or retaining final evidence may leave that pointer
at the new digest, but cannot admit it. Admission requires the immutable digest,
successfully retained final evidence, and the reviewed Git-committed admission
record in a follow-up evidence-only PR. The interval between policy merge and
that evidence PR is explicitly `pending_main_publication`; release evidence is
absent and runtime use is forbidden. Candidate and final artifact names include
both run ID and run attempt so a rerun cannot collide with an immutable earlier
upload.
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
and bundles, observed toolchain, runtime smoke, image SBOM, scanner metadata,
Trivy report, and promotion result in Git for the admission lifetime. Cosign
verification binds issuer, identity, workflow name, repository, ref, SHA, and
trigger. SLSA verification uses CLI signer/source filters and then reconciles
the certificate, workflow path/ref/SHA, run and attempt, builder identity, and
subject digest. A GitHub Actions artifact may mirror those files for operator
download, but its finite retention window is not the durable source of truth. A
source-built candidate remains blocked from runtime manifests until a
resident-ledger supply-chain record backed by those retained files passes
`check-images`.

Git-only repository verification must not trust the retained certificate or
the claimed verification JSON structurally. It invokes the contract-pinned
Cosign version against the retained v0.3 bundle and raw OCI manifest with the
exact issuer and GitHub workflow constraints. A missing binary, version drift,
invalid Fulcio chain, identity mismatch, invalid DSSE signature, or invalid
transparency material fails closed.

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
