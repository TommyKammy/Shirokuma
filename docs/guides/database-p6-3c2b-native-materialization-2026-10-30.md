# DB-P6-3C2B1 標準TABLE materializationの接続実証

2026年10月30日。C2Aに続き、installed dbtの標準TABLE materializationと既存DataOps実行境界を接続し、限定した実証の技術受入を完了した。この単位は新規SANDBOX候補1ノードのprivate実証であり、通常の`dbt run/build`全体を利用可能にする段階とは区別する。

## 対象を絞る理由

dbt-trino 1.10.3の[標準TABLE macro](https://github.com/starburstdata/dbt-trino/blob/v1.10.3/dbt/include/trino/macros/materializations/table.sql)は、既定のrename方針でも新規targetならCTAS 1件になる。既存targetでは中間TABLE作成、旧targetのbackupへのrename、中間TABLEのrename、backupのdropが必要になる。標準VIEWはOR REPLACEと既定DEFINERを使い、C2Aの固定CREATE／INVOKERと同じ契約ではない。

通常RunTaskはnode実行前にSchemaやrelation cacheを調べる。installed adapterのrelation一覧には`system.metadata.materialized_views`とのJOINがあり、現行のDB限定権限と衝突する。これを解消するためにmetadata権限や通常SQL guardを一括して広げない。

最初の接続点はinstalled `ModelRunner.execute`とadapter instanceの`execute` callbackとする。標準macroが実際に生成したCTASをcallbackで型付き検証し、既存DataOps childへ同期送信する。offlineでSQLだけを生成して従来WAPで実行したものを、標準runnerの成功とは表示しない。

## private実行契約

- 元本人と9項目のcontextに一致する、承認済みprojectの保存READY planを入力にする。plan digest・source digestを検査し、元TABLEと展開済みEPHEMERALだけを読むTABLE nodeを1件選ぶ。VIEWや他の実体modelに依存するnodeはこの単位で扱わない。
- 出力はoperationごとに割り当てる同DBの新規SANDBOX名だけとする。target・tmp・backupのlookupは正確な割当名に限定し、RESTで不在を確認する。Schema全体を空と偽装せず、未知のmetadata照会は拒否する。
- プロジェクトのSQL helperは既存compilerで展開する。runtimeの実行macroはinstalledのbuiltinに限定し、project側のmaterialization／実行helperの上書きを標準macroの実績へ混ぜない。
- native SQLはASTで単一の新規CTAS、割当target、固定PARQUET、承認済みSELECT本文を検査する。元TABLE Ref／Snapshot／schemaを一度固定し、Snapshot句を型付きで注入する。callbackのSQLやheader・connection名は権限を供給しない。
- DataOps operation・元資格情報・親admissionを使い、既存query intentとchild関連付けをPOST前に保存する。唯一のwrite stepは固定`native-create`とし、別のsequence番号で同じCREATEを再送しない。例外・UNKNOWN後のcallback、cleanup／rollbackによる追加SQLを拒否する。
- commit前のreceiptは`native_verified=False`を保持する。engineの終端成功、標準macroの成功、実TABLE UUID／Snapshotとsource schemaの照合がそろった場合だけverified候補とする。native成功はWAP test成功や正式公開を意味しない。
- 元keyの回収は保存状態だけを返す。CREATE後の失敗・owner停止では候補を保持し、未実行node／DDLを再開しない。既知FINISHEDのchildだけなら、既存のinspect→UNKNOWN→reconcile_failureで閉鎖できる。候補の存在だけで未閉鎖queryを完了扱いしない。

この関数は隔離したoperator processで実行する。dummy dbt profileに実資格情報を渡さず、DBAPIを開かせない。Shirokumaのstate／認証／HOME／kubeconfigに必要なprocess環境はcallback中も保持し、dbtのInvocationContextと明示設定だけを隔離する。既存の通常API／CLI／Workbench、v1／v2 SQL/WAP plan・run・公開gateは変更しない。

呼出し元は通常サーバーと同じruntime初期化を行う。既存の認証で得たprincipalと、保存planのcontextをそのまま使う。

```python
runtime_profile.configure_process()
with logical_creations.lifetime() as provider:
    warehouse_lifecycle.observe_startup(provider)
    operation = database_dbt_native.materialize(
        principal, context, plan_id=plan_id, node_id=node_id, key=key)
```

[dbtのPython invocation文書](https://docs.getdbt.com/reference/programmatic-invocations)も、process内の並行invocationと内部objectの互換性に注意を求めている。本実証はinstalled dbt-core 1.12.5／dbt-trino 1.10.3に固定し、ModelRunner内部APIの利用を一般的なpublic adapter APIの保証とは扱わない。

## 確認結果

専用DBの同梱projectと、compile-only SQL helperを持つ承認済みの一般projectで確認した。実装にfixtureのproject名・model名・行数による分岐はない。private証拠は`var/shirokuma/db-p6-3c2b-2026-10-30/private`へ保存し、実資格情報をdbt profileや公開receiptへ含めない。

| 経路 | 実機結果 |
| --- | --- |
| 標準macro→engine | Eのdim、Aのfct、一般SQL helper、別DBのE dimの4候補がnative ModelRunner経由で成功。各operationの固定native-createは1件で、全て元backend identity、PARQUET、実TABLE UUID／Snapshotを確認 |
| Snapshot固定 | 使い捨てRAW sourceをSnapshot固定後に追記。sourceは2行、native候補は元Snapshotの1行を保持。native SQLには保存Snapshot句があり、同keyでrunner／SQLを再実行しない |
| 2DB・権限 | ALPHA／BETAの候補値とDB別backend identityを分離。VIEWERと別actorのplan実行はadmission前に拒否。別DBへのtarget差替えもquery intent 0件で拒否 |
| CREATE後の失敗／取消 | commitは保持し、FAILED／CANCELED、native_verified=False。候補は残り、同keyは観測だけでquery 1件から増えない |
| 実owner停止 | 既知CREATE完了・native検証前にoperator processをos._exit。別processのinspectでUNKNOWN、既存reconcile_failureでFAILEDへ閉鎖。commit・未検証候補を保持し、SQLを再送しない |

最初のruntime未初期化の呼出しはbackend IDなしで失敗した。元receiptを観測し、初期化を補った別の要求で実証した。完了済みCREATEの記録取得を修正した際も元receiptから回収し、成功した要求を再送していない。これはstandalone operator呼出しの確認であり、通常サーバーへ新しいnative routeは追加していない。

最終比較で、141 DB／564 Schema／各141件のbinding、grant・identity・資格情報・password・file ACL・OPA guard、既存profile／host固有YAML、RAW／MARTS 18 Tableのheadと全legacy dbt／WAP／DataOps／query rowを保持した。新規UNKNOWN・未閉鎖admission・保留creationは0、元のFENCED_READ_ONLY UNKNOWN 2件を保持し、Auto-Chill 0分／revision 5・engine 2/2である。検証用RAW source 1件とSANDBOX候補7件は残し、うち3件は未検証候補として正式公開しない。

`make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3`はexit 0、84 recipe・3,278件中3,244成功・34既存skip・失敗0だった。nativeの25件を含む単一の全体実行だけを集計し、worker／focusedの重複件数は加算していない。Go・design-context・差分・衛生チェックも成功。doctorは作業前後ともhealthy、通常8888／8080を新しいコードで起動し、既定の3 VIEW／SQL-WAP planとnative SQL protocolのSELECTを別に確認した。

標準RunTaskのschema／cache／graph、native test、置換・rename／drop、native VIEWと通常導線への接続は後続とする。C2AのSQL/WAP実行や正式公開gateをnative実行へ黙示変更せず、DB-P6-3全体とM1本人受入も未完了である。

同日の後続[C2B2](database-p6-3c2b2-native-runs-2026-10-30.md)で、新規native TABLE 1件の明示選択を保存run／API／CLIへ接続した。SDKのprocess分離と履歴のテスト未実行・公開不可表示を追加し、通常RunTask／graph・native test・置換や画面からのnative実行選択とは区別する。
