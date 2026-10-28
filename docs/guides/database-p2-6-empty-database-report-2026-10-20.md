# P2-6 空Databaseの作成・適用・再現の検証記録

2026年10月20日。P2-6の限定技術受入を完了した。通常環境は `sales` / `finance` / `insights_e88387a5` の3DBがREADY、Warehouse受付はOPEN。後続Phaseの実装には進んでいない。

旧 `iceberg` / `shirokuma_l1` 等のテストデータ・SQLite・定義・タブは移行していない。まず新しいcanonical SALES / FINANCEを作り、その後に作成した検証TableでDB追加・再配備時の保持を検証した。空環境への再構築は宣言の再現であり、Table・データ・UUIDの復元ではない。

## 実装した最小経路

- 認証済みplatform adminの `database create` / `POST /api/v1/databases`。登録profileは `local-polaris-1.8.0`。DB・不変binding・4標準Schema・作成Operation・Idempotency-Keyを同じSQLite transactionで登録する。APIは新規・冪等再送とも202。異なるpayloadは409、任意endpoint / Secret値は受け付けない。
- `export-spec` は現在のregistryからID・revision・grantsと既存Secret参照を出力する。`register-spec` は空registry限定で承認済みpublic specを再登録する。applied revisionは0、DBはREGISTEREDで開始し、object / query / 実データは取り込まない。
- bootstrap plan / 明示applyでDB別Polaris・Catalog・4 namespace・Trino用とIceberg bootstrap用の専用principalを準備する。PREPAREDで受付を閉じたまま停止する。既存DBのSP・S3キー・JWT鍵・passwordsを維持し、新DBの未発行SPだけを保留する。
- 発行済み材料からfull用Tofu saved planとprivate runtime profileを作り、新しいplanを承認して明示applyする。旧bootstrapの予約を同じtransactionで引き継ぎ、全構成・資格情報・実SQLを検証してAPPLIED / READY / OPENへ進める。
- Secret観測はGeneration共通の可変値ではなくOperationごとに保存する。PREPARED後の改版は物理DB集合・binding・Schema identity・profileを維持した明示再planに限定する。UNKNOWNや未確認の外部POSTを自動でやり直さない。
- PREPAREDを引き継いだfullが構成変更の送信前にタイムアウトした場合も、受付閉鎖と旧ownerを保ったまま再planできる。同じactor・元bootstrap・物理identityに加え、active attemptなし・構成stepすべてPENDING・publication未送信または成功receiptを条件とする。明示apply時に条件を再検証して原子的に予約を移し、旧fullをREPLAN_REQUIREDにする。処理中や結果不明のstepは引き継がない。
- Workbenchとnative SQL endpoint双方にadmin限定のruntime証明を設けた。すべてのfull適用・完了照合で同じ世代、state、backend / Catalog / CA / auth材料を確認する。private profileと参照ファイルのbyteをplanに固定し、起動後のファイル変更や片方だけ旧世代の状態では受付を開かない。
- 新規DB登録もWarehouse予約と同じSQLite transactionで調停する。閉鎖・予約中の新規登録は409、既存要求の冪等再送は維持する。
- Flux childは停止中のままSSAで構成を配布し、UID / resourceVersion付きpatchで停止解除してからReadyと実構成を確認する。SSAと停止patchの管理者名を統一し、通常Operatorにはforce-conflictsを追加していない。doctorも7 childの生成healthChecksから任意名DBを診断する。

control_storeはschema v5。private値は0600のoperator材料・receiptへ保存し、SQLite・通常API・GitOps snapshotには載せない。既存Jobs / query / admission / UNKNOWN契約は全体回帰で維持を確認した。

## 実機の経路と到達点

| 経路 | 結果 |
| - | - |
| 新canonical 2DB | `p26-live-initial` のbootstrap PREPARED → full APPLIED。SALES=`sales`、FINANCE=`finance`へクリーン切替 |
| 任意名の3番目 | 管理APIで `insights_e88387a5` を登録。同じ要求で同じDB / Operation、異payload409。READY前のSQLはWorkbench / nativeとも409 |
| 3DB適用 | `p26-live-three`、bootstrap `447a222c-bf4d-4ca9-b2f8-d76936e30b4e` → full `1985af47-c67f-489f-806f-d928cc6df9fc` がAPPLIED、3DB READY |
| 通常再配備 | `p26-live-redeploy`、full `bb395929-75d6-4a49-8b41-dfb816d91278` がAPPLIED。3DBの既存資格情報を保持し、生成構成だけで新世代へ適用 |
| 空環境 | 新規K3s・PVC・S3・PostgreSQLと空SQLite。public specとDB / Schema ID・grants・bindingをそのまま再登録。bootstrap `54eb7643-1b3c-43dc-a142-431cb1e4c369` → full `5613742c-4b33-4803-a389-7c114d0aee64` がAPPLIED、同じpublic `p26-live-three` の3DB READY |

3番目のDB IDは `b7633428-316e-43c8-9ade-ada606b11c9d`。各DBのservice / realm / Polaris Catalog / S3 prefix / keyは個別で、追加Catalogのaliasではない。生成した全7 child・Catalog / mount / Secret版・S3 server policy・Trino ACL / OPAをOperatorが照合した。

再配備では受付閉鎖とblocker 0を確認してから、Polaris 3、Trino、PostgreSQL、SeaweedFSの6 Podを検証用に再作成した。Pod UIDは全6件で変更、PVC UIDは不変。新世代の両通常入口を閉鎖中に起動し、実Operatorのfull検証後に受付を開いた。このPod再作成は再起動耐性の受入操作であり、通常applyが常に全Podを削除する仕様ではない。

## 成功証拠の範囲

| 検証 | 結果と範囲 |
| - | - |
| DB×roleの直接Trino | 222 PASS。各DBの正常読書き、viewerのMARTS読取 / RAW拒否、engineerのRAW / SANDBOX操作、cross-DB SELECT / INSERT / UPDATE / DELETE / CREATE / DROP拒否。実在するsnapshotでMARTS rollbackのviewer / engineer拒否・head不変、admin成功・行復元 |
| 通常HTTP / native | 通常G3、再配備後G4、空環境で各56 PASS。adminとsales-only / finance-only / 両DB本人のviewer / engineerによる一覧・session・SELECT 1の許可 / 拒否。HTTP経由全DMLの試験とは区別 |
| S3分離 | 通常・空環境で各33 PASS。DB専用キーのown GET / PUT / DELETE / LIST、他DBへの4操作計24件の403、検証objectの不変・後片付け。Copy / MPU等の全S3 APIを再試験した判定ではない |
| 2→3の保持 | 新canonical SALES / FINANCEのRAW検証Table各1件で、REST Table UUID・行・binding・4 Schema IDを保持（2 PASS） |
| 再配備の保持 | 3DBのRAW検証Table各1件で同じ項目を保持（3 PASS）。既存2DBのroot / JWT / S3 / 両SPとrole passwordsのbyteも一致 |
| 空環境のTable | 同じ宣言・同じ検証Table名から新規作成（3 PASS）。3 UUIDすべて通常環境と異なり、検証用SQLで新しい行を投入。旧SQLite / Table / データをコピーしていない |
| position-delete purge | 通常・空環境で各9 PASS。3DBともformat v2のposition-delete実在を確認後DROPし、そのTableのS3 prefixが0 object。手動S3削除を成功根拠にしない |
| OPA停止 | 空環境24 PASS。OPA containerだけをpauseし、停止前成功→停止中deny→finally resume後成功を確認 |
| stale plan | 空環境でplan後にSecretのresourceVersionを変更。旧fullはSTALE_SECRETS、bootstrapの予約・非READYを維持。新しいsaved planと明示full planから成功。grant / desired改版、private file変更、UNKNOWN時の引継ぎ拒否とtrusted照合後の再開はfocused回帰でも確認 |
| 未送信fullのtimeout後再plan | 独立temp ledger・別processの実flock・実Secret 21件のmetadata観測で24 PASS。timeout後の旧full owner / closed UNKNOWN保持、grant差分による旧plan拒否、fresh plan時の所有者不変、明示applyでの引継ぎと旧plan無効化を確認。外部step / verifyはno-op callbackで、K3s全構成applyとは区別する。gate / drain timeout、Secret差分、処理中・UNKNOWN拒否、競合も追加9回帰で確認 |
| 登録排他 | 閉鎖中の新DB作成409、元の作成要求の再送成功。最終通常HTTPの冪等再送202も確認 |
| 最終状態 | 通常・空環境ともTofu detailed-exitcode 0 / 構成差分0、doctor healthy、3DB READY・desired=applied、受付OPEN、両入口の世代一致 |

SQL 222項目には正常処理が含まれ、拒否の一律失敗を成功根拠にしていない。queryは送信前記録、POST各1回、固有sourceと実query ID・終端を記録した。全Table・旧legacyデータ・DRを保持した判定ではない。

## 初回切替の制約と復旧

通常環境の旧固定構成からの初回切替には、旧SSA管理情報・postBuild・停止フラグの所有権整理が必要だった。結果不明や構成不一致はUNKNOWNと受付閉鎖を保持し、対象UIDと承認済み構成を確認した手動整理、実構成・発行済みSPの正の照合で再開した。SP再発行、根拠のない成功・未実行への変換はしていない。初回手順の途中で作られた `before-first-apply` の共同所有も対象を限定して引き継いだ。

したがって**通常環境の初回2→3切替を無介入の全自動成功とは扱わない**。手動整理を必要としなかった空環境のbootstrap / full、および通常環境の新世代への再配備は、通常Operatorの全7 child適用・検証が完了した証拠と分けて記録する。通常Operatorへ無制限の強制SSAやUNKNOWN自動解除を入れていない。

## 検証コマンドと運用境界

基本順序は `create → export-spec → render → bootstrap材料 / saved plan → plan → apply(PREPARED) → full材料 / saved plan / profile → 新しいplan → 両入口の明示再起動 → apply(APPLIED)`。private operator configでは `phase`、fullの `predecessor_id`、`runtime_profile`、`runtime_endpoint` を明示する。Workbench URL、profileのnative SQL URL、候補の直接TLS Trino URLは別の接続先である。

空環境は `register-spec --spec APPROVED_SPEC --idempotency-key KEY --auth-headers PRIVATE_FILE` から同じ順序へ進む。namespace / source用S3 / 7 suspended childの初回foundationを準備してから通常Operatorへ渡す。生成後のsnapshotはimmutable Bucket経由とし、旧固定2DB向けactivateへ戻さない。新規namespace作成やlegacy所有権整理を通常のSecret-only saved-plan検査の例外にはしない。

空環境の最終状態は検証完了時の観測である。完了後は専用K3s container `shirokuma-p2-6-rebuild-20261020` と検証用両入口を停止し、volume / 証拠を保持した。通常環境の3DBと両入口は稼働を継続し、global kubeconfigは変更していない。

最終修正後の全体回帰は **1,038件中1,006成功・既定32 skip**。`make verify-design-context` は14試験と文書検査成功。Go製品範囲 `go test ./cmd/... ./internal/...` / `go vet ./cmd/... ./internal/...`、Operation 53件を含むfocused試験、独立レビューも完了した。最終ソースで通常の両入口を再起動し、HTTP / native 56項目、3DB READY・OPEN・Tofu差分0・doctor healthyを再確認した。今回UIのHTML / CSS / 操作契約は変更していない。

証拠はGit対象外の `var/shirokuma/p2-6-acceptance/` に保存する。主な参照は以下。private material / receipt / ToFu stateには秘密値が含まれるため、public reportへ転載しない。

- `live/third-create-evidence.json`、`live/closed-create-evidence.json`、`live/final-create-replay-evidence.json`
- `live/p26-live-three/{bootstrap,full}/evidence-apply.json`、`auth3-matrix.json`、`human3-endpoints.json`、`mains3-s3.json`
- `live/p26-live-redeploy/full/{evidence-apply,restart-evidence}.json`、`keep3-sentinel-check.json`、`finalhttp-endpoints.json`、`latesthttp-endpoints.json`、`finalpurge-purge.json`、`final-evidence.json`、`doctor.json`
- `rebuild/empty-cluster.json`、`rebuild/p26-live-three/declaration-rebuild-evidence.json`、`full/secret-observation-replan-evidence.json`、`freshs3-s3.json`、`freshpurge-purge.json`、`freshopa-opa.json`、`freshhttp-endpoints.json`、`final-evidence.json`
- `retention-evidence.json`、`prepared-timeout-component-evidence-final.json`、`cleanup-evidence.json`、`validation/results.json`、各実行の公開query記録。初回復旧の経過は同ディレクトリの`progress.md`と復旧証拠を参照

## 残す範囲

最終レビューで検出した未送信fullのtimeout後再plan経路は、限定修正・追加回帰・独立レビュー・上記24項目で解消した。P2-6の限定受入を止める未解決事項はない。初回component試験では継承済みmutation_started=1を0と期待するharness側の誤りで停止したが、前述のstep記録を判定根拠に修正して再実施し、失敗証跡も保持している。対応profileは4標準Schemaに限定し、private材料の準備と両入口の再起動は明示したoperator手順で行う。新規環境のfoundationと旧構成からの初回所有権引継ぎも通常applyと区別する。

DB削除 / retire、完成した管理UI、任意Schema profile、新DBへのIngest / WAP / Export / Jobs等の一般化、Windows Power BI / 外部driver実機、DRや一般公開の受入は後続の範囲。今回の技術受入をそれらの完了判定には広げない。
