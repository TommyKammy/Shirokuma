---
project: Shirokuma
doc_id: "PF-AGENTS"
title: "Root AGENTS.md for Shirokuma"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2.1"
area: "project-files"
tags: [shirokuma, agents]
---

# AGENTS.md

## Mission

You are working on Shirokuma, an agentic OSS data cloud lab running on a single Mac Studio M3 Ultra through Colima.

## Hard constraints

- Primary profile is `mac-studio-solo`.
- Assume Linux/arm64 inside Colima.
- Use Colima built-in k3s as the default local Kubernetes runtime; kind is CI/reset-only.
- Do not introduce multi-node physical cluster assumptions.
- Do not add Ceph to the mainline path.
- Prefer SeaweedFS for object storage.
- Use MinIO only as a pinned-image/source-build fallback when a compatibility experiment explicitly requires it; record digest, CVE risk, and replacement plan.
- Do not add Apache Ranger to the default L3 path; use OPA and native RBAC unless an ADR changes this.
- Doris is benchmark-only, not resident.
- Agents must not bypass GitHub PR, CI, policy checks, and CODEOWNERS.

## Supervisor context

- Repository-owned design context lives under `docs/design/`.
- Before planning or implementation, read every path listed by the current issue under `Related docs / ADR`.
- Do not infer missing design decisions. Stop and report a blocker when a referenced document is absent.
- Run `make supervisor-preflight` before starting the host codex-supervisor loop.
- Do not commit workstation-local Obsidian paths; GitHub issues and durable repository docs must use repository-relative paths.

## Required checks

Before opening a PR:

1. Update related Obsidian note and WBS if scope changes.
2. Update ADR if an architectural decision changes.
3. Update ARM64 compatibility notes for new resident components.
4. Include test evidence.
5. Include rollback or nuke/rebuild note for infrastructure changes.
6. For storage changes, include host SSD free-space impact and backup/export notes.
