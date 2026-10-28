# P4-9 通常入口・永続化・clean 再構築の技術受入

検証日: 2026-10-23。P4-9 の実装・技術受入を完了した。
対象は `local/autonomous-experiment` の専用 Shirokuma 環境。
既存環境と、新規 node・PVC・認証材料から構築する clean 環境を別々に検証する。
利用者による Phase 5 の最終評価はこの技術受入に含めない。

## 実装

既存の operator recovery に、終端済みの子 query と UNKNOWN の親を照合する
`TERMINAL_PARENT` を追加した。実 backend ID・終了時刻・元の SQL・9 項目の
コンテキスト・owner・親子 admission を固定し、全子が実際に終端済みの場合だけ
親を失敗として閉じ、未実行 step を skipped とする。終端結果を成功へ変換せず、
SQL の再送、backend の停止、結果の昇格を行わない。

既存の `FENCE` は単一管理 Docker backend の read-only Job SQL に限定する。
実 backend の停止とその正の証拠、replacement の健全性を確認して実行を閉鎖する。
query の UNKNOWN と `FENCED_READ_ONLY` という実行閉鎖を別に保持する。
型付き DB 所有権を共通 graph guard で照合する。legacy NULL の例外にも、
canonical link と全コンテキストの一致を要求する。
手順は [recovery runbook](database-read-only-job-execution-recovery-2026-10-21.md)。

最終ブラウザーで実観測した、Database-mode の接続失敗表示が復帰後も残る問題も修正した。
既存 identity 判定の直後に `status-error` の消去 1 文だけを追加した。
現在の失敗は引き続き表示し、古い identity の成功 / 不健全 / 失敗応答は現在の表示を変えない。
ADR-0036 の bounded Medium CLI worker に 2-file task として渡し、関連 UI 79 tests が PASS。
追加した 2 tests は修正前に失敗し、修正後に成功することも記録した。

## 候補とコード検証

- recovery source: `073920b9f90e47d6a7192128efbad36672d14b0b2ab706530c7efec2aa387d8b`。
- 通常 CLI binary: `c864ea4ad7b10485d893be8b08ba6c21bffc8a17c57f70f3f22a7623a97c2379`。
- 独立 recovery focused: 73 tests PASS。
- 既存 venv、通常 umask 022 の最終 `make verify-core`: Python 1,892 PASS / 33 skip、Go・設計検査 PASS。
- UI 修正後の全 69 group / 1,925 tests を再実行し exit 0。`verify-core-after-ui.log` と receipt を保存した。
- 初回は検証プロセスが private evidence 用 umask 077 を継承し、既存の 0755 mkdir fixture が失敗した。
  通常 umask の同一試験と全体試験を通した。product・fixture の条件は緩めていない。

private 証跡の基点は `var/shirokuma/p4-9-acceptance/private/`。
送信前 intent・最初の応答・失敗・実 backend ID を保持し、credential は本文へ転記しない。
既存 623 source、4 account、27 client-auth file、6 DB、旧 UNKNOWN 2 件と
未関連 UNKNOWN reservation の基準を `baseline/` に保存した。

## 既存環境の縦断

新規専用 owner の通常認証 HTTP と実 Go CLI を使い、Sales / Finance / Insights に
同名 `p49v_existing_v1` の Table / Stage / model / Mart / Job / schedule を作成した。
19 段階 × 3 DB の 57 段階が全成功した。
Stage append は新規 bytes を取り込み、同一内容を再取込せず、raw と Mart の型付き
3 行を照合した。Mart は公開 Snapshot を更新し、変更なし更新では Snapshot と
公開時刻を維持した。Job は source 読取・Mart 読取・厳密 Boolean alert の 3 段階。
CSV / JSONL は全行・Snapshot・物理 UUID・bytes・SHA256 を照合した。
証跡は `vertical-existing-v1/current-state-matrix.json` と各段階の最初の応答。

Workbench の専用ログインでも SQL の値 49、Stage scan の成功・skipped、
Mart check の `changed=false`、3 段階 Job の全成功を確認した。
新規 UI Job は `39e1ef44-64f4-4966-83a0-064eb32fb55c`。
3 行の Export receipt `e941730c-9c79-4a8d-8903-3a842e2a50a9` を表示し、
権限を再確認する download ボタンから取得した。画面証跡は `browser/`。
非 platform admin の owner には DB 管理ナビゲーションを表示しない。
実 download の JSONL 113 bytes / 3 行は受領 SHA256 と一致した。private にコピーを
保持し、今回だけの Downloads file を除去した。閲覧者へ変更すると Stage 保存・scan・
Export download が無効になり、既存のアクセス情報が消えた。元の admin 選択へ戻した。

通常再起動で 23 組の定義・履歴・grant・旧 UNKNOWN・reservation・P48 retired 状態を
正照合し、protected file と profile の bytes も一致した。再起動後の通常 CLI
`SELECT 49` は実 backend `20261023_041103_00169_s3ecf` で FINISHED。
`recovery-live-v1/normal-restart-comparison.json` を参照。
通常 full 再配備 `p49-recovered-persisted` は Operation
`34727fc9-f53d-45e4-af85-d1359a621d52` APPLIED revision 17、共有制御 OPEN revision 151。
既発行 7 DB consumer を保持し、新規 bootstrap で資格情報を作り直していない。
両通常入口の世代・profile ref が一致し、Sales / Finance の pending grant は
この verified full 後だけ effective admin / epoch 5 となった。他 2 owner grant は epoch 2 のまま。
新 context の通常 CLI は 3DB とも `SELECT 49`、元 revision 1 Job の新規 3 段、
raw / Mart の型付き 4 行を返した。受理された SQL は計 18 件。
旧 9 項目 context の Job / run / export は実 409 `CONTEXT_MISMATCH`、
旧 query metadata / result は実 403 `DB_ACCESS_REVOKED`。
現在の history は 200 で旧項目を除外した。Workbench の再読込でも新しい Job 履歴の
4 / 4 / TRUE と実行可能状態を確認し、旧実行詳細は消去されたままだった。
Insights の初回 CLI に論理ラベルを渡した拒否は別に保持した。query / admission の
作成 0 を確認後、context の正式な catalog `insights_e88387a5` で未実行分だけ受け入れた。
検証用 owner の 4 grant は通常管理 API で先に失効し、effective は revoked /
epoch 6 / 6 / 3 / 3。新規の空 DB `f0621762-32cc-422b-8279-ca6e2381e39d` は
通常 CLI の retire / 明示 reconcile で retired revision 3、Operation
`461e8d2e-d053-4044-abaa-de7deaaf24e1` は RETIRED revision 2 となった。
blocker / stopped schedule は 0。他 39 desired / 39 effective row と認証素材 3 file は不変、
query / admission / DataOps / 各 run / schedule / tick 増分は 0。
この空 DB の清掃を物理保持の証拠に数えず、clean の新規 retire 6 Table と区別する。
`fixture-cleanup-v1/completion.json` を参照。
最終 generation `p49-final-persisted` の Operation
`05161787-8269-4a22-8cdf-fa41b98120a9` は APPLIED revision 17、LOCAL OPEN revision 155。
両通常入口は HTTP 200、同じ generation / profile ref を返した。
`final-generation/applied.json` を参照。
元の 4 account へ 2 file を byte 単位で復元し、同じ generation で障害注入なしの
通常再起動を行った。旧 fixture client の session は実 401。
復帰後の通常 admin CLI の 6 query は 3DB の raw / Mart 各 4 行を返し、
別の 6 inventory / REST 照合では、再配備前の Ref / 物理 UUID / Snapshot と一致した。
`final-admin-six-v1/summary-v2.json` の 27 照合が PASS。初回の観測側 revision 誤期待は
元の STOP を保持して Operation 2 / DB 3 の実 anchor へ訂正し、実処理を再送していない。
全 admission は閉鎖済み、元 6DB の state / authority と、旧 2 件 + 今回 1 件の
UNKNOWN / FENCED 履歴、Finance CANCELED、旧 reservation、9 off 予定を保持した。
通常 `doctor` は復帰後も exit 0 / healthy。SQL はこの別の実値証拠で確認した。
配備直後の補助 runtime GET は観測側が `profile_ref` の key を誤記し、初回 raw 保存前に停止した。
失敗記録と後続の raw 再観測を分けて保存した。後続応答を初回応答とは扱わず、
canonical verify と認証復元後の最終観測を判定に使う。
`post-regrant-v1/summary.json` は受理 SQL 18 / admission 30、21 照合 PASS、
24 GET の拒否・history filter、unclosed 0 を記録した。
Job の内部 query mode `dataops` と利用者の read-only 定義を source の対応で照合し、
native query 9 件と Job 子 query 9 件の表現差を同一文字列として扱わない。

最初の同時刻 Stage 3 件では Sales だけを受理し、Finance / Insights は
`skipped/BUSY`・run ID NULL となった。共通 guard は DB をまたぐ ACTIVE / UNKNOWN の
Job / scan を排他にする。後続 6 予定は無効のまま停止した。
受理済み Sales は再実行せず、未受理 2 件だけを operator が別の将来時刻へ明示設定した。
自動的な追いかけ再実行や file bytes の追加変更はしていない。
時刻を分散した明示予定は Stage → Mart → Job の 9 受理済みグラフすべてが成功した。
39 query / 69 admission と、別の実値確認用 6 query が終了し、9 予定はすべて
disabled / next NULL。3DB の raw・Mart は各 4 行、Mart の audit は count 4 / null 0。
alert は同じ ID のまま Sales 2→3、Finance / Insights 1→2 となった。
自然実行の証跡を固定した後、Sales の通知を Workbench で ACK し未確認 0 とした。
`natural-existing-v1/actions-v2/final-state-matrix.json` と `browser/natural-alert-ack.json` を参照。

## UNKNOWN と grant 失効の回復

Sales の実 Job run `63aeecaf-103b-482d-bfdf-9965f7d2fcec` では、元の backend GET が
実際に返った後、その一度の delivery を失わせた。production が query
`52ec233c-36d1-49b1-abcd-91357ff7f2d3` を UNKNOWN とし、9 項目の context・3 admission・
未送信の第2 SQL を照合した。grant は通常管理入口から revoke / pending regrant とし、
effective REVOKED / epoch 4 のまま。旧 context の Job は実 HTTP 403。
Workbench でも Job のアクセス情報が消え、実行ボタンが無効になった。

operator recovery `cb5ce398-c1ad-4d5b-824a-31eca93c439a` は、実 Docker stop の後、
proof 保存前に中断した。永続 STOP_INTENT / DRAINING を通常 reconcile で再開し、
実 stopped-inspect の証拠を PROVED とした。別途測定した実 QueryInfo GET は 410。
通常 Go CLI reconcile-query は UNKNOWN を維持した。この 410 を閉鎖証拠にしていない。
明示 apply は replacement の健全性を確認し、親 FAILED・未実行 step skipped・OPEN とした。
元 query outcome は UNKNOWN のまま、実行閉鎖だけを FENCED_READ_ONLY として残した。
取消・query 再送・架空 QueryInfo は使っていない。証跡は `recovery-live-v1/`。

Finance run `f2035413-9bcf-4efd-8082-54063c6769b8` にも実 delivery loss と
revoke / pending regrant を重ねた。元の Workbench pin の実 DELETE ACK により query
`b152ecb5-b59b-4759-b3f4-f98def12751c` は CANCELED、親は UNKNOWN のまま。
その状態が通常再起動を越えて残った。operator Operation
`c18ff9df-d224-47fe-835c-819788fc50bf` は TERMINAL_PARENT を導出し、子の CANCELED を
変えず、親 FAILED・第2 step skipped・OPEN とした。同一 backend container / StartedAt を
照合し、この分岐で stop / remove / SQL 再送を行っていない。
通常 owner の status-only reconcile は許可され CANCELED を返す一方、public query inspect は
実 403 だった。状態照合と結果取得の認可を混同しない。

最後の障害注入なし再起動後も、両 recovery Operation の同一 key plan / inspect が同じ応答を返し、
reconcile は終端状態を変更しなかった。全 unclosed admission 0、第2 SQL 0。
original 4 account・8 desired / 8 effective grant・旧 UNKNOWN 2 件 / reservation / P48 retired を
正照合した。`recovery-live-v1/final-recovery-preservation-runtime.json` を参照。

補助入力の初回 Job create revision が None、初回 seam の profile が launcher の実パスと
異なることを、SQL 送信前に検出した。元の失敗証跡を保持し、canonical な revision 0 と
実 per-generation profile へ入力だけを直した。未実行の同じ Job 定義を使用し、
product guard・frozen helper は緩めていない。

## clean 環境

既存 Colima cluster・旧 P2-6 clean container を再利用せず、専用 container / network /
volume を作成した。node UID `947ce7ab-d081-4059-bf11-e2188b14e86b`、API / CA / client
credential は既存環境と異なる。開始時 PVC 0 と Shirokuma namespace 不在を確認した。
固定 digest の k3s と 10 image を使用し、Flux 4 controller の Ready を確認した。
SeaweedFS は repository 所有の固定 derivative image であり、upstream image と偽らない。

新規 private account 3 件と、通常 Go CLI による新規 DB 3 件を登録した。
初期 desired grant は canonical authenticated 管理処理で準備し、effective は revoked のまま。
この準備は通常 HTTP の証拠と区別する。verified full apply 前に有効化しない。
bootstrap Operation `a4274a05-6bdc-494f-87e4-417c9c755d41` は PREPARED revision 15。
その実発行 consumer を使った full Operation `f7789524-6aad-46f8-9540-5d6678e32f32` は
APPLIED revision 17。generation は `p49-clean-ready`、full の 6 step が成功した。
両入口 18888 / 18080 の世代・profile ref・新規 3 catalog ID を実応答で照合し、
新規 service UID / endpoint と専用 kubeconfig を持つ 5 forward も固定した。
Workbench の専用 owner に新規 3DB だけが見え、実 SQL が 49 を返した。
clean の 19 段階 × 3 DB、57 段階も全成功した。初回取込・Stage scan / dedup・
model / Mart の公開と変更検知・3 段階 Job・9 disabled 予定・CSV / JSONL 全行を含む。
`clean/vertical-graph-matrix-v1.json` は全 immutable accepted receipt の SHA を持つ。
Workbench でも scan の skipped、Mart の changed=false、新規 Job
`04dc29b2-7615-4274-b957-870b67e07145` の全 3 段成功を確認した。
Sales 限定 data_engineer の通常 CLI は Sales の 49 に成功し、Finance は exit 1・
query 記録増分 0。CLI から HTTP status を推測していない。
clean Sales の通常 CLI retire Operation `4cdc89d8-98c0-4817-bb85-27838da2bc2b` は
RETIRED revision 2、DB は retired revision 3。新規 retire を旧環境からの復元で代用していない。
退役前後の 6 Table は実 TLS SQL / REST で UUID・Snapshot・型・各 3 行が一致した。
Workbench Job result と Export content、および native の対応する Export は実 409 STALE_BINDING。
native Job 管理 URL の 404 は未提供 route と区別し、退役拒否の証拠に数えていない。
対象 Sales の既に無効だった 3 予定は revision 1→2 / paused / DATABASE_RETIRING へ遷移し、
disabled / next NULL / spec / target を保持した。他 6 予定と他 2 DB は不変。
通常ペアの再起動後も同じ retire Operation / DB revision が残り、他 2 DB は READY revision 1。
6 Table の UUID・Snapshot・型・行は、retire 前後と再起動後の 3 回すべて一致した。
この物理確認の実 TLS query 18 件は、通常入口の縦断とは別の証拠として全件 FINISHED。
再起動前の native admission 170 件は不変で、追加した Finance / Insights の通常 CLI
2 query は各 3 行を返し、172 件すべて FINISHED。ACTIVE / UNKNOWN は 0。
`clean/retire-and-restart-summary-v1.json` に各 proof と照合結果を固定した。
Workbench の再読込でも元の Sales ID が「利用不可」のまま残り、他 DB へ切り替わらず、
Job の実行・照会・予定を無効化して古い詳細を消去した。
専用 kubeconfig と通常 binary の `doctor` は exit 0 / healthy。SQL check は
informational と明示されており、上記の実 SQL と混同しない。
検証後は現在の所有記録でペア・転送を閉じ、専用 container だけを停止した。
全 9 port が閉じ、4 volume と専用 network は再確認用に保持した。
旧 P2-6 container の full inspect は Mounts の順序だけが異なり、全 mount 辞書集合・
停止状態・identity / Config / HostConfig / NetworkSettings が一致した。
最初の比較失敗も保存した。既存 node UID は不変・Ready、両通常入口は HTTP 200。
`clean/final-cleanup-v1/summary.json` を参照。

構築前に、Docker inspection の Mounts の順序だけが変動することを 5 回の実観測で
再現した。内容全体と件数を保持して比較時だけ順序を正規化し、55 focused tests を通した。
既存 proof は上書きしていない。private ToFu copy に必要な public ConfigMap 1 file を
同じ相対参照位置へ固定 SHA でコピーした。失敗した最初の plan は保持した。
namespace / Secret の saved plan・apply と Seaweed Ready の後、S3 bucket 作成で
`BucketAlreadyOwnedByYou` を受けた。起動時の `mini -bucket` と署名付き HEAD / LIST により、
lakehouse が既に存在し空であることを正照合した。受理済み prefix を再実行せず、
不在だった GitOps bucket だけを作成し、全 public bytes と suspended children を照合して
残る準備を完了した。失敗した最初の応答を保持し、架空の create ACK は作らない。

## 境界と残件

修正版を通常再読込し、実ペア停止中の status 取得で `Failed to fetch` を表示した。
同じ generation の通常再起動後、画面の更新操作で準備完了となり、error は空 / hidden。
`browser/status-recovery-real-offline.json` / `status-recovery-real-success.json` が実観測。
元の 2 worksheet ID と SQL / DB / schema / role / Warehouse / mode が基準と一致し、
日本語 / admin / 詳細 / データ探索も元へ戻した。`browser/final-original-preservation.json` を参照。
不在の元 DB や古い context は利用不可のまま保持し、別 DB へ fallback していない。
この追加通常再起動後の CLI 1 query も Sales raw の型付き 4 行を返した。
backend `20261023_052911_00027_ahnia` と親 admission が FINISHED。
`final-ui-restart-one-query/summary.json` の 14 照合で、6 process の canonical ancestry / profile、
障害注入環境なし、両入口の同じ世代・profile ref、OPEN / unclosed 0 を確認した。

R03 の Stage / Mart は各 1・48・49 文字を通常 API / CLI で確認した。
各 kind の 1 文字は API create → CLI 同一 key、48 文字は CLI create → API 同一 key。
4 定義は revision 1 の同じ ID を返し、再送の作成増分は 0。
Stage 49 文字は API 400 `STAGE_INPUT_REJECTED` / `INVALID_INPUT` / `NOT_ATTEMPTED`、
CLI は client 生成前の local 拒否。Mart 49 文字は API 400 `INVALID_INPUT`、
CLI は通常 server validation 経路の exit 1。CLI の wire status は推測していない。
全 12 入口の query / admission / DataOps / run / schedule / tick 増分は 0。
既存定義は不変で、Stage の新規 2 directory は空・0700。
記録は `r03-name-boundaries-v1/completion.json`。観測側の name 入れ子位置と
Stage 固有 error code の誤期待で停止した最初の応答も保持した。
予定した同一 key replay 以外に、受理済み create を別 key の新規作成としてやり直していない。

通常 CLI の実 Trino で、小文字名と引用した dot を含む名前の exact Ref 解決を確認した。
引用した mixed case / space の名前は native create / SELECT に成功しても、REST 一覧で
lowercase / `+` へ変化し、元の exact locator は 404 になった。
これは downstream の exact-name round trip の未対応 capability として残す。
local な候補検索・名前の補正・別物への fallback は追加していない。
4 fixture は測定した物理 identity に限定した実 Trino 清掃を終えた。

write 不確定、分散 / 外部 backend、他 DataOps の不確定処理、空 state への旧 retire
tombstone 復元 DR は対象外。clean の containerd backend に Docker FENCE 契約を広げない。
不在・TTL・PID・架空 QueryInfo を成功の証拠にしない。

## 受入 matrix

既存の focused 受入を基準に、今回の通常縦断・永続化・実回復・clean 再構築を加える。
過去の故障試験を今回再実行したとは扱わない。HTTP status、CLI exit、物理照合、
Workbench の表示は別の証拠として記録する。

| 条件 | focused 証拠 | 今回の追加証拠 / 判定 |
| - | - | - |
| P2-R01 / P4-A01〜03 | [P4-1](database-p4-1-grant-boundary-report-2026-10-21.md) / [P4-2](database-p4-2-metadata-management-report-2026-10-21.md) の CAS・pending・migration・epoch | 実 UNKNOWN と revoke / pending を重ね、verified full 後だけ再付与。旧 context の取得拒否・新規 18 SQL と Workbench の復帰 PASS |
| P2-R02 / P4-A04 | P4-2 の表示名 CAS と immutable identity / purpose / plan | 再起動・再配備で既存 ID / binding / authority を保持。clean は新規 ID を別記録 |
| P2-R03 / P4-A05 | P4-2 の DB 1 / 31 / 不正・重複、schema / display / Ref / alias、[P4-3](database-p4-3-jobs-manual-adapter-report-2026-10-21.md) の Job 1 / 64 / 65 | Stage / Mart 1 / 48 / 49 の 12 入口 PASS。native exact-name の mixed case / space 制限を明記し、補正しない |
| P4-A06 | P4-3〜8 の DB / owner / role / 非 READY 分離 | 既存・clean の各 3DB 同名縦断。viewer UI 消去、Sales 限定 CLI の Finance 拒否、retired Sales を他 DB へ fallback しない |
| P4-A07 | P4-3 の定義版・親 admission・多 step・競合 | 通常 CLI と Workbench の新規 3 step Job、自然予定と回復の親子 graph を照合 |
| P4-A08〜09 | [P4-4](database-p4-4-local-stage-report-2026-10-21.md) / [P4-5](database-p4-5-manual-stage-scan-dedup-report-2026-10-22.md) の固定 bytes・commit・部分 retry / dedup | 各 57 段階の append / 同一 bytes skipped、自然予定後の実 4 行。UNKNOWN SQL の再送は 0 |
| P4-A10 | P4-4 の Snapshot 固定・部分非公開・故障・失効 | CSV / JSONL の全型付き行・物理 UUID / Snapshot / bytes 照合。Workbench の実 download 113 bytes / 3 行 |
| P4-A11 | [P4-6](database-p4-6-managed-marts-report-2026-10-22.md) の WAP / 変更確認 / precommit・postcommit 故障 | 変更あり公開・変更なし維持、自然予定後の新 Snapshot / count 4 / null 0、clean の物理値保持 |
| P4-A12〜13 | [P4-7](database-p4-7-schedules-alerts-report-2026-10-23.md) の重複・MISSED・DST・Boolean / fault / isolation | 実 BUSY は未受理のまま保持し、明示した将来予定だけ 9 graph 成功。alert 増分と Workbench ACK、全予定 off |
| P4-A14 | [P4-8](database-p4-8-acceptance-2026-10-23.md) の UNKNOWN blocker・対象依存停止・full plan | clean の新規 retire と再起動、6 Table の 3 回の物理一致。対象 3 予定だけ canonical paused、他 6 予定不変 |
| P4-A15 | P4-1〜8 の intent / 同一 key / 故障照合 | 実 delivery loss、実 stop 後の中断と PROVED 再開、実終端子を保つ TERMINAL_PARENT。第2 SQL 0 |
| P4-A16 | 既存 UI focused の focus / locale / tab / 遅延応答 | Workbench の viewer / revoke / retired 時の消去・無効化。実接続失敗からの表示復帰と、再読込 / 再起動後の元 2 tab / locale / role / 表示選択の一致 PASS |
| P4-A17 | P4-1〜8 の永続台帳 | 既存通常 restart / verified full、専用 clean の bootstrap / full・57 段階・新規 retire / restart。認証撤去後の 6 実読取・6 物理 identity / Snapshot と 27 照合 PASS |
| P4-A18 | 今回の focused / 最終 Python 1,892 PASS・33 skip / Go / 設計 PASS | 最終 doctor healthy、別の実 SQL、所有物清掃・残存基準 PASS。Phase 5 の所有者評価を別 gate とする |

## 最終状態と清掃

最終 generation は `p49-final-persisted`、LOCAL OPEN revision 155、実行未解決 0。
今回の 9 予定は disabled / next NULL。UNKNOWN 結果 3 件の FENCED 閉鎖、
Finance の CANCELED と失敗した親、未連結 UNKNOWN reservation 1 件を別に保持した。
旧 P4-8 DB と今回の空 DB は retired revision 3 のままである。
最終の通常 doctor も healthy / exit 0、既存 node UID は基準と同じで Ready。

検証用 account は live 認証から除去し、元 4 account と 2 file の bytes を復元した。
4 grant は revoked tombstone / final-applied として残した。元の authority と、
認証 header・credential seed・CLI binary の protected 5 file、既存 client 27 file は基準と一致。
global runtime profile の変更は最終 canonical profile との byte 一致で確認した。
元 623 source の許可外差分は 0。差分は bounded worker の 5 file と本計画の更新だけで、
追加の非 ignored file は今回の brief / 報告 2 件だけだった。`final-files-audit.json` を参照。

Workbench / CLI の正常ペアは既存環境で稼働し、元の browser tab は開いたままにした。
今回の owner / clean 検証 tab は閉じた。native-name の 4 fixture と一時 download は清掃済み。
Stage / Mart / Job の定義・file・公開 data・履歴・受領記録は監査と再確認用に残す。
削除や purge で受入証拠を失わせず、予定の無効化と権限失効で新規の自動実行を止めた。
clean の所有ペア・転送・専用 container は停止し、9 port が閉鎖。専用 4 volume / network 1 は保持した。
旧 P2-6 container と既存 cluster を破壊せず、global kubeconfig や既存 client を変更していない。

P2-R01〜03 / P4-A01〜18 の技術 closeout を完了する。
上記 native exact-name の未対応 capability と、Phase 5 の所有者による最終操作性評価は残る。
