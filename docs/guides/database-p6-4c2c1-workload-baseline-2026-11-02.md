---
project: Shirokuma
doc_id: DB-P6-4C2C1
title: Fixed LOCAL workload declaration baseline review
status: accepted
created: 2026-11-02
updated: 2026-11-02
version: "1.0"
area: guides
tags: [database, bears, platform, workload, baseline, authorization]
---

# DB-P6-4C2C1: 固定LOCALのworkload宣言基準レビュー

凍結契約に基づく限定実装と技術受入の記録であり、C2C1の限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,232成功・34 skip、両HTTP面の実APIとWorkbenchの3面表示、最終保全PASS）。[C2B](database-p6-4c2b-platform-observations-2026-11-01.md)・[P5A](database-p6-5a-compute-observations-2026-11-01.md)・[P5B](database-p6-5b-platform-resources-2026-11-02.md)・[P5C](database-p6-5c-contract-2026-11-02.md)の各受入済み範囲と証拠は別に保持する。本IDはnative graphのDB-P6-3C2Cとは異なる。

C2C1をlive usageやAI設定書込みより先に進めたのは、今回のCommanderによる優先順位の選択である。既存ロードマップが全後続単位の総順序を確定していたとは扱わない。

## 対象とP認可

独立した`GET /api/v1/bears/platform-workload-baseline`を両HTTP面へ追加する。query引数とbodyはゼロとし、非空JSONや空白だけのbodyも拒否する。現在のaccountとprincipal双方の真P、maximum role admin、選択adminをsource取得前に検査し、取得後も現在のfingerprintを比較する。未認可ではsourceを呼ばず、DB／Schema／grantを前提としない。

固定foundation namespaceの`trino-local`／coordinatorと`trino-local-worker`／worker、二つのDeployment Pod templateだけを読む。ordinaryとinitを全て種類内1始まりのordinalで扱い、replicas=0やrolloutでもtemplate宣言を対象とする。実Pod・ephemeral containerの状態を検証したとはしない。

## 旧4項目の宣言意図と8 admission policyの区別

server所有のversion `local-container-declarations-v1`で、[既存Policy advisory](../../tools/local_experiment/bears_resident.py)の4項目の意図を次の順序で限定する。結果は宣言条件との一致であり、現在のruntime security、admissionの許可、完全なglobal policy監査ではない。

| check ID | metとなる宣言条件 |
|---|---|
| read_only_root_filesystem | containerのreadOnlyRootFilesystemが厳密にtrue。Podからは継承しない。 |
| non_root | 実効runAsNonRootがtrue、かつ実効runAsUserがnumeric 0ではない。両UID未指定でもnonRoot=trueなら旧ruleどおりmetを許す。 |
| drop_all_capabilities | containerのcapabilities.dropに厳密なALLが含まれる。追加drop項目はmembershipを変えず、Podからは継承しない。 |
| cpu_memory_limits | CPU／memoryの両limitsがP5Bの既存Quantity構文に適合し、numeric coefficientがいずれも非zero。requests・ratio・合計・usageは評価しない。 |

non_rootの二つのfieldは独立して継承する。container fieldが欠落／nullの場合だけPod securityContextの同fieldを参照し、falseと0はPod値を上書きする。nonRootが欠落／falseならUIDが正でもnot_met、UID 0もnot_metである。UIDは非負int64を厳格に解釈し、不正bool／UIDはsource全体を確認不可にする。image USERや実行時の非root保証を推測しない。

cpu_memory_limitsはP5Bのpure validator／grammarを変更せず使い、coefficientのzeroだけを判定する。欠落、+0.0m／0e9／00Giなどのzero表記はnot_metとし、exponentの数字をcoefficientに混ぜない。unit変換・float演算・大小比較・headroom計算は行わず、不正Quantityはsource全体を確認不可にする。

[repoのnative-baseline定義](../../deploy/local-experiment/policy/native-baseline.json)にある8 admission policyとは、対象・rule数・強制の責務が異なる。本レビューはその適用・有効化・全条件を検証せず、8項目のPASSへ読み替えない。aggregate allow／deny／PASS、安全性や権限の承認を返さず、findingを緑にするために設定を変えない。

## 固定source・失敗・閉じたDTO

P5Bと同じread-only snapshot／foundationパターンを使う、固定fieldだけのkubectl GET 1回に限る。stdout上限・deadline・kill／reapを固定し、retryや新しいsupervisorを追加しない。全workload列挙、Pod／log／raw JSON、annotation／env／volume／image／secret、SQL／ClickHouse／LLM／metrics／lifecycle／復旧／書込みは呼ばない。

公開DTOのtop-levelはscope=platform、warehouse=LOCAL、selected_role=admin、UTCのobserved_at、baseline_version、coverage、baselineだけである。coverageは次の固定値を持つ。

```json
{"kind":"deployment_container_declarations","pod_template":true,"per_container":true,"runtime_security":false,"admission_decision":false,"global_policy":false}
```

baselineはavailability、code、UTC observed_at、deploymentsを持つ。availableではcode=null、deploymentsはcoordinator／workerの規定順の二つである。各componentは普通containerを1件以上と任意initを含み、全体のcontainer上限は32、公開DTOは64 KiB以内とする。現在fixtureの件数へ固定しない。

各containerはkind=container|init_container、positive ordinal、規定4つのcheck IDを持つchecksだけを公開し、check値はmet|not_metである。名前、UID、raw quantity、raw securityContext、errorを返さない。UIでは「宣言基準に一致」「宣言不足・不一致」「確認不可」とし、LOCAL／component／kind・ordinal、条件の短い説明、観測時刻を示す。

欠落した宣言を含む正常なsourceは既知のnot_metである。一方、source欠落・不正scalar・重複／不正component・過大入力・timeout等では全体をunavailableとし、code=WORKLOAD_BASELINE_UNAVAILABLE、deployments=nullだけを返す。部分的なmetや古い安心材料を残さず、raw errorを公開しない。

## 3面の手動共有snapshot

独立したbaseline controller／cache／tokenを、SRE／PolicyのPlatform scopeとComputeの3面で共有する。Computeはsibling `#observation-baseline-panel`、Bear側も既存C2B outputの外側に兄弟mountを置き、既存renderによる置換と分離する。Database scopeにplatform結果を表示しない。

source GETは明示captureだけとし、scope／Bear／tab／navigation／language／認可refreshで自動取得しない。同じ認可の完了snapshotと時刻を3面で共有し、view／role／scope変更では進行中の古い応答を無効化する。認可の現在失効は、C2B／P5B cacheが空でも全ての非表示baseline出力を消す。busy中の認可tickは手動captureのtokenを置き換えず、新しいtimerを追加しない。

現在のHTTP／parse認可失敗は自分の結果を破棄し、古いerrorが新captureを消さない。source unavailableは旧snapshotを確認不可へ置き換える。C2AのSRE／Policy別DB full9とsnapshot、Audit、P5B／P5C、runtime CAS／UNKNOWN、4下書きを分離する。

既存C2Bの4source DTO／cache／requestと旧 `/api/bears/run`・manifest check・SRE inspect・Policy baselineの拒否は維持する。新しい宣言レビューを既存の任意action開放へ置き換えない。日英・390px・既存keyboard操作を検証し、新たな視覚systemは導入しない。

## 検証証拠と限定技術受入

重点・事前・全体回帰は別の証拠として扱い、重複する件数を合算しない。最終の製品・test・build差分は7ファイルで、旧source／controller等の23 guardは変更していない。旧Bears testは無変更で、既存Database UI testは新Compute card IDのallowlist追加だけである。

| 層 | 結果と適用範囲 |
|---|---|
| focused backend／UI | backend199件（新規24件を含む）、UI重点310件＋Marts3件（新規baseline UI20件を含む）がそれぞれ成功。独立継承・false／UID0上書き・UID未指定・厳格scalar・ALL membership・両limits検証・zero／exponent・32総数・原子的確認不可、3面共有・現在／遅延error・hidden purge・busy認可tickを確認。 |
| 実API | Workbench8888／native8080で22 GETが期待結果と一致（200が2、403が10、400が10。各面200が1・403が5・400が5）。独立した固定field Go-template＋Decimal coefficientの投影と両正常応答が一致。coordinatorのordinary1＋init1、workerのordinary1の3コンテナで、計12個のmetという宣言結果を照合した。aggregateな安全性PASSではない。 |
| 認可の証拠 | 実機では現在のP・選択adminの取得と、非P／選択lower role・入力違反の拒否を確認。db_adminというhelper aliasのlogical_engineerはcurrent account／maximumがEngineerであり、admin要求は上限不足の拒否として扱う。非P adminの拒否、PのDB grantなし、取得途中のaccount／role失効はfixtureの証拠と分ける。実機のgrant／accountは変更していない。 |
| 3面の実画面 | SRE／PolicyのPlatform scopeとComputeから明示captureし、3つの非空出力が同じsnapshot／時刻で一致することを確認。日英・scope・Bear・tab・navigation・認可更新で自動source GETを追加しない。同じ認可の完了captureを共有し、現在header roleを下げると可視・非表示の3面から一致結果と観測時刻を消去した。 |
| DB reportとの分離 | C2AはPlatform／Databaseで表示slotを共用するため、Platform表示中の文字列をDB reportの変更と扱わず、DB scopeへ戻してSRE／Policyそれぞれのfields＋outputが原本と完全一致することを確認。role喪失後は新しくDB reportを確認し直し、保護cacheが失効を越えて残ったとは主張しない。 |
| 既存状態／復元 | Auditの2ページ目・詳細・download有効状態、C2Bの4source cache・P5B・P5Cの一覧、未保存runtime15分、4下書きがbaseline取得後も一致。検証後はSRE admin／A-marts、Policy admin／B-rawのDB report、Audit self／admin／A／空条件50件・詳細なし、P5C DB未選択・行0、runtime0、元の4下書き、Worksheet admin／A-marts／read-only／LOCALへ復元した。 |
| networkの適用範囲 | 全タブ共有serverの03:03〜03:28 UTCの区間は1,728 GETであり、新tabだけの件数ではない。新baselineは手動5 GET・全て200、自動capture・mutation・console warning／errorは0。C2A4回（準備2＋role復帰後の再確認2）、C2B1回、P5B1回、既存Audit一覧2＋詳細1、P5C一覧1も区別した。既存runtime196 GET等を新しいtimer・SQL inspection／実行へ読み替えない。 |
| 画面幅 | 日英390pxでpage client／scrollが390、card client／scrollが362でoverflowなし。同じcapture時刻を保持し、viewportを1920へ戻した。最終画面は日本語で、console異常0。 |
| source failureの範囲 | 不一致、未指定、source unavailable、malformed、timeout、0 replicasはfocused testsで検証した。実機は現在構成の12 metと無変更を確認し、実際の故障注入や設定変更は行っていない。条件を一致させるための構成修正を受入証拠に含めない。 |
| 最終保全 | active11,955行＋default156行を全て保持、新規行0、schema23、既存UNKNOWN、31 TABLE／33 VIEW headの完全一致、7保護ファイルとowner2 YAMLの内容／mode一致を確認。設定・proof・policyを変更せず、SQL・LLM・lifecycle・復旧操作を行っていない。 |
| canonical全体回帰 | 最終7 source／test／buildと23無変更guardを固定し、100群・4,266件中4,232成功・34 skip・失敗0・error0・終了コード0。skip34件の名称・理由は前回と同じで、追加・削除なし。 |
| 最終doctor | 終了コード0・overall healthy、46確認中45 healthyとSQL informational1。新しいEngine SQL実行／probeの証拠ではない。 |
| design-context（root最終gate） | 最終文書で2群14件成功・終了コード0、design-context正常（documents 94／issues 10／references 42）。239ローカルリンク、89能力ID順序、変更3行以外の86行とC2B本文prefixの保全、差分チェックも確認した。 |

上記は固定Pod-templateの4宣言条件に対する限定技術受入であり、C2C全体のglobal policy、native-baseline8項目の強制、現在のruntime security、admission許可、所有者本人による全体・運用受入の代行ではない。元のC2B・P5A／P5B／P5Cの受入結果と各snapshotのcoverageを維持する。

## 残るgate

C2C全体のglobal policy、admission enforcement、native workload action、任意manifest、AI設定、live usage／capacity／resizeは対象外であり、P6-4全体／P6-5全体／P6-7／M1本人受入は未完了のままである。
