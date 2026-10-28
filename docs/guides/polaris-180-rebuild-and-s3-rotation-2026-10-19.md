# Polaris 1.8.0: S3キー更新とクリーン再構築の実機検証

2026-10-19、専用ローカル環境で停止を伴うDB別S3キーのローテーションを実施した。
続いて独立した空のKubernetesクラスタを作成し、恒久OpenTofu / GitOps構成から再構築した。
S3更新は30項目、クリーン再構築は機能61項目と再現性21項目が成功した。
Database製品モデル、Workbenchなど後続Phaseの機能は追加していない。

## 検証した環境の境界

- 通常環境: `colima-mac-studio-solo` の既存Kubernetes。DB別S3キーを実際に更新した。
- 再構築環境: 同じ専用ColimaのDocker内に作成した独立K3s container
  `shirokuma-polaris-rebuild-20261019`。別networkと新規named volumeを使用する。
- K3sは既存環境と同じ `v1.33.4+k3s1` / arm64。
  server imageは `rancher/k3s@sha256:31a85202039b95a75781d537b35e5756ded4b43949f8e1f9f3db8e69d3b73497`。
- 新クラスタのAPIはhostの127.0.0.1の別portへ公開し、Pod / Service CIDRも既存環境と分けた。
- private kubeconfig内だけに既定context名を定義した。hostの既定contextは切り替えず、
  OpenTofuへ `kubeconfig_path` の絶対pathを明示した。
- 新環境の開始時点でShirokuma namespace / PVCは0件。既存state、metadata、volume、
  provision state、root / S3 / OAuth資格情報、JWT鍵、Trino passwordを流用していない。

この検証で再現したのは空のKubernetesからのShirokumaデータ基盤全体である。
Colima VM / macOS / 開発ツール自体の新規インストールは対象に含めない。
通常環境のColima再作成や他profileの操作は行っていない。
K3s containerの方式は[公式Docker実行手順](https://docs.k3s.io/advanced#running-k3s-in-docker)に基づく。

## 停止を伴うS3キーの更新

今回、SALESとFINANCEの両キーを1回のmaintenance windowで更新した。
OAuth、JWT、PostgreSQL、operator、GitOps readerの資格情報は更新対象から除いた。
署名鍵世代はSALES 2 / FINANCE 1を維持した。

1. 新規SQL / Job投入を止め、実行中処理を完了または明示停止する。
   rootはsuspendを維持し、7つのchildをsuspendする。
   Bucketも止める場合は、以降のsource upload / reconcile前にresumeする。
2. Trinoを0台にし、両DBのconsumer bootstrap Jobを停止する。
   今回はCompleted Jobを削除して再実行に備えた。
   両Polarisを0台にし、対象Podが存在しないことを確認する。PostgreSQLは維持する。
3. 現在の有効なprivate入力で対象DBのS3 pairを新規生成する。
   `seaweedfs_s3_credential_generation` とSeaweedFS Pod template annotationを同じ世代へ進める。
   今回は両方を `3` とした。annotationの変更はローカルsnapshotにも反映する。
4. 同じ有効stateに対するOpenTofu planを確認してapplyする。
   今回の変更はS3 server config SecretとDB別client Secret 2件の計3件のみ。
5. storage reconcilerを止めたままSeaweedFSを明示的に再起動し、Pod再作成とReadyを確認する。
   `currentRevision == updateRevision` も確認する。
6. **更新前から存在する同一object**で、旧キーの拒否と新キーの読取りを確認する。
   新キーの自prefix読書き、相手DBのGET / PUT / DELETE / LIST拒否、root LIST拒否も確認する。
7. 両PolarisがReadyになってから、現行templateによる新しいbootstrap Jobを実行する。
   bootstrap成功後にTrinoを起動する。Completed Jobのunsuspendだけでは再実行にならない。
8. 両DBのSQL / position-delete purgeを確認し、ローカルsnapshotを更新する。
   Bucket Readyを待ち、childを依存順に再開する。rootはsuspendのままにする。
   最後にOpenTofu差分0件とdoctor healthyを確認する。

実行時は手順5後にPolarisとTrinoを先に起動して既存Tableの読取りを確認し、
その後、依存順のFlux再開で新bootstrap Jobの成功を確認した。
上記の再実行手順は、恒久DAGに合わせてbootstrap → Trinoへ統一している。

| 更新後の確認 | 結果 |
| --- | --- |
| 旧SALES / FINANCEキー | 両方403 `InvalidAccessKeyId` |
| 新キーによる既存object読取り | 両DBで成功 |
| 更新前のIceberg rowとの照合 | 両DBで一致 |
| 自prefix操作と他DB拒否 | 14項目成功 |
| 新bootstrap Job / Pod | 両DBでComplete |
| position-delete purge | 両DBで成功、各11 objectから0件 |
| 最終S3 generation | SecretとSeaweedFS templateとも3 |
| GitOps reader / 通常環境 | Bucket / child 7件Ready、doctor healthy |

資格情報の変更時にS3 consumerが停止していることを確認した。
Secret更新だけでFileIOやS3 serverが新キーへ切り替わるとは扱わない。
この構成は共用SeaweedFS / Trinoの停止時間を伴う。新旧キーの併存や無停止更新は実装していない。

## 空のKubernetesからの構築順序

1. 独立K3sを新規volumeで起動し、Node Ready、arm64、local-path StorageClassを確認する。
   **Nodeが登録される前の `kubectl wait nodes --all` は対象なしで終了するため、存在確認も行う。**
2. 固定digestのFlux 4種、SeaweedFS、PostgreSQL、Polaris server / admin、Trino、OPAを用意する。
   今回は10 imageを新しいcontainerdへレジストリからdigest指定でpullした。
   Trino / OPAは `imagePullPolicy: Never` のため、先に正規名で存在確認する。
3. `deploy/gitops/clusters/local-lite/flux-system` のKustomize出力を使用して
   controller / CRD / RBAC / NetworkPolicyを導入する。
   `GitRepository` と元のroot Kustomizationは除き、Bucketを参照するsuspended rootを作る。
   `gotk-components.yaml` 単体applyではdigest pinのpatchが適用されない。
4. private入力を新規生成する。consumer client id / secretは初回未設定とし、
   **空の専用OpenTofu state**へ初回applyする。12 resourceを新規作成した。
5. `deploy/gitops/object-storage` の恒久renderを先に直接applyする。
   メモリはlocal GitOps childと同じrequest 1Gi / limit 8Giに合わせる。
   operatorでbucketの存在を確認し、欠けているものだけ作る。
   今回の空環境では `shirokuma-lakehouse` は既に一覧に現れ、`shirokuma-gitops` を新規作成した。
   `BucketAlreadyOwnedByYou` を未知の失敗としてbucketを作り直さない。
6. `local_gitops.py snapshot` → Bucket Ready → `activate --suspend` と進める。
   helperはbucketを作成しない。immutable snapshotの供給方法は[local GitOps guide](local-gitops-source.md)を参照。
7. dev → object-storage → catalog-database → catalog-bootstrap → catalogを順にresumeする。
   PostgreSQL schema、両realm、両PolarisのReadyを確認する。
8. **新しいprivate provision state**で専用principal / role / namespaceを作る。
   発行されたconsumer資格情報を入力へ反映し、第2回OpenTofu applyでSecret 4件を新規作成する。
9. `trino_auth.py init` で新しいTLS / passwordを作成し、`trino-local-auth` Secretを用意する。
   iceberg-bootstrap → trinoの順にresumeする。両bootstrapが新PodでCompleteとなり、TrinoもReadyとなった。
10. SQL / S3 / JWT / OPA / purgeの確認後、snapshot・provision・OpenTofuを再適用する。
    意図的なDeploymentのreplica driftもGitOpsで復旧できることを確認する。

fresh試験では `lab_lifecycle.py` / `colima_baseline.sh` を呼ばない。
これらは通常環境のprofileを直接起動・停止するため、別クラスタの初回構築には使用しない。
PostgreSQL Secretは `ignore_changes=[data]` であり、稼働後のpassword入力変更だけを
DB password rotationの成功とは扱わない。

## 再現性と機能の受入結果

| 検証 | 成功項目数 | 確認した内容 |
| --- | ---: | --- |
| SQL・利用者認可 | 28 | 両DBのtable / schema / view、FINANCEの非admin拒否、MARTS巻戻し拒否、admin巻戻し |
| S3 | 14 | 自prefixの操作、他DB直接アクセス拒否、root LIST拒否 |
| 固定JWT鍵 | 10 | 同じBearerでPod再起動後も成功、他realm token拒否、service / bootstrapの権限範囲 |
| OPA障害 | 5 | OPA taskをpauseすると3 roleとも拒否、resume後に成功 |
| position-delete purge | 4 | 両DBでdelete fileを生成し、DROP後に各11 objectから0件 |
| 構築・再適用・drift復旧 | 21 | 空namespace / PVC / state、全資格情報とPVCの新規性、snapshot再送、provision再適用、OpenTofu差分0、Flux復旧、doctor healthy |

機能検証は依存する操作を直列に実行する。途中でPolaris再起動試験とpurge試験を並行した際は
queryが失敗したため、再起動完了後にpurge用Tableの状態を確認して初期化し、単独で再検証した。
上表はこの前提を揃えた最終結果であり、実行中の再起動に対するquery継続は受入対象に含めない。

第2回apply後と最終再適用時のplanは変更0件。保存した変更なしplanのapplyも成功した。
2026-10-19 19:18:50 JSTの新環境doctorはhealthy。
FINANCEのPolarisを意図的に0台へ変更すると、catalog Kustomizationのreconcileで1台へ復旧し、
その後のSQLも成功した。新環境には外部GitRepositoryを作成していない。
通常環境と同じローカルsnapshotを供給し、child 7件をReadyへ再現した。

## 証跡と終了状態

- S3更新: `var/shirokuma/polaris-adoption/acceptance-s3-rotation.json`、
  `after-s3-rotation-runtime.json`、`flux-after-s3-rotation.json`、`doctor-after-s3-rotation.json`、
  `doctor-final-after-rebuild.json`。
- クリーン再構築: `var/shirokuma/polaris-rebuild-20261019/empty-cluster.json`、
  `initial-plan-summary.json`、`consumers-plan-summary.json`、`imported-images.json`、
  `acceptance-*.json`、`runtime-final.json`、`doctor-final.json`、`ended-state.json`。
- 両directoryは0700、private入力・state・plan・kubeconfig・資格情報は0600。
  結果ファイルと同じ場所にも秘密値を含むファイルがあるため、directory全体を公開しない。

通常環境は更新後のS3キーで稼働し、OpenTofu / GitOpsのdesired stateを維持している。
再構築環境は検証後にcontainerを停止し、CPU / メモリを解放した。
新規volumeとprivate証跡はローカルに保持している。
2026-10-19 19:21:35 JST、停止後の通常環境doctorもhealthyだった。
既存テストデータの移行・保全は行っていない。ローテーション検証で更新前のobjectを照合したのは
キー更新後のアクセスを証明するためであり、既存データの移行作業ではない。

再確認時は `rtk proxy docker --context colima-mac-studio-solo start shirokuma-polaris-rebuild-20261019` で起動し、
各kubectl / helper / doctorプロセスだけにprivate kubeconfigを指定する。
通常環境のkubeconfigや有効stateへこの検証環境の値を上書きしない。
