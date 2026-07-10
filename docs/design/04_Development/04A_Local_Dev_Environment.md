---
project: Shirokuma
doc_id: "DEV-04A"
title: "Local Dev Environment"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2.1"
area: "development"
tags: [shirokuma, local-dev, colima]
---

# Local Dev Environment

## Primary profile: `mac-studio-solo`

The only mainline development environment is a single Mac Studio M3 Ultra with Colima. Use Colima built-in k3s as the default long-running Kubernetes runtime. Use kind only for CI/reset experiments where fast cluster disposal matters.

## Colima startup profiles

### Lite

```bash
colima start --vm-type=vz --arch aarch64 \
  --cpu 16 --memory 96 --disk 400 \
  --kubernetes --runtime docker
```

### Core

```bash
colima start --vm-type=vz --arch aarch64 \
  --cpu 32 --memory 192 --disk 1000 \
  --kubernetes --runtime docker
```

### Heavy benchmark

```bash
colima start --vm-type=vz --arch aarch64 \
  --cpu 48 --memory 256 --disk 2000 \
  --kubernetes --runtime docker
```

`solo-heavy` may go up to 64 CPU / 320GB memory for temporary Spark/warehouse benchmark work if macOS native local LLM is not running.

## Rosetta rule

Rosetta is not default. Use it only if the WP accepts x86_64 emulation:

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

## Reset path

```bash
shirokuma chill --all
colima stop
# destructive rebuild
colima delete -f
shirokuma init --profile mac-studio-solo
```

See [[08_Runbooks/RB-013_Nuke_and_Rebuild_mac_studio_solo]].
