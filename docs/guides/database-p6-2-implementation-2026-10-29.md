# DB-P6-2 共有LOCALの休止・自動復帰の実装と検証

2026年10月29日。**実装・技術受入済み**。登録済み`logical_v1`基盤の共有LOCALで、休止・自動復帰・Auto-Chillを通常API／CLI／Workbenchへ接続した。M1の所有者本人による操作受入は別gateとして残す。

- [実装契約](database-p6-2-contract-2026-10-29.md)
- [限定的なread-only Query復旧契約](database-p6-2-logical-recovery-contract-2026-10-29.md)
- [機能一覧と利用条件](database-capability-matrix-2026-10-29.md)
- [全機能化とコンピュートの後続計画](database-full-capability-and-compute-plan-2026-10-28.md)

## 利用できる範囲

共有LOCALの状態参照、基盤管理者専用の明示Wake／Chill・Auto-Chill設定・操作照会、および認可済みSQLに付随する自動Wakeを提供する。基盤操作には実accountのplatform権限・最大admin・選択adminをすべて必要とする。データ作成者・閲覧者は許可済みSQLから自動復帰できる。

休止はTrino coordinatorとworkerの正確な0／0への変更である。ストレージ・カタログ・VMは稼働を継続し、ホスト全体の資源解放を意味しない。LOCALの変更は全DBへ影響する。XS／Sの実スペック変更はDB-P7、複数WarehouseはDB-P8で扱う。dbt・Bears・SRE／Policyの未接続機能は開放しない。

| 入口 | 実装と検証範囲 |
| --- | --- |
| Workbench SQL・通常query API | 同じpublic submit経路。実ブラウザーで休止中の実行受付・待機・結果取得を確認 |
| 通常CLI | HTTP client経由のruntime操作とSQLを確認。拒否・同一冪等キーの受付保持も確認 |
| native endpoint | 公式Python Trino DB-API 0.339.0で認証・cold SQL・poll・cancel・timeoutを実機確認 |
| Job | 実データ作成者の保存済み2-step Jobを休止から実行し、両step・親runの成功を確認 |
| Stage／Mart／予定実行・その他typed DataOps child | 共通SQL経路への接続を自動回帰で確認。今回新たな取込や予定処理は再発行していない |
| metadata参照・モデル保存 | 起動しない。休止中のJob保存・server起動・管理API参照を実機確認 |

native clientは選定済みdriverであり、全ODBC／JDBC製品の互換認定ではない。既存loopback HTTPへ正式なhttp_sessionで認証し、driverのmax_attempts=1を使った。backendのTLS検証は維持した。

## 実装の要点

元context・認可・SQL policyとdurable admissionを確定してから共有Wakeへ合流し、復帰後と初回SQL POST直前にも最新認可を確認する。cold要求は安定したquery IDと待機pageを返し、queryごとの初回workerを一つに限定する。GETやprocess再起動から元SQLを自動再送しない。初回POST前の取消はbackend queryを作らずCANCELEDへ収束させる。

Auto-Chillは既存server内の単一cross-process ownerが実行し、待機processが終了したownerを引き継ぐ。停止前はglobal admission、実engineの未完了query、親Job／DataOps、logical DB作成・認証refreshのrequest／effect／rootを同じ停止gate内で検査する。engine診断には既存認証付きGET /metricsを利用し、global SQL本文の参照権限を追加していない。観測失敗時は停止しない。

休止中のserver起動はcontrol-planeの検証と完全engine bindingを区別し、Provider mutationを有効にせず管理APIを提供する。復帰後は各process自身のProvider tunnelと独立したSQL tunnelを修復する。同一epochのwarm接続は再利用し、実行中queryのpinを書き換えない。peer起動は既存の共有epochを維持する。観測によるproof失効も物理lockと比較条件で保護し、並行して公開された新proofや他processの接続を閉じない。

画面は物理状態・engine検証時刻・操作の受付と完了・停止blocker・設定revisionを分ける。設定競合時は入力値を保持し、明示的な再読込で破棄する。応答喪失時は同じ冪等キーで受付を照合する。未対応profileと一時的な観測失敗も区別する。

## 性能の実機結果

同一主体・owner_accept_20261028 / raw / admin・read-only・max_rows 1000のSELECT 1を使った。初回POSTと返された全pageを終端まで取得するHTTP end-to-endであり、UI描画時間やengine単体の測定ではない。warmは各30回、別のwarmup 1回を除外した。coldは毎回0／0を確認してから送信した。

| 条件 | 成功数 | p50 | p95 |
| --- | --- | --- | --- |
| 反映前warm | 30／30 | 146.19 ms | 168.23 ms |
| 反映後warm | 30／30 | 176.76 ms | 203.50 ms |
| 反映後cold | 30／30 | 26.44 秒 | 26.92 秒 |

coldのp95は目標30秒以内だった。warmのp95差は約35.27msで、計画上の比較目標250ms以内だった。ただし別時点の分布の差であり、個々の要求の純粋な追加overheadを分離した測定ではない。公式native driverの最初のcold SQLは約25.01秒、実閲覧者は約25.55秒、最終sourceで両serverを休止中に起動し直した後のnative SQLも25.49秒で完了した。

## 認可・競合・故障時の実機結果

- Workbenchとnative endpointの同時cold要求は一つのWakeへ合流し、各Deploymentのgeneration増分は1だった。2 queryは異なるbackend IDで完了した。実データ作成者・閲覧者の手動Wake／Chill／ReconcileはFORBIDDENで、拒否要求による操作追加と物理起動は0だった。
- 専用DBで実データ作成者grantを起動待機中に失効させると、queryはFAILED・backend IDなしとなり、結果参照もDB_ACCESS_REVOKEDで拒否した。初回認証refreshは古いProvider接続による既知のBLOCKED履歴を残したため、元要求を再送せず入口でのbinding修復を修正した。別の新規要求による2回目の失効・再付与はすべてCOMPLETEとなり、同じ失効拒否を再確認した。
- 専用DB p62_resume_accept_20261029は正規経路でREADYとなった。並行Chillはgateで直列化され、作成完了後に停止した。作成中拒否の証拠とは扱わない。2-step Jobは休止から両step・親runが成功し、親runがactiveな間の明示ChillはADMISSION_CONFLICTで拒否された。
- 初回SQL POST前の取消はCANCELED・backend IDなしへ収束し、別利用者も待つ共有Wakeは完了した。実native cancelはdriver側で受理され、公開queryもCANCELEDとなった。
- 実native timeoutではdriver応答が失われたが、要求は一度だけ受付され、元SQLを再発行しなかった。同じoriginでの後続取消でも終端を断定できずUNKNOWNを維持した。下記の限定復旧で実行を閉じ、timeoutを取消成功とは記録しない。
- 稼働中のpeer server起動は共有epochを変えず、既存paged queryと新queryが完了した。その間の明示ReconcileはWAREHOUSE_BUSYとなり、実行中queryの接続を閉じなかった。
- 最終Auto-Chill検証はWorkbenchからnative standbyへのmonitor引継ぎ後、最後のSQLから約77秒で0／0となった。休止中の両server起動後も139 DBがREADYで、約90秒の観測中に操作履歴は増えなかった。native SQLによる復帰後、engine検証とDB作成capabilityが復帰した。

### 結果不明のread-only Queryの限定復旧

接続寿命の修正途中に発生した1件とnative timeoutの1件を、限定復旧契約のprivate operator経路で処理した。各々同じoperationをPLANNED → STOP_INTENT／DRAINING → PROVED → CLOSED → OPENへ進めた。置換待ちでは同じoperationのGET-only照合と最新revisionでの明示applyを使い、対象の旧Container IDを増やしたりSQLを再発行したりしていない。

coordinatorを先に停止し、両対象のpositive stop receiptを保存してから旧Containerの除去と実行閉鎖を進めた。元queryの結果は2件ともUNKNOWNのまま保持し、admissionのFENCED_READ_ONLYと証拠参照で実行を閉じた。最終状態は未閉鎖実行0・LOCAL OPEN／revision 5である。書込query・親DataOps／Job・外部nodeの一般的な復旧は対象外とした。

対象外の旧shirokuma-dev coordinatorは、固定設定fileとConfigMapの一致、独立discovery、Docker／Pod所有関係と隔離networkを照合した。同じContainer ID・開始時刻で稼働を継続し、foreign workerや不明なactorの拒否は維持した。

## 画面と保持の確認

実ブラウザーで日本語のcold SQL受付・待機・結果1、物理状態とengine検証の表示、Pの操作、E／Vの参照専用表示、下書き競合・明示破棄を確認した。英語390pxの画面はscrollWidthも390pxだった。Databaseモードでは未対応SRE／Policyの旧操作と重複overviewを非表示にした。

反映前の138 DB・552 Schema・138 catalog bindingは、保存済み台帳と全行・全fieldが一致した。専用受入DB 1件だけを追加し、最終139 DBはすべてREADYである。元sampleの2行はdigestが一致した。専用DBのE権限は再付与済み、V権限は保持した。既存13 Worksheetと選択中のSQL全文・DB／Schema／保存admin roleも保持し、受入用Worksheetだけを閉じた。

最終共有computeは2／ready2・engine検証済み、未完了logical要求0、停止blocker全カテゴリ0である。Auto-Chillは元の無効設定へ戻し、0分・revision 5となった。host固有の既存YAML差分は変更対象から除外した。

## 検証記録と次の作業

全体回帰make verify-local-experimentは3,034件中3,001件成功・33件skipだった。skipは既存OPA 2件・pyarrow 1件・専用runtime条件30件である。Goの全体test・build・vetも成功した。

全体回帰後の接続・同期・認証refreshの差分は個別に再検証した。主要275件、proof／epoch関連107件、最後の認証refresh修正のfocused 28件と全logical 411件、および最終runtime route／client 20件が成功した。これらは重複を含む独立の実行記録であり、全体成功数へ加算していない。設計整合性とwhitespaceの検査も実施した。

生の計測値・認証を含む実機fixture・台帳snapshotはGit対象外のvar/shirokuma/db-p6-2-2026-10-29/private/に保持した。代表資料はcold-metrics.json、warm-before.json、warm-accepted.json、final-asleep-native-cold.json、final-preservation.json、browser-preservation.json、native-timeout-verification.jsonである。画面証拠は同じ受入directoryのscreenshots/に保存した。

次はDB-P6-3のdbt接続feasibilityへ進む。DB-P6-7の統合受入とM1の本人受入は引き続き別gateとし、今回の共有LOCAL受入を全機能の完成とは表示しない。
