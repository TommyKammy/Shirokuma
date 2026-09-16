---
project: Shirokuma
doc_id: "WP-LOCAL-MILESTONES"
title: "Local Experiment Milestones"
status: active
created: 2026-09-16
updated: 2026-09-16
version: "1.0"
area: work-packages
tags: [shirokuma, local, evidence]
---

# Local Experiment Milestones

## 2026-09-16: ローカル開発への移行とLakehouse SQL検証

適用方針は[ADR-0034](../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)。
この記録はローカル実験の実測結果であり、過去の公開承認・Issue完了・本番受入を変更しない。
作業ブランチは `local/autonomous-experiment`、ホストの作業checkoutは
`Shirokuma/worktree/local-autonomous`。従来のmain checkoutと未コミット作業は保全した。

### 確認した機能

- **WP-L1-LAKE-003**: 既存Colima/k3sを起動し、SeaweedFS・PostgreSQL・Polarisの
  readinessを確認。新しいIceberg smoke Jobで既存fixtureを再読込した。
  `created=false`、2行、snapshot `7141845066324476177`で、保持データの再作成はしていない。
- **WP-L1-QUERY-001**: Trino 483の単一coordinatorをARM64で起動。
  `SELECT id, name FROM iceberg.smoke.fixture_v1 ORDER BY id` は
  `[[1,"shirokuma"],[2,"polaris"]]`を返した。
- **ローカル適用経路**: `prepare → apply → resume`を既存catalogに対して実行。
  UID/resourceVersionで競合を検出し、変更したFlux停止状態だけを復元できた。
  親`flux-system`の停止は今回のローカルモード用に維持している。
- 新規の実験用namespace `local_experiment`でCREATE/SELECT/DROPが成功した。
  namespace限定`TABLE_WRITE_DATA`とcatalogのpurge設定を追加した。
  `smoke_4bc60afe11ad4123a4737e82ea41c7c5`の2行を読み戻し、同tableを削除した。
  初回の失敗時に作ったtableも削除し、`SHOW TABLES FROM iceberg.local_experiment`は0件。
  保持fixtureの権限とデータは変更していない。
  実測時刻は2026-09-16 04:40–04:41 UTC（13:40–13:41 JST）。

### ローカル成果物と範囲

- 入力は既存のupstream Trino 483 archive、SHA-256
  `4f3978428f26f36398c94b85a3e03b5301394919c8a4271b497b0fcd1698d0cb`。
  hardened source rebuildや公開署名済み成果物として扱わない。
- 実測runtimeは`docker://28.4.0`。同じColima Docker daemonでビルドし、
  runtimeで解決できる`RepoDigest`を使用した。containerd importはこのprofileでは不要。
- 実行imageは`shirokuma.local/trino@sha256:e169b9d87a2f41b62ffc9ff6b7b90187ed6f4f758503f409566e16a0a5966ed2`。
  `imagePullPolicy: Never`、ClusterIP、非root、読み取り専用root filesystem、
  CPU limit 4、memory limit 6Gi。ホスト公開用Ingress/NodePortは追加していない。
- 新規imageは約630MB。ローカル再利用用image archiveも同程度。
  既存PVC・バックアップ・Colima diskは削除していない。

### 再実行

コマンドは作業checkoutのrootから実行する。

```sh
rtk make verify-local-experiment
rtk make local-experiment-status
rtk make local-trino-build TRINO_ARCHIVE="$TRINO_ARCHIVE"
rtk make local-trino-render TRINO_IMAGE_DIGEST="$LOCAL_RUNTIME_DIGEST"
rtk proxy kubectl --context colima-mac-studio-solo -n shirokuma-dev \
  port-forward service/trino-local 18080:8080 --address 127.0.0.1
rtk make local-trino-smoke
rtk make local-trino-smoke-write
```

`local-trino-smoke-write`は一意の試験tableを作成し、その実行が作成に成功したtableだけを削除する。
初回のpurge設定は`tools/local_experiment/polaris_permissions.py`と
`bootstrap/trino/local-experiment/README.md`を参照する。
runtime適用の詳細と復元は`docs/LOCAL_EXPERIMENT_RUNNER.md`を参照する。

### 検証と未達事項

ローカル機能の証拠は、ホストのGit管理外の`local-autonomous-20260916` evidence directoryと
checkoutの`tmp/local-experiment/`に保存した。認証情報はcommit・ログへ出力しない。

- `make verify-local-experiment`: 36 tests passed（runner 18、SQL 6、Polaris権限12）。
- `make verify-design-context`: 54 documentsを検証し、関連14 testsもpass。
- `verify_polaris_runtime.py audit`: 既存のruntime activation contractを保持。
- 既存Polaris 198、Iceberg 126、Trino publisher 133、feasibility 52、security feasibility 12、
  UI、observability、Go、Kyverno、secret scanと差分の空白検査はpass。
- 旧Trino bootstrapの4 failuresと供給網の5 failures、期限切れTrivy例外は下記の別境界として残す。

Polaris設定変更は`currentEntityVersion=1`からversion 2へ更新した。
`polaris.config.drop-with-purge.enabled`は以前は未設定、現在は`"true"`。
設定はcatalog単位だが、追加したdata privilegeは`local_experiment`に限定した。
失敗時も元のエラーと試験table名を失わないよう、SQL smokeのcleanup報告を修正した。

従来の`make verify`は公開経路向けの判定を保持するため、ローカル実験全体の合格を表す
コマンドにはしない。期限切れのresident authorization・Trivy例外、および
公開未承認のTrino runtime/固定input inventoryの検査は引き続き失敗する。
過去の期限・検証結果を更新して成功扱いにはしていない。

ホストのHelm 4.2.3は旧テストが固定する4.2.2と異なるため、該当テストはchecksum検証済み
4.2.2をそのプロセスのPATHだけに指定して実行した。グローバル設定は変更していない。

`shirokuma doctor`のclusterはhealthyだが、意図的なFlux停止と旧policy判定により
全体はdegraded。Trino/Polaris/S3のデータ経路はSQL smokeと実機readinessを別に確認する。
保持fixtureの`REPORT_READ_METRICS`権限不足は警告として残るが、SELECT成功とは区別する。

### 復元

実験用TrinoのDeployment/Service/NetworkPolicyと生成ConfigMapだけを削除すれば
query engineを撤去できる。既存catalog・storage・PVCは削除しない。
Polarisの追加roleは`local_experiment`限定であり、撤去時はbindingを外して追加roleを削除し、
catalogのpurge設定を保存した以前の値へ戻す。
親Fluxの元の停止状態は`flux-before.json`に保存済み。GitHubのdesired stateへ戻す場合だけ
差分を確認して復元する。Colima全体のresetはこの手順に含めない。
