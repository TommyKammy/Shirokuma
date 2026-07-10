---
project: Shirokuma
doc_id: "DEV-04A"
title: "Local Dev Environment"
status: draft
created: 2026-07-05
updated: 2026-07-10
version: "0.2.4"
area: "development"
tags: [shirokuma, local-dev, colima]
---

# Local Dev Environment

## Primary profile: `mac-studio-solo`

The only mainline development environment is a single Mac Studio M3 Ultra with Colima. Use Colima built-in k3s as the default long-running Kubernetes runtime. Use kind only for CI/reset experiments where fast cluster disposal matters.

## Colima startup profiles

### Lite

```bash
colima start --profile mac-studio-solo --vm-type=vz --arch aarch64 \
  --cpu 16 --memory 96 --disk 400 \
  --kubernetes --runtime docker
```

### Core

```bash
colima start --profile mac-studio-solo --vm-type=vz --arch aarch64 \
  --cpu 32 --memory 192 --disk 1000 \
  --kubernetes --runtime docker
```

### Heavy benchmark

```bash
colima start --profile mac-studio-solo --vm-type=vz --arch aarch64 \
  --cpu 48 --memory 256 --disk 2000 \
  --kubernetes --runtime docker
```

`solo-heavy` may go up to 64 CPU / 320GB memory for temporary Spark/warehouse benchmark work if macOS native local LLM is not running.

## Resource envelope

- Minimum host reserve: 192GB of unified memory for macOS, the IDE, browser, Codex supervisor, and any native services.
- VM memory maximum: 320GB. Never raise Colima beyond this limit on the 512GB host; reduce the VM profile when macOS memory pressure or swap activity is elevated.
- The accepted baseline is `solo-lite` at 16 CPU / 96GB memory / 400GB disk. Scale to `solo-core` or `solo-heavy` only when the active WP records the need.
- Treat the 400GB, 1TB, and 2TB VM disk values as host SSD capacity commitments even if the VM disk is sparse. Before growing or rebuilding a profile, check host free space and retain enough space for required exports or backups outside the VM.
- A Colima disk can grow; do not rely on an in-place shrink for rollback. Use export, delete, and rebuild when returning to a smaller disk profile.

## Rosetta rule

Rosetta is not default. Evaluation requires a Work Package decision that records the exact x86_64 image and digest, the native ARM64 failure evidence, the experiment boundary, and the removal plan. Record the result in [`docs/design/10_Research/106_ARM64_Container_Image_Compatibility.md`](../10_Research/106_ARM64_Container_Image_Compatibility.md). Missing or inconclusive evidence keeps the component out of the resident profile.

Only after that decision may the WP evaluate an x86_64 profile:

```bash
colima start --vm-type=vz --vz-rosetta --arch x86_64 \
  --cpu 16 --memory 96 --disk 400
```

## Local LLM outside VM

Local models run on macOS native using MLX or llama.cpp-compatible servers.

```text
macOS MLX server : http://127.0.0.1:11434 or http://127.0.0.1:8000/v1
Colima VM Agent  : calls host endpoint via host address / port-forwarded gateway
```

The Agent Runtime must support cloud API and local OpenAI-compatible endpoint routing.

## Status and lifecycle checks

Use the repository-owned wrapper for the accepted baseline. It always targets
`mac-studio-solo` and `colima-mac-studio-solo`; it does not depend on or change
the caller's current kubectl context.

```bash
make colima-start
make colima-status
```

`start` pins VZ, aarch64, 16 CPU, 96GB memory, 400GB disk, Docker, and Colima
built-in Kubernetes. `status` fails closed unless the VM reports `aarch64`, at
least one node reports `arm64` and `Ready=True`, `kubectl cluster-info` succeeds,
and `helm list --kube-context colima-mac-studio-solo --all-namespaces` reaches
the Kubernetes API. Run the underlying `scripts/colima_baseline.sh` directly
when a Make target is unavailable.

Run both Colima views after start or recovery. `colima status` is the operator-readable view; `colima list --json` is the repeatable machine-readable record.

```bash
colima status --profile mac-studio-solo
colima list --json
colima ssh --profile mac-studio-solo -- uname -m
kubectl --context colima-mac-studio-solo get nodes -o wide
```

The accepted result is a running VZ VM reporting `aarch64`, Docker runtime, and a ready node from Colima built-in Kubernetes. A missing field, unexpected architecture, or unready node is a failed baseline, not an implied success.

For a non-destructive stop:

```bash
shirokuma chill --all
colima stop --profile mac-studio-solo
colima status --profile mac-studio-solo
```

`colima stop` preserves the VM disk. Restart with the same profile command, then rerun all status checks above.

## Reset and recovery

Reset is destructive. Export any non-reproducible object data, catalog metadata, and other required evidence outside the Colima VM first. Confirm host free space can hold the export and the selected replacement disk capacity. Git-tracked manifests and GitOps state are the rebuild source; the VM disk is not a backup.

Record the pre-reset state, stop workloads, and remove the VM:

```bash
colima status --profile mac-studio-solo
colima list --json
shirokuma chill --all
colima stop --profile mac-studio-solo --force
colima delete --profile mac-studio-solo --data --force
```

After exports and host free space have been checked, the automated equivalent
requires an explicit data-loss acknowledgement and rebuilds the same pinned
profile before running every status check:

```bash
scripts/colima_baseline.sh reset --confirm-data-loss
```

Recover the accepted `solo-lite` baseline with the same pinned command used for initial creation:

```bash
colima start --profile mac-studio-solo --vm-type=vz --arch aarch64 \
  --cpu 16 --memory 96 --disk 400 \
  --kubernetes --runtime docker --binfmt=false --activate=false
shirokuma init --profile mac-studio-solo
colima status --profile mac-studio-solo
colima list --json
colima ssh --profile mac-studio-solo -- uname -m
kubectl --context colima-mac-studio-solo get nodes -o wide
```

If recovery verification fails, preserve the command output and exported data, stop the VM, and do not promote the lab as ready. Repeating `colima delete --profile mac-studio-solo --data --force` is safe only after reconfirming that required data has been exported.
