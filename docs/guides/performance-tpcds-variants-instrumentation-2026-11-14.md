# 性能試験の準備: TPC-DSの対応範囲と計測器負荷

2026-11-14 JST。[Parquet分割生成・複数ファイル登録](performance-parquet-shards-2026-11-14.md)の後続。既存99 SQLを維持し、iteration補完と承認variantの対応範囲を分け、全件取得・比較で繰り返す容量走査の負荷を整理する。

## TPC-DSの対応範囲

| 分類 | 対象 | 位置付け |
|---|---|---|
| 既定corpus | DuckDB 1.4.3の固定99 SQL | 99本とも単一statement。既存SQL・contract・既定経路を維持 |
| iteration補完 | 14b / 23a / 24b / 39b | Apache Flink由来の4つの実行case。既存99と別suiteで明示的に準備・照合 |
| 承認variant | 5 / 10 / 14 / 18 / 22 / 27 / 35 / 36 / 51 / 67 / 70 / 77 / 80 / 86 の14件 | 現行v4 Appendix Cで列挙されるもの。別corpusの由来・方言・同値性の受入が残る |

iterationのa/bと、承認variantのa.tplは別の分類である。既存DuckDBは14/24/39のiteration 1と23のiteration 2を持つ。今回の4caseを加えた実行case数は103となるが、異なる由来・固定parameterを組み合わせるため、公式の103 statementやTPC-DS v4全体の合格を意味しない。[TPC-DS v4.0.0 Appendix C](https://www.tpc.org/TPC_Documents_Current_Versions/pdf/TPC-DS_v4.0.0.pdf)

補完sourceはApache Flink `release-2.1.0` のcommit `4cb6bd30df3d80cea0b504e7250d65777a021bca`、`flink-end-to-end-tests/flink-tpcds-test/tpcds-tool/query` 配下の4 SQL。upstream READMEはv2.11.0由来と記す。特に39bのDECIMAL(7,2)入力castと重複列名を保持し、既存39との同じ結果を前提にしない。[Flink sourceとREADME](https://github.com/apache/flink/tree/4cb6bd30df3d80cea0b504e7250d65777a021bca/flink-end-to-end-tests/flink-tpcds-test/tpcds-tool)

raw sourceと生成したportable SQL bodyはGitに同梱しない。repoには固定URL・hash・構造contract・由来を保存し、callerが供給する4 sourceの全hashを確認してから専用環境で準備する。受け取ったSQLと生成SQLはcampaign artifactに保存する。FlinkのLICENSE/NOTICE、queryにheaderがなくRAT除外である観測もprivate入力に保持し、個別SQLの明示的なApache grantまで確認したとは扱わない。

承認variantの公開候補はApache Spark v4.0.1の `sql/core/src/test/resources/tpcds-v2.7.0` にある14 SQL。commit `29434ea766b0fc3c3bf6eaadb43a8f931133649e` の14 SQLを保存して再検査し、13件はDuckDB単文parseに成功、77aは予約語のalias `returns` 付近でParserExceptionとなった。binding・SQL実行は行っていない。旧版由来・parameter・変換意味の検証が未完了であり、現行v4 templateとの一致は未受入である。公式toolsの登録/EULAフォームは送信していないが、14件すべてが登録待ちで実施不能という意味ではない。[Spark候補source](https://github.com/apache/spark/tree/29434ea766b0fc3c3bf6eaadb43a8f931133649e/sql/core/src/test/resources/tpcds-v2.7.0)

`tpcds-coverage` は実装上の対応範囲を返し、実行済みや公式合格を宣言しない。補完の準備はDuckDB 1.4.3 / SQLGlot 30.31.0専用venvで行い、既定のrequirementsにはSQLGlotを追加しない。

```sh
rtk proxy python3 -m benchmarks.local_perf tpcds-coverage
rtk proxy uv --cache-dir "${TMPDIR:-/tmp}/local-perf-variants-uv" venv --python 3.12 benchmarks/local_perf/.venv-variants
rtk proxy uv --cache-dir "${TMPDIR:-/tmp}/local-perf-variants-uv" pip install --python benchmarks/local_perf/.venv-variants/bin/python -r benchmarks/local_perf/requirements-tpcds-variants.txt
rtk proxy benchmarks/local_perf/.venv-variants/bin/python -m benchmarks.local_perf prepare-tpcds-supplement "$CAMPAIGN" "$TPCDS_DATASET" supplement --source-dir "$FOUR_SOURCE_DIR" --timeout 300
rtk proxy python3 -m benchmarks.local_perf bind-tpcds "$CAMPAIGN/supplement/queries.json" "$PINS" "$BOUND_MANIFEST"
```

`FOUR_SOURCE_DIR` には固定版の `query14b.sql` / `query23a.sql` / `query24b.sql` / `query39b.sql` をdirect childとして置く。他のSQLや下位directoryのSQLを混在させない。downloadは暗黙に実行しない。callerのsource bytes、input schema、全Parquet hash、再生成したportable hash・出力型・ORDER/LIMIT・変換由来が固定contractと一致することを確認する。準備の入口から、hash検査・SQL実行・最終再検査まで1つのabsolute deadlineを適用する。

sourceの末尾commentがordinal cast wrapperの閉じ括弧を飲む問題は、新しい補完経路のwrapper/LIMIT auditに渡すbodyだけをSQLGlotでcomments=Falseに正規化して解消した。raw sourceは元bytesで実行し、raw SHA・正規化body SHA・parser版・用途を記録する。旧99のprojectionやcontract bytesは変更しない。初回のParserException planを保持し、修正後のfresh planを資格確認に使う。

`bind-tpcds` は補完suiteの4 ID、source/portable hash、構造・正規化・由来を静的contractから再構築して検証し、元dataset IDと一致する既存24表pinsへ接続する。bind・run・coverageではSQLGlotを必要としない。既存99の入口は従来の完全な99件検査を維持し、両suiteを混在させない。

実source試験は `SHIROKUMA_TPCDS_VARIANTS_SOURCE` / `SHIROKUMA_TPCDS_VARIANTS_DATASET`、非空caseは追加の `SHIROKUMA_TPCDS_VARIANTS_SYNTHETIC_DATASET` で明示する。証跡は通常の一時directoryへ保持し、任意の既存parentは `SHIROKUMA_TPCDS_VARIANTS_OUTPUT_DIR` で指定する。通常Makeはdownloadや実データ生成を行わない。

## 補完SQLの照合

| 検証 | 結果 |
|---|---|
| 実SF0.01でのsource/cast/portable | 4本・28行。14b=27、23a=1、24b=0、39b=0 |
| 固定24表の独立engine照合 | 同じ4本・28行がParquet参照・直接Trino・公開APIで一致。ORDER/LIMITと前後のnative UUID/schema/snapshot/metadataも確認 |
| 非空の数値経路 | synthetic 19行・24 Parquetを通常prepareへ入力し、24bのpaid=100、39bのmean=333/cov=√3/翌月をsource/cast/portableで確認 |
| 独立engineの数値経路 | 同じ19行をtyped inline CTEへ展開し、24b/39b各1行がParquet由来の参照契約・直接Trino・公開APIで一致。新tableの登録は不要 |
| 重点試験 | variant moduleは実source・syntheticを有効にして17件全件成功。計測/比較/復旧/実HTTP等はPython 3.9と3.12で各153件全件成功 |

syntheticはDSDGENではなく、空結果だけでは確認できない丸め・float演算を検証するための別caseである。性能実績やTPCのscale件数へ合算しない。参照方言照合は同じDuckDB内の検査で、実Trino・公開APIとの全値照合を独立した層として記録する。

## 計測器負荷の切り分け

前回の200,006行のattemptは、取得完了が直接約101秒・公開約112秒、比較まで含めた全体が約400秒・398秒だった。今回、保存済み約70MBの全結果をread-onlyで再検査し、負荷源を分けた。

| 処理 | 今回の観測 |
|---|---|
| JSON読込とSHA | 0.314秒 |
| 型・footer付きReader | 1.334秒 |
| ORDER検査、容量callbackをnoop | 1.378秒、783 callback |
| unordered全値比較、容量callbackをnoop | 6.923秒、1,598 callback |
| Writer 200,006行 | 1.629秒 |
| Timings 10,000 page | 0.0137秒 |
| 元baselineを含む全体容量走査 | 10標本の中央値0.0929秒/回 |
| local run領域だけの容量走査 | 中央値0.000191秒/回 |

同じ10,000行抜粋の自己全値比較では、毎callbackで全体容量を走査する旧方式が10.106秒/106走査、200ms sampling試作が0.560秒/3走査だった。両方とも全値一致で、元artifactのhashと一時DB清掃を確認した。elapsedは初回forceを除外し最終forceを含み、走査積算は両forceを含む。このoffline対照実験はengine性能の改善を示さない。

主な負荷源は全baselineの繰り返し走査であり、型変換・SQLite・計測event自体を変える根拠は乏しい。改善では期限判定とlocal run peakの検査を全callbackで維持し、全体容量の走査だけを200ms間隔に抑える。最初・送信前・取得/検証完了の境界はforceし、走査後にも期限を確認する。samplingはfilesystem quotaではなく、元の将来peak・drain予約・host予備を維持する。

`CapacitySampler` は成功した走査の完了から200msを数える。失敗をcacheせず例外を保持して次回は即再評価し、並行callbackの全体走査を短いlockで直列化する。送信前forceはdurable INTENTを保存する前に置き、未送信の失敗をUNKNOWNにしない。送信後の失敗・復旧・UNKNOWNの契約は維持する。

各attemptの `capacity_guard` に秒単位の `interval_s`、`check_calls`、`global_requests`、`global_scans`、`global_failed_scans`、`global_scan_s`、`local_peak_check_s` と意味を保存する。run共通のpreflightはこの集計に含めず、走査時間はlock待ちを含めない。local確認時間と並行して重なる場合があるため、engine時間から単純に減算しない。既存Timings v1のevent・HTTP・backend値とinclusiveなartifact時間の意味は変えない。

| 200,006行の実機照合 | 直接Trino | 公開全件API |
|---|---:|---:|
| 取得完了 | 4.550秒 | 7.583秒 |
| 全値比較まで含むattempt | 18.560秒 | 21.642秒 |
| local peak/期限callback | 3,292回 | 3,485回 |
| 全体容量走査 | 61回 / 6.856秒 | 71回 / 7.732秒 |
| local peak確認時間 | 0.616秒 | 0.679秒 |

空tableを含む2表・200,006行の型・全値・重複が、前回と同じParquet参照に一致した。前後のnative UUID/schema/snapshot/metadataも一致し、同じ600秒期限で通過した。新しい常駐service・SQLite設定・型/ORDER/許容差の変更はない。hostの同時負荷やcacheを統制したengine比較ではなく、依然として全体容量走査に6.9〜7.7秒を要する。大規模性能評価の前に、より大きいcampaignで観測負荷とin-flight/drain予約の余力を再確認する。

初回のprivate実機harnessは、Parquet専用venvからnative metadata観測を呼び、PyYAML不足でSQL送信前に失敗した。native観測を既存DataOps環境のread-only子処理へ分けた後、fresh試行で両経路を完了した。既定venvには依存を追加せず、元のFAILED記録も保持した。

## 検証と後続

追加物理占有2TB、host予備256GiB、guest data予備 `max(64GiB,10%)` とroot予備 `max(4GiB,10%)` を維持する。元のVM/state baselineを使い続け、過去のartifact/SDKと今回のsource・venv・結果を重複なく加算する。coordinator heap8GiB/container12GiB、worker heap2GiB/container4GiB、VM16 CPU/96GiB/400GiBを変更しない。

新しい表の登録や旧campaignの更新はせず、成功済みpinsに対するread-only SQLと新campaignの結果を使った。前後の59表のUUID/schema/snapshot/metadata、既存binding/identity/context/control、旧query/receipt/UNKNOWN履歴、既存設定・GitOps差分が一致した。Workbench再起動なし、Pod UID/container不変、再起動/OOM/kill 0、未終結0、一時認証ファイル0、doctor healthy 46項目を確認した。

全体回帰はMake定義160群・5,875件をソース固定で4並列実行し、5,806成功・69 skip・失敗0だった。元159コマンドの内容・順序を維持し、variant試験1コマンドだけを追加した。既存253 Python test file・5,664 methodの削除0と、既定99 contract bytes不変を確認した。

Make完走後の最終差分は、新しいopt-in試験の証跡出力先2箇所を任意env/通常tempfileへ一般化するtest1 fileだけで、production・期待値・method数は不変だった。対象17件は実SDKの明示parent/通常tempfileの両条件で17成功・skip0、stdlibで15成功・既定2 skip。全体回帰をやり直して合算せず、元SHA→2箇所の正確なdiff→最終SHAと対象再検証を別記録として残した。

証拠の保存先はGit外の `shirokuma-variants-meter-2026-11-14`。`meter-analysis-285b16ad/profile.json` / `profile-ab.json` が切り分けと試作対照実験、`variant-sources` が固定sourceと由来、`candidate-variants-review/inventory.json` が未受入14件の固定候補とparse-only観測である。

`campaign/variant-plan` / `variant-bound-queries.json` が補完準備とbinding、`variants-reference` / `variants-direct` / `variants-public` が実SF0.01の結果である。`meter-direct` / `meter-public-all` が200,006行の全値結果とcapacity_guard、`numeric-reference` / `numeric-direct` / `numeric-public` が数値caseである。`numeric-inline-parquet-proof.json` で24表の全schema/全行・19行を元Parquetと双方向EXCEPT ALLで照合し、inline CTEへ渡すdataも確認した。

`inventory-6e28d5e7` / `inventory-2900292f` が前後の59表・台帳・資源。`verify-parallel/plan.json`・160群ログ・`regression-parallel-summary.json` と `compatibility-audit.json` が全体回帰と互換性、`postregression-test-output-fix/summary/evidence.json` が出力先だけの最終差分と再検証である。`verification-summary.json` で最終ソースの検証連鎖・保護対象・認証ファイル0を追跡できる。設計整合・改行・空白・秘密情報ファイル名は `static-checks.log` が検査記録。

prefreeze workerの全体Makeはsandboxのloopback EPERMで中断しており、最終集計へ含めない。元の失敗plan・harness失敗・制限環境logは保持する。今回のsource・専用venv・結果と旧artifactを合わせた元baselineからの物理占有を計上し、最終実機観測のhost空きは約3.097TBだった。所有一時DB/spillは清掃し、成功結果・固定pins・FAILED証拠を保持する。

次は承認variant14件の由来・方言・oracle受入と別scaleの資格確認を整理する。計測器の観測負荷をpilotで再確認し、L1/guest disk拡張と100GB pilot、容量・生成/取込時間・spillの再見積りへ進む。
