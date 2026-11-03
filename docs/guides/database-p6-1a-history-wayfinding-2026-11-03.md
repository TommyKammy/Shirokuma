---
project: Shirokuma
doc_id: DB-P6-1A
title: Saved history wayfinding for queries loads and actions
status: accepted
created: 2026-11-03
updated: 2026-11-03
version: "1.0"
area: guides
tags: [database, audit, history, acceptance]
---

# DB-P6-1A: query・load・actionの保存履歴をたどる

[閉鎖監査のAUD-02](database-p6-0r1-capability-closure-audit-2026-11-03.md)で残った履歴の対応付けと通常到達性を扱う。既存[Database Audit](database-p6-1-implementation-2026-10-29.md)の読取り・認可・保持を使い、通常の監査画面へ案内と履歴種別の説明を追加する。

## 旧履歴と現在の入口

旧Runtime履歴はengineの直近50件以内のqueryと、Pawprintsの直近100件以内から選んだ以下のload／action種別であり、全操作の永続的な完全履歴ではなかった。旧cross-DB履歴APIを再開放せず、現在の保存記録と通常入口を対応付ける。

| 旧種別 | 現在の通常入口・保存記録 |
| --- | --- |
| warehouse.query | SQL WorksheetのRecent Query History、監査のquery |
| lake.ingest | Ingestの元処理追跡、監査のdataops。登録Stageの取り込み／出力実行はstage_run、Stage走査はstage_scan |
| lake.export | ExplorerのSnapshot固定Table Export元処理、監査のdataops。登録Stageの出力実行はstage_run |
| catalog.clone | ExplorerのClone元処理、監査のdataops |
| catalog.drop | TableのDrop元処理、監査のdataops |
| bear.data_engineer | Copilotの保存AI metadata、Manual WAPのmodel版／build／audit／publication、dataops。登録済み対象の更新はManaged Martsのmart_run |

Jobsのrun／step、Database dbt、予定／tickは各通常画面の履歴と、監査のjob_run／job_step、dbt、schedule／schedule_tickに対応する。DB作成・管理などの既存sourceも選択欄に残す。いずれも現在のself／DB administration／Platform managementの認可・DB隔離・role上限を適用する。

## 実装した契約

現在提供されたsourceの表示名だけを日英で説明的にし、wire値は変えない。提供されないsourceを追加せず、未知の提供値は元の文字列で表示する。query／dataops／stage_run／job_run／wap_build／wap_audit／wap_publication／aiの8選択は、現在提供されたsourceだけを選ぶ。source一覧はbackendが認識する種別であり、記録の存在や各scopeでの閲覧権限を保証しない。selfは元本人・保存role・許可schema、DB administrationは既存の管理条件に従い、Platform scopeには既存の基盤管理記録だけが現れる。

選択はsourceのdraftを更新して「検索で適用」を示し、source欄へfocusする。他のfilter draft、適用済み条件、page cursor、詳細選択、各画面の独立context・未保存入力・元処理追跡を保持する。検索・詳細取得・画面遷移・提案取込・SQLやDataOpsの再送は行わない。capabilityの欠落・purge・権限不足時は選択できない。

保存記録は作成時刻と現在の保存状態のmetadataであり、全状態遷移ではない。AI成功はデータcommitを示さず、Ingestなどのcommitとcleanup errorも別に読む。関連IDは内容へのアクセス権限を与えない。旧engine／Pawprints sampleの移行、全read操作の記録、新しい履歴APIは追加しない。

## 検証と受入

2026-11-03 JST、以下の範囲で限定技術受入を完了した。実装、重点・全体試験、実ブラウザーの通常導線とrole条件、台帳・設定保全を分けて確認する。

| 層 | 今回の確認 |
| --- | --- |
| 重点試験 | Audit UI 33、Database UI 69、共通Workbench 98、AI plan UI 39の計239件が成功。sourceごとの選択、認可・提供optionの再確認、取得中の未適用条件、20種の表示名・未知値fallback・実optionの同一性を確認。初回の新試験1件は明示認可更新を表示切替の無通信区間へ含めた計数位置を補正し、製品コードを保持して239件を再確認 |
| 全体回帰 | canonical 123群・総計4,970件を1回実行し、4,936成功・34 skip、失敗0（1,002.6秒）。通常Workbench群1,223件を含む |
| 文書整合 | design-contextの14試験と94文書・10 issue参照・42参照を確認。機能表の89 ID／順序を維持し、AUD-02以外の88行と日付固定の0R1監査は不変 |
| 通常導線 | 折り畳み式の対応表、20種の日英表示とwire値の保持、8個の選択、Enterキーとsource欄へのfocusを実画面で確認。8選択で他のdraft条件・適用済み履歴・詳細・ページ状態を保持し、自動の履歴検索は0件。区間内に通常認可の背景GET 8件があり、全HTTP 0とは判定しない |
| DB・role | adminの独立した監査参照でA／Bの保存queryを明示検索し、表示行は選択DBの記録だけ。現在のセッションでE／VのDB inventoryは利用不可となり、履歴・詳細の消去と全8選択の無効化を確認。E／Vの成功条件・上限の正例は重点試験の証拠と区別する |
| 既存保存記録 | Auditのmetadata読取りだけで、AのIngestのcommit済みと清掃エラー、BのWAP build、別DBのJob run、AのAI保存metadataを照合。通常JobのrecoverやBのSQL history inspectへ進まず、元要求の再送・清掃・新規AI生成を行わない |
| 表示 | 日英・通常1920pxと390px幅を確認。狭幅では両言語ともdocument幅390pxで、8ボタンが画面内に折り返す。実200% zoomは未実施 |
| ブラウザー後片付け | 元の20 tab項目（詳細＋19 Worksheet）・選択・SQL／context、Bears入力28項目が開始時と完全一致。日本語と通常1920pxへ戻し、検証タブを閉じた |
| 実行・保全境界 | 完全なWorkbenchログ区間はGET 1,864件・全200、POST／他のmutation・旧/api/historyは0。8080はDEBUGログのため0bytesを全通信0の証拠にしない。active台帳12,271行／79表とdefault台帳156行／78表の全行・schemaは一致し追加0。元UNKNOWN query 3／admission 3／AI 6／dbt 1、active 0、保護12ファイルとmode・owner 2 YAML・engine証跡・backend 17ファイル・通常2 PIDと環境を保持 |

ブラウザーのCDP記録は28区間で欠落なし・mutation 0。保存行のGETは現在の権限に従うmetadata照会であり、実engine／LLMを使う新規操作の受入ではない。HTMLは通常サーバーへの認証済みGET 1回で凍結sourceと一致した。

証拠は非公開の `var/shirokuma/db-p6-1a-history-wayfinding-2026-11-03/private/` に保存する。重点試験の初回と補正後、canonical全体、source hash、served HTML、browser／network要約と画像、アクセスログ有限区間、台帳全行・schema・保護ファイル・PID比較を分けて保持する。

## 残るgateと次の単位

AUD-02の対応付けを、全89行の閉鎖・M1本人受入と同一視しない。[6FのIngest清掃残件](database-p6-6f-dataops-recovery-2026-11-03.md)も保持する。次はAI-04／AI-05／CMP-05の既存必須操作と現行観測・通常導線を限定して照合する。実Compute伸縮はM1後とする。
