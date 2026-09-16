# Local Trino 483 experiment

This image is for the approved host-only development loop. It uses the existing
upstream Trino 483 release archive, whose SHA-256 is
`4f3978428f26f36398c94b85a3e03b5301394919c8a4271b497b0fcd1698d0cb`
(851,844,304 bytes), and the repository-pinned Maven/Temurin Java 25 Ubuntu ARM64
base. Only the launcher, server libraries, and Iceberg connector are included.

The archive identity is recorded in `../v483/admission.json` as
`evaluated_upstream_binary_not_approved_build_input`. It is **not** the hardened
offline rebuild, a signed Shirokuma release, or a production admission result.
The local exception does not change the existing release/admission contracts.

From the repository root, set `TRINO_ARCHIVE` to the existing host artifact and
build:

```sh
./bootstrap/trino/local-experiment/build-local.sh "$TRINO_ARCHIVE"
```

The helper explicitly uses Docker context `colima-mac-studio-solo`, verifies the archive
before extracting a temporary build context, builds `linux/arm64`, and never
pushes. It requires the pinned base to be cached or pulled. The full archive is
first extracted because Iceberg JARs use hard links to other plugin directories.
The build context then retains only the selected directories and upstream NOTICE.
All temporary files are removed on exit. Set `SHIROKUMA_TRINO_VALIDATE_ONLY=1`
to verify the archive and complete this extraction/link check without Docker.

Check the live node runtime before deciding how to make the image available:

```sh
kubectl --context colima-mac-studio-solo get nodes \
  -o 'jsonpath={range .items[*]}{.metadata.name}{" "}{.status.nodeInfo.containerRuntimeVersion}{"\n"}{end}'
```

For `docker://...`, k3s uses the same Docker daemon targeted by the build helper;
no containerd import is needed. Inspect the image's `RepoDigests` through the
explicit Docker context and use the digest from the matching
`shirokuma.local/trino@sha256:...` reference:

```sh
docker --context colima-mac-studio-solo image inspect \
  shirokuma.local/trino:483-experiment --format '{{json .RepoDigests}}'
```

For `containerd://...`, import the OCI image into that node's actual containerd
runtime and `k8s.io` namespace, then resolve the imported image's manifest digest.
Do not assume a socket location from the Kubernetes distribution name.

Set `LOCAL_RUNTIME_DIGEST` to the digest resolved by the active runtime, then
render with the checked helper:

```sh
python3 bootstrap/trino/local-experiment/render-local.py "$LOCAL_RUNTIME_DIGEST" \
  > /tmp/shirokuma-trino-local.yaml
```

The helper validates the digest syntax and replaces the explicit placeholder in
the rendered manifest. It does not check whether the image reference is actually
available; verify that in the node's runtime before applying. Do not substitute
a Docker image/config ID for a repository manifest digest merely because both
use SHA-256.
`imagePullPolicy: Never` prevents remote pulls of the experiment image.

The deployment uses only existing Polaris/S3 Secrets, listens on a ClusterIP,
runs as UID/GID 10000 with a read-only root filesystem, and stores temporary
runtime files in disposable `emptyDir` volumes. It does not delete or migrate
existing catalog or object-store data. Existing Flux reconciliation must not
overwrite this experimental deployment.

Connector properties were checked against the Trino 483 source:
`IcebergRestCatalogConfig` (`http-headers`, `warehouse`, REST URI),
`OAuth2SecurityConfig` (credential, scope, token exchange),
`S3FileSystemConfig`, and `docs/src/main/sphinx/security/secrets.md` (`ENV`).
The realm header uses `Polaris-Realm:value`. Vended credentials and OAuth token
exchange are disabled because this experiment uses the existing static S3
application credentials.

The first useful query is `SELECT * FROM iceberg.smoke.fixture_v1 ORDER BY id`.
Capture its actual result and compare it with the existing Iceberg fixture;
startup/readiness alone does not establish data-plane acceptance.

## Disposable SQL table cleanup

Trino's REST connector sends `purgeRequested=true` for SQL `DROP TABLE`.
Polaris 1.6 requires both `TABLE_DROP` and `TABLE_WRITE_DATA` for that operation,
and its purge capability is disabled by default. The local helper adds only
`TABLE_WRITE_DATA` on namespace `local_experiment` through the named
`local_experiment_purge` catalog role assigned to `service_admin`. Existing
catalog metadata privileges already provide `TABLE_DROP`.

With a Polaris port-forward already bound to loopback, explicitly enable the
catalog capability when preparing disposable SQL CRUD checks:

```sh
python3 tools/local_experiment/polaris_permissions.py --port 18181 \
  --apply --enable-catalog-purge
```

`--enable-catalog-purge` requires `--apply`. It sets
`polaris.config.drop-with-purge.enabled=true` on catalog `shirokuma_l1` after
verifying the namespace-only grant. The capability switch is catalog-wide;
the helper adds no data privilege to the retained `smoke` namespace. No realm
or global setting is changed. Catalog updates preserve every unrelated property
and `storageConfigInfo`, use `currentEntityVersion`, and stop on a version
conflict without retrying stale configuration.

Retain stdout's `catalog_purge.previous` value/presence and version alongside the
resulting value/version as the rollback receipt. To restore the capability,
GET the current catalog and PUT an update using its fresh `entityVersion` as
`currentEntityVersion`, preserving current storage and unrelated properties.
Restore the recorded prior property value, or remove that property if the receipt
says it was absent; do not replay an old complete catalog object. The namespace
grant can separately be revoked through the management grant-revocation API or
the experimental role unassigned from `service_admin`.
