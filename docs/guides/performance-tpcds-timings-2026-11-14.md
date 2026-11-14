# 性能試験の準備: TPC-DS照合契約と計測境界

2026-11-14 JST。[試験計画](performance-comparison-plan-2026-11-13.md)のP1として、TPC-DSの固定99 SQLと共通runnerの計測境界を整備した。SF0.01の24表・277,976行を公開APIで隔離登録し、全表内容と、直接Trino／公開SQLの各99本・3,928行がDuckDB参照と一致した。これは小規模な正しさ・計測条件の検証で、正式TPCスコアや大規模性能の合否は扱わない。

## 実装と再実行

[利用手順](../../benchmarks/local_perf/README.md#persisted-tpc-ds-qualification)に、`prepare-tpcds`、`bind-tpcds`、取込batch指定と計測schemaをまとめた。[契約](../../benchmarks/local_perf/tpcds_contracts.json)はDuckDB 1.4.3の固定99本を対象とし、全variant／TPC-DS 4の受入とは区別する。24表・99 ID・canonical hash・出力型・ORDER・LIMITを検査し、変換前／出力cast後／portable SQLの全値と重複を照合する。

AVGと除算のDOUBLE演算、整数SUMのBIGINT範囲、日付・方言・出力名を明示する。SQLGlot 30.31.0は静的契約の再生成時だけ使用し、通常runtimeの依存を増やさない。24表の型・context・SUCCESS状態のpinsを確認してから各表を`FOR VERSION AS OF`へbindし、外側のORDERも保持する。元のSQL・結果・pinsを上書きしない。

ORDERには列位置のほか、出力列の減算と整数CASEの限定した式を使用する。LIMIT境界が同順位ならunlimited結果を最後まで走査し、そのORDER groupの全行が型付き出力全体で完全一致する場合だけ許容する。Q59は全2,548行を走査し、境界49行が同一であることをsource／portable双方で確認した。異なる値を含む同順位は拒否する。

近似照合の前に全multisetの完全一致を検査する。部分的な完全一致だけを取り除かず、必要な再割当を維持する。容量／期限callbackをまとめても、全行・重複・型・footerの検証は省略しない。

## 検証結果と受入の範囲

| 検証 | 結果 |
|---|---|
| source | 専用DuckDB 1.4.3でSF0.01を再生成。24表・277,976行、測定logical 26,764,077 bytes。全表のParquet roundtrip成功 |
| 99 SQLの変換照合 | 全99本SUCCESS、72本非空・27本空、計3,928行。source／cast／portableの型・全値・重複・ORDER・LIMIT監査が成功。これは同一engineの変換検証 |
| 公開登録 | 新しい専用prefixへ43 chunks・103,905,239 bytesを500行batchで投入。43操作すべてFINISHED、277,976行commit、POST応答喪失0、全表でstage登録解除完了 |
| snapshot・全表内容 | 24表の実UUID・schema・main／metadata履歴を固定。全24表・277,976行すべてを許可済みDBユーザーの直接TrinoとParquet参照で照合し一致。COUNTだけの判定ではない |
| 直接Trino／公開SQL | 最終の各99本・3,928行が参照と一致。両経路それぞれ72本非空・27本空、最大1,974行。公開APIの10,000行上限を維持し、打切りやprivate経路を成功へ加算しない |
| 前後の不変性 | 全表照合、直接99本、公開99本の前後で24表のcontext／schema／snapshot／metadataが不変。元の基準構成へ戻した後も24表のpins不変と公開AS OFのCOUNT値73,049を確認 |
| 計測の実機確認 | 既存TPC-Hの直接／公開各22本と、TPC-DSの各99本でversion 1計測を確認。TPC-DS計198試行はbackend4項目を各99件保持し、27件ずつの空結果でfirst／last rowがnullになることも確認 |
| 最終重点試験 | Python 3.9.6は203件中197成功・明示6 skip。別のPython 3.12.12環境で実生成・Parquet・両TPC source・SQL再生成を有効にした203件はすべて成功。通常benchmark環境へSQLGlotを追加しない |
| 全体回帰 | `make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3`は154群・5,577成功・既定37 skip・失敗0。これは最終Q72／preflight修正前の全体Makeで、その後の変更範囲は上記203件と実機再実行で再検証した |

## 計測と実機で修正した点

計測は初回送信と後続取得のconnect／send／header wait／body read／decode、最初／最後のdecoded page・batch、終端観測、結果artifact完成、schema／ORDER／全件照合、取消・復旧を分ける。未観測はnullとし、失敗時は部分計測と元の失敗を保持する。engineのqueue／CPU／wall／elapsedは最新の`response.stats`を別欄へ保存し、header waitをengine queueと呼ばず、page間で累積値を加算しない。従来の`fetch_s`／`validation_s`／`elapsed_s`は意味を維持する。

最初の直接Trino試行は71本成功後、Q72の`DATE + INTEGER`でTYPE_MISMATCHとなった。型付きASTで`CAST(date + signed_days * INTERVAL '1' DAY AS DATE)`へ変換し、DATE型・NULL・日数・比較条件を維持した。変わったportable契約はQ72だけで、99本のcanonical hashは不変。旧FAILEDと参照を残し、新しいplan／reference／runで全99本を再実行した。型の差は[DuckDB 1.4の日付演算](https://duckdb.org/docs/1.4/sql/functions/date)と[Trino 483の日付演算](https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/functions/datetime.md)も参照する。

再実行では過去のrunを検査するたびに、各attemptから全99 SQLと24表pinsを再検証していた。読取専用のsnapshot確認を中断し、manifest／report／全query契約／pins指紋を保存済みrunごとに一度検証する形へ整理した。全attemptのSQL・variant・dataset・adapter・拒否証拠・reconciliation・未確定状態の検査は維持する。永続cacheはなく、次の呼出しではファイルを読み直す。99 query／121 attemptの構造試験で回数と未実行SQLの改変検知を確認し、中断した記録も保持した。

今回の計測は正しさと境界の確認で、cacheはunknown。回帰検証と並行した観測を速度比較・percentile・SLO判定には使用しない。

## 取込の失敗・UNKNOWNと資源条件

初回は長い表名とprefixが公開APIの31文字上限を超え、受理0行でFAILEDになった。名前生成と事前検査を修正し、不正な準備済み入力もHTTP送信前に拒否する。次の短いprefixの試行では、2,000行batchの34列INSERTが2,009,418文字となり、Trinoの1,000,000文字上限でFAILEDだった。

この確定FAILEDのcleanupはpurge付きDROPを試み、Polarisのpurge禁止で失敗した。所有stageだけを別operator処理で非purge登録解除し、ACK204と不在404を確認した。先行したoperator CALLの拒否もFAILEDとして保持し、公開APIのcleanup成功へ置き換えない。非purgeによるdata保持は容量解放とみなさない。

500行batchの旧試行は計93,637行をcommitした後、共有coordinatorのheap 2GiBで`OutOfMemoryError: Java heap space`となった。当該chunkは受理7,569行、stageへ3,000行load、targetへのcommit 0行として記録され、failure-cleanupはbackend IDなしのUNKNOWNになった。非終端query／ACTIVEが0でも履歴上の成否は確定できないため、元operation・query・admission・claimと原journalのUNKNOWNを保つ。

所有者の承認済みローカル再構成の範囲で、古いWorkbenchと両engine containerの実行終了を証明し、coordinatorだけheap 8GiB／container 12GiBにした。Workerのheap 2GiB／container 4GiB、CPU、query 1GB、VM16 vCPU／96GiB／400GiBは維持した。初回Workbench起動は実readiness前に拒否された記録を残し、無改変の通常provider検証を通過してから復旧した。最初の環境変数は22標準キーからの再構成で完全一致ではない制約も記録し、元profile bytesと9項目contextは維持した。

新campaign・新prefixは通常APIの受付条件を満たし、旧UNKNOWNの台帳を書き換えずに全24表と99 SQLを検証できた。旧UNKNOWNは成功へ変更せず、実行終了の証拠と履歴上の成否を区別する。検証後はcoordinatorのConfigMapとDeployment仕様を基準の2GiB／4GiBへ完全復元し、workerは再起動せず、通常API・context・doctor healthy 46件を確認した。一時overrideを正式な資源profileやM1 compute elasticityの受入とは扱わない。

## 容量・証拠・次の順序

追加物理占有2TB・host予備256GiBを維持し、L1は未適用。採用campaignはVMの2 image・campaign出力・hostのcontrol-storeを生成前から計上し、終盤の増分は約0.56GB、host空きは約3.102TB。旧campaignはcontrol-storeの自動計上を含まないため、その制約を区別し、新campaignでは生成前から対象へ加えた。全表のtyped出力・SQLite照合には1GiBのpeakと600秒／queryを予約した。

採用証拠はGit外の`shirokuma-tpcds-8g-2026-11-14/campaign`にある。`tpcds-source`、`portable-plan-v2`、`public-load`、`snapshot-pins`、`tables-direct`、`tpcds-reference-v2`／`tpcds-direct-v2`／`tpcds-public-v2`と、`qualification-summary.json`、`verify-restored-*.json`から追跡できる。旧失敗・監査・停止／復元証拠は`shirokuma-tpcds-live-2026-11-14`に保持し、成功runだけにしない。

次は旧ingestのUNKNOWN cleanupについて実行終了証拠を使う復旧・放棄と、失敗時の非purge cleanupを整える。続いて正式なcoordinator資源profile、bulk取込と公開全結果取得を整備する。固定99本以外のvariant、別scale、cold／同時実行条件等のG1残件を確認してから、大規模試験へ進む。
