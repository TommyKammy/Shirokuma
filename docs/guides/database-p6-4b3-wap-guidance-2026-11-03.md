---
project: Shirokuma
doc_id: DB-P6-4B3
title: Normal SQL Copilot and WAP workflow guidance
status: accepted
created: 2026-11-03
updated: 2026-11-03
version: "1.0"
area: guides
tags: [database, bears, copilot, wap, guidance, acceptance]
---

# DB-P6-4B3: SQL案から通常WAPへつなぐ案内

[89項目の監査](database-p6-0r1-capability-closure-audit-2026-11-03.md)で残ったAI-02／SQL-12／SQL-13の通常導線を整える。対応済みManual WAP・Managed Martsまで一括して未対応と読める案内を修正し、到達可能なBears共通領域とCopilotから既存の通常画面へ移動できるようにする。

## 実装した契約

SQL Copilotへの移動は現在のWorksheetを使い、提案の生成やSQLの実行を行わない。Manual WAPへの移動は現在のheader上限E／A、Managed Martsへの移動はAと既存subtabの条件に従う。移動先が持つ独立したDB／schema／role、draft、metadata選択、元の処理や追跡情報を変更しない。初回の通常metadata読取りは既存の画面遷移からのみ行い、編集中panelの再初期化を追加しない。

一般の移動と[B2の明示的な提案引渡し](database-p6-4b2-wap-handoff-2026-11-01.md)を区別する。B2は本人の成功済み保存案と同一DB／同一E・A role、許可されたSANDBOX、空の新規formが必要であり、移動だけで提案を渡さない。元の条件を満たして利用者が明示した場合にだけ、既存の「Manual WAPで編集」→「提案の下書きを読込」を使う。panelと新規targetのSANDBOX条件を案内し、満たすために入力を自動で消去・変更しない。

保存以降は、通常WAPのmodel保存→build→audit→Aによる明示公開を案内する。rollbackは既存publicationに結びつく操作であり、旧任意Snapshot rollbackとの完全互換を宣言しない。refreshはManaged MartsのA操作へ案内する。旧Bearフォーム、旧Copilot Stage／Publishや旧Data Engineerタブを有効化しない。SRE／Policy観測、P専用Platform・共有AI設定の条件を維持する。

6Fの保存済み未確定要求も、B2が新しい下書きを読み込める空formと区別する。既存の復旧判定を再利用し、未解決の要求をformだけ空にして無視しない。backend・保存形式・認可・実行経路は追加しない。

## 検証と受入

2026-11-03 JST、以下の範囲で限定技術受入を完了した。実装・重点試験・通常画面とrole条件・台帳／設定保全を分けて記録する。

| 層 | 今回の確認 |
| --- | --- |
| 重点試験 | AI WAP 36、SQL proposals 37、Platform observations 16、Database UI 69、Managed Marts 16の計174件が成功。V／E／A・最大role上限・Pを持たないDB admin、到達可能な配置、focus、B2の未確定追跡・別本人残留状態も確認 |
| 全体回帰 | canonical 123群・総計4,965件（4,931成功・34 skip）。初回のWorkbench群で翻訳辞書の単独抽出2件が新しいcomputed label定義を欠き失敗したため、既存2テストの依存抽出だけを各1行修正。製品コードを保持し、成功済み122群を再利用、失敗したWorkbench群1,218件を全件再実行して成功。初回失敗と補正後の証拠を両方保持 |
| 文書整合 | design-contextの14試験と94文書・10 issue参照・42参照を確認。機能表は89 ID／順序を維持し、AI-02／SQL-12／SQL-13以外の86行は不変 |
| 通常導線 | 管理者の通常画面でBears→Copilot、Bears／Copilot→Manual WAP、Bears／Copilot→Managed Martsの5導線を確認。EnterキーでCopilotへ移動し、既存入力欄へfocus。旧Data Engineerタブと直接実行は無効のまま |
| 独立context・入力保持 | AのWorksheet／MartsとBの未保存WAP draftを使い、移動・言語切替後もWAPのDB／schema／role・SQL・model／target・source・検査条件が一致。初期化済みWAP／Martsへの再訪は追加HTTP 0。初回の通常metadata GETと区別 |
| ブラウザー後片付け | 検証用Worksheetだけを破棄し、元の20 tab項目（詳細＋19 Worksheet）・選択・SQL／contextとBears入力28項目が開始時と完全一致。言語は日本語、viewportは通常1920pxへ復元し、検証タブを閉じた |
| 表示 | 日英と390px幅で追加案内の折返し・全ボタンの表示範囲を確認。Bearsはdocument幅390px。Copilot追加ボタンは範囲内だが、Worksheet既存warehouse-stripに由来するdocument幅410〜413pxも観測。画面全体の狭幅受入、実200% zoomは今回の判定に含めない |
| 実行境界 | 完全なWorkbenchアクセスログ区間はGET 2,334件・全200、POST／他のmutation 0。AI生成・SQL実行・提案取込・保存・build・audit・publish・rollback・refresh-runを送信しない。終盤のCDP記録は確認ダイアログ中断で欠落し、完全区間の根拠は8888ログを使用。8080はDEBUGログのため0bytesを全通信0の根拠にしない |
| データ・設定保全 | active台帳12,271行／79表、default台帳156行／78表の全行・schemaが一致し追加0。元のUNKNOWN query 3／admission 3／AI 6／dbt 1、active 0、保護12ファイルとmode・ownerの2 YAML・engine証跡・backend 17ファイル・通常2 PIDと環境を保持 |

実ブラウザーのroleはadminであり、その他のrole／上限条件は重点試験の証拠と区別する。今回の変更は案内と既存B2の未解決処理ガードであり、実engine／LLMを使う新規操作の受入は行っていない。既存[B1](database-p6-4b1-sql-proposals-2026-11-01.md)・[B2](database-p6-4b2-wap-handoff-2026-11-01.md)の証拠とも区別する。

証拠は非公開の `var/shirokuma/db-p6-4b3-wap-guidance-2026-11-03/private/` に保存する。重点試験、canonical初回と補正後、凍結source hash、served HTML照合、browser／network要約と画像、アクセスログ有限区間、台帳全行・保護ファイル・PID比較を分けて保持する。backend・認可・保存形式・実行API・サーバー起動状態は変更していない。

## 受入範囲と次の単位

今回の案内だけでAI-02の全旧操作、全89行、P6-7／M1本人受入を閉じない。Ingest清掃失敗の照合・限定修正も[6Fの残件](database-p6-6f-dataops-recovery-2026-11-03.md)として保持する。次は[AUD-02の履歴と通常導線の対応付け](database-p6-0r1-capability-closure-audit-2026-11-03.md)へ進み、実Compute伸縮はM1後とする。
