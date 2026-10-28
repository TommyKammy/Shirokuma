# Phase 4 実装・実機受入計画

2026年10月20日。データ管理アーキテクチャのPhase 4を対象とする。
DEV-04Fにある歴史的なPhase 4とは別の番号である。2026-10-20の作成時点では**計画・未実装**。
計画作成時の作業は文書と現行sourceの読取り確認で、製品実装・配備・grant変更を含めていない。

2026-10-21追記：P4-0開始確認とP4-1の限定技術受入を完了した。
結果は[P4-1受入報告](database-p4-1-grant-boundary-report-2026-10-21.md)。
同日、P4-2の表示metadata・Workbench管理・名前境界の限定技術受入を実施した。
結果と検証上の制約は[P4-2受入報告](database-p4-2-metadata-management-report-2026-10-21.md)。
P4-3の実機技術受入は完了した。[P4-3受入報告](database-p4-3-jobs-manual-adapter-report-2026-10-21.md)に
3DBのWorkbench/通常CLI新規実行、UNKNOWNの実行閉鎖、grant失効・backend検証後の再付与、旧context取得拒否、元SQLタブ保持、最終清掃・照合を記録した。
P4-3時点の最終generationは`p43-recovery-final`、LOCAL OPEN、未解決実行0。過去結果UNKNOWNの2件は`FENCED_READ_ONLY`を別記録して保持している。
2026-10-22、P4-4 ローカルStageの手動IngestとSnapshot固定Exportの限定技術受入を完了した。
[P4-4報告](database-p4-4-local-stage-report-2026-10-21.md)に、3DBのWorkbench/通常CLI、定義CAS・許可済みlocal root・Snapshot固定・grant失効・元SQLタブ保持・清掃・認証撤去後の復帰照合を記録した。
P4-4時点の最終generationは`p44-stage-final`、LOCAL OPEN、未解決実行0。
2026-10-22、P4-5 Stageの手動scan・内容dedupの限定技術受入を完了した。
[P4-5報告](database-p4-5-manual-stage-scan-dedup-report-2026-10-22.md)に、3DBの通常API/CLI・Workbench、競合・復旧、
grant失効のAPI/CLI・実GUI拒否、限定清掃、検証用認証の撤去、通常入口復帰と元2SQLタブの最終照合を記録した。
最終generation `p45-scan-final`はLOCAL OPEN revision 105、未解決実行0、過去の`FENCED_READ_ONLY` 2件を保持した。
実OPA 145件とProductionOperatorのidentity・権限・Schema用SQL probe 15件を確認した。
2026-10-22、P4-6 管理Martの手動全件更新・変更確認の限定技術受入を完了した。
[P4-6報告](database-p4-6-managed-marts-report-2026-10-22.md)に、P4-5 scan/dedup連携・3DBの通常API/CLIとWorkbench、
Snapshot固定・append/rollback・identity再作成・公開後照合・grant失効、有限清掃と認証撤去後の復帰を記録した。
最終generation `p46-mart-final`はLOCAL OPEN revision113。実OPA145件、backend SQL probe15件、
元2SQLタブと全59台帳の最終独立照合25/25を確認し、未解決実行0・過去FENCED_READ_ONLY 2件を保持した。
公開後3窓の元イベント記録欠落と既存scanner marker1件は報告書へ明記した。
2026-10-23、P4-7 予定実行・自動scan／更新・alertの限定技術受入を完了した。
[P4-7報告](database-p4-7-schedules-alerts-report-2026-10-23.md)に、3DBの自然発火・通常CLI／Workbench、予約・競合・定義変更、
alertとgrant失効・明示再開、実DRAIN、有限清掃と認証復元、元2SQLタブと59台帳の独立最終照合を記録した。
最終generation `p47-schedule-final`はLOCAL OPEN revision125。実OPA145件・SQL probe15件が成功し、
全19予定無効・実受付の未解決0、過去FENCED2件と未連結UNKNOWN予約1件を別に保持した。
2026-10-23、P4-8 非purge DB retireの限定技術受入を完了した。
[P4-8報告](database-p4-8-acceptance-2026-10-23.md)に、Workbenchでの退役要求・通常CLIの明示確定、
実行中/UNKNOWNの待機・内部継続・grant失効・元pin取消、権限拒否、対象予定停止と他DB保持を記録した。
専用DBはretired revision3、最終generation `p48-nonpurge-final`はLOCAL OPEN revision136。
実OPA186件・SQL probe18件、新旧full planの拒否/適用・再起動後のretired維持と物理データ/credential保持を確認した。
元16,380台帳行の欠落0、50セルの正常な世代metadata更新、認証3 files・既存client22 files・元2SQLタブの復帰を独立照合した。
2026-10-23、P4-9の実装・技術受入を完了した。
[P4-9報告](database-p4-9-acceptance-2026-10-23.md)に、既存・専用cleanの通常縦断各57段階、
自然予定9 graph、実UNKNOWN/grant失効回復、通常再起動・verified full・新規retireと物理保持を記録した。
最終generation `p49-final-persisted`はLOCAL OPEN revision155、元認証を復元し実行未解決0。
Workbenchの接続失敗表示の復帰も修正・実機確認した。変更後の全体検査はPython1,892 PASS/33 skip、Go・設計検査PASS。
元2SQLタブと認証を復元し、6 TableのUUID/Snapshot/型/行、権限と回復履歴、所有物清掃を最終照合した。
P2-R01〜03とP4-A01〜18の技術closeoutを同報告に記録した。
native quoted名とPolaris実名の下流差異は再照合し、mixed case/spaceのexact-name制限を残件として記録した。
Phase 5の所有者による操作性評価は別gateとして残る。
後続の[Database Phase 5実装・実機受入計画](database-phase5-implementation-plan-2026-10-23.md)に、
WebUIからDB登録・初期アクセス・bootstrap/fullの明示適用・READY化までの導線と、実機・所有者受入条件を定めた。
同計画は計画段階であり、P4の受入結果をP5の新規導線の受入へ読み替えない。
既存scannerの誤検知はP4-2報告に記録した。P4-2のOSロックで保留だったブラウザー作業も解除後に完了した。

## 到達点とPhase 3からの引継ぎ

任意のREADY DBで、通常Workbench / API / CLIから権限と表示名を管理し、
登録Job、ローカルStage、管理Martの手動操作から限定した定期実行まで運用できる状態を目指す。
SALES / FINANCE / 任意名DBの同名資源を分離し、失効・構成変更・応答喪失時にも
P3の本人/context固定、Snapshot、UNKNOWN、commit判定を維持する。

根拠は[Phase 2 closeout / Phase 3計画](database-phase2-closeout-phase3-plan-2026-10-20.md)と
[P3-5受入報告](database-p3-5-normal-entry-acceptance-report-2026-10-20.md)。
P3の通常入口1,289項目、全体1,241試験（32skip）、doctor healthyは**保存済み受入証拠**であり、
今回その実機試験を再実施した結果ではない。P3-1〜5の限定技術受入を出発点とし、
所有者の最終操作性評価・全体最終受入は引き続きPhase 5へ残す。

P3で保持したimmutable履歴・終端claimと、故障注入によるmodel照合markerを引き継ぐ。
実機の全体台帳ではP3-3 FINANCE2件とP3-5 SALES1件の計3件であり、P4-1/2でも保持を照合した。
markerを成功履歴へ書き換えたり、そのmodelを自動的に定期更新へ登録したりしない。
legacy SQLiteへの補助probeの注意点も保持し、P4のmigration・試験は明示profileのcontrol storeだけを開く。

| 残件 | Phase 4で閉じる条件 | 担当unit |
| - | - | - |
| P2-R01 通常grant変更API | desired/effectiveを分け、付与はbackend検証後、剥奪は即時制限。通常API/CLI/UI、CAS・監査・再plan・失効後の取得拒否を実機確認 | P4-1、P4-2の管理画面、P4-9 |
| P2-R02 表示名更新API | DB/Schema表示名更新を通常入口から実行し、SQL名・binding・接続先・Table identity・実データが不変 | P4-2、P4-9 |
| P2-R03 名前入力境界のcoverage | 入口×入力境界×期待副作用のmatrixを作り、有効な境界名の実作成/apply/SQL成功と不正入力の送信前拒否を対で実証 | P4-2で基礎を閉じ、後続unitごとに追加、P4-9で最終照合 |

P2-R04のprivate材料準備、bootstrap/fullの明示plan/apply、両入口の世代確認は運用手順として維持する。
管理画面の追加を、秘密値の自動作成や無条件applyの受入へ読み替えない。

## 固定する対象範囲

Phase 4へ含めるのは、grant管理、DB/Schema表示metadata、既存DB登録/plan/apply/statusの管理導線、
DB対応Job、ローカルStageの手動Ingest・全件Export・内容dedup、管理Martの全件更新・変更確認、
それらの既存schedulerによる予定実行とWorkbench内alert、最後に非purgeのDB retireである。
Schemaは現行の`raw / staging / marts / sandbox`とpurposeの固定profileを使う。

任意Schemaのprovisioning/profile変更、SQL catalog/Schemaの物理rename、Table表示名管理API、
物理DB DROP/purge・storage/PVC削除・credential撤去は別計画へ残す。
外部Stage、connector同期、AI/dbt/DR、外部BI driver認定、DAG/任意command実行、
CDC/増分Mart、cross-DB合成、外部メール/Slack通知、新schedulerサービスは含めない。
旧JSON・固定`iceberg`向け定義を新DBへ暗黙移行する機能も作らない。

既存guideのCompletedは旧実装の受入である。現sourceには
`jobs_resident.py`の固定catalog、`stages_resident.py`の名前ベースdedup、
`marts_resident.py`の固定source制限、`stage_exports.py`の旧Export経路が残る。
旧routeのgate解除やcatalog文字列の差替えだけではDB対応済みとしない。

## 共通契約

### 権限・identity・定義とrun

- DB/Schema/Object ID、実Table UUID/incarnation、DB内のStage/Job/Mart IDを正本とする。同名を別DBで使え、表示名変更でidentity・dedup・履歴の所属が変わらない。未解決の旧定義を既定DBへ割り当てない。
- 定義はowner、保存selected role、DB/Schema/Warehouse、version、source/target Refを持つ。各runはその意図を現在のeffective grantで再認可し、完全な9項目ResolvedQueryContext・入力bytes・model版・Snapshot・credential pinを受付時に固定する。定義の保存contextと、毎回の実行contextを混同しない。
- binding先やTable incarnationが変わった定義は明示更新を要求する。失効で停止した予定は、再付与だけで自動再開しない。進行中runのcontextを新しいepochや別roleへ差し替えない。
- 管理mutationは既存のplatform admin判定を継承する。grantはprincipalのglobal role ceilingやplatform_admin属性を変更しない。owner/roleを本文で偽装させず、保存と実行、設定の一覧とfile/result取得を分けて認可する。
- Jobsは現行どおりengineerの自分のSQL/読取alertを許可し、viewerのJobs APIは拒否する。file/WAP全更新/Stage Exportはadmin、StageとMartはadmin ownerに限定する。P3の手動Ingest/build/auditのengineer権限はそのまま維持する。

### 実行・再試行・運用

- Job/Mart全体が一つの親admissionをstep間も保持し、内部adapterだけが受入済み親を継承する。子SQL intent、DataOps operation、runの対応を保存する。外部clientへadmission IDを指定してguardを迂回するAPIを作らない。
- 受理済み親の内部後続step・状態照合・元pinでの取消はdrain中も進める。retiring時の利用者向け結果/artifact取得は拒否し、管理者の状態確認・reconcileは残す。新規要求は共通のmaintenance/競合契約で拒否する。UNKNOWNを無視して次step、retry、構成apply、Chillへ進めない。
- 同key/同payloadは同じoperation、異payloadは409。応答喪失・process再起動・PID/TTLだけで未実行判定しない。既知のcommitと清掃/記帳失敗を分け、artifact・dedup・更新基準を架空の成功へ進めない。
- 現行の同一state DB・単一ホストの全体1 Job実行制限、既存scheduler/tick、SQLiteを再利用する。待ち行列、新しい常駐worker、per-DB並列実行保証は追加しない。1件の不正定義でscheduler全体を停止させない。
- capabilityは機能・roleごとの実機受入後に有効化する。旧実装、未対応形式、View全件Export、Worksheet結果保存等は、そのunitの対象でなければ拒否を維持する。

## 実装順序

P4-0は開始確認。実装はP4-1から一単位ずつ進める。
基本順序は`1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9`。
P4-4のExportは手動Ingestの後、P4-5のdedupとP4-6の変更確認は手動実行で先に受け入れ、
自動実行はP4-7まで有効にしない。各unitで通常入口と実機の証拠を揃えてから次へ進む。

### P4-0 開始確認・移行準備

実装前にactive checkout/profile、3DBのREADY/binding/世代、OPEN、UNKNOWN/親admission、
現在grantとapplied snapshot、照合markerを読み取り確認する。保存済みP3結果で現在値を代用しない。
既存stateのcopyと新規空stateの両方でmigrationを確認し、明示profile不在でlegacy DBへfallbackしない。
既存のTable/履歴を消して試験を通さず、再利用するmoduleと追加schemaをunitのbriefへ限定する。

### P4-1 管理mutation境界・通常grant API/CLI（P2-R01）

既存`grants`をdesired状態として保持し、principal×DBのeffective role、単調増加する認可epoch、
applied desired revision/generationのprojectionを追加する。新規/昇格はCAS・key・監査を保存してpending、
剥奪/降格は同transactionでeffectiveを制限しepochを進め、backend収束は別に追跡する。
`request_auth`だけでなくWorksheet/nativeの継続・結果・履歴、DataOps、downloadもこのprojectionを使う。

既存のgeneration/管理Operation/plan/applyを利用する。candidate DB×role credentialによるprivate検証後、
最終transactionでdesired snapshotを再照合してeffective昇格とAPPLIEDを原子的に確定する。
verifyを新grant本人のREADY権限に依存させない。途中のrevoke/編集、stale plan、UNKNOWNでは昇格しない。
reconcileでAWAITING_APPLYへ戻るだけでは有効化せず、同じ最終検証/commitを明示applyで通す。

現backend identityは**DB×role共有**である。本人の失効は全通常/native入口の再認可、
backend検証は世代固定のDB-role policy/credentialの適用確認として記録する。
共有backend secretのprincipal別失効を実装したとは主張せず、secretをclientへ配布する設計や新しいidentity基盤は追加しない。
APIの`grant_revision`はeffective認可epochとし、generationに固定するdesired revisionと区別する。
epochのtombstoneはrevoke/regrant・再起動・migrationでも削除/初期化しない。
移行根拠は現在の`binding.applied_generation_id`に対応するverified full APPLIEDのplan manifestに限定する。
その`principal_id / database_id / maximum_role / revision`全体との一致を証明できるgrantだけを初期有効化し、
それ以外は明示apply待ちにする。epochは旧grant revision以上の永続値を引き継ぎ、任意に1へ戻さない。

通常CLIも認証済み管理HTTPを使い、grant変更のためにprivate operator材料を要求しない。
mutationは対象principal/DB、desired role、期待desired revision、keyを受け、新規は`expected_revision=0`の不存在CAS、更新は正のrevisionとする。
grant変更Operationと構成apply Operationの対応、actor、before/after role、期待/確定revision、effective epoch、適用世代、receiptを監査へ残す。
route/command名は実装briefで確定し、同key再照会と異payloadの409はP3共通契約を継承する。

実機受入：pending中の新権限はHTTP/nativeとも拒否し、verified full apply後だけ成功する。
失効直後の新規SQL・nextUri・結果・history・Export取得拒否、元pinの取消、再付与後の旧epoch拒否を対にする。
CAS競合、同key再照会、apply中revoke、応答喪失・再起動・再planを測定し、他本人/他DBの許可は保持する。

主対象：`data_management / control_store / request_auth / query_context / query_service / dataops_service`、
`catalog_admin / warehouse_operations / warehouse_operator / database_generation`と管理route、`internal/cli/database.go`。

### P4-2 表示metadata・管理画面・名前境界（P2-R02/03）

DBはdisplay_name/description、Schemaはdisplay_nameだけをCAS更新するAPI/CLIと管理画面を接続する。
schema.purposeは権限コードなので変更させない。SQL名・binding desired revision・物理identity・接続先・READYを変更しない。
metadata revision変更で未apply planがstaleになる場合は明示replanとし、古いplanを黙って使わない。
既存DB登録、固定4Schema一覧、plan/bootstrap/full/apply/status、P4-1 grantのpending/effective/revoke状態を同じ管理導線へ置く。

名前matrixはDB/Schema SQL名、表示名、Table locator/Ref、model/source alias、後続Stage/Job/Mart名を区別する。
SQL名validatorの`[a-z][a-z0-9_]{0,30}`と除外名`system / information_schema / tpch / tpcds`を維持し、DB登録/spec生成ではlegacy `iceberg`も拒否する。
勝手な小文字化や別名へのfallbackをしない。
Table識別子・source alias・表示名へ同じ31文字制限を一律に適用せず、各既存契約を明記する。

| 入力境界 | 有効境界・無効入力 | 重複範囲 | 外部副作用・実証入口 |
| - | - | - | - |
| DB SQL名 | 1/31文字は成功、32文字・予約4名・`iceberg`・文法違反は拒否 | 同一registryのcatalog名競合を拒否 | 通常登録HTTP/CLIとspec生成で拒否時送信0。有効名はplan/full apply/実SQLまで確認 |
| Schema SQL名/profile | 固定4Schemaだけをprovision。文法が有効な任意1/31文字名もprofile対象外として拒否 | DB内で一意、別DBの同名は許可 | spec/管理HTTP/CLIで固定profileを検証。任意Schema CRUDは非対応であり成功扱いしない |
| DB/Schema表示名 | 長さ・空値・型の契約はP4-2 briefで明示固定し境界を試験。日英/Unicodeを保持し、古いCASを拒否。HTML等は文字として描画 | SQL名の一意性と別契約、同じ表示名でidentityを合流させない | PATCH相当HTTP/CLI/UI、文字のround-tripとSQL/binding不変を実測 |
| DataOps作成先Table locator | 現`validate_sql_name`の1/31文字・予約4名除外。32文字/不正文法を拒否 | DB/Schema内。同名外部Tableを暗黙採用しない | Ingest/WAP作成入口を実HTTP/CLI/engineで確認。不正入力はSQL送信0 |
| 既存Table ResourceRef | ID/incarnation型と現在実体を照合。表示名へfallbackせず、他DB/再作成/欠落Refを拒否 | DB/Schema/Object IDとincarnation | Ingest/WAP/Exportの各入口で実UUIDと照合。RefへSQL名31文字制限をかけない |
| native SQLの識別子path | 現SQL guardの引用・path・同DB制約を維持。許可引用名と禁止cross-DB/不正pathを対にする | 実catalog/Schema/Tableのscope | 通常/native HTTPと実engine。DataOps作成locatorの制限をnative SQLへ一律追加しない |
| model名/source alias | P3 model/SQL契約を維持。空・不正型・重複alias・他DB Ref等の拒否を個別記録 | modelはDB内、aliasはmodel内 | model保存/buildのHTTP/CLI、保存だけではSQL送信0。Table名規則と混同しない |
| Stage/Job/Mart名 | 各unitで現資源validatorと長さ/不正入力を固定 | DB内IDとownerで分離、同名別DB成功 | P4-3〜6の通常HTTP/CLI/UI。表示名変更でrun/dedup所属不変 |

実機受入：有効な1文字/31文字DBを通常登録→plan/apply→実SQLまで通す。
32文字、除外名、同名競合、先頭数字/underscore、大文字、空白・dot・quote・path・制御文字等の不正入力は該当契約で拒否し、外部送信/実体作成0を確認する。
同名Table/modelは別DBで成功することも対にする。固定profile以外のSchema作成は明示拒否を受入とする。
日英/Unicode表示名変更の前後でDB/Schema/Table ID・incarnation・Polaris UUID・SQL結果・接続先を実測照合する。

主対象：`data_management / catalog_admin / database_inventory`、管理route/CLI、`workbench/index.html`。
P4-1のeffectiveness表示もここで実ブラウザー受入する。R03 matrixは後続の新入口ごとに追加する。

### P4-3 DB対応登録Job・手動実行adapter

DB内Job ID、owner/selected role、定義CAS、引数型、各stepのlocator/Refを保存し、runをP3共通境界へ接続する。
SQLは既存guard、Ingest/WAP/Exportは受入済みの型付きadapterだけを使う。固定`iceberg` dispatcherとdbt/任意commandへ迂回しない。
定義保存ではSQLを送らず、実行受付で定義と入力を固定する。結果行をJob履歴へ丸ごと保存しない。
親admission継承を先に実装し、複数stepの途中でleaseが抜けるadapterをStage/Martへ転用しない。

実機受入：3DB同名Job、owner違い/保存role違い/古い定義/再作成Ref拒否、実行中編集/二重受付/取消を通常API/CLI/UIで確認する。
step間drain・role失効・応答喪失・再起動でも全体の親とUNKNOWNを保持し、実送信回数と全子の本人/context/pinを照合する。

主対象：`jobs_resident / dataops_service / query_service / warehouse_admission`、Job route/CLI/UI。

2026-10-21時点：実機技術受入完了。3DBでWorkbenchの新規SQL/typed Job各1件、通常CLIの新規SQL Jobを成功させ、CSV/JSONL実内容・DB分離・owner/role/context/pinを照合した。
単一管理backendの正の停止証跡で、元のUNKNOWNと検証helper由来の追加read-only UNKNOWNを閉鎖した。Query結果UNKNOWNを保持し、別の`FENCED_READ_ONLY`で未閉鎖実行0を示す。
応答喪失・実process再起動・step間drain/取消を検証し、grant剥奪は即時拒否、再付与はbackend verify後だけ有効化した。
最終generationでWorkbenchと3DBの通常CLIを再実行し、旧context・旧query/continuation拒否、元SQLタブ2枚の保持、所有fixture清掃後のfacts25/25を確認した。LOCAL OPEN、通常2入口正常、検証用入口停止。

### P4-4 ローカルStageの手動IngestとSnapshot固定Export

前半はDB内Stage ID、owner、定義CAS、許可済みlocal rootと取込先を登録し、一覧→file選択→型付きpreview→確認→P3 Ingestを通す。
入力はP3で受け入れたCSV/JSON/JSONLに限定する。旧guide記載の追加形式は、形式ごとの実機受入なしに有効化しない。
受付時に実bytesか操作所有の不変copyを保持し、内容/設定fingerprintを固定する。previewから受付までのfile・定義変更は再確認を要求し、受付後に元pathを読み直さない。
標準path解決と既存25MiB/file・件数/合計容量上限を維持する。
この段階からStage ID、入力/設定fingerprint、target Ref、Operation/run、commit判定を保存し、P4-5から同じ取込証拠を使う。
後半は同じStageへのExportをP3 `manual_export`へ接続し、完了artifactだけを所有Stageの`exports/`へ公開する。
CSV/JSONL、安定Table Ref、開始時Snapshot、owner付きreceipt/取得を継承し、旧`full=False`経路を使わない。
出力名は直接file名に限定し、既存fileを上書きしない。非公開temporaryへ書き、公開直前に権限/定義を再確認する。
file公開と記帳は区別し、公開後の記帳不明を未出力と見なして再出力しない。

実機受入：3DB同名Stage/同名file、他owner/他DBの一覧・preview・取込・取得拒否、root外/path traversal/部分file拒否を確認する。
受付前/後のfile変更、target再作成、途中失敗、失効、Export中append/Snapshot消失を実際に起こし、固定bytes/型・Snapshotと清掃範囲を照合する。
既存出力名の衝突、公開後応答/記帳喪失も試し、完了fileの保持と二重出力の拒否を照合する。
View ExportはStage経由でも拒否する。Export directoryを自動scanが再取込しないことも確認する。

主対象：`stages_resident / stage_exports / manual_ingest / manual_export / jobs_resident`、Stage route/CLI/UI。

### P4-5 Stageの手動scan・内容dedup

2026-10-22時点：**P4-5の限定技術受入完了**。
最終generation `p45-scan-final`、LOCAL OPEN revision 105、未解決実行0、過去の`FENCED_READ_ONLY` 2件を保持した。
失効後の実GUI、認証撤去後の通常入口復帰・元2SQLタブ、独立最終照合まで完了した。
検証範囲と制約は[P4-5報告](database-p4-5-manual-stage-scan-dedup-report-2026-10-22.md)を参照する。
P4-5の後続要件はP4-6以降の各節へ引き継いだ。

P4-4の後、まず手動scanを作る。scanは現行どおりappend/upsertだけを許可し、create/replaceは設定時/実行時に送信前拒否する。
create/replaceはP4-4の単一file手動操作に限定する。upsert keyを検証し、dedup keyはDB/Stage ID、target Ref、mode・upsert key・型/encoding等の設定、実入力bytesを含める。
file名や表示名をidentityにせず、改名した同内容、同じ名の更新内容、設定/target変更を区別する。
Ingest commit・run・dedup確定の対応を保存し、UNKNOWNやcommit後記帳失敗で未実行扱い/自動再取込しない。
P4-4の手動取込証拠も照合し、scan追加のための再登録を要求しない。複数fileはfileごとにcommitし、後続失敗で先行成功fileを取り消したり再取込したりしない。

実機受入：同内容別file名、更新時刻だけの変更、同名別bytes、DB/target違い、手動とscanの競合、2process競合、再起動を確認する。
create/replaceのscan拒否、upsert key違い、途中file失敗/UNKNOWNと先行fileのcommit/dedup保持を確認する。
既知の未commit失敗だけを明示retryできること、commit済み/UNKNOWNは正照合まで止まることを実SQL回数とtarget内容で示す。
これはfile内容単位の重複防止であり、行単位dedupやSQLite/Iceberg間のexactly-once保証とはしない。自動scanはP4-7まで無効。

主対象：`stages_resident / jobs_resident / control_store`とscan/dedup UI/CLI。

### P4-6 管理Martの手動全件更新・変更確認

2026-10-22時点：**P4-6の限定技術受入完了**。
最終generation `p46-mart-final`、LOCAL OPEN revision113、未解決実行0、過去の`FENCED_READ_ONLY` 2件を保持した。
Workbench/通常CLI、実故障・失効、42 Table/9入力の有限清掃、検証auth2/private8撤去、元2タブの復帰と独立最終照合まで完了した。
証拠の制限と範囲は[P4-6報告](database-p4-6-managed-marts-report-2026-10-22.md)を参照する。
予定実行はP4-7まで無効とし、Phase4全体と所有者の最終操作性評価は後続計画へ残す。

DB内Mart IDと、P3 model ID/明示version、同DB source Ref、MARTS target、品質条件を結び、登録だけではSQLを送らない。
admin ownerの手動更新をP3 build→audit→publishへ接続する。初回locatorはP3と同じ実測materialized Refへ移り、次回はそのRefを使う。
同名外部Tableを採用しない。modelの変更は明示CAS/version更新とし、公開後の更新には現在target Refを要求する。
無関係なP3 modelや旧定義を同名だけで自動登録しない。

変更確認はsource Ref＋開始時Snapshot ID集合＋選択model versionを最後の公開成功基準と比較する。
Snapshotは大小でなく一致で判定し、rollbackも変更とする。全sourceの同時点transactionやSQLからの依存自動推論は約束しない。
自動更新の対象には同DBの監視Table Refを1件以上明示し、欠落/権限喪失/現Snapshotなしを変更なし扱いしない。
時刻依存式、Schemaだけの変更、targetへの直接編集はこの変更検知の対象外とする。
公開・基準保存・履歴確定の成功を分け、公開後記帳失敗では未確認の基準/履歴を捏造せず対象Martだけを照合待ちにする。
基準保存済みなら後続履歴失敗だけで巻き戻さない。更新中のsource変更を成功基準へ取り込まず、次の変更確認へ残す。

実機受入：新規/既存targetの全件更新、変更なし、source append/rollback、model版変更、source/target再作成、audit後stage変更を確認する。
commit前の品質不合格・失効では旧公開結果/前回基準を保つ。既知のcommit後は新公開結果と確認済み基準を保持し、
清掃/記帳失敗で自動rollback・再publishせず正照合する。変更なしでは公開成功時刻を更新しない。
更新中source append、監視sourceなし/消失/取得拒否も受入し、未処理の変更を基準更新で消さない。
同名Martを3DBで分離し、公開Snapshotと監査Snapshotの対応を実値で照合する。予定更新はまだ有効にしない。

主対象：`marts_resident / manual_wap / jobs_resident / control_store`、Mart route/CLI/UI。

### P4-7 既存schedulerでの予定実行・自動scan/更新・alert

2026-10-23追記：限定技術受入を完了した。P4-3〜6の通常実行経路を再利用し、3DBの自然発火、権限失効時の停止と明示再開、
DRAIN中の新規停止・受理済み完了、alert、清掃・認証撤去・元SQLタブ復帰を実機確認した。
最終の独立監査は6項目すべて合格。実施範囲と証拠の制限は[P4-7報告](database-p4-7-schedules-alerts-report-2026-10-23.md)を参照する。

P4-3〜6で受け入れた定義だけを、既存間隔/カレンダー予定へ接続する。既定無効、owner/role/定義版と実行意図を保存する。
各発火をschedule ID・予定回・定義revisionへ結び、tick予約から受付までに定義が変われば古い予約を拒否する。
定義編集後は明示再有効化を要求する。tick時に現在認可・binding/Refを確認してrunを固定し、期限超過/競合/停止を明示記録して追いかけ実行しない。
失効・失敗・UNKNOWNで止めた予定の再開は明示操作とし、別roleでの救済や自動再POSTをしない。
alertは既存の読取SQL1行1列Boolean判定とWorkbench内通知だけをDB対応する。

実機受入：3DBの予定を実際に発火させ、同時tick/手動run/再起動で同じ予定を二重受付しないことを確認する。
時刻/TZ/DST/月末・遅延・競合、無効定義1件のfault isolation、drain中の新規停止と受理済み完了を確認する。
tick予約とrun受付の間の定義編集を試し、古い定義の実行0と明示再有効化を確認する。
Stageの重複skip、Martの変更なし/変更あり、alert TRUE/FALSE/NULL/多行/失敗、通知owner/DB分離を実値で照合する。
権限喪失中のalert失敗をFALSE扱いして既存通知を解消せず、遅延応答で新しい状態を巻き戻さない。

主対象：`schedule_resident / jobs_resident / stages_resident / marts_resident`、alert・予定route/CLI/UI。

### P4-8 非purgeのDB retire

P4-3〜7の依存Refを列挙できてから、専用のadmin管理Operationとして実装する。
CASでretiringへ移し、新規受付/結果取得を拒否して対象DBの予定だけを止め、全既存run・子query・UNKNOWNを照合する。
受理済み親の内部継続、元pin取消、管理状態確認/reconcileは残し、利用者向け結果/artifact取得の拒否と区別する。
安全なdrain完了後にretiredを確定する。物理Table・Catalog・storage・credentialは削除せず、ID/SQL名の再利用もしない。
中断・UNKNOWN中はretiringとblockerを表示し、timeoutだけで完了/OPEN/再有効化しない。
generationの最終確定もstate CASを再照合し、通常full applyや再planがretiring/retiredをREADYへ戻さないようにする。

実機受入：使い捨てDBを通常管理入口からretireし、依存Job/Stage/Mart/Export/runを表示・停止できることを確認する。
実行中/UNKNOWN/競合中のretireは安全に待機/拒否され、他DBの定義・許可・実データは保つ。
retired DBの通常/native/全自動入口が拒否し、物理データ保持とfull apply・再plan・再起動後のretired維持をREST/engineのoperator照合で確認する。

主対象：`data_management / catalog_admin / warehouse_operations / warehouse_admission`、全機能の依存検索とcapability、管理UI/CLI。

### P4-9 通常入口の縦断・永続化・clean環境受入

各unitのfocused受入を集め、通常Workbench/CLI→認証HTTP→実Trino/Polaris/S3を一連で確認する。
既存環境の再起動/通常再配備と、専用のclean環境再構築を別の証拠にする。P3の既存環境成功でclean gateを代用しない。
clean側も明示profile、private材料、bootstrap/full plan/apply、両入口の世代確認を通し、専用Shirokuma環境だけを対象とする。
clean側では新規定義と新規retireを受け入れる。既存control storeのretire tombstoneを空stateへ復元するDRは本計画に含めない。
backend終端記録を取得できないUNKNOWN、およびUNKNOWNとgrant epoch変更が重なる場合のoperator回復契約・runbookも明示し、実機で受け入れる。
P4-3では単一管理backendのread-only Job SQLに限定した実行閉鎖を先行実装・実機確認した。write不確定・分散/外部backend・他機能の不確定処理へ適用を広げない。
不在/TTL/PIDや架空のQueryInfoで既存reconcileを通さず、必要な証拠・保持期間・回復不能時の停止条件を固定する。
本unitでP2-R01〜03の残件matrixを閉じ、未対応capability・Phase 5の所有者評価を残した技術closeoutを作る。

## 実機受入matrix

unitごとの件数目標ではなく、次の条件と実証層で合否を判断する。
同じ成否だけでなく、外部送信回数、実Table UUID/Snapshot/行/bytes、認可epoch、親子台帳、設定/履歴の所属を記録する。

| ID | 必須条件 | 実証層 |
| - | - | - |
| P4-A01 | grant pending中は昇格不可、verified full applyでのみ有効化。migrationはprincipal/DB/role/revisionを照合し、未照合desiredは自動有効化しない | 通常管理API/CLI/UI＋generation/台帳＋実DB-role backend検証 |
| P4-A02 | revoke/downgrade直後のSQL/nextUri/結果/history/artifact拒否、元pin取消、regrant後の旧epoch拒否。他本人/他DB正常系を対にする | 実HTTP/native/ブラウザー＋DELETE記録 |
| P4-A03 | grant CAS競合・同key/異payload・apply中変更・UNKNOWN/reconcile/replanで過剰付与/二重送信なし | 複数process＋実HTTP fault＋永続台帳 |
| P4-A04 | 表示名CAS前後でSQL名/ID/binding/Polaris UUID/接続先/行が不変。purpose変更不可、古いplanは再plan | 通常HTTP/CLI/browser＋実engine/REST |
| P4-A05 | R03入口matrixを全行実証。有効1/31文字DBの作成/apply/SQL、不正/重複の副作用0、別DB同名成功 | 通常API/CLI/spec＋実engine。validator単体だけで完了しない |
| P4-A06 | 3DBの同名Stage/Job/Mart/Table/file、別owner、admin降格、未知/非READY DBを分離 | 通常UI/API/CLI＋実値/owner/Ref照合 |
| P4-A07 | Jobの定義版とrunを固定、全step間の親admission保持、全子のowner/context/pin一致、未対応action拒否 | 実複数step＋2process競合＋台帳 |
| P4-A08 | Stage受付前のfile/設定変更で再確認、受付後は固定bytes。target再作成拒否、型・commit意味と手動取込証拠を保持 | 実file/engine＋preview/commit/清掃照合 |
| P4-A09 | scanはappend/upsert限定。dedupをDB/Stage/target/設定/内容で分離。先行file成功を保持し、改名/競合/再起動/記帳失敗でもUNKNOWNを再取込しない | 実内容・送信回数・target照合 |
| P4-A10 | Stage Exportは開始時Snapshotの全件、消失時fallbackなし、View拒否、部分file非公開、上書き/二重公開拒否、失効後取得拒否 | 実同時更新/REST/CSV/JSONL/公開後故障/HTTP/browser |
| P4-A11 | Martはsource Ref/Snapshot集合/model版で変更確認し監査Snapshotを公開。precommit失敗は旧公開を保持、既知commit後は新公開を保持して記帳を照合。更新中の新source変更を基準へ消費しない | 実WAP/rollback/故障＋Snapshot/実値照合 |
| P4-A12 | 予定の重複/遅延/競合/再起動・DSTと予約後編集を扱い、追いかけ再実行なし。失効/UNKNOWNで停止し、無関係な定義のschedulerを止めない | 実tick・複数process・時計境界回帰・実SQL |
| P4-A13 | alertの厳密Boolean、TRUE/FALSE/失敗の状態差、通知のDB/owner分離、遅延結果の逆転防止 | 実SQL＋通知API/browser |
| P4-A14 | DB retireで対象依存だけを停止、UNKNOWNを残して完了にしない。full apply/再planでも再有効化しない。他DB不変、物理データ/履歴保持 | 通常管理入口＋各実行入口＋REST/engine正照合 |
| P4-A15 | 全新入口で送信前intent、同key照会、再起動、結果喪失、commit後清掃/記帳失敗、正照合後の明示再開を確認 | 故障注入＋実engine/台帳。fake成功応答で代用しない |
| P4-A16 | 既存タブのSQL/DB/Schema/role/Warehouse/mode保持、失効時cache消去、focus・日英・遅延応答・未対応gate維持 | 実ブラウザー＋UI回帰 |
| P4-A17 | 定義・grant desired/effective/epoch tombstone・予定・run/UNKNOWN・retired状態が再起動/再配備後も整合。clean環境は新規作成/retireを含む同等縦断を別途成功 | 既存再配備＋専用clean構築＋両入口世代証明 |
| P4-A18 | focused→全体/Go/設計検査、doctorと別の実SQL、所有物だけの清掃と残存照合、残件記録 | 技術closeout。Phase 5の所有者最終評価と分ける |

R03の管理対象を実体作成する試験には、既存3DBとは別の使い捨てDB名を使う。
通常管理入口の拒否と、診断transportによるDB-role backend境界確認は別々に記録し、片方で他方を代替しない。
削除/失効等で取得不能な履歴は、現在許可された履歴全体を壊さず隠し、現在DBの認可も返却前に再確認する。

## 実装・検証の進め方

各unitの開始時に対象ファイル、非目標、通常入口、正負/faultの実機条件をbriefで固定し、
ADR-0036どおりbounded workerへ渡す。保存層、HTTP、CLI、UIの単なる配線完了で次unitへ進めない。
module名は上記の現行sourceを起点とし、必要なroute/adapterを小さく追加する。汎用workflow engineは作らない。

検証は変更moduleのfocused tests、対応する実機受入、`make verify-local-experiment`、
Go製品範囲`go test ./cmd/... ./internal/...`、`make verify-design-context`、文書/空白検査を区別する。
active profileの通常`shirokuma doctor`と、readiness表示とは別の通常SQL実値を確認する。
現在のGit管理外`var`には過去のGo source copyがあるため、`go test ./...`の混入結果を製品試験と混同しない。

受入記録にはprofile/世代、actor/role/DB、試験prefix、Operation/Ref/Snapshot、期待した副作用と実測、
清掃範囲、残した監査metadata・照合待ちを簡潔に残す。秘密値は記録・公開しない。
既存環境・clean環境・ブラウザー自動観察・所有者の評価は別gateとし、外部公開の完了判定へ広げない。
計画作成時の次の実装単位はP4-1とした。最新の受入状態は冒頭の追記を参照する。
