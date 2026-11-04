---
project: Shirokuma
doc_id: DB-P6-4C2I
title: Namespace controller template review for platform Policy
status: accepted
created: 2026-11-04
updated: 2026-11-04
version: "1.0"
area: guides
tags: [database, policy, compute, controllers, acceptance]
---

# DB-P6-4C2I namespace内のコントローラーテンプレート宣言

旧Policy baselineは選択namespaceでDeployment、StatefulSet、DaemonSet、Job、CronJob、Podを検査した。C2Fの実Podと対応する次の単位として、登録済みFoundationが指定するnamespaceの5種のコントローラーを読む。各コントローラーのPod templateを対象にし、CronJobではjob template内のPod templateを確認する。ラベルや稼働状態にかかわらず対象namespaceの一覧を扱う。[旧Policy](../../tools/local_experiment/bears_resident.py#L504) · [C2F 実Pod宣言](database-p6-4c2f-live-pod-baseline-2026-11-03.md) · [CronJobのJob template](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/#job-template)

Policy Platform／ComputeのP管理者が手動で取得する。通常・init containerごとにread-only root filesystem、non-root、ALL capabilitiesのdrop、CPUとメモリの正のlimitsを宣言上の値で確認する。参照対象・時刻・個別結果を表示し、正常な空一覧と取得不可を分ける。実Podのephemeral containerはC2Fの画面で確認する。

SREの本文閲覧は公開範囲と機密情報の扱いを整理する。旧Loki検索には検索対象ストリームの取り込み契約が必要。Policyの任意manifest入力と他namespaceへの拡大要否も別に扱う。[C2H ログ診断](database-p6-4c2h-sre-logs-2026-11-04.md)

## 検証

| 検証 | 結果 |
| --- | --- |
| 重点試験 | 新規30件と関連135件が成功。5種の射影、CronJobのネスト、権限とFoundationの前後照合、空一覧と取得不可、画面の独立性を確認 |
| 全体回帰 | `make verify-local-experiment` の全133レシピを各1回実行。5,163件中5,129件成功、34件skip、失敗0。実行前後で対象7ファイルが一致 |
| 実HTTP・認可 | 両HTTP面でP管理者の取得が成功。下位ロール4件は403 |
| 実コントローラー | Deployment 6件とJob 1件、通常7・init 3の計10 container。4項目×10の宣言結果が一致。種類・名前だけの独立取得が両API応答と一致。StatefulSet・DaemonSet・CronJobは実環境で0件であり、3種の宣言は重点試験で確認 |
| 通常画面 | Policy／Computeで手動取得各1件。キーボード展開、日英切替、390px幅、再読み込み後の19ワークシートと入力保持を確認 |
| doctor・保全 | doctorはexit 0。通常2プロセスの起動条件、control storeの12,271＋156行とschema、保護ファイル14件、既存UNKNOWNと6F残件を照合 |

実環境と通常画面の記録は `var/shirokuma/db-p6-4c2i-2026-11-04/private/`、重点試験は `var/shirokuma/db-p6-4c2i-controller-baseline-2026-11-04/private/` に保存した。
