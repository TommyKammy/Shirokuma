---
project: Shirokuma
doc_id: "ARCH-02C"
title: "Deployment Topologies"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2.1"
area: "architecture"
tags: [shirokuma, deployment, colima]
---

# Deployment Topologies

## Primary topology: `mac-studio-solo`

`mac-studio-solo` is the mainline v0.2 topology.

```text
Mac Studio M3 Ultra / 512GB unified memory / 4TB SSD
  ↓ macOS native layer
  - shirokuma CLI
  - local LLM server: MLX / llama.cpp-compatible OpenAI endpoint
  - browser / IDE / Codex supervisor tooling
  ↓
Colima VM: Linux/arm64, --vm-type vz
  ↓
Colima built-in k3s Kubernetes
  ↓
Shirokuma stack:
  GitOps / OPA / Observability / SeaweedFS / Iceberg / Polaris / Trino / ClickHouse / StarRocks / Agents
```

## Colima resource profiles

| Profile | CPU | Memory | Disk | Use |
|---|---:|---:|---:|---|
| `solo-lite` | 16 | 96GB | 400GB | L0-L1 bootstrap, docs, CI smoke |
| `solo-core` | 32 | 192GB | 1TB | L0-L3 resident lab, Trino, Polaris, ClickHouse minimal, StarRocks |
| `solo-heavy` | 48-64 | 256-320GB | 2TB | L4 Spark/Comet, L5 benchmarks, temporary large tests |

Rules:

- Do not allocate all host memory to Colima. macOS, IDE, browser, and local LLM need reserved unified memory.
- Local MLX LLM is **outside** Colima and therefore its memory budget must be reserved on macOS.
- `--vm-type vz` is the default on Apple Silicon.
- Rosetta is **not** default. Use `--vz-rosetta` only for a WP that explicitly accepts x86_64 emulation.
- Colima built-in k3s is the default Kubernetes runtime.
- kind is reserved for CI/reset experiments and must not be the default long-running lab profile.
- `cloud-lab`, `x86-multinode`, and `two-mac-cluster` are side options only.

Example:

```bash
colima start --vm-type=vz --arch aarch64 \
  --cpu 32 --memory 192 --disk 1000 \
  --kubernetes --runtime docker
```

For x86_64-only experiments:

```bash
colima start --vm-type=vz --vz-rosetta --arch x86_64 \
  --cpu 16 --memory 96 --disk 400
```

## Side options

| Topology | Status | Note |
|---|---|---|
| `cloud-lab` | side option | Useful for x86_64 image gaps or cloud-like networking. Not primary. |
| `x86-multinode` | future side option | Required for serious Ceph and HA validation. |
| `mac-studio-plus-nas` | future side option | Backup/storage expansion only, not compute cluster. |
| `two-mac-cluster` | out of scope in v0.2 | Mentioned only as future experiment. |
