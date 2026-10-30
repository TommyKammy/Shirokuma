# DB-P6-3C2B3 画面からのnative TABLE実行選択

2026年10月30日。[C2B2](database-p6-3c2b2-native-runs-2026-10-30.md)の保存run／API／CLIを、Workbenchの明示選択へ接続した。この限定範囲の技術受入を完了した。

## 操作

データ変換のDatabase dbtで、元本人のDatabase・実行ロールとSANDBOX contextを選び、保存プランを開く。実行方式の既定はSQL／WAPである。Native TABLEを選ぶと対象モデルと利用条件を表示する。対象モデルは自動選択せず、実行可能なTABLE 1件を選んで「Native TABLE を実行」へ進む。

nativeはRAWの元TABLEと展開済みEPHEMERALだけを読む固定PARQUETのTABLEを対象にし、同DBの新規SANDBOX候補を作る。テスト未実行・正式公開不可である。モデルごとの不適合理由を表示し、対応モデルが0件なら選択・実行を止めて互換性のあるプランのコンパイルを案内する。設定やmaterializationを自動で書き換えない。

project／source／公開先Schemaの入力は次のcompile用で、方式・モデルは選択中の保存プランへ適用する。別のplan／context／roleへ移ると選択と適否情報を破棄する。保存runの方式・対象・状態を、次回実行用の入力値で読み替えない。

受付中と受付結果不明の間は方式・モデル・元body・keyを固定する。「元の受付を照合」は同じ要求だけを回収し、GET・reload・履歴・direct linkでは実行しない。UNKNOWN／実行中では元runの照会・照合を優先し、新規実行の準備へ誘導しない。

## 判定と互換性

認可済み`GET /api/v1/dbt/plans/{id}`のenvelopeに、保存planとは別のversion 1 `run_options`を返す。plan ID／source digest、native executor、全体と各nodeの適否／固定reason codeを含む。画面はこの情報を表示し、欠落・不明version・別planの情報を使って実行を許可しない。

元actor／全9項目context、READY v2／digest／source identityと、TABLE／PARQUET／依存閉包／使用bindingのRAW条件を、既存native POSTと共有する。projectionはSDK・compile・資格情報capture・Snapshot pin・runtime／REST probe・新admissionを呼ばず、保存record・plan hashへ追加しない。既存のcompile owner喪失時のmetadata回収は維持する。

実行可否は保存プランの対応条件であり、実行時のproject／権限／source検証を省略しない。旧v1は従来のSQL／WAPを利用でき、nativeだけ再compileを案内する。adminの閲覧権限を、別actorのnative実行権限へ広げない。

SQL／WAPの既存POSTは従来の`plan_id`だけを保持する。nativeでは明示したexecutor／nodeを凍結し、ACKのexecutor／selected nodeも照合する。異なるACKを採用せず元intentを保持する。API／CLIの入力と、WAPの品質・正式公開gateは変更しない。

## 確認結果

実機証拠は`var/shirokuma/db-p6-3c2b3-2026-10-30/private`へ保存した。通常Workbench 8888を使用し、実行受付は画面から行った。

| 観点 | 結果 |
| --- | --- |
| 対応条件 | プロジェクト既定の3 VIEWを含むプランでは、対応モデル0件の理由を表示し、対象選択・native実行を禁止。EPHEMERALを明示したプランではTABLE 2件が選択可能で、初期選択は空欄。設定の自動変更は0件 |
| 実native実行 | admin本人が2DBでdim_customersを明示実行し、各1 child／1 native-createでNATIVE_VERIFIED。元DBのbackend identity／資格情報参照、PARQUET、実TABLE UUID／Snapshot、使用source pinを照合。候補のALPHA／BETAを実SQLで確認 |
| 休止復帰 | 共有LOCALを0/0へ休止後、画面からのnative実行で透過復帰。最終2/2、Auto-Chill 0分・revision 5を保持 |
| 既存経路 | 既定のSQL／WAPも画面から実行し、3 VIEWと下流TABLE、全testがREADY。12 childが完了し、正式公開は行っていない |
| 本人と権限 | adminが別本人のREADYプランを参照してもnative実行不可。role変更で方式・node・適否情報を破棄し、許可DBだけを再表示。viewerではSQL・モデル・runの内容を消去し、実行を禁止。実engineのdata_engineer本人経路はC2B2の別証拠であり、今回の画面成功2件はadmin本人である |
| 観測と画面 | 日英、390px幅の折返し・横overflowなし、Tabのfocus移動、直接リンクの初回読込／reloadと履歴を確認。これらの参照・選択操作による追加の受付／child／query／admissionは0件。元のadmin・日本語・通常viewport、13 Worksheetと表示中SQL下書きを保持 |
| 読取projection | 同一READY planのGETを4回反復し、保存record・受付・child・query・owner／admissionが不変。compile担当process喪失時の既存回収は別に回帰テストで維持 |
| 保全 | 141 DB／564 Schema／各141 binding、既存grant・identity・資格情報・password・file ACL・OPA guard、profile／host固有YAML、RAW／MARTS 20 Tableのheadと全legacy rowを保持。WAP publicationは不変。新規未閉鎖admission／UNKNOWNは0、元FENCED_READ_ONLY UNKNOWN 2件を保持。doctorはhealthy・46 check |

焦点テスト166件が成功した。不確かな受付、異なるACK、元key／bodyの回収、stale応答、旧SQL／WAP payloadは意味のあるfrontend／backend回帰テストで確認した。今回の実機で通信障害を新たに注入したとは扱わない。

`make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3`は84 suite・計3,315件を完走し、3,281件成功・既存skip 34件・失敗0件だった。Go、設計contextと書式・秘密file名の検証も成功した。コードと7文書の独立レビューで必須修正は残っていない。

同じtabでURLのhash部分だけを変更した場合は、既存の起動時復元の制約により再読込が必要である。画面内の履歴選択は通常のinspect処理を使う。hash遷移時の復元は別課題として残す。

品質テストは後続の[C2B4](database-p6-3c2b4-native-quality-tests-2026-10-30.md)で選択TABLEの付属suiteを扱う。RunTask／graph全体、置換・rename／drop、native VIEWとnative成果物の正式公開は引き続き後続である。DB-P6-3全体とM1本人受入は未完了である。
