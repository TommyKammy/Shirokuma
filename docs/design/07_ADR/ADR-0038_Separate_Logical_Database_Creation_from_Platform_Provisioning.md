---
project: Shirokuma
doc_id: "ADR-0038"
title: "Separate Logical Database Creation from Platform Provisioning"
status: proposed
created: 2026-10-27
updated: 2026-10-28
version: "0.3"
area: adr
tags: [shirokuma, adr, database, provisioning, authorization, usability]
---

# ADR-0038: Separate Logical Database Creation from Platform Provisioning

## Context

P5-7の所有者操作で、DB作成からREADY・SQL実行まで到達した。しかし所有者は、
Snowflakeに比べて操作・承認・待ち時間が多いことをblocking指摘とし、DB作成方針そのものの
再設計を求めた。[本人受入記録](../../guides/database-p5-7-owner-acceptance-2026-10-27.md)の
受入は当時保留した。説明の改善や約12%の単発処理時間差では、この要求を満たさない。

本ADRは再設計案である。2026-10-28に順序3の製品新経路を実装し、技術検証を進めた。
[実装・検証記録](../../guides/database-logical-create-implementation-2026-10-28.md)に、通常CREATE、
実データ、現在認可、資格情報の期限、性能を区別して記録する。
順序4の[切替・本人受入](../../guides/database-logical-create-owner-acceptance-2026-10-28.md)は条件付き受入となった。
所有者は作成導線の改善を確認し、待ち時間の改善を継続条件とした。暫定性能目標は未達であり、改善を続ける。
ADR-0037とP5契約は従来経路に引き続き適用し、新しい`logical_v1`には後継契約を適用する。
基盤変更は管理操作へ分離し、その基盤変更に必要な承認を通常CREATEの省略で代替しない。

### 従来経路の因果関係

| 従来経路の選択 | DBを1つ追加した時の結果 | source |
| --- | --- | --- |
| 登録時にDB固有のservice・realm・catalog・S3 prefixを固定 | 論理DBと物理配備が早期に結合 | `catalog_admin.py:_create_database` |
| 全DBの構成を世代として再生成 | 全Polaris Deploymentと共用TrinoのPod templateにも世代を付加し、既存Podまで再配備 | `database_generation.py:_mark/_render_polaris/_render_trino` |
| DB別Polarisプロセス・PostgreSQL DB・署名鍵・S3キー | 新しい空のDBにも専用サービスの起動・bootstrap待ち | `database_generation.py:_render_polaris` |
| TrinoのDB別catalog・consumer/S3環境変数、password/file ACL/OPA、Seaweedのidentityを静的生成 | DB追加で共用計算基盤と両API runtimeの構成変更 | `database_generation.py`, `database_security.py`, `database_material.py` |
| bootstrapでconsumerを発行し、その実値からfullの材料を確定 | 同設計では2段階の準備・承認が必要 | `database_creation_preparation.py:prepare` |
| warehouse全体のdrain、全DBの再照合後にOPEN | 他DBも利用制限を受け、既存DB数に応じて作業が増える | `warehouse_operations.py:_apply`, `warehouse_operator.py:verify` |

上記の広い変更を行う現経路では、drain・厳密な照合・不明結果の停止は必要である。
通常のDB作成に広い変更を含めることが設計上の問題であり、検査を消すだけでは解決しない。
Ready待ちのtimeoutは待機の上限で、固定sleepによる数分の遅延ではない。

今回の新DBでは、要求ごとの処理時間の合計が382.455秒、要求間の操作・承認等が1088.201秒、
受付からREADYまで1470.656秒だった。処理合計には2回の計画準備と適用が含まれる。
これは1件の実測であり、一般的な性能値でも、改善単独の効果でもない。

SnowflakeではDBはschemaの論理的な集まりで、computeはVirtual Warehouseとして別に扱う。
その分離を製品モデルの参考とする。公式資料はShirokumaでの秒数やSnowflakeの一律の
作成時間を保証しない（[database DDL](https://docs.snowflake.com/en/sql-reference/ddl-database)、
[architecture](https://docs.snowflake.com/en/user-guide/intro-key-concepts)）。

## Proposed direction

### 1. 通常のDB作成と基盤管理を分離する

| 操作 | 担当する変更 | 利用者に見せる操作 |
| --- | --- | --- |
| 基盤の初回構築・更新 | storage、catalog server、compute、認証方式、ネットワーク、鍵、構成世代の変更 | 管理者が影響・計画を確認。必要な承認、メンテナンスと全体検証を実施 |
| 通常のDB作成 | 用意済み基盤上のcatalog、既定schema、初期権限とDB bindingの追加 | 名前と初期アクセスを指定して「作成」を1回。そのままSQLへ |

通常CREATEはbootstrap/full、ToFu plan/apply、Flux全体反映、既存Podの再配備、
warehouse全体のdrain、両APIのprofile切替、全DB再検査を呼ばない。
基盤が未準備なら管理者向けの基盤準備へ案内し、通常CREATEの途中へ暗黙に混在させない。
倉庫の休止・再開とDB作成の時間も分けて表示・計測する。

権限の確認は実行時と確定時に機械的に行う。通常CREATEに必要な明示意思は1回の作成操作とし、
基盤変更用の2回の承認を通常CREATEの要件から外す。現行の承認を内部で自動代行する方式は採らない。

### 2. 先に解くべき技術上の条件

**Trinoのhot catalogだけでは成立しない。** 現在のTrino 483にはdynamic catalog管理があるが、
実験機能であり、CREATE CATALOGの全文がquery履歴・Web UIへ記録される。
秘密値をSQLへ直接埋め込まず、新DBごとの環境変数追加で再起動が必要になる方式も避ける。
永続化・通常再起動・coordinator復旧・workerへの伝播を同じ構成で実証する。
([483 catalog管理](https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/admin/properties-catalog.md)、
[483 CREATE CATALOG](https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/sql/create-catalog.md))

**現行のDB別Polarisには理由がある。**
[1.8.0採用記録](../../guides/polaris-180-adoption.md)では、STSなしの内部FileIOがプロセスのS3資格情報に
依存するため、DB別プロセスとprefix限定キーで分離した。realmの起動時allowlistもある。
共有サービスにcatalogを追加できることや、SQL上のRBACがあることだけで、このstorage境界を
同等に保てるとは扱わない。全DBへアクセスする共通キーへの差替えを高速化として採用しない。

最初の実証では、次の候補を比較して採用可否を決める。

- **第一候補：共有catalogサービスと動的な資格情報供給。** 環境／信頼領域を単位にPolarisを準備し、
  その中へDBごとのcatalogを追加する。catalog別consumerとstorage範囲を保ち、Trinoのcatalog、
  認証・認可も再起動なしに更新する。PolarisのSTS／credential vendingとstorage側の実装互換性を
  実証対象にする。現行SeaweedFS構成が対応済みとは主張しない。
- **比較候補：DB別サービスを維持して準備を前倒しする。** 事前に用意した小さなpoolへ論理DBを
  割り当てる。SQL名と物理IDを分離し、catalogのhot登録、資格情報参照、再充填で既存DBを再配備
  しないことが必要。pool枯渇時の遅延と常駐資源を別評価し、常時高速とは称さない。

Polarisの公式資料でも、STSなしではsubscoped credential vendingを行わずclientがstorageへ直接
認証する。この点を飛ばしてvendingを有効化するだけの案にはしない。
([Polaris 1.8.0 S3](https://polaris.apache.org/releases/1.8.0/configuration/configuring-polaris-for-production/configuring-aws-s3-cloud-storage-specific/))

現在のstackで条件を満たせなければ、catalog／storage／credential供給の組合せを比較し直す。
既存stackを維持するためだけに、DBごとの全体再構築や独自の大規模adapterを温存しない。
実装方式はこの実証の結果で確定し、共有サービス化による信頼境界の変更は明記する。

### 3. 通常CREATEの契約と互換性

新経路でもDB IDによる参照、`database.schema.table`、選択role、read-only、Job／Stage／Martの
DB context契約を維持する。再構築するDBは新IDとし、旧IDを別catalogへ付け替えない。
SQL文字列の一括置換で別catalog名へ見せかけない。
API runtimeはDB単位のbinding／資格情報参照を取得し、DB追加でprofile全体を作り直さない。
現行`runtime_profile.py`／`request_auth.py`が要求する全DBの単一generationと集合完全一致を、
基盤revisionとDB別catalog／権限revisionへ分離する。動的registryを読む全入口が同じ有効版を
参照し、未確定のbindingや旧権限を利用しないことを契約に含める。
current account・role ceiling・platform権限・対象grant失効を実際の認証読取経路で検査する。

作成intentと外部効果を永続記録し、同じ要求の照会・重複送信・応答喪失を区別する。
同名競合は原子的に拒否する。排他はDB名・対象bindingの範囲とし、通常CREATEのたびに
環境全体を予約しない。基盤更新との競合は基盤revisionで検出し、不一致なら成功確定しない。
provider結果不明をTTLや画面上の成功で解消せず、元intentを観測してから再開する。

対象catalog・schemaへの到達、対象roleの認可、runtime bindingの一致を確認してREADYとする。
対象DBに限局した作成失敗では新DBを隔離し、既存READY DBを一括RECONCILINGにしない。
共有基盤自体の故障・失効・不整合には基盤側の停止・復旧契約を適用する。
HTTP受付だけを高速化し、裏で同じ数分の処理を走らせることを達成条件にしない。

## Migration and bounded work

2026-10-27: 所有者の指示で順序1の有界実証を完了した。
[実現性実験の記録](../../guides/database-adr0038-feasibility-2026-10-27.md)に、事前準備方式、
短期資格情報、起動後の秘密参照、共有Polaris、workerの実I/O、native PVC公開を分けて記録した。
共有Polaris＋STS vending＋immutable秘密参照を次の実装対象に選定する。
共有プロセスを管理する主体は各DBのroleを引受け可能であり、このサービス自体を信頼する境界になる。
共有Trino processと管理主体も全DB consumer秘密を読めるため、その侵害からのDB別秘密分離は保証しない。
native公開の初期範囲は同一namespace・単一K3s nodeのlocal RWO PVC共有Pod群に限定する。
順序1時点では通常CREATEの製品認証／ACL／OPA更新は未実証だった。
[順序2の後継契約案](../../guides/database-logical-create-contract-2026-10-27.md)に現在認可・DB別revision・
最終CAS・UNKNOWN・限定publisherと順序3の実装単位を固定し、2026-10-28の製品実装で検証した。
新経路では基盤の17資源を保持したままDBを追加し、CLI・WebUIから同じAPIへ接続する。
共有認可ファイルのmetadata描画費用は残る。計測済みの数秒を、暫定SLOの達成とは扱わない。

| 順序 | 成果物と実証 | 主な変更対象候補 |
| --- | --- | --- |
| 1: 実現性 | 隔離した実stackでhot catalog、秘密値非露出、Polaris内部FileIOとstorage分離、再起動復旧を実証。候補の採否と資源を比較 | 既存deploy／credential／catalog構成。製品経路へ入れる前の有界な実験 |
| 2: 契約更新 | 実証済み方式に合わせ通常CREATEと基盤管理のAPI、権限、状態遷移、競合・不明結果を固定 | 本ADR、P5-0後継契約、catalog binding／runtime設定 |
| 3: 新経路 | DB単位の作成・binding反映・対象検証を実装し、CLIとWebUIを同じ契約へ接続 | `database_creations.py`, provisioning、security、query/DataOps context解決、Workbench |
| 4: 切替・本人受入 | 再構築または既存DBとの並存を選び、通常再起動、権限・同名データ分離、Job等を実証し、1回の作成からSQLまで本人が確認 | 必要なmigration、clean／選択した既存受入、owner記録 |

2026-10-27の追加指示により、既存DBの保持は必須条件としない。保持・移行対応や検証の
overheadが大きい場合は、Shirokumaの実験DBを破棄して新方式で再構築できる。
方式比較で旧DBとの並存adapterや物理移行を必須にせず、単純な再構築も評価する。
破棄する場合は対象と必要な証拠を記録し、旧UNKNOWN／承認を新方式の成功や再送権限へ変換しない。
並存を選ぶ場合だけ、基盤更新が他方式のcatalog／秘密参照を削除しない所有境界を定める。
全Colimaや無関係な資源の破棄はこの指示の対象外とする。

## Acceptance targets

稼働済み基盤・起動済みwarehouseで、作成操作からREADYと同contextの最初のSQL成功まで
**p50 2秒以内／p95 5秒以内**を暫定目標とする。未計測の目標であり、達成保証ではない。
初回基盤構築、warehouse起動、pool枯渇、復旧は別に計測・表示する。

実stackの複数DB数・複数回で計測し、既存DB数に応じた全件再配備・全件検査が通常経路にないこと、
別DBの実行を止めないことを確認する。一般名・任意の有効SQL・実Iceberg tableの書込み／読取り、
他DB／他prefixへの拒否、role失効、資格情報漏洩なし、再起動後の同一identity保持まで実証する。
単一fixtureやSELECT定数だけではcatalog・storageの実現性をPASSにしない。

## Consequences

作り直す主対象はDB作成と基盤配備の境界、静的binding／秘密供給／認可の反映方式である。
既存のデータモデル、権限要件、永続intent・不明結果照合、SQL／DataOpsの資産は再利用する。
基盤配備用の厳格な承認と検証は、その影響が発生する管理操作へ残す。
先行実証は独立したnetwork／永続領域で行う。方式決定後は、保持の負担に応じて既存実験DBの
再構築を選べる。現時点で旧DB移行互換性を実装開始の前提としない。
