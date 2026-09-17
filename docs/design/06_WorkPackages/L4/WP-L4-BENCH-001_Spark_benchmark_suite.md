---
project: Shirokuma
doc_id: "WP-L4-BENCH-001"
title: "WP-L4-BENCH-001 Spark benchmark suite"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.3"
area: "workpackage"
tags: [shirokuma, workpackage, l4, benchmark]
---

# WP-L4-BENCH-001 Spark benchmark suite

## Summary

Spark/Gluten/Comet/Trino比較を行う

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

`level:l4`, `area:benchmark`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L4-BENCH-001: Spark benchmark suite.
Read AGENTS.md, related ADRs, and this Work Package.
Follow ADR-0034 local execution and commits without Issues, PRs or external review.
Include tests, docs, measurement limits and owned resource cleanup evidence.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-17: same-snapshot local measurement plan

WP-L4-SPARK-001で確認した既存Spark4.1.3/Iceberg1.11.0 image、Trino483、Polaris、
SeaweedFSを使う。新image/service/cloud費用・実LLM呼出しは不要。
固定TPCH SF0.1 lineitemの9列をDecimalへ正規化し、owned table 1つを新規作成する。
生DOUBLE値の有限性・2桁往復・NULL/範囲を先に検査し、sourceと作成後snapshot全行を
同じ順序でstreamしてPython Decimal oracle/logical SHA/件数を照合する。
raw datasetはhostへ保持しない。最大1M行/256MiB受信、data files32個/256MiB。
旧SF10の3run receiptや保持prefixを再利用・削除しない。

両engineが同じUUID/snapshot/metadata/data-file一覧を読む。queryはQ1由来の4SUM/countと
Q6 revenueだけとし、型推論の異なるAVGは含めない。標準TPC-H全体とは扱わない。
最終Decimal(28,6)、NULL、countを独立oracleと照合し、2engine×2query×4回＝16観測を記録する。
初回1＋繰返し3で、shared cache既温・Trino先行/Spark後行の固定順を明記する。
SparkSession起動、native全体、query時間を分ける。中央値から一般的な性能優劣は主張しない。

Sparkは既存driver/executor各1CPU/2GiBの固定profileで読み取りだけを行う。
専用Polaris roleはTABLE_LIST/READ_PROPERTIESのみ。ただしS3の既存static bucket資格情報は
read-only/prefix限定になったとは扱わない。開始前にhost/VM空き5GiB以上を確認する。
Spark停止とowned Trino query drainを確認して一時権限・同UUID tableを回収し、保持fixtureを再照合する。
不明なPOST結果の再送や名前だけでの削除を行わず、旧失敗receiptを保持する。
実行手順・測定範囲・回収の限界は
[Spark/Trino benchmark runbook](../../../../tools/local_experiment/spark_benchmark/README.md)に記録する。
Comet/Gluten、自動engine選択、複数規模/統計的評価、HA、本番受入は後続のまま。


## 2026-09-17: Spark / Trino same-snapshot baseline

WP-L4-BENCH-001の最小測定を実装・実機検証した。固定TPCH SF0.1 lineitemの9列、
Decimal正規化、独立Python oracleを使う。元データのNULL/finite/型/往復検査後、
sourceと作成後snapshot全行をstreamし、件数・logical SHA・期待集計が一致した。
hostに全datasetは保存しない。実sourceは600,572行、受信38,853,382bytes、
IcebergはParquet 1file/5,011,080bytes。data-file予算32個/256MiB内である。

run `e727a68de2b64313bc3fa3dc98817dac` は13:00:58–13:05:01 UTCに成功した。
同UUID `e6abed5b-9cda-4d8c-9d29-a71e42aac122`、snapshot `8013311425425878260`（親なし）、
同metadata locationとfile一覧を両engineで確認し、snapshot指定の16測定がすべてoracleと一致。
logical SHAは `87e3f2d1f7cca433c559c2f183baae1c78b37a26358223000bc0039a20f9ffbe`。
Q1由来の4SUM/countは4group、Q6 revenueは `11803420.253400`。
Spark4.1.3/Iceberg1.11.0の既存ARM64 imageを使い、新build/download/model呼出しはない。

| 固定query | Trino初回client秒 | Trino反復3回中央値秒 | Spark初回秒 | Spark反復3回中央値秒 |
|---|---:|---:|---:|---:|
| q1_sums | 2.563 | 2.568 | 6.484 | 0.677 |
| q6 | 3.062 | 3.076 | 0.442 | 0.252 |

TrinoはPOSTから最終page消費までのclient wallで、network/JSON/row処理・途中のdurable journal保存を含む。
serverのelapsedTimeMillisは1075–1542ms、wallTimeMillisは17–139msとして別に保存した。
Sparkはsql→collect完了までで、結果正規化/oracleは計測外。SparkSession開始6.358秒、
nativeのpreflight→cleanup全体38.305秒も分離した。executor readinessは初回queryへ残りうる。
元stream/guard/権限準備、別executor probeとcleanupをquery時間へ足していない。
shared cache既温、Trino先行・Spark後行、resource配分差、各query3反復という条件の観測であり、
一般的なengine速度順位・cold cache・標準TPC-H性能・統計的有意差は主張しない。

source/schema/query/result/hashを型付き固定契約にし、任意SQL/endpoint/SFはCLIから受け付けない。
実Trinoのdecimal型名に含まれるcomma後空白を狭く正規化し、precision/scale変更は拒否する。
Spark4.1.3で未対応のDECIMAL型付きliteralは両engine共通の固定CASTへ変更した。
AVGの型推論差は今回のSUM/count比較へ混入させない。

Polaris一時roleはTABLE_LIST/READ_PROPERTIESの2権限だけ。READ_ONLYはcatalog権限の意味で、
既存S3資格情報がbucket scopeのままである点は維持する。token実TTL3600秒。
native19UID（17明示/owned＋2自動Endpoints系）、一時ingress NP2・Polaris3entities/2bindingsを回収し、
owned query drain後に同UUID tableをDROPした。保持fixture/全table一覧の前後一致を確認。
executor別probeはstage28/task36,37/partition0,1/attempt0で、query計測へ加算していない。
Secret本文は保存projectionへ入れず、未知POSTの再送・不明な同名table削除を禁止する。
S3 orphan不存在・即時JWT失効・完全rollback・本番受入とは区別する。

benchmark79＋native/access追加10を含むSpark331 testsが成功。
全local回帰44suites/1416件（1415成功＋既存optional OTel1skip）が成功。
全logと集計は `tmp/local-experiment/spark-benchmark-checks/3a7b51d8f277490aa1fd457dc2e18bde/`。
fresh前後保持監査は `spark-benchmark-preflight/f8a7fdda5ed342eebb75780cf7d6431b/`。
AH440増分0、Metadata7plans/14tables/14edgesの35endpoints、保持3044files、
14Pods/27resources6CM3SecretUID、Docker3、SQL履歴4tables/5DDL、fixture全歴史、
MLflow59tables/11artifacts、images553、Polaris6権限一覧の内容が不変。
開始前の空き容量はhost2,486,954,598,400bytes/VM346,868,105,216bytesで5GiB以上。

WPはlocal-partialを維持する。Comet/Gluten、複数規模/資源条件の評価は後続。
次は保存済み16測定を既存MLflowの別experimentへ取り込み、観測範囲を保って検索・比較できるようにする。
