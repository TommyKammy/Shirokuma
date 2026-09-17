# Local Query Doctor Bear

This bounded WP-L2-AGENT-005 experiment inspects two fixed read-only Trino
queries over the retained two-row fixture. It uses the existing local Qwen
endpoint to choose additional evidence reads. It neither executes model SQL
nor changes data, statistics, engine settings or workload configuration.

```sh
rtk make local-query-bear-eval
rtk make local-query-bear-smoke
```

The host executes a fixed projection and a filtered projection, verifies their
results independently, and obtains JSON EXPLAIN for the same statements.
Query history is limited to the current invocation's dedicated user and captured
query IDs. The model receives a bounded structural projection, not raw SQL,
free-form plan details, connection settings or other users' query history.

The only follow-ups are fixed fixture statistics and the invocation's own query
history. The selected checks must cover the observed missing evidence. No
`ANALYZE`, DML, arbitrary `EXPLAIN ANALYZE`, session changes, indexes or data
layout changes are generated or executed.

Unknown estimates remain unknown: JSON EXPLAIN can represent them as `NaN`,
and a node may contain several estimate records. Zero is a known numeric value,
not a replacement for missing data. Plan estimates are not actual runtime
measurements. EXPLAIN's own query duration does not measure the target SELECT.
The two SELECTs have different result semantics and are not competing latency
benchmarks.

Each diagnostic step records safe audit outcomes through AgentHouse, Loki and
saved OpenTelemetry spans. Source observations, selected reads and final
assessment remain in private generated evidence. The assessment preserves
`root_cause: undetermined` and `performance_gain: unmeasured`: a tiny fixture
cannot demonstrate a production optimization or identify a general root cause.
The useful result is a verified observation and an explicit account of which
evidence is still missing.

No resident component is added. Stop using the entrypoint to stop this
experiment; retained source data and service settings remain available. Any
interrupted queries must be handled only within the invocation's captured
identity. The runner must report cleanup uncertainty instead of claiming that
all queries finished.

The parser is intentionally limited to the observed Trino 483 plan shape.
[Trino documents JSON plan compatibility limits](https://trino.io/docs/current/sql/explain.html#explain-type-distributed-format-json).
The history fields follow the
[pinned 483 QuerySystemTable source](https://github.com/trinodb/trino/blob/483/core/trino-main/src/main/java/io/trino/connector/system/QuerySystemTable.java):
`started` is execution start, so `created_to_end_ms` and `started_to_end_ms`
remain separate observations. Neither is reported as CPU time or summed with
phase timings.
