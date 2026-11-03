---
project: Shirokuma
doc_id: DB-P6-6F
title: Manual Ingest and WAP original operation recovery after reload
status: accepted
created: 2026-11-03
updated: 2026-11-03
version: "1.0"
area: guides
tags: [database, dataops, ingest, wap, recovery, acceptance]
---

# DB-P6-6F: 再読み込み後の元Ingest／WAP処理への再接続

[89項目の監査](database-p6-0r1-capability-closure-audit-2026-11-03.md)で残ったING-03／TRF-06の通常再接続導線を整備し、限定技術受入を完了した。元の処理を追跡し直すための機能であり、新しい取り込み・SQL・公開を復旧操作から送信しない。対象はManual IngestとManual WAPのstage／audit／publish／rollbackの5 actionである。

## 実装する契約

新規送信の認可を確認した後、最初のPOSTより前に、現在の利用者に属する元キー・action・完全な実行context・作成時刻と、判明後の処理ID・最後に確認した状態だけを保存する。再接続用のブラウザー保存には、ファイル本文、SQL、POST本文、処理結果、credential、backend IDを含めない。利用者ごとに25件までとし、未確定の処理を自動的に消して空きを作らない。保存できない場合は新規POSTを止め、入力と元キーを保持する。読み込み後の別タブによる保存内容の変更や破損を検出した場合も停止し、未確定の情報を古いcacheで上書きしない。ブラウザー間の原子的な排他を追加したものではない。

通常のIngest／WAP panelに保存済み処理の選択と明示的な再接続を置く。画面表示やreload、言語変更、navigationから自動で再接続しない。保存した元のDB／schema／roleを案内し、現在のフォームやmetadata選択を上書きしない。元のDB／schema／実行role／binding・generation・grantの条件を現在の認可と照合する。利用者・権限・contextが変わった場合は保護対象の表示と遅れた応答を捨て、元の追跡情報を別の要求に転用しない。実際に本人が切り替わった場合は、前本人のファイル・draft・inventory・context選択も消去する。同一本人のcontext／画面移動では入力を保持する。

受付結果が不明でIDがない場合は、`GET /api/v1/dataops/by-key`を使う。queryは`context`・`route`・`key`各1件に限定し、認証済みactor＋対象5 route＋元キーに一致する既存recordだけを照会する。完全contextの現在認可を前後で検査し、不在と別contextは同じ404を返す。応答は`context`と`recovery: {operation_id, route, state}`だけで、本文・結果・backend情報を含めない。照会は新規受付・lease・状態照合・取消・Wakeを行わない。既存のcontext検査が使うcontrol-store接続を、OSレベルのread-only接続と呼ばない。

ID判明後は、同じ明示操作から既存の処理GETへ接続する。既存GETには認可失効時の取消やowner消失時のUNKNOWN照合があるため、状態への作用が一切ないとは扱わない。404は元POSTが未受付だった証明にはならず、未確定の元キーを保持する。reload後は元POST本文を再構築・再送しない。同じpanelに未確定の元要求が残る間は、別のreceiptを選んで新規要求の制約を迂回できない。

元の画面にPOST本文が残る既存の明示的な同一キー再送と、reload後の既存処理照会は区別する。取消は既知の元ID・contextと既存backendの条件に従い、再接続時に自動で取り消さない。UNKNOWNや子処理のUNKNOWNは未解決のまま保持し、新しい実行で置き換えない。

## 実装と検証

実装は[Workbench](../../tools/local_experiment/workbench/index.html)と[DataOps HTTP adapter](../../tools/local_experiment/dataops_routes.py)、既存の[UI試験](../../tests/test_workbench_dataops_ui.py)・[HTTP試験](../../tests/test_dataops_routes.py)に限定する。新しいstore・schema・lease・実行経路・Makefile群は追加していない。共通JSONヘルパーでは、現在の認可を要求する既存AI設定の401／403を本文未読で拒否する条件を維持した。

| 検証層 | 結果・範囲 |
|---|---|
| 重点試験 | HTTP 36、DataOps UI 69、基本Worksheet 69、AI設定12の計186件PASS。5 actionのlookup／reload、元本人・完全context、権限とschema喪失、遅延・ABA、保存失敗・破損・25件上限、既存live同一キー操作と復旧後GET限定を確認 |
| 正規回帰 | 123群4,958件＝4,924成功・34 skip、失敗0（974.9秒）。Workbench集約1,211件PASS。AI設定no-parse回帰を修正した最終sourceで全群を完了し、Makefileの群・順序と一致するコマンドをホストvenv・`TMPDIR=/tmp`で実施 |
| 実HTTP・隔離 | 8888／8080で純lookup 32 GET。既存build／audit／publishと新Ingestの閉じたDTO、別本人・DB・schema、古いgrant、認証、厳密なquery／bodyを確認。phase前後の全行・schemaは不変 |
| 実HTTPの限界 | Pのadmin以外への切替にも明示grantが必要で、PのE選択はlookup前に403となった。想定404だった1 receiptを再送せず別記録で再分類し、元receiptは保持。有効な同一actor・別roleのlive 404は未実施で、正規fixtureのHTTP試験で確認した。実環境のgrant／accountは変更していない |
| 実Ingest・故障復旧 | A／RAWへ3行を1回だけ取り込み、成功HTTP200の応答を検証用tabで喪失させた。ID未判明のままreload後、元キーlookup→同じIDのGETでFINISHED・commit 3行・清掃失敗を回復。再接続区間のPOSTは0 |
| 実WAP・故障復旧 | B／SANDBOXで新modelの版1を保存し、buildを1回だけ実行。成功応答喪失→reload→元キーlookup→同じIDのGETでbuild ID・版・固定stageを回復し、現在のSQL・model入力・metadata選択を保持。再接続区間のPOSTは0 |
| 実処理の範囲 | 新parent 2、子query 6、admission 8。実engineで新規に受入した復旧はIngestとWAP build。audit／publish／rollbackの新しい実行は追加せず、復旧5 actionは重点試験、既存build／audit／publishの純lookupは実HTTPで確認 |
| 台帳・設定保全 | 元active 79表12,234行・旧default 78表156行はschemaを含め一致。追加37行だけを明示2 parent IDからquery・admission・model／build・新object／owned／claimへ追跡して照合した。旧default追加0、保護12ファイルのbytes／modeとowner-local 2 YAMLを保持。active 0、既存UNKNOWN query 3／admission 3／AI 6／dbt 1を維持 |
| 通常画面 | 元本人で2 DBの復旧を実施。新しい復旧toolbarの日英label／helpと390px幅を確認し、横overflow 0。言語・viewport変更時の自動DataOps照会／POSTは0。検証用の終了済み追跡情報だけを除去し、元19 Worksheet・SQL・context・Bear入力・日本語・通常viewportを復元、専用tabを閉じた |
| 記録の限界 | 全期間のCDP bufferは先頭が欠落したため、総POST数はserver access logと台帳で照合した。Ingest・model save・WAP build各1と純preview 1の計4 POSTで、cancel／audit／publish／rollbackの送信は0。再接続区間のCDP記録は欠落なし。実200% zoom・新しい実取消／rollback・M1縦断と本人操作性は今回の受入範囲に含めず、全体gateを継続する |
| runtime | 既存profile・引数・stable TMPDIRのまま通常HTTP 2 processだけを再起動。変更していないbackend 12 moduleを保持。HTMLは修正前後の認証付き200を各1回取得し、最終source一致を確認。通常起動のread-only health SQLは台帳外・未計測として別扱い |
| 診断・文書 | scoped doctor 1回はhealthy、45項目healthy、SQLはworkload readinessのみのinformational。`verify-design-context` 14件PASS、94文書・10 issue・42参照の検査が成功。最終`git diff --check`もPASS |

全suite後も元の全行と許可済み追加37行がphase snapshotと完全一致し、最終保全はPASSである。新しいactive／UNKNOWNは0。証拠はローカルの`var/shirokuma/db-p6-6f-dataops-recovery-2026-11-03/private/`に保持する。既存UNKNOWNをinspectしていない。

## 継続する残件

実IngestはFINISHED・commit 3行だが、最後のcleanup子queryがengine送信後にFAILEDとなり、清掃エラーとowned中間stageが1件残り、終端の最終target claimも保持されている。既存の終端ownerからのhandoffにより、claim単独で次のwriteを塞ぐ状態ではない。新しいUNKNOWNではなく、復旧画面はcommitと清掃失敗を分離して表示した。細かいengine原因は台帳・既存logから確定できず、再DROPや清掃を自動で追加していない。この清掃失敗の照合・限定修正をM1の残件として保持する。

この単位はING-03／TRF-06の通常再接続を限定受入するもので、全89行・全DataOps障害・P6-7の統合受入・M1本人受入を閉じない。全体計画の実Compute伸縮はM1後という順序を維持する。
