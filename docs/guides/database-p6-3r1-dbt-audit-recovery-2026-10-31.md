# DB-P6-3R1 native公開の読取り監査UNKNOWNの運用復旧

2026年10月31日。C2E2の実機確認で、native公開の行数監査がバックエンドIDを得る前にUNKNOWNとなり、通常照合でも終端を証明できない事象を確認した。新たな公開を送らず、限定した運用復旧を実装し、この範囲の技術受入を完了した。

## 対象と保持する履歴

対象はnative_graph公開の監査COUNTだけがUNKNOWNとなった単一の閉じた親子関係である。native_tableやSQL/WAPの公開、不明な書込みはこの経路に含めない。publish SQL、commit marker、公開済み・committedの証拠、行数監査結果がある対象を含めない。source、公開intent、元actorとcontext、正規child／queryと固定SnapshotのCOUNT SQLを照合する。任意SQLや結果不明の書込みを同じ経路で閉鎖しない。

運用者は元queryと同じplatform adminで認証し、DBT／DataOps／queryのowner leaseを取得できる状態で実施する。元HTTPプロセスがleaseを保持する場合は、認証・台帳・process同一性を確認した計画停止で所有権を引き継ぐ。HTTP停止自体はquery停止の証明ではない。現在のbindingと全関連行を固定し、変更検知と排他制御を通してから、既存のlogical compute停止証明を取得する。LOCALは全Databaseの共有コンピュートなので、復旧はその管理対象compute全体を対象とする。SQLを再送しない。

停止証拠が揃った場合だけ実行閉鎖を記録する。queryのUNKNOWN、backend ID不明、監査結果なし、公開のUNKNOWNを保持し、運用者による読取り実行の放棄と区別して表示する。公開を成功へ昇格せず、元公開keyと選択TABLEの予約も保持する。通常のPUBLISHED証明を緩めない。

## 操作と状態

専用operator CLIは`tools/local_experiment/catalog_admin.py`の`recover-dbt-audit-plan`／`inspect`／`apply`／`reconcile`である。configは保護された絶対pathを使用する。planはpublication IDと復旧用idempotency key（元公開keyとは別）、applyは保存operation ID・現在のrevision・確認済みintent digestを必須とする。復旧中に別key／別planへ切り替えない。

STOP_INTENTは停止完了ではない。結果が不明な場合は同じoperationのinspect／reconcileで確認する。reconcileは追加のstopを行わず、両old memberの証明が揃えばPROVEDへ進める。元コンテナが残る場合、削除・閉鎖・置換先health確認・OPENへの受付再開には同じoperationの明示applyを使う。CLOSEDは実行閉鎖のみでhealth未確認、OPENは復旧時の置換先検証と受付再開まで完了した状態である。

公開API／画面はUNKNOWNを保持したまま、検証済みreceiptからだけ`execution_closure`と`execution_recovery`を導出する。raw JSONの同名fieldだけでは停止済み表示を作らない。履歴のGETが復旧完了と競合する場合も、取得したDBT owner lockの内側でmarkerを再確認し、閉鎖済みの親を再び未閉鎖へ戻さない。

## 検証と受入

実機で元coordinator／workerの2コンテナだけを同じplanで停止した。停止後の構成照合で中断した段階を同じoperationで照合・再開し、全停止証明、元コンテナの削除、閉鎖、health確認、OPENまで完了した。停止対象をreplacementへ変更せず、元COUNTは再送していない。最初の監査通信失敗や一時的な構成不一致の原因を特定したという主張はしない。

全78台帳テーブルの独立比較で、変更を予定した復旧行だけに限定できた。公開はmarker以外元JSONのままで、query／SQL／実行contextは完全に保持した。元の2 fence、source／build／test／要求、RAW／MARTSの28 head、profile、所有者の2 YAMLも不変である。controlはrevision 5から7へ進み、元の状態値を保持した。

更新後の実APIでGET／list／元公開keyの再送がUNKNOWN＋検証済みOPENを返し、SQL・要求・admissionを増やさないことを確認した。その後の別TABLE公開、source取消、VIEW変更、公開先head更新後にも停止履歴を照会できた。画面は日英で結果不明と実行停止を区別し、監査値0を生成しない。390px、viewer切替時の消去、13 worksheet／SQL下書きの保全と参照時の件数不変を確認した。

復旧29 testを含め正規88グループを段階検証し、3,535件＝3,501成功＋34既存skip。影響する5グループは最終修正後に再実行済みである。owner競合は修正前の2件失敗を再現し、修正後は両race・positive closure・historical caseの5件に成功した。通常構成を復元し、LOCAL 2/2、policy minutes=0／revision=5、doctor 46項目で異常なしを確認した。

証拠は`var/shirokuma/db-p6-3r1-2026-10-31/private`、関連する公開・独立証拠は[C2E2](database-p6-3c2e2-view-graph-publication-2026-10-31.md)を参照する。native_table、SQL/WAP、不明な書込みや任意SQLの放棄、一般の自動復旧へ対応範囲を広げていない。DB-P6-3全体とM1本人受入は引き続き後続である。
