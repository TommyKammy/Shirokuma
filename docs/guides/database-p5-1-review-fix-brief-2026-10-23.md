# P5-1 独立レビュー修正 worker 指示

あなたは既にImplementation Worker。Commanderによるcodex-cli委譲は成立しているため、別workerを起動しない。
AGENTS/RTKとP5-0契約を守る。既存未commit変更を保持し、commit/push/live runtime/config/data操作をしない。

## 修正1: positive reconcile後の古い承認を復活させない

独立レビューで実再現済み：P5 bootstrapのpublish callbackが外部送信前に失敗→UNKNOWN、canonical
`NOT_EXECUTED/confirmed_not_dispatched` reconcile→AWAITING_APPLY revision5。
古いAPPROVEがVALID/CLAIMED、revision_cursor5、attemptNULLのまま残る。
fresh APPROVEはCREATION_BUSY、古いclaimの再取得は同じpermitを返し、古いpermitでPREPAREDまで進めてしまう。

warehouse_operations._reconcile_lockedのpositive reconcile→AWAITING_APPLY境界で、元P5 APPROVEを
同一transaction内で撤回/終了させる。cursorを進めるだけにしない。最新root/op revisionの新規明示承認を必須にする。
新しい承認でも元operationと確定済みstepを維持し、既知成功の外部作用を再送しない。
UNKNOWNのままの観測、およびnon-P5通常経路は既存契約を維持する。

tests/test_database_creation_auth.pyに実claimと実canonical reconcileを通る回帰試験を追加する。
旧claim再取得・旧permit適用の両方が拒否され、新しい承認のみ進めること、rollbackで状態を汚さないことを確認する。

## 修正2: 旧versionを再現するmigration fixture

make verify-coreがtests/test_db_marts.py:test_schema15_actual_copy_preserves_all_tables_rows_and_blobsで失敗した。
schema19の4表を残したままuser_versionを15へ戻すfixtureのため、再移行時にtable db_creations already existsとなる。
旧schemaの再現時に19の追加4表/関連indexを除く。実migrationを既存tableありで黙って成功させる回避は禁止。
同様のfixtureは下記許可testsだけ確認し、必要箇所のみ修正する。旧表の全row/blob保持検証を弱めない。

## 許可ファイル

- tools/local_experiment/database_creation_auth.py
- tools/local_experiment/warehouse_operations.py
- tests/test_database_creation_auth.py
- tests/test_db_marts.py
- tests/test_db_stage_scans.py
- tests/test_db_jobs.py
- tests/test_db_stages.py
- tests/test_control_store.py
- tests/test_grant_management.py

上記以外は変更しない。docsの更新はCommanderが行う。
既存venvで修正関連suiteとP5新規3module/warehouse_operations/warehouse_operatorのまとめ実行を行い、
正確な件数/時間/失敗原因/変更範囲を報告する。全makeはCommanderが別途実行する。
