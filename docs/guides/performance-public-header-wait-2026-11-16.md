# 公開API応答ヘッダ待ちの内訳とページ直列化の削減（2026-11-16）

SF0.1 `store_sales` 23列を **100,000 / 288,464 / 1,000,000行**で返し、同じSQL・同じsnapshot・同じcontextのまま、通常Workbench公開API（all-results）と直接Trinoを比較した。公開APIの応答ヘッダ待ちを「上流Trinoの次ページ待ち」と「Workbench内のページ準備（復号・整形・再符号化・状態DB）」に分解し、重複していたページJSON符号化を製品経路から1回削除した。SLO・engine性能・他製品比較ではない。benchmark・計測器・guard・正式なtiming経路は変更していない。

## 変更（製品経路のみ）

`tools/local_experiment/query_service.py` と `tools/local_experiment/database_http_json.py`:

- full-resultsのreplay cacheを、ページ辞書のdeep copyから**不変のUTF-8 JSON bytes**に変更した。初回配信はそのbytesを`JSONResponse`（dict subclass）としてHTTP層へ渡し、`respond`は再直列化せず`Content-Length`付きで同じbytesを書く。
- 変更前は1ページにつき、上流bodyの復号に加えて「replay cache用の`json.dumps`+`json.loads`」と「HTTP応答用の`json.dumps`」があり、合計2回encode・2回decodeだった。変更後はcache用の1回encodeだけで、初回配信のdecodeはない。replay時だけcache bytesをdecodeして独立した辞書を返す。
- 2ページbound、token順序、単一消費、replay/競合、owner/role/context/revocation/closure再確認、cancel/UNKNOWN、16MiB body bound、deadline、接続再利用は変更していない。cacheはbytes 2ページ分に縮小し、呼出側の辞書mutationはcacheに影響しない。
- 新しい依存・product instrumentation・計測イベントは追加していない。

## BEFOREの再利用と同一性

BEFOREは前campaignの比較24 runをそのまま使用した。両者は同じ入力manifest（SHA-256一致）、同じ23列、同じdataset/snapshot/context、同じroute順（repごとにdirect→public / public→direct交互、initial 1 + warm 3）である。BEFORE campaignのattemptは全件SUCCESS/full comparisonで、実行時のWorkbenchはPID 57099（10:16起動、変更対象ファイルmtime 10:14）であり、その後HEADまでの製品code差分はない（HEADはdocs-only commit）。BEFORE campaignの監査で24/24 SUCCESS、pending/not-run 0、engine ID 24 uniqueを再確認した。

AFTERは新しい専用campaignで、同じmanifest・同じDuckDB reference・同じpublic connection/contextを使った。Workbenchはcode反映のため通常serviceを1回再起動し（PID 79909、15:22起動、code mtime 15:17）、状態DBと履歴は保持した。再起動時に実行中queryはなく、既存UNKNOWN 5件は変更していない。検証suite中の停止と復元は後述の別事象である。

## 測定方法（測定と推論の区別）

- 比較本体: 3 size × 2 route × (initial 1 + warm 3) = **24/24 SUCCESS**。全runで`full comparison`、23列、600秒deadline、declared peak 4GiB、concurrency 1、0.2秒sampler、forced scan、guard failure 0。engine IDは24件で一意。
- 位相帰属: 通常Workbench processに対する外部統計profiler（macOS `sample(1)`、10ms間隔、150秒）を、比較とは別の**1M public diagnostic**（BEFORE 1回、AFTER 1回）に適用した。product/benchmarkには計測コードを追加しておらず、診断も通常の600秒/4GiB/2TB guard内で実行した。
- sample(1)のindentはnested frameごとに**2 column**である。count位置のcolumnから深さを求め、次のnodeのcolumnが現在以上ならstackをpopした。nodeの`self = count - children`を計算し、leafだけでなくinternal nodeに残るself sampleも分類した。v1の4-column前提と`"read"`/`"write"`の部分文字列分類（`Thread_`、`pthread`、無関係なfile writeを含み得た）は破棄した。
- 分類はexact native symbolのみ: upstream TLS I/O（SSL read/write、BIO/sock_write、その配下のpoll/read）、TLS setup（handshake/cert、steady-stateと分離）、JSON encode（`encoder_*`/`py_encode_basestring_ascii`）、JSON decode（`scanner_*`/`scan_once_unicode`/`scanstring_unicode`）、sqlite（`pysqlite_*`/`sqlite3*`）、public request read（TLS配下でない`sock_recv_into`/`__recvfrom`）、public response send（TLS配下でない`sock_sendall`/`__sendto`）、idle/lock（既知stackに属さないpoll/select/`__psynch_cvwait`等）、unknown（上記以外。Thread bootstrap、Python bytecode、GC、malloc、file stat/rename等）。`Thread_`/`pthread`や単なる`read`/`write`をsocket分類に使わない。
- request threadは「50 sample以下の短命threadで、上記のexact markerを1つ以上含む」とし、最小のthread idをinitial POST（sample数とinitial header waitの整合で補強）、残りをfollow-upとした。長寿命daemon 7本と短命timer/nonrequest 136/138本は明示的に除外した。AFTERはfollow-up 137/138本（1本未観測）で、未観測分を捏造しない。follow-upのself sampleは751/598、header-eligible（public request read/response sendを除く）は744/594。全threadで`self`合計=countを確認し、負のselfは0件だった。
- 代表的なnative symbol: upstreamは`_ssl__SSLSocket_read_impl`/`PySSL_select`配下の`poll`、`SSL_read_ex`/`ssl3_read_bytes`/`tls_read_record`/`BIO_read`/`sock_read`、JSON encodeは`encoder_call`/`encoder_listencode_obj`/`encoder_encode_key_value`/`py_encode_basestring_ascii`、JSON decodeは`scanner_call`/`scan_once_unicode`/`scanstring_unicode`、sqliteは`pysqlite_connection_execute_impl`/`sqlite3VdbeExec`/`pread`、public sendは`sock_sendall`配下の`__sendto`、public request readは`sock_recv_into`配下の`__recvfrom`。unknownは`_PyEval_EvalFrameDefault`/`__open`/`_PyObject_Free`/`list_dealloc`等を含み、既知componentへ割り当てない。artifactにカテゴリ別top symbolを保存した。
- 上記shareを同じdiagnosticのclient観測follow-up header waitへ比例配分した秒数は**推論**であり、unknown/idleを既知componentへ配分しない。全カテゴリの比例配分合計はheader waitと一致する（差0.000s）。sampleはrequest thread内で時系列相関するため、二項標準誤差は不確実性を過小評価する。統計検定はしていない。
- 補助として、実shapeに近い2.15MBページのoffline反復（5回中央値）を行った。`json.dumps` 5.9ms、`json.loads` 5.7ms、round-trip 10.6ms、`deepcopy` 21.5ms。製品経路の測定ではなく、機構の整合性確認用である。

## 結果（warm 3回のmedian [min–max]、秒）

| rows | route | phase | wall | dispatch→result | validation | HTTP total | HTTP header wait (all calls) | follow-up calls | follow-up hdr/call ms |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|
| 100k | direct | before | 28.641 [28.383–28.750] | 6.564 [6.348–6.635] | 16.926 [16.915–16.968] | 1.233 [1.137–1.256] | 0.645 [0.605–0.646] | 24 | 24.9 |
| 100k | direct | after | 29.282 [29.055–29.371] | 6.659 [6.646–6.683] | 17.187 [16.911–17.198] | 1.211 [1.184–1.219] | 0.613 [0.604–0.636] | 24 | 23.7 |
| 100k | public | before | 29.506 [29.367–29.873] | 7.341 [7.170–7.418] | 17.017 [16.925–17.224] | 1.682 [1.578–1.775] | 1.585 [1.484–1.682] | 24 [23–24] | 62.1 |
| 100k | public | after | 29.905 [29.787–30.038] | 7.208 [7.162–7.271] | 17.377 [17.246–17.413] | 1.541 [1.498–1.583] | 1.446 [1.400–1.486] | 23 [23–24] | 57.6 |
| 288k | direct | before | 80.512 [80.411–81.245] | 16.385 [16.228–16.649] | 50.178 [50.140–50.455] | 1.400 [1.290–1.427] | 0.198 [0.189–0.204] | 42 [41–42] | 4.1 |
| 288k | direct | after | 82.207 [81.807–82.518] | 16.976 [16.701–17.061] | 50.734 [50.637–50.998] | 1.526 [1.519–1.559] | 0.228 [0.207–0.230] | 42 [41–42] | 4.4 |
| 288k | public | before | 82.498 [81.964–83.141] | 18.423 [18.196–18.611] | 50.090 [49.881–50.525] | 2.742 [2.683–2.781] | 2.478 [2.414–2.517] | 41 [39–41] | 58.4 |
| 288k | public | after | 83.769 [83.763–84.528] | 18.497 [18.320–18.775] | 50.956 [50.753–51.415] | 2.385 [2.339–2.389] | 2.123 [2.080–2.124] | 41 [40–41] | 49.0 |
| 1M | direct | before | 285.682 [285.540–286.806] | 58.442 [57.774–58.463] | 180.434 [179.879–181.240] | 4.907 [4.715–5.070] | 0.643 [0.596–0.658] | 136 [135–138] | 4.5 |
| 1M | direct | after | 287.772 [285.653–289.383] | 58.585 [58.340–59.579] | 181.460 [179.987–181.750] | 4.896 [4.877–5.043] | 0.638 [0.636–0.640] | 133 [132–135] | 4.4 |
| 1M | public | before | 294.481 [292.451–295.019] | 65.331 [64.957–65.554] | 181.089 [180.356–182.295] | 9.309 [9.262–9.462] | 8.403 [8.354–8.566] | 135 [132–137] | 62.6 |
| 1M | public | after | 292.651 [292.155–295.515] | 63.513 [63.346–63.799] | 181.678 [181.115–183.487] | 7.628 [7.615–7.873] | 6.732 [6.691–6.948] | 134 [132–135] | 50.2 |

- 表の`initial`は各route/sizeの最初の1 run（cold cacheの証明ではない）、`warm`は続く3 runである。`HTTP header wait (all calls)`はinitial POSTとfollow-upの合計、`follow-up hdr/call`はfollow-upだけの値である。診断節の「initial header wait」はPOST 1 callを指し、この表のinitial phaseとは別である。
- publicのall-call header waitは **100k −0.139s（−8.8%）、288k −0.355s（−14.3%）、1M −1.671s（−19.9%）**。HTTP totalは同方向に **−0.141 / −0.357 / −1.681s**。pageあたりの改善は **4.6 / 4.1 / 5.5 ms/MB**で、2.15MBページのoffline encode+decode 11.6ms（5.4ms/MB）と整合する。
- initial phase（最初の1 run、all calls）のpublic header waitは **1.857→1.312、2.528→2.057、8.571→6.771s**。1回ずつなので傾向の補助にとどめる。
- body readとclient decodeはほぼ不変（1M public body 0.077→0.069s、decode 0.764→0.791s）。改善はclient側のbody読み・decodeではなく、serverがheaderを返す前の作業から来ている。
- directは変更経路ではない。header waitは **−0.032 / +0.030 / −0.005s**、HTTP totalは **−0.022 / +0.126 / −0.010s**で、publicのような一方向の低下はない。

### public − direct のpaired差（warm median [min–max]）

| rows | HTTP total before | HTTP total after | header (all calls) before | header (all calls) after | wall before | wall after |
|---:|---:|---:|---:|---:|---:|---:|
| 100k | 0.519 [0.345–0.546] | 0.322 [0.287–0.399] | 0.980 [0.838–1.037] | 0.809 [0.786–0.882] | 1.123 [0.725–1.124] | 0.534 [0.506–0.982] |
| 288k | 1.381 [1.255–1.453] | 0.860 [0.780–0.869] | 2.289 [2.210–2.319] | 1.893 [1.851–1.916] | 1.553 [1.254–2.629] | 1.562 [1.245–2.721] |
| 1M | 4.392 [4.355–4.595] | 2.738 [2.732–2.830] | 7.807 [7.711–7.908] | 6.094 [6.055–6.307] | 8.213 [6.770–8.941] | 6.132 [4.879–6.503] |

HTTP extraは **−37.9 / −37.8 / −37.7%**、all-call header extraは **−17.4 / −17.3 / −21.9%**。分母が小さいHTTP extraの相対値は大きく見えるが、絶対減はheader減（≈1.7s at 1M）が主で、direct側の変動は小さい。

## 位相帰属（1M public diagnostic、測定と推論）

| 項目 | BEFORE | AFTER | 差 |
|---|---:|---:|---:|
| **follow-up** header wait（測定） | 8.519s / 135 calls（63.1ms/call） | 6.808s / 138 calls（49.3ms/call） | −1.711s / −20.1% |
| initial header wait（測定、別分母） | 0.103s / 1 call | 0.434s / 1 call | +0.331s（one-shot、帰属に使わない） |
| all-call header wait（initial+follow-up、参考） | 8.622s / 136 calls | 7.242s / 139 calls | −1.380s |
| follow-up request thread（観測/期待） | 135 / 135 | 137 / 138 | 1本未観測 |
| follow-up self samples（測定） | 751 | 598 | — |
| header-eligible self samples | 744 | 594 | — |

| category | samples B/A | all-sample share B/A | header-eligible share B/A | 比例配分 B/A（推論、秒） | Δ秒 |
|---|---:|---:|---:|---:|---:|
| upstream TLS I/O | 244 / 233 | 32.5 / 39.0% | 32.8 / 39.2% | 2.794 / 2.671 | −0.123 |
| TLS setup | 1 / 1 | 0.1 / 0.2% | 0.1 / 0.2% | 0.011 / 0.011 | ±0.000 |
| JSON encode | 128 / 71 | 17.0 / 11.9% | 17.2 / 12.0% | 1.466 / 0.814 | −0.652 |
| JSON decode | 114 / 58 | 15.2 / 9.7% | 15.3 / 9.8% | 1.305 / 0.665 | −0.641 |
| sqlite state | 140 / 142 | 18.6 / 23.7% | 18.8 / 23.9% | 1.603 / 1.628 | +0.024 |
| public request read | 0 / 1 | 0.0 / 0.2% | 0.0 / 0.0% | — | — |
| public response send | 7 / 3 | 0.9 / 0.5% | 0.0 / 0.0% | — | — |
| idle/lock wait | 2 / 3 | 0.3 / 0.5% | 0.3 / 0.5% | 0.023 / 0.034 | +0.011 |
| unknown | 115 / 86 | 15.3 / 14.4% | 15.5 / 14.5% | 1.317 / 0.986 | −0.331 |
| **total** | **751 / 598** | **100 / 100%** | **100 / 100%** | **8.519 / 6.808** | **−1.711** |

- JSON合計は **242/129 samples（all-sample 32.2→21.6%、header-eligible 32.5→21.7%）**、比例配分 **2.771→1.479s（−1.292s）**。JSON encode/decodeの各shareが下がり、ページあたり1回のencode+decode削除という機構と方向が一致する。
- 既知のlocal work（JSON+sqlite）は **382/271 samples（all-sample 50.9→45.3%、header-eligible 51.3→45.6%）**、比例配分 **4.374→3.106s（−1.268s）**。upstream TLSは **244/233（all-sample 32.5→39.0%、header-eligible 32.8→39.2%）**、**2.794→2.671s（−0.123s）**、sqliteは **+0.024s**、TLS setupはほぼ0、unknownは **1.317→0.986s**。これらはwall sampleの比率で、CPU時間ではない。
- 観測header減−1.711sの内訳は、known local −1.268s（うちJSON −1.292s、sqlite +0.024s）、unknown+idle −0.320s、upstream TLS −0.123s。**unknown/idleをJSONやengineへ再配分しない**。したがって「JSON削除がheader減の約1.29sを説明する」までは言えるが、残り0.32sをJSONへ帰属しない。
- 全sampleのraw shareが保守的な主値で、header-eligible shareはpublic request read/response sendだけを分母から除いた感度である。両者を混同しない。unknownは15%前後で残り、既知componentへ割り当てていない。
- upstream TLSはnetwork/TLS/read待ちを含むが、**純粋なTrino engine時間ではない**。Trino engine内部のtimestampは取得していない。directのheader wait 0.64sもpublic processのupstream待ちの代用にしない（directとpublicは別process・別page cadence・別接続）。
- 比例配分はrequestごとのtimestamp照合ではなく、sampleの位相比率をheaderへ写したもの。JSON share差−10.6ppの二項近似は±5pp程度だが、thread内の時系列相関で過小評価であり、1 profile/phaseの推定である。秒の差は方向と大きさの推定で、厳密な配分値ではない。
- 背景除外の評価: 長寿命daemon 7本（sample 91,210/93,121）と短命timer/nonrequest 136/138本（sample 337/330）を除外した。後者はTLS/JSON/sqlite/public socket markerを持たず、timer待ちが主である。AFTERのfollow-up 1本は未観測で、この不足は分母に捏造せず、coverage 137/138として残す。

## 帰属再解析の修正

v1 attribution artifactは、sample(1)のindentを4 column/levelとして扱い、`"read"`/`"write"`の部分文字列で`Thread_`/`pthread`や無関係なfile I/Oをsocket分類し得たため、祖先関係とカテゴリが誤っていた。v1は`attribution-v1-superseded.json`として保持し、v2 scriptで再解析した。raw profile、比較48値（BEFORE 24 + AFTER 24）、製品code、meter、guardは変更していない。再解析は新しいbenchmark runをしていない。
- 再解析: `harness/analyze_sample.py`（2-column indentation、stack pop、self sample、exact symbol）と`harness/build_attribution.py`。成果物は`diag/attribution.json`、`diag/attribution-before-v2.json`、`diag/attribution-after-v2.json`。raw profileのSHA-256とattempt SHA-256をartifactに記録した。

## なぜwallの符号・相対効果が一致しないか

- validationとartifactがwallの大半（1M publicで約181s + client側処理）を占め、今回の変更はその外側のheader待ちだけを削る。headerが1.7s短くなってもwallは0.6%程度にしかならず、phase間のengine/validation drift（direct wall +0.7〜+2.2%、288k public validation +0.87sなど）に埋もれる。
- 100k/288kのpublic wallは+1.4〜+1.5%だが、directも+2.1〜+2.2%で、変更経路外も同方向に遅い。1M public wallが−0.6%なのはdirect +0.7%と逆で、dispatch−2.8%・validation+0.3%の混合であり、header改善の因果効果とは分離できない。
- 相対効果はpayload量に比例する。100kは24ページ・30.2MBで固定費が相対的に大きく−8.8%、1Mは135ページ・302.8MBで−19.9%。HTTP totalの相対効果がheaderより少し小さいのは、initial/connect/teardownなど変わらない成分を含むため。
- paired HTTP extraの−38%は「public余分」の分母が小さいことによる見かけの増幅で、header extraの−17〜−22%と混同しない。

## 保証・検証・cleanup

- BEFORE 24/24とAFTER 24/24は、同じSQL hash、dataset、snapshot、23列、public context/all-results、direct catalog/schema、DuckDB reference full comparison、timeout 600s、peak 4GiB、concurrency 1、sampler 0.2s、guard failure 0を満たした。AFTERのengine IDは24件で一意。失敗・未実行・上書き・再送は0。
- 新campaignは前campaignの51 baselineをそのまま継承し、TASK treeとcampaign rootをzero baselineとして追加した53 root。host baseline・2TB ceiling・512MiB drainを引き継ぎ、used 30,000,807,936 bytes / remaining 1,969,462,321,152 bytesで上限内。既存accountingはresetしていない。
- 新規query recordは公開経路の14件（diagnostic 2 + 比較12）で全てFINISHED。direct 12件はTrino側で終端確認済み。新規UNKNOWN/CANCELED/FAILEDは0、既存UNKNOWN 5件は不変。cancel/停止障害注入は行っておらず、正常完走は停止保証の再実証ではない（回帰testと不変コードが根拠）。
- doctorはpre/postともhealthy（45 healthy + 1 informational）。Trino pod UID不変。code反映のための再起動はPID 79909の1回で、診断/比較を実行した。検証suite中に通常serverが停止したため、検証後に同じcodeでdetached process（PID 71599）へ復元した。これは追加のcode変更ではなく、状態DB・履歴を保持したrestorationである。所有tunnel・一時authは終了時に閉塞/削除を確認し、新しいcredentialは残していない。
- focused tests: full-results 34、cold resume 20、query service 47、corrections 21、result exports 18、request auth 20、grant boundary 5、AI plan 52、execution recovery 125、Workbench database UI 71、local-perf public/corrections/timings 44/18/25。合計500件、すべてPASS。
- `make verify-local-experiment` はdataops venvを`PYTHON`に指定して **162 suite / 5,966 tests / failure 0**。当初の無指定実行はsystem Python 3.9にPyYAMLがなく`test_database_inventory`で停止したため、同じ変更を正規venvで再実行した（製品codeの失敗ではない）。背景で開始したverificationはsuite途中でharness環境により終了され、test failureの記録は残らなかった。foregroundの再実行が上記結果である。
- `make verify-design-context` exit 0（documents=94、issues=10、references=42）、newline/trailing-whitespace/secret-filename checks exit 0。最終doctorはhealthy（45 healthy + 1 informational）、Workbench session/statusは200、Trino pod UIDは不変。
- 診断wrapperの初回起動はsample待機timeoutでcampaign run/attemptを作らず終了し、同一commandの再実行で成功した。比較24・診断成功2には数えない。
- 最終の通常Workbenchをdetached process（PID 71599）で復元し、/api/sessionと/api/statusを確認した。検証suite中に通常serverが停止していたため、matrix/diagnosticのPID 79909とは別の復元であり、状態DB・履歴は保持している。
- 変更はlocal commitのみ。push・外部公開・依存追加・global設定変更はない。

## 制約

- 単一host、同時実行1、warm 3、initial 1。統計検定なし。initialはcold cacheの証明ではない。BEFORE/AFTERは別phaseで、server再起動・cache状態不明・host/backend driftを含む。
- benchmark validationとartifact書出しは製品の返却時間ではない。HTTP wire bytesは取得せず、JSONL file bytesをpayload proxyとした。
- 帰属はnative symbolの外部sampleで、Python frame名・per-request時刻を持たない。診断はBEFORE/AFTER各1回、follow-up self sample 751/598（header-eligible 744/594）で、AFTERはfollow-up 1本未観測、時系列相関・sampling overhead未較正・背景thread除外の限界がある。initial/follow-upの識別は最小thread idとsample数/initial header waitの整合に依存する。比例配分の秒数は推論である。
- 1M入力は288,464行の3コピー+134,608行tailであり、scan量とoutput量は別。10M/100Mは実行していない。SLO・一般wall・cross-engine性能は未受入。

## 結論

公開APIのheader待ちは、1M publicで**8.403→6.732s（−1.671s / −19.9%）**、HTTP totalで**9.309→7.628s（−1.681s / −18.1%）**。診断のfollow-up内訳では、known local work（JSON+sqlite）が**約4.37→3.11s（推論、−1.27s）**、うちJSONが**約2.77→1.48s（−1.29s）**、upstream TLSが**約2.79→2.67s（−0.12s）**、unknown/idleが**約1.34→1.02s**だった。JSONの重複encode/decode削除は観測header減の約1.3sを説明するが、unknown/idleを既知componentへ再配分しない。direct controlはheaderほぼ不変で、変更経路の効果と整合する。wallはvalidation/artifactとphase driftが支配するため符号が一定せず、一般wall改善・SLO・100M性能としては主張しない。採用は「公開full-resultsのページ直列化1回削減」に限定する。
