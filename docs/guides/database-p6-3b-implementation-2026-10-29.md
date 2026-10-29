# DB-P6-3B Database dbt SQL / WAP 実装・検証記録

2026年10月29日。table／ephemeral SQL対応範囲の技術受入完了。[契約](database-p6-3b-dbt-contract-2026-10-29.md)に従い、[DB-P6-3A](database-p6-3a-dbt-feasibility-2026-10-29.md)のoffline compileを耐久plan／run、全testの公開gate、通常API／CLI／Workbenchへ接続した。

## 利用できる範囲

データ変換の「Database dbt SQL / WAP」で、承認済みproject、Database、source／公開先Schemaを選び、compile→候補構築・test→モデル単位の明示公開へ進める。Worksheetと独立したSANDBOX contextを使う。同梱`lake_marts`のstaging既定はviewなので、現在の対応範囲では`ephemeral`を明示指定する。自動でmaterializationを変更しない。

これはcompiled table／ephemeral SELECTを既存WAPで構築する実行経路である。標準dbt build executor、view、table properties固有処理、Python、任意shell、外部package実行には対応していない。任意project path、接続秘密値、DDLを公開入力として受け付けない。追加projectはoperatorが承認済みローカルprojectとして登録し、同じ処理で検証する。

compile・build／testは現在のE／A、公開は現在のAが行う。Eは本人の保存roleに一致するartifact、Aは同DBの認可済みmetadataを参照できる。AがEの候補を公開するときはA自身の新しい親admissionと資格情報を使う。閲覧者へsource SQLを返さない。

## 実装

- `db_dbt.py`がplan／run／publicationとDataOps childを管理し、共通adapterをWorkbench／native HTTPへ登録した。CLIは`shirokuma dbt`、画面は既存の変換パネルへ接続した。
- Control storeを21→22へ移行し、artifact、child operationリンク、同一key受付の3テーブルを追加した。既存台帳を保持する。過去schemaへの移行fixtureは、新規DBへ旧version番号だけを設定する状態を修正した。
- 実際にcompileが読んだbytesからproject版を確定する。run開始時にsource Ref／Snapshot／schemaを一度固定し、全モデルとtestへ再利用する。候補Ref／SnapshotとWAP版も保存する。
- 公開gateは必要test ID集合の完全一致、全件pass、取消／UNKNOWNなし、plan版と候補identityを検査する。dbt管理下の候補を旧WAP公開routeへ渡しても迂回できない。
- 受付後は元key／body／contextのreceiptを回収する。GET、画面再読み込み、再起動、reconcileから未実行のSQLやcompileを開始しない。取消は先に親へ保存し、SQL送信・結果取得の直前にも現在権限と取消を確認する。
- 公開はモデル単位である。runのREADYは「テスト成功・公開待ち」、公開commit後の記帳失敗は`COMMITTED_RECORDING_FAILED`として区別する。複数Tableの一括原子性を約束しない。
- dbt監査は保存metadataと認可済み関連IDだけを返す。SQL本文、private credential reference、raw logは監査projectionへ含めない。

## 実機で確認した結果

既存の専用2DBで同名の5モデル／7testを使った。APIとnative HTTP CLI、実ブラウザーを分けて確認した。一般のsource／model処理を使い、fixture専用の実行分岐は追加していない。

| 確認 | 結果 |
| --- | --- |
| APIのDB A | 2 table候補と3 ephemeralを処理。accepted_valuesがfailure_count=1、他6件成功。runはFAILED、公開は`DBT_TEST_GATE_FAILED`で拒否 |
| CLIのDB B | 保存compile、run、statusを通常CLIで実行。7/7成功しREADY。Aが2モデルを個別公開、各PUBLISHED |
| source版固定 | 両runの5 source pin、各2 WAP buildの入力測定・SQL、各7 testのSQL ASTを照合し、保存Snapshotの再利用を確認 |
| 実TableとDB分離 | DB BはBETAの2行へ置換され、追記の重複なし。失敗したDB Aは従来のTable Ref、Snapshot一覧、ALPHAの値を保持 |
| 公開権限とgate | Eの公開、失敗testの公開、旧WAPからの迂回、未承認path入力を拒否。保存admin planは同actorのE上限でもGET／listへ露出しない |
| 受付の再要求 | 完了run／publicationを元key／bodyで再要求して同じIDを取得。child operationリンクは増えない |
| cold compileと取消 | replicas=0でcompileしてもWakeなし。別HTTP processから取消し、run=CANCELED。leafはFAILED、backend query IDなし、親はFINISHED。SQL POST前の確定中断である |
| 権限失効 | cold runを失効させ、`DB_ACCESS_REVOKED`でSQL送信前に停止。通常GETは拒否、元owner／保存contextのcleanup取消はCANCELED。元E権限へ復元 |
| owner停止・再起動 | 実backend ID確定後に専用native ownerだけを停止。再起動後はUNKNOWNを保持し、新SQLなし。継続中だった専用queryをoperatorが明示DELETEし、既存queryのGET照合でrunをFAILEDとして閉鎖。候補構築やtestの続きを自動開始しない |
| commit後の記帳失敗 | 隔離Workbench processで1候補のartifact記録を一度だけ失敗させた。実Tableの2行／値は反映済み、DataOps committed=true、publicationはCOMMITTED_RECORDING_FAILED。再公開要求を拒否しchild増加なし |
| 監査 | Bのadmin scopeでdbt metadata、詳細と関連先を確認。同DBに限定され、SQL／private fieldなし。別DBからの詳細参照はNOT_FOUND |

故障の注入はprivateな受入launcherだけで行った。応答喪失と記帳失敗を製品設定へ追加していない。owner停止後の実行中queryを自動取消できたとは扱わず、元pinを失った場合にoperator cleanupが必要な境界を記録する。

## 実ブラウザー

通常の変換画面からEでproject／Schema／ephemeralを指定し、compile、候補構築、7/7 test成功を確認した。Aに切り替えてEの保存実行を履歴から参照し、実際のFQN・候補Snapshot・WAP版を確認して公開した。Eの公開操作は無効、閲覧者への切替ではSQL・artifact・認可inventoryを消去する。

compile応答を保存後に切断し、画面の元要求キー保持、reload時の自動POSTなし、明示的な同一key回収、元plan IDへの復帰を確認した。実画面で見つけたIdempotency-Keyの不一致は修正し、backendの実adapter契約を参照する回帰を追加した。日英切替、Tabによる公開確認操作、390pxでの水平overflowなしも確認した。

受入launcher停止後の通常8888で、直接リンクから同じplan／runを復元し、reload後も保存されたテスト成功・公開状態を参照できた。別processで記録したCOMMITTED_RECORDING_FAILEDも正しく表示した。この確認中のdbt POSTは0、受付記録とchildリンクの件数も不変だった。最終修正後の閲覧者切替ではadminのDB／Schema選択肢も消去される。390pxの一時viewportは解除し、通常画面を残した。

## コード検証と環境保全

`go test ./...`、`go vet ./...`、CLI build、`make verify-design-context`、`git diff --check`は成功。Python全体回帰は83 recipe、3,207件中3,174成功・33既存skip・失敗0だった。

初回`make verify-local-experiment`は成功37群の後、歴史的schema fixtureの6件で停止した。fixture修正後に失敗recipeから残る46群を再開し、すべて成功した。さらに画面修正後のWorkbench全体566件を成功させ、旧563件の群を一度だけ置換した集計である。最初の失敗群、重複run、workerのfocused検証は合計へ加えず、初回のmake自体がexit 0だったとは扱わない。

受入証拠はprivateな`var/shirokuma/db-p6-3b-2026-10-29/private`へ保存した。SQL、認証情報、実機の詳細logを公開文書へ転記しない。既存2件のFENCED_READ_ONLYなUNKNOWNは履歴を保持した。受入用project・故障serverは停止し、通常8888／8080を既定`lake_marts`だけへ戻した。臨時grantは通常の変更履歴を残して元のeffective権限へ復元し、認証更新COMPLETEとapplied revisionを確認した。

既存141 DB／564 Schema／各141件のcatalog・logical bindingは全row比較で不変、元のサンプル2行も一致した。13 WorksheetのID・選択状態と選択中SQL／Database／Schema／role／modeをDOMで照合した。runtime profileのbytes、Auto-Chill 0分・revision 5を保持し、共有LOCALはengine検証済み2/2、活動中admission・未閉鎖の新UNKNOWN・保留creationは0。既存のhost固有YAML 2件はコミットへ含めない。

## 残件

DB-P6-3Bのtable／ephemeral SQL対応と、DB-P6-3全体、M1本人受入を分ける。標準dbt executor／view等は次の互換性unitで可否・対象materialization・型付き操作を先に確定する。現在のview既定を無条件に対応済みへ変えない。Bears／Copilot、SRE／Policy、残る探索操作、実サイズ変更と用途別Warehouseは各後続unitへ残す。
