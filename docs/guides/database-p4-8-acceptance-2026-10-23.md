# P4-8 非purge DB retire 実装・実機検証

検証日: 2026-10-23。対象は専用の `p48_nonpurge_20261023`
(`73d38a4f-5ec5-42f0-8ac9-a8554c9240de`)。この記録は技術検証であり、
P4-9 の clean 環境受入や利用者による操作受入を代替しない。

## 実装

管理 Operation は DB revision の CAS で READY → retiring とし、対象 DB の
schedule のみを無効化する。明示 reconcile が実行グラフと閉鎖証拠を確認してから
retired を確定する。UNKNOWN や証拠不足は blocker として残る。

通常の新規受付・結果・artifact 取得には READY を要求する。受理済み親の内部継続は
永続化された ACTIVE admission と同一 actor・DB・generation に限り、現在の
account・role・grant・binding の認可を再確認する。元 pin の取消と管理照合は残す。
退役だけを理由とする結果 poll の拒否は暗黙の取消にしない。

Job / Stage / scan / Mart / WAP / Export / run / query / admission / schedule /
alert と、履歴を含む ResourceRef の依存を列挙する。Table・Catalog・storage・
credential は削除せず、DB ID と SQL 名を保持する。generation 最終確定でも
state と revision を再照合し、retiring / retired を READY に戻さない。

Workbench の DB 管理と通常 CLI の `database retire-preview` / `retire` /
`retire-status` / `retire-reconcile` を利用する。管理には現在の platform admin、
role 上限 admin、選択 admin が必要。変更要求は revision と idempotency key を持つ。

## 候補ビルドと試験

- 候補 binary SHA256: `c864ea4ad7b10485d893be8b08ba6c21bffc8a17c57f70f3f22a7623a97c2379`。
- 98 source の manifest は private `candidate-v1/source-hashes.json`。
- 既存 venv の `make verify-local-experiment PYTHON=.../.venv/bin/python3`: 1,887 tests PASS。
- `make verify-go` (test / vet): PASS。
- 初回の system Python gate は `yaml` 不在で停止。既存 venv で全体を実行し直した。

## 実機証跡の範囲

既存 5 DB の退役前基準は、20 namespace 一覧、選定した 3 sentinel Table の
UUID・Snapshot・各 2 行、および credential の比較用情報。71 HTTP がすべて 200、
6 SELECT が FINISHED。全 Table の内容を網羅した検証ではない。

専用 DB は通常の管理 API で登録し、bootstrap PREPARED と同じ generation の
full APPLIED、通常 resident の再起動を経て READY とした。generation は
`p48-nonpurge-add`。共有制御 OPEN、schema 18。

退役前の Workbench preview は依存 86 件、blocker 0。source 3 行、Stage の
append 後 6 行、Mart 3 行と、実際の Snapshot 固定 CSV Export を照合した。
通常 CLI の native 経路でも source の 3 行を照会した。

## 退役と失敗時の実機結果

退役 Operation は `7a632508-b6ae-4eee-9331-929627638c06`。
Workbench から要求し、通常 CLI で最後の reconcile を実行した。
最終状態は Operation `RETIRED` revision 3、DB `retired` revision 3、依存 101 件。

| 検証 | 実機結果 |
| --- | --- |
| 対象 DB の予定停止 | Job / Stage / Mart の 3 ID のみを停止 |
| 実行中と UNKNOWN | 第1 SQL 終了点の ACTIVE Job と、実 POST 後の通信障害で生じた UNKNOWN を blocker として表示 |
| blocked reconcile | RETIRING revision 2・blocker 7。同一 key の再送は同じ応答 |
| 競合 | 同一 key の異なる本文は `IDEMPOTENCY_CONFLICT`、不一致 revision は `REVISION_CONFLICT` (各409) |
| 受理済み内部継続 | retiring 中に第2 SQL が実 backend ID を持って FINISHED |
| 公開結果と artifact | native page・通常結果・Export receipt/content を403または409で拒否。拒否 poll 後も UNKNOWN を維持 |
| grant 失効 | 現在の worker grant を revoked に変更。親 Job は failed、第1/2段 succeeded、第3段 skipped、第3 SQL の送信0 |
| 元 pin の取消 | 失効後も元 owner の native DELETE が ID と CANCELED のみを返し、対象 query を閉鎖 |
| 中断と再起動 | 障害注入なしの通常再起動後も RETIRING・blocker 0・3予定停止を維持。自動完了せず、明示 CLI reconcile で RETIRED |
| UI 権限 | 閲覧者への切替で DB 管理タブ・退役フォーム・操作情報が消える |

退役後の新規受付は、有効な platform admin の通常入口で個別に確認した。
Job run (8888)、Stage scan・Mart refresh (8888/8080)、3種 schedule の再有効化
(各8888/8080)、通常 query の計12 JSON POST が409 `STALE_BINDING`、native SQL
POST は409 `Stale binding`、同じ SQL 名の再登録は409 `DATABASE_ALREADY_EXISTS`。
受理された対象処理は0。target の8種台帳件数は前後で一致した。

非 platform admin の worker は選択 admin / data_engineer の両方・両portで
管理 GET 8件が403 `FORBIDDEN`、対応する通常 CLI 8件が exit 1。
platform admin の preview / status は両portの通常 CLI 4件で成功した。
退役 DB の通常 warehouse CLI は exit 1、他 DB (SALES) の `SELECT 1` は
実 backend ID を持って FINISHED、結果 `[[1]]`。

自然な resident scheduler の20.005秒観察で、対象 Job run 1、scan 1、Stage run 2、
Mart run 1、DataOps 9、query 26、admission 38、tick 0の全件数が不変だった。
対象3予定は無効のまま。scheduler tick を行う通常 CLI が存在すると仮定せず、
実際の resident 周期と台帳増分を照合した。

有限の検証用 hook は通常 Workbench / native の正確な Python 起動コマンドに
限定し、通常 HTTP と実 SQL を使用した。台帳への直接書込みで ACTIVE / UNKNOWN を
作っていない。通常再起動で hook を外した。

補助ツールは公開一覧・artifact の拒否を403と仮定していたが、実際は409
`STALE_BINDING` だったため途中で停止した。最初の応答を保存し、既存の
第2段終了イベントから root が明示的に継続した。成立済みの SQL や Job は再送せず、
失効・第3 SQL 未送信・元 query 取消・実行グラフ閉鎖を別の証跡で確認した。

## 保持と generation

退役直後の REST / engine 比較は、退役前と Catalog 一覧、選定 Table の UUID・
Snapshot・行データ、credential fingerprint が一致した。専用 DB も source 3 行、
Stage 6 行、Mart 3 行を保持した。

退役前に固定・bind した `p48-nonpurge-before-retire` の full apply は
`STALE_REGISTRY` で dispatch 前に拒否された。6 step はすべて PENDING のまま、
共有制御は OPEN revision 132、DB は retired revision 3 を維持した。

retired を含む `p48-nonpurge-final` の新しい full plan は実適用に成功した。
Operation `f5d1ecb2-fc4a-4bfc-a277-c99b095a9f40` は APPLIED、LOCAL は OPEN
revision 136。実際の mounted OPA 186 decision と ProductionOperator の SQL probe
18件を通過し、DB は retired revision 3 を維持した。

通常再起動、検証タブを閉じてからの認証復元、復元後の再起動を行った。
最終の REST / engine 比較も6 DBすべてについて `physical_equal` /
`catalog_listings_equal` / `credential_fingerprints_equal` が true。
各物理照合で新たに所有した7本の port forward はすべて閉鎖した。

通常8888/8080の generation は両方 `p48-nonpurge-final`。通常 DB 一覧はREADYの5件、
Workbench 管理一覧は retired を含む6件、両portの管理 CLI は RETIRED を返す。
撤去した worker の認証は両portで実際に401。doctor は `healthy`。
native の一般管理一覧 `/api/v1/admin/databases` は既存の非対応経路 (404) であり、
native側の管理確認には実装済み retire preview/status を使用した。

元2 SQLタブの本文・DB/schema ID・role・mode・warehouse はすべて一致した。
管理者・日本語・詳細タブを元の選択に戻し、利用不可の保存 context を別DBへ代替しなかった。
98 source と候補 binary の最終ハッシュも一致した。

閉じた最終 snapshot は schema 18、66 tables、16,698 rows。backup 元に BEGIN を
開かず、元・先の接続を閉じてから独立比較した。開始前の64 tables・16,380 rowsは
すべて残り、元 PK・元 column の欠落は0。変化した既存セルは50件で、5 binding の
適用世代と revision (+2ずつ)、38 effective grant の適用世代、LOCAL の世代と
revision のみ。2回の通常 full apply と bootstrap の実 receipt に一致し、
他 DB の定義・許可実値・ResourceRef incarnation・履歴に変更はない。
独立照合は20/20 PASS、元152,025セルを比較し、指摘0だった。

過去2件の UNKNOWN / `FENCED_READ_ONLY` と、未連結 UNKNOWN 予約1件は元の全columnを保持。
今回の native root query と独立した admission は両方 CANCELED、Job の親・2 DataOps・
2 query の計5 admission もすべて閉鎖した。新たな対象未解決実行は0。

元の認証3 files と既存 client22 files は byte 一致し、元4 account に復帰した。
検証用 worker と主要 owned client2 files は撤去し、死んだ資格情報を含む生の検証記録は
private 証跡として保管する。DB の backend service credential と物理データは保持する。

P4-8 の限定技術受入を完了とする。P4-9、Phase 4 全体の最終受入、所有者による
操作性評価は別の gate として残す。

## 証跡の所在

資格情報を含む生の記録は Git 管理外の `var/shirokuma/p4-8-acceptance/private/`
に保管する。最初の要求・応答を保存し、失敗時に同じ取込や実行を再送しない。
runtime 補助ツールは Git 管理外であり、製品機能の条件分岐には使用しない。
最終独立照合の詳細は private `final-independent-audit.json` / `.md`、
最終起動確認は `final-runtime-attestation.json`、台帳は `final-ledger.sqlite3` に保管した。

専用 DB とその物理データ・履歴・資格情報は最終状態でも保持する。
