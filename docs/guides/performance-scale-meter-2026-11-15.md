# 性能試験: SF0.1 TPC-DS qualificationと計測器負荷

2026-11-15 JST。SF0.01で確認した承認14件を、実TPC-DS SF0.1の新規24表で独立に再資格確認し、同時に容量走査・全件比較・取得の計測器負荷をcharacterizeした。結果はqualificationであり、SLO・engine性能・G1完了ではない。

## Datasetとsource

- DuckDB 1.4.3の `dsdgen(sf=0.1)` で新規24表を生成。実測 **1,175,873行**、Parquet **26,247,168 bytes**、measured logical **117,210,281 bytes**（1GB qualification cap以下）。
- 固定次元は線形に増えない。`date_dim` は0.01と同じ73,049行、factは `store_sales` 288,464、`catalog_sales` 143,657、`web_sales` 71,632。
- 固定Spark v4.0.1 raw 14 source、同じSpark 4.1.3 image/ANSI/UTC/profileからfresh referenceを作成し、14件すべてSUCCESS。合計 **1,113行**（5a/14a/18a/22a/27a/35a/36a/51a/67a/80a/86a各100、10a 0、70a 3、77a 10）。
- `prepare-tpcds-approved` は新24表・source・Spark reference・型/全値/重複/ORDERを14件で独立照合した。99 generationは列挙したが、**99件/4 iterationのnative実行はしていない**。既存99/4 contractsは不変。

## Native表と承認14件

| 検証 | 結果 |
|---|---|
| 新24表 直接Trino | SUCCESS 24/24、1,175,873行、全内容・型・重複一致 |
| 新24表 公開API all-results | SUCCESS 24/24、1,175,873行、同 |
| 14a standalone 直接/公開 | 各SUCCESS 100行（client attempt elapsed 3.239s / 3.189s） |
| 正式14件 3 blocks 直接 | 各14/14、1,113行（block elapsed合計 23.889 / 15.394 / 16.464s） |
| 正式14件 3 blocks 公開all-results | 各14/14、1,113行（19.157 / 16.976 / 15.377s） |

3 blocksはdirect/publicの順を交互にし、同じ固定SQL・snapshot・new expected resultsを使用した。各結果は全型・全値・重複・ORDER、浮動小数 `1e-8` で照合し、未実行・失敗は0。提示時間はclient attempt `elapsed_s` の合計で、結果取得・検証境界を含む。engine query time・SLO・速度比較ではない。

`store_sales` 全23列・288,464行を直接/公開all-resultsで各3 blocks（計6回、timeout1800s）取得し、全値・型・重複がParquet referenceと一致した。full resultのtruncation/capは0。この6回はprotocolの600sより長いtimeoutで実行されたためcorrectness evidenceに限定し、600s deadlineを満たしたとは主張しない。追加のcorrected native runsは後述の600sで実行した。

## 計測器characterization（initial invalid / corrected）

初回計測はproduction guardではなく、毎callbackの `campaign.check` を直接呼ぶ古いguardを測っていた。Writerをtimed pairの前に実行し、maxrssは入力展開を含む累積値、容量baselineはVM/state・旧artifactを欠き、scan phaseは同一最終状態のsubtree scan、child CPUはsampling subprocessを含み、tables/large runはtimeout1800sだった。初回の135%と15.8msは**production計測として不採用**。初期script・結果・failed receiptはcorrection-01に保存し、履歴として残す。

Corrected measurementは、sourceの `CapacitySampler(0.2)`、毎callbackのlocal root growth/deadline、scan後deadline再確認、実際のremaining peak引数、first/boundary/final forceを使い、1M行offline immutable artifactで3 alternating checked/no-op blocksをfresh processで実行した。

- corrected native-campaign `campaign.check()`: **12,719,124,480 bytes used**、**29 disjoint roots**（offline/parent correction campaignは28 roots）。`scan-corrected.json`自体はmanifest identityを埋め込んでいないため、このroot数/bytes対応はnative-campaign manifestの記録に基づく。10 samplesで median **301.6ms**、p95 **315.9ms**（p95は `floor(0.95*(n-1))` index、n=10）、max **332.1ms**。200ms criterionを満たさない。
- reconstructed/current artifact volumes（実file count/allocated、10 samples）: dataset 26 files/26.7MB（median0.20ms）、initial campaign 928 files/1.749GB（20.8ms）、offline 1M 1 file/319.6MB（<0.01ms）、native campaign 27 files/612MB（0.62ms）。これらは**最終時点のsubtree size比較**であり、過去の生成/load時系列を装わない。temporal growth curve自体は未測定。
- corrected offline production guard: checked median **197.28s**、no-op median **76.21s**、per-pair overhead median **155.7%**（range154.4–162.1%、ratio-of-medians158.9%）。checked guardはglobal 377–380 scans/117.0–119.6s（約315ms/scan）、local 0.68–0.71s、check_calls 7,819。checked maxrss 30.4–31.0MB、no-op 26.9–27.0MB。Writer/Readerは別fresh processで **56.45–57.68s**、maxrss約22.5MBとして分離し、overhead ratioから除外した。
- corrected native large-result（288,464 rows、default150、timeout600s、4GiB peak、3 alternating paired blocks）: 6/6 SUCCESS。attempt `elapsed_s`は **113.771–122.900s**、outer observer-instrumented elapsedは114.44–123.45s。guardはglobal 220–233 scans / 69.1–74.1s、local 0.84–0.97s。child CPU direct user56.1–57.8s/system54.6–56.5s、public user56.4–57.6s/system56.7–58.6s。child maxrss 95.7–107.4MB、observer CPU 0.46–0.54s user/1.03–1.38s system、observer maxrss 38.4–39.4MB。Workbench RSS約178–184MB。
- 観測最大成長率78.0–88.5MB/s、最大scheduling gap約0.236s、corrected最大scan0.332s、最大in-flight delta19.9MB。exposure = growth × (200ms + 0.332s + gap) + delta = 各run **76,714,482.9–87,754,327.8 bytes**（約76.7–87.8MB decimal / 73.2–83.7MiB）。task drain **512MiB**、corrected capacity 12.72GB、2TB cap/remaining headroom内。observed envelopeでありhard boundではない。

**Observer-readiness screen: FAILED.** corrected production guardでも最大global scan **332ms > 200ms**、1M行offline per-pair overhead median **155.7% > 10%**。したがって計測器readinessは不採用であり、大規模性能/SLO/100GB readinessの判断に使わない。thresholdやsampling intervalを結果後に変更せず、**繰り返しfull-global capacity scanの回数または1回あたりcostを、deadline/local peak/stop保証を維持したまま減らす**ことを次項目とする。local guard 0.68–0.71sはその保証を弱める根拠にしない。absolute timesとraw pairsを保持する。

独立評価は6 formal suite（84 query）、standalone 14a、24表両経路（各1,175,873行）、6 large run（各288,464行）の型/全値/重複/ORDERを再計算し、全件一致を確認した。reviewer evidenceは `reviewer-results.json` / `reviewer-corrected-results.json`。corrected capacity manifestはlocal SDK/venv・artifact rootsも明示rootとして含み、root間の重複を避けている。初期invalid measurementと1800s timeout runsは履歴として保持し、production claimに使わない。

## Capacity scan optimization（2026-11-15追記）

`allocated`を`os.scandir`へ置換し、31-root full scanはmax **393.7ms -> 170.6ms**、median **332.3ms -> 145.6ms**となった。max<200msは満たしたが、1M offline checked/no-op per-pair overhead medianは **150.7% -> 78.4%** で10% criterionは未達。`CapacitySampler(0.2)`、local/deadline/forced boundaries、root coverage、2TB/512MiBは不変。詳細は[capacity scan最適化](performance-capacity-scan-2026-11-15.md)。

## Protocol deviations / errata

- 初回のtables/large runsのtimeout1800sはprotocolの600sからの逸脱。correctness evidenceとして残し、600s deadline達成とは扱わない。corrected native large runsは600sで成功した。
- 初回protocolの `restore.stage_limit_before=400` は誤記。実pre-task baselineは **150** で、correction protocolに150として記録した。frozen protocol自体は書き換えない。
- standalone 14aのmanual wrapperはaccepted full14 manifestの `acceptance` pathを欠くため、独立したaccepted bound manifestではない。formal full14 gateはacceptedで、wrapperのquery/schema/order/snapshot/referenceがfull14の14aと同一であることは `standalone-equality-14a.json` で確認した。独立manifestのvalidation成功とは主張しない。
- Spark per-query row一覧は 36a=100 を含む。合計1,113行（5a/14a/18a/22a/27a/35a/36a/51a/67a/80a/86a各100、10a 0、70a 3、77a 10）。

## Runtimeと復元

- stage400を正式profileへ適用し、coordinatorだけに `query.max-stage-count=400` を設定。Deployment UIDを保持し、worker CM/Deployment/templateは不変。
- stage400 coordinator cgroup peak **3,594,616,832 bytes**、worker peak **4,040,691,712 bytes**（4GiB limit内）。OOM/oom_kill 0、restart 0。new24表登録と全run後も旧59表pins/historyは不変。
- 復元後は設定bytes SHA256 `e797cab3...`、stage annotation/propertyなし、coordinator/worker CM data・Deployment template・UIDsはbaseline一致。復元Podはplanned replacement、restartではない。Workbench/public context/24+59 pins/doctor healthy 46を確認。fenced UNKNOWN 5件は保持し、新規unresolved 0。
- 新24表はreview用にowned prefix `s01xq0h` で保持。旧59表は変更していない。private backup/settings/launch環境はGit外0600で保護し、tree全体を共有可能とは扱わない。

## 回帰と制約

Make162/162 commandを4並列で実行し、**5,948件中5,874成功・74 skip・失敗0**、source unchanged。設計整合・改行・空白・秘密情報filename・diff checkは成功。

残り: SF0.1の99/4 native qualificationは未実施。capacity scanはmax<200msを満たしたがoffline10%は未達であり、OS bulk-attribute等でper-scan costをさらに下げる必要がある。別scale、同時実行/到着率、計測器を含むSLO、L1/guest disk拡張、100GB pilotは後続。SF0.1の結果を大規模性能・TPC公式score・G1完了へ読み替えない。
