# 性能試験: capacity scanのsubtree work queueとrequested attribute検証

2026-11-15 JST。`CapacitySampler(0.2)`、毎callbackのdeadline/local peak、scan後deadline再確認、remaining peak、forced boundary、fail-closedなstop/cancel、2TB cap、512MiB drainを変更せず、full-global scanのper-scan costを下げられるかをGit外prototypeで再測定した。production code、test、runtime、benchmark contractは変更していない。

## 本passのprototypeにあった2件の欠陥（独立reviewで発見）

本passで新規に書いた`ca_scan`（別entry point）に2件の欠陥が見つかり、版ごとに修正した。いずれも本passのprototype固有で、先行bulk記録のv3（`ca_allocated_many`によるroot単位worker）とその19.99%は変更なく有効である。

1. **混合batchの会計bug（初期`proto`で混入、`proto2`で修正）**: directory root用のmerge loopを全rootに適用していたため、混合batchでfile root（VM image、1M payload等）の直接測定値が0で上書きされた。単一root比較とattribute mode同士のbatch比較では検出できない。35 root batchで初期buildは4 root（VM image 2件、1M JSONL 2件）が0、修正buildは0 mismatch。
2. **失敗経路のdescriptor leak（`proto2`のerrno cleanup変更で混入、`proto3`で修正）**: 初期`proto`は`close`を無条件に呼んでいたため漏れは無かったが、`proto2`でerrno捕捉のため`if (rc == 0 && close(fd) != 0)`へ書き換えた結果、失敗したdirectoryで`close`が実行されずdescriptorが残った。5回の失敗scanでfd数が4→9へ増加（`proto3`は4→4）。

修正内容:

- 非directory rootは直接測定値として保持し、mergeはdirectory rootのみに適用。
- descriptorは全経路で必ずcloseし、closeの失敗は他に原因がない場合のみ報告する（最初のerrorを保持）。
- 併せてworker/cleanupの`errno`をfree・mutex・closeより前に捕捉する形へ修正した。queueのpending会計とerror停止・joinは不変で、queued workを抱えた失敗でもhangせず再実行可能なことをtestした。
- 追加test: 混合rootの順序permutation（file前/後、空dir、fileへのsymlink root）6通り × worker 1/4/6/8 × attribute mode 2通り、実35 root batch vector（1/4/6/8 × 2 modeで全一致）、campaign `used=sum(max(0,current-baseline))`、file baselineからの実growth、5回連続失敗後のfd数不変と再実行成功。

## Requested attribute削減の検証（不採用）

`ATTR_CMN_DEVID`を落とし、containing directoryの`fstat` devを使う案を実装・検証した。mount pointを含む4ディレクトリ（38 entry）で、`getattrlistbulk`の`DEVID`/`FILEID`と`fstatat`の`st_dev`/`st_ino`を比較すると、不一致は**すべてmount pointのdirectory**で、非directoryは0件だった。

ただし観測した少数のmount rootはregular fileのidentity一般を証明しない。加えてdirdev/devidのscan cost差は測定誤差内（4 worker 35.33 vs 35.55ms、5 worker 31.53 vs 32.35ms、8 worker 36.64 vs 35.69ms）で利得が無いため、**per-file DEVIDを維持**し、attribute削減は不採用とする。追加の仮定を増やさない。

## Work queue設計

- 単一のglobal pool（最大8 worker、nested poolなし）と`(root, directory)` itemのmutex+condvar work-stealing queue。
- 各rootは独立したseenを持ち、そのrootの全subtreeのentryをworker-local vectorへ集約し、traversal終了後にroot単位でmerge・sort・dedupeする。cross-rootのdedupはしない。
- 最初のerrorでpoolを停止し、queueを解放して全threadをjoinし、`errno`を保持して部分結果を返さない。listingが`VDIR`のentryだけを再帰する。
- measured candidate（`use_devid=1`）のidentityはper-file `ATTR_CMN_DEVID` + `ATTR_CMN_FILEID`とsymlink targetの`stat`から作る。directoryの`fstat` deviceはexperimentalなdirdev mode専用で、measured candidateのdedup identityの上書きには使わない（追加のarchitecture変更なし）。

## Frozen measurement

- **35 root** = 直前33 rootのbaseline値をそのままcarry forward + 今回のtask tree全体 + campaign。task treeとcampaignはbaseline 0で、新規artifactを除外しない。campaignはtask tree外に置いた。
- `baseline_host_free`は先行manifestの**3089681522688**をそのまま保持。ceiling 2TB、drain 512MiB。各runの前にscopeとhost baselineの不変をassertした。
- Python **3.12.12**（`benchmarks/local_perf/.venv/bin/python`）。manifest `70f20ecc…`、baseline snapshot `9030e086…`。各runにmanifest hash、`capacity.py`、prototype source、dylib、interpreter executable+versionを記録。armはblock単位でrotateしてinterleaveし、各armのchecked warm-upは中央値から除外。
- 3 arms × 3 alternating checked/noop 1M fresh-process pairs = 18 measured runs。同一left/right・同一order・同一compare、offline deadline 1800s、4GiB local peak。writerは先行のseparate evidenceを再利用（未変更・再測定せず）。

arms（結果前に固定）: `baseline` = 現行production scandir、`subtree6` = 主候補（cap 8に対し6 worker）、`subtree8` = 比較用。

計測のprovenance: 下記18 run（9 pair、各arm 3 pair）の性能値は**proto2 binary**（成功経路）で取得した。descriptor cleanupの修正はproto3で行い、proto3ではparser 28件・scheduler/accounting 10件・35 root vector parityを再実行して全passしているが、性能は再測定していない。error経路のみの変更でreadinessは既に不合格、production採用も無いため18 runの再実行は行わない。

| 指標 | baseline | subtree6 | subtree8 |
|---|---:|---:|---:|
| 10-sample scan median / max | 148.88ms / 186.09ms | 28.99ms / **31.06ms** | 38.35ms / 46.63ms |
| in-situ per-scan | 153.02ms | **32.55ms** | 37.91ms |
| 1M checked median | 137.50s | **88.27s** | 90.18s |
| 1M no-op median | 75.53s | 74.41s | 75.23s |
| per-pair overhead median | 79.59% | **18.62%** | 20.08% |
| system CPU per checked run | 46.9–49.0s | 64.1–65.4s | 100.6–102.3s |
| maxrss | 104–112MB | 36.7–40.1MB | 38.4–40.1MB |

raw pairs（checked / no-op / ratio / scans / per-scan）:

- baseline: 132.93 / 75.10 / 77.01% / 371 / 152.62ms、137.50 / 75.53 / 82.04% / 384 / 153.04ms、138.47 / 77.10 / 79.59% / 387 / 153.02ms
- subtree6: 86.52 / 75.00 / 15.36% / 366 / 32.55ms、88.27 / 74.41 / 18.62% / 371 / 32.73ms、88.87 / 74.32 / 19.58% / 375 / 31.97ms
- subtree8: 89.97 / 74.93 / 20.08% / 371 / 37.63ms、90.74 / 75.23 / 20.62% / 374 / 37.97ms、90.18 / 75.85 / 18.90% / 372 / 37.91ms

独立reviewは`proto3`のparser 28件とscheduler/accounting 10件を再実行し、35 disjoint root・旧33 baseline・host baseline・新規0 baselineを確認、18 raw runすべてでPython 3.12.12・manifest・proto2 source/binary fingerprintを照合し、同時thread数≤8を確認した。subtree6 median **18.624168743%** / max **31.063709ms**、subtree8 **20.077512767%** / **46.630208ms** を再計算し、sessionのmodelも実測で確認している。

**判定: max scan <200ms は両候補PASS、offline paired overhead ≤10% はFAIL（best 18.62%）。** 10%に必要な差は約7.44秒で、subtree6の実測差は約13.87秒。native helper・build dependency・Makefile変更はproductionへ入れず、`allocated`は`os.scandir`のまま据え置く。

## 計測上の限界

- 6 workerが8 workerより速い逆転は本hostで再現した（scan 29.0 vs 38.4ms、offline 18.62% vs 20.08%）。system CPUが6 worker 64–65s、8 worker 101–102sと増えており、kernel側のmetadata競合として整合するが、原因の切り分けは未実施。
- 単一host・単一sessionの測定で、worker数感度はrun間で±10–20%変動する。
- prototype限定の証拠であり、全保証のqualifiedを主張しない。productionのdeadline/local peak/stop/cancelコードは変更しておらず、offlineのguardは維持し、error経路のcleanupは今回targetedに検証した範囲にとどまる。runtimeのfailure mode qualification、native workload、full regressionは本taskで実施していない。
- per-scan costの内訳（open/`getattrlistbulk`/`fstat`/parse）は未分解。

## 次の具体的な一歩

同じcoverage・同じ保証・同じ8 worker上限のまま、per-directory costの内訳を計測し、`openat`による親fd経由のdescendやdirectory単位のsyscall削減でper-scan costを下げられるかをprototypeで確認する。10%達成を主張せず、criteria・coverage・sampling intervalの緩和は提案しない。本結果はG1/性能/SLOの受入ではない。

## 証拠

- 計測に使用したprototype（`proto2`, source `6d400caa…` / dylib `403d3b2c…`）: `bulk_core2.c`、`bulk_core2.h`、`parse_test.c`、`sched_test.py`、`screen_lib.py`、`measure_screen.py`、`measure_offline.py`。
- cleanup修正版（`proto3`, source `a79100f8…` / dylib `64d60d37…`）: 同じsourceにclose修正を加えたもの、fd再現script、parser/scheduler/vector parity log。
- corrected frozen run（`runtime/correction-01`）: `manifest-frozen.json`、`scan-*.json`と`.log`、`offline-runs.json`、`screen-summary.json`、`bug-repro.log`、`campaign-build.log`。
- 本passのprototype系列は、初期`proto`（混合batch会計bug）、`proto2`（会計修正＋errno cleanupでdescriptor leak混入）、`proto3`（両方修正済み）として未変更で保持する。pass-1 evidenceも未変更。先行bulk記録のv3/`correction-01`は有効なまま未変更。
