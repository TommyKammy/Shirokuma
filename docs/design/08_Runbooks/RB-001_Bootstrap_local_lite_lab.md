---
project: Shirokuma
doc_id: "RB-001"
title: "Bootstrap local-lite lab"
status: draft
created: 2026-07-05
updated: 2026-07-12
version: "0.4.1"
area: "runbook"
tags: [shirokuma, runbook]
---

# Bootstrap local-lite lab

## Purpose

Start and recover the accepted `solo-lite` Colima built-in k3s baseline on the
single `mac-studio-solo` host.

## Preconditions

- Colima, the Docker CLI, kubectl, and Helm are installed.
- The host can reserve 16 CPU, 96GB memory, and 400GB disk for the profile while
  retaining the required 192GB host memory reserve.
- Host SSD free space has been checked. Any non-reproducible VM data has been
  exported outside Colima before reset or recovery.

## Procedure

1. Start the accepted `solo-lite` profile. This pins the
   `mac-studio-solo` VZ/aarch64 VM and Colima built-in k3s configuration.

```bash
make colima-start
```

2. Re-run the non-mutating baseline check at any time. The wrapper uses the
   explicit `colima-mac-studio-solo` Kubernetes context and does not rely on the
   current kubectl context.

```bash
make colima-status
```

3. Capture the operator-facing evidence required by the Work Package.

```bash
kubectl --context colima-mac-studio-solo cluster-info
kubectl --context colima-mac-studio-solo get nodes -o wide
helm version
helm list --kube-context colima-mac-studio-solo --all-namespaces
```

The accepted result is a running `aarch64` VM, a Ready `arm64` node from the
expected context, and successful Helm access to the API. Missing or malformed
architecture/readiness output is a failed baseline.

## GitOps bootstrap

OpenTofu 1.12.3, Helm 4.2.3, kubectl, and the Argo CD CLI are required. The
repository pins the OpenTofu providers, Argo CD chart, and workload image
digests. Before any cluster mutation, the bootstrap target checks that every
image digest is backed by an admitted resident-image ledger entry. Missing,
malformed, High, or Critical evidence blocks bootstrap; do not bypass this gate
with a direct Helm or kubectl invocation.

The Make targets are non-interactive for supervised execution. OpenTofu init
uses the committed provider lock file in read-only mode, and apply/destroy use
explicit auto-approval only after the repository-owned admission and status
gates have run. `gitops-status` scopes Argo CD core-mode queries to the
`argocd` namespace without changing the operator's current context.

```bash
make tofu-fmt
make tofu-validate
make gitops-bootstrap
```

The OpenTofu root installs Argo CD into `argocd`, installs the repository-owned
`dev-root` Application, and creates `shirokuma-dev`. It does not change the
operator's current Kubernetes context. Confirm the authoritative Application
state and the Git-reconciled smoke object with:

```bash
make gitops-status
kubectl --context colima-mac-studio-solo \
  -n shirokuma-dev get configmap repository-reconciliation-smoke
```

The accepted result is `dev-root` at `Synced` and `Healthy`, with the smoke
ConfigMap present. For repository-to-dev evidence, merge a bounded change to
`deploy/gitops/dev/smoke-configmap.yaml` through the normal PR path and observe
Argo CD reconcile it. A direct `kubectl apply` is not valid smoke evidence.

Teardown uses the same OpenTofu state and removes the releases and both managed
namespaces:

```bash
make gitops-teardown
```

The bootstrap adds no persistent data volume and has negligible impact on the
400GB Colima disk allocation. Future application data remains subject to the
export and free-space checks below.

## Reset and recovery

Reset deletes the entire profile disk. First export non-reproducible object
data, catalog metadata, and required evidence outside the VM; confirm host free
space can hold both the export and replacement 400GB profile. Then run:

```bash
scripts/colima_baseline.sh reset --confirm-data-loss
```

The command stops and deletes only `mac-studio-solo`, recreates it with the
same pinned baseline, and runs the full status gate. If it fails, retain the
command output and exports, stop the VM, and do not treat the lab as ready.

## Verification

- `make verify`
- `kubectl --context colima-mac-studio-solo cluster-info`
- `kubectl --context colima-mac-studio-solo get nodes -o wide`
- `helm version`
- `helm list --kube-context colima-mac-studio-solo --all-namespaces`
- `make tofu-fmt`
- `make tofu-validate`
- `kubectl --context colima-mac-studio-solo -n argocd get applications`
- `make gitops-status`

## Rollback

- Stop the profile with `colima stop --profile mac-studio-solo`.
- Remove GitOps resources with `make gitops-teardown` before stopping the
  profile when the OpenTofu state is available.
- Revert the focused PR if repository checks regress.
- Restore exported data only after the rebuilt baseline passes its status gate.
- A Colima disk cannot be safely shrunk in place; export, delete, and rebuild
  when returning to a smaller profile.

## Notes

このRunbookはGxP/本番SLAを対象にしません。Lab実験用です。
