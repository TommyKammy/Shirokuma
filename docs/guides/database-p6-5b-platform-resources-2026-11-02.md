---
project: Shirokuma
doc_id: DB-P6-5B
title: Configured LOCAL container resources
status: accepted
created: 2026-11-02
updated: 2026-11-02
version: "1.0"
area: guides
tags: [database, compute, resources, platform, authorization]
---

# DB-P6-5B: 固定LOCALコンテナ設定資源

2026-11-02 JSTの凍結契約に基づく限定実装である。P5Bの限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,166成功・34 skip、両HTTP面の実API・Workbenchの実画面、最終保全PASS）。[P5A](database-p6-5a-compute-observations-2026-11-01.md)と[C2B](database-p6-4c2b-platform-observations-2026-11-01.md)の受入済み証拠は別単位として維持する。本書は限定範囲の技術受入を記録し、所有者本人の全体受入を代行しない。

## APIと現在認可

独立した`GET /api/v1/bears/platform-resources`を提供する。入力・body・queryはゼロで、DB／schema／grantを前提にしない。全source読取りの前後で、現在のaccountとprincipal双方の真のplatform管理権限、最大role admin、選択role adminを確認する。既存C2B API・閉じたDTO・3画面共有cache・既存requestのargv／回数／timeoutは変更しない。

閉じたDTOは`scope=platform`、`warehouse=LOCAL`、`selected_role=admin`、`observed_at`を持つ。coverageは`kind=deployment_container_resource_configuration`、`pod_template=true`、`per_container=true`、`live_usage=false`、`effective_pod_resources=false`、`capacity=false`である。resourcesは`availability=available|unavailable`、`code=null|RESOURCE_CONFIGURATION_UNAVAILABLE`、`observed_at`、`deployments=null|配列`とし、公開DTOはUTF-8で65,536 bytes以内とする。source取得失敗を認可成功や既存C2Bの失敗へ読み替えない。

## 固定sourceと投影

固定LOCALの`trino-local`と`trino-local-worker` DeploymentのPod template内コンテナ設定だけを読む。公開配列は厳密にcoordinator、workerの順とする。各Deploymentはordinaryと任意のinitを合わせて1〜16コンテナ、ordinaryは1件以上を要する。全ordinary・initを`kind=container|init`と種類内1始まりの`index`で示し、各kindのindexは重複のない連番とする。公開値にコンテナ名、namespace、image、env、command、replicas、合計を含めない。

各コンテナの`requests.cpu`、`requests.memory`、`limits.cpu`、`limits.memory`は互いに独立した非負Kubernetes quantityのraw文字列またはnullである。欠落はnullで画面に「未指定」と示し、明示zeroは元のzero表記を保つ。単位変換・正規化・float演算・合算・default補完・requestsとlimitsの大小比較を行わない。

quantityは最大64 bytesのASCII全体一致で、符号付き非負mantissaとdecimal／SI／binary SI／exponentの限定構文を検査し、綴りをliteralに保持する。外側の負符号・空白・末尾改行・単独Kを認めず、plus、zero、fraction、exponent、n／uは許す。これはbounded literal syntaxの検査であり、値の意味・policy・capacityやrangeの計算ではない。

sourceはread-only `audit.snapshot`と`wr._logical_foundation(db)`の下で、検証したnamespace・header対応を使う固定kubectl JSONPath GET 1回に限る。`D\tname\tnamespace` headerとC／I行・4 quantity cellを最大34行／16 KiBで取得し、request 3秒・全体8秒、stderrはDEVNULL、retryなし、timeout時はkillとwaitで終了を回収する。壊れた値・部分投影・不正な件数や対応はresources source全体をunavailableとし、部分値を公開しない。raw Kubernetes manifestやstderrを公開しない。

[Kubernetes公式のResource Management for Pods and Containers](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/)を設定fieldの一次参照とする。本投影はDeployment templateのcontainer requests／limitsに設定された値だけであり、live usage、利用可能容量、Pod-level設定、admission default適用後またはschedulerのeffective Pod budgetを表さない。Pod合計や資源解放・完全停止を導出せず、サイズ適用・resizing・live telemetry・policy監査の証拠にしない。

## Compute画面とcache

Compute専用のsibling `#observation-resources-panel`で明示操作によるGETだけを行い、独立P cacheに保持する。画面表示・navigation・language変更・認可refreshによる自動GETを行わず、DB context keyも追加しない。既存C2Bのrenderingとcacheは維持する。未指定とsource unavailableを区別し、JA／ENと390px表示を検証対象とする。

既存P認可helperと無条件purge hookを使い、現在の権限を失った場合はC2B cacheが空でも、非表示のresource DOMとcacheをすべて破棄する。遅れて戻る古い応答の書込みを防ぐ。同一権限でのnavigation／language変更ではsnapshotと下書きを保持でき、既存runtime／editor／UNKNOWN／Auditを妨げないことを確認対象とする。真の認可喪失時は既存security purgeが働き、editor状態や下書きの保持を保証しない。

## 検証証拠と限定技術受入

2026-11-02 JSTに限定技術受入とした。重複する重点・事前・全体回帰の件数は合算しない。

| 層 | 結果と適用範囲 |
|---|---|
| focused backend／UI | backend111件（新規資源18件を含む）、UI113件がそれぞれ成功。全Workbenchの事前回帰936件も成功。数量構文・null／zero・複数ordinary／init・上限・不正／部分source・timeout・process cleanupを確認。 |
| real API | Workbench8888／SQL endpoint8080の22ケースが成功（200が4、権限拒否403が10、不正入力400が8）。独立したKubernetes Go-template投影と新APIのcoordinator ordinary／init・worker設定が一致。両面で旧C2Bのstrict DTOも検証。 |
| role isolation | PのDB／grant不要、取得前後のcurrent account／principal・上限／選択role失効はfixtureで検証。実APIで非PのDB admin・E・VとPの選択lower roleを拒否。実画面はC2B cacheが空で資源cacheだけある状態でも、非表示中の降格で値を消去しボタンを無効化。復帰・認可更新時の自動取得なし。 |
| real browser | 新APIの明示GETは2回、旧C2B GETは0回。検証区間286 GET・mutation0・AI family呼出し0。旧共通runtime40 GETは別経路。nav／日英で完了snapshotの時刻を維持し、幅390pxでページ390／scroll390、資源card364／scroll364。viewportは1920へ戻し、console error／warnは0。 |
| draft／既存導線 | 同権限の取得で4入力・監査の未適用条件（ai／limit3／sql_proposal）・旧P5A表示を保持。未保存Auto-Chill15分も保持し、検証後は元の0分へ復帰。入力・query role・検証用の監査条件を元へ戻し、実行・保存ボタンは押していない。 |
| preservation | 最終保全PASS。active11,955行＋default156行、schema23、既存UNKNOWN、7ファイルの内容／mode、31 TABLE／33 VIEWを維持。新規行0。ownerの2 YAMLは内容／modeを保ちcommit対象外。 |
| canonical全体回帰 | 最終7 source／testと16無変更guardを固定し、99群・4,200件中4,166成功・34 skip・失敗0・終了コード0。skipの名称・理由は前回と同一。 |
| shirokuma doctor | 46確認中45 healthy・SQL probe未実施を示すinformational1、終了コード0。新しいEngine SQL実行の証拠ではない。 |
| design-context | 最終文書のdesign-context確認は2群14件成功・終了コード0。能力ID89件の順序、CMP-05以外の能力行、過去の受入結果、D1文書の元prefixを維持する。 |

境界ケース（未指定・timeout・0 replicas等）はfixtureで、実機では現在の構成・認可・画面・無変更を検証した。

## 次の範囲と残る境界

次の優先単位はDB-P6-5Cとする。既存AUD-03を再利用し、Compute専用の手動filter／cacheでquery／job_runのDB保存metadataだけを読む。V／Eは本人・保存roleと現在のDB／Schema権限、DBAは許可DB内に限り、P権限でDB閲覧を代用しない。SQL・結果・live／global activity・復旧・自動pollを追加せず、既存Auditのdraftを使い回さない。[全体計画](database-full-capability-and-compute-plan-2026-10-28.md)のP6-4全体／P6-5全体／P6-7／M1所有者本人受入はOPENを維持する。C2C／global policy、usage／capacity／activity、資源解放・resizing・size適用は独立した後続タスクとし、CMP-05の旧SRE check／Policy baseline routeを開放しない。能力の一括完了やowner2 YAML変更は含めない。


## P5Cの限定技術受入と認可の明確化

[DB-P6-5C](database-p6-5c-contract-2026-11-02.md)は既存AUD-03のquery／job_run保存metadataだけをComputeの独立した手動一覧・前後pageへ接続した限定技術受入単位である。P5Cの限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,188成功・34 skip、既存AUD-03の実APIとWorkbenchの実画面、最終保全PASS）。P5Bの上記受入範囲・証拠と、P専用の資源観測API／cacheは変更しない。

P5Cでいう既存AUD-03の現在認可は、現在のplatform_admin account・account role admin・選択adminに対するeffective DB admin例外も維持し、grant_revisionがnullとなる場合を含む。上記の次範囲にある「P権限でDB閲覧を代用しない」は新しい権限を付与しないという意味であり、既存例外を除くexplicit-grant-only制限ではない。明示READY DB・現在binding／generation・query可能Schemaを検査し、platform scopeのDB workload閲覧は作らない。SQL・結果・詳細・復旧・export・自動pollを追加せず、P6-4全体／P6-5全体／P6-7／M1本人受入は未完了を維持する。
