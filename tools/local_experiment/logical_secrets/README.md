# Logical-v1 private adapter (source only)

`logical_foundation.load(path)`, `validate(config)` and `kube_argv(config)` retain
the exact version-1 settings schema. `Provider(config, *, command=None,
http=None, sdk=None, popen=None)` accepts that mapping or an absolute immutable0600
JSON path and copies it. Imports and construction are inert. `open()` returns
self, owns ephemeral loopback tunnels to the three fixed owned Services, and
`close()` closes only those children. The context manager opens/closes them.
Native configuration uses Service DNS. The clean default namespace is
`shirokuma-logical`. `foundation_id` is the real core-registered WCG; this adapter
creates no ledger. Core retains admission, receipts, and final READY CAS.

## Primary build command (Commander only)

From the repository root, with the exact official Trino 483 image already cached:

```sh
rtk proxy mkdir -p -m 700 var/logical-build
rtk proxy python3 tools/local_experiment/logical_secrets/build.py --context colima-mac-studio-solo --output "$PWD/var/logical-build/logical-secrets.jar"
```

`build(output, *, overwrite=False, context='colima-mac-studio-solo', command=None) -> Path` and CLI `--output`
accept only the authorized `colima-mac-studio-solo` context (also the default),
and require an absolute `.jar` path in a private 0700 directory. Existing output
requires explicit `--overwrite`. Output is 0600. The tracked helper uses only
`docker --context colima-mac-studio-solo run --pull=never --network=none`, the exact Dockerfile image digest,
resident JDK 25 and resident Airlift SPI 439. It requires no buildx, image build,
pull, package installation or upstream rebuild. Only the fixed own source tree
(read-only) and a fresh isolated build subdirectory under the requested private output parent are mounted; no credential or
prototype directory is mounted. The constant shell command compiles own Java
sources, copies only the intended service descriptor, and references resident
SPI in the manifest. Python zipfile validation rejects foreign classes, extra
files/directories, duplicate entries and traversal. Nested own classes are
allowed. The Dockerfile remains an optional inert alternative.

The worker has not invoked this command or compiled Java. Mocked subprocess,
archive validation, syntax and inert import checks are the only build evidence.

## Private Python contract

Every `db` is exactly `{database_id, sql_catalog, generation_id,
foundation_revision}`. IDs/revision must match the validated foundation.

| Callable signature | Successful private result |
| --- | --- |
| `create_role(db)` | `{role_arn}` (canonical known account ARN) |
| `create_catalog(db)` | Raw decoded Polaris management response |
| `issue_consumer(db)` | Flat `{client_id, client_secret, realm, principal_name}`; immediately returned, no follow-up API |
| `create_namespaces(db, consumer)` | `{namespaces: [raw, staging, marts, sandbox]}` |
| `publish_material(db, material)` | Publisher hash mapping: `target.<fixed-filename>` to SHA256 or `MISSING` |
| `attach_catalog(db, credential_ref)` | `{catalog, credential_ref}` |
| `check_role(db)` | `{verified: bool, role_arn}` |
| `check_catalog(db)`, `check_consumer(db, consumer)`, `check_namespaces(db, consumer)` | `{verified: bool}` |
| `check_material(db, material)` | `{verified: bool, hashes, compute_uids}` |
| `check_attached(db, credential_ref)` | `{verified: bool, credential_ref, catalog_sha256}` (digest, `MISSING`, or absent receipt as null) |
| `verify_revoked(db, engine_identity)` | `{verified: bool, control_positive: true, identity_denied: bool}` after actual same-DB INTERNAL control |
| `verify_target(db, engine_identity)` | `{verified: bool, prefix_positive, prefix_negative, identity_positive, identity_negative, principal_id, selected_role, grant_epoch, policy_revision}` plus `marker_sha256` when prefix verification runs |
| `check_verify_target(db, engine_identity)` | Same verification proof as `verify_target`, read-only observation with zero marker PUTs; no effects callback needed |
| `probe()` | `{ready: bool, revision, foundation_id, readiness, incarnation}` plus `hashes, spi_sha256` on success |
| `effects(callback)` | Context manager yielding self; callback contract below |

`consumer` has exactly the four fields returned by `issue_consumer`.
`engine_identity` has exactly `{principal_id, database_id, selected_role,
grant_epoch, policy_revision, username, password, credential_ref}`. It must be the
actual selected user, never the internal provisioner. `policy_revision` is a
positive exact integer; `grant_epoch` is a positive exact integer except that
`None` is allowed only for `selected_role == "admin"`. Booleans deny. The
None-admin tuple means platform authority was proven by core's private current
account gate; SQL does not infer it and the provider has no provisioner fallback.
Core filters `authority_source` and `account_gate_revision` before calling this
exact eight-key provider contract. SQL positive and real
engine denial checks are separate from DB-role STS prefix positive/AccessDenied
checks. The deterministic verification marker is retained for reconciliation.

`verify_revoked` requires the exact previously issued, known-valid persisted
identity tuple and the caller's current intended grant/account gate. Invented
bad credentials cannot establish an authority transition. It first queries
`current_user` using the INTERNAL provisioner and checks all expected schemas
in the same target DB. Only then does it issue a read using the supplied old
identity. Only HTTP 401/403 or actual `ENGINE_ACCESS_DENIED` proves revocation.
Still-usable old credentials return `{verified: false, control_positive: true, identity_denied: false}`. Failed control, generic
SQL errors, not-found, timeout and transport/response loss raise a safe error;
they never count as revocation. Neither verification substitutes internal
credentials for user-identity proof. These reads need no effects callback.

`material` allows only `credentials`, optional `password_db`, `rules_json`,
`guard_rego`, and `expected_hashes`. `credentials` maps validated reference IDs to
nonempty strings or `{client_id, client_secret}` with optional integer
`version: 1`; pairs normalize to `client_id:client_secret`. Unknown keys and
backend `{username,password}` pairs deny. Each optional snapshot is a string;
its fixed filename needs a SHA256 or `MISSING` in `expected_hashes`.
Private provisioner JSON is exactly `{version: 1, username:
"logical_provisioner", password}` in a 0600 file. It is never a userSQL fallback.

`check_material` resolves the same normalized value on every active owned Trino
compute pod. `check_attached` requires SQL catalog presence, actual SPI reference
readback, and the exact persisted catalog property digest, including
`connector.name=iceberg`, native endpoints, warehouse, realm, OAuth server,
vending, token-exchange false, and the specified reference. It proves attachment
configuration; the separate target check proves user authorization.

## Effects and error semantics

Use `with provider.effects(callback): ...`. The callback is
`(key, safe_identity, call) -> decoded_response`. It must checkpoint the intent before
`call()` and its JSON receipt before returning. Each IAM create/attach, Polaris
business POST/PUT, a whole mutating SQL action, fixed Java publication exec, and
storage verification marker PUT is a distinct leaf. The key is SHA256 of a
canonical identity with `method`, `service`, fixed `target`, and
`payload_sha256`. No raw SQL, passwords, tokens, headers or secret body appears
in the identity/key. The closure privately holds the request. HTTP status checks
and JSON decoding occur inside the callable; its ACK is the decoded JSON-safe
API payload. SDK known rejections are classified inside the callable, before
the callback sees them. SDK management dictionaries may contain date/datetime
values, which core converts to ISO strings. Publisher ACKs are already-validated
hash dictionaries. Neither HTTP tuples/bytes nor stdout bytes enter receipts.
The provider consumes that durable ACK before any next management API. A SQL
mutation closure sends one statement POST and bounded read-only continuation GETs
until terminal completion. Only then does it return a JSON-safe terminal receipt
(`rows`, `query_id`, `success`) for durable ACK. A frozen ACK resumes without
network access. Initial/poll transport loss and generic terminal mutation errors
remain Unknown; actual engine ACCESS_DENIED is a known rejection. Core may
authorize a fresh attempt after rejection; the provider never retries a mutation.

For explicit safe resume the callback may return a saved frozen ACK without
calling the closure. There is no automatic resend; the closure can run at most
once per leaf invocation. `ContextVar` scopes reset in `finally`; nested scopes,
concurrent tasks/threads, and separate provider instances keep their own scope.
No normal method installs a callback. Missing callback raises known-no-effects
`Rejected("EFFECTS_REQUIRED_NO_EFFECTS")` at the mutation boundary.
OAuth acquisition/expiry refresh, GET/HEAD, STS authentication, read-only SQL
SELECT/SHOW are unjournaled; SQL continuation GETs are part of the single
mutating action closure and never separate mutation leaves. SDK `total_max_attempts` remains 1.

`Rejected` is a fixed-code rejection/observation failure, not rollback of earlier
leaves. Callback exceptions propagate unchanged, including their exact object
identity, before dispatch, during a classified leaf rejection, or after an ACK.
Core owns checkpoint outcome translation; the adapter never converts core's
known Rejected-to-CREATION_FAILED translation into Unknown. Transport loss/ambiguous mutation
responses are Unknown and require reconciliation. `StaleCAS` is a pre-write
known denial. Core must save issued credentials before invoking later methods.
No public READY authority is granted by private observations.


Every `check_role`, `check_catalog`, `check_consumer`, `check_namespaces`,
`check_material`, and `check_attached` result retains its detailed evidence and
adds the exact top-level DB descriptor, `kind` (respectively `role`, `catalog`,
`consumer`, `namespaces`, `publication`, `attach`), `verified: bool`, and
`complete: bool`. Complete is true only when the full fixed parent effects are
verified. Partial/failed returned proofs have complete false.
`grants_verified` on namespace proofs matches the full consumer, exact RBAC and
four-namespace result. `binding_verified` on attach proofs requires the exact
catalog properties/reference and actual SQL presence; `credential_ref` is retained.
These are current semantic observations, never reconstructed historical HTTP ACKs.
Core owns exact matching against original parent intent, claim/STOP gates and
`OBSERVED_APPLIED`. A consumer observation requires the original private issuance
receipt. Existence/clientId cannot recover a lost secret, and an envelope never
authorizes reissuance.

Both target verification methods share the same SQL and storage checks and
return `kind="verification"`, complete/verified, exact DB descriptor, and typed
`principal_id`, `selected_role`, `grant_epoch`, `policy_revision`,
`credential_ref`, `username` pins. Passwords never appear in proof results.
`bi_viewer` must see marts and must not see raw/staging/sandbox; admin and
data_engineer must see all four. `information_schema` is tolerated. Consumer
namespace proof independently establishes all four namespaces exist. Fixed
`system.runtime.nodes` userSQL must actually deny; worker membership uses only
INTERNAL control. Generic SQL/transport errors never establish denial.

Normal `verify_target` journals the deterministic marker PUT once per leaf;
`check_verify_target` passes `write_marker=False`, authenticates fresh STS,
performs real user SQL positive/negative reads and exact marker GET/hash and
foreign-prefix 403 `AccessDenied`. A lost PUT response can therefore be observed
without a second PUT. Missing/wrong marker or partial reads cannot pass. OAuth
acquisition when needed remains read-only; engine-side OAuth renewal is a live
runtime gate. Neither observer performs business writes. Password/file ACL
refresh remains explicitly 1s. Target checks re-observe only read-only engine
identity, role visibility and negative queries for up to five seconds, at 100ms
intervals. Only expected 401/403/ACCESS_DENIED or incomplete visibility/negative
proofs can be observed again; generic SQL/transport errors fail immediately.
Storage verification runs once after the engine proof, so marker PUT is never
retried. Revocation performs its positive INTERNAL control once, then polls
the old identity until an actual 401/403/ACCESS_DENIED. The deadline returns
verified=false unless the required reads have proved the result.

Known mutation rejection statuses are exactly 400, 401, 403, 404, 405, 409, 412,
422. Other statuses including 408/499 remain Unknown. SDK RequestTimeout,
RequestTimeoutException and PriorRequestNotComplete remain Unknown even with
one of those 4xx statuses. Classification occurs inside the leaf; read-only
failures close the proof. No raw response/error bodies escape and no retry is added.

`probe().readiness.opa` requires the owned fixed trino-opa Deployment's current
observed generation, exactly one desired/current/updated/available/ready replica,
and pinned OPA image, in addition to foundation ownership/revision. Failure
keeps global ready false. No OPA tunnel or Pod enumeration is added. Metadata
readiness is separate from effective target SQL authorization proof.

## Polaris readback

Principal creation follows the proven shared-v1 request:
`{"principal":{"name":name},"credentialRotationRequired":false}`.
`check_namespaces` checks exact consumer name/clientId and realm, the principal's
single expected principal role (`roles` wrapper), that role's single expected
catalog role in the same catalog (`roles` wrapper), exactly one catalog grant
(`grants` wrapper), and exactly the four namespaces through consumer credentials.
Missing, extra or wrong roles/grants/namespaces deny verification. Checks perform
no management mutation. `check_catalog` compares the detailed definition,
including roleArn, location prefix, native S3/internal/STS endpoints and settings;
all Polaris calls are bound to the configured realm.

The single scoped `CATALOG_MANAGE_CONTENT` grant includes catalog read properties,
full table/namespace/view metadata, and table read/write data according to the
[official Polaris 1.8 access-control contract](https://polaris.apache.org/releases/1.8.0/managing-security/access-control/).
It does not need redundant grants for those included privileges.

## Foundation and remaining runtime gates

The owned `<prefix>-polaris-features` ConfigMap contains `application.properties`
with the default canonical feature keys exactly:

```properties
polaris.features."STORAGE_CREDENTIAL_DURATION_SECONDS"=900
polaris.features."STORAGE_CREDENTIAL_CACHE_DURATION_SECONDS"=450
```

Only the shared Polaris server mounts its whole directory read-only at
`/etc/polaris-foundation`, with
`QUARKUS_CONFIG_LOCATIONS=file:/etc/polaris-foundation/application.properties`.
[Quarkus config locations](https://quarkus.io/guides/config-reference/#locations)
loads this file in addition to default/classpath configuration. Prepare emits
the normalized private Polaris settings; there is no public input or HTTP API.
IAM `tokenDuration=1h` and `maxSessionLength=12h` remain unchanged: Polaris
explicitly requests 900 seconds with these defaults. Configuration is not renewal
evidence. Commander must measure actual vended expiration and successful renewal
across the 900-second expiration boundary.

The initial seed grants catalog `owner` only to fixed INTERNAL
`logical_provisioner` (any catalog before the first database exists), denies
other users, and explicitly grants only that user `system_information` read.
OPA initially allows only the same fixed internal user. Trino 483 catalog `all`
does not imply OWNER for catalog creation. UserSQL never falls back to this user.
`prepare` and `render` share the canonical bootstrap rules. Render takes the
existing private state's password/empty credential material, replaces only the
generated seed's rules and guard with the current canonical policy, and never
rewrites existing private state or standalone host files.

For an already prepared `foundation-a`, rerun the canonical command with its
absolute 0600 settings path, then review the fresh `render-public.yaml` and
`render-secrets.yaml` in that settings file's private directory:

```sh
rtk proxy python3 -m tools.local_experiment.logical_foundation render --settings /absolute/path/to/foundation-a/settings.json
```

Old standalone host `rules.json` and `foundation-secrets.yaml` remain snapshots
and need no rewrite: apply always rerenders from the same settings/private state
and does not consume those seed artifacts. Render outputs are exclusive private
files; an existing output is not overwritten. FixedPublisher bootstrap remains
absent-only and never overwrites existing runtime PVC files on reapply. This
seed update is for the first clean apply, not a runtime policy migration.

`prepare(*, private_dir, kubeconfig, context, node_name, foundation_id, revision,
environment_id, warehouse_id, seaweed_image, jar, namespace="shirokuma-logical",
prefix="logical", volume_max=32, workers=0, exclude_coordinator=False, command=_run)` writes new private material and
rendered documents, without applying them. `render(config)` returns
`(public_documents, private_secret_documents)`.
`apply(config, *, apply=False, command=_run)` requires literal `apply=True` and
returns None only after staged rollout, existing/new bootstrap Job completion,
foundation bucket readback, and bounded foundation probe success. Only its
explicit bucket creation installs a short pass-through callback. Existing Jobs
are awaited, never replayed. Ordinary Provider methods never deploy, wake
warehouses, run ToFu/Flux or verify all databases.

The exact top-level schema is `{version, creation_mode, foundation_id, revision,
environment_id, warehouse_id, kube, prefix, images, storage, polaris, trino,
artifacts, private_dir}`. Version is integer 1; creation_mode is `logical_v1`.
Nested fields are exactly:

| Section | Keys |
| --- | --- |
| `kube` | `kubeconfig, context, namespace, node_name, api_server, ca_sha256, node_uids` |
| `images` | `seaweedfs, postgres, polaris, polaris_admin, trino, opa` |
| `storage` | `account_id, bucket, region, endpoint, sts_endpoint, admin_credentials_file, issuer_credentials_file` |
| `polaris` | `endpoint, realm, root_credentials_file, credential_duration_seconds, credential_cache_duration_seconds` |
| `trino` | `endpoint, ca_file, provisioner_credentials_file, publisher_pod, publisher_container, compute_selector, workers, include_coordinator` |
| `artifacts` | `jar` |

Old otherwise well-shaped settings may omit `workers` and `include_coordinator`;
validation normalizes them to integer 0 and boolean true. Polaris settings may
also omit `credential_duration_seconds` and
`credential_cache_duration_seconds`; validation copies and normalizes these to
900 and 450. Both require exact integers (booleans deny), with
900 <= duration <= 43200 and 0 <= cache < duration. Other missing/unknown
keys deny. `workers` is exactly integer 0 or 1; false `include_coordinator`
requires workers 1. Prepare CLI exposes `--workers {0,1}` and
`--exclude-coordinator` (only with `--workers 1`). The compatibility
`compute_selector` remains fixed at `app.kubernetes.io/name=trino-local`;
compute observation enumerates namespace Pods and validates every matching
Trino container/image or fixed compute label, including unexpected serving Pods.

The two owned compute Deployments are exactly `trino-local` (one coordinator)
and `trino-local-worker` (default replicas 0, optional 1). The Service selects
only the coordinator. Both retain public HTTPS 8080; namespace-internal HTTP
8081 uses `discovery.uri=http://trino-local:8081`, the shared internal secret,
and `http-server.authentication.allow-insecure-over-http=false`. User PASSWORD
authentication is never enabled on plaintext. Both use the same pinned node,
official image/JAR, environment, secrets SPI, file ACL and OPA settings. Worker
data is in its own `/tmp` emptyDir; its entire runtime PVC mount is read-only.
The worker has no bootstrap publisher, catalog-store PVC, `catalog.store` or
`catalog.config-dir`. Fixed publication always selects the coordinator.

`_compute` validates owner chain/UID, configured replicas, all active candidate
Pods, readiness, image digest, node, nonroot security and fixed PVC/code/config
mounts. Private probe reads all fixed runtime hashes on every active compute
Pod, retaining `target.password.db`, `target.rules.json` and `target.guard.rego`
for core CAS. These hashes are private and are not a public capability output.
A configured zero-replica worker does not block readiness. With workers 1,
`verify_target` also returns `runtime_nodes` from a real INTERNAL SQL query of
`system.runtime.nodes` and requires an active coordinator and worker. Pod metadata
alone does not establish engine membership or physical worker I/O. Commander
must complete the explicit 3a worker physical-I/O gate after source freeze.

PVCs explicitly request local-path, Filesystem, same-node RWO and the documented
[per-PVC `volumeType: local` annotation](https://github.com/rancher/local-path-provisioner/blob/v0.0.31/README.md#volume-types),
without changing the global StorageClass. Probe checks
bound PV identity, claim UID, local type and node affinity. Seaweed announces
PodIP via Kubernetes argument expansion; S3/STS URLs remain Service DNS. Durable
filer/master/volume/IAM/signer material, nonroot users and whole-directory mounts
remain required. The shared foundation Secret is projected per component: Seaweed
gets only `s3.json`, `iam.json`, `filer.toml`; Polaris bootstrap gets only
`credentials.json`; Polaris server gets only `public.pem`, `private.pem`; Trino
coordinator and worker get only `trino.pem`. Projections mount directories with
mode 0440/fsGroup 10001. Process-required secretKeyRef environment fields remain
scoped to PG/JDBC password, Polaris issuer pair, or Trino shared internal secret.
OPA mounts no private foundation Secret. OPA shares the runtime PVC and is a
trusted reader of consumer credentials and `password.db`; this shared-process
trust limit is not technical credential separation. The init-only seed Secret and runtime
PVC directory/fileSPI behavior remain unchanged. The trusted provider owns its
separate host-side S3 administrator material.
OPA watches the mounted directory for atomic rename refresh;
two AND access-control plugins remain enabled with request/response logs false.
Password/file ACL refresh is 1s. Cache size 0 is accepted by Trino 483
[FileConfig](https://raw.githubusercontent.com/trinodb/trino/483/plugin/trino-password-authenticators/src/main/java/io/trino/plugin/password/file/FileConfig.java),
passed to CacheBuilder by
[PasswordStore](https://raw.githubusercontent.com/trinodb/trino/483/plugin/trino-password-authenticators/src/main/java/io/trino/plugin/password/file/PasswordStore.java),
and zero is supported by the official
[CacheBuilder contract](https://guava.dev/releases/33.4.8-jre/api/docs/com/google/common/cache/CacheBuilder.html#maximumSize(long)).
These are source observations, not runtime reload evidence.

This slice has only mocked Python and source/inert checks. Commander owns SPI/Java
compilation, clean official image startup, storage permissions/admission and
PV metadata access, actual managed IAM/Polaris behavior, exact catalog persistence,
SPI readback across compute, password/ACL/OPA reload and old-credential denial,
STS renewal/isolation, restart, cold start and the 3a same-node worker physical-I/O proof.
No compilation, deployment or live readiness is established by unit tests.

## Fixed Java protocol and privilege boundary

The fixed invocation is `java -cp /opt/logical-secrets/logical-secrets.jar
org.shirokuma.logical.secrets.FixedPublisher MODE`. Exactly one mode argument;
values use UTF-8 Java Properties on stdin, capped at 16 MiB. No arbitrary paths,
commands or targets. Duplicate/unknown request fields deny.

| Mode | stdin | Successful stdout after `status=OK` |
| --- | --- | --- |
| `publish` | `credential.<id>=value`; optional base64 `password_db`, `rules_json`, `guard_rego`, each paired with `expected.<filename>` | Four `target.<filename>` hashes or `MISSING` |
| `read` | Empty | Four target hashes or `MISSING` |
| `resolve` | Exactly `ref=<validated-id>` | `ref.sha256` from actual FileSecretProvider |
| `catalog` | Exactly `catalog=<validated-SQL-name>` | `catalog.sha256` or `MISSING` |
| `bootstrap` | Empty | Four target hashes after copying only absent bootstrap targets |

`catalog` reads only `/var/trino-catalogs/<name>.properties`, with name matching
`[A-Za-z][A-Za-z0-9_]{0,62}`. Java Properties are parsed; sorted normalized
`key=value` newline pairs are SHA256 hashed, never printed. Python hashes the
same exact expected map. Runtime secret references match
`[A-Za-z0-9][A-Za-z0-9_.-]{0,191}`; FileSecretProvider reopens the fixed
`/etc/trino-private-secrets/credentials.properties` for each resolution.

Python recognizes exit 22 only with exact sentinel line
`LOGICAL_SECRETS_CAS_STALE`, and exit 23 only with exact sentinel line
`LOGICAL_SECRETS_DENIED`; kubectl's additional exit-status stderr line is allowed.
Substring matches deny classification. Code 24, other mutation exec errors and
response loss remain Unknown. Arbitrary stderr is discarded, never echoed/stored.

Immutable credential merge, short host fcntl lock and stable PVC publisher lock
with pre-write CAS remain. Each 0600 replacement is atomic per file; multi-file
publication is not atomic and any partial write remains Unknown. Read receipts
are not simultaneous snapshots. Mount entire directories; do not use subPath.

The code-only jar uses the official Trino 483 resident Airlift 439 secrets SPI;
it mounts at `/opt/logical-secrets/logical-secrets.jar` and
`/usr/lib/trino/secrets-plugin/logical-file/logical-secrets.jar`. The factory is
`file`, its configuration map is empty, and `${file:reference-id}` is the SQL
property reference. Configure `/etc/trino/secrets.toml` via `-DsecretsConfig`.
The publisher/plugin are trusted readers of all consumer credentials. Namespace
RBAC and the trusted executor are required: Kubernetes exec has no Pod UID
precondition, and exec privilege permits more than this helper's fixed modes.
UID reread/fixed argv does not reduce Kubernetes exec itself to a sandbox.
