# Database Phase 5 実装・実機受入計画

2026年10月23日作成。データ管理アーキテクチャのPhase 5を対象とする。現状は**P5-6技術受入完了・P5-7はDB作成方式の再設計待ちで保留**。本人は改善後のREADY・SQL成功を確認した上で、操作・承認・時間をblocking指摘とし、基盤からの見直しを求めた。[本人の操作結果と指摘](database-p5-7-owner-acceptance-2026-10-27.md)と[ADR-0038案](../design/07_ADR/ADR-0038_Separate_Logical_Database_Creation_from_Platform_Provisioning.md)を参照。以下の2承認・全体drain等は現経路の契約として保持し、新設計の要件と自動的にみなさない。Phase5全体の本人受入は未完了。
DEV-04Fの歴史的なPhase 5（WAP）とは別のtrackであり、本計画ではP5-0〜7を用いる。
2026-10-27追加：[ADR-0038の有界実証](database-adr0038-feasibility-2026-10-27.md)を完了し、
共有Polaris＋STS＋動的秘密参照を次の実装対象に選定した。[後継契約案](database-logical-create-contract-2026-10-27.md)で
基盤と通常CREATE、DB別revision、UNKNOWN、認証／認可の動的更新を分けた。所有者の指示により旧DB移行は必須とせず、
clean基盤での再構築を基本案とする。以下は旧経路のdated契約で、新経路・P5-7の完了を意味しない。
初回の計画作成は文書とsource確認のみを対象とした。以降の実装・配備・検証状況は下記追記と各報告を参照する。

最終更新（2026-10-27 03:49 JST）：P5-A01〜19の技術受入を完了した。OPA4の実query timeout／FAILEDとTLS4の元CAによる実x509拒否からUNKNOWN停止を確認し、復元・WebUI・正常STOP／MAX_ONE=0・保全を独立RO29／26項目で確認した。TLSのSQL1 dispatchは未完了・backend IDなしであり、SQL0やengine FAILEDとは扱わない。現source201・材料147・test101・binary・whole2354成功＋既存skip33とPRIMARY rev6の既知process／canonicalを読み取りで再確認し、最後に既存の`rtk make verify-design-context`を1回実行してexit0（11＋3テスト、設計文書93件）を確認した。旧HOLD／隔離UNKNOWN・CLAIMEDは履歴として保持し、P5-7の所有者本人受入は別に未実施のままとする。[受入表と個別索引](database-p5-6-acceptance-2026-10-25.md#受入束)に証拠範囲の限界と最終検査を記録した。以下のdated追記は当時の記録であり、新規commit・製品／runtime変更は0である。

2026-10-23追記：P5-0の契約固定を完了した。[公開/private契約](database-p5-0-contract-2026-10-23.md)に
API・追加migration19・委譲・受付排他・失効停止・runtime引継ぎと限定verify回復を具体化した。
[ADR-0037](../design/07_ADR/ADR-0037_Use_Bounded_Database_Creation_Delegation_and_Runtime_Handoff.md)と
[前提確認報告](database-p5-0-preflight-2026-10-23.md)を参照。

2026-10-23追記：P5-1のcore実装・独立レビュー・技術検証と既存環境のschema19移行を完了した。
[P5-1実装報告](database-p5-1-implementation-2026-10-23.md)に排他・原子登録・初期desired・委譲・永続停止・
旧経路guard、標準検証、実HTTP/通常CLI、既存履歴保持の証拠と制約を記録した。
P5-1時点ではP5-3以降の実foundation/owner接続・新WebUI導線と全体受入は未実装・未受入であり、通常環境の新Createは503で停止した。

2026-10-23追記：P5-2のprivate増分準備・実bootstrap receipt・full候補を実装し、専用実機の正例・負例を確認した。
[P5-2実装報告](database-p5-2-implementation-2026-10-23.md)に既存資格情報保持、bootstrap/fullの承認分離、
ACK保存後と秘密値保存前の応答喪失、再発行防止、失効時の永続STOPの証拠と制約を記録した。
P5-2時点のfull候補は未適用・新DBは非READYであり、P5-3の実owner/runtime引継ぎでは再計画と新承認を必要とした。

2026-10-24追記：P5-3の実dispatch・owner引継ぎ・連続作成の材料継承・UNKNOWN回復を実装し、独立レビューと標準検査を通した。
[P5-3実装報告](database-p5-3-implementation-2026-10-24.md)に、専用clean環境の同一enrollmentで2件のREADY、
逆origin duplicateのdispatch 0、実ACK応答喪失・executor失効STOP・明示RELEASEと新承認による回復、通常SQL CLIと権限分離の証拠を記録した。
P5-4は公開進捗projectionの固定`OPERATOR_UNAVAILABLE` fallback、観測要求の初期BLOCKED残留、過去stop理由の扱いを先に修正し、
実readiness・requester/executorの現在gateと一致させてから新UIへ接続する。旧ACKの取得を実行再開と表示しない。
初期の別検証環境に残る単独query UNKNOWNは保全・当該環境停止として記録し、一般query回復や全体受入の完了を主張しない。

2026-10-24追記：P5-4の実装と対象導線の技術検証を完了した。[P5-4報告](database-p5-4-implementation-2026-10-24.md)に
WebUI／通常CLIの別承認からREADY、SQL権限分離、永続STOPとfresh受付拒否、有限清掃後の独立RO保全証明を記録した。
cold HTTP確認はmanaged owner再起動／再配備の受入ではなく、Phase5全体は未受入のままとする。

2026-10-25追記：P5-5の実装と対象の故障・競合回復の実機検証を完了した。[P5-5報告](database-p5-5-implementation-2026-10-24.md)に、
WebUI／通常CLIの実応答喪失、grant／実権限失効、永続STOP、正の観測と個別RELEASE、新承認によるUNKNOWNからREADY／実SQLへの回復、
drain・非purge退役、既存環境と保存履歴の保全を記録した。初期4環境のUNKNOWNは停止・隔離して保全し、別cleanで正の経路を実証した。
P5-6の再起動／再配備を含む統合技術受入、P5-7の所有者本人による操作性受入は未完了であり、Phase5全体の受入とは扱わない。

2026-10-26追記：P5-6では両環境の3DB縦断、正常再起動／Trino再配備、grant剥奪のverified full、非purge退役とretire勝者のstale拒否を実証した。独立cleanでは正常退役後の新規PREPARE、実Go owner crash・元context取消・FD fence・同record再起動拒否と停止保全を確認した。source147-r7の2364宣言／2331成功＋既存skip33は当時の全体結果として保持する。A07のstale／PENDING／REGISTEREDは安全停止を確認し、旧PENDINGのplan未生成CLAIMEDは後付け証拠で回復済みとせず保全する。OPA初回ARMはsocket長guardのHOLDで承認／故障0、A13のOPA／TLS実故障と最終設計検査は未完了である。各HOLD・隔離環境の未解決claimと後続成功は分けて[受入記録](database-p5-6-acceptance-2026-10-25.md)に残す。

同日追加（最終cohort）：既知prejournal失敗をlive owner／claim／epoch／root revisionのfenceとCAS、private receiptで終端化し、同rootの既存REPLANへ進める導線と未適用desired grantの通常取消を追加した。同generationの複数FULLを検証し、元row／監査／epochと取消の元証拠を保つ。本体207件・auth／client77件・専用helper各142件は別検査で成功。現cohortの標準wholeは正規make1回、76群・2387宣言＝2354成功＋既存skip33、failure/error0で、source201／147材料／test101／helper71＋新34とbinaryの前後一致を確認した（[個別証拠](database-p5-6-acceptance-2026-10-25.md#実証json索引)）。helperの件数はwholeへ合算しない。

追加実機では、WebUIの既知失敗COMPLETE→通常CLI revokeのCANCELED→同logical revision・fresh ownerでの通常再起動→同root REPLAN sequence2→WebUIの別bootstrap／full承認→APPLIED17／READYを確認した。元row／監査／epochと取消projectionが永続化し、実runtime ACK2・検証SQL6・WebUIの5型SQL1、実BI認証の拒否、後続FULLでの取消pair非復活と次CREATE受付も確認した。追加CREATE intentはREGISTERED／予約1、PREPARE・材料・発行0として残し、元root READY／予約0と区別する。正常STOP／MAX_ONE0後も70表・private556件・volume／networkを保持した（[13件の実機索引](../../var/shirokuma/p5-6-prepare-recovery-acceptance/private/actual-prepare-recovery-same-root-WebUI-CLI-restart-final-acceptance-index-PASS-v1.json)）。旧CLAIMEDには後付けreceiptを作らず、回復済みとは扱わない。

PRIMARYの現cohort通常rev6更新は[24項目](../../var/shirokuma/p5-6-acceptance/private/existing/final-current-cohort-primary-normal-rev6-v1/PRIMARY-final-current-source-normal-rev6-SQL1-preservation-PASS-v1.json)で確認し、元70表・private65件・auth4・物理84件を保持した。通常CLI SQL1の正規増分はquery関連3行だけで、[WebUIのGET reload](../../var/shirokuma/p5-6-acceptance/private/existing/browser/final-preservation-rev6-v1/browser-readonly-preservation-PASS-v1.json)でも元2draft・role ceiling・日本語を保持、SQL0だった。OPA／TLS実故障・最終設計検査とP5-7は未完了であり、P5-6技術完了を宣言しない。

OPA2のfirstARMはproxy handle取得後のcoordinatorのPID判定でHOLDとなり、full承認・実故障・SQL0のまま正常停止した。元intentと履歴を保ち、再ARMせずMAX_ONE=0を確認した。同機能のOPA3 helper9は識別子だけの機械cloneとnative142件で検査し、製品source201／元9を保持した（[個別記録](database-p5-6-acceptance-2026-10-25.md#実証json索引)）。この準備検査をOPA／TLS実故障のPASSへ流用しない。

2026-10-27追記：OPA3もselected／held前のhelper HOLDで実故障に未到達。helper終了・復元の後にcanonicalに記録された通常full承認1回からAPPLIED17／READY／DE effective／SQL6件成功へ進み、その正結果とhelper UNKNOWNを分けて保全した。再ARM／再承認せず正常STOP、70表・599保存物・volume／network・MAX_ONE=0を確認した。OPA／TLS実故障と最終設計検査は未完了である。

同日、新OPA4／TLS2の私用helper18を複製し、4ファイルだけで厳格な通常readonly IPCの透過・保持中の処理・元socket復元を補完した。既存284 test methodのASTと製品source201・旧helper18を保ち、追加7件を含む各149件の実nativeと独立ROを成功した。FULL承認CLIは正規のWorkbench base-urlを明示し、非readonly DISPATCHを透過しない。sandbox拒否・合成fixture準備HOLD・最終PASSを分けて保存し、これらを実OPA／TLS故障のPASSやwhole件数へ合算しない（[個別索引](database-p5-6-acceptance-2026-10-25.md#実証json索引)）。実機と最終設計検査の完了まではP5-6未完了を維持する。

同日OPA4では、実Trino queryのOPA timeout 5000msとFAILED、full UNKNOWN／未READY／effective revoked／非OPENを確認した。同一OPA PIDの復元・元socket復元とWebUI direct／reload表示、通常STOP／MAX_ONE=0まで完了し、70表・private604件・volume／networkを保持した。旧setup HOLDやOPA3の通常APPLIEDと分けて記録する。TLS実故障・最終独立照合・設計検査は未完了であり、まだP5-6を技術完了とはしない。

TLS2はcoordinatorのPATCH応答解析HOLDで実x509拒否に未到達。finallyのPATCH成功と後段のJSON解析失敗、未保存の初期例外を区別し、元field／Secret／元CAを正常確認後にSTOP・隔離した。70表・private600件・owner／volume／networkを保持し、未復元socketや旧UNKNOWNを手修復しない。製品機能変更を加えず、TLS3の9ファイルを識別子だけ変えてcloneし旧149 methodの逆ASTとnative149件を検査した。新DATAはPATCHの明示JSON応答だけを補完し、fresh専用実機へ引き継ぐ。TLS実障害・最終設計検査が終わるまでP5-6未完了を維持する。

TLS3も実x509拒否前の接続RSTでHOLDした。ALT／原CAの両peer失敗とSSH接続拒否2件を保存し、後続の元CA正常応答、元設定と70表・private600件の通常STOP保全を確認した。特定NodePort収束を断定せず、実TLS障害PASSには数えない。次の試験では製品／helperを変更せず、元deadline内の限定read-only peer待ちだけをDATAで補完する。

## 目標と引継ぎ

設定済みの専用ローカル環境で、platform adminがWebUIからDBを登録し、初期アクセスを指定し、
bootstrapとfullの計画をそれぞれ明示承認して、実検証後のREADYと通常SQL/DataOps利用まで完結できるようにする。
通常導線でgeneration ID、predecessor、digest、private path、秘密値、shell commandを手入力させない。
「WebUIで完結」はoperatorの省略や一括無条件applyを意味しない。**bootstrapとfullの2回の適用確認**を残す。

前提は[Phase 4計画](database-phase4-implementation-plan-2026-10-20.md)と
[P4-9受入報告](database-p4-9-acceptance-2026-10-23.md)。P4-1〜9の権限、DB分離、DataOps、予定、retire、
UNKNOWN回復を引き継ぐ。P4-9の既存・clean受入は保存済みの前提証拠であり、P5の新しい公開要求から
private operatorへの接続、runtime引継ぎ、WebUIでの新規作成を証明するものではない。
技術試験、実ブラウザー確認、実環境運用、所有者本人による操作性評価を別のgateとして記録する。

### 対象と前提条件

- 既存のSQL名制約、表示metadata、固定4 Schema、既存accountへのDB grant、bootstrap/fullを利用する。
  暗黙の小文字変換・別名fallbackは追加しない。nativeのmixed case/space exact-name制限も独立した残件として維持する。
- operatorのprivate設定、許可済み専用profile、接続先、CA、認証、storage/Flux/ToFuの有効な初期状態と
  canonical Workbench/native ownerは環境導入時に設定する。WebUIから任意cluster・接続先を作る範囲ではない。
- 「cleanからの再構築」は独立した空の専用環境に上記foundationを通常手順で導入した後、
  新しいDB/consumerをWebUIで作る試験とする。既存PVC、primaryのstate/auth、過去のDB成果物を流用しない。
  foundation未設定も負試験とし、準備不足を明示して停止する。隠れたcluster作成やprimaryへのfallbackは禁止する。
- account/password/SSO管理、任意Schema CRUD、汎用workflow engine、外部cloud接続、全面DR、
  外部公開・有料資源の追加は対象外。fixture専用launcherや過去の受入helperを製品の実行経路へ組み込まない。

## 現状と追加する境界

| 現状の正本 | P5で追加する内容 |
| - | - |
| [catalog_admin.py](../../tools/local_experiment/catalog_admin.py)のcreateはDB/binding/固定Schemaを登録しREGISTEREDを返す | 作成・初期desired grant・後続処理を結ぶ永続的な作成処理ID。登録成功と利用可能を分ける |
| [warehouse_operations.py](../../tools/local_experiment/warehouse_operations.py)の公開applyはREQUESTED/requires_operatorを保存する | 承認済みP5要求だけをprivate operatorへ渡す固定bridge。既存の登録APIとhandoff APIの意味は変えない |
| 同coreがglobal drain、固定6 step、CAS、bootstrap PREPARED、verified full後のREADY/grant昇格を持つ | 同じcoreを使い、WebUIの表示やbridgeで成功・READYを代行しない |
| [database_material.py](../../tools/local_experiment/database_material.py)は材料を検査し、資格情報を発行しない | 既存秘密材料を保持する増分準備と、実bootstrap発行receiptからfull材料を組む経路 |
| [warehouse_operator.py](../../tools/local_experiment/warehouse_operator.py)はprivate資格情報のprincipalと要求actorの同一性を検査する | WebUI要求者とprivate executorを監査上分離する、対象・revision・digest限定の委譲契約 |
| [runtime_profile.py](../../tools/local_experiment/runtime_profile.py)は起動時固定、[workbench.go](../../internal/cli/workbench.go)はPython childの終了を返す | canonical Go ownerによる既知childのprofile引継ぎ。HTTP child再起動をまたいでoperatorが存続する経路 |
| [管理UI](../../tools/local_experiment/workbench/index.html)は登録/grant/plan/applyの別フォームとメモリ上の要求Mapを持つ | 一連の作成導線、永続処理の再発見、実進捗、明示再計画、現在権限を検査した利用開始 |

## WebUIの通常導線

| 段階 | 利用者に示す内容・操作 | サーバーで成立させる条件 |
| - | - | - |
| 1 名前 | SQL名と表示名を区別し、固定4 Schemaを説明する | 現行の名前・予約語・重複検査。表示名はliteralとして扱う |
| 2 初期アクセス | 既存の有効accountとDB roleを選択。アクセスを付けず作成する選択も可能 | accountの最大roleを超えないdesired grant。暗黙の自己grantやglobal role変更を行わない |
| 3 内容・影響確認 | 作成DB、初期アクセス、全warehouseの利用制限・再起動、待機中の実行、必要な2回の確認を表示 | 登録・初期desired grantと処理の関連を原子的に保存。private準備と実bootstrap計画を作成する |
| 4 bootstrap確認・適用 | 固定された計画と影響を確認し、明示して適用 | 最新権限、revision、digest、private材料、排他を検査して既存operatorが実行する |
| 5 PREPARED・full確認 | 資格情報発行済み、利用は未開始と表示。実receiptを反映したfull計画を別途確認・適用 | bootstrap predecessorを固定する。fullの新しい承認なしに後続applyを送らない |
| 6 検証・利用開始 | runtime引継ぎ・実検証の進捗と、READY／利用権限を別々に表示 | verified full後のbinding/grant/READY確定、両runtime一致、現在権限を照合して新しいSQLタブを開く |

bootstrap後のfull確認待ちも、現行契約によりwarehouse controlは適用中のまま保持される。
この間の利用制限を開始前と進捗画面に明記する。待ち時間から自動承認・自動OPEN・自動rollbackを推定しない。
手順を途中で離れても同じ処理へ戻れるようにし、確認待ちの長期化は運用上の注意として表示する。

画面上の進捗は「準備待ち／計画確認待ち／適用中／full確認待ち／検証中／利用可能／再計画が必要／照合が必要」等とし、
既存DB lifecycleやOperation/stepの正本から導出する。UIの進捗のために新しいDB lifecycleを作らない。
202、Pod Ready、stepの保存、PREPAREDを「利用可能」にしない。失敗時は対象、判明した事実、停止理由、許可された次操作を示す。
照会失敗と実行失敗を分け、現在のidentityによる再照会成功時には古い接続エラー表示を消す。

処理IDはサーバー側の一覧・詳細とURLから再発見できるようにする。再読込、タブを閉じる、HTTP再起動後もGETで復帰し、
新しい作成キーを自動発行しない。技術ID・step証拠・監査は読取詳細へ段階的に開示する。
登録後に中断してもDBを自動削除しない。未送信なら実行停止を明示できるが、外部効果発生後は即時取消やrollbackを約束しない。

利用開始は最新のDB lifecycle、binding、runtime generation、effective grantと現在のplatform管理権限を取得して判定する。
初期grantなしでもREADYにできる。通常のDB利用者は「DBは利用可能・現在の利用権限なし」とし、
現行のplatform admin＋選択adminによるアクセス例外は保持して、その権限による利用であると表示する。暗黙の自己grant行は作らない。
利用開始時に新しいSQLタブを開き、SQLを自動実行しない。
元のSQLタブ、draft、DB/Schema/role/mode、localeを保持する。DataOpsは既存のrole・owner・capability制約をそのまま適用する。

既存のsystem font、色・余白・focus token、light/darkを継承し、別の管理画面デザインを導入しない。
日英、keyboardのみ、focus復帰、200% zoom、狭い画面、長い表示名を確認する。状態は文言と構造でも区別し、色だけに依存しない。

## 実装契約

### 小さな永続処理と既存coreの再利用

新規の永続記録はDB作成専用の関連・承認・claimに限定する。要求actor、環境、DB、create/metadata/grant、
bootstrap/full generationとOperation、公開action requestの各IDを結び、既存Operation/step台帳を実行状態の正本とする。
rootと登録・初期desired grantの対応は同一control store transactionで確定する。後続childは固定slotと冪等キーを持ち、
intentを効果より先に保存する。応答が失われても同じchildを再発見できることをDoDとする。

同一環境・warehouseのP5作成受付は、DB内の事前条件確認、作成受付claim、root/DB/初期grant登録を同一transactionで排他確定する。
同キーの再発見・payload照合を先に行い、異キーの競合側は登録前に409とする。双方をREGISTEREDにして互いの未適用DBで停止させない。
既存の登録専用APIも有効な作成受付claim中の別DB登録を拒否し、正当なroot内部登録だけを許可する。
この受付claimは外部applyのowned attemptとは区別し、private計画作成や外部通信をDB transaction内で行わない。
browser closeやTTLでclaimを解放しない。完了、または実効果/未解決intentを照合した明示終了でのみ解放し、
停止中のrootは同じIDから回復する。既存の未適用DBを削除して次要求を通すことはしない。

公開契約案は「作成処理の登録／一覧・詳細／計画準備／計画の明示承認／照合要求／再計画」とする。
正確なroute・CLI名・DTO・migrationはP5-0で固定し、未実装APIを既存機能として扱わない。
承認はroot/phase/Operation/revision/digest/環境に束縛し、変更後の再計画は新しい明示承認を必要とする。
同じactor・キー・payloadは同じ処理ID/DB IDを返し、同キーで内容が違えば409。GETとpollはapplyを起動しない。
既存のDB登録APIと公開handoff APIは従来どおり残し、P5で明示承認された処理だけをbridgeの対象にする。
通常CLIにも同じ公開契約を使う操作を用意し、既存の登録専用・手動operator経路との互換性を保つ。

### private operator bridgeとruntime引継ぎ

canonical Go Workbench ownerが、既存の固定operatorを一回実行するchildとして起動する構成を採る。
HTTP Pythonのrequest threadやそのprocess groupに外部applyの寿命を依存させない。新しい常駐daemonや汎用schedulerは追加しない。
private IPCは永続化済みの型付き要求IDと固定actionだけを受け、設定済みprofile/warehouse/context/state/接続先へ束縛する。
browserからargv、env、PID、private path、任意URL、kubeconfig、credentialを受け取らない。dev modeを自動適用権限にしない。

Workbench/nativeの既存Go ownerは、それぞれ自分が起動した既知のPython childだけをsealed candidate profileへ引き継ぐ。
native ownerのchild process groupも分離し、共有Go group全体のkillや任意PID探索を行わない。
operator childはこの引継ぎをまたいで存続し、両ownerの応答後、既存operatorが両方の通常HTTP runtimeを実際に再照会する。
期待generation/catalog IDs/profile_ref、固定loopback URL、CA、backend credentialの検査を省略しない。
片側だけ更新された場合はverifyとREADY確定を止める。Go owner自体のcrash時は永続intentから照合し、未確認stepを起動時に再送しない。
この所有関係をcanonical起動モデルで実現できなければP5-0へ戻して設計を更新し、受入用helperで代替しない。

### 要求者とexecutorの権限

公開管理操作は既存の**platform admin＋maximum role admin＋現在選択role admin**を必要とする。
DB admin、engineer、viewerは管理操作をできない。UI非表示に加え、API、CLI、private dispatchでも検査する。
account選択は既存の有効principalに限定し、入力されたactor/roleを本人確認の代わりにしない。Origin制約も維持する。

現行Operatorのactor同一性検査を全体で無効にしない。P5専用のserver生成private委譲を、
要求者・受理時admin選択・root/action/Operation・revision/digest・環境に限定して追加する。
private executorの技術的権限と要求者の管理権限を別々に検証し、両者を監査へ残す。
既存の同一actorによる手動operator経路は変更しない。browser passwordやsession credentialを公開台帳へ永続化しない。
異なる実在のplatform admin 2名を同じprivate executorで処理できることを確認し、単一fixture actor専用の仕組みにしない。

受理時に加え、claim前、各新規外部dispatch前、最終CAS直前に要求者の現在のaccount有効性・最大role・platform権限を検査する。
browserで選択roleを下げる、logoutする、画面を閉じることと、管理権限そのものの失効は区別する。
前者で受理済み外部操作を勝手に取消さず、以降の画面/API操作は現在のroleで拒否し、admin cacheと遅延応答を破棄する。
後者を検出した場合は、対象principalのP5変更要求・承認受付停止と未完了承認の無効化を同一transactionで永続化し、
停止理由を残して未送信stepを止める。既知receiptと不確実な結果を保持する。
in-flight効果を無条件kill・rollbackしない。検出済みまたは明示撤回済みの承認は再付与だけで復活させず、
本人の最新権限、実効果の照合、現在計画の再確認を必要とする。
別adminへの暗黙の所有権移譲は実装しない。本人を回復できない場合はprivate運用照合で安全停止を維持する。

現行account設定は現在のflagsを持つが、管理権限の単調増加epochを持たない。
MVPは検出した失効を永続的に記録する保証とし、検査間で完結する設定ファイルの失効→同一値への復元の検出は保証しない。
運用上の権限撤回も、private手順で上記受付停止と既存承認撤回を原子的に確定し、その後account設定を変更する順序を必須とする。
新規要求・承認、claim、各dispatch、最終CASはこの停止を検査する。account復元だけでは解除しない。
実効果照合・現在権限の確認後にprivateで明示解除し、失効済み承認は復活させず新しい承認を必要とする。
P5-0でこの限定操作と停止点を固定し、P5-1に実装する。内容hashだけを失効epochとみなさず、account管理全体の新設は行わない。

初期grantはdesiredとeffectiveを分ける。新DBのgrantを持たないoperatorでも独立したprivate検証ができる構造を保つ。
要求者のあるDBへのgrant失効だけでplatform管理権限が消えたと推定しない。grantに依存するデータアクセスは直ちに拒否し、
現行のplatform admin＋選択adminによるアクセス例外は維持する。選択roleを下げた場合はその例外を適用しない。
対象grantが途中変更された場合、古い候補からのeffective昇格をCASで拒否する。

### 材料・排他・READY

whole-warehouseのimmutable candidate、saved ToFu plan、state lineage/serial、registry/DB/metadata/binding/grant revision、
Secret UID/resourceVersion、public digestとprivate材料の束縛を維持する。
MVPでは無関係な未適用desired grant、別のREGISTERED DB、他の構成変更がある場合、開始前に明示して停止する。
別変更を一緒に適用して権限を昇格させない。確認後の変更もstaleとして再計画を要求し、候補へ黙って取り込まない。

増分準備は既存DBのconsumer IDとcredential、TLS/S3/JWT、retired参照を保持し、新規DB分だけを追加する。
shared policy/password DBの正当な追記は許容するが、既存entryの値を変えない。
全資格情報を再生成する`initialize_database()`を既存環境への追加経路として使わない。
bootstrapでは新規refの発行前intentと実発行receiptを保存し、その実値をprivateに保管してfull材料へ接続する。
材料のvalidationを資格情報発行の証拠にしない。公開DTOはallowlistで作り、秘密値・raw ToFu JSON・private pathを
API応答、JS、browser storage、公開log、downloadへ渡さない。秘密値を含む元証拠はprivate保管とする。

既存のglobal warehouse CAS/drain/owned attemptを利用する。DRAIN中は新規SQL/DataOpsを拒否し、
受理済みJobの継続・照会・原pinでの取消を維持する。未閉鎖UNKNOWNは適用を止める。
履歴上の`FENCED_READ_ONLY`は既存の閉鎖判定に従い、単なるUNKNOWN行数やtimeoutで可否を決めない。
予定は既存のBUSY/MISSED規則を使い、全DBの予定を勝手に無効化したり、復帰後catchupしたりしない。

bootstrapはPREPAREDであり、Trino stepは契約上PENDINGを残す。full successorで
`publish_snapshot → apply_secrets → reconcile_storage → reconcile_polaris → reconcile_trino → verify`
の実効果・receiptを成立させる。両通常runtimeの世代一致と実OPA/Polaris/TLS/SQL検証後に、
既存transactionでbinding適用、effective grant昇格、新DB READY、generation/Operation APPLIED、control OPENを確定する。
verify失敗・権限失効・staleではこの成功確定を行わない。retire競合時も既存lifecycle CASを守り、退役DBを復活させない。

### 失敗と回復

| 状況 | 動作・利用者の次操作 |
| - | - |
| foundation/private材料不足、外部送信前の既知失敗 | 理由を示して停止。同一処理を照会し、必要な運用準備または明示再計画を行う |
| 公開応答喪失、browser close、HTTP再起動 | 同一キー/処理IDを一覧・GETで照会。新規DBや新しいchildを自動作成しない |
| stale plan、競合、対象grant変更 | 元計画・証拠を保持し、最新差分から再計画と再承認。確認済みdigestを上書きしない |
| 発行・外部mutation後の結果喪失 | UNKNOWNとして送信停止。既存production reconciliationの信頼済み正の観測で実効果を照合する |
| 要求者失効、verify失敗、片側runtime不一致 | 未送信stepと成功確定を止め、現在の権限/計画/実効果を確認して明示再開する |

UNKNOWNはTTL、404、PID不在、DBが見えないことから未実行と推測しない。blind resend、架空ACK、台帳の成功書換え、
自動rollbackを禁止する。構成applyのUNKNOWNに、read-only Job用のFENCEや模擬QueryInfoを流用しない。
正の証拠を得られないケースは照合待ちとして停止を維持し、private runbookに必要な証拠と責任者を記録する。
安全な明示再開は現在の権限とcanonical照合に基づき、確認済み効果を再送しない。正常なGET復帰と外部効果の再実行を混同しない。

## 実装順序と完了条件

各unitはCommanderが対象fileとDoDを限定してworkerへ渡し、diffと検証をreviewする。
新規の汎用基盤を先に作らず、既存coreへ接続する順に進める。下記の主要対象は着手時のsource確認で確定する。

| Unit | 内容・主要対象 | 完了条件と依存 |
| - | - | - |
| P5-0 契約固定 | public DTO/route、永続migration、委譲、Go owner/IPC、foundation、失効検出/事前撤回の停止点を固定。必要なADR差分を作る | 既存環境・cleanの前提と保持対象、権限変更時のprivate手順を記録し設計review可能。後続全unitの前提 |
| P5-1 作成処理・認可 | catalog_admin/control_store/warehouse_operations/request_auth/grant。warehouse受付排他と原子的登録、初期desired、child関連、承認/claim、principal停止と承認撤回、委譲監査 | 同key再発見、異key競合の登録前拒否、CAS、2名の要求者とexecutor分離、失効後の停止保持が成立。P5-0依存 |
| P5-2 増分材料・計画 | database_generation/database_material/database_provision/warehouse_operator。private準備、実bootstrap receipt、full候補 | 既存資格情報を保ち、bootstrap/fullを別承認で結ぶ。発行応答喪失で再発行しない。P5-1依存 |
| P5-3 実dispatch・世代引継ぎ | internal/cliのWorkbench/native owner、private IPC、runtime_profile/operator。固定one-shotと両ownerのchild更新 | HTTP再起動中も実行寿命を保ち、両実runtimeの一致後だけverify。UNKNOWN再送なし。P5-1/2依存 |
| P5-4 WebUI・通常CLI | workbench_server/index、internal/cli。名前→アクセス→2回の計画確認→進捗→新SQL、永続照会/再計画 | 技術ID/秘密材料の手入力なしで通常両入口が同じ契約を使う。元draft/role/locale保持。P5-1〜3依存。2026-10-24：実装・対象導線の技術検証完了（[報告](database-p5-4-implementation-2026-10-24.md)）。P5-5〜7の全体受入は別途 |
| P5-5 故障・競合回復 | 上記core/bridge/UI。権限・target grant失効、retire、drain、応答喪失、stale、runtime片側不一致 | 下記の負試験で停止点と回復証拠が一致し、成功やREADYを誤表示しない。P5-1〜4依存。2026-10-25：実装・対象実機検証完了（[報告](database-p5-5-implementation-2026-10-24.md)）。P5-6技術受入も完了、P5-7本人受入は実施中 |
| P5-6 実機技術受入 | 既存環境＋専用clean、通常Workbench/CLI、全体検査、再起動・再配備、有限清掃、closeout報告 | P5-A01〜19を新しい証拠で満たす。制約と所有者gateを分けて記録。P5-5依存。2026-10-27：技術受入完了。両環境縦断・再配備・清掃、実owner crash、同root回復・取消の永続化、PRIMARY rev6保全に加え、OPA4／TLS4の実故障・UNKNOWN停止・復元・正常STOPと最終設計検査を確認。旧CLAIMED／UNKNOWNの隔離証拠を保全し、P5-7本人受入は実施中（[受入表と個別証拠](database-p5-6-acceptance-2026-10-25.md#受入束)） |
| P5-7 所有者操作性受入 | 所有者本人が作成・確認・失敗説明・回復導線・SQL/DataOpsを操作する | 2026-10-28：ADR-0038新方式へ切替後、1回の作成からSQL・実データ・Job自動成功・reload・閲覧者制限・管理者復帰・Job定義保持まで本人確認。作成導線は条件付き受入、待ち時間の改善を継続条件とし、暫定p50≤2秒/p95≤5秒は未達として[性能改善](database-logical-create-performance-2026-10-28.md)を進める。[本人記録](database-logical-create-owner-acceptance-2026-10-28.md)。従来方式の記録は保存する |

UIのcomponent確認は先行できるが、stubの成功表示をP5-3/4完了に数えない。
P5-6で技術PASSとなってもP5-7を自動完了にしない。未解決UNKNOWNや未確認外部効果のある環境では次の適用を行わず、停止・隔離して元証拠を保全する。別cleanでの正の検証は新しいidentity・材料・volume・networkで行い、隔離環境の回復済みとは扱わない。

## 実機受入matrix

以下は**受入条件**であり、現在の実施結果は[P5-6受入記録](database-p5-6-acceptance-2026-10-25.md)に分けて記載する。P4の証拠の転記、fixtureだけの成功、HTTP202だけの確認でPASSにしない。
各ケースは対象要求ごとの副作用を測定する。拒否要求の送信0と、正規operatorが行う検証SQLを分けて記録する。
fault注入は専用の所有対象と限定した停止点へ行い、実際の最初のintent・応答・効果を保存する。

| ID | 操作・故障 | 合格条件・必要な実証 |
| - | - | - |
| P5-A01 | 既存環境でWebUIから新規DBを作成 | terminal/private JSON/台帳補正なしで、名前・初期アクセス・bootstrap承認・PREPARED・full承認・READYまで完走。実consumer発行とfullの6 step receiptが同一処理へ結び付く |
| P5-A02 | 通常CLI/APIと既存入口 | 新しいCLIで同じ承認/照会契約が成立し、旧登録はREGISTERED、旧handoffはrequires_operatorのまま。READY後は実Workbenchと通常CLIの型付きSQLが成功し、両runtimeが期待generation/profile_refと一致 |
| P5-A03 | 実accountによる認可matrix | platform admin 2名＋1 executorの成功を確認。DB admin/engineer/viewer、最大adminだが選択engineer、偽actor、異常Origin、private path/argv/URL注入をAPI/CLI/GUIで拒否。拒否要求に起因する外部dispatch 0、秘密漏洩0 |
| P5-A04 | 初期アクセス・未付与・失効 | REGISTERED/PREPARED、desiredのみのgrantでは通常利用不可。verified full後だけeffective昇格。初期grantなしのREADYでは通常利用者を拒否し、既存のplatform admin＋選択admin例外による利用は維持・明示。暗黙自己grant、role ceiling超過、別DB権限の流用なし |
| P5-A05 | 名前の実境界 | 有効SQL名の1/31文字・通常名を実作成→READY→SQLで確認。32文字、予約語、重複、不正文法は登録/発行/provider効果0（拒否監査は可）。表示名の日英/HTML文字はliteral。別registryでの同名も環境分離を保持 |
| P5-A06 | 二重クリック・並列要求 | 同キー同内容は同一DB/処理/child、異内容は409。2 browser＋CLIの異キー同時受付では一方だけ登録、他方は409でDB/grant追加0、勝者は完走可能。旧登録APIとの競合、2 operator claimも確認し、consumer/外部mutation重複・宣言上書き・受理処理の行方不明なし |
| P5-A07 | 未適用の別変更・stale | 無関係なpending grant/別REGISTERED DB/構成差分はpreflightで停止。確認後にDB/metadata/grant/binding、Secret UID、state lineage/serial、材料/digestを変更してstale拒否。権限変更の便乗適用なし |
| P5-A08 | 公開受理の前後で応答喪失 | 実受理応答を失っても同キー/一覧/GETで同じIDへ復帰。browser close/reload、HTTP child再起動後も同じ処理を表示し、新規作成キーやchildを自動生成しない |
| P5-A09 | 実発行・mutation後のreceipt喪失 | 実効果後にUNKNOWNとなる例を作り、正のproduction観測で照合して明示再開。最初のintent/欠落/効果を保持し、再発行・再送・架空ACKなし。正の証拠がない場合の停止も確認 |
| P5-A10 | bootstrap/fullの確認待ち・runtime切替 | full未承認の間は新たなapply/READY/OPENなし。実切替中にHTTPを再起動しoperator存続を確認。片側旧profileはverify拒否、両側一致後だけ確定。Go owner crashも未確認stepを自動replayしない |
| P5-A11 | 要求者権限・対象grant失効 | claim前、phase間、外部in-flight、最終commit前でaccount/最大role/platform権限を失効。検出後のprincipal受付停止/未完了承認無効化を原子的に永続化。private事前停止/撤回と設定変更の間の新規承認も拒否し、設定復元だけでは再開しない。対象grant変更はepoch/CASで古い昇格を拒否。実効果照合・private解除・本人再承認で回復 |
| P5-A12 | 実Job・予定とglobal drain | 既に受理済みのJobは元contextで継続/取消でき、新規受付は拒否。未閉鎖UNKNOWNでは待機し、閉鎖済みFENCED履歴だけなら妨げない。予定はBUSY/MISSEDとなりcatchupせず、無関係な予定の定義を保持 |
| P5-A13 | 実verify・材料不一致 | OPA/Polaris/TLS/Secret/profile不一致と実検証失敗でREADY/effective/APPLIED/OPENを成功確定しない。bootstrapのTrino PENDINGとfullの6 step成功を区別。private材料不足で架空資格情報を作らない |
| P5-A14 | retireとの競合 | 現行lifecycleが許す退役開始点で、対象/候補内DBのretireとapplyを競合させる。勝者以外はCAS/排他で停止し、stale full、再plan、再起動でRETIRING/RETIREDを復活させない。物理データ・credential参照・履歴を保持 |
| P5-A15 | 専用cleanでの再構築 | node UID/API/CA/auth/state/volumesを独立させ、空のfoundationから新DB ID/consumerをWebUIで作りbootstrap→full→READY→両入口SQLを確認。foundation欠落は明示停止。primaryへの接続/変更や旧PVC流用なし |
| P5-A16 | 3DBとP4機能への接続 | P5で作った3DBの同名Table/Job等を別ID/Ref/UUID/型付き値で識別し、cross-DB参照・SQLをbackend前に拒否。READY後のStage ingest/scan、Mart更新、Job/Export、予定・alertを代表縦断し既存guardが働く。既存・cleanの双方で通常入口を通す |
| P5-A17 | 永続化・既存環境の再起動/再配備 | 途中処理とREADY後を通常再起動・verified full再配備で照合。既存consumer、DB ID、Table UUID/Snapshot/型/行、grant、tombstone、予定と回復履歴を保持。新DBも同一identityで利用できる |
| P5-A18 | 実ブラウザーの回復・操作性 | 元2SQLタブ/draft/DB/Schema/role/mode/locale保持。役割切替・失効でadmin cache/遅延応答を破棄し、旧接続エラーは正常再照会で消える。日英/keyboard/focus/200% zoom/狭い画面、長い名前、待機・UNKNOWN・権限なしの表示を実操作で確認 |
| P5-A19 | 検査と有限清掃 | focused→当時のPython/Go/設計全体検査、両環境doctor、実SQLを通す。所有予定停止・fixture grant剥奪の適用・専用DBの非purge退役・元認証復元・通常入口再起動を照合。exactな所有processだけ停止し、元環境と保持対象を変更しない |
| P5-A20 | 所有者本人の受入 | 所有者がWebUIだけで作成→2回の影響確認→READY→SQL/DataOpsを操作し、全体利用制限、初期権限、失敗時の停止/再開理由を理解できる。本人評価とblocking指摘の解消を記録。agent/CUAの代行PASSはP5-7の代わりにしない |

## 証拠、清掃、closeout

受入開始時にsourceと実profile、actor/role、DB/処理/child/Operation/generationのID、
元認証・既存client設定・元SQLタブ・物理identity・台帳・予定のbeforeを記録する。
公開HTTP、CLI exit、実ブラウザー、private operator receipt、backend直接観測を別々に採取し、実効果件数を対応付ける。
秘密を含むfirst response・raw provider/ToFu証拠はprivateに保存し、報告書には安全な参照と要約だけを記す。
試験補助側の観測失敗は最初の失敗を残す。外部効果がないと確認できたfixture修正と、製品の再送・台帳補正を混同しない。

清掃対象はP5が所有したIDとprocessに限定する。元の4 accountや既存設定の数は開始時に再確認し、過去の数を固定値として扱わない。
所有予定を止め、必要なfixture grant剥奪をverified fullで確認し、認証を戻してから通常入口の元アクセスを再検証する。
所有DBは非purgeの退役とし、必要な物理データ・履歴を保持する。cleanの停止は所有した正確なprocess/containerへ行い、
primary、他worktree、global kubeconfig、専用profile外のColimaを変更しない。
過去のFENCED履歴、未連結UNKNOWN予約、retired値、意図したhost-local設定差分をP5の清掃で書き換えない。
Go owner crashや応答喪失等で未確認効果・STOP・claimを残す専用scopeには、上記の停止・隔離規則を優先する。
正常な所有DBは非purge退役し、故障scopeは報告書に環境・所有ID・未解決点を列挙して、正確な所有process/containerだけを停止する。物理データ・材料・履歴・claimを保全し、退役のための追加適用や回復済みの扱いはしない。

P5-6報告にはmatrixの各結果・未実施・制約・回復不能な停止条件、両環境の最終照合、所有物の処置を記録する。
技術PASSとP5-7所有者受入を別欄に置き、全blocking項目と本人評価が揃うまでPhase 5全体完了としない。
本計画の文書検査PASSは、製品実装やこれら実機受入のPASSを意味しない。
