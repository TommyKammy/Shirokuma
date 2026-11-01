---
project: Shirokuma
doc_id: DB-P6-5A
title: Read-only fixed platform observations in Compute
status: accepted
created: 2026-11-01
updated: 2026-11-01
version: "1.0"
area: guides
tags: [database, compute, governance, platform, observation, authorization]
---

# DB-P6-5A: Compute画面のP専用固定観測

受入済みの[C2B固定LOCAL観測](database-p6-4c2b-platform-observations-2026-11-01.md)を、Compute画面の読取り専用sectionへ接続した。既存のSRE／Policyと同じ明示GET・strict DTO・global P cacheを再利用する限定統合である。P6-5Aの限定範囲を技術受入済みとした（全体回帰4,122成功・34 skip、両HTTP面の実APIと権限拒否、Computeの実画面、最終保全PASS）。

## 利用条件と取得

既存`GET /api/v1/bears/platform-observations`とbackendの認可・source取得・返却契約を変更しない。利用条件は現在のaccountとprincipal双方のP権限、上限admin、選択adminであり、DB選択・context・grantを前提にしない。DB adminやPの下位role選択では保護情報を表示せず、platform GETも行わない。実機のP accountにDB grantがある場合、「DB contextなし」の実機証拠と「grantなし」の重点テストを区別する。

ComputeにP専用の固定LOCAL観測sectionを置き、権限の明示更新と観測の明示取得を案内する。画面表示・tab・言語・navigation切替ではplatform GETを自動実行しない。既存Warehouse状態の更新やWake／Chill・AutoChill設定等の操作は従来の契約のまま維持し、新sectionから実行しない。

## 表示と共通cache

| source | 表示する内容 | 表示から推定しないこと |
|---|---|---|
| Deployment | coordinator／workerのdesired／ready要約と観測時刻 | 全Pod停止・資源解放・完全rollout・現在のSQL health |
| 保存engine証拠 | 過去の検証日時と取得時刻 | live engine probeや現在のSQL成功保証 |
| 保存AutoChill設定 | minutes／revisionと観測時刻。0分は無効と表示 | 設定変更の完了や休止の実行 |
| 保存admission | 限定したACTIVE／UNKNOWN件数と観測時刻 | 完全なblocker集合・安全な休止・security audit |

既存のstrict DTOだけを描画し、sourceのunavailable・固定code・null値を正常値や0へ置き換えない。raw manifest・log・SQL・環境変数・資格情報・内部pathを追加しない。

SRE／Policy／Computeは同じglobal P snapshotと観測時刻を共有する。DBだけの選択やrevision変更はP cacheの利用条件にせず、同じ認可でのtab・言語・navigation切替は完了snapshotを保持する。離脱時は進行中のtokenを無効化し、認可／session／header role喪失では非表示中も含めて保護cacheを消去する。古い応答や401／403が新しいsnapshotを壊さず、以前の権限を復活させない。

同じ認可での観測操作では、C2AのSRE／Policy各DB snapshot、SQL／Copilot／Doctor／WAPの4 draft、既存runtime editorのdirty／base／conflict／receiptを保全する。真の401／403や権限喪失では、既存の`dbRuntimePurge`が保護されたruntime editor／intentの表示を消去する。この表示が残ることを保全条件にせず、serverに保存された元key・UNKNOWN等の既存行の不変と再送なしを別に確認する。JA／EN、390px、通常desktop、role表示と操作可能条件の一致を確認する。

## 旧検査と全体gate

CMP-05への接続は、受入済みC2Bの固定P観測をComputeへ表示する範囲だけである。旧SRE check、任意manifestのPolicy check、workload baseline、全面的なglobal policy auditは開放しない。C2Cのbaselineは独立した後続単位とし、本統合でP6-4全体の閉鎖を宣言しない。

CMP-01〜04の既存Warehouse状態・操作・設定の契約と、CMP-06の実サイズ変更がP7／8である境界を維持する。[全機能計画](database-full-capability-and-compute-plan-2026-10-28.md)の対応API受入後に読取り統合を進める方針に従い、P6-4全体／P6-5全体／P6-7縦断検証／M1本人受入は継続する。

## 検証と技術受入の層

| 層 | 必要な確認 | 状態 |
|---|---|---|
| 重点UIと既存機能 | 共有cache・厳密DTO・真P／下位role・失効・遅着・DB独立性、既存Compute操作とBearsの回帰 | 重点host Node25でUI 197件とMarts 3件、計200件がPASS。全Workbench前段918件とallowlist統合修正後の重点139件も別にPASS。最終固定4 UI／testファイルと、変更しないAPI／lifecycle等6ファイルがmanifestと一致。既存の認可・DTO・backend APIは変更せず、各回帰の件数は合算しない。 |
| 実APIと実画面 | 既存endpointだけを使い、Computeでの明示取得・観測時刻・限定意味、SRE／Policyとのcache共有、JA／EN・390pxを確認 | 両HTTP面のP GET 2回と非P／下位roleの403拒否10件がPASS。実画面では手動P GET 2回・C2A GET 2回、mutation request 0。従来のruntime状態更新／polling GET 46回を新しい観測取得と分けて確認した。3画面で同じ観測時刻とcacheを保持し、JA／EN・390pxの外側／内側ともoverflowなし、最後に1920へ戻した。 |
| 無変更と保全 | GETだけでoperation・SQL・LLM・file／store／proofの変更がなく、DB snapshot・4 draft・UNKNOWN・owner YAMLを保全 | 実機後の保全PASS。旧active 11,955行／default 156行・AI 75件・UNKNOWN／schema23、31 TABLE／33 VIEW headと保護7ファイルのbytes／modeが一致し、新規行0。lifecycle操作を行わずLOCAL 2/2を維持した。最終doctorは終了コード0・46項目で全体healthy（45 healthy・SQL 1項目はinformational）で、SQL probeの証拠にはしない。全体回帰後の最終保全もPASSし、全既存行・UNKNOWN・head・保護fileの不変と新規行0を再確認した。 |
| 全体回帰と文書 | 最終sourceでcanonicalの終了コード・件数を確認し、89能力ID順序・過去実績・local link・design-contextを照合 | 最終固定4ファイルでcanonicalを再実行し、98単位・4,156件（4,122成功・34 skip）、終了コード0・失敗0・error 0。文書のlocal link・89能力ID順序と過去実績を照合し、最終design-contextもPASSした。 |

初回canonicalは、既存UI testの無効化対象allowlistに新しい`observation-compute-panel`が未登録のため1件失敗した。正確な対象IDだけを追加するtest統合修正で対応し、product・backendは変更せず全体を再実行した。上表の全体件数と成功は、この再実行の確定結果である。

実画面では同じ認可の観測操作中、runtime入力15分と元operation receipt、SRE／Policyの2 DB snapshot、4 draftを保全した。header roleを下げるとPの取得を禁止し、非表示中を含む3画面の元captureを消去した。adminへ戻しても自動取得せず、明示した2回目の取得で更新した。runtime editorの表示は既存の権限喪失時purgeに従い、最終入力を保存値0分へ戻した。これはserverのpolicy変更ではない。
