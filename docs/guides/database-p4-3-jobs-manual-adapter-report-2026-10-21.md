# P4-3 DB対応登録Job・手動実行adapter 実装・実機受入報告（2026-10-21）

P4-3の実装、通常HTTP/CLIとWorkbenchによる3DBの新規手動実行、UNKNOWN runの安全な実行閉鎖、grant失効・backend検証後の再付与、最終清掃・照合を完了した。**P4-3の実機技術受入は完了**。
過去のQuery結果はUNKNOWNを保持し、正のContainer停止証跡に基づく`FENCED_READ_ONLY`を別記録した。未閉鎖の実行は0件、LOCALはOPEN。
Workbenchでの保存・context/CAS・入力訂正・role変更時の情報消去に続き、Mac解除後の3DBでSQL/型付きJob各1件、計6件が成功した。別の通常CLI SQL実行も各DBで成功した。
失効検証helperの不備で生じた追加のread-only UNKNOWNも同じ停止証跡境界で閉鎖し、過去結果UNKNOWNの2件と未解決実行0件を区別している。最終generationは`p43-recovery-final`、LOCALはOPEN、通常2入口は正常で、検証用2入口は停止した。
次の計画単位はP4-4で、今回は着手していない。所有者の最終操作性評価・Phase 4全体の最終受入は引き続きPhase 5へ残す。

## 実装した契約

- DB内UUIDのJobをowner・保存selected role・DB/Schema/LOCAL・binding・revisionとともに保存する。定義保存ではSQLを送らない。
- 作成revision 0、更新はJob IDと正のCAS。同key・同payloadは元のreceipt、異payloadは409。owner/保存roleを変更しない。
- Job名は既存の`[a-z][a-z0-9_-]{0,63}`、1〜64文字。Table作成名の1〜31文字契約とは分ける。
- 引数はstring/integer/boolean/date。識別子の置換には使わない。stepは1〜50件、名前重複を拒否する。
- engineerは自分のSQL/Boolean alert、adminは加えてIngest・WAP全更新・Table Export。viewerのJobs APIを拒否する。
- 受付時に定義revision・引数・名前付き入力bytes・完全な9項目context・本人とbackend pinを固定する。実行中の定義編集を受付済みrunへ反映しない。
- Job全体の親admissionをstep間も保持し、内部の子だけが元context/pinを継承する。親・DataOps child・SQL intentを送信前に永続対応付けする。
- Job SQLは保存時と実行直前に`check_sql_policy(..., database_only=True)`で同DBに制限する。通常SQL入口の既定値Falseは維持する。
- SQL内のTable名は既存のSQL名前解決に従う。typed adapterのResourceRefはUUID/incarnationを照合し、再作成Refを拒否する。
- WAP/Exportのsource Snapshotは対応するchild stepの受付時に固定する。先行Ingestの結果を後続stepが利用できる。
- 履歴には成否・件数・Boolean・child ID等の要約を残し、結果行やprivate upload bytesを返さない。
- 全体1 Job、既存SQLite/flockを維持する。固定`iceberg`定義の暗黙移行、dbt/任意command、DB対応の予定実行は有効にしない。

主実装は`db_jobs.py`、`job_routes.py`、`jobs_client.py`、Go `internal/cli/jobs.go`、Workbench Jobs panel。
control store v12は5個のDB Job用tableを追加し、既存のlegacy tableと分離する。
v13はquery admissionの実行閉鎖列とoperator recovery requestを追加する。
read-only UNKNOWNの限定的な実行閉鎖は[復旧runbook](database-read-only-job-execution-recovery-2026-10-21.md)を参照する。

## 通常入口

既存の認証・selected-role header・同一Origin制御を使い、完全なcontextを要求する。
GETは`context=<full9 JSON>`、mutationは本文context、保存/runは`Idempotency-Key`を使う。

| 操作 | route |
| - | - |
| 一覧 / CAS保存 | `GET /api/v1/jobs` / `POST /api/v1/jobs` |
| 定義詳細 / 手動実行 | `GET /api/v1/jobs/{job_id}` / `POST /api/v1/jobs/{job_id}/runs` |
| 履歴 / 実行照会 | `GET /api/v1/jobs/runs` / `GET /api/v1/jobs/runs/{run_id}` |
| 元contextで取消 / 証拠照合 | `POST /api/v1/jobs/runs/{run_id}/cancel` / `POST /api/v1/jobs/runs/{run_id}/reconcile` |

通常Go CLIは`job {list,save,show,run,runs,inspect,cancel,reconcile}`。
通常client profileで認証し、`--database`/`--schema`または`--context-file`で選択する。

```sh
shirokuma job list --database sales --schema sandbox
shirokuma job save --context-file ctx.json --definition job.json --expected-revision 0 --idempotency-key create-1
shirokuma job run --context-file ctx.json --job-id JOB_UUID --expected-revision 1 --arguments @args.json --idempotency-key run-1
shirokuma job inspect --context-file ctx.json --run-id RUN_UUID
```

更新はsaveへ`--job-id`と現在revisionを付ける。Ingest入力は`--input batch=/absolute/input.csv`等で指定し、POST前に全bytesを読む。
WorkbenchはWorksheet/Explorerと独立したJobs context、定義編集、名前付きfile、CAS、最新revisionの明示再読込、履歴・照会・取消・reconcileを実装した。
未保存変更はRunを止め、応答喪失時は元payload/keyを保持する。初回かつID未取得の厳密な`JOB_INPUT_REJECTED`/`admission=NOT_ATTEMPTED`だけ入力訂正を許す。
自動試験に加え、下記の範囲を実ブラウザーで追試した。新規手動実行の成功・失効中取消まで受け入れたとは扱わない。

## 確認した実機操作と検証

通常Go起動のHTTP/CLI→実Trino/Polaris/S3、隔離diagnostic serverの故障制御、unit/mockを別の証拠として扱う。
fixture準備・故障制御・清掃helperを通常利用者入口の証拠へ読み替えない。

| 検証 | 結果・証拠（`var/shirokuma/p4-3-acceptance/`内） |
| - | - |
| 通常HTTP/CLIの故障注入前 | 861項目。HTTP 678回、通常CLI 31回。`live/state.json`と各phase log |
| 3DB | SALES / FINANCE / `insights_e88387a5`で同名Jobを別UUIDで保存・手動実行。SQL/typed引数/Boolean alert、SQL→named input Ingest→WAP build/audit/publish→CSV/JSONLをHTTPと通常CLIで確認 |
| 実データ・pin | 各DBのraw/publishedのid 1/2/3、Exportの行/bytes/Snapshot、全childの本人・full9・backend user・credential ref・実backend query IDを照合 |
| 拒否境界 | 他owner/他DB/viewer/保存role違い、CAS/key競合、不正型/入力/action/偽造親を拒否し、対象SQL送信・run追加0を照合 |
| 名前・Ref | 1文字/64文字JobはHTTP/CLIで実行成功。65文字/大文字/pathを拒否。Table再作成の旧Refを最初のSQL前に拒否し、新Refへの明示CAS後は成功 |
| Job SQLのDB境界 | adminでも他DB SQLを保存/実行前に拒否する修正。修正前に保存だけした定義をrun時に拒否し、同DB SQLへのCAS後は成功。`live/state.json` identity追試 |
| step間drain | 受付済み2 stepは元pinで継続、新Job/通常SQLは拒否。同key再受付・実行中CASでも受付済みrevision不変、実POST 2回。`fault-gap-v2.log` |
| step間取消 | 元runをcanceled、後続stepをskip、実POST 1回。`fault-cancel.log` |
| UNKNOWN再起動 | 通常2サービス再起動後も同run/親をUNKNOWNで保持。同keyは元run、新keyは409、reconcileは終端証拠不足409。追加SQL intent 0。`retained-unknown-restart-confirmed.log` |
| 保存済み中間集計 | 945項目pass、HTTP 745回、CLI 31回。ただし途中で失敗したharnessと未実施項目を含む受入全体の合格を意味しない |
| migration | 実機state copyのv11→v12/新規/再接続で18項目pass。既存40table・6,551rowとschemaを保持。`migration-checks.json` |
| resident suite（ブラウザー追試のUI修正前） | 1,363件実行、1,331 pass / 32 skip。`resident-core-final.log` |
| UI / Go（ブラウザー追試前） | host上のWorkbench自動試験270件pass、shipped-handler DOM試験20件pass（別実行、実ブラウザー操作なし）。`go test ./cmd/... ./internal/...` pass |
| source gate | design-context 14試験・文書検証、`tofu fmt -check -recursive opentofu`、対象24pathのwhitespace検査pass。`source-gates-final.json` |
| doctor | local-experiment healthy。workload readinessの証拠でありJob受入完了を意味しない。`doctor-pending.json` |

FINANCEのCSV/JSONLは行順が異なったため、同一行の重複を保持したmultisetで照合した。`finance-export-order-proof.json`を保持する。
初回drain診断はgeneric OperationのFK記帳不足で失敗した。drain transactionはrollback、runはUNKNOWNを保持し、正のbackend照合で再送せずfailedへ終了した。`recover-gap-attempt.log`を保持する。

## 初回Workbench実ブラウザー追試（復旧前の時点記録）

通常8888の元のChromeタブで、更新済み受入用credentialにより再ログインした。パスワード保存の更新は選ばなかった。
次を実画面で確認し、`browser/browser-observations.json`、同directoryのPNG・AX記録へ保存した。

- SALES / FINANCE / 任意名DBのsandboxへ同名`p43_ui_context_job`を別UUIDで保存。SALES/FINANCEはv1、任意名DBはCAS追試後v3。
- 15秒の定期更新をまたぐ未保存文・入力focus・Run無効化、別DB往復後の下書き復元、Worksheet/Explorerと独立したJob context。
- 既存SQL 2タブのID・個別DB/Schema/role/SQLを保持。元から利用不可のcontextはそのまま拒否し、Job操作で書き換え・SQL実行をしなかった。
- 別の通常HTTP操作でJobをv1→2、v2→3へ更新した後、古いUI下書きの保存は失敗し、下書きを保持。明示再読込の確認後は一覧・編集欄・状態表示が最新v3へ揃い、Runが有効になる。
- 既存の5 step Jobと、以前の通常API実行による成功履歴・全child ID・要約を表示。これは今回Workbenchから新規runを成功させた証拠ではない。
- 必須の名前付き入力なしの要求は受付前拒否を表示し、編集と再Runを許可。入力名`batch`と実file `ui-input.json`を選択できる。新しいIngestは実行していない。
- 利用roleをadmin→data_engineerへ下げると、当該principalに利用可能DB grantがなく、Job定義・履歴・選択fileを消去。adminへ戻すだけでは復元せず、明示refreshで再認可する。grant自体の変更・失効試験とは分ける。
- 新規Runおよび元要求の明示照会は、既存UNKNOWNのため成功receiptを取得せず、結果不明表示とRun無効化を維持する。

実ブラウザーで、最新revision再読込後の一覧だけ旧v1のまま残り、dirty案内がCASエラーや読込成功状態を上書きする表示不整合を発見した。
`dbJobsLoad/render`に限定して、世代・要求・選択IDの照合、一覧のreceipt更新、下書きを保持したエラー表示、成功時の案内文更新を修正した。
修正後は実ブラウザーでv2→3のCAS競合・明示再読込・DB往復を再確認した。
実際のText nodeを用いる英日両方の回帰試験を含め、Jobs UI 22件とWorkbench全体276件がpass。
独立reviewと対象3試験もpass。`entry-native-cas-focused.log`、`entry-native-cas-workbench.log`、`entry-cas-independent-review.log`を保持する。
最後にJob詳細のlabelを既存訳の「実行コンテキスト」へ1行だけ変更し、receipt未取得の要求を「受理済み」と表示しないようにした。
この文言変更後もJobs UI 22件pass。`entry-native-context-label-focused.log`を保持する。全体276件はこの文言変更前の結果である。
最終reload後も元のSQL 2タブ・contextを保持し、拒否画面のlabelを実ブラウザーで確認した。`browser/workbench-final.png`が最終画面である。

UI操作前後のread-only比較は`ui-readonly-comparison-pre-fix.json`で9項目pass。
query 1,348・Job run 20・admission 1,860とUNKNOWN chainは不変。定義は23→25で、baselineにSALESのUI定義が含まれるため増分は2。
修正・最終文言変更・再追試後も`ui-readonly-comparison-final.json`が9項目passで、これらの件数とUNKNOWN chainは不変。
最終`current.json`とbaselineを別途照合し、DB/Schema・physical binding・8 grant・既存WAP marker・legacy Job件数も不変だった。
`facts-current-after-browser.log`は11項目中7項目pass。残る4項目は既存UNKNOWNに対応するためexit 1で、技術受入の合格とは扱わない。
通常Go入口はHTTP serverのstderrを破棄するため、これらのUI操作のHTTP access logは採取できていない。
画面表示とread-only台帳、別の通常HTTP CAS記録を区別し、架空のnetwork traceで補わない。`browser/http-pre-fix-log-availability.json`を参照する。

## 復旧前のUNKNOWN（時点記録）

以下は安全な実行閉鎖を追加する前の記録。現在の状態は次節を参照する。

応答消失診断の初回harnessは、POST応答の`nextUri`を通常のtoken登録前に取得しようとしてnetwork送信前に拒否された。
予定した「実terminal応答の消失」まで到達していない。実送信traceはPOST 1回と非terminal応答で、修正済みharnessの再実行はしていない。

- run: `def30a2c-3780-4f41-8e6d-bc80e87b1f29`、owner `both-db`、SALES/sandbox、generation `p42-live-final`。
- query: `213b77da-3738-42ea-ae77-9158c2212ebf`、観測済みbackend ID `20261021_013055_00133_utvt5`。
- Job 1件・DataOps 1件・query 1件と対応する3 admissionはUNKNOWNを維持。通常reconcileは`UNRESOLVED_CHILD_QUERIES`。
- backend既知IDのGETは410、一覧と保持ログにも終端証拠がない。410・不在・PID・TTLから成功/未実行/終端を推定しない。
- `faults/terminal-loss/trace.json`、`known-backend-observation.json`、`known-backend-metadata-status.json`、失敗logと再起動確認を保存した。

5DB READY、8 grant収束、両通常入口の世代一致、LOCAL制御はOPEN/管理ownerなし。一方UNKNOWNの解消条件4項目は不合格で、`current.json`は11項目中7項目pass。
OPEN表示からJob再受付・構成apply可能とは判断しない。grantは今回変更しておらず、既存のrole/epochを維持した。

残作業は、正の終端証拠に基づくoperator回復、修正済みterminal-loss診断、実行中grant失効と元pin取消・取得拒否・verified regrant、Workbench実操作、最終清掃/照合。
この時点ではMac解除後に上記の追試を実施したが、新規Run成功とgrant失効追試はUNKNOWNが残るため未完了だった。後述の復旧後追試とは別の記録として保持する。
元のSQL 2タブを削除せず、再開用fixture・private profile・監査履歴を保持し、隔離診断serverは停止した。
初回保留時のDB/Schema・8 grant・既存WAP marker・legacy Job件数の不変と、隔離port 8891/8893の非listenを`pending-summary-confirmed.json`で照合した。
UNKNOWNを含むSQLite一貫backup `pending-jobs.sqlite3`は0600、integrity/FK検査に合格した。台帳を復元・変更するための処理は実行していない。
この時点では、UNKNOWNとgrant epoch変更が重なる場合やbackend終端記録を取得できない場合のoperator回復契約をP4-9へ残していた。
現在のreconcileへ架空のterminal QueryInfoを渡す、台帳を直接終端化する、state復元でblockerを隠す方法は採らない。

## read-only UNKNOWNの実行閉鎖と再検証

ユーザーの明示的な復旧依頼に従い、単一の管理対象Trinoでread-only Job SQLだけを実行閉鎖するoperator境界を実装した。
永続run定義・DataOps payload・全query/admission・本人/context/pinを検証し、write不確定、typed child不確定、別context、無関係な未完了処理を拒否する。
Query結果はUNKNOWNを保持し、実行停止だけを`FENCED_READ_ONLY`とする。親DataOps/Jobは放棄によるfailed、後続stepはskippedで、SQLを再送しない。

- configured resident suiteは1,416件実行、1,384 pass / 32 skip。独立focused検証はRecovery 51/51、既存admission-entry 48/48。件数は重複するため合算しない。
- v12 stateコピーのmigrationは25/25。実生成bundleと実Docker/Kubeを使うscope照合は4/4で、source/copy不変を確認した。事前照合で見つかった生成file参照・Docker PID列と、全体試験で見つかった通常reconcileの応答互換性を修正した。
- live planは11/11。同keyは同operation、古いrevisionのapplyは拒否され、既存投影row・SQL/run/admission件数を保持した。CLIは意図的に詳細errorを隠すため、印字されたerror codeの検証とは扱わない。
- 実停止は2026-10-21 05:19:16 UTC。正確な旧ContainerのRunning=false / exited / ExitCode 143を削除前に永続化し、旧Container不在と新Containerのreadinessを確認した。同operationの観測専用reconcileでOPEN/revision 5へ収束した。
- 復旧直後factsは22/22、通常入口再起動後は23/23。復旧中のSQL増加0、元statementは1件のまま。新しい通常SQLだけが1件増え、`SELECT 43043`の実結果を確認した。所有者の通常取得6/6、5DB/8 grant・binding・既存commit/legacy/WAP markerを保持した。
- 修正済みterminal-lossは30/30、実process再起動は31/31、終了後factsは23/23。各実POSTは1回で、実metadataによりUNKNOWNを照合し、後続を送信せずfailedへ収束した。診断8893は停止した。

証跡は`var/shirokuma/p4-3-recovery-acceptance/`の`recovery-plan-safe-summary.json`、
`recovery-engine-proof-summary-final.json`、`facts-recovered-runtime.json`、`fault-phase-safe-summary.json`、`resident-core-v13-final.log`。
新規UI追試は`p4-3-acceptance/post-recovery-ui/`と`p4-3-recovery-acceptance/browser/`へ分離し、以前の失敗harnessと受入記録を保持している。
Macの手動解除後、3DBの新規UI SQL/型付きJobが各1件ずつ成功した。型付きJobの5段階（SQL、Ingest、WAP refresh、CSV/JSONL Export）はすべてsucceededで、初回SQLは1行、append後のraw/publishedと両Exportは2行。各DBで別の通常CLI SQL runも成功し、2行を確認した。これらの照合は累積294/294、sales-onlyの失効前取得を含め306/306。重複する累積件数を合算しない。

失効検証helperが`maximum_role: null`を送って400で拒否され、grant変更前の終了処理で受付済みread-only runをUNKNOWNにした。通常observerは一致する終端記録を取得できず、traceのbackend IDへの410も終端証拠とは扱わなかった。追加の専用recovery Operationで停止証跡を保存し、同operationをOPEN/revision 5へ収束させた。追加queryもUNKNOWN+FENCED_READ_ONLY、親はfailed、SQL再送0。2回目の停止中・再起動中のquery/run/admission件数は不変で、再起動後factsは25/25、LOCAL OPEN、未解決実行0。失敗harnessと観測記録を保持し、以後の失効検証は成功済みrunと通常public queryのcontinuationに限定する。

## grant失効・再付与と最終受入

3DBのUI/CLIとraw/published・CSV/JSONL・snapshot・子queryのcontext/pin照合は完了した。sales-onlyのWorkbench取得成功を確定後、通常grant APIでCAS 9→10の剥奪を行い、同じpublic continuationの200→403、旧run同key要求403、SQL/run/admission件数不変を確認した。再起動前のprivate pinを再構成せず、再起動後の新しい通常SELECTを1回だけ使って失効前の200を確立した。

Workbenchは定義・子query/operation/summary・履歴・draft・row・file入力を0にし、save/run/observeを無効化した。元の取消handleとしてrun ID/status/contextの最小headerを保持し、これを保護された結果の再取得とは扱わない。通常取得6経路はすべて403で、UI正規化factsを含む累積310/310を確認した。terminal queryへの取消HTTP 200はno-opであり、backend DELETE送信の証拠とは扱わない。

CAS 10→11のdesired engineer復元後もeffectiveはrevoked。既存triggerはdesired更新でもepochを増やすため、剥奪epoch 11→pending epoch 12となる。「pending中はepoch不変」という検証側の誤った仮定による7/8記録を保持し、source契約と実inventory空・continuation 403に基づく別の12/12記録で訂正した。権限やepochを直接修正していない。

実planの13/13照合後、新generation `p43-recovery-final`を適用した。OperationはAPPLIED/revision 17、全6step成功で、verify前はeffective revokedを維持し、backend verify後だけengineer/applied 11/epoch 13となった。5DB/20Schema/8 grant記録・bindingを保持し、再配備後factsは25/25、Node/boot・Trino/5 Polaris Deployment・PVCの同一性照合は5/5。新規DB bootstrapや専用Nodeの再作成は行っていない。

sales-onlyでDB/Schemaの明示再選択・同じ所有JobのLoad後、新規runがsucceeded/2行となった。旧contextのJob取得4経路は409 CONTEXT_MISMATCH、旧query metadata/continuationは403で、旧run・子query・operationを現行historyへ復活させなかった。失効中の403と世代変更後の409を混同しない。旧helperの「再付与後も全経路403」という誤った期待によるread-only失敗記録を保持し、context拒否契約に合わせたhelperで累積323/323を確認した。元SQLタブ2枚のSQL/DB/Schema/role/Run無効状態も比較一致した。通常Workbenchのadminでも最終generationの新規SQL Jobが2行で成功した。

最終generationの通常CLI追試は34/34。sales-onlyの新run取得はCLI終了0、旧contextの取得はCLI終了1と同一通常HTTPの409 CONTEXT_MISMATCHで、SQL/run/admission増加0だった。CLIはerror codeを印字しないため、終了1単独を403/409の証拠とは扱わない。3DBで別々のSQL Jobを各1件だけ新規実行し、すべてv1/2行で成功した。元のUI/CLI checkpointは不変、一時sales-only認証pairは削除済み。`post-recovery-ui/final-cli/`に独立証跡を保持する。

清掃前factsは25/25。新しいfixtureを先に清掃し、所有9 object・Export6実体・admin認証pairを削除、累積335/335で完了した。旧cleanupは最初の6テーブルのDROP完了後、同名tableの旧inc1診断SQLを新inc2へ流用して停止した。6件はTrino FINISHEDとresolver/Polaris 404を照合し、再DROPしなかった。既知所有の新inc2とWAP sandbox 6件は、個別Ref/backend UUID再照合→事前intent→各1回DROP→terminal receipt保存→404確認を行った。旧Export13実体・private profile4・input JSON3を削除し、対象manifest内の残存は0。旧state・失敗log・診断証跡は保持した。`old-cleanup-continuation-completion.json`に記録する。

清掃後factsは25/25。5DB・20Schema・物理binding・8 grant記録（active 7、finance-only/SALES revoked 1）、既存legacy 0/0/0・WAP marker 3件を保持した。query 1,425/run 38/Job 35/admission 1,994の履歴を保持し、LOCAL OPEN、未解決execution 0、歴史上のfenced UNKNOWN 2である。通常8888/8080は同じ最終generationで200、aux8891・診断8893は停止・port閉鎖を確認した。通常CLI doctorはhealthy（21 healthy・1 informational）。source独立最終監査、設計整合性14件、security契約34件は通過し、後述の既存secret scan制約を再確認した。

最終credential衛生検査は、現行fixture passwordとBasic encodingを非表示で読み、changed/new sourceと両受入evidence（画像・binaryを含む）をexact-byte検索した。想定外一致0、read失敗0、清掃済みprivate fileの残存0、canonical private JSON 4件のfile 0600/dir 0700を確認した。値・断片・一致行を表示せず、path/count/permissionだけを記録した。件数と範囲は`final-credential-hygiene.json`に保存する。現行の既知credentialに限定し、画像OCR・圧縮archive展開・未知secret全般の検査とは扱わない。

## credential衛生と既存制約

検証中のtool出力に受入用管理者fixture credentialが含まれたため、ローカルで更新し、旧credential 401・新credential 200を確認した。秘密値は記載しない。
現行credential/Basic encodedの限定監査は1,524file、想定外一致0、許可済みprivate JSON 4fileのみ一致、権限不備0。
これは時点・対象限定の監査であり、過去tool streamや未知secret全般の消去を意味しない。`credential-hygiene.json`、`hygiene-summary.json`を保持する。
ブラウザー追試と最終source修正後の時点監査は1,642file、想定外一致0、許可済み4fileはいずれも0600、read失敗0。
`hygiene-browser-summary.json`を保持する。先の1,524file監査とは別時点であり、件数を合算しない。
`make verify-security`は既存`opentofu/dev/variables.tf:125`のkey形式validatorを秘密鍵markerと判定するfalse positiveでexit 2。
既存変更のreset・scanner抑止はしておらず、全体`make verify`がgreenになったとは扱わない。`security-final.log`を参照する。
