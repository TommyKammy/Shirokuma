# OpenTofu

The `dev` root owns the repeatable bootstrap for Argo CD and the Shirokuma dev
root application on Colima built-in k3s. It uses the explicit
`colima-mac-studio-solo` context and never changes the operator's current
Kubernetes context.

Pinned inputs are recorded in `dev/versions.tf`, `dev/.terraform.lock.hcl`, and
`dev/main.tf`. Workload images are selected by `linux/arm64` digest. A candidate
image must not be admitted to the resident profile until its SBOM and Trivy
evidence pass the repository supply-chain policy with no High or Critical
finding.

From the repository root, format and validate a clean checkout with:

```bash
make tofu-fmt
make tofu-validate
```

After `make colima-status` and the supply-chain evidence gate pass, bootstrap
and inspect reconciliation with:

```bash
make gitops-bootstrap
make gitops-status
kubectl --context colima-mac-studio-solo \
  -n shirokuma-dev get configmap repository-reconciliation-smoke
```

The smoke ConfigMap is under `deploy/gitops/dev/`. Change it through a reviewed
Git commit and confirm Argo CD returns `dev-root` to `Synced` and `Healthy`.
Do not use a direct `kubectl apply` as a substitute for this evidence.

Teardown is declarative and removes both managed namespaces:

```bash
make gitops-teardown
```

OpenTofu state is workstation-local and must not be committed. The bootstrap
adds only small control-plane metadata to the existing 400GB Colima profile;
it does not create a persistent data volume. Export any later non-reproducible
application data before a full profile reset.
