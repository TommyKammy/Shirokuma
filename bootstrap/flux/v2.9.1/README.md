# Flux v2.9.1 bootstrap candidate

This directory retains the exact output of the pinned Flux CLI for the four L0
controllers, with the linux/arm64 candidate tags replaced by exact platform
digests. It is evidence and a reviewable bootstrap template, not an admitted
deployment path.

`make gitops-bootstrap` verifies `opentofu/dev/bootstrap-images.json` against
`security/resident-images.json` before invoking `flux bootstrap github`. The
current official controller candidates contain unresolved High findings, so
the gate fails before OpenTofu, Flux, Git, or cluster mutation. Do not copy this
directory under `deploy/` or admit its digests until retained scan/SBOM evidence
passes with High=0 and Critical=0.
