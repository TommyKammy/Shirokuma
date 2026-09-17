# Isolated Trino / OPA local experiment

This experiment exercises Trino 483 authorization using synthetic `X-Trino-User`
headers and OPA 1.20.2. It does **not** authenticate users. A fixed case explicitly
demonstrates that a caller can select the administrator header. There is no
Keycloak, JWT, mTLS, production RBAC, or production security acceptance claim.

## Fixed inputs and scope

`inputs.lock.json` pins the native Linux ARM64 inputs by SHA-256. The build uses
the existing Trino archive, the already cached Maven/JDK base image, and the
official static OPA ARM64 release binary. It verifies the archive before and
after extraction, verifies the binary, and runs OPA's strict policy check and
eight policy tests inside the native image build. Each local image tag has a
fresh UUID; images are never pushed. Failed build receipts remain available.

The shadow Trino image includes only `tpch` and `opa`. Each run creates exactly
one NetworkPolicy, one immutable ConfigMap, and one two-container Pod at a time
in `shirokuma-dev` on `colima-mac-studio-solo`. Names and ownership markers include
a new run UUID. Both phases undergo name-collision and server-dry-run validation
before the first mutation. The normal phase is removed before the fault phase.

There are no Services, Secrets, PVCs, host paths, external endpoints, retained
catalogs, or mounted service-account tokens. Trino has a 2 CPU / 4 GiB limit
and a 2 GiB heap; OPA has a 500m CPU / 256 MiB limit. Two bounded `emptyDir`
volumes provide 256 MiB temporary space and 512 MiB Trino data space. The Pod
uses a non-root UID, read-only root filesystems, dropped capabilities, and
`RuntimeDefault` seccomp. NetworkPolicy denies all ingress and egress; OPA is
bound to the same-Pod loopback address. The host tunnel binds only 127.0.0.1.

## Commands

From the active checkout, build the new local images:

```sh
rtk proxy python3 tools/local_experiment/trino_opa/build.py \
  --trino-archive "$TRINO_ARCHIVE"
```

Use the returned 32-hex build token. `plan` writes a private manifest plan only;
`run` creates a fresh run and applies the bounded experiment:

```sh
rtk proxy python3 tools/local_experiment/trino_opa/run.py plan --build-token BUILD_TOKEN
rtk proxy python3 tools/local_experiment/trino_opa/run.py run --build-token BUILD_TOKEN
rtk proxy python3 -m unittest discover -s tests -p test_local_trino_opa.py
```

Every successful run must verify these fixed TPCH `tiny.nation` cases:

| Case | Required observation |
| --- | --- |
| `admin_full` | All 25 nation keys and original names |
| `row_filter` | Only nation key 0 / ALGERIA |
| `column_mask` | All 25 keys, each name MASKED |
| `combined` | Only key 0 with name MASKED |
| `denied_user` | Trino permission denial and no rows |
| `denied_column` | Permission denial for the fixed `comment` projection |
| `header_is_not_authentication` | The same caller chooses the administrator header and reads all rows |
| `opa_unavailable` | Same SELECT fails with OPA backend connection failure and no rows |

Trino's `read-only` system access control is also enabled. The OPA Rego tests
check that a write operation is denied; the runtime query suite performs only
the fixed SELECTs above. No claim of an observed runtime write denial is made.

The unavailable phase changes only OPA endpoint port 8181 to the unused 8182.
Trino version/readiness must be observable before and after the query. The error
must contain both the pinned Trino OPA `QueryFailed` class and a
`java.net.ConnectException` cause. A generic query error, dead Trino, syntax
error, or denial is insufficient evidence for OPA transport failure.

OPA decision logs must correlate each non-null query ID with the exact case's
synthetic user, decision path, operation, and outcome. Trino's `ExecuteQuery`
decision can lack a query ID; its receipt explicitly records the narrower
`user-and-execute-query-only` correlation. This is authorization evidence for
synthetic identities, not authenticated-user attribution.

## Evidence and cleanup

Evidence is stored under `tmp/local-experiment/trino-opa/{builds,runs}/<UUID>`.
Directories are private (0700), and JSON files are private (0600). Receipts
persist pending operations before requests and confirmed object UIDs afterward.
Query receipts contain safe error classes, hashes, counts, and exact-match
results, not returned rows or raw query errors. On failure, bounded logs from
UID-verified owned Pods are saved privately before cleanup. Such diagnostic logs
are local evidence and are not exported or passed to a model.

Cleanup checks the saved ownership marker, content, and known UID, then deletes
with UID and resourceVersion preconditions and orphan propagation. It removes
the Pod before its configuration and network isolation, waits for absence,
and refuses a replaced or modified object. It never deletes by label alone.
A create request with no returned UID remains `deferred`, even if the name is
currently absent: absence cannot identify the outcome of that request. No UID
is adopted from a subsequent GET, and no cleanup-complete claim is made for it.

Recover confirmed objects from a stopped run:

```sh
rtk proxy python3 tools/local_experiment/trino_opa/run.py recover --run-token RUN_TOKEN
```

Recovery uses the private saved build, manifest plan, object hashes, and known
UIDs. Current Rego or input pin changes do not prevent cleanup. It retains the
original receipt and writes a separate `recovery.json`; an exclusive run lock
prevents concurrent run/recovery. Unknown UIDs and ownership conflicts are
reported for inspection, without adopting or deleting the uncertain object.
Exit 0 means the requested operation passed; any failed verification, deferred
cleanup, or interruption returns nonzero. Image/cache retention is deliberate;
there is no broad image prune or cluster reset.

## Pinned primary sources

- [Trino 483 OPA documentation](https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/security/opa-access-control.md)
- [Trino 483 OPA access control implementation](https://github.com/trinodb/trino/blob/483/plugin/trino-opa/src/main/java/io/trino/plugin/opa/OpaAccessControl.java)
- [Trino 483 insecure authentication](https://github.com/trinodb/trino/blob/483/core/trino-main/src/main/java/io/trino/server/security/InsecureAuthenticator.java)
- [Trino 483 OPA HTTP error handling](https://github.com/trinodb/trino/blob/483/plugin/trino-opa/src/main/java/io/trino/plugin/opa/OpaHttpClient.java)
- [OPA 1.20.2 release](https://github.com/open-policy-agent/opa/releases/tag/v1.20.2)
