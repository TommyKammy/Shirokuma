# 性能試験の準備: TPC-H実データ・snapshot・22 SQL

2026-11-14 JST。[試験計画](performance-comparison-plan-2026-11-13.md)のP1として、[接続qualification](performance-connections-2026-11-13.md)に続き、SF0.01のTPC-H由来データを永続Icebergへ隔離登録し、固定snapshotの全表・全22 SQLを照合した。今回の範囲は小規模な正しさ・再実行条件の確認で、正式TPCスコアや大規模性能の合否は扱わない。

## 実装と再実行入口

[利用手順](../../benchmarks/local_perf/README.md#persisted-tpc-h-qualification)に、生成・配布済み期待結果・SQL変換のparity・公開取込・snapshot固定・全表／query参照・両経路の実行手順をまとめた。

- 公開ingestのpreview、冪等キー、create／atomic appendを使用する。Parquetを型付きJSONLへstream変換し、実byte数で4MiB以下へ分割する。全入力を事前検査し、送信前のintent・key・fingerprintと処理結果を保存する。既存表の置換・無断採用・private hookは使わない。
- 公開ResourceRef、table profile、通常SQLのmetadata tableから、UUID・snapshot／current schema・main snapshot・metadata参照先・履歴を固定する。各入力表を`FOR VERSION AS OF`で指定し、実行前後にcontext・参照・metadata・schemaの変化を検査する。
- [22 SQLの契約](../../benchmarks/local_perf/tpch_contracts.json)を宣言し、元のSQL／hashと期待結果を保持する。AVG・比率の演算型、Q15のGROUP BY、Q12の整数範囲、Q18の列名を明示する。元のSQL・出力cast後・portable SQLを全行比較し、snapshot実行SQLと参照SQLも別hashで記録する。
- 型・全値・重複の比較と、18本のORDER BYの検査を分ける。同順位内は任意順を許容し、LIMITのある5本はN+1行で境界tieを監査する。このdatasetでは境界tieなし。別scaleでも再監査する。

取込のatomic性はchunk単位であり、8表全体の一括transactionではない。部分commit・cleanup失敗・不明な操作を成功へ加算しない。既知の処理が期限を超えた場合は、元のcommit結果を保持して所有する操作の取消・照会を行う。応答を失いoperation IDも回収できないprofileはUNKNOWNを保持する。空JSONL table等の未対応入力は送信前に拒否する。

## 実機での結果

| 検証 | 結果 |
|---|---|
| source | 専用DuckDB 1.4.3環境でSF0.01を再生成。8表・86,805行。全Parquet roundtrip、canonical 22 SQL、配布済み期待結果22件が成功 |
| portable SQL | 全22本でsource／cast／portableの全値・重複が一致。浮動値だけabs／rel各1e-8を許容。整数・Decimal・日付・NULL等は厳密比較 |
| 公開登録 | 13 chunks・28,352,734 bytesのJSONLを通常APIへ投入。13操作すべてFINISHED、計86,805行commit、POST応答喪失0件、stage登録解除の完了を確認 |
| snapshot | 8表の実UUID・ResourceRef・snapshotとschemaを固定。mainとmetadata履歴も照合。元のpinsを上書きしない |
| 全表内容 | 同じsnapshotの8表・86,805行すべてを、許可済みDBユーザーの直接Trino streamとParquet参照で照合。全件一致。COUNT／profileだけの判定ではない |
| Trino直接 | 22 SQL・計932行すべてが参照と一致。全件取得、schema、ORDER、終端footerを確認 |
| 公開SQL API | 修正反映後の新しいrunで22 SQL・計932行すべて一致。既定の10,000行上限を維持し、privateな無制限経路を使用しない |
| 接続先 | 両経路のDB専用実行ユーザー・catalog・schema・Trino 483が一致。Trino同士のidentity比較は独立engine検証と誤表示しない |
| 前後の不変性 | 全表照合、直接22本、公開22本の前後で本体8表のcontext／schema／snapshot／metadataに変化なし |
| driftの実証 | 別の所有検証表へ1行append。旧snapshotは5行、currentは6行を返し、旧pinsの検査は`PUBLIC_SNAPSHOT_DRIFT`で拒否。本体8表と元pinsは不変 |
| 重点試験 | Python 3.9.6／3.12.12で各126件中123件成功・明示実行用3件skip。実Parquetのexport／SQL parityは別途23件すべて成功。権限検査の関連130件もhostで成功 |
| 全体回帰 | `make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3`を完走。149群・5,514件成功・既定36件skip・失敗0件 |
| 診断・文書 | 実行後のdoctorは46 checksでhealthy。`make verify-design-context`、改行・末尾空白・secret filenameの検証が成功 |

Git外の採用campaignは`shirokuma-tpch-live-2026-11-14/campaign`。本体の`public-load`、`snapshot-pins`、`tables-direct`、`tpch-direct`、`tpch-public-v2`と、`final-live-summary.json`から、入力・操作receipt・pins・実行SQL・全結果・前後検査を追跡できる。drift用の別表は本体8表の件数へ加算しない。

## 実機で見つけて修正した点

最初の全表参照では、順序検証が各行で容量走査を行い、lineitemの検証中に120秒の期限を超えた。全行・全ORDER key・終端footerの検査を維持し、容量／期限callbackを256行単位へまとめた。60,175行の回帰で、遅い位置の順序違反・不完全footer・中断を検出できることも確認した。失敗した参照runはFAILED／CANCELEDとして保存し、別名で全8表を再実行した。

最初の公開runは21本成功後、Q22の標準`SUBSTRING(... FROM ... FOR ...)`を表参照と誤認し403で拒否した。標準scalar関数の同じ括弧深さにあるFROMだけを引数区切りとして扱うよう修正し、nested SELECTや後続FROMのcatalog／schema／role検査を維持した。同一profile・起動条件でWorkbenchだけを更新し、新しいrunで全22本を確認した。

旧clientはHTTP拒否の詳細を保持しておらず、元試行はUNKNOWNだった。観測した403、当時のguard再現、WALを含むread-only台帳の該当0件、backendの非終端0件をlocal operator監査で突き合わせた。元のattempt／manifest／runは不変のまま、補足記録へFAILED／REJECTEDを保存した。403の観測とsourceから導いた拒否codeを区別し、最初の失敗を残す。以後は完全な403 `SQL_PERMISSION_DENIED`を初回POSTの限定された拒否receiptとして保存し、他のエラーや不完全応答はUNKNOWNのまま扱う。

## 資源と残る範囲

campaign開始時にVMのroot／data image実割当と出力のbaselineを固定し、取消・drainへ256MiBを予約した。追加物理占有2TB・host予備256GiBの上限を維持する。VM16 vCPU／96GiB／400GiB、現在の小さなTrino構成で実行し、L1は未適用。取込stageは登録解除後も非purgeでdataを保持する仕様のため、容量解放とは見なさない。

次はTPC-DS各variantの参照・方言・結果照合、送信／queue／first・last row等の計測境界、公開APIで10,000行を超える結果の取得を整える。TPC-Hの別scaleでも、入力全件・LIMIT tie・snapshotを再検証する。G1の残件を確認してからL1／guest拡張と100GB以上、同時実行、継続試験へ進む。
