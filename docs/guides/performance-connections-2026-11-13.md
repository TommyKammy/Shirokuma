# 性能試験の準備: 実Trino・公開API接続と全結果照合

2026-11-13〜14 JST。[試験計画](performance-comparison-plan-2026-11-13.md)のP1として、共通runnerを実Trinoと通常の公開SQL APIへ接続した。[利用手順](../../benchmarks/local_perf/README.md#public-api-and-portable-connection-qualification)に再実行入口を記載する。今回は読み取り専用の小規模接続qualificationであり、G1全体の受入や大規模性能の合否は継続する。

## 接続と結果の契約

- 直接経路は、現在のlogical engineへ所有するloopback HTTPS tunnelを開き、CA・hostname検証とACTIVEなDB専用実行ユーザーを使用する。旧profileのSQL URLをlogical engineと混同しない。benchmarkはoperatorの秘密情報を自動検索せず、明示された0600認証ファイルだけを読む。
- 公開経路は、通常のBasic認証・選択ロールでDB／schemaを取得し、ID・SQL名・権限・binding／generation／grant revisionの9項目を固定する。全pageでcontext・request／worksheet ID・backend ID・schema・状態を確認する。
- POST前にSQL・mode・request／worksheet IDを永続化する。応答喪失時は履歴の完全一致が1件の場合だけ既存queryを回収し、取消・終端を確認する。0件・複数件はUNKNOWNとして後続投入を止め、POSTは再送しない。結果未取得の試行を成功へ変更しない。
- 同じinline VALUES／SQLをDuckDB参照・実Trino・公開APIで実行し、全値・型・順序・重複と終端footerを照合する。SQL-only参照はtable登録を伴わず、架空の`dataset.json`を作らない。

移植可能な[通常9 query](../../benchmarks/local_perf/examples/connection-qualification.json)と[10,001行の打切り確認](../../benchmarks/local_perf/examples/overflow-qualification.json)を同梱した。列情報到着前の正常な空pageも読み進め、確定後のschema変更・重複page・別queryのURL・truncationは拒否する。公開経路の上限10,000行は維持する。

## 実機で検出した精度問題

最初の全値照合で、`TIMESTAMP(6)`の`.123456`が`.123`へ丸められることを検出した。Trinoのクライアントcapabilityが未指定だったため、直接adapterと公開serviceのbackend接続へ`PARAMETRIC_DATETIME`を追加した。[Trino protocol](https://trino.io/docs/current/develop/client-protocol.html)に沿う修正で、認可・context・SQL本文は維持する。DataOps／EXPLAINも固定済みheaderを引き継ぐ。

公開Workbenchだけを同一profile・起動条件で再起動し、精度保持を確認した。再起動前に、現在のprincipal／adminから可視な148DBの履歴で、closureなしの非終端queryが0件であることを確認した。既存履歴を保持し、VM・資源profile・manifestは変更していない。

## 検証証拠

| 検証 | 結果 |
|---|---|
| 重点回帰 | Python 3.9.6／3.12.12で各70件すべて成功。模擬公開HTTPとの実通信を含む9件もskipなし |
| 公開serviceの送信契約 | Python 3.9で対象8件成功。capability・精度・DataOpsの固定header再利用を確認 |
| 通常9 query | DuckDB 1.4.3参照に対し、Trino 483直接／公開APIの両経路で各9件・4,111行の全結果が一致。整数・22桁Decimal・NULL・空文字・日付・timestamp・集約・join・window・空結果を含む |
| 実page分割 | 4,096行のwide結果で両経路ともdata pageが3件。全page取得と型・順序を照合 |
| 接続先・公開selector | 両経路の実行ユーザー・catalog・schema・engine versionが一致。公開CLIのDB／schema選択結果も固定contextと一致 |
| 公開10,001行 | `PUBLIC_TRUNCATED`、試行はFAILED、backendはCANCELED。全件完了・性能成功へ加算しない |
| POST応答喪失 | 実際の受付後に応答だけを失う試験で、送信1回のまま公開履歴から特定してCANCELED。試行はFAILEDのまま保持 |
| ロール分離 | 同じaccountでviewerロールを選び、admin queryの取得は409 `CONTEXT_MISMATCH`。SQL・context・query情報を返さない |
| ローカル診断 | `shirokuma doctor --profile local-experiment`は46 checksでhealthy。既存profileの診断であり、logical SQLの接続証明は上記の実queryと分ける |
| 全体回帰 | `make verify-local-experiment PYTHON=tools/local_experiment/dataops/.venv/bin/python3`を単一実行で完走。145群・5,456件成功・既定34件skip・失敗0件 |
| 設計・文書 | `make verify-design-context`と改行・末尾空白・secret filenameの確認が成功 |

Git外の採用証拠は`shirokuma-perf-live-2026-11-13/campaign-3af359b4`。通常run・型付き全結果・SQL snapshot・試行intent・page観測・意図した失敗のauditを保持した。試行中の精度不一致、SQL方言差、検証scriptの期待HTTP形式を修正した履歴もFAILEDのまま残し、最終qualificationと区別する。一時認証コピーと所有tunnelは終了時に解放した。

## 次の推奨順

1. 生成済みTPC-Hを隔離したIcebergへ登録し、snapshotと同じ8表を固定する。22 SQLの方言・型・ORDER BY／同順位契約を整理し、参照と両経路の全結果を照合する。
2. TPC-DS全variantの実行・参照対応、公開APIの10,000行を超える結果取得、送信・queue・first／last row等の計測境界を整える。
3. G1の残件を確認してL1適用・guest拡張・heap／spill検証へ進み、その後に100GB以上、同時実行、継続試験を行う。

今回のinlineデータは同じ永続Iceberg snapshotの検証を代替しない。資源上限は追加物理占有2TB・host予備256GiB、現在のVM16 vCPU／96GiB／400GiBを維持した。Snowflake／Databricksへの直接接続・費用発生はない。
