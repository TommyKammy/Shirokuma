---
project: Shirokuma
doc_id: DB-P6-4A5
title: Inference-only explanation of a saved execution plan
status: accepted
created: 2026-10-31
updated: 2026-10-31
version: "1.0"
area: guides
tags: [database, bears, copilot, explain, saved-plan, local-ai, acceptance]
---

# DB-P6-4A5: 保存した実行計画だけで再説明する

## 使い方と対象

[A4の最新SELECT読込](database-p6-4a4-latest-query-source-2026-10-31.md)に続き、Query Doctor／Copilotで保存済み計画を選び、「保存済み計画を再説明」から別の説明を要求できる。SQL入力の編集や再実行は不要で、元SQL・metadata・安全な演算子・取得日時を固定した新しい推論だけを作る。

元のA2／A3説明に有効なCOLLECTED計画があり、保存された状態と元の型付きquery台帳の両方でFINISHEDを確認できることを必要とする。元説明はSUCCEEDED／FAILED／CANCELED／UNKNOWNが対象で、QUEUED／RUNNINGや未取得計画、A1や再説明からの連鎖は対象外とする。

UNKNOWN元説明が対象になるのは、計画取得は正常終了済みで、元推論の結果だけが不明な場合である。元のUNKNOWNを終了扱いにしたり、元要求を再送したりしない。元の状態を表示したまま別の説明を作り、A2の新しい計画取得を許可する条件も変更しない。

結果は元計画の取得時点の説明であり、現在のVIEW定義、現在の計画、実測行数・時間・費用の証明ではない。履歴取得後のTABLE／VIEWの変更・削除では、現在のcatalogへ取り直さない。

## API・認可・固定する情報

`/api/v1/bears/saved-plan-explanations`のPOST／GET一覧／GET詳細と、`/<新しいID>/cancel`を追加する。POST本文は`{context,source_explanation_id,language}`だけで、既存と同じ保存キーを使う。clientからSQL・plan・provider設定を受け取らない。kindは`saved_plan_explanation`とし、A1／A2のPOST契約を維持する。

元の本人・kind・厳密な9項目context、元IDに結び付くEXPLAIN子queryのactor／context／request ID／worksheet／SQL／FINISHEDをローカル台帳で検証する。元のGET復旧、query照会・取消やcatalog再取得を受入判定に使わない。元情報の検証・深いコピー・現在認可・新規保存を同じtransactionで行う。

現在のprimaryと凍結された再帰依存全体のschema権限、generation／binding／grant／logical identityを、受付・推論前・結果保存時・GET／履歴／取消で確認する。元のgrant revisionを現在値へ置き換えず、adminでも別人の計画を使用しない。current catalogのheadやTABLEのデータappendを検査する処理は追加しない。

新レコードには元の公開metadataとprivate権限pinsを別々にコピーする。`source_snapshot`は`explanation_id / query_id / state / query_state / format / captured_at / sql / operators`の8項目だけである。元queryのFINISHEDは出典情報として保持し、新しいqueryの進捗や取消先へ流用しない。元の制御情報・所有者・回答・provider設定は共有しない。

再送・履歴は新レコード自身のコピーを使う。元の保存行が後から変化しても、新しい説明を元行へ書き戻さず、元計画のquery／admissionへ新しい親IDを付けない。

## モデル・取消・復旧

既存の許可されたLOCAL AIへ、値・コメントを除いた元SQL、取得時点の公開relations／view dependenciesと閉じた安全な演算子だけを渡す。元回答、VIEW定義・private head・digest・snapshot ID、格納先、raw Engine応答、行データを渡さない。

A2／A3のv1演算子の上限とmetadata予算を維持する。TABLEはrelations、VIEWは`{relations,view_dependencies}`を64KiB以下とし、取得日時はその予算に含めない。共有依存の検証は高さをメモ化し、64関係・深さ16の有効なDAGを経路数に応じて繰り返し探索しない。

新IDだけが推論の所有者になる。取消は新しい回答を破棄し、遅れて届く本文を保存しない。元説明・元queryを取り消さない。推論timeoutやowner喪失はUNKNOWNのまま保持し、GET・履歴・再送で推論を自動再実行しない。

画面は元IDを固定し、両SQL入力から分離して新しい説明・履歴・復旧・取消を扱う。再読込や別source選択後も未確定のA5要求を保持し、新しいkeyを作らず、受理済みIDのGETまたは元key／bodyの明示復旧を使う。自動pollは別の未確定要求を選んで再送しない。遅着応答は現在の入力・選択・scopeへ古い回答を表示せず、入力やworksheet結果も上書きしない。

## 検証記録

開始前に利用中・旧defaultのSQLite、設定とowner-local YAMLのbytes／mode、28 TABLE・33 VIEW headを保存した。schema versionは23のままとする。

| 検証層 | 2026-10-31の結果 |
|---|---|
| 重点・独立レビュー | 新規A5 23・A1 49・A2 52・A3 59・A4 15のbackend198件、画面189件・言語切替1件のUI190件が最終ソースでPASS。独立したsource reviewもPASS。 |
| 全体回帰 | `make verify-local-experiment`は終了コード0。93グループ・3,858件を実行し、3,824成功・34 skip・失敗0。 |
| 実モデル・休止中の動作 | LOCALが0/0のまま、2DB・3role、定数／native VIEW、失敗・取消・推論UNKNOWN、元VIEW head変更後の計画を使う8件が成功。同一key再送・履歴では推論を増やさず、query／QEC／admissionとWakeの追加は0。 |
| 実画面 | UNKNOWN元説明から9件目の実モデルによる再説明が完了。元UNKNOWNを保持し、異なるSQLの両入力とworksheet結果を変更しない。再読込後も新しい推論をせず元IDのGETで同じ結果を復元。LOCALは0/0を維持。 |
| 新しい取消 | 遅い応答を1件だけ制御し、新A5を取消。遅着本文を保存せず、元UNKNOWN説明／元queryを変更しない。実モデル9件とは区別する。 |
| 拒否と分離 | 他actor・異なるcontext、A1／未取得／A5からの連鎖、client SQLの追加、元IDへの新kind取消、保存キーの本文変更を拒否し、AI／query／推論を増やさない。権限失効・owner喪失・timeout・遅着・projection上限の境界は重点回帰で確認。 |
| provider入力 | 全10件を検査。元回答13件、VIEW定義22件、native snapshot ID全7件等と照合し、private情報・元ID／時刻／状態・SQLの値／コメントを含めない。Authorization不在・thinking無効、閉じた入力契約を確認。 |
| 保全と復元 | 利用中の旧11,837行と旧defaultの156行、28 TABLE／33 VIEW head、profile・AI設定・owner-local YAML 2件のbytes／modeが一致。UNKNOWN／fenceと元説明48行も不変。検証後の明示Wakeで2/2・元policyへ復元し、doctorはhealthy 45・informational 1。全体回帰後の最終照合もPASS。 |

コピー後の元保存行変更・削除、再帰schemaの失効、推論中の権限失効はunitの隔離storeで検証し、実環境の既存行やgrantは書き換えない。実環境では、以前に変更・再作成されたVIEWの保存済み計画をそのまま利用し、取得時点の情報が維持されることを確認した。

モデル入力の検査はtask専用loopback中継を用い、既存の実LOCALモデルへ転送した。検査後に中継を停止し、AI設定を元のbytes／modeへ復元した。元モデルのプロセスを停止・再設定していない。明示CHILL／復元WAKEは検証準備・後片付けとして記録し、A5によるWarehouse操作に含めない。

private証跡は`var/shirokuma/db-p6-4a5-2026-10-31/private/`、実画面は同配下の`ui-saved-plan-explanation-cold.jpg`、送信検査は`privacy-summary.json`へ保存した。

## 残件

再説明からの連鎖、指示文やSQLの編集を伴う再説明は対象外とする。A5受入時点の後続だったSQL案生成は、[B1のSQL下書き](database-p6-4b1-sql-proposals-2026-11-01.md)として技術受入済みであり、本ガイドのA5受入結果は維持する。B1を技術受入済みとした（全体回帰3,892成功・34 skip、2DB・3role実機・最終保全PASS）。次はB2の既存認可・SQL guardを通すWAP引渡しと他Bearsを別単位で扱う。P6-4全体・P6-7の縦断検証・M1本人受入は継続する。
