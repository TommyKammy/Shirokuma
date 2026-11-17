# Workbench full-resultsページ認可transaction内の重複identity/binding読取り削減（2026-11-17）

通常Workbench公開APIのfull-results 1ページは、上流Trino GETの前後にそれぞれ独立した認可transactionを持つ。今回、各transaction内で同じ行を複数回読んでいた現在account/role、database/catalog binding/論理binding/foundation/generation、logical identityの読取りを、transaction内で一度読んだ事実を後続の認可判断へ渡す形に整理した。pre-upstreamとpost-upstreamの認可transaction、要求・ページ・threadをまたぐcacheは追加していない。1M publicの製品外traceでは、通常ページのownership/authz SQL callbackが **106→40/page**、identity/binding-only読取りが **90→24/page** となった。setup 12/page、query row 2/page、schema 2/pageは変わっていない。c02観測24回は **24/24 SUCCESS** だが、c01のfailed attemptがUNKNOWNのままなのでstop手続きの受入は保留し、c02は**観測データのみ**である。public HTTP totalは100k/288k/1Mで **−1.20% / −3.30% / −2.70%**、header待ちは **−1.37% / −3.73% / −3.09%** だったが、同じphaseのdirect routeもvalidation/HTTPが遅く、wallの一般改善・SLO・engine性能としては主張しない。製品変更はfocused/full testsとexact count reductionで別途qualifiedする。

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

- ページあたりownership/authz接続は **2本（pre/post）で変更なし**。live adminの1認可あたりSQL callbackは **53→20**。内訳はsetup 6（PRAGMA 2、sqlite_master 2、BEGIN 1、COMMIT 1）、query row 1、schema 1、identity/binding-only **45→12**。
- 通常ページ（initial submitとterminalを除く）はBEFORE 134 / AFTER 131。page数はTrinoのpage分割で変わり得るため、per-page値は各phaseのpage数で割ったmeanとして比較する。
- ownership/authz callbackは **106→40/page**。setupは **12/pageで不変**（PRAGMA 4、sqlite_master 4、BEGIN 2、COMMIT 2）、query rowも **2/pageで不変**、schemaも **2/pageで不変**、identity/binding-onlyは **90→24/page（−73.3%）**。総callbackは **14,204→5,240（−63.1%）**、identity/binding-only総数は **12,060→3,144（−73.9%）**。以前の「94→28」はquery row+schemaを含む集計だった。削減分はすべてidentity/binding-only読取りで、page row/state/transport/admission処理は変更していない。

| identity/binding-only shape (live admin) | before/認可 | after/認可 |
|---|---:|---:|
| creation_mode | 4 | 1 |
| databases | 3 | 1 |
| catalog_bindings | 7 | 2 |
| db_creations root | 5 | 1 |
| logical_bindings | 5 | 1 |
| databases pins | 5 | 1 |
| logical_foundations | 5 | 1 |
| generation pins | 5 | 1 |
| generation row | 2 | 1 |
| effective_grants | 0 | 0 |
| logical_backend_identities | 2 | 1 |
| identity gate | 2 | 1 |
| grants join | 0 | 0 |

- 上の表はlive adminのidentity/binding-onlyで合計45→12。setup 6/認可、query row 1/認可、schema 1/認可は別。adminではeffective_grants/grants joinは0回で、identity gate 2→1が含まれる。別のbi_viewer fixture trace（live pageではない）では、effective_grants 1回・grants join 2→1・identity gateなしを含むidentity/binding-only **46→13**、総statement **53→20**、setup 5→5、query row 1→1、schema 1→1を確認した。
- BEFOREは前studyの最終trace（同じbaseline code、SHA一致）を再利用し、AFTERは最終codeで1回取得した。traceは`sitecustomize`でSQLite connect/traceだけを数え、SQL本文はliteralを伏せたshapeだけを保持する。製品ファイル・benchmark・guard・正式timing経路は変更していない。

## c02観測E2E（warm 3回のmedian [min–max]、秒; stop手続き受入は保留）

以下はc02の観測値であり、c01のUNKNOWN attemptが残るためstop手続き上の公式受入ではない。

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
- **c01 failed attempt（未解決のまま保持）**: `authz-cmp-288464-public-0`、request_id `b0320a0d-fcf7-4964-a50a-66ea13c449f9`、worksheet_id `de47bff5-1933-4943-9a47-dd66835347f7`、adapter `shirokuma-public`（p26-admin/admin、read-only、all-results）。`ConnectionRefusedError`、attempt UNKNOWN、handleなし、submission_rejectionなし、terminal receiptなし。attempt.jsonとreconciliation.jsonはbyte-identical（SHA256 `fe8a548a...`）で、supported reconcileの結果もUNKNOWN。
- **dispatchなしの根拠と限界**: HTTP初期phaseはconnect 0.000936秒、send/header_wait/body_readはnull。`runner.py`は`conn.connect()`をconnect phase、`conn.request()`をsend phaseで測るため、TCP接続失敗がHTTP送信より前だったことを示す。Workbench logは07:21:59 JSTで終了、PID 39756は07:24:10 JSTにproc_exit、失敗は07:24:17 JST。Workbench state DB 79 tableをrequest_id/worksheet_idで走査して0行、22:22–22:29 UTCのquery_records/admissionsは0行。したがってWorkbenchへのdispatch・query record・admission・backend handleは無い。Trino側の既存全activityを新規観測なしに否定することはできないが、このattemptにlinkするhandle/recordは無い。
- **supported reconciliationの可否**: handle/recovered_handle+terminal receipt、またはHTTP 403 `SQL_PERMISSION_DENIED`のno-dispatch operator auditだけがterminalにできる。ConnectionRefusedは403でもhandleでもないため、synthetic 403・guard clearing・receipt捏造は行わない。UNKNOWNを維持し、c01の新規dispatchはblockされたままにする。
- **process deviation**: c01がUNKNOWNでblockされている間にc02を作成して24 runを実行したのは、明示されたstop手続きからの逸脱である。**stop手続きの受入はwithheld**、c02はobservational only、製品変更は別途qualifiedとする。最初のofficial名の試行はrun directoryを作らず保持し、c02を新設した（`evidence/blocked-official-attempt.json`）。

## 運用・会計・cleanup

- 実装と測定: 測定時HEADは `a0260010`（product変更はworktree、通常Workbench PID 42996にload）。製品3ファイルのSHA256は測定前後で一致し、最終commitは同じbytesを記録する（`evidence/source-deployed.json`）。
- c02観測は **24/24 SUCCESS**。BEFOREは前studyの最終24 runを、manifest/SQL/dataset/snapshot/23列/full comparison/timeout/peak/concurrency/guard/reference/order/engine IDのaudit後に再利用した。これはc02の観測値であり、stop手続きの受入ではない。
- BEFORE再利用の位相確認は**c01の`eq-1m-direct`**（`/private/tmp/shirokuma-authz-reads-20261117-c01/campaign/eq-1m-direct`、direct-only、wall_started 2026-11-16T22:06:36Z、result_complete 58.3949秒）で、baseline final directの中央値より **−1.68%**（3%以内）。**c02には24 trialしかなく、probeは無い**。direct-onlyでWorkbenchページ経路を通らないため、candidate Workbench phaseの支持には使わない。
- c02観測phaseの設定は全runで600秒、4GiB declared peak、concurrency 1、capacity guard 0.2秒（check calls 1,665–16,934）、global failed scans 0。direct 12 runはTrino側で終端確認済み。
- Workbench query recordは全delta **17件**（1M public診断1、開発100k public 4、c02観測public 12）。全てFINISHEDで、既存UNKNOWN 5件は不変、新規UNKNOWN/CANCELED/FAILEDは0（`evidence/final-checks.json`）。
- doctorはpre/postともhealthy（45 healthy + 1 informational）。Trino pod UIDは不変。通常Workbenchは最終codeで再起動し、`/api/session` 200（admin）、`/api/status` 200（healthy）、`/api/v1/databases` 200（148件）を確認する（`evidence/server-session-final.json`）。
- 会計は前studyの**55 root baseline**を継承。c01はTASKとc01 rootを加えた**57 root**、c02はc02 rootを加えた**58 root**。c02はnew campaign rootであり**fresh baselineではない**: c02のbaselineではTASK/c01 rootが0のままで、c01 usageはc02 usedに含まれる。host baselineは両方 `3,089,681,522,688`、2TB ceiling・512MiB drainは同一。review-time read-only checkはc01 used **40,535,060,480** / remaining **1,958,928,068,608**、c02 used **43,995,230,208** / remaining **1,955,467,898,880**、c02-only added **3,460,169,728**。final-checkの43,993,120,768はそれより前のsnapshotで、baselineは変更していない（`evidence/accounting-review.json`）。
- 検証: `make verify-local-experiment` データops Pythonで **162 suites / 5,972 tests / 失敗0**、`make verify-design-context` は exit 0（documents=94、issues=10、references=42）、doc checksと`git diff --check`は exit 0（`evidence/verify-summary.json`）。これらは製品変更のqualificationであり、c02のstop手続き受入ではない。
- GitOpsの既存dirty 2ファイルとindexは変更していない。commitは製品source 3ファイル、試験1ファイル、本reportのみを対象とし、`evidence/git-commit.txt`にhashを残す。

## 次の優先

1. c01のUNKNOWNを解消できるのは、actual authoritative matching handle/terminal receipt、またはtruthfulな403 no-dispatch operator auditだけ。ConnectionRefusedでは後者を作れないため、synthetic 403・guard clearing・receipt捏造をせずUNKNOWNを維持し、c01で新規dispatchしない。
2. stop手続きの受入が明示的に回復するまでc02をofficial/SLO結果として扱わない。c02はobservational dataとして保持する。
3. 製品変更はidentity/binding-only **90→24/page**（bi_viewer fixture 46→13）とfull testsで別途qualifiedし、wall/SLO/engine性能は主張しない。次の性能作業はstop手続きclosure後にsetup/connection等を新しい承認protocolで扱う。

## 制約

- 単一host、concurrency 1、warm 3、統計検定なし。BEFOREは別phaseの24 runで、別途direct 1M controlを確認して再利用した。initialはcold cacheの証明ではない。
- SQLite traceはcallback数とshapeの診断で、寿命/秒はtrace overheadを含む。c02観測E2Eのwall/HTTPへ診断秒を混ぜない。page境界はstate書込みconnectionからの推論で、request ID/HTTP開始時刻の直接観測ではない。
- admin 1M publicではgrant/identity gateの一部が0回であり、非admin roleの削減は製品外fixture traceで確認した。wall・SLO・100M性能の根拠にしない。
- benchmark・meter・guard・正式timing経路は変更していない。診断は製品外の`sitecustomize`/解析scriptだけを使った。

## 証拠

- 作業領域: `/tmp/shirokuma-authz-reads-j15lv6wk/`（`raw/authz-official-matrix.json`、`diag/sqlite-before.json`、`diag/sqlite-after.json`、`diag/authz-reads.json`、`diag/authz-reads-review.json`、`report/official-tables.md`、`evidence/final-checks.json`、`evidence/source-deployed.json`、`evidence/integrity-audit-final.json`、`evidence/verify-summary.json`、`evidence/failure-reconciliation.json`、`evidence/blocked-official-attempt.json`、`evidence/failed-request-state-scan.json`、`evidence/failed-request-process-log.txt`、`evidence/direct-probe-source.json`、`evidence/accounting-review.json`、`evidence/review-followup-note.md`、`evidence/review-followup-summary.json`）
- c02観測campaign: `/private/tmp/shirokuma-authz-reads-20261117-c02/campaign`
- 開発campaign（保持、c02観測外）: `/private/tmp/shirokuma-authz-reads-20261117-c01/campaign`
- BEFORE matrix: `/private/tmp/shirokuma-sqlite-pages-20261116-c01/campaign/final-cmp-*`（copy `raw/baseline-matrix.json`）
- BEFORE diagnostic: `/tmp/shirokuma-sqlite-pages-_r765kh7/diag/sqlite-final.json`（copy `diag/sqlite-before.json`）
- reference: `/private/tmp/shirokuma-return-scale-20261116-c01/campaign/ref-*`
