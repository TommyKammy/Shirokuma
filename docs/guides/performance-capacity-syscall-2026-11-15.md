# 性能試験: capacity scanのper-directory cost分解とsyscall削減prototype

2026-11-15 JST。`CapacitySampler(0.2)`、毎callbackのdeadline/local peak、scan後deadline再確認、remaining peak、forced boundary、fail-closedなstop/cancel、2TB cap、512MiB drainを変更せず、full-global scanのper-directory costを分解し、最小のsyscall削減候補をGit外prototypeで実測した。production code、test、runtime、benchmark contractは変更していない。

## Cost分解

instrumented build（`CA_INSTRUMENT`、timersは`clock_gettime`）で**先行35 root campaign**（4,486 directory）を8 workerで1回走査した。frozen paired screenのscopeは37 rootだが、この分解は35 root時点の測定である。phase合計はworker横断の**合計**でありwall timeではない。

| phase | 時間share |
|---|---:|
| `open`（path lookup） | 57.5% |
| `getattrlistbulk` | 37.3% |
| `close` | 1.7% |
| record parse | 1.6% |
| `fstat` | 1.4% |
| queue lock / enqueue | 0.4% |

literal call counts（上記35 root、記録したphase内のみ。root setup等その他callは含まない）: open 4,486、fstat 4,486、`getattrlistbulk` 8,960、close 4,486、合計22,418。上位2**種**（open + bulk）で13,446 call = 全callの60.0%、phase時間では94.8%を占める。`getattrlistbulk`の内訳はdata callと終端0件callで、終端callは完全性の証明を保たない限り省略できない。

profiler overheadは**frozen 37 root manifest**を対象に、同一flag・同一worker数（8）・interleaved 12 sampleで比較した**約+1.17%**（36.89ms → 37.32ms）。分解（35 root）とoverhead確認（37 root）は別scopeであり、shareは近似で厳密な帰属ではない。

## Parent-fd descentの検証（不採用）

root-fd `openat`はroot prefixを落とすだけで、immediate-parentの単一component descentではない。そこで同一のparent fd上で両形式をback-to-backに計測した（4,455 directory、8,910 sample）: root-relative `openat` median **10.791µs**、immediate-parent単一component median **8.792µs**、paired差 **-1.75µs**/open。

4,486 openでは6 workerの理想並列を仮定すると約1.3msのwall削減に相当するが、これはend-to-endで実測した値ではなくhard boundでもない（run間変動±10–20%）。加えてqueueへfd所有権を渡す方式は、enqueue後に削除されたdirectoryをfd経由で走査してしまう意味論変更を伴う。したがって**今回はdefer**し、root-fd候補を採用した。path最適化全般を否定するものではない。

## Candidate

採用した候補（新規、`cand2`）は4点のみ:

- per-file `ATTR_CMN_DEVID` + `ATTR_CMN_FILEID` とsymlink targetの`stat`でidentityを作る（attribute削減はしない）。
- per-file DEVIDを保持する場合に不要なdirectory `fstat`を省略（dirdev modeでは従来どおり）。
- 通常fileのentryでpath文字列を組み立てない（directoryとsymlinkのみ）。
- workerごとにbufferとchild-path vectorを再利用し、directory rootごとに最大1本のpinned FDへ`openat`する。使用descriptorは**pinned FD（directory rootごとに最大1本）＋workerごとのworking FD 1本**で、これはhost全体のdescriptor上限ではない。全経路でcloseする（failure cleanupのみ検証し、runtime全体のqualificationではない）。

## Frozen measurement

- scope: **37 root** = 直前35 rootのbaseline値をそのままcarry forward + 今回のtask tree全体 + campaign（ともにbaseline 0）。campaignはtask tree外。
- `baseline_host_free`は先行manifestの**3089681522688**を保持。ceiling 2TB、drain 512MiB。各run前にscopeをassert。
- manifest `eeea6d64…`、baseline snapshot `1f52b506…`。Python **3.12.12**。各runにmanifest、`capacity.py`、候補source/dylib、control dylib、interpreterを記録。
- 3 arms × 3 alternating checked/noop 1M fresh-process pairs = 18 measured runs。armはblock単位でrotate、warm-upは中央値から除外。writerは先行のseparate evidenceを再利用（未変更・再測定せず）。
- arms（測定前に記録）: `baseline` = 現行production scandir、`control` = 変更していない先行proto3 subtree queue 6 worker、`primary` = 新候補 `cand2` 6 worker。

| 指標 | baseline | control | primary |
|---|---:|---:|---:|
| 10-sample scan median / max | 149.27ms / 176.69ms | 29.95ms / 43.33ms | 27.78ms / **38.55ms** |
| in-situ per-scan | 153.76ms | 32.76ms | **32.17ms** |
| 1M checked median | 134.27s | 86.16s | 86.98s |
| 1M no-op median | 74.34s | 73.75s | 73.76s |
| per-pair overhead median | 79.26% | **16.55%** | **17.35%** |
| system CPU per checked run | 46.8–48.0s | 62.6–64.8s | 64.3–65.5s |
| maxrss | 104–114MB | 36.7–37.0MB | 36.9–40.5MB |

raw pairs（checked / no-op / ratio / scans / per-scan）:

- baseline: 131.90 / 73.78 / 78.77% / 368 / 153.44ms、134.27 / 74.90 / 79.26% / 374 / 153.80ms、135.20 / 74.34 / 81.87% / 376 / 153.76ms
- control: 84.90 / 72.84 / 16.55% / 359 / 32.41ms、86.16 / 73.75 / 16.83% / 364 / 32.88ms、86.95 / 74.66 / 16.46% / 367 / 32.76ms
- primary: 85.50 / 72.86 / 17.35% / 363 / 32.17ms、86.98 / 74.89 / 16.14% / 369 / 31.91ms、87.48 / 73.76 / 18.61% / 369 / 32.20ms

**判定: max scan <200ms は3 armともPASS、offline paired overhead ≤10% はFAIL。bestはcontrol 16.55%、新候補primaryは17.35%で、**今回の測定では明確な改善を確認できない**（n=3の形式的な同等性検定はしていない）。** 新候補の採用根拠が無いためnative helper・build dependency・Makefile変更はproductionへ入れず、`allocated`は`os.scandir`のまま据え置く。

## 正しさ

- injected-record parser test **28件**を候補ごとのbuild（baseline / cand / cand2 / instrumented）で個別に実行し、全pass。macroがparse pathを変えるためbuildごとに確認した。
- 37 root whole-map parity（`cand`/`cand2`、worker 1/6/8、DEVID/dirdev両mode）で全entryがproduction `allocated`と一致（frozen screenはこの修正後に実施）。campaign `used`の前後比較、directory/file/symlink rootの順序入替、root内の全subtreeにまたがるhardlink dedup、errno停止とjoin、5回連続失敗後のfd数不変（両候補）、worker数上限を確認。
- 先行passで見つかった2件（混合batch会計bug、`proto2`由来のfd leak）は修正済みで、その履歴は[subtree work queue試験](performance-capacity-subtree-2026-11-15.md)に残す。今回の候補開発中に、lazy path化でsymlink targetのpathが未初期化になる欠陥を自分で作り、**最初の35-root vector test**が検出したため修正した。37 rootでのfull screenは修正後にのみ実施しており、過去の記録・測定には影響しない。
- ある1回の測定で1 rootだけがbase/cand/cand2の全rowでずれた。専用probeではdrift 0、再実行もcleanだったが、外部要因と**断定はしない**（未解決として記録）。

## 独立review

独立reviewはparser 28件 × 4 build = **112 check**とscheduler/accounting 7件を再実行し、37 root whole-map、旧35 baseline・host baseline・新規0 baselineのdisjoint 37 rootを確認した。18 raw run / 9 pairすべてでinterpreter 3.12.12、source/control/primary binary、manifestのfingerprint一致、0.2 intervalで7819 guard check、実worker 6（≤8）を確認し、baseline 79.261519%、control 16.552184%、primary 17.350496%、primary max 38.549916msを再計算した。sessionのmodelも実測で確認している。

## 限界

- prototype限定で、全保証のqualifiedを主張しない。productionのdeadline/local peak/stop/cancelコードは変更していない。
- 単一host・単一session。worker数感度は±10–20%。6 workerが8 workerより速い逆転はsystem CPUの増加と整合するが原因は未特定。
- phase shareはtimers込みの近似（約+1.17%）で、worker側のCPU時間であってwall timeではない。call countは本dataset・記録phase内の観測であり、一般的な保証ではない。
- per-scan costの下限見積りは測定であって証明ではない。

## 次の具体的な一歩

costはopen 4,486回と`getattrlistbulk` 8,960回に集中しており、この2種が支配的である。次は同じcoverage・同じ保証・8 worker上限のまま、(a) APIを保ったままcall数を減らせるか、(b) merge/dedupやqueue配送などsyscall以外の残差の内訳を測る。終端0件callは完全性の証明を保たない限り省略せず、安全でないshortcutは取らない。10%達成を主張せず、criteria・coverage・sampling intervalの緩和は提案しない。本結果はG1/性能/SLOの受入ではない。

## 証拠

- prototype（`proto`）: `bulk_core3.c`、`bulk_core3.h`、`parse_test.c`、`sys_test.py`、`micro_open.py`、`measure.py`、`screen_lib2.py`、`measure_screen2.py`、`measure_offline4.py`、build log。
- frozen run（`runtime/phase-01`）: `manifest-frozen.json`、`selection.json`、`scan-*.json`と`.log`、`offline-runs.json`、`screen-summary.json`、`prof-overhead.log`、`sys-test.log`、`campaign-build.log`。
- 先行passの成果物（proto2 / proto3 / c02 campaign）は未変更で保持する。
