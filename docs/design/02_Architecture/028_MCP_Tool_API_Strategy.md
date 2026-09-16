---
project: Shirokuma
doc_id: "ARCH-028"
title: "MCP Tool API Strategy"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2"
area: "architecture"
tags: [shirokuma, architecture]
---

# MCP Tool API Strategy

## 方針

MCPはAgent APIの第一候補です。ただし、MCPが存在しない製品を不採用にする必要はありません。REST API、CLI、JDBC、Kubernetes CRD、GitHub APIを薄くMCP化します。

## 初期MCP Tools

| Tool | Backing system | Purpose |
|---|---|---|
| github | GitHub API | Issue/PR/Checks/Labels |
| k8s-readonly | Kubernetes API | Pod/Job/Event/Resource診断 |
| dbt | dbt MCP or wrapper | model/test/docs/lineage |
| openmetadata | OpenMetadata API/MCP | glossary/owner/lineage |
| trino | JDBC/REST wrapper | EXPLAIN/query history |
| clickhouse | MCP/JDBC | AgentHouse query |
| shirokuma | CLI wrapper | doctor/plan/warehouse/lineage |

## Security

- Tool allowlist
- Input schema validation
- Command sandbox
- No shell by default
- Short-lived credentials
- Full audit to Pawprints
