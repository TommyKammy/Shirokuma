---
project: Shirokuma
doc_id: "MOD-051"
title: "shirokuma CLI"
status: draft
created: 2026-07-05
updated: 2026-09-17
version: "0.3.0"
area: "module"
tags: [shirokuma, module]
---

# shirokuma CLI

## Goal

人間とAI Agentの共通入口となるCLIを提供します。

## Commands

```text
init, status, status --export, doctor, plan, pr, ask, data, warehouse, lineage, paws, chill, wake, nuke, restore, bench, version
```

## Implementation

- Language: Go
- CLI framework: Cobra
- Config: YAML + env
- Output: table/json/markdown
- Kubernetes: client-go
- GitHub: gh CLI or GitHub API wrapper

The L0 `doctor` command is implemented with external `kubectl` and repository
`make verify-security` checks to avoid adding a Kubernetes client dependency at
this level. It emits schema version 1 JSON or bounded Markdown summaries. Raw
stdout/stderr, kubeconfig content, credentials, environment values, and prompts
are never copied into the report.

```bash
shirokuma doctor --profile local-lite --context colima-mac-studio-solo --output json
```

The repository root is discovered by walking upward from the current directory.
Installed binaries invoked elsewhere must pass `--repo-root /path/to/Shirokuma`
so the policy check runs against the intended checkout.

The report status is `healthy` only when Kubernetes readiness, all four Flux
controller Deployments, every discovered GitRepository/Kustomization/HelmRelease,
and repository policy are healthy. Flux resources must report `Ready=True` for
their observed generation. A degraded report is
still emitted successfully so it can be attached as machine-readable triage
evidence.

## Example

### Local experimental Data execution

ADR-0034のlocal modeでは、`data run`が既存のQwen → Data Engineer → Catalog
Curatorを呼び出す。`data status TOKEN`は保存証跡だけを読み、`data resume TOKEN`は
同一handoffのDataを再実行せず検証済み成果物から継続する。完了済みresumeは再検証のみ。
現時点の処理は固定fixture上のuppercase_names、name_lengths、row_countの3種類である。

```bash
shirokuma data run --case name_lengths_ja
shirokuma data run --request-stdin
shirokuma data status TOKEN
shirokuma data resume TOKEN
```

早期`allocated`行は識別子の通知であり永続intentや実処理開始の証明ではない。
stdinの自然文はメモリとchild stdinだけで受け渡し、argv/env/receiptへ本文を残さない。
statusの`journal-only`と`saved-evidence`、処理結果と観測の成功を分ける。
取消時はPython ownerのcleanupを待ち、終了未確認ならtoken/PIDとunknownを返す。
既存専用venvを用い、環境の自動installやservice起動はしない。詳細とexit契約は
[Data to Catalog runbook](../../../tools/local_experiment/bears/DATA_CATALOG.md)を参照。

### Historical delivery examples

以下は全体設計のコマンド例であり、すべて実装済みという意味ではない。
PR作成などの外部deliveryは別途許可された場合だけ使用する。

```bash
shirokuma doctor --profile local-lite --output markdown
shirokuma status --export ./rebuild-evidence/status-before.json
shirokuma pr --issue 123 --agent data-engineer
shirokuma warehouse scale analyst-m --size L --as-pr
shirokuma nuke --yes-i-know-this-deletes-colima
shirokuma restore --profile demo
```
