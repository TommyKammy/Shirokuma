# ADR-0038 DB作成方式の実現性実験（2026-10-27）

状態: **順序1の有界実証を完了。共有方式を次の実装対象に選定し、順序2の後継契約案を作成した。製品実装・性能目標・本人受入は未完了**。
[ADR-0038](../design/07_ADR/ADR-0038_Separate_Logical_Database_Creation_from_Platform_Provisioning.md)
の順序に従い、共有catalog＋短期資格情報と、専用サービスの事前準備を比較する。

## 対象と実施方法

- 専用Colima上の独立Docker bridge／volume／loopback portで実施し、秘密公開だけはK3sの専用Pod/PVCでも確認した。通常WorkbenchのDB作成要求は発行していない。
- Polaris 1.8.0、Trino 483、PostgreSQL 16.4、SeaweedFS 4.51のARM64 imageをdigest固定で使用。現基盤のSeaweedFS 4.39とは別であり、その更新済みとはしない。
- 実装workerが有界な実験helperを作成し、Commanderがsource・独立レビュー・実サービスの結果を確認した。helperはprivate実験領域だけに置き、製品から呼ばない。
- 所有者の追加指示により、既存DBの保持・移行互換性は必須条件から外した。再構築の負担が小さい実験では専用DBを破棄して再作成し、元の失敗証拠を残す。

## 事前準備した専用サービスからのhot catalog追加

2つの専用Polaris／prefix限定キーを事前準備し、起動済みTrinoへcatalogを動的追加した。
同名のIceberg tableへ異なる11行を書き込み、値・実ファイル・metadata identityを照合した。

| 観測 | DB A | DB B |
| --- | ---: | ---: |
| catalogのhot登録 | 1.4198秒 | 0.4263秒 |
| raw schema作成 | 0.4495秒 | 0.4232秒 |
| 最初のcontext SQL | 0.5510秒 | 0.6001秒 |
| 上記合計 | 2.4203秒 | 1.4496秒 |
| table作成・書込み・読取り | 4.3544秒 | 3.1455秒 |

各1件のprototype観測であり、プールの初回準備時間、製品の4schema・初期アクセス・台帳確定を
含まない。p50／p95達成とは扱わない。Bのhot登録でAが使え、container再起動はなかった。

初回helperはforeign principal拒否のエラー名を`invalid_client`だけに限定し、実際の
401／`unauthorized_client`をFAILとした。元のFAILを残し、構造化エラーを正しく判定する
sourceで既存データの境界を再確認した。再起動前後それぞれ30項目が通り、同じcontainer ID、
table UUID／snapshot／実データとSQL・S3・realmの分離を確認した。
([元の観測](../../var/shirokuma/adr-0038-spike/private/pool-v1/probe-summary.json)、
[独立した再起動観測](../../var/shirokuma/adr-0038-spike/private/pool-v1/functional-restart-observation.json))

## SeaweedFSの短期資格情報と動的IAM

非Adminのissuerと、異なるprefixを許可する2つのroleを使用した。role／policyは実APIで追加し、
サーバーを再起動せずに両方で実objectのput/get/listが成功した。

| 実証 | 結果 | 限界 |
| --- | --- | --- |
| 動的role追加、trust／policy readback | 8項目成功 | metadataのscalar／list表記差を意味を保って正規化 |
| prefix外get/list/put/delete/copy、未信頼issuer、session policyの狭窄・拡張要求 | 40項目成功 | mockや存在しないobjectの404を拒否証拠にしない |
| 通常再起動、trust／policy保持、旧／新session、範囲外拒否 | 別instanceで65項目成功 | GETで既存markerが読めるまで復旧を待つ |
| 期限を過ぎた旧session | A/Bとも403、未失効のcontrol sessionは同じobjectを読取り成功 | 期限前の正常読取と期限後の拒否を別時刻で観測 |
| ExternalIdによる条件付きtrust | 未実証 | 現版handlerに対応の根拠を確認できず、本方式の成立条件に含めない |

[初回40項目](../../var/shirokuma/adr-0038-spike/private/sts-v1/seaweed_4_51-growth16/1793094850837828000-probe.summary.json)、
[新instanceの40項目](../../var/shirokuma/adr-0038-spike/private/sts-v1/seaweed_4_51-settled/1793095682820313000-probe.summary.json)、
[65項目の再起動](../../var/shirokuma/adr-0038-spike/private/sts-v1/seaweed_4_51-settled/1793095839910850000-restart-check.summary.json)、
[期限の観測](../../var/shirokuma/adr-0038-spike/private/sts-v1/seaweed_4_51-growth16/expiration-observation.json)。
重なる検査を合算して受入件数にはしない。

最初の小さいvolume上限ではfiler内部領域等に枠を使い、実objectのPUTが失敗した。
また最初の再起動試験ではlist APIだけでreadyと判断して次のPUTがタイムアウトした。
元instanceのpendingを保全し、上限16の別instanceと既存markerのGETを含むreadinessで検証した。
後に元PUT固有のkeyをGETし、200・元のmarker本文の完全一致を観測した。その1操作だけを
`OBSERVED_APPLIED`として照合し、再送していない。元の途中終了した再起動試験はPASSへ変更していない。
([正の観測と照合](../../var/shirokuma/adr-0038-spike/private/sts-v1/seaweed_4_51-growth16/pending-reconciliation-summary.json))

## 起動後に追加した秘密参照

Airlift 439の公式SPIを使う83行のfile secret-providerを、公式Trino imageへ別jarとしてmountした。
上流class／imageの置換はない。空の秘密fileと空catalog storeから起動した後、A/Bの新参照IDを追加し、
DB別環境変数なしでhot CREATE、実table書込み／読取り、欠落参照の拒否を確認した。
再起動後もA/BのUUID／snapshot／実データが一致し、Polarisは再起動しなかった。
([機能・再起動観測](../../var/shirokuma/adr-0038-spike/private/file-secrets-v1/runtime-functional-restart-observation.json))

21 queryの全文とQueryInfo JSONに既知の生の秘密値はなかった。一方、静的S3キーを渡す比較方式では
TrinoのINFO設定ログへA/Bのaccess-key IDが各1件出た。secret key／OAuth secretの一致ではないが、
識別子も含める厳格な照合はFAILのまま残す。共有＋credential vending方式では静的S3キー自体を
catalogへ渡さず、この経路も含めて検証する。
([元のscan結果](../../var/shirokuma/adr-0038-spike/private/file-secrets-v1/runtime-run-summary.json))

provider単体は複数propertyの同時世代一致、既存catalogのrotation、HAを保証しない。
新規参照をimmutableに保存してからCREATEする条件でのprototype実証である。

## 共有Polarisとの統合と互換性

最初のcatalog作成は、Seaweed dynamic CreateRoleが返す`arn:aws:iam::role/<name>`を
PolarisがAWS標準ARNとして受け付けず、HTTP400で拒否された。A/Bの不在をGET404で確認し、
当該試験だけのDB・container・空catalog storeを破棄して、stateと失敗記録を`attempt1`へ残した。

同じroleに対して`arn:aws:iam::000000000000:role/<name>`を使う直接AssumeRole試験では、
自prefixの読取成功と他prefixの403を確認した。上流のARN解析も同じrole名・RoleIdへ解決する。
返却形式を無条件に信用せず、既知account／role名と元ARNを照合する限定補正を統合実験へ入れる。
([直接試験](../../var/shirokuma/adr-0038-spike/private/sts-v1/seaweed_4_51-growth16/canonical-arn-observation.json))

次の試行ではDocker名のunderscoreを含むendpointがJavaのURI hostとして認識されず、
内部FileIOが500になった。所有を確認した専用networkのIPv4へendpointを揃え、IP不一致なら
停止する条件で再試験した。製品ではDNS互換の固定Service名を使い、この実験用IPを転用しない。

INFO設定の統合試行は実データと分離を通したが、Polarisログにconsumer IDが4件残った。
元のFAILを`attempt3`へ残し、通常の`QUARKUS_LOG_LEVEL=WARN`で新しい試行を実施した。
secret値だけにscan対象を狭めてPASSへ変更していない。

### 共有サービスの縦断結果

単一Polarisプロセス・単一realmに2つのcatalogを追加し、別consumer・STS role・prefixと
`raw/staging/marts/sandbox`を作成した。同名tableへ異なる13行を書き、実Parquet fileと
table UUID／snapshotを照合した。Polarisのissuer自体にはobjectの直接get/put権限を与えず、
DB別に払い出された短期資格情報でのみ内部FileIOを行う。

| 観測 | 結果 |
| --- | --- |
| 作成・実データ・SQL/REST/storage相互拒否・既存A利用継続 | 67項目成功、通常作成中のcontainer再起動0 |
| 通常再起動後の実データ・metadata identity・権限境界・秘密参照の永続化 | 54項目成功、同じcontainer IDで新しい起動時刻 |
| 完全なquery履歴／QueryInfo、既知の資格情報・識別子を含むログ照合 | 作成時39件、再起動後17件のQueryInfo欠落0・一致0、ログ一致0 |

[共有縦断](../../var/shirokuma/adr-0038-spike/private/shared-v1/probe-summary.json)、
[共有再起動](../../var/shirokuma/adr-0038-spike/private/shared-v1/restart-check-summary.json)。
HTTP受付やcatalog一覧だけの検証ではない。比較方式のINFOログFAILとは別の試行である。

| 稼働済み共有基盤上の処理 | DB A | DB B |
| --- | ---: | ---: |
| catalog／consumer／権限／4schema | 2.7479秒 | 4.0200秒 |
| Trino hot登録 | 1.3773秒 | 0.4313秒 |
| 最初のcontext SQL | 0.5732秒 | 0.5836秒 |
| 上記合計 | 4.7003秒 | 5.0360秒 |
| 実table作成・書込み | 2.2248秒 | 1.1212秒 |
| 実データ読取り | 1.9330秒 | 1.7418秒 |

BにはAの利用継続を確かめるSQLも含む。Python driverの起動・通信費を含み、IAM roleは前段で
準備済みである。製品の受付／台帳確定・新規accountの認証反映は含まない。
共有PolarisとTrinoの準備10.9382秒も、起動済みPostgreSQL/S3とcached imageを使った部分値である。
2件からpercentileや旧方式に対する性能倍率を算出しない。

## 起動済みworkerへの新catalog伝播

coordinatorを計算対象から外し、同じ公式imageのworkerを起動した。その後に新しいCのIAM role、
catalog、consumer、4schema、秘密参照を追加し、実Parquet tableへ異なる37行を書いて読んだ。
QueryInfoのtaskでは既知workerが37 positions／1477Bを読み、coordinatorの物理入力は0だった。
単なるnode登録やcatalog表示を伝播成功と扱っていない。

84項目が成功し、通常のC作成中にcoordinator／worker／Polaris／STSは再起動しなかった。
作成時23件・復元後7件の完全QueryInfoを取得し、履歴・両Trinoログの秘密一致は0だった。
試験後にworkerを停止しcoordinatorの設定を元へ戻して、A/B/Cの実データを再読取した。
([worker縦断](../../var/shirokuma/adr-0038-spike/private/worker-v1/run-summary.json))

基盤準備6.3616秒・worker起動17.4442秒・復元12.6766秒は通常CREATEと分けた。
Cのrole作成から実table書込み・全検査まで12.1574秒は性能比較用の同一測定区間ではない。
独立した2つのsecret fileへ先にimmutable参照を公開した試験であり、分散atomic更新ではない。
内部通信は共有secret付きHTTPで、内部TLS、worker障害、HA、rotationは未実証である。

## 方式比較と順序2への条件

| 候補 | 実証できたこと | 採否と負担 |
| --- | --- | --- |
| 専用サービスの事前準備 | hot登録、実データ、prefix／realm分離、再起動保持 | 比較候補。DB数に応じたPolaris／PostgreSQL DB／鍵とpool再充填が必要。静的S3 key IDのINFO露出も残った |
| 共有Polaris＋STS vending＋動的秘密参照 | 共有プロセス内のcatalog別SQL/REST/storage境界、実FileIO、再起動、起動後workerへの伝播、WARN設定で非露出 | **次の実装対象に選定**。通常CREATEから専用サービス起動を外せる。STS4.51導入と限定publisher、共有基盤を信頼する境界が必要 |

同時刻のidle snapshotは共有Polaris 256.9MiB、専用A/B 231.3／224.8MiBだった。
各container上限は1GiB。履歴・負荷が違う1回の観測で、性能・容量保証ではない。
poolの常駐サービスがDB数とともに増える構造を評価し、共有方式を有力とする。
([資源観測](../../var/shirokuma/adr-0038-spike/private/catalog-resource-observation.json))

既存DBを保持するadapterは初期範囲に入れず、clean基盤上での再構築を基本案とする。
今のprimary DBをこの実験で削除する必要は生じていない。
順序2は[後継契約案](database-logical-create-contract-2026-10-27.md)へ進める。
native PVC公開と、製品の認証／ACL／OPAの動的更新は別の証拠として扱う。
試作A/Bは起動前user、Cは既存operatorを使ったため、現在の利用者への新DB権限反映を
実証済みとしない。製品の通常CREATE・CLI/WebUI・p50/p95・本人受入は未完了である。
STS単体の期限切れ拒否と新sessionの正常利用は確認したが、実Trino/Polarisでの長時間稼働中の
session自動更新・issuer/role失効時の発行済sessionの扱いは未実証で、順序3aのgateとする。

## native PVC上の限定publisher

既存K3sのnamespaceに専用100Mi local-path/RWO PVCとwriter／reader Podだけを作成した。
公式Trino image内のJDKと実file secret-providerを使い、両PodはUID10000・非root・RO rootfs・
service account tokenなし、同nodeのwhole-directory mountである。writerはRW、readerはROとした。
ConfigMapは自作classだけの小さいjarで、実資格情報を使わずprivateなrandom markerで試した。

23項目が成功した。新しい参照の即時読取、同値の冪等公開、2つの並行公開での既存値保持、
異値上書き拒否、readerでの正常読取後の書込み拒否、writer Pod再作成後の4参照保持を確認した。
公開・各readの間に可視化pollやPod再起動を挟んでいない。専用のエラーcodeとsentinelを要求し、
通信失敗を拒否成功に数えていない。

公開round-trip 484.5ms、writer読取まで累計894.0ms、reader読取まで累計1339.2msだった。
kubectl execと所有照合を含む1回の値で、DB作成SLOではない。
[実行結果](../../var/shirokuma/adr-0038-spike/private/pvc-v1/run.log)、
[Commanderのnative照合](../../var/shirokuma/adr-0038-spike/private/pvc-v1/commander-native-observation.json)。

この方式を順序3aの実装候補とする。既存private executorから固定publisherへstdinで公開でき、
DB追加ごとのSecret投影・deployment変更・新しい常駐secret serviceを必要としない。
`pods/exec`権限はargvを制限しないため、private executorを信頼する境界と対象制限は後継契約へ明記した。
実SPIでの複数Pod読取と、前節の実Trino worker I/Oは別の試験であり、native新DB縦断済みとはしない。
native初期範囲は同一namespace・単一K3s nodeのlocal RWO PVC共有であり、別K3s nodeへの伝播ではない。
共有Trino process／管理主体は全DB consumer秘密を読める信頼主体とし、利用者のDB別認可と区別する。

## 終了時の範囲確認

専用PVC試験のPod2件・PVC・ConfigMapを所有UID照合付きで削除した。Docker側の稼働中の専用9サービスは
依存順に停止し、volume／network／元の失敗記録と実データはprivate領域に保持した。稼働中の
`adr0038` containerは0である。primaryのDBを削除・再作成していない。

primaryの36 Podは試験前後でUID・状態・restart countが一致し、canonical70表の件数と
13DBの記録済みstate/revisionも一致した。これは記録した列の照合で、全行全columnの証明ではない。
([最終照合](../../var/shirokuma/adr-0038-spike/private/primary-final-observation.json))
`shirokuma doctor`はexit0だがstatusはdegradedで、旧固定名のPolaris項目が現generation構成に
一致しない項目を含む。全doctor greenや新方式の製品受入としない。

## 最終検証と変更範囲

標準`verify-local-experiment`は、最初のsystem PythonでPyYAML不足、既存DataOps venvを指定した
再実行では日本語切替のテストharness1件で停止した。実際の案内文定数を抽出していないことが原因で、
`tests/test_workbench.py`の抽出式だけを最小修正した。assertionと製品HTML/serverは変更していない。

元の成功71群、修正後のWorkbench全431件、未実行だった残り4群を確認し、標準の全76群を完了した。
重複再実行を除く2413宣言＝2380成功＋既存skip33。単一makeの成功ではなく、元の失敗と修正後の
対象検証を分けた記録である。設計検査14件・context文書94件、文書のlocal linkと空白検査も成功した。
([検証索引](../../var/shirokuma/adr-0038-spike/private/final-verification-summary.json))
標準検査後にもPod・表件数・DB state/revisionの一致を[再照合](../../var/shirokuma/adr-0038-spike/private/primary-post-tests-observation.json)した。

変更は設計・受入記録、テストharness1箇所、ignored/private領域の有界な試作に限定した。
新しい製品API／作成経路の実装、primaryのDB破棄、commit／外部公開は行っていない。
