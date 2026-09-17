# Local Query Doctor Bear

This bounded WP-L2-AGENT-005 experiment inspects two fixed read-only Trino
queries over the retained two-row fixture. It uses the existing local Qwen
endpoint to choose additional evidence reads. It neither executes model SQL
nor changes data, statistics, engine settings or workload configuration.

```sh
rtk make local-query-bear-eval
rtk make local-query-bear-smoke
```

The product CLI uses the existing dedicated Python environment and fixed local
provider configuration. Build from the checkout root, then run:

```sh
rtk go build -o tmp/shirokuma ./cmd/shirokuma
rtk proxy tmp/shirokuma query run --case retained-fixture
rtk proxy tmp/shirokuma query status <evidence-token>
rtk make test-query-doctor-cli
```

`run` invokes the workflow once with a fresh evidence token. `status` only reads
saved local evidence: it makes no model or API calls and does not create or
change evidence files. Both use the `local-experiment` profile. An explicit
`--repo-root` can select this checkout when launched elsewhere. The CLI does
not install dependencies or accept arbitrary SQL, prompts or endpoint options.

The JSONL allocation event identifies the directory but does not establish that
durable intent or model execution occurred. The final event is an allowlisted
summary; raw observations, SQL, prompts, responses and exception messages stay
out of CLI output. `evidence_token` names `query-bear/<token>`; the separate
`query_run_token` identifies the workflow's captured Trino user and queries.
The bridge records its invocation intent and file hashes in `query-cli/<token>`.
Complete older evidence without a CLI journal can also be inspected, without
claiming fresh runtime verification or a retroactive hash seal.

On cancellation the CLI signals only its own child, allows up to 240 seconds
for cleanup, and preserves an unknown outcome with the token and child PID
if completion cannot be established. It does not send SIGKILL or replay the
workflow. There is no `resume` command: inspect status before deciding how to
handle an interrupted attempt. A closed verified failure differs from an
initial in-progress receipt whose result field is already `failed`.

Exit 0 for `run` requires verified passed evidence and a matching child exit;
exit 1 reports failure or uncertainty. Cancellation uses 130 or 143 when
established. For `status`, exit 0 means the saved status could be inspected,
including busy, unknown or failed status; it does not mean the diagnosis passed.

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
