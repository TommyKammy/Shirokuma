---
project: Shirokuma
doc_id: "MOD-051"
title: "shirokuma CLI"
status: draft
created: 2026-07-05
updated: 2026-09-18
version: "0.7.0"
area: "module"
tags: [shirokuma, module]
---

# shirokuma CLI

## Goal

人間とAI Agentの共通入口となるCLIを提供します。

## Commands

```text
init, status, status --export, doctor, plan, pr, ask, data, query, sre, policy, warehouse, lineage, paws, chill, wake, nuke, restore, bench, version
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

### Local experimental Query Doctor

`shirokuma query run --case retained-fixture`は、保持2行fixtureの固定SELECTと
同一SQLのJSON EXPLAIN、自身のquery historyを観測し、local Qwenの選択に従って
固定statistics/historyだけを追加確認する。`query status TOKEN`は保存証跡のみを読む。
任意SQL・設定変更・自動再実行・resumeは受け付けない。

JSONLの`evidence_token`は保存先、`query_run_token`は内部query identityである。
allocationは実行開始の証明ではなく、結果とchild exitの整合を確認後にfinalを返す。
取消時はownerのcleanupを待ち、不明ならtoken/PIDを残す。原SQLやpromptは出力しない。
`status`のexit0は読取成功であり、診断のpassedや最新runtimeの確認とは区別する。
旧証跡のstatusは保存内容の検証で、過去に存在しなかったhash sealを作らない。
詳細は[Query Doctor runbook](../../../tools/local_experiment/bears/QUERY_DOCTOR.md)を参照。

### Local experimental SRE diagnosis

`shirokuma sre run`は既存local Qwenを使い、doctor・Pod・Event・platformの4観測源から
最大2種の固定追加readを選んで比較する。`sre status TOKEN`は保存結果だけを照会する。
任意のprompt/namespace/tool引数・自動修復・resumeは受け付けない。

CLIのpassedは診断workflowの完了を意味する。観測healthのattention-requiredやunknownを
healthyへ変換せず、root_cause=undetermined、remediation_executed=falseを維持する。
evidence_tokenは保存先、workflow_tokenは内部監査identityである。原logs/manifestや
promptを出力せず、MCP返却span IDによる相関と保存model/root spanを区別する。
取消・保存状態・exit契約は[SRE CLI runbook](../../../tools/local_experiment/bears/SRE_CLI.md)を参照。

### Local experimental Pod policy check

`shirokuma policy check --pod-stdin`は16KiB以内の限定Pod JSON1件を検証し、
safe featureだけのlocal Qwen分類と、同じ候補へのnative server dry-run1回を照合する。
`policy status TOKEN`は保存証拠だけを読む。原候補・原API応答・モデル原文は保存しない。

CLIはworkflowのpassedとadmissionのallowed/deniedを分ける。checkは許可確認でexit0、
拒否確認でexit2、不明・失敗でexit1。statusのexit0は保存内容の読取成功である。
モデルは権限を付与せず、保存workloadは0。対象namespace・16policy/bindingの同一性、
LimitRange空、dry-run前後の不存在、pending intent、監査と4保存spanを検証する。
defaultingが違反特徴を変える入力は事前拒否し、不明な結果を自動再送しない。
詳細は[Policy CLI runbook](../../../tools/local_experiment/bears/POLICY_CLI.md)を参照。

### Local experimental query-triggered wake

`shirokuma warehouse query XS --session TOKEN`はquery-modeで起動した短命ownerへ、
停止済みXSのwakeと固定TPCH SELECT1回を同じ要求IDで依頼する。ownerは既存wake probesの後に
別caller queryを送信し、25行/結果hash/query ID/XS終端履歴/S不変/監査を確認して応答する。
旧`chill`/`wake`とQuery Doctorの`query run/status`は既存契約を維持する。

新query CLIは240秒待機する。切断はownerの処理を止めず、同じsession/request IDだけを
生存ownerと終了後15秒のgrace内で再照会できる。完了応答は同一内容を返し、追加POST/scaleは0。
任意SQL、owner再起動後の要求復旧、自動再送、tenant認可、cold-query性能を主張しない。
caller成功後の回収・最終監査は別途ownerのsession receiptで確認する。
[Runbook](../../../tools/local_experiment/warehouse_lifecycle/README.md#query-triggered-wake)を参照。
