# DB-P6-1 Database別監査ログの実装と検証

2026年10月29日。状態は**技術受入済み**。[実装契約](database-p6-1-contract-2026-10-29.md)に従い、保存済みmetadataを検索・参照するDatabase監査API、通常CLI、Workbenchを実装し、通常環境へ反映した。M1の所有者本人の受入は別に残す。

## 提供する範囲

本人のDB内記録、DB管理情報、基盤管理記録を分ける。本人scopeはactorと保存role、許可Schemaを固定し、DB管理scopeは現在のDB admin権限、platform scopeは現在の実accountのplatform権限を必要とする。別ownerのSQL・結果・定義・artifactを監査の許可で取得することはできない。

query、DataOps、Job／Stage／Mart、WAP、予定・alert、DB作成・要求・grant・表示名・retire・構成operationの保存済み記録を、読み取り専用snapshotから投影する。一覧・詳細・JSON取得の終わりに新しい読取りで認可を再確認する。監査GETは既存のobserve／recover／settleを呼ばず、UNKNOWNの解消や実行状態の変更を行わない。

表示は作成時刻と最新保存状態であり、全状態遷移の再現ではない。未記録時刻やhistoricalな実効roleはnullとする。SQL・prompt・結果・定義・入力file・credential参照・private path・例外全文は、出力にも検索にも使わない。DataOpsのcommitと清掃／記帳問題を別に示す。

期間・source・状態・公開ID等を絞り込める。署名付きのpage／next cursorは本人・role・scope・DB・認可・検索条件へ束縛し、ページ往復で初回の取得上限を保持する。関連IDはFKで証明できる範囲に限り、監査metadataとして再認可できたeventだけを辿れるようにする。

## WorkbenchとCLI

監査タブに独立したscope・参照role・DB、UTC期間、source・状態・検索語、明示検索、前後ページ、inline詳細、metadata JSON保存を置く。編集中のfilterと表示中の条件を分け、WorksheetやExplorerの実行先へ追従しない。role／grant／principal変更時は表示とdownloadを消し、古い応答で復活させない。

`shirokuma audit list`、`show`、`export`、`telemetry`は同じHTTP APIを利用する。CLIから台帳やbackend資格情報を読まず、exportは明示した新しいfileへmetadata JSONを保存する。

旧PawprintsはDB／actor／roleを識別できないため、P専用の独立したglobal previewとする。上限100件と補助sourceであることを明示し、取得済み8項目だけを検索・状態で絞り込める。停止・資格情報不備等は固定codeで表示する。ClickHouseを起動せず、durable監査の検索に接続待ちや成否を混ぜない。

## 検証の記録

### 自動検証

監査service／route／clientとUIの回帰は、同名資源を持つ2DB、別owner、V／E／A／P、途中失効、retire、他DB event ID、secret sentinel、cursor流用／改ざん／同時刻／追加rowを扱う。共有applyを複数DBのいずれかへ誤帰属させないこと、Mart／Stage scanの子DataOpsにも同じ可視性を適用することを確認する。UNKNOWNを変更せず、commit有無と清掃エラーを分ける。

| 検証 | 結果 |
| --- | --- |
| 監査service／route／client | 23件成功 |
| 監査UI | 最終23件成功。独立したDatabase UI 67件も成功 |
| Go | `go test ./...`、`go vet ./...`、通常binary build成功 |
| 設計・形式 | `make verify-design-context`、改行・末尾空白・secretファイル名・diffチェック成功 |
| 全体回帰 | `make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3`成功。2,895件中2,862件成功、33件skip、失敗0。Workbenchは493件成功 |

最初の既定Pythonでの実行は既存inventoryテストのPyYAML不足で停止したため、テスト条件を弱めずプロジェクトの仮想環境を明示して再実行した。focusedの件数を全体回帰へ重複加算しない。skipは既存の専用runtime受入30件、OPA binary不足2件、任意pyarrow依存1件であり、今回の監査テストはすべて実行した。専用runtimeの故障受入を、この回帰の成功から推定しない。

### 実APIと通常CLI

通常profileの同じ認証主体を使い、次を実環境で確認した。

- DB管理scopeで一覧・詳細・JSON取得、source／保存状態／公開IDの検索、UTC期間、前後page。初回のpage cursorで同じ行へ戻れる。
- 別DBからのevent直接取得は`NOT_FOUND`、cursorの他DB流用と改ざんは`STALE_CURSOR`。旧`/api/pawprints`は`FEATURE_UNAVAILABLE`を維持する。
- V／Eはselfのみ。platform／DB管理scope／platform telemetryは拒否する。`logical_owner_20261028`のrawでEの読取り取消記録、martsでVの読取り完了記録を作り、本人の同じ保存roleには見え、別roleには見えないことを確認した。
- 監査GET群の前後で台帳の論理dump digestが一致した。digest計測中はSQL実行・権限変更を行っていない。後のロール確認用queryによる正規の台帳追加とは区別する。
- Pawprints previewはHTTP 200でも`availability=unavailable`、`code=TELEMETRY_UNAVAILABLE`だった。空配列を取得成功・履歴0件と解釈せず、UIも利用不可を表示し、耐久監査は正常に検索できた。非空DTOのfilterとsource停止・timeoutはコード回帰で確認した。実環境でClickHouseを停止したり、不備の原因を特定したりしたとは記録しない。
- 通常`shirokuma audit list/show/export/telemetry`がHTTP経由で成功。showと新規fileへexportしたJSONが一致した。

Eの最初の読取りテストは検証スクリプトが応答の`id`を`query_id`と取り違え、page取得前に中断した。重複送信せず、その1件を正規cancel APIで終端化した。Vのqueryは返された`next_uri`を消費してFINISHEDを確認した。

実環境の非platform DB admin、別owner、grant剥奪、retire、応答途中失効、非空Pawprints、source故障注入はこの反映では再現していない。それらは上記のコード回帰の証拠であり、実運用の故障受入とは区別する。API／CLIの結果は`var/shirokuma/db-p6-1-2026-10-29/private/`に保持する。

### 実ブラウザーと反映

反映前は共有LOCAL稼働中、DB138件READYで、query・DataOps・Job・admissionは終端のみだった。Workbench childのPID・親PID・cwd・portを確認してSIGINTで正常停止し、通常CLIで同じprofileとstateを再使用した。native SQL endpoint、計算・storage基盤、既存DBは再起動していない。

反映後も138件READY、native endpointの元のGo／Python PIDは継続していた。検証用の読取りqueryは1件CANCELED・1件FINISHEDとなり、query・DataOps・Job・admissionはすべて終端だった。既存のhost固有YAML変更2件は今回のコミットへ含めない。

実Chromeで検索、前後page、inline詳細、Job→DataOps→queryの関連metadata、JSON保存、日英切替を確認した。ブラウザーのdownload通知待ちはtimeoutしたが、作成直後の`Downloads/audit-metadata.json`の時刻・JSON構造・query sourceを確認できた。

ロール上限の低下時は、表示を消去し、許可される参照roleとself scopeへ戻し、DB管理／platformを無効にする。以前のDBが利用不可でも、現在許可される別DBを選び直して検索できることを実画面で確認した。この確認で見つかったフォーム同期とDB一覧再取得の不足を修正し、全header handlerを通る回帰を追加した。

390×844の実viewportではDB選択欄と長い適用条件を折り返し、ページ幅・scroll幅とも390pxであることを確認した。表の横scrollは表内に保つ。キーボードで検索欄から取得件数へ移れる。言語の切替でも選択scopeと値を保つ。最後にviewportを元へ戻し、日本語・adminで監査タブを表示した。既存13枚のWorksheetと選択中SQL、DB／Schema／保存admin roleは反映前と一致した。

スクリーンショット：[通常画面](../../var/shirokuma/db-p6-1-2026-10-29/screenshots/audit-database.jpg)、[390px画面](../../var/shirokuma/db-p6-1-2026-10-29/screenshots/audit-narrow.jpg)、[Pawprints利用不可](../../var/shirokuma/db-p6-1-2026-10-29/screenshots/preview-unavailable.jpg)。これらはlocal環境の証拠であり、リポジトリへの画像公開は行わない。

## 保持と後続

既存台帳とPawprintsの保持設定を変更せず、監査用のSQL／prompt／結果コピーや自動削除は追加しない。未記録の中間遷移を補完せず、時刻なしの記録は最後へ並べ、期間filter時に対象外となることを示す。

DB-P6-1の完了はM1の本人受入や、次の共有Warehouse自動休止・復帰の完了を意味しない。次のunitはDB-P6-2とする。
