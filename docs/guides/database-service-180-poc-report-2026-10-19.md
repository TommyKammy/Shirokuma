# Phase 1 Polaris 1.8.0 再試験 — 2026-10-19

## 結論

**公式Polaris 1.8.0のDB別2プロセス・2realm構成で、前回失敗したposition-delete付きテーブルの完全purgeが成功した。** 共有application S3キーを無効化し、rootをconsumerから外した状態で、既存Trino・Java bootstrap・file＋OPA認可を確認した。S3キーとOAuth principalの切替後にも再試験した。

これはPhase 1の限定PoCであり、恒久IaCや後続Phaseの実装ではない。既存テストデータの移行・保全は行っていない。[前回1.6.0の結果](database-service-poc-report-2026-10-19.md)とは試験時点を分けて扱う。

## 構成

- checkout: `worktree/local-autonomous` / `local/autonomous-experiment`、開始時HEAD `c639dc585dd0acb27ff7a03fb959d2724d6187f2`。contextは専用の`colima-mac-studio-solo`。
- 公式1.8.0 ARM64 server: `docker.io/apache/polaris@sha256:74e6416c75f24563fc62f4ed52699d4fb872741b6a3e0af77fdc551b4ee72fce`。
- 公式1.8.0 ARM64 admin: `docker.io/apache/polaris-admin-tool@sha256:9f85ebe1fdfa9eb1214c4d5d343de29f76fef3dda7b4eb11c6767acc7e6c4fa4`。上流バイナリの加工・ソースビルドはしていない。
- 既存PostgreSQLサーバー内に一時DB `database_service_180_poc`を作り、`polaris_schema`を作成して両realmを新規bootstrap。admin/serverのJDBC URLに`currentSchema=polaris_schema`を指定。1.8はschema v6を使用し、事前のschema作成が必要。既存1.6.0 DBは変更していない。[公式リリースノート](https://polaris.apache.org/downloads/1.8.0/)
- realmは`SALES_POC` / `FINANCE_POC`、主Catalogは`service_poc_sales` / `service_poc_finance`。各Polarisには自DB prefixだけを許可したS3キー1組を渡す。共有application S3 identityは検証中除去した。
- 既存`trino-local`はstorage互換性用、既存PoCのfile＋OPA Trinoは5利用者の認可用。Trino/OPA/Java bootstrapのimageと利用者認可規則は前回のものを再利用した。
- 非STS構成のFileIOにはDB別ambient keyを使用する。名前付きstatic credentialsだけでは非STS経路を解消できないという前回の1.8ソース調査結果を変更するものではなく、named-only設定の実機A/B試験は今回含めない。

## rootを使わないconsumer

各DBにTrino用とbootstrap用のprincipalを別々に作り、それぞれ1 principal roleとown Catalog内の1 catalog roleだけに所属させた。rootは管理者による初期設定・検証・cleanupに限定した。

| Consumer | 実機で成立したgrant |
| --- | --- |
| Trino | own Catalog scopeの`CATALOG_READ_PROPERTIES`、`NAMESPACE_LIST`、`NAMESPACE_READ_PROPERTIES`、`TABLE_FULL_METADATA`、`TABLE_READ_DATA`、`TABLE_WRITE_DATA`、`VIEW_LIST` |
| Java bootstrap | Catalog scopeの`CATALOG_READ_PROPERTIES`。既存`raw` namespace scopeの`NAMESPACE_LIST`、`NAMESPACE_READ_PROPERTIES`、`TABLE_CREATE`、`TABLE_LIST`、`TABLE_READ_PROPERTIES`、`TABLE_WRITE_PROPERTIES`、`TABLE_READ_DATA`、`TABLE_WRITE_DATA` |

Trino consumerにはCatalog・namespace・grant・principalの管理権限を与えない。bootstrapは既存Javaの`-Dshirokuma.namespace=raw`を使い、Catalogとnamespaceは先に管理者が作る。bootstrapのMARTS読取、raw tableのDROP、principal管理は拒否された。上表は実測で成立した権限集合であり、各権限の除去試験を網羅した最小性の証明ではない。

利用者別のviewer / engineer / admin認可はTrinoのfile＋OPAが担当する。PolarisにはDBごとのservice principalで接続するため、利用者の権限をPolarisへ直接伝播した結果ではない。またbootstrapのnamespace制限はREST境界であり、DB prefix全体を扱えるS3キーにrawだけの制限が加わるわけではない。

## 実機結果

| 検証 | 結果 |
| --- | --- |
| 2realmでの利用者認可 | 切替前95/95、旧資格情報失効後95/95。各回4人のviewer / engineerによる実在する旧snapshotへのMARTS巻き戻しを拒否し、head不変を確認 |
| 管理者の巻き戻し | 両DBで実際に旧snapshotへ移動し、SQLでheadを確認して元snapshotへ復元 |
| service / realm / bootstrap境界 | 22チェック成功。正確なrole/grant、管理操作拒否、相手realmでのtoken/Bearer拒否、bootstrap制限等を含む |
| 直接S3 | 切替前148/148、旧資格情報失効後148/148。相手DBの直接アクセス拒否とcleanupを含む |
| position-delete purge | 初回は各DB22 object → 0。切替・旧キー失効後は各DB17 → 0。各tableでposition-delete fileが実在することをSQLで確認し、手動object削除なし |
| 再起動後の同一性 | 8テーブルのUUID / location / current snapshot / 全行が一致 |
| Java bootstrap | 初回2 Jobは`created=true`。Ready確認後の切替再実行2 Jobと旧キー失効後の2 Jobは`created=false`で成功。起動との競合による別の2 Job失敗は下記に記載 |
| S3ローテーション | 両DBで新旧キー併存時の旧write / 新read、新キー切替、旧キーの既知object GETが403 InvalidAccessKeyId、新read / write / deleteを確認 |
| OAuthローテーション | native dual-secretの挙動を両DBで確認。実consumer 4 principalは新規principalへの切替、旧role剥奪、旧principal削除後の拒否を確認 |

各realmには相手Catalog名の**実在する無grant sentinel Catalogとtable**を置き、rootでload成功を確認した。これにより、95ケース内のcross-Catalog 404を単なる不存在で成功扱いしない。sentinelは自DB内の主Catalogと重ならないprefixを使う。別途、相手realmの実在tableを対象に相手endpointでのtoken発行・Bearerアクセス拒否も確認した。

1.8の`ManifestFileCleanupTaskHandler`はDATA / DELETESでreaderを分ける。前回1.6.0で残ったdelete manifestとposition-delete Parquetを、今回は自動で削除できた。最終cleanupの各DB5テーブルも自動purgeし、主prefixとsentinel prefixの残存objectは0だった。[1.8の修正箇所](https://github.com/apache/polaris/blob/821d2a2357ba338a32a5e20b9ca3a247ad15ed68/runtime/service/src/main/java/org/apache/polaris/service/task/ManifestFileCleanupTaskHandler.java#L125-L132)

## 検証で修正した条件

- 最初のservice roleは6権限だった。`DROP SCHEMA`の4拒否試験が期待した認可拒否ではなく`Failed to list views`で失敗したため、`VIEW_LIST`を追加した。追加後の95/95を成立結果として扱う。
- 新キー切替時にPolaris再起動とbootstrap Job起動を同時に行った2 Jobは`ConnectException`で失敗した。Polaris Readyを確認した後、別名Jobで明示的に再実行し成功した。以後は起動完了を待ってからone-shot Jobを発行する。
- 公式imageの既定file loggingは読み取り専用root filesystemと合わなかったため、`QUARKUS_LOG_FILE_ENABLED=false` / `QUARKUS_LOG_CONSOLE_ENABLED=true`にした。修正後のserverログには当該エラーがない。[公式既定設定](https://github.com/apache/polaris/blob/apache-polaris-1.8.0/runtime/defaults/src/main/resources/application.properties#L83)

## Secretローテーションの手順

1. S3 server側へDBごとの新キーを追加し、新旧が使える期間を設ける。新しいOAuth principalを作り、同じ限定roleへ所属させる。
2. consumer Secretを新世代へ切り替える。Pod環境変数はSecret更新だけでは変わらないため、設定世代のannotationを変えてPolaris / Trinoを再起動する。Polaris Ready後にbootstrap Jobを実行する。
3. 新principalのload、SQL、bootstrap、S3アクセスが成功するまで旧資格情報を残す。今回は8テーブルの同一性も確認した。
4. 旧principalのrole membershipを外し、直前までloadできた同じBearerでRESTが403になることを確認する。その後旧principalを削除し、旧client credentialsによるtoken発行が401になることを確認する。新principalのloadは継続して成功する。
5. S3 serverから旧キーを除去して反映・再起動し、既知objectへの旧キーGETの403と、新キーでの読書き・purge成功を確認する。

**native rotateだけを即時失効手順にはしない。** 1回目のrotateでは旧secretもtokenを発行でき、2回目で最初のsecretが401になった。一方、それ以前に発行したBearerは同じtableを引き続きloadできた。role剥奪後は同BearerのRESTが403になった。期限満了まで待った試験ではなく、Bearerの資格情報世代を通常の署名検証では照合しない点は公式仕様でも確認した。[公式テスト](https://github.com/apache/polaris/blob/821d2a2357ba338a32a5e20b9ca3a247ad15ed68/runtime/service/src/test/java/org/apache/polaris/service/auth/internal/broker/JWTBrokerCredentialsBindingTest.java#L45-L47)

今回の既定RSA署名鍵はプロセス内生成のため、Polaris再起動前のBearerは401となった。この401をprincipal失効の証拠には使わず、再起動後に旧secretで新tokenを取得し、正のload → role剥奪 → 拒否の順に検証した。恒久設定ではrealm別の署名鍵をSecretとして永続化するか、再起動で既発行tokenが無効になる運用とrefresh挙動を明示する必要がある。[署名鍵の生成と保持](https://github.com/apache/polaris/blob/apache-polaris-1.8.0/runtime/service/src/main/java/org/apache/polaris/service/auth/internal/broker/RSAKeyPairJWTBrokerFactory.java#L44-L87)

## 恒久組込みへの引継ぎ

今回の構成・権限・起動順を、DB binding mapから生成するOpenTofu / GitOps / bootstrap設定へ反映するのが次の作業になる。rootはoperator経路だけに置き、service principalとS3キーのSecret参照をDB別にする。SQL認可のfile＋OPAを保持し、設定世代・Ready待ち・旧資格情報の拒否確認を切替手順へ含める。

共用PostgreSQLの管理者権限、両DBキーを持つTrinoプロセス、operatorキーは別の信頼境界として残る。root / PostgreSQL管理資格情報自体のローテーション、無停止切替、HA、1.8接続中のOPA停止試験は今回の対象外。前回のOPA停止試験を今回の再試験結果とは扱わない。恒久設定やWorkbench等の後続Phaseにはまだ実装していない。

再利用したTrino OPA pluginの元image生成レシピは現checkoutにない。Polaris公式imageの確認とは別に、製品採用時のOPA plugin供給経路を確定する必要がある。[前回の再現性に関する留保](database-poc-authorization-report-2026-10-19.md#実機構成と再現性)

## 検証後の復旧

元のPolaris 1.6.0 / TrinoのDeployment spec、既存PoCの停止状態、ConfigMap・Secretを復元した。S3 server Secretのdataも元と一致し、元applicationキーの読書きと、検証用4キー（両DBの旧・新）の`403 InvalidAccessKeyId`を確認した。元Polaris APIと元Trinoの`SELECT 1` / Catalog一覧も正常だった。

一時Kubernetes resource 27件と、一時PostgreSQL DBを削除した。今回のrealm / principal / task metadataも一時DBとともに撤去済み。主prefixとsentinel prefixは空で、全port-forwardを終了した。子Flux 2件は元の状態へ戻し、既存root Fluxのsuspendは維持した。取得した公式imageは専用Colimaのローカルcacheに残る。

2026-10-19 17:31:06 JSTの`shirokuma doctor --profile local-experiment`は`healthy`。恒久環境のPolarisを1.8.0へ更新した状態ではない。

## 証跡とコード

Git対象外の`var/shirokuma/database-service-180-poc/`に保存した。ディレクトリ0700・ファイル0600。baseline、credentials、root、token、生成resourceを含むprivateファイルは公開しない。

| ファイル | 内容 |
| --- | --- |
| `runtime-version.json` | 公式image、実行1.8.0、最終serverログのエラー確認 |
| `authorization-before-rotation.json`, `authorization-after-rotation.json` | 95ケース×2とsnapshot不変 |
| `sentinel-preconditions.json`, `service-and-realm-boundaries.json` | 実在sentinel、22境界チェック、adminの実変更・復元 |
| `s3-before-rotation.json`, `s3-after-rotation.json`, `s3-old-key-revocation.json` | 148ケース×2と旧キー拒否 |
| `position-delete-purge.json`, `position-delete-purge-after-rotation.json` | 初回22→0、切替後17→0の両DB purge |
| `before-rotation-tables.json`, `after-rotation-tables.json` | 8テーブルの同一性 |
| `bootstrap-results.json`, `bootstrap-rollout-race.json` | 成功6 Jobと起動競合の失敗2 Job |
| `oauth-native-rotation.json`, `oauth-blue-green-rotation.json` | native挙動とconsumer 4 principalの切替・旧アクセス拒否 |
| `runtime-key-references.json`, `data-cleanup.json` | 稼働consumerの共有pair / root参照0、最終自動purge |
| `restoration.json`, `doctor-after.json` | 元設定・停止状態・API / SQL・キー失効・一時DB撤去・正常稼働の確認 |

[database_poc.py](../../tools/local_experiment/database_poc.py)の`verify`へ任意のDB別REST targetsを追加し、既存のデフォルト経路は保持した。[focused test](../../tests/test_database_poc_targets.py) 5件、構文検証、独立レビュー、`make verify-local-experiment`、`make verify-design-context`が成功した。
