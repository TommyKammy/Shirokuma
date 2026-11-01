---
project: Shirokuma
doc_id: DB-P6-4C1
title: Authorized catalog explanations and description drafts
status: accepted
created: 2026-11-01
updated: 2026-11-01
version: "1.0"
area: guides
tags: [database, bears, catalog, metadata, authorization, acceptance]
---

# DB-P6-4C1: Catalog Curatorのmetadata説明・説明文案

## 対象と操作

Catalog CuratorでDatabase・role・primary schemaを選び、contextを明示更新する。そのschemaのobjectを読み込み、安定したResourceRefを持つTABLE／VIEWを1件選んで「説明と説明文案を作成」を実行する。SQLワークシートの選択は前提とせず、Explorerの選択や他のBears・WAPの入力からcontextを自動転用しない。LOCALコンピュートを起動せず、既存のLOCAL AIを使用する。

対象は本人の現在のV／E／A権限と保存9項目context、同じDB・primary schema・binding／generationに属するTABLE／VIEWである。選択したschemaの現在のquery権限を検査し、write権限やplatform管理権限を付与しない。任意の有効な名前・現行列を対象とし、固定サンプルだけの機能にはしない。

結果は、取得時点の名前・型から推定した要約、表の説明文案、最大8列の説明文案である。列文案は一部だけの場合があり、対象数と上限を表示する。業務上の意味・正しさを確認した記録や適用済みのcatalog説明ではない。利用者が内容を確認する読取り専用の提案として表示し、説明の保存・COMMENT／DDL・SQL実行・editor書換え・WAP操作は提供しない。

## 入力とmetadataの境界

推論に渡すのは、明示選択した1件のschema名・table名・kindと、現在の列名・型だけである。自由入力のpromptは設けない。既存コメントや型のdocumentation、行データ・件数・統計・snapshot、catalog properties・格納先・設定・credential、VIEW定義・default namespace・依存関係を公開metadataやprivate pins、モデル入力へ含めない。

TABLEのcurrent schema、VIEWのcurrent versionに対応するschemaをcatalog RESTから読み、registryの安定Ref・backend UUID・列構造と照合する。VIEWの列metadataを説明する操作であり、INVOKER／DEFINERや依存先の実行可能性、[A3のVIEW計画条件](database-p6-4a3-view-plan-explanation-2026-10-31.md)を検証したとは表示しない。lineageやprofilingも対象外とする。

REST前に現在の本人・保存context・schema権限・local identityを検査し、取得後、推論前後、最終保存時にも該当する現在権限と構造の整合性を検査する。UUID・列構造の変更は新しい成功結果へ混在させない。TABLEの通常append・propertyだけの変更、同じUUID・同じ列を保つVIEW定義／version更新は許容する。これはmetadataの構造検査であり、SQLの意味や依存の固定ではない。

`resolve_object`等のregistry更新を伴う経路を使わず、catalogの直接読取りを使う。AI操作が書き込むのは既存のAI lifecycleとowner leaseだけである。object一覧を読む明示操作は既存inventoryの別操作として扱い、AIの説明要求と同一の測定区間へ混ぜない。

## APIと出力契約

| 操作 | 契約 |
|---|---|
| `POST /api/v1/bears/catalog-proposals` | 既存の`Idempotency-Key`必須。bodyは`{context,relation,language}`だけ。contextは正確な9項目、relationは選択元のRef6、languageは`ja`／`en`。SQLやclient metadataは受け付けない。 |
| collection／detailの`GET` | 保存contextの指定と現在認可が必須。本人・kind・元contextを検査し、取得時点のmetadataを返す。catalogやモデルを再呼出ししない。 |
| `POST .../<id>/cancel` | 元contextで本人の要求だけを取り消す。他のBearsやSQLへ作用しない。 |

kindは`catalog_proposal`とし、既存のAI storageを使う。公開recordの`input`はrelation／language、`metadata`は1件の公開relationと取得時刻、`proposal`は成功時だけの厳密なJSONとする。SQL・実行ID・private pins・接続URL・providerの生応答は公開しない。

成功出力は厳密な`{summary,description,columns}`だけであり、summary／descriptionは各4,096 UTF-8 bytes以内、columnsは0〜8件の`{name,description}`である。列名は取得した列に厳密に一致し、重複や未知の列を拒否する。列の説明は各1,024 bytes以内、全JSONは16,384 bytes以内とし、元の列順に整える。重複key・余分なfield・空の説明・NUL・不正UTF-8・周囲の文章やcode fenceは拒否する。

metadataは49,152 UTF-8 bytes以内とし、既存providerの固定request／response上限を維持する。型やmetadataが不正、catalog I/Oが失敗した場合は固定の`AI_METADATA_UNAVAILABLE`、識別情報や列構造の変化は`AI_METADATA_CHANGED`、不正な生成物は`AI_OUTPUT_INVALID`を返す。現在権限の喪失、他actor、入力不正、冪等性conflict等も既存の固定codeで扱い、SQL・秘密値・parser／providerの生抜粋をerrorへ含めない。

## 元要求・履歴・取消

最初の要求で本人・全context・選択Ref・language・keyを固定する。同じkeyの照合は元recordだけを返し、異なるpayloadやkindへ再利用しない。元の受付IDがあれば明示GETで復旧し、受付が不明な場合だけ元key／bodyを明示照合する。object・context・roleを変えても元の未解決要求を上書きせず、reloadや履歴から自動POSTしない。UNKNOWNを停止や未受付の証拠へ読み替えない。

現在の結果は元の選択に結び付け、履歴は元のsource・context・取得時刻・model・状態を示す。履歴の表示によって現在のsource pickerを変更しない。後着応答が新しい選択や別要求を上書きしないようにし、権限失効時は該当する出力を隠して安全なreceiptを保持する。取消・遅着結果・owner喪失・最終認可失敗から成功文案を復活させない。

## 検証記録

C1の限定範囲を技術受入済みとした（全体回帰4,020成功・34 skip、2DB・3roleの実API、adminの実画面、最終保全PASS）。

| 検証層 | 確認する範囲 |
|---|---|
| 契約・独立レビュー | 単一TABLE／VIEW、現在権限・identity、限定metadata、厳密な部分出力、独立した元要求・履歴・取消のソースレビューを完了した。実推論4件の独立70項目、画面・故障検証と復元後の独立84項目の照合もPASS。 |
| Backend／UI重点回帰 | Backend 399件（C1新規35件）、UI 451件（C1新規41件）成功、skip 0。差替え・列変更・失効・後着応答と、通常append／同じ列のVIEW版変更の扱いを重点検証した。全文JS／Martsを先行確認し、既存AI・WAP・legacy・JA／ENも通過した。 |
| 全体回帰・文書 | 最終固定10ファイル（当初8ファイルとNode入力方法だけを変更した既存テスト2ファイル）でcanonical 96単位・4,054件を実行（4,020成功・34 skip）、終了コード0。文書のdesign-contextも全体回帰と分けてPASSを確認した。 |
| 実機・画面 | 2DB・V／E／AでTABLE／通常VIEW／native VIEWの実LOCAL推論4件と、画面から別DBのRAWテーブルを選ぶ実推論1件が成功した。拒否14ケースは全SQLiteテーブル・provider呼出し数が不変。モデル停止相当／不正応答／取消／タイムアウトも確認し、元keyの再照合で呼出しは増えなかった。タイムアウト1件はUNKNOWNを維持する。 |
| 読取専用性・復旧 | 推論区間で変更は新しいAI行だけ、LOCAL 0/0、SQL／Engine／Wake／admission・registry変更なし。現在のsourceを変えても履歴は固定sourceを表示し、reloadは受付IDのGETで復旧する。完全なserver access logで、画面生成後のcollection POSTが元の1件だけであることを確認した。他のSQL／Doctor／WAP本文も不変。 |
| 保全・復元 | 実機後の照合は旧active 11,931行／default 156行・旧AI 66件・UNKNOWN／schema23・保護対象31 TABLE／33 VIEW head・設定とowner YAMLのbytes／modeが不変。追加14行はAI 9行と明示管理操作5行（CHILL／WAKEの各operation・requestと終了済みWake受付）に全件分類した。元のAI設定・LOCAL 2/2・policyへ戻し、doctor healthy。全体回帰後の最終照合もPASSし、doctorの終了コード0・全体healthyを確認した。 |

初回のcanonicalは92単位・3,961件の実行で、既存UIテスト3件のNode subprocessがSIGSEGV（終了コード-11）となり、終了コード2で中断した。Node 25.5.0へ全文scriptを`-e`引数で渡す経路で再現し、テスト内のassertionへ到達しなかった。
[test_workbench.py](../../tests/test_workbench.py)の2か所と[test_workbench_refresh.py](../../tests/test_workbench_refresh.py)の1か所だけをstdin入力へ変更し、同じscriptと全assertionを維持した。C1の当初固定8ファイルも変更していない。
対象3件はNode 25／22の双方で成功し、hostのcombined UI 860件と後続のCatalog／Marts／semantic 28件も成功した。stdin方式を含む最終10ファイルで全体回帰を再実行し、上表の96単位・4,054件で終了コード0を確認した。個別確認と全体回帰の件数は合算しない。

実LOCALモデルへの送信は5件であり、別の4件はtask専用loopback応答による停止相当・不正応答・遅延の検証である。いずれも新しいAI記録として区別し、UNKNOWNを未受付や停止済みへ読み替えない。実機の休止／復元は独立した管理者操作で、C1による自動Wakeではない。

差替え・列変化・権限失効の境界は重点テストで確認し、実機では既存の保護対象を変更せず現在権限・identityの照合、3ロールと拒否ケースを確認した。業務上の正しさや説明の保存・適用、VIEWの定義・lineage対応を受入したものではない。

## 後続

[B2の限定技術受入](database-p6-4b2-wap-handoff-2026-11-01.md)を維持し、SRE／Policyの必須観測はDB内情報とP専用platform情報・観測時刻を分けて統合する。旧Curatorの検索／lineage経路、説明の保存・適用や任意manifest入力を開放しない。P6-4全体・P6-5・P6-7縦断検証・M1本人受入は継続する。
