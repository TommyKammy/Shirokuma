# 性能試験の準備: Parquet分割生成と複数ファイル登録

2026-11-14 JST。[bulk取込と公開全件API](performance-bulk-results-2026-11-14.md)の後続として、1表を複数Parquetへ分割し、まとめてIcebergへ登録する経路を整備した。

## 生成とmanifest

既定の `generate` は従来のformat 1を維持する。`--target-file-bytes` の明示時は、DuckDBの生成tableから直接複数ParquetへCOPYし、巨大な単一Parquetを先に作らない。既存の成功済み所有datasetには `shard-dataset` を使う。元datasetを書き換えず、新しいdataset IDとsource manifestのhashで系譜を記録する。

```sh
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf generate "$CAMPAIGN" source --suite tpch --scale 0.01 --target-file-bytes 268435456 --row-group-size 122880 --max-file-bytes 104857600000
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf shard-dataset "$CAMPAIGN" "$SOURCE_DATASET" sharded --target-file-bytes 268435456 --row-group-size 122880 --max-file-bytes 104857600000 --timeout 600 --peak-bytes 2147483648
```

2つ目は同じcampaignに所有登録されたSUCCESS datasetを入力とする別の利用例である。出力名は常にfreshにする。生成/分割のSQLはpinned DuckDB 1.4.3で実行し、分割ごとのLIMIT/OFFSET再走査や全行Arrow化を行わない。

サイズ目標の既定は256MiB。DuckDBの `FILE_SIZE_BYTES` はrow group境界で切り替わる目安で、厳密なファイルサイズではない。最小row groupは2,048行、既定122,880行。別のhard上限を生成後に検査し、超過時はFAILEDを保持する。hard上限の最大104,857,600,000 bytes/ファイルはS3転送実装に合わせて維持する。総tableサイズを単一ファイルの上限に制限しない。[DuckDB 1.4.3 COPY実装](https://github.com/duckdb/duckdb/blob/v1.4.3/src/execution/operator/persistent/physical_copy_to_file.cpp)

| 項目 | format 1 | format 2 |
|---|---|---|
| Parquet参照 | tableの `file` | 順序付き `files` 一覧 |
| ファイル情報 | 従来のtable単位情報 | 各fileのbasename/rows/file_bytes/physical_bytes/sha256 |
| 集約 | tableのrows/bytes | 全fileのrows/bytesの合計とtable宣言値が一致 |
| source identity | 単一fileのSHA256 | 順序付きfile/rows/file_bytes/sha256一覧のSHA256 |
| query manifest | format 1 | 引き続きformat 1。新dataset IDへ結び直し、SQL/hash/variantを保持 |

format 2には単一 `file` を混在させない。参照は宣言したdirect-childのfile一覧だけで、globを使わない。ファイル名のglob文字、重複path、欠落、集約不一致を拒否する。同じ内容hashを持つ別fileは有効で、重複データとして保持する。

分割前後のschema・全行・重複を双方向EXCEPT ALLで照合し、元manifestとfile hashが変わっていないことを確認してからSUCCESSとする。空tableもschemaを持つ空Parquetを1つ生成する。容量/期限を確認し、失敗時は所有する一時DB/spillを片付け、生成途中のParquetとFAILED証拠を残す。

## 登録と参照経路

`load-bulk` はformat 1/2を読み、すべてのfileのサイズ・hash・footer・行数・schemaを最初の変更前に確認する。format 2は、fileごとにordinalを含む所有object名を作り、同一hashのfileも上書きしない。以降のfileとcommit前にvended STSを読み直し、UUID/location/schema ID/propertiesと未commit状態を照合する。

全fileの転送・検証後、tableごとに `add_files(all_paths)` を1回だけcommitする。snapshotの集約値と全registered fileのpath/rows/bytesを照合する。空fileもproof対象に含め、実SDKが省略する0件の `added-records` を偽造せず、total-recordsとfile metadataで確認する。SHA確認はfile数に対して線形の回数とし、各fileの境界で全datasetを繰り返しhashしない。

変異の応答喪失はUNKNOWNを保持し、自動再送しない。commitとfile proofが確定した後のsource変化は、確定した行数・snapshot・ACKを保持してFAILEDとする。既存tableへのappend/replaceや遠隔の自動purgeは追加していない。

runnerのDuckDB参照、TPC-H/TPC-DS方言照合、JSONL取込準備も共通file一覧へ接続した。参照/方言照合ではfile別のschema/sizeを確認し、毎queryで全データをhashし直さない。生成・分割・登録・JSONL準備の境界でhashを検証する。通常JSONL取込のサイズ・空table等の既存制限は維持する。

```sh
rtk proxy benchmarks/local_perf/.venv-bulk/bin/python -m benchmarks.local_perf load-bulk "$CAMPAIGN" "$SHARDED_DATASET" multi-load --prefix "$PREFIX" --adapter public --connection-file "$PUBLIC_CONNECTION" --bulk-auth-file "$BULK_AUTH" --timeout 600 --peak-bytes 2147483648
rtk proxy python3 -m benchmarks.local_perf freeze-snapshots "$CAMPAIGN" "$CAMPAIGN/multi-load" pins --adapter public --connection-file "$PUBLIC_CONNECTION"
```

認証・snapshot固定・公開全件取得は[前段の契約](performance-bulk-results-2026-11-14.md)を使う。新しい常駐serviceや依存packageは追加しない。native loaderは試験データ準備用で、通常取込APIのthroughput測定とは分ける。

## 検証

| 検証 | 結果 |
|---|---|
| 既存datasetの分割 | 200,006行のeventsを4file、同じschemaの空tableを1fileへ出力。目標4MiB、row group 8,192行、hard上限16MiB、最大実file 3,666,990 bytes。元datasetとの全schema・全行・重複一致とhash不変を確認 |
| 直接分割生成 | TPC-H SF0.01は8表・28file・86,805行と全22 SQLの参照方言照合が成功。TPC-DS SF0.01は24表・65file・277,976行を生成し、別の600秒検証枠で固定99 SQL・3,928行の参照方言照合が成功 |
| 実native登録 | 2表・5file・200,006行をfresh prefixへ登録し、各表1回commit、空fileを含む全path/rows/bytesを確認。通常公開API観測とnative UUID/schema/snapshotで2表を固定 |
| 全件照合 | 2表・200,006行のBIGINT/VARCHAR/DECIMAL(20,4)/TIMESTAMP(6)/DATE/BOOLEAN、NULL・空文字・先頭ゼロ・重複が、分割Parquet参照と直接Trino/公開全件APIの両方で一致。前後snapshotも一致 |
| JSONL準備 | format 2から2表・200,006行を19 partへ出力し、source一覧とhashの再検査が成功。空tableの準備も確認したが、通常公開loadの既存の空table制限は維持 |
| 保護と健全性 | 元57表と新2表のUUID/schema/snapshot/metadataが一致。既存binding/identity/context/control、旧query/receipt/UNKNOWN履歴、既存設定・GitOps差分を保持。Workbench再起動なし、Pod UID/container不変、再起動/OOM/kill 0、未終結0、doctor healthy 46項目 |
| 重点試験 | 実SDKを有効にしたbulk 91件、実DuckDBを含むsharding 27件が全件成功。JSONL準備・v1互換・TPC・binding・計測境界も別途確認し、互換用データで旧format 1のinput JSONと6 JSONL partは旧版とbyte一致 |
| 全体回帰 | Make定義159群・5,848件をソース固定で4並列実行。5,781成功、67 skip、失敗0 |

TPC-DSの最初の参照方言照合は、84件完了後に300秒のplan期限でFAILEDとなった。生成済みdatasetはSUCCESSのまま保持し、失敗planを書き換えず、別campaign・fresh名・事前に600秒を指定した新しい検証で99件を完了した。SQL・比較規則・許容差は変更していない。この期限は小規模の参照資格確認用であり、性能SLOの合否ではない。

全体回帰は `make -n verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3` の159 unittestコマンドを実行し、実行前後のソースhash一致を確認した。元158コマンドの内容と順序を維持してsharding試験1コマンドを追加した。既存248 Python test file・5,598 test methodをAST比較し、削除0件を確認した。67 skipは既定opt-inや任意依存等で、任意SDK・DuckDBが必要な今回の重点試験は別環境でskipなしで確認した。

200,006行の取得完了は直接約101秒、公開約112秒、全値比較まで含むattempt全体はそれぞれ約400秒・398秒だった。同一hostでの保存・容量確認・比較の負荷が大きく、engine性能・SLO・大規模throughputの判定には使わない。0.5秒間隔の観測ではWorkbench RSS最大約161MiB、client約133MiBだった。これは観測値であり上限保証ではない。

証拠はGit外の `shirokuma-parquet-shards-2026-11-14` に保存した。`campaign/source` / `sharded` が不変の元datasetと分割出力、`tpch-sharded` / `tpch-portable` と `tpcds-sharded` が直接生成・方言照合、`native-load` / `snapshot-pins` が登録証拠である。`tables-reference` / `tables-direct` / `tables-public-all` に全値結果、`jsonl-prep` に取込準備を保存した。期限切れの `campaign/tpcds-portable` と、再検証の `oracle-campaign/tpcds-portable-600` を両方保持する。

`inventory-06c443e1` / `inventory-e86b8ff5` が前後のnative・台帳・資源観測で、`verification-summary.json` で保護対象と認証ファイル0を追跡できる。生成・分割・方言準備・比較に使う所有一時DB/spillは清掃済み。成功した登録tableとsnapshot、およびFAILED証拠は後続試験用に保持する。`verify-parallel/plan.json`・159群ログ・`regression-parallel-summary.json` が全体回帰で、設計整合・改行・空白・秘密情報ファイル名は `static-checks.log` が検査記録。

追加物理占有2TB、host予備256GiB、guest data予備 `max(64GiB,10%)` とroot予備 `max(4GiB,10%)` を維持する。元campaignのVM/state baselineを容量台帳として継続し、過去のartifact/SDK領域と新artifactを加算する。別campaignの参照方言照合を並行した間も、その次工程2GiB枠を追加予約した。最終実機観測のhost空きは約3.098TB。現在のcoordinator heap8GiB/container12GiB、worker heap2GiB/container4GiB、VM16 CPU/96GiB/400GiBは変更しない。

次は残るTPC-DS variant/scaleと計測器負荷を整理し、L1/guest disk拡張、100GB pilotでサイズ・生成/取込時間・spillを再見積りする。
