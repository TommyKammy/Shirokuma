---
project: Shirokuma
doc_id: DB-P6-4C2A
title: Read-only SRE and Policy observations for an owned Database context
status: accepted
created: 2026-11-01
updated: 2026-11-01
version: "1.0"
area: guides
tags: [database, bears, sre, policy, observations, authorization, acceptance]
---

# DB-P6-4C2A: 本人のDB contextを観測する

## 利用方法と対象

SRE BearとPolicy Gatekeeper Bearに、それぞれ独立したDatabase用contextを置く。role・Database・primary schemaを選んでcontextを明示更新し、「観測を取得」を実行する。warehouseはLOCALに固定し、SQLワークシートやCuratorの選択を自動転用しない。観測の自動取得・polling、modelへの送信、local storageへの要求保存、復旧・取消の操作は追加しない。

対象は現在認可された本人と、保存された9項目contextが完全に一致するDB内metadataである。V／E／DB admin／platform adminのいずれも現在のrole上限・DB grant・binding／generation・primary schemaのquery権限を満たす必要がある。SREでは本人のqueryとAIの保存状態、Policyではそのcontextの現在権限を表示する。現在のruntime health、全履歴や他actorの活動を示す画面にはしない。

SQL・LLM・catalog・Kubernetes・engine proof・policy file・blocker・telemetry・raw logを呼ばず、AI／query lifecycleの既存get・履歴・lease・照合helperも利用しない。読取り専用の観測であり、UNKNOWNや進行中要求を変更せず、他のSQL／Doctor／Copilot／Curator／WAPの入力・context・未解決要求を保持する。

## APIと認可

入口はWorkbenchとnativeの`GET /api/v1/bears/context-observations?context=<JSON>`である。contextパラメータは1件だけとし、追加・重複パラメータ、GET body、POST、任意manifest・path・source filter・cursorは受け付けない。

contextは`database_id`、`schema_id`、`warehouse_id`、`sql_catalog`、`sql_schema`、`selected_role`、`binding_revision`、`generation_id`、`grant_revision`の9項目だけである。選択roleは認証headerと一致し、revisionはboolを除く整数、grant revisionだけは明示的なnullも許す。欠落・余分なkey・重複key・不正なJSONや型は拒否する。XS／SをLOCALへ読み替えない。

既存auditのread-only snapshotを利用し、SQLiteを`mode=ro`・`query_only`・読取りtransactionで開く。現在のaccount・DB READY・role／grant・binding／generation・identity・primary schemaを確認し、schemaのquery権限も明示的に検査する。full9の再検査だけでviewer RAWを許可しない。返却直前にも新しいsnapshotで同じ認可を確認し、途中の失効やcontext変更から古い観測を返さない。

state directoryやSQLiteの作成・schema移行・chmodを伴う接続、runtime観測によるproof破棄などの経路は使用しない。必要なstore／schemaを読めない場合は固定の失敗応答とし、空のhealthy結果へ置き換えない。

## 応答と保存状態

応答の最上位は`{context,observed_at,coverage,sre,policy,platform}`だけとする。`observed_at`は取得時のUTC時刻である。`coverage`は`kind=latest_persisted_own_context`、`limit_per_source=20`、`full_history=false`、`live_runtime=false`を明示する。

`sre.queries`と`sre.ai`はそれぞれ`{items,has_more,omitted}`である。各itemは次の5項目だけを返す。

| 項目 | query | AI |
|---|---|---|
| `id` | 保存query ID | 保存AI ID |
| `kind` | `query` | `sql_explanation`／`sql_plan_explanation`／`saved_plan_explanation`／`sql_proposal`／`catalog_proposal` |
| `state` | QUEUED／RUNNING／FINISHED／FAILED／CANCELED／UNKNOWN | QUEUED／RUNNING／SUCCEEDED／FAILED／CANCELED／UNKNOWN |
| `created_at` | 保存された作成時刻 | 保存された作成時刻 |
| `updated_at` | null | 保存された更新時刻 |

queryのFINISHEDをSUCCEEDEDへ変換しない。IDは256文字以内の空でない表示可能文字列、時刻は64文字以内のT区切りでZまたは数値offsetを持つ保存ISO時刻とする。timezoneのない時刻や相対時刻を使わず、有効な保存文字列とoffsetを保持する。保存状態や時刻を現在の実行・停止の保証へ変換せず、取得時刻で保存時刻を上書きしない。

SQL・mode・結果・error／error code・backend ID・credential・worksheet・AI input／metadata／pins／model／provider／proposal／inference state・実行リンクは返さない。観測metadataが読めることを、元のquery結果やAI説明全体へのアクセス権として扱わない。

## 有限の候補窓と省略

queryは保存queryと保存実行contextのtyped JOINを使い、本人とfull9の全値を一致させる。grant revisionのnullも正確に一致させ、実行contextが欠けた記録を現在のcontextから補わない。

AIは外側のactorと、安全に読める保存full9 contextが一致する記録を対象にする。contextは8,192 UTF-8 bytes以内とし、容量・JSON妥当性を順に検査してからSQLのJSON式を使う。recordも262,144 bytes以内へ制限してから読み、外側のID・actor・contextとの一致を検査する。重複key・非有限値・深すぎるJSON・未知のkind／state・不正な時刻などを推測で補正しない。

安全な保存時刻等の候補条件を満たす記録を、保存作成時刻の`julianday`降順・rowid降順で21件まで選ぶ。表示検査は先頭20候補だけとし、21件目の存在を`has_more`で示す。これは古い候補があるという意味で、さらに表示できる正常な記録の存在を保証しない。

先頭20候補のうち投影できないものは省略し、`omitted`に0〜20の件数を返す。省略後に古い記録を探して補充せず、itemsと省略の合計は20以下とする。安全な時刻を取れない不正・過大なAI記録は候補窓に入る前に除外されるため、`omitted`は全不正履歴の件数ではない。診断payloadや元本文を省略理由として返さない。

## Policyとplatformの境界

Policyは`context_authorized=true`、現在の`schema_permissions={browse,query,write}`、正規の`sql_modes`、`statement_approval=false`、`shared_local=true`だけを返す。SQL modeは許可された`read-only`／`sandbox-write`／`admin-write`の上限であり、個別statement・対象・資源の検査や通常の実行認可は引き続き必要である。共有LOCALの案内は、現在の物理状態や全DBの稼働を観測した結果ではない。

`platform.allowed`は、認証principalと現在有効なaccountの双方がplatform adminで、最大role・現在role・選択roleもadminの場合だけtrueとなる。DB adminの選択だけでplatform権限を得ない。falseなら`PLATFORM_ADMIN_REQUIRED`、trueでも`PLATFORM_OBSERVATION_DEFERRED`を返す。Pを含む全roleでplatformの実情報を取得せず、platform scopeの選択肢も設けない。

## 画面と検証記録

各Bearの状態・contextは分離し、取得前／取得中／空／失敗／省略／古い候補ありを案内する。返却された閉じた応答形とfull9、取得上限、権限・mode・platform理由の整合性を検査してから表示する。認可の失効時は該当contextと観測を直ちに隠し、遅着成功や古い403が新しい状態を上書きしない。JA／ENと390px幅も確認する。

C2Aの限定範囲を技術受入済みとした（全体回帰4,073成功・34 skip、2DB・3実行ロールの実API、未付与権限の拒否、adminの実画面、最終保全PASS）。

| 検証層 | 確認する範囲 |
|---|---|
| 契約・独立レビュー | read-only authority、schema query権限、full9一致、5項目投影、有限窓・省略、全roleのplatform未観測のソースレビューを完了した。実API・復元の76項目と最終UIを含む21項目の独立照合もPASS。最終8ファイルを固定した。 |
| Backend／UI重点回帰 | Backend 335件（C2A新規24件）とNode 25のWorkbench UI 889件が成功した。Marts 3件を前後で、言語切替1件を別に確認した。途中失効・不正／過大な候補・省略と補充なし、grant付きnon-P adminとP accountの下位roleなどの認可境界は重点テストで確認した。件数を全体回帰へ合算しない。 |
| 全体回帰・文書 | 最終固定8ファイルでcanonical 97単位・4,107件を実行（4,073成功・34 skip）、終了コード0。重点回帰と全体回帰の件数を合算しない。文書のdesign-contextもPASSした。 |
| 実API | true P accountの選択adminでA／B、grant付きengineerでB、viewerでA、nativeのengineerでBの5ケースを各3回、warm／cold各15 GETで確認した。Aのadmin窓はquery／AI各20件でhas_more、Bのengineerでは既存UNKNOWNを保持した。拒否16件は、grantのないnon-P adminとP accountの下位roleを含む。これらの実機拒否をgrant付き実機成功の証拠とは扱わない。 |
| 実画面・読取専用性 | platform admin accountの選択adminで、SREはB RAW、PolicyはA MARTS、主worksheetはAを保った。4つのdraftは不変。Bear切替・主navigation・JA／EN切替で観測値と取得時刻を保持し、JA／ENの390pxでdocument幅390、最後に1920へ戻した。最終reload後の完全なserver logは明示GET 200が2件、POST／PUT／PATCH／DELETEが0件。各API・UI測定区間の全SQLite行・保護file・engine proofも不変だった。 |
| 保全・復元 | 実機後の照合は旧active 11,945行／default 156行・旧AI 75件・UNKNOWN／schema23、31 TABLE／33 VIEW head、設定とowner YAML 5ファイルのbytes／modeが不変。追加5行は明示CHILL／WAKEの各operation・requestと終了済みWake受付だけで、C2AによるAI／query等の追加はない。LOCAL 0/0から元の2/2、policy minutes 0／revision 5へ復元した。全体回帰後の最終照合もPASSし、doctorは46項目の確認で終了コード0・全体healthyとなった。 |

画面のavailabilityを扱う既存B1テストの古い文言を現在の機能範囲へ更新した。390pxではselectorとpanelの幅を制約し、navigationで完了済みの観測を消さず、取得中の遅着応答だけを無効にするよう修正した。元の観測時刻を保持し、再表示による自動GETや状態の再計算は追加していない。

最初の管理者CHILLは誤ったURLでNOT_FOUNDとなり、受付0件を確認した。元keyを保持して正しいruntime/chillへ明示送信し、管理操作の重複はなかった。これらのCHILL／WAKEと終了済みWake受付はcold検証・復元のための独立した管理者操作であり、C2Aの観測による起動やadmissionではない。

## 後続

[C1の限定技術受入](database-p6-4c1-catalog-proposals-2026-11-01.md)を維持し、P専用で別に認可した固定sourceのplatform観測、global workload／policy検査、モデルによる説明は別単位とする。旧SRE／Policyのrun／manifest・Computeの旧検査を開放しない。P6-4全体・P6-5・P6-7縦断検証・M1本人受入は継続する。
