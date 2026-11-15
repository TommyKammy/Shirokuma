# 性能試験: capacity scanのdispatch/merge分解と39 rootでのqualification再測定

2026-11-15 JST。`CapacitySampler(0.2)`、毎callbackのdeadline/local peak、scan後deadline再確認、remaining peak、forced boundary、fail-closedなstop/cancel、2TB cap、512MiB drainを変更せず、per-directory costの残差（root setup、worker start/join、queue、merge/dedup）を分解し、**新候補を作らず**、変更していないcontrol（先行proto3 6 worker）で39 rootの同一screenを再測定した。production code、test、runtime、benchmark contractは変更していない。本測定は**繰り返しqualification**であり、新しい削減の証明ではない。

## 残差の分解

先行passのinstrumented build（37 root、4,494 directory、8,976 `getattrlistbulk` call、6 worker、wall 27.69ms）で境界を追加計測した。

| wall内訳 | ms | wall比 |
|---|---:|---:|
| root setup（stat/open/lock init） | 0.065 | 0.2% |
| worker lifetime（spawnからjoinまで） | 26.53 | 95.8% |
| merge / sort / per-root dedup | 1.08 | 3.9% |

worker lifetimeは**処理と待ちを含む並列区間の長さ**であり、spawn/join自体のコストではない。上記3項の和がwallに一致するのは**選択した測定境界の中**でのことであり、timer・cleanupなど未計測の残差がないことの証明ではない。

worker側timerの合計（6 worker合計154.2ms、1 worker平均25.7ms）の内訳:

| phase | worker時間share |
|---|---:|
| `open` | 52.33% |
| `getattrlistbulk` | 42.22% |
| record parse | 2.13% |
| `close` | 1.41% |
| `fstat` | 1.40% |
| queue lock | 0.15% |
| queue wait | 0.31% |
| enqueue | 0.05% |

**この合計はworkerごとのelapsed timerの和であり、CPU時間でもcore並列効率でもない。** 6 worker合計/6≒25.7msとwall 27.69msの比はworker占有のproxyにすぎず、「並列効率92.8%」のようなCPU効率の主張はしない。queue waitが小さいことは、このsampleでworkerがqueue待ちで止まらなかったことの観測であり、dispatchが最適である証明ではない。

merge/dedup 1.08ms（wallの3.9%）とqueue系（lock 0.24ms + enqueue 0.08ms + wait 0.47ms、いずれも合計値）は**別々に**扱う。両者を足して「削減可能上限4.6%」のような形式的な上界は主張しない。profiler overheadは同一flag・同一root・6 worker・interleaved 10 sampleで**約2.59%**であり、phase shareは近似である。

## API completeness: EOF表現の有無

installed SDKとman pageの該当箇所を`evidence/api-contract-snippets.txt`に保存した（macOS 27.2.1、SDK 27.2）。

- `man 2 getattrlistbulk` RETURN VALUES: 「A value of 0 indicates there are no more entries. Once 0 is returned, no further entries are returned even if new entries are added to the directory.」および「When iterating all entries in a directory, getattrlistbulk() is called repeatedly until a 0 is returned.」— **0が完了signalであり、それ以外の完了通知はcontractに無い**。
- `sys/attr.h`の`FSOPT_*`（`NOFOLLOW`, `NOINMEMUPDATE`, `REPORT_FULLSIZE`, `PACK_INVAL_ATTRS`, `ATTR_CMN_EXTENDED`, `RETURN_REALDEV`, `NOFOLLOW_ANY`, `RESOLVE_BENEATH`, `UNIQUE`）にbatch完了/EOFを示すflagは無い。
- `ATTR_DIR_ENTRYCOUNT`は静的probe（0/100/5000 file）で実数と一致したが、取得には専用の`fgetattrlist`呼び出しが必要で、data call＋終端callと**同じcall数**になる。さらに読み取り後に追加されたentryは検出できず、man pageは非HFS+で「usually expensive」としている。並行変更下の安全性は仮定であり、採用しない。

したがって、検討した`getattrlistbulk` contractの範囲では**終端0件callを安全に省略できない**。これは特定flagの確認であって「あらゆるAPIが存在しない」証明ではなく、`fts(3)`やper-file `getattrlist`のcall数見積りも実benchmarkではない。

## 候補

**新候補は作らない。** dispatch（queue lock/enqueue/wait）とmerge/dedupは上記の通り小さく、syscallはworker時間の94.5%を占める。検討した選択肢（終端call省略、ENTRYCOUNT併用、merge/dedupのデータ構造変更、queue batching）はいずれも**この測定では正当化されない**。残るコストは`open`と`getattrlistbulk`の1 callあたりの、**user-spaceから観測されたelapsed**が支配的である。kernel内部の内訳は未調査で、本passではtracingを実施していない。ユーザー空間で今後も削減不能という主張はしない。

## Frozen qualification screen

- scope: **39 root** = 直前37 rootのbaseline値をそのままcarry forward + 今回のtask tree全体 + campaign（ともにbaseline 0、disjoint）。campaignはtask tree外。
- `baseline_host_free`は先行manifestの**3089681522688**を保持。ceiling 2TB、drain 512MiB。各run前にscopeをassert。
- manifest `156950d6…`、baseline snapshot `af0a3ea9…`。Python **3.12.12**（`benchmarks/local_perf/.venv/bin/python`）。各runにmanifest、`capacity.py`、control dylib/source、interpreterを記録。
- 2 arms × 3 alternating checked/noop 1M fresh-process pairs = **12 measured runs / 6 pairs**。armはblock単位でrotate、warm-upは中央値から除外。writerは先行のseparate evidenceを再利用（未変更・再測定せず）。
- arms（測定前固定）: `baseline` = 現行production scandir、`control` = 変更していない先行proto3 subtree queue 6 worker。

| 指標 | baseline | control |
|---|---:|---:|
| 10-sample scan median / p95 / max | 148.42ms / 163.82ms / 197.97ms | 27.02ms / 28.87ms / **28.89ms** |
| in-situ per-scan | 164.75ms | **32.86ms** |
| 1M checked median | 142.78s | 87.96s |
| 1M no-op median | 76.50s | 74.22s |
| per-pair overhead median | 84.89% | **15.54%** |
| system CPU per checked run | 51.7–53.1s | 64.1–68.7s |
| maxrss | 94.2–102.2MB | 36.7–41.0MB |

raw pairs（checked / no-op / ratio / scans / per-scan）:

- baseline: 137.16 / 74.19 / 84.89% / 369 / 166.45ms、144.47 / 78.26 / 84.60% / 389 / 164.75ms、142.78 / 76.50 / 86.63% / 388 / 162.03ms
- control: 85.76 / 74.22 / 15.54% / 362 / 32.86ms、87.96 / 73.33 / 19.95% / 369 / 34.51ms、88.99 / 79.48 / 11.96% / 375 / 32.63ms

**判定: max scan <200ms は両armともPASS（baseline 197.97msは上限に近い）、offline paired overhead ≤10% はFAIL（best control 15.54%、独立再計算15.5407%）。性能10%の不受入は維持する。** 新候補が無いためnative helper・build dependencyはproductionへ入れず、`allocated`は`os.scandir`のまま据え置く。

## 正しさ

- 39 root whole-map parityは、**offline測定の完了後に`qual_check`が連続2回実行したもの**である（offline runを挟んだbefore/afterではない）。両回とも全entryがproduction `allocated`と一致（0 mismatch）、campaign `used`もproductionの`Campaign.check`と一致（13,770,625,024 bytes）。workerは6（≤8）。offline測定そのものの前後比較ではないため、scan開始時の`used`とは区別して扱う。
- error/FD: 20 directoryを抱えたtreeで5回連続EACCES、descriptor数4→4で不変。broken symlink・missing rootはENOENT、失敗後の再scanは正常値を返す。campaign `used`は作成時13,768,826,880から13,770,625,024へ増加しており、本passのartifact growthを反映している。
- 先行passのparser 28件・scheduler/accounting 7件・mixed file+directory root・lazy symlink path・失敗時のcloseは変更していない（control binaryは先行と同一hash）。
- 独立reviewは**actual unchanged control**に対してscheduler/accounting 7件を再実行してPASS、current `parse_test4`でparser 28件PASS、39 rootの全map一致を確認した。reviewerの初回実行はtest側のctypes `Stats` class型不一致で7 ERRORとなり、reviewer側で同一classに揃えて再実行している（prototypeの欠陥ではない）。12 measured rawのmedianも独立再計算され、baseline 84.8923% / control 15.5407%、scan max 197.969ms / 28.891msだった。

## 限界

- interleaved A/B（n=10）でcontrol 29.90ms / 再build baseline 29.46msは「観測された明確な差が無い」ことを示すだけで、形式的な同等性検証ではない。
- 単一host・単一session、n=3。controlのper-pair ratioは11.96–19.95%に散る。no-opが73.33–79.48s、checkedが85.76–88.99sと**双方が変動**しており（medianはno-op 74.22s）、散らばりをno-op変動だけに帰属させない。criteria・coverage・sampling intervalは変更していない。
- prototype限定で、全保証のqualifiedを主張しない。productionのdeadline/local peak/stop/cancelコードは変更していない。
- `open`・`getattrlistbulk`の約9–10µs/callは**user-spaceから観測されたelapsed**（前passのmicro-benchmark、Python計測）であり、kernelが専有した時間ではない。kernel内部の内訳は本passで調査していない。

## 次のevidence gap

ユーザー空間の残差（dispatch、merge/dedup）は小さく、今回の検討範囲では追加の正当化された変更が無い。**本passの調査はここで完了**であり、新しい最適化の成果ではない。残る不明点はkernel内部のper-call内訳（APFS上の`open`と`getattrlistbulk`）で、本passでは計測していない。次は利用可能な方法を検討する段階であり、特定手段の要否や権限の判断はここでは行わない。criteria・interval・coverageの変更は提案しない。本結果はG1/性能/SLOの受入ではない。

## 証拠

- prototype（`proto`）: `bulk_core4.c`、`bulk_core4.h`、`parse_test.c`、`parse_test4`、`sys_test.py`、`api_probe.c`、`measure4.py`、`screen_lib3.py`、`measure_screen3.py`、`measure_offline5.py`、`qual_check.py`。
- 残差分解: `evidence/decomposition.json`、`evidence/measure4.log`、`evidence/ab-control.log`、`evidence/api-contract-snippets.txt`。
- qualification: `runtime/screen-01/manifest-frozen.json`、`scan-*.json`、`offline-runs.json`、`screen-summary.json`、`qual-check.json`、`campaign-build.log`。
- 先行artifact（proto2 / proto3 / c02・c03 campaign、phase-01）は未変更で保持する。
