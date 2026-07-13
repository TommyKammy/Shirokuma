# Policies

Shirokuma L0 uses Kyverno `ValidatingPolicy` resources for Kubernetes admission
and the repository-owned supply-chain ledger for exact image approval.

- `kyverno/baseline.yaml` denies privileged containers, host namespace,
  hostPath and hostPort access, public Service/Ingress exposure, missing
  CPU/memory requests or limits, and mutable images.
- `../security/resident-images.json` is the only exact image approval ledger;
  `make verify-security` rejects deployment references absent from that ledger.
- `exceptions/` contains narrowly scoped, time-bounded PolicyException JSON.

Run `make verify-policy` before opening a pull request. The policy bundle is
tested offline and is not installed into a cluster until its controller images
pass the same resident-image gate.

Negative fixtures use the `.yaml.fixture` suffix so general Kubernetes scanners
do not mistake intentionally unsafe test inputs for deployable manifests;
`kyverno-test.yaml` lists every fixture explicitly.
