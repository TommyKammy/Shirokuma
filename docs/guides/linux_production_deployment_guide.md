# Linux production deployment guide

This guide describes a generic Linux deployment of the resident Shirokuma
services. The repository's supported experimental environment remains the
single Mac Studio lab (ADR-0036); this guide is a deployment blueprint requiring
validation on your own Linux infrastructure, not evidence of production readiness.

## Platform and sizing

Target Ubuntu **22.04 or 24.04 LTS**, on **x86_64 or ARM64**. Use official upstream
container images pinned by SHA256 digest for the selected architecture. Check
multi-architecture image manifests before deployment; existing lab pins and
Colima storage settings must be reviewed for the target host.

| SME sizing | CPU | RAM | Persistent storage |
| --- | --- | --- | --- |
| Minimum, light workload | 4 vCPU | 16 GB | 100 GB SSD |
| Recommended | 8 vCPU | 32 GB | 500 GB NVMe SSD |

These are starting points for small-to-medium enterprises, not concurrency or
availability guarantees. Reserve RAM for the OS, Kubernetes and PostgreSQL;
set Trino JVM/query memory and ClickHouse limits within the remaining budget.
Measure real queries and ingestion volumes, and provision backup capacity
separately from warehouse disks. A single host has a single failure domain.

## Architecture and infrastructure preparation

SQL clients reach the fixed Shirokuma warehouse endpoint, which connects to
Trino. Trino accesses Iceberg tables through Polaris, backed by PostgreSQL;
table objects reside in SeaweedFS or an independently validated MinIO S3 service.
ClickHouse stores AgentHouse events. The Workbench UI and Bears provide the
Shirokuma application services. Persistent storage is required for the S3
backend, PostgreSQL, ClickHouse and application state.

1. Prepare a private Linux Kubernetes cluster (for example k3s) with persistent
   volumes, DNS, synchronized time and adequate disk headroom. Install `kubectl`
   matching the cluster version. Colima is unnecessary on Linux.
2. Install Python 3.10+ (`python3`, `python3-venv`), Git and the Go version
   specified in `go.mod` if building the `shirokuma` CLI. Create a dedicated
   `shirokuma` service account on the host. Place a reviewed checkout at
   `/opt/shirokuma` and create `/var/lib/shirokuma` owned by that account,
   with mode `0700`. Install optional dbt/ingestion dependencies in a venv
   for the features you enable; the DR and resident HTTP entrypoints use stdlib.
3. Adapt repository manifests/charts to the Linux storage class and image
   architecture. Provision SeaweedFS (or MinIO), PostgreSQL, Polaris, Trino
   and ClickHouse. Configure the Iceberg catalog's Polaris URI, S3 endpoint,
   credentials and warehouse location consistently across services. With MinIO,
   validate endpoint/path-style settings and read/write operations explicitly.
4. Keep the resident interface contract: namespace `shirokuma-dev`, deployment
   `trino-local` and service `trino-local` exposing Trino on port 8080.
   `deploy/local-experiment/trino` supplies reference configuration, not a
   turn-key Linux installation. Configure AgentHouse's ClickHouse connection
   using `tools/local_experiment/agenthouse/manage.py`'s environment settings.
5. Install the service account's kubeconfig at
   `/etc/shirokuma/kubeconfig` with restricted permissions. Grant namespace
   permissions for workload inspection, pod/service port forwarding, Trino
   deployment scaling and rollout inspection. Test these as the service user.
   Build/install the CLI from `./cmd/shirokuma` and run `shirokuma doctor`.

Protect credentials through restricted files or your secret manager. Keep the
SQL endpoint and Workbench on loopback; use SSH tunneling or an authenticated
TLS reverse proxy on a private network. The resident UI and SQL username-based
policy controls are not a complete authentication boundary. Limit cluster and
storage access to the required principals and keep secrets out of Git.

## Environment

Create `/etc/shirokuma/resident.env`, readable only by the service account:

```ini
KUBECONFIG=/etc/shirokuma/kubeconfig
KUBE_CONTEXT=default
KUBE_NAMESPACE=shirokuma-dev
SHIROKUMA_STATE_DIR=/var/lib/shirokuma
TRINO_PORT=8080
```

`KUBE_CONTEXT=default`, `none`, or an empty string omits `kubectl --context`,
using the kubeconfig's current context. Any other value selects a named context;
if unset, the lab default is `colima-mac-studio-solo`. `KUBE_NAMESPACE` defaults
to `shirokuma-dev`. Set these before starting processes. Some optional lab
scripts still assume Colima and must be evaluated separately on Linux.

`SHIROKUMA_STATE_DIR` selects durable WAP and AI configuration storage and the
default backup directory. `TRINO_PORT` below is passed explicitly to the
endpoint's `--port` argument by systemd; it does not change the Kubernetes
Trino service port or automatically configure other tools.

## systemd services

Create `/etc/systemd/system/shirokuma-warehouse-endpoint.service`:

```ini
[Unit]
Description=Shirokuma fixed Trino SQL endpoint
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=shirokuma
Group=shirokuma
WorkingDirectory=/opt/shirokuma
EnvironmentFile=/etc/shirokuma/resident.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 tools/local_experiment/warehouse_resident.py endpoint --bind 127.0.0.1 --port ${TRINO_PORT}
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/shirokuma-workbench.service`:

```ini
[Unit]
Description=Shirokuma Workbench Web UI
Wants=network-online.target shirokuma-warehouse-endpoint.service
After=network-online.target shirokuma-warehouse-endpoint.service

[Service]
Type=simple
User=shirokuma
Group=shirokuma
WorkingDirectory=/opt/shirokuma
EnvironmentFile=/etc/shirokuma/resident.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 tools/local_experiment/workbench_server.py --host 127.0.0.1 --port 8888
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

Ensure `kubectl` is in the service's PATH. If using a venv, replace the Python
executable with `/opt/shirokuma/.venv/bin/python`. Then activate:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now shirokuma-warehouse-endpoint.service shirokuma-workbench.service
sudo systemctl status shirokuma-warehouse-endpoint.service shirokuma-workbench.service
curl --fail http://127.0.0.1:8080/v1/info
curl --fail http://127.0.0.1:8888/
```

The endpoint can wake a chilled warehouse on query. Test a real SQL request as
well as HTTP health; an active systemd process alone does not prove cluster health.

## Automated backup

Run as the service account, with the same environment and kubeconfig:

```sh
cd /opt/shirokuma
set -a
. /etc/shirokuma/resident.env
set +a
umask 077
python3 tools/local_experiment/disaster_recovery.py backup
python3 tools/local_experiment/disaster_recovery.py backup --output /var/lib/shirokuma/backups/manual.tar.gz --no-tables
```

The default creates a uniquely timestamped `.tar.gz` under
`$SHIROKUMA_STATE_DIR/backups`. Archives contain `manifest.json`, SHA256 checksums
for every payload file, existing `wap_models.json` and `ai_config.json` in
`state/`, recursive dbt SQL models and YAML schemas/tests (`*.sql`, `*.yml`,
`*.yaml`) in `models/`, schema/table/view DDL in
`catalog/`, and optional full table results in `data/`. The manifest maps table
names to DDL/data paths; data JSON retains Trino column metadata and row values.
`--no-tables` omits rows while retaining definitions. Missing application state
files are normal for a fresh installation and are omitted.

The warehouse must be awake and reachable. Query errors or truncated results
abort backup. Quiesce writers, WAP publication and model editing before backup
to obtain a coordinated recovery point: sequential queries do not provide a
transactional snapshot across tables. Logical exports currently materialize
data in memory; size the backup process for the dataset. For large datasets,
use consistent storage/database-native backups alongside this bundle.

For daily backup, install this entry with `sudo crontab -u shirokuma -e`:

```cron
SHELL=/bin/sh
PATH=/usr/local/bin:/usr/bin:/bin
15 2 * * * cd /opt/shirokuma && (umask 077; set -a; . /etc/shirokuma/resident.env; set +a; /usr/bin/flock -n /var/lib/shirokuma/backup.lock python3 tools/local_experiment/disaster_recovery.py backup) >> /var/lib/shirokuma/backup.log 2>&1
```

Schedule wake/quiescence before that window and alert on backup failures or
stale archives. Rotate logs and implement retention according to your recovery
objectives, deleting old archives only after a newer off-host copy is verified.
Encrypt and copy bundles to a separate failure domain; `ai_config.json` can
contain API keys and table exports contain business data. SHA256 detects
corruption, not malicious replacement; protect archive provenance separately.

## Disaster recovery procedure

1. Provision the replacement cluster and the same compatible application
   revision and images. Restore S3 objects, Polaris PostgreSQL, ClickHouse
   AgentHouse and cluster configuration/secrets from their coordinated native
   backups. The logical bundle does not include these services' internal state,
   Iceberg snapshot history, authentication secrets or Kubernetes manifests.
2. Stop the Workbench and pause ingestion/model writers. Validate the bundle:

   ```sh
   python3 tools/local_experiment/disaster_recovery.py restore --input /secure/backup.tar.gz --target-dir /var/lib/shirokuma --dry-run
   ```

3. Run the same command without `--dry-run`. Every archive member and checksum
   is validated before any writes. Corruption, missing/extra files, duplicate
   members, links or traversal paths fail closed. State is restored into the
   target directory; model SQL and YAML are restored into the checkout's
   `tools/local_experiment/dataops/dbt_persistent/models`, including nested paths.
   The service user needs write permission there during recovery. Existing
   matching files are replaced; unrelated files are retained. Omit
   `--target-dir` to use `SHIROKUMA_STATE_DIR` (default `var/shirokuma`).
4. For full logical warehouse recovery into an empty catalog, add
   `--restore-warehouse` to both the dry run and the actual restore:

   ```sh
   python3 tools/local_experiment/disaster_recovery.py restore --input /secure/backup.tar.gz --target-dir /var/lib/shirokuma --restore-warehouse --dry-run
   python3 tools/local_experiment/disaster_recovery.py restore --input /secure/backup.tar.gz --target-dir /var/lib/shirokuma --restore-warehouse
   ```

   Trino must be awake and reachable using the service environment's
   `KUBE_CONTEXT`, `KUBE_NAMESPACE` and kubeconfig. The `iceberg` catalog, Polaris
   and object storage must be configured and writable, and `shirokuma-restore`
   must have schema/table/view creation and insert permissions. Review archived
   schema/table location properties before replay to avoid writing into old data.
   A full data recovery requires a backup made without `--no-tables`.
   The dry run validates and prepares SQL without connecting or writing.
   Actual recovery validates the bundle, creates schemas and base tables, inserts
   logical rows in batches using column types (including ARRAY, MAP and ROW), then
   creates views in repeated passes to resolve view-on-view dependencies. It
   finally restores application state and dbt SQL/YAML files. Unresolved views
   fail recovery. SQL replay is not transactional: inspect partial results before
   retrying, since existing table DDL or duplicate inserts can prevent safe replay.
   Without `--restore-warehouse`, only state/model files are restored. If native
   storage/catalog backups were restored together, verify existing tables instead
   of replaying DDL/data. Logical row copies do not recover Iceberg snapshot history.
5. Compare row counts, representative values, models and WAP state with the
   source recovery point. Restart services, run `shirokuma doctor` with the
   replacement kubeconfig, run SQL read/write smoke checks and verify AgentHouse
   event ingestion. Record the recovery point and elapsed recovery time. Rehearse
   this procedure on an isolated environment before relying on it operationally.

## Health checks and monitoring

Use `shirokuma doctor` for component diagnostics, `systemctl status` for process
state and `journalctl -u shirokuma-workbench -u shirokuma-warehouse-endpoint` for
application failures. Inspect `kubectl get pods,pvc -n shirokuma-dev` with the
configured context. Monitor Trino query latency/errors and memory, Polaris and
PostgreSQL health, S3 capacity/errors, ClickHouse ingestion/disk, Kubernetes
restarts and volume usage. Integrate metrics and alerts into Prometheus/Grafana
or your monitoring service. Track backup age, off-host copy completion and
restore drill results alongside service health.
