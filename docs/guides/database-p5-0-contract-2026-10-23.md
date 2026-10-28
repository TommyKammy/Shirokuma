# P5-0 DB作成の公開・private契約 v1

2026年10月23日。[Phase 5計画](database-phase5-implementation-plan-2026-10-23.md)の契約固定。
[ADR-0037](../design/07_ADR/ADR-0037_Use_Bounded_Database_Creation_Delegation_and_Runtime_Handoff.md)を具体化する。
**以下のroute・table・委譲・IPCはP5-1〜5で実装する固定仕様であり、現機能ではない。**
今回の環境確認・保持対象・未充足条件は[P5-0前提確認](database-p5-0-preflight-2026-10-23.md)を参照する。

## 1. 境界と正本

- 公開HTTPのcollectionを`C=/api/v1/admin/database-creations`に固定する。通常CLIも同じ公開契約を利用する。
  environment/warehouse/profile/接続先は設定済みserverから解決し、clientに選ばせない。確認画面には対象環境を表示する。
- 既存のDB登録専用APIはREGISTERED、既存の公開applyはrequires_operatorのまま残す。
  既存の非P5 manual operatorは同一actor認可を維持する。P5関連Operationを旧経路から実行する迂回は拒否する。
- DB lifecycle、既存Operation、6 step、発行receipt、binding、grant epoch、warehouse controlが正本。
  新規記録は作成の関連・承認・受付/実行claim・停止に限定し、既存stepの別実装や成功台帳を作らない。
- bootstrapはPREPAREDで止まり、fullの別承認を必要とする。full確認待ちもwarehouseの利用制限は続く。
  READYは既存verified fullの最終transactionのみで確定する。GET/poll、202、owner ACK、Pod Readyでは確定しない。

## 2. 公開API

### 共通規約

既存認証と単一の`X-Shirokuma-Role: admin`を使い、現在のaccount有効性、maximum admin、platform adminを検査する。
P5は明示設定された認証accountを必要とし、従来の未設定trusted-loopback/local modeをP5管理権限へ読み替えない。
Originがあるmutationは既存same-origin検査を通す。重複role/key header、未知field、型違いは400。
全mutationに単一の`Idempotency-Key`を要求する。長さ1〜256の非空文字列、NUL/CR/LFを拒否する。
scopeは`(authenticated actor, server environment_id, canonical route, key)`。payloadは型検査後のcanonical JSONで比較する。
初期grantはprincipal順に整列して比較するが、SQL名・表示名の正規化、入力不備の黙示補正はしない。
同scope・同payloadは保存した同一ACKを返し、異payloadは409。現在認可を検査してから再発見する。
同キー再送は新規実行ではない。ACKに含まれる受理時revisionと、GETが返す現在revisionを区別する。

有効なplatform adminは全作成の管理要約を読めるが、公開mutationは元の要求者だけに許す。
別adminの再開・所有権移譲は403。独立executorによるprivate観測は別契約とする。
応答は`Cache-Control: no-store`。root revisionだけで子step進行をcacheしない。

| Method / route | body | 応答・効果 |
| - | - | - |
| GET `C/capabilities` | なし | 200。対象環境のpublic ID、warehouse ID/revision、configured/available、blocker code。GETは準備・dispatchしない |
| GET `C/principals` | なし | 200。現在の有効accountの`principal_id,display_name,maximum_role`だけを返す。初期アクセス選択用で、認証材料・platform flag・無効accountは返さない |
| GET `C/runtime-instance` | なし | 200。両通常HTTP originに§5のP5 instance readbackを追加。既存`/api/v1/runtime`は変更しない |
| POST `C` | 下記Create | 新規201、同key再発見200。同一transactionで受付予約・root・DB/固定Schema/binding・表示metadata・初期desired grantを登録 |
| GET `C` | なし | 200。`limit` 1〜100、opaque cursorの管理一覧。cursorは環境と認可scopeに束縛 |
| GET `C/{creation_id}` | なし | 200。現在状態、計画要約、blocker、許可された次操作を返す |
| POST `C/{id}/plans` | `{expected_revision,phase}` | 202。bootstrapまたはfullのprivate計画準備を永続依頼。fullは当該rootのPREPARED predecessorを必要とする |
| POST `C/{id}/phases/{phase}/approve` | 下記Approve | 202。表示済みの固定計画だけを承認し、対応するapply依頼を一度受理する |
| POST `C/{id}/withdraw` | `{expected_revision}` | 202。未完了承認を撤回し新しいdispatchを停止。in-flight効果は保持し、DB削除・rollback・受付予約解放を行わない |
| POST `C/{id}/replan` | `{expected_revision,phase}` | 202。旧計画を残して次の計画番号を予約。新候補は新承認が必要。未分類の外部効果があれば409 |
| POST `C/{id}/reconcile` | `{expected_revision}` | 202。private observerによる照合依頼。clientからevidence/outcome/NOT_EXECUTEDを受け取らない |
| POST `C/{id}/phases/full/recover-verify` | Approveに`recovery_digest`を追加 | 202。§6の固定verify recovery計画を別途明示承認。元UNKNOWNの書換えや一般applyを許さない |

Createは次の形とする。`display_name`省略時はSQL名、`description`省略時は空文字、`initial_grants`省略時は空配列を
serverが明示的に補完してから冪等比較する。空の配列と暗黙自己grantを混同しない。

```json
{
  "sql_catalog": "p5_demo",
  "display_name": "受注分析",
  "description": "部門の分析用DB",
  "initial_grants": [{"principal_id": "existing-principal-id", "maximum_role": "data_engineer"}]
}
```

SQL名は既存の`[a-z][a-z0-9_]{0,30}`と予約語・重複検査を維持する。固定4 Schemaは現profileを利用する。
表示名は1〜128 Unicode文字・空白のみ不可・Cc不可、descriptionは0〜2048文字・CcはCR/LF/TABのみを許し、
既存`display_management.validate_payload`と同じUTF-8/literal処理を使う。
初期grantは既存の有効principal、`bi_viewer|data_engineer|admin`、accountのrole ceilingを検査し、重複principalを拒否する。
`revoked`は初期grantに使わず、未付与は空配列で表す。roleの別名変換を追加しない。
principal一覧のdisplay_nameは既存の非秘密account表示名を使い、未登録なら既存login nameを表示用に使う。
表示用の新しいaccount CRUDは作らず、password/salt/hashを一覧へ含めない。
profile_id、actor、SQL、任意Schema、endpoint、command、private材料はCreateで受け取らない。

ApproveはUI/CLIが既に取得した計画から次を送る。利用者に技術IDを手入力させない。

```json
{
  "expected_revision": 7,
  "plan_id": "server-plan-id",
  "operation_id": "existing-operation-id",
  "expected_operation_revision": 3,
  "delivery_digest": "public-delivery-digest"
}
```

各revisionはboolを除く正の整数。phaseは`bootstrap|full`のみ。計画が変われば409で新しい確認へ戻す。
`recovery_digest`は実観測から生成した固定recovery計画のdigestであり、任意stepの選択肢ではない。
新規ACKは`{request_id,creation_id,action,phase,accepted_revision}`を保存する。phase不要なactionではnullとする。
202は受理を意味し、以後はLocationの作成詳細をGETする。既存rootでbridgeが停止しても保存済みACKを失敗へ変更しない。

作成詳細のallowlistは、root/DBのpublic ID、sql_catalog/表示metadata、要求者ID、root revision、DB lifecycle、
初期desired/effective grant要約、phase/plan/Operation/generation IDと現在revision、public digest、固定6 stepの状態、
進捗文言code、blocker code、next_actions、現在認可のaccess_sourceに限定する。
`access_source`は`effective_grant|platform_admin|none`。READYでも一般principalはgrantなしでは利用不可だが、
現行platform admin＋選択adminの独立権限は維持し、その権限による利用を表示する。暗黙の自己grant行は作らない。
manifest/observation/private receiptを丸ごと返さず、秘密値、raw ToFu JSON、private path、nextUri、認証headerを除外する。

P5-4の進捗projectionは`progress_code`を`PREPARATION_PENDING|PLAN_REVIEW_REQUIRED|APPLYING|FULL_REVIEW_REQUIRED|VERIFYING|READY|REPLAN_REQUIRED|RECONCILE_REQUIRED|STOPPED|BLOCKED`に限定する。
各planの`public_summary`は`code`、固定impact code（`BOOTSTRAP_MAINTENANCE|FULL_RUNTIME_HANDOFF|VERIFY_RECOVERY_LIMITED`）、既存6 step名の`fixed_steps`だけを返す。DB・grant・phaseと合わせて確認するための公開要約であり、private材料の展開ではない。
`next_actions`は既存routeの`plans|approve|withdraw|replan|reconcile|recover-verify`だけを使い、現在の正本、認可、永続停止、照合証拠から導く。過去の停止理由を現在のblockerとして残さず、UNKNOWNを一般apply可能に戻さない。
GET/list/pollは計画生成・承認・claim・dispatch・成功確定を行わない。ただし実認証の失効を検出した場合の永続STOP記録は維持する。新規受付の厳格なfoundation照合と、受理済み処理のHTTP切替中の進捗観測を区別する。

P5-4のWebUI・通常CLIは、凍結要求の環境IDを`X-Shirokuma-Environment`に自動設定する。これは送信先の選択ではなく、サーバーが保持するenrollmentとの一致を要求する任意の前提条件であり、bodyのallowlistは変えない。
サーバーは現在の認証・role確認後、保存ACKの照会・登録・dispatchより前に比較する。不一致は409 `ENVIRONMENT_MISMATCH`、重複・空・不正形式は400で拒否し、当該要求の業務登録・admission・dispatchは0とする。認証失効による安全STOP記録は維持する。
header未指定の既存API呼出しは互換性を維持する。新clientは確認GETとPOST間の環境切替をこの前提条件で止め、旧環境の不確実な受付結果を未実行扱いにせず、元key/bodyと証拠を元環境のscopeに保持する。

エラーは新routeで`{error:CODE,message:safe_text}`とし、既存routeの形式は変えない。

| HTTP | 主なcode | 副作用 |
| - | - | - |
| 400 | INVALID_REQUEST / INVALID_NAME / INVALID_DISPLAY_METADATA / INVALID_INITIAL_GRANTS | DB/credential/provider変更0。拒否監査は可 |
| 401 / 403 | UNAUTHENTICATED / ADMIN_REQUIRED / CREATION_OWNER_REQUIRED / INVALID_ORIGIN / PRINCIPAL_STOPPED | 当該拒否要求のdispatch 0 |
| 404 | CREATION_NOT_FOUND | なし。認可検査を先に行う |
| 409 | IDEMPOTENCY_CONFLICT / REVISION_CONFLICT / CREATION_BUSY / UNAPPLIED_CHANGES / STALE_PLAN / RECONCILIATION_REQUIRED / ENVIRONMENT_MISMATCH | 同key ACK以外の追加登録・applyなし。現在処理を照会する |
| 503 | FOUNDATION_NOT_READY / OPERATOR_UNAVAILABLE | 新規Createの受理前なら登録0。受理済み処理の進行不能はGETのblockerで示す |

## 3. 永続化とtransaction

現sourceのcontrol storeはversion18。**次の追加migrationを19**に予約し、以下4表を同じstoreへ追加する。
既存Operation/step/grant履歴・tombstone・UNKNOWNを再構成しない。foreign key有効、migration全体はtransactionとする。
旧resident停止→private backup→新binaryでmigration→検査→新resident起動の順で配備し、旧versionとの混在起動を避ける。
P5-0ではlive DBを開いてmigrationしない。

| 新規表 | 固定する主なcolumn・制約 |
| - | - |
| `db_creations` | `id` PK、`environment_id,warehouse_id,actor,enrollment_id,enrollment_revision`、`database_id` UNIQUE FK、`create_operation_id` UNIQUE FK、canonical `intent_json`、metadata/grant子IDの固定map、正の`revision`、`reservation_active` bool、`terminal_outcome` nullable、created/updated時刻 |
| `db_creation_phase_plans` | `id` PK、root FK、`phase`、`purpose` APPLYまたはVERIFY_RECOVERY、正の`sequence`、既存`operation_id/generation_id/predecessor_operation_id` FK、public delivery/recovery digest、sealed private材料のopaque ref、public plan要約、current approval request FK |
| `db_creation_requests` | `id` PK、環境/actor/正規route/key、canonical payload、root/plan FK（private principal操作のみroot不要）、固定action、immutable ACK、`approval_state` nullable VALID/REVOKED/CONSUMED、`dispatch_state`、executor/owner/claim ID、承認時principal gate revision/op revision、実行中revision cursor、停止理由・private receipt ref、時刻 |
| `db_creation_principal_gates` | `(environment_id,principal_id)` PK、ACTIVE/STOPPED、正のrevision、reason code、変更したprivate request ID FK、時刻。global account storeの代替にはしない |

必須indexは、`db_creations(environment_id,warehouse_id) WHERE reservation_active=1`のUNIQUE、
`requests(environment_id,actor,route,key)`のUNIQUE、`phase_plans(root,phase,purpose,sequence)`のUNIQUE、
APPLYの`operation_id`のpartial UNIQUE、一覧用`(environment_id,actor,created_at,id)`とする。
VERIFY_RECOVERYは元APPLYと同じOperationを参照する。既存6 stepやquery receiptを複製しない。
requestのdispatch_stateは`ACCEPTED|CLAIMED|COMPLETE|BLOCKED|UNKNOWN`。これは依頼配送の状態であり、外部効果成功の正本ではない。
actionはCREATE/PREPARE_PLAN/APPROVE/WITHDRAW/REPLAN/RECONCILE/RECOVER_VERIFYと、private専用STOP_PRINCIPAL/RELEASE_PRINCIPAL/OBSERVEに限定する。
private停止/解除/観測もrequestとして監査し、公開clientからは作れない。
approvalはclaim中もVALIDのままとし、CONSUMEDはbootstrap PREPARED、full APPLIED、または限定recoveryのcanonical reconcile完了時だけとする。
CLAIMEDを消費済みとみなさず、失効時には実行中の未完了承認も撤回する。

### 登録・計画・承認

Createは認証・入力・設定済みcapabilityを検査した後、`BEGIN IMMEDIATE`で次の順序とする。
同キー照合→principal停止検査→warehouse OPEN/他ownerなし・別の未適用宣言なしを確認→
受付予約とroot・既存DB/binding/4 Schema/表示metadata/初期desired grant/既存子Operation・固定ACKを一括commitする。
内部のID採番・INSERT順はFKを満たす順とし、IMMEDIATE transactionの空き確認からcommitまで他writeを介在させない。
同キー再発見は自分の受付予約と競合させない。異キー競合は登録前409、敗者のDB/grant追加0とする。
停止中でも現在認可を通った同keyの保存済みACK照会は再受理・再dispatchを起こさず、停止を解除しない。
最初からprivate foundation/ownerが未準備なら受理前503。通信障害が受理後に判明した場合は同じrootをBLOCKEDにする。

既存create/display/grant wrapperは保持し、既に検査した同一connectionを受け取る内部helperへ分離する。
wrapperを呼び合ってtransactionを分割しない。DB transaction中にprovider通信、ToFu、IPC、SQLを実行しない。
子冪等キーはserverが`root＋固定kind/phase＋sequence`から生成し、既存request表で再発見する。
既存generation Operation作成とP5 planへの関連付けも同じtransactionで確定し、未関連の実行可能な窓を作らない。

承認transactionはcurrent requester、principal gate、root revision、phase、plan/Operation/digest/private bindingを確認する。
immutable承認claimsとACK、既存warehouse action requestへの参照を保存する。既存handoff表のREQUESTED制約は変更しない。
root revisionは関連・承認・受付制御の変更で増やす。子stepの進行は現在正本から導出し、UI状態をDB lifecycleへ保存しない。
承認時op revisionは不変。実行中のrevision cursorは同じowned attemptによる正当な進行と同じtransactionでだけ更新する。
各stepで承認時revisionとの単純な等値比較を繰り返して、自分の進行をstale扱いにしない。

### 排他と終了

作成受付予約はwarehouse controlのDRAIN/APPLYINGとは別物であり、予約取得でcontrolをDRAININGに偽装しない。
有効予約中は旧create/register-specの別登録と、非rootのgeneration plan/apply/reconcileを409で拒否する。
正当なbootstrap/full/replan/recoveryは正本へ関連付けてからdispatchする。grant剥奪、既存Jobの取消・観測・固定recoveryまで一律に禁止しない。
pre-mutation停止でcontrolがOPENへ戻った後のJob UNKNOWN回復は、既存のOPEN/no-owner条件で可能とする。
P5がAPPLYING/UNKNOWNを所有中に別の回復がcontrolを乗っ取ることは許さない。

予約はTTL、browser close、PID不在で解放しない。verified fullのroot完了と同じtransactionで解放する。
private明示終了は、全intentの実効果が分類済み、control ownerが解決済み、対象DBが未適用宣言として残らないことを条件とする。
REGISTEREDを放置したまま終了・削除して次の要求を通す経路は作らず、同じrootから回復する。
withdrawは安全停止であり、予約終了ではない。retireが可能なlifecycleでは現行非purge契約とCASを守る。

replanは既知の未送信・安全な既存閉鎖を確認して新sequenceを作り、古い承認を引き継がない。
bootstrap PREPAREDはfullの正当なpredecessorとして保持する。部分適用・UNKNOWNを新Operationへ隠して送信し直さない。
正の照合で同じOperationがAWAITING_APPLYへ戻った場合は、最新revisionでそのOperationの新承認を行う。
VERIFIED_STALEなら既存の再計画規則へ進み、古い候補からREADYを確定しない。

## 4. private委譲と停止点

### enrollmentと委譲

環境導入時のprivate enrollment v1を固定する。必須項目は、environment/warehouse ID、正のenrollment revision、
control state位置、承認済みbase profile、executor account IDとprivate認証参照、両canonical ownerのprivate socket参照、
固定Workbench/native/backend origin、専用kube context/API/CAのpin、ToFu workdir/state identityである。
candidate固有のsaved plan/serial、Secret UID/resourceVersion、材料、runtime profileはphaseごとにsealed artifactへ束縛する。
設定と実profileの不一致は停止する。環境変数のdefaultやprimary/global kubeconfigへfallbackしない。
enrollment変更は旧承認をstaleにし、現在設定と実効果を確認して再計画・再承認する。

P5 permitはprivate workerが正本から構築する型付き値とする。公開APIのactorや任意JSONをpermitへ変換しない。
claimsは`environment/warehouse/enrollment revision, principal_gate_revision, creation/DB, phase/plan, approval request,
Operation/承認時revision/delivery digest, candidate binding, requester, executor, claim/attempt`に固定する。
要求IDは権限tokenではない。private channelの認証、現在executor認証、正本への関連の全てを必要とする。
要求者のbrowser password/sessionを保管せず、要求者を模したAuthenticatedPrincipalを生成しない。
要求者の現在権限は現在の認証account正本からIDで検査し、candidateや過去のapproval内flagsを流用しない。
executor自身も現在有効なplatform admin/maximum adminかつ設定された専用executorであることを独立に検査する。

**必須core guard**は、OperationのP5関連とwarehouseの有効予約を正本から判定する。
P5関連ならtyped permit、approval、principal gateと承認時gate revisionの一致、candidate/attempt/revision cursorを必須とし、
旧manual apply/reconcile、同一actor、callback省略、ContextVar未設定からの迂回を拒否する。
P5でないOperationでも有効予約のあるwarehouse構成変更は、当該rootへの正当な関連がなければ拒否する。
非P5 manual経路の`Operator.authorize()`による同一actor検査は維持する。

### 原子的な失効停止

private `STOP_PRINCIPAL`は現在のexecutor認可と固定環境を検査し、`BEGIN IMMEDIATE`で
principal gateをSTOPPED/revision増加→CLAIMEDを含む未完了のVALID approvalをREVOKED→停止理由/ACK保存を一括commitする。
新しい要求・承認も同じgateを同じtransactionで検査するため、撤回とaccount変更の間の新規承認を許さない。
元account設定を変更する前にこのprivate操作を行う。検出した現在権限の失効も同じ停止処理へ接続する。
未認証のHTTP失敗や、browserの選択roleを下げただけの操作で他の受理済み処理を停止させない。

private `RELEASE_PRINCIPAL`は現在権限と既知/未知効果の照合を確認して明示実行し、gate revisionを増やす。
account復元だけでは解除しない。REVOKED approvalは解除後も不変であり、本人による新しい承認が必要。
新承認は解除後のgate revisionに束縛し、旧permit/claimは新規leaf/最終CASでrevision不一致として拒否する。
現行account fileに単調増加epochはないため、検査間で完結する直接file失効→同一値復元は検出保証外とする。
内容hashでこの制限を隠さず、運用上の撤回は上記の停止先行手順へ統一する。
target grantは既存のmonotonic epoch/CASを利用する。DB grant失効とplatform管理権限を混同しない。

| 停止点 | 同じtransaction内で必須の確認・動作 |
| - | - |
| root/準備要求受付、承認、claim | current authority、principal gate、root/plan revision、actor、環境、予約所有、approval一致。停止が先なら受理/claimしない |
| step PENDING→RUNNING | 必須P5 guardと所有attemptを検査してintentをcommit。その後に外部通信する |
| 新規provider/credential/ToFu/activation/検証SQL leaf送信 | 当該leafのdurable intentとapproval/gate/attempt検査を束ねる。step開始時だけの検査で代用しない |
| 外部結果受領 | 失効後でも既知receiptを保存する。結果を捨てて未送信に戻さず、後続leafを開始しない |
| bootstrap PREPARED確定 | 現在権限、gate、approval/owned cursor、registry/材料pinを再検査。停止が先なら成功確定しない |
| full最終確定 | 同一transactionでgate/approval/attempt、registry/DB/binding/grant/Secret/plan検証条件を満たした場合だけ、binding/effective/READY/APPLIED/OPEN、root完了・予約解放を行う |

外部通信中にSQLite transactionを保持しない。intentのcommitが停止より先ならその効果はin-flightとして扱う。
停止transactionと新intent/最終CASを同じstoreで直列化する。最終確定が先に完了したDBを、後の失効でrollbackしない。
以後の通常データアクセスは現在の認可で判定する。fresh grantはverified fullまでeffectiveへ昇格しない。

停止中のprivate `OBSERVE`は元pinのGET/receipt/構成照会と証拠追記に限定する。
新しいmutation、SQL POST、profile activation、READY/grant/OPEN確定、承認復活を許さない。
公開mutationはgateに従うため、停止中の照合はprivate運用から行う。正の証拠がない場合は停止を維持する。

## 5. canonical owner・IPC・runtime引継ぎ

Workbench/nativeそれぞれのGo ownerをprivate enrollmentで有効化する。private directoryは0700、Unix socketは0600。
owner role・instance ID・起動時random capabilityを照合し、capabilityは継承FD等のprivate経路で既知childへ渡す。
公開API、argvの公開入力、log、browser storageへcapabilityを置かない。同一OS利用者を超える新しいsandboxは作らない。
server自身もcanonical ownerに管理されていることを確認し、単独Python起動を自動的にP5 executorへ昇格させない。

| 固定IPC action | 受け取る識別子と効果 |
| - | - |
| DISPATCH_REQUEST | durable request IDだけから、固定product one-shot workerを起動。action/対象は正本から再解決する |
| GET_REQUEST / GET_ACTIVATION | 同じIDの状態・receiptを観測。GETはworker起動・child再起動をしない |
| ACTIVATE_CANDIDATE | 元verify intentに属する固定activation IDと承認済みrequest ID。profile/child/targetはsealed artifactから解決する |
| OBSERVE_REQUEST | private観測権限で同一rootの証拠を取得する固定worker。未送信処理の実行権限にはしない |

requestから任意argv/env/PID/path/URLを組み立てない。workerはcurrent executor認証後にstoreのclaim CASと既存operator flockを取得する。
duplicate IPCは既存claim/receiptの照会へ戻り、2 workerが同じ外部効果を実行しない。process終了やtimeoutだけでclaimを取り直さない。

Workbench ownerの寿命、HTTP Pythonの世代ごとのcontext、operator childのcontext/process groupを分離する。
native Pythonも既知childとして独立groupにする。現行Workbenchの「Python終了→owner context取消」と
execRunnerのgroup SIGKILL配線を、そのままmanaged handoffに流用しない。
ownerは自分の既知childだけを停止・起動する。HTTP切替中もoperatorとprivate IPCは存続する。
意図したowner停止は新dispatchを止めて既知childを対象に処理し、途中のintentは不確実性として残す。
owner crash後は前のRUNNING/未ACKを実行済み不明として観測し、起動時apply・TTL再送・任意listener/PIDの取得を行わない。

fullの通常実行では、既存`verify` RUNNING intentの内部で両originをsealed candidate profileへ引き継ぐ。
限定例外は§6のCLAIMEDなRECOVER_VERIFY要求、VALID承認、所有するdurable recovery intentの組である。
typed permitはVERIFY_RECOVERY planと元UNKNOWN verifyへ束縛し、その未完了leafだけを許す。
bootstrap verifyはruntimeを切り替えない。固定6 stepに第7stepを追加しない。
per-origin activation IDは`root/元のAPPLY phase-plan ID/candidate/owner-role`に固定し、
別recovery plan、新sequence、core retry attemptから新しいactivation IDを作らない。
private journalへ、固定action、元verify、candidate、既知old child、予定new childの非秘密instance ID、owner identity、
未送信→intent→実ACKをdurableに残す。stop/start各effectにも送信前intentが必要で、欠落から未実行を推測しない。
既存`/api/v1/runtime`はverifierの完全一致契約を保ち、fieldを追加しない。
新しいadmin限定`C/runtime-instance`を両通常originに追加し、厳密に
`{version:1,environment_id,owner_role,owner_instance_id,runtime_instance_id,runtime_attestation}`だけを返す。
`runtime_attestation`はそのプロセスの既存v1 runtime応答と同一。instance IDはopaqueな非秘密値であり、PIDやcapabilityではない。
managed childでない場合は503とし、既存attestationへ架空ownerを付けない。
owner ACKだけでは成功にせず、両通常HTTPの実readbackとprivate journalの一致を確認する。

続いて既存`verify_runtime_handoff()`の両入口世代/profile_ref、固定origin、state位置、CA/backend credential照合と、
実OPA/Polaris/TLS/Schema/SQL検証を通す。片側だけ旧profile、別owner、別candidateならverifyを止める。
既存DBのconsumer/credential/TLS/S3/JWT/retired参照を保持し、新DB分だけ材料を増分準備する。
`initialize_database()`による一括再生成や、`database_material`のvalidationを実発行receiptに置き換えることは禁止する。

## 6. UNKNOWNと限定verify recovery

公開応答喪失は同じkey/IDのGETへ戻る。発行や外部mutationの結果喪失はUNKNOWNとし、
production observerの正の証拠だけで分類する。TTL、404、PID不在、模擬QueryInfo、read-only Job FENCEを流用しない。
未知の効果を新plan/Operationへ移して再送せず、client提供の成功証拠でcanonical reconcileしない。

fullの片側activation後にverifyがUNKNOWNとなる場合、現coreの通常経路だけでは回復できない。
`_run_step`はUNKNOWNを実行せず、`Operator.assert_intent()`はRUNNINGを要求し、
`observe_completed_verify()`は完成済み`verification_complete`と実SQL receiptを必要とする。
そのためP5に、**元verify配下の固定recovery要求**を実装する。通常経路のassertを無効化しない。

1. private観測で全activation/検証leafを分類し、固定recovery計画とdigestを保存する。
   purpose VERIFY_RECOVERY、元root/UNKNOWN verify/Operation/candidate/private journalへ束縛する。
   未分類のDISPATCHED leafが1件でもあれば、計画を実行可能にせず照合待ちを維持する。
2. 現在の本人が`recover-verify`で別途明示承認する。停止中なら実効果照合とprivate解除が先に必要。
   fresh approval/claim/recovery intentを保存し、元step UNKNOWNと最初のintentを保持する。
3. typed recovery permitは元の所有・同じcandidate・固定未完了leafだけに限定する。
   完了済みactivationは再起動せず、確実に未送信と分類済みのsubactionだけを実行する。
   単なるreceipt不在は未送信の証明にしない。ownerが既知childを証明できない場合も停止する。
4. 残りの実verifyも完了する。完了済みSQL receiptは再利用し、保存済みnextUriはGETだけを続ける。
   送信済み応答不明のSQLを再POSTしない。未送信を証明できた検証leafだけ新規送信し、各送信の認可/intentを保存する。
5. 実`verification_complete`を保存後、既存observer→canonical reconcileへ渡す。
   UNKNOWN→RUNNINGへの便宜的reset、片側更新後のwhole verify NOT_EXECUTED、架空の完了receiptは禁止する。
6. canonical reconcileの結果はAWAITING_APPLYであり、APPLIED/READYではない。
   最新op/root revisionで通常fullを再確認・再承認し、既存apply/最終CASへ進む。
   完了済みの検証SQLは既存receiptを使い、new POSTを増やさない。STALEは既存VERIFIED_STALE/再計画規則を守る。

この回復は任意shell/SQL/profile変更を受け付けない。通常成功時の2承認に、故障時の限定回復・最終再承認を追加する。
証拠不足、材料drift、元owner不明なら運用照合待ちを継続し、UIが成功や安全な自動再送を約束しない。

短期read-authの補足（P5-6実機検証で具体化）：OAuth access tokenは既存のconsumer/client secretやJWT署名材料と区別する。
実ACKのtokenについて期限切れを正に確認できた場合だけ、同じ宣言済みHTTP identity・固定OAuth endpoint/body・既存資格情報による補助認証attemptを許す。
現在のtyped recovery、実観測要求、または通常FULLの実typed CreationPermitへ束縛して認可し、private INTENT→ACKを別記録する。通常FULLは現在の承認・claim・operation cursorと封印済みHTTP宣言を検証し、必須の再verifyを省略しない。元の完了leaf・ACK・responseは保持し、activation／SQL等の未完了scopeを広げない。
新規の認証INTENT前に、同operatorによる宣言済み固定forwardの実所有・存命・readyを確認する。forwardを持たないfull公開観測は新規mintせず、明示承認付きVERIFY_RECOVERYへ渡す。有効ACKの再利用は新規送信と区別する。
同じ観測要求または承認とHTTP identityで送信は高々1回とし、有効ACKは再利用する。INTENT／UNKNOWNは新要求へ移して再POSTせず、期限情報が不明・不正な場合も停止する。
これは未知effectをTTLで未送信扱いする規則ではない。consumer／group／password／署名材料を再発行せず、OAuth mint件数を別計上する。

## 7. 実装・検証への引渡し

| Unit | 固定契約に基づく対象と重点検証 |
| - | - |
| P5-1 | control_store/catalog_admin/display_management/grant_management/request_auth/warehouse_operations。4表/migration19、共有transaction helper、受付排他、mandatory P5 guard、委譲/停止、旧経路迂回拒否、immutable ACKとcursorを検証 |
| P5-2 | database_generation/database_material/database_provision/warehouse_operator。既存consumer保持、増分private準備、bootstrap実発行receipt、full successor、別変更の混入拒否を検証 |
| P5-3 | internal/cli/workbench.go、warehouse_resident.go、固定private workerとruntime_profile/operator。managed children/IPC、両実runtime attestation、per-origin journal、限定verify recoveryのpermit/assert/receipt接続を検証 |
| P5-4/5 | workbench_server/index/通常CLI。公開route、2回の計画確認、永続照会、withdraw/再計画/照合/回復、current identityのcache破棄と元draft保持、権限/失敗matrixを検証 |

focused検証では、旧schema18→19の全履歴保持、migration rollback、同key/異key/旧登録競合、
独立した2要求者＋1 executor、private停止と新承認/leaf/最終CASの競合、旧manual同actor迂回、
HTTP世代交代、片側handoff、実effect後ACK喪失、SQL再POSTなし、retire競合を優先する。
通常sourceの全体検査と、Phase5計画P5-A01〜19の既存/fresh clean実機受入へ順に進める。
P5-0の文書検査やread-only前提確認を、これら実装・故障試験・所有者受入のPASSへ読み替えない。
