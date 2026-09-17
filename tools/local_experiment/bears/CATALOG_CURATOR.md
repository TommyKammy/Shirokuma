# Local Catalog Curator Bear

This bounded WP-L2-AGENT-004 experiment uses the existing local Qwen and
OpenMetadata services. It records successful Data Engineer Bear executions as
historical catalog entries. Their generated Iceberg relations were already
removed by verified cleanup; the entries are not live tables.

## Run

First run a [Data Engineer Bear case](DATA_ENGINEER.md). Copy its host-generated
`verification.run_token` from the successful receipt, then run:

```sh
rtk make local-catalog-bear-eval
rtk make local-catalog-bear-smoke CATALOG_DATA_PLAN_TOKEN=<32-hex-data-plan-token>
```

The token must name this checkout's private generated
`tmp/local-experiment/data-plans/<token>` evidence. Arbitrary paths and automatic
selection of an unrelated run are not supported. Evaluation only queries the
local model and records audits; the smoke performs the bounded catalog writes.

## Evidence and writes

The input boundary is the Data Plan materialization evidence; it does not authenticate
a separate Data Bear model receipt. The host validates the plan, generated project hashes, dbt dependencies and
successful results, Dagster run identity, query drain, exact cleanup and source
preservation. The model receives a small fixed projection of missing catalog
fields and evidence identifiers. It can select only `stg`/`mart` and
`description`/`owner`/`lineage`; the selected set must exactly match the observed
missing set and cite the verified evidence. It cannot choose API paths, entity
names, owner identities, SQL or arbitrary descriptions.

The host generates descriptions that explicitly say historical and removed,
uses the existing `ShirokumaLocalExperiment` team, and writes only the two
run-specific table entities and the fixture → staging → mart lineage edges.
Existing services, database, schemas, team and fixture are read and checked.
Conflicting existing owners, edited descriptions, schemas or lineage cause a
stop before applying changes. A matching registration is a no-op. Readback and
an immediate repeat verify the saved fields and absence of redundant writes.

These edges use OpenMetadata's `Manual` lineage source, grounded in retained dbt
execution evidence. They do not claim new OpenLineage events or a new Dagster
execution. The existing native OpenLineage ingestion workflow remains available
for its original DataOps evidence format.

Safe model metrics and workflow outcomes are correlated through AgentHouse,
Loki and OpenTelemetry. Evidence is stored privately under
`tmp/local-experiment/catalog-bear/`; raw credentials are not model input or
committed output. Failed or partial application must be inspected through its
receipt before retry; readback distinguishes completed writes from attempted
ones.

## Recovery and limits

Use the existing [metadata backup runbook](../metadata/README.md#backup-stop-and-recovery)
before broader local experiments. The non-destructive rollback is to stop using
this entrypoint and revert the local code. Existing catalog data, volumes and
backups are retained. This command has no whole-catalog delete or restore path.

This proves a small evidence-bound metadata workflow for three fixed fixture
transformations. It does not infer business owners, author general descriptions,
repair arbitrary existing entities, establish catalog-wide completeness or
claim production acceptance. No cloud inference, external publication or new
resident services are required.

The API writes are not one transaction. A private progress journal records each
pending write before transmission and confirms identities after readback.
Interrupted or failed writes can remain uncertain until the next preflight.
There is no atomic isolation guarantee against concurrent external editing.
