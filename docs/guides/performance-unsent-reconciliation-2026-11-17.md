# 公開初期POSTのTCP接続拒否（送信前）をオフラインで確定する手順とc01結果（2026-11-17）

## 結論

c01の`authz-cmp-288464-public-0`は、公開初期POSTのTCP connectが送信前に拒否された
ことを一次証拠から確認し、既存のUNKNOWN attemptと`reconciliation.json`を1 byteも
変えずに、別の追記専用`not-sent.json`で **FAILED / NOT_SENT** と確定した。
`gate-status`は本番gateと同じ`inspect_campaign`経路で `ok=true`、他runの有効状態は
不変である。クエリ送信・再試行・follow-up・応答は無く、成功結果へ読み替えない。
c02の24 runは観測データのままで、遡及受入しない。

## 判定条件（transportの意味）

- `benchmarks/local_perf/runner.py`の`TrinoAdapter.request`は、
  `connect`を`conn.connect()`、`send`を`conn.request(...)`の周囲で測る。
- `Timings.measure`は例外時も`finally`でphase値を書く。したがって
  `connect`だけが非nullで`send`/`header_wait`/`body_read`/`decode`がnullなら、
  `conn.request`へ到達していない。`ConnectionRefusedError`はTCP connect自体の
  拒否なので、HTTP request bytesは送信されていない。
- 対象attemptは次を満たす必要がある。
  - `status=UNKNOWN`、`handle` keyが存在してnull、`submission_rejection`無し、
    `result.jsonl`無し、`recovered_handle`無し。
  - `error_type=ConnectionRefusedError`、`error_code=PUBLIC_QUERY_UNMET`、
    `initial.calls=1`、`follow_up.calls=0`。call数はboolでない整数であること。
  - `connect`は正、`send`/`header_wait`/`body_read`/`decode`はnull、
    `initial.total >= connect`、`follow_up`の全phaseはnull。
  - `dispatch_started`、`recovery_started`、`recovery_complete`、
    `attempt_complete`は正の順序、terminal/response/backend観測は無し。
  - SQL SHA256、凍結manifest、dataset/snapshot、adapter identity（account、
    selected_role、9項目context、http loopback origin、all-results）、
    run/campaign所有、timeout、UNKNOWN reconciliationのscopeが一致する。
- 次の場合はfail-closedでreceiptを作らない。送信済みphase、複数initial call、
  follow-up、timeout、ConnectionReset等の曖昧なnetwork error、403
  `SQL_PERMISSION_DENIED`、terminal handle/recovered handle、応答・結果file、
  scope/hash不一致、必須field欠落。
- DB行の不在だけでは解錠しない。transport proofを必須とし、process終了または
  read-only state scanの独立corroborationを最低1つ要求する。c01は両方を用いた。

## ゲートの記録照合（レビュー修正）

- `inspect_campaign`は`run.json.attempts[]`の各`path`と`request_id`を、実際に
  発見した`*/attempt.json`へ照合する。記録されたfile/directoryが無い、IDが
  一致しない、pathが不正・重複、`attempts`が無い場合はblockerにし、dispatchしない。
  `planned_attempts`がある場合は`attempts + pending + not_run`の会計も照合し、
  記録とfileを同時に消した場合の欠落を検出する。
- 記録に無いattempt fileは従来どおり検証する。UNKNOWN等ならblockし、terminal
  receiptやNOT_SENT sidecarの検証も省略しない。`not_run_attempts`の未実行tailは
  fileを要求しない（intentional NOT_RUNと通常のrunner lifecycleを維持）。
- 元attemptが無い、またはsidecarが無い状態でUNKNOWNが残る場合は解錠しない。
- 空の`attempts`はpre-dispatch失敗として許容するが、記録されたattemptの欠落は
  たとえrunがFAILED/SUCCESS表記でも許容しない。

## 部分失敗の再試行

- corroborationはattempt directory内のhidden stagingへ書き、全検証後に
  `not-sent-evidence/capture-<id>/`へatomic renameし、最後にreceiptを書く。
  receipt本体もtemp fileへfsync後にhard linkで公開し、途中終了でpartialな
  `not-sent.json`を残さない。
- 誤ったPID、state一致行、server log後続entry等で最初の確認が失敗しても、
  fixed-name evidenceを公開しない。同じcaseで引数を直して再実行でき、元の
  attempt/reconciliation/既存receiptを削除・書換えする必要はない。
- 公開済みcapture directoryとreceiptは不変。既存c01 receiptは
  `not-sent-evidence/process.log`等の元pathのままbyte-identicalで再検証される。

## オフライン手順

1. 変更しないattempt/reconciliation/run/queries/campaignのSHA256を記録する。
2. macOS unified logから対象Workbench PIDの終了を取得する。
   `log show --start ... --end ... --style compact --predicate 'eventMessage CONTAINS[c] "<PID>"'`
3. Workbench access logと、read-onlyで開くauthoritative state DBを用意する。
   state scanは`mode=ro`・`query_only=ON`・単一transactionで、request_id/
   worksheet_idの全table一致、失敗時刻±60秒のquery record/admission、
   execution contextを数える。書込み・checkpoint・service再起動はしない。
4. 同じ公開connection file（account/role/context/origin）でreceiptを作る。

```sh
rtk proxy python3 -m benchmarks.local_perf confirm-not-sent \
  /absolute/run/attempt.json --adapter public \
  --connection-file /absolute/public.json \
  --process-log /absolute/unified.log --process-pid <PID> \
  --utc-offset-seconds 32400 --server-log /absolute/server.log \
  --state-db /absolute/jobs.sqlite3 --state-window-seconds 60 \
  --historical-evidence /absolute/prior.json

rtk proxy python3 -m benchmarks.local_perf gate-status /absolute/campaign
```

`confirm-not-sent`はsocketを開かず、SQLを送らず、attempt/reconciliationを書き換えない。
`not-sent.json`は`open(O_CREAT|O_EXCL)`の追記専用で、同じ入力の再実行は既存bytesを
そのまま返す。`gate-status`は`run`と同じ`inspect_campaign`を呼び、dispatchしない。
gate読取り時はattempt/reconciliation/run/queriesのhash、manifest/SQL/snapshot、
campaign ID/root/run所有、process log、server log、state scan、prior evidenceを
再検証し、すべての判定を再導出する。campaign.json全体のhashはmint時のprovenance
として保持し、その後の無関係なrun追加だけではreceiptを無効化しない。

## c01の適用結果

- 対象: `/private/tmp/shirokuma-authz-reads-20261117-c01/campaign/authz-cmp-288464-public-0/table_store_sales-0`
- request_id: `b0320a0d-fcf7-4964-a50a-66ea13c449f9`
- worksheet_id: `de47bff5-1933-4943-9a47-dd66835347f7`
- sidecar: `not-sent.json` SHA256 `33b42d8d6f0f6a4b007b698f10604fd65e6ab289708ea3d02ecd10fbeeaef780`
- original attempt / reconciliation: 両方 `fe8a548a8b533c00b78dc52ca2a454cf36386ab5cb38f48236b314da53646bf1`
  のまま（適用前後で一致）。
- transport: `initial.calls=1`、`connect=0.000936秒`、`send/header_wait/body_read/decode=null`、
  `follow_up.calls=0`、`dispatch_started=0.355732秒`、`recovery_complete=0.357131秒`。
- process: PID 39756の`termination reported by proc_exit` 2026-11-17 07:24:10.203 JST
  （attempt開始の7.283秒前）。unified logの生出力を
  `not-sent-evidence/process.log`に保存。
- server: 最終entry 07:21:59 JST、attempt以後のentry 0、`POST /api/v1/queries` 0。
- state: 79 table、request_id/worksheet_id一致0、失敗時刻±60秒のquery record 0、
  admission 0、execution context 0。DB SHA256
  `0be63e04ed0e5e55edb89397c6537df2f1329b19d6bfa931d29550c35942184b`。
- `gate-status`: `ok=true`、`authz-cmp-288464-public-0`の`stored_status=UNKNOWN`、
  `effective_status=FAILED`、`reconciliation=NOT_SENT`、blockers 0。他の11 attemptは
  SUCCESSのまま。c02の24 runはSUCCESSのままで、`not_sent` 0、遡及受入なし。
- 元の`reconciliation.json`はUNKNOWN sidecarのまま保持し、reconcile再実行も
  sidecarを返してHTTPを出さない（テストで`request`未呼出しを確認）。

## 保証・限界

- 保証するのは「このclient processの初期POSTがTCP connectで拒否され、HTTP bytesを
  送っていない」こと。他client・他processの同時要求やTrino全体の履歴は監査しない。
- PID 39756とWorkbench commandの対応は同時刻の`ps`/`source-deployed.json`証拠に依拠し、
  unified log自体はprocess終了のみを示す。gateはこの限界をreceiptに保持する。
- state scanはcapture時点のread-only snapshotであり、全体のcluster状態や現在の
  query healthを証明しない。DB行の不在は補強であり、単独の解錠条件ではない。
- 追記専用receiptはlocal filesystemのtrustに依存し、暗号署名ではない。改竄・
  流用・scope不一致・terminal handle・403・応答・再試行はfail-closedで拒否する。
- 本作業はオフライン確定とgate読取りのみ。性能run、SQL送信、data生成、service再起動、
  c01/c02の再開は行っていない。

## 検証

- `make test-local-perf`: 21 suite / 461 tests、失敗0（既存skipのみ）。
- `make verify-local-experiment`: 162 suite / 5,991 tests、失敗0。
  `make verify-design-context`: exit 0（documents=94、issues=10、references=42）。
- 追加したfail-closed試験: 送信/retry/follow-up/response/timeout/reset、terminal handle、
  403 receipt、result file、process時刻/PID不一致、state一致行、server後続entry、
  scope/hash/field改竄、別attemptへのsidecar流用、無関係UNKNOWNの残存block、
  run.json記録attemptのfile/directory欠落・ID不一致・不正/重複path・attempts欠落、
  planned/pending/not_run会計不一致、orphan UNKNOWN attempt、handle key欠落、
  bool/float call数、初回capture失敗後の再試行。
- `gate-status` c01: `ok=true`、blockers 0。`gate-status` c02: 24/24 SUCCESS、
  `not_sent` 0、blockers 0（観測データのまま）。
- 元attempt/reconciliation/campaign/run/queriesのSHA256は
  `/tmp/shirokuma-unsent-reconcile-qqp60du_/before-artifact-hashes.json`と一致。

## 証拠

- sidecar/evidence:
  `/private/tmp/shirokuma-authz-reads-20261117-c01/campaign/authz-cmp-288464-public-0/table_store_sales-0/not-sent.json`
  と同`not-sent-evidence/`（`process.log`、`server.log`、`state-scan.json`、prior evidence）。
- 作業・capture: `/tmp/shirokuma-unsent-reconcile-qqp60du_/`（`capture/unified-39756.log`、
  `capture/confirm-result.json`、`capture/idempotent-result.json`、
  `capture/test-local-perf.out`、`capture/c02-gate-status.json`）。
- 既存review証拠: `/tmp/shirokuma-authz-reads-j15lv6wk/evidence/`（`review-followup-note.md`、
  `failed-request-state-scan.json`、`failed-request-process-log.txt`、
  `failure-reconciliation.json`、`source-deployed.json`）。
