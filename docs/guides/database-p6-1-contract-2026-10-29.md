# DB-P6-1 Database監査の実装契約

2026年10月29日。[後続計画](database-full-capability-and-compute-plan-2026-10-28.md)のDB-P6-1を具体化する。既存台帳の保存済みmetadataを通常Workbench／API／CLIから参照し、監査GETによって回復・取消・SQL実行・再送を起こさない。

## 閲覧範囲

| scope | 必要な現在権限 | 閲覧範囲 |
| --- | --- | --- |
| `self` 本人のDB内記録 | 選択roleが現在のaccount／DB grantの範囲内。有効なREADY DBと許可Schema | 本人かつ保存roleが一致する実行metadata。本文と結果は含めない |
| `database` DB管理情報 | 選択adminと現在のeffective DB admin権限。既存platform admin例外は維持 | 選んだDBの他actorを含むmetadata。別ownerのSQL・結果・定義・artifact権限は付与しない |
| `platform` 基盤管理記録 | 現在有効な実accountのplatform_admin、最大admin、選択admin | 作成・grant・表示名・retire・共有基盤構成等の管理metadata。retired DBの管理履歴も扱う |

DBを識別できない旧Pawprintsはplatform専用の別previewとする。DB名・SQL・時刻から帰属を推測しない。通常scopeでは未知・不許可・非READYを同じ利用不可として扱う。

認可は取得前と取得後に検査する。最後の検査は同じSQLite snapshotを再利用せず、現在のaccount、grant epoch、binding／generation、DB状態を新しく読む。途中失効時は部分的な一覧や詳細を返さない。

## 正本と公開する情報

query、DataOps、Job／Stage／Martのrun、scan／schedule／alert、WAP、DB作成と要求、grant／表示名／retire／構成operationを既存storeから読み取り専用で投影する。既存のlist／get関数には状態回復を伴うものがあるため、集約に使用しない。新しいevent pipeline、常駐サービス、台帳の複製は追加しない。

一覧・詳細・JSON取得に公開するのは、source、event ID、actor、DB／Schema／Warehouse、保存role、実在する時刻・revision、保存状態、commit有無、清掃エラー有無、信頼できる関連IDとする。historicalな実効roleを保存していない場合は未記録とし、現在の参照roleから作らない。

SQL、prompt、結果、定義、引数、入力file／directory、payload、backend username、credential参照、private path、例外全文は返さず、全文検索にも使用しない。SQL本文等は既存の本人・role・contextで再認可する通常APIだけが扱う。関連IDの提示は、その内容へのアクセス許可ではない。

WAP metadataがDB共有であることと、実行operationが呼出者に固定されることを区別する。各rowの作成時刻と最新の保存状態を示し、全状態遷移の履歴とは表示しない。model保存等の未記録時刻は`null`で示し、現在時刻で補完しない。時刻なしの行は最後へ並べ、期間filterがある場合は対象外と明示する。

operationのDBはsource固有のFKで解決する。空の`operations.targets`や構成manifestから単一DBを推測しない。`operations.timestamps`のlist形式を処理し、時刻が空のlegacy行も未記録として扱う。

## HTTP API

全入口は認証と明示した`X-Shirokuma-Role`を必須にする。監査routeはGETだけで、POST等は拒否する。

| 入口 | パラメーターと出力 |
| --- | --- |
| `GET /api/v1/audit/capabilities` | 現在roleで選べるscope、source、公開範囲・保持方針。DBごとの許可は各要求で検査 |
| `GET /api/v1/audit/events` | `scope`、`database_id`、`source`、`state`、`search`、`from`、`to`、`limit`、`cursor` |
| `GET /api/v1/audit/events/{id}` | `scope`、`database_id`で同一の権限検査を行い、許可metadataと関連IDだけを返す |
| `GET /api/v1/audit/events/{id}/download` | 詳細と同じ認可・同じmetadataのJSON。SQLや成果物downloadの代用にしない |
| `GET /api/v1/audit/telemetry` | P専用、旧Pawprintsの上限100件preview。DBの監査とは別表示 |

`scope`は既定`self`。`database_id`はself／databaseで必須、platformでは任意の絞り込み。limitは既定50・最大100。検索は公開されたID・source・action・actor・状態・許可されたDB識別子へのliteral検索のみ。未知・重複query引数、過大な入力、不正な日時／limit／cursorを拒否する。期間はUTCへ正規化する。

一覧は`items`、`page_cursor`、`next_cursor`、正規化した`filters`、認可済み`context`、`observed_at`、`coverage`を返す。`page_cursor`は初回も同じ取得上限で表示し直せる署名tokenで、前ページへ戻る際にcursorなしの新検索へ戻さない。各itemは`id`、`source`、`source_id`、`action`、`actor`、`database_id`、`schema_id`、`warehouse_id`、`saved_role`、`state`、`created_at`、`updated_at`、`finished_at`、`revision`、`committed`、`has_cleanup_error`、`links`を基本形とし、未記録値はnullにする。detailは`event`と同じcontext／coverageを返す。IDはopaque tokenとし、UIは内部構造を解釈しない。

`context`は`scope`、`principal_id`、`selected_role`、`database_id`、`binding_revision`、`generation_id`、`grant_revision`、`platform_admin`を持つ。platform全体のDB固有値はnullにする。telemetry previewは`items`、`availability`、固定`code`、`limit`、`truncated`、`source`を返し、各itemは既存schemaの`event_id`、`timestamp`、`agent_id`、`action_type`、`target_system`、`status`、`trace_id`、`duration_ms`に限定する。

`links`は`relation`、`source`、`source_id`、`event_id`の配列とする。関連eventの現在の監査閲覧権限まで確認できた場合だけopaqueな`event_id`を返し、監査metadata内で辿れるようにする。DB等のresource IDはtyped metadataとして示し、contentへの任意URLや実行操作には変換しない。

cursorは署名とversionを持ち、本人・role・scope・DB・現在認可・全filter・limitへ束縛する。作成時刻／source／IDの一意な降順seekと初回上限を使い、後から追加されたrowでページがずれないようにする。別scope流用・改ざん・認可変更・process再起動後は`STALE_CURSOR`として再検索を案内する。offsetと最新100件だけの固定切取りは使用しない。

## Pawprintsと保持方針

Pawprintsのpreviewは、既存AgentHouseの明示列だけを固定SELECTで読む。runtimeの作成・起動は呼ばず、接続timeoutを短く制限する。資格情報不備、停止、HTTP／JSON異常は固定code付きの`availability=unavailable`で返し、例外本文は出さない。durable監査の要求にはClickHouseを含めず、停止しても台帳の状態や検索結果へ影響させない。

previewはsourceと上限を明示し、全履歴やDB相関として扱わない。画面の検索・状態filterは取得済みの上記8項目だけに作用し、全履歴検索やraw payload検索とは表示しない。role／scope／認可変更時は取得行と表示を消去し、古い応答で復活させない。既存台帳とPawprintsの保持設定は変更せず、監査用にSQL／prompt／結果を複製しない。台帳の自動削除・保持期限変更は本unitに含めない。

## WorkbenchとCLI

既存監査タブに独立したscope／role／DBと期間・種別・状態・検索語を置く。明示検索、cursor前後移動、行の詳細、metadata JSON保存を提供する。フォームと適用済み条件を区別し、選択や後着応答でWorksheet／Explorerの実行先を変更しない。

principal、role上限、platform権限、DB／grantの変更、認可取得失敗で行・詳細・download handleを消し、後着成功で復活させない。定期処理は既存の認可再確認へ統合し、全履歴の自動pollはしない。フォーム・focus・caret・開いた詳細を無関係なrefreshで再生成しない。日本語／英語、keyboard、狭い画面に対応する。

CLIは同じHTTP APIを使う`shirokuma audit`のlist／show／export／telemetryとする。control storeやbackend資格情報をCLIから直接読まない。exportは明示したlocal pathへmetadata JSONだけを保存し、既存fileを黙示上書きしない。

## 必須検証

同名資源を持つ2DB、別owner、V／E／A／P、途中失効、retire、他DBイベントID、SQL等のsecret sentinel、cursor流用／改ざん／同時刻／追加rowを試験する。UNKNOWN・commit後記帳失敗の監査GET前後でstoreと外部送信が不変であることを確認する。

実ブラウザーでは通常入口の検索・詳細・page・JSON、scope／role／DB変更、遅延応答、reload、日英と狭い画面を確認する。既存環境のbackend反映は、活動とUNKNOWNを再確認した上で既知Workbench ownerだけを正常再起動し、同じprofile・台帳・SQL下書きを保つ。
