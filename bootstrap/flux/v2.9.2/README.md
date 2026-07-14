# Flux v2.9.2 bootstrap approved local-lab inventory

This directory records the exact Flux distribution and linux/arm64 controller
candidates without retaining an upstream-generated Kubernetes deployment manifest.
`components.json` must match `opentofu/dev/bootstrap-images.json`.

The four-controller manifest can be regenerated for review with the pinned Flux
CLI after verifying the official release checksum:

```bash
flux install --export \
  --version=v2.9.2 \
  --components=source-controller,kustomize-controller,helm-controller,notification-controller
```

Do not redirect that output under `deploy/`; the official bootstrap command owns
the generated resources. Every exact digest is admitted by
`security/resident-images.json`. High findings are permitted only by the
digest- and CVE-bound, time-boxed local-lab decisions in
`security/resident-image-exceptions.json`; strict and production use remains
blocked.

The generated upstream manifest contains cluster-wide controller RBAC by
design. It is evaluated only as part of an approved Flux bootstrap, not retained
as an inactive YAML fixture that repository-wide configuration scanners could
mistake for an admitted deployment.
