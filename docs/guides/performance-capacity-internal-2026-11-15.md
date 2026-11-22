# 性能試験: capacity scanの内部コスト調査と41 rootでのqualification再測定

2026-11-15 JST。`CapacitySampler(0.2)`、毎callbackのdeadline/local peak、scan後deadline再確認、remaining peak、forced boundary、fail-closedなstop/cancel、2TB cap、512MiB drainを変更せず、`open`/`getattrlistbulk`の内部コストを調査し、**新候補を作らず**、変更していないcontrol（先行proto3 6 worker）で41 rootの同一screenを再測定した。production code、test、runtime、benchmark contractは変更していない。本測定は**既存controlの繰り返しqualification**であり、新しい最適化の成果ではない。

## 観測できる範囲

| 手段 | 結果 |
|---|---|
| `sample`（自分の測定processのみ、3秒） | 成功。user-spaceと言語runtime、`libsystem_kernel`のstubまで |
| `fs_usage` | 失敗（root権限が必要） |
| `xctrace` | 失敗（full Xcode不在、CLTのみ） |
| `dtrace` | 失敗（SIP有効、追加権限が必要） |

失敗はいずれもstderrとexit codeを保存した。`sample`のcall graphにkernel frame（`namei`、`vn_open`、`vnode_lookup`、`mac_vnode_check`、`fd_install`、`getdirentries`、`copyout`、`kauth`）は出現せず、**kernel内部の時間内訳は本passの利用可能なtoolでは観測できていない**。frame出現回数は親子で二重に数えられ、exclusive時間ではない。

## 旧比較の撤回と修正版

**今回の初回比較**（本passで最初に書いたprobe。過去の正式screenや過去の記録を対象にしたものではない）は、1つのdirectoryを1回だけopenして3 variantを連続実行していた。`getattrlistbulk`はfdごとにdirectory offsetを持つため、最初のvariantだけが列挙し、残りは即EOFを読んでいた。したがって旧値（18,000 calls、3.061µs/call、「2%以内」）は**invalid**であり撤回する。旧rawとsourceはそのまま保持する。

修正版はvariantごとに**fresh fd**（計時外）で開き、必ず0（EOF）まで列挙し、roundごとに順序を回転させ、10 roundのrawを残す。対象はprobeが列挙できた4,503 directory（実行記録として`walk_truncated=0`、`path_truncated=0`）で、productionの全coverage証拠は別途41 root全mapで取得した。entry数はround 0で全directory・全variant一致（mismatch 0）を確認したが、**全roundの個別map検証ではない**。

| variant | 全round (ms) | 先頭位置の中央値 |
|---|---|---|
| full 256KiB | 26.62 / 29.94 / 51.94（min/median/max） | 50.34（n=4） |
| full 1MiB | 26.01 / 28.72 / 52.51 | 52.06（n=3） |
| `DEVID`なし 256KiB | 25.97 / 28.62 / 56.17 | 48.44（n=3） |

同じroundでは**先に実行されたvariantだけが48.409–56.172ms（中央値付近が48–52ms）、後続は約26–30ms**だった。これは実行位置による大きなelapsed差であり、cacheか内部処理かの**機構は特定していない**。位置を揃えた値は重なっており、このsample数ではvariant間の差を分離できない。1MiB化でdata callは4,491→4,489の2回減にとどまる。

`open`のflag別（249 directory × 3 pass = 各747回）: `O_RDONLY|O_DIRECTORY` 10.945µs、`O_SEARCH` 2.391µs、`O_EVTONLY` 10.909µs。`O_SEARCH`は1 directoryのprobeで`getattrlistbulk`がEBADFとなり、全rootでの確認はしていない。`DEVID`はper-file identityのため設計上維持しており、先行passの「削減利得なし」は測定結果であってidentity上の不可能性の証明ではない。parent-fd descentの10.791 vs 8.792µsは先行passの測定で、本passの実測とは分けて扱う。

## 候補

**新候補は作らない。** 修正版の測定ではvariant間の差を分離できず、`O_SEARCH`は使用不可、`O_EVTONLY`は変化なし、buffer 1MiBはcall 2回減である。位置依存の差の機構も未確認のため、これを根拠にした最適化は正当化できない。

## Frozen qualification screen

- scope: **41 root** = 直前39 rootのbaseline値をそのままcarry forward + 今回のtask tree全体 + campaign（ともにbaseline 0、disjoint）。campaignはtask tree外。
- `baseline_host_free`は先行manifestの**3089681522688**を保持。ceiling 2TB、drain 512MiB。各run前にscopeをassert。
- manifest `0242bb47…`、baseline snapshot `87b691c3…`、Python **3.12.12**。各runにmanifest、`capacity.py`、control dylib、interpreterを記録。
- 2 arms × 3 alternating checked/noop 1M fresh-process pairs = **12 measured runs / 6 pairs**。armはblock単位でrotate、warm-upは中央値から除外。writerは先行のseparate evidenceを再利用（未変更・再測定せず）。
- arms（測定前固定）: `baseline` = 現行production scandir、`control` = 変更していない先行proto3 subtree queue 6 worker。
- **41 root全mapと`used`のparityは、測定の前と後の2回、別processで取得**した（before → 全測定 → after）。両方とも全entryがproductionと一致し、`used`も一致。`used`は13,792,174,080 → 13,793,505,280へ増加し、本passのartifact growthを反映している。

| 指標 | baseline | control |
|---|---:|---:|
| 10-sample scan median / p95 / max | 155.72ms / 172.33ms / 190.26ms | 40.74ms / 42.36ms / **43.17ms** |
| in-situ per-scan | 160.86ms | **35.15ms** |
| 1M checked median | 141.61s | 88.82s |
| 1M no-op median | 75.54s | 74.23s |
| per-pair overhead median | 83.17% | **18.28%** |
| system CPU per checked run | 48.6–52.1s | 65.2–72.8s |
| maxrss | 98.6–114.9MB | 37.2–38.7MB |

raw pairs（checked / no-op / ratio / scans / per-scan）:

- baseline: 134.99 / 74.78 / 80.52% / 372 / 157.16ms、141.61 / 77.31 / 83.17% / 387 / 160.86ms、141.88 / 75.54 / 87.82% / 387 / 161.55ms
- control: 86.92 / 73.49 / 18.28% / 366 / 33.04ms、89.81 / 74.23 / 21.00% / 374 / 36.01ms、88.82 / 75.64 / 17.42% / 371 / 35.15ms

**判定: max scan <200ms は両armともPASS、offline paired overhead ≤10% はFAIL（best control 18.28%）。** 新候補が無いためnative helper・build dependencyはproductionへ入れず、`allocated`は`os.scandir`のまま据え置く。過去のscreen数値（16.55%、15.54%等）はそれぞれのscopeでの記録として変更しない。今回18.28%と前回15.54%の差はscope（41 root vs 39 root）と時点が異なるため、性能の改善・悪化として断定しない。

## 独立評価

独立reviewは、actual unchanged controlに対して会計・dedup・error・worker上限の既存7 testsを再実行してPASS、41 root全mapと`used`の一致、旧39 baselineの完全継承とdisjointを確認した。修正版probeも独立に再実行され（10 round × 3 variant = 30 record）、errors 0、entries 31,136一致、round 0の各directory cross-check 0、`walk_truncated`/`path_truncated` 0が確認された。12 measured runs（6 pairs）のrawからbaseline 83.16999%、control 18.28172%、scan max 190.259ms / 43.171msを再計算した。harness差分はpath・root数・importのみで、guard contractは同一と確認されている。本passでcode/runtimeの変更は無い。

**受入の範囲**: 利用可能なtoolで観測できた範囲の調査記録と、既存controlの再測定を受け入れる。kernel内部のper-call分析は**未達・未観測**、追加削減は**無し**、10%基準は**FAIL**のままである。

## 限界

- 単一host・単一session、n=3。controlのper-pair ratioは17.42–21.00%に散り、checked・no-op双方が変動した。criteria・coverage・sampling intervalは変更していない。
- probeは`opendir`失敗を無視し`DT_UNKNOWN`を降りないため、**列挙できたdirectory subset**の測定である。全coverageの証拠は41 root map。
- `sample`はframe出現のみでkernel内部時間ではない。profile observer overheadは同一flag・同一root・6 worker・interleaved 10 sampleで+4.53%であり、elapsedの比較であってCPU時間ではない。
- prototype限定で、全保証のqualifiedを主張しない。productionのdeadline/local peak/stop/cancelコードは変更していない。

## 次のevidence gap

ユーザー空間の残差は小さく、修正版測定でも追加の正当化された変更が無い。残る不明点はkernel内部のper-call内訳で、本passの利用可能なtoolでは観測できていない。次は利用可能な方法を検討する段階であり、特定手段の要否や権限の判断はここでは行わない。criteria・interval・coverageの変更は提案しない。本結果はG1/性能/SLOの受入ではない。

## 証拠

- probe（`proto`）: `internals_probe.c`、`attr_buffer_probe.c`（invalid、保持）、`attr_buffer_probe2.c`、`sample_target.py`、`map_parity.py`、`qual41.py`、`screen_lib4.py`、`measure_screen4.py`、`measure_offline6.py`。
- 調査: `evidence/internals-evidence.md`、`attr-buffer-probe2.log`、`attr-buffer-probe2-analysis.log`、`sample-analysis.txt`、`tool-*.err/exit`、`api-contract-snippets.txt`。
- qualification: `runtime/campaign-01/manifest-frozen.json`、`qual41.json`、`runtime/screen-01/{map-before,map-after}.json`、`scan-*.json`、`offline-runs.json`、`screen-summary.json`。
- 先行artifact（proto2 / proto3 / c02〜c04 campaign）は未変更で保持する。
