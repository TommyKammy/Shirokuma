# SeaweedFS 4.39 admission evidence

This directory is populated only from a successful trusted publication on
`refs/heads/main`. A feature branch may change and validate the builder,
contract, and verifier, but it cannot publish, sign, promote, or approve its own
image.

The lifecycle is deliberately split into two reviewed phases:

1. merge the main-only builder, closed input contract, and verifier;
2. let the merged workflow publish from `main`, then copy its complete final
   artifact into this directory in an evidence-only follow-up PR.

Phase 2 completed from main run
[`29418029340`, attempt 1](https://github.com/TommyKammy/Shirokuma/actions/runs/29418029340/attempts/1).
The admitted immutable digest is
`sha256:d1339701907587c93c6af8740388226ac2277cbbfd3df581c0e85d815c90e421`;
the complete final artifact is `seaweedfs-4.39-arm64-29418029340-1`.
`../admission.json` is now `approved`, and repository audit uses pinned
Cosign v3.1.1 to cryptographically revalidate every retained bundle.
Runtime manifests remain forbidden until parent Issue #26 adds the resident
supply-chain record and passes `check-images`.

Before any trusted build, the static verifier binds the workflow's global
source commit, tree, and archive digest and its checkout repository/ref to
`../source.json`; repository coordinates must be a literal GitHub owner/name
slug, and source pins cannot be shadowed by job or step `env` entries. The job
set and canonical block grammar are closed. Every job
step must have a non-empty name and the exact ordered
step-name set is closed by the contract. The retained Trivy scan policy is also
closed to `vuln`, `HIGH,CRITICAL`, `ignore-unfixed=false`,
`vuln-type=os,library`, and `exit-code=1`.

For an approved release, `scripts/verify_trusted_image.py repository --root .`
requires the complete evidence set and re-runs pinned Cosign v3.1.1 against the
retained image-signature, SLSA, CycloneDX, and Trivy Sigstore bundles. The check
therefore verifies the Fulcio certificate chain, workflow identity, digest,
predicate type, DSSE signature, and transparency material cryptographically.
The signed SBOM and scan predicates must equal the retained JSON objects; shape,
self-reported claims, and updated Git hashes alone are not sufficient.

The final artifact must be copied byte-for-byte as one set. Its expected files
are enumerated by `../trusted-build-contract.json`, including the reviewed Go
module manifest, deterministic vendor archive, and dedicated SBOM and Trivy
attestation bundles. Generated evidence must never be mixed across runs.

Repository audit fully extracts the retained vendor archive. Pull-request CI and
the main publisher also regenerate `vendor` from the exact clean upstream source
using Go 1.25.12 and a fresh module cache authenticated by the public Go proxy and
checksum database. The subsequent proxy-disabled regeneration must match every
retained file record, while all 496 actually vendored effective module
checksums—including replacements—must match the pinned upstream `go.sum`.
Network, checksum, VCS, private-module, ambient
cache, workspace, and toolchain fallback are forbidden; failure keeps admission
pending. This networked provenance check is separate from the image build, which
continues to consume only the retained archive with `--network=none`.

The mutable `4.39-arm64` tag is only a publication pointer. After approval, the
immutable digest plus repository-retained evidence are the authority. Runtime
use remains separately blocked until parent Issue #26 adds and verifies its
resident-image supply-chain record.

Before promotion credentials are exposed, the workflow revalidates the complete
candidate and binds the downloaded artifact and target digest to the builder
attempt. A promotion-only retry retains that candidate attempt while recording
the later promotion attempt in the final artifact.
