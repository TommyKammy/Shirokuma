# Superset local BI experiment

WP-L1-BI-001 local functional implementation: Superset 6.1.0 queries the existing
Trino / Polaris / SeaweedFS Iceberg fixture and saves a table chart and dashboard.
It uses the documented local profile alternative in this work package.

The official Superset 6.1.0 Linux ARM64 manifest is pinned to
`sha256:cc424513bf159c860b1db27655f40aa82fc270be35038cbcbe5272cdcaedb37d`.
The derived local image adds Trino 0.339.0 plus three missing dependencies using
exact versions and ARM64 wheel SHA256 hashes. Existing dependencies remain fixed
by the base image. `uv pip check` verifies the combined Python environment.
No image is pushed or published.

The runtime is one container in the fixed `colima-mac-studio-solo` Docker context,
with a dedicated labeled SQLite metadata volume. It consumes up to 2 CPUs and
3 GiB RAM. The container uses the Colima VM host network to reach the existing
Trino ClusterIP, because the Docker bridge is outside the Kubernetes ingress
policy. Gunicorn binds **127.0.0.1:18088 only** inside the VM; Colima forwards that
loopback listener to the macOS host. It has a read-only root filesystem, all
capabilities dropped, no privilege escalation, and a bounded `/tmp` filesystem.
No existing Kubernetes resources, PostgreSQL, PVCs, or object data are changed.

From the repository root:

```sh
rtk proxy python3 tools/local_experiment/superset/local_superset.py build
rtk proxy python3 tools/local_experiment/superset/local_superset.py start
rtk proxy python3 tools/local_experiment/superset/local_superset.py smoke
rtk proxy python3 tools/local_experiment/superset/local_superset.py status
```

Open `http://127.0.0.1:18088/`. The account is `local-admin`; its generated password
is the `admin_password` field in the ignored, owner-readable
`tmp/local-superset/private.json`. The file also retains the generated application
secret. These values are supplied to the initialization container on stdin and
stored in its private metadata volume, not in Docker environment variables,
command arguments, Git, or verification summaries. Keep this directory private.

`smoke` authenticates through Superset's API with CSRF enabled, registers a Trino
connection, imports `iceberg.smoke.fixture_v1`, and verifies the exact two rows.
It creates or reuses the experiment chart/dashboard, retrieves the saved objects,
checks the dashboard layout association, and reruns the saved chart query.
The connection disables DML, CTAS, and CVAS; the smoke submits read-only queries.
The fixture is not regenerated or modified. Saved objects persist across restart.
Evidence, generated private state, image identity, and initialization logs are
under ignored `tmp/local-superset/`.

```sh
rtk proxy python3 tools/local_experiment/superset/local_superset.py restart
rtk proxy python3 tools/local_experiment/superset/local_superset.py smoke
rtk proxy python3 tools/local_experiment/superset/local_superset.py export
rtk proxy python3 tools/local_experiment/superset/local_superset.py stop
rtk proxy python3 tools/local_experiment/superset/local_superset.py remove
```

`stop` stops only the labeled experiment container. `remove` removes that
container after stopping it and preserves the metadata volume and private
credentials. `start` recreates the container using the retained metadata.
Name collisions without the ownership label are refused. Initialization timeout
cleanup checks both the label and exact image before removing its own temporary
container. A file lock prevents overlapping lifecycle operations.

`export` uses SQLite's online backup API and retains both a metadata snapshot and
the matching private application/admin state in a timestamped, private
`tmp/local-superset/exports/` directory. Keep these two files together; the
application secret is needed for encrypted metadata fields. Snapshots and the
named metadata volume remain after container removal. Recovery should copy a
snapshot into a fresh experiment volume and validate it before replacing any
retained metadata. No automated volume deletion or backup overwrite is provided.
The metadata contains the local BI objects and account hashes, not Iceberg data.

This is a local SQLite profile with one web process, no Celery scheduler, Redis,
SSO, HA, public endpoint, or production acceptance. Reverting this experiment
means stopping/removing its container while keeping metadata/export evidence.
Historical publication and Flux admission remain separate from these local
functional results.

Primary references:

- [Superset Docker image and ARM64 guidance](https://superset.apache.org/user-docs/6.1.0/installation/docker-builds/)
- [Superset Trino driver](https://superset.apache.org/user-docs/databases/supported/trino/)
- [Trino Python driver compatibility](https://pypi.org/project/trino/0.339.0/)
