# P4-1 管理mutation境界・通常grant API/CLI 実装brief

2026-10-20。正本は[Phase 4計画](database-phase4-implementation-plan-2026-10-20.md)のP4-0/1。
P4-0の現状態をCommanderが確認してから実装へ進む。既存のdirty/untracked作業を保持する。

## 範囲と入口

- 既存grantをdesiredとして維持し、principal×DBのeffective role/永続認可epochと適用revision/世代を追加する。
- 認証済みplatform admin向けの通常grant一覧/変更/revoke/Operation照会HTTPと、HTTPを使う通常CLIを追加する。
- CASは新規0、更新正のdesired revision。同key同payloadを再照会し、異payloadは409。監査とdesired/effective変更は同transaction。
- 付与/昇格はpending。剥奪/降格は即時にeffectiveを制限してepochを進める。global role ceiling/platform_admin属性は変更しない。
- 既存plan/full applyのcandidate DB×role backend検証後、desired snapshotの再照合と同transactionでeffective/APPLIEDを確定する。
- replan/reconcileを区別し、UNKNOWNやAWAITING_APPLYだけでは昇格させない。apply中の編集/剥奪は古いplanを失効させる。
- 全通常/Worksheet/native/DataOpsの新規/継続/結果/history/downloadでeffective epochを照合する。元pin取消の既存契約は維持する。

## 移行と監査

空stateと既存state copyのmigrationを検証する。明示profileがない実state/legacy SQLiteを開かない。
初期有効化は現在binding.applied_generation_idに結ばれたverified full APPLIEDのplan manifestの
principal/DB/role/revision全体に一致するgrantだけ。PLANNED/PREPARED/未検証やrevisionだけの一致を根拠にしない。
epochは旧grant revision以上の永続値から始め、revoke/regrant/再起動/migrationでもtombstoneを削除・初期化しない。

監査はactor、対象principal/DB、before/after role、期待/確定desired revision、effective epoch、
grant Operationと構成apply Operationの対応、適用世代、receiptを残す。secret/tokenを保存・返却しない。
DB×role共有backend identityのまま、principal別secret失効を実装したと扱わない。

## 対象ファイル

`tools/local_experiment/control_store.py`, `data_management.py`, `request_auth.py`,
`query_context.py`, `query_service.py`, `dataops_service.py`, `catalog_admin.py`,
`warehouse_operations.py`, `warehouse_operator.py`, `database_generation.py`,
`workbench_server.py`、同directoryの小さなgrant管理moduleと関連test、
`internal/cli/database.go`と必要なCLI client/test、必要なMakefile検査登録。
認可の別読取りが見つかれば同directoryの直接呼出元に限って修正し、変更理由を報告する。
workerはfocused testsまで。active runtime変更・実grant変更・全体検査はCommanderが順番に行う。

## 検証条件

1. pending付与は認可されず、verified full applyのみ有効化。未証明migrationは拒否、正照合migrationは現在許可を維持。
2. revoke/downgrade直後のSQL/nextUri/results/history/Export取得拒否、元pin取消、regrant後の旧epoch拒否。
3. 新規/更新CAS競合、同key再照会/異payload409、actor/owner/DB分離、監査/Operationの原子的保存。
4. apply中revoke/編集、失敗/応答喪失/再起動/UNKNOWN/reconcile/replanで過剰付与・epoch巻戻しなし。
5. grantを使う既存focused suiteを回帰。Go CLI製品範囲を検証。変更に即した試験を作り、既存安全性を弱めない。

実機ではSALES/FINANCE/任意名DBの正常系を対にし、通常API/CLIとnative/実backend検証を別々に証明する。
入力/台帳と外部送信回数、実Table/取得結果、epoch、適用世代を照合する。
UI管理画面・表示名/R03・Jobs/Stages/Marts/Schedule/retireは後続unit。汎用workflow基盤や追加常駐workerは作らない。
