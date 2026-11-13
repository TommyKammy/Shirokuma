# 推奨リファクタリングの後続作業

2026-11-13 JST。追加調査で挙げた候補を、依存境界と保守性の改善効果が高い順に進める。
先行したfixture改善は完了済み。各変更を小さな作業単位で実装・重点検証・レビューし、最後に変更全体を固定してMake回帰と設計整合を確認する。

| 順序 | 対象 | 完了条件 | 状態 |
|---|---|---|---|
| 1 | 入れ子fixtureの寿命 | 設定失敗時の即時解放と成功時の親への移譲、部分失敗時の資源復元を検証する | 完了: `b1dcf58c`、記録`123cc8cd` |
| 2 | Platform HTTP adapter | Platformのルート・専用reader・依存を専用moduleが所有し、AI入口と両HTTP面の互換性を保つ | 完了: `a63e0082` |
| 3 | 通常JSONの読込・応答 | 両サーバーが小さなstdlib共通部品を直接呼び、nativeがWorkbenchHandlerのcodecに依存しない | 完了: `f512c8a6` |
| 4 | Saved Lineage | 機能側が状態・取得・描画を所有し、mainはExplorer／認可との明示的な接続と操作入口を担当する | 完了: `ec1340ab` |
| 5 | Result Export | 機能側がreceipt・応答検証・busy・downloadを所有し、queryの再実行なしで既存の操作を保つ | 完了: `ec1340ab` |

## 維持する契約

- Platformのbasic／重複header検査／strictの入力差、Manifestの遅延読取と完全UTF-8 frame、LokiのOriginと接続終了、historyのraw query grammar。
- 認証・role・Origin・CORS・エラー応答の既存の所有者と順序。通常JSONの読込条件・文字コード・応答bytes・Location・401 challenge。
- Workbenchの権限再確認、context／epoch／tokenの照合、古い応答の破棄、finallyの解除、翻訳とclassic scriptの初期化順序。
- 保存済みqueryのreceiptとページ台帳を用いるExport。SQLの再実行・公開・復旧の契約は変更しない。

## 検証と完了監査

- 各作業単位の新規境界試験と既存の関連回帰。移動した処理の旧実装との比較。
- 両HTTP adapterの正常・不正入力と認可拒否。Manifestと通常JSONの相違も独立して確認する。
- Workbenchの専用UI試験・全体UI回帰と、通常画面の日英・キーボード・320/390px・深い経路からの再読込。
- 最終ソースを固定した`make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3`と`make verify-design-context`、差分レビュー、ローカルコミット。

Scheduleや復旧・認可全体の再設計は追加調査で優先度を下げたため、この作業には含めない。

## 重点検証の証拠

- Platformの12 branchとManifest readerは旧実装とASTが一致し、AI／contextの処理を保ったまま17経路の依存を移した。新規12試験でframingの差・接続終了・Origin・完全UTF-16/32 frameの拒否を固定した。
- 通常JSONの2関数は`self`を`handler`へ置き換えた旧実装とASTが一致する。新規7試験で読込条件・bytes／header順・Location・401・nativeの独立性を固定した。
- 通常環境でPlatform 261件、AI 308件、両HTTP面・bear observations・認可など95件がすべて通過した。worker sandboxのloopback制限による未実行分も、この再実行で確認した。
- LineageとExportをfactory型classic assetへ分離し、mainに明示adapterと操作入口を残した。処理順はadapter名を戻すと旧コードと一致し、状態の変更も操作APIへ集約した。
- Node harness全体を通常の関数で包み、factoryの`new Function`からアプリ変数を読めないことを検証した。隔離確認は旧harnessでは失敗する。関連138件と、通常環境のWorkbench／認可127件が通過した。

## 通常画面の確認

- 起動前と同じprofile・環境でWorkbenchだけを更新した。profileは変更せず、query／DataOps／admissionに実行中の処理がないことを確認して再起動した。
- 選択先`m1_owner_20261106_1121 / marts / admin / read-only`で既存参照SQLから3行を受領し、CSVの実ファイルと画面の値を照合した。サーバーログはquery送信1回・Export1回で、保存・履歴入力復元・画面遷移によるSQLの再実行はない。
- LineageはmartsのTABLEのWAP公開証跡とrawのTABLEのWAP構築証跡を明示更新で表示した。選択変更・閲覧者への切替で古い結果が消え、adminへ戻しても復活しないこと、矢印キーによるInspector移動とfocusを確認した。
- 両機能の日英表示、320/390pxの本文・操作・理由表示を確認し、文書幅に横溢れがないことを確認した。`#transformation`の再読込では目的のページを維持し、初回観測でSQL画面は表示されなかった。保存済みartifactの深い経路はナビゲーション20試験でも確認した。
- 最後に元の日本語・admin・SQL画面と通常viewportへ戻し、ブラウザーconsole errorは0件だった。画像・CSV・実行ログはversion管理外の検証artifactに保持した。

## 最終完了監査

- ソース`ec1340ab`を固定した全体Makeは138群・5,395件を完走し、5,361件成功・既定34件skip。Makeの変更は新規Platform試験の1行追加だけで、既存の対象と環境区分を維持した。
- 新規23試験を追加した。既存の異なる環境roleでLineage／Exportの3試験をそれぞれ選択するため、実行数の増加は26件となる。不要な重複削除や試験の省略はない。
- Platformの17経路と入力契約、通常JSONのnative独立性、両factoryの状態・取得・保存の所有者、読込・認可・表示の互換性を、コード・旧実装比較・境界試験・通常画面の証拠で照合した。
- `make verify-design-context`と差分レビューを通過。最終試験後の変更は記録のみとし、既存のローカル構成差分2件を維持した。上表の作業単位を完了する。

## 追加調査に基づく第2巡

2026-11-13 JST、追加調査で確認した4項目を次の順に実施した。UIの追加分割、Schedule、復旧・認可全体の再編は今回の範囲に含めなかった。

| 順序 | 対象 | 完了条件 | 状態 |
|---|---|---|---|
| 1 | Queryの入れ子fixture | 7か所を既存helperへ移行し、実際の呼出元で設定途中の失敗と親子の解放順を検証する | 完了: `e4be0cc` |
| 2 | OriginとExportの依存 | 共通Origin判定と既存JSON部品を利用し、routeからWorkbenchのpolicy／codecへの依存を解消する | 完了: `02f5825` |
| 3 | CLIのHTTP正常系 | 同一処理を持つMartも含む4 clientのRequest構築・正常応答読込を共通化し、例外境界・timeout・binary・不確定結果と復旧情報の契約を保つ | 完了: `4f9cb94` |
| 4 | Platformの入力検査 | 同じadapter内の同一検査をまとめ、basic／unique／strict、Manifest、history、Lokiの差と検査順を保つ | 完了: `2b02f7d` |

各単位の重点回帰・差分レビュー・ローカルコミット後、最終確認に`make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3`と`make verify-design-context`を用いた。

- Queryの全7呼出元に失敗を注入し、子の即時解放と親のpatch保持、LIFO・一度だけの解放を検証した。新規回帰は旧Query実装で7経路とも失敗し、移行後の既存44試験と寿命管理6試験が通過した。
- Origin関数のASTは移動前と一致し、各入口の空値・重複・検査順を保った。policy／Platform16試験と、両HTTP面・Export・作成・廃止・runtime・通常JSONの69試験が通過した。policy4試験は標準Pythonでも通過した。
- 4 clientの事前変換と例外分岐を変えず、共通正常応答処理へ接続した。追加6試験は旧実装でも通過し、関連88試験でtimeout・binary・短読込・復旧情報・送信結果の不確定分類を確認した。
- Platformは2つの小さなpredicateだけを追加した。展開したASTは旧実装と一致し、17経路×11入力と検査順の16試験は旧・新とも通過した。関連backend265試験は264成功・既定1 skipだった。

最終ソース`2b02f7d`の全体Makeは139群・5,412件を完走し、5,378成功・既定34 skipだった。追加16試験に対し、Exportの既存の環境別選択を保ったため実行数は17件増えた。Makeの既存対象・環境区分・順序は変わらず、Origin試験の1行だけを追加した。

設計整合と差分レビューを通過し、4項目の実装・失敗時の所有権・依存境界・互換性を現行ソースと試験で照合した。検証は一時SQLite・模擬catalog／transport・ローカルHTTP・Node harnessを用いた。最終試験後の変更は記録のみとし、既存のローカル構成差分2件を維持して第2巡を完了する。

## 追加調査に基づく第3巡

2026-11-13 JST、残る価値のある整理として次の2項目を順に実施した。

| 順序 | 対象 | 完了条件 | 状態 |
|---|---|---|---|
| 1 | LogicalRegistry系fixture | 接続取得直後にcloseを登録し、3呼出元の設定失敗を保護する。Accessは子を早期解放する寿命を維持する | 完了: `c86a838` |
| 2 | Platformのstdout読込 | 既存共通moduleへ読込・出力上限・残時間による終了待機を集約し、起動・認可・期限生成・終了処理・解析は6 module側に残す | 完了: `a44672f` |

実際のleafと全3呼出元の失敗注入、Accessの成功時の解放時点、各collectorの期限・上限・例外・認可・cleanupの契約を重点検証した。旧実装との比較とレビュー・ローカルコミット後、最終ソースで全体Makeと設計整合を確認した。

- 実SQLiteでbootstrap／BEGIN失敗時のcloseとprofile復元、親の資源保持、一度だけの解放を確認した。Accessは最初のprincipal構築前に子を解放する。追加3試験は旧4実装の後片付け漏れを検出し、関連73試験が通過した。
- 6 callerの共通readerを展開したASTは旧実装と一致する。cleanup・認可・解析の後半は同一のまま、追加4境界試験は旧処理でも通過した。関連7 suite・142試験が通過した。

最終ソース`a44672f`の全体Makeは139群・5,419件を完走し、5,385成功・既定34 skipだった。追加7試験と実行数の増加7件を照合し、既存のMake対象・環境区分・順序は変更していない。

設計整合・差分レビューと2項目の完了監査を通過した。一時SQLite・模擬process・ローカルHTTP・Node harnessで検証し、最終試験後の変更は記録のみとした。既存のローカル構成差分2件を保持し、UIの追加分割と契約の異なるUsage／Docker読込は今回の範囲に含めず、第3巡を完了する。
