---
project: Shirokuma
doc_id: "RB-001"
title: "Bootstrap local-lite lab"
status: draft
created: 2026-07-05
updated: 2026-07-10
version: "0.3"
area: "runbook"
tags: [shirokuma, runbook]
---

# Bootstrap local-lite lab

## Purpose

Start and recover the accepted `solo-lite` Colima built-in k3s baseline on the
single `mac-studio-solo` host.

## Preconditions

- Colima, kubectl, and Helm are installed.
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

## Rollback

- Stop the profile with `colima stop --profile mac-studio-solo`.
- Revert the focused PR if repository checks regress.
- Restore exported data only after the rebuilt baseline passes its status gate.
- A Colima disk cannot be safely shrunk in place; export, delete, and rebuild
  when returning to a smaller profile.

## Notes

このRunbookはGxP/本番SLAを対象にしません。Lab実験用です。
