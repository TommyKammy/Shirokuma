# P5-1 作成受付・委譲・永続停止 実装報告

2026-10-23。[P5-0契約](database-p5-0-contract-2026-10-23.md)に従うP5-1の技術検証記録。
core実装、独立レビュー、標準検証、隔離した実入口確認、既存環境の移行・再起動を完了した。
P5全体の受入完了を意味しない。実foundation/owner接続と新WebUI導線は後続フェーズに残る。

## 実装した境界

- migration19に作成root、phase plan、要求/承認、principal gateの4表を追加した。既存66表は維持する。
- 作成受付は同keyの保存ACKを先に解決し、warehouse予約、既存未適用変更、admissionを再確認する。
  root・DB・固定4Schema・表示metadata・初期desired grant・child関連・ACK・予約を同じSQLite transactionで登録する。
  初期effective grantは作らず、要求者への暗黙grantも付けない。
- 承認、撤回、再計画、照会要求を永続化し、元要求者と独立したexecutorをtyped permitで結ぶ。
  現在のaccount権限、環境/enrollment、gate revision、承認、plan、operation、attempt、cursorを再照合する。
- principal停止と未完了承認の撤回を一緒にcommitする。再有効化しても古いclaimは復活しない。
  authority喪失時は業務transactionをrollbackし、別transactionで停止を永続化する。
- P5との関連・warehouse予約を既存coreから必須確認し、同一actorの旧API/CLIやcallback省略でも迂回できない。
  SQL、ToFu、provider/source、snapshotの新しい送信intentと最終確定にも境界を設ける。

既存関数の外部signatureを維持し、原子的登録に必要なconnection受取helperを追加した。
既存のJob/query観測・固定回復と、P5に属さない通常操作の互換性を別途確認する。

## 未実装フェーズとの境界

P5-2の増分材料生成とP5-3の実owner/IPC/runtime引継ぎは未実装である。
実foundationとownerを毎回検査するprivate adapterが存在しない通常起動では、新規公開Createは
`503 FOUNDATION_NOT_READY`で登録前に停止する。設定flagや公開JSONで準備完了にはできない。
準備・照合要求はblocked状態を公開し、未実行を成功・READYとして表示しない。

正の作成・承認・委譲試験には隔離storeとcontrolled adapterを使う。これはcoreの契約試験であり、
実bootstrap/full、WebUIの新規作成導線、runtime引継ぎの実機受入証拠には数えない。
P5-4のWebUI/通常CLI新UX、P5-5〜7の故障・縦断・所有者受入は後続タスクに残る。

## 検証記録

- 実装workerの関連suite：329件PASS、97.433秒。
- 新規3モジュールを標準targetへ組み込み、初回個別26件PASS。レビュー後に認可の回帰5件を追加した。
- 隔離した実WorkbenchHandlerへのHTTP 36件とcanonical Go CLI 6件、計42件が期待結果でPASS。
  新APIの未準備・認可・header・Originの19ケース、既存登録の202/再発見/409、desired/effectiveとgrant CAS・撤回を確認した。
  P5追加4表・query・admissionは0行、provider送信0。専用listener停止と一時認証削除を確認した。
- 最終ソースで上記42件をfresh store/pinsetから再実行してPASSし、75 source/binary pinの一致も確認した。
- 補足の予約component試験：正本coreで作ったP5 rootを独立storeに保持し、実Go CLIのcreate/register-specを各exit1、
  実HTTP createを409 `CREATION_BUSY`、有効なexport specのcanonical register_specを`CREATION_BUSY`として確認した。
  CLIは例外詳細を秘匿する既存仕様のため、CLI自体がerror codeを出したとは扱わない。
  同じnamed adminの認証済みinspectは両sessionでexit0。対象14表の増分0・同一active root・provider0・76 pin一致を確認した。
  server/listener停止・一時認証削除もPASS。fixture作成はcomponent準備であり、public handlerのfoundationは有効化していない。
  初回のevidence writerの排他的書込みエラーを保存し、未実行分だけを継続した2記録からの検証である。
  旧HTTP register-spec routeは存在しないため、その409を主張しない。実apply/reconcile CLIは本補足試験では実行せず、
  共通coreの同一actor/callback省略経路は下記独立回帰試験で確認した。
- 独立レビューで失効時の業務rollback＋STOP/承認撤回永続化を別fixtureでも確認した。
  原子登録、旧経路の必須guard、leaf送信境界、最終CASに追加blockerはなかった。
- レビュー修正後：関連202件PASS（101.139秒）、P5/warehouse 130件PASS（64.022秒）。
- 独立した3ケースの再現と7件の回帰試験PASS（2.693秒）。旧claim/permit拒否、新承認での同一operation再開、
  成功済みpublish/apply_secretsの再送なしを確認した。追加のmaterial issueはない。
- `make verify-core PYTHON=tools/local_experiment/dataops/.venv/bin/python3`：exit0、395.8秒。
  Python 72グループ・1,965件中1,932件PASS／33件skip、失敗0件。
  Go、design-context（93文書・10issue・42参照）、改行・末尾空白・秘密filename検査もPASS。
- 既存環境移行：PASS。2026-10-23 08:38:29 UTC、schema19・70表で再起動した。詳細は次節。

まとめ実行で新規leaf試験5件のfixtureが必要なowned attemptを持たない問題を検出した。
実claim・warehouse・step・cursorを組み立てるようfixtureを修正し、product guardを維持して329件を再実行した。

独立レビューでは、正のcanonical reconcile後に古いAPPROVEがVALID/CLAIMEDのまま残り、
新承認が拒否される一方で古いpermitが再利用できる問題をtemporary fixtureで実証した。
正の照合と同一transactionで古い承認をREVOKED/COMPLETEにし、root revisionを進める修正を行った。
最新root/op revisionでの新しい明示承認が必要となる。未解決UNKNOWNと非P5操作の動作は維持した。
標準検証の初回は、schema15を再現するfixtureが新規19表を残していたため停止した。
実migrationの制約を弱めず、旧schema fixtureを更新して再実行する。

## 既存環境とclean

既存環境は旧residentを完全停止し、read-only接続から0600のprivate backupを取得した。
単一接続でschema18→19へ移行し、再起動前に既存66表の全row・BLOB・schema hashの完全一致と追加4表の空状態を確認した。
同じ`p49-final-persisted`・profile_ref・元認証を使い、両canonical Go ownerを再起動した。
両runtime GETは200・同一generation/profile_ref、通常metadata CLIはexit0。
新Createは`503 FOUNDATION_NOT_READY`、P5追加表への登録0、SQL送信0だった。
protected profile/auth 6ファイルはbyte単位で一致した。

OPEN revision155、Job/Query/admission未閉鎖0、台帳増分0を確認した。
FENCED_READ_ONLYのUNKNOWN 3件と未連結UNKNOWN予約1件を保持し、成功への読み替え・再送を行っていない。
予定enabled0も維持した。

最初の停止前検査はnative配下の正規kubectl port-forwardを想定集合から漏らし、GROUP_NOT_EXCLUSIVEで停止した。
この時点ではsignal・backup・migrationはなかった。所有関係とloopback転送先を再確認し、
group一括killを使わず、Workbenchの既存停止伝播と所有確認済みnative子/ownerの個別停止で実施した。

P4-9のclean環境は停止保持した。08:41:20 UTCのread-only再照合でcontainer停止・4volume・networkの保持を確認した。
起動・再配備・再構築はしていない。既存cleanの保持はP5専用の新規再構築・受入には数えない。

private証拠：`deployment-owned-pids-20261023T083826Z/summary.json`、`clean-retention-after.json`、
`isolated-run-v2/report.json`、`isolated-source-freeze-v2/pins.json`。
補足componentの証拠は`isolated-component-run-v1/split-summary-v1.json`。元の42件の実入口記録とは別に保持する。

account設定自体にはepochがない。検査の間だけ失効・復元する変更の検出には、P5-0で定めた
STOP先行のaccount管理手順が必要である。停止を観測済みならaccount復元だけでは継続しない。

## 証拠の取扱い

private evidenceは`var/shirokuma/p5-1-acceptance/private/`に置き、認証値・秘密path・生の環境変数を公開しない。
先行P5-0前提確認中に既存private proofの証明書/鍵がtool出力へ入った事象は、
[前提確認報告](database-p5-0-preflight-2026-10-23.md)に記録した。値を含めず、新規evidenceをallowlistへ修正した。
