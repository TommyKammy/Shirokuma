# DB-P6-3C2C native RunTaskと全graph実行

2026年10月31日。[C2B5](database-p6-3c2b5-native-publication-2026-10-31.md)の単一native TABLE実行に続き、対応する保存済みgraph全体をinstalled dbtの通常RunTaskで実行する。この限定範囲の技術受入を完了した。

## 選択と対応範囲

Database dbtで対応プランを保存し、実行方式としてNative graphを明示選択する。API／CLIは`executor: native_graph`を指定し、単一nodeの指定を受け付けない。SQL/WAPの既定と単一`native_table`の既存body／keyは維持する。

対象は、TABLE／EPHEMERALからなる保存済み全モデルのDAGである。物理TABLEは固定PARQUET、RAW sourceは同じDatabase内、出力は実行ごとのSANDBOX候補とする。非対応materializationを含むgraphは実行前に理由を示し、全体を拒否する。対応nodeだけを黙って選んだり、VIEWをTABLEへ暗黙変換したりしない。

画面には全モデル数・TABLE数・依存関係と利用条件を表示する。`graph_options`は保存metadataの読取りだけで返し、現在の実行許可や実行成功とは分ける。品質テストは`native_tests: true`の明示指定とし、指定時は保存された非空のgeneric test一式をTestTaskで実行する。source testや別TABLEを参照するrelationshipsも含み、依存に現れるだけのtestから所有関係を再推定しない。

## 実行と記録

一つの隔離SDK processで実際の`RunTask.run()`を使い、dbt自身のselector・queue・runner・依存失敗処理を通す。初期設定は直列実行である。単一ModelRunnerを親が順に呼ぶ方式を、通常RunTaskとして扱わない。

private manifestでTABLEの出力先を固有のSANDBOX名へ割り当て、raw SQLの再compileとEPHEMERAL CTE展開をdbtへ任せる。SDKの実資格情報やDBAPI接続を使わず、必要なSchema確認・relation cacheを許可された範囲のmetadata操作へ接続する。SQLの実送信は元本人・全9項目context・固定資格情報を持つ親DataOpsが行う。

RAW Snapshotはrun開始時に固定する。TABLE依存は同じrunで検証した候補Ref／UUID／incarnation／columns／Snapshotへ結び付ける。SDKのSQLを保存プランと構造比較し、親が保存済みSQLと固定bindingから実行SQLを再構成する。下流が既存MARTSやlatestへ戻ることを許可しない。

TABLEごとにCREATEの子操作・queryと候補measurementを保存する。EPHEMERALはEXPANDED、依存失敗による未実行nodeはSKIPPEDとし、これらに空の子操作を作らない。TABLE作成とRunTaskの完了証拠、TestTaskの品質結果を分離する。全CREATEが終わっていても、SDKの正のTask完了が確認できなければgraph成功にしない。

取消・SDK停止・実行元停止・UNKNOWNでは、作成済み候補と確認できた証拠を保持する。同じkeyの回収、GET、reload、履歴、reconcileは観測だけを行い、残ったnodeを再開したり新しいSDK／SQLを送ったりしない。

## 公開と後続範囲

graph由来の候補の正式公開は、graph単位とモデル単位の証拠を公開gateへ接続する後続単位とする。今回のgraph runは`publication_allowed=false`である。C2B5の単一native TABLEの明示MARTS公開と、既存SQL/WAPのTABLE／EPHEMERAL／SANDBOX INVOKER VIEWを維持する。

native VIEW・incremental等の追加materializationと並列実行は別の対応範囲である。DB-P6-3全体とM1本人受入は未完了である。

## 検証結果

証拠の保存先は`var/shirokuma/db-p6-3c2c-2026-10-31/private`である。

- 実CLI／E／DB B：休止0/0から透過復帰し、6 TABLEと保存全10 testを実行した。実RunTask／TestTaskがともに成功し、TABLE間依存・深さ3のgraph・source test・relationships・pure helper macro・aliasの再割当てを通した。同じGo CLI要求の再照会は同じrunを返し、SQLを追加しなかった。
- 実画面／A／DB A：3 TABLE＋3 EPHEMERALのRunTaskが成功し、TestTaskは全10件を完了した。9件成功、accepted_valuesだけ失敗1件である。元RAWを新たにSELECTし、orderstatusがF/O/X各1行であることを確認した。許容O/F/Pに対する既存Xの検出であり、RAWを変更して成功に見せない。graph構築検証済みと品質FAILEDを別表示・保存し、正式公開は不可のままとした。
- 実SQL失敗：無関係の6 TABLEは成功し、失敗branchのSDK結果はerror、依存leafはSKIPPEDとなった。SKIPPEDの子操作は0で、TestTaskは起動しなかった。
- 途中取消／実行元停止：最初のTABLE検証後、次の物理nodeへ入る前に停止した。各1 child／1 CREATEを保持し、元keyの回収・reconcileでSQLは増えなかった。
- 実APIの他DB・viewer・別所有者・graphのnode指定・非bool品質指定・graph公開preview／publishの7境界を拒否した。graph_optionsの反復GETを含め、子操作／SQL／admissionは増えなかった。
- 作成候補を実際にJOINし、AはALPHA、BはBETAの結果と元本人／全9項目context／read-only modeを照合した。JA/EN、390pxの横はみ出しなし、viewerでmodel SQL／候補／test／run／公開情報の消去、元runのreload・履歴復元を確認した。後者の画面上の参照・reload・履歴操作ではSQL／intent／admissionは増えず、元のSQL下書きと13 Worksheetを保持した。

実機で見つかった親metadata処理時間のSDK待機予算への混入を修正した。真のSDK無応答timeoutは維持し、idle pollingだけ候補catalog scanを省略する。SQL callbackと実送信直前の厳密な検査は維持する。最初の失敗runと6候補を保存し、元keyを再実行せず、新しい明示要求で修正後の成功を確認した。通常のSQL失敗を不正Task記録と誤分類する箇所も修正した。未commitのFAILED query・元のcontext／資格情報・admission閉鎖を照合し、CREATE成功後のSDKエラーと区別する。新しい明示要求の実機CREATE失敗は`DBT_GRAPH_BUILD_FAILED`となり、全RunTaskのerror／skipped証拠を保存、品質opt-inでもTestTaskを起動せずERRORとし、元key回収・reconcileの追加SQLは0だった。

`make verify-local-experiment`の現行85コマンド群を段階実行し、全群を通過した。先行76群・後半8群に続き、graph群の新しいassertionでbackend query IDの参照先を訂正し、最終版の33件を全再実行した。合計3,413件のうち3,379件成功、既存skip34件。Go全体・design-context・改行／空白・diff検査も通過した。graph群をMakefileの正規経路へ追加している。

独立したledger／SQL AST／Catalog照合で、8個の新規runに対応する47 child／query記録と23個の検証済み候補、Taskのselected／nodes／results、本人・全9項目context・固定資格情報・admission親子関係・RAW／同run上流候補のSnapshotを確認した。受入用fault processでprovider lifetimeを付け忘れた最初の要求はbackend送信前にFAILEDで閉鎖され、元記録を保持した。修正した独立の故障注入要求で取消／owner停止を確認しており、この準備失敗を故障注入成功と数えていない。schema macroが欠けた一時projectのcompile拒否も保持し、通常設定をコピーした新planで最終のSQL失敗確認を行った。

元の141 DB／564 Schema、141 catalog binding／141 logical binding、権限・identity・資格情報・policy、全WAP行と旧dbt／DataOps行を保持した。B5公開先を含む元22 RAW／MARTS Tableのheadは変えていない。新規の未閉鎖は0、従来のFENCED_READ_ONLY UNKNOWNは2件のままである。意図したローカルYAML差分2件とruntime profileも変更していない。

Workbench8888／SQL endpoint8080を通常processへ戻し、一時project登録を解除して両方とも`lake_marts`だけを返すことを確認した。doctorはhealthy（46チェック）、LOCALは2/2、AutoChillはminutes=0／revision=5を保持する。主な証拠は`final-proof.json`、`test-summary.json`、`normal-restored.json`と各runの元要求・完了記録である。
