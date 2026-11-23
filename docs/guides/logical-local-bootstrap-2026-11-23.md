---
project: Shirokuma
doc_id: DB-LOGICAL-LOCAL-BOOTSTRAP
title: Clean-cluster bootstrap of a local logical_v1 instance
status: accepted
created: 2026-11-23
updated: 2026-11-23
version: "1.0"
area: guides
tags: [database, logical, foundation, trino, workbench, bootstrap]
---

# クリーンなクラスタからの logical_v1 起動

この guide は、Shirokuma の namespace・PV・admission policy・Flux オブジェクトが
存在しない状態から、logical_v1 の foundation（Trino 484）と Workbench
（`127.0.0.1:8888`）、SQL endpoint（`127.0.0.1:8080`）を起動する手順を示す。
旧 `make lab-up` / `shirokuma-dev` の Flux・Bucket・managed owner 経路は使わない。

作業ツリーは `main`、kubectl context は `colima-mac-studio-solo` を前提とする。
秘密値はコマンド出力へ表示しない。

## 1. 前提

- Colima の `mac-studio-solo` が起動し、kubectl が使えること。

```sh
kubectl --context colima-mac-studio-solo get nodes
```

- リポジトリと Python 環境。

```sh
cd /Users/jp.infra/Dev/Shirokuma/main
cd tools/local_experiment/dataops
uv sync --frozen
cd -
```

- 使用する image が k3s の image store にあること。`prepare` は image を pull しない。

```sh
colima -p mac-studio-solo ssh -- sudo k3s crictl images \
  | grep -E 'trinodb/trino|chrislusf/seaweedfs|chainguard/postgres|apache/polaris|openpolicyagent/opa'
```

Trino は `logical_foundation.py:296` の pin
（`docker.io/trinodb/trino@sha256:62dc5ad8...`）を使う。SeaweedFS は
**タグなしの digest**（`docker.io/chrislusf/seaweedfs@sha256:...`）を使う。
`:4.51@sha256:...` のようなタグ付き参照は、foundation の admission policy が
拒否するため `prepare` の時点で `INVALID_FOUNDATION_SETTINGS` になる。

## 2. 作業ディレクトリ

`var/` は Git 管理外である。作業ディレクトリは 0700、秘密ファイルは 0600 にする。

```sh
ROOT="$PWD"
DIR="$ROOT/var/shirokuma/local-20261123"
FOUNDATION_ID="foundation-20261123"
REVISION=1
ENVIRONMENT_ID="local-20261123"
mkdir -p "$DIR"/{runtime/credentials,state,logs}
chmod 700 "$DIR" "$DIR"/{runtime,runtime/credentials,state,logs}
```

以降のコマンドは同じシェルで実行し、`$ROOT` / `$DIR` / `$FOUNDATION_ID` を引き継ぐ。

## 3. logical-secrets jar のビルド

`logical_secrets/build.py` は pinned Trino 484 image の中で javac を実行し、
SPI 451 の jar を 0600 で出力する（`build.py:13`、`:62`）。

```sh
PATH="$ROOT/tools/local_experiment/dataops/.venv/bin:$PATH" python3 - <<'PY'
import os
import sys
from pathlib import Path
sys.path.insert(0, 'tools/local_experiment')
from logical_secrets import build

output = Path(os.environ['DIR']) / 'logical-secrets.jar'
build.build(output)
print(output)
PY
```

## 4. foundation の prepare

`logical_foundation.py:270` の `prepare` は、kubeconfig の API server/CA と
node UID を取得して private な設定・秘密材料・manifest を生成する。クラスタには
まだ適用しない。

```sh
PATH="$ROOT/tools/local_experiment/dataops/.venv/bin:$PATH" python3 \
  tools/local_experiment/logical_foundation.py prepare \
  --private-dir "$DIR/foundation" \
  --kubeconfig "$HOME/.kube/config" \
  --context colima-mac-studio-solo \
  --node-name colima-mac-studio-solo \
  --foundation-id "$FOUNDATION_ID" \
  --revision "$REVISION" \
  --environment-id "$ENVIRONMENT_ID" \
  --warehouse-id LOCAL \
  --seaweed-image 'docker.io/chrislusf/seaweedfs@sha256:7e68a388de0806ed8d96f30cb7e927182a3d80d42920cb87d97d23311a7a1a77' \
  --jar "$DIR/logical-secrets.jar" \
  --namespace shirokuma-logical \
  --workers 1
```

- `--warehouse-id LOCAL`: Workbench のクエリ context は `warehouse_id=LOCAL` を
  要求する（`workbench_server.py` の query context 検証）。
- `--workers 1`: coordinator に加えて worker を 1 台作る。
  `include_coordinator` は既定で true なので、`system.runtime.nodes` は 2 node になる。
- `--seaweed-image` はタグなし digest にする。`prepare` の検証は
  `logical_foundation.py:192` で untagged のみ許可する。

成功すると `FOUNDATION_DOCUMENTS_READY` が表示され、`foundation/settings.json`、
`foundation/provisioner.json`、`foundation/trino-ca.pem` などが 0600 でできる。

## 5. foundation の apply

```sh
PATH="$ROOT/tools/local_experiment/dataops/.venv/bin:$PATH" python3 \
  tools/local_experiment/logical_foundation.py apply \
  --settings "$DIR/foundation/settings.json" \
  --apply
```

`apply` は namespace、cluster-scoped admission policy、PVC、Service、
SeaweedFS/Postgres/Polaris、bootstrap Job、Trino coordinator/worker、OPA を作成し、
Deployment の rollout と Job の完了を待つ（`logical_foundation.py:603`）。

成功時の NEXT 表示は、新規 foundation なら `register-foundation`、既存 foundation の
image/SPI 更新なら `refresh-verification` を案内する。新規構築では次節の登録へ進む。

## 6. runtime profile・logical-config・accounts・client・platform-admin

`runtime_profile.py:144` の `RuntimeProfile` と `:217` の `logical_config` が
要求する exact schema に合わせる。すべて絶対パス・0600、ディレクトリは 0700。
以下のコードは値を表示しない。`SHIROKUMA_LOGIN_USER` は任意のログイン名
（既定 `admin`）に置き換えてよい。

```sh
export DIR="$PWD/var/shirokuma/local-20261123"
export FOUNDATION_ID="foundation-20261123"
export REVISION=1
export ENVIRONMENT_ID="local-20261123"
export SHIROKUMA_LOGIN_USER="admin"
PATH="$PWD/tools/local_experiment/dataops/.venv/bin:$PATH" python3 - <<'PY'
import hashlib, json, os, secrets
from pathlib import Path

root = Path(os.environ['DIR'])
foundation = root / 'foundation'
runtime = root / 'runtime'
credentials = runtime / 'credentials'
state = root / 'state'
foundation_id = os.environ['FOUNDATION_ID']
environment_id = os.environ['ENVIRONMENT_ID']
login = os.environ['SHIROKUMA_LOGIN_USER']

def write_private(path, value):
    data = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)

# Workbench の UI ログイン。PBKDF2-HMAC-SHA256/600000。
password = secrets.token_urlsafe(24)
salt = secrets.token_bytes(16)
digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600_000, dklen=32)
write_private(runtime / 'accounts.json', {
    login: {'role': 'admin', 'platform_admin': True,
            'salt': salt.hex(), 'password_hash': digest.hex()}})
write_private(runtime / 'client.json', {
    'username': login, 'password': password, 'selected_role': 'admin'})

# platform-admin は foundation の provisioner と同一 identity にする。
# ここが異なると DB 作成の attach が 401 になるため、起動時に
# PLATFORM_ADMIN_MISMATCH で fail fast する。
provisioner = json.loads((foundation / 'provisioner.json').read_text())
write_private(credentials / 'platform-admin.json', {
    'version': 1,
    'username': provisioner['username'],
    'password': provisioner['password']})

write_private(runtime / 'logical-config.json', {
    'version': 1,
    'environment_id': environment_id,
    'warehouse_id': 'LOCAL',
    'foundation_generation_id': foundation_id,
    'foundation_revision': int(os.environ['REVISION']),
    'credentials_dir': str(credentials),
    'platform_admin_credential_ref': 'platform-admin',
    'provider_config_file': str(foundation / 'settings.json'),
    'executor_id': login})

write_private(runtime / 'runtime-profile.json', {
    'version': 2,
    'mode': 'logical_v1',
    'state_dir': str(state),
    'auth_file': str(runtime / 'accounts.json'),
    'trino_ca_file': str(foundation / 'trino-ca.pem'),
    'client_auth_file': str(runtime / 'client.json'),
    'trino_auth_file': '',
    'catalog_auth_file': '',
    'sql_endpoint_url': 'http://127.0.0.1:8080',
    'logical_config_file': str(runtime / 'logical-config.json')})
PY
```

`platform-admin.json` の `username`/`password` は `foundation/provisioner.json`
（`logical_provisioner`）からコピーする。`logical_creations.py` の
`_require_platform_admin` は、この 2 つが一致しなければ
`PLATFORM_ADMIN_MISMATCH`（503）で起動を止める。

## 7. foundation の登録

Workbench と 8080 endpoint を停止した状態で、正規経路
`logical_creations.lifetime(register=True)` を CLI から呼ぶ
（`logical_creations.py:112`、`:596`）。

```sh
export SHIROKUMA_RUNTIME_PROFILE="$DIR/runtime/runtime-profile.json"
PATH="$PWD/tools/local_experiment/dataops/.venv/bin:$PATH" python3 \
  tools/local_experiment/logical_creations.py register-foundation \
  --confirm-workbench-stopped
```

成功時は次の形の JSON を表示する（値は環境依存）。

```json
{"generation_id": "foundation-...", "registered": true, "revision": 1}
```

`register_foundation` は control store が空であること、foundation probe が
storage/polaris/trino/runtime_files/spi/opa のすべてに ready であることを要求する。
既に登録済みの foundation に対して image/SPI だけを更新した場合は、登録ではなく
`refresh-verification --confirm-workbench-stopped` を使う。

## 8. Workbench と 8080 endpoint の起動・停止

```sh
export SHIROKUMA_RUNTIME_PROFILE="$DIR/runtime/runtime-profile.json"
export KUBE_CONTEXT="colima-mac-studio-solo"
export KUBE_NAMESPACE="shirokuma-logical"
export PATH="$PWD/tools/local_experiment/dataops/.venv/bin:$PATH"
mkdir -p "$DIR"/{run,logs}

nohup python3 tools/local_experiment/workbench_server.py \
  --host 127.0.0.1 --port 8888 >"$DIR/logs/workbench.log" 2>&1 &
echo $! >"$DIR/run/workbench.pid"

nohup python3 tools/local_experiment/warehouse_resident.py endpoint \
  --bind 127.0.0.1 --port 8080 >"$DIR/logs/endpoint.log" 2>&1 &
echo $! >"$DIR/run/endpoint.pid"
```

Workbench は `workbench_server.py:865`、endpoint は
`warehouse_resident.py:1984` の main で `configure_process()` を呼び、
logical profile が有効なら `logical_creations.lifetime()` を所有する。

停止は SIGTERM を使う。SIGTERM は所有する port-forward も cleanup する。

```sh
kill -TERM "$(cat "$DIR/run/workbench.pid")" "$(cat "$DIR/run/endpoint.pid")"
```

## 9. 検証

### 9.1 Trino 484 と node

Trino の Service へ一時的に port-forward し、CA を指定して `/v1/info` を確認する。
確認後は必ず port-forward を止める。

```sh
kubectl --context colima-mac-studio-solo -n shirokuma-logical \
  port-forward svc/trino-local 18443:8080 --address 127.0.0.1 &
PF=$!
sleep 1
curl --cacert "$DIR/foundation/trino-ca.pem" -s https://127.0.0.1:18443/v1/info
kill "$PF"
```

`nodeVersion.version` が `484`、`coordinator` が `true`、`starting` が `false` ならよい。
`system.runtime.nodes` は provisioner の Basic 認証で照会し、coordinator と worker が
`state=active`、`node_version=484` であることを確認する。パスワードは
`foundation/provisioner.json` から読み、画面へ出さない。

### 9.2 Workbench

`/api/status` は Basic 認証が必要。ログイン名は `accounts.json` のキー、
パスワードは `runtime/client.json` の `password` を使う。パスワードは表示しない。

```sh
PATH="$PWD/tools/local_experiment/dataops/.venv/bin:$PATH" python3 - <<'PY'
import base64, json, os
from pathlib import Path
from urllib.request import Request, urlopen

root = Path(os.environ['DIR'])
client = json.loads((root / 'runtime/client.json').read_text())
auth = 'Basic ' + base64.b64encode(
    (client['username'] + ':' + client['password']).encode()).decode()
with urlopen(Request('http://127.0.0.1:8888/api/status',
                     headers={'Authorization': auth}), timeout=30) as response:
    report = json.load(response)
print(report['status'], report.get('warehouse'))
PY
```

`status=healthy`、warehouse が `state=active` / `replicas=2` / `ready_replicas=2`
ならよい。logical mode の `/api/status` は legacy と形が違い、`components` ではなく
`creation_mode=logical_v1` と warehouse 状態を返す。

### 9.3 SQL endpoint（8080）

8080 の `/v1/statement` は、登録済み DB の catalog と schema を
`X-Trino-Catalog` / `X-Trino-Schema` で必ず指定する。欠けると
`UNKNOWN_CONTEXT`（404）になる。`X-Shirokuma-Role` と Basic 認証も必要。

```sh
curl -u "$SHIROKUMA_LOGIN_USER" \
  -H 'X-Shirokuma-Role: admin' \
  -H 'X-Trino-Catalog: <sql_catalog>' \
  -H 'X-Trino-Schema: raw' \
  -H 'Content-Type: text/plain' \
  --data 'SELECT current_user, version(), 42' \
  http://127.0.0.1:8080/v1/statement
```

応答の `nextUri` を同じヘッダーで取得し、`stats.state=FINISHED` と
`version()=484` を確認する。`/v1/info` は endpoint では
`{"nodeVersion":{"version":"shirokuma"}}` を返す合成応答であり、Trino 本体の
`484` は 9.1 の `/v1/info` で確認する。

### 9.4 最初の DB 作成

Workbench の管理 API で空 DB を作る。`team` は必須。

```sh
curl -u "$SHIROKUMA_LOGIN_USER" \
  -H 'X-Shirokuma-Role: admin' \
  -H 'Idempotency-Key: smoke-20261123-1' \
  -H 'Content-Type: application/json' \
  --data '{"sql_catalog":"smoke_20261123","display_name":"Smoke 2026-11-23","description":"first operation","team":"data","initial_grants":[]}' \
  http://127.0.0.1:8888/api/v1/admin/database-creations
```

`202` の `creation_id` を `GET /api/v1/admin/database-creations/<id>` で polling し、
`progress_code=READY` を確認する。作成後は 9.3 の catalog/schema に
`smoke_20261123` と `raw` を指定して SELECT できる。

## 10. 撤去

Workbench と endpoint を停止してから、namespace と cluster-scoped policy を消す。
namespace を消しても `ValidatingAdmissionPolicy` / Binding は残るため、必ず
foundation label で消す。

```sh
kill -TERM "$(cat "$DIR/run/workbench.pid")" "$(cat "$DIR/run/endpoint.pid")"

kubectl --context colima-mac-studio-solo delete namespace shirokuma-logical --ignore-not-found
kubectl --context colima-mac-studio-solo delete \
  validatingadmissionpolicy,validatingadmissionpolicybinding \
  -l shirokuma.dev/logical-foundation="$FOUNDATION_ID" --ignore-not-found
```

local-path の PV は PVC 削除に伴って Delete reclaim される。残っていないことを確認する。

```sh
kubectl --context colima-mac-studio-solo get pv -o json \
  | python3 -c 'import json,sys; print([p["metadata"]["name"] for p in json.load(sys.stdin)["items"] if p.get("spec",{}).get("claimRef",{}).get("namespace")=="shirokuma-logical"])'
```

最後に秘密を含む作業ディレクトリを削除する。

```sh
rm -rf "$DIR"
```

## 11. トラブルシューティング

| 症状 | 確認と対処 |
|---|---|
| `PLATFORM_ADMIN_MISMATCH` | `runtime/credentials/platform-admin.json` の username/password を `foundation/provisioner.json` と一致させる。DB 作成後に誤った状態で公開した場合は、`password.db` から provisioner が消えているため、使い捨て環境なら namespace と control store を作り直すのが確実。 |
| `INVALID_FOUNDATION_SETTINGS`（prepare） | SeaweedFS が `:4.51@sha256:...` のタグ付きになっていないか。untagged `.../seaweedfs@sha256:...` にする。 |
| `FOUNDATION_NOT_READY` | bootstrap Job、PVC、Pod、foundation receipt を確認する。image/SPI 更新後は `refresh-verification --confirm-workbench-stopped` を使う。 |
| `OPERATOR_UNAVAILABLE` | `SHIROKUMA_RUNTIME_PROFILE` が正しいか、profile が `mode=logical_v1` か確認する。 |
| `WAREHOUSE_START_REQUIRED` | compute が停止している。Workbench の lifecycle で起動するか、Trino Deployment を確認する。 |
| 8080 が `UNKNOWN_CONTEXT` | `X-Trino-Catalog` と `X-Trino-Schema` の両方を指定する。catalog/schema は登録済み DB のものに限る。 |

## 参照

- `tools/local_experiment/logical_foundation.py:192`（SeaweedFS untagged 検証）、
  `:270`（prepare）、`:296`（Trino 484 pin）、`:603`（apply）
- `tools/local_experiment/logical_secrets/build.py:13`、`:62`（SPI jar）
- `tools/local_experiment/runtime_profile.py:144`、`:217`、`:246`、`:294`
- `tools/local_experiment/logical_creations.py:112`（lifetime）、`:596`（register）、
  `:1780`（CLI）
- `tools/local_experiment/request_auth.py:24`、`:44`、`:60`（accounts）
- `docs/guides/logical-foundation-verification-refresh-2026-11-23.md`
