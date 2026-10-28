# Phase 2 P2-3: 探索・SQL入口の実装と検証

対象は `local/autonomous-experiment` の通常 Workbench、SQL endpoint、warehouse query CLI と探索APIです。既存テストデータの移行・保全は行いません。P2-4以降の構成生成・管理操作は実装対象に含めません。

**判定: P2-3の限定したローカル技術受入を完了。** 通常入口の固定2DB接続、実HTTP / Trino、実ブラウザー、全体回帰、独立レビューを完了しました。P2-4以降には進んでいません。

## 実装した境界

- `runtime_profile` を通常の各起動入口で読み、本人Basic認証、control_store、DB別backend資格情報、限定Polaris接続、SQL endpointを指定します。profileは秘密値を含まない絶対パス参照です。明示profileが不正、または有効化markerだけが残る場合は起動を拒否します。
- `query_context` でDatabase / Schema / Warehouse ID、SQL Catalog / Schema、選択role、binding desired revision、構成世代、grant revisionを確定します。非adminは既存のraw / sandbox / staging / martsと同じpurposeの組合せに限定します。
- `query_service` がWorkbenchとnative endpointの受付・継続・取消を共通化します。送信前にowner、SQL、request / worksheet ID、context、mode、backend usernameと秘密値を含まない資格情報snapshot参照をSQLiteへ保存します。schema v2はquery用の追加表だけを作り、Jobsの既存表と契約を維持します。
- 実行時のbackend資格情報とTLS contextはprocess内へ固定します。継続で現在の本人・元DB・元roleを再認可し、別のDBや新資格情報へ結び直しません。grant失効時は結果を返さず元資格情報で取消を試み、本人の明示取消は失効・降格後も受け付けます。他人のqueryにはplatform adminでもアクセスできません。
- backendが発行した完全なURIにopaque tokenを対応させ、別owner、role / catalog / schema変更、不明token、余分なquery parameter、許可外methodを拒否します。同一ページの再取得では行数を二重計上しません。
- history / inspectはownerと元DB / roleに限定した台帳参照です。別processでpinを持たないことだけを理由に履歴の状態を書き換えません。process内pinを失った継続・取消は拒否し、資格情報の再束縛やSQL POSTの自動再送を行いません。
- SQL名はTrino nativeの1 / 2 / 3部名として解決します。裸名は固定context、2部名は同Catalogの明示Schema、3部名は明示Catalog / Schemaを使い、SQL本文を書き換えません。通常ユーザーのcross-DBは両方へのgrantがあっても拒否します。
- 探索APIはDB専用Polaris machine principalから実Table UUIDを読み、ResourceRefへ不変object ID / incarnation / binding revision / generationを返します。resolve / detailでUUIDを再確認し、同名再作成は新incarnation、旧参照は拒否します。Viewの実identityが取得できない場合は強いidentityが必要な操作を公開しません。

## 通常runtimeへの接続

既存Phase 1のSQL名を維持し、SALES=`iceberg` / Polaris=`shirokuma_l1`、FINANCE=`finance` / Polaris=`finance`を固定登録しました。private operator手順で新しいSQLite / human accounts / DB別backend資格情報を用意し、通常Trinoへv2のfile ACL / OPAとpassword databaseを反映しました。

既定profileは `var/shirokuma/runtime-profile.json`、有効化markerは同じ場所の `runtime-profile.active` です。現在の参照先はprivateな `var/shirokuma/p2-3-acceptance/` 配下にあります。これらはGit管理外・所有者限定です。Workbenchへroot / S3 operator / JWT秘密鍵は渡しません。

`local_gitops` の通常snapshot経路にもprofileの認可overlayを接続し、file ACLとOPAの2ファイルだけを同じv2 manifestから生成します。これにより次の通常snapshotで旧role-only policyへ戻ることを防ぎます。Secret値はsource archiveに含めません。root Flux Kustomizationの意図したsuspendと、切替前の子Kustomizationのsuspend状態を維持します。

これは固定2DBの入口接続です。任意DB用のOpenTofu / GitOps構成全体の生成、plan / apply、3番目DB作成はP2-4〜P2-6に残します。

## 検証記録

| 検証 | 結果 |
| - | - |
| 実HTTP / engine | 最新判定181項目すべて成功。sales-only / finance-only / 両DBの別本人、1 / 2 / 3部名、未知DB・非READY・古いbinding、両DB grant保持者のcross-DB拒否を確認。書込み対象の1 / 2 / 3部名も両DBで成功 |
| query所有者・継続・取消 | 別本人（platform adminを含む）のGET / DELETEを拒否。role / Catalog / Schema変更、未知token、余分なquery string、method交換を拒否。失効・アカウント降格後は結果を拒否し、元roleを指定しないnative取消も本人の保存済みroleで受付。別入口の履歴照会で実行中台帳を破壊しない |
| 資格情報とページ | query送信後にprivate backend passwordを変更しても元のpinで完了。3,000行を複数data pageで取得し、同じpage tokenの再取得は同じ内容・二重計上なし。native endpointは101,000行を末尾まで返し、無言の100,000行打切りなし |
| 通常CLI / process再起動 | 通常 `bin/shirokuma warehouse query LOCAL` で両DBの実行・固定contextの台帳記録・最大行数での明示打切りを確認。Workbench / nativeで実行開始したqueryを残して両HTTP processを停止・通常起動し、元page / cancelを409で拒否。inspect / history / query ID / context / ledgerは不変、Trino管理APIで元の一意SQLのquery ID集合も不変、追加POSTなし |
| Polaris / Iceberg参照 | 両DBの実Table UUID / ResourceRef、columns / preview / snapshots / DDL。Snapshot IDは文字列、高精度Decimalは `12345678901234567890.1234567890` を維持。同名DROP / CREATE後は新incarnation・旧ref拒否。Viewのidentity不足時の制限はfocused試験で確認し、Viewの詳細な実機受入は後続 |
| 実ブラウザー | SALES / FINANCEの別sentinel、非同期query中の別タブ選択、独立Explorer、明示的新規Worksheet、role往復・grant失効で入力保持 / rowsとhistoryを非表示、更新後の明示再選択で復帰、現在grantの履歴を実行せず復元。再読込みはcontext / SQLだけを復元。日英、Ctrl / Cmd+Enter、1440 / 1024 / 390pxのページ幅を確認 |
| 全体回帰 | `make verify-local-experiment`: 773件中741成功・32skip・失敗なし。skipはopt-in実機・任意依存・ローカルOPA CLI不在であり、その機能全体の実機受入を意味しない |
| 他の確認 | Go `./internal/cli` 成功。`make verify-design-context`: 14試験と文書検査成功（92 documents / 10 issues / 42 references）。再起動後doctorは2026-10-19T14:32:39Zにhealthy。通常 `python -m tools.local_experiment.local_gitops prepare` は適用したsnapshotと同じdigest・60filesを生成 |
| 秘密値 | private入力から抽出した13秘密値をコード503・SQLite・公開結果・ログ16・通常snapshot相当60filesへ照合し、一致0。秘密ファイル自体はGit管理外・所有者限定 |

元の実HTTP不合格4件は試行履歴に残しています。DDL表示2件はread-onlyの `SHOW CREATE TABLE / VIEW` 解釈を追加し、複数ページ開始1件は標準の非修飾 `UNNEST` を許可して再試験成功しました。一般のtable functionや引数内cross-DBは引き続き拒否します。残る1件は「非READY DBは必ず一覧で無効表示する」という試験側の誤った期待でした。計画P2-A03は一覧から除外する動作も許すため、非表示または無効表示・SQL拒否を正しい条件として再確認しました。

再起動の補助試験では追加7件の期待値不一致がありました。3件はTrino 483の初回POSTがquery ID予約である点を見落とした実行開始前の観測で、最初のqueued nextUriをGETしてから台帳・engineを比較するよう補正しました。4件は実際には409拒否済みでしたが、Workbenchのtop-level `code` とnativeのhuman-readable `error.message` を共通の入れ子codeと誤認した判定でした。製品コード・権限は変更せず、実レスポンス契約へ試験を合わせて再確認しました。全試行は250件（239成功・11失敗）を保持し、同一項目の最終判定181成功・失敗0と区別します。

ブラウザーで見つかった4タブ時のスクロールバー重なりは、CSSだけで下余白と折返しを修正し、1440pxで選択、1024pxで選択・閉じる操作を再確認しました。JavaScriptは不変で、修正後のUI38 / Catalog17 / Refresh15の計70試験が成功しました。認可応答の順序逆転・履歴inspect中のdraft編集・別DBへ切替後の遅い失効応答は遅延させるfocused UI試験で確認しました。ブラウザーの可視確認と自動試験は別の証跡です。

最終Workbench画像は `var/shirokuma/p2-3-acceptance/workbench-final.png` に保存しました。

証跡はGit管理外の `var/shirokuma/p2-3-acceptance/` に保存します。`http-results.json` は各試行を保持し、再試験後の最新判定と元の失敗を区別します。private資格情報を含むファイルを公開・配布しません。

## 残す範囲

- P2-4: 任意DBの固定構成生成、Secret版参照、単一世代からの全構成生成。
- 2026-10-20追記: P2-4は固定4物理Schemaとpurpose同値のprofileに限定しました。それ以外のSchema / purposeのpolicy生成は別途profile拡張と受入が必要です。[P2-4記録](database-p2-4-config-generation-report-2026-10-20.md)を参照してください。
- P2-5: 全入口が参加するWarehouse drain / admission、管理Operation、複数processの適用排他、再起動後のUNKNOWN照合と継続復旧。P2-3の秘密snapshotはprocess内のみです。backend再配備を伴うoperator切替ではHTTPサービスも停止・再起動します。
- P2-6: 任意名の3番目DB、通常再配備と空環境構築での全構成再現。
- Jobs / Stages / Marts / schedule / AI / Ingest / WAP / Export / dbt / DRのDB一般化と管理UI、Windows Power BI / ODBC等のdriver実機受入。v2で未対応の通常Workbench / Bears入口は明示的に利用不可とし、旧Catalogへの暗黙fallbackを認めません。
- v2のSQL結果CSV / JSON / TSV出力も現段階では無効です。Table全件Exportとあわせて、出力時の本人・元DB再認可を含む後続受入へ残します。画面で無効理由を表示します。

既存の `warehouse_activity` には参加しますが、現在の一要求単位のlockをquery全寿命や全入口のdrain完了の証明には使いません。binding / generation / grantの変更は既存queryも再認可で失敗させます。適用中も継続・取消を保証する管理手順はP2-5の受入対象です。grant revisionが変わった古い履歴は復元を拒否し、現在の許可で作った履歴を使います。profile / registry / private資格情報の恒久配置と再生成全体はP2-4〜P2-6でまとめます。

再起動だけでは古いQUEUED / RUNNING記録を照合・終端化しないため、履歴の状態がengineの現況とずれることがあります。pin不在だけでは、別processが継続中のqueryまでUNKNOWNへ書き換えません。再起動後の既存queryの状態確認・必要なbackend取消はoperatorが行う範囲とし、自動照合・復旧はP2-5へ残します。

本記録は限定したローカル技術受入です。後続Phaseや運用者の受入、DR、外部公開の承認を兼ねません。
