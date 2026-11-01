---
project: Shirokuma
doc_id: DB-P6-4B1
title: SQL proposals from explicitly selected authorized metadata
status: accepted
created: 2026-11-01
updated: 2026-11-01
version: "1.0"
area: guides
tags: [database, bears, copilot, sql, local-ai, acceptance]
---

# DB-P6-4B1: 許可metadataからSQL案を生成する

## 利用方法と対象

[A5の保存済み計画再説明](database-p6-4a5-saved-plan-explanation-2026-10-31.md)に続き、DatabaseモードのCopilotで、自然文の依頼と明示選択した参照元から読取り用SQL案を生成する。現在の本人・Database・実行roleで参照できるTABLE／VIEWを1〜8個選ぶ。選択したschemaだけのオブジェクト一覧を読み、列metadataをモデルへ渡すのは生成要求で選んだものに限る。

SQLワークシートの「AI Bear コパイロット」を開いて依頼を入力し、スキーマを選んで「参照元を読み込む」を押す。TABLE／VIEWを選び「ソースを追加」で確定してから「SQL 案を生成」を押す。保存済みの案は「SQL 案の履歴を読み込む」から選択でき、元の依頼文と取得時点の参照元を確認できる。

依頼本文は最大4KiBで、そのままLOCALモデルへ送る。自動収集する情報は選択元のschema・物理名・kind・列名・型形状であり、行データ、現在のSQL入力や結果、VIEW定義、properties、格納先、snapshot、platform情報は添付しない。自然文に含めた任意の秘密値を自動で検出・除去する機能ではない。

生成結果は未実行のSQL案と短い説明として表示する。両SQL入力、worksheetの結果、WAP対象を変更せず、SQLの実行、Stage／Publish、コンピュートの起動を行わない。構文・参照範囲・read-only policyの検査は、列の結合、型、関数の引数、業務上の正しさや実行成功の保証ではない。内容の確認・修正と通常の実行認可を引き続き必要とする。

VIEWは許可された列metadataだけを扱う。定義の展開・保存や再帰依存の収集は行わず、計画取得・実行の適格性や基底データへの権限を証明するものではない。

## APIと保存

`/api/v1/bears/sql-proposals`のPOSTは、既存のidempotency headerと、`context / request / relations / language`だけを受け付ける。`context`は保存9項目、`language`は`ja / en`、`relations`はinventoryから取得した6項目のResourceRefを変更せず渡す。別DB、重複、失効・置換された参照やclient側のSQL／metadataの追加を拒否する。

同collectionのGETで履歴、`/<id>`のGETで詳細、`/<id>/cancel`への`{context}` POSTで当該推論の取消を扱う。既存の`context / explanation(s)` envelopeを使う。

新kindは`sql_proposal`であり、既存の`ai_explanations`へ保存する。schema versionは23を維持する。元の依頼・選択ref・言語、取得時点の公開metadataと日時、SQL案・説明・推論状態を保持し、認可pinsは分けて保存する。同一actor／keyの再送は同一本文だけを復旧し、別kindや依頼・参照元・言語・contextの変更は競合として拒否する。

全参照の同一DB・schema権限・local registry上の識別情報を、最初のREST取得より前に検査する。列metadataを取得後、生成前後に同じ参照と列を楽観的に再確認する。通常のTABLE appendは許容し、置換・列変更・VIEW version変更時は案を破棄する。最終保存も現在の本人・保存context・全schemaの権限と取消状態をwriter transaction内で検査する。

## モデル応答と復旧

モデルへは依頼本文と公開relationsだけを送る。応答は厳密な`{sql, explanation}` JSONで、重複・余分なkey、文章やcode fence、非文字列、空・過大・途切れた応答、tool callを拒否する。

SQLは単一の対応するSELECT／集合演算だけとし、CTEや選択元間のjoinを扱う。既存のparserとread-only policyを通し、実参照が選択したschema・物理名の集合内に収まることを検査する。変更操作、EXPLAIN、複数文、危険な関数、別DB、system schema、未選択参照等を拒否する。モデルが名前を出した追加オブジェクトを取得しない。拒否時は固定error codeだけを保持し、SQL案・説明・parserの抜粋を保存・表示しない。自動修正・再推論を行わない。

履歴・詳細GET・同一要求の復旧は、取得時点のmetadataと保存結果を使い、catalogやモデルを再度呼ばない。元contextと全選択schemaの現在権限は必要とする。元オブジェクトの後日の変更と、保存済みの履歴を区別する。

取消は当該推論だけに作用し、遅着した結果を保存しない。owner喪失や通信の結果が不確かな場合はUNKNOWNを保持する。入力・参照元・worksheetを変えても元key／本文を保持し、明示復旧だけが元の要求を扱う。pollや再読込は新しい推論を送らず、別contextへ遅着結果を表示しない。

## 検証記録

開始前に利用中・旧defaultのSQLite、設定とowner-local YAMLのbytes／mode、28 TABLE・33 VIEW headを保存した。既存11,852行・旧default156行と元AI58件を基準に照合する。

この環境のNode 25.5.0では、大きな`-e`引数でMartsの構文チェックが異常終了した。同じ検査プログラムを一時JSファイルから起動する形へ変更し、全文の構文検査・翻訳評価・全assertionを維持した。独立host検証でMarts3件と言語切替1件が成功し、機能コードは変更せず全体回帰を再実行し、終了コード0で完了した。

| 検証層 | 2026-11-01の結果 |
|---|---|
| 重点・独立レビュー | 最終backend229件（新B1 31・既存AI198）、UI227件（新B1 37・既存画面189・言語切替1）がPASS。独立したbackend／UI source reviewもPASS。 |
| 全体回帰 | 修正後の最終ソースで`make verify-local-experiment`が終了コード0。94グループ・3,926件を実行し、3,892成功・34 skip・失敗0。 |
| 実モデル・休止中の動作 | 2DB・3roleのAPI3件と画面1件が成功。TABLE join／CTE・native／通常VIEWを扱い、Trinoは0/0を維持。同一key再送・履歴で推論を増やさず、SQL／QEC／admission・Wakeの追加は0。 |
| 実画面 | 参照元を明示選択し、未実行案と元要求を表示。両SQL入力・結果は不変。別の依頼を編集中でも履歴を区別し、再読込後も元案をGETで復元。モデル接続停止中にも成立し、追加推論は0。 |
| 拒否・取消・分離 | 実HTTPの拒否10ケースでAI／SQL／provider呼出しを増やさない。制御応答による危険SQL案の拒否とHTTP503はFAILED、取消1件は遅着後もCANCELEDでSQL／説明を保存しない。認可失効・metadata変更・UNKNOWN等は重点回帰で確認。 |
| provider入力 | 全7件（実モデル4・制御応答3）が監査PASS。依頼原文と保存metadataの公開projectionだけを送信。既存回答19件、両SQL入力、VIEW定義22件、native snapshot ID全7件等との照合、Authorization不在・thinking無効を確認。 |
| 運用差分 | 新AI7件は成功4・取消1・失敗2。SQL／QEC／query admission・DataOps・dbt・WAPの追加は0。事前一覧取得による9件の参照登録と、明示CHILL／WAKEの2 operation・2 intake・1 wake admissionを別に照合。 |
| 保全と復元 | 全体回帰後の最終照合もPASS。旧11,852行・旧default156行・元AI58件は不変。28 TABLE／33 VIEW headと、profile・AI設定・owner-local YAML 2件のbytes／modeが一致。今回のAI7件と追記行の集合も中間照合後に不変。LOCALは元policyの2/2へ復元し、doctorはhealthy45・informational1。 |

## 後続

B1受入時点の後続だった[B2 Manual WAP下書き引渡し](database-p6-4b2-wap-handoff-2026-11-01.md)は限定範囲を技術受入済みであり、本ガイドのB1受入結果を維持する。元の保存SQLとcontextを保って通常WAPの新規フォームへ渡し、利用者の確認・編集後に既存の認可／SQL guard／WAPを使う。B2の限定範囲を技術受入済みとした（全体回帰3,944成功・34 skip、2DBの実機・画面・最終保全PASS）。AI専用の高権限実行経路やVIEW sourceの近道を作らず、次は他Bearsの必須導線を統合する。P6-4全体・P6-7縦断検証・M1本人受入は継続する。
