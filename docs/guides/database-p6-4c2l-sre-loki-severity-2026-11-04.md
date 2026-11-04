---
project: Shirokuma
doc_id: DB-P6-4C2L
title: Search sampled platform error and fatal events in Loki
status: accepted
created: 2026-11-04
updated: 2026-11-04
version: "1.0"
area: guides
tags: [database, sre, loki, telemetry, acceptance]
---

# DB-P6-4C2L Lokiのerror／fatal検索

旧SREはKubernetesの `shirokuma-dev` namespaceラベルを持つストリームを本文の文字列で検索した。現行Lokiの既存投入はtask IDで照会するPawprintsの5項目で、当該namespaceのログ投入は確認できなかった。C2Lの対象は、C2Hで所有を確認できる現行LOCALのAgentHouseとLoki。名前空間別ログ全体を扱うには、対象と投入元の確定が必要になる。[旧SRE](../../tools/local_experiment/bears_resident.py#L130) · [現行Loki](../../tools/local_experiment/loki/manage.py#L98) · [C2K](database-p6-4c2k-sre-raw-logs-2026-11-04.md)

通常WorkbenchのAI Bears → SRE → Platformで、現在のP管理者が採取・取り込みと検索をそれぞれ明示実行する。所有を確認した各コンテナの直近10分・最大100行・64 KiBを読み、発行元の形式で重大度を判定する。error／fatalの時刻、発行元、重大度だけを専用ストリームへ取り込む。同じ時刻・発行元・重大度を照合し、未登録の記録だけを送る。HTTP 204で受付を確定し、応答喪失時は未確認と表示する。SQLや接続情報を含み得る本文はC2Kの一時閲覧で扱う。

書き込み前に稼働中Lokiの設定を照合する。専用ストリームの保持ポリシーは24時間で、Compactorが周期的に期限切れを処理する。手動削除APIは無効に設定する。Workbenchからは直近10分の新しい20件まで参照し、検索時刻・0件・取得不可を区別する。0件は検索範囲内の取り込み済み記録についての結果。[Loki範囲検索API](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-logs-within-a-range-of-time) · [Lokiのストリーム別保持](https://grafana.com/docs/loki/latest/operations/storage/retention/#configuring-the-retention-period)

## 検証

| 層 | 結果 |
| --- | --- |
| 重点試験 | 112件成功。取り込み上限、発行元の重大度判定、認可の前後照合、204の受付、重複の除外、画面の遅延応答と既存パネルを確認。 |
| 固定版Lokiと保持 | 隔離したARM64 Loki 3.7.0で、実効設定の専用ストリーム1日、全体0s、Compactor有効、手動削除APIの403を確認。設定差分APIの500を受け、実装は通常の `/config` を上限付きで読み、実際の期間表記 `1d` を認識する。 |
| 実Lokiの保存と検索 | Docker採取の境界に合成行を渡し、製品の分類・取り込み処理が3件を実Lokiへ保存した。実機で追加された `detected_level` は保存した重大度と一致する場合だけ受け入れる。修正後に元の3件を取得し、同じ入力の再採取は追加0件。時間外の記録とPawprintsは検索結果から除外された。 |
| 実HTTPと認可 | 両HTTP面で検索結果の3件が一致した。nativeの実コンテナ採取は両方取得可能・新規0件。下位ロール4件と異なるOrigin 2件は403、想定外の入力2件は400。結果と拒否の `no-store` を確認した。 |
| 通常Chrome | SRE Platformで権限更新、手動検索3件と実コンテナの手動取り込み0件を確認。画面離脱時の消去、日英表示、閲覧者ロールでの操作無効、再読み込み後に明示権限更新が必要なことも確認した。 |
| 検証環境の撤去 | 既存Lokiの停止状態・データ領域を照合し、一時Lokiだけを撤去した。両HTTP面はLoki停止時に取得不可を返す。24時間の設定と検索時刻の境界は照合済み。24時間経過後の物理消去は今回の観測範囲外。 |
| 全体回帰 | 135レシピ、5,219件中5,185件成功・34件スキップ・失敗0件。設計整合チェックと専用プロファイルのdoctorも通過した。 |
| 保全 | 同じ起動条件で通常サーバーを更新し、既存台帳12,636行と既定台帳156行、保護ファイル14件、元の未確定記録、エンジン検証を照合した。検証ではログ本文を証跡に保存しなかった。 |
