---
project: Shirokuma
doc_id: DB-P6-4C2D
title: Passive storage and catalog Deployment controller observations
status: accepted
created: 2026-11-03
updated: 2026-11-03
version: "1.0"
area: guides
tags: [database, bears, sre, platform, controllers, authorization]
---

# DB-P6-4C2D: storage／catalogのDeployment controller観測

[C2C2](database-p6-4c2c2-foundation-baseline-2026-11-03.md)の後、AI-04／CMP-05の旧SRE inspectと通常導線を照合し、Trino以外の管理対象3 Deploymentの受動的なcontroller記録を次の限定実装に選んだ。2026-11-03 JSTにこの限定範囲の技術受入を完了した。旧SRE全体、global Policy、P6全体、M1本人受入を閉じない。

## 対象と意味

旧SREの`inspect`はTrino／Polaris／PostgreSQL／Seaweedのready数を読むが、C2Bの通常導線はTrino 2 Deploymentだけである。C2C2は宣言条件のレビューであり、controllerの報告値とは分ける。旧実装のreadyとdesiredからhealthyを作る式は引き継がない。

validatedなcommitted／APPLIED／READY foundationからprefix・namespace・foundation ID・revisionを取得し、`${prefix}-seaweed`、`${prefix}-postgres`、`${prefix}-polaris`の3 Deploymentだけを対象とする。公開componentは規定順にstorage／catalog_database／catalog。Job・OPA・実Pod・他namespaceは含めない。

入力なしの独立した`GET /api/v1/bears/platform-foundation-controllers`を両HTTP面に追加する。現在のaccountとprincipal双方の真P・最大admin・選択adminをsource前後に確認し、DB／schema／grantを前提としない。query・body・重複role headerを拒否する。

## 数値の閉じた契約

| field | 公開・検証 |
|---|---|
| component | storage／catalog_database／catalogの3件、規定順・各1回 |
| generation | 必須の正整数 |
| observed_generation | controllerの記録。省略／nullはnull、明示0は0。現世代より小さい値も有効、現世代より大きい値は不正source |
| desired_replicas | 必須の非負整数。0を許し、省略を1で補わない |
| replicas／updated_replicas／ready_replicas／available_replicas／unavailable_replicas | 報告された非負整数をそのまま返す。省略／nullはnull、明示0と区別。省略にはAPIが0を省略した場合もあるため、実値0を推定しない |

全数値はJS safe integer以下とし、bool・負数・非整数・不正な数値表記を拒否する。rollout中にdesiredを上回る報告値を許し、count間の大小関係でsourceを拒否しない。unavailableをdesired−ready等で算出しない。世代一致、ready==desired、取得時刻からhealthy・収束・controllerの生存を判定しない。

フィールドの意味は[Kubernetes apps/v1の一次定義](https://raw.githubusercontent.com/kubernetes/api/v0.33.4/apps/v1/types.go)を参照し、rolloutの報告値は[公式Deployment説明](https://v1-33.docs.kubernetes.io/docs/concepts/workloads/controllers/deployment/#rolling-update-deployment)と区別する。nullableは本APIが取得projectionの省略を保持するための契約であり、Kubernetesの型そのものを変更するものではない。

top-levelはscope=platform／warehouse=LOCAL／selected_role=admin、observed_at、controller_version=`foundation-storage-catalog-controllers-v1`、coverage、controllersだけとする。coverageはkind=`deployment_controller_counts`、managed_foundation=true、service_connectivity=false、sql_health=false、policy_decision=false。controllersはavailability／code／observed_at／deploymentsを持ち、各rowは上記9 fieldだけである。

top-levelのobserved_atはAPI記録の作成時刻、controllers.observed_atはsource結果の記録時刻である。HTTP応答完了・controller heartbeat・最終更新・readiness検査時刻ではない。UIで世代の未報告・遅れ・一致を説明しても、その取得記録の関係だけを示す。数値の未報告は「未報告（0省略の場合あり）」、明示0は「0」と表示する。

## sourceと失敗境界

固定fieldだけのkubectl GET 1コマンドで3件を取得する。Kubernetes API全体の単一transactionではない。kind／apiVersion、name／namespace、objectとtemplateのfoundation／revision label、UID妥当性・非削除を内部確認し、保存foundationの同一性を再確認する。登録済みworkload UIDの連続性の証明ではない。`logical_foundation.render()`やprivate artifactを読まない。

source上限16 KiB、公開DTO上限64 KiB、deadline 8秒・request timeout 3秒とし、retryを加えずkill／reapする。欠落・重複・所有不一致・不正field・過大・timeout・foundation変更は全体unavailable／code=`FOUNDATION_CONTROLLERS_UNAVAILABLE`／deployments=null。単なるstatus省略や世代遅れは有効な記録として扱う。認可失効は403を優先し、raw errorや部分的な安心材料を返さない。

Pod／conditions／reason／message／paused／probe設定／URL／資格情報／raw JSONは取得・公開しない。Provider.probe、runtime.status、SQL／exec／port-forward、S3／catalog疎通、新しいhealth probe、lifecycle／修復／applyを呼ばない。正常らしく見せるための設定変更を行わない。

## 通常導線と受入

SREのPlatform scopeとComputeの2面に、既存の様式に沿った独立の手動取得・共有cacheを追加する。Policy面へ観測を混ぜず、C2B／C2C1／C2C2、DB report、runtime操作、他の下書きと分離する。navigation／scope／locale／認可更新でsourceを自動取得せず、遅着・ABA・権限失効を処理し、非表示の結果も消す。

重点試験、正規全体回帰、両HTTP面の現在P認可、独立した実source照合、通常ブラウザーの日英・390px・実200%を別々に記録する。real sourceの未報告・旧世代・0・surgeをそのまま保持する。active／default control-storeの全行／schema・保護12ファイル・所有者2 YAMLのbyte／modeを照合し、engine実データの再照会とは区別する。通常serverの限定再起動とstartup health SQLの未計測、doctorのreadiness証拠も新sourceのSQL実行とは分ける。

### 限定技術受入

| 層 | 結果と適用範囲 |
|---|---|
| 実装・独立レビュー | 指定7ファイル内の実装。閉じた3件・9 field、両label階層・UID妥当性・非削除、現在P／foundationの前後検査、nullable・safe integer、全体unavailable、strict GET、2面の独立手動cacheを確認。Policyに新mountを置かず、既存JA値・旧観測・操作経路を維持 |
| 正規全体回帰 | venvでMakefileの正規123群が完了。4,912件中4,878成功・34 skip・失敗0、971.1秒。Workbench全体1,178件も成功。重点群との重複件数は合算しない |
| 集中試験 | 新backend 19件・旧C2C2 backend 23件が成功。別の有効foundation／異なるlabel revisionとDeployment世代、入力順序、全status fieldの省略／null／0、desired 0、旧・未来世代、surge、不正source・認可失効・timeoutを検証。ホスト上で新UI 20件・旧C2C2 UI 22件・基本Database UI 67件も成功し、実initializationからの2 mount・他cache／下書き・遅着／ABA・hidden purgeを確認 |
| 独立source・実API | 製品parserをimportしないGo-template 1コマンド（482 bytes）と独立したpresence／数値oracleに対し、両HTTP面の3 rowが完全一致。実記録は3件ともgeneration／observed generationが1／1、desired・replicas・updated・ready・availableも1、unavailableは3件とも省略からnullを保持。実0・旧世代・surgeは今回の実sourceでは発生しておらず、制御試験の証拠と分ける |
| 認可・入力 | API helper 34 GET（200×2／403×12／401×6／400×14）とpure現在P認可adapter 9ケースが成功。各GET後のcontrol-store全行／schema、12保護ファイルのbyte／modeは差分0。account／grantは変更しない。実ブラウザーはadminを維持し、role低下／失効の分岐はHTTPと制御UI試験で確認 |
| 通常2面・分離 | Computeで手動取得1回、SRE Platformの全出力と二つの時刻が一致。Policy面の新panelは0。旧C2C2を別の手動1 GETで取得し、新取得後もsnapshotは一致。SRE／PolicyのDatabase出力と全Bear入力を復元・照合した。取得後のCDP観測52 GET中、新source 0・書込み0、記録欠落なし。新route全体は35 GET（API 34＋browser 1）であり、全ブラウザー通信の総数ではない |
| 幅・復元 | 日英390pxでCompute panel／outputは364／336px、SREは362／334px。Chrome実200%（960px・DPR 2）でComputeは742／714px、SREは740／712px。それぞれclient幅とscroll幅が一致。記録用の一時1920×1200とviewport overrideを解除し、日本語・100%（1920px・DPR 1）へ戻した。19 Worksheet＋詳細tab、SQL・DB／schema／role／mode／LOCAL／最大1000行、全Bear入力が変更前と一致し、検証tabは閉じた |
| runtime・doctor | 正規2 serverだけを所有・profile・stable TMPDIR・実行中0・台帳／設定でガードして限定再起動。doctorはexit 0／healthy、45 healthy＋SQL informational 1という別のreadiness証拠。新sourceやengine SQLの検証と同一視しない。通常startupの既存health SQLは別枠・未計測とし、本単位の明示SQL／LLM／Provider／lifecycle／applyは0 |
| ブラウザー後の保全 | 原本・API後とcontrol-store全行／schemaが一致。active 12,234行／79表、default 156行／78表、追加／削除／変更行0。12保護ファイル・所有者2 YAML・engine proofのbyte／modeが一致し、UNKNOWNを保持、実行中0。再起動後の正規2プロセスと凍結3 source・旧7 guardも不変 |
| 設計文書 | `make verify-design-context`とdiff checkが成功。89能力ID・順序を保持し、AI-04／CMP-05以外の87行は変更前と一致 |
| 全試験後の最終保全 | active／default control-store全行／schemaが原本・ブラウザー後と完全一致し、追加／削除／変更行0。12保護ファイル・所有者2 YAMLのbyte／mode、engine proofのbyte／mode／generation／revision／epochも一致。既存UNKNOWNを保持し、実行中0。正規2プロセス・stable TMPDIR・凍結3 source・旧7 guardも不変。最終照合自体のHTTP・Kubernetes・doctor・SQL・runtime操作・状態復元は0 |

非公開記録は`var/shirokuma/db-p6-4c2d-foundation-controllers-2026-11-03/private/`へ保存する。重点群・正規回帰・実source／HTTP・ブラウザー・保全は別の層として扱い、重複する件数を合算しない。engine実データの再照会、サービス疎通、旧SRE全体、global Policy、M1本人受入をこの限定単位の証拠で代行しない。

## 後続

既存Bear actionと89行の必須機能について、実装・受入証拠・通常導線の対応を監査し、必須残件を確定する。既存のPawprints／DB保存監査等を新しいtelemetry機能として重複実装せず、旧実装のraw logやProbeをそのまま次の必須開発へ昇格させない。P6全体閉鎖、DB-P6-7統合受入とM1本人受入を継続し、実Compute伸縮はM1後とする。
