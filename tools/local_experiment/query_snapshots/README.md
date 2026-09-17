# Read a retained snapshot

```sh
shirokuma query snapshots --table smoke.fixture_v1
shirokuma query select --table smoke.fixture_v1 --columns id,name --limit 10 --snapshot-id <listed-id>
rtk make test-local-query-snapshots
```

The listing shows retained snapshot IDs, parent IDs, sequence numbers, commit
times in milliseconds, schema IDs, operation and schema compatibility. IDs and
sequence numbers are exact decimal strings. Ordering follows commit time; a
snapshot ID's numeric size says nothing about ancestry. A parent absent from
the listing can have been removed by retention and is marked `parent_retained:
false`; the command does not invent its history.

An explicit `--snapshot-id` uses the separate schema v2 profile and writes
private evidence under `tmp/local-experiment/query-snapshots/`. Omitting this
flag preserves the current-head Reader v1 command and receipt. Only the existing
local-experiment profile, Iceberg catalog and smoke/local_experiment schemas are
accepted. No SQL expressions, names of branches or tags, timestamps, writes,
model calls, new services or fallback to the current head are added.

Before reading data, the command discovers the table and columns and fetches
current Polaris metadata. The selected snapshot must still be retained and its
recorded schema must have the same ordered field IDs, names, scalar types and
nullability as the current schema. Missing or incompatible historical schemas
can appear in the listing but cannot authorize a data read. An unlisted ID is
`SNAPSHOT_NOT_RETAINED`, without claiming whether it expired or ever existed.

The SELECT is pinned to the selected ID. `current_snapshot_id` continues to
describe the actual table head, while `snapshot_id` and `selected_snapshot`
describe what was read. Metadata, UUID, schemas, head and the snapshot listing
must remain identical through the before/after checks. Any drift or uncertain
query outcome prevents success; data SELECT is never retried.

The profile supports at most 64 retained snapshots and inherits the Reader's
metadata, cell, request, result and query budgets: 2 MiB metadata, 4 KiB cells,
1–16 selected columns, at most 100 output rows and a 256 KiB public result. It
fetches limit+1 to report truncation. Integers and decimals remain exact strings;
row ordering is unspecified. Raw rows are explicit command output and are not
saved in the normal receipt.

Output is released after owned queries end, local tunnels close, source checks
and receipt persistence finish, and Python's final status matches the Go
protocol. Failure and cancellation retain safe evidence and the original
interrupt, without resuming old tokens. No retained user data is modified.

The existing `query diagnose` supports Reader v1. This snapshot profile is not
silently accepted there: current SHOW STATS must not be mixed with historical
data as if both described the same snapshot. Metadata checks cover the read
window; they are not an atomic guarantee against concurrent writers or future
expiration. This is bounded local functionality, not production acceptance.
