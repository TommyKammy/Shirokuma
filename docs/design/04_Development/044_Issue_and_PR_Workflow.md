---
project: Shirokuma
doc_id: "DEV-044"
title: "Issue and PR Workflow"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2"
area: "development"
tags: [shirokuma, github, workflow]
---

# Issue and PR Workflow

## Issue types

| Type | Use |
|---|---|
| Epic | Level/Epic単位 |
| Work Package | WBS単位。基本は1 WP = 1 Issue |
| ADR | 設計判断が必要なIssue |
| Bug | 不具合 |
| Runbook | 運用手順追加/修正 |
| Research | OSS比較、ライセンス、ベンチマーク調査 |

## Issue must include

- Summary
- Context
- Scope
- Non-scope
- Deliverables
- Acceptance criteria
- Test commands
- Related ADR
- Risk level
- Agent instructions

## PR must include

- Intent
- Changes
- Related Issue
- Tests
- Screenshots/logs if applicable
- Risk
- Rollback
- Agent disclosure
- Checklist
