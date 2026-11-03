---
project: Shirokuma
doc_id: DB-P6-4C2G
title: Telemetry endpoint checks for platform SRE
status: accepted
created: 2026-11-04
updated: 2026-11-04
version: "1.0"
area: guides
tags: [database, sre, compute, telemetry, acceptance]
---

# DB-P6-4C2G テレメトリのHTTP応答確認

旧SREは全namespaceからAgentHouse／LokiのDeployment・StatefulSetを探し、ready数、直近10分・最大100行のコンテナログ、Lokiのerror／fatal検索を集約した。現行の両サービスは専用Docker環境に固定のloopback入口を持つ。[旧SRE](../../tools/local_experiment/bears_resident.py#L115)／[AgentHouse管理](../../tools/local_experiment/agenthouse/manage.py)／[Loki管理](../../tools/local_experiment/loki/manage.py)

| 旧出力 | 現行の対応 |
| --- | --- |
| AgentHouse／Lokiのready数 | 今回は固定HTTP入口を各1回観測。AgentHouseは `/ping` の応答、Lokiは `/ready` の結果を個別に表示する |
| 生ログとLokiのerror／fatal検索 | 閲覧対象、期間、行数、機密情報の扱いを決める残件。現行のLoki参照はtask IDで絞る正規化済み5項目 |
| 最新の失敗Pawprints | C2Eの保存済み失敗metadata最大20件で対応。[C2E](database-p6-4c2e-recent-failures-2026-11-03.md) |

現在のP管理者がSRE Platform／Computeから手動取得する。両HTTP面で現在権限とロール上限を確認し、サービスごとの時刻と結果を返す。観測範囲はHTTP応答とLokiが報告するreadiness。コンテナの所有・実行状態やログの投入・検索は別の観測対象とする。[ClickHouse HTTP仕様](https://clickhouse.com/docs/concepts/features/interfaces/http)／[Loki readiness仕様](https://grafana.com/docs/loki/latest/reference/loki-http-api/#readiness-probe)

## 検証

| 検証 | 結果 |
| --- | --- |
| 重点試験 | 149件成功。最終backend 7件とUI 11件を含む。Lokiの200／503、接続失敗、応答の上限、両HTTP面の認可、画面切替中の遅延応答を試験した |
| 全体回帰 | `make verify-local-experiment` の全129レシピを各1回実行。5,103件中5,069件成功、34件skip、失敗0。実行前後で対象7ファイルが一致 |
| 実HTTP | 8888／8080ともP管理者で200。AgentHouseは応答あり、停止中のLokiは取得不可。選択ロールを閲覧者／データ作成者にした4件は403 |
| 通常画面 | SRE／Computeで手動取得各1件。UTC時刻、取得結果の独立保持、日英切替と390px幅を確認。再読み込み後に19ワークシートと選択・SQLコンテキスト、Bear 28入力、Audit 11入力が一致 |
| 保全 | 通常2プロセスを同じ起動引数・作業ディレクトリ・環境で更新。control storeの12,271＋156行とschema、保護ファイル14件、既存UNKNOWNと6F残件を照合 |

Lokiの準備完了と準備未完了は制御した応答で検証した。実環境の観測結果は取得不可。検証記録は `var/shirokuma/db-p6-4c2g-sre-readiness-2026-11-04/private/`、重点試験ログは `var/shirokuma/db-p6-4c2g-telemetry-readiness-2026-11-04/private/` に保存した。
