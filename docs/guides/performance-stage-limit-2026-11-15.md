# 性能試験: coordinator stage上限の正式適用と承認14件suite再検証

2026-11-15 JST。既存の固定source・oracle・SQL・snapshotを変更せず、coordinatorの任意 `max_stage_count` を正式設定へ追加して14aと承認14件suiteを再実行した。結果はqualificationであり、SLO・大規模性能・G1全体の完了ではない。

## 設定と復元

Trino 483の設定キーは `query.max-stage-count`（既定150）。今回の正式profileは `trino.coordinator.max_stage_count=400` とし、coordinatorの `config.properties` だけへ反映した。workerのConfigMap/Deployment、heap8GiB/container12GiB、worker2GiB/container4GiB、query memory 1GB/node、VM16 vCPU/96GiB/400GiBは変更していない。

適用・復元は既存のcanonical transition手順を使用した。差分は `ConfigMap/logical-trino-config` と `Deployment/trino-local` だけであり、Deployment UID `d70013f7-30d8-49ff-a406-bac38249540c` を保持した。stage400時のcoordinator Podは新規作成、復元時も新規Podへ置換され、worker Podは同一のままだった。stage annotation/propertyは復元後に存在せず、設定bytesは事前のSHA256 `e797cab33c129eb2f9bf5cfc895700170d09ab43df9c9e750773b4330288cc6b` と一致した。Workbenchは既知の起動argv/envで再起動し、公開APIのsession/contextと59表pinsを確認した。

## 結果

| 経路 | 14a standalone | 正式14件suite | 行数 | 備考 |
|---|---|---|---|---|
| 直接Trino | SUCCESS 100行 | SUCCESS 14/14 | 903 | 14a 9.11秒、suite合計15.054秒（client attempt `elapsed_s` の単純合計） |
| 公開API all-results | SUCCESS 100行 | SUCCESS 14/14 | 903 | 14a 4.752秒、suite合計15.511秒（同） |

独立評価でfocused 217件（48+16+153）と30 attemptsの全型・全値・重複・ORDER、bound manifest、materialized Spark reference、59表pins、protected hashesを再確認した。Make162/162の全commandを実行し、5,874成功・74 skip・失敗0だった。最終runtimeはstage未設定のTrino既定150へ復元済みであり、同じ14件suiteを再実行するにはqualified 400 profileの再適用が必要である。

比較は既存の全型・全値・重複・ORDER契約、decimal/integer/date/NULLの厳密比較、浮動小数 `1e-8` 絶対/相対許容差を使用した。参照は固定DuckDB referenceとし、両経路とも `independent_cross_engine=true`、`public_api_qualified=true`（公開API）、未実行0・失敗0だった。14aの直接query IDは `20261114_221533_00005_by7cn`、公開backendは `20261114_221543_00006_by7cn`。正式suiteの14aは直接 `20261114_221550_00009_by7cn`、公開backend `20261114_221605_00023_by7cn`。

## 資源・健全性

stage400のcoordinator Podはcgroup memory peak約2.73GB、worker Podは約2.49GB（2,487,177,216 bytes）を観測した。1回の欠測は計画的なPod置換中のresource sampleであり、45有効サンプルでOOM/oom_kill 0、container再起動0、新規の未解消実行0。復元後coordinator Podはpeak約1.78GB、restart 0、doctor healthy 46項目。既存のfenced UNKNOWN 5件は履歴として保持し、unresolved admissionは0のまま。59表（24+8+24+1+2）のpinsは適用前後で同一SHAだった。

## 再現

```sh
# 正式設定の適用/復元はGit外のtransition harnessで、設定bytes・native object・
# 台帳・pins・Workbench起動環境を保存して実行した。
# 14a standalone: 既存 case-public-14a/queries.json + approved-reference
# 正式suite: approved-bound-queries.json + approved-reference
rtk proxy benchmarks/local_perf/.venv/bin/python -m benchmarks.local_perf run "$CAMPAIGN" approved-direct \
  "$OLD/approved-bound-queries.json" --adapter trino --url "$DIRECT_URL" --ca-file "$TRINO_CA" \
  --auth-file "$DIRECT_AUTH" --catalog logical_perf_01 --schema sandbox \
  --references "$OLD/campaign/approved-reference" --scale 0.01 --timeout 600 \
  --peak 2147483648 --abs-tol 1e-8 --rel-tol 1e-8
```

公開APIは別途 `freeze-public-connection --all-results` で9項目contextを固定し、同じmanifest/referenceへ `--adapter public --connection-file ... --all-results` を実行する。stage上限を変えたままsuiteを再実行する場合は、同じ形式の別campaignを使い、旧failed receiptを上書きしない。

## 制約

- 400は観測した348 stagesを許容するための今回の設定であり、他のSQL・scale・同時実行に対する一般値を主張しない。
- `elapsed_s` はclient attemptの試行時間で、結果取得・検証境界を含むqualificationの単発観測である。engine query time・SLO・percentile・warm/cold比較ではない。data cacheはunknown。
- 別scaleの原Spark資格確認、計測器負荷、L1/guest disk拡張、100GB pilot、同時実行/到着率、24時間試験は未実施。G1全体は未完了。
- 証拠はGit外のreview root `runtime/` に保存する。privateな設定bytes・Workbench launch環境・control-store backupは0600のままGit外で保護し、artifact tree全体を共有可能とは扱わない。一時認証fileは利用後に除去した。
