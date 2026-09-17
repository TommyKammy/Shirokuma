# Describe the retained fixture's catalog records

```sh
shirokuma catalog describe --table smoke.fixture_v1 --profile local-experiment
```

This fixed read-only profile describes `smoke.fixture_v1` in the existing local
OpenMetadata service. It does not browse arbitrary tables, run SQL or call an
LLM. Use `--repo-root` when invoking the binary outside this checkout. The
[existing metadata runtime](../metadata/README.md) and its private account must
already be available; this command installs no dependencies, starts no service
and changes no credentials or catalog entities.

## What the result means

The schema 1 observation contains nine fields: `schema_version`, `table`,
`catalog_entity`, `description`, `owners`, `columns`, `lineage`, `sql_presence`
and `observation`. It identifies `iceberg.smoke.fixture_v1` and its OpenMetadata
UUID, FQN, revision string and update time in milliseconds. The fixed owner is
the `ShirokumaLocalExperiment` team; columns are `id: BIGINT` and `name: VARCHAR`,
with their optional catalog comments.

Description/comment `null` remains distinct from an empty string. Lineage has
`scope: one-hop` and contains bounded neighboring table identities and edges.
Each edge retains the catalog's `Manual`, `OpenLineage` or unknown `null` source.
An explicit empty node/edge list is an observed empty result; a missing or
unread lineage response is a failure, not an empty graph. Neighbor identities
are restricted to the known local generated-table naming scope, with validated
UUIDs, connected edges and no duplicate nodes or edges.

`sql_presence: not-checked` applies both to the fixture and every lineage node.
This command does not contact Trino or prove that any SQL relation currently
exists. Historical generated-table records can remain after their SQL tables
were removed. One-hop catalog edges do not establish whole-catalog coverage,
current job success or independently verified execution provenance. The
[Catalog Curator](../bears/CATALOG_CURATOR.md) and
[lineage coverage report](../metadata/COVERAGE.md) remain separate workflows.

## Read and consistency boundaries

The client logs in once with the existing account, then reads five fixed
anchors—team, service, database, schema and table—and the table's one-hop lineage.
It repeats that six-response window once: one login POST and at most 12 metadata
GETs, with no retries. The lineage request is bound to the observed table UUID.
Failure or cancellation can stop before the full budget is used.

Parent references, owner, root UUID, revisions and fixed column shape must agree.
The surrounding checks also bind the existing runtime's container identity,
Compose project/service, loopback endpoint and pinned image. Both windows must
have identical safe projections, anchor revisions and canonical SHA-256 hashes
of **all six complete API responses**. Even a change to an undisplayed response
field can conservatively reject the result.

`observation: {scope: catalog-metadata-only, stable: true}` describes those two
matching observations. They are not a transactional snapshot: concurrent edits
that revert between requests, future changes and unqueried catalog contents
remain outside the check. A changed, incomplete, oversized or inconsistent
response cannot become a successful description.

Descriptions and column comments are limited to 2048 UTF-8 bytes each; lineage
has at most 64 nodes and 64 edges. API responses are bounded at 2 MiB each and
the public observation at 64 KiB. Each HTTP request has a 10-second cap; all HTTP
operations share a 120-second deadline within the owner's 180-second deadline.
No new tunnel or background service is created, and the command
does not stop the existing runtime or send a logout mutation.

Only the validated projection, anchor revisions, response hashes and bounded
execution evidence are saved under
`tmp/local-experiment/catalog-read/TOKEN/summary.json` in new private directories
(0700) and files (0600). Existing runtime files retain their recorded permissions.
Raw API documents,
login passwords/tokens, connection settings, hrefs, SQL and unrestricted lineage
detail text are excluded. A hash is a comparison value, not a copy of the raw
response. HTTP resources and owned inspection processes must be closed, and
the runtime/state/source guards must pass before success is published. An
unverified final guard retains a failed or unknown outcome.

This exposes the readback portion of the existing metadata bootstrap and
Curator work. It does not fill missing descriptions, infer business owners,
change lineage, establish tenant authorization or complete broader governance
and production acceptance.

## Local validation

Two actual descriptions passed in 0.716/0.567 seconds with identical public
observations and verified cleanup. Each used one login and 12 GETs, reporting
the same catalog UUID, description, team owner, two columns and eight neighboring
nodes/eight edges. SQL presence remained `not-checked`. Invalid input was rejected
before allocation. No model calls, SQL, catalog writes or service changes occurred.

The before/after guard preserved 45 sources, 396 evidence files, MLflow's 59
tables and 16 runs/1320 metric points/16 artifacts, and AgentHouse's 519 rows.
Independent saved-only verification passed 29 checks over 67 inputs without new
API calls. The full local suite passed 2438 tests with one existing optional skip;
Go race (220 top-level/376 subtests), vet and build also passed. Evidence is under
`tmp/local-experiment/catalog-read-checks/c431c0633e2b4107826732277b3b9ab4/`.
These are two bounded catalog observations, not evidence of SQL-table presence
or complete lineage coverage.
