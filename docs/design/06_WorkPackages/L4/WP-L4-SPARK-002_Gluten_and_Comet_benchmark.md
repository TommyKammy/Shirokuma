---
project: Shirokuma
doc_id: "WP-L4-SPARK-002"
title: "Comet-first Spark accelerator benchmark with Gluten bonus"
status: local-complete
created: 2026-07-05
updated: 2026-09-20
version: "0.3"
area: "workpackage"
tags: [shirokuma, wp, l4, compute]
---

# Comet-first Spark accelerator benchmark with Gluten bonus

## Local scope（2026-09-20）

Comet 1.0.0のARM64 local native gateと、同一remote Iceberg snapshotでのstock Spark比較を完了した。既存Spark 4.1.3/Scala 2.13/Java 17/Iceberg 1.11.0 imageへ、固定JARとjobだけのoffline layerを追加した。
手順・固定artifact/source identityは[実装plan](../../../../tools/local_experiment/spark_comet/plan.md)。公開SHA-1とdownloadの固定SHA-256を照合し、detached signatureは保存したが暗号学的検証は未実施。

### Local acceptance — complete

- [x] networkなしの`local[1]`で32行・4集約がoracleと一致し、native Iceberg scan/HashAggregateを確認した。prepare1.396秒/build11.673秒/native14.203秒は初回passed。所有UUID drop・container終了/回収も確認した。
- [x] remote600,572行の同一UUID/snapshotでstock→Comet各8測定が初回passed（218.856秒）。16結果が共通の保存Decimal oracleと一致し、Comet全8測定にnative scan/aggregateがある。
- [x] remote catalog/S3 credential経路を実行し、別namespaceのUID・終了・回収、同UUID table drop、access回収・query drain・既存fixture保持を確認した。
- [x] 同条件のrepeat3回中央値を報告した。q1はstock688.041ms/Comet437.773ms、q6は195.601ms/197.370ms。各queryの初回は分けて保持した。

実行順序はstock→Comet固定で、source準備や先行queryが共有cacheを温め得る。単一hostの固定TPC-H由来subsetに限定し、一般的な高速化・標準TPC-H score・multi-node・本番受入を主張しない。両variantはdriver/executor各1 CPU上限・2 GiB、heap1 GiB/off-heap256 MiB、executor1台・AQE無効の同条件。S3は専用bucket範囲の静的credentialで、catalog read-only grantがS3 write拒否を保証するものではない。table drop後のS3 orphan不在は未検証。
独立保存照合はlocal81checks/31inputs、remote191checks/68inputsで成功した。後者は保存oracleと16結果・plan・所有cleanupの照合であり、600,572行の独立再生成ではない。全回帰102suites/3295実行（3294PASS・既存1skip）と既存44files/MLflow22runs・1383points・22artifacts/AgentHouse560保持も成功した。

### Remaining boundary

2026-09-20の[Gluten公式downloads](https://gluten.apache.org/downloads/)は1.6.0のSpark 3.3/3.4/3.5/4.0 binariesとIntel Xeon 8272CLのbuild targetを案内し、選定Spark 4.1.3/ARM64の公開binary経路は確認できなかった。今回はfailedではなくskippedであり、ARM64全般が未対応と断定しない。source buildの互換性検証は任意の後続作業とする。
x86/Rosetta・外部registry公開・常駐service・host-global設定は追加しない。JAR/context/image・旧image・証跡・backupを保持し、unknownは所有ID/cleanupを確認せず再利用しない。容量とprivate証跡は[Milestones](../061_Local_Experiment_Milestones.md)に記録した。

[ADR-0034](../../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)により、下記2026-07-05のIssue/PR/CI gateは歴史として保持し、承認済みlocal実験の待機条件にはしない。

## 2026-07-05 historical plan

| Field | Value |
|---|---|
| WP ID | WP-L4-SPARK-002 |
| Level | L4 |
| Workstream | Compute |
| Depends-on | WP-L4-SPARK-001, WP-L4-BENCH-001 |

## Scope

Reframe Photon-like Spark acceleration around DataFusion Comet as the first candidate on Apple Silicon. Gluten/Velox is a bonus only if arm64 support works in the selected environment.

## Acceptance Criteria

- Comet runs with the selected arm64 Spark image, or a documented blocker is produced.
- Baseline Spark vs Spark+Comet is benchmarked on TPC-H/TPC-DS subset.
- Gluten is tested only after confirming compatible arm64 native path; otherwise it is marked skipped, not failed.
- No x86_64/Rosetta path is accepted unless explicitly recorded in ARM64 compatibility table.
- Report explains that single-box results are relative only.

## Notes

Comet is Rust/DataFusion/Arrow-native and is the primary Apple Silicon candidate. Gluten remains interesting but not required.

## Definition of Done

- Work is represented as a GitHub Issue linked to `WP-L4-SPARK-002`.
- Changes are committed through PR.
- Required CI and policy checks pass.
- Documentation and WBS are updated.
- Any deviation from ARM64-native execution is recorded in [[10_Research/106_ARM64_Container_Image_Compatibility]].
