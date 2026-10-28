# 2 Database PoC 実行結果 — 2026-10-19

> 以下はfile ACLだけを使用した初回検証の履歴。後続のfile＋OPA検証では95/95が成功し、4件の巻き戻し拒否を確認した。[認可方式の追加検証結果](database-poc-authorization-report-2026-10-19.md)と、その後の[DB別S3資格情報の検証結果](database-poc-s3-report-2026-10-19.md)を参照する。最新の受入範囲とservice keyの制約は後者に記載した。

Phase 1の基盤構成と再現性を実機で確認した。最終の自動検証は **95項目中91成功・4不合格**。不合格はSALES / FINANCEのviewer / engineerによる公開MARTSの巻き戻しで、実際にSnapshotが変わった。したがって、**DB別RBACの受入は未完了、一般利用者への公開は不可**。

Phase 2以降の実装には進んでいない。既存テストデータの移行・保全も行っていない。

## 実施範囲

- checkout: `worktree/local-autonomous`、branch: `local/autonomous-experiment`
- 開始時HEAD: `c639dc585dd0acb27ff7a03fb959d2724d6187f2`
- Kubernetes context: `colima-mac-studio-solo`、namespace: `shirokuma-dev`
- Trino: 実SQL `SELECT version()` = `483`。既存secure overlayと同じローカルimage digestを使用。
- Polaris: 稼働image digestがリポジトリの1.6.0 pinと一致。
- 新規資源: `trino-database-poc` のDeployment / Service / ConfigMap / Secret / NetworkPolicy、以下のPolaris資源と合成データ。
- 製品ソース、既存`trino-local`、Workbench、Gatewayルート、control_storeは変更していない。新規コードは管理者用単発helperのみ。

| SQL Catalog | Polaris Catalog | 接続principal | 許可Storage prefix |
| - | - | - | - |
| `sales` | `db_poc_sales` | `poc_sales` | `s3://shirokuma-lakehouse/database-poc/sales` |
| `finance` | `db_poc_finance` | `poc_finance` | `s3://shirokuma-lakehouse/database-poc/finance` |

各DBに`raw / marts / sandbox / staging`を作成。Trinoの5 identityは`poc_admin`、`sales_engineer / sales_viewer`、`finance_engineer / finance_viewer`。各Catalogの接続principalを固定し、利用者ロールはTrinoで制約する。既存`shirokuma_l1`への別名接続ではない。

## 実機結果

| 検証 | 結果 | 確認した範囲 |
| - | - | - |
| 同名`raw.orders`の独立性 | 成功 | RESTの実Table UUIDが異なる。metadata / data fileの実パスが各DB prefix内にある |
| 片側更新 | 成功 | SALESだけへの追記前後でFINANCEのUUID・location・Snapshot・行内容が不変 |
| native 1 / 2 / 3部名 | 成功 | `X-Trino-Catalog / Schema`で`orders`、`raw.orders`、`sales.raw.orders`等を解決。DBごとに固有sentinelも確認 |
| 明示名とcontext | 成功 | adminがFINANCE contextから`sales.raw.orders`を読むとSALESを参照。4部名はSYNTAX_ERROR。SQL書換えなし |
| 通常SQLのDB別ACL | 成功 | 自DBの許可読書き、他DB拒否、viewer RAW/SANDBOX拒否、MARTS DML拒否、Schema管理拒否、偽装拒否、通常CALL拒否 |
| Table作成・削除 | 成功 | engineerのRAW/SANDBOX CREATE / DROP。Schema ownerを付与せずTable OWNERSHIPで実現 |
| metadata表示 | 成功 | SHOW一覧に加え、4 identityでinformation_schema.tables / columnsを確認。実在するSTAGING Tableは不可視、他DBのmetadata直接参照も拒否 |
| Polaris principal分離 | 成功 | 自Catalogのnamespace一覧・Table load成功、他Catalogの一覧・loadは403/非表示404で拒否 |
| 任意LOCATIONの越境 | 成功 | 自DB prefix指定のCREATEは成功し、他DB prefix指定はPolaris Forbiddenで拒否。双方向で実施 |
| 共有S3資格情報 | 制約を実証 | 同じapplication credentialで両DBのmetadata.jsonを直接GETでき、REST UUIDとの一致を確認。資格情報によるDB間隔離ではない |
| static再配備・再起動 | 成功 | 同じbootstrap / deployの再実行＋明示的Trino再起動後、RAW/MARTS計4 TableのUUID・location・Snapshot・行内容が不変。native参照・通常ACLも確認 |
| 空Catalogからの再初期化 | 成功 | PoC Table / Schema / Catalog Role割当 / Role / Catalogを除去し、2 Catalog・4 Schema・grantを再作成。再seed後のUUIDは新しくなった |
| 公開MARTSの巻き戻し拒否 | **不合格** | 両DBのviewer / engineerが`ALTER TABLE … EXECUTE rollback_to_snapshot`を実行でき、current Snapshotが変化 |

最終helper検証の91成功 / 4不合格には、独立して行った保存先・metadata・再配備等の追加試験を水増しして含めていない。終了コード1は、巻き戻し拒否の不合格を保持した結果である。

### UUIDの実測

| DB | 最初の`raw.orders` UUID | 空から再作成後のUUID |
| - | - | - |
| SALES | `9de740bb-aec4-459b-b806-5fd6cae43dd9` | `852eedda-962e-4ea1-8f55-377433145a12` |
| FINANCE | `f33c6d1e-578d-4a00-8e0e-0baba3dec452` | `3b31e683-a870-4370-9481-223656e0bf41` |

再起動では上表の最初のUUIDが維持された。空からの再作成ではSQL名を同じにしたままUUIDとTableディレクトリが新しくなった。既存データを引き継ぐ処理はない。

## 残課題と実装前の判断

### 1. Table procedureを含むエンジン側認可が必要

最終再初期化後にも以下の4件で、対象roleのSQLがFINISHEDとなりSnapshotが変化した。

| identity | Query ID | current Snapshotの変化 |
| - | - | - |
| sales_engineer | `20261019_041948_00047_6qw3e` | `6525499582959629949` → `3962387365607650186` |
| sales_viewer | `20261019_041950_00068_6qw3e` | `1673409240847811220` → `3962387365607650186` |
| finance_engineer | `20261019_041953_00106_6qw3e` | `2724934351215616311` → `6698633383187446868` |
| finance_viewer | `20261019_041954_00127_6qw3e` | `6639187919093462235` → `6698633383187446868` |

実在するcurrent Snapshotの親を指定し、操作前後をadminで照合した。無効なSnapshotや構文エラーを拒否成功と数えていない。

Trino 483のfile system access controlはTable procedure専用のチェックが空で、Icebergのrollback経路はSnapshot変更を実行する。この挙動は今回の実測と一致する。[483の認可実装](https://github.com/trinodb/trino/blob/483/lib/trino-plugin-toolkit/src/main/java/io/trino/plugin/base/security/FileBasedSystemAccessControl.java#L942)、[Iceberg rollback実装](https://github.com/trinodb/trino/blob/483/plugin/trino-iceberg/src/main/java/io/trino/plugin/iceberg/IcebergMetadata.java#L2139)

**次の限定作業は、Table procedureを拒否できるエンジン側認可方式を選び、同じ4件を再検証すること。** アプリ側のSQL検査だけでこの試験を代替しない。Phase 2の共通基盤やUI実装を、この問題が解決済みという前提で開始しない。

### 2. Storageの隔離範囲を確定する

Polaris principal / allowedLocationsとSQL Catalogは分離できた。一方、既存SeaweedFS application credentialはバケット全体の権限を持ち、両prefixを直接読める。今回の管理者限定PoCはこの共通資格情報を使用した。

信頼された接続プロセス内のCatalog分離でよいのか、接続資格情報の漏洩時にもDB間の拒否が必要なのかを、公開条件として明示する必要がある。後者ならDB別Storage資格情報または同等のStorage側制御を別途検証する。今回、共有S3設定や既存identityは変更していない。

### 3. 初期化・削除手順へ反映する事項

- 新規Catalogは`polaris.config.drop-with-purge.enabled=true`が必要。未指定時はCREATE後のDROPがPolaris Forbiddenで失敗した。PoCの2 Catalogだけで有効化し、最終helperは作成時指定・既存値検証に対応した。
- 空Catalogでも専用Catalog Roleが残ると削除できなかった。Role割当解除 → Role削除 → Catalog削除の順序で成功した。将来の管理操作で必要な順序として引き継ぐ。
- STAGINGにTableがあると`DROP SCHEMA`はACL判定前に`SCHEMA_NOT_EMPTY`を返した。これを権限拒否成功とは数えず、metadata試験後に一時Tableを除去し、最終の空STAGINGで再検証した。エラー経由の存在情報まで隠す要件は別途検討対象。

Viewの安定ID、並行セッションのquery ownership、UI / API / BI / AI / Jobs / WAP管理経路、利用者の操作受入は今回の検証対象外。Phase 1の直接Trino / Polaris試験を全経路の受入へ拡張解釈しない。

## 再現手順と証跡

通常の準備・接続・実行方法は[管理者ガイド](database-poc.md)、実行コードは[database_poc.py](../../tools/local_experiment/database_poc.py)を参照。

```sh
rtk proxy python3 tools/local_experiment/database_poc.py prepare
# localhostだけにPolarisのport-forwardを開始
rtk proxy python3 tools/local_experiment/database_poc.py bootstrap
rtk proxy python3 tools/local_experiment/database_poc.py deploy
# localhostだけにPoC TrinoのTLS port-forwardを開始
rtk proxy python3 tools/local_experiment/database_poc.py seed --table orders
rtk proxy python3 tools/local_experiment/database_poc.py verify --table orders
```

既存private stateに対する`prepare`の再実行は不要。静的設定の同一再配備後、`kubectl --context colima-mac-studio-solo -n shirokuma-dev rollout restart deployment/trino-database-poc`で明示的に再起動した。port-forwardは新Podへ張り直した。

空からの再作成は、PoCのTableをDROPし4 SchemaをDROP、PoC Trinoをreplicas=0にしてから、Polaris管理APIで各DBの次の順序を実行した。認証は既存root Secretをメモリ内で読み取り、値を記録していない。

1. `DELETE /api/management/v1/principal-roles/poc_<db>_principal/catalog-roles/db_poc_<db>/poc_<db>_content`
2. `DELETE /api/management/v1/catalogs/db_poc_<db>/catalog-roles/poc_<db>_content`
3. `DELETE /api/management/v1/catalogs/db_poc_<db>`
4. Catalog不存在を確認し、`bootstrap → deploy → seed → verify`を再実行。

`<db>`は`sales`または`finance`だけ。machine principal / principal roleとその資格情報は再利用し、Catalog / Catalog Role / grant / Schema / Tableを再作成した。製品の汎用削除・再試行機構は実装していない。

ローカル証跡はGit対象外の`var/shirokuma/database-poc/`に保存した。ディレクトリ0700、各ファイル0600。資格情報と生成Secretは公開しない。以下は資格情報を含まない検証結果ファイル。

| 証跡 | 内容 |
| - | - |
| `verification-before-opa.json` | 初回95項目、query ID / identity / context / SQL、rollback前後Snapshot。OPA追加前に旧`verification.json`をこの名前へ保存 |
| `queries.json` | helperが記録したSQL receipt。実行順・終端状態・未知の結果を区別 |
| `phase1-initial.json` | 初期UUID / location / data file数、片側更新の独立性 |
| `location-boundary.json` | 自prefix作成成功・相手prefix拒否とPolaris原因 |
| `shared-s3-boundary.json` | 共通credentialで両metadataを直接GETした結果 |
| `metadata-visibility.json` | tables / columnsにおける4 identityの可視範囲 |
| `before-redeploy.json`, `redeploy-result.json` | static再配備・再起動前後の同一性 |
| `reset-removed.json`, `reinitialization-result.json` | PoCの削除対象と空からの再作成・新UUID |

## 終了時の状態

PoC用Deploymentはreplicas=0、作成したlocalhost port-forwardはすべて終了。PoC用Catalog、合成データ、専用Secret、private stateと再現手順は再検証用に残した。外部公開、GitHub操作、commit、image build / pullは行っていない。

`make verify-local-experiment`と`make verify-design-context`は成功。helperは構文検証と認可判定・POST一回・UNKNOWN判定を含むオフライン検証を実施した。これらと上記の実エンジン試験は別の証拠として扱う。

開始前・終了後ともdoctorはcluster / policyがhealthy、Fluxの既存suspended設定により全体degradedで同じだった。終了後、既存Polaris / TrinoはReady 1/1、PoC Trinoはdesired / readyとも0を確認した。既存TrinoのPod内`/v1/info`もversion=483 / starting=falseを確認した。既存Workbenchの機能一式を再受入したという意味ではない。
