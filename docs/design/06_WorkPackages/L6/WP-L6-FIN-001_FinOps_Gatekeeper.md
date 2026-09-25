---
project: Shirokuma
doc_id: "WP-L6-FIN-001"
title: "FinOps Gatekeeper with inference and disk watermark cost"
status: local-partial
created: 2026-07-05
updated: 2026-09-20
version: "0.5"
area: "workpackage"
tags: [shirokuma, wp, l6, finops]
---

# FinOps Gatekeeper with inference and disk watermark cost

## Saved-report history（2026-09-26・限定offline受入）

明示選択した保存public report 2–8件（v1/v2）だけを静的HTMLへ時刻順に表示する。旧v1のlocal process欠落を停止/0と誤認せず、v2の停止・unknown・observedを区別する。identityとsample時刻、host/Colimaの別diskと当時の助言、各24時間宣言event windowを保持し、異なる区間の使用量・費用・event数を合算/差分化しない。選択原本と表示に秘密のpathを埋め込まず、入力不正ならHTMLを出さず固定エラーで終了。自動収集・継続dashboard・idle/chill判断は未達。

Colima停止時のread-only CLIはpartialで、所有3群はSTOPPED/null、Kubernetes・Colima disk・宣言eventは未取得、host diskのみobserved。保存v1/稼働時v2/停止時v2の3レポートから、原本不変・決定的なHTMLをoffline生成した。focused FinOps 72tests、全回帰121suites/3687実行＝3686PASS＋既存1skip。ブラウザーでの見た目は未確認。操作・容量・証拠は[runbook](../../../../tools/local_experiment/finops/README.md)と[Milestones](../061_Local_Experiment_Milestones.md)を参照。WPはlocal-partialのまま。

## Local owned-process observation（2026-09-20・限定受入完了）

新規収集をv2へ拡張し、固定3群の `local_processes` を追加する。対象はMLflow、Notebook Labのserverと所有kernel、固定model-serving。既存のKubernetes 3components・disk・宣言model eventと、保存済みv1 JSON/HTMLの契約は維持する。収集は既存Lab専用環境の固定read-only workerに委譲し、前後のPID/start identityが一致する所有processだけを集計する。

1秒のCPU累積時間差とRSS合計を観測する。短いCPU sampleは量子化の影響があり、RSSは共有ページを重複計上し得るため、Kubernetes working-setや物理一意メモリとは同一視しない。停止状態を確定できた群はidentity/sampleをnullとして「停止中」と表示し、0へ置き換えない。他の取得が成功していれば全体completeにできる。所有関係や計測が不明なら理由を保持してpartialとする。

2026-09-20T14:38:57.093Zの実01は4.931秒でcompleteとなり、Kubernetes3件とローカル3群がすべてobservedだった。MLflowは2process/487.40625MiB RSS、Labはserver＋13kernelの14process/1354.859375MiB、Servingは1process/32.375MiB。約1.0314595秒のCPU増分は3群とも0msで、量子化を含む観測値として保持し、idleとは判定しない。

1134保護files、ML24runs/1393points/24artifacts、Registry1/1、AgentHouse585行、各runtime identityは当時の前後で不変。保存v1 reportの再検証・HTML生成も互換を保持した。新HTMLの構造・数値・外部resourceなしを検証したが、Browserのfile URL policyで画面表示は未確認であり迂回していない。全回帰は121suites・3682実行＝3681PASS＋既存1skip（93.749秒）。保存public JSON＋HTMLは2files/33,232 logical bytes／36,864 allocated bytes（0600）。検証証拠は別、物理一意容量ではない。2026-09-25のread-only確認では当時のserver PIDは見つからず、現在の稼働を主張しない。

新依存・サービス・start/stop・model実行・MLflow/AgentHouse書込は追加しない。今回の限定3群を超える全component/PVC帰属、idle判断・費用/電力換算、budget強制、継続dashboardは未達のままとする。操作は[FinOps runbook](../../../../tools/local_experiment/finops/README.md)、実測は[Milestones](../061_Local_Experiment_Milestones.md)へ集約する。以下は先行v1の観測時点と受入結果を保持する。

## Local read-only increment（2026-09-20・限定受入完了）

`rtk proxy make local-finops-report`で、固定Trino/Polaris/SeaweedFSのCPU平均・memory working setと観測window/identity、host workspace filesystemと専用Colima filesystemの空き容量、AgentHouseの固定24時間モデル宣言eventをJSONで読む。修正後の実02は0.931秒で`complete`/exit0となった。
[実装runbook](../../../../tools/local_experiment/finops/README.md)の範囲で、新service/model呼出し/AgentHouse書込・stop/delete・budget overrideは行わない。読取失敗は理由とnullを保持する`partial`/exit1、全取得成功は`complete`/exit0であり、FinOps全体の完成を意味しない。

### Observation contract

- CPUはsample windowの平均、memoryはworking set。componentはprimary containerだけを対象とし、identity/start timeとwindowを照合する。欠測・不整合は0に置換しない。
- hostとColimaの空き容量は分け、25%/20%/15%以下をwarning/low/criticalとして固定助言を返す。共有physical storageなので合算せず、APFS reclaimable量や削除可能量を推定しない。
- model/provider別の宣言eventにはsynthetic実験記録も含む。event数を実modelcall数と同一視せず、tokens/costの既知小計と全件coverageを区別する。未知total/currencyはnullのまま、costはproducer-defined estimate、event durationは推論latencyそのものではないproxyとする。

### Local observation

2026-09-20T08:57:46.723Zの実02で3componentsがobserved、host空き61.670289%・Colima81.9312%はいずれもnormal。24時間の宣言model eventは10件/1groupで、prompt4000・completion178 tokens、event duration14288msを記録した。これらは実験記録を含むevent集計であり、実呼出し数や純推論latencyの保証ではない。cost/CPU/memory proxyの全件値とcurrencyはnullを維持した。
初回はbootstrap podがPolaris対象へ混在し、`TARGET_AMBIGUOUS`としてpartialとなった。元report/logを保持し、固定対象の選択を修正して別実02を行った。詳細・最終検証は[Milestones](../061_Local_Experiment_Milestones.md)を参照する。

### Cleanup correction and saved HTML（2026-09-20）

補助processで親rtkが先に終了し子がstdoutを保持する場合のtimeout cleanupを修正した。修正後transport26testsがPASS（0.080秒）、実03も0.995秒でcomplete。2026-09-20T09:06:21.343Zに3components observed、host空き61.669668%/Colima81.929112%がnormal、宣言10eventsとcost/proxyのunknownを維持した。実02とその時点の回帰結果は別記録として残す。
`rtk proxy make local-finops-html < report.json > report.html`は、保存JSONの公開契約を検証して静的HTMLへ表示する入口。追加収集を行わず、観測時刻・partial・欠測を保つ。保存先は`umask 077`のprivate directoryとする。保存した実02の通常HTMLと初回partialのHTMLをdesktop/390px幅で表示し、3components/2disks/10events・duration/cost unknown・Polaris TARGET_AMBIGUOUSを確認した。tableのkeyboard横scrollも動作し、追加した一時preview serverは終了した。修正後の最終回帰は3340PASS＋既存1skip、既存44files/MLflow/AgentHouse/runtimeも元baselineと一致した。

### Remaining acceptance

固定3componentsを超える全component/PVC別帰属、idle判断とchill実行、価格/電力換算、budget強制・override、履歴/自動更新/task別などの継続dashboardとPRあたりの費用は未実装。静的reportの表示とは区別する。観測レポートの成功から本番FinOpsや費用削減効果を主張しない。
[ADR-0034](../../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)下の承認済みlocal開発では、旧Issue/PR gateは待機条件にしない。以下の元計画は歴史として保持する。

## 2026-07-05 historical plan

| Field | Value |
|---|---|
| WP ID | WP-L6-FIN-001 |
| Level | L6 |
| Workstream | FinOps |
| Depends-on | WP-L2-OBS-001, WP-L3-CH-001 |

## Scope

Monitor and enforce resource budgets for CPU, memory, Kubernetes PVCs, host SSD free-space watermark, query cost, idle services, cloud token spend, and local inference cost proxies.

## Acceptance Criteria

- Captures CPU/memory/storage usage by component.
- Captures host free-space watermark from macOS/Colima context and emits alerts at 25%/20%/15% free-space thresholds.
- Captures prompt/completion tokens and estimated cost by model/provider.
- Captures local inference latency and resource proxy where available.
- Provides `chill` recommendations.
- Can block or require approval for PRs that raise resource, disk, model-cache, benchmark-data, or token budgets.
- Dashboard shows task inference cost and token cost per accepted PR.

## Notes

FinOps is rule-based; do not let an LLM decide budget override alone.

## Definition of Done

- Work is represented as a GitHub Issue linked to `WP-L6-FIN-001`.
- Changes are committed through PR.
- Required CI and policy checks pass.
- Documentation and WBS are updated.
- Any deviation from ARM64-native execution is recorded in [[10_Research/106_ARM64_Container_Image_Compatibility]].
