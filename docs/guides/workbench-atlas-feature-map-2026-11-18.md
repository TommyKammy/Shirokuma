# Workbench 新UI（Shirokuma Atlas）機能対応表

2026-11-18 JST。UX-A0「規則と機能対応表を固める」の成果物。確定した
[Shirokuma Atlas モックアップ](../design/assets/ui_mockups/shirokuma-atlas.html)（`03ece343`）を
現行 Workbench（`tools/local_experiment/workbench/index.html`、配信は `workbench_server.py`）へ
適用する前に、現行の操作・要素ID・不足API・休止判定を整理する。

- 対象は現行 `index.html` の静的な `<button>` 93個と `data-tab` 9個、および動的生成ボタンの主要family。
- 事実はファイルパスと行番号を付ける。推測は「推測」、確認できていないことは「未確認」と書く。
- WPは計画書の UX-A1〜A7。行き先は §6 の決定事項に従う。
- 既存の契約（権限の再確認、取消、UNKNOWNの照合、公開履歴、古い応答の破棄）は変えない。

## 1. 画面の対応

| 現行の画面（`data-tab`） | 現行の主な入口 | Atlas の画面・経路 | 主なWP | 備考 |
| --- | --- | --- | --- | --- |
| SQL Worksheets | `#warehouse`、`tab-warehouse` | SQL ワークシート（`#sql`） | A3 | 最初に開く画面はホームへ変更（A2）。 |
| Data Explorer | `#catalog`、`tab-catalog` | データ探索（`#explore`、`#explore/{db}.{schema}.{table}`） | A3 | DB→スキーマ→オブジェクトの順に選ぶ。Databaseモード以外は「Databaseモードの利用条件と案内」を表示（決定6）。 |
| Ingestion & Stages | `#ingestion`、`tab-ingestion` | 取り込み・ステージ（`#ingestion`、`#ingestion/stages`） | A4 | ファイル取込と保存済みステージ。 |
| Transformation | `#transformation`、`tab-transformation` | データ変換（`#transformation`） | A4 | モデル、手動WAP、公開済みMARTS、dbt。 |
| Jobs | `#jobs`、`tab-jobs` | ジョブ（`#jobs`、`#jobs/{id}`） | A6 | 通知は上部バーへ。 |
| AI Bears | `#bears`、`tab-bears` | AI Bears（`#bears`） | A6 | 5体の常駐AIと受け渡し。 |
| DB management | `#db-admin`、`tab-db-admin` | データベース管理（`#databases`、`#databases/{id}`、`#databases/create`） | A5 | 管理者のみ。 |
| Audit Log | `#pawprints`、`tab-pawprints` | 監査ログ（`#audit`、`#audit/{id}`） | A6 | db-audit の sources を統合し、全利用者のクエリ・ロード履歴の置き場所にする（決定8）。 |
| Compute & Governance | `#compute`、`tab-compute` | コンピュート & ガバナンス（`#compute`） | A6 | 休止・起動、自動休止、AI設定（本体）、運用診断。 |
| （新設） | なし | ホーム（`#home`） | A2 | 最初に開く画面。確認が必要な項目を集める。 |

現行は `workbench-boot` の起動画面を持ち、`workbench-header`・`workbench-nav`・`workbench-main` を
`inert` で初期非表示にする（`index.html:29-61`、`tests/test_workbench_navigation_ui.py`）。
Atlas適用後も起動失敗時のフォールバックは残す（UX-A1）。

左ナビの「お気に入り」区分（ピン留めしたテーブル・DB）は、現行 Workbench にピン留め機能がないため
未実装（UX-A1）。必要になった時点で保存・解除・利用終了DBの除外を同じWPで設計する。

## 2. 機能対応表

### 2.1 静的なボタン

`index.html` の `<button>` をすべて数え、`id` のない主要なボタン（サンプルSQL 3個）も含めた。
全93個。`data-tab` の9個は §2.3、`role="tab"` のサブタブ14個はこの表に含めている。

#### 起動

| 現行の場所 | 現行のID・ラベル | 機能 | 新UIでの置き場所（画面・領域） | WP | 備考 |
| --- | --- | --- | --- | --- | --- |
| `index.html:33` | `workbench-boot-reload` | 起動失敗時の再読み込み | 起動画面（Atlas外のフォールバック） | A1 | そのまま残す。通常のAtlas画面には出さない。 |

#### 共通ヘッダー

| 現行の場所 | 現行のID・ラベル | 機能 | 新UIでの置き場所（画面・領域） | WP | 備考 |
| --- | --- | --- | --- | --- | --- |
| `index.html:39` | `quick-chill` | 共有LOCALの休止（旧ヘッダー） | 上部バーの状態チップ→コンピュート & ガバナンス | A1/A6 | 旧ヘッダーの重複操作。A1でチップへ、休止の確認はA6で行う。 |
| `index.html:39` | `quick-wake` | 共有LOCALの起動（旧ヘッダー） | 上部バーの状態チップ→コンピュート & ガバナンス | A1/A6 | 旧ヘッダーの重複操作。A1でチップへ、実行はA6へ移す。 |
| `index.html:40` | `database-compute-status` | DBモードのコンピュート状態表示 | 上部バーのコンピュート状態チップ | A1 | 表示のみ。クリックでコンピュート画面へ。 |
| `index.html:43` | `ai-settings-button` | AIモデル設定を開く | ユーザーメニュー（コンピュート & ガバナンスのAI設定へのリンク） | A1/A6 | 設定本体はコンピュート & ガバナンス。ユーザーメニューはリンクだけ（決定1）。 |
| `index.html:44` | `lang-en` | UI言語を英語に切り替える | ユーザーメニュー | A1 | 現在の `aria-pressed` を維持する。 |
| `index.html:44` | `lang-ja` | UI言語を日本語に切り替える | ユーザーメニュー | A1 | 同上。 |

#### データベース管理

| 現行の場所 | 現行のID・ラベル | 機能 | 新UIでの置き場所（画面・領域） | WP | 備考 |
| --- | --- | --- | --- | --- | --- |
| `index.html:63` | `db-admin-refresh` | DB一覧とリビジョンの更新 | データベース管理（一覧） | A5 | Atlasの一覧は自動更新・引継ぎ。IDは残す。 |

#### SQLワークシート

| 現行の場所 | 現行のID・ラベル | 機能 | 新UIでの置き場所（画面・領域） | WP | 備考 |
| --- | --- | --- | --- | --- | --- |
| `index.html:69` | `chill` | 共有LOCALの休止 | コンピュート & ガバナンス | A6 | SQL画面の操作列から外し、状態とリンクだけ残す。 |
| `index.html:69` | `refresh-status` | コンピュート状態の再観測 | コンピュート & ガバナンス／上部チップ | A6 | 観測期限切れの再取得に使う。 |
| `index.html:69` | `wake` | 共有LOCALの起動 | コンピュート & ガバナンス | A6 | SQL画面の操作列から外し、起動はコンピュート画面へ移す。 |
| `index.html:73` | （idなし）Customers | 顧客のサンプルSQLを挿入 | SQLワークシート（サンプルSQL） | A3 | idなし。同上。 |
| `index.html:73` | （idなし）Recent orders | 注文のサンプルSQLを挿入 | SQLワークシート（サンプルSQL） | A3 | idなし。Atlasではセレクト＋読み込みに統合する。 |
| `index.html:73` | （idなし）TPC-H Revenue by Nation | TPC-H国別売上のサンプルSQLを挿入 | SQLワークシート（サンプルSQL） | A3 | idなし。同上。 |
| `index.html:76` | `add-worksheet-tab` | ワークシートを追加 | SQLワークシート（タブバーの「＋」） | A3 | モックの `add-worksheet`。IDは残す。 |
| `index.html:76` | `worksheet-close-action` | 選択したワークシートを閉じる | SQLワークシート（タブの「閉じる」） | A3 | モックの `close-worksheet`。最後の1枚の保護は残す。 |
| `index.html:78` | `run-query` | SQLを実行（選択範囲／全文） | SQLワークシート（エディター直下） | A3 | 文言を「▶ 選択範囲を実行」「▶ 全文を実行」に。IDは残す。 |
| `index.html:79` | `copilot-explain` | SQLを説明する | AI Bears（SQLを説明） | A6 | 実行計画の実測はしない。SQL画面のSQL提案とは別（A1）。 |
| `index.html:79` | `copilot-generate` | 説明からSQLを生成 | AI Bears（Query Doctor／変換案） | A6 | AI Copilotの生成はAI Bearsへ。SQL画面のSQL提案は残す（A1）。 |
| `index.html:79` | `copilot-publish` | martsに公開 | データ変換（公開確認） | A4 | 公開影響を1行で表示する。 |
| `index.html:79` | `copilot-stage` | サンドボックスにステージして監査 | データ変換（候補の構築・品質検査） | A4 | AI Bearsの変換案から受け取る。 |
| `index.html:82` | `copy-tsv` | 結果をTSVでコピー | SQLワークシート（結果ツール） | A3 | 残す。保存（CSV/JSON/TSV）の隣の副操作。IDは維持（決定2）。 |
| `index.html:82` | `export-csv` | 結果をCSVで保存 | SQLワークシート（結果ツール） | A3 | 受信全行・受信順の契約を維持する。 |
| `index.html:82` | `export-json` | 結果をJSONで保存 | SQLワークシート（結果ツール） | A3 | 同上。 |
| `index.html:82` | `export-tsv` | 結果をTSVで保存 | SQLワークシート（結果ツール） | A3 | 現在は非表示。Atlasでは常時表示にする。 |
| `index.html:82` | `result-view-chart` | 結果をグラフ表示 | SQLワークシート（結果ツール） | A3 | モックの「グラフ」。 |
| `index.html:82` | `result-view-table` | 結果をテーブル表示 | SQLワークシート（結果ツール） | A3 | モックの「テーブル」。IDは残す。 |

#### 取り込み・ステージ

| 現行の場所 | 現行のID・ラベル | 機能 | 新UIでの置き場所（画面・領域） | WP | 備考 |
| --- | --- | --- | --- | --- | --- |
| `index.html:89` | `ingestion-view-file` | ファイル取込の表示に切り替え | 取り込み・ステージ（ファイル取込） | A4 | タブ。IDは残す。 |
| `index.html:89` | `ingestion-view-stages` | 保存済みステージの表示に切り替え | 取り込み・ステージ（保存済みステージ） | A4 | タブ。IDは残す。 |
| `index.html:92` | `clear-upload` | ブラウザファイルの選択を解除 | 取り込み・ステージ（取込元の変更） | A4 | モックの「取込元ファイルを変更」detailsに移す。 |
| `index.html:98` | `preview-ingest` | 取込ファイルのスキーマとサンプルを表示 | 取り込み・ステージ（ファイル取込） | A4 | 既存行数・更新行数を追加する（要API2）。 |
| `index.html:98` | `run-ingest` | ファイルを取り込む | 取り込み・ステージ（確定） | A4 | 確定ボタン直前に影響1行を出す。 |
| `index.html:103` | `stage-new` | 新しいステージを登録 | 取り込み・ステージ（保存済みステージ） | A4 |  |
| `index.html:103` | `stages-refresh` | ステージ一覧を更新 | 取り込み・ステージ（保存済みステージ） | A4 |  |
| `index.html:116` | `stage-save` | ステージ定義を保存 | 取り込み・ステージ（保存済みステージ） | A4 |  |
| `index.html:125` | `stage-schedule-stop` | 自動取込を停止 | 取り込み・ステージ（スケジュール） | A4 | 停止は保存済みの予定だけを無効にする。 |
| `index.html:136` | `stage-scan` | 今すぐ確認して取り込む | 取り込み・ステージ（手動走査） | A4 | 結果不明時は照合を先に出す。 |
| `index.html:136` | `stage-schedule-save` | 自動取込のスケジュールを保存 | 取り込み・ステージ（スケジュール） | A4 |  |
| `index.html:140` | `stage-files-refresh` | ステージのファイル一覧を更新 | 取り込み・ステージ（ファイル） | A4 |  |
| `index.html:142` | `stage-import` | ステージファイルを取り込む | 取り込み・ステージ（ファイル） | A4 | 影響行数を表示する。 |
| `index.html:142` | `stage-preview-button` | ステージファイルをプレビュー | 取り込み・ステージ（ファイル） | A4 |  |
| `index.html:143` | `stage-view-run` | 取込の実行履歴を開く | ジョブ（実行詳細） | A4/A6 | 取込の実行はジョブ履歴へリンクする。 |
| `index.html:144` | `stage-exports-refresh` | 出力一覧を更新 | 取り込み・ステージ（出力） | A4 | モックに出力UIはないが、機能は残す。 |
| `index.html:151` | `stage-export-submit` | テーブル全体を出力 | 取り込み・ステージ（出力） | A4 | 受領記録とダウンロードの契約を維持する。 |

#### データ変換

| 現行の場所 | 現行のID・ラベル | 機能 | 新UIでの置き場所（画面・領域） | WP | 備考 |
| --- | --- | --- | --- | --- | --- |
| `index.html:158` | `transformation-view-marts` | 更新対象表示に切り替え | データ変換（公開済みMARTS → 更新のレビューと保存） | A4 | 独立タブをやめ、公開済みMARTSの詳細へ統合する（決定3）。 |
| `index.html:158` | `transformation-view-models` | モデル表示に切り替え | データ変換（モデル） | A4 | タブ。IDは残す。 |
| `index.html:158` | `transformation-view-wap` | 手動WAP表示に切り替え | データ変換（手動WAP） | A4 | タブを1本の流れに統合する。 |
| `index.html:160` | `refresh-models` | モデル一覧を更新 | データ変換（モデル） | A4 |  |
| `index.html:160` | `run-dbt-build` | dbtビルドを実行 | データ変換（dbtプラン） | A4 |  |
| `index.html:160` | `run-dbt-test` | dbtテストを実行 | データ変換（dbtプラン） | A4 |  |
| `index.html:163` | `open-model-sql` | モデルのSQLをSQL画面で開く | SQLワークシート | A3/A4 | DB・スキーマ・ロールを引き継ぐ。 |
| `index.html:163` | `refresh-wap-model` | 保存済みWAPモデルを更新 | データ変換（公開済みMARTSの更新） | A4 | 画面文言は「データ変換」に統一する。 |
| `index.html:167` | `mart-new` | 更新対象を新規登録 | データ変換（公開済みMARTS → 更新のレビューと保存） | A4 | 公開済みMARTSの詳細へ統合（決定3）。 |
| `index.html:167` | `marts-reload` | 更新対象一覧を再読込 | データ変換（公開済みMARTS → 更新のレビューと保存） | A4 | 公開済みMARTSの詳細へ統合（決定3）。 |
| `index.html:176` | `mart-save` | 更新対象を保存 | データ変換（公開済みMARTS → 更新のレビューと保存） | A4 | 公開済みMARTSの詳細へ統合（決定3）。 |
| `index.html:180` | `mart-run` | 更新対象を手動更新 | データ変換（公開済みMARTS → 更新のレビューと保存） | A4 | 全件置換の影響を表示する（決定3）。 |
| `index.html:180` | `mart-view-run` | 更新実行をジョブで確認 | ジョブ（実行詳細） | A6 | 更新のレビューから実行詳細へ（決定3）。 |
| `index.html:182` | `mart-schedule-stop` | 自動更新を停止 | データ変換（公開済みMARTS → 更新のレビューと保存） | A4 | 決定3。 |
| `index.html:194` | `mart-schedule-save` | 自動更新のスケジュールを保存 | データ変換（公開済みMARTS → 更新のレビューと保存） | A4 | 決定3。 |

#### ジョブ

| 現行の場所 | 現行のID・ラベル | 機能 | 新UIでの置き場所（画面・領域） | WP | 備考 |
| --- | --- | --- | --- | --- | --- |
| `index.html:202` | `jobs-view-alerts` | 通知の表示に切り替え | 上部バー（ベル＋ポップオーバー）／ジョブ画面 | A1/A6 | 未読件数・最新アラート・ジョブ画面へのリンク（決定4）。 |
| `index.html:202` | `jobs-view-definitions` | 保存済みジョブの表示に切り替え | ジョブ（一覧・詳細） | A6 | タブを1画面に統合する。 |
| `index.html:202` | `jobs-view-runs` | 実行履歴の表示に切り替え | ジョブ（実行履歴） | A6 | 詳細の下へ移す。 |
| `index.html:205` | `job-new` | 新しいジョブを作成 | ジョブ（定義） | A6 |  |
| `index.html:205` | `jobs-refresh` | ジョブ一覧を更新 | ジョブ（一覧） | A6 |  |
| `index.html:211` | `job-add-step` | ジョブにステップを追加 | ジョブ（定義） | A6 |  |
| `index.html:211` | `job-save` | ジョブ定義を保存 | ジョブ（定義） | A6 |  |
| `index.html:214` | `job-run` | ジョブを手動実行 | ジョブ（手動実行） | A6 |  |
| `index.html:214` | `job-run-open` | 実行詳細を開く | ジョブ（実行詳細） | A6 |  |
| `index.html:217` | `job-schedule-stop` | ジョブのスケジュールを停止 | ジョブ（スケジュール） | A6 |  |
| `index.html:229` | `job-schedule-save` | ジョブのスケジュールを保存 | ジョブ（スケジュール） | A6 |  |
| `index.html:236` | `job-runs-refresh` | 実行履歴を更新 | ジョブ（実行履歴） | A6 |  |
| `index.html:240` | `job-alerts-refresh` | 通知を更新 | 上部バー（ベル＋ポップオーバー）／ジョブ画面 | A1/A6 | 未読件数・最新アラート・ジョブ画面へのリンク（決定4）。 |

#### データ探索

| 現行の場所 | 現行のID・ラベル | 機能 | 新UIでの置き場所（画面・領域） | WP | 備考 |
| --- | --- | --- | --- | --- | --- |
| `index.html:244` | `refresh-catalog` | カタログを更新 | データ探索 | A3 |  |
| `index.html:245` | `clone-table-btn` | サンドボックスへ複製 | データ探索（テーブルの管理） | A3 |  |
| `index.html:245` | `drop-table-btn` | カタログ登録を解除（データ保持） | データ探索（テーブルの管理） | A3 | 名前確認とデータ保持の説明を残す。 |
| `index.html:245` | `export-table-btn` | テーブルをエクスポート | データ探索（テーブルの管理） | A3 |  |
| `index.html:245` | `open-table-sql` | テーブルをSQLで開く | SQLワークシート（テーブル詳細から） | A3 | 主ボタン「SQLで開く →」。 |
| `index.html:245` | `refresh-table-details` | テーブル詳細を更新 | データ探索（詳細） | A3 |  |
| `index.html:247` | `inspect-tab-columns` | カラム定義のタブ | データ探索（スキーマタブ） | A3 | DDLを内包する。 |
| `index.html:248` | `inspect-tab-preview` | データプレビューのタブ | データ探索（データプレビュー） | A3 |  |
| `index.html:249` | `inspect-tab-profile` | プロファイル・NULL統計のタブ | データ探索（プロファイル） | A3 |  |
| `index.html:250` | `inspect-tab-ddl` | DDLのタブ | データ探索（スキーマタブ内） | A3 | 独立タブを廃止する。 |
| `index.html:251` | `inspect-tab-snapshots` | スナップショット履歴のタブ | データ探索（履歴・タイムトラベル） | A3 |  |
| `index.html:252` | `inspect-tab-lineage` | テーブルリネージのタブ | データ探索（リネージ） | A3 |  |

#### 監査ログ

| 現行の場所 | 現行のID・ラベル | 機能 | 新UIでの置き場所（画面・領域） | WP | 備考 |
| --- | --- | --- | --- | --- | --- |
| `index.html:263` | `refresh-pawprints` | Pawprintsを更新 | 監査ログ（db-audit の sources） | A7 | 旧Pawprintsの更新UIはA7で撤去（決定8）。 |
| `index.html:264` | `refresh-history` | クエリ・ロード履歴を更新 | 監査ログ（db-audit の sources） | A7 | db-audit の sources に統合し、旧履歴の更新UIはA7で撤去（決定8）。 |

#### コンピュート & ガバナンス

| 現行の場所 | 現行のID・ラベル | 機能 | 新UIでの置き場所（画面・領域） | WP | 備考 |
| --- | --- | --- | --- | --- | --- |
| `index.html:265` | `compute-policy-check` | Policy Gatekeeper監査 | コンピュート & ガバナンス（運用診断） | A6 |  |
| `index.html:265` | `compute-refresh` | コンピュート状態を更新 | コンピュート & ガバナンス | A6 |  |
| `index.html:265` | `compute-sre-check` | SREクラスタ健全性チェック | コンピュート & ガバナンス（運用診断） | A6 |  |
| `index.html:268` | `save-auto-chill` | 自動休止を適用 | コンピュート & ガバナンス（自動休止） | A6 |  |

#### AIモデル設定

| 現行の場所 | 現行のID・ラベル | 機能 | 新UIでの置き場所（画面・領域） | WP | 備考 |
| --- | --- | --- | --- | --- | --- |
| `index.html:279` | `ai-close` | AI設定を閉じる | コンピュート & ガバナンス（AI設定） | A6 |  |
| `index.html:279` | `ai-save` | AI設定を保存 | コンピュート & ガバナンス（AI設定） | A6 |  |
| `index.html:279` | `ai-test` | AI設定の接続テスト | コンピュート & ガバナンス（AI設定） | A6 |  |
### 2.2 動的に生成される操作（ボタン・リンク）

現行のDatabaseモードは、画面を開いた後に `dbAdminButton`・`dbCreationButton`・`dataopsButton` などで
ボタンやリンクを生成する。数が多く、IDの一部は実行時のDB IDやモデルIDになるため、機能familyごとに
置き場所とWPを決める。値そのものは各WPの実装時に既存の生成関数を残して維持する。

| 現行の場所 | IDのfamily（例） | 機能 | 新UIでの置き場所（画面・領域） | WP | 備考 |
| --- | --- | --- | --- | --- | --- |
| DB管理（一覧・作成・照合） | `db-admin-creation-entry`、`db-admin-check-readiness`、`db-admin-inspect`、`db-admin-use-plan`、`db-admin-use-registration`、`db-admin-grant-revoke` | 作成入口、READY確認、操作照会、plan/registrationの再利用、権限取消 | データベース管理（作成・READY・権限・要確認の操作） | A5 | `db-admin-*` のIDは残す。 |
| DB管理（登録・plan/apply） | `db-admin-register`、`db-admin-plan`、`db-admin-apply`、`db-admin-operation-id`、`db-admin-predecessor`、`db-admin-delivery`、`db-admin-phase` | 既存スペックの登録、warehouse plan/applyの復旧、READY確認 | データベース管理（管理・復旧） | A5 | 管理者のみの「管理」折りたたみに残す（決定9）。 |
| DB管理（作成ウィザード） | `db-creation-new`、`db-creation-next`、`db-creation-back`、`db-creation-add`、`db-creation-submit`、`db-creation-approve`、`db-creation-retry`、`db-creation-sql`、`db-creation-team`（新設） | 名前・担当チーム・初期アクセス・確認・作成・照合・再試行 | データベース管理（DBを作成） | A5 | 必須の担当チームを論理作成に追加（決定5・7、要API3）。 |
| DB管理（DB別アクセス権） | `db-admin-grants`、`db-admin-grant-principal`、`db-admin-grant-role`、`db-admin-grant-revision`、`db-admin-grant`、`db-admin-grant-revoke` | 利用者ごとの希望ロール、実効ロール、検証 | データベース管理（DB別のアクセス権） | A5 | 閲覧者は読み取り専用。 |
| DB管理（表示情報） | `db-admin-metadata`、`db-admin-schemas`、`db-admin-database-{id}-*`、`db-admin-schema-{id}-*` | DB・スキーマの表示名と説明の更新、リビジョン確認 | データベース管理（DB・スキーマの表示情報） | A5 | リビジョン照合を残す。 |
| DB管理（作成再開・照合） | `db-creation-reconcile`、`db-creation-resume`、`db-creation-withdraw`、`db-creation-retry`、`db-creation-approve`、`db-creation-replan` | 作成の照合・再開・中止・再試行・承認 | データベース管理（要確認の操作） | A5 | 応答不明を再送しない。 |
| DB管理（利用終了） | `db-admin-retire-preview`、`db-admin-retire-inspect`、`db-admin-retire-confirm`、`db-admin-retire-save`、`db-admin-retire-reconcile-save`、`db-admin-retire-*` | 影響確認、対象確認、開始、観測、照合、完了 | データベース管理（利用終了） | A5 | `database_retirement` の現行の仕組みを使う。 |
| 監査ログ（Databaseモード） | `db-audit-panel`、`db-audit-submit`、`db-audit-next`、`db-audit-previous`、`db-audit-download`、`db-audit-telemetry`、`db-audit-preview-*` | 保存済みメタデータ監査の範囲・発生元・検索・ページ・保存 | 監査ログ | A6 | `db-audit` の sources を統合し、全利用者のクエリ・ロード履歴の置き場所にする（決定8）。発生元の表示名は日本語化。 |
| コンピュート（状態・操作） | `runtime-wake`、`runtime-chill`、`runtime-save`、`runtime-refresh`、`runtime-reconcile`、`runtime-receipt`、`runtime-blockers` | 起動・休止・自動休止・照合・受領・妨げの表示 | コンピュート & ガバナンス（状態・操作） | A6 | 休止の可否はサーバーの判定に従う。 |
| コンピュート（使用量・容量） | `observation-usage-*`、`observation-capacity-*`、`observation-resources-*`、`observation-compute-*` | 実使用量、空き容量、観測の取得 | コンピュート & ガバナンス（使用量と空き容量） | A6 | 設定上の割当と混ぜない。 |
| コンピュート（ライフサイクル履歴） | `observation-history-*`、`observation-activity-*` | 起動・休止・ポリシー・照合の履歴 | コンピュート & ガバナンス（履歴） | A6 | UTCは詳細だけ。 |
| コンピュート（基盤観測・診断） | `observation-baseline-*`、`observation-pod-baseline-*`、`observation-controller-baseline-*`、`observation-foundation-*`、`observation-sre-*`、`observation-telemetry-*`、`observation-policy-*`、`observation-recent-failures-*` | 基盤の観測、SREの生ログ、Pod・コントローラーの基準 | コンピュート & ガバナンス（運用診断） | A6 | プラットフォーム管理者だけに出す。 |
| データ探索（テーブルの管理） | `table-profile-*`、`db-clone-*`、`db-drop-*`、`db-table-manage`、`explorer-*`、`catalog-context-*` | プロファイル、サンドボックス複製、登録解除、文脈の再選択 | データ探索（テーブルの管理） | A3 | 登録解除はデータを消さない。 |
| 取り込み（Databaseモード） | `p35-ingest-preview`、`p35-ingest-confirm`、`p35-ingest-cancel`、`p35-ingest-observe` | 入力プレビュー、確定、取消、照会 | 取り込み・ステージ（ファイル取込） | A4 | 既存行数・更新行数を追加（要API2）。 |
| データ変換（手動WAP） | `p35-wap-sql`、`p35-wap-build-action`、`p35-wap-ai-load`、`p35-wap-model-id` | 手動WAPのSQL、ビルド、AI案の読込、モデル選択 | データ変換（手動WAP） | A4 | 画面文言は「データ変換」。 |
| データ変換（ステージ・マート・dbt） | `db-stages-*`、`db-marts-*`、`db-dbt-*` | ステージ、公開済みマート、dbtの計画・実行・公開 | 取り込み・ステージ／データ変換 | A4 | `db-stages-*` は取り込み、`db-marts-*`・`db-dbt-*` はデータ変換。マートは「公開済みMARTS」→「更新のレビューと保存」へ統合（決定3）。 |
| 出力 | `p35-export-*`、`stage-export-*`、`export-table-*` | テーブル全体の出力、受領記録、ダウンロード | 取り込み・ステージ（出力）／データ探索（テーブルの管理） | A4/A3 | 受領記録と再認可の契約を残す。 |
| ジョブ（Databaseモード） | `db-jobs-*` | ジョブ定義、引数、ステップ、実行、履歴 | ジョブ | A6 | 直近100件の表現を残す。 |
| AI Bears（提案・説明） | `proposal-*`、`curator-*`、`explanation-*`、`bear-*` | SQL提案、Catalog Curator、SQL説明、Bear実行 | AI Bears | A6 | AI Copilotの生成・説明はAI Bearsへ。SQL画面のSQL提案は残す（A1）。 |
| ジョブの引数・ステップ | `job-arg-*`、`job-step-*` | 引数入力、ステップの追加・削除・並べ替え | ジョブ（定義） | A6 | 型付き引数の検証を残す。 |
| SQLワークシートのタブ | `worksheet-{uuid}` | ワークシートの選択・切替 | SQL ワークシート（タブバー） | A3 | タブの削除・隣接移動を残す。 |

### 2.3 タブ

`data-tab` は9個。すべて `<a>` で、`href="#<data-tab>"` を持つ（`index.html:49-58`、
`tests/test_workbench_navigation_ui.py`）。

| 現行のタブ | ID | 機能 | 新UIでの置き場所 | WP | 備考 |
| --- | --- | --- | --- | --- | --- |
| Data Explorer | `tab-catalog` | カタログ探索 | データ探索 | A3 | ラベルを「データ探索」に。IDは残す。 |
| SQL Worksheets | `tab-warehouse` | SQLワークシート | SQL ワークシート | A3 | ラベルを「SQL ワークシート」に。IDは残す。 |
| Ingestion & Stages | `tab-ingestion` | 取り込み・ステージ | 取り込み・ステージ | A4 | ラベルを「取り込み・ステージ」に。 |
| Transformation | `tab-transformation` | データ変換 | データ変換 | A4 | ラベルを「データ変換」に。 |
| Jobs | `tab-jobs` | ジョブ | ジョブ | A6 | 通知ベルは上部バーへ（決定4）。 |
| AI Bears | `tab-bears` | AI Bears | AI Bears | A6 | 5体の常駐AI。 |
| DB management | `tab-db-admin` | DB管理 | データベース管理 | A5 | 管理者だけに表示。 |
| Audit Log | `tab-pawprints` | 監査ログ | 監査ログ | A6 | ラベルを「監査ログ」に。中身は db-audit の sources（決定8）。 |
| Compute & Governance | `tab-compute` | コンピュート & ガバナンス | コンピュート & ガバナンス | A6 | ラベルはそのまま。 |

`role="tab"` のサブタブは14個（取り込み2、データ変換3、ジョブ3、テーブル詳細6）。
表2.1に含めている。Atlasでは画面内の1本のナビに統合し、`aria-selected` と roving `tabindex` を残す。

## 3. 要素ID一覧

### 3.1 静的に存在するID（226件）

`tests/` 配下の全 `*.py` から `$('...')`、`getElementById('...')`、`querySelector('#...')`、
`byId('...')` を抽出し、`index.html` に静的に存在するIDと交差した。全226件を
「参照ファイル数 / 参照回数」と「残す・変える」で示す。参照回数はテストコード内の出現回数で、
UIテストの実行回数ではない。

#### 共通・起動・ヘッダー

| ID | 参照（ファイル数 / 回数） | 新UIで残す・変える | WP | 備考 |
| --- | --- | --- | --- | --- |
| `ai-close` | 2ファイル / 2回（test_workbench.py ほか） | 残す | A6 | 設定本体はコンピュート & ガバナンス。ユーザーメニューはリンクのみ。IDは残す（決定1）。 |
| `ai-config-status` | 2ファイル / 17回（test_workbench_ai_settings_database_ui.py ほか） | 残す | A6 | 設定本体はコンピュート & ガバナンス。ユーザーメニューはリンクのみ。IDは残す（決定1）。 |
| `ai-key` | 2ファイル / 16回（test_workbench_ai_settings_database_ui.py ほか） | 残す | A6 | 設定本体はコンピュート & ガバナンス。ユーザーメニューはリンクのみ。IDは残す（決定1）。 |
| `ai-model` | 2ファイル / 12回（test_workbench_ai_settings_database_ui.py ほか） | 残す | A6 | 設定本体はコンピュート & ガバナンス。ユーザーメニューはリンクのみ。IDは残す（決定1）。 |
| `ai-save` | 1ファイル / 9回（test_workbench_ai_settings_database_ui.py） | 残す | A6 | 設定本体はコンピュート & ガバナンス。ユーザーメニューはリンクのみ。IDは残す（決定1）。 |
| `ai-settings` | 2ファイル / 14回（test_workbench_ai_settings_database_ui.py ほか） | 残す | A6 | 設定本体はコンピュート & ガバナンス。ユーザーメニューはリンクのみ。IDは残す（決定1）。 |
| `ai-settings-button` | 3ファイル / 10回（test_workbench_ai_settings_database_ui.py ほか） | 残す | A1/A6 | 設定本体はコンピュート & ガバナンス。ユーザーメニューはリンクのみ。IDは残す（決定1）。 |
| `ai-settings-form` | 2ファイル / 4回（test_workbench.py ほか） | 残す | A6 | 設定本体はコンピュート & ガバナンス。ユーザーメニューはリンクのみ。IDは残す（決定1）。 |
| `ai-shared-help` | 1ファイル / 2回（test_workbench_ai_settings_database_ui.py） | 残す | A6 | 設定本体はコンピュート & ガバナンス。ユーザーメニューはリンクのみ。IDは残す（決定1）。 |
| `ai-test` | 2ファイル / 5回（test_workbench_ai_settings_database_ui.py ほか） | 残す | A6 | 設定本体はコンピュート & ガバナンス。ユーザーメニューはリンクのみ。IDは残す（決定1）。 |
| `database-compute-state` | 1ファイル / 7回（test_workbench_warehouse_runtime_ui.py） | 残す | A1 | 上部バーのコンピュート状態チップへ移す。 |
| `database-compute-status` | 1ファイル / 2回（test_workbench_warehouse_runtime_ui.py） | 残す | A1 | 上部バーのコンピュート状態チップへ移す。 |
| `legacy-header-warehouse` | 1ファイル / 1回（test_workbench_warehouse_runtime_ui.py） | 削除 | A1 | 上部バーの状態チップに置き換え。A7で削除。 |
| `quick-chill` | 2ファイル / 4回（test_workbench_warehouse_runtime_ui.py ほか） | 変える | A1 | 旧ヘッダーの重複操作。上部バーのチップに置き換え、休止の確認はA6のコンピュート画面で行う。 |
| `quick-status-error` | 3ファイル / 8回（test_workbench_database_ui.py ほか） | 変える | A1 | 画面単位メッセージへ統合。旧IDはA7で削除。 |
| `quick-wake` | 3ファイル / 8回（test_workbench_database_ui.py ほか） | 変える | A1 | 旧ヘッダーの重複操作。上部バーのコンピュート状態チップに置き換え、実行はA6のコンピュート画面へ移す。 |
| `quick-warehouse-state` | 3ファイル / 5回（test_workbench_creations.py ほか） | 変える | A1 | チップ表示に統合。旧IDはA7で削除。 |
| `role-indicator` | 1ファイル / 2回（test_workbench.py） | 残す | A1 | ユーザーメニュー／上部バーへ移す。IDは残す。 |
| `role-selector` | 6ファイル / 49回（test_workbench_database_ui.py ほか） | 残す | A1 | ユーザーメニュー／上部バーへ移す。IDは残す。 |
| `role-selector-label` | 1ファイル / 2回（test_workbench_database_ui.py） | 残す | A1 | ユーザーメニュー／上部バーへ移す。IDは残す。 |
| `tab-bears` | 14ファイル / 41回（test_workbench_platform_observations_database_ui.py ほか） | 残す | A1 | ラベルとURLをAtlasへ。IDは残す。 |
| `tab-catalog` | 6ファイル / 18回（test_workbench_database_ui.py ほか） | 残す | A1 | ラベルとURLをAtlasへ。IDは残す。 |
| `tab-compute` | 18ファイル / 95回（test_workbench_compute_governance_database_ui.py ほか） | 残す | A1 | ラベルとURLをAtlasへ。IDは残す。 |
| `tab-db-admin` | 4ファイル / 13回（test_workbench_admin_ui.py ほか） | 残す | A1 | ラベルとURLをAtlasへ。IDは残す。 |
| `tab-ingestion` | 3ファイル / 7回（test_workbench_navigation_ui.py ほか） | 残す | A1 | ラベルとURLをAtlasへ。IDは残す。 |
| `tab-jobs` | 6ファイル / 15回（test_workbench_navigation_ui.py ほか） | 残す | A1 | ラベルとURLをAtlasへ。IDは残す。 |
| `tab-pawprints` | 1ファイル / 1回（test_workbench_navigation_ui.py） | 残す | A1 | ラベルとURLをAtlasへ。IDは残す。 |
| `tab-transformation` | 2ファイル / 5回（test_workbench_ai_wap_database_ui.py ほか） | 残す | A1 | ラベルとURLをAtlasへ。IDは残す。 |
| `tab-warehouse` | 29ファイル / 70回（test_workbench_database_ui.py ほか） | 残す | A1 | ラベルとURLをAtlasへ。IDは残す。 |
| `workbench-boot` | 1ファイル / 9回（test_workbench_navigation_ui.py） | 残す | A1 | 起動・再読込のまま。 |
| `workbench-boot-error` | 1ファイル / 2回（test_workbench_navigation_ui.py） | 残す | A1 | 起動・再読込のまま。 |
| `workbench-boot-loading` | 1ファイル / 2回（test_workbench_navigation_ui.py） | 残す | A1 | 起動・再読込のまま。 |
| `workbench-boot-reload` | 1ファイル / 6回（test_workbench_navigation_ui.py） | 残す | A1 | 起動・再読込のまま。 |

#### SQLワークシート

| ID | 参照（ファイル数 / 回数） | 新UIで残す・変える | WP | 備考 |
| --- | --- | --- | --- | --- |
| `add-worksheet-tab` | 1ファイル / 7回（test_workbench_database_ui.py） | 残す | A3 | ワークシートのタブバーへ。IDは残す。 |
| `chill` | 1ファイル / 2回（test_workbench_warehouse_runtime_ui.py） | 残す | A6 | コンピュート画面の休止操作へ移す。SQL画面には状態とリンクだけ残す。 |
| `copilot-explain` | 10ファイル / 85回（test_workbench_bears_database_ui.py ほか） | 変える | A6 | AI Bearsの「SQLを説明」へ移す。 |
| `copilot-generate` | 3ファイル / 40回（test_workbench_ai_sql_proposals_database_ui.py ほか） | 変える | A6 | AI Copilotの生成はAI BearsのQuery Doctor／変換案へ移す。SQL画面のSQL提案は残す（A1）。 |
| `copilot-publish` | 2ファイル / 3回（test_workbench_ai_wap_database_ui.py ほか） | 変える | A4 | データ変換の公開確認へ移す。 |
| `copilot-request` | 18ファイル / 52回（test_workbench_ai_sql_proposals_database_ui.py ほか） | 変える | A6 | AI Bearsの入力欄へ移す。 |
| `copilot-result` | 4ファイル / 50回（test_workbench_ai_plan_database_ui.py ほか） | 変える | A6 | AI Bearsの結果表示へ移す。 |
| `copilot-stage` | 2ファイル / 4回（test_workbench_ai_wap_database_ui.py ほか） | 変える | A4 | データ変換の候補構築・品質検査へ移す。 |
| `copy-tsv` | 2ファイル / 3回（test_workbench_database_ui.py ほか） | 残す | A3 | 残す。保存（CSV/JSON/TSV）の隣の副操作。IDは維持（決定2）。 |
| `database-page-description` | 1ファイル / 1回（test_workbench_database_ui.py） | 変える | A3 | ページ見出し直下の常設リード文を廃止する。テストの期待値もA3で更新する。 |
| `database-page-title` | 1ファイル / 3回（test_workbench_database_ui.py） | 残す | A3 | 画面名を「データ探索」「SQL ワークシート」に変更する。IDは残す。 |
| `export-csv` | 1ファイル / 1回（test_workbench_result_exports_database_ui.py） | 残す | A3 | 結果ツールへ。IDは残す。 |
| `export-json` | 1ファイル / 1回（test_workbench_result_exports_database_ui.py） | 残す | A3 | 結果ツールへ。IDは残す。 |
| `export-tsv` | 1ファイル / 1回（test_workbench_result_exports_database_ui.py） | 残す | A3 | 結果ツールへ。IDは残す。 |
| `max-rows` | 1ファイル / 1回（test_workbench_database_ui.py） | 残す | A3 | SQLワークシートの実行コンテキスト／結果へ。IDは残す。 |
| `proposal-panel` | 1ファイル / 1回（test_workbench_ai_sql_proposals_database_ui.py） | 変える | A6 | AI Bearsの提案レビューへ移す。 |
| `query-chart` | 1ファイル / 1回（test_workbench.py） | 残す | A3 | SQLワークシートの実行コンテキスト／結果へ。IDは残す。 |
| `query-history` | 1ファイル / 28回（test_workbench_database_ui.py） | 残す | A3 | SQLワークシートの実行履歴。IDは残す。 |
| `query-meta` | 2ファイル / 9回（test_workbench_database_ui.py ほか） | 残す | A3 | SQLワークシートの実行コンテキスト／結果へ。IDは残す。 |
| `query-mode` | 2ファイル / 20回（test_workbench_database_ui.py ほか） | 残す | A3 | SQLワークシートの実行コンテキスト／結果へ。IDは残す。 |
| `query-results` | 3ファイル / 12回（test_workbench_database_ui.py ほか） | 残す | A3 | SQLワークシートの結果表。IDは残す。 |
| `query-schema` | 3ファイル / 12回（test_workbench_database_ui.py ほか） | 残す | A3 | SQLワークシートの実行コンテキスト／結果へ。IDは残す。 |
| `result-action-state` | 2ファイル / 10回（test_workbench_result_exports_database_ui.py ほか） | 残す | A3 | 結果ツールへ。IDは残す。 |
| `result-filter` | 2ファイル / 6回（test_workbench.py ほか） | 残す | A3 | 結果ツールへ。IDは残す。 |
| `run-query` | 6ファイル / 44回（test_workbench_database_ui.py ほか） | 残す | A3 | SQLワークシートの実行ボタン。文言を選択範囲／全文に変更。IDは残す。 |
| `sql` | 28ファイル / 208回（test_workbench_database_ui.py ほか） | 残す | A3 | SQLエディター本体。IDは残す。 |
| `status-error` | 3ファイル / 12回（test_workbench_database_ui.py ほか） | 残す | A3 | SQLワークシートの画面単位メッセージへ統合する。IDは残す。 |
| `wake` | 1ファイル / 1回（test_workbench_database_ui.py） | 残す | A6 | コンピュート画面の起動操作へ移す。 |
| `warehouse` | 2ファイル / 8回（test_workbench_database_ui.py ほか） | 残す | A3 | URLルートのコンテナとして残す。中身はA3で置き換え。 |
| `warehouse-state` | 3ファイル / 6回（test_workbench_creations.py ほか） | 残す | A6 | コンピュート画面の状態表示へ移す。ラベルはAtlasの用語に変更。 |
| `worksheet-close-action` | 1ファイル / 1回（test_workbench_database_ui.py） | 残す | A3 | ワークシートのタブバーへ。IDは残す。 |
| `worksheet-close-select` | 1ファイル / 1回（test_workbench_database_ui.py） | 残す | A3 | ワークシートのタブバーへ。IDは残す。 |
| `worksheet-closers` | 1ファイル / 1回（test_workbench_database_ui.py） | 残す | A3 | ワークシートのタブバーへ。IDは残す。 |
| `worksheet-explorer` | 1ファイル / 11回（test_workbench_database_ui.py） | 残す | A3 | ワークシートのタブバーへ。IDは残す。 |
| `worksheet-tabs` | 4ファイル / 16回（test_workbench_database_ui.py ほか） | 残す | A3 | ワークシートのタブバー。IDは残す。 |
| `worksheet-tree` | 2ファイル / 8回（test_catalog_ui.py ほか） | 残す | A3 | ワークシートのタブバーへ。IDは残す。 |
| `worksheet-tree-filter` | 1ファイル / 4回（test_workbench_database_ui.py） | 残す | A3 | ワークシートのタブバーへ。IDは残す。 |

#### データ探索

| ID | 参照（ファイル数 / 回数） | 新UIで残す・変える | WP | 備考 |
| --- | --- | --- | --- | --- |
| `catalog` | 3ファイル / 4回（test_workbench_database_ui.py ほか） | 残す | A3 | データ探索の一覧・詳細へ。IDは残す。 |
| `catalog-error` | 2ファイル / 17回（test_catalog_ui.py ほか） | 残す | A3 | データ探索の一覧・詳細へ。IDは残す。 |
| `catalog-filter` | 2ファイル / 20回（test_workbench_database_ui.py ほか） | 残す | A3 | データ探索の一覧・詳細へ。IDは残す。 |
| `catalog-tree` | 2ファイル / 13回（test_workbench_database_ui.py ほか） | 残す | A3 | DB→スキーマ→オブジェクトのツリー。IDは残す。 |
| `clone-table-btn` | 4ファイル / 8回（test_workbench_clone_database_ui.py ほか） | 残す | A3 | データ探索の操作へ。IDは残す。 |
| `drop-table-btn` | 3ファイル / 25回（test_workbench_drop_database_ui.py ほか） | 残す | A3 | データ探索の操作へ。IDは残す。 |
| `export-table-btn` | 3ファイル / 5回（test_workbench_dataops_ui.py ほか） | 残す | A3 | データ探索の「テーブルの管理」へ移す。IDは残す。 |
| `export-table-format` | 2ファイル / 3回（test_workbench_drop_database_ui.py ほか） | 残す | A3 | データ探索の「テーブルの管理」へ移す。IDは残す。 |
| `inspect-lineage` | 1ファイル / 3回（test_workbench_lineage_database_ui.py） | 残す | A3 | データ探索の一覧・詳細へ。IDは残す。 |
| `inspect-profile` | 2ファイル / 3回（test_workbench_profile_database_ui.py ほか） | 残す | A3 | データ探索の一覧・詳細へ。IDは残す。 |
| `inspector-tabs` | 1ファイル / 1回（test_workbench_database_ui.py） | 残す | A3 | データ探索の一覧・詳細へ。IDは残す。 |
| `lineage` | 1ファイル / 2回（test_workbench_lineage_database_ui.py） | 変える | A7 | 独立したdbtリネージ面板はDatabaseモードで使わない。テーブル詳細のリネージタブへ移し、旧UIはA7で撤去（決定6）。 |
| `open-table-sql` | 3ファイル / 18回（test_workbench_database_ui.py ほか） | 残す | A3 | データ探索の操作へ。IDは残す。 |
| `refresh-catalog` | 1ファイル / 2回（test_workbench_database_ui.py） | 残す | A3 | データ探索の操作へ。IDは残す。 |
| `refresh-table-details` | 1ファイル / 4回（test_catalog_ui.py） | 残す | A3 | データ探索の操作へ。IDは残す。 |
| `snapshot-preview` | 2ファイル / 6回（test_catalog_ui.py ほか） | 残す | A3 | データ探索の操作へ。IDは残す。 |
| `table-columns` | 2ファイル / 29回（test_catalog_ui.py ほか） | 残す | A3 | スキーマタブのカラム定義。IDは残す。 |
| `table-lineage` | 2ファイル / 7回（test_workbench_lineage_database_ui.py ほか） | 残す | A3 | データ探索の一覧・詳細へ。IDは残す。 |
| `table-preview` | 2ファイル / 5回（test_workbench_database_ui.py ほか） | 残す | A3 | データ探索の一覧・詳細へ。IDは残す。 |
| `table-snapshots` | 1ファイル / 4回（test_catalog_ui.py） | 残す | A3 | データ探索の一覧・詳細へ。IDは残す。 |
| `table-title` | 2ファイル / 11回（test_workbench_explorer_compute_layout_ui.py ほか） | 残す | A3 | データ探索の一覧・詳細へ。IDは残す。 |

#### 取り込み・ステージ

| ID | 参照（ファイル数 / 回数） | 新UIで残す・変える | WP | 備考 |
| --- | --- | --- | --- | --- |
| `ingestion-view-stages` | 1ファイル / 1回（test_workbench_stages_database_ui.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `run-ingest` | 2ファイル / 2回（test_workbench.py ほか） | 残す | A4 | 取り込み・ステージの確定操作へ移す。影響1行を直前に出す。 |
| `stage-append-notice` | 1ファイル / 1回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-confirmation` | 1ファイル / 1回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-directory` | 1ファイル / 1回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-export-area` | 1ファイル / 3回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-export-directory` | 1ファイル / 5回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-export-file` | 1ファイル / 3回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-export-form` | 1ファイル / 4回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-export-format` | 1ファイル / 2回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-export-submit` | 1ファイル / 1回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-export-table` | 1ファイル / 4回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-exports` | 1ファイル / 6回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-exports-refresh` | 1ファイル / 3回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-file` | 1ファイル / 10回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-files` | 1ファイル / 2回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-files-refresh` | 1ファイル / 1回（test_workbench_refresh.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-form` | 2ファイル / 6回（test_workbench.py ほか） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-import` | 1ファイル / 14回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-key` | 1ファイル / 1回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-legacy-notice` | 1ファイル / 1回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-mode` | 1ファイル / 2回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-preview` | 1ファイル / 3回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-preview-button` | 1ファイル / 2回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-retry` | 1ファイル / 4回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-scan` | 1ファイル / 6回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-schedule-enabled` | 1ファイル / 9回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-schedule-form` | 1ファイル / 7回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-schedule-kind` | 1ファイル / 1回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-schedule-minutes` | 1ファイル / 6回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-schedule-save` | 1ファイル / 1回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-schedule-state` | 1ファイル / 1回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-schedule-stop` | 1ファイル / 6回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-schedule-time` | 1ファイル / 1回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-schedule-timezone` | 1ファイル / 1回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-schedule-weekday-1` | 1ファイル / 1回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-schedule-weekday-2` | 1ファイル / 1回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-table` | 2ファイル / 6回（test_workbench.py ほか） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stage-view-run` | 1ファイル / 7回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stages-area` | 1ファイル / 2回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |
| `stages-refresh` | 1ファイル / 6回（test_workbench.py） | 残す | A4 | 取り込み・ステージへ。IDは残す。 |

#### データ変換

| ID | 参照（ファイル数 / 回数） | 新UIで残す・変える | WP | 備考 |
| --- | --- | --- | --- | --- |
| `mart-form` | 1ファイル / 5回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-min-rows` | 1ファイル / 4回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-name` | 1ファイル / 1回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-new` | 1ファイル / 2回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-not-null` | 1ファイル / 1回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-run` | 1ファイル / 9回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-save` | 1ファイル / 3回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-schedule-day` | 1ファイル / 4回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-schedule-enabled` | 1ファイル / 6回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-schedule-form` | 1ファイル / 2回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-schedule-help` | 1ファイル / 1回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-schedule-kind` | 1ファイル / 9回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-schedule-minutes` | 1ファイル / 3回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-schedule-save` | 1ファイル / 2回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-schedule-state` | 1ファイル / 1回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-schedule-stop` | 1ファイル / 2回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-schedule-time` | 1ファイル / 3回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-sql` | 1ファイル / 5回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-state` | 1ファイル / 1回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-view-run` | 1ファイル / 1回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `mart-watch-tables` | 1ファイル / 1回（test_marts_ui.py） | 残す | A4 | データ変換の「公開済みMARTS」→「更新のレビューと保存」へ。IDは残す（決定3）。 |
| `marts-admin` | 1ファイル / 1回（test_marts_ui.py） | 変える | A4 | 登録済み更新対象の独立面板を、データ変換の「公開済みMARTS」→「更新のレビューと保存」へ統合する（決定3）。 |
| `marts-list` | 1ファイル / 1回（test_marts_ui.py） | 変える | A4 | 登録済み更新対象の独立面板を、データ変換の「公開済みMARTS」→「更新のレビューと保存」へ統合する（決定3）。 |
| `run-dbt-build` | 1ファイル / 2回（test_workbench_dataops_ui.py） | 残す | A4 | データ変換のモデル画面へ。IDは残す。 |
| `run-dbt-test` | 1ファイル / 2回（test_workbench_dataops_ui.py） | 残す | A4 | データ変換のモデル画面へ。IDは残す。 |
| `transformation` | 2ファイル / 2回（test_workbench_database_ui.py ほか） | 残す | A4 | データ変換のモデル画面へ。IDは残す。 |
| `transformation-marts` | 1ファイル / 4回（test_workbench_ai_wap_database_ui.py） | 残す | A4 | データ変換のモデル画面へ。IDは残す。 |
| `transformation-models` | 3ファイル / 4回（test_workbench_ai_wap_database_ui.py ほか） | 残す | A4 | データ変換のモデル画面へ。IDは残す。 |
| `transformation-view-marts` | 2ファイル / 3回（test_workbench_dataops_ui.py ほか） | 変える | A4 | 独立タブをやめ、「公開済みMARTS」→「更新のレビューと保存」に統合する（決定3）。 |
| `transformation-view-models` | 1ファイル / 3回（test_workbench_ai_wap_database_ui.py） | 残す | A4 | データ変換のモデル画面へ。IDは残す。 |
| `transformation-view-wap` | 3ファイル / 4回（test_workbench_ai_wap_database_ui.py ほか） | 変える | A4 | 独立タブをやめ、データ変換の1本の流れに統合する。 |
| `transformation-wap` | 3ファイル / 7回（test_workbench_ai_wap_database_ui.py ほか） | 残す | A4 | データ変換のモデル画面へ。IDは残す。 |

#### ジョブ

| ID | 参照（ファイル数 / 回数） | 新UIで残す・変える | WP | 備考 |
| --- | --- | --- | --- | --- |
| `job-alerts` | 1ファイル / 2回（test_workbench_refresh.py） | 変える | A6 | 上部バーのベル＋ポップオーバーへ移す（決定4）。 |
| `job-alerts-error` | 1ファイル / 1回（test_workbench_refresh.py） | 変える | A6 | 上部バーのポップオーバーのエラー表示へ移す（決定4）。 |
| `job-alerts-refresh` | 1ファイル / 1回（test_jobs_ui.py） | 変える | A6 | 上部バーのポップオーバーとジョブ画面の通知を更新する（決定4）。 |
| `job-alerts-timezone` | 1ファイル / 2回（test_workbench_refresh.py） | 変える | A6 | ポップオーバーとジョブ画面の通知時刻をJSTに統一する（決定4）。 |
| `job-description` | 1ファイル / 2回（test_workbench_refresh.py） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-detail-state` | 2ファイル / 8回（test_jobs_ui.py ほか） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-form` | 1ファイル / 4回（test_workbench_refresh.py） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-name` | 1ファイル / 8回（test_workbench_refresh.py） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-parameter-example` | 1ファイル / 1回（test_workbench.py） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-parameters` | 1ファイル / 4回（test_workbench_refresh.py） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-run` | 1ファイル / 10回（test_jobs_ui.py） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-run-arguments` | 2ファイル / 3回（test_workbench_refresh.py ほか） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-run-detail` | 2ファイル / 8回（test_jobs_ui.py ほか） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-run-form` | 2ファイル / 4回（test_jobs_ui.py ほか） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-run-help` | 1ファイル / 1回（test_jobs_ui.py） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-run-open` | 2ファイル / 20回（test_jobs_ui.py ほか） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-run-state` | 1ファイル / 6回（test_jobs_ui.py） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-runs` | 2ファイル / 10回（test_jobs_ui.py ほか） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-runs-filter` | 2ファイル / 9回（test_jobs_ui.py ほか） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-save-state` | 1ファイル / 2回（test_workbench_refresh.py） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-schedule-day` | 2ファイル / 4回（test_jobs_ui.py ほか） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-schedule-enabled` | 3ファイル / 18回（test_jobs_ui.py ほか） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-schedule-kind` | 2ファイル / 5回（test_workbench.py ほか） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-schedule-message` | 1ファイル / 1回（test_jobs_ui.py） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-schedule-name` | 1ファイル / 1回（test_jobs_ui.py） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-schedule-save` | 1ファイル / 2回（test_workbench.py） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-schedule-state` | 1ファイル / 1回（test_jobs_ui.py） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-schedule-stop` | 1ファイル / 1回（test_jobs_ui.py） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-schedule-time` | 2ファイル / 4回（test_jobs_ui.py ほか） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-schedule-timezone` | 2ファイル / 3回（test_workbench_refresh.py ほか） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-schedule-weekday-2` | 1ファイル / 1回（test_workbench.py） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-schedule-weekday-5` | 1ファイル / 1回（test_workbench.py） | 残す | A6 | ジョブへ。IDは残す。 |
| `job-steps` | 1ファイル / 3回（test_workbench_refresh.py） | 残す | A6 | ジョブへ。IDは残す。 |
| `jobs` | 3ファイル / 3回（test_workbench_database_ui.py ほか） | 残す | A6 | ジョブへ。IDは残す。 |
| `jobs-alert-tab-count` | 1ファイル / 3回（test_workbench_refresh.py） | 変える | A6 | 上部バーのベル＋ポップオーバーの未読件数へ移す（決定4）。 |
| `jobs-error` | 1ファイル / 2回（test_workbench_refresh.py） | 残す | A6 | ジョブへ。IDは残す。 |
| `jobs-list` | 2ファイル / 3回（test_workbench_refresh.py ほか） | 残す | A6 | ジョブへ。IDは残す。 |

#### AI Bears

| ID | 参照（ファイル数 / 回数） | 新UIで残す・変える | WP | 備考 |
| --- | --- | --- | --- | --- |
| `bears` | 7ファイル / 16回（test_workbench_sre_policy_database_ui.py ほか） | 残す | A6 | AI Bearsのルートコンテナとして残す。 |

#### 監査ログ

| ID | 参照（ファイル数 / 回数） | 新UIで残す・変える | WP | 備考 |
| --- | --- | --- | --- | --- |
| `history-error` | 1ファイル / 3回（test_workbench_refresh.py） | 変える | A7 | Atlasの監査ログは db-audit の sources を使う。旧履歴UIはA7で撤去（決定8）。 |
| `load-history-results` | 1ファイル / 5回（test_workbench_refresh.py） | 変える | A7 | Atlasの監査ログは db-audit の sources を使う。旧履歴UIはA7で撤去（決定8）。 |
| `pawprint-error` | 1ファイル / 4回（test_workbench_refresh.py） | 変える | A7 | Atlasの監査ログは db-audit の sources を使う。旧Pawprints UIはA7で撤去（決定8）。 |
| `pawprint-results` | 1ファイル / 1回（test_workbench_refresh.py） | 変える | A7 | Atlasの監査ログは db-audit の sources を使う。旧Pawprints UIはA7で撤去（決定8）。 |
| `pawprints` | 1ファイル / 1回（test_workbench_navigation_ui.py） | 残す | A6 | 監査ログのルートコンテナとして残し、中身を db-audit の sources に置き換える（決定8）。 |
| `refresh-history` | 2ファイル / 4回（test_workbench.py ほか） | 変える | A7 | db-audit の sources に統合する。旧履歴の更新UIはA7で撤去（決定8）。 |
| `refresh-pawprints` | 2ファイル / 3回（test_workbench_refresh.py ほか） | 変える | A7 | db-audit の sources に統合する。旧Pawprintsの更新UIはA7で撤去（決定8）。 |
| `runtime-queries-results` | 1ファイル / 5回（test_workbench_refresh.py） | 変える | A7 | Atlasの監査ログは db-audit の sources を使う。旧履歴UIはA7で撤去（決定8）。 |

#### コンピュート & ガバナンス

| ID | 参照（ファイル数 / 回数） | 新UIで残す・変える | WP | 備考 |
| --- | --- | --- | --- | --- |
| `auto-chill-minutes` | 3ファイル / 4回（test_workbench_warehouse_runtime_ui.py ほか） | 残す | A6 | コンピュート & ガバナンスへ。IDは残す。 |
| `auto-chill-state` | 1ファイル / 1回（test_workbench_warehouse_runtime_ui.py） | 残す | A6 | コンピュート & ガバナンスへ。IDは残す。 |
| `compute` | 9ファイル / 27回（test_workbench_compute_activity_database_ui.py ほか） | 残す | A6 | コンピュート & ガバナンスへ。IDは残す。 |
| `compute-components` | 1ファイル / 1回（test_workbench_warehouse_runtime_ui.py） | 残す | A6 | コンピュート & ガバナンスへ。IDは残す。 |
| `compute-legacy-auto-chill` | 1ファイル / 1回（test_workbench_warehouse_runtime_ui.py） | 変える | A6 | 自動休止を「コンピュート & ガバナンス」のポリシー欄へ移す。 |
| `compute-legacy-overview` | 1ファイル / 1回（test_workbench_warehouse_runtime_ui.py） | 変える | A6 | 旧コンピュート画面の見出しをAtlasの構成に置き換える。 |
| `compute-legacy-status` | 1ファイル / 1回（test_workbench_warehouse_runtime_ui.py） | 変える | A6 | 旧状態面板をAtlasの「稼働状態の要約」に置き換える。 |
| `compute-policy-check` | 2ファイル / 4回（test_workbench_warehouse_runtime_ui.py ほか） | 残す | A6 | コンピュート & ガバナンスへ。IDは残す。 |
| `compute-refresh` | 1ファイル / 2回（test_workbench_warehouse_runtime_ui.py） | 残す | A6 | コンピュート & ガバナンスへ。IDは残す。 |
| `compute-sre-check` | 2ファイル / 4回（test_workbench_warehouse_runtime_ui.py ほか） | 残す | A6 | コンピュート & ガバナンスへ。IDは残す。 |
| `compute-summary` | 1ファイル / 1回（test_workbench_warehouse_runtime_ui.py） | 残す | A6 | コンピュート & ガバナンスへ。IDは残す。 |
| `compute-warehouse` | 2ファイル / 4回（test_workbench_refresh.py ほか） | 残す | A6 | コンピュート & ガバナンスへ。IDは残す。 |
| `save-auto-chill` | 2ファイル / 2回（test_workbench_refresh.py ほか） | 残す | A6 | コンピュート & ガバナンスの自動休止へ移す。 |

#### データベース管理

| ID | 参照（ファイル数 / 回数） | 新UIで残す・変える | WP | 備考 |
| --- | --- | --- | --- | --- |
| `db-admin` | 3ファイル / 6回（test_workbench_admin_ui.py ほか） | 残す | A5 | データベース管理へ。IDは残す。 |
| `db-admin-content` | 3ファイル / 5回（test_workbench_admin_ui.py ほか） | 残す | A5 | データベース管理へ。IDは残す。 |
| `db-admin-error` | 3ファイル / 4回（test_workbench_retirement_ui.py ほか） | 残す | A5 | データベース管理へ。IDは残す。 |
### 3.2 動的に生成されるIDのfamily

動的IDは実行時のDB ID・モデルID・実行IDを含むため、1行ずつ固定しない。familyごとに
残す・変えるを決め、値は各WPの生成関数で維持する。件数は代表例。

| IDのfamily（例） | 参照の例 | 新UIで残す・変える | WP | 備考 |
| --- | --- | --- | --- | --- |
| `p35-wap-*` | `p35-wap-sql` 91回 / 22ファイル、`p35-wap-model-id` 15回 / 2ファイル | 残す | A4 | 手動WAPのSQLとビルド。 |
| `bear-query-*` | `bear-query-sql` 75回 / 16ファイル、`bear-query-run` 20回 / 1ファイル | 変える | A6 | AI BearsのQuery Doctorへ移す。 |
| `p35-ingest-*` | `p35-ingest-confirm` 64回 / 1ファイル、`p35-ingest-name` 26回 / 4ファイル | 残す | A4 | 影響行数を追加（要API2）。 |
| `explanation-*` | `explanation-latest` 63回 / 3ファイル、`explanation-copilot-plan` 33回 / 2ファイル | 変える | A6 | AI Bearsの説明・提案へ移す。 |
| `runtime-*` | `runtime-minutes` 58回 / 9ファイル、`runtime-wake` 29回 / 6ファイル | 残す | A6 | コンピュートの状態・自動休止。 |
| `db-audit-*` | `db-audit-source` 35回 / 5ファイル、`db-audit-search` 24回 / 5ファイル | 残す | A6 | sources を統合し、全利用者のクエリ・ロード履歴の置き場所にする（決定8）。発生元の表示名は日本語化。 |
| `db-creation-*` | `db-creation-process` 35回 / 1ファイル、`db-creation-next` 33回 / 1ファイル | 残す | A5 | 作成ウィザード。必須の担当チームを追加（決定5）。 |
| `db-jobs-*` | `db-jobs-description` 28回 / 2ファイル、`db-jobs-name` 24回 / 1ファイル | 残す | A6 | ジョブ定義・実行。 |
| `db-stages-*` | `db-stages-schema` 20回 / 1ファイル、`db-stages-options` 17回 / 1ファイル | 残す | A4 | 保存済みステージ。 |
| `db-marts-*` | `db-marts-list` 20回 / 1ファイル | 残す | A4 | 公開済みマート。 |
| `db-dbt-*` | 複数 | 残す | A4 | dbtの計画・実行・公開。 |
| `db-clone-*` / `db-drop-*` | `db-clone-name` 18回 / 3ファイル、`db-drop-submit` 18回 / 1ファイル | 残す | A3 | テーブルの管理。 |
| `table-profile-*` | `table-profile-snapshot` 18回 / 1ファイル | 残す | A3 | プロファイル。 |
| `curator-*` / `proposal-*` | `curator-output` 21回 / 1ファイル、`proposal-output` 21回 / 2ファイル | 変える | A6 | AI Bearsの提案へ移す。 |
| `runtime-*` / `observation-*` | `runtime-minutes` 58回 / 9ファイル、`runtime-wake` 29回 / 6ファイル、`observation-compute-auth` 10回 / 1ファイル | 残す | A6 | コンピュートの状態・操作・運用診断。 |
| `observation-usage-*` / `observation-capacity-*` / `observation-resources-*` | 複数 | 残す | A6 | 使用量・容量・リソース。 |
| `observation-history-*` / `observation-activity-*` | 複数 | 残す | A6 | ライフサイクル履歴・アクティビティ。 |
| `worksheet-{uuid}` | 動的 | 残す | A3 | ワークシートのタブ。 |
| `job-arg-*` / `job-step-*` | 動的 | 残す | A6 | ジョブの引数・ステップ。 |

### 3.3 抽出コマンド

次のコマンドをリポジトリ直下で実行した。`index.html` に静的に存在するIDだけを表3.1に残し、
それ以外は動的IDまたはテスト用のスタブとして除外した。`querySelector` は `#id` 形式だけを対象にした。

```bash
python3 - <<'PY'
import re, pathlib, collections
root = pathlib.Path('.')
html = (root / 'tools/local_experiment/workbench/index.html').read_text()
static = set(re.findall(r'\bid="([A-Za-z0-9_:.-]+)"', html))
patterns = [
    re.compile(r"\$\(\s*['\"]([A-Za-z0-9_:.-]+)['\"]\s*\)"),
    re.compile(r"getElementById\(\s*['\"]([A-Za-z0-9_:.-]+)['\"]\s*\)"),
    re.compile(r"querySelector(?:All)?\(\s*['\"]#([A-Za-z0-9_:.-]+)['\"]"),
    re.compile(r"\bbyId\(\s*['\"]([A-Za-z0-9_:.-]+)['\"]"),
]
refs = collections.defaultdict(collections.Counter)
for path in sorted((root / 'tests').glob('*.py')):
    text = path.read_text()
    for pattern in patterns:
        for match in pattern.finditer(text):
            refs[match.group(1)][path.name] += 1
for ident in sorted(set(refs) & static):
    files = refs[ident]
    print(ident, len(files), sum(files.values()), dict(files))
PY
```

## 4. 要API 3件

### 4.1 DBをまたいだ検索（UX-A3）

**現行。** Databaseモードのデータ探索は、選択した1スキーマの中だけを検索する。

- UIは `/api/v1/schemas/{schema_id}/objects?q=&limit=&cursor=` を呼ぶ（`index.html:6457-6467`）。
- サーバーは `workbench_server.py:208-214` で受け、`database_inventory.list_objects` に渡す。
- `list_objects` は `schema_id` を必須にし、`_schema`（`database_inventory.py:75-83`）で
  その1スキーマの参照権限を確認する。オブジェクト一覧は `catalog_api(database, binding)` で
  DBごとに取得する（`database_inventory.py:344-380`、特に352）。
- `q` はオブジェクト名の部分一致だけを見る（`database_inventory.py:364`）。説明文は検索しない。
- DB一覧 `list_databases`（`database_inventory.py:86-103`）とスキーマ一覧 `list_schemas`（118-136）はある。
- 旧来の `/api/catalog` は `workbench_server.py:513-524` から `bears_resident.catalog` を呼ぶ
  単一カタログ（`iceberg`）の経路で、DBをまたぐ検索ではない。Databaseモードでは
  `FEATURE_UNAVAILABLE` になる（`bears_resident.py:397-398`）。

**足りないもの。**

- スキーマIDを指定せず、権限のある複数DB・複数スキーマを1回で検索するAPIがない。
- 複数DBの結果をまたぐ安定したカーソル（scope/digest/offset）がない。クライアントで
  N回に分けると、途中の権限変更・部分失敗・重複の扱いが難しくなる。
- 説明文検索は現行の一覧APIにない。必要ならメタデータ取得を追加する（未確認：カタログAPIの
  説明文を一覧で返せるか。現行コードは名前だけを `q` で絞る）。
- 利用終了したDBと参照権限のないスキーマを候補から外す判定を、検索API側でも一貫して行う必要がある。

**実装案。** `db_inventory_routes.dispatch` に `GET /api/v1/objects?q=&kind=&limit=&cursor=` を追加し、
`list_databases` → `list_schemas` → `list_objects` の内部処理を、権限のある範囲だけ結合する。
カーソルには検索条件・DB/スキーマ集合・結果ダイジェストを含め、変更時は `STALE_CURSOR` を返す。
`_permissions` の browse 判定を再利用し、DBをまたいでも1オブジェクトも漏らさない。

Atlasのデータ探索は `/api/v1/...` のDatabaseモードAPIだけを使う。Databaseモード以外では
現行の「Databaseモードの利用条件と案内」を表示し、旧 `/api/catalog` のUIは使わない。
サーバー側の旧APIは今回の計画では削除しない（決定6）。

### 4.2 取り込みの影響行数（UX-A4）

**現行。** 取り込みプレビューは入力ファイルの行数だけを返し、取込先の既存行数・更新行数・追加行数を返さない。

- Databaseモードのプレビューは `manual_ingest.input_preview`（`manual_ingest.py:205-218`）。
  `total_rows=len(rows)` は入力行数で、`columns` と `rows`（最大50行のサンプル）を返す。
- APIは `dataops_routes.py:268-281` の `POST /api/v1/ingests/preview` で、`preview` 封筒に
  その戻り値を入れる。
- 旧来の `/api/lake/preview` は `lake_ingest.preview`（`lake_ingest.py:278-284`）で、
  これもファイルの `row_count` だけを返す。
- 実行後の受領情報には `accepted_rows`・`committed_rows` がある（`manual_ingest.py:317`、
  354、367）。しかしプレビュー時点では取得できない。
- 書込みは `dataops_service.ingest_publish`（`dataops_service.py:980-1019`）の
  CTAS / INSERT SELECT / CREATE OR REPLACE / MERGE で、MERGEの更新行数と追加行数を分けて返さない。
- `database_inventory.object_detail` の `detail='preview'` は最大50行を返すが、全行数は返さない
  （`database_inventory.py:440-491`、特に485-490）。全行数はCOUNTクエリを別に実行する必要があり、
  取り込みプレビューはそれを呼んでいない。
- 既存テーブルなら、下流の変換は `object_lineage.get_lineage`（`object_lineage.py:200` 以降）と
  `/api/v1/objects/{id}/lineage`（`db_inventory_routes.py:11-25`）で取れるが、取り込みプレビューの
  応答には含まれない。新規作成モードでは対象オブジェクトがまだない。

**足りないもの。**

- `before`（既存行数）、`incoming`（入力行数）、`after`（取込後行数）、`updated`（更新行数）、
  `inserted`（追加行数）を、モードごとに返す項目。
- 下流の変換（dbtモデル・WAPモデル）の名前と公開状態。再ビルドまで公開内容が変わらないことの表示。
- 取込先のリビジョン・行数を含むfingerprint。現行の `preview_fingerprint` は入力・mode・
  target identity・contextを含むが、取込先の行数やリビジョンを含まないため
  （`manual_ingest.py:201-202`）、プレビュー後の取込先行数変化を検出できない。
- upsertの更新行数を出すには、キー一致の件数を取込先で数える必要がある。MERGEの実行結果だけでは
  更新と追加を分けられない。

**実装案。** `input_preview` の応答に `impact` と `downstream` を追加する。`before` は権限のある
取込先メタデータ／COUNT、`updated` はupsertキーの一致件数、`after` はモードごとの計算で出す。
取得できない値は `null` と理由を返し、UIは確定を無効にする。fingerprintに取込先のリビジョンと
行数を含め、`expected_preview_fingerprint` で再確認する。

### 4.3 DB作成時の担当チーム（UX-A5）

**現行。** DB作成の入力・保存・表示に「担当チーム」がない。

- 作成意図（論理作成）の受理フィールドは `sql_catalog`・`display_name`・`description`・
  `initial_grants` の4つだけ（`database_creations.py:145-156`）。APIクライアントの検証も同じ
  （`database_creation_client.py:273-275`）。
- `databases` テーブルは `id`・`sql_catalog`・`display_name`・`description`・`managed_by`・
  `state`・`revision` を持つ（`control_store.py:256-259`）。`team` はない。
- `data_management.create_database`（`data_management.py:217-220`）は `managed_by` を保存するが、
  これは `catalog_admin.py:111` で `'user'` 固定であり、担当チームではない。
- 作成詳細の応答は `database_creations.py:521-524` で、`sql_catalog`・`display_name`・
  `description`・`requester_id`・`revision`・`lifecycle`・`initial_grants`・`plans` を返す。`team` はない。
- 表示情報の更新は `display_management.validate_payload`（`display_management.py:12-34`）が
  `display_name` と `description` だけを受け付ける。`team` を後から更新する経路もない。
- 作成画面の入力は `index.html:11398` で、送信は `11427`・`11440`。`sql_catalog`・
  `display_name`・`description`・`initial_grants` だけを送る。
- 低レベルの `POST /api/v1/databases` は `catalog_admin.create_database`（`catalog_admin.py:68-101`）で、
  `sql_catalog` と `profile_id` だけを受け付ける（76-78）。こちらにもチームはない。
  決定7により、この経路は変更しない。

**足りないもの。**

- `databases` の `team` 列（既存DBは「未設定」）。
- 作成意図の受理・検証・保存、作成詳細と一覧の表示、監査記録への追加。
- 自由入力の検証（前後空白除去、1〜64文字、制御文字不可）と、既存DBのチーム値を候補にする datalist。
  現行の `request_auth` やアカウント情報にチームの定義はない（未確認：別の構成ファイルに
  チームがあるか。リポジトリ内の検索では見つからない）。
- 作成確認画面とDB詳細の「担当チーム」表示。既存DBは「未設定」と表示し、表示情報の編集で
  後から設定できるようにする。

**実装案。** チームは必須の自由入力とし、前後空白を除去して1〜64文字、制御文字不可で検証する。
候補は既存DBのチーム値を datalist に出し、チームのマスタ表は作らない。既存DBは「未設定」と表示し、
表示情報の編集で後から設定できる。`databases.team` を追加し、`database_creations._intent`・
`_create_database`・詳細応答・`display_management` の表示情報に `team` を通す。
作成後の display_name・description・grants の適用は `database_creations.py:306-316` で行っているため、
team の保存も同じ箇所に置く。担当チームは論理作成（`database_creations`）にだけ追加し、
低レベル `POST /api/v1/databases` は変更しない（決定5・7）。`managed_by` をチームの代用にしない。

## 5. 休止を妨げる処理の判定（UX-A6）

**結論。** 現行サーバーは、休止を妨げる処理を `warehouse_admission.blockers()` で判定する。
`execution_closure` が付いたクエリ（`FENCED_READ_ONLY`・`FENCED_STAGE_CLEANUP`）と、
終端状態（`FINISHED`・`FAILED`・`CANCELED`）の処理は除外する。したがって
「照合済みの処理は休止を妨げない」は、サーバーが照合の証拠として `execution_closure` を
記録した場合、または状態が終端になった場合に一致する。画面の「確認済み」「照合記録あり」だけでは
一致しない。

**判定箇所。**

1. `warehouse_resident.check_lifecycle`（`warehouse_resident.py:164-184`）が
   `admission.blockers(db, warehouse_id)` を確認し、1件でもあれば
   `AdmissionConflict('warehouse has unresolved work')` を投げる（178-179）。
   論理構成では `warehouse_lifecycle.runtime_blockers`（181-182）と
   `logical_creations.compute_blockers`（183-184）も見る。
2. `warehouse_lifecycle.chill`（`warehouse_lifecycle.py:411-471`）は 431-435 で
   `wr.check_lifecycle(db=db)` を呼ぶ。加えて 448-450 でTrinoの実行中クエリ数を確認し、
   0でなければ `WAREHOUSE_BUSY` 409 を返す。
3. `warehouse_admission.blockers`（`warehouse_admission.py:248-252`）は
   `warehouse_admissions` の `state IN ('ACTIVE','UNKNOWN')` を対象にし、
   `kind='query' AND execution_closure IN ('FENCED_READ_ONLY','FENCED_STAGE_CLEANUP')` を除外する。
4. `warehouse_admission.execution_unresolved`（`warehouse_admission.py:48-50`）も同じ除外をする。
5. `warehouse_lifecycle.blockers`（`warehouse_lifecycle.py:348-353`）は
   `runtime_operations`・`admissions`・`unknown`・`creation` の件数を返す。
   `/api/v1/runtime` の応答はこの件数を `blockers` として返す（`warehouse_runtime_routes.py:103-131`）。
6. `execution_closure` は `execution_recovery.py:1339-1345` の `_close` で
   `warehouse_admissions` に記録される。Ingestの停止は `ingest_execution_recovery.py:61-63` の
   `FENCED_STAGE_CLEANUP`。通常のクエリ照合は `query_service.reconcile_query`
   （`query_service.py:1359-1438`）が、証拠が得られた場合に状態を終端へ更新する。
   証拠が得られない場合は `UNKNOWN` のまま残り、妨げが続く。

**DB利用終了との関係。** `database_retirement._inventory`（`database_retirement.py:190-431`）も
同じ考え方で、`_TERMINAL`（70-71）と `_closed_query_admission`（74-187、338-340、389）により、
閉鎖済みのクエリを `blocked=False` にして妨げから外す。`add` の 200-208 は、明示的な
`blocked=False` がない非終端だけを妨げにする。利用終了の開始・照合は
`preview`（447-456）・`request_retire`（459-486）・`reconcile`（496-522）が担い、
`reconcile` は `blockers` が空のときだけ `RETIRED` へ進む（511-516）。

**ずれやすい点。**

- `/api/v1/runtime` の `blockers` は件数の要約で、影響を受けるジョブ・ワークシートの名前を返さない。
  UX-A6が「影響を受けるジョブ・ワークシート」を出すには、`blockers` の安全な投影
  （kind・id・stateだけ。SQL・入力・結果は含めない）を追加するか、既存のジョブ・実行APIと
  突き合わせる必要がある（未確認：現在のジョブAPIだけでadmission IDと一致できるか）。
- `check_lifecycle` は `ACTIVE` のadmissionをすべて妨げにする。ジョブがアイドルでも、
  admissionがACTIVEなら妨げになる。これは「処理単位」で止める設計であり、UIは
  「実行中のSQL」と断定せず「実行中の処理」と表示する。
- 状態を取得した後にTrinoのクエリが始まると、`blockers` が空でも `WAREHOUSE_BUSY` になり得る。
  UIはエラーを受けたら状態を再取得し、件数表示を信用しきらない。
- ライフサイクル履歴の `RECONCILED` は「保存した観測」であり、`execution_closure` を
  記録しない限り妨げは残る。現行の翻訳も
  「照合済みは保存時点の観測であり、元操作の成功や現在の健全性ではありません」
  （`index.html:4959`）としている。UIの「照合済み」表示を停止可否の根拠にしない。

## 6. 決定事項（2026-11-18）

| # | 項目 | 決定 | 理由 |
| --- | --- | --- | --- |
| 1 | AI設定の入口と本体 | 設定本体は「コンピュート & ガバナンス」。ユーザーメニューには本体へのリンクだけを置く。 | A0とA1の記述を、設定の実体を1か所に保つ形で統合する。 |
| 2 | SQL結果のTSVコピー | 残す。結果の保存（CSV/JSON/TSV）の隣の副操作。`copy-tsv` のIDは維持。 | 既存テストとコピー操作を保ち、見本の保存群を置き換えない。 |
| 3 | 更新対象（Managed Marts） | 独立タブをやめ、データ変換の「公開済み MARTS」→「更新のレビューと保存」に統合（見本の構成）。担当 UX-A4。 | 更新対象の編集を公開済みMARTSの詳細に集約し、見本の流れに合わせる。 |
| 4 | 通知 | 上部バーにベル＋ポップオーバー（未読件数、最新のアラート、ジョブ画面のアラートへのリンク）。担当 UX-A1（枠）、UX-A6（中身）。 | 見本に一覧面がないため、上部バーに小さなポップオーバーを置き、詳細はジョブ画面に残す。 |
| 5 | 担当チーム | 必須の自由入力（前後空白除去、1〜64文字、制御文字不可）。候補として既存DBのチーム値を datalist で出す。既存DBは「未設定」と表示し、表示情報の編集で後から設定できる。チームのマスタ表は作らない。 | マスタ表を持たず、既存値のdatalistで入力揺れを抑えつつ、既存DBを後から設定できる。 |
| 6 | Legacy `/api/catalog` | Atlasの画面はDatabaseモードのAPIだけで作る。Databaseモード以外では現行の「Databaseモードの利用条件と案内」を表示する。サーバー側の旧APIは今回の計画では削除しない（A7では未使用のUIコードだけ撤去）。 | 画面を1つのAPI系に固定し、サーバーAPIの削除をA7のUI撤去と分離する。 |
| 7 | DB作成API | 担当チームは論理作成（`database_creations` の作成要求）にだけ追加する。低レベル `POST /api/v1/databases` は変更しない。 | Atlasの作成導線を論理作成に固定し、低レベル経路の互換性を保つ。 |
| 8 | 監査ログ | `db-audit` の sources（query / dataops / job_run / stage_run 含む）をAtlasの監査ログに統合し、これを「全利用者のクエリ・ロード履歴」の置き場所とする。発生元フィルターの表示名は日本語化する。 | 既存の `db-audit` が対象sourceを持つため、新しい監査APIを増やさず、表示名だけ整える。 |
| 9 | register-spec / warehouse plan | DB管理の「管理」（管理者のみ、折りたたみ）に残す。担当 UX-A5。 | 見本の主導線を汚さず、復旧・登録の運用導線を保持する。 |

## 7. 未確認・制約

- 静的に存在しないIDは536件（762件の参照IDのうち。動的生成IDとテスト用スタブを含む）。
  すべてを1行ずつ分類せず、familyで整理した。全件は §3.3 のコマンドで再生成できる。
- `db-audit` の sources のうち、`query` がTrinoの全実行履歴か保存SQLだけかは実装時に確認する。
  監査ログの置き場所は決定8により `db-audit` の sources を正とする。
- DBをまたぐ検索で説明文を検索できるかは未確認。現行 `list_objects` は名前だけを `q` で絞る。
- 取り込みの下流変換をdbtグラフとWAPモデルのどちらから取るかは、UX-A4の実装時に
  既存のリネージ応答を確認して決める。計画書は「既存のdbtグラフ・リネージ」としている。
- 担当チームは新規列。既存DBは「未設定」で移行し、表示情報の編集で後から設定する（決定5）。
- 旧 `/api/catalog` のUIはAtlasで使わないが、サーバーAPIは残す（決定6）。
- 検証: `make verify-design-context` は exit 0（`design-context: ok documents=94 issues=10 references=42`）。
