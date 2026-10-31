---
project: Shirokuma
doc_id: DB-P6-4A1
title: Database LOCAL SQL explanation
status: accepted
created: 2026-10-31
updated: 2026-10-31
version: "1.0"
area: guides
tags: [database, bears, copilot, local-ai, acceptance]
---

# DB-P6-4A1: 許可されたSQLのローカル説明

Query Doctor と SQL ワークシートの Copilot Explain を、Database モードの認可・コンテキスト検証へ接続した。最初の範囲はSQLの説明であり、実行計画は取得しない。この限定範囲の技術受入を完了した。

## 対象と利用条件

- 現在のSQLワークシートが持つdatabase／schema／role等の9項目を固定し、参照する全schemaのquery権限を検査する。管理者にも他DB参照を許可しない。
- Query Doctorは明示入力したSQL、Copilot Explainは現在のSQL草稿を対象にする。説明は読み取り専用で、エディターや実行結果を置換しない。
- 既存のDatabase read-only SQL guardを適用する。単一SELECT／CTE／JOIN／集計を扱い、更新SQL・複数文・未対応のtable function／metadata形式を拒否する。既存のschema／purpose制約は広げない。
- ローカル設定の`local-openai`を使い、推論先は数値loopbackのHTTPに限定する。プロキシ・リダイレクト・自動再送・外部fallback・資格情報転送を使わない。
- モデルへ送るのはコメント・リテラル（hex／JSONPath等を含む）を伏せたSQLの構造・識別子と許可済みの列名／型。行データ、VIEW定義、DDL、保存先、資格情報は送らない。元SQLは本人の履歴に保持する。
- 受付時・推論前後・応答時に認可を検査する。履歴は現在の本人・固定コンテキスト・全参照schema権限を検査し、当時のmetadataを表示する。

## 受付と履歴

`POST /api/v1/bears/explanations`へ`context`、元の`sql`、`language`とIdempotency-Keyを渡し、202の受付IDを得る。`GET`の個別照会／一覧は再推論しない。同じキーの別入力は拒否する。受付が不明な場合は元のキーと入力で照合し、処理中の所有者を失った記録は`UNKNOWN`として残す。

HTTP入口はWorkbench 8888とSQL Endpoint 8080。設定は呼出し時の`<state_dir>/ai_config.json`から読み、legacy Bearsのプロセス内設定cacheを引き継がない。今回のprofileにはファイルがなかったため、既存ローカル設定のprovider／model／URL／timeout／token数等のみで0600の設定を初期化した。資格情報はコピーせず、既存のglobal設定は変更していない。

現在のMTPLX 2.11.2は要求単位の`enable_thinking:false`に対応する。A1はこの固定値と簡潔な説明指示を送り、1,024 tokenの設定を説明本文に使う。サーバー本体・モデル・保存済みtoken上限は変更しない。未完・空本文・tool call等は成功扱いにしない。新しいCLIコマンドはこの単位には含めない。旧Bears経路・AI設定画面や、SQLの生成・実行・公開はDatabaseモードの既存制約を保つ。

## TRF-11の優先度確認

承認済み`lake_marts`は3 staging VIEWから2 MARTS TABLEを作る5モデル構成で、incremental／hook／独自materializationを採用していない。project／models／compile／保存済みplan、RunTask、TestTask、監査／公開、cancel／復旧／履歴への既存の対応付けを確認した。必須の実装欠落によるAI着手の阻害はなかった。

この静的対応付けは、承認済みプロジェクト全体の新たな実機受入やDB-P6-3全体の完了とは区別する。正式VIEW公開、VIEW連鎖、incremental、並列化は別の拡張単位とする。

## 検証と残件

| 層 | 確認した内容 |
|---|---|
| focused | AI 49件、ストア20件、既存設定3件、説明画面43件＋既存Database画面67件のNode検証（110件）。権限失効を保存直前のtransactionで再検査し、遅着した本文を保存しない。 |
| 実ローカル推論 | 通常の承認済み`marts.dim_customers`に対するCTE・集計を、A DBの管理者／8888とB DBのデータ作成者／8080で説明。両入口ともactive profileの設定を使用し、別actor検証を含む3キーで推論3回。コメント・文字列・数値sentinelと資格情報を送信していない。 |
| 実認可・隔離 | 管理者の他DB参照、閲覧者のRAW参照、更新SQL、provider上書き、別actor／別DB／別schemaの履歴、同一キー入力変更、8080の不正Originを拒否。同一9項目の別actorにも履歴を開示しない。 |
| HTTP異常・復旧 | タスク専用loopback応答で503／不正出力／redirect／timeoutを確認。timeoutはUNKNOWN、明確な拒否・不正出力はFAILED。同一キー照会と履歴で再推論しない。実モデルは停止せず、実HTTPサービス停止後の所有者喪失はUNKNOWN／AI_OWNER_LOSTへ照合。 |
| ブラウザー | Copilot／Query Doctorの実説明成功。応答待ちにSQLを編集した後は、サーバー側で成功しても旧応答を表示しない。元SQL・実行結果を置換しない。推論先を503応答にした状態でも再読込・履歴読取は追加のAI記録／推論なしで成立した。role上限変更で出力を消し、戻しても自動表示せず、現在の認可で明示的に読み直す。説明本文を先に表示し、元SQL・対象・model・時刻は表示専用とする。未対応のEXPLAIN入力は、SQL編集の案内を出してAI記録を作成しない。 |
| 保全・健全性 | control storeの既存78表の全行と28件のRAW／MARTS catalog head、runtime profile・global AI設定・owner-local YAMLを維持。旧default storeの例外は下記に記す。追加はschema23のAI記録表とprofile専用AI設定。doctorは45 healthy＋1 informational。 |
| canonical | Makefileの89検証単位、3,629件（3,595成功・34 skip）。成功済み1–38、旧schema fixture修正後の39（411件）、後半40–89を集計した。管理HTTP fixtureを隔離した49（17件）と翻訳harnessを修正した85（673件）は全件再実行して差し替え、重複を除外。design-context／diff checkも成功。 |

旧default store（`var/shirokuma/jobs.sqlite3`）には例外がある。既存の管理HTTPテストが一時stateを設定せず、generic operationの退役判定で開いたため、schema 22から23へ自動移行した。増加12,288 byteは空AI表と2索引の3ページ分に一致し、既存ジョブ2件・履歴7件の時刻は10月18日のままである。移行前の全行snapshotはないため、このストアの全行／byte一致は主張しない。移行後のbackupと調査記録をprivateへ保存し、当該テストを一時stateへ隔離した。再実行ではdefault storeのmtime／ctime／sizeが不変で、意図しないアクセスをguardする回帰テストも通った。active storeの既存78表の全行一致とは分けて報告する。

最初の複雑SQLは旧promptの内部reasoningだけで上限に達し、FAILED／AI_OUTPUT_INVALIDとなった。この失敗履歴を残し、対応する要求フラグを追加した後に新しい明示キーで成功を確認した。取得した応答本文や秘密値をこのガイドへ転載しない。実停止・遅延の実証は対象を限定したもので、実モデル停止や実権限失効の運用試験とは区別する。後者の失効・metadata変更はfocusedテストで検証した。

検証では両Python変数にプロジェクトの仮想環境を明示する。OS標準Pythonでは既存inventoryテストのPyYAMLが不足したため、その試行は成功結果に含めない。

```sh
rtk proxy make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3 DATAOPS_PYTHON=tools/local_experiment/dataops/.venv/bin/python3
rtk proxy make verify-design-context PYTHON=tools/local_experiment/dataops/.venv/bin/python3 DATAOPS_PYTHON=tools/local_experiment/dataops/.venv/bin/python3
```

証跡は`var/shirokuma/db-p6-4a1-2026-10-31/private/`、canonical／focusedログは`tmp/db-p6-4a1-*`へ保存した。旧3件のFENCED UNKNOWNと既存の公開／監査結果を変更せず、SQL実行・入場・DataOps・WAP件数は実機検証の前後で増加していない。

次はDB-P6-4Aの実行計画説明を独立して接続し、その後にSQL案・既存guard／WAPとの明示連携を進める。5 Bears全action、AI設定権限、DB-P6-3／P6-7の縦断受入、owner M1受入は継続する。
