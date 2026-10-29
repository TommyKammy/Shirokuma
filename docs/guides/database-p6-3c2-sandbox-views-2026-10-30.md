# DB-P6-3C2A run専用SANDBOX VIEW

2026年10月30日。C2Aの実装と技術受入を完了した。C1の設定互換性に続き、SQL/WAP executorで実体のあるSANDBOX VIEWを扱う。標準dbt executor、MARTSへのVIEW公開、DB-P6-3全体とM1本人受入はこの単位へ含めない。

## 実機で確認した成立条件

現在のTrino 483／Polaris 1.8で、専用DBの新しいSANDBOXオブジェクトだけを使った。E本人の当該DB用backend identityで、次を確認した。管理者の成功をEの証拠へ流用していない。

- `CREATE VIEW … SECURITY INVOKER AS SELECT … FROM <TABLE> FOR VERSION AS OF <snapshot>`が成功し、REST catalogにview UUID、current-version-id、schema、Trino SQL representation、metadata-locationが保存された。`SHOW CREATE VIEW`でもINVOKERを確認した。
- 実体のあるVIEWを読むPARQUET TABLEのCTASが成功した。新しい基底TABLEへ追記してもVIEWは元Snapshotの1行を読み続けた。
- probe VIEWの置換でversionとmetadata-locationが変わり、最新Tableを読む新しい定義では2行へ変わった。
- A DB用のE identityからB DBのVIEWを読む操作と、B DB用viewer identityからSANDBOX VIEWを読む操作は、実backendでPERMISSION_DENIEDになった。
- VIEWへ数値の`FOR VERSION AS OF`を指定するとNOT_SUPPORTEDになった。VIEW versionをTable Snapshotへ流用しない。

このprobeはoperatorによる実行可能性検証であり、製品API・UI・取消／復旧の受入とは別の証拠である。各SQLは送信前intentとbackend ID・終端結果をprivateに保存し、同じintentの再送を拒否した。実行中は既存のwarehouse admissionとlifecycle gateを使った。証拠の場所は`var/shirokuma/db-p6-3c2-2026-10-30/private`である。

## 製品契約

VIEWはrunごとの一意名でSANDBOXへ一度だけ作成する。固定構文はSECURITY INVOKER、OR REPLACEやDEFINERへの代替は使わない。元TABLEのRef・Snapshot・schemaをrunの開始時に固定し、VIEW定義のTABLE参照へ埋め込む。下流TABLE候補とgeneric testは、この実体VIEWを参照する。ephemeralへ変換してVIEW対応とは表示しない。

VIEWの測定値はTABLE候補のSnapshotと分ける。保存Ref、view UUID、version、schema・columns、定義digest、securityとmetadata headを照合する。VIEWのmetadata-locationは内部に保持し、公開応答へ出さない。VIEW参照には通常のFQNを使い、TABLE参照だけにSnapshot句を付ける。この読取りbindingは当該runの型付きdbt操作に限定し、独立した手動WAPや通常SQLの許可範囲を広げない。

互換性契約はversion 2とし、VIEW modelに適用済み`view_security=INVOKER`を保存してplan digestへ含める。VIEWのpropertyと明示DEFINER、VIEW→VIEW依存は理由を示してcompile時に拒否する。未指定のVIEWはこの固定INVOKER方針で扱う。C1のversion 1 TABLE／EPHEMERAL plan、受入済みrun／公開・元key回収を維持する。契約のない旧planは従来どおり新規run前に再compileを要求する。

下流build／testの直前と完了後、公開前にVIEWの保存測定値を検査する。途中で定義・UUID・head・securityが変われば、生成済み候補を保持してもrunは失敗・公開不可とする。version番号だけで同一と判断しない。VIEW自身にはTrinoの数値version指定がないため、定義変更を任意時点へ読み戻す機能とは区別する。

公開先は従来どおりTABLEだけである。EがVIEWと候補を構築し、全test成功後にAが独立したadmissionと資格情報で明示公開する。MARTSだけを読めるviewerへRAW／SANDBOX権限を加えない。一般公開VIEWとDEFINER所有者の契約は残件とする。

現環境ではEのVIEW作成は既に許可されていたが、DB内AのOPA規則には`CreateView`がなかった。A本人のcatalogかつSANDBOXだけに同操作を追加した。`DropView`やDEFINER用の`CreateViewWithSelectFromColumns`は追加しない。現行と一致する保存policy本文へ同じ規則だけを加え、既存Providerのleaf intent／ACK・CAS公開・全compute replica検証で適用した。資格情報の再発行は行わず、credentials／password／file ACLのbytes、identity／grant epoch、runtime profileを保持する。AによるSANDBOX作成の成功とRAW／STAGING／MARTSへの拒否を実backendで確認した。

CREATEの前に既存DataOps childとquery intentをtransactionで関連付ける。取消・応答喪失・owner停止は元operationの記録と既知backend／catalog metadataから照合し、CREATEを再送しない。終端前にVIEWができた場合も、残存artifactと成功したrunを区別する。GET・reload・restartを未実行SQLの開始契機にしない。

## 上流仕様との関係

[Iceberg view仕様](https://iceberg.apache.org/view-spec/)では、view UUID、version、schema、SQL representationとmetadata fileの切替を扱う。これはTable Snapshotとは異なる。
[Trino 483のanalyzer](https://github.com/trinodb/trino/blob/483/core/trino-main/src/main/java/io/trino/sql/analyzer/StatementAnalyzer.java#L2189-L2197)もVIEWの数値version指定を拒否する。
[Trino 483のREST view実装](https://github.com/trinodb/trino/blob/483/plugin/trino-iceberg/src/main/java/io/trino/plugin/iceberg/catalog/rest/TrinoRestCatalog.java#L627-L759)はTrino SQL representationとrun-as-owner propertyを扱う。これらの静的根拠に加え、上記の現環境probeを確認した。

標準dbt executorは引き続き別設計である。nativeのrename／drop／introspection／Schema操作、親child関連付け、資格情報固定、取消・失効・owner停止を統合するまで、通常dbtのDDLを無制限に開放しない。

## 製品受入

同梱projectを複製し、公開先aliasだけを変えた承認済みの一般projectで確認した。実行処理にfixture専用分岐はない。元のRAW／MARTSと利用者のWorksheetは検証用に書き換えない。

| 経路 | 確認結果 |
| --- | --- |
| 通常API | Eが既定の3 VIEW・2 TABLE候補を構築し、7/7 test成功。実catalogの3 VIEW定義に元TABLE Snapshotが埋め込まれ、A公開後も同じheadだった |
| 公開 | Eの保存結果からAが独立したadmissionと資格情報で2 TABLEを公開。正式データと実DDLのPARQUETを照会。E自身の公開、VIEW nodeの公開、失敗runの公開を拒否 |
| native CLI・2DB | 他DBの同名モデルでも3 VIEW・2 TABLEを構築。不正なorderstatusをaccepted_valuesが1件検出し、6成功・1失敗でrun FAILED、公開不可 |
| compile前提 | 明示DEFINERと未対応VIEW propertyは固定codeでblocked。run／child／SQLへ進めない |
| 画面 | Aが既定VIEWのcompile・buildを実行し7/7成功。VIEW version／INVOKERとTABLE Snapshotを独立列で表示し、Ref・操作IDをnative detailsで開閉。保存リンク・reload、日英、keyboard、390pxでのbody横overflow 0を確認。viewer上限では診断・SQL・DB選択肢を消去 |
| 観測失敗・取消 | CREATE後の測定失敗はFAILED、取消はCANCELED。既知の作成済みVIEWを残し、GET／照合後も元query 1件から増えず、未閉鎖admissionなし |
| 処理中の変更 | 下流CTASが終わった直後にrunのVIEWを置換するとDBT_VIEW_CHANGEDでFAILED。候補を成功／公開扱いせず、GET／照合からSQLを再送しない |
| 実owner停止 | CREATE完了・測定前に隔離serverを`os._exit`で終了。別processからDBT_OWNER_LOSTへ回収し、query 1件・VIEW残存・再送なしを確認。記録した当該processのtransportだけを終了 |

generic DataOps／WAP HTTPでもVIEW測定値の非公開digestを除去する。保存した内部WAP recordは完全一致比較に使い続け、正常な`_source` aliasや`_id`監査列は応答で保持する。通常8888／8080は一時project設定を外した既定構成へ戻した。

最終比較で、141 DB／564 Schema／各141件のcatalog・logical binding、既存grant／identity／作成記録と全legacy dbt／WAP row、RAW／MARTS 16 Tableのmetadata head、runtime profileと既存host固有YAMLのbytesを保持した。元サンプル2行と13 WorksheetのID・選択状態・選択中SQL／contextも不変だった。role変更・reload後も画面runのchild SQLは12件から増えない。元のFENCED_READ_ONLY UNKNOWN 2件を保持し、新規の未閉鎖admission／UNKNOWN・保留creationは0、Auto-Chill 0分／revision 5・engine 2/2である。通常構成の同梱lake_martsも既定の3 INVOKER VIEWとしてcompileできた。

`make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3`はexit 0、83 recipe・3,253件中3,219成功・34 skip・失敗0だった。標準PythonにYAML依存がなく途中停止した初回ログと、worker／focusedの重複件数は合算していない。最終HTTP境界23件・dbt画面45件、Go、design-context、差分・衛生チェックも成功。doctorは作業前後ともhealthyで、通常native SQL protocolのSELECTも別に成功した。

実機のCREATE後owner停止と、fixtureで検証した未閉鎖queryのUNKNOWN／ACK喪失は区別し、実機UNKNOWNを実施したとは扱わない。標準executor／VIEW連鎖・正式VIEW公開とM1本人受入は引き続き後続である。
