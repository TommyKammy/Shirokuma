# Phase 1 Polaris・既存サービスのDB別S3資格情報 — 2026-10-19

続く[公式Polaris 1.8.0の限定再試験](database-service-180-poc-report-2026-10-19.md)で、下記position-delete purgeの残課題を解消し、service principal・2realm認可・資格情報ローテーションを検証した。本書は1.6.0時点の記録として残す。

## 検討範囲

**Polaris 2プロセス・2realm、既存Trinoの2 Catalog、DB別bootstrap Jobにより、共有application S3キーを失効した状態でも両DBの読書きが成立した。** 直接S3、Polaris内部FileIO、既存サービスのDB別資格情報を実機で確認した。ただし、position deleteを含むテーブルの完全purgeは現行Polaris 1.6.0の不具合で失敗した。資格情報分離の成立と、Iceberg操作の全面的な受入は分けて扱う。

既存テストデータの移行・保全は行わず、新規検証用Catalogだけを使用した。恒久IaC・Workbench・Gateway・JobsのDBモデルなど後続Phaseは実装していない。

## Polaris 1.8.0でも名前付き設定だけでは解消しない

Polaris 1.8.0は2026-09-28に公開された。公式の1.8.0ソースを調べた結果、今回のSeaweedFS構成の`stsUnavailable: true`では、名前付き資格情報だけで内部FileIOの共有キー依存を解消できない。これはソースによる判定であり、1.8.0への更新・実機試験は行っていない。[公式リリース](https://polaris.apache.org/downloads/1.8.0/)

設定`polaris.storage.aws.<name>.access-key / secret-key`とCatalogの`storageConfigInfo.storageName`は存在する。ただし`RESOLVE_CREDENTIALS_BY_STORAGE_NAME`によるresolverはSTSのAssumeRole分岐内でのみ呼ばれ、非STS経路はendpoint / region等を返すだけでS3キーを追加しない。`DefaultFileIOFactory`はこの空のcredentialsを含む設定をS3FileIOへ渡すため、プロセス環境の資格情報への依存が残る。1.8.0のHTTP設定やマージ順の改善はこの点を変更しない。

- [1.8.0資格情報生成処理（commit固定）](https://github.com/apache/polaris/blob/821d2a2357ba338a32a5e20b9ca3a247ad15ed68/polaris-core/src/main/java/org/apache/polaris/core/storage/aws/AwsCredentialsStorageIntegration.java#L211-L300)
- [1.8.0 FileIO生成処理（commit固定）](https://github.com/apache/polaris/blob/821d2a2357ba338a32a5e20b9ca3a247ad15ed68/runtime/service/src/main/java/org/apache/polaris/service/catalog/io/DefaultFileIOFactory.java#L57-L73)
- [1.6.0にも同じSTS内の制限](https://github.com/apache/polaris/blob/apache-polaris-1.6.0/polaris-core/src/main/java/org/apache/polaris/core/storage/aws/AwsCredentialsStorageIntegration.java#L209-L302)

STSを追加する案は別途成立性を調べる必要があり、今回の最小案には含めない。Catalog propertiesへ秘密値を置く方法も採用しない。

## 最小構成

現行1.6.0 imageを維持し、PolarisをDB別の2プロセス・2realmに分ける。各プロセスには自DB prefixだけを許可したS3キー1組と、自realmだけを受け付ける設定を渡す。PostgreSQLは共用し、realm別の新規metadataを使う。realmを分けることで、非同期purgeも受付requestのrealm内で処理する。

既存Trinoは一時的に2 CatalogとDB別キーを参照する。既存IcebergBootstrap.javaは既にCatalog・namespace・table・base-locationを引数化できるため、DB別one-shot Jobとして再利用する。既存共有applicationキーを検証中に失効させ、成功した操作が共有キーへfallbackしていないことを確かめる。

## 恒久設定への組込み方針

1. DB binding mapにCatalog / realm / prefix / Secret参照名を集め、OpenTofuのS3 identitiesとSecret、GitOpsのPolaris / Trino / bootstrap参照を同じ入力から生成する。秘密値は既存のsensitive input経路に置く。
2. region / endpointなど非秘密設定はobject-storage contract ConfigMapへ揃え、regionだけのために共有資格情報Secretを参照しない。
3. Secret変更とSeaweedFS・consumerの再起動を同じ設定世代で管理する。ローテーションは新キー導入、consumer切替、旧キー失効、拒否確認の順に行う。
4. bootstrapはDB別Jobとして維持する。共有キーを参照する停止済み旧Jobも恒久切替時に撤去・置換対象へ含める。
5. 現在のJobs / Stages / Alerts / Marts / dbtはTrino経由なので、S3キーの直接注入機構を新設しない。現行の稼働構成にSparkはない。

2プロセスはS3キーとrealmの分離であり、共用PostgreSQLの管理権限や、両キーを保持するTrinoプロセス侵害からの隔離を保証しない。operatorの管理キーも別の管理境界として残す。

## 実機構成

- checkout: `worktree/local-autonomous` / `local/autonomous-experiment`、context: `colima-mac-studio-solo`。
- Polaris: 現行1.6.0、image `ghcr.io/tommykammy/shirokuma-polaris@sha256:db403e2db7afbe4e8a62261500e229f6d796a420e814564b49f3e14217fd6c9e`を再利用。1.8.0のimageは導入していない。
- 各Polarisに自DBのS3キー1組だけを渡し、realmを`SALES_POC` / `FINANCE_POC`へ固定。PostgreSQL接続先・資格情報は既存のものを共用。
- Catalogは`service_poc_sales` / `service_poc_finance`。新規データは`database-poc/<db>/service-poc/`配下。前回PoCと同じDB別S3 identityを一時的に再導入し、権限は`database-poc/<db>/*`のRead / Write / Tagging / Listだけ。
- 元の共有キーPolarisは検証中replicas=0。既存`trino-local`のConfigMap・環境変数を一時的に2 Catalog / 2組へ置き換え、共有AWS pairと旧Polaris root参照を除去。TLS・password・SQL ACLは既存設定を維持。
- 既存`IcebergBootstrap.java`を変更せず、DB別のendpoint / realm / S3キーと4種類の`-Dshirokuma.*`設定を注入したJobで実行した。
- Trino・bootstrapのOAuthには、各検証realmのrootをTEST ONLYで使用した。own Catalogに`CATALOG_MANAGE_CONTENT / TABLE_READ_DATA / TABLE_WRITE_DATA`を付与。これは製品向けservice principalや利用者認可の完成形ではない。
- 共有application S3 identityをSeaweedFSから一時除去した。既知の存在するobjectへの旧キーGETが両DBとも`403 InvalidAccessKeyId`で失敗すること、稼働consumerの共有pair参照数が0であることを確認した。
- 一時変更の競合を避けるため、対象の子Flux `shirokuma-catalog` / `shirokuma-object-storage`だけを作業中suspendした。root Fluxの既存suspend設定は変更していない。

## 実機結果

| 検証 | 結果 |
| --- | --- |
| 直接S3 runner | 148/148成功。setup・状態比較・cleanupを含む。サービス再起動後にも148/148再成功 |
| Polaris内部FileIO・realm境界 | 16/16成功。両DBでmetadata生成と実object HEAD、load-table、API応答にstatic S3 keyがないこと、別realm headerの404、相手root secretの401、foreign prefixへのS3拒否、purge後のobject消失 |
| 既存Trino | 22/22成功。両DBでCREATE / INSERT / SELECT / UPDATE / DELETE / append / CTAS / DROP。実object消失の確認対象はCTASテーブル |
| 既存Java bootstrap | DB別2 Job成功。両方のfixtureを既存Trinoから読取。再起動後の2 Job再実行も成功し、`created=false`で再作成なし |
| SALESキーだけの失効 | 6/6成功。direct S3が403 InvalidAccessKeyId、SALES Polaris新規metadata作成・Trino INSERTが失敗。FINANCEのmetadata作成・Trino読取・purgeは成功 |
| サービス再起動・復旧 | 12/12成功。4テーブルのUUID / location / current Snapshot / 全行が一致。その後両DBのINSERT / DELETEとPolaris作成 / purgeが再成功 |
| position deleteを含むテーブルの最終purge | **両DBで失敗**。各DBにposition-delete Parquet 3個とDELETES manifest 3個が残存。下記の既存Polaris不具合を確認 |

Polarisのforeign prefix試験は、own realmのrootで**相手prefixをallowedLocationsに設定した一時Catalog**を作り、内部FileIOのS3書込みを実際に発生させた。応答にS3のAccessDeniedが含まれることと、owner資格情報で相手prefixが不変であることを確認した。Catalogのlocation検証だけで拒否した結果とは区別している。一時Catalogは撤去した。

SALES失効試験は片側の資格情報障害に対する切り分けを確認するためのもの。FINANCEキーだけを失効する対称試験は行っていない。SALESキーの復旧後、SeaweedFS・両Polaris・既存Trinoを再起動して再検証した。共有applicationキーは、この再検証完了まで失効したままだった。

初回のPolaris purgeは403となった。原因は新realmのrootに`TABLE_WRITE_DATA`がなかったことで、storage設定やS3権限の不足ではない。own Catalog内の明示grantを追加すると、purgeと実object削除が成功した。`DROP_TABLE_WITH_PURGE`は`TABLE_DROP`と`TABLE_WRITE_DATA`の両方を要求する。[公式認可定義](https://github.com/apache/polaris/blob/apache-polaris-1.6.0/polaris-core/src/main/java/org/apache/polaris/core/auth/RbacOperationSemantics.java#L310-L311)

今回のSQL試験はstorage互換性を対象に既存Trinoの管理ユーザーで行った。前回のfile＋OPAによる95ケースやviewer / engineerの利用者認可を、今回の2realm構成で再受入したという意味ではない。

## 恒久採用前に残る課題

最終cleanupで、UPDATE / DELETEを行った`raw.service_orders`のpurge後に、両DBで各6 objectが残った。Polarisログには`Cannot read a delete manifest with a ManifestReader`が記録され、S3のAccessDenied / InvalidAccessKeyIdは記録されていなかった。各DBキーで残存objectを削除でき、両prefixが空になることを確認したため、prefix権限不足とは区別できる。

1.6.0の`ManifestFileCleanupTaskHandler`はDELETES manifestにも`ManifestFiles.read`を使うが、Icebergはこの経路をDATA manifestに限定している。1.8.0の同処理にはDATA / DELETESの分岐と`readDeleteManifest`がある。**1.8.0の修正はソース上で確認しただけで、実機での解消は未検証。** 名前付き資格情報の非STS制限は、これとは別の問題として1.8.0にも残る。

- [Polaris 1.6.0のmanifest cleanup](https://github.com/apache/polaris/blob/apache-polaris-1.6.0/runtime/service/src/main/java/org/apache/polaris/service/task/ManifestFileCleanupTaskHandler.java#L83)
- [Iceberg 1.11.0のDATA manifest制約](https://github.com/apache/iceberg/blob/apache-iceberg-1.11.0/core/src/main/java/org/apache/iceberg/ManifestFiles.java#L162)
- [Polaris 1.8.0のDELETES分岐](https://github.com/apache/polaris/blob/821d2a2357ba338a32a5e20b9ca3a247ad15ed68/runtime/service/src/main/java/org/apache/polaris/service/task/ManifestFileCleanupTaskHandler.java#L125-L132)

次の限定検証では、1.8.0で同じDB別構成とposition-delete purgeを再試験する必要がある。恒久組込みには、製品向けservice principal、2realm構成での利用者認可再検証、Secretのローテーション手順も残る。今回これらの実装・更新には進んでいない。

## 再現に必要なもの

[pure manifest builder](../../tools/local_experiment/database_service_poc.py)は、既存Polaris / admin bootstrap / Iceberg bootstrap / Trinoのmanifestを入力として受け取り、DB別の一時resourceを生成する。ファイル読取・通信・起動処理は含まない。新規unit test 4件、独立静的レビュー、`make verify-local-experiment`、`make verify-design-context`は成功した。

1. 専用contextで元Deployment / ConfigMap / server Secretと対象Fluxの状態を私有領域へ記録する。DB別キーを前回のS3 helperで登録し、SeaweedFSへ反映する。
2. `build_database`で各DBのSecret / Polaris / Service / NetworkPolicy / realm bootstrap Jobを生成し、bootstrap Job用のPostgreSQL・own Polaris・S3・DNS通信も許可する。
3. 各realmでown Catalog / namespaces / content roleを作成する。`build_trino`とTrino用追加NetworkPolicyを適用し、旧共有キーPolarisを停止する。`build_iceberg_job`で既存Java JobをDBごとに実行する。
4. 共有application identityを失効して再起動後、旧キー拒否・稼働consumer参照を確認する。上記API / SQL / purge / 片側失効 / 再起動試験を実行する。通常の名前付き設定を追加するだけでは代用できない。
5. 生成テーブルをpurgeし、残存objectを確認する。1.6.0では上記不具合の残存物を各DBキーで明示的に回収した。prefixが空であることを確認し、Catalogのrole割当・role・Catalogも撤去してから元設定へ復旧する。Secretの元dataはbyte単位で戻し、現在のresourceVersionを使う。明示した一時resource一覧で撤去し、元Flux状態へ戻す。

bootstrapには既存JavaのソースConfigMapとadmin imageが必要。新image build / pull、外部公開、GitHub操作は行っていない。

## 検証後の状態

元のPolaris / Trino Deployment specとS3 server Secretのdataを復元した。両DeploymentとSeaweedFSはReady、元Polaris API・元Trinoの`SELECT 1`とCatalog一覧も正常だった。元の共有applicationキーの読書き復旧と、両DB検証キーの`403 InvalidAccessKeyId`を確認した。したがって、DB別分離を恒久環境へ適用済みという意味ではない。

一時Kubernetes resource 21件を明示名で撤去し、対象の子Flux 2件を元のunsuspendedへ戻した。検証用prefixとCatalogは空／削除済みで、port-forwardも終了した。共有PostgreSQL内の検証用2realmの管理metadataと失敗task記録は残るが、対応する稼働Polaris・Catalog・S3資格情報はない。既存root Fluxのsuspendは変更していない。

2026-10-19 16:41:55 JSTの`shirokuma doctor --profile local-experiment`は`healthy`。clusterと稼働Flux resourceは正常で、既存root Kustomizationのsuspendは維持されている。SQLはdoctorとは別に上記の疎通を確認した。

## 証跡

Git対象外の`var/shirokuma/database-service-poc/`に結果を保存した。ディレクトリ0700、ファイル0600。`baseline.json`・各credentials/rootファイル・生成resource JSON・server Secret記録は秘密を含むため公開しない。

| 結果ファイル | 内容 |
| --- | --- |
| `shared-key-disabled.json` | 既存キーの両prefix GETが403 InvalidAccessKeyId |
| `runtime-key-references.json` | 稼働consumerの共有pair参照数0、DB別Secret参照 |
| `s3-verification-before-restart.json`, `s3-verification.json` | 再起動前後の148チェック |
| `polaris-fileio.json` | Polaris 16チェック |
| `existing-trino.json` | 既存Trino 22チェック |
| `before-restart.json`, `bootstrap-repeat.json` | 4テーブルの同一性基準とJava Jobの初回・再実行 |
| `sales-key-revocation.json` | SALESキー失効時の6チェック |
| `after-restart.json` | 再起動後12チェック |
| `purge-initial-error.json` | 初回の権限不足。成功扱いには含めない |
| `purge-residual-objects.json`, `purge-task-errors.json` | 最終purgeで各DBに残った6 objectとdelete manifest例外 |
| `data-cleanup.json` | 各DBキーによる残存物の回収、prefix空、Catalog撤去 |
| `restoration.json` | 元設定・API / SQL・S3の復旧、検証キー失効、resource撤去等の13チェック |
| `doctor-after.json` | 復旧後のlocal-experiment doctorはhealthy |
