---
project: Shirokuma
doc_id: "REQ-034"
title: "Platform Requirements"
status: draft
created: 2026-07-05
updated: 2026-07-14
version: "0.2.2"
area: "requirements"
tags: [shirokuma, platform, colima]
---

# Platform Requirements

## Fixed lab hardware

| Requirement | Value |
|---|---|
| Host | Mac Studio M3 Ultra |
| Memory | 512GB unified memory |
| Disk | 4TB SSD |
| Primary runtime | Colima |
| VM type | `vz` |
| Guest arch | Linux/arm64 |
| Kubernetes | Colima built-in k3s by default; kind only for CI/reset experiments |
| Multi-node | Out of scope for mainline v0.2 |

## Colima resource policy

| Requirement ID | Requirement |
|---|---|
| PLAT-COLIMA-001 | Colima VM resource allocation must be profile-based: `solo-lite`, `solo-core`, `solo-heavy`. |
| PLAT-COLIMA-002 | `solo-core` must leave sufficient macOS memory for IDE, browser, local LLM, and Codex supervisor workflows. |
| PLAT-COLIMA-003 | Local LLM inference memory must not be counted as Colima memory because MLX runs on macOS native outside the VM. |
| PLAT-COLIMA-004 | Rosetta/x86_64 emulation must be opt-in per WP and recorded in the ARM64 compatibility table. |
| PLAT-COLIMA-005 | Full rebuild path must include `colima stop`, `colima delete`, volume cleanup, and GitOps redeploy. |

## Flux GitOps policy

| ID | Requirement |
|---|---|
| PLAT-FLUX-001 | GitOps reconciler must use an approved stable `fluxcd/flux2` release pinned in the repository. Runtime `latest` and `main` are prohibited. |
| PLAT-FLUX-002 | L0 must install source-controller, kustomize-controller, helm-controller, and notification-controller in `flux-system`. Extra controllers require explicit approval. |
| PLAT-FLUX-003 | Cluster desired state must be rooted in a `GitRepository` and `Kustomization`; Helm workloads must use Flux Source resources and `HelmRelease`. |
| PLAT-FLUX-004 | Readiness must be evaluated from controller availability and Flux `Ready` conditions for the observed generation and revision. |
| PLAT-FLUX-005 | Bootstrap must verify controller image digest, signature, SBOM, provenance, ARM64 support, and the High/Critical vulnerability gate. |
| PLAT-FLUX-006 | Private Git credentials must use the approved Secret path and least privilege. Credentials must not be committed or included in evidence. |
| PLAT-FLUX-007 | Normal mutation and rollback must occur through Git PR/revert and Flux reconciliation, not direct cluster mutation. |
| PLAT-FLUX-008 | OpenTofu must own cluster prerequisites; Flux must own Git-managed self-upgrade and workload reconciliation after bootstrap. |
| PLAT-FLUX-009 | The default strict image profile must require High=0/Critical=0. The mac-studio-solo local lab may allow only ADR-approved, digest/CVE/package/version-bound High exceptions for at most 30 days; Critical and production use remain prohibited. |

## Storage policy

- No Ceph primary deployment on `mac-studio-solo`.
- Primary object store: SeaweedFS.
- MinIO: pinned-image/source-build fallback only, with CVE and replacement notes.
- RustFS requires maturity/security WP before promotion.
