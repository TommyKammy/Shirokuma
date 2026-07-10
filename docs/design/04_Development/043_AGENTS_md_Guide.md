---
project: Shirokuma
doc_id: "DEV-043"
title: "AGENTS.md Guide"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2"
area: "development"
tags: [shirokuma, agents-md, development]
---

# AGENTS.md Guide

## 目的

AGENTS.mdは、AI Coding Agentに対するリポジトリ内の明示的な作業指示です。Shirokumaでは必須ファイルとします。

## 記載すべき内容

- プロジェクト概要
- 禁止事項
- ディレクトリ構造
- よく使うコマンド
- テストコマンド
- コーディング規約
- PRの書き方
- セキュリティ注意
- 依存追加ルール
- Docs更新ルール

## 配置方針

```text
AGENTS.md                    # repo全体
cmd/shirokuma/AGENTS.md      # CLI固有
controllers/AGENTS.md        # Operator固有
agents/AGENTS.md             # Agent runtime固有
charts/AGENTS.md             # Helm固有
policies/AGENTS.md           # OPA/Kyverno固有
benchmarks/AGENTS.md         # benchmark固有
```

## 原則

- 短く、具体的に、テストコマンドを明記する。
- 失敗しがちなパターンを禁止事項に入れる。
- Agentが迷ったらIssueへ質問コメントするよう指示する。
