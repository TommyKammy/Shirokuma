---
project: Shirokuma
doc_id: DB-P6-4C2J
title: Pasted workload declaration review for platform Policy
status: accepted
created: 2026-11-04
updated: 2026-11-04
version: "1.0"
area: guides
tags: [database, policy, manifest, acceptance]
---

# DB-P6-4C2J 貼り付けたmanifestの宣言確認

旧Policyの `check` は指定パスを `kubectl create --dry-run=client` で解析し、Pod specの4項目を確認した。WorkbenchではP管理者がPolicy PlatformへYAML／JSONを1文書貼り付けて検査する。対象はPod、Deployment、StatefulSet、DaemonSet、Job、CronJob。Pod specの位置は種類ごとに決め、通常・init containerと、Podのephemeral containerに既存の4条件を適用する。[旧Policy](../../tools/local_experiment/bears_resident.py#L504) · [C2I コントローラーテンプレート宣言](database-p6-4c2i-controller-baseline-2026-11-04.md)

貼り付けた文書をサーバーのメモリ上で解析する。本文はUTF-8で64KiBまで。対応範囲は1つのワークロード宣言で、複数文書、`List`、重複キー、別名、マージ、明示タグは入力エラーとする。結果はリソース種別、コンテナ種別・順序、4項目の宣言に限定する。画面の入力はその場で保持し、編集時に古い判定を消去する。再読み込みや権限失効で入力も消去する。

## 検証

| 検証 | 結果 |
| --- | --- |
| 重点試験 | 修正後の新規16件と関連147件が成功。6種類の宣言、入力上限、nullの継承、解析前後の権限、ロール上限更新、本文と名前の非返却を確認 |
| 全体回帰 | `make verify-local-experiment` の全135レシピを各1回実行。5,184件中5,150件成功、34件skip、失敗0。実行前後で対象7ファイルが一致 |
| 実HTTP・認可 | 両HTTP面で合成YAML PodとJSON CronJobの4項目が期待値と一致。不正入力2件は400、下位ロール4件は403。本文の確認用文字列は全応答から除外 |
| 通常画面 | キーボードで合成YAMLを1回検査し、4項目の一致・不一致を確認。日英と390px幅、編集による結果消去、再読み込みでの入力消去を確認。既存19ワークシートと入力は一致 |
| 保全 | 検査をメモリ上の解析に限定し、通常2プロセスの起動条件、control storeの12,271＋156行とschema、保護ファイル14件、既存UNKNOWNと6F残件を照合 |

HTTP・画面・保全の記録は `var/shirokuma/db-p6-4c2j-2026-11-04/private/`、重点試験は `var/shirokuma/db-p6-4c2j-manifest-advisory-2026-11-04/private/` に保存した。
