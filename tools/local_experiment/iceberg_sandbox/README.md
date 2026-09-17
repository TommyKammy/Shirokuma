# Local Iceberg snapshot sandbox

This bounded WP-L3-ICE-001 experiment demonstrates snapshot reads, restoration
into a separate table, and rollback using the existing Trino 483, Polaris and
SeaweedFS deployment. It creates two fresh, owned experiment tables and keeps
the retained `iceberg.smoke.fixture_v1` read-only.

```sh
rtk make local-iceberg-sandbox
rtk make test-local-iceberg-sandbox
```

The entrypoint checks the dedicated local context and manages its own loopback
Trino tunnel. It needs only the existing Python standard library and runtime.

The intended sequence is:

1. Create a two-row experiment table and capture snapshot A.
2. Append one fixed row and capture snapshot B; verify current and historical
   rows against independent expected values.
3. Read A into a separate new table with CTAS and verify its contents.
4. Roll back the original experiment table to A, checking snapshot history and
   current rows while retaining evidence of B.
5. Confirm that this invocation's queries have ended, verify ownership, and
   remove only its two generated tables. Check the retained fixture again.

The fixed SQL follows the
[Trino 483 Iceberg connector documentation](https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/connector/iceberg.md).
Snapshot IDs come from the newly generated table's metadata. The experiment
does not accept an arbitrary table, snapshot ID or SQL command from a caller.

In Trino 483, the
[`HistoryTable` implementation](https://github.com/trinodb/trino/blob/483/plugin/trino-iceberg/src/main/java/io/trino/plugin/iceberg/system/HistoryTable.java)
iterates retained snapshots and marks their current ancestry. It does not append
a second A row when the table rolls back from B to A. The verifier therefore
checks the `main` reference, both retained snapshots, B's changed ancestry and
the actual A/B query results. Operation order comes from the saved query ledger.

CTAS writes a separate logical copy. It is not a zero-copy clone and does not
create a named Iceberg branch or tag. Rollback here changes only the new
experiment table's current snapshot; it is not a retained-data recovery or
backup guarantee. Snapshot expiry and orphan-file cleanup are outside this
entrypoint.

Each run records private evidence under
`tmp/local-experiment/iceberg-sandbox/<run-token>/`. Request identities,
snapshot relationships, row checks and cleanup outcomes remain explicit.
Uncertain query completion, ownership or cleanup must remain unresolved in the
receipt and must not be presented as success.

No service or model is added. Existing local runtime settings and retained
data remain available after this disposable experiment.
