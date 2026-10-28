# DB対応の予定実行とWorkbench内alert

P4-7の予定は、保存済みJob・Stage・管理Martを対象にする。既存schedulerのinterval、daily、once、weekly、monthlyを使う。
予定は無効の状態から保存し、対象のownerと保存roleで明示的に有効化する。

## Workbench

Jobs、取り込み・ステージ、データ変換でDB・Schema・実行roleを選び、保存済みの対象定義を読み込む。
予定欄で間隔またはカレンダーを設定し、「無効のまま保存」後に「スケジュールを有効化」を押す。
次回実行、対象定義と予定のrevision、停止理由、実行履歴を同じ欄で確認できる。
Stageは内容dedup付きscan、Martは監視sourceが変化した場合の全件更新を行う。
アップロードを必要とするJobは予定へ登録せず、Stageを使う。

Jobsの通知一覧は、現在の本人・DB・保存roleに属する通知だけを表示する。
確認済み操作と条件解消は別の状態であり、確認済みにしてもTRUE条件は解消しない。
alertはJobのstepとして保存する。例えば`{"name":"condition","action":"alert","sql":"SELECT true"}`は、
手動または予定実行の成功時にそのJob・版・step・引数に属する通知を記録する。

## 通常CLI

認証を含む既存のprivate client profileを`SHIROKUMA_RUNTIME_PROFILE`で指定する。
既定の通常入口は`http://127.0.0.1:8080`。Workbench入口を使う場合は`--api-url`を明示する。

```sh
rtk proxy shirokuma schedule list --database sales --schema raw
rtk proxy shirokuma schedule save --database sales --schema raw --definition schedule.json --idempotency-key unique-save-key
rtk proxy shirokuma schedule show --database sales --schema raw --schedule-id <schedule-id>
rtk proxy shirokuma schedule ticks --database sales --schema raw --schedule-id <schedule-id>
rtk proxy shirokuma alert list --database sales --schema raw
rtk proxy shirokuma alert ack --database sales --schema raw --alert-id <alert-id> --idempotency-key unique-ack-key
```

`schedule.json`の新規Job予定例：

```json
{
  "target_kind": "job",
  "target_id": "<saved-job-id>",
  "target_revision": 1,
  "expected_revision": 0,
  "spec": {"kind": "interval", "every_minutes": 60},
  "intent": {"arguments": {}},
  "enabled": false
}
```

更新は同じ対象と`schedule_id`、最新の`expected_revision`を指定する。
有効化はその保存済み予定を`enabled:true`で明示更新し、停止は`enabled:false`で更新する。
Stageは`target_kind:"stage"`と`intent:{"action":"scan"}`、Martは`target_kind:"mart"`と
`intent:{"action":"refresh","check_changes":true}`を使う。MartのSchemaは`marts`を選ぶ。
カレンダーの`spec`には`time:"09:00"`とIANA `timezone`を指定し、onceは`date`、weeklyはISO `weekdays`、
monthlyは`day_of_month`（1〜31または`"last"`）を追加する。

## 停止と回復

各発火は予定ID・予定回・予定revision・対象revisionに結び付く。予約後に対象や予定を編集すると、古い予約は受付されない。
対象編集、grant失効、role・binding変更、実行失敗、UNKNOWNによる停止後は、原因を照合して明示的に保存・有効化する。
grantを再付与しただけでは再開しない。遅延と競合は履歴へ記録し、過去の予定を追いかけて実行しない。

応答喪失時は元context・idempotency key・予定IDを保持し、一覧・詳細・tickと元runを照会する。
受理済みrunやUNKNOWNを新しいkeyで再送しない。実行結果と物理終了を確認できない場合は、既存のreconcile手順で照合する。
alertは読取SQLの1行1列・NULLでないBooleanだけを受け付ける。NULL、多行、SQL失敗、権限喪失をFALSEとして扱わない。
