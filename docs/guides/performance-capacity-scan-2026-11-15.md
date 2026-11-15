# 性能試験: full-global capacity scanのscandir最適化

2026-11-15 JST。`CapacitySampler(0.2)`、毎callbackのdeadline/local peak、scan後deadline再確認、remaining peak、forced boundaryを変更せず、`allocated`の実装だけを`Path.rglob`/`Path.is_file`/`Path.stat`から単一threadの`os.scandir`へ置き換えた。cross-root hardlink dedup、cache、sampling interval変更、root除外は行っていない。

## 変更

- `benchmarks/local_perf/capacity.py` の `allocated` のみ。
- 各rootで独立したseen setを維持し、hardlinkはroot内で1回、root間は独立に計上。
- symlink fileはtarget regular fileとして計上し、child-directory symlinkは再帰しない。明示的に渡された解決済みrootは通常どおり走査する。sparseは`st_blocks`を使用。
- 毎scanでlive traversal。traversal/stat errorは伝播し、skipしない。broken symlinkは旧実装がsilent skipし得たが、今回fail-closedへ強化しtestした。
- `Campaign.check`のbaseline、used=sum(max(0,current-baseline))、2TB cap、host reserve、512MiB drain、snapshot形式は不変。

## Before / after

同一31-root accounting scope（root数31、VM/state original baseline、SDK/artifact rootsを含む）で測定した。manifest bytesはowned attemptのstatus更新等で変わるためbyte-identicalではなく、before/after間の新規run/evidence growthはused bytesに反映し、baselineはresetしていない。

| 指標 | before | after |
|---|---:|---:|
| 10 whole `Campaign.check` median | 332.3ms | **145.6ms** |
| p95（floor .95*(n-1), n=10） | 353.2ms | **148.8ms** |
| max | 393.7ms | **170.6ms** |
| used bytes | 12,563,472,384 | 13,177,335,808 |

**Max scan <200ms: PASS.** 10 samplesのempirical maxでありworst-case boundではない。

Offline 1M checked/no-op 3 alternating fresh-process pairs。reported wrapper elapsedはexternal pre/post capacity checkとscratch cleanupを含む。

| 指標 | before | after |
|---|---:|---:|
| checked median | 189.61s | **134.13s** |
| no-op median | 76.22s | **75.18s** |
| per-pair overhead median | 150.7% | **78.4%** |
| checked global scan | 364–373 / 110.7–115.2s | **376–384 / 55.5–57.4s** |
| local guard | 0.595–0.681s | 0.407–0.440s |

**Offline overhead <=10%: FAIL.** The scan implementation improved max latency and roughly halved overhead, but 31-root full-global scans remain too costly at the frozen 0.2s sampler. No cache/interval/root exclusion was introduced.

Native large-result on retained SF0.1 pins, default150, 600s, 4GiB peak, 3 alternating direct/public pairs: **6/6 SUCCESS**, 288,464 rows each, full type/value/duplicate/order comparison. After attempt elapsed **75.154–81.915s**; guard global 213–228 scans / 32.1–34.4s, local 0.44–0.50s. Child CPU/RSS via `wait4`: direct user 40.7–41.4s/system 31.2–31.4s/maxrss 140.8–144.2MB; public user 41.6–42.0s/system 32.9–33.2s/maxrss 134.2–143.0MB. Observer maxrss 39.2–40.0MB. Exposure using max scan0.1706s, observed growth/gap and max in-flight delta: **61.6–67.5MiB**, within 512MiB drain (observed, not a hard bound).

## 安全性・検証

- `CapacitySampler(0.2)`、deadline/local peak per callback、forced boundaries、stop/cancel/UNKNOWN handling unchanged.
- `test_local_perf`に2 test methodを追加し、live add/delete、hardlink、symlink file/dir、cross-root hardlink independence、broken symlink/unreadable dir fail-closedを確認した。sparseとcap/drain/host-freeは既存testを保持し、新規追加とは数えない。既存timings/preflight assertionsも保持した。
- Focused: `test_local_perf` 12, `test_local_perf_timings` 25, `test_local_perf_preflight` 10; Python 3.12 and 3.9 pass.
- Full Make: 162 commands, **5,950 total / 5,876 pass / 74 skip / 0 failed**, source unchanged.
- Runtime unchanged: stage default150, coordinator/worker resources/UIDs baseline, old59 pins SHA `33eb6146...`, new24 pins verified, unresolved0, doctor46. No new data, L1, 100GB or SLO work.

## 独立評価

reviewerは58 focused tests（12+25+10+6+5）、6 native large-result sets（各288,464行、full values/types/duplicates/configured order）、current runtime settings/default150/CM/templates/UIDs/doctor46/old59 pins/new24 pinsを独立確認した。evidenceはreviewer-focused/reviewer-results/reviewer-live。

## 次

full-global scanは200msを満たしたが、offline 10% screenは未達（current 78.4%）。次はdeadline/local peak/stop保証とroot coverageを維持したまま、per-scan costをさらに下げるOS bulk-attribute取得等を検討する。sampling interval延長、cache、root除外、threshold変更は行わない。G1/性能/SLOの受入とはしない。

## 追記: OS bulk属性取得prototypeのscreen再測定

2026-11-15 JST。`getattrlistbulk(2)`をctypes経由でin-process実行するGit外prototypeを、直前31 rootのbaseline値をcarry forwardした33 root campaign（Python 3.12.12、manifest `223b0b57…`）で同じfrozen screenにかけた。詳細は[bulk取得prototype](performance-capacity-bulk-2026-11-15.md)。

| 指標 | 現行scandir | bulk 8-thread |
|---|---:|---:|
| 10-sample scan median / max | 147.7ms / 174.3ms | 36.8ms / **42.7ms** |
| in-situ per-scan | 153.0ms | **39.3ms** |
| 1M checked / no-op median | 137.14s / 75.37s | **90.33s** / 75.28s |
| per-pair overhead median | 81.96% | **19.99%** |

max scan <200msはPASSしたがoffline ≤10%はFAIL。10%に必要な差は約7.5秒で、candidateの実測差は約15.0秒だった。したがってnative helper・build dependencyはproductionへ入れず、`allocated`は`os.scandir`のまま据え置く。Python 3.9.6で先行実施した同scopeのrunはcharacterizationとして保持し、frozen比較には使わない。
