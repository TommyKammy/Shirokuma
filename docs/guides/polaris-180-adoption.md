# Polaris 1.8.0 の採用と恒久構成

2026-10-19、所有者の指示により Apache Polaris 1.8.0 の採用を確定した。
対象は専用ローカル環境 `colima-mac-studio-solo` の基盤設定と切替である。
既存テストデータの移行・保全は行わない。Database の製品モデル、Workbench、
後続Phaseの機能はこの変更に含めない。

## 構成上の決定

| binding | SQL Catalog | Polaris Service / realm / Catalog | S3 prefix |
| --- | --- | --- | --- |
| SALES | `iceberg` | `polaris` / `SALES` / `shirokuma_l1` | `databases/sales/` |
| FINANCE | `finance` | `polaris-finance` / `FINANCE` / `finance` | `databases/finance/` |

既存機能との互換性のため SALES の正規 SQL 名は `iceberg` を維持する。
同一Catalogへの `sales` alias は作らない。FINANCEは現時点では管理者の明示SQL用で、
既存 engineer / viewer には許可しない。

Polaris 1.8.0でも、STSを使わない名前付きS3資格情報だけでは内部FileIOの
ambient資格情報依存を除去できない。DBごとにPolarisプロセスとS3キーを分離する。
各プロセスは自DBのprefixだけにアクセスできる。Trinoは両Catalogを扱うため
両DBのキーを持つ。これはSQL認可とは別の信頼境界として残る。

PostgreSQLは共用し、1.8.0用の `polaris_schema` とrealmでメタデータを区分する。
rootはadmin bootstrapとoperatorによる権限設定に限定する。TrinoとIceberg bootstrapは
DB別の専用principalを使う。bootstrapの書込み範囲は事前作成した `smoke` namespaceとする。
Trinoの専用roleは既存のschema・table・view操作に必要なCatalog内権限を持ち、
利用者単位の制限はfile ACLとOPAの両方で判定する。

## 署名鍵

realmごとに異なるRSA鍵を生成し、OpenTofu管理の専用Secretへ保存する。
秘密鍵は暗号化なしPKCS#8 PEM、公開鍵はX.509 SubjectPublicKeyInfo PEMを使用する。
各Polaris Podに自realmの鍵だけをread-only mountする。鍵そのものを環境変数や
GitOpsのConfigMapへ埋め込まない。

通常の再起動では同じ鍵を再利用する。鍵の更新時は対応するJWT世代を進め、
そのPolaris Podを再作成する。Polarisは鍵をメモリに保持するためSecret更新だけでは
反映されない。単一鍵ペア方式なので、署名鍵交換は旧Bearerの失効とtoken再取得を伴う。
client secretのローテーションとは別の操作として扱う。

## 供給元

| 用途 | 公式image・linux/arm64 digest |
| --- | --- |
| Polaris server | `docker.io/apache/polaris@sha256:74e6416c75f24563fc62f4ed52699d4fb872741b6a3e0af77fdc551b4ee72fce` |
| Polaris admin | `docker.io/apache/polaris-admin-tool@sha256:9f85ebe1fdfa9eb1214c4d5d343de29f76fef3dda7b4eb11c6767acc7e6c4fa4` |
| Trino 483 | `docker.io/trinodb/trino@sha256:aa18e61b2e7776ab8641ba8baaa8687d0430894e88c639e61010cc46a994ab36` |
| OPA 1.20.2 | `docker.io/openpolicyagent/opa@sha256:2ce2bacc87ddc25a475d71b67d422d206c20cf85b6ed00d81d520a5a039e22ce` |

Trinoのfull公式配布にはpassword-authenticatorsとOPA pluginが同梱されている。
独自pluginビルドや出所不明のローカルimageからのコピーは不要。
Polaris独自のOPA authorizerとTrinoのOPA access-control pluginは別物であり、
この構成では後者を使う。OPAサーバーはTrinoと同じPodのloopbackでのみ待受ける。

## 初回構築と更新

1. 専用環境のFluxと `flux-system` namespaceが存在することを確認する。
   root Kustomization `flux-system` をsuspendし、切替中のchildもsuspendする。
   DB別S3キー・root secret・固定署名鍵と、共用PostgreSQL passwordをprivate入力として用意する。
   SeaweedFS operatorキーと `local_gitops_s3_credentials` の専用Read/Listキーも用意する。
   consumerのclient id / secretは初回未設定にする。
2. OpenTofuでSecretを作成し、SeaweedFSのDB別prefix制限を反映する。
   新規環境ではoperatorが先にSeaweedFSの基盤manifestを直接applyして起動し、
   operator資格情報で `shirokuma-lakehouse` と `shirokuma-gitops` bucketの存在を確認し、
   欠けたbucketだけを作成する。snapshot helper自身はbucketを作成しない。
   この後にローカルsourceのsnapshot / activate --suspendを行い、childを依存順にresumeする。
3. PostgreSQL schemaと両realmをadmin bootstrapで作成し、両PolarisのReadyを待つ。
4. operatorがCatalog・namespace・専用principal・roleを作成する。
   発行された資格情報をprivate出力からOpenTofu入力へ反映し、consumer Secretを適用する。
5. 両PolarisがReadyであることを確認してからIceberg bootstrapとTrinoを起動する。
   TrinoのTLS・password Secretも[secure overlayの手順](../../deploy/local-experiment/trino/secure/README.md)に従って準備する。
   TrinoとOPAは `imagePullPolicy: Never` のため、起動前に下記の公式imageをノードへ配置する。

専用ColimaはDocker runtimeを使用する。新しいノードでは次を実行して存在を確認する。
Polaris server/adminは `IfNotPresent` であり、この手動preload必須条件とは異なる。

```sh
rtk proxy docker --context colima-mac-studio-solo pull docker.io/trinodb/trino@sha256:aa18e61b2e7776ab8641ba8baaa8687d0430894e88c639e61010cc46a994ab36
rtk proxy docker --context colima-mac-studio-solo pull docker.io/openpolicyagent/opa@sha256:2ce2bacc87ddc25a475d71b67d422d206c20cf85b6ed00d81d520a5a039e22ce
rtk proxy docker --context colima-mac-studio-solo image inspect --format '{{.Os}}/{{.Architecture}}' docker.io/trinodb/trino@sha256:aa18e61b2e7776ab8641ba8baaa8687d0430894e88c639e61010cc46a994ab36
rtk proxy docker --context colima-mac-studio-solo image inspect --format '{{.Os}}/{{.Architecture}}' docker.io/openpolicyagent/opa@sha256:2ce2bacc87ddc25a475d71b67d422d206c20cf85b6ed00d81d520a5a039e22ce
```

両方のinspect結果が `linux/arm64` であることを確認する。

入力、plan、stateには秘密値が含まれる。Git対象外のprivate directoryを0700、
各ファイルを0600とし、planの秘密値やSecretの内容をログへ出さない。
今回の有効なlocal stateは `var/shirokuma/polaris-adoption/terraform.tfstate` に置く。
旧main checkoutのstateから初回に複写したもので、旧stateで並行applyしない。
`tofu plan` と保存planへの `tofu apply` の両方で同じ絶対pathの `-state` を明示する。
`-chdir` 指定時も、入力・plan・stateの絶対pathを使って保存場所の取り違えを避ける。

外部Gitへのpushは行わない。ローカルGitOpsの供給方法と更新コマンドは
[local-gitops-source.md](local-gitops-source.md)に定義する。
専用 `shirokuma-gitops` bucketの読取り資格情報はDBキーと分離し、source-controllerに
同bucketのRead/Listだけを許可する。operatorがローカルsnapshotを更新する。

## ローテーション方針

- 現行の恒久S3入力はDBごとに1組のキーを管理する。更新時は対象Polaris・Trino・
  bootstrapを停止し、該当reconcilerをsuspendしてからDBのキーをOpenTofuで変更する。
  S3世代入力とSeaweedFS template annotationを同じ値へ進める（現在は3）。
  SeaweedFSを再起動し、対象PolarisのReady、新bootstrap Job、Trinoの順に再起動・再実行する。
  旧キーの拒否と新キーの読書き・purgeを確認してreconcileを再開する。
  SeaweedFSとTrinoが共用なので停止時間を伴う。前段PoCで検証した新旧S3キーの併存を、
  この単一キー入力で自動実行できるとは扱わない。
- consumer OAuthは新principalへ同じ限定roleを付与し、consumer切替後に旧principalのroleを撤回する。
  既発行の旧Bearerが拒否されることを確認してから旧principalを削除する。
- native client-secret rotateだけで既発行Bearerが即時失効するとは扱わない。
- JWT署名鍵は独立した更新とし、旧token拒否・新token成功・他realm無影響を確認する。

OAuthのblue/green切替では限定roleを共有し、旧principalからそのroleのmembershipだけを
撤回する。role自体のCatalog grantを撤回すると新principalも影響を受ける。
consumer切替の正確認後、private provision stateの `principal_name` と資格情報を
atomicに更新してから旧membershipを撤回・旧principalを削除し、provisionを再実行する。
世代名は従来名の `__g2` などとし、role名は従来のまま維持する。
rotation中に古いstateでprovisionを並行実行しない。

JWT鍵はSecret更新だけでは切り替わらない。OpenTofu apply後、GitOpsの該当JWT世代を
反映してPolarisを再作成する。Trinoも再起動してcached tokenを再取得する。
今回の最終世代はSALESが2、FINANCEが1である。

前段の実機根拠は[1.8.0限定検証報告](database-service-180-poc-report-2026-10-19.md)を参照。

## 恒久切替の検証結果

2026-10-19 18:42:45 JST、専用環境の `doctor --profile local-experiment` は `healthy`。
Polaris両Deployment、Trino、admin bootstrap、両DBのIceberg bootstrapはReady / Complete。
ローカルBucket sourceと7つのchild Kustomizationは最新世代でReady、childのsuspendは解除済み。
root Kustomizationだけを意図的にsuspendし、外部Gitへのpushなしで恒久manifestを供給している。
最終OpenTofu planはexit 0で変更0件だった。

| 実機検証 | 結果 |
| --- | --- |
| SQL・利用者認可 | 28項目成功。両DBのtable / schema / view操作、engineerのSALES RAW書込み、FINANCE拒否、viewer / engineerのMARTS巻戻し拒否とhead不変、admin巻戻しを確認 |
| DB別S3キー | 14項目成功。自prefixの読書き・一覧と、相手prefixのGET / PUT / DELETE / LIST拒否、bucket root LIST拒否を両方向で確認 |
| 共用キー廃止・GitOpsキー | 3項目成功。旧共用applicationキーはInvalidAccessKeyId、GitOps readerはDB読取りとsource bucket書込みを拒否 |
| 固定JWT鍵・realm | 10項目成功。両DBで同じBearerがPod再起動後も有効、他realm token拒否、service principalのoperator操作拒否、bootstrapのRAW読取り拒否 |
| JWT鍵更新 | 4項目成功。SALES旧tokenは401、新token成功、FINANCE既発行tokenと新tokenは成功。最終鍵世代はSALES 2 / FINANCE 1 |
| OAuth blue/green | 12項目成功。SALES bootstrapを `__g2` に切替えJob成功。旧membership撤回後の旧Bearerは403、旧principal削除後のclient credentialsは401。provisionを2回再適用しても旧principalは復活しない |
| OPA障害 | 5項目成功。OPA container停止中はadmin / engineer / viewerともSQL拒否、復旧後成功 |
| position-delete purge | 4項目成功。両DBでposition-deleteを生成し、DROP後に各11 objectが0件へ減少。Polaris内部FileIOの削除を確認 |

Trino公式full image内の `trino-opa-483.jar` とpassword pluginを非rootで読めること、
公式Polaris 1.8.0 imageのJava / Iceberg依存で両bootstrapが動くことも確認した。
Fluxの環境変数置換でTrinoの `${ENV:...}` が壊れないよう、secure ConfigMapはsubstituteを無効化した。
consumerにはroot・旧共用S3・JWT鍵の参照がなく、JWT鍵は対応するPolarisだけにmountされている。

静的検証は `make verify-local-experiment`、`make verify-design-context`、OpenTofu validate / fmt、
Kustomize、OPA policy 11項目、関連Python / Go testsが成功した。
最終principal世代対応のfocused testsは14項目成功。独立レビューで判明した初回preloadと
ローカルsourceのbootstrap順序の手順不足も、この文書とlocal GitOps guideに反映した。

証跡はGit対象外の `var/shirokuma/polaris-adoption/` 内に保存する。
`acceptance-*.json`、`runtime-final.json`、`doctor-final.json` が結果であり、
同directoryの入力・state・plan・Bearerファイルには秘密値を含むため共有しない。
既存Polaris metadataはテスト用schemaを再作成し、旧データの移行は行っていない。

## この段階で残す範囲

恒久切替を妨げる未解決事項はない。S3のキー更新は上記の停止を伴うoperator手順とし、
2026-10-19に両DBの実機更新を30項目で確認した。無停止S3 rotationは実装していない。
Trino / SeaweedFS / PostgreSQLは共用で、DB別Polarisとprefix制限はこの単一ホスト構成の境界である。
続く[クリーン再構築試験](polaris-180-rebuild-and-s3-rotation-2026-10-19.md)では、独立した空の
Kubernetes / 新規volume / 空stateから同じ恒久構成を再現し、82項目が成功した。
対象はKubernetesから上位のデータ基盤であり、Colima VM / OSの再インストールは含めない。
Database製品モデル、DB×roleのidentity展開、動的provision、Workbenchなどの後続Phaseには進んでいない。

## Phase 1の完了判定

2026-10-19、Database計画のPhase 1を管理者限定の2 Database基盤PoCとして完了と判定した。
名前解決・同名Table分離は初回PoC、認可・資格情報の問題解消は追加試験、恒久再現性は上記の切替・再構築記録で確認している。
製品の本人→DB×role連携、全verbのcross-DB SQL拒否matrix、QueryContext / control_store / Operation / drainはPhase 2の必須受入に残る。
Phase 1完了時の恒久構成はSALES=`iceberg`、FINANCE=`finance`、固定3サービスユーザーであった。本人・DB別identityへの後続切替は下記P2-3を参照する。Phase 2のP2-4〜P2-6で新構成を生成・適用する際に、計画上の`sales` / `finance`へクリーン切替する。P2-1のregistry初期化だけでは既存DBの自動登録やruntime切替を行わない。
一般利用者への公開判定ではなく、Phase 2の範囲・受入を整理した段階である。

## Phase 2 P2-1の保存基盤

2026-10-19、P2-1の `tools/local_experiment/control_store.py` と `data_management.py` を追加した。
空の `jobs.sqlite3` にschema version 1を作り、Jobsの接続・bootstrapだけを共通化する。
Database / Schema / Bindingの不変IDとCAS revision、ObjectBindingのincarnation、および管理用snapshotの保存を扱う。
新規focused 16件、Jobs 168件（別途1件skip）、DR 24件、空の独立台帳と実Trinoの14項目が成功した。
`make verify-local-experiment` は616件成功・31件skip、`make verify-design-context` は14件と文書検査が成功した。
skipはopt-in実機試験と任意pyarrow依存であり、全runtime試験の実行済み判定にはしない。
独立レビューのblockerはなく、doctorはhealthy。証跡はGit対象外の `var/shirokuma/p2-1-acceptance/` に保存する。
P2-1のQueryContext / ResourceRefは保存用ID snapshotであり、認可・公開resolver・API・plan / apply・admission制御は未実装である。
新registryは空で、現Polaris / Trino / Secret / ACLの構成と固定3ユーザーは変更していない。
このP2-1時点ではP2-2以降へ進まず、Phase 2全体の受入を継続した。

## Phase 2 P2-2の本人・DB別認可

2026-10-19、新v2認可APIとDB×role資格情報・file ACL / OPA生成を実装し、専用Trinoで限定受入を完了した。
元474項目は473成功・1失敗で、client capability不足だった1件は補正した単独追試で拒否を確認。UNKNOWNは0件。
通常v1の3 roleも実SQL成功、全体632件成功・32件skip、文書14件成功、doctor healthy。一時リソースと試験Tableは撤去した。
PolarisのDB別machine principalは変更せず、通常入口のv2接続はP2-3、恒久構成世代への組込みはP2-4へ残す。
APIとpolicyの境界は[本人認証の説明](shared_http_authentication.md#database-authentication-foundation-p2-2)を参照する。

## Phase 2 P2-3の探索・SQL入口

通常Workbench / SQL endpoint / warehouse query CLIをv2本人・DB別認可へ接続した。
SALES=`iceberg` / FINANCE=`finance`と既存DB別Polarisを明示bindingし、通常Trinoのfile ACL / OPA / password databaseへ反映した。
通常local GitOps snapshotは同じv2 manifestから認可2ファイルをoverlayする。
これは固定2DBの限定接続であり、任意DBの構成生成・plan / apply・drainは実装していない。
受入結果とprocess内credential pinの制約は[P2-3検証記録](database-p2-3-exploration-sql-report-2026-10-19.md)を参照する。

## Phase 2 P2-4の構成生成

2026-10-20、DB別Polaris、Catalog / namespace / service principal定義、版付きSecret参照、S3 policy、Trino ACL / OPAを公開generationから生成する経路を追加した。
private adapterは明示された既存資格情報を同じ参照へ組み立て、bootstrap / fullを分ける。local GitOpsの新経路はoffline prepareに限定し、現P2-3 runtimeを切り替えない。
Catalog / service principalの実作成、管理Operation / apply / READY、任意3DBの実機反映は後続に残す。
検証結果と制約は[P2-4生成記録](database-p2-4-config-generation-report-2026-10-20.md)を参照する。

## 公式資料

- [Polaris 1.8.0 release](https://polaris.apache.org/downloads/1.8.0/)
- [RSA key loading and caching](https://github.com/apache/polaris/blob/apache-polaris-1.8.0/runtime/service/src/main/java/org/apache/polaris/service/auth/internal/broker/RSAKeyPairJWTBrokerFactory.java)
- [Supported PEM formats](https://github.com/apache/polaris/blob/apache-polaris-1.8.0/runtime/service/src/main/java/org/apache/polaris/service/auth/internal/broker/PemUtils.java)
- [Trino full distribution](https://github.com/trinodb/trino/blob/483/core/trino-server/src/main/provisio/trino.xml)
- [Trino core distribution](https://github.com/trinodb/trino/blob/483/core/trino-server-core/src/main/provisio/trino-core.xml)
- [Flux Bucket source](https://fluxcd.io/flux/components/source/buckets/)
