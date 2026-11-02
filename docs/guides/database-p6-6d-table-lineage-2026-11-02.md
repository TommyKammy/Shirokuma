# DB-P6-6D: Table / Explorer lineage

2026-11-02。EXP-12の次に、EXP-13とEXP-14の残るExplorer導線を実装した。
以下の限定範囲を技術受入とする。DB-P6全体とM1本人受入は継続する。

## 契約

- 選択したTABLE / VIEWを起点に、保存済みWAP / dbtの直接のupstream / downstreamを読取り専用で表示する。TableのLineageタブと、Explorerの選択対象ヘッダーにある「保存済みリネージュ」から同じ表示に到達できる。
- 実行・公開の証拠がある関係と保存定義を区別する。Snapshot、VIEW版、object UUID / incarnationを照合し、過去の関係を現行版の関係として扱わない。宣言欄は版を固定しないRef間の依存であり、実行時の固定版は確認済み証跡に表示する。
- E / Aの変換metadata権限と、各endpointの現在のDB・schema・query権限を確認する。dbtはE本人・保存role、A同一DBの既存境界を保ち、dbt管理下のWAPにも親の可視性を適用する。
- 保存記録から見える範囲を表示する。空表示を完全な依存関係の不存在と解釈しない。未実行dbtモデルは論理上の行き先として区別し、planned名称から物理Tableを同定しない。clone等の別操作やSQL解析から関係を推定しない。
- GETはquery・compile・admission・operationを生成せず、未確定状態の整合処理やregistry更新も行わない。

## 検証

| 層 | 結果 |
| --- | --- |
| 重点コード検証 | backend 18、最終Lineage UI 11、Database UI 67が成功。profiling 23 / DataOps UI 38も回帰成功。readonly proofの既存呼出しは従来どおりで、lineageだけがsettle=Falseを使う |
| 実HTTP | Workbench / native両入口で40ケース成功。200×16、403×7、409×5、400×8、404×4。古いgrant revisionの拒否など先行GET 23件も別証拠に保持 |
| 版と認可 | A現行graph、B現在VIEW v2と保存v1、Eの保存owner / role、別DB・古いincarnation・不正context / method / framingを確認 |
| 不変性 | HTTP・ブラウザー後ともactive / default store 12177 / 156行・全schema、保護12filesとmodes、source 8件の全metadata一致、追加行0。UNKNOWN query 3 / admission 3 / AI 6 / dbt 1を保持。SQL・compile・更新operationは0 |
| 全体回帰 | canonical 110群を実行。旧hidden LineageパネルのUI期待値1件を修正し、同一のWorkbench wildcard群1058件を最終sourceで再実行して全成功。失敗群をこの再実行結果に置き換えた最終合算は4499成功 / 34 skip |
| 設計整合 | make verify-design-context成功（94 documents / 10 issues / 42 references）、diff --check成功。機能表89行を維持し、変更はEXP-13 / 14の2行のみ |
| 通常画面 | 可視のExplorerヘッダーからLineageを取得。A Tableは確認済み6 / 定義5、A RAW customerは13 / 32（計画中モデル20）、B VIEWは現在v2 / 記録v1の過去2関係、履歴なしTableは0件とscope案内を確認。対象切替で旧表示を消去し、元の19 Worksheet・SQL・role・contextを復元 |
| 表示 | 日英切替、390pxのLineage領域346px / scroll346px、実Chrome 200%の領域716px / scroll716pxを確認。日本語pageは390px、英語pageは別領域による405pxの横溢れが残るため、画面全体の狭幅受入とは扱わない |

証拠はローカルの`var/shirokuma/db-p6-6d-table-lineage-2026-11-02/private/`に保持した。最初のcanonical結果と失敗log、最終Workbench再実行結果は別々に保存し、`verification-final-summary.json`で対応を記録した。通常画面のSnapshot番号は文字列のまま表示し、SQLは再実行していない。

次はEXP-11のDrop導線を別単位で整理する。clone provenance、任意SQLの依存推定、推移的な全catalog graph、Catalog Curator Bearのlineage actionは今回の閉鎖対象に含めない。
