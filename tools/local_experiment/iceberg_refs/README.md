# Local Iceberg named references

This WP-L3-ICE-001 experiment creates one new owned table in
`iceberg.local_experiment` and checks named Iceberg tags and branches through
the existing Polaris REST catalog and Trino 483. It uses synthetic rows and
the Python standard library; no new service or dependency is installed.

```sh
rtk make test-local-iceberg-refs
rtk make local-iceberg-refs
```

The runner takes no arbitrary table, SQL, snapshot, URL or output path. It owns
its Trino tunnel and the loopback Polaris tunnel on port 18181. An occupied
listener is rejected. The retained Polaris Service UID and specification are
checked around use. Credentials stay in memory and are omitted from receipts.

The fixed sequence is:

1. Create a UUID-named table with an ownership comment and two synthetic rows
   at snapshot A. Bind all subsequent operations to its actual table UUID.
2. Create `baseline_tag` and `audit_branch`, both pointing to A.
3. Append one fixed row to main, creating B. Read both names through Trino and
   confirm they still return A's two rows while main returns B's three rows.
4. Move only the branch from A to B using an expected-reference condition.
   Confirm tag A, branch B and main B in REST metadata, Trino references and
   named SQL reads.
5. Submit an intentionally stale branch condition and require rejection with
   unchanged metadata. Drain the run's queries and remove only the table
   whose UUID and ownership comment still match.

Every reference mutation carries the table UUID, expected main snapshot and
expected target-reference snapshot. New names require an absent reference.
The tag cannot be moved by this client. Request intent is saved before a
single HTTP attempt; an uncertain response stops further mutations and leaves
cleanup deferred. There is no automatic write retry.

Reference changes must preserve the current snapshot, snapshot metadata and
the independently observed data-file inventory. This is a metadata reference
experiment, not a separate-table zero-copy clone or a demonstration of writes
to a branch. Snapshot expiry, orphan-file deletion and retained-table changes
are outside the entrypoint.

The retained fixture's schema, rows, snapshots and references, plus table
inventory, are checked before and after the run. Evidence is private under
`tmp/local-experiment/iceberg-refs/<run-token>/summary.json`. File paths are
hashed in reference projections. Unknown commit state, ownership or cleanup
never counts as successful completion.

The protocol follows the [Iceberg 1.11 REST catalog specification](https://github.com/apache/iceberg/blob/apache-iceberg-1.11.0/open-api/rest-catalog-open-api.yaml)
used by Polaris 1.6 and [Trino 483 named time-travel queries](https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/connector/iceberg.md#time-travel-queries).
The Work Package records actual live results separately from this sequence.
