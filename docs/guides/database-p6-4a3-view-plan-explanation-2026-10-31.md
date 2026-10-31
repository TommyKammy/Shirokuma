---
project: Shirokuma
doc_id: DB-P6-4A3
title: Database VIEW plan acquisition and explanation
status: accepted
created: 2026-10-31
updated: 2026-10-31
version: "1.0"
area: guides
tags: [database, bears, copilot, view, explain, local-ai, acceptance]
---

# DB-P6-4A3: VIEWの実行計画取得と説明

## 対象と利用条件

[A2](database-p6-4a2-plan-explanation-2026-10-31.md)の計画説明を、logical_v1のINVOKER VIEWへ広げる。nativeで作成したVIEWと、互換形式で外部登録したVIEWを同じ検証で扱う。本人・9項目context・選択role・全参照schemaのquery権限・LOCAL AIを必要とし、adminでも他DB参照を許可しない。

元SQLへ固定の`EXPLAIN (TYPE DISTRIBUTED, FORMAT JSON)`を付け、通常のquery受付とWakeを使う。ANALYZE、元SELECTの実行、VIEW定義のSQLへの展開、作成・公開・変更操作は行わない。従来のTABLE／参照なしSELECTとA1のSQL説明を維持する。

DEFINER、未対応のSQL表現、他DB・未許可schema、循環依存、曖昧な保存defaultは受付前に拒否する。未対応VIEWにはSQL説明の導線を残す。legacyのplatform共有credential経路はVIEW計画の対象にしない。

## 依存関係と取得時点の検証

SQLGlotのphysical scopeでCTE名と実体参照を区別し、VIEWごとに保存されたcatalog／namespaceで依存先を解決する。外側worksheetのschemaへ補完しない。保存catalogがない場合も完全修飾参照は扱えるが、必要なdefaultのない1部分名／2部分名は拒否する。

直接・再帰依存は合計64関係、VIEW深度16、定義は各64KiB・全体256KiBに制限する。各子metadataを取得する前に、そのfrontier全体のschema権限を検査する。共有依存を重複解決せず、循環を拒否する。

VIEWのUUID・版・列・INVOKER設定・defaults・定義digest・依存先と、metadata全体＋metadata-locationのdigestをprivate pinへ固定する。版番号を変えないowner property変更も検出する。TABLEはidentityと列定義を確認し、通常のデータappendで変わるTABLE headは固定しない。

既存native VIEW内部の数値`FOR VERSION AS OF`は、指定snapshotの存在と列定義を検証する。対象はTABLEの正の整数snapshot IDに限り、snapshotの列が現在の取得列と異なる場合、timestamp／tag／式、VIEW自体へのversion指定は対象外とする。通常のSQL入力・A1の構文条件は広げない。snapshot情報はprivateな検証に用い、画面・モデルへ内部IDを渡さない。

受付前、Wake／gate通過後の送信直前、EXPLAIN終了時、推論前後に依存closureを再検査する。metadata通信はSQLite write transactionの外で行い、保存時の現在認可はtransaction内で再確認する。取得中の変更で計画を確認できなければ演算子を公開せず、既知のquery終了状態は保持する。正しく取得した後の変更では説明本文を止め、取得済み計画は取得時点の履歴として保持する。

これはREST metadataの前後照合による楽観的な検証であり、VIEW版をEngineへ固定した計画取得ではない。同時の特権catalog変更に対するserializableなsnapshotは保証しない。

## モデル・画面・履歴

API、kind、取消・復旧、SQL未実行フラグ、演算子投影はA2を継続する。直接参照の`metadata.relations`はTABLE／VIEWを区別する。VIEWを含む要求では、直接参照を除いた再帰closureを`metadata.view_dependencies`へ保存する。定数VIEWは空配列である。表示・モデル入力は認可された名前・列名・型だけで、定義SQL・コメント・リテラル・properties・格納先・digestを含めない。匿名演算子と特定TABLEの対応関係を推測して付けない。

VIEWの公開metadataはcompact UTF-8 JSONの`{relations,view_dependencies}`を64KiB以下とする。`captured_at`はこの予算に含めず、既存TABLEのみの履歴の検証条件は変更しない。画面は既存の表示専用disclosureで依存先を案内し、入力変更・遅着・再読込でも未確定な元要求のキーを保持する。

完成済み履歴は現在の本人・同一context・保存済みclosure全体のschema権限で検査する。現在のVIEW定義やTABLE headを取得し直さず、更新・削除後も取得時点のmetadataと安全な演算子を返す。UNKNOWNの照会・復旧でEXPLAINや推論を再実行しない。

## 検証記録

実装前に利用中／旧defaultの全SQLite backup、設定・owner-local YAMLのbytes／mode、28 TABLE headと27既存VIEW headを保存した。検証用の名前を持つVIEWをカタログ管理APIで別途用意し、通常のWorkbench／8888・8080 APIから計画取得・説明を検証した。既存VIEW・実データは変更せず、schema versionは23を維持した。

| 検証層 | 2026-10-31の結果 |
|---|---|
| 全体回帰 | `make verify-local-experiment`は91グループすべてOK、3,719成功・34 skip、終了コード0。実行開始後の修正は、最終ソースのVIEW 59・A2 52・A1 49・query 44、計204件の追加回帰でPASS。件数を独立した総数として合算しない。 |
| 最終画面回帰 | 計画画面36・Bears画面43・日英表示1の計80件がPASS。 |
| 実Engine＋実model | 2DB、admin／data_engineer／bi_viewer、8888／8080で確認。外部登録のネストVIEW、保存default、defaultなし完全修飾、定数VIEW、既存native VIEW内の数値snapshot、休止からの自動Wakeと画面要求の計9件が、固定EXPLAINのFINISHED／COLLECTEDから実ローカルモデルのSUCCEEDEDまで完了。 |
| 拒否・分離 | 他DB、未許可schema、DEFINER、曖昧default、循環を受付前に拒否し、AI／query／推論を増やさない。別actor・異なる9項目contextの照会は404。 |
| 定義変更と取消 | gate待機中のowner property変更を送信前に検出し、`FAILED / AI_METADATA_CHANGED`、POST・DELETE・推論なし。取得済み計画の推論中変更では本文を保存せず、計画を取得時点の履歴として保持。画面からの明示取消はCANCELED／NOT_SUBMITTEDでquery・推論なし。 |
| 履歴・画面 | VIEW更新、削除、再作成後も保存済み本文・metadata・演算子が同一で、Engine／推論を再実行しない。再読込後の元要求照合、Query Doctorの実説明、依存先2件と安全な演算子、元SQL未実行の表示を確認。 |
| 保全・通常運用 | 利用中の旧11,754行、旧defaultの156行は全列一致し、既存UNKNOWN／fenceも不変。28 TABLE／27既存VIEWのhead一致。検証VIEWは正常系6件を保持し、拒否系6件を削除。設定・owner-local YAML 2件はbytes／mode一致。通常両portのSANDBOXは`lake_marts`だけを提示。doctorはhealthy 45・informational 1。 |

実RESTで空propertiesが省略されることを確認し、省略だけを空mapとして扱った。初回は受付前拒否で記録がないことを確認し、保存した元key／bodyで復旧した。nullや不正型は引き続き拒否する。

送信前の定義変更検証では、内部停止処理が利用者の取消フラグを立て、原因をCANCELEDへ置き換える問題も修正した。内部cleanupと明示取消を区別し、最初の失敗原因を保存する。修正前の終了記録を変更せず、別名の検証要求で修正後のFAILEDと送信なしを確認した。停止結果が不確定な場合はUNKNOWNを維持する。

推論中変更と最初の画面完了には制御した応答を2件使用し、実modelの9件と区別した。11件すべての送信内容を検査し、VIEWの定義・値・コメント・内部snapshot ID等の非公開情報を含めないことを確認した。検証中継を停止し、AI設定を元のbytesへ復元した。private証跡は`var/shirokuma/db-p6-4a3-2026-10-31/private/`、最終画面は同配下の`ui-view-plan-explanation-viewport.jpg`へ保存した。

## 参照と残件

保存defaultとINVOKER判定は[Trino 483 REST catalog](https://raw.githubusercontent.com/trinodb/trino/483/plugin/trino-iceberg/src/main/java/io/trino/plugin/iceberg/catalog/rest/TrinoRestCatalog.java)および[VIEW Session](https://raw.githubusercontent.com/trinodb/trino/483/core/trino-main/src/main/java/io/trino/Session.java)に合わせる。元VIEWへの計画取得を保ち、[VIEW型変換・row filter／column mask](https://raw.githubusercontent.com/trinodb/trino/483/core/trino-main/src/main/java/io/trino/sql/planner/RelationPlanner.java)を省略する近似的なSQL展開は行わない。

DEFINER、異なるhistorical列定義、version tag／timestamp、最新query指定、取得済み計画だけを使う新しい説明、他Bears／SQL案とWAP連携は後続である。DB-P6-4全体、P6-7の縦断検証、M1本人受入は継続する。
