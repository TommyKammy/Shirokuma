---
project: Shirokuma
doc_id: "ADR-0015"
title: "Use AGENTS.md as mandatory repository instruction"
status: "accepted"
created: 2026-07-05
updated: 2026-07-05
version: "0.2"
area: "adr"
tags: [shirokuma, adr, v0.2]
---

# ADR-0015: Use AGENTS.md as mandatory repository instruction

## Status

Accepted

## Context

Codex supervisorやOpenHandsにIssueを任せる場合、毎回長いシステムプロンプトでルールを渡すより、repository内に標準化されたAGENTS.mdを置く方が再現性が高い。Shirokumaでは複数サブディレクトリに異なる安全ルールが必要になる。

## Decision

Root AGENTS.mdを必須とし、必要に応じてsubtree AGENTS.mdを置く。Issue/PR templateはAGENTS.md参照を必須にする。

## Alternatives Considered

- **READMEだけ**: 人間向けには良いがAgent用の禁止事項やDoDが散らばる。
- **Promptだけ**: 履歴に残りにくく、repo内レビュー対象にならない。
- **外部Policy DB**: 強いが初期Labには重い。

## Consequences

- Agent指示がGit管理される。
- AGENTS.md自体の品質がAgent成果物に強く影響する。
- subtree指示が衝突しないようDoc mapが必要。

## Related

- [[04_Development/043_AGENTS_md_Guide]]
- [[99_Project_Files/AGENTS.md]]
