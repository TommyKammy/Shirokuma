# Phase 15 レビュー対応と開発引き継ぎ（2026-10-18）

対象は Astra Pro の2026-10-18受入再評価と、レビュー対象コミット `848dc5af`。
所有者の指示で Codex が開発を引き継ぎ、既存ブランチ上で修正した。

| 指摘 | 対応 | 検証 |
|---|---|---|
| R01 認証・認可 | サーバー側の利用者認証と3ロールを Workbench／SQL／Bear に共通適用。共有モードは認証付きTLSバックエンドを必須化 | HTTPの偽装・権限昇格・継続URIの所有者を回帰検証 |
| R02 SQL制限 | 関係名・別名・句の位置を区別。省略AS、LIMIT／WINDOW／FETCH、JOIN条件内の列名を検証 | WorkbenchとSQL入口でRaw・書込みを拒否 |
| R03 AI資格情報 | Pythonの生成・再試行も共通の設定マージを使用 | 実HTTPキャプチャで旧直接キー・旧envキーの未送信を確認 |
| R05 整数キー | 非整数・非有限・型の範囲外を反映前に拒否。UpsertのNULL・既存重複キーも拒否 | 旧表保持、反復取込をSQL実行で確認 |
| R07 日時型 | `timestamp(p)`を変換・型指定・リテラル化で統一。精度超過は拒否 | 実Trino／Iceberg検証を下記に記録 |
| R12 Auto-Chill | 同一ホストの受付を共有ロック、停止を排他ロックで調停 | 実2プロセスで受付先行・停止先行を固定して検証 |

認証の設定は [共有HTTP認証](shared_http_authentication.md)、Trino側の設定は
[TLS・パスワード認証overlay](../../deploy/local-experiment/trino/secure/README.md) を参照。
資格情報はGit管理外に生成し、ロールごとのバックエンド資格情報を利用者へ返さない。

Auto-Chillの調停には全プロセスで同じ `SHIROKUMA_STATE_DIR` を使う。
既存のロックは読み取り専用でも利用でき、活動時刻の保存失敗は警告で継続する。
調停ロック自体を開けない場合は、停止との競合を避けるためSQLを送信する前に拒否する。

## 検証記録

- `make verify`：成功。Python 501件成功、実機専用6件は通常実行ではスキップ。Go・設計整合性・秘密情報検査・ポリシー検査も成功。
- `SHIROKUMA_RUNTIME_TESTS=1` の実機6件：すべて成功。TrinoのTLS認証、平文SQLの403、本人偽装の `PERMISSION_DENIED`、3ロール、Workbench／SQL／Bearの拒否経路とデータ整合性を確認。検証用テーブルは削除済み。
- Chrome：認証済み閲覧者で初期ロールが自動選択され、上位ロールを選べないこと、`SELECT 1`成功、省略ASによるRaw参照の拒否を確認。ローカル証跡は `var/shirokuma/phase16-viewer.png`。
- 既存モデルの実dbtテスト7件成功。既存のローカルQwen接続試験もHTTP 200／`connected`。AI通信へのTrino資格情報混入を防ぐ専用回帰テストも成功。
- `shirokuma doctor`：cluster／policyはhealthy、Flux Kustomizationがsuspendedのため総合degraded。Fluxには変更していない。

実機検証で、軽量Trinoイメージが公式パスワード認証プラグインを省いていたことが判明。
元イメージと同一の検証済みTrino 483配布物からプラグインだけを戻し、secure overlayで固定した。
ソースやJARの改変・バージョン更新は行っていない。
Polarisの削除用権限は旧 `local_experiment` 名前空間にしかなかったため、
`raw`／`sandbox`／`marts`／`staging`へ名前空間単位の `TABLE_WRITE_DATA` を追加した。
再現用ツールは `trino_auth_image.py` と `polaris_permissions.py`。

## 現在のローカル起動設定

専用ラボのTrinoはTLS・パスワード認証・ロール別ACLを有効化済み。
Workbenchはループバックの `http://127.0.0.1:8888/` で稼働し、所有者のローカル管理者モードを維持する。
共有モードの3アカウントは検証時だけ生成して破棄した。実利用者のアカウントはまだ配備していない。
Auto-Chillは0（Off）。既存のAI設定は保持している。

Workbench、SQL endpoint、dbt、取込CLIを再起動するときは、secure overlayに合わせて以下を設定する。
パスは作業ツリーのルートから指定する。資格情報の内容をコピー・Git追加しない。

```sh
export SHIROKUMA_TRINO_AUTH_MODE=password
export SHIROKUMA_TRINO_AUTH_FILE="$PWD/var/shirokuma/trino-auth/backend.json"
export SHIROKUMA_TRINO_CA_FILE="$PWD/var/shirokuma/trino-auth/tls.crt"
rtk proxy python3 tools/local_experiment/workbench_server.py --port 8888
```

Trinoをmanifestから再配備するときもsecure overlayを使用する。
通常のbase manifestは未認証の単独ラボ用であり、共有モードの配備に使わない。

## 受入範囲

この修正は業務データ共有パイロットの最終受入とは区別する。
実利用者のアカウント配備、利用するデータ型を含む空環境への完全復旧、
SQL Server実ソースとPower BI実クライアントの継続運用を確認してから受入を判定する。
既存のR04／R06／R08／R09／R10／R11に関する回帰テストも成功した。
