# ADR-0038 順序4：既定切替と本人受入

2026-10-28。既定設定の切替・技術確認は完了。**本人は条件付き受入**と判断した。
DB作成の流れは分かりやすくなったと評価され、待ち時間の改善を継続条件として残す。
[ADR-0038](../design/07_ADR/ADR-0038_Separate_Logical_Database_Creation_from_Platform_Provisioning.md)と
[後継契約](database-logical-create-contract-2026-10-27.md)に従う。

## 切替

対象は有効な開発checkout `worktree/local-autonomous`。その既定runtime profileを
再構築済みの共有基盤C・`logical_v1`へ切り替えた。通常の入口は同checkoutでの `./bin/shirokuma`。
旧13DBは新しい既定経路の対象外とし、旧profile・空のactive marker・台帳を私有backupへ退避した。
旧台帳のUNKNOWN/承認/Job/Stage参照は変更せず、旧IDを新DBへ付け替えていない。
旧領域の物理削除は実施せず、履歴とrollback用に残した。移行adapterは追加していない。

## 切替後の技術確認

- profile指定の環境変数を外した通常CLIが新しい共有基盤を選択することを確認。
- 通常CLIで共有warehouseを0/0へ休止し、作成受付の拒否を確認。1/1への起動はengine確認まで約20.4秒。
- 通常のnative API/Workbenchを既定設定で起動し、両方で `logical_v1` の作成受付が有効。
- 18DBのregistry・認証参照を保持。1万行、別DBの同名tableの7/42、Job/Martのデータを通常SQLで読取。
- 既存READYの詳細から「新しい作成」への遷移で、読込中に見出しへfocusして停止する不具合を修正。
  回帰88件を通過し、実ブラウザでも再読込なしで空の作成フォームが表示されることを確認した。
- SQLのモード欄にある空の案内項目「モード」が選択できる不備を修正。
  既存UI回帰64件を通過。実ブラウザで同項目が選択不能となり、読取専用の選択・SQL本文・実行先が再読込後も保持されることを確認した。

証拠は `var/shirokuma/adr-0038-owner-acceptance-2026-10-28/private` に0600で保存した。
rollbackは両API停止、退避profile/markerの復元、旧環境の健康確認、API再起動の順で行う。
設定の復元でCの新DBを旧IDへ変換しない。

## 本人操作の進捗

ご本人が実際に操作し、返答された結果をこの表へ記録する。準備操作・自動検査を本人の成功へ数えない。
管理者の空フォームを表示した状態で引き継ぐ。準備時の画面は
`var/shirokuma/adr-0038-owner-acceptance-2026-10-28/owner-create-form-ready.jpg` に記録した。

| 操作 | 状態 | 記録 |
| --- | --- | --- |
| 新しいDBを1回の作成操作でREADYへ | 本人確認済み | `owner_accept_20261028` が「利用可能」、新しいSQLの導線を表示したとの返答。台帳のREADYも照合。所要時間は未計測 |
| 同じDB/rawで新しいSQL結果を取得 | 本人確認済み | 1行、`database_name=owner_accept_20261028`、`schema_name=raw` との返答。探索でもraw選択後に新しいSQLを開けた |
| 新DBの実データを書込み・読取 | 本人確認済み | `raw.accept_sample` を作成し、読取専用で `1/alpha`、`2/beta` の2行との返答 |
| Jobの保存・実行・自動完了 | 本人確認済み | 保存・実行後、「実行を照会」を押さず自動で「成功」になったとの返答 |
| reload・閲覧者制限・管理者復帰 | 本人確認済み | 再読込保持、閲覧者で管理UI非表示・元タブ12の実行無効、管理者復帰後 `owner_accept_20261028/raw` の読取専用で2行を本人確認。保存済みJobを読み込み、`accept_sample` のSELECT保持も確認。別viewerタブの権限内読取と区別済み |
| 作成導線と待ち時間の評価・本人受入 | 条件付き受入 | 本人「DB作成の流れは分かりやすくなりましたが、待ち時間は引き続き改善したい」。性能改善を継続する |

受入前の通常CREATE計測は初SQL中央値6.82秒（4回）。受入後の[性能改善記録](database-logical-create-performance-2026-10-28.md)では、
改善前6回の6.339秒、改善後8回の5.619秒を経て、継続改善後20回は中央値2.700秒・標本p95 2.759秒となった。
API再起動直後は5.220秒。p50≤2秒は未達であり、標本内のp95だけで暫定目標全体の達成とはしない。
条件付き受入と性能改善を記録し、機能確認の成功を性能目標の達成へ置き換えない。
