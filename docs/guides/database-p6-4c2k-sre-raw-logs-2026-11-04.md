---
project: Shirokuma
doc_id: DB-P6-4C2K
title: Manually view managed telemetry container logs in Workbench
status: accepted
created: 2026-11-04
updated: 2026-11-04
version: "1.0"
area: guides
tags: [database, sre, telemetry, acceptance]
---

# DB-P6-4C2K SREのコンテナログ本文

| 旧SREの取得 | 今回の対応 |
| --- | --- |
| コンテナログ本文 | 旧実装は名前で見つけたKubernetesのAgentHouse／Lokiに対して取得した。現行ではC2Hと同じ専用Colimaの管理対象2コンテナを使い、SRE Platformに本文の手動取得を追加する。 |
| Loki error／fatal検索 | 旧実装は `{namespace="shirokuma-dev"} \|~ "(?i)error\|fatal"` を最大20件検索した。現行の投入は `service_name=shirokuma-local,component=pawprint` の正規化済み5項目で、task ID参照が契約。名前空間別の対象ストリームと閲覧・保持範囲の定義を後続で扱う。 |

[旧SRE](../../tools/local_experiment/bears_resident.py#L115) · [C2Hの診断概要](database-p6-4c2h-sre-logs-2026-11-04.md) · [現行Lokiの投入と照会](../../tools/local_experiment/loki/manage.py#L98) · [LogQLのストリーム選択](https://grafana.com/docs/loki/latest/query/log_queries/)

## 契約

通常WorkbenchのAI BearsからSRE、Platformを開く。現在のP管理者権限を更新し、本文を明示取得する。AgentHouseとLokiごとに採取時刻、stdout／stderr、行数を表示し、空と取得不可を区別する。C2Hの診断概要とは別の操作。

専用Docker socket、固定コンテナの所有照合、現在権限の前後検査をC2Hと共有する。取得は各コンテナの直近10分・最大100行・64KiB。Dockerの時刻付きUTF-8本文をストリーム別に扱う。不完全な行、時刻やUTF-8の不正、上限超過、採取失敗はそのソースを取得不可とする。[Dockerログ取得](https://docs.docker.com/reference/cli/docker/container/logs/)

本文にはSQLや接続情報が含まれ得る。取得前に画面で知らせ、伏字を加えずテキストとして一時表示する。両HTTP面の専用GETは入力を受けず、異なるOriginを拒否し、`Cache-Control: no-store` を付ける。本文は永続化やAI入力への転送の対象外とする。明示消去、画面離脱、認可の変更で本文を消去する。

## 検証

| 層 | 結果 |
| --- | --- |
| 重点試験 | 101件成功。合成ログでstdout／stderrの正確な文字、マークアップのテキスト表示、範囲・所有・失効・遅延応答を確認。C2Hの概要・readiness・失敗metadataを回帰検証。 |
| 実HTTPと認可 | 2つの通常HTTP面でP管理者は200、下位ロール4件と異なるOrigin 2件は403、想定外の入力2件は400。各応答の `no-store` を確認した。採取範囲は両コンテナとも取得可能・0行。 |
| 通常Chrome | AI Bears → SRE → Platformで権限更新と手動取得を日英各1回実施。キーボードでの消去、Computeへの移動と復帰、閲覧者ロールでの無効化、再読み込み後の非復元を確認した。 |
| 全体回帰 | 135レシピ、5,206件中5,172件成功・34件スキップ・失敗0件。設計整合チェックと専用プロファイルのdoctorも通過した。 |
| 保全 | 2つの通常サーバーを同じ起動条件で更新し、台帳12,636行と既定台帳156行、保護ファイル14件、既存の未確定記録とエンジン検証を照合した。取得の証拠は状態・件数・バイト数を記録した。 |
