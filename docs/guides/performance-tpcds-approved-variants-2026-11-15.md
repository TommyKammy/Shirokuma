# 性能試験の準備: TPC-DS承認variant14件の由来・方言・結果照合

2026-11-14 JST着手、2026-11-15 JST更新。[対応範囲と計測器負荷](performance-tpcds-variants-instrumentation-2026-11-14.md)で候補とした14件を、原Spark実行と照合する。既存99 SQLとFlink由来4 iterationは維持する。

2026-11-15 追記: 後続の[stage上限の正式適用](performance-stage-limit-2026-11-15.md)でcoordinatorの `max_stage_count=400` を正式設定へ適用し、14a standaloneは直接Trino/公開APIとも100行、正式14件suiteは両経路とも14/14・903行で参照と一致した。以下の348>150と11件未実行は当時の失敗証拠として保持し、成功へ読み替えない。適用後は設定bytesを復元し、stage annotation/propertyを除去した。

## 採用sourceと検証の範囲

対象は5a / 10a / 14a / 18a / 22a / 27a / 35a / 36a / 51a / 67a / 70a / 77a / 80a / 86a。Apache Spark v4.0.1、commit `29434ea766b0fc3c3bf6eaadb43a8f931133649e` の `sql/core/src/test/resources/tpcds-v2.7.0` を固定し、14 raw fileのhash・URL・由来を保存した。[固定source](https://github.com/apache/spark/tree/29434ea766b0fc3c3bf6eaadb43a8f931133649e/sql/core/src/test/resources/tpcds-v2.7.0)

14のfamily名はTPC-DS v4 Appendix Cと対応する。ただしAppendix Cは一覧でSQL本文を含まず、この旧具体SQLと現行v4 templateの完全同一性は確認していない。77aには、v2.9改訂履歴にある `cr_call_center_sk` のSELECT/GROUP BY追加が現rawに存在しないことも確認した。70aは現rawで括弧欠落を再現しなかった。sourceを推測で現行版へ修正しない。[TPC-DS v4.0.0](https://www.tpc.org/TPC_Documents_Current_Versions/pdf/TPC-DS_v4.0.0.pdf)

今回確認する同値性は、固定したvariant原文を原Sparkで実行した結果と、そのDuckDB/Trino向け移植の結果である。元functional queryとの一般同値性、現行v4 templateの完全同一性、公式TPCスコアを別の主張として区別する。

同commitの対応base 14件も保存した。14aの明示的な対応先はbase14bで、他は同番号のbaseが候補となる。13組は固定parameterが異なり、変更せずに結果差を意味の差とは判定できない。同条件で比較できた36aとbase36は、実SF0.01のgroup/rankキー60行が一致しても、gross_margin全60行の厳密な値は一致しなかった。variantはDECIMAL(38,11)、baseはDECIMAL(37,20)で、1/3の小caseでも差を再現した。許容差を広げて同値としない。

22aの平均の平均とbaseの直接AVGにも一般入力で反例がある。これは採用sourceを忠実に移植する検査と、元queryに対する代替式の証明が異なる理由であり、DSDGEN全条件での非同値をこの小caseだけで断定しない。

raw・effective・portable SQL bodyはGitへ同梱しない。repoは固定source hash・構造contract・変換由来を持ち、callerが供給するraw sourceから準備する。

## 原Spark oracle

既存のofficial `docker.io/library/spark@sha256:82d7d3c2315a8b826d83485728013c01d1f2542708c83c0c641a363924d065b7`、Spark 4.1.3 / JDK17を短命containerで使う。SQL sourceのrelease 4.0.1、resource v2.7.0、実行engine 4.1.3を混同しない。hostへJava/PySparkをinstallせず、常駐serviceも追加しない。

| 原実行profile | 固定値 |
|---|---|
| ANSI | true |
| timezone / caseSensitive | UTC / false |
| shuffle partitions | 2 |
| decimal allowPrecisionLoss | true |
| enforceReservedKeywords | false |
| allowNegativeScaleOfDecimal | false |
| storeAssignmentPolicy | ANSI |

Spark 4.xのANSI既定と、同sourceを扱うANSISuiteの設定を確認した。原test JVMのtimezoneはAmerica/Los_Angelesだが、今回の実測profileはUTCを明示する。対象24表と14queryのDATE処理をこのprofileで実行する。[Spark 4.0.1 ANSI](https://spark.apache.org/docs/4.0.1/sql-ref-ansi-compliance.html)

containerは2 CPU / 6GiB、driver3GiB、network/UI無効、read-only rootfs、own tmpfsを使った。24 Parquet約3.86MBと14 raw SQLをstdin tarで渡し、結果を回収後にown containerを除去する。既存VM・Trino資源やmount設定を変更しない。

原Spark14件はすべて成功し、合計903行だった。内訳は5a100 / 10a0 / 14a100 / 18a100 / 22a100 / 27a100 / 35a51 / 36a60 / 51a100 / 67a100 / 70a3 / 77a7 / 80a22 / 86a60。`benchmarks/local_perf/spark_reference.py` を版管理し、正式bundle `versioned-spark-reference-final-aa4ea340/out/reference` で全input/resultのSHA、Spark schema、8設定、engine/image由来、JSONL footerを保存した。reportのSHA256は `5f7ccb8163a5e2c40a718702b3145d27bc37932eff41e996026e2a86de074168`。

exporterはPySparkを原実行時だけ遅延importする。既存SparkContextの暗黙再利用を拒否し、独自context・job group・一時directory・timeoutを管理する。callerのjob local propertiesは失敗時も戻し、失敗bundleは成功へ書き換えず残す。format 1/2双方の宣言file一覧、全schema/行数/SHAを実行前後で検査する。bundleのvalidatorはPySparkやSQLGlotなしで使える。

## 方言と型の保持

単純な13 raw/77a識別子修正のDuckDB実行は、行数903が同じでも原Sparkと一致しなかった。NULL順序とLIMIT集合、AVG/除算のDECIMAL精度を分けて修正する。

- Sparkの省略NULL順序はASC FIRST / DESC LASTとして各ORDERへ明示する。
- SQLGlotのSpark parseが作るTryCastとsafe divisionは、ANSI=trueの原CAST/除算へ合わせる。NULLIFや寛容なcastを暗黙に加えない。
- 18aの35 AVGはDECIMAL(16,6)、27aの9 decimal AVGはDECIMAL(11,6)へ明示する。
- 36aの3除算は、SparkのUNION共通型DECIMAL(38,11)をRANK/ORDERより前に保持する。
- 最終ordinal projectionを原Spark schemaへcastし、列名・precision・rankのinteger型も固定する。

raw Spark、effective body、canonical effective-source、portableのSHAと変換由来を分ける。effective-sourceをDuckDBで実行したことを、raw sourceをSpark以外で実行した証拠とは扱わない。既存のsource/castが同じSQLになる場合も、独立した根拠は原Sparkとの比較である。

この方法でSF0.01のSpark/effective/portableは14件・903行の型・全値・重複・ORDERが一致し、source/portableのLIMIT境界peer全値監査も成功した。ただしDuckDBのdecimal平均・除算の内部計算にはDOUBLEが使われるため、全データの代数的同値証明としない。準備対象datasetごとに原Sparkreferenceとの比較を必須にし、異なる入力で一致しなければ資格確認を失敗させる。

DECIMALと整数は厳密比較し、浮動小数列だけ絶対/相対許容差を各 `1e-8` 以下に制限する。今回のprepareとnative照合もこの値を使い、DECIMAL差を許容差で隠していない。

非空/境界の別caseとして、10aのEXISTSへ重複saleを含む13行を渡して6つのcntが各2、decimal AVG `[0,.01,.01]` が.006667、全NULLがNULLを確認した。36aのゼロ分母はANSIのDIVIDE_BY_ZEROを期待失敗として保持し、NULLへ変更しない。これらはsyntheticでありTPCのscaleや性能実績へ加算しない。

## 準備・binding・実機検証

SQLGlot 30.31.0を持つ専用環境で `prepare-tpcds-approved` を追加した。既存99 SQL、Flink由来4 iterationとは別suiteで扱い、coverageは99+4+14=117を列挙する。この数を全native queryの合格数にはしない。

```sh
rtk proxy "$PERF_PYTHON" -m benchmarks.local_perf prepare-tpcds-approved "$CAMPAIGN" "$DATASET" approved-plan --source-dir "$APPROVED_RAW_SOURCE" --spark-reference "$SPARK_REFERENCE" --timeout 300
rtk proxy "$PERF_PYTHON" -m benchmarks.local_perf bind-tpcds "$CAMPAIGN/approved-plan/queries.json" "$SNAPSHOT_PINS" "$BOUND_MANIFEST"
```

`DATASET` は成功した所有dataset、`APPROVED_RAW_SOURCE` は固定commitの14 raw file、`SPARK_REFERENCE` はそのdatasetから原Sparkで作ったfresh bundleを指定する。exporterの引数は `dataset source_dir output --timeout 600 --query-timeout 60 --max-rows 1000 --image-receipt receipt.json` であり、上記のhost用PythonへPySparkを追加する手順ではない。原Spark runtime内で実行する。今回のstdin tar/container起動・終了のhost側手順はGit外の `versioned-spark-reference-final-aa4ea340/host.py` に保存した。

prepareは原Spark、effective-source、cast、portableの結果を型・全値・重複・順序まで照合する。成功したcampaign所有entry、plan、全manifest本文、14 source、dataset file、Spark bundle、比較reportと42+14の全resultが実在して一致した場合だけ受入証拠を作る。bindと新規runもこれを再検査し、未実行・失敗・欠落・改変・別dataset・偽装wrapperを拒否する。frozenな既存attemptのreconcile/cancelはこの可変sourceを前提にせず、証拠が失われても終了確認を妨げずに再送を避ける。既存99/4や汎用SQLの契約は維持した。

実SF0.01・24表の既存snapshotを固定して次を確認した。

| 検証 | 結果 | 判定範囲 |
|---|---|---|
| 原Spark / prepare / DuckDB reference | 14件・903行が一致 | 固定variant原文に対する当該datasetの移植受入 |
| 正式suiteの直接Trino | 5a/10a成功後、14aでFAILED。残り11件は未実行 | suite全件成功ではない |
| 個別case監査の直接Trino | 13件・803行一致、14aは既知の終端失敗を参照 | 正式suiteを変更せず残りcaseを監査 |
| 個別case監査の公開API | 13件・803行一致、14aを実行してFAILED | 各caseは同じ資格確認・SQL・snapshotに対応 |

14aは両経路ともTrinoの `QUERY_HAS_TOO_MANY_STAGES`（code 40）、348 stagesに対して許可150で計画時に停止した。数値不一致・OOM・未解消UNKNOWNは観測していない。正式suiteの失敗と11件の未実行を保持し、個別監査を正式suite成功へ読み替えない。公開APIでの正式14件suiteは未実行である。stage上限やSQLをこの検証中に変更しなかったため、native14件全体の受入とG1完了は未達である。

非空の10aと36aの1/3 caseは、literal CTEによる別のsynthetic manifestで参照・直接Trino・公開APIの3経路を照合した。10aは1行の6cntが全て2、36aは3行のgross_marginが全て `.33333333333`。zero分母は別のfresh campaignで両native経路が終端FAILEDになり、`NUMERIC_VALUE_OUT_OF_RANGE`（code 19、InfinityをDECIMALへcastできない）が得られた。SparkのDIVIDE_BY_ZEROと同一error codeとは主張せず、いずれも算術エラーをNULLへ変えないことを確認した。

private witness harnessには、期待したlocal zero失敗のcatch後にreport用変数をattempt pathへ再利用した誤りがあった。このためlocal-onlyの `witness-q36_zero-reference` receiptを壊し、後続native送信前に停止した。破損receipt・元FAILED run・tracebackを保持し、main campaignを再開せず `zero-campaign` に分けてnativeの終端を確認した。正式planやnative receipt、入力・snapshotは変更されていない。この先行witness phase全体を成功扱いにはしない。

全体回帰はMake定義162群を4並列で実行し、5,940件中5,866成功・74 skip・失敗0。元160コマンドの内容・順序と既存99/4 metadata bytesを保持し、実行前後の全対象source hashが一致した。新しい実source有効試験26件、exporter関連試験と既存補完17件も確認した。

## 資源と証拠

追加物理占有2TB、host予備256GiB、guest data予備 `max(64GiB,10%)` とroot予備 `max(4GiB,10%)` を維持する。元のVM/state baselineを継続し、過去のartifact/SDKと今回の入力・結果を加算する。coordinator heap8GiB/container12GiB、worker heap2GiB/container4GiB、VM16 CPU/96GiB/400GiBを変更しない。

Git外の `shirokuma-approved-variants-2026-11-14` は着手日を名前に保持する。`inventory-0c5cc17b` が着手前の59表・台帳・資源、`provenance-analysis` が公式仕様・source由来・AST差分、`base-pairs-source` がbase対応とparameter、`spark-oracle-c904a0ac` / `spark-oracle-analysis-9444903f` が原実行と式型、`architecture-spark-prototype-d55ef6f1` がeffective/portable prototype、`spark-witness-07cd7f47` が非空・丸め・ゼロ分母・base36の別caseである。

`campaign/approved-plan` / `approved-bound-queries.json` が正式受入とbinding、`approved-reference` が14件の参照結果、`approved-direct` が正式suiteの失敗である。`case-audit-summary.json` と `case-{direct,public}-*` が分母14を保った個別監査、`approved14-{native,public-native}-failure.json` がstage上限の実engine証拠、`private-witness-harness-error.json` と `zero-campaign` が先行harness誤りとfreshな期待失敗の区別になる。`regression-parallel-summary.json` と162群のlogが全体回帰であり、`verification-summary.json` / `qa-final-oracle.json` / `compatibility-audit.json` に最終確認を残す。

private harnessの先行API/型構築失敗も残し、成功したfresh試行と区別する。元59表と旧部分取込、固定snapshot・過去履歴・認証contextを保持する。Workbench PID4113とVM/engine資源を維持し、nativeのOOM/再起動0、未解消実行0、doctor healthy 46項目を確認した。所有container・一時DB/spill・認証fileは終了時に除去し、成功結果・失敗証拠は残す。host空きは最終実機観測で約3.095TBだった。

次はcoordinatorのstage上限を正式な設定・復元手順へ含め、資源影響を確認して14aと正式14件suiteを再検証する。その後に別scaleの原Spark資格確認と計測器負荷、L1/guest disk拡張、100GB pilotへ進む。今回の小規模照合を大規模性能や元functional query全14件の一般同値性の実績に含めない。
