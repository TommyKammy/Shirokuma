---
project: Shirokuma
doc_id: DB-P6-6F-R1
title: Cleanup of a committed Manual Ingest stage
status: accepted
created: 2026-11-04
updated: 2026-11-04
version: "1.0"
area: guides
tags: [database, ingest, cleanup, recovery, acceptance]
---

# DB-P6-6F-R1 コミット済みIngestのステージ清掃

6Fの取込は3行をコミットし、その後のTrino `DROP TABLE` が失敗した。元の操作には失敗した子クエリ、所有済みSANDBOXステージ、別の公開先を指す終端claimが残る。台帳は `DATAOPS_STEP_FAILED` を保持していた。元のTrino IDを読取り専用で照会し、`ICEBERG_CATALOG_ERROR` と内部のpurgeへの言及を確認した。拒否理由の詳細は確定していない。[6Fの受入記録](database-p6-6f-dataops-recovery-2026-11-03.md)

Trino 483のIceberg REST用 `DROP TABLE` はpurgeを要求し、6Eで確認した稼働Polaris構成ではpurgeが許可されていなかった。この組み合わせを踏まえ、コミット後のManual Ingestが所有するステージに6Eの非purge登録解除を使う。元の操作と公開先は照合用の基準とする。[TrinoのREST catalog実装](https://github.com/trinodb/trino/blob/483/plugin/trino-iceberg/src/main/java/io/trino/plugin/iceberg/catalog/rest/TrinoRestCatalog.java) · [6Eの受入記録](database-p6-6e-table-drop-2026-11-02.md)

現在権限、操作とステージの所有関係、同一Database・SANDBOX、incarnation、catalog UUIDを照合する。送信前に判定未確定の記録を残す。204の受領後にステージの登録退役と清掃成功を同じ台帳トランザクションに記録する。物理ファイルは保持される。応答喪失時は清掃未確定として扱い、元のコミット結果と分けて表示する。

同じ `manual-ingest` 経路を使うJobの取込にも適用する。コミット確定後に清掃の結果だけが未確定の場合、親操作は `FINISHED` を保持し、Jobは後続stepへ進める。コミット前の失敗と他の取込経路の清掃は従来の処理を使う。

## 検証

- 重点試験で所有関係、認可、UUIDの不一致、登録解除の拒否と応答喪失、コミット後の旧SQL清掃へのフォールバック防止を確認した。
- 通常Workbench APIで検証用CSVを1回取り込み、親操作 `c3fe0c6f-edd0-46da-bff3-da1e269e292e` は `FINISHED`、コミットは3行、清掃は `ACKNOWLEDGED`（HTTP 204）となった。公開先のIcebergスナップショットも3行を示した。新規ステージはカタログから参照できなくなり、台帳の登録は退役し、所有記録が残った。SQL子操作は通常の4件だった。
- 最初の検証では、取込POSTを扱わない8080に送信してHTTP 404を受けた。ルーティングと台帳で未受付を確かめ、未使用だった同じキーで通常Workbench API（8888）に1回送信した。
- 全体回帰は135グループ、5,192件中5,158件成功・34件スキップ・失敗0件。設計整合チェックも通過した。
- 元の6F公開先・ステージのカタログメタデータと既存台帳は照合前後で一致した。検証用の公開先と取込台帳は検証証拠として保持した。

実環境確認の範囲はWorkbench APIの取込、カタログ、台帳。M1本人受入は継続する。
