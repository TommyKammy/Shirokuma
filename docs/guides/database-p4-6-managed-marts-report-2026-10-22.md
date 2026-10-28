# P4-6 管理Martの手動全件更新・変更確認 限定技術受入完了報告

2026-10-22。P4-6の実装・通常入口・実機の故障検証・失効・清掃・最終generation・認証撤去後の復帰と独立最終照合を完了した。P4-6の限定技術範囲の必須残件は0。Phase4全体、P4-7以降、所有者の最終操作性評価は後続計画とする。

## 実装

DB内のMartをP3 modelの明示version、同DB source ResourceRef、MARTS target、品質条件に結び付けた。定義保存はCASで、SQLを送らない。手動変更確認と全件更新はP3-1の共通実行境界を通り、本人・owner・full9 contextを受付時に固定する。公開済みtargetは実測Refを要求し、同名Tableを採用しない。

変更確認はsource RefとSnapshotの集合、model ID/versionの等値比較を用いる。SQLは開始時Snapshotに固定し、同じRefのaliasは一度だけ取得する。全件更新の既定は無条件で、明示した場合だけ変更確認付きになる。更新はP3のbuild→audit→publishと品質条件を再利用する。変更なしでは公開成功時刻を更新しない。

通常APIは`/api/v1/marts`以下、通常CLIは`shirokuma mart save/list/get/check/refresh/runs/inspect`。Workbenchの「データ変換」→管理Martに登録・版の明示Load・変更確認・手動更新・履歴を接続した。SQL Worksheetとは別のcontextを持つ。現在の権限で保護詳細を再検証し、失効後は保護情報を消す。

SQLite schemaは16。追加した4表はMart定義、run、DataOpsとの対応、idempotency receipt。初回202 ACKは、受付transaction内で権限確認後に作った公開用copyを返す。worker開始後のSQLite再読を避け、実workerがwrite lockを持つ回帰試験で初回ACKの成立を確認した。

## 固定sourceと試験

最終candidateはv3、source203 files、manifestは`runtime/source-plans/p46-marts-candidate-v3/manifest.json`。source digest `d65411be2715bf4464306d35ba2ea52e349118a6f233f4cb56bfdc8d553ff5fc`、CLI binary digest `45db27c4846c102302cde1f733942ba1f01a3601ec976287f6260d6d03b78a35`。historical v2のtop-level manifestと混同しない。

rootの固定candidate検証はresident 1,697試験（1,664成功、既存33skip）、design14成功、security unit34成功。Go CLIのtest・vet・gofmtも独立に成功し、v3で変更していないGo source28件とbinaryを照合した。security scanのexit2は既存の`opentofu/dev/variables.tf` host-local marker1件で、新規findingはない。意図されたhost設定は保持した。source・binaryの前後一致とschema15→16の隔離移行18試験も照合した。実行環境はproject venvのPython3.12.12。

## 実機で完了した通常操作

SALES、FINANCE、任意名DB `insights_e88387a5`の同名Martを通常API・既定native8080の通常CLIから操作した。P4-5 Stage scanの初回取込・JSONL append・新しいkeyでの重複skipを出発点とし、新規保存、変更確認、無条件／条件付き更新、modelの明示版更新、所有者・role・CAS・不正入力拒否を確認した。13回の公開、39子SQLと監査・公開Snapshotの対応をcanonical graphで照合した。

Workbenchでは3DBそれぞれの既存Martを選び直して変更確認と手動更新を行った。さらに既公開modelのcurrent target Refから新しいMartを保存し、変更確認・更新・履歴まで確認した。保存時はSQL0、4更新の12子SQLは全てFINISHED。3DBの選択肢・owner・実Ref・UUIDの分離を照合した。通常API/CLIの証跡と実画面の証跡を分けて保存した。

追加の通常系では、監視sourceなしの手動更新、watch必須のcheck／条件付き更新の拒否、品質不合格によるpublish前停止、同一Refの複数alias、別ownerの自分のMart操作と他ownerへの取得拒否を確認した。manual-only fixtureは通常P3 WAPで作成した。先行したStage→MARTS作成要求は正当なschema制限でSQL0拒否となり、別の通常P3操作へ進んだ。拒否要求は再送していない。

## 応答と証拠の扱い

SALESの先行CLI更新で応答エラーが出たが、元のaccepted runはcanonical照合で成功・commitを確認した。後続の別目的・別keyの診断更新を再送として扱わない。別の変更確認では受付後502が出たが、元runはSQL0の成功へ終端していた。実測した例外型がないためSQLite競合と断定しない。この受付後応答経路をv3で修正した。初回の生応答・誤った検証期待・後続照合は上書きせず保持した。

## 実機の故障系・競合

監視sourceの取得拒否は、限定した補助process内で実Catalog metadata GETのRequestをcopyし、そのcopyの認証だけを除去した。実HTTP401を受け、元のCatalogエラー経路を通ってMartは`CATALOG_UNAVAILABLE`で失敗した。変更あり／なしはともに未判定、子SQL0、物理親はFINISHED、旧成功基準・時刻・targetは不変。元Requestの認証や他の要求は変更していない。これは後述する通常grant失効の403とは別の試験である。

実行中appendではsource S0=`7901945948412945876`を取得した元runを停止し、通常JobとStage scanが別handlerから409で拒否され台帳増0になることを確認した。独立したplatform operatorの実SQLを1回だけ送ってsourceをS1=`7450582710649965500`へappendし、1.922秒で元runを再開した。元runのbuildはS0へ固定され、監査2行・公開の対応を実測した。物理親の終了後、同じ保存Jobと空scanは新しいkeyで成功した。

続く通常更新でS1の3行を公開し、その実成功基準を確認してから、実SQL1回でsourceだけを保持済みS0へrollbackした。次のcheckはchanged=true／SQL0であり、公開target3行・S1基準・成功時刻を保った。rollback後sourceの2行とtargetの3行は実値で照合した。外部operator SQLは、通常CLIやresidentのSQL台帳による操作と混同しない。

audit後変更では、実build・合格auditの後に生成Stageへ実SQL1回でappendした。元publishは`STAGE_CHANGED`で未commit失敗となり、publish SQL0、build/auditの2SQLはFINISHED、物理親もFINISHED。旧target・基準・時刻を保ち、補助processを停止した。

先行するaudit検証1件は補助callerのprojection誤りにより、operator SQLを送る前にdeadlineで終了した。旧結果保持と物理終了は確認したが、Stage変更拒否の成功数へ含めない。元run・2SQL・生成Stage・原応答を保持し、実データのreaderを訂正した後、別purpose/keyのv2要求で上記を検証した。受理済み要求は再送していない。Jobの入口・HTTP200・履歴GETに関する補助callerの期待誤りも、初回の生応答と未受理／既受理の区別を保持した。

公開後は、publication artifact未記帳、成功基準未保存、基準保存済みで履歴だけ未確定という3つの状態を、それぞれ実commit・実build/audit・実target Snapshotとともに保存した。物理親は全て終了し、対象Martの新要求は409／未受理、別の有効なMartのcheckはSQL0で利用できた。補助process停止後、新しい未patch processで同じrun IDをObserveすると、3件ともSQL・DataOps・admission・runの増加0で復旧した。自動rollback・再publishはない。

artifact窓はDataOpsのdurable resultに実publicationがあり、公開artifact表は未記帳、`cleanup_error=RuntimeError`を保持していた。基準窓は旧基準を保持し、同IDの照合後に実publishのfinished_atで正基準を保存した。履歴窓は既に保存済みの基準と実publish時刻を、履歴確定後もそのまま保持した。ここでの`cleanup_error`はartifact記帳例外を既存P3のcatchが記録した値であり、物理TableのDROP清掃失敗を注入した証拠とはしない。

記録上の制限として、3件とも補助process切替時にpatched processの`events.json`がunpatched processのlisteningで上書きされた。元の故障イベントcopyはない。直接の故障呼出回数・元イベント列は未実証として撤回し、再生成していない。上の判定は保存済みraw pending／初回HTTP応答、実SQL・DataOps・publication、同ID照合前後のSQLite状態、実時刻とcountの独立照合に限定する。既受理runやSQLをこの記録損失のために再実施していない。以後の切替は原イベントを先にexclusive copyで保全する。

## identityとSnapshotの境界

source再作成では、実DROPのFINISHEDと同じ権限による404を保存し、CREATE前の元Ref checkが`NOT_FOUND`・SQL0で失敗することを確認した。CREATEは一度だけ行い、新しいUUID／incarnation 2を実測した。明示CAS前の旧Ref checkは`STALE_RESOURCE`・SQL0で拒否された。Stageを新RefへCASし、通常Ingest、model／Martの明示CAS、変更確認、3子SQLによる更新・型付き2行まで成功した。元targetのRef・UUIDを保ち、同名による自動採用はない。

Snapshotなしの実機検証では、最初のTrinoのplain CREATEが空Tableにも実Snapshotを作った。その原receiptは保持し、「Snapshotなし」の証拠に流用しない。追加の一件は[公式Iceberg REST仕様](https://raw.githubusercontent.com/apache/iceberg/apache-iceberg-1.7.1/open-api/rest-catalog-open-api.yaml)のcreate-tableを使い、実測済みschemaと新しいname、`stage-create=false`だけを送った。metadataの直接編集、新しい認証・依存追加は行っていない。実200・UUID・通常のRef解決、head=-1／snapshots=[]を確認した。

そのRefへ既存model／Martを明示CASすると、通常checkは202受付後に`SNAPSHOT_REQUIRED`・SQL0で失敗し、物理親はFINISHED、旧成功基準・時刻・targetは不変だった。有効なsource incarnation 2へ明示CASで戻した後のcheckはchanged=true・SQL0で成功した。追加REST fixtureは実Ref／UUIDに限定したDROPのFINISHEDと権限付き404を確認して退役させた。REST作成をSQL CREATEの証拠へ置き換えていない。

target再作成では、元targetを実DROPし、同じSQL名でCREATEした新incarnation 2／新UUIDを確認した。有効なsourceを使う元Martのcheckは`STALE_RESOURCE`・SQL0で失敗した。P3 modelのmaterialized Ref、Mart定義、runのtargetは旧Refのままで、新Tableを自動採用しなかった。旧成功基準・成功時刻も保持した。

## grant失効と通常入口

MAINの合格audit後、通常P4-1管理APIのCAS1でownerのSALES grantを剥奪した。初回202を先に保存し、publishの停止を直ちに解除した。元run `f6f2d338-5047-4c4d-b279-5a12d9db8898`は`DB_ACCESS_REVOKED`・未commitで失敗し、build/auditの2実SQLはFINISHED、publishのlink／SQLはなく、物理親はFINISHEDとなった。直前の正当な公開 `6e28d0c3-7548-4e19-b46d-3fbb7c109228` のtarget・基準・成功時刻を保持した。

旧／現contextによるAPIのlist・get・history・inspect・新check・新refresh・replayは403で拒否された。既定8080のコンパイル済み通常CLIでも、現profileと保持済みcontextそれぞれ6件がexit1／stdout空で終了した。CLIについて観測していないHTTP statusは主張しない。別DB2件と別ownerのSALES操作は成功し、この拒否／取得確認中のSQL・DataOps・admission・Mart・run増分は0だった。

Workbenchは失効直前のMAIN定義と成功履歴を表示してから一覧更新した。失効後はMart名・model名・旧履歴・source/target IDが消え、保存・check・refresh・inspect・履歴が無効になった。DBの利用不可表示と安全なintent handleは保った。画面の一般的なエラー文言からHTTP403を推定せず、APIの証拠と分けて記録した。対象ID2件と11真偽項目のDOM照合、実画面を保存し、検証用incognito windowを閉じ、RAMの検証用認証を解放した。

先行するgrant検証1件は、補助callerが存在しない台帳名を参照したため剥奪前に停止した。finallyで元runを解放し、3SQLの正当な公開成功を確認した。このrunをgrant失効試験の成功へ含めず、原応答を保持した。v2は台帳を実DDLで確認した上で、別purpose/keyから行った。

GUIと別DBの確認後、残り5件の検証用grantも通常管理APIのCAS1で各1回だけ剥奪した。全6件はdesired/effectiveともrevoked・revision2／epoch3となり、既失効のSALES ownerへ再送していない。旧20grantはこの時点で完全一致、SQL等の増分0を確認した。

## 有限清掃と最終適用

追加53 ResourceRefは、今回清掃する物理Table42件、既にDROP済みのIngest temporary8件、source/target再作成前の旧incarnation2件、既に物理退役したREST fixture1件に分類した。registryのcurrent cacheが残る物理退役例を再DROPせず、元414行を全field保持した。P3の直接操作2件は実build/publication artifactで識別し、存在しない登録run IDを作らない。operator CREATE3件の実SQL実行者p26と、検証fixtureの論理ownerも区別した。

現在のp26 platform-adminの認証・full9認可、作成時のowner/context、実CREATE・artifact・Ref・UUIDを個別に確認した後、42 Tableを各1回だけDROPした。全42件の初回receipt、実backend FINISHED、直後の同権限による`NOT_FOUND` 404を保存した。全Tableの確認後に、実bytesと所有履歴で限定した入力9ファイルだけを除去した。実行の再送・失敗はない。全59 control-store表のhash/countは清掃前後で一致し、履歴・BLOB・内容証拠は保持した。清掃アダプターはconfigured Python3.12でroot独立13試験を通した。

最終generationは`p46-mart-final`、full planのoperationは`48b3861d-2877-4399-8668-aa31b0620159`。request revision1、mutation未開始、6手順PENDING、LOCAL OPEN revision109を実SQLiteで照合し、同じCASによるbind/applyを一度だけ行った。spec差分はgeneration名と検証用6 grantのrole/revisionの13 leafだけだった。実OPA 145件と、元のProductionOperatorによる15実SQL probeを通過し、全6手順SUCCEEDED／APPLIED、LOCAL OPEN revision113になった。通常2サーバーはcanonical applyが最終generationへ切り替えたため、重複restartはしていない。

最終ready照合34項目とattestation7項目で、元5 DB・20 Schema・414 Ref、binding identity、旧20 desired grantとeffectiveのrole/epoch等を保持した。旧effective grantのapplied_generation_idは正常に最終generationへ進み、検証用6件はrevoked・desired revision2／effective epoch3／applied revision2で一致した。Secret31件はUID/data一致、元4人の認証と保護対象bytesも保持した。backend SQL probe15件はoperatorの原receiptであり、通常利用者のquery/admission台帳へ足し込まない。

## 認証撤去と元Workbenchの復帰

固定した7段階（通常停止、検証auth撤去、private8除去、通常再起動、attestation、doctor、credential hygiene）を一度だけ実施した。各intent／初回receiptを保存し、全てexit0。検証用2アカウントと正確な8ファイルは不在、元4アカウントのrecord/password bytesと元auth file bytesは一致した。前後の全59台帳hash/count、OPEN113、source203／binary、元414 Ref、旧20 grant、Secret31 UID/dataを保持し、SQL送信・backend applyはなかった。doctorはhealthy、credential hygieneはunexpected match・permission issue・read failureが0、retired actor2件も許容例外0だった。

通常Workbenchを再読み込みし、利用可能contextを更新した。有効な元タブ2は、5選択値を保った明示再選択後に実行可能へ戻り、SQL本文は`SELECT 1 AS p35_ready`のまま。元から利用不可のタブ1は、元DB/Schema/role/mode/warehouseと`SELECT sentinel FROM p23_context`を保持し、実行禁止のままだった。SQLは実行していない。2タブのID・順序、Catalogと詳細ビューの選択は開始時と一致し、探索側の選択値も再選択前後で一致した。最終画面を保存・目視確認した。

最終の独立post-GUI照合は25/25成功。元2タブの全文・5選択値・ID順・navigationを原baselineへ直接比較した。現SQLiteの全59表のhash/countは認証撤去実planと完全一致し、通常query2,432・DataOps782・admission3,377・Mart定義10・Mart run66は追加0。Martのbusiness/物理親とDataOpsは全て閉鎖済み、未解決実行0、過去のFENCED_READ_ONLY 2件を保持した。source203／binary、元5 DB・20 Schema・414 Ref・20 grant core・auth4・保護bytes、清掃済み42/9、検証auth2/private8不在も一致した。31 Secretと補助process停止は、原applyの実測証拠と実teardown wrapperのbefore/after guardへ明確に結合し、この最終auditで新しい外部callを行ったとはしない。

## 主要な証跡

以下は`var/shirokuma/p4-6-acceptance/`からの相対path。初回の生応答、訂正前のharness期待、superseded draftも保持している。

| 対象 | 証跡 |
| --- | --- |
| 固定candidateと試験 | `gates/candidate-v3/results.json`、`gates/candidate-v3/root-clearance.json`、`gates/go-independent.json` |
| 通常API/CLI | `runtime/common-completion-summary.json`、`runtime/common-canonical-graphs.json`、`functional/state.json` |
| Workbench新規／3DB更新 | `runtime/ui-completion-fixed-summary.json`、`runtime/ui-three-db-canonical-graphs.json`、`ui/new-save-canonical.json` |
| 故障・append・rollback・identity | `runtime/fault-cases/`各caseの初回receipt、summary、canonical graph、operator progress |
| 失効拒否 | `runtime/fault-cases/grant-loss-main-v2/summary.json`、`denial-summary.json`、`ui/grant-post-revoke-purged-proof.json` |
| 清掃と最終apply | `runtime/finite-cleanup-completion.json`、`runtime/finite-cleanup/progress.json`、`runtime/final-ready-summary.json` |
| 認証撤去 | `runtime/final-auth-teardown/completion.json`と7段階の初回receipt、`runtime/attestations/final-auth-complete/` |
| 独立最終照合 | `runtime/post-gui-integrated-independent-proof.json`（25/25成功） |
| 元タブ／最終画面 | `ui/final-original-tab1.json`、`ui/final-original-tab2.json`、`ui/final-restored-original-workbench.json`と各PNG |

## 範囲

P4-7のscheduler、自動scan・自動更新は有効化していない。source集合は各Refの開始時測定であり、複数sourceをまたぐ単一transactionではない。SQL依存の自動推論、時刻依存式・schemaのみの変更・target直接編集の検出、行単位dedup、exactly-onceは今回の保証範囲に含めない。真のSQL UNKNOWNはunitの検証と実機の既存履歴を区別し、新しいUNKNOWNを故障系の代用として作らない。所有者の最終操作性評価とPhase全体の最終受入はPhase5に残す。
