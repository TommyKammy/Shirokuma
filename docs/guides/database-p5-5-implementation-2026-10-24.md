# P5-5 故障・競合回復の実装と実機検証（2026-10-24〜25）

状態: **P5-5の実装・対象実機検証完了（2026-10-25 JST）**。WebUIの正のUNKNOWN回復・実失効停止・明示RELEASE・別承認full・READY・実SQL、通常CLIの権限matrix、非purge退役、最終保全確認を完了した。P5-6の統合技術受入、P5-7の所有者本人による受入は未完了。

[Phase5計画](database-phase5-implementation-plan-2026-10-23.md)と[P5-0契約](database-p5-0-contract-2026-10-23.md)を使い、P5-4までの公開API・Workbench・通常CLI・private operatorを再利用する。UNKNOWNのreset、receipt補作、証拠のない外部呼出し再送で回復しない。

## 製品の変更

- 明示再計画は、初期principal・roleの範囲内で現在のdesired grantと実アカウント上限を再確認する。取消済みgrantを再生せず、実bootstrap receiptと既発行値を引き継ぐ。
- verify前のUNKNOWNも、同じOperation・claim・cursor・seal・ownerに拘束した正のproduction観測で分類する。停止中は証拠記録に限定し、private RELEASEにも新しい観測を要求する。失効済み承認を復活させない。
- fullのSecret適用応答を失った場合、private receiptの `secret_after` が欠落しているときだけ、同Operation・actor・generation・delivery・phase・predecessorと元の `secrets-observed` digestに一致するcanonical baselineを使う。明示された不正値を欠落扱いにせず、旧形式は初期UID検証を維持する。読み取りは既存DBへのmode=ro/query_onlyで行い、保存planのIO前に接続を閉じる。DB・旧表・権限を作成／変更しない。
- CLIの準備待ち表示は、最新のPREPARATION_PENDINGかつblockerなしの場合に限定する。WebUIでは限定回復と最新fullのreview・同意・最終承認を別に扱う。
- 最初のmanaged rootの固定base親は、封印済みprofile・private材料・tree・ownerで証明する。現runtimeが旧／候補のどちらかは既存projection/recoveryで別に検証し、正しい片側切替を親証明の時点で拒否しない。閉じた旧／候補ID、候補profile、同じownerの検査は維持する。

元source704件のうち、対象の製品・テスト12件を変更し、692件を保持した。base親の修正では既存対象2ファイルとbridge回帰テストだけを変更し、bridge本体は維持する。新しい実機用helperは専用の `var/shirokuma/` 配下に置き、既存P53/P54、旧fixture、renderer、保存済みmanifest・receiptは上書きしない。

## 実機結果

| 対象 | 現在の実証 |
| - | - |
| 作成受付応答喪失 | 通常CLIの実plan受付202だけを切断。同key再照会は元RID・同sequence 1 planへ戻り、PREPARE 1／APPROVE 0／operator mutation 0を維持 |
| 競合・権限拒否 | 別管理者の同名・別名作成をCREATION_BUSYで拒否。DE／BIの管理操作はADMIN_REQUIRED。WebUIのrole ceiling低下でDB管理を非表示にしSQL下書きを保持 |
| grant取消・stale | 実bootstrap PREPARED後にWebUIでDE、別管理者の通常CLIでBIを取消。旧full reviewの承認はSTALE_REVIEW、要求・適用・発行増分0。新review／承認で現在desiredだけを適用 |
| full適用応答喪失 | 実ToFu upstream 1回・exit 0の応答を失わせapply_secretsをUNKNOWNにした。正のproduction観測→旧承認REVOKED→新review／承認で残4stepを完了、APPLIED／READYへ到達 |
| 資格情報引継ぎ | consumer再発行0。実full receiptのsecret_after／state_afterは最後まで欠落したまま、新2 Secretはcanonical16 Secret・元観測digestに一致。元Secret UID／dataとprivate値を保持 |
| 通常CLI SQL | 取消済みDE／BIの送信前拒否に加え、正の回復後は既存client profileを編集せず8ケース・63項目PASS。admin／DE sandbox CTAS・SELECT／BI読取り／baseline履歴用SQLの5件がFINISHED、current native generation・typed context・実backendに一致。BI書込み・DEのbaseline context／qualified横断3件はquery/context/admission/backend追加0で拒否。DEの実Tableは1行を保持 |
| drain・予定 | 受理済み実SQL JobのRUNNING・backend ID・元contextを確認してbootstrap承認。予定tickはskipped／DRAIN／run_id=null、元Jobは元contextでcancel→canceled、未閉鎖admission 0 |
| stale retire | 保存したREADY revisionを、実DBがRECONCILINGになった後に送信。通常CLI失敗、WebUIはREVISION_CONFLICTを表示。実HTTP 409はcaptureしておらず主張しない |
| 非purge退役 | 専用baselineのfresh preview（READY/r1・blockerなし）から通常CLIでretireを1回送信、RETIRING後は別key／operation r1の明示reconcileを1回実行しRETIRED/r3へ確定。GETの自動完了は仮定しない。退役後のSQLはquery/context/admission/backend追加0で拒否。元FINISHED履歴、16 SecretのUID/data/immutable、新DB READY／有効grant／OPENを保持し14項目PASS |
| 片側runtime | WebUIで実fullを別承認。WB実切替ACKを1回失い、WB新runtime／native旧runtimeでUNKNOWN・照合要求となり、READY／grant昇格／SQL dispatchを行わない |
| 限定回復 | 公開WebUI照合でWB COMPLETE／native UNSENTに固定したVERIFY_RECOVERYを生成。実失効後の停止中観測・個別RELEASE・新review／同意／最終承認でnativeだけを切替。さらに普通のfullを別承認しAPPLIED／READY／OPEN・DE/BI effective昇格へ到達。ToFu再適用・consumer再発行・WB切替replayなし |
| 実権限喪失・復元 | 正規private STOP後、実認証からexecutor除去＋要求者platform_admin=false。CLI list/showは実ADMIN_REQUIRED、session 200かつplatform_admin=false、利用可能DBは空、query/context/admission/backend増分0。WebUIはDB管理とadmin cacheを破棄し、2SQL下書き・元DB/Schema・role・mode・日本語を保持 |
| 永続STOP | 元認証bytes／5accountsを完全復元しても両gateはSTOPPED/r2、旧full／回復承認はREVOKED、bootstrap CONSUMED不変。WebUI／通常CLIはPRINCIPAL_STOPPEDと次操作なしを表示 |
| 実Go失効停止と明示解除 | fresh限定回復中に実認証からexecutorだけを除去。Goが1回の実要求を拒否してnative UNSENT／executor STOP2 AUTHORITY_LOST、旧fullと失敗RRをREVOKEDにする。厳密な停止中FD観測1回からproductionが新しい実行可能な回復分類を保存。executorと要求者は別processの新しいproduction観測・別keyのRELEASEで各ACTIVE3へ戻り、旧承認は失効のまま |
| WebUI回復後のSQL・永続表示 | READYから新SQLを開くと3枚目に新DB／marts／admin／read-onlyを設定。identity SELECTが実完了しshirokuma_admin／p55readyを返す。作成URLの直接遷移・再読込後もREADY／effectiveを保持し、元2下書き・旧DB／marts・role／mode／日本語、新3枚目のSQLとcontextを維持 |

通常CLIの一般的なSQLエラーからHTTP statusを推定しない。drainの実証は受理済みJobと予定tickの結果であり、非READY DBへのSQLがSTALE_BINDINGで先に拒否される場合をDRAIN拒否と数えない。P5-A11の全失効境界を今回すべて実機実証したとはせず、既存P53/P54実証・native回帰とP5-6での追加確認を分ける。

## 隔離したUNKNOWNと正の回復試験

1. `p5-5-acceptance`：ストレージ応答喪失の注入は、補助ツールの選択intent前に停止して不成立。最後のchildはsuspend=trueのままなので、全child完了を正に証明できずUNKNOWNを保持。解除・再送・手動unsuspendは行わず、元receipt・履歴・4volume・networkを保持して停止した。実202喪失・競合・drain・stale retireの負試験証拠は有効。
2. `p5-5-continuity-acceptance`：通常CLI側 `p55continuity` の実UNKNOWN回復・READY・SQLを確認した。WebUI側 `p55recovery` のnative権限喪失注入はGoへ転送する前にfault guardで止まり、認証変更・Go受理記録・SQL dispatchは0。親native INTENTだけが残ったため、journal欠落をUNSENTへ読み替えずUNKNOWNを保持した。正規private STOPと実認証喪失／完全復元の試験後、実証済みA履歴とBのUNKNOWN・99元証拠・DB全表を保持して停止した。managed/foundation停止は各1回、既知7プロセス停止、導出10ポート閉鎖、4volume・network保持。
3. `p5-5-web-recovery-acceptance`：新5アカウントと実baseline exportを生成し、WebUIでbootstrap PREPARED、別承認fullの実WB切替ACK喪失（upstream 1回、SQL 0件）、WB新／native旧、UNKNOWN/r16を確認した。最初のmanaged rootで、固定base親の検証が現WBにも旧runtime一致を求めて照合を非提示にする製品不具合を発見した。両principalを正規private STOP/r2、元full承認REVOKEDとして、認証5件、UNKNOWN、DB全表、78元証拠、source129、製品3ファイルの変更前bytesを保全し停止。既知process終了・107対象ポート閉鎖・4volume/network保持は17項目PASS。稼働環境のsource固定を上書きして回復済みとは扱わない。
4. `p5-5-base-recovery-acceptance`：修正後の専用foundationは初回3段階成功、10 images・4 controllers ready・root suspendedを実確認した。準備時にauth出力先を先に作ったため、既存の未存在ガードで初回authが保存前に停止した。資格ファイル・metadata DB・create/exportは0で、原intent/UNKNOWNと12項目の正の保存前証拠を保持した。専用のfixture回復機能やguard迂回は追加せず、foundation停止を1回実行し、105対象ポート閉鎖・4volume/network・元18証拠の保持を15項目で確認した。
5. `p5-5-ready-recovery-acceptance`：修正後の正のWebUI回復を別clean環境で実証した。新5アカウントを既存factoryで初回発行し、実baseline bootstrap/fullを別承認。foundation transport初回UNKNOWNは元receiptを保持し、既存照合1回でapply増分0・署名付きS3 HEAD200・検証済みHTTPS GET200へ回復した。source129／helper14を固定したmanaged owner上で新DBをWebUI作成し、bootstrapの実consumer 2件・導出82 ACK、既存材料保持を確認した。別承認fullのWB切替ACKを実際に1回失い、WB新／native旧・UNKNOWN/r16・SQL0を記録。最初のmanaged baseでも正の照合を提供することを独立15項目で確認した。限定回復の実executor失効→Go UNSENT／AUTHORITY_LOST STOP→要求者の事前STOP／実platform失効→元5認証の完全復元→厳密な停止中実観測→2件のfresh RELEASE→新限定回復承認→native切替→別full承認でREADYへ到達した。現在のfull receiptの実検証SQLは宣言と同じ6件。元UNKNOWN／失敗RR／各失効理由／proxy原inode／新旧証拠を保持し、WebUI identity SQLと再読込後のdraft・contextも確認した。通常CLI8ケースと専用baselineの非purge退役を終え、新DBを利用可能なまま保持している。

最初の4環境は停止・隔離しており、次の適用先には使わない。証拠の不足を成功へ読み替えず、別環境・別DB・別資格情報・別network・別volumeで正の経路を確認した。

## 回帰と保全

- base親修正前のfocusedはoperator49・recovery32・core57の計138件成功。最小修正後はrecovery/bridgeの49件を通常環境で全成功（skip/error 0）。新3ケースの修正前red、修正後green、workspace-write sandboxの既存socket制約、最終native PASSを区別し、重複加算しない。
- 最終全体回帰v4は `make PYTHON=tools/local_experiment/dataops/.venv/bin/python3 verify-local-experiment`：76組、2222件中33 skip、2189件実行、全組成功／native exit 0。修正前v3（2219件）とv2のsystem Python起動指定不備のログは別に保持。
- Go回帰成功、入力34件不変。最終設計確認は `make PYTHON=tools/local_experiment/dataops/.venv/bin/python3 verify-design-context`。実行結果は専用環境の `private/final-design-verification.json` を参照。
- continuityの実機helperは35＋59＋20＋16の130件を確認。新webコピーはsetup35件とfault67・停止観測22・ToFu16の計140件、AST13件成功。fault105件は通常review環境でUnix socket試験も実行し、skip 0を確認した。helperのnative成功を実機受入へ読み替えない。
- primaryはPOST・product import・migration・再起動を行わない。最終primary＋停止済み12環境の独立RO照合は127項目PASS。primaryのschema19、query2794、db_job_runs85、admission3993、意味上の未閉鎖0、READY5／retired2、FENCED_READ_ONLY UNKNOWN3、schedule unknown1、P5関連4表は各0を維持し、元process／auth／runtime／Kube identityも一致。旧12環境は停止・48volume／12networkを保持した。旧C環境の元選択5要求と明示STOP監査2件の投影を区別し、collector原本と訂正証拠を保持して要求を再送していない。証拠は最後の専用環境の `private/preflight/primary-old12-preservation-final-v2-projection-correction.json`。
- 原704 map、旧P54 harness8／125 seal、owner metadata18、旧130 manifest bytesを保持する。共有operatorとrecoveryの承認済み変更は変更前backupを残し、旧130の現在contentが全一致すると主張しない。
- 最後の専用環境は独立30項目PASS。root APPLIEDとDB READYを区別し、予約0、baseline retired、実6件のFINISHED query/context/admission/backend、元5認証・両ACTIVE3・元owner identity、proxy停止、source129／helper14一致を確認した。正常なmanaged owner・foundation tunnelは稼働保持している。証拠は `private/preflight/r-final-positive-recovery-independent-v2-projection-correction.json`。

## 証拠と後続gate

private証拠の起点：

- `var/shirokuma/p5-5-acceptance/private/`
- `var/shirokuma/p5-5-continuity-acceptance/private/`
- `var/shirokuma/p5-5-web-recovery-acceptance/private/`
- `var/shirokuma/p5-5-base-recovery-acceptance/private/`
- `var/shirokuma/p5-5-ready-recovery-acceptance/private/`

公開文書へ資格情報・capability・実材料を転記しない。P5-6の既存環境／専用cleanの再起動・再配備を含む統合技術受入、P5-7の所有者本人の操作性受入は、このP5-5の結果から自動完了にしない。

最後の専用環境の主な証拠は `private/faults/` 以下に保存している：`observations/r-final-ready-ui-query-ro-v1.json`、`public-cli/r-final-eight-summary-v1.json`、`public-cli/r-baseline-nonpurge-retirement-final-v1.json`、`browser/r-ready-after-retirement-settled.png`。誤った投影条件で停止したcollectorの原本は保持し、既存の実証拠を読み直したcompanion訂正と区別する。訂正のために公開要求・SQL・外部適用を再送していない。
