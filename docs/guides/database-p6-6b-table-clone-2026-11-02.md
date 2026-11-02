# DB-P6-6B: 固定SnapshotからのTable clone

2026-11-02 JST。SQL-08の返却済み結果保存に続き、EXP-10の未接続cloneを通常Workbenchへ接続する実装単位。実装とこの限定範囲の技術受入を完了した。DB-P6-6全体とM1本人受入は継続する。

## コピーの契約

同じREADY Database内の登録済みTABLEを、新しいSANDBOX TABLEへ一度だけ物理コピーする。元sourceはMARTSなどの読取り可能schemaでもよく、書込み実行contextはコピー先SANDBOXのEngineerまたはadminとする。source閲覧権限とtarget作成権限を別に検査し、source schemaへの書込みを要求しない。Viewer、別DB、VIEW source、既存TABLE／VIEWと同名のtargetは拒否する。

CURRENTは初回実行時に現在Snapshotを固定する。過去SnapshotはASCII十進文字列のIDで指定し、JavaScript Numberへ変換しない。存在しないSnapshotをCURRENTへ置き換えない。sourceの物理UUID、登録incarnation、選択Snapshotのschemaと現在schemaを別々に固定・再検査する。通常のhead appendは、選択Snapshotとschemaが保たれている限りコピー内容を変えない。

Snapshotがないsourceは利用不可と理由を示す。存在するSnapshotの0行は有効であり、0行CTAS後のtargetにSnapshotがない場合は、target_refと作成commitを維持し、確認未完了のwarningを表示する。コピー後のtargetはsourceと独立する。source履歴、partition spec、table properties、内部field ID、NOT NULL制約、所有権、権限などの完全複製は保証せず、選択Snapshotの列と行のコピーと明示する。

## 一度の作成と回復

新しいoperation kindや永続storeを作らず、既存DataOpsの受付、Idempotency-Key、target claim、現在認可、owner lease、取消とUNKNOWNを再利用する。初回のSQL intentより前にsource計測とdestination、コピー意味をoperationへ保存する。SQLは固定のSELECTと既存のSnapshot固定compilerから生成し、clientから任意SQLを受け取らない。

既存targetの不在を検査し、target名をclaimして単一のCREATE TABLE AS SELECTを発行する。OR REPLACE、IF NOT EXISTS、事前DROP、schema作成は使わない。engine成功時の作成commitをDataOps queryの確定処理とともに記録し、後段でtargetのUUID・schema・Snapshotを観測する。最終targetを一時stageとして所有したり、自動削除したりしない。

同じkey・同じbodyの再送は元operationを観測する。別bodyなら競合として拒否する。応答消失やUNKNOWNでは元key・元body・元operation IDを保持し、新keyで自動作成しない。取消は作成済みTABLEのrollbackを意味しない。commit後の観測失敗は「作成済み・確認未完了」を保持し、未作成や自動再試行へ戻さない。

## 通常画面とHTTP入口

Explorerの既存clone導線から、source、CURRENT／過去Snapshot、コピー先SANDBOXと新しいTABLE名、実行roleを確認できる。sourceを閲覧するcontextとtargetへ書き込むcontextを分けて表示する。長い処理中の通常の待機、状態確認、取消と元intentの回復を案内する。

未解決intentはnavigation／reload後も保持し、選択変更や遅着応答で別source／targetの操作に置き換えない。古い認可、source変更、target競合、Snapshot変更を再検査する。既存SQL-08、AI設定、WAP、ingest、Table exportの状態を維持し、新しい全体tabや独自schedulerは追加しない。

作成要求は`POST /api/v1/table-clones`と単一の`Idempotency-Key`を使う。bodyは`context`（コピー先SANDBOXのfull9）、`source`（ResourceRefの4項目）、`destination`（DestinationLocatorの3項目）、`snapshot_id`（CURRENTならnull、指定時はASCII十進文字列）のちょうど4項目とする。状態取得は既存`GET /api/v1/dataops/{id}?context=...`、取消は`POST /api/v1/dataops/{id}/cancel`を使う。

再読込みや選択変更後は、Explorerで元と同じsource TABLEを選び直してから「操作を照会」する。現在権限・同じsource Refを確認できるまで保護された受領記録を表示しない。別sourceを選択しても元intentのsource／target／Snapshotを変更しない。

初回POSTは同期応答のため、元受付IDが返るまでは画面の即時取消を提供できない。送信前に元key・bodyを保存し、reload後の「状態確認」は同じkeyの観測専用replayでIDを回収する。IDを確認してからGET／取消へ進む。親ACTIVE・子query UNKNOWNも未解決として扱う。

Workbenchとnative residentの両HTTP面で同じ認証・origin・現在context・DataOps guardを通す。作成だけでなく、既存DataOpsの状態取得・取消まで実入口から検証する。

## 技術受入の観点

- コード: current／過去Snapshot、履歴schema、head append、UUID／schema変更、0行、衝突、現在認可、key競合、target claim、取消、UNKNOWN、commit後観測失敗を確認する。
- HTTP: Workbenchとnativeの実HandlerでPOSTからDataOps状態取得・取消までを通し、同じintentが単一CTASに留まることを確認する。
- 実engine: 2DB、Engineer／admin、current／過去Snapshotを含む小さいコピーを作成し、元sourceが不変で、targetの列・型・値・件数が選択Snapshotと一致することを確かめる。
- 通常ブラウザー: Explorerからの作成、進行状態、target確認、日英と狭幅、権限変更・navigation・reload回復を確認する。
- 保全: 検証前baselineの元control rows・schema・UNKNOWN、12保護設定、ownerの2つのYAML差分を保持する。許可したcloneと照合用queryの増分は元データと分けて記録する。

## 技術受入の結果

| 層 | 結果と適用範囲 |
| --- | --- |
| 重点・独立レビュー | hostでbackend19件と既存DataOps39件が成功。workerの最終操作UIはClone18件・既存DB67件・DataOps38件が成功。source読取りとtarget書込み、SANDBOX purpose、履歴schema、head append、UUID／schema変更、nested型の新field ID、0行、現在失効、target claim、取消、UNKNOWN、commit後観測失敗、owner切替・遅着応答を確認した。重複件数は合算しない。 |
| canonical | Makefileから明示venvで展開した106群を4並列で実行。4,419件中4,385成功・34 skip、全群終了コード0、失敗・error0。skipの名称・理由はSQL-08と同一。最後の回復案内文だけを全体回帰後に補足し、Clone UI18件と実ブラウザーで再確認した。 |
| 実engine・両HTTP面 | A／Bの実Engineer・platform adminで4コピー。CURRENTの2行を3件、Bの過去Snapshotの3行を1件、新しいSANDBOXへ作成した。12件の有限SELECTで列・型・全値・件数が一致。sourceの全headを保持し、targetは独立UUID・格納先。各intentのCTASは1件で、cross-port GET・元key replay・別body409を確認した。 |
| 拒否・取消 | 19件の不正入力・権限・別owner／保存roleの要求が想定どおり拒否され、追加SQLは0件。これとは別に4件の同key別body競合を409とした。2件の完了後取消で作成済みtargetを保持。衝突拒否で1件のown terminal target claimが新しいFAILED operationへ移る既存仕様を識別し、元20 claimを変更しなかった。claimやcontrol storeの復元は行わない。 |
| 最初の検証名の拒否 | 最初の検証名が既存31文字上限を超え、FAILED・未commit・子queryなしで400となった。target不在とsource保全を確認後、元key／body／失敗記録を保持し、短い名前の別intentとして上記4件を実行した。通常画面にも31文字・英小文字始まり・予約名の案内、maxlength、ARIAを追加した。 |
| 通常ブラウザー | Bの元TABLEからadmin・過去Snapshot `1695794972297287595` を指定し、`p66b_b_old_a_ui_20261102`へ1回作成。FINISHED・committed・VERIFIEDを表示し、別の新規SELECT2件でもsourceとtargetの6列・全3行が一致した。CURRENTは単一選択肢とし、過去IDをNumber化しない。 |
| 回復・表示 | reload後、元sourceを再選択し、元受付IDの照会で同じkey・過去Snapshot・targetの完了結果を回収。CTASは全体5件、UI操作の子queryは1件のまま。日英の作成・回復案内、無効名の送信禁止を確認。390pxで日本語page390・panel346、英語panel346で横はみ出しなし。英語の背面page全体は410pxで、本単位のpanel外の残件。実200%（dpr2／幅960）でpage960・panel716。100%／1920px・日本語・元の19 Worksheetと選択SQL／A・marts・admin・read-onlyへ復元し、console warning／errorは0。 |
| 保全・実行の内訳 | 元active11,983行・default156行、全schema・UNKNOWN、12保護設定とmode、ownerの2 YAML差分を保持。追加は親操作10件（copy5・SQL前失敗5）、CTAS5、target claim5、登録object5、query／context各20、admission30。20 queryはCTAS5＋有限読取り15（初回検証1＋API12＋UI2）。新規targetを保持し、元sourceへのDDL／DML、LLM、WAP model、stage所有・自動DROPを追加しない。 |
| doctor・最終gate | 終了コード0・healthy（45 healthy＋SQL informational1）。新しいSQL probeの証拠とはしない。design-contextは14件成功、documents94／issues10／references42。89能力IDの順序とEXP-10以外の88行を保持。最後のreload／照会・画面復元後も全台帳はactive12,078行／default156行でschema・全行一致し、追加SQL・取消・復元書込みは0。 |

COPYはTableの行順、履歴、内部IDや制約を複製する契約ではない。実環境でUNKNOWNを発生させる破壊的な中断試験は行わず、UNKNOWN／commit後障害の保持はコード検証、実環境では元のUNKNOWNの保全と単一CTAS・回復時の非再実行を分けて確認した。

```sh
rtk proxy make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3 DATAOPS_PYTHON=tools/local_experiment/dataops/.venv/bin/python3
rtk proxy make verify-design-context PYTHON=tools/local_experiment/dataops/.venv/bin/python3
```

89能力IDとEXP-10以外の88行の意味を保持する。次はEXP-12のprofiling／NULL統計を限定単位として進め、その後のTable／Explorer lineage、Dropを分ける。P6の全必須行、統合受入、M1本人受入は完了扱いにしない。
