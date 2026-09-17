---
project: Shirokuma
doc_id: "WP-LOCAL-MILESTONES"
title: "Local Experiment Milestones"
status: active
created: 2026-09-16
updated: 2026-09-17
version: "1.4"
area: work-packages
tags: [shirokuma, local, evidence]
---

# Local Experiment Milestones

最新の到達点（2026-09-17）：既存MTPLX Qwenで実MCP inventory workflowが成功。
Cloud APIは無効のまま、ローカル実LLMを利用して開発を継続できる。
稼働停止していた専用Colimaを既存設定で復旧し、5 component groupsすべてreadyを確認した。
ローカル回帰185件・設計関連14件が成功し、設計66 documentsを検証した。

前回確認（2026-09-16 09:55 UTC）：ローカル回帰157件と設計関連14件が成功し、
repositoryの設計66 documentsを検証した。今回更新した12 materialized documentsは
Obsidian sourceと一致する。`local-platform-status`の5 component groupsはすべてready。
Pawprintをprivate JSONEachRowへexportし、OpenMetadataの追加backupも保存した。
Cloud APIは未実行。provider/model・認証環境変数名・追加予算は外部利用時の別判断として保持する。

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
