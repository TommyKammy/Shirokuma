# P4-2 表示metadata・DB管理・名前境界の実装と実機受入

日付: 2026-10-21。対象: `local/autonomous-experiment`、専用Shirokuma local lab。
状態: P4-2の限定技術受入。通常入口・実backend・実ブラウザーを分けて検証した。
全体`make verify`の既存scanner誤検知は下記に明記する。OSロックによる画面保存の保留は解除後に解消した。

## 実装

通常WorkbenchにDatabase mode専用の「DB管理」を追加した。管理対象の選択を
Explorer/Worksheetから分離し、非READY DBも表示する管理read modelを用意した。
通常ExplorerのREADY条件・DB認可条件は変更していない。

* `GET /api/v1/admin/databases`: 全registry DB、固定Schema、安全なbinding列。
* `PATCH /api/v1/databases/{id}/display`: `display_name / description / expected_revision`。
* `PATCH /api/v1/schemas/{id}/display`: `display_name / expected_revision`。
* 通常CLI: `database display list / set / schema / operation`。普通のclient profileで
  認証HTTPを使用し、private operator設定は要求しない。
* 全管理read/write/replay/Operation照会は現在のplatform admin・上限admin・選択adminを要求する。
* 表示CAS、APPLIED Operation、要求の冪等性、before/after監査を同一transactionで確定する。
  schema v11の小さなmetadata request/audit台帳を使用する。
* 同一actor/route/keyとpayloadは元の応答、異payload・古いrevisionは409。
  未知IDは404、型・余分field・不正Unicodeは400。自動再送・自動再planはしない。

表示名は1〜128 Unicode code points、空白だけ・Cc制御文字・不正surrogateを拒否する。
説明は0〜2048 code points、改行・タブ以外のCcを拒否する。正規化・trim・lowercaseはしない。
同じ表示名の別DBはSQL名とIDで区別する。HTML文字列は文字として描画する。
Schema purpose、SQL名、ID、Table incarnation/Polaris UUID、binding、接続先、READYを
表示更新から変更しない。metadata revisionの更新は既存の未適用planをstaleにする。

P4-1 grantの希望・有効role、希望/適用revision、有効epoch、backend検証待ちと取消を表示する。
DB登録、固定4Schema、bootstrap/full plan、Operation照会、apply要求も同じ管理画面に接続した。
公開applyは`requires_operator=true`のREQUESTED handoffであり、private operatorによる
実行・検証を別途必要とする。秘密値・private設定ファイルを画面に入力しない。

## API・CLIと実機の確認

開始状態を明示runtime profileとURI `mode=ro`で確認し、整合backupを保存した。
元のSALES/FINANCE/任意名DBは3件ともREADY、LOCAL OPEN、UNKNOWN/実行中0だった。

元3DBに同名`p42_identity` Tableを作り、表示更新前後でSQL名、DB/Schema ID、purpose、
Ref/incarnation、Polaris UUID、binding全列、接続先、READY、実行結果を照合した。
CAS競合は片方だけ成功し、同key replay・異payload・未知ID・権限不足・余分field・
Unicode/HTML・表示名/説明の上下限を通常HTTP/CLIで確認した。表示PATCHによるengine queryは0。

表示変更前の`p42-before-display` planは`STALE_REGISTRY`で送信前拒否された。
Operation、server記録は不変、private dispatchは空だった。新しいspecをexportして明示再planする。
この未送信の歴史planは監査用に`AWAITING_APPLY`のまま保持し、実行中やUNKNOWNと区別する。

名前入口の受入matrixは次のとおり。31文字制限をすべての識別子に横展開しない。

| 境界 | 通常入口と実機の確認 |
|---|---|
| DB SQL名 | 1文字`a`をWorkbench/HTTP、31文字`p42_boundary_abcdefghijklmnopqr`を通常CLIで登録。specへ両方を含めbootstrap/full applyと実SQLで確認する |
| DB名の拒否 | HTTP/CLI/specの32文字・予約4名・DB限定`iceberg`・空・大文字・不正文法・重複を拒否。新registry identity/外部dispatchなし |
| Schema | raw/staging/marts/sandboxのみ。文法上有効な1/31文字の任意Schemaもspecで拒否。purpose変更はAPIで拒否 |
| 表示名 | Unicode/絵文字/結合文字/HTML、1/128、説明0/2048、空白・型・制御文字・過長、stale CAS、同名別DB |
| DataOps Table locator | 1/31文字の実Ingest/実行結果。不正名はchild query 0。同名Tableが元3DBで別Refとして存在 |
| ResourceRef | 他DB、未知の長いID、空/不正型、0/bool/誤incarnationを拒否。SQL名validatorへ統合しない |
| native SQL | 引用した31文字超のTableを通常SQLで作成・照会・resolve。mixed case/空白/ドットの追加probeは後述。既存のSQL grammar/同DB guardを維持 |
| model/source alias | Unicodeの同名modelを3DBへ保存、256文字model・32文字alias、空/型/過長/別DB Ref/不正alias/JSON重複aliasを確認。保存によるchild query 0 |
| View | 既存通常SQL guardはView作成を拒否するため、fixture diagnosticでViewを用意。通常resolve後のExport拒否とchild query 0を確認 |
| Stage/Job/Mart | P4-3以降の新入口でmatrixを追加する。P4-2で任意Schema CRUD等を先行実装しない |

DB正例は同じ2 identityをAPI/CLI間で同key replayし、specに含めた実applyと対応づける。
各入口で新しいDBを複製する試験ではない。1/31文字DBはP4-8のretire実装までは空READY identityと
監査履歴を保持する。元3DBへ数を戻すためのregistry/Polaris/storage直接削除は行わない。

## 実ブラウザー

通常ChromeのWorkbenchで2画面CASを実行し、片方の成功後、競合側の入力・focus・期待revisionを保持した。
別画面のmetadata更新を15秒refreshで受けても管理nav・DOM・draftを維持することを確認した。
表示名が同じDBはSQL名/IDで分かれ、HTMLは実行されない。admin選択を下げると管理navが消え、
管理フォームcacheは0になる。SQLタブの本文とDB/Schema/role/mode/warehouse選択を別管理選択から分離した。

実ブラウザーで発見した修正:

* 結果不明の要求がある間の管理DB切替を拒否し、凍結した同一payload/keyで明示回復する。
* metadataによるDB receipt更新をplatform管理権限失効と混同しない。account/platform/最大role/
  選択role失効と管理API401/403では引き続き管理cacheと遅延応答を無効化する。

grant保存直後はdesired data_engineer / effective revokedとbackend検証待ちを確認し、通常SQLも拒否された。
Workbenchでbootstrap/fullの公開plan・inspect・apply handoffを実行し、operator待ちの表示を確認した。

## 最終検証・清掃

`p42-live-five`のbootstrapはPREPARED、fullはAPPLIEDになった。
Workbenchからのgrantはdesired revision6 / applied6 / epoch8のdata_engineerへ昇格したことを
画面・通常SQL・台帳で確認した。剥奪はWorkbenchからrevision7で行い、effective revoked /
epoch9へ即時制限された。既存query、native nextUri、history、Export receipt/content、DataOps
Operation取得と新規SQLは拒否され、別利用者/他DBのSQLは成功した。

直接backendの15 probeは成功7/拒否8。元3DB×2 roleのown読取6件、engineer RAW読取が成功し、
cross DB6件、viewer RAW/書込はすべてPERMISSION_DENIEDだった。製品入口の証拠とは別保存である。

最後に通常Go CLIで`p42-live-final`をplan/inspectし、同じOperation
`7c9d4f4f-3025-464c-a38f-41b7fa8943ac`をfull applyした。APPLIED revision17、5DB READY、
LOCAL OPEN/ownerなし、全8grantはdesired/effective/applied revisionが一致した。
finance-onlyのSALESはrevokedのまま。両通常入口の世代と最終5DBの実SQL `[[42]]`も一致した。

実機harnessは累計377 checksが成功。763 HTTP記録・30 CLI記録には正負ケース、再照会、
途中のharness補正に伴う診断も含む。fixture診断による実体作成/清掃と直接backend probeは
通常入口の成功件数と区別している。
所有9 Table/ViewはRef/UUID確認後に清掃し、正しくquoteしたexact resolveの404を9件記録した。
20 Schemaの実REST一覧でも`p42`対象は空。Exportの実ファイルと一時client認証profile2ファイルを
削除し、監査台帳・model定義・歴史marker3件を保持した。独立したURI `mode=ro`＋両入口GETの
最終factsは**211/211成功**、doctorはhealthyだった。

試験・静的検証:

* backend重点105件、Go CLI、管理UI重点144件（新規20＋既存124）、Workbench全体254件は成功。
* 最終resident-coreは**1,294実行 = 1,262成功 + 32skip**。
  `make verify`内のPython実行合計は1,452（成功1,420 + 32skip）。重複実行を含む数である。
* Goの`./cmd/... ./internal/...`、設計検査14件、canonical Terraform format、対象差分検査は成功。
* 後段のpolicyを別途実行し、Python15件・Kyverno21件・適用7ruleが成功。
* 全体`make verify`は`verify-security`で停止した。開始時からdirtyの
  `opentofu/dev/variables.tf:125`にあるPEM形式検証用文字列をscannerが検出する。
  private-key実体の検出ではなく、P4-2は同ファイルを変更していない。誤検知を隠す除外は加えていない。

v9を再現するfixtureはv11台帳も除いてから旧versionを作るよう更新した。旧管理照会・nav・日英辞書の
試験期待値も新契約へ更新した。ignoredの歴史Tofu fixtureは変更せず、format対象を全tracked
Terraformが存在する`opentofu`へ限定した。

追加native probeでは、SQL `"p42 Native.Table"`の作成/読取は成功した一方、Polaris一覧の実名は
`p42+native.table`だった。元表記によるexact resolveは404、一覧で観測した実名/Refでは解決できた。
ローカル層はSQLをそのまま送信し、一覧の実名を保持しており、変換する下流層は未確定である。
fallbackや名前の自動変換は加えず、実名・UUIDを確認して当該fixtureも清掃した。
これはDB登録/表示metadataの契約と分け、P4-9のnative名互換性再照合へ残す。

最終世代でも表示更新をまたぐSQL本文、caret、focus、DB/Schema/role/mode/warehouse選択は保持された。
日英切替とブラウザーconsole例外0も確認した。受入用SQLタブ終了時のOSロックを手動解除後、
再読込して元のワークシート1/2だけが残り、受入用ワークシート3と確認ダイアログがないことを確認した。
最終管理画面でも`p42-live-final`、5DB READY、finance-only SALESのdesired/effective revoked・
希望/適用revision7・epoch9を確認し、`browser/management-final.png`と完了checksを保存した。
ブラウザーの保留作業は解消済み。所有者の最終操作性評価はPhase 5へ残る。

証拠はignoredの`var/shirokuma/p4-2-acceptance/`。private materialは既存の専用lab領域に置き、
公開reportへ認証値・candidate credentials・秘密ファイル内容を転記しない。

主要証拠: `live/state.json`、`final-facts.json`、`final-doctor.json`、
`browser/checks.json`、`browser/grant-effective.png`、`browser/management-final.png`、
`broad-verify-final-v4.log`、`broad-policy.log`。
実applyは既存lab領域の`live/p42-live-five`と`live/p42-live-final`に記録される。
