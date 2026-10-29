# DB-P6-2 共有LOCALの実装契約

2026年10月29日。実装・技術受入済み。結果と検証範囲は[実装記録](database-p6-2-implementation-2026-10-29.md)に記す。[後続計画](database-full-capability-and-compute-plan-2026-10-28.md)の共有Warehouse休止・自動復帰を、登録済み`logical_v1`基盤の固定LOCALへ接続する。XS／Sの実サイズ変更と複数Warehouseは後続trackとし、M1の本人受入は別に残す。

## 権限と公開API

状態参照は現在の認証と選択roleの範囲内で提供する。手動Wake／Chill／復旧とAuto-Chill設定は、現在の実accountのplatform権限、最大admin、選択adminの全条件を必要とする。利用者の自動Wakeは元の許可済みSQL intentに付随し、platform権限を付与しない。認可拒否、metadata GET、モデル保存だけでは起動しない。

| route | 契約 |
| --- | --- |
| `GET /api/v1/warehouses/LOCAL/runtime` | 共有状態、観測時刻、engine検証状態、policy版、公開可能なblocker件数、利用可能操作。物理変更しない |
| `POST .../runtime/wake`、`chill` | P専用。空body、Idempotency-Key必須。202の操作IDは受付であり完了ではない |
| `POST .../runtime/reconcile` | P専用。中断した基盤操作の現在状態を明示照会・検証する。SQLは再送しない |
| `POST .../runtime/auto-chill` | P専用。`minutes`（0〜1440）と`expected_revision`、Idempotency-Key。保存済みpolicyと入力中の値を区別 |
| `GET .../runtime/operations/{id}` | P専用の操作metadata。actor、action、phase、時刻、固定error code。SQL・資格情報・private pathを返さない |

手動操作の同じkeyと同じintentは同じ受付へ戻り、keyの異なるintentへの流用は拒否する。通常CLIは`shirokuma warehouse runtime`配下のHTTP clientとし、従来の直接Python運用commandと混同しない。

## 状態と実行owner

物理Deploymentの状態と、受付済み／起動中／停止中／失敗／状態不明の操作を分ける。「利用可能」は実engine・catalog／秘密参照の既存proofを検証した結果と観測時刻を伴う。replica readyだけを実SQL準備完了と表示しない。共有操作は全DBへ影響することを操作前に示す。

既存server process内の実行と、固定LOCAL用の単純なcross-process lockへ集約する。新しいservice／supervisorは追加しない。既存operationsとwarehouse_admissionsを再利用し、外部待ちの全期間をSQLite write transactionで囲まない。独立したWake admissionが起動中を保護し、あるqueryの取消は他利用者も待つ共有Wake全体の取消とは扱わない。

操作owner喪失を成功扱いせず、未確認操作を自動再実行しない。実状態の照会・明示的な復旧と、新しいSQL intentの送信を分ける。以前のquery／DataOpsのUNKNOWNや失われたresponseをWakeによって再送しない。

## SQLの自動復帰

Workbench SQL、通常CLI、native endpoint、Job、Stage、Mart、予定実行の実装済みSQLは、既存query_serviceのpublic submit／typed DataOps childへ集約されている。そこへ、認証・元context／SQL policy確認・durable admission／intent保存の後、tunnel取得前のWakeを追加する。起動後と最終初回POST直前にも最新認可を確認する。未接続のdbt／Bearsをこのunitで開放しない。

warm経路を維持し、cold経路では元query IDとcontextを固定したQUEUED応答を返す。queryごとに初回実行workerは一つだけとし、GET／poll／再起動から再生成しない。pending page tokenは進行させ、各tokenの再取得は同じ応答にする。起動待ち中にqueryを取消した場合は、元SQLを送らずCANCELEDを永続化する。初回POSTを試みた後の不確実性には既存の保守的なUNKNOWN／回復規則を維持する。

「一度だけ」はこの受理済みintentの初回POSTを指す。現在のrequest_idは相関IDであり、異なるclient POSTの一般的な冪等性とは表示しない。

## Auto-ChillとDB作成

monitorは既存Workbench／native serverのlifecycleに置き、cross-process lockを持つ単一ownerだけが実行する。待機側はowner終了後に引き継ぎ、画面の有無に依存させない。policyが0のときも後の設定変更を観測できる。設定変更自体と物理停止を分け、実行中でもPが無効化できる。

停止前に、global admission／drain、実行中・待機中query、受理済みJob／DataOps／Stage／Mart、Wake、未解決UNKNOWN、保守処理と実engineのactive queryを検査する。最後のHTTPアクセス時刻だけでは判断しない。観測に失敗した場合は停止しない。

実engineの補助診断は、既存provisionerのsystem information読取り権限とstrict TLSで固定`GET /metrics`を利用する。Trino 483のDispatchManagerが公開するQueryTrackerの`AllQueriesCount - ExpiredQueriesCount`で全未完了stateを数える。Running等4stateの和ではPLANNING／STARTING等を落とすため代用しない。欠落・重複・非数・負数・非整数・大小矛盾は観測失敗とする。raw scrapeや資格情報を公開せず、SQL table権限を追加しない。[DispatchManager](https://raw.githubusercontent.com/trinodb/trino/483/core/trino-main/src/main/java/io/trino/dispatcher/DispatchManager.java)、[QueryTracker](https://raw.githubusercontent.com/trinodb/trino/483/core/trino-main/src/main/java/io/trino/execution/QueryTracker.java)、[management endpointの分類](https://raw.githubusercontent.com/trinodb/trino/483/core/trino-main/src/main/java/io/trino/server/security/ServerSecurityModule.java)

logical作成のhealth確認からroot／request commitまでshared lifecycle gateで守る。停止側は同じexclusive gate内で、current environment／Warehouseの未完了logical request／effect／root phaseを検査する。作成・認証refreshのphase gapも保護する。休止中の正規AUTH_REFRESHはmetadataとして待機させ、Wake後の完全bindingを経て既存経路で処理する。UNKNOWN効果の再送は行わない。

## 休止中のserver起動と接続

停止中のcontrol-plane bindingと、稼働中の完全bindingを区別する。休止中は保存済みtrust、stable pins、storage、SPI／Deployment specと正確な0／0を検証し、Providerを未open・未検証のまま管理APIを提供する。SQL準備完了やprovider mutation権限を作らない。部分停止やproof不一致を休止中へ丸めない。

Wake後は各server process自身のowned tunnelを再確立し、既存の完全probeと保存proofの比較を通す。新観測UIDを保存proofに採用しない。SQL用tunnelも初回POST前に再確立し、失敗したPOSTをtunnel再接続のために再送しない。

## 実clientと検証

導入済み公式Python Trino DB-API driver 0.339.0をnative clientとして選ぶ。既存loopback HTTPへ正式なhttp_sessionで現在の認証を設定し、max_attempts=1と明示timeoutを使う。空session／NONE transactionという無操作headerだけを許容し、非空session、実transaction、role／impersonationの禁止は維持する。infoUri、安定した公開query ID、poll、204 cancel応答を実clientと整合させる。全ODBC／JDBC製品の認定とはしない。

focused回帰、全体回帰、実API／CLI、実ブラウザー、実機の0／0→1／N→SQLを分けて記録する。同時Wakeのscale一回、拒否要求のscaleゼロ、初回SQL二重送信ゼロ、取消・失効・UNKNOWN・長時間処理・DB作成競合を確認する。休止中のserver再起動、復帰後のSQL／新規DB作成も扱う。

cold SELECT 1とwarm追加overheadは各30回・同条件で計測する。元計画のcold p95 30秒／warm追加p95 250msを目標として維持し、測定結果と実装完了・技術受入・M1本人受入を区別する。
