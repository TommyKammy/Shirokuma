# ADR-0038 通常DB作成の後継契約案

2026-10-27。[ADR-0038](../design/07_ADR/ADR-0038_Separate_Logical_Database_Creation_from_Platform_Provisioning.md)
の順序2。**設計契約案であり、公開API・製品経路は未実装**。
[実現性記録](database-adr0038-feasibility-2026-10-27.md)の共有catalog方式を前提に、
順序3の実装範囲と成功条件を固定する。旧[P5-0契約](database-p5-0-contract-2026-10-23.md)は
既存経路へ引き続き適用し、新方式の根拠として旧承認を流用しない。

## 1. 採る構成と切替単位

- 基盤は環境・信頼領域単位の共有Polaris／realm、PostgreSQL、SeaweedFS 4.51互換STS、
  Trino 483 dynamic catalogと永続catalog storeを用意する。DBはcatalog、consumer、
  prefix限定IAM role/policy、固定4schema、binding、初期grantの組とする。
- Polarisのissuerにはobjectの直接読書きを許さず、DBごとの短期資格情報を払い出す。
  共有プロセスを管理する主体は各DBのstorage roleを引受け可能であり、共有サービス自体を
  信頼する境界になる。悪意あるcatalog serverからのDB隔離や独立した障害領域を保証しない。
- 新DBの秘密値はimmutableな参照IDへ先に保存し、catalog SQLには参照だけを渡す。
  DB別ENVや静的S3キーを追加しない。全workerへの公開を確認する前にcatalogを公開しない。
  共有Trino coordinator／workerのprocessと管理主体も全DBのconsumer秘密を読める信頼主体である。
  共有processの侵害からのDB別秘密分離は保証せず、実利用者のSQL/REST/storage認可隔離と区別する。
- 既存DB移行adapterを初期実装の条件としない。別のcleanな専用基盤で新経路を実装・検証し、
  切替時は旧実験DBを破棄して再構築できる。破棄対象・旧履歴・未解決intentを記録する。
  旧UNKNOWNは成功・未実行・再送可能へ変換しない。
  再構築したDBは新IDとし、同名でも旧Job／Stage等の参照IDを黙示的に新DBへ付け替えない。
- 通常CREATEはToFu、Flux、全体drain、Pod再配備、両API profile切替、全DB走査を呼ばない。
  共有基盤の初回導入・image更新・SPI導入・鍵変更には基盤管理の承認・影響検査を適用する。

Seaweedのlegacy ARNは、固定account・検証済みrole名・元ARNの一致を条件として標準ARNへ
限定補正する。任意ARNの書換えや別accountへのfallbackをしない。
Trino dynamic catalogは実験機能であるため、catalog削除時のconnector resource回収や
通常再起動の制約を記録し、初期範囲では既存catalogの上書き・credential rotationを扱わない。

## 2. 公開APIと利用者の操作

collectionは既存の`C=/api/v1/admin/database-creations`を利用する。serverの導入設定で
`creation_mode=logical_v1`を選び、capabilitiesと各rootへ固定する。clientに接続先や実装方式を
選ばせない。旧rootのmodeを後から変更しない。

| 操作 | 新modeの契約 |
| --- | --- |
| GET `C/capabilities` | mode、環境、foundation revision、warehouseの起動状態、準備可否と安全なblockerを返す。副作用なし |
| GET `C/principals` | 旧契約と同じ有効account・role ceilingだけを返す |
| POST `C` | 旧Createの入力とIdempotency-Keyを維持。新規202で同一rootの受付ACKとLocation、同key再発見200。1回の作成操作で対象DBの処理を依頼 |
| GET `C` / `C/{id}` | 現在の対象DB状態、進捗、desired/effective grant、blockerと許可された操作を返す。照会で次段階を実行しない |
| GET `C/by-name/{sql_catalog}` | 現在の環境・modeで名前を完全一致させ、`C/{id}`と同じ詳細を返す。一覧全件の描画を避け、UNKNOWN／STOPPEDも照会できる。照会で処理を実行しない |
| POST `C/{id}/withdraw` | 未完了処理への新dispatchを停止。確定済み外部効果・元intentは残し、削除やrollbackとしない |
| POST `C/{id}/reconcile` | 対象DBの固定observerによる照合を依頼。clientの任意evidence／成功宣言を受け取らない |
| POST `C/{id}/resume` | `{expected_revision}`と新しいIdempotency-Keyで、結果が全て既知になったBLOCKED rootの未送信処理だけを再依頼。元要求者の現在認可を検査。UNKNOWN／STOPPEDのままでは409 |
| 旧modeのplans / approve / replan / recover-verify | 新modeのrootでは409 `CREATION_MODE_MISMATCH`。隠れた2承認の自動代行をしない |

Createの名前、表示metadata、既存principal、初期grantの入力制約、same-origin、重複header拒否、
明示認証account・選択admin・platform admin確認は旧契約を継承する。
権限を持つ利用者が名前と初期アクセスを入力し「作成」を押す。その後は自動進行を表示し、
READYになれば同じDB・schemaのSQLへ進む。更新・再読込・deep linkで追加dispatchしない。
SQL本文、実行先、保存Job等のcontextを別DBへ黙示変更しない。

停止中warehouseは`WAREHOUSE_START_REQUIRED`、未設定・不整合の基盤は
`FOUNDATION_NOT_READY`として受理前に停止する。基盤構築やwarehouse起動の数分を通常CREATEへ
隠さず、別操作として案内する。新規202はREADYを意味せず、HTTP応答だけを性能達成としない。
公開応答はno-store、秘密値・private path・raw provider error・catalog SQLを含まない。
名前による照会も現在の認証・選択admin・platform権限を検査する。名前は一度だけURL decodeして
既存の識別子制約を適用し、queryを受け付けない。未発見は404 `DATABASE_NOT_FOUND`、
曖昧な結果は409 `DATABASE_NAME_AMBIGUOUS`とし、別環境や履歴ACKで代用しない。

## 3. 正本・排他・不明結果

foundation revisionと、DB別binding／権限revisionを分ける。単一generationへの全DB集合完全一致を
新modeの条件から外す。全入口が同じregistryを使い、未確定bindingを既存static profileへfallback
しない。具体的なtable／migration番号は順序3のsource確認時に決め、既存stepと成功台帳を二重化しない。

| 対象DBの状態 | 意味と次の処理 |
| --- | --- |
| `CREATING` | 名前・要求・初期desired grantを原子的に予約。対象resourceを順に作成し、各外部効果の前にintentを保存 |
| `VERIFYING` | 秘密参照、catalog、policy、認証／認可、bindingの候補が揃い、対象DBだけを確認中 |
| `READY` | 同一の有効revisionと現在認可を最終CASで確定。effective grantを公開し、新規SQL/DataOpsを許可 |
| `BLOCKED` | 未dispatchまたは結果が既知の停止。原因と現在許される操作を表示 |
| `UNKNOWN` | 受付／資格情報発行／provider適用等の結果不明。同じ外部効果を自動再送しない |
| `STOPPED` | 撤回・要求者／executor失効等による永続停止。新dispatchを禁止し、観測と既存結果保存を許可 |

受付transactionは同key照合、現在認可、基盤revision、名前の一意予約、root／DB／desired grantと
ACKを一括確定する。scopeは旧契約同様actor・環境・route・keyとし、異payloadは409。
同名の異keyは原子的に409、別名の作成は並行可能とする。DB transaction中にprovider通信しない。
永続owner／claimとCASは対象DB単位に置き、基盤更新はrevisionとdispatch fenceで競合を防ぐ。
既存resident/private executorへ有界処理を載せ、別の常駐supervisorや汎用workflow engineを追加しない。

作成順は、対象role/policy→catalog/consumer/権限/4schema→immutable秘密参照の全node公開→
catalog登録→認証／認可・binding反映→対象検証→READYを基本とする。
前段が不明なら後段へ進まない。credential発行応答を保存できなかった場合は新credentialを
再発行して穴埋めしない。再発行には元principalの停止・失効確認と新しい明示intentを必要とする。

observerは元intentに束縛したresource identity、catalog、policy、metadata、credential参照等を
読み取り、正の一致を保存する。404単独やTTLを未実行証拠にしない。
今回のPUT照合のように固有key・元payload一致を観測できる場合だけ当該効果を確定する。
reconcileは新dispatchを行わず、UNKNOWNの全効果が既知になればBLOCKEDへ移す。
未送信の後続処理は元要求者の明示resumeを必要とし、既存効果は再送しない。
STOPPEDでは観測・receipt保存だけを許し、自動復活・resume・READY確定を禁止する。
対象DBの失敗で他のREADY DBを一括RECONCILINGにしない。共有基盤の故障は別の基盤障害として扱う。

## 4. 秘密公開と現在の認可

file secret-providerは固定pathだけを読み、参照IDから任意pathを解決しない。秘密fileは
同じPVC内でlock・既存値との一致検査・atomic replaceを行う。既存参照を別値で上書きしない。
nativeの初期範囲は同一namespace・単一K3s node上のlocal RWO PVCを共有するPod群とする。
以下の全node／workerはその範囲のTrino nodeであり、別K3s nodeへの伝播やmulti-node storageを保証しない。
新規refは全nodeの実providerによるreadbackを確認してから利用する。
whole directoryをmountし、file単体／subPath mountやKubernetes Secret投影の待ち時間に依存しない。
worker追加時も同じ有効ref集合を確認してから利用可能とする。

hostの既存private executorから公開する場合は、server側で固定したPod/container/UIDとJava publisherへ
stdinで値を渡す。公開APIからcommand/path/pod名を受け取らない。`pods/exec`のRBACだけではargvを
制限できないため、これはprivate executorを信頼する境界である。実装時に既存権限と対象制限を
確認し、汎用exec API、新しい常駐secret server、hostPathを追加しない。

**新しいDB名が見えることと、そのDBへ現在の利用者がアクセスできることを分けて検証する。**
prototypeのA/B userは起動前設定、Cは既存operatorを使った。password/file ACL/OPAの動的更新は
まだ実証していない。順序3では次を同じDB権限revisionへ束縛する。

- 実際の認証読取経路でaccount有効性、role ceiling、platform権限、対象grantの失効を確認する。
  受付・各新dispatch・READY確定・新規SQL/Stage/Job/Mart受付で再評価する。
- desired grant、候補binding、private認証材料、engine認可の反映を別々に保存し、全ての有効版が
  一致してからeffectiveを公開する。file refreshの時間経過だけで成功扱いせず、実利用者・roleで
  対象SQLの正例と拒否を確認する。
- 過渡期は未確定DBへの利用を拒否する。失効後の新規要求を旧cacheで許可せず、engine側にも
  拒否が反映されたことを確認する。既に受理した実行の観測・取消は既存契約を継承する。
- SQLだけでなくStage、Job、Mart、manual/scheduled execution、表示・deep linkも同じregistryを使う。
  client指定contextや保存時revisionだけで実行を許可しない。

READY検証は対象4schema、catalog／role/policyのreadback、対象prefixの限定marker I/O、
候補bindingと実roleのSQL/拒否を用いる。READY最終CASはroot未停止、現在のrequester/executor gateと
grant epoch、所有claim、foundation/binding/policy revisionが検証時と一致することを同時に要求する。
撤回・失効・claim喪失が先ならin-flightの結果receiptだけを保存し、effective grant／READYを確定しない。
全DBのtable検査は通常CREATEに含めず、
実Iceberg tableの書込み・読取りと他prefix拒否は基盤受入・統合回帰でも別途確認する。

## 5. 実装を分ける単位と未完了gate

| 単位 | 変更と必要な証拠 |
| --- | --- |
| 3a: 共有基盤とpublisher | digest固定deploy、PVC/SPI、STS/Polaris境界、private固定publisher、cold起動／通常再起動／worker追加、実engineのsession期限前後の更新・issuer/role失効時挙動、秘密非露出 |
| 3b: 対象DBのcore | modeと永続intent、名前排他・DB別claim、binding/auth revision、失効、応答喪失・再照合、動的認証／ACL/OPAの実経路 |
| 3c: CLIとWebUI | 同一API、1回の作成・自動進行・reload/deep link、対象SQL/Stage/Job/Mart、実stackでの複数回／DB数別計測 |
| 4: 切替と本人受入 | 選択した再構築、退役対象記録、正常再起動・権限分離、所有者が1回の作成からSQLまで確認 |

Trino workerへの実I/O伝播、native PVC公開の実証結果は実現性記録へ追記する。
内部TLS、HA、worker障害、既存credential rotation、全面DRは本prototypeの保証に含まれない。
STS単体の期限切れ拒否は確認したが、実Trino/Polarisの長時間稼働中のsession自動更新は未実証である。
issuer/roleの失効と発行済sessionの有効期限を同一視せず、順序3aで挙動と許容期限を固定する。
2026-10-28の[製品実装記録](database-logical-create-implementation-2026-10-28.md)で、実engineの期限後更新と、
対象roleへのissuer発行許可取消・900秒の残存session・期限後拒否・許可復元後の読取を検証した。
上記の未実証は順序2時点の状態であり、現在の証拠と残る制限は実装記録を参照する。
稼働済み基盤上の作成からREADY／同context初SQLまでp50≤2秒・p95≤5秒は未達成の暫定目標を維持する。
2026-10-28の[新経路の本人受入](database-logical-create-owner-acceptance-2026-10-28.md)は条件付き受入。
作成導線は受け入れられたが、待ち時間の改善を継続条件とし、暫定性能目標の未達を別に管理する。
