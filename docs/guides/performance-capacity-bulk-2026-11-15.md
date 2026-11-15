# 性能試験: OS bulk属性取得によるfull-global capacity scanのscreen再測定

2026-11-15 JST。`CapacitySampler(0.2)`、毎callbackのdeadline/local peak、scan後deadline再確認、remaining peak、forced boundary、fail-closedなstop/cancel、2TB cap、512MiB drainを変更せず、full-global scanのper-scan costだけをmacOSの`getattrlistbulk(2)`で下げられるかをGit外prototypeで実測した。production code、test、runtime、benchmark contractは変更していない。

## 測定環境と先行runとの差異

- 採用比較はPython **3.12.12**（`benchmarks/local_perf/.venv/bin/python`）。先行protocolの`scan-after.json`と同じinterpreterで、全runにexecutableとversionを記録した。
- 先行のPython 3.9.6 runは**characterizationであり受入ではない**。同一条件ではない: 先行は34 root（task artifactの`v2`/`evidence`を個別rootとしてcreate時baselineで計上）、prototypeは`v2`（multi-root errorで`errno=-1`を返す欠陥あり）、本比較は33 root（task tree全体をbaseline 0）、prototypeは`v3`（同欠陥を修正）。scope・binary・interpreterのいずれも異なるため、両者の数値を直接比較しない。

## Prototype

- `getattrlistbulk(2)`（`sys/attr.h`、`man 2 getattrlistbulk`、`man 2 getattrlist`）で`ATTR_CMN_NAME|DEVID|OBJTYPE|FILEID|ERROR|RETURNED_ATTRS`と`ATTR_FILE_ALLOCSIZE`を一括取得し、record境界・attrreference・NUL終端・per-record error・負のallocated bytesをすべて検証してfail closedするparserをCで実装。
- `-dynamiclib`を`ctypes`でin-process呼び出し（spawnなし）。root横断は**8 thread上限**のbounded batchingのみ。
- 各rootで独立した`(st_dev, st_ino)` dedupe、regular fileの`st_blocks*512`相当、symlink fileはtarget regular fileとして計上、child-directory symlinkは再帰しない、traversal/stat errorは伝播。

## 安全性の実測範囲

- 合成recordのinjected test **22件**: required attribute全bit必須（`(common & mask) != mask`）、record長<header、buffer超過、count超過、name offset/lengthのrecord外参照、NUL欠落・長さ不一致、per-record error伝播、FILEID欠落、負のallocated、allocsize無しのregular file、directoryのallocsize省略。
- live filesystem parity **8件**: grow/shrink/add/delete、sparse、hardlink（root内1回・cross-root独立）、symlink file/dir、broken symlink、unreadable directory、missing root、symlink root、bounded multi-root error伝播。
- 多root error伝播に実装欠陥があった。workerが`ca_allocated`の戻り値-1をerror値として保存し、`ca_allocated_many`が`errno=-1`を返していた（ENOENT/EACCESが失われる）。workerで失敗直後に`errno`を捕捉し成功時0を保存する形へ修正し、ENOENT/EACCESをbounded multi-root経路で検証するtestを追加した。修正前`errno=-1`、修正後`errno=2`/`13`を実測。parser/parityは修正後に再実行して全pass。
- offline prototypeはproductionの`CapacitySampler(0.2)`、毎callbackのdeadline/local peak guard、scan後deadline再確認、remaining peak、forced boundaryを維持した。productionのstop/cancel pathは変更していない。
- これらはprototypeの限界であり、production pathの安全性主張ではない。runtimeのfailure mode qualification、native workload、full regressionは本taskで実施していない。

## Frozen measurement

- scope: **33 root** = 直前**31 root（baseline値は不変でcarry forward）** + 今回のtask tree全体 + campaign。task treeとcampaignは**baseline 0**（今回追加したartifactを除外せず計上）。campaignはwhole task tree rootとのoverlapを避けるためtask tree外に置いた。
- `baseline_host_free`は先行manifestの**3089681522688**をそのままcarry forward。ceilingは2TB cap、drainは512MiB。
- manifest `223b0b57…`、baseline snapshot `8af3ec3b…`。各runにmanifest hash、`capacity.py`、prototype source、dylib、interpreter executable+versionを記録。arm間はpair単位でinterleaveし、各armのchecked warm-upは中央値から除外。
- 10 whole `Campaign.check` samples、3 alternating checked/noop 1M fresh-process pairs/arm、同一left/right・同一order・同一compare、offline deadline 1800s、4GiB local peak、writerは先行のseparate evidenceを再利用（未変更・再測定せず）。

| 指標 | 現行scandir | bulk 8-thread |
|---|---:|---:|
| 10-sample scan median / max | 147.7ms / 174.3ms | 36.8ms / **42.7ms** |
| in-situ per-scan | 153.0ms | **39.3ms** |
| 1M checked median | 137.14s | **90.33s** |
| 1M no-op median | 75.37s | 75.28s |
| per-pair overhead median | 81.96% | **19.99%** |

raw pairs（checked / no-op / ratio / scans / per-scan）:

- baseline: 134.91 / 74.98 / 79.93% / 377 / 152.39ms、139.57 / 75.84 / 84.03% / 389 / 152.99ms、137.14 / 75.37 / 81.96% / 382 / 154.44ms
- candidate: 90.33 / 75.28 / 19.99% / 371 / 39.23ms、90.06 / 75.28 / 19.64% / 369 / 39.36ms、90.72 / 75.22 / 20.60% / 371 / 39.34ms

## 独立review

- prototype parser 22件とlive parity 8件をreviewerが再実行してpass。33 rootでnative 8 threadとscandirのbyte一致、prior 31 baselineとoriginal host freeの保持、新規rootのbaseline 0・disjointを確認した。
- 12 run record（checked/noop 6 pair、各arm 3 pair）すべてでPython 3.12.12・source/binary/manifest fingerprintの一致を確認し、median 81.959728%（baseline）と19.991639%（candidate）、max scan 42.719417msを再計算した。sessionのmodelも実測で確認している。

**判定: max scan <200ms はPASS、offline paired overhead ≤10% はFAIL（19.99%）。** 10%に必要なのは約7.5秒で、candidateの実測差は約15.0秒。scan costはscandir比で約3.9倍改善したがscreenは未達のため、native helper・build dependency・Makefile変更はproductionへ入れず、`allocated`は`os.scandir`のまま据え置く。

## 後続passとの関係

2026-11-15 JST追記。本記録のv3 prototypeは`ca_allocated_many`でroot単位にworkerを割り当てる実装で、混合rootの会計欠陥を持たない。したがって本記録のparity、19.99%を含む計測値、結論は**そのまま有効**であり、変更しない。

後続の[subtree work queue試験](performance-capacity-subtree-2026-11-15.md)は別実装の`ca_scan`を新規に書き、その初期版に「directory root用のmerge loopを全rootへ適用し、混合batchでfile rootの直接測定値を0で上書きする」会計bugと、失敗経路でdescriptorを閉じないcleanup欠陥があった。いずれも後続passのprototype固有の欠陥で、本記録の測定・parity・結論には影響しない。経緯と修正は後続記録に記す。

## 計測上の限界

- 最遅単一root（TPC-DS 8GBのdataset tree、2,908 files）は単独で18.84ms（in-process、spawnなし）を要した。これは観測値であり、root並列の理論的下限ではない。
- 8 threadは今回のbounded上限。4 threadでも実測差は数msで、thread数を増やす余地はbounded方針の範囲では小さい。
- 対象root数は33で、先行31 rootから2 root増えた。単発screenはhost負荷とinterpreterに敏感なため、比較は同一interpreter・同一manifest・同一sessionで行う必要がある。
- 次に試す価値があるのは、同じcoverage・同じ保証・同じ8 thread上限のまま、要求attribute setの縮小（`ATTR_CMN_DEVID`をdirectory単位の`fstat`で代替する場合もdevice identityの意味を保つこと）とroot内subtree単位のschedulingでper-scan costを下げられるかである。未検証であり、達成を主張しない。criteria・coverage・sampling intervalの緩和は提案しない。本結果はG1/性能/SLOの受入ではない。

## 証拠

- 修正後prototype（`v3`）: `bulk_core.c`、`bulk_parse_test.c`、`parity_test.py`、build/parse/parity log。
- corrected frozen run（`correction-01`）: `manifest-frozen.json`、`screen-*.json`と`.log`、`offline-runs.json`、`screen-summary.json`、`final-summary.json`、`doc-checks.log`。campaignはこのmanifestに記録したhashで識別する。
- 先行3.9.6 runのscan生配列（`evidence/screen-*.json`）は、corrected harnessの出力先指定ミスで上書きされ失われた。3.9.6のmedian/p95/maxは`evidence/final-summary.json`と`evidence/screen-*.log`に、offline raw pairは`evidence/screen-summary.json`と`evidence/offline-runs.json`に残る。corrected runの生配列は`correction-01/screen-*.json`に保持している。経緯は`evidence/SUPERSEDED-correction-01.md`に記録した。
- 先行prototype（`v2`）のsourceとdylibは未変更で保持し、修正版は`v3`へ別途buildした。
