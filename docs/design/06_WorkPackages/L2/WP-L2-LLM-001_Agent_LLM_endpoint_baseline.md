---
project: Shirokuma
doc_id: "WP-L2-LLM-001"
title: "WP-L2-LLM-001 Agent LLM endpoint baseline"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.3.0"
area: "workpackage"
tags: [shirokuma, workpackage, l2, agent]
---

# WP-L2-LLM-001 Agent LLM endpoint baseline

## Summary

クラウドAPI本線 + macOSネイティブlocal LLM実験枠のLLM接続方針を実装する。
ADR-0034のローカル開発では、OWNERが指定した既存Qwen endpointを利用し、
Cloud APIの追加費用判断を待たずに実LLMの機能検証を進める。

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
- [x] Local endpoint mockまたは実LLMでsmoke taskが成功する。
- [x] token/cost/latencyがPawprintに記録される（実測token/latency、価格未定のcostはNULL）。

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

## 2026-09-17 real local LLM functional evidence

OWNER指定の既存MTPLX `qwen3.8-flash-next`（`http://127.0.0.1:8001/v1`）で、
`plan → real MCP dbt_list_models → summarize`の2 graph runsが成功した。
`mart_fixture` / `stg_fixture`の2モデル名、件数、保持検証記録の有無が取得元と完全一致。
8件のPawprintを再読込し、task/trace、provider/model、入力/出力tokens、latencyを照合した。
推論4回は0.482–1.731秒、入力計376・出力計128 tokens。costは未価格評価のNULLを保持。
Colima→`host.lima.internal:8001`からの実モデル推論も成功した。

空一覧・別名・検証記録なし・16件上限など5合成fixture×2回も10/10成功。
入力計1,510・出力計644 tokens、10監査eventの即時読戻しが成功した。
`rtk make local-llm-qwen-eval`で再実行でき、出力の事実はfixtureに対して厳密照合する。
LLM/evaluationの41 focused testsを含む全199回帰・設計関連testsが成功した。

32,768 contextをAPIで確認し、MTPLXが公開する`enable_thinking=false`を設定した。
設定は専用profileへ分離し、出力512 tokens、timeout 60秒、8 KiB以下の固定入力、retryなし。
既存サーバ・自動起動・weightsは変更していない。runtime停止からの復旧は既存Colima設定・
保持volumeを維持して実施した。新規課金・外部送信・GitHub操作はない。

再実行は`rtk make local-llm-qwen-smoke`。deterministic baselineは`rtk make local-llm-smoke`。
Cloud criterionは未達のまま保持するが、local実LLM接続の依存にはしない。
本結果は小さな読み取りinventory taskの検証であり、汎用モデル品質・診断・自動修復・本番受入ではない。
