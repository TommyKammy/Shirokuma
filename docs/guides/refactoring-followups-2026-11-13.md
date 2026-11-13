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
