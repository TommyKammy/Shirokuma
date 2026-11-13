# Shirokuma／Snowflake／Databricks 大規模性能比較計画

2026-11-13 JST。状態: 計画案。環境調査と一次資料の確認のみ実施し、負荷投入・構成変更・クラウド資源の作成・課金は開始していない。

## 1. 目的と結論の範囲

同じ論理データと処理要求について、Shirokumaが比較対象と同等以上の性能を、宣言した負荷範囲で正しい結果・予期しないエラー0件で提供できるかを判定する。優位・同等以上・劣位・判定不能のすべてを許容し、速いqueryだけを抜き出して結論を出さない。

「同じサイズ名」はハードウェアの同一性を表さない。Snowflakeはwarehouseの種別・サイズ・世代等で資源を定義し、Databricks Pro／Classicはdriverとworkerの構成を公開する。Serverlessは同じラベルでもinstanceが異なる場合がある。このため、比較は次の区分を分け、Snowflakeを含む全製品の物理資源が完全一致したとは証拠なしに記述しない。[Snowflake warehouse](https://docs.snowflake.com/en/user-guide/warehouses)、[Databricks sizing](https://docs.databricks.com/aws/en/compute/sql-warehouse/warehouse-behavior)

| 区分 | 揃える条件 | 判定できる内容 |
|---|---|---|
| A0: 固定資源総量 | 同一cloud／region、CPU architecture、公称vCPU／RAM合計、SSD容量を揃え、各製品の台数を試験中固定する | 単一NodeのShirokumaと複数nodeのDatabricksを比較する場合、配置・networkの差も含む資源総量比較。物理構成の同一とは呼ばない |
| A1: 配置も対応 | 観測可能なinstance型・CPU世代・RAM・SSD・network、coordinator／driverとworkerの台数・配置まで揃える | 実資源の一致を確認できた範囲の比較。Shirokumaの分散配置・identity・復旧対応の先行実装と受入が必要 |
| B: 商用サービス | 同一データ・SQL・負荷、固定warehouse種別／世代／サイズ、同一費用帯 | Snowflake／Databricksを含む、公開設定・実費に基づく比較。A1のハードウェア一致とは区別 |
| C: 製品最適化 | 同一論理データ・処理要求とチューニング工数上限。各製品のnative形式・最適化を利用 | 製品としての性能・運用費。物理レイアウトが同じという主張はしない |

最初の推奨はA0＋B＋公開APIの比較である。A1は配置まで厳密に対応させたい場合の追加設計判断とする。Bの同一費用帯は、同じ会計範囲・稼働窓で実効費用差±10%以内を初期案として本測定前に固定する。サイズの段差で帯に入らなければ、隣接サイズの費用―性能曲線と同一SLOを満たす最小費用を示し、「同一費用」としない。

主比較のSQL engineはShirokumaのTrino、Snowflake SQL、Databricks SQL（Photon等の実設定を保存）とする。Spark／Cometは別実験であり、現在存在しないharnessを利用可能と扱わない。各区分でSQL engineの測定と公開API経由の製品測定を別に出す。Trino直接接続の結果を、Shirokumaの認可・受付・履歴・ページ取得を含む性能として扱わない。

## 2. 現状と事前に解消する制約

以下は2026-11-13の読み取り時点の状態であり、実行直前に再取得する。

| 項目 | 確認した状態と意味 |
|---|---|
| Host／VM | M3 Ultra・32論理CPU・512GiB RAM。専用Colimaは16 vCPU／96GiB／400GiB設定、arm64単一Node |
| Disk | VM共通データfilesystemは約393GiB、空き約147GiB。生成元・Parquet・Iceberg・spillを併存させるTB試験には不足 |
| SQL基盤 | Coordinator 1＋Worker 1、各limits 4 CPU／4GiB、requests 100m／256Mi。CoordinatorはSQL実行に参加しないため、SQL workerは4 CPU上限／4GiB。「4コア占有」の保証ではない |
| Memory | heap 2GiB、query memory全体／node各1GB、headroom 512MB。現在値で100GB〜TBに対応できるとは仮定しない |
| 競合 | 旧Trino／Polaris等と別VMが同居。現状は性能試験用の隔離環境ではない |
| 構成生成・配置 | Worker数0／1、CPU／memory／heap等の固定値があり、全Podが単一Nodeに固定される。provider／復旧も同一Node・engineを照合する。複数worker・大メモリ・分散配置を区別して先行対応が必要 |
| 公開API | 結果上限1〜10,000行に達すると残るbackendページを取消する。HTTP接続timeout30秒はSQL全体のdeadlineとは異なる。打切り時間を全SQL完了時間として測らない |
| bulk／結果転送 | 通常JSON body 10MiB、Stageは1ファイル25MiB／一覧1,000件、Query Exportは16MiB／4,096 receipt。経路ごとの実効上限を確認し、bulk／streamが必要な試験は拡張を先行する |
| 既存harness | `m1_small_load.py`は最大10,000行・5 roundsの小規模ライフサイクル検証。大規模runnerとしては使わない |

根拠: [構成検証・render](../../tools/local_experiment/logical_foundation.py)、[provider](../../tools/local_experiment/logical_provider.py)、[復旧identity](../../tools/local_experiment/logical_execution_recovery.py)、[公開SQL API](../../tools/local_experiment/workbench_server.py)、[query transport](../../tools/local_experiment/query_service.py)、[JSON](../../tools/local_experiment/database_http_json.py)、[Stage](../../tools/local_experiment/db_stages.py)、[Export](../../tools/local_experiment/query_result_exports.py)、[既存runner](../../tools/local_experiment/m1_small_load.py)、[M1小規模負荷の限界](database-p6-7-small-load-2026-11-05.md)。過去のTPC-H／Spark検証記録は正解確認の参考とするが、参照される専用harnessが現checkoutにないため、再実行可能な資産とは扱わない。

## 3. 環境とコンピュートの揃え方

### ローカル予備試験

- 現行4 CPU workerでの限界とAPI overheadを、専用データ・試験窓で先に測る。既存利用と混ぜた結果を基準値にしない。
- 他のShirokuma実験workloadの停止・VM拡張等は、所有者・使用状況を調べたうえで別の実施手順にする。他プロジェクトのVMを自動停止しない。
- 専用benchmark profileを用意し、CPU／RAM／heap／query memory／spill／worker数／resource requestsとlimitsを宣言・検証・再作成できるようにする。単なるmanifest手修正で構成台帳と実体を食い違わせない。
- Residentの固定LOCAL・arm64基盤を維持する。benchmarkの依存は`benchmarks/`配下の専用環境へ隔離し、既定runtimeへ混入させない。

### クラウド本比較の基準案

クラウド指定が未確定のため、AWSの同一regionを基準案とする。利用可能な契約・region・料金に合わせてG0で確定する。追加費用が生じる資源の起動は、この計画作成には含めない。

Databricks Pro／Classicの公開構成を起点とした候補は以下。実アカウントのinstance型・台数を取得し、表と異なる場合は実体で再定義する。`i3.2xlarge`の公称値は8 vCPU／61GiBであり、これをMacの8コアと同等とは扱わない。合計値はdriverとworkerからの計算値で、アプリが全量を専有する保証ではない。[Databricks sizing](https://docs.databricks.com/aws/en/compute/sql-warehouse/warehouse-behavior)、[AWS I3](https://aws.amazon.com/ec2/instance-types/i3/)

| 構成候補 | Databricks Pro／Classic | Shirokuma側の対応案 |
|---|---|---|
| R1 | 2X-Small: driver i3.2xlarge＋worker 1台 | 公称合計16 vCPU／122GiB。A0は同総量の単一Node、A1は同じ型のcoordinator 1＋worker 1へ配置 |
| R2 | X-Small: driver i3.2xlarge＋worker 2台 | 公称合計24 vCPU／183GiB。A0は予算総量を合わせ、A1は同じ型のcoordinator 1＋worker 2へ配置 |
| R3 | Small: driver i3.4xlarge＋worker 4台 | A0は合計資源予算を合わせ、A1は同じdriver型のcoordinator 1＋同じworker型4台。実資源をmanifestへ固定 |

R1から開始し、R2／R3は固定profile・容量・費用を確認して拡大する。R1のA0は例えば単一i3.4xlargeの公称16 vCPU／122GiBが候補になるが、driver／workerを分けた構成とのIO・network差を明示する。R2／R3で同総量の単一Nodeを確保できなければ、そのcellはA0として成立しない。大きいVMを使う場合も、公称物理量とCPU quota／memory上限を区別し、quotaだけの一致を物理割当の一致としない。

A0では単一Node内のcoordinator／worker割当とOS／k3s用の余裕、実効CPU／RAM／IOを測り、topology差を結果に残す。x86比較はbenchmark専用の互換性確認を行い、resident arm64を置き換えない。公式imageの対応architectureとdigestを固定する。

A1は単にWorker数の検証値を変えて開始しない。単一Nodeを前提にした配置、Node UID／engine identity、provider、復旧証拠の拡張をWPとして設計・試験・受入する必要がある。既存のguardを無効化しない。分散Trinoだけを起動できた段階はengine比較に限定し、Shirokuma製品の分散性能・障害復旧の実績としない。

Coordinator／driverを除いたworker資源と、全computeの合計資源を両方報告する。Storage／catalog／control plane／load generatorは別に資源と費用を記録する。Shirokumaだけstorageを同じworkerへ同居させてCPUを奪う構成や、無償の別storageを使って総費用から除外する構成にしない。

現行rendererはstorage／catalogも同じNodeへ固定するため、上記の分離は現在使える機能ではなくG2のprofile／provider実装・受入を要する。現行の同居構成を維持する場合はSQL compute一致のA0とせず、Bの製品比較または独立した「全stack同居」条件として報告する。OS・制御系・storageを含むNode全量をSQL用資源として二重計上しない。単独Trinoを外部storageへ接続しただけの結果はengine比較に限定する。

Snowflake B比較はStandard warehouseの世代・resource constraintを明示し、まずX-Small／Small／Mediumの候補からpilotで費用帯を合わせる。Databricksの同名サイズと対応づけない。固定容量baselineはmin=max cluster=1、resize禁止、QAS無効。新規Gen2ではQASが初期有効となる現行仕様があるため、設定値を明示・保存する。C比較では追加サービスを利用してもよいが、その使用量と費用を含める。[Gen2／QAS](https://docs.snowflake.com/en/user-guide/warehouses-gen2)

## 4. データ・SQL・物理レイアウト

| 段階 | データ | 用途 |
|---|---|---|
| D0 | TPC-H SF1→SF10、TPC-DSの小規模qualification用データ | loader・SQL互換性・oracle・測定境界の確認 |
| D1 | 約100GB級。TPC-H SF100、TPC-DS由来の対応scale | pilotと同時実行ramp。ローカルに収まる場合のみローカル実行 |
| D2 | 約1TB級（SF1000） | 最初の大規模必須判定 |
| D3 | 約3TB級（SF3000） | 大規模必須判定の拡張 |
| D4 | 約10TB級（SF10000） | 1TB／3TBの容量・費用・SLOを確認した後の追加上限試験 |

規模は生成元の概算であり、圧縮後サイズではない。generator版・seed・schema・table別行数・論理量・物理bytes・file数／サイズ・partition／sort・codec・統計・snapshotをmanifestへ保存する。TPC-H全22 query、TPC-DS全99テンプレートと、その版で生成されるvariant／複数statementを列挙し、実行本数を事前固定する。公式の現行版TPC-H 3.0.1／TPC-DS 4.0.0を基準に、toolsの動作・ライセンスをG0で確認して版固定する。これは独自のTPC由来workloadであり、監査済みTPC公式スコアとして扱わない。[TPC現行仕様](https://tpc.org/TPC_Documents_Current_Versions/current_specifications5.asp)

以下のデータ経路を混ぜない。

- 共通lake: 全製品が同一Iceberg snapshot／Parquet file集合を読めることを小規模で確認できた場合のみ実施。各製品のcatalog連携・型・Iceberg仕様の対応を確認する。Databricks foreign Icebergはread-only等の制約があるため、未確認の連携を前提条件にしない。[Databricks Iceberg](https://docs.databricks.com/aws/en/iceberg)
- Native: 同一の論理データをShirokuma Iceberg、Snowflake native、Databricks Delta等へloadし、同じ行数・値を検証する。file layoutの同一性は主張しない。load・compact・統計収集・clusteringの時間と費用も記録する。

Baselineはindex／materialized result／search optimization等の追加最適化を固定し、最適化ありはC比較へ分ける。全製品に同じ調整工数と試行回数の上限を設け、未使用seedの検証queryで過適合を点検する。

大量loadはParquet／object storage等のbulk経路を使う。既存CSV／Base64の全量メモリ生成をTBまで拡大しない。bulk登録・table inventory・認可が未対応なら、実装WPを先行させる。

容量は `生成元＋変換後＋tableコピー＋spill＋一時出力＋snapshot保持＋ログ＋安全余裕` で積算する。小規模実測から必要量を見積もり、最低25%の空き余裕を残す。現在の約147GiB空きで100GB級が収まるとは決めつけない。10TBは全コピーとspillを含む専用storageを確保してから実施する。

## 5. ワークロードと実験の縮約

| 系統 | 内容 | 負荷 |
|---|---|---|
| SQL単発 | scan、join、集計、window、sort、高cardinality、selective query | TPC由来suite全体、並列1 |
| SQL同時実行 | 上記queryの固定mix、異なるseed・parameter | 並列1／4／8／16／32／64、飽和時は128を追加 |
| 到着率 | query完了を待たず送信するopen-loop負荷 | pilot後に全製品共通の絶対到着率gridを固定。各製品自身の処理能力比率は補助指標のみ |
| DataOps | CTAS、増分取込、dbt変換、品質検証、公開・rollback | engine時間と製品workflowの全体時間を別計上 |
| データ特性 | 80/20の偏り、high-cardinality、small files、大結果取得、spill | 標準layoutと分け、変更条件を固定 |
| 継続負荷 | 宣言した定常mix | 2時間予備→8時間→24時間 |
| 障害・過負荷 | query取消、client応答喪失、worker停止、storage／catalog遅延、容量超過 | 正常系の性能測定とは別run |

SQL read-onlyと、物理操作に排他があるDataOpsの409拒否を区別する。通常運転として宣言したmixの必須操作が排他により成功しない場合は、その要求の未達として残す。過負荷専用runの期待した拒否を正常系の成功件数へ加えない。

主SQL suiteは不変snapshotを読む。混合負荷のwriteは別の所有table／copyへ入れ、readの正解を変えずに資源競合を測る。同じtableへのread/write整合性は別caseとし、batch ID・commit／snapshot台帳から許される結果と一度だけの効果を検証する。read-onlyのforeign Icebergを全製品の共通write経路として扱わない。

全組合せを最初から実行しない。D1×R1／Bの候補でpilotを行い、D2ではR1〜R3／Bの選定3段階で単発と並列rampを実施する。D3は最低限の実用構成と上位構成で確認する。D4・C比較・Serverless・A1の分散製品比較は追加枠とする。24時間soakは宣言する代表構成で各製品に実施する。合格を主張する他の構成は、事前登録した独立blockの測定を追加する。

## 6. キャッシュ・測定境界・統計

- 主比較は結果cache OFF。Snowflake／Databricksは`USE_CACHED_RESULT=false`を明示し、session設定とquery historyで実行を確認する。設定は測定する各connection／sessionへ適用し、SQL editorの設定が別のAPI sessionへ引き継がれるとはみなさない。データcacheの温冷は別の軸にする。[Snowflake結果cache](https://docs.snowflake.com/en/user-guide/querying-persisted-results)、[Databricks cache](https://docs.databricks.com/aws/en/sql/user/queries/query-caching)
- `cold`はfresh compute／data cache排除を確認できた場合だけ使う。確認できない場合は「fresh compute・data cache不明」。Databricks Serverlessのremote result cacheは再起動でも残る。Snowflakeのwarehouse data cacheとpersisted resultも別物である。[Snowflake data cache](https://docs.snowflake.com/en/user-guide/performance-query-warehouse-cache)
- 各queryで同じwarm-up手順後に最低5測定を行う。pilotは3 block、本測定は最低5独立block（再起動・時刻を分離）を基準に、製品順・query順をseed付きで無作為化する。primary cohort・重要query・重み・反復数・最大block数・CI方式を本測定前に登録する。
- 並列／到着率試験は各blockで10分warm-up、最低30分の定常測定、全要求の完了またはdeadlineまでのdrainを初期案とする。到着率は同じ絶対値、mix・seed・deadlineも全製品で共通にし、処理できた要求だけで負荷条件を変えない。
- p95はcohort別に十分な件数、p99は最低10,000完了を目標とする。少数の重いqueryにp99を付けず、その場合は全観測値・最大・CIを出す。24時間で10,000件に達しない重負荷は、件数不足を明記し、軽量queryの水増しで代用しない。
- 閉ループの並列数試験と、到着率を固定するopen-loop試験を別集計する。後者は予定到着時刻からの遅延も測り、混雑で送信自体が遅れることを隠さない。

主指標は同じload generatorからの `送信直前→全結果取得・stream検証完了`。受理、queue、compile、engine実行、first row、last row、検証、取消完了の時刻を別に保存する。Databricksの`total_duration_ms`はfetchを除くため、異なる測定境界を同じ値として比較しない。SDK／driver版とpoll／fetch設定を固定し、driver overheadを別記録する。[Databricks query history](https://docs.databricks.com/aws/en/admin/system-tables/query-history)、[Snowflake query history](https://docs.snowflake.com/en/sql-reference/functions/query_history)

clientは測定対象computeから分離し、同一regionで各製品へ接続する。CPU・network・検証処理が飽和していないことを記録する。Shirokumaの認可・受付・履歴を無効化して公開APIの結果を良くしない。製品APIの10,000行上限に達した結果やtruncated結果は完全取得の成功にしない。大結果試験に必要なbulk／stream機能がない場合は、製品として未対応と記録する。

## 7. 正しさ・エラー・性能の合格基準

以下は実行前に確定する判定案。D2（1TB）とD3（3TB）、比較区分・構成・cache状態・負荷mix・並列数ごとに、対Snowflake／対Databricksを独立に判定する。

| 項目 | 判定案 |
|---|---|
| SQL対応・結果 | 必須suite全件対応・全結果一致。未対応／timeout／誤答／途中打切りを除外しない |
| 型と比較 | integer／decimal／date／NULLは厳密比較、timestampはUTCとprecisionを固定。floatだけ事前定義した絶対・相対誤差を使用。順序未定義はmultisetで重複を含め比較 |
| 通常運転の信頼性 | 宣言した負荷範囲と24時間内で、予期しないSQL／HTTP失敗、timeout、OOM／再起動、結果欠損・重複、未解消UNKNOWNが0件 |
| 同等以上の主張 | 主要cohortのレイテンシ比 `Shirokuma / 相手` の95%CI上限≤1.00、成功throughput比の95%CI下限≥1.00。p95が測れるcohortでも同じ上限条件。どちらか跨ぐ場合は判定保留 |
| Query別の確認 | 全queryの比率・CI・遅いqueryを報告する。重要queryが相手の1.2倍を超える場合は総合合格を止め、原因を確認する。全query同等以上という主張には各queryの基準も必要 |
| 優位の主張 | 他の必須指標の同等以上条件も満たし、latency比の95%CI上限<1.00、またはthroughput比の95%CI下限>1.00となった指標・範囲を明記する。10%以上等の実用上の差も併記する |
| 障害・飽和 | 想定した拒否／取消を別集計し、黙った誤答・重複効果・他DB／ownerへの漏えい・復旧後の未解決処理は0件 |

Query別中央値、固定query mixの幾何平均、p50／p95／p99、成功throughput、queue時間、飽和点、最大値、費用を併記する。CIは独立blockを単位とした再標本化等で求め、異なるqueryや同一block内の反復を独立標本と誤認しない。配置・cache等の必須条件が不明／不一致のcellは、該当区分で合格としない。精度不足は判定保留にする。追加測定は事前規定の対称な追加blockまたは別実験とし、優位になるまで一方だけ反復しない。実用同等の許容幅を設ける場合は別の非劣性基準として事前合意し、後から同等以上の基準を緩めない。

oracleは小規模の独立参照結果と全製品の結果を照合して確立し、大規模でも全結果をstreamで検証する。順不同の大結果は外部sort等を使い、先頭pageやサンプル一致だけで正解としない。SQL方言差分は版管理し、Q15のviewをCTEにする等の変更も全製品で意味を合わせる。演算の削除・精度低下・一方だけのLIMIT追加をしない。

「エラー0件」は観測範囲の記述であり、将来の無障害保証ではない。例えば0／10,000件でも、独立試行を仮定する片側95%上限は約0.03%である。総件数・期間・cohort・相関した障害の有無を必ず添える。

## 8. 実行制御と観測

- run ID／case ID／query ID／request ID／SQL版／seed／profile／dataset snapshotを対応づける。送信前intentと受領handleを永続化し、不明応答時にPOSTを盲目的に再送しない。元のID／履歴で照合できなければUNKNOWNとしてrunを保留する。
- 個々のSQL deadlineはD0 120秒、D1 900秒、D2 1,800秒、D3／D4 3,600秒を初期案とし、pilot前に固定する。各製品で同じ条件にし、client／service側の実効値と取消後の停止を確認する。HTTPの30秒timeoutと混同しない。
- deadline後の取消・孤立処理・lock／admission解放を確認してから次runへ進む。自動retryでfirst-attempt失敗を隠さない。意図的な障害runは正常系と分離する。
- CPU／throttling、RSS／heap／GC、spill、scan bytes／files、network／disk、storage／catalog latency、queue／admission、query plan、worker数と再起動を時刻同期して記録する。
- 安全余裕を割るdisk使用、OOM、結果不一致、未解決UNKNOWN、所有範囲外の変更、費用／時間上限到達では新規投入を止める。原因・復旧を確認するまで規模拡大しない。
- 終了時はowned runの処理だけを取消・照合し、未確定履歴を保持する。専用namespace／DB／object prefix／warehouseの所有台帳に基づきcleanupし、既存利用者の設定・データは対象にしない。

## 9. 費用と所要期間

クラウド・region・契約単価・予算は未確定。数字を仮定して有償実行を始めず、D0／D1の実測時間と次式から段階ごとの見積りを作る。

`費用 = compute稼働時間×実単価 + storage + IO／転送 + load／最適化 + catalog／control plane + 起動／idle + 保持・cleanup費用`

Snowflakeのcredit単価、DatabricksのDBU＋VM費、Shirokumaのcoordinator／worker／storage等を同一通貨・時点・契約条件で集計する。ローカルを無料として価格優位を主張しない。サービス総費用とSQL compute費用は別に示し、成功queryあたり費用と失敗を含むrun総費用を両方出す。

全matrixの予算を先に消費しない。各段階で最大wall time／query数／並列数／保管量を固定し、課金遅延を見込む余裕も含めて費用上限内で投入する。予算アラートだけを即時停止保証としない。専用資源の停止手順と取消権限を事前確認する。[Databricks budgets](https://docs.databricks.com/aws/en/admin/account-settings/budgets)、[Snowflake resource monitors](https://docs.snowflake.com/en/user-guide/resource-monitors)

実行件数は `N = Σ(query実本数 × 反復 × 独立block × 製品 × 構成 × データ規模 × cache条件)` から積算する。例えばqueryを121本と仮置きし、5反復×5 block×3製品×3構成×1規模×2 cache条件なら54,450試行となる。平均30秒でも約454時間の逐次実行時間であり、variant／追加statement・warm-up・並列試験・soak・loadは別加算である。A0／Bが同一実設定・同一条件になる結果は再利用し、同じrunを二重計上しない。

準備5〜10営業日はローカルrunner／qualificationの初期目安に限定する。大規模本測定の期間・費用はpilotの実測とcase選定から積算し、A1分散製品対応は別のスコープ確定・見積りを要する。製品間／case間の並列実行はstorage・network・load generatorまでの隔離と余裕を確認できた場合だけとし、通常は順序を変えた逐次blockで比較する。pilot前に完了日や総費用を保証しない。

## 10. 作業順と完了ゲート

| 順序 | 作業単位 | 完了証拠／次へ進む条件 |
|---|---|---|
| P0／G0-L | ローカルprotocol・比較表を固定 | 比較区分、必須query、SLO、case上限、所有資源・停止手順を定義。既存利用との隔離と容量を確認。cloud未確定でもP1へ進める |
| G0-C | 有償cloud実行条件を確定 | cloud／region／契約／費用上限・課金観測・停止権限を確定。cloud資源作成と有償loadの前に必須。ローカル実装の開始条件とは分離 |
| P1／G1 | Dataset／oracle／runner | 全query IDと方言差分、全page検証、失敗分類、durable handle、対称な時刻計測、resume／cancel／cleanupの試験を通過 |
| P2／G2 | 再現可能なbenchmark profileとD0 | 単一Node固定profileのresource／memory／spill、SQL・OS・storage／catalogの配置と資源会計を検証。A0には必要な分離とprovider対応の受入が必須。同居なら別条件へ分類。必要なbulk／全結果取得を実装・受入し、10GB級で全結果一致、計測器の飽和なし、単発・小並列・取消が正常 |
| P3／G3 | D1 pilot | 100GB級で正解・cache設定・全計測・容量／費用を確認し、D2／D3の実行上限を再見積り。有償環境は確定した範囲でのみ開始 |
| P4／G4 | D2／D3本比較 | 単発・並列・到着率rampを独立blockで実行。両baselineに対するCI・未達query・安定運転範囲を確定 |
| P5／G5 | 24時間・障害・最終判定 | 通常系0エラーを観測件数とともに記録。障害系の停止／復旧／重複防止・cleanupを確認し、合格範囲と未達範囲を報告 |
| 追加枠 | D4／C最適化／Serverless／A1分散配置 | 先行結果と追加予算・設計受入に基づき別caseとして実行。A1はprovider／identity／復旧の対応完了が必須。各比較区分へ混ぜない |

成果物はprotocol／environment／dataset／query manifest、raw attempts、結果照合、query profile／資源時系列、費用台帳、cleanup記録、query別比較表と分布・scaling曲線、合否matrixとする。大きなデータ・資格情報・生ログはGit外へ保存し、repoには再現手順・集計結果・artifact参照だけを置く。

最初に着手するのはP0とP1の設計・実装である。現行小規模runnerの上限値だけを増やして大規模比較を開始しない。benchmarksの実装は通常のCommander／Worker手順と重点試験・レビューで進める。この計画では追加課金・外部公開・常駐基盤の再設計を承認済みと扱わない。
