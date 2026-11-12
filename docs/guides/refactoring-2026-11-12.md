# Shirokuma リファクタリングの実装・完了確認

2026-11-12の分析と後続の差分レビューに基づく、今回の作業範囲を示す。
目的は、同じ修正の反映漏れと変更の波及を減らすこと。行数やprivate関数の数だけを完了条件にしない。

## 実装順と完了条件

| 項目 | 完了条件 | 状態・証拠 |
|---|---|---|
| UIテスト読込・独立機能 | JSの共通読込口を使用し、チャートを固定classic scriptとして分離する | 完了: `7f4b3909` |
| UI非同期処理 | Platform・Readiness・Resourcesで要求の権限・世代・解除判定を共通化する | 完了: `803ab8e9` |
| AI保存・所有者・履歴 | SQL提案・カタログ提案・保存済み計画説明の同一処理を共通化する | 完了: `d0092e65` |
| dbt VIEW・品質テスト | 保存runの準備と品質テスト前後の確認をdbtが所有し、DataOpsが送信・観測を担当する | 完了: `0642a4da`, `60416c0d` |
| dbt継続scope | 名前付きの不変recordと読取・bind APIを使い、DataOps外からraw ContextVarや位置番号を参照しない | 完了: 新規4契約試験・関連回帰459件 |
| native audit・公開 | DataOpsがpublicationの内部recordやdbt固有receiptを解釈せず、送信・commit保持・観測を担当する | 未完了 |
| CSS・基本翻訳辞書 | 固定assetへ分離し、共通テスト読込・両サーバーモードの認証・MIME・表示を維持する | 完了: Workbench回帰1,379件・認証11件、日英・再読込・320/390px |
| Platform収集 | ResourcesとWorkload Baselineで完全に同じbounded subprocess収集を1実装にする | 完了: backend42件・関連UI36件、process blockのAST一致 |
| テスト実行定義 | 同じPython環境での不要な重複選択を除き、環境別の検証意図と対象集合を明示する | 完了: 環境別対象集合・順序を維持し、同一環境の重複1件だけ削除 |

## 維持する契約

- 権限・actor・全context・owner pin、保存済み子操作との照合、scopeの優先順と終了時の復元。
- SQL送信前後の確認順、snapshot・VIEW定義・UUID・公開先の照合、commitと観測失敗の分離。
- 同じ要求の再送防止、取消の保持、識別子のない不確定な結果をUNKNOWNのまま扱う復旧。
- Workbenchの操作・表示・翻訳・初期化順序・深い経路からの再読込。

## 最終確認

各変更の重点試験を先に実行し、最後に以下の証拠を揃える。以前の試験結果は当時の証拠として扱う。

- backend: SQL/WAP・native・graph・公開・取消・復旧の関連回帰。
- frontend: 共通ソース読込、assetの認証・MIME、UI回帰、通常Workbenchの日英・再読込・320/390px表示。
- 実行定義: 整理前後のテスト対象集合とPython環境の照合、および最終`make verify-local-experiment`。
- `make verify-design-context`、差分レビュー、ローカルコミット、残件の再監査。

Go全体の再設計、frontend framework移行、全global変数・全private呼出しの排除、過去資料の大掃除は当初分析で推奨していない。
実行復旧や権限確認の必要な複雑さを機械的に共通化せず、上の完了条件を満たした時点で今回の整理を完了する。
