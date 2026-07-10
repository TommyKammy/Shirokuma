---
project: Shirokuma
doc_id: "REQ-033"
title: "Agent Requirements"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2"
area: "requirements"
tags: [shirokuma, agent, requirements]
---

# Agent Requirements

## Agent要件

| ID | Requirement | Acceptance |
|---|---|---|
| AR-001 | AgentはIssue本文、関連ファイル、AGENTS.md、ADRを読む | PR本文に参照を記載 |
| AR-002 | Agentは変更前にPlanを出す | PR descriptionにPlanあり |
| AR-003 | Agentはテストコマンドを実行または提案する | CI結果またはローカルログあり |
| AR-004 | Agentは危険操作を避ける | Policy violationが出た場合に修正PR |
| AR-005 | AgentはPawprintを記録する | OTel/ClickHouseにevent |
| AR-006 | Agentは不確実性を明記する | PRにAssumptions/Risks |
| AR-007 | AgentはUnknown codeを無断実行しない | Supply chain policy通過 |
| AR-008 | AgentはSecretsをログ出力しない | secret scan合格 |

## Agent Type

- SRE Bear
- Data Engineer Bear
- Query Doctor Bear
- Catalog Curator Bear
- Policy Bear
- FinOps Bear
- Release Bear
