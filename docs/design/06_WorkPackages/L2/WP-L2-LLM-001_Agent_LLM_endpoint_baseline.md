---
project: Shirokuma
doc_id: "WP-L2-LLM-001"
title: "WP-L2-LLM-001 Agent LLM endpoint baseline"
status: local-partial
created: 2026-07-05
updated: 2026-09-16
version: "0.2.1"
area: "workpackage"
tags: [shirokuma, workpackage, l2, agent]
---

# WP-L2-LLM-001 Agent LLM endpoint baseline

## Summary

クラウドAPI本線 + macOSネイティブlocal LLM実験枠のLLM接続方針を実装する。

## Metadata

| Field | Value |
|---|---|
| Level | L2 |
| Workstream | Agent |
| Depends-on | WP-L2-AGENT-001, WP-L2-OBS-001 |
| Profile | mac-studio-solo |
| Arch | linux/arm64 first |

## Context

Agent Runtimeの推論基盤が未定義だったため、v0.2で明示する。

## Scope

- Cloud API provider configを定義する。
- Local MLX/llama.cpp compatible endpoint configを定義する。
- VM内Agent Runtimeからhost endpointへ接続する方式を確認する。
- token/cost telemetryをPawprintへ送る。

## Non-scope

- 特定モデルの固定。
- local LLMの本線化。
- Colima VM内での大規模LLM推論。

## Deliverables

- ADR-0017
- Agent runtime config schema
- smoke test
- cost event schema

## Acceptance Criteria

- [ ] Cloud APIでAgent smoke taskが成功する。
- [ ] Local endpoint mockまたは実LLMでsmoke taskが成功する。
- [ ] token/cost/latencyがPawprintに記録される。

## Suggested Labels

`level:l2`, `area:agent`, `agent-ready`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP-L2-LLM-001: Agent LLM endpoint baseline. Read AGENTS.md, WBS Master, related ADRs, and this Work Package. Create a focused PR with tests, docs, rollback notes, and no unrelated dependencies.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] Scope外の変更を含まない。
- [ ] CI、policy check、secret scanが通る。
- [ ] 必要なPawprintまたは検証ログが残る。
- [ ] 関連Obsidian noteとWBS Masterが更新される。

## 2026-09-16 local functional evidence

LangGraph/provider config/loopback mockの接続検証が成功。2 graph runs、8相関event、Colima→macOS loopback接続、13 focused testsが成功した。実LLM推論やモデル品質の証明ではない。Cloud provider/model、認証環境変数名、明示予算はOWNER判断待ち。

[ADR-0034](../../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)によるlocal functional検証。旧GitHub/PR/CI gateは公開経路の履歴として保持する。実行手順・証跡・制限は[Local Experiment Milestones](../061_Local_Experiment_Milestones.md)を参照。
