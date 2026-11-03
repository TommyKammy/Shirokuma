---
project: Shirokuma
doc_id: DB-P6-4C2H
title: Recent container log diagnostics for platform SRE
status: accepted
created: 2026-11-04
updated: 2026-11-04
version: "1.0"
area: guides
tags: [database, sre, compute, telemetry, acceptance]
---

# DB-P6-4C2H コンテナログの診断概要

| 旧SREの取得 | 現行のログ源と対応 |
| --- | --- |
| 全namespaceから名前で見つけたAgentHouse／Lokiのコンテナログ。直近10分・最大100行 | 専用Colima上の管理対象2コンテナのstdout／stderrを読み、取得範囲内の件数と警告・エラー・致命的ログの時刻を表示する |
| Lokiの `shirokuma-dev` namespaceを対象にしたerror／fatal本文検索 | 現行のLoki参照はtask IDで絞る正規化済み5項目。namespaceや重大度を含む検索対象ストリームの取り込みと閲覧範囲は別の残件 |
| 最新20件の失敗Pawprints | C2Eで保存済み失敗metadataを参照する |

[旧SRE](../../tools/local_experiment/bears_resident.py#L115) · [AgentHouse管理](../../tools/local_experiment/agenthouse/manage.py) · [Loki管理](../../tools/local_experiment/loki/manage.py) · [Loki設定](../../tools/local_experiment/loki/config.yaml)

現在のP管理者がSRE Platform／Computeから手動取得する。専用socketで固定コンテナの識別情報と公開ポート・mountを照合し、取得を10分・100行・64KiBに制限する。表示項目は取得行数、重大度別の件数、該当する直近最大20件のUTC時刻と重大度。SQLや接続情報が含まれ得る本文は取得処理内で分類に使う。空の取得結果と取得不可は分けて表示する。[Dockerログの時刻と取得範囲](https://docs.docker.com/reference/cli/docker/container/logs/)

## 検証

| 検証 | 結果 |
| --- | --- |
| 重点試験 | 161件成功。固定socket・所有照合・上限・異常な識別情報・重大度と時刻の抽出・本文の除外・権限失効・パネルの独立性を検証 |
| 全体回帰 | `make verify-local-experiment` の全131レシピを各1回実行。5,118件中5,084件成功、34件skip、失敗0。実行前後で対象7ファイルが一致 |
| 実ログ源 | 直近10分は両方0行。別の形式確認ではAgentHouseの過去20行とLokiの過去18行でUTCナノ秒時刻を確認。Lokiは先頭の `level=` を認識でき、AgentHouseの20行は今回の重大度分類の対象外。形式確認の記録は集計のみ |
| 実HTTPと認可 | 両HTTP面のP管理者で200。AgentHouseと停止中のLokiは取得成功、各0行・イベント0件。下位ロール4件は403。警告以上の表示と取得不可は制御した応答で検証 |
| 通常画面 | SRE／Computeで手動取得各1件、日英切替、390px幅、キーボード、再読み込み後の19ワークシートと入力保持を確認 |
| 保全 | 通常2プロセスの起動条件、control storeの12,271＋156行とschema、保護ファイル14件、既存UNKNOWNと6F残件を照合 |

検証記録は `var/shirokuma/db-p6-4c2h-sre-logs-2026-11-04/private/`、重点試験ログは `var/shirokuma/db-p6-4c2h-telemetry-logs-2026-11-04/private/` に保存した。
