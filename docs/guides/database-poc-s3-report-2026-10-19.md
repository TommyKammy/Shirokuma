# Phase 1 DB別S3資格情報・prefix制限の検証 — 2026-10-19

続く[Polaris・既存サービスのDB別資格情報検証](database-service-poc-report-2026-10-19.md)で、共有キー失効中の内部FileIO・既存Trino・bootstrapを確認した。同検証でPolaris 1.6.0のdelete manifest purge不具合も判明している。本書の成功結果は以下に記した試験範囲に限る。

**SALES / FINANCEに別々のS3キーを割り当て、自DB prefixだけを許可する最小構成が実機で成立した。相手DBへの直接アクセスを拒否し、Iceberg操作も95/95成功した。** bucket全体のRead / Listを追加する必要はなかった。

直接S3 runnerは148チェック成功（本体122・cleanup 26）、追加境界試験45チェック成功。SeaweedFSとPoC Trinoの再起動後も直接S3 148/148・SQL 95/95が再び成功した。検証後はPoCを停止し、追加identityの失効と元設定への復旧を確認した。

Phase 2以降、Workbench / Gateway / control_storeの実装には進んでいない。既存データの移行・保全は行っていない。

## 構成と範囲

- checkout: `worktree/local-autonomous`、branch: `local/autonomous-experiment`、開始時HEAD: `c639dc585dd0acb27ff7a03fb959d2724d6187f2`。
- context: `colima-mac-studio-solo`。S3は`shirokuma-storage/seaweedfs`、SQLは`shirokuma-dev/trino-database-poc`。
- 実行binary: SeaweedFS `4.39 db42bb49757b459551607939807017d7a9d5a94a linux arm64`。公式4.39 tagと一致。Trinoは前回と同じ483＋OPA構成。
- クライアント: task専用一時venvのboto3 / botocore `1.43.114`。SigV4・path-style、SDK retry 0、connect 3秒 / read 5秒。checksum設定は`when_required`。
- S3 endpointはlocalhost forwardの`http://127.0.0.1:18333`。外部公開なし。新image build / pullなし。

| S3 identity | 許可対象 | actions |
| --- | --- | --- |
| `database-poc-sales-s3` | `shirokuma-lakehouse/database-poc/sales/*` | Read / Write / Tagging / List |
| `database-poc-finance-s3` | `shirokuma-lakehouse/database-poc/finance/*` | Read / Write / Tagging / List |

access key / secret keyは両方とも別値。`sales*`ではなく`sales/*`とし、Admin・bucket全体のRead / List・追加bucket policyを付与していない。開始時にbucket policyが`NoSuchBucketPolicy`、public ACLなしであることを確認した。4.39はactionのprefix照合に対応する。[公式認可実装](https://github.com/seaweedfs/seaweedfs/blob/4.39/weed/s3api/auth_credentials.go#L1639)

PoC TrinoはCatalogごとの`SALES_S3_*` / `FINANCE_S3_*`だけを参照する。専用Secretに2組を格納し、共有`AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY`をPodへ渡さなくなったことをlive Deploymentで確認した。regionだけは既存Secretの非秘密値を参照する。資格情報が欠けた場合の共有キーfallbackはない。

server側はOpenTofu管理の`seaweedfs-s3-credentials:s3.json`へPoCの2 identityだけを一時追加した。既存operator / application identity、既存キー、`iam.readOnly=true`は維持。OpenTofuファイルやFlux設定は変更していない。最後に追加分を撤去し、元のSecret dataをbyte単位で復旧した。

## 直接S3の結果

相手側のobject / uploadを先に実在させ、負例後にownerの資格情報で内容・Tagging・parts・存在が変わっていないことを確認した。通常の負例は`403 AccessDenied`のみ成功扱いとし、HEADだけはbodyなし403も許容した。接続失敗や対象不存在を認可拒否と扱っていない。

| 試験 | 両方向の結果 |
| --- | --- |
| 自prefixのPUT / GET / HEAD / DELETE / Tagging / LIST | 成功 |
| 他DBのGET / HEAD / 上書きPUT / DELETE / Tagging / LIST | 403。相手marker不変 |
| 空prefix・親prefix・他DB・似た名前のsiblingのLIST | 403。V1 / V2双方で確認 |
| own LISTのpagination / delimiter | 自prefixの3 objectを複数ページで取得し、他DB key混入なし |
| HEAD bucket | 403のまま維持。Iceberg SQLは成功 |
| CopyObject | own→own成功。other→own、own→otherは拒否、拒否先objectなし |
| multipart | own create / part / ListParts / complete / abort成功。他DBのcreate / part / complete / abort / ListPartsは拒否 |
| UploadPartCopy | own source→own upload→complete成功。other source→own uploadは拒否、partsとsource不変 |
| ListMultipartUploads | own prefixのみ成功。空・親・他DB prefixは拒否 |
| upload IDの取り違え | 他DBのIDを自DB keyと組み合わせると404 NoSuchUpload。認可拒否とは別の整合性確認 |
| DeleteObjects | ownのみは成功。mixedはHTTP 200の中でownがDeleted、otherがError AccessDenied。other object残存 |
| 匿名 / 不正キー | 匿名GETは403 AccessDenied。不正キーは403 InvalidAccessKeyId |
| `own/../other/` / encoded dot prefix LIST | HTTP 200・空結果。他DBデータの開示なし。403の証拠には数えない |

CopyとUploadPartCopyのsource認可、複数DELETEのkey別認可があることは公式sourceでも確認し、上記を実機で検証した。[Copy実装](https://github.com/seaweedfs/seaweedfs/blob/4.39/weed/s3api/s3api_object_handlers_copy.go#L165)、[key別DELETE](https://github.com/seaweedfs/seaweedfs/blob/4.39/weed/s3api/s3api_object_handlers_delete.go#L426)

148チェックのrunnerとは別に、45チェックでpagination / delimiter、追加MPU経路、匿名・不正キー等を確認した。チェック数にはsetup・状態比較・cleanupも含み、148種類の独立した認可機能を検証したという意味ではない。

## Iceberg互換性と再起動

既存95ケースを変更せず、DB別キーを注入したTrinoで実行した。raw / sandboxのSELECT・INSERT・UPDATE・DELETE・CREATE / DROP、Catalog / Schema / Polaris principal境界、MARTSの読取・DML拒否、4 nonadminの有効な祖先Snapshotへのrollback拒否を含めて95/95成功した。file＋OPA認可は維持した。

SeaweedFSと専用Trinoを再起動し、4テーブル（両DBのraw / marts）のUUID / location / current Snapshot / 行数が一致した。その後、直接S3 148チェックとSQL 95ケースを再実行し、全件成功した。

直接S3の初回runner実行は、再作成済みPodへの古いport-forwardが切れて18件UNKNOWNとなった。これを合格に数えず、接続を開き直してから検証を完了した。最後の復旧時にもPod置換中のforward起動が1回失敗し、Readyになった新Podへ接続し直した。

原因はアプリケーションcrashではなく、動作中の子Flux `shirokuma-object-storage`が手動`rollout restart`のannotationをGit宣言へ戻し、2度目のPod置換を起こすことだった。05:20:18Zの手動restart、05:20:37Zのkustomize-controllerによるStatefulSet更新、05:20:40Zの再生成をevents / managedFieldsで照合した。再現時はUID / Ready / currentRevisionとupdateRevisionの一致を確認し、forward切断を拒否成功と誤認しない。Flux設定は変更していない。

## 達成した境界と残る条件

達成したのは、**SALES / FINANCEの専用S3キーを単独で使う直接S3 APIのprefix境界**と、それを使うPoC Trinoの動作互換性である。

Polarisと既存コンポーネントは共通application keyを保持する。SQL lifecycle / purgeの成功はend-to-endの互換性を示すが、個々のS3 I/Oの使用資格情報を計測した証拠ではない。全コンポーネントの物理分離、Polarisの侵害や共通service keyの漏洩からの隔離、両キーを保持するTrinoプロセスの侵害からの隔離は受入範囲外。

既存共通キーの全面置換、永続的なIaC組込み、製品採用時のimage生成経路、アプリケーション/API/BI/Jobs/Workbenchの認可と利用者受入は後続の別スコープとする。大量データや負荷試験も実施していない。今回のローカルPoC成功を一般公開の許可とは扱わない。

## 再現と撤去

[S3 helper](../../tools/local_experiment/database_poc_s3.py)は`install / revoke / verify`だけを持つ。既存identityを維持し、同名・同access keyの衝突を拒否する。resourceVersionを保持したSecretの置換をstdinで行い、値をログへ出さない。private stateは0700、資格情報ファイルは0600。SDKはverify時だけ必要。CLIは自動でworkloadを再起動しない。[SQL helper](../../tools/local_experiment/database_poc.py)の既存ACL・SQL検証処理は不変。

初回だけ、`install`前に原Secretを私有stateへ保存する。同名baselineがあれば上書きせず、以前の撤去と現設定の一致を確認する。

```sh
rtk proxy python3 - <<'PY'
import json
from tools.local_experiment import database_poc as p
assert not (p.STATE / 's3-server-before.json').exists()
args = ['kubectl', '--context', p.CONTEXT, '-n', 'shirokuma-storage']
p.save('s3-server-before.json', json.loads(p.run(args + [
    'get', 'secret', 'seaweedfs-s3-credentials', '-o', 'json'])))
PY
```

`install`→SeaweedFS再起動・Ready確認→専用Trino deployの順序は[実行ガイド](database-poc.md)を参照する。直接S3試験は別ターミナルのlocalhost forwardと、task専用venvを使う。SQLとは異なるprobe prefixへ書き込む。

```sh
rtk proxy uv venv /tmp/shirokuma-database-s3-poc-20261019
rtk proxy uv pip install --python /tmp/shirokuma-database-s3-poc-20261019/bin/python boto3==1.43.114
rtk proxy kubectl --context colima-mac-studio-solo -n shirokuma-storage port-forward --address 127.0.0.1 svc/seaweedfs-s3 18333:8333
# forwardを動かしたまま別ターミナルで実行
rtk proxy /tmp/shirokuma-database-s3-poc-20261019/bin/python tools/local_experiment/database_poc_s3.py verify
```

終了時は専用Trinoをreplicas=0、`revoke`を実行する。JSONの再serializeによる空白・キー順差も戻す場合は、元configとのsemantic一致を必ず確認してから元のbytesを戻す。別のidentity変更があれば上書きせず止める。

```sh
rtk proxy python3 tools/local_experiment/database_poc_s3.py revoke
rtk proxy python3 - <<'PY'
import base64, json
from tools.local_experiment import database_poc as p
args = ['kubectl', '--context', p.CONTEXT, '-n', 'shirokuma-storage']
current = json.loads(p.run(args + ['get', 'secret', 'seaweedfs-s3-credentials', '-o', 'json']))
original = p.load('s3-server-before.json')
assert json.loads(base64.b64decode(current['data']['s3.json'])) == json.loads(base64.b64decode(original['data']['s3.json']))
current['data']['s3.json'] = original['data']['s3.json']
p.run(args + ['replace', '-f', '-'], json.dumps(current).encode())
PY
```

その後SeaweedFSを再起動し、Readyになった新Podにforwardを開き直す。SIGHUPによる再読込は既存identityへmergeするため、削除による失効の証拠にはならない。[static identityの読込](https://github.com/seaweedfs/seaweedfs/blob/4.39/weed/s3api/auth_credentials.go)

今回、旧専用キーで既知の自DB objectをGETすると、両方とも`403 InvalidAccessKeyId`になった。同じobjectを既存application keyでは取得でき、LIST / DELETEも成功した。server Secret dataは開始時とbyte単位で一致した。再検証用に残した私有キーはserver側で無効であり、再開時は明示的な`install`が必要。

## 証跡と終了状態

Git対象外の`var/shirokuma/database-poc/`へ保存した。`s3-server-before.json`と`s3-principals.json`は秘密を含むため公開しない。以下は値を含まない結果ファイル。

| ファイル | 証跡 |
| --- | --- |
| `s3-baseline.json`, `s3-preflight.json` | bucket policy / ACL / 既存キーの確認、専用キーの先行試験 |
| `s3-verification-initial-transport.json` | 接続切断時のUNKNOWN。合格対象外 |
| `s3-verification-before-restart.json`, `s3-verification.json` | 再起動前後の148チェック |
| `s3-extra.json` | 追加45チェック |
| `verification-s3-before-restart.json`, `verification.json` | DB別キーでの再起動前後95 SQLケース |
| `s3-before-restart.json`, `s3-restart-tables.json`, `s3-restart-runtime.json` | 4テーブル同一性とruntime再起動 |
| `s3-revocation.json`, `s3-revocation-runtime.json` | 専用キー失効、元Secret data復旧、既存キー正常 |

新規offlineテスト12件、`make verify-local-experiment`、`make verify-design-context`は成功。独立静的レビューにblockerはなかった。

PoCはreplicas=0。localhost forwardは全て終了。追加S3 identityは撤去し、既存キーと元server Secretへ復旧した。Catalogと合成データ、再現用の専用Secret・private stateは残した。外部公開、GitHub操作、push、後続Phaseの実装は行っていない。

終了後のSeaweedFS・既存Trino・PolarisはRunning / Ready。05:21:28Zの`doctor --profile local-lite`はcluster / policy healthy、Fluxだけ既存root Kustomizationのsuspendによりdegradedで、検証前と同じ状態だった。
