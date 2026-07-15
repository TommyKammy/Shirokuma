# SeaweedFS 4.39 admission evidence

This directory is populated only from a successful trusted publication on
`refs/heads/main`. A feature branch may change and validate the builder,
contract, and verifier, but it cannot publish, sign, promote, or approve its own
image.

The lifecycle is deliberately split into two reviewed phases:

1. merge the main-only builder, closed input contract, and verifier;
2. let the merged workflow publish from `main`, then copy its complete final
   artifact into this directory in an evidence-only follow-up PR.

Until phase 2 completes, `../admission.json` is
`pending_main_publication`, `../release-evidence.json` is absent, and runtime
manifests remain forbidden. A successful feature-branch bootstrap run may be
recorded for diagnostic value, but it is never admission authority.

For an approved release, `scripts/verify_trusted_image.py repository --root .`
requires the complete evidence set and re-runs pinned Cosign v3.1.1 against the
retained raw OCI manifest and Sigstore bundle. The check therefore verifies the
Fulcio certificate chain, workflow identity, DSSE signature, and transparency
log material cryptographically; JSON shape and self-reported claims alone are
not sufficient.

The final artifact must be copied byte-for-byte as one set. Its expected files
are enumerated by `../trusted-build-contract.json`, including the reviewed Go
module manifest and deterministic vendor archive. Generated evidence must never
be mixed across runs.

The mutable `4.39-arm64` tag is only a publication pointer. After approval, the
immutable digest plus repository-retained evidence are the authority. Runtime
use remains separately blocked until parent Issue #26 adds and verifies its
resident-image supply-chain record.

Before promotion credentials are exposed, the workflow revalidates the complete
candidate and binds the downloaded artifact and target digest to the builder
attempt. A promotion-only retry retains that candidate attempt while recording
the later promotion attempt in the final artifact.
