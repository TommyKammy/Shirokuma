# DB-P6-3C dbt materialization互換性

2026年10月29日。C1の技術受入完了。DB-P6-3BのSQL/WAP経路で、実行設定の黙殺をなくした。標準executor／真のVIEWはC2の別設計とし、C1の完了へ含めない。

## 判断

同梱dbt-core 1.12.5／dbt-trino 1.10.3は、tableの既定で中間Tableの作成・rename・dropを行い、viewを既定でSECURITY DEFINERとして作成する。通常dbtを現在のSQL endpointへ向けても、DataOps childの親・actor・context・資格情報をtransaction内で関連付ける契約には接続されない。

真のVIEWにはTable Snapshotとは別のidentity／version、依存relation、公開・取消・応答喪失・owner停止時の照合が必要である。INVOKERの公開viewからRAW／SANDBOXを読むと、MARTSだけを読める閲覧者の権限契約とも衝突する。viewをephemeralへ変えて対応済みとは扱わない。

この判断はinstalled adapterのtable／view macroと現在の型付きWAPを照合した結果である。上流の説明は[dbt Trino設定](https://docs.getdbt.com/reference/resource-configs/trino-configs)と[Iceberg view仕様](https://iceberg.apache.org/view-spec/)を参照する。現在のTrinoを新版へ更新する作業は含めない。

## C1の契約

現在のcompilerは一部のmaterialization設定とtest設定を受け付けても、SELECT projectionへ保存しない。例えばproperties／on_table_existsや、testのseverity／fail_calc／error_if／limitは、SQL/WAPの実際の動作と異なる指定でも省略される。この不整合を解消する。

- 解決済みmanifestのmodel／test configで意味のある設定を検査する。meta等の自由記述を同名キーだけで拒否しない。
- Table propertyは固定の`format=PARQUET`だけを型付きでサポートする。任意のproperty式をSQLへ補間しない。宣言された設定を不変planへ保存し、候補CTASと正式公開CTAS／CRTAの両方に適用する。
- その他のproperties、明示on_table_exists、未実装のmodel設定、失敗件数0判定と異なるtest実行設定はcompile時に拒否する。testの対応契約はerror／COUNT(*)／失敗件数0である。
- Planは`compatibility={version:1, executor:sql_wap, status:supported|blocked, issues:[{node_id,code,setting}]}`を返す。issuesは固定のcode／settingとnode IDだけとし、設定値・path・SQL・秘密値を含めない。未対応viewには明示したephemeral選択という既存の代替導線を案内する。
- 新しい互換性契約をplan digestへ含める。旧planは閲覧可能だが、新規runには再compileを要求する。既に受け付けたrun／publicationの記録・取消・照合と既存の公開gateは保持し、過去の結果を再認定しない。
- APIは既存plans／runsを使う。CLIは既存JSONで診断を取得でき、Workbenchは同じ保存planの理由と適用propertyを表示する。GETや診断からSQLを開始しない。

TABLEでは働かないview_security、contract.enforced=falseでの型・制約metadataは新たな拒否対象へ広げない。適用形式はTABLEだけに保存し、EPHEMERALは継承されたPARQUET指定があっても`{}`、画面では「対象外」とする。

## C1の受入

同梱projectの明示ephemeral compile、任意の正常なtable／ephemeral SQLモデル、test、WAP公開を維持する。未対応のview／table option／property式／test policyを具体的に拒否し、実SQLが増えないことを確認する。

実Tableの候補・新規公開・既存Table置換でPARQUETを確認する。2DBの同名モデル分離、source／candidate Snapshot固定、全test gate、A／E／viewerの境界を維持する。API／CLIの診断、日英の画面、直接リンク・reload、狭幅・keyboardをコード検証と分けて確認する。既存DB・台帳・data・Worksheet・host固有YAMLを保持する。

## 確認結果

一般の同梱projectを複製した承認済みローカルprojectを使い、実行処理にfixture専用分岐を追加せず確認した。証拠はprivateな`var/shirokuma/db-p6-3c-2026-10-29/private`へ保存し、秘密値・内部パス・実DDLの保存先を公開文書へ転記しない。

| 経路 | 結果 |
| --- | --- |
| API診断 | 既定viewは3ノード、未対応propertyは2ノード、on_table_existsは2ノード、非既定test条件は4設定を固定code／settingで報告。設定値のsentinelは公開応答に含まれない |
| 実行前拒否 | 4件のblocked planからrunを作れず、child／SQLなし。旧planのGETは維持し、新規runはDBT_PLAN_RECOMPILE_REQUIREDでadmission前に拒否 |
| CLI | 通常native HTTPのcompile／plan／runを使い、互換性契約とPARQUETを保存。7/7 test成功。blocked property／testの診断も通常plan取得で確認 |
| 候補・新規公開・既存置換 | CLI候補2件、新規公開先2件、既存Table置換1件で実engineのSHOW CREATE TABLEからPARQUETを確認。全3 run／3 publicationの実CREATE SQL計9件にも固定WITH句と実backend IDを確認 |
| test gate・分離 | 別DBのaccepted_valuesは1件失敗、他6件成功。公開を拒否し、従来のTable Ref・Snapshot一覧・値を保持 |
| 旧受付回収 | 完了済みlegacy publicationを元key／bodyで回収し、childリンク件数が不変 |
| 画面 | 検証中／未対応理由／対応済みを区別し、TABLEのPARQUETとEPHEMERALの対象外表示を確認。7/7成功後、実aliasを確認して2モデルを新規公開。日英切替、Tab操作、390pxで水平overflowなし |

通常8888／8080を既定のlake_martsだけへ戻し、保存plan／runの直接リンクとreloadで同じ診断を復元した。role上限をviewerへ下げると診断・SQL・DB選択肢が消去される。role復元とreloadでdbt受付・childリンクは増えず、compileやSQLを自動再送しなかった。

既存141 DB／564 Schema／各141件のcatalog・logical binding、全legacy dbt記録は全row比較で不変だった。元のサンプル2行、13 WorksheetのID・選択状態と選択中SQL／context、runtime profile・host固有YAMLのbytesを保持した。旧FENCED_READ_ONLYのUNKNOWN 2件を保持し、新規未閉鎖のUNKNOWN・活動中admission・保留creationは0、Auto-Chill 0分／revision 5・engine検証済み2/2である。

`shirokuma doctor --profile local-experiment`は作業前後ともhealthy。SQLは別途、通常Workbenchの元データ照会とnative SQL protocolのSELECTで確認した。Go tests／vet、design-context、diff checkは成功。Python全体回帰は `make verify-local-experiment` がexit 0で完了し、83 recipe・3,227件中3,194成功・33既存skip・失敗0だった。最新のcompiler18件・dbt画面42件もこの全体実行に含め、worker／focusedの重複件数を加算していない。

## C2へ残す設計

標準executorはSQL transportだけの変更では成立しない。最初にnativeのDDL／introspection／schema操作の全体、viewのINVOKER／DEFINERと閲覧者の基底relation権限、Tableと異なるview versionの公開・取消・復旧を定義する。型付きadapterの実証なしに任意DDL、管理者credential fallback、暗黙Schema作成を開放しない。DB-P6-3全体とM1本人受入は未完了である。
