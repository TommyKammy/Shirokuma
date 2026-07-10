---
project: Shirokuma
doc_id: "ADR-0016"
title: "Use Colima on Mac Studio Solo as primary lab runtime"
status: accepted
created: 2026-07-05
updated: 2026-07-05
version: "0.2.1"
area: "adr"
tags: [shirokuma, adr]
---

# Use Colima on Mac Studio Solo as primary lab runtime

## Status

accepted

## Context

The v0.2 lab hardware is fixed: one Mac Studio M3 Ultra with 512GB unified memory and 4TB SSD. Colima is already installed. Comparing Lima, UTM, Tart, or remote clusters would waste effort and obscure the main project goal.

## Decision

Use Colima as the fixed container VM runtime. The primary profile is `mac-studio-solo`: macOS → Colima VM Linux/arm64 → **Colima built-in k3s** → Shirokuma stack. Use `--vm-type vz` by default. Rosetta is opt-in only for explicit x86_64 experiments. kind is not the default long-running runtime; it is reserved for CI/reset experiments where disposable clusters are more important than persistence.

## Resource policy

- `solo-lite`: 16 CPU / 96GB / 400GB.
- `solo-core`: 32 CPU / 192GB / 1TB.
- `solo-heavy`: 48-64 CPU / 256-320GB / 2TB.
- Reserve macOS memory for IDE, browser, Codex supervisor, and local MLX LLM.

## Alternatives Considered

| Alternative | Rejected / deferred because |
|---|---|
| Docker Desktop | Not the chosen installed runtime; less aligned with lightweight VM control. |
| Lima raw | Colima already wraps Lima-style workflows and is installed. |
| UTM/Tart | Useful for VM experiments, but not needed for container/K8s lab. |
| Cloud Kubernetes | Side option only; contradicts self-contained Mac lab. |
| kind default | Deferred. Fast reset is useful, but Colima already provides built-in Kubernetes and the runbooks are written around `colima start --kubernetes`. kind remains CI/reset-only. |
| Multi-node physical cluster | Out of scope for v0.2. |

## Consequences

- Every deployment note must assume Apple Silicon and arm64 first.
- ARM64 image gaps become a tracked risk.
- Full rebuild can use `colima delete` and GitOps bootstrap.
- OQ-001 is closed: default Kubernetes runtime is Colima built-in k3s; kind is CI/reset-only.
