---
project: Shirokuma
doc_id: DB-P6-4C2B
title: Fixed read-only LOCAL platform observations for Platform administrators
status: accepted
created: 2026-11-01
updated: 2026-11-01
version: "1.0"
area: guides
tags: [database, bears, sre, policy, platform, observation, authorization]
---

# DB-P6-4C2B: P専用の固定LOCAL観測

SRE／Policyから、Platform管理者（P）が固定LOCALの限定情報を明示取得する機能を実装した。[C2AのDB内観測](database-p6-4c2a-context-observations-2026-11-01.md)とは独立し、DB contextやDB grantを利用条件にしない。C2Bの限定範囲を技術受入済みとした（全体回帰4,109成功・34 skip、両HTTP面の実APIと権限拒否、Pの実画面、復元と最終保全PASS）。

## 利用条件と操作

`GET /api/v1/bears/platform-observations`を8888／8080の両面に追加する。query parameterとbodyは受け取らず、選択roleは既存の認証済みheaderを使用する。対象Warehouseは常に`LOCAL`とし、任意のpath・Deployment・manifest・commandを指定できない。

取得前に`audit_service._authority(None, principal, role, 'platform', None)`で、現在の有効accountとprincipal双方のP権限、上限admin、選択adminを検査する。全source取得後にも同じ認可を新しく読み、fingerprintを比較する。権限喪失や不一致では取得内容全体を破棄し、固定`PLATFORM_ACCESS_DENIED`／403とする。DB adminやPの下位role選択はsourceを読む前に拒否する。PでDB grantがない場合も、この認可を満たせば利用対象になる。認可のためにSQLite snapshotを開かず、保存admission sourceの障害は独立した観測不可として扱う。

SRE／PolicyそれぞれにDatabase／Platformの明示scope選択を用意し、既定はDatabaseとする。PlatformはDB選択やC2Aの能力flagを流用せず、現在のP権限とglobal roleで判断する。取得はボタン操作だけとし、scope・tab・言語・navigationの切替で自動取得しない。

## 固定sourceと表示の意味

| source | 公開する値 | 意味と制約 |
|---|---|---|
| `physical` | coordinator／workerのdesired・ready、合計、限定state | 固定2 Deploymentのdesired／ready要約。正のdesiredとreadyの一致かつ現在generationの確認を`active`、両合計0を`chilled`、それ以外を`transitioning`とする。これは完全rollout・完全停止・資源解放・現在のSQL healthの証明ではない。 |
| `engine_evidence` | 保存された`verified_at`、固定`live_probe:false` | 固定fileを4 KiB以内で読み、現在設定のgeneration／revisionと一致する過去の証拠日時だけを示す。現在のengine検証やSQL実行を行わない。 |
| `auto_chill` | 保存minutes（0〜1440）／revision | 固定fileを4 KiB以内で読み、明示保存された値だけを示す。file不在や不正データを0分設定へ置き換えない。 |
| `saved_admissions` | LOCALのACTIVE／UNKNOWN件数 | 読取り専用SQLiteで保存件数だけを集計する。`query`かつ`FENCED_READ_ONLY`は既存条件に従い除外する。全blocker・稼働中query・安全に休止できることの証明ではない。 |

`physical`は読取り専用snapshotから検証した適用済みfoundationを使い、固定2 Deploymentの名前・namespace・desired・ready・generation・observedGenerationだけを1回の固定kubectl GETで取得する。request timeoutを3秒以内、process全体を8秒以内、stdoutを4 KiB以内に制限し、stderrを返さない。公開値に内部の名前・namespace・path・UID・label・manifest・stdout／stderrを含めない。欠損・重複・不正な件数は固定`PHYSICAL_UNAVAILABLE`とする。

トップレベルは`scope`、`warehouse`、`selected_role`、UTCの`observed_at`、`coverage`、4 sourceだけとする。coverageは固定観測であることと、live engine probe・policy audit・完全blocker集合を含まないことを示す。各sourceにavailability・固定code・UTC観測時刻を持たせ、不在・不正・timeoutでは値をすべてnullにする（`live_probe:false`は固定）。認可済みの場合、source単位の障害は他のsourceを残したHTTP200として表示する。

JSONは重複key・非有限値を拒否し、byte数・depthを制限する。時刻は実在するcalendarとtimezoneを検証し、64文字以内とする。小数秒は全体の長さ上限内で1桁以上を認める。boolを整数として受け付けず、件数とrevisionは安全な整数だけを公開する。

## 読取りと画面の境界

観測はstore・file・engine proofへ書き込まない。書込み可能な`control_store.connect`／`root`、旧runtime status、proof無効化、operation照合・受付、Wake／Chill、SQL／Trino／ClickHouse、LLM・provider、raw log・telemetryを呼ばない。任意manifestや旧SRE check／Policy auditの入口を開放しない。

Platformの完了snapshotは同じ認可のscope・tab・言語・navigationやDBだけの変更で保持する。scopeを離れた進行中応答は無効化し、現在のP／session／header role喪失では非表示中の保護snapshotも消去する。古い401／403が新しい正常snapshotを消したり、遅着応答が以前の権限を復活させたりしない。C2Aの各DB snapshot、SQL／Copilot／Doctor／WAPのdraftと未解決UNKNOWN受付を維持する。

C2AのAPI応答と`PLATFORM_OBSERVATION_DEFERRED`等の既存codeは変更せず、引き続き全roleでplatform sourceを読まない。DB表示の説明は「DB snapshotには含めず、別のP専用scopeで扱う」とし、全機能が利用不可であるという表示にしない。JA／ENと390pxの画面を検証する。

## 検証と技術受入の層

| 層 | 必要な証拠 | 現在の状態 |
|---|---|---|
| 重点backend／UI | DB grantなしP、非P・下位role・途中失効、厳密DTO、source障害・timeout、遅着とcache保全、旧機能の回帰 | backend新規20件と既存103件、計123件がhostでPASS。最終UI185件とMarts3件、計188件もPASS。最終固定7ファイルの一致を確認し、重点回帰と全体回帰は分けて記録する。 |
| 実API | 8888／8080で真Pの固定4 sourceと拒否を確認し、C2Aがplatform sourceを読まないことを別に確認する | DB contextなしのP GETはwarm 6回・cold 6回PASS。warmは4 sourceがavailable、coldはdesired／ready 0/0と過去proofのunavailableを確認。非P・下位role・framingの16拒否、C2Aの2DB・3実行role等の5 GET回帰もPASS。 |
| 実画面 | 明示scopeと取得、観測時刻と証拠の限定、切替・失効・JA／EN・390px、DB snapshotと4 draftの保全 | coldは明示P GET 1回・C2A GET 2回、warmは明示P GET 1回で、両区間ともmutation request 0・全ledger／file／proof／policyが不変。viewer参照のSREとadmin参照のPolicyのDB snapshotを保持し、DB参照roleと独立したP取得、下位header roleでのcache消去・取得禁止、復帰後の自動取得なしを確認。JA／ENのscope・tab・navigation切替と4 draftを保全し、390pxは両言語とも横幅390、確認後1920へ戻した。 |
| 無変更と復元 | store全既存行・UNKNOWN・catalog head・設定とowner YAMLのbytes／modeを照合する。試験用の明示operator操作はGETから分けて記録する | 各GET区間は全table・proof・policy・保護fileが不変。実機後の保全は旧active 11,950行／default 156行・旧AI 75件・UNKNOWN／schema23、31 TABLE／33 VIEW headと保護5ファイルのbytes／modeが一致しPASS。追加5行は明示CHILL／WAKEのoperation各1行・request各1行とFINISHEDのWake受付1行だけで、GETによるAI／query等の追加はない。既存operatorの強い状態確認でLOCAL 2/2・policy 0分／revision 5への復元を別に確認した。復元後のdoctorは終了コード0・46項目で全体healthy（45 healthy・SQL診断1項目はinformational）。これをSQL実行検証の証拠にはしない。全体回帰後の最終保全もPASSし、既存行・UNKNOWN・head・保護fileと追加5行の分類を再確認した。 |
| 全体回帰・文書 | canonicalの実終了コード・成功／skip件数、design-context、local linkと能力IDの保全 | 文書の198 local link・89能力ID順序・CMP-05と過去実績の保全を確認。最終固定7ファイルでcanonical 98単位・4,143件を実行（4,109成功・34 skip）、終了コード0・失敗0。最終design-contextもPASSした。 |

認証refreshの既知401／403ではJSON解釈前に保護cacheを消去し、正常bodyの遅着も現在のtokenと認可を再確認するように修正した。現在・非表示中の拒否と古い拒否／bodyが新しいsnapshotを壊さない回帰を含めて確認した。

実APIのP accountには2件の明示DB grantがあるため、実機結果は「DB contextなし」の証拠とする。「DB grantなしでも認可できる」条件は、DB／grantを持たない重点テストの証拠として区別する。

既存の[C2A受入実績](database-p6-4c2a-context-observations-2026-11-01.md)を維持する。global workload／policy検査、raw telemetryやモデルによる説明、変更操作は後続の独立単位とし、P6-4全体／P6-5／P6-7縦断検証／M1本人受入は継続する。

## 後続: Compute画面への限定読取り統合

[P6-5A](database-p6-5a-compute-observations-2026-11-01.md)で、Compute画面へのP専用固定LOCAL観測の読取り統合を実装した。受入済みC2Bの明示GET・strict DTO・global P cacheを再利用し、新しいAPIやDB context／grantの前提を追加しない。現在の真P・上限admin・選択adminを条件とし、完了snapshotと観測時刻をSRE／Policy／Computeで共有する。画面を開くだけでplatform観測を取得せず、既存Warehouse操作へも接続しない。desired／ready要約・過去のengine証拠・保存AutoChill設定・限定admission件数を現在healthや完全なPolicy監査へ読み替えない。P6-5Aの限定範囲を技術受入済みとした（全体回帰4,122成功・34 skip、両HTTP面の実APIと権限拒否、Computeの実画面、最終保全PASS）。本ガイドのC2B受入範囲と実績を維持し、C2Cのbaseline、旧SRE check／Policy baseline・global auditは独立後続とする。P6-4全体／P6-5全体／P6-7／M1本人受入は継続する。


## 後続: C2C1の限定宣言レビュー（限定技術受入）

[DB-P6-4C2C1](database-p6-4c2c1-workload-baseline-2026-11-02.md)は固定LOCALの2 Deployment Pod-templateを、旧4項目の宣言意図だけで手動確認する限定技術受入単位である。C2C1の限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,232成功・34 skip、両HTTP面の実APIとWorkbenchの3面表示、最終保全PASS）。現在の真P・上限admin・選択adminを取得前後で検査し、DB／grantを前提にしない。SRE／Policy PlatformとComputeの3面で独立したsnapshotを共有し、本ガイドのC2B 4source DTO・cache・requestと受入結果は変更しない。

nonRoot=trueかつUID未指定は旧宣言ruleどおり一致を許し、宣言欠落・不一致をnot_met、不正／部分sourceを全体確認不可にする。8 admission policyやglobal policy全体の検証、aggregate PASS／allow、実行・admission許可ではない。旧任意manifest・SRE check／Policy baseline routeは引き続き閉じ、C2C全体・P6-4全体／P6-5全体／P6-7／M1本人受入は未完了を維持する。
