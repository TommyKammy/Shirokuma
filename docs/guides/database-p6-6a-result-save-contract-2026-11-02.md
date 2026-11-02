# DB-P6-6A: Worksheet返却済み結果保存の設計案

2026-11-02 JST。AI-06の次に進めるSQL-08の読取り調査・設計案であり、実装・技術受入は未着手。Snapshot固定Table全件Export、clipboard、clone／Dropとは別単位とする。

## 現在の経路と制約

`query_service.py`のPython JSON読取りはBIGINTを正確な整数として保持するが、Workbenchの`response.json()`でJavaScript Numberへ変換すると安全整数範囲を超える値が丸められる。ブラウザーの行値から元の数字を復元しない。[Trino 483の公式serializer](https://github.com/trinodb/trino/blob/483/core/trino-main/src/main/java/io/trino/server/protocol/JsonEncodingUtils.java)ではDECIMALは文字列であり、その桁・scaleを保持できる。REAL／DOUBLEに元の型以上の精度を保証しない。

元のpage本文は`_Pin.initial_response`／`_Pin.responses`というprocess内cacheにある。SQLite台帳と履歴は結果行を永続化しない。reload／restart後に結果を再生できる機能と、今回の保存操作を同一視しない。

## 推奨する限定範囲

画面が受領したpage receiptに対応するサーバーcacheだけを、元の順序で保存する。取得済み全行を対象とし、表示filter／sortの反映は初回範囲へ含めない。query ID・元request／Worksheet・受領pageを固定し、返却件数、保存件数、partial／truncated、元contextを明示する。

同じ初期応答が複数のpoll tokenに対応し得るため、tokenを総結合せず、実結果pageの安定したreceiptを用意する。同一値の正当な行を消す値ベースdedupもしない。`query_service._owned()`による本人・保存role・現在account／binding／generation／grant／backend identityをcache読取り前後に検査する。adminによる他人の結果取得例外を作らない。

CSV／JSON／TSVはいずれもファイル保存とする。サーバーで生成したbytesをbase64等で返し、ブラウザーが数値を再解釈せずBlob化する。送信した行値や単なる画面の有効状態を認可の証拠にしない。受信後も現在認可・Worksheet・元query・保存role・epochを確認し、失効・別owner・遅着応答からdownloadを開始しない。

cache消失時は結果保存が利用不可であることを示す。未取得pageの取得、SQLの再実行、Wake、LLM、Table全件Exportを代替として呼ばない。

## 形式の候補と実装時の確認点

- JSONはcolumn名・型の配列と行の配列を使い、重複列名・順序・JSON nullを保持する。BIGINTの正確な数値literalと、型・encodingを伴う整数文字列のどちらを採用するか実装契約で固定する。通常のJavaScript JSON readerによる再読込みの精度限界も表示仕様へ含める。
- CSV／TSVはUTF-8、header、固定改行、引用符の二重化、埋込改行とUnicodeを検証する。候補はunquoted `\N`をNULL、非NULL値を全て引用するNULL-aware dialect。空文字、文字列NULL、文字列`\N`を区別できる読取り契約を固定し、一般CSV parserの型復元を保証しない。
- strict API、receipt追加、認可付きcache snapshot、serializer、両HTTP adapter、3形式の通常UIを小さい変更へまとめる。新しい結果永続化基盤やSQL経路を追加しない。

重点受入はBIGINT境界、DECIMALの桁・scale、NULL／空文字／literal NULL、重複列名、引用符・改行・Unicode、同値重複行、partial page、2DB・別owner・現在失効、cache消失、遅着downloadの拒否とする。
