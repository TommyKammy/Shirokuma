# P4-0開始確認・P4-1管理mutation境界と通常grant API/CLI

2026-10-21。[Phase 4計画](database-phase4-implementation-plan-2026-10-20.md)のP4-0/1を対象とする。
P4-0開始確認とP4-1の実装・限定技術受入を完了した。
管理UIと表示名/R03はP4-2、Phase 4全体の縦断・clean環境はP4-9、所有者評価はPhase 5へ残す。

## P4-0の開始確認

明示active profileのcontrol storeをSQLite URI `mode=ro`で確認し、一貫性backupを取得した。
開始時はSALES / FINANCE / `insights_e88387a5`の3DBがREADY、binding desired/appliedは1、
両通常入口とLOCALの適用世代は`p26-live-redeploy`、WarehouseはOPENだった。
非終端query/DataOps、ACTIVE/UNKNOWN admissionは0だった。

現在bindingに結ばれたfull APPLIED Operation、verify成功receipt、plan manifestのprincipal/DB/role/revisionを照合した。
5grantのうち4件は完全一致し、`sales-only` SALESのdesired revision9だけが適用snapshot revision1と不一致だった。
移行ではこの1件をpendingにし、backend検証済みfull applyまで認可しなかった。
既存copyと空stateでversion9→10、integrity、再open後のepoch/provenance保持を確認した。

model照合markerは全体で3件を保持する。P3-5報告の1件は同作業の所有範囲であり、
今回その1件だけを全体件数とみなさず、過去P3-3の2件も開始時証拠に含めた。

## 実装した契約

- `grants`はdesired、`effective_grants`は実効role・永続epoch・適用revision/世代を保存する。新規/昇格はpending、剥奪/降格はCAS・Operation・監査と同一transactionで即時制限する。
- epochを通常contextの`grant_revision`へ公開する。revoke/regrant・再起動で初期化せず、Worksheet/native/DataOpsの受付・継続・結果・履歴・Export取得で照合する。元pinのowner取消は維持する。
- full applyは世代固定のDB×role credentialを使ってbackendを検証し、最終registry再照合後、APPLIEDとeffective昇格を同一transactionで確定する。bootstrap/PREPARED/reconcileだけでは昇格しない。
- 同actor/route/key・同payloadは同じOperation、異payloadは409。旧pending変更はSUPERSEDEDとなる。監査にdesired role前後、effective role、actor/対象、期待/確定revision、epoch、apply Operation/世代/receiptを記録する。
- 通常grant CLIは認証HTTPを使い、operator材料を要求しない。管理変更・grant Operation取得は現在のplatform adminと選択admin roleを要求する。global role ceiling/platform_admin属性は変更しない。

## 通常入口

| 入口 | 契約 |
| - | - |
| `GET /api/v1/databases/{database_id}/grants` | desired/effective role、desired `revision`、effective epoch `grant_revision`、適用provenanceを返す |
| `POST /api/v1/databases/{database_id}/grants` | 本文は`principal_id / maximum_role / expected_revision`、`Idempotency-Key`必須。新規CASは0、更新は現在の正のrevision。受付は202 |
| `GET /api/v1/operations/{operation_id}` | grant変更の状態・要求・監査を現在の管理権限で照会 |

`maximum_role`は`bi_viewer / data_engineer / revoked`。CAS競合・同key異payloadは409、
不正型/未知fieldは400、管理権限不足は403となる。

```sh
shirokuma database grant list --database-id DB_ID
shirokuma database grant set --database-id DB_ID --principal-id USER \
  --maximum-role data_engineer --expected-revision 0 --idempotency-key KEY
shirokuma database grant revoke --database-id DB_ID --principal-id USER \
  --expected-revision REVISION --idempotency-key KEY
shirokuma database grant operation --operation-id OPERATION_ID
```

認証は通常のruntime/client profile、接続先は既定Workbenchまたは`--api-url`を使う。
grant変更受付後は、desiredを含む新specをprivate operatorが明示plan/full applyする必要がある。CLI受付の202を付与完了と扱わない。
既存DB operator CLIもDataOps Pythonを選択するよう修正し、通常CLIから再planを実証した。
DataOps lockへ既存検証済みのboto3/botocore `1.43.114`を追加した。既存dependency pinの変更・削除は0。

## 実機受入

Workbenchの認証HTTPと通常Go CLIを実入口にし、実Trino/Polaris/S3に接続した。
通常入口での本人認可と、private DB×role credentialでのbackend確認は別の証拠にした。

| 条件 | 実測結果 |
| - | - |
| migration | 完全一致4件だけを有効化。不一致revision9はepoch9以上・pendingで、通常SQL/nativeを拒否 |
| 新規付与 | `finance-only`→SALESを不存在CAS0で受付。READY DBでもpending中はHTTP/native拒否、verified full apply後に成功 |
| CAS/再照会 | 同key同payloadのCLI再照会、同key異payload409、古いrevision409、2要求のCAS競合は成功1/競合1 |
| 応答喪失 | POST送信後に応答を受けず切断。保存済み変更を正照合し、同key CLI再照会で監査1件・変更1回 |
| apply中revoke | 実backend verify完了後に通常管理APIでrevoke。最終registry照合はVERIFIED_STALE、pending付与の昇格0。新specを通常CLIで再planし、full applyでAPPLIEDへ収束 |
| 同世代の降格 | engineerがviewer roleで作成したquery/Exportを保持してviewerへ降格。世代/binding不変でepochだけ進み、新viewer SQL/native成功、旧query inspect/page/native継続は拒否、旧history項目は非表示 |
| revoke | 新規SQL/native・history・Export metadata/content・DataOps取得を拒否。元pinのowner取消と他DBの許可を保持 |
| 再起動 | 同世代で通常両serverを再起動。revoked grant/epoch/provenance不変、新規SQL/nativeを引き続き拒否 |
| 再付与 | backend検証前はpendingで拒否。最終full apply後に新規SQL成功・旧context取得拒否。再起動/後続変更後の旧key再照会は元のSUPERSEDED Operationを返し、古い権限を復活させない |
| DB×role backend | 3DB×2roleの実Table読取6件成功、cross-DB読取6件はPERMISSION_DENIED。engineer RAW読取成功、viewer RAW読取/書込はPERMISSION_DENIED |
| 取得競合 | submit/fetch中revoke、履歴収集中revoke→regrant、受付transaction内epoch再照合をfocused試験で確認 |

backend identityはDB×role共有のまま。本人失効は通常/native入口の再認可で実現し、
共有secretのprincipal別失効を実装したという判定にはしない。

初回のsnapshot公開はoperator Pythonのboto3不足でclient作成前に停止し、UNKNOWNを保持した。
元receipt・traceと、upload関数最初のimportで停止したsourceを独立照合し、未送信確定の根拠を保存した。
coreの`confirmed_not_dispatched`と対応するprivate receiptを明示照合し、元記録を保存してから明示applyした。
以後は必要dependencyを含む固定環境で実行した。timeout/404/PIDだけで未実行判定する回復は行っていない。

## 検査・証拠

- `make verify-local-experiment`：1,260 tests、既存skip 32、成功。
- `go test ./cmd/... ./internal/...`、`make verify-design-context`、`git diff --check`：成功。
- 独立focused 108 testsと、sandboxで実行できなかったloopback試験を通常権限で再検証して成功。
- 通常doctor：healthy。最終3DBの通常SQLは各`[[41]]`、両通常入口は同じ最終世代。
- 独立した最終read-only照合：102項目すべてPASS、失敗0。

Git管理外の証拠は`var/shirokuma/p4-1-acceptance/`。
`p4-0-readonly-baseline.json`、`baseline-state.sqlite3`、`migration-copy-evidence.json`、
`live/*-evidence.json`、`live/state.json`、`root-broad-complete.log`に条件別の結果を保存した。
最終状態は`final-facts.json`、`final-doctor.json`、`live/cleanup-object-listings.json`、`final-sql.log`で照合した。
各generationのprivate operator receipt/実verifyは既存`var/shirokuma/p2-6-acceptance/live/`配下に保持する。
秘密値を本報告へ転載しない。

## 最終状態・持越し

最終世代`p41-live-final`はfull APPLIED、3DB READY、LOCAL OPEN、UNKNOWN/非終端実行0。
元の5grantは権限を回復・維持し、追加した`finance-only` SALESは剥奪・backend収束済みのtombstoneとして残した。
grant Operation8件はAPPLIED3件・SUPERSEDED5件で、監査・apply/verify receiptの対応を確認した。

所有Refを確認したTable4件をoperator経路で削除し、各resolveの404と12SchemaのCatalog一覧で残存0を確認した。
通常SQL入口のDROP拒否は維持した。所有Export1件のprivate fileと認証profile copy2件も清掃した。
Export監査metadata、epoch tombstone、過去の終端履歴、開始時のmodel照合marker3件は保持した。
P2-R01のAPI/CLI部分を実装し、管理画面はP4-2、Phase 4全体の残件closeoutはP4-9で行う。
