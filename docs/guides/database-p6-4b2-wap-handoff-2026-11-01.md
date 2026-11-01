---
project: Shirokuma
doc_id: DB-P6-4B2
title: Explicit SQL proposal handoff into Manual WAP
status: accepted
created: 2026-11-01
updated: 2026-11-01
version: "1.0"
area: guides
tags: [database, bears, copilot, wap, authorization, acceptance]
---

# DB-P6-4B2: SQL案をManual WAPの下書きへ渡す

## 利用方法と対象

[B1のSQL案生成](database-p6-4b1-sql-proposals-2026-11-01.md)で保存された本人の成功済み案を、既存のManual WAPへ明示的に引き渡す。準備するのは新規・未保存の編集用下書きであり、AI推論、SQL、コンピュート起動やWAPの保存・実行は行わない。元のB1案、worksheet／DoctorのSQLと結果を変更しない。

Copilotで保存済み案を取得し、E／Aで「Manual WAPで編集」を選ぶ。最初の操作は元案のID・SQL・依頼文・contextを固定してManual WAPへ移動するだけである。そこで利用者が同じDB・同じ実行roleのSANDBOX contextを選び、パネルを明示的に更新する。元案のprimary schemaはRAW／MARTS／SANDBOX／STAGINGでもよく、SANDBOXへ読み替えない。

既存の編集中・読込済みモデルや未解決操作がある場合は上書きしない。新規フォームは、model ID・target・品質指定が空／初期値、SQLが`SELECT * FROM source`、sourceが未選択の状態を必要とする。利用者が既存の「新しいモデル」を選んで意図的に下書きを戻し、必要なcontext・inventoryを確認してから「提案の下書きを読込」を選ぶ。未解決の保存済み要求や操作は、フォームを空にしても無視しない。

取込後は変換済みSQLとsource aliasが通常の編集欄へ入り、model IDとtargetは未指定のままとする。SQL・source・target・品質を確認・編集し、既存の「モデルの版を保存」→「保存済みの版をビルド」→「ビルドを監査」→Aの「監査済みビルドを公開」をそれぞれ明示的に行う。Eは下書き準備・通常の保存／build／auditを使えるが、公開権限は得ない。VはB1案を閲覧できても、この引渡しは行えない。

## 元contextと実行先context

元案とWAP実行先は、それぞれ厳密な9項目contextを持つ。本人、DB、catalog、LOCAL、実行role、binding／generation／grantの値を一致させ、異なってよいのは`schema_id`と`sql_schema`だけである。実行先は現在書込可能なSANDBOXとする。role昇格、別DBへの引渡し、古いgrantの現在値への置換やcontextの自動選択は行わない。

元案のprimaryと、B1で選択した全schemaの現在のquery権限を検査する。SQLで使わないVIEWや削除済みオブジェクトは、新たにmetadataを取得しないが、そのschemaの権限は必要である。SQLで使うsourceはTABLEに限り、現在のregistry識別情報、UUID、列名・型を確認する。通常のdata appendや空TABLEは許容し、置換・列変更等は拒否する。VIEW sourceの私的なDBT経路は使用しない。

準備前後に両contextと現在の権限・使用sourceを再確認する。これはcatalogのserializable snapshotを作るものではなく、通常WAPの保存・build・audit・publishは、その時点の既存認可とsource／target検証を改めて行う。

## SQLの変換とAPI

`GET /api/v1/bears/sql-proposals/<proposal_id>/wap-draft`は、`context`と`destination_context`だけを各9項目のJSONとして受け付ける。GET body、idempotency key、client SQL、model ID、targetやmetadataを受け取らない。元案は保存済みの`sql_proposal`かつSUCCEEDEDであることを必要とし、元要求の復旧・状態変更をしない。

保存SQLは元contextのschemaで解釈する。SQLGlotのscopeを使って物理TABLEとCTEを区別し、CTE名と競合しないsource aliasへ機械的に変換する。明示したSQL aliasや相関scopeを保ち、namespace付きcolumnは同じ物理bindingだと一意に確認できる場合だけ修飾を外す。不確かな修飾、使用VIEW、sourceのないSQL、コメント・再帰CTE等の通常WAP非対応構文は拒否する。文字列置換やVIEW定義の展開は行わない。

変換後は既存の[Manual WAP compiler](../../tools/local_experiment/manual_wap.py)で互換性を検査する。source bindingは元の6項目ResourceRefを検証した後、通常WAPが受け取る4項目Refへ渡す。列の結合、型、業務上の正しさや実行成功を保証する検査ではない。

応答は`{context,draft}`で、draftは`version / proposal_id / source_context / destination_context / original_sql / sql / sources / request / proposal_created_at / prepared_at`の10項目だけとする。sourcesは`{alias,ref,schema,table}`の1〜8件であり、refは元の6項目を維持する。private pins、credential、格納先、行データ、実行planや新しいoperation IDを含めない。

主な固定拒否は、元案が使えない`AI_WAP_UNAVAILABLE`、VIEWや不確かな変換の`AI_WAP_UNSUPPORTED`、使用source変更の`AI_METADATA_CHANGED`、contextの組合せ不一致の`AI_WAP_CONTEXT_MISMATCH`である。現在の認可拒否や別actor／kind／元contextへの404も維持する。parserやprivate metadataの抜粋をエラーへ出さない。

## 画面の保護と元案の表示

準備する元案は現在のworksheetやsource pickerから取り直さず、選択時のID・本文・contextへ固定する。取得中のcontext、認可、inventory、フォームやworksheet変更で遅着応答を無効にする。全bindingが現在のTABLE inventoryと一致することを確認するまでフォームを変更せず、名前や先頭の候補へのfallbackをしない。

元の依頼・SQL・contextと、現在のWAP実行先を読取専用で別表示する。取込後に編集したSQLがAI承認済みだとは扱わない。同じ下書きの明示保存・build・audit中は元案表示を保持できるが、新しいモデル、別モデルの読込や認可／contextの変更では解除する。この表示はsession内だけの情報であり、保存モデルへの永続的なAI lineageは追加しない。

GETの準備要求は保存レコードや実行を作らず、明示的な再試行ができる。再読込後はB1履歴から元案を改めて取得・選択する。通常WAPの版や実行の復旧は既存の元key・版・artifactを使い、UNKNOWNを終了扱いにしたり、処理を自動再実行したりしない。

## 検証記録

B2の限定範囲を技術受入済みとした（全体回帰3,944成功・34 skip、2DBの実機・画面・最終保全PASS）。P6-4全体・P6-7縦断検証・M1本人受入は未完了である。

| 検証層 | 記録する範囲 |
|---|---|
| Backend／UI・独立レビュー | Backend 364件（B2新規23件）、UI 410件（B2新規29件）成功。全文JS／Martsを先に確認し、既存AI／DataOps／DBT／言語切替の回帰と独立ソースレビューを完了した。 |
| 全体回帰・文書 | 最終ソースのcanonical 95単位・3,978件実行（3,944成功・34 skip）、終了コード0。全文JS／Martsを先行確認し、既存WAP／AI／言語切替も通過。design-contextを別に確認した。 |
| 実機の準備 | AのMARTS由来の結合／CTEと画面用TABLE案、BのEのRAW TABLE案を、それぞれ同じDB・roleのSANDBOXへ準備した。GETの反復と画面取込の測定区間で全SQLiteテーブル不変、LOCAL 0/0、SQL・AI・Wake・保存0を確認した。使用VIEW／V／別actor／不正destination等を拒否した。 |
| 明示的な通常WAP | 画面でAの新規モデルをCAS保存v1→build→audit合格→A公開し、新規MARTS `p64b2_balances_20261101`を作成した。Vの明示QAクエリは監査と一致する2件の集計を返した。BのEは別の新規モデルを保存・build・auditし、最小行数未達を確認した。Eの公開は権限で、Aの公開は品質gateで拒否された。これらは準備GETとは別の明示操作である。 |
| 保全・復元 | 実機確認後の照合は既存active 11,873行／default 156行、元の28 TABLE・33 VIEW head、旧UNKNOWN／fence、schema version 23、設定・owner-local YAMLのbytes／modeが不変。元のLOCAL 2/2・policyへ復元し、doctor healthy。全体回帰後の最終照合もPASS。追加58行は独立レビューで全件を分類し、未知の追加実行・公開はない。 |

画面取込では、MARTSとSANDBOXの両context、元の読取専用SQL、編集用alias SQLを別に表示した。既存の下書きを入力すると読込は無効になり、利用者による「新しいモデル」の選択後も元案の選択は保持された。同じモデルの保存・build・audit・公開後も元案を表示し、worksheet／Doctorの本文は変わらなかった。

実機用のEのTABLE案を準備するため、既存B1経路でLOCALモデルを1回だけ明示的に呼び、AI記録を1件追加した。その後は検証用loopback中継を503に固定し、B2のAPI／画面取込と通常WAP・QA中の追加呼出しが0件であることを確認した。中継は停止し、元のAI設定bytes／modeへ戻した。SQL案準備のB1推論、B2のmetadata読取り、通常WAPとQAのSQLを合算して「実行なし」とは扱わない。

## 後続

他Bearsの必須導線を別単位で統合する。native VIEWの私的経路や新しいAI実行権限で範囲を広げない。P6-4全体・P6-7の縦断検証・M1本人受入は継続する。
