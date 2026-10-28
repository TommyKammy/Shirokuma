# P4-7 予定実行・自動scan／更新・alert 実装検証報告

**状態: 限定技術受入れ完了。Phase4全体と所有者による最終操作性評価は後続計画へ残す。**

既存schedulerをP4-3〜6の通常実行APIへ接続した。Workbenchと通常CLIから登録Job、Stage scan、管理Mart refreshの予定を保存・有効化・停止し、発火履歴とDB内alertを確認できる。
操作方法は[予定・alertガイド](database-schedules-alerts.md)を参照する。
予定は既定で無効とし、owner、DB／schema、role、定義revisionと実行意図を保持する。対象変更、grant失効、失敗、UNKNOWNからの再開は明示操作とする。

## 実装と実機結果

| 対象 | 確認した結果 |
| --- | --- |
| 3DBのJob | SALES／FINANCE／INSIGHTSで自然な予定発火とnative実行を確認。同一occurrenceの予約を再送しない。 |
| Stage scan | 既存内容はSQL・snapshot追加なしで重複skip。新規appendは各DBでcommit済みchild 1件と新snapshotを作成。 |
| 管理Mart | `NO_CHANGES` は既存publicationと成功時刻を維持。変更後は3DBすべてで型付き4行を確認。先行する競合skipと後続成功を分けて保持。 |
| Workbench／通常CLI | 無効保存、有効化、停止、CAS保存、list／show／ticks、alert一覧・ackを確認。UIの同一contextでdraft・focusを保持し、DB切替と失効時に保護表示を更新。 |
| DB／権限境界 | 36ケース・43 HTTP要求で確認。viewer／engineerのadmin昇格要求、他owner・他DB・同名別identityへの操作、cross-catalog要求を拒否し、拒否経路のnative実行は0件。 |
| 競合／二重実行 | 並行tickと再起動で予約を二重受付しない。受理済みJobへの通常手動実行は409 `JOB_BUSY`、余分なSQLなし。Stage／Martの実際の競合skipと後続成功も保持。 |
| 定義revision | 予約後の定義変更を `TARGET_REVISION_CHANGED` として拒否し、run／SQL／admissionを作らない。測定ケースのreleaseはdueから1.162秒後。 |
| 実時間の遅延 | due前に両通常runtimeを停止し、dueから78秒後に再起動。対象回は `MISSED`、runなしとなり、追いかけ実行しない。 |
| fault isolation | SALESの無効alert結果で予定が停止しても、FINANCEの別Jobは自然発火して成功。 |

通常CLIの既定native8080で予定／alertルートがquery serviceへ落ちる欠陥を修正し、既存の認証済みDB adapterへ接続した。POSTには既存のOrigin検査を適用した。

`manual_wap.get_model` は既存SQLite connectionでadmissionを再検証するよう変更した。コピーDBで確認したnested-reader interlockを解消し、dirty／SELECT-only双方の回帰検証を行った。先行したSALES Martの `OperationalError` の原因は確定していない。

## Alertとgrant失効

既存の読取SQLによる1行1列Boolean判定とWorkbench内通知をDB対応した。
TRUEで発生・再発を記録し、FALSEで解消する。NULL、多行、SQL失敗は失敗として扱い、既存のactive通知を解消しない。
通知のowner／DB分離、FINANCEのWorkbench ack、INSIGHTSの通常CLI ackを実機確認した。

受理前のINSIGHTS grant失効では、自然tickが `DB_ACCESS_REVOKED` となり、runを作らなかった。既存run数は1件のままで、SALES／FINANCEの正規要求は継続した。
SALESでは最初のSQLがFINISHEDになった後に通常経路でgrantを失効させた。第2 stepはSKIPPED、対応するSQL operationは0件、親はFAILEDで閉じた。
Workbenchは保護データとalert表示を消去し、実行・保存・予定操作を無効化した。既存alertのoccurrencesは3のまま保持された。

通常のregrant要求とfull世代適用後、SALES／INSIGHTSの実効epochは5、FINANCEは2となった。
FINANCEは保存済みの旧世代intentを維持しながら、新世代のfull9で次の自然occurrenceに成功した。
SALESは現在の権限で明示再開し、自然発火の2 SQLがFINISHED。同じactive alertのoccurrencesは4となり、元の通知contextを保持した。確認後に予定を停止した。
INSIGHTSの失効停止予定は明示再開せず、無効のまま保持した。

## DRAINと世代切替

受理済みFINANCE Jobの最初のSQL完了後にfull applyを開始し、実際のDRAININGを観測した。
DRAIN中の別予定は `DRAIN`・runなし、新規通常queryは503 `WAREHOUSE_MAINTENANCE`・query／admissionなしだった。
DRAIN観測から1.508秒後に受理済みJobを解放し、両SQLがFINISHED、親・worker・pin ownerの閉鎖を確認してからapplyが進行した。

regrant世代はfullの6 stepがSUCCEEDED、実際のOPA145ケースと既存production verifierの15 SQLが成功し、両通常runtimeが同じ世代、LOCALがOPEN revision121となった。
先行DRAIN v1はprivate observerの小数秒比較assertでapply前に停止したため、DRAIN成功には数えない。元の親は両SQL成功で閉じ、最初のreceiptを保持した。修正後のprivate検証11件と別の有限v2を区別している。

## 日時・TZ・月末

通常CLIで日次Tokyo22:00、週次New York月曜20:00、月次31日Tokyo22:00を保存・有効化・発火前停止し、次回UTC時刻を照合した。3予定のtickと対象native実行の増分は0件。
DataOps Python／zoneinfoで固定時刻を入力する6ケースでは、DST gap、最初のfold、同日2回目のfoldの除外、31日指定の月skip、2026年2月末を確認した。
これらは予定計算の決定的な検証であり、実時間のDST切替まで待った証拠とはしない。実際の遅延・停止再起動は別途上記で確認した。

## 検証と最終復元

候補v5を固定した。source集合208件とcanonical Go binaryの固定値は `var/shirokuma/p4-7-acceptance/candidate-v5.json` に保持する。
最終変更後の `make verify-local-experiment` はexit0。出力が省略されたため全体件数は記載しない。
native routeの限定20件、SQLite修正の限定115件、Go tests／vet／gofmtも成功した。private cleanup53件、実resolverのRef4／Ref6を厳密に照合する監査v2の74件も成功した。オフライン検証は実機結果と区別し、元の監査補助と実証跡も保持する。

所有証拠で確定したTable15件を通常の認証済みTLS SQLで個別にDROPし、全件のnative FINISHEDと同じ権限での正規404を確認した。その後にbyte所有を照合した入力12件だけを削除した。定義・履歴・alertと、未連結予約の証跡は台帳に保持した。

検証用grant12件を通常経路で失効させ、最終full世代 `p47-schedule-final` を適用した。operation `c84276c2-76e8-4971-85cf-128d13a9d446` は6 stepがSUCCEEDED。最終世代でも実OPA145ケースと既存verifierの15 SQLが成功し、LOCALはOPEN revision125となった。

通常サービスを停止して元の4認証を復元し、設定3ファイルと保護対象の正規client22件が元のbyte列と一致することを確認した。検証用profile／client16件は撤去した。再起動後の両通常runtimeは同じ最終世代とstartup identityを返し、元4主体の通常要求は200、doctorはhealthyだった。元の認証で送った定数SELECT 1は実backendでFINISHEDとなり、対応admissionも閉じた。

元のSQLタブ2件はSQL本文とDB／schema／role／mode／warehouseのintentを保持し、日本語・admin・詳細ビューへ復帰した。第1タブの古いDB参照の利用不可表示も保持し、保存済みSQLは実行していない。

最後にchunked SQLite backupを0.025秒で取得してlive接続を閉じ、その分離snapshotと固定した実測証跡を別担当が監査した。全6項目が合格し、74 graph、78 operation、151 query、278追加admissionの閉鎖、全19予定の無効化、清掃・認証復元・元2タブを照合した。既存59台帳の旧行／BLOBとobject identityは、世代適用による32件の明示された管理投影を除き保持されている。実受付の未解決は0件で、過去のFENCED2件と今回の未連結UNKNOWN予約1件を別に保持した。

証跡は `var/shirokuma/p4-7-acceptance/` に保持した。
主要な記録は `final-resident-gate-v5.json`、`private/security/sec3-recheck-result/result.json`、`private/calendar-metadata/summary.json`、`private/clock-combined/reconciled1.json`、`private/grant-loss-c1-closure-proof.json`、`private/drain-plans-v2/drain-closure-proof.json`、`regrant-generation-proof.json`、`private/finance-generation-continuation/state.json`、`private/sales-grant-recovery/state.json`、`private/cleanup/current-v5-p26-completion-v1.json`、`final-generation-proof.json`、`private/final-auth-preparation/final-runtime-and-sql-completion.json`、`browser/final-original-tabs-proof.json`、`private/final-audit/actual-final-audit-v1.json`。
UIの実表示は `browser/sales-recovery-success.png` などに保存した。

## 証拠の範囲

- 再起動ケースの未連結UNKNOWN予約1件は、native受付がない予約として保持する。受付済み実行の未解決件数とは分ける。元のFENCED_READ_ONLY2件も別に保持する。
- 先行するSALES MartのOperationalError、INSIGHTS有効化の502、alert準備時の拒否されたUPDATE、遅すぎた対象変更ケースは、そのまま最初の記録を保持する。後続成功で失敗記録を置換しない。
- 同一対象の手動Stage／Martをholdする追加ケースは実施していない。今回の実測はJobのmanual BUSYとStage／Martの実際の競合skipであり、native guardはP4-5／6のものを再利用する。
- これはlocal環境の有限ケースによる技術受入れであり、SQLiteとIcebergを跨ぐexactly-once保証や分散schedulerの保証とはしない。P4-8／P4-9、Phase5、所有者の最終操作性評価は残る。
