# UX-D1: 探索・SQLの操作改善

**状態: 製品UIの実装と限定検証を完了。P2-6以降は未着手。** P2-5までの固定2 Database構成で、共通ツリー、詳細、明示的な新規SQL、タブ別の実行roleを接続しました。DB作成、plan / apply、READYへの遷移、OpenTofu / GitOpsの変更は行っていません。

## 実装した操作

- SQL画面とデータベース探索は同じDatabase → Schema → Tables / Viewsツリー、検索、展開状態、詳細を使います。Database / Schemaにも概要を表示し、展開・検索だけでデータ行を取得しません。
- オブジェクト選択は「詳細」を開きます。「新しいSQLを開く」は完全修飾名を使った読み取り専用の新タブを作り、自動実行しません。空Schemaでは空のSQLタブを開けます。
- 「SQL名を挿入」は対象Worksheet名を表示し、そのタブのSQLだけを変更します。「このSchemaを実行先にする」は対象タブのDB / Schemaだけを、現在のroleの認可を再確認して変更します。
- 各タブに`selected_role`を保存します。ヘッダーは利用roleの上限であり、降格しても既存タブのroleを変更しません。上限外のタブは実行を拒否し、上限を戻した後も再認可を待ちます。role未保存の既存タブは初回に現在の有効roleで初期化します。
- 実行結果とnext / cancelは実行時の本人・DB / Schema・role・Warehouseに固定します。履歴はアクティブタブの入力だけを復元し、ヘッダーroleを変えず、SQLを自動実行せず、結果行を再取得しません。
- role / grant失効時は結果・詳細・履歴のローカル表示を無効化し、SQL入力を維持します。再grantしても消去済みの行を復活させず、現在の認可と明示的なコンテキスト再選択・新規実行を要求します。サーバーが改めて許可した履歴metadataは取得できます。
- UNKNOWNと元queryのcleanup pinは新規Runの可否から分けて保持します。本人変更時はrole別inventory / Schema cacheを消去します。共有タブのキーボード操作、日英切替、1024px以下でのExplorer折りたたみを実装しました。

## 確認した結果

| 区分 | 結果 |
| - | - |
| 重点UI回帰 | `test_workbench_database_ui.py` 55件成功。role別の並列取得を両応答順で確認し、同DB複数タブの失効、本人変更、UNKNOWN / cleanup、旧DOM撤去後の起動、遅延object / More選択を含む |
| 既存UI / API | Catalog 17件・refresh 15件は全体回帰に含め成功。Database APIの13件も別途成功 |
| 全体回帰 | `make verify-local-experiment` 990件中958件成功、既定の32件skip。skip対象の専用runtime受入や任意依存を今回の成功に数えない |
| 設計整合 | `make verify-design-context` 14件と文書参照検査成功。inline JavaScript構文、差分の空白検査も成功 |
| 独立レビュー | 認可・キャッシュ失効と探索選択の競合を別担当が再確認。最終確認範囲にblockerなし |
| 基盤の事前状態 | 通常環境のdoctorはhealthy。UI変更はHTMLの再読込みで反映し、サービスの再起動や基盤切替は不要 |

実ブラウザーは通常の`http://127.0.0.1:8888/`をChromeの既存本人セッションで使用しました。モックや合成DOMの結果とは分けています。

| 実ブラウザーでの操作 | 観測結果 |
| - | - |
| 既存3タブの読込み | 既存ID、SQL、DB / Schema、engineer、read-only、LOCALを保持。タブ別role選択が表示される |
| 共通ツリー → 詳細 → 新SQL | 単一クリックは詳細。閉じたDBは1回で展開。新しいSQLは別タブに完全修飾名で作成され、準備完了のまま未実行 |
| 名前挿入 / Schema適用 | 挿入はSQLだけを変更し詳細に留まる。Schema適用は現在のタブのDB / Schemaだけを変更。viewerに許可されないSchemaへの適用は入力を変えない |
| role降格と復帰 | ヘッダーviewer / 保存engineerでRun拒否。保存roleと入力は保持。engineer復帰後は再認可して実行可能 |
| タブ別viewer実行 | ヘッダーengineerのまま、SALES.martsの`SELECT 42 AS ux_d1_viewer_probe`をviewerとして実行し、実結果42と固定roleを表示 |
| 編集と履歴復元 | SQLを編集しても結果の実行時roleを維持。viewer履歴の明示復元でもヘッダーはengineer、結果行は再取得しない |
| FINANCE grant失効 | FINANCEを実行済みの既存2タブともRun拒否・結果行消去。両タブのSQL / DB / Schema / roleを保持し、SALESは利用可能 |
| FINANCE grant復元 | 検証対象の権限を元のengineerへ復元。revisionは1 → 2（失効）→ 3（復元）。旧結果行は復活せず、明示再選択と新規実行でFINANCEの行を再取得 |
| 日英 / 検索 / キーボード | 詳細と検索語を維持して日英切替。検索不一致を表示。Home / 矢印キーで詳細とWorksheetを切替 |
| 狭幅 | 900pxでExplorerをキーボードで展開・折りたたみ可能。編集領域を維持し、ページの横溢れなし。検証後にviewport設定を解除 |

実装中に見つかった旧tree DOM参照による起動失敗、role別認可の応答競合、複数タブのgrant変更判定、本人変更時のcache残留、古いobject / More選択による対象混在を修正し、対応する回帰を追加しました。

## 証跡と境界

コード変更は`tools/local_experiment/workbench/index.html`と`tests/test_workbench_database_ui.py`に限定しました。バックエンドの認可・QueryContext・Operation契約、runtime profile、OpenTofu / GitOpsを変更していません。元の計画書には今回の実装状態を反映し、設計用の元モックHTMLは変更していません。

製品では既存の探索コントロールを共通Explorerへ集約し、Schema概要を開く「Schema」と「このSchemaを実行先にする」をそこへ置きました。元モックのSchema概要・パンくず内の配置とは表示位置が異なりますが、選択Schemaと対象Worksheetを明示し、認可後に当該タブだけを変更する契約は同じです。

ログ、ブラウザーで観測した入力・結果、検証用grantの復元記録、画面画像、開始時との差分範囲はGit対象外の`var/shirokuma/ux-d1-implementation-20261020/`に保存します。資格情報やSQLiteのprivate材料は公開文書へ記載しません。

残る作業はP2-6の別受入です。任意名の3番目DB、空DB bootstrap → SP取得 → full plan、候補全体のAPPLIED / READY、再配備・空環境再構築は今回実施していません。admin上限・本人切替・通信順序・UNKNOWN / cancelの境界と空Schemaは重点回帰で確認し、今回の実ブラウザー受入はengineer本人とviewer選択、固定2DBに限定しています。Windows / Power BI、一般公開、全業務や所有者による操作性の受入を完了した判定ではありません。

検証終了時にMacロックで保留した片付けは、所有者による解除後に完了しました。生成した検証用Worksheet 4 / 5だけを閉じ、既存3タブのID・SQL・DB / Schema・保存role・Warehouse・mode・ヘッダーroleが開始時と一致することを実ブラウザーで最終確認しました。比較結果を`browser-cleanup-final.json`、共通ツリー・詳細・既存3タブの最終画面を`browser-final-desktop.png`へ保存しました。失効検証用grantは復元済み、viewport設定も解除済みです。今回の保留作業は残っていません。P2-6以降には進んでいません。

## デザイン調整：UX-D1モックとの整合（2026-10-20）

元モックを比較基準として、Database modeの探索・SQL画面の配置と密度を揃えました。操作契約・認可・取得制御のコードは変更していません。

- 探索：検索と更新を先頭、Database / Schema / Tables・Viewsのツリーを残りの高さへ置き、種類アイコン・字下げ・選択背景・focusを統一。DB / Schemaの一覧選択と再選択は下部の探索コンテキストへ移動。
- 作業領域：「詳細」とWorksheetを1本のタブ列にし、履歴は結果の下へ移動。詳細はパンくず・名前・SQL名の後に「新しいSQLを開く（主操作）／SQL名を挿入／このSchemaを実行先にする」を置き、未提供のClone / Exportは末尾にまとめて無効表示。
- SQL：実行コンテキスト（DB / Schema / 実行role / mode / Warehouse / 最大行数）をエディター上、取消と実行を下へ。実行状態は文字と色で示す。1024px以下は探索を上部の折りたたみ行にする。

| 確認 | 結果 |
| - | - |
| 実ブラウザー（通常8888、所有者のChromeセッション、1440 / 900px、日英） | 単クリックは詳細、新しいSQLは別タブで未実行・完全修飾名・read-only、挿入は対象タブのSQLだけを変更。Schema適用は対象タブのDB / Schemaと対応するbinding / generationだけを変更し、SQL・保存role・mode・Warehouseと他タブは維持（今回は同一DB内のSchema変更で確認）。viewer降格でRun拒否・保存role維持、復帰で再認可。履歴復元は対象タブだけでPOSTなし。キーボードで探索の展開・選択とタブ移動。横溢れなし、console errorなし |
| 既存タブ | 開始時と終了時でworksheets v2 / v1・履歴のハッシュ、ヘッダーrole、言語が一致。検証で追加したWorksheet 4だけを閉じた |
| 自動テスト | Database UI 55、Catalog UI 17、refresh 15、Database API 13、Workbench 98件成功。`make verify-local-experiment` 990件中958件成功・32件skip、`make verify-design-context` 14件成功。inline JS構文・差分空白検査成功 |

独立再レビューで、15秒周期の権限確認がパンくずを作り直し、キーボードのフォーカスが外れる不具合（P2）が見つかりました。同じパスではパンくずのDOMを保持し、パスが変わった場合だけ同じ項目へフォーカスを戻します（消えた項目には戻さない）。DBのパンくずは押した時点の最新inventoryで動作します。周期更新後もノードとフォーカスが変わらないことを回帰テストに追加しました。

意図して残した差：モックの権限プレビュー、合成データ、件数表示、行番号付きエディター、ヘッダーのWarehouse選択は製品へ移していません（件数は取得しない契約、Warehouseはタブごとの選択）。DB / Schemaの一覧選択は、ツリーを使わない操作の代替として折りたたんで残しています。モックはfile://を自動ブラウザーで開けないため、ヘッドレスChromeで画面画像を取得して比較しました。画像と開始・終了時の状態は`var/shirokuma/ux-d1-alignment-20261020/`（Git対象外）にあります。操作性の最終評価は所有者の確認に残します。P2-6以降は未着手です。

## ナビゲーション整理（2026-10-20）

- 「データを扱う」の先頭を「データ探索 / Data Explorer → SQL ワークシート」とし、単独のCatalog見出しを削除。Legacy modeは独立した探索画面の動作を維持します。
- 共通画面の表示中paneからメニュー選択・tabIndex・ARIA参照・見出しと説明を同期。ツリー、詳細 / Worksheet、新規SQL、履歴の各導線に追従し、別画面の背景更新では選択を奪いません。再読込時は従来どおり保存済みWorksheetへ戻ります。
- Chromeの通常8888で日英・Home / End / 矢印キー・Jobs表示中とRAWパンくずの周期更新・新規SQL / 履歴を確認。既存3タブのSQL・DB / Schema・保存role・mode・Warehouseを保持し、viewer上限への降格では保存engineerを変えず実行を無効化、復帰後の再認可で実行可能へ戻ることを確認しました。
- 独立重点テスト223件、全体チェック996件中964件成功・32件skip・失敗0件、design 14件が成功（runtime opt-inは無効）。inline JS構文・対象差分の空白検査も成功し、全体チェック前後の製品HTML・対象テストは不変です。既存の認可・保存・SQL実行処理は変更せず、独立レビューで未解消の指摘はありません。画面画像と検証記録は`var/shirokuma/ux-d1-navigation-20261020/`に保存します。P2-6以降は未着手です。
