# Mac Studio上のShirokuma大規模性能・安定性試験計画

2026-11-14 JST改訂。状態: P0固定、P1のTPC-H実機照合、TPC-DS固定99 SQLの実機照合と計測境界を確認。TPC-DSは一時coordinator heap8GiBで24表・277,976行と両経路の各99本・3,928行が一致し、検証後に基準構成へ戻した。旧2GiB試行のOOM／UNKNOWN cleanupは履歴と停止証拠を保持する。Mac Studio内の実測を主目的とし、Snowflake／Databricksは公開値による粗い参考比較とする。[初期準備](performance-preparation-2026-11-13.md)、[接続qualification](performance-connections-2026-11-13.md)、[TPC-H照合](performance-tpch-2026-11-14.md)、[TPC-DS・計測と実行条件](performance-tpcds-timings-2026-11-14.md)に証拠とG1残件を記録する。次は旧UNKNOWNの復旧、正式なcoordinator資源profile、bulk取込／全結果API・残るvariantを整えてからP2以降の大規模負荷・VM拡張へ進む。

## 1. 目的・実施範囲・承認

Mac Studio M3 Ultra／512GB RAM／4TB SSD上で、Shirokumaが正しい結果・予期しないエラー0件で扱えるデータ規模、SQL性能、同時実行数、継続運転範囲とボトルネックを実測する。CPU／RAMの割当を増やした効果と、SQL engineから公開APIまでの時間差も測る。

所有者は、このホストの空き容量が許す範囲で、検証にホスト資源を利用することを明示承認した。専用Shirokuma ColimaのCPU／RAM／disk拡張、Shirokumaの試験用profile、所有する一時データの作成・削除・再作成を、この範囲の実施手順として具体化する。既存の4 CPU／4GiB workerを試験上限とはしない。同じ範囲で再承認を待たず進める。

実行対象はローカルのShirokumaだけとする。Snowflake／Databricksの契約・アカウント・認証情報・予算を開始条件にしない。旧案のクラウド実測、同資源／同費用での直接比較、x86環境、3TB／10TB試験、物理分散配置を作業順・必須ゲートから外した。公開資料の参照・必要なOSSの取得に有償サービスを導入しない。

追加回答の「まず既存のローカル環境のみ」を実施前提とする。将来Snowflakeを利用する場合の事業想定はAWS東京リージョン・月額約70万円であり、公開情報のregion／費用条件を読む背景として記録する。今回の試験予算やクラウド実行枠にはせず、月額だけからwarehouse規模・同時実行数・性能を換算しない。

| 区分 | 方法 | 結論の範囲 |
|---|---|---|
| 主試験: Shirokuma製品 | 認可・受付・履歴・全結果取得を含む公開APIで測定 | このホスト・構成・データ・負荷で達成した処理能力と安定性 |
| 補助試験: engine／構成差 | 同じデータ・SQLでTrino直接経路とAPI、資源profile間を比較 | API overhead、CPU／memory増強効果。直接経路だけの成功を製品の成功としない |
| 参考比較: 公開値 | Snowflake／Databricksを測定した当事者の原資料を読み、対応するSQL・規模・指標を並べる | 条件差を伴う粗い位置づけ。統計的な同等以上・同資源・価格優位は判定しない |

## 2. 現状と先行作業

2026-11-13の読み取り時点。実行前に再取得し、環境manifestへ保存する。TBは10^12 bytes、GiB／TiBは2進単位として区別する。

| 項目 | 確認した状態と意味 |
|---|---|
| Host | M3 Ultra、32 CPU（Performance 24＋Efficiency 8）、512GiB RAM。vCPUをP-core占有とみなさない |
| Host SSD | filesystem約3.6TiB、空き3,105,012,654,080 bytes（約3.105TB／2.824TiB）へ増加。公称4TBと現在の空きを区別する |
| 専用Colima | `mac-studio-solo`、16 vCPU／96GiB／400GiB設定、Linux/arm64単一Node |
| Guest disk | data filesystem約393GiB・空き約146GiB、rootは別20GiB・空き約15.7GiB。host空きだけではTB試験に進めない |
| SQL基盤 | Coordinator 1＋Worker 1、各limits 4 CPU／4GiB、requests 100m／256Mi。CoordinatorはSQL実行に参加しない。heap 2GiB、query memory全体／node各1GB、headroom 512MB |
| 競合 | 旧Shirokuma実験workloadと別プロジェクトのVM（4 vCPU／8GiB）が稼働。同じhost／SSDを共有している |
| 構成生成 | Worker数0／1、資源・heap等の固定値があり、storage／catalogも単一Nodeに固定。大きな単一worker用profileを先行実装する |
| 公開API | 結果上限1〜10,000行に達すると残るbackendページを取消。HTTP接続timeout30秒はSQL全体deadlineと別。打切り時間を完了時間にしない |
| 転送上限 | 通常JSON 10MiB、Stage 1ファイル25MiB／一覧1,000件、Query Export 16MiB／4,096 receipt。実効上限を経路ごとに確認し、必要なbulk／streamを先行整備する |
| 既存harness | `m1_small_load.py`は最大10,000行・5 roundsの小規模検証。上限だけを増やして大規模runnerにしない |

根拠: [構成検証・render](../../tools/local_experiment/logical_foundation.py)、[provider](../../tools/local_experiment/logical_provider.py)、[復旧identity](../../tools/local_experiment/logical_execution_recovery.py)、[公開SQL API](../../tools/local_experiment/workbench_server.py)、[query transport](../../tools/local_experiment/query_service.py)、[JSON](../../tools/local_experiment/database_http_json.py)、[Stage](../../tools/local_experiment/db_stages.py)、[Export](../../tools/local_experiment/query_result_exports.py)、[既存runner](../../tools/local_experiment/m1_small_load.py)、[小規模検証の限界](database-p6-7-small-load-2026-11-05.md)。過去のTPC-H記録は正解確認の参考とするが、現checkoutにない専用harnessを再実行可能な資産とは扱わない。

## 3. ホスト資源を使う構成

単一Colima／単一k3s Node／coordinator 1＋worker 1を基本とする。storage・catalogも同じhostに置いた製品全体を測り、CPU／RAM／SSD／VM overheadを含めて報告する。複数workerや物理分散対応を開始条件にしない。

| Profile宣言値 | VM全体 | 主な用途 |
|---|---|---|
| L0: 現状 | 16 vCPU／96GiB、workerは現行4 CPU／4GiB | 1〜10GBで現状の限界と互換性を記録 |
| L1: 拡張 | 24 vCPU／192GiB | 次に適用するprofile。100〜300GBのpilot、worker16 CPU／128GiB、heap96GiB |
| L2: 大メモリ | 24 vCPU／384GiB | L1とCPU数を揃え、worker16 CPU／256GiB、heap192GiBでmemory効果とTB級を確認 |
| L3: 上限探索 | 28 vCPU／384GiB | worker20 CPU／256GiB。host・他VM・計測器の余力を確認した場合だけ採用 |
| L3-extra: 追加上限 | 30 vCPU／416GiB | worker22 CPU／288GiB、heap216GiB。競合が小さい場合だけ採用 |

P0では次に適用する対象をL1に固定し、[version 1 protocol](../../benchmarks/local_perf/protocol.json)に宣言値を保存する。今回VMを変更したことは表さず、G2で実機への適用・allocatable・復旧を検証する。Coordinator、SeaweedFS、Polaris／PostgreSQL、OPA、k3s、OSに、L1は合計8 vCPU／64GiB、L2は8 vCPU／128GiBを予約する。load generatorはhost側に別枠を置く。各Podへの内訳はG2で合計予約枠内に設定し、requests／limitsと実消費を照合する。

Trinoのheap、query memory全体／node、headroom、off-heap、spillの上限は一組で設計・検証する。L1はheap96GiBに対しquery memory64GiB＋headroom24GiB、L2はheap192GiBに対し128GiB＋48GiBを宣言値とし、G2で版ごとの制約・並列時の総量・実行時metricsと適合を確認する。CPU／memoryを一度に変えた結果と、片方だけ変える効果確認を区別する。

Spillはすべての演算を救済できる機能ではない。実際に使うTrino版の対応演算・設定・上限を小規模で確認し、SSDが空いていれば必ず実行できるとは仮定しない。fault-tolerant execution／exchange導入が必要になれば、別caseの設定・復旧・容量を検証してから採用する。[Trino spill](https://trino.io/docs/current/admin/spill.html)

30 vCPU／416GiBは目標値ではなく追加候補。別VMが動く状態ではhost／load generatorを含むCPUの過剰割当があり得るため、競合が大きければ24または28 vCPUへ戻す。RAMはhost OS用64GiBを初期予備とし、他VM・load generatorの予算を加えて512GiB内に収め、pressureを見て調整する。別プロジェクトの停止・削除は行わない。試験窓では所有するShirokumaの競合workloadだけを整理し、停止前の状態と復元手順を残す。

既存VMの拡張可否、virtual diskとguest filesystemへの反映、公式imageのarm64対応とdigest、config再生成、復旧identityを先に確認する。対応できない場合は同じhost内のShirokuma専用試験VMを代案とし、disk copyを含む容量内で一つずつ稼働する。専用profileとして通常LOCAL設定と切り替え可能にし、guardの解除やmanifestだけの手修正で代替しない。benchmark依存は`benchmarks/`の専用環境へ隔離する。

## 4. 2TB上限とデータ規模

「2TBまで」は、保守的に**試験が追加で占有する物理SSD容量の上限2,000,000,000,000 bytes**として計画する。論理データ量も最大2TBまでとするが、2TBのtableに加えて中間物を無制限に作る意味にはしない。圧縮率・spill・空き次第で、実施できる最大scaleは下がる。

実行開始時の有効な追加容量予算は、`B = min(2TB, hostの実空き − host予備領域)` とする。host予備は256GiBに固定する。空き更新後は約2.83TBが予備差引後に残るため、従来の上限2TBを満額利用できる。新しい空き容量だけを理由に試験上限を3TBへ自動拡大しない。他の書込みが増えれば有効容量は再計算する。Guest dataにも最低`max(64GiB, filesystem容量の10%)`、rootは`max(4GiB, root容量の10%)`の空きと十分なinodeを残す。VMの公称diskサイズやAPFSのpurgeable表示だけで利用可能としない。

会計を二重に足さない。host側では試験開始前のbaselineから増えたVM imageの**実割当**と、重複しないhost直置き試験ファイルを合計する。guest内のParquet／Iceberg／spillはimageの内訳であり、同じbytesをhost会計に再加算しない。削除してもimageの割当やsnapshotが解放されない場合は使用中として扱い、host実空きも別に監視する。campaignの途中でbaselineを取り直して使用量を0へ戻さない。

| 段階 | 論理データ規模の候補 | 実施条件 |
|---|---|---|
| D0 | TPC-H SF1→SF10、小規模TPC-DS | 全SQL・正解・計測・取消・cleanupを確認 |
| D1 | 約100GB、TPC-H SF100等 | 基本pilot。圧縮率・load時間・最大占有を実測 |
| D2 | 約300GB、TPC-H SF300等 | 並列ramp、heap／spill／IOの限界を確認 |
| D3 | 約1TB、TPC-H SF1000／TPC-DS対応scale | 大規模の主目標。生成中・測定中の最大占有がBとguest空き条件に収まる場合に実施 |
| D4 | 1TB超〜最大2TBの論理量 | 先行実測から容量内と確認できるscaleのみ。生成器が対応する派生workloadとし、公式scale外は明記 |

1TB到達や論理2TBを合格の必須条件とはせず、容量内で到達した最大scaleと到達できない理由を成果とする。3TB／10TBは本計画の対象外。TPC-HとTPC-DS、各scaleは同時保管せず、結果・manifest保存後に所有データを片付けて順番に生成する。

各段階のpeakを `生成中間物＋Parquet／Iceberg＋必要なwrite先＋spill＋検証用sort／出力＋保持snapshot＋ログ` で見積もり、測定誤差・取消中の増加も予約する。生成はchunk／streamで行い、元の非圧縮データ全量・複数形式・旧scaleを併存させない。load・compact・rollbackの一時複製も予算へ入れる。中間物は検証済み単位で解放し、データ経路をroot／container ephemeralへ流さない。

各batch前に、in-flight処理の最大追加量と取消・drain余裕を含めて空きを確認する。生成、spill、write先、検証出力、ログの個別上限を決め、Bに到達してから止める設計にしない。上限前に投入停止・所有処理の取消・停止照合を行う。容量不足は「未実施／容量上限」、実行中のOOMや予期しないENOSPCは失敗として区別する。

## 5. SQL・データとワークロード

主engineは現在のTrino。保存先はShirokumaのSeaweedFS／Iceberg／Polarisとし、別engineの常駐導入はしない。TPC-H全22 queryを最初に整備し、続いてTPC-DS全99テンプレートと生成されるvariant／複数statementを列挙する。全体の実行本数・SQL方言差分を版管理し、未対応を黙って除外しない。TPC公式仕様とtoolsの版・利用条件を確認し、TPC由来の独自試験として記録する。[TPC現行仕様](https://tpc.org/TPC_Documents_Current_Versions/current_specifications5.asp)

generator版・seed・schema・table別行数・論理量・圧縮物理bytes・file数／サイズ・partition／sort・codec・統計・snapshotをmanifestへ保存する。Baselineは再現可能な標準layout、compact／sort／統計等の調整後は別caseとする。未使用seedの検証queryも用意し、特定queryだけに合わせない。bulk登録・認可が必要な経路は機能整備を先行する。

| 系統 | 内容・負荷 |
|---|---|
| SQL単発 | scan、join、集計、window、sort、selective query、高cardinality。全suiteを並列1で測る |
| 同時実行 | 同じ固定mixを並列1／4／8／16／32／64。64以上は飽和確認に必要な場合だけ追加 |
| 到着率 | 完了を待たず投入するopen-loop。pilot後にprofile共通の絶対到着率gridを固定し、queue増大・飽和点を測る |
| 製品workflow | CTAS、増分取込、dbt変換、品質検証、公開・rollback。engine時間と全workflow時間を分ける |
| データ特性 | 80/20の偏り、small files、大結果取得、spill。標準layoutとは別case |
| 継続負荷 | 2時間予備→8時間→代表構成で24時間。read中心と必要なDataOpsを含む定常mix |
| 障害・過負荷 | query取消、応答喪失、worker停止、storage／catalog遅延、設定上限超過。正常系とは別run |

物理操作の排他によるDataOpsの409拒否も、通常mixの必須操作が成功しなければ未達として残す。過負荷専用runで期待した拒否を成功throughputに加えない。主SQLは不変snapshotを読み、混合writeは容量予算内の別所有tableへ行う。同じtableのread/write整合性はbatch／commit台帳で期待値を定義した別caseにする。

全scale×全profile×全負荷を実施しない。D0でL0／L1を確認し、D1でL1／L2と必要ならL3をpilot、D2／D3は選定した実用構成・上位構成で全suiteとrampを測る。D4は容量が残る場合の追加。24時間試験は最大scaleを必ず使うのではなく、全結果検証・混合write・長時間spillが収まる代表scale／profileを事前に宣言する。

## 6. 計測と再現性

- 主指標は`送信直前→全結果取得・stream検証完了`。受付、queue、計画、engine、first row、last row、検証、取消完了を別記録する。公開APIとengine直接経路を同じ図の別系列にする。
- 結果cacheを無効化または使用していないことを確認する。warm dataとfresh processを分け、VM／Trino再起動だけで全cacheが消えたとしない。host／guest page cacheまで排除を確認できなければ「data cache不明」とする。global cache purgeの設定変更を前提にしない。
- 各query最低5測定、pilotは3独立block、本測定は最低5独立blockを初期案とする。profile／query順をseed付きで変え、warm-up、SQL、snapshot、deadline、最大反復数を測定前に固定する。比較条件が異なるrunを一緒に平均しない。
- 並列／到着率runは10分warm-up＋最低30分定常測定＋drainを初期案とする。予定到着時刻からの遅延も測り、混雑で送信自体が遅れる影響を残す。
- Query別中央値、全suite所要時間、p50／p95／p99、成功throughput、queue、最大値と信頼区間を出す。p99はcohortごとに10,000完了を目標とし、少数の重いqueryは全観測値・最大・CIを出す。軽いqueryで件数を水増ししない。
- CIは独立block単位の再標本化等を使う。異なるqueryや同blockの反復を独立標本とみなさない。精度不足は判定保留とし、追加blockは事前規定に従う。

load generatorはhost上で専用processとして動かし、VMとはCPU／memory予算を分ける。外部hostを必須にせず、単一hostの共有CPU／SSDという制約を明記する。host／guestのCPU、memory pressure、swap増加、heap／GC、throttling、spill、disk／network、storage／catalog、queue、再起動、計測器消費を時刻同期して収集する。利用可能なら温度・thermal状態も残し、不明なら不明とする。計測器が飽和したrunは別分類にして再設計する。

CPU100%は限界測定の一部であり、それだけで停止しない。hostの強いmemory pressure、継続的swap増加、応答悪化、予備diskの割込み、制御不能なログ増大は投入停止条件にする。最大負荷でもOS・操作・取消用の資源を残す。

## 7. 正しさ・安定性・達成能力の判定

本計画ではSnowflake／Databricksに勝つことを合格条件にしない。各scale／profile／cache状態／負荷mixについて、次を判定する。

| 項目 | 判定 |
|---|---|
| SQL対応・結果 | 宣言した必須suite全件対応・全結果一致。未対応／timeout／誤答／打切りは失敗または未達として残す |
| 型 | integer／decimal／date／NULLは厳密比較、timestampはUTCとprecision固定。floatは事前定義の絶対・相対誤差。順不同は重複を含むmultiset比較 |
| 通常運転 | 宣言した負荷範囲・24時間で、予期しないSQL／HTTP失敗、timeout、OOM／再起動、欠損・重複、未解消UNKNOWNが0件 |
| 応答性能 | pilotで用途別に固定するp95／deadline／queueのSLOを満たす最大同時実行数・到着率を確定する。閾値は本測定後に緩めない |
| 構成効果 | 同一scale・SQL・cache・負荷でL1／L2等を比較し、時間・throughput比と95%CI、CPU／RAM増加量を併記。速くならない場合も結果として報告 |
| 容量限界 | 到達scale、物理peak、spill余裕を示す。未実施、容量で停止、誤答・OOM等の失敗を区別 |
| 障害・飽和 | 期待する取消／拒否を別集計。誤答・重複効果・他DB／ownerへの漏えい、復旧後の未解決処理は0件 |

小規模は独立参照engineや既知の参照結果と照合する。参照engineはbenchmark専用環境の一時用途に限定する。大規模も全結果をstream検証し、必要な独立参照処理は時分割・同じ容量予算内で行う。参照SQLの対応とoracleの完成をG1で確認し、COUNTやサンプル一致だけで全結果正解としない。Q15のview→CTE等の差分も意味を維持する。

公開APIの10,000行で取り消された結果は完了扱いにしない。必要な全page／bulk取得が未対応なら、engine結果と製品未対応を分けて報告する。全page取得・外部sort等の検証領域も容量予算へ含める。

「0件」は観測件数・期間・負荷範囲を伴う結果であり、将来保証ではない。失敗を分母から外さず、first attemptとretry後を分ける。例えば0／10,000件でも、独立試行の仮定で片側95%上限は約0.03%である。

## 8. Snowflake／Databricksの公開値との参考比較

公開情報だけで作る参考表は、主試験の完了ゲートにしない。まず同scaleのTPC-Hを中心に、次の原資料を候補とする（2026-11-13確認）。外部harnessのcloud起動処理は実行しない。

| 原資料 | 使用する範囲と条件差 |
|---|---|
| [SELECT: Snowflake vs Databricks Showdown](https://select.dev/blog/snowflake-vs-databricks-showdown)（2026-04-10）、[公開コード](https://github.com/get-select/snowflake-databricks-benchmark) | 両製品のTPC-H SF1000・22 query、逐次／並列／cold startの参考候補。記事時点のSQL・commitと実行記録の対応を確認する。Snowflakeの並列runには複数clusterがあり、単一Macと同資源ではない。記事のサイズ対応仮説は採用しない |
| [Snowflake公式: Real Workload Performance](https://www.snowflake.com/en/blog/engineering/measuring-real-workload-performance/)（2026-06-25）、[公式TPC-H harness](https://github.com/Snowflake-Labs/analytics/blob/main/tpc-h/README.md) | Snowflake自身の100GB／1TB公表値とoriginal／modern SQL、集計方式を確認する。最小値／平均とlocal中央値は別にする。他社値の引用・推定はDatabricksの一次測定値として転載しない |
| TPC公式の監査済み結果 | 規模・製品版・metricが異なる結果は方法・歴史の参考に限定。QphDS等の公式複合指標を独自query秒数へ換算しない |

参考値台帳にはURL・公開日／取得日・測定主体・commit・製品世代／warehouse・台数／autoscale・データscale／行数・SQL集合・形式／layout・cache・並列数・測定境界・統計量・反復・除外queryを記録する。公開されない項目は「不明」とし、資料間の矛盾も残す。

粗い比較表は「同じ論理scale・対応SQL・指標の公表値」と「Shirokuma実測」を条件差つきで並べる。倍率はquery集合／集計／単位／計測境界まで対応を確認できた部分だけに限定する。条件不足なら絶対値の併記に留める。公開資料が最小値を使う場合は、主報告の中央値を維持したまま同方式の補助集計を別に作る。公開値に反復・分散がなければ比較CIや統計的優位は出さない。

ARM／x86、SSD／cloud object storage、Iceberg／native形式、warehouse世代、scale-out、cacheの違いを併記する。vCPU数・サイズ名による性能換算、異なるscaleの線形外挿、速いqueryだけの抽出、宣伝上の倍率や異なるcache条件の混在は行わない。公開値との粗い近さ・差は述べても「同じcomputeで同等以上」とは結論しない。費用比較は本計画から外す。

## 9. 実行制御と片付け

- run／case／query／request ID、SQL版、seed、profile、snapshotを対応づけ、送信前intentと受領handleを永続化する。不明なPOSTを盲目的に再送せず、履歴照合できなければUNKNOWNとして保留する。
- SQL deadline初期案はD0 120秒、D1 900秒、D2 1,800秒、D3／D4 3,600秒。pilotで用途別SLOとともに固定し、socket timeoutと分ける。deadline後の取消・孤立処理・lock／admission解放を確認してから次runへ進む。
- 最大wall time、query数、並列数、生成／spill／出力bytes、ログ量をrun manifestで固定する。hostとguestの空き、in-flight増加、停止余裕を確認してから次batchへ進む。
- 結果不一致、OOM、未解消UNKNOWN、disk予備割込み、host応答悪化では新規投入を停止する。必要に応じてowned処理を取消し、原因を確認するまで拡大しない。
- 所有するnamespace／DB／object prefix／VM作業領域だけを台帳に基づきcleanupする。他プロジェクト、既存利用者のデータ、全Colimaを一括で消さない。未確定履歴は保持し、profileと停止したShirokuma workloadを復元する。

## 10. 作業順・成果物・所要時間

| 順序 | 作業 | 次へ進む証拠 |
|---|---|---|
| P0／G0 | ローカルprotocolと資源・容量台帳 | 試験窓、所有範囲、host／guest予備、2TB上限、SLO確定方法、停止・復元手順を固定。ホスト資源利用は承認済み |
| P1／G1 | Dataset／oracle／共通runner | TPC-H→TPC-DS、全結果検証、計測、durable handle、resume／cancel／cleanup、容量予算の重点試験を通過 |
| P2／G2 | VM拡張と単一worker profile、bulk／stream | L1から順に構成・guest disk・heap／spill・認可・復旧を確認。D0全件一致、単発・小並列・取消、計測器余力が正常 |
| P3／G3 | D1→D2のpilot | 圧縮率・最大占有・生成／load時間・CPU／memory／IOを実測。profile・SLO・反復・到着率gridを固定し、大規模の容量と時間を再見積り |
| P4／G4 | D3と容量内のD4 | 全suite、並列／到着率ramp、構成効果を測る。最大scaleと安定運転範囲、未達query・ボトルネックを確定 |
| P5／G5 | 24時間・障害・復元 | 0エラーの観測範囲と件数、取消／復旧／重複防止、owned cleanupとhost／通常profileの復元を確認 |
| 並行作業 | 公開値の参考表 | 原資料の条件を記録して粗く併記。公開情報不足でもローカル試験を止めない |

成果物はprotocol、環境／資源／容量／dataset／SQL manifest、raw attempts、全結果照合、query profileと資源時系列、query別分布、規模／同時実行／profile別の性能曲線、最大占有、エラー台帳、公開値参考表、cleanup／復元記録とする。大きなデータ・ログはGit外の所有領域へ置き、repoには再現手順・集計・artifact参照を残す。

追加クラウド費用は0円を前提とする。hostの占有時間・生成時間・書込み量は記録するが、ローカルを無料と見なす価格優位は主張しない。準備5〜10営業日はrunner／qualificationの初期目安に限定し、profile／転送経路の先行変更が増える場合は見直す。本測定はpilotのquery時間と `Σ(実query本数×反復×block×profile×scale×cache条件)`、生成・load・soak・cleanupから積算する。最初から全matrixを実行せず、24時間試験は予備2／8時間とは別に確保する。

現在の着手範囲はP0／P1。実装は通常のCommander／Worker手順、重点試験、差分レビューで進める。準備用の小規模生成・参照実行を大規模性能の実績とは扱わず、P1の初期整備とG1全体の受入を分けて記録する。後続のローカル資源利用を未承認として再確認待ちにはしない。
