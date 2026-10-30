# DB-P6-3C2B2 明示選択するnative TABLEの保存実行

2026年10月30日。[C2B1](database-p6-3c2b-native-materialization-2026-10-30.md)のprivate接続実証を、既存の保存runとAPI／CLIへ接続した。この限定範囲の技術受入を完了した。

## 実行契約

既定のSQL／WAP実行はそのまま使う。native実行は、元本人と保存contextが一致するREADY v2 planに対し、`executor=native_table`と`node_id`を明示する。選択できるのはRAWの元TABLEと展開済みEPHEMERALだけに依存する、固定PARQUETのTABLE 1件である。同DBの新規SANDBOX候補を作り、他のモデルを暗黙に実行しない。

native runは既存`dbt_runs`へexecutor・選択node・plan／source digest・元contextを保存する。成功は`NATIVE_VERIFIED`で、`tests_status=NOT_RUN`、`publication_allowed=false`とする。標準macroの成功、engine成功、実TABLE UUID／Snapshotの照合がそろった場合だけ`native_verified=true`を記録する。WAPのbuildや公開可能なtest済み成果物へ変換しない。

SQL送信、資格情報、元owner、admissionとquery intentはサーバー側DataOpsへ残す。SDKだけをdummy環境の専用processで実行し、匿名pipeのcallbackを親の型付き境界で検査する。子processへ実資格情報やstoreの権限を渡さず、dbtのglobal状態を通常サーバーへ持ち込まない。source Snapshotは保存runで一度固定し、その後の追記で取り直さない。

SDKの無応答を120秒で止める。実SQLや親のmetadata照会に使った時間はこの待機時間へ算入しない。pipeの送受信・部分応答・終了待ちでも取消を確認し、所有する子processを回収する。保存runの成功は返却dictだけで決めず、正規child receipt、1件のquery intent／engine ID、元actor・context・資格情報参照、候補の所有・Ref・Snapshotを照合する。

既存のGET／履歴／取消／限定reconcileで観測する。同keyは元runの観測だけを返し、GETや再起動、UNKNOWN回収からSDKやSQLを再実行しない。CREATE後の失敗ではcommitと未検証候補を保持する。SDKのEOF・異常終了・無効なcallbackは追加SQLを止める。

## API／CLI

通常の認証済み`POST /api/v1/dbt/runs`へ、従来の`context`／`plan_id`に加えて`executor: "native_table"`と`node_id`を送る。executorを省略した既存リクエストは従来どおりSQL／WAPで実行する。未知のexecutor、nativeのnode欠落、SQL／WAPへのnode指定は拒否する。

```sh
shirokuma dbt run --database <database> --schema sandbox \
  --plan-id <saved-plan-id> --executor native_table \
  --node-id <model-unique-id> --idempotency-key <original-key>
shirokuma dbt status --database <database> --schema sandbox --run-id <run-id>
```

不明な受付結果では元contextとkeyを保持する。新しいkeyを発行して同じ要求をやり直さず、元runを回収する。Workbenchの新しいnative実行選択UIはこの単位の対象外とし、通常のSQL／WAP導線を維持する。

## 受入

実機証拠は`var/shirokuma/db-p6-3c2b2-2026-10-30/private`へ保存した。通常API／native HTTP／実CLIで元本人のE／Aと2DBを確認し、一般SQL helperを持つ承認済みprojectも同じ処理で実行した。fixture名や行数による製品の分岐はない。

| 確認 | 結果 |
| --- | --- |
| 保存run／元資格情報 | Eの2DBとAの実CLI、一般projectの4件がNATIVE_VERIFIED。各runはDataOps child 1件、native-create 1件、元backend identity／資格情報参照、実TABLE UUID／SnapshotとPARQUETを照合 |
| 休止からの復帰 | 通常LOCALを0/0まで休止後、native HTTP 8080を使う実CLIから透過復帰して成功。設定はAuto-Chill 0分・revision 5を保持し、2/2へ復帰 |
| Snapshot | 保存runのpin取得後、独立したoperator threadで使い捨てRAWへ追記。sourceは4行、候補は元Snapshotの3行を保持。同keyのAPI再送は観測だけ |
| 権限と公開 | viewer、別actor、別DB、executor／nodeの不正組合せ、同keyのnode変更を送信前拒否。E／Aともnative候補の正式公開を拒否し、新childは0件 |
| 停止と取消 | CREATE後の例外・SDK強制終了・取消ではcommitと未検証候補を保持。無応答SDKは取消で0 SQL。実ownerをos._exitした後も元runをFAILEDとして回収し、SDK／SQLを再実行しない |
| 履歴画面 | 保存runのexecutor／node／NATIVE_VERIFIEDとテスト未実行・公開不可を日英で表示。reload／direct link、viewerでの内容非表示、390px幅の折返しを確認。元admin・日本語・通常viewportへ戻し、13 Worksheetと表示中の下書きを保持。追加のadmin dbt受付は0件 |
| 既存経路 | 元SQL／WAP keyの回収でrun／operation／query件数が不変。通常サーバーで既定の3 VIEWと下流TABLE／全testがREADY。native SQL protocolのSELECTも別に成功 |

回帰は単一の`make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3`で84群・3,302件中3,268成功・34既存skip・失敗0。worker／focusedの重複件数は加算しない。Go、design-contextと差分・衛生チェックも成功した。

検証用appendのadmissionを誤ってnative familyへ置いた2件は、新規のoperator記録の親だけを独立実行へ訂正し、元native runはFAILEDとして回収した。run／queryの状態を直接変更せず、再SQLや成功への昇格も行っていない。起動補助が削除される一時TMPDIRを継承したcompile失敗は元FAILED planを保持し、host環境の通常プロセスで既定3 VIEWのcompile／実行・testを再確認した。これらの補助手順の失敗は成功4件へ数えない。

最終保全比較では141 DB／564 Schema／各141 binding、既存grant・identity・資格情報・password・file ACL・OPA guard、profile／host固有YAML、RAW／MARTS 19 Tableのheadと全legacy rowを保持した。検証用RAW source 1件とnative候補10件を残し、native保存runは成功4件・未検証候補を残す6件・SQL前取消1件。新規の未閉鎖admission／UNKNOWNは0、元のFENCED_READ_ONLY UNKNOWN 2件を保持した。

画面からのnative実行選択は後続の[C2B3](database-p6-3c2b3-native-ui-2026-10-30.md)で扱う。RunTaskのschema／cache／graph、複数の実体model、native test、置換・rename／drop、native VIEWと正式公開は引き続き後続とする。DB-P6-3全体とM1本人受入は未完了である。
