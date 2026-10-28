# P5-2 増分材料・bootstrap receipt・full候補 実装報告

2026-10-23。[Phase5計画](database-phase5-implementation-plan-2026-10-23.md)と
[P5-0契約](database-p5-0-contract-2026-10-23.md)に従う技術検証記録。
P5-1の正本root・準備要求・委譲・永続停止を利用するprivate準備処理を追加した。
部品実装・独立レビュー・専用実機の技術検証を完了した。Phase5全体の受入は後続に残る。

## 実装した境界

- `database_creation_preparation.py`が、固定されたprivate設定と正本の準備要求IDから材料を生成する。
  公開入力にprivate path、任意command、資格情報、receiptを渡す経路は追加しない。
  要求単位の排他・claimと不変の保存先を使い、保存済み候補は同じ要求から再発見する。
- 既存のconsumer ID/secret、TLS/S3/JWT、既存DBのpassword verifier、retired参照を引き継ぐ。
  新DBに不足する材料だけを追加し、既存DBへの追加時に全体初期化は行わない。
  shared policy/password DBの必要な追記と、既存entryの変更を区別する。
- 新DBのactive desired grant集合を、P5-1受付時の初期grant intentと照合する。
  他DBの未適用変更や、受付後のgrant追加・変更・撤回を候補へ黙って取り込まない。
  材料生成前と正本への最終登録transactionで再確認する。
- 保存ToFu planを検査してから、generation・operation・phase plan・準備要求を原子的に関連付ける。
  各phaseのToFu data directoryを固定し、init/plan/show/applyで同じ値を使う。
  auth headers、S3設定、kubeconfig、CAなどの外部入力も初回Operator bind前からpinに含める。
- 要求者と独立したexecutorの現在権限・principal gateを継続検査する。
  executorのgate epochを準備claimと適用claimの両方に結び、STOP後のアカウント復旧・RELEASEでも
  古いclaimを復活させない。P5-1の適用guardで不足していたexecutor側の永続停止も補った。
  失効時は業務書込をrollbackし、別transactionでSTOPと依存する未完了承認の撤回を保存する。
  送信直前と最終確定で再確認し、他の要求者のRUNNING/UNKNOWN効果がある場合もexecutorの解除を拒否する。
- 新しいbootstrap/full承認・限定回復承認にも、同じtransactionでexecutorの現在権限とACTIVE gateを要求する。
  STOP中の新承認は拒否し、同keyの保存済みACKは承認を復活させず再発見する。
  RELEASE後も本人の新しい承認を必要とする。

## 実発行と承認の分離

bootstrapの資格情報発行は既存`database_provision.provision()`を使う。
発行前のDISPATCHED、秘密値のprivate保存、ACKの順序を維持し、応答喪失・不正応答・保存失敗・
再起動・GET 404を、資格情報の再POSTを許す証拠として扱わない。

新しいbootstrap verify proofには、private発行receiptの正規化digestを含める。
P5-2のfull準備では、正本のPREPARED bootstrap、成功したverify stepのevidence ref、operation/actor/
generation/binding、configuration、全発行ACK、二つの異なるconsumer、発行receiptのdigestを照合する。
材料validationだけで実発行済みとは判定しない。旧形式receiptを読む既存経路は保持し、
P5-2のstrict経路に旧形式を流用しない。

full候補は実bootstrapの資格情報とpredecessorを用いて別の保存planとして生成する。
同じrootの正本PREPARED bootstrapが既存DBをRECONCILINGへ移した場合だけを許容し、
他のroot・operation・revision・desired変更を拒否する。bootstrap承認はfull承認に流用しない。
full候補の作成時点ではREADY化、effective grant昇格、runtime activationを行わない。

## 検証記録

- 実装と修正は限定ファイルを指定した実`codex-cli` workerで行い、別agentが認可・receipt・互換性をレビューした。
- 準備処理の修正後、関連6 suiteは137件、route suiteは5件、計142件PASS。
  STOP/RELEASE後の古いclaim、初期grantの変更、ToFu環境変数、外部入力の変更、
  bootstrapからfullへの接続と異なる予約の拒否を含む。
- 最終レビューで既存適用経路のexecutor停止不足を再現し、適用側に9件、承認受付側に8件の回帰を追加した。
  適用側の関連7 suiteは177件、承認側の統合4 suiteは79件PASS。元のtest methodとassertionを維持する。
- 最終ソースの`make verify-core`はexit 0、435.87秒。Python 74 group、2,020件中1,987件PASS・33件SKIP。
  Goのtest/vet/buildとCLI help、設計context、改行・空白・秘密file名の検査もPASS。
  `verify-local-experiment`と共通の`test-resident-core`を含む。
  SKIPは別機能のruntime opt-in 30件、ローカルOPA binary未配置2件、optional pyarrow 1件。
  これらをP5-2の実機試験成功として数えない。
- 独立した3ケースで、executorのSTOP、実検出したaccount失効後の復旧、STOP/RELEASE後の旧claim・permitを拒否した。
  未完了承認はREVOKED、外部処理callbackはすべて0件。他要求者のUNKNOWNがあるexecutorのRELEASEも拒否し、
  UNKNOWNとSTOPPEDを保持した。試験前後の固定ソース639件のhashはすべて一致した。
- unitの制御されたverify callbackと仮receiptは、正本・材料契約の試験である。
  実Polaris発行と実Operator検証の証拠には数えない。
- 生成器、`database_security.py`、`trino_auth.py`、`requirements-generation.txt`のSHAは作業前と一致する。
  既存の受入済みprofileを再生成・更新する変更は加えていない。
- 受入用ハーネスは最終60件PASS。worker実行と独立再実行の両方で確認し、6ファイルのcompileもPASS。
  専用3環境の実行を終え、全専用process・containerを停止し、volume・network・ledger・receiptを保全した。
- 計画更新後の文書検証もexit 0。関連14件、設計context 93文書・10 issues・42 references、
  改行・空白・秘密file名の検査を通過した。

private証拠は`var/shirokuma/p5-2-acceptance/private/`に保持する。
公開報告へ秘密値、raw ToFu JSON、private入力の内容を転載しない。

専用実機でのP5-1受付前提には、private process内だけのcontrolled foundation/owner adapterを使う。
正本のcreate/mutate/claim処理を通し、rootやapprovalのSQL直接挿入で代用しない。
これはP5-2の部品検証用前提であり、P5-3の実owner capability・公開bridgeの成立証拠ではない。
実認証ファイルの読取り、Operator、ToFu、Polaris、OAuth、発行receiptは制御された成功callbackで代替しない。

## 専用環境の実機記録

初回の専用正例環境では、実Operatorのbootstrap/full適用による既存DB一つのREADYを確認した。
P5-2の増分bootstrap候補で、既存DBの値・password・shared値・TLS・verifierの保持を確認した。
実認証ファイルのexecutor失効も検出し、STOPの永続化、旧承認のREVOKED、
アカウント復旧後の旧permit拒否を確認した。
この停止試験で計測したOperator・ToFu・Polarisの効果callbackは全て0件である。
資格情報保持の実機母数は既存active DB一つであり、retired参照の保持と複数principalの独立した認可は
関連unit試験の証拠として区別する。

続くbootstrap適用は、意図した発行応答喪失に到達する前の`reconcile_storage`でUNKNOWNとなった。
`publish_snapshot`と`apply_secrets`はACK、Polaris発行は0件である。
PVCはBound、legacy Flux rootはsuspend中、保存ToFu planの変更はSecretのみだった。
Bucketのprefixは期待値へ更新されず、準備用と製品の二つのSSA managerが所有していた。
既知S3応答後の初回source/child適用を別managerで行った受入準備が、この所有状態を作っていた。
同じdesired payloadと製品managerによるserver dry-runは`.spec.prefix`の競合を再現し、
UID・generation・resourceVersion・specは前後で不変だった。元の実applyのエラー本文は残っていないため、
dry-runの再現結果と元UNKNOWNの証拠を分けて保持する。

このUNKNOWNは成功化・再apply・再発行せず、初intent、ledger、receipt、専用volumeを保全する。
fresh環境の初回source/child適用managerを製品と揃え、別rootで再試験した。
初回で未到達だった項目は、以下の別rootの正例で確認した。

| fresh正例の項目 | 実機結果 |
| --- | --- |
| 既存DBを持つbaseline | 実bootstrap PREPARED、full APPLIED、既存DB一つがREADY |
| 増分bootstrap材料 | 既存DB値、password、shared値・secret、TLS、verifierを保持 |
| executor失効 | 実account readerで失効を検出しSTOP、旧承認REVOKED、復旧後の旧permitも拒否。計測効果0 |
| ACK保存後の応答喪失 | 実資格情報の保存を確認した後、外側の戻り経路を一度中断しUNKNOWNを確認 |
| 限定回復 | productionの正の観測と新bootstrap承認で同じoperationがPREPARED。回復時の業務POST・PUT・ToFu適用は全て0。観測用OAuthは6回 |
| full候補 | 実bootstrap receiptを使って別operationをAWAITING_APPLYで登録。発行identityと既存資格情報を保持 |
| phase承認分離 | bootstrap permitによるfull適用を拒否し、計測効果0。full承認は0件 |

READY一つはbaseline構築時点の観測である。増分bootstrap後の実値は旧DBがRECONCILING、
新DBがREGISTEREDであり、full候補は未適用のまま保持する。既存資格情報の保持とREADYの継続を区別する。
独立した正本照合で、実発行4consumer（各DB二つ）の全ACK、provisioning digest、verify evidence ref、
ledgerの一致を確認した。bootstrap/fullのroot・phase・predecessor・manifest・file sealも一致する。
fullの全stepはPENDING、approvalは0件、targetのactive effective grantも0件だった。

正例の故障注入は、実Polaris処理とdurable ACKの成立を確認してから戻り経路を中断する。
正例の根拠は`positive-v2/stages/incremental-*/first-receipt.json`と正本・sealed receiptに保持する。

別環境の負例では、実first credential POSTの応答を受け、実client ID/secretが返ったことを内部で
確認してから、private保存前の戻り経路を一度中断した。秘密値自体は証拠出力へ保存しない。

| 保存前の応答喪失 | 実機結果 |
| --- | --- |
| 初回実行 | operationはUNKNOWN、credential leafはDISPATCHED、資格情報のdurable保存なし |
| 既存observer | 実principalのGETが成功しても、秘密値未保存のため同じoperationをUNKNOWNで保持 |
| 観測時の再送 | 業務POST・PUT・ToFu・Operator実行は全て0。GETは1回、観測用OAuthは2回 |
| 停止点 | strict receipt validatorが拒否。新DBは非READY、active effective grantとfull候補は0件 |

根拠は`stop/stages/incremental-*/first-receipt.json`と正本・private発行receiptに保持する。
実機faultは上記のACK保存後と秘密値保存前の二つの境界である。
不正応答・保存例外・GET 404等の他の再発行禁止条件は、関連unit試験の証拠として区別する。

## 既存環境の最終保全確認

全専用環境の停止後、既存Workbench/nativeは認証付きHTTP 200、generation/profile refは初回と一致し、
記録済みの6 PIDも生存していた。通常profile validationは成功し、AUTH_FILEのbytesは対応する過去の
file単位の保存hashと一致した。postflight v1のmappingをscalarとして比較した誤判定は元記録を保持し、
訂正receiptで対応file一意選択と一致を記録した。

schema19・70 tables、query 2,794件・job 85件・admission 3,993件、未閉鎖0、OPEN155、
FENCED UNKNOWN 3件と未連結予約UNKNOWN 1件は初回と一致した。既存環境のP5四表は0件のままである。
primary nodeのUID一致とReadyは、全専用環境停止後にCommanderも独立して読み取り確認した。
P49 cleanは停止した同一containerと4 volumes・networkを保持し、P5-2の3環境も停止・資源保全した。

証拠は`postflight-final-v1.json`と`postflight-auth-anchor-correction-v1.json`に保持する。
profile自体の開始時byte hashは未採取なので、開始時からのbyte一致は主張しない。
profileの通常validationとruntime generation/profile refの一致を確認した範囲で扱う。
実機終了時は固定ソース639件全てが一致し、その後はPhase5計画の進捗文書一件だけを更新した。

## 後続フェーズとの境界

P5-3のcanonical Go owner/IPCと両runtimeの引継ぎ、P5-4の新WebUI・通常CLI導線は未実装である。
通常起動の公開Createは、実foundation/owner adapterが存在しないため、引き続き登録前に
`503 FOUNDATION_NOT_READY`で停止する。今回のprivate準備機能を公開経路の準備完了へ読み替えない。
今回のfull候補にはP5-3の実owner/runtime引継ぎ情報がまだ束縛されていない。
その接続時は必要なruntime profile等を束縛した再計画と新承認を行い、保存済み候補の内容を後から書き換えない。
P5-5〜7の縦断・故障・所有者受入も後続に残る。
