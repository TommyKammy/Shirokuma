# 性能改善: 公開APIページ返却copyのJSON経路化（2026-11-16）

方針は前passと同じ: 計測器の追加チューニングは打ち切り、10%計測器負荷目標は**未達のまま保持**しつつ探索の開始gateから外す。安全保証（deadline・容量上限・stop/cancel）は維持する。**SLO・大規模性能・他製品比較は未受入**。過去の計測値は変更しない。

## 変更

`tools/local_experiment/query_service.py` の2箇所のみ:

- `_page_copy(value)` を追加。`json.loads(json.dumps(value))` でコピーし、JSON serialization が `TypeError`/`ValueError` を raise したときだけ `deepcopy` にフォールバック。
- `_remember_full` は c16ce0c0 の ownership 移譲（cache が fresh response を保持）を維持し、**呼出元へ返す copy だけ**を `_page_copy` に置換。

**値域契約（ソースで確認）**: ページ応答は `_transport` の `json.loads`（backend body）と `_page`/`_pending_page` の組立て、`_metadata(row)`（str と int/None）から成り、`stats` は `type(value) in (str, bool, int, float)` に限定される。したがって値域は str/int/float/bool/None/list/str-key dict のみで、JSON round-trip はこの値域で値を保存する。**generic deepcopy の代替ではない**（tuple は list 化、非strkey は string 化、shared alias/cycle/subclass は保存されない）。そのため fast path はページ返却に限定し、汎用コピーへ拡張しない。fallback は JSON serialization が `TypeError`/`ValueError` を raise した場合に限られ、tuple や非 str key など他の非 JSON 値は coerce され保護されない。generic 等価性や「非 JSON 安全網」の主張ではない。

メモリ: encode は page size（実測で約1MB/page）に比例する一時文字列のみで、無制限の結果bufferは追加していない。新依存なし。

## テスト

`tests/test_query_full_results.py` に1件追加:

- `test_json_domain_page_values_survive_the_return_copy` — bigint（2^63+7）、decimal文字列、UTF-8「雪」、空文字、bool、None、nested list/map、`-0.0` を fast path 経由で往復し、値と型（int/float、`str(-0.0)=='-0.0'`）を検証。cache 側にも元値が残ることを確認。
- 既存の copy/replay mutation・認可/revocation・terminal/cancel・cold pending・2-page bound テストは保持。

focused: `test_query_full_results` 20、`test_query_cold_resume`、`test_query_service`、`test_query_corrections`、`test_local_perf_corrections`、`test_request_auth` すべて PASS。

## 独立受入

reviewer は raw 80 run（before 40 + after 40）を再検証し、両 phase とも `FINISHED`/`SUCCESS`/`full comparison`、pending/notrun 0、engine ID 80 unique、SQL/reference/context 一致（direct の tunnel port 差を除く）、600秒/4GiB/0.2s sampler/scan 失敗0を確認した。focused tests は独立に 20+20+21+20 = 81 件が PASS。45 旧 root と host baseline を引き継ぐ 47 root、既存 GitOps 2 diff の一致、`capacity.py` 不変も確認済み。実装時点の `make verify-local-experiment`（正規 venv）と `make verify-design-context` は exit 0、doc checks（newline/whitespace/secret/diff）も exit 0。

## 測定の provenance（before 40 の成立経緯）

before 40 は単一の連続した試行ではない。最初の試行では small 32 run が manifest path の typo（先行 campaign 配下を指す誤り）で全て preflight failed となり、C6 `b-` 8 run だけが成功した。その後、small 32 を正しい path で再実行して `runs.json` を32件で上書きし、C6 を `b2-` として8件再測定して、比較用の before 40（small 32 + `b2-` C6 8）を構成した。初回の preflight 32 失敗と `b-` C6 成功8件は既存 log に残し（上書きしていない）、raw `b-` C6 8件も保存されている。

比較に `b2-` を使うのは、初回の small 32 がデータを生成しておらず、同じ corrected path・同じ phase で揃った full set が `b2-` + small 32 だけだからであり、結果を見て良い run を選んだものではない。先行の public 試行（`b-`）の warm hdr は [3.488926, 3.477549, 3.363415]（median 3.477549）、比較採用の `b2-` は [3.3442, 3.3211, 3.3496]（median 3.3442）、after は 2.8141 で、先行値と比べても改善方向は一致する。

## 前後比較（同一 frozen 47 root・同一SQL/reference、各40 run）

warm median [min–max] 秒。`header_wait` は各 run の followup 全 call 累計の warm 3 中央値。

| case | route | before wall | after wall | before hdr | after hdr | before cpu | after cpu |
|---|---|---|---|---:|---:|---:|---:|
| 27a | direct | 1.69 [1.65–1.72] | 2.36 [2.34–2.46] | 0.252 | 0.245 | 100 | 103 |
| 27a | public | 1.88 [1.81–1.88] | 2.50 [2.48–2.53] | 0.350 | 0.363 | 110 | 94 |
| 18a | direct | 2.74 [2.65–2.80] | 3.35 [3.33–3.44] | 0.984 | 0.959 | 545 | 553 |
| 18a | public | 2.91 [2.82–2.98] | 3.48 [3.35–3.52] | 1.042 | 1.062 | 581 | 547 |
| 22a | direct | 1.95 [1.92–2.16] | 2.64 [2.57–2.67] | 0.256 | 0.245 | 119 | 108 |
| 22a | public | 2.16 [2.02–2.29] | 2.74 [2.68–2.76] | 0.365 | 0.328 | 113 | 111 |
| 5a | direct | 2.30 [2.22–2.31] | 2.84 [2.82–2.95] | 0.360 | 0.324 | 205 | 216 |
| 5a | public | 2.43 [2.34–2.51] | 3.07 [3.00–3.19] | 0.423 | 0.482 | 214 | 208 |
| C6 | direct | 79.84 [79.61–80.28] | 80.58 [80.08–80.65] | 0.205 | 0.164 | 46 | 48 |
| C6 | public | 84.16 [83.34–84.45] | 83.45 [82.73–84.12] | **3.344** | **2.814** | 45 | 45 |

- **C6 public header_wait 3.344 → 2.814s（−0.530s、−15.85%）**。C6 public の CLI wall は 84.163 → 83.454s。Trino engine CPU は 45ms で不変（server 側 copy の CPU 改善とは別）。followup calls は before public [40,40,38]、after [39,39,39]。
- 小caseの wall は今回も +0.5–0.7s だが header_wait は横ばいで、page 数も数call。原因は未特定で、変更の効果・退行として帰属しない。
- C6 direct の CLI wall は 79.843 → 80.585s（+0.742s）、hdr は 0.205 → 0.164s。before/after は別 phase・server restart・cache 状態未知であり、この差の因果や一般 wall 改善を証明するものではない。

## 診断（改修後、guarded C6 public 1回・通常40runとは別）

| component | calls | wall s | cpu s | ms/call | CPU |
|---|---:|---:|---:|---:|---:|
| `fetch`（inclusive） | 41 | 2.660 | 1.480 | 64.9 | 56% |
| `_receive`（inclusive） | 42 | 1.892 | 0.671 | 45.0 | 36% |
| `_transport`（inclusive） | 42 | 1.642 | 0.421 | 39.1 | 26% |
| `_remember_full` | 41 | 0.484 | 0.484 | **11.8** | 100% |
| `_owned` | 87 | 0.356 | 0.356 | 8.7/page | 100% |
| `respond` | 43 | 0.237 | 0.223 | 5.5 | 95% |
| `_page` | 42 | 0.194 | 0.194 | 4.6 | 100% |

`_remember_full` は 27.1 → 11.8ms/page に低下（実shapeの deepcopy 25.69ms 対 JSON round-trip 11.13ms と整合）。これは改修後1回の診断で、profile overhead は未較正。`_transport` は inclusive で 39.1ms/page（CPU 26%）まで測れたが、実 connect / TLS / response 待ち / body read / JSON decode の内訳は未分離であり、「大半が backend 応答待ち」とは断定しない。process CPU は他 thread を含むため、CPU 差分を待ち時間へ帰属できない。`respond` 5.5ms/page も JSON encode と write を分離しておらず、`json.dumps` 全178 call平均 2.43ms は respond 内 encode の実測ではない。`_receive`/`_transport` は inclusive のため子と二重計上になる。次の優先課題は transport 内部の実 connect / TLS / response / read / decode を実処理境界で限定診断し、改善候補の有無を判断することであり、計測器の追加チューニングは再開しない。

## 保証・制約

- 認可/context/pre-post `_owned`/admission/stop-cancel/UNKNOWN/token順序/replay/2-page bound/列schemaは不変。
- 同じ 47 root frozen scope、600s deadline、4GiB local declared peak、2TB ceiling、512MiB drain、0.2s sampler、callback 毎 deadline/localpeak、forced boundary を維持。
- 比較 40 run は before/after とも `FINISHED`/`SUCCESS`/`full comparison`、pending/notrun 0、`global_failed_scans=0`、engine ID unique。**正常完走は停止障害注入の再実証ではない**（保証コード不変と回帰 tests が根拠）。
- 単一host・warm 3・before/after 別 phase（server restart、cache 状態未知）。統計検定なし、SLO ではない。
- live table UUID/head 照合は引き続き未取得、doctor は既存 degraded。

## 採否と残課題

**採用**: JSON 値域のページ返却 copy 改善。C6 public の warm hdr は 3.344 → 2.814s（−0.530s / −15.85%）、CLI wall は 84.163 → 83.454s。ただし before/after は別 phase・server restart・cache 状態未知で、この差の因果や一般的な wall 改善を証明したものではない。direct は 79.843 → 80.585s、小 case の +0.5–0.7s は原因未特定。

次の優先課題は `_transport` 内部の実 connect / TLS / response / read / decode を実処理境界で限定診断し、改善候補の有無を判断すること。今回の inclusive 39.1ms は内訳未分離で、サーバ側 copy 以外と断定しない。計測器の追加チューニングは再開しない。SLO・一般性能・他製品比較は引き続き未受入。
