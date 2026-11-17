# Workbench full-resultsページ認可transaction内の重複identity/binding読取り削減（2026-11-17）

通常Workbench公開APIのfull-results 1ページは、上流Trino GETの前後にそれぞれ独立した認可transactionを持つ。今回、各transaction内で同じ行を複数回読んでいた現在account/role、database/catalog binding/論理binding/foundation/generation、logical identityの読取りを、transaction内で一度読んだ事実を後続の認可判断へ渡す形に整理した。pre-upstreamとpost-upstreamの認可transaction、要求・ページ・threadをまたぐcacheは追加していない。1M publicの製品外traceでは、通常ページのownership/authz SQL callbackが **106→40/page**、identity/binding読取りが **94→28/page** となり、接続数・setup・page state/transport処理は変えていない。公式24 runは **24/24 SUCCESS**。public HTTP totalは100k/288k/1Mで **−1.20% / −3.30% / −2.70%**、header待ちは **−1.37% / −3.73% / −3.09%** だったが、同じphaseのdirect routeもvalidation/HTTPが遅く、wallの一般改善・SLO・engine性能としては主張しない。

## 変更（製品経路のみ）

- `tools/local_experiment/request_auth.py`
  - 現在account/roleの検証とgrant epoch読取りを小さな内部helperへ分け、`_authorize_database_current` と新しい `_authorize_database_prepared` で共有した。
  - `_authorize_database_prepared` は、呼出側が同一transactionで読んだdatabase/catalog binding/generation/logical bindingと、任意で同じtransactionのaccountを受け取る。account/role/grant/logical identity authorityは引き続きこの関数内で現在値を検証し、渡された事実だけで認可を完結させない。
  - `_authorize_database_current` のlogical identity解決は、既に読んだ `resolve_binding` の結果を `_identity_for_binding` へ渡す形にし、同じtransaction内の二重 `resolve_binding` を除いた。
- `tools/local_experiment/query_context.py`
  - `_resolve_context_in` はdatabase/schema/catalog bindingを一度読み、logical modeでは `resolve_binding` とgenerationを一度読んで `_authorize_database_prepared` へ渡す。従来の `authorize_database` 呼出しと、その後の `resolve_identity` 呼出しをこの経路から除いた。
  - `_owned` が既に読んだaccountとlogical modeを内部引数で渡せるようにした。legacy modeは従来どおり短い接続を分け、`_resolve_context_finish` の最終認可も変更しない。
- `tools/local_experiment/query_service.py`
  - `_owned` はlogical_v1のとき、同じtransactionで読んだaccountとmodeを `_resolve_context` へ渡す。pre-upstream/post-upstreamの `_owned` 呼出し自体は分離したまま。
- 保持したもの: 毎回の現在account/role/grant/platform判断、logical identityのbackend username照合、context/catalog/schema/binding/revision/generation照合、retiring/conflicting physical state、execution closure、stop/cancel/UNKNOWN、replay、range、failure処理、上流I/O後の再認可。要求をまたぐsnapshot/cacheはない。

## 調査した2つの認可transaction

- 通常ページの `fetch` は `_fetch_full` で上流GETの前に `_owned` を1回、GET後に `_owned` をもう1回呼ぶ。それぞれが `control_store.connect()` + `BEGIN` の独立transactionで、post側は上流I/O後の現在値を再読する。
- 変更前のlogical_v1 `_owned` は、同じtransaction内で `authorize_database` のaccount/database/binding/generation/grant/logical binding/identity読取りを行った後、`_resolve_context_in` が `resolve_identity` をもう一度呼び、`authorize_database` 自身も `resolve_binding` を再実行していた。
- 変更後は、row/account/context/database/schema/catalog bindingを一度読み、logical binding/foundation/generationを一度解決し、`_authorize_database_prepared` がaccount/role/grant/identity authorityだけを現在値で検証する。pre/postの2 transaction、および要求・ページ・thread間でこの事実を共有しない。

## SQLite内訳（製品外trace、1M public all-results）

- ページあたりownership/authz接続は **2本（pre/post）で変更なし**。1認可あたりのSQL callbackは **53→20**、setup（PRAGMA/sqlite_master/BEGIN）を除く認可読取りは **48→15**。
- 通常ページ（initial submitとterminalを除く）はBEFORE 134 / AFTER 131。page数はTrinoのpage分割で変わり得るため、per-page値は各phaseのpage数で割ったmeanとして比較する。
- ownership/authz callbackは **106→40/page**、うちidentity/binding読取りは **94→28/page**、setupは **12/pageで不変**。総callbackは **14,204→5,240（−63.1%）**、identity/binding総数は **12,596→3,668（−70.9%）**。削減分はすべてidentity/binding読取りで、page row/state/transport/admission処理は変更していない。

| shape | before/認可 | after/認可 |
|---|---:|---:|
| query row | 1 | 1 |
| creation_mode | 4 | 1 |
| databases | 3 | 1 |
| schemas | 1 | 1 |
| catalog_bindings | 7 | 2 |
| db_creations root | 5 | 1 |
| logical_bindings | 5 | 1 |
| databases pins | 5 | 1 |
| logical_foundations | 5 | 1 |
| generation pins | 5 | 1 |
| generation row | 2 | 1 |
| effective_grants | 1 | 1 |
| logical_backend_identities | 2 | 1 |
| identity gate | 2 | 1 |
| grants join | 2 | 1 |

- 1M publicのadmin contextではeffective_grants/grants joinは0回（platform admin経路）。bi_viewerの製品外fixture traceでは、effective_grants 1回・grants join 2→1回を含めて53→20を確認した。
- BEFOREは前studyの最終trace（同じbaseline code、SHA一致）を再利用し、AFTERは最終codeで1回取得した。traceは`sitecustomize`でSQLite connect/traceだけを数え、SQL本文はliteralを伏せたshapeだけを保持する。製品ファイル・benchmark・guard・正式timing経路は変更していない。

## 公式E2E（warm 3回のmedian [min–max]、秒）

| rows | route | wall before | wall after | HTTP total before | HTTP total after | header before | header after | dispatch→result before | dispatch→result after |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 100k | direct | 29.921 [29.802–29.965] | 30.059 [29.788–30.240] | 1.126 [1.118–1.173] | 1.178 [1.144–1.208] | 0.629 [0.619–0.631] | 0.626 [0.593–0.630] | 6.757 [6.700–6.813] | 6.809 [6.733–6.918] |
| 100k | public | 30.350 [30.327–30.610] | 30.782 [30.708–31.365] | 1.450 [1.423–1.511] | 1.433 [1.400–1.457] | 1.352 [1.326–1.413] | 1.334 [1.302–1.358] | 7.202 [7.185–7.398] | 7.221 [7.201–7.384] |
| 288k | direct | 82.293 [82.200–82.605] | 83.876 [83.776–84.531] | 1.415 [1.410–1.548] | 1.445 [1.392–1.492] | 0.199 [0.189–0.233] | 0.214 [0.204–0.221] | 16.867 [16.803–17.259] | 17.166 [17.127–17.522] |
| 288k | public | 84.421 [83.634–84.734] | 85.905 [85.480–87.449] | 2.085 [2.052–2.095] | 2.016 [2.009–2.102] | 1.821 [1.787–1.822] | 1.753 [1.740–1.836] | 18.174 [17.973–18.305] | 18.561 [18.466–18.629] |
| 1M | direct | 287.388 [287.373–288.504] | 293.926 [293.632–295.394] | 4.881 [4.855–4.923] | 4.968 [4.555–4.978] | 0.628 [0.627–0.638] | 0.654 [0.583–0.668] | 59.067 [58.974–59.178] | 59.975 [59.675–60.392] |
| 1M | public | 290.535 [288.069–290.770] | 295.977 [295.740–297.090] | 7.033 [6.823–7.145] | 6.844 [6.756–6.943] | 6.117 [5.932–6.232] | 5.928 [5.839–6.033] | 62.149 [61.332–62.373] | 62.965 [62.610–63.322] |

- public HTTP totalは3 sizeで **−1.20% / −3.30% / −2.70%**、header待ちは **−1.37% / −3.73% / −3.09%**。同じphaseのdirect HTTP totalは **+4.69% / +2.16% / +1.78%**、direct headerは **−0.49% / +7.58% / +4.18%** で、絶対値の低下を変更だけへ帰属しない。
- public−direct paired（warm median、秒）: HTTP extraは100k **0.3246→0.2252（−30.6%）**、288k **0.6699→0.6165（−8.0%）**、1M **2.1100→1.9647（−6.9%）**。header extraは100k **0.7206→0.7033（−2.4%）**、288k **1.6229→1.5319（−5.6%）**、1M **5.4890→5.2737（−3.9%）**。方向は全sizeで一致するが、warm 3回の範囲は広い。
- wallは100k/288k/1M publicで **+1.43% / +1.76% / +1.87%**、directも **+0.46% / +1.92% / +2.28%**。validationはpublic/directとも+2〜4%で、同じphaseの外部変動を含む。paired wall extraは **+68% / −8.7% / −20.0%** と混合・高分散であり、wall改善は主張しない。
- 判定: wallのE2E改善は確認できず、SLO・engine性能の主張はしない。一方、認可SQL callbackの削減はexactで、public HTTP/headerのpaired extraは全sizeで低下したため、安全な重複削減として変更を保持する。残るtradeoffは、接続数・setup・page state/transportは変わらず、wallは外部phaseに埋もれることである。
- 100k/288k/1Mの各warm repのpaired HTTP/header extraは `report/official-tables.md` に保持する。initialは各1回で、paired HTTP extraは100k −7.3%、288k +2.8%、1M −7.1%、paired header extraは100k −1.2%、288k +0.4%、1M −1.1%。

## 保証・敵対的検証

- 上流I/O中にlogical identityをrevokeした場合、返却予定ページのdataを返さず `DB_ACCESS_REVOKED`。再GETもbackend GETを再送しない。
- 上流I/O中にcatalog bindingのdesired revisionを変更した場合、dataを返さず `DB_ACCESS_REVOKED`。
- 上流I/O中にaccountをdisableした場合、dataを返さず `DB_ACCESS_REVOKED`。
- 既存のgrant剥奪・closure・replay前identity revoke・owner/role/closure再確認・cancel/UNKNOWN・cold pending・transport deadline・接続再利用・DataOps link fenceを再実行する。
- page tokenを同じownerの別queryへ流用するcross-context substitutionを追加し、`QUERY_NOT_FOUND` でbackend GETを出さないことを確認する。
- 開発中にc01 campaignの288k public-0でWorkbench processが消え、`ConnectionRefusedError` のUNKNOWN attemptが残った。reconcileはUNKNOWNのままで、新しいdispatchをblockしたため、c01は証拠として保持し、c02 campaignをfresh baselineで作成して公式24 runを実行する。失敗runのdata/backend handleは無く、公式値には使わない。c01のunreconciled attemptが新規dispatchをblockしたため、最初のofficial名の試行はrun directoryを作らず保持し、c02を新設した（`evidence/blocked-official-attempt.json`）。

## 運用・会計・cleanup

- 実装と測定: 測定時HEADは `a0260010`（product変更はworktree、通常Workbench PID 42996にload）。製品3ファイルのSHA256は測定前後で一致し、最終commitは同じbytesを記録する（`evidence/source-deployed.json`）。
- 公式c02は **24/24 SUCCESS**。BEFOREは前studyの最終24 runを、manifest/SQL/dataset/snapshot/23列/full comparison/timeout/peak/concurrency/guard/reference/order/engine IDのaudit後に再利用した。
- BEFORE再利用の位相確認として、c02でone-off direct 1M `eq-1m-direct` を実行し、baseline final directの`result_complete`中央値より **−1.68%**（事前の3%以内）。directはWorkbenchページ経路を通らないcontrolである。
- 公式phaseの設定は全runで600秒、4GiB declared peak、concurrency 1、capacity guard 0.2秒（check calls 1,665–16,934）、global failed scans 0。direct 12 runはTrino側で終端確認済み。
- Workbench query recordは全delta **17件**（1M public診断1、開発100k public 4、公式public 12）。全てFINISHEDで、既存UNKNOWN 5件は不変、新規UNKNOWN/CANCELED/FAILEDは0（`evidence/final-checks.json`）。
- doctorはpre/postともhealthy（45 healthy + 1 informational）。Trino pod UIDは不変。通常Workbenchは最終codeで再起動し、`/api/session` 200（admin）、`/api/status` 200（healthy）、`/api/v1/databases` 200（148件）を確認する（`evidence/server-session-final.json`）。
- 会計はc01の57 rootを継承し、c02 campaign rootを加えた58 root。c01は開発run 12件（うち1件FAILED/UNKNOWN）として保持し、c02の公式24件は全てSUCCESS。final checkは **used 43,993,120,768 bytes（約43.99GB）**、**remaining 1,955,470,008,320 bytes（約1.9555TB）**、2TB ceiling・512MiB drain・4GiB additional内（`evidence/final-checks.json`）。
- 検証: `make verify-local-experiment` データops Pythonで **162 suites / 5,972 tests / 失敗0**、`make verify-design-context` は exit 0（documents=94、issues=10、references=42）、doc checksと`git diff --check`は exit 0（`evidence/verify-summary.json`）。
- GitOpsの既存dirty 2ファイルとindexは変更していない。commitは製品source 3ファイル、試験1ファイル、本reportのみを対象とし、`evidence/git-commit.txt`にhashを残す。

## 制約

- 単一host、concurrency 1、warm 3、統計検定なし。BEFOREは別phaseの24 runで、別途direct 1M controlを確認して再利用した。initialはcold cacheの証明ではない。
- SQLite traceはcallback数とshapeの診断で、寿命/秒はtrace overheadを含む。公式E2Eのwall/HTTPへ診断秒を混ぜない。page境界はstate書込みconnectionからの推論で、request ID/HTTP開始時刻の直接観測ではない。
- admin 1M publicではgrant/identity gateの一部が0回であり、非admin roleの削減は製品外fixture traceで確認した。wall・SLO・100M性能の根拠にしない。
- benchmark・meter・guard・正式timing経路は変更していない。診断は製品外の`sitecustomize`/解析scriptだけを使った。

## 証拠

- 作業領域: `/tmp/shirokuma-authz-reads-j15lv6wk/`（`raw/authz-official-matrix.json`、`diag/sqlite-before.json`、`diag/sqlite-after.json`、`diag/authz-reads.json`、`report/official-tables.md`、`evidence/final-checks.json`、`evidence/source-deployed.json`、`evidence/integrity-audit-final.json`、`evidence/verify-summary.json`、`evidence/failure-reconciliation.json`、`evidence/blocked-official-attempt.json`）
- 公式campaign: `/private/tmp/shirokuma-authz-reads-20261117-c02/campaign`
- 開発campaign（保持、公式外）: `/private/tmp/shirokuma-authz-reads-20261117-c01/campaign`
- BEFORE matrix: `/private/tmp/shirokuma-sqlite-pages-20261116-c01/campaign/final-cmp-*`（copy `raw/baseline-matrix.json`）
- BEFORE diagnostic: `/tmp/shirokuma-sqlite-pages-_r765kh7/diag/sqlite-final.json`（copy `diag/sqlite-before.json`）
- reference: `/private/tmp/shirokuma-return-scale-20261116-c01/campaign/ref-*`
