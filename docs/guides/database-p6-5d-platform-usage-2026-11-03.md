---
project: Shirokuma
doc_id: DB-P6-5D
title: Observed LOCAL container CPU and memory usage
status: accepted
created: 2026-11-03
updated: 2026-11-03
version: "1.0"
area: guides
tags: [database, compute, metrics, platform, authorization]
---

# DB-P6-5D: 固定LOCALのCPU・メモリ使用量

2026-11-03 JSTに限定技術受入を完了した。探索操作の後続として、Computeの実使用量を[P5Bの設定requests／limits](database-p6-5b-platform-resources-2026-11-02.md)と区別して観測する。DB-P6-5全体、CMP-05全体、M1本人受入は継続する。実サイズ変更・Worker増減はM1後のDB-P7で扱う。

## APIと現在認可

独立した`GET /api/v1/bears/platform-usage`を明示操作で呼び出す。DB／schema／grantを利用条件にせず、現在のaccountとprincipal双方の真のP権限、最大role admin、選択role adminを全source読取り前後に確認する。DB grantの有無・NULLに依存せず、現在の真Pと最大・選択adminだけで認可し、非PのNULL grantから権限を推定しない。認可失敗時は観測値を返さない。

sourceは既存foundation metadataとKubernetesの読取りだけとする。SQL・engine probe・runtime status修復・Wake／Chill・admission・control store書込みを起動しない。取得できない値や欠落をzeroと扱わず、古い観測を現在の値として返さない。P5A／P5B／C2C1のAPI・DTO・cache・旧route拒否を維持する。

閉じたDTOは`scope=platform`、`warehouse=LOCAL`、`selected_role=admin`、`observed_at`、`coverage`、`availability`、`code`、`samples`からなる。coverageは`kind=fixed_local_container_usage`、`cpu=window_average`、`memory=working_set_sample`、`capacity=false`、`database_dedicated=false`を固定する。availableはcode=nullと全sample、unavailableはcode=`USAGE_UNAVAILABLE`とsamples=nullを返す。各sampleは`role`、`container_ordinal`、`sampled_at`、`window_seconds`、`cpu_cores`、`cpu_millicores`、`memory_bytes`だけを持つ。値は正規化した非負decimal文字列であり、zeroを保ち、CPU coreとmillicoreを正確に対応させる。

## 観測の意味と範囲

対象は固定LOCALのcoordinatorとworker Deploymentに属する現在のPodとordinary containerだけとする。namespace・owner・container対応と観測時刻を確認し、旧Pod、曖昧な対応、部分取得、malformed、期限切れを成功した全体観測へ読み替えない。公開DTOにはrole／ordinalを用い、Pod・container・node名、image、env、command、SQL、DB名、raw manifestやstderrを含めない。

両Deployment各1 ready Podとordinary container全件を対象とし、完全に対応できない場合は全体をunavailableとする。metadataをmetrics取得前後で再照合し、Pod UID・owner連鎖・container incarnation・foundationの変化を拒否する。sampleのageは120秒以内、future toleranceは5秒、windowは0秒超120秒以内とする。現在のcontainer起動より前のCPU観測期間を採用しない。

UTC Zのtimestampは最大9桁のfractionを正確に比較する。Go形式のdurationをnanosecond精度の秒数へ正規化する。quantityは入力64文字以内・exponent絶対値30以内、zeroまたは`1e-30`以上`1e24`以下に限定し、CPU millicoreは正確な1,000倍で導く。UIは96文字以内のcanonical decimal文字列をBigIntで検査する。

固定2 Deployment、namespaceのReplicaSet／Pod、同namespaceのPodMetricsを取得し、metadata 3読取りを再確認する。全体deadlineは15秒、各kubectl requestは2秒、各stdoutは1 MiB、公開DTOは32 KiB以内とし、stderrは公開しない。失敗時のretryや別のSQL経路へのfallbackは設けない。

CPUは観測window内の平均core使用量、memoryは観測時点のworking set bytesである。設定requests／limits、利用可能容量、空きメモリ、割当資源、性能保証、同時実行許可を表さない。観測結果とsample timestamp／windowを同時に表示する。zeroの実測と未取得を区別し、サイズ選択や伸縮操作は追加しない。

一次参照は[Kubernetes v1.33のResource metrics pipeline](https://v1-33.docs.kubernetes.io/docs/tasks/debug/debug-cluster/resource-metrics-pipeline/)と[同版のPodMetrics型定義](https://github.com/kubernetes/metrics/blob/v0.33.4/pkg/apis/metrics/v1beta1/types.go)である。CPUのwindowとmemory working setの意味を本UIに適用する。

## 通常画面

Computeに独立した手動更新panelを設け、初回は未取得とする。画面を開く・移動する・DBを切り替える・localeを変える・認可をrefreshするだけでは観測GETを送らない。取得中の操作と完了したsnapshotを区別し、完了時刻とsourceの観測時刻を表示する。Pの現在権限を失った場合はcacheを破棄し、古い応答や認可のABAで復活させない。

日本語／英語、390px、実200%を検証する。設定資源panelとの意味の違いを通常画面の文言で説明する。

## 検証と受入証拠

実装後に、quantity変換・source対応・鮮度・認可前後・UIの独立性、両HTTP面の実metrics、通常ブラウザー、保存状態の保全を分けて記録する。private証拠は`var/shirokuma/db-p6-5d-platform-usage-2026-11-03/private/`に保存する。

準備時点で、既存metrics-serverと`metrics.k8s.io/v1beta1`は利用可能であり、固定LOCALのcoordinator 1／worker 1の実CPU／メモリをtimestamp／window付きで取得できた。新しいmetrics基盤のinstall、scale、SQL、probeは行っていない。これは準備証拠であり、実装後の受入証拠とは区別する。

| 検証層 | 2026-11-03 JSTの証拠 |
| --- | --- |
| 実装・独立レビュー | ADR-0036のCLI workerで実装。コンテナ再起動をまたぐ旧metricsを拒否する修正、duration・timestamp・decimal精度と日英案内の整合を確認した。 |
| focused | 修正後backend 14件・UI 9件成功。現在P認可、NULL grant、source所有関係、全件性、zero／欠落、鮮度、同一Podの再起動・window境界・source drift、手動取得と遅着応答を検証。近接127件＋Compute／runtime 75件の初回回帰も成功。 |
| canonical | 114群4,663件を完走（34 skip）。既存の「旧未対応panelの全操作をdisabledとする」テストがP5D panelを含めたため1件失敗し、初回記録を保存した。既存P5B等と同じ独立panelの除外にP5Dを追加する1行だけを修正。製品コードは不変で、基本DB UI 67件＋P5D UI 9件は再成功し、最終Workbench全1,092件も成功した（125.9秒、skip 0）。他113群は成功済みであり、初回114群を全成功と書き換えない。 |
| 実HTTP | 通常Workbench 8888とnative 8080で34 GETの期待statusを確認（200×2、400×14、401×6、403×12）。両面の実metricsはavailable、閉DTO・sample時刻・window・正確なCPU換算を確認。Pの選択V／E、非Pのadmin／P偽装、未知principal、不正入力を拒否。認可adapterもcanonical P NULL成功＋8拒否の9件成功。 |
| 実metrics | 8888のsample 22:01:45Zはcoordinator 18.552691 mCPU／1,868,578,816 bytes、worker 15.084351 mCPU／840,577,024 bytes。8080のsample 22:02:45Zも独立取得した。設定requests 100m／256Mi、limits 4CPU／4Giとは別の値として確認した。数値は当時のsampleであり現在値へ固定しない。 |
| 通常ブラウザー | 未取得→明示取得→同一snapshotの日英切替→明示更新を確認（手動GETは2回）。390pxは日英ともpanel 364／scroll 364、実200%は日英ともDPR 2・viewport 960・panel 742／scroll 742。検証対象panelの折返しを確認し、アプリ全体の既存overflowを本単位の改善と主張しない。 |
| 復元 | 日本語・実100%（DPR 1、1920px）、元19 Worksheet（Detailsを含む20tab）、選択Worksheet、SQL、A／Marts／admin／read-onlyを維持し、検証用タブを閉じた。 |
| 保全 | API・ブラウザー後もactive 12,234／default 156行の全行・schema、12保護ファイルとmodeの差分0。UNKNOWN query 3／admission 3／AI 6／dbt 1、実行中0を維持。最終比較は`final-comparison-summary.json`に保存した。 |

通常サーバーは同一argv・cwd・profile env・stable TMPDIRのまま8888／8080へ再起動した。機能の観測経路と受入harnessによる追加SQL／SDK／engine health probeは0である。通常startupの既存read-only health SQL回数は別枠の未計測として扱い、総engine SQL 0とは主張しない。

公開の受入記録は本書、詳細はprivateの`http-acceptance-summary.json`、`authority-adapter-summary.json`、`after-api-summary.json`、`final-comparison-summary.json`、`browser-*.json`／`browser-*.txt`、`compute-usage-ja.png`とする。API 34 GETとブラウザーの手動2 GETは合計36件で、8888 access logの19件はAPI 17＋ブラウザー2と整合した。8080はaccess logを出力しないため、個別HTTP receiptで17件を証明する。取得不能・期限切れ・同一Pod再起動・権限の途中失効は隔離テストで確認し、実環境で障害や口座変更を起こしていない。

## 後続

容量／空き容量、起動・停止履歴と停止理由、権限の説明を既存対応と照合し、残る単位を別途進める。Bears／Policy残件、89行の全体照合、DB-P6-7の統合受入・M1本人受入を継続する。
