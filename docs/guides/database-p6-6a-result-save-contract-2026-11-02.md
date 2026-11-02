# DB-P6-6A: Worksheet返却済み結果保存の実装契約

2026-11-02 JST。AI-06に続くSQL-08の通常Workbench保存を実装し、この限定範囲の技術受入を完了した。Snapshot固定Table全件Export、clipboard、clone／Dropとは別単位とする。

## 現在の経路と制約

`query_service.py`のPython JSON読取りはBIGINTを正確な整数として保持するが、Workbenchの`response.json()`でJavaScript Numberへ変換すると安全整数範囲を超える値が丸められる。ブラウザーの行値から元の数字を復元しない。[Trino 483の公式serializer](https://github.com/trinodb/trino/blob/483/core/trino-main/src/main/java/io/trino/server/protocol/JsonEncodingUtils.java)ではDECIMALは文字列であり、その桁・scaleを保持できる。REAL／DOUBLEに元の型以上の精度を保証しない。

元のpage本文は`_Pin.initial_response`／`_Pin.responses`というprocess内cacheにある。SQLite台帳と履歴は結果行を永続化しない。reload／restart後に結果を再生できる機能と、今回の保存操作を同一視しない。

## 限定範囲と受領page

画面が受領したpage receiptに対応するサーバーcacheだけを、元の順序で保存する。取得済み全行を対象とし、表示filter／sortの反映は初回範囲へ含めない。query ID・元request／Worksheet・受領pageを固定し、返却件数、保存件数、partial／truncated、元contextを明示する。

通常queryの実結果pageに一度だけ`result_page_id`を付ける。pending placeholderとAI planには付けない。同じ初期応答が複数のpoll tokenに対応し得るため、tokenを総結合せず、`initial_response`から`next_uri`のtokenに対応する既存`responses[token]`だけを辿る。要求receipt列がその連続prefixに一致することを確認する。新しいpage storeや永続台帳は追加しない。

UIは同一receiptの再受領で行を二重追加しない。同一値の正当な行を消す値ベースdedupもしない。IDなしの旧実結果は表示できるが保存不可とし、pendingの空応答は後続の保存を妨げない。

`query_service._owned()`による本人・保存role・現在account／binding／generation／grant／backend identityをcache読取り前後と直列化後に検査する。認可喪失時のcleanupやcancelを呼ばない。adminによる他人の結果取得例外、AI planやDataOps内部queryのWorksheet保存は追加しない。

CSV／JSON／TSVはいずれもファイル保存とする。サーバーで生成したbytesをbase64等で返し、ブラウザーが数値を再解釈せずBlob化する。送信した行値や単なる画面の有効状態を認可の証拠にしない。受信後も現在認可・Worksheet・元query・保存role・epochを確認し、失効・別owner・遅着応答からdownloadを開始しない。

cache消失時は結果保存が利用不可であることを示す。未取得pageの取得、SQLの再実行、Wake、LLM、Table全件Exportを代替として呼ばない。履歴復元は入力だけであり、保存結果が復元されたとは表示しない。

## APIと固定形式

両HTTP面の`POST /api/v1/queries/{id}/results/export`へ、format・元request_id・元worksheet_id・受領順のreceived_pagesを送る。DB contextや行値をclientから受け取らない。応答はserver生成bytesのbase64、固定filename／content_type、元ID・context、returned_rows／saved_rows、現在state、partial／truncatedに限定する。SQLやbackend URL、credential、raw例外を含めない。

- JSONはcolumn名・型の配列と行の配列を使い、重複列名・順序・JSON nullを保持する。整数セルは大小を問わずdecimal文字列へ変換し、encoding metadataで明示する。ARRAY／MAP／ROW内も同じ規則で再帰的に扱い、boolは数値へ変換しない。metadataの件数・revisionは数値のままとする。DECIMALは元の文字列とscale、有限floatは現在の表現を維持する。
- CSV／TSVはUTF-8・CRLF・headerあり。NULLは引用しないバックスラッシュ1文字＋N（`\N`）、非NULL値は全てdouble quoteで囲み、内部quoteを二重化する。空文字、文字列NULL、文字列`\N`を区別するNULL-aware dialectとし、一般CSV readerの型復元は保証しない。複合値は同じ整数encodingのcompact JSONとする。
- 対象は最大4,096 receipt、生成bytesは最大16 MiB。超過は全体を拒否し、保存範囲を黙って切り詰めない。partialはprefix末尾に次pageがある、末尾／現在stateがFINISHED以外、またはtruncatedの場合に真とする。returned_rowsは要求prefixの行数であり、pin全体の取得数を使わない。

CSV／JSON／TSVはそれぞれファイル保存とする。DatabaseモードではTSVのコピーと区別し、legacyのclipboard処理を流用しない。進行中の保存と現在のWorksheet／query／context／role／受領列を分離し、変更後の遅着応答ではdownloadを開始しない。

重点受入はBIGINT境界、DECIMALの桁・scale、NULL／空文字／literal NULL、重複列名、引用符・改行・Unicode、同値重複行、partial page、2DB・別owner・現在失効、cache消失、遅着downloadの拒否とする。

## 技術受入の結果

| 層 | 結果と適用範囲 |
| --- | --- |
| focused・独立レビュー | Rootの初回7群295件、native入口補完後のbackend17件＋query service44件、最終locale修正後のUI16件＋既存3群118件が成功。重複件数は合算しない。real Handlerのsubmit→page→export、前後認可・失効・元key／page照合、pending alias、zero-row、byte上限、nested整数・bool・NULLを確認した。 |
| canonical | venvを明示してMakefileから展開した全104群を4並列で実行。4,362件中4,328成功・34 skip・失敗0・error0、全群終了コード0。回帰開始後のlocale表示修正は最終重点134件と実画面でも再確認した。 |
| 実engineとAPI | A／Bの2DB、実P-admin／Engineer／Viewerの6contextでSELECTを1回ずつ受け付け、19列の値とsource catalog／schemaを確認。追加のmax_rows=1検査は3行中1行を返し、truncated=true・正常な上限取消によるCANCELEDを保持した。通常18＋truncated3＋prefix1の22ファイルが期待値と一致。exportの72要求は200=22／404=10／409=20／400=20で全件一致した。 |
| native入口 | 実機で8080の通常query入口が未接続と判明したため、native=Trueの`/api/v1/queries`名前空間だけを既存Workbench処理へ委譲した。認証・明示role・origin・context・query guardを共有し、`/v1/statement`・`/v1/info`・native DELETEの既存経路は維持する。最初のB POSTは元request／Worksheetの台帳・履歴がともに0件と照合し、未受付と確定。再送せず、修正後は別intentでBの3件と上限検査を実行した。 |
| 通常ブラウザーと実ファイル | 専用WorksheetからA／marts／admin／read-onlyで新規SELECTを1回実行し、CSV／JSON／TSVを各1回保存。Downloads内の該当する実ファイルだけを読み、整数・DECIMAL・NULL区別・重複列名・CRLF／tab／Unicode・複合値を照合した。表示filterで0行にしても、保存は受領済み1行である。言語修正の確認は別intentの新規SELECT1件とJSON1回で行い、JA→EN→JAの保存状態も正しく再表示した。 |
| 幅・role・復元 | 日英で操作ラベルを確認。390pxで結果panel client／scroll=346／346、日本語page=390／390。英語背面の既存Warehouse badgeによるpage幅405は設定画面時と同様の後続課題であり、保存panelの横はみ出しではない。実200%（devicePixelRatio=2、viewport960）でもpanel=716／716。Viewerへの上限変更で行とreceiptを消去し保存を無効化。100%／1920px・日本語・admin・元19 Worksheetの件数と選択SQL／contextへ復元した。console warning／errorは0。 |
| cache喪失と非実行 | 両normal serverを再起動し、旧7件のAPI保存をすべて409と確認。ブラウザーの旧結果も再実行せず保存不可へ変わり、表示中の1行と元queryのFINISHEDを保持した。再起動と保存拒否の前後で全control rows／schemaは一致。SQL・LLM・Wake・取消・再照合書込みはexportに追加しない。 |
| 保全・doctor | fresh baselineはactive11,956行／default156行。元の全行・schema・UNKNOWNと12保護設定／modeを保持。増分は許可された9件（API7＋UI2）のquery／execution context／admission各9行だけ。export前後には増分・変更0。最終doctorはhealthy、45 healthy＋SQL informational1、実SQL probeではない。 |
| 文書・最終gate | design-contextは14件成功・終了コード0（documents94／issues10／references42）。89能力IDの順序とSQL-08以外の88行を保持。skip34件の名称・理由はAI-06時点と同一。全体回帰とブラウザー復元後の保全再照合もPASS、差分チェック正常。 |

通常のQUERY受付・保存時の現在認可は既存query page取得と同じ`_owned()`を使う。結果表示自体のJavaScript数値丸めや、元sourceの現時点の再実行検証を完了したとはしない。ファイルは元queryの保持値を保存する。

再現用の全体検査は、標準Pythonの依存不足を避けて既存DataOps venvを明示する。

```sh
rtk proxy make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3 DATAOPS_PYTHON=tools/local_experiment/dataops/.venv/bin/python3
rtk proxy make verify-design-context PYTHON=tools/local_experiment/dataops/.venv/bin/python3
```

次の実装単位はEXP-10の同DB・固定Snapshotから新しいSANDBOX TABLEへの物理コピーとする。SQL-08の受入をDB-P6-6全体やM1本人受入へ広げず、clone／profiling／lineage／Dropをそれぞれ限定して進める。
