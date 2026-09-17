---
project: Shirokuma
doc_id: "WP-L2-AGENT-002"
title: "WP-L2-AGENT-002 SRE Bear"
status: local-partial
created: 2026-07-05
updated: 2026-09-18
version: "0.8"
area: "workpackage"
tags: [shirokuma, workpackage, l2, agentops]
---

# WP-L2-AGENT-002 SRE Bear

## Summary

K8s Pod/Event・runtime状態の固定観測から実LLMが追加読取を選び、同一identityの変化を確認する。
containerの現在状態と過去終了履歴を分離する。logs/metricsによる原因診断・自動修復は未達。

## 2026-09-18: fixed Trino resource usage — local acceptance complete

既存`k8s_read_pods`へ、固定Trino 1Pod・application container `trino`のCPU平均nanocoresと
memory working-set bytesをoptional `resource_usage`として追加した。新usageを含む実CLI1回が
passed/exit0となり、初期・followupでobservedを確認した。独立保持監査73checksが成功し、
この観測機能のlocal受入を完了した。一般的な原因診断は未達で、WPはlocal-partialを維持する。

Pod一覧の固定labelから対象を一意に選び、pods呼出しごとにアプリ側kubectl取得操作を最大2回
（Metrics APIと同Podの再読取）追加し、アプリ側では再試行しない。各操作は8秒deadline、
所有childのcleanup猶予2秒、返却64KiB以内に制限する。`get_attempts`は取得intent数であり、
kubectl/client-go内部の429/Retry-After再試行を含むHTTP送信総数や、既存context確認の回数ではない。
Pod UID・container identity hash・restart/start時刻を前後照合し、Pod観測→request→取得終了と
sample windowを整数nsで比較する。Metrics APIの名前・時刻に基づく相関であり、
UIDで認証されたsample、独立した2実測点、CPU peak/RSS/持続負荷・OOM原因とは主張しない。

有効な値はexact int64文字列、明示0と欠測を分離する。unknownではsampleをNULLにし、
既存Pod readinessの件数を書き換えず新scopeのcoverageをunknownとする。
過去3/4源・container詳細なし・usage fieldなしは当時のshapeで再構成し、新観測を補わない。
同じtimestamp/windowの再読取はcached sampleとして扱い、trendや回復を判定しない。
Pod比較の`no-change-observed`は従来のPod/container状態の比較であり、usage値の同一性を意味しない。
`resource_usage.comparison=different-sample-window`は別windowとして分離し、数値差をtrendへ昇格しない。
追加finding・tool・remediationはなく、root_cause=undetermined、公開CLI schemaは不変。

- [x] pure evidence・保存projection・sample-window比較とprovider 8KiB上限を検証する。
- [x] 旧SRE84件を含む95 focused tests、新usage/provider11件の独立確認を通す。
- [x] 製品CLIを1回実行し、usage observedまたは固定reason付きunknownを原結果のまま保存する。
- [x] 保持runtime/data・元証跡の差分を限定し、追加監査とsource bindingを確認する。

実装・受入記録は`tmp/local-experiment/sre-usage-checks/8aa1e4d2d00c419e8c1d35ca8ab7752b/`。
一般的なlogs/metrics原因診断、修復案の品質、自動修復、本番受入の未達条件は保持する。

### 実CLIの観測結果

`sre-llm/52aa250a1b92b29d90205836a6ea0a3a/`のworkflowは
`0c461cecb373405ab2809ee5245045a1`、traceは`c6f208e7ec9493673b4306c59ff852f2`。
初期・followupともPod UID `117b813d-42aa-48d1-86db-56430424d624`、container identity hash、
restartCount 1、Running開始時刻`2026-09-17T00:51:07Z`が一致した。

| 観測 | sample timestamp (UTC) | window ns | CPU平均 nanocores | memory working-set bytes |
| --- | --- | ---: | ---: | ---: |
| initial | 2026-09-17T19:18:52Z | 15014000000 | 11121886 | 2162106368 |
| followup | 2026-09-17T19:19:07Z | 14990000000 | 11591194 | 2162118656 |

usage比較は`different-sample-window`。Pod/container状態の差分は0だが、usage値が不変とはしない。
過去の終了/restartに関する6warningからQwenはPod再読取1種を選び、assessmentは
attention-required、root_causeはundetermined、remediation_executedはfalseを維持した。
Qwen1callは入力1956/出力104tokens、6550ms、cost NULL。これは品質評価caseへの追加ではない。
producerはMCP5件とLLM/root各1の7監査eventを検証した。workflow保存span2件と
Gateway保存span5件を区別し、MCP返却span ID5件との相関を独立に確認した。

実行前23checks、保持4003files、AH486、14Pods/27resources/3Docker/16policies/54locksをbaselineとした。
source219の不変と、全local56suites/2139件（2138成功・既存optional1skip）、Go SRE16件、
CLI29件の成功を確認した。

### 最終保持監査

独立73checksは成功。保持4003filesのうち3999は保存fingerprint（SHA・mode・mtime・size・uid・nlink）が一致し、
Gatewayの2filesは旧prefixを保持して各5recordsだけ追記した。残るruntime2filesは内容・modeが同一で、
mtimeのみ更新した。全件fingerprint一致を要求した親afterの元結果は22/23成功として保持し、
この4差分の限定確認を独立結果へ記録した。
AHは旧486行のSHAを全て保持して493行となり、新7eventsだけが増えた。
同taskのAH/Loki各7件、local2/Gateway5の保存spans、MCP5件が対応する。
source219は不変、旧観測lock54件と新SRE lockはそれぞれinactiveだった。

新status2回はJSON全体が一致し、対象8entriesは不変。旧4件のbefore/after計8照会も
JSON一致・対象24entries不変を確認した。初回status helperのcommand名期待誤りと、
独立helperのmodel spanへのtask_id誤要求は、元の失敗記録を保存してhelperだけ訂正した。
製品失敗は0で、再検証のためのmodel再呼出しはない。独立相関確認のAH/Loki読取は各2回で、
保存cache取得後はsaved-onlyで検証した。

最終証拠は上記checkdirの`independent-verification.json`（SHA256
`fe588433bbe73546366cf72991dd11a3cf6a664f8a74cb84fbc39361e6869715`）と
`audit-readback-clarification.json`。CPU/memoryのtrend、原因・修復品質、本番受入はこの受入に含めない。

## 2026-09-17: product CLI plan

既存のQwen・MCPによる4観測源診断を`shirokuma sre run`へ接続し、保存済み結果を
`shirokuma sre status TOKEN`から照会できるようにする。新たな故障注入や修復は行わない。

- [x] local-experiment固定のGo入口とPython bridgeを追加する。
- [x] 1回のinvoke intent、所有childの取消、未確定結果の保持と再実行拒否を検証する。
- [x] 保存観測からtriage・比較・audit/span相関を検証し、旧保存結果もAPI/model/write0で照会する。
- [x] 製品CLIから実Qwenを1回実行し、保持runtime/data不変と監査読戻しを確認する。
- [x] local回帰と独立reviewを通し、関連ノートとlocal commitを更新する。

最大2種の固定readとUID比較の境界を維持する。現在のcritical・過去restart・unknownを
混同せず、assessmentは観測scopeの値として出す。原logs/manifest・prompt・例外本文を
CLI出力へ転記せず、root causeはundetermined、remediation_executedはfalseのまま保持する。

## Context

このWork PackageはLevel L2「Agent第一陣」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

## Scope

- 設計・実装・設定の最小単位を定義する。
- 関連するREADME/Runbook/ADRを更新する。
- CIで検証できる形にする。

## Non-scope

- Level外の大規模OSS追加。
- 本番データ利用。
- GxP対応。
- 人間承認が必要な危険操作の自動化。

## Deliverables

- 実装または設定ファイル
- ドキュメント更新
- テストまたは検証ログ
- 必要に応じてADR/Runbook

## Acceptance Criteria

- [ ] 変更がGitHub PRとして提出される。
- [ ] CIが通る。
- [ ] Policy checkが通る。
- [ ] `shirokuma doctor`または関連コマンドで状態確認できる。
- [ ] README/Obsidian noteが更新される。
- [ ] 未解決High riskがない。

## Suggested Labels

`level:l2`, `area:agentops`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L2-AGENT-002: SRE Bear.
Read AGENTS.md, related ADRs, and this Work Package.
Create a focused PR that implements only this scope.
Include tests, docs, risk notes, and rollback instructions.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-16 local functional evidence

MCP doctor/Pod/Eventを使う固定ルールの読み取り診断を実装。実機で3 tool callsと診断自身の4 eventがClickHouse/Lokiで相関一致した。意図的Flux停止と過去restartを区別し、現在の停止とは断定しない。11 focused tests成功。LLM診断・自動修復・PR提案は含まない。

[ADR-0034](../../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)によるローカル実験。旧公開gateは履歴として保持する。実行手順・失敗と修正・復元・未達事項は[Local Experiment Milestones](../061_Local_Experiment_Milestones.md)を参照。

## 2026-09-17 local Qwen triage and re-observation

既存Qwenを用いたLangGraph `collect → triage → followup`が実機で成功した。
MCP doctor/Pod/Eventを取得し、モデルが過去restartのある3 Podを根拠IDで選択した。
許可されたPod再読取だけを実行し、約4.7秒の間隔ではUID・状態・restart回数に変化なし。
`attention-required`を保持し、現在障害・原因・回復とは断定しない。
4 MCP calls + 1 inference + 1 assessmentの6件をClickHouse/Loki/OTelの相関で照合した。
モデル入力656・出力83 tokens、推論4.266秒、cost NULL。checkpoint完了も確認した。

モデルの選択は最大2種の固定読み取りに限定し、既知の根拠ID、許可tool、重大Pod所見の
再観測必須、観測health保持を検証する。任意引数・shell・root-cause主張は受理しない。
Pod同名でもUIDが変われば置換として扱い、未再観測、欠測、truncatedを明記する。
6合成ケース×2回のQwen評価も12/12成功し、全12推論の監査読戻しを確認した。
正常、過去restart、重大Pod異常、Warning、doctor degraded、Event不完全を含む。
固定射影receiptを保存する最終runでも6監査eventの検証が成功した。保存観測から
根拠と差分・hashを再計算して一致。SREの39 focused testsを含む245回帰/設計testsが成功した。

### Local functional acceptance

- [x] 実LLMが観測根拠に基づく読み取り確認候補を選び、実MCPで再観測する。
- [x] 不正な根拠・許可範囲外の提案・重大Pod確認の脱落を実行前に拒否する。
- [x] UID/時刻に基づく限定比較と、usage/latency/監査相関を検証する。
- [ ] logs/metricsを含む原因診断・修復案の品質を評価する。
- [ ] 自動修復を実装・実験で検証する。

旧PR gateはローカル実行条件にしない。再実行は`rtk make local-sre-qwen-smoke`、
合成評価は`rtk make local-sre-qwen-eval`。新しい常駐サービス・weights・workload変更なし。
既存固定ルールは`rtk make local-sre-smoke`で利用でき、監査記録は保持する。

## 2026-09-17 disposable Failed Pod drill

新規Pod一個を`exit 1`でFailedにし、Qwenがcritical根拠として選択・再観測する実機試験に成功。
初回/followupの同UID Failedを照合し、モデルの出力ではなく実観測に基づいて判定した。
入力735・出力90 tokens、推論3.398秒、6監査eventが相関一致した。
作成receiptのUID/resourceVersionを条件に削除し、残留drill Pod0、既存6 PodのUID・状態・
restart前後一致を確認した。既存サービスを故障させたり修復したりする試験ではない。

14 drill testsを含む計259回帰/設計testsが成功。再実行は`rtk make local-sre-qwen-drill`。
証跡・所有確認付きcleanup・未適用のlive admission境界は[Local Experiment Milestones](../061_Local_Experiment_Milestones.md)を参照。
logs/metricsによる原因診断と既存サービスの自動修復は未達として保持する。

## 2026-09-17 five runtime groups in Qwen evidence

Qwen workflowの初期観測に`local_platform_status`を加え、doctor/Pods/Eventsと合わせ4源とした。
5群のnot-readyをcomponentごとのcritical、unknownを警告として区別し、未観測は正常扱いしない。
critical Podとcritical runtimeが同時にある場合は最大2回のreadで両方を必ず再観測する。
Platformの差分はready/statusの変化だけを述べ、instance identityや復旧・原因は断定しない。

実機4源のSREは成功し、5群readyと既存Podの過去restartを区別してPod再観測を選んだ。
Qwen入力773・出力51 tokens、3.675秒、7監査event相関を確認した。
`tmp/local-experiment/sre-llm/0c69d3ef5ebc401ca3d27bdb4bd98ea7/`の安全な保存射影から
根拠・triage・比較・hashを再計算して一致した。8合成ケース×2回の16/16評価でも、
同時Pod/runtime障害と観測不能を含む読み取り選択・16監査のreadbackが成功。
評価証跡は`tmp/local-experiment/sre-evaluation/22615db4c5344a81a261a2086437113b/`。
評価には実service停止や故障注入を含まない。SRE tests64件成功。

Native admission適用後にも新規Failed Pod drillを再実行した。
4初期観測＋Pod/Eventの2followups、8監査event相関が成功。入力854・出力146 tokens、4.930秒。
作成UID `6f55c9f5-83e9-47a9-b69d-4bedfdadbb89`だけを削除し、既存6 Pod前後一致・残留0。
証跡は`tmp/local-experiment/sre-drill/4a856109b32f408185aebe247508c1b2/`。

## 2026-09-17 current and historical container evidence

Gatewayの型付き`container_observation`をhost側で検証し、現在の非0終了、報告されたOOMKilled、
image-pull待機、CrashLoopBackOffをcriticalとして分類する。過去の非0終了・OOM報告はwarningであり、
現在の障害に昇格させない。exit137だけではOOMとせず、未知reasonから原因を推測しない。
findingはPod名をsubjectとし、container名・current/last-termination・観測完全性・ready・restart・
固定stateを根拠として持つ。モデルの返却JSONはassessment/checks/root_causeのまま、
current criticalの全根拠IDを`k8s_read_pods`で再観測することをhostが要求する。

application containersだけを対象に256行まで検査・8行まで返し、raw message/log/secretは除外する。
unknown/truncatedは健康の根拠にせず、旧snapshotに詳細キーがなければnot-observedを明示し、
旧readiness評価との互換を維持する。保存射影から根拠を再構成できる。
比較は同Pod UID＋container名に限定し、state移動・last_termination変化・restart減少を記録する。
観測可能になったことやRunningへの変化を復旧と推定せず、root_causeはundeterminedのまま保持する。
入力8KiB・最大24findings・最大2種の追加readの制限を維持する。

### Actual model evaluation and disposable drill

既存8caseを保持し、current非0/OOM/image-pull/backoff、historical非0/OOM、unknown/truncatedの
8caseを追加した。実Qwen評価`c0de842e01824262bdc19298cfb9f0ac`は16case×2＝32/32成功・監査32件。
入力26368/出力2070tokens、推論91289ms、cost NULL。合成評価は実故障注入と区別する。

実drill `850681c5c93949f09cd449c4d6728c9a`（run `8efbf36cdca845af8caba3d5a3b26b5a`）では
新規Pod UID `ba22d3e2-6df7-464d-af62-4df1287a1e72`の`fail-once`がcurrent terminated / Error /
exit1 / ready=false / restarts0 / last_termination=nullと観測された。
開始・終了はともに2026-09-17 09:53:25 UTC。初回とfollowupで同UID・全固定値を確認し、
モデルはcriticalのf01/f02を選択、所有UIDを条件としたcleanupでPodを削除した。

workflow `workflow-c4455c5ca4664504899009781b79d8bb`、trace
`4706d338a360bb614ce3d2a51a122054`。入力1731/出力174tokens、推論6996ms。
4初期read＋2followups＋LLM＋assessmentの8件をAgentHouse/Lokiで照合した。
保存された実OTel spanは2件、MCP6件は返却span IDによる相関であり、保存span8件とは呼ばない。
評価とdrillの合計33callsは28099入力/2244出力tokens・98285ms、cost NULL。

SRE84 tests（新host17＋drill3件を含む）、Gateway関連94、report24が成功。
最初の全回帰998件で新Gatewayテストの配線漏れを検出し、14件をMakeへ追加した。
正式な最終全回帰は1012件（1011成功・optional OTel 1skip）。
この結果はlocal-partialの観測機能を進めたもので、一般的な原因診断・自動修復・本番受入は未達。

最終独立監査は`tmp/local-experiment/sre-container-preflight/0ac17cd4dad044d5838caccc68473445/verification.json`
の80checksが成功。AH388→428は旧388件の全hash保持＋新評価32/workflow8、Loki8件が完全一致した。
保存実span2＋MCP返却span ID相関6、同UID/current Error/exit1とf01/f02再観測を独立再計算して一致。
保持Kube/Docker/履歴4tables・5DDL/1151filesは不変、生成Podの不存在もfresh確認した。
fixture/metadataは前handoffのsealed証拠を継承しており、今回のfresh読取とはしない。
全評価reportの母集団はQwen152/152・mock10/10（別群）、全体162/162・11valid runs。
上記drillの1推論はgolden評価に含めない。

## 2026-09-17: SRE Bear product CLI

`shirokuma sre run`と`sre status TOKEN`を追加し、既存Qwen・MCPによる診断へ接続した。
初期4観測源と最大2種の固定followupを維持し、任意prompt/namespace/tool引数や修復は受け付けない。
GoはData transportの所有childへのSIGINT・最大240秒cleanup待機を再利用する。
厳密なallocated4/result13fieldsを照合し、finalとchild exitが整合してから結果を出す。
workflowのpassedと環境のassessmentを分離し、attention-requiredを正常へ変換しない。

`sre-cli/<token>`のintent/lock/hash sealと、既存`sre-llm/<token>`のworkflow証拠を分けた。
呼出しは1回限りで、自動再送/resume/SIGKILLを追加していない。statusはAPI/model/write0。
snapshotからevidence・triage・比較・hashを再計算し、MCP返却IDの相関と保存spanを区別する。
旧3源、container詳細導入前の4源は当時のscopeだけを検証し、未観測情報を作らない。
旧保存passedでも再構成に必要なobservationsが無ければunknown/journal-onlyとする。
statusによって旧ファイルへ新たなhash sealを書き足さない。

元取消の保持をmodel audit・最終保存・tracer shutdownまで補強した。
MCP Clientの終了処理がOSErrorを上げる場合や取消を抑制する場合も、所有contextの終了を通った後に
同じ取消objectを返す。通常cleanup失敗は握り潰さない。gateway stderrは作成時から0600とした。
保存statusやCLI出力は原logs/manifest/prompt/応答/例外本文を転記しない。

実CLI run `sre-llm/07ca3b1a93988ec2a92e53bf69639ca5/` はpassed。
内部workflow token `bb3f4eee7c61490191dc0f76c6e694d4`、trace
`37fd761746c93059112c86bc56e1d0fe`。Qwen1callは入力1458/出力104tokens、5788ms、cost NULL。
初期doctor/Pods/Events/platformの4readから、3Podの過去非0終了とrestartの6warningを拾い、
f01–f06に対するPod再読取1回を選択した。約6.828秒間で同UID/state/restartに変化は無かった。
現在の障害・原因・回復とは断定せず、assessment=attention-required、root_cause=undetermined、
remediation_executed=falseを維持した。他の3観測源はnot-reobservedのまま記録した。
MCP5calls＋LLM/root各1の計7監査eventをAgentHouse/Lokiで照合した。
保存された実OTel spanはmodel/rootの2件であり、MCP返却span ID相関5件とは区別する。

旧run `0c69d3ef5ebc401ca3d27bdb4bd98ea7`と新runを各3回、計6回CLI statusで照会した。
旧内部token `f4c7620dbba149bab92b0a8df95b78ca`は当時のattention-requiredを保ち、
対象26files/directoriesのhash/mode/mtimeが不変だった。
新Python26testsと既存SRE84tests、実run＋fake外部＋actual OTelの結合が成功。
Goは78top-level tests＋52subtests（SRE16を含む）とvet/race、
actual Python main/fake workflowの成功・失敗・不存在・取消4経路が成功した。
全local回帰48suites/1580件（1579成功・既存optional OTel1skip）が成功した。

build/source hash・全log・status前後比較は
`tmp/local-experiment/sre-cli-checks/b851301006164c69ba8ca15587d2faba/`へ保存。
独立監査は `sre-cli-preflight/912433d9483f46c991a362f5fa39a461/`。
実行前は直前Query after/finalをhash参照で継承し、fresh14Pods・Docker3・context/node/VM・
AH446件・MLflowの所有identity/healthy/store/indexを確認した。新Query証拠を加え3365filesを保全した。
deepなDB/Metadata/fixture検証は直前Query sealの継承であり、SRE前に再度fresh全走査したとはしない。

これはbounded観測CLIのlocal-partialであり、一般的なroot cause診断・自動修復・本番受入は未達。
次は利用者がstdinで渡す限定Pod候補を、Qwen分類とnative server dry-runへ接続する。

最終独立監査 `sre-cli-preflight/912433d9483f46c991a362f5fa39a461/independent-verification.json`
は63checks成功。AH446→453の増分7件だけで旧446件の全行hashは不変、Loki7件も一致した。
workflow保存span2件に加え、共有Gateway exporterの保存span5件を別ファイルとして照合した。
返却ID・保存場所・parent edgeの証拠を混同せず、完全な親子trace graphは主張しない。
14Pods/Docker3/MLflow等の軽量fresh identityは不変で、deep dataは直前Query sealを継承した。
保全3365filesのうち3361は完全一致。Gatewayのevent/span JSONL2件は旧bytes prefixを保って
今回MCP5件分だけを追記した。readiness処理によるLoki runtime.json/Metadata runtime.lockの
2件は内容・mode同一でmtimeだけ更新された。strict比較falseの原after.jsonも保持している。
CLI status自体の6回/26files・directories不変とは別に、これらrun中の更新を記録した。
監査tunnel終了、design83docs/14tests・secret scan・diff check成功。
SRE WP/MilestonesをVaultへ同期し、MOD-051の既存言語差を維持して追記した。
