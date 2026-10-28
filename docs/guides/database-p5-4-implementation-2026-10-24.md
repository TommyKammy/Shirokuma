# P5-4 WebUI・通常CLI実装と実機検証（2026-10-24）

状態: **P5-4実装・対象導線の技術検証完了**。Phase5全体は未受入であり、P5-5〜7とowner受入は未完了。以下は限定された実証の記録である。

参照: [Phase5計画](database-phase5-implementation-plan-2026-10-23.md)、[P5-0契約](database-p5-0-contract-2026-10-23.md)、[P5-3記録](database-p5-3-implementation-2026-10-24.md)。

## 実装範囲

既存DB管理画面に、名前・表示情報、初期アクセス、影響確認、bootstrapとfullそれぞれの計画確認・明示承認、進捗、READY後の新SQLタブを追加した。通常CLIは既存`database create`を保持し、`database creation`から同じ公開契約を使う。

公開進捗と`next_actions`は現在の正本・認可・永続停止・照合証拠から導く。履歴の`OPERATOR_UNAVAILABLE`を現在の障害と混同せず、GETから準備・claim・dispatch・成功確定を行わない。実認証失効時の安全STOP、厳格な新規受付、二段階承認、UNKNOWNの照合を維持する。

UI/CLIは凍結した環境・key・bodyを保持し、POSTの`X-Shirokuma-Environment`で環境切替を拒否する。Workbenchの選択URLはfragmentへ保存し、通常reloadのHTTP requestにcreation queryを送らない。既存serverのquery検証は緩めていない。

## 実機で確認済みの範囲

専用環境は`shirokuma-p54-clean-20261024`。既存primaryと停止中の過去7環境を保全対象とする。

| 対象 | 現在の結果 |
| - | - |
| WebUIの作成導線 | DEのみのgrantで`p54ui`を作成し、bootstrap/fullを別々に確認・明示承認。作成revision 8でREADY、fullはAPPLIED |
| 通常CLIの作成導線 | BIのみのgrantで`p54cli`を作成。admin/executorへの暗黙自己grantなし。bootstrap/fullの別review・別承認後、revision 8でREADY、fullはAPPLIED、6 step成功 |
| SQLへの引継ぎ | 空のread-only新規タブを開いた時点の自動queryは0。その後の明示`SELECT 1`はFINISHED |
| 既存作業の保持 | 元のSQL/draftとEN設定を保持。fragment URLの実reload前後で対象3 ledger tablesのhashが同一 |
| 表示・session | JP表示と520px幅で操作controlsが収まる。role ceiling低下で管理導線が隠れ、新sessionでAdmin表示へ復帰 |
| 通常CLIのSQL・分離 | BIの`p54cli`、DEの元`p54ui`、adminのbaselineで実queryがFINISHED。DEの`p54cli`とBIの`p54ui`は拒否。3成功／2拒否で、拒否による新query/context/admission/backend SQLは0 |
| 受付・権限制御 | CLIの非admin作成、処理中排他、grant上限超過、他adminの変更を拒否。対応するroot/request/planの新規増加は0 |
| 環境precondition | 両通常HTTP originへの誤環境POSTが409 `ENVIRONMENT_MISMATCH`。対象ledgerの変化は0 |

有限証拠: [READY後の通常CLI・DB分離](../../var/shirokuma/p5-4-acceptance/private/clean/ui-ready-normal-cli-isolation-v1.json)、[元作業の保持](../../var/shirokuma/p5-4-acceptance/private/clean/webui-new-sql-preservation-v1.json)、[新タブの自動実行0](../../var/shirokuma/p5-4-acceptance/private/clean/webui-open-sql-no-auto-execution-v1.json)、[実reload](../../var/shirokuma/p5-4-acceptance/private/clean/webui-reload-result-v2.json)。

認可の証拠: [有限negative確認](../../var/shirokuma/p5-4-acceptance/private/clean/public-negative-guards-v1.json)、[両originの環境不一致](../../var/shirokuma/p5-4-acceptance/private/clean/public-environment-mismatch-v2.json)。表示の証拠は[JP](../../var/shirokuma/p5-4-acceptance/private/clean/webui-ready-ja-v1.jpg)、[520px](../../var/shirokuma/p5-4-acceptance/private/clean/webui-narrow-progress-v1.jpg)、[実SQL完了](../../var/shirokuma/p5-4-acceptance/private/clean/webui-sql-finished-v1.jpg)。session表示の確認とoperator実認証失効時の永続STOPは別の受入項目である。

通常CLIは[bootstrap review](../../var/shirokuma/p5-4-acceptance/private/clean/cli-bootstrap-review-ro-v1.json)・[承認受付](../../var/shirokuma/p5-4-acceptance/private/clean/cli-bootstrap-approved-v1.log)から、[full review](../../var/shirokuma/p5-4-acceptance/private/clean/cli-full-review-ro-v1.json)・[別承認](../../var/shirokuma/p5-4-acceptance/private/clean/cli-full-approved-v1.log)を経て[READY](../../var/shirokuma/p5-4-acceptance/private/clean/cli-ready-progress-v1.json)まで確認した。[最終SQL分離](../../var/shirokuma/p5-4-acceptance/private/clean/final-normal-cli-sql-isolation-v1.json)を現時点の証拠とする。受理済みfull ACKを同一key/bodyで再取得しても、新request/dispatch効果はない。[無副作用確認](../../var/shirokuma/p5-4-acceptance/private/clean/cli-full-historical-ack-no-effects-v1.json)、[ACK再取得](../../var/shirokuma/p5-4-acceptance/private/clean/cli-full-historical-ack-retry-v1.log)。履歴ACK receiptと現在statusは別に照会する。

資格情報継承の正本は[最終v2](../../var/shirokuma/p5-4-acceptance/private/clean/final-cli-ready-credential-continuity-ro-v2.json)：`CREDENTIAL_CONTINUITY_PASS`、errors 0、全check true。password/verifier件数は4→7→10、baseline 11／UI 16／current 21のSecret論理fieldを保持した。v1診断は過去operator設定から現在の共有ToFu stateを参照したため、原観測を保持し、v2で各時点のsavedplan-after／次plan-beforeへ束縛し直した。v1をPASSとせず、古いstate pathからrotation・再発行を推定しない。[最終doctor](../../var/shirokuma/p5-4-acceptance/private/clean/doctor-final-three-ready-v1.json)はbaseline・p54ui・p54cliの3 READYでhealthy。p54stopは別のREGISTERED/BLOCKEDである。

## 回帰検証と証拠の範囲

- venvによる`verify-local-experiment`は76 groups / 2178 cases、native exit 0。ただし最終修正前のsnapshotである。[実行記録](../../var/shirokuma/p5-4-acceptance/private/verify-local-experiment-venv-v3.log)
- 最終native creation suiteは215 tests / 166.216秒、exit 0。[最終creation](../../var/shirokuma/p5-4-acceptance/private/final-creation-focused-v1.log)。最終native Workbench suiteは401 tests / 36.907秒、exit 0。[最終Workbench](../../var/shirokuma/p5-4-acceptance/private/final-workbench-focused-v2.log)。先行398 tests / 34.501秒の[記録](../../var/shirokuma/p5-4-acceptance/private/final-workbench-focused-v1.log)も保持する。
- workerのUI 38 tests、production JS構文検査、対象diff検査もPASS。実HTTP selectorと既存static handlerでfragment URLの200、旧queryの400維持を確認した。38件は全Workbench suiteと合算しない。[修正のfocused結果](../../var/shirokuma/p5-4-acceptance/private/ui-fragment-worker/result-v1.md)
- 最終client 46 PASS。[結果](../../var/shirokuma/p5-4-acceptance/private/client-stop-projection-worker/result-v1.md)。最終backend STOP guard 45 focused PASSはcreation 215のsubsetで重複し得る。先行backend 93／client 42の[backend記録](../../var/shirokuma/p5-4-acceptance/private/backend-projection-worker/result-v3.md)・[client記録](../../var/shirokuma/p5-4-acceptance/private/cli-environment-native-focused-v1.log)、変更のないGo sourceに対する[native Go CLI PASS](../../var/shirokuma/p5-4-acceptance/private/cli-native-go-v2.log)も保持する。件数は合算しない。
- [最終技術source review](../../var/shirokuma/p5-4-acceptance/private/final-technical-source-review-v1.json)は8 checks PASS。元125 sealのbytesを保持し、承認済みclient/backendの2 source差分を明記した。現在の125 filesが全て不変という意味ではなく、resealなし。protected renderer 4＋P53 factory 2、P54 private harness 8 v2は不変、最終HTML hashは実browser STOP証拠と一致する。[先行限定レビュー](../../var/shirokuma/p5-4-acceptance/private/ui-fragment-worker/review-v1.json)も保持する。最終design-context・scope auditの結果は[最終closeout](../../var/shirokuma/p5-4-acceptance/private/final-acceptance-closeout-v1.json)に別記する。

## 永続STOPと新規受付の最終確認

初期grantなしの`p54stop`でexecutorの実認証を失効させ、永続principal STOPを確認した。[通常CLI最終確認](../../var/shirokuma/p5-4-acceptance/private/clean/stop-normal-cli-final-v1.json)はBLOCKED／PRINCIPAL_STOPPED、許可された安全操作のみを示す。資格情報を復元しても自動再開せず、履歴CREATE ACK再取得後も現在はBLOCKED、client planはACTION_NOT_AVAILABLEである。

実Chromeの最終reloadで3件を再発見し、CLI READYからSTOPへ選択すると直ちにBLOCKED／PRINCIPAL_STOPPEDとなり、reconcile／withdrawのみ、SQLなし。load-stopと選択renderのraceを修正し、古いREADY／SQL操作を残さない。exact 7 DB table hashesと12 dispatch filesは不変。[最終UI証拠](../../var/shirokuma/p5-4-acceptance/private/clean/webui-stop-final-v1.json)、[画面](../../var/shirokuma/p5-4-acceptance/private/clean/webui-stop-final-v1.jpg)。

元の`stop-new-plan-after-auth-restored-v1.json`は202とrequest／owner request file各1追加（`no_new_work=false`）の失敗観測として保持する。backendのfresh PREPARE／REPLANに同一transaction内のexecutor STOP gateを追加した。[最終独立cold HTTP probe](../../var/shirokuma/p5-4-acceptance/private/clean/backend-admission-v1/negative-http-actual-v1.json)は`passed:true`、実認証session 200、fresh plans／replan 403 PRINCIPAL_STOPPED、旧CREATE 200、SQLite全体と全16 journals不変、probe所有HTTPは終了した。

このprobeは修正後fresh handlerへの実HTTPであり、`foundation_ready=false`、`owner_ready=false`、`managed_restart_verified=false`、`unavailable_foundation_limitation=true`。`zero_dispatch_inferred=true`、`dispatch_directly_measured=false`であり、managed Go ownerの通常再起動／再配備やdispatch hook直接測定を証明しない。STOP解除後の明示RELEASE・新承認を含む故障／再起動matrix全体はP5-5/6に残る。

## 有限清掃と清掃後の保全

[known process停止](../../var/shirokuma/p5-4-acceptance/private/clean/final-known-stop-v1.log)はexit 0、STOPPED、known_processes 7。専用P54 containerを停止し、4 volumes／1 networkを保持した。停止routineの最終7 handle一覧は永続保存されていないため、未記録のchild PIDを補わない。

独立した[清掃後RO確認](../../var/shirokuma/p5-4-acceptance/private/clean/post-cleanup-primary-retained-ro-v1.json)（0600）は138/138 PASS、errors 0。primaryの6 PIDs／auth 3、8 kubeconfig／CA、node／system UID、8080/8888 runtime、schema 19、P5 tables 0、FENCED_UNKNOWN 3、lowercase unknown tick 1、未閉鎖admissionなしを確認した。過去7環境STOPPEDと28 volumes／7 networks、元P53 partial query `936...`とadmission `df889...`のUNKNOWNは不変。P54 container STOPPED、4 volumes／1 network保持、指定5 listener ports不在、保存済み過去process identity不在を確認した。RO監査によるsource／SQL／auth／runtime変更はない。

bootstrap時点の111 PASS、元projection FAILと修正、[enrollment後比較](../../var/shirokuma/p5-4-acceptance/private/clean/post-enroll-primary-retained-ro-v1.json)は各時点の証拠として保持し、最終清掃後証明と区別する。Chrome agent tabは閉じた。temporary IABはcloseがURL policyに阻まれ、残る終了経路はturn終了時の自動cleanupであり、全tab閉鎖済みとはしない。

## 保存する履歴と受入境界

専用transportの当初stageはUNKNOWNとして原記録を保持する。所有資源に限った明示reconcileのPASSは別証拠であり、元UNKNOWNを成功へ書き換えない。[当初観測](../../var/shirokuma/p5-4-acceptance/private/clean/transport-failure-ro-v1.json)、[reconcile後のRO](../../var/shirokuma/p5-4-acceptance/private/clean/post-reconcile-ro-v2.json)。

P5-3 partial環境のquery UNKNOWNも未解決の保存履歴として停止環境とともに残す。今回のREADYや専用transportの回復と混同せず、retry／cancel／reconcile／resetは行っていない。P5-4の技術検証とP5-7のowner受入は別であり、この報告はP5-5〜7の完了や最終owner受入を宣言しない。keyboard／200% zoomや将来matrix全件のPASSも主張しない。
