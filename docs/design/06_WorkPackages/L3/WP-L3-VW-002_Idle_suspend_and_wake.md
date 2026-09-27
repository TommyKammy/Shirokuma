---
project: Shirokuma
doc_id: "WP-L3-VW-002"
title: "Idle suspend and wake for VirtualWarehouse"
status: local-partial
created: 2026-07-05
updated: 2026-09-28
version: "0.16"
area: "workpackage"
tags: [shirokuma, wp, l3, warehouse]
---

# Idle suspend and wake for VirtualWarehouse

| Field | Value |
|---|---|
| WP ID | WP-L3-VW-002 |
| Level | L3 |
| Workstream | Warehouse |
| Depends-on | WP-L3-VW-001, WP-L0-OBS-001 |

## 2026-09-28 safe empty socket directory retention — offline

前増分はsocket `unlink`を元directory fdへ固定したが、その後の空directory `rmdir`はpathで実行していた。隔離tempで元の空dirを退避して同pathに別の空dirを置くと、旧`close()`は模擬別dirを削除。POSIXのpath-based `rmdir`には確認済みinodeとの原子的な一致条件がないため、ownerはsocketを閉じて相対unlinkした後、private `shirokuma-wl-<session-id>` directoryを**自動削除せず残す**。close中に開いた元dirで余分なentryの不在を確認し、存在すれば削除せず`SOCKET_DIRECTORY_NOT_EMPTY`で拒否。通常の新UUID sessionは従来どおり動作し、同IDの再利用は既存dir衝突で拒否。実Unix owner/Go query integrationを2経路で確認し、テストが作成した固定IDの空dirのみowner/inode/mode/空内容を照合してテスト側で除去。実保持run/backup、Colima/SQLには接続せずWP local-partial。

残置は1 sessionあたり空directoryのinode/entryを消費し、OSの一時領域管理に依存する。host backup/exportへの包含は未確認で、保持backupには触れない。保持するrootや別actorのdirを自動cleanupせず、operatorが個別のidentityと使用状況を確認する。既存保存`cleanup.verified`はKubernetes等のowner cleanup claimであり、host上の全path不在や残置dirの独立確認を意味しない。同UID actorによる元dir内socketのcheck/unlink間改変、owner再起動後の未知request回復と現在backend/最終cleanup独立証明は未達。復旧時は新ownerを止め、保存runを残してコードだけ以前のrevisionへ戻し、unknown要求は再POSTしない。private検証は`tmp/local-experiment/warehouse-socket-directory-retention-checks/20260928/`。

Warehouse focused143件PASS、最終全回帰121 suites/3815実行＝3807PASS＋8skip、89.155秒、log SHA256 `b0522bf0eef046f6f9fc87b8f88cf636099fc8b4f6a51e8945f2cdbe5af51e88`。design-context/secret scan・diff check PASS、専用Colima Stopped。初回integrationは旧「dir不存在」期待で失敗し、固定IDの新規空dirだけ検証して除去した後に新契約へ合わせて通過。余分なentry拒否・有界fd走査追加後に全回帰を再実行した。

## 2026-09-28 descriptor-bound socket unlink on session close — offline

終了処理の`close()`はsocket inode確認後にpathを再解決して`control.sock`をunlinkしていた。隔離tempで確認直後に所有socket directoryを移動し、現pathを模擬別socket treeへのsymlinkへ差し替えると、修正前は**別socketを削除**した（元socketは残存、後続rmdirは失敗）。新実装はsocket directoryをno-followで開き、fdのowner/mode/inodeとfd相対socketのtype/owner/mode/inodeを照合し、同fd相対でunlinkする。open前差し替えはunlink前に拒否、open後差し替えは元socketだけを除去して現pathの不一致を拒否し、模擬別socketは残る。通常の終了は従来どおりsocketと空directoryを除去。差し替えで拒否された元dirや別actorのsocketは自動削除しない。終了時の空directory `rmdir`には同UID actorによる最後のpath差し替え競合がなお残り、元dir内socketのcheck/unlink間の同UID改変も完全排除しない。保持run/backup・Colima/SQLは変更せず、owner再起動後の要求回復・現在backend/cleanupの独立証明ではない。WP local-partial。private検証は`tmp/local-experiment/warehouse-socket-close-fd-checks/20260928/`。

復旧時は新session起動を止め、保存runを保持しコードのみ以前のローカルrevisionへ戻す。残った所有socket directoryは自動で消さず、個別にidentityと使用状況を確認する。未知requestは再POSTしない。

Warehouse focused141件PASS、全回帰121 suites/3813実行＝3805PASS＋8skip、89.461秒、log SHA256 `8c0bb875073836ca6bc2658bbd5644231337dc79da1b43e15507c8d0f37f6720`。design-context/secret scanとdiff check PASS。修正前の負例は模擬別socketの削除と元socket残存、隔離testのteardown失敗を記録。修正中のopen後試験では元socket削除の期待値を旧値として1件失敗し、期待値修正後にfocused・全回帰通過。専用Colima Stopped。

## 2026-09-28 descriptor-bound session journal writes — offline

短命ownerの`SessionRPC._persist()`はjournal directoryのpath identity確認後、path指定の`mkstemp`/`replace`/fsyncを再実行していた。同UID actorが確認と書込みの間にjournal親pathを別treeへ差し替える境界を、隔離tempのopen前/直後の差し替え試験で固定した。新実装はdirectoryを`O_DIRECTORY|O_NOFOLLOW`で開き、owner/mode/inodeを照合し、`journal.json`既存fileのno-follow metadata、exclusive temp作成、atomic replace、directory fsync、temp cleanupを同fd相対に限定。成功replace後はtempを再削除せず、失敗時も作成した同一inodeに限ってcleanup（別actorが置換したtempは残す）。open前差し替えは保存前に拒否、open後差し替えは元directoryへだけ書き現path identity不一致で拒否、模擬保持先は空。元directoryや差し替えたpathは自動削除しない。これは同UID actorによる元dir内の改変、保存物の独立認証、owner再起動後の未知request回復・現在backend/最終cleanupの確認を証明しない。実archived session/backup・Colima/SQLには触れずWP local-partial。private記録は`tmp/local-experiment/warehouse-journal-fd-checks/20260928/`。

復旧は新しいsession ownerの起動を止めたうえでコードだけを以前のローカルrevisionへ戻し、既存のunknown requestを再POSTせず保存runをそのまま残す。fd失敗時に残る元dir/別actorのtempも自動削除せずoperatorが個別確認する。

Warehouse focused139件PASS、全回帰121 suites/3811実行＝3803PASS＋8skip、89.685秒、log SHA256 `5c73e804e69dca32c85856ba2d51e9b924d58e6924c41374158466c6ff62c472`。design-context/secret scanとdiff check PASS。最初の負例はopen後の正当な`UNSAFE_PATH`拒否を旧期待codeにして1件失敗し、期待を修正して再実行。最後の置換temp負例追加後にも全回帰を再実行した。専用Colima Stopped。

## 2026-09-27 closed session recovery guard — retained-run protection

従来の汎用`local-warehouse-lifecycle-recover`は、保存済みXS sessionが`closed/passed`でもlockを開いて`recovery.json`を書き得た。既存完了sessionのread-only receiptをVM/cluster check・lock・回収より前に確認し、`RECOVERY_CLOSED_SESSION`で拒否。lock取得後にも再読して状態変化を拒否する。汎用runの従来回収・失敗runのretry経路は保持。Make recipeのtokenをシェル補間から環境変数渡しへ変更し、PythonでID検証する。実archived2 sessionはMakeから非0/固定codeで拒否され、保持ファイル集合・SHA/inode/modeは不変。不正なtokenを介したシェルcommandも実行しない。初回smokeはMakeが失敗時に出す通常stderrを誤って空と期待し検証側が失敗、期待値を修正して再実行成功。専用Colimaは停止のまま、実cluster回収/バックアップ復元/現backend確認は未実施。保存receiptの`passed/closed`も独立した現cleanup証明ではなく、owner再起動後のrequest再POST/回復は未実装。WP local-partial。private証拠は`tmp/local-experiment/warehouse-recover-closed-guard-checks/20260927/`。

warehouse focused5 suites/167件PASS、全回帰121 suites/3798実行＝3790PASS＋8skip、87.669秒、log SHA256 `da220f2e37f331de36e43be5d23acac6054273beaade1e8dd6d231730a3949e8`。design-context/secret scan PASS、専用Colima Stopped。保持archived2 sessionはlock/recovery file追加なし。

## 2026-09-27 saved completion requires exact final replies — offline

`correlated-saved-claim`が同UUID・別actionの拒否replyや最終reply欠損を含むjournalでも、receipt件数を合わせれば通ることを修正前に再現。opt-in completion照合に限り、全replyが元requestの完全一致を要し、全operationに保存responseそのものの**最終replyが1件以上**あることを確認する。正当な同requestの処理中`unknown`は許可。競合・最終reply欠損は`SAVED_COMPLETION_UNVERIFIED`、CLI stdoutなし。journal-only statusは拒否された競合をなお表示可能で、旧入口は変更しない。実archived2 sessionのcompletionは通過し、元journal SHA不変。これは同じownerの保存物の整合性のみで、callerへのreply到達、実backend/cleanup/監査の独立証明ではない。owner/SQL/Colima/保持run/backup変更なし、WP local-partial。private証拠は`tmp/local-experiment/warehouse-completion-reply-checks/20260927/`。

warehouse focused5 suites/165件PASS、全回帰121 suites/3796実行＝3788PASS＋8skip、87.429秒、log SHA256 `5cccc7b5774c123ec1b97cbc2f9b49ebc14f35db208fe81c100883f1a6a1ae3d`。design-context/secret scan PASS、archived2 sessionのcompletion JSONは旧出力とbyte一致、元journal SHA不変、専用Colima Stopped。

## 2026-09-27 archived RPC negative-reply correlation — offline

保存statusが成功replyは元request/保存応答と相関していた一方、`unknown/busy/failed` replyは同UUIDだけで受け入れる隙間を修正。元requestと完全一致した**保存responseそのもの**、または実ownerが処理中/同ID別action競合時に出す**operation/receiptなしの`unknown`**だけを許す。元actionと異なるreplyがoperation ID/hashを主張する場合や、保存結果にない`busy`を捏造した場合は`JOURNAL_UNVERIFIED`で部分JSONなし。修正前に不正3種が誤受理されることを再現し、修正後拒否。実Unix ownerの同ID競合・query replay、正当な処理中replyを確認。archived2 sessionの旧status JSONはbyte不変、保存完了claimも通過、元journal SHA不変。保存物は同一owner由来であり、独立実行・監査配送・現backend/cleanupは証明しない。owner/SQL/Colima/保持backupは変更せず、WP local-partial。private証拠は`tmp/local-experiment/warehouse-journal-reply-contract-checks/20260927/`。

warehouse focused5 suites/164件PASS、全回帰121 suites/3795実行＝3787PASS＋8skip、87.473秒、log SHA256 `f463db4631dada04df4ac6b9292743a6a3b04b8f6147a6ded70c48947abd6421`。design-context/secret scan PASS、専用Colima Stopped、archived2 sessionの元journal SHA不変。

## 2026-09-27 lost-reply guidance across CLI paths — offline

`chill/wake`の通信失敗時に旧「同じIDでもう一度確認」としか示さない誤解を修正。`chill/wake`と`warehouse query`のいずれも元session/UUIDを保持し、固定`warehouse saved-request --session ... --request-id ...`の**read-only保存照会**を先に案内する。同じUUIDのRPC再送は**同じownerが生存中（有界replay graceを含む）に限る**と明記し、owner退出後/別sessionへの再送を禁じる。保存上のpending/不在を「未実行」や再POST許可に変換しない。自動再送、live owner判定、実backend/cleanup確認、保持run変更なし。既存transport・owner・保存statusの実行契約は不変。local-partial。private検証は`tmp/local-experiment/warehouse-lost-reply-guidance-checks/20260927/`。

明示UUIDと自動生成UUIDの両方でRPC failure時に元session/UUIDが診断に残り、追加呼出し0・部分stdoutなしを確認。`gofmt`、`go test ./...`、`go vet ./...` PASS。全回帰121 suites/3794実行＝3786PASS＋8skip、90.383秒、log SHA256 `c82d2efd24e8becbe18cab532e84774fdfbd755c72b8db977d693dbb3a9d1053`。design-context/secret scan PASS、Colima Stopped。

## 2026-09-27 archived request lookup in Go CLI — offline

`shirokuma warehouse saved-request --session <session-id> --request-id <original-uuid> --profile local-experiment`を追加。既存live query/transitionとは別コマンドで、固定checkoutの既存保存status scriptを専用Pythonで1回だけ起動。session/UUID/profileの事前検証、固定子プロセス引数・空環境、上限付き1行JSONのsession/UUID・`runtime_state=unknown`検査、失敗時の部分JSON抑止を行う。任意socket/SQL/owner回復/scaleの入口は追加せず、旧Make/Python・live queryの契約を維持。archived2 sessionの元UUIDをGo CLIからread-only確認し、保存記録上のquery/wakeとruntime unknownを確認。欠損IDは`not-recorded-in-snapshot`で未実行証明ではない。元保持journal/backup変更なし、Colima起動なし、owner再起動後の要求回復・現在のbackend/cleanup独立確認は未達。取り消す場合は新CLIを使用せず、保持物の復元・移行不要。private証拠は`tmp/local-experiment/warehouse-saved-request-cli-checks/20260927/`。WP local-partial。

`gofmt`、`go test ./...`、`go vet ./...` PASS。Go CLIで不在UUIDはnullの保存状態、無効UUIDはstdoutなしで失敗。最終全回帰121 suites/3794実行＝3786PASS＋8skip、88.396秒、log SHA256 `7e6250d2889d5bd20b99900fb63123b50a34dfa0569d428863695f4e98f4a350`。design-context/secret scan PASS。変更前後の保持journal SHAは不変、専用ColimaはStopped。

## 2026-09-27 archived request-ID lookup without replay — offline

紛失したRPC応答の**元request UUID**を1件だけ照会する`make local-warehouse-session-request-status WAREHOUSE_LIFECYCLE_RUN_TOKEN=... WAREHOUSE_LIFECYCLE_REQUEST_ID=...`を追加。既存固定private journal readerの保存値にexact UUIDで束縛し、記録上のaction/result、operation IDとreceipt相関hash（ある場合）を投影する。`pending/unknown`を成功にしない。見つからないIDは`not-recorded-in-snapshot`＋null値であり、実際に要求が**未実行だったとは証明しない**。UUID不正は読取前に拒否し、Make引数をrecipeへ展開せず環境変数経由。既存all-entries status JSON bytesを維持し、ownerへのRPC/SQL再送や新たなGo CLI child実行は追加しない。

実保存2sessionの元UUIDでquery/wakeの保存結果を個別照合し、存在しないcanonical UUIDはnot-recordedと確認。元journalの内容SHA不変、現warehouseは常にunknown。前回のopt-in完了claimはsession全体の保存相関であり、欠損requestの未実行証明にはならない。Colima停止、保持run/backup変更なし、owner再起動後のlive照合・要求回復・汎用warehouse/本番受入は未達。private証拠は`tmp/local-experiment/warehouse-session-request-checks/20260927/`。WP local-partial。

warehouse focused5 suites/163件すべてPASS、全回帰121 suites/3794実行＝3786PASS＋8skip、88.177秒、log SHA256 `ff01430e0896ba09903310dc65a9aaf0713eb94d8693de2a066983fb7cd97dc6`。design-context/secret scan PASS。保存journal原本SHAと旧全件status JSONのbyte不変を確認。

## 2026-09-27 saved owner completion claim correlation — offline

既存journal-only statusを維持したまま、opt-in `make local-warehouse-session-completion WAREHOUSE_LIFECYCLE_RUN_TOKEN=<session-id>`を追加。固定sessionのprivate最終`receipt.json`、全`operations/<operation-id>.json`とRPC journalを有界no-followで読取・再読し、closed/passed/cleanup・telemetryの保存claim、request/response順、operation結果と**raw file bytes** SHA、reply数と余分なoperation file不在を照合する。欠損・矛盾・途中差替えは部分JSONを出さず`SAVED_COMPLETION_UNVERIFIED`。旧status入口のJSON bytesは変えず、実保存2sessionsは記録2/4 operationsで`correlated-saved-claim`。両セッションの元journal/receipt/operationのSHA、inode/mode/mtimeは前後一致。

初回の新CLIはoperationに存在しない`receipt_sha256`をfile fieldとして比較し拒否。修正後もcanonical dict hashを試して拒否されたが、ownerが実際にはoperation **file生bytes**をhashしていると確認して修正した。失敗を隠さず、既存保存ファイルを再生成・修復せずに成功。`correlated-saved-claim`は**同じownerが残した複数保存物の内部整合のみ**で、実backend query、独立audit delivery、VM/owner現在状態、最終cleanup実施の独立証明ではない。表示`runtime_state=unknown`を維持し、owner再起動・再POST/scale・recoveryなし。Colima停止、保持run/backup変更なし、WP local-partial。使わなければofflineコマンドを呼ばず、移行/削除は不要。private証拠は`tmp/local-experiment/warehouse-session-completion-checks/20260927/`。

warehouse focused5 suites/161件すべてPASS、最終全回帰121 suites/3792実行＝3784PASS＋8skip、87.615秒、log SHA256 `c44f9e33dfc479526591b41bda8f69f89e869f556edd9d73ab3c64ea14c5efd1`。design-context/secret scan PASS。元2sessionのjournal/receipt/operation file集合・SHAとmode/inode/mtimeは前後一致。

## 2026-09-27 archived warehouse RPC status — read-only local increment

`make local-warehouse-session-status WAREHOUSE_LIFECYCLE_RUN_TOKEN=<session-id>`を追加。固定warehouse runs rootの一つのprivate `rpc/journal.json`のみを有界no-followで読み、owner/mode/link/root deviceと読取前後metadata、session/request/response/replyの保存契約を照合する。`recorded_result`は当時のjournalの記録であり、出力の`runtime_state`は**常にunknown**。保留中・失敗・不正/途中変更を成功に昇格させず、欠損/不正は`JOURNAL_UNVERIFIED`で拒否。取引時のreceipt本文・操作の実在・owner最終cleanup・現warehouse readinessを独立証明しない。journal SHAは相関用で署名ではない。

既存短命owner・Go CLI/ query送信の契約を変更せず、owner再起動後の実行再開・再POST・永続queue・自動復旧は未実装。停止中Colimaへ接続/起動せず、既存2件のarchived journalをread-onlyで投影した。記録上の結果は一方が2 actions、他方が4 actions、両方の現状態はunknown。内容SHAとmode/inode/size/mtimeは前後で一致し、credentials・旧run・backupの書換え/削除なし。作業を止めるにはoffline statusコマンドを使わず、旧記録の復元操作は不要。証拠は`tmp/local-experiment/warehouse-session-status-checks/20260927/`。WP local-partial。

warehouse focusedは5 suites/158件すべてPASS。最終全回帰121 suites/3789実行＝3781PASS＋8skip、85.694秒、log SHA256 `1cc7f6a2e0d7f59fa37f234086e8b23716ea442fd39be4be6c5e098909146f0d`。design-context/secret scan PASS。journalの`sha256`は同一保存bytesの相関だけで、同一ユーザー等による偽造や保存後の変化を排除しない。読取に伴うatime影響は除外せず、backup復旧の検証はしていない。

## 2026-09-18 query-triggered wake plan

停止中の生成XSへ固定SELECTを要求し、同じownerがwake完了後に一度だけ実行する。
`session run --query-mode`と`shirokuma warehouse query XS --session TOKEN`を追加する。
旧chill/wake、既存wakeのXS/S probeを維持し、callerは別case/query ID/sourceで束縛する。
任意SQL・保持catalog・新resident service・LLM呼出しは追加しない。

- [x] chill済みXSのquery要求をdurable intentで記録し、同じUIDのDeploymentをwakeする。
- [x] Pod/process/Endpoint/registryの確認と既存probe完了後に、固定TPCH SELECTを一度だけPOSTする。
- [x] 25nation結果・XS自身の終端履歴・Sへfallbackしないこと・全handle消費を照合する。
- [x] 要求UUID・SQL/結果hash・query ID・監査を束縛し、完了応答の保存後だけ成功を返す。
- [x] 生存ownerと終了後15秒grace内の同ID再送は同一応答で、追加scale/POSTを行わない。
- [x] 失敗・取消・切断・不明結果で再POSTせず、所有資源の回収結果を保持する。
- [x] 既存実験を保持して実CLI/監査/回帰を確認し、Vaultへ結果と制限を反映する。

待機時間には既存wake probe・guard・監査が含まれ、cold-query性能の証明にはしない。
短命・単一ownerの固定XSだけが対象であり、owner再起動後の要求復旧、任意warehouse/query、
一般tenant認可、HA、常駐queue、実課金削減、本番受入は未実装として残す。

## Scope

Implement idle suspend/wake semantics for Trino VirtualWarehouse classes. This is a Shirokuma controller behavior, not native Trino scale-to-zero.

## Acceptance Criteria

- Controller detects idle warehouse.
- Coordinator/worker deployments can be stopped and restarted.
- Gateway routing is updated or health checks prevent routing to stopped backend.
- `shirokuma chill` and `shirokuma wake` commands operate on warehouse definitions.
- Pawprints record suspend/wake decisions.

## Notes

Plain Trino does not provide Snowflake-like warehouse suspend by itself.

## Definition of Done

- Work is represented as a GitHub Issue linked to `WP-L3-VW-002`.
- Changes are committed through PR.
- Required CI and policy checks pass.
- Documentation and WBS are updated.
- Any deviation from ARM64-native execution is recorded in [[10_Research/106_ARM64_Container_Image_Compatibility]].

## 2026-09-17 local experiment plan

ADR-0034/DEV-04Bの承認済みlocal loopで、新規runだけを対象に停止/再開を実装する。
旧Issue/PR/都度承認gateは外部delivery向けであり、このlocal loopの実行条件にはしない。
Gateway/PGを専用Pod、Trino XS/Sを別々の新規Deployment＋Serviceに分離し、
11新規resourceとcontroller生成RS/Podのowner UID chainだけを管理する。
既存Trino/catalog/Lakehouse/DB/PVCへの接続、ホスト/Colima全体の変更は行わない。
固定ARM64 imagesはVW-001のbuild証跡を再利用する。合成TPCHの同じ25nationを検証する。
Serviceの実clusterIPは作成応答と所有権を照合して登録し、DNS/外部向け通信許可は追加しない。

停止判断にはfreshな全query一覧とhost側POST intent/未完了handleの両方を使う。
Trinoは受付直後のqueryが最初のGETまで一覧へ現れないため、サーバーの0件だけでidleとしない。
既知handleの未完了・POST結果不明・別client queryがあれば停止せず理由を保存する。
既知queryの終端を確認してからGateway deactivate→再drain確認→UID/RV付きreplicas0。
wakeは同じDeploymentをreplicas1へ戻し、新しいPod UID/readiness確認後にactivateする。
停止中もSへのSELECTが成功し、XSがSへfallbackしないことと、再開後のXS結果不変を確認する。

同一hostの単一coordinator/worker構成であり、本番の分散drain、独立fault domain、
HA、汎用CRD reconciliation、無期限の自動運転、原価削減率を証明しない。
CLIとPawprints連携を含め観測できた範囲だけをlocal-partialとして記録する。
未観測項目や実装段階は実機結果と分け、実行前の計画を完了扱いにはしない。

## 2026-09-17 local functional evidence

同期run `97b8a7289b2e42e496a1d87bddd2b4ad` とCLI session
`c41cf9f753c14275837ee9f453ed2ad0` が成功した。どちらも7固定requestを検証し、
初期XS/S・未完了handleの消費・停止中S・再開後XS/Sの6SELECTが25nation行と実node IDで一致。
停止中XSはHTTP500・空body・query IDなしでSの全query一覧へ現れず、Sのprocess IDは不変。
host未完了handleが1件ある時、サーバーの全query一覧が0件でも停止を延期した。
完了後のfreshなidle/drain観測を経て、同じXS Deploymentだけをreplicas1→0→1へ変更し、
新しいPod UIDとTrino coordinator ID、空の新XS履歴、Gatewayの再配送を確認した。

実compiled Go CLIから6commandを実行：prepared状態のwakeはnoop、chillはverified、
同request IDのchill再送は同一応答、別IDのchillはnoop、wakeはverified、その再送も同一応答。
4個のowner operation receiptと6応答をprivate保存し、再送によるscale追加は0。
socketは同UIDの0700 directory/0600 Unix socket、型付きrequest/responseとdurable journalを使用。
受付は準備後10分・16接続、成功wake後15秒は既存応答の再取得だけを許す。
CLI切断は実行開始済み操作を取り消さず、ownerが検証・回収まで担当する。
これは生成済み固定planのXSだけを対象にした短命sessionで、任意warehouse定義の操作ではない。

各runは17個の既知UIDを明示回収し、停止時にcontrollerが消した旧XS Podも残留0。
3decision＋1runのPawprint/Loki/実OTel spanをそれぞれ4件照合した。
busy-deferはblockedだがpolicy拒否ではなく、usage/costはNULL。
同期trace `2823135a5def108632ed71493ce72530`、CLI trace `ced9a0cbc583bf91dbeed4541eddf247`。
OTel child spanは監査deliveryを計測し、先に実行したcontroller操作時間を遡ってtrace化しない。
Pawprintには別途測定したcontroller durationを保存する。

初期4runはfailedとして保持した。Kubernetes生成Pod名の切り詰め、空EndpointSliceのnull値、
停止中PodのSucceeded状態、Gateway inactiveとhealth表示の非同期更新、TCP TIME_WAITを修正し、
UID/spec/配送対象の確認を維持して回帰テストを追加した。最初のrecover失敗もarchiveを残し、
次のrecoverで既知UIDのみ回収した。CLI初回はscript相対importでruntime生成前に失敗し、
入口のimport修正と実script回帰を追加した。失敗receiptを成功へ書き換えていない。

証跡は `tmp/local-experiment/warehouse-lifecycle/runs/<run-token>`。
[Runbook](../../../../tools/local_experiment/warehouse_lifecycle/README.md)とMake targetsで再実行できる。
汎用CRD reconciliation、複数workerのgraceful drain、tenant認可/HA/常駐運転/本番受入は未達。
次は既存Data Bearの3固定operationを自然文入力から利用できる入口を実装する。

独立最終監査 `warehouse-lifecycle-audit/80d8bfbffe19418b82b249aed451dd92`（08:17:20 UTC）で、
保持6Pods/27resources/6CM/3Secret UIDs、3Dockerの全設定/起動時刻/restarts、履歴4表の全行hash/5DDL、
従来22証跡filesが不変。Pawprints330＝従来311件不変＋今回19件、同じLoki19件と保存span IDsを照合。
失敗4runの11件も残し、成功2run各4件と区別した。全6runtime runsと初CLI startup failureの残留0。
CLI6応答/4operationのraw SHA256、実binary hash、同IDの完全同一応答、scale2回だけを独立再確認。
全回帰836件（835成功・標準環境のoptional OTel1skip）後、更新済みfocused112件がすべて成功。
同OTel結合は専用venvで成功。Go test/vet/race、設計79documents/関連14tests、diff checkも成功。

## 2026-09-18 query-triggered wake evidence

`warehouse query XS`をquery-modeの短命ownerへ接続した。旧chill/wakeと7固定caseは維持し、
既存wake probes後に別source/request UUIDの`xs_caller`を追加した。callerのintent、POST intent、
query ID、固定SQL/結果hash、終端履歴、UID/scale chain、監査を束縛してから成功応答を保存する。
query CLIは240秒待機し、切断でownerを止めず、same-ID再照会以外の自動再実行をしない。

実session `5edf12a5864e4a1391b8c4d1a7d309da`でGo CLIのchill→query→same-ID query再送が成功。
caller request `7ce1b703-b16c-4375-8db6-ead09ae38485` はSQL POST1回、
query `20260917_152707_00001_gps5i`の25nation行とXS nodeが一致した。
結果hashは `eba954dccf4cef71463d4c4ffe8f7dfbd3777be9660c71265791467025f4acc1`。
再送応答はbyte単位で同一、追加SQL/scaleは0。RPCは3接続・2実operationを記録した。
全8caseの結果/handleを消費し、XS終端履歴はwake probeとcallerの2件、Sは既存3終端caseだけだった。

同じXS Deploymentを1→0→1に変更し、新Pod/processを確認、SのPod/Deployment identityは不変。
17既知UIDを明示回収、停止時にcontrollerが削除した旧XS Podも残存0、conflict/deferredは0。
caller計測はwake待機26059ms、query client3283ms、監査前までの全体31581ms。
前者には起動/probes/監査/guard、後者には結果取得/host処理が含まれ、cold-query性能とはしない。
busy/chill/wake/query/rootの5件をAgentHouse/Loki/実保存OTelで照合し、
traceは `dc95af2097fd91da1fe61ecaac05276e`。LLM/cloud呼出し0、usage/costはNULL。
query成功応答後の回収と最終root監査まで含め、owner exit0・session passedを確認した。

新query22・query telemetry15・RPC追加6のPython43件と新Go6件が成功。
実Go→Unix RPC→実owner/controller/client（外部だけfake）結合で同ID再送とprechill拒否を確認した。
厳密な型、scope/hash、UID/scale履歴、取消の原object、保存障害/監査失敗/改変したspanを確認。
旧session/chill/wakeを含む全local回帰1701件（1700成功・既存optional OTel1skip）、
Go97 top-level/59 subtests、vet/race、設計83documents/関連14tests、secret検査が成功した。

実行・検証証拠は `tmp/local-experiment/warehouse-query-checks/f735cfb20dcc4bcd9c532b7d64f9f086/`、
owner証拠は `warehouse-lifecycle/runs/5edf12a5864e4a1391b8c4d1a7d309da/`、
独立監査は `warehouse-query-preflight/bded3460dc4d4afba5459680c4c3604c/`。
fresh前後18checkで旧AH465全行hash、保持3478files、14Pods/27resources/3Docker/MLflow、
16native policiesとnamespaceが不変、現AH470、生成resource残留0、ports/locks/socket閉鎖を確認した。
fixture/SQL履歴/Metadata/MLflow全論理データは直前監査のsealを継承し、全再走査していない。
任意SQL/warehouse、実tenant認可、owner再起動後復旧、常駐queue、HA、一般並行writer、本番受入は未達。
