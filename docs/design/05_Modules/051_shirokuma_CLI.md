---
project: Shirokuma
doc_id: "MOD-051"
title: "shirokuma CLI"
status: draft
created: 2026-07-05
updated: 2026-09-18
version: "0.16.0"
area: "module"
tags: [shirokuma, module]
---

# shirokuma CLI

### Select with one typed equality

`query select ... --where-column COLUMN --equals VALUE`で単一の型付き等値を指定できる。
`--snapshot-id ID`と併用可能で、未指定なら観測current headへ固定する。型はfresh schemaから取得し、
integer/bigint/decimal/date/boolean、非NULL・128 bytes以内のASCII値に限定する。
非投影の条件列もfull schemaへ束縛し、非表示cellを追加取得しない。
filter無しv1/v2は維持し、新receipt/resultはschema3、sourceは`filtered-snapshot-reader-v3`へ分離する。
literalはargv/shell history/private request/Trino SQLには残るが、公開predicate metadata・モデル・監査へは追加しない。
明示rowsは値を含み得る。診断は同snapshot/同条件のEXPLAIN/STATSだけを読み、元SELECTを再送しない。
保存reportはv3を別groupとして扱う。実filtered7件/旧v1 1件・限定拒否・歴史診断1回を確認済み、
同UUID cleanup・保持状態復帰・保存167検証まで完了し、local機能受入済み。
[Typed equality runbook](../../../tools/local_experiment/query_filter/README.md)を参照。

### Inspect saved Query Doctor runs

`query diagnose report [--token TOKEN]`で全診断または1件を同schemaのbounded JSONとして表示する。
既存診断commandを維持し、固定private summary/spansだけを読む。元Reader/tableの現存は不要、API/model/audit0。
command_resultと過去のrecorded_resultを分離し、失敗・active・不正を成功groupへ混ぜない。
既知usage小計と欠損数を分け、欠測costはNULL、v1/v2と保存model/providerを別groupにする。
新規evidence directoryや任意path/SQL入力は追加しない。9実CLIの保存読取を受入済み。
保存6件の4passed/2failed・3groupsを維持し、all/single6行とall再読取が一致した。
missingは固定row/exit1、他8回はexit0。入力6dirs/18files・source77不変、API/model/新診断/audit/runtime0。
[Saved diagnosis report runbook](../../../tools/local_experiment/query_diagnose_report/README.md)を参照。

### Read a retained snapshot

`query snapshots --table schema.name`で最大64個の保持snapshotを一覧化し、
`query select ... --snapshot-id ID`で明示選択した過去版を読む。
通常のReader v1は維持し、明示指定だけ別のschema v2 profile/保存先を使用する。
current headとselected snapshotを分け、同field IDs/name/type/nullability/orderのschemaだけ許容する。
前後のmetadata/UUID/schema/head/一覧が変わった場合や未掲載IDは拒否し、再送・現在版へのfallbackは行わない。
固定snapshot SELECTを所有query終了・接続回収・保存・child exitまで確認してから公開する。
整数/decimalの精度と100行上限を継承し、モデル・書込み・named refs・cloneは追加しない。
[Snapshot reader runbook](../../../tools/local_experiment/query_snapshots/README.md)を参照。

### Diagnose a completed Reader SELECT

Reader v1に加え、明示snapshot読取のv2 source adapterを実装し、local実診断と保持状態復帰を確認した。
現在のidentity Bと選択Aを分離し、前後の保持snapshot一覧を保存sourceと完全一致させる。
EXPLAINは元AのSELECT、STATSは全compatible schema列をAへ固定したsubquery（LIMITなし）を使う。
v1のstatement/journalは維持し、v2だけrowless snapshot catalogsを追加する。

`query diagnose --read-token TOKEN`は成功したReader selectの保存証跡から、
元SELECTを再送せず同SQLのEXPLAIN・元queryの限定history・table statisticsを読む。
UUID/schema/headの一致と接続回収後、名前・SQL・行値を除いた数値/欠測だけをQwenへ渡す。
履歴の不在はnot-retainedとし、元read tokenと新diagnosis tokenを区別する。
監査3event・Loki・保存span・最終receipt・child exitを確認してから固定所見と次の確認項目を返す。
root causeはundetermined、performance gainはunmeasuredを維持する。
旧`query run/status`を変更せず、診断のresume/任意SQL/自動書換えは追加しない。
[Diagnosis runbook](../../../tools/local_experiment/query_diagnose/README.md)を参照。

### Local experimental Lakehouse reads

`query tables --schema smoke|local_experiment`で一覧を確認し、
`query describe --table schema.name`で型とsnapshotを確認できる。
`query select --table schema.name --columns id,name --limit 1..100`は
発見済み列だけを観測snapshotへ固定して読む。これらはLLMを呼ばない。

整数・decimal・snapshot IDはJSONの文字列で精度を保持する。
limit+1行で省略を検出し、orderingは未指定と明示する。行は明示的なselect応答だけに含め、
既定receiptには保存しない。結果公開前にschema/metadataの再照合、所有queryの終了、
接続回収とchild exitを確認する。不正な式は実行前、未発見table/列は発見query後に拒否する。
上記の単一等値以外の任意SQL・一般条件式/join・書込み・tenant認可は追加しない。
[Reader runbook](../../../tools/local_experiment/query_read/README.md)を参照。

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

2026-09-18の小増分では、既存Pod readへ固定Trino 1containerのCPU平均nanocoresと
memory working-set bytesをoptionalで追加した。実CLI1回がpassed/exit0となり、初期・followupの
両方でobservedを確認した。独立保持監査73checksも成功し、この観測機能のlocal受入を完了した。
保持4003files中3999は完全一致し、監査2filesの限定追記とruntime2filesのmtime更新を別途確認した。
有効0と欠測NULLを区別し、名前・時刻による対応付けとsample windowを保存する。
cached sampleを新しい測定とは扱わず、数値だけで高負荷・原因・回復を断定しない。
旧usage欠落は過去のshapeで照会し、公開CLI schemaと自動修復なしの契約は維持する。

保存usageの表示には`sre status TOKEN --usage`を使う。既定status/runのschema1/13keysを維持し、
このflagの最終frameだけschema2/14keys、allocatedはschema1/4keysとする。runへのflag指定は拒否する。
`resource_usage`のinitial/followupは保存されたexact整数文字列・時刻・identityだけを表示し、
比較は同window/別window/未再観測の既存判定を用いる。現在の負荷・trend・原因とは解釈しない。
verified passedではfield有りがavailable、旧未観測がlegacy-absent。保存unknownはsample NULLのまま、
失敗・busy・未検証はunverifiedで全payload NULLとなる。最終frame上限は改行込み4096B。
同じlock/snapshot内の保存検証を再利用し、追加API/model/audit・新token・元証拠書換えは0。
保存CLI17回で全5件の既定結果を保持し、新usage2samples・旧legacy-absent/unverifiedを確認した。
入力32dir/file entries・2parent inventory・2locks・Gateway監査2files・source223の不変を確認し、
全local2150件（既存1skip）・独立review・保存受入68checksを通過した。
前段AH493は再照会せず継承する。

保存log観測は`sre status TOKEN --logs`で表示する。最終frameはschema3/14keys、
`log_signals`はsource/availability/initial/followup/comparisonの5fieldsで、通常schema1とusage schema2を保つ。
両flag trueはchild起動前に拒否し、最終frame上限4096Bを維持する。passed/saved-evidenceだけを公開し、
旧field欠落はlegacy-absent、それ以外の未検証状態はunverified・全payload NULLとする。
unknownのfragment NULLと有効なempty/0を区別する。comparisonは保存identityと要求窓overlapだけで、
header形の件数をevent数・ログ健全性・原因・回復へ拡大しない。同じlockと前後snapshotで再検証し、
追加API/model/audit・原文出力・証拠書換えは行わない。

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
