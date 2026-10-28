# read-only JobのUNKNOWN実行を閉鎖する運用境界（2026-10-21）

backendの終端記録を失ったQueryは、結果を成功・失敗・取消と推測できない。
本境界はread-onlyのUNKNOWN Jobを放棄して新規受付を回復する。modeは永続graphからserverが決定し、callerは指定できない。
終端記録がなければ`FENCE`で管理対象backendの停止を正に証明し、Query結果`UNKNOWN`と実行閉鎖`FENCED_READ_ONLY`を別に保持する。
正確な子が通常の応答・reconcile・元pinのcancel ACKで既に終端していれば`TERMINAL_PARENT`で親だけを放棄する。子の実際の結果・行・full9を保持し、QueryにFENCEDを付けない。
両modeとも親DataOpsとJobはFAILEDに閉鎖し、SQL再送・後続step・結果の再構築は行わない。

## 適用条件

- 現在もplatform administratorであり、選択roleとrole上限がadminのoperatorだけが実行する。
- immutableなrun定義、step、DataOps、Query、admissionの永続リンクと元の本人・DB・contextを照合する。
- root・DataOps・Query admissionの非NULLな`database_id`はrun contextのDB IDと完全一致させる。legacy NULLはcanonical run/operation/queryのfull9・pin・永続リンクでscopeを証明できる場合だけ許容し、DBの補完・名前fallback・NULLの書換えは行わない。
- 不確定な子はすべて`job-sql`のread-only SQLまたはBoolean alertであり、既存のread-only・同一DB検証を再実行できること。
- 不明な子、別context、write不確定、Ingest/WAP/Exportの不確定な子、無関係な未完了admissionは拒否する。既知のcommitは保持する。
- このadapterは管理対象の単一Trino Containerがcoordinatorと全workerを兼ねる構成に限定する。分散・外部backendには適用しない。

## FENCEの順序（Query終端記録なし）

1. 実stateの一貫backupとread-onlyな事前照合を保存する。旧stateの復元でblockerを隠さない。
2. 通常の管理者認証でplanを作成する。現行profile・適用世代から対象scopeを導出し、Engine、Container、Pod、Service、開始時刻、restart policy、全workerの包含を検証する。
3. plan revisionと本人ごとの冪等キーを使用する。既存operator/Job/子処理の所有権とlifecycle gateを取得し、再照合後にDRAINING所有権と停止intentを先に永続化する。
4. 正確な旧Containerを停止する。IDとEngineを含む停止済みinspectの証跡を**削除より先に永続化**し、その後にvolumeを削除せず旧Containerを除去する。
5. 証跡を参照する同一transactionで対象Queryの実行閉鎖と親の放棄を記録する。過去のQuery結果はUNKNOWNのまま保持する。
6. 新Containerの正体とreadiness、無関係なblockerの不在を確認してOPENへ戻す。通常入口で新規実行と失効後の取得拒否を検証する。

## TERMINAL_PARENTの順序（正確な子が既に終端）

UNKNOWNのread-only `job-sql`親を少なくとも1つ含み、全子のQuery/admissionが同じFINISHED・FAILED・CANCELEDで、backend ID・完了時刻・元SQL・full9・永続リンクを照合できる場合だけ適用する。
UNKNOWNからの終端は通常経路の正のevidenceを必要とする。ACTIVEからの通常終端はevidence_refなしでも有効であり、ref欠落だけから過去の遷移を推測しない。
同じ4コマンドでplanを照合し、全子のquery-owner leaseを取得する。applyはDRAININGと型付き終端証跡をPROVEDとして先に保存し、親だけをFAILED、pending stepをSKIPPEDにする。
同じ管理対象Container・binding・世代・閉鎖graph・現在operator・drain所有権を再検証してOPENへ戻す。SQL、backend停止・削除・置換health確認、子の結果変更は行わない。

両modeともownerのgrant失効・epoch変更時は、**binding・適用世代を変更せず、現在も認可された別のplatform adminが選択adminで先にrecoverする**。
元のrun context・pin・旧grant epochは置き換えない。ownerのepoch喪失と親の放棄は結果取得・submitの権限を与えない。
pending regrantは回復によって有効化されない。回復完了**後**に検証済みfull applyを経るまで昇格は無効であり、旧contextの権限復活にもならない。

両modeのscopeはoperatorが現行構成と観測から確立する。過去のadmissionがContainer UIDを保存していたという主張はしない。
実機では同一Containerの開始が受付以前であり、再起動0、Serviceの唯一のEndpoint、単一coordinator兼workerであることを確認する。

## operatorコマンド

`catalog_admin.py recover-job-{plan,inspect,apply,reconcile}`をDataOps用Pythonで実行する。
通常APIには強制閉鎖の入口を追加しない。設定JSONは0700のディレクトリ内に0600で保存し、次の5項目だけを指定する。

```json
{
  "version": 1,
  "auth_headers": "/absolute/private/admin-headers.json",
  "runtime_profile": "/absolute/current/runtime-profile.json",
  "docker_context": "colima-mac-studio-solo",
  "kube_context": "colima-mac-studio-solo"
}
```

`auth_headers`は既存の通常管理者認証と選択admin roleを含むprivate JSONを参照する。
Container IDや成否の証拠を設定に渡すことはできない。scopeと証跡は実装が観測する。
実行processの`KUBECONFIG`には、事前照合した専用clusterのprivate kubeconfigを明示する。ホストの設定は更新しない。

```sh
rtk proxy env KUBECONFIG=/absolute/private/kubeconfig tools/local_experiment/dataops/.venv/bin/python3 tools/local_experiment/catalog_admin.py recover-job-plan --config /absolute/private/recovery.json --run-id RUN_UUID --idempotency-key RECOVERY_KEY
rtk proxy env KUBECONFIG=/absolute/private/kubeconfig tools/local_experiment/dataops/.venv/bin/python3 tools/local_experiment/catalog_admin.py recover-job-inspect --config /absolute/private/recovery.json --operation-id OPERATION_UUID
rtk proxy env KUBECONFIG=/absolute/private/kubeconfig tools/local_experiment/dataops/.venv/bin/python3 tools/local_experiment/catalog_admin.py recover-job-apply --config /absolute/private/recovery.json --operation-id OPERATION_UUID --expected-revision INSPECTED_REVISION
```

FENCEの実機plan前に、旧schemaのresident moduleを残さないよう、所有を照合した通常サービスを停止する。
planのrun・本人・DB・世代・Engine/Containerのscopeを照合してからapplyする。
出力はprivateな証跡へ保存する。FENCE後の通常サービス再開・世代照合は別の実機受入で確認する。TERMINAL_PARENTはbackendを再起動しない。

## 中断・再実行

同じoperationと冪等キーでinspect/reconcileする。reconcileは観測だけを行い、新たな停止を送信しない。
PLANNEDのreconcileは両modeとも変更しない。TERMINAL_PARENTのPROVED/CLOSEDは保存済み証跡とgraphを再照合して閉鎖・再OPENを完了できる。modeのない旧intentはgraphを変更せずFENCEとして再実行する。
停止前に中断し、同じ旧Containerが現在も稼働中と正に確認できる場合に限り、operatorが最新revisionを指定したapplyで停止を再開できる。
その際も本人・scope・子処理・DRAINING所有権を再検証する。保存済みの停止証跡があればそれを使用し、SQLや停止対象の選択をやり直さない。
停止証跡が未保存なら、同一Engine・正確な旧Containerの停止済みinspect、または実際のdie/destroy eventでのみ補える。
Docker eventは永続journalではなく、daemon再起動や保持上限で失われ得る。正の証拠を回復できなければDRAININGを維持する。
Container不在、queryの410、一覧不在、経過時間、PID、新backendのreadinessだけでは閉鎖しない。
現在のoperatorの認可喪失はplan・再開・OPEN移行を拒否する。これは上記のrun ownerのgrant喪失と区別する。
正の停止証跡が利用不能、unsupported/write/foreign/曖昧なwork、CAS不一致、binding・適用世代・runtime変更、対象不一致ではblockerと開始済みのDRAININGを保持する。

共有lifecycle gateの取得後にもadmission閉鎖を検証し、gate待ちの旧POSTが新backendへ送られる競合を防ぐ。
FENCEでは遅延したstate更新、cached fetch、通常reconcileでも閉鎖を取り消さず、旧結果を提供しない。

## 実装・実機記録

実装と受入記録は[P4-3報告](database-p4-3-jobs-manual-adapter-report-2026-10-21.md)に集約する。
この文書の運用境界だけで実機復旧・P4-3受入の完了を意味しない。
