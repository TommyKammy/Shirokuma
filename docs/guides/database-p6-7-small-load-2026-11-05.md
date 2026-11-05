# DB-P6-7：M1前の小規模負荷確認（2026-11-05）

固定LOCALの通常Workbench（`127.0.0.1:8888`）と同じ公開APIで、専用の合成データDBを使って確認した。run-cで作成・投入・混合負荷・待機取消・休止復帰・全テーブル再読取りが通過した。発見した起動時の競合2件を修正し、run-a／bの失敗履歴と回復証拠も保持している。

## 範囲と実行条件

- Coordinator 1台＋Worker 1台。両方のrequestsは100m CPU／256Mi、limitsは4 CPU／4Giのまま。
- AutoChillは0分・revision 5を維持。サイズ変更、Worker追加、長時間の飽和負荷は実施していない。
- 通常APIから新規DBを作成し、RAWにCSVを投入。`seed`／`mix_1`／`mix_2`は各1,000行で、期待値は行数1,000・id合計500,500・value合計47,025。
- 各混合負荷はSQL集計・別テーブルへのIngest・2ステップJobを最大3系統で開始し、2回実行。読み取りは投入と競合しない固定seedを使用する。
- Jobは集計不一致を検出するalertと、読み取りSQLの2ステップ。1つのJobが終わってから次を開始する。
- 開始前に既存SQLite台帳79表を一貫したスナップショットへ保存。専用DBのデータは削除せず保持する。

## 最終再試験（run-c）

| 確認 | 結果 |
| --- | --- |
| SQL・Ingest・Jobの混合負荷2回 | すべて成功。Ingestはcommit済み、清掃エラーなし。行数・集計値が一致。 |
| 休止 | 処理が残っていないことを確認し、物理0/0を観測。 |
| 起動待ち | SQL 2件をQUEUED・backend IDなしで受け付け、Jobも受け付けた。これは起動待ちであり、engineの同時実行上限による待ちとは区別する。 |
| 取消 | 待機中SQLの片方を取消。復帰後もCANCELED・backend IDなしを確認。 |
| 処理中の休止保護 | 競合ChillはADMISSION_CONFLICTで失敗。停止barrier・物理停止開始ともなし。 |
| 共有起動 | 自動Wakeは1回。残るSQLとJob全ステップが完了。 |
| データ保持 | 復帰後にseed／mixの全3テーブルを再集計し一致。run-a／bの6テーブルも回復後の新しい参照コンテキストで一致。 |
| 終了状態 | ACTIVE 2/2、engine検証済み、全blocker 0。固定構成とAutoChillを維持。 |

run-cのテストDB台帳はSQL 31件FINISHED（Ingest／Jobの子SQLを含む）・取消1件、Job 3件成功。新規UNKNOWN・未終了admissionは0件。元の79表は既存行を保持し、許容した差分は回復によるwarehouse control revisionの7→9だけだった。旧3件の閉鎖済みUNKNOWNも変更していない。Trino転送数は開始前後とも71で増加しなかった。

## 発見した不具合と修正

1. **受理済みJobと休止要求の競合（run-a）**：共有Wake後、まだ物理停止を開始していないChillの管理intentを復帰阻害要因と扱い、受理済みJobがbackend送信前に失敗した。query自身のadmissionを引き継ぎ、共有lifecycle gate・OPEN・有効なACTIVE admissionのもとでのみ、停止前の生存Chillと共存できるようにした。停止barrier、物理開始、UNKNOWN、owner消失は引き続き遮断する。
2. **同じ起動を待つ処理の接続再作成（run-b）**：先行SQLが初回POSTに使用中の接続を、後続waiterの同epoch再bindが閉じ、SQLがUNKNOWNとなった。lifecycle lock取得後にreadinessを再確認し、既に同processで検証済みの接続を再利用する。別process／新epochの再検証は維持した。

両方にイベント同期による再現テストを追加。修正前の処理をメモリ内へ戻すと、Job失敗／接続切断によるUNKNOWNが再現することも確認した。通常Workbenchとresidentの長寿命processは修正後に再起動し、実行コードの一致とprofile・権限・稼働状態を確認してからrun-cを実施した。

## UNKNOWNの回復

run-bの元SQLは再送していない。GET-only照合では完全一致するengine受理証拠がなく、成功・失敗を推定しなかった。既存の`recover-query-*`で、現在の同一Engineに属するTrino Coordinator／Workerの正確な旧container 2件だけを計画した。

途中のSTOP_INTENTでは、読み取り照合で停止済みCoordinatorの正の証拠と、未停止の同じWorkerを確認した。最新revisionの同operationで残るWorkerだけを続行し、全停止証拠を保存してから旧container削除・置換engine検証・受付再開へ進めた。停止済みの対象を再停止せず、PVC・catalog・desired replicasを保持した。

回復operation `0e57e248-af12-4bf7-8c08-5421c83b0947`はOPEN・revision 5。元SQLのstateはUNKNOWNのまま、対象admissionだけを`FENCED_READ_ONLY`で閉鎖した。run-c終了時に未閉鎖UNKNOWNは0件である。初回回復applyの正確な例外codeはCLIに保存されておらず、停止証拠と現在の対象範囲を改めて確認して安全に再開した。

## 観測値

| 指標 | run-cの観測 |
| --- | --- |
| 通常SQL単独（1標本） | 2.26秒 |
| 混合負荷SQL（2標本の中央値） | 2.93秒 |
| Ingest（previewを含む、2標本の中央値） | 3.57秒 |
| Job（2標本の中央値） | 1.08秒 |
| 冷間SQL要求→engine検証完了 | 25.46秒 |
| 冷間SQL要求→SQL完了確認 | 28.42秒 |
| Coordinator／Worker CPU観測最大 | 1.664／0.390 cores |
| Coordinator／Worker working set観測最大 | 1.518／0.848 GiB |

HTTPのpoll待ちを含む小標本の経過時間で、SLA・最大性能・p95の根拠にはしない。ホストでは回帰検証も並行していた。資源観測は5秒間隔。回復前からのPod再起動数1は基準値として区別し、run-c中の増加はなく、休止復帰で新規作成したPodも再起動0、OOMKilledなしだった。

## 検証

- 変更箇所のfocused suites：264件成功。再現テスト、停止・認可・epochの境界を含む。
- 全体回帰：`verify-local-experiment`の全136 unittestレシピを専用DataOps venvで並列実行し、5,288件・失敗0・skip 34件。skipは実runtimeへのopt-in 30件、ホストOPA binary不在3件、任意pyarrow不在1件。実LOCALの今回の確認は上記run-cで別途通過した。
- 初回の全体回帰は標準PythonのPyYAML不足で中断したため、専用venvで全レシピを再実行した。コマンド一覧と各結果を保存し、最終Makefileのレシピとも照合した。
- `make verify-design-context`成功。最終の専用profile doctorはhealthy、差分検査も成功。

## 再実行と証拠

```sh
rtk proxy tools/local_experiment/dataops/.venv/bin/python3 \
  tools/local_experiment/m1_small_load.py \
  --rows 1000 --rounds 2 --lifecycle \
  --output-dir /private/tmp/shirokuma-m1-small-load-new-run
```

出力先は新規または空の私用ディレクトリを指定する。Database／Platform管理権限が必要。`--lifecycle`は共有LOCALの休止・復帰を実行する。省略時は稼働中のwarm確認だけになる。POST前に要求・キー・request IDを記録し、応答不明時は再送しない。失敗時は保存した元のhandleを照合する。成功時だけ開始時の物理状態へ戻す。

Git対象外の証拠は`var/shirokuma/db-p6-7-small-load-2026-11-05/private/`に保存した。

- `run-a`／`run-b`／`run-c`：要求・応答・集計・処理ID・時間・resource／admission記録。
- `query-fence-*`：回復計画、各intent、読み取り照合、停止証拠、最終OPEN。
- `audit-run-*-after-recovery`：以前の専用DBを再読取りした結果。
- `preservation-after-run-c.json`／`run-c-authoritative-ledger-analysis.json`：既存行・元UNKNOWN・終了状態の照合。
- `final-runtime-capacity-usage.json`／`trino-forwards-*-run-c.json`：構成・容量・転送数。
- `verify-venv`：全体検証のコマンドとグループ別ログ。

通常Workbench APIでの技術確認として扱う。M1本人受入、ブラウザー操作性評価、大規模／長時間負荷、DB-P7の実資源伸縮はそれぞれ別の判定として継続する。
