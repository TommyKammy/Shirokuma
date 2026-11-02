# DB-P6-6E: Tableのカタログ解除（データ保持）

2026-11-02着手、2026-11-03 JSTに限定技術受入を完了した。EXP-11の削除方式を明示し、通常Explorerから利用できるようにした。DB-P6全体とM1本人受入は継続する。

## 契約

- 操作名は「カタログ解除・データ保持」。TABLE全体をカタログから外す操作で、選択Snapshotだけの削除ではない。物理データpurge、VIEW削除、cascade、復元は別範囲とする。
- 現在のAと実効DB管理権限（DB grantまたは既存の認証済みP管理者例外）、正確なfull9、登録済みraw / sandbox / staging / marts（sql_name == purpose）のquery / write / adminを確認する。NULLのgrant revisionだけを管理権限の証明にはしない。現在の登録TABLE Ref / UUID / incarnationを照合し、DBやschemaを名前から補完しない。DataOps所有のstage / backupは対象外とする。
- 読取りpreviewで対象・方式・head / schemaを確認し、正確な対象名と確認内容、元keyで実行する。直前に再検査し、専用transportからpurgeRequested=falseのDELETEを一度だけ送る。通常SQLのDROP拒否と既存procedure権限は維持する。
- 送信intentとACKを既存DataOps台帳へ保持する。正のACKだけをcommitとし、元RefだけをCASで非currentにする。履歴・定義・data filesは保持する。応答喪失とowner lossはUNKNOWNであり、不在404を成功の証明にしない。既存要求の同key再送は照会だけとし、自動再実行しない。
- 既存SQL・VIEW・Job等の参照を自動更新・停止しない。保存lineageの空表示から依存不存在を判断しない。不変Refを持つ機能は古いRefを拒否し、SQL名による後続解決はその機能の既存契約に従う。
- 完了後やUNKNOWNの照会は保存owner / role / 現在contextで認可し、削除対象が現存することを要求しない。操作IDを受け取れなかった場合は元key専用のGETで既存要求を探す。このGETは未受付の解除を開始せず、404も初回POSTの未到着を証明しないため不明要求を保留する。取消で送信済みDELETEの無実行や巻戻しを主張しない。

共有SQL / DataOpsの既定動作は変更していない。REST-onlyの確定ACK・元Ref retirement・parent・query不在を純読取りで検証し、DB利用終了の閉鎖判定には同DBの正しいDropだけを認める。generic DataOpsの観測UIは変更せず、ID未受領時のGET復旧はDrop専用とする。

Iceberg RESTのDELETEには期待UUIDによる条件付き削除がない。直前検査とアプリ内claimは、任意の外部DDLとの原子的な排他を保証しない。別経路での同時作成・置換を止めて実行する運用条件を示す。

## 現環境と方式の根拠

稼働するlogical Polaris 1.8.0では、A / Bのcatalog propertiesとfeature overrideにpurge許可がなく、同版のdefaultはfalseである。今回、設定を変更せず、advertiseされた非purge REST DELETEを用いる。

通常のTrino DROPと非purgeの登録解除は別の経路である。[Trino 483実装](https://github.com/trinodb/trino/blob/483/plugin/trino-iceberg/src/main/java/io/trino/plugin/iceberg/catalog/rest/TrinoRestCatalog.java)、[Iceberg REST契約](https://github.com/apache/iceberg/blob/apache-iceberg-1.10.1/open-api/rest-catalog-open-api.yaml)、[Polaris 1.8.0 default定義](https://github.com/apache/polaris/blob/apache-polaris-1.8.0/polaris-core/src/main/java/org/apache/polaris/core/config/FeatureConfiguration.java#L393-L402)。

## 検証

| 層 | 結果 |
| --- | --- |
| 全体回帰 | P / key修正前のcanonical 112群、4607 tests中4573成功 / 34 skip。その後、実画面で見つけたPのcanonical NULLと読取り専用key復旧を修正し、影響する最終backend 40、retirement 28、DataOps routes 23、全Workbench 1083をホストで再実行して全成功。SDK service 40も成功 |
| 設計整合 | make verify-design-context成功（94 documents / 10 issues / 42 references）、diff --check成功。機能表89行のうち変更はEXP-11だけで、他88行は保持 |
| 認可・protocol | preview 27 GET（200×8 / 403×6 / 409×6 / 400×6 / 404×1）。真Pの正規NULLは許可し、非Pの偽造NULL・E/V、別DB / schema、古いRef / context、VIEW / owned stage、method / framing等を拒否 |
| 実登録解除 | A SANDBOX・B SANDBOX・A RAWをAPI、別A SANDBOXを通常画面から解除。4件すべてACK204 / FINISHED / committed=true / 旧Ref current=0 / pure closure PROVEN。DropのSQL子処理は0 |
| 元key・再作成 | 両HTTP入口のtyped / generic GET、同key再送・異body conflict、role拒否を確認。Aの同名Tableを一度だけ再作成し、新UUID / incarnation 2 / 別location / 2行を確認。旧Drop keyの再送後も新Refと全文metadataを保持 |
| ID未受領の復旧 | by-key 16 GET：既存3 key×両入口200、未使用key×両入口404、role / context / parameter拒否。全台帳・schema差分0。応答喪失・遅延初回POST・owner loss・取消競合はコード検証で、UNKNOWNから成功や無送信を推定しない |
| データ保持 | 旧4 location prefixに対するLIST4・既知22 keysのHEADを解除前 / API後 / 同名再作成後 / UI後で比較。metadata / manifest / data fileのkey・length・ETag・mtimeが全件一致。DeleteObject / data download / purge要求は0 |
| 通常画面 | 正確な名前と確認checkboxでのみ実行。誤名は無効、preview更新で確認をreset。完了後に旧オブジェクトと専用操作を無効化し、対象未選択のreload後も本人GETで同じ完了・commit・ACKを復旧。汎用の新規空Worksheet作成は既存契約どおり |
| 表示 | 日英390pxの解除領域346px / scroll346px、実Chrome 200%の領域716px / scroll716pxを確認。英語page全体には別領域による412pxの横溢れが残り、画面全体の狭幅受入とはしない。識別用JSON / digestは既定で折り畳む |
| 保全 | 最終active / default storeは12234 / 156行。元12177行と全schema、保護12files / modes、元source全文metadata、UNKNOWN query 3 / admission 3 / AI 6 / dbt 1を完全保持。実行中は0。元の19 Worksheet、SQL、A / read-only / LOCAL / max1000、日本語100%を復元 |

実API harnessの有限検証は89 HTTP（preview27、Drop / 再送 / 再作成45、by-key16、UI結果の読取り照合1）。ブラウザー・発見・metadata / S3交通はこの件数に含めない。正常なfixture用SQLは6（clone_create4 / create1 / ingest_rows1）、別途SDK startup readiness SELECTを2件計測した。通常serverの3回×2process起動にも既存health処理があるため、全engine SQL総数をこの8件とは断定しない。

初回の検証用RAW SDKはProvider未初期化によりSQL送信前に失敗した。元key / parent / FAILED child（backend IDなし）を保存し、既存のowner-loss観測とfailure reconciliationで同じ親をFAILED確定した。この検証経路が無効化したreadiness proofだけを、現generation / epoch / engine状態を確認して元bytes / modeへ復旧した。正しい通常compositionで確認後に、別の明示intentとしてRAWを作成した。失敗record1は成功SQL6と分けて保持する。

証拠は`var/shirokuma/db-p6-6e-table-drop-2026-11-02/private/`に保持した。`final-comparison-summary.json`、`final-footprint-and-case-counts.json`、`final-browser-done-storage-summary.json`、最終host test logsと通常画面の画像を対応付けている。

89能力IDと他88行の意味を維持する。次はComputeの実使用量・容量・活動 / 起動履歴・停止理由・現在権限を限定単位で進める。実CPU / memory変更とworker伸縮はM1後のDB-P7のままである。
