# P5-3 実dispatch・世代引継ぎ 実装報告

**状態：P5-3の実装・専用実機の技術確認済み。同じenrollmentで2件のREADYと失効回復を確認し、専用環境を停止保全した。Phase 5全体は未受入。**

対象は[Phase 5計画](database-phase5-implementation-plan-2026-10-23.md)のP5-3と
[P5-0契約](database-p5-0-contract-2026-10-23.md)。
P5-1の正本・承認・委譲・永続STOPと、P5-2の増分材料・bootstrap/full分離を利用する。
新しいWebUI作成画面と通常CLIの作成UXはP5-4、全体技術受入と所有者操作性受入はP5-6/7で扱う。

## 実装した境界

- Workbench/nativeのGo ownerが任意のenrollmentを自動採用せず、明示されたprivate enrollment、
  固定loopback origin、固定runtime profileに結び付いて起動する。
- private IPCの固定actionとrequest単位の永続journalから、固定Python one-shot workerを起動する。
  GETはworkerを起動せず、重複要求は元のjournalを再発見する。応答不明の処理を再送しない。
- HTTP childとoperator workerは異なる実行寿命を持つ。HTTPの世代切替でoperatorとIPC ownerを終了させない。
  継承FDのEOFは次の外部効果と最終更新の停止条件となる。HTTPのREADYは実bind後に返す。
- full検証は両ownerを封印されたcandidateへ引き継ぎ、両方の実runtime attestationとSQL証拠が一致してから完了する。
  bootstrapではruntimeを切り替えない。既存runtime APIとの互換性を保つ。
- activation IDを安定したjournal識別子にし、recovery要求UUIDが変わっても既存ACKを再実行しない。
  INTENTや欠落した応答からUNSENTを推定しない。
- 新規activationでは、固定descriptorと既知の旧HTTP childを確認し、resolver前に自己の未送信leafを永続記録する。
  これは効果ゼロの観測である。実効果には現在のresolver・材料binding・各leaf認可を引き続き要求する。
  失効でAUTH_FILEが変わった場合にも、実際に未送信だったことを正に観測できる。
- 2件目以降の材料生成は、同じenrollmentの直前のAPPLIED fullと成功した検証receiptを正本から確認して継承する。
  中断回復では、元planに封印された直前profileを使い、片側が新世代、もう片側が直前の成功世代という状態を観測する。
- fullのUNKNOWN回復は別のVERIFY_RECOVERY承認・claim・typed permitを使う。完了済みSQLを再POSTせず、
  正にUNSENTと判定した残りの処理だけを進める。観測完了後も通常fullの新しい承認を経てAPPLIED/READYにする。
- 実際の認証読込で要求者／executorの失効を検出して永続STOPと承認撤回を行う。
  private RELEASEは実ownerの読取証拠と停止epochを再確認し、資格情報の復元だけでは再開しない。

## 互換性と変更範囲

既存公開DTO・作成受付／承認route・schema 19を維持した。managed enrollmentを指定しない起動は既存経路を保ち、
未設定の作成能力は停止する。既存CLIとSQL/DataOpsの正本は共通のguardを利用する。
4つのrenderer fingerprint入力は変更していない。

作業開始時の395ファイルに対し、既存379件一致・許可範囲のproduct15件と計画文書1件の変更・欠落0。
新規productファイルはGo owner/testとPython bridge/worker/recoveryおよび対応testの8件。
開始前からの変更は保持している。

## 検査

| 検査 | 現在の結果 |
| --- | --- |
| Go CLI全体（native） | 266 PASS、build成功 |
| cross-origin重複dispatch修正後のcreation/auth/recovery/bridge/worker/preparation/routes（native） | 145 PASS |
| `make verify-local-experiment`（通常SQL foundation・重複dispatch修正後、既存project venv指定） | 75群・2,078件、2,045 PASS／33 SKIP／失敗0 |
| private foundation/transport/fault/停止後観測 harness（実Docker Gateway対応後、native） | 168 PASS。productの実操作受入とは区別 |
| source独立レビュー | owner・dispatch・失効前記録・連続作成と歴史的profile回復を確認 |
| design-context | `make verify-design-context`成功。関連14 testsと文書整合checkを通過 |

内側workerのsandboxでps／socketが拒否された検査はnativeで再実行した。
system PythonのPyYAML不足は、既存project venvを指定して解消した。新しい依存導入は行っていない。

## 専用実機の記録

private証拠は`var/shirokuma/p5-3-acceptance/private/`に保持する。秘密材料・capability・認証headerは公開報告へ転記しない。
primaryと停止保全環境、専用foundation、SSH設定等は事前の94項目でdrift 0を確認した。

初回positive環境では、helperのfull-apply呼出しに必須binary引数が不足し、正本のapply前に停止した。
元UNKNOWNを変更せず、正本fullがAWAITING_APPLY・未送信であることを読取りで分類し、既知pairと所有foundationを停止保全した。

新しいpartial環境では実bootstrap/full baselineを適用・検証してREADYにし、既知pairを停止後に実enrollmentを登録した。
Pod再作成をまたぐ接続のため、元Serviceを保ち、固定NodePort Serviceと既知SSHを使う。
最初の接続試験は実NetworkPolicyに拒否され、元のUNKNOWNとService作成ACKを保持した。
NodePortと対象Pod拒否カウンタの対応を確認し、専用VM bridgeの実経路source `/32`からS3 TCP8333／Trino TCP8443への
2本の追加Ingress policyだけを別の有限手順で作成した。元policyは保持した。
別intent・別known-process記録によるSSH接続と、署名付きS3 HEAD／CA検証・認証付きTrino GETは実機で成功した。

| P5-3実機確認 | 状態 |
| --- | --- |
| 実Go owner／FD／両通常originの起動 | 両caps configured/available、実Go ownerと継承FDを確認 |
| 通常作成のbootstrap→full→両runtime一致→READY | negativeのroot1はPREPARED→APPLIED、root READY8、両activation ACK。Go owner維持・HTTP child更新 |
| 同じenrollmentで2件目の増分作成 | 直前のAPPLIED full材料を継承。root2も最終READY16、両runtimeが3DBの同一世代に一致 |
| 逆originからのduplicate | native CREATE201→Workbench同key/body200、同ACK、両ownerの要求journal byte hash・activation内容一致、新worker 0 |
| Workbench切替ACK応答喪失／native未送信のUNKNOWN | 実Go ACKを受信後に返却だけを遮断。公開full UNKNOWN、実観測でWB COMPLETE／native UNSENT |
| recovery中の実executor失効、永続STOP、旧承認撤回 | 実AUTH_FILE読取で失効を検出。STOPPED epoch2、元fullと失敗recovery承認はREVOKED、native 3 leaf UNSENT |
| 正確な資格情報復元→private実owner観測→RELEASE→新承認→READY | 復元だけでは新承認403。実Go OBSERVE→最新frozen一致→明示RELEASE epoch3→新VERIFY_RECOVERY→別の通常full承認でREADY |
| 完了済みSQL／activationの再利用 | 検証SQL9件の全object・backend IDを保持、新SQL POST intent 0。最終full承認で両ACT journal不変 |
| 通常CLI・権限／DB分離・doctor | base1件、新DBのDE/BI2件、回復後の既存DB DE／新DB BI2件が実FINISHED。権限外4件はquery/context/admission増分0。doctorは各READY後healthy |
| 有限停止・primary／停止保全環境の事後不変確認 | 既知7 processと所有containerだけを停止、4 volume＋1 networkを保持。最終ROは188 PASS／失敗・error 0 |

この表はP5-3範囲の技術確認であり、新WebUIの操作性やPhase 5全体の受入を代替しない。

停止後の`negative/final-stopped-primary-retained-ro-v1.json`でprimaryのquery 2,794／admission 3,993、
OPEN revision155、P5作成行0、既存FENCED UNKNOWNとscheduler UNKNOWN、認証3ファイル・profile・原6 PIDを再取得し一致を確認した。
保全済み6 container・volume/network、renderer4入力・factory2ファイル、current source144件にもdriftはない。
Pod24件は現在のsnapshotだけで、過去のUID／restart数との一致を主張しない。
negativeの正本には3DB READY、作成2件のAPPLIEDとgrant6件が残り、通常query/admission各5件はすべてFINISHED、UNKNOWNは0。

現在の専用環境は`negative`（enrollment `f5bc2531-d405-4d9b-abab-f308bc16af4b` revision1）。
root1は`c7ab7154-9781-4d17-b924-a81be7d7050e`、root2は`8c40f376-b6fb-495c-8754-242e45ac56ed`。
適用・回復中はGo owner PID 3558／3562を保持し、HTTP childだけを世代ごとに切り替えた。
bootstrapで置換される世代Secret 3件は既存資格値を含めて保持し、DBデータ・PVC・namespaceの削除は0。
bootstrapのPREPARED後もwarehouseはAPPLYINGのままであり、full検証後だけOPENになる。
旧Secret名を参照するruntimeの即時可用性は、この明示maintenance区間では保証しない。

実機証拠は`negative/public/`、`negative/cli/`、`negative/recovery/`と各operator reviewに保存した。
特に`recovery/initial-observation-{frozen,receipt}-v1.json`、`post-stop-observation-*`、
`verification-complete*`、`final-reuse-proof-v1.json`で各境界を分け、後段のreceiptを初期状態の証拠へ転用していない。
通常CLIのHTTPエラーstatusは直接捕捉していないため403とは断定せず、実auth/grant、正常なendpoint、
各要求の正本増分0とguard順序で拒否を確認した。Createの4拒否とSTOP後の新承認403は実HTTPで確認している。
新しい画面の操作性、全targetのsmoke、再起動・再配備を含む全体受入はP5-4〜7で扱う。

### 通常SQL接続のisolation修正

最初のBI CLI queryはUNKNOWNとなった。managed HTTP childに専用KUBECONFIGがなく、
通常SQLの既存一時port-forwardが既定のprimary contextを参照していた。
queryとadmissionのUNKNOWN、backend ID未取得を保持し、SQLを再送していない。
primaryのquery/admission件数は事前値と一致し、P53 actorのquery/admissionは0。
保護環境の134項目は訂正済み投影でdrift 0を確認した。

実Managedのenrollmentから固定kubeconfig/contextを明示する修正をP5-3で行った。
base profileと封印されたfull候補をそれぞれ検証し、live API/CA/node UIDとstate lineageを照合してから
固定namespaceのport-forwardを起動する。managed経路にambient contextへのfallbackはない。
既存recover-job経路には単独queryを偽のJobとして渡さない。
UNKNOWNの外部結果を未送信／成功へ書き換えず、明示したprivate operatorの隔離証拠と新しい検証境界を残す。
旧source manifestとroot1のseal／receiptを保持した。partialの既知Go owner・SSHと所有containerを有限停止し、
volume・ledger・元UNKNOWNを保存した。primaryの停止・取消・FENCEは行っていない。
単独queryの一般回復機能は追加せず、元UNKNOWNは未解決制約として残す。
修正後の再検証には別container・network・volume・CA・node UID・state・enrollmentのnegative環境を使った。
初期base profileへの通常CLI SQL、同一enrollmentでの2件のmanaged作成と故障回復を、新しい証拠として扱う。

negativeではbaselineを実READYにし、managed起動後・最初のCreate前のCLI queryがFINISHEDとなった。
実backend query IDと終了したadmissionを独立に確認した。両originのcapabilitiesは利用可能で、
DE/BIの4通りのCreateは403、canonical作成・要求行0、owner効果0だった。

### originをまたぐ重複再取得の修正

negativeの最初のWorkbench CREATEをnativeから同一key/bodyで再取得した際、公開ACKは同一だったが、
native ownerにも登録確認用one-shotが起動した。元の両Go journalとv1 source manifestを保存した。
CREATE workerは登録を照会するだけで、claim・phase plan・材料生成・SQLなどの外部効果はなかった。

共通HTTP handlerを、同じtransactionで得た新規受付判定に基づいて新しい要求だけdispatchするよう修正した。
既存の公開ACK/statusと直接呼出しを保ち、最初のdispatch失敗後もduplicateからの再送を許さない。
初managed plan前にreviewed source v2を有効化した。差分は共通handlerの1ファイルで、baseline20 pins・enrollmentの内容/revision・ownerは維持した。
root1は一意keyで進めた。
通常full handoffで両HTTP childが修正後のhandlerを読み込み、root2の逆origin duplicateでworker増加0を実測した。
修正前の重複はPASS根拠に用いない。

## P5-4へ引き継ぐ公開進捗表示

実capabilitiesが両入口とも`configured=true / available=true`で処理が進行している場合にも、
作成詳細GETは`blocker=OPERATOR_UNAVAILABLE`を返す。P5-1の`_summary()`に残る固定fallbackであり、
dispatch・claim・実行認可・phase状態・READY判定はこの表示値を利用しない。
P5-3の実dispatch／runtime引継ぎと、公開GETのblocker／next_actionsの表示受入を区別する。

P5-4の先頭で、現在gate・関連要求・実readinessからprojectionを作るよう修正する。
SQLite transaction外でreadinessを照会し、正常な実行中／承認待ち、実owner不在、STOP→明示RELEASE後、UNKNOWNを確認する。
過去の`stop_reason`を現在のblockerとして残すケースも確認する。固定値を単純にnullへ置換して実障害を隠さない。

実観測で`VERIFY_RECOVERY`候補を保存済みでも、`RECONCILE`要求行には受付時の
`BLOCKED / OPERATOR_UNAVAILABLE`が残る。観測の完了は現在receiptと一致するfrozen planで確認し、
この初期表示だけを根拠に観測を再送しない。P5-4ではこの要求行と公開進捗の整合も修正対象に含める。

executorだけをSTOPした場合、権限を保持するrequesterは旧keyで同じACKを取得できる。
これは`fresh=false`の照会であり、旧承認の再有効化やdispatchではない。
資格情報復元後も旧承認は`REVOKED`、新しい承認要求は実HTTPで`403 PRINCIPAL_STOPPED`となる。
UIはACKの再取得を再開成功と表示せず、現在のgateとphaseを照会する。
