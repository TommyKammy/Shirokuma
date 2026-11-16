# 返却規模別の直接Trino / 公開全件API比較（2026-11-16）

SF0.1 `store_sales` の同一23列を **100,000 / 288,464 / 1,000,000行**で返し、直接Trinoループバックと通常Workbench公開API（all-results）を同じSQL・同じsnapshot・同じcontextで比較した。qualificationであり、SLO・engine性能・他製品比較ではない。サービス・benchmark・計測器の変更、追加dependency、10M/100M実行は行っていない。

## Scopeとprovenance

- 既存 `large-bound-01.json` の `table_store_sales` と `tables-reference-01` を入力にした。288,464行は元SQLをbyte-for-byteで使用し、100,000行は `ss_ticket_number, ss_item_sk` の一意順序による先頭subset、1,000,000行は3回の全体コピーと134,608行の同順序subsetの`UNION ALL`（3×288,464 + 134,608）で構成した。naked LIMITは使っていない。
- 3ケースとも23列・型・snapshot pin・dataset id・contextは同一。各sizeのdirect/publicは同じmanifest SQL textを使用した。全SQLはdirect Trinoの `EXPLAIN (TYPE VALIDATE)` で構文・解決を事前確認した（比較24回には含めない）。
- 独立referenceはDuckDB 1.4.3でSF0.1 source Parquetから生成した。288,464行referenceのfooter SHA-256は既存 `tables-reference-01` と一致した。各比較runはreferenceとの全値・型・重複のfull comparison（unordered、`ordered=false`）で検証した。

## Campaign・guard・accounting

- 直前campaign2の **49 root baselineと`baseline_host_free`をそのまま継承**し、TASK全体と新campaignを重複しないzero baselineとして追加した51 root。既存campaignのaccountingはresetせず、直前campaignの83 runは全てSUCCESSで未解決stopは0だった。
- 新campaignはTASK外の専用root。host基準freeは `3,089,681,522,688` bytes、ceilingは `2,000,000,000,000` bytes。作成時usedは `21,924,196,352` bytesで、600秒deadline、declared peak 4GiB、2TB cap、512MiB drain、host/guest reserve、callback deadline/local peak/forced scan、0.2秒sampler、concurrency 1をrunnerの既存guardのまま使用した。guardの変更・迂回はない。
- 全run後のusedは `25,948,364,800` bytes、remainingは `1,973,514,764,288` bytesで、2TB ceiling/4GiB peak/512MiB drainの範囲内。campaignの27 run（reference 3 + comparison 24）は全てSUCCESS。

## 実行と検証

- 比較は **24/24 SUCCESS** = 3 size × 2 route × (initial 1 + warm 3)。route順はrepごとにdirect→public / public→directを交互にした。referenceは3/3 SUCCESS。failed 0、未実行0、上書き・再送0。
- 24件すべて `attempt.status=SUCCESS`、`validation="full comparison"`、`pending_attempts=0`、`not_run_attempts=0`、footer `terminal=SUCCESS`、23列、期待行数一致。engine IDは24件で一意。`global_failed_scans=0`、`interval_s=0.2`。
- 失敗・未実行trialはない。事前の `SELECT 1` と `EXPLAIN (TYPE VALIDATE)` は比較trialとは分けて記録し、比較24回には数えていない。
- 同一性: 全runでSQL SHA-256、dataset id、snapshot pin、23列、public context、direct catalog/schemaが一致し、`independent_cross_engine=true`、timeout 600秒、peak 4GiB、concurrency 1、sampler 0.2秒、guard failure 0。integrity failuresは0。
- reference compositionも独立確認した: 288,464行の `(ss_ticket_number, ss_item_sk)` は全件一意、100,000行subsetは欠落0・重複0、1,000,000行は全行がfullに存在し「全行3回 + 先頭134,608行だけ4回」の多重度不一致0。

## 結果（warm 3回のmedian [min–max]）

CLI wallはprocess起動・guard・artifact書出し・benchmark validationを含む。dispatch→resultは `result_complete - dispatch_started` で、artifact処理とguardを含む。表のvalidationはreferenceとのfull comparison（compare）のみで、別途result完了後のorder/footer全行走査を後述する。HTTP totalはinitial+follow-upのconnect/send/header/body/decode/teardownを含むclient観測累計で、header waitはその一部。

| rows | route | CLI wall s | dispatch→result s | validation s | HTTP total s | header wait s | follow-up pages | JSONL MB proxy |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 100,000 | direct | 28.641 [28.383 – 28.750] | 6.564 [6.348 – 6.635] | 16.926 [16.915 – 16.968] | 1.233 [1.137 – 1.256] | 0.645 [0.605 – 0.646] | 24 [23 – 24] | 30.2 |
| 100,000 | public | 29.506 [29.367 – 29.873] | 7.341 [7.170 – 7.418] | 17.017 [16.925 – 17.224] | 1.682 [1.578 – 1.775] | 1.585 [1.484 – 1.682] | 24 [23 – 24] | 30.2 |
| 288,464 | direct | 80.512 [80.411 – 81.245] | 16.385 [16.228 – 16.649] | 50.178 [50.140 – 50.455] | 1.400 [1.290 – 1.427] | 0.198 [0.189 – 0.204] | 42 [41 – 42] | 87.4 |
| 288,464 | public | 82.498 [81.964 – 83.141] | 18.423 [18.196 – 18.611] | 50.090 [49.881 – 50.525] | 2.742 [2.683 – 2.781] | 2.478 [2.414 – 2.517] | 41 [39 – 41] | 87.4 |
| 1,000,000 | direct | 285.682 [285.540 – 286.806] | 58.442 [57.774 – 58.463] | 180.434 [179.879 – 181.240] | 4.907 [4.715 – 5.070] | 0.643 [0.596 – 0.658] | 136 [135 – 138] | 302.8 |
| 1,000,000 | public | 294.481 [292.451 – 295.019] | 65.331 [64.957 – 65.554] | 181.089 [180.356 – 182.295] | 9.309 [9.262 – 9.462] | 8.403 [8.354 – 8.566] | 135 [132 – 137] | 302.8 |

initial（各route/sizeの最初の1回。cold cacheの証明ではない）:

| rows | route | CLI wall s | dispatch→result s | validation s | HTTP total s | header wait s | JSONL MB proxy |
|---:|---|---:|---:|---:|---:|---:|---:|
| 100,000 | direct | 28.807 | 6.632 | 16.956 | 1.291 | 0.729 | 30.2 |
| 100,000 | public | 29.982 | 7.790 | 17.019 | 1.954 | 1.857 | 30.2 |
| 288,464 | direct | 78.710 | 15.964 | 49.300 | 1.451 | 0.213 | 87.4 |
| 288,464 | public | 81.556 | 18.058 | 49.657 | 2.788 | 2.528 | 87.4 |
| 1,000,000 | direct | 283.242 | 57.401 | 178.787 | 4.789 | 0.610 | 302.8 |
| 1,000,000 | public | 290.349 | 65.355 | 177.918 | 9.495 | 8.571 | 302.8 |

### 公開API − 直接Trinoのpairwise差（warm、repごとの対応差）

| rows | CLI wall Δ s | dispatch→result Δ s | validation Δ s | HTTP total Δ s | HTTP Δ s/MB |
|---:|---:|---:|---:|---:|---:|
| 100,000 | 1.123 [0.725 – 1.124] | 0.783 [0.606 – 0.993] | 0.090 [0.010 – 0.257] | 0.519 [0.345 – 0.546] | 0.017 |
| 288,464 | 1.553 [1.254 – 2.629] | 1.968 [1.773 – 2.226] | -0.298 [-0.365 – 0.385] | 1.381 [1.255 – 1.453] | 0.016 |
| 1,000,000 | 8.213 [6.770 – 8.941] | 7.112 [6.868 – 7.183] | 1.055 [-0.077 – 1.210] | 4.392 [4.355 – 4.595] | 0.015 |

sec/MBとMB/s（warm median、JSONL file bytes proxy。HTTP wire bytesではない）:

| rows | public extra wall s/MB | public extra HTTP s/MB | direct MB/s dispatch | public MB/s dispatch | direct MB/s HTTP | public MB/s HTTP |
|---:|---:|---:|---:|---:|---:|---:|
| 100,000 | 0.037 | 0.017 | 4.606 | 4.119 | 24.523 | 17.973 |
| 288,464 | 0.018 | 0.016 | 5.332 | 4.742 | 62.424 | 31.857 |
| 1,000,000 | 0.027 | 0.015 | 5.181 | 4.635 | 61.711 | 32.527 |

HTTP totalとheader waitの内訳（warm 3回の合計。run合計でありmedianではない）:

| rows | route | HTTP total s | initial total s | follow-up total s | header wait s | body read s | decode s | calls | follow-up calls |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 100,000 | direct | 3.626 | 0.176 | 3.450 | 1.896 | 0.467 | 0.261 | 74 | 71 |
| 100,000 | public | 5.036 | 0.317 | 4.719 | 4.751 | 0.022 | 0.229 | 74 | 71 |
| 288,464 | direct | 4.117 | 0.121 | 3.996 | 0.591 | 1.261 | 0.694 | 128 | 125 |
| 288,464 | public | 8.206 | 0.305 | 7.901 | 7.409 | 0.068 | 0.670 | 124 | 121 |
| 1,000,000 | direct | 14.692 | 0.148 | 14.544 | 1.897 | 4.560 | 2.464 | 412 | 409 |
| 1,000,000 | public | 28.034 | 0.296 | 27.738 | 25.323 | 0.228 | 2.292 | 407 | 404 |
| 全24 | both | 85.479 | 2.055 | 83.424 | 56.374 | 8.816 | 8.828 | 1,623 | 1,599 |

publicのbody readが小さいのは、Workbenchがpage準備をheader返却前に進め、clientのsocket readが短時間で終わるためと考えられるが、server内のpage準備とsocket readの内訳は分離していない。server側のpage準備はheader waitに含まれ得るため、body readだけを転送コストと呼ばない。

### 処理費の内訳（warm median）

| rows | route | wall s | HTTP s | artifact s | order/footer scan s | compare s | benchmark validation / wall | process overhead s |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 100,000 | direct | 28.641 | 1.233 | 5.173 | 4.239 | 16.926 | 73.9% | 0.409 |
| 100,000 | public | 29.506 | 1.682 | 5.625 | 4.280 | 17.017 | 72.0% | 0.342 |
| 288,464 | direct | 80.512 | 1.400 | 14.521 | 12.972 | 50.178 | 78.4% | 0.500 |
| 288,464 | public | 82.498 | 2.742 | 15.708 | 12.889 | 50.090 | 76.3% | 0.528 |
| 1,000,000 | direct | 285.682 | 4.907 | 52.167 | 46.062 | 180.434 | 79.4% | 0.631 |
| 1,000,000 | public | 294.481 | 9.309 | 55.756 | 46.162 | 181.089 | 77.4% | 0.589 |

`order/footer scan`はresult完了後の `validate_order` 全行走査（ORDER contractが空でもfooter/型を検証）、`compare`はreferenceとのfull multiset比較である。どちらもbenchmark専用で、製品の返却時間ではない。`process overhead`はCLI wall − attempt elapsed（process起動・manifest/reference検査・終了処理）。

### Client / Trino / Workbench（warm median、scopeを混同しない）

| rows | route | client user+sys s | client max RSS MB | Trino cgroup CPU s | Trino lifetime peak after MB | Workbench CPU Δ s | Workbench max RSS MB |
|---:|---|---:|---:|---:|---:|---:|---:|
| 100,000 | direct | 26.760 | 121.4 | 0.371 | 1,251.7 | 0.040 | 122.7 |
| 100,000 | public | 27.110 | 121.1 | 0.398 | 1,251.7 | 0.830 | 127.9 |
| 288,464 | direct | 77.100 | 152.1 | 0.995 | 1,251.7 | 0.100 | 161.7 |
| 288,464 | public | 77.490 | 138.8 | 1.045 | 1,251.7 | 1.850 | 162.4 |
| 1,000,000 | direct | 272.220 | 160.1 | 3.609 | 1,251.7 | 0.350 | 187.6 |
| 1,000,000 | public | 275.770 | 155.7 | 3.691 | 1,251.7 | 6.270 | 199.1 |

- clientはbenchmark CLI subprocessの `/usr/bin/time -l`。user+sys、max RSS。Trinoはtrino container cgroupのCPU累計差と`memory.peak`で、後者はcontainer生涯watermarkでありrun単体peakの上限証明ではない。run中の`memory.peak`増分は全runで0（既存watermark以下）だった。Workbenchはserver processの`ps` 0.5秒pollで、CPUは`ps time`差、RSSは観測最大。いずれも欠測・丸め・sampling限界を含む。
- 全24 run中の観測最大: Trino lifetime peak **1,251.7 MB**、client max RSS **165.6 MB**、Workbench sampled max RSS **209.5 MB**。run単体のpeakや合計メモリではない。

## 100,000,000行の条件付き推定

100Mは実行していない。以下の推定は1Mまでのwarm 3回のHTTP totalだけを使う。primaryは**within-rep paired warm deltaの中央値**（同じrepのpublic−direct）を10^8行へ100倍する推定で、median of differencesのestimandである。絶対時間は別estimandとして、public/directそれぞれのwarm HTTP totalの中央値を100倍する。

| rows | direct HTTP median s | public HTTP median s | paired Δ median s |
|---:|---:|---:|---:|
| 100,000 | 1.2330 | 1.6823 | 0.5187754576 |
| 288,464 | 1.3996 | 2.7425 | 1.3813790843 |
| 1,000,000 | 4.9069 | 9.3094 | 4.3921327563 |

- 追加HTTP（primary）: 4.3921327563 × 100 = **439.2133 s**。
- 隣接2区間の傾きを1Mにanchorして100Mへ延長した感度は **423.30 s**（288k→1M傾き）から **457.52 s**（100k→288k傾き）。これは**信頼区間ではなく感度範囲**。
- 絶対HTTP（別estimand）: public median 9.3094375 × 100 = **930.94 s**、direct median 4.9069134 × 100 = **490.69 s**。medianの差 9.3094375 − 4.9069134 = 4.4025240 sはprimaryのpaired median 4.3921328 sと一致しない。両者を混同しない。
- この方法は行幅、page cadence、client/server構成、同時実行1、cache状態、workloadが1Mと同様という条件付き。1Mは288,464行の3コピー+134,608行tailであり、**input scan量とoutput量は別**。100Mも同じ行の繰り返しを仮定し、scan costを外挿しない。
- JSONL payload proxyは約 **30.3 GB**（1M実測×100）で、**declared peak 4GiB**を単一runの結果だけで超える。2TB ceilingには収まるが、public absolute HTTP 930.94 sは**600秒deadline**を超える。primaryの追加分439.21 s単独では600秒未満だが、絶対publicは超える。HTTP wire bytes、圧縮、validation/artifactは含まない。

## 優先順位（bulkとaggregate/smallを分ける）

| 優先 | 対象 | 測定根拠 | 次action |
|---:|---|---|---|
| 1 | bulkが必要な場合のpublic page preparation/wait | 1M public HTTP total 9.309 sのうちheader wait 8.403 s。paired Δ median 4.392 s | client decodeの推測より先に、server側page準備/待ちを計測・改善する |
| 2 | それでも必要な場合のbounded bulk export prototype | 100M proxy 30.3 GB、4GiB peak/600秒deadline | 別guard/export経路の設計。今回のguardでは実行しない |
| 3 | aggregate/small result | 100kでもbenchmark validation/artifactが大半。small返却の一般overheadを優先する証拠はない | 別実験。今回の結果からgeneralized overhead改善を正当化しない |

- 負の結果: publicは全sizeでdirectよりwall/HTTPとも小さくならなかった。1Mのpublic body readは0.077 sで、client decodeの削減を優先する証拠は今回の内訳にない。100k subsetの`ORDER BY` sortと1Mの増幅は、行あたりのengine仕事を同一にしていない。
- benchmark validation（order/footer scan + compare）は1M wallの約75–80%を占めるが、これは製品の返却コストではなく測定設計のコストである。meter tuningは行わない。
- 不確実: 接続再利用・JSON経路化の効果は過去phaseで観測済みだが、今回の24 runはコード変更前後の比較ではなく、同一phase内のroute差である。cache状態は不明。100Mの外挿はHTTPのみで、4GiB/600秒の制約から実行可能性を主張しない。

## 健全性・cleanup・repository

- `shirokuma doctor --profile local-experiment`: pre **healthy**（45 healthy + 1 informational）、post **healthy**（45 healthy + 1 informational）。
- Trino pod UID/restart、Workbench PID、host free、query履歴のpre/postを記録した。公開run分のquery recordはFINISHEDで、既存5件のUNKNOWNは変更していない。
- 直接Trino用の一時auth fileと所有tunnelはrun終了時に削除/停止を確認した。公開接続は既存profileのread-only参照を使い、新しいcredentialは残していない。サービス・Trino設定・benchmark source・GitOps 2差分・indexは変更していない。
- 認可・guard・stop/cancel・UNKNOWNの安全意味は変更していない。今回の24 runでinduced cancellationは0、reconciliation 0、既存CANCELED/UNKNOWN履歴は不変。
- Gitの追加変更はこのreport 1ファイルのみ。finalizationではこのreportだけをlocal commitし、pushしない。既存GitOps 2差分はunstagedのまま、indexは空を保持する。

## Evidence paths

- TASK: `/tmp/shirokuma-return-scale-2m5nued0`
- Evidence index: `/tmp/shirokuma-return-scale-2m5nued0/EVIDENCE-INDEX.md`
- Campaign: `/private/tmp/shirokuma-return-scale-20261116-c01/campaign`

## 制約

- 単一host、同時実行1、warm 3、initial 1。統計検定なし。initialはcold cacheの証明ではない。
- HTTP wire bytesは取得していない。JSONL file bytesを共通proxyとしてsec/MBとMB/sを出したが、HTTP wire bytes・compression・TLS overheadとは等価ではない。
- HTTP total/header waitはclient adapterの観測で、server内の純engine時間ではない。Trino CPUはcontainer cgroup、Workbench CPUは`ps time`差、RSSはsamplingで、process scope・丸め・欠測が異なる。
- full validationとartifact書出しはbenchmark側のコストを含む。製品の返却時間・SLO・100M/10M性能・他製品比較としては扱わない。

## 独立評価

実行者の初回結果（24/24成功、全値・型・重複、initial/warm表、guard/health）は変更せず保持する。Codexの独立再解析は、全24 attemptのstatus・guard/receipt・HTTP duration、24 unique backend IDを確認し、public/direct warm rep1のbyte-row multisetをDuckDB referenceと別途比較して3 sizeすべてで一致した。認可/guard/stop/UNKNOWNの安全契約は不変で、今回のrunにinduced cancellationはない。raw run/attempt/reference/guard/healthとGit差分は上記TASK evidence indexに保持する。

## 結論

同一SQL・同一snapshot・同一contextで100k/288,464/1Mの全値をdirect/public両経路で照合し、24/24成功・失敗0・未実行0を確認した。公開APIはdirectよりわずかに遅く、規模が大きくなってもHTTP差よりvalidation/artifactのbenchmarkコストが支配的だった。100Mのprimary追加HTTPは**439.2133 s**（within-rep paired warm delta中央値×100）、隣接傾き感度は**423.30–457.52 s**（CIではない）で、public absoluteは約**931 s**、directは約**491 s**。JSONL proxy約30.3 GBは4GiB peakを超え、public absoluteは600秒deadlineを超えるため、現guardのまま100Mは実行しない。bulkが必要ならpublic page preparation/waitを先に扱い、client decodeの推測やgeneralized overhead改善を今回の結果から優先しない。
