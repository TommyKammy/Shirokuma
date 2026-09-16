# Local OpenMetadata and OpenLineage

This is the ADR-0034 local alternative for WP-L1-META-001. It runs the actual
OpenMetadata server and persists metadata plus native OpenLineage lineage in
PostgreSQL. Elasticsearch supports ordinary catalog search. It does not claim
Flux-managed deployment, strict supply-chain admission, production readiness,
or a live Dagster web service.

## Bootstrap and repeat

Run from the active Shirokuma checkout:

```sh
rtk python3 tools/local_experiment/metadata/runtime.py up
rtk python3 tools/local_experiment/metadata/ingest.py
rtk python3 tools/local_experiment/metadata/runtime.py status
rtk python3 -m unittest -v tests.test_local_metadata
```

The fixed Docker context is `colima-mac-studio-solo`; the isolated Compose project
is `shirokuma-local-metadata`. Only the server publishes a host port,
[127.0.0.1:18585](http://127.0.0.1:18585). PostgreSQL and search have no published
ports and use an internal network. The server also has a separate bridge network
for the loopback binding. There is no Docker socket mount or host networking.
Existing Kubernetes workloads, Polaris PostgreSQL, retained Iceberg data and
Colima settings are not changed.

Images are pinned to their native Linux/arm64 manifest digests in
`deploy/local-experiment/metadata/images.json`: OpenMetadata/server and its
PostgreSQL image are 1.12.6, Elasticsearch is 9.3.0. The choices follow the
[official 1.12.6 PostgreSQL quickstart](https://github.com/open-metadata/OpenMetadata/blob/1.12.6-release/docker/docker-compose-quickstart/docker-compose-postgres.yml).
Limits: server 2 CPU/4 GiB, database 1 CPU/2 GiB, search 2 CPU/3 GiB. Migration
uses another temporary 2 CPU/4 GiB. Elasticsearch mmap is disabled in this
container configuration; no VM-wide sysctl change is required.

`up` creates private generated database/admin/JWT/Fernet credentials below
ignored `tmp/local-experiment/metadata/`, then waits for migration and server
health, rotates the initial admin password and verifies authenticated API access.
Repeat `up` reuses credentials and named volumes. Do not remove the private state
directory while retaining its volumes. Bind-mounted key/init files are readable
by the corresponding non-root containers; their enclosing host directory is mode
0700, and `.env` is mode 0600. Passwords and API tokens are never printed.
Credentials are for this local generated environment only. UI login uses
`admin@open-metadata.org` and the generated `OM_ADMIN_PASSWORD` in that private
`.env` file.

## Ingestion and evidence

`ingest.py` selects the latest successful generated DataOps evidence with verified
query drain, or accepts `--dataops-evidence` pointing to one run below
`tmp/local-experiment/dataops/`. It reads only the retained Trino fixture using
an automatically managed loopback tunnel, validates its rows, and introspects
its schema. It creates a dedicated team owner, Trino database/schema/table
entities and Dagster pipeline entities through authenticated native APIs.

The two dbt model entities describe a **historical successful run** whose SQL
tables have already been removed by DataOps cleanup. Their descriptions state
that explicitly. Column names come from the retained dbt manifest; absent SQL
types remain `UNKNOWN`. The live fixture types come from Trino `DESCRIBE`.
No missing type information or new successful job execution is invented.

Actual manifest dependencies and the successful dbt/Dagster execution identity
produce reproducible START/COMPLETE OpenLineage event pairs. Actual dbt execute
start/end times and the Dagster parent run ID are preserved. Events are sent to OpenMetadata's native
`/api/v1/openlineage/lineage` endpoint with auto-creation disabled and explicit
namespace-to-service mapping. START events are explicitly filtered by the server;
COMPLETE events create lineage. The payload follows the [OpenLineage 2.0.2 schema](https://openlineage.io/spec/2-0-2/OpenLineage.json).
Saved API readback must verify all owners/schemas,
the fixture → staging → mart edges, their `OpenLineage` source, and their Dagster
pipeline reference. Repeating ingestion upserts the same entities and edges.
No separate event collector substitutes for OpenMetadata.

Each successful invocation writes a private `ingestion-*.json` record containing
source artifact hashes, live fixture evidence, events, processing results and
saved metadata/lineage. A response saying the event was accepted alone is not
sufficient. Runtime health also remains separate from functional ingestion.

The official Elasticsearch profile currently warns that its optional
`vectorEmbedding` index uses unsupported OpenSearch k-NN settings. Ordinary
metadata APIs, native lineage and server health work; vector search is not
claimed. MCP server CORS configuration is also outside this metadata profile.

## Backup, stop and recovery

```sh
rtk python3 tools/local_experiment/metadata/runtime.py backup
rtk python3 tools/local_experiment/metadata/runtime.py stop
rtk python3 tools/local_experiment/metadata/runtime.py up
```

`backup` writes a PostgreSQL custom-format dump under the private state directory.
The dump contains metadata/authentication state and must remain private. Keep the
matching generated `.env` and key files with any operator-managed backup; do not
commit them. Search indexes are derived state and can be rebuilt from PostgreSQL.
A 2026-09-16 restore check restored the dump into a new uniquely named scratch
database, verified its three table entities, and removed only that scratch
database; the receipt is `backup-restore-verification.json` in the state directory.

`stop` retains both volumes and all credentials. `up` is the normal retry after a
transient failure. Inspect only this project's container logs for migration or
API errors; do not copy raw configuration/credentials into durable notes.
To recover a backup, restore it into a new scratch database first and verify it
before any operator decision to replace the retained metadata database.

There is deliberately no `down -v`, volume prune or global nuke command. Removing
retained metadata volumes/backups requires a separate explicit decision. During
this experiment only newly generated scratch databases may be dropped after
identity and creation have been confirmed. Reverting the local deployment/code
and stopping this Compose project is the non-destructive rollback.
