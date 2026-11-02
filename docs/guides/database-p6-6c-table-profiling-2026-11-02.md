# DB-P6-6C: Table profiling／NULL統計

2026-11-02 JST。EXP-10に続くEXP-12を通常Explorerへ接続し、この限定範囲の実装と技術受入を完了した。DB-P6-6全体とM1本人受入は継続する。

## 対象と集計範囲

認可済みTABLEの選択Snapshotについて、全行数・列数・各トップレベル列のNULL件数／NULL率を実SQLで取得する。Worksheetの行数上限やpreviewの件数は集計範囲へ流用しない。列の値そのものがNULLかを扱い、ARRAY／MAP／ROW内部のNULL要素数は数えない。空文字、文字列NULL、false、数値0はNULLではない。

min/max、distinct、sample rows、品質合否やpolicyの判定は追加しない。legacyの「行数が正なら品質合格」を流用しない。source schemaを実行contextにしてV／E／Aの現在query権限を検査し、書込み権限、SANDBOX、platform管理者を前提にしない。Viewerの既存MARTS参照制約を維持する。VIEW、別DB、sourceとprimary schemaの不一致は具体的に拒否する。

CURRENTは初回受付で整数Snapshotを一度固定する。過去SnapshotはASCII十進文字列のIDを受け取り、JavaScript Numberへ変換しない。存在しないSnapshotをCURRENTへ置き換えない。Ref・incarnation・UUID、選択Snapshotのschemaと現在schemaを固定して再検査する。同じSnapshotとschemaが残る通常のhead appendは許す。

CURRENTにSnapshotも履歴もない空Tableも実COUNTで確認する。この経路だけはSnapshot句を付けず、前後で同じUUID・現在schema・Snapshot未作成を確認し、全件数と全NULL件数が0であることを要求する。途中のappend、schema変更、同名再作成は結果を確定しない。明示Snapshotの消失を空Tableへ読み替えない。既存export／cloneのSnapshot必須条件は変更しない。

## 精度と生成SQL

serverが全列を安全にquoteし、単一SELECTのCOUNT(*)と各COUNT(*)−COUNT(column)をVARCHARへCASTして取得する。clientからSQL、列名、filter、sample件数、row limitを受け取らない。列名と型は固定したschemaの順序を維持し、引用符・Unicode・複合型を一般の入力として扱う。

row_count／column_count／null_countは十進文字列とする。engineからの件数はcanonicalな非負BIGINTとし、NULL件数が行数以下であることを検査する。NULL率はserverで正確な件数から小数2桁へHALF_UP丸めした文字列を生成する。0行では率をnullとし、UIは「—」を表示する。ブラウザーでNumberへ変換して割算や丸めを行わない。行数・型・projectionが不正なengine応答を0件や部分成功へ変換しない。

## 受付、観測と取消

`POST /api/v1/table-profiles`は単一のIdempotency-Keyと、ちょうど`context`（source schemaのfull9）、`source`（ResourceRefの4項目）、`snapshot_id`（CURRENTならnull、指定時は十進文字列）を受け付ける。

既存DataOpsの親operation・query・admission・owner leaseを再利用し、新しいstoreやoperation kind、schedulerを作らない。新規受付だけが小さい非同期workerを開始し、frozen sourceを保存して単一aggregate SELECTを発行する。同じkey／bodyの再送は元operationの観測であり、別bodyは競合とする。読取りなのでcommitted=trueや書込みcommit marker、target claim、stage所有を作らない。

状態取得と取消は既存`/api/v1/dataops/{id}`を使う。親ACTIVE・子UNKNOWNも未解決として保持し、restart、応答消失、取消失敗で新しいSELECTを自動発行しない。取消は既存owner／contextの最小応答とし、失効後に統計値を返す経路へしない。

profileの新規応答・同key replay、generic DataOps GET、POSTエラーのoperation詳細は、同じtyped inspectionで現在認可とfrozen sourceを再検査する。別owner、失効、同名再作成、schema変更、選択Snapshot消失から保存済み統計を取得できないようにする。観測GETは新しいSQLを実行せず、UNKNOWNを停止済みへ読み替えない。source変化などで統計取得を拒否する場合も、現在owner・保存full9・現在query権限を前後に検査できる範囲では、エラーに最小のid／state／child_unknownだけを付けて元受付を回収可能にする。source本文、統計、query IDはこの最小応答に含めない。account／grant／contextの失効を通すfallbackにはしない。

## 通常画面

Explorerの「プロファイル & NULL統計」をTABLEと現在query権限の条件で接続する。source、実行role、CURRENT／過去Snapshot、全行・全トップレベル列の集計を示し、明示開始で実行する。tabを開く／metadataを更新するだけでSQLを発行しない。

時間がかかる集計の状態、元受付IDの手動照会と取消を案内する。localStorageにはowner別の元key・body・受付ID・状態だけを保存し、保護された統計値は保存しない。reload後は元source TABLEを再選択して「操作を照会」し、同じ集計結果を回収する。未解決intentをnavigationや新ownerの画面へ付け替えない。

owner、role、grant／binding／generation、source、選択Snapshotが変わると統計を消去する。遅着応答や古い権限エラーが、新しい画面の統計やbusy状態を変更しない。既に確定したterminal操作に対する観測エラーは新たなSQL実行の不確実性ではなく、統計の非表示と明示的な新規集計を両立する。Snapshotの選択は元intentに固定し、変更は確定後の明示的な新規集計から行う。取消は元owner・元受付ID・許可されたheader roleを確認し、選択中のsourceやDB grantの喪失だけを理由に既存ownerの取消を隠さない。UNKNOWNは既存reconciliation条件を維持する。既存Worksheet、AI設定、clone、WAP、Table exportの状態を維持し、lineageとは未対応理由を分ける。

## 技術受入の観点

- コード：NULLなし／全NULL／混在／空、整数精度、0行の率、quoted／Unicode／nested列、Snapshot固定・head append・UUID／schema変更、現在認可、single SELECT、取消・UNKNOWN・worker喪失を確認する。
- HTTP：Workbench／nativeの実Handlerで受付・観測・取消・replayを通し、generic GETやエラー詳細からsource検査を迂回できないことを確認する。
- 実engine：2DB×V／E／A、既存MARTS、63行の専用NULL混在TABLEと空TABLE、現在／過去Snapshotで実集計と全値から求めた期待件数を照合する。元sourceへのprofilingによる変更を認めない。
- ブラウザー：通常Explorerで明示開始、日英・狭幅・200%、空TableとNULL率、source変更時消去、reloadから元操作の回収を確認する。
- 保全：fresh baselineのactive12,078行／default156行、既存全schema・UNKNOWN、12保護設定とmode、ownerの2 YAML差分を保持する。専用fixtureの準備とprofile／証拠queryの増分は個別に識別する。

## 技術受入の結果

| 層 | 結果と適用範囲 |
| --- | --- |
| 重点・独立レビュー | hostで初回profile27件／DataOps40件、最終の一般読取り名対応後にprofile30件が成功。最終UIはProfile23件・既存DB67件・DataOps38件の128件成功。公開検査中の失効で取消しないこと、元key回復、source拒否時の最小状態、owner／navのABA、Snapshot固定、現在権限、取消とUNKNOWNを確認した。重複件数は合算しない。 |
| canonical | 明示venvでMakefileから展開した108コマンドを4並列で実行。実行件数4,494中4,460成功・34 skip、全終了コード0・失敗／error0。skipの名称・理由はEXP-10と同一。profile30件・UI22件を含み、その後の型表示だけの改善は最終UI23件と実画面で別途確認した。 |
| 実engine・2DB×3role | Viewer／Engineer／adminの8件を両HTTP面で受け付け、全件が元IDのGETでFINISHED・committed=falseになった。各1 aggregate SELECTだけで、MARTSの2行／過去3行、専用Sandboxの63行／0行を全列照合。63行ではNULLなし0、混在21／42、全NULL63と、0.00／33.33／66.67／100.00%が一致した。ARRAY／MAP／ROWはトップレベルNULLだけを数え、引用符付きUnicode列も一致した。 |
| 実値の基準と範囲 | 専用fixtureは2 TABLEだけ。Aは3パターン×21の63行・11列、Bは0行・11列。CREATE2件と全行読取り2件、追加MARTS読取り3件で期待値を独立に作成した。63行はpreview 50行を超え、sampling／LIMITなしの集計範囲を確認した。元のMARTSとfixtureの全Iceberg headはprofilingで変化しなかった。 |
| 再送・拒否・取消 | 元keyによるreplay計16件と別body競合8件、さらに跨port GET／replay・他owner・保存role・別DB／schema・Snapshot／VIEW・DTO／methodなど42追加HTTPチェックが全て期待status。拒否由来のSQLは0。完了後取消2件でも台帳とsourceを保持。source検査拒否時の最小id／state／child_unknownは、現在query権限・保存full9を満たす場合だけ返し、統計やquery IDを含めない。 |
| 再起動後の読取り | 実行中0を確認して最終backendへ通常serverを再起動し、既存8件を元owner／保存context・反対portからGETだけで回収。全metrics／frozen sourceが一致し、全台帳はactive12,159行／default156行でschema・全行一致。追加POST・SQL・取消は0。実行中COUNTを強制中断する試験とは分ける。 |
| 通常ブラウザー | Profileタブを開く／contextを更新するだけではCOUNTは8件のまま。明示開始から63行・過去3行・空0行の3件を各1回実行し、11件目まで増加した。3件とも全metricsを実データ基準と照合した。0行は全列NULL数0・率「—」、品質合否を表示しない。ネスト型は内部IDを除いた論理型で表示する。 |
| 回復・scope・locale | CURRENTと過去Snapshotのreload後も元ID／keyを保持。過去ID `1695794972297287595` の選択は文字列のまま復元・固定され、元TABLEのcontext更新後に同じ3行を回収した。別TABLEでは古い統計を非表示にし、元TABLE再選択で回収。日英の件数／率を確認し、再照会・言語変更で追加COUNTは0。 |
| 幅・復元 | 日本語390pxでpage390・panel346、結果表は内部scroll318／445。英語はpanel346で、背面page408pxは本panel外の残件。実200%（dpr2／幅960）でpage960・panel716。100%／1920px、日本語、元19 Worksheetと選択SQL／A・marts・admin・read-onlyへ復元。console warning／errorは0。 |
| 保全と実行内訳 | 元active12,078行／default156行、全schema・全UNKNOWN、12保護設定bytes／mode、ownerの2 YAML差分を保持。追加はfixture登録object2、profile親16（FINISHED11／SQL前FAILED5）、COUNTリンク11、query／context各18、admission34のみ。18 SQLはfixture CREATE2＋基準読取り5＋COUNT11。新しいtarget claim、stage所有、WAP model、LLM、Wake、source変更をprofilingへ追加しない。最終GETと画面復元後も全行／schemaはactive12,177／default156で完全一致。 |
| doctor・文書 | doctorは終了コード0・healthy（45 healthy＋SQL informational1）。新しいSQL probeの証拠とはしない。design-contextは14件成功・終了コード0（documents94／issues10／references42）。89能力IDの順序とEXP-12以外の88行を保持し、差分検査も成功した。 |

この実機のTrino 483は空のCREATEでも整数のEMPTY APPEND Snapshotを作成した。Snapshot未作成分岐と長い／特殊なTABLE名はコード検証であり、実機の対象としては作成・変更していない。実Snapshot消失、実行中COUNTの強制中断、grantの実変更も誘発せず、これらの保護はコード検証と、実機の現在認可・拒否・非再実行・元UNKNOWN保全を分けて扱う。

```sh
rtk proxy make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3 DATAOPS_PYTHON=tools/local_experiment/dataops/.venv/bin/python3
rtk proxy make verify-design-context PYTHON=tools/local_experiment/dataops/.venv/bin/python3
```

EXP-12の受入をP6全体やM1本人受入へ広げず、次のTable／Explorer lineageとDropを独立して進める。89能力IDと他88行の意味を維持する。
