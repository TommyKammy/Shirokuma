---
project: Shirokuma
doc_id: "WP-L4-SPARK-001"
title: "WP-L4-SPARK-001 Spark on Kubernetes"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.5"
area: "workpackage"
tags: [shirokuma, workpackage, l4, compute]
---

# WP-L4-SPARK-001 Spark on Kubernetes

## Summary

Spark on Kubernetesのnative driver/executorを固定の小規模集計で検証する。
ADR-0034のlocal実験では既存k3sを保持し、毎回新namespaceに限定する。

## Context

このWork PackageはLevel L4「OSS Databricks再現」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

## Scope

- 設計・実装・設定の最小単位を定義する。
- 関連するREADME/Runbook/ADRを更新する。
- CIで検証できる形にする。

## Non-scope

- Level外の大規模OSS追加。
- 本番データ利用。
- GxP対応。
- 人間承認が必要な危険操作の自動化。

## Deliverables

- 実装または設定ファイル
- ドキュメント更新
- テストまたは検証ログ
- 必要に応じてADR/Runbook

## Acceptance Criteria

- [ ] 変更がGitHub PRとして提出される。
- [ ] CIが通る。
- [ ] Policy checkが通る。
- [ ] `shirokuma doctor`または関連コマンドで状態確認できる。
- [ ] README/Obsidian noteが更新される。
- [ ] 未解決High riskがない。

## Suggested Labels

`level:l4`, `area:compute`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L4-SPARK-001: Spark on Kubernetes.
Read AGENTS.md, related ADRs, and this Work Package.
Use ADR-0034 local commits without GitHub issues or PRs.
Include bounded runtime evidence, local tests and owned-resource cleanup.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-17 native local experiment plan

歴史的PR/CIの項目をlocal実行gateにしない。WP-L0-PLAT-001の既存専用k3s上で、
Operator/CRDやcluster更新を追加せず、Pod内client-mode driverとexecutor 1台を動かす。
Spark 4.1.3の公式要件はKubernetes 1.32以上、現環境は1.33.4である。
4.2.0の要件1.34以上に合わせるために保持clusterを更新することはしない。

- [x] 公式Spark 4.1.3 Java17/Python3 imageのlinux/arm64 manifestをdigest固定し、実CRIから確認する。
- [x] 新namespace/SA/Role/ConfigMap/driver Service/Podに限定し、既存NS・Secrets・PVC・tableを使わない。
- [x] restricted PSA v1.33、non-root、read-only root、限定NetworkPolicyを適用する。
- [x] driver/executor各1CPU/2Gi、全体2CPU/4Gi/2Podsの上限内で固定集計を実行する。
- [x] 32行の固定入力から得る4groupの結果を独立Python oracleと比較し、実executorのtask証拠を確認する。
- [x] success exit0と、同じ集計後に意図して失敗するexit17を区別し、両方で自分の生成資源を回収する。
- [x] 全所有UID、image identity、source hash、logs、検証・cleanup結果をprivate証跡へ残す。
- [x] 保持サービス・fixture・評価証跡・MLflowの状態を維持する。

新namespace名は`shirokuma-spark-<32hex-token>`、driver/executorの永続volumeは作らない。
API接続先は現service/endpointのprivate IPv4とportを読んで限定し、全宛先egress許可へ広げない。
既存のnative admission baselineは`shirokuma-dev`限定であり、新namespaceへ適用済みとは扱わない。
このnamespaceではPSAと明示された実験manifestの境界を検証し、全組織policy適合とは区別する。

imageは`docker.io/library/spark@sha256:82d7d3c2315a8b826d83485728013c01d1f2542708c83c0c641a363924d065b7`。
live nodeInfoによる現k3sのCRIはDocker 28.4.0である。専用Docker cacheへ公式digestを直接pullし、
save/importの変換を挟まずk3s CRIから同一参照を確認する。既存imageの削除や上書きは行わない。
圧縮layersは約757MiB（registry metadata時点）、実disk影響はprepare後に別記する。

Spark benchmark、Polaris/Iceberg入出力、JupyterHub、MLflow training連携、Comet、multi-node/HAは未達。
正常な固定集計を一般的な性能・本番受入と読み替えない。保持data/backupの削除やwhole-Colima resetはしない。
作成したnamespaceのUID/所有が不明な場合は削除せず、不確実な証拠と残留を明示する。

参照: [Spark 4.1.3 on Kubernetes](https://spark.apache.org/docs/4.1.3/running-on-kubernetes.html)、
[公式image定義](https://github.com/docker-library/official-images/blob/master/library/spark)。

## 2026-09-17: native Spark driver and executor

本WPをlocal-partialとして実機検証。
既存k3s1.33.4/Docker CRI/ARM64を維持し、公式Spark4.1.3の固定ARM64 digestを直接1回pullした。
以前の546 image rowsを保持し、追加はSpark1行のみ。圧縮layers794127774 B、Docker報告794141545 B、
filesystem使用増分1999745024 B（全FSの時点差分）、残容量346994528256 B。
`spark-prepare/ad1a2bac18f445ecaee00fece9d873ef/pull-verification.json`にmanifest/config生byteshashとCRI照合を保存。
Docker snapshotterのID/default null表現差で失敗したprepare2試行も残し、追加pullなしで修正した。

毎回新namespaceにdriver/executor各1CPU2Gi・計2Pods上限、restricted PSA v1.33・non-root・read-only root・emptyDir。
固定32行から21行/4groupを集計し、2partitionのTaskContext・executor1・実Pod hostnameと独立oracleを照合した。
成功`e5b1bbad07a4471286d7e137ff065951`はdriver Succeeded/exit0、
意図的失敗`cb316657a2064bdcb46b2891d25990a6`は同じ集計後にFailed/exit17、
両方で15明示resource＋自動Endpoint2の計17UIDを記録してnamespaceごと回収した。
新namespaceへの既存shirokuma-dev限定admission適用やNetworkPolicyの全拒否経路実証とは主張しない。
Secrets/PVC/保持table/LLMにはアクセスしていない。

初回`aec20a5cb0204d378ca2ffcee7eedc9a`は計算/exit0でもPod phase反映待ち不足でwrapper failed。
Service Orphan削除でEndpointSlice ownerRefが失われたためcleanupも保留した。
元failed receiptを維持し、run labels/既知driver target UIDを再確認してsliceと既知namespaceを限定回収。
`spark-checks/b3d67df806554366bcf38ff0a2db774b/initial-recovery/verification.json`に別の復旧証拠を保存した。
runnerは終端phase待ちと生成Endpoint UIDの先行保存・限定削除へ修正。
Sparkの不要Service/PVC自動cleanupを設定で無効化し、RBACを拡張せずshutdown403を解消した。
namespace置換・曖昧create・不正復旧入力は拒否し、取消の元例外を保持する。
完了後recoverは原receiptを変えず、再Spark実行しないことも実確認した。

正式全回帰43 suites・1129件（1128成功＋従来optional OTel 1skip）、新Spark65件
（contract19/prepare18/runner28）。ログと集計は
`spark-checks/b3d67df806554366bcf38ff0a2db774b/local-regression-final.log` / `regression-summary.json`。
初回compute独立22checksは`spark-preflight/c957ac05ccaa4df4b7b9e5025d943a36/initial-compute-verification.json`
に保存し、wrapperを遡及して合格化していない。


独立fresh監査は`tmp/local-experiment/spark-preflight/c957ac05ccaa4df4b7b9e5025d943a36/final-runs-verification.json`の86checksと
`success-recovery-verification.json`の追補4checksで成功。全14Pod/保持27resources6CM3SecretUID、Docker3、AH428、
履歴4tables5DDL、MLflow59tables/11artifacts、旧2609files/1033directoriesが不変。MLflow DBbytesも一致しserver.logだけ更新。
3実験namespaceのfresh GET不存在、原failed証跡と旧67runfiles不変を確認した。
fixture/metadataは先行sealed証拠継承で、今回のfresh SQL/API確認ではない。設計82documents/関連14testsも成功した。

再現/回収は`tools/local_experiment/spark/README.md`と`make local-spark-*`。
benchmark、Iceberg/Polaris接続、JupyterHub/MLflow training、Comet、multi-node/HA、本番受入は未達。
次はIceberg1.11.0/Spark4.1用JARの隔離classpath試験を進め、成功後に新規tableだけのSpark→Trino照合を検討する。
この節目では次のJAR準備/純粋contractのみで、既存catalogへのSpark接続は未実施。

## 2026-09-17 isolated Spark–Iceberg classpath plan

既存Polaris/SeaweedFSへSparkを接続する前に、公式Iceberg1.11.0のSpark4.1/Scala2.13 runtimeとaws-bundleを
固定hashで準備し、既存公式Spark4.1.3 imageへ追加したlocal imageを使う。既知classpath報告#16856は
この組合せで実際に再現するかを検証し、成功を前提に保持catalogへ接続しない。

- [x] JAR2本の公開SHA512/SHA256・サイズ・ZIP CRCを検証し、PGP未検証を区別する。
- [x] network-none/no-pull buildと全input/context/image identityを検証し、既存imageを保持する。
- [x] Docker network none、host mount0、UID185、1CPU2Gi、bounded tmpfsでlocal[1]を起動する。
- [x] 新HadoopCatalog tableへ32行を書き、fresh DataFrame/SQL/集計を独立oracleで照合する。
- [x] UUID/snapshot/metadataを照合し、同tableのDROPと既知container ID回収を確認する。
- [x] 初回失敗も保持し、retained runtime/MLflow/AH/旧証跡不変を確認する。

このgateはsingle-process local-filesystem試験であり、分散Iceberg・S3接続・Polaris認証・Trino readbackではない。
class存在だけをAWS接続成功としない。詳細と復旧は`tools/local_experiment/spark_iceberg/README.md`。

## 2026-09-17: isolated Spark–Iceberg classpath and read/write gate

Spark4.1.3＋Iceberg1.11.0/Scala2.13の隔離gateが成功。
Maven Centralの固定JAR2本111572756 Bを公開SHA512/SHA256・全ZIP CRCで検証し、
PGP signatureは保存のみ/未検証として区別した。prepare成功`99e250c5978244b3ab00105aba975f4b`。
公式JARのLICENSE/NOTICE重複を初回に厳密拒否した失敗を保持し、既知legal entriesだけを
occurrence/CRC付きで許容、code重複は拒否する。準備証跡は124023450 B。

公式Spark baseの11layersを保持し、JAR2本＋固定scriptだけを1COPY layer追加。
network-none/no-pull build `b8bea1b087b44b4eb53e1351f156e9cf` のimageは
`shirokuma.local/spark-iceberg-classpath@sha256:b7632ecd9a1b619a2836f6de23a27e0a7d13ade09d614fcfe1cc4e23401c8b43`。
Docker報告894722788 B、reuse `9f72a0b92f6c47bc96bd8aaff7d00018` は同一imageでbuild0。
input/source/context前後hashと固定tagを検証し、既存tagを上書きしない。

初回run `2008311aa12e49308e83e09cf91d25b2` はWRITE_FAILED/exit1。
Docker tmpfs既定noexecでARM64 Zstd JNIをmmapできず、誤って一般的なARM64非対応とは判断しなかった。
同UUIDの新tableと既知containerを回収し、failed receipt/logを保持した。
使い捨て/tmpだけexecを明示し（512MiB）、work-dir64MiBはnoexec、network noneのまま
hostnameをlocalhostに固定。root filesystem/UID185/1CPU2Gi/256PIDs/host mount0を維持した。

同image再実行 `61c875252b61416d8e3fb3237cfb0eee` はexit0・7checksすべて成功。
32固定行を新HadoopCatalog tableへ書き、新sessionのDataFrame/SQL全行と集計を独立oracle照合。
groupsは`[[0,8,840],[1,8,896],[2,8,952],[3,8,1008]]`。
table UUID `ee09c58d-0fb7-417d-8c95-78a3fdb65b50`、snapshot `8088807033386546123` を
Pythonの整数でmetadata v2へ照合し、同UUIDをDROPした。64bit IDをJavaScript Number経由で保存しない。
container `ee15ada82dfc7d57613b4275d4a17ffe4acf1f35a73f63cf71220812688da536` は回収し、
実recoverは追加create/start/remove0・原receipt保持。成功時ERROR/Exception log0。
報告済みclasspath issue16856はこの固定構成/範囲では再現せず、他構成全体の解決とは主張しない。

正式全回帰43 suites・1196件（1195成功＋従来optional OTel 1skip）。
新67testsはcontract16/prepare20/build15/runner16、既存native65と同じtest-local-sparkへ含まれる。
`spark-iceberg-checks/2e44ac11e1584fb9b7fa8880541b0e19/local-regression.log` / `regression-summary.json`へ保存した。
read helperがbytesを返す境界とmacOS /var symlinkで落ちた初期offline logsも保持して修正・回帰追加済み。


独立監査`tmp/local-experiment/spark-iceberg-preflight/ea0173dfc7cf45a7ba295d483e4d5bbe/verification.json`の56checksが成功。
14Pod/保持27resources6CM3SecretUID、Docker3、AH428、履歴4tables5DDL、MLflow59tables/11artifacts、
旧2609files/1033directoriesとnative Sparkの67files（初回29inputsも）が不変。両CIDのfresh不存在を確認した。
fixture/metadataは先行sealed証拠継承で、このgateではfresh readしていない。
image inventoryは547→553、旧547全保持。final1に加えclassic Docker buildのUSER＋4LABELの5中間imageがあり、
各build Step ID/actualinspect/config変化とbase11layersを照合した。1追加を期待した最初の検査failedを残し、
上記監査で6追加の由来を補足した。削除なし、全FS時点使用増259411968 B・残容量346592722944 B。
新37inputfilesを封印し、設計82documents/関連14testsも成功した。

これはlocal[1]/local filesystemのclasspath gateであり、Polaris/S3・分散Iceberg・Trino readbackではない。
新推論/監査event0、Secretsなし。再現と回収は`tools/local_experiment/spark_iceberg/README.md`。
次は新規tableだけのSpark write→Trino readbackへ進む。既存NPを編集せずrun専用の一時Ingress NPを
追加する必要があり、資格情報を証跡へ保存しないscopeを具体化してから適用する。
