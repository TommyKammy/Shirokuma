# Standalone two-database PoC

Follow-up evidence: [DB-scoped credentials for Polaris and existing services](database-service-poc-report-2026-10-19.md). That bounded test uses two Polaris realms/processes, documents the Polaris 1.8.0 non-STS limitation, and records a remaining 1.6.0 delete-manifest purge failure.

The subsequent [official Polaris 1.8.0 retest](database-service-180-poc-report-2026-10-19.md) verifies automatic position-delete purge, scoped service principals, two-realm authorization, and S3/OAuth credential rotation. The `verify` helper accepts optional per-database REST `targets`; existing callers keep the original behavior.

This Phase 1 admin helper creates a disposable, separate Trino coordinator for
`sales` and `finance`. It does not integrate with Workbench, product APIs,
existing `trino-local`, Vault, or production. Run it from the repository root
with Python 3.9+ and existing `rtk`, `kubectl`, bcrypt-capable `htpasswd`, and
OpenSSL. The operator builds the dedicated image locally before deployment;
the helper never builds or pulls images.

## Operator sequence

The 2026-10-19 file-only baseline passed 91 of 95 checks; four nonadmin mart
rollbacks changed the snapshot. The follow-up file + OPA configuration passed
**95/95**, including all four real rollback denials with unchanged snapshots.
Admin rollback/restoration, OPA outage/recovery, and Pod restart were also
verified. See [the authorization report](database-poc-authorization-report-2026-10-19.md)
for table-procedure acceptance, and [the initial report](database-poc-report-2026-10-19.md)
for earlier storage, metadata and catalog recreation evidence.
The subsequent [S3 credential report](database-poc-s3-report-2026-10-19.md)
records DB-specific prefix permissions: 148 direct checks (including 26 cleanup
checks), 45 supplementary checks, and 95/95 SQL checks. After restart, the 148
direct checks and 95 SQL checks were repeated and passed again.
The PoC deployment was scaled to zero and its localhost forwards were closed.
The two temporary S3 identities were revoked and the original server Secret
restored. Reinstall them before starting this revised PoC again.
All Kubernetes operations are fixed to context `colima-mac-studio-solo`, using
`shirokuma-dev` for the PoC and `shirokuma-storage` for S3. The dedicated image copies the complete OPA plugin directory
unchanged from the pinned, existing local Trino 483 OPA experiment image into
the pinned password-enabled Trino 483 base. The old source-image recipe is
absent from this checkout; this is a local PoC input, not a newly verified
official-distribution provenance claim. Existing product images and configuration stay unchanged.
The helper separately checks that this base still matches the secure overlay.

The parent/operator builds with the approved local images already present:

```sh
rtk proxy docker --context colima-mac-studio-solo build --pull=false --network=none -f deploy/local-experiment/database-poc-opa/Dockerfile -t shirokuma.local/trino:483-password-opa-database-poc deploy/local-experiment/database-poc-opa
```

A fresh build can have a different local image ID. Inspect the output in this
Docker context and review/update the helper's literal `IMAGE` to that exact
`shirokuma.local/trino@sha256:...` reference before deploying a rebuilt image.

The operator's local build is complete and the helper's literal `IMAGE` is set
to its immutable output pin. The pinned images were available to the dedicated
Colima node in this run. If recreating the node, make those exact images
available using the existing local image workflow before deployment.
Both containers use `imagePullPolicy:
Never`; absent images must fail locally. Do not substitute the base digest for
the output pin. For an existing PoC, preserve private state and rerun `deploy`
to reload configuration and policies, then explicitly restart the dedicated
Deployment and reopen its forward. Do not rerun `prepare` to reload policies.

```sh
rtk proxy python3 tools/local_experiment/database_poc.py prepare
```

`prepare` creates ignored `var/shirokuma/database-poc` (0700). Every generated
file is 0600, including `clients.json` with five distinct random passwords,
`password.db` with bcrypt hashes, `shared-secret`, `tls.key`, `tls.crt`,
`tls.pem`, and a reviewable `configmap.json`. Passwords reach `htpasswd` only
on stdin. The localhost certificate lasts 365 days. Open client files privately;
never paste their contents into logs or source control. `prepare` refuses an
existing directory, including a partial preparation. Inspect/recover that state
manually rather than deleting credentials for an already deployed PoC.

Start a Polaris forward yourself in another terminal (the helper starts none):

```sh
rtk proxy kubectl --context colima-mac-studio-solo -n shirokuma-dev port-forward --address 127.0.0.1 service/polaris 18181:8181
```

```sh
rtk proxy python3 tools/local_experiment/database_poc.py bootstrap
rtk proxy python3 tools/local_experiment/database_poc_s3.py install
rtk proxy kubectl --context colima-mac-studio-solo -n shirokuma-storage rollout restart statefulset/seaweedfs
rtk proxy kubectl --context colima-mac-studio-solo -n shirokuma-storage rollout status statefulset/seaweedfs --timeout=120s
rtk proxy python3 tools/local_experiment/database_poc.py deploy
```

Before `install`, privately capture the current server Secret for exact-byte
restoration using the procedure in the S3 report. `install` creates/reuses
`s3-principals.json` (0600), adds only the two exact PoC identities to the static
server Secret, and never restarts workloads. It rejects conflicting names/keys.
This is a temporary change to an OpenTofu-owned Secret; do not run OpenTofu apply
while the experiment is active. Wait for the replacement SeaweedFS Pod to be
Ready before continuing. The active child Flux reconciler can remove the manual
restart annotation and cause a second Pod replacement; reopen forwards only
after that replacement settles. Do not disable IAM read-only mode or change Flux.

`bootstrap` reads Kubernetes Secrets into captured process memory, authenticates
to Polaris, and provisions the catalog boundary described below. `deploy` saves
private `resources.json` and applies exactly six resources:
Deployment, Service, ConfigMap, Secret, and NetworkPolicy, each named
`trino-database-poc`, plus policy-only ConfigMap `trino-database-poc-opa`.
It waits for that Deployment only. Review the pure
`manifests()` generator before the first deployment if needed; the generated
resource file contains secret material and must stay private.

Start the dedicated HTTPS forward in another terminal:

```sh
rtk proxy kubectl --context colima-mac-studio-solo -n shirokuma-dev port-forward --address 127.0.0.1 service/trino-database-poc 18443:8443
```

```sh
rtk proxy python3 tools/local_experiment/database_poc.py seed --table orders --rows 10
rtk proxy python3 tools/local_experiment/database_poc.py verify --table orders
```

Optional `--polaris-url http://127.0.0.1:18181` and
`--trino-url https://127.0.0.1:18443` select other operator-owned localhost
ports. Only explicit `localhost`/`127.0.0.1` origins with a port are accepted;
Trino always requires HTTPS with certificate verification using `tls.crt`.
Redirects and proxy environment variables are disabled. Polaris uses HTTP only
over the local operator forward, matching the existing lab transport.

`seed` is deliberately destructive **within the named PoC tables**: it drops and
recreates `<sales|finance>.<raw|sandbox|marts>.<table>`, then commits two inserts
to produce at least two real snapshots. `--table` accepts a lowercase SQL
identifier and defaults to `orders`; `--rows` accepts 1–10000 synthetic rows. There is no production data
copy. Re-seed after a verification run if a clean data baseline is needed.

## Boundary and credentials

| SQL catalog | Polaris INTERNAL catalog | Principal / principal role / catalog role | Base and sole allowed prefix |
| --- | --- | --- | --- |
| `sales` | `db_poc_sales` | `poc_sales` / `poc_sales_principal` / `poc_sales_content` | `s3://shirokuma-lakehouse/database-poc/sales` |
| `finance` | `db_poc_finance` | `poc_finance` / `poc_finance_principal` / `poc_finance_content` | `s3://shirokuma-lakehouse/database-poc/finance` |

Both catalogs have `raw`, `marts`, `sandbox`, and `staging`. Each principal role
receives only its own content role, with catalog-scoped
`CATALOG_MANAGE_CONTENT`, `TABLE_READ_DATA`, and `TABLE_WRITE_DATA`. There are
no service-admin or cross-catalog grants. The connector warehouse is its
`db_poc_<database>` name; that name is not a Trino SQL catalog. Only `sales` and
`finance` connector files are mounted (no `iceberg` or `tpch` SQL catalog).
Trino's built-in `system` catalog is visible only to the PoC admin.

Only these dedicated PoC catalogs require the string property
`polaris.config.drop-with-purge.enabled: 'true'` so Iceberg DROP can purge.
Bootstrap validates this alongside the base/storage settings and refuses a
missing or mismatched value; it never updates an existing catalog. The live run
set this property only on the two PoC catalogs, then confirmed that bootstrap
also sets it when recreating them from empty. This property does not provide
physical S3 isolation.

`polaris-root-credentials` keys `client_id`, `client_secret`, and `realm` are
decoded in memory for bootstrap. Root client credentials are never saved or
injected into the PoC Pod. The realm is saved in private `realm.json` and copied
to the PoC Secret. Newly returned dedicated principal credentials are immediately
saved to `sales-principal.json`/`finance-principal.json` before further network
operations. Each connector receives explicit `SALES_CLIENT_ID/CLIENT_SECRET` or
`FINANCE_CLIENT_ID/CLIENT_SECRET` environment references. OAuth uses Basic,
`Polaris-Realm`, client credentials grant, and `PRINCIPAL_ROLE:ALL` scope;
only the single provisioned role may be assigned to each principal.

The PoC Kubernetes Secret contains password hashes, TLS PEM, internal shared
secret, dedicated Polaris credential pairs, the two DB-specific S3 credential
pairs, and realm. It contains no plaintext Trino user passwords. Each Catalog
uses only its own `SALES_S3_ACCESS_KEY_ID/SALES_S3_SECRET_ACCESS_KEY` or
`FINANCE_S3_ACCESS_KEY_ID/FINANCE_S3_SECRET_ACCESS_KEY` environment references.
The PoC Pod no longer receives the shared `AWS_ACCESS_KEY_ID` or
`AWS_SECRET_ACCESS_KEY`. Only the non-secret region still references
`seaweedfs-s3-application-credentials:S3_REGION`. Missing DB keys fail deployment;
there is no shared-key fallback.
Both connectors set `vended-credentials-enabled=false`.

**DB-specific S3 keys now have a verified direct-API prefix boundary.** Each
identity has only Read / Write / Tagging / List for
`shirokuma-lakehouse/database-poc/<db>/*`, including the slash before `*`.
Root/parent/other-prefix LIST and HEAD bucket return 403; Iceberg still works
without bucket-wide Read/List grants. Copy, multipart and mixed batch deletion
were verified independently of SQL lifecycle operations.

Polaris and existing components retain their shared service key. SQL lifecycle
success establishes end-to-end compatibility, not attribution of each S3 I/O to
the DB key. This is not isolation from a compromised Polaris service, leaked
shared service key, or compromised Trino process holding both DB keys. Keep the
PoC local and administrator-operated; no general-user release is implied.

The dedicated single coordinator keeps the existing non-root UID/GID 10000,
read-only root filesystem, dropped capabilities, RuntimeDefault seccomp,
disabled service-account token, and ephemeral tmp/data volumes. It uses a 2 GiB
heap, 2 GiB requested memory and 4 GiB limit. Its distinct selector preserves
`trino-local`. The dedicated NetworkPolicy denies Pod ingress and permits
standard namespace egress to Polaris, SeaweedFS and DNS; no shared policy is
modified. The `shirokuma.dev/object-storage-client="true"` label opts into the
existing SeaweedFS ingress rule. Kubernetes policies are additive: the operator
must inspect other selecting policies before claiming effective deny ingress.
SQL clients use the operator port-forward; the internal HTTP listener remains
for discovery/health with insecure HTTP authentication disabled.

## SQL authorization

| Identity | Allowed SQL |
| --- | --- |
| `poc_admin` | Both catalogs, publication, schemas, queries, procedures and functions |
| `sales_engineer` / `finance_engineer` | Own raw/sandbox SELECT, INSERT, DELETE, UPDATE, OWNERSHIP; own marts SELECT |
| `sales_viewer` / `finance_viewer` | Own marts SELECT |

Catalog access defaults to deny. Nonadmins have no staging/cross-catalog access,
Schema ownership, procedure/function grants, impersonation, authorization
changes, or permission to view/kill others' queries. Their metadata is filtered;
they can execute their own queries. Table OWNERSHIP intentionally permits table
creation/drop in raw/sandbox without granting Schema administration. This uses
[Trino 483's `checkCanCreateTable` implementation](https://github.com/trinodb/trino/blob/483/lib/trino-plugin-toolkit/src/main/java/io/trino/plugin/base/security/FileBasedSystemAccessControl.java).

That same source has an empty `checkCanExecuteTableProcedure`. The dedicated
PoC now configures both file and Trino's OPA access controls (AND semantics).
OPA is an **auxiliary guard and must never be used alone**: known authenticated
identities pass ordinary operations to the unchanged file ACL; unknown users
are denied. Only `poc_admin` may execute table procedures, and only in `sales`
or `finance`. Every nonadmin table procedure is denied across all four schemas.
Permission-management operations remain governed by the file ACL. OPA HTTP
requests have a 5-second request timeout and a 2-second connection timeout.

The pinned OPA sidecar binds only `127.0.0.1:8182`, mounts `guard.rego` and
`api.rego` read-only, receives no secrets, and exposes no Service or container
port. Basic API authorization permits only POST of the guard decision and GET
`/health`; policy/data administration, queries and mask endpoints are denied.
It runs as UID/GID 10000 with a read-only root, dropped capabilities and bounded
resources, without decision or request/response logging. The S3 follow-up changes
only the PoC's storage credentials; its file/OPA and Polaris principal rules remain unchanged.

Ordinary `CALL` grants alone do not establish `ALTER TABLE ... EXECUTE` denial.
The unchanged 95-case verification executes the
full `ALTER TABLE <db>.marts.<table> EXECUTE
rollback_to_snapshot(snapshot_id => <real old snapshot>)` as each nonadmin.
It requires two real seeded snapshots, and adds an admin synthetic insert before
each attempt so an earlier unexpected rollback cannot turn the next attempt
into a no-op. An unexpected allow remains **FAILED** and can roll back the PoC
mart, even if no snapshot change is observed. Each case records the real current
snapshot before and after, the valid non-current parent (`old`), and `changed`.
The target role's ALTER receipt remains the case's `query`; prerequisite and
aftercheck admin reads are separately retained in `queries`. Only denial of
the target ALTER counts; failed prerequisites or afterchecks fail the case.
Only a real authorization denial becomes a pass. Re-seed to restore a clean table afterward.
The admin positive test explicitly switched to the recorded parent and then
back to the recorded original snapshot. Trino 483 implements this procedure
with Iceberg `setCurrentSnapshot`; the original snapshot must still exist.
The destructive `seed` command remains the clean-fixture reset option.

The follow-up runtime acceptance passed within this local PoC scope. To repeat it, run native `opa test`
against `guard.rego`, `api.rego` and `policy_test.rego` using the pinned OPA
image, then require all 95 live checks, including the four real rollback
denials and their before/after snapshot evidence. Separately demonstrate a
valid admin table procedure with an actual snapshot change and explicit
restoration, and that unavailable OPA fails closed for protected operations.
Confirm disallowed OPA APIs are rejected. Offline manifest checks alone do not
establish these runtime outcomes.

OPA unavailability is an execution failure, not a normal authorization denial:
the observed queries terminated with `GENERIC_INTERNAL_ERROR` after about five
seconds. Ordinary SELECTs, including admin SELECT, also fail until OPA recovers.
Do not treat that failure as permission to fall back to file-only access control.

## Re-runs, errors, and acceptance

Bootstrap GETs existing named entities, memberships and grants before creating
only missing ones. Existing catalogs must match type, base, exact allowed
prefix, endpoints, region and storage flags. Existing principals/roles must
carry the PoC marker. Unexpected role membership or grants fail; they are not
silently revoked. Existing principals without local credentials fail explicitly:
GET cannot recover their secrets. There is no automatic rotation, reset, delete,
or generic write retry. If a principal creation response or its local save is
lost, operator recovery is required. Deploy re-applies only the dedicated
resources; it does not inspect or patch shared resources. Keep the private state
alongside the PoC for re-runs, and run one helper invocation at a time.

Each requested SQL statement is POSTed to `/v1/statement` **exactly once**.
Only `nextUri` GETs are polled, within the same configured localhost origin,
with a 120-second polling budget. There is no POST resend after timeout,
authentication/network failure or unknown execution. Missing/mismatched query
IDs and missing terminal status fail. A timed-out statement can still complete
server-side; inspect its recorded query ID before any explicit manual retry.
Optional SQL catalog/schema context is sent as `X-Trino-Catalog` and
`X-Trino-Schema` on both the initial POST and every polling GET.
Private `queries.json` retains authenticated identity (null for missing auth),
claimed identity when different, catalog/schema context, exact SQL, status,
query ID when known, and a bounded uppercase/digit/underscore `errorName` when
available. This metadata survives polling updates. It stores no passwords,
Authorization headers or raw error messages/bodies. SQL used here is synthetic;
do not place secrets in tested SQL.
For seed and verify,
each invocation replaces the previous invocation's query receipts. Preserve it
before re-running if investigating an unknown outcome.
HTTP error bodies, subprocess output, credentials and tracebacks are suppressed.

`verify` prints concise step statuses and writes private `verification.json`
incrementally, recording all query receipts per case in `queries` (including
setup and followup queries), the compatibility `query`, and real table UUIDs,
locations and namespaces. It aggregates failures instead of stopping at the
first expected gap and exits nonzero for any failed/unknown check. It checks:

- Missing/wrong passwords and claimed-admin impersonation rejection.
- Own allowed reads/writes, engineer table create/drop, viewer raw/sandbox
  rejection, marts DML rejection, staging and other-catalog rejection.
- Schema create/drop/rename rejection, filtered catalog/schema lists,
  absent underlying Polaris SQL names, and native 1-part (`orders` with catalog
  and raw schema headers), 2-part (`raw.orders` with catalog header), and 3-part
  (`<db>.raw.orders`) access as each database's engineer against the seeded table
  (or the selected `--table`).
- Ordinary connector procedure rejection and valid old-snapshot mart rollback.
- Direct dedicated-principal own namespace list and REST raw table load with
  real UUID/location under its expected prefix, followed by other-catalog
  namespace-list and table-load rejection (403 or hidden 404).

Verification itself writes synthetic data, creates/drops engineer probe tables
and attempts operations that could succeed if the boundary is broken. Use only
these disposable PoC catalogs. An unexpected allowed Schema DROP/RENAME or
procedure can change them; subsequent checks may fail too. A pre-existing
`<table>_create_probe` is not overwritten; investigate it before re-running.

The execution report records additional live checks for distinct UUIDs and data
paths, cross-prefix LOCATION rejection, information_schema tables/columns,
native naming, static redeployment, and recreation from empty catalogs. These
are separate from the helper's 95 checks. Staging must be empty for the helper's
DROP SCHEMA negative test: a populated schema can return SCHEMA_NOT_EMPTY before
the ACL decision, which is deliberately not counted as an authorization denial.
Concurrent query ownership, View identity, product/API/BI/Jobs/Workbench paths,
and user acceptance remain outside this Phase 1 run. The verified boundary is
between the two dedicated S3 keys, not all components of the storage system.

Polaris envelopes and membership paths follow the official
[Polaris 1.6.0 management specification](https://raw.githubusercontent.com/apache/polaris/apache-polaris-1.6.0/spec/polaris-management-service.yml)
and the repository's Java catalog bootstrap and `polaris_permissions.py`.
The table-procedure gap and direct DB-key prefix boundary are verified for this
PoC. Service-key/process isolation and product/API/BI/Jobs/Workbench acceptance
remain outside this result. Later phases were not implemented here.

After testing, scale the PoC to zero, run `database_poc_s3.py revoke`, restore the
original server `s3.json` bytes only after semantic equality is confirmed, and
restart SeaweedFS. SIGHUP alone does not guarantee identity removal. Verify both
old PoC keys return `403 InvalidAccessKeyId`, existing application access still
works, and close all forwards. See the S3 report for the exact restoration and
evidence procedure. Retained local PoC keys are inactive until a later explicit
`install`; do not reuse them outside this disposable experiment.
