---
project: Shirokuma
doc_id: DB-P6-4C2C2
title: Managed logical foundation workload declaration review
status: accepted
created: 2026-11-03
updated: 2026-11-03
version: "1.0"
area: guides
tags: [database, bears, platform, policy, baseline, authorization]
---

# DB-P6-4C2C2: 管理対象の論理基盤workload宣言レビュー

[P5G](database-p6-5g-compute-explanations-2026-11-03.md)の後、Bearsの既存actionを必須機能表と現在の通常導線へ照合し、AI-05／CMP-05の残件から管理対象の論理基盤に対する4条件の宣言レビューを選んだ限定実装単位である。2026-11-03 JSTにこの限定範囲の技術受入を完了した。C2C全体、DB-P6-4・5全体、global policy、M1本人受入を閉じない。

## 既存actionと必須操作の対応

| 対象 | 現在の通常導線との対応 | 残件・区別 |
|---|---|---|
| Query Doctor | SQL／保存計画の説明と最新SELECT読込 | 既存の許可入力とcontextを維持する |
| AI-02 Data Engineer | generateはB1 Copilot、stage／auditはB2＋Manual WAP、publishはTRF-04、refreshはManaged Marts、rollbackはTRF-05 | publicationに紐づくrollbackと旧任意Snapshot rollbackは同じ範囲ではない。旧Bearフォームの再開放で重複実装しない |
| AI-03 Catalog Curator | Explorerの検索・対象情報、C1のmetadata説明、EXP-13／14の保存lineage | 各既存認可を維持し、C1はV／E／A、保存lineageはE／Aの限定範囲。raw catalog設定の取得、旧横断検索、任意SQLからの推定、説明の保存まで完了とはしない |
| AI-04 SRE inspect | C2A／C2B／C2C1とComputeの使用量・容量観測 | storage／catalog readiness、recent error／log／telemetry等の対応関係は残る。現在health全体を保証しない |
| AI-05 Policy baseline | C2C1の固定LOCAL 2 Deploymentに対する4条件 | 今回は管理対象7テンプレートへ宣言レビューを追加する。旧namespace-wide baseline、実Pod／ephemeral、全体policyの閉鎖ではない。任意manifest checkは引き続き閉じる |

旧actionは`bears_resident.policy`等、対応付け要件は[全体計画](database-full-capability-and-compute-plan-2026-10-28.md)のDB-P6-4に基づく。既存受入と現在の導線への対応付けであり、この単位でB1／B2・WAP・Explorerの全機能を新たに再受入したものではない。native-baselineの8 admission policyは旧4条件レビューと異なる。元定義のnamespaceだけで判断せず、logical foundationが論理namespace用の8 policy／bindingを生成することも区別する。その設定観測だけを今回の必須実装にはしない。

## 対象と読取り境界

現在のcommitted／APPLIED／READYなlogical foundationから、validated prefix・namespace・foundation ID・revisionを取得する。`logical_foundation.render()`は秘密artifactを読むため使用しない。対象はそのrendererが管理する次の7 Pod templateに固定する。

| kind | 内部target | 公開component |
|---|---|---|
| Deployment | prefix-seaweed | storage |
| Deployment | prefix-postgres | catalog_database |
| Deployment | prefix-polaris | catalog |
| Deployment | trino-local | coordinator |
| Deployment | trino-local-worker | worker |
| Deployment | trino-opa | authorization |
| Job | prefix-bootstrap | catalog_bootstrap |

入力なしの独立した`GET /api/v1/bears/platform-foundation-baseline`を両HTTP面で提供する。query／body／重複role headerは拒否する。現在のaccountとprincipal双方の真P・最大admin・選択adminをsource前後に検査し、DB／schema／grantを前提としない。

sourceは固定fieldだけのmixed-kind kubectl GET 1コマンドとし、Kubernetes API全体の単一transactionとは扱わない。kind／apiVersion・name／namespace・objectとtemplateのfoundation／revision labelを照合し、UIDの存在・妥当性と非削除を確認する。登録済みworkload UIDとの連続性の証明ではない。取得後に保存foundationの同一性を再確認する。namespace列挙、実Pod／log／metrics、SQL／LLM、status／lifecycle、manifest applyや設定修正は行わない。Jobの完了状態やreplica数を実行条件にせず、現在保存されたtemplateを読む。完了済みJobも宣言対象である。

## 4条件と閉じた結果

判定は[C2C1](database-p6-4c2c1-workload-baseline-2026-11-02.md)のpure validatorと同じ意図を使う。readOnlyRootFilesystemの厳密true、field単位でPodから継承したnonRoot／UID、dropのALL membership、両limitsの有効かつ非zero coefficientだけを判定する。UID未指定・nonRoot=trueの既存解釈、false／0上書き、Quantity構文を維持する。

全ordinary／initコンテナをkind別ordinalで扱い、総数64、source 32 KiB、公開DTO 128 KiBを上限とする。新baseline versionは`foundation-container-declarations-v1`。公開するのはplatform／LOCAL／選択admin・観測時刻・version・閉じたcoverage、7件のcomponent／kind、コンテナkind／ordinal、4 checkのmet／not_metだけである。名前・namespace・UID・raw宣言・エラー本文は公開しない。

coverageは管理対象のPod template宣言・per-containerの範囲を明示し、runtime security／admission decision／global policyはfalseとする。宣言欠落や条件不一致はnot_met、不正source・7件の欠落／重複／所有不一致・上限超過・timeout・foundation変更は全体unavailableとし、部分的なmetを返さない。認可失効は403とし、aggregate allow／PASSや操作許可を返さない。

## 通常導線と検証

SRE／PolicyのPlatform scopeとComputeに、既存の表示様式に沿った独立した手動取得と共有snapshotを置く。新controller／cacheをC2C1、C2A／B、Computeの各観測、runtime操作から分離する。navigation／scope／locale／認可更新でsourceを自動取得せず、進行中の古い応答と権限失効を処理し、非表示の結果も消去する。既存の下書き・DB report・Audit等を保持する。

重点試験、正規全体回帰、現在認可と両HTTP面、通常ブラウザー、実sourceとの独立照合、active／default control-storeの全行／schema・保護設定の保全を別々に記録する。実sourceのnot_metをmetにするための設定変更は行わない。live clusterのdoctor結果も新engine SQLの証拠とは分ける。

### 限定技術受入

| 層 | 結果と適用範囲 |
|---|---|
| 実装・独立レビュー | 指定7ファイル内の実装。C2C1のsource・4 pure validatorと既存cacheの契約を維持。現在Pの前後検査、保存foundationの再確認、7件のkind／所有label、非削除UID、全体unavailable、strict GET、3面の手動cacheを確認。追加JAキー23件は既存と衝突しない |
| 正規全体回帰 | venvでMakefileの正規121群が完了。4,853件中4,819成功・34 skip・失敗0、970.5秒。Workbench全体1,158件も成功。重点試験と重複する件数は合算しない |
| 集中試験 | 新backend 23件・旧C2C1 backend 24件が成功。別の有効prefix／namespace／foundation ID／revision、入力順序、ordinary／initの可変件数、64上限、現在認可・欠落・不正・timeout等を確認。ホスト上の新UI 22件・旧baseline UI 20件・基本Database UI 67件も成功。遅着・ABA・非表示DOM消去・他の下書き／cacheの分離を検証 |
| 実source・API | 製品parserをimportしない固定field Go-template 1コマンドと独立した4条件の判定に対し、両HTTP面の7 workload・10コンテナ（ordinary 7／init 3）・40 checkが一致。実宣言は40 metであり、安全性全体のPASSではない。API受入helperは34 GET（200×2／403×12／401×6／400×14）、pureな現在P認可adapterは9ケース成功。各GET直後のcontrol-store全行／schema・12保護ファイルとmodeは差分0 |
| 通常3面 | Computeから明示取得1回。SRE／PolicyのPlatform scopeとComputeの全出力・二つの観測時刻が一致し、切替で再取得しない。旧LOCALの明示取得1回を別に行い、新取得後も旧snapshotは一致。SRE／PolicyのDatabase出力と全Bear入力を復元・照合した。現在role低下は元19 Worksheetを変更するため実ブラウザーでは行わず、認可APIと分離したUI試験で検証 |
| 表示・通信 | 日英390pxでpanel 364px／scroll 364px・output 336px／336px、Chrome実200%（960px・DPR 2）でpanel 742px／742px・output 714px／714px。明示取得後のCDP観測43 GET中、新sourceは0、書込み0、欠落記録なし。新route全体は35 GET（API helper 34＋browser 1）であり、全ブラウザー通信の総数ではない |
| 復元・保全 | 日本語・100%（1920px・DPR 1）へ戻し、viewport override解除。19 Worksheet＋詳細tab・選択SQL・DB／schema／role／mode／LOCAL／最大1000行と全Bear入力が変更前に一致し、検証tabは閉じた。ブラウザー後のactive control-store 12,234行／79表・default control-store 156行／78表、全schema・12保護ファイル・所有者2 YAML・engine proofのbyte／modeが一致、追加／削除／変更行0、UNKNOWNを保持、実行中0 |
| runtime・doctor | 正規serverの2プロセスだけを、所有・profile・stable TMPDIR・実行中0・保全でガードして限定再起動。doctorはexit 0／healthy、45 healthy＋SQL informational 1。これは別のreadiness観測であり、SQL実行や4条件以外のpolicy受入ではない。通常startupのhealth SQLは別枠・未計測とし、新source／受入helperからのSQL・LLM・lifecycle・applyは0 |
| 設計文書 | `make verify-design-context`とdiff checkが成功。89能力ID・順序を保持し、AI-02／03の対応表とAI-04／05／CMP-05の限定追加以外の84行は変更前と一致 |
| 全試験後の最終保全 | active／default control-storeの全行・schemaが原本・ブラウザー後と完全一致、追加／削除／変更行0。12保護ファイル・所有者2 YAMLのbyte／mode、engine proofのbyte／mode／generation／revision／epochも一致。既存UNKNOWNを保持し、実行中0。限定再起動後の正規2プロセスとstable TMPDIR、凍結3 sourceと旧6 guard moduleも不変。最終照合自体のHTTP・Kubernetes・doctor・SQL・lifecycle・状態復元は0 |

非公開記録は`var/shirokuma/db-p6-4c2c2-foundation-baseline-2026-11-03/private/`に保存する。engine実データの再照会は行っていない。APIの200・実ブラウザーの一致・doctorのhealthyだけで、既存隔離・故障回復・M1本人受入を通過したとは扱わず、正規全群の終了結果と最終保全を別に確認する。

## 後続

AI-04のreadiness／error・log／telemetryと、Policyの残る全体対象・実行時範囲を既存actionに照合し、必須残件を次の限定単位へ分ける。native8の設定観測や旧フォームの再開放だけを必須開発へ昇格させない。89行の全体閉鎖、DB-P6-7の統合受入とM1本人受入を継続し、P7の実Compute伸縮はM1後とする。
