# Flux v2.9.1 bootstrap candidate inventory

This directory records the exact Flux distribution and linux/arm64 controller
candidates without retaining an unapproved Kubernetes deployment manifest.
`components.json` must match `opentofu/dev/bootstrap-images.json`.

The four-controller manifest can be regenerated for review with the pinned Flux
CLI after verifying the official release checksum:

```bash
flux install --export \
  --version=v2.9.1 \
  --components=source-controller,kustomize-controller,helm-controller,notification-controller
```

Do not redirect that output under `deploy/` or bootstrap it into a cluster until
every exact digest is admitted by `security/resident-images.json`. The current
official images contain unresolved High findings, so `make gitops-bootstrap`
fails before OpenTofu, Git, Flux, or cluster mutation.

The generated upstream manifest contains cluster-wide controller RBAC by
design. It is evaluated only as part of an approved Flux bootstrap, not retained
as an inactive YAML fixture that repository-wide configuration scanners could
mistake for an admitted deployment.
