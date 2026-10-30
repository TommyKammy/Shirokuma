# DB-P6-3C2B4 native TABLEの品質テスト

2026年10月30日。[C2B3](database-p6-3c2b3-native-ui-2026-10-30.md)の明示選択に、選択モデルの品質テストを追加する。この限定範囲の技術受入を完了した。

## 操作と範囲

Database dbtで新しい対応プランをコンパイルし、Native TABLEと対象モデルを選択して「品質テストを実行」を指定する。APIは`native_tests: true`、CLIは`--native-tests`を明示する。品質テストを指定しない既存のbody／keyと、過去のNOT_RUN履歴は維持する。

対象は、そのモデルに付属する非空のgeneric test一式である。依存関係に現れるだけの別モデルのtestを集めない。別のTABLE／VIEWの構築が必要なsuiteや対応外の設定は、実行前に理由を表示して拒否し、一部を黙って省略して成功にしない。過去プランに付属先の情報がない場合は、品質テスト用の再コンパイルを案内する。

同じ新規runでTABLEを1件作成し、その候補に対してinstalled標準TestRunnerとtest materializationを実行する。過去runへの追加test、full RunTask／graph、native VIEW、置換と正式公開はこの単位に含めない。

## 保存と結果

TABLE作成の検証と品質結果を区別する。作成済み候補のRef／UUID／incarnation／Schema／SnapshotとRAWの元pinを固定し、testはそれらを読む。候補のheadが変われば検証を止める。SDKには実資格情報を渡さず、親DataOpsが元本人・全9項目context・資格情報参照とadmissionを維持する。

標準testの三列一行のSELECTだけを型付き境界で検査し、親が承認済みtest本体からSQLを再構成する。SELECT成功時の失敗件数とRunnerの結果を照合する。失敗件数0の全testの検証が揃った場合だけ品質成功とし、backend／SDK error、取消、owner喪失、結果不明を成功にしない。品質に失敗しても、証明できたTABLE作成と候補の情報を保持する。

品質完了前はrun全体をTESTINGとして観測し、途中で終端成功にしない。受付結果が不明な間は、品質指定も元のbody／keyに固定する。GET／reload／履歴や同keyの回収でSDKやSQLを再実行しない。品質成功後も`publication_allowed=false`である。

## 確認結果

証拠は`var/shirokuma/db-p6-3c2b4-2026-10-30/private`へ保存した。通常Workbench 8888／native HTTP 8080／再ビルドした実CLIと、専用の短命な障害注入processを使用した。

| 観点 | 結果 |
| --- | --- |
| APIとsuite | E本人のDB Aで2 testがPASSED。DB Bの独立した承認済みprojectではNULL候補がFAILED（失敗件数2と0）。source-attachedのowner=None testがあっても、選択TABLEに付属する2件だけを実行。testのないTABLEと別モデル依存suiteは受付前に拒否 |
| 画面 | admin本人が2 testを明示指定してPASSED、失敗件数0を表示。受付競合は品質指定・node・元keyを保持して同じ要求から回収。旧planはbuild-onlyを維持し、品質用の再compileを案内。失敗runもTABLE検証済みと品質の失敗件数2を区別して表示 |
| CLIと休止 | 実CLIの`--native-tests`をnative HTTP 8080へ送り、0/0から透過復帰してPASSED。元keyでの再照会は同じrunだけを返す。最終2/2、Auto-Chill 0分・revision 5を保持 |
| SDK停止 | test SELECT完了後にSDKを停止し、run=FAILED／品質=ERROR。完了SELECTの件数だけで成功にせず、作成済みTABLEの証拠を保持 |
| 取消 | test SDKを無応答にして元runを取消。run=CANCELED／品質=CANCELED、test SELECTは0件。子processを回収し、buildの証拠を保持 |
| 実行元停止 | test SELECT後に実行元を終了。元IDの観測でrun=FAILED／品質=UNKNOWNを保持し、再SQL・品質成功への昇格なし。所有していたtransportだけを確認して終了 |
| 認可・公開 | viewer、別本人、別DB、不正bool、SQL/WAPでの品質指定、空suite、他materialized依存、旧plan、同keyの品質指定変更を拒否。品質成功後もE／Aの正式公開を拒否。保存済み拒否keyの逐次照合で受付／child／query／admissionが不変 |
| 物理証拠 | 新native 7件は各1 CREATE、test SELECTは計10件。test子は各0または1 SELECTだけで、保存suite以外のchildなし。保存台帳・SQL AST・Catalog metadataから、元actor・全9context・backend identity／資格情報参照・候補UUID／Snapshotとpinを独立照合。新しいSELECTでAのALPHA／BのBETAを確認 |
| 互換・保全 | 旧native NOT_RUNとSQL/WAPを元keyで回収し、追加SQL 0件。141 DB／564 Schema／各141 binding、全legacy／WAP row、RAW／MARTS 20 Tableのhead、既存grant・identity・資格情報・password・ACL・OPA guard・profile／host固有YAMLを保持。新規未閉鎖admissionは0、元FENCED_READ_ONLY UNKNOWN 2件を保持。品質UNKNOWNは未閉鎖admissionと区別する |
| 表示とテスト | 日英、390px幅で横overflowなし、viewerでSQL・モデル・test・runの消去と品質操作禁止を確認。凍結body／key、異なるACK・stale情報・旧payload、候補head変更・RAW pin、全test保存後の取消／owner喪失は焦点テストでも確認。候補変更／RAW追記を今回の実機で新たに注入したとは扱わない |

コンパイラ19件、native 52件の一式と所有関係の追加1件、ワークフロー71件、route 7件、Python CLI 14件、UI 60件が成功した。Goと設計・書式検証も成功した。

`make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3`は84 suite・計3,341件を完走し、3,307件成功・既存skip 34件・失敗0件だった。コードと7文書の独立レビューで必須修正は残っていない。

検証手順では、旧CLIバイナリの送信前エラーを再ビルド後に元keyで回収した。拒否検証の全体件数比較が障害注入と重なったため、保存した拒否要求を同じkeyで逐次再照合した。これらを新しい実行要求へ置き換えてはいない。

通常サーバーは検証projectの一時登録を外して復元し、doctorはhealthy・46 checkだった。直接リンクの初回読込／reloadと履歴は参照だけを行う。同じtabのhashだけの変更には既存どおり再読込が必要である。

DB-P6-3全体とM1本人受入は未完了である。

2026年10月31日の後続[C2B5](database-p6-3c2b5-native-publication-2026-10-31.md)で、品質成功済みnative TABLEの明示的なMARTS公開を追加した。本書の公開不可はC2B4受入時点の状態を記録している。
