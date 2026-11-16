# 公開APIページ転送の接続再利用（2026-11-16）

方針は前passと同じ: 計測器の追加チューニングは再開せず、10%計測器負荷目標は未達のまま保持する。安全保証（deadline・容量上限・stop/cancel）は維持し、SLO・一般wall・他製品比較は未受入とする。過去の数値は変更しない。

## Scopeとprovenance

最初のfresh-before campaignは38/40でWorkbenchがlistenを停止した（37 SUCCESS、1 UNKNOWN、残り2未実行）。failed attemptは上書きせず保持し、UNKNOWNのreconcileはUNKNOWNのままで、同campaignのbenchmark dispatch guardにより継続できなかった。停止原因はlog/終了証拠から特定できず、不明として扱う。failed request_idはcontrol storeの実行rowを持たず、live Trinoのrunning/queued queryにも一致しなかった（元attempt/fenceは変更していない）。このphaseは比較baselineに使わない。

採用したbefore 40は、最初のcampaignをzero baselineとして保持した49-root scopeで取得した。47 inherited roots、task tree全体、failed campaignの49 rootで、`campaign2`はtask tree内に置いてroot数を増やさない。600秒/4GiB/2TB/512MiB drain/reserves/0.2秒sampler、callbackごとのdeadline/local peak/stopを維持した。before 40は40/40 SUCCESS、engine ID unique、full comparison、pending/not-run 0、guard failure 0、timeout 600秒。

同じfrozen 49-root/campaign2、同じ5 case・SQL・reference・context・route順・initial 1 + warm 3でafter 40を取得した。afterも40/40 SUCCESS、full comparison、engine ID unique、pending/not-run 0、guard failure 0。before/after合計80 raw attemptsはすべてFINISHED、engine ID 80 unique、manifest hash 1/case、exit 0。

## 変更

`tools/local_experiment/query_service.py` の `_Pin` に、既存位置引数を変えない末尾field `transport` を追加した。

- `full_results` のfollow-up GETだけ、同じpinのHTTPS connectionを再利用する。最初のGETは新規、2本目以降が同じsocketを使う。
- initial POST、DELETE、previewは新規connectionのままで、保持しない。
- 再利用前に、新しい `FULL_REQUEST_TIMEOUT` の絶対deadlineから残り時間を計算し、socket timeoutを更新する。前pageの短いbody-read timeoutを持ち越さない。既存のper-request timer、16MiB body bound、read長/EOF検査、status検査は変えない。
- `will_close`、peer切断、socket消失、invalid/truncated/oversized body、JSON error、request error、deadline、cancel、terminal、revocation、retirementではcloseして保持をclearする。途中requestの自動再送はなく、同じcursorのGET/POSTを失敗後に出さない。
- terminal/cancel/cleanupで `_close_query_owner` がsocketを閉じる。新たなlockは取らないため、既存pin.lock/DB transaction/global lockの順序に新しいdeadlock edgeを追加しない。atexitでも保持socketを閉じる。connection pool/global cache/replay変更はない。
- 1 active full-result queryにつき1 socket。admissionで制限されるactive query数を超えない。放置queryは既存のowner/terminal/cancel寿命に従い、追加supervisorやidle timerは設けない。
- TLSはpin固定のcontextを使い、CA/hostname検証を変更しない。keep-alive中は新規handshakeをせず、新しいqueryは新しいcontextを持つ。証明書hot reloadは追加しない。

`tests/test_query_full_results.py` に、実HTTP/1.1 loopbackでの複数page再利用・最終close、socket timeout再設定、`will_close`/peer close、異なるpin-origin/TLS context分離、再利用中errorのclose/非retry、POST/DELETE非保持、invalid/truncated/oversized/deadlineを追加・強化した。既存の認可/revocation/terminal/cancel/cold pending/cache mutation/2-page bound testsは維持し、弱めていない。focusedはfull-result 32、cold resume 20、query service 47、dataops 44、corrections 21、local-perf corrections 18、request auth 20で全てexit 0。`make verify-local-experiment` はcleanup修正前の実装時点で162 suite・exit 0。今回のcleanup修正後は追加fullmakeを行わず、関連focused testsを再実行し、`make verify-design-context` とdoc checksもexit 0。

独立reviewで、認可成功後にclosed executionを観測した場合などに保持socketが残る欠陥が見つかった。`_owned`のowner/role確認後（別actor/roleの拒否より後）のrevocation/retirement、`_fetch_full`のclosure/cancel/terminal/cached failure/backend error、closed executionのcancelでclose-clearするよう修正した。POST/DELETE/preview、再送、UNKNOWN、closure/fenceの意味は変えていない。実HTTP/1.1のpeer silent closeとcached failureのno-retry、closed execution/cancel/retirementのsocket closeを追加testで確認した。

## before/after

C6 `store_sales` 288,464行/23列、warm 3、CLI wallとfollow-up header wait（1 runのfollow-up累計）:

| route | before wall | after wall | before hdr | after hdr |
|---|---:|---:|---:|---:|
| direct | 81.745 [80.726-82.059] | 82.791 [82.616-82.953] | 0.176 | 0.179 |
| public | 83.863 [82.624-85.559] | 84.643 [84.490-85.819] | **2.655 [2.592-2.729]** | **2.409 [2.360-2.603]** |

- C6 public header wait は **2.655 → 2.409秒（−0.246秒 / −9.3%）**。afterのfollow-up callsは中央値39→41（after個別41,40,41）で、固定page数と誤解せず、call数が増えてもheader waitは低下した。
- C6 public CLI wallは **+0.780秒**、directは **+1.046秒**。directは変更経路を持たず、small caseもwall +0.64–0.83秒ではあるがhdrはほぼ横ばい。host/backend driftは仮説であり、wall増加の原因は特定していない。一般wall改善とは主張しない。C6 publicのvalidationは50.666→50.568秒、artifactは15.774→16.125秒で、wallの差はtransportだけではない。
- 変更の実使用確認としてnormal serverをfinal codeでrestartし、public C6を実行した。after40測定後のcode差分はreviewer指摘の異常時cleanupと追加testsだけで、copy/TLS/request timeout/通常success経路は変えていない。before/afterは別phase・server restart・cache状態未知・単一host・統計検定なし。

small 4 case（同じbefore/after phaseのraw中央値、秒。過去phaseの値は混ぜていない）:

| case | direct wall before→after | direct hdr before→after | public wall before→after | public hdr before→after |
|---|---:|---:|---:|---:|
| 27a | 1.586→2.247 | 0.239→0.242 | 1.703→2.438 | 0.329→0.335 |
| 18a | 2.564→3.269 | 0.929→0.895 | 2.786→3.619 | 1.051→1.154 |
| 22a | 1.854→2.516 | 0.249→0.237 | 1.993→2.733 | 0.342→0.330 |
| 5a | 2.065→2.826 | 0.323→0.326 | 2.329→2.972 | 0.420→0.437 |

## 実処理境界の診断（変更後1回、40 runとは別）

変更後のscoped C6 public 1回（full comparison、guard failure 0、SUCCESS/FINISHED）を、変更前の同種診断2回と比較した。診断は通常serverを停止してscoped serverで実行し、終了後にnormal serverを復元した。

| 項目 | before（2診断の合計/80 pages） | after（1診断/40 pages） |
|---|---:|---:|
| HTTP connection / TLS handshake | 40 / 40 per 40-page run | **2 / 2 per 40-page run** |
| TLS handshake wall | 1,068.4 ms（per-run mean 534.2 ms） | 29.1 ms |
| `_transport` total | 3,034.8 ms（per-run mean 1,517.4 ms） | 1,104.5 ms |
| client follow-up hdr | diag1 2.722 s / diag2 2.862 s | 2.296 s（this diagnostic） |
| `will_close=false` | 40/40（diag2） | 40/40 |

beforeのphase値は、`_transport` total 3,034.8ms、TLS handshake 1,068.4ms（35.2%）、body read1 889.7ms（29.3%）、body全体 917.8ms（30.2%）、decode 459.6ms（15.1%）、header read 385.7ms（12.7%）、pre-connect 106.4ms（3.5%）、timer+close 61.7ms（2.0%）だった。read1とbody全体は別の値で、read1単体29.3%をbody全体30.2%と呼ばない。TCP/DNS/TLS/header/read1はinclusive childで、exclusive waterfallに再加算しない。

after診断ではhandshakeが2回に減り、TLSは29.1ms、`_transport`は1,104.5msだった。通常warmのbefore/afterは2.655→2.409秒で、診断値とは分けて扱う。client follow-up hdrはclient adapterの累計、server `getresponse`/header readはheader parseを含むため、純engine waitとは呼ばない。body/read1は541.9ms、decodeは226.2msで、page内容・backend応答時間・host条件の変動を含む。診断wall 84.560秒はbefore warm range [82.624-85.559] 内だが、before診断2回（84.219/83.342秒）より+0.34/+1.22秒で、profile overheadとbackend変動は分離していない。診断は変更後1回のみで、追加tuningはしない。

## 保証・制約

- 認可/context/pre-post `_owned`/admission/stop-cancel/UNKNOWN/token順序/replay/2-page bound/列schemaは不変。TLS CA/hostname検証、pin origin/principal分離、POST再送不可、中断UNKNOWNも不変。
- 同じ49-root frozen scope、600秒deadline、4GiB declared peak、2TB ceiling、512MiB drain、0.2秒sampler、callbackごとのdeadline/local peak/force/stopを維持。benchmark source・計測器のtuningはしていない。
- before/afterは別phase（server restart、cache状態未知）。単一host、warm 3、統計検定なし。C6 directは変更経路でなく、after wallの差を接続再利用へ帰属しない。
- live table UUID/head照合は未取得、doctorは既存degraded（pre/postとも同じ）。正常完走は停止障害注入の再実証ではない。
- 最初の38/40 phaseは不採用として保持し、成功80だけを試行したようには扱わない。

## 採否

接続再利用は**ページ応答待ち改善として採用**する。handshake 40→2、C6 public follow-up hdr 2.655→2.409秒、変更後診断2.296秒で、機構の実使用を確認した。一方、C6 public CLI wallは+0.780秒、direct/smallは変更経路外も含めて同方向に遅く、**一般wall改善・SLO・cross-engineは未受入**のままとする。残る主要コストはbody read（read1/readinto）とJSON decode、server `getresponse`/header phase（header parseを含み純engine waitではない）で、今回の範囲では追加変更しない。

## 独立評価

reviewerはcleanup修正後に **140 tests**（full-result 32、cold resume 20、corrections 21、request auth 20、query service 47）を再実行して全passを確認し、raw 80 attemptsのFINISHED/full comparison/pending0/guard failure0/engine ID 80 uniqueを再集計した。handshakeはbefore 40/40 pages→after 2/40 pages、TLSは平均534.2ms→29.1ms、`_transport`は1,517.4ms→1,104.5msとして直接確認した。

reviewerは最初の実装に異常時cleanup欠陥（closed execution等で保持socketが残る）を発見し、修正後のclose-clear、正当actor/role確認位置、peer silent closeのno-retry、timeout/TLS/pin隔離、source diffを独立確認して受入した。after40とfullmakeはcleanup修正前の実装時点の測定で、cleanupだけを後から修正してfocused 140で再検証した範囲を保持する。before 2回+after 1回の診断、単一host、warm 3、統計検定なしの限界も維持する。
