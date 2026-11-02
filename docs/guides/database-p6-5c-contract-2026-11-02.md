---
project: Shirokuma
doc_id: DB-P6-5C
title: Compute Database saved activity list
status: accepted
created: 2026-11-02
updated: 2026-11-02
version: "1.0"
area: guides
tags: [database, compute, activity, audit, authorization]
---

# DB-P6-5C: ComputeのDB保存activity一覧

2026-11-02 JSTの凍結契約に基づくUIのみの限定実装と技術受入の記録である。P5Cの限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,188成功・34 skip、既存AUD-03の実APIとWorkbenchの実画面、最終保全PASS）。既存[AUD-03](database-p6-1-contract-2026-10-29.md)の認可・API・保存metadata投影を再利用し、[P5B](database-p6-5b-platform-resources-2026-11-02.md)の限定技術受入や所有者本人の全体受入を代行しない。

## 対象と読取り契約

Computeのsibling `#observation-activity-panel`に、DB保存activityの一覧だけを追加した。初期sourceは単一選択の`query`／`job_run`で、既定はqueryとする。queryは許可された保存query_records全体を含み、WorksheetのSELECTだけには限定しない。job_runは保存されたrun要約である。現在のEngine活動、全状態遷移、完了の再確認を表さない。

Workbenchの既存`GET /api/v1/audit/events`と正確な`X-Shirokuma-Role`を使い、新backend・API・policy・catalog読取り・Makefile変更を追加しない。native SQL endpointのAudit対応を新たに主張しない。sourceのall／空選択とplatform scopeは提供しない。

参照role、明示READY DB、self／database scope、sourceごとの保存state、metadata search、page sizeをこのcardだけに保持する。searchは256文字以内、page sizeは既定20・1〜100である。手動検索と前／次pageだけを提供し、日付filter、詳細、関連link、download／export、inspect、復旧・再実行を追加しない。

## 現在認可と既存例外

V／Eのselfは本人・保存roleと現在のDB／query可能Schema権限を条件とする。database scopeは現在のcapabilitiesで利用可能な管理範囲だけとし、許可したDBの他actorを含む保存metadataを扱う。保存recordのcontextを現在のrevisionとの完全一致へ置き換えない。

既存AUD-03のeffective DB admin例外を維持する。現在のplatform_admin accountでaccount roleがadmin、選択roleがadminの場合には、明示DB grantを持たずgrant_revisionがnullでも対象になり得る。explicit-grant-onlyの制限は追加しない。この例外も現在の有効principal・maximum role、明示READY DB、現在binding／generation・query可能Schemaの検査を免除しない。platform scopeでDB workload記録を取得する導線にはしない。

一覧の前後に、このcardのroleでDB inventoryとcapabilitiesを新しく確認し、`dbAuditBinding`を比較、`dbAuditContext`を独立proofに照合する。現在認可の失効では、P5A／P5B cacheが空でも非表示の一覧を破棄する。旧epochの成功・errorが新しい状態を上書きしない。

## 公開metadataと保存state

既存event wireは変更しない。返却件数が要求limit以下で、DBとsourceが選択対象に一致することを検証する。sourceとidは検証だけに使い、直ちに`source_id`、`actor`、`schema_id`、`saved_role`、`state`、`created_at`、`finished_at`へ投影する。linksと余分なpayloadを捨て、SQL、parameters、error、結果、raw JSON、cursorを表示しない。

queryの保存stateは`QUEUED／RUNNING／FINISHED／FAILED／CANCELED／UNKNOWN`、job_runは`queued／running／succeeded／failed／canceled／unknown`としてcaseを保持する。過去記録のnullは未記録として示し、database scopeの他actorや過去のnull role／Schemaを不正扱いしない。selfは本人actorと選択した保存roleを条件とする。UNKNOWNの表示を停止証明・closure・再送許可へ変換しない。

表示には適用済みsource／scope／role／DB／filter、観測時刻、最新保存要約であることと既存retentionの限界を示す。DB名・SQL・時刻から記録の帰属を推測しない。

## 手動page・状態の分離

返された`page_cursor`を同page回収、`next_cursor`を次pageに使い、独立したcursor stackで前pageへ戻る。既存の署名cursorは不透明な文字列として24,000文字以内で扱い、解釈・描画・exportしない。409ではpage履歴を消し、明示的な新検索を案内する。

既存cursorは非暗号化base64とHMACを用い、台帳のrowid上限を含む。署名・認可・filter束縛を再利用し、挿入進行量の秘匿やpageが不変な全履歴であるとは主張しない。新しい暗号化・cursor protocol・server状態を作らない。

filter編集では進行中の応答を無効化し、このcardのpage・proof・cursorを消す。一覧の自動GETは行わない。同じ認可・対象ならnavigation／languageで完了cacheを保持し、認可更新はinventory／capabilitiesの確認と必要なpurgeだけを行う。新しいtimerやevent-list pollは追加しない。

Auditのdraft／applied filter／page／詳細／Blob URLと、P5A／P5Bのcontroller／cacheを使い回さない。pure helperだけを再利用する。既存runtimeのCAS・UNKNOWN・4入力を同一権限で保持したことを確認し、真の認可喪失時に既存security purgeで消える保護された表示の保持は保証しない。

## 検証証拠と限定技術受入

各層は別の証拠として扱い、重複する重点・事前・全体回帰の件数を合算しない。製品差分はindex.html、新規Compute activity UI test、既存Database UI testへのcard ID allowlist追加の3ファイルであり、backend・API・Makefileを含む22 guardファイルは変更していない。

| 層 | 結果と適用範囲 |
|---|---|
| focused UI／事前回帰 | 重点UI160件とMarts3件が成功し、activityの新規22件でsource・state・page・filter・現在認可・遅延応答・独立状態を確認。日英の前／次pageラベル2箇所だけの最終修正後は90件が成功。全Workbench958件の事前回帰はそのラベル修正前で、最終sourceは下記canonicalで確認した。 |
| 実API | Workbench8888の既存events GET58ケース（200が34、404が16、409が8）とcapability GET4ケース（200が3、404が1）の計62 GETが期待結果と一致。A／Bのquery database一覧173／302件を読取り専用の保存行と全pageで照合し、同page回収・source／limit変更cursor拒否を確認。新APIやnative Audit対応を追加していない。 |
| Jobの保存要約 | A／Bのjob_runは0件。別の既存READY DBの過去job_run1件をself／databaseで保存行と照合し、succeededの小文字filter・同page回収を確認。大文字filterは0件。今回の新規Job実行は0件である。 |
| 認可 | 現在のP accountを選択adminで使う既存effective DB admin例外ではnull grant_revisionでもA／Bを取得し、Pの選択E／Vは拒否された。実E／Vの本人scopeは取得、database scopeは拒否。非P effective DBAの正例はfixtureで確認し、実機のlogical_engineerによるadmin要求はaccount／maximum ceiling不足の拒否として扱う。 |
| 実画面の一覧／page | queryのB／database・limit2で前後pageと元の2行の再表示を確認。UNKNOWN保存行1件と、別DBのsucceeded保存Job1件を表示し、source／filter編集でpageとcontextを消去。新cardの詳細・inspect・実行・復旧・download操作はない。 |
| 画面の認可／表示 | 可視・非表示でグローバルロール上限を下げると自分の行を消去し、P5A／P5B cacheが空の場合もhidden purgeを確認。現在のP adminで参照roleだけVに下げるとA／B候補がなく検索不可。復帰・認可更新で自動LISTなし。同権限のnavigation／日英ではsnapshot時刻とpageを維持。 |
| 既存状態の独立／復元 | Auditの未適用条件・適用結果・行・詳細・download有効状態、P5A／P5Bの完了snapshot、未保存Auto-Chill15分、4入力をP5C読取り後も保持。検証後はAudit self／admin／A／all sources／50件・空条件・詳細なし、runtime入力0、元の4入力へ復元した。実行・保存ボタンは押していない。 |
| networkの適用範囲 | 全タブ共有serverの検証区間は1,042 GETであり、新tabだけの件数ではない。そのうち明示activity一覧8、既存Audit一覧3・詳細1は全て200で、自動LIST・mutation・console warning／errorは0。既存platform-observations2とplatform-resources2は明示比較取得で、他のBears pathはない。既存runtime110 GET等をP5Cの新しいpollやEngine実行に読み替えない。 |
| 画面幅と最終画面 | 日英390pxでpage client／scrollが390、card client／scrollが364でoverflowなし。既存の翻訳を使う前／次pageラベルも確認し、viewportは1920へ戻した。最終画面はadminの保存Job1件で、console異常0。 |
| 最終保全 | active11,955行＋default156行を全て保持し、新規行0。schema23、既存UNKNOWN、7保護ファイルの内容／mode、31 TABLE／33 VIEW headは一致した。API・画面の両検証でも保存行・設定を変更せず、実行・復旧・lifecycle操作を行っていない。 |
| canonical全体回帰 | 最終3 source／testと22無変更guardを固定し、99群・4,222件中4,188成功・34 skip・失敗0・error0・終了コード0。skipの名称・理由は前回と同じ34件で、追加・削除なし。 |
| doctor | 最終確認は2026-11-02T02:06:13Z、終了コード0・overall healthy、46確認中45 healthyとSQL informational1。新しいEngine SQL実行／probeの証拠ではない。 |
| design-context | 最終文書で14件成功・終了コード0、design-context正常（documents 94／issues 10／references 42）。226ローカルリンク、89能力IDの順序、変更2行以外の87行とP5B本文prefixの保全、差分チェックも確認した。 |

上記は保存metadataの読取り統合という限定範囲の技術受入であり、所有者本人による全体・運用受入の代行ではない。既存cursorの挿入進行量や全履歴の秘匿・不変性、native endpointのAudit表示、現在のEngine実行状態を新たに保証しない。

## 残る境界

P6-4全体／P6-5全体／P6-7／M1所有者本人受入は未完了である。C2C／global policy、包括的なactivity、live telemetry、usage／capacity、AI設定、resizeは独立した後続単位とする。既存SRE check／Policy baseline routeの拒否とP5A／P5BのP専用認可は維持する。
