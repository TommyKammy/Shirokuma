# Count rows in one snapshot

```sh
shirokuma query count --table smoke.fixture_v1
shirokuma query count --table smoke.fixture_v1 --where-column id --equals 1
shirokuma query count --table smoke.fixture_v1 --snapshot-id 1234567890123456789
shirokuma query count --table smoke.fixture_v1 --snapshot-id 1234567890123456789 --where-column id --equals 1
```

Replace the example snapshot ID with one returned by `query snapshots`. Only
the existing `local-experiment` profile, Iceberg catalog and `smoke` or
`local_experiment` schemas are accepted. Use `--repo-root` when the binary runs
outside this checkout. The existing dedicated Python environment and local
services must already be available; the command installs or starts no service.
Run local Reader operations one at a time: they share fixed tunnel ports,
including the Polaris endpoint on 18181. Starting a Count and a select in
parallel can fail before a query is sent; that failure is not a count of zero.

This executes one exact `COUNT(*)` over a fixed retained snapshot. There is no
`--columns`, `--limit`, sample, approximate count or optimizer estimate. A count
may scan all matching rows in that snapshot; small output does not imply a cheap
query. Existing query timeouts and cleanup bounds still apply. Without a filter,
rows containing NULL cells are included. An observed count of zero is valid data,
not a substitute for a failure or missing observation.

The optional `--where-column` and `--equals` flags must appear together. They use
the existing [typed equality contract](../query_filter/README.md): integer,
bigint, exact decimal, date or boolean, inferred from the freshly verified schema.
There are no arbitrary SQL expressions, text predicates or NULL equality. A NULL
cell does not match the supplied non-NULL equality.

Without `--snapshot-id`, the command resolves the current head once before
counting. An explicit positive int64 ID must be retained and have the same ordered
field IDs, names, scalar types and nullability as the current schema. There is no
fallback to another ID. `snapshot_id` and `selected_snapshot` describe the count's
snapshot; `current_snapshot_id` continues to describe the actual table head.
UUID, schema, head and the bounded snapshot catalog must match after the query.
A table without a current snapshot cannot be counted by this profile.

The command uses schema 4 and the `snapshot-count-reader-v4` profile. A successful
observation contains `count` as a canonical decimal string from `"0"` through
`"9223372036854775807"`, table/UUID/schema identity, both snapshot identities,
the selected snapshot record, optional `where` descriptor and `complete: true`.
The descriptor contains column/type/`operator: eq`, without the literal. No
individual rows are returned. Existing Reader v1, Snapshot v2 and Filter v3
commands keep their formats. Query Doctor and its saved report do not yet support
schema 4 count receipts as diagnostic sources.

Discovery/schema queries and metadata readbacks surround the single data
COUNT POST; query drain has its own control request. The data POST is attempted
at most once and is not automatically retried after an unknown outcome. A new
token is allocated under `tmp/local-experiment/query-count/`; tokens are not
reused or automatically resumed. Successful output requires metadata checks,
owned query drain, tunnel cleanup, durable evidence and a matching child exit.
Failure or cancellation cannot publish an unverified count.

The private receipt saves the request and aggregate/identity evidence, not
individual rows, SQL text, credentials or unrestricted upstream errors. A filter
literal remains in CLI argv, possible shell history, the private request and
Trino SQL/history. The public descriptor omits it. No LLM, table writes, grants,
new service or retained-data deletion is involved. The profile inherits the
Reader's request/metadata/public byte caps, 32-column schema and 64-snapshot
catalog bound; it does not inherit a data-row LIMIT.

These checks cover the observation window and do not provide an atomic guarantee
against concurrent writers or future snapshot expiration. This is bounded local
access, not tenant authorization, a performance claim or production acceptance.

## Local validation

Four actual counts passed on a disposable two-snapshot fixture: current 3,
historical 2, current equality 1 and historical equality 0. The existing v1
Reader also returned typed rows containing NULL. Invalid syntax was refused
before allocation, and a varchar predicate was refused before data COUNT.
A first attempt collided with another Reader's fixed tunnel before any query;
its failed, unobserved public result remains intact. Successful runs were serial.
The owned fixture was dropped once under the same UUID; retained views/inventory
were restored. All 54 sources and 384 retained files, MLflow's 59 tables and
16 runs/1320 metric points/16 artifacts, and AgentHouse's 519 rows were unchanged.
The full local suite passed 2402 tests with one existing optional skip; Go tests,
race and vet passed. Independent saved-only verification passed 206 checks over
86 inputs with no further API/model/SQL calls. Evidence is under
`tmp/local-experiment/query-count-checks/26cbcfe56a684d95b610b06e2ccca68b/`.
