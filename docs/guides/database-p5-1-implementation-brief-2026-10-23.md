# P5-1 実装worker brief

対象は[P5-0固定契約](database-p5-0-contract-2026-10-23.md)の§1〜4とP5-1引渡し。
P5-0検査は既存venvで1,892 PASS/33 skip、設計検査93 documents PASS。P5-1の変更前baselineを別に保持する。

## 実装範囲

1. control storeの追加migration19と4表。既存履歴、grant epoch/tombstone、UNKNOWN、retiredを保持する。
2. warehouse単位の作成受付排他、同key再発見優先、異key/旧登録競合の登録前拒否。
   shared connection内部helperを使い、root/DB/binding/固定Schema/metadata/初期desired grant/ACKを原子的に保存する。
3. creationの公開route、principal一覧、永続GET、準備/承認/withdraw/再計画/照合依頼の記録。
   P5-2/3未実装を成功扱いせず、能力不足を明示する。新規Createのfoundation/owner不足503契約を弱めない。
4. typed private委譲、現在のrequester/executor認可、gate revision束縛、承認/claim/cursorとimmutable ACK。
   principal STOP＋未完了承認撤回の原子的記録、明示RELEASEと新承認、private停止/解除の運用入口。
5. P5 Operation/有効warehouse予約をcoreが必ず認識するguard。旧manual同actor、旧公開API/CLI、
   callback未設定からの迂回を拒否。非P5互換、grant剥奪、既存Jobの合法な観測/取消/固定回復は維持する。

P5-2の材料生成、P5-3のIPC/Go owner管理/外部dispatch/verify recovery、P5-4のWebUIと新公開CLI UXは実装しない。
P5-1はその正本関連・承認・guardの基礎を用意し、存在しないprivate機能を呼んで成功表示しない。
private plan関連付けは将来の製品準備workerから使う内部境界とし、公開の任意Operation attach入口を作らない。

## 許可file

- `tools/local_experiment/control_store.py`
- `tools/local_experiment/catalog_admin.py`
- `tools/local_experiment/display_management.py`
- `tools/local_experiment/grant_management.py`
- `tools/local_experiment/request_auth.py`
- `tools/local_experiment/warehouse_operations.py`
- `tools/local_experiment/warehouse_operator.py`
- `tools/local_experiment/workbench_server.py`
- 新規`tools/local_experiment/database_creations.py`、必要なら新規`database_creation_auth.py`
- 新規`tests/test_database_creations.py`、`test_database_creation_routes.py`、`test_database_creation_auth.py`と、
  上記の既存機能に対応する限定した既存test file。

docs、Go、UI、live設定、private認証、runtime profile、cluster、既存台帳・物理データをworkerは変更しない。
repoには既存の大量の変更がある。reset/clean/reformat/commit/pushを行わず、許可fileの今回diffだけを扱う。
RTK/AGENTS/ADR-0036を守り、shellはrtkを付ける。秘密値やraw private設定をtool出力へ出さない。

## 検証とDoD

focused検証を既存dataops venvで実行する。temporary control store、named auth account、通常HTTP/CLI入口を使い、
migration保持/rollback、登録途中失敗の全rollback、同key ACK、異key並列、初期grant desired/effective分離、
role ceiling、別admin拒否、独立2要求者＋executor、gate停止/解除/古いclaim拒否、旧経路迂回を検証する。
current authority lossと表示role切替を区別し、CONSUMEDをclaim時に設定しない。
claim/leaf/最終CASのgateを同じtransactionで検査し、外部通信中にtransactionを保持しない。
新しいprivate機能の未準備を示す負試験と、既存API/CLIの正常互換を対で確認する。
fixtureだけに通用する分岐や公開のtest bypassを追加しない。missing adapterを静的available flagで偽装しない。

workerは実装・focused試験・変更file・制約を報告する。Commanderがdiff、独立guard review、全体検査、
必要な通常入口での隔離検証を行ってP5-1技術報告を作る。P5全体実機受入は別gateとして残す。
