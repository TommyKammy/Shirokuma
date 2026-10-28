# P3-5 通常入口での縦断受入（2026-10-20）

## 判定と範囲

Phase 3計画に沿って、Workbenchと通常Go CLIをP3-1の共通実行境界へ接続した。
SALES・FINANCE・任意名DB `insights_e88387a5` の実Trino / Polaris / S3で、
手動Ingest、WAP、Snapshot固定Table Exportの通常入口の正負操作を確認した。
通常入口の実機1,289項目、実ブラウザー、最終回帰、清掃・doctor・清掃後の疎通を完了し、
P3-5の技術受入を通過した。

これは技術受入である。所有者による最終操作性評価はPhase 5に残す。
P2-R01〜03、管理API/UI、Stage・Jobs・Schedule等のDB一般化は今回の完成範囲へ加えない。

計画：[Phase 2 closeout / Phase 3計画](database-phase2-closeout-phase3-plan-2026-10-20.md)。
共通境界と個別unitの実装・既存の実機証拠は
[P3-1](database-p3-1-execution-boundary-report-2026-10-20.md)、
[P3-2](database-p3-2-manual-ingest-report-2026-10-20.md)、
[P3-3](database-p3-3-manual-wap-report-2026-10-20.md)、
[P3-4](database-p3-4-snapshot-export-report-2026-10-20.md)を参照。
今回の通常入口の証拠を、それらの過去の試験で代替していない。

## 提供した通常入口

- Workbench「取り込み・ステージ」の手動Ingestは、ブラウザーで取得した入力bytesを固定し、型付きpreviewを確認してから明示modeで実行する。入力・設定・contextの変更は確認を解除する。作成先locatorと既存Table Refを区別する。
- 「データ変換」の手動WAPは同DBのmodel/version、source alias、build、audit、publicationを扱う。engineerのsave/build/auditとadminのpublish/rollbackを分離する。別roleで公開する場合は、そのroleのcontextでmodel/build/auditを明示取得する。
- ExplorerのTable Exportは、読取可能な安定した実Tableだけを対象とする。CSVとJSONL（UTF-8、1行1 object）を提供し、開始時UUID・Snapshot・schemaを固定する。owner・DB・Table・Snapshot文字列・format・件数・byte数のreceiptと、取得時に再認可するdownloadを表示する。
- 各DataOpsパネル、受付済みoperation、Worksheet、Explorerはそれぞれcontextを保持する。global role ceilingや別パネルの操作で保存済みタブを書き換えない。
- ID不明の応答喪失は、保存済みの同一payload/keyによる明示照会で回復する。UNKNOWNの自動再送・自動成功化は行わない。取消は元のcredential/pinを保持し、UNKNOWNの子へDELETEを送らない。
- View全件Export、Worksheet結果CSV/JSON/TSV保存、旧Stage Export、Jobs/Schedule/dbt等の未対応入口の拒否を維持する。

認証済みHTTP APIは完全な9項目のResolvedQueryContextを受け取り、actor・selected role・DB・schema・binding・generation・grant revisionを再検証する。
idempotency keyは操作の受付前に固定する。受付後に失敗しても、本人が認可されたoperation/contextを回復用応答へ残す。
通常CLIはHTTP clientとして動作し、backend credentialや直接adapterへの迂回を持たない。

## 通常CLI

既存のactive profileとprivate client認証を使用する。Workbenchの通常launcherは既存DataOps virtualenvを選択し、PATHの一時書換えを要しない。

```sh
shirokuma workbench

shirokuma lake ingest input.csv \
  --database sales --schema raw --table orders \
  --mode create --format csv --idempotency-key ingest-001

shirokuma lake ingest additions.csv \
  --database sales --schema raw --table orders \
  --mode append --atomic-append --format csv --idempotency-key append-001

shirokuma lake export orders \
  --database sales --schema raw --format jsonl \
  --output orders.jsonl --idempotency-key export-001
```

Ingestの`create / replace / append / upsert`は明示する。通常appendの非atomic仕様とatomic appendを区別する。
Exportへ行数制限は指定できず、通常Worksheetの最大行数とは独立して全件を取得する。

```sh
shirokuma lake wap save \
  --database sales --schema sandbox --model-id daily_orders \
  --sql 'SELECT id, note FROM source' \
  --sources "$SOURCES_JSON" --target "$TARGET_JSON" --expected-version null

shirokuma lake wap build --database sales --schema sandbox \
  --model-id daily_orders --version 1 --idempotency-key build-001
shirokuma lake wap audit --database sales --schema sandbox \
  --build-id "$BUILD_ID" --idempotency-key audit-001
shirokuma lake wap publish --database sales --schema sandbox \
  --build-id "$BUILD_ID" --audit-id "$AUDIT_ID" --idempotency-key publish-001
shirokuma lake wap rollback --database sales --schema sandbox \
  --publication-id "$PUBLICATION_ID" --idempotency-key rollback-001

shirokuma lake operation get "$OPERATION_ID" --context-file saved-operation.json
shirokuma lake operation cancel "$OPERATION_ID" --context-file saved-operation.json
```

`SOURCES_JSON`はalias→同DBのTable Ref、`TARGET_JSON`は作成先locatorまたは既存Table RefのJSON文字列である。
新規modelは`--expected-version null`、更新は現在versionを指定する。publish/rollbackにはadmin権限を選択した認証profileが必要。
`--context-file`は保存した受付応答の元contextを使う。受付後の失敗やdownload失敗でも回復用JSONをstdoutへ残し、終了コードは非ゼロにする。

## 実機での通常入口の証拠

Git管理外の証拠は`var/shirokuma/p3-5-acceptance/fdd90c86/`、
ブラウザーの観察記録と取得ファイルは`var/shirokuma/p3-5-acceptance/browser-eb95071e/`に保存した。
成功経路は通常HTTP・通常Go CLI・実Workbenchから実行し、台帳と実engineの値を別途照合した。
fixture準備、障害注入、operatorによる照合・清掃は成功経路と区別して記録している。

| 対象 | 通過項目数 | 主な証明 |
| - | -: | - |
| HTTP Ingest / Export | 267 | 3DB同名Table、型・件数・bytes、CSV/JSONL、再照会、SALES 1,003行の全件出力 |
| 通常Go CLI Ingest / Export | 243 | 同じ3DBの通常コマンド、明示mode、本人付き取得、出力照合 |
| HTTP WAP | 174 | engineer build/audit、admin publish×2/rollback、実値2→1→2、UUID・監査元対応 |
| 通常Go CLI WAP | 174 | nested CLIによる同じ3DBの縦断、権限の正負確認 |
| 本人・DB・role・context | 86 | sales-only / finance-only / 両DB本人、admin降格、owner違い、古いbinding/generation/grant等を拒否 |
| View | 27 | 3DBの実View SELECT正常系、HTTP/CLI Export拒否、子SQL・出力なし |
| 入力preview | 52 | 型・format・入力確認と書込前の不正入力拒否 |
| 同時append | 22 | COUNT7の後、SELECT送信前にappendで8行へ更新。Exportは同じ開始時Snapshotの7行 |
| drain | 20 | 新規受付503、新規operation/子SQLなし。受理済み操作・通常CLI照会は継続、OPENへ復帰 |
| 応答喪失 | 24 | parent/child UNKNOWNを保持、HTTP/CLIの同キー照会で実POST増加なし、実backend照合後に明示解消 |
| grant失効・取消 | 47 | 取得拒否、元pinによる実DELETE/通常CLI取消、再付与後の新context正常系 |
| mode途中失敗・同名再作成 | 41 | 型不一致appendでtarget不変、実UUIDの変化を測定して旧Refを拒否 |
| Snapshot消失 | 17 | 開始時Snapshotを実際に失効させ、latestのSELECTへfallbackせず失敗 |
| WAP監査後の変更 | 46 | 元RAW append後も監査stageを公開、stage変更後は公開拒否、target不変 |
| commit後の清掃・記帳失敗 | 49 | 実commitを保持、同キー再送0SQL、架空の公開履歴なし、未照合modelの次操作を拒否 |

phaseごとの台帳集計は累積値であり、operation/query/artifact件数を合算しない。
上表の15phaseは1,289項目が通過した。commit後の所有stage清掃失敗でも実targetを照合し、
WAPの公開後記帳失敗は`FINISHED / committed=true / cleanup_error`として保持した。
記帳できなかった公開を成功履歴として捏造せず、そのmodelの次のsave/build/publishを
`MODEL_RECONCILIATION_REQUIRED`で0SQLのまま拒否することを確認した。
初回のfixture/harness設定ミスと修正後の試行も別ファイルに残し、失敗を削除していない。
通常View DDLは既存SQL guardで拒否されるため、実Viewの準備だけは明示adminの診断transportを使用した。
実Viewのresolverは安定Refを返さない。APIの型guardは実Viewを観測登録した診断用VIEW Refで確認し、UI/CLIの安定Ref拒否と区別した。
View Export成功や通常View作成機能を提供したとは扱わない。

## 実ブラウザーの観察

- 3DBで同名CSV Tableを各5行作成し、任意名DBにはNULLを含むJSONL 6行も取り込んだ。大きいBIGINT、Decimal(38,9)、先頭ゼロ、microsecond timestamp、Unicode・改行・空文字・literal backslash-Nを確認した。
- SALESでengineerのsave/build/audit、adminの明示再取得とpublish、2版目のpublish、rollbackを操作した。独立した実値照合は5→2→5、同じtarget UUID、元auditのprovenance復元を示した。
- CSV実downloadは5行710bytes、JSONLは6行1,094bytes。実ファイルを別途parseし、全行の型付き値と入力の一致、JSONLのNULLと空文字の区別を確認した。
- SALES grant rev1を失効させると、元operation照会403、receiptのowner/件数/Snapshotとdownloadボタンの消去を確認した。receipt/contentの直接取得も403。rev3で再付与後、旧contextは拒否を維持し、新contextの新規Exportとdownloadが成功した。
- SALES RAW/engineer、FINANCE RAW/admin・admin-write、任意名DB MARTS/viewerの3タブで、別々のSQL/DB/Schema/保存role/mode/LOCALを保持した。ExplorerやDataOps、global role ceiling、日英切替はそれらを書き換えなかった。
- 元から存在したWorksheetのSQLと全contextはそのまま保持した。今回追加した一時タブだけを整理し、最終疎通用のread-onlyタブを残す。
- Cmd/Ctrl+Enterからの実SQL、定期更新後のSQL focus保持、実ViewのExport無効、未対応capabilityの再描画後の無効を確認した。
- role喪失で読込済みWAP SQLを消去した。実際に遅延させたpreviewのHTTP200応答が後から到着しても、sampleと確認ボタンは復活しなかった。検証用network latencyはゼロへ戻した。

ブラウザーのdownloadイベント待受は一度timeoutしたが、Chromeによる実ファイル保存と710bytesの全内容照合で成功を確認した。
ブラウザー操作の自動観察と、所有者自身による最終操作性評価は分けている。

## 受入中に修正した問題

- 共通取消のUNKNOWN raceを修正し、parent/childとpinを再確認して不明な子へのDELETEや誤ったCANCELED化を防いだ。
- 通常Warehouse CLIの返却envelopeをGo側契約へ揃えた。小さい`--max-rows 1`でのSELECT1は上限取消の負例として保持し、十分な上限の別要求でFINISHED/実値1を確認した。
- DataOpsのadmissionエラーを共通契約へ揃えた。DRAININGは汎用502ではなく`503 WAREHOUSE_MAINTENANCE`、競合・gate timeoutは既存の409を維持する。
- 古いgrant revisionの個別履歴が、有効な現在DBの履歴全体を403にする問題を修正した。古いrecordは隠し、旧queryの直接取得拒否と一覧返却直前の現在DB再認可を維持する。
- 履歴の真の認可失敗時は保護データを消し、保存入力を保持して明示的な利用不可と更新操作を示す。Checking表示のままにはしない。
- definiteな受付前拒否後に入力を修正できない問題、role/context更新後に読込済みWAP SQLが残る問題を修正した。真の応答不明は元のintentを保持する。

## 自動検証と最終状態

- `make PYTHON=tools/local_experiment/dataops/.venv/bin/python3 verify-local-experiment`：1,241試験、43suite、32skip、失敗0。
- Workbench全体：230試験。routes：19試験、query/corrections：34試験。実handler・永続台帳・応答順序・履歴中のgrant失効を含む。
- `go test ./cmd/... ./internal/...`：成功。`go test ./...`はGit管理外の過去の`var/.../before/internal/cli`コピーまで拾って失敗したため、その古い証拠を削除せず、製品source範囲を分けて検証した。
- `verify-design-context`：文書追加後の再検査を含め14試験成功。
- `git diff --check`：成功。既存のdirty checkoutを維持し、push・PR・外部公開は行っていない。

物理清掃は測定済み102候補を照合し、現在identityの54件をDROPしてRESTで不存在を確認した。
同名再作成等で既に置換された旧identity48件は、別の実体を消さずskipした。
最終catalog一覧の今回のTable/View/stageは0、private Table Exportファイルも0。
入力・download・診断証拠のcopyは保持した。private Export実ファイル48件と
今回作成した認証/profile copy8件は除去し、元のactive profile/authは維持した。
p26-adminへ今回だけ加えた3DBのgrant行は除去した。
sales-onlyの権限は復元し、失効・再付与に伴うrevisionの前進（7→8→9）は巻き戻していない。
LOCALはOPEN revision37、3DB READY、未解決DataOps 0、実行中/UNKNOWN query 0、warehouse blocker 0。
doctorは実際の通常Goコマンドでexit0/healthy、16check（SQL項目はreadinessの説明）だった。
別途、通常Go Warehouse CLIはFINISHED/実値1/truncated=false、Workbenchも
新しいSALES RAW/admin/read-only contextの`SELECT 1 AS p35_ready`でFINISHED/実値1を確認した。
原Worksheetを保持し、検証用の他2タブだけを閉じ、1枚のread-only疎通タブを残した。

今回の189操作は159 FINISHED（commit94・読取等65）/29 FAILED/1 CANCELEDで、全件終端状態。
子query484件は483 FINISHED/1 CANCELED、parentの本人・9項目context・backend username/pinと全件一致した。
terminal target claim34件（FINISHED33・FAILED1）は再利用可能な履歴として残し、active/UNKNOWN claimは0。
Export metadata59件（COMPLETE48・FAILED9・INTENT2）も履歴を保持する。
INTENT2件のparentはFAILED1・CANCELED1で、未完了fileや昇格可能なartifactはない。
rootによるactive SQLiteのread-only再照合でも、この状態と終端claimを確認した。

最終証拠は上記directoryの`final-readonly-facts.json`、`cleanup-evidence.json`、
`final-doctor.json`。catalogの24回の実Table/View一覧で、今回の名前と所有stageの残存0を照合した。

WAP modelのhead/versionとbuild/audit/publicationはFKで結ばれた監査履歴であり、
清掃のためにimmutable履歴を削除したりFKを無効化したりしない。
今回のmodel定義9件、version16件、build17件、audit17件、publication22件と、
故障注入による1件のmodel metadata照合markerは保存した。
そのmarkerを成功履歴へ書き換えず、対象modelだけの次操作を止める。
物理Table/stageとprivate Exportファイルの清掃、warehouseのUNKNOWN/blocker解消とは区別する。

独立reviewの誤った補助probeがlegacy `var/shirokuma/jobs.sqlite3`を開き、bootstrap自動migrationが動いた可能性がある。
事前baselineがないためlegacy schema/metadataの変更有無は断定できず、そのファイルは削除・復元していない。
実active profileとはpath/inodeが異なり、該当時刻のactive operation/query作成0件、grantは想定値と一致した。
実SQLやactive profileの変更はないことを別途照合し、`legacy-probe-separation-evidence.json`に記録した。

## 持越し

P2-R01（通常grant管理API）、P2-R02（表示名更新API）、P2-R03（名前の全入力境界の実HTTP/engine coverage）はPhase 4へ維持する。
管理UI・DB lifecycle追加・Stage/Jobs/Schedule一般化・外部BI driver認定も今回の受入に含めない。
新しいclean cluster再構築は今回の検証対象ではなく、既存3DB環境での通常入口を検証した。
所有者による最終操作性評価・全体の最終受入はPhase 5で行う。

## 次の計画

[Phase 4実装・実機受入計画](database-phase4-implementation-plan-2026-10-20.md)に、P2-R01〜03を含む実装順序・対象範囲・実機条件を整理した。
同書は計画であり、本報告の受入範囲や保存済み実機結果を拡張するものではない。
