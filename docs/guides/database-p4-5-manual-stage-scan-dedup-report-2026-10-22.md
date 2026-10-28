# P4-5 Stage手動scan・内容dedup 限定技術受入完了報告

2026-10-22。状態：**P4-5の限定技術受入完了**。
対象は[Phase4計画](database-phase4-implementation-plan-2026-10-20.md)のP4-5。
通常API/CLI、Workbenchの主要操作、故障・別process復旧、grant失効のAPI/CLI拒否を確認した。
限定清掃、検証grant全6件の剥奪、最終generation `p45-scan-final`のfull適用も完了した。
失効後の画面、owner用window終了、検証用認証の撤去、通常入口の復帰、元2SQLタブと独立した最終照合まで完了した。
Phase4全体の最終受入は未完了であり、P4-6以降と自動scan・予定実行は後続計画に残す。

## 実装した境界

P4-4のStage ID・admin owner・許可済みlocal root・定義CASを継承し、
手動scanをP3-1の共通実行境界へ接続した。scanは`upsert`、または
`atomic_append=true`の`append`だけを受け付ける。`create/replace`は従来の単一file手動操作に残す。
受付時に対象fileのbytes・定義・full9 contextを固定し、名前順にfile単位でcommitする。
後続fileの失敗で先行fileのcommitを取り消さない。

内容identityにはDB・Stage ID・target ResourceRef・正規化した実効Ingest設定・入力bytesを含める。
設定にはmode、upsert key、型、encoding、delimiter、batch、atomic appendを含み、
file名・表示名・mtime・定義revision・generation/grantの変更だけで同じ内容を再取込しない。
単一file手動Ingestとscanは同じ永続reservationを共有し、既存P4-4のcommit証拠も利用する。
重複時は元の保護されたcontextやsummaryを返さず、実行IDを捏造しない最小の`skipped`応答とする。

schema 15へscan・item・内容台帳を追加した。各fileのrunとcommit・子queryの確定状態を照合し、
UNKNOWN、部分commit、commit後の記帳失敗を未実行扱いにしない。
既知の未commit失敗だけを、サーバーの`retryable`判定とattempt CASにより明示retryできる。
retryは対象fileだけを含む**新しいscan・parent・run**を作り、元のscanを再開しない。
応答喪失時は元keyで照合し、再起動や所有processの消失を自動再送の理由にしない。
scan全体のparent admission、Jobとの同時実行上限、DRAINING、権限再検証も共通境界で扱う。

### 通常入口

| 操作 | HTTP API | 通常CLI |
| --- | --- | --- |
| 新規手動scan | `POST /api/v1/stages/{id}/scans` | `stage scan` |
| 一覧 | `GET /api/v1/stages/{id}/scans` | `stage scans` |
| 元scanの照会 | `GET /api/v1/stages/{id}/scans/{scan_id}` | `stage scan-inspect` |
| 既知未commit fileの明示retry | `POST /api/v1/stages/{id}/scans/{scan_id}/retry` | `stage scan-retry` |

通常CLIは既定8080の認証付きHTTPを使い、DB/Schemaまたは元のfull9 contextを明示する。
scan/retryには定義revisionとkey、retryにはitem IDとexpected attemptを要求する。
CLIからlocal filesystem・SQLite・SQLへの迂回は追加していない。

WorkbenchのStageパネルに手動スキャン、一覧・照会、file別状態、明示retryを接続した。
Stage contextはworksheet・Explorerから独立し、UNKNOWNには照会だけを提示する。
refresh時の下書き・focus・元タブ保持、失効時の保護内容消去、遅延応答の復活防止は
単体・UI契約テストで確認済み。失効後の実画面と、認証撤去・復帰後の元タブ最終確認も完了した。

途中の実ブラウザー確認で、SALESのscan結果を保持したままFINANCEへ移り、Schemaだけを選ぶと、
FINANCEのStageを読み込む前にSALESのfile別結果とObserve操作が再表示される不具合を発見した。
cached結果と現在のDB/context・読み込み済みStageの関連付けが不足していた。
UIとそのテストだけを修正し、表示・照会・retryに元のfull9 contextと現在のStageの一致を要求した。
context/Stage移動では保護された詳細・一覧を消去し、受付済みID等の安全なhandleを保持する。
元のcontextへ戻って同じStageを明示的に読み込めば、新しい認可付きObserveは継続できる。
`retryable`は現在のbackend判定として扱い、過去の表示値だけで再試行しない。

この修正のPython 3.12 UIテストは40件成功した。
修正後source v3を固定して広域ゲートを通し、実ブラウザーでもFINANCE/rawの準備完了後・
Stage未読込み時に、SALESのfile別結果が消去されたままObserveが無効であることを再確認した。
該当する4項目の画面照合はすべて成功した。修正前v2の証拠も保持し、sourceを区別する。

主実装：[db_stage_scans.py](../../tools/local_experiment/db_stage_scans.py)、
[db_stages.py](../../tools/local_experiment/db_stages.py)、
[dataops_service.py](../../tools/local_experiment/dataops_service.py)、
[db_stage_routes.py](../../tools/local_experiment/db_stage_routes.py)、
[Workbench](../../tools/local_experiment/workbench/index.html)、
[stages_client.py](../../tools/local_experiment/stages_client.py)、
[通常CLI](../../internal/cli/stages.go)。

## 現時点の実証

### 固定sourceとテスト

v3 source manifestは195件。広域ゲートの前後で全sourceが一致し、Go binaryも変更されていない。

| 検証 | 結果 |
| --- | --- |
| resident core | 56 suite、1,611件実行、1,578成功・33 skip、失敗0 |
| design/context | 14テスト成功、整合確認成功 |
| Go CLI（変更なし） | 74トップレベル、子テスト込み176成功 |
| security（既存記録） | 34テスト成功。既存のhost固有marker検出により全体exit 2 |

securityの`opentofu/dev/variables.tf`は既存のhost固有設定として保持した。
新規scanのmigration、内容reservation、競合・不確実性、retry CAS、権限・遅延応答を
テストで扱っているが、これを後述の故障系実機受入の代用とはしない。

証拠は`var/shirokuma/p4-5-acceptance/`配下の
`source-manifest.json`、`runtime/v3-gates/summary.json`、`runtime/v3-gates/activation-proof.json`、
`runtime/v3-gates/verify-local-experiment.log`、`runtime/v3-gates/verify-design-context.log`、
`gates/go-test.jsonl`、`gates/security-result.json`、`gates/security.log`。
修正前v2の1,605件ゲートとmanifestは元記録として保持した。
UI修正の証拠は`workers/ui-context-fix-review.md`、`workers/ui-context-fix-index.patch`、
`workers/ui-context-fix-tests.patch`、`workers/ui-context-fix-root-python.log`。v2のbeforeimageも保持した。

### schema 14から15への隔離migration

実際のschema 14バックアップを隔離コピーで15へ移行し、18/18照合が成功した。
旧SQLite control storeの51テーブル・10,065行・BLOB 5,544 bytesと元Schemaを保持し、
追加4テーブルが空であること、再接続bootstrapの冪等性、integrity check成功、FK違反0を確認した。
新規空DBのschema 15生成と再接続も同様に確認し、入力バックアップとsourceは変更していない。
証拠：`runtime/migration/proof.json`、`runtime/migration-first.log`。

### 通常API/CLI

初期generation `p45-scan-initial`を実適用し、backend OPA 145件と既存production検証の実SQL 15件を確認した。
この15件はProductionOperatorのidentity・権限・Schema probeであり、全業務データのhash照合ではない。
通常8888/8080のgeneration一致、Python 3.12、LOCAL OPEN、検証用2利用者の非platform admin属性と
3DBずつ計6件のbackend検証済みadmin grantを確認した。

SALES・FINANCE・任意名DBで、通常API/CLIの有効な照合**723件が成功**した。
atomic appendとupsert、元keyでの同一scan照会、改名・mtime変更・同内容別名のskip、
同名別bytes・実効設定・key・target・Stage IDの区別、型付きtarget内容を照合した。
create/replace・非atomic append・不正upsert key・古い定義CASのSQL送信前拒否、
他owner・別DB・engineer/viewerからの取得・新規scan拒否とSQL未送信も確認した。

通常API/CLIの複数file部分成功・明示retryは別途12/12照合が成功した。
最初のfileのcommit、次の不正fileの未commit失敗、後続fileの`not_started`を確認し、
物理path上のfileを修正しても明示retryは元BLOBを使って新scan・attempt 2として失敗した。
その後のfresh scanでは先行成功fileをskipし、修正fileと未開始fileをcommitしてtarget 8行を確認した。
保存run ID・attempt・BLOBと、現在の公開`retryable`判定を区別し、harnessの期待訂正は元記録を保持した。
証拠：`runtime/partial-summary.json`、`runtime/partial-history-readonly-correction.json`。

schema 14時点のP4-4手動commitについても3DBで同じStage IDを再利用し、
再登録なしのskip、子SQL・DataOps・runの追加なし、元Ref UUID、元manual runへの内容台帳linkを確認した。
古いcontextでのrun取得は拒否した。

既定encoding `utf-8-sig`と同じ明示設定への期待値1件、およびgeneration変更可能台帳の比較基準は、
元のharness記録を保持し、再送なしのread-only訂正として区別した。
記録724件をすべて成功とは数えない。

通常確認後の照合では、元5DB・20Schema、元grant・既存履歴、195件source、
legacy3件のidentityを保持し、未解決実行0、過去の`FENCED_READ_ONLY` 2件を保持した。

証拠：`runtime/initial-ready-summary.json`、`ordinary/state.json`、
`runtime/ordinary-summary-ready-baseline.json`、`runtime/ordinary-summary.json`、
`runtime/matrix-encoding-readonly-correction.json`、`runtime/legacy-preservation.json`。

### Workbench

owner用の独立した画面で、SALES・FINANCE・任意名DBそれぞれ別のStage IDからCSV/JSONLをscanし、
file別結果を照会した。SALESの最初の`a.csv/b.jsonl`成功とskipはv2の証拠であり、
FINANCE・任意名DBの新規成功、および次のSALES追加取込は修正後v3で確認した。

SALESの新規scanは、既成功`a.csv/b.jsonl`をskipし、`c.csv`をcommit済み、
不正な`d.csv`を未commit失敗、後続`e.csv`を`not_started`として表示した。
Stage上の`d.csv`を修正後も、明示retryは元の受付内容に対する新scan・新run/operation ID、
attempt 2として失敗を表示した。続くfresh scanでは`a/b/c`をskipし、修正された`d.csv`と
未開始だった`e.csv`が成功・commit済みとなった。
これらの画面結果に対する独立照合は、有効な**481項目すべてが成功**した。
full9 context、run/operation、新parent、子queryの対応を照合し、retryが修正済み物理fileではなく
元の不正BLOBを保持していたことを確認した。通常SQLによるtyped target照合は
SALES 9行・FINANCE 6行・任意名DB 6行で一致した。
元321件のResourceRefを保持し、未解決実行0、過去の`FENCED_READ_ONLY` 2件も保持した。

不正CSVの処理でも、解析前にtargetの`DESCRIBE` queryが実際に`FINISHED`となっていた。
その後のcanonical DataOpsは`FAILED`・未commitとして確定しており、SQL未送信とは記載しない。
元harnessの「解析失敗は事前SQLなし」という期待1件は削除せず、訂正済み記録として区別した。

同contextのStage refreshでは、`batch_size=37`の未保存draft、同じStage、保存revision 2が保持され、
その後元の入力値へ戻した。実機でのfocus/caret競合は未計測であり、動的UIテスト40件の結果と区別する。
失効前の比較画面には、SALESの失敗retryと再試行可能表示を保存した。
失効後の表示・操作拒否は後述の最終GUI照合で確認した。
元2SQLタブは認証撤去前の照合に続き、復帰後の最終8/8照合も完了した。

証拠：`ui/actions-summary.json`、`ui/v3-context-switch-proof.json`、
`ui/v3-finance-before-load.txt`、`ui/finance-scan-success.txt`、
`ui/arbitrary-scan-success.txt`、`ui/partial-scan-failed.txt`、
`ui/partial-file-correction.json`、`ui/partial-retry-failed.txt`、
`ui/partial-fresh-success.txt`、`ui/same-context-draft-refresh-proof.json`、
`ui/before-grant-revocation.txt`。対応するPNGも同じdirectoryに保存した。
canonical照合の証拠は`runtime/gui-verification-summary.json`、`gui-verify/state.json`。
画面記録と独立照合は、通常API/CLIの723件とは別に扱う。

### 競合・記帳喪失と別process復旧

| 事例 | 結果 |
| --- | --- |
| manual先行・別processのscanとの競合 | 47/47成功、独立照合13/13成功 |
| scan先行・別processのmanualとの競合 | 34/34成功、独立照合5/5成功 |
| 3fileのcommit後item記帳喪失・別process復旧 | 80/80成功、canonical証拠の独立照合成功 |

manual先行では、同内容の競合を保持したまま、先行manualをSQL前の既知未commit失敗として確定した。
Observeは元のfailed runへのlinkを返し、通常CLIの明示retryだけが新parent・新run・attempt 2を作って成功した。
途中で物理fileの内容を変えてもretryは元BLOBを使い、元bytesへ戻した後のfresh scanはSQLなしでskipした。

逆順ではscanだけが一度commitした。競合manualの409とroot admission上限の409で、
不要なoperation・admission・queryが残らないことを照合した。
確定後のmanual重複要求は`id:null`・現在contextの最小`skipped`となり、元の実行詳細を返さなかった。

記帳喪失は3fileで実際に注入した。最初のfile成功に続き、2件目も実commitを完了したが、
そのitemの記帳を失わせた。GET前に保存したraw台帳ではitemがUNKNOWN、3件目は未開始で、
先行2fileの計14子queryはすべて`FINISHED`だった。この間、新scanと手動SQL Jobは409で拒否され、
新しい実行は増えなかった。

故障注入のない新しいprocessから同じscanをObserveすると、canonical commit証拠に基づき、
同じIDのままSQL・run・operationを追加せず復旧した。scanは`partial`、parentは`FAILED`で閉じ、
3件目を自動再開しなかった。明示fresh scanで先行2fileをskipして3件目だけをcommitし、
その後の全件skipではquery・DataOps・run増分0、新scan parentだけが1件増えた。

3事例の補助入口は停止済みで、未解決実行0、過去の`FENCED_READ_ONLY` 2件を保持した。

証拠：`runtime/fault-manualfirst-summary.json`、`fault-manualfirst/state.json`、
`runtime/fault-scanfirst-summary.json`、`fault-scanfirst/state.json`、
`runtime/fault-bookloss-v4-summary.json`、`fault-bookloss-v4/state.json`、
`runtime/fault-plans/bookloss/raw-unknown-before-GET.json`。

### 実行中grant失効のAPI/CLI

通常grant APIのCASでownerのSALES grantをadmin revision 1から明示`revoked` revision 2へ変更した。
最初の202受付と監査1件を照合し、backend apply前にeffective roleが`revoked`・epoch 3となり、
applied revisionは1のままであることを確認した。他19件のdesired/effective grantは変わらなかった。
この実機検証は48/48成功、独立照合も12/12成功した。

受付済みscanでは、最初のfileの7子queryが`FINISHED`・commit済みだった。
次fileのadmission前に失効を反映し、2件目のrun・operation・linkは作られなかった。
scanは`canceled`、parentは`CANCELED`、全体の`committed=true`で閉じ、先行commitを保持した。
これは前節の記帳喪失による`partial/FAILED`とは異なる、権限失効による停止結果である。
停止待ちを解除して該当補助入口を停止し、人の画面操作を待つ実行は残さなかった。

通常APIの取得・一覧・新規・retryは403 `DB_ACCESS_REVOKED`、通常CLIの同4操作はexit 1・
保護されたstdout出力0となり、query・DataOps・admission・Stage・runの5件数に増分はなかった。
別の権限を持つ照合ではtargetは先行fileまでの4行だけで、ownerのFINANCE・任意名DB取得は200だった。
未解決実行0、過去の`FENCED_READ_ONLY` 2件、v3 source 195件を保持した。
roleの保存表現と停止statusに関するharness期待2件は元記録を保持し、再送なしで訂正した。

証拠：`runtime/fault-finalgrant-summary.json`、`fault-finalgrant/state.json`。
最初のMac手動解除後に3DB操作・partial/retry・v3 context境界を確認した。
再ロック後の2回目の手動解除を受け、全6 grant失効・最終generation適用後の実GUIを確認した。
Stage refresh前に明示的なアクセス不可表示、refresh後にgenericな失敗表示があり、
file・options・directory・履歴の保護内容が消去され、scan・一覧・Observe・downloadは無効、
Retry操作はなく、受付済みID等の安全なheaderだけが残った。13/13項目が成功した。
genericな画面文言からHTTP403を推論せず、403は前述の通常API/CLI証拠で確認した。
この最終GUIは全6 grant失効後であり、SALESだけの失効と他DB取得200の独立API/CLI検証とは分けて扱う。

所有するincognito windowを4/4 guardで確認して終了し、owner認証への操作ツール内参照も消去した。
元の通常windowは保持した。認証撤去前の元2SQLタブはcontext・SQL・タブ順の3/3が一致し、
認証撤去後にも同じタブを再照合した。
証拠：`ui/grant-revoked-proof.json`、`ui/grant-revoked-before-refresh.txt`、
`ui/grant-revoked.txt`、`ui/grant-revoked.png`、`ui/owner-window-close-proof.json`、
`ui/normal-before-auth-restore-dom.json`、`ui/normal-before-auth-restore.txt`。

### 限定清掃・検証grant整理・最終適用

列挙・固定した検証用18 TableをDROPし、18/18件の実backend `FINISHED`と、
現在の権限を確認した通常入口からの取得404を照合した。対象54 fileも保存済みの正確な一覧に従って削除し、
すべての不在を確認した。Stage/run・監査・保存BLOB・内容dedupの証拠は保持した。

続いて残る5件を通常grant APIで剥奪し、35/35照合が成功した。
P4-5検証用6 grantすべてがdesired `revoked` revision 2、effective `revoked` epoch 3であり、
旧14件のdesired grantを保持した。この照合時点ではprivate profiles 8件をGUI確認用に保持した。
最終generation `p45-scan-final`のfull PLANをroot・独立レビュー後にbindし、
operation `331f6d0c-a698-476c-a6e3-c6f8f73e41da`の最初のapply receiptで`APPLIED/full`を確認した。
canonical request revisionは17、`mutation_started=1`、6 stepすべて`SUCCEEDED`で、
LOCALは`OPEN` revision 105・適用generation `p45-scan-final`となった。
保存したGO前の6件`PENDING`と適用途中の`RUNNING`は過去経過として保持し、完了receiptと区別する。

実際のactive OPA 145件と、完了したProductionOperatorのbackend SQL probe 15件を確認した。
通常8888/8080のattestationは各6/6成功し、検証用owner/otherのprofile contextはともに0で、
全6 grantは`revoked` revision 2・epoch 3を維持したままapplied revision 2へ揃った。
最終照合38/38が成功し、元5DB・20Schema・321 ResourceRef、旧14 grantのdesiredと実効権限、
31 SecretのUID・内容、元4利用者の認証情報、固定sourceとGo binaryを保持した。
実際の両listener PIDはPython 3.12.12と確認し、未解決実行0、過去の`FENCED_READ_ONLY` 2件を保持した。
検証用補助入口8891/8893は停止している。
独立した最終保全auditも30/30成功し、17台帳の履歴・claim・保存入力BLOBのhash保持を確認した。
これは認証撤去後の復帰確認とは区別する。

証拠：`runtime/finite-cleanup-completion.json`、`runtime/finite-cleanup-progress.json`、
`finite-cleanup/state.json`、`runtime/final-five-revokes-summary.json`。
最終適用は`runtime/generation-actions/p45-scan-final/apply/first-receipt.json`、
`runtime/active-opa/p45-scan-final/331f6d0c-a698-476c-a6e3-c6f8f73e41da/matrix.json`、
`runtime/attestations/final-ready/summary.json`、`runtime/final-ready/profiles-first-receipt.json`、
`runtime/final-ready-summary-v3.json`、`runtime/final-generation-independent-review.json`。
production receiptは`var/shirokuma/p2-6-acceptance/live/state/warehouse-operator-receipts/331f6d0c-a698-476c-a6e3-c6f8f73e41da.json`。

### 認証撤去・通常入口復帰・元タブの最終確認

撤去対象を`runtime/final-auth-teardown-proposal.json`へ固定し、GUI証拠の保存後に
レビュー済み7 phaseをすべてexit 0で完了した。検証用2利用者と正確に指定したprivate profiles 8件を撤去し、
元4利用者の認証file・password fileは初期baselineのbytesと一致した。
同じ`p45-scan-final`で通常2入口を再起動し、実listenerはPython 3.12.12、attestation成功、doctor healthyとなった。
追加SQLやbackend applyは行わず、55 SQLite control-store台帳のhash・件数を保持した。

credential残存検査は6,384 fileを対象に、現行credentialの検索値8件と撤去対象の検索値4件で実施した。
scanner exit 0、撤去対象の許可例外0、想定外match・読取り失敗・権限問題はいずれも0だった。
独立した認証撤去後照合も26/26成功した。これは元タブの実画面確認と別の証拠である。

復帰後の実ブラウザーでは元2SQLタブの8/8照合が成功した。
両タブのSQLとDB・Schema・role・mode・warehouseの5選択値、タブID・順序・選択中tab2を保持した。
tab1の旧DBは利用不可のまま保持し、別DBへ付け替えていない。
tab2は通常Refresh後、同じ5選択値のまま明示的にReselect contextを行って新generationへ更新し、
Ready・実行可能・`Failed to fetch`なしを確認した。generation変更時の自動再bindは行わず、SQLも実行していない。
Explorerは独立したcontextであり、今回は再選択していない。全paneがReadyになったとはしない。

GUI操作後の統合読取り照合も18/18成功した。認証撤去completion後からの55台帳のhash・件数は完全に一致し、
source 195件、LOCAL OPEN revision 105、元5DB・20Schema・321 ResourceRef、旧14 desired grantを保持した。
検証用認証2件・private profiles 8件は不在、補助入口8891/8893は停止、未解決実行0、
過去の`FENCED_READ_ONLY` 2件も保持した。
最後の独立統合レビューも13/13成功した。GUIの原AX/DOMと開始時baselineから、
失効表示13/13・元タブ8/8を再導出し、owner window終了4/4の記録範囲、55台帳と実行件数、
元認証・撤去対象不在・source 195件の保持を確認した。P4-5の技術範囲に残るblockerは0である。

証拠：`runtime/final-auth-teardown/completion.json`、`runtime/final-auth-teardown/hygiene-first-receipt.json`、
`runtime/final-auth-independent-review.json`、`ui/original-tabs-final-proof.json`、
`ui/normal-final-dom.json`、`ui/normal-final.txt`、`ui/normal-final.png`、
`ui/normal-tab1-preserved-dom.json`、`ui/normal-tab1-preserved.txt`、`ui/normal-tab1-preserved.png`、
`ui/normal-tab2-ready-dom.json`、`ui/normal-tab2-ready.txt`、`ui/normal-tab2-ready.png`、
`runtime/post-gui-integrated-proof.json`、`runtime/final-integrated-independent-review.json`。

## 受入状態

P4-5の実装・通常入口・実機故障検証・失効・清掃・最終generation・認証撤去後の復帰と最終照合をもって、
**P4-5の限定技術受入を完了**した。P4-5技術範囲の必須残件は0。
Phase4全体の最終受入、P4-6以降、所有者の最終操作性評価は後続計画とする。

## 適用範囲と限界

- 内容dedupはfile単位であり、行単位dedupやSQLite/Iceberg間のexactly-onceは保証しない。
  自動scan・予定実行はP4-7の範囲である。
- 実機のUNKNOWNはcommit後のitem記帳喪失で確認した。backend SQL自体の真のUNKNOWNは単体テストのみで、
  transport失敗は注入していない。PIDやTTLだけを理由にした復旧・再送は行っていない。
- 実ブラウザーではdraft・Stage・revisionの保持を確認した。focus/caretの競合は動的UIテストの証拠であり、
  実機で同じ競合を測定したとはしない。
- 保存比較の旧「51テーブル」はSQLite control storeの台帳であり、物理Iceberg Tableの件数ではない。
  ProductionOperatorの15 SQL probe、各対象Tableのtyped rows照合、台帳の保持確認はそれぞれ区別する。
- 過去のfalse assertionは元証拠を保持し、上記のencoding・比較基準・CSV解析前SQL・enum/statusの期待を
  `superseded`として扱った。公開`retryable`の現在値と保存履歴を同一視した期待も区別した。
  最終照合でも`ps`の表示名から実行ファイルを推定した観測と、適用に伴うbinding CASの期待を訂正した。
  同じ実PIDの実パス、binding identityと正常なCAS進行を別に確認し、元の34/35・36/37記録を保持した。
  有効な723件・481件等と元の記録総数を混同せず、再送で成功記録に置き換えていない。
