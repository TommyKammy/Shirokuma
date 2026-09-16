---
project: Shirokuma
doc_id: "ADR-0010"
title: "Use LangGraph plus MCP Gateway for agent workflows"
status: "accepted"
created: 2026-07-05
updated: 2026-07-05
version: "0.2"
area: "adr"
tags: [shirokuma, adr, v0.2]
---

# ADR-0010: Use LangGraph plus MCP Gateway for agent workflows

## Status

Accepted

## Context

ShirokumaのAgentは単発Q&Aではなく、Issue理解、metadata検索、DuckDB検証、PR作成、CI結果解釈、修正PRという長時間ワークフローを扱う。状態管理、human-in-the-loop、tool routing、traceabilityが必要になる。

## Decision

Agent workflowの第一候補をLangGraphとし、tool access boundaryをMCP Gatewayに集約する。OpenHandsはcoding/PR agent、kagentはKubernetes SRE agentとして専門利用する。

## Alternatives Considered

- **CrewAI**: 役割分担の表現は分かりやすいが、Shirokumaでは状態機械・長時間workflow・checkpointを重視する。
- **OpenAI Agents SDK**: クラウドLLM本線とは相性が良いが、model/provider中立性とlocal LLM比較のためLangGraphを優先する。
- **自作ステートマシン**: 最小依存だが、eval/trace/human-in-loopを自前で作る負担が大きい。
- **OpenHandsだけ**: PR生成には強いが、全Agent orchestrationを任せるとtool境界が曖昧になる。

## Consequences

- Agent workflowの再現性と観測性が上がる。
- LangGraph/MCP双方のバージョン追随が必要になる。
- MCP tool allowlistとegress制限が必須になる。

## Related

- [[05_Modules/055_Agent_Runtime]]
- [[04_Development/042_Codex_Supervisor_GitHub_Workflow]]
