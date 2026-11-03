---
project: Shirokuma
doc_id: DB-P6-4C2F
title: Namespace Pod declaration review for platform Policy
status: accepted
created: 2026-11-03
updated: 2026-11-03
version: "1.0"
area: guides
tags: [database, policy, compute, pods, acceptance]
---

# DB-P6-4C2F: namespace内の実Pod宣言を確認する

[C2E](database-p6-4c2e-recent-failures-2026-11-03.md)の後続として、旧SRE／Policyの操作を再照合した。次の実装は旧 `bears_resident.policy` の実Pod分岐に対応する。登録済みFoundationが選ぶnamespaceの全Podを対象とし、通常・init・ephemeral containerへ既存4項目を適用する。namespace内のPodを一律にFoundation所有とは扱わない。

## 必須操作との対応

| 旧操作・出力 | 現在の対応・今回の判断 |
| --- | --- |
| SREのcontroller readiness／container使用量／失敗Pawprints | C2B・C2D、P5D、C2Eでそれぞれ限定対応。controller報告、実使用量、保存済み失敗metadataを現在healthと同一視しない |
| SREのAgentHouse／Loki readiness・raw logs・Loki query range | 残件。両者には固定Docker runtimeがあり、Kubernetesのdeploy一覧だけでは対応を判定できない。Lokiの正規化task読取りも旧raw logsとは異なる。managerのstatusはローカル状態を書き込むため、新しいpassive sourceへ直接流用しない |
| Policyの固定LOCAL／管理基盤template・4項目 | C2C1／C2C2の限定対応を維持 |
| Policyの実Pod・通常／init／ephemeralの4項目 | 今回、登録済みnamespaceの全Pod宣言を独立した手動snapshotへ接続。Pending・終了済み・削除中を状態で除外しない |
| Policyの全controller template・任意manifest check、他namespaceへの拡大要否 | controller／manifestの残件とnamespaceの範囲判断を継続する。旧Policy baselineは選択namespaceの検査であり、他namespaceへの拡大要否はowner合意で判断する |

根拠は [旧SRE／Policy](../../tools/local_experiment/bears_resident.py)、[AgentHouse](../../tools/local_experiment/agenthouse/manage.py)、[Loki](../../tools/local_experiment/loki/manage.py)、[C2C1](database-p6-4c2c1-workload-baseline-2026-11-02.md)／[C2C2](database-p6-4c2c2-foundation-baseline-2026-11-03.md)である。旧actionを再開放せず、一つの必要な出力を現在の認可へ接続する。

## 実装契約

Policy PlatformとComputeに独立した手動panelを置く。入力なしの `GET /api/v1/bears/platform-pod-baseline` を両HTTP面へ追加し、現在の真P・global current／maximum admin・selected adminを取得前後に確認する。DB閲覧grantで代用しない。登録済みFoundationのnamespaceとbindingも前後に照合する。

対象namespaceのPodListを、必要なidentityと宣言だけに射影して1回読む。raw manifest・環境変数・引数・log・secretは返さず、任意namespaceやmanifest入力を受けない。取得は8秒・sourceと共有HTTP encodingのDTOを各64KiB・64 Pod／合計256 containerまでとし、欠落・continuation・malformed・上限超過は全体をunavailableとする。正常な空一覧は「対象なし」で、allowや全項目適合とは表示しない。

確認する4項目は、readOnlyRootFilesystem、宣言上の実効runAsNonRootがtrueかつ実効UIDが0でないこと、capabilitiesのALL drop、CPU／memoryの正のlimitsである。container側のnonRoot／UIDをPod側より優先し、nonRoot=true・UID未指定は既存ruleどおり許す。これは実Podに保存された宣言の確認であり、image内の実行UID・実際の強制・現在health・admission・aggregate allow・global Policy判断は表さない。

panelごとに明示的な権限更新と取得を行い、自動取得・poll・retryを追加しない。元のDB context、Worksheet、Bear入力、Audit、他panelの取得結果を保持し、日英切替でも記録と展開状態を保持する。

## 検証と受入

2026-11-03 JST、この宣言観測の限定技術受入を完了した。証拠はgit対象外の `var/shirokuma/db-p6-4c2f-live-pod-baseline-2026-11-03/private/` に保存する。重点試験で生じた初回のfixture期待値の失敗も、修正後の成功と区別して保持する。

| 検証層 | 結果と限界 |
| --- | --- |
| 重点試験・差分 | 7群200件成功。最終の名前境界case追加後にbackend 19件を再実行して成功し、他6ファイルの同一性を確認。通常／init／ephemeral、継承・欠落、空・故障、framing・continuation・上限、失効・Foundation差替、両HTTP adapter、独立receipt・遅延応答を検証 |
| 正規全体回帰 | `make verify-local-experiment` 相当の全127群を各1回、最終source 7ファイルと一致する版で実行。5,074件中5,040成功・34 skip・失敗0、949.1秒。Workbench全体1,258件も成功。make dry-runの全127 commandを実行し、追加recipeの省略・再実行は0 |
| 実HTTP・認可 | 両portの真P＋selected adminで200 available、各2,828 bytes。selected bi_viewer／data_engineerは両面とも403、計4件。DB grantのみ・非P・上限不足・途中失効はcontrolled試験で確認し、実HTTPの証拠と区別 |
| 実Pod source | 登録namespaceから7 Pod・通常7＋init 3の10 containerを取得し、両portの対象と4項目が一致。40結果はmetであり、security PASS／allowの判断にはしない。実環境のephemeralは0件で、同種別・Pending／終了済み／削除中を除外しない契約の検証はcode evidenceと区別 |
| 通常ブラウザー | Policy／Computeから各1回だけ手動取得し、異なるAPI時刻を保持。権限更新・移動・日英切替・reloadはsource取得0。native Enterで展開でき、日英切替後も7 Pod・10 container、時刻・展開状態を保持。390pxの日英表示はdocument幅390・panel幅366で操作ボタンが収まる。200% zoomは未実施 |
| 元の操作状態 | 元の19 Worksheetと選択・SQL・DB／schema／role／mode、Bear 28入力、Audit 11入力、言語・viewportを復元後とreload後に厳密比較。検証tabだけを閉じた。Worksheet／TrinoのSQL実行、LLM、WAP操作は行わない |
| doctor・通常process | 読取り専用doctorを1回実行しexit 0。scope／cluster／Flux／生成workloadの観測で、SQLはinformational。通常2processは同じargv・cwd・各29環境変数・安定TMPDIR・loopbackで1回ずつ交代し、PID92794／92795で継続。新collectorの取得とdoctor・通常初期化時の既存SQLは別に扱う |
| 保全・文書 | 各実操作前後で全12,271＋156行・schema・14保護ファイルとmode、既存backend 20ファイル、新source 7ファイルを照合。active 0、既存UNKNOWNのquery 3／admission 3／AI 6／dbt 1と6F残件を保持。機能表89 ID・順序を維持し、AI-05／CMP-05以外の87行は変更しない。ownerの2 YAML差分は保持してcommitから除外 |

`make verify-design-context` は成功（14件、94 documents／10 issues／42 references）。8888は有限access logと、欠落のないbrowser CDP 10区間で手動取得数を照合した。8080はaccess logを出さないため、保存済みの直接HTTP 3件を根拠とする。

## 残るgate

AI-05／CMP-05全体、SRE readiness／raw logs、全89必須行、P6-7／M1本人受入は継続する。[6F清掃残件](database-p6-6f-dataops-recovery-2026-11-03.md)も保持する。必須範囲の変更はowner合意で扱い、実Compute伸縮はM1後とする。
