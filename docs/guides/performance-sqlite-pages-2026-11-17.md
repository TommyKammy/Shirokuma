# WorkbenchページSQLite処理の内訳と重複認可の削減（2026-11-17）

通常Workbench公開APIのfull-results 1ページごとに発生するSQLite接続・認可・状態書込みを、製品外の診断で分解し、同じ要求内で重複していた認可・closure読取り・未リンクDataOps照会・backend ID再書込みを削減した。1M publicのHTTP header待ちは **6.732→6.117秒（−0.616秒 / −9.1%）**、HTTP totalは **7.628→7.033秒（−0.595秒 / −7.8%）** だった。一方、wallは100k/288kで微増、1Mで微減であり、一般wall改善・SLO・engine性能としては主張しない。benchmark・meter・guard・正式timing経路は変更していない。

## 変更（製品経路のみ）

- `tools/local_experiment/query_service.py`
  - `_owned` は所有者確認・row読取り・現在のcontext/認可・logical identity確認を、logical_v1では1接続・1トランザクションにまとめた。legacyは従来どおり短い接続を分ける。上流I/O後の再認可は維持し、要求をまたぐsnapshotは持たない。
  - 通常の未リンクqueryでは、最初のページでDataOps linkが無いことを確認した後、不変の「無し」をpin内で再利用する。linkがあるqueryは毎ページ再確認する。`_state` のDataOps子照会も同じ既知結果を使う。
  - `_state` は同一transaction内で確認済みのclosureをregistry書込みへ渡し、2ページ目以降の不変backend ID再書込みを省略した。
- `tools/local_experiment/query_context.py`: 既存接続を共有できる内部経路を追加。logical identityのbackend username照合を、現在の認可判断と同じtransaction内で行う。legacyの最終認可は従来どおり接続を閉じた後に行う。
- `tools/local_experiment/data_management.py`: 呼出側が同一transactionでclosureを確認済みの場合だけ、重複closure SELECTを省略できる任意引数を追加。
- 保持したもの: 毎回の現在account/role/grant/platform判断、logical identityのbackend username照合、context/catalog/schema/binding/revision照合、execution closure、cancel/UNKNOWN、再送なし、replayのowner/role/context/closure再確認、上流I/O後確認、16MiB/deadline、接続再利用。
- 新しい依存・計測イベント・guard変更なし。

## 計測方法（診断と公式E2Eの区別）

- 公式E2E: 3 size × 2 route × (initial 1 + warm 3) = 24/24。同じmanifest SQL、DuckDB full reference、23列、600秒、4GiB declared peak、concurrency 1、0.2秒sampler、forced scan。BEFOREは最新の未変更24 runを、コード・manifest・context・dataset/snapshot・reference・serviceを監査したうえで再利用した。
- BEFORE再利用の確認: product差分 `1561927d..a760e2af` は空、manifest SHA一致、public role/context一致、reference SHA一致、BEFORE 24/24 SUCCESS、engine ID 24 unique。現在のdirect 1M controlはbaseline direct中央値より **−1.68%** で、事前に定めた3%以内。directはWorkbenchページ経路を通らないcontrolである。
- SQLite内訳: 同じ1M public all-resultsを、製品外の`sitecustomize`でSQLite `connect`/traceのみ数えた。製品ファイル・benchmark・guardは変更せず、SQL本文はliteralを伏せたshapeだけを保持した。通常ページはinitial submitとterminalを除く134ページ。traceの接続寿命はcallback overheadを含むため診断値であり、公式E2E時間ではない。
- 位相確認: 別の1M public diagnosticにmacOS `sample(1)`（10ms、100秒）を適用した。BEFOREの診断は変更前code、AFTERは最終code。公式E2Eとは別phaseであり、診断の秒数を公式値へ混ぜない。

## SQLite内訳（通常データページ、134ページ）

| 項目 | BEFORE | AFTER | 差 |
|---|---:|---:|---:|
| SQLite接続/page | 11.36 | 6.37 | −44.0% |
| 接続setup/page（PRAGMA×2 + sqlite_master×2を含む） | 44 | 24 | −45.5% |
| BEGIN+COMMIT/page | 12 | 6 | −50% |
| SELECT/page | 158 | 101 | −36.1% |
| INSERT/UPDATE/DELETE/page | 2 | 1 | −50% |
| SQL callback/page | 218.9 | 135.0 | −38.3% |
| 接続寿命合計/page（診断、ms） | 14.16 | 9.82 | −30.6% |

- 接続1回の独立測定は、382MBのstate DBで `connect`+setup+`SELECT 1` 中央値 **0.61ms**（200回）。1ページの接続setup削減は約5接続×0.6msに相当するが、これは診断であり公式wallへ加算しない。
- ownership/authz/grants: 7接続・183 SQL/page → 3接続・111 SQL/page。row読取り、context解決、logical認可、identity照合をまとめ、同じ要求内で二重だったauthorize/identity読取りを除いた。
- query state/stop: 4接続・33 SQL/page → 3接続・21 SQL/page。closure SELECTの重複、2ページ目以降のbackend ID再書込み、未リンクDataOps照会を削減した。admission停止確認・transport再確認・terminal admission確定は残した。
- terminalページ: 11接続・219 SQL → 6接続・135 SQL。replayはこのE2Eに含まれず、下記の敵対的試験で別に確認した。
- `sample(1)`のsqlite shareは **22.4%（134/599）→18.1%（100/553）**、header待ちへの比例配分は **1.526→1.138秒（−0.388秒 / −25.4%、推論）**。unknown/idleをsqliteへ再配分していない。

## 公式E2E（warm 3回のmedian [min–max]、秒）

| rows | route | wall before | wall after | HTTP total before | HTTP total after | header before | header after |
|---:|---|---:|---:|---:|---:|---:|---:|
| 100k | direct | 29.282 [29.055–29.371] | 29.921 [29.802–29.965] | 1.211 [1.184–1.219] | 1.126 [1.118–1.173] | 0.613 [0.604–0.636] | 0.629 [0.619–0.631] |
| 100k | public | 29.905 [29.787–30.038] | 30.350 [30.327–30.610] | 1.541 [1.498–1.583] | 1.450 [1.423–1.511] | 1.446 [1.400–1.486] | 1.352 [1.326–1.413] |
| 288k | direct | 82.207 [81.807–82.518] | 82.293 [82.200–82.605] | 1.526 [1.519–1.559] | 1.415 [1.410–1.548] | 0.228 [0.207–0.230] | 0.199 [0.189–0.233] |
| 288k | public | 83.769 [83.763–84.528] | 84.421 [83.634–84.734] | 2.385 [2.339–2.389] | 2.085 [2.052–2.095] | 2.123 [2.080–2.124] | 1.821 [1.787–1.822] |
| 1M | direct | 287.772 [285.653–289.383] | 287.388 [287.373–288.504] | 4.896 [4.877–5.043] | 4.881 [4.855–4.923] | 0.638 [0.636–0.640] | 0.628 [0.627–0.638] |
| 1M | public | 292.651 [292.155–295.515] | 290.535 [288.069–290.770] | 7.628 [7.615–7.873] | 7.033 [6.823–7.145] | 6.732 [6.691–6.948] | 6.117 [5.932–6.232] |

- 1M public: wall **−2.116秒（−0.72%）**、dispatch→result **−1.364秒（−2.15%）**、validation **−1.479秒（−0.81%）**、HTTP total **−0.595秒（−7.8%）**、header **−0.616秒（−9.1%）**。public wallのafter範囲はbefore範囲と重ならない。
- 288k public: wall **+0.652秒（+0.78%）**、HTTP total **−0.301秒（−12.6%）**、header **−0.302秒（−14.2%）**、dispatch **−0.323秒**、validation **+0.355秒**。direct wallは+0.11%。HTTPは改善、wallはvalidation増で微増の混合。
- 100k public: wall **+0.444秒（+1.49%）**、HTTP total **−0.091秒（−5.9%）**、header **−0.094秒（−6.5%）**。direct wallも+2.19%で、変更経路外のphase差を含む。
- public−direct paired: 1M HTTP extra **2.738→2.110秒（−22.9%）**、header extra **6.094→5.489秒（−9.9%）**。288kはHTTP extra **−22.1%**、header extra **−14.3%**。100kはHTTP extra +0.9%（ほぼ不変）、header extra **−10.9%**。
- follow-up header/call: 100k **57.59→52.89ms**、288k **48.97→42.36ms**、1M **50.16→44.73ms**。
- Workbench CPU delta（補助、ps観測）: 1M public **4.63→3.96秒（−14.5%）**、288k **1.39→1.19秒**、100k **0.68→0.56秒**。directはほぼ不変で、ページ経路のlocal work削減と方向が一致する。これは公式CPU時間ではない。
- 1M publicのwall改善はdirect control（−0.13%）より大きく、HTTP/headerの低下はpairedでも確認できる。ただし100k/288k wallは混合であり、wall一般の改善とはしない。

## 保証・敵対的検証

- 追加試験: 上流I/O後にgrantを剥奪した場合、返却予定ページのdataを返さず `DB_ACCESS_REVOKED`。同じ失敗はtyped failureとしてcacheされ、再GETでdataを返さない。
- 追加試験: 上流I/O後に `execution_closure` を立てた場合、dataを返さず `EXECUTION_CLOSED`。再GETも同codeで、backend GETを再送しない。
- 既存のreplay前再認可試験を再実行: owner/role不一致、closure、current grant revision、logical identity revoke。cache済みdataを返さないことを確認。
- 既存のcancel/UNKNOWN、cold pending、transport deadline、接続再利用、DataOps link付きqueryの毎ページfenceを再実行。新しい負のcacheは未リンク通常queryだけに作用し、link付きqueryは毎回確認する。
- `test_query_full_results` に上記2試験を追加。focused 502件、`make verify-local-experiment` は **162 suite / 5,968 tests / 失敗0**、`make verify-design-context` は exit 0（documents=94、issues=10、references=42）。`git diff --check` exit 0。

## 運用・会計・cleanup

- 公式AFTERは新しい24 runで **24/24 SUCCESS**。BEFOREも24/24 SUCCESS、auditでmanifest/SQL/dataset/snapshot/23列/full comparison/timeout/peak/concurrency/guard/reference/order/engine IDを確認。失敗・未実行・上書き・再送は0。
- 公式AFTER phaseの新規Workbench query recordは14件（matrix public 12 + 診断2）で全てFINISHED。既存UNKNOWN 5件は不変、新規UNKNOWN/CANCELED/FAILEDは0。direct 12件はTrino側で終端確認済み。
- 会計は前campaignの53 rootを継承し、新TASKと新campaign rootを加えた55 root。owned 57件は全てSUCCESS。check時点でused **約39.60GB**、remaining **約1.9599TB**、2TB ceiling・512MiB drain・4GiB declared peak内（最終値は`evidence/accounting-final.json`）。既存accountingはresetしていない。
- doctorはpre/postともhealthy（45 healthy + 1 informational）。Trino pod UID不変。通常Workbenchは最終codeで再起動し、`/api/session` 200（admin）、`/api/status` 200（healthy）、`/api/v1/databases` 200（148件）を確認。状態DB・履歴は保持した。最終確認中に通常serverが停止したため、同じcode・同じ状態DBのままdetached processへ再起動し、同じ3面を再確認した（追加のcode変更なし）。
- GitOpsの既存dirty 2ファイルとindexは変更していない。commitは製品4ファイル、試験1ファイル、本reportのみを対象とする。
- 開発中の証拠として、最初の24 run（`cmp-*`）と途中診断（`diag-1m-public-before/after`、`cnt-*`）を保持するが、公式値には最終24 runと最終診断だけを使う。最初のclean diagnosticはsample fileが期限前に確定せず不採用として保持し、再実行していない。開発中に`test_database_ai`でdatabase lockedが出た原因は、`_resolve_context`の開発途中版がread transactionを保持したまま別接続を開いたことであり、最終版では接続を閉じてからlegacy最終認可を行う形に修正し、全回帰を通した。

## 制約

- 単一host、concurrency 1、warm 3、統計検定なし。BEFOREは別phaseの24 runで、direct controlは−1.68%のphase差を含む。initialはcold cacheの証明ではない。
- SQLite寿命はtrace callback overheadを含む診断値。接続数・SQL分類はexact callback count、秒の比例配分は推論。sampleは1 phase 1 profileで、thread内時系列相関により不確実性を過小評価する。
- replayはE2E 1M publicに含まれず、adversarial unit testsで確認した。100k/288k wallは混合で、wall一般・SLO・100M性能の根拠にしない。benchmark/meter/guardは変更していない。

## 証拠

- 作業領域: `/tmp/shirokuma-sqlite-pages-_r765kh7/`（`raw/final-matrix.json`、`diag/sqlite-final.json`、`diag/sqlite-components.json`、`diag/attribution-*.json`、`evidence/integrity-audit-final.json`、`evidence/accounting-final.json`、`report/final-tables.md`）
- campaign: `/private/tmp/shirokuma-sqlite-pages-20261116-c01/campaign`
- BEFORE再利用元: `/private/tmp/shirokuma-header-wait-20261116-c01/campaign`、reference: `/private/tmp/shirokuma-return-scale-20261116-c01/campaign`
- 診断helper: `harness/instrument/sitecustomize.py`、`harness/analyze_sqlite.py`、`harness/analyze_sample.py`（すべてTASK配下、製品外）
