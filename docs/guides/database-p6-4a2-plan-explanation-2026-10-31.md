---
project: Shirokuma
doc_id: DB-P6-4A2
title: Database execution plan acquisition and explanation
status: accepted
created: 2026-10-31
updated: 2026-10-31
version: "1.0"
area: guides
tags: [database, bears, copilot, explain, local-ai, acceptance]
---

# DB-P6-4A2: 実行計画の取得とローカル説明

Query Doctor と Copilot に、SQLの説明とは別の「実行計画を取得して説明」を接続する。元SQLを実行せず、現在の認可済みコンテキストでTrinoへ固定形式の計画取得を依頼し、その安全な構造をローカルモデルで説明する。実装・検証中の記録であり、技術受入は下記の結果確定後に判定する。

## 対象と利用条件

- A1と同じ本人・9項目context・primary／全参照schemaのquery権限を使う。管理者にも他DB参照を許可しない。
- 最初の対象はTABLEまたは参照なしの単一SELECT／CTE／JOIN／集計。VIEWは内部の再帰依存の認可・固定が別途必要なため、計画用queryのadmission前に拒否し、A1のSQL説明を案内する。
- SQLは既存のread-only guardで検証し、サーバーが`EXPLAIN (TYPE DISTRIBUTED, FORMAT JSON)`を付加する。clientがEXPLAIN options、ANALYZE、query ID、provider設定等を指定する入口は作らない。
- 計画取得は通常のquery admissionとLOCALのWakeを通る。コンピュートの起動や計画処理があり、offline／無負荷の操作ではない。元SQLの行結果や実測の処理時間・読取量・課金額を説明しない。

Trino 483の[公式仕様](https://raw.githubusercontent.com/trinodb/trino/483/docs/src/main/sphinx/sql/explain.md)に従う。実機の形式確認でも2 fragment／6 operatorのJSONを取得した。最初のprobeは既存SQL APIの5項目contextが必要なところにfull9を渡して入力拒否されたため、queryが未登録であることを確認し、新しい明示request IDで修正した。A2の新APIはfull9を要求する。

## 計画データと説明の境界

生のJSON／TEXT計画には、値・述語・table handle・格納先・統計等が入り得る。A1のSQL伏せ字だけでは除去できないため、生計画はモデル・画面・generic queryのページやcacheへ返さない。

保存・表示・モデル入力には、許可したoperator名、匿名fragment、children／remote sourceの接続、および固定enumのphase／partitioning／join type／distributionだけを残す。未知のoperator名は固定名へ置き換え、自由文字列のdescriptor／details／outputs／estimatesは使わない。列名・型の説明はA1の認可済みmetadataから行う。table handleを除いた構造からscanと表の対応を推測しない。

計画は応答bytes・cell bytes・fragment数・node数・深さ・収集期限を制限する。ローカルWake待ちのpollはbackendの計画page数には数えない。完成した履歴は当時の安全な構造とmetadataを返し、catalog／query／modelへ再取得しない。

## 受付・復旧・取消

`/api/v1/bears/plan-explanations`のPOST／GET／一覧を使い、bodyはA1同様`context/sql/language`、POSTにはIdempotency-Keyを要求する。kindは`sql_plan_explanation`で、A1のSQL-only記録・historyと区別する。別kindで同じキーを使うとconflictになり、元の記録や意味を変えない。

親AI記録と子query ID・query intent・execution context・admissionを同じtransactionで結び、Wake／POSTより前に確定する。最後の送信claimでも取消を検査する。queryの終了と検証済みの計画保存も同じtransactionへまとめ、モデル呼出しはその後に一度だけ行う。既知のquery終了後に計画検証が失敗しても、queryをUNKNOWNへ書き換えない。

`plan.query_state`と`inference_state`を分ける。モデル停止・不正応答・timeoutでも、取得済み計画とquery evidenceを保つ。同じキーの照会・再読込・履歴・復旧はqueryもモデルも再実行しない。ownerを失った未確定queryは、元IDとadmission blockerを保持し、INTERNALのobserverや新規EXPLAINを自動実行しない。

`/{id}/cancel`は元記録への要求を先に保存する。送信前は計画queryを開始せず、送信後は所有中のquery pinを使って取り消す。DELETEは一度だけclaimし、不明な停止を再送しない。説明中の取消は結果受信の取消であり、モデルの物理停止を意味しない。遅着した説明本文は保存しない。

画面の「新しい要求」は現在の認可で終端を確認してから使う。unknown／activeなキーを自動で交換しない。APIの別キーは独立した明示要求として扱い、SQLの内容だけで恒久的に重複排除しない。

## 検証と残件

利用中と旧defaultの全SQLite backup、設定・owner-local YAMLのbytes／mode、28件のRAW／MARTS headを実装前に保存した。schema versionは23を維持し、既存AI表のkindとJSONを使う。

重点検証はバックエンド52件、A1互換49件、query44件が成功した。UIの追加29件、既存Bears43件・Database UI67件・日英表示1件も成功した。

`make verify-local-experiment`は90単位・3,706件（3,672成功・34 skip）、終了code 0で完了した。最初のA2単位は終端例外の最終補強前の47件であり、補強後は52件とA1 49件・query44件を再実行して成功した。全体回帰のUI単位には最終版29件を含む。文書検証・差分検証も成功し、このTABLE／参照なしSELECTの限定範囲を技術受入済みとする。

実Engine／実modelでは、Aのadmin（8888）とBのdata_engineer（8080）の2DBで、一般的なCTE・集計の計画取得と説明が成功した。各要求の元キー再送・履歴でquery／admissionが増えないこと、通常のDataOps・dbt・WAP実行が増えないことを確認した。外向きの実model本文はprivate relayで取得し、値・コメント・生descriptor／統計／ストレージpathが含まれず、認証headerを転送しないことを検証した。relay以外の外部送信やモデルprocess変更はない。

モデル不調・遅着・owner喪失はtask-owned loopbackで制御した。503では取得済み計画とFINISHEDを保持して説明だけFAILEDとなる。推論中の取消では遅着本文を保存せず、取得済み計画は保持する。実HTTP serverを計画保存後・推論中に再起動したケースは、元要求がUNKNOWN／FINISHED／inference UNKNOWNとなり、新しいquery／推論を起動しない。active Engine owner喪失・送信後DELETEの一度限りclaimは重点unitで確認し、実機の計画保存後restartと区別する。

実認可では、同一9項目contextでも別actorの要求は404となり、adminでも他DBのSQL参照を400で拒否した。閲覧者は許可されたMARTSのTABLE計画・実model説明に成功し、未許可RAW参照は403で拒否した。既存VIEW、クライアントのEXPLAIN／EXPLAIN ANALYZE／DMLは受付前に拒否し、AI／query／admissionの追加がない。A1／A2の履歴kindと同キーの異kind conflictも確認し、A1の実model説明にはqueryが増えない。

実LOCALを0／0へ休止した後のA2は、自動Wakeで2／2へ戻り、計画取得と実model説明が成功した。別の起動待ち要求は子queryがQUEUED・backend IDなしの段階で取消し、query／admissionともCANCELEDとなった。対応するWake admissionもCANCELEDでphysical_startedがなく、公開operationは既存仕様のFAILED／WAREHOUSE_WAKE_FAILEDである。取消が確定した元要求は再実行せず、独立したcold positiveの成功で復帰を確認した。policyはminutes 0／revision 5を維持した。

Chromeでは新しいワークシート15・16を使い、元の13・14のSQLを保持した。Copilot／Query Doctorの両方で実modelの説明・phase・取得日時・コンテキスト・元SQLの表示を確認した。再読込後は元要求の照合から復旧し、履歴とreadonlyの演算子ツリーを表示する。worksheetのSQL実行・結果の置換は行わない。

A2の未確定要求は両入力から独立した本人・9項目context・worksheet・kindで保持する。現在の権限epochで元要求の終了を照合するまで、新しいキーを発行しない。終端の証拠はメモリ内に限定し、保存したhintや古い別要求で解除しない。実画面ではCopilot要求中にDoctor入力だけを変更し、モデルtimeout後のUNKNOWNを再読込して復旧したが、queryも推論も各1回のままだった。計画取得はFINISHED／COLLECTED、推論だけUNKNOWNとして保存した。

受付前の構造化HTTP400（AI_INPUT_INVALID／AI_PLAN_VIEW_UNSUPPORTED、accepted IDなし）だけは、不受付を確認して入力修正を許可する。再読込後は元キー・元bodyで同じ拒否を確認し直し、修正後のSQLを勝手に送信しない。実画面のANALYZE拒否・編集・再読込・元要求復旧・履歴選択で、AI／query／admissionの追加がないことを確認した。完了履歴の演算子ツリーは定期更新後も開いた状態を保持する。

保全比較は利用中79表の既存11,698行、旧default 78表の156行がすべて同一で、旧defaultへの追加は0だった。28件のRAW／MARTS head、profile・LOCAL AI設定・global AI設定・owner-local YAML 2件のbytes／modeも同一である。schemaは両storeとも23のまま維持した。検証に伴う追加56行は新しいAI／query／admission／LOCAL操作の履歴で、既存のUNKNOWN fenceを含む既存行を変更していない。

追加queryは12件（FINISHED 11、送信前CANCELED 1）で、controlled format probe 1件を含め全て固定の非ANALYZE EXPLAINである。追加AIは12件（成功7、失敗1、取消2、推論結果不明2）。追加admission 14件はすべてFINISHEDまたはCANCELEDであり、新しい物理実行の未閉鎖はない。DataOps／dbt／WAPの実行追加は0。LOCALは2／2へ戻し、policyはminutes 0／revision 5を維持した。task relayを停止してAI設定を元bytes・0600へ復元した。

詳細証拠は`var/shirokuma/db-p6-4a2-2026-10-31/private/`の`preservation-after-canonical.json`、`final-execution-summary.json`、`ui-unknown-final-summary.json`と`ui-plan-explanation.jpg`に保存した。これらはprivateな検証資料であり、配布成果物には含めない。

A1のSQL説明は継続する。VIEW依存の計画対応、取得済み計画だけを使う新しい明示再説明、他BearsやSQL案／WAPへの連携は後続単位とする。DB-P6-3全体、P6-7の縦断検証、M1の本人受入は継続する。
