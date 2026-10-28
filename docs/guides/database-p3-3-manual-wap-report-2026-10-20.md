# P3-3 手動WAP 実装・実機検証報告

確認日: 2026-10-20 / checkout: `local/autonomous-experiment`

P3-1の共通DataOps実行境界を使う手動WAPを実装し、実Trino 483のsales / finance / insights_e88387a5でbuild → audit → publish、rollback、DB分離・権限制御を確認した。実機の記録済み472項目はすべて成功した。これはP3-3のbackend単位の技術検証である。

## 実装

主な実装は[manual_wap.py](../../tools/local_experiment/manual_wap.py)、生成SQLと実測Refの検査は[dataops_service.py](../../tools/local_experiment/dataops_service.py)に置いた。

| API | 保存・実行する内容 |
| --- | --- |
| `save_model(principal, context, *, model_id, sql, sources, target, expected_version, min_rows=1, not_null=())` | DB内model IDとimmutable version。更新はversion CAS。source alias → 同DBの実在ResourceRef、既存targetはResourceRef、初回の存在しないtargetはDestinationLocatorを要求 |
| `build(principal, context, *, key, model_id, version)` | sourceの実UUID/current Snapshot/schemaを固定し、制約付きread SQLをserver生成のSnapshot参照へ書き換えて、登録済みSANDBOXに所有stageをCTAS。build Ref・inputRefs・生成SQL・target期待値を保存 |
| `audit(principal, context, *, key, build_id)` | model版・stage Ref/Snapshot/schemaを照合し、固定Snapshotで件数・not-nullを監査。min_rowsを満たすpassed auditだけ公開可能 |
| `publish(principal, context, *, key, build_id, audit_id)` | adminのみ。監査済みstageの明示列とSnapshotからCTAS/CRTAし、public Ref/Snapshotと監査元、previousの対応を保存 |
| `rollback(principal, context, *, key, publication_id)` | adminのみ。保存済みpublicationのprevious Snapshotへ限定し、現target Ref/head/schemaと旧Snapshotの存在を実行前に照合 |

`get_model / get_build / get_audit / get_publication`は現在の本人・DB contextを再認可する。adminはengineerが作ったDB内artifactを参照して公開できる。別本人のquery結果取得権限を緩めてはいない。

build / audit / publish / rollbackは別々のDataOps Operationであり、その呼出時の本人、selected/effective role、grant/binding revision、backend identity・credentialを固定する。子query intentは外部POSTより先に保存する。同じキー・同じpayloadの再実行は保存済みOperationの観測となり、SQLを再送しない。

[control_store.py](../../tools/local_experiment/control_store.py)をschema 8へ進め、DB内model head/version、build、audit、publicationを保存する。runtime profile確定後のstoreを使い、旧`wap_models.json`は移行しない。[wap_resident.py](../../tools/local_experiment/wap_resident.py)にはlazy facadeと旧pathの遅延解決を追加した。

SQLは`sqlglot==28.0.0`でASTとscopeを検査し、宣言した未修飾source aliasだけを物理Tableへ解決する。CTE・join・aggregateを確認した。未宣言source、別catalog/schema、table function、任意time travel、write CTE、複数文、未対応の関数・relation形式は拒否する。comments・recursive CTEも初期contractでは未対応。依存はpyprojectと既存uv.lockの両方に直接指定した。public WorksheetのSQL guardは変更していない。

## 追加した整合性検査

- stageのデータ変更だけでなく、Snapshot IDが変わらないschema変更も拒否する。
- 初回publish後は保存した実Refを使い、同名Tableの再作成を採用しない。
- rollbackは任意Snapshot IDを受け付けない。previousがない初回公開、current headの外部更新、旧Snapshot失効を拒否する。
- schema / field identityが互換でないrollbackは`ROLLBACK_SCHEMA_INCOMPATIBLE`でSQL送信前に拒否する。Snapshot rollbackによるschemaの自動復元は行わない。
- previousの実測値が過去publicationのtargetと一致する場合だけ、そのpublication/auditへ対応付ける。外部追記を含むprevious Snapshotは保存し、監査帰属を`None`とする。
- backendの確定FINISHEDでcommitを記録する。公開・rollbackのcommit後にartifact保存が欠けた場合、結果を保持したうえで`MODEL_RECONCILIATION_REQUIRED`により後続のmodel操作を止める。UNKNOWNから正の証拠でcommitを照合した後も同じ検査を適用する。

## 自動検証

実行場所は有効な開発checkoutのリポジトリroot。既存のPython 3.12 DataOps環境を使用した。

| 検査 | 結果 |
| --- | --- |
| focused: manual WAP 30 + DataOps 25 + control_store 11 + legacy WAP 10 | 76成功 |
| `rtk proxy make PYTHON=tools/local_experiment/dataops/.venv/bin/python3 verify-local-experiment` | 40 suites、1,141件中1,109成功 / 32 skip、exit 0 |
| `rtk proxy make PYTHON=tools/local_experiment/dataops/.venv/bin/python3 verify-design-context` | 14成功、exit 0 |
| system Python 3.9.6で変更module import | 成功、sqlglotはimportされずlazyのまま |
| `rtk proxy uv lock --check --offline --project tools/local_experiment/dataops` | exit 0、install・package version変更なし |
| `rtk proxy git diff --check` | exit 0 |

独立レビューで、commit後の記帳欠落、外部追記前後の監査帰属、schema変更rollbackの3ケースを再現し、修正後は追加POST 0件で期待どおり拒否・記録されることを再確認した。sourceのAST迂回、grant失効後のdefinite settlement、proposed receiptと確定commitの区別もレビューした。

## 実機結果

run ID: `fa328d97`。既存default profile、実Trino 483 / Polaris / Icebergを使用した。3DBで同じmodel名・source/target名、異なる件数と値を使い、Decimal・NULL・空文字を含めて全件比較した。engineerは`both-db / sales-only / finance-only`、adminは`p26-admin`を本人認証して使用した。

| フェーズ | 成功した確認数 | 主な確認 |
| --- | ---: | --- |
| 通常系 | 178 | 3DBの同名model分離、engineer build/audit、engineer/viewer publish・rollback拒否とadmin成功の対、RAW追記後も固定Snapshot由来の内容、初回rollback拒否、同一キー再送0件 |
| 変更・分離 | 138 | audit後のstage追記、Snapshot不変のADD COLUMN、model版変更、target再作成・外部更新、cross-DB source/target/artifact拒否。拒否時の追加mutating POST 0件 |
| grant | 24 | 失効時の操作・metadata取得拒否、再付与後も古いcontextを拒否、新revision contextで本人のbuild成功。CASでgrantを復元 |
| schema変更rollback | 40 | 非互換schemaを送信前に拒否し、公開済みv2のhead/schema/行を保持 |
| previousの出所 | 41 | 次build前のtarget外部追記を含むSnapshotへ実rollbackし、過去auditへの誤帰属がないことを全件照合 |
| 確定応答の喪失 | 25 | 実commit後のterminal応答を失わせ、parent/child UNKNOWN、replay 0 POST、実engineの正の証拠によるchild照合、明示reconcile後のcommit保持 |
| commit後の記帳失敗 | 26 | 実公開成功後にartifact保存を失敗させ、commitとerrorを保持。後続build/publish/saveを0 POSTで拒否し、公開履歴を捏造しない |
| 合計 | **472** | 全フェーズPASSED |

一次証拠は[実機ハーネス](../../var/shirokuma/p3-3-acceptance/runtime-checks.py)と[run-summary.json](../../var/shirokuma/p3-3-acceptance/fa328d97/run-summary.json)、同ディレクトリの`faults / grant / schema-rollback / provenance / loss / bookkeeping-summary.json`に保存した。各phaseは実行時source情報とSQL・receipt・件数を記録している。fixtureと証拠はignoredなlocal領域に置き、資格情報を報告書へ含めていない。

## 最終状態と障害証拠

最終読取確認は2026-10-20 10:32 UTC頃。schema 8、sales / finance / insights_e88387a5はREADY、LOCALはOPEN。doctorはhealthy / exit 0だった。doctorのhealth表示と実SQLの成功証拠は別々に確認した。

実機runに属する93 Operationは66 FINISHED / 27 expected FAILED。107子queryはすべてFINISHEDで、送信は107 POST、そのうち72 mutating POSTだった。子queryはすべてparentの本人・Database・backend identityと一致した。全体の未解決DataOps Operationとwarehouse blockerは0件。13件のtarget claimはすべてterminal ownerであり、受付を阻害しない。

意図したfault証拠として、公開artifactが不完全な既知commitを2件保持する。

| Operation | 状態 | 扱い |
| --- | --- | --- |
| `2a6f663a-686b-4eb2-9529-8dc95ff7dbe1` | FAILED + committed | 応答喪失後、実engineで子query成功を照合して明示終了。publication artifactは捏造せず、対象modelは手動照合要求を保持 |
| `477ff33b-fb75-4e14-b7cf-c5c09f885f1c` | FINISHED + committed + cleanup_error | 公開成功後のartifact保存失敗。対象modelの後続操作を拒否し、commitを未実行扱いしない |

これらは正常な公開履歴として受け入れていない。欠落対応を修復する際は実測証拠による明示照合を要し、SQLの自動再送・自動修復を行わない。warehouseのUNKNOWN blockerは残していない。

検証用34 Tableは、記録済みRef・実UUID・terminal Operationを確認したoperator診断経路で清掃した。10:33:47 UTCの最終実Catalog REST GETで34件すべての404を確認した（所有stage 22、source/target等のfixture 12）。この再確認でSQL POSTは行っていない。一般DROP権限やproductの清掃対象を拡張していない。[cleanup-evidence.json](../../var/shirokuma/p3-3-acceptance/fa328d97/cleanup-evidence.json)、[最終REST・所有者確認](../../var/shirokuma/p3-3-acceptance/fa328d97/fixture-absence-and-owner-evidence.json)、[grant復元証拠](../../var/shirokuma/p3-3-acceptance/fa328d97/grant-restore-evidence.json)を保存した。

## 残る計画境界

通常Workbench / resident / CLIのWAP導線とbrowser縦断受入はP3-5、所有者による最終操作性評価はPhase 5に残る。P3-4 Exportは今回の実装対象に含めていない。未対応入口の拒否は維持した。

同一サービスのmodel操作・target操作は直列化する。独立した外部writer全体をロックする仕組みは追加しておらず、実行直前のRef/head/schema観測と固定Snapshot参照を使う。

P2-R01の通常grant管理API、P2-R02の表示名更新API、P2-R03の名前入力境界全体の実HTTP / engine確認は、引き続きPhase 4に残る。このunitのbackend検証をそれらの完成やPhase 3全体の完了へ振り替えていない。commit / push / PR作成 / 外部公開は行っていない。
