---
project: Shirokuma
doc_id: DB-P6-4C2M
title: Search sampled Foundation OPA errors in Loki
status: accepted
created: 2026-11-04
updated: 2026-11-04
version: "1.0"
area: guides
tags: [database, sre, loki, telemetry, acceptance]
---

# DB-P6-4C2M 登録済み基盤のOPAログ検索

旧SREは `shirokuma-dev` のLokiストリームを本文の `error|fatal` で検索していた。現行のC2Lは管理対象DockerのAgentHouseとLokiを扱う。C2Mでは、登録済みKubernetes基盤の `trino-opa` Deploymentの `opa` コンテナを収集対象に加える。旧名前空間の履歴と、他のPod・コンテナを含む収集範囲は引き続き照合する。[旧SRE](../../tools/local_experiment/bears_resident.py#L130) · [C2L](database-p6-4c2l-sre-loki-severity-2026-11-04.md) · [OPAの基盤定義](../../tools/local_experiment/logical_foundation.py#L445)

専用Colimaプロファイルでは `shirokuma-dev` と `shirokuma-logical` がともに存在し、現行LOCALの登録・適用済み基盤は `shirokuma-logical` だった。旧名前空間で稼働するDeployment 14件とStatefulSet 1件のメタデータを確認した。いずれも現行基盤の所有・世代ラベルを持たず、旧名前空間は収集対象から除外した。登録済みOPAの実ログは、取得した80行すべてにKubernetesの時刻とJSONの `level` があり、採取時点では全件 `info` だった。照合の証跡には行数と形式だけを記録した。

通常WorkbenchのAI Bears → SRE → Platformで「登録済み Foundation · trino-opa」を選び、現在のP管理者が取り込みと検索を個別に実行する。登録済みKubernetes contextと名前空間に対してDeployment・ReplicaSet・Podの所有関係を確認し、稼働中の現コンテナの直近10分・最大100行・64 KiBを読む。取得前後のPodとコンテナの同一性を確認する。Kubernetesのログ時刻とOPA JSONの明示的な `level` が有効な `error`／`fatal` だけを扱う。[kubectl logsの取得条件](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_logs/#options)

専用の `sre-foundation-severity` ストリームへ時刻・発行元・重大度を保存する。検索は環境ID・名前空間・基盤ID・世代が現在の登録と一致するストリームに絞り、直近10分の最大20件を表示する。取り込み済み0件と取得不可を区別する。Lokiの実効設定で専用ストリームの24時間保持と手動削除の無効化を照合する。C2LのDocker収集とPawprintsは別のストリームを使う。

## M1の必須範囲の照合

| 項目 | 現状と残る判断 |
| --- | --- |
| SQL-09 | 旧サンプルメニューには固定データへのSQLがある。DatabaseのData Explorerでは現在の対象と権限を確認し、未実行の読取りSQLを新規Worksheetへ開ける。旧サンプルの代替にできるかは所有者の受入範囲に残る。 |
| ADM-09 | 条件付きの高度な登録・世代計画・apply依頼の導線がある。登録は `REGISTERED`、apply依頼は `REQUESTED` まで。準備済み世代を使ったoperator適用とREADYの照合、および受入対象の決定が残る。 |
| ADM-10 | 段階作成のbootstrap／full承認、再計画、取下げ、回復照合には既存導線がある。本人・計画・revisionを照合する。通常Workbenchの論理DB作成とは別に、段階作成を受け入れる環境と手順の決定が残る。 |
| ADM-11 | 既存退役はカタログ、ストレージ、資格情報を保持する。物理purgeやcredential／PVC削除を必須に含めるかは、独立した所有者の範囲判断に残る。 |

[89項目の閉鎖監査](database-p6-0r1-capability-closure-audit-2026-11-03.md) · [段階作成の契約](database-p5-0-contract-2026-10-23.md) · [退役の受入](database-p4-8-acceptance-2026-10-23.md) · [DB-P6-7統合受入](database-p6-7-integrated-acceptance-2026-11-04.md)

## 検証

| 層 | 結果 |
| --- | --- |
| 重点試験 | 76件成功。登録範囲・所有関係・コンテナの入れ替わり、重大度と取得上限、認可、送信直後の受付未確認、保持設定、画面の対象切替と遅延応答を確認。 |
| 実効保持設定 | 隔離したARM64 Loki 3.7.0の `/config` で、C2L・C2Mの専用2ルールが各 `1d`、全体保持が `0s`、手動削除APIが403であることを確認。24時間経過後の物理消去は今回の観測範囲外。 |
| 実Lokiへの保存・検索 | 現在のP権限と実Kubernetesの所有関係を使い、ログ読取り境界だけを合成応答に差し替えた。製品の取り込み処理は2件をHTTP 204で保存し、同じ入力の再取り込みは0件だった。別の名前空間・環境・基盤・世代、C2LのDocker記録、時間外を含む7件を別途投入し、C2Mの検索から除外した。 |
| 実HTTPと認可 | 通常Workbenchとresidentの両HTTP面で、実OPAは取得可能・新規0件、隔離Lokiの検索は合成2件で一致した。下位ロール4件と異なるOrigin 2件は403、想定外の入力2件は400。各応答の `no-store` を確認した。 |
| 通常Chrome | 権限の明示更新、合成2件の手動検索、実OPAの手動取り込み0件を確認した。日英表示、Dockerへの切替後の既存検索、閲覧者ロールへの変更時の結果消去と操作無効、再読み込み後の再認可を確認した。 |
| 検証環境の撤去 | 専用プロファイルのdoctorは成功した。一時Lokiだけを撤去して、既存Lokiの停止状態とデータ領域を照合した。両HTTP面はLoki停止時に取得不可を返した。 |
| 全体回帰 | 135グループ、5,227件中5,193件成功・34件スキップ・失敗0件。初回は既定PythonのPyYAML不足でDB Inventory試験が中断した。DataOps仮想環境のコマンドと一致する65グループの成功結果を照合し、残る70グループを同環境で実行した。設計整合チェックも通過した。 |
| 保全 | 通常Workbenchとresidentを同じ起動条件で更新した。既存台帳12,636行、既定台帳156行、保護ファイル14件、元の未確定記録とエンジン証跡を照合し、台帳の追加行は0件だった。 |
