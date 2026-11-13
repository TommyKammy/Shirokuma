# ローカル性能試験の準備: P0／P1

2026-11-13 JST。[試験計画](performance-comparison-plan-2026-11-13.md)に沿い、ローカル条件・資源配分・容量上限を固定し、データ生成・全結果検証・共通runnerの初期基盤を整備する作業記録。大規模性能の合否と、準備用の小規模qualificationを分ける。

## 固定した条件

| 項目 | 宣言値・現在値 |
|---|---|
| ホスト | Mac Studio M3 Ultra、24P＋8E、512GiB RAM、4TB SSD |
| 空き容量の更新 | 読み取り時3,105,012,654,080 bytes、約3.105TB。旧約2.16TBから更新 |
| 試験用の上限 | 追加物理占有2TB、host予備256GiB。現在は2TBの予算を確保できる |
| 次の資源profile | L1: VM24 vCPU／192GiB、worker16 CPU／128GiB、heap96GiB、query64GiB、headroom24GiB |
| 共用基盤・host | VM内に8 vCPU／64GiBを予約。VM外にOS64GiB・他VM8GiB・準備process8GiBのRAM予算 |
| 準備実行 | qualification、既定TPC-H SF0.01、2 threads／4GiB、query deadline120秒、反復1・並列1 |
| 実機への適用 | 未実施。現在のVM16 vCPU／96GiB／400GiB、guest data空き約146GiBのまま。適用・拡張はP2 |

機械読込の正本は[protocol.json](../../benchmarks/local_perf/protocol.json)。資源表示は観測値と要求値を分け、VMを変更しない。L2はCPU数をL1と揃えたmemory比較、L3／L3-extraは競合を確認する追加条件とする。別プロジェクトのVMは停止・変更していない。

容量はcampaign開始時のbaselineを維持し、VM image実割当とhost直置きファイルを重複なく計上する。host空きとguest空きは別に判定し、生成・spill・検証出力・停止中の増加を予約する。論理データ量の固定倍率は既定概算として使い、pilot根拠付きの明示peakで計画を更新できる。これにより1TB規模を一律拒否せず、2TB上限は維持する。

## 初期基盤の範囲

[利用手順](../../benchmarks/local_perf/README.md)と[共通runner](../../benchmarks/local_perf/runner.py)を`benchmarks/local_perf`へ分離し、DuckDB 1.4.3を専用Python環境に固定した。通常のMake回帰は標準ライブラリのみで動き、データ生成・拡張取得は明示したqualificationだけで実行する。

- TPC由来データを一時disk DBで生成し、Parquetへ出力。全tableのschema・行数・重複を含む内容を検証し、失敗時は接続と所有する一時DB／spillを解放する。
- SQLとデータのmanifest、型付きの全結果、必須終端footerを保存する。Decimal・整数・NULL・日付・UTC timestampを区別し、順不同の重複保持と明示したfloat誤差で全件比較する。
- 任意SQL manifestを扱う逐次runnerで、送信前intent、query handle、SQL識別、予定・実行・未実行件数を記録する。POST応答喪失を自動再送せず、未確定処理があるcampaignは後続投入を止める。
- DuckDB参照経路とloopback Trino engine経路を用意する。公開Shirokuma API adapter、実Trinoへのdataset登録・照合、全結果APIの拡張は後続とする。

## 再実行入口

```sh
rtk proxy python3 -m benchmarks.local_perf protocol --profile L1
rtk make test-local-perf
rtk make qualify-local-perf
```

実データ生成の準備・専用venv作成・fresh campaign・preflight・取消照合・cleanupは[README](../../benchmarks/local_perf/README.md)を参照する。出力はGit外へ保存し、既存campaignのbaselineや未確定receiptを上書きしない。

## 完了証拠と残る範囲

| 検証 | 結果 |
|---|---|
| `make test-local-perf` | Python 3.9.6／3.12.12で各45件成功。模擬HTTPサーバーとの実通信8件、容量・型・全件比較・deadline・応答喪失・復旧の確認を含む |
| `make qualify-local-perf` | SF0.01、8表の全件Parquet照合、22参照結果、配布済み期待結果22件、再実行比較22件が成功。Parquet実割当2,150,400 bytes |
| 追加scale確認 | SF0.1でも8表・22参照・期待結果22件・再実行比較22件が成功。Parquet実割当21,880,832 bytes |
| TPC-DS初期生成 | SF0.01、24表の全件Parquet照合・99 SQL列挙が成功。Parquet実割当3,915,776 bytes。99 queryの実行は未実施 |
| 容量preflight | host側は2TBの上限を保持。1TB追加割当の例では現guestが未準備と判定され、data側の不足約912GBを表示。host空きとguest容量を混同しない |
| 設計整合 | `make verify-design-context`成功。通常の設計検証14件と文書整合を確認 |
| 回帰の検証範囲 | 既存139群と追加5群を確認。合算144群・5,430成功・既定34 skip。下記の修正後再実行を含む |

全体の`make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3`では、既存core138群が成功した後、末尾の新HTTP試験でPython 3.12のEOF処理差を検出した。応答完了後に閉じたsocketへ再操作しないよう修正し、追加5群を両Pythonで再実行した。未実施だった既存client65件は`make -o test-resident-core verify-local-experiment`（同じPYTHON指定）で追完した。未変更の既存群の成功証拠と、修正後に検証した範囲を合わせた記録である。

小規模のParquet参照再実行は同じengineのroundtripであり、独立したShirokumaの正解証明とはしない。配布済みTPC-H期待結果の照合も、実Trinoとの独立照合とは分ける。Q17のNULL表現とQ18の無名集約列ラベルは入力形式として処理し、通常の結果比較では型・列名・値の検証を維持した。

実行証拠はGit外のcampaignと検証ログに保存した。実装中に見つけた拡張読込・期待結果形式の失敗記録も保持し、成功した最終qualificationと区別している。今回の準備用データを、100GB以上の負荷試験や通常運転0エラーの観測へ加算しない。

G1全体の受入には、実Trinoのsnapshot・SQL方言・型・取消の確認、query別の順序・同順位の契約、TPC-DS全variantの対応と照合、公開APIの認可・履歴・結果取得が残る。初期TPC manifestの照合は重複を保持するmultisetであり、ORDER BYの検証まで完了したとは扱わない。P2ではL1を適用してguest disk・heap／spill・復旧を検証し、その後に100GB以上へ進める。同時実行・到着率・24時間試験は未実施である。
