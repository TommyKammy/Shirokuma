# ADR-0038 通常DB作成の製品実装

2026-10-28。状態は**新経路の機能実装・技術検証済み、性能目標は未達、本人受入は未実施**。[ADR-0038](../design/07_ADR/ADR-0038_Separate_Logical_Database_Creation_from_Platform_Provisioning.md)と
[後継契約](database-logical-create-contract-2026-10-27.md)の順序3a→3b→3cを対象とする。
上記は順序3完了時点の記録。その後の[順序4の本人受入](database-logical-create-owner-acceptance-2026-10-28.md)は条件付き受入となり、
待ち時間の改善を継続条件として[性能改善](database-logical-create-performance-2026-10-28.md)を実施した。暫定性能目標は引き続き未達である。
所有者の指示で既存DB保持は必須とせず、専用のclean基盤と新しいDB IDで再構築できる。
旧UNKNOWN・承認・保存Job/Stageの参照を新DBの成功へ読み替えない。

## 実装単位

| 単位 | 成果と確認する境界 | 状態 |
| --- | --- | --- |
| 3a: 基盤 | 共有Polaris、Seaweed STS、永続dynamic catalog、固定pathの秘密publisher、動的password/ACL/OPA、通常再起動と期限更新 | 実装・実機確認済み |
| 3b: 対象DB | foundationとDB別revision、名前排他、永続intent、STOP/UNKNOWN/明示resume、現在認可と最終CAS、全SQL/DataOps入口のregistry参照 | 対象回帰・実機認可確認済み |
| 3c: CLI/WebUI | 同じ公開API、一画面の作成、自動進行、reload/deep link、対象SQL/Stage/Job/Mart、複数回計測 | 機能・実機確認済み、暫定SLO未達 |

実装ワーカーの変更ファイルを分け、Commanderが差分・対象試験・実機・ブラウザーを順に確認する。
先行実証のprivate helperを製品から呼ばない。公式digest固定imageと小さい独自SPIを用い、
汎用workflow、新しい常駐secret service、外部公開は追加しない。

## 今回固定する追加事項

共有TrinoのDB/role共通credentialは、個人の失効を他利用者から切り離せない。
新modeでは利用者・対象DB・選択role・grant epochに束縛したbackend identityを使い、
内部provisioner/observerの管理credentialを利用者SQLへ渡さない。既存modeは維持する。
foundationのprovenanceは既存generationの外部キーを保ち、DB binding/policy revisionは別に束縛する。

共有秘密の信頼境界にはcoordinator、workerと、同じruntimeディレクトリを読み取るOPAプロセスを含める。
S3管理者、Polaris issuer、PostgreSQL、JWT鍵のfoundation Secretは、必要なコンポーネントだけへ投影する。
利用者のDB別identityと、カタログ作成・内部観測専用のprovisionerを区別する。
Polarisの製品設定はSTS要求900秒・cache450秒とし、受入では実際に返された有効期限を基準に更新を検証する。

## 通常経路

`logical_v1` は稼働済みの共有基盤に対してDB別のcatalog、storage role/policy、bindingと
利用者別認証を追加する。CLIの `database creation create` とWorkbenchの「データベースを作成」は
同じAPIを使い、基盤のplan/applyを通常CREATEへ持ち込まない。
画面は名前と任意の初期アクセスを受け取り、CREATING→VERIFYING→READYを自動確認する。
「新しいSQLを開く」は利用可能なrawを優先し、許可されたread-only contextの新規worksheetを作る。
SQLを自動実行せず、既存worksheetの本文・実行先は書き換えない。

foundationの登録時とHTTPプロセス起動時には全体の実証・保存UIDへの束縛を行う。
通常受付は現在のaccount/gate、foundation/binding、実際のcompute状態とhealthを再確認する。
Kubernetes metadataは同じmTLS接続上のfresh GETで読み、exec/mutationの直前確認は維持する。
新DBの公開は対象catalog refだけを既存ファイルへmergeし、全computeで4ファイルの一致と
対象refの実SPI読取を確認する。既存のACK・UNKNOWNの保存payloadは変更しない。
共通password/ACL/OPAは現在の全identityから描画するため、DB数と利用者数に依存するCPU費用は残る。

## 実機・ブラウザーの証拠

新基盤Cは空のcontrol store・空の永続領域から構築した。初回適用と内部SQL/issuer検証は61.28秒。
これは管理者の基盤準備であり、通常DB作成の計測には含めない。
作業用の不成立基盤A/Bは当該namespaceと所有policyを退役し、BのrootをSTOPPEDとして保存した。
その発行ACK・失敗・退役記録をCの成功へ読み替えていない。

| 境界 | 確認済みの内容 |
| --- | --- |
| 通常のSQLとworker | 通常CLIのadmin-writeで1万行のIceberg tableを作成し、count=10000・sum=50005000を読取。専用workerの実入力45998 bytes/10000行、coordinatorのI/O担当なし |
| SQL構文 | 末尾セミコロン付きの通常CLI・ブラウザーSQLが成功。保存本文を保ち、backendへ送る単一文だけを正規化 |
| DataOps | Stage CSV→実table、JobのCTAS/read、Mart build/audit/publishが全てsucceeded。各出力2行の値が一致 |
| DB分離 | 2DBの同名tableがそれぞれ7/42を返し、別DBのSQL・consumer REST・双方向の実storage prefixが拒否された |
| 動的認可 | grant縮小/取消、account無効化/再有効化、role ceiling変更を実認証経路で確認。再有効化は新しいID/username/refを発行し、旧engine認証は拒否を維持。別利用者のSQLは継続 |
| 管理者変更 | 新しいplatform adminへ当時のREADY 14DBの個別identityを発行し、実データを読取。管理者権限を外すとAPI/旧engine認証を拒否し、STOPPED gateを保持。元の所有者のSQLは継続 |
| 期限更新 | 実vended sessionの期限後に旧credentialは403。同じbackend identityの実データSQLは45998 bytesを読み、新しい期限・credentialでS3読取200 |
| issuerのrole発行許可取消 | 対象roleのtrust取消後、新AssumeRoleは403。発行済sessionは期限内200、実期限900秒の後は403かつengine query FAILED。trust復元後は同じtableを読取、新sessionは200 |
| UI | 一画面の1回CREATEから自動READY、deep link/reload、raw優先の新規SQL、セミコロン付きSQL結果、閲覧者での管理非表示/実行拒否と本文保持、管理者復帰をChromeで確認 |
| 基盤の規模 | 通常CREATE・権限反映・DataOps後も同じ17資源のUID/generation/replicasを保持。DBごとの基盤追加・再配備なし |
| 通常再起動 | CLIで0/0へ休止し、作成拒否を確認後1/1へ起動。18DBのregistry、23ACTIVE identityのrefとcredential内容、共有4ファイルが一致。新workerで45998 bytesを読み、同名table・Stage/Job/Martのデータも保持 |
| 保存catalogの観測 | 18 catalog全てのruntime存在、永続catalog設定の一致、SPI refをread-onlyで確認。新規発行・再attachなし |

権限テストで見つかった2件も修正した。busyなroot lockで未claimのまま残ったAUTH_REFRESHは、
既存のowned loopが次の有限passで処理する。accountを再有効化する際は、ACTIVE identityが0件でも
現在のREADY DBと有効なdesired grantから対象を見つける。UNKNOWN/CLAIMEDを自動再送せず、
停止gateと失効済み資格情報を復活させない。修正後の実機21項目はPASS。

再起動確認では、Deployment readyだけでwakeが返るとengine参加前のAPI起動が失敗する競合を確認した。
保存証拠をscale前に検証し、wake/noopとも同じProviderによる実engineのstrict bindまで待機するよう修正した。
修正後はwake約20.05秒の直後にAPI/Workbenchを起動できた。warehouse起動の計測であり、通常CREATEへ加算しない。
catalog再照合は権限外のsystem table読取を使わず、許可済みの`SHOW CATALOGS`と
完全一致・保存設定・SPI refの検証を組み合わせる。内部provisionerの権限拡大は行っていない。

## 性能

作成開始から同contextの初SQL成功までを通常CLIで測る。ACKまでの時間をREADYとして扱わない。
初期の測定は標準回帰と併走し、最後に標準回帰を止めた条件でも再計測した。
いずれも少数標本であり、本番p95を保証しない。

| 条件 | 既存READY DB数 | 回数 | READY秒 | 初SQL成功秒 |
| --- | --- | --- | --- | --- |
| 最初の実装、明示admin grantで3identity | 0 | 1 | 26.96 | 27.81 |
| mTLS接続再利用、初期grantなし | 2–9 | 8 | 6.64–8.95 | 7.13–9.51 |
| 対象refだけの公開・読取、初期grantなし | 10–13 | 4 | 5.89–6.50 | 6.40–7.01 |
| 同じ経路、標準回帰終了後・初期grantなし | 14–17 | 4 | 6.23–6.40 | 6.81–6.98 |

各作成後も、既存DBの1万行を通常の利用者SQLで読めた。最終4回の初SQL中央値は6.82秒。
**暫定目標のp50≤2秒・p95≤5秒には未達**である。
基盤再配備と全catalog refの反復確認は通常経路から除いたが、権限ファイル描画・対象検証・
現在のhealth確認には費用が残る。初回基盤構築やwarehouse起動の時間とは分けて扱う。

## 回帰と範囲

標準回帰`make verify-local-experiment`は2770件（2737 PASS・33 skip）、exit 0。
skipはOPA/pyarrowの任意依存と、従来の専用lab/calendar/export等のopt-in試験であり、
その全てを新基盤の実証で置き換えたとは扱わない。新経路の実SQL/Stage/Job/Mart、
認可、期限・role取消、ブラウザーは前述の別の実機証拠で確認した。
標準回帰後の2箇所の修正は、起動完了判定77件とcatalog読取126件で再確認した。
他の製品sourceは標準回帰開始時の119ファイルの保存内容と一致し、後続変更はこの2ファイルのみである。
Go test/vet、設計context・改行・末尾空白・秘密filenameの検査も成功した。
再起動後の実Podログと公開DTO等を再捕捉し、774ファイル/3,511,992 bytesを照合した時点で
収集した秘密値の一致は0件、読取エラーも0件。private credential/ACKは秘密保持対象として区別した。
順序4の所有者本人による1回の作成→SQLの受入は独立して残す。
初期範囲は単一K3s node・共有local RWO PVC・coordinator 1/worker 1である。
複数K3s node、HA、内部TLS、issuer credential自体のrotation、全面DRを今回の保証に含めない。
role trustの取消は発行済みSTSを即時無効化しないため、残存期間の上限を900秒として扱う。
compute休止中のHTTP新規起動は対象外で、管理者がwarehouseを起動してからAPI/Workbenchを起動する。

証拠はworkspace内の`var/shirokuma/adr-0038-implementation/private`に0600で保存し、
credential/ACKと公開DTO・測定結果を区別した。資格情報そのものはこの文書へ転記しない。
順序3では検証用runtime profileを明示して新CLI/API/Workbenchを起動した。
2026-10-28の追加指示による[順序4の切替・本人受入](database-logical-create-owner-acceptance-2026-10-28.md)で
有効なcheckoutの既定profileを新経路へ切り替え、通常CLI/API/Workbenchで再確認した。
本人の操作・使い勝手の判断は同記録へ分けて残す。

開始時は専用VMとWorkbenchが停止していた。保存済み設定でVMを起動し、旧InternalIPとeth0の
不一致で停止していたK3sを、UID・resourceVersion・旧IP条件付きのNode status更新だけで復旧した。
既存control70表・13DBはprivate backupへ保存し、新方式の導入とは分けて記録する。
Go全件検査がignored領域の古いsource snapshotやmodule cacheまで走査したため、`var/go.mod`と`tmp/go.mod`のlocal module境界で
生成物を除外した。旧snapshot・失敗log・DBデータはこの対応で削除していない。

新基盤は`shirokuma-logical` namespace、空のcontrol storeとruntime profile v2で構成する。
従来の`local-experiment doctor`は旧namespace・Flux inventoryを検査するため、新基盤の受入結果には使わない。
foundation適用、通常CLI/API、実SQL、認可、再起動、ブラウザーの結果を別々に記録する。
