# DB-P6-3B dbt SQL候補の実行・テスト・公開契約

2026年10月29日。table／ephemeral SQL対応範囲の[実装・技術受入完了](database-p6-3b-implementation-2026-10-29.md)。[先行実証](database-p6-3a-dbt-feasibility-2026-10-29.md)のoffline compileと既存WAPを、耐久履歴・通常API／CLI／Workbenchへ接続した。標準dbt build executor、view、table properties等のmaterialization固有処理は未対応として明示する。

## 対象と権限

サーバーが承認済みproject IDをローカルdirectoryへ解決する。既定は同梱の`lake_marts`で、operator設定による追加projectにも同じ一般処理を適用する。公開入力に任意path・資格情報・任意DDLを受け付けない。sourceは指定した一つの登録Schema、候補は同じDBのSANDBOX、公開先も同じDBの明示Schema／Tableである。

compile・候補構築・testは現在のdata_engineer／admin、候補の明示公開は現在のadminを必要とする。閲覧者へsource SQLや実行権限を開放しない。Eが作った候補をAが公開する場合は、A自身の新しい親admissionと資格情報を使い、親子actor一致条件を維持する。

## 通常API

各routeはDataOpsと同じ完全な`context`を受ける。GETはJSON query parameter、POSTはbodyで受け、POSTにIdempotency-Keyを必須とする。受付を保存してからworkerを開始し、同じkey／intentの再要求は元receiptを返す。GET・再起動から新しいSQLやcompileを開始しない。

| route | 応答／処理 |
| --- | --- |
| `GET /api/v1/dbt/projects` | 許可されたprojectのID・名前・対応範囲。private pathは返さない |
| `POST /api/v1/dbt/plans` | context、project_id、source_schema、target_schema、staging_materializationを固定し、compile受付を返す |
| `GET /api/v1/dbt/plans`、`GET .../plans/{id}` | 保存compile状態・project版・model SQL・refs／sources／依存関係の参照 |
| `POST /api/v1/dbt/runs` | contextとplan_idで候補構築・全testを受け付ける |
| `GET /api/v1/dbt/runs`、`GET .../runs/{id}` | 元DB／role、model／test状態、候補Ref／Snapshot、関連operationの参照 |
| `POST .../runs/{id}/cancel` | 先に取消を保存し、新childの受付を止めて既存childを取消 |
| `POST .../runs/{id}/reconcile` | 既存childの状態とcommit証拠を照合する。未実行stepは開始しない |
| `POST .../runs/{id}/publish` | contextとnode_idで、指定modelだけの監査・明示公開を受け付ける |
| `GET /api/v1/dbt/publications/{id}` | 管理者自身の公開処理・commitと記帳の状態を照会 |

envelopeは`context`と`projects`／`items`／`plan`／`run`／`publication`を返す。保存artifactはid、kind、actor、context、state、project_id／name、source_digest、時刻、error_codeを持つ。planのmodelsはunique_id、name、materialized、relation、depends_on、compiled_sql、testsはunique_id、name、test_type、depends_onを持つ。runはplan_id、modelごとのWAP版・build・候補Ref／Snapshot、testごとのfailure_countと状態、関連operation IDを持つ。private credential ref・frozen header・raw logは公開projectionから除く。

planはQUEUED／COMPILING／READY／FAILED、runはQUEUED／BUILDING／TESTING／READY／FAILED／CANCELING／CANCELED／UNKNOWN、publicationはQUEUED／AUDITING／PUBLISHING／PUBLISHED／FAILED／CANCELING／CANCELED／UNKNOWN／COMMITTED_RECORDING_FAILEDを区別する。runのREADYは全test成功・公開待ちであり、公開済みとは表示しない。

## 永続化とSQLの境界

既存Martと同じJSON記録・個別owner leaseを使う。dbtのplan／run／publicationは区別した記録として保存し、DataOps childとrunのリンクはchild admissionと同じtransactionで確定する。新しいサービスや汎用workflow executorは追加しない。

compileが実際に読んだsource bytesから版を確定し、不変manifestを保存する。run開始時に全sourceのTable Ref・Snapshot・schemaを一度固定し、各modelとtestが同じ測定を使う。候補も構築後のRef／SnapshotとWAP版を保存する。SQLは既存WAP compilerとtyped DataOps childへ渡し、任意のdbt DDLを直接実行しない。

testは保存済みの既知nodeだけを型付きchildで実行し、Snapshot固定SELECTのfailure countを保存する。公開gateは必要なtest ID集合の完全一致・全件pass・取消／UNKNOWNなし・元plan版と候補Ref／Snapshot／WAP版の一致を必要とする。dbt管理下のWAP artifactを旧WAP公開routeへ渡しても、このgateを迂回できない。通常の独立した手動WAPは維持する。

公開はmodel単位で、複数Tableの原子性を約束しない。各commitを独立に保存する。公開commit後の記帳失敗はCOMMITTED_RECORDING_FAILEDとして実commitと区別し、自動再公開しない。管理者権限があることだけでtest gateを省略しない。

## 取消・失効・中断と画面

親admissionはcompile後から候補構築・testの間を保護し、READYで終了する。明示公開には別の親admissionを置き、全phase gapを保護する。現在権限をchild受付とSQL送信直前に再確認する。取消・失効・owner喪失後に新stepを始めず、不確実なSQLを再送しない。GETは既存記録・queryの照合だけを行う。

WorkbenchにはWorksheetと独立したDB・SANDBOX・保存role・project選択を置く。承認済projectのcompile、model SQL／lineage、候補構築とtest、履歴、取消、model単位の明示公開を表示する。古いDB／roleへの応答を別contextへ適用せず、lost ACKは同じkeyで元receiptを照合する。日本語・英語、reload、deep link、keyboard、390pxで通常操作と理由を確認する。

## 受入

2DBの同名model・複数modelの構築／全test・実Table／値／lineage、失敗test・取消・失効・中断時の公開先保全、成功候補の明示公開、直接WAP routeからのgate迂回拒否を確認する。コード回帰、実API／CLI、実ブラウザー、実機の結果を分ける。既存DB・台帳・データ・worksheetとhost固有設定を保持する。DB-P6-3全体とM1の本人受入は別に追跡する。
