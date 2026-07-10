---
project: Shirokuma
doc_id: "DEV-041"
title: "Repository Strategy"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2"
area: "development"
tags: [shirokuma, repo, development]
---

# Repository Strategy

## 推奨: Monorepo

```text
shirokuma/
  AGENTS.md
  README.md
  cmd/shirokuma/
  internal/
  api/v1alpha1/
  controllers/
  agents/
  mcp/
  charts/
  deploy/
  opentofu/
  policies/
  examples/
  benchmarks/
  docs/
  obsidian/
  .github/
```

## 理由

- Agentが全体文脈を読みやすい。
- CRD、CLI、Chart、Policy、Docsの整合をCIで見やすい。
- 初期プロジェクトではリポジトリ分割による同期コストが高い。

## 将来分割候補

| Repo | 分割条件 |
|---|---|
| shirokuma-cli | CLIが安定し外部利用者が増える |
| shirokuma-operator | CRD/API versioningが独立する |
| shirokuma-agents | Agent runtimeの依存が重くなる |
| shirokuma-charts | Helm chart配布が必要になる |
| shirokuma-docs | Docs site化する |
