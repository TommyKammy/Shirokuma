---
project: Shirokuma
doc_id: "DEV-042"
title: "Codex Supervisor x GitHub Workflow"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2"
area: "development"
tags: [shirokuma, codex, github, agent]
---

# Codex Supervisor x GitHub Workflow

## Operating model

```text
GitHub Issue → Codex/Coding Agent → branch → PR → CI/Policy → review → merge → Argo CD reconcile
```

Agents do not apply high-risk changes directly. The Codex supervisor controls task assignment, scope, and merge decisions.

## Agent execution sandbox

Prohibitions in prompts are not enough. Agent execution must be sandboxed:

| Control | Requirement |
|---|---|
| Runner isolation | Coding agents run inside disposable containers or ephemeral VMs, not on the host shell. |
| Network egress | Default deny; allow GitHub, package registries, and documented vendor endpoints only. |
| Secrets | No long-lived production-like secrets. Use short-lived tokens or no secrets for code tasks. |
| Filesystem | Workspace mount only. No access to host home, SSH keys, browser profiles, or Obsidian vault secrets. |
| Package install | Dependency additions require diff review and lockfile update. No `curl | bash`. |
| Tool allowlist | MCP tools are explicitly allowlisted per agent role. |
| Audit | Every agent action creates a Pawprint event with model, tokens, repo, task, and risk tier. |

## PR requirements

- Link to Work Package ID.
- Include test evidence.
- Include policy impact summary.
- Include agent self-review.
- Include rollback or nuke/rebuild note if infra change.

## Branch protection

- Required CI.
- Required policy check.
- CODEOWNERS review for infra/security changes.
- Human approval for Tier 3/4 changes.
