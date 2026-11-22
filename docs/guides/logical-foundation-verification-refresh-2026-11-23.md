---
project: Shirokuma
doc_id: DB-LOGICAL-VERIFY-REFRESH
title: Logical foundation verification receipt refresh
status: accepted
created: 2026-11-23
updated: 2026-11-23
version: "1.0"
area: guides
tags: [database, logical, foundation, trino, receipt]
---

# Logical foundation verification receipt の更新

`logical_foundation apply` で Trino イメージや secrets SPI jar を更新すると、Kubernetes の foundation は更新されるが、`logical_foundations.verification_ref` が指す保存 probe は更新されない。保存 probe の `spi_sha256` が現在の jar と一致しないため、Workbench と 8080 endpoint は `FOUNDATION_NOT_READY` で起動・照会に失敗する。

この guide は、その差分だけを明示的に解消する正規手順を示す。通常の Workbench 起動や `logical_foundation apply` が暗黙に receipt を更新することはない。

## 前提

- `logical_foundations` は `READY`、`warehouse_config_generations.state` は `APPLIED`。
- Trino は `ACTIVE`（coordinator + worker が ready）で、SPI を含む foundation probe が成功する状態。
- Workbench と 8080 endpoint を停止しておく。CLI は `--confirm-workbench-stopped` を必須とし、`warehouse.lock` の exclusive gate を 5 秒 deadline で取得する。取得できない場合は `FOUNDATION_BUSY` で終了する。フラグは停止の確認であり、プロセス検出ではない。gate は lifecycle 操作を排他するが、idle な Workbench 自体は排他しないため、停止を手順の必須条件とする。
- Workbench と同じ `SHIROKUMA_RUNTIME_PROFILE` を設定する。

## 実行

```sh
export SHIROKUMA_RUNTIME_PROFILE="$PWD/var/shirokuma/runtime-profile.json"
PATH="$PWD/tools/local_experiment/dataops/.venv/bin:$PATH" \
python3 tools/local_experiment/logical_creations.py refresh-verification \
  --confirm-workbench-stopped
```

成功時は次の形の JSON を出力する。

```json
{"changed":true,"generation_id":"...","revision":1,
 "previous_verification_ref":".../creations/old.json",
 "verification_ref":".../creations/new.json"}
```

差分がない場合は `changed:false` で、receipt も DB も変更しない。

## 比較と更新の境界

- 比較するのは `foundation_id` / `revision`、`readiness`、`hashes`、`incarnation.node`、および `_stable_pins`（namespace / PVC / Service / Deployment / ConfigMap / PV の UID）。
- 許容する差分は `spi_sha256` と、Trino rollout で変わる `incarnation.compute` / `incarnation.images` / `incarnation.tunnels` のみ。`images` は Trino compute Pod の UID に対する差分だけを許し、SeaweedFS / Postgres / Polaris の image digest 集合が変われば拒否する。Trino compute Pod の UID 変更や `tunnels` だけの差分は no-op とし、Trino image digest または `spi_sha256` が変われば新しい receipt を作る。
- stable pin、node、runtime file hash、generation/revision の差分は `FOUNDATION_BINDING_MISMATCH` または `FOUNDATION_NOT_READY` で拒否する。
- 成功時は `_receipt` で**新しい** 0600 receipt を作り、`supersedes` に元の receipt パスを記録する。`logical_foundations.verification_ref` だけを CAS で更新する。
- 元の receipt ファイルは削除・上書きしない。`warehouse_config_generations.configuration_refs`、`database_revisions`、`grant_revisions`、generation row、`logical_foundations.revision` は変更しない。
- `warehouse_resident` の provenance fence は、`configuration_refs` の元 receipt を `supersedes` で参照し、stable pin / node / hash が一致する後継 receipt だけを許容する。これにより immutable な generation provenance を書き換えずに現在の probe を進められる。

## 更新後

Workbench と 8080 endpoint を通常の手順で起動し、`/api/status`、`/api/v1/admin/databases`、`SELECT version()`、および Iceberg テーブルへの SELECT を確認する。`logical_foundation apply` の成功出力にも、この refresh コマンドが次手順として表示される。
