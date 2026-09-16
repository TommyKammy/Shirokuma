---
project: Shirokuma
doc_id: "ARCH-02A"
title: "Observability and AgentHouse"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2"
area: "architecture"
tags: [shirokuma, observability, clickhouse]
---

# Observability and AgentHouse

## v0.2 change

ClickHouse minimal single-instance is moved to **Level 2** because Pawprint telemetry depends on a structured event store. Loki remains log storage; ClickHouse stores normalized agent/action/query events.

## Data stores

| Store | Role |
|---|---|
| Prometheus | Metrics |
| Grafana | Dashboards and alerts |
| Loki | Logs |
| Tempo | Traces |
| OpenTelemetry | Common collection and trace format |
| ClickHouse | AgentHouse: Pawprints, query history extracts, benchmark events, FinOps events |
| PostgreSQL | Control-plane metadata for Polaris, Trino Gateway, and small app state |

## Pawprint event model

Core fields:

```text
event_id, timestamp, agent_id, task_id, repo, issue, pr,
action_type, target_system, risk_level, approval_tier,
model_provider, model_name, prompt_tokens, completion_tokens,
inference_cost_estimate, cpu_seconds, memory_mb_seconds,
status, blocked_by_policy, human_override
```

The AgentHouse enables one of the main Shirokuma KPIs: how much work agents complete safely, at what cost, and with what error rate.
