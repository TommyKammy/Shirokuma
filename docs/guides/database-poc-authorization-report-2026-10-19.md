# Phase 1 Table procedure認可の追加検証 — 2026-10-19

> 以下はOPA追加時点の結果。続く[DB別S3資格情報・prefix制限の検証](database-poc-s3-report-2026-10-19.md)で、専用キー間の直接S3アクセス境界を確認した。Polaris等の既存service keyとプロセス侵害に関する制約は残る。

**既存file ACLとOPAの併用により、SALES / FINANCEのviewer・engineerによるMARTSの巻き戻しを拒否できた。95項目すべて成功し、前回の4不合格を解消した。** 管理者による実際の巻き戻し、OPA停止・復旧、Pod再起動も確認した。

今回の受入は、合成データを使う管理者限定のローカルPoC。共有S3資格情報による直接アクセスは分離されておらず、一般利用者への公開や製品全体のRBAC受入を意味しない。Phase 2以降、Workbench、Gateway、control_store、既存`trino-local`の実装・設定には進んでいない。既存テストデータの移行・保全は行っていない。

## 選定した最小構成

| 選択肢 | 判断 |
| --- | --- |
| file ACLのみ | Trino 483の`checkCanExecuteTableProcedure`が実質的に許可するため、既存の通常procedureルールでは穴を閉じられない |
| Catalog全体をread-only化 | engineerに必要なraw / sandbox更新も止まる |
| Polarisだけで制約 | Catalog固定principalのためTrinoのviewer / engineerを識別できない |
| file＋OPA | 既存ACLを変更せずTable procedureの制約を追加できる。今回採用 |
| 独自Java ACL | 独自の認可コード・ビルドの保守を増やすため今回採用しない |

`access-control.config-files`にfileとOPAの2設定を指定する。Trinoは両方の許可を必要とするため、OPAが通常操作を通してもfile側のSQL権限は緩まない。[Trino 483の複数ACL仕様](https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/security/built-in-system-access-control.md)

- 既存file ACL、password認証、5 identity、Catalog別Polaris principalは不変。
- OPAは既知の5 identityの通常操作をfile ACLへ委ねる。未知identityは拒否。
- `ExecuteTableProcedure`は`poc_admin`かつ対象Catalogが`sales` / `finance`の場合だけ許可。
- viewer / engineerは全Schemaで全Table procedureを拒否する。raw / sandboxの`optimize`等も管理者担当とする最小運用で、procedure別の役割分担は追加しない。
- `opa.allow-permission-management-operations=true`により、権限管理は既存file ACLで引き続き判定する。OPAの独自grant管理は導入しない。
- OPA停止時のallow fallbackは設けない。request timeoutは5秒、connect timeoutは2秒。

483の要求形式は`action.operation=ExecuteTableProcedure`、対象は`resource.table`、procedure名は`resource.function.functionName`。大文字小文字やprocedure名の列挙に依存せず、非管理者のTable procedure全体を拒否する。[483 OPA実装](https://github.com/trinodb/trino/blob/483/plugin/trino-opa/src/main/java/io/trino/plugin/opa/OpaAccessControl.java#L618)、[設定](https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/security/opa-access-control.md)

## 実機構成と再現性

- checkout: `worktree/local-autonomous`、branch: `local/autonomous-experiment`、開始時HEAD: `c639dc585dd0acb27ff7a03fb959d2724d6187f2`。
- context: `colima-mac-studio-solo`、namespace: `shirokuma-dev`。専用Deploymentは`trino-database-poc`。
- Trinoの実SQL `SELECT version()`は`483`。OPAは既存ローカル1.20.2 image。
- 既存password版Trinoをベースに、既存ローカルTrino OPA imageの`/opt/trino/plugin/opa`をディレクトリごと変更せずコピー。新しいPoC専用imageを作成した。既存image/tagは置換していない。
- ビルドはローカルに存在する固定入力を使用し、`--pull=false --network=none`、Dockerfileは2つのFROMと1つのCOPYだけ。配布物のダウンロード、Javaソースビルドは行っていない。
- 旧OPA source imageの生成レシピは現checkoutにない。ローカル483 plugin packageの再利用として検証したもので、公式配布tarからの再構築や由来の再検証を完了したとは扱わない。製品採用時のimage生成・供給経路は別途確定する。

| 入出力 | ローカルimage reference |
| --- | --- |
| passwordベース | `shirokuma.local/trino@sha256:7347d14eb3a3017c351c0220c811d50b556e5c8596564d8b6195276600064670` |
| OPA pluginコピー元 | `shirokuma.local/trino-opa@sha256:420d88cf037c400e23ed42a319f0683d6753a525dc94d8f46bfdd57d86aacaa3` |
| 今回の派生image | `shirokuma.local/trino@sha256:f6390682c4b0ff0241ce55cfea095df8626ea8e122ced2255ca1045830df6317` |
| OPA sidecar | `shirokuma.local/opa@sha256:9bf48d2d88d954b562ee2090a9887814966707ec55f676f7c5127afb4f29918b` |

OPAは同一Podの`127.0.0.1:8182`だけにbindする。Serviceや外部portを追加せず、OPAには資格情報を渡さない。ポリシーは専用ConfigMapからread-only mountする。認可決定POSTとhealth GETだけをAPIで許可し、decision logは設定せず、通常request/response logも出さない。ConfigMapだけを変更した場合は明示的な専用Deploymentの再起動が必要で、ガイドに手順を残した。

再現用ファイルは[helper](../../tools/local_experiment/database_poc.py)、[Dockerfile](../../deploy/local-experiment/database-poc-opa/Dockerfile)、[guard](../../deploy/local-experiment/database-poc-opa/guard.rego)、[API制限](../../deploy/local-experiment/database-poc-opa/api.rego)、[ポリシーテスト](../../deploy/local-experiment/database-poc-opa/policy_test.rego)、[実行ガイド](database-poc.md)。

## 実機結果

既存の95ケースを変更せず再実行し、**95成功・不合格/UNKNOWN 0**。認証、なりすまし、DB境界、staging、MARTS DML、engineerのraw / sandbox CRUD・CREATE / DROP、Schema管理、直接Polarisアクセスの回帰も通過した。

4件とも存在する非currentの祖先Snapshotを指定した。拒否対象SQLの終端が`PERMISSION_DENIED`であり、前後Snapshotが一致することを確認した。

| identity | query ID | Snapshot前後（同一） |
| --- | --- | --- |
| sales_engineer | `20261019_044829_00023_et7sq` | `1632887276858387387` |
| sales_viewer | `20261019_044831_00044_et7sq` | `7242447560637821137` |
| finance_engineer | `20261019_044835_00082_et7sq` | `4976213269152138642` |
| finance_viewer | `20261019_044836_00103_et7sq` | `4487750129363064391` |

追加実機検証は次のとおり。

| 項目 | 観測結果 |
| --- | --- |
| adminのrollbackと明示復元（両DB） | 既知のparentへ変更し、行数13→12を確認。元のSnapshotへ戻し、行数13とSnapshot復元を確認 |
| 4 nonadminのMARTS `optimize` | 全件`PERMISSION_DENIED`、Snapshot不変 |
| OPAコンテナを一時pause | 4 nonadminの有効なrollbackが各約5.1秒で`QUERY_FAILED / GENERIC_INTERNAL_ERROR`。query ID付きの終端エラーを確認。独立したPolaris RESTで両Snapshot不変 |
| OPA停止中の普通のSQL | admin `SELECT 1`とviewerのMARTS SELECTも失敗。可用性への影響を確認 |
| OPA unpause後 | 参照成功、4 rollbackは再び`PERMISSION_DENIED`。復旧後もSnapshot不変 |
| OPA API | GET health=200。GET policies / data、PUT policiesの無効probe、POST queryは全て401。管理APIへ迂回できないことを確認 |
| Pod rollout restart | 2/2 Ready。両MARTSのUUID / location / Snapshot / 行数が一致。4 identityのSELECT成功・有効なrollback拒否を再確認 |

admin操作では、SALESは`7242447560637821137 → 1632887276858387387 → 7242447560637821137`、FINANCEは`4487750129363064391 → 4976213269152138642 → 4487750129363064391`を観測した。これは単なるno-op成功ではない。Trino 483は当該procedureでIceberg `setCurrentSnapshot`を使用する。[実装](https://github.com/trinodb/trino/blob/483/plugin/trino-iceberg/src/main/java/io/trino/plugin/iceberg/IcebergMetadata.java#L2139)

OPA停止時の結果は正常な認可拒否とは別に評価する。HTTP問い合わせの失敗はqueryを失敗させ、通常SQLも停止する。運用でfile-onlyへ自動切替してはならない。[HTTP失敗処理](https://github.com/trinodb/trino/blob/483/plugin/trino-opa/src/main/java/io/trino/plugin/opa/OpaHttpClient.java#L169)

## 共有S3資格情報の扱い

今回は共有`seaweedfs-s3-application-credentials`を継続する。**信頼する管理者だけが合成データを操作するPoC内部の資格情報**であり、viewer / engineerへ配布しない。localhost接続のみとし、一般公開しない。S3 identityやbucket policyの変更は行っていない。

現行資格情報はbucket全体のRead / List / Tagging / Writeを持つ。前回、同じ資格情報で両DBのmetadataを直接GETできたことを実測済みである。OPAはそのS3要求経路には介在しない。したがってSQL認可の改善を、物理ストレージ隔離の改善と表現しない。[現行S3設定](../../opentofu/dev/object-storage.tf)

SeaweedFS 4.39は`Action:bucket/prefix/*`の照合を実装し、LISTの指定prefixも認可へ渡す。prefix制限自体が不可能という判断はしない。ただし、このPoCでは独立資格情報での実機受入をしていない。[4.39公式ソース](https://github.com/seaweedfs/seaweedfs/blob/4.39/weed/s3api/auth_credentials.go)

一般利用に向けた次のストレージ検証候補は、DB別static identity＋prefix別Read / Write / Tagging / List。bucket全体のListは付けず、空prefix・親prefix・相手DB prefixの列挙/取得/更新拒否、HEAD bucket、multipart、Copy、複数DELETE、再起動後を確認する。互換性を優先する代案はDB別bucket＋bucket別資格情報。同じTrinoプロセスに両資格情報を置く構成では、プロセス侵害からの隔離までは保証しない。今回は候補整理までで実装していない。

## 証跡・残課題・終了状態

Git対象外の`var/shirokuma/database-poc/`に、次の資格情報を含まない結果を保存した。既存のprivate state・Secretをログや文書へ出していない。

| 証跡 | 内容 |
| --- | --- |
| `verification-before-opa.json` | 旧91/95の履歴 |
| `verification-before-s3.json` | OPA追加時の95/95。S3追加検証前に旧`verification.json`をこの名前へ保存 |
| `opa-admin-and-procedures.json` | adminの実変更・復元、4 optimize拒否 |
| `opa-outage.json` | 4 rollbackのOPA停止時エラー、復旧後拒否、Snapshot不変 |
| `opa-outage-availability.json` | OPA停止中の通常SQL失敗と復旧 |
| `opa-before-restart.json`, `opa-restart.json` | 再起動前後のtable同一性と認可維持 |

固定OPA imageによるポリシーテストは7/7。`make verify-local-experiment`、`make verify-design-context`も成功。構成・ポリシーの独立静的レビューにblockerはない。これらは実機結果とは別の検証として扱う。

Table procedureの認可方式はこのPoC範囲で決着した。残る受入条件は、共有S3を置換する方式の実機確認、製品採用時のimage生成経路、後続Phaseのアプリケーション認可・運用・利用者受入である。これらを完了したとは扱わず、後続Phaseへ着手しない。

検証後は専用Deploymentをreplicas=0へ戻し、作成したlocalhost forwardを全て閉じた。OPAのpauseは`finally`で解除し、復旧を確認してからPodを停止した。既存Trino・Polaris、共有S3設定は維持した。PoC資源と合成データは再検証用に残している。外部公開、GitHub操作、pushは行っていない。

終了時の既存Trino / PolarisはReady 1/1。doctorは開始前と同じくcluster / policyがhealthy、既存Flux Kustomizationのsuspendedにより全体degradedだった。18181 / 18443のlistenerがないこと、private stateが0700・全ファイル0600であることも確認した。
