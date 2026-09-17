---
project: Shirokuma
doc_id: "WP-LOCAL-MILESTONES"
title: "Local Experiment Milestones"
status: active
created: 2026-09-16
updated: 2026-09-17
version: "3.4"
area: work-packages
tags: [shirokuma, local, evidence]
---

# Local Experiment Milestones

最新の到達点（2026-09-17）：新規Iceberg tableでnamed tag/branchが実機成功。
mainへのappend後の名前付き過去読取、branchの条件付き移動、古い条件のHTTP409拒否を確認。
参照更新前後のsnapshot/file metadata不変、実験table回収、保持fixture全履歴/一覧不変を照合した。

前回確認（2026-09-16 09:55 UTC）：ローカル回帰157件と設計関連14件が成功し、
repositoryの設計66 documentsを検証した。今回更新した12 materialized documentsは
Obsidian sourceと一致する。`local-platform-status`の5 component groupsはすべてready。
Pawprintをprivate JSONEachRowへexportし、OpenMetadataの追加backupも保存した。
Cloud APIは未実行。provider/model・認証環境変数名・追加予算は外部利用時の別判断として保持する。

## 2026-09-17: Iceberg named tag and branch

[WP-L3-ICE-001](L3/WP-L3-ICE-001_Iceberg_branch_tag_clone_demo.md)のrun
`9ad2e966fd6c414698abca0706f97467` は08:53:27–08:56:17 UTCに初回成功。
A=480567632882700496の2行にtag/branchを作成し、main appendでB=8990253737475478975の3行へ。
名前付き読取はtag/branchともAを維持し、CASによるbranch移動後はtag A・branch/main Bを照合。
古いexpected AはHTTP409拒否し、metadata不変。3成功＋1拒否それぞれで同current snapshotと
file metadata一覧hashが不変。object内容byte hashの検証や別tableのzero-copy cloneとは区別する。
85手順、worker32/control28 queries。UUID/COMMENT一致の生成1tableを回収し、fixture全履歴/refsと
全10table一覧が元へ戻った。新service/image、権限変更、Pawprint追加、LLM呼出はなし。
41 focused、全local915件（914成功＋標準環境optional OTel1skip）、設計79documents/14testsが成功。
branchへの独立書込・保持table復元保証・本番受入は未検証でlocal-partial。次は既存Data履歴の
OpenMetadata lineage充足状況を、read-only reportとして可視化する。


独立最終監査 `iceberg-refs-preflight/8e22af705b4644928b783811e4fac7b7/final-verification.json` が成功。
保持Kube6Pods/27resources/6CM/3Secret UIDs、Docker3、履歴4tables/5DDL、fixture全履歴/refsと
全table一覧、Pawprints366全行hash、既存247証跡filesが前後不変。Pawprint追加は0。
保存receiptの41項目を再計算し、実Trinoでも捕捉したworker/control全60IDsがFINISHED、active0。
独立readback後に監査用tunnelも終了した。

## 2026-09-17: Data Bear natural-language entry

[WP-L2-AGENT-003](L2/WP-L2-AGENT-003_Data_Engineer_Bear.md)にUTF-8/512文字の
`--request`/`--request-stdin`を追加。固定fixture/3operationだけを型付きIRで選び、
採択IRの実行結果を独立oracleへ照合する。一般の自由文意図を常に正解するという主張はしない。
unsupportedはmaterialization0/exit2。本文/モデル生応答を保存せずhashとplan/usageを保持する。

新言い換え・曖昧/削除/未知列/injectionの7×2は14/14、既存6×2は12/12成功。
評価run `d87069723481431f96e87cc0d9d17b2c` / `fb2781b2c73a442facafd74a8ac913a6`。
自然文文字数実行 `fc906b457f834162acd80a35204a7c1c` はdbt2回・結果 `[[1,9],[2,7]]`、
4AH/Loki/実span相関と生成2relation回収が成功。DataOps `396f97befadd41b3af49bf4db9be4b1c`。
削除依頼の初回 `3f4315b3e32e410fbd9f14e25c1a4486` は正しく拒否したが監査helperの
success固定でfailedとなり、期待statusの両store照合を修正。元証拠を保持し、再実行
`cffc3740880c437aa883ee7d887b94c1` はunsupported/実行0/3event相関まで成功。
新service/image・MTPLX設定変更・cloud呼出はなし。新評価のcase群は既存評価と分離して集計する。


独立最終監査 `data-request-audit/34a457215c244a589c790d06ab8a16e4`（08:32:49 UTC）が成功。
保持Kube6Pods/27resources/6CM/3Secret、3Docker、履歴4表/5DDL、fixtureのschema/rows/
全snapshots/history/refs/headとIceberg全table一覧、旧330eventsが不変。
全5試行の36追加event（success32/blocked4）を帰属させ、AH366件、workflowのLoki/実spanも一致。
正常runのowned2tablesとactive query残留0、拒否2試行はadapter生成なし。
失敗試行を含む29推論の実測usageは12620入力/633出力tokens、cost NULL、cloud/retry0。
既存62証跡と今回163filesが監査中不変で、workflow証跡にraw要求全文の保持は検出されなかった。
Data91＋SRE64 focusedが成功、全local回帰867件（866成功＋標準環境optional OTel1skip）も成功。
同OTel結合は専用venvで成功している。

## 2026-09-17: warehouse idle suspend/wake and local CLI

[WP-L3-VW-002](L3/WP-L3-VW-002_Idle_suspend_and_wake.md)の同期run
`97b8a7289b2e42e496a1d87bddd2b4ad`、CLI session `c41cf9f753c14275837ee9f453ed2ad0` が成功。
各7固定requestで6SELECTの25nation行/実node IDを確認し、停止中XSはSへ未配送。
full query一覧0件でも未消費handleがあればbusy-defer、完了後だけfresh idle/drainを確認し、
Gateway deactivate→同UID Deployment scale0→scale1→新Pod UID/process ready→activate。
保持Sのprocessは不変。一般の未追跡producerやmulti-worker drainの証明ではない。

実Go CLI6commandでprepared wake noop、chill、chill同ID再送、別IDchill noop、wake、wake同ID再送。
4owner操作のreceipt hashと6応答が一致し、再送でscale操作は増えない。private同UIDsocketで
受付10分/16接続、wake後15秒replayのみ、ownerが処理と限定cleanupを完了する。
各17UID明示回収＋旧XS Pod消失、4Pawprints/Loki/実OTel spanを保存・読戻し。
同期trace `2823135a5def108632ed71493ce72530`、CLI trace `ced9a0cbc583bf91dbeed4541eddf247`。
初期4実行失敗とrecover履歴、CLI runtime前import失敗はそのまま保持した。
生成名/空EndpointSlice/終了Pod/非同期health/TCP再接続/script入口を実挙動へ合わせて修正。
汎用CRD、HA/tenant認可/常駐service/本番受入は未達。次のData Bear入口へ進む。

独立最終監査 `warehouse-lifecycle-audit/80d8bfbffe19418b82b249aed451dd92`（08:17:20 UTC）で、
保持6Pods/27resources/6CM/3Secret UIDs、3Dockerの全設定/起動時刻/restarts、履歴4表の全行hash/5DDL、
従来22証跡filesが不変。Pawprints330＝従来311件不変＋今回19件、同じLoki19件と保存span IDsを照合。
失敗4runの11件も残し、成功2run各4件と区別した。全6runtime runsと初CLI startup failureの残留0。
CLI6応答/4operationのraw SHA256、実binary hash、同IDの完全同一応答、scale2回だけを独立再確認。
全回帰836件（835成功・標準環境のoptional OTel1skip）後、更新済みfocused112件がすべて成功。
同OTel結合は専用venvで成功。Go test/vet/race、設計79documents/関連14tests、diff checkも成功。

## 2026-09-17: isolated Gateway VirtualWarehouse

[WP-L3-VW-001](L3/WP-L3-VW-001_Trino_Gateway_VirtualWarehouse.md)のrun
`2823f7f9bc0842c1adf54f37b0b9cc0e`で8/8case成功。XS/Sの実node ID＋25nation行、
各3queryのterminal履歴、最初の逆class付き継続GET XS5/S4回を照合。
未知/欠落routeは500・空bodyかつ両backendでSource0件。未知classにはlegacy groupも付与。
両固定DROPはPERMISSION_DENIED、前後の正常SELECTは同じ結果。認証/warehouse権限の証明ではない。

Gatewayのhistory保存falseをPG実データ0件で確認。PG18.4/Flyway4migration/backend2件、
5363byteのprivate dump→別新規DB復元→schema/index/data＋元DB再読一致。
前後readiness4チェック・両backend HEALTHY、4UID回収、recover追加削除0。
XS/Sは同一Pod/hostを共有するのでtenant/fault isolation/HA/performanceは未観測。
49 focusedを含む全回帰740成功＋標準環境1skip（同OTel結合は専用venvで成功）、78documents。
証跡`gateway-warehouse-preflight/7c1d0d4027eb4e9aa03fbffc114fded5`。
独立after照合で保持Kube6Pods/27resources/6CM/3Secret UIDs、Docker3の全設定/起動時刻/restarts、
Pawprints311件＋履歴4表3/72/1/6の全行hash/5DDL、保護19filesのhash/mtime/modeが完全不変。
live/plan-only計8資源名の残留0。保存historyはuser/rawrowsを省くため、その部分はlive producer検証、
PG復元もprivate dump/hashとreceipt再照合であり独立再実行は行っていない。

## 2026-09-17: live identity audit visibility

run`b94184c624174f0c8f01fc00cae8d7d7`の全15case/61decisionsとcleanup成功を、
新しい実測時刻・case観測時間で16Pawprints/16Loki/16OTelへ記録。7success/9blocked、
うちOPA拒否3件だけpolicy flag=true、認証拒否6件false。推論usage/costはNULL。
送信前runtime同一性、journal、両store読戻し、保存spanのname/status/task/root/query/decision相関を検査。
途中失敗でもcleanup結果を残し、全配送/照合が終わるまで実験成功にしない。
MCPの既存agenthouse_recentが全16件を返し、source metadataと一致。新MCP監査は1件。
Grafanaの同Task/Trace絞込みに拒否9件を実表示。詳細OPA相関はprivate telemetry/OTelの限定射影。
既存Kube6Pods/27resources/6CM/3Secret UIDs不変、3run計12生成resources残留0。
独立after照合でPawprintsは311件＝保持294件完全不変＋新identity16＋MCP1。残4表の全行hash/DDL、
Pawprint DDL、AH/Loki/GrafanaのID/Config/HostConfig/起動時刻/restarts/mount/ports/networksも一致。
保護31filesのhash/mtime/modeを保持。実結果行は非保存のため再照合はcount/matched射影までとする。

59 focused全成功、全回帰691成功＋標準環境1skip（同結合は専用venvで成功）、76documents検証。
証跡`trino-identity-telemetry-preflight/c96c6414e88c4531a7f21b7f2c760a3c`。
次は隔離Gateway/2backendのVirtualWarehouse機能実験へ進む。

## 2026-09-17: authenticated Trino identity

[WP-L3-SEC-001](L3/WP-L3-SEC-001_Keycloak_OPA_native_RBAC_integration.md)の新しい3container Podで、
Keycloak26.7.4の実発行JWT5件をTrino483/OPAへ渡した。寿命300秒/署名/issuer/audience/principalを検証。
正常6、認証拒否6、認可拒否3の全15caseが一致。認証拒否は401＋OPA未到達、
両user header偽装はlab_mask→lab_adminのImpersonateUser拒否。前後readiness4応答も保存。
成功run`8121b529b1844086bfa489a1336a7b60`、61decisionsのquery-ID相関52/直列window限定相関9。
初回の必須claim検査失敗を保持し、空default scopesに明示sub mapperを追加して再試行した。
2run計8resource残留0、初回recoverは削除0、既存6Pods/27resources/6CM/3Secret UIDs一致。
秘密はprivate fixtures/planに限定し、通常receiptの非混入とprivate mode/hashを再検査。
独立再検査は保存射影の照合であり、実token/完全結果行の検査はlive runnerの証拠と区別する。
証跡`trino-identity-preflight/fc0e1b3343bb411b992cb55fc18e76c5`。
全回帰664 tests（identity focused32を含む）・設計76documentsが成功。
次はlive case観測のPawprints/Loki/OTel連携。browserSSO/rotation/role同期/StarRocks/本番受入は未達。

## 2026-09-17: isolated Trino OPA authorization

[WP-L3-SEC-001](L3/WP-L3-SEC-001_Keycloak_OPA_native_RBAC_integration.md)の使い捨てTPCH-only Podで
全25行・行制限1行・列mask25行・複合1行・user/column拒否0行・header偽装の限界・OPA不達0行を確認。
成功run`02986c45d05247d0918cd7cae440f380`。正常decision59件はquery ID+user52件、
query IDなしExecuteQueryの限定照合7件として保存。認証済みidentityとは主張しない。
起動完了とHTTP200の差、Java例外名のJSON表記差を検出・修正し、先行failed2runも保持。
別PodのOPA不達は固定QueryFailed＋ConnectExceptionを要求し、一般errorを合格にしない。
個別readiness応答は保存しておらず、query前後の必須チェックを通ったコード経路として記録。

既知UID/resourceVersionだけを削除し、未知createはdeferred。保存manifestを独立検証する復旧は
現行renderer/config/Rego/PINS変更に依存しない。初回failedrunのrecoverは削除0で成功。
独立検証で3run合計15生成UID残留0、既存6Pods/27resources/6CMが完全一致。
全回帰632 tests、native OPA policy8 tests、設計76documents。保持Trino/Lakehouseへの接続なし。
証跡`trino-opa-preflight/d14316b781e74ec0ab18804df9ac8739`。
本WPはlocal-partialで、次はKeycloak tokenとTrinoのidentity bindingを確認する。

## 2026-09-17: retained history read-only UI

[クエリ実験の履歴](http://127.0.0.1:18030/d/shirokuma-query-history-local)へ5表を追加。
benchmark成功1/失敗2と72query sides、Query Doctorの1run・実測4件・補助2件を表示。
全期間の固定SELECT/FINAL/行数上限で、来歴不明・未観測・測定0を区別する。
既存readerの4table SELECT追加を既知XMLの原本保存・hash照合・atomic replaceで実施し、
同credentials/limits/containersを維持。再enableは変更なし、up/smokeもready。
5panelのAPI/専用reader前後SELECT一致、UIの失敗filterとNULL・精度表示を確認。
API証跡`grafana-history-verification/e4f54e9e4d2740c5809b2afc5150c025`。
保全54項目、5tables全hash/DDLと145元files不変。全回帰609 tests/76documents。
証跡`grafana-history-preflight/8e1933d437a44e36ae11923a3f0f17f2`。

## 2026-09-17: captured own-query history

[WP-L3-CH-001](L3/WP-L3-CH-001_ClickHouse_AgentHouse.md)の追加2tableへ、既存Query Doctorの
1run/6captured query IDsを保存。4件はhistory実測、2件の補助読取は`not_target`とNULLを維持。
保存observer射影の由来を明示し、Gateway/server監査と混同しない。稼働中の初期failedは
終了span・固定終端error等の証拠がない限り、inventoryに残してapplyを拒否する。
初回`b26cf6f6c753459abfd49f581812391b`は1/6追加、再実行`bbe00a0c30c2485fa94d02ca3a94f07f`は0/0。
19 focused testsを含む全回帰592件・設計76documentsが成功。新たなTrino/LLM呼出なし。
独立再照合で元8filesと既存3tables（監査294・benchmark run3・query side72件）の全hash/DDL不変。
private証跡は`query-history-preflight/509e684aee2b4c28a99cea9cffbdead8`。
未達の継続収集/Gateway連携/履歴画面/実費計測を残し、隔離Trino OPA増分へ進む。

## 2026-09-17: failed runsを含むbenchmark履歴

[WP-L3-CH-001](L3/WP-L3-CH-001_ClickHouse_AgentHouse.md)の追加2tableへ、保存済みSF10の
成功1・失敗2runと72query sidesを取込。記録36比較を再照合し、予定残り30は未観測とした。
旧SQL hash欠損はunknown group、NULLと測定0を分離。元SQL/結果行/生errorをDBへ渡さない。
初回`e2c5fd7ea9c24fd1818846399aa2846b`は3/72行、再実行`998612d03fb44bbc86a8df3306262f10`は0/0。
05:32 UTCに全論理行一致、元137filesとPawprint schema/294件不変を独立確認。
固定runtime/所有table定義・衝突拒否・部分挿入の再開を21focused testsで検証。
全回帰573 tests/75documents。証跡は`benchmark-events-preflight/013ba0f37c944c69a7f6710cd473ff7d`。
新たなTPC-H/LLM呼出やruntimeは増やさず、Grafana readerの権限も変更しない。

## 2026-09-17: AgentHouse read-only Grafana

[WP-L3-CH-001](L3/WP-L3-CH-001_ClickHouse_AgentHouse.md)へnative ARM64 Grafana13.2.2と
署名確認済みClickHouse plugin4.21.3を追加。`127.0.0.1:18030`のanonymous Viewerで
既存Pawprintsの失敗/ブロック、Task/Trace、処理別p50/p95、モデルtokensの欠測を確認できる。
専用SELECT-only readerとDB側の30秒/256MiB/1万結果行制限を使用し、writer資格情報を渡さない。
未測定費用はNULL、event成功とTask/PR全体の成功は区別する。

内部bridge単独のport publish不成立とdriverのtimeout+5秒を実機で検出し、
Grafanaだけのfrontend bridgeとplugin25秒設定で解決。全パネルの厳密percentencode filterを
実DB/画面で確認し、40APIcasesの列型/値/NULLを前後SELECTで一致させた。
empty resultのplugin形式差は初回failed証跡を残して検証器を修正した。

停止中network attachmentの誤報も修正し、05:25 UTCに実切断→復元を確認。
同Grafana ID/volume/env/credential、既存CH ID/image/env/mount/port/start/restartを保持し、
監査294件の全bytes/hash不変。05:26 UTCの再API照合40/40、全回帰552 testsが成功。
最終API証跡`grafana-verification/9785773e72e540f2ad63c014db659b70`、
保全/復元証跡`grafana-preflight/b781f2d079d8469c9e1bf274ab83d374`。
実費計測・benchmark取込・Trino query履歴持続化は未達で、後続増分として扱う。

## 2026-09-17: Iceberg snapshot / restore sandbox

[WP-L3-ICE-001](L3/WP-L3-ICE-001_Iceberg_branch_tag_clone_demo.md)の使い捨て2tableで
A（2行）→B（3行）、Aの過去参照、別tableへAのCTAS復元、sourceのAへのrollback、
rollback後のB過去参照が成功した。Run `94a2396b745747c693919337411acc9d`、24手順。
初回`9c127032f70b4bad833a9d3141368081`はTrino483履歴期待の誤りでfailedを保持。
固定versionのsourceに合わせ、current refs/祖先flag/実query結果を厳密照合して修正した。

04:51 UTCに合計4生成table・owned active query残留0、保持fixtureの5種の観測が前後一致。
`tmp/local-experiment/iceberg-sandbox-preflight/8ec05a4823724073bfbd070956af8ec2/`へ独立検証を保存。
20 focused tests、全回帰506 tests、設計75documentsが成功。追加runtime/model呼出なし。
CTASは物理コピーで、named branch/tag・zero-copy clone・保持dataの復元保証は未達。
次は[WP-L3-CH-001](L3/WP-L3-CH-001_ClickHouse_AgentHouse.md)のGrafana read-only表示を進める。

## 2026-09-17: Qwen Policy Gatekeeper

[WP-L2-POL-001](L2/WP-L2-POL-001_Policy_Gatekeeper_Bear.md)はneutralなnative15候補と
操作境界10候補を4batchで分類。host truthと全件一致後、固定native15件をserver dry-runで検証。
入力3907/出力1171tokens、推論33.125秒。allow2/deny12/対象外control1が全件一致し、
7監査と保存OTel、completed checkpointも一致。モデルは権限付与・policy変更をしない。
`tmp/local-experiment/policy-bear/f37433c5b92d462a8ea69f4465c334b5/`に保持。
04:31 UTCに保持6Pod/16policy resourcesの前後不変・残存object0を独立確認した。

8合成case×2は16/16、16監査、12454/2768tokens、81.187秒。
新規core12/Bear18と既存native17を含む全回帰486 tests、設計73documentsを検証。
評価harnessは6suite全8receipt/8groups valid、欠損/不整合/重複/保存失敗/未観測0。
Qwen94callsとmock10を分離し、入力16filesと以前のreport9filesはhash不変。
最終report `tmp/local-experiment/evaluation-reports/076b0abbce144d179e7c223c0126058d/`。
任意policyやリスク品質、署名/CVE保証、本番Tier/PR承認は未達のまま保持する。

## 2026-09-17: Query Doctorと全評価receipt集計

[Query Doctor](L2/WP-L2-AGENT-005_Query_Doctor_Bear.md)は固定2SELECTの結果・同文JSON EXPLAIN・
自身のquery historyを照合し、Qwenが固定SHOW STATSを追加選択した。491/36 tokens、1.696秒。
row count estimate=2を取得し、列統計unknown6項目、root cause未確定、性能改善未測定を維持する。
4監査/保存spanが一致。04:20 UTCにmodel evidence/assessmentを独立再計算し一致、
自身の実行中query0、保持fixture不変。`query-bear/6aae039137f748548db53b59f8e2d3b7/`に保持。
8 golden×2は16/16、16監査、8022/586 tokens、27.881秒。

[評価harness](L2/WP-L2-EVAL-001_Agent_evaluation_harness.md)はモデル/APIを呼ばず全UUID receiptを検査。
5suite・7receiptすべてvalidで、欠損/重複/不整合/保存failureは0。Qwen78/78監査済みcallsと
mock10を分離し、SRE旧6case/現8caseも別groupにする。これは一般的なモデル品質の合算ではない。
元14filesのhash不変を再確認。reportは`tmp/local-experiment/evaluation-reports/14f84889fc1f4fb99c2e4309113989d8/`。
開始日時・code/fixture版・chronological初回成功率・費用はunknown、PR指標/MTTP等はN/Aとする。
Query37＋harness14のfocused checksを含む全回帰455 tests、設計73 documentsが成功。

## 2026-09-17: Qwen Catalog Curator

[WP-L2-AGENT-004](L2/WP-L2-AGENT-004_Catalog_Curator_Bear.md)をlocal-partialとして実装。
固定Data Planのproject/SQL/hash・dbt結果/依存・Dagster保存DB・cleanupを照合し、
Qwenは不足6項目と固定evidence IDsだけを選ぶ。Hostが履歴説明と既存experiment ownerを生成する。
新規6table/6lineageを実OpenMetadataで読戻し、全3caseの再適用は0writes。
name_lengthsのworkflow全体再実行ではQwenもnoopと判断し、table ID不変・0writesだった。

4workflowで計20監査eventと保存OTelを照合。推論は314/96 tokens・2.978秒、
再実行296/42・1.590秒、他2caseは314/96・2.958秒と314/96・2.980秒。
6合成case×2は12/12（3682/862 tokens、28.491秒、12監査読戻し）。
03:59 UTCの独立検証で事前のfixture/team/service/schema API結果が完全一致した。
`tmp/local-experiment/catalog-preflight/a17284a0e35441e09ac690855ace15bf/`に保持。
実行UUID・backup・再実行方法はWP参照。Catalog50 tests、全回帰404 tests、設計72 documents。

LineageはManual sourceで、削除済みSQL relationの履歴である。一般的な説明/owner推定、
任意entity更新、本番受入は対象外。API部分失敗はsafe journalへ残し、rollback成功と誤認しない。

## 2026-09-17: Qwen Data Engineer Bear

[WP-L2-AGENT-003](L2/WP-L2-AGENT-003_Data_Engineer_Bear.md)をlocal-partialとして実装。
モデルは固定fixtureに対する3変換の型付き計画だけを返し、hostが専用dbt projectを生成する。
文字数・大文字化・行数を実データで検証し、それぞれ2回のmaterializationと全dbt testsが成功。
入力/出力tokensは478/23、430/23、400/23、推論秒は1.335、1.328、1.310。
各4件の監査eventがClickHouse/Loki/OTel間で相関一致した。
6 golden要求×2回は12/12成功（4754/262 tokens、15.723秒、12監査読戻し）。

Data Bear証跡UUIDは`a95175c8abc74dd897699a8e38f3acc6`、
`ddf4422d47294b8d912172d284024f49`、`e364c200e2c648e3b1ec79923c6acbff`。
別途`data-plans/eb8902c0a4e54c8591dcbfee02550927/`で意図的test失敗→retry成功を確認。
03:43 UTCに4runのhash/dbt lineage/resultsと実fixtureを再照合し、生成8relation/query残留0。
詳細receiptは`tmp/local-experiment/data-bear-preflight/b09b846899ac41d0847f8c6eb612fb81/`。
全回帰354 tests、設計69 documents。範囲は固定2列2行・3変換だけで、
一般的なデータモデリングや任意コード生成、業務データ変更、本番受入は含めない。
次はこの成功証跡を使うCatalog Curatorの限定履歴登録へ進む。

## 2026-09-17: 5 runtime groupsを含むQwen SRE

Supersetのready判定をowned container running＋固定loopback `/health`のHTTP200/OKへ修正。
停止/HTTP異常と通信不能/観測不明を区別する。proxy/redirect/認証なし・単発3秒・body上限16bytes。
10 testsと実HTTP確認が成功した。

引数なしMCP `local_platform_status`を追加し、Gatewayは9固定read toolsとなった。
5群の固定名・bool/status・全体集計・取得時刻を検証し、reason/free textをモデル出力から除く。
Scope確認＋statusを75秒worker deadlineへ入れ、timeoutでは新規process groupだけを回収する。
Gateway31 tests、実MCP12 callsのCH/Loki/OTel相関が成功。最終証跡は
`tmp/local-experiment/gateway/smoke-1aff3c763c9c41e490cc59a16d51505a.json`。

SREの初期観測はdoctor/Pods/Events/platformの4源。Runtime not-readyをcritical、unknownを
未観測として区別し、critical Pod＋runtime同時発生時には両方のreadを必須にする。
実Qwenは5群readyと過去restartを区別し、Pod再観測を選択。773/51 tokens、3.675秒、7監査相関。
保存射影からhash/evidence/triage/comparisonの再計算一致を確認した。
`tmp/local-experiment/sre-llm/0c69d3ef5ebc401ca3d27bdb4bd98ea7/`に保持する。

8合成ケース×2で16/16成功・16監査readback、入力10308/出力1000tokens、推論合計40.678秒。
証跡は`tmp/local-experiment/sre-evaluation/22615db4c5344a81a261a2086437113b/`。
Runtime差分は状態だけの比較で、instance identity・復旧・原因を断定しない。

Admission適用後の実Failed Pod drillも成功。4初期read＋Pod/Event followupの計6read、
8監査相関、854/146 tokens、4.930秒。作成UID `6f55c9f5-83e9-47a9-b69d-4bedfdadbb89`だけを削除。
既存6 PodのUID/phase/ready/restartsは前後一致、drill残留0。
`tmp/local-experiment/sre-drill/4a856109b32f408185aebe247508c1b2/`に保持する。

全回帰はローカル295＋設計14＝309 tests、設計68 documents。SRE64、Gateway31、
platform-status10、native-policy17のfocused checksを含む。課題はlogs/metricsを使う原因診断、
一般的な修復案の品質と自動修復、production/human受入。これらの達成は主張しない。

## 2026-09-17: namespace限定native admission baseline

**WP-L0-POL-001のlocal機能**として、7種類の既存CEL制約を8個のnative VAPと8 Deny bindingsへ
投影した。Service/Ingressはnativeの型検査に合わせて分割。他6制約の式と元Kyverno source、
過去のimage/CVE判定は変更しない。追加controller/image/volumeなし。
[ADR-0035](../07_ADR/ADR-0035_Use_native_admission_baseline_for_local_experiments.md)と
[WP](L0/WP-L0-POL-001_OPA_Kyverno_baseline_policies.md)に判断・境界・再実行を記録した。

既存6 Pods/3 Services/6 templatesの事前照合は違反0。初回の共通式はIngress型警告で
binding有効化前に停止したため、型別に分割し、今回作成した未稼働の定義1個だけを
UID/RV条件付きで再作成した。最終8 policiesはcurrent generationの明示的な型検査完了・警告0。

実APIでallow2、deny12、対象外namespace control1の15/15が成功。全件server dry-runで
Pod/Service/Ingressは永続化していない。拒否のpolicy/binding/messageを照合し、
単なる通信・RBAC・Schema・CEL評価エラーを成功と数えない。専用BindingのみをUID/RV条件付きで
撤去して再適用し、policy UIDを保持、新Binding UIDを確認後、15/15が再成功した。
既存6 PodのUID/phase/ready/restartsは前後一致、2026-09-17 03:04 UTCの5群はready。

証跡: `tmp/local-experiment/policy/411294d0640a43d1a59155a834469301`（初回成功）、
`4259b9e128b0448b86c20e05336c173b`（撤去）、`539ef89674b746859a443f60afb51c2d`（再適用）、
`3a501f3b4d384238bf0ece97ab6db566`（最終smoke）。保持Pod比較は
`tmp/local-experiment/policy-validation/0acf015be3674b6397524e22f17c919b/after.json`。
17 focused testsが成功。`rtk make local-policy-status local-policy-smoke`で再確認できる。
controller autogen/background report、live ephemeral probe、image attestation、本番受入は未達。

## 2026-09-17: 使い捨てFailed Podの実機SRE drill

**WP-L2-AGENT-002の実障害検知実験**として、新規の専用Pod一個だけを`exit 1`で失敗させた。
モデルはそのPodを`WORKLOAD_FAILED`のcritical所見として選び、実MCPで再観測した。
初回・followupとも同UID・同Failed状態を確認し、既知の過去restart所見と区別できた。
Qwen入力735・出力90 tokens、推論3.398秒、6監査eventの相関・readbackが成功した。

作成物は`sre-drill-f5573fa58e5d4ffc853bd55b02e42a9f`、
UID `10d94a3c-1d29-484a-8f6d-d1e410c9a2e7`。終了時にowner/run labelsとUIDを再照合し、
UID/resourceVersion precondition付きDELETEで削除した。残留drill Podは0。
既存6 Podのname/UID/phase/ready/restartを前後比較して完全一致し、保持workloadを変更していない。

証跡は`tmp/local-experiment/sre-drill/f34f175d82934d5090244ca2fbfb4340/`。
`creation-intent.json`、`created.json`、`summary.json`、nested `workflow/`、
`retained-pods-verification.json`を保持する。作成結果やreceiptが不確かな場合、または
同名でもUIDが変わった場合に名前だけで削除せず、cleanup不明はdeferred・全体failedとする。
この試験Podのcleanupは既存serviceの自動修復ではない。

既存Trinoの固定local image digestとUID10000を使い、pullなし、50m CPU / 32Mi、
非root・readOnlyRootFilesystem・dropALL・RuntimeDefault、token/volume/host pathなしで実行した。
restartNever、deadline60秒。事前server dry-run成功、専用labelsは既存Service/controllerと非一致。
追加image・weights・resident service・data volumeはなく、保存増分は小さなprivate証跡と監査。

14 focused drill testsが成功し、全ローカル回帰245件＋設計14件の計259 testsを検証した。
再実行は`rtk make local-sre-qwen-drill`。deferred時はcreated receiptと現物UIDを照合し、
未確認の作成物や保持データをまとめて消さない。Colima resetは復旧手順に含めない。

事前調査時のlive clusterにはKyverno CRD・追加admission policy/webhookが存在しなかった。
従ってこのdrillのガードをcluster-wide policyの適用証拠にはしない。
その後のローカルpolicy導入結果は上記native admission節に分けて記録した。

## 2026-09-17: QwenによるSRE読み取り確認と再観測

**WP-L2-AGENT-002のlocal機能**としてLangGraph `collect → triage → followup`を追加した。
初回MCP doctor/Pod/Eventの3観測を分類し、Qwenが根拠IDに対応する最大2種類の追加readを選ぶ。
固定のcontext/namespace/引数だけで再観測し、Pod/Event UID・状態・count・取得時刻を比較する。
モデルが生成したshellや引数は使わず、重大Pod所見の確認脱落・未知の根拠・不適切なtool・
healthの改変・root-cause断定は実行前に拒否する。Cloud、GitHub、workload修復は実行しない。

実機では6 PodのうちPolaris/PostgreSQL/Trinoの過去restartを根拠にPod再観測を選んだ。
最新runの累計restartはそれぞれ6/4/1、約3.56秒の間隔でUID・状態・回数に変化なし。
`attention-required`と`root_cause: undetermined`を保持し、障害発生中や回復とは断定しない。
doctor/Eventは再選択されておらず`not-reobserved`を明記する。
Qwen入力656・出力83 tokens、推論3.127秒、cost NULL。4 MCP + 1 model + 1 assessmentの
6件をClickHouseから読戻し、Lokiのtask/trace/span/actionとモデルusage/latencyを照合した。
LangGraphの完了checkpointとlocal OTel spanも確認した。

最新証跡は`tmp/local-experiment/sre-llm/0979b832f0284b15a733cb7ee1ccdd25/`。
`observations.json`には自由記述を除いた固定項目を保持する。保存した4 receiptを再読込し、
元観測hash、followup hash、モデル向け根拠、比較結果の4項目が再計算で一致した。
結果は`replay-verification.json`。最初の成功runも`3a6c80e230094f329228e4e6c2f2fbe4`に保持。

正常/意図Flux停止、過去restart、2つの重大Pod異常、Warning、doctor degraded、Event打切りの
6合成ケースを各2回評価し、**12/12成功**した。入力計6,244・出力計538 tokens、
推論合計23.506秒、12件すべてのPawprintを即時読戻しした。
証跡は`tmp/local-experiment/sre-evaluation/2670bf3683a94a19aa6d79ea48423090/result.json`。
合成評価はMCP呼出し・故障注入をせず、traceは相関IDでありOTel spanを生成しない。

Gateway観測にはUIDと取得時刻、Event最終観測時刻とtruncatedを追加した。
Eventの初回時刻を繰返しEventの直近時刻へ流用せず、作成時刻も発生時刻と混同しない。
再作成Podの同名・UID差、欠測、不完全一覧、逆順時刻、未再観測を区別する。
Python 3.9と3.12の小数秒解析の差を回帰で発見し、明示的な精度正規化で修正した。

focused testsはcontract22、runner/provider/evaluation17、Gateway19件。
全ローカル回帰231件＋設計14件と設計66 documentsが成功。
既存の固定ルールSREも4監査eventで成功した：`tmp/local-experiment/bears/f78c933a281f4675972ce8c7d8f62436/`。

```sh
rtk make local-sre-qwen-smoke
rtk make local-sre-qwen-eval
rtk make test-local-sre-llm
rtk make verify-local-experiment verify-design-context
```

既存MTPLX設定・weightsとworkloadは変更せず、追加常駐サービス・volumeはない。
保存増分は小さなprivate観測receiptと監査。戻す場合は`local-sre-smoke`を使い、保持証跡は削除しない。
これは読み取り確認を選んで比較する機能であり、logs/metricsに基づく原因診断や自動修復、
監視の常駐化、本番・人間受入は未達のまま残る。

## 2026-09-17: 既存MTPLX Qwenへの実LLM接続

**WP-L2-LLM-001のlocal実LLM機能検証**が成功した。OWNER指定の
`qwen3.8-flash-next`を`http://127.0.0.1:8001/v1`で使用する。
APIのmodel ID完全一致と32,768 context、hostの`127.0.0.1:8001` listenerを確認した。
MTPLX 2.11.2・MTPなし・weights約115GBはOWNER提供の構成情報で、再インストールや
weightsの取得、server設定・ログイン時自動起動の変更は行っていない。
OpenAPIで確認した`enable_thinking=false`を明示し、出力512 tokens、timeout 60秒、retryなし。
入力は最大8 KiBの固定instructionsと最大16個の短いmodel識別子に制限する。
これはbyte境界であり、32K全長や最大context性能の評価ではない。

最初の単純JSON probeは0.627秒、入力46・出力14 tokensで成功。
続く実`plan → MCP dbt_list_models → summarize`は2回とも成功し、
`mart_fixture` / `stg_fixture`、件数2、保持されたverified runの有無を完全照合した。
モデルへ渡すのは識別子一覧とbooleanだけで、任意tool payload、path、run tokenは渡さない。
verified runは過去の検証記録を表し、cleanup済み生成tableの現在存在を意味しない。

実測4推論のclient wall timeは0.518 / 1.731 / 0.482 / 1.589秒、
入力計376・出力計128 tokens。AgentHouseから8件を再読込し、task/trace、
provider/model、実測tokens/latencyが元の応答と一致した。価格未評価のcostはNULL。
別枠のColima→`host.lima.internal:8001`推論も0.811秒で成功した。
証跡は`tmp/local-experiment/llm/cb4e7e837bc7425b9e634b23b7521ca7/summary.json`。
mockの回帰2 runsも成功：`tmp/local-experiment/llm/859f888874934d569d1003f450fdf1fa/summary.json`。

応答検証をhost/VMで共通化した最終run
`tmp/local-experiment/llm/8c79338e13b2474784dc3f636f219f88/summary.json`も成功。
VM probeの入力56・出力13 tokens、0.733秒をgraphとは別のreceiptへ保持した。
後続graphの4推論は入力計376・出力計128 tokens、0.519–1.603秒で、8監査eventと一致した。
途中で応答や監査保存が失敗しても、既知の使用量・時間・配送成否はprivate証跡に残す。

さらに、空一覧、1件、別名3件、未検証記録、16件上限の5合成fixtureを各2回評価し、
Qwenは**10/10**成功した。必須keys、モデル名、件数、booleanが毎回一致し、
10件の`llm.eval_summary`をAgentHouseから即時読戻しした。入力計1,510・出力計644 tokens、
推論時間合計19.110秒、1回0.782–4.910秒、観測した入力+出力の最大418 tokens。
traceは評価相関用IDであり、この評価コマンドはOTel spanやMCP tool呼出しを生成しない。
証跡は`tmp/local-experiment/llm-evaluation/478640a6721444be8e9b15ee6518dd18/summary.json`。
mockも同じfixtureで10/10成功し、usage/cost NULLを保持した。
再実行は`rtk make local-llm-qwen-eval`（mockは`local-llm-eval`）。
これはWP-L2-LLM-001の入力追従検証であり、WP-L2-EVAL-001のPR成功率・修正率・MTTPは測っていない。

focused testsはLLM31件＋評価10件。`make verify-local-experiment verify-design-context`は
合計199 testsと設計66 documentsが成功した。fixture捏造、誤count/flag、incomplete JSON、
fail時usage、監査障害時停止、VM失敗receipt、cloud実行拒否を検証する。

Colima停止は検出時の事実で、停止原因は未特定。16 CPU / 96 GiB / 400 GiB設定を保持して
再開し、既存container・volumeを使って5 component groupsを復旧した。
Colima設定hash、active Docker/kube contextは不変。Seaweed8Gi、親Flux停止を維持した。
非モデルMCP smokeは8 tools、11監査eventのClickHouse/Loki/OTel相関が成功。
復旧証跡は`tmp/local-experiment/recovery-20260917/summary.json`。

再実行：`rtk make local-llm-qwen-smoke`。専用profileは
`tools/local_experiment/llm/providers.qwen-local.json`、default mockは変更していない。
応答のmodel ID、finish_reason、JSON、要約事実の不一致は拒否し、失敗時も有効なusageを保持する。
戻す場合は`rtk make local-llm-smoke`を使うだけで、既存MTPLX serviceや保持監査記録は削除しない。
新規resident component・weights・tableはなく、追加保存は小さなコードと監査/証跡のみ。

Cloud smokeは未達のまま保留するが、local実LLM作業の依存条件にはしない。
この結果は固定の読み取りinventory taskの検証であり、汎用モデル品質、SRE診断、自動修復、
本番・人間受入の完了ではない。

## 2026-09-16: TPC-H SF10 全22クエリの完走

**WP-L1-BENCH-001のローカル機能検証**が成功した。Trino 483の固定query corpusを使い、
SF10の8テーブル・86,586,082行をIcebergへ生成した。全22 queryでgenerator参照結果と
Iceberg結果が一致し、実行queryの終了確認後、今回の8 tableを所有コメント確認付きで削除した。
保持fixtureの前後一致と生成table残数0を確認した。

成功runは`3ca015b7c9d2431c9997b23dda777cb9`、完了時刻は2026-09-16 09:52:05 UTC。
SF10は公称10GBのsource規模で、実測Parquet data filesは2,286,204,977 bytesだった。
22 queryのclient wall time合計はgenerator参照174.35秒、Iceberg66.81秒。
generator・client・測定対象を同居させ、cacheや並行実験を分離していないため、
公式TPC成績・本番capacity・他製品との性能比較として扱わない。
100GBは実行しておらず、別の容量・実行時間評価として扱う。

環境はColima 16 CPU / 96 GiB / 400 GiB、Trino 8 CPU / 16 GiB（heap 12G、query 8GB）。
SeaweedFSは次の実障害の修正で2 CPU / 8 GiB、memory request 1 GiBへ変更した。
既存PVC、保持dataset、backupは変更・削除していない。

- 最初のrunはQ15で不一致。同じgenerator queryだけでも0行→1行が再現し、
  CTEを二度展開したDOUBLE SUMが`2194132.8166000005`と`2194132.8165999996`に分かれた。
  Q15の金額・割引だけをDECIMALへ変換し、両経路へ同じ式を適用した。
  全59,986,052 lineitemで元の2桁金額・割引値を保持することを確認した。
  vendor SQLとchecksum、比較条件は保持し、実行時SQL/hashと変換理由を記録する。
- 次のrunはSeaweedFSの2 GiB上限でOOMKilled（exit 137）となり、Parquet commitが失敗した。
  Flux storage childのlocal patchで8 GiBへ変更し、rollout後に再試験した。
  失敗したCTASの専用prefixに残った15 incomplete multipart uploadsだけを回収し、
  upload/objectとも0件を確認した。SQL tableが0件でもobject回収完了とはみなさない。
- 型検証は完全なcolumn type列を要求する。Icebergの文書化された`varchar(n) → varchar`
  変換だけを明示的に扱い、文字列型と元の長さ上限も検証する。
  数値・文字列・booleanの型違いは拒否し、金額結果の比較を緩めていない。

証跡は`tmp/local-experiment/benchmark/<run>/`。失敗runも保持している。
現行の型比較実装で保存済み22組を再検証し、source/result hashを
`comparison-reverification.json`へ保存した。比較器のfocused testsは20件。
SeaweedFSはrollout後の同一Podでrestart 0、観測したmemory sample最大値は4,828Miだった。
再実行は`rtk make local-tpch-smoke`、詳細はrepositoryの
`tools/local_experiment/benchmark/README.md`と`deploy/local-experiment/storage/README.md`を参照。

## 2026-09-16: 読み取り専用SRE診断

**WP-L2-AGENT-002の部分実装**として、MCP doctor/Pod/Eventの構造化snapshotを
固定ルールで分類する診断を追加した。実LLM推論や自動修復、PR提案の完了とは扱わない。
実機6 Pod / 9 Eventを読み取り、3 tool callsと診断自身の計4件をClickHouse/Lokiへ記録した。
同じtask/traceと各span/action、event ID、usage NULLを再取得して照合した。

結果は`attention-required`。Polarisの過去4回、PostgreSQLの過去3回のrestartを警告し、
現在の停止とは断定しない。承認済みのroot Flux停止はinformationalへ分離した。
failed/pending/unreadyのfixture、namespace逸脱、監査の不一致など11 focused testsが成功。
初回は検証側がLoki actionの`mcp.` prefixを欠いて不一致となり、修正後の再試験が成功した。
失敗記録は保持している。

成功証跡は`tmp/local-experiment/bears/61faa9b3a8a44ea09fb082365d023d23/result.json`。
再実行は`rtk make local-sre-smoke`。詳細はrepositoryの
`tools/local_experiment/bears/README.md`を参照。

## 2026-09-16: MCP・Pawprint・LangGraphのローカル連携

**WP-L2-AGENT-001 / WP-L2-OBS-001**のローカル機能として、公式Python MCP SDK
2.2.0のstdio Gatewayを実装した。8つの固定読み取りtool（doctor、Pod、Event、dbt models、
固定fixture EXPLAIN、固定lineage、直近Pawprint、policy）だけを公開する。
任意shell/SQL/URL/path、別namespace、GitHub書き込みは受理しない。
Kubernetes context名だけでなく、実際のloopback endpointも要求する。

実機11呼び出し（8成功・3 policy拒否）すべてで、OpenTelemetry SDK 1.44.0のspan、
Loki log、ClickHouse Pawprintのtask/trace/span相関を確認した。
いずれかの監査保存に失敗した場合は成功を返さず、backend dataを返却しない。
自由入力・prompt・credential・生の例外は監査payloadへ保存しない。
最終再実行は`tmp/local-experiment/gateway/smoke-39827f89c41543889d2e5bf2d3ca7624.json`。
Gateway 12、metadata 13、Loki 7 focused testsが成功した。

**WP-L2-LLM-001**はLangGraph 1.2.11、provider設定schema、cloud無効化境界、
一時的なloopback mock HTTP endpointを実装した。2回の`plan → MCP → summarize`が成功し、
8件のAgentHouse eventと親子traceの相関を確認した。Colimaから`host.lima.internal`を
経由したmacOS loopback mock接続も成功した。13 focused testsが成功した。
最終実測は`tmp/local-experiment/llm/d9fedb72a0894873acd24196cdda2772/summary.json`。

これは接続と制御フローの検証であり、実LLM推論・モデル品質・診断能力の証明ではない。
summarizeへ渡す入力はtool成功状態のみ。token/costは未知としてNULLに保持する。
model weightsは取得せず、cloud APIは一度も呼び出していない。
Cloud側はprovider/model・APIキーの環境変数名・明示予算の判断待ち。
ローカル開発の包括承認へ追加課金を含めていない。

```sh
rtk make local-platform-status
rtk make local-gateway-smoke
rtk make local-llm-smoke
rtk make verify-local-experiment
```

`local-platform-status`はLakehouse、Superset、metadata、AgentHouse、Lokiの状態を
一括確認し、不健全・欠落・異なるruntimeで非ゼロ終了する。機能検証は各smokeと分ける。
詳細と復元はrepositoryの`tools/local_experiment/{gateway,llm}/README.md`を参照。
metadataのprivate設定が消失しても、保持volume/containerがあれば秘密情報を再生成しない。

## 2026-09-16: BI・metadata・AgentHouseの実機導入

ADR-0034のローカル適用経路で、以下の機能を検証した。GitHubでの公開・
Flux admission・本番受入の完了は意味しない。

- **WP-L1-BI-001**: Superset 6.1.0のnative ARM64 imageに固定Trino driverを追加。
  既存Iceberg fixtureの2行を取得し、dataset/chart/dashboardを保存した。
  container再起動・再作成後も同じ保存物とquery結果が残った。
  SQLite online backupと対応するprivate設定をexportし、integrity checkが成功した。
  `127.0.0.1:18088`のみ、2 CPU / 3 GiB、Colima VM host networkからTrinoへ接続する。
- **WP-L1-META-001**: OpenMetadata 1.12.6 / PostgreSQL 1.12.6 / Elasticsearch 9.3.0の
  native ARM64を固定。実fixtureとhistorical dbt modelsの3 dataset、schema、team owner、
  Dagster pipelineに紐づく2 native OpenLineage edgesをAPI保存・再取得した。
  実際のdbt実行時刻とDagster run IDを使う4件のSTART/COMPLETE eventを送信する。
  PostgreSQL backupを新規scratch DBへ復元し、3 table entitiesを確認してscratchだけ削除した。
  serverは`127.0.0.1:18585`のみ、DB/searchは非公開。
- **WP-L2-CH-001**: ClickHouse 26.8.5.13のnative ARM64、2 CPU / 4 GiB、
  `127.0.0.1:18123`のみ。Pawprint schema、insert/query/export、同じevent IDの
  論理的重複排除と再起動後の保持が成功した。未知のtoken/costはNULLとする。
- Loki 3.7.0のnative ARM64を1 CPU / 1 GiB、`127.0.0.1:13100`のみで起動した。
  正規化したtask/trace/span/action/statusのpush/queryと再起動後の保持を確認した。
  MCP連携・LLM workflowの実測結果は後続記録へ分ける。

SupersetはSQLiteの単一process profile。OpenMetadataのdbt model entitiesは
cleanup済みの過去実行を表すことを明記し、artifactにない型はUNKNOWNのままにする。
Elasticsearchのoptional vector index警告は残るが、通常のmetadata/lineage APIは成功した。
保持データ・volume・backupの削除は行わず、生成された秘密情報はGit管理外に保存する。

```sh
rtk make local-superset-start local-superset-smoke
rtk make local-metadata-start local-metadata-ingest
rtk make local-agenthouse-start local-agenthouse-smoke
rtk make local-loki-start
rtk make test-local-platform
```

再起動・export・復元・制限の詳細はrepositoryの
`tools/local_experiment/{superset,metadata,agenthouse,loki}/README.md`を参照。
Supersetの保存dashboardは`/superset/dashboard/shirokuma-local-lakehouse/`。
証跡はGit管理外の`tmp/local-superset/`と`tmp/local-experiment/{metadata,agenthouse,loki}/`。
Metadata最終実測は`ingestion-20260916T093504Z.json`、復元記録は
`backup-restore-verification.json`。focused testsはBI 11、metadata 8、AgentHouse 9、Loki 5件。

## 2026-09-16: Dagster / dbtによるローカルDataOps

**WP-L1-DATAOPS-001のローカル機能検証**を実装した。既存のIceberg fixtureを
dbt staging tableへ読み込み、martでnameを大文字に変換する。Dagster assetが
`dbt build`と`dbt test`を実行し、同じ入力で再実行できる。

2026-09-16 05:03–05:05 UTC（14:03–14:05 JST）の実測では、通常・再実行・retryの
3回が成功し、それぞれ2 models + 8 data tests、追加の`dbt test` 8件が通過した。
martの結果は毎回`[[1,"SHIROKUMA"],[2,"POLARIS"]]`。
意図的に期待行数を3へ変えた試行は、指定したrow-count testの1件だけが失敗し、
Dagsterも失敗として記録した。期待行数を2へ戻したretryは成功した。
保持fixtureの前後の値は一致し、今回生成した2 tableを所有コメント確認後に削除した。

```sh
rtk make local-dataops-smoke
rtk make local-dataops-recovery-smoke
rtk make local-lakehouse-smoke
rtk make local-doctor
```

コマンドは固定contextを確認し、空きポートにloopback限定tunnelを作成して終了時に閉じる。
dbt profileとDagster instanceはGit管理外の実行ディレクトリ内だけに生成する。
実験namespaceは既存`iceberg.local_experiment`を使用し、credentialや権限は追加していない。
run UUID付きの2 tableだけが再作成・削除対象で、既存データは対象にしない。

中断・timeoutではローカルdbt processを回収後、run UUIDごとに分離したTrino userの
実行中queryを停止し、終了を確認してからcleanupする。確認できない場合は
`cleanup.status=deferred`としてtableを保持し、成功扱いにしない。
実機でもRUNNING query `20260916_051124_00143_ftq95`の限定停止とactive query 0件を確認した。
想定したdbt assertion失敗はexit code 1だけを受理し、timeoutや強制終了とは区別する。

### 依存・環境・診断

- Python 3.12.12 / macOS ARM64、Dagster 1.13.22、dbt-core 1.12.5、dbt-trino 1.10.3。
- `uv.lock`の96 package entries / 395 archive hashesを固定し、91 installed packagesの
  依存整合性を検証した。追加の常駐Pod・scheduler・公開endpointはない。
- `.venv`は約295MB、初回成功までの実行証跡は約11MB。生成tableは計4行で、実験後に削除。
  backup対象の追加はなく、fixture・versioned models・lockfileから再現できる。
- `doctor --profile local-experiment`は4 workloadの現在世代・replica・rollout失敗を確認する。
  親Flux停止だけをinformationalとして表示し、他の不健全なresourceはdegradedを維持する。
  SQLと公開承認は未評価と明記し、旧default profileの動作は保持した。
- ローカル回帰61 tests（runner 18、SQL 6、Polaris 12、session 7、DataOps 18）、
  設計55 documentsと関連14 tests、`go test ./...`、`go vet ./...`、secret scanを検証した。

実行証跡は`tmp/local-experiment/dataops/5bc35888a9144c5090fbe726d924c33b/summary.json`、
各attemptのDagster run storage、dbt `run_results.json`とログに保存した。
初回はDagster作業directoryの作成漏れで失敗し、修正後に成功した。失敗記録も保持している。
中断対策後の最終再実行`edfd3bf2fe7b440eaea576e133621e14`でも4試行の期待結果が一致し、
query終了確認・実験table 2件の削除・保持fixture一致を確認した。
詳しい再実行・復旧手順はrepositoryの`tools/local_experiment/dataops/README.md`を参照。

この到達点はhost-localのDataOps機能検証であり、常駐Dagster UI/scheduler、
OpenMetadata/OpenLineage、10GB TPC-H、旧公開admissionの完了は含まない。
次の依存先はWP-L1-META-001、独立した候補はWP-L1-BENCH-001とWP-L1-BI-001。

## 2026-09-16: ローカル開発への移行とLakehouse SQL検証

適用方針は[ADR-0034](../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)。
この記録はローカル実験の実測結果であり、過去の公開承認・Issue完了・本番受入を変更しない。
作業ブランチは `local/autonomous-experiment`、ホストの作業checkoutは
`Shirokuma/worktree/local-autonomous`。従来のmain checkoutと未コミット作業は保全した。

### 確認した機能

- **WP-L1-LAKE-003**: 既存Colima/k3sを起動し、SeaweedFS・PostgreSQL・Polarisの
  readinessを確認。新しいIceberg smoke Jobで既存fixtureを再読込した。
  `created=false`、2行、snapshot `7141845066324476177`で、保持データの再作成はしていない。
- **WP-L1-QUERY-001**: Trino 483の単一coordinatorをARM64で起動。
  `SELECT id, name FROM iceberg.smoke.fixture_v1 ORDER BY id` は
  `[[1,"shirokuma"],[2,"polaris"]]`を返した。
- **ローカル適用経路**: `prepare → apply → resume`を既存catalogに対して実行。
  UID/resourceVersionで競合を検出し、変更したFlux停止状態だけを復元できた。
  親`flux-system`の停止は今回のローカルモード用に維持している。
- 新規の実験用namespace `local_experiment`でCREATE/SELECT/DROPが成功した。
  namespace限定`TABLE_WRITE_DATA`とcatalogのpurge設定を追加した。
  `smoke_4bc60afe11ad4123a4737e82ea41c7c5`の2行を読み戻し、同tableを削除した。
  初回の失敗時に作ったtableも削除し、`SHOW TABLES FROM iceberg.local_experiment`は0件。
  保持fixtureの権限とデータは変更していない。
  実測時刻は2026-09-16 04:40–04:41 UTC（13:40–13:41 JST）。

### ローカル成果物と範囲

- 入力は既存のupstream Trino 483 archive、SHA-256
  `4f3978428f26f36398c94b85a3e03b5301394919c8a4271b497b0fcd1698d0cb`。
  hardened source rebuildや公開署名済み成果物として扱わない。
- 実測runtimeは`docker://28.4.0`。同じColima Docker daemonでビルドし、
  runtimeで解決できる`RepoDigest`を使用した。containerd importはこのprofileでは不要。
- 実行imageは`shirokuma.local/trino@sha256:e169b9d87a2f41b62ffc9ff6b7b90187ed6f4f758503f409566e16a0a5966ed2`。
  `imagePullPolicy: Never`、ClusterIP、非root、読み取り専用root filesystem、
  CPU limit 4、memory limit 6Gi。ホスト公開用Ingress/NodePortは追加していない。
- 新規imageは約630MB。ローカル再利用用image archiveも同程度。
  既存PVC・バックアップ・Colima diskは削除していない。

### 再実行

コマンドは作業checkoutのrootから実行する。

```sh
rtk make verify-local-experiment
rtk make local-experiment-status
rtk make local-trino-build TRINO_ARCHIVE="$TRINO_ARCHIVE"
rtk make local-trino-render TRINO_IMAGE_DIGEST="$LOCAL_RUNTIME_DIGEST"
rtk proxy kubectl --context colima-mac-studio-solo -n shirokuma-dev \
  port-forward service/trino-local 18080:8080 --address 127.0.0.1
rtk make local-trino-smoke
rtk make local-trino-smoke-write
```

`local-trino-smoke-write`は一意の試験tableを作成し、その実行が作成に成功したtableだけを削除する。
初回のpurge設定は`tools/local_experiment/polaris_permissions.py`と
`bootstrap/trino/local-experiment/README.md`を参照する。
runtime適用の詳細と復元は`docs/LOCAL_EXPERIMENT_RUNNER.md`を参照する。

### 検証と未達事項

ローカル機能の証拠は、ホストのGit管理外の`local-autonomous-20260916` evidence directoryと
checkoutの`tmp/local-experiment/`に保存した。認証情報はcommit・ログへ出力しない。

- `make verify-local-experiment`: 36 tests passed（runner 18、SQL 6、Polaris権限12）。
- `make verify-design-context`: 54 documentsを検証し、関連14 testsもpass。
- `verify_polaris_runtime.py audit`: 既存のruntime activation contractを保持。
- 既存Polaris 198、Iceberg 126、Trino publisher 133、feasibility 52、security feasibility 12、
  UI、observability、Go、Kyverno、secret scanと差分の空白検査はpass。
- 旧Trino bootstrapの4 failuresと供給網の5 failures、期限切れTrivy例外は下記の別境界として残す。

Polaris設定変更は`currentEntityVersion=1`からversion 2へ更新した。
`polaris.config.drop-with-purge.enabled`は以前は未設定、現在は`"true"`。
設定はcatalog単位だが、追加したdata privilegeは`local_experiment`に限定した。
失敗時も元のエラーと試験table名を失わないよう、SQL smokeのcleanup報告を修正した。

従来の`make verify`は公開経路向けの判定を保持するため、ローカル実験全体の合格を表す
コマンドにはしない。期限切れのresident authorization・Trivy例外、および
公開未承認のTrino runtime/固定input inventoryの検査は引き続き失敗する。
過去の期限・検証結果を更新して成功扱いにはしていない。

ホストのHelm 4.2.3は旧テストが固定する4.2.2と異なるため、該当テストはchecksum検証済み
4.2.2をそのプロセスのPATHだけに指定して実行した。グローバル設定は変更していない。

`shirokuma doctor`のclusterはhealthyだが、意図的なFlux停止と旧policy判定により
全体はdegraded。Trino/Polaris/S3のデータ経路はSQL smokeと実機readinessを別に確認する。
保持fixtureの`REPORT_READ_METRICS`権限不足は警告として残るが、SELECT成功とは区別する。

### 復元

実験用TrinoのDeployment/Service/NetworkPolicyと生成ConfigMapだけを削除すれば
query engineを撤去できる。既存catalog・storage・PVCは削除しない。
Polarisの追加roleは`local_experiment`限定であり、撤去時はbindingを外して追加roleを削除し、
catalogのpurge設定を保存した以前の値へ戻す。
親Fluxの元の停止状態は`flux-before.json`に保存済み。GitHubのdesired stateへ戻す場合だけ
差分を確認して復元する。Colima全体のresetはこの手順に含めない。
