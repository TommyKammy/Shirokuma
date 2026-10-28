# Phase 2の完了判定とPhase 3の実装・受入計画

2026年10月20日。対象はデータ管理アーキテクチャ計画のPhase番号であり、DEV-04Fの歴史的なPhase番号とは別である。今回は文書の整理と読取り確認だけを行い、Phase 3の実装・配備・状態変更は行っていない。

## 完了判定

**Phase 2の最小探索・SQLと、明示operatorによるDB管理基盤は限定技術受入完了とする。** P2-1〜P2-6の縦断は達成した。通常grant変更API・表示名更新APIは未実装であり、当初計画との差分として残す。全入力境界を実HTTP / engineで網羅した判定や、管理機能全体・一般公開の完了判定には広げない。

現在の通常環境を読取り専用で再確認した。`sales` / `finance` / `insights_e88387a5` はREADY、受付OPEN、Workbench / native SQL双方が `p26-live-redeploy` を返す。Tofu差分0・doctor healthyは直前のP2-6保存証拠による。空環境の正常状態は検証終了時の観測で、専用K3sはその後停止しvolumeと証拠を保持している。

最終コード回帰は1,038件中1,006成功・既定32 skip、設計14試験と文書検査、Go製品範囲test / vet成功。今回はこの保存結果を確認し、全体の実機試験を再実施したとは扱わない。

| Phase 2条件 | 判定と証拠の範囲 |
| - | - |
| P2-A01 ID / revision・Jobs | P2-1の空SQLite・process再起動・Jobs契約回帰で確認。現在schema v5 |
| P2-A02 SQL名・表示名不変 | 共通validatorの1 / 31 / 32文字・予約名・重複、表示名CASと物理名不変は単体 / processで確認。HTTP / engineは代表名の作成・適用のみ。全境界の実機coverageは持越し |
| P2-A03 本人・DB・履歴 | P2-2 / P2-3の別本人、未知 / 不許可 / 非READY拒否、履歴分離。P2-6の通常・再配備・空環境HTTP各56項目で補完 |
| P2-A04 DB×roleの正負 | P2-2に加えP2-6直接Trino 222項目。正常操作と他DBの各DML / DDL拒否、MARTS rollback拒否・admin成功。通常HTTP全DMLの証拠とは別 |
| P2-A05 identity偽装・降格 | P2-2 / P2-3の本人・role・header偽装拒否、admin降格時のDB限定identity |
| P2-A06 native名前解決 | P2-3の同名Table・1 / 2 / 3部名・書込みcontext・古いbinding拒否。SQL本文の書換えなし |
| P2-A07 owner / nextUri / cancel | P2-3の複数ページ、本人・DB・role固定、失効、資格情報pin、process再起動時の拒否。透過的な継続復旧を保証しない |
| P2-A08 ResourceRef | P2-3のREST実Table UUID・同名再作成・型別capability。強いidentityを取得できない操作は無効 |
| P2-A09 冪等・非冪等送信 | P2-3 / P2-5の送信前intent・応答喪失・送信回数。P2-6の作成202 / 同payload同ID / 異payload409 |
| P2-A10 UNKNOWN | P2-5の途中停止・再開・owner / admin照合、証拠なしの未実行化・自動再POST拒否 |
| P2-A11 世代 / Secret | P2-5の同時編集・再planとP2-6のfull検証、両入口証明・private材料固定。desired差分を誤READYにしない |
| P2-A12〜13 admission / drain | P2-5のwhole-operation・継続GET / cancel・schedule停止・process排他。P2-6の未送信full再plan component 24項目と追加9回帰で補完。componentの外部stepはno-op |
| P2-A14 任意3番目DB | P2-6でDB別process / realm / prefix / key、全7 child、Secret / ACL / OPA / authを同一世代から実適用 |
| P2-A15 再配備・空環境 | P2-6の6 Pod再作成・PVC維持、3 Table UUID / 行 / binding保持、空環境3DB再現、S3各33・purge各9・OPA停止24項目。DRとは別 |
| P2-A16 未対応入口 | P2-3 / P2-5の旧route拒否とadmission参加 / 明示停止。新DBのDataOps対応済みという意味ではない |

P2-A13の「受付を戻す」は、**元OPEN・先行構成変更なし・未送信が確定した場合だけOPENへ戻す**ことを意味する。PREPARED継承、変更済み、結果不明の場合は閉鎖を保持する。timeout / PID / TTLだけで未実行とせず、未送信fullの再planもplan時はowner不変、明示apply時だけ原子的に移管する。無条件OPENへ戻す受入ではない。

根拠は [P2-3記録](database-p2-3-exploration-sql-report-2026-10-19.md)、[P2-4記録](database-p2-4-config-generation-report-2026-10-20.md)、[P2-5記録](database-p2-5-operation-report-2026-10-20.md)、[P2-6記録](database-p2-6-empty-database-report-2026-10-20.md)、[UX-D1記録](database-ux-d1-explorer-sql-report-2026-10-20.md)。P2-3の181項目、P2-5の95項目、P2-6の件数は異なる時点・対象の証拠であり、単純合算で現在の全機能coverageとはしない。

## 明示して持ち越す事項

| ID | 残課題・制約 | 次に扱う範囲 |
| - | - | - |
| P2-R01 | 通常grant変更APIは未実装。grant CAS・失効再認可・backend分離の成功とAPI完成を区別 | Phase 4の管理API / UI。付与はbackend適用後、剥奪は即時拒否、revision競合・監査・再planを受入する。当面は明示operator手順 |
| P2-R02 | 通常表示名更新APIは未実装。保存層のCAS・物理名不変だけではAPI受入にならない | Phase 4。表示名変更でSQL / binding / Table identity・接続先が変わらないことを実HTTP / engineで確認 |
| P2-R03 | 名前の全入力境界の実HTTP / engine確認は未実施 | Phase 4の管理API受入で補完するcoverage残。Phase 3では共通validator回帰を維持し、未実施を解消済みとしない |
| P2-R04 | private材料の準備、bootstrap / fullの承認、両入口再起動、初回foundationは明示操作。旧構成の初回切替は手動SSA所有権整理を要した | 現行operator手順として維持。新環境・通常再配備の通常Operator成功と初回手動整理を区別 |
| 後続受入 | 新DBのDataOps、DB削除 / retire、任意Schema、Jobs / Stages / Marts / schedule / AI / dbt、DR、外部BI・所有者操作観察 | 対応capability単位のPhase 3〜5。未対応は引き続き無効・明示拒否 |

R01 / R02は当初Phase 2に含めたAPI記述との差分であり、実装済みへ読み替えない。R03も残したうえでの限定完了である。最小探索・SQLを妨げる既知の追加技術blockerはない。

## Phase 3の到達点と固定する契約

到達点は、**任意のREADY DBでローカルCSV / JSONを手動Ingestし、通常SQLで確認、手動WAPのbuild → audit → publish、Snapshot固定のTable全件Exportまで通すこと**。SALES / FINANCEと任意名3番目DBを同じ経路で扱い、同名Table・modelでも別DBへ影響しない。新規環境を再作成するPhaseではない。

- 新規作成先はDatabase / Schema IDとSQL Table名を持つlocator、既存対象は実Table UUID / incarnationを持つResourceRefで区別する。存在しないTableに仮UUIDを付けず、create完了後に実測してRefを返す。
- actor・保存QueryContext・selected / effective role・grant / binding revision・backend identityを受理時に固定する。step前・publish前・出力取得時にも現在の許可を確認し、roleやDBを自動代替しない。
- Ingestの入力byte・format / encoding・型指定・modeのfingerprintを受付payloadに固定する。CLIのfileとWorkbenchのcontentを同じ入力契約へ正規化し、受付後の入力変更は拒否する。同じIdempotency-Keyで入力が変われば409とし、ファイルpathだけで同一要求とみなさない。
- engineerはRAW / SANDBOXのIngestとWAP build / audit、adminはpublish / rollback、viewerは許可MARTS Tableの読取 / Exportとする。WAPのstageは既存SANDBOXを使い、STAGINGへの配置変更やadmin資格情報への自動代替は追加しない。
- 公開WorksheetのSQL Guardを広げない。一時表清掃・upsert / promote・WAP管理SQLはサーバー生成の対象と操作だけを許可し、同じ本人・DB・実効権限・durable query intentを通す。内部SQLを任意指定できるAPIや無制限のguard bypassは作らない。
- 操作全体が一つの親admissionを保持し、各SQLは子queryへ記録する。送信前intent、同key / 同payloadの同Operation、UNKNOWN保持・再POST禁止を継承する。成功済みcommitと後続の清掃失敗を分ける。
- featureごとに受入後だけcapabilityを有効にする。通常UI / API / CLIは同じ判定を使い、未接続の旧routeは明示拒否を維持する。

## 小さく実装する順序

| 作業 | 具体範囲 | 受入の要点 |
| - | - | - |
| P3-1 共通DataOps実行境界 | locator / ResourceRef、本人・DB別の実行adapter、必要な内部SQL、操作intentとwhole-operation admissionを接続。Warehouse構成applyの固定stepへDataOpsを混ぜない | 任意READY DB、未知 / 非READY / 不許可・古いbinding・再作成Tableの拒否。清掃SQLは当該操作の実測済みstage / backupに限定。query ownerとUNKNOWNを保持 |
| P3-2 手動Ingest | ローカルCSV / JSON、明示型 / encoding、create / replace / append / upsert。登録済みRAW / SANDBOXを使い、暗黙Schema作成を外す。既存TableはRefを必須にする | 先頭ゼロ、Decimal、NULL / 空文字、timestamp・upsert未指定列を保持。target競合・途中障害・atomic append / replace / upsert、commit後の清掃失敗を実機で確認 |
| P3-3 手動WAP | DB内model ID＋version、同DBのsource / stage / target Ref、監査対象Snapshot、publish / previousの対応を保存。stateはprofile確定後のcontrol_storeへ置き、旧JSONは移行しない | 同名modelのDB分離、engineer build / audit成功・publish拒否、admin publish / rollback成功。audit後のstage変更・target再作成・grant失効を拒否。公開結果は監査Snapshot由来であることを照合 |
| P3-4 Snapshot固定Table Export | 本人の読取許可Tableから開始時UUID / Snapshotを固定しCOUNT / 全件SELECT / receiptで共有。CSV / JSONLの完了artifactと本人付き取得を接続 | 同時appendでも開始時の全件一致、上限による無言打切りなし。Snapshot取得不能 / 失効・Table再作成・件数不一致は成功にしない。View全件ExportはUI / API / CLIとも拒否 |
| P3-5 通常入口で縦断受入 | Workbenchの必要なIngest / WAP / Table Export導線と通常CLIを接続。SALES / FINANCE / 任意名DBの同名資源で一連を実行 | 通常UI / CLI→実Trino / Polaris / S3で正負確認。タブcontext保持、失効時の結果 / artifact拒否、遅延応答・日英・キーボード回帰。未対応入口の拒否を維持 |

P3-1から順に一単位ずつ限定実装・レビューし、途中段階をPhase 3全体完了とはしない。専用stage名や2行fixtureにしか動かない実装を作らず、複数Table名・入力件数・値で検証する。

新規createはDB / Schema / Table名の明示locatorを必須とし、既存先なら409、暗黙OR REPLACEを行わない。append / upsert / replaceは既存Refと明示modeを要求する。入力ファイル名や画面の最新DB選択から実行先を推定しない。

主な対象は、P3-1が`query_service / query_context / request_auth / warehouse_admission / data_management / control_store`、P3-2が`lake_ingest`、P3-3が`wap_resident`、P3-4がTable Exportのresolverと`lake_ingest`、P3-5がWorkbench / resident / CLIの呼出元である。必要な箇所だけ小さく変更し、汎用workflowや常駐workerを追加しない。

現sourceには、`lake_ingest.target_table`の固定`iceberg`、Ingest / WAPの暗黙Schema作成、清掃DROPの既存guard迂回、別時点COUNT / SELECT、WAPのimport時state path固定が残る。単なるcatalog文字列置換や旧decoratorの解除では受入しない。WAP stageのSchemaは現在のSANDBOXを維持する。

## Phase 3の必須受入

| ID | 合格条件 | 証明する層 |
| - | - | - |
| P3-A01 | 存在しない作成先locatorと既存Refを区別。Tableの作成競合・同名再作成・古いbindingを拒否し、他DBへ代替しない | 共通model＋実HTTP / CLI / REST |
| P3-A02 | 3DBの同名Table・同名modelを分離。sales-only / finance-only / 両DB本人とadmin降格で正負操作を対にする。複合操作のcross-DBは拒否 | 実Trino / HTTP。engine側拒否はアプリの一律失敗と区別 |
| P3-A03 | 先頭ゼロ、大きいDecimal、NULLと空文字、既存TIMESTAMP精度（0〜6）・timezone-naiveの値、upsert keyと未指定列を保持。offset付きTIMESTAMP等の未対応型は書込前に拒否し、暗黙変換しない。replace / atomic append / upsertの途中失敗でtargetを不完全に公開しない | 型・障害回帰＋実engine。通常appendの非atomic仕様は明示し、全modeを原子的と偽らない。VARCHARとしての原文保持は別契約 |
| P3-A04 | parent admissionをstep間も保持。drain中の新規要求を拒否し、受理済み操作と継続 / cancelは進む。UNKNOWNの子を無視してapply / Chillしない | 実複数process＋通常入口 |
| P3-A05 | Operation / query intentを外部送信前に保存。応答喪失・process停止後も二重送信せずUNKNOWNを保持。query IDなし・not foundだけで未実行にしない | HTTP fault injection・送信回数・台帳照合 |
| P3-A06 | WAPは同DBの固定sourceと監査Snapshotを使う。監査後のstage / model版変更・未監査公開を拒否し、元RAWへのappendで公開元をlatestに差し替えない。公開Table / Snapshotと監査元の対応を記録。engineer / viewerのpublish / rollbackは拒否、admin正常系を対にする | 実engine / REST＋永続model version |
| P3-A07 | Export中の同時appendに対し、COUNT / SELECT / receiptが同じ開始時UUID / Snapshot・件数 / 内容を示す。開始時Snapshot消失時もlatestへfallbackしない | 実engineの並行更新・Snapshot失効・件数照合 |
| P3-A08 | Viewの全件ExportをUI / API / CLIで拒否。receiptはDB / Schema / Table identity・Snapshot ID文字列・format・件数・ownerを持つ。部分ファイルを完了artifactにしない | 型別capability＋実HTTP / CLI |
| P3-A09 | 受付後のDB / role切替で対象・結果・履歴・artifactが別DBへ移らない。grant失効後は取得を拒否、元credentialで取消要求し、別identityへ切替えない | 実HTTP / browser・別本人 |
| P3-A10 | backend commit成功後の清掃 / 記帳失敗を未実行扱いしない。操作所有のstage / backup以外を消さず、UNKNOWN時の手動照合を可能にする | fault injection＋実engine正照合 |
| P3-A11 | 既存タブのSQL / DB / Schema / 保存role / Warehouse / modeを保持。遅延応答・日英・キーボードを回帰し、未対応capabilityが再描画で有効化されない | 実browser＋UI回帰 |
| P3-A12 | 各unitのfocused tests、全体・設計検査、doctor、通常入口の縦断証拠を揃える。操作成功と出力照合・人の操作観察を別記録にする。P2-R01〜03の未実施は引き続き明示 | 技術closeout。所有者による最終操作性評価はPhase 5 |

P2のSELECT試験、直接Trino 222項目、purge試験はこの縦断の代替にしない。Exportは「返却済みWorksheet結果の保存」と別機能であり、結果CSV / TSV等の再有効化は別の受入に残す。

初期Table Export形式はCSVとJSONL（1行1 objectのUTF-8、JSON配列ではない）。receiptとUI / CLIでもこの区別を表示し、JSON配列・Parquetを今回の新DB対応済み形式へ加えない。

## Phase 3へ含めないもの

DB / Schema / grant / 表示名の管理UIと通常管理API、DB削除 / retire、任意Schema profile、Stage登録・自動scan / dedup / Stage Export、Jobs / Marts / Schedule / AlertのDB一般化、SQL Server等の外部connector sync、AI / dbt / DR、外部BI driver認定は含めない。WAPのDB内model identityはP3に必要な最小分だけ先行し、Martの変更検知・定期更新はPhase 4へ残す。共通型処理の変更では旧sync等の既存契約回帰を維持する。

旧テストデータ・定義・JSON・タブ形式の移行 / 保全は不要。P3で新しく作る対象と実行状態の整合性・UNKNOWN・清掃範囲は、それとは別に保証する。Phase 3の実装開始は今回の作業には含めない。

今回の文書確認は`make verify-design-context`の14試験・文書検査、証拠リンク・空白検査、独立した完了判定 / 統合契約レビューを完了した。製品ソース・モックHTML・配備設定は変更していない。
