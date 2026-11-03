---
project: Shirokuma
doc_id: DB-P6-5G
title: Observed shared LOCAL runtime operation conditions
status: accepted
created: 2026-11-03
updated: 2026-11-03
version: "1.0"
area: guides
tags: [database, compute, runtime, authorization]
---

# DB-P6-5G: 共有LOCALの操作条件と停止状態の説明

[P5Fの保存済み操作履歴](database-p6-5f-compute-history-2026-11-03.md)に続き、既存Compute画面内で、観測済みの操作条件と物理状態の意味を説明する限定実装単位である。2026-11-03 JSTにこの限定範囲の技術受入を完了した。DB-P6-3・4・5全体、統合受入、M1本人受入は完了扱いにしない。

## 表示する判断材料

既存のruntime status、session、画面内の未確定要求・設定draftを使い、Wake／Chill／Reconcile／Auto-Chill保存の選択可否の理由を説明する。profile、P権限、最大role、選択role、観測済みavailable_actions、未確定の元要求、共有操作中、Chillのblocker、設定draftと競合を扱う。available_actionsは取得時点の操作候補であり、操作時の現在認可・受付・成功を保証しない。観測時刻と、実行時にserverが再確認することを併記する。

Chillに必要な6区分のblocker検証を他の操作へ広げない。集計値だけではReconcileの実行可否を確定できない。UNKNOWNの元要求は未送信扱いにせず、元のhandleを維持する。既存の「元の受付を確認」操作は、受付不明時に元のbodyとkeyをPOSTする場合があるため、自動実行も読取り専用との説明もしない。

Auto-Chill保存は、既存の送信直前の数値検証と同じ条件でボタンを無効化する。Number変換、整数、0〜1440の既存の受理範囲を維持する。その他の操作条件とhandlerは変更しない。

空文字・空白のみは従来どおり拒否する。Numberで解釈できる指数表記などを独自の文字列文法で狭めず、-0をJSON送信時に0とする既存挙動も維持する。設定のbase revisionを送信直前に検査する既存guardは保持し、画面の無効化条件へ別途追加しない。

## 停止状態の意味

activeは固定Deploymentの現世代の反映と、期待replica数・観測数の一致が確認されたことを示す。chilledは観測世代が現世代に達し、希望数と観測数が0になる条件を満たしたことを示す。全Podの停止や資源解放の証明ではなく、chilledから停止理由・実際の停止時刻を推定しない。startingは開始・停止の双方を含み得る遷移として表示する。unknownは物理状態が確認できていないことを示し、停止済みと扱わない。

保存済みengine検証は現在のhealthと分ける。最後の操作、保存済み操作履歴、現在のAuto-Chill分数から現在の停止原因を推定しない。既存の「共有LOCALの保存済み操作履歴」「状態を更新」への案内は、追加の取得や操作を発火させない。

## 境界と検証

UIのみの変更とし、API・DTO・永続化・server側認可・操作経路を追加しない。既存の取得契機と通信回数を維持し、表示関数から通信や操作を行わない。既存status GET自体のKubernetes観測・非active時の保存proof無効化は従来動作であり、本変更で新たに導入するものではない。

限定技術受入では、操作条件の既存挙動との一致、共有statusの認可境界、通常ブラウザーの日本語／英語・狭幅・200%表示、既存Database隔離を含む正規回帰、全DB行・schema・保存設定の維持を別々に記録する。今回はDB scopedな実engine SQLによる新しい隔離操作は行わない。実Computeの伸縮、実停止原因の新規記録、現在の権限を保証する新しい観測APIはこの単位に含めない。

### 限定技術受入

| 層 | 確認内容 |
|---|---|
| 実装・独立レビュー | 既存の全操作条件とhandlerを照合。Saveの無効入力だけを既存送信前検証へ一致させた。fetch箇所42・interval箇所1は変更前後で同数。既存の日本語訳全項目を比較し、汎用Starting／Transitioningを変更していない |
| 正規全体回帰 | venvでMakefileの正規119群を実行し、4,786件中4,752成功・34 skip・失敗0（970.5秒）。Workbench全体1,136件も成功。新規機能だけの成功から既存隔離・復旧の成功を推定せず、全群の終了結果を確認した |
| ホスト集中試験 | 新規15件、既存runtime UI 40件、基本Database UI 67件が成功。新規の-0ケースは実際のJSON送信で0へ正規化される期待値へ修正した。製品側の変換・送信は変更していない |
| 実API・認可境界 | Workbench 8888の既存共有status 13 GETで、P／選択admin、P／選択E・V、非P E・Vの5共有観測は200、候補4操作はP／選択adminだけ。偽admin／偽P主張2件は既存の安全な404、認証不正3件は401、framing不正3件は400。HTML 1 GETは凍結済みindexとbyte一致。各GET直後に全DB行・schema・12保護ファイルとmodeを照合して差分0 |
| 実ブラウザー | 通常Computeで観測時刻・4操作と再読込の理由・停止状態／保存engine証跡の説明を確認。実観測はactive・replica 2/2・保存engine証跡の日付2026-11-01。他の物理状態と未確定元要求の分岐は分離したUI試験で検証し、実engineの休止や受付再送は行っていない |
| 表示と保全 | 日本語・英語の390px幅でpanel 364px／scroll 364px、Chrome実200%（960px、DPR 2）で742px／742px。全説明のclient幅とscroll幅も一致。画面記録用に一時1920×1200へ変更した後、override解除、日本語・100%（1920px、DPR 1）へ復元。19 Worksheet＋詳細tab、選択SQL・DB／schema／role／mode／LOCAL／最大1000行は変更前と一致。検証tabは閉じた |
| 設計文書 | `make verify-design-context`とdiff checkが成功。機能表の89 ID・順序を維持し、CMP-02／03以外の87行は変更前と一致 |
| 全試験後の最終保全 | active 12,234行／79表、default 156行／78表の全行・schemaが完全一致し、追加・削除・変更行0。12保護ファイルのbyte／mode、所有者の2 YAML、engine proofのbyte／mode／generation／revision／epochも一致。既存UNKNOWNを保持し、実行中件数0。通常serverのPID・設定・stable TMPDIRは不変、再起動0。最終照合自体のHTTP・Kubernetes・engine操作・状態復元は0 |

初回CLI sandbox内では既存Node 25が終了値-11・stderr空で停止したため、そこでのJS試験結果を成功扱いにしていない。Node／既存harnessを変更せず、修正後の上記3群を通常ホストで検証した。全体回帰も同じ凍結済み製品コードを対象に完了した。

resident 8080へ行った最初のstatus GETは既存の未対応routeによる404だった。`warehouse_runtime_routes.dispatch`はWorkbench側にだけ接続されており、今回はrouteを追加せず残り12件を未送信とした。実API受入はWorkbenchの通常導線についてのものである。API受入helperでの実GET総数は15（Workbench status 13、resident既存境界1、HTML 1）であり、通常ブラウザーの既存自動観測はこの件数に含めない。重複送信・server再起動・別Kubernetes probe・engine SQL・操作POSTは0である。非公開受入helperのblocker形状・既存拒否statusの期待値を実契約へ合わせた際も、取得済みの原記録を再利用し、同じ要求を再送していない。

非公開の受入記録は`var/shirokuma/db-p6-5g-compute-explanations-2026-11-03/private/`に保存する。ブラウザーではWake／Chill／Reconcile／保存／元受付確認を実行していない。

## 後続

Computeの必須残件を機能表と照合して限定単位で閉じた後、推奨順にBears／Policyの既存action・89行の必須操作・現在の通常導線を対応付け、残件を限定する。既にCopilot／WAP／Managed Marts／Explorerで提供する機能を旧Bear画面へ重複実装しない。C2C1から全workload／global policyの完了を推定せず、固定8種のnative policy設定観測も、それだけで必須baseline auditを閉じる機能とは扱わない。P7の実CPU・メモリ変更とWorker 1→2→1はM1完了後とする。
