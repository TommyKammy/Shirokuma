---
project: Shirokuma
doc_id: DB-P6-5E
title: Observed enrolled node capacity and available resources
status: accepted
created: 2026-11-03
updated: 2026-11-03
version: "1.0"
area: guides
tags: [database, compute, capacity, platform, authorization]
---

# DB-P6-5E: 登録Nodeの容量・利用可能量

2026-11-03 JSTに限定技術受入を完了した。[P5Dのコンテナ実使用量](database-p6-5d-platform-usage-2026-11-03.md)に続き、LOCALを収容する登録Nodeの容量と、直接報告される利用可能量をComputeから手動観測する。設定requests／limits、実使用量、容量・利用可能量の意味を分け、DB-P6-5全体・CMP-05全体・M1本人受入は継続する。

## 範囲と値の意味

対象は現在のfoundationがnameとUIDで固定した単一のKubernetes Nodeであり、共有VMの観測である。Macホスト全体やDB専用割当、伸縮時に安全に追加できる予算を表さない。休止中もNodeとDeployment templateの配置を確認できれば利用でき、現在のRunning PodやSQLを前提にしない。

| 表示 | 直接参照する値と意味 |
| --- | --- |
| CPU／メモリ容量 | exact Nodeの`status.capacity`。CPU coreとmemory bytesへ正確に正規化する。 |
| CPU／メモリallocatable | exact Nodeの`status.allocatable`。Pod用の設定上の予算であり、現在の空き・残りrequests・実行許可を示さない。使用量やrequestsを差し引かない。 |
| メモリ利用可能推定 | kubelet summaryの`node.memory.availableBytes`。kubeletが報告するlimit−working setの推定値であり、OSのfree値やスケジューリング余力へ読み替えない。 |
| Node rootFS | summaryの`node.fs`に報告されるcapacity／available。NodeのKubernetes componentが利用するrootfsの観測であり、データ保存先PVCやS3のquotaではない。 |
| ImageFS／ContainerFS | 同じsummaryの`node.runtime.imageFs`／`containerFs`を、それぞれ独立した任意の観測として表示する。省略・不正・期限切れは確認不可とする。Node rootFSと異なる場合や、相互に下層FSを共有する場合があり、値の一致だけで同じFSと判定せず、合算しない。 |

Filesystemのused値を表示する場合も、sourceの報告値を独立に扱う。`capacity−used`からavailableを作らず、`used+available=capacity`を要求しない。PVCの宣言容量から空きを作らない。CPUの空きcore、未予約量、入場許可、実サイズ変更・Worker増減は追加しない。

一次参照は[Kubernetes v1.33のNode Allocatable](https://v1-33.docs.kubernetes.io/docs/tasks/administer-cluster/reserve-compute-resources/)、[v1.33のnode pressure指標](https://v1-33.docs.kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/)、[Kubelet Summary API](https://kubernetes.io/docs/reference/kubelet-api/stats.v1alpha1/)、[v1.33.4のstats型定義](https://github.com/kubernetes/kubernetes/blob/v1.33.4/staging/src/k8s.io/kubelet/pkg/apis/stats/v1alpha1/types.go)である。

## API・現在認可・sourceの対応

独立した`GET /api/v1/bears/platform-capacity`を提供する。入力・body・queryを受け付けず、現在のaccountとprincipal双方の真P、最大role admin、選択role adminを各外部読取り前と返却前に検査する。DB／schema／grantを条件にせず、非Pの自己申告やNULL grantから権限を推定しない。

閉じたDTOは`scope`、`warehouse`、`selected_role`、`observed_at`、`coverage`、`availability`、`code`、`core`、`image_fs`、`container_fs`からなる。`scope=platform`、`warehouse=LOCAL`、`selected_role=admin`を固定する。coreはCPU capacity／allocatable、memory capacity／allocatable／availableとsample時刻、Node収集start時刻、rootFSの固定fieldだけを持つ。各FSはcapacity／available／任意のusedとsample時刻だけを持つ。Node名・UID・bootID・filesystem path・PodやDB名・raw errorを返さない。

必須coreの取得不能・不正・不一致では`availability=unavailable`、`code=CAPACITY_UNAVAILABLE`、core／image_fs／container_fsをすべてnullにする。成功は`availability=available`、code=nullとcoreを持つ。ImageFS／ContainerFSは独立した任意sourceなので、一方の欠落・不正・期限切れはそのfieldだけnullとし、画面に確認不可を表示する。実測availableのzeroは維持する。

Bytesは非負uint64の正規化した整数文字列、CPUはboundedな正規decimal文字列とする。allocatableがcapacityを超える値を拒否する。メモリavailableが登録Nodeのmemory capacityを超える報告も、本観測の保守的な整合条件として確認不可にする。これはKubernetes全般の数式を主張するものではなく、availableの導出やworking setとの等式には用いない。FSのavailableも当該FSのcapacity以内とする。

現在のfoundationを純粋なmetadata読取りで取得し、固定2 Deploymentのfoundation labelとhostnameの配置、固定Nodeのname／UID、削除・boot情報を対応付ける。Deployment・Nodeをsummaryの前後に読み、対応するsourceとfoundationが変わった結果を破棄する。無関係なheartbeatやresourceVersionの変化だけで不一致にしない。

Summaryはexact Nodeのproxy endpointを1回だけ読み、`summary.node`内の固定fieldだけを投影する。Pod／PVC／volume／network／process一覧やNodeMetricsへfanoutしない。登録Node以外の探索やfallback、exec／SSH、SQL、engine health probe、`runtime.status`の修復、Wake／Chill、admission、control store書込みを行わない。

外部GETは固定2 Deployment→exact Node→exact Node summary→Node→Deploymentの5回とし、retryしない。全体deadline 15秒、各kubectl request 2秒、各stdout 1 MiB、公開DTO 32 KiBを上限とする。stderrを公開せず、timeout後のsubprocessを回収する。

Node UID・bootIDとsource start／各sample時刻を検査する。summaryのstartTimeは収集開始・再開時刻であり、Node API objectの作成より前でもよい。sampleがsource startより前、古過ぎる、未来、別Nodeの場合を拒否し、取得不能・欠落をzeroへ置換しない。これは前後のsource照合による観測であり、異なるAPI間の完全なatomic snapshotを主張しない。

UTC Z timestampは最大9桁のfractionを扱い、sample ageは120秒以内、future toleranceは5秒とする。source startにも5秒のfuture toleranceを適用する。Nodeの無関係なheartbeatだけで観測を失敗させず、現在のNode UID・boot・選択した容量値・template配置の対応を比較する。

## 通常画面と受入

ComputeにP専用の独立した手動取得panelを設ける。最初は未取得とし、画面表示・移動・DB切替・locale変更・認可refreshだけでは観測GETを送らない。現在のP権限を失った場合はcacheを破棄し、遅着応答やABAで古い記録を復活させない。P5A／P5B／P5D／C2C1と既存activityのAPI・DTO・cacheを維持する。

値は正確なdecimal／整数文字列として検査し、bytesはBigIntによるGiBの読みやすい表示と正確なbytesを併記する。取得時刻、メモリ・FSのsample時刻、取得不能を表示する。日本語／英語、390px、実200%を検証する。

準備時点で、登録NodeのCPUは16 core、capacity／allocatable memoryは98,678,176 Ki、メモリavailable推定は90,159,845,376 bytesだった。Node rootFSはcapacity 19,682,557,952／available 17,296,388,096 bytesで、ImageFS／ContainerFSはそれと異なる大きさだった。値は当時のsource事実であり、固定値や現在値へ読み替えない。既存APIの読取りだけで取得でき、新しい基盤導入・権限変更は行っていない。

コード・認可、実API、ブラウザー、保全を別々に確認した。private証拠は`var/shirokuma/db-p6-5e-platform-capacity-2026-11-03/private/`に保存し、code testの成功だけで通常利用やM1本人受入を代行しない。

| 検証層 | 2026-11-03 JSTの証拠 |
| --- | --- |
| 実装・独立レビュー | ADR-0036のCLI workerで7ファイルを実装。Node pin／UID／boot・配置・foundationの前後照合、休止を模した0 replica、source startとsampleの意味、任意FSの独立性、現在P認可と読取り専用を別担当がレビューした。 |
| focused | 修正後backend 12件・UI 11件成功。mem available上限とzero／等値／2^53超／uint64最大を検査し、不正sourceをall-nullまたは任意FSだけnullにする。初回の重点・近接128件も成功。ブラウザーで日本語見出し1値だけを整え、UI 11件が再成功した。既存の未対応panel判定には独立capacity panelを追加した。 |
| canonical | 116群4,697件を完走し、4,663成功・34 skip・失敗0（1,002.3秒）。backend12／新UI11と、見出し修正後のWorkbench全1,103件も成功した。 |
| 実HTTP | 通常8888／native8080で34 GETの期待statusを確認（200×2、400×14、401×6、403×12）。両面のcore・image_fs・container_fsはavailableで、閉DTO・正確な整数／decimal・容量整合・sample鮮度を確認。現在Pの選択V／E、非Pのadmin／P自己申告、未知principal、不正framing／入力を拒否。認可adapter 9件もcanonical P NULL成功と8拒否を確認した。 |
| 実測 | CPU capacity／allocatableは16 core、memory capacity／allocatableは101,046,452,224 bytes。8888のmemory availableは90,167,726,080 bytes、rootFS availableは17,296,105,472 bytes（sample22:47:10Z）。任意の2FSは独立に170,890,063,872 bytes available（sample22:46:50Z）を報告した。別port・ブラウザーで後続sampleを新たに取得し、値を固定しないことを確認した。 |
| 通常ブラウザー | 未取得→明示取得→日英で同じsnapshotを表示→見出し修正のstatic再読込み→明示取得を確認（手動GET合計2）。日英390pxはpanel364／scroll364、最終日本語見出しも同じ幅で折返した。実200%はDPR2／viewport960／panel742／scroll742で日英とも成立。対象panelの表示を検証し、アプリ全体の既存overflow解消は本単位の成果にしない。 |
| 証拠画面・復元 | `compute-capacity-ja.png`はpanel全体を収めるため1920×1200の一時viewportで保存し、overrideを解除した。日本語・実100%（DPR1／1920px）、元19 Worksheet（Detailsを含む20tab）と選択Worksheet／SQL／A／Marts／admin／read-onlyを保持し、検証用タブを閉じた。 |
| 保全 | API・ブラウザー後もactive12,234／default156行の全行・schema、12保護ファイルとmodeに差分0。UNKNOWN query3／admission3／AI6／dbt1、実行中0を維持。通常serverのargv／cwd／loopback／stable TMPDIRも維持した。 |

API34 GET＋ブラウザー手動2 GETは合計36件。8888 access logの19件はAPI17＋ブラウザー2と一致した。8080はaccess logを出力しないため、個別HTTP receiptで17件を証明する。通常serverは同一profile・argv・cwd・env・stable TMPDIRで限定再起動した。追加engine SQL／SDK／engine health probeは0であり、通常startupの既存read-only health SQL回数は別枠の未計測として総SQL 0とは区別する。

詳細はprivateの`http-acceptance-summary.json`、`authority-adapter-summary.json`、`final-comparison-summary.json`、`browser-*.json`／`browser-*.txt`と各test logに保存する。故障・stale・boot／Node不一致・任意FS欠落・権限途中失効・休止中の取得は隔離テストで検証し、実環境でNode再起動・Chill・口座変更を起こしていない。

## 後続

起動・停止履歴と停止理由、現在の操作権限の説明を既存対応と照合し、残件を限定単位で進める。Bears／Policy残件、89行の全体照合、DB-P6-7／M1本人受入は継続する。DB-P7の実伸縮とホスト／VMの安全な割当予算の採用はM1後に扱う。
