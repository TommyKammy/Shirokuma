# P4-7 予定実行・自動scan／更新・alert 実装brief

2026-10-22。対象は[Phase4計画](database-phase4-implementation-plan-2026-10-20.md)のP4-7。
P4-3〜6のDB対応定義・admission・実行履歴を既存schedulerへ接続する。

## 範囲と境界

既存のinterval／daily／once／weekly／monthly計算、単一ホストtick、SQLiteを再利用する。
Jobの保存引数、Stage scan、変更確認付きMart更新、読取SQL alertとWorkbench内通知を対象にする。
予定は既定無効。DB・owner・保存role・対象revision・実行意図を保存し、編集と失効・失敗・UNKNOWN後の再開は明示操作とする。
新scheduler、待ち行列、任意command、cross-DB、外部通知、P4-8 retireは含めない。

## 実装分担

- backend worker：`db_schedules`、control store migration、既存Job／Stage／Mart受付・編集との原子的な連携、既存tick、focused試験。
- 入口worker：通常schedule／alert HTTP adapter・CLI・Workbenchとfocused試験。UIは既存token／componentを継承する。
- Commander：差分レビュー、統合gate、専用Shirokuma実機への適用、通常CLI／browser、grant失効・有限清掃・証拠照合。

両workerはADR-0036の`codex-cli gpt-6.1-sol medium`で有界に実行する。
既存変更・履歴・FENCED_READ_ONLYを保持し、GitHubへの公開・pushは行わない。

## 共通API契約

`POST /api/v1/schedules`は`context,target_kind,target_id,target_revision,expected_revision,spec,intent`を受け、
任意の`enabled`は既定false、更新時は`schedule_id`とrevision CASを要求する。idempotency keyはheaderで指定する。
`spec`は既存の`kind,every_minutes,time,timezone,date,weekdays,day_of_month`を使用する。
`intent`はJobの`{arguments:{}}`、Stageの`{action:"scan"}`、Martの`{action:"refresh",check_changes:true}`。
`GET /api/v1/schedules`、`/{id}`、`/{id}/ticks`と、`GET /api/v1/alerts`、`POST /api/v1/alerts/{id}/ack`を提供する。
全入口で9項目contextを照合し、owner／DB／保存roleの境界を保持する。

## 必須証拠

3DBで実際の予定発火と子SQL・実値を照合する。同時tick／手動競合／再起動、予約後編集、遅延と追いかけ実行なし、
無効定義のfault isolation、drain、Stage内容dedup、Mart変更なし／あり、alert TRUE／FALSE／NULL／多行／失敗を確認する。
grant失効は停止・取得拒否・再付与後の明示再開まで確認し、通知をFALSE扱いで解消しない。
TZ／DST／月末は時計境界回帰と実tickを区別する。Workbenchは元SQLタブ、draft／focus、遅延応答、DB／owner分離を確認する。
focused試験、全体resident、Go、設計検査、doctorと実SQLを通し、今回所有物だけの清掃と最終台帳照合を記録する。
