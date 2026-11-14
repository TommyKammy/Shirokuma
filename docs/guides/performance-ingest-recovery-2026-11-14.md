# 性能試験の準備: 旧取込の実行終了と失敗時の清掃

2026-11-14 JST。[試験計画](performance-comparison-plan-2026-11-13.md)のP1として、失敗したatomic取込の後片付けと、清掃だけがUNKNOWNになった旧取込の明示的な放棄を整備した。

## 復旧の契約

非公開operator CLI `tools/local_experiment/catalog_admin.py` の `recover-ingest-plan` / `apply` / `reconcile` / `inspect` を使う。platform adminかつ元のactorであり、元取込の選択roleもadminであること、現在の権限・context・backend identity・generation、親子の全intent/admission、所有stageのobject/incarnation/UUID、target claimを照合する。

対象はmanual-ingest・atomic・未commitで、load失敗が確定し、UNKNOWNがその取込の所有stageに対する正確なfailure-cleanup DROPだけの場合に限定する。commit/publishや別SQLの成否が不明な取込は対象外。planのrevisionとintent digestをapply時にも照合する。

元のqueryのUNKNOWN、backend ID、SQLと元の取込receiptを保ち、execution closure `FENCED_STAGE_CLEANUP` を別に記録する。親は明示的な取込放棄としてFAILEDへ移し、claimの履歴を残す。通常のclaim再取得は既存のterminal-owner規則で行う。

旧時点のcontainerを現在のreplacementと取り違えない。既存のlogical分散復旧adapterで、現在の全実行可能backendを調査し、producer/query leaseと排他的lifecycle gateを保持して、coordinatorとworker双方の新しい停止証明を永続化する。旧operator receiptは補足証拠にとどめ、履歴上のDROP成功/失敗やデータファイル削除の証明には使わない。

private configは既存recover-queryと同じversion 1形式で、auth_headers、runtime_profile、docker_context、kube_contextを指定する。auth_headersはAuthorizationとX-Shirokuma-Roleの2項目を持つprivate JSONとする。認証ファイルとconfigはGit外に置く。

```sh
rtk proxy tools/local_experiment/dataops/.venv/bin/python3 tools/local_experiment/catalog_admin.py recover-ingest-plan --config /private/operator/config.json --ingest-operation-id <ingest-id> --idempotency-key <fresh-key>
rtk proxy tools/local_experiment/dataops/.venv/bin/python3 tools/local_experiment/catalog_admin.py recover-ingest-apply --config /private/operator/config.json --operation-id <recovery-id> --expected-revision <revision> --expected-intent-digest <digest>
rtk proxy tools/local_experiment/dataops/.venv/bin/python3 tools/local_experiment/catalog_admin.py recover-ingest-inspect --config /private/operator/config.json --operation-id <recovery-id>
```

applyが中断した場合はinspectでphaseとrevisionを読み直し、reconcileで観測する。元のINSERT/DROPを再送しない。停止証明やreplacement readinessが未確定ならdrainを保持し、同じ復旧intentから明示applyで継続する。

## 失敗時の清掃

公開manual-ingestの失敗時にも、成功commit後と同じUUID検査付きの非purge catalog登録解除を使う。全childが確定済みの場合だけ開始し、元loadがUNKNOWNなら開始しない。取込先や他operationのstageを登録解除しない。

元の失敗とloaded/accepted/committed件数を保存してから、送信前にcleanup=unknown / cleanup_phase=SENTを永続化する。正確な204受領と台帳更新の両方が成功したときだけACKNOWLEDGEDとし、object bindingを退役する。応答喪失・ACK台帳失敗はSENT/unknownを保持して自動再送せず、送信前の拒否はREJECTEDにする。非purgeなのでdata file保持を容量解放に数えない。

## 検証

| 検証 | 結果 |
|---|---|
| 台帳移行 | 実v23台帳のコピー79表・21,287行でv24へ移行。全行・rowid・FK・索引・trigger・履歴を保持し、整合性検査と再openの不変性を確認。本台帳はWorkbench停止・backup後に移行した |
| 旧UNKNOWN | 元operation `9176021e-8e25-44f0-85b3-c48ab1ec1a1c` を明示放棄してFAILED。cleanup query `5cf077e3-f763-4acc-81fc-17c50c504f58` はUNKNOWNのまま、execution closureだけを記録。元10子のquery/SQL/context、receiptと0 commit、target claimを保持 |
| 実行終了 | 復旧operation `c81bb6f3-322a-4efe-bcd1-1cdc859af29a` は両memberの停止証明とreplacement healthを確認してOPEN/revision 5。未終結の実行admission、ACTIVE/CANCELING/UNKNOWN取込はいずれも0 |
| 旧stage | 所有stage `dop_963d0d85b8979f41e628e187` のUUID・snapshot・所有者を再確認し、別operator receiptで非purge解除。ACK204・catalog404・binding退役を確認。元receiptのcleanup=unknownと旧benchmark journalは書き換えない |
| 途中失敗の実機 | 通常preview/ingest APIで2行をbatch 1で処理。先頭1行のload後、次の長いVARCHARを既知のQUERY_TEXT_TOO_LARGEで拒否。FAILED、accepted 2/loaded 1/committed 0、非purge cleanup ACK204、stage404、target不在を確認。OOMやserverの障害注入は使わない |
| 通常再取込 | 同じtarget名へ新しいkeyで通常createし、claimを通常規則で再取得。FINISHED/commit 1、成功後cleanup ACK204、公開SQLのCOUNT=1を確認 |
| 保護対象・構成 | 旧targetは45,539行の同じsnapshot、採用TPC-DS全24表はUUID/schema/snapshot/metadata pointer不変。元campaign等の保護対象1,901ファイルのbytes、元profile/ローカルGitOps差分、baselineのConfigMap/Deployment仕様を保持。Workbenchは正確な起動argv/envと9項目contextで復帰し、doctor healthy 46項目 |
| 重点試験 | 失敗cleanup 14件、復旧17件、v24/platform 44件を確認。既存query/dbt recovery 156件・logical adapter 45件も成功。主失敗/途中投入、応答喪失、所有者/権限/UUID差替え、CAS、未連結intent、部分停止、後続復旧との共存を検証 |
| 全体回帰 | `make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3` は157群・5,626成功・39 skip・失敗0。ソース固定後の単一実行で確認 |

39 skipは既定の実機opt-in、OPA/pyarrow、任意benchmark依存/既存source指定の未設定による。今回の復旧17件・失敗cleanup14件はskipなし。設計整合・改行・空白・秘密情報ファイル名検査も成功した。

実機では、停止直後のDocker censusとKubernetesのcontainer情報の反映差を安全側に拒否し、STOP_INTENT/DRAININGのまま中断した。同じintentから未停止memberだけを継続し、reconcileで両memberの証明を永続化した後、明示applyで受付を再開した。replacementを新たな停止対象にせず、元のINSERT/DROPを再送していない。中断記録も採用証拠へ含める。

旧campaignのUNKNOWNは原本として保存し、実行終了とステージ清掃の補足receiptと合わせて読む。旧campaignへの自動再投入は行わず、採用済みの別campaignと固定snapshotを維持する。今回の清掃はcatalog登録解除であり、未参照data fileを残す。物理回収は別の処理とし、追加占有2TBの計上から除外しない。

証拠はGit外の `shirokuma-ingest-recovery-2026-11-14` に保存した。`migration-preflight/receipt.json`、`operator/plan.json`・`reconcile.json`・`apply.json`・`inspect.json`、`oldstage-cleanup-*.json`、`failed-cleanup-acceptance-cea1a747`、`verification-summary.json` と全体回帰logを参照する。元のbenchmark入力・結果は保持した。検証時host空きは約3.102TBで、この復旧作業時点ではL1/VM拡張や正式な資源profile適用は行っていなかった。

## 次の順序

coordinatorの正式資源設定は[後続作業](performance-coordinator-profile-2026-11-14.md)で整備・適用した。次はbulk取込、公開全結果取得、残るTPC-DS variant/scaleを先に整え、大規模負荷・VM拡張へ進む。追加物理占有2TB・host予備256GiBを維持する。
