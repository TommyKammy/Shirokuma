---
project: Shirokuma
doc_id: "MOD-051"
title: "shirokuma CLI"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2.1"
area: "module"
tags: [shirokuma, module]
---

# shirokuma CLI

## Goal

人間とAI Agentの共通入口となるCLIを提供します。

## Commands

```text
init, status, status --export, doctor, plan, pr, ask, warehouse, lineage, paws, chill, wake, nuke, restore, bench, version
```

## Implementation

- Language: Go
- CLI framework: Cobra
- Config: YAML + env
- Output: table/json/markdown
- Kubernetes: client-go
- GitHub: gh CLI or GitHub API wrapper

The L0 `doctor` command is implemented with external `kubectl` and repository
`make verify-security` checks to avoid adding a Kubernetes client dependency at
this level. It emits schema version 1 JSON or bounded Markdown summaries. Raw
stdout/stderr, kubeconfig content, credentials, environment values, and prompts
are never copied into the report.

```bash
shirokuma doctor --profile local-lite --context colima-mac-studio-solo --output json
```

The repository root is discovered by walking upward from the current directory.
Installed binaries invoked elsewhere must pass `--repo-root /path/to/Shirokuma`
so the policy check runs against the intended checkout.

The report status is `healthy` only when Kubernetes readiness, every discovered
Argo CD Application, and repository policy are healthy. A degraded report is
still emitted successfully so it can be attached as machine-readable triage
evidence.

## Example

```bash
shirokuma doctor --profile local-lite --output markdown
shirokuma status --export ./rebuild-evidence/status-before.json
shirokuma pr --issue 123 --agent data-engineer
shirokuma warehouse scale analyst-m --size L --as-pr
shirokuma nuke --yes-i-know-this-deletes-colima
shirokuma restore --profile demo
```
