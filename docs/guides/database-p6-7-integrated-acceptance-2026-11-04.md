---
project: Shirokuma
doc_id: DB-P6-7
title: Database Workbench integration acceptance
status: in_progress
created: 2026-11-04
updated: 2026-11-04
version: "0.3"
area: guides
tags: [database, workbench, integration, acceptance]
---

# DB-P6-7 Workbench統合受入

2026-11-04 JST、6F-R1を含む `9e9ce251` を基準に、現行ラボの通常Workbenchで予備縦断に着手した。専用の新規DB `p67_integrated_20261104` と合成データを使った。独立したclean環境と故障環境での受入は別の判定欄に置く。[統合受入条件](database-full-capability-and-compute-plan-2026-10-28.md#db-p6-7-統合受入)

## 必須範囲の照合

現行の機能表89 IDと0R1の台帳が一致することを確認した。0R1の区分はM1義務82行、別track 3行（ING-11／JOB-11／CMP-06）、範囲判断4行（SQL-09／ADM-09〜11）。その後の限定受入を照合し、範囲区分の変更合意は確認できていない。[機能表](database-capability-matrix-2026-10-29.md) · [0R1の監査](database-p6-0r1-capability-closure-audit-2026-11-03.md)

| 行 | 今回の照合 |
| --- | --- |
| ING-03／TRF-06、AUD-02、SQL-12／13 | 6Fの元操作への再接続と6F-R1の非purge清掃、1Aの保存履歴対応、B3のWAP／Managed Martsへの案内を既存の限定証拠に対応付けた。 |
| SQL-09 | Worksheetから、現在の接続先を確認するSQLを読取り専用の新規下書きに開ける。Data Explorerには選択した表の読取りSQLを開く導線がある。両導線を旧3種のサンプルメニューの代替として受け入れるかは、所有者の範囲判断に残る。 |
| AI-04／CMP-05 | 固定のreadiness、診断集計、保存済み失敗metadata、固定Docker 2コンテナの生ログとLoki重大度検索に限定対応した。登録済み基盤のOPA重大度検索と旧名前空間全体の範囲はC2Mで区別する。 |
| AI-02／AI-03／AI-05 | 既存の限定操作を受入済み。任意Snapshotへの旧rollback、横断raw metadata／説明保存、登録外namespaceと広いpolicy scopeについて、旧操作との対応範囲を確定する。 |
| ADM-09〜11 | 高度な登録・staged operator操作には条件付きの導線がある。既存退役はデータ・資格情報を保持する。operator受入と物理purgeの要否は範囲判断に残る。 |
| TRF-11 | 承認済みprojectの保存plan、build／test、明示TABLE公開を対象にする。正式VIEW公開・VIEW連鎖・incremental・並列実行は別拡張。 |

[C2Mの収集範囲とSQL-09／ADM-09〜11の照合](database-p6-4c2m-sre-foundation-logs-2026-11-04.md)

SQL-09の接続先確認SQLは、現在のDB・スキーマ・実行ロールを引き継いだ読取り専用の新規下書きとして開く。通常Chromeでは、操作後のクエリ台帳1,054件が一致し、明示実行1回で実Trinoが `p67_integrated_20261104 / raw` を返した。実行記録 `e35eab66-d7ce-47b1-b9e3-6fedb685d00a` は `LOCAL / admin / read-only`。元の下書きと書込みモードの保持、日英ラベル、ロール上限による操作無効、Workbenchの70テスト成功を確認した。

## 現行環境での予備縦断

| 区間 | 観測 |
| --- | --- |
| DB作成と初回SQL | DB管理で初期grantなしで作成し、4固定スキーマとREADYを確認。新しいWorksheetの `raw / admin / LOCAL / read-only` から `current_catalog`、`current_schema`、marker 67 が一致した。 |
| Manual Ingestと再接続 | 合成CSVを `raw.p67_events` に3行コミット。清掃は登録解除ACK 204。再読み込み後に保存済みの同じ操作IDに再接続し、別の実SQLで3行の値を照合した。 |
| dbtと公開 | 同じRAWにLake martsの入力5表・11行を取り込み、各操作で清掃ACK 204を確認した。保存プランは5モデル、SQL/WAPで候補5件（SANDBOX INVOKER VIEW 3件、TABLE 2件）を構築しテスト7件が合格した。 `fct_orders` だけを明示公開し、MARTSの実SQLで注文201・202の純売上130.0000・60.0000と明細数2・1を確認した。 |
| AI | 同じMARTSのSQLをCopilotの実ローカル推論で説明した。 `qwen3.8-flash-next` の説明が完了し、AIの台帳はSQL未実行・実行計画未取得を保持した。 |
| Jobと監査 | `p67_orders_check` v1を同じMARTS・admin・read-onlyで保存し、手動runのSQL stepが成功した。通常の保存メタデータ監査で元のJob、AI説明、dbt公開を同じIDで照合した。 |
| 一回限りの予定 | Job v1の予定を無効で保存し、明示有効化した。1件のtickからSQL stepが成功し、予定は無効・次回実行なしになった。監査では手動・予定の両run IDを照合した。 |
| LOCAL休止と復帰 | 一回限りの予定を消費した後、休止阻害の全件数0を確認して共有LOCALを1回休止した。0/0の休止状態から同じMARTS SELECTを1回実行し、自動起動の待機を経て2/2の稼働と同じ2行を確認した。 |
| 認可 | 非Pの既存閲覧者で、許可されたAのMARTSスキーマ一覧は200、grantのない検証DBとAにBのスキーマIDを組み合わせた要求は404。DB grantの変更や閲覧者のSQL実行を伴わない実API確認。Workbenchのrole上限を閲覧者へ下げると保存済みadmin Worksheetが実行不可になり、元の上限とコンテキストを明示的に再選択した。 |

照合に使った主な元IDは、作成 `d8451129a98a45c2a500a5e8169f5321`、Ingest `9a1ed730-05e5-467a-8920-81cbadd9d114`、dbt run `6d06336d-71c0-45c0-bdfc-24c1a5025805`、公開 `80321d30-1836-47b3-be83-555d2e46ca63`、手動Job `8d1b78ec-fbf0-448a-ac1b-dee400442778`、予定Job `130dbe3d-0557-4551-8719-339a8b150dd5`、休止 `3965ef31-6904-44ea-8973-006fce88af0d`、復帰後SQL `27086f9c-3366-4ab0-bf77-b21d46affd20`。

## 作成画面の遷移修正

READYの作成リンクからSQLを開き、Ingestへ移動して再読み込みすると、URLに残った作成識別子でDB管理を開き直していた。認可されたDB管理画面から別の有効なタブに移るとき、作成に属するURL項目を取り除く。正しい作成直接リンク、他のURL項目、元操作の追跡を保持する。

修正版の通常Chromeで、同じ作成直接リンクからREADY詳細を開き、新しいSQLへ移動して作成項目が外れることを確認した。Ingestへ移動して再読み込みした後は既定Worksheetが開き、保存済みのRAWコンテキストを使えた。Ingestを開いて同じ元操作へ再接続した。テストでは無効タブ、権限失効による画面退避、遅延詳細の応答も確認した。

## 検証記録

重点の作成・画面遷移91件、設計整合チェック、専用プロファイルのdoctorを通過した。全体回帰135グループは5,193件中5,159件成功・34件スキップ・失敗0件。既存台帳と新規DBの操作を照合し、従来の未確定記録を保持した。今回の休止・自動起動、更新された同世代のengine検証、復帰後のSQLを区別して記録した。

## 判定を分ける残件

- 独立clean環境と故障環境での縦断、必須範囲の閉鎖、本人の操作性評価を継続する。元の失敗・未確定履歴を証拠として保持する。[環境と受入層](database-full-capability-and-compute-plan-2026-10-28.md#8-共通の受入と性能評価)
- 作成性能はREADYと同じRAWでの初回SQL成功を含む標本で別に判定する。今回の単一作成は機能確認。既存20標本ではp95 2.759秒、p50 2秒目標は未達。[作成性能記録](database-logical-create-performance-2026-10-28.md)
