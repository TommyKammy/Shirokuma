---
project: Shirokuma
doc_id: DB-P6-5F
title: Saved shared LOCAL runtime operation history
status: accepted
created: 2026-11-03
updated: 2026-11-03
version: "1.0"
area: guides
tags: [database, compute, history, platform, authorization]
---

# DB-P6-5F: 共有LOCALの保存済み操作履歴

2026-11-03 JSTに限定技術受入を完了した。[P5Eの容量・利用可能量](database-p6-5e-platform-capacity-2026-11-03.md)に続き、共有LOCALの保存済み起動・休止・設定・再検証操作を、Computeから手動で参照する。DBごとの保存query／job一覧である[P5C](database-p6-5c-contract-2026-11-02.md)とは独立した、現在P専用のglobal観測である。P6全体・M1本人受入は継続する。

## 保存記録の意味

sourceは現在のcontrol storeの`operations`と、明示要求の確認に必要な`warehouse_action_requests`に限る。対象は`WAREHOUSE_WAKE`、`WAREHOUSE_CHILL`、`WAREHOUSE_AUTO_CHILL`、`WAREHOUSE_RECONCILE`の4 actionと、exact LOCAL targetの記録である。保存されている最新要約を読むもので、状態遷移すべてを復元するaudit logではない。

| 記録 | 表示する意味と限界 |
| --- | --- |
| 起動 | 保存された起動要求・状態・時刻。明示API要求の対応が確認できる場合だけその契機を示す。対応するrequestがないことからSQL自動復帰や手動起動を推定しない。 |
| 休止 | 保存された休止要求。SUCCEEDEDでも、既に休止していた場合のno-opを含み得る。実際に停止した時刻へ読み替えない。 |
| 自動休止 | `WAREHOUSE_AUTO_CHILL`のうちpolicy変更ではない記録。保存された自動休止要求として示し、当時のminutes値や実停止を推定しない。 |
| Auto-Chill設定変更 | 同じactionでも`policy_change=true`は設定保存として区別し、自動停止として表示しない。 |
| 再検証・RECONCILED | 保存された再検証の状態と、記録があれば当時のobserved stateを示す。元の要求結果が成功した証明にはしない。 |

保存ACCEPTED／RUNNINGをPID生存判定でUNKNOWNへ変更せず、そのまま保存状態として表示する。`physical_started`の記録は送信・試行開始に関するmarkerであり、物理効果の証明ではない。marker未記録も未送信の証明ではない。記録された理由コードは当時のものであり、現在のblockerや停止原因と結び付けない。

休止要求の種別は保存actionのCHILLを`stop_request`、policy変更を除いたAUTO_CHILLを`auto_idle_request`、他をunknownとする。現在のwriterが保存する要求種別の分類であり、実停止の原因・当時の設定値・実行者の推定ではない。producerが存在しない`request_kind` metadataは解釈せず、任意の付加fieldで分類を変えない。

monitorのidle-not-due／blocked／already-stoppedなどはoperationを作らない場合があるため、存在しない見送り履歴を作らない。現在停止している原因、現在のプロセス・engineの健全性、包括的な起動回数・稼働時間は本履歴から導出しない。現在状態と操作時の条件は、既存の共有LOCAL runtime panelで再確認する。

## API・現在認可・読取り境界

独立した`GET /api/v1/bears/platform-runtime-history`を提供する。queryは`limit`（既定20、1〜50）と`cursor`だけとし、bodyやDB contextを入力に取らない。現在accountとprincipal双方の真P、最大role admin、選択role adminを読取り前と返却前に確認する。DB／schema／grantに依存せず、非Pの自己申告・NULL grantから権限を推定しない。

sourceは`audit.snapshot()`による純粋なmetadata読取りとし、runtimeの`status`／`operation`／`_operation`を呼ばない。liveness、PID検査、Kubernetes、SDK、engine SQL、engine health probe、Wake／Chill／reconcile、engine proof修復・無効化、control store更新を起動しない。既存runtime APIと現在の操作権限・blocker判定も変更しない。

公開値は操作ID、固定した操作種別、保存状態、保存された固定時刻、裏付けのある要求契機、marker／再検証の限定情報、安全な理由コードだけに絞る。actor／principal、DB／schema／query ID、payload、idempotency key、PID、private metadata、raw error messageは返さない。不明な理由コードから新しい理由を作らず、限定した「その他」にする。

閉じたDTOはscope／warehouse／selected_role／observed_at／coverage／availability／code／items／page_cursor／next_cursor／scanned_count／omitted_count／limitの13 fieldである。各itemもoperation_id／kind／action／saved_state／origin／stop_trigger／accepted_at／started_at／completed_at／reconciled_at／error_category／dispatch_marker／reconciled_observed_stateの13 fieldだけを持つ。source取得不能では`HISTORY_UNAVAILABLE`とitems=null、確認できる空ページはitems=[]として区別する。各JSON sourceは16 KiB以内、公開DTOは32 KiB以内、cursorは2,048文字以内に限定する。

## ページングと破損記録

挿入順のrowid keysetと初回ceilingを使い、候補を`limit+1`まで読む。新しい行の追加は「最新を取得」で取り直す。ceilingは既存行の後続状態更新を凍結しないため、完全な過去時点snapshotとは呼ばない。

Cursorは既存Auditのprocess-local署名を再利用し、用途・版・limit・現在権限のhash・範囲を対応させる。tokenにprincipalやrequest keyを入れず、token自体を権限として扱わない。再起動や別processで無効になったcursorは、最新一覧の明示取得へ案内し、復旧や操作再送を行わない。新しいsecretや永続storeは作らない。

破損・不正な候補行はboundedに扱い、確認できなかった件数と走査範囲を明示する。全件を埋めるための無制限なrefillを行わない。部分的に読めない結果を完全な履歴として表示せず、取得不能と有効な空一覧を区別する。

保存timestampはUTC Zと`+00:00`を受け付け、最大9桁のfractionを保持してZへ正規化する。過去の保存時刻をlive metricsの鮮度制限で落とさず、現在のGET観測時刻と区別する。一般Auditの配列形式timestamp投影を、runtimeの辞書形式へそのまま適用しない。

response observed_atだけは受領時に120秒以内・future tolerance5秒の範囲を検査する。保存された過去時刻へこの鮮度制限を適用しない。署名済みcursorが他用途・他limit・他権限・他processのもの、壊れた署名や範囲なら`STALE_CURSOR`409とし、無効なqueryは400で拒否する。

## 通常画面と受入

Computeに独立したP専用の手動一覧を設け、「最新を取得」「古いページ」「新しいページ」を提供する。最初は未取得とし、画面表示・移動・DB切替・locale変更・認可refreshだけでは履歴GETを送らない。新旧ページを同じceilingで読み、現在権限・所有者・navigation／request epochによって遅着応答とABAを破棄する。

実行・復旧・再送・exportのボタンは追加しない。日本語／英語と390px・実200%で、保存状態と要求契機、取得時刻、理由、ページの位置が読めることを確認する。P5A／P5B／P5D／P5E／C2C1の観測cache、P5C activityと既存の操作導線を維持する。

一覧は操作・保存状態・主要な保存時刻・当時の理由を簡潔に示し、操作ID、他の保存時刻、操作元、要求分類、marker、照合結果、安全な理由コードは「保存された詳細」で展開する。詳しいcoverageも「履歴の範囲」で展開する。localeや既存controls更新時も同じrecordの展開状態・focusを保持し、既存の起動／休止ボタンの日本語訳を変更しない。

準備では現在storeの対象操作144件を確認した。lifecycleは139件（SUCCEEDED132、FAILED7）、別のpolicy変更は5件である。保存timestampはすべて辞書形式・UTC `+00:00`であり、request87件は異なる親operationへ対応していた。この件数は当時の保存事実であり、固定値や完全な実稼働回数として扱わない。

コード・認可、実API、通常ブラウザー、保全を分けて検証し、private証拠を`var/shirokuma/db-p6-5f-compute-history-2026-11-03/private/`に保存する。UNKNOWN・既存記録を保護し、履歴受入のための新しい起動・休止や口座変更は行わない。

| 検証層 | 2026-11-03 JSTの証拠 |
| --- | --- |
| 実装・独立レビュー | CLI workerの7ファイルを別担当がレビュー。policy discriminator、既存actionからの要求分類、正の証拠だけのorigin、過去時刻、cursor、破損gap、readonlyとglobal JA互換を確認した。 |
| focused／環境差 | backend23／新UI18成功。worker側のNode25初期化RangeErrorにより既存67件を確認できなかったため、ホストで67件を実行して成功（5.283秒）を確認した。Nodeや既存harnessの設定は変更していない。新しい履歴専用の日本語3値を整え、UI18件が再成功した。 |
| canonical | 118群4,756件を完走し、4,722成功・34 skip・失敗0（987.7秒）。backend23／新UI18と、最終Workbench全1,121件も成功した。 |
| 実API | 8888／8080で50 GETの期待statusを確認（200×10、400×14、409×8、401×6、403×12）し、認可adapter9件も成功。両面50→50→44件の全144 ID順・重複なし・全13 item fieldを原snapshotと照合し、省略0を確認した。limit1、異なるprocess／limit、tamper、重複queryも検証した。 |
| 実記録の区別 | lifecycle139／policy5、全SUCCEEDED137（lifecycle132＋policy5）／FAILED7、5種類の非null理由コードを保持。origin explicit87／unrecorded57、dispatch記録130／未記録14、休止要求分類stop68／auto3／unknown73も確認した。実稼働・実効果の回数とは呼ばない。 |
| 通常ブラウザー | 最新20→古い20→新しい20を明示取得し、先頭ページのID復元とページ間非重複を確認。失敗記録の意味と`WAREHOUSE_WAKE_FAILED`を詳細で表示し、日英で同じsnapshot・詳細展開を保持した。日本語3値のstatic再読込み後も同じ20 IDを取得した。手動GET合計4。 |
| レイアウト | 日英390pxでpanel364／scroll364、詳細を開いた状態でも成立。実200%は日英ともDPR2／viewport960／panel742／scroll742、詳細展開を保持。対象panelの検証であり、アプリ全体の既存overflow解消は主張しない。 |
| 復元・保全 | 日本語・実100%（DPR1、1920px）、元19 Worksheet（Detailsを含む20tab）と選択Worksheet／SQL／A／Marts／admin／read-onlyを保持し、検証用タブを閉じた。API・ブラウザー後もactive12,234／default156行の全行・schema、12保護ファイルとmodeの差分0、UNKNOWN query3／admission3／AI6／dbt1、実行中0を維持した。 |

API50 GET＋ブラウザー4 GETは計54件。8888 access logはAPI25＋ブラウザー4の29件と一致した。8080はaccess logを出力しないため、個別HTTP receiptで25件を証明する。通常serverは同一profile・argv・cwd・env・stable TMPDIRのまま限定再起動し、sourceのための追加status／operation／liveness／Kubernetes／SDK／engine SQL／health probeを行っていない。通常startupの既存read-only health SQL回数は別枠の未計測とする。

詳細はprivateの`http-acceptance-summary.json`、`pagination-acceptance-summary.json`、`authority-adapter-summary.json`、`final-comparison-summary.json`、`browser-*.json`／`browser-*.txt`、`compute-history-ja.png`、各test logに保存する。非terminal／RECONCILED、no-op、壊れた行・深いJSON・重複／不正bool、途中失効・append／後続行更新、stale cursorは隔離テストで検証し、実環境の記録や口座は変更していない。

## 後続

現在の操作権限とblockerの説明、停止原因として確定できる範囲を既存runtimeと照合し、必要な残件を別単位で進める。Bears／Policy残件、89行の全体照合、DB-P6-7の統合受入・M1本人受入は継続する。実伸縮のDB-P7はM1後に扱う。
