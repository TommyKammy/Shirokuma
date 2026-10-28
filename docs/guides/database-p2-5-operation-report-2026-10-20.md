# Phase 2 P2-5: plan / applyとOperation

**状態: P2-5限定受入済み。** 対象は管理Operation、明示apply、全入口のadmission / drain、process間排他、Secret版変更時の再plan、UNKNOWN保持と明示照合です。P2-6の新Database作成・3DBの縦断受入、既存テストデータの移行・保全は対象にしません。

## 実装の境界

SQLiteにWarehouseの受付状態と操作全体のtokenを保存します。queryのページ間、Jobのstep間、Stage claimから記帳までの間も追跡し、backend queryが一時的に0件という理由でapplyを許可しません。drain待機ではSQLite transactionやexclusive flockを保持せず、元queryのGET / cancelと受理済み処理を進めます。

公開planはP2-4のbundle・registry revision・Secretの観測descriptorを固定します。秘密値はprivate operator側に置きます。HTTPは認可した永続intentと202 handoffを返し、実行は単発のprivate operatorが行います。固定stepの送信前intentと成功済み結果を保持し、応答が失われたstepはUNKNOWNのまま照合を要求します。

## 実機検証の区分

現在のP2-3固定2DBはSALES=`iceberg / shirokuma_l1`、FINANCE=`finance / finance`です。P2-4候補の新SQL名・`db_<ID>`・prefixを、この現構成の無変更再適用とは扱えません。今回の受入は次を分けて記録します。

- 現2DBの実Trino / 通常HTTP入口による受付・継続・取消・応答喪失と照合。
- 独立したSQLiteと別processによるplan / apply / gate / deadline / UNKNOWNの競合検証。
- 同じtyped operator実装による候補の非active prefixへの配送、既存Flux childの限定制御、現構成のsaved no-op ToFu planの適用と観測。
- 新しい候補全体の切替とDB作成からREADYまでの縦断はP2-6へ残す。限定step検証を候補DatabaseのREADYの証拠にはしない。

## OpenTofu stateの事前確認

実stateは `var/shirokuma/polaris-adoption/terraform.tfstate`、開始serialは8です。現namespace UIDとの一致を確認しました。別のrebuild stateは現在のnamespaceと一致しません。

stateを省略した最初のread-only planは16 CREATEとなり、適用せず `rejected-missing-state.*` に隔離しました。正しいstateを明示したsaved planは16件すべてno-op、既存PG resource addressのmoved 1件だけです。operatorはstate path・lineage・serial・接続先を固定して使います。

P2-4で比較していたmodule既定state pathは実在せず、当時の「実stateのhash不変」という記載は不正確でした。稼働workload / profileの比較と、stateを適用していない事実は有効ですが、実stateの直接比較証拠とは区別して訂正しました。

## 実装した契約

control_storeはschema v3です。Warehouse gate、root / child admission、固定plan、冪等要求、step intent / evidence、HTTP handoffを同じSQLiteで扱います。受付とdrainは`BEGIN IMMEDIATE`で競合を判定します。固定stepは`publish_snapshot → apply_secrets → reconcile_storage → reconcile_polaris → reconcile_trino → verify`です。公開snapshotのuploadだけは受付停止前に許可し、Bucketやchildの参照先は変更しません。

applyと照合はWarehouse共通のoperator flockで排他します。applyの外部変更はdrain完了後に期限付きlifecycle exclusive flockを取って行います。照合はoperator / lifecycle exclusive flockの内側で観測・private receipt更新を行います。receiptは設定ファイルの置場ではなく、共通state directoryの`warehouse-operator-receipts/<operation ID>.json`に一つだけ置き、別config directoryから同じOperationへ異なるprivate材料を結び付けられません。

planは生成物のbyte / digest、全DBのbinding・Schema・grant revision、profile、Secretのnamespace / name / version / keysetとUID / resourceVersion / immutableを固定します。applyはactorの現在の認可、expected revision、配送digest、実観測を再照合します。自身のSecret作成によるABSENT→PRESENTや正しいmetadata更新は成功receiptに基づき一度だけ取り込み、それ以後の変化には再planを要求します。適用後のregistry変更が確定した場合は`VERIFIED_STALE`とし、DBをREADYにせず新planへ渡します。結果不明の場合はUNKNOWNと閉じたgateを保持します。

SQLには送信前のローカルquery IDから固有sourceを付けます。query ownerのOS lockは最後のpageまたは確定cancelまで保持します。別processがin-memory pinを持たないという理由では、未取得pageのあるqueryを解放できません。owner喪失や応答喪失後は、元backend user・SQL・source・query IDとGETによるbackend終端証拠を照合します。not found、PID、TTLだけでは未実行と判断しません。legacyのPOSTも自動再送せず、id / statsが欠けた応答を成功扱いしません。

## 実機結果

次の95項目は、通常HTTP / 実backendと、隔離ledgerでの実process事故を区別して記録しました。

| 検証 | 結果・証拠の範囲 |
| - | - |
| 実複数process・隔離SQLite | 35/35成功。planだけでは未送信、明示apply、同key競合、drain timeout後の再開、applyとapply / reconcileの排他。witness fsync直後にSIGKILLし、RUNNING / UNKNOWN保持→明示照合→未完stepだけ再開。各固定stepの送信countは1。backend設定の完了証明には使わない |
| 実Secret metadata | 15/15成功。実Kubernetesの一時SecretでABSENT→PRESENTを観測。古いplanのapplyは`STALE_SECRETS`、外部callback 0回、gate OPEN、applied不更新。同じ世代は409、新世代で再plan可。一時SecretはUID条件付きで削除済み |
| 通常Workbench / SQL endpoint | 18/18成功。queryのpage間token、両入口の新SQL503、継続GETとcancel、0 queryでも勝手にgateを開けないことを確認。既存Job関数と直接chillもSQL / scale前に503。別Python processによる生存queryの早期解放を拒否 |
| 実Trino応答喪失 | 8/8成功。実POST応答をアプリへ渡さずUNKNOWNを永続化。pinを失った後、元本人のGET照合で実queryのFINISHEDを確認してtokenを解放。adminをviewerへ降格した別owner照合は404。POSTは1回 |
| 管理HTTP・process再起動（隔離state） | 10/10成功。実認証付きplan、非実行、明示applyの202 handoff、再起動後の同request ID、異payload409、降格時403、他owner404、本人の降格後inspect。HTTPから外部applyは起動しない |
| 実S3 / Flux childの限定leaf | 5/5成功。2 DB候補の公開fileを非active prefixへuploadして全byteをGET照合。Bucket未切替。既存object-storage childのUIDと他specを維持してsuspend / restore。候補全体をAPPLIEDとは判定しない |
| 実OpenTofuの限定leaf | 4/4成功。正しい実stateによるsaved plan 16 no-opを、通常HTTPを閉じた状態で同じ固定実行leafから適用。PG addressのmovedを反映しserial 8→9。fresh planもインフラ差分0。P2-4候補のSecret承認・全step適用の証明とは区別 |

S3 / Flux / ToFuの限定leafは同じ内部固定関数を直接呼びました。full Operatorのprivate材料検証・plan承認・全構成verifyを通した実機成功には数えません。そのguardは集中テストで検証し、実配置を含む縦断はP2-6へ残します。

5 workload specとactive runtime profileのhashは開始時と一致しました。ToFu stateだけは上記address移動により意図どおり変化しました。doctorは`local-experiment` profileで`2026-10-19T23:09:14Z`にhealthyです。通常Workbench / endpointは最終ソースとschema v3で再起動済みで、SALES / FINANCEのSQL疎通、OPEN / blocker0も確認しました。

## 入口と運用

SQL、Job全step、schedule firingからJob完了まで、compound Ingest / Export / Clone / WAP、dbt、seed、sync、DR、wake / chill / lab lifecycle、既存GitOps変更経路を共通受付へ接続しました。UNKNOWNの子が残る親は終端にしません。scheduleはmaintenance_skippedを記録し、onceを無効化、interval / calendarを次回へ進め、catch-upしません。DB一般化が未対応の通常v2 Jobs / DataOps routeは従来どおり501で明示拒否します。新DB向けの機能成功を主張するものではありません。

管理APIは`POST /api/v1/warehouses/LOCAL/plans`、`GET /api/v1/operations/<ID>`、`POST .../apply`、`POST .../reconcile`です。書込みには現在のplatform adminと選択role admin、Idempotency-Keyを要求します。apply bodyはexpected_revision / delivery_digest、reconcile bodyはexpected_revisionだけです。クライアントからpath・Secret値・自己申告の成功証拠を受け付けません。

HTTPのplanはoperatorが明示した`SHIROKUMA_PUBLIC_BUNDLE_ROOT`と`SHIROKUMA_PUBLIC_OBSERVATIONS_ROOT`からgeneration IDだけで参照します。管理HTTPの受入はこれらを明示した隔離stateで行い、通常P2-3 runtimeへ候補世代を登録・切替していません。

実行は`catalog_admin.py`または`shirokuma database`の単発operatorで行います。plan / apply / reconcileには、bundle、snapshot、saved ToFu plan、実state path / lineage / serial、専用context、S3資格情報、本人認証header、検証用のDB別資格情報を明示したprivate configが必要です。0700 directory / 0600 fileを要求し、既定stateや既定contextへfallbackしません。inspectとquery照合は現在の本人認証headerだけで使えます。generation / operator機能にはPyYAML / boto3を含むPython環境が必要で、今回の実機では既存の検証venvを使用しました。通常Workbench・旧routeの起動にこれらを必須化していません。

UNKNOWNは固定stepのack、実際のsnapshot / Secret / childの観測、保存済みverify成功証拠を使って照合します。verifyを照合するためにmetadata SQLを再POSTしません。保存証拠や現構成のGET観測が不足する場合はUNKNOWNを維持します。無根拠の解除や成功済みstepの再送はできません。

## 回帰・レビューと残課題

`make verify-local-experiment`は973件中941成功 / 任意依存等32 skip、failure / error 0で成功しました。`make verify-design-context`の14試験、文書・改行・空白・Secret filename検査、`git diff --check`も成功しました。Goは製品範囲の`go test ./cmd/... ./internal/...`と`go vet ./cmd/... ./internal/...`に成功しました。checkout全体の`go test ./...`は既存の`tmp/rv/gomodcache`を拾って失敗するため、製品範囲の結果とは区別します。

core focusedは63件、入口の最終focusedは59件、管理operatorの最終追補は37件、管理HTTPは8件成功しました。Jobs全体は169成功 / 任意依存1 skip、Exportは47成功です。Python 3.9構文は変更42ファイル、公開対象525ファイルへの実資格情報16種類の混入は0でした。独立レビューで見つかったreceiptの排他外書込み、別configによる同Operationの材料差、別processによる生存query解放、HTTP status消失を修正しました。回帰検証で見つかったalertの既知ローカル記帳rollbackはFAILEDへ戻し、実UNKNOWN子は保持することを確認しました。legacy応答のfixtureは実Trino契約どおりのid / statsへ修正し、製品の厳密検査と既存assertionは維持しました。

P2-6には着手していません。現在のP2-3固定2DBから新しいbinding / SQL名への切替、Catalog / namespace / service principalの実作成、版付きSecretの実applyとbootstrap / fullの二段階適用、共有SeaweedFSを再起動する切替順序、任意名3番目DBのREADY・既存DB不変・再配備・空環境再現をP2-6で受入します。現operatorのverify入力は発行済みDB別SPとTrino identityを前提とするため、空DBのbootstrap→SP取得→full planへのhandoffは未接続です。今回の限定leaf成功を、その縦断完了や候補のAPPLIED / READYの根拠にはしません。UI、全DataOpsのDB一般化、DB削除・retireも未実装です。旧テストデータの移行・保全は行っていません。

証跡、private材料、ログはGit対象外の`var/shirokuma/p2-5-acceptance/`に保存しています。主な入口は`core-process-latest.json`、`secret-observation-latest.json`、`http-admission-results.json`、`sql-response-loss-results.json`、`management-http-results.json`、`operator-leaves-results.json`、`tofu-leaf-results.json`です。
