# DB-P6-4E: Databaseモードの共有AI設定

2026-11-02 JST。AI-06を対象に、通常Workbenchから共有AI設定の取得・編集・明示接続テスト・保存を接続し、この限定範囲の技術受入を完了した。M1本人受入とは分ける。

## 操作と権限

ヘッダーの「AI モデル設定」からprovider・model・API URL・API key・timeoutを扱う。設定はDatabase別ではなく共有であり、DB／schemaの選択やgrantを前提にしない。現在のaccountと認証principal双方のplatform権限、上限admin、選択中のglobal adminを検査する。DB管理権限だけでは操作できない。

新しいDatabase APIは`/api/v1/bears/ai-config`のGET／POSTと`/api/v1/bears/ai-test`のPOSTを使い、両HTTP面で同じadapterを通す。旧`/api/ai/config`・`/api/ai/test`のDatabaseモードでの拒否は維持する。取得と保存はLLMを呼ばず、接続テストだけが固定の短い疎通用入力を送る。接続成功と設定保存は別の結果である。

active runtimeの`ai_config.json`を使用し、旧default storeやprocess内cacheを設定元にしない。公開fieldを限定し、keyはmaskのみを返す。provider／URLの変更時は旧直接keyと環境credential参照を引き継がず、同じ接続先の省略・maskは保存credentialを維持する。永続化が失敗した場合は保存成功と表示しない。providerの応答本文やraw例外をエラーに含めない。

共有設定のprovider選択・疎通確認と、Database AI支援の対応条件を分ける。接続testは既存のOpenAI互換`/chat/completions`通信を再利用し、各社native APIやAWS署名を新設しない。既存Database推論は`local-openai`、明示loopback IP・HTTP port・`/v1`、対応model名、timeout最大60秒などの現行契約を維持する。共有設定の疎通成功だけでSQL説明・案生成の対応範囲を広げない。外部有料providerへの実呼出しは今回の実機検証に含めない。

## 画面と遅着応答

既存dialog・入力・日英翻訳・focusの構造を再利用する。編集、閉じる／再表示、本人・role・現在認可の変更で古い取得・test・save応答を無効化し、keyを消す。roleを下げた後にdialogや非表示の成功結果を残さない。接続testと保存の重複操作を防ぎ、自動test、SQL、Warehouse操作、DB下書きの更新を追加しない。

## 検証記録

| 層 | 結果と適用範囲 |
| --- | --- |
| コードと独立レビュー | Rootの重点5群181件が成功。実画面で見つけた編集後の成功色残留を修正し、最終UI12件＋既存plan UI39件が成功。新規backendは8件で、現在account／principal・P・role、grant非依存、途中失効、active root、key保護、保存失敗、両HTTP adapterを検証した。件数は重複するため合算しない。 |
| 実API | Workbench 8888／native 8080の84要求が期待結果と一致。timeoutを30→31へ保存して別portから確認し、30へ戻した。max_tokens／temperatureの保持、無保存の接続test、旧経路拒否、不正入力とmethod拒否を確認した。 |
| 認可 | 実機では真P・選択adminを許可し、非P Engineer／ViewerとPの選択lower roleを拒否。現在の非P最大admin accountは存在しないため、その拒否と途中失効はunit証拠として区別する。設定APIへDB contextを渡さず、grant非依存は空grantのunitでも確認した。 |
| 実LLM・失敗 | 固定の短い疎通入力によるLOCAL pingはAPI2回（1,436ms／149ms）、ブラウザー1回（167ms）が成功。接続できないloopback portへの無保存testはAI_TEST_FAILEDだけを返し、元設定を維持。実SQL・外部provider・有料呼出しは行っていない。 |
| 通常ブラウザー | ヘッダーから取得・接続test・31秒の保存・閉じる／再表示で保持・30秒へ復帰を確認。provider切替、Engineer時の非表示／無効化、閉じた後のkey消去と起点へのfocus復帰、Enterでの表示・Escapeでの閉鎖、日英を確認。保存後の編集は中立表示となり、成功色を残さない。console warning／errorは0。 |
| 幅・拡大 | 日英390pxでdialog client／scrollは356／356。日本語pageは390／390。200%実拡大（devicePixelRatio=2、viewport960px）でもdialog内の横はみ出しがなく、縦scrollで全操作に到達できる。100%と通常viewportへ復帰した。英語の背面にある既存Warehouse状態badgeはpage幅407pxとなるため、設定dialogの合格と全画面の狭幅合格を分け、Computeの後続課題へ残す。 |
| 復元と保全 | 検証用の設定変更後、active／default AI設定の元bytes・modeを復元し、両HTTP面のGETが原設定・30秒と一致した。profile・auth・proof・AutoChill・所有者2 YAMLを含む12保護ファイル、active79 table／11,955行・default78 table／156行とschemaが完全一致した。ブラウザーは19 Worksheetの件数と現在SQLを保持し、admin・日本語へ戻した。 |
| doctor | API受入後は終了コード0・overall healthy、45 healthyとSQL informational1。新しいEngine SQL probeの証拠とはしない。 |
| canonical全体回帰 | 最終sourceで、Makefileの`verify-local-experiment`からvenv明示で展開した全102群を独立process・4並列で実行。4,298件中4,264成功・34 skip・失敗0・error0、全群終了コード0。skipの名称・理由はC2C1時点と同一。最終UI修正と追加回帰testも含む。標準Pythonで停止した初回の部分結果は合算しない。 |
| 文書・最終gate | `verify-design-context`は14件成功・終了コード0、design-context正常（documents94／issues10／references42）。機能表は89 IDの順序を保持し、変更はAI-06だけ、残る88行は同一。全体回帰後の12保護ファイルと両control storeの再照合もPASS、差分チェック正常。 |

所有者本人の操作性評価、P6-4全体、DB-P6-3・4・5・6の全必須行の閉鎖とP6-7／M1の判定は継続する。AI設定の技術受入を、global policy全体や外部provider推論の受入へ広げない。

## 再現コマンド

標準PythonにはPyYAMLがないため、全体検査は既存DataOps venvを明示する。最初の標準Python実行ではinventory検査がこの不足で停止し、同検査19件はvenvで成功した。hostへのpackage追加は行わない。

```sh
rtk proxy make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3 DATAOPS_PYTHON=tools/local_experiment/dataops/.venv/bin/python3
rtk proxy make verify-design-context PYTHON=tools/local_experiment/dataops/.venv/bin/python3
rtk proxy ./bin/shirokuma doctor --profile local-experiment --output json
```

## 後続の順序

所有者の2026-11-02の指示に従い、AI設定の後はSQL-08と探索操作、Computeの実使用量・容量・履歴・停止理由・現在権限、Bears／Policyの残件、89行の必須機能対応、DB-P6-7とM1本人受入の順で進める。

次の限定unitは[SQL-08の返却済みWorksheet結果保存（DB-P6-6A設計案）](database-p6-6a-result-save-contract-2026-11-02.md)とする。保存直前の本人・保存role・現在contextの再認可、返却範囲・件数、NULLと文字列NULL、重複列名、引用符・Unicode・数値表現を契約化する。TSVファイル保存とclipboardは分ける。対応済みSnapshot previewとSnapshot固定Table全件Exportは別機能として維持する。

clone、profiling、Table lineage、Explorer lineage、Dropは独立した受入単位とし、旧global APIをそのまま開放しない。dbtの正式VIEW公開・VIEW連鎖・incremental・並列実行は別拡張のまま、TRF-11の必須操作との照合を先に行う。実compute伸縮のDB-P7はM1後とし、DB-P8／P9の既存依存条件は変更しない。
