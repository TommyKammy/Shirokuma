# Databaseモードの機能一覧と利用条件

[DB-P6-4E](database-p6-4e-ai-settings-2026-11-02.md)でAI-06の共有設定・明示接続test・保存を通常Workbenchへ接続し、限定技術受入を完了した（全体回帰4,264成功・34 skip、両HTTP面84要求、実LOCAL疎通・日英ブラウザー、原設定と既存データの保全）。現在の真P・上限admin・選択global adminを検査し、DB閲覧・管理権限と分離する。共有設定の接続成功はDatabase推論の準備完了を意味せず、既存LOCAL推論の対応条件を維持する。次はSQL-08と残る探索操作であり、89項目全体とM1本人受入は継続する。

[DB-P6-4C2C1](database-p6-4c2c1-workload-baseline-2026-11-02.md)はAI-04／AI-05／CMP-05へP専用の固定LOCAL宣言基準レビューを加えた限定技術受入単位である。旧4項目をmet／not_metで示す範囲に限定し、8 admission policyやglobal policy全体、実行許可にはしない。C2C1の限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,232成功・34 skip、両HTTP面の実APIとWorkbenchの3面表示、最終保全PASS）。

[DB-P6-5C](database-p6-5c-contract-2026-11-02.md)はCMP-02へDB保存activityの手動一覧を追加したUIのみの限定技術受入済み単位である。既存AUD-03のself／database認可とeffective DB admin例外を保ち、P専用のCMP-05とは別に扱う。P5Cの限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,188成功・34 skip、既存AUD-03の実APIとWorkbenchの実画面、最終保全PASS）。

[DB-P6-5B](database-p6-5b-platform-resources-2026-11-02.md)はconfigured container resourcesだけの限定技術受入済みの単位であり、能力の一括完了を意味しない。P5Bの限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,166成功・34 skip、両HTTP面の実API・Workbenchの実画面、最終保全PASS）。

[DB-P6-4D1](database-p6-4d1-ai-audit-metadata-2026-11-01.md)は既存AUD-03へ保存済みAI要約metadataを追加した限定技術受入済みの単位である。`source=ai`と閉じた5種のaction、既存event形状・`links=[]`だけを使い、内容fieldを追加しない。最新保存要約であり、遷移監査ではない。現在のself／database認可を維持し、保存full9は内部整合を要するが現在revisionとの一致を要しない。platform scopeはAIを除外する。D1の限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,135成功・34 skip、Workbenchの実API・実画面、最終保全PASS）。 C2C／P5B・AI設定とP6-4全体／P6-5全体／P6-7／M1本人受入は継続する。

2026年11月1日。[DB-P6-0](database-full-capability-and-compute-plan-2026-10-28.md)で固定した89項目に、監査・共有LOCAL管理、SANDBOX VIEW・native TABLE・[LOCAL SQL説明](database-p6-4a1-local-sql-explanation-2026-10-31.md)・[TABLE／条件付きVIEW計画説明](database-p6-4a3-view-plan-explanation-2026-10-31.md)・[Query Doctorの最新SELECT読込](database-p6-4a4-latest-query-source-2026-10-31.md)・[保存済み計画の明示再説明](database-p6-4a5-saved-plan-explanation-2026-10-31.md)を反映した。[B1 SQL案生成](database-p6-4b1-sql-proposals-2026-11-01.md)と[B2 Manual WAP下書き引渡し](database-p6-4b2-wap-handoff-2026-11-01.md)は限定範囲を技術受入済みであり、[C1 Catalog Curatorのmetadata説明・説明文案](database-p6-4c1-catalog-proposals-2026-11-01.md)は限定範囲を技術受入済みである。[C2AのDB内SRE／Policy観測](database-p6-4c2a-context-observations-2026-11-01.md)は限定範囲を技術受入済みである。機能数は増やさず、M1の本人受入は未完了である。

VIEW計画は`logical_v1`の対応するnative／外部登録INVOKER VIEWに限り、再帰依存先も同一DB・現在の全schema query権限を必要とする。保存済みVIEW定義内で整数snapshotを指定するTABLEは、そのsnapshotのschemaが現行列構成と一致する場合に扱う。利用者の直接入力に対するVERSION対応は広げない。DEFINER・他DB参照・履歴schemaの列構成差分・timestamp／tag／branch等の非整数time travelは未対応である。固定の非ANALYZE EXPLAINを実行し、元SQLは実行しない。

最新SELECT読込はQuery Doctor入力だけを対象とし、本人と保存9項目contextが一致する対象の通常read-only queryを選ぶ。全参照schema権限と現在のidentityを再検査し、SQL・AI・Wake・catalogを呼ばない。元queryのID・時刻・状態は出典情報であり、UNKNOWN等の照合・取消・再実行は行わない。説明や計画取得は別の明示操作とする。

保存済み計画の再説明は、元A2／A3のCOLLECTED証拠と、元queryの保存値・現在台帳がともにFINISHEDであることが条件。元の説明がSUCCEEDED／FAILED／CANCELED／UNKNOWNの場合に、取得時点の安全な計画とmetadataを使う。本人・保存9項目contextと、凍結した全依存schemaの現在権限を再検査する。catalogのhead再取得・Engine・Wake・admissionは行わず、元UNKNOWNの解消やA2の新規query受付の解除とは分ける。新しい説明には独立した履歴・復旧・取消を持ち、そのUNKNOWNを自動再推論しない。

B1は明示選択した同一DBの1〜8件のTABLE／VIEWについて、現在の本人・保存9項目context・全source schema権限とResourceRefを検査し、依頼文の原文と許可された名前・列名・型だけをLOCALへ送る。VIEWは列metadataだけを扱い、A3の計画・実行条件を検証したとは表示しない。生成物は単一read-only SELECTの構文・参照範囲・policyを確認する下書きであり、列・型・業務上の正しさや実行可能性は保証しない。実行・editor書換え・WAP引渡しは含めない。B1を技術受入済みとした（全体回帰3,892成功・34 skip、2DB・3role実機・最終保全PASS）。

B2は成功済みB1案から、元contextと同一DB・同一E／A roleのSANDBOX実行先contextを明示指定して、通常WAPの新規未保存下書きを準備する。全選択schema権限を検査し、SQLで使用する現在のTABLE識別情報・列を確認する。使用VIEW・不確かなnamespace変換は拒否し、未使用VIEWの定義や依存を取得しない。明示GETと空の新規フォームへの取込はAI・SQL・Wake・モデル保存を行わず、以後の編集・保存・build・audit・A公開は既存WAPへ委ねる。B2の限定範囲を技術受入済みとした（全体回帰3,944成功・34 skip、2DBの実機・画面・最終保全PASS）。

C1はSQLワークシートから独立したcontextで、同じprimary schemaの安定したTABLE／VIEW 1件を明示選択する。現在の本人・保存9項目context・schema query権限とRef／UUID／列構造を確認し、名前・列名・型だけでLOCALの要約・表説明と最大8列の部分的な説明文案を作る。VIEWの定義・依存や既存コメントを入力へ含めず、A3の計画対応や業務上の意味を保証しない。元要求の復旧・履歴・取消を分離し、SQL・Wake・説明保存・catalog metadata変更は行わない。C1の限定範囲を技術受入済みとした（全体回帰4,020成功・34 skip、2DB・3roleの実API、adminの実画面、最終保全PASS）。P6-4全体／P6-5／P6-7／M1本人受入は継続する。

C2Aは各Bearで明示する独立contextについて、現在の本人・保存9項目context・primary schemaのquery権限が一致する本人のquery／AI記録だけを各最大20候補の窓から取得する。項目はID・kind・保存状態・保存時刻の5項目だけとし、省略数と古い候補の有無を示す。queryのFINISHEDとAIのSUCCEEDEDは変換せず、UNKNOWNも照合しない。Policyは現在権限・SQL modeの上限とplatform能力flagだけを示し、個別SQLの承認にしない。全roleでplatform source・SQL・LLM・書込みを呼ばず、旧SRE／Policy検査は開放しない。重点backend335件・UI889件、実API・admin実画面と復元を確認し、最終8ファイルで全体回帰を完了した。C2Aの限定範囲を技術受入済みとした（全体回帰4,073成功・34 skip、2DB・3実行ロールの実API、未付与権限の拒否、adminの実画面、最終保全PASS）。P6-4全体／P6-5／P6-7／M1本人受入は継続する。

[DB-P6-4C2B](database-p6-4c2b-platform-observations-2026-11-01.md)では、SRE／PolicyのP専用固定LOCAL観測を実装した。現在のaccountとprincipal双方のP権限、上限admin、選択adminをsource取得前後に確認し、DBやgrantを利用条件にしない。固定2 Deploymentのdesired／ready要約、過去のengine検証日時、保存AutoChill設定、LOCALの保存admission件数だけを扱う。desired／readyの一致や両方0をSQL health・完全停止・資源解放の証明にせず、保存日時を現在の稼働保証へ変換しない。明示GETだけで書込み・SQL・LLM・操作を行わず、C2AのDB観測はplatform sourceを読まないままとする。C2Bの限定範囲を技術受入済みとした（全体回帰4,109成功・34 skip、両HTTP面の実APIと権限拒否、Pの実画面、復元と最終保全PASS）。P6-4全体／P6-5／P6-7／M1本人受入は継続する。

[DB-P6-5A](database-p6-5a-compute-observations-2026-11-01.md)では、Compute画面へのP専用固定LOCAL観測の読取り統合を実装した。受入済みC2Bの明示GET・strict DTO・global P cacheを再利用し、新しいAPIやDB context／grantの前提を追加しない。現在の真P・上限admin・選択adminを条件とし、完了snapshotと観測時刻をSRE／Policy／Computeで共有する。画面を開くだけでplatform観測を取得せず、既存Warehouse操作へも接続しない。desired／ready要約・過去のengine証拠・保存AutoChill設定・限定admission件数を現在healthや完全なPolicy監査へ読み替えない。P6-5Aの限定範囲を技術受入済みとした（全体回帰4,122成功・34 skip、両HTTP面の実APIと権限拒否、Computeの実画面、最終保全PASS）。CMP-05の接続はこの限定観測だけを対象とし、旧検査・global auditとP6-4全体／P6-5全体／P6-7／M1本人受入は未完了とする。

## 利用条件と表示の意味

- **V／E／A**は`bi_viewer`／`data_engineer`／選択中のDB `admin`。利用role上限、現在のaccount、DB grantの上限をすべて満たす必要がある。
- **P**は実accountのplatform管理権限、最大role `admin`、選択role `admin`のすべてを満たす状態。Aの選択だけではPにならない。実platform管理者がAを選ぶ場合の既存DB grant例外は維持する。
- **有効context**は本人、DB／Schema、保存role、LOCAL、binding／generation／grant revisionが現在の認可と一致すること。DBはREADYで、desired／applied bindingが一致する。未知・不許可・非READYのDBは同様に隠し、一覧にない理由を推測して開示しない。
- **対応**は実装された入口があるという意味であり、対象未選択や権限失効でも操作できるという意味ではない。**未接続**はDatabase用の入口がなく、roleを管理者にしても解除されない。**別計画**は追加の製品契約が必要な機能である。

| 表示の分類 | 判定と案内 | 利用者の次の操作 |
| --- | --- | --- |
| 機能未接続 | その機能名とDatabase対応状況を表示。古いroute／buttonの拒否を保持 | 対応済みのSQL・WAP・実行履歴などへ移動 |
| 既知のrole条件不一致 | 選択role上限、A／E条件、P条件が不一致 | 許可された実行roleを明示選択。存在を隠したDBの権限不足とは断定しない |
| 対象未選択 | 操作自身のDB／Schema／Table等が未選択 | そのパネルの対象を選ぶ |
| 確認中・context利用不可 | 認可読取り中、古い版、失効、非公開の対象不在等 | context更新・明示再選択。古い結果を再表示しない |
| 利用可能 | 実装対応、現在の認可、操作自身のcapability、入力、状態の全条件が成立 | 既存の確認・送信操作へ進む |

この分類は案内の意味を固定するもので、新しい認可方式ではない。UIは既存の`dbReason`／`dbExplorerReason`と各DataOpsの判定を使う。APIは要求ごとに再認可する。`manage=False`はplatform管理機能の包括許可ではなく、DB内admin操作の全拒否を意味するものでもない。

## 所有と実行記録の共通契約

| 記号 | 入力と出力 | 所有・監査・回復 |
| --- | --- | --- |
| U | ローカルの表示・下書き・明示選択→表示更新 | ブラウザーの下書き。role上限変更で保存済み実行roleを黙示変更しない |
| Q | SQL、mode、行上限、query／worksheet ID、context→query・page・結果 | 本人と受理時contextへ固定。結果・履歴を再認可。取消は元のidentityを使い、UNKNOWNを再送しない |
| X | Schema／Table Ref、detail、Snapshot、format→metadata／preview／artifact | Explorer独自context。実Table identityとincarnation、Snapshotを検査。exportは本人・元operationに固定しdownload時も再認可 |
| I | 入力bytes、型、format、mode、locator／Ref、key、context→preview／取込operation | 入力変更で再preview。受理済みpayloadを固定。本人と元contextで照会・取消し、commitと後続記帳失敗を分ける |
| W | DB内model ID／version、source Ref、build／audit／publication ID→WAP metadata／公開操作 | **modelとbuild／audit／publication metadataはDB内で共有**し、actorを記録。EからAへの明示引渡しが可能。実行operationは呼出者へ固定 |
| J | 版管理したtyped steps、引数、file、context→Job定義／run／step履歴 | owner＋保存role＋DB／Schema／context。CASと受理版固定。元runの取消・証拠に基づく照合 |
| S | 宣言済みlocal root、設定、file、target、context→Stage／preview／scan／export | ownerとDB／context。内容dedupと設定版を保存。UNKNOWNを再取込しない。receiptは完成した出力のみ |
| M | WAPの明示model版、監視source、MARTS target、context→Mart／check／refresh履歴 | ownerとDB／context、定義revision。監査済みSnapshotを公開。基準保存と公開commitを区別 |
| T | Job／Stage／Martの保存版、予定、timezone→予定／tick／alert | ownerと保存role／対象版。既存schedulerのみ。既定無効、競合・遅延は既存skip方針。失効・UNKNOWNで停止 |
| A | Pと管理context、期待revision、key、対象→管理operation／状態 | 作成root・actor・環境、CASと現在権限。登録／受付と実READYを区別。UNKNOWNは同じ要求で照合 |

## 全操作の対応表

入口欄のAPI群はHTTP-only CLIからも利用する。Qは送信時のWorksheet contextを保持し、I／W／J／S／M／Tは各パネル自身のcontextを持つ。受理済み操作を画面の現在選択へ付け替えない。未接続の行はDB-P6以降の残件として保持し、本一覧で対象から落とさない。

| ID | 操作と入力から出力 | 状態・利用条件 | 所有・記録・回復 | 入口または後続 |
| --- | --- | --- | --- | --- |
| GLOBAL-01 | 言語切替 | 対応、認証済み利用者 | U、設定を保存 | ヘッダー |
| GLOBAL-02 | 利用role上限の選択 | 対応、account上限以内 | U、各保存roleは保持 | ヘッダー |
| SQL-01 | Worksheet追加・選択・閉じる | 対応、最後の1枚は保持。表示名は既存の自動採番 | U、独自下書き | SQLワークシート |
| SQL-02 | DB／Schema／role／mode／LOCAL／行上限、更新・再選択 | 対応、有効contextと許可mode | Q、明示再選択 | SQL、`/api/v1/databases`／schemas |
| SQL-03 | SQL本文／選択部分を実行 | VはMARTS読取、Eは4Schema読取とRAW／SANDBOX書込、Aは許可admin-write | Q、SQL guardを維持 | `/api/v1/queries` |
| SQL-04 | page取得・poll・実行状態 | 対応、元queryの現在認可 | Q、部分・打切り・UNKNOWNを区別 | query pages |
| SQL-05 | query取消 | 対応、元本人のcleanup契約 | Q、失効後の取消と結果閲覧を区別 | query cancel |
| SQL-06 | 最近の履歴・詳細・復元 | 対応、本人と許可context | Q、復元だけでは実行しない | query history／inspect |
| SQL-07 | table／chart、filter、sort | 対応、許可された返却済み行 | U＋Q、失効後は消去 | 結果表示 |
| SQL-08 | Worksheet結果CSV／JSON／TSV保存 | 未接続。Table全件Exportと別機能 | 結果保存の再認可は後続 | DB-P6-6 |
| SQL-09 | 固定sample SQL shortcut | 未接続、旧catalogを仮定 | U、固定SQLを自動送信しない | 選択DBのSQLを明示入力 |
| SQL-10 | Copilot SQL生成 | B1の未実行案に条件付き対応。B2のWAP下書き引渡しは限定技術受入済み | V／E／Aで許可metadataによるLOCAL案を生成。B2は成功済み本人案・元contextと同一DB／同一E／A roleのSANDBOX実行先・使用TABLEが条件。worksheet／Doctor本文や結果を変更せず、取込後は通常WAPで明示編集・保存する | DB-P6-4B1技術受入済み、B2限定技術受入済み |
| SQL-11 | Copilot Explain | SQL説明・TABLE／条件付きVIEW実行計画の説明・保存済み計画の明示再説明に対応 | V／E／A、有効なSQLワークシートcontext・再帰依存を含む全schemaの現在のquery権限・LOCAL AI。新規VIEW計画は`logical_v1`の対応INVOKER・同一DBに限定し、固定の非ANALYZE EXPLAINは通常受付・Wakeを利用。再説明は元A2／A3のCOLLECTED＋FINISHED証拠と凍結contextが条件で、Engine等を呼ばない。これらの説明操作で元SQLを実行しない | DB-P6-4A1／A2／A3／A5対応 |
| SQL-12 | Copilot Stage and Audit | 旧Stage／Auditボタンは未接続。B2の手動WAP下書き引渡しに条件付き対応 | 引渡しでは保存・build・auditを行わず、通常WAPのW条件と明示操作を維持 | 手動WAP、DB-P6-4B2 |
| SQL-13 | Copilot Publish | 未接続。手動WAP公開は対応 | 将来もAによる明示WAP公開 | 手動WAP、DB-P6-4 |
| EXP-01 | DB／Schema／Table／Viewツリー、検索・page・更新 | 対応、V／E／Aの許可Schema | X、Explorer独自context | inventory／objects |
| EXP-02 | DB／Schema概要、identity、columns | 対応、許可browseと安定Ref | X、再作成した同名Tableは別identity | object detail |
| EXP-03 | 最新データpreview | 対応、許可queryとpreview capability | X＋Q、query pages | object preview |
| EXP-04 | Snapshot一覧 | 対応、読取可能な安定TABLE。Viewは対象外 | X、metadata取得 | object snapshots |
| EXP-05 | Snapshot／Time Travel preview | 対応、選んだSnapshotとpreview capability | X＋Q、latestへの代替なし | Snapshot preview |
| EXP-06 | DDL表示 | 対応、Schemaの**write capability**が必要 | X＋Q、browseのみでは不可 | object ddl |
| EXP-07 | 新しいSQL、SQL名挿入、Explorer Schemaの明示適用 | 対応、許可context | U＋X、新しい／元Worksheetを区別 | 探索の明示ボタン |
| EXP-08 | Snapshot固定Table全件Export CSV／JSONL | 対応、読取可能な安定TABLE。許可Vも可 | X、元Snapshotと件数・内容 | `/api/v1/table-exports` |
| EXP-09 | Export照会・取消・新規・receipt／download | 対応、本人と元context | X、同一operation／key、完全出力のみ | table export／dataops |
| EXP-10 | 現在／過去Tableのsandbox clone | 未接続 | 将来はsource／target identityを固定 | DB-P6-6 |
| EXP-11 | 探索のDrop Table | 未接続。A選択だけで解除しない | 削除方式と認可を別途確定 | DB-P6-6 |
| EXP-12 | profiling／NULL統計 | 未接続。columns／previewは対応 | 将来はXの集計範囲と再認可 | DB-P6-6 |
| EXP-13 | Table lineage | 未接続。WAP metadataと別機能 | 将来は許可source／target | DB-P6-6 |
| EXP-14 | dbtモデルlineage | 変換画面の保存planでmodels／sources／refs、TABLE候補SnapshotとVIEW UUID／versionを区別して表示 | Eは本人、Aは同DBの認可済みmetadata。Explorerの旧lineage入口は未接続 | [DB-P6-3B](database-p6-3b-implementation-2026-10-29.md)、[C2A](database-p6-3c2-sandbox-views-2026-10-30.md)、残るExplorerはDB-P6-6 |
| ING-01 | browser file／drop、型・delimiter等、preview | 対応、E／AかつRAW／SANDBOXのwrite | I、入力と設定のfingerprint | ファイル取り込み |
| ING-02 | create／replace／append／upsertの明示確認 | 対応、previewと現在入力が一致 | I、固定payload／key | `/api/v1/ingests` |
| ING-03 | 取込照会・取消・回復情報 | 対応、元本人／context | I、UNKNOWN再送なし | `/api/v1/dataops` |
| ING-04 | 旧server path／固定catalogフォーム | 非表示・未接続 | 新しいbrowser file入口を使用 | 旧ingest routeは拒否 |
| ING-05 | Stage一覧・新規・Load・再Load・保存 | 対応、A、本人の同DB Stage | S、宣言済local rootとCAS | `/api/v1/stages` |
| ING-06 | Stage file一覧・preview・単件取込確認 | 対応、上記条件と入力確認 | S＋I、設定／bytesを固定 | Stage files／ingest |
| ING-07 | Stage scan・照会・scan履歴 | 対応、A、本人のStage、atomic append／keyed upsert | S、内容dedup、未知効果を保護 | Stage scans |
| ING-08 | Stage Snapshot export・receipt・download | 対応、A、元owner／context | S＋X、安定sourceと完成出力 | Stage exports |
| ING-09 | Stage operation照会・取消・履歴 | 対応、元owner／context | S、元の受理identity | Stage operation |
| ING-10 | Stage予定の作成・Load・保存・有効化・停止・tick | 対応、A、atomic append／keyed upsert | S＋T、既定無効、保存target版 | schedules |
| ING-11 | 外部Stage／connector同期 | 別計画。local Stageと区別 | 外部接続の契約が必要 | 後続scope |
| TRF-01 | WAP model一覧・版Load・版保存・source alias／target | 対応、E／A、有効contextと同DB Ref。B2の新規下書き取込に条件付き対応 | W、DB共有metadata。B2は空の新規フォームだけへ取込み、model ID・target・品質を確認後に既存CAS版保存 | `/api/v1/wap/models`、DB-P6-4B2 |
| TRF-02 | 保存版のWAP build | 対応、E／A | W、呼出者とimmutable版 | WAP build |
| TRF-03 | buildのaudit、build／audit／publication詳細 | 対応、E／A、同DB | W、E→Aのmetadata引渡し | WAP audit／details |
| TRF-04 | 監査済みbuildのpublish | 対応、A | W、監査Snapshotから明示公開 | WAP publish |
| TRF-05 | publication rollback | 対応、A | W、publication IDを固定 | WAP rollback |
| TRF-06 | WAP operation照会・取消・metadata ID取得 | 対応、許可role／context | W、実行はcaller-bound、metadataはDB共有 | WAP／dataops |
| TRF-07 | Managed Mart新規・一覧・Load・保存・model版選択 | 対応、A、MARTS purpose、本人のMart | M、定義revisionと固定model版 | `/api/v1/marts` |
| TRF-08 | 変更確認、無条件／条件付き全件更新 | 対応、上記条件。変更確認はwatch sourceが必要 | M、source Ref／Snapshot集合 | Mart check／refresh |
| TRF-09 | Mart照会・履歴 | 対応、元owner／context | M、未知操作再送なし | Mart runs |
| TRF-10 | Mart予定のLoad・新規・保存・有効化・停止・tick | 対応、A、監視sourceあり | M＋T、保存版で実行 | schedules |
| TRF-11 | dbt一覧・model SQL・build／test | SQL／WAPのcompile・候補build／test・公開をAPI／CLI／Workbenchで提供。単一native TABLEと付属品質test・明示MARTS公開に加え、保存済みTABLE／EPHEMERAL全graphのRunTask／TestTaskを実装（C2C技術受入済み）。C2Dでgraph内TABLE単位の明示MARTS公開を技術受入済み。C2E1のnative VIEW graph構築・品質testを技術受入済み。C2E2のVIEW証拠付きTABLE公開・独立公開履歴を技術受入済み | SQL／WAPが既定で、TABLE／EPHEMERALとrun専用SANDBOX INVOKER VIEWに対応。native共通条件は元本人・READY v2 plan・同DB RAW source・TABLEは固定PARQUET・新規SANDBOX候補。`native_table`はRAW／展開済EPHEMERAL依存の1モデルと付属suiteをopt-inし、全品質成功後にAの固定preview・最低1行監査で同DB MARTS公開。`native_graph`はVIEW→TABLE・TABLE間依存を含む対応する全DAGと保存全generic test一式をopt-inし、source testも含む。品質未指定はNOT_RUN。非対応nodeを含むgraph・品質指定時の空suiteを拒否。VIEWを含むnative graphのTABLE公開は全VIEWの定義・UUID／version・作成query・推移依存を固定する。graph公開は全RunTask／TestTask成功・全非空suiteの厳密な0件成功・全候補証拠を条件に、Aの固定previewと最低1行監査を経て選択TABLEだけを同DB MARTSへ反映。他TABLEは別操作。正式VIEW公開・VIEW連鎖、incremental・並列化・候補直接置換・manual-WAP rollback統合は後続 | [DB-P6-3B](database-p6-3b-implementation-2026-10-29.md)、[C2A](database-p6-3c2-sandbox-views-2026-10-30.md)、[C2B4](database-p6-3c2b4-native-quality-tests-2026-10-30.md)、[C2B5](database-p6-3c2b5-native-publication-2026-10-31.md)、[C2C](database-p6-3c2c-native-graph-2026-10-31.md)、[C2D](database-p6-3c2d-graph-publication-2026-10-31.md)、[C2E1](database-p6-3c2e1-native-views-2026-10-31.md)、[C2E2](database-p6-3c2e2-view-graph-publication-2026-10-31.md) |
| TRF-12 | 旧dynamic WAP一覧・refresh widget | 未接続の旧widget | 手動WAP／Managed Martを使用 | 変換の新パネル |
| JOB-01 | Job新規・一覧・Load・再Load・定義／引数保存 | 対応、E／A | J、owner＋保存role＋context、CAS | `/api/v1/jobs` |
| JOB-02 | SQL／alert step | 対応、E／A、既存SQL policy | J、alertはread-only | typed Job step |
| JOB-03 | Ingest／WAP refresh／Table export step | 対応、A | J、型付きadapterのみ | typed Job step |
| JOB-04 | 手動run、名前付きfile入力 | 対応、保存roleで実行可能なowner | J、受理版／引数／fileを固定 | Job run |
| JOB-05 | 自動poll・照会・実行／step履歴 | 対応、元owner／context | J、元runの状態 | Job runs |
| JOB-06 | run取消 | 対応、元ownerとcleanup契約 | J、失効後も結果閲覧とは分離 | Job cancel |
| JOB-07 | UNKNOWN runの照合 | 対応、E／A、元owner／context | J、backend証拠。未解決子を無視しない | Job reconcile |
| JOB-08 | Job予定一覧・新規・保存・有効化・停止・tick | 対応、E／A、Job owner。runtime upload Jobは不可 | J＋T、保存定義版 | schedules |
| JOB-09 | interval／daily／once／weekly／monthly／timezone | 対応、予定の入力契約 | T、競合・遅延の既存方針 | schedules |
| JOB-10 | alert一覧・更新・確認 | 対応、E／A、元owner／context | T、確認と解消を区別 | alerts |
| JOB-11 | native Stored Procedure／CALL／DAG／任意command | 別計画。Jobと同義にしない | Routineの意味を先に確定 | DB-P9-2 |
| AI-01 | Query Doctor Bear | 明示SQL・TABLE／条件付きVIEW実行計画の説明、最新SELECTの入力読込、保存済み計画の明示再説明に対応 | V／E／A、現在のSQLワークシートcontext・全参照schemaのquery権限。説明にはLOCAL AIを使用。VIEW新規計画は`logical_v1`の対応INVOKERと再帰依存の同一DB権限が条件。最新読込は本人・保存9項目contextが一致する対象の通常read-only SELECTのみ。再説明は元A2／A3のCOLLECTED＋FINISHED証拠と全凍結依存schemaの現在権限が条件。読込から説明を自動送信せず、再説明で元SQL・planを再実行しない | DB-P6-4A1／A2／A3／A4／A5対応 |
| AI-02 | Data Engineer Bear | 旧Bear画面は未接続。CopilotのB1 SQL案生成は条件付き対応、B2の手動WAP下書き引渡しは限定技術受入済み | 原案とWAP実行先のcontextを分離し、B2は同一DB／同一E／A roleと使用TABLEが条件。AIへ実行権限を渡さず、保存以降は既存WAPを使用 | DB-P6-4B1、B2限定技術受入済み |
| AI-03 | Catalog Curator Bear | C1のmetadata説明・説明文案に条件付き対応 | V／E／A、独立contextと同じprimary schemaの安定TABLE／VIEW 1件、現在のquery権限とLOCAL AI。名前・列名・型による推定で、列文案は最大8列。未適用の提案であり、旧検索／lineage・説明保存は対象外 | DB-P6-4C1限定技術受入済み |
| AI-04 | SRE Bear | C2AのDB内保存metadata・C2BのP専用固定LOCAL観測は限定技術受入済み。C2C1の宣言基準レビューも限定技術受入済み | DB内はV／E／A、本人・保存full9・primary schema権限。P専用は現在のP account・principal・上限admin・選択adminを独立検査しDB／grant不要。C2C1は固定2 templateの全ordinary／initに旧4宣言条件だけを確認し、SQL health・runtime security・旧SRE検査を保証しない | DB-P6-4C2A／C2B限定技術受入済み、[C2C1契約・限定技術受入](database-p6-4c2c1-workload-baseline-2026-11-02.md) |
| AI-05 | Policy Gatekeeper Bear | C2Aの現在context権限・C2BのP専用保存設定観測は限定技術受入済み。C2C1の4宣言レビューも限定技術受入済み | DB内の現在schema権限・SQL mode上限とP専用scopeを分離。C2C1は現在真P・選択adminで手動取得し、nonRoot=trueかつUID未指定は旧ruleどおり許す。met／not_metとsource確認不可だけで、8 admission policy・global policy全体・個別SQL承認やaggregate allowを示さない | DB-P6-4C2A／C2B限定技術受入済み、[C2C1契約・限定技術受入](database-p6-4c2c1-workload-baseline-2026-11-02.md) |
| AI-06 | AI provider／model／URL／key／timeout、接続test／保存 | 対応。共有設定、現在の真P・上限admin・選択global admin | DB／grant不要。key保護・接続先変更時のcredential解除。Database推論は既存LOCAL契約を維持し、疎通成功と区別 | [DB-P6-4E限定技術受入](database-p6-4e-ai-settings-2026-11-02.md)、`/api/v1/bears/ai-config`・`ai-test` |
| AUD-01 | Pawprints一覧・filter・status・更新 | 対応。P専用のglobal補助preview、最新100件以内 | Pが明示取得。DB／本人scopeへ帰属させず、耐久監査とは別表示 | DB-P6-1、旧API拒否は維持 |
| AUD-02 | 旧runtime query／load履歴画面 | 未接続。個別query／Job履歴は対応 | 個別履歴へ案内 | SQL／Job／WAP |
| AUD-03 | 統合Database監査の検索・詳細・download | 対応。保存metadataの検索・前後page・関連記録・JSON。D1のAI要約追加は限定技術受入済み | V／Eは本人・保存role・許可Schema、Aは対象DB。現在権限を再確認し、保存full9は内部整合のみ。Pのplatform scopeは基盤管理記録だけでAIを除外。本文・全遷移は対象外 | DB-P6-1実装契約、[D1保存AI要約](database-p6-4d1-ai-audit-metadata-2026-11-01.md)（既存event形状、source=ai、閉じた5種のaction、links=[]、内容field追加なし） |
| CMP-01 | ヘッダー／SQLの共有Warehouse状態・更新 | logical_v1は共有runtime観測を実装。技術受入済み | 物理状態・観測時刻・engine検証を分離し、replica数だけでSQL準備完了にしない | `/api/v1/warehouses/LOCAL/runtime`。旧Database profileは従来の観測のみ |
| CMP-02 | Compute panelの共有状態・停止blocker・保存policy・DB保存activity | 共有runtimeは認証済みV／E／Aで技術受入済み。P5Cのquery／job_run保存metadata一覧も限定技術受入済み | 共有状態と物理状態／engine検証を区別。P5Cは独立した手動self／database一覧で、本人・保存role・現在DB／Schema権限と既存effective DB admin例外を維持。live activity・SQL／結果・詳細／復旧・exportは含めず、CMP-05のP専用観測を開放しない | 共有LOCAL panel／runtime GET、[P5C契約・限定技術受入](database-p6-5c-contract-2026-11-02.md) |
| CMP-03 | quick／SQL／Computeの明示Wake／Chillと状態再検証 | logical_v1のP専用API／UI／HTTP CLIを実装。技術受入済み | 全DBへの影響を表示。元key／receipt保持、現在権限・admission／drain・全blockerを再確認。SQLは再送しない | runtime操作／operation照会。旧Database profileは未対応 |
| CMP-04 | Auto-Chill設定と許可済みSQLのAuto-resume | logical_v1で設定CAS、server側休止、SQL共通経路の自動復帰を実装。技術受入済み、cold 30回はp50 26.44秒・p95 26.92秒（30回すべて成功） | 設定はP、0は無効。自動起動は元のDB／role／intentを認可し、同一初回POSTを再送しない。queued取消・UNKNOWN保護を維持 | Workbench／native／CLI、既存DataOps・Job・Stage・Mart・対応済みdbt・予定の共通SQL経路。AIは未接続 |
| CMP-05 | ComputeのSRE check／Policy audit | P5Aの固定観測・P5Bの設定資源は限定技術受入済み。C2C1の宣言レビューも限定技術受入済み。旧SRE check／任意Policy baselineは未接続 | P専用の現在account・principal・上限admin・選択admin、DB／schema／grant不要。C2C1はSRE／Policy Platform・Computeの3面で独立cacheを明示共有し、固定2 templateの4宣言結果だけを表示。不正sourceは全体確認不可。既存C2B／P5B／P5Cと旧route拒否を維持し、security PASS・admission許可・usage／capacityや操作許可を示さない | P5A／[P5B](database-p6-5b-platform-resources-2026-11-02.md)限定技術受入済み、[C2C1契約・限定技術受入](database-p6-4c2c1-workload-baseline-2026-11-02.md)／DB-P6-4・5全体は未完了 |
| CMP-06 | XS／Sの実サイズ、Worker増減、用途別Warehouse | 未実装。休止・復帰の対応から伸縮完了を推定しない | 同一computeのaliasを維持し、容量・実資源・性能の計測後に採用 | DB-P7／8 |
| ADM-01 | DB管理一覧・選択・revision／binding情報 | 対応、P | A、Worksheetと独立 | `/api/v1/admin/databases` |
| ADM-02 | 論理DB作成、作成capability、初期access | 対応、P、logical_v1の準備条件 | A、root／actor／環境／key | admin database-creations |
| ADM-03 | 同じ要求の再発見・状態照会・固定payloadの再確認 | 対応、P | A、同じ要求を再発見 | 作成一覧／詳細 |
| ADM-04 | 作成の照合・resume・dispatch停止 | 対応、P＋元owner＋状態別action | A、logical／stagedの意味を分離 | creation recovery |
| ADM-05 | READYから新しいSQL | 対応、現在の許可role／schema | U＋Q、新しいWorksheet | 作成詳細 |
| ADM-06 | DB／Schema表示metadataのCAS更新 | 対応、P | A、SQL名／binding不変 | display API |
| ADM-07 | desired grant一覧・変更・取消 | 対応、P | A、desired／effectiveを分離。付与はbackend検証が条件 | grants API |
| ADM-08 | retire依存確認・要求・照会・照合 | 対応、P、非purge | A、既存UNKNOWNと履歴を保全 | retirement API |
| ADM-09 | 高度なregistry登録、構成plan／operator apply要求 | 条件付き、P＋準備済み材料／profile。通常logical CREATEとは別経路 | A、要求受付のみではREADYにしない | warehouses／operations |
| ADM-10 | staged plan確認・承認・再計画・回復承認・停止 | 条件付き、P＋元root owner＋staged modeの状態契約 | A、明示承認とoperator証拠 | staged creation操作 |
| ADM-11 | 物理purge、credential／PVCの削除 | 非purge retireには含まない | 別の破棄scopeが必要 | 別計画 |

## 画面と実装の対応

安定したIDの案内をパネルへ置き、手動WAP等の追加順序によって説明対象が変わらないようにする。取り込みと変換、保存metadata監査は対応済み操作へ案内する。logical_v1の共有LOCAL管理は、旧Database profileの非対応と一時的な観測失敗を区別する。dbt、AI Bears、SRE／Policy検査は未接続のまま、理由と利用できる確認先を示す。

Worksheet結果保存の案内は「結果保存が未対応」とし、対応済みTable全件Exportを否定しない。profilingとlineageは機能名を示し、対象identity未確認・対象／roleのcapability不足とは説明を分ける。未接続機能は管理者への変更を解決策にしない。

実装上の根拠は[認可](../../tools/local_experiment/request_auth.py)、[inventoryのcapability](../../tools/local_experiment/database_inventory.py)、[HTTP routeの拒否](../../tools/local_experiment/workbench_server.py)、[Workbench](../../tools/local_experiment/workbench/index.html)、[WAPのDB共有metadata](../../tools/local_experiment/manual_wap.py)、[Jobs](../../tools/local_experiment/db_jobs.py)、[Stages](../../tools/local_experiment/db_stages.py)、[Marts](../../tools/local_experiment/db_marts.py)、[予定とalert](../../tools/local_experiment/db_schedules.py)である。

## DB-P6-0の検証対象

全ナビゲーションと上表の操作について、対応済み／未接続の説明が正しいことを確認する。実行条件は各パネル自身で判定し、role切替、context欠落・失効、遅延応答、言語切替、refresh／reload後に誤って有効化しない。WAPとManaged Martへ到達でき、旧APIは引き続き拒否される。

本unitは監査API・dbt実行・Bears・Warehouse管理を新設しない。コード回帰、実ブラウザー、認可API、性能baselineの結果は[DB-P6-0実装と検証記録](database-p6-0-implementation-2026-10-29.md)へ記録する。

R1ではnative_graph公開の不確定COUNT監査だけをoperator CLIで復旧できる。元本人のplatform admin、取得可能なowner lease、凍結intent／全親子台帳、管理対象computeの停止証明を条件とする。公開・queryのUNKNOWNは保持し、画面には検証済みの実行閉鎖を別表示する。native_table／SQL-WAPや任意の不明書込みは対象外。[R1の契約と証拠](database-p6-3r1-dbt-audit-recovery-2026-10-31.md)。
