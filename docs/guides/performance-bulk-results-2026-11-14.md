# 性能試験の準備: 大量取込と公開APIの全件取得

2026-11-14 JST。[性能試験計画](performance-comparison-plan-2026-11-13.md)と[coordinator設定](performance-coordinator-profile-2026-11-14.md)の後続。整備と小規模の実機照合を完了した。

## 大量取込

`benchmarks.local_perf.bulk.load_bulk` と `load-bulk` は、所有campaignの成功済みParquet datasetを新しいIceberg v2 tableへ登録する試験用loaderである。対象DB専用のPolaris consumer OAuthを明示し、テーブル応答で得た一時STS資格情報だけで、Parquetを1MiBずつテーブル所有のdata prefixへコピーする。その後、PyIceberg `add_files` でsnapshotをcommitする。行をSQL VALUESへ展開せず、全データをArrow tableやHTTP JSON bodyへ載せない。

既存の通常取込API・stage機能の制限は維持する。このloaderは、試験データのnative登録経路であり、通常取込APIのthroughputやDataOps operation/admissionの測定値として扱わない。既存tableへのappend/replace/upsertは行わず、fresh prefixを使う。公開APIで現在のDB・schema・権限を再確認し、raw/sandboxの書込可能なadmin/data_engineer contextだけを受け付ける。

任意依存は[requirements-bulk.txt](../../benchmarks/local_perf/requirements-bulk.txt)に隔離したPyIceberg 0.12.0とPyArrow 26.0.1。通常runtimeへ追加しない。macOS arm64の実機で確認し、Linux/aarch64の実行確認は後続とする。SDKのcreate/commit・HTTP・S3の自動再送を無効にした小さなadapterを使い、SDKをグローバルに変更しない。[PyIceberg 0.12.0](https://pypi.org/project/pyiceberg/0.12.0/)、[固定版REST実装](https://github.com/apache/iceberg-python/blob/pyiceberg-0.12.0/pyiceberg/catalog/rest/__init__.py)

```sh
rtk proxy tools/local_experiment/dataops/.venv/bin/python3.12 -m venv benchmarks/local_perf/.venv-bulk
rtk proxy benchmarks/local_perf/.venv-bulk/bin/python -m pip install -r benchmarks/local_perf/requirements.txt -r benchmarks/local_perf/requirements-bulk.txt
rtk proxy benchmarks/local_perf/.venv-bulk/bin/python -m benchmarks.local_perf load-bulk "$CAMPAIGN" "$DATASET" native-load --prefix "$PREFIX" --adapter public --connection-file "$PUBLIC_CONNECTION" --bulk-auth-file "$BULK_AUTH" --timeout 600 --peak-bytes 2147483648
```

`$BULK_AUTH` はmode 0600の明示ファイルで、次のfieldを持つ。通常API用Basic認証とは分離し、platform provisioner、storage admin、root OAuthを渡さない。loaderはruntime profileや環境からbackend資格情報を探索しない。Polarisへの外部origin/redirectを拒否し、S3は明示したloopback endpointとvended STSに限定する。ambient AWS認証を使わない。

```json
{
  "format": 1,
  "uri": "http://127.0.0.1:18181/api/catalog",
  "warehouse": "selected_sql_catalog",
  "realm": "selected_realm",
  "client_id": "DB consumer client ID",
  "client_secret": "DB consumer client secret",
  "storage_endpoint": "http://127.0.0.1:18333",
  "region": "us-east-1",
  "database_id": "selected_database_id",
  "storage_prefix": "s3://selected-bucket/selected_database_id/"
}
```

現manifestは1ファイル／表で、1ファイルの上限は104,857,600,000 bytes（約104.86GB、境界値を含む）。固定版Arrowの10MiB multipart／最大10,000 partsに合わせ、超過入力はhash/footer読込より前に拒否する。2TBの総容量上限は1ファイルの対応を意味しない。これを超える表には、Parquet分割生成と複数ファイル登録を先行整備する。[Arrow 26.0.1実装](https://github.com/apache/arrow/blob/apache-arrow-26.0.1/cpp/src/arrow/filesystem/s3fs.cc#L1508-L1515)

全入力のhash・Parquet footer・正確なscalar schema・行数・容量を最初の変更前に検証する。対応しない型や既存Parquet field IDsを黙って変換しない。manifestとsourceの版を取込中も確認し、create/upload/commitのINTENTを送信前に保存する。返却されたUUID、location、snapshotと登録fileをjournalへ保存し、公開inventory・table profile・snapshot観測へ接続する。`freeze-snapshots` は通常の `public-load` と新しい `bulk-load` の両方に対応し、native登録時のUUID/snapshotと実観測を照合する。

応答喪失はUNKNOWNとして保持し、自動再送やtable／公開済みdata fileの自動削除をしない。同campaignの未解消bulk処理は次のbulk投入を止める。片付けはjournalのUUID・snapshot・file prefixを現在の実体と確認してから、所有tableだけを対象に行う。無関係なtableやsourceを消さず、非purge登録解除で残ったfileも物理占有へ計上する。既存の `cleanup` がbulk証拠を消すことはない。

## 公開SQLの全件モード

従来の6 fieldと整数 `max_rows` (1〜10,000) は従来どおり。全件取得は通常の `POST /api/v1/queries` に明示的に `result_mode: "all"` を加え、`max_rows: null` と `mode: "read-only"` を指定する。通常の認証・SQL policy・9項目context・所有者と現在のgrant/identity確認を通る。

```json
{
  "sql": "SELECT * FROM selected_table",
  "request_id": "fresh-request-id",
  "worksheet_id": "fresh-worksheet-id",
  "context": {
    "database_id": "selected_database_id",
    "schema_id": "selected_schema_id",
    "warehouse_id": "LOCAL",
    "binding_revision": 1,
    "generation_id": "selected_generation_id"
  },
  "mode": "read-only",
  "max_rows": null,
  "result_mode": "all"
}
```

返却された `next_uri` を順に取得し、明示的なFINISHEDと `next_uri: null`、整合するschema、打切りなしを確認して完了とする。全件・待機ページは `result_mode: "all"` を返す。サーバーは直近2ページだけを再取得用に保持し、次ページの取得を古いページの受領として扱う。退去したtokenは404となり、同じbackend GETを再送しない。取得済みデータはclientが逐次保存する。全件モードを既存の「受信済みページのexport」APIへ渡すと明示的に拒否する。

backend応答は1ページ16MiB、1回30秒の絶対期限で制限する。サイズ超過、不完全なContent-Length、deadline、backend失敗を成功やサイレントな打切りにしない。全件結果のcampaign artifact・重複検知用の一時SQLite・全値照合領域も容量に含める。SQLiteは有界cacheで動作し、成功・失敗の両方で一時索引を解除する。全値比較中のSQLite進捗callbackは、高コストの容量確認を確認完了から200ms間隔で行う。初回・256行単位・明示境界・最終確認は維持し、deadlineや照合範囲を緩めない。これはsamplingでありfilesystem quotaではないため、in-flight/drain予備を別途予約する。processを失った場合は従来どおりcontext lostとなり、不明なPOSTを再実行しない。

```sh
rtk proxy python3 -m benchmarks.local_perf freeze-public-connection "$FULL_CONNECTION" --adapter public --runtime-profile "$PROFILE" --database "$DATABASE" --schema sandbox --all-results
rtk proxy python3 -m benchmarks.local_perf run "$CAMPAIGN" public-all "$MANIFEST" --adapter public --connection-file "$FULL_CONNECTION" --references "$REFERENCES"
```

`--all-results` と `--max-rows` は同時指定できず、全件flagはpublic adapter専用。凍結したconnection/attemptにはmodeを固定し、復旧時に切り替えない。history/inspectによる復旧はSQL・context・所有者を検証し、queryの終結を確認するだけで、失われた結果を照合成功へ昇格させない。送信・待機・結果取得・artifact処理の既存の計測区分を維持する。

## 検証と実機証拠

| 検証 | 結果 |
|---|---|
| 重点試験 | query関連124件、public/CLI/HTTP関連60件、native SDK60件、snapshot12件、比較関連55件が成功。SDK試験は任意依存を入れた別環境でskipなし。Python 3.9.6でもbulk48成功/12任意SDK skipと比較guard3件を確認 |
| 既存全件取得 | 固定済みdate_dimの73,049行が、Parquet参照・直接Trino・公開全件APIで型・全値・重複を含めて一致 |
| native登録 | 独自生成Parquet 54,256,183 bytes・200,006行を新prefixへ登録。UUID、snapshot、1 data fileと行数/bytesを確認し、通常公開profileとmetadata観測で固定 |
| 新しい全件照合 | 200,006行のBIGINT/VARCHAR/DECIMAL(20,4)/TIMESTAMP(6)/DATE/BOOLEAN、NULL・空文字・先頭ゼロ・重複が直接Trino/公開全件APIともParquet参照と一致。前後のsnapshotも一致 |
| 打切り・取消 | 旧previewはPUBLIC_TRUNCATED、全件モードの実行中取消はCANCELED。意図した失敗2件として保存し、未終結を残さない |
| 保護と健全性 | 元56表と新1表のUUID/schema/snapshot/metadataを保持。既存binding/identity/context/control、旧query/receipt/UNKNOWN履歴、既存設定・GitOps変更を保持。Pod UID/containerは不変、自動再起動0、cgroup OOM/kill 0、doctor healthy 46項目 |
| 全体回帰 | ソース固定後、Make定義158群・5,782件をすべて実行。5,731成功、51 skip、失敗0。既存test methodの削除0 |

初回の200,006行direct試行は、全件取得後の比較が600秒でtimeoutした。SQLはFINISHED、取得済みfooterは200,006行だったが、その試行を成功へ書き換えていない。SQLite進捗callbackの高コスト確認を200ms samplingへ修正し、新しい試行で直接/公開とも同じ600秒の期限内で全値照合を通過した。総所要は直接約559秒、公開約595秒で、保存・容量確認・比較の負荷が大きい。公開経路の取得完了eventは約161秒。これは検証器と同一hostの回帰試験を含む小規模qualificationであり、engine性能・SLO・通常取込APIのthroughputの判定に使わない。

全体回帰は `make -n verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3` が列挙した158 unittestコマンドを、定義の欠落なく4並列で実行した。最終ソースのhashを実行前後で確認し、先行の中断実行は集計対象にしていない。51 skipは既存39件のopt-in/任意依存等と新しい任意SDK12件。後者は別SDK環境で全件通過した。設計整合・改行・空白・秘密情報ファイル名の検査も通過した。

Workbenchは元のargv/envで一度だけ再起動してAPI変更を反映し、通常APIと元contextを確認した。新しい全件試行中、0.5秒間隔の観測でWorkbench RSSは最大約135MiB、clientは約100MiBだった。これは観測値であり一般的な上限保証ではない。

証拠はGit外の `shirokuma-bulk-results-2026-11-14` に保存した。`inventory-d180a856` / `inventory-fd66d300` が前後の57表・台帳・native resource、`campaign/native-load` / `snapshot-pins` が登録証拠である。`old-reference` / `old-direct` / `old-public-all`、`bulk-reference` / `bulk-direct-fixed` / `bulk-public-all` に全値結果を保持した。元の `bulk-direct` timeout、期待失敗2件、`verify-parallel/plan.json`・全158群ログと `regression-parallel-summary.json`、`verification-summary.json` も残した。temporary auth fileは0で、認証値をGitへ保存しない。

追加物理占有2TB、host予備256GiB、guest data予備 `max(64GiB,10%)` とroot予備 `max(4GiB,10%)` を維持する。前campaignのVM/state増分を含む元のbaselineを使い、新artifactと任意SDK環境も追加計上する。coordinator heap8GiB/container12GiB、worker heap2GiB/container4GiB、VM16 CPU/96GiB/400GiBは維持する。今回の小規模検証から大規模性能・SLOの合格を主張しない。

## 次の順序

Parquet分割生成・複数ファイル登録と、残るTPC-DS variant/scale等のG1残件を確認し、L1/guest disk拡張、100GB pilot、容量・時間の再見積りへ進む。
