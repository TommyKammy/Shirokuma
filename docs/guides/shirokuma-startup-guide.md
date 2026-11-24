---
project: Shirokuma
doc_id: SHIROKUMA-STARTUP-GUIDE
title: Shirokuma startup, daily operation and rebuild guide
status: accepted
created: 2026-11-24
updated: 2026-11-24
version: "1.0"
area: guides
tags: [startup, logical, foundation, workbench, operations]
---

# Shirokuma 起動ガイド

このホスト（Mac Studio、Colima `mac-studio-solo`）で Shirokuma の logical_v1 インスタンスを
起動・停止・確認し、必要なら作り直すための手順です。クリーンなクラスタからの詳細な構築手順は
[logical_v1 起動ガイド](logical-local-bootstrap-2026-11-23.md) にあり、この文書はそこから
日常運用に必要な部分を抜き出して補ったものです。

コマンドはリポジトリのルート（`main`）で実行します。秘密値は画面やログに出さないでください。

## 1. 構成とバージョン

| 役割 | コンポーネント | 版 |
|---|---|---|
| オブジェクトストレージ | SeaweedFS（公式 arm64 イメージ） | 4.55 |
| カタログ | Apache Polaris（公式イメージ）＋ PostgreSQL（Chainguard） | 1.9.0 |
| テーブル形式 | Apache Iceberg（Polaris に同梱） | 1.12.0 |
| クエリエンジン | Trino（coordinator ＋ worker 1台） | 484 |
| 変換 | dbt-core ／ dbt-trino（`tools/local_experiment/dataops`） | 1.12.7 ／ 1.12.0 |
| 認可 | OPA（Trino のアクセス制御） | foundation の pin |
| 画面・API | Workbench `http://127.0.0.1:8888`、SQL endpoint `http://127.0.0.1:8080` | リポジトリの版 |

イメージはすべて digest で固定されています。SeaweedFS 以外は
`tools/local_experiment/logical_foundation.py` の `prepare` が `deploy/` の manifest と
Trino の pin から読み取り、SeaweedFS は `prepare` の `--seaweed-image` で渡します。

セキュリティ面の既定:

- SeaweedFS の filer IAM gRPC は `WEED_JWT_FILER_SIGNING_KEY` で認証付きです。
- namespace 内の Pod から SeaweedFS へは S3（8333）だけが届きます。
- Workbench と SQL endpoint は `127.0.0.1` だけで待ち受け、Basic 認証が必要です。

インスタンスの秘密と状態は Git 管理外の `var/shirokuma/local-YYYYMMDD/` に置きます
（ディレクトリ 0700、ファイル 0600）。

## 2. 日常の起動と停止

### 2.1 Colima の起動（ホスト再起動後など）

```sh
colima start mac-studio-solo
kubectl --context colima-mac-studio-solo -n shirokuma-logical get pods
```

すべての Pod が `Running`（bootstrap Job は `Completed`）になるまで待ちます。

### 2.2 Workbench と SQL endpoint

```sh
tools/local_experiment/instance.sh start     # 起動（起動済みなら何もしない）
tools/local_experiment/instance.sh status    # 状態
tools/local_experiment/instance.sh stop      # 停止（SIGTERM。port-forward も片付く）
```

対象のインスタンスは、第2引数、`SHIROKUMA_INSTANCE_DIR`、`var/shirokuma/local-*` の
最新の順に決まります。ログは `<インスタンス>/logs/workbench.log` と `endpoint.log` です。

### 2.3 ログイン

ブラウザで `http://127.0.0.1:8888/#home` を開くと Basic 認証を求められます。
ユーザー名は `admin`、パスワードは `<インスタンス>/runtime/client.json` の `password` です。
画面に出さずにクリップボードへ写すには次のようにします。

```sh
python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["password"], end="")' \
  var/shirokuma/local-20261124/runtime/client.json | pbcopy
```

## 3. 状態の確認

```sh
DIR="$PWD/var/shirokuma/local-20261124"
PATH="$PWD/tools/local_experiment/dataops/.venv/bin:$PATH" python3 - "$DIR" <<'PY'
import base64, json, sys
from urllib.request import Request, urlopen
client = json.load(open(sys.argv[1] + '/runtime/client.json'))
auth = 'Basic ' + base64.b64encode(f"{client['username']}:{client['password']}".encode()).decode()
print(json.load(urlopen(Request('http://127.0.0.1:8888/api/status', headers={'Authorization': auth}), timeout=30)))
PY
```

`status=healthy`、warehouse が `state=active` / `replicas=2` / `ready_replicas=2` なら正常です。
Trino 本体の版とノードは [logical_v1 起動ガイド §9.1](logical-local-bootstrap-2026-11-23.md#91-trino-484-と-node)
の手順で確認します（`484`、coordinator と worker が `active`）。

## 4. インスタンスの作り直し

既存インスタンスを捨てて、リポジトリの現在の版で新しく立ち上げる手順です。
**DB とデータはすべて消えます。**

### 4.1 撤去

```sh
OLD="$PWD/var/shirokuma/local-20261124"         # 撤去するインスタンス
OLD_FOUNDATION_ID="foundation-20261124"
tools/local_experiment/instance.sh stop "$OLD"
kubectl --context colima-mac-studio-solo delete namespace shirokuma-logical --ignore-not-found --wait=true
kubectl --context colima-mac-studio-solo delete \
  validatingadmissionpolicy,validatingadmissionpolicybinding \
  -l shirokuma.dev/logical-foundation="$OLD_FOUNDATION_ID" --ignore-not-found
kubectl --context colima-mac-studio-solo get pv -o json \
  | python3 -c 'import json,sys; print([p["metadata"]["name"] for p in json.load(sys.stdin)["items"] if p.get("spec",{}).get("claimRef",{}).get("namespace")=="shirokuma-logical"])'
rm -rf "$OLD"
```

PV の一覧が `[]` であることを確認してから `rm -rf` します。

### 4.2 新規構築

日付で新しい ID を決めます。

```sh
export ROOT="$PWD"
export DIR="$ROOT/var/shirokuma/local-$(date +%Y%m%d)"
export FOUNDATION_ID="foundation-$(date +%Y%m%d)" REVISION=1
export ENVIRONMENT_ID="local-$(date +%Y%m%d)" SHIROKUMA_LOGIN_USER=admin
mkdir -p "$DIR"/{runtime/credentials,state,logs,run}
chmod 700 "$DIR" "$DIR"/{runtime,runtime/credentials,state,logs,run}
(cd tools/local_experiment/dataops && uv sync --frozen)
```

次に、[logical_v1 起動ガイド](logical-local-bootstrap-2026-11-23.md) の順に進めます。

1. イメージが k3s にあるか確認します（§1）。`prepare` は pull しません。
2. logical-secrets jar をビルドします（§3）。
3. `prepare` を実行します（§4）。`--foundation-id "$FOUNDATION_ID"`、
   `--environment-id "$ENVIRONMENT_ID"`、`--seaweed-image` は SeaweedFS 4.55 の
   **タグなし** arm64 digest にします。
4. `apply` を実行します（§5）。`--settings` は **絶対パス**（`"$DIR/foundation/settings.json"`）で渡します。
5. runtime profile・accounts・client・platform-admin を作ります（§6）。
   §6 は上で決めた `FOUNDATION_ID` / `REVISION` / `ENVIRONMENT_ID` を引き継ぎます。
   別の値に変えると §7 の登録が `Logical foundation differs` で失敗します。
6. Workbench と endpoint を止めた状態で `register-foundation` を実行します（§7）。
   `{"registered": true}` が返ればよいです。

その後、起動して最初の DB を作ります。

```sh
tools/local_experiment/instance.sh start
```

最初の DB は Workbench の管理 API（[§9.4](logical-local-bootstrap-2026-11-23.md#94-最初の-db-作成)）か
画面から作ります。2026-11-24 の構築では `smoke_20261124` を作りました。

### 4.3 構築後の確認

- `/api/status` が healthy（§3）。
- Trino が `484`、ノード2台が `active`。
- Polaris の Pod に `polaris-core-1.9.0` と `iceberg-core-1.12.0` が入っている。

  ```sh
  kubectl --context colima-mac-studio-solo -n shirokuma-logical exec deploy/logical-polaris -- \
    sh -c 'ls /deployments/lib/main | grep -E "polaris-core-|iceberg-core-"'
  ```

- SeaweedFS のログに `4.55` と `admin Bearer token required` が出ている。

  ```sh
  kubectl --context colima-mac-studio-solo -n shirokuma-logical logs deploy/logical-seaweed \
    | grep -E "Start Seaweed S3 API Server|IAM gRPC"
  ```

- 新しい DB で `CREATE TABLE … AS SELECT`・`INSERT`・`SELECT` が通り、
  dbt の compile（`compiler_versions` が dbt 1.12.7 / dbt_trino 1.12.0）と run が通る。

## 5. 既存インスタンスのイメージ更新

DB を残したまま Polaris や SeaweedFS などのイメージだけを上げる手順です。

1. `tools/local_experiment/instance.sh stop`
2. `<インスタンス>/foundation/settings.json` と `state/jobs.sqlite3`（`sqlite3 … ".backup …"`）をバックアップする。
3. `settings.json` の `images.<名前>` を新しい digest に書き換える（SeaweedFS はタグなし）。
4. `logical_foundation.py apply --settings <絶対パス> --apply`
5. `tools/local_experiment/instance.sh start`

`logical_creations.py refresh-verification` は Trino（compute）イメージと SPI の変更専用で、
Polaris・SeaweedFS・PostgreSQL の変更は `FOUNDATION_BINDING_MISMATCH` で拒否します。
これらは手順 5 の起動だけで足ります。`apply` は bootstrap Job を再実行しないので、
Polaris の DB スキーマの版が新しい版の要求と合っているかは事前に確認してください
（Polaris 1.9.0 はスキーマ v6 が必要）。SeaweedFS 4.54 以降は filer のメタデータ形式が変わるため、
同じ PVC のまま古い版へ戻せる保証はありません。

## 6. トラブルシューティング

| 症状 | 原因と対処 |
|---|---|
| ブラウザで 401 | Workbench は Basic 認証が必要。§2.3 のユーザー名とパスワードでログインする。 |
| `apply` が `INVALID_FOUNDATION_SETTINGS` | `--settings` を相対パスで渡していないか。絶対パスにする。SeaweedFS の image がタグ付き（`:4.55@sha256:…`）でも同じエラーになる。 |
| `refresh-verification` が `FOUNDATION_BINDING_MISMATCH` | Trino 以外のイメージ変更では使わない。`instance.sh start` だけでよい（§5）。 |
| `PLATFORM_ADMIN_MISMATCH` | `runtime/credentials/platform-admin.json` を `foundation/provisioner.json` と一致させる。 |
| SeaweedFS 起動直後のログに `Not current leader` | master のリーダー選出待ち。十数秒で `becomes leader` が出れば正常。 |
| 8080 が `UNKNOWN_CONTEXT` | `X-Trino-Catalog` と `X-Trino-Schema` の両方を、登録済み DB のものにする。 |
| dbt CLI が `request_uncertain` | `server_error` にサーバーのコードが入る。同じ idempotency key での再送は安全。 |

## 参照

- [logical_v1 起動ガイド](logical-local-bootstrap-2026-11-23.md)（構築手順の詳細）
- `tools/local_experiment/instance.sh`（起動・停止・状態）
- `tools/local_experiment/logical_foundation.py`（`prepare` / `apply` / render）
- `tools/local_experiment/logical_creations.py`（`register-foundation` / `refresh-verification`）
