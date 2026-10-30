# DB-P6-3C2B5 native TABLEの明示公開

2026年10月31日。[C2B4](database-p6-3c2b4-native-quality-tests-2026-10-30.md)の品質成功済み候補を、同じDatabaseのMARTSへ正式公開する単位である。この限定範囲の技術受入を完了した。

## 操作と条件

新規native TABLE 1件に付属する非空の品質test一式を明示実行し、全件PASSED・失敗件数0を保存したrunを対象とする。TABLE作成済みだけのNOT_RUN、品質失敗・取消・結果不明は対象にしない。`publication_allowed`は確認へ進める状態を示し、現在の公開権限や公開済みを意味しない。

Eが作成した候補をAが確認して公開できる。作成者のactor・全9項目context・資格情報参照を維持し、公開には現在のA本人・公開context・新しいadmissionと固定資格情報を使う。別Databaseや別Schemaへ読み替えない。公開先はcompile時に保存した同じDatabaseのMARTS relationであり、モデル表示名やSANDBOX候補名から再生成しない。

Workbenchで対象runの公開確認を開き、作成者／公開者、候補Snapshot、品質test数、公開先FQNと既存Snapshotまたは未作成を確認してから送信する。CLIは`dbt publish-preview`のJSONを保存し、`dbt publish --expected-target-file <保存したJSON>`へ渡す。APIは`GET /api/v1/dbt/runs/{id}/publication-preview`で読み取った`expected_target`を、明示した`node_id`とともにpublishへ送る。既存SQL/WAPの公開payloadは維持する。

## 実行・履歴・回復

previewはmetadataの読取りだけを行う。受付前、監査前、公開SQLの最終送信直前にも候補と確認済み公開先を再検査する。既存targetは元Ref／UUID／incarnationを維持し、未作成targetに別TABLE／VIEWが現れた場合は自動採用しない。受付前に確認対象が変わった場合だけ、公開確認をやり直すよう案内する。

公開処理は固定候補Snapshotの行数監査（最低1行）と、明示列・固定PARQUETのCTAS／CREATE OR REPLACE TABLEからなる。native SDKのbuild／品質testを再実行しない。既存DataOpsの操作・query receiptとdbt publication履歴へ`origin: native_table`を保存する。manual-WAPのmodel／build／audit／publicationを見せかけで作成しない。

受付後は元body／key／publication IDへ固定し、応答不明や再読込みから新しい公開を作らない。元keyの回収は、公開によってtarget Snapshotが変わった後も同じreceiptを返す。受付済みrunは失敗やUNKNOWNの場合も新keyで再公開しない。正のquery完了と公開receiptが揃った場合だけPUBLISHEDとし、commit済みで記帳が揃わない状態とUNKNOWNを区別する。後日のtarget更新を理由に過去のPUBLISHEDを取り消さない。

target claimはShirokumaの協調的なwriter間の競合を防ぐ。最終検査後に外部の直接SQLが書き換えることに対して、ストレージ全体の原子的CASを提供するものではない。

## 検証結果

証拠の保存先は`var/shirokuma/db-p6-3c2b5-2026-10-31/private`である。通常Workbench 8888、native HTTP 8080、再ビルドした実CLI、2つの既存Databaseを使用した。元20テーブルのheadを変えず、専用のMARTS aliasへ公開した。

| 観点 | 確認結果 |
| --- | --- |
| 画面と新規公開 | E本人のDB Aでnative TABLEと付属2 testがPASSED。A本人が画面の候補Snapshot・品質2件・公開先FQN・作成者／公開者・未作成を確認し、監査2行・PUBLISHEDへ遷移。詳細証跡は閉じたdisclosureで保持 |
| API／実CLIと既存target | E本人のDB Bで品質成功後、APIから新規公開。別の新規候補を実CLIのpreview JSONとexpected-target-fileで既存targetへ公開。公開によってSnapshotが変わった後の元key replayも同じreceiptを返し、追加SQLなし |
| 空候補と休止復帰 | 品質2 testがPASSEDの空候補を作成し、LOCAL 0/0から監査で復帰。COUNT=0でFAILED、publish SQLは0件。親への失敗監査記録がrollbackされる不具合を修正し、別の新規候補でcount0／passed=falseの親履歴保存を確認。最初の失敗ID／child証跡を保持し、同じrunの新key公開へ置き換えていない |
| 受付前の拒否 | E公開、viewer preview、別DB preview、旧build-only、descriptor欠落・bool version、受付済みsourceへの新key公開を拒否。逐次の前後比較でdbt record／request・DataOps child／query・admission・WAP publication数が不変 |
| 物理証拠 | 新native候補5件・CREATE5件・品質SELECT10件。成功公開3件はaudit／publish各1 child・各1 query、空監査失敗2件はaudit1 child／queryだけ。SQL AST、全9context、本人・資格情報参照、admission親子、candidate Snapshot、commit marker、CatalogのUUID／PARQUETを独立照合。元Eのbuild／testをA公開で再実行せず、新鮮なMARTS SELECTでA=ALPHA2行／B=BETA2行を確認 |
| 表示・読取り・認可 | direct／reload・保存状態照会・履歴更新・日英・390pxで追加SQLなし、横overflowなし。viewerへ切替後にSQL・run・test・公開を消去。13 WorksheetとSQL下書き、admin・日本語・通常画面幅を維持 |
| 保全と復元 | 141 DB／564 Schema／各141 binding、全legacy rowと全manual-WAP row、元RAW／MARTS20 head、grant・identity・資格情報・password・ACL・OPA guard・runtime profile・ホスト固有YAML・既定storeを保持。新しい未閉鎖admission0、元FENCED_READ_ONLY UNKNOWN2件を保持。通常serverは一時検証project登録を外して復元し、LOCAL2/2・Auto-Chill0分/revision5、doctor healthy・46 checkを確認 |

候補／targetのhead・UUID変更、TABLE／VIEW出現、wake後の最終送信時変更、権限失効、同時key、ACK喪失、実行元停止、commit後計測／親記録失敗、証跡改変は焦点テストで確認した。今回の実機でこれらの全障害を注入したとは扱わない。

受入手順の一時的な失敗もprivate logへ保持した。helperの監査行数の期待値（1に対し実際2）、ClientErrorの属性参照、旧native flagが省略された行の検索を修正した際は、保存済みID／元keyから照合し、正常処理を再実行していない。

焦点検証は修正後のワークフロー90件、DataOps39件、route8件、Python CLI16件、UI66件が成功した。Go、設計・書式検証も成功した。コードと7文書の独立レビューに必須修正は残っていない。

正規の`verify-local-experiment`対象83 test群を段階的に検証した。最初の実行は75群成功後、品質成功でも公開不可とする旧B4期待値1件で停止した。期待値を更新し、native53件と未実行だった残り7群をMakefileと同じ順序で完走した。実行途中に修正したワークフローは、最終版90件を別途完走している。最終版の対象は合計3,306件（成功3,272件・既存skip34件）で、未解消の失敗は0件である。一度のmake実行が無停止で完走した記録とは区別する。

## 後続範囲

通常RunTask／全graph、native VIEW、native SDKによる既存候補の直接置換、manual-WAPの公開履歴／rollbackへのnative統合は別単位である。既存SQL/WAPによるTABLE／EPHEMERALとrun専用SANDBOX INVOKER VIEWを維持する。DB-P6-3全体とM1本人受入は未完了である。

## 2026-10-31 C2C追補

[C2C](database-p6-3c2c-native-graph-2026-10-31.md)で対応するTABLE／EPHEMERAL全graphの通常RunTaskと、保存全generic testの明示TestTaskを実装した。上記のRunTask／全graph残件はB5受入時点の記録である。C2Cのgraph候補は正式公開不可であり、B5の単一native TABLEの公開条件を拡張しない。native VIEW／incremental・並列実行・候補直接置換・manual-WAP rollback統合は後続である。
