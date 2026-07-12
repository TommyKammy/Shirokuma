# OpenTofu and Flux bootstrap

The `dev` OpenTofu root owns only the Shirokuma dev namespace prerequisite on
Colima built-in k3s. Flux v2 owns its controllers, Git Source, root
Kustomization, and workload reconciliation after bootstrap. Both paths use the
explicit `colima-mac-studio-solo` context and never change the operator's
current Kubernetes context.

Pinned OpenTofu inputs are recorded in `dev/versions.tf` and
`dev/.terraform.lock.hcl`. The approved Flux distribution is `v2.9.1`; exact
linux/arm64 controller candidates are recorded in `dev/bootstrap-images.json`
and mirrored in the non-deployable inventory at `bootstrap/flux/v2.9.1/`.

Format and validate a clean checkout with:

```bash
make tofu-fmt
make tofu-validate
```

Install the pinned Flux CLI separately and export `GITHUB_TOKEN` only for the
bootstrap process. The Make target verifies the exact CLI version and the
resident-image ledger before mutating the cluster:

```bash
flux check --pre
make gitops-bootstrap
make gitops-status
kubectl --context colima-mac-studio-solo \
  -n shirokuma-dev get configmap repository-reconciliation-smoke
```

The smoke ConfigMap is under `deploy/gitops/dev/`. Change it through a reviewed
Git commit and confirm the `GitRepository` and root `Kustomization` return
`Ready=True` for the approved revision. Do not use direct `kubectl apply` as a
substitute for this evidence.

Teardown removes Flux and the OpenTofu-owned dev namespace:

```bash
make gitops-teardown
```

OpenTofu state and bootstrap credentials are workstation-local and must not be
committed. The bootstrap adds only small control-plane metadata to the existing
400GB Colima profile and creates no persistent data volume.
