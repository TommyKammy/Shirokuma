---
project: Shirokuma
doc_id: DB-P6-4A4
title: Latest authorized SELECT source for Query Doctor
status: accepted
created: 2026-10-31
updated: 2026-10-31
version: "1.0"
area: guides
tags: [database, bears, query-doctor, query-history, authorization, acceptance]
---

# DB-P6-4A4: 最新のSELECTをQuery Doctorへ読み込む

## 優先順位と使い方

[A3のVIEW計画説明](database-p6-4a3-view-plan-explanation-2026-10-31.md)に続き、Query Doctorの残る入力導線を先に接続する。AI-01の最新query指定を、現在の本人・DB・schema・roleに合い、既存A1 parserの対象となる最新のSELECTとして提供する。次は取得済み計画だけを使う明示的な再説明、SQL案／WAP引渡し、他Bearsを別単位で扱う。

Query Doctorで「最新の SELECT を読み込む」を選ぶと、説明用の入力欄だけに元SQLをコピーする。元queryのID・受付時刻・状態を出典として示す。SQLの実行や推論は開始しない。内容を確認し、「SQLの説明」または「実行計画を取得して説明」を別途明示操作する。ワークシート本文・結果は変更しない。

元queryが過去に実行済みでも、読み込み・SQL説明・計画取得で元SQLを再実行しない。計画説明の「元のSQLは未実行」は、その説明要求が元SELECTを実行していないことを示す。

同じ9項目contextなら、別worksheetで受け付けた本人のSELECTも対象になる。最新は受付時刻順で、同時刻なら保存時の挿入順に決める。元queryがRUNNINGやUNKNOWNでも扱うが、出典の状態を表示するだけで照会・再開・取消・終了判定を行わない。該当する履歴がなければ、手入力またはworksheetからのコピーを案内する。

## APIと認可

`GET /api/v1/bears/query-sources/latest?context=<JSON>`をWorkbenchとnative endpointで共有する。GET bodyや追加parameter、任意query ID、POSTを受け付けない。

応答は`{context,source}`で、`source`はnullまたは`{query_id,created_at,state,sql,context}`だけを返す。backend ID・credential・結果・request／worksheet ID・provider設定は含めない。

履歴上の本人と9項目context、read-only modeを照合する。現在のgrantへ履歴のgrant revisionを置き換えない。primaryとSQLに明示された参照schemaの現在のquery権限、generation／binding／logical identityを返却までに再確認する。adminにも他人の履歴を返さない。

既存A1 parserで扱えるSELECT／CTE／集合演算を対象とし、EXPLAIN・書込み・不正構文、DataOps・AI・admissionの親子台帳に結び付いた内部処理を除外する。構文上の対象外を飛ばす一方、対象候補の認可失敗では古いSQLへfallbackしない。履歴の静かな構文確認により、不正な過去SQLをparserのログへ漏らさない。

読み込みはローカル台帳と認可情報だけを使用し、catalog・Engine・Wake・AIへ接続しない。VIEW定義や依存先を新たに取得しない。読み込んだ後のA1／A2操作では、その既存の現在認可・metadata／VIEW検証・値とコメントの除外を改めて実行する。POSTのbody・key・取消／復旧契約は維持する。

## 画面と元要求の復旧

取得中の手編集、actor・role・context・worksheet変更、コピーや再読込で遅着応答を無効にする。出典は編集・コピー・scope変更・再読込で消し、未検証の出典情報を永続化しない。null／失敗では入力を保つ。異なるSQLになった場合の既存の説明表示の無効化を維持し、過去の保存済み説明は書き換えない。

A1／A2に未確定の元要求がある場合は、先に明示的な更新／復旧へ案内する。受理済みの元IDはGET、受理不明なら保存した元key／bodyだけを明示的に再送する。入力が変わった場合も元要求を選び、異なる入力欄の下へ古い説明を表示せずに終了状態を確認する。履歴の表示上限を超えた元要求も同じ方法で確認する。

受付前の構造化400 `AI_INPUT_INVALID`は、元key／body・現在のscope・未受理を確認した場合だけ、画面内の読み込み許可に使う。保存されたhintだけで再読込後の許可を与えず、元要求への新しい応答で再確認する。A1の新規説明受付の意味は変更しない。

## 検証記録

実装前に利用中・旧defaultのSQLite全体、設定とowner-local YAMLのbytes／mode、28 TABLE・33 VIEWのheadを保存した。schema versionは23を維持する。

| 検証層 | 2026-10-31の結果 |
|---|---|
| 重点回帰 | 新規source 15・A1 49・A2 52の116件と、画面167件・言語切替1件の168件が、最終ソースでPASS。既存の画面テストケースを維持し、追加分も検査した。 |
| 全体回帰 | `make verify-local-experiment`は92グループすべてOK、3,779成功・34 skip、終了コード0。重点検証と実機証跡を併せて、この限定範囲を技術受入済みとした。 |
| 実API | admin／data_engineer／bi_viewer、2DB、8888／8080で元ID・SQL・9項目context・状態を確認。空のcontextではnull、追加ID・POST・GET body・他catalogへ改変したcontextは拒否。読み込み前後のAI／query／admission／DataOps／dbt／WAP件数は同一。 |
| 実画面 | 別の検証worksheetから最新SELECTを読み込み、出典ID・受付時刻・状態を表示。手編集と再読込で出典が消え、再取得で元SELECTへ戻ることを確認。読み込み・編集・再読込では新しい処理を開始しない。 |
| 明示的な説明 | 読み込んだVIEW参照SQLから、別操作で固定EXPLAINと実ローカルモデルの説明がFINISHED／COLLECTED／SUCCEEDEDまで完了。後から生成されたAIのEXPLAINを最新SELECTへ混入させない。 |
| 運用差分 | 追加は準備用の通常SELECT 3件と、明示的な計画説明のAI 1件・EXPLAIN 1件だけ。4 query／QEC／admissionは全件FINISHED、重複request IDと未確定な新規実行は0。DataOps／dbt／WAP・object binding・lifecycleの変更は0。 |
| 保全・通常運用 | 利用中の旧11,824行と旧defaultの156行は全列一致。既存UNKNOWN／fenceも不変。28 TABLE／33 VIEW head、profile・AI設定・owner-local YAML 2件のbytes／mode一致を確認。通常両portのSANDBOXは`lake_marts`だけを提示。最終doctorはhealthy 45・informational 1。全体回帰後の最終照合も一致。 |

準備用SELECTは通常のWorkbench受付で明示的に作成し、元request IDごとに1件だけであることと、既知の完了状態を台帳から確認した。読み込みの検証と元SELECTの実行を分けて記録している。

private証跡は`var/shirokuma/db-p6-4a4-2026-10-31/private/`へ保存した。実画面は同配下の`ui-latest-query-explanation.jpg`、実行差分は`final-operation-audit.json`である。ローカル設定・モデルの接続先・grantを検証のために変更していない。

## 残件

取得済みplanの明示再説明、SQL案／WAP引渡し、Catalog Curator／SRE／Policy Gatekeeper等は別単位である。P6-4全体、P6-7の縦断検証とM1本人受入は未完了である。
