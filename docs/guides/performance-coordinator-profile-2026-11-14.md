# 性能試験の準備: coordinatorの正式な資源設定

2026-11-14 JST。[性能試験計画](performance-comparison-plan-2026-11-13.md)の後続として、coordinatorのCPU・container memory・JVM heapをprivate設定から再現できるようにする。worker、query memory、VMの拡張は後続として扱う。

## 設定と資源予算

`logical_foundation` のversion 1設定に任意の `trino.coordinator` を追加する。未指定時の設定正規化とpublic/private renderは従来と同じbytesになる。値はmillicores/MiBの正整数で、bool・小数・不明field、request超過、container以上のheapを拒否する。

```json
{
  "trino": {
    "coordinator": {
      "cpu_request_millicores": 1000,
      "cpu_limit_millicores": 4000,
      "memory_request_mib": 8192,
      "memory_limit_mib": 12288,
      "heap_mib": 8192
    }
  }
}
```

これは既存設定へ追加する部分であり、単独のfoundation設定ではない。任意の有効な値を受け付け、固定の試験名や特定データ専用にしない。CPUは1000mを1、memoryは1024Miを1Giに正規化し、Kubernetesが返す数量と一致させる。

| 項目 | 従来のcoordinator | 今回の正式設定 |
|---|---|---|
| CPU request / limit | 100m / 4 | 1 / 4 |
| memory request / limit | 256Mi / 4Gi | 8Gi / 12Gi |
| JVM heap | 2GiB | 8GiB |
| query memory全体 / node | 1GB / 1GB | 1GB / 1GB |
| heap headroom | 512MB | 512MB |

workerはheap 2GiB/container 4GiB、requests 100m/256Mi、limit 4 CPUを維持する。bootstrap initContainerと他のfoundation componentも変更しない。VMは16 vCPU/96GiB/400GiBのまま、L1は未適用。

Trino 483はquery max-memory-per-nodeとheap headroomの合計がheap未満であることを要求する。現在の固定値1GB+512MBに合わせ、設定validatorはheap>1536MiBを要求する。headroomはheap内部の未追跡割当用であり、heap外のcontainer余裕とは別である。今回の8GiB/12GiBはheap外へ4GiBを残す。workerのquery容量を増やしたことにはしない。[483 resource management](https://raw.githubusercontent.com/trinodb/trino/483/docs/src/main/sphinx/admin/properties-resource-management.md)、[483 deployment](https://raw.githubusercontent.com/trinodb/trino/483/docs/src/main/sphinx/installation/deployment.md)

適用前のnodeは16 CPU/約94.1GiB allocatable、実使用約954m/9,769Mi。今回の変更後のrequests合計は約6.675 CPU/18.92GiB、bounded memory limits合計は約81.29GiBとなり、このVMで適用可能と確認した。CPU limitsの合計は既存のovercommitを含むため、専用CPUの保証や実測性能とみなさない。

## 生成・検証・適用

[設定生成](../../tools/local_experiment/logical_foundation.py)は共通の純粋helperで数量・JVM設定・profile digestを作る。workerのコピー後にcoordinatorだけへ適用し、heapのみ変更した場合もPod templateのdigestが変わって再起動する。helperはsecret/SPI jarの読込や全manifest生成を行わない。

[Provider](../../tools/local_experiment/logical_provider.py)は明示profile時、coordinatorのDeployment/Podのrequests・limits・digestと、ConfigMapのJVM設定を照合する。readyだけで資源設定一致としない。既存の登録証拠にないConfigMap UIDを新stable pinとして加えず、owner/revision/name/namespaceと既存UID pinを確認するため、旧登録証拠からの接続を維持できる。共通のUID pin照合規則や凍結済み復旧証拠は変更しない。

既存foundationでは、次の順で所有するcoordinatorの2 objectだけを変更する。

1. 現在の設定・native object・起動argv/env・台帳・固定snapshotを保存し、実行中/未終結の処理と資源予算を確認する。
2. Workbenchを停止し、operator gateと排他的lifecycle gateを保持する。private設定は同じpathへ保存し、foundation generation/revision、DB binding/identity/grantを維持する。
3. 新設定をcanonical renderし、差分がcoordinatorのConfigMapとDeploymentだけであることを確認する。現在のowner/UID/resourceVersionを照合して両objectをreplaceする。
4. rolloutと元の登録証拠への通常Provider bindを確認し、実JVM引数・cgroup memory上限も確認する。gateを解放してWorkbenchを同じ起動環境で再開する。
5. 通常APIで固定snapshotのSQLと停止/再開を確認する。復元時は元設定bytesと元の2 objectを同じ手順で適用する。送信結果が不明な場合はreceiptとnative実体を照合してから続行する。

private設定のbytesはruntime profileのsealに含まれるため、Workbenchの再起動が必要である。foundation全体のapplyはbootstrap/他componentも対象にするため、今回のcoordinatorだけの更新では使用しない。

## 検証結果

| 検証 | 結果 |
|---|---|
| 設定と互換性 | foundation 43件、profile/Provider 13件が全件成功。未指定の3構成で旧HEADとの設定JSON・public/private YAML bytes完全一致。元登録proofから新profileへのfresh bindも成功 |
| 適用・復元 | 正式設定→元の2GiB設定→正式設定を3回の明示transitionで検証。coordinatorのConfigMap/Deploymentだけをcanonical renderから変更し、worker instanceと他objectを保持。各transitionのpublic SQL前に台帳79表の全行不変を確認 |
| 実JVM・cgroup | 最終coordinatorは-Xmx8G、memory.max=12GiB、CPU quota=4。Pod requestsは1 CPU/8Gi、limitsは4 CPU/12Gi。workerは-Xmx2G、memory.max=4GiB、元requests/limitsのまま |
| 通常停止・再開 | 通常APIのchill/wakeが成功。停止中のWorkbench再起動も元登録証拠にbindし、再開後に正式設定を維持。foundation generation IDとrevision 1、DB binding/identity/grant、9項目contextを維持 |
| 新しい公開取込 | SF0.01の既存24 Parquetをbyte一致のまま新campaignへ複製し、新prefix cpa787へ43 chunks・277,976行をbatch500で通常APIから投入。全操作FINISHED、全stage登録解除完了 |
| 全表・99 SQL | 全24表・277,976行の型・全値・重複がParquet参照と一致。直接Trino/公開SQLの各99本・3,928行が参照と一致し、全24表pinsを前後で確認。固定99本以外のvariantの合否は含めない |
| 保護対象と健全性 | 元TPC-DS24表/TPC-H8表の全列schema・UUID・snapshot・metadata pointer、旧部分取込先と履歴を保持。qualification中の自動再起動0、cgroup OOM/kill 0、現container logのheap OOM 0。doctor healthy 46項目 |
| 全体回帰 | `make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3` は157群・5,645成功・既定39 skip・失敗0。旧試験の欠落0、新規19件を確認。ソース固定後の単一実行で通過 |

設計整合・改行・空白・秘密情報ファイル名検査も通過した。39 skipは既定の実機opt-in、OPA/pyarrow、任意benchmark依存/既存source指定による。今回の重点56件はskipなし。

queryと取込の速度比較・SLOの判定は行っていない。回帰検証と並行した小規模qualificationであり、新しいseedの生成や大規模性能の合否とは分ける。設定省略時のlegacy probeに新しいI/Oは増やさず、明示profileのACTIVE確認にも全manifest/secret/SPI jarの読込を追加していない。

証拠はGit外の `shirokuma-coordinator-profile-2026-11-14` に保存した。元設定backupとnode予算は `inventory-196c418c`、native適用/復元と停止再開は `transition-formal-*` / `transition-baseline-*` / `transition-lifecycle-*`、実値と保護対象は `final-native-engine.json` / `retained-pins-after-qualification.json` / `verification-summary.json` から追跡できる。保存した元設定backup・元登録証拠・旧UNKNOWNのquery/intent/proofは書き換えない。

新campaignの `public-load`、`snapshot-pins`、`tables-reference` / `tables-direct`、`tpcds-reference` / `tpcds-direct` / `tpcds-public` に入力・receipt・全結果を保存した。元campaignのVM/state増分と新artifact領域を合わせて容量確認し、最大次工程1GiBとdrain256MiBも予約した。新campaign計上増分は約0.53GB、検証時host空きは約3.100TB。stageの非purge登録解除で残るfileも占有から除外しない。

## 次の順序

後続の[bulk取込と公開APIの全結果取得](performance-bulk-results-2026-11-14.md)と[Parquet分割生成/複数ファイル登録](performance-parquet-shards-2026-11-14.md)を整備した。次は残るTPC-DS variant/scaleと計測器負荷等のG1残件を整え、L1/guest disk拡張と100GB pilotへ進む。追加物理占有2TB・host予備256GiBを維持する。
