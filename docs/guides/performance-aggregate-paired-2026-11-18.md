# 1M行集計の公開API / 直接Trino paired測定（2026-11-18、accepted second batch）

## 結論（accepted second batch）

既存の文書化済み1,000,000行union（288,464行の3コピー + 先頭134,608行tail）に対する
業務型集計（`ss_store_sk`別のcount + 売上・純支払額sum、2行返却）を、同一SQL・同一
Iceberg snapshot・同一contextで、通常Workbench公開API（all-results）と直接Trinoに
よる8 warm pairで再測定した。今回はhistorical baselineを補正したaccepted second
batchをheadlineとする。

- **完了までの中央値は public が direct より +92.5 ms（約9.6%）遅い**。8 pair中
  7 pairでpublicが遅く、範囲は **−20.6〜+177.5 ms**、CV 63.5%。first batchより
  一貫してpublic側に寄っている。
- **一貫して遅いのは結果準備の前段**である。dispatch→initial response は **+53.4 ms
  （8/8 pairで正、+33.3〜+79.0 ms）**、dispatch→first row は **+292.3 ms（8/8 pairで正、
  +193.4〜+381.0 ms）**、HTTP totalは **+97.5 ms（8/8 pairで正、+6.1〜+175.3 ms）**。
  公開APIは最初の行を返すまでページ準備を待つため、first rowの遅れが大きい。一方、
  完了までの差は約0.1秒に縮む。
- **engine実行はpublicの方が遅くない**。Trino finished query statsのelapsedは direct
  739.0 ms / public 758.4 ms、paired中央値 **−1.3 ms**（4正/4負）。CPUも **−10 ms**
  （2正/5負）。差はノイズ範囲で、公開APIが1M行のscan/aggregateを遅くした証拠はない。
- CLI wallは direct 2,205.0 ms / public 2,321.8 ms、paired中央値 **+119.5 ms（約5.4%）**、
  7/8 pairで正、範囲 −33.0〜+253.3 ms、CV 65.9%。benchmark validation/artifactの
  変動を含む。
- **物理scanは1,153,856行**（4 branch × 288,464、同一snapshot
  `4180393143209654299`）、**論理行は1,000,000**（返却count合計）。metadata-onlyではなく、
  TableScanOperatorのphysical input positionsで確認した。1,153,856 − 1,000,000 =
  153,856は、tail branchが288,464行をscanして134,608行だけ返す差分である。
- 結果2行は全18 trialでbyte-identical（`cbd5a47e…`）。集計値はfirst batchと同じ。

要するに、この1M行scan・2行返却の集計では、**公開APIはengineを遅くせず、初回応答/
HTTPに約0.05〜0.1秒、first rowに約0.3秒の固定費を加える。完了までの中央値は約0.09秒
（約10%）遅いが、絶対量は約1秒に対して数十〜100msで、倍率の大きなslowdownではない**。

## first batch（exploratory only）とbaseline補正

- first batch campaign `/private/tmp/shirokuma-aggregate-paired-20261117-c01` は、
  `baseline_host_free` が **3,044,453,998,592** で、authoritative previous after campaignの
  **3,089,681,522,688** と一致しなかった。またinherited rootのbaseline値を現在の
  disk usageで再測定していた（例: page-transport root 0 → 2,339,733,504）。このため
  first batchは**exploratory only**とし、accepted evidenceには使わない。
- first batchの19 attempts（reference 1 + comparison 18）はすべてterminal SUCCESSで、
  UNKNOWN/canceled/failed/reconciliation/resendは0。未解決stopはない。ファイル・
  attempt・result・QueryInfo・evidenceはそのまま保持し、本reportはfirst batchを
  exploratoryと明示するため更新した。
- accepted second batchは、authoritative
  `/private/tmp/shirokuma-authz-remeasure-20261117-after/campaign.json` の
  `baseline_host_free` と全historical root baseline値を**逐語で継承**した。
  - historical host free: **3,089,681,522,688**（new campaignとexact equal）。
  - historical 62 rootsの値はすべて一致（baseline合計 244,504,821,760 bytes）。
  - `/var/folders/.../shirokuma-formal-remeasure-cl3ghoig` は
    `/private/var/...` へcanonicalizeし、value 0のまま1回だけ保持（duplicateなし）。
  - 新規rootはzero baseline: previous aggregate campaign、task directory、new campaign。
  - root数は **65**（62 historical + 3 new zero）。baseline snapshot SHA256は
    `7ccbc448…`。present disk usageへの再設定はしていない。
- accepted campaign: `/private/tmp/shirokuma-aggregate-paired-20261118-c01`
  （scope: `second-batch/evidence/scope-init.json`）。

## 対象と方法

- 入力はfirst batchと同じfrozen manifest（SHA256 `4f3e7a5d…`）。既存
  `manifest-1000000.json` の凍結SQLと `snapshot_pins` をそのまま使い、外側に
  `GROUP BY ss_store_sk` のcount/sumを追加した。SQLはdirect/publicで同一
  （`8423ad17…`）。snapshotは`0ea55aff…`、datasetは
  `43004b09-…/tpcds-source-01/snapshot/0ea55aff…`。generate_seriesや合成定数は
  使っていない。
- 集計結果（2行、全18 trialで同一）:

| ss_store_sk | line_count | total_ext_sales_price | total_net_paid |
|---:|---:|---:|---:|
| NULL | 44,702 | 43,719,693.70 | 39,736,873.81 |
| 1 | 955,298 | 1,782,042,533.90 | 1,602,975,055.03 |
| 合計 | **1,000,000** | **1,825,762,227.60** | **1,642,711,928.84** |

- 独立referenceは既存DuckDB SF0.1 source Parquetを同じaggregate SQLで実行して作成。
  全18 trialをfull comparisonで照合し、result fileは18/18 byte-identical
  （`cbd5a47e…`）。既存の288,464行reference/1M行resultから、288,464 distinct
  (ticket,item) key、1M unionの多重度が「3回: 153,856 key、4回: 134,608 key」で
  あることもオフライン確認した。
- trialは initial 1回/route + warm 8 pair（各pairがdirect/public）= 18。warmは
  public→direct / direct→public を交互にし、concurrency 1。600秒deadline、
  4GiB declared peak、2TB ceiling、512MiB drain、0.2秒capacity sampler、
  既存runnerのcancellation/UNKNOWN semanticsを変更していない。
- 各trial後にSQLを送らない `GET /v1/query/{id}` でTrino finished query statsを取得し、
  `stages`/`operatorSummaries`のTableScanOperatorとHashAggregationOperatorを保存した。
  publicはWorkbenchの永続query receipt（`query_records`/`query_execution_contexts`）も
  read-onlyで照合した。preflightはsession/status/databases、Trino `/v1/info`、gate再読
  のみで、SQL送信なし。

## 結果（accepted second batch、warm 8 pair、ms）

| metric | direct median [min–max] | public median [min–max] | public−direct median [min–max] | +/− |
|---|---:|---:|---:|---:|
| dispatch→result | 959.6 [934.3–1,049.7] | 1,056.7 [1,004.8–1,114.6] | **+92.5 [−20.6–+177.5]** | 7/1 |
| dispatch→first row | 743.2 [715.6–819.5] | 1,040.9 [988.9–1,096.6] | **+292.3 [+193.4–+381.0]** | 8/0 |
| dispatch→initial response | 56.7 [35.2–74.4] | 113.8 [93.0–118.7] | **+53.4 [+33.3–+79.0]** | 8/0 |
| HTTP total | 748.1 [721.9–820.2] | 851.0 [801.3–897.3] | **+97.5 [+6.1–+175.3]** | 8/0 |
| follow-up header wait | 669.1 [634.6–703.8] | 739.0 [706.4–776.5] | +69.7 [+6.8–+141.9] | 8/0 |
| engine elapsed | 739.0 [702.8–806.5] | 758.4 [706.3–880.6] | **−1.3 [−83.8–+145.8]** | 4/4 |
| engine CPU | 1,145 [1,110–1,180] | 1,135 [1,110–1,170] | −10 [−40–+60] | 2/5 |
| CLI wall | 2,205.0 [2,058.8–2,357.7] | 2,321.8 [2,202.9–2,428.5] | +119.5 [−33.0–+253.3] | 7/1 |
| full comparison | 222.5 [212.7–234.0] | 222.5 [213.0–232.7] | −2.7 [−19.5–+19.0] | 4/4 |

- initial（各1回）は direct dispatch→result 1,040.4 ms / public 953.7 ms、
  engine elapsed 803.1 / 687.1 ms、engine CPU 1,140 / 1,110 msだった。direct initialの
  engine/planningが重く、publicが速いという証拠にはしない（cold cacheの証明でもない）。
- 物理/論理: 全18 trialでphysical input positions **1,153,856**、processed
  **1,153,856**、output **2**、TableScan **4**、HashAggregationOperator **5**、
  snapshotは1種類。physical input dataは13,407,436 B、processed input dataは
  36,346,464 B。metadata-onlyではない。
- 公開APIの最初の行が遅いのは、2行結果をページとして準備してから返すためと考えられる。
  完了までの差がfirst rowの差より小さいのは、directが最初の行を早く返した後も
  fetch/terminal処理に時間を使うためと考えられ、公開APIが完了時に追加で大きく待つ
  わけではない。

## first batch（exploratory）との比較

first batchはbaseline会計が不正なためaccepted evidenceではないが、同じSQL・同じ
snapshot・同じ18 trialで、方向は一致している。

| metric | first batch Δ median [range] | accepted second batch Δ median [range] |
|---|---:|---:|
| dispatch→result | +58.3 [−51.6–+135.4] | **+92.5 [−20.6–+177.5]** |
| dispatch→first row | +262.3 [+161.0–+336.7] | **+292.3 [+193.4–+381.0]** |
| dispatch→initial response | +47.5 [+25.3–+71.6] | **+53.4 [+33.3–+79.0]** |
| HTTP total | +68.5 [−28.3–+138.8] | **+97.5 [+6.1–+175.3]** |
| engine elapsed | −26.2 [−200.1–+41.0] | **−1.3 [−83.8–+145.8]** |
| CLI wall | +82.8 [−13.3–+225.2] | **+119.5 [−33.0–+253.3]** |

- 両batchとも「publicはengineを遅くせず、初回応答/first row/HTTPに固定費を加える」
  という方向で一致した。accepted second batchの方がpublic側の符号が揃っている。
- first batchのcampaign/attempts/evidenceはそのまま保持し、本reportではexploratoryと
  明示した。accepted second batchのheadlineには使わない。

## 安全性・検証・容量（accepted second batch）

- 18/18 SUCCESS、full comparison 18/18、result rows 2、grouped count合計1,000,000。
  public永続receiptは9/9 FINISHEDでSQL/backend ID一致。backend query IDは18個すべて一意。
- failure 0、UNKNOWN 0、canceled 0、reconciliation 0、resend 0。既存UNKNOWN 5件は不変、
  新規UNKNOWN/CANCELED/FAILED 0。public query recordはpre-batch 5030から+9
  （すべてFINISHED、final 5039）。
- campaign gate（c01/c02/before/after/return-scale/**previous aggregate**/current）は
  すべてok。previous aggregateの19 attemptsもすべてterminal SUCCESSで、未解決stopなし。
  doctor finalはhealthy（45 healthy + 1 informational）。Trino pod UIDはprevious studyと同一。
- accepted scope: host baseline **3,089,681,522,688** exact equal、historical 62 root
  values exact、canonical alias 1件、new zero roots 3件、root数65。finalization時点の
  used **50,700,640,256 bytes**、2TB ceilingに対するremaining **1,948,762,488,832
  bytes**（used/drain控除後、4GiB peakは別check）で、4GiB peak/512MiB drain内
  （`second-batch/evidence/final-checks.json`）。usedの大部分はhistorical rootsの
  現在growth（約50.6GB）で、new zero roots（previous aggregate/task/new campaign）は
  約75.7MB。second batchの成果物自体は数十MBである。
- 製品・runnerは無変更（HEAD `cef46f40`、product/runner diff空）。既存GitOps 2差分と
  indexは不変。prior measurementのcampaign/report/evidenceはread-onlyで、measurement
  start以後にmtimeが更新されたprior artifactは0。唯一、稼働中Workbench serverの
  access log（`.../logs/server-candidate.log`）だけがpublic page requestで追記されたが、
  これは測定成果物ではなく通常のserver logである。push/publication/新規spendingなし。

## 限界

- 単一host、concurrency 1、warm 8 pair、統計検定なし。initialは各route 1回で
  cold-cacheの証明ではない。
- HTTP total/initial/follow-upはclient観測で、server内のpage準備とnetwork待ちを分離して
  いない。engine elapsed/CPUは非送信の`GET /v1/query/{id}`によるTrino QueryInfoで、
  clientのdispatch時刻とは別scope。
- wallはbenchmark validation/artifact処理を含む。full comparisonはreferenceとの完全比較
  であり、referenceは既存DuckDB source Parquetから作成したが、今回の測定は単一SQL case。
- 1Mは288,464行の3コピー+tailであり、scan量とdistinct data量は別。100M/10Mへの外挿は
  していない。
- accepted second batchのbaselineはhistorical authoritative値の継承であり、現在の
  disk usageをbaselineに再設定していない。新規rootはzero baselineで、growthはusedに
  算入される。

## 証拠

- **accepted primary task/evidence**:
  `/private/tmp/shirokuma-aggregate-worker-3gqirq0y/second-batch/`
  - `raw/matrix.json`、`raw/comparison.json`、`report/tables.md`
  - `evidence/preflight.json`、`evidence/scope-init.json`、`evidence/final-checks.json`、
    `evidence/integrity-audit-final.json`、`evidence/provenance.json`
  - `evidence/stats/aggregate-1m-*.json`（18件のTrino QueryInfo、すべてFINISHED）
- **accepted campaign**: `/private/tmp/shirokuma-aggregate-paired-20261118-c01/`
  （`reference-aggregate-1m` + 18 comparison runs。run/attempt/result/query manifests）
- **first batch exploratory only（保持）**:
  `/private/tmp/shirokuma-aggregate-paired-20261117-c01/` と
  `/private/tmp/shirokuma-aggregate-worker-3gqirq0y/`（first-level evidence）。
- worker events/log: `/private/tmp/shirokuma-aggregate-worker-3gqirq0y/`
