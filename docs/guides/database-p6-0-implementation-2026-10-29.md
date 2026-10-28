# DB-P6-0 機能一覧と画面表示の整合化

2026年10月29日。状態は**DB-P6-0実装・技術検証完了**。[後続実装計画](database-full-capability-and-compute-plan-2026-10-28.md)のDB-P6-0を対象とし、認可方式や未対応機能の実行経路を追加せず、機能一覧・利用条件・画面の説明を揃えた。

## 機能一覧と変更

[機能一覧と利用条件](database-capability-matrix-2026-10-29.md)に89行を定義した。各行は対応／未接続／別計画、DB／platform scope、許可role、入力と出力、owner・監査・回復、通常入口または後続unitを持つ。WAP metadataのDB内共有と、呼出者に固定する実行operationを区別した。Job／Stage／Martのowner・保存role・contextも分けた。

画面の案内は固定IDに結び付け、手動WAPを先頭へ挿入しても対応済み機能に旧来の利用不可表示が残らないようにした。取り込み、変換、AI、統合監査、コンピュートにはそれぞれの対応範囲と確認先を表示する。Worksheet結果保存とTable全件Exportも別機能として説明する。

対象のidentity未確認、detail未対応、role／objectのcapability不足を分け、拒否時に余分な取得を行わない。旧dbt、Bears、Copilot、AI設定、統合監査、Warehouse操作、profiling／lineage、clone／drop、Worksheet結果保存は引き続き無効とする。非同期操作後に無効状態が解除されない既存guardを保つ。

手動Ingest／WAPの利用条件は各パネル自身のrole・DB・Schema・認可状態に結び付け、操作結果やUNKNOWNの表示と分けて案内する。Worksheetの保存SQL・実行role・実行先を、別パネルやヘッダーのrole操作で変更しない。

## 検証

コード回帰・認可API・実ブラウザーはそれぞれ別の証拠として確認した。

| 検査 | 結果 |
| --- | --- |
| `test_workbench*.py` | 現checkoutのDataOps用`.venv`で470件成功、失敗・skipなし |
| `test_request_auth.py`／`test_database_inventory.py` | 20件／19件成功。上記470件とは別群 |
| DataOps／Database UI focused | 38件／67件成功。470件の内数であり合算しない |
| 独立レビュー | 機能一覧と実装を別に確認。通常refreshでreceiptを再生成する指摘を修正し、details node・open・focus・追加POSTなしを回帰検証 |
| 実ブラウザー | 日本語／英語、A／E／V、対象未選択、明示context更新、WAP／Martへの到達、reloadと既存作成詳細の直リンク、keyboard移動を確認 |
| 狭い画面 | 幅390pxで案内が折り返され、page幅も390px。viewportを元へ戻した |
| 設計と文書 | `make verify-design-context`成功（11＋3件とdesign-context検査）。変更文書のlocal link、改行、末尾空白、`git diff --check`も通過 |

最初のworker試行ではsystem Pythonの`yaml`不足、修正後のworker sandboxではテスト用HTTP bind拒否があった。依存を追加せず、既存`.venv`で通常のローカル検証を実施し、最終470件の成功を確認した。中断試行の件数は合算しない。

ブラウザーではWAPの独立したDB／role／schemaを更新すると条件案内が消え、許可された入力が有効になることを確認した。閲覧者では保存・公開と管理Martが無効で、元WorksheetのSQL・保存admin roleを保持した。旧dbtと未対応のAI／監査／Computeの操作は言語切替後も無効で、各パネルの理由と確認先が表示された。WAP／Martの作成・公開は本unitの検証で再実行していない。

実APIの読取りではA／E／Vのinventory・Schema capabilityと、旧models／Bears／Pawprints／Warehouse routeの`FEATURE_UNAVAILABLE`を確認した。未知・不許可・非READYの区別を利用者へ開示する変更はない。GETによる旧routeの観測を、旧mutationの実行検証として扱わない。

証拠は`var/shirokuma/db-p6-0-2026-10-29/private`に保管する。認証値、credential参照の内容、process環境は公開文書へ転記しない。

画面は[対応機能の案内](../../var/shirokuma/db-p6-0-2026-10-29/screenshots/transformation-final.jpg)、[閲覧者制限](../../var/shirokuma/db-p6-0-2026-10-29/screenshots/viewer-restriction.jpg)、[英語のCompute案内](../../var/shirokuma/db-p6-0-2026-10-29/screenshots/compute-en.jpg)、[390pxの表示](../../var/shirokuma/db-p6-0-2026-10-29/screenshots/transformation-390.jpg)に保存した。

## 性能baseline

| 対象 | 日付と条件 | 結果と扱い |
| --- | --- | --- |
| warm SQL | 2026-10-29、通常Workbench HTTP API、共有LOCAL稼働中、`owner_accept_20261028/raw`、admin／read-only、`SELECT 1 AS p60_baseline`、上限1000、30回 | 全30回FINISHEDかつ1行。中央値154.4165ms、nearest-rank標本p95 291.614ms、最小131.544ms、最大320.261ms |
| 通常DB作成から初SQL | [2026-10-28の保存済み計測](database-logical-create-performance-2026-10-28.md)、起動済み基盤C、初期grantなし、20回 | 中央値2.700秒、標本p95 2.759秒。暫定p50≤2秒は未達。今回はCREATEを再実行していない |
| 手動Warehouse起動 | [2026-10-28の切替記録](database-logical-create-owner-acceptance-2026-10-28.md)、運用CLI、休止からengine確認 | 約20.4秒の単発観測。今回のcold起動やp95の新規計測とはしない |
| Database Auto-resume | 現Database経路は未接続 | DB-P6-2で接続後に入口ごとのcold startを測る。上の手動起動値で代用しない |

warm SQLはHTTP認証、context解決、dispatchと返却された`next_uri`のpage取得を含み、UI描画時間や純粋な受付overheadを分離した計測ではない。30回の標本値を性能保証にしない。DB-P6-2では同条件の比較に加えてcoldと追加overheadを分ける。

計測準備の2試行は別に保存した。最初のcollectorはpage取得ではなく状態照会を繰り返したため元queryを明示取消し、1行上限の試行は返却後の既存打切り処理でCANCELEDとなった。上限1000と正しいpage取得の30回だけを上表へ使用した。実Table・grant・DB作成・Warehouse停止は行っていない。

## 後続

DB-P6-0の技術完了はDB-P6全体やM1の所有者受入を意味しない。次の実装はDB-P6-1のDatabase別監査とする。既存のDB作成待ち時間の継続条件、全必須capabilityの実装、M1の本人操作性評価はそれぞれ追跡する。
