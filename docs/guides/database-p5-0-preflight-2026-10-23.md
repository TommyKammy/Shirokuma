# P5-0 前提確認・契約固定報告

2026年10月23日。[Phase 5計画](database-phase5-implementation-plan-2026-10-23.md)のP5-0を対象とする。
公開API・永続化・委譲・runtime引継ぎ・停止点は[P5-0契約](database-p5-0-contract-2026-10-23.md)と
[ADR-0037](../design/07_ADR/ADR-0037_Use_Bounded_Database_Creation_Delegation_and_Runtime_Handoff.md)に固定した。
P5-1以降の製品実装、migration適用、WebUI作成・READY化の実機受入は未実施。

## 読取り確認

2026-10-23 06:20 UTCの環境snapshotと、06:25:19 UTCの実行閉鎖照合。
private evidence IDは`p50-environment-20261023T062027Z`。元設定の秘密値やworkstation絶対pathを公開報告へ含めない。
通常HTTPのGET、明示した専用Kubernetes/Docker contextの読取り、SQLite `mode=ro`を使用した。
product Storeを開いてmigrationすること、SQLの新規送信、起動・port forward・再配備・認証変更は行っていない。

| 対象 | 今回確認した事実 | P5に向けた判定 |
| - | - | - |
| 既存の通常入口 | Workbench/nativeのruntime GETがともに200。同じ`p49-final-persisted`、同じprofile_ref | 起動中の両入口の世代一致を確認。今回のGETをSQL実行・P5引継ぎ成功の証拠にはしない |
| 既存control store | schema 18、66 tables、warehouse OPEN revision155。READY 5 DB、retired 2 DB、REGISTERED 0、未適用grant 0 | 現行台帳を保持して次の追加migrationを設計可能。P5の受付・承認・停止表はまだ存在しない |
| 受理済み実行 | Job/Query/admissionの未閉鎖はいずれも0 | 現時点の未閉鎖なし。実apply時は改めて既存coreのdrain/閉鎖判定を使う |
| 過去UNKNOWN | 結果UNKNOWNのままFENCED_READ_ONLYで実行閉鎖済み3件。run_id未連結UNKNOWN予定予約1件 | 成功へ変えず別計上。再送・消去せず、UNKNOWN行数だけで新規applyを判定しない |
| 既存専用cluster | 明示contextのnode UIDを既存証拠と照合、Ready。24 Pod中17 Running/Ready、7 Succeeded | 読取り時点の基本状態を確認。全storage機能・実provider変更の試験とは区別する |
| 認証・profile参照 | 元4 account、既存profileの参照先存在を確認 | 旧運用の前提を保持。内容の妥当性、2要求者＋独立executorのP5委譲は後続で実検証する |
| P5実行能力 | 現sourceはREQUESTEDのhandoff保存まで。新bridge/委譲/managed-child handoffは未実装 | P5の自動作成能力は未充足。既存環境が健全でもP5 capabilityを有効と表示しない |
| 旧専用clean | P4-9の同一containerはexited。4 volumeとnetworkを保持。両通常入口は閉、API到達不可 | 停止中の使用済み証拠環境。起動後健全性は未確認。P5のfresh cleanとして再利用しない |

## 保持対象と後続の前提

既存のcontrol store、DB/binding、consumerとcredential、Table UUID/Snapshot/型/行、grant tombstone、
retired値、回復履歴、予定、元認証・client設定・元SQLタブを保持する。
今回のreadbackはP4-9の全物理データ照合を再実施したものではない。P5-6で新しいbefore/afterを採取する。
P5-0の変更対象は契約・ADR・本報告・計画の参照追記に限定する。

| 後続gate | 満たす条件 |
| - | - |
| P5-1着手 | schema18の共有transaction helperと追加migration19を実装し、旧residentとの混在起動を避ける配備手順を守る。今回live DBをmigrationしない |
| 既存環境での新dispatch | server-owned enrollment、固定private targets、現在のrequester/executor認証、mandatory P5 guard、受付停止・撤回、managed ownersが実装・設定・検査済み |
| fresh cleanでの実行 | 別の空の専用foundationを通常手順で用意する。固有node/API/CA/auth/state/volumes、既存と異なるDB/consumer ID、独立した両ownerを検査する |
| clean隔離 | primaryやP4-9保持volumeを流用しない。global kubeconfig切替をせず、明示context/固定接続先のみ。foundation不足は明示停止しprimaryへfallbackしない |
| 実機受入 | P5-A01〜19を新しい証拠で実施。技術PASS後にP5-A20の所有者本人による受入を行う |

旧cleanの停止は不具合とは判定しない。今回その起動やfresh環境作成を行わず、必要な新規foundationを後続準備として明記した。
P5-0の完了はこの前提と契約の固定であり、両環境でのP5実装・実機受入完了を意味しない。

## 文書・既存検査

既存venvの`make verify-local-experiment verify-design-context`は69 groups、1,925 tests中1,892 PASS/33 skip。
新ADRをcontext manifestへ登録後の設計検査もPASS。公開文書の相対link・JSON例・書式・秘密材料markerを確認した。
最初の検査呼出しはツールの300秒上限で結果取得不能となったため、残存検査processなしを確認し、
結果を保持できる実行経路で再実施した。これは製品試験の失敗や実機applyの再送ではない。

## 証拠取扱いの訂正

補助抽出で、既存private proofの証明書・private keyが会話内ツール結果へ混入した。
また、新規Pod証拠へ秘密値を含み得る環境変数値を一時保存したため、その新規artifactを識別子allowlistへ置換した。
公開成果物へ値を転載せず、以後は件数・識別子・一致判定だけを採取した。元proof/authは変更していない。
公開、commit、任意の外部宛送信は行っていない。会話内ツール出力への開示があった事実は残す。
