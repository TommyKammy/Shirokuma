# P4-4 ローカルStage 実装・実機受入報告

2026-10-21〜22。状態：**P4-4の限定技術受入完了**。
対象は[Phase4計画](database-phase4-implementation-plan-2026-10-20.md)のP4-4。
自動scan・内容dedup・予定実行はP4-5/P4-7に残す。

## 開始時の照合

通常Workbench 8888とnative API/CLI 8080はともに`p43-recovery-final`、5DBで一致。
LOCAL OPEN、未解決admission 0、旧Stage/Job/schedule 0、台帳schema 13を確認した。
過去UNKNOWN 2件は`FENCED_READ_ONLY`のまま保持し、再送対象にしない。
元SQLタブ2件のSQL・DB・Schema・role・実行不可状態をブラウザーで保存した。

## 実装した境界

StageはDB内UUIDとadmin ownerを持ち、定義CAS、許可済みlocal root、directoryの重複登録拒否を備える。
受付前はfileと設定の変更を検出し、受付後は固定したbytesをP3 Ingestへ渡す。
ExportはP3の開始時Snapshot・全件CSV/JSONLを継承し、完了artifactだけを`exports/`へ公開する。
公開と記帳の不確実性を分け、公開済みfileの保持と同名への二重公開拒否を維持する。

開始確認で、通常DB grantがengineerまで、selected adminはplatform admin限定でDB grantを迂回する前提が見つかった。
Stageの失効を通常grantで検証するため、global admin上限を持つ利用者へのDB単位admin grantを前提実装に含める。
platform admin属性と管理mutation権限は昇格させず、DB専用backend identity・effective epoch・backend検証後の付与を使う。
旧2role generationの内容と検証証拠を保持し、新3role generationへの移行を区別する。

通常CLIはHTTP API経由でStageを操作し、既定の8080からDB・Schemaを解決する。
native APIとWorkbenchは同じinventory routeとStage実行境界を使う。
native Python起動は既存のWorkbench用venv選択を共用し、CSV出力に必要なPython 3.12を使う。
APFSで大文字小文字の異なるpathが同じdirectoryを指す場合も、他owner/DBのStage登録を拒否する。

## 完了した実機検証

`p44-stage-initial`のfull applyでは、既存5DB・20 Schemaと元のgrantを保持した。
追加した非platform-adminのowner/other-adminへのDB単位admin grantは、
実backendでのidentity・Schema検証後にeffectiveとなった。

通常APIでSALES/FINANCE/任意名DBの型付き取込とCSV/JSONL出力を確認した。
他owner・他DB、CAS競合、許可root外、直接file名、部分file、容量・件数上限、
APFS aliasの拒否と、実ViewのExport拒否も確認した。
APIのcheck記録には初回helper期待値の誤り2件を保持し、修正後の照合証拠を別に残した。

通常CLIでは、追加のAPI URLやcontext fileを指定せずに3DBのStage一覧を取得した。
続く手動操作は43項目を照合した。実際に受付・commitした入力はSALESの`batch.csv`、
FINANCEの`batch.json`、任意名DBの`batch.jsonl`で、各DBからCSV/JSONLを出力した。
viewerと他ownerを拒否し、engineerは利用可能なSALES contextでStage操作を拒否した。

初回API/CLIの6取込は親が`FINISHED`・commit済みとなったが、scoped-adminのOPA allowlistに`DropTable`がなく、
一時テーブルの清掃が拒否された。既存のDB/4 Schema/table名guardを保って
`DropTable`だけを追加した。残った6個の一時テーブルはRef・incarnation・Table UUIDと
Iceberg snapshotのquery所有証拠を保存し、元の取込を再送せず、最終清掃の24対象に含めて削除した。
元の`cleanup_error=DATAOPS_STEP_FAILED`と`cleanup=failed`を成功に書き換えない。

修正後の`p44-stage-cleanup`では実OPA 70件とbackend検証を通し、
別の3DB新規取込・6 Exportを76項目で照合した。
27子SQLすべてが`FINISHED`となり、実際の一時Table DROPと、前後の権限を確認した404を含む。
full applyの最初のverify未送信は不変traceで確認して正式に`NOT_EXECUTED`へ照合し、
同じOperationのverifyだけを再開した。前段5 mutationを繰り返していない。

Workbenchでは通常8888の3DBとowner専用8891のSALESから、新規Stage保存・
CSV/JSON/JSONLの型付きIngest各1件、CSV/JSONL Export各2件を操作した。
保存した実受付IDに対するGETのみの検証は536/536成功し、4 TableのRef・UUID、
型付き2行、8出力、全子SQLと一時Table清掃を照合した。
通常Workbenchの元SQLタブ2件のSQL・context・実行不可状態も途中照合で保持した。

受付前のfile変更・設定変更は再確認を要求し、受付後にfileとbatch設定を変えても、
固定済みbytes・設定で取込を完了した。次batchの送信前に既知の失敗を起こしたケースでは、
旧Tableの4行・UUID・Snapshotを保持して一時Tableだけを清掃した。
ExportのCOUNT後に同じTableへ実appendを行い、現在の5行に対して、
出力は開始時Snapshotの4行に固定された。fixture SQLの最初の型変換失敗と、
別intentによる修正後の成功をともに保存し、元Exportは再送していない。

公開前CAS競合・予約済み出力名・物理ファイル衝突を拒否し、旧bytesを保持した。
物理衝突時の内部P3 artifactとStage公開receiptを区別し、公開一覧除外とreceipt取得404を確認した。
公開後の記帳失敗1件では、実ファイルと2子SQLの完了を伴う`UNKNOWN`を保存した後、
同じ実行のObserveで`FINISHED`へ正照合した。追加SQL・再公開はなく、inode・610 bytes・内容も同一だった。

別の受付済みIngestをSQL送信前で止め、専用TableをDROP/CREATEした。
新UUID・incarnation 2と旧Ref退役を確認して元runを解放すると、
`STALE_RESOURCE`で失敗し、子SQL 0・新Tableの0行を保持した。
DROP後の404形式をhelperが誤認したため、完了済みDROPは再送せず、
未送信CREATEだけを別の固定intent・TLS接続・初回ACKで実行し、両証拠を結び付けた。

別のExportではCOUNT 2行後に同UUIDへappendし、実`expire_snapshots`で
旧Snapshot `8537465663627949542`を消し、現在head `8594620261660640522`だけを残した。
元runは`EXPORT_SNAPSHOT_UNAVAILABLE`で失敗し、COUNTのみ完了、SELECT 0・出力なしだった。
この検証で、既存の取得不能receipt 2件がExport一覧全体を409にする不具合を検出した。
一覧は403/404/409の取得不能receiptだけを隠し、返却直前に再認可する限定修正を行った。
500/503等の障害は伝播し、個別取得の拒否を維持する。通常3入口を同世代で再起動し、
旧2件のreceipt/contentは409のまま、新しい現在Snapshotの3行Exportだけが
一覧200・取得200となる混在状態を実機で確認した。新規2子SQLだけが増え、元runの再送はない。

Stageの受付済み202を永続化した後、HTTP応答だけを失わせたケースも確認した。
元のCOUNT/SELECTは完了し、明示した同key Observeは同じrun・Operationを返した。
Observe前後の5台帳件数は完全に同じで、追加SQL・admissionは0、型付き5行の出力を保持した。

公開前のExportで通常DB grantの降格・剥奪・restore pendingを試し、取得拒否とStage未公開を確認した。
剥奪後に同じrunを解放すると`DB_ACCESS_REVOKED`で失敗し、内部artifactと完了済み2子SQLを保持した。
続く実OPA 70件と元のbackend検証15 SQLを経て`p44-stage-regrant`を適用し、
既存資産・通常入口・再付与の37/37照合が成功した。ownerのSALES grantは検証後にadminへ戻った。

再付与後の通常CLI新規upsertはSALESの1件だけが受付され、
`FAILED / committed=false / cleanup=finished`となった。FINANCE・任意名DBはこの窓では未実行である。
実backendのMERGEで列UPDATEの権限拒否を確認し、
[Trino 483のOPA実装](https://raw.githubusercontent.com/trinodb/trino/483/plugin/trino-opa/src/main/java/io/trino/plugin/opa/OpaAccessControl.java)と照合した。
既存のDB・固定4 Schema・非空table名guard内に`UpdateTableColumns`だけを追加した。
元runと初回ACK、既知の失敗結果は保持し、修正後の実機受入には別の新intentを使った。

更新policyは`p44-stage-upsert`の実OPA 145項目と元のbackend検証を通して適用した。
通常3入口と既存資産の37/37照合後、通常CLIから3DBへ別々の新intentでupsertし、
27子SQLすべての`FINISHED`、commit、一時Tableの清掃、同じRef・UUID・型付き2行を確認した。
続くSALES CSV 1件の新規Export・取得も成功した。定義のbindingは明示CASで更新し、
新contextでStageを取得でき、旧contextでのrun・receipt・content取得を拒否した。
追加の旧context照合は25/25成功し、SQL・admissionの追加はなかった。

作成時の所有者・Ref・実体UUID・初回ACKを照合して、清掃対象を23 TablesとView 1件に固定した。
24件すべてのDROPが`FINISHED`となり、同じ権限contextでの再取得はすべて404だった。
別途、固定した一覧の1,281ファイルを内容hash・サイズで事前照合し、1件ごとの削除記録と全件の不在を確認した。
Stage定義・directory claim・受入bytes・実行履歴・監査と証拠コピーは保持した。
検証用6 grantの最終剥奪後も、SQL・DataOps・admission・Stage・Stage runの件数は変わらなかった。
独立した読み取り専用照合は125/125成功した。清掃前後でcanonical全51テーブルの内容hashが一致し、
その後の6 grant剥奪による5テーブルの差分を分離した。元5DB・20Schema・8 grantと開始時280 Ref identity、
修復前318 Ref履歴行と修復用の退役済み一時Table履歴3行を保持した。
この清掃はcatalog上の不在と指定local fileの削除を確認したもので、object storageの物理purgeは主張しない。
最終剥奪後の通常入口は34/34照合が成功した。検証用2利用者のDB一覧は空、6 DB/利用者組のSchemaは404で隠され、
旧full9 contextのStage取得は6件とも`403 DB_ACCESS_REVOKED`だった。
通常CLIはfresh named contextと旧contextの双方で終了1・保護内容の出力なしとなった。
CLIはHTTPの詳細を伏せる既存契約なので、403は同一profile・contextの別API証拠として区別する。
これらの照合によるSQL・grant mutation・5台帳件数の増分は0だった。
最終planはgeneration IDと検証用6 grantのrole・revisionだけの差分を確認して適用した。
`p44-stage-final`は`APPLIED`、6処理すべて`SUCCEEDED`となり、LOCALは`OPEN`へ戻った。
検証用6 grantはdesired・effectiveともrevoked、applied revisionとgenerationも一致した。
最終適用後もquery 1,689、DataOps 599、admission 2,326、Stage 23、Stage run 68のままだった。
最終runtimeの38/38照合が成功し、通常8888/8080とowner用8891は同じ最終generationを返した。
実OPA 145件・既存backend検証15 SQLが成功し、元DB・Schema・grant・Ref履歴・31 Secretを保持した。
認証撤去前のdoctorはhealthyだった。これは、下記の認証撤去後のdoctor・独立通常SQLとは区別する。

## 受入と清掃

UPDATE権限の限定修正後に`make verify-local-experiment`を再実行し、
55スイート、1,547件中1,514件成功・33件skip、失敗0だった。更新後の193ファイルmanifestを固定した。
skipはホストOPA不在2件、任意pyarrow不在1件、過去Phaseのopt-in実機検証30件。
通常CLIのGo検証は172件成功。設計・context/hygiene検証も成功した。
security契約検証34件は成功したが、security全体の終了コードは2で、
既存のホスト固有設定`opentofu/dev/variables.tf`に対するsecret guard指摘を保持している。
このファイルは変更していない。

2026-10-22、Macの手動解除後に最終画面を確認した。実ブラウザーの34/34照合で、
owner画面のStage設定・実行詳細が消去され、receipt取得・ダウンロード・再実行が無効であることを確認した。
Stage更新後も保護内容は再表示されず、受付済みIDなどの取消用情報だけを保持した。
この画面にはgeneration変更・対象清掃も関与するため、即時grant失効による拒否は前述の独立API/CLI証拠と区別する。

元の2つのSQLタブは、SQL本文・DB ID・Schema ID・role・mode・LOCAL・実行不可状態が開始時と一致した。
owner用シークレットwindowだけを閉じ、通常Workbenchと元の選択タブを保持した。
画面証拠を固定した後、検証用2利用者と8 private profilesを撤去し、元の4利用者・認証内容を保持した。
撤去対象credentialの残存検査は許可例外0で成功し、同じ最終generationの通常2入口を再起動した。

認証撤去後の復帰照合は12/12成功した。doctorはhealthy、通常8888入口から
`SELECT 44 AS p44_final_acceptance`が`FINISHED`・結果`[[44]]`となった。
追加はqueryとadmissionが各1だけで、最終件数はquery 1,690、DataOps 599、admission 2,327、Stage 23、Stage run 68。
元資産・grant・binding・credential seed・ホスト固有設定を保持し、未解決実行0、過去のFENCED_READ_ONLY 2件も保持した。

その後の通常ブラウザー再読込みでも22/22照合が成功した。状態表示は準備完了、通信エラー表示なしとなり、
元2タブのSQLとcontext・実行不可状態・選択タブを再確認した。この画面操作によるSQL送信は0だった。
独立した最終監査も29/29成功し、既存のquery・context・admission行を保った各1行の追加、
canonical全51テーブルの最終状態、認証撤去、元資産・sourceの保持を照合した。
P4-4の範囲は限定技術受入とし、自動scan・dedup・予定実行、所有者の最終操作性評価、全体最終受入は後続計画へ残す。

開始時証拠：`var/shirokuma/p4-4-acceptance/runtime/baseline-readonly.json`、
`runtime/baseline-schema13.sqlite3`、`worksheet-before.json`。
通常入口の証拠：`api/normal-summary.json`、`api/public/state.json`、
`cli/window-summary.json`、`cli/read-only-headless/`。
追加証拠：`api/cleanup-generation/policy/policy-proof.json`、`ui-observed.json`、
`ui-verifier/evidence/`、`api/cleanup-generation/faults/public/state.json`、
`runtime/regrant-ready-summary.json`、`runtime/regrant-upsert-failure-diagnostic.json`、
`runtime/upsert-ready-summary.json`、`cli/state.json`の`upsert_repairs`と`grant_windows`、
`runtime/finite-cleanup/after-cli/drop-plan-root.progress.json`、
`runtime/finite-cleanup/after-cli/file-delete-root.jsonl`、
`runtime/finite-cleanup/after-cli/final-cleanup-proof.json`、
`runtime/final-generation/final-revoked-denials-v3/summary-existing-CLI-denial-contract.json`、
`runtime/final-plan-root-review.json`、
`runtime/final-ready-summary.json`、`runtime/final-gui-proof.json`、
`runtime/final-post-auth-browser-proof.json`、`runtime/final-auth-hygiene/completion.json`、
`runtime/final-auth-hygiene-independent-proof.json`、
`ui-final-owner-revoked.png`、`ui-post-auth-main.png`、
`gate-results.json`、`source-manifest-upsert-policy.json`。過去のsource固定記録も保持している。
