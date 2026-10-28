# P3-4 Snapshot固定Table Export 実装・実機検証報告

確認日: 2026-10-20 / checkout: `local/autonomous-experiment`

P3-1の共通DataOps実行境界を使うTable Exportを実装した。実Trino / Polaris / S3を使い、sales / finance / insights_e88387a5でCSV / JSONL、開始時Snapshotの固定、本人・DB別の取得制御、View拒否を検証した。この報告はP3-4のbackend単位を対象とする。通常Workbench / CLIの接続とbrowser縦断受入は、計画どおりP3-5へ残す。

## 実装と呼出契約

主な実装は[manual_export.py](../../tools/local_experiment/manual_export.py)、REST実測と生成SQLは[dataops_service.py](../../tools/local_experiment/dataops_service.py)に置いた。

| API | 契約 |
| --- | --- |
| `export_table(principal, context, *, key, source, format='csv')` | 実在する同DBのTable ResourceRefを要求。形式は`csv` / `jsonl`のみ。DataOpsのOperationを返し、受付時から`result.export_id`、完了時は`result.receipt`も返す |
| `get_export(principal, context, *, export_id)` | 完了済みartifactのreceiptを、現在の認可・source状態・ファイル整合性を再確認して取得 |
| `read_export(principal, context, *, export_id)` | 同じ検査のうえで`{receipt, content: bytes}`を返す。filesystem pathは返さない |
| `lake_ingest.manual_export(...)` | 上記へのlazy facade。旧Export入口のgateは維持 |

開始時に実Table UUID / incarnation、main Snapshot IDの十進文字列、そのSnapshotのschema / field ID / 列名 / 型を固定する。COUNTと明示全列SELECTの両方を同じ`FOR VERSION AS OF`で実行し、receiptにも同じidentityを保存する。Snapshotに属する旧列名と、開始時のcurrent schemaを別々に保持するため、過去のADD / RENAMEがあるTableにも対応し、Export中のDDLは拒否する。current headへのappendは許容する。

Snapshotの取得不能・失効、UUIDの変更、schemaの変更、ページの列metadata・行幅の不一致、COUNTと出力件数の不一致は完了扱いにしない。latestへのfallbackや行数上限による切捨ては行わない。全ページを取得し、`expected_count == written_count`を確認する。初期実装は全行と出力byteをメモリに保持するため、利用可能メモリが実用上の上限となる。大容量向けのstreamingは未実装。

親admissionをSQL実行からファイル確定まで保持し、子Query intentを外部送信前に保存する。同じIdempotency-Key / payloadの再実行は保存済みOperationの観測となり、SQLを再送しない。ExportのSQLはread-onlyであり、`committed`は成功時も`false`。完了artifactの状態は別途管理する。

[control_store.py](../../tools/local_experiment/control_store.py)をschema 9へ進め、`export_artifacts`にINTENT / COMPLETE / FAILED、owner、保存context、receipt、server生成の相対名、profile識別子を保存する。設定済みprofile rootのprivateディレクトリ0700 / ファイル0600へ一時出力し、flush / fsync / atomic renameの後、親FINISHEDとartifact COMPLETEを同一transactionで確定する。取消しとの競合ではdurableな取消し状態を優先する。

取得には作成本人と受理時contextの完全一致を要求する。現在のgrant / binding、DB状態、Table identity、Snapshotの残存、schemaを再確認し、byte数とSHA-256を照合する。読取後も認可する。adminによる他人のartifact取得への自動代替はない。sourceの削除・Snapshot失効・schema変更後には、以前の完了artifactも取得を拒否する。

## 出力形式

UTF-8 / LF。CSVはheader付き、非NULLを引用し、NULLは非引用の空欄、空文字は`""`として区別する。文字列`\N`、先頭ゼロ、改行、Unicodeを保持する。1列TableのNULL行は、headerで列数を定義したうえで空の1レコード（LFのみ）とする。空Snapshotはheaderだけになる。

JSONLは1行1 objectでありJSON配列ではない。NULLは`null`、boolean / 数値はJSON token、date / timestamp / stringは文字列とする。空Snapshotは0 byte。BIGINTとDecimal(38)はbinary floatを経由せず、TrinoのCAST文字列表現から検証して出力する。float / doubleの有限性と表現範囲も検査する。出力の行順は保証しない。

対応するIceberg型はstring、boolean、int、long、float、double、decimal(p,s)（p <= 38）、date、timezoneなしtimestamp（小数秒6桁まで）。complex / binary / time / timezone型、JSON配列、Parquetは未対応として拒否する。

CSV書込には既存Python 3.12 runtimeの`QUOTE_NOTNULL`を使う。Python 3.12の標準readerは同定数によるNULLと空文字の復元に対応していないため、そのreaderをround-tripの証拠にしていない。実機検証ではraw byteと引用有無・header列数を保持する独立parserで照合した。このreader側の変更は[CPython issue #113732](https://github.com/python/cpython/issues/113732)で3.13向けに扱われている。製品用readerやhost runtimeの変更は追加していない。

receiptはexport / operation ID、ownerと保存context、DB / Schema / Table Ref、UUID、Snapshot文字列、Snapshot schemaとcurrent schema guard、format / encoding / newline / NULL規約、期待件数 / 出力件数 / byte数 / SHA-256を持つ。

## 実機で検出して修正した問題

- 過去にADD / RENAMEしたTableでcurrent schemaをtime travel SELECTへ使うと、実Trinoは旧Snapshotの列を解決できなかった。Snapshotに属するschemaで投影し、current schemaは別のDDL guardに分離した。旧Snapshot 7行と、append後の新schema / Snapshot 8行をそれぞれ照合した。
- Python 3.12 writerは1列NULLをそのまま渡すと例外になった。宣言済み1列headerのもとで非引用空レコードを出力し、NULL / 空文字 / `\N` / Unicode / 改行を両形式で実測した。
- 実SELECTの最終FINISHED応答を捨てた際、失敗処理がUNKNOWN子Queryへ自動取消しを行っていた。UNKNOWN親・子は観測専用とし、確定して動作中の子だけを元credentialで取り消すよう修正した。新キーで実応答喪失を再現し、自動DELETE 0、同キー再送POST 0、UNKNOWN保持を確認した。本人・source・SQL・backend IDが一致する実FINISHED観測後もartifactを昇格せず、親を明示的に`reconcile_failure`した。

最後の問題を検出した旧操作の誤CANCELED記録は、実FINISHED応答・独立observer証拠とともに欠陥の履歴として残した。親だけを明示的にFAILEDへ確定し、子台帳の書換えや完了artifactへの昇格は行っていない。初期のschema不一致・検証harness自身の失敗記録も、修正後の合格記録とは分けて保持する。

単体検証でも、取消しとCOMPLETE確定の競合、書込とunlinkの複合失敗、空ページのmetadata検査、float範囲を修正・確認した。清掃失敗は元の例外と`EXPORT_FILE_CLEANUP_FAILED`を残し、privateな未完成ファイルを取得可能にしない。

## 検証結果

実機の合格記録は機能1,176項目＋清掃145項目の**1,321 / 1,321**。これはharnessのassertion数であり、SQL実行回数ではない。旧不具合・harness失敗の記録と、互換性確認の重複実行66項目はこの合格件数に含めない。合格phaseはMAIN 601、schema evolution 113、互換取得66、faults 123、advanced 56、1列・空Snapshot 121、grant 57、View正負10、修正後応答喪失29、清掃145。

| 実機確認 | 結果 |
| --- | --- |
| 3DBの同名RAW / MARTS Table、複数本人・role、両形式 | sales 1,003行 / finance 7行 / insights_e88387a5 13行を全件照合。viewerの許可MARTS、engineer、明示adminを正例にし、別DB・別本人・別context・未許可roleを拒否 |
| COUNT後・SELECT前のappend | 開始Snapshot `3870363484849773251`の7行でCOUNT / SELECT / receiptが一致。currentは`4104788304001069198`・8行へ進んでも出力へ混入しない |
| schema evolution | 既存ADD / RENAME後も旧Snapshotの列名・schemaで7行を出力。新Snapshotは新schemaの8行を出力。Export中のDDLは拒否 |
| 型と境界 | BIGINT > 2^53、Decimal(38)、先頭ゼロ、NULL / 空文字 / `\N`、Unicode、改行、timestamp小数秒6桁を照合。1列NULLと空Snapshotを実測。実ARRAY型はSQL送信前に拒否 |
| Snapshot取得不能・失効 | 実`expire_snapshots`で固定Snapshotを削除し、`EXPORT_SNAPSHOT_UNAVAILABLE`で失敗。latestへfallbackせず、同キーの再送POST 0 |
| Table再作成・件数不一致・部分出力 | UUID変更、COUNT差異、書込・rename・台帳保存の障害で未完了。取得を拒否し、親を確定するかUNKNOWNとして保持 |
| grant失効・復旧 | 実行中のgrant失効で元username / Authorizationの取消しを確認。旧artifactは拒否。復旧後の新しいcontextによる自分の新規Exportは成功 |
| 実Viewの正負 | 3DBともnative COUNTが7 / 13 / 1,003行で成功。同じ実ViewへのExportは`VIEW_EXPORT_NOT_SUPPORTED`、adapter POST 0 |
| ファイル・本人・profile | 0700 / 0600、別owner・別profile・別contextの拒否、byte改変の検出と復元を確認。書込後・COMPLETE前の取消しでも取得不可 |
| 実FINISHED応答の喪失 | 自動DELETE 0、再送POST 0、UNKNOWN保持。正のbackend観測と明示失敗確定を行い、完了artifactは作らない |

実機全試行のadapter POSTは139件（うちfixture用Ingestのmutating POST 50件）。保存台帳の子Query 139件と一致し、全件で親のactor / Database / Schema / Warehouse / backend usernameとの一致、backend Query IDの存在を独立確認した。Exportは57 Operationすべてread-only・`committed=false`。SQLの診断用直接読取・限定DDL / DROPはharnessで別集計し、public APIの許可拡張には使っていない。

| ソース確認 | 最終結果 |
| --- | --- |
| Export / DataOps / WAP focused regression | 29＋25＋30＝84成功 |
| `make PYTHON=tools/local_experiment/dataops/.venv/bin/python3 verify-local-experiment` | 41 suite、1,172件中1,140成功・32 skip、失敗0 |
| `make PYTHON=tools/local_experiment/dataops/.venv/bin/python3 verify-design-context` | 14成功、設計context検査成功 |
| system Python 3.9のimport互換・差分 / ファイル衛生 | 成功 |
| `go run ./cmd/shirokuma doctor --profile local-experiment --output json` | `healthy`。SQLの正負証拠は上表の実機記録で別途確認 |

## 最終台帳と清掃

検証run `c185a70f`の全69操作はFINISHED 38 / FAILED 31。FINISHEDはExport 26とfixture用Ingest 12。FAILEDには修正前のschema不一致・誤取消しの2履歴を含む。子QueryはFINISHED 136 / FAILED 1（旧schema不一致）/ CANCELED 2（正当なgrant失効取消し1、保存した旧誤取消し1）。未確定DataOps、ACTIVE / UNKNOWN admission、未確定ownerのtarget claimはすべて0。terminal ownerの再取得可能なclaim履歴は残す。

artifact台帳はCOMPLETE 26 / FAILED 28 / INTENT 3。INTENTはCOMPLETE前取消しと新旧の応答喪失で、親はすべてFAILED、取得不能のまま保持した。完了への昇格は行っていない。

実測Ref / UUIDを照合した検証用16 Table・3 Viewを清掃し、最終の独立REST確認でも全19資源が404だった。サーバーファイル27件（完了26＋測定済み未公開temp 1）を削除し、当該runの最終ファイル・temp残存は0。清掃前に本人付き取得が通った24件のCSV / JSONL証拠を保存し、独立してbyte数・digest一致を確認した。残り2件はgrant revision変更後の旧sales-only contextで取得を拒否したため、証拠コピーも作っていない。清掃後のhistorical COMPLETE receiptは残るが、sourceがないため製品getterで取得可能とは扱わない。

sales-onlyのsales grantは`data_engineer`・revision 7へ復元済み。3DBはREADY、LOCAL WarehouseはOPEN。既存P3-3の記録と資源には手を加えていない。

証拠はignored local stateに保存した。

- [実機集計](../../var/shirokuma/p3-4-acceptance/c185a70f/final-runtime-evidence.json)
- [独立最終確認](../../var/shirokuma/p3-4-acceptance/c185a70f/final-readonly-evidence.json)
- [操作・所有資源manifest](../../var/shirokuma/p3-4-acceptance/c185a70f/cleanup-manifest.json)
- [清掃検証](../../var/shirokuma/p3-4-acceptance/c185a70f/cleanup-summary.json)
- [取得済み証拠manifest](../../var/shirokuma/p3-4-acceptance/c185a70f/completed-evidence-manifest.json)

## 次の受入境界

本単位ではprivate backend APIを実engineへ接続した。通常WorkbenchのTable Export導線、通常CLI、型別capabilityの表示・日英・キーボード・遅延応答を含むbrowser縦断はP3-5で行う。現UIのExport無効化、HTTP `/api/lake/export`と旧CLIの拒否を維持し、実View ResourceRefを渡すbackend呼出も`VIEW_EXPORT_NOT_SUPPORTED`でSQL送信前に拒否する。UUIDを安定取得できないViewのRefは作らない。

P2-R01〜03の未実施、Phase 4の運用受入、所有者によるPhase 5の最終操作性評価は継続する。P3-4の技術検証をPhase 3全体の完了とは扱わない。
