# 性能改善: 公開APIページ受渡の応答コピー削減（2026-11-15）

方針は前passと同じ: 計測器の追加チューニングは打ち切り、10%計測器負荷目標は**未達のまま保持**しつつ探索の開始gateから外す。安全保証（deadline・容量上限・stop/cancel）は維持する。**SLO・大規模性能・他製品比較は未受入**。過去の計測値は変更しない。

## 原因（実測）

C6 public（288,464行・23列）1回の guarded 実行で、server 内の呼出を限定 wrapper で計測した（診断は baseline の40 runとは別実行）。

| component | calls | wall s | cpu s | ms/call |
|---|---:|---:|---:|---:|
| `_fetch_full`（inclusive） | 40 | 4.261 | 3.127 | 106.5 |
| `_remember_full` | 40 | **2.123** | **2.123** | **53.1** |
| `_receive`（inclusive） | 41 | 1.860 | 0.669 | 45.4 |
| `_owned` | 85 | 0.355 | 0.355 | 4.2 |
| `_page` | 41 | 0.186 | 0.186 | 4.5 |

`_remember_full` は `deepcopy` を2回（cache格納用と返却用）行っており、100% CPU。実shape（7,200行×23列、JSON約1.07MB/page）で `deepcopy` は 23.7ms/回、2回で約47ms + `_prune_full` 等で53msと整合。`_receive` は45ms/pageだがCPUは36%で、残りはbackend待ちを含む（backend待ちの全てと断定はしない）。

## 変更

`tools/local_experiment/query_service.py` の `_remember_full` のみ:

- 呼出元が渡す fresh/owned な `response` の所有権を `pin.responses[token]` へ移譲。
- 呼出元へ返す `deepcopy` は1回だけ。
- payloadはnested list/dict/array/map/NULLを含み可変なので、**浅copyにはしない**。

意味論: cache immutability（返却値はcopy）、replay（cacheは元payloadを保持）、2-page bound（`_prune_full`不変）、previous columns、terminal/cancel、取得後のrevocation再検証、token順序、idempotencyはいずれも変更なし。`_fetch_full` は `_remember_full` を2箇所で呼ぶが、いずれも直前に `pin.initial_response=None` 済みか `_receive` の新規戻り値で、他からの別名は無い。

テスト: `tests/test_query_full_results.py` に、配信ページの深いnested mutation（dict/list/None）がcache/replayを汚さないこと、cacheに元payloadが残ること、再fetchがbackendを再送しないことを検証するtestを追加。既存の `test_query_full_results`・`test_query_cold_resume`・`test_query_service`・`test_query_corrections`・`test_local_perf_corrections`・`test_request_auth`・`test_local_auth_refresh` は全てPASS。

## 前後比較（同一 frozen 45 root・同一SQL/reference・40 run 各）

warm median [min–max] 秒。header_wait は各 run の followup 全 call 累計を warm 3 run で中央値にしたもの。

| case | route | before wall | after wall | before hdr | after hdr |
|---|---|---:|---:|---:|---:|
| 27a | direct | 1.52 [1.49–1.57] | 2.18 [2.16–2.23] | 0.254 | 0.264 |
| 27a | public | 1.73 [1.70–1.78] | 2.36 [2.31–2.45] | 0.369 | 0.380 |
| 18a | direct | 2.67 [2.59–2.85] | 3.25 [3.17–3.26] | 1.124 | 0.935 |
| 18a | public | 2.76 [2.65–2.77] | 3.42 [3.32–3.48] | 1.057 | 1.045 |
| 22a | direct | 1.87 [1.77–1.98] | 2.48 [2.40–2.50] | 0.261 | 0.256 |
| 22a | public | 1.99 [1.98–2.07] | 2.61 [2.60–2.64] | 0.374 | 0.356 |
| 5a | direct | 2.06 [2.06–2.12] | 2.70 [2.69–2.91] | 0.397 | 0.360 |
| 5a | public | 2.42 [2.18–2.46] | 2.84 [2.79–2.87] | 0.500 | 0.449 |
| C6 | direct | 79.12 [78.05–79.13] | 80.16 [79.68–80.22] | 0.180 | 0.179 |
| C6 | public | 84.37 [84.02–87.04] | 83.89 [82.68–84.43] | **4.449** | **3.297** |

- **C6 public の header_wait は 4.449 → 3.297s（−1.15s、−26%）**。followup calls は before 38–40 / after 39–40 で、39 は中央値であり固定page数ではない。rows 288,464、両phase `FINISHED` / `full comparison`。engine CPU の warm 中央値は public 46→44ms、direct 45→45ms。
- 一方で **wall の改善は確認できない**（C6 public 84.37→83.89、C6 direct 79.12→80.16）。参考として dispatch→result complete は public 21.560→19.853s、direct 16.270→16.421s だが、これはbenchmark側のartifact処理を含むCLI区間であり純粋なAPI応答時間ではない。
- 小caseは全体的に0.6–0.7s遅く、followup call数も数call変動した。**原因は未特定**で、host driftは仮説にすぎず、変更の効果・退行として帰属しない。
- 40 run は before/after とも `FINISHED` / `full comparison` / `global_failed_scans=0` / `timeout=600` / local peak 4GiB。engine ID は全80件で unique。

## 独立評価

reviewer は raw 80件（before 40 + after 40）の run/attempt を確認し、`SUCCESS`/`FINISHED`/`full comparison`、pending・not-run 0、engine ID 80件 unique、600秒・4GiB・0.2s sampler・failed scans 0、同一SQL/reference/context（direct tunnel portのみ変化）を確認した。43既存rootのbaselineとhost基準を保持した45 root、既存GitOps差分の一致、`capacity.py` のSHA不変も確認している。focused は FullResult 19 + ColdResume 20 + Corrections 21 + RequestAuth 20 = **80 tests 全pass**。`make verify-local-experiment` は正規venv指定で成功（初回の system python は PyYAML 欠落で失敗、環境要因）。

## 保証・制約

- 認可・context・pre/post `_owned`・admission・stop/cancel・UNKNOWN・token順序・replay・2-page bound・列schemaは不変（コード差分は `_remember_full` の格納1行と所有権コメントのみ）。
- `_receive` は inclusive な計測で、process CPUは他thread分も含む。差分 wall−CPUをTrino待ちとして分離することはできず、after側のサーバ内訳も測定していない。したがって残るコストの内訳は**未分離**であり、公開APIに残る転送・ページ処理の本体側切り分けが次の課題である。
- 単一host、warm 3回。before/after は別phaseで、間に server restart と未知のcache状態を挟む。統計検定はしておらず、SLOや一般性能の受入ではない。
- live table UUID/head照合は引き続き未取得（既存3経路すべて不可）。doctorはdegraded（Flux suspended / policy exit 2）で不変。

## 採否

コードは**採用**（挙動不変・テスト追加）。C6 public の header_wait はこの同一hostの warm 3回で 4.449→3.297s と観測したが、before/after は別phase・server restart・cache状態未知の条件下で、**「4秒削減」ではなく約1.1秒**、体感wallの改善は未確認。次の本体課題は公開APIに残る転送・ページ処理の切り分けであり、計測器の追加チューニングは再開しない。
