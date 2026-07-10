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

## Example

```bash
shirokuma doctor --profile local-lite --output markdown
shirokuma status --export ./rebuild-evidence/status-before.json
shirokuma pr --issue 123 --agent data-engineer
shirokuma warehouse scale analyst-m --size L --as-pr
shirokuma nuke --yes-i-know-this-deletes-colima
shirokuma restore --profile demo
```
