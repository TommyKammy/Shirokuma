# DB-P6-3A dbt SQLモデルの先行実証

2026年10月29日。DB-P6-3Aの実装・先行実証・技術受入を完了した。[DB-P6-3](database-full-capability-and-compute-plan-2026-10-28.md)の最初のunitとして、既存projectのoffline compileと既存WAPへの接続を実証した。通常のdbt build executor、Database API／CLI／UIの機能開放はこのunitの完了条件に含めず、DB-P6-3全体は未完了として残す。

## 接続方式

信頼するローカルSQL projectを一時directoryへコピーし、資格情報を持たないdummy profileで`compile --no-introspect --no-populate-cache --no-partial-parse`を行う。元project内にtarget／logsを作らず、telemetry・外部package取得・backend接続を使わない。既存projectの既定値は維持し、対象catalog・source schema・候補SANDBOXとstagingのephemeral化を実証時に明示する。

manifestのmodel版、完全relation名、sources／refs／依存関係、compiled SELECT、testを保存する。実行可能な候補はtableとephemeralに限定する。元projectのviewを暗黙にtableへ変換せず、明示overrideがないviewは未対応として拒否する。Python model、hooks、外部packages、incremental、contract、grants／docs変更、失敗relation保存、schema作成は除外する。

sourceは指定した一つのSchemaに限定し、compile後のcatalog／Schema一致も検査する。同名Tableの参照が曖昧になるSQLは明示aliasを必要とする。現在のgeneric test対応はunique、not_null、relationships、accepted_valuesである。

このcompilerが検証・引き渡すのはSELECTとその参照関係であり、dbtのtable properties等を適用するmaterialization executorではない。SQL候補の実証から、すべてのdbt model configの実行互換性は推定しない。

compiled SELECTの物理参照をmanifestと同じDBの明示Table Refへ照合し、既存manual WAPのsource aliasへ変換する。CTEと物理Tableを区別し、元aliasを保持する。SQL policyやWAP compilerを緩めず、候補構築・Snapshot固定・query intent・admission・現在権限は既存の通常APIへ委ねる。任意のdbt DDLを共通transportへ直接送らない。

compiled testもSELECTとして同じ候補とsource Snapshotへ固定し、認可済みの通常query APIで実行する。返された失敗行の件数0をpassとする。これは標準dbt test executorの互換性や`store_failures`対応を意味しない。

全testの成功を確認した後だけ、指定候補をsourceにした既存WAPのbuild → audit → 明示publishへ進む。通常APIを使い、候補の所有記録や監査を省く直接採用は行わない。2DBの同名model・source・公開先を完全identityで分ける。

## 既存dbt buildとの境界

導入済みdbt-core 1.12.5／dbt-trino 1.10.3／Trino DB-API 0.339.0のsourceを確認した。標準executorには暗黙schema作成、既存table置換時のDROP／ALTER RENAME、既定の`SECURITY DEFINER` view、contractの複文、hooks／grants等がある。既存public SQL guardの拒否を解除する方法は採用しない。LDAP profileのHTTPS固定と既存loopback proxyの接続条件も、native executorの後続実証で扱う。

## 受入と後続

コード回帰、実offline compile、2DBの実Table・値・Snapshot・lineage、test失敗時の公開先保全、成功後の明示WAP公開を別々に記録する。元の台帳・データ・worksheet・host固有設定は保持し、実証専用DBだけを追加する。

DB-P6-3B以降で、耐久run／parent operation、API／CLI／UI、compile後の版固定、test gateのサーバー強制、cancel・権限失効・process停止・commit後の記帳失敗を統合受入する。view materialization等の未実証項目は対応一覧へ追加せず、M1の残件として扱う。

## 実装と呼び出し

[database_dbt.py](../../tools/local_experiment/database_dbt.py)はSQLを実行しないprivate operator向けcompilerである。次の明示指定で、元projectを変えずに候補用manifestを得る。

```python
plan = compile_project(
    trusted_project,
    catalog="target_database",
    source_schema="raw",
    staging_materialization="ephemeral",
)
```

`normalize_select`は必要な完全relation名と同一DBのResourceRefを受け取り、WAP用のsource aliasとSQLを返す。依存順のtable modelだけを既存WAPへ登録・buildし、testは同じ候補Snapshotへ固定して通常query APIで取得した。SQL実行や公開を行う新しい汎用executorは追加していない。

projectの変数は`shirokuma_source_database`、`shirokuma_source_schema`、`shirokuma_candidate_schema`、`shirokuma_staging_materialization`である。旧操作の既定値iceberg／tpch_tiny／staging・marts／viewは維持し、既存のlegacy operation gateも変更していない。

## 実機結果

最初の既存projectのコピーによるcompileは、接続関数を例外化しても成功した。5 SQLモデル・7 generic testとrefs／sources／依存関係を取得し、接続試行は0、元projectの全source hashは一致した。実装後の3回のguarded offline compileでも接続試行0、default view拒否と明示ephemeral成功を確認した。

| 確認 | 結果 |
| --- | --- |
| 専用DB A／Bの同名project | 各5モデル（3 ephemeral・2 table）をcompile。Table Ref・Snapshot・source lineageをDBごとに分離 |
| 実データ作成者による候補構築 | 各DBでdim_customers／fct_ordersを既存WAPで構築。compiled testは両DBとも7／7 pass |
| 明示公開 | 基盤管理者が既存WAPのaudit・publishを実行。公開先はAがALPHA／金額300、BがBETA／金額900で各2行 |
| 不正データの候補 | Aだけにorderstatus=Xを追加し再構築。7 test中accepted_valuesの1件が失敗し、公開しなかった |
| 失敗時の公開先 | Aの公開済み2 TableのSnapshot一覧・値・Table Refが失敗前と一致。Bも保持 |
| 参照の拒否 | 別DBのRefをAのcompile SQLへ結び付ける要求はINVALID_BINDING。指定外source Schemaも回帰で拒否 |
| 実閲覧者 | 両DBの公開済み2 modelを取得。raw参照と選択DBを越えるSQLはSQL_PERMISSION_DENIEDで、query受付0 |
| 保全 | 元139 DB・556 Schema・139 bindingは全行・全fieldが一致。専用2 DBを加え141 DBすべてREADY |

失敗後の公開を止めたのはprivate operatorの実証手順であり、dbt test結果を既存WAPの公開条件へサーバー側で追加した実装ではない。M1の機能開放には後続のtest gate統合を必要とする。最初の実証scriptはDataOpsのfull contextを省略してmetadata保存を拒否された。保存model・SQL実行が0と確認し、正しいcontextを持つ別要求で進めた。

最終の共有LOCALは2／ready2・engine検証済み、Auto-Chillは0分・revision 5を保持し、未閉鎖実行と未完了logical要求は0である。過去の2件のUNKNOWNは既存のFENCED_READ_ONLY履歴のまま変更していない。既存worksheet・SQLとhost固有YAMLに手を加えていない。

新compilerとlegacy pipelineのfocused回帰は20件成功した。Makefileの全体検証に新moduleを追加した。`doctor --profile local-experiment`はhealthyだったが、旧namespaceも対象のため、共有logical基盤の受入は上記の実SQLとruntime観測で独立に確認した。全体回帰は81グループ・3,117件中3,084件成功・33件skipとなった。初回は72グループ通過後、新testのimport設定で停止した。標準discovery向けにtestだけを修正し、PYTHONPATHなしで新旧20件と残り9グループを確認した。通過済みprefixの製品コードは変わっておらず、集計は成功した全81グループを一度ずつ数えている。設計整合性とdiffcheckも成功した。

生のmanifest・Table Ref／Snapshot・正常／失敗test・公開記録・台帳snapshotは、Git対象外の`var/shirokuma/db-p6-3a-2026-10-29/private/`へ保存した。主な資料は`good-0-proof.json`、`good-1-proof.json`、`bad-0-proof.json`、`failed-tests-publication-preserved.json`、`final-preservation.json`である。
