---
project: Shirokuma
doc_id: "RB-001"
title: "Bootstrap local-lite lab"
status: draft
created: 2026-07-05
updated: 2026-07-14
version: "0.6"
area: "runbook"
tags: [shirokuma, runbook]
---

# Bootstrap local-lite lab

## Purpose

Start and recover the accepted `solo-lite` Colima built-in k3s baseline on the
single `mac-studio-solo` host.

## Preconditions

- Colima, the Docker CLI, kubectl, Helm, OpenTofu, and the repository-pinned Flux CLI are installed.
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

OpenTofu 1.12.3, Helm 4.2.3, kubectl, and the repository-pinned Flux CLI are
required. The repository pins the OpenTofu providers, `fluxcd/flux2`
distribution, controller image digests, and workload image digests. Before any
cluster mutation, the bootstrap target checks that every controller image is
ARM64-capable and admitted by the fail-closed resident-image gate. The
`mac-studio-solo/local-lab` path may use only the exact, unexpired High findings
approved by ADR-0019; Critical, new High, stale exceptions, evidence mismatch,
and production use remain blocked. Candidate manifests remain under
`bootstrap/` rather than `deploy/` until admitted.

The Make targets are non-interactive for supervised execution. OpenTofu manages
cluster prerequisites; `flux bootstrap github` installs the four standard
controllers into `flux-system` and creates repository sync resources without
changing the operator's current Kubernetes context.

```bash
make tofu-fmt
make tofu-validate
flux check --pre
make gitops-bootstrap
```

Confirm controller readiness, Source/Kustomization state, and the
Git-reconciled smoke object with:

```bash
flux check
make gitops-status
flux get sources git -A
flux get kustomizations -A
kubectl --context colima-mac-studio-solo \
  -n shirokuma-dev get configmap repository-reconciliation-smoke
```

The accepted result is four Available controller Deployments, a `GitRepository`
and root/dev `Kustomization` at `Ready=True`, and the smoke ConfigMap present.
Merge a bounded smoke change through the normal PR path and observe Flux
reconcile the approved revision. Direct `kubectl apply` is not valid evidence.

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
- `kubectl --context colima-mac-studio-solo -n flux-system get deployments`
- `flux check`
- `flux get sources git -A`
- `flux get kustomizations -A`
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
