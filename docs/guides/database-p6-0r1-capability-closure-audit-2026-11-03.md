---
project: Shirokuma
doc_id: DB-P6-0R1
title: Capability implementation evidence and normal path closure audit
status: accepted
created: 2026-11-03
updated: 2026-11-03
version: "1.0"
area: guides
tags: [database, capability, acceptance, audit, workbench]
---

# DB-P6-0R1: 89項目の実装・証拠・通常導線の閉鎖監査

[C2D](database-p6-4c2d-foundation-controllers-2026-11-03.md)後、既存89 ID・順序を維持し、通常Workbenchの入口、実装、既存受入、role条件と残件を対応付ける監査である。単に旧routeが閉じていることをM1の対象外・完了の根拠にせず、未対応の必須行と範囲未確定をopenに保つ。89行の監査とSQL-01の限定修正を完了した。P6全体やM1本人受入は継続する。

## 判定基準

[全体計画](database-full-capability-and-compute-plan-2026-10-28.md)のM1／DB-P6-0／P6-7が基準である。既存コア操作の対象を外す場合は所有者との合意・理由・代替導線が必要で、実装側だけで除外しない。P7／P8／P9等への延期は計画の明示的な根拠を示す。

「必須範囲」と「現在の対応」を別に扱う。既存の限定技術受入はそのguideの対象と条件までとし、この監査で実engine／dbt／LLM・全role・故障回復を新たに再受入したものではない。部分対応・旧入口閉鎖・代替ありだけで、行全体のM1 gateを自動的に通さない。統合受入と所有者本人の操作性評価も別gateである。

| 表記 | 意味 |
|---|---|
| 必須 | 現行M1の義務。role／対象／実装済みの条件と残件を併記する |
| 別track | 既存計画が明示するM1後／別採用。今回の判断で必須範囲を縮小しない |
| 範囲確認 | 旧機能と代替の差異に対し、義務の満足や対象変更の合意が未確認。openとして残す |
| 既存受入 | 過去guideの限定証拠への対応付け。今回の全機能再受入ではない |
| 部分／未接続 | 実装または証拠・通常導線の残件がある。M1未完了の根拠を維持する |

## 今回見つかったSQL-01の不具合と限定修正

Database初期化後の`addWorksheet`は、引数省略時に旧fixtureの`marts.fct_orders`を本文へ入れていた。初回のDatabase Worksheetは空であり、固定sampleボタンも閉じているため、新規tabだけが別の既定値になる不具合である。

修正対象はDatabaseモードの既定SQLを空文字へ合わせる部分と、その操作回帰だけとする。明示的なSQL引数、Explorerからの許可済み対象を限定したSQL、既存tabの本文・実行状態、継承するDB／schema／role／mode／LOCAL／行上限を維持する。追加時にSQLを実行せず、旧非Databaseモードのsample既定値とSQL-09の旧入口拒否も変更しない。

## 全89行の照合

3分担のread-only調査を統合し、89 IDの重複なし・順序一致を確認した。各sectionの分類記号はそのsection内の定義に従う。曖昧な代替や未確認の受入はopenとして記録する。

監査上の整理は、M1義務を維持する82行、既存計画で明示的に別trackの3行（ING-11／JOB-11／CMP-06）、範囲判断をopenに保つ4行（SQL-09／ADM-09〜11）である。範囲判断4行を対象外と認定したものではない。82行にも下記の残件と全行共通のP6-7／本人受入が残り、実装済み・既存限定証拠ありの数をM1完了数へ変換しない。

優先して閉じるべき通常UIの残件は、①ING-03／TRF-06のreload後の元operation再接続、②AI-02／SQL-12／13の既存WAPへの案内と広すぎる未対応文、③AUD-02の旧履歴から各通常履歴への全操作対応、④AI-04／05／CMP-05の旧SRE・global Policyの必要範囲の確定である。controller記録・Pawprintsの限定previewを全health／全失敗履歴と同一視しない。EXP等のguideに残る実故障誘発・全画面幅・縦断操作性の検証限界も各行のまま保持する。

CMP-04の「AI未接続」はA2／A3の計画取得の通常admission／必要時Wakeという既存受入に対するdated copyであり、新しい全AI自動復帰機能を実装した証拠にはしない。



### GLOBAL／SQL／探索

基準HEADは`bc16d8fd`。29 ID・順序は現行matrixと一致しました。今回はsource／既存受入記録の照合であり、新しい実API・ブラウザー受入ではありません。

判定は **R＝M1必須操作、L＝旧controlを意図的に閉じ既存導線へ移管、O＝閉鎖条件に所有者判断が必要**。内訳は **R 26、L 2、O 1、行全体をdeferredとするもの0**です。R26＋L2は計28件のM1 core obligationであり、Lも必須機能自体を除外する判定ではありません。SQL-09はscope OPENで、waiverではありません。copy／wayfindingの残件は実行機能の対応とは別にOPENとします。全行にP6-7統合受入・M1本人受入が別に残ります。

| ID | 判定・根拠 | 通常Workbench入口 | 実装・既存受入証拠 | 正確な残件・検証限界 |
|---|---|---|---|---|
| GLOBAL-01 | R：言語設定 | `lang-ja`／`lang-en`→`setLanguage` | W:1165–1196、[GS-d1]の日英実画面、[GS-p6-0] | 各新panelの日英検証は単位別。全画面の最終操作性評価は未完 |
| GLOBAL-02 | R：account内の利用role上限 | `role-selector`→`dbApplyRole`、Worksheetの`query-execution-role` | W:5493–5506、5951、6043、[GS-d1] | ヘッダー上限と保存実行roleを混同しない。降格後の保存role書換えは不要 |
| SQL-01 | R：Worksheet追加・選択・閉じる | `add-worksheet-tab`、`worksheet-tabs`、各close | W:1640–1655、5770、6027、[GS-d1] | **今回修正：Database追加の既定値を空下書きへ変更し、重点試験と通常画面の追加・reload・復元を確認。** 自動採番は現行範囲、rename追加は不要 |
| SQL-02 | R：明示実行context | `query-database`／schema／execution-role／mode／warehouse／max-rows、context更新・再選択 | W:5256、5291、5981–5986、6045–6047、QC、[GS-p2-3]・[GS-d1] | LOCALのみ。XS／S実サイズはP7。別panelでWorksheet保存contextを変えない |
| SQL-03 | R：本文・選択SQL実行 | `run-query`／Cmd・Ctrl+Enter→`dbRunQuery` | W:5456–5479、QS:`submit`621、QC、[GS-p2-3]・[GS-p6-2] | VのMARTS、E/Aの許可mode・Schema境界を維持。無制限SQL言語の対応ではない |
| SQL-04 | R：page・poll・状態 | `dbPoll`／`dbAcceptPage`、結果status | W:5325–5347、QS:`fetch`948、[GS-p2-3]・[GS-p6-2]・[GS-6a] | partial／truncated／UNKNOWNを保持。process pin消失・ID未受領を含む全通常UI故障回復はP6-7で再照合 |
| SQL-05 | R：本人取消 | `cancel-query`→`dbCancelQuery` | W:5481–5491、QS:`cancel`1023、[GS-p2-3]・[GS-p6-2] | 失効後cleanupと結果閲覧を分離。取消の失敗を停止成功にしない |
| SQL-06 | R：最近の履歴・詳細・復元 | `query-history`→`dbLoadHistory`／`dbRestoreHistory`、Request details | W:5508–5558、QS:`inspect_query`1052／`history`1251、[GS-d1]・[GS-p3-5] | 入力復元のみ。結果再取得・SQL再実行や歴史的状態の現在化ではない |
| SQL-07 | R：返却行の表示操作 | `result-view-table/chart`、`result-filter`、列header、chart tools | W:1664–1668、2042–2070、5295–5311、[GS-p2-3] | 表示対象は返却済み行。ブラウザーJS数値の既存精度境界があり、SQL-08の正確なserver保存と区別 |
| SQL-08 | R：結果ファイル保存 | `export-csv/json/tsv`→`dbExportResults` | W:5362–5430、RE:`export`96、[GS-6a] | 受領済み連続pageのみ、4096 page／16 MiB。cache喪失で再実行しない。clipboard・Table全件Exportとは別 |
| SQL-09 | **O：固定sample menuの閉鎖条件未確定** | 旧`data-sample`は無効。代替はExplorerの`open-table-sql` | W:1197–1200、6021、5849–5859、[GS-d1]:65 | EXP-07は選択対象の完全修飾SELECTを新規・未実行Worksheetへ生成する実在代替。ただし「Recent orders／Revenue／Customers」の名前付き例menuとの完全互換ではない。旧fixture再開も一方的除外もしない |
| SQL-10 | R：SQL案生成 | `copilot-generate`、`proposal-*`→`dbProposalSubmit` | W:3383、3461、AI-SQL:`submit`240、[GS-b1]・[GS-b2] | 未実行案、許可metadata、LOCALのみ。B2は同DB・同一E/A・使用TABLE等の条件付き |
| SQL-11 | R：SQL・計画説明 | `copilot-explain`、`explanation-copilot-plan`／saved-plan等 | W:5017–5077、`db_ai.py`／`db_ai_plan.py`／`db_ai_reexplain.py`、[GS-a1]・[GS-a2]・[GS-a3]・[GS-a5] | LOCAL説明、条件付きINVOKER VIEW等の範囲。計画取得は通常admission／Wake、保存計画再説明は新規EXPLAINなし |
| SQL-12 | L：旧Stage／Audit control閉鎖、必須操作は既存WAPへ移管 | 成功E/A案の`proposal-wap`→`Edit in Manual WAP`→`p35-wap-save/build-action/audit-action` | W:3455、6886–6906、AI-WAP:`prepare`184、MW:`save_model/build/audit`、[GS-b2]・[GS-p3-5] | 引渡しだけで保存・SQL・build・auditを行わない。旧disabled tooltipの「staging…unavailable」はManual WAPへの案内が弱い |
| SQL-13 | L：旧Copilot Publish閉鎖、必須公開は明示WAPへ移管 | `p35-wap-publish`／対象audit選択 | W:6903–6906、MW:`publish`444、[GS-p3-5]・[GS-b2] | Aの現在contextとbuild／audit条件を維持。AIからの自動公開は不要。旧buttonから既存公開へのwayfinding改善候補 |
| EXP-01 | R：許可対象のtree・検索・page・更新 | `catalog-filter`、共通tree、`refresh-catalog`、More | W:5619–5734、I:`list_databases/list_schemas/list_objects`、[GS-p2-3]・[GS-d1] | 選択Schemaの許可object名検索。全catalogの内容横断検索や行数censusではない |
| EXP-02 | R：概要・identity・columns | schema breadcrumb、Table Inspector→Columns | W:`dbDescribe`／`dbShowMetadataOverview`、I:`object_detail`439、[GS-p2-3]・[GS-d1] | 同名再作成は別Ref。UUID等のidentity不足時は詳細を開放しない |
| EXP-03 | R：最新preview | `inspect-tab-preview`→`dbDescribe`／`dbDetailPages` | W:5927–5949、I:484–489、[GS-p2-3] | `LIMIT 50`のpreview。全件・Snapshot固定Exportとは別 |
| EXP-04 | R：Snapshot一覧 | `inspect-tab-snapshots`／`table-snapshots` | I:471–473、W:`dbDescribe`、[GS-p2-3] | 安定TABLEのみ。VIEWはSnapshot機能対象外。IDは文字列 |
| EXP-05 | R：選択Snapshot preview | Snapshot行のpreview→`snapshot-preview` | I:439–441、475–489、W:`dbDetailPages`、[GS-p2-3] | 整数Snapshotを指定し、消失時にlatestへ代替しない。既存対応済みで再実装不要 |
| EXP-06 | R：DDL表示 | `inspect-tab-ddl`→`dbDescribe` | I:448、487–489、QS、[GS-p2-3] | **Schema write capability必須**。browseだけで利用できる機能とは記載しない |
| EXP-07 | R：新規SQL・名前挿入・Schema適用 | `open-table-sql`／`insert-table-sql`／`use-explorer-schema` | W:5780、5787、5849–5859、[GS-d1]:65 | 新規Worksheetと既存Worksheet編集を分離。新規SQLは完全修飾・未実行で、元draft／runを保持 |
| EXP-08 | R：Snapshot固定全件Export | `export-table-format`／`export-table-btn` | W:`dataopsStartExport`6954、ME:`export_table`199、[GS-p3-4]・[GS-p3-5] | CSV／JSONL・対応型のTable全行。complex等の非対応型、VIEW、Parquet、streamingは既存境界。Worksheet結果保存とは別 |
| EXP-09 | R：Export照会・取消・receipt・download | `p35-export-*`、`dataopsTrackExport`／receipt／download | W:6931–6997、ME:`get_export/read_export`、[GS-p3-4]・[GS-p3-5]・[GS-p4-9] | 完全artifactだけ公開。ID未受領の元key回復は同body POSTを含み得るため、全てpure GETと説明しない |
| EXP-10 | R：Snapshot物理コピー | `clone-table-btn`→`db-clone-*` | W:7454–7475、MC:`clone_table`41、[GS-6b] | 同DB・新SANDBOX・E/A、単一CTAS。zero-copy／履歴等の完全複製・overwriteは範囲外。guideの英語page410pxはpanel外の過去の狭幅検証残件 |
| EXP-11 | R：TABLE登録解除・data保持 | `drop-table-btn`→`db-drop-*`、対象消失後の元key GET | W:7689–7708、MD:`preview/submit/inspect`、[GS-6e] | Aの実効DB管理権限。ACK／UNKNOWN・元Ref CASを分離。purge／VIEW／cascade／Undropなし。guideの英語page412pxは全画面合格としない |
| EXP-12 | R：全行・全列NULL統計 | `inspect-tab-profile`、`table-profile-run/observe/cancel` | W:7155–7173、TP:`profile`184／`inspect`78、[GS-6c] | TABLE・V/E/A、件数文字列／server率。min/max／distinct等は追加不要。実Snapshot失効・実行中grant失効・COUNT強制中断は実機誘発せず、code証拠との区別が残る |
| EXP-13 | R：Tableの保存lineage | `inspect-tab-lineage`／`explorer-open-lineage`／`db-lineage-refresh` | W:7021–7090、OL:`get_lineage`200、[GS-6d] | **E/A限定**。保存WAP/dbtの直接関係・現在endpoint認可。clone／任意SQL推定・全catalog網羅ではない |
| EXP-14 | R：dbt定義・保存実行lineage | 変換の保存plan、Explorerの同じLineage面 | W:`dbDbtSelectPlan`／`dbDbtGraphProjection`8651＋上記Lineage、`db_dbt.get_plan`2101、[GS-3b]・[GS-3c2a]・[GS-6d] | E本人・保存role／A同DB。宣言は版を固定しないRef、確認済み関係はSnapshot／VIEW版固定。C1のV/E/A条件を流用しない |

優先度が明確な具体的修正はSQL-01の空下書き既定値です。その後はSQL-12／13の既存WAPへの案内を検討できます。SQL-09は、そのメニュー機能まで必要か、EXP-07の安全なSELECT生成を対応結果として扱えるかの判断を残します。

実装参照（リンクは組立先`docs/guides`からの相対位置）：

- W＝[Workbench](../../tools/local_experiment/workbench/index.html)
- QS＝[query_service.py](../../tools/local_experiment/query_service.py)、QC＝[query_context.py](../../tools/local_experiment/query_context.py)、I＝[database_inventory.py](../../tools/local_experiment/database_inventory.py)
- RE＝[query_result_exports.py](../../tools/local_experiment/query_result_exports.py)、AI-SQL＝[db_ai_sql_proposal.py](../../tools/local_experiment/db_ai_sql_proposal.py)、AI-WAP＝[db_ai_wap.py](../../tools/local_experiment/db_ai_wap.py)
- MW＝[manual_wap.py](../../tools/local_experiment/manual_wap.py)、ME＝[manual_export.py](../../tools/local_experiment/manual_export.py)、MC＝[manual_clone.py](../../tools/local_experiment/manual_clone.py)
- MD＝[manual_table_drop.py](../../tools/local_experiment/manual_table_drop.py)、TP＝[table_profile.py](../../tools/local_experiment/table_profile.py)、OL＝[object_lineage.py](../../tools/local_experiment/object_lineage.py)
- AI説明＝[db_ai.py](../../tools/local_experiment/db_ai.py)、[db_ai_plan.py](../../tools/local_experiment/db_ai_plan.py)、[db_ai_reexplain.py](../../tools/local_experiment/db_ai_reexplain.py)。dbt plan＝[db_dbt.py](../../tools/local_experiment/db_dbt.py)。

[GS-d1]: database-ux-d1-explorer-sql-report-2026-10-20.md
[GS-p2-3]: database-p2-3-exploration-sql-report-2026-10-19.md
[GS-p3-4]: database-p3-4-snapshot-export-report-2026-10-20.md
[GS-p3-5]: database-p3-5-normal-entry-acceptance-report-2026-10-20.md
[GS-p4-9]: database-p4-9-acceptance-2026-10-23.md
[GS-p6-0]: database-p6-0-implementation-2026-10-29.md
[GS-p6-2]: database-p6-2-implementation-2026-10-29.md
[GS-6a]: database-p6-6a-result-save-contract-2026-11-02.md
[GS-6b]: database-p6-6b-table-clone-2026-11-02.md
[GS-6c]: database-p6-6c-table-profiling-2026-11-02.md
[GS-6d]: database-p6-6d-table-lineage-2026-11-02.md
[GS-6e]: database-p6-6e-table-drop-2026-11-02.md
[GS-b1]: database-p6-4b1-sql-proposals-2026-11-01.md
[GS-b2]: database-p6-4b2-wap-handoff-2026-11-01.md
[GS-a1]: database-p6-4a1-local-sql-explanation-2026-10-31.md
[GS-a2]: database-p6-4a2-plan-explanation-2026-10-31.md
[GS-a3]: database-p6-4a3-view-plan-explanation-2026-10-31.md
[GS-a5]: database-p6-4a5-saved-plan-explanation-2026-10-31.md
[GS-3b]: database-p6-3b-implementation-2026-10-29.md
[GS-3c2a]: database-p6-3c2-sandbox-views-2026-10-30.md

### 取り込み／変換／Job：34行の静的閉鎖監査

対象HEAD `bc16d8fd`。現行source・通常導線と既存の限定技術受入を対応付けたもので、今回34行を実機再受入した結果ではない。監査のためのHTTP／engine SQL／LLM／Kubernetes／試験／状態変更は実施していない。

**分類: M1必須core義務32行、明記済みP9後続2行、独自のscope免除0行。** ING-04／TRF-12もcore32行に含め、通常置換で満たす義務を残す。旧route閉鎖2行は実装属性であり、必須行を免除する根拠ではない。別拡張を新規必須へ昇格させていない。

**G（全core行に共通）**: 過去guideはそれぞれの日付・限定範囲の技術受入であり、現行logical_v1の通常Workbenchによる作成→投入→変換→Job／予定→監査→休止・復帰のDB-P6-7とM1本人受入はOPEN。Gは個別backend欠落が判明した意味ではない。

**R1（ING-03／TRF-06のOPEN候補）**: `dataopsPanel()` は `p.op=null` で初期化し、`dataopsObserve()` はp.opを必要とする。Manual Ingest／WAPにはoperation ID再入力欄／operation履歴selectorが見当たらず、対象コードにlocalStorage/sessionStorageへのhandle保存・復元もない。WAP Metadata ID欄はbuild/audit/publicationの読取りで、元実行operation再接続とは別。backend GET `/api/v1/dataops/{id}` と通常CLI operation get/cancelは存在する。したがって「backendに復旧がない」ではなく、**同一ページの元key/body照会の既存受入を、reload後の通常UI復旧へ代用できない**というgap。P6-6のreload受入／P6-7へ対応付け、必要なら既知ID・元context/keyを保持して再接続する小unitに限定する。UNKNOWNの自動再送・新key・新SQLは提案しない。

| ID | 分類・basis | 実際の通常UI入口＋実装 | 既存の受入／試験 | 精密なgap・境界 |
|---|---|---|---|---|
| ING-01 | M1必須・I契約 | 取り込み→ファイル `p35-ingest`、browser file/drop・型/format/delimiter→Preview。`manual_ingest`＋`dataops_routes` | I／TI | G。CSV/JSON/JSONL。previewは入力sampleで、target互換性の最終保証ではない |
| ING-02 | M1必須・I契約 | create/replace/append/upsert選択→Confirm preview and ingest、`POST /ingests` | I／TI | G。変更で再preview、bytes/設定/key固定。atomic appendとupsert key条件を維持 |
| ING-03 | M1必須・元operation | `p35-ingest-observe/cancel`、元key/context/ID receipt、`dataopsObserve`→DataOps GET/cancel | I／TI（pending・応答喪失・失効） | **R1＋G**。reload後の元operationへ通常UIで再接続する入口は見当たらない。同一ページの復旧は既存受入あり |
| ING-04 | M1必須・通常置換／旧route閉鎖 | 旧server-path/固定catalog formを隠しING-01〜03へ接続。host folderはING-05〜06の登録local Stage | N／I／S4、TI／TS | G＋置換の本人確認。browser bytes/current DBと任意server-path/固定iceberg/旧Parquetの完全互換は主張しない。閉鎖だけで免除しない |
| ING-05 | M1必須・S契約 | 取り込み→Stages `db-stages-panel`、New/Load/current revision/Save。`db_stages.save_stage/list_stages` | S4／TS | G。A・owner・同DB・宣言済approved local roots、directory immutable/CAS。任意pathではない |
| ING-06 | M1必須・S＋I | Stages→Refresh files/Preview file/Confirm import。設定版/file版/bytes固定 | S4＋I／TS＋TI | G。単件create/replaceとscanのatomic append/upsertを分ける |
| ING-07 | M1必須・S scan | Manual scan/Observe scan/Refresh scans、`db_stage_scans`、内容dedup/履歴 | S5／TS | G。atomic append又はkeyed upsert。UNKNOWN/部分commitを未実行扱いしない |
| ING-08 | M1必須・S export | Export snapshot→receipt/Download authorized file。`db_stages.export_table/read_export`＋`stage_exports` | S4／TS | G。固定Snapshot、CSV/JSONL、完成artifactのみ。Worksheet結果保存とは別 |
| ING-09 | M1必須・S operation | Observe run/Cancel run/Stage history、scan履歴・receipt一覧。`db_stages.list_runs/cancel_run` | S4／S5、TS | G。元owner/context・履歴再選択・UNKNOWNの現在統合確認が必要 |
| ING-10 | M1必須・S＋T | Stage内Database schedule→Reload/New/Load/Save disabled/Enable/Stop/Inspect ticks、既存scheduler | T＋S5／TT＋TS | G。UIのtickは履歴照会、発火は既存tick。atomic append/upsert・保存target版・既定無効 |
| ING-11 | P9後続・P9-1明記 | 現UI/APIはlocal Stage。外部Stage/connector同期の入口は未提供 | 全体計画P9-1／matrix ING-11 | M1 gapへ繰り上げない。provider/認証/更新/運用契約は採用時に定める。local Stageで対応済みとはしない |
| TRF-01 | M1必須・W契約 | データ変換先頭Manual WAP `p35-wap`、model版Load/Save、source alias/target。`manual_wap.save_model` | W＋I／TW | G。DB共有metadataとcaller-bound operationを区別。B2空draft引渡しは保存/buildではない |
| TRF-02 | M1必須・W build | Build saved version→`/wap/build`、`manual_wap.build` | W＋I／TW | G。immutable保存版の候補構築。未保存formや公開先の変更とは別 |
| TRF-03 | M1必須・W audit/詳細 | Audit build、Build/Audit/Publication選択・Metadata ID Load。`manual_wap.audit`／各詳細GET | W＋I／TW | G。同DB E→A metadata引渡しは実装済み。実行operationのowner変更ではない |
| TRF-04 | M1必須・W publish | Publish audited build (admin)→`manual_wap.publish` | W＋I／TW | G。Aの明示公開、監査済み候補/Snapshot。AI専用高権限ではない |
| TRF-05 | M1必須・W rollback | Rollback publication (admin)→保存publication ID→`manual_wap.rollback` | W＋I／TW | G。publicationのprevious Snapshot限定。旧Bear任意Table/Snapshot rollbackとは別 |
| TRF-06 | M1必須・W operation | Observe/Cancel operation、receipt metadata ID、Metadata ID Load→DataOps/metadata GET | W＋I／TW | **R1＋G**。model/build再取得とreload後の元operation再接続を分ける |
| TRF-07 | M1必須・M契約 | 変換→Managed Marts `db-marts-panel`、New/Load/Save/Load model version、`db_marts.save_mart` | M／TM | G。A・本人Mart・明示model版・MARTS purpose/Ref。旧global定義の黙示採用なし |
| TRF-08 | M1必須・M check/refresh | Check changes/Full refresh＋Check only when changed、`db_marts` checks/refreshes | M／TM | G。manual-onlyは無条件更新可、check/条件付きはwatch必須。incrementalではない |
| TRF-09 | M1必須・M run | Observe run/Refresh history、保存Mart runとDataOps receipt | M／TM | G。元owner/context・source固定、commitと後続記帳失敗、UNKNOWN保持 |
| TRF-10 | M1必須・M＋T | Mart内Database schedule→Load/New/Save disabled/Enable/Stop/Inspect ticks | M＋T／TM＋TT | G。watchあり・保存target/model版・変更確認付きrefresh、既存scheduler |
| TRF-11 | M1必須・限定dbt契約 | 変換→Models→Database dbt `db-dbt-panel`、approved project/Compile/Model SQL・lineage/保存plan Build-test/履歴復旧。`db_dbt`＋`dbt_routes` | D／TD | G＋P6-3全体閉鎖。SQL/WAP、単一native TABLE、対応VIEW/TABLE/EPHEMERAL graphとTABLE公開まで。正式VIEW公開/VIEW連鎖/incremental/並列/候補直接置換/manual-WAP rollback統合は別拡張 |
| TRF-12 | M1必須・通常置換／旧widget閉鎖 | 旧Dynamic WAP list/refreshを隠しManual WAP版管理＋Managed Mart登録/全件更新へ対応 | N／W／M、TW＋TM | G＋置換の本人確認。旧global定義/ID/refresh recipeの自動移行・互換は未提供。旧widget閉鎖だけで免除しない |
| JOB-01 | M1必須・J定義 | Jobs→Database Jobs `db-jobs-panel`、New/Saved Jobs/Load current revision/Save、`db_jobs.save_job/list_jobs` | J／TJ | G。owner/保存role/独立context/CAS。typed JSON編集の本人操作性は未評価 |
| JOB-02 | M1必須・J SQL/alert | Steps JSONのSQL/read-only Boolean alert。保存時・実行時SQL policy | J／TJ | G。同DB guard維持。任意command/他DBは含めない |
| JOB-03 | M1必須・J管理step | Steps JSONのIngest/WAP refresh/Table export、Aのtyped adapter | J／TJ | G。型付き入力/model版/Ref/保存role。dbt任意command/nativeSPではない |
| JOB-04 | M1必須・J run | Run Job、typed Arguments JSON、Add named input→受付版/引数/bytes固定 | J／TJ | G。file inputは手動run。予定upload Jobへの対応ではない |
| JOB-05 | M1必須・J照会/履歴 | Observe run/Job history/step詳細、条件付き自動poll。`dbJobsSyncPolling`／`db_jobs.list_runs` | J／TJ | G。元run/contextの観測。結果行/private upload bytesは履歴に出さない |
| JOB-06 | M1必須・J cancel | Cancel run→`/jobs/runs/{id}/cancel`、`db_jobs.cancel_run` | J＋復旧runbook／TJ | G。cleanup取消と閲覧権限を分離。失効後取消/UNKNOWN子の現行統合再確認 |
| JOB-07 | M1必須・J reconcile | Reconcile unknown run→`db_jobs.reconcile_run`、backend証拠に基づく子照合 | J＋復旧runbook／TJ | G。結果UNKNOWNと実行閉鎖、元pin喪失時operator境界を分ける |
| JOB-08 | M1必須・J＋T | Job内Database schedule→Reload/New/Load/Save disabled/Enable/Stop/Inspect ticks | T／TT＋TJ | G。owner・保存Job版・引数固定。upload Jobは不可、Stage scanへ案内 |
| JOB-09 | M1必須・T calendar | schedule formのinterval/daily/once/weekly/monthly/timezone | T／TT、runtime calendar | G。DST/月末/時計変化/MISSED/競合skipの既存契約。追いかけ実行を追加しない |
| JOB-10 | M1必須・T alert | Job内Unacknowledged alerts→Refresh/Acknowledge。`db_schedules.list_alerts/acknowledge_alert` | T／TT | G。ackは確認済みで原因解消ではない。owner/DB/保存roleと失効時消去 |
| JOB-11 | P9後続・P9-2明記 | 現UIはtyped Job/schedule。nativeSP/CALL/汎用DAG/任意commandは開放しない | 全体計画P9-2／matrix JOB-11 | M1不足へ繰り上げない。Routine意味・型付き引数・版管理は採用時に定め、JobをnativeSPと称しない |

#### 根拠・試験の凡例（過去の限定技術受入）

- 規範: [全体計画](database-full-capability-and-compute-plan-2026-10-28.md) §1、P6-6、P9-1/2と[機能表](database-capability-matrix-2026-10-29.md)。**N**=[P6-0](database-p6-0-implementation-2026-10-29.md)。Nは機能表/案内の受入で、閉じたmutationの実機受入ではない。
- **I**=[P3-2 ingest](database-p3-2-manual-ingest-report-2026-10-20.md)＋[P3-5通常入口](database-p3-5-normal-entry-acceptance-report-2026-10-20.md)。**W**=[P3-3 WAP](database-p3-3-manual-wap-report-2026-10-20.md)＋同P3-5。通常Workbench/HTTP/CLI、3DB、入力/権限/故障の2026-10-20限定証拠。
- **S4**=[P4-4 local Stage](database-p4-4-local-stage-report-2026-10-21.md)、**S5**=[P4-5 scan/dedup](database-p4-5-manual-stage-scan-dedup-report-2026-10-22.md)、**M**=[P4-6 Managed Mart](database-p4-6-managed-marts-report-2026-10-22.md)。各scopeの通常画面/故障/失効を含む既存証拠。
- **J**=[P4-3 Jobs](database-p4-3-jobs-manual-adapter-report-2026-10-21.md)＋[read-only Job復旧](database-read-only-job-execution-recovery-2026-10-21.md)。**T**=[P4-7 schedules/alerts](database-p4-7-schedules-alerts-report-2026-10-23.md)（自然発火、target版、DB境界、競合、calendar、ack）。
- **D**=[P6-3B SQL/WAP](database-p6-3b-implementation-2026-10-29.md)、[C1設定互換](database-p6-3c-dbt-compatibility-2026-10-29.md)、[C2A VIEW](database-p6-3c2-sandbox-views-2026-10-30.md)、[native UI](database-p6-3c2b3-native-ui-2026-10-30.md)、[native品質](database-p6-3c2b4-native-quality-tests-2026-10-30.md)、[native公開](database-p6-3c2b5-native-publication-2026-10-31.md)、[graph](database-p6-3c2c-native-graph-2026-10-31.md)、[graph公開](database-p6-3c2d-graph-publication-2026-10-31.md)、[native VIEW graph](database-p6-3c2e1-native-views-2026-10-31.md)、[VIEW証拠付きTABLE公開](database-p6-3c2e2-view-graph-publication-2026-10-31.md)、[監査復旧](database-p6-3r1-dbt-audit-recovery-2026-10-31.md)。後続guideが拡張した部分だけを合成する。
- **TI**=[manual ingest](../../tests/test_manual_ingest.py)、[DataOps routes](../../tests/test_dataops_routes.py)、[DataOps UI](../../tests/test_workbench_dataops_ui.py)。**TW**=[manual WAP](../../tests/test_manual_wap.py)＋同route/UI。
- **TS**=[Stages](../../tests/test_db_stages.py)、[scan](../../tests/test_db_stage_scans.py)、[routes](../../tests/test_stage_routes.py)、[Stage UI](../../tests/test_workbench_stages_database_ui.py)。**TM**=[Marts](../../tests/test_db_marts.py)、[Mart UI](../../tests/test_workbench_marts_database_ui.py)。
- **TJ**=[Jobs](../../tests/test_db_jobs.py)、[routes](../../tests/test_job_routes.py)、[Job UI](../../tests/test_workbench_jobs_database_ui.py)。**TT**=[schedule](../../tests/test_db_schedules.py)、[routes](../../tests/test_db_schedule_routes.py)、[schedule UI](../../tests/test_workbench_schedules_database_ui.py)、[runtime calendar](../../tests/test_runtime_calendar_schedules.py)。
- **TD**=[dbt基本](../../tests/test_database_dbt.py)、[native](../../tests/test_database_dbt_native.py)、[graph](../../tests/test_database_dbt_graph.py)、[VIEW graph](../../tests/test_database_dbt_graph_views.py)、[graph公開](../../tests/test_database_dbt_graph_publication.py)、[VIEW公開証拠](../../tests/test_database_dbt_graph_publication_views.py)、[routes](../../tests/test_dbt_routes.py)、[dbt UI](../../tests/test_workbench_dbt_database_ui.py)。
- 現行source: [Workbench](../../tools/local_experiment/workbench/index.html) の `dataopsInstallIngest/InstallWap`、`dbJobsInstall`、`dbStagesInstall`、`dbMartsInstall`、`dbDbtInstall`、`dbScheduleInstall`。[DataOps routes](../../tools/local_experiment/dataops_routes.py)、[Jobs](../../tools/local_experiment/db_jobs.py)、[Stages](../../tools/local_experiment/db_stages.py)、[Marts](../../tools/local_experiment/db_marts.py)、[schedules](../../tools/local_experiment/db_schedules.py)。通常DataOps/Jobs CLIの既定APIは8888で、本監査は全routeのnative8080対応を主張しない。

#### 次の限定作業

この34行で個別backend欠落を裏付ける具体的な未実装は確認されていない。一方R1の通常UI復旧はOPENである。全要件完成の判定ではない。SQL-01（Databaseの無引数＋Worksheetが旧sampleを入れる不具合）は今回修正済みであり、次はR1の元operation再接続だけを限定した単位で扱う。ING-04/TRF-12の意味差はM1本人受入で置換が満たす義務を確認し、曖昧なままscope免除しない。

### AI・監査・Compute・管理の26行

2026-11-03、HEAD `bc16d8fd`でsourceと公開guideだけを照合した。HTTP、Kubernetes、SDK、doctor、実engine、運用操作は呼んでいない。通常サーバー85616/85617のargv・checkout・stable TMPDIRはOSの読取りで確認した。既存owner 2 YAMLの変更を保持した。

分類は **T 17行 / O 5行 / C 3行 / D 1行**。Tは既定の限定範囲に技術受入証拠がある必須行、Oは必須行の閉鎖がOPEN、Cは条件付き・破壊的範囲についてowner判断がOPEN、Dは明示的な後続trackである。**TをM1閉鎖と数えず、Cを必須免除と数えない。** 新たな除外承認は0行。

[PA-全機能計画](database-full-capability-and-compute-plan-2026-10-28.md)の「到達点」「DB-P6-0」「DB-P6-7」により、旧入口が未接続・条件付きという実装側の記述だけではM1対象を除外できない。全必須行の対応、通常導線、統合受入、owner本人の操作性評価を別に残す。

| ID | 分類・判断根拠 | 通常UI入口・source | 技術受入証拠と正確な残件 |
| --- | --- | --- | --- |
| AI-01 | T・許可SQL／plan／保存planの説明 | Bears Query Doctor／SQL Copilot。[PA-ui]の`dbExplanationInstall`、`explanation-copy/latest`、[PA-ai routes]の説明・plan・latest入口 | [PA-a1]〜[PA-a5]。TABLE／対応INVOKER VIEW、本人と保存full9等の限定を保持。縦断・owner本人受入が残る。再説明からの連鎖やSQL再実行へ広げない |
| AI-02 | O・既存actionを既存認可／WAPへ対応させる必須操作 | SQL Copilot→SQL案→Manual WAP draft。[PA-ui]の`dataopsAiLoad`、[PA-ai routes]の`sql-proposals/{id}/wap-draft`、`db_ai_sql_proposal.py`／`db_ai_wap.py` | [PA-b1]／[PA-b2]と[PA-c2c2]の既存action表。stage／audit→WAP、publish→TRF-04、refresh→Marts、rollback→TRF-05は存在。一方、旧Engineerフォーム無効のまま、[PA-ui]の`dbExplanationFence`とCopilot初期案内が広くstaging／publishing未対応と表示する。通常導線・案内の整合が実コード残件。旧任意Snapshot rollbackの同等性は未承認 |
| AI-03 | T・metadata説明／未適用提案の限定範囲 | Bears Catalogの独立context、Explorer検索／保存lineage。[PA-ui]のCatalog install、[PA-ai routes]の`catalog-proposals`、`db_ai_catalog.py` | [PA-c1]と[PA-c2c2]。V／E／Aの単一TABLE／VIEW metadata推定と、E／Aの保存lineageを分離。raw catalog設定、旧横断検索、任意SQL推定、説明保存まで完了とはしない。既存actionの最終対応とowner確認は別 |
| AI-04 | O・SRE既存inspectの範囲が未閉鎖 | SREのDB／Platform、Compute。[PA-ai routes]の`context-observations`／`platform-observations`／`platform-foundation-controllers` | [PA-c2a]／[PA-c2b]／[PA-c2c1]／[PA-c2c2]／[PA-c2d]の限定受入。C2Dはstorage／catalogのcontroller記録であり現在readiness／SQL healthではない。recent error／log／telemetry等のinspect対応と必要な観測範囲を限定して閉じる必要がある |
| AI-05 | O・全体Policyは未閉鎖 | Policy DB／Platform、Computeの宣言snapshot。[PA-ai routes]のworkload／foundation baseline | [PA-c2a]／[PA-c2b]／[PA-c2c1]／[PA-c2c2]。固定2＋管理7 templateの4宣言条件はnamespace-wide、runtime／ephemeral、admission、global policyの全体受入ではない。任意manifest旧checkは閉じたまま、必要な残り範囲をowner合意で限定する |
| AI-06 | T・設定管理とDB閲覧を分離 | header AI settings、`ai-settings-form/test/save`、`dbAISettings*`。[PA-ai routes]の`ai-config`／`ai-test`、`db_ai_settings.py` | [PA-ai設定]。真P／maximum admin／selected global admin、鍵保護、明示ping／saveを受入。外部provider実推論とM1本人評価は別 |
| AUD-01 | T・P専用の補助preview | PawprintsとDatabase Auditの別scope。[PA-ui]の`loadPawprints`／`dbAuditTelemetry`、[PA-audit routes]の`telemetry` | [PA-p6-1受入]。**現行はlatest100の取得済み8項目に対するlocal filter**。旧latest20失敗とは別。実sourceの`TELEMETRY_UNAVAILABLE`を正しく表示する限定受入で、非空／故障はcode evidence。全履歴／DB耐久監査とは表示しない |
| AUD-02 | O・履歴操作は必須、旧screen閉鎖だけで免除しない | 旧Runtime queriesはDatabase未接続（[PA-ui]の`runtime-queries-results`／`loadRuntimeHistory`）。SQL個別query、Jobs、WAP、Database Auditに代替機能が存在 | [PA-p6-0受入]／[PA-p6-1受入]と各SQL／Job／WAP guide。query／load／actionをどの現行履歴で満たすかの全件対応と通常到達性を閉じる必要がある。旧cross-DB APIの再開放は不要 |
| AUD-03 | T・保存metadataの監査 | Database Audit検索／detail／JSON。[PA-ui]の`dbAuditSearch/Detail/Download/Install`、[PA-audit routes]、`audit_service.py`／`audit_ai.py` | [PA-p6-1受入]＋[PA-d1]。page／関連record／current rights再検査を受入。本文や全遷移を含まない。P6-1で実環境未再現のgrant剥奪／retire／非P DB admin等はcode evidenceと区別し、統合・故障受入へ残す |
| CMP-01 | T・共有stateの観測 | header／SQL／Compute。[PA-ui]の`dbRuntimeRefresh/Install`、[PA-runtime routes]の`status` | [PA-p6-2受入]＋[PA-p5g]。physical状態・観測時刻・保存engine証跡を別に表示。native8080の既存runtime404はP5Gに記録済みで、全HTTP面対応とは書かない |
| CMP-02 | T・活動／保存履歴／観測済み条件 | Compute Runtime＋DB activity＋P history。[PA-ui]の`dbRuntimeRender`、[PA-ai routes]の`platform-runtime-history` | [PA-p5c]／[PA-p5f]／[PA-p5g]。本人／許可DBの保存query／jobと共有保存操作を分離。現在liveness／停止原因を過去履歴から推定しない。画面だけでの全体理解と本人受入は別 |
| CMP-03 | T・明示Wake／Chillと再検査 | quick／SQL／Compute。[PA-ui]の`dbRuntimeAct/Receipt/Observe`、[PA-runtime routes]の`accept/dispatch` | [PA-p6-2受入]／[PA-logical復旧]／[PA-p5g]。P限定、全DB影響、元key／receipt、blocker再検査、SQL再送禁止を維持。故障・縦断・本人受入は別 |
| CMP-04 | T・Auto-Chill／共通admission自動復帰 | Compute policyと各実行path。[PA-runtime routes]の`policy/_set_policy/monitor_chill`、`db_ai_plan.py` | [PA-p6-2受入]と[PA-a2]。A2は通常admission／LOCAL Wakeを通り、実coldで0／0→2／2、plan取得・model説明が成功した。**機能表の「AI未接続」はdated copy不整合であり、新規AutoResume実装不足ではない。** engineを呼ばないAIを含む全AI経路の受入へ拡張しない |
| CMP-05 | O・観測基盤の技術受入と旧checkの閉鎖を区別 | Compute usage／capacity／resources／controller／宣言panel。[PA-ai routes]のplatform読取り各入口。[PA-ui]では旧`compute-sre-check/policy-check`を無効化 | [PA-p5a]／[PA-p5b]／[PA-p5d]／[PA-p5e]＋[PA-c2c1]／[PA-c2c2]／[PA-c2d]。requests／limits≠実使用、node available≠DB専用余力、count≠health。旧SRE check／任意Policy baselineの必要操作と現行代替の対応はAI-04／05と共にOPEN |
| CMP-06 | D・DB-P7／P8の明示後続 | LOCAL／XS／S alias表示、実resource controlsは未提供 | [PA-全機能計画]のM2／M3とDB-P7／P8。実サイズ、Worker 1→2→1、用途別WarehouseはM1後。M1不足へ再算入しない |
| ADM-01 | T・管理一覧／選択 | DB management。[PA-ui]の`dbAdminInstall/Refresh/Select`、`workbench_server.py`のadmin一覧、`database_inventory.py` | [PA-p4-9]／[PA-p5-6]。P管理selectionとWorksheetを独立させrevision／bindingを保持。新コード不足は今回見つからず、統合受入へ |
| ADM-02 | T・通常logical CREATE | DB managementのnew creation。[PA-ui]の`dbCreationInstall/AdoptCapability/Mutate`、`logical_creations.py`／`database_creations.py` | [PA-logical実装]／[PA-logical本人受入]。本人成功に加え、**条件付き受入（待ち時間改善を継続）**。旧P5-7 blockingだけを現状とせず、性能の継続条件も消さない |
| ADM-03 | T・同一requestの再発見／固定payload | creation一覧／detail／restore。[PA-ui]の`dbCreationReadSaved/Restore/Load`、`database_creations.py` | [PA-p5-6]／[PA-logical実装]。UNKNOWNの元intentを保持し、勝手な新規受付／再送をしない。M1縦断でのreload受入は別 |
| ADM-04 | T・状態別reconcile／resume／stop | creation detail。[PA-ui]の`dbCreationCanMutate/Mutate/RenderProcess`、`database_creation_recovery.py`／`logical_creations.py` | [PA-p5-4]／[PA-p5-5]／[PA-p5-6]／[PA-logical実装]。logicalとstagedのstate契約を混ぜず、現在P・owner・元record条件を維持。UNKNOWNを別成功で消さない |
| ADM-05 | T・READYから新しいSQL | creation detailの新しいSQL。[PA-ui]の`dbCreationSQL` | [PA-logical本人受入]の本人SQL／write／read／Job／reload確認。DB／schema／role／modeをfresh結果で確認する。P6-7の一連の受入は別 |
| ADM-06 | T・display metadata CAS | DB management detail。[PA-ui]の`dbAdminMetadata/RenderDetail`、display API | [PA-p4-2]／[PA-p5-6]。SQL名／bindingを変えずP／CASを維持。新コード不足は今回見つからず、統合受入へ |
| ADM-07 | T・desired／effective grantsの分離 | DB management grants。[PA-ui]の`dbAdminLoadGrants`、`grant_management.py`／`grant_client.py` | [PA-p4-1]／[PA-p4-9]／[PA-p5-6]。昇格pending／剥奪即制限、verified full条件を維持。M1のrole失効縦断は別 |
| ADM-08 | T・非purge retirement | DB management retirement。[PA-ui]の`dbAdminRetire*`、[PA-retirement]の`request_retire/inspect/reconcile` | [PA-p4-8]／[PA-p4-9]／[PA-p5-6]。依存／observe／reconcile／CAS／UNKNOWN保持、data／credential保持。EXP-11のcatalog解除とも別 |
| ADM-09 | C・advanced register／plan／operator applyは条件付き既存操作 | DB managementのregistration／public generation plan／inspect operation。[PA-ui]の`dbAdminInstall`、`warehouse_operations.py` | P2-4／P2-5、[PA-p5-0]、[PA-adr-0038]。`REQUESTED/requires_operator`≠READYは意図した契約。通常logical CREATEとは別。M1で必要なoperator経路・環境・受入範囲のowner判断がOPENであり、未実装／除外のいずれにも即断しない |
| ADM-10 | C・staged approval／replan／recoveryは条件付き | creation staged detail。[PA-ui]の`dbCreationCanMutate/Mutate/RenderProcess`、preparation／recovery modules | [PA-p5-0]／[PA-p5-6]／[PA-adr-0038]。通常CREATEから2承認を外した一方、基盤変更の承認は保持。logical成功をstaged全機能受入へ流用せず、M1範囲と証拠更新のowner判断を残す |
| ADM-11 | C・破壊的purgeは既存nonpurge契約に含まない | 実行入口なし。retireの説明・拒否境界。[PA-retirement]先頭docstring | [PA-p4-8]のdata／credential保持と[PA-adr-0038]の旧領域保持。物理purge／credential／PVC削除を無断追加しない。一方、全機能計画に対するこのinventory行の閉鎖理由・owner合意記録は必要。未実装をそのまま必須免除へ変換しない |

次の限定候補は、AI-02の対応済みB2→Manual WAP／Managed Martsへ正確な通常導線を設け、広すぎるstaging／publish未対応案内を直すことである。旧formや高権限経路を再開放しない。今回rootが選定したSQL-01の空Worksheet修正には混載しない。CMP-04のdated AI文は文書整合の修正候補。AI-04／05は先に必要sourceと残る範囲をownerへ示し、global policy全体の完了とは宣言しない。

[PA-ui]: ../../tools/local_experiment/workbench/index.html
[PA-ai routes]: ../../tools/local_experiment/ai_routes.py
[PA-audit routes]: ../../tools/local_experiment/audit_routes.py
[PA-runtime routes]: ../../tools/local_experiment/warehouse_runtime_routes.py
[PA-retirement]: ../../tools/local_experiment/database_retirement.py
[PA-全機能計画]: database-full-capability-and-compute-plan-2026-10-28.md
[PA-p6-0受入]: database-p6-0-implementation-2026-10-29.md
[PA-p6-1受入]: database-p6-1-implementation-2026-10-29.md
[PA-p6-2受入]: database-p6-2-implementation-2026-10-29.md
[PA-logical復旧]: database-p6-2-logical-recovery-contract-2026-10-29.md
[PA-a1]: database-p6-4a1-local-sql-explanation-2026-10-31.md
[PA-a2]: database-p6-4a2-plan-explanation-2026-10-31.md
[PA-a3]: database-p6-4a3-view-plan-explanation-2026-10-31.md
[PA-a4]: database-p6-4a4-latest-query-source-2026-10-31.md
[PA-a5]: database-p6-4a5-saved-plan-explanation-2026-10-31.md
[PA-b1]: database-p6-4b1-sql-proposals-2026-11-01.md
[PA-b2]: database-p6-4b2-wap-handoff-2026-11-01.md
[PA-c1]: database-p6-4c1-catalog-proposals-2026-11-01.md
[PA-c2a]: database-p6-4c2a-context-observations-2026-11-01.md
[PA-c2b]: database-p6-4c2b-platform-observations-2026-11-01.md
[PA-c2c1]: database-p6-4c2c1-workload-baseline-2026-11-02.md
[PA-c2c2]: database-p6-4c2c2-foundation-baseline-2026-11-03.md
[PA-c2d]: database-p6-4c2d-foundation-controllers-2026-11-03.md
[PA-d1]: database-p6-4d1-ai-audit-metadata-2026-11-01.md
[PA-ai設定]: database-p6-4e-ai-settings-2026-11-02.md
[PA-p5a]: database-p6-5a-compute-observations-2026-11-01.md
[PA-p5b]: database-p6-5b-platform-resources-2026-11-02.md
[PA-p5c]: database-p6-5c-contract-2026-11-02.md
[PA-p5d]: database-p6-5d-platform-usage-2026-11-03.md
[PA-p5e]: database-p6-5e-platform-capacity-2026-11-03.md
[PA-p5f]: database-p6-5f-compute-history-2026-11-03.md
[PA-p5g]: database-p6-5g-compute-explanations-2026-11-03.md
[PA-p4-1]: database-p4-1-grant-boundary-report-2026-10-21.md
[PA-p4-2]: database-p4-2-metadata-management-report-2026-10-21.md
[PA-p4-8]: database-p4-8-acceptance-2026-10-23.md
[PA-p4-9]: database-p4-9-acceptance-2026-10-23.md
[PA-p5-0]: database-p5-0-contract-2026-10-23.md
[PA-p5-4]: database-p5-4-implementation-2026-10-24.md
[PA-p5-5]: database-p5-5-implementation-2026-10-24.md
[PA-p5-6]: database-p5-6-acceptance-2026-10-25.md
[PA-logical実装]: database-logical-create-implementation-2026-10-28.md
[PA-logical本人受入]: database-logical-create-owner-acceptance-2026-10-28.md
[PA-adr-0038]: ../design/07_ADR/ADR-0038_Separate_Logical_Database_Creation_from_Platform_Provisioning.md

## 検証と残るgate

今回のコード差分の重点試験・通常画面・正規回帰と、89行の静的な証拠照合は区別して記録する。実環境のcontrol-store／保護設定と、元Worksheet・Bear入力の保全を確認する。新しいengine SQL・LLM・Kubernetes操作を監査のためだけに追加しない。

| 検証層 | 今回の結果・限界 |
|---|---|
| 89行の静的照合 | 元matrixと89 ID・順序が一致。重複0、ローカル参照先137件と63参照定義を確認。3分担の独立レビューで通常導線・権限・未完了境界を照合した |
| コード重点試験 | ホストのPython 3.12 venvで`test_workbench_database_ui.py` 69件PASS。実際の＋ボタン経由の空SQL、保存・reload、全context継承、既存draft／run保持、明示SQL引数と旧モードのsample既定値を確認 |
| 正規回帰 | 123群4,914件＝4,880成功・34 skip、失敗0（952.6秒）。最終Workbench集約は1,180件PASS。既存Makefileの123コマンドと一致させ、ホストvenv・`TMPDIR=/tmp`で実施 |
| 通常ブラウザー | ＋Worksheetで空SQL、reload後も同じ空下書きとDB／schema／admin／read-only／LOCAL／上限1000を確認。検証用の空tabだけを閉じ、元19tab・選択SQL・context・Bear入力・言語の一致を確認。監視した160要求はGETのみ、query POST 0、記録の欠落なし |
| 配信・runtime | 既存の8888／8080プロセスを再起動せず、認証付きHTML配信が今回sourceと一致。helper HTTPは無認証401と認証付き200の計2回。backend12ファイル・プロセス引数／cwd／profile／TMPDIRを保持 |
| 台帳・設定保全 | ブラウザー後と全suite後の最終比較がPASS。active store 79表12,234行、旧default 78表156行がschemaを含め完全一致し、追加・変更・削除0。保護12ファイルのbytes／mode、既存UNKNOWN（query 3／admission 3／AI 6／dbt 1）とactive 0を保持 |
| 文書・差分 | matrixの変更行はSQL-01とCMP-04の2行だけで、他87行は同一。`verify-design-context`は14件PASS、94文書・10 issue・42参照の検査が成功。最終`git diff --check`もPASS |

既存UIの通常metadata／status取得は行われるが、新しい実行SQL・LLM要求・Compute操作は送っていない。受動的status取得が既存runtime内で行う観測までゼロと主張しない。空SQL時の実行button活性条件やレイアウトは今回の変更範囲ではない。

証拠はローカルの`var/shirokuma/db-p6-0r1-capability-closure-audit-2026-11-03/private/`に保持する。今回の限定受入は、89行の監査とSQL-01の既定値修正に限る。過去guideの実engine／dbt／LLM・認可／隔離・故障回復の証拠を、今回一括再受入した結果として扱わない。

全行が表に載ったことと全行が完了したことを区別し、必須未対応・合意未確認・技術受入不足・P6-7／M1本人受入を次の作業へ渡す。P7の実Compute伸縮はM1後という順序を維持する。
