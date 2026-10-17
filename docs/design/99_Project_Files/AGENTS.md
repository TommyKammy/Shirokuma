---
project: Shirokuma
doc_id: "PF-AGENTS"
title: "Root AGENTS.md for Shirokuma"
status: accepted
created: 2026-07-05
updated: 2026-10-16
version: "0.8.0"
area: "project-files"
tags: [shirokuma, agents]
---

# AGENTS.md

## Mission and Current Operating Model (2026-10-16 — ADR-0036 / DEV-04F)

Shirokuma is an experimental OSS data cloud lab on a single Mac Studio M3 Ultra
through Colima (`mac-studio-solo`), with no current production-use objective.
Follow
`docs/design/07_ADR/ADR-0036_Adopt_Commander_Worker_Model_and_Pragmatic_Core_Reset.md`
and
`docs/design/04_Development/04F_Commander_Worker_and_Pragmatic_Roadmap.md`
(which build upon and supersede the scheduling and CVE source-build rules in
`docs/design/07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md`
and `docs/design/04_Development/04B_Autonomous_Local_Development.md`).

### Commander–Worker Division of Labor

- **Commander / Architect / Reviewer (`Antigravity`)**:
  - Owns architecture, roadmap prioritization, Work Package scoping, and
    repository/Vault documentation alignment.
  - Crafts bounded task prompts for the worker with explicit anti-over-engineering
    guardrails.
  - Reviews worker diffs, test outcomes, and runtime behavior; rejects
    fixture-only hacks, defensive symlink/fd paranoia, or scope drift.
- **Implementation Worker (`codex-cli` with `gpt-6.1-sol` Medium)**:
  - Invoked via `/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex exec -m gpt-6.1-sol -c model_reasoning_effort="medium" -s workspace-write -C "$(git rev-parse --show-toplevel)" "<task>"`.
  - Executes scoped code edits and focused unit/integration tests in the target
    files specified by the Commander.
- **Retirement of Unattended 500-Session Autonomous Loop**:
  - The historical `autonomous_runner.py` / `gate_supervisor.py` /
    `restart_supervisor.py` loop is retired.

### Anti-Over-Engineering Rules (Mandatory for All Agents)

1. **No Fixture-Only Features**: Do not hardcode features to only accept a single
   `--case retained-fixture`, 2-row table, or 48-row dataset. Build general
   functions that accept arbitrary valid SQL, tables, and parameters.
2. **No Symlink / FD Paranoia in Local Tooling**: Do not add multi-layer
   `openat2(NO_XDEV)`, fd-relative `unlink`/`chmod` state machines, or Mach
   clock-drift guards to local developer/experiment scripts. Use clean standard
   library path handling. (When fixing macOS temp directory tests, canonicalize
   the test temp root with `Path.resolve()` rather than rewriting production path
   guards.)
3. **No Supervisors of Supervisors**: Do not create new wrapper scripts to
   supervise or audit AI sessions.
4. **No Upstream Binary Surgery for CVEs**: Use official upstream ARM64 images
   pinned by SHA256 digest. Do not custom-compile or patch upstream OSS binaries
   or APK layers (Flux, Trino, Polaris) to silence upstream CVE scanners.
5. **Concise, Human-Readable Records**: Keep milestone and WBS updates to 3–5
   concise lines per milestone. Do not dump raw SHA256 lists or lengthy negative
   disclaimers into planning documents.

## Boundaries

- Host execution and changes to the dedicated Shirokuma Colima/k3s lab are
  authorized: local build/import/registry, manifests, configuration, local RBAC,
  policy, services, restarts, and disposable experiment data.
- Do not create Issues/PRs, write to GitHub, push, or publish to external registries.
- Additional paid spending, public exposure, unrelated projects, global host
  settings, and project-purpose changes require separate owner authorization.
- Keep secrets, generated data, and workstation-local paths out of Git.

## Architecture Constraints

- Primary profile is `mac-studio-solo`, Linux/arm64 in Colima built-in k3s (`--vm-type vz`).
  `kind` is CI/reset-only; no physical multi-node assumptions.
- Primary Core Stack:
  - **Object Storage & Catalog**: SeaweedFS + Apache Iceberg + Apache Polaris (+ PostgreSQL)
  - **Compute & Virtual Warehouse**: Trino + Trino Gateway (XS/S auto-suspend & wake) + Spark/Comet
  - **DataOps & Governance**: Dagster + dbt + OpenMetadata + OPA / native admission policies
  - **Agent Control Plane**: `shirokuma` CLI + MCP Gateway + 5 Core Bears (SRE, Query Doctor, Data Engineer, Catalog Curator, Policy Gatekeeper) + ClickHouse AgentHouse + OTel Pawprints
  - **Operator Surface**: Shirokuma Workbench UI + Superset + Grafana
- Peripheral engines (StarRocks, Doris, Gravitino, XTable, RustFS, Cube, Ray, Amoro)
  are deferred to the Optional Plugin Track until Phase 1–3 core milestones are complete.
- Keep pinned Flux v2 (`source-controller`, `kustomize-controller`, `helm-controller`,
  `notification-controller` in `flux-system`).

## Verification

- Run focused unit/integration tests for changed components first.
- At milestones, run `make verify-local-experiment` and `make verify-design-context`.
- When working on live cluster capabilities (Phase 1+), verify with `shirokuma doctor`
  and targeted runtime smoke checks.
