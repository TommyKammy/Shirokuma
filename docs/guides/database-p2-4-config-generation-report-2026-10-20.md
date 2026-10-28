# Phase 2 P2-4: 固定構成の生成

**状態: P2-4の生成範囲を実装・限定受入済み（2026-10-20 JST）。** 対象は `local/autonomous-experiment` の固定構成生成です。既存テストデータの移行・保全は行っていません。管理Operation、drain、apply、READY更新、新Databaseの実機反映には進んでいません。

## 同一世代から生成する範囲

秘密値を含まないgeneration manifestを構成の正本にします。Database ID、SQL名、binding desired revision、DB専用Polaris service / realm / Catalog / prefix、Schemaとgrant revision、承認済み配置profile、版付きSecretRefを固定します。generation IDと、配送ファイル集合から求めるGitOps digestは別の識別子です。

生成対象は次のとおりです。

- DB別Polaris Deployment / Service / bootstrap設定と参照Secret、対応するPostgreSQL DB / realmの初期化定義。
- Catalog / 4 namespace / service principalの作成定義とTrino Catalog properties。namespaceは `raw / marts / sandbox / staging`、purposeと物理名が一致する固定profileだけを許可します。独自Schema名・purposeは拒否します。P1のsmoke Table作成fixtureは含めません。
- Trino base / secure両方のCatalog一覧、env、mount、NetworkPolicy、file ACL、OPA identity / Table procedure policy。
- DB別S3 identityのprefix制限、共通operator / GitOps identityを区別したserver policy、OpenTofu向けmetadata。
- 実在する生成resourceを指すFlux child / healthChecks。生成だけではCatalogやDatabaseをREADYにしません。

SecretRefはnamespace / 不変name / version / keysを含み、nameのversion suffixを必須にします。Secret値、password verifier、署名秘密鍵は公開manifest / GitOps source / control_storeへ入れません。公開identityからpolicyを生成し、private backend manifestを別正本としてACL / OPAを上書きしません。`password.db` は秘密値なのでpublic rendererで発行せず、明示private入力の既存verifierと同世代identityの完全一致を確認してprivate Secretへ組み立てます。

## Private入力と二段階の材料

private adapterは、公開参照と版・key集合が一致する明示的なprivate入力だけを読みます。既存のpassword / password.db / TLS / shared-secret、DB別S3 / OAuth / JWT材料を再利用し、資格情報・署名鍵・principalを新規発行しません。`full` の出力は所有者限定のprivate ToFu入力とruntime auth、`bootstrap` はprivate ToFu入力と未供給参照の一覧です。

`full` は全Secret参照を要求する既定値です。明示的な `bootstrap` ではPolaris起動・root metadata初期化に必要な材料だけを出し、まだ発行できないservice principalとTrino authを未供給として列挙します。bootstrapからruntime authを作りません。同じ世代の参照先やversionを途中で変更する方式にはしません。

これは材料の生成契約です。二段階の適用、APIによるprincipal発行、停止・再起動・検証を伴う操作はP2-5以降です。

## OpenTofu / GitOpsとの接続

OpenTofuは新modeで同じ公開metadataとprivate Secret mapを受け、DB数・SQL名をsales / financeに固定しません。version付きSecretの参照を不変名へ結び付けます。generation未指定時の現在の構成を維持し、P2-4では実stateに対するapplyを行いません。

Polaris metadata bootstrap Jobの名前は実際のpod入力から求めます。全体generation annotationだけの変更では同じJobを再利用し、関係するSecret版の変更時は新Jobにします。Polaris 1.8の既存realm bootstrapは成功するno-opであり、既存root資格情報のrotationにはなりません（[1.8.0の実装](https://github.com/apache/polaris/blob/apache-polaris-1.8.0/runtime/admin/src/main/java/org/apache/polaris/admintool/BootstrapCommand.java#L147-L177)）。Catalog / namespace / service principalのREST操作は作成定義までで、今回実行しません。

generation用PostgreSQL Secretは既存resourceから分離し、`immutable = true`、dataの変更無視なしとしました。既存経路は旧挙動を維持し、resource addressの変更はmovedで対応します。今回はstateへ適用していません。

通常local GitOpsのgeneration指定は `prepare` に限定します。候補世代の公開treeを検証し、現在稼働中のprofileやprivate資格情報を暗黙に読みません。生成modeでのupload / snapshot / activateは提供しません。明示された候補authが別世代・別DB集合なら拒否します。

現在稼働中のSALES=`iceberg` / FINANCE=`finance`と、P2-3のprofileは切り替えません。計画上の正規名と新Polaris Catalog / prefixは候補生成物として検証します。

## 検証

以下を最終ソースで確認しました。

| 検証 | 結果 |
| - | - |
| 通常render / check / GitOps prepare CLI | 2 DB / 任意名3 DBを生成。同一入力の2回の出力がbyte単位で一致。改変bundle、生成modeでのsnapshot / upload / activate、別世代authを拒否 |
| 実Kustomize 5.8.1 | 8 root / 合計39 documentを展開。Secret documentを含まない。既存secure overlayの親directory参照には `--load-restrictor LoadRestrictionsNone` を使用。`config / node / log` Catalog名でも本体設定との衝突なし |
| 通常private material CLI | actual core bundleの合成private入力からbootstrap 14 / full 21 Secretを組立て。未供給7参照、同一世代・同じ既存値、byte単位の再現、directory 0700 / file 0600、bootstrapからbackend.jsonを出さないことを確認 |
| 実OPA 1.20.2 | 公式固定digest imageをnetworkなし・read-onlyで実行。3 DB、admin / DB別engineer / viewer / 未知identity、既知 / 未知Catalog、4 SchemaのTable procedure 128ケース成功 |
| 実OpenTofu 1.12.3 | provider cacheと公開依存ファイルだけを含む隔離copyでinit / fmt / validate成功。Kubernetes / Helmを明示mockしplanのみ実行。full / bootstrapの件数・DB別および共通Secretの参照とdata、既存2DB mode維持、不正phase拒否の4 run成功。実state / auto.tfvars / 実資格情報はcopyしていない |
| focused回帰 | 113件中112成功・1skip（任意OPA CLI未導入。実OPA検証は上記で別実行） |
| 全体回帰 | `make PYTHON=<venv>/bin/python verify-local-experiment` は828件中796成功・32skip（opt-in実機試験・任意依存）。失敗なし |
| 文書・独立レビュー | `make verify-design-context` は14試験と検査に成功。Go CLI回帰・`git diff --check`成功。最終差分の独立レビューはblockerなし |
| 現runtime | doctorは2026-10-19T15:35:48Zにhealthy。稼働5 workloadのspecとactive runtime profileのhashが作業前と一致。2026-10-20訂正：比較したToFu既定state pathは実在せず、実stateのhash比較の証拠にはならない。P2-4ではstate適用自体を行っていない。実stateの特定は[P2-5記録](database-p2-5-operation-report-2026-10-20.md)を参照 |

秘密値は合成fixtureだけを使用しました。PEMの実暗号署名、資格情報の実ログイン、追加3 DBのengine / S3操作を再受入した結果ではありません。OPAはfile ACLとANDで使用するprocedure guardであり、通常SELECT等のDB分離をOPA単体が担うとはしていません。

focusedには、不正SQL名・重複binding / prefix / key、未知profile、Secretの欠落・版混入・余剰key・realm不一致、root資格情報のSPへの流用、DB間でのS3秘密値 / root秘密値 / JWT秘密鍵の共有の拒否を含みます。bcryptは72 byte超と同一verifierを拒否し、供給済みpasswordとの照合を行います。公開treeとreceiptへの合成private値の混入も検査しました。registryへのGeneration保存やSecretの実在・版変更照合はまだ実装していません。

OpenTofuのmockとplan専用testの使い方は[1.12公式資料](https://opentofu.org/docs/v1.12/cli/commands/test/)に従います。既定のtestはapplyを伴い得るため、検証用testではproviderを明示的にmockし、`command = plan`を固定します。

証跡・合成private材料・ログはGit対象外の `var/shirokuma/p2-4-acceptance/` に保存します。private入力やplanの秘密値を公開しません。

開始時HEADは `c639dc585dd0acb27ff7a03fb959d2724d6187f2`、作業前に既存変更を含む503ファイルのhashを保存しました。既存Phase 1 / P2-1〜P2-3の未commit変更を保った同じcheckoutで作業しています。検証ソースのsnapshotはP2-4だけの純粋なHEAD差分を意味しません。

## 後続へ残す範囲

- P2-5: 管理Operation、明示apply、全入口のadmission / drain、排他、Secret版変更時の再plan、UNKNOWN照合、適用後のREADY判定。
- P2-6: 任意の3番目DBの実機provision、二段階適用、通常再配備・空環境からの再構築、engine / S3の正負試験。
- Secret旧版の清掃、停止を伴うrotationの新世代への適用、サービス用principalの実発行と資格情報の受渡しは今回実行しません。

本記録は生成と整合性の限定受入です。構成を稼働させた証拠や、後続Phase・運用者の受入を兼ねません。
