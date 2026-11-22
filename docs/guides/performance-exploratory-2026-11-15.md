# 性能探索: 既存TPC-DSデータでの代表ケース実測（2026-11-15, 最終整理）

方針: 計測器の追加チューニングは打ち切り、10%計測器負荷目標は**未達のまま保持**しつつ探索の開始gateから外す。安全保証（deadline・容量上限・stop/cancel）は維持する。**SLO・大規模性能・他製品比較は未受入**であり、今回本体への追加修正は行わない。過去の計測器結果は変更しない。

本版は独立再計算後の最終整理である。追加負荷は行っていない。

## 実行と集計

- 成功run **40** = initial 10（rep0）+ warm 30（rep1–3）、5 case × 2 route。
- 40件すべて `page_state=FINISHED`、`validation="full comparison"`、`capacity_guard.global_failed_scans=0`、`interval_s=0.2`、`timeout_s=600`、peak 4GiB。engine IDは40件すべて相異なる（direct `attempt.handle.id`、public `handle.backend_query_id`）。
- 起動前に落ちた48件は**入力manifestの不備**で、query engineの失敗ではない。
- **C1 countは未測定**（reference接続が未整備のためこの実行では省略）。独立oracleが原理的に不可能という意味ではない。5代表ケースの探索としては十分であり、C1の追加測定は不要。`count_case.py` のguard外迂回は**実行していない**。

## warm実測（median [min–max]）

`cli_wall_s` はCLI全体の壁時計で、**guard・結果照合・artifact処理を含む**ため「Shirokuma製品だけの所要時間」ではない。`backend_*` は `attempt.timings.backend_report` の値（`source=response.stats`, `client_observed=true`, latest client-observed, 単位ms）。

| case | route | cli_wall_s | backend_wall_ms | backend_elapsed_ms | header_wait_s |
|---|---|---|---:|---:|---:|
| C2 27a | direct | 2.04 [1.76–2.07] | 1030 | 754 [522–816] | 0.507 |
| C2 27a | public | 2.21 [1.99–2.36] | 1374 | 683 [624–893] | 0.533 |
| C3 18a | direct | 2.76 [2.73–2.82] | 1504 | **1358** [1286–1424] | 1.075 |
| C3 18a | public | 2.96 [2.96–3.01] | 1865 | **1477** [1438–1532] | 1.241 |
| C4 22a | direct | 1.94 [1.89–2.00] | 456 | 486 [466–529] | 0.274 |
| C4 22a | public | 2.10 [2.10–2.28] | 214 | 532 [508–639] | 0.385 |
| C5 5a | direct | 2.38 [2.25–2.41] | 786 | 819 [633–828] | 0.443 |
| C5 5a | public | 2.61 [2.58–2.87] | 841 | 690 [653–1150] | 0.544 |
| C6 projection | direct | 75.75 [75.59–76.63] | 92 | 15499 [15359–15504] | 0.192 |
| C6 projection | public | 79.85 [79.68–80.11] | 107 | 19727 [19097–19805] | **4.507** |

engine CPU（ms, median [min–max], `queuedTimeMillis=0` は全run）: C2 508 [152–563] / 685 [344–734]、C3 631 [623–638] / 620 [609–633]、C4 251 [150–258] / 133 [127–175]、C5 237 [224–410] / 320 [238–1269]、C6 63 [61–70] / 65 [61–82]。

initial（rep0）のcli_wall_s: C2 3.08 / 2.45、C3 3.10 / 2.98、C4 2.37 / 2.45、C5 4.09 / 3.36、C6 76.04 / 79.71（direct / public）。

C6 warm direct で **`dispatch_started` からの差分**（秒）なのは、初回応答 0.036、初行 0.111、全行 15.552、result complete 15.564 の4つだけである。これとは起点が異なる値として、validation window 59.413 は validation 開始→終了の区間、full compare `validation_s` 47.401 と artifact processing 13.578 はそれぞれの duration、`fetch_s` 28.292 は attempt 開始→順序検査終了までを含む既存fieldであり、互いに重複加算しない。

## 境界の扱い

`events` は attempt 開始からの秒であるため、上記は dispatch 起点の差分に直した値。`fetch_s` は `validate_order` 終了後まで含み純粋転送ではない（C6 direct: 28.29s vs result complete 15.56s）。`validation_s` は full comparison に近く、window は schema/order/compare 全体。`artifact_processing_s` は guard・schema・typed serialization・fsync の複合。HTTPは `connect/send/header_wait/body_read/decode/total` に分かれ、重複区間を加算・控除しない。

## 結果の正しさ

SQLは明示的な `FOR VERSION AS OF` でpinned snapshotを参照し、`validation="full comparison"` かつ `reference_evidence` のengine familyはDuckDB。C2–C5は既存qualified referenceと全件・型・順序で一致（100行）、C6は288,464行がpin行数と一致。ただしこれは**run時に指定したsnapshotに対する完全比較**の実証であり、liveなtable head不変や全metadata保持を確認したものではない。

## 本体側の改善優先順位

1. **public経路のpage受け渡し待ち**（確度: 中）— follow_up `header_wait` が C6 で **4.507s**（direct 0.192s）、小caseでも0.27–1.24s。`connect`/`decode` は小さい。ただし削減可能量を4.3sと**確約しない**（HTTP待ちにbackend待ちが含まれ得る、独立run）。原因の切り分けが先。
2. 次に本体側で見るなら **C3 18a のjoin**（backend elapsed 1.358 / 1.477s と小case最長、CPU 631 / 620ms）。必要なら `EXPLAIN` でプラン確認する候補。ただし本データ（SF0.1）で緊急のengine最適化や資源増強を正当化するものではない。
3. 小caseのAPI差は **warm CLI差 約0.16–0.23s**（以前の0.2–0.4sはinitial混在）。低優先で、固定オーバーヘッドとは断定しない。

**本体の改善対象から除外**（計測器側であり、打ち切り済みの追加チューニングに属する）: validation window 59.4s / full compare 47.4s、artifact processing 13.6–15.0s（guard・serialization・fsync混在）、CLI wallに含まれるguard・照合時間。原因未確定の列指向化・キャッシュ導入などの実装推奨は行わない。

## 後続（公開APIページ受渡の改善）

C6 public の followup `header_wait` 4.449s の原因を server 内で計測し、`_remember_full` の二重 `deepcopy`（53ms/page、100% CPU）を1回に削減した。同一 frozen 45 root・同一条件の再測定で **header_wait 4.449 → 3.297s**、C6 public wall 84.37 → 83.89s。小caseはsession driftで比較不能。詳細は[公開APIページ受渡の改善](performance-public-pages-2026-11-15.md)。

## 制約・未確認（隠さない）

- **live table UUID/head/snapshotの照合は before/post とも未取得**。`verify_snapshots` はcampaign所有pinsを要求して拒否、`observe` はpublic APIが`PUBLIC_QUERY_UNMET`で拒否した。run時のSQLは明示`FOR VERSION AS OF`で、完全比較は成功しているが、それは上記の範囲の実証にとどまる。
- engineのplanning/spill/input-outputは未取得。n=3 warmのためp95/SLOは主張しない。
- doctorは実測前後とも**degraded**（Flux Kustomization suspended、policy exit 2）。Pod構成・UID・restart countは前後で同一（trino 0、他は3d22h前のまま）。これは観測範囲の記録であり、OOM完全未発生の証明ではない。既存のUNKNOWN/fenceは未変更（追加の検証はしていない）。
- 現データはSF0.1（fact 288k行）で小さく、大規模時のengine bottleneckは確定できない。

## 復元・安全

Pod構成/UID/restart count/doctorは測定前後で同一。容量は43 root campaign（41 baseline継承 + 本task tree + campaign = 0 baseline）、2TB ceiling、512MiB drain、host reserveを維持し、run毎のguardが有効。データ生成・削除・heap/stage/worker設定変更は行っていない。helperのauth fileはcontext終了時に削除され、tunnelは停止済み。
