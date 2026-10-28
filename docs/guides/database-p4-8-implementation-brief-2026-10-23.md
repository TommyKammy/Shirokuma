# P4-8 非purge DB retire 実装brief

2026-10-23。[Phase4計画](database-phase4-implementation-plan-2026-10-20.md)のP4-8。
P4-3〜7の定義・実行・予定と共通admissionを利用し、管理OperationとしてDBの利用を停止する。

## 状態と権限

現行platform administratorが最大role・選択roleともadminの場合だけ管理できる。
DB revision CASでREADYからretiringへ移し、対象DBの予定を原子的に無効化する。
管理者の明示reconcileが実行閉鎖を確認できた場合だけretiredを確定する。
UNKNOWN・競合・中断はblockerとして残す。経過時間で完了や再開にはしない。
Table、Catalog、storage、credential、履歴を保持し、ID・SQL名を再利用しない。

## 実行との境界

DBを共通admissionに保存し、新規受付とretireのCASを同じSQLiteトランザクションで競合制御する。
retiringでは利用者向け取得・全自動入口を拒否し、管理上の依存検索と照合を残す。
受理済み親の内部継続はprivateなaccepted scopeのDB・actor・generationと永続admissionを照合する。
account、role、grant、binding、generationの再検査を維持し、元pinの取消は最小状態だけ返す。
generationの開始・最終確定にもrevisionとlifecycleのCASを適用し、再plan・full applyで退役を解除しない。

## 分担と入口

- core worker：schema 18、共通admission・認可、退役Operationと依存照合。
- integration worker：既存Job／Stage／Mart／DataOpsの内部継続・取消、generation CAS。
- entry worker：管理REST・通常CLI・Workbenchとfocused回帰。
- Commander：差分レビュー、実機、統合gate、物理保持と既存資産の照合。

各実装workerはADR-0036の実際の`codex-cli gpt-6.1-sol medium`で有界に実行する。
管理RESTは`/api/v1/admin/databases/{id}/retire`のGET／POST、OperationのGET／reconcileを使う。
開始はDB revision、reconcileはOperation revisionとIdempotency-Keyを要求する。
通常CLIは`database retire-preview`、`retire`、`retire-status`、`retire-reconcile`を提供する。

## 実機受入

専用DBを通常管理入口から作成し、Job／Stage／Mart／Export／予定／runと実データを関連づける。
Workbenchと通常CLIで依存・停止・blocker・完了、非管理者拒否、CAS競合と同一key再送を確認する。
受理済み継続・元pin取消、UNKNOWN時の待機、grant失効、他DBの継続と結果取得拒否を個別に確認する。
退役後の再plan・full apply・再起動、通常／native／自動入口拒否とoperatorによる物理保持を照合する。
実機前の64 tableの閉じたsnapshot、既存5DB・認証設定・元の2下書きを基準に最終確認する。
